"""Offline Apple-encrypted backup reader over authenticated grouped objects.

This source-only adapter is deliberately absent from the released collector.
The grouped objects contain original Apple ciphertext, never decrypted SQLite.
"""

from __future__ import annotations

import hashlib
import logging
import os
import plistlib
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Self

from bpylist2 import archiver
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap
from pyiosbackup.entry import Entry
from pyiosbackup.keybag import Keybag, encryption_key_struct
from pyiosbackup.manifest_dbs.sqlite3 import MBFile
from pyiosbackup.manifest_plist import ManifestPlist

from .backup_files import copy_backup_file
from .ios_backup import DATABASES, HOME_DOMAIN
from .offline_cloud_capture import (
    MAX_ARCHIVED_ENTRY_BYTES,
    MAX_PLIST_BYTES,
    CaptureAdmission,
)
from .offline_grouped_object_vfs import (
    GroupedRangeReader,
    GroupedSourceReceipt,
    GroupedWalSet,
    RangeBackend,
    _verify_set,
)
from .offline_object_vfs import (
    CACHE_BYTES,
    CHUNK_BYTES,
    DIGEST,
    MAX_SQLITE_READ_BYTES,
    ObjectVfsError,
    SourceBinding,
    _open_verified_wal_readers,
)
from .workspace import WorkspaceError

AES_BLOCK = 16
CBC_READ_BYTES = 128 * 1024
FILE_ID = set("0123456789abcdef")


def _stat_token(path: Path) -> tuple[int, int, int, int, int]:
    observed = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(observed.st_mode):
        raise ObjectVfsError("apple_control_invalid")
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


