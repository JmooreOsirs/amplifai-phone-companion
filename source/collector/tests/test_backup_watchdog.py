from __future__ import annotations

import asyncio
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from amplifai_phone.backup_watchdog import (
    BACKUP_IDLE_SECONDS,
    MAX_BACKUP_ELAPSED_SECONDS,
    RECEIVE_CHUNK_BYTES,
    BackupCleanupIncomplete,
    BackupStalled,
    BackupTimeLimit,
    BackupWatchdog,
    observe_backup_receives,
    run_backup_session,
)
from amplifai_phone.ios_backup import collect_iphone
from amplifai_phone.workspace import SESSION_MARKER


async def checkpoint() -> None:
    ready = asyncio.get_running_loop().create_future()
    handle = asyncio.get_running_loop().call_soon(
        lambda: None if ready.done() else ready.set_result(None)
    )
    try:
        await ready
    finally:
        handle.cancel()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ByteReader:
    def __init__(self, payload: bytes, clock: Clock, seconds_per_read: float) -> None:
        self.payload = payload
        self.clock = clock
        self.seconds_per_read = seconds_per_read
        self.requests: list[int] = []

    async def read(self, size: int) -> bytes:
        await checkpoint()
        self.requests.append(size)
        self.clock.now += self.seconds_per_read
        result, self.payload = (
            self.payload[: min(size, 4096)],
            self.payload[min(size, 4096) :],
        )
        return result


class BackupWatchdogTest(unittest.IsolatedAsyncioTestCase):
    async def test_discarded_upload_bytes_survive_old_hour_cap_without_new_percent(
        self,
    ) -> None:
        from pymobiledevice3.service_connection import ServiceConnection, build_plist
        from pymobiledevice3.services.device_link import DeviceLink

        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        payload = b"synthetic" * (256 * 1024 // 9)

        def prefixed(value: bytes) -> bytes:
            return struct.pack(">I", len(value)) + value

        wire = (
            build_plist(["DLMessageUploadFiles", "", 7.0])
            + prefixed(b"synthetic-device-entry")
            + prefixed(b"discarded-file")
            + struct.pack(">IB", len(payload) + 1, 0xC)
            + payload
            + struct.pack(">IB", 1, 0)
            + b"\x00\x00\x00\x00"
            + build_plist(["DLMessageProcessMessage", {"ErrorCode": 0}])
        )
        reader = ByteReader(wire, clock, seconds_per_read=120)
        local_socket, peer_socket = socket.socketpair()
        try:
            connection = ServiceConnection(local_socket)
            connection.reader = reader
            connection.writer = MagicMock(drain=AsyncMock())
            await observe_backup_receives(SimpleNamespace(service=connection), watchdog)
            with tempfile.TemporaryDirectory(
                prefix="amplifai-receive-test-"
            ) as temporary:
                root = Path(temporary)
                link = DeviceLink(connection, root, preserve_file=lambda *_: False)
                progress: list[float] = []

                async def backup() -> None:
                    watchdog.start()
                    try:
                        await link.dl_loop(progress.append)
                        watchdog.backup_completed()
                    finally:
                        link.cleanup_discarded_files()

                await run_backup_session(
                    backup(), watchdog, lambda: self.fail("no residue")
                )
                self.assertEqual(progress, [7.0])
                self.assertGreater(clock.now, 3600)
                self.assertLess(clock.now, MAX_BACKUP_ELAPSED_SECONDS)
                self.assertEqual(list(root.iterdir()), [])
                self.assertLessEqual(max(reader.requests), RECEIVE_CHUNK_BYTES)
        finally:
            local_socket.close()
            peer_socket.close()

    async def test_no_receive_bytes_stalls_and_finishes_cleanup(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        started, cleanup = asyncio.Event(), asyncio.Event()

        async def backup() -> None:
            watchdog.start()
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()

        task = asyncio.create_task(
            run_backup_session(backup(), watchdog, lambda: self.fail("cleaned"))
        )
        await started.wait()
        clock.now = BACKUP_IDLE_SECONDS
        watchdog.wake()
        with self.assertRaises(BackupStalled):
            await task
        self.assertTrue(cleanup.is_set())

    async def test_absolute_limit_stops_even_with_continuing_bytes(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        capture: list[bool] = []
        cleanup = asyncio.Event()

        async def backup() -> None:
            watchdog.start()
            try:
                for _ in range(25):
                    clock.now += 600
                    watchdog.received(1)
                    await checkpoint()
                watchdog.backup_completed()
                capture.append(True)
            finally:
                cleanup.set()

        with self.assertRaises(BackupTimeLimit):
            await run_backup_session(backup(), watchdog, lambda: self.fail("cleaned"))
        self.assertEqual(capture, [])
        self.assertTrue(cleanup.is_set())

    async def test_incomplete_cancellation_flags_residue_and_blocks_late_reads(
        self,
    ) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        started, teardown, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        preserved: list[bool] = []
        capture: list[bool] = []

        async def backup() -> None:
            watchdog.start()
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                teardown.set()
                await release.wait()
                watchdog.received(1)
                capture.append(True)

        with patch("amplifai_phone.backup_watchdog.CLEANUP_GRACE_SECONDS", 0):
            task = asyncio.create_task(
                run_backup_session(backup(), watchdog, lambda: preserved.append(True))
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(BackupCleanupIncomplete) as stopped:
                await task
        self.assertEqual(stopped.exception.reason, "cancelled")
        self.assertEqual(preserved, [True])
        await teardown.wait()
        release.set()
        await checkpoint()
        self.assertEqual(capture, [])

    async def test_stall_with_hung_cleanup_preserves_its_primary_reason(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        started, release = asyncio.Event(), asyncio.Event()
        preserved: list[bool] = []

        async def backup() -> None:
            watchdog.start()
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await release.wait()

        with patch("amplifai_phone.backup_watchdog.CLEANUP_GRACE_SECONDS", 0):
            task = asyncio.create_task(
                run_backup_session(backup(), watchdog, lambda: preserved.append(True))
            )
            await started.wait()
            clock.now = BACKUP_IDLE_SECONDS
            watchdog.wake()
            with self.assertRaises(BackupCleanupIncomplete) as stopped:
                await task
        self.assertEqual(stopped.exception.reason, "backup_stalled")
        self.assertEqual(preserved, [True])
        release.set()
        await checkpoint()

    async def test_adapter_preserves_upstream_connection_terminated_error(self) -> None:
        from pymobiledevice3.exceptions import ConnectionTerminatedError
        from pymobiledevice3.service_connection import ServiceConnection

        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        local_socket, peer_socket = socket.socketpair()
        try:
            connection = ServiceConnection(local_socket)
            connection.reader = ByteReader(b"ab", clock, seconds_per_read=1)
            connection.writer = MagicMock()
            await observe_backup_receives(SimpleNamespace(service=connection), watchdog)
            watchdog.start()
            with self.assertRaises(ConnectionTerminatedError):
                await connection.recvall(3)
        finally:
            local_socket.close()
            peer_socket.close()


class CollectionAbortTest(unittest.IsolatedAsyncioTestCase):
    async def test_collection_cancel_cleans_session_and_never_parses_capture(
        self,
    ) -> None:
        await self._cancel_collection(hang_teardown=False)

    async def test_hung_service_exit_retains_private_marked_session(self) -> None:
        await self._cancel_collection(hang_teardown=True)

    async def _cancel_collection(self, *, hang_teardown: bool) -> None:
        import pymobiledevice3.lockdown
        import pymobiledevice3.services.mobilebackup2
        import pymobiledevice3.usbmux

        device = SimpleNamespace(
            is_usb=True,
            is_network=False,
            connection_type="USB",
            serial="SYNTHETIC-UDID",
        )
        lockdown = MagicMock(udid="SYNTHETIC-UDID")
        lockdown.__aenter__ = AsyncMock(return_value=lockdown)
        lockdown.__aexit__ = AsyncMock(return_value=False)
        service = MagicMock()
        service.__aenter__ = AsyncMock(return_value=service)
        service.__aexit__ = AsyncMock(return_value=False)
        service.service._ensure_started = AsyncMock(
            return_value=(asyncio.StreamReader(), MagicMock())
        )
        started, teardown, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def backup(**_options: object) -> None:
            started.set()
            await asyncio.Event().wait()

        async def exit_service(*_args: object) -> bool:
            teardown.set()
            if hang_teardown:
                await release.wait()
            return False

        service.backup = AsyncMock(side_effect=backup)
        service.__aexit__.side_effect = exit_service
        with (
            tempfile.TemporaryDirectory(prefix="amplifai-abort-test-") as temporary,
            patch.object(
                pymobiledevice3.usbmux, "list_devices", AsyncMock(return_value=[device])
            ),
            patch.object(
                pymobiledevice3.lockdown,
                "create_using_usbmux",
                AsyncMock(return_value=lockdown),
            ),
            patch.object(
                pymobiledevice3.services.mobilebackup2,
                "Mobilebackup2Service",
                return_value=service,
            ),
            patch("amplifai_phone.ios_backup.parse_selected_backup") as parse,
            patch(
                "amplifai_phone.backup_watchdog.CLEANUP_GRACE_SECONDS",
                0 if hang_teardown else 5,
            ),
        ):
            root = Path(temporary) / "sessions"
            task = asyncio.create_task(
                collect_iphone(
                    password_provider=lambda: self.fail("no password"),
                    sessions_root=root,
                )
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(
                BackupCleanupIncomplete if hang_teardown else asyncio.CancelledError
            ):
                await task
            parse.assert_not_called()
            await teardown.wait()
            sessions = list(root.glob("session-*"))
            self.assertEqual(len(sessions), int(hang_teardown))
            if hang_teardown:
                self.assertTrue((sessions[0] / SESSION_MARKER).is_file())
                self.assertEqual(sessions[0].stat().st_mode & 0o077, 0)
                release.set()
                await checkpoint()


if __name__ == "__main__":
    unittest.main()
