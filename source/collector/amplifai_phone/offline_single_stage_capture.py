"""Offline provisional DeviceLink groups in their final object namespace.

This module is deliberately outside the released collector. A caller supplies
an authenticated gateway port and a disposable ciphertext transport; neither
credentials nor Apple keys cross either port.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .offline_cloud_capture import (
    CaptureAdmission,
    CaptureReceipt,
    CaptureSeamError,
    EncryptedAttemptBackend,
    OfflineCloudCaptureLink,
)
from .offline_group_upload_ledger import GroupUploadLedger
from .offline_grouped_object_vfs import (
    GROUP_CHUNKS,
    MAX_GROUP_CIPHERTEXT,
    GroupedWalSet,
    RangeReply,
    seal_grouped_wal_set,
    seal_prepared_grouped_index,
)
from .offline_object_vfs import CHUNK_BYTES, ObjectVfsError, SourceBinding, chunk_aad
from .virtual_source_store import Chunk
from .workspace import SessionWorkspace

CAPTURE_MAC = re.compile(r"^[0-9A-Fa-f]{12}$")
EMPTY_DESCRIPTOR_SHA256 = hashlib.sha256(b"").hexdigest()
PROCESSING_CIPHER_CACHE_BYTES = 160 * 1024 * 1024


@dataclass(frozen=True)
class ExactGrant:
    object_key: str
    cipher_bytes: int
    cipher_sha256: str
    expires_at: float
    upload_url: str | None = None


class AttemptCipherCache:
    """One bounded encrypted-group LRU shared by every reader of one admission."""

    def __init__(
        self,
        admission: CaptureAdmission,
        budget_bytes: int = PROCESSING_CIPHER_CACHE_BYTES,
    ) -> None:
        if (
            type(budget_bytes) is not int
            or budget_bytes < MAX_GROUP_CIPHERTEXT
            or budget_bytes > PROCESSING_CIPHER_CACHE_BYTES
        ):
            raise ObjectVfsError("cloud_processing_cache_budget_invalid")
        self.admission = admission
        self.budget_bytes = budget_bytes
        self._groups: OrderedDict[
            tuple[CaptureAdmission, int, str, str],
            tuple[str, int, str, bytes, float, float],
        ] = OrderedDict()
        self._lock = threading.RLock()
        self._fetch_slots = threading.BoundedSemaphore(3)
        self.current_bytes = 0
        self.peak_bytes = 0

    def fetch_slot(self) -> threading.BoundedSemaphore:
        """At most three in-flight <=1 MiB group responses per attempt."""
        return self._fetch_slots

    def get(
        self,
        key: tuple[CaptureAdmission, int, str, str],
        now: float,
    ) -> tuple[str, int, str, bytes, float, float] | None:
        if key[0] != self.admission:
            raise ObjectVfsError("cloud_processing_cache_attempt_changed")
        with self._lock:
            entry = self._groups.get(key)
            if entry is None:
                return None
            if now >= entry[4] or time.time() + 5 >= entry[5]:
                self.current_bytes -= len(entry[3])
                del self._groups[key]
                return None
            self._groups.move_to_end(key)
            return entry

    def put(
        self,
        key: tuple[CaptureAdmission, int, str, str],
        entry: tuple[str, int, str, bytes, float, float],
    ) -> None:
        if (
            key[0] != self.admission
            or len(entry[3]) != entry[1]
            or (not 1 <= entry[1] <= MAX_GROUP_CIPHERTEXT)
        ):
            raise ObjectVfsError("cloud_processing_cache_binding")
        if entry[4] <= time.monotonic() or entry[5] <= time.time() + 5:
            return
        with self._lock:
            old = self._groups.pop(key, None)
            if old is not None:
                self.current_bytes -= len(old[3])
            while self._groups and self.current_bytes + entry[1] > self.budget_bytes:
                _, removed = self._groups.popitem(last=False)
                self.current_bytes -= len(removed[3])
            self._groups[key] = entry
            self.current_bytes += entry[1]
            self.peak_bytes = max(self.peak_bytes, self.current_bytes)

    def clear(self) -> None:
        with self._lock:
            self._groups.clear()
            self.current_bytes = 0


class AuthenticatedGateway(Protocol):
    """Server-owned account/session checks; native IDs are assertions only."""

    def start(
        self,
        admission: CaptureAdmission,
        challenge: str,
        claim_sha256: str,
        capture_mac: str,
        session_nonce: str,
    ) -> None: ...

    def reserve(
        self,
        admission: CaptureAdmission,
        category: str,
        role: str,
        group_index: int,
        group_id: str,
        first_nonce: str,
        cipher_bytes: int,
        cipher_sha256: str,
    ) -> str: ...

    def grant(
        self,
        admission: CaptureAdmission,
        category: str,
        role: str,
        group_index: int,
        group_id: str,
    ) -> ExactGrant: ...

    def verify(
        self,
        admission: CaptureAdmission,
        object_key: str,
        cipher_bytes: int,
        cipher_sha256: str,
    ) -> None: ...

    def terminal(
        self,
        admission: CaptureAdmission,
        challenge: str,
        claim_sha256: str,
        source_set_sha256: str,
        inventory_sha256: str,
    ) -> None: ...

    def dispose(
        self,
        admission: CaptureAdmission,
        category: str,
        disposition: str,
        group_count: int,
        cipher_bytes: int,
        descriptor_sha256: str,
    ) -> None: ...

    def capture_ambiguity(
        self,
        admission: CaptureAdmission,
        category: str,
        reason: str,
        event_count: int,
        generation_count: int,
        evidence_sha256: str,
        group_count: int,
        descriptor_sha256: str,
    ) -> None: ...

    def seal(self, admission: CaptureAdmission) -> None: ...

    def processing_group(
        self,
        admission: CaptureAdmission,
        epoch: int,
        category: str,
        group_id: str,
    ) -> tuple[str, int, str]: ...

    def revoke(self, admission: CaptureAdmission) -> None: ...

    def cleanup(
        self, admission: CaptureAdmission, objects: CipherTransport
    ) -> bool: ...


class CipherTransport(Protocol):
    """Exact-key write/readback; connected transport must bound HTTP reads."""

    def put_exact(self, key: str, payload: bytes, digest: str) -> None: ...

    def get_exact(self, key: str) -> bytes: ...

    def get_range(
        self,
        key: str,
        start: int,
        end: int,
        max_bytes: int,
    ) -> RangeReply: ...


@dataclass(frozen=True)
class SingleStageSource:
    category: str
    source_set: GroupedWalSet
    main_index: Path
    wal_index: Path


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")
    ).hexdigest()


def _retry_exact(call):
    for attempt in range(2):
        try:
            return call()
        except (OSError, TimeoutError):
            if attempt:
                raise
    raise AssertionError("unreachable")


def _file_descriptors(
    chunks: tuple[Chunk, ...],
    ledger: GroupUploadLedger,
    role: str,
) -> Iterator[tuple[object, ...]]:
    for chunk in chunks:
        group_id = chunk.key.split("/")[2]
        _seq, recorded_role, _size, _digest_value, descriptors, _key, state = (
            ledger.group(group_id)
        )
        if recorded_role != role or state != "verified":
            raise ObjectVfsError("cloud_upload_group_changed")
        yield from descriptors


class ProvisionalGroupBackend:
    """One 1 MiB virtual chunk becomes one final 8-part GCM group."""

    # The authenticated gateway verifies exact provider bytes before put returns.
    put_verifies_readback = True

    def __init__(
        self,
        workspace: SessionWorkspace,
        admission: CaptureAdmission,
        gateway: AuthenticatedGateway,
        objects: CipherTransport,
        object_key: bytes,
        receipt_key: bytes,
        index_root: Path,
    ) -> None:
        if (
            "contacts" not in admission.categories
            or not isinstance(object_key, bytes)
            or len(object_key) != 32
            or not isinstance(receipt_key, bytes)
            or len(receipt_key) != 32
            or not index_root.is_dir()
            or index_root.is_symlink()
            or stat.S_IMODE(index_root.stat().st_mode) != 0o700
        ):
            raise CaptureSeamError("capture_backend_invalid")
        self.control = EncryptedAttemptBackend(workspace, admission, object_key, None)
        self.admission = admission
        self.gateway = gateway
        self.objects = objects
        self.object_key = object_key
        self.receipt_key = receipt_key
        self.index_root = index_root
        self.cipher = AESGCM(object_key)
        self.bindings: dict[str, SourceBinding] = {}
        self.ledgers: dict[str, GroupUploadLedger] = {}
        self.next_group: dict[str, int] = {}
        self.revoked = False
        self.max_input_bytes = 0
        self.max_sealed_bytes = 0
        self.max_buffered_bytes = 0
        self.ciphertext_high_water_bytes = 0
        self.committed_ciphertext_bytes = 0
        self.native_groups = 0

    @property
    def local_control_bytes(self) -> int:
        return self.control.local_control_bytes

    def bind(self, session_nonce: str, start_claim_sha256: str) -> None:
        if self.bindings or self.revoked:
            raise CaptureSeamError("capture_backend_invalid")
        for category in self.admission.categories:
            source_set = str(
                uuid.uuid5(
                    uuid.UUID(self.admission.attempt_id),
                    f"single-stage-v1:{category}:{session_nonce}:{self.admission.consent_receipt_id}",
                )
            )
            self.bindings[category] = SourceBinding(
                self.admission.owner_id,
                self.admission.phone_id,
                self.admission.attempt_id,
                category,
                source_set,
            )
            self.ledgers[category] = GroupUploadLedger(
                self.index_root / f"{category}.ledger.sqlite3",
                (
                    self.admission.owner_id,
                    self.admission.phone_id,
                    self.admission.attempt_id,
                ),
                source_set,
                start_claim_sha256,
                self.admission.consent_receipt_id,
                self.object_key,
                self.receipt_key,
            )

    @staticmethod
    def _parts(key: str) -> tuple[str, str]:
        parts = key.split("/") if isinstance(key, str) else []
        if len(parts) != 3 or len(parts[2]) != 32:
            raise OSError("Invalid provisional virtual key")
        if parts[1] == "control":
            return "control", parts[2]
        category, separator, role = parts[1].partition("-")
        if separator != "-" or role not in ("main", "wal", "shm"):
            raise OSError("Invalid provisional source role")
        uuid.UUID(hex=parts[2])
        return category, role

    def put(self, key: str, payload: bytes) -> None:
        category, role = self._parts(key)
        if category == "control":
            self.control.put(key, payload)
            return
        if (
            self.revoked
            or category not in self.bindings
            or role not in ("main", "wal", "shm")
            or not payload
            or len(payload) > GROUP_CHUNKS * CHUNK_BYTES
        ):
            raise OSError("Provisional source is unavailable")
        group_index = self.next_group.get(f"{category}-{role}", 0)
        binding = self.bindings[category]
        group_id = key.split("/")[2]
        group = bytearray()
        entries: list[tuple[object, ...]] = []
        self.max_input_bytes = max(self.max_input_bytes, len(payload))
        for offset in range(0, len(payload), CHUNK_BYTES):
            part = payload[offset : offset + CHUNK_BYTES]
            index = group_index * GROUP_CHUNKS + len(entries)
            nonce = os.urandom(12)
            sealed = self.cipher.encrypt(
                nonce, part, chunk_aad(binding, role, index, len(part))
            )
            entries.append(
                (
                    index,
                    group_id,
                    0,
                    len(group),
                    len(sealed),
                    nonce.hex(),
                    len(part),
                    hashlib.sha256(part).hexdigest(),
                    hashlib.sha256(sealed).hexdigest(),
                )
            )
            group.extend(sealed)
        total = len(group)
        entries = [(*entry[:2], total, *entry[3:]) for entry in entries]
        ciphertext = bytes(group)
        digest = hashlib.sha256(ciphertext).hexdigest()
        ledger = self.ledgers[category]
        seq = ledger.group_count
        ledger.prepare(seq, role, group_id, total, digest, tuple(entries))
        self.max_sealed_bytes = max(self.max_sealed_bytes, total)
        self.max_buffered_bytes = max(self.max_buffered_bytes, len(payload) + total)
        self.ciphertext_high_water_bytes = max(
            self.ciphertext_high_water_bytes, self.max_buffered_bytes
        )
        group_uuid = str(uuid.UUID(hex=group_id))
        key_on_server = _retry_exact(
            lambda: self.gateway.reserve(
                self.admission,
                category,
                role,
                group_index,
                group_uuid,
                str(entries[0][5]),
                total,
                digest,
            )
        )
        ledger.reserved(seq, group_id, key_on_server)
        grant = _retry_exact(
            lambda: self.gateway.grant(
                self.admission,
                category,
                role,
                group_index,
                group_uuid,
            )
        )
        if (
            grant.object_key != key_on_server
            or grant.cipher_bytes != total
            or grant.cipher_sha256 != digest
        ):
            raise ObjectVfsError("cloud_grant_changed")
        if grant.upload_url is None:
            _retry_exact(
                lambda: self.objects.put_exact(key_on_server, ciphertext, digest)
            )
        else:
            upload = getattr(self.objects, "put_granted", None)
            if upload is None:
                raise ObjectVfsError("cloud_signed_transport_missing")
            _retry_exact(lambda: upload(grant, ciphertext))
        _retry_exact(
            lambda: self.gateway.verify(
                self.admission,
                key_on_server,
                total,
                digest,
            )
        )
        ledger.verified(seq)
        self.next_group[f"{category}-{role}"] = group_index + 1
        self.native_groups += 1
        self.committed_ciphertext_bytes += total
        self.ciphertext_high_water_bytes = max(
            self.ciphertext_high_water_bytes, self.committed_ciphertext_bytes
        )

    def get(self, key: str) -> bytes:
        category, role = self._parts(key)
        if category == "control":
            return self.control.get(key)
        if self.revoked or category not in self.ledgers:
            raise OSError("Provisional source is unavailable")
        group_id = key.split("/")[2]
        _seq, recorded_role, size, digest, entries, object_key, state = self.ledgers[
            category
        ].group(group_id)
        if recorded_role != role or state != "verified" or object_key is None:
            raise OSError("Provisional group is unavailable")
        sealed = self.objects.get_exact(object_key)
        if len(sealed) != size or hashlib.sha256(sealed).hexdigest() != digest:
            raise OSError("Provisional group changed")
        output = bytearray()
        for entry in entries:
            index, _, total, start, length, nonce, plain_len, plain_sha, cipher_sha = (
                entry
            )
            if total != size or start + length > size:
                raise OSError("Provisional descriptor changed")
            part = sealed[start : start + length]
            if hashlib.sha256(part).hexdigest() != cipher_sha:
                raise OSError("Provisional ciphertext changed")
            try:
                plain = self.cipher.decrypt(
                    bytes.fromhex(nonce),
                    part,
                    chunk_aad(self.bindings[category], role, index, plain_len),
                )
            except Exception as error:
                raise OSError("Provisional binding changed") from error
            if (
                len(plain) != plain_len
                or hashlib.sha256(plain).hexdigest() != plain_sha
            ):
                raise OSError("Provisional plaintext changed")
            output.extend(plain)
        return bytes(output)

    def delete(self, key: str) -> None:
        category, _role = self._parts(key)
        if category == "control":
            self.control.delete(key)
            return
        if not self.revoked:
            self.revoked = True
            for ledger in self.ledgers.values():
                ledger.mark_erasure_pending()
            self.gateway.revoke(self.admission)

    def retire(self, key: str) -> bool:
        """Keep superseded optional ciphertext in the exact cleanup inventory."""
        category, role = self._parts(key)
        if category not in ("calls", "messages") or role == "control":
            return False
        row = self.ledgers[category].group(key.split("/")[2])
        if row[1] != role or row[6] != "verified" or row[5] is None:
            raise OSError("Superseded group was not verified")
        return True

    def descriptor_digest(self, category: str) -> tuple[int, int, str]:
        count = total_bytes = 0
        expected_index = {"main": 0, "wal": 0, "shm": 0}
        chain = b""
        for (
            _seq,
            role,
            group_id,
            size,
            digest,
            entries,
            object_key,
            state,
        ) in self.ledgers[category].iter_groups(order_by_role=True):
            if state != "verified" or object_key is None:
                raise ObjectVfsError("cloud_upload_groups_incomplete")
            index = entries[0][0] // GROUP_CHUNKS
            if index != expected_index[role]:
                raise ObjectVfsError("cloud_upload_group_gap")
            expected_index[role] += 1
            row = (
                role,
                index,
                str(uuid.UUID(hex=group_id)),
                entries[0][5],
                size,
                digest,
                object_key,
            )
            chain = hashlib.sha256(
                chain + "|".join(map(str, row)).encode("utf-8")
            ).digest()
            count += 1
            total_bytes += size
        return count, total_bytes, hashlib.sha256(chain).hexdigest()


class SingleStageCaptureLink(OfflineCloudCaptureLink):
    """DeviceLink dispatch with provisional writes and post-terminal source seal."""

    def __init__(
        self,
        service,
        workspace: SessionWorkspace,
        watchdog,
        admission: CaptureAdmission,
        assert_current,
        object_key: bytes,
        receipt_key: bytes,
        gateway: AuthenticatedGateway,
        objects: CipherTransport,
        index_root: Path,
        start_challenge: str,
        terminal_challenge: str,
        capture_mac: str,
    ) -> None:
        if not CAPTURE_MAC.fullmatch(capture_mac) or not all(
            isinstance(value, str) and str(uuid.UUID(value)) == value
            for value in (start_challenge, terminal_challenge)
        ):
            raise CaptureSeamError("capture_challenge_invalid")
        backend = ProvisionalGroupBackend(
            workspace, admission, gateway, objects, object_key, receipt_key, index_root
        )
        super().__init__(
            service,
            workspace,
            watchdog,
            admission,
            assert_current,
            object_key,
            receipt_key,
            None,
            selected_backend=backend,
        )
        self.required_categories = frozenset({"contacts"})
        self.gateway = gateway
        self.objects = objects
        self.index_root = index_root
        self.start_challenge = start_challenge
        self.terminal_challenge = terminal_challenge
        self.capture_mac = capture_mac
        self._seen_selected: set[str] = set()
        self._generations: dict[str, int] = {}
        self._pending_files: dict[str, tuple[int, int]] = {}
        self._completion_evidence: str | None = None
        self.capture_unavailable_reasons: dict[str, str] = {}
        self._started = False
        self._sealed = False
        self.processing_cache = AttemptCipherCache(admission)

    def _scope(self, device_name: str, file_name: str) -> str:
        scope = super()._scope(device_name, file_name)
        if scope not in ("control", "discard"):
            category, role = scope.split("-")
            if category == "contacts" and (
                scope in self._seen_selected or role == "shm"
            ):
                raise CaptureSeamError("capture_replacement_requires_new_attempt")
            if scope in self._seen_selected:
                self.capture_unavailable_reasons[category] = "optional_capture_changed"
            if role == "shm":
                self.capture_unavailable_reasons[category] = "optional_shm_unqualified"
            generation = self._generations.get(scope, 0) + 1
            self._generations[scope] = generation
            first_group = self.backend.ledgers[category].group_count
            self.backend.ledgers[category].append_capture_event(
                role,
                generation,
                "start",
                first_group,
                first_group,
                hashlib.sha256(file_name.encode("utf-8")).hexdigest(),
            )
            self._pending_files[scope] = (generation, first_group)
            self._seen_selected.add(scope)
        return scope

    def _after_file_commit(self, scope: str, file_name: str) -> None:
        if scope in ("control", "discard"):
            return
        category, role = scope.split("-")
        generation, first_group = self._pending_files.pop(scope)
        self.backend.ledgers[category].append_capture_event(
            role,
            generation,
            "complete",
            first_group,
            self.backend.ledgers[category].group_count,
            hashlib.sha256(file_name.encode("utf-8")).hexdigest(),
            self.store.file_size(file_name),
            self.store.file_sha256(file_name),
        )

    async def remove_items(self, message) -> None:
        for value in message[1]:
            path = self.store._path(value)
            for stored_path, record in tuple(self.store._files.items()):
                if stored_path != path and path not in stored_path.parents:
                    continue
                if record.scope in ("control", "discard"):
                    continue
                category, role = record.scope.split("-")
                if category == "contacts":
                    raise CaptureSeamError("capture_required_source_changed")
                self.capture_unavailable_reasons[category] = "optional_capture_changed"
                generation = self._generations[record.scope]
                count = self.backend.ledgers[category].group_count
                self.backend.ledgers[category].append_capture_event(
                    role,
                    generation,
                    "delete",
                    count,
                    count,
                    hashlib.sha256(str(stored_path).encode("utf-8")).hexdigest(),
                    record.size,
                    record.sha256,
                )
        await super().remove_items(message)

    async def run_capture(self) -> None:
        claim = _digest(
            (
                self.admission.owner_id,
                self.admission.phone_id,
                self.admission.attempt_id,
                self.admission.action_id,
                self.admission.collection_receipt_id,
                self.admission.consent_receipt_id,
                self.admission.disclosure_version,
                self.admission.categories,
                self.start_challenge,
                self.capture_mac,
                self.session_nonce,
            )
        )
        try:
            _retry_exact(
                lambda: self.gateway.start(
                    self.admission,
                    self.start_challenge,
                    claim,
                    self.capture_mac,
                    self.session_nonce,
                )
            )
            self._started = True
            self.backend.bind(self.session_nonce, claim)
            await super().run_capture()
            self._completion_evidence = self._evidence_digest()
        except BaseException:
            self.processing_cache.clear()
            if self._started:
                self.backend.revoked = True
                self.gateway.revoke(self.admission)
            raise

    def finalize(
        self, *, authority=None
    ) -> tuple[CaptureReceipt, dict[str, SingleStageSource]]:
        try:
            return self._finalize(authority=authority)
        except BaseException:
            self.processing_cache.clear()
            if self._started and not self._sealed:
                self.backend.revoked = True
                self.gateway.revoke(self.admission)
            raise

    def _finalize(
        self, *, authority=None
    ) -> tuple[CaptureReceipt, dict[str, SingleStageSource]]:
        if (
            self._sealed
            or self.backend.revoked
            or not self._started
            or self._completion_evidence is None
            or self._completion_evidence != self._evidence_digest()
        ):
            raise CaptureSeamError("capture_seal_denied")
        self.defer_source_readback = True
        try:
            receipt = self.seal(authority=authority)
        finally:
            self.defer_source_readback = False
        selected = sorted(
            (file.category, file.role, file.size, file.sha256) for file in receipt.files
        )
        source_set_sha = _digest(selected)
        inventory_sha = _digest(
            (
                receipt.mac,
                self.store.snapshot(),
                receipt.control_sha256,
                self._completion_evidence,
            )
        )
        terminal_claim = _digest(
            (
                self.admission.attempt_id,
                self.terminal_challenge,
                receipt.mac,
                source_set_sha,
                inventory_sha,
            )
        )
        _retry_exact(
            lambda: self.gateway.terminal(
                self.admission,
                self.terminal_challenge,
                terminal_claim,
                source_set_sha,
                inventory_sha,
            )
        )
        # The second projection is a post-terminal full authenticated readback.
        self.verify_receipt(receipt, authority=authority)
        completed: dict[str, SingleStageSource] = {}
        for category in self.admission.categories:
            files = {
                file.role: file for file in receipt.files if file.category == category
            }
            count, cipher_bytes, digest = self.backend.descriptor_digest(category)
            if category in self.capture_unavailable_reasons:
                reason = self.capture_unavailable_reasons[category]
                if category == "contacts" or reason not in (
                    "optional_capture_changed",
                    "optional_shm_unqualified",
                ):
                    raise CaptureSeamError("capture_source_incomplete")
                events, generations, evidence_sha = self.backend.ledgers[
                    category
                ].capture_evidence()
                _retry_exact(
                    partial(
                        self.gateway.capture_ambiguity,
                        self.admission,
                        category,
                        reason,
                        events,
                        generations,
                        evidence_sha,
                        count,
                        digest,
                    )
                )
                _retry_exact(
                    partial(
                        self.gateway.dispose,
                        self.admission,
                        category,
                        "unavailable",
                        count,
                        cipher_bytes,
                        digest,
                    )
                )
                continue
            if not files:
                if category == "contacts" or count:
                    raise CaptureSeamError("capture_source_incomplete")
                _retry_exact(
                    partial(
                        self.gateway.dispose,
                        self.admission,
                        category,
                        "unavailable",
                        0,
                        0,
                        EMPTY_DESCRIPTOR_SHA256,
                    )
                )
                self.capture_unavailable_reasons[category] = "optional_source_missing"
                continue
            if set(files) != {"main", "wal"} or not count:
                if category != "contacts" and set(files) <= {"main", "wal"}:
                    reason = (
                        "optional_sidecar_missing"
                        if set(files) != {"main", "wal"}
                        else "optional_source_empty"
                    )
                    _retry_exact(
                        partial(
                            self.gateway.dispose,
                            self.admission,
                            category,
                            "unavailable",
                            count,
                            cipher_bytes,
                            digest,
                        )
                    )
                    self.capture_unavailable_reasons[category] = reason
                    continue
                raise CaptureSeamError("capture_sidecar_unqualified")
            indexes = {
                role: self.index_root / f"{category}-{role}.index"
                for role in ("main", "wal")
            }
            grouped = {}
            for role in ("main", "wal"):
                file = files[role]
                path = f"{self.admission.phone_id}/{file.file_id[:2]}/{file.file_id}"
                chunks = self.store.file_chunks(path)
                grouped[role] = seal_prepared_grouped_index(
                    _file_descriptors(chunks, self.backend.ledgers[category], role),
                    indexes[role],
                    self.backend.bindings[category],
                    role,
                    file.size,
                    file.sha256,
                    self.receipt_key,
                    sidecars=("wal",) if role == "main" else (),
                )
            source_set = seal_grouped_wal_set(
                grouped["main"], grouped["wal"], self.receipt_key
            )
            _retry_exact(
                partial(
                    self.gateway.dispose,
                    self.admission,
                    category,
                    "sealed",
                    count,
                    cipher_bytes,
                    digest,
                )
            )
            completed[category] = SingleStageSource(
                category, source_set, indexes["main"], indexes["wal"]
            )
        self.gateway.seal(self.admission)
        self._sealed = True
        for ledger in self.backend.ledgers.values():
            ledger.finish()
        return receipt, completed

    def _evidence_digest(self) -> str:
        return _digest(
            tuple(
                (category, *self.backend.ledgers[category].capture_evidence())
                for category in self.admission.categories
            )
        )

    def _payload(self, files, controls) -> bytes:
        return super()._payload(files, controls) + self._evidence_digest().encode(
            "ascii"
        )

    def retry_cleanup(self) -> bool:
        if not self._started:
            return True
        self.processing_cache.clear()
        self.backend.revoked = True
        self.gateway.revoke(self.admission)
        return self.gateway.cleanup(self.admission, self.objects)

    @staticmethod
    def abandon_interrupted(
        admission: CaptureAdmission,
        gateway: AuthenticatedGateway,
        objects: CipherTransport,
    ) -> bool:
        """No source/key authority survives a process restart; revoke and sweep."""
        gateway.revoke(admission)
        return gateway.cleanup(admission, objects)


class ProcessingRanges:
    def __init__(
        self,
        link: SingleStageCaptureLink,
        category: str,
        epoch: int,
    ) -> None:
        self.link = link
        self.category = category
        self.epoch = epoch
        if category not in link.admission.categories:
            raise ObjectVfsError("cloud_processing_category_denied")
        cache = getattr(link, "processing_cache", None)
        if cache is None:
            cache = AttemptCipherCache(link.admission)
            link.processing_cache = cache
        if (
            not isinstance(cache, AttemptCipherCache)
            or cache.admission != link.admission
        ):
            raise ObjectVfsError("cloud_processing_cache_attempt_changed")
        self.cache = cache

    @property
    def peak_group_cache_bytes(self) -> int:
        return self.cache.peak_bytes

    def get_group_range(
        self,
        group_id: str,
        start: int,
        end: int,
        total: int,
        max_bytes: int,
    ) -> RangeReply:
        if self.link.backend.revoked:
            self.cache.clear()
            raise ObjectVfsError("cloud_processing_revoked")
        if (
            type(total) is not int
            or type(start) is not int
            or type(end) is not int
            or type(max_bytes) is not int
            or not 0 <= start <= end < total <= MAX_GROUP_CIPHERTEXT
            or end - start + 1 > max_bytes
        ):
            raise ObjectVfsError("cloud_processing_range_invalid")
        identity = (self.link.admission, self.epoch, self.category, group_id)
        cached = self.cache.get(identity, time.monotonic())
        if cached is not None:
            key, size, digest = self._authorized_group(group_id, total)
            if (key, size, digest) != cached[:3]:
                self.cache.clear()
                raise ObjectVfsError("cloud_processing_binding")
            body = cached[3][start : end + 1]
            return RangeReply(206, f"bytes {start}-{end}/{total}", len(body), body)
        with self.cache.fetch_slot():
            key, size, digest = self._authorized_group(group_id, total)
            cached = self.cache.get(identity, time.monotonic())
            if cached is not None:
                if (key, size, digest) != cached[:3]:
                    self.cache.clear()
                    raise ObjectVfsError("cloud_processing_binding")
                body = cached[3][start : end + 1]
                return RangeReply(206, f"bytes {start}-{end}/{total}", len(body), body)
            try:
                reply = self.link.objects.get_range(
                    key, 0, size - 1, MAX_GROUP_CIPHERTEXT
                )
            except BaseException:
                self.cache.clear()
                raise
            if (
                reply.status != 206
                or reply.content_range != f"bytes 0-{size - 1}/{size}"
                or reply.content_length != size
                or len(reply.body) != size
                or hashlib.sha256(reply.body).hexdigest() != digest
            ):
                self.cache.clear()
                raise ObjectVfsError("cloud_processing_group_integrity")
            if self.link.backend.revoked:
                self.cache.clear()
                raise ObjectVfsError("cloud_processing_revoked")
            current = self._authorized_group(group_id, total)
            if current != (key, size, digest):
                self.cache.clear()
                raise ObjectVfsError("cloud_processing_binding")
            if (
                reply.grant_expires_at is None
                or not time.time() < reply.grant_expires_at <= time.time() + 31
            ):
                self.cache.clear()
                raise ObjectVfsError("cloud_processing_read_grant_expired")
            if reply.grant_expires_at <= time.time() + 5:
                body = reply.body[start : end + 1]
                return RangeReply(206, f"bytes {start}-{end}/{total}", len(body), body)
            cache_seconds = min(25, reply.grant_expires_at - time.time() - 5)
            self.cache.put(
                identity,
                (
                    key,
                    size,
                    digest,
                    reply.body,
                    time.monotonic() + cache_seconds,
                    reply.grant_expires_at,
                ),
            )
            body = reply.body[start : end + 1]
            return RangeReply(206, f"bytes {start}-{end}/{total}", len(body), body)

    def _authorized_group(
        self, group_id: str, total: int | None = None
    ) -> tuple[str, int, str]:
        try:
            if self.link.backend.revoked:
                raise ObjectVfsError("cloud_processing_revoked")
            key, size, digest = self.link.gateway.processing_group(
                self.link.admission, self.epoch, self.category, group_id
            )
            if self.link.backend.revoked:
                raise ObjectVfsError("cloud_processing_revoked")
            row = self.link.backend.ledgers[self.category].group(group_id)
            if (
                row[2] != size
                or row[3] != digest
                or row[5] != key
                or row[6] != "verified"
                or (total is not None and total != size)
            ):
                raise ObjectVfsError("cloud_processing_binding")
            return key, size, digest
        except BaseException:
            self.cache.clear()
            raise

    def get_range(
        self,
        group_id: str,
        start: int,
        end: int,
        max_bytes: int,
    ) -> RangeReply:
        if self.link.backend.revoked:
            self.cache.clear()
            raise ObjectVfsError("cloud_processing_revoked")
        key, size, _digest = self._authorized_group(group_id)
        if end >= size or max_bytes > MAX_GROUP_CIPHERTEXT:
            raise ObjectVfsError("cloud_processing_binding")
        return self.link.objects.get_range(key, start, end, max_bytes)
