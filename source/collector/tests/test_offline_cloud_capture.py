"""Synthetic pinned DeviceLink transcript; no phone, network or cloud resource."""

from __future__ import annotations

import asyncio
import hashlib
import os
import plistlib
import sqlite3
import struct
import tempfile
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from amplifai_phone.backup_watchdog import RECEIVE_CHUNK_BYTES, BackupWatchdog
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN
from amplifai_phone.metadata import read_calls, read_contacts, read_messages
from amplifai_phone.offline_capture_object_store import SyntheticCaptureObjects
from amplifai_phone.offline_cloud_capture import (
    CLOUD_DISCLOSURE_VERSION,
    CaptureAdmission,
    CaptureSeamError,
    EncryptedAttemptBackend,
    OfflineCloudCaptureLink,
)
from amplifai_phone.offline_grouped_object_vfs import (
    RangeReply,
    open_grouped_wal_source,
    package_grouped_source,
    seal_grouped_wal_set,
)
from amplifai_phone.offline_object_vfs import SourceBinding
from amplifai_phone.record_store import RecordStore
from amplifai_phone.virtual_source_store import VirtualStoreError
from amplifai_phone.workspace import SessionWorkspace
from large_backup_probe import archived_entry
from pymobiledevice3.service_connection import build_plist


