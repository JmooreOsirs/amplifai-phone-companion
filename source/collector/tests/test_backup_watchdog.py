from __future__ import annotations

import asyncio
import os
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from amplifai_phone.backup_watchdog import (
    BACKUP_IDLE_SECONDS,
    RECEIVE_CHUNK_BYTES,
    BackupCleanupIncomplete,
    BackupStalled,
    BackupWatchdog,
    observe_backup_receives,
    run_backup_session,
)
from amplifai_phone.ios_backup import collect_iphone
from amplifai_phone.windows_storage import private_windows_directory
from amplifai_phone.workspace import SESSION_MARKER, SessionWorkspace, WorkspaceError


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
    async def test_initial_silence_past_fifteen_minutes_can_then_transfer(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)

        async def backup() -> str:
            watchdog.start()
            clock.now = 20 * 60
            self.assertIsNone(watchdog._expired())
            await checkpoint()
            watchdog.file_transfer_started()
            watchdog.payload_received(1024)
            watchdog.file_transfer_completed()
            watchdog.backup_completed()
            return "completed"

        self.assertEqual(
            await run_backup_session(backup(), watchdog, lambda: self.fail("no residue")),
            "completed",
        )

    async def test_initial_control_traffic_cannot_extend_thirty_minute_limit(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        watchdog.start()
        for minute in range(1, 30):
            clock.now = minute * 60
            watchdog.received(1)
            self.assertIsNone(watchdog._expired())
        clock.now = 30 * 60
        with self.assertRaisesRegex(BackupStalled, "thirty-minute initial readiness"):
            watchdog.received(1)

    async def test_first_file_frame_keeps_fifteen_minute_stall_limit(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        watchdog.start()
        clock.now = 60
        watchdog.file_transfer_started()
        clock.now += BACKUP_IDLE_SECONDS
        self.assertIsInstance(watchdog._expired(), BackupStalled)

    async def test_late_first_payload_cannot_revive_expired_readiness(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        watchdog.start()
        clock.now = 30 * 60
        with self.assertRaises(BackupStalled):
            watchdog.payload_received(1024)
        self.assertIsInstance(watchdog._expired(), BackupStalled)

    async def test_discarded_upload_bytes_survive_old_hour_cap_without_new_percent(
        self,
    ) -> None:
        from amplifai_phone.backup_stream import StreamedDeviceLink
        from pymobiledevice3.service_connection import ServiceConnection, build_plist

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
            with tempfile.TemporaryDirectory(prefix="amplifai-receive-test-") as temporary:
                workspace = SessionWorkspace(Path(temporary) / "sessions")
                with workspace as root:
                    link = StreamedDeviceLink(
                        connection, root, workspace=workspace, watchdog=watchdog,
                        preserve_file=lambda *_: False,
                    )
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
                    self.assertLess(clock.now, 4 * 60 * 60)
                    self.assertEqual(
                        {path.name for path in root.iterdir()},
                        {SESSION_MARKER, "discarded-file"},
                    )
                    self.assertEqual((root / "discarded-file").stat().st_size, 0)
                    self.assertLessEqual(max(reader.requests), RECEIVE_CHUNK_BYTES)
        finally:
            local_socket.close()
            peer_socket.close()

    async def test_no_new_receive_bytes_stalls_and_finishes_cleanup(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        started, cleanup = asyncio.Event(), asyncio.Event()

        async def backup() -> None:
            watchdog.start()
            watchdog.payload_received(1)
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

    async def test_live_control_frames_do_not_look_like_a_stalled_finalization(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        watchdog.start()
        watchdog.file_transfer_started()
        watchdog.payload_received(1024)
        watchdog.file_transfer_completed()
        for minute in range(1, 31):
            clock.now = minute * 60
            watchdog.received(1)
            self.assertIsNone(watchdog._expired())
        watchdog.backup_completed()


    async def test_active_file_bytes_continue_past_old_total_budget(self) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        capture: list[bool] = []
        cleanup = asyncio.Event()

        async def backup() -> None:
            watchdog.start()
            try:
                for _ in range(40):
                    clock.now += 600
                    watchdog.payload_received(1024 * 1024)
                    await checkpoint()
                watchdog.backup_completed()
                capture.append(True)
            finally:
                cleanup.set()

        await run_backup_session(backup(), watchdog, lambda: self.fail("cleaned"))
        self.assertEqual(capture, [True])
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
            watchdog.payload_received(1)
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
    async def test_initial_deadline_covers_each_setup_await(self) -> None:
        for stage in ("discovery", "pairing", "service", "encryption", "reader"):
            with self.subTest(stage=stage):
                await self._stop_setup(stage, cancel=False)

    async def test_cancel_during_pairing_cleans_owned_session(self) -> None:
        await self._stop_setup("pairing", cancel=True)

    async def test_pairing_result_after_cancel_cannot_confirm_or_start_backup(self) -> None:
        started, teardown, release, closed = (asyncio.Event() for _ in range(4))
        device = SimpleNamespace(is_usb=True, is_network=False, connection_type="USB", serial="SYNTHETIC-UDID")
        lockdown = MagicMock(udid=device.serial)
        lockdown.__aenter__ = AsyncMock(return_value=lockdown)

        async def close(*_args):
            closed.set()
            return False

        lockdown.__aexit__ = AsyncMock(side_effect=close)

        async def late_pairing(**_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                teardown.set()
                await release.wait()
                return lockdown

        connections = []
        with (
            tempfile.TemporaryDirectory(prefix="amplifai-late-pairing-") as temporary,
            patch("pymobiledevice3.usbmux.list_devices", AsyncMock(return_value=[device])),
            patch("pymobiledevice3.lockdown.create_using_usbmux", AsyncMock(side_effect=late_pairing)),
            patch("pymobiledevice3.services.mobilebackup2.Mobilebackup2Service") as service,
            patch("amplifai_phone.backup_watchdog.CLEANUP_GRACE_SECONDS", 0),
        ):
            root = Path(temporary) / "sessions"
            task = asyncio.create_task(collect_iphone(
                password_provider=lambda: self.fail("no password"),
                connection_callback=connections.append, sessions_root=root,
            ))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(BackupCleanupIncomplete) as stopped:
                await asyncio.wait_for(task, 1)
            self.assertEqual(stopped.exception.reason, "cancelled")
            await teardown.wait()
            sessions = list(root.glob("session-*"))
            self.assertEqual(len(sessions), 1)
            self.assertTrue((sessions[0] / SESSION_MARKER).is_file())
            release.set()
            await asyncio.wait_for(closed.wait(), 1)
            self.assertEqual(connections, [])
            service.assert_not_called()

    async def _stop_setup(self, stage: str, *, cancel: bool) -> None:
        clock = Clock()
        watchdog = BackupWatchdog(clock=clock)
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def wait_at_boundary(*_args, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        device = SimpleNamespace(is_usb=True, is_network=False, connection_type="USB", serial="SYNTHETIC-UDID")
        lockdown = MagicMock(udid=device.serial)
        lockdown.__aenter__ = AsyncMock(return_value=lockdown)
        lockdown.__aexit__ = AsyncMock(return_value=False)
        lockdown.get_value = AsyncMock(return_value=True)
        service = MagicMock()
        service.__aenter__ = AsyncMock(return_value=service)
        service.__aexit__ = AsyncMock(return_value=False)
        service.backup = AsyncMock()
        service.service._ensure_started = AsyncMock(return_value=(asyncio.StreamReader(), MagicMock()))
        discovery = AsyncMock(return_value=[device])
        pairing = AsyncMock(return_value=lockdown)
        boundary = {"discovery": discovery, "pairing": pairing, "service": service.__aenter__,
                    "encryption": lockdown.get_value, "reader": service.service._ensure_started}[stage]
        boundary.side_effect = wait_at_boundary
        with (
            tempfile.TemporaryDirectory(prefix="amplifai-setup-deadline-") as temporary,
            patch("pymobiledevice3.usbmux.list_devices", discovery),
            patch("pymobiledevice3.lockdown.create_using_usbmux", pairing),
            patch("pymobiledevice3.services.mobilebackup2.Mobilebackup2Service", return_value=service),
            patch("amplifai_phone.ios_backup.BackupWatchdog", return_value=watchdog),
        ):
            root = Path(temporary) / "sessions"
            task = asyncio.create_task(collect_iphone(
                password_provider=lambda: self.fail("no password"),
                require_encrypted_backup=True, sessions_root=root,
            ))
            await asyncio.wait_for(started.wait(), 1)
            if cancel:
                task.cancel()
            else:
                clock.now = 30 * 60
                watchdog.wake()
            with self.assertRaises(asyncio.CancelledError if cancel else BackupStalled):
                await asyncio.wait_for(task, 1)
            self.assertTrue(cleaned.is_set())
            self.assertEqual(list(root.glob("session-*")), [])
            service.backup.assert_not_awaited()

    async def test_startup_storage_failure_is_reported_without_waiting(self) -> None:
        with (
            patch(
                "amplifai_phone.ios_backup.SessionWorkspace.__enter__",
                side_effect=WorkspaceError("synthetic storage unavailable"),
            ),
            self.assertRaisesRegex(WorkspaceError, "synthetic storage unavailable"),
        ):
            await asyncio.wait_for(self._cancel_collection(hang_teardown=False), 1)

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
            startup = asyncio.create_task(started.wait())
            try:
                done, _pending = await asyncio.wait(
                    (task, startup), timeout=20, return_when=asyncio.FIRST_COMPLETED
                )
                if task in done:
                    await task  # Surface early storage/setup failure, not a CI hang.
                    self.fail("collection returned before the synthetic backup started")
                self.assertIn(startup, done, "collection startup exceeded 20 seconds")
            finally:
                startup.cancel()
                await asyncio.gather(startup, return_exceptions=True)
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
                if os.name == "nt":
                    private_windows_directory(root)
                else:
                    self.assertEqual(sessions[0].stat().st_mode & 0o077, 0)
                release.set()
                await checkpoint()


if __name__ == "__main__":
    unittest.main()
