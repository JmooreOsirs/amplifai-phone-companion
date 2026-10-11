"""Fictional encrypted backup files at the selected-size integrity boundary."""

from __future__ import annotations

import hashlib
import io
import json
import plistlib
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_wrap
from packaging.version import Version
from pyiosbackup import Backup
from pyiosbackup.keybag import Keybag
from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3

from amplifai_phone.ios_backup import DATABASES, _extract_database, _parse_entries
from amplifai_phone.agent import _payload_diagnostic_event, run_connect
from amplifai_phone import sqlite_encrypted_size_compat
from amplifai_phone.metadata import RETAINED_HISTORY_START, SelectedPayloadIntegrityError, UnsupportedSchema
from amplifai_phone.workspace import SessionWorkspace


class EncryptedSizeCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="amplifai-encrypted-size-fictional-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = SessionWorkspace(self.root / "sessions")
        self.session = self.workspace.__enter__()
        self.addCleanup(self.workspace.__exit__, None, None, None)
        self.backup_path = self.session / "backup"
        self.backup_path.mkdir(mode=0o700)
        self.copy_path = self.session / "copy"
        self.copy_path.mkdir(mode=0o700)
        self.sink = self.root / "worker-sink"
        self.sink.touch()
        self.devnull = patch("os.devnull", str(self.sink))
        self.devnull.start()
        self.addCleanup(self.devnull.stop)
        self.class_key = b"c" * 32
        self.file_key = b"f" * 32
        self.wrapped_key = struct.pack("<I", 1) + aes_key_wrap(self.class_key, self.file_key)

    def _database(self, category: str) -> bytes:
        path = self.root / f"{category}-{self.backup_path.name}.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA page_size=4096")
            if category == "contacts":
                db.execute("CREATE TABLE ABPerson (ROWID INTEGER PRIMARY KEY, First TEXT, Last TEXT)")
                db.execute("CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)")
                db.execute("INSERT INTO ABPerson VALUES (7, 'Fictional', 'Person')")
                db.execute("INSERT INTO ABMultiValue VALUES (7, 3, '+12025550123')")
            elif category == "calls":
                db.execute("CREATE TABLE ZCALLRECORD (Z_PK INTEGER PRIMARY KEY, ZDATE, ZDURATION, "
                           "ZADDRESS, ZORIGINATED, ZANSWERED)")
                db.execute("INSERT INTO ZCALLRECORD VALUES (7, 800000000, 60, '+12025550123', 1, 1)")
            else:
                db.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
                db.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date, service, is_from_me, "
                           "handle_id, text)")
                db.execute("INSERT INTO handle VALUES (1, '+12025550123')")
                db.execute("INSERT INTO message VALUES (7, 800000000, 'SMS', 0, 1, 'PRIVATE_BODY')")
        return path.read_bytes()

    def _archive(self, relative: str, declared: int, digest: bytes | None) -> bytes:
        objects: list[object] = [
            "$null",
            {"$class": plistlib.UID(3), "RelativePath": plistlib.UID(2), "Size": declared,
             "Mode": 0o100600, "LastModified": 0, "LastStatusChange": 0, "Birth": 0,
             "GroupID": 0, "UserID": 0},
            relative,
            {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]},
        ]
        for name, value in (("EncryptionKey", self.wrapped_key), ("Digest", digest)):
            if value is None:
                continue
            position = len(objects)
            objects[1][name] = plistlib.UID(position)
            objects.extend((
                {"$class": plistlib.UID(position + 1), "NS.data": value},
                {"$classname": "NSMutableData", "$classes": ["NSMutableData", "NSData", "NSObject"]},
            ))
        return plistlib.dumps({"$archiver": "NSKeyedArchiver", "$version": 100000,
                               "$top": {"root": plistlib.UID(1)}, "$objects": objects},
                              fmt=plistlib.FMT_BINARY)

    def _backup(self, categories: tuple[str, ...] = ("contacts",), *,
                digest_mode: str = "ciphertext", wrong_category: str | None = None,
                delta: int = 4096,
                sidecar: tuple[str, str] | None = None,
                corrupt: str | None = None) -> Backup:
        manifest_path = self.backup_path / "Manifest.db"
        with closing(sqlite3.connect(manifest_path)) as db, db:
            db.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, "
                       "relativePath TEXT, flags INTEGER, file BLOB)")
            for category in categories:
                category_digest = "wrong" if category == wrong_category else digest_mode
                plain = bytearray(self._database(category))
                if corrupt == category:
                    plain[4096] = 255
                padder = padding.PKCS7(128).padder()
                padded = padder.update(bytes(plain)) + padder.finalize()
                encryptor = Cipher(algorithms.AES(self.file_key), modes.CBC(b"\x00" * 16)).encryptor()
                ciphertext = encryptor.update(padded) + encryptor.finalize()
                relative = DATABASES[category]
                file_id = hashlib.sha1(f"HomeDomain-{relative}".encode()).hexdigest()
                source = self.backup_path / file_id[:2] / file_id
                source.parent.mkdir(exist_ok=True)
                source.write_bytes(ciphertext)
                digest = (hashlib.sha1(ciphertext).digest() if category_digest == "ciphertext" else
                          hashlib.sha1(plain).digest() if category_digest == "plaintext" else
                          None if category_digest == "missing" else b"x" * 20)
                db.execute("INSERT INTO Files VALUES (?, ?, ?, 1, ?)",
                           (file_id, "HomeDomain", relative,
                            self._archive(relative, len(plain) + delta, digest)))
            if sidecar is not None:
                category, suffix = sidecar
                relative = DATABASES[category] + suffix
                file_id = hashlib.sha1(f"HomeDomain-{relative}".encode()).hexdigest()
                db.execute("INSERT INTO Files VALUES (?, ?, ?, 1, ?)",
                           (file_id, "HomeDomain", relative, self._archive(relative, 0, None)))
        manifest = ManifestDbSqlite3(manifest_path)
        self.addCleanup(manifest._conn.close)
        return Backup(self.backup_path, manifest,
                      SimpleNamespace(is_encrypted=True, product_version=Version("18.0")),
                      {"SnapshotState": "finished"}, {"Product Version": "18.0"},
                      Keybag({1: self.class_key}))

    def _extract(self, backup: Backup, category: str = "contacts") -> tuple[bool, list[dict]]:
        samples: list[dict] = []
        accepted = _extract_database(
            backup, DATABASES[category], self.copy_path / category,
            workspace=self.workspace, payload_diagnostic_callback=samples.append,
        )
        return accepted, samples

    def test_ciphertext_digest_admits_exact_three_selected_sources(self) -> None:
        backup = self._backup(tuple(DATABASES))
        capture = _parse_entries(
            backup, self.copy_path, datetime(2026, 10, 11, tzinfo=UTC),
            RETAINED_HISTORY_START, workspace=self.workspace,
        )
        self.assertEqual(tuple(len(item.records) for item in
                               (capture.contacts, capture.calls, capture.messages)), (1, 1, 1))
        self.assertEqual(capture.missing_sources, ())
        self.assertIs(capture.backup_encrypted, True)
        self.assertEqual(capture.calls.records[0].source_id, 7)

    def test_accepted_size_compatibility_cannot_stick_as_later_failure(self) -> None:
        backup = self._backup()
        accepted, samples = self._extract(backup)
        self.assertTrue(accepted)
        self.assertEqual(samples[-1]["result"], "accepted_compatibility")
        self.assertIsNone(_payload_diagnostic_event(samples[-1]))

        async def later_error(**kwargs: object):
            for sample in samples:
                kwargs["payload_diagnostic_callback"](sample)
            raise UnsupportedSchema("Later unrelated source schema")

        output = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), output, later_error), 1)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(any(event["kind"] == "selected-payload-diagnostic" for event in events))
        self.assertEqual(events[-1], {"kind": "error", "code": "unsupported_schema", "stage": "connecting"})

    def test_completed_validation_worker_exit_race_keeps_the_result(self) -> None:
        backup = self._backup()
        ordinary_popen = sqlite_encrypted_size_compat.subprocess.Popen

        class FinishedDuringKill:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.worker = ordinary_popen(*args, **kwargs)

            def __getattr__(self, name: str):
                return getattr(self.worker, name)

            def poll(self):
                return None

            def kill(self):
                raise ProcessLookupError("Worker already exited")

        with patch.object(sqlite_encrypted_size_compat.subprocess, "Popen", FinishedDuringKill):
            accepted, samples = self._extract(backup)
        self.assertTrue(accepted)
        self.assertEqual(samples[-1]["size_difference_validation"], "validated_contacts_tables")

    def _reject_length(self, backup: Backup, category: str = "contacts") -> None:
        with self.assertRaises(SelectedPayloadIntegrityError) as raised:
            self._extract(backup, category)
        self.assertEqual(raised.exception.code, "selected_payload_length")

    def test_missing_producer_digest_rejects_required_contacts(self) -> None:
        self._reject_length(self._backup(digest_mode="missing"))

    def test_wrong_producer_digest_rejects_required_contacts(self) -> None:
        self._reject_length(self._backup(digest_mode="wrong"))

    def test_plaintext_digest_cannot_substitute_for_ciphertext_digest(self) -> None:
        self._reject_length(self._backup(digest_mode="plaintext"))

    def test_declared_wal_rejects_required_contacts(self) -> None:
        self._reject_length(self._backup(sidecar=("contacts", "-wal")))

    def test_declared_shm_and_journal_reject_required_contacts(self) -> None:
        for suffix in ("-shm", "-journal"):
            with self.subTest(suffix=suffix):
                self.backup_path = self.session / f"backup-{suffix[1:]}"
                self.backup_path.mkdir(mode=0o700)
                self.copy_path = self.session / f"copy-{suffix[1:]}"
                self.copy_path.mkdir(mode=0o700)
                backup = self._backup(sidecar=("contacts", suffix))
                self._reject_length(backup)

    def test_larger_than_manifest_plaintext_is_not_a_compatibility_case(self) -> None:
        self._reject_length(self._backup(delta=-4096))

    def test_ciphertext_truncation_never_reaches_size_compatibility(self) -> None:
        backup = self._backup()
        entry = backup.get_entry_by_domain_and_path("HomeDomain", DATABASES["contacts"])
        source = entry.real_path
        source.write_bytes(source.read_bytes()[:-1])
        with self.assertRaises(SelectedPayloadIntegrityError) as raised:
            self._extract(backup)
        self.assertEqual(raised.exception.code, "selected_payload_crypto")

    def test_source_replacement_after_copy_rejects_authenticated_proof(self) -> None:
        backup = self._backup()
        entry = backup.get_entry_by_domain_and_path("HomeDomain", DATABASES["contacts"])
        from amplifai_phone import ios_backup
        ordinary_copy = ios_backup.copy_backup_file

        def replace_after_copy(*args, **kwargs):
            copied = ordinary_copy(*args, **kwargs)
            source = entry.real_path
            source.write_bytes(source.read_bytes())
            return copied

        with patch.object(ios_backup, "copy_backup_file", side_effect=replace_after_copy):
            self._reject_length(backup)

    def test_copy_tampering_after_write_rejects_without_exposing_private_bytes(self) -> None:
        backup = self._backup()
        from amplifai_phone import ios_backup
        ordinary_copy = ios_backup.copy_backup_file

        def tamper_after_copy(*args, **kwargs):
            copied = ordinary_copy(*args, **kwargs)
            destination = Path(args[1])
            with destination.open("r+b") as stream:
                stream.seek(4096)
                stream.write(b"x")
            return copied

        samples: list[dict] = []
        with patch.object(ios_backup, "copy_backup_file", side_effect=tamper_after_copy):
            with self.assertRaises(SelectedPayloadIntegrityError) as raised:
                _extract_database(backup, DATABASES["contacts"], self.copy_path / "contacts",
                                  workspace=self.workspace, payload_diagnostic_callback=samples.append)
        self.assertEqual(raised.exception.code, "selected_payload_length")
        final = samples[-1]
        self.assertEqual(final["size_difference_validation"], "copy_checksum_mismatch")
        safe = _payload_diagnostic_event(final)
        self.assertEqual(safe["source"], "contacts")
        self.assertEqual(safe["producerDigest"], "matched")
        self.assertEqual(safe["sidecars"], "absent")
        entry = backup.get_entry_by_domain_and_path("HomeDomain", DATABASES["contacts"])
        self.assertEqual(safe["declaredBytes"], entry.size)
        for private in ("path", "hash", "key", "PRIVATE_BODY"):
            self.assertNotIn(private, str(safe))

    def test_corrupt_selected_sqlite_rejects_required_contacts(self) -> None:
        self._reject_length(self._backup(corrupt="contacts"))

    def test_optional_call_digest_failure_is_unavailable_not_zero_history(self) -> None:
        backup = self._backup(("contacts", "calls"), wrong_category="calls")
        capture = _parse_entries(
            backup, self.copy_path, datetime(2026, 10, 11, tzinfo=UTC),
            RETAINED_HISTORY_START, workspace=self.workspace,
        )
        self.assertEqual(capture.missing_sources, ("calls", "messages"))
        self.assertEqual(dict(capture.unavailable_reasons)["calls"], "selected_payload_length")
        self.assertEqual(len(capture.calls.records), 0)

    def test_wrong_key_does_not_admit_size_difference(self) -> None:
        backup = self._backup()
        backup.keybag = Keybag({1: b"w" * 32})
        with self.assertRaises(InvalidUnwrap):
            self._extract(backup)
