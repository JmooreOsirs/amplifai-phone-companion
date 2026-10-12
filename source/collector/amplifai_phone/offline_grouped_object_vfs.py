"""Offline-only paged index and grouped encrypted Range reader for SQLite.

The producer and read adapter are a synthetic qualification seam, not a cloud
grant issuer. APSW and these objects remain outside the released collector.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager, nullcontext
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .offline_object_vfs import (
    CACHE_BYTES,
    CHUNK_BYTES,
    DIGEST,
    MAX_SQLITE_READ_BYTES,
    OBJECT_ID,
    ObjectVfsError,
    SourceBinding,
    _open_verified_wal_readers,
    chunk_aad,
)

GROUP_CHUNKS = 8
PAGE_CHUNKS = 64
MAX_GROUP_CIPHERTEXT = GROUP_CHUNKS * (CHUNK_BYTES + 16)
MAX_DESCRIPTOR_PAGE = 32 * 1024
MAX_RANGE_ATTEMPTS = 3


@dataclass(frozen=True)
class RangeReply:
    status: int
    content_range: str
    content_length: int
    body: bytes
    grant_expires_at: float | None = None


class RangeBackend(Protocol):
    # A connected adapter must cap streamed bytes before buffering a reply.
    def get_range(
        self, object_id: str, start: int, end: int, max_bytes: int
    ) -> RangeReply: ...


class GroupWriter(Protocol):
    def put(self, object_id: str, ciphertext: bytes) -> None: ...


class ChunkStream(Protocol):
    def read(self, size: int) -> bytes: ...


@dataclass(frozen=True)
class GroupedSourceReceipt:
    binding: SourceBinding
    role: str
    size: int
    sha256: str
    chunk_count: int
    page_count: int
    index_sha256: str
    sidecars: tuple[str, ...]
    mac: str


@dataclass(frozen=True)
class GroupedWalSet:
    main: GroupedSourceReceipt
    wal: GroupedSourceReceipt
    mac: str
    writer_active_at_freeze: bool = False


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _binding(binding: SourceBinding) -> list[str]:
    return [
        binding.owner_id,
        binding.phone_id,
        binding.attempt_id,
        binding.source,
        binding.source_set_id or "",
    ]


def _source_payload(receipt: GroupedSourceReceipt) -> bytes:
    return _canonical(
        [
            "amplifai-grouped-source-v1",
            *_binding(receipt.binding),
            receipt.role,
            receipt.size,
            receipt.sha256,
            receipt.chunk_count,
            receipt.page_count,
            receipt.index_sha256,
            list(receipt.sidecars),
        ]
    )


def _set_payload(source_set: GroupedWalSet) -> bytes:
    return _canonical(
        [
            "amplifai-grouped-wal-set-v1",
            source_set.main.mac,
            source_set.wal.mac,
            source_set.writer_active_at_freeze,
        ]
    )


def _page_mac(
    key: bytes, binding: SourceBinding, role: str, page: int, payload: bytes
) -> str:
    return hmac.new(
        key,
        _canonical(["amplifai-descriptor-page-v1", *_binding(binding), role, page])
        + payload,
        hashlib.sha256,
    ).hexdigest()


def _index_update(digest: object, page: int, payload: bytes, mac: str) -> None:
    digest.update(
        _canonical([page, len(payload), hashlib.sha256(payload).hexdigest(), mac])
    )


def _validate_source(receipt: GroupedSourceReceipt, key: bytes) -> None:
    if (
        not isinstance(receipt, GroupedSourceReceipt)
        or not isinstance(receipt.binding, SourceBinding)
        or receipt.binding.source_set_id is None
        or receipt.role not in ("main", "wal")
        or type(receipt.size) is not int
        or receipt.size < (512 if receipt.role == "main" else 32)
        or not isinstance(receipt.sha256, str)
        or not DIGEST.fullmatch(receipt.sha256)
        or not isinstance(receipt.index_sha256, str)
        or not DIGEST.fullmatch(receipt.index_sha256)
        or type(receipt.chunk_count) is not int
        or type(receipt.page_count) is not int
        or receipt.chunk_count != (receipt.size + CHUNK_BYTES - 1) // CHUNK_BYTES
        or receipt.page_count != (receipt.chunk_count + PAGE_CHUNKS - 1) // PAGE_CHUNKS
        or not isinstance(receipt.sidecars, tuple)
        or (
            receipt.sidecars not in (("wal",), ("wal", "shm"))
            if receipt.role == "main"
            else bool(receipt.sidecars)
        )
        or not isinstance(key, bytes)
        or len(key) != 32
        or not isinstance(receipt.mac, str)
        or not DIGEST.fullmatch(receipt.mac)
        or not hmac.compare_digest(
            receipt.mac,
            hmac.new(key, _source_payload(receipt), hashlib.sha256).hexdigest(),
        )
    ):
        raise ObjectVfsError("vfs_index_receipt_invalid")


def seal_grouped_wal_set(
    main: GroupedSourceReceipt,
    wal: GroupedSourceReceipt,
    receipt_key: bytes,
    *,
    writer_active_at_freeze: bool = False,
) -> GroupedWalSet:
    """The caller must independently freeze one coherent main+WAL instant."""
    _validate_source(main, receipt_key)
    _validate_source(wal, receipt_key)
    if (
        main.role != "main"
        or wal.role != "wal"
        or main.binding != wal.binding
        or not isinstance(writer_active_at_freeze, bool)
    ):
        raise ObjectVfsError("vfs_source_set_invalid")
    unsigned = GroupedWalSet(main, wal, "", writer_active_at_freeze)
    return GroupedWalSet(
        main,
        wal,
        hmac.new(receipt_key, _set_payload(unsigned), hashlib.sha256).hexdigest(),
        writer_active_at_freeze,
    )


def _verify_set(source_set: GroupedWalSet, receipt_key: bytes) -> None:
    if not isinstance(source_set, GroupedWalSet) or not isinstance(
        source_set.writer_active_at_freeze, bool
    ):
        raise ObjectVfsError("vfs_source_set_invalid")
    expected = seal_grouped_wal_set(
        source_set.main,
        source_set.wal,
        receipt_key,
        writer_active_at_freeze=source_set.writer_active_at_freeze,
    )
    if (
        not isinstance(source_set.mac, str)
        or not DIGEST.fullmatch(source_set.mac)
        or not hmac.compare_digest(expected.mac, source_set.mac)
    ):
        raise ObjectVfsError("vfs_source_set_invalid")


def package_grouped_source(
    source_path: Path | ChunkStream,
    index_path: Path,
    binding: SourceBinding,
    role: str,
    writer: GroupWriter,
    key: bytes,
    receipt_key: bytes,
    *,
    sidecars: tuple[str, ...] = (),
    prepare_group: Callable[[str, tuple[tuple[object, ...], ...], int, str], None]
    | None = None,
) -> GroupedSourceReceipt:
    """Fixture producer: bounded group assembly and a durable private index."""
    if (
        binding.source_set_id is None
        or role not in ("main", "wal")
        or not isinstance(key, bytes)
        or len(key) != 32
        or not isinstance(receipt_key, bytes)
        or len(receipt_key) != 32
        or index_path.exists()
        or (
            sidecars not in (("wal",), ("wal", "shm"))
            if role == "main"
            else bool(sidecars)
        )
    ):
        raise ObjectVfsError("vfs_index_producer_invalid")
    descriptor_digest = hashlib.sha256()
    plaintext_digest = hashlib.sha256()
    size = chunk_count = page_count = 0
    page_entries: list[list] = []
    handle, temporary_name = tempfile.mkstemp(
        prefix=".amplifai-index-", dir=index_path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE descriptor_pages (page INTEGER PRIMARY KEY, payload BLOB NOT NULL, mac TEXT NOT NULL)"
            )
            with (
                source_path.open("rb")
                if isinstance(source_path, Path)
                else nullcontext(source_path)
            ) as stream:
                while True:
                    group = bytearray()
                    group_entries: list[list] = []
                    object_id = uuid.uuid4().hex
                    for _ in range(GROUP_CHUNKS):
                        plain = stream.read(CHUNK_BYTES)
                        if not plain:
                            break
                        nonce = os.urandom(12)
                        cipher = AESGCM(key).encrypt(
                            nonce,
                            plain,
                            chunk_aad(binding, role, chunk_count, len(plain)),
                        )
                        group_entries.append(
                            [
                                chunk_count,
                                object_id,
                                0,  # Set after the last part establishes object length.
                                len(group),
                                len(cipher),
                                nonce.hex(),
                                len(plain),
                                hashlib.sha256(plain).hexdigest(),
                                hashlib.sha256(cipher).hexdigest(),
                            ]
                        )
                        group.extend(cipher)
                        plaintext_digest.update(plain)
                        size += len(plain)
                        chunk_count += 1
                    if not group_entries:
                        break
                    for entry in group_entries:
                        entry[2] = len(group)
                    if prepare_group is not None:
                        prepare_group(
                            object_id,
                            tuple(tuple(entry) for entry in group_entries),
                            len(group),
                            hashlib.sha256(group).hexdigest(),
                        )
                    writer.put(object_id, bytes(group))
                    page_entries.extend(group_entries)
                    if len(page_entries) == PAGE_CHUNKS:
                        payload = _canonical(page_entries)
                        if len(payload) > MAX_DESCRIPTOR_PAGE:
                            raise ObjectVfsError("vfs_index_page_oversize")
                        mac = _page_mac(receipt_key, binding, role, page_count, payload)
                        db.execute(
                            "INSERT INTO descriptor_pages VALUES (?, ?, ?)",
                            (page_count, payload, mac),
                        )
                        _index_update(descriptor_digest, page_count, payload, mac)
                        page_count += 1
                        page_entries = []
                if page_entries:
                    payload = _canonical(page_entries)
                    if len(payload) > MAX_DESCRIPTOR_PAGE:
                        raise ObjectVfsError("vfs_index_page_oversize")
                    mac = _page_mac(receipt_key, binding, role, page_count, payload)
                    db.execute(
                        "INSERT INTO descriptor_pages VALUES (?, ?, ?)",
                        (page_count, payload, mac),
                    )
                    _index_update(descriptor_digest, page_count, payload, mac)
                    page_count += 1
        if not chunk_count:
            raise ObjectVfsError("vfs_index_producer_invalid")
        receipt = GroupedSourceReceipt(
            binding,
            role,
            size,
            plaintext_digest.hexdigest(),
            chunk_count,
            page_count,
            descriptor_digest.hexdigest(),
            sidecars,
            "",
        )
        receipt = GroupedSourceReceipt(
            binding,
            role,
            size,
            receipt.sha256,
            chunk_count,
            page_count,
            receipt.index_sha256,
            sidecars,
            hmac.new(receipt_key, _source_payload(receipt), hashlib.sha256).hexdigest(),
        )
        _validate_source(receipt, receipt_key)
        os.link(temporary, index_path)
        directory = os.open(index_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
    finally:
        temporary.unlink(missing_ok=True)


def seal_prepared_grouped_index(
    entries: Iterator[tuple[object, ...]],
    index_path: Path,
    binding: SourceBinding,
    role: str,
    size: int,
    sha256: str,
    receipt_key: bytes,
    *,
    sidecars: tuple[str, ...] = (),
) -> GroupedSourceReceipt:
    """Page already-uploaded, authenticated group descriptors without re-encryption."""
    if (
        index_path.exists()
        or not index_path.parent.is_dir()
        or index_path.parent.is_symlink()
        or not isinstance(binding, SourceBinding)
        or binding.source_set_id is None
        or role not in ("main", "wal")
        or type(size) is not int
        or size <= 0
        or not isinstance(sha256, str)
        or not DIGEST.fullmatch(sha256)
        or not isinstance(receipt_key, bytes)
        or len(receipt_key) != 32
    ):
        raise ObjectVfsError("vfs_index_producer_invalid")
    expected_chunks = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
    descriptor_digest = hashlib.sha256()
    page_entries: list[list] = []
    page_count = chunk_count = 0
    handle, temporary_name = tempfile.mkstemp(
        prefix=".amplifai-index-", dir=index_path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE descriptor_pages (page INTEGER PRIMARY KEY, "
                "payload BLOB NOT NULL, mac TEXT NOT NULL)"
            )
            for supplied in entries:
                entry = list(supplied)
                if (
                    len(entry) != 9
                    or type(entry[0]) is not int
                    or entry[0] != chunk_count
                    or type(entry[6]) is not int
                    or entry[6] != min(CHUNK_BYTES, size - chunk_count * CHUNK_BYTES)
                ):
                    raise ObjectVfsError("vfs_index_integrity")
                page_entries.append(entry)
                chunk_count += 1
                if len(page_entries) == PAGE_CHUNKS:
                    payload = _canonical(page_entries)
                    if len(payload) > MAX_DESCRIPTOR_PAGE:
                        raise ObjectVfsError("vfs_index_page_oversize")
                    mac = _page_mac(receipt_key, binding, role, page_count, payload)
                    db.execute(
                        "INSERT INTO descriptor_pages VALUES (?, ?, ?)",
                        (page_count, payload, mac),
                    )
                    _index_update(descriptor_digest, page_count, payload, mac)
                    page_count += 1
                    page_entries = []
            if page_entries:
                payload = _canonical(page_entries)
                if len(payload) > MAX_DESCRIPTOR_PAGE:
                    raise ObjectVfsError("vfs_index_page_oversize")
                mac = _page_mac(receipt_key, binding, role, page_count, payload)
                db.execute(
                    "INSERT INTO descriptor_pages VALUES (?, ?, ?)",
                    (page_count, payload, mac),
                )
                _index_update(descriptor_digest, page_count, payload, mac)
                page_count += 1
        if chunk_count != expected_chunks:
            raise ObjectVfsError("vfs_index_integrity")
        unsigned = GroupedSourceReceipt(
            binding,
            role,
            size,
            sha256,
            chunk_count,
            page_count,
            descriptor_digest.hexdigest(),
            sidecars,
            "",
        )
        receipt = GroupedSourceReceipt(
            binding,
            role,
            size,
            sha256,
            chunk_count,
            page_count,
            unsigned.index_sha256,
            sidecars,
            hmac.new(
                receipt_key, _source_payload(unsigned), hashlib.sha256
            ).hexdigest(),
        )
        _validate_source(receipt, receipt_key)
        os.link(temporary, index_path)
        directory = os.open(index_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
    finally:
        temporary.unlink(missing_ok=True)


class GroupedRangeReader:
    """Stream-authenticated descriptor pages and exact bounded Range replies."""

    def __init__(
        self,
        source: GroupedSourceReceipt,
        index_path: Path,
        backend: RangeBackend,
        key: bytes,
        receipt_key: bytes,
        *,
        cache_bytes: int = CHUNK_BYTES,
        check_cancel: Callable[[], None] | None = None,
    ) -> None:
        _validate_source(source, receipt_key)
        if not isinstance(key, bytes) or len(key) != 32 or cache_bytes < CHUNK_BYTES:
            raise ObjectVfsError("vfs_cache_invalid")
        self.source = source
        self.backend = backend
        self.cipher = AESGCM(key)
        self.receipt_key = receipt_key
        self.cache_limit = cache_bytes
        self.check_cancel = check_cancel
        self.cache: OrderedDict[int, bytes] = OrderedDict()
        self.cached_bytes = 0
        self.peak_cached_bytes = 0
        self.peak_descriptor_bytes = 0
        self.descriptor_pages_fetched = 0
        self.descriptor_pages_verified = 0
        self.descriptor_index_bytes_verified = 0
        self.objects_fetched = 0
        self.bytes_fetched = 0
        self._page_index = -1
        self._descriptors: list[list] = []
        try:
            self.index = sqlite3.connect(
                f"{index_path.absolute().as_uri()}?mode=ro",
                uri=True,
                isolation_level=None,
            )
            self.index.execute("PRAGMA query_only=ON")
            self.index.execute("PRAGMA cache_size=-256")
            self.index.execute("BEGIN")
            self._verify_index()
        except sqlite3.Error as error:
            if hasattr(self, "index"):
                self.index.close()
            raise ObjectVfsError("vfs_index_integrity") from error
        except Exception:
            if hasattr(self, "index"):
                self.index.close()
            raise

    def _check(self) -> None:
        if self.check_cancel is not None:
            try:
                self.check_cancel()
            except Exception as error:
                raise ObjectVfsError("vfs_cancelled") from error

    def _verify_index(self) -> None:
        digest = hashlib.sha256()
        expected = 0
        try:
            cursor = self.index.execute(
                "SELECT page, payload, mac FROM descriptor_pages ORDER BY page"
            )
            for page, payload, mac in cursor:
                self._check()
                if (
                    page != expected
                    or not isinstance(payload, bytes)
                    or len(payload) > MAX_DESCRIPTOR_PAGE
                    or not isinstance(mac, str)
                    or not hmac.compare_digest(
                        mac,
                        _page_mac(
                            self.receipt_key,
                            self.source.binding,
                            self.source.role,
                            page,
                            payload,
                        ),
                    )
                ):
                    raise ObjectVfsError("vfs_index_integrity")
                _index_update(digest, page, payload, mac)
                self.descriptor_pages_verified += 1
                self.descriptor_index_bytes_verified += len(payload)
                expected += 1
            if (
                expected != self.source.page_count
                or digest.hexdigest() != self.source.index_sha256
            ):
                raise ObjectVfsError("vfs_index_integrity")
        except sqlite3.Error as error:
            raise ObjectVfsError("vfs_index_integrity") from error

    def _page(self, number: int) -> list[list]:
        self._check()
        if number == self._page_index:
            return self._descriptors
        try:
            row = self.index.execute(
                "SELECT payload, mac FROM descriptor_pages WHERE page=?", (number,)
            ).fetchone()
        except sqlite3.Error as error:
            raise ObjectVfsError("vfs_index_integrity") from error
        if row is None or len(row[0]) > MAX_DESCRIPTOR_PAGE:
            raise ObjectVfsError("vfs_index_integrity")
        payload, mac = row
        if not hmac.compare_digest(
            mac,
            _page_mac(
                self.receipt_key,
                self.source.binding,
                self.source.role,
                number,
                payload,
            ),
        ):
            raise ObjectVfsError("vfs_index_integrity")
        try:
            entries = json.loads(payload)
        except (TypeError, ValueError) as error:
            raise ObjectVfsError("vfs_index_integrity") from error
        if not isinstance(entries, list) or len(entries) != min(
            PAGE_CHUNKS, self.source.chunk_count - number * PAGE_CHUNKS
        ):
            raise ObjectVfsError("vfs_index_integrity")
        for offset, entry in enumerate(entries):
            index = number * PAGE_CHUNKS + offset
            if not self._valid_entry(entry, index):
                raise ObjectVfsError("vfs_index_integrity")
        self._page_index = number
        self._descriptors = entries
        self.descriptor_pages_fetched += 1
        self.peak_descriptor_bytes = max(self.peak_descriptor_bytes, len(payload))
        return entries

    def _valid_entry(self, entry: object, index: int) -> bool:
        if not isinstance(entry, list) or len(entry) != 9:
            return False
        (
            i,
            object_id,
            total,
            start,
            cipher_len,
            nonce,
            plain_len,
            plain_sha,
            cipher_sha,
        ) = entry
        return (
            type(i) is int
            and i == index
            and isinstance(object_id, str)
            and bool(OBJECT_ID.fullmatch(object_id))
            and all(
                type(value) is int for value in (total, start, cipher_len, plain_len)
            )
            and 0 <= start < total <= MAX_GROUP_CIPHERTEXT
            and cipher_len == plain_len + 16
            and start + cipher_len <= total
            and plain_len == min(CHUNK_BYTES, self.source.size - index * CHUNK_BYTES)
            and isinstance(nonce, str)
            and len(nonce) == 24
            and all(char in "0123456789abcdef" for char in nonce)
            and isinstance(plain_sha, str)
            and bool(DIGEST.fullmatch(plain_sha))
            and isinstance(cipher_sha, str)
            and bool(DIGEST.fullmatch(cipher_sha))
        )

    def _entry(self, index: int) -> list:
        return self._page(index // PAGE_CHUNKS)[index % PAGE_CHUNKS]

    def _fetch(self, object_id: str, start: int, end: int, total: int) -> bytes:
        expected_length = end - start + 1
        if (
            not OBJECT_ID.fullmatch(object_id)
            or expected_length <= 0
            or expected_length > MAX_GROUP_CIPHERTEXT
            or start < 0
            or end >= total
            or total > MAX_GROUP_CIPHERTEXT
        ):
            raise ObjectVfsError("vfs_range_invalid")
        for attempt in range(MAX_RANGE_ATTEMPTS):
            self._check()
            try:
                grouped_fetch = getattr(self.backend, "get_group_range", None)
                if grouped_fetch is None:
                    reply = self.backend.get_range(
                        object_id, start, end, expected_length
                    )
                else:
                    reply = grouped_fetch(object_id, start, end, total, expected_length)
            except (KeyError, FileNotFoundError) as error:
                raise ObjectVfsError("vfs_object_missing") from error
            except (OSError, TimeoutError) as error:
                if attempt + 1 < MAX_RANGE_ATTEMPTS:
                    continue
                raise ObjectVfsError("vfs_range_unavailable") from error
            self.objects_fetched += 1
            if (
                not isinstance(reply, RangeReply)
                or not isinstance(reply.body, bytes)
                or len(reply.body) > expected_length
            ):
                raise ObjectVfsError("vfs_range_response_invalid")
            if isinstance(reply, RangeReply) and reply.status in (
                429,
                500,
                502,
                503,
                504,
            ):
                if attempt + 1 < MAX_RANGE_ATTEMPTS:
                    continue
                raise ObjectVfsError("vfs_range_unavailable")
            if (
                reply.status != 206
                or reply.content_range != f"bytes {start}-{end}/{total}"
                or type(reply.content_length) is not int
                or reply.content_length != expected_length
            ):
                raise ObjectVfsError("vfs_range_response_invalid")
            self.bytes_fetched += len(reply.body)
            if len(reply.body) != expected_length:
                if attempt + 1 < MAX_RANGE_ATTEMPTS:
                    continue
                raise ObjectVfsError("vfs_range_short")
            self._check()
            return reply.body
        raise ObjectVfsError("vfs_range_unavailable")

    def _decrypt(self, entry: list, ciphertext: bytes) -> bytes:
        index, _, _, _, cipher_len, nonce, plain_len, plain_sha, cipher_sha = entry
        if (
            len(ciphertext) != cipher_len
            or hashlib.sha256(ciphertext).hexdigest() != cipher_sha
        ):
            raise ObjectVfsError("vfs_ciphertext_integrity")
        try:
            plain = self.cipher.decrypt(
                bytes.fromhex(nonce),
                ciphertext,
                chunk_aad(self.source.binding, self.source.role, index, plain_len),
            )
        except Exception as error:
            raise ObjectVfsError("vfs_binding_integrity") from error
        if len(plain) != plain_len or hashlib.sha256(plain).hexdigest() != plain_sha:
            raise ObjectVfsError("vfs_plaintext_integrity")
        return plain

    def _remember(self, index: int, plain: bytes) -> None:
        if index in self.cache:
            return
        while self.cached_bytes + len(plain) > self.cache_limit:
            _, evicted = self.cache.popitem(last=False)
            self.cached_bytes -= len(evicted)
        self.cache[index] = plain
        self.cached_bytes += len(plain)
        self.peak_cached_bytes = max(self.peak_cached_bytes, self.cached_bytes)

    def _chunk(self, index: int) -> bytes:
        self._check()
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        entry = self._entry(index)
        _, object_id, total, start, cipher_len, *_ = entry
        ciphertext = self._fetch(object_id, start, start + cipher_len - 1, total)
        plain = self._decrypt(entry, ciphertext)
        self._remember(index, plain)
        return plain

    def read(self, offset: int, amount: int) -> bytes:
        self._check()
        if (
            type(offset) is not int
            or type(amount) is not int
            or offset < 0
            or amount < 0
            or amount > MAX_SQLITE_READ_BYTES
            or offset + amount > self.source.size
        ):
            raise ObjectVfsError("vfs_range_invalid")
        out = bytearray()
        while amount:
            index, within = divmod(offset, CHUNK_BYTES)
            part = self._chunk(index)
            piece = part[within : within + amount]
            if not piece:
                raise ObjectVfsError("vfs_range_invalid")
            out.extend(piece)
            offset += len(piece)
            amount -= len(piece)
        return bytes(out)

    def verify_complete(self) -> None:
        """Coalesce only the demanded whole-source verification by group."""
        digest = hashlib.sha256()
        for first in range(0, self.source.chunk_count, GROUP_CHUNKS):
            self._check()
            entries = [
                self._entry(index)
                for index in range(
                    first, min(first + GROUP_CHUNKS, self.source.chunk_count)
                )
            ]
            object_id, total = entries[0][1:3]
            if (
                any(entry[1] != object_id or entry[2] != total for entry in entries)
                or entries[0][3] != 0
                or entries[-1][3] + entries[-1][4] != total
                or any(
                    previous[3] + previous[4] != current[3]
                    for previous, current in pairwise(entries)
                )
            ):
                raise ObjectVfsError("vfs_index_integrity")
            group = self._fetch(object_id, 0, total - 1, total)
            for entry in entries:
                plain = self._decrypt(entry, group[entry[3] : entry[3] + entry[4]])
                digest.update(plain)
                self._remember(entry[0], plain)
        if digest.hexdigest() != self.source.sha256:
            raise ObjectVfsError("vfs_source_integrity")

    def close(self) -> None:
        self.cache.clear()
        self.cached_bytes = 0
        self._descriptors.clear()
        self.index.close()


@contextmanager
def open_grouped_wal_source(
    source_set: GroupedWalSet,
    main_index: Path,
    wal_index: Path,
    backend: RangeBackend,
    key: bytes,
    receipt_key: bytes,
    *,
    cache_bytes: int = CACHE_BYTES,
    check_cancel: Callable[[], None] | None = None,
) -> Iterator:
    """Source-only grouped adapter to the already qualified SQLite WAL engine."""
    _verify_set(source_set, receipt_key)
    if not isinstance(cache_bytes, int) or cache_bytes < 2 * CHUNK_BYTES:
        raise ObjectVfsError("vfs_cache_invalid")
    with ExitStack() as cleanup:
        main = GroupedRangeReader(
            source_set.main,
            main_index,
            backend,
            key,
            receipt_key,
            cache_bytes=cache_bytes // 2,
            check_cancel=check_cancel,
        )
        cleanup.callback(main.close)
        wal = GroupedRangeReader(
            source_set.wal,
            wal_index,
            backend,
            key,
            receipt_key,
            cache_bytes=cache_bytes - cache_bytes // 2,
            check_cancel=check_cancel,
        )
        cleanup.callback(wal.close)
        with _open_verified_wal_readers(
            main, wal, writer_active_at_freeze=source_set.writer_active_at_freeze
        ) as opened:
            yield opened
