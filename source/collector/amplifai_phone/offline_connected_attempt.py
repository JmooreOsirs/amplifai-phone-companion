"""Source-only captured-source to journal/object reader bridge.

No production capture, route, cloud credential or network code imports this module.
The caller supplies a trusted journal port and a private ciphertext object store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .offline_apple_object_reader import (
    LocalKeyAuthority,
    open_apple_grouped_wal_source,
)
from .offline_capture_object_store import SyntheticCaptureObjects
from .offline_cloud_capture import (
    CaptureReceipt,
    CaptureSeamError,
    OfflineCloudCaptureLink,
    SelectedFile,
)
from .offline_group_upload_ledger import GroupUploadLedger
from .offline_grouped_object_vfs import (
    MAX_GROUP_CIPHERTEXT,
    GroupedSourceReceipt,
    GroupedWalSet,
    RangeReply,
    package_grouped_source,
    seal_grouped_wal_set,
)
from .offline_object_vfs import ObjectVfsError, SourceBinding


class JournalPort(Protocol):
    """Mirror of the offline CloudAttemptJournal streaming methods."""

    def begin_captured_source(self, claim: dict[str, str]) -> None: ...

    def reserve_captured_group(
        self,
        actor: tuple[str, str, str],
        category: str,
        role: str,
        group_id: str,
        index: int,
        size: int,
        digest: str,
    ) -> str: ...

    def verify_part(
        self, actor: tuple[str, str, str], category: str, index: int
    ) -> None: ...

    def seal_captured_source(
        self, actor: tuple[str, str, str], category: str
    ) -> None: ...

    def processing_group_by_id(
        self,
        actor: tuple[str, str, str],
        epoch: int,
        category: str,
        group_id: str,
    ) -> tuple[str, int, str]: ...

    def revoke_and_erase(self, actor: tuple[str, str, str]) -> int: ...


class CipherObjectStore(Protocol):
    def put_exact(self, object_key: str, payload: bytes, digest: str) -> None: ...

    def get_range(
        self, object_key: str, start: int, end: int, max_bytes: int
    ) -> RangeReply: ...


@dataclass(frozen=True)
class CapturedGroupedSource:
    category: str
    source_set: GroupedWalSet
    main_index: Path
    wal_index: Path


class _GroupWriter:
    def __init__(self, bridge: CapturedGroupBridge, role: str) -> None:
        self.bridge = bridge
        self.role = role

    def put(self, object_id: str, ciphertext: bytes) -> None:
        if len(ciphertext) > MAX_GROUP_CIPHERTEXT:
            raise ObjectVfsError("cloud_group_oversize")
        index = self.bridge.next_index
        digest = hashlib.sha256(ciphertext).hexdigest()
        for retry in range(2):
            try:
                key = self.bridge.journal.reserve_captured_group(
                    self.bridge.actor,
                    self.bridge.category,
                    self.role,
                    object_id,
                    index,
                    len(ciphertext),
                    digest,
                )
                self.bridge.upload_ledger.reserved(index, object_id, key)
                self.bridge.objects.put_exact(key, ciphertext, digest)
                self.bridge.journal.verify_part(
                    self.bridge.actor, self.bridge.category, index
                )
                self.bridge.upload_ledger.verified(index)
                break
            except (OSError, TimeoutError):
                if retry:
                    raise
        self.bridge.next_index += 1


class _ProcessingRanges:
    def __init__(self, bridge: CapturedGroupBridge, epoch: int) -> None:
        self.bridge = bridge
        self.epoch = epoch

    def get_range(
        self, object_id: str, start: int, end: int, max_bytes: int
    ) -> RangeReply:
        key, size, _digest = self.bridge.journal.processing_group_by_id(
            self.bridge.actor, self.epoch, self.bridge.category, object_id
        )
        if end >= size or max_bytes > MAX_GROUP_CIPHERTEXT:
            raise ObjectVfsError("cloud_range_invalid")
        return self.bridge.objects.get_range(key, start, end, max_bytes)


class CapturedGroupBridge:
    """Package original Apple ciphertext after a current completed capture."""

    def __init__(
        self,
        link: OfflineCloudCaptureLink,
        receipt: CaptureReceipt,
        authority: LocalKeyAuthority,
        category: str,
        journal: JournalPort,
        objects: CipherObjectStore,
        object_key: bytes,
        receipt_key: bytes,
    ) -> None:
        link.verify_receipt(receipt, authority=authority)
        if (
            category not in link.admission.categories
            or authority.admission != link.admission
        ):
            raise CaptureSeamError("capture_source_unapproved")
        files = {
            entry.role: entry for entry in receipt.files if entry.category == category
        }
        if set(files) != {"main", "wal"}:
            # SQLite may regenerate SHM, but dropping a captured sidecar without
            # an explicit local-only disposition would hide source coverage.
            raise CaptureSeamError("capture_sidecar_unqualified")
        if (
            not isinstance(object_key, bytes)
            or len(object_key) != 32
            or not isinstance(receipt_key, bytes)
            or len(receipt_key) != 32
        ):
            raise CaptureSeamError("capture_key_invalid")
        admission = link.admission
        source_set_id = str(
            uuid.uuid5(uuid.UUID(admission.attempt_id), f"{receipt.mac}:{category}")
        )
        self.link = link
        self.receipt = receipt
        self.authority = authority
        self.category = category
        self.journal = journal
        self.objects = objects
        self.object_key = object_key
        self.receipt_key = receipt_key
        self.files: dict[str, SelectedFile] = files
        self.actor = (admission.owner_id, admission.phone_id, admission.attempt_id)
        self.binding = SourceBinding(*self.actor, category, source_set_id)
        self.next_index = 0
        self.upload_ledger: GroupUploadLedger | None = None
        self.upload_directory: Path | None = None
        self.claim = {
            "ownerId": admission.owner_id,
            "phoneId": admission.phone_id,
            "attemptId": admission.attempt_id,
            "actionId": admission.action_id,
            "collectionReceiptId": admission.collection_receipt_id,
            "consentReceiptId": admission.consent_receipt_id,
            "disclosureVersion": admission.disclosure_version,
            "category": category,
            "captureMac": receipt.mac,
            "sessionNonce": receipt.session_nonce,
            "sourceSetId": source_set_id,
        }

    def package(self, index_root: Path) -> CapturedGroupedSource:
        self.link.verify_receipt(self.receipt, authority=self.authority)
        if not index_root.is_dir() or index_root.is_symlink():
            raise ObjectVfsError("vfs_index_producer_invalid")
        work_dir = index_root / f".{self.binding.source_set_id}-upload"
        if work_dir.exists():
            raise ObjectVfsError("cloud_upload_restart_requires_revoke")
        work_dir.mkdir(mode=0o700)
        self.upload_directory = work_dir
        self.upload_ledger = GroupUploadLedger(
            work_dir / "ledger.sqlite3",
            self.actor,
            self.binding.source_set_id,
            self.receipt.mac,
            self.link.admission.consent_receipt_id,
            self.object_key,
            self.receipt_key,
        )
        indexes = {role: work_dir / f"{role}.index" for role in ("main", "wal")}
        grouped = {}
        try:
            self.journal.begin_captured_source(self.claim)
            for role in ("main", "wal"):
                selected = self.files[role]
                grouped[role] = package_grouped_source(
                    self.link.source_reader(
                        self.receipt, selected, authority=self.authority
                    ),
                    indexes[role],
                    self.binding,
                    role,
                    _GroupWriter(self, role),
                    self.object_key,
                    self.receipt_key,
                    sidecars=("wal",) if role == "main" else (),
                    prepare_group=lambda group_id, entries, size, digest, role=role: (
                        self.upload_ledger.prepare(
                            self.next_index, role, group_id, size, digest, entries
                        )
                    ),
                )
                if (
                    grouped[role].size != selected.size
                    or grouped[role].sha256 != selected.sha256
                ):
                    raise CaptureSeamError("capture_source_mutated")
                self.authority.project_entry(grouped[role])
            self.link.verify_receipt(self.receipt, authority=self.authority)
            source_set = seal_grouped_wal_set(
                grouped["main"], grouped["wal"], self.receipt_key
            )
            self.journal.seal_captured_source(self.actor, self.category)
            _write_source_set(work_dir / "source-set.json", source_set)
            self.upload_ledger.finish()
        except BaseException:
            try:
                self.retry_cleanup()
            except BaseException as cleanup:
                raise ObjectVfsError("cloud_cleanup_incomplete") from cleanup
            raise
        return CapturedGroupedSource(
            self.category, source_set, indexes["main"], indexes["wal"]
        )

    def resume_completed(self, index_root: Path) -> CapturedGroupedSource:
        """Reattach only a completed set with still-current capture and keys."""
        self.link.verify_receipt(self.receipt, authority=self.authority)
        work_dir = index_root / f".{self.binding.source_set_id}-upload"
        if not work_dir.is_dir() or work_dir.is_symlink():
            raise ObjectVfsError("cloud_upload_restart_requires_revoke")
        ledger = GroupUploadLedger(
            work_dir / "ledger.sqlite3",
            self.actor,
            self.binding.source_set_id,
            self.receipt.mac,
            self.link.admission.consent_receipt_id,
            self.object_key,
            self.receipt_key,
            reopen=True,
        )
        if ledger.state != "complete":
            raise ObjectVfsError("cloud_upload_restart_requires_revoke")
        source_set = _read_source_set(
            work_dir / "source-set.json", self.binding, self.receipt_key
        )
        for entry in (source_set.main, source_set.wal):
            self.authority.project_entry(entry)
        indexes = {role: work_dir / f"{role}.index" for role in ("main", "wal")}
        if any(not path.is_file() or path.is_symlink() for path in indexes.values()):
            raise ObjectVfsError("cloud_upload_restart_requires_revoke")
        self.upload_ledger = ledger
        self.upload_directory = work_dir
        return CapturedGroupedSource(
            self.category, source_set, indexes["main"], indexes["wal"]
        )

    def retry_cleanup(self) -> None:
        if self.upload_ledger is None or self.upload_directory is None:
            raise ObjectVfsError("cloud_upload_ledger_invalid")
        cleanup_abandoned_upload(
            self.upload_ledger,
            self.upload_directory,
            self.journal,
            self.link.backend.remote_objects,
        )

    @contextmanager
    def open_source(self, grouped: CapturedGroupedSource, epoch: int):
        if (
            grouped.category != self.category
            or grouped.source_set.main.binding != self.binding
        ):
            raise ObjectVfsError("cloud_source_binding_invalid")
        self.link.verify_receipt(self.receipt, authority=self.authority)
        entries = {
            role: self.authority.project_entry(receipt)
            for role, receipt in (
                ("main", grouped.source_set.main),
                ("wal", grouped.source_set.wal),
            )
        }
        with open_apple_grouped_wal_source(
            grouped.source_set,
            grouped.main_index,
            grouped.wal_index,
            _ProcessingRanges(self, epoch),
            self.object_key,
            self.receipt_key,
            entries["main"],
            entries["wal"],
            self.authority,
        ) as db:
            yield db


def _source_row(receipt: GroupedSourceReceipt) -> list[object]:
    return [
        receipt.role,
        receipt.size,
        receipt.sha256,
        receipt.chunk_count,
        receipt.page_count,
        receipt.index_sha256,
        list(receipt.sidecars),
        receipt.mac,
    ]


def _write_source_set(path: Path, source_set: GroupedWalSet) -> None:
    payload = json.dumps(
        [
            "amplifai-captured-source-set-v1",
            _source_row(source_set.main),
            _source_row(source_set.wal),
            source_set.mac,
        ],
        separators=(",", ":"),
    ).encode("ascii")
    if len(payload) > 4096:
        raise ObjectVfsError("cloud_source_set_invalid")
    temporary = path.with_name(".source-set-pending")
    handle = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _read_source_set(
    path: Path, binding: SourceBinding, receipt_key: bytes
) -> GroupedWalSet:
    try:
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(handle, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            payload = stream.read(4097)
        if len(payload) > 4096:
            raise ValueError
        document = json.loads(payload)
        if (
            type(document) is not list
            or len(document) != 4
            or document[0] != "amplifai-captured-source-set-v1"
        ):
            raise ValueError
        rows = []
        for role, row in zip(("main", "wal"), document[1:3], strict=True):
            if (
                type(row) is not list
                or len(row) != 8
                or row[0] != role
                or type(row[6]) is not list
            ):
                raise ValueError
            rows.append(
                GroupedSourceReceipt(
                    binding,
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    tuple(row[6]),
                    row[7],
                )
            )
        expected = seal_grouped_wal_set(rows[0], rows[1], receipt_key)
        if not isinstance(document[3], str) or not hmac.compare_digest(
            expected.mac, document[3]
        ):
            raise ValueError
        return expected
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        raise ObjectVfsError("cloud_source_set_invalid") from error


class PrivateCipherObjects:
    """Offline file-backed stand-in; only grouped GCM ciphertext is stored."""

    def __init__(self, root: Path, attempt_id: str) -> None:
        if root.exists() or not root.parent.is_dir():
            raise ObjectVfsError("cloud_store_invalid")
        self.root = root
        self.attempt_id = str(uuid.UUID(attempt_id))
        root.mkdir(mode=0o700)

    def _path(self, key: str) -> Path:
        parts = key.split("/") if isinstance(key, str) else []
        try:
            valid_object_id = len(parts) == 2 and str(uuid.UUID(parts[1])) == parts[1]
        except (ValueError, TypeError):
            valid_object_id = False
        if not valid_object_id or parts[0] != self.attempt_id:
            raise ObjectVfsError("cloud_store_scope_invalid")
        return self.root / parts[1]

    @staticmethod
    def _reader(path: Path):
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            os.close(handle)
            raise ObjectVfsError("cloud_store_scope_invalid")
        return os.fdopen(handle, "rb")

    def put_exact(self, object_key: str, payload: bytes, digest: str) -> None:
        if not isinstance(payload, bytes) or len(payload) > MAX_GROUP_CIPHERTEXT:
            raise ObjectVfsError("cloud_group_oversize")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ObjectVfsError("cloud_store_integrity")
        path = self._path(object_key)
        try:
            handle = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            with self._reader(path) as stream:
                existing = stream.read(MAX_GROUP_CIPHERTEXT + 1)
            if (
                len(existing) != len(payload)
                or hashlib.sha256(existing).hexdigest() != digest
            ):
                raise ObjectVfsError("cloud_store_conflict") from None
            return
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def get_range(
        self, object_key: str, start: int, end: int, max_bytes: int
    ) -> RangeReply:
        if (
            type(start) is not int
            or type(end) is not int
            or type(max_bytes) is not int
            or start < 0
            or end < start
            or end - start + 1 > max_bytes
            or max_bytes > MAX_GROUP_CIPHERTEXT
        ):
            raise ObjectVfsError("cloud_range_invalid")
        path = self._path(object_key)
        with self._reader(path) as stream:
            total = stream.seek(0, os.SEEK_END)
            if end >= total:
                raise ObjectVfsError("cloud_range_invalid")
            stream.seek(start)
            body = stream.read(end - start + 1)
        if len(body) != end - start + 1:
            raise ObjectVfsError("cloud_range_invalid")
        return RangeReply(206, f"bytes {start}-{end}/{total}", len(body), body)

    def exists(self, object_key: str) -> bool:
        return self._path(object_key).exists()

    def delete(self, object_key: str) -> None:
        self._path(object_key).unlink(missing_ok=True)
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def read_chunks(self, object_key: str):
        with self._reader(self._path(object_key)) as stream:
            while chunk := stream.read(128 * 1024):
                yield chunk


def cleanup_abandoned_upload(
    ledger: GroupUploadLedger,
    work_dir: Path,
    journal: JournalPort,
    capture_objects: SyntheticCaptureObjects,
) -> None:
    """Revoke first, then retry exact-key and capture-object erasure."""
    actor = tuple(ledger.identity[:3])
    if (
        ledger.path.parent != work_dir
        or capture_objects.attempt_id != actor[2]
        or work_dir.is_symlink()
    ):
        raise ObjectVfsError("cloud_cleanup_scope_invalid")
    if ledger.state != "erased":
        ledger.mark_erasure_pending()
    if journal.revoke_and_erase(actor) != 0:
        raise ObjectVfsError("cloud_cleanup_incomplete")
    capture_objects.cleanup()
    if capture_objects.keys():
        raise ObjectVfsError("cloud_cleanup_incomplete")
    if ledger.state != "erased":
        ledger.mark_erased()
    for path in work_dir.iterdir():
        if (
            path.is_symlink()
            or not path.is_file()
            or (
                path.name
                not in {
                    "main.index",
                    "wal.index",
                    "ledger.sqlite3",
                    "ledger.sqlite3-journal",
                    "source-set.json",
                    ".source-set-pending",
                }
                and not path.name.startswith(".amplifai-index-")
            )
        ):
            raise ObjectVfsError("cloud_cleanup_scope_invalid")
        if path.name != "ledger.sqlite3":
            path.unlink()
    ledger.path.unlink()
    work_dir.rmdir()
