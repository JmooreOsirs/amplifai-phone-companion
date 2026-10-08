"""Session-local adapters for pinned pymobiledevice3 10.4.0 DeviceLink.

The upstream selection protocol is retained. File frames are drained in small
chunks, including rejected payloads; selected writes reserve their parsing copy.
No process-global SDK patch, private path, filename or content enters progress.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import struct
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from pymobiledevice3.services.device_link import (
    BULK_OPERATION_ERROR,
    CODE_ERROR_LOCAL,
    CODE_ERROR_REMOTE,
    CODE_FILE_DATA,
    CODE_SUCCESS,
    ERRNO_TO_DEVICE_ERROR,
    FILE_TRANSFER_TERMINATOR,
    DeviceLink,
)

from .backup_watchdog import RECEIVE_CHUNK_BYTES, BackupWatchdog
from .metadata import SourceCapacityLimit, UnsupportedSchema
from .workspace import SessionWorkspace, WorkspaceError

MAX_PATH_BYTES = 4096
NO_COPY_METADATA = frozenset({"Info.plist", "Status.plist", "Manifest.plist"})


class DeviceBackupRejected(RuntimeError):
    def __init__(self, device_code: int | None) -> None:
        self.device_code = device_code
        super().__init__("The iPhone rejected the backup operation")


@dataclass(frozen=True)
class TransferProgress:
    receivedBytes: int
    retainedBytes: int
    discardedBytes: int
    filesReceived: int
    elapsedSeconds: float
    bytesPerSecond: float


class StreamedDeviceLink(DeviceLink):
    def __init__(
        self,
        service,
        root_path: Path,
        *,
        workspace: SessionWorkspace,
        watchdog: BackupWatchdog,
        preserve_file=None,
        transfer_callback: Callable[[dict[str, object]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(service, root_path, preserve_file=preserve_file)
        self.workspace = workspace
        self.watchdog = watchdog
        self._transfer_callback = transfer_callback
        self._clock = clock
        self._started = clock()
        self._last_emitted = self._started
        self._received = self._retained = self._files = 0
        self._reserve_current_copy = False
        self._reserved_paths: dict[Path, int] = {}

    async def receive_message(self):
        message = await super().receive_message()
        if message[0] == "DLMessageProcessMessage":
            status = message[1]
            if isinstance(status, dict) and status.get("ErrorCode") != 0:
                raw_code = status.get("ErrorCode")
                device_code = (
                    raw_code
                    if isinstance(raw_code, int)
                    and not isinstance(raw_code, bool)
                    and -(2**31) <= raw_code <= 2**32 - 1
                    else None
                )
                raise DeviceBackupRejected(device_code)
        return message

    def _path(self, name: str, *, create_parents: bool = False) -> Path:
        if not isinstance(name, str) or not name or len(name.encode()) > MAX_PATH_BYTES:
            raise UnsupportedSchema("Invalid backup path")
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or ":" in name
            or "\x00" in name
        ):
            raise WorkspaceError("Invalid backup path", code="workspace_unsafe")
        if any(
            part.startswith(".amplifai-") or part == "pairing" for part in path.parts
        ):
            raise WorkspaceError(
                "Backup path overlaps private session controls", code="workspace_unsafe"
            )
        return self.workspace.checked_path(
            self.root_path.joinpath(*path.parts), create_parents=create_parents
        )

    def publish_progress(self, *, final: bool = False) -> None:
        now = self._clock()
        # A DeviceLink handshake/preflight failure must not look like a backup
        # file transfer merely because the context manager is closing.
        if self._received == 0 and self._files == 0:
            return
        if self._transfer_callback is None or (
            not final and now - self._last_emitted < 0.5
        ):
            return
        elapsed = max(0.001, now - self._started)
        value = TransferProgress(
            self._received,
            self._retained,
            self._received - self._retained,
            self._files,
            elapsed,
            self._received / elapsed,
        )
        self._transfer_callback({"stage": "backup", **asdict(value)})
        self._last_emitted = now

    async def _prefixed_recv(self) -> str:
        (size,) = struct.unpack(">I", await self._recvall(4))
        if size > MAX_PATH_BYTES:
            raise SourceCapacityLimit("Backup control path exceeds safe memory bounds")
        try:
            return (await self._recvall(size)).decode("utf-8")
        except UnicodeError:
            raise UnsupportedSchema("Invalid backup path encoding") from None

    async def _frame(self) -> tuple[int, int]:
        (size,) = struct.unpack(">I", await self._recvall(4))
        if size < 1:
            raise UnsupportedSchema("Invalid backup frame length")
        code = (await self._recvall(1))[0]
        return size - 1, code

    async def _consume_file_transfer(
        self, size: int, code: int, destination: BinaryIO | None = None
    ) -> tuple[int, int]:
        while code == CODE_FILE_DATA:
            if size <= 0:
                raise UnsupportedSchema("Invalid empty backup data frame")
            self.watchdog.file_transfer_started()
            while size:
                self.watchdog.raise_if_aborted()
                payload = await self._recvall(min(size, RECEIVE_CHUNK_BYTES))
                self.watchdog.raise_if_aborted()  # no late write after bounded teardown
                if not payload:
                    raise UnsupportedSchema("Incomplete backup data frame")
                if not self.watchdog.wire_payload_accounting:
                    self.watchdog.payload_received(len(payload))
                if destination is not None:
                    self.workspace.write_chunk(
                        destination, payload, reserve_copy=self._reserve_current_copy
                    )
                    self._retained += len(payload)
                self._received += len(payload)
                size -= len(payload)
                self.publish_progress()
                await asyncio.sleep(0)  # bounded checkpoint even with a buffered socket
            self.watchdog.file_transfer_completed()
            size, code = await self._frame()
        return size, code

    async def upload_files(self, _message) -> None:
        while True:
            self.watchdog.raise_if_aborted()
            device_name = await self._prefixed_recv()
            if not device_name:
                break
            file_name = await self._prefixed_recv()
            path = self._path(file_name)
            size, code = await self._frame()
            keep = (
                self.preserve_file(file_name, device_name)
                if self.preserve_file
                else True
            )
            self._reserve_current_copy = keep and path.name not in NO_COPY_METADATA
            retained_before = self._retained
            if keep:
                previous = self._reserved_paths.pop(path, 0)
                with self.workspace.open_private(path) as destination:
                    self.workspace.release_copy_budget(previous)
                    size, code = await self._consume_file_transfer(
                        size, code, destination
                    )
                if self._reserve_current_copy:
                    self._reserved_paths[path] = self._retained - retained_before
                self._discarded_files.discard(Path(file_name))
            else:
                # The device may read back a filtered file during finalization.
                # Preserve the empty protocol entry without retaining its payload
                # or growing the SDK's discarded-path set.
                previous = self._reserved_paths.pop(path, 0)
                with self.workspace.open_private(path):
                    self.workspace.release_copy_budget(previous)
                size, code = await self._consume_file_transfer(size, code)
            self._files += 1
            if code == CODE_ERROR_REMOTE:
                if size > MAX_PATH_BYTES:
                    raise SourceCapacityLimit(
                        "Backup control error exceeds safe memory bounds"
                    )
                await self._recvall(size)  # discard the device's private error text
                if keep and path.name != "backup_manifest.db":
                    raise UnsupportedSchema(
                        "Selected backup file was not transferred completely"
                    )
            elif code != CODE_SUCCESS or size:
                raise UnsupportedSchema("Invalid backup transfer terminator")
            self.publish_progress(final=True)
        await self.status_response(0)

    async def get_free_disk_space(self, _message) -> None:
        # DeviceLink preflights the complete logical stream, while only selected
        # files consume disk. Real writes remain guarded by workspace.check_bound.
        await self.status_response(
            0, status_dict=self.workspace.advertised_stream_capacity_bytes()
        )

    async def create_directory(self, message) -> None:
        self._path(message[1], create_parents=True).mkdir(mode=0o700, exist_ok=True)
        await self.status_response(0)

    async def contents_of_directory(self, message) -> None:
        path = self._path(message[1])
        if path.exists():
            await super().contents_of_directory(message)
        else:
            await self.status_response(0, status_dict={})

    async def download_files(self, message) -> None:
        # Same pinned DeviceLink response/framing contract, bounded outbound reads.
        status = {}
        for name in message[1]:
            path = self._path(name)
            encoded = name.encode()
            await self._sendall(struct.pack(">I", len(encoded)) + encoded)
            try:
                with path.open("rb") as reader:
                    while payload := reader.read(RECEIVE_CHUNK_BYTES):
                        self.watchdog.raise_if_aborted()
                        await self._sendall(
                            struct.pack(">IB", len(payload) + 1, CODE_FILE_DATA)
                            + payload
                        )
                        self.watchdog.payload_sent(len(payload))
                        await asyncio.sleep(0)
                await self._sendall(struct.pack(">IB", 1, CODE_SUCCESS))
            except OSError as error:
                reason = "Local backup file unavailable"
                status[name] = {
                    "DLFileErrorString": reason,
                    "DLFileErrorCode": ctypes.c_uint64(
                        ERRNO_TO_DEVICE_ERROR.get(error.errno, -11)
                    ).value,
                }
                await self._sendall(
                    struct.pack(">IB", len(reason) + 1, CODE_ERROR_LOCAL)
                    + reason.encode()
                )
        await self._sendall(FILE_TRANSFER_TERMINATOR)
        await self.status_response(
            BULK_OPERATION_ERROR if status else 0,
            "Multi status" if status else "",
            status if status else None,
        )

    async def move_items(self, message) -> None:
        movements = []
        for source, destination in message[1].items():
            old = self._path(source)
            new = self._path(destination, create_parents=True)
            if old.exists() and new.exists() and os.path.samefile(old, new):
                raise WorkspaceError(
                    "Backup move overlaps its source", code="workspace_unsafe"
                )
            if new.is_dir():
                new = new / old.name
            movements.append((old, new, old.is_file()))
        await super().move_items(message)
        for old, new, source_was_file in movements:
            if source_was_file:
                self.workspace.release_copy_budget(self._reserved_paths.pop(new, 0))
            moved = {
                path: size
                for path, size in self._reserved_paths.items()
                if path == old or old in path.parents
            }
            for path, size in moved.items():
                target = new / path.relative_to(old)
                self.workspace.release_copy_budget(self._reserved_paths.pop(target, 0))
                self._reserved_paths.pop(path)
                self._reserved_paths[target] = size

    async def copy_item(self, message) -> None:
        source = self._path(message[1])
        destination = self._path(message[2], create_parents=True)
        if source.exists():
            if source.is_file() and destination.is_dir():
                destination = self.workspace.checked_path(destination / source.name)
            if (
                destination.exists() and os.path.samefile(source, destination)
            ) or source in destination.parents:
                raise WorkspaceError(
                    "Backup copy overlaps its source", code="workspace_unsafe"
                )
            if source.is_dir():
                self.watchdog.raise_if_aborted()
                destination.mkdir(mode=0o700, exist_ok=False)
                await asyncio.sleep(0)
                for directory in (path for path in source.rglob("*") if path.is_dir()):
                    self.watchdog.raise_if_aborted()
                    self.workspace.checked_path(directory)
                    (destination / directory.relative_to(source)).mkdir(mode=0o700)
                    await asyncio.sleep(0)
            files = (
                (source,)
                if source.is_file()
                else (path for path in source.rglob("*") if path.is_file())
            )
            for path in files:
                self.watchdog.raise_if_aborted()
                self.workspace.checked_path(path)
                target = (
                    destination
                    if source.is_file()
                    else destination / path.relative_to(source)
                )
                reserve = path in self._reserved_paths
                with (
                    path.open("rb") as reader,
                    self.workspace.open_private(target) as writer,
                ):
                    self.workspace.release_copy_budget(
                        self._reserved_paths.pop(target, 0)
                    )
                    while payload := reader.read(RECEIVE_CHUNK_BYTES):
                        self.watchdog.raise_if_aborted()
                        self.workspace.write_chunk(
                            writer, payload, reserve_copy=reserve
                        )
                        await asyncio.sleep(0)
                if reserve:
                    self._reserved_paths[target] = target.stat().st_size
        await self.status_response(0)

    async def remove_items(self, message) -> None:
        paths = [self._path(name) for name in message[1]]
        await super().remove_items(message)
        for path in tuple(self._reserved_paths):
            if any(path == removed or removed in path.parents for removed in paths):
                self.workspace.release_copy_budget(self._reserved_paths.pop(path))


def bind_streamed_device_link(
    service,
    workspace: SessionWorkspace,
    watchdog: BackupWatchdog,
    transfer_callback: Callable[[dict[str, object]], None] | None = None,
) -> None:
    """Replace only this service instance's documented DeviceLink factory seam."""

    @asynccontextmanager
    async def device_link(
        backup_directory: Path, filter_callback=None, password: str = ""
    ):
        if backup_directory != workspace.directory:
            raise WorkspaceError(
                "Backup factory is outside its session", code="workspace_unsafe"
            )
        await service.connect()
        link = StreamedDeviceLink(
            service.service,
            backup_directory,
            workspace=workspace,
            watchdog=watchdog,
            preserve_file=lambda name, device: service.should_preserve_backup_file(
                name, device, filter_callback
            ),
            transfer_callback=transfer_callback,
        )
        await link.version_exchange()
        await service.version_exchange(link)
        try:
            yield link
        finally:
            link.publish_progress(final=True)
            await link.disconnect()

    service.device_link = device_link
