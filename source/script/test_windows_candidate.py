"""Synthetic build-boundary checks; not a Windows freeze or GUI receipt."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_windows_candidate import (
    GUI_FILES,
    clean_child_environment,
    file_records,
    freezer_command,
    gui_digest,
    parse_helper_packet,
)


class WindowsCandidateBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="amplifai-build-test-")
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def test_gui_receipt_binds_paths_and_every_production_module(self):
        for relative in GUI_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
        first = gui_digest(self.root)
        (self.root / GUI_FILES[1]).write_bytes(b"changed")
        self.assertNotEqual(gui_digest(self.root), first)

    def test_gui_receipt_rejects_missing_or_linked_input(self):
        with self.assertRaises((FileNotFoundError, ValueError)):
            gui_digest(self.root)
        for relative in GUI_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic")
        target = self.root / GUI_FILES[0]
        target.unlink()
        target.symlink_to(self.root / GUI_FILES[1])
        with self.assertRaisesRegex(ValueError, "linked"):
            gui_digest(self.root)

    def test_runtime_environment_does_not_modify_parent_or_private_pyi(self):
        original = {"PYTHONPATH": "source", "PYTHONHOME": "other", "_PYI_ARCHIVE_FILE": "parent", "KEEP": "yes"}
        with patch.dict(os.environ, original, clear=True):
            environment = clean_child_environment()
            self.assertEqual(dict(os.environ), original)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("PYTHONHOME", environment)
        self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(environment["_PYI_ARCHIVE_FILE"], "parent")

    def test_helper_and_window_use_distinct_frozen_runtimes(self):
        options = {"python": self.root / "python.exe", "source": self.root,
                   "build": self.root / "owned-build"}
        helper = freezer_command(**options, window=False)
        wrapper = freezer_command(**options, window=True)
        self.assertIn("--console", helper)
        self.assertNotIn("--windowed", helper)
        self.assertIn("--windowed", wrapper)
        self.assertNotIn("--console", wrapper)
        self.assertNotEqual(helper[helper.index("--distpath") + 1], wrapper[wrapper.index("--distpath") + 1])
        self.assertEqual(helper[-1], str(self.root / "collector/agent_entry.py"))
        self.assertEqual(wrapper[-1], str(self.root / "collector/windows/app.py"))
        for command in (helper, wrapper):
            self.assertIn("--noupx", command)
            self.assertNotIn("--uac-admin", command)
            self.assertNotIn("--add-data", command)

    def test_helper_packet_requires_one_bounded_json_line(self):
        valid = b'{"kind":"runtime","ready":true}\n'
        self.assertEqual(parse_helper_packet(valid, "runtime-check"), {"kind": "runtime", "ready": True})
        for data in (valid.rstrip(), valid + valid, b"not json\n", b"[]\n", b"x" * 65537):
            with self.subTest(data_length=len(data)), self.assertRaises(ValueError):
                parse_helper_packet(data, "runtime-check")

    def test_runtime_and_inspection_fail_closed(self):
        for data, mode in ((b'{"kind":"runtime","ready":false}\n', "runtime-check"),
                           (b'{"kind":"residue","sessions":[{"name":"synthetic"}]}\n', "inspect"),
                           (b'{"kind":"error","code":"runtime_missing"}\n', "runtime-check")):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                parse_helper_packet(data, mode)
        self.assertEqual(parse_helper_packet(b'{"kind":"residue","sessions":[]}\n', "inspect"),
                         {"kind": "residue", "sessions": []})
        with self.assertRaisesRegex(ValueError, "mode"):
            parse_helper_packet(b"{}\n", "connect")

    def test_file_inventory_binds_relative_bytes_and_rejects_links(self):
        nested = self.root / "_internal/runtime.dll"
        nested.parent.mkdir()
        nested.write_bytes(b"synthetic runtime")
        self.assertEqual(file_records(self.root), [{"path": "_internal/runtime.dll", "bytes": 17,
                                                  "sha256": hashlib.sha256(nested.read_bytes()).hexdigest()}])
        (self.root / "escape").symlink_to(nested)
        with self.assertRaisesRegex(ValueError, "linked"):
            file_records(self.root)


if __name__ == "__main__":
    unittest.main()
