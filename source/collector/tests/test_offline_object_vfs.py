"""Synthetic-only APSW object VFS qualification; never contacts a device/cloud."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    import apsw  # Optional source-only candidate dependency.
except ModuleNotFoundError:
    apsw = None

from amplifai_phone.metadata import (
    RETAINED_HISTORY_START,
    read_calls,
    read_contacts,
    read_messages,
)
from amplifai_phone.record_store import RecordStore
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if apsw is not None:
    from amplifai_phone.offline_object_vfs import (
        CACHE_BYTES,
        CHUNK_BYTES,
        EncryptedChunk,
        EncryptedRangeReader,
        EncryptedSource,
        FrozenWalSet,
        ObjectVfsError,
        SourceBinding,
        chunk_aad,
        open_frozen_wal_source,
        open_object_source,
        seal_frozen_wal_set,
    )


class LocalCipherObjects:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir()

    def put(self, name: str, ciphertext: bytes) -> None:
        (self.root / name).write_bytes(ciphertext)

    def get(self, name: str, max_bytes: int) -> bytes:
        with (self.root / name).open("rb") as stream:
            value = stream.read(max_bytes + 1)
        if len(value) > max_bytes:
            raise ValueError("Synthetic object exceeded its bounded range")
        return value

    def remove(self, name: str) -> None:
        (self.root / name).unlink()

    def alter(self, name: str) -> None:
        path = self.root / name
        value = path.read_bytes()
        path.write_bytes(bytes([value[0] ^ 1]) + value[1:])


@unittest.skipIf(apsw is None, "Offline APSW candidate is not in the release lock")
class ObjectVfsOfflineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-vfs-offline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = LocalCipherObjects(self.root / "objects")
        self.key = os.urandom(32)
        self.owner = str(uuid.uuid4())
        self.phone = str(uuid.uuid4())
        self.attempt = str(uuid.uuid4())
        self.virtual_root = self.root / "virtual"
        self.virtual_root.mkdir()

    def create_source(self, source: str, page_size: int, *, rows: int = 80) -> Path:
        path = self.root / f"{source}.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(f"PRAGMA page_size={page_size}")
            if source == "contacts":
                db.execute("CREATE TABLE ABPerson (First TEXT, Last TEXT, Note BLOB)")
                db.execute(
                    "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)"
                )
                db.executemany(
                    "INSERT INTO ABPerson VALUES (?, ?, ?)",
                    (
                        (
                            f"Person {index}",
                            "Example",
                            b"PRIVATE-NOTE-" + os.urandom(16_384),
                        )
                        for index in range(rows)
                    ),
                )
                db.executemany(
                    "INSERT INTO ABMultiValue VALUES (?, 3, ?)",
                    ((index + 1, f"+1202555{index:04d}") for index in range(rows)),
                )
                db.executemany(
                    "INSERT INTO ABMultiValue VALUES (?, 4, ?)",
                    (
                        (index + 1, f"person{index}@example.test")
                        for index in range(0, rows, 4)
                    ),
                )
            elif source == "calls":
                db.execute(
                    "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, "
                    "ZORIGINATED INTEGER, ZANSWERED INTEGER, Opaque BLOB)"
                )
                db.executemany(
                    "INSERT INTO ZCALLRECORD VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        (
                            800_000_000 + index,
                            30,
                            f"+1202555{index:04d}",
                            index % 2,
                            1,
                            os.urandom(16_384),
                        )
                        for index in range(rows)
                    ),
                )
            else:
                db.execute("CREATE TABLE handle (id TEXT)")
                db.execute(
                    "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, "
                    "handle_id INTEGER, text BLOB, attributedBody BLOB)"
                )
                db.execute(
                    "CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)"
                )
                db.execute(
                    "CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)"
                )
                db.executemany(
                    "INSERT INTO handle VALUES (?)",
                    ((f"+1202555{index:04d}",) for index in range(rows)),
                )
                db.executemany(
                    "INSERT INTO message VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        (
                            800_000_000 + index,
                            "SMS",
                            index % 2,
                            index + 1,
                            b"PRIVATE-MESSAGE-" + os.urandom(16_384),
                            b"PRIVATE-ATTRIBUTED",
                        )
                        for index in range(rows)
                    ),
                )
                db.executemany(
                    "INSERT INTO chat_handle_join VALUES (7, ?)", [(1,), (2,)]
                )
                db.execute("INSERT INTO chat_message_join VALUES (7, 1)")
        self.assertGreater(path.stat().st_size, CACHE_BYTES * 4)
        return path

    def package(
        self, path: Path, source: str, *, sidecars: tuple[str, ...] = ()
    ) -> EncryptedSource:
        binding = SourceBinding(self.owner, self.phone, self.attempt, source)
        full = hashlib.sha256()
        chunks = []
        size = 0
        with path.open("rb") as stream:
            for index, plaintext in enumerate(
                iter(lambda: stream.read(CHUNK_BYTES), b"")
            ):
                nonce = os.urandom(12)
                ciphertext = AESGCM(self.key).encrypt(
                    nonce, plaintext, chunk_aad(binding, "main", index, len(plaintext))
                )
                object_name = uuid.uuid4().hex
                self.objects.put(object_name, ciphertext)
                chunks.append(
                    EncryptedChunk(
                        index,
                        object_name,
                        nonce,
                        len(plaintext),
                        hashlib.sha256(plaintext).hexdigest(),
                        hashlib.sha256(ciphertext).hexdigest(),
                    )
                )
                full.update(plaintext)
                size += len(plaintext)
        return EncryptedSource(
            binding, "main", size, full.hexdigest(), tuple(chunks), sidecars
        )

    def local_parse(self, path: Path, source: str) -> tuple[int, list]:
        store = RecordStore(self.root / f"local-{source}.sqlite3")
        try:
            result = self.parse(path, source, store)
            return result.rows_seen, list(result.records)
        finally:
            store.close()

    @staticmethod
    def parse(path: Path, source: str, store: RecordStore):
        if source == "contacts":
            return read_contacts(path, store=store)
        if source == "calls":
            return read_calls(path, RETAINED_HISTORY_START, store=store)
        return read_messages(path, RETAINED_HISTORY_START, store=store)

    def object_parse(
        self, manifest: EncryptedSource, source: str
    ) -> tuple[int, list, EncryptedRangeReader, int]:
        reader = EncryptedRangeReader(manifest, self.objects, self.key)
        store_path = self.root / f"object-{source}.sqlite3"
        store = RecordStore(store_path)
        try:
            with patch(
                "amplifai_phone.metadata._open_readonly",
                side_effect=lambda _path: open_object_source(reader),
            ):
                result = self.parse(self.virtual_root / source, source, store)
            return (
                result.rows_seen,
                list(result.records),
                reader,
                store_path.stat().st_size,
            )
        finally:
            store.close()

    def assert_code(self, code: str, action) -> None:
        with self.assertRaises(ObjectVfsError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_current_parser_parity_and_bounded_cache_for_three_page_sizes(self) -> None:
        for source, page_size in (
            ("contacts", 1024),
            ("calls", 4096),
            ("messages", 8192),
        ):
            with self.subTest(source=source, page_size=page_size):
                path = self.create_source(source, page_size)
                local_rows, local_records = self.local_parse(path, source)
                with closing(sqlite3.connect(path)) as db:
                    local_schema = list(
                        db.execute(
                            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
                        )
                    )
                    local_columns = {
                        name: list(db.execute(f"PRAGMA table_info({name})"))
                        for name, _ in local_schema
                    }
                manifest = self.package(path, source)
                source_bytes = path.stat().st_size
                path.unlink()
                schema_reader = EncryptedRangeReader(manifest, self.objects, self.key)
                with open_object_source(schema_reader) as db:
                    object_schema = [
                        tuple(row)
                        for row in db.execute(
                            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
                        )
                    ]
                    object_columns = {
                        name: [
                            tuple(row)
                            for row in db.execute(f"PRAGMA table_info({name})")
                        ]
                        for name, _ in object_schema
                    }
                self.assertEqual(object_schema, local_schema)
                self.assertEqual(object_columns, local_columns)
                object_rows, object_records, reader, scratch_bytes = self.object_parse(
                    manifest, source
                )
                self.assertEqual(
                    (object_rows, object_records), (local_rows, local_records)
                )
                self.assertEqual(object_rows, 80)
                self.assertGreater(source_bytes, CACHE_BYTES * 4)
                self.assertLessEqual(reader.peak_cached_bytes, CACHE_BYTES)
                self.assertLess(scratch_bytes, source_bytes)
                self.assertFalse((self.virtual_root / source).exists())
                self.assertFalse(
                    any(
                        b"PRIVATE-MESSAGE-" in item.read_bytes()
                        or b"PRIVATE-NOTE-" in item.read_bytes()
                        for item in self.objects.root.iterdir()
                    )
                )

    def test_random_ranges_reopen_and_full_hash_after_cache_eviction(self) -> None:
        path = self.create_source("contacts", 4096)
        original = path.read_bytes()
        manifest = self.package(path, "contacts")
        path.unlink()
        reader = EncryptedRangeReader(manifest, self.objects, self.key)
        try:
            reader.verify_complete()
            for offset in (
                0,
                CHUNK_BYTES - 10,
                len(original) // 2,
                len(original) - 4096,
            ):
                self.assertEqual(
                    reader.read(offset, 4096), original[offset : offset + 4096]
                )
            self.assertLessEqual(reader.peak_cached_bytes, CACHE_BYTES)
            self.assertGreater(reader.objects_fetched, len(manifest.chunks))
        finally:
            reader.close()
        reopened = EncryptedRangeReader(manifest, self.objects, self.key)
        with open_object_source(reopened) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM ABPerson").fetchone()[0], 80
            )
            self.assertEqual(db.execute("PRAGMA page_size").fetchone()[0], 4096)

    def test_sixteen_megabyte_source_exceeds_cache_without_local_plaintext_copy(
        self,
    ) -> None:
        path = self.create_source("contacts", 4096, rows=1024)
        source_bytes = path.stat().st_size
        self.assertGreater(source_bytes, 16 * 1024 * 1024)
        manifest = self.package(path, "contacts")
        path.unlink()
        rows, records, reader, scratch_bytes = self.object_parse(manifest, "contacts")
        self.assertEqual((rows, len(records)), (1024, 1024))
        self.assertLessEqual(reader.peak_cached_bytes, CACHE_BYTES)
        self.assertLess(scratch_bytes, source_bytes // 8)
        self.assertFalse((self.virtual_root / "contacts").exists())

    def test_missing_corrupt_reordered_and_cross_source_chunks_fail(self) -> None:
        path = self.create_source("contacts", 4096)
        manifest = self.package(path, "contacts")
        path.unlink()
        missing = manifest.chunks[3]
        self.objects.remove(missing.key)
        self.assert_code(
            "vfs_object_missing",
            lambda: EncryptedRangeReader(
                manifest, self.objects, self.key
            ).verify_complete(),
        )
        self.objects.put(missing.key, b"altered")
        self.assert_code(
            "vfs_ciphertext_integrity",
            lambda: EncryptedRangeReader(
                manifest, self.objects, self.key
            ).verify_complete(),
        )
        altered = replace(
            manifest,
            chunks=(manifest.chunks[1], manifest.chunks[0], *manifest.chunks[2:]),
        )
        self.assert_code(
            "vfs_manifest_invalid",
            lambda: EncryptedRangeReader(altered, self.objects, self.key),
        )
        self.objects.put(
            missing.key,
            AESGCM(self.key).encrypt(
                missing.nonce,
                b"x" * missing.plaintext_bytes,
                chunk_aad(
                    manifest.binding, "main", missing.index, missing.plaintext_bytes
                ),
            ),
        )
        self.assert_code(
            "vfs_ciphertext_integrity",
            lambda: EncryptedRangeReader(
                manifest, self.objects, self.key
            ).verify_complete(),
        )
        # Repackage unaltered bytes, then change only the claimed source binding.
        path2 = self.create_source("contacts", 4096)
        clean = self.package(path2, "contacts")
        for changed in (
            replace(clean.binding, owner_id=str(uuid.uuid4())),
            replace(clean.binding, phone_id=str(uuid.uuid4())),
            replace(clean.binding, attempt_id=str(uuid.uuid4())),
            replace(clean.binding, source="calls"),
        ):
            wrong = replace(clean, binding=changed)
            self.assert_code(
                "vfs_binding_integrity",
                lambda wrong=wrong: EncryptedRangeReader(
                    wrong, self.objects, self.key
                ).verify_complete(),
            )
        moved_first = replace(clean.chunks[1], index=0)
        moved_second = replace(clean.chunks[0], index=1)
        self.assert_code(
            "vfs_binding_integrity",
            lambda: EncryptedRangeReader(
                replace(clean, chunks=(moved_first, moved_second, *clean.chunks[2:])),
                self.objects,
                self.key,
            ).verify_complete(),
        )
        self.assert_code(
            "vfs_source_integrity",
            lambda: EncryptedRangeReader(
                replace(clean, sha256="0" * 64), self.objects, self.key
            ).verify_complete(),
        )

    def test_object_ids_and_nonces_are_validated_before_fetch(self) -> None:
        path = self.create_source("contacts", 4096)
        clean = self.package(path, "contacts")
        bad_key = replace(clean.chunks[0], key="../outside")
        self.assert_code(
            "vfs_manifest_invalid",
            lambda: EncryptedRangeReader(
                replace(clean, chunks=(bad_key, *clean.chunks[1:])),
                self.objects,
                self.key,
            ),
        )
        duplicate_nonce = replace(clean.chunks[1], nonce=clean.chunks[0].nonce)
        self.assert_code(
            "vfs_manifest_invalid",
            lambda: EncryptedRangeReader(
                replace(
                    clean, chunks=(clean.chunks[0], duplicate_nonce, *clean.chunks[2:])
                ),
                self.objects,
                self.key,
            ),
        )

    def test_cancel_and_wal_sidecar_fail_closed(self) -> None:
        path = self.create_source("contacts", 4096)
        manifest = self.package(path, "contacts")
        count = 0

        def cancel() -> None:
            nonlocal count
            count += 1
            if count == 4:
                raise InterruptedError

        reader = EncryptedRangeReader(
            manifest, self.objects, self.key, check_cancel=cancel
        )

        def cancelled_open() -> None:
            with open_object_source(reader):
                pass

        self.assert_code("vfs_cancelled", cancelled_open)
        self.assertEqual(reader.cached_bytes, 0)

        wal_path = self.root / "active-wal.sqlite3"
        db = sqlite3.connect(wal_path)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("CREATE TABLE recent (value TEXT)")
            db.execute("INSERT INTO recent VALUES ('COMMITTED-IN-WAL')")
            db.commit()
            self.assertEqual(db.execute("SELECT count(*) FROM recent").fetchone()[0], 1)
            self.assertGreater(Path(str(wal_path) + "-wal").stat().st_size, 0)
            unsafe_main = self.package(wal_path, "contacts", sidecars=("wal", "shm"))
            self.assert_code(
                "vfs_sidecar_unqualified",
                lambda: EncryptedRangeReader(unsafe_main, self.objects, self.key),
            )
        finally:
            db.close()


@unittest.skipIf(apsw is None, "Offline APSW candidate is not in the release lock")
class FrozenWalObjectVfsTest(unittest.TestCase):
    create_source = ObjectVfsOfflineTest.create_source
    parse = staticmethod(ObjectVfsOfflineTest.parse)
    local_parse = ObjectVfsOfflineTest.local_parse

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-wal-vfs-offline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = LocalCipherObjects(self.root / "objects")
        self.key = os.urandom(32)
        self.receipt_key = os.urandom(32)
        self.owner = str(uuid.uuid4())
        self.phone = str(uuid.uuid4())
        self.attempt = str(uuid.uuid4())
        self.virtual_root = self.root / "virtual"
        self.virtual_root.mkdir()

    def package_member(
        self, path: Path, binding: SourceBinding, role: str
    ) -> EncryptedSource:
        digest = hashlib.sha256()
        parts = []
        size = 0
        with path.open("rb") as stream:
            for index, plaintext in enumerate(
                iter(lambda: stream.read(CHUNK_BYTES), b"")
            ):
                nonce = os.urandom(12)
                ciphertext = AESGCM(self.key).encrypt(
                    nonce, plaintext, chunk_aad(binding, role, index, len(plaintext))
                )
                object_id = uuid.uuid4().hex
                self.objects.put(object_id, ciphertext)
                parts.append(
                    EncryptedChunk(
                        index,
                        object_id,
                        nonce,
                        len(plaintext),
                        hashlib.sha256(plaintext).hexdigest(),
                        hashlib.sha256(ciphertext).hexdigest(),
                    )
                )
                digest.update(plaintext)
                size += len(plaintext)
        return EncryptedSource(
            binding,
            role,
            size,
            digest.hexdigest(),
            tuple(parts),
            ("wal",) if role == "main" else (),
        )

    def create_wal_pair(self, source: str, page_size: int = 4096, *, rows: int = 80):
        path = self.create_source(source, page_size, rows=rows)
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        db.execute("PRAGMA wal_autocheckpoint=0")
        table = {"contacts": "ABPerson", "calls": "ZCALLRECORD", "messages": "message"}[
            source
        ]
        db.execute(f"ALTER TABLE {table} ADD COLUMN WalSchema INTEGER DEFAULT 7")
        db.commit()
        db.execute(f"DELETE FROM {table} WHERE ROWID=2")
        if source == "contacts":
            db.execute("UPDATE ABPerson SET First='WAL Updated' WHERE ROWID=1")
            db.execute(
                "INSERT INTO ABPerson (First, Last, Note) VALUES ('WAL Recent', 'Example', ?)",
                (os.urandom(16_384),),
            )
            db.execute(
                "INSERT INTO ABMultiValue VALUES (?, 3, '+12025559999')", (rows + 1,)
            )
            db.executemany(
                "UPDATE ABPerson SET Note=? WHERE ROWID=?",
                ((os.urandom(16_384), index) for index in range(3, rows + 1)),
            )
        elif source == "calls":
            db.execute("UPDATE ZCALLRECORD SET ZADDRESS='+12025558888' WHERE ROWID=1")
            db.execute(
                "INSERT INTO ZCALLRECORD (ZDATE,ZDURATION,ZADDRESS,ZORIGINATED,ZANSWERED,Opaque) VALUES (800000081,30,'+12025559999',1,1,?)",
                (os.urandom(16_384),),
            )
            db.executemany(
                "UPDATE ZCALLRECORD SET Opaque=? WHERE ROWID=?",
                ((os.urandom(16_384), index) for index in range(3, rows + 1)),
            )
        else:
            db.execute("UPDATE message SET is_from_me=1 WHERE ROWID=1")
            db.execute(
                "INSERT INTO message (date,service,is_from_me,handle_id,text,attributedBody) VALUES (800000081,'SMS',0,1,?,NULL)",
                (os.urandom(16_384),),
            )
            db.executemany(
                "UPDATE message SET text=? WHERE ROWID=?",
                ((os.urandom(16_384), index) for index in range(3, rows + 1)),
            )
        db.commit()
        db.execute(f"CREATE INDEX wal_probe ON {table}(WalSchema)")
        db.commit()
        wal_path = Path(str(path) + "-wal")
        self.assertGreater(path.stat().st_size, CACHE_BYTES)
        self.assertGreater(wal_path.stat().st_size, CACHE_BYTES)
        binding = SourceBinding(
            self.owner, self.phone, self.attempt, source, str(uuid.uuid4())
        )
        main = self.package_member(path, binding, "main")
        wal = self.package_member(wal_path, binding, "wal")
        frozen = seal_frozen_wal_set(main, wal, self.receipt_key)
        return path, db, frozen

    def object_parse(self, source_set: FrozenWalSet, source: str):
        store = RecordStore(self.root / f"wal-object-{source}-{uuid.uuid4()}.sqlite3")
        try:
            with patch(
                "amplifai_phone.metadata._open_readonly",
                side_effect=lambda _path: open_frozen_wal_source(
                    source_set, self.objects, self.key, self.receipt_key
                ),
            ):
                result = self.parse(self.virtual_root / source, source, store)
            return result.rows_seen, list(result.records)
        finally:
            store.close()

    def assert_code(self, code: str, action) -> None:
        with self.assertRaises(ObjectVfsError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_sqlite_wal_recovery_matches_local_parser_for_all_sources(self) -> None:
        for source, page_size in (
            ("contacts", 1024),
            ("calls", 4096),
            ("messages", 8192),
        ):
            with self.subTest(source=source):
                path, db, source_set = self.create_wal_pair(source, page_size)
                if source == "calls":
                    # -shm is a derived WAL index, recorded in inventory but
                    # rebuilt privately by EXCLUSIVE mode, never admitted.
                    source_set = seal_frozen_wal_set(
                        replace(source_set.main, sidecars=("wal", "shm")),
                        source_set.wal,
                        self.receipt_key,
                    )
                local_rows, local_records = self.local_parse(path, source)
                local_schema = list(
                    db.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                )
                db.close()
                path.unlink()
                with open_frozen_wal_source(
                    source_set, self.objects, self.key, self.receipt_key
                ) as opened:
                    self.assertEqual(
                        opened.execute("PRAGMA journal_mode").fetchone()[0], "wal"
                    )
                    self.assertEqual(
                        [
                            tuple(row)
                            for row in opened.execute(
                                "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
                            )
                        ],
                        local_schema,
                    )
                    self.assertTrue(opened.vfs.wal_opened)
                    self.assertEqual(opened.vfs.mutation_attempts, 0)
                    update = {
                        "contacts": "UPDATE ABPerson SET First='denied'",
                        "calls": "UPDATE ZCALLRECORD SET ZADDRESS='denied'",
                        "messages": "UPDATE message SET service='denied'",
                    }[source]
                    with self.assertRaises(apsw.ReadOnlyError):
                        list(opened.execute(update))
                    self.assertEqual(opened.vfs.mutation_attempts, 0)
                    self.assertLessEqual(
                        opened.vfs.reader.peak_cached_bytes
                        + opened.vfs.wal_reader.peak_cached_bytes,
                        CACHE_BYTES,
                    )
                    main_reader = opened.vfs.reader
                    wal_reader = opened.vfs.wal_reader
                    vfs = opened.vfs
                self.assertEqual(vfs.mutation_attempts, 0)
                self.assertEqual(
                    (local_rows, local_records), self.object_parse(source_set, source)
                )
                self.assertEqual(local_rows, 80)
                self.assertGreater(main_reader.objects_fetched, 0)
                self.assertGreater(wal_reader.objects_fetched, 0)
                self.assertFalse((self.virtual_root / source).exists())

    def test_missing_corrupt_reordered_stale_and_wrong_bound_wal_fail(self) -> None:
        path, db, frozen = self.create_wal_pair("contacts")
        db.close()
        path.unlink()
        first = frozen.wal.chunks[0]
        self.objects.remove(first.key)
        self.assert_code(
            "vfs_object_missing",
            lambda: self.object_parse(frozen, "contacts"),
        )
        self.objects.put(first.key, b"bad")
        self.assert_code(
            "vfs_ciphertext_integrity",
            lambda: self.object_parse(frozen, "contacts"),
        )
        self.objects.remove(first.key)
        wrong_parts = (
            frozen.wal.chunks[1],
            frozen.wal.chunks[0],
            *frozen.wal.chunks[2:],
        )
        self.assert_code(
            "vfs_receipt_invalid",
            lambda: self.object_parse(
                replace(frozen, wal=replace(frozen.wal, chunks=wrong_parts)), "contacts"
            ),
        )
        for change in (
            replace(frozen.wal.binding, owner_id=str(uuid.uuid4())),
            replace(frozen.wal.binding, phone_id=str(uuid.uuid4())),
            replace(frozen.wal.binding, attempt_id=str(uuid.uuid4())),
            replace(frozen.wal.binding, source_set_id=str(uuid.uuid4())),
        ):
            self.assert_code(
                "vfs_source_set_invalid",
                lambda change=change: self.object_parse(
                    replace(frozen, wal=replace(frozen.wal, binding=change)), "contacts"
                ),
            )
        self.assert_code(
            "vfs_receipt_invalid",
            lambda: self.object_parse(
                replace(frozen, wal=replace(frozen.wal, sha256="0" * 64)), "contacts"
            ),
        )
        self.assert_code(
            "vfs_source_set_invalid",
            lambda: self.object_parse(
                replace(frozen, main=replace(frozen.main, sidecars=("wal", "journal"))),
                "contacts",
            ),
        )
        reused_nonce = replace(frozen.wal.chunks[0], nonce=frozen.main.chunks[0].nonce)
        self.assert_code(
            "vfs_source_set_invalid",
            lambda: seal_frozen_wal_set(
                frozen.main,
                replace(frozen.wal, chunks=(reused_nonce, *frozen.wal.chunks[1:])),
                self.receipt_key,
            ),
        )

    def test_resealed_malformed_wal_and_cross_set_ciphertext_fail(self) -> None:
        path, db, frozen = self.create_wal_pair("contacts")
        wal_path = Path(str(path) + "-wal")
        original = wal_path.read_bytes()

        def reseal(data: bytes) -> FrozenWalSet:
            mutated = self.root / f"mutated-{uuid.uuid4()}.wal"
            mutated.write_bytes(data)
            new_wal = self.package_member(mutated, frozen.main.binding, "wal")
            return seal_frozen_wal_set(frozen.main, new_wal, self.receipt_key)

        wrong_page = bytearray(original)
        wrong_page[8:12] = (8192).to_bytes(4, "big")
        self.assert_code(
            "vfs_wal_format_invalid",
            lambda: self.object_parse(reseal(bytes(wrong_page)), "contacts"),
        )
        damaged_frame = bytearray(original)
        damaged_frame[32 + 24 + 100] ^= 1
        self.assert_code(
            "vfs_wal_checksum_invalid",
            lambda: self.object_parse(reseal(bytes(damaged_frame)), "contacts"),
        )
        self.assert_code(
            "vfs_wal_format_invalid",
            lambda: self.object_parse(reseal(original[:-1]), "contacts"),
        )
        for changed in (
            replace(frozen.main.binding, owner_id=str(uuid.uuid4())),
            replace(frozen.main.binding, phone_id=str(uuid.uuid4())),
            replace(frozen.main.binding, attempt_id=str(uuid.uuid4())),
            replace(frozen.main.binding, source_set_id=str(uuid.uuid4())),
        ):
            cross_bound = seal_frozen_wal_set(
                replace(frozen.main, binding=changed),
                replace(frozen.wal, binding=changed),
                self.receipt_key,
            )
            self.assert_code(
                "vfs_binding_integrity",
                lambda cross_bound=cross_bound: self.object_parse(
                    cross_bound, "contacts"
                ),
            )
        db.close()

    def test_valid_uncommitted_tail_recovers_last_commit_without_partial_rows(
        self,
    ) -> None:
        path, db, frozen = self.create_wal_pair("contacts")
        baseline_rows, baseline_records = self.local_parse(path, "contacts")
        wal_path = Path(str(path) + "-wal")
        before = wal_path.stat().st_size
        db.execute("PRAGMA cache_size=10")
        db.execute("BEGIN")
        db.executemany(
            "UPDATE ABPerson SET Note=? WHERE ROWID=?",
            ((os.urandom(16_384), index) for index in range(1, 81)),
        )
        db.execute(
            "INSERT INTO ABPerson (First,Last,Note) VALUES ('UNCOMMITTED','Example',?)",
            (os.urandom(16_384),),
        )
        self.assertGreater(wal_path.stat().st_size, before)
        tail = self.package_member(wal_path, frozen.main.binding, "wal")
        frozen_with_tail = seal_frozen_wal_set(
            frozen.main, tail, self.receipt_key, writer_active_at_freeze=True
        )
        db.rollback()
        db.close()
        path.unlink()
        self.assertEqual(
            self.object_parse(frozen_with_tail, "contacts"),
            (baseline_rows, baseline_records),
        )
        with open_frozen_wal_source(
            frozen_with_tail, self.objects, self.key, self.receipt_key
        ) as opened:
            self.assertGreater(opened.vfs.wal_audit.valid_frames, 0)
            self.assertEqual(
                opened.execute("SELECT count(*) FROM ABPerson").fetchone()[0], 80
            )

    def test_wal_with_only_uncommitted_frames_reads_committed_main(self) -> None:
        path = self.create_source("contacts", 4096)
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("PRAGMA cache_size=10")
        db.execute("BEGIN")
        db.execute("UPDATE ABPerson SET First='UNCOMMITTED' WHERE ROWID=1")
        db.executemany(
            "UPDATE ABPerson SET Note=? WHERE ROWID=?",
            ((os.urandom(16_384), index) for index in range(1, 81)),
        )
        wal_path = Path(str(path) + "-wal")
        self.assertGreater(wal_path.stat().st_size, CACHE_BYTES)
        local_rows, local_records = self.local_parse(path, "contacts")
        binding = SourceBinding(
            self.owner, self.phone, self.attempt, "contacts", str(uuid.uuid4())
        )
        frozen = seal_frozen_wal_set(
            self.package_member(path, binding, "main"),
            self.package_member(wal_path, binding, "wal"),
            self.receipt_key,
            writer_active_at_freeze=True,
        )
        db.rollback()
        db.close()
        path.unlink()
        with open_frozen_wal_source(
            frozen, self.objects, self.key, self.receipt_key
        ) as opened:
            self.assertEqual(opened.vfs.wal_audit.committed_frames, 0)
            self.assertGreater(opened.vfs.wal_audit.incomplete_uncommitted_frames, 0)
            self.assertEqual(
                opened.execute("SELECT First FROM ABPerson WHERE ROWID=1").fetchone()[
                    0
                ],
                "Person 0",
            )
        self.assertEqual(
            self.object_parse(frozen, "contacts"), (local_rows, local_records)
        )
        self.assert_code(
            "vfs_receipt_invalid",
            lambda: self.object_parse(
                replace(frozen, writer_active_at_freeze=False), "contacts"
            ),
        )
        self.assert_code(
            "vfs_wal_checksum_invalid",
            lambda: self.object_parse(
                seal_frozen_wal_set(frozen.main, frozen.wal, self.receipt_key),
                "contacts",
            ),
        )

    def test_wal_reset_stale_physical_tail_is_ignored_by_sqlite(self) -> None:
        path, db, _ = self.create_wal_pair("contacts")
        wal_path = Path(str(path) + "-wal")
        before = wal_path.stat().st_size
        self.assertEqual(db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[0], 0)
        db.execute("UPDATE ABPerson SET First='After reset' WHERE ROWID=1")
        db.commit()
        self.assertEqual(wal_path.stat().st_size, before)
        binding = SourceBinding(
            self.owner, self.phone, self.attempt, "contacts", str(uuid.uuid4())
        )
        frozen = seal_frozen_wal_set(
            self.package_member(path, binding, "main"),
            self.package_member(wal_path, binding, "wal"),
            self.receipt_key,
        )
        local_rows, local_records = self.local_parse(path, "contacts")
        db.close()
        path.unlink()
        with open_frozen_wal_source(
            frozen, self.objects, self.key, self.receipt_key
        ) as opened:
            self.assertGreater(opened.vfs.wal_audit.stale_tail_frames, 0)
            self.assertEqual(
                opened.execute("SELECT First FROM ABPerson WHERE ROWID=1").fetchone()[
                    0
                ],
                "After reset",
            )
        self.assertEqual(
            self.object_parse(frozen, "contacts"), (local_rows, local_records)
        )

    def test_cancel_and_fresh_reopen_preserve_wal_rows(self) -> None:
        path, db, frozen = self.create_wal_pair("contacts")
        original_wal = Path(str(path) + "-wal").read_bytes()
        db.close()
        path.unlink()
        random_reader = EncryptedRangeReader(
            frozen.wal,
            self.objects,
            self.key,
            _frozen_wal_member=True,
        )
        try:
            for offset in (
                0,
                CHUNK_BYTES - 10,
                len(original_wal) // 2,
                len(original_wal) - 4096,
            ):
                self.assertEqual(
                    random_reader.read(offset, 4096),
                    original_wal[offset : offset + 4096],
                )
            self.assertLessEqual(random_reader.peak_cached_bytes, CACHE_BYTES)
        finally:
            random_reader.close()
        count = 0

        def cancel() -> None:
            nonlocal count
            count += 1
            if count == 5:
                raise InterruptedError

        def cancelled_open() -> None:
            with open_frozen_wal_source(
                frozen,
                self.objects,
                self.key,
                self.receipt_key,
                check_cancel=cancel,
            ):
                pass

        self.assert_code("vfs_cancelled", cancelled_open)
        armed = False

        def cancel_during_read() -> None:
            if armed:
                raise InterruptedError

        with open_frozen_wal_source(
            frozen,
            self.objects,
            self.key,
            self.receipt_key,
            check_cancel=cancel_during_read,
        ) as opened:
            armed = True
            self.assert_code(
                "vfs_cancelled",
                lambda: list(opened.execute("SELECT count(*) FROM ABPerson")),
            )
            self.assertEqual(opened.vfs.wal_reader.last_error, "vfs_cancelled")
        with open_frozen_wal_source(
            frozen, self.objects, self.key, self.receipt_key
        ) as reopened:
            self.assertEqual(
                reopened.execute("SELECT count(*) FROM ABPerson").fetchone()[0], 80
            )
            self.assertEqual(
                reopened.execute(
                    "SELECT First FROM ABPerson WHERE ROWID=81"
                ).fetchone()[0],
                "WAL Recent",
            )
            self.assertEqual(reopened.vfs.mutation_attempts, 0)

    def test_large_main_and_wal_have_bounded_process_memory(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "collector:collector/tests"
        result = subprocess.run(
            [sys.executable, "-m", __name__, "--rss-worker"],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        measured = json.loads(result.stdout)
        self.assertGreater(measured["main_bytes"], 16 * 1024 * 1024)
        self.assertGreater(measured["wal_bytes"], 16 * 1024 * 1024)
        self.assertEqual(measured["rows_seen"], 1024)
        self.assertEqual(measured["retained"], 1024)
        self.assertLessEqual(measured["peak_python_cache_bytes"], CACHE_BYTES)
        self.assertEqual(measured["source_mutation_attempts"], 0)
        self.assertEqual(measured["virtual_source_files"], 0)
        self.assertLess(measured["sanitized_store_bytes"], measured["main_bytes"] // 8)
        self.assertLess(
            measured["rss_peak_bytes"] - measured["rss_initial_bytes"], 32 * 1024 * 1024
        )
        self.assertLess(measured["sqlite_peak_bytes"], 16 * 1024 * 1024)


def _rss_worker() -> None:
    case = FrozenWalObjectVfsTest("test_large_main_and_wal_have_bounded_process_memory")
    case.setUp()
    try:
        unit = 1 if sys.platform == "darwin" else 1024
        initial = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        path, db, frozen = case.create_wal_pair("contacts", rows=1024)
        main_bytes = frozen.main.size
        wal_bytes = frozen.wal.size
        db.close()
        path.unlink()
        with open_frozen_wal_source(
            frozen, case.objects, case.key, case.receipt_key
        ) as opened:
            main_reader = opened.vfs.reader
            wal_reader = opened.vfs.wal_reader
            vfs = opened.vfs
            store = RecordStore(case.root / "wal-rss-sanitized.sqlite3")
            try:

                @contextmanager
                def borrow_connection(_path):
                    yield opened

                with patch(
                    "amplifai_phone.metadata._open_readonly",
                    side_effect=borrow_connection,
                ):
                    result = read_contacts(case.virtual_root / "contacts", store=store)
                retained = len(result.records)
            finally:
                store.close()
        print(
            json.dumps(
                {
                    "main_bytes": main_bytes,
                    "wal_bytes": wal_bytes,
                    "rows_seen": result.rows_seen,
                    "retained": retained,
                    "peak_python_cache_bytes": main_reader.peak_cached_bytes
                    + wal_reader.peak_cached_bytes,
                    "main_object_fetches": main_reader.objects_fetched,
                    "wal_object_fetches": wal_reader.objects_fetched,
                    "source_mutation_attempts": vfs.mutation_attempts,
                    "virtual_source_files": len(list(case.virtual_root.iterdir())),
                    "sanitized_store_bytes": (case.root / "wal-rss-sanitized.sqlite3")
                    .stat()
                    .st_size,
                    "rss_initial_bytes": initial,
                    "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    * unit,
                    "sqlite_peak_bytes": apsw.status(apsw.SQLITE_STATUS_MEMORY_USED)[1],
                }
            )
        )
    finally:
        case.doCleanups()


if __name__ == "__main__":
    if sys.argv[1:] == ["--rss-worker"]:
        _rss_worker()
    else:
        unittest.main()
