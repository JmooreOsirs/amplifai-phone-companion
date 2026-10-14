import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import export_windows_pillow_evidence as evidence


class PillowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wheel = self.root / "wheel"
        self.installed = self.root / "installed"
        self.destination = self.root / "evidence"
        self.receipt = self.root / "candidate.json"
        records, csv_rows = [], []
        for name in evidence.NATIVE_NAMES:
            relative = f"PIL/{name}.cp312-win_amd64.pyd"
            shipped = f"resources/phone-helper/_internal/{relative}"
            data = (name + "-actual-native-fixture").encode()
            digest = hashlib.sha256(data).digest()
            self.write(self.wheel / relative, data)
            self.write(self.installed / shipped, data)
            records.append({"path": shipped, "bytes": len(data), "sha256": digest.hex()})
            csv_rows.append(f"{relative},sha256={base64.urlsafe_b64encode(digest).rstrip(b'=').decode()},{len(data)}")
        for name in evidence.METADATA_LIMITS:
            data = ("\n".join(csv_rows) + "\n").encode() if name == "RECORD" else b"{}" if name.endswith(".json") else b"fixed metadata\n"
            self.write(self.wheel / evidence.DIST_INFO / name, data)
        self.receipt.write_text(json.dumps({"files": records, "package_inventory_sha256": "a" * 64}))
        distribution = SimpleNamespace(version=evidence.VERSION,
                                       files=[path.relative_to(self.wheel).as_posix() for path in self.wheel.rglob("*") if path.is_file()],
                                       locate_file=lambda name: self.wheel / name)
        self.distribution = distribution
        self.patcher = patch.object(evidence.importlib.metadata, "distribution", return_value=distribution)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def export(self):
        return evidence.export(self.installed, self.receipt, self.destination, "b" * 64)

    def test_exact_metadata_and_all_seven_shipped_native_files_are_bound(self):
        result = self.export()
        self.assertEqual(7, len(result["nativeFiles"]))
        self.assertEqual(4, len(result["metadata"]))
        self.assertEqual("b" * 64, result["msiSha256"])
        for record in result["metadata"]:
            data = (self.destination / record["path"]).read_bytes()
            self.assertEqual(record["bytes"], len(data))
            self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())

    def test_changed_wheel_native_file_fails_its_record(self):
        (self.wheel / "PIL/_avif.cp312-win_amd64.pyd").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "RECORD"):
            self.export()
        self.assertFalse(self.destination.exists())

    def test_changed_actual_msi_file_fails_before_export(self):
        (self.installed / "resources/phone-helper/_internal/PIL/_avif.cp312-win_amd64.pyd").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "MSI-installed"):
            self.export()

    def test_changed_candidate_native_hash_fails(self):
        receipt = json.loads(self.receipt.read_text())
        receipt["files"][0]["sha256"] = "0" * 64
        self.receipt.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "MSI candidate"):
            self.export()

    def test_oversized_or_missing_sbom_fails(self):
        sbom = self.wheel / evidence.DIST_INFO / f"sboms/pillow-{evidence.VERSION}.cdx.json"
        sbom.write_bytes(b" " * (256 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "fixed bound"):
            self.export()
        self.distribution.files.remove(sbom.relative_to(self.wheel).as_posix())
        with self.assertRaisesRegex(ValueError, "missing"):
            self.export()

    def test_wrong_wheel_version_fails(self):
        self.distribution.version = "12.2.0"
        with self.assertRaisesRegex(ValueError, "exact locked"):
            self.export()


if __name__ == "__main__":
    unittest.main()
