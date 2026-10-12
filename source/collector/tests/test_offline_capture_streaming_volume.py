"""Lazy DeviceLink source-volume probe; synthetic bytes never enter Production."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import plistlib
import resource
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from amplifai_phone.backup_watchdog import RECEIVE_CHUNK_BYTES, BackupWatchdog
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN
from amplifai_phone.metadata import read_contacts
from amplifai_phone.offline_capture_object_store import SyntheticCaptureObjects
from amplifai_phone.offline_cloud_capture import OfflineCloudCaptureLink
from amplifai_phone.offline_connected_attempt import PrivateCipherObjects
from amplifai_phone.offline_group_upload_ledger import GroupUploadLedger
from amplifai_phone.offline_grouped_object_vfs import (
    open_grouped_wal_source,
    package_grouped_source,
    seal_grouped_wal_set,
)
from amplifai_phone.offline_object_vfs import SourceBinding
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace
from large_backup_probe import archived_entry
from pymobiledevice3.service_connection import build_plist
from test_offline_cloud_capture import CaptureFixture, _prefixed


class LazyWire:
    def __init__(
        self, files: list[tuple[str, str, Path | bytes]], *, replacement: bool = True
    ) -> None:
        self.files = files
        self.replacement = replacement
        self.parts = iter(self._segments())
        self.pending = memoryview(b"")
        self.largest_read = 0
        self.sent_bytes = 0
        self.plists = []

    def _segments(self):
        directories = sorted({str(Path(name).parent) for _, name, _ in self.files})
        for directory in directories:
            yield build_plist(["DLMessageCreateDirectory", directory])
        yield build_plist(["DLMessageUploadFiles", "", 7.0])
        if self.replacement:
            device, name, _ = self.files[0]
            yield _prefixed(device) + _prefixed(name)
            yield struct.pack(">IB", len(b"prior replacement") + 1, 0xC)
            yield b"prior replacement"
            yield struct.pack(">IB", 1, 0)
        for device, name, content in self.files:
            yield _prefixed(device) + _prefixed(name)
            size = content.stat().st_size if isinstance(content, Path) else len(content)
            yield struct.pack(">IB", size + 1, 0xC)
            if isinstance(content, Path):
                with content.open("rb") as source:
                    while chunk := source.read(RECEIVE_CHUNK_BYTES):
                        yield chunk
            else:
                yield content
            yield struct.pack(">IB", 1, 0)
        yield struct.pack(">I", 0)
        yield build_plist(["DLMessageProcessMessage", {"ErrorCode": 0}])

    async def recvall(self, size: int) -> bytes:
        self.largest_read = max(self.largest_read, size)
        out = bytearray()
        while len(out) < size:
            if not self.pending:
                try:
                    self.pending = memoryview(next(self.parts))
                except StopIteration:
                    raise asyncio.IncompleteReadError(bytes(out), size) from None
            count = min(size - len(out), len(self.pending))
            out.extend(self.pending[:count])
            self.pending = self.pending[count:]
        return bytes(out)

    async def send_plist(self, value) -> None:
        self.plists.append(value)

    async def sendall(self, value: bytes) -> None:
        self.sent_bytes += len(value)


class FileGroupedObjects:
    def __init__(self, store: PrivateCipherObjects, ledger: GroupUploadLedger) -> None:
        self.store = store
        self.ledger = ledger

    def _key(self, object_id: str) -> str:
        return f"{self.store.attempt_id}/{uuid.UUID(hex=object_id)}"

    def put(self, object_id: str, ciphertext: bytes) -> None:
        key = self._key(object_id)
        self.ledger.reserved(self.ledger.group_count - 1, object_id, key)
        self.store.put_exact(key, ciphertext, hashlib.sha256(ciphertext).hexdigest())
        self.ledger.verified(self.ledger.group_count - 1)

    def get_range(self, object_id: str, start: int, end: int, max_bytes: int):
        return self.store.get_range(self._key(object_id), start, end, max_bytes)


async def run_probe(scale: int) -> dict[str, int]:
    fixture = CaptureFixture("test_real_dispatch_projection_readback_and_parser_parity")
    fixture.setUp()
    try:
        files: list[tuple[str, str, Path | bytes]] = []
        manifest = fixture.root / "Manifest.db"
        with closing(sqlite3.connect(manifest)) as control, control:
            control.execute(
                "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)"
            )
            control.execute("CREATE TABLE unrelated (private TEXT)")
            control.execute("INSERT INTO unrelated VALUES ('LOCAL-ONLY-INVENTORY')")
            for category in ("contacts", "calls", "messages"):
                source = fixture.source(category)
                db = fixture.connections[-1]
                db.execute("CREATE TABLE stream_volume (payload BLOB)")
                db.executemany(
                    "INSERT INTO stream_volume VALUES (zeroblob(65536))",
                    [()] * (12 * scale),
                )
                db.commit()
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.executemany(
                    "INSERT INTO stream_volume VALUES (zeroblob(65536))",
                    [()] * (8 * scale),
                )
                db.commit()
                for role, suffix in (("main", ""), ("wal", "-wal")):
                    member = Path(str(source) + suffix)
                    relative = DATABASES[category] + suffix
                    device = HOME_DOMAIN + "-" + relative
                    file_id = hashlib.sha1(device.encode()).hexdigest()
                    control.execute(
                        "INSERT INTO Files VALUES (?, ?, ?, 1, ?)",
                        (
                            file_id,
                            HOME_DOMAIN,
                            relative,
                            archived_entry(relative, member.stat().st_size),
                        ),
                    )
                    files.append(
                        (
                            device,
                            f"{fixture.admission.phone_id}/{file_id[:2]}/{file_id}",
                            member,
                        )
                    )
        for name, payload in (
            ("Manifest.db", manifest),
            ("Manifest.plist", plistlib.dumps({"IsEncrypted": False})),
            ("Info.plist", plistlib.dumps({})),
            ("Status.plist", plistlib.dumps({"SnapshotState": "finished"})),
        ):
            files.append((name, f"{fixture.admission.phone_id}/{name}", payload))
        files.append(
            (
                "CameraDomain-Unrelated",
                f"{fixture.admission.phone_id}/unselected/raw.db",
                b"UNSELECTED-SYNTHETIC-BYTES",
            )
        )
        remote = SyntheticCaptureObjects(
            fixture.root / "synthetic-remote", fixture.admission.attempt_id
        )
        wire = LazyWire(files)
        workspace = SessionWorkspace(fixture.root / "workspace")
        with workspace:
            watchdog = BackupWatchdog()
            watchdog.start()
            link = OfflineCloudCaptureLink(
                wire,
                workspace,
                watchdog,
                fixture.admission,
                fixture.authority,
                fixture.object_key,
                fixture.receipt_key,
                remote,
            )
            await link.run_capture()
            receipt = link.seal()
            link.verify_receipt(receipt)
            grouped_store = PrivateCipherObjects(
                fixture.root / "grouped-remote", fixture.admission.attempt_id
            )
            source_sets = {}
            indexes_by_category = {}
            ranges_by_category = {}
            ledgers = []
            for category in ("contacts", "calls", "messages"):
                selected_files = {
                    item.role: item
                    for item in receipt.files
                    if item.category == category
                }
                binding = SourceBinding(
                    fixture.admission.owner_id,
                    fixture.admission.phone_id,
                    fixture.admission.attempt_id,
                    category,
                    str(uuid.uuid4()),
                )
                ledger = GroupUploadLedger(
                    fixture.root / f"{category}-upload-ledger.sqlite3",
                    (
                        fixture.admission.owner_id,
                        fixture.admission.phone_id,
                        fixture.admission.attempt_id,
                    ),
                    binding.source_set_id,
                    receipt.mac,
                    fixture.admission.consent_receipt_id,
                    fixture.object_key,
                    fixture.receipt_key,
                )
                ledgers.append(ledger)
                ranges = FileGroupedObjects(grouped_store, ledger)
                indexes = {
                    role: fixture.root / f"{category}-{role}.index"
                    for role in ("main", "wal")
                }
                grouped = {}
                for role in ("main", "wal"):
                    grouped[role] = package_grouped_source(
                        link.source_reader(receipt, selected_files[role]),
                        indexes[role],
                        binding,
                        role,
                        ranges,
                        fixture.object_key,
                        fixture.receipt_key,
                        sidecars=("wal",) if role == "main" else (),
                        prepare_group=lambda group_id, entries, size, digest, role=role, ledger=ledger: (
                            ledger.prepare(
                                ledger.group_count,
                                role,
                                group_id,
                                size,
                                digest,
                                entries,
                            )
                        ),
                    )
                ledger.finish()
                source_sets[category] = seal_grouped_wal_set(
                    grouped["main"], grouped["wal"], fixture.receipt_key
                )
                indexes_by_category[category] = indexes
                ranges_by_category[category] = ranges
            source_set = source_sets["contacts"]
            indexes = indexes_by_category["contacts"]
            ranges = ranges_by_category["contacts"]
            baseline = RecordStore(fixture.root / "baseline-review.sqlite3")
            review = RecordStore(fixture.root / "grouped-review.sqlite3")
            (fixture.root / "virtual").mkdir()
            try:
                local = read_contacts(fixture.root / "contacts.sqlite3", None, baseline)
                with patch(
                    "amplifai_phone.metadata._open_readonly",
                    side_effect=lambda _path: open_grouped_wal_source(
                        source_set,
                        indexes["main"],
                        indexes["wal"],
                        ranges,
                        fixture.object_key,
                        fixture.receipt_key,
                    ),
                ):
                    streamed = read_contacts(
                        fixture.root / "virtual" / "contacts", None, review
                    )
                assert streamed.rows_seen == local.rows_seen
                assert list(streamed.records) == list(local.records)
            finally:
                baseline.close()
                review.close()
            selected_bytes = sum(item.size for item in receipt.files)
            source_members = [
                {
                    "category": item.category,
                    "role": item.role,
                    "size": item.size,
                    "sha256": item.sha256,
                }
                for item in sorted(
                    receipt.files, key=lambda item: (item.category, item.role)
                )
            ]
            source_set_sha256 = hashlib.sha256(
                json.dumps(
                    source_members, sort_keys=True, separators=(",", ":")
                ).encode("ascii")
            ).hexdigest()
            grouped_bytes = sum(
                path.stat().st_size for path in grouped_store.root.iterdir()
            )
            result = {
                "selected_bytes": selected_bytes,
                "selected_file_count": len(receipt.files),
                "selected_source_set_sha256": source_set_sha256,
                "selected_source_members": source_members,
                "capture_object_count": len(remote.keys()),
                "capture_ciphertext_bytes": remote.ciphertext_bytes,
                "group_object_count": len(list(grouped_store.root.iterdir())),
                "group_ciphertext_bytes": grouped_bytes,
                "max_virtual_buffer_bytes": link.store.max_buffered_bytes,
                "max_device_read_bytes": wire.largest_read,
                "max_capture_input_bytes": link.backend.max_input_bytes,
                "max_capture_ciphertext_bytes": link.backend.max_sealed_bytes,
                "local_control_bytes": link.backend.local_control_bytes,
                "local_index_bytes": sum(
                    path.stat().st_size
                    for pair in indexes_by_category.values()
                    for path in pair.values()
                ),
                "local_upload_ledger_bytes": sum(
                    ledger.path.stat().st_size for ledger in ledgers
                ),
                "group_ledger_records": sum(ledger.group_count for ledger in ledgers),
                "local_review_bytes": (fixture.root / "grouped-review.sqlite3")
                .stat()
                .st_size,
                "parser_rows": streamed.rows_seen,
                "whole_process_peak_rss_bytes": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
            }
            assert not any("discard" in key for key in remote.keys())  # noqa: SIM118 - object store
            return result
    finally:
        fixture.tearDown()
        fixture.doCleanups()


class StreamingVolumeTest(unittest.TestCase):
    def test_two_lazy_source_volumes_stay_bounded(self) -> None:
        results = []
        for scale in (4, 12):
            process = subprocess.run(
                [sys.executable, __file__, "--probe", str(scale)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            results.append(json.loads(process.stdout))
        small, large = results
        self.assertGreater(small["selected_bytes"], 12 * 1024 * 1024)
        self.assertGreater(large["selected_bytes"], small["selected_bytes"] * 2)
        for result in results:
            self.assertEqual(result["selected_file_count"], 6)
            self.assertEqual(result["parser_rows"], 1)
            self.assertLessEqual(result["max_virtual_buffer_bytes"], 1024 * 1024)
            self.assertLessEqual(result["max_capture_input_bytes"], 1024 * 1024)
            self.assertLessEqual(result["max_device_read_bytes"], RECEIVE_CHUNK_BYTES)
            self.assertGreater(result["capture_object_count"], 6)
            self.assertGreater(result["group_object_count"], 1)
        self.assertLess(
            large["whole_process_peak_rss_bytes"]
            - small["whole_process_peak_rss_bytes"],
            24 * 1024 * 1024,
        )
        self.measurements = results


class SyntheticCaptureObjectRecoveryTest(unittest.TestCase):
    def test_reopen_pending_orphan_and_cleanup_retry(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="amplifai-object-recovery-"
        ) as temporary:
            root = Path(temporary) / "synthetic-remote"
            attempt = str(uuid.uuid4())
            store = SyntheticCaptureObjects(root, attempt)
            key = f"{uuid.uuid4().hex}/contacts-main/{uuid.uuid4().hex}"
            ciphertext = os.urandom(256)
            store.put_exact(key, ciphertext)
            reopened = SyntheticCaptureObjects(root, attempt, reopen=True)
            self.assertEqual(reopened.get_exact(key), ciphertext)
            with reopened._db() as db:
                db.execute("UPDATE object SET state='pending' WHERE key=?", (key,))
            with self.assertRaises(OSError):
                reopened.get_exact(key)
            orphan = reopened._path(f"{uuid.uuid4().hex}/calls-wal/{uuid.uuid4().hex}")
            orphan.write_bytes(os.urandom(128))
            original_delete = reopened.delete
            failed = False

            def once(value):
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("synthetic cleanup interrupted")
                original_delete(value)

            reopened.delete = once
            with self.assertRaises(OSError):
                reopened.cleanup()
            reopened = SyntheticCaptureObjects(root, attempt, reopen=True)
            reopened.cleanup()
            self.assertFalse(reopened.keys())
            self.assertFalse(orphan.exists())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        print(json.dumps(asyncio.run(run_probe(int(sys.argv[2])))))
    else:
        unittest.main()
