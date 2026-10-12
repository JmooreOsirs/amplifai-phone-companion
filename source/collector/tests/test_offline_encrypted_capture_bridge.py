"""Synthetic DeviceLink-to-Apple-CBC bridge; no phone or network access."""

from __future__ import annotations

import asyncio
import hashlib
import plistlib
import unittest
import uuid
from dataclasses import replace
from unittest.mock import patch

import test_offline_apple_object_reader as apple_fixtures
import test_offline_cloud_capture as capture_fixtures
from amplifai_phone.backup_watchdog import BackupWatchdog
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN
from amplifai_phone.metadata import read_contacts
from amplifai_phone.offline_apple_object_reader import open_apple_grouped_wal_source
from amplifai_phone.offline_capture_object_store import SyntheticCaptureObjects
from amplifai_phone.offline_cloud_capture import (
    CaptureSeamError,
    OfflineCloudCaptureLink,
)
from amplifai_phone.offline_grouped_object_vfs import (
    package_grouped_source,
    seal_grouped_wal_set,
)
from amplifai_phone.offline_object_vfs import ObjectVfsError, SourceBinding
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace


class EncryptedCaptureBridgeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.apple = apple_fixtures.AppleEncryptedObjectTest(
            "test_parser_parity_three_page_sizes_and_bounded_local_floor"
        )
        self.apple.setUp()
        self.addCleanup(self.apple.doCleanups)
        self.apple.admission = replace(self.apple.admission, categories=("contacts",))
        self.apple.current.admission = self.apple.admission

    def _files(self, fixture: dict) -> list[tuple[str, str, bytes]]:
        phone = self.apple.admission.phone_id
        files = []
        for role, suffix in (("main", ""), ("wal", "-wal")):
            device = HOME_DOMAIN + "-" + DATABASES["contacts"] + suffix
            file_id = hashlib.sha1(device.encode()).hexdigest()
            files.append(
                (
                    device,
                    f"{phone}/{file_id[:2]}/{file_id}",
                    fixture["encrypted_paths"][role].read_bytes(),
                )
            )
        for name, payload in (
            ("Manifest.db", fixture["manifest_cipher"].read_bytes()),
            ("Manifest.plist", fixture["plist"].read_bytes()),
            ("Info.plist", plistlib.dumps({})),
            ("Status.plist", plistlib.dumps({"SnapshotState": "finished"})),
        ):
            files.append((name, f"{phone}/{name}", payload))
        files.append(
            (
                "CameraDomain-Library/Unrelated/raw.db",
                f"{phone}/unselected/raw.db",
                b"UNSELECTED-SYNTHETIC-PRIVATE-BYTES",
            )
        )
        return files

    async def _capture(self, fixture: dict):
        files = self._files(fixture)
        workspace = SessionWorkspace(self.apple.root / f"session-{uuid.uuid4().hex}")
        workspace.__enter__()
        self.addCleanup(workspace.__exit__, None, None, None)
        watchdog = BackupWatchdog()
        watchdog.start()
        wire = capture_fixtures.Wire(
            capture_fixtures.CaptureFixture.transcript(self.apple, files)
        )
        link = OfflineCloudCaptureLink(
            wire,
            workspace,
            watchdog,
            self.apple.admission,
            self.apple.current,
            self.apple.object_key,
            self.apple.receipt_key,
            SyntheticCaptureObjects(
                self.apple.root / f"capture-remote-{uuid.uuid4().hex}",
                self.apple.admission.attempt_id,
            ),
        )
        await link.run_capture()
        return link, wire

    async def test_encrypted_current_device_link_to_wal_parser(self) -> None:
        fixture = self.apple.fixture("contacts", page_size=1024)
        link, wire = await self._capture(fixture)
        with self.assertRaisesRegex(CaptureSeamError, "capture_encrypted_unqualified"):
            link.seal()
        with self.assertRaises(ObjectVfsError):
            link.open_local_key_authority("wrong-synthetic-password")
        self.assertFalse(
            list((link.workspace.directory / "cloud-authority-controls").iterdir())
        )
        authority = link.open_local_key_authority(self.apple.password)
        self.addCleanup(link.close_local_key_authority)
        receipt = link.seal(authority=authority)
        self.assertEqual({entry.role for entry in receipt.files}, {"main", "wal"})
        self.assertEqual(receipt.attempt_id, self.apple.admission.attempt_id)
        self.assertGreaterEqual(len(link.backend.remote_objects.keys()), 2)
        self.assertNotIn(b"UNSELECTED-SYNTHETIC-PRIVATE-BYTES", wire.sent)
        self.assertGreater(link.backend.local_control_bytes, 0)

        binding = SourceBinding(
            self.apple.owner,
            self.apple.phone,
            self.apple.attempt,
            "contacts",
            str(uuid.uuid4()),
        )
        objects = apple_fixtures.RangeObjects()
        indexes = {}
        grouped = {}
        for role in ("main", "wal"):
            selected = next(entry for entry in receipt.files if entry.role == role)
            indexes[role] = self.apple.root / f"captured-{role}.index"
            grouped[role] = package_grouped_source(
                link.source_reader(receipt, selected, authority=authority),
                indexes[role],
                binding,
                role,
                objects,
                self.apple.object_key,
                self.apple.receipt_key,
                sidecars=("wal",) if role == "main" else (),
            )
            self.assertEqual(grouped[role].sha256, selected.sha256)
        source_set = seal_grouped_wal_set(
            grouped["main"], grouped["wal"], self.apple.receipt_key
        )
        entries = {
            role: authority.project_entry(grouped[role]) for role in ("main", "wal")
        }
        baseline_rows, baseline_records, _ = self.apple._parse(
            fixture, "contacts", local=True
        )
        review = RecordStore(self.apple.root / "captured-review.sqlite3")
        try:
            with patch(
                "amplifai_phone.metadata._open_readonly",
                side_effect=lambda _path: open_apple_grouped_wal_source(
                    source_set,
                    indexes["main"],
                    indexes["wal"],
                    objects,
                    self.apple.object_key,
                    self.apple.receipt_key,
                    entries["main"],
                    entries["wal"],
                    authority,
                ),
            ):
                result = read_contacts(
                    self.apple.virtual_root / "contacts", None, review
                )
            self.assertEqual(result.rows_seen, baseline_rows)
            self.assertEqual(list(result.records), baseline_records)
        finally:
            review.close()
        self.assertFalse(
            any(
                b"UNRELATED-LOCAL-CONTROL" in value
                for value in link.backend.remote_objects.ciphertexts()
            )
        )
        self.assertFalse(
            any(
                b"UNSELECTED-SYNTHETIC-PRIVATE-BYTES" in value
                for value in link.backend.remote_objects.ciphertexts()
            )
        )
        self.assertGreater(sum(path.stat().st_size for path in indexes.values()), 0)
        link.close_local_key_authority()
        resumed = link.open_local_key_authority(self.apple.password)
        link.verify_receipt(receipt, authority=resumed)

    async def test_stale_authority_and_control_sidecar_are_denied(self) -> None:
        fixture = self.apple.fixture("contacts", page_size=4096)
        link, _ = await self._capture(fixture)
        authority = link.open_local_key_authority(self.apple.password)
        self.addCleanup(link.close_local_key_authority)
        receipt = link.seal(authority=authority)
        self.apple.current.active = False
        with self.assertRaisesRegex(RuntimeError, "no longer current"):
            link.verify_receipt(receipt, authority=authority)
        self.apple.current.active = True
        link.store.open_write(
            f"{self.apple.phone}/Manifest.db-wal", selected=True, scope="control"
        ).finish()
        with self.assertRaisesRegex(CaptureSeamError, "capture_source_mutated"):
            link.verify_receipt(receipt, authority=authority)

    async def test_encrypted_capture_needs_real_completion(self) -> None:
        fixture = self.apple.fixture("contacts", page_size=1024)
        files = self._files(fixture)
        workspace = SessionWorkspace(self.apple.root / "incomplete-session")
        with workspace:
            watchdog = BackupWatchdog()
            watchdog.start()
            link = OfflineCloudCaptureLink(
                capture_fixtures.Wire(
                    capture_fixtures.CaptureFixture.transcript(
                        self.apple, files, terminal=False
                    )
                ),
                workspace,
                watchdog,
                self.apple.admission,
                self.apple.current,
                self.apple.object_key,
                self.apple.receipt_key,
                SyntheticCaptureObjects(
                    self.apple.root / "incomplete-remote",
                    self.apple.admission.attempt_id,
                ),
            )
            with self.assertRaises(asyncio.IncompleteReadError):
                await link.run_capture()
            with self.assertRaisesRegex(CaptureSeamError, "capture_not_completed"):
                link.open_local_key_authority(self.apple.password)


if __name__ == "__main__":
    unittest.main()
