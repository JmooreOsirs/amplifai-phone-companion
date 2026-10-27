"""Focused offline boundary checks for the private Windows MSI authoring path."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from build_windows_candidate import (BUILD, CHANNEL, INSTALLER_VERSION, VERSION as CANDIDATE_VERSION,
                                     checked_candidate_identity, file_records)
from build_windows_msi import NS, PROTOCOL_KEY, UPGRADE_CODE, VERSION, author_xml, checked_records
from verify_windows_install import verify_setup_protocol


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
            "version": CANDIDATE_VERSION, "build": BUILD,
            "installer_version": INSTALLER_VERSION, "channel": CHANNEL,
            "package_inventory_sha256": digest, "package_files": len(records),
            "package_bytes": sum(row["bytes"] for row in records), "files": records,
        }))

    def test_per_user_authoring_and_exact_payload(self) -> None:
        _, records = checked_records(self.package, self.receipt)
        tree = author_xml(records, self.package)
        ns = {"w": NS}
        self.assertEqual(VERSION, "1.0.26")
        self.assertEqual(tree.find(".//w:Product", ns).attrib["UpgradeCode"], UPGRADE_CODE)
        self.assertEqual(UPGRADE_CODE, "0C4BE78C-C5E1-4EA9-841B-4D8D93F45871")
        self.assertIsNotNone(tree.find(".//w:MajorUpgrade", ns))
        self.assertEqual(tree.find(".//w:Product", ns).attrib["Version"], VERSION)
        package = tree.find(".//w:Package", ns)
        self.assertEqual(package.attrib["InstallScope"], "perUser")
        self.assertEqual(package.attrib["InstallPrivileges"], "limited")
        self.assertIsNotNone(tree.find(".//w:Directory[@Id='LocalAppDataFolder']", ns))
        self.assertIsNone(tree.find(".//w:Directory[@Id='ProgramFilesFolder']", ns))
        files = tree.findall(".//w:File", ns)
        self.assertEqual(len(files), len(records))
        self.assertEqual({Path(item.attrib["Source"]).relative_to(self.package).as_posix() for item in files},
                         {item["path"] for item in records})
        components = tree.findall(".//w:Component", ns)
        self.assertEqual(len(components), 5)
        self.assertEqual(len(tree.findall(".//w:ComponentRef", ns)), len(components))
        for component in components:
            registry = component.find("w:RegistryValue", ns)
            self.assertEqual(registry.attrib["Root"], "HKCU")
            self.assertEqual(registry.attrib["KeyPath"], "yes")
            self.assertIsNotNone(component.find("w:RemoveFolder", ns))
            self.assertTrue(all("KeyPath" not in file.attrib for file in component.findall("w:File", ns)))
        self.assertEqual(len(tree.findall(".//w:Shortcut", ns)), 1)
        self.assertIsNotNone(tree.find(".//w:RemoveFolder[@Directory='ApplicationProgramsFolder']", ns))

    def test_changed_payload_fails(self) -> None:
        (self.package / "AmplifaiPhone.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "does not match"):
            checked_records(self.package, self.receipt)

    def test_old_or_mismatched_candidate_identity_fails_even_with_exact_payload(self) -> None:
        original = json.loads(self.receipt.read_text())
        for changes in (
            {"version": "2026.10.26-rc25", "build": "26102625", "installer_version": "1.0.25"},
            {"build": "26102625"}, {"installer_version": "1.0.25"},
            {"channel": "different-test-channel"}, {"version": None},
        ):
            with self.subTest(changes=changes):
                self.receipt.write_text(json.dumps({**original, **changes}))
                with self.assertRaisesRegex(ValueError, "identity differs"):
                    checked_records(self.package, self.receipt)

    def test_build_identity_matches_runtime_without_executing_source(self) -> None:
        from updater import BUILD as RUNTIME_BUILD, CHANNEL as RUNTIME_CHANNEL
        from updater import INSTALLER_VERSION as RUNTIME_INSTALLER, VERSION as RUNTIME_VERSION
        self.assertEqual((CANDIDATE_VERSION, BUILD, INSTALLER_VERSION, CHANNEL),
                         (RUNTIME_VERSION, RUNTIME_BUILD, RUNTIME_INSTALLER, RUNTIME_CHANNEL))
        path = self.root / "collector/windows/updater.py"
        path.parent.mkdir(parents=True)
        pins = (f"VERSION = {CANDIDATE_VERSION!r}\nBUILD = {BUILD!r}\n"
                f"INSTALLER_VERSION = {INSTALLER_VERSION!r}\nCHANNEL = {CHANNEL!r}\n")
        path.write_text(pins + "raise RuntimeError('fictional source must not execute')\n")
        self.assertEqual(checked_candidate_identity(self.root), {
            "version": CANDIDATE_VERSION, "build": BUILD,
            "installer_version": INSTALLER_VERSION, "channel": CHANNEL,
        })
        for malformed in (pins.replace(BUILD, "26102625"),
                          pins + f"BUILD = {BUILD!r}\n", pins + "BUILD = str(26102726)\n",
                          pins + f"BUILD: str = {BUILD!r}\n", pins + "BUILD += '0'\n",
                          pins + f"BUILD, OTHER = ({BUILD!r}, 'unused')\n",
                          pins.replace("VERSION =", "VERSION = OTHER =", 1)):
            with self.subTest(malformed=malformed):
                path.write_text(malformed)
                with self.assertRaisesRegex(ValueError, "identity"):
                    checked_candidate_identity(self.root)

    def test_setup_protocol_is_per_user_and_quotes_the_installed_launcher_and_uri(self) -> None:
        _, records = checked_records(self.package, self.receipt)
        tree = author_xml(records, self.package)
        ns = {"w": NS}
        values = tree.findall(".//w:RegistryValue", ns)
        protocol = [item.attrib for item in values if item.attrib["Key"].startswith(PROTOCOL_KEY)]
        self.assertEqual(protocol, [
            {"Root": "HKCU", "Key": PROTOCOL_KEY, "Value": "URL:AMPLIFai Phone setup", "Type": "string"},
            {"Root": "HKCU", "Key": PROTOCOL_KEY, "Name": "URL Protocol", "Value": "", "Type": "string"},
            {"Root": "HKCU", "Key": PROTOCOL_KEY + r"\shell\open\command",
             "Value": '"[INSTALLFOLDER]AmplifaiPhone.exe" "%1"', "Type": "string"},
        ])
        self.assertIsNone(tree.find(".//w:RegistryKey[@ForceDeleteOnUninstall='yes']", ns))

    def test_installed_protocol_rejects_missing_stale_or_unremoved_registration(self) -> None:
        entries = {
            (PROTOCOL_KEY, ""): ("URL:AMPLIFai Phone setup", 1),
            (PROTOCOL_KEY, "URL Protocol"): ("", 1),
            (PROTOCOL_KEY + r"\shell\open\command", ""): (f'"{self.package / "AmplifaiPhone.exe"}" "%1"', 1),
        }

        def query(key: str, name: str) -> tuple[str, int]:
            try:
                return entries[(key, name)]
            except KeyError:
                raise FileNotFoundError from None

        registry = SimpleNamespace(HKEY_CURRENT_USER="HKCU", REG_SZ=1,
                                   OpenKey=lambda root, key: nullcontext(key), QueryValueEx=query)
        with patch.dict("sys.modules", {"winreg": registry}):
            self.assertEqual(verify_setup_protocol(self.package, removed=False),
                             {"setup_protocol_registered": True})
            with self.assertRaisesRegex(ValueError, "remains"):
                verify_setup_protocol(self.package, removed=True)
            command_key = (PROTOCOL_KEY + r"\shell\open\command", "")
            original = entries[command_key]
            for changed in (('"C:\\stale\\AmplifaiPhone.exe" "%1"', 1), (original[0], 2)):
                entries[command_key] = changed
                with self.assertRaisesRegex(ValueError, "differs"):
                    verify_setup_protocol(self.package, removed=False)
            entries.clear()
            with self.assertRaisesRegex(ValueError, "missing"):
                verify_setup_protocol(self.package, removed=False)
            self.assertEqual(verify_setup_protocol(self.package, removed=True),
                             {"setup_protocol_registered": False})

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
