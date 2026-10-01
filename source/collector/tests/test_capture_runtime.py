from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from amplifai_phone.workspace import SESSION_MARKER

STUCK_SESSION = r"""
import asyncio
import json
import signal
import sys
from pathlib import Path
import amplifai_phone.backup_watchdog as deadline
from amplifai_phone.backup_watchdog import BackupCleanupIncomplete, BackupWatchdog, run_backup_session
from amplifai_phone.workspace import SESSION_MARKER, SessionWorkspace

deadline.BACKUP_IDLE_SECONDS = .01
deadline.CLEANUP_GRACE_SECONDS = .01
if sys.argv[1] == "interrupted":
    deadline.BACKUP_IDLE_SECONDS = 900
    def interrupt(_number, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)

async def collect():
    workspace = SessionWorkspace(Path(sys.argv[2]))
    with workspace as root:
        if sys.argv[1] == "interrupted":
            asyncio.get_running_loop().call_later(.01, signal.raise_signal, signal.SIGTERM)
        watchdog = BackupWatchdog()
        async def backup():
            watchdog.start()
            try:
                await asyncio.Event().wait()
            finally:
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        continue
        try:
            await run_backup_session(backup(), watchdog, workspace.preserve_for_recovery)
        except BackupCleanupIncomplete as error:
            print(json.dumps({"phase": "retained", "reason": error.reason, "marked": (root / SESSION_MARKER).is_file()}), flush=True)
            raise
    return {"kind": "capture"}

try:
    if sys.argv[1] == "old":
        result = asyncio.run(collect())
    else:
        import amplifai_phone.capture_runtime as runtime
        runtime.RUNTIME_CLEANUP_SECONDS = .05
        result = runtime.run_local_capture(collect())
except BackupCleanupIncomplete as error:
    print(json.dumps({"kind": "error", "code": error.code, "reason": error.reason}), flush=True)
    raise SystemExit(1)
else:
    print(json.dumps(result), flush=True)
"""

STUCK_GENERATOR = r"""
import asyncio
import json
import amplifai_phone.capture_runtime as runtime
from amplifai_phone.backup_watchdog import BackupCleanupIncomplete

runtime.RUNTIME_CLEANUP_SECONDS = .05
held = []
async def source():
    try:
        yield "synthetic"
    finally:
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                continue
async def collect():
    generator = source()
    held.append(generator)
    await anext(generator)
    return {"kind": "capture"}
try:
    result = runtime.run_local_capture(collect())
except BackupCleanupIncomplete as error:
    print(json.dumps({"kind": "error", "code": error.code}), flush=True)
    raise SystemExit(1)
else:
    print(json.dumps(result), flush=True)
"""


class CaptureRuntimeTest(unittest.TestCase):
    def _environment(self) -> dict[str, str]:
        return {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}

    def test_baseline_asyncio_run_hangs_after_watchdog_retains_session(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="amplifai-old-runtime-test-"
        ) as temporary:
            root = Path(temporary) / "sessions"
            with self.assertRaises(subprocess.TimeoutExpired) as hung:
                subprocess.run(
                    [sys.executable, "-c", STUCK_SESSION, "old", str(root)],
                    check=False,
                    env=self._environment(),
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                )
            output = hung.exception.stdout.decode()
            self.assertIn('"phase": "retained"', output)
            self.assertNotIn('"kind": "capture"', output)
            sessions = list(root.glob("session-*"))
            self.assertEqual(len(sessions), 1)
            self.assertTrue((sessions[0] / SESSION_MARKER).is_file())

    def test_stuck_upstream_teardown_exits_nonzero_without_capture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-runtime-test-") as temporary:
            root = Path(temporary) / "sessions"
            result = subprocess.run(
                [sys.executable, "-c", STUCK_SESSION, "new", str(root)],
                check=False,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=1,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(
                events[-1],
                {
                    "kind": "error",
                    "code": "cleanup_incomplete",
                    "reason": "backup_stalled",
                },
            )
            self.assertFalse(any(item.get("kind") == "capture" for item in events))
            sessions = list(root.glob("session-*"))
            self.assertEqual(len(sessions), 1)
            self.assertTrue((sessions[0] / SESSION_MARKER).is_file())
            self.assertEqual(sessions[0].stat().st_mode & 0o077, 0)
            self.assertEqual(result.stderr, "")

    def test_normal_success_closes_tasks_and_async_generators(self) -> None:
        from amplifai_phone.capture_runtime import run_local_capture

        closed: list[str] = []
        generators = []

        async def background(started: asyncio.Event) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append("task")

        async def source():
            try:
                yield "synthetic"
            finally:
                closed.append("generator")

        async def collect() -> str:
            started = asyncio.Event()
            asyncio.create_task(background(started))
            await started.wait()
            generator = source()
            generators.append(generator)
            await anext(generator)
            return "capture"

        self.assertEqual(run_local_capture(collect()), "capture")
        self.assertCountEqual(closed, ["task", "generator"])

    def test_signal_interruption_preserves_workspace_before_forced_teardown(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="amplifai-signal-runtime-test-"
        ) as temporary:
            root = Path(temporary) / "sessions"
            result = subprocess.run(
                [sys.executable, "-c", STUCK_SESSION, "interrupted", str(root)],
                check=False,
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=1,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(
                events[0], {"phase": "retained", "reason": "cancelled", "marked": True}
            )
            self.assertEqual(
                events[-1],
                {"kind": "error", "code": "cleanup_incomplete", "reason": "cancelled"},
            )
            self.assertFalse(any(item.get("kind") == "capture" for item in events))
            sessions = list(root.glob("session-*"))
            self.assertEqual(len(sessions), 1)
            self.assertTrue((sessions[0] / SESSION_MARKER).is_file())
            self.assertEqual(result.stderr, "")

    def test_normal_cancellation_finishes_cleanup_and_propagates(self) -> None:
        from amplifai_phone.capture_runtime import run_local_capture

        closed: list[bool] = []

        async def collect() -> None:
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        with self.assertRaises(asyncio.CancelledError):
            run_local_capture(collect())
        self.assertEqual(closed, [True])

    def test_incomplete_background_cleanup_never_returns_capture(self) -> None:
        from unittest.mock import patch

        from amplifai_phone.backup_watchdog import BackupCleanupIncomplete
        from amplifai_phone.capture_runtime import run_local_capture

        tasks: list[asyncio.Task[None]] = []

        async def background(started: asyncio.Event) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        continue

        async def collect() -> str:
            started = asyncio.Event()
            tasks.append(asyncio.create_task(background(started)))
            await started.wait()
            return "capture"

        with (
            patch("amplifai_phone.capture_runtime.RUNTIME_CLEANUP_SECONDS", 0.01),
            self.assertRaises(BackupCleanupIncomplete),
        ):
            run_local_capture(collect())
        self.assertTrue(all(task.done() for task in tasks))

    def test_infinite_async_generator_cleanup_exits_without_capture(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", STUCK_GENERATOR],
            check=False,
            env=self._environment(),
            capture_output=True,
            text=True,
            timeout=1,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"kind": "error", "code": "cleanup_incomplete"}
        )
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
