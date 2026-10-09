"""Focused offline boundary checks for the private Windows MSI authoring path."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_windows_candidate import file_records
from build_windows_msi import NS, author_xml, checked_records


class InstallerAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "AmplifaiPhone"
        for name in (
            "AmplifaiPhone.exe", "_internal/runtime.dll",
            "resources/phone-helper/AmplifaiPhoneHelper.exe",
            "resources/legal/COPYRIGHT", "resources/legal/LICENSE",
            "resources/legal/THIRD-PARTY-NOTICES.txt",
        ):
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.receipt = self.root / "candidate-receipt.json"
        self.write_receipt()

    def write_receipt(self) -> None:
        records = file_records(self.package)
        digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.receipt.write_text(json.dumps({
            "status": "disposable-windows-candidate-not-a-release",
            "package_inventory_sha256": digest, "package_files": len(records),
            "package_bytes": sum(row["bytes"] for row in records), "files": records,
        }))

    def test_per_user_authoring_and_exact_payload(self) -> None:
        _, records = checked_records(self.package, self.receipt)
        tree = author_xml(records, self.package)
        ns = {"w": NS}
        package = tree.find(".//w:Package", ns)
        self.assertEqual(package.attrib["InstallScope"], "perUser")
        self.assertEqual(package.attrib["InstallPrivileges"], "limited")
        self.assertIsNotNone(tree.find(".//w:Directory[@Id='LocalAppDataFolder']", ns))
        self.assertIsNone(tree.find(".//w:Directory[@Id='ProgramFilesFolder']", ns))
        files = tree.findall(".//w:File", ns)
        self.assertEqual(len(files), len(records))
        self.assertEqual({Path(item.attrib["Source"]).relative_to(self.package).as_posix() for item in files},
                         {item["path"] for item in records})
        self.assertEqual(len(tree.findall(".//w:ComponentRef", ns)), len(records))
        self.assertEqual(len(tree.findall(".//w:Shortcut", ns)), 1)

    def test_changed_payload_fails(self) -> None:
        (self.package / "AmplifaiPhone.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "does not match"):
            checked_records(self.package, self.receipt)

    def test_missing_legal_fails_even_with_fresh_receipt(self) -> None:
        (self.package / "resources/legal/LICENSE").unlink()
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "missing staged legal"):
            checked_records(self.package, self.receipt)

    def test_case_collision_fails(self) -> None:
        records = file_records(self.package)
        duplicate = dict(next(item for item in records if item["path"] == "_internal/runtime.dll"))
        duplicate["path"] = "_internal/RUNTIME.dll"
        records.append(duplicate)
        records.sort(key=lambda row: row["path"])
        receipt = json.loads(self.receipt.read_text())
        receipt["files"] = records
        receipt["package_files"] = len(records)
        receipt["package_bytes"] = sum(row["bytes"] for row in records)
        receipt["package_inventory_sha256"] = hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.receipt.write_text(json.dumps(receipt))
        with patch("build_windows_msi.file_records", return_value=records):
            with self.assertRaisesRegex(ValueError, "case-folding"):
                checked_records(self.package, self.receipt)

    def test_linked_payload_fails(self) -> None:
        (self.package / "_internal/link.dll").symlink_to(self.package / "_internal/runtime.dll")
        with self.assertRaises(ValueError):
            checked_records(self.package, self.receipt)


if __name__ == "__main__":
    unittest.main()
