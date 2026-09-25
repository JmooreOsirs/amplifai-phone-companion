from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amplifai_phone.workspace import (
    SessionWorkspace,
    WorkspaceError,
    abandoned_sessions,
    clear_abandoned,
)


class WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-workspace-test-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "sessions"

    def test_normal_lifecycle_removes_only_created_session(self) -> None:
        unrelated = self.parent / "owner-backup"
        unrelated.mkdir()
        (unrelated / "keep").write_text("not our data")
        workspace = SessionWorkspace(self.root)
        with workspace as directory:
            (directory / "synthetic-source").write_bytes(b"synthetic")
            self.assertEqual(abandoned_sessions(self.root), ())
            self.assertTrue(directory.exists())
        self.assertFalse(directory.exists())
        self.assertEqual((unrelated / "keep").read_text(), "not our data")

    def test_crash_residue_requires_explicit_cleanup(self) -> None:
        unrelated = self.root / "session-not-owned"
        workspace = SessionWorkspace(self.root)
        directory = workspace.__enter__()
        unrelated.mkdir()
        (unrelated / "keep").write_text("owner")
        (directory / "synthetic-source").write_bytes(b"synthetic")
        with patch("amplifai_phone.workspace._pid_alive", return_value=False):
            residue = abandoned_sessions(self.root)
            self.assertEqual([item.name for item in residue], [directory.name])
            self.assertTrue(directory.exists())
            with self.assertRaises(WorkspaceError):
                clear_abandoned(self.root, {"session-not-owned"})
            self.assertEqual(clear_abandoned(self.root, {directory.name}), 1)
        self.assertFalse(directory.exists())
        self.assertEqual((unrelated / "keep").read_text(), "owner")

    def test_bound_rejects_oversize_fixture(self) -> None:
        workspace = SessionWorkspace(self.root)
        with workspace as directory:
            (directory / "synthetic-source").write_bytes(b"large-but-synthetic")
            with (
                patch("amplifai_phone.workspace.MAX_SESSION_BYTES", 8),
                self.assertRaisesRegex(WorkspaceError, "1 GB"),
            ):
                workspace.check_bound()

    def test_failure_cleans_created_session_and_leaves_external_symlink_target(
        self,
    ) -> None:
        external = self.parent / "unrelated"
        external.mkdir()
        (external / "keep").write_text("owner")
        workspace = SessionWorkspace(self.root)
        with (
            self.assertRaisesRegex(ValueError, "synthetic failure"),
            workspace as directory,
        ):
            (directory / "outside-link").symlink_to(external, target_is_directory=True)
            raise ValueError("synthetic failure")
        self.assertFalse(directory.exists())
        self.assertEqual((external / "keep").read_text(), "owner")

    def test_existing_unmarked_root_is_not_claimed(self) -> None:
        self.root.mkdir()
        (self.root / "owner-backup").write_bytes(b"do-not-touch")
        with self.assertRaises(WorkspaceError):
            SessionWorkspace(self.root).__enter__()
        self.assertEqual((self.root / "owner-backup").read_bytes(), b"do-not-touch")


if __name__ == "__main__":
    unittest.main()
