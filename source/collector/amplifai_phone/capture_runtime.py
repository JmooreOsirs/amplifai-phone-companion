"""Owned capture event loop with a finite async-teardown budget."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from pymobiledevice3.exceptions import ConnectionTerminatedError

from . import backup_watchdog
from .backup_stream import DeviceBackupRejected
from .backup_watchdog import (
    BackupCleanupIncomplete,
    BackupNoFileProgress,
    BackupStalled,
    BackupTimeLimit,
)

RUNTIME_CLEANUP_SECONDS = 1
Result = TypeVar("Result")


def _consume_errors(tasks: set[asyncio.Task[object]]) -> bool:
    failed = False
    for task in tasks:
        if not task.cancelled() and task.exception() is not None:
            failed = True
    return failed


async def _shutdown(
    loop: asyncio.AbstractEventLoop, primary: asyncio.Task[object]
) -> tuple[set[asyncio.Task[object]], bool]:
    # A synchronous signal may interrupt run_until_complete while capture still
    # owns its workspace. Let its watchdog mark residue before closing any task.
    grace = backup_watchdog.CLEANUP_GRACE_SECONDS if not primary.done() else 0
    deadline = loop.time() + grace + RUNTIME_CLEANUP_SECONDS
    failed = False
    if not primary.done():
        primary.cancel()
        done, _pending = await asyncio.wait(
            (primary,), timeout=grace + RUNTIME_CLEANUP_SECONDS / 2
        )
        failed = _consume_errors(done)
    pending = asyncio.all_tasks(loop) - {asyncio.current_task()}
    for task in pending:
        task.cancel()
    if pending:
        done, pending = await asyncio.wait(
            pending, timeout=max(0, deadline - loop.time())
        )
        failed = _consume_errors(done) or failed
    generators = asyncio.create_task(loop.shutdown_asyncgens())
    done, _pending = await asyncio.wait(
        (generators,), timeout=max(0, deadline - loop.time())
    )
    failed = _consume_errors(done) or failed
    return asyncio.all_tasks(loop) - {asyncio.current_task()}, failed


async def _checkpoint() -> None:
    ready = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(ready.set_result, None)
    await ready


def _close_unfinished(
    loop: asyncio.AbstractEventLoop, tasks: set[asyncio.Task[object]]
) -> None:
    # The backup watchdog has already retained its workspace. Stop these paused
    # coroutines before closing the loop; do not let loop destruction resume writers.
    for task in tasks:
        try:
            task.get_coro().close()
        except RuntimeError:
            # A finally block awaiting during GeneratorExit is incomplete cleanup;
            # the caller always raises BackupCleanupIncomplete for this path.
            pass
        task.cancel()
    loop.run_until_complete(_checkpoint())
    _consume_errors({task for task in tasks if task.done()})


def _reason(error: BaseException | None) -> str:
    if isinstance(error, BackupCleanupIncomplete):
        return error.reason
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
        return "cancelled"
    if isinstance(error, (BackupStalled, BackupTimeLimit, BackupNoFileProgress)):
        return error.code
    if isinstance(error, DeviceBackupRejected):
        return "device_backup_failed"
    if isinstance(error, (ConnectionTerminatedError, ConnectionError)):
        return "connection_lost"
    if isinstance(error, TimeoutError):
        return "connection_timeout"
    return "cleanup_incomplete"


def run_local_capture(operation: Awaitable[Result]) -> Result:
    """Return only after async cleanup succeeds; propagate incomplete teardown.

    The collector and pinned backup path do not dispatch writes to executors.
    This deadline bounds asynchronous teardown, not arbitrary synchronous code.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runtime_failed = False

    def record_runtime_error(
        _loop: asyncio.AbstractEventLoop, _context: dict[str, object]
    ) -> None:
        nonlocal runtime_failed
        # An async-generator/callback failure blocks capture delivery. Keep raw
        # dependency exceptions out of the local protocol and process stderr.
        runtime_failed = True

    loop.set_exception_handler(record_runtime_error)
    error: BaseException | None = None
    result: Result
    try:
        primary = asyncio.ensure_future(operation, loop=loop)
        try:
            result = loop.run_until_complete(primary)
        except BaseException as exc:  # noqa: BLE001 - interrupts still require owned-loop cleanup
            error = exc
        pending, failed = loop.run_until_complete(_shutdown(loop, primary))
        if pending:
            _close_unfinished(loop, pending)
        if pending or failed or runtime_failed:
            raise BackupCleanupIncomplete(_reason(error)) from error
        if error is not None:
            raise error
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()
