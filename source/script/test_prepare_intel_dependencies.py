"""Input-integrity regressions for the Intel source-build boundary."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("prepare_intel_dependencies", Path(__file__).with_name("prepare_intel_dependencies.py"))
prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prep)


class IntelDependencyIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(prep.DEFAULT_MANIFEST.read_text())

    def test_all_102_versions_and_hashes_match_canonical_lock(self):
        prep.validate_manifest(self.manifest, prep.DEFAULT_LOCK)
        inputs = self.manifest["runtime_wheels"] + self.manifest["runtime_sources"]
        self.assertEqual(len(inputs), 102)
        self.assertEqual(next(x["version"] for x in inputs if x["name"] == "cryptography"), "50.0.1")

    def test_version_downgrade_is_rejected(self):
        altered = copy.deepcopy(self.manifest)
        altered["runtime_sources"][0]["version"] = "49.0.0"
        with self.assertRaisesRegex(ValueError, "original lock"):
            prep.validate_manifest(altered, prep.DEFAULT_LOCK)

    def test_unreviewed_source_and_missing_package_are_rejected(self):
        altered = copy.deepcopy(self.manifest)
        altered["runtime_wheels"].pop()
        with self.assertRaisesRegex(ValueError, "every runtime"):
            prep.validate_manifest(altered, prep.DEFAULT_LOCK)
        altered = copy.deepcopy(self.manifest)
        altered["runtime_sources"].append(altered["runtime_wheels"].pop())
        with self.assertRaisesRegex(ValueError, "Unreviewed source"):
            prep.validate_manifest(altered, prep.DEFAULT_LOCK)

    def test_lock_edit_requires_reaudit(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock"
            lock.write_bytes(prep.DEFAULT_LOCK.read_bytes() + b"# change\n")
            with self.assertRaisesRegex(ValueError, "re-audit"):
                prep.validate_manifest(self.manifest, lock)

    def test_download_tampering_cannot_become_cached_input(self):
        with tempfile.TemporaryDirectory() as directory:
            item = {"filename": "input.whl", "url": "https://files.pythonhosted.org/input.whl", "sha256": hashlib.sha256(b"valid").hexdigest()}
            with patch.object(prep.urllib.request, "urlopen", return_value=io.BytesIO(b"tampered")):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    prep.download_verified(item, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cached_tampering_is_not_silently_redownloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "input.whl"
            target.write_bytes(b"tampered")
            item = {"filename": target.name, "url": "https://files.pythonhosted.org/input.whl", "sha256": hashlib.sha256(b"valid").hexdigest()}
            with patch.object(prep.urllib.request, "urlopen") as request:
                with self.assertRaisesRegex(ValueError, "Cached artifact"):
                    prep.download_verified(item, Path(directory))
                request.assert_not_called()

    def test_validated_download_has_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            item = {"filename": "input.whl", "url": "https://files.pythonhosted.org/input.whl", "sha256": hashlib.sha256(b"valid").hexdigest()}
            with patch.object(prep.urllib.request, "urlopen", return_value=io.BytesIO(b"valid")):
                result = prep.download_verified(item, Path(directory))
            self.assertEqual(result.read_bytes(), b"valid")

    def test_archive_cannot_escape_build_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../../outside", "bad")
            with self.assertRaisesRegex(ValueError, "Unsafe source"):
                prep.extract_verified(archive, Path(directory) / "source")
            self.assertFalse((Path(directory).parent / "outside").exists())

    def test_cargo_follows_locked_nested_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "src/rust").mkdir(parents=True)
            expected = source / "src/rust/Cargo.toml"
            expected.write_text("[package]\nname='example'\nversion='1.0.0'\n")
            (source / "Cargo.lock").write_text("version = 4\n")
            (source / "pyproject.toml").write_text('[tool.maturin]\nmanifest-path="src/rust/Cargo.toml"\nlocked=true\n')
            self.assertEqual(prep.cryptography_cargo_manifest(source), expected.resolve())
            (source / "pyproject.toml").write_text('[tool.maturin]\nmanifest-path="../Cargo.toml"\nlocked=true\n')
            with self.assertRaisesRegex(ValueError, "contained Cargo"):
                prep.cryptography_cargo_manifest(source)

    def test_cargo_rejects_unlocked_build(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "Cargo.toml").write_text("[workspace]\n")
            (source / "Cargo.lock").write_text("version = 4\n")
            (source / "pyproject.toml").write_text('[tool.maturin]\nmanifest-path="Cargo.toml"\nlocked=false\n')
            with self.assertRaisesRegex(ValueError, "locked"):
                prep.cryptography_cargo_manifest(source)

    def test_non_native_host_fails_before_build(self):
        with patch.object(prep.platform, "system", return_value="Darwin"), patch.object(prep.platform, "machine", return_value="arm64"):
            with self.assertRaisesRegex(ValueError, "native|macos-15-intel"):
                prep.require_native_host()


if __name__ == "__main__":
    unittest.main()
