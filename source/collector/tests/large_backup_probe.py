"""Opt-in, real >1 GiB synthetic wire/SQLite probe. Never opens a phone/owner backup.

Uses actual socket backpressure and pinned ServiceConnection/DeviceLink. Local
throughput is not a USB/phone-speed benchmark. No sparse throughput fixtures.
"""

from __future__ import annotations

from contextlib import closing

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import plistlib
import resource
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from types import SimpleNamespace

from amplifai_phone.backup_stream import StreamedDeviceLink
from amplifai_phone.backup_watchdog import BackupWatchdog, observe_backup_receives
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN, parse_selected_backup
from amplifai_phone.workspace import MIN_FREE_BYTES, SessionWorkspace, WorkspaceError
from pymobiledevice3.service_connection import ServiceConnection, build_plist
from pymobiledevice3.services.device_link import DeviceLink
from pymobiledevice3.services.mobilebackup2 import (
    BackupSelectionRule,
    Mobilebackup2Service,
)

BLOCK_BYTES = 128 * 1024
PHONE = "SYNTHETIC-PHONE"
LEGACY_REVISION = "4cd847047916f40cf1a5bcde0bbb2ecf5cd4cefa"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def archived_entry(relative: str, size: int) -> bytes:
    # Real MBFile archive decoded by the pinned SDK, not a mocked Entry.
    return plistlib.dumps(
        {
            "$archiver": "NSKeyedArchiver",
            "$version": 100000,
            "$objects": [
                "$null",
                {
                    "$class": plistlib.UID(2),
                    "RelativePath": plistlib.UID(3),
                    "Size": size,
                    "Mode": 0o100600,
                    "LastModified": 1_700_000_000,
                    "LastStatusChange": 1_700_000_000,
                    "Birth": 1_700_000_000,
                    "GroupID": 0,
                    "UserID": 0,
                },
                {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]},
                relative,
            ],
            "$top": {"root": plistlib.UID(1)},
        },
        fmt=plistlib.FMT_BINARY,
    )


