"""Capacity regressions. Sparse files here prove accounting, never throughput."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from amplifai_phone.workspace import MIN_FREE_BYTES, SessionWorkspace, WorkspaceError


class BackupCapacityTest(unittest.TestCase):
    def test_session_larger_than_one_gib_is_allowed_with_disk_reserve(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-capacity-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                # Intentional sparse fixture: reproduces the old logical-size rejection.
                with (directory / "synthetic-selected.sqlite").open("wb") as stream:
                    stream.truncate(1024 * 1024 * 1024 + 4096)
                with patch(
                    "amplifai_phone.workspace.shutil.disk_usage",
                    return_value=SimpleNamespace(free=MIN_FREE_BYTES + 3 * 1024**3),
                ):
                    workspace.check_bound()
            self.assertFalse(directory.exists())

    def test_retained_write_reserves_its_future_parsing_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-capacity-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with (
                workspace as directory,
                workspace.open_private(directory / "source") as stream,
            ):
                with patch(
                    "amplifai_phone.workspace.shutil.disk_usage",
                    return_value=SimpleNamespace(free=MIN_FREE_BYTES + 15),
                ):
                    with self.assertRaises(WorkspaceError) as error:
                        workspace.write_chunk(stream, b"12345678", reserve_copy=True)
                    self.assertEqual(error.exception.code, "workspace_low_space")
                    self.assertEqual(stream.tell(), 0)
                workspace.write_chunk(stream, b"12345678", reserve_copy=True)
                with (
                    patch("amplifai_phone.workspace.shutil.disk_usage", return_value=SimpleNamespace(free=MIN_FREE_BYTES + 8)),
                    workspace.open_private(directory / "extracted") as parsed,
                ):
                    workspace.write_chunk(parsed, b"12345678", consume_copy=True)
                    self.assertEqual(parsed.tell(), 8)

    def test_abandoned_size_is_exact_above_the_former_ceiling(self) -> None:
        from amplifai_phone.workspace import abandoned_sessions

        with tempfile.TemporaryDirectory(prefix="amplifai-capacity-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                for name in ("one", "two"):
                    with (directory / name).open("wb") as stream:
                        stream.truncate(1024**3 + 1)
                expected = sum(path.stat().st_size for path in directory.iterdir())
                with patch("amplifai_phone.workspace._pid_alive", return_value=False):
                    self.assertEqual(
                        abandoned_sessions(workspace.root)[0].bytes_on_disk, expected
                    )


if __name__ == "__main__":
    unittest.main()
