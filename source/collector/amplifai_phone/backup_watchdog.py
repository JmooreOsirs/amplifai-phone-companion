"""Byte-activity deadlines and bounded cancellation for one private backup session."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TypeVar

MAX_BACKUP_ELAPSED_SECONDS = 4 * 60 * 60
BACKUP_IDLE_SECONDS = 15 * 60
CLEANUP_GRACE_SECONDS = 5
RECEIVE_CHUNK_BYTES = 128 * 1024

Result = TypeVar("Result")


class BackupTimeLimit(TimeoutError):
    code = "backup_time_limit"

    def __init__(self) -> None:
        super().__init__("Backup reached the four-hour elapsed limit")


class BackupStalled(TimeoutError):
    code = "backup_stalled"

    def __init__(self) -> None:
        super().__init__("No backup bytes were received for fifteen minutes")


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
        self._started_at: float | None = None
        self._last_received_at: float | None = None
        self._active = False
        self._aborted = False
        self._changed = asyncio.Event()

    def start(self) -> None:
        self.raise_if_aborted()
        self._started_at = self._last_received_at = self._clock()
        self._active = True
        self.wake()

    def received(self, byte_count: int) -> None:
        self.raise_if_aborted()
        if self._active and byte_count > 0:
            self._last_received_at = self._clock()
            self.wake()

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
            self._started_at + MAX_BACKUP_ELAPSED_SECONDS - now,
            self._last_received_at + BACKUP_IDLE_SECONDS - now,
        )

    def _expired(self) -> BackupTimeLimit | BackupStalled | None:
        if not self._active:
            return None
        now = self._clock()
        if now - self._started_at >= MAX_BACKUP_ELAPSED_SECONDS:
            return BackupTimeLimit()
        if now - self._last_received_at >= BACKUP_IDLE_SECONDS:
            return BackupStalled()
        return None

    async def wait_expired(self) -> BackupTimeLimit | BackupStalled:
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
        self._watchdog.received(len(payload))
        return payload

    async def readexactly(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("readexactly size must not be negative")
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


def _consume_task_error(task: asyncio.Task[object]) -> None:
    if not task.cancelled():
        task.exception()


async def _cancel_session(
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


async def run_backup_session(
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
