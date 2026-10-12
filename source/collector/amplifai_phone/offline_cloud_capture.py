"""Offline DeviceLink selected-source admission probe; never imported by collection."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import plistlib
import sqlite3
import stat
import struct
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from bpylist2 import archiver
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pyiosbackup.manifest_dbs.sqlite3 import MBFile  # registers pinned archive class
from pymobiledevice3.services.device_link import (
    CODE_ERROR_REMOTE,
    CODE_FILE_DATA,
    CODE_SUCCESS,
    FILE_TRANSFER_TERMINATOR,
)

from .backup_stream import StreamedDeviceLink
from .backup_watchdog import RECEIVE_CHUNK_BYTES, BackupWatchdog
from .ios_backup import DATABASES, HOME_DOMAIN
from .offline_capture_object_store import SyntheticCaptureObjects
from .offline_grouped_object_vfs import GROUP_CHUNKS
from .offline_object_vfs import CHUNK_BYTES
from .virtual_source_store import SelectedSourceStore, VirtualStoreError
from .workspace import SessionWorkspace

if TYPE_CHECKING:
    from .offline_apple_object_reader import LocalKeyAuthority

CLOUD_DISCLOSURE_VERSION = "phone-cloud-source-v1"
REQUIRED_CONTROLS = frozenset(
    {"Manifest.db", "Manifest.plist", "Info.plist", "Status.plist"}
)
OPTIONAL_CONTROLS = frozenset({"Manifest.db-wal", "Manifest.db-shm"})
CONTROLS = REQUIRED_CONTROLS | OPTIONAL_CONTROLS
ROLES = ("main", "wal", "shm")
MAX_PLIST_BYTES = 16 * 1024 * 1024
MAX_ARCHIVED_ENTRY_BYTES = 1024 * 1024


class CaptureSeamError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CaptureAdmission:
    owner_id: str
    phone_id: str
    attempt_id: str
    action_id: str
    collection_receipt_id: str
    consent_receipt_id: str
    categories: tuple[str, ...]
    disclosure_version: str
    cloud_source_approved: bool

    def __post_init__(self) -> None:
        values = (
            self.owner_id,
            self.phone_id,
            self.attempt_id,
            self.action_id,
            self.collection_receipt_id,
            self.consent_receipt_id,
        )
        try:
            valid_ids = all(str(uuid.UUID(value)) == value.lower() for value in values)
        except (TypeError, ValueError, AttributeError):
            valid_ids = False
        valid_categories = (
            tuple(sorted(set(self.categories)))
            if isinstance(self.categories, tuple)
            and all(isinstance(source, str) for source in self.categories)
            else ()
        )
        if (
            not valid_ids
            or self.cloud_source_approved is not True
            or self.disclosure_version != CLOUD_DISCLOSURE_VERSION
            or not self.categories
            or valid_categories != self.categories
            or any(source not in DATABASES for source in self.categories)
        ):
            raise CaptureSeamError("capture_admission_invalid")


@dataclass(frozen=True)
class SelectedFile:
    category: str
    role: str
    file_id: str
    size: int
    sha256: str


@dataclass(frozen=True)
class CaptureReceipt:
    attempt_id: str
    session_nonce: str
    files: tuple[SelectedFile, ...]
    control_sha256: tuple[tuple[str, str], ...]
    mac: str


class EncryptedAttemptBackend:
    """Private local controls plus bounded encrypted synthetic remote objects."""

    def __init__(
        self,
        workspace: SessionWorkspace,
        admission: CaptureAdmission,
        key: bytes,
        remote_objects: SyntheticCaptureObjects | None,
    ) -> None:
        workspace_root = workspace.directory.resolve()
        remote_root = remote_objects.root.resolve() if remote_objects else None
        if (
            not isinstance(key, bytes)
            or len(key) != 32
            or (
                remote_objects is not None
                and remote_objects.attempt_id != admission.attempt_id
            )
            or (remote_root is not None and workspace_root == remote_root)
            or (remote_root is not None and workspace_root in remote_root.parents)
            or (remote_root is not None and remote_root in workspace_root.parents)
        ):
            raise CaptureSeamError("capture_key_invalid")
        self.workspace = workspace
        self.local_root = workspace.checked_path(
            workspace.directory / "cloud-local-controls", create_parents=True
        )
        self.admission = admission
        self.cipher = AESGCM(key)
        self.remote_objects = remote_objects
        self.max_input_bytes = 0
        self.max_sealed_bytes = 0
        self.local_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _scope(self, key: str) -> str:
        parts = key.split("/")
        if (
            len(parts) != 3
            or len(parts[0]) != 32
            or len(parts[2]) != 32
            or any(
                character not in "0123456789abcdef"
                for value in (parts[0], parts[2])
                for character in value
            )
            or parts[1]
            not in {
                "control",
                *(f"{source}-{role}" for source in DATABASES for role in ROLES),
            }
        ):
            raise OSError("Invalid synthetic object identity")
        return parts[1]

    def _aad(self, key: str, size: int) -> bytes:
        return json.dumps(
            [
                "amplifai-capture-object-v1",
                self.admission.owner_id,
                self.admission.phone_id,
                self.admission.attempt_id,
                self._scope(key),
                key,
                size,
            ],
            separators=(",", ":"),
        ).encode("ascii")

    def put(self, key: str, payload: bytes) -> None:
        scope = self._scope(key)
        if scope == "control":
            self.workspace.check_bound(len(payload))
            target = self.workspace.checked_path(
                self.local_root / key.replace("/", "-"), create_parents=True
            )
            handle = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return
        if scope.split("-")[0] not in self.admission.categories:
            raise OSError("Source was not approved")
        if self.remote_objects is None:
            raise OSError("Selected-source backend is unavailable")
        self.max_input_bytes = max(self.max_input_bytes, len(payload))
        nonce = os.urandom(12)
        sealed = nonce + self.cipher.encrypt(
            nonce, payload, self._aad(key, len(payload))
        )
        self.max_sealed_bytes = max(self.max_sealed_bytes, len(sealed))
        # A lost response retries the same encrypted bytes, never a new nonce
        # under the same write-once object identity.
        for attempt in range(2):
            try:
                self.remote_objects.put_exact(key, sealed)
                if self.remote_objects.get_exact(key) != sealed:
                    raise OSError("Synthetic object readback changed")
                return
            except OSError:
                if attempt:
                    raise

    def get(self, key: str) -> bytes:
        if self._scope(key) == "control":
            return self.workspace.checked_path(
                self.local_root / key.replace("/", "-")
            ).read_bytes()
        if self.remote_objects is None:
            raise OSError("Selected-source backend is unavailable")
        sealed = self.remote_objects.get_exact(key)
        if len(sealed) < 28:
            raise OSError("Invalid simulated object")
        try:
            return self.cipher.decrypt(
                sealed[:12], sealed[12:], self._aad(key, len(sealed) - 28)
            )
        except ValueError as error:
            raise OSError("Invalid simulated object") from error
        except Exception as error:
            raise OSError("Simulated object integrity failed") from error

    def delete(self, key: str) -> None:
        if self._scope(key) == "control":
            # Cleanup must still run if free space falls below the write reserve.
            (self.local_root / key.replace("/", "-")).unlink(missing_ok=True)
        else:
            if self.remote_objects is None:
                raise OSError("Selected-source backend is unavailable")
            self.remote_objects.delete(key)

    @property
    def local_control_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.local_root.iterdir())

    @property
    def remote_ciphertext_bytes(self) -> int:
        return self.remote_objects.ciphertext_bytes if self.remote_objects else 0


class SelectedSourceReader:
    """Bounded sequential reader over verified virtual-store chunks."""

    def __init__(self, store: SelectedSourceStore, path: str) -> None:
        self._chunks: Iterator[bytes] = iter(store.read_chunks(path))
        self._pending = b""

    def read(self, size: int) -> bytes:
        if type(size) is not int or size < 0 or size > RECEIVE_CHUNK_BYTES:
            raise CaptureSeamError("capture_read_size_invalid")
        out = bytearray()
        while len(out) < size:
            if not self._pending:
                try:
                    self._pending = next(self._chunks)
                except StopIteration:
                    break
            count = min(size - len(out), len(self._pending))
            out.extend(self._pending[:count])
            self._pending = self._pending[count:]
        return bytes(out)


class OfflineCloudCaptureLink(StreamedDeviceLink):
    """Pinned DeviceLink wire semantics; no connection to the released collector."""

    def __init__(
        self,
        service,
        workspace: SessionWorkspace,
        watchdog: BackupWatchdog,
        admission: CaptureAdmission,
        assert_current: Callable[[CaptureAdmission, str | None], None],
        object_key: bytes,
        receipt_key: bytes,
        remote_objects: SyntheticCaptureObjects | None,
        *,
        selected_backend: EncryptedAttemptBackend | None = None,
    ) -> None:
        if not isinstance(receipt_key, bytes) or len(receipt_key) != 32:
            raise CaptureSeamError("capture_key_invalid")
        self.admission = admission
        self.assert_current = assert_current
        self.assert_current(admission, None)
        if selected_backend is None:
            self.backend = EncryptedAttemptBackend(
                workspace, admission, object_key, remote_objects
            )
        else:
            if remote_objects is not None or selected_backend.admission != admission:
                raise CaptureSeamError("capture_backend_invalid")
            self.backend = selected_backend
        self.store = SelectedSourceStore(
            self.backend, object_chunk_bytes=GROUP_CHUNKS * CHUNK_BYTES
        )
        self.receipt_key = receipt_key
        self.session_nonce = uuid.uuid4().hex
        self.required_categories = frozenset(admission.categories)
        self._terminal = False
        self._completed = False
        self.defer_source_readback = False
        self._completion_snapshot: tuple[tuple[str, str, int, str], ...] | None = None
        self._encrypted_authority: LocalKeyAuthority | None = None
        self._authority_control_paths: tuple[Path, Path] | None = None
        super().__init__(
            service,
            workspace.directory,
            workspace=workspace,
            watchdog=watchdog,
            preserve_file=None,
        )

    def _check(self, source: str | None = None) -> None:
        self.watchdog.raise_if_aborted()
        self.assert_current(self.admission, source)

    def _scope(self, device_name: str, file_name: str) -> str:
        control_prefix = f"{self.admission.phone_id}/"
        if file_name in (control_prefix + name for name in CONTROLS):
            return "control" if device_name in CONTROLS else "discard"
        for category, relative in DATABASES.items():
            for role, suffix in (("main", ""), ("wal", "-wal"), ("shm", "-shm")):
                name = HOME_DOMAIN + "-" + relative + suffix
                file_id = hashlib.sha1(name.encode("utf-8")).hexdigest()
                expected_path = f"{self.admission.phone_id}/{file_id[:2]}/{file_id}"
                if device_name == name and file_name == expected_path:
                    return (
                        f"{category}-{role}"
                        if category in self.admission.categories
                        else "discard"
                    )
        return "discard"

    async def receive_message(self):
        message = await super().receive_message()
        if message[0] == "DLMessageProcessMessage":
            if (
                len(message) != 2
                or not isinstance(message[1], dict)
                or type(message[1].get("ErrorCode")) is not int
                or message[1]["ErrorCode"] != 0
                or self._terminal
            ):
                raise CaptureSeamError("capture_completion_invalid")
            self._terminal = True
        return message

    async def run_capture(self) -> None:
        if self._completed or self._terminal:
            raise CaptureSeamError("capture_retry_ambiguous")
        self._check()
        try:
            await self.dl_loop()
            self.watchdog.backup_completed()
            self._completion_snapshot = self.store.snapshot()
            self._completed = self._terminal
        except BaseException:
            try:
                self.store.remove_items([self.admission.phone_id])
            except VirtualStoreError as cleanup:
                raise CaptureSeamError("capture_cleanup_incomplete") from cleanup
            raise

    async def upload_files(self, _message) -> None:
        while True:
            self._check()
            device_name = await self._prefixed_recv()
            if not device_name:
                break
            file_name = await self._prefixed_recv()
            scope = self._scope(device_name, file_name)
            source = (
                scope.split("-")[0] if scope not in {"control", "discard"} else None
            )
            self._check(source)
            keep = scope != "discard"
            writer = self.store.open_write(file_name, selected=keep, scope=scope)
            try:
                size, code = await self._frame()
                while code == CODE_FILE_DATA:
                    if size <= 0:
                        raise CaptureSeamError("capture_frame_invalid")
                    self.watchdog.file_transfer_started()
                    while size:
                        self._check(source)
                        payload = await self._recvall(min(size, RECEIVE_CHUNK_BYTES))
                        writer.write(payload)
                        if not self.watchdog.wire_payload_accounting:
                            self.watchdog.payload_received(len(payload))
                        self._received += len(payload)
                        if keep:
                            self._retained += len(payload)
                        size -= len(payload)
                        await asyncio.sleep(0)
                    self.watchdog.file_transfer_completed()
                    size, code = await self._frame()
                if code == CODE_ERROR_REMOTE:
                    while size:
                        await self._recvall(min(size, RECEIVE_CHUNK_BYTES))
                        size -= min(size, RECEIVE_CHUNK_BYTES)
                    if keep:
                        raise CaptureSeamError("capture_selected_incomplete")
                elif code != CODE_SUCCESS or size:
                    raise CaptureSeamError("capture_terminator_invalid")
                writer.finish()
                self._after_file_commit(scope, file_name)
            except BaseException:
                writer.abort()
                raise
            self._files += 1
            self.publish_progress(final=True)
        await self.status_response(0)

    def _after_file_commit(self, scope: str, file_name: str) -> None:
        """Offline capture extensions may bind a completed file generation."""

    async def create_directory(self, message) -> None:
        self._check()
        self.store.create_directory(message[1])
        await self.status_response(0)

    async def contents_of_directory(self, message) -> None:
        self._check()
        await self.status_response(0, status_dict=self.store.list_directory(message[1]))

    async def download_files(self, message) -> None:
        self._check()
        for name in message[1]:
            encoded = name.encode("utf-8")
            await self._sendall(struct.pack(">I", len(encoded)) + encoded)
            for payload in self.store.read_chunks(name):
                self._check()
                await self._sendall(
                    struct.pack(">IB", len(payload) + 1, CODE_FILE_DATA) + payload
                )
                self.watchdog.payload_sent(len(payload))
            await self._sendall(struct.pack(">IB", 1, CODE_SUCCESS))
        await self._sendall(FILE_TRANSFER_TERMINATOR)
        await self.status_response(0)

    async def copy_item(self, message) -> None:
        self._check()
        self.store.copy_item(message[1], message[2])
        await self.status_response(0)

    async def move_items(self, message) -> None:
        self._check()
        self.store.move_items(message[1])
        await self.status_response(0)

    async def remove_items(self, message) -> None:
        self._check()
        self.store.remove_items(message[1])
        await self.status_response(0)

    def _control_chunks(self, name: str) -> Iterator[bytes]:
        path = f"{self.admission.phone_id}/{name}"
        if self.store.file_scope(path) != "control":
            raise CaptureSeamError("capture_control_invalid")
        return iter(self.store.read_chunks(path))

    def _control(self, name: str) -> bytes:
        if self.store.file_size(f"{self.admission.phone_id}/{name}") > MAX_PLIST_BYTES:
            raise CaptureSeamError("capture_control_oversize")
        return b"".join(self._control_chunks(name))

    def open_local_key_authority(self, password: str) -> LocalKeyAuthority:
        """Unlock only local controls after this DeviceLink attempt completed."""
        self._check()
        if not self._completed or self._completion_snapshot != self.store.snapshot():
            raise CaptureSeamError("capture_not_completed")
        if self._encrypted_authority is not None:
            raise CaptureSeamError("capture_authority_already_open")
        from .offline_apple_object_reader import LocalKeyAuthority

        phone = self.admission.phone_id
        controls = self.store.scoped_paths("control")
        if any(f"{phone}/{name}" in controls for name in OPTIONAL_CONTROLS):
            raise CaptureSeamError("capture_control_sidecar_unqualified")
        for name in REQUIRED_CONTROLS:
            if f"{phone}/{name}" not in controls:
                raise CaptureSeamError("capture_control_invalid")
        root = self.workspace.checked_path(
            self.workspace.directory / "cloud-authority-controls",
            create_parents=True,
        )
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        paths = (root / "Manifest.plist", root / "Manifest.db")
        try:
            for name, path in zip(("Manifest.plist", "Manifest.db"), paths):
                handle = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(handle, "wb") as sink:
                    for chunk in self._control_chunks(name):
                        self._check()
                        self.workspace.check_bound(len(chunk))
                        sink.write(chunk)
                    sink.flush()
                    os.fsync(sink.fileno())
            authority = LocalKeyAuthority(
                self.admission,
                paths[0],
                paths[1],
                password,
                self.assert_current,
            )
        except BaseException:
            for path in paths:
                path.unlink(missing_ok=True)
            raise
        self._encrypted_authority = authority
        self._authority_control_paths = paths
        return authority

    def close_local_key_authority(self) -> None:
        authority = self._encrypted_authority
        paths = self._authority_control_paths
        self._encrypted_authority = None
        self._authority_control_paths = None
        if authority is not None:
            authority.close()
        if paths is not None:
            for path in paths:
                path.unlink(missing_ok=True)

    def _project(
        self, authority: LocalKeyAuthority | None = None
    ) -> tuple[SelectedFile, ...]:
        try:
            controls = {
                name: plistlib.loads(self._control(name))
                for name in ("Manifest.plist", "Info.plist", "Status.plist")
            }
        except (
            ValueError,
            TypeError,
            plistlib.InvalidFileException,
            VirtualStoreError,
        ) as error:
            raise CaptureSeamError("capture_control_invalid") from error
        if any(not isinstance(value, dict) for value in controls.values()):
            raise CaptureSeamError("capture_control_invalid")
        encrypted = controls["Manifest.plist"].get("IsEncrypted")
        if encrypted is True:
            if authority is None:
                raise CaptureSeamError("capture_encrypted_unqualified")
            if authority is not self._encrypted_authority:
                raise CaptureSeamError("capture_authority_invalid")
            authority._check(None)
            if any(
                f"{self.admission.phone_id}/{name}"
                in self.store.scoped_paths("control")
                for name in OPTIONAL_CONTROLS
            ):
                raise CaptureSeamError("capture_control_sidecar_unqualified")
            digest = hashlib.sha256()
            for chunk in self._control_chunks("Manifest.db"):
                digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), authority.manifest_sha256):
                raise CaptureSeamError("capture_authority_invalid")
            try:
                with closing(
                    sqlite3.connect(
                        f"{authority.control_copy_path.as_uri()}?mode=ro", uri=True
                    )
                ) as db:
                    db.row_factory = sqlite3.Row
                    db.execute("PRAGMA query_only=ON")
                    return self._project_rows(db, encrypted=True)
            except (sqlite3.Error, ValueError, TypeError, KeyError) as error:
                raise CaptureSeamError("capture_manifest_invalid") from error
        if encrypted is not False or authority is not None:
            raise CaptureSeamError("capture_encrypted_unqualified")
        with tempfile.TemporaryDirectory(
            prefix="capture-control-", dir=self.workspace.directory
        ) as temporary:
            path = Path(temporary) / "Manifest.db"
            with path.open("xb") as sink:
                for chunk in self._control_chunks("Manifest.db"):
                    self.workspace.check_bound(len(chunk))
                    sink.write(chunk)
            for name in OPTIONAL_CONTROLS:
                if f"{self.admission.phone_id}/{name}" in self.store.scoped_paths(
                    "control"
                ):
                    with Path(temporary, name).open("xb") as sink:
                        for chunk in self._control_chunks(name):
                            self.workspace.check_bound(len(chunk))
                            sink.write(chunk)
            try:
                with closing(
                    sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
                ) as db:
                    db.row_factory = sqlite3.Row
                    db.execute("PRAGMA query_only=ON")
                    files = self._project_rows(db, encrypted=False)
            except (sqlite3.Error, ValueError, TypeError, KeyError) as error:
                raise CaptureSeamError("capture_manifest_invalid") from error
        return files

    def _project_rows(
        self, db: sqlite3.Connection, *, encrypted: bool
    ) -> tuple[SelectedFile, ...]:
        projected = []
        seen_paths = set()
        for category in self.admission.categories:
            for role, suffix in (("main", ""), ("wal", "-wal"), ("shm", "-shm")):
                relative = DATABASES[category] + suffix
                rows = db.execute(
                    "SELECT fileID, flags, file FROM Files WHERE domain=? AND relativePath=? LIMIT 2",
                    (HOME_DOMAIN, relative),
                ).fetchall()
                if len(rows) > 1 or (
                    role == "main" and not rows and category in self.required_categories
                ):
                    raise CaptureSeamError("capture_manifest_incomplete")
                if not rows:
                    continue
                row = rows[0]
                file_id = hashlib.sha1(
                    (HOME_DOMAIN + "-" + relative).encode()
                ).hexdigest()
                if (
                    row["fileID"] != file_id
                    or row["flags"] != 1
                    or not isinstance(row["file"], bytes)
                    or len(row["file"]) > MAX_ARCHIVED_ENTRY_BYTES
                ):
                    raise CaptureSeamError("capture_manifest_identity")
                try:
                    entry = archiver.unarchive(row["file"])
                except Exception as error:
                    raise CaptureSeamError("capture_manifest_identity") from error
                if (
                    not isinstance(entry, MBFile)
                    or entry.relative_path != relative
                    or type(entry.size) is not int
                    or entry.size < 0
                    or not stat.S_ISREG(entry.mode)
                ):
                    raise CaptureSeamError("capture_manifest_identity")
                if encrypted:
                    if (
                        not isinstance(entry.encryption_key, bytes)
                        or len(entry.encryption_key) < 12
                    ):
                        raise CaptureSeamError("capture_manifest_identity")
                elif entry.encryption_key:
                    raise CaptureSeamError("capture_manifest_identity")
                virtual_path = f"{self.admission.phone_id}/{file_id[:2]}/{file_id}"
                if category in getattr(self, "capture_unavailable_reasons", {}):
                    # Still validate the final manifest identity above. An
                    # ambiguous optional generation is never parsed or sealed.
                    if virtual_path in self.store.scoped_paths(f"{category}-{role}"):
                        seen_paths.add(virtual_path)
                    continue
                if self.store.file_scope(virtual_path) != f"{category}-{role}":
                    raise CaptureSeamError("capture_source_binding")
                if self.defer_source_readback:
                    total = self.store.file_size(virtual_path)
                    digest_hex = self.store.file_sha256(virtual_path)
                else:
                    digest = hashlib.sha256()
                    total = 0
                    for chunk in self.store.read_chunks(virtual_path):
                        self._check(category)
                        digest.update(chunk)
                        total += len(chunk)
                    digest_hex = digest.hexdigest()
                expected_size = (entry.size // 16 + 1) * 16 if encrypted else entry.size
                if total != expected_size:
                    raise CaptureSeamError("capture_source_length")
                projected.append(
                    SelectedFile(category, role, file_id, total, digest_hex)
                )
                seen_paths.add(virtual_path)
        stored_selected = frozenset().union(
            *(
                self.store.scoped_paths(f"{source}-{role}")
                for source in DATABASES
                for role in ROLES
            )
        )
        if seen_paths != stored_selected:
            raise CaptureSeamError("capture_inventory_invalid")
        return tuple(projected)

    def _payload(
        self, files: tuple[SelectedFile, ...], controls: tuple[tuple[str, str], ...]
    ) -> bytes:
        return json.dumps(
            [
                "amplifai-capture-receipt-v1",
                self.admission.owner_id,
                self.admission.phone_id,
                self.admission.attempt_id,
                self.admission.action_id,
                self.admission.collection_receipt_id,
                self.admission.consent_receipt_id,
                self.admission.disclosure_version,
                self.session_nonce,
                [(f.category, f.role, f.file_id, f.size, f.sha256) for f in files],
                controls,
            ],
            separators=(",", ":"),
        ).encode("ascii")

    def seal(self, *, authority: LocalKeyAuthority | None = None) -> CaptureReceipt:
        self._check()
        if not self._completed:
            raise CaptureSeamError("capture_not_completed")
        if self._completion_snapshot != self.store.snapshot():
            raise CaptureSeamError("capture_source_mutated")
        files = self._project(authority)
        control_paths = self.store.scoped_paths("control")
        expected_control_paths = {
            f"{self.admission.phone_id}/{name}" for name in CONTROLS
        }
        if not control_paths <= expected_control_paths:
            raise CaptureSeamError("capture_control_invalid")
        if not REQUIRED_CONTROLS.issubset({Path(path).name for path in control_paths}):
            raise CaptureSeamError("capture_control_invalid")
        controls = []
        for name in sorted(CONTROLS):
            if f"{self.admission.phone_id}/{name}" not in control_paths:
                continue
            digest = hashlib.sha256()
            for chunk in self._control_chunks(name):
                digest.update(chunk)
            controls.append((name, digest.hexdigest()))
        controls = tuple(controls)
        mac = hmac.new(
            self.receipt_key, self._payload(files, controls), hashlib.sha256
        ).hexdigest()
        return CaptureReceipt(
            self.admission.attempt_id, self.session_nonce, files, controls, mac
        )

    def verify_receipt(
        self, receipt: CaptureReceipt, *, authority: LocalKeyAuthority | None = None
    ) -> None:
        self._check()
        if (
            not isinstance(receipt, CaptureReceipt)
            or receipt.attempt_id != self.admission.attempt_id
            or receipt.session_nonce != self.session_nonce
            or not isinstance(receipt.mac, str)
            or len(receipt.mac) != 64
        ):
            raise CaptureSeamError("capture_receipt_invalid")
        current = self.seal(authority=authority)
        if (
            not hmac.compare_digest(current.mac, receipt.mac)
            or receipt.files != current.files
            or receipt.control_sha256 != current.control_sha256
        ):
            raise CaptureSeamError("capture_receipt_invalid")

    def source_reader(
        self,
        receipt: CaptureReceipt,
        file: SelectedFile,
        *,
        authority: LocalKeyAuthority | None = None,
    ) -> SelectedSourceReader:
        self.verify_receipt(receipt, authority=authority)
        if file not in receipt.files:
            raise CaptureSeamError("capture_source_unapproved")
        self._check(file.category)
        path = f"{self.admission.phone_id}/{file.file_id[:2]}/{file.file_id}"
        return SelectedSourceReader(self.store, path)
