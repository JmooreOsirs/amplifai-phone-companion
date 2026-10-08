from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import helper_process


class HelperBoundaryTests(unittest.TestCase):
    def test_malformed_stdout_closes_input_and_drains_without_automatic_termination(self):
        events = []
        process = Mock()
        process.stdout = io.StringIO("not-json\nprivate-details-not-forwarded\n")
        process.stdin = io.StringIO()
        process.poll.return_value = None
        process.wait.return_value = 130
        helper = helper_process.HelperProcess([sys.executable], events.append)
        helper.process = process
        helper._read(process)
        process.terminate.assert_not_called()
        self.assertTrue(process.stdin.closed)
        self.assertEqual(events, [{"kind": "error", "code": "protocol", "cleanupRequired": True},
                                  {"kind": "process-exit", "code": 130}])
        self.assertNotIn("private-details", repr(events))

    def test_helper_frame_cannot_impersonate_the_owned_process_exit(self):
        events = []
        process = Mock()
        process.stdout = io.StringIO('{"kind":"process-exit","code":0}\n')
        process.stdin = io.StringIO()
        process.poll.return_value = None
        process.wait.return_value = 130
        helper = helper_process.HelperProcess([sys.executable], events.append)
        helper._read(process)
        self.assertEqual(events, [{"kind": "error", "code": "protocol", "cleanupRequired": True},
                                  {"kind": "process-exit", "code": 130}])
        process.terminate.assert_not_called()

    def test_actual_malformed_synthetic_helper_observes_stdin_eof_and_exits(self):
        events = []
        fixture = str(Path(__file__).with_name("fake_helper.py"))
        helper = helper_process.HelperProcess([sys.executable, fixture], events.append)
        helper.start("clear-residue")
        helper.join(3)
        self.assertEqual(events, [{"kind": "error", "code": "protocol", "cleanupRequired": True},
                                  {"kind": "process-exit", "code": 130}])

    def test_launch_is_private_and_isolated_from_cwd_and_parent_frozen_runtime(self):
        helper = helper_process.HelperProcess([sys.executable], lambda _event: None)
        with (patch.dict(os.environ, {"PYTHONPATH": "untrusted-modules"}),
              patch.object(helper_process.subprocess, "Popen") as spawn,
              patch.object(helper_process.threading, "Thread")):
            helper.start("inspect")
        args, options = spawn.call_args
        self.assertEqual(args[0], [sys.executable, "inspect"])
        self.assertEqual(options["cwd"], str(Path(sys.executable).parent))
        self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertNotIn("PYTHONPATH", options["env"])
        self.assertFalse(options["shell"])
        self.assertTrue(options["close_fds"])
        self.assertEqual(options["stdin"], helper_process.subprocess.PIPE)
        self.assertEqual(options["stderr"], helper_process.subprocess.DEVNULL)


class FrozenResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-win-resources-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.exe = self.root / "AmplifaiPhone.exe"
        self.exe.write_bytes(b"synthetic-executable")
        self.resources = self.root / "resources"
        self.resources.mkdir()
        self.logo = self.resources / "amplifai-by-nexus-original.png"
        self.logo.write_bytes(b"synthetic-logo")
        self.bundle = self.resources / "phone-helper"
        self.bundle.mkdir()
        self.helper = self.bundle / "AmplifaiPhoneHelper.exe"
        self.helper.write_bytes(b"synthetic-helper")
        self.runtime = self.bundle / "_internal"
        self.runtime.mkdir()

    def test_fixed_frozen_complete_bundle_is_the_only_resource_contract(self):
        self.assertEqual(helper_process.bundled_resources(self.exe, frozen=True),
                         (self.helper, self.logo))
        with self.assertRaises(ValueError):
            helper_process.bundled_resources(self.exe, frozen=False)
        self.runtime.rmdir()
        with self.assertRaises(ValueError):
            helper_process.bundled_resources(self.exe, frozen=True)

    def test_symlink_escape_is_rejected_without_path_or_raw_error_disclosure(self):
        foreign = self.root / "foreign.exe"
        foreign.write_bytes(b"synthetic-foreign")
        self.helper.unlink()
        self.helper.symlink_to(foreign)
        with self.assertRaises(ValueError) as caught:
            helper_process.bundled_resources(self.exe, frozen=True)
        self.assertNotIn(str(foreign), str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_unreadable_runtime_inventory_is_not_accepted_as_complete(self):
        def unreadable(_root, **options):
            if options.get("onerror"):
                options["onerror"](OSError("private runtime detail"))
            return []
        with patch.object(helper_process.os, "walk", unreadable):
            with self.assertRaises(ValueError) as caught:
                helper_process.bundled_resources(self.exe, frozen=True)
        self.assertNotIn("private runtime detail", str(caught.exception))

    def test_internal_runtime_link_is_rejected_even_with_contained_entrypoint(self):
        foreign = self.root / "foreign.dll"
        foreign.write_bytes(b"synthetic-runtime")
        (self.runtime / "linked.dll").symlink_to(foreign)
        with self.assertRaises(ValueError):
            helper_process.bundled_resources(self.exe, frozen=True)


if __name__ == "__main__":
    unittest.main()
