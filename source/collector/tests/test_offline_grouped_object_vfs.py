"""Synthetic-only grouped encrypted Range and descriptor-index qualification."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    import apsw
except ModuleNotFoundError:
    apsw = None

import test_offline_object_vfs as legacy_fixtures
from amplifai_phone.metadata import read_contacts
from amplifai_phone.record_store import RecordStore

if apsw is not None:
    from amplifai_phone.offline_grouped_object_vfs import (
        GROUP_CHUNKS,
        MAX_DESCRIPTOR_PAGE,
        PAGE_CHUNKS,
        GroupedRangeReader,
        GroupedSourceReceipt,
        ObjectVfsError,
        RangeReply,
        SourceBinding,
        _canonical,
        _index_update,
        _page_mac,
        _source_payload,
        open_grouped_wal_source,
        package_grouped_source,
        seal_grouped_wal_set,
    )
    from amplifai_phone.offline_object_vfs import (
        CACHE_BYTES,
        CHUNK_BYTES,
        open_frozen_wal_source,
    )


class LocalRangeObjects(legacy_fixtures.LocalCipherObjects):
    def __init__(self, root: Path):
        super().__init__(root)
        self.requests = 0
        self.bytes = 0
        self.fault: str | None = None
        self.fault_count = 0

    def get_range(self, object_id: str, start: int, end: int, max_bytes: int):
        self.requests += 1
        fault = None
        if self.fault is not None and self.fault_count:
            fault = self.fault
            self.fault_count -= 1
            if self.fault_count == 0:
                self.fault = None
            if fault == "timeout":
                raise TimeoutError("synthetic transient failure")
            if fault == "missing":
                raise KeyError(object_id)
        with (self.root / object_id).open("rb") as stream:
            total = stream.seek(0, os.SEEK_END)
            stream.seek(start)
            body = stream.read(min(max_bytes + 1, end - start + 1))
        if fault == "short":
            body = body[:-1]
        elif fault == "wrong_range":
            return RangeReply(206, f"bytes 1-{end}/{total}", max_bytes, body)
        elif fault == "wrong_total":
            return RangeReply(206, f"bytes {start}-{end}/{total + 1}", max_bytes, body)
        elif fault == "wrong_status":
            return RangeReply(200, f"bytes {start}-{end}/{total}", max_bytes, body)
        elif fault == "wrong_length":
            return RangeReply(206, f"bytes {start}-{end}/{total}", max_bytes + 1, body)
        elif fault == "server_busy":
            return RangeReply(503, "", 0, b"")
        self.bytes += len(body)
        return RangeReply(206, f"bytes {start}-{end}/{total}", max_bytes, body)


class CountingLegacyBackend:
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.requests = 0
        self.bytes = 0

    def get(self, object_id, max_bytes):
        data = self.wrapped.get(object_id, max_bytes)
        self.requests += 1
        self.bytes += len(data)
        return data


@unittest.skipIf(apsw is None, "APSW is isolated from the release lock")
class GroupedWalOfflineTest(unittest.TestCase):
    setUp = legacy_fixtures.FrozenWalObjectVfsTest.setUp
    create_source = legacy_fixtures.FrozenWalObjectVfsTest.create_source
    create_wal_pair = legacy_fixtures.FrozenWalObjectVfsTest.create_wal_pair
    package_member = legacy_fixtures.FrozenWalObjectVfsTest.package_member
    local_parse = legacy_fixtures.FrozenWalObjectVfsTest.local_parse
    parse = staticmethod(legacy_fixtures.FrozenWalObjectVfsTest.parse)

    def grouped(self, path: Path, frozen):
        self.grouped_objects = LocalRangeObjects(
            self.root / f"grouped-{uuid.uuid4().hex}"
        )
        self.main_index = self.root / f"main-{uuid.uuid4().hex}.index"
        self.wal_index = self.root / f"wal-{uuid.uuid4().hex}.index"
        main = package_grouped_source(
            path,
            self.main_index,
            frozen.main.binding,
            "main",
            self.grouped_objects,
            self.key,
            self.receipt_key,
            sidecars=frozen.main.sidecars,
        )
        wal = package_grouped_source(
            Path(str(path) + "-wal"),
            self.wal_index,
            frozen.main.binding,
            "wal",
            self.grouped_objects,
            self.key,
            self.receipt_key,
        )
        return seal_grouped_wal_set(main, wal, self.receipt_key)

    def grouped_open(self, source_set, *, check_cancel=None):
        return open_grouped_wal_source(
            source_set,
            self.main_index,
            self.wal_index,
            self.grouped_objects,
            self.key,
            self.receipt_key,
            check_cancel=check_cancel,
        )

    def grouped_parse(self, source_set, source):
        store = RecordStore(self.root / f"grouped-store-{uuid.uuid4().hex}.sqlite3")
        try:
            with patch(
                "amplifai_phone.metadata._open_readonly",
                side_effect=lambda _path: self.grouped_open(source_set),
            ):
                result = self.parse(self.virtual_root / source, source, store)
            return result.rows_seen, list(result.records)
        finally:
            store.close()

    def assert_code(self, code, action):
        with self.assertRaises(ObjectVfsError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_parser_parity_cold_warm_eviction_reopen_and_request_comparison(self):
        for source, page_size in (
            ("contacts", 1024),
            ("calls", 4096),
            ("messages", 8192),
        ):
            with self.subTest(source=source):
                path, db, frozen = self.create_wal_pair(source, page_size)
                local = self.local_parse(path, source)
                grouped = self.grouped(path, frozen)
                self.assertGreater(grouped.main.size, CACHE_BYTES)
                self.assertGreater(grouped.wal.size, CACHE_BYTES)
                db.close()
                path.unlink()
                baseline_backend = CountingLegacyBackend(self.objects)
                with open_frozen_wal_source(
                    frozen, baseline_backend, self.key, self.receipt_key
                ) as baseline:
                    self.assertEqual(
                        baseline.execute("PRAGMA journal_mode").fetchone()[0], "wal"
                    )
                    baseline.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchone()
                    baseline.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchone()
                    baseline_reads = baseline_backend.requests
                    baseline_bytes = baseline_backend.bytes
                with self.grouped_open(grouped) as opened:
                    main_reader = opened.vfs.reader
                    wal_reader = opened.vfs.wal_reader
                    first = opened.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchone()
                    self.assertIsNotNone(first)
                    self.assertEqual(
                        opened.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                        ).fetchone()[0],
                        first[0],
                    )
                    self.assertLessEqual(
                        main_reader.peak_cached_bytes + wal_reader.peak_cached_bytes,
                        CACHE_BYTES,
                    )
                    self.assertLessEqual(
                        max(
                            main_reader.peak_descriptor_bytes,
                            wal_reader.peak_descriptor_bytes,
                        ),
                        MAX_DESCRIPTOR_PAGE,
                    )
                    self.assertEqual(opened.vfs.mutation_attempts, 0)
                    grouped_reads = (
                        main_reader.objects_fetched + wal_reader.objects_fetched
                    )
                    grouped_bytes = main_reader.bytes_fetched + wal_reader.bytes_fetched
                self.assertLess(grouped_reads, baseline_reads)
                # Grouped verification should only add AEAD tags, not whole-file copies.
                self.assertLessEqual(grouped_bytes, baseline_bytes * 1.1)
                self.assertEqual(self.grouped_parse(grouped, source), local)
                self.assertEqual(self.grouped_parse(grouped, source), local)
                self.assertFalse((self.virtual_root / source).exists())
                self.assertFalse(
                    any(
                        b"PRIVATE-MESSAGE-" in item.read_bytes()
                        for item in self.grouped_objects.root.iterdir()
                    )
                )

    def test_exact_ranges_random_reopen_and_partial_reply_retries(self):
        path, db, frozen = self.create_wal_pair("contacts")
        expected = path.read_bytes()
        grouped = self.grouped(path, frozen)
        db.close()
        path.unlink()
        reader = GroupedRangeReader(
            grouped.main,
            self.main_index,
            self.grouped_objects,
            self.key,
            self.receipt_key,
        )
        try:
            reader.verify_complete()
            before_cold = self.grouped_objects.requests
            self.assertEqual(reader.read(0, 31), expected[:31])
            after_cold = self.grouped_objects.requests
            self.assertGreater(after_cold, before_cold)
            self.assertEqual(reader.read(0, 31), expected[:31])
            self.assertEqual(self.grouped_objects.requests, after_cold)
            for offset in (0, CHUNK_BYTES - 7, len(expected) // 2, len(expected) - 31):
                self.assertEqual(
                    reader.read(offset, 31), expected[offset : offset + 31]
                )
            self.assertLessEqual(reader.peak_cached_bytes, CHUNK_BYTES)
            self.assertGreater(reader.descriptor_pages_fetched, 0)
            after_eviction = self.grouped_objects.requests
            self.assertEqual(reader.read(0, 31), expected[:31])
            self.assertGreater(self.grouped_objects.requests, after_eviction)
        finally:
            reader.close()
        for fault in ("short", "timeout", "server_busy"):
            with self.subTest(fault=fault):
                self.grouped_objects.fault = fault
                self.grouped_objects.fault_count = 1
                reopened = GroupedRangeReader(
                    grouped.main,
                    self.main_index,
                    self.grouped_objects,
                    self.key,
                    self.receipt_key,
                )
                try:
                    self.assertEqual(reopened.read(0, 32), expected[:32])
                finally:
                    reopened.close()
        for fault, expected_code in (
            ("short", "vfs_range_short"),
            ("timeout", "vfs_range_unavailable"),
            ("server_busy", "vfs_range_unavailable"),
        ):
            with self.subTest(exhausted=fault):
                self.grouped_objects.fault = fault
                self.grouped_objects.fault_count = 3
                reopened = GroupedRangeReader(
                    grouped.main,
                    self.main_index,
                    self.grouped_objects,
                    self.key,
                    self.receipt_key,
                )
                try:
                    self.assert_code(
                        expected_code, lambda reader=reopened: reader.read(0, 32)
                    )
                finally:
                    reopened.close()
        for fault in ("wrong_range", "wrong_total", "wrong_status", "wrong_length"):
            with self.subTest(fault=fault):
                self.grouped_objects.fault = fault
                self.grouped_objects.fault_count = 1
                reopened = GroupedRangeReader(
                    grouped.main,
                    self.main_index,
                    self.grouped_objects,
                    self.key,
                    self.receipt_key,
                )
                try:
                    self.assert_code(
                        "vfs_range_response_invalid",
                        lambda reader=reopened: reader.read(0, 32),
                    )
                finally:
                    reopened.close()

    def test_missing_corrupt_reordered_stale_cross_binding_and_index_fail_closed(self):
        path, db, frozen = self.create_wal_pair("contacts")
        grouped = self.grouped(path, frozen)
        db.close()
        path.unlink()
        names = list(self.grouped_objects.root.iterdir())
        self.assertGreater(len(names), 2)
        original = names[0].read_bytes()
        self.grouped_objects.remove(names[0].name)
        self.assert_code(
            "vfs_object_missing", lambda: self.grouped_parse(grouped, "contacts")
        )
        names[0].write_bytes(original)
        names[0].write_bytes(bytes([original[0] ^ 1]) + original[1:])
        self.assert_code(
            "vfs_ciphertext_integrity", lambda: self.grouped_parse(grouped, "contacts")
        )
        names[0].write_bytes(original)
        first_equal, equal_length = next(
            (
                (left, right)
                for position, left in enumerate(names)
                for right in names[position + 1 :]
                if left.stat().st_size == right.stat().st_size
            ),
            (None, None),
        )
        self.assertIsNotNone(first_equal)
        self.assertIsNotNone(equal_length)
        first_content = first_equal.read_bytes()
        stale = equal_length.read_bytes()
        first_equal.write_bytes(stale)
        self.assert_code(
            "vfs_ciphertext_integrity", lambda: self.grouped_parse(grouped, "contacts")
        )
        first_equal.write_bytes(first_content)
        first_equal.write_bytes(stale)
        equal_length.write_bytes(first_content)
        self.assert_code(
            "vfs_ciphertext_integrity", lambda: self.grouped_parse(grouped, "contacts")
        )
        first_equal.write_bytes(first_content)
        equal_length.write_bytes(stale)
        for field in ("owner_id", "phone_id", "attempt_id", "source_set_id"):
            with self.subTest(wrong_binding=field):
                wrong = replace(
                    grouped.main,
                    binding=replace(grouped.main.binding, **{field: str(uuid.uuid4())}),
                )
                self.assert_code(
                    "vfs_index_receipt_invalid",
                    lambda candidate=wrong: seal_grouped_wal_set(
                        candidate, grouped.wal, self.receipt_key
                    ),
                )
        with sqlite3.connect(self.main_index) as index:
            index.execute("UPDATE descriptor_pages SET payload=X'01' WHERE page=0")
        self.assert_code(
            "vfs_index_integrity", lambda: self.grouped_parse(grouped, "contacts")
        )

    def test_index_missing_and_interrupted_producer_do_not_admit(self):
        path, db, frozen = self.create_wal_pair("contacts")
        grouped = self.grouped(path, frozen)
        with sqlite3.connect(self.main_index) as index:
            index.execute("DELETE FROM descriptor_pages WHERE page=0")
        self.assert_code(
            "vfs_index_integrity", lambda: self.grouped_parse(grouped, "contacts")
        )

        class InterruptedWriter:
            def put(self, _object_id, _ciphertext):
                raise OSError("synthetic interruption")

        new_index = self.root / "interrupted.index"
        with self.assertRaises(OSError):
            package_grouped_source(
                path,
                new_index,
                frozen.main.binding,
                "main",
                InterruptedWriter(),
                self.key,
                self.receipt_key,
                sidecars=("wal",),
            )
        self.assertFalse(new_index.exists())
        self.assertFalse(list(self.root.glob(".amplifai-index-*")))
        db.close()

    def test_rebound_signed_index_cannot_reuse_other_attempt_ciphertext(self):
        path, db, frozen = self.create_wal_pair("contacts")
        grouped = self.grouped(path, frozen)
        db.close()
        path.unlink()
        new_binding = replace(grouped.main.binding, attempt_id=str(uuid.uuid4()))

        def rebind(receipt, original_index):
            new_index = self.root / f"rebound-{receipt.role}.index"
            shutil.copy2(original_index, new_index)
            digest = hashlib.sha256()
            with sqlite3.connect(new_index) as index:
                pages = list(
                    index.execute(
                        "SELECT page, payload FROM descriptor_pages ORDER BY page"
                    )
                )
                for page, payload in pages:
                    mac = _page_mac(
                        self.receipt_key, new_binding, receipt.role, page, payload
                    )
                    index.execute(
                        "UPDATE descriptor_pages SET mac=? WHERE page=?", (mac, page)
                    )
                    _index_update(digest, page, payload, mac)
            unsigned = replace(
                receipt, binding=new_binding, index_sha256=digest.hexdigest(), mac=""
            )
            signed = replace(
                unsigned,
                mac=hmac.new(
                    self.receipt_key, _source_payload(unsigned), hashlib.sha256
                ).hexdigest(),
            )
            return signed, new_index

        main, rebound_main = rebind(grouped.main, self.main_index)
        wal, rebound_wal = rebind(grouped.wal, self.wal_index)
        resealed = seal_grouped_wal_set(main, wal, self.receipt_key)
        self.main_index, self.wal_index = rebound_main, rebound_wal
        self.assert_code(
            "vfs_binding_integrity", lambda: self.grouped_parse(resealed, "contacts")
        )

    def test_cancel_and_no_source_write(self):
        path, db, frozen = self.create_wal_pair("contacts")
        grouped = self.grouped(path, frozen)
        db.close()
        path.unlink()
        checks = 0

        def cancel():
            nonlocal checks
            checks += 1
            if checks > 10:
                raise RuntimeError("cancel")

        def cancelled_open():
            with self.grouped_open(grouped, check_cancel=cancel):
                self.fail("Cancelled source opened")

        self.assert_code("vfs_cancelled", cancelled_open)
        with self.grouped_open(grouped) as opened:
            self.assertEqual(opened.vfs.mutation_attempts, 0)
            self.assertEqual(
                opened.execute("SELECT First FROM ABPerson WHERE ROWID=1").fetchone()[
                    0
                ],
                "WAL Updated",
            )
            vfs = opened.vfs
        self.assertEqual(vfs.mutation_attempts, 0)
        self.assertFalse(any(self.virtual_root.iterdir()))

    def test_large_main_wal_and_descriptor_only_rss(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "collector:collector/tests"
        result = subprocess.run(
            [sys.executable, "-m", __name__, "--rss-worker"],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        measured = json.loads(result.stdout)
        self.assertGreater(measured["main_bytes"], 16 * 1024 * 1024)
        self.assertGreater(measured["wal_bytes"], 16 * 1024 * 1024)
        self.assertEqual(measured["rows_seen"], 1024)
        self.assertEqual(measured["retained"], 1024)
        self.assertLessEqual(measured["cache_peak"], CACHE_BYTES)
        self.assertLessEqual(measured["descriptor_page_peak"], MAX_DESCRIPTOR_PAGE)
        self.assertLess(measured["rss_delta"], 48 * 1024 * 1024)
        self.assertLess(measured["requests_grouped"], measured["requests_baseline"])
        descriptor = subprocess.run(
            [sys.executable, "-m", __name__, "--descriptor-worker"],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        scaled = json.loads(descriptor.stdout)
        self.assertGreater(scaled["descriptor_only_chunk_count"], 60_000)
        self.assertLessEqual(scaled["descriptor_only_page_peak"], MAX_DESCRIPTOR_PAGE)
        self.assertLess(scaled["descriptor_only_rss_delta"], 32 * 1024 * 1024)


def _rss_worker():
    case = GroupedWalOfflineTest("test_large_main_wal_and_descriptor_only_rss")
    case.setUp()
    try:
        unit = 1 if sys.platform == "darwin" else 1024
        whole_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        path, db, frozen = case.create_wal_pair("contacts", rows=1024)
        local = case.local_parse(path, "contacts")
        grouped = case.grouped(path, frozen)
        db.close()
        path.unlink()
        start_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        with case.grouped_open(grouped) as opened:
            main = opened.vfs.reader
            wal = opened.vfs.wal_reader
            store = RecordStore(case.root / "grouped-rss-sanitized.sqlite3")
            try:

                @contextmanager
                def borrow(_path):
                    yield opened

                with patch(
                    "amplifai_phone.metadata._open_readonly", side_effect=borrow
                ):
                    result = read_contacts(case.virtual_root / "contacts", store=store)
                actual = (result.rows_seen, list(result.records))
            finally:
                store.close()
            grouped_requests = main.objects_fetched + wal.objects_fetched
            grouped_bytes = main.bytes_fetched + wal.bytes_fetched
            cache_peak = main.peak_cached_bytes + wal.peak_cached_bytes
            desc_peak = max(main.peak_descriptor_bytes, wal.peak_descriptor_bytes)
            page_fetches = main.descriptor_pages_fetched + wal.descriptor_pages_fetched
            pages_verified = (
                main.descriptor_pages_verified + wal.descriptor_pages_verified
            )
            index_payload_verified = (
                main.descriptor_index_bytes_verified
                + wal.descriptor_index_bytes_verified
            )
        if actual != local:
            raise AssertionError("large source parser parity")
        baseline_backend = CountingLegacyBackend(case.objects)
        with open_frozen_wal_source(
            frozen, baseline_backend, case.key, case.receipt_key
        ) as baseline:
            baseline_store = RecordStore(case.root / "baseline-rss-sanitized.sqlite3")
            try:

                @contextmanager
                def borrow_baseline(_path):
                    yield baseline

                with patch(
                    "amplifai_phone.metadata._open_readonly",
                    side_effect=borrow_baseline,
                ):
                    baseline_result = read_contacts(
                        case.virtual_root / "contacts", store=baseline_store
                    )
                if (baseline_result.rows_seen, list(baseline_result.records)) != local:
                    raise AssertionError("baseline source parser parity")
            finally:
                baseline_store.close()
            baseline_requests = baseline_backend.requests
            baseline_bytes = baseline_backend.bytes
        source_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        print(
            json.dumps(
                {
                    "main_bytes": grouped.main.size,
                    "wal_bytes": grouped.wal.size,
                    "rows_seen": actual[0],
                    "retained": len(actual[1]),
                    "requests_grouped": grouped_requests,
                    "requests_baseline": baseline_requests,
                    "bytes_grouped": grouped_bytes,
                    "bytes_baseline": baseline_bytes,
                    "grouped_over_unique_ciphertext": round(
                        grouped_bytes
                        / (
                            grouped.main.size
                            + grouped.wal.size
                            + 16 * (grouped.main.chunk_count + grouped.wal.chunk_count)
                        ),
                        4,
                    ),
                    "baseline_over_unique_ciphertext": round(
                        baseline_bytes
                        / (
                            grouped.main.size
                            + grouped.wal.size
                            + 16 * (grouped.main.chunk_count + grouped.wal.chunk_count)
                        ),
                        4,
                    ),
                    "cache_peak": cache_peak,
                    "descriptor_page_peak": desc_peak,
                    "descriptor_pages_fetched": page_fetches,
                    "descriptor_pages_verified": pages_verified,
                    "descriptor_index_payload_verified_bytes": index_payload_verified,
                    "descriptor_index_disk_bytes": case.main_index.stat().st_size
                    + case.wal_index.stat().st_size,
                    "rss_delta": source_rss - start_rss,
                    "whole_fixture_rss_delta": source_rss - whole_start,
                }
            )
        )
    finally:
        case.doCleanups()


def _descriptor_worker():
    # This fixture only measures index scaling; it is not a real database.
    case = GroupedWalOfflineTest("test_large_main_wal_and_descriptor_only_rss")
    case.setUp()
    try:
        unit = 1 if sys.platform == "darwin" else 1024
        fake_count = 65_536
        fake_index = case.root / "descriptor-only.index"
        binding = SourceBinding(
            case.owner, case.phone, case.attempt, "contacts", str(uuid.uuid4())
        )
        digest = hashlib.sha256()
        with sqlite3.connect(fake_index) as index:
            index.execute(
                "CREATE TABLE descriptor_pages (page INTEGER PRIMARY KEY, payload BLOB NOT NULL, mac TEXT NOT NULL)"
            )
            for page in range(fake_count // PAGE_CHUNKS):
                entries = []
                for offset in range(PAGE_CHUNKS):
                    part = page * PAGE_CHUNKS + offset
                    entries.append(
                        [
                            part,
                            f"{part // GROUP_CHUNKS:032x}",
                            GROUP_CHUNKS * (CHUNK_BYTES + 16),
                            (part % GROUP_CHUNKS) * (CHUNK_BYTES + 16),
                            CHUNK_BYTES + 16,
                            "00" * 12,
                            CHUNK_BYTES,
                            "0" * 64,
                            "0" * 64,
                        ]
                    )
                payload = _canonical(entries)
                mac = _page_mac(case.receipt_key, binding, "main", page, payload)
                index.execute(
                    "INSERT INTO descriptor_pages VALUES (?, ?, ?)",
                    (page, payload, mac),
                )
                _index_update(digest, page, payload, mac)
        unsigned = GroupedSourceReceipt(
            binding,
            "main",
            fake_count * CHUNK_BYTES,
            "0" * 64,
            fake_count,
            fake_count // PAGE_CHUNKS,
            digest.hexdigest(),
            ("wal",),
            "",
        )
        receipt = replace(
            unsigned,
            mac=hmac.new(
                case.receipt_key, _source_payload(unsigned), hashlib.sha256
            ).hexdigest(),
        )
        start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
        reader = GroupedRangeReader(
            receipt, fake_index, case.objects, case.key, case.receipt_key
        )
        try:
            reader._entry(fake_count - 1)
            measurement = {
                "descriptor_only_chunk_count": fake_count,
                "descriptor_only_index_bytes": fake_index.stat().st_size,
                "descriptor_only_page_peak": reader.peak_descriptor_bytes,
                "descriptor_only_pages_verified": reader.descriptor_pages_verified,
                "descriptor_only_index_bytes_verified": reader.descriptor_index_bytes_verified,
                "descriptor_only_pages_fetched": reader.descriptor_pages_fetched,
                "descriptor_only_rss_delta": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss
                * unit
                - start,
            }
        finally:
            reader.close()
        with sqlite3.connect(fake_index) as index:
            first = index.execute(
                "SELECT payload, mac FROM descriptor_pages WHERE page=0"
            ).fetchone()
            second = index.execute(
                "SELECT payload, mac FROM descriptor_pages WHERE page=1"
            ).fetchone()
            index.execute(
                "UPDATE descriptor_pages SET payload=?, mac=? WHERE page=0", second
            )
            index.execute(
                "UPDATE descriptor_pages SET payload=?, mac=? WHERE page=1", first
            )
        try:
            GroupedRangeReader(
                receipt, fake_index, case.objects, case.key, case.receipt_key
            )
        except ObjectVfsError as error:
            if error.code != "vfs_index_integrity":
                raise
            measurement["reordered_page_rejected"] = True
        else:
            raise AssertionError("Reordered descriptor pages were admitted")
        print(json.dumps(measurement))
    finally:
        case.doCleanups()


if __name__ == "__main__":
    if sys.argv[1:] == ["--rss-worker"]:
        _rss_worker()
    elif sys.argv[1:] == ["--descriptor-worker"]:
        _descriptor_worker()
    else:
        unittest.main()