class LocalAppleEntry:
    """Local-only selected manifest claim; wrapped file key is never serialized."""

    __slots__ = (
        "binding",
        "ciphertext_sha256",
        "file_id",
        "manifest_sha256",
        "plaintext_size",
        "role",
        "wrapped_file_key",
    )

    def __init__(
        self,
        binding: SourceBinding,
        role: str,
        file_id: str,
        plaintext_size: int,
        ciphertext_sha256: str,
        manifest_sha256: str,
        wrapped_file_key: bytes,
    ) -> None:
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "file_id", file_id)
        object.__setattr__(self, "plaintext_size", plaintext_size)
        object.__setattr__(self, "ciphertext_sha256", ciphertext_sha256)
        object.__setattr__(self, "manifest_sha256", manifest_sha256)
        object.__setattr__(self, "wrapped_file_key", wrapped_file_key)

    def __setattr__(self, _name, _value) -> None:
        raise AttributeError("Local Apple manifest claims are immutable")

    @property
    def size(self) -> int:
        return self.plaintext_size

    def __repr__(self) -> str:
        return "LocalAppleEntry(<local key material redacted>)"

    def __getstate__(self):
        raise TypeError("Local Apple entry keys cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("Local Apple entry keys cannot be serialized")

    @classmethod
    def from_pinned_entry(
        cls,
        entry: Entry,
        source: GroupedSourceReceipt,
        manifest_sha256: str,
    ) -> LocalAppleEntry:
        binding = source.binding
        suffix = "" if source.role == "main" else "-wal"
        if (
            not isinstance(entry, Entry)
            or binding.source not in DATABASES
            or source.role not in {"main", "wal"}
            or entry.domain != HOME_DOMAIN
            or entry.relative_path != DATABASES[binding.source] + suffix
            or not entry.is_file()
            or type(entry.size) is not int
            or entry.size < (512 if source.role == "main" else 32)
            or not isinstance(entry.encryption_key, bytes)
            or len(entry.encryption_key) < 12
            or not isinstance(manifest_sha256, str)
            or not DIGEST.fullmatch(manifest_sha256)
        ):
            raise ObjectVfsError("apple_manifest_entry_invalid")
        expected_id = hashlib.sha1(
            (HOME_DOMAIN + "-" + entry.relative_path).encode("utf-8")
        ).hexdigest()
        if (
            entry.file_id != expected_id
            or len(entry.file_id) != 40
            or not set(entry.file_id) <= FILE_ID
        ):
            raise ObjectVfsError("apple_manifest_entry_invalid")
        expected_cipher_size = (entry.size // AES_BLOCK + 1) * AES_BLOCK
        if source.size != expected_cipher_size:
            raise ObjectVfsError("apple_ciphertext_length")
        return cls(
            binding,
            source.role,
            entry.file_id,
            entry.size,
            source.sha256,
            manifest_sha256,
            entry.encryption_key,
        )


class LocalKeyAuthority:
    """Short-lived local password/keybag scope with current-attempt checks."""

    __slots__ = (
        "_assert_current",
        "_closed",
        "_control_paths",
        "_control_temp",
        "_control_tokens",
        "_keybag",
        "admission",
        "control_copy_path",
        "manifest_sha256",
    )

    def __init__(
        self,
        admission: CaptureAdmission,
        manifest_plist_path: Path,
        local_manifest_path: Path,
        password: str,
        assert_current: Callable[[CaptureAdmission, str | None], None],
    ) -> None:
        if not isinstance(admission, CaptureAdmission) or not callable(assert_current):
            raise ObjectVfsError("apple_authority_invalid")
        try:
            assert_current(admission, None)
        except Exception as error:
            raise ObjectVfsError("apple_authority_stale") from error
        # Pinned pyiosbackup logs derived key bytes at DEBUG; never permit that.
        logging.getLogger("pyiosbackup").setLevel(logging.WARNING)
        try:
            manifest_paths = (manifest_plist_path, local_manifest_path)
            tokens = tuple(_stat_token(path) for path in manifest_paths)
            if tokens[0][2] > MAX_PLIST_BYTES:
                raise ObjectVfsError("apple_control_invalid")
            handle = os.open(
                manifest_plist_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(handle, "rb") as control:
                manifest = ManifestPlist(plistlib.loads(control.read()))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ObjectVfsError("apple_control_invalid") from error
        try:
            encrypted = manifest.is_encrypted
        except (KeyError, TypeError) as error:
            raise ObjectVfsError("apple_control_invalid") from error
        if encrypted is not True or not password:
            raise ObjectVfsError("apple_authority_invalid")
        if any(
            Path(str(local_manifest_path) + suffix).exists()
            for suffix in ("-wal", "-shm", "-journal")
        ):
            raise ObjectVfsError("apple_control_sidecar_unqualified")
        digest = hashlib.sha256()
        try:
            handle = os.open(
                local_manifest_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(handle, "rb") as stream:
                while chunk := stream.read(CBC_READ_BYTES):
                    assert_current(admission, None)
                    digest.update(chunk)
        except OSError as error:
            raise ObjectVfsError("apple_control_unavailable") from error
        except Exception as error:
            raise ObjectVfsError("apple_authority_stale") from error
        try:
            keybag = Keybag.from_manifest(manifest, password)
            manifest_key = manifest.manifest_key
        except Exception as error:
            raise ObjectVfsError("apple_key_invalid") from error
        try:
            control_temp = tempfile.TemporaryDirectory(
                prefix="apple-local-manifest-", dir=local_manifest_path.parent
            )
        except OSError as error:
            raise ObjectVfsError("apple_control_unavailable") from error
        control_copy_path = Path(control_temp.name) / "Manifest.db"

        def ensure_current() -> None:
            try:
                assert_current(admission, None)
            except Exception as error:
                raise ObjectVfsError("apple_authority_stale") from error

        try:
            copy_backup_file(
                local_manifest_path,
                control_copy_path,
                keybag=keybag,
                encryption_key=manifest_key,
                padded=False,
                bound_callback=ensure_current,
            )
            with closing(
                sqlite3.connect(f"{control_copy_path.as_uri()}?mode=ro", uri=True)
            ) as control:
                control.execute("PRAGMA query_only=ON")
                control.execute("SELECT fileID FROM Files LIMIT 1").fetchone()
            copy_token = _stat_token(control_copy_path)
        except Exception as error:
            control_temp.cleanup()
            if isinstance(error, ObjectVfsError):
                raise
            if isinstance(error, WorkspaceError):
                raise ObjectVfsError("apple_control_storage") from error
            raise ObjectVfsError("apple_control_invalid") from error
        self.admission = admission
        self.manifest_sha256 = digest.hexdigest()
        self._keybag = keybag
        self._control_temp = control_temp
        self._control_paths = (*manifest_paths, control_copy_path)
        self._control_tokens = (*tokens, copy_token)
        self.control_copy_path = control_copy_path
        self._assert_current = assert_current
        self._closed = False

    def __repr__(self) -> str:
        return "LocalKeyAuthority(<local key material redacted>)"

    def __getstate__(self):
        raise TypeError("Local Apple key authority cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("Local Apple key authority cannot be serialized")

    def __enter__(self) -> Self:
        self._check(None)
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._keybag = None
            self._control_temp.cleanup()

    def _check(self, source: str | None) -> None:
        if self._closed:
            raise ObjectVfsError("apple_authority_closed")
        try:
            if (
                tuple(_stat_token(path) for path in self._control_paths)
                != self._control_tokens
            ):
                raise ObjectVfsError("apple_control_changed")
        except OSError as error:
            raise ObjectVfsError("apple_control_changed") from error
        try:
            self._assert_current(self.admission, source)
        except Exception as error:
            raise ObjectVfsError("apple_authority_stale") from error

    def unwrap(self, entry: LocalAppleEntry) -> bytes:
        self._check(entry.binding.source)
        if (
            entry.manifest_sha256 != self.manifest_sha256
            or entry.binding.owner_id != self.admission.owner_id
            or entry.binding.phone_id != self.admission.phone_id
            or entry.binding.attempt_id != self.admission.attempt_id
            or entry.binding.source not in self.admission.categories
        ):
            raise ObjectVfsError("apple_binding_invalid")
        try:
            parsed = encryption_key_struct.parse(entry.wrapped_file_key)
            key = aes_key_unwrap(self._keybag.get_key(parsed.class_), parsed.key)
            if len(key) not in {16, 24, 32}:
                raise ValueError
            return key
        except Exception as error:
            raise ObjectVfsError("apple_key_invalid") from error

    def project_entry(self, source: GroupedSourceReceipt) -> LocalAppleEntry:
        """Select one current MBFile row from the private decrypted control copy."""
        self._check(source.binding.source)
        category, role = source.binding.source, source.role
        if category not in DATABASES or role not in {"main", "wal"}:
            raise ObjectVfsError("apple_manifest_entry_invalid")
        relative = DATABASES[category] + ("-wal" if role == "wal" else "")
        try:
            with closing(
                sqlite3.connect(f"{self.control_copy_path.as_uri()}?mode=ro", uri=True)
            ) as control:
                control.row_factory = sqlite3.Row
                control.execute("PRAGMA query_only=ON")
                rows = control.execute(
                    "SELECT fileID, flags, file FROM Files "
                    "WHERE domain=? AND relativePath=? LIMIT 2",
                    (HOME_DOMAIN, relative),
                ).fetchall()
            if (
                len(rows) != 1
                or rows[0]["flags"] != 1
                or not isinstance(rows[0]["file"], bytes)
                or len(rows[0]["file"]) > MAX_ARCHIVED_ENTRY_BYTES
            ):
                raise ObjectVfsError("apple_manifest_entry_invalid")
            mb = archiver.unarchive(rows[0]["file"])
            if (
                not isinstance(mb, MBFile)
                or mb.relative_path != relative
                or type(mb.size) is not int
                or mb.size < 0
                or not stat.S_ISREG(mb.mode)
                or not isinstance(mb.encryption_key, bytes)
            ):
                raise ObjectVfsError("apple_manifest_entry_invalid")
            now = datetime.now(UTC)
            entry = Entry(
                SimpleNamespace(path=self.control_copy_path.parent),
                rows[0]["fileID"],
                HOME_DOMAIN,
                relative,
                now,
                now,
                now,
                mb.size,
                mb.mode,
                mb.group_id,
                mb.user_id,
                mb.encryption_key,
            )
            return LocalAppleEntry.from_pinned_entry(
                entry, source, self.manifest_sha256
            )
        except ObjectVfsError:
            raise
        except Exception as error:
            raise ObjectVfsError("apple_manifest_entry_invalid") from error

    def assert_entry(
        self, supplied: LocalAppleEntry, source: GroupedSourceReceipt
    ) -> None:
        current = self.project_entry(source)
        if (
            supplied.binding != current.binding
            or supplied.role != current.role
            or supplied.file_id != current.file_id
            or supplied.plaintext_size != current.plaintext_size
            or supplied.ciphertext_sha256 != current.ciphertext_sha256
            or supplied.manifest_sha256 != current.manifest_sha256
            or supplied.wrapped_file_key != current.wrapped_file_key
        ):
            raise ObjectVfsError("apple_manifest_entry_invalid")


class AppleCbcRangeReader:
    """CBC random access after outer GCM and full-source integrity verification."""

    def __init__(
        self,
        encrypted: GroupedRangeReader,
        entry: LocalAppleEntry,
        authority: LocalKeyAuthority,
    ) -> None:
        if (
            encrypted.source.binding != entry.binding
            or encrypted.source.role != entry.role
            or encrypted.source.sha256 != entry.ciphertext_sha256
            or encrypted.source.size
            != (entry.plaintext_size // AES_BLOCK + 1) * AES_BLOCK
        ):
            raise ObjectVfsError("apple_binding_invalid")
        self.encrypted = encrypted
        self.source = entry
        self.authority = authority
        authority.assert_entry(entry, encrypted.source)
        self._key = authority.unwrap(entry)
        self._verified = False
        self._closed = False
        self.peak_cbc_window_bytes = 0

    def _check(self) -> None:
        if self._closed:
            raise ObjectVfsError("apple_authority_closed")
        self.authority._check(self.source.binding.source)

    def _decrypt_window(self, start: int, end: int) -> bytes:
        """Decrypt complete blocks covering [start,end), with the prior block as IV."""
        self._check()
        first = start // AES_BLOCK * AES_BLOCK
        last = (end + AES_BLOCK - 1) // AES_BLOCK * AES_BLOCK
        fetch_start = max(0, first - AES_BLOCK)
        ciphertext = self.encrypted.read(fetch_start, last - fetch_start)
        self.peak_cbc_window_bytes = max(self.peak_cbc_window_bytes, len(ciphertext))
        iv = b"\x00" * AES_BLOCK if first == 0 else ciphertext[:AES_BLOCK]
        current = ciphertext if first == 0 else ciphertext[AES_BLOCK:]
        decryptor = Cipher(algorithms.AES(self._key), modes.CBC(iv)).decryptor()
        plain = decryptor.update(current) + decryptor.finalize()
        return plain[start - first : start - first + end - start]

    def verify_complete(self) -> None:
        self._check()
        self.encrypted.verify_complete()
        tail = self._decrypt_window(
            self.encrypted.source.size - AES_BLOCK, self.encrypted.source.size
        )
        padding_length = tail[-1]
        if (
            padding_length < 1
            or padding_length > AES_BLOCK
            or tail[-padding_length:] != bytes([padding_length]) * padding_length
            or self.encrypted.source.size - padding_length != self.source.plaintext_size
        ):
            raise ObjectVfsError("apple_padding_invalid")
        self._verified = True

    def read(self, offset: int, amount: int) -> bytes:
        self._check()
        if not self._verified:
            raise ObjectVfsError("apple_source_unverified")
        if (
            type(offset) is not int
            or type(amount) is not int
            or offset < 0
            or amount < 0
            or amount > MAX_SQLITE_READ_BYTES
            or offset + amount > self.source.plaintext_size
        ):
            raise ObjectVfsError("apple_range_invalid")
        result = bytearray()
        while amount:
            self._check()
            take = min(amount, CBC_READ_BYTES)
            result.extend(self._decrypt_window(offset, offset + take))
            offset += take
            amount -= take
        return bytes(result)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._verified = False
            self._key = None  # Python cannot guarantee physical key erasure.
            self.encrypted.close()


@contextmanager
def open_apple_grouped_wal_source(
    source_set: GroupedWalSet,
    main_index: Path,
    wal_index: Path,
    backend: RangeBackend,
    object_key: bytes,
    receipt_key: bytes,
    main_entry: LocalAppleEntry,
    wal_entry: LocalAppleEntry,
    authority: LocalKeyAuthority,
    *,
    cache_bytes: int = CACHE_BYTES,
    check_cancel: Callable[[], None] | None = None,
    metrics: dict[str, int] | None = None,
) -> Iterator:
    """Expose plaintext through SQLite only; no source database file is written."""
    _verify_set(source_set, receipt_key)
    if type(cache_bytes) is not int or cache_bytes < 2 * CHUNK_BYTES:
        raise ObjectVfsError("vfs_cache_invalid")
    if (
        main_entry.binding != source_set.main.binding
        or wal_entry.binding != source_set.wal.binding
        or main_entry.role != "main"
        or wal_entry.role != "wal"
    ):
        raise ObjectVfsError("apple_binding_invalid")
    authority._check(main_entry.binding.source)
    with ExitStack() as cleanup:
        main_group = GroupedRangeReader(
            source_set.main,
            main_index,
            backend,
            object_key,
            receipt_key,
            cache_bytes=cache_bytes // 2,
            check_cancel=check_cancel,
        )
        try:
            main = AppleCbcRangeReader(main_group, main_entry, authority)
        except BaseException:
            main_group.close()
            raise
        cleanup.callback(main.close)
        wal_group = GroupedRangeReader(
            source_set.wal,
            wal_index,
            backend,
            object_key,
            receipt_key,
            cache_bytes=cache_bytes - cache_bytes // 2,
            check_cancel=check_cancel,
        )
        try:
            wal = AppleCbcRangeReader(wal_group, wal_entry, authority)
        except BaseException:
            wal_group.close()
            raise
        cleanup.callback(wal.close)
        try:
            with _open_verified_wal_readers(
                main, wal, writer_active_at_freeze=source_set.writer_active_at_freeze
            ) as db:
                yield db
        finally:
            if metrics is not None:
                metrics.update(
                    grouped_plaintext_cache_peak_bytes=(
                        main_group.peak_cached_bytes + wal_group.peak_cached_bytes
                    ),
                    cbc_window_peak_bytes=max(
                        main.peak_cbc_window_bytes, wal.peak_cbc_window_bytes
                    ),
                    descriptor_page_peak_bytes=max(
                        main_group.peak_descriptor_bytes,
                        wal_group.peak_descriptor_bytes,
                    ),
                )
