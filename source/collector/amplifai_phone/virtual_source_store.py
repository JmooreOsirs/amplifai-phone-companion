"""Offline selected-source DeviceLink store prototype; not wired to collection.

The backend in the contract tests stands in for private object storage. This
module makes no network request and grants no cloud consent or account access.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol, Self

from .backup_watchdog import RECEIVE_CHUNK_BYTES

INPUT_CHUNK_BYTES = RECEIVE_CHUNK_BYTES
OBJECT_CHUNK_BYTES = 8 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_PATH_PARTS = 64
APPLE_EPOCH_SECONDS = 978307200
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class VirtualStoreError(RuntimeError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class ChunkBackend(Protocol):
    def put(self, key: str, payload: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class Chunk:
    key: str
    size: int
    sha256: str


@dataclass(frozen=True)
class StoredFile:
    chunks: tuple[Chunk, ...]
    size: int
    sha256: str
    modified_at: float
    scope: str = "legacy"


class SelectedSourceStore:
    """Private virtual file tree for one synthetic, isolated backup attempt."""

    def __init__(
        self, backend: ChunkBackend, *, object_chunk_bytes: int = OBJECT_CHUNK_BYTES
    ) -> None:
        if (
            type(object_chunk_bytes) is not int
            or object_chunk_bytes < INPUT_CHUNK_BYTES
            or object_chunk_bytes > OBJECT_CHUNK_BYTES
            or object_chunk_bytes % INPUT_CHUNK_BYTES
        ):
            raise VirtualStoreError("Invalid object chunk size", code="virtual_input")
        self.backend = backend
        self.object_chunk_bytes = object_chunk_bytes
        self.max_buffered_bytes = 0
        self._namespace = uuid.uuid4().hex
        self._directories = {
            PurePosixPath("."): datetime.datetime.now(datetime.UTC).timestamp()
        }
        self._files: dict[PurePosixPath, StoredFile] = {}
        self._references: dict[str, int] = {}
        self._garbage: set[str] = set()
        self._writing: set[PurePosixPath] = set()

    @property
    def pending_cleanup_count(self) -> int:
        return len(self._garbage)

    def _path(self, value: str) -> PurePosixPath:
        try:
            encoded_size = len(value.encode("utf-8")) if isinstance(value, str) else 0
        except UnicodeError:
            encoded_size = MAX_PATH_BYTES + 1
        if (
            not isinstance(value, str)
            or not value
            or encoded_size > MAX_PATH_BYTES
            or value.startswith("/")
            or "\\" in value
            or ":" in value
            or "\x00" in value
        ):
            raise VirtualStoreError("Invalid backup path", code="virtual_path")
        parts = value.split("/")
        if (
            len(parts) > MAX_PATH_PARTS
            or any(not part or part in {".", ".."} for part in parts)
            or any(part.startswith(".amplifai-") or part == "pairing" for part in parts)
        ):
            raise VirtualStoreError("Invalid backup path", code="virtual_path")
        return PurePosixPath(*parts)

    def _parents(self, path: PurePosixPath) -> None:
        current = PurePosixPath(".")
        for part in path.parts[:-1]:
            current /= part
            if current in self._files:
                raise VirtualStoreError(
                    "Backup path overlaps a file", code="virtual_path"
                )
            self._directories.setdefault(
                current, datetime.datetime.now(datetime.UTC).timestamp()
            )

    def _assert_not_writing(self, path: PurePosixPath) -> None:
        if any(
            path == active or path in active.parents or active in path.parents
            for active in self._writing
        ):
            raise VirtualStoreError("Backup destination is busy", code="virtual_busy")

    def create_directory(self, value: str) -> None:
        path = self._path(value)
        self._assert_not_writing(path)
        self._parents(path)
        if path in self._files:
            raise VirtualStoreError("Backup path overlaps a file", code="virtual_path")
        self._directories.setdefault(
            path, datetime.datetime.now(datetime.UTC).timestamp()
        )

    def open_write(
        self, value: str, *, selected: bool, scope: str = "legacy"
    ) -> VirtualWriter:
        path = self._path(value)
        if (
            not isinstance(selected, bool)
            or not isinstance(scope, str)
            or scope
            not in {
                "legacy",
                "discard",
                "control",
                "contacts-main",
                "contacts-wal",
                "contacts-shm",
                "calls-main",
                "calls-wal",
                "calls-shm",
                "messages-main",
                "messages-wal",
                "messages-shm",
            }
        ):
            raise VirtualStoreError("Invalid source selection", code="virtual_input")
        self._assert_not_writing(path)
        if path in self._directories:
            raise VirtualStoreError(
                "Backup destination is a directory", code="virtual_path"
            )
        self._parents(path)
        self._writing.add(path)
        return VirtualWriter(self, path, selected=selected, scope=scope)

    def file_scope(self, value: str) -> str:
        record = self._files.get(self._path(value))
        if record is None:
            raise VirtualStoreError(
                "Backup file is unavailable", code="virtual_missing"
            )
        return record.scope

    def scoped_paths(self, scope: str) -> frozenset[str]:
        return frozenset(
            str(path) for path, record in self._files.items() if record.scope == scope
        )

    def snapshot(self) -> tuple[tuple[str, str, int, str], ...]:
        return tuple(
            sorted(
                (str(path), record.scope, record.size, record.sha256)
                for path, record in self._files.items()
            )
        )

    def _read_chunk(self, chunk: Chunk) -> bytes:
        try:
            payload = self.backend.get(chunk.key)
        except OSError as exc:
            raise VirtualStoreError(
                "Selected-source readback is unavailable", code="virtual_storage"
            ) from exc
        if (
            not isinstance(payload, bytes)
            or len(payload) != chunk.size
            or hashlib.sha256(payload).hexdigest() != chunk.sha256
        ):
            raise VirtualStoreError(
                "Selected-source readback failed integrity verification",
                code="virtual_integrity",
            )
        return payload

    def read_chunks(self, value: str):
        path = self._path(value)
        record = self._files.get(path)
        if record is None:
            raise VirtualStoreError(
                "Backup file is unavailable", code="virtual_missing"
            )
        digest = hashlib.sha256()
        total = 0
        for chunk in record.chunks:
            payload = self._read_chunk(chunk)
            digest.update(payload)
            total += len(payload)
            for offset in range(0, len(payload), INPUT_CHUNK_BYTES):
                yield payload[offset : offset + INPUT_CHUNK_BYTES]
        if total != record.size or digest.hexdigest() != record.sha256:
            raise VirtualStoreError(
                "Selected-source file failed integrity verification",
                code="virtual_integrity",
            )

    def file_size(self, value: str) -> int:
        record = self._files.get(self._path(value))
        if record is None:
            raise VirtualStoreError(
                "Backup file is unavailable", code="virtual_missing"
            )
        return record.size

    def file_chunks(self, value: str) -> tuple[Chunk, ...]:
        record = self._files.get(self._path(value))
        if record is None:
            raise VirtualStoreError(
                "Backup file is unavailable", code="virtual_missing"
            )
        return record.chunks

    def file_sha256(self, value: str) -> str:
        record = self._files.get(self._path(value))
        if record is None:
            raise VirtualStoreError(
                "Backup file is unavailable", code="virtual_missing"
            )
        return record.sha256

    def list_directory(self, value: str) -> dict[str, dict[str, object]]:
        path = self._path(value)
        if path not in self._directories:
            return {}
        result: dict[str, dict[str, object]] = {}
        for child in self._directories:
            if child.parent == path:
                result[child.name] = {
                    "DLFileType": "DLFileTypeDirectory",
                    "DLFileSize": 0,
                    "DLFileModificationDate": datetime.datetime.fromtimestamp(
                        self._directories[child] - APPLE_EPOCH_SECONDS,
                        datetime.UTC,
                    ).replace(tzinfo=None),
                }
        for child, record in self._files.items():
            if child.parent == path:
                result[child.name] = {
                    "DLFileType": "DLFileTypeRegular",
                    "DLFileSize": record.size,
                    "DLFileModificationDate": datetime.datetime.fromtimestamp(
                        record.modified_at - APPLE_EPOCH_SECONDS, datetime.UTC
                    ).replace(tzinfo=None),
                }
        return result

    def _add_references(self, record: StoredFile) -> None:
        for chunk in record.chunks:
            self._references[chunk.key] = self._references.get(chunk.key, 0) + 1

    def _drop_references(self, record: StoredFile) -> None:
        for chunk in record.chunks:
            remaining = self._references[chunk.key] - 1
            if remaining:
                self._references[chunk.key] = remaining
            else:
                del self._references[chunk.key]
                retire = getattr(self.backend, "retire", None)
                if retire is None or not retire(chunk.key):
                    self._garbage.add(chunk.key)

    def _replace(self, path: PurePosixPath, record: StoredFile) -> None:
        previous = self._files.get(path)
        self._add_references(record)
        self._files[path] = record
        if previous is not None:
            self._drop_references(previous)

    def collect_garbage(self) -> None:
        for key in tuple(self._garbage):
            try:
                self.backend.delete(key)
            except OSError as exc:
                raise VirtualStoreError(
                    "Selected-source cleanup is incomplete", code="virtual_cleanup"
                ) from exc
            self._garbage.remove(key)

    def copy_item(self, source_value: str, destination_value: str) -> None:
        source = self._path(source_value)
        destination = self._path(destination_value)
        self._assert_not_writing(source)
        self._assert_not_writing(destination)
        if source not in self._files and source not in self._directories:
            return
        if source in self._files:
            if destination in self._directories:
                destination /= source.name
            if source == destination or destination in self._directories:
                raise VirtualStoreError(
                    "Backup copy overlaps its source", code="virtual_path"
                )
            self._parents(destination)
            record = self._files[source]
            self._replace(
                destination,
                StoredFile(
                    record.chunks,
                    record.size,
                    record.sha256,
                    datetime.datetime.now(datetime.UTC).timestamp(),
                    record.scope,
                ),
            )
        else:
            if (
                source == destination
                or source in destination.parents
                or destination in self._files
                or destination in self._directories
            ):
                raise VirtualStoreError(
                    "Backup copy overlaps its source", code="virtual_path"
                )
            self._parents(destination)
            directories = tuple(
                path
                for path in self._directories
                if path == source or source in path.parents
            )
            files = tuple(
                (path, record)
                for path, record in self._files.items()
                if source in path.parents
            )
            for path in directories:
                self._directories[destination / path.relative_to(source)] = (
                    self._directories[path]
                )
            for path, record in files:
                self._replace(
                    destination / path.relative_to(source),
                    StoredFile(
                        record.chunks,
                        record.size,
                        record.sha256,
                        datetime.datetime.now(datetime.UTC).timestamp(),
                        record.scope,
                    ),
                )
        self.collect_garbage()

    def move_items(self, items: dict[str, str]) -> None:
        for source_value, destination_value in items.items():
            source = self._path(source_value)
            destination = self._path(destination_value)
            self._assert_not_writing(source)
            self._assert_not_writing(destination)
            if source not in self._files and source not in self._directories:
                continue
            if destination in self._directories:
                destination /= source.name
            if (
                source == destination
                or source in destination.parents
                or destination in self._directories
                or (source in self._directories and destination in self._files)
            ):
                raise VirtualStoreError(
                    "Backup move overlaps its source", code="virtual_path"
                )
            self._parents(destination)
            if source in self._files:
                prior = self._files.pop(destination, None)
                self._files[destination] = self._files.pop(source)
                if prior is not None:
                    self._drop_references(prior)
            else:
                directories = tuple(
                    path
                    for path in self._directories
                    if path == source or source in path.parents
                )
                files = tuple(
                    (path, record)
                    for path, record in self._files.items()
                    if source in path.parents
                )
                directory_times = {
                    path: self._directories.pop(path) for path in directories
                }
                for path, _record in files:
                    del self._files[path]
                for path in directories:
                    self._directories[destination / path.relative_to(source)] = (
                        directory_times[path]
                    )
                for path, record in files:
                    self._files[destination / path.relative_to(source)] = record
        self.collect_garbage()

    def remove_items(self, values: list[str]) -> None:
        for value in values:
            path = self._path(value)
            self._assert_not_writing(path)
            removed = tuple(
                child for child in self._files if child == path or path in child.parents
            )
            for child in removed:
                self._drop_references(self._files.pop(child))
            for child in tuple(self._directories):
                if child == path or path in child.parents:
                    del self._directories[child]
        self.collect_garbage()


class VirtualWriter:
    def __init__(
        self,
        store: SelectedSourceStore,
        path: PurePosixPath,
        *,
        selected: bool,
        scope: str,
    ) -> None:
        self.store = store
        self.path = path
        self.selected = selected
        self.scope = scope
        self._buffer = bytearray()
        self._chunks: list[Chunk] = []
        self._digest = hashlib.sha256()
        self._size = 0
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind, _value, _traceback) -> None:
        if kind is None:
            self.finish()
        else:
            self.abort()

    def write(self, payload: bytes) -> None:
        if (
            self._closed
            or not isinstance(payload, bytes)
            or len(payload) > INPUT_CHUNK_BYTES
        ):
            raise VirtualStoreError(
                "Invalid selected-source chunk", code="virtual_input"
            )
        if not self.selected:
            return
        self._size += len(payload)
        self._digest.update(payload)
        offset = 0
        while offset < len(payload):
            count = min(
                self.store.object_chunk_bytes - len(self._buffer),
                len(payload) - offset,
            )
            self._buffer.extend(payload[offset : offset + count])
            offset += count
            self.store.max_buffered_bytes = max(
                self.store.max_buffered_bytes, len(self._buffer)
            )
            if len(self._buffer) == self.store.object_chunk_bytes:
                self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        payload = bytes(self._buffer)
        key = f"{self.store._namespace}/{self.scope}/{uuid.uuid4().hex}"
        digest = hashlib.sha256(payload).hexdigest()
        self.store._garbage.add(key)
        try:
            self.store.backend.put(key, payload)
        except OSError as exc:
            raise VirtualStoreError(
                "Selected-source write is unavailable", code="virtual_storage"
            ) from exc
        if (
            not getattr(self.store.backend, "put_verifies_readback", False)
            and self.store._read_chunk(Chunk(key, len(payload), digest)) != payload
        ):
            raise VirtualStoreError(
                "Selected-source readback failed integrity verification",
                code="virtual_integrity",
            )
        self.store._garbage.remove(key)
        self._chunks.append(Chunk(key, len(payload), digest))
        self._buffer.clear()

    def finish(self) -> None:
        if self._closed:
            raise VirtualStoreError(
                "Selected-source writer is closed", code="virtual_input"
            )
        try:
            self._flush()
        except VirtualStoreError as primary:
            try:
                self.abort()
            except VirtualStoreError as cleanup:
                raise cleanup from primary
            raise
        record = StoredFile(
            tuple(self._chunks),
            self._size if self.selected else 0,
            self._digest.hexdigest() if self.selected else EMPTY_SHA256,
            datetime.datetime.now(datetime.UTC).timestamp(),
            self.scope,
        )
        self.store._replace(self.path, record)
        self._closed = True
        self.store._writing.remove(self.path)
        self.store.collect_garbage()

    def abort(self) -> None:
        if self._closed:
            return
        self.store._garbage.update(chunk.key for chunk in self._chunks)
        self._closed = True
        self.store._writing.remove(self.path)
        self.store.collect_garbage()
