from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from amplifai_phone.agent import run_admin, run_connect
from amplifai_phone.bridge import _selected_metadata, selected_metadata
from amplifai_phone.call_accounting import (
    PARSER_FIELDS, SELECTION_FIELDS, CallAccounting, CallAccountingHistory,
    call_accounting, validate_call_accounting, validate_call_accounting_history,
)
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult, read_calls
from amplifai_phone.paged_transfer import PagedTransfer
from amplifai_phone.record_store import RecordStore
from amplifai_phone.source_diagnostics import DiagnosticHistory, DiagnosticsStorageError
from amplifai_phone.workspace import SessionWorkspace


def totals(selected=None):
    return {"rawRows": 8, "invalidDateRows": 2, "beforeCutoffRows": 1,
            "invalidAddressRows": 2, "validBeforeSelectionRows": 3,
            "selectedRows": selected,
            "selectedContactMismatchRows": None if selected is None else 3 - selected}


def synthetic_calls():
    records = tuple(Interaction(i, "call", "call", "2026-01-01T00:00:00+00:00",
                                "incoming", (phone,), 10)
                    for i, phone in enumerate(("+12025550101", "+12025550101", "+12025550102"), 1))
    return SourceResult(8, records, 5, CallAccounting(8, 2, 1, 2, 3))


class CallAccountingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="amplifai-call-accounting-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def capture(self, calls=None, workspace=None, store=None):
        return IPhoneCapture(
            SourceResult(2, (Contact(1, "Synthetic Selected", ("+12025550101",), ()),
                             Contact(2, "Synthetic Unselected", ("+12025550102",), ())), 0),
            calls or synthetic_calls(), SourceResult(0, (), 0),
            "2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00", (), workspace, store)

    def test_parser_counts_disjoint_reasons_without_changing_records(self):
        path = self.root / "synthetic-calls.sqlite3"
        cutoff = datetime(2026, 1, 1, tzinfo=UTC)
        when = cutoff.timestamp() - 978307200
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, "
                       "ZORIGINATED INTEGER, ZANSWERED INTEGER)")
            db.executemany("INSERT INTO ZCALLRECORD VALUES (?, ?, ?, ?, ?)", [
                (None, 1, "private-address@example.test", 0, 1),
                (float("inf"), 1, "+12025550101", 0, 1),
                (when - 1, 1, "private-address@example.test", 0, 1),
                (when, 1, "private-address@example.test", 0, 1),
                (when, 1, None, 0, 1),
                (when, 1, "+12025550101", 0, 1),
                (when + 1, 1, "(202) 555-0101", 1, 1),
                (when + 2, 1, "+12025550102", 0, 0),
            ])
        for disk_backed in (False, True):
            with self.subTest(disk_backed=disk_backed):
                store = RecordStore(self.root / "records.sqlite3") if disk_backed else None
                try:
                    result = read_calls(path, cutoff, store=store)
                    self.assertEqual([record.source_id for record in result.records], [6, 7, 8])
                    self.assertEqual((result.rows_seen, result.excluded), (8, 5))
                    self.assertEqual(call_accounting(result), totals())
                    self.assertEqual(call_accounting(result, 2), totals(2))
                    self.assertNotIn("private-address", repr(result.call_accounting))
                finally:
                    if store:
                        store.close()

    def test_empty_available_source_is_zero_and_legacy_source_stays_unmeasured(self):
        path = self.root / "empty.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE ZCALLRECORD (ZDATE, ZDURATION, ZADDRESS, ZORIGINATED, ZANSWERED)")
        result = read_calls(path, datetime(2001, 1, 1, tzinfo=UTC))
        self.assertEqual(call_accounting(result, 0), dict.fromkeys(PARSER_FIELDS + SELECTION_FIELDS, 0))
        self.assertIsNone(call_accounting(SourceResult(8, synthetic_calls().records, 5), 2))

    def test_strict_integer_allowlist_and_both_reconciliation_equations(self):
        self.assertEqual(validate_call_accounting(totals(2)), totals(2))
        for field in PARSER_FIELDS + SELECTION_FIELDS:
            for value in (True, -1, 1.0, "private value", float("nan"), 2**53):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_call_accounting({**totals(2), field: value})
        for value in ({**totals(2), "phone": "+12025550101"},
                      {**totals(2), "rawRows": 9}, {**totals(2), "selectedRows": None},
                      {**totals(2), "selectedContactMismatchRows": 2}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_call_accounting(value)
        for result in (replace(synthetic_calls(), rows_seen=9),
                       replace(synthetic_calls(), excluded=4),
                       replace(synthetic_calls(), records=()),
                       replace(synthetic_calls(), call_accounting={"private": "value"})):
            with self.assertRaises(ValueError):
                call_accounting(result, 2)
        with self.assertRaises(ValueError):
            call_accounting(synthetic_calls(), 4)

    def test_legacy_payload_bytes_and_paged_manifest_hash_are_unchanged(self):
        capture = self.capture()
        legacy = replace(capture, calls=replace(capture.calls, call_accounting=None))
        self.assertEqual(selected_metadata(capture, {1}), selected_metadata(legacy, {1}))
        payload, local_accounting = _selected_metadata(capture, {1})
        self.assertEqual(local_accounting, totals(2))
        self.assertNotIn("callAccounting", payload.decode())
        self.assertNotIn("invalidDateRows", payload.decode())
        workspace = SessionWorkspace(self.root / "paged-sessions")
        with patch("amplifai_phone.workspace.MIN_FREE_BYTES", 0), workspace as directory:
            store = RecordStore(directory / "records.sqlite3", workspace)
            try:
                for record in capture.contacts.records:
                    store.add("contacts", record)
                for record in capture.calls.records:
                    store.add("calls", record)
                paged = replace(capture, contacts=replace(capture.contacts, records=store.collection("contacts")),
                                calls=replace(capture.calls, records=store.collection("calls")),
                                messages=replace(capture.messages, records=store.collection("messages")),
                                review_workspace=workspace, record_store=store)
                legacy_paged = replace(paged, calls=replace(paged.calls, call_accounting=None))
                baseline = PagedTransfer(legacy_paged, {1}, directory / "legacy.sqlite3")
                current = PagedTransfer(paged, {1}, directory / "current.sqlite3")
                snapshot = store.append_selection(str(uuid4()), 0, [1], 1)
                selected = PagedTransfer(paged, snapshot, directory / "snapshot.sqlite3")
                try:
                    self.assertIsNone(baseline.call_accounting)
                    self.assertEqual(current.call_accounting, totals(2))
                    self.assertEqual(selected.call_accounting, totals(2))
                    self.assertEqual(baseline.manifest, current.manifest)
                    self.assertEqual(baseline.sha256, current.sha256)
                    self.assertEqual(current.manifest, selected.manifest)
                    self.assertEqual([row["sourceId"] for row in json.loads(current.page("calls", 0)["chunk"])], [1, 2])
                finally:
                    baseline.close()
                    current.close()
                    selected.close()
            finally:
                store.close()

    def test_history_survives_cleanup_is_separate_private_bounded_and_rejects_personal_fields(self):
        sessions = self.root / "sessions"
        history = CallAccountingHistory(sessions)
        identity = str(uuid4())
        self.assertEqual(history.load(), [])
        self.assertFalse(history.root.exists())
        history.record(identity, totals())
        history.record(identity, totals(2))
        self.assertEqual(CallAccountingHistory(sessions).load()[0]["callAccounting"], totals(2))
        self.assertEqual(DiagnosticHistory(sessions).load(), [])
        self.assertNotEqual(history.root, DiagnosticHistory(sessions).root)
        encoded = (history.root / "history.json").read_text()
        self.assertNotIn("Synthetic", encoded)
        self.assertNotIn("+1202555", encoded)
        self.assertNotIn("example.test", encoded)
        if os.name != "nt":
            self.assertEqual(history.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((history.root / "history.json").stat().st_mode & 0o777, 0o600)
        for index in range(12):
            history.record(str(uuid4()), totals(index % 4))
        self.assertEqual(len(history.load()), 10)
        before = (history.root / "history.json").read_bytes()
        with self.assertRaises(DiagnosticsStorageError):
            history.record(identity, {**totals(2), "contact": "private value"})
        self.assertEqual((history.root / "history.json").read_bytes(), before)
        with self.assertRaises(ValueError):
            validate_call_accounting_history([{"attemptId": identity, "createdAt": 0,
                                               "callAccounting": totals(2), "path": "private"}])

    def test_history_expires_old_attempts_and_rejects_future_or_duplicate_records(self):
        history = CallAccountingHistory(self.root / "sessions")
        identity = str(uuid4())
        now = 2_000_000_000
        with patch("amplifai_phone.call_accounting.time.time", return_value=now):
            history.record(identity, totals(), created_at=now - 31 * 24 * 60 * 60)
            self.assertEqual(history.load(), [])
            with self.assertRaises(DiagnosticsStorageError):
                history.record(identity, totals(2), created_at=now + 1)
            history.record(identity, totals(2), created_at=now)
            entry = history.load()[0]
            with self.assertRaises(ValueError):
                validate_call_accounting_history([entry, entry])

    def test_normal_workspace_exit_retains_only_counts_and_rejects_message_body(self):
        sessions = self.root / "sessions"
        history = CallAccountingHistory(sessions)
        identity = str(uuid4())
        private = {"messageBody": "SYNTHETIC-PRIVATE-MESSAGE-BODY",
                   "contact": "Synthetic Private Contact",
                   "phone": "+12025550999",
                   "email": "private-synthetic@example.test"}
        workspace = SessionWorkspace(sessions)
        with patch("amplifai_phone.workspace.MIN_FREE_BYTES", 0), workspace as directory:
            path = directory / "synthetic-private-calls.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, "
                           "ZORIGINATED INTEGER, ZANSWERED INTEGER, messageBody TEXT, contact TEXT, email TEXT)")
                db.executemany("INSERT INTO ZCALLRECORD VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                    (800000000, 10, address, 0, 1, private["messageBody"],
                     private["contact"], private["email"])
                    for address in (private["phone"], private["email"])
                ])
            parsed = read_calls(path, datetime(2001, 1, 1, tzinfo=UTC))
            counts = call_accounting(parsed, 0)
            self.assertEqual(counts, {"rawRows": 2, "invalidDateRows": 0,
                                     "beforeCutoffRows": 0, "invalidAddressRows": 1,
                                     "validBeforeSelectionRows": 1, "selectedRows": 0,
                                     "selectedContactMismatchRows": 1})
            history.record(identity, counts)
            self.assertTrue(path.exists())
            self.assertNotIn(directory, history.root.parents)
        # This is the normal context-manager exit, rather than crash-residue cleanup.
        self.assertFalse(directory.exists())
        self.assertFalse(path.exists())
        reloaded = CallAccountingHistory(sessions).load()
        self.assertEqual(reloaded[0]["callAccounting"], counts)
        history_file = history.root / "history.json"
        before = history_file.read_bytes()
        serialized = before.decode("ascii")
        for field, value in private.items():
            with self.subTest(field=field):
                self.assertNotIn(field, reloaded[0]["callAccounting"])
                self.assertNotIn(value, serialized)
        with self.assertRaises(DiagnosticsStorageError):
            history.record(identity, {**counts, "messageBody": private["messageBody"]})
        self.assertEqual(history_file.read_bytes(), before)

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_history_refuses_symlink_storage(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        history = CallAccountingHistory(self.root / "sessions")
        history.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(DiagnosticsStorageError):
            history.record(str(uuid4()), totals(2))
        self.assertEqual(list(target.iterdir()), [])

    def test_agent_retains_counts_after_cleanup_without_changing_helper_protocol(self):
        workspace = SessionWorkspace(self.root / "sessions")
        with patch("amplifai_phone.workspace.MIN_FREE_BYTES", 0):
            directory = workspace.__enter__()
        capture = self.capture(workspace=workspace)
        history = CallAccountingHistory(self.root / "sessions")
        async def collector(**_options):
            return capture
        sink = io.StringIO()
        commands = '{"action":"review","ids":[1]}\n{"action":"disconnect"}\n'
        self.assertEqual(run_connect(io.StringIO(commands), sink, collector,
                                     call_accounting_history=history), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["state", "capture", "review", "state"])
        self.assertEqual(history.load()[0]["callAccounting"], totals(2))
        self.assertFalse(directory.exists())
        inspected = io.StringIO()
        self.assertEqual(run_admin("inspect", inspected, self.root / "sessions"), 0)
        residue = json.loads(inspected.getvalue())
        self.assertNotIn("callAccountingHistory", residue)
        legacy = replace(capture, calls=replace(capture.calls, call_accounting=None),
                         review_workspace=None)
        async def legacy_collector(**_options):
            return legacy
        output = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(commands), output, legacy_collector,
                                     call_accounting_history=history), 0)
        self.assertNotIn('"kind":"call-accounting"', output.getvalue())
        self.assertEqual(len(history.load()), 1)

    def test_agent_without_selection_retains_unknown_selection_instead_of_zero(self):
        async def collector(**_options):
            return self.capture()
        history = CallAccountingHistory(self.root / "sessions")
        self.assertEqual(run_connect(io.StringIO('{"action":"disconnect"}\n'), io.StringIO(),
                                     collector, call_accounting_history=history), 0)
        self.assertEqual(history.load()[0]["callAccounting"], totals())

    def test_agent_storage_failure_uses_existing_bounded_failure_event(self):
        async def collector(**_options):
            return self.capture()
        history = CallAccountingHistory(self.root / "sessions")
        output = io.StringIO()
        with patch.object(history, "record", side_effect=DiagnosticsStorageError()):
            self.assertEqual(run_connect(io.StringIO('{"action":"disconnect"}\n'), output,
                                         collector, call_accounting_history=history), 0)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        failures = [event for event in events if event["kind"] == "diagnostic-storage"]
        self.assertEqual(failures, [{"kind": "diagnostic-storage", "state": "failed",
                                     "code": "diagnostics_unavailable"}])

    def test_failed_selection_write_retries_on_identical_review_and_clears_failure(self):
        async def collector(**_options):
            return self.capture()
        history = CallAccountingHistory(self.root / "sessions")
        original = history.record
        writes = []

        def transient_failure(*args, **kwargs):
            writes.append(args[1].copy())
            if len(writes) == 2:
                raise DiagnosticsStorageError()
            return original(*args, **kwargs)

        output = io.StringIO()
        commands = ('{"action":"review","ids":[1]}\n' * 2
                    + '{"action":"disconnect"}\n')
        with patch.object(history, "record", side_effect=transient_failure):
            self.assertEqual(run_connect(io.StringIO(commands), output, collector,
                                         call_accounting_history=history), 0)
        self.assertEqual(len(writes), 3)
        self.assertEqual(history.load()[0]["callAccounting"], totals(2))
        states = [event["state"] for event in map(json.loads, output.getvalue().splitlines())
                  if event["kind"] == "diagnostic-storage"]
        self.assertEqual(states, ["failed", "retained"])

    def _capture_with_source_failure(self):
        from test_source_diagnostic_flow import expected
        return replace(self.capture(), missing_sources=("messages",), backup_encrypted=True,
                       unavailable_reasons=(("messages", "selected_payload_length"),),
                       source_diagnostics=(expected(),))

    def test_source_history_success_cannot_mask_failed_call_history(self):
        capture = self._capture_with_source_failure()
        async def collector(**_options):
            return capture
        history = CallAccountingHistory(self.root / "sessions")
        source_history = DiagnosticHistory(self.root / "sessions")
        output = io.StringIO()
        with patch.object(history, "record", side_effect=DiagnosticsStorageError()):
            self.assertEqual(run_connect(io.StringIO('{"action":"disconnect"}\n'), output,
                                         collector, diagnostic_history=source_history,
                                         call_accounting_history=history), 0)
        self.assertFalse((history.root / "history.json").exists())
        self.assertTrue((source_history.root / "history.json").exists())
        states = [event["state"] for event in map(json.loads, output.getvalue().splitlines())
                  if event["kind"] == "diagnostic-storage"]
        self.assertTrue(states)
        self.assertTrue(all(state == "failed" for state in states))

    def test_call_history_success_cannot_mask_failed_source_history(self):
        capture = self._capture_with_source_failure()
        async def collector(**_options):
            return capture
        history = CallAccountingHistory(self.root / "sessions")
        source_history = DiagnosticHistory(self.root / "sessions")
        output = io.StringIO()
        with patch.object(source_history, "record", side_effect=DiagnosticsStorageError()):
            self.assertEqual(run_connect(io.StringIO('{"action":"review","ids":[1]}\n'
                                                     '{"action":"disconnect"}\n'), output,
                                         collector, diagnostic_history=source_history,
                                         call_accounting_history=history), 0)
        self.assertEqual(history.load()[0]["callAccounting"], totals(2))
        self.assertFalse((source_history.root / "history.json").exists())
        states = [event["state"] for event in map(json.loads, output.getvalue().splitlines())
                  if event["kind"] == "diagnostic-storage"]
        self.assertTrue(states)
        self.assertTrue(all(state == "failed" for state in states))


if __name__ == "__main__":
    unittest.main()
