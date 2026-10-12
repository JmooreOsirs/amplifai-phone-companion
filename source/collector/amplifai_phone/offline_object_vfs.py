"""Offline-only encrypted object VFS experiment; not imported by the collector.

The caller must supply a trusted, immutable source manifest and a local key.
This module makes no network requests, accepts no backup password, and writes
no plaintext source database.  Its APSW dependency is isolated from the
released companion lock until packaging and parser integration qualify.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import struct
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Protocol

import apsw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHUNK_BYTES = 128 * 1024
CACHE_BYTES = 256 * 1024
SQLITE_CACHE_KIB = 2048
MAX_SQLITE_READ_BYTES = 1024 * 1024
DIGEST = re.compile(r"^[0-9a-f]{64}$")
OBJECT_ID = re.compile(r"^[0-9a-f]{32}$")


class ObjectVfsError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ObjectBackend(Protocol):
    # A connected adapter must enforce the limit while streaming from a Range
    # GET; checking a fully buffered response afterward is insufficient.
    def get(self, key: str, max_bytes: int) -> bytes: ...


class ReadableSource(Protocol):
    @property
    def size(self) -> int: ...


class VerifiedRangeReader(Protocol):
    @property
    def source(self) -> ReadableSource: ...

    def read(self, offset: int, amount: int) -> bytes: ...

    def verify_complete(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SourceBinding:
    owner_id: str
    phone_id: str
    attempt_id: str
    source: str
    source_set_id: str | None = None

    def __post_init__(self) -> None:
        try:
            for value in (self.owner_id, self.phone_id, self.attempt_id):
                if str(uuid.UUID(value)) != value.lower():
                    raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ObjectVfsError("vfs_binding_invalid") from error
        if self.source not in {"contacts", "calls", "messages"}:
            raise ObjectVfsError("vfs_binding_invalid")
        if self.source_set_id is not None:
            try:
                if str(uuid.UUID(self.source_set_id)) != self.source_set_id.lower():
                    raise ValueError
            except (ValueError, TypeError, AttributeError) as error:
                raise ObjectVfsError("vfs_binding_invalid") from error


@dataclass(frozen=True)
class EncryptedChunk:
    index: int
    key: str
    nonce: bytes
    plaintext_bytes: int
    plaintext_sha256: str
    ciphertext_sha256: str


@dataclass(frozen=True)
class EncryptedSource:
    binding: SourceBinding
    role: str
    size: int
    sha256: str
    chunks: Sequence[EncryptedChunk]
    sidecars: tuple[str, ...]


@dataclass(frozen=True)
class FrozenWalSet:
    """A trusted freezer's exact, coherent main+WAL capture receipt.

    The MAC authenticates the *declared* pair. Its issuer must first freeze and
    capture both files coherently; matching account/source IDs alone cannot
    establish SQLite snapshot coherence.
    """

    main: EncryptedSource
    wal: EncryptedSource
    receipt_mac: str
    writer_active_at_freeze: bool = False


@dataclass(frozen=True)
class WalAudit:
    valid_frames: int
    committed_frames: int
    stale_tail_frames: int
    incomplete_uncommitted_frames: int
    page_size: int


def chunk_aad(binding: SourceBinding, role: str, index: int, size: int) -> bytes:
    """Bind each sealed part to a specific owner, phone, attempt and source."""
    return json.dumps(
        [
            "amplifai-object-vfs-v2"
            if binding.source_set_id
            else "amplifai-object-vfs-v1",
            binding.owner_id,
            binding.phone_id,
            binding.attempt_id,
            binding.source,
            *([binding.source_set_id] if binding.source_set_id else []),
            role,
            index,
            size,
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _wal_receipt_payload(
    main: EncryptedSource, wal: EncryptedSource, writer_active_at_freeze: bool
) -> bytes:
    def descriptor(source: EncryptedSource) -> list:
        return [
            source.role,
            source.size,
            source.sha256,
            list(source.sidecars),
            [
                [
                    part.index,
                    part.key,
                    part.nonce.hex(),
                    part.plaintext_bytes,
                    part.plaintext_sha256,
                    part.ciphertext_sha256,
                ]
                for part in source.chunks
            ],
        ]

    binding = main.binding
    return json.dumps(
        [
            "amplifai-frozen-wal-set-v1",
            binding.owner_id,
            binding.phone_id,
            binding.attempt_id,
            binding.source,
            binding.source_set_id,
            writer_active_at_freeze,
            descriptor(main),
            descriptor(wal),
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def seal_frozen_wal_set(
    main: EncryptedSource,
    wal: EncryptedSource,
    receipt_key: bytes,
    *,
    writer_active_at_freeze: bool = False,
) -> FrozenWalSet:
    """Only a trusted coherent-snapshot freezer may issue this receipt."""
    _validate_wal_members(main, wal)
    if not isinstance(receipt_key, bytes) or len(receipt_key) != 32:
        raise ObjectVfsError("vfs_receipt_key_invalid")
    if not isinstance(writer_active_at_freeze, bool):
        raise ObjectVfsError("vfs_source_set_invalid")
    mac = hmac.new(
        receipt_key,
        _wal_receipt_payload(main, wal, writer_active_at_freeze),
        hashlib.sha256,
    )
    return FrozenWalSet(main, wal, mac.hexdigest(), writer_active_at_freeze)


def _validate_wal_members(main: EncryptedSource, wal: EncryptedSource) -> None:
    if (
        not isinstance(main, EncryptedSource)
        or not isinstance(wal, EncryptedSource)
        or not isinstance(main.binding, SourceBinding)
        or not isinstance(wal.binding, SourceBinding)
        or main.binding != wal.binding
        or main.binding.source_set_id is None
        or main.role != "main"
        or wal.role != "wal"
        or main.sidecars not in (("wal",), ("wal", "shm"))
        or wal.sidecars
        or len({part.nonce for part in (*main.chunks, *wal.chunks)})
        != len(main.chunks) + len(wal.chunks)
    ):
        raise ObjectVfsError("vfs_source_set_invalid")


def _verify_wal_receipt(source_set: FrozenWalSet, receipt_key: bytes) -> None:
    if not isinstance(source_set, FrozenWalSet):
        raise ObjectVfsError("vfs_source_set_invalid")
    _validate_wal_members(source_set.main, source_set.wal)
    if not isinstance(receipt_key, bytes) or len(receipt_key) != 32:
        raise ObjectVfsError("vfs_receipt_key_invalid")
    if (
        not isinstance(source_set.receipt_mac, str)
        or not DIGEST.fullmatch(source_set.receipt_mac)
        or not isinstance(source_set.writer_active_at_freeze, bool)
    ):
        raise ObjectVfsError("vfs_receipt_invalid")
    expected = hmac.new(
        receipt_key,
        _wal_receipt_payload(
            source_set.main, source_set.wal, source_set.writer_active_at_freeze
        ),
        hashlib.sha256,
    )
    if not hmac.compare_digest(expected.hexdigest(), source_set.receipt_mac):
        raise ObjectVfsError("vfs_receipt_invalid")


class EncryptedRangeReader:
    """Verify/decrypt one range at a time into a fixed-size private LRU."""

    def __init__(
        self,
        source: EncryptedSource,
        backend: ObjectBackend,
        key: bytes,
        *,
        cache_bytes: int = CACHE_BYTES,
        check_cancel: Callable[[], None] | None = None,
        _frozen_wal_member: bool = False,
    ) -> None:
        if _frozen_wal_member:
            allowed = (
                isinstance(source.binding, SourceBinding)
                and source.binding.source_set_id is not None
                and (
                    (
                        source.role == "main"
                        and source.sidecars in (("wal",), ("wal", "shm"))
                    )
                    or (source.role == "wal" and not source.sidecars)
                )
            )
        else:
            allowed = source.role == "main" and not source.sidecars
        if not allowed:
            raise ObjectVfsError("vfs_sidecar_unqualified")
        if not isinstance(key, bytes) or len(key) != 32:
            raise ObjectVfsError("vfs_key_invalid")
        if not isinstance(cache_bytes, int) or cache_bytes < CHUNK_BYTES:
            raise ObjectVfsError("vfs_cache_invalid")
        if (
            not isinstance(source.binding, SourceBinding)
            or source.size < (32 if source.role == "wal" else 512)
            or not DIGEST.fullmatch(source.sha256)
            or not source.chunks
        ):
            raise ObjectVfsError("vfs_manifest_invalid")
        if (
            any(
                part.index != index
                or not OBJECT_ID.fullmatch(part.key)
                or len(part.nonce) != 12
                or part.plaintext_bytes < 1
                or part.plaintext_bytes > CHUNK_BYTES
                or (
                    index < len(source.chunks) - 1
                    and part.plaintext_bytes != CHUNK_BYTES
                )
                or not DIGEST.fullmatch(part.plaintext_sha256)
                or not DIGEST.fullmatch(part.ciphertext_sha256)
                for index, part in enumerate(source.chunks)
            )
            or sum(part.plaintext_bytes for part in source.chunks) != source.size
            or len({part.nonce for part in source.chunks}) != len(source.chunks)
        ):
            raise ObjectVfsError("vfs_manifest_invalid")
        self.source = source
        self.backend = backend
        self.cipher = AESGCM(key)
        self.cache_limit = cache_bytes
        self.check_cancel = check_cancel
        self.cache: OrderedDict[int, bytes] = OrderedDict()
        self.cached_bytes = 0
        self.peak_cached_bytes = 0
        self.objects_fetched = 0
        self.last_error: str | None = None

    def _check(self) -> None:
        if self.check_cancel is not None:
            try:
                self.check_cancel()
            except Exception as error:
                self.last_error = "vfs_cancelled"
                raise ObjectVfsError("vfs_cancelled") from error

    def _chunk(self, index: int) -> bytes:
        self._check()
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        part = self.source.chunks[index]
        try:
            ciphertext = self.backend.get(part.key, part.plaintext_bytes + 16)
        except ValueError as error:
            self.last_error = "vfs_ciphertext_integrity"
            raise ObjectVfsError("vfs_ciphertext_integrity") from error
        except (OSError, KeyError) as error:
            self.last_error = "vfs_object_missing"
            raise ObjectVfsError("vfs_object_missing") from error
        self.objects_fetched += 1
        if (
            not isinstance(ciphertext, bytes)
            or len(ciphertext) != part.plaintext_bytes + 16
            or hashlib.sha256(ciphertext).hexdigest() != part.ciphertext_sha256
        ):
            self.last_error = "vfs_ciphertext_integrity"
            raise ObjectVfsError("vfs_ciphertext_integrity")
        try:
            plaintext = self.cipher.decrypt(
                part.nonce,
                ciphertext,
                chunk_aad(
                    self.source.binding, self.source.role, index, part.plaintext_bytes
                ),
            )
        except Exception as error:
            self.last_error = "vfs_binding_integrity"
            raise ObjectVfsError("vfs_binding_integrity") from error
        if hashlib.sha256(plaintext).hexdigest() != part.plaintext_sha256:
            self.last_error = "vfs_plaintext_integrity"
            raise ObjectVfsError("vfs_plaintext_integrity")
        self._check()
        while self.cached_bytes + len(plaintext) > self.cache_limit:
            _, evicted = self.cache.popitem(last=False)
            self.cached_bytes -= len(evicted)
        self.cache[index] = plaintext
        self.cached_bytes += len(plaintext)
        self.peak_cached_bytes = max(self.peak_cached_bytes, self.cached_bytes)
        return plaintext

    def read(self, offset: int, amount: int) -> bytes:
        self._check()
        if (
            not isinstance(offset, int)
            or not isinstance(amount, int)
            or offset < 0
            or amount < 0
            or amount > MAX_SQLITE_READ_BYTES
            or offset + amount > self.source.size
        ):
            self.last_error = "vfs_range_invalid"
            raise ObjectVfsError("vfs_range_invalid")
        out = bytearray()
        while amount:
            index, within = divmod(offset, CHUNK_BYTES)
            part = self._chunk(index)
            piece = part[within : within + amount]
            if not piece:
                self.last_error = "vfs_range_invalid"
                raise ObjectVfsError("vfs_range_invalid")
            out.extend(piece)
            offset += len(piece)
            amount -= len(piece)
        return bytes(out)

    def verify_complete(self) -> None:
        """Authenticate every source byte before exposing a SQLite connection."""
        digest = hashlib.sha256()
        for index in range(len(self.source.chunks)):
            digest.update(self._chunk(index))
        if digest.hexdigest() != self.source.sha256:
            self.last_error = "vfs_source_integrity"
            raise ObjectVfsError("vfs_source_integrity")

    def close(self) -> None:
        self.cache.clear()
        self.cached_bytes = 0


def _wal_checksum(
    data: bytes, seed: tuple[int, int], byteorder: str
) -> tuple[int, int]:
    words = struct.Struct("<II" if byteorder == "little" else ">II")
    if len(data) % 8:
        raise ObjectVfsError("vfs_wal_format_invalid")
    first, second = seed
    for value0, value1 in words.iter_unpack(data):
        first = (first + value0 + second) & 0xFFFFFFFF
        second = (second + value1 + first) & 0xFFFFFFFF
    return first, second


def _audit_wal(
    main: VerifiedRangeReader,
    wal: VerifiedRangeReader,
    *,
    writer_active_at_freeze: bool,
) -> WalAudit:
    """Reject a damaged current WAL tail; SQLite still owns page recovery.

    SQLite intentionally treats a bad checksum as the end of a WAL. For a
    frozen source that could silently hide committed later frames, so this
    bounded pass checks frame integrity only. It does not apply WAL pages.
    """
    main_header = main.read(0, 20)
    if main_header[:16] != b"SQLite format 3\x00" or main_header[18:20] != b"\x02\x02":
        raise ObjectVfsError("vfs_wal_main_incompatible")
    main_page_size = int.from_bytes(main_header[16:18], "big")
    if main_page_size == 1:
        main_page_size = 65536
    header = wal.read(0, 32)
    magic = int.from_bytes(header[:4], "big")
    page_size = int.from_bytes(header[8:12], "big")
    if (
        magic not in (0x377F0682, 0x377F0683)
        or int.from_bytes(header[4:8], "big") != 3007000
        or page_size != main_page_size
        or page_size < 512
        or page_size > 65536
        or page_size & (page_size - 1)
        or wal.source.size < 32 + 24 + page_size
        or (wal.source.size - 32) % (24 + page_size)
    ):
        raise ObjectVfsError("vfs_wal_format_invalid")
    byteorder = "little" if magic == 0x377F0682 else "big"
    checksum = _wal_checksum(header[:24], (0, 0), byteorder)
    if checksum != struct.unpack(">II", header[24:32]):
        raise ObjectVfsError("vfs_wal_checksum_invalid")
    frame_bytes = page_size + 24
    frame_count = (wal.source.size - 32) // frame_bytes
    valid = committed = stale = incomplete = 0
    stale_tail = False
    incomplete_tail = False
    for index in range(frame_count):
        offset = 32 + index * frame_bytes
        frame_header = wal.read(offset, 24)
        if frame_header[8:16] != header[16:24]:
            if incomplete_tail:
                if int.from_bytes(frame_header[4:8], "big"):
                    raise ObjectVfsError("vfs_wal_checksum_invalid")
                incomplete += 1
                continue
            if (
                writer_active_at_freeze
                and frame_header[8:16] == b"\x00" * 8
                and not int.from_bytes(frame_header[4:8], "big")
            ):
                # SQLite may spill pages from an uncommitted transaction with
                # zeroed salts/checksums; only a trusted active-writer receipt
                # permits this explicitly unfinished suffix.
                incomplete_tail = True
                incomplete += 1
                continue
            stale_tail = True
            stale += 1
            continue
        if stale_tail or int.from_bytes(frame_header[:4], "big") == 0:
            raise ObjectVfsError("vfs_wal_format_invalid")
        commit_marker = int.from_bytes(frame_header[4:8], "big")
        if incomplete_tail:
            if commit_marker:
                raise ObjectVfsError("vfs_wal_checksum_invalid")
            incomplete += 1
            continue
        checksum = _wal_checksum(frame_header[:8], checksum, byteorder)
        checksum = _wal_checksum(wal.read(offset + 24, page_size), checksum, byteorder)
        if checksum != struct.unpack(">II", frame_header[16:24]):
            if not writer_active_at_freeze or commit_marker:
                raise ObjectVfsError("vfs_wal_checksum_invalid")
            incomplete_tail = True
            incomplete += 1
            continue
        valid += 1
        if commit_marker:
            committed += 1
    return WalAudit(valid, committed, stale, incomplete, page_size)


class _ReadOnlyFile:
    def __init__(
        self, reader: VerifiedRangeReader, *, immutable: bool, vfs: _ObjectVFS
    ):
        self.reader = reader
        self.immutable = immutable
        self.vfs = vfs

    def xRead(self, amount: int, offset: int) -> bytes:
        return self.reader.read(offset, amount)

    def xFileSize(self) -> int:
        return self.reader.source.size

    def xClose(self) -> None:
        pass

    def xLock(self, level: int) -> None:
        pass

    def xUnlock(self, level: int) -> None:
        pass

    def xCheckReservedLock(self) -> bool:
        return False

    def xFileControl(self, op: int, ptr: int) -> bool:
        return False

    def xSectorSize(self) -> int:
        return 4096

    def xDeviceCharacteristics(self) -> int:
        return apsw.SQLITE_IOCAP_IMMUTABLE if self.immutable else 0

    def xWrite(self, data: bytes, offset: int) -> None:
        self.vfs.mutation_attempts += 1
        raise apsw.ReadOnlyError("The selected source is read-only")

    def xTruncate(self, size: int) -> None:
        self.vfs.mutation_attempts += 1
        raise apsw.ReadOnlyError("The selected source is read-only")

    def xSync(self, flags: int) -> None:
        self.vfs.mutation_attempts += 1
        raise apsw.ReadOnlyError("The selected source is read-only")


class _ObjectVFS(apsw.VFS):
    def __init__(
        self,
        reader: VerifiedRangeReader,
        wal_reader: VerifiedRangeReader | None = None,
    ):
        self.reader = reader
        self.wal_reader = wal_reader
        self.filename = f"/amplifai-private/{uuid.uuid4()}/source.sqlite3"
        self.name = f"amplifai-object-{uuid.uuid4()}"
        self.wal_opened = False
        self.mutation_attempts = 0
        self.wal_audit: WalAudit | None = None
        super().__init__(self.name, "")

    def xFullPathname(self, pathname: str) -> str:
        if pathname != self.filename:
            raise apsw.CantOpenError("Only the bound source is available")
        return pathname

    def xOpen(
        self, name: str | apsw.URIFilename | None, flags: list[int]
    ) -> _ReadOnlyFile:
        filename = name.filename() if isinstance(name, apsw.URIFilename) else name
        if filename == self.filename and flags[0] & apsw.SQLITE_OPEN_MAIN_DB:
            if not flags[0] & apsw.SQLITE_OPEN_READONLY or flags[0] & (
                apsw.SQLITE_OPEN_READWRITE | apsw.SQLITE_OPEN_CREATE
            ):
                raise apsw.CantOpenError("The main source must be read-only")
            flags[1] = apsw.SQLITE_OPEN_MAIN_DB | apsw.SQLITE_OPEN_READONLY
            return _ReadOnlyFile(
                self.reader, immutable=self.wal_reader is None, vfs=self
            )
        if (
            filename == self.filename + "-wal"
            and self.wal_reader is not None
            and flags[0] & apsw.SQLITE_OPEN_WAL
        ):
            # SQLite requests READWRITE|CREATE for a WAL even on a read-only
            # connection. Report the actual read-only handle so closing the
            # last connection does not attempt a checkpoint into the source.
            flags[1] = apsw.SQLITE_OPEN_WAL | apsw.SQLITE_OPEN_READONLY
            self.wal_opened = True
            return _ReadOnlyFile(self.wal_reader, immutable=False, vfs=self)
        raise apsw.CantOpenError("Only the bound read-only source set is available")

    def xAccess(self, pathname: str, flags: int) -> bool:
        return flags != apsw.SQLITE_ACCESS_READWRITE and (
            pathname == self.filename
            or (self.wal_reader is not None and pathname == self.filename + "-wal")
        )

    def xDelete(self, pathname: str, syncdir: bool) -> None:
        self.mutation_attempts += 1
        raise apsw.ReadOnlyError("The selected source set is read-only")


class _Row:
    def __init__(self, names: tuple[str, ...], values: tuple):
        self.names = {name.casefold(): index for index, name in enumerate(names)}
        self.values = values

    def __getitem__(self, key: int | str):
        return (
            self.values[self.names[key.casefold()]]
            if isinstance(key, str)
            else self.values[key]
        )

    def __iter__(self) -> Iterator:
        return iter(self.values)


class _Cursor:
    def __init__(self, cursor: apsw.Cursor):
        self.cursor = cursor
        try:
            self.names = tuple(item[0] for item in cursor.getdescription())
        except apsw.ExecutionCompleteError:
            # APSW releases an empty result before its description is read.
            # The parser cannot index a row that does not exist.
            self.names = ()

    def __iter__(self) -> Iterator[_Row]:
        for values in self.cursor:
            yield _Row(self.names, values)

    def fetchone(self) -> _Row | None:
        values = self.cursor.fetchone()
        return None if values is None else _Row(self.names, values)


class _Connection:
    def __init__(self, connection: apsw.Connection, vfs: _ObjectVFS):
        self.connection = connection
        self.vfs = vfs

    def execute(self, statement: str, parameters: tuple = ()) -> _Cursor:
        return _Cursor(self.connection.cursor().execute(statement, parameters))


@contextmanager
def open_object_source(reader: EncryptedRangeReader) -> Iterator[_Connection]:
    """Present unchanged metadata SELECTs through a read-only APSW adapter."""
    with ExitStack() as cleanup:
        cleanup.callback(reader.close)
        reader.verify_complete()
        vfs = _ObjectVFS(reader)
        cleanup.callback(vfs.unregister)
        connection = apsw.Connection(
            f"file:{vfs.filename}?mode=ro&immutable=1",
            flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI,
            vfs=vfs.name,
        )
        cleanup.callback(connection.close)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA mmap_size=0")
        connection.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
        yield _Connection(connection, vfs)


@contextmanager
def open_frozen_wal_source(
    source_set: FrozenWalSet,
    backend: ObjectBackend,
    key: bytes,
    receipt_key: bytes,
    *,
    cache_bytes: int = CACHE_BYTES,
    check_cancel: Callable[[], None] | None = None,
) -> Iterator[_Connection]:
    """Read one authenticated frozen main+WAL pair with SQLite's WAL engine.

    This is source-only. The receipt issuer must freeze a coherent pair under
    its own trusted protocol; this function verifies the authenticated pair
    but cannot make two asynchronously captured files coherent.
    """
    _verify_wal_receipt(source_set, receipt_key)
    if not isinstance(cache_bytes, int) or cache_bytes < 2 * CHUNK_BYTES:
        raise ObjectVfsError("vfs_cache_invalid")
    with ExitStack() as cleanup:
        main_reader = EncryptedRangeReader(
            source_set.main,
            backend,
            key,
            cache_bytes=cache_bytes // 2,
            check_cancel=check_cancel,
            _frozen_wal_member=True,
        )
        cleanup.callback(main_reader.close)
        wal_reader = EncryptedRangeReader(
            source_set.wal,
            backend,
            key,
            cache_bytes=cache_bytes - cache_bytes // 2,
            check_cancel=check_cancel,
            _frozen_wal_member=True,
        )
        cleanup.callback(wal_reader.close)
        with _open_verified_wal_readers(
            main_reader,
            wal_reader,
            writer_active_at_freeze=source_set.writer_active_at_freeze,
        ) as opened:
            yield opened


@contextmanager
def _open_verified_wal_readers(
    main_reader: VerifiedRangeReader,
    wal_reader: VerifiedRangeReader,
    *,
    writer_active_at_freeze: bool,
) -> Iterator[_Connection]:
    """Run SQLite's own WAL recovery for independently verified range readers."""
    with ExitStack() as cleanup:
        main_reader.verify_complete()
        wal_reader.verify_complete()
        audit = _audit_wal(
            main_reader,
            wal_reader,
            writer_active_at_freeze=writer_active_at_freeze,
        )
        vfs = _ObjectVFS(main_reader, wal_reader)
        vfs.wal_audit = audit
        cleanup.callback(vfs.unregister)
        connection = apsw.Connection(
            f"file:{vfs.filename}?mode=ro",
            flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI,
            vfs=vfs.name,
        )
        cleanup.callback(connection.close)
        # Must be the first SQL before SQLite's first WAL-mode access.
        if (
            connection.execute("PRAGMA locking_mode=EXCLUSIVE").fetchone()[0]
            != "exclusive"
        ):
            raise ObjectVfsError("vfs_wal_exclusive_unavailable")
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            raise ObjectVfsError("vfs_wal_incompatible")
        if not vfs.wal_opened:
            raise ObjectVfsError("vfs_wal_missing")
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA mmap_size=0")
        connection.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
        yield _Connection(connection, vfs)
