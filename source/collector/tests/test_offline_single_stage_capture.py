"""Changed-seam synthetic provisional capture; no provider or product import."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import stat
import struct
import time
import unittest
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import apsw
import test_offline_cloud_capture as capture_fixture
from amplifai_phone.backup_watchdog import BackupWatchdog
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN
from amplifai_phone.metadata import (
    Interaction,
    SelectedPayloadIntegrityError,
    UnsupportedSchema,
    read_contacts,
)
from amplifai_phone.offline_capture_object_store import SyntheticCaptureObjects
from amplifai_phone.offline_cloud_capture import CaptureAdmission, CaptureSeamError
from amplifai_phone.offline_cloud_optional_processing import process_sealed_sources
from amplifai_phone.offline_grouped_object_vfs import (
    RangeReply,
    open_grouped_wal_source,
)
from amplifai_phone.offline_object_vfs import ObjectVfsError
from amplifai_phone.offline_single_stage_capture import (
    ExactGrant,
    ProcessingRanges,
    SingleStageCaptureLink,
)
from amplifai_phone.record_store import RecordStore
from amplifai_phone.virtual_source_store import VirtualStoreError
from amplifai_phone.workspace import SessionWorkspace, WorkspaceError


class FinalObjects(SyntheticCaptureObjects):
    def __init__(self, root: Path, attempt_id: str) -> None:
        super().__init__(root, attempt_id)
        self.put_calls = self.get_calls = self.range_calls = self.delete_calls = 0
        self.get_bytes = self.range_bytes = 0
        self.high_water = 0

    def _path(self, key: str) -> Path:
        parts = key.split("/") if isinstance(key, str) else []
        try:
            valid = (
                len(parts) == 6
                and parts[0] == "cloud-v1"
                and parts[1] == self.attempt_id
                and parts[2] in ("contacts", "calls", "messages")
                and parts[3] in ("main", "wal", "shm")
                and int(parts[4]) >= 0
                and str(uuid.UUID(parts[5])) == parts[5]
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise OSError("Final object key is outside the attempt")
        return self.root / hashlib.sha256(key.encode("ascii")).hexdigest()

    def put_exact(self, key: str, payload: bytes, digest: str) -> None:
        if hashlib.sha256(payload).hexdigest() != digest:
            raise OSError("Final object digest changed")
        self.put_calls += 1
        super().put_exact(key, payload)
        self.high_water = max(self.high_water, self.ciphertext_bytes)

    def get_exact(self, key: str) -> bytes:
        self.get_calls += 1
        payload = super().get_exact(key)
        self.get_bytes += len(payload)
        return payload

    def get_range(self, key: str, start: int, end: int, max_bytes: int) -> RangeReply:
        self.range_calls += 1
        if (
            type(start) is not int
            or type(end) is not int
            or start < 0
            or end < start
            or end - start + 1 > max_bytes
            or max_bytes > 1024 * 1024 + 128
        ):
            raise OSError("Range exceeds bounded object")
        payload = super().get_exact(key)
        if end >= len(payload):
            raise OSError("Range exceeds object")
        body = payload[start : end + 1]
        self.range_bytes += len(body)
        return RangeReply(
            206,
            f"bytes {start}-{end}/{len(payload)}",
            len(body),
            body,
            time.time() + 30,
        )

    def delete(self, key: str) -> None:
        self.delete_calls += 1
        super().delete(key)


class Journal:
    """Disposable state machine matching the reviewed Postgres procedure shape."""

    def __init__(self, admission, objects: FinalObjects) -> None:
        self.admission = admission
        self.objects = objects
        self.state = "admitted"
        self.start_challenge = str(uuid.uuid4())
        self.terminal_challenge = str(uuid.uuid4())
        self.start_claim = self.terminal_claim = None
        self.groups: dict[tuple[str, str, int], dict] = {}
        self.dispositions: dict[str, tuple] = {}
        self.processing_outcomes: dict[str, tuple] = {}
        self.ambiguities: dict[str, tuple] = {}
        self.now = time.time()
        self.grant_horizon = 0.0
        self.epoch = 0
        self.erased = False
        self.fail_delete_once = False
        self.gateway_calls = self.db_round_trips = 0

    def _call(self, admission, *states: str) -> None:
        self.gateway_calls += 1
        self.db_round_trips += 1
        if admission != self.admission or self.state not in states:
            raise ObjectVfsError("cloud_admission_closed")

    def start(self, admission, challenge, claim, capture_mac, nonce) -> None:
        self._call(admission, "admitted", "receiving")
        if challenge != self.start_challenge or not capture_mac or len(nonce) != 32:
            raise ObjectVfsError("cloud_start_denied")
        if self.state == "receiving" and self.start_claim != claim:
            raise ObjectVfsError("cloud_start_replay_changed")
        self.start_claim = claim
        self.state = "receiving"

    def reserve(
        self, admission, category, role, index, group_id, nonce, size, digest
    ) -> str:
        self._call(admission, "receiving")
        if category not in admission.categories or role not in ("main", "wal", "shm"):
            raise ObjectVfsError("cloud_source_unapproved")
        identity = (role, index, group_id, nonce, size, digest)
        position = (category, role, index)
        prior = self.groups.get(position)
        if prior:
            if prior["identity"] != identity:
                raise ObjectVfsError("cloud_group_replay_changed")
            return prior["key"]
        if index != sum(key[:2] == (category, role) for key in self.groups):
            raise ObjectVfsError("cloud_group_gap")
        key = f"cloud-v1/{admission.attempt_id}/{category}/{role}/{index}/{group_id}"
        self.groups[position] = {
            "identity": identity,
            "key": key,
            "verified": False,
        }
        return key

    def grant(self, admission, category, role, index, group_id) -> ExactGrant:
        self._call(admission, "receiving")
        row = self.groups[(category, role, index)]
        if row["identity"][2] != group_id or row["verified"]:
            raise ObjectVfsError("cloud_grant_denied")
        self.grant_horizon = max(self.grant_horizon, self.now + 120)
        return ExactGrant(
            row["key"], row["identity"][4], row["identity"][5], self.now + 60
        )

    def verify(self, admission, key, size, digest) -> None:
        self._call(admission, "receiving", "terminal")
        row = next((row for row in self.groups.values() if row["key"] == key), None)
        if row is None or (size, digest) != row["identity"][4:6]:
            raise ObjectVfsError("cloud_group_changed")
        payload = self.objects.get_exact(key)
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            raise ObjectVfsError("cloud_group_readback")
        row["verified"] = True

    def terminal(self, admission, challenge, claim, source_set, inventory) -> None:
        self._call(admission, "receiving", "terminal")
        if challenge != self.terminal_challenge or not source_set or not inventory:
            raise ObjectVfsError("cloud_terminal_denied")
        identity = (claim, source_set, inventory)
        if self.state == "terminal" and self.terminal_claim != identity:
            raise ObjectVfsError("cloud_terminal_replay_changed")
        self.terminal_claim = identity
        self.state = "terminal"

    def dispose(self, admission, category, disposition, count, size, digest) -> None:
        self._call(admission, "terminal")
        rows = [row for key, row in self.groups.items() if key[0] == category]
        chain = b""
        for row in sorted(rows, key=lambda value: value["identity"][:2]):
            role, index, group_id, nonce, length, sha = row["identity"]
            line = f"{role}|{index}|{group_id}|{nonce}|{length}|{sha}|{row['key']}"
            chain = hashlib.sha256(chain + line.encode()).digest()
        expected = hashlib.sha256(chain).hexdigest()
        if (
            count != len(rows)
            or size != sum(row["identity"][4] for row in rows)
            or digest != expected
            or not all(row["verified"] for row in rows)
            or (disposition == "sealed" and not rows)
            or (disposition == "declined" and bool(rows))
            or (disposition == "unavailable" and category == "contacts")
            or (
                disposition == "unavailable"
                and bool(rows)
                and {row["identity"][0] for row in rows} not in ({"main"}, {"wal"})
                and self.ambiguities.get(category, (None,))[-1] != digest
            )
        ):
            raise ObjectVfsError("cloud_disposition_incomplete")
        existing = self.dispositions.get(category)
        new = (disposition, count, size, digest)
        if existing is not None and existing != new:
            raise ObjectVfsError("cloud_disposition_replay_changed")
        self.dispositions[category] = new

    def capture_ambiguity(
        self,
        admission,
        category,
        reason,
        events,
        generations,
        evidence_sha,
        count,
        descriptor_sha,
    ) -> None:
        self._call(admission, "terminal")
        if (
            category not in ("calls", "messages")
            or reason not in ("optional_capture_changed", "optional_shm_unqualified")
            or events < 2
            or generations < 1
            or count < 1
            or len(evidence_sha) != 64
            or count != sum(key[0] == category for key in self.groups)
            or (
                reason == "optional_shm_unqualified"
                and not any(key[:2] == (category, "shm") for key in self.groups)
            )
            or (
                reason == "optional_capture_changed"
                and events == 2 * generations
                and generations < 3
            )
        ):
            raise ObjectVfsError("cloud_capture_ambiguity_denied")
        candidate = (reason, events, generations, evidence_sha, descriptor_sha)
        prior = self.ambiguities.get(category)
        if prior is not None and prior != candidate:
            raise ObjectVfsError("cloud_capture_ambiguity_changed")
        self.ambiguities[category] = candidate

    def seal(self, admission) -> None:
        self._call(admission, "terminal")
        if (
            set(self.dispositions) != set(admission.categories)
            or self.dispositions["contacts"][0] != "sealed"
        ):
            raise ObjectVfsError("cloud_sources_incomplete")
        self.state = "sealed"

    def begin_processing(self) -> int:
        self._call(self.admission, "sealed")
        self.state = "processing"
        self.epoch += 1
        return self.epoch

    def processing_group(self, admission, epoch, category, group_id):
        self._call(admission, "processing")
        if epoch != self.epoch:
            raise ObjectVfsError("cloud_worker_stale")
        row = next(
            (
                row
                for key, row in self.groups.items()
                if key[0] == category
                and row["identity"][2].replace("-", "") == group_id
            ),
            None,
        )
        if row is None:
            raise ObjectVfsError("cloud_group_missing")
        return row["key"], row["identity"][4], row["identity"][5]

    def processing_category(self, admission, epoch, category, disposition, descriptor):
        self._call(admission, "processing")
        recorded = self.dispositions.get(category)
        if (
            epoch != self.epoch
            or recorded is None
            or recorded[0] != disposition
            or recorded[3] != descriptor
        ):
            raise ObjectVfsError("cloud_processing_category_denied")

    def record_processing_outcome(
        self, admission, epoch, category, descriptor, coverage
    ):
        self.processing_category(
            admission, epoch, category, self.dispositions[category][0], descriptor
        )
        prior = self.processing_outcomes.get(category)
        candidate = (descriptor, coverage)
        if prior is not None:
            if prior != candidate:
                raise ObjectVfsError("cloud_processing_outcome_changed")
            return True
        self.processing_outcomes[category] = candidate
        return False

    def revoke(self, admission) -> None:
        self._call(
            admission,
            "admitted",
            "receiving",
            "terminal",
            "sealed",
            "processing",
            "revoked",
        )
        self.state = "revoked"

    def cleanup(self, admission, objects) -> bool:
        self._call(admission, "revoked")
        for row in self.groups.values():
            if self.fail_delete_once:
                self.fail_delete_once = False
                raise OSError("Synthetic cleanup interrupted")
            objects.delete(row["key"])
        if self.now < self.grant_horizon + 30:
            return False
        if any(objects._path(row["key"]).exists() for row in self.groups.values()):
            return False
        self.erased = True
        return True


class SingleStageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixture = capture_fixture.CaptureFixture(
            "test_real_dispatch_projection_readback_and_parser_parity"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.temporary.cleanup)
        self.addCleanup(self.fixture.tearDown)
        self.objects = FinalObjects(
            self.fixture.root / "single-final-objects",
            self.fixture.admission.attempt_id,
        )
        self.gateway = Journal(self.fixture.admission, self.objects)
        self.index_root = self.fixture.root / "private-index"
        self.index_root.mkdir(mode=0o700)
        self.assertEqual(stat.S_IMODE(self.index_root.stat().st_mode), 0o700)

    def link(self, files, *, terminal=True, operations=False, transcript=None):
        wire = capture_fixture.Wire(
            transcript
            if transcript is not None
            else self.fixture.transcript(
                files,
                terminal=terminal,
                operations=operations,
            )
        )
        workspace = SessionWorkspace(
            self.fixture.root / f"workspace-{uuid.uuid4().hex}"
        )
        workspace.__enter__()
        self.addCleanup(workspace.__exit__, None, None, None)
        watchdog = BackupWatchdog()
        watchdog.start()
        link = SingleStageCaptureLink(
            wire,
            workspace,
            watchdog,
            self.fixture.admission,
            self.fixture.authority,
            self.fixture.object_key,
            self.fixture.receipt_key,
            self.gateway,
            self.objects,
            self.index_root,
            self.gateway.start_challenge,
            self.gateway.terminal_challenge,
            "A1B2C3D4E5F6",
        )
        return link, wire

    def supported_files(self):
        files = self.fixture.inputs()
        manifest = self.fixture.root / "Manifest.db"
        with sqlite3.connect(manifest) as db:
            db.execute("DELETE FROM Files WHERE relativePath LIKE '%-shm'")
        return [
            (device, path, manifest.read_bytes() if device == "Manifest.db" else data)
            for device, path, data in files
            if not device.endswith("-shm")
        ]

    def changed_optional_transcript(
        self,
        files,
        *,
        category="calls",
        repeats=1,
        delete=False,
        bad_control=False,
        prior_bytes=1024 * 1024 + 100,
    ):
        target = next(
            (device, path)
            for device, path, _ in files
            if device == HOME_DOMAIN + "-" + DATABASES[category]
        )
        payload = bytearray()
        for directory in sorted({str(Path(name).parent) for _, name, _ in files}):
            payload.extend(
                capture_fixture.build_plist(["DLMessageCreateDirectory", directory])
            )
        payload.extend(capture_fixture.build_plist(["DLMessageUploadFiles", "", 7.0]))
        for index in range(repeats):
            # Large enough to produce a complete provisional group and a
            # smaller final group before the next generation replaces it.
            payload.extend(
                capture_fixture._file(*target, bytes([65 + index]) * prior_bytes)
            )
        for device, path, data in files:
            if bad_control and device == "Manifest.db":
                data = b"invalid final control"
            payload.extend(capture_fixture._file(device, path, data))
        payload.extend(struct.pack(">I", 0))
        for _ in range(int(delete)):
            payload.extend(
                capture_fixture.build_plist(
                    ["DLMessageRemoveItems", [target[1]], "", 10.0]
                )
            )
        payload.extend(
            capture_fixture.build_plist(["DLMessageProcessMessage", {"ErrorCode": 0}])
        )
        return bytes(payload)

    async def test_optional_replacement_generations_preserve_contacts_and_inventory(
        self,
    ):
        files = self.supported_files()
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files, repeats=2)
        )
        await link.run_capture()
        before = tuple(link.backend.ledgers["calls"].iter_groups())
        self.assertGreater(len(before), 2)
        self.assertEqual(len({row[2] for row in before}), len(before))
        self.assertEqual(
            set(link.backend.ledgers["calls"].object_keys()), {row[6] for row in before}
        )
        receipt, sources = link.finalize()
        self.assertEqual(set(sources), {"contacts", "messages"})
        self.assertNotIn("calls", {file.category for file in receipt.files})
        self.assertEqual(
            link.capture_unavailable_reasons, {"calls": "optional_capture_changed"}
        )
        self.assertEqual(tuple(link.backend.ledgers["calls"].iter_groups()), before)
        events, generations, chain = link.backend.ledgers["calls"].capture_evidence()
        self.assertEqual((events, generations), (8, 4))
        self.assertEqual(chain, self.gateway.ambiguities["calls"][3])
        self.assertEqual(len(self.objects.keys()), link.backend.native_groups)
        epoch = self.gateway.begin_processing()
        store = RecordStore(self.fixture.root / "replacement-review.sqlite3")
        try:
            coverage = process_sealed_sources(
                link,
                sources,
                epoch,
                store,
                self.gateway,
                datetime(2001, 1, 1, tzinfo=UTC),
            )
            self.assertEqual(coverage["calls"].status, "unavailable")
            self.assertIsNone(coverage["calls"].rows_seen)
            self.assertEqual(store.counts["calls"], 0)
            self.assertEqual(store.counts["contacts"], 1)
        finally:
            store.close()

    async def test_repeated_optional_deletion_retains_cleanup_inventory(self):
        files = self.supported_files()
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files, delete=2)
        )
        await link.run_capture()
        _receipt, sources = link.finalize()
        self.assertNotIn("calls", sources)
        self.assertTrue(self.gateway.ambiguities["calls"])
        self.assertEqual(len(self.objects.keys()), link.backend.native_groups)
        self.assertEqual(link.backend.ledgers["calls"].capture_evidence()[:2], (7, 3))

    async def test_sms_replacement_marks_only_sms_unavailable(self):
        files = self.supported_files()
        link, _wire = self.link(
            files,
            transcript=self.changed_optional_transcript(files, category="messages"),
        )
        await link.run_capture()
        _receipt, sources = link.finalize()
        self.assertEqual(set(sources), {"contacts", "calls"})
        self.assertEqual(
            link.capture_unavailable_reasons, {"messages": "optional_capture_changed"}
        )
        self.assertEqual(self.gateway.dispositions["messages"][0], "unavailable")

    async def test_replacement_with_corrupt_final_control_is_fatal(self):
        files = self.supported_files()
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files, bad_control=True)
        )
        await link.run_capture()
        with self.assertRaises(CaptureSeamError):
            link.finalize()
        self.assertEqual(self.gateway.state, "revoked")
        self.gateway.now = self.gateway.grant_horizon + 31
        self.assertTrue(link.retry_cleanup())
        self.assertFalse(self.objects.keys())

    async def test_optional_change_cannot_mask_contact_manifest_mismatch(self):
        files = self.supported_files()
        manifest = self.fixture.root / "Manifest.db"
        relative = DATABASES["contacts"]
        contact_size = next(
            len(data)
            for device, _path, data in files
            if device == HOME_DOMAIN + "-" + relative
        )
        with sqlite3.connect(manifest) as db:
            db.execute(
                "UPDATE Files SET file=? WHERE domain=? AND relativePath=?",
                (
                    capture_fixture.archived_entry(relative, contact_size + 1),
                    HOME_DOMAIN,
                    relative,
                ),
            )
        files = [
            (device, path, manifest.read_bytes() if device == "Manifest.db" else data)
            for device, path, data in files
        ]
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files)
        )
        await link.run_capture()
        with self.assertRaises(CaptureSeamError):
            link.finalize()
        self.assertEqual(self.gateway.state, "revoked")

    async def test_optional_change_cannot_mask_malformed_manifest_identity(self):
        files = self.supported_files()
        manifest = self.fixture.root / "Manifest.db"
        with sqlite3.connect(manifest) as db:
            db.execute(
                "UPDATE Files SET flags=0 WHERE domain=? AND relativePath=?",
                (HOME_DOMAIN, DATABASES["calls"]),
            )
        files = [
            (device, path, manifest.read_bytes() if device == "Manifest.db" else data)
            for device, path, data in files
        ]
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files)
        )
        await link.run_capture()
        with self.assertRaises(CaptureSeamError):
            link.finalize()
        self.assertEqual(self.gateway.state, "revoked")

    async def test_capture_generation_tamper_and_stale_consent_revoke(self):
        files = self.supported_files()
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files)
        )
        await link.run_capture()
        ledger = link.backend.ledgers["calls"]
        first = next(ledger.iter_groups())
        with sqlite3.connect(ledger.path) as db:
            db.execute("UPDATE capture_event SET group_end=group_end+1 WHERE seq=1")
        with self.assertRaises(ObjectVfsError):
            link.finalize()
        self.assertEqual(self.gateway.state, "revoked")
        self.assertIn(first[6], self.objects.keys())
        self.gateway.now = self.gateway.grant_horizon + 31
        self.assertTrue(link.retry_cleanup())
        self.assertFalse(self.objects.keys())

    async def test_optional_change_cannot_mask_required_contact_replacement(self):
        files = self.supported_files()
        calls = next(
            (device, path)
            for device, path, _ in files
            if device == HOME_DOMAIN + "-" + DATABASES["calls"]
        )
        contacts = next(
            (device, path)
            for device, path, _ in files
            if device == HOME_DOMAIN + "-" + DATABASES["contacts"]
        )
        transcript = self.changed_optional_transcript(files, prior_bytes=1024)
        marker = capture_fixture._file(*calls, b"A" * 1024)
        transcript = transcript.replace(
            marker, marker + capture_fixture._file(*contacts, b"wrong" * 10), 1
        )
        link, _wire = self.link(files, transcript=transcript)
        with self.assertRaises(CaptureSeamError):
            await link.run_capture()
        self.assertEqual(self.gateway.state, "revoked")
        self.assertGreater(link.backend.native_groups, 0)
        self.gateway.now = self.gateway.grant_horizon + 31
        self.assertTrue(link.retry_cleanup())
        self.assertFalse(self.objects.keys())

    async def test_optional_evidence_decline_replay_and_cancel_are_denied(self):
        files = self.supported_files()
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files)
        )
        await link.run_capture()
        _receipt, _sources = link.finalize()
        reason, events, generations, chain, descriptor = self.gateway.ambiguities[
            "calls"
        ]
        count = len([key for key in self.gateway.groups if key[0] == "calls"])
        self.assertEqual(
            link.backend.ledgers["calls"].capture_evidence(),
            (events, generations, chain),
        )
        with self.assertRaises(ObjectVfsError):
            self.gateway.capture_ambiguity(
                self.fixture.admission,
                "calls",
                reason,
                events,
                generations,
                "f" * 64,
                count,
                descriptor,
            )
        with self.assertRaises(ObjectVfsError):
            self.gateway.dispose(
                self.fixture.admission,
                "calls",
                "declined",
                count,
                self.gateway.dispositions["calls"][2],
                descriptor,
            )
        self.fixture.authority.active = False
        with self.assertRaises(CaptureSeamError):
            link.verify_receipt(_receipt)

    async def test_partial_optional_replacement_group_fails_closed_and_sweeps(self):
        files = self.supported_files()
        original = self.objects.put_exact

        def fail_second_group(key, payload, digest):
            if "/calls/main/1/" in key:
                raise OSError("synthetic optional upload interrupted")
            return original(key, payload, digest)

        self.objects.put_exact = fail_second_group
        link, _wire = self.link(
            files, transcript=self.changed_optional_transcript(files)
        )
        with self.assertRaises((OSError, VirtualStoreError)):
            await link.run_capture()
        self.assertEqual(self.gateway.state, "revoked")
        self.assertFalse(link._sealed)
        self.assertFalse(self.gateway.dispositions)
        self.assertGreater(len(link.backend.ledgers["calls"].object_keys()), 1)
        self.gateway.now = self.gateway.grant_horizon + 31
        self.assertTrue(link.retry_cleanup())
        self.assertFalse(self.objects.keys())

    async def test_provisional_single_namespace_and_post_terminal_parser(self) -> None:
        link, wire = self.link(self.supported_files())
        await link.run_capture()
        receipt, sources = link.finalize()
        self.assertEqual(set(sources), set(self.fixture.admission.categories))
        self.assertEqual(len(self.objects.keys()), link.backend.native_groups)
        self.assertEqual(self.objects.ciphertext_bytes, self.objects.high_water)
        self.assertLessEqual(wire.largest_read, 128 * 1024)
        self.assertLessEqual(link.store.max_buffered_bytes, 1024 * 1024)
        self.assertLessEqual(link.backend.max_sealed_bytes, 1024 * 1024 + 128)
        self.assertFalse(
            any(
                b"SYNTHETIC-PRIVATE-BODY" in blob for blob in self.objects.ciphertexts()
            )
        )
        self.assertFalse(
            any(
                b"UNRELATED-INVENTORY-SENTINEL" in blob
                for blob in self.objects.ciphertexts()
            )
        )
        self.assertEqual(
            len(receipt.files),
            6,
        )
        local = RecordStore(self.fixture.root / "local-review.sqlite3")
        grouped = RecordStore(self.fixture.root / "grouped-review.sqlite3")
        try:
            baseline = read_contacts(
                self.fixture.root / "contacts.sqlite3", None, local
            )
            epoch = self.gateway.begin_processing()
            source = sources["contacts"]
            with patch(
                "amplifai_phone.metadata._open_readonly",
                side_effect=lambda _path: open_grouped_wal_source(
                    source.source_set,
                    source.main_index,
                    source.wal_index,
                    ProcessingRanges(link, "contacts", epoch),
                    self.fixture.object_key,
                    self.fixture.receipt_key,
                ),
            ):
                streamed = read_contacts(
                    self.fixture.root / "virtual-contacts", None, grouped
                )
            self.assertEqual(streamed.rows_seen, baseline.rows_seen)
            self.assertEqual(list(streamed.records), list(baseline.records))
        finally:
            local.close()
            grouped.close()
        self.assertGreater(self.objects.range_calls, 0)

    async def test_verified_optional_main_without_wal_is_unavailable(self) -> None:
        files = self.supported_files()
        relative = DATABASES["calls"] + "-wal"
        device_name = HOME_DOMAIN + "-" + relative
        manifest = self.fixture.root / "Manifest.db"
        with sqlite3.connect(manifest) as db:
            db.execute(
                "DELETE FROM Files WHERE domain=? AND relativePath=?",
                (HOME_DOMAIN, relative),
            )
        files = [
            (device, path, manifest.read_bytes() if device == "Manifest.db" else data)
            for device, path, data in files
            if device != device_name
        ]
        link, _wire = self.link(files)
        await link.run_capture()
        _receipt, sources = link.finalize()
        self.assertEqual(set(sources), {"contacts", "messages"})
        self.assertEqual(
            link.capture_unavailable_reasons, {"calls": "optional_sidecar_missing"}
        )
        self.assertGreater(self.gateway.dispositions["calls"][1], 0)
        epoch = self.gateway.begin_processing()
        store = RecordStore(self.fixture.root / "optional-review.sqlite3")
        try:
            result = process_sealed_sources(
                link,
                sources,
                epoch,
                store,
                self.gateway,
                datetime(2001, 1, 1, tzinfo=UTC),
            )
            self.assertEqual(result["contacts"].status, "available")
            self.assertEqual(result["calls"].status, "unavailable")
            self.assertIsNone(result["calls"].rows_seen)
            self.assertEqual(result["messages"].retained_rows, 1)
            self.assertEqual(store.counts, {"contacts": 1, "calls": 0, "messages": 1})
            self.assertTrue(
                self.gateway.record_processing_outcome(
                    self.fixture.admission,
                    epoch,
                    "calls",
                    self.gateway.dispositions["calls"][3],
                    result["calls"],
                )
            )
        finally:
            store.close()

    async def test_optional_postseal_midread_rollback_and_global_failure(self) -> None:
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        _receipt, sources = link.finalize()
        epoch = self.gateway.begin_processing()
        store = RecordStore(self.fixture.root / "partial-review.sqlite3")
        part = Interaction(
            777,
            "call",
            "call",
            "2026-01-01T00:00:00+00:00",
            "incoming",
            ("+12025550101",),
            2,
        )

        def source_local_failure(_path, _since, _check, store, *, open_connection):
            store.add("calls", part)
            raise UnsupportedSchema("Synthetic mid-read optional schema failure")

        try:
            with patch(
                "amplifai_phone.offline_cloud_optional_processing.read_calls",
                side_effect=source_local_failure,
            ):
                result = process_sealed_sources(
                    link,
                    sources,
                    epoch,
                    store,
                    self.gateway,
                    datetime(2001, 1, 1, tzinfo=UTC),
                )
            self.assertEqual(result["contacts"].retained_rows, 1)
            self.assertEqual(result["calls"].reason_code, "optional_schema_unsupported")
            self.assertIsNone(result["calls"].retained_rows)
            self.assertEqual(store.counts, {"contacts": 1, "calls": 0, "messages": 1})
            self.assertEqual(len(list(store.collection("calls"))), 0)
        finally:
            store.close()

        previous_outcomes = dict(self.gateway.processing_outcomes)
        wrapped_provider_failure = apsw.CorruptError("SQLite surfaced provider failure")
        wrapped_provider_failure.__cause__ = ObjectVfsError("cloud_group_readback")
        for error in (
            SelectedPayloadIntegrityError("critical source integrity"),
            ObjectVfsError("cloud_processing_group_integrity"),
            WorkspaceError("private store full", code="workspace_low_space"),
            wrapped_provider_failure,
        ):
            other = RecordStore(
                self.fixture.root / f"failure-{uuid.uuid4().hex}.sqlite3"
            )

            def fail_after_partial(
                _path, _since, _check, store, *, open_connection, error=error
            ):
                store.add("calls", part)
                raise error

            try:
                with (
                    patch(
                        "amplifai_phone.offline_cloud_optional_processing.read_calls",
                        side_effect=fail_after_partial,
                    ),
                    self.assertRaises(type(error)),
                ):
                    process_sealed_sources(
                        link,
                        sources,
                        epoch,
                        other,
                        self.gateway,
                        datetime(2001, 1, 1, tzinfo=UTC),
                    )
                self.assertEqual(self.gateway.processing_outcomes, previous_outcomes)
            finally:
                other.close()

    async def test_actual_optional_cursor_midread_corrupt_is_unavailable(self) -> None:
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        _receipt, sources = link.finalize()
        epoch = self.gateway.begin_processing()
        store = RecordStore(self.fixture.root / "cursor-review.sqlite3")

        class FaultCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def __iter__(self):
                for row in self.cursor:
                    yield row
                    raise apsw.CorruptError("Synthetic source-local cursor fault")

        class FaultConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, statement, parameters=()):
                cursor = self.connection.execute(statement, parameters)
                if statement.startswith("SELECT ROWID, ZDATE"):
                    return FaultCursor(cursor)
                return cursor

        @contextmanager
        def failing_open(source_set, *args, **kwargs):
            with open_grouped_wal_source(source_set, *args, **kwargs) as db:
                yield (
                    FaultConnection(db)
                    if source_set.main.binding.source == "calls"
                    else db
                )

        try:
            with patch(
                "amplifai_phone.offline_cloud_optional_processing.open_grouped_wal_source",
                side_effect=failing_open,
            ):
                result = process_sealed_sources(
                    link,
                    sources,
                    epoch,
                    store,
                    self.gateway,
                    datetime(2001, 1, 1, tzinfo=UTC),
                )
            self.assertEqual(result["calls"].reason_code, "optional_sqlite_invalid")
            self.assertIsNone(result["calls"].rows_seen)
            self.assertEqual(store.counts, {"contacts": 1, "calls": 0, "messages": 1})
            self.assertEqual(len(list(store.collection("calls"))), 0)
        finally:
            store.close()

    async def test_stale_worker_and_cancel_never_mark_optional_unavailable(
        self,
    ) -> None:
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        _receipt, sources = link.finalize()
        epoch = self.gateway.begin_processing()
        stale = RecordStore(self.fixture.root / "stale-review.sqlite3")
        try:
            with self.assertRaises(ObjectVfsError):
                process_sealed_sources(
                    link,
                    sources,
                    epoch + 1,
                    stale,
                    self.gateway,
                    datetime(2001, 1, 1, tzinfo=UTC),
                )
            self.assertFalse(self.gateway.processing_outcomes)
        finally:
            stale.close()
        canceled = RecordStore(self.fixture.root / "canceled-review.sqlite3")

        def stop_before_optional() -> None:
            if "contacts" in self.gateway.processing_outcomes:
                raise CaptureSeamError("synthetic_cancel")

        try:
            with self.assertRaises(CaptureSeamError):
                process_sealed_sources(
                    link,
                    sources,
                    epoch,
                    canceled,
                    self.gateway,
                    datetime(2001, 1, 1, tzinfo=UTC),
                    check_cancel=stop_before_optional,
                )
            self.assertEqual(set(self.gateway.processing_outcomes), {"contacts"})
            self.assertEqual(canceled.counts["calls"], 0)
        finally:
            canceled.close()

    async def test_replacement_and_missing_terminal_revoke(self) -> None:
        link, _wire = self.link(self.supported_files(), operations=True)
        with self.assertRaises(CaptureSeamError):
            await link.run_capture()
        self.assertEqual(self.gateway.state, "revoked")
        self.assertFalse(link.backend.native_groups == 0)
        self.gateway.now = self.gateway.grant_horizon + 31
        self.assertTrue(link.retry_cleanup())
        self.assertFalse(self.objects.keys())

    async def test_optional_shm_is_unavailable_and_contacts_remain_verifiable(
        self,
    ) -> None:
        files = [
            row
            for row in self.fixture.inputs()
            if not row[0].endswith("-shm")
            or row[0] == HOME_DOMAIN + "-" + DATABASES["calls"] + "-shm"
        ]
        manifest = self.fixture.root / "Manifest.db"
        with sqlite3.connect(manifest) as db:
            db.execute(
                "DELETE FROM Files WHERE relativePath LIKE '%-shm' AND relativePath != ?",
                (DATABASES["calls"] + "-shm",),
            )
        files = [
            (device, path, manifest.read_bytes() if device == "Manifest.db" else data)
            for device, path, data in files
        ]
        link, _wire = self.link(files)
        await link.run_capture()
        _receipt, sources = link.finalize()
        self.assertEqual(set(sources), {"contacts", "messages"})
        self.assertEqual(
            link.capture_unavailable_reasons, {"calls": "optional_shm_unqualified"}
        )
        self.assertEqual(self.gateway.dispositions["calls"][0], "unavailable")
        self.assertEqual(
            self.gateway.ambiguities["calls"][0], "optional_shm_unqualified"
        )
        self.assertEqual(len(self.objects.keys()), link.backend.native_groups)
        self.assertEqual(self.gateway.state, "sealed")

    async def test_changed_group_and_lost_responses_fail_closed(self) -> None:
        original_reserve = self.gateway.reserve
        lost_reserve = False

        def reserve_once(*args):
            nonlocal lost_reserve
            value = original_reserve(*args)
            if not lost_reserve:
                lost_reserve = True
                raise TimeoutError("reserve response lost")
            return value

        self.gateway.reserve = reserve_once
        original_put = self.objects.put_exact
        lost_put = False

        def put_once(*args):
            nonlocal lost_put
            original_put(*args)
            if not lost_put:
                lost_put = True
                raise TimeoutError("write response lost")

        self.objects.put_exact = put_once
        original_verify = self.gateway.verify
        lost_verify = False

        def verify_once(*args):
            nonlocal lost_verify
            original_verify(*args)
            if not lost_verify:
                lost_verify = True
                raise TimeoutError("readback response lost")

        self.gateway.verify = verify_once
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        self.assertTrue(lost_reserve and lost_put and lost_verify)
        self.assertGreater(self.objects.put_calls, link.backend.native_groups)
        category, ledger = next(iter(link.backend.ledgers.items()))
        row = next(ledger.iter_groups())
        with self.assertRaises(ObjectVfsError):
            self.gateway.reserve(
                self.fixture.admission,
                category,
                row[1],
                0,
                str(uuid.UUID(hex=row[2])),
                str(row[5][0][5]),
                row[3],
                "f" * 64,
            )
        with self.assertRaises(ObjectVfsError):
            self.gateway.reserve(
                self.fixture.admission,
                category,
                row[1],
                0,
                str(uuid.UUID(hex=row[2])),
                "f" * 24,
                row[3],
                row[4],
            )
        wrong_owner = replace(self.fixture.admission, owner_id=str(uuid.uuid4()))
        for changed in (
            wrong_owner,
            replace(self.fixture.admission, phone_id=str(uuid.uuid4())),
            replace(self.fixture.admission, attempt_id=str(uuid.uuid4())),
        ):
            with self.assertRaises(ObjectVfsError):
                self.gateway.reserve(
                    changed,
                    category,
                    row[1],
                    0,
                    str(uuid.UUID(hex=row[2])),
                    str(row[5][0][5]),
                    row[3],
                    row[4],
                )
        original_terminal = self.gateway.terminal
        lost_terminal = False

        def terminal_once(*args):
            nonlocal lost_terminal
            original_terminal(*args)
            if not lost_terminal:
                lost_terminal = True
                raise TimeoutError("terminal response lost")

        self.gateway.terminal = terminal_once
        link.finalize()
        self.assertTrue(lost_terminal)

    async def test_missing_terminal_and_required_contacts_fail_closed(self) -> None:
        link, _wire = self.link(self.supported_files(), terminal=False)
        with self.assertRaises(asyncio.IncompleteReadError):
            await link.run_capture()
        self.assertEqual(self.gateway.state, "revoked")
        self.assertFalse(self.gateway.erased)

    async def test_separate_cloud_consent_is_required_before_capture(self) -> None:
        with self.assertRaises(CaptureSeamError):
            CaptureAdmission(
                **{
                    **self.fixture.admission.__dict__,
                    "cloud_source_approved": False,
                }
            )

    async def test_postseal_object_mutation_does_not_parse_as_saved_history(
        self,
    ) -> None:
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        _receipt, sources = link.finalize()
        epoch = self.gateway.begin_processing()
        first = next(link.backend.ledgers["contacts"].iter_groups())
        self.objects._path(first[6]).write_bytes(b"changed after seal")
        source = sources["contacts"]
        with (
            self.assertRaises((OSError, ObjectVfsError, sqlite3.Error)),
            open_grouped_wal_source(
                source.source_set,
                source.main_index,
                source.wal_index,
                ProcessingRanges(link, "contacts", epoch),
                self.fixture.object_key,
                self.fixture.receipt_key,
            ) as db,
        ):
            list(db.execute("SELECT count(*) FROM ABPerson"))

    async def test_corrupt_readback_and_midstream_revocation(self) -> None:
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        first_key = self.objects.keys()[0]
        self.objects._path(first_key).write_bytes(b"corrupt")
        with self.assertRaises((OSError, CaptureSeamError, VirtualStoreError)):
            link.finalize()
        self.assertEqual(self.gateway.state, "revoked")

    async def test_revocation_during_first_group_blocks_later_capture(self) -> None:
        original_put = self.objects.put_exact
        first = True

        def revoke_after_put(*args):
            nonlocal first
            original_put(*args)
            if first:
                first = False
                self.gateway.revoke(self.fixture.admission)

        self.objects.put_exact = revoke_after_put
        link, _wire = self.link(self.supported_files())
        with self.assertRaises(ObjectVfsError):
            await link.run_capture()
        self.assertEqual(self.gateway.state, "revoked")
        self.assertGreater(len(self.objects.keys()), 0)

    async def test_restart_abandons_provisional_objects(self) -> None:
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        expected = len(self.objects.keys())
        self.assertGreater(expected, 0)
        self.assertFalse(
            SingleStageCaptureLink.abandon_interrupted(
                self.fixture.admission, self.gateway, self.objects
            )
        )
        self.assertEqual(self.gateway.state, "revoked")
        self.gateway.now = self.gateway.grant_horizon + 31
        self.assertTrue(
            SingleStageCaptureLink.abandon_interrupted(
                self.fixture.admission, self.gateway, self.objects
            )
        )
        self.assertFalse(self.objects.keys())

    async def test_optional_missing_and_required_contact_failure(self) -> None:
        files = self.supported_files()
        manifest = self.fixture.root / "Manifest.db"
        with sqlite3.connect(manifest) as db:
            for category in ("calls", "messages"):
                db.execute(
                    "DELETE FROM Files WHERE relativePath IN (?, ?)",
                    (DATABASES[category], DATABASES[category] + "-wal"),
                )
        selected = [
            (device, path, manifest.read_bytes() if device == "Manifest.db" else data)
            for device, path, data in files
            if not any(
                device == HOME_DOMAIN + "-" + DATABASES[category] + suffix
                for category in ("calls", "messages")
                for suffix in ("", "-wal")
            )
        ]
        link, _wire = self.link(selected)
        await link.run_capture()
        _receipt, sources = link.finalize()
        self.assertEqual(set(sources), {"contacts"})
        self.assertEqual(self.gateway.dispositions["calls"][0], "unavailable")
        self.assertEqual(self.gateway.dispositions["messages"][0], "unavailable")

    async def test_required_contact_absent_blocks_all_source_seal(self) -> None:
        files = self.supported_files()
        manifest = self.fixture.root / "Manifest.db"
        with sqlite3.connect(manifest) as db:
            db.execute(
                "DELETE FROM Files WHERE relativePath IN (?, ?)",
                (DATABASES["contacts"], DATABASES["contacts"] + "-wal"),
            )
        selected = [
            (device, path, manifest.read_bytes() if device == "Manifest.db" else data)
            for device, path, data in files
            if not any(
                device == HOME_DOMAIN + "-" + DATABASES["contacts"] + suffix
                for suffix in ("", "-wal")
            )
        ]
        link, _wire = self.link(selected)
        await link.run_capture()
        with self.assertRaises(CaptureSeamError):
            link.finalize()
        self.assertEqual(self.gateway.state, "revoked")

    async def test_cleanup_horizon_resurrection_and_epoch(self) -> None:
        link, _wire = self.link(self.supported_files())
        await link.run_capture()
        _receipt, sources = link.finalize()
        epoch = self.gateway.begin_processing()
        group = next(link.backend.ledgers["contacts"].iter_groups())
        with self.assertRaises(ObjectVfsError):
            ProcessingRanges(link, "contacts", epoch - 1).get_range(group[2], 0, 1, 2)
        key = group[6]
        payload = self.objects.get_exact(key)
        digest = hashlib.sha256(payload).hexdigest()
        self.gateway.revoke(self.fixture.admission)
        self.assertFalse(link.retry_cleanup())
        self.assertFalse(self.objects.keys())
        # A still-live previously issued PUT grant recreates the same exact key.
        self.objects.put_exact(key, payload, digest)
        self.gateway.fail_delete_once = True
        with self.assertRaises(OSError):
            link.retry_cleanup()
        self.gateway.now = self.gateway.grant_horizon + 31
        self.assertTrue(link.retry_cleanup())
        self.assertFalse(self.objects.keys())
        self.assertTrue(self.gateway.erased)
        self.assertEqual(sources["contacts"].category, "contacts")


if __name__ == "__main__":
    unittest.main()
