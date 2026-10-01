from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from amplifai_phone.workspace import (
    MIN_FREE_BYTES,
    ROOT_MARKER,
    SESSION_MARKER,
    SessionWorkspace,
    WorkspaceError,
    _pid_alive,
    abandoned_sessions,
    clear_abandoned,
)


class WorkspaceTest(unittest.TestCase):
    def test_windows_process_probe_never_sends_a_signal(self) -> None:
        with (
            patch("amplifai_phone.workspace.os.name", "nt"),
            patch(
                "amplifai_phone.workspace.windows_process_alive", return_value=True
            ) as probe,
            patch("amplifai_phone.workspace.os.kill") as signal,
        ):
            self.assertTrue(_pid_alive(321))
            probe.assert_called_once_with(321)
            signal.assert_not_called()

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
                self.assertRaisesRegex(WorkspaceError, "1 GB") as error,
            ):
                workspace.check_bound()
            self.assertEqual(error.exception.code, "workspace_size_limit")

    def test_entry_low_space_has_distinct_code_and_creates_no_session(self) -> None:
        workspace = SessionWorkspace(self.root)
        with (
            patch(
                "amplifai_phone.workspace.shutil.disk_usage",
                return_value=SimpleNamespace(free=MIN_FREE_BYTES - 1),
            ),
            self.assertRaises(WorkspaceError) as error,
        ):
            workspace.__enter__()
        self.assertEqual(error.exception.code, "workspace_low_space")
        self.assertIsNone(workspace.directory)
        self.assertEqual([item.name for item in self.root.iterdir()], [ROOT_MARKER])

    def test_runtime_low_space_has_distinct_code_and_cleans_session(self) -> None:
        workspace = SessionWorkspace(self.root)
        with workspace as directory:
            with (
                patch(
                    "amplifai_phone.workspace.shutil.disk_usage",
                    return_value=SimpleNamespace(free=MIN_FREE_BYTES - 1),
                ),
                self.assertRaises(WorkspaceError) as error,
            ):
                workspace.check_bound()
            self.assertEqual(error.exception.code, "workspace_low_space")
        self.assertFalse(directory.exists())

    def test_entry_storage_failure_is_sanitized(self) -> None:
        original = PermissionError("private backup /Users/private/backup is blocked")
        workspace = SessionWorkspace(self.root)
        with (
            patch("amplifai_phone.workspace.shutil.disk_usage", side_effect=original),
            self.assertRaises(WorkspaceError) as error,
        ):
            workspace.__enter__()
        self.assertEqual(error.exception.code, "workspace_unavailable")
        self.assertNotIn("/Users/private", str(error.exception))
        self.assertIs(error.exception.__cause__, original)
        self.assertIsNone(workspace.directory)

    def test_runtime_storage_failure_is_sanitized(self) -> None:
        original = OSError("private backup /Users/private/backup is unavailable")
        workspace = SessionWorkspace(self.root)
        with workspace as directory:
            with (
                patch(
                    "amplifai_phone.workspace.shutil.disk_usage", side_effect=original
                ),
                self.assertRaises(WorkspaceError) as error,
            ):
                workspace.check_bound()
            self.assertEqual(error.exception.code, "workspace_unavailable")
            self.assertNotIn("/Users/private", str(error.exception))
            self.assertIs(error.exception.__cause__, original)
        self.assertFalse(directory.exists())

    def test_directory_scan_failure_cannot_bypass_size_check(self) -> None:
        workspace = SessionWorkspace(self.root)
        original = PermissionError("cannot scan /Users/private/backup")
        with workspace:
            with (
                patch("amplifai_phone.workspace.os.scandir", side_effect=original),
                self.assertRaises(WorkspaceError) as error,
            ):
                workspace.check_bound()
            self.assertEqual(error.exception.code, "workspace_unavailable")
            self.assertNotIn("/Users/private", str(error.exception))

    def test_session_marker_failure_rolls_back_only_created_session(self) -> None:
        with SessionWorkspace(self.root):
            pass
        unrelated = self.root / "session-not-owned"
        unrelated.mkdir()
        (unrelated / "keep").write_bytes(b"owner data")
        real_open = os.open
        original = OSError("cannot write /Users/private/session marker")

        def fail_session_marker(path: Path, *args: object, **kwargs: object) -> int:
            if Path(path).name == SESSION_MARKER:
                raise original
            return real_open(path, *args, **kwargs)

        workspace = SessionWorkspace(self.root)
        with (
            patch("amplifai_phone.workspace.os.open", side_effect=fail_session_marker),
            self.assertRaises((WorkspaceError, OSError)) as error,
        ):
            workspace.__enter__()
        self.assertIsNone(workspace.directory)
        self.assertEqual(
            {item.name for item in self.root.iterdir()},
            {ROOT_MARKER, unrelated.name},
        )
        self.assertEqual((unrelated / "keep").read_bytes(), b"owner data")
        self.assertIsInstance(error.exception, WorkspaceError)
        self.assertEqual(error.exception.code, "workspace_unavailable")
        self.assertNotIn("/Users/private", str(error.exception))

    def test_root_marker_failure_does_not_strand_an_unowned_app_root(self) -> None:
        unrelated = self.parent / "owner-backup"
        unrelated.mkdir()
        (unrelated / "keep").write_bytes(b"owner")
        with (
            patch(
                "amplifai_phone.workspace._write_private",
                side_effect=OSError("private path"),
            ),
            self.assertRaises(WorkspaceError),
        ):
            SessionWorkspace(self.root).__enter__()
        self.assertFalse(self.root.exists())
        self.assertEqual((unrelated / "keep").read_bytes(), b"owner")
        with SessionWorkspace(self.root):
            pass

    def test_non_object_marker_is_ignored_without_deleting_its_session(self) -> None:
        workspace = SessionWorkspace(self.root)
        directory = workspace.__enter__()
        (directory / SESSION_MARKER).write_text("[]")
        with patch("amplifai_phone.workspace._pid_alive", return_value=False):
            self.assertEqual(abandoned_sessions(self.root), ())
            self.assertEqual(clear_abandoned(self.root), 0)
        self.assertTrue(directory.exists())

    def test_cleanup_failure_retains_primary_error_and_owned_session(self) -> None:
        workspace = SessionWorkspace(self.root)
        directory = workspace.__enter__()
        primary = WorkspaceError("Less than 2 GB remains", code="workspace_low_space")
        original = PermissionError("cannot remove /Users/private/backup")
        with (
            patch("amplifai_phone.workspace.shutil.rmtree", side_effect=original),
            self.assertRaises(WorkspaceError) as error,
        ):
            workspace.__exit__(type(primary), primary, None)
        self.assertEqual(error.exception.code, "workspace_cleanup")
        self.assertIs(error.exception.primary_error, primary)
        self.assertIs(error.exception.__cause__, original)
        self.assertNotIn("/Users/private", str(error.exception))
        self.assertTrue(directory.exists())
        self.assertEqual(workspace.directory, directory)
        workspace.__exit__(None, None, None)

    def test_incomplete_teardown_retains_marked_session_for_explicit_cleanup(
        self,
    ) -> None:
        workspace = SessionWorkspace(self.root)
        with workspace as directory:
            (directory / "synthetic-source").write_bytes(b"synthetic")
            workspace.preserve_for_recovery()
        self.assertTrue(directory.exists())
        self.assertEqual(abandoned_sessions(self.root), ())
        with patch("amplifai_phone.workspace._pid_alive", return_value=False):
            self.assertEqual(
                [item.name for item in abandoned_sessions(self.root)], [directory.name]
            )
            self.assertEqual(clear_abandoned(self.root, {directory.name}), 1)
        self.assertFalse(directory.exists())

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
        with self.assertRaises(WorkspaceError) as error:
            SessionWorkspace(self.root).__enter__()
        self.assertEqual(error.exception.code, "workspace_unsafe")
        self.assertEqual((self.root / "owner-backup").read_bytes(), b"do-not-touch")


if __name__ == "__main__":
    unittest.main()
