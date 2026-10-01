"""Failure-path checks for local release evidence packaging."""

import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from collect_companion_native_closure import archive_notices, requested_sources
from package_companion_review import (
    assemble,
    contained_file,
    native_input_records,
    verify_app,
    verify_file,
)


class PackageEvidenceTests(unittest.TestCase):
    def test_complete_native_closure_is_not_reduced_to_the_interpreter_roots(self):
        records = [{"name": name} for name in ("CPython", "python-build-standalone", "openssl-3.5")]
        self.assertEqual(native_input_records({"records": records}), records)
        with self.assertRaisesRegex(ValueError, "standalone build recipe"):
            native_input_records({"records": records[1:]})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="amplifai-package-test-")
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def test_inventory_rejects_changed_or_extra_app_file(self):
        helper = self.root / "Contents/Resources/helper/agent"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b"synthetic binary")
        inventory = {"file_hashes": [{"path": "agent", "sha256": hashlib.sha256(helper.read_bytes()).hexdigest()}],
                     "outer_app_file_hashes": []}
        self.assertEqual(verify_app(self.root, inventory), 1)
        helper.write_bytes(b"changed binary")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_app(self.root, inventory)
        helper.write_bytes(b"synthetic binary")
        (helper.parent / "extra").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "file set"):
            verify_app(self.root, inventory)

    def test_rejects_manifest_traversal_and_symlink(self):
        for relative in ("../outside", "/absolute", "dir/../../outside", "dir\\outside"):
            with self.assertRaisesRegex(ValueError, "unsafe manifest path"):
                contained_file(self.root, relative)
        (self.root / "target").write_text("synthetic")
        (self.root / "alias").symlink_to(self.root / "target")
        with self.assertRaisesRegex(ValueError, "symlink"):
            contained_file(self.root, "alias")

    def test_refuses_existing_output_before_reading_other_inputs(self):
        with self.assertRaisesRegex(ValueError, "never replaced"):
            assemble(self.root, self.root / "missing-app", self.root / "missing-inventory",
                     self.root / "missing-evidence", self.root)

    def test_refuses_corrupt_source_archive(self):
        (self.root / "source.tar.gz").write_bytes(b"changed archive")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_file(self.root, {"filename": "source.tar.gz", "sha256": "0" * 64}, "filename")

    def test_refuses_non_registry_cargo_source_before_download(self):
        report = {"cryptography_nested_components": [{"source_status": "external", "name": "example",
                  "version": "1.0.0", "cargo_lock": {"source": "git+https://example.test/source"}}]}
        with self.assertRaisesRegex(ValueError, "unrecognized Cargo source"):
            requested_sources(report)

    def test_archive_inspection_rejects_traversal_without_extraction(self):
        path = self.root / "unsafe.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo("../LICENSE")
            member.size = 7
            archive.addfile(member, io.BytesIO(b"example"))
        with self.assertRaisesRegex(ValueError, "unsafe archive member"):
            archive_notices(path)


if __name__ == "__main__":
    unittest.main()
