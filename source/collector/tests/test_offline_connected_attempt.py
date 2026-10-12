"""Offline captured Apple ciphertext through journal port and grouped VFS."""

from __future__ import annotations

import hashlib
import resource
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_offline_encrypted_capture_bridge as capture_fixtures
from amplifai_phone.metadata import read_contacts
from amplifai_phone.offline_capture_object_store import SyntheticCaptureObjects
from amplifai_phone.offline_connected_attempt import (
    CapturedGroupBridge,
    PrivateCipherObjects,
    cleanup_abandoned_upload,
)
from amplifai_phone.offline_group_upload_ledger import GroupUploadLedger
from amplifai_phone.offline_object_vfs import CACHE_BYTES, ObjectVfsError
from amplifai_phone.record_store import RecordStore


class JournalPortFixture:
    """Synchronous offline port; the real SQLite journal has separate TS tests."""

    def __init__(self, store: PrivateCipherObjects) -> None:
        self.store = store
        self.expected: dict[str, str] = {}
        self.parts: list[dict] = []
        self.sealed = False
        self.active = True
        self.epoch = 0
        self.fail_cleanup_once = False

    def begin_captured_source(self, claim: dict[str, str]) -> None:
        if not self.active or claim != self.expected:
            raise ObjectVfsError("cloud_capture_unverified")

    def reserve_captured_group(
        self,
        actor: tuple[str, str, str],
        category: str,
        role: str,
        group_id: str,
        index: int,
        size: int,
        digest: str,
    ) -> str:
        if (
            not self.active
            or self.sealed
            or actor != self._actor
            or category != self.expected["category"]
        ):
            raise ObjectVfsError("cloud_admission_closed")
        if index < len(self.parts):
            prior = self.parts[index]
            if (prior["role"], prior["id"], prior["size"], prior["digest"]) != (
                role,
                group_id,
                size,
                digest,
            ):
                raise ObjectVfsError("cloud_part_changed")
            return prior["key"]
        if index != len(self.parts):
            raise ObjectVfsError("cloud_parts_incomplete")
        key = f"{actor[2]}/{uuid.uuid4()}"
        self.parts.append(
            {
                "role": role,
                "id": group_id,
                "size": size,
                "digest": digest,
                "key": key,
                "verified": False,
            }
        )
        return key

    @property
    def _actor(self) -> tuple[str, str, str]:
        return tuple(self.expected[key] for key in ("ownerId", "phoneId", "attemptId"))

    def verify_part(
        self, actor: tuple[str, str, str], category: str, index: int
    ) -> None:
        if (
            not self.active
            or actor != self._actor
            or category != self.expected["category"]
        ):
            raise ObjectVfsError("cloud_admission_closed")
        part = self.parts[index]
        digest = hashlib.sha256()
        size = 0
        for chunk in self.store.read_chunks(part["key"]):
            digest.update(chunk)
            size += len(chunk)
        if size != part["size"] or digest.hexdigest() != part["digest"]:
            raise ObjectVfsError("cloud_object_integrity")
        part["verified"] = True

    def seal_captured_source(self, actor: tuple[str, str, str], category: str) -> None:
        if (
            not self.active
            or actor != self._actor
            or category != self.expected["category"]
        ):
            raise ObjectVfsError("cloud_admission_closed")
        if (
            not self.parts
            or {part["role"] for part in self.parts} != {"main", "wal"}
            or not all(part["verified"] for part in self.parts)
        ):
            raise ObjectVfsError("cloud_sources_incomplete")
        self.sealed = True

    def begin_processing(self) -> int:
        if not self.active or not self.sealed:
            raise ObjectVfsError("cloud_sources_incomplete")
        self.epoch += 1
        return self.epoch

    def processing_group_by_id(
        self,
        actor: tuple[str, str, str],
        epoch: int,
        category: str,
        group_id: str,
    ) -> tuple[str, int, str]:
        if (
            not self.active
            or not self.sealed
            or epoch != self.epoch
            or actor != self._actor
            or category != self.expected["category"]
        ):
            raise ObjectVfsError("cloud_worker_fenced")
        matches = [part for part in self.parts if part["id"] == group_id]
        if len(matches) != 1 or not matches[0]["verified"]:
            raise ObjectVfsError("cloud_part_missing")
        part = matches[0]
        return part["key"], part["size"], part["digest"]

    def revoke_and_erase(self, actor: tuple[str, str, str]) -> int:
        if actor != self._actor:
            raise ObjectVfsError("cloud_admission_denied")
        self.active = False
        if self.fail_cleanup_once:
            self.fail_cleanup_once = False
            raise OSError("synthetic cleanup interrupted")
        for part in self.parts:
            self.store.delete(part["key"])
        return sum(self.store.exists(part["key"]) for part in self.parts)


class OfflineConnectedAttemptTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fixture = capture_fixtures.EncryptedCaptureBridgeTest(
            "test_encrypted_current_device_link_to_wal_parser"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.apple.doCleanups)
        self.addCleanup(self.fixture.doCleanups)

    async def _bridge(self):
        apple_fixture = self.fixture.apple.fixture("contacts", page_size=1024)
        link, _wire = await self.fixture._capture(apple_fixture)
        authority = link.open_local_key_authority(self.fixture.apple.password)
        self.addCleanup(link.close_local_key_authority)
        receipt = link.seal(authority=authority)
        root = self.fixture.apple.root
        objects = PrivateCipherObjects(
            root / "journal-cipher-objects", link.admission.attempt_id
        )
        journal = JournalPortFixture(objects)
        bridge = CapturedGroupBridge(
            link,
            receipt,
            authority,
            "contacts",
            journal,
            objects,
            self.fixture.apple.object_key,
            self.fixture.apple.receipt_key,
        )
        journal.expected = bridge.claim
        return apple_fixture, link, bridge, journal, objects

    async def test_capture_journal_range_and_parser_parity(self) -> None:
        fixture, link, bridge, journal, objects = await self._bridge()
        grouped = bridge.package(self.fixture.apple.root)
        self.assertEqual(len(journal.parts), len(list(objects.root.iterdir())))
        self.assertTrue(journal.sealed)
        self.assertGreater(
            sum(path.stat().st_size for path in objects.root.iterdir()), 0
        )
        epoch = journal.begin_processing()
        baseline_rows, baseline_records, _ = self.fixture.apple._parse(
            fixture, "contacts", local=True
        )
        review = RecordStore(self.fixture.apple.root / "journal-review.sqlite3")
        try:
            with patch(
                "amplifai_phone.metadata._open_readonly",
                side_effect=lambda _path: bridge.open_source(grouped, epoch),
            ):
                result = read_contacts(
                    self.fixture.apple.virtual_root / "contacts", None, review
                )
            self.assertEqual(result.rows_seen, baseline_rows)
            self.assertEqual(list(result.records), baseline_records)
        finally:
            review.close()
        self.assertEqual(
            sum(part["size"] for part in journal.parts),
            sum(path.stat().st_size for path in objects.root.iterdir()),
        )
        for path in objects.root.iterdir():
            payload = path.read_bytes()
            self.assertNotIn(b"UNRELATED-LOCAL-CONTROL", payload)
            self.assertNotIn(self.fixture.apple.password.encode(), payload)
        self.assertGreater(link.backend.local_control_bytes, 0)
        selected_bytes = sum(entry.size for entry in bridge.receipt.files)
        object_bytes = sum(part["size"] for part in journal.parts)
        index_bytes = (
            grouped.main_index.stat().st_size + grouped.wal_index.stat().st_size
        )
        authority_bytes = sum(
            path.stat().st_size
            for path in (
                link.workspace.directory / "cloud-authority-controls"
            ).iterdir()
        )
        self.assertGreater(selected_bytes, CACHE_BYTES)
        self.assertLessEqual(
            max(part["size"] for part in journal.parts), 8 * (128 * 1024 + 16)
        )
        self.measurements = {
            "selected_ciphertext_bytes": selected_bytes,
            "capture_simulated_remote_ciphertext_bytes": link.backend.remote_ciphertext_bytes,
            "grouped_ciphertext_bytes": object_bytes,
            "group_count": len(journal.parts),
            "largest_group_bytes": max(part["size"] for part in journal.parts),
            "local_control_backend_bytes": link.backend.local_control_bytes,
            "authority_input_copy_bytes": authority_bytes,
            "decrypted_manifest_control_bytes": bridge.authority.control_copy_path.stat().st_size,
            "private_index_bytes": index_bytes,
            "sanitized_review_store_bytes": (
                self.fixture.apple.root / "journal-review.sqlite3"
            )
            .stat()
            .st_size,
            "parser_rows_seen": result.rows_seen,
            "combined_fixture_process_peak_rss_bytes": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
        }

    async def test_revocation_corruption_and_epoch_fail_closed(self) -> None:
        _fixture, _link, bridge, journal, objects = await self._bridge()
        grouped = bridge.package(self.fixture.apple.root)
        epoch = journal.begin_processing()
        with (
            self.assertRaisesRegex(ObjectVfsError, "cloud_worker_fenced"),
            bridge.open_source(grouped, epoch + 1) as db,
        ):
            db.execute("SELECT count(*) FROM ABPerson").fetchone()
        first = journal.parts[0]
        Path(objects.root / first["key"].split("/")[1]).write_bytes(b"corrupt")
        with (
            self.assertRaises(ObjectVfsError),
            bridge.open_source(grouped, epoch) as db,
        ):
            db.execute("SELECT count(*) FROM ABPerson").fetchone()
        journal.active = False
        with (
            self.assertRaisesRegex(ObjectVfsError, "cloud_worker_fenced"),
            bridge.open_source(grouped, epoch) as db,
        ):
            db.execute("SELECT count(*) FROM ABPerson").fetchone()

    async def test_claim_scope_and_write_once_replay_are_exact(self) -> None:
        _fixture, _link, bridge, journal, objects = await self._bridge()
        with self.assertRaisesRegex(ObjectVfsError, "cloud_capture_unverified"):
            journal.begin_captured_source(
                {**bridge.claim, "phoneId": str(uuid.uuid4())}
            )
        with self.assertRaisesRegex(ObjectVfsError, "cloud_store_scope_invalid"):
            objects.put_exact(
                f"{uuid.uuid4()}/{uuid.uuid4()}",
                b"synthetic",
                hashlib.sha256(b"synthetic").hexdigest(),
            )
        bridge.package(self.fixture.apple.root)
        first = journal.parts[0]
        payload = b"".join(objects.read_chunks(first["key"]))
        objects.put_exact(first["key"], payload, first["digest"])
        journal.verify_part(bridge.actor, "contacts", 0)
        changed = b"different encrypted group"
        with self.assertRaisesRegex(ObjectVfsError, "cloud_store_conflict"):
            objects.put_exact(
                first["key"], changed, hashlib.sha256(changed).hexdigest()
            )

    async def test_lost_response_reuses_exact_encrypted_group(self) -> None:
        _fixture, _link, bridge, journal, objects = await self._bridge()
        original_verify = journal.verify_part
        lost = False

        def once(actor, category, index):
            nonlocal lost
            original_verify(actor, category, index)
            if not lost:
                lost = True
                raise TimeoutError("synthetic reply lost after commit")

        journal.verify_part = once
        bridge.package(self.fixture.apple.root)
        self.assertTrue(lost)
        self.assertEqual(bridge.upload_ledger.state, "complete")
        self.assertEqual(bridge.upload_ledger.group_count, len(journal.parts))
        self.assertEqual(len(list(objects.root.iterdir())), len(journal.parts))
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_group_invalid"):
            bridge.upload_ledger.prepare(
                0,
                "main",
                journal.parts[0]["id"],
                journal.parts[0]["size"],
                journal.parts[0]["digest"],
                (("changed nonce",),),
            )

    async def test_lost_object_upload_response_keeps_group_identity(self) -> None:
        _fixture, _link, bridge, journal, objects = await self._bridge()
        original_put = objects.put_exact
        lost = False

        def once(key, payload, digest):
            nonlocal lost
            original_put(key, payload, digest)
            if not lost:
                lost = True
                raise TimeoutError("synthetic upload response lost")

        objects.put_exact = once
        bridge.package(self.fixture.apple.root)
        self.assertTrue(lost)
        self.assertEqual(len(journal.parts), bridge.upload_ledger.group_count)
        self.assertEqual(len(list(objects.root.iterdir())), len(journal.parts))

    async def test_completed_set_reattaches_only_with_current_capture_and_keys(
        self,
    ) -> None:
        _fixture, link, bridge, journal, objects = await self._bridge()
        grouped = bridge.package(self.fixture.apple.root)
        fresh = CapturedGroupBridge(
            link,
            bridge.receipt,
            bridge.authority,
            "contacts",
            journal,
            objects,
            bridge.object_key,
            bridge.receipt_key,
        )
        resumed = fresh.resume_completed(self.fixture.apple.root)
        self.assertEqual(resumed.source_set, grouped.source_set)
        epoch = journal.begin_processing()
        with fresh.open_source(resumed, epoch) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM ABPerson").fetchone()[0], 80
            )
        wrong_key = CapturedGroupBridge(
            link,
            bridge.receipt,
            bridge.authority,
            "contacts",
            journal,
            objects,
            b"x" * 32,
            bridge.receipt_key,
        )
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_ledger_binding"):
            wrong_key.resume_completed(self.fixture.apple.root)
        source_set_path = fresh.upload_directory / "source-set.json"
        original = source_set_path.read_bytes()
        source_set_path.write_bytes(b"changed signed source set")
        with self.assertRaisesRegex(ObjectVfsError, "cloud_source_set_invalid"):
            fresh.resume_completed(self.fixture.apple.root)
        source_set_path.write_bytes(original)
        self.fixture.apple.current.active = False
        with self.assertRaisesRegex(RuntimeError, "no longer current"):
            fresh.resume_completed(self.fixture.apple.root)

    def test_changed_descriptor_or_cipher_key_cannot_resume(self) -> None:
        path = self.fixture.apple.root / "standalone-ledger.sqlite3"
        actor = tuple(str(uuid.uuid4()) for _ in range(3))
        source_set = str(uuid.uuid4())
        capture_mac = hashlib.sha256(b"capture").hexdigest()
        consent = str(uuid.uuid4())
        key = self.fixture.apple.object_key
        ledger = GroupUploadLedger(
            path,
            actor,
            source_set,
            capture_mac,
            consent,
            key,
            self.fixture.apple.receipt_key,
        )
        group_id = uuid.uuid4().hex
        digest = hashlib.sha256(b"group").hexdigest()
        descriptors = ((0, group_id, 32, 0, 32, "00" * 12, 16, digest, digest),)
        ledger.prepare(0, "main", group_id, 32, digest, descriptors)
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_group_changed"):
            ledger.prepare(0, "main", group_id, 32, digest, (("changed nonce",),))
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_ledger_binding"):
            GroupUploadLedger(
                path,
                actor,
                source_set,
                capture_mac,
                consent,
                b"x" * 32,
                self.fixture.apple.receipt_key,
                reopen=True,
            )
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_ledger_binding"):
            GroupUploadLedger(
                path,
                actor,
                source_set,
                capture_mac,
                consent,
                key,
                b"y" * 32,
                reopen=True,
            )
        ledger.mark_erasure_pending()
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_group_invalid"):
            ledger.reserved(0, group_id, f"{actor[2]}/{uuid.uuid4()}")
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_group_invalid"):
            ledger.verified(0)

    async def test_restart_requires_revoke_and_retries_cleanup(self) -> None:
        _fixture, link, bridge, journal, objects = await self._bridge()
        journal.fail_cleanup_once = True

        def lost_forever(_actor, _category, _index):
            raise TimeoutError("synthetic verification unavailable")

        journal.verify_part = lost_forever
        with self.assertRaisesRegex(ObjectVfsError, "cloud_cleanup_incomplete"):
            bridge.package(self.fixture.apple.root)
        path = bridge.upload_directory / "ledger.sqlite3"
        with self.assertRaisesRegex(ObjectVfsError, "cloud_upload_ledger_binding"):
            GroupUploadLedger(
                path,
                bridge.actor,
                bridge.binding.source_set_id,
                bridge.receipt.mac,
                str(uuid.uuid4()),
                bridge.object_key,
                bridge.receipt_key,
                reopen=True,
            )
        recovered = GroupUploadLedger(
            path,
            bridge.actor,
            bridge.binding.source_set_id,
            bridge.receipt.mac,
            link.admission.consent_receipt_id,
            bridge.object_key,
            bridge.receipt_key,
            reopen=True,
        )
        self.assertEqual(recovered.state, "erasure_pending")
        self.assertGreater(recovered.group_count, 0)
        remote = SyntheticCaptureObjects(
            link.backend.remote_objects.root, link.admission.attempt_id, reopen=True
        )
        cleanup_abandoned_upload(recovered, bridge.upload_directory, journal, remote)
        self.assertFalse(remote.keys())
        self.assertFalse(list(objects.root.iterdir()))
        self.assertFalse(bridge.upload_directory.exists())

    async def test_cleanup_retries_after_capture_object_delete_failure(
        self,
    ) -> None:
        _fixture, link, bridge, _journal, objects = await self._bridge()
        bridge.package(self.fixture.apple.root)
        remote = link.backend.remote_objects
        original_delete = remote.delete
        failed = False

        def once(key):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("synthetic capture deletion interrupted")
            original_delete(key)

        remote.delete = once
        with self.assertRaises(OSError):
            bridge.retry_cleanup()
        self.assertEqual(bridge.upload_ledger.state, "erasure_pending")
        self.assertFalse(list(objects.root.iterdir()))
        remote.delete = original_delete
        bridge.retry_cleanup()
        self.assertFalse(remote.keys())
        self.assertFalse(bridge.upload_directory.exists())

    async def test_cleanup_retries_after_local_index_delete_failure(self) -> None:
        _fixture, link, bridge, _journal, objects = await self._bridge()
        bridge.package(self.fixture.apple.root)
        original_unlink = Path.unlink
        failed = False

        def once(path, *args, **kwargs):
            nonlocal failed
            if path.name == "main.index" and not failed:
                failed = True
                raise OSError("synthetic local index deletion interrupted")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", once), self.assertRaises(OSError):
            bridge.retry_cleanup()
        self.assertEqual(bridge.upload_ledger.state, "erased")
        self.assertFalse(link.backend.remote_objects.keys())
        self.assertFalse(list(objects.root.iterdir()))
        bridge.retry_cleanup()
        self.assertFalse(bridge.upload_directory.exists())


if __name__ == "__main__":
    unittest.main()