def fixtures(root: Path) -> tuple[list[tuple[str, str, Path]], sqlite3.Connection]:
    contacts, calls, messages = (
        root / name for name in ("contacts.sqlite", "calls.sqlite", "sms.db")
    )
    # Dense SQLite pages with actual writes. A fixed synthetic block is not private data.
    payload = bytes(range(256)) * (1024 * 1024 // 256)
    with closing(sqlite3.connect(contacts)) as db, db:
        db.executescript(
            "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-2048;"
            "CREATE TABLE ABPerson (First TEXT, Last TEXT);"
            "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT);"
            "INSERT INTO ABPerson VALUES ('Synthetic', 'Contact');"
            "INSERT INTO ABMultiValue VALUES (1, 3, '+12025550101');"
            "CREATE TABLE synthetic_ignored_content (payload BLOB);"
        )
        db.executemany(
            "INSERT INTO synthetic_ignored_content VALUES (?)",
            ((payload,) for _ in range(1025)),
        )
    with closing(sqlite3.connect(calls)) as db, db:
        db.executescript(
            "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER);"
            "INSERT INTO ZCALLRECORD VALUES (800000000, 65, '+12025550101', 1, 1);"
        )
    sms = sqlite3.connect(messages)
    sms.executescript(
        "PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;"
        "CREATE TABLE handle (id TEXT);"
        "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, handle_id INTEGER, text TEXT);"
    )
    sms.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    sms.execute("INSERT INTO handle VALUES ('+12025550101')")
    sms.executemany(
        "INSERT INTO message VALUES (?, 'SMS', 0, 1, 'SYNTHETIC-BODY-EXCLUDED')",
        ((800000000 + index,) for index in range(2)),
    )
    sms.commit()
    entries = [(DATABASES["contacts"], contacts), (DATABASES["calls"], calls)]
    entries.extend(
        (DATABASES["messages"] + suffix, Path(str(messages) + suffix))
        for suffix in ("", "-wal", "-shm")
    )
    manifest = root / "Manifest.db"
    files = []
    with closing(sqlite3.connect(manifest)) as db, db:
        db.executescript(
            "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-2048;"
            "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB);"
            "CREATE TABLE synthetic_ignored_manifest_payload (payload BLOB);"
        )
        for relative, source in entries:
            file_id = hashlib.sha1((HOME_DOMAIN + "-" + relative).encode()).hexdigest()
            db.execute(
                "INSERT INTO Files VALUES (?, ?, ?, 1, ?)",
                (
                    file_id,
                    HOME_DOMAIN,
                    relative,
                    archived_entry(relative, source.stat().st_size),
                ),
            )
            files.append(
                (
                    HOME_DOMAIN + "-" + relative,
                    f"{PHONE}/{file_id[:2]}/{file_id}",
                    source,
                )
            )
        db.executemany(
            "INSERT INTO synthetic_ignored_manifest_payload VALUES (?)",
            ((payload,) for _ in range(129)),
        )
    for name, value in (
        (
            "Manifest.plist",
            {"IsEncrypted": False, "Lockdown": {"ProductVersion": "18.0"}},
        ),
        ("Status.plist", {}),
        ("Info.plist", {}),
    ):
        path = root / name
        path.write_bytes(plistlib.dumps(value))
        files.append((name, f"{PHONE}/{name}", path))
    files.append(("Manifest.db", f"{PHONE}/Manifest.db", manifest))
    assert contacts.stat().st_size > 1024**3
    assert manifest.stat().st_size > 128 * 1024**2
    # Verify the message rows truly are WAL-only in the source main database.
    with closing(sqlite3.connect(messages.as_uri() + "?immutable=1", uri=True)) as immutable, immutable:
        assert immutable.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 0
    return files, sms


def legacy_workspace_class():
    source = subprocess.run(
        ["git", "show", LEGACY_REVISION + ":collector/amplifai_phone/workspace.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    module = types.ModuleType("amplifai_phone.legacy_workspace_probe")
    sys.modules[module.__name__] = module
    exec(compile(source, "owned-prior-workspace-probe", "exec"), module.__dict__)  # noqa: S102 - exact owned Git revision, never external input
    return module.SessionWorkspace


async def transfer(files, parent: Path, *, legacy: bool) -> dict:
    left, right = socket.socketpair()
    left.setblocking(False)
    right.setblocking(False)
    connection = ServiceConnection(left)
    _reader, sender = await asyncio.open_connection(sock=right)
    workspace = (
        legacy_workspace_class()(parent / "legacy-sessions")
        if legacy
        else SessionWorkspace(parent / "fixed-sessions")
    )
    watchdog = BackupWatchdog()
    await observe_backup_receives(SimpleNamespace(service=connection), watchdog)
    rules = (
        *Mobilebackup2Service.resolve_backup_selection(
            ("contacts", "call_history", "sms")
        ),
        BackupSelectionRule(HOME_DOMAIN, DATABASES["messages"] + "-wal"),
        BackupSelectionRule(HOME_DOMAIN, DATABASES["messages"] + "-shm"),
    )
    filter_callback = Mobilebackup2Service.selection_filter_callback(rules)

    def preserve(name, device):
        return Mobilebackup2Service.should_preserve_backup_file(
            name, device, filter_callback
        )

    progress, percentages, processing = [], [], []
    requests = []
    started = time.monotonic()
    selected = sum(source.stat().st_size for _, _, source in files)
    discarded = 2 * 64 * 1024**2

    async def prefixed(value: str) -> None:
        encoded = value.encode()
        sender.write(struct.pack(">I", len(encoded)) + encoded)

    async def send_file(device_name: str, file_name: str, source: Path | None) -> None:
        await prefixed(device_name)
        await prefixed(file_name)
        length = source.stat().st_size if source else 64 * 1024**2
        remaining = length
        # Legacy gets small device frames to avoid deliberately allocating >1 GiB.
        # Fixed gets one full >1 GiB frame and must independently bound its reads.
        frame_size = 4 * 1024**2 if legacy else length
        reader = source.open("rb") if source else None
        try:
            while remaining:
                frame_remaining = min(frame_size, remaining)
                sender.write(struct.pack(">IB", frame_remaining + 1, 0xC))
                while frame_remaining:
                    count = min(BLOCK_BYTES, frame_remaining)
                    payload = reader.read(count) if reader else b"d" * count
                    assert len(payload) == count
                    sender.write(payload)
                    await sender.drain()
                    remaining -= count
                    frame_remaining -= count
            sender.write(struct.pack(">IB", 1, 0))
        finally:
            if reader:
                reader.close()

    async def produce() -> None:
        for directory in sorted({str(Path(name).parent) for _, name, _ in files}):
            sender.write(build_plist(["DLMessageCreateDirectory", directory]))
        sender.write(build_plist(["DLMessageUploadFiles", "", 7.0]))
        for device_name, file_name, source in files:
            await send_file(device_name, file_name, source)
        for index in range(2):
            await send_file(
                f"CameraDomain-Library/Unselected/{index}",
                f"{PHONE}/discarded/{index}",
                None,
            )
        sender.write(b"\x00" * 4)
        sender.write(build_plist(["DLMessageProcessMessage", {"ErrorCode": 0}]))
        await sender.drain()

    directory = None
    try:
        with workspace as directory:
            cls = DeviceLink if legacy else StreamedDeviceLink
            kwargs = {"preserve_file": preserve}
            if not legacy:
                kwargs.update(
                    workspace=workspace,
                    watchdog=watchdog,
                    transfer_callback=progress.append,
                )
            link = cls(connection, directory, **kwargs)
            original_recv = link._recvall

            async def measured_recv(size):
                requests.append(size)
                return await original_recv(size)

            link._recvall = measured_recv
            watchdog.start()
            task = asyncio.create_task(produce())
            try:
                await link.dl_loop(percentages.append)
                await task
                link.cleanup_discarded_files()
                elapsed = time.monotonic() - started
                retained = [directory / name for _, name, _ in files]
                logical = sum(path.stat().st_size for path in retained)
                allocated = sum(path.stat().st_blocks * 512 for path in retained)
                assert logical == selected
                assert all(
                    file_hash(source) == file_hash(directory / name)
                    for _, name, source in files
                )
                assert not list((directory / PHONE / "discarded").glob("*"))
                if legacy:
                    try:
                        workspace.check_bound()
                    except RuntimeError as error:
                        assert error.code == "workspace_size_limit"
                        return {
                            "mode": "legacy-real-stream",
                            "expected_rejection": error.code,
                            "retained_logical_bytes": logical,
                            "retained_allocated_bytes": allocated,
                            "wire_payload_bytes": selected + discarded,
                            "elapsed_seconds": elapsed,
                            "source_revision": LEGACY_REVISION,
                        }
                    raise AssertionError(
                        "Old capacity policy did not reject the real >1 GiB session"
                    )
                watchdog.backup_completed()
                workspace.check_bound()
                capture = parse_selected_backup(
                    directory / PHONE,
                    "",
                    workspace=workspace,
                    bound_callback=workspace.check_bound,
                    transfer_callback=processing.append,
                )
                assert (
                    len(capture.contacts.records),
                    len(capture.calls.records),
                    len(capture.messages.records),
                ) == (1, 1, 2)
                assert capture.missing_sources == ()
                assert "SYNTHETIC-BODY-EXCLUDED" not in repr(capture)
                assert not list(directory.glob("amplifai-db-*"))
                assert percentages == [7.0]
                assert (
                    len(progress) > 1
                    and progress[-1]["receivedBytes"] == selected + discarded
                )
                assert (
                    progress[-1]["retainedBytes"] == selected
                    and progress[-1]["discardedBytes"] == discarded
                )
                assert max(requests) <= BLOCK_BYTES
                return {
                    "mode": "fixed-real-stream",
                    "retained_logical_bytes": logical,
                    "retained_allocated_bytes": allocated,
                    "wire_payload_bytes": selected + discarded,
                    "discarded_payload_bytes": discarded,
                    "selected_file_count": len(files),
                    "elapsed_transfer_seconds": elapsed,
                    "local_wire_mib_per_second": (selected + discarded)
                    / elapsed
                    / 1024**2,
                    "max_requested_receive_bytes": max(requests),
                    "percentages": percentages,
                    "transfer_events": len(progress),
                    "processing_events": len(processing),
                    "processed_copy_bytes": processing[-1]["processedBytes"],
                    "capture_records": {
                        "contacts": 1,
                        "calls": 1,
                        "messages_wal_only": 2,
                    },
                    "body_excluded": True,
                    "parsing_copies_removed": True,
                    "peak_rss_bytes": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss,
                    "synthetic_transport_only_not_phone_speed": True,
                }
            finally:
                if not task.done():
                    task.cancel()
                link.cleanup_discarded_files()
        raise AssertionError("Probe did not return a result")
    finally:
        sender.close()
        await sender.wait_closed()
        await connection.close()
        if directory is not None:
            assert not directory.exists()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--skip-legacy", action="store_true",
                        help="reuse an existing legacy-red receipt when qualifying selected compiled code")
    args = parser.parse_args()
    assert importlib.metadata.version("pymobiledevice3") == "10.4.0"
    assert importlib.metadata.version("pyiosbackup") == "0.2.4"
    if shutil.disk_usage(tempfile.gettempdir()).free < MIN_FREE_BYTES + 4 * 1024**3:
        raise WorkspaceError(
            "Large synthetic qualification needs 6 GiB real free space",
            code="workspace_low_space",
        )
    with tempfile.TemporaryDirectory(prefix="amplifai-large-synthetic-") as temporary:
        root = Path(temporary).resolve()
        os.chmod(root, 0o700)
        sources = root / "synthetic-sources"
        sources.mkdir(mode=0o700)
        print(json.dumps({"phase": "generating-dense-synthetic-sqlite"}), flush=True)
        files, sms = fixtures(sources)
        try:
            old = None
            if not args.skip_legacy:
                print(json.dumps({"phase": "legacy-real-wire-red"}), flush=True)
                old = await transfer(files, root, legacy=True)
                print(json.dumps(old, sort_keys=True), flush=True)
            print(json.dumps({"phase": "fixed-real-wire-and-parser"}), flush=True)
            fixed = await transfer(files, root, legacy=False)
            print(json.dumps(fixed, sort_keys=True), flush=True)
            receipt = {
                "pymobiledevice3": "10.4.0",
                "pyiosbackup": "0.2.4",
                "legacy": old,
                "fixed": fixed,
                "owned_sessions_cleaned": True,
                "private_phone_data_used": False,
            }
        finally:
            sms.close()
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps({"phase": "complete", "owned_fixture_removed": not root.exists()}),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