def _prefixed(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def _file(device_name: str, name: str, payload: bytes) -> bytes:
    return (
        _prefixed(device_name)
        + _prefixed(name)
        + struct.pack(">IB", len(payload) + 1, 0xC)
        + payload
        + struct.pack(">IB", 1, 0)
    )


class Wire:
    def __init__(self, payload: bytes) -> None:
        self.payload = memoryview(payload)
        self.offset = 0
        self.plists = []
        self.sent = bytearray()
        self.largest_read = 0
        self.after_read = None

    async def recvall(self, size: int) -> bytes:
        self.largest_read = max(self.largest_read, size)
        result = self.payload[self.offset : self.offset + size].tobytes()
        self.offset += len(result)
        if len(result) != size:
            raise asyncio.IncompleteReadError(result, size)
        if self.after_read is not None:
            self.after_read(result)
        return result

    async def send_plist(self, value) -> None:
        self.plists.append(value)

    async def sendall(self, value: bytes) -> None:
        self.sent.extend(value)


class RangeObjects:
    def __init__(self) -> None:
        self.objects = {}

    def put(self, object_id: str, ciphertext: bytes) -> None:
        self.objects[object_id] = ciphertext

    def get_range(
        self, object_id: str, start: int, end: int, max_bytes: int
    ) -> RangeReply:
        value = self.objects[object_id]
        body = value[start : end + 1]
        if len(body) > max_bytes:
            raise AssertionError("Unbounded object request")
        return RangeReply(206, f"bytes {start}-{end}/{len(value)}", len(body), body)


class CurrentAuthority:
    def __init__(self, admission: CaptureAdmission) -> None:
        self.admission = admission
        self.active = True

    def __call__(self, candidate: CaptureAdmission, source: str | None) -> None:
        if not self.active or candidate != self.admission:
            raise CaptureSeamError("capture_consent_stale")
        if source is not None and source not in candidate.categories:
            raise CaptureSeamError("capture_source_unapproved")


class CaptureFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="amplifai-capture-offline-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.admission = CaptureAdmission(
            *(str(uuid.uuid4()) for _ in range(6)),
            ("calls", "contacts", "messages"),
            CLOUD_DISCLOSURE_VERSION,
            True,
        )
        self.authority = CurrentAuthority(self.admission)
        self.object_key = os.urandom(32)
        self.receipt_key = os.urandom(32)
        self.connections = []

    def tearDown(self) -> None:
        for db in self.connections:
            db.close()

    def source(self, category: str) -> Path:
        path = self.root / f"{category}.sqlite3"
        db = sqlite3.connect(path)
        self.connections.append(db)
        db.execute("PRAGMA page_size=4096")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        if category == "contacts":
            db.executescript(
                "CREATE TABLE ABPerson (First TEXT, Last TEXT);"
                "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT);"
            )
        elif category == "calls":
            db.execute(
                "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, "
                "ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER)"
            )
        else:
            db.executescript(
                "CREATE TABLE handle (id TEXT);"
                "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, "
                "handle_id INTEGER, text TEXT);"
            )
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if category == "contacts":
            db.execute("INSERT INTO ABPerson VALUES ('Synthetic', 'Contact')")
            db.execute("INSERT INTO ABMultiValue VALUES (1, 3, '+12025550101')")
        elif category == "calls":
            db.execute(
                "INSERT INTO ZCALLRECORD VALUES (800000000, 65, '+12025550101', 1, 1)"
            )
        else:
            db.execute("INSERT INTO handle VALUES ('+12025550101')")
            db.execute(
                "INSERT INTO message VALUES (800000000, 'SMS', 0, 1, 'SYNTHETIC-PRIVATE-BODY')"
            )
        db.commit()
        return path

    def inputs(self, *, stale_control: bool = False, manifest_wal: bool = False):
        files = []
        manifest = self.root / "Manifest.db"
        with closing(sqlite3.connect(manifest)) as db, db:
            db.execute(
                "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)"
            )
            db.execute("CREATE TABLE unrelated (secret TEXT)")
            db.execute("INSERT INTO unrelated VALUES ('UNRELATED-INVENTORY-SENTINEL')")
            for category in ("contacts", "calls", "messages"):
                source = self.source(category)
                for role, suffix in (("main", ""), ("wal", "-wal"), ("shm", "-shm")):
                    member = Path(str(source) + suffix)
                    if not member.exists():
                        continue
                    relative = DATABASES[category] + suffix
                    device = HOME_DOMAIN + "-" + relative
                    file_id = hashlib.sha1(device.encode()).hexdigest()
                    db.execute(
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
                            f"{self.admission.phone_id}/{file_id[:2]}/{file_id}",
                            member.read_bytes(),
                        )
                    )
        controls = {
            "Manifest.db": manifest.read_bytes(),
            "Manifest.plist": plistlib.dumps({"IsEncrypted": False}),
            "Info.plist": plistlib.dumps({}),
            "Status.plist": plistlib.dumps({"SnapshotState": "finished"}),
        }
        if stale_control:
            relative = DATABASES["contacts"]
            with closing(sqlite3.connect(manifest)) as db, db:
                db.execute(
                    "UPDATE Files SET file=? WHERE domain=? AND relativePath=?",
                    (
                        archived_entry(
                            relative,
                            (self.root / "contacts.sqlite3").stat().st_size + 1,
                        ),
                        HOME_DOMAIN,
                        relative,
                    ),
                )
            controls["Manifest.db"] = manifest.read_bytes()
        if manifest_wal:
            db = sqlite3.connect(manifest)
            self.connections.append(db)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("INSERT INTO unrelated VALUES ('WAL-ONLY-CONTROL')")
            db.commit()
            controls["Manifest.db-wal"] = Path(str(manifest) + "-wal").read_bytes()
            controls["Manifest.db-shm"] = Path(str(manifest) + "-shm").read_bytes()
        for name, payload in controls.items():
            files.append((name, f"{self.admission.phone_id}/{name}", payload))
        files.append(
            (
                "CameraDomain-Library/Unrelated/raw.db",
                f"{self.admission.phone_id}/unselected/raw.db",
                b"UNSELECTED-PRIVATE-BYTES",
            )
        )
        return files

    def transcript(self, files, *, terminal=True, malformed=False, operations=False):
        phone = self.admission.phone_id
        payload = bytearray()
        for directory in sorted({str(Path(name).parent) for _, name, _ in files}):
            payload.extend(build_plist(["DLMessageCreateDirectory", directory]))
        payload.extend(build_plist(["DLMessageUploadFiles", "", 7.0]))
        if operations:
            prior_device, prior_name, _ = next(
                value
                for value in files
                if value[0] == HOME_DOMAIN + "-" + DATABASES["contacts"]
            )
            payload.extend(_file(prior_device, prior_name, b"prior replacement"))
        for device, name, data in files:
            payload.extend(_file(device, name, data))
        payload.extend(struct.pack(">I", 0))
        if operations:
            target = next(
                name
                for device, name, _ in files
                if device == HOME_DOMAIN + "-" + DATABASES["contacts"]
            )
            payload.extend(
                build_plist(["DLContentsOfDirectory", str(Path(target).parent)])
            )
            payload.extend(
                build_plist(
                    [
                        "DLMessageDownloadFiles",
                        [target, f"{phone}/unselected/raw.db"],
                        "",
                        8.0,
                    ]
                )
            )
            payload.extend(
                build_plist(["DLMessageCopyItem", target, f"{phone}/TempCopy"])
            )
            payload.extend(build_plist(["DLContentsOfDirectory", phone]))
            payload.extend(
                build_plist(
                    [
                        "DLMessageMoveItems",
                        {f"{phone}/TempCopy": f"{phone}/TempMoved"},
                        "",
                        9.0,
                    ]
                )
            )
            payload.extend(build_plist(["DLContentsOfDirectory", phone]))
            payload.extend(
                build_plist(["DLMessageRemoveItems", [f"{phone}/TempMoved"], "", 10.0])
            )
            payload.extend(build_plist(["DLContentsOfDirectory", phone]))
        if terminal:
            status = {"ErrorCode": False if malformed else 0}
            payload.extend(build_plist(["DLMessageProcessMessage", status]))
        return bytes(payload)

    def open_link(self, wire: Wire, workspace: SessionWorkspace):
        watchdog = BackupWatchdog()
        watchdog.start()
        remote_objects = SyntheticCaptureObjects(
            self.root / f"synthetic-capture-{uuid.uuid4().hex}",
            self.admission.attempt_id,
        )
        return OfflineCloudCaptureLink(
            wire,
            workspace,
            watchdog,
            self.admission,
            self.authority,
            self.object_key,
            self.receipt_key,
            remote_objects,
        )

    async def test_real_dispatch_projection_readback_and_parser_parity(self) -> None:
        files = self.inputs()
        (self.root / "virtual").mkdir()
        wire = Wire(self.transcript(files, operations=True))
        workspace = SessionWorkspace(self.root / "sessions")
        with workspace:
            link = self.open_link(wire, workspace)
            await link.run_capture()
            receipt = link.seal()
            link.verify_receipt(receipt)
            self.assertEqual(
                {file.category for file in receipt.files},
                set(self.admission.categories),
            )
            self.assertEqual(
                len([file for file in receipt.files if file.role == "wal"]), 3
            )
            self.assertTrue(
                any(
                    entry.get("DLFileSize", 0) > 0
                    for response in wire.plists
                    if response[0] == "DLMessageStatusResponse"
                    and isinstance(response[3], dict)
                    for entry in response[3].values()
                    if isinstance(entry, dict)
                )
            )
            root_listings = [
                response[3]
                for response in wire.plists
                if response[0] == "DLMessageStatusResponse"
                and isinstance(response[3], dict)
                and ("TempCopy" in response[3] or "TempMoved" in response[3])
            ]
            self.assertEqual(len(root_listings), 2)
            self.assertIn("TempCopy", root_listings[0])
            self.assertNotIn("TempMoved", root_listings[0])
            self.assertIn("TempMoved", root_listings[1])
            self.assertNotIn("TempCopy", root_listings[1])
            self.assertNotIn(
                "TempMoved",
                [
                    response[3]
                    for response in wire.plists
                    if response[0] == "DLMessageStatusResponse"
                    and isinstance(response[3], dict)
                ][-1],
            )
            self.assertIn(b"SQLite format 3", wire.sent)
            self.assertNotIn(b"prior replacement", wire.sent)
            self.assertLessEqual(wire.largest_read, RECEIVE_CHUNK_BYTES)
            self.assertGreater(link.backend.local_control_bytes, 0)
            self.assertGreater(link.backend.remote_ciphertext_bytes, 0)
            self.assertFalse(
                any(
                    b"SYNTHETIC-PRIVATE-BODY" in value
                    for value in link.backend.remote_objects.ciphertexts()
                )
            )
            self.assertFalse(
                any(
                    b"UNSELECTED-PRIVATE-BYTES" in value
                    for value in link.backend.remote_objects.ciphertexts()
                )
            )
            self.assertTrue(
                any(
                    b"UNRELATED-INVENTORY-SENTINEL" in path.read_bytes()
                    for path in link.backend.local_root.iterdir()
                )
            )
            self.assertEqual(
                b"".join(
                    link.store.read_chunks(
                        f"{self.admission.phone_id}/unselected/raw.db"
                    )
                ),
                b"",
            )
            self.assertEqual(
                len(link.backend.remote_objects.keys()), len(receipt.files)
            )
            local_control_bytes = link.backend.local_control_bytes
            for category, reader in (
                ("contacts", read_contacts),
                ("calls", read_calls),
                ("messages", read_messages),
            ):
                selected = {
                    file.role: file
                    for file in receipt.files
                    if file.category == category
                }
                objects = RangeObjects()
                index_main = self.root / f"{category}-main.index"
                index_wal = self.root / f"{category}-wal.index"
                binding = SourceBinding(
                    self.admission.owner_id,
                    self.admission.phone_id,
                    self.admission.attempt_id,
                    category,
                    str(uuid.uuid4()),
                )
                key = os.urandom(32)
                main = package_grouped_source(
                    link.source_reader(receipt, selected["main"]),
                    index_main,
                    binding,
                    "main",
                    objects,
                    key,
                    self.receipt_key,
                    sidecars=("wal", "shm") if "shm" in selected else ("wal",),
                )
                wal = package_grouped_source(
                    link.source_reader(receipt, selected["wal"]),
                    index_wal,
                    binding,
                    "wal",
                    objects,
                    key,
                    self.receipt_key,
                )
                source_set = seal_grouped_wal_set(main, wal, self.receipt_key)
                with open_grouped_wal_source(
                    source_set, index_main, index_wal, objects, key, self.receipt_key
                ) as db:
                    self.assertEqual(
                        db.execute(
                            "SELECT COUNT(*) FROM "
                            + (
                                "ABPerson"
                                if category == "contacts"
                                else "ZCALLRECORD"
                                if category == "calls"
                                else "message"
                            )
                        ).fetchone()[0],
                        1,
                    )

                def parse(
                    path: Path, store: RecordStore, *, category=category, reader=reader
                ):
                    if category == "contacts":
                        return reader(path, None, store)
                    from datetime import UTC, datetime

                    return reader(path, datetime(2001, 1, 1, tzinfo=UTC), None, store)

                local_store = RecordStore(self.root / f"{category}-local-record.sqlite")
                remote_store = RecordStore(
                    self.root / f"{category}-remote-record.sqlite"
                )
                try:
                    original = parse(self.root / f"{category}.sqlite3", local_store)
                    with patch(
                        "amplifai_phone.metadata._open_readonly",
                        side_effect=lambda _path, source_set=source_set, index_main=index_main, index_wal=index_wal, objects=objects, key=key: (
                            open_grouped_wal_source(
                                source_set,
                                index_main,
                                index_wal,
                                objects,
                                key,
                                self.receipt_key,
                            )
                        ),
                    ):
                        grouped = parse(self.root / "virtual" / category, remote_store)
                    self.assertEqual(grouped.rows_seen, original.rows_seen)
                    self.assertEqual(list(grouped.records), list(original.records))
                finally:
                    local_store.close()
                    remote_store.close()
            self.assertGreater(
                local_control_bytes, (self.root / "Manifest.db").stat().st_size
            )
            descriptor_bytes = sum(
                path.stat().st_size for path in self.root.glob("*.index")
            )
            review_bytes = sum(
                path.stat().st_size for path in self.root.glob("*-record.sqlite")
            )
            manifest_bytes = (self.root / "Manifest.db").stat().st_size
            self.measurements = {
                "selected_file_count": len(receipt.files),
                "selected_source_bytes": sum(file.size for file in receipt.files),
                "unselected_wire_bytes": len(b"UNSELECTED-PRIVATE-BYTES"),
                "local_control_bytes": local_control_bytes,
                "temporary_manifest_copy_bytes": manifest_bytes,
                "private_descriptor_index_bytes": descriptor_bytes,
                "sanitized_review_store_bytes": review_bytes,
                "conservative_local_sum_bytes": (
                    local_control_bytes
                    + manifest_bytes
                    + descriptor_bytes
                    + review_bytes
                ),
                "simulated_remote_ciphertext_bytes": link.backend.remote_ciphertext_bytes,
                "largest_device_read_bytes": wire.largest_read,
                "parser_rows_each_source": 1,
            }

    async def test_consent_identity_and_terminal_fail_closed(self) -> None:
        files = self.inputs()
        workspace = SessionWorkspace(self.root / "sessions")
        with workspace:
            self.authority.active = False
            with self.assertRaisesRegex(CaptureSeamError, "capture_consent_stale"):
                self.open_link(Wire(self.transcript(files)), workspace)
            self.authority.active = True
            for malformed, terminal in ((True, True), (False, False)):
                with self.subTest(malformed=malformed, terminal=terminal):
                    link = self.open_link(
                        Wire(
                            self.transcript(
                                files, malformed=malformed, terminal=terminal
                            )
                        ),
                        workspace,
                    )
                    with self.assertRaises(
                        (CaptureSeamError, asyncio.IncompleteReadError)
                    ):
                        await link.run_capture()
                    with self.assertRaisesRegex(
                        CaptureSeamError, "capture_not_completed"
                    ):
                        link.seal()
            link = self.open_link(Wire(self.transcript(files)), workspace)
            await link.run_capture()
            receipt = link.seal()
            with self.assertRaisesRegex(CaptureSeamError, "capture_receipt_invalid"):
                link.verify_receipt(replace(receipt, mac=123))
            self.authority.active = False
            with self.assertRaisesRegex(CaptureSeamError, "capture_consent_stale"):
                link.verify_receipt(receipt)
            self.authority.active = True
            fresh = self.open_link(Wire(self.transcript(files)), workspace)
            await fresh.run_capture()
            with self.assertRaisesRegex(CaptureSeamError, "capture_receipt_invalid"):
                fresh.verify_receipt(receipt)
            with self.assertRaisesRegex(CaptureSeamError, "capture_retry_ambiguous"):
                await link.run_capture()

    async def test_owner_phone_attempt_source_and_midstream_revocation(self) -> None:
        files = self.inputs()
        for field in ("owner_id", "phone_id", "attempt_id", "consent_receipt_id"):
            with self.subTest(field=field):
                workspace = SessionWorkspace(self.root / f"sessions-{field}")
                with workspace:
                    link = self.open_link(Wire(self.transcript(files)), workspace)
                    self.authority.admission = replace(
                        self.admission, **{field: str(uuid.uuid4())}
                    )
                    with self.assertRaisesRegex(
                        CaptureSeamError, "capture_consent_stale"
                    ):
                        await link.run_capture()
                    self.assertFalse(link.backend.remote_objects.keys())
                    self.authority.admission = self.admission
        with self.assertRaisesRegex(CaptureSeamError, "capture_admission_invalid"):
            replace(
                self.admission,
                categories=("calls", "contacts", "messages", "unapproved"),
            )
        with self.assertRaisesRegex(CaptureSeamError, "capture_admission_invalid"):
            replace(self.admission, cloud_source_approved=1)
        workspace = SessionWorkspace(self.root / "midstream")
        with workspace:
            wire = Wire(self.transcript(files))

            def revoke_if_file(payload: bytes) -> None:
                if b"SQLite format 3" in payload:
                    self.authority.active = False

            wire.after_read = revoke_if_file
            link = self.open_link(wire, workspace)
            with self.assertRaisesRegex(CaptureSeamError, "capture_consent_stale"):
                await link.run_capture()
            self.assertFalse(link.backend.remote_objects.keys())
            self.assertEqual(link.backend.local_control_bytes, 0)

    async def test_after_completion_mutation_or_leftover_copy_denies_seal(self) -> None:
        files = self.inputs()
        for mutate in ("replace", "sidecar", "extra_copy"):
            with self.subTest(mutate=mutate):
                workspace = SessionWorkspace(self.root / f"sessions-{mutate}")
                with workspace:
                    link = self.open_link(Wire(self.transcript(files)), workspace)
                    await link.run_capture()
                    receipt = link.seal()
                    target = next(
                        name for device, name, _ in files if device.endswith("sms.db")
                    )
                    if mutate == "extra_copy":
                        link.store.copy_item(
                            target, f"{self.admission.phone_id}/extra.db"
                        )
                    else:
                        if mutate == "sidecar":
                            target = next(
                                name
                                for device, name, _ in files
                                if device.endswith("sms.db-wal")
                            )
                        with link.store.open_write(
                            target, selected=True, scope=link.store.file_scope(target)
                        ) as writer:
                            writer.write(b"changed after completion")
                    with self.assertRaisesRegex(
                        CaptureSeamError, "capture_source_mutated"
                    ):
                        link.verify_receipt(receipt)

    async def test_early_disconnect_and_incomplete_object_cleanup(self) -> None:
        files = self.inputs()
        for case in ("disconnect", "object"):
            with self.subTest(case=case):
                workspace = SessionWorkspace(self.root / f"sessions-{case}")
                with workspace:
                    transcript = self.transcript(files)
                    wire = Wire(
                        transcript[:-25] if case == "disconnect" else transcript
                    )
                    link = self.open_link(wire, workspace)
                    if case == "object":
                        original_put = link.backend.put

                        def corrupt_object(
                            key: str,
                            payload: bytes,
                            *,
                            original_put=original_put,
                            backend=link.backend,
                        ) -> None:
                            original_put(key, payload)
                            if "/control/" not in key:
                                backend.remote_objects._path(key).write_bytes(
                                    b"incomplete"
                                )

                        link.backend.put = corrupt_object
                    with self.assertRaises(
                        (asyncio.IncompleteReadError, VirtualStoreError)
                    ):
                        await link.run_capture()
                    self.assertFalse(link.backend.remote_objects.keys())
                    self.assertEqual(link.backend.local_control_bytes, 0)
                    with self.assertRaisesRegex(
                        CaptureSeamError, "capture_not_completed"
                    ):
                        link.seal()

    async def test_encrypted_backup_requires_separate_key_path_qualification(
        self,
    ) -> None:
        files = self.inputs()
        encrypted = []
        for device, name, payload in files:
            if device == "Manifest.plist":
                payload = plistlib.dumps(
                    {"IsEncrypted": True, "BackupKeyBag": b"LOCAL-ONLY"}
                )
            encrypted.append((device, name, payload))
        workspace = SessionWorkspace(self.root / "encrypted")
        with workspace:
            link = self.open_link(Wire(self.transcript(encrypted)), workspace)
            await link.run_capture()
            with self.assertRaisesRegex(
                CaptureSeamError, "capture_encrypted_unqualified"
            ):
                link.seal()
            self.assertFalse(
                any(
                    b"LOCAL-ONLY" in ciphertext
                    for ciphertext in link.backend.remote_objects.ciphertexts()
                )
            )

    async def test_manifest_control_wal_stays_local_and_is_parsed(self) -> None:
        files = self.inputs(manifest_wal=True)
        workspace = SessionWorkspace(self.root / "manifest-sidecar")
        with workspace:
            link = self.open_link(Wire(self.transcript(files)), workspace)
            await link.run_capture()
            receipt = link.seal()
            self.assertIn("Manifest.db-wal", dict(receipt.control_sha256))
            self.assertIn("Manifest.db-shm", dict(receipt.control_sha256))
            self.assertFalse(
                any(
                    b"WAL-ONLY-CONTROL" in value
                    for value in link.backend.remote_objects.ciphertexts()
                )
            )

    async def test_missing_corrupt_reordered_and_stale_source_are_denied(self) -> None:
        cases = ("missing", "corrupt", "wrong_scope", "stale_control", "reordered_wire")
        parent = self.root
        for case in cases:
            with self.subTest(case=case):
                self.root = parent / case
                self.root.mkdir()
                files = self.inputs(stale_control=case == "stale_control")
                if case == "missing":
                    files = [
                        value for value in files if not value[0].endswith("sms.db-wal")
                    ]
                if case == "reordered_wire":
                    files = [
                        ("CameraDomain-Unrelated", name, payload)
                        if device.endswith("sms.db-wal")
                        else (device, name, payload)
                        for device, name, payload in files
                    ]
                workspace = SessionWorkspace(self.root / "sessions")
                with workspace:
                    link = self.open_link(Wire(self.transcript(files)), workspace)
                    await link.run_capture()
                    if case == "corrupt":
                        key = next(
                            key
                            for key in link.backend.remote_objects.keys()  # noqa: SIM118 - object store
                            if "/messages-wal/" in key
                        )
                        link.backend.remote_objects._path(key).write_bytes(b"corrupt")
                    elif case == "wrong_scope":
                        source = next(
                            name
                            for device, name, _ in files
                            if device.endswith("sms.db")
                        )
                        wrong = next(
                            name
                            for device, name, _ in files
                            if device.endswith("CallHistory.storedata")
                        )
                        link.store.copy_item(wrong, source)
                    expected = {
                        "missing": "virtual_missing",
                        "corrupt": "virtual_storage",
                        "wrong_scope": "capture_source_mutated",
                        "stale_control": "capture_source_length",
                        "reordered_wire": "capture_source_binding",
                    }[case]
                    with self.assertRaises(
                        (CaptureSeamError, VirtualStoreError)
                    ) as denied:
                        link.seal()
                    self.assertEqual(denied.exception.code, expected)
        self.root = parent

    async def test_wrong_actor_cannot_decrypt_existing_selected_object(self) -> None:
        files = self.inputs()
        workspace = SessionWorkspace(self.root / "cipher-binding")
        with workspace:
            link = self.open_link(Wire(self.transcript(files)), workspace)
            await link.run_capture()
            link.seal()
            object_id = next(
                key
                for key in link.backend.remote_objects.keys()  # noqa: SIM118 - object store
                if "/contacts-main/" in key
            )
            for field in ("owner_id", "phone_id", "attempt_id"):
                other = replace(self.admission, **{field: str(uuid.uuid4())})
                with self.subTest(field=field):
                    if field == "attempt_id":
                        with self.assertRaisesRegex(
                            CaptureSeamError, "capture_key_invalid"
                        ):
                            EncryptedAttemptBackend(
                                workspace,
                                other,
                                self.object_key,
                                link.backend.remote_objects,
                            )
                    else:
                        backend = EncryptedAttemptBackend(
                            workspace,
                            other,
                            self.object_key,
                            link.backend.remote_objects,
                        )
                        with self.assertRaises(OSError):
                            backend.get(object_id)
            cross_source = object_id.replace("/contacts-main/", "/messages-main/")
            link.backend.remote_objects.put_exact(
                cross_source, link.backend.remote_objects.get_exact(object_id)
            )
            with self.assertRaises(OSError):
                link.backend.get(cross_source)


if __name__ == "__main__":
    unittest.main()
