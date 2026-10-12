"""Synthetic encrypted Apple backup over offline authenticated grouped objects."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import plistlib
import resource
import sqlite3
import struct
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_offline_object_vfs as legacy_fixtures
from amplifai_phone.backup_files import copy_backup_file
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN, parse_selected_backup
from amplifai_phone.metadata import (
    RETAINED_HISTORY_START,
    read_messages,
)
from amplifai_phone.offline_apple_object_reader import (
    AppleCbcRangeReader,
    LocalAppleEntry,
    LocalKeyAuthority,
    open_apple_grouped_wal_source,
)
from amplifai_phone.offline_cloud_capture import (
    CLOUD_DISCLOSURE_VERSION,
    CaptureAdmission,
)
from amplifai_phone.offline_grouped_object_vfs import (
    GroupedRangeReader,
    RangeReply,
    package_grouped_source,
    seal_grouped_wal_set,
)
from amplifai_phone.offline_object_vfs import CACHE_BYTES, CHUNK_BYTES, ObjectVfsError
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import WorkspaceError
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
from packaging.version import Version
from pyiosbackup.entry import Entry
from pyiosbackup.keybag import Keybag
from pyiosbackup.manifest_plist import ManifestPlist


def _tag(name: bytes, payload: bytes | int) -> bytes:
    data = struct.pack(">I", payload) if isinstance(payload, int) else payload
    return name + struct.pack(">I", len(data)) + data


def _encrypt(payload: bytes, key: bytes, *, padded: bool) -> bytes:
    if padded:
        padder = padding.PKCS7(128).padder()
        payload = padder.update(payload) + padder.finalize()
    else:
        assert len(payload) % 16 == 0
    worker = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
    return worker.update(payload) + worker.finalize()


def _archived_entry(relative: str, size: int, wrapped_key: bytes) -> bytes:
    """Pinned MBFile archive shape including its local-only EncryptionKey."""
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
                    "EncryptionKey": plistlib.UID(4),
                },
                {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]},
                relative,
                {"$class": plistlib.UID(5), "NS.data": wrapped_key},
                {
                    "$classname": "NSMutableData",
                    "$classes": ["NSMutableData", "NSData", "NSObject"],
                },
            ],
            "$top": {"root": plistlib.UID(1)},
        },
        fmt=plistlib.FMT_BINARY,
    )


class RangeObjects:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, object_id: str, ciphertext: bytes) -> None:
        self.objects[object_id] = ciphertext

    def get_range(
        self, object_id: str, start: int, end: int, max_bytes: int
    ) -> RangeReply:
        data = self.objects[object_id]
        body = data[start : end + 1]
        if len(body) > max_bytes:
            raise AssertionError("Unbounded synthetic Range response")
        return RangeReply(206, f"bytes {start}-{end}/{len(data)}", len(body), body)


class CurrentConsent:
    def __init__(self, admission: CaptureAdmission) -> None:
        self.admission = admission
        self.active = True

    def __call__(self, admission: CaptureAdmission, source: str | None) -> None:
        if (
            not self.active
            or admission != self.admission
            or (source is not None and source not in admission.categories)
        ):
            raise RuntimeError("Synthetic cloud-source authority is no longer current")


class AppleEncryptedObjectTest(unittest.TestCase):
    create_source = legacy_fixtures.FrozenWalObjectVfsTest.create_source
    create_wal_pair = legacy_fixtures.FrozenWalObjectVfsTest.create_wal_pair
    package_member = legacy_fixtures.FrozenWalObjectVfsTest.package_member
    parse = staticmethod(legacy_fixtures.FrozenWalObjectVfsTest.parse)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-apple-object-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = legacy_fixtures.LocalCipherObjects(
            self.root / "legacy-fixture-objects"
        )
        self.key = os.urandom(32)
        self.object_key = os.urandom(32)
        self.receipt_key = os.urandom(32)
        self.owner = str(uuid.uuid4())
        self.phone = str(uuid.uuid4())
        self.attempt = str(uuid.uuid4())
        self.virtual_root = self.root / "virtual"
        self.virtual_root.mkdir()
        self.password = "synthetic-local-password-only"
        self.admission = CaptureAdmission(
            self.owner,
            self.phone,
            self.attempt,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            ("calls", "contacts", "messages"),
            CLOUD_DISCLOSURE_VERSION,
            True,
        )
        self.current = CurrentConsent(self.admission)

    def _keybag(self, root: Path, wrapping: bytes, manifest_key: bytes) -> Path:
        salt, dpsl = os.urandom(16), os.urandom(16)
        intermediate = hashlib.pbkdf2_hmac(
            "sha256", self.password.encode(), dpsl, 3, 32
        )
        derived = hashlib.pbkdf2_hmac("sha1", intermediate, salt, 5, 32)
        keybag = b"".join(
            (
                _tag(b"SALT", salt),
                _tag(b"ITER", 5),
                _tag(b"DPSL", dpsl),
                _tag(b"DPIC", 3),
                _tag(b"CLAS", 1),
                _tag(b"WRAP", 2),
                _tag(b"WPKY", aes_key_wrap(derived, wrapping)),
                _tag(b"KTYP", 0),
                _tag(b"UUID", os.urandom(16)),
            )
        )
        manifest = root / "Manifest.plist"
        manifest.write_bytes(
            plistlib.dumps(
                {
                    "IsEncrypted": True,
                    "BackupKeyBag": keybag,
                    "ManifestKey": struct.pack("<I", 1)
                    + aes_key_wrap(wrapping, manifest_key),
                    "Lockdown": {"ProductVersion": "18.0"},
                }
            )
        )
        return manifest

    def fixture(
        self,
        category: str,
        page_size: int = 4096,
        *,
        bad_padding: bool = False,
    ) -> dict:
        source, db, frozen = self.create_wal_pair(category, page_size)
        binding = frozen.main.binding
        wal_path = Path(str(source) + "-wal")
        original = {"main": source.read_bytes(), "wal": wal_path.read_bytes()}
        root = self.root / f"candidate-{category}-{uuid.uuid4().hex}"
        root.mkdir()
        wrapping, manifest_key = os.urandom(32), os.urandom(32)
        plist = self._keybag(root, wrapping, manifest_key)
        encrypted_paths = {}
        keys = {role: os.urandom(32) for role in ("main", "wal")}
        self.assertNotEqual(keys["main"], keys["wal"])
        wrapped = {
            role: struct.pack("<I", 1) + aes_key_wrap(wrapping, key)
            for role, key in keys.items()
        }
        plain_manifest = root / "fixture-plain-manifest.db"
        with closing(sqlite3.connect(plain_manifest)) as control, control:
            control.execute(
                "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, "
                "flags INTEGER, file BLOB)"
            )
            control.execute("CREATE TABLE unrelated (private_body TEXT)")
            control.execute("INSERT INTO unrelated VALUES ('UNRELATED-LOCAL-CONTROL')")
            for role, payload in original.items():
                relative = DATABASES[category] + ("-wal" if role == "wal" else "")
                file_id = hashlib.sha1(
                    (HOME_DOMAIN + "-" + relative).encode()
                ).hexdigest()
                cipher = _encrypt(payload, keys[role], padded=True)
                if bad_padding and role == "wal":
                    # Forge a final CBC block that decrypts to all zeroes. Outer
                    # GCM is valid after packaging, so the CBC padding check owns it.
                    previous = cipher[-32:-16]
                    final = Cipher(
                        algorithms.AES(keys[role]), modes.CBC(previous)
                    ).encryptor()
                    cipher = (
                        cipher[:-16] + final.update(b"\x00" * 16) + final.finalize()
                    )
                path = root / file_id[:2] / file_id
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(cipher)
                encrypted_paths[role] = path
                control.execute(
                    "INSERT INTO Files VALUES (?, ?, ?, ?, ?)",
                    (
                        file_id,
                        HOME_DOMAIN,
                        relative,
                        1,
                        _archived_entry(relative, len(payload), wrapped[role]),
                    ),
                )
        manifest_plain = plain_manifest.read_bytes()
        manifest_cipher = root / "Manifest.db"
        manifest_cipher.write_bytes(
            _encrypt(manifest_plain, manifest_key, padded=False)
        )
        plain_manifest.unlink()
        keybag = Keybag.from_manifest(ManifestPlist.from_path(plist), self.password)
        objects = RangeObjects()
        indexes = {role: root / f"{role}.index" for role in ("main", "wal")}
        grouped = {
            role: package_grouped_source(
                encrypted_paths[role],
                indexes[role],
                binding,
                role,
                objects,
                self.object_key,
                self.receipt_key,
                sidecars=("wal",) if role == "main" else (),
            )
            for role in ("main", "wal")
        }
        source_set = seal_grouped_wal_set(
            grouped["main"], grouped["wal"], self.receipt_key
        )
        backup = SimpleNamespace(
            path=root,
            ios_version=Version("18.0"),
            is_encrypted=True,
            keybag=keybag,
        )
        entries = {}
        authority = LocalKeyAuthority(
            self.admission, plist, manifest_cipher, self.password, self.current
        )
        self.addCleanup(authority.close)
        manifest_copy = authority.control_copy_path
        self.assertEqual(manifest_copy.read_bytes(), manifest_plain)
        for role in ("main", "wal"):
            claim = authority.project_entry(grouped[role])
            now = datetime.now(UTC)
            entry = Entry(
                backup,
                claim.file_id,
                HOME_DOMAIN,
                DATABASES[category] + ("-wal" if role == "wal" else ""),
                now,
                now,
                now,
                claim.size,
                0o100600,
                0,
                0,
                claim.wrapped_file_key,
            )
            if not (bad_padding and role == "wal"):
                self.assertEqual(entry.read_bytes(), original[role])
            entries[role] = claim
        return {
            "source": source,
            "db": db,
            "original": original,
            "root": root,
            "objects": objects,
            "indexes": indexes,
            "set": source_set,
            "entries": entries,
            "authority": authority,
            "plist": plist,
            "manifest_cipher": manifest_cipher,
            "manifest_copy": manifest_copy,
            "manifest_key": manifest_key,
            "encrypted_paths": encrypted_paths,
            "wrapped": wrapped,
            "keybag": keybag,
            "manifest_plain_bytes": len(manifest_plain),
        }

    def _parse(self, fixture: dict, category: str, *, local: bool, metrics=None):
        root = fixture["root"]
        if local:
            path = root / "baseline" / f"{category}.sqlite3"
            path.parent.mkdir(exist_ok=True)
            for role, suffix in (("main", ""), ("wal", "-wal")):
                copy_backup_file(
                    fixture["encrypted_paths"][role],
                    Path(str(path) + suffix),
                    keybag=fixture["keybag"],
                    encryption_key=fixture["wrapped"][role],
                    padded=True,
                )
            self.assertEqual(path.read_bytes(), fixture["original"]["main"])
            self.assertEqual(
                Path(str(path) + "-wal").read_bytes(), fixture["original"]["wal"]
            )
        else:
            path = self.virtual_root / category
        store_path = root / (
            ("baseline" if local else "candidate")
            + f"-review-{uuid.uuid4().hex}.sqlite3"
        )
        store = RecordStore(store_path)
        try:
            if local:
                result = self.parse(path, category, store)
            else:
                with patch(
                    "amplifai_phone.metadata._open_readonly",
                    side_effect=lambda _path: open_apple_grouped_wal_source(
                        fixture["set"],
                        fixture["indexes"]["main"],
                        fixture["indexes"]["wal"],
                        fixture["objects"],
                        self.object_key,
                        self.receipt_key,
                        fixture["entries"]["main"],
                        fixture["entries"]["wal"],
                        fixture["authority"],
                        metrics=metrics,
                    ),
                ):
                    result = self.parse(path, category, store)
            return result.rows_seen, list(result.records), store_path
        finally:
            store.close()

    def _code(self, expected: str, action) -> None:
        with self.assertRaises(ObjectVfsError) as raised:
            action()
        self.assertEqual(raised.exception.code, expected)

    def test_normal_encrypted_backup_parser_reads_selected_calls_with_contacts(self) -> None:
        # Use the real Manifest.plist/keybag/Manifest.db/file entry path, while
        # retaining the machine's disk reserve in product code. The tiny fixture
        # alone is allowed to run below that reserve on a crowded test host.
        with patch("amplifai_phone.backup_files.MIN_FREE_BYTES", 0):
            fixture = self.fixture("calls", page_size=4096)
            root = fixture["root"]
            contacts_main, _, _ = self.create_wal_pair("contacts", 1024)
            control = root / "expanded-manifest.db"
            control.write_bytes(fixture["manifest_copy"].read_bytes())
            with closing(sqlite3.connect(control)) as manifest_db:
                for suffix in ("", "-wal"):
                    relative = DATABASES["contacts"] + suffix
                    path = Path(str(contacts_main) + suffix)
                    payload = path.read_bytes()
                    file_key = os.urandom(32)
                    wrapped = struct.pack("<I", 1) + aes_key_wrap(
                        fixture["keybag"].get_key(1), file_key
                    )
                    file_id = hashlib.sha1(
                        (HOME_DOMAIN + "-" + relative).encode()
                    ).hexdigest()
                    selected = root / file_id[:2] / file_id
                    selected.parent.mkdir(exist_ok=True)
                    selected.write_bytes(_encrypt(payload, file_key, padded=True))
                    manifest_db.execute(
                        "INSERT INTO Files VALUES (?, ?, ?, ?, ?)",
                        (
                            file_id, HOME_DOMAIN, relative, 1,
                            _archived_entry(relative, len(payload), wrapped),
                        ),
                    )
                manifest_db.commit()
            (root / "Manifest.db").write_bytes(
                _encrypt(control.read_bytes(), fixture["manifest_key"], padded=False)
            )
            (root / "Info.plist").write_bytes(
                plistlib.dumps({"Product Version": "18.0"})
            )
            (root / "Status.plist").write_bytes(
                plistlib.dumps({"SnapshotState": "finished"})
            )

            capture = parse_selected_backup(
                root, self.password, datetime(2026, 10, 9, tzinfo=UTC)
            )
            self.assertIs(capture.backup_encrypted, True)
            self.assertEqual(capture.contacts.rows_seen, 80)
            self.assertEqual(capture.calls.rows_seen, 80)
            self.assertEqual({row.direction for row in capture.calls.records},
                             {"incoming", "outgoing"})
            self.assertEqual(capture.missing_sources, ("messages",))
            self.assertEqual(capture.unavailable_reasons, (("messages", "absent"),))

            from cryptography.hazmat.primitives.keywrap import InvalidUnwrap

            with self.assertRaises(InvalidUnwrap):
                parse_selected_backup(root, "wrong-synthetic-password")

            selected_calls_wal = fixture["encrypted_paths"]["wal"]
            selected_calls_wal.unlink()
            unavailable = parse_selected_backup(root, self.password)
            self.assertEqual(unavailable.contacts.rows_seen, 80)
            self.assertEqual(unavailable.calls.rows_seen, 0)
            self.assertIn("calls", unavailable.missing_sources)
            self.assertIn(("calls", "selected_payload_missing"),
                          unavailable.unavailable_reasons)

    def test_parser_parity_three_page_sizes_and_bounded_local_floor(self) -> None:
        measurements = []
        for category, page_size in (
            ("contacts", 1024),
            ("calls", 4096),
            ("messages", 8192),
        ):
            with self.subTest(category=category):
                fixture = self.fixture(category, page_size)
                self.assertEqual(
                    fixture["set"].main.sha256,
                    hashlib.sha256(
                        fixture["encrypted_paths"]["main"].read_bytes()
                    ).hexdigest(),
                )
                self.assertFalse(
                    any(
                        b"SQLite format 3" in value
                        or b"UNRELATED-LOCAL-CONTROL" in value
                        for value in fixture["objects"].objects.values()
                    )
                )
                local_rows, local_records, _ = self._parse(
                    fixture, category, local=True
                )
                metrics = {}
                object_rows, object_records, review_path = self._parse(
                    fixture, category, local=False, metrics=metrics
                )
                self.assertEqual(
                    (object_rows, object_records), (local_rows, local_records)
                )
                self.assertGreater(object_rows, 0)
                self.assertGreater(len(fixture["original"]["main"]), CACHE_BYTES)
                self.assertGreater(len(fixture["original"]["wal"]), CACHE_BYTES)
                self.assertLessEqual(
                    metrics["grouped_plaintext_cache_peak_bytes"], CACHE_BYTES
                )
                self.assertLessEqual(metrics["cbc_window_peak_bytes"], 128 * 1024 + 32)
                self.assertLessEqual(metrics["descriptor_page_peak_bytes"], 32 * 1024)
                review_bytes = sum(
                    path.stat().st_size
                    for path in review_path.parent.glob(review_path.name + "*")
                )
                control_bytes = sum(
                    path.stat().st_size
                    for path in (
                        fixture["plist"],
                        fixture["manifest_cipher"],
                        fixture["manifest_copy"],
                    )
                )
                index_bytes = sum(
                    path.stat().st_size for path in fixture["indexes"].values()
                )
                measurements.append(
                    {
                        "source": category,
                        "page_size": page_size,
                        "main_plaintext_bytes": len(fixture["original"]["main"]),
                        "wal_plaintext_bytes": len(fixture["original"]["wal"]),
                        "local_control_bytes": control_bytes,
                        "private_descriptor_index_bytes": index_bytes,
                        "sanitized_review_store_bytes": review_bytes,
                        "candidate_local_floor_sum_bytes": control_bytes
                        + index_bytes
                        + review_bytes,
                        "outer_ciphertext_object_bytes": sum(
                            len(value) for value in fixture["objects"].objects.values()
                        ),
                        **metrics,
                    }
                )
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        observed_rss_bytes = peak if sys.platform == "darwin" else peak * 1024
        output = os.environ.get("AMPLIFAI_APPLE_OBJECT_MEASURE_PATH")
        if output:
            Path(output).write_text(
                json.dumps(
                    {
                        "sources": measurements,
                        "process_rss_high_water_bytes": observed_rss_bytes,
                        "rss_scope": "whole fixture process, includes input generation and baseline",
                    },
                    indent=2,
                )
                + "\n"
            )

    def test_cbc_first_final_unaligned_cross_chunk_and_preverify_denial(self) -> None:
        fixture = self.fixture("contacts", 1024)
        grouped = GroupedRangeReader(
            fixture["set"].main,
            fixture["indexes"]["main"],
            fixture["objects"],
            self.object_key,
            self.receipt_key,
        )
        reader = AppleCbcRangeReader(
            grouped, fixture["entries"]["main"], fixture["authority"]
        )
        try:
            self._code("apple_source_unverified", lambda: reader.read(0, 16))
            reader.verify_complete()
            expected = fixture["original"]["main"]
            for offset, amount in (
                (0, 16),
                (1, 31),
                (15, 65),
                (CHUNK_BYTES - 7, 57),
                (len(expected) - 33, 33),
                (len(expected) - 1, 1),
            ):
                with self.subTest(offset=offset):
                    self.assertEqual(
                        reader.read(offset, amount), expected[offset : offset + amount]
                    )
            self.assertEqual(
                reader.read(31, 1024 * 1024), expected[31 : 31 + 1024 * 1024]
            )
            self.assertGreater(reader.peak_cbc_window_bytes, 128 * 1024)
            self.assertLessEqual(reader.peak_cbc_window_bytes, 128 * 1024 + 32)
            self._code("apple_range_invalid", lambda: reader.read(len(expected), 1))
            self.current.active = False
            self._code("apple_authority_stale", lambda: reader.read(0, 16))
            self.current.active = True
        finally:
            reader.close()

    def test_truncated_padding_declared_length_and_outer_integrity_fail_closed(
        self,
    ) -> None:
        short = self.fixture("contacts")
        late_original_object_id = list(short["objects"].objects)[-1]
        wal_path = short["encrypted_paths"]["wal"]
        wal_path.write_bytes(wal_path.read_bytes()[:-1])
        truncated_wal = package_grouped_source(
            wal_path,
            short["root"] / "truncated-wal.index",
            short["set"].wal.binding,
            "wal",
            short["objects"],
            self.object_key,
            self.receipt_key,
        )
        self._code(
            "apple_ciphertext_length",
            lambda: LocalAppleEntry.from_pinned_entry(
                self._entry_from_claim(short, "wal"),
                truncated_wal,
                short["authority"].manifest_sha256,
            ),
        )
        bad = self.fixture("calls", bad_padding=True)
        self._code(
            "apple_padding_invalid", lambda: self._parse(bad, "calls", local=False)
        )
        normal = self.fixture("messages")
        self._rewrite_local_manifest_size(normal, "wal", 1)
        normal["authority"].close()
        normal["authority"] = LocalKeyAuthority(
            self.admission,
            normal["plist"],
            normal["manifest_cipher"],
            self.password,
            self.current,
        )
        self.addCleanup(normal["authority"].close)
        normal["entries"] = {
            role: normal["authority"].project_entry(receipt)
            for role, receipt in (
                ("main", normal["set"].main),
                ("wal", normal["set"].wal),
            )
        }
        self._code(
            "apple_padding_invalid",
            lambda: self._parse(normal, "messages", local=False),
        )
        intact = short
        name = late_original_object_id
        payload = intact["objects"].objects[name]
        intact["objects"].objects[name] = bytes([payload[0] ^ 1]) + payload[1:]
        self._code(
            "vfs_ciphertext_integrity",
            lambda: self._parse(intact, "contacts", local=False),
        )

    def _entry_from_claim(self, fixture: dict, role: str) -> Entry:
        claim = fixture["entries"][role]
        now = datetime.now(UTC)
        return Entry(
            SimpleNamespace(path=fixture["root"], ios_version=Version("18.0")),
            claim.file_id,
            HOME_DOMAIN,
            DATABASES[claim.binding.source] + ("-wal" if role == "wal" else ""),
            now,
            now,
            now,
            claim.size,
            0o100600,
            0,
            0,
            claim.wrapped_file_key,
        )

    def _rewrite_local_manifest_size(
        self, fixture: dict, role: str, increase: int
    ) -> None:
        claim = fixture["entries"][role]
        relative = DATABASES[claim.binding.source] + ("-wal" if role == "wal" else "")
        with closing(sqlite3.connect(fixture["manifest_copy"])) as control, control:
            control.execute(
                "UPDATE Files SET file=? WHERE domain=? AND relativePath=?",
                (
                    _archived_entry(
                        relative,
                        claim.size + increase,
                        claim.wrapped_file_key,
                    ),
                    HOME_DOMAIN,
                    relative,
                ),
            )
        fixture["manifest_cipher"].write_bytes(
            _encrypt(
                fixture["manifest_copy"].read_bytes(),
                fixture["manifest_key"],
                padded=False,
            )
        )

    def test_wrong_key_binding_stale_authority_and_resume_with_local_password(
        self,
    ) -> None:
        fixture = self.fixture("contacts")
        first, records, _ = self._parse(fixture, "contacts", local=False)
        old_entry = fixture["entries"]["main"]
        self.assertNotIn(self.password, repr(fixture["authority"]))
        self.assertNotIn("EncryptionKey", repr(old_entry))
        self.assertGreaterEqual(logging.getLogger("pyiosbackup").level, logging.WARNING)
        with self.assertRaises(AttributeError):
            old_entry.plaintext_size = 0
        with self.assertRaises(TypeError):
            pickle.dumps(fixture["authority"])
        with self.assertRaises(TypeError):
            pickle.dumps(old_entry)
        fixture["authority"].close()
        self._code(
            "apple_authority_closed",
            lambda: self._parse(fixture, "contacts", local=False),
        )
        fixture["authority"] = LocalKeyAuthority(
            self.admission,
            fixture["plist"],
            fixture["manifest_cipher"],
            self.password,
            self.current,
        )
        self.addCleanup(fixture["authority"].close)
        resumed, resumed_records, _ = self._parse(fixture, "contacts", local=False)
        self.assertEqual((resumed, resumed_records), (first, records))
        wrong_attempt = replace(old_entry.binding, attempt_id=str(uuid.uuid4()))
        fixture["entries"]["main"] = LocalAppleEntry(
            wrong_attempt,
            old_entry.role,
            old_entry.file_id,
            old_entry.size,
            old_entry.ciphertext_sha256,
            old_entry.manifest_sha256,
            old_entry.wrapped_file_key,
        )
        self._code(
            "apple_binding_invalid",
            lambda: self._parse(fixture, "contacts", local=False),
        )
        for field in ("owner_id", "phone_id", "attempt_id", "source_set_id", "source"):
            with self.subTest(binding_field=field):
                replacement = "calls" if field == "source" else str(uuid.uuid4())
                changed = replace(old_entry.binding, **{field: replacement})
                fixture["entries"]["main"] = LocalAppleEntry(
                    changed,
                    old_entry.role,
                    old_entry.file_id,
                    old_entry.size,
                    old_entry.ciphertext_sha256,
                    old_entry.manifest_sha256,
                    old_entry.wrapped_file_key,
                )
                self._code(
                    "apple_binding_invalid",
                    lambda: self._parse(fixture, "contacts", local=False),
                )
        fixture["entries"]["main"] = old_entry
        wrapped = bytearray(old_entry.wrapped_file_key)
        wrapped[-1] ^= 1
        fixture["entries"]["main"] = LocalAppleEntry(
            old_entry.binding,
            old_entry.role,
            old_entry.file_id,
            old_entry.size,
            old_entry.ciphertext_sha256,
            old_entry.manifest_sha256,
            bytes(wrapped),
        )
        self._code(
            "apple_manifest_entry_invalid",
            lambda: self._parse(fixture, "contacts", local=False),
        )
        self._code(
            "apple_key_invalid",
            lambda: LocalKeyAuthority(
                self.admission,
                fixture["plist"],
                fixture["manifest_cipher"],
                "wrong synthetic password",
                self.current,
            ),
        )

    def test_replayed_manifest_consent_and_cancellation_fail_closed(self) -> None:
        fixture = self.fixture("calls")
        original_control = fixture["manifest_cipher"].read_bytes()
        fixture["manifest_cipher"].write_bytes(original_control + b"stale")
        self._code(
            "apple_control_invalid",
            lambda: LocalKeyAuthority(
                self.admission,
                fixture["plist"],
                fixture["manifest_cipher"],
                self.password,
                self.current,
            ),
        )
        fixture["manifest_cipher"].write_bytes(original_control)
        self._rewrite_local_manifest_size(fixture, "wal", 1)
        fresh = LocalKeyAuthority(
            self.admission,
            fixture["plist"],
            fixture["manifest_cipher"],
            self.password,
            self.current,
        )
        self.addCleanup(fresh.close)
        fixture["authority"] = fresh
        self._code(
            "apple_manifest_entry_invalid",
            lambda: self._parse(fixture, "calls", local=False),
        )
        stable = self.fixture("messages")
        self.current.active = False
        self._code(
            "apple_authority_stale",
            lambda: self._parse(stable, "messages", local=False),
        )
        self.current.active = True
        count = 0

        def cancel() -> None:
            nonlocal count
            count += 1
            if count >= 3:
                raise RuntimeError("synthetic cancellation")

        with patch(
            "amplifai_phone.metadata._open_readonly",
            side_effect=lambda _path: open_apple_grouped_wal_source(
                stable["set"],
                stable["indexes"]["main"],
                stable["indexes"]["wal"],
                stable["objects"],
                self.object_key,
                self.receipt_key,
                stable["entries"]["main"],
                stable["entries"]["wal"],
                stable["authority"],
                check_cancel=cancel,
            ),
        ):
            store = RecordStore(stable["root"] / "cancel-review.sqlite3")
            try:
                self._code(
                    "vfs_cancelled",
                    lambda: read_messages(
                        self.virtual_root / "cancel",
                        RETAINED_HISTORY_START,
                        store=store,
                    ),
                )
            finally:
                store.close()

    def test_local_manifest_sidecar_and_after_admission_mutation_are_explicit(
        self,
    ) -> None:
        fixture = self.fixture("contacts")
        sidecar = Path(str(fixture["manifest_cipher"]) + "-wal")
        sidecar.write_bytes(b"unqualified-local-control-sidecar")
        self._code(
            "apple_control_sidecar_unqualified",
            lambda: LocalKeyAuthority(
                self.admission,
                fixture["plist"],
                fixture["manifest_cipher"],
                self.password,
                self.current,
            ),
        )
        sidecar.unlink()
        fixture["manifest_cipher"].write_bytes(fixture["manifest_cipher"].read_bytes())
        self._code(
            "apple_control_changed",
            lambda: self._parse(fixture, "contacts", local=False),
        )

    def test_control_copy_disk_failure_remains_storage_and_cleans_scratch(self) -> None:
        fixture = self.fixture("contacts")
        before = set(fixture["root"].glob("apple-local-manifest-*"))
        with patch(
            "amplifai_phone.offline_apple_object_reader.copy_backup_file",
            side_effect=WorkspaceError(
                "synthetic disk full", code="workspace_low_space"
            ),
        ):
            self._code(
                "apple_control_storage",
                lambda: LocalKeyAuthority(
                    self.admission,
                    fixture["plist"],
                    fixture["manifest_cipher"],
                    self.password,
                    self.current,
                ),
            )
        self.assertEqual(set(fixture["root"].glob("apple-local-manifest-*")), before)
