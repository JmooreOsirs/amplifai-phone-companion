"""Source staging rejects archive substitution and historical product payloads."""
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
import warnings
import zipfile

from stage_release_source_offer import (
    extract_dependencies, native_inputs, selected_dependency, retain_intel_source_declarations,
    verify_readable_authored_python,
)


class SourceOfferChecks(unittest.TestCase):
    def test_frozen_only_intel_is_supported_but_missing_or_partial_arm_copy_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree, app = root / "source", root / "app"
            authored = {"source/collector/amplifai_phone/__init__.py": "authored/init.py"}
            (tree / "authored").mkdir(parents=True)
            (tree / "authored/init.py").write_text("VALUE = 1\n")
            self.assertEqual(verify_readable_authored_python(tree, app, authored, "macos-intel-test"), [])
            with self.assertRaises(ValueError):
                verify_readable_authored_python(tree, app, authored, "macos-arm64")
            embedded = app / "Contents/Resources/collector/amplifai_phone/__init__.py"
            embedded.parent.mkdir(parents=True)
            # A present but incomplete Intel copy cannot silently use the frozen-only exception.
            with self.assertRaises(ValueError):
                verify_readable_authored_python(tree, app, authored, "macos-intel-test")
            embedded.write_text("VALUE = 1\n")
            self.assertEqual(verify_readable_authored_python(tree, app, authored, "macos-arm64"), list(authored))
            embedded.write_text("VALUE = 2\n")
            with self.assertRaises(ValueError):
                verify_readable_authored_python(tree, app, authored, "macos-intel-test")

    def test_intel_retention_drops_obsolete_binary_inventory_and_success_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "third_party/current-intel-source-closure.json"
            original.parent.mkdir()
            declarations = {name: [] for name in ("pythonVersion", "runtimeArchiveSha256", "pythonSourceInputs",
                "pythonProvider", "rustCrates", "localRustCrates", "rustNoticeRecords", "staticOpenSSL")}
            original.write_text(json.dumps({**declarations, "actualNativeFileSourceMapping": [{"sha256": "old"}],
                "currentAppSboms": [{"sha256": "old"}], "sourceArchivesVerified": True, "commit": "old"}))
            retain_intel_source_declarations(root, {"sha256": "baseline"})
            retained = json.loads((root / "third_party/retained-intel-source-declarations.json").read_text())
            self.assertEqual(retained["sourceDeclarations"], declarations)
            self.assertEqual(set(retained), {"schema", "baselineSourceArchive", "sourceDeclarations"})
            self.assertFalse(original.exists())

    def test_only_dependency_material_is_reused(self):
        for channel in ("macos-arm64", "macos-intel-test"):
            for name in ("source/collector/agent_entry.py", "release-source-map.json",
                         "third_party/final-signed-app-inventory.json", "third_party/package-file-manifest.json",
                         "release-evidence/source-finalization.json", "SOURCE-README.md"):
                self.assertFalse(selected_dependency(name, channel), (name, channel))
            self.assertTrue(selected_dependency("third_party/source_archives/Python.tar.xz", channel))

    def zip_case(self, members):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path = root / "baseline.zip"
        with zipfile.ZipFile(path, "w") as archive, warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for name, mode in members:
                item = zipfile.ZipInfo(name)
                item.external_attr = mode << 16
                archive.writestr(item, b"source")
        return path, root / "dependencies"

    def test_regular_zip_selection(self):
        path, output = self.zip_case([("offer/source/old.py", stat.S_IFREG | 0o644),
            ("offer/third_party/source_archives/input.tar.gz", stat.S_IFREG | 0o644)])
        extract_dependencies(path, output, "macos-arm64")
        self.assertEqual((output / "third_party/source_archives/input.tar.gz").read_bytes(), b"source")
        self.assertFalse((output / "source").exists())

    def test_zip_traversal_symlink_duplicate_and_multiple_roots_fail(self):
        ordinary = ("offer/third_party/source_archives/input.tar.gz", stat.S_IFREG | 0o644)
        cases = [
            [("../third_party/source_archives/input.tar.gz", stat.S_IFREG | 0o644)],
            [("offer/third_party/source_archives/link", stat.S_IFLNK | 0o777)],
            [ordinary, ordinary],
            [ordinary, ("other/third_party/source_archives/two.tar.gz", stat.S_IFREG | 0o644)],
        ]
        for members in cases:
            with self.subTest(members=members):
                path, output = self.zip_case(members)
                with self.assertRaises(ValueError):
                    extract_dependencies(path, output, "macos-arm64")

    def test_streaming_tar_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "baseline.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                item = tarfile.TarInfo("offer/intel-python-source-inputs/link")
                item.type = tarfile.SYMTYPE
                item.linkname = "/etc/passwd"
                archive.addfile(item)
            with self.assertRaises(ValueError):
                extract_dependencies(path, root / "dependencies", "macos-intel-test")

    def test_streaming_tar_copies_regular_sources_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "baseline.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                for name in ("source/old.py", "intel-python-source-inputs/recipe.json"):
                    item = tarfile.TarInfo("offer/" + name)
                    item.size = 6
                    archive.addfile(item, io.BytesIO(b"source"))
            output = root / "dependencies"
            extract_dependencies(path, output, "macos-intel-test")
            self.assertEqual((output / "intel-python-source-inputs/recipe.json").read_bytes(), b"source")
            self.assertFalse((output / "source").exists())

    def test_unrecognized_native_component_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unmapped native"):
            native_inputs("Contents/Resources/new.dylib", set(), {}, "macos-arm64")


if __name__ == "__main__":
    unittest.main()
