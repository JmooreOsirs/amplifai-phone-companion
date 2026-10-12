"""Isolated lazy 644 MiB single-stage measurement; no provider endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import json
import plistlib
import resource
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_offline_capture_streaming_volume as volume_fixture
import test_offline_cloud_capture as capture_fixture
import test_offline_single_stage_capture as stage_fixture
from amplifai_phone.backup_watchdog import RECEIVE_CHUNK_BYTES, BackupWatchdog
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN
from amplifai_phone.metadata import read_contacts
from amplifai_phone.offline_grouped_object_vfs import open_grouped_wal_source
from amplifai_phone.offline_single_stage_capture import (
    ProcessingRanges,
    SingleStageCaptureLink,
)
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace
from large_backup_probe import archived_entry


async def run_volume(scale: int) -> dict[str, object]:
    fixture = capture_fixture.CaptureFixture(
        "test_real_dispatch_projection_readback_and_parser_parity"
    )
    fixture.setUp()
    started = time.monotonic()
    measurement: dict[str, object] | None = None
    try:
        files: list[tuple[str, str, Path | bytes]] = []
        manifest = fixture.root / "Manifest.db"
        with closing(sqlite3.connect(manifest)) as control, control:
            control.execute(
                "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, "
                "flags INTEGER, file BLOB)"
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
        for name, content in (
            ("Manifest.db", manifest),
            ("Manifest.plist", plistlib.dumps({"IsEncrypted": False})),
            ("Info.plist", plistlib.dumps({})),
            ("Status.plist", plistlib.dumps({"SnapshotState": "finished"})),
        ):
            files.append((name, f"{fixture.admission.phone_id}/{name}", content))
        files.append(
            (
                "CameraDomain-Unrelated",
                f"{fixture.admission.phone_id}/unselected/raw.db",
                b"UNSELECTED-SYNTHETIC-BYTES",
            )
        )
        objects = stage_fixture.FinalObjects(
            fixture.root / "one-final-namespace", fixture.admission.attempt_id
        )
        journal = stage_fixture.Journal(fixture.admission, objects)
        index_root = fixture.root / "private-index"
        index_root.mkdir(mode=0o700)
        wire = volume_fixture.LazyWire(files, replacement=False)
        workspace = SessionWorkspace(fixture.root / "workspace")
        with workspace:
            watchdog = BackupWatchdog()
            watchdog.start()
            link = SingleStageCaptureLink(
                wire,
                workspace,
                watchdog,
                fixture.admission,
                fixture.authority,
                fixture.object_key,
                fixture.receipt_key,
                journal,
                objects,
                index_root,
                journal.start_challenge,
                journal.terminal_challenge,
                "A1B2C3D4E5F6",
            )
            await link.run_capture()
            receipt, sources = link.finalize()
            selected_bytes = sum(file.size for file in receipt.files)
            if (
                len(receipt.files) != 6
                or len(objects.keys()) != link.backend.native_groups
            ):
                raise AssertionError("Single-stage source coverage changed")
            review = RecordStore(fixture.root / "single-stage-review.sqlite3")
            try:
                epoch = journal.begin_processing()
                contacts = sources["contacts"]
                with patch(
                    "amplifai_phone.metadata._open_readonly",
                    side_effect=lambda _path: open_grouped_wal_source(
                        contacts.source_set,
                        contacts.main_index,
                        contacts.wal_index,
                        ProcessingRanges(link, "contacts", epoch),
                        fixture.object_key,
                        fixture.receipt_key,
                    ),
                ):
                    result = read_contacts(
                        fixture.root / "virtual-contacts", None, review
                    )
                rows = result.rows_seen
            finally:
                review.close()
            members = sorted(
                (file.category, file.role, file.size, file.sha256)
                for file in receipt.files
            )
            source_set_sha256 = hashlib.sha256(
                json.dumps(members, separators=(",", ":")).encode()
            ).hexdigest()
            measurement = {
                "selected_bytes": selected_bytes,
                "selected_file_count": len(receipt.files),
                "source_set_sha256": source_set_sha256,
                "source_members": members,
                "final_object_count": len(objects.keys()),
                "final_ciphertext_bytes": objects.ciphertext_bytes,
                "temporary_ciphertext_high_water_bytes": objects.high_water,
                "native_groups": link.backend.native_groups,
                "s3_put_calls": objects.put_calls,
                "s3_get_calls": objects.get_calls,
                "s3_get_bytes": objects.get_bytes,
                "s3_range_calls": objects.range_calls,
                "s3_range_bytes": objects.range_bytes,
                "s3_delete_calls": objects.delete_calls,
                "gateway_calls": journal.gateway_calls,
                "db_round_trips": journal.db_round_trips,
                "max_device_read_bytes": wire.largest_read,
                "max_virtual_buffer_bytes": link.store.max_buffered_bytes,
                "max_group_input_bytes": link.backend.max_input_bytes,
                "max_group_ciphertext_bytes": link.backend.max_sealed_bytes,
                "max_backend_live_buffer_bytes": link.backend.max_buffered_bytes,
                "local_control_bytes": link.backend.local_control_bytes,
                "local_index_bytes": sum(
                    path.stat().st_size for path in index_root.glob("*.index")
                ),
                "local_ledger_bytes": sum(
                    path.stat().st_size for path in index_root.glob("*.ledger.sqlite3")
                ),
                "local_review_bytes": (fixture.root / "single-stage-review.sqlite3")
                .stat()
                .st_size,
                "parser_contact_rows_seen": rows,
                "whole_process_peak_rss_bytes": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
                "local_wall_seconds": round(time.monotonic() - started, 3),
            }
            if wire.largest_read > RECEIVE_CHUNK_BYTES:
                raise AssertionError("DeviceLink read exceeded 128 KiB")
            objects.cleanup()
            if objects.keys():
                raise AssertionError("Disposable final objects survived cleanup")
            measurement["synthetic_object_cleanup_verified"] = True
            return measurement
    finally:
        fixture.tearDown()
        fixture.temporary.cleanup()
        if measurement is not None:
            measurement["temporary_directory_removed"] = not fixture.root.exists()


class SingleStageVolumeTest(unittest.TestCase):
    def test_lazy_retained_volume_crosses_incident_with_one_namespace(self) -> None:
        result = subprocess.run(
            [sys.executable, __file__, "--probe", "170"],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        measurement = json.loads(result.stdout)
        self.assertGreater(measurement["selected_bytes"], 616_819_240)
        self.assertEqual(
            measurement["native_groups"], measurement["final_object_count"]
        )
        self.assertLessEqual(measurement["max_virtual_buffer_bytes"], 1024 * 1024)
        self.assertLessEqual(measurement["max_device_read_bytes"], RECEIVE_CHUNK_BYTES)
        self.assertEqual(measurement["parser_contact_rows_seen"], 1)
        self.assertLessEqual(
            sum(
                measurement[name]
                for name in ("s3_put_calls", "s3_get_calls", "s3_range_calls")
            ),
            4096,
        )
        self.assertTrue(measurement["synthetic_object_cleanup_verified"])
        self.assertTrue(measurement["temporary_directory_removed"])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        print(json.dumps(asyncio.run(run_volume(int(sys.argv[2])))))
    else:
        unittest.main()
