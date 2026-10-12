"""Integrity boundaries for the Intel runtime source/notice collector."""
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import collect_intel_python_sources as collector


class IntelPythonSourceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(collector.DEFAULT_MANIFEST.read_text())

    def test_all_native_metadata_dependencies_have_matching_source(self):
        collector.validate_manifest(self.manifest)
        self.assertEqual(len(self.manifest["sources"]), 11)
        self.assertEqual(self.manifest["python_version"], "3.12.15")

    def test_missing_openssl_source_rejected(self):
        altered = copy.deepcopy(self.manifest)
        altered["sources"] = [item for item in altered["sources"] if item["provider_key"] != "openssl-3.5"]
        with self.assertRaisesRegex(ValueError, "coverage is incomplete"):
            collector.validate_manifest(altered)

    def test_unmapped_static_library_rejected(self):
        altered = copy.deepcopy(self.manifest)
        altered["metadata_evidence"]["linked_extensions"]["_extra"] = [{"links": [{"name": "unexpected"}]}]
        with self.assertRaisesRegex(ValueError, "coverage is incomplete"):
            collector.validate_manifest(altered)

    def test_mismatched_runtime_fails_before_any_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "wrong-runtime.tar.gz"
            runtime.write_bytes(b"not the reviewed Intel runtime")
            with patch.object(collector, "download_verified") as download:
                with self.assertRaisesRegex(ValueError, "actual build runtime archive differs"):
                    collector.collect(self.manifest, root / "output", runtime)
                download.assert_not_called()

    def test_notice_symlink_is_not_followed(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as bundle:
            member = tarfile.TarInfo("provider/LICENSE")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            bundle.addfile(member)
        data.seek(0)
        with tarfile.open(fileobj=data) as bundle:
            with self.assertRaisesRegex(ValueError, "Unexpected provider"):
                collector.provider_file(bundle, "provider", "LICENSE")


if __name__ == "__main__":
    unittest.main()
