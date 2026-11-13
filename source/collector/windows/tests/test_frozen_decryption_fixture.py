"""Source IPC integration of the non-shipped frozen-decryption qualification."""
from __future__ import annotations

import json
import marshal
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from frozen_decryption_fixture import (
    CASES, DEFAULT_PASSWORD, MODULES, check_case, commands, production_manifest, run_matrix,
)


class FrozenDecryptionHarnessTest(unittest.TestCase):
    def test_real_parser_ipc_matrix_and_fresh_retry(self):
        # Every case launches a fresh process with actual redirected OS pipes.
        # This is a source test of the harness, never Windows/frozen evidence.
        script = Path(__file__).with_name("frozen_decryption_fixture.py").resolve()
        results = run_matrix([sys.executable, str(script), "--source-case"], os.environ.copy())
        self.assertEqual(len(results), len(CASES) + 2)
        self.assertEqual([item["case"] for item in results[-2:]], ["retry-wrong_password", "retry-valid"])
        self.assertTrue(all(item["ownedCleanup"] and item["passwordPipeUnchanged"] for item in results))
        self.assertTrue(all(item["productionModules"] == [] for item in results))
        optional = next(item for item in results if item["case"] == "both_optional_keys")
        self.assertEqual(optional["missingSources"], ["calls", "messages"])
        for case, prompts, parses, exit_code in (
            ("same-backup-wrong-correct", 2, 2, 0),
            ("same-backup-max-attempts", 3, 3, 1),
            ("same-backup-cancel", 2, 1, 130),
            ("same-backup-keybag-corrupt", 3, 3, 1),
            ("same-backup-manifest-invalid", 1, 1, 1),
        ):
            with self.subTest(case=case):
                item = next(item for item in results if item["case"] == case)
                self.assertEqual(item["acquisitionCount"], 1)
                self.assertEqual(item["passwordPrompts"], prompts)
                self.assertEqual(item["parseAttempts"], parses)
                self.assertEqual(item["exitCode"], exit_code)
                self.assertTrue(item["sameCompletedBackup"])

    def test_validator_rejects_unavailable_as_zero_history_and_secret_echo(self):
        script = Path(__file__).with_name("frozen_decryption_fixture.py").resolve()
        with tempfile.TemporaryDirectory(prefix="fictional-validator-") as temporary:
            root = Path(temporary)
            proof_path = root / "proof.json"
            result = subprocess.run(
                [sys.executable, str(script), "--source-case", "--case", "calls_wal_key",
                 "--proof", str(proof_path), "--sessions-root", str(root / "sessions")],
                input=commands("calls_wal_key"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60, check=False,
            )
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            check_case("calls_wal_key", result, proof)
            packets = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
            for packet in packets:
                if packet.get("kind") == "capture":
                    packet["missing"] = []
                    packet["unavailableReasons"] = {}
            changed = ("\n".join(json.dumps(packet) for packet in packets) + "\n").encode()
            with self.assertRaisesRegex(AssertionError, "explicit optional unavailability"):
                check_case("calls_wal_key", subprocess.CompletedProcess([], 0, changed, b""), proof)
            with self.assertRaisesRegex(AssertionError, "password"):
                check_case("calls_wal_key", subprocess.CompletedProcess([], 0, DEFAULT_PASSWORD.encode(), b""), proof)
            with self.assertRaisesRegex(AssertionError, "stderr"):
                check_case("calls_wal_key", subprocess.CompletedProcess([], 0, b"", b"private exception"), proof)

    def test_custody_rejects_stale_packaged_decryption_code(self):
        # Model only the archive-reading boundary. The code comparison itself
        # is real, and must reject a changed backup_files module in the helper.
        with tempfile.TemporaryDirectory(prefix="fictional-code-custody-") as temporary:
            root = Path(temporary)
            source = root / "source"
            helper = root / "AmplifaiPhoneHelper.exe"
            helper.write_bytes(b"Fictional archive reader fixture")
            codes = {}
            for module, relative in MODULES.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                contents = "def boundary(value):\n    return value + 1\n"
                path.write_text(contents, encoding="utf-8")
                codes[module] = compile(contents, "frozen/module.py", "exec", dont_inherit=True, optimize=0)

            class Archive:
                toc = {"PYZ.pyz": ("z",)}

                def open_embedded_archive(self, _name):
                    return self

                def extract(self, name):
                    return marshal.dumps(codes[name]) if name == "agent_entry" else codes[name]

            with (patch("PyInstaller.archive.readers.CArchiveReader", return_value=Archive()),
                  patch("frozen_decryption_fixture.subprocess.check_output", return_value=b"f" * 40 + b"\n")):
                manifest = production_manifest(source, helper)
                self.assertTrue(all(record["packagedHelperCodeMatchesSource"]
                                    for record in manifest["productionModules"]))
                codes["amplifai_phone.backup_files"] = compile(
                    "def boundary(value):\n    return value + 2\n", "frozen/module.py", "exec")
                with self.assertRaisesRegex(RuntimeError, "amplifai_phone.backup_files"):
                    production_manifest(source, helper)


if __name__ == "__main__":
    unittest.main()
