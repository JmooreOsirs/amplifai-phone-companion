"""Dense >800 MB fictional encrypted Messages export through production parsing.

No device, existing backup or account is accessed. The one-page exception must
retain all 302,304 metadata rows while ignoring the synthetic body payload.
"""
from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import sqlite3
import struct
import tempfile
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
from packaging.version import Version
from pyiosbackup import Backup
from pyiosbackup.keybag import Keybag
from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3

from amplifai_phone.ios_backup import DATABASES, _parse_entries
from amplifai_phone.metadata import RETAINED_HISTORY_START
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace
from qualify_frozen_encrypted_worker import SCHEMAS

ROWS = 302_304
PAGE = 4096
CLASS_KEY = b"c" * 32
FILE_KEY = b"f" * 32
WRAPPED_KEY = struct.pack("<I", 1) + aes_key_wrap(CLASS_KEY, FILE_KEY)


def archive(relative: str, size: int, digest: bytes) -> bytes:
    objects = ["$null", {
        "$class": plistlib.UID(2), "RelativePath": relative,
        "Size": size, "Mode": 0o100600, "LastModified": 0, "LastStatusChange": 0,
        "Birth": 0, "GroupID": 0, "UserID": 0,
    }, {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]}]
    for name, value in (("Digest", digest), ("EncryptionKey", WRAPPED_KEY)):
        position = len(objects)
        objects[1][name] = plistlib.UID(position)
        objects.extend(({"$class": plistlib.UID(position + 1), "NS.data": value},
                        {"$classname": "NSMutableData", "$classes": ["NSMutableData", "NSData", "NSObject"]}))
    return plistlib.dumps({
        "$archiver": "NSKeyedArchiver", "$version": 100000,
        "$top": {"root": plistlib.UID(1)},
        "$objects": objects,
    }, fmt=plistlib.FMT_BINARY)


def encrypted_copy(source: Path, destination: Path) -> bytes:
    digest = hashlib.sha1()
    encryptor = Cipher(algorithms.AES(FILE_KEY), modes.CBC(bytes(16))).encryptor()
    padder = padding.PKCS7(128).padder()
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        while chunk := incoming.read(256 * 1024):
            encrypted = encryptor.update(padder.update(chunk))
            outgoing.write(encrypted)
            digest.update(encrypted)
        encrypted = encryptor.update(padder.finalize()) + encryptor.finalize()
        outgoing.write(encrypted)
        digest.update(encrypted)
    return digest.digest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--growth-pages", type=int, choices=(1, 2), default=1)
    growth_pages = parser.parse_args().growth_pages
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="amplifai-one-page-qualification-") as temporary:
        workspace = SessionWorkspace(Path(temporary).resolve() / "sessions")
        with workspace as session:
            backup_path, extracted = session / "backup", session / "extracted"
            backup_path.mkdir(mode=0o700)
            extracted.mkdir(mode=0o700)
            manifest_path = backup_path / "Manifest.db"
            sizes = {}
            with closing(sqlite3.connect(manifest_path)) as manifest, manifest:
                manifest.execute("CREATE TABLE Files(fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
                for category, statements in SCHEMAS.items():
                    path = session / (category + ".sqlite")
                    with closing(sqlite3.connect(path)) as db, db:
                        db.executescript("PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-4096;")
                        for statement in statements:
                            db.execute(statement)
                        if category == "messages":
                            db.execute("DELETE FROM message")
                            db.executemany("INSERT INTO message VALUES (?, 800000000, 'SMS', 0, 1, NULL)",
                                           ((index,) for index in range(1, ROWS + 1)))
                            db.execute("CREATE TABLE fictional_body_payload(value BLOB)")
                            # Dense writes, not a sparse throughput fixture. Parsing never selects this table.
                            block = bytes(range(256)) * 4096
                            db.executemany("INSERT INTO fictional_body_payload VALUES (?)", ((block,) for _ in range(768)))
                    actual = path.stat().st_size
                    declared = actual - growth_pages * PAGE if category == "messages" else actual
                    relative = DATABASES[category]
                    file_id = hashlib.sha1(("HomeDomain-" + relative).encode()).hexdigest()
                    raw = backup_path / file_id[:2] / file_id
                    raw.parent.mkdir(exist_ok=True)
                    digest = encrypted_copy(path, raw)
                    manifest.execute("INSERT INTO Files VALUES (?, 'HomeDomain', ?, 1, ?)",
                                     (file_id, relative, archive(relative, declared, digest)))
                    sizes[category] = {"plaintext": actual, "declared": declared, "ciphertext": raw.stat().st_size}
                    path.unlink()
            assert sizes["messages"]["plaintext"] > 800_000_000
            manifest = ManifestDbSqlite3(manifest_path)
            store = RecordStore(session / "records.sqlite", workspace)
            try:
                backup = Backup(backup_path, manifest,
                                SimpleNamespace(is_encrypted=True, product_version=Version("18.0")),
                                {"SnapshotState": "finished"}, {"Product Version": "18.0"}, Keybag({1: CLASS_KEY}))
                parse_started = time.monotonic()
                capture = _parse_entries(backup, extracted, datetime(2026, 10, 22, tzinfo=UTC),
                                         RETAINED_HISTORY_START, workspace=workspace, record_store=store,
                                         bound_callback=workspace.check_bound)
                parse_seconds = time.monotonic() - parse_started
                assert capture.missing_sources == () and capture.source_diagnostics == (), {
                    "missing": capture.missing_sources, "reasons": capture.unavailable_reasons,
                    "diagnostics": capture.source_diagnostics,
                }
                assert store.counts == {"contacts": 1, "calls": 1, "messages": ROWS}
                assert capture.messages.rows_seen == ROWS
                assert capture.messages.records[0].source_id == 1
                assert capture.messages.records[-1].source_id == ROWS
            finally:
                store.db.close()
                manifest._conn.close()
        assert not session.exists()
    print(json.dumps({"status": "pass", "fictional": True, "deviceOrAccountAccess": False,
                      "messages": ROWS, "growthPages": growth_pages, "sizes": sizes, "parseSeconds": round(parse_seconds, 3),
                      "totalSeconds": round(time.monotonic() - started, 3), "ownedSessionCleanup": "complete"}, sort_keys=True))


if __name__ == "__main__":
    main()
