"""Byte-activity deadlines and bounded cancellation for one private backup session."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

BACKUP_IDLE_SECONDS = 15 * 60
MAX_CONTROL_ONLY_SECONDS = 60 * 60
CLEANUP_GRACE_SECONDS = 5
RECEIVE_CHUNK_BYTES = 128 * 1024
MAX_CONTROL_BYTES = 16 * 1024 * 1024


class BackupTimeLimit(TimeoutError):
    """Legacy error code from already-signed companions; no new session emits it."""
    code = "backup_time_limit"

    def __init__(self) -> None:
        super().__init__("Backup made too little data progress to continue safely")


class BackupStalled(TimeoutError):
    code = "backup_stalled"

    def __init__(self) -> None:
        super().__init__("No backup bytes were received for fifteen minutes")


class BackupNoFileProgress(TimeoutError):
    code = "backup_no_file_progress"

    def __init__(self) -> None:
        super().__init__("No backup file bytes were received for one hour")


class BackupCleanupIncomplete(RuntimeError):
    code = "cleanup_incomplete"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            "Backup cleanup did not finish; private session retained for recovery"
        )


class BackupWatchdog:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_received_at: float | None = None
        self._last_payload_at: float | None = None
        self._receiving_file = False
        self._wire_payload_accounting = False
        self._active = False
        self._aborted = False
        self._changed = asyncio.Event()

    def start(self) -> None:
        self.raise_if_aborted()
        self._last_received_at = self._last_payload_at = self._clock()
        self._receiving_file = False
        self._active = True
        self.wake()

    def received(self, byte_count: int) -> None:
        self.raise_if_aborted()
        if self._active and byte_count > 0:
            self._last_received_at = self._clock()
            self.wake()

    def payload_received(self, byte_count: int) -> None:
        """Only real file bytes extend the slow-transfer budget, not framing."""
        self.raise_if_aborted()
        if self._active and byte_count > 0:
            self._last_received_at = self._last_payload_at = self._clock()
            self.wake()

    def payload_sent(self, byte_count: int) -> None:
        # DeviceLink may request a retained file back during backup negotiation.
        # Actual outbound file bytes are progress too, never a plist keepalive.
        self.payload_received(byte_count)

    def file_transfer_started(self) -> None:
        if not self._receiving_file:
            self._last_payload_at = self._clock()
        self._receiving_file = True
        self.wake()

    def file_transfer_completed(self) -> None:
        self._receiving_file = False
        self.wake()

    @property
    def wire_payload_accounting(self) -> bool:
        return self._wire_payload_accounting

    def _activity_at(self) -> float:
        return self._last_payload_at if self._receiving_file else self._last_received_at

    def backup_completed(self) -> None:
        self.raise_if_aborted()
        error = self._expired()
        if error is not None:
            raise error
        self._active = False
        self.wake()

    def abort(self) -> None:
        self._aborted = True
        self.wake()

    def raise_if_aborted(self) -> None:
        if self._aborted:
            raise asyncio.CancelledError()

    def wake(self) -> None:
        self._changed.set()

    def _remaining_seconds(self) -> float | None:
        if not self._active:
            return None
        now = self._clock()
        return min(
            self._activity_at() + BACKUP_IDLE_SECONDS - now,
            self._last_payload_at + MAX_CONTROL_ONLY_SECONDS - now,
        )

    def _expired(self) -> BackupStalled | BackupNoFileProgress | None:
        if not self._active:
            return None
        now = self._clock()
        if now - self._activity_at() >= BACKUP_IDLE_SECONDS:
            return BackupStalled()
        if now - self._last_payload_at >= MAX_CONTROL_ONLY_SECONDS:
            return BackupNoFileProgress()
        return None

    async def wait_expired(self) -> BackupStalled | BackupNoFileProgress:
        while True:
            self._changed.clear()
            error = self._expired()
            if error is not None:
                return error
            try:
                await asyncio.wait_for(self._changed.wait(), self._remaining_seconds())
            except TimeoutError:
                # This timer only cancels our Event waiter, never the upstream backup.
                continue


class _ActivityReader:
    def __init__(self, reader: asyncio.StreamReader, watchdog: BackupWatchdog) -> None:
        self._reader = reader
        self._watchdog = watchdog

    def __getattr__(self, name: str) -> object:
        return getattr(self._reader, name)

    async def read(self, size: int = -1) -> bytes:
        self._watchdog.raise_if_aborted()
        payload = await self._reader.read(size)
        if self._watchdog._receiving_file:
            self._watchdog.payload_received(len(payload))
        else:
            self._watchdog.received(len(payload))
        return payload

    async def readexactly(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("readexactly size must not be negative")
        if size > MAX_CONTROL_BYTES:
            from .metadata import BackupControlFrameLimit

            raise BackupControlFrameLimit("Backup control frame exceeds safe memory bounds")
        payload = bytearray()
        while len(payload) < size:
            chunk = await self.read(min(RECEIVE_CHUNK_BYTES, size - len(payload)))
            if not chunk:
                raise asyncio.IncompleteReadError(bytes(payload), size)
            payload.extend(chunk)
        return bytes(payload)


async def observe_backup_receives(service: object, watchdog: BackupWatchdog) -> None:
    # In pinned pymobiledevice3 10.4.0, every DeviceLink receive (including rejected
    # payloads and plist framing) uses this session's ServiceConnection.reader.
    connection = service.service
    reader, _writer = await connection._ensure_started()
    connection.reader = _ActivityReader(reader, watchdog)
    watchdog._wire_payload_accounting = True


def _consume_task_error(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()


async def _cancel_session[Result](
    task: asyncio.Task[Result],
    watchdog: BackupWatchdog,
    reason: str,
    preserve_for_recovery: Callable[[], None],
) -> None:
    watchdog.abort()
    task.cancel()
    try:
        done, _pending = await asyncio.wait((task,), timeout=CLEANUP_GRACE_SECONDS)
    except asyncio.CancelledError:
        preserve_for_recovery()
        task.add_done_callback(_consume_task_error)
        raise BackupCleanupIncomplete(reason) from None
    if not done:
        preserve_for_recovery()
        task.add_done_callback(_consume_task_error)
        raise BackupCleanupIncomplete(reason)
    _consume_task_error(task)


async def run_backup_session[Result](
    operation: Awaitable[Result],
    watchdog: BackupWatchdog,
    preserve_for_recovery: Callable[[], None],
) -> Result:
    session = asyncio.ensure_future(operation)
    monitor = asyncio.create_task(watchdog.wait_expired())
    try:
        try:
            done, _pending = await asyncio.wait(
                (session, monitor), return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            await _cancel_session(session, watchdog, "cancelled", preserve_for_recovery)
            raise
        if session in done:
            return session.result()
        error = monitor.result()
        await _cancel_session(session, watchdog, error.code, preserve_for_recovery)
        raise error
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor
