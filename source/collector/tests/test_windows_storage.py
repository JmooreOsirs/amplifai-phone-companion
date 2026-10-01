from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from amplifai_phone.windows_storage import (
    _ACL_SCRIPT,
    private_windows_directory,
    windows_process_alive,
)


class WindowsPolicySourceTest(unittest.TestCase):
    def test_policy_starts_with_valid_error_preference_not_patch_markup(self) -> None:
        self.assertEqual(
            _ACL_SCRIPT.strip().splitlines()[0], "$ErrorActionPreference = 'Stop'"
        )


@unittest.skipUnless(os.name == "nt", "actual Windows security APIs required")
class WindowsStorageTest(unittest.TestCase):
    def test_acl_policy_parses_with_the_actual_windows_powershell_parser(self) -> None:
        environment = os.environ.copy()
        environment["AMPLIFAI_ACL_PARSE_INPUT"] = _ACL_SCRIPT
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; [void][scriptblock]::Create($env:AMPLIFAI_ACL_PARSE_INPUT)",
            ],
            env=environment,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            result.returncode, 0, "ACL policy must parse on the actual Windows runtime"
        )

    def test_new_directory_is_private_and_existing_inherited_directory_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-win-acl-") as parent:
            private = Path(parent) / "private"
            private.mkdir()
            with self.assertRaises(PermissionError):
                private_windows_directory(private)
            private_windows_directory(private, create=True)
            private_windows_directory(private)
            child = private / "source"
            child.write_bytes(b"synthetic-only")
            self.assertEqual(child.read_bytes(), b"synthetic-only")

    def test_process_probe_keeps_live_process_and_recognizes_exited_process(
        self,
    ) -> None:
        self.assertTrue(windows_process_alive(os.getpid()))
        process = subprocess.Popen(["cmd.exe", "/c", "exit", "0"])
        process.wait(timeout=10)
        self.assertFalse(windows_process_alive(process.pid))


if __name__ == "__main__":
    unittest.main()
