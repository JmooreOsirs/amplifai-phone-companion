from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from amplifai_phone import agent
from amplifai_phone.bridge import selected_metadata
from amplifai_phone.ios_backup import (
    DATABASES, IPhoneCapture, _parse_entries, capture_source_diagnostics,
)
from amplifai_phone.metadata import (
    RETAINED_HISTORY_START, Contact, ContactsCapacityLimit, SelectedPayloadIntegrityError, SourceResult,
    UnsupportedSchema,
)
from amplifai_phone.paged_transfer import PagedTransfer
from amplifai_phone.record_store import RecordStore
from amplifai_phone.source_diagnostics import DiagnosticHistory, DiagnosticsStorageError
from amplifai_phone.workspace import SessionWorkspace


def sample(result: str = "length_mismatch") -> dict[str, object]:
    return {
        "source": "messages", "component": "database", "result": result,
        "manifest_bytes": 8192, "raw_after_bytes": 4112,
        "copied_plaintext_bytes": 4096, "encrypted": True,
        "entry_key_present": True, "producer_digest_present": True,
        "producer_digest_matches": True, "plaintext_copy_checksum_matches": True,
        "sidecars_absent": False, "size_difference_validation": "sidecar_present",
        "size_difference_validation_scope": "encrypted_messages_database_v1",
    }


def expected(reason: str = "selected_payload_length") -> dict[str, object]:
    return {
        "source": "messages", "component": "database", "reason": reason,
        "result": "length_mismatch" if reason == "selected_payload_length" else "unavailable",
        "declaredBytes": 8192, "rawBytes": 4112, "copiedBytes": 4096,
        "encrypted": True, "entryKeyPresent": True,
        "validationCode": "sidecar_present",
        "validationScope": "encrypted_messages_database_v1",
        "producerDigest": "matched", "copyDigest": "matched", "sidecars": "present",
    }


class SourceDiagnosticFlowTest(unittest.TestCase):
    def parsed(self, *, reader_failure: bool = False) -> IPhoneCapture:
        def extract(_backup: object, path: str, *_args: object, **kwargs: object) -> bool:
            if path == DATABASES["messages"]:
                value = sample()
                if reader_failure:
                    value.update(result="match", manifest_bytes=4096,
                                 size_difference_validation=None,
                                 size_difference_validation_scope=None,
                                 producer_digest_present=None, producer_digest_matches=None,
                                 plaintext_copy_checksum_matches=None, sidecars_absent=None)
                kwargs["payload_diagnostic_callback"](value)
                if not reader_failure:
                    raise SelectedPayloadIntegrityError("private database path", code="selected_payload_length")
            return True

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("amplifai_phone.ios_backup._extract_database", side_effect=extract),
            patch("amplifai_phone.ios_backup.read_contacts", return_value=SourceResult(1, (Contact(1, "Fictional", (), ()),), 0)),
            patch("amplifai_phone.ios_backup.read_calls", return_value=SourceResult(0, (), 0)),
            patch("amplifai_phone.ios_backup.read_messages", side_effect=UnsupportedSchema("private message text") if reader_failure else None),
        ):
            return _parse_entries(SimpleNamespace(is_encrypted=True), Path(temporary),
                                  datetime.now(UTC), RETAINED_HISTORY_START,
                                  payload_diagnostic_callback=lambda _event: None)

    def test_partial_success_keeps_message_verification_facts(self) -> None:
        capture = self.parsed()
        self.assertEqual(capture.missing_sources, ("messages",))
        self.assertEqual(list(getattr(capture, "source_diagnostics", ())), [expected()])
        self.assertNotIn("private", repr(capture))

    def test_reader_failure_preserves_copy_facts_and_valid_zero_calls(self) -> None:
        capture = self.parsed(reader_failure=True)
        self.assertEqual(capture.missing_sources, ("messages",))
        self.assertEqual(capture.calls.rows_seen, 0)
        facts = {**expected("unsupported_schema"), "declaredBytes": 4096,
                 "validationCode": None, "validationScope": None,
                 "producerDigest": "unknown", "copyDigest": "unknown", "sidecars": "unknown"}
        self.assertEqual(list(getattr(capture, "source_diagnostics", ())), [facts])

    def test_current_failure_survives_helper_exit_and_inspect_without_entering_next_capture(self) -> None:
        capture = self.parsed()

        async def collector(**kwargs: object) -> IPhoneCapture:
            kwargs["payload_diagnostic_callback"]({**sample(), "path": "/private/phone.db",
                                                   "messageBody": "private content"})
            return capture

        with tempfile.TemporaryDirectory() as temporary:
            sessions = Path(temporary).resolve() / "sessions"
            output = io.StringIO()
            self.assertEqual(0, agent.run_connect(io.StringIO('{"action":"disconnect"}\n'),
                             output, collector, diagnostic_history=DiagnosticHistory(sessions)))
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            review = next(item for item in events if item["kind"] == "capture")
            self.assertEqual([expected()], review["sourceDiagnostics"])
            self.assertTrue(any(item["kind"] == "diagnostic-storage" and
                                item["state"] == "retained" for item in events))
            relaunched = io.StringIO()
            self.assertEqual(0, agent.run_admin("inspect", relaunched, sessions,
                             diagnostic_history=DiagnosticHistory(sessions)))
            history = json.loads(relaunched.getvalue())["sourceDiagnosticHistory"]
            self.assertEqual([expected()], history[0]["sourceDiagnostics"])
            persisted = "".join(path.read_text() for path in sessions.parent.joinpath("diagnostics").iterdir())
            for private in ("phone.db", "private content", "messageBody", "path"):
                self.assertNotIn(private, persisted + output.getvalue() + relaunched.getvalue())

            async def readable(**_kwargs: object) -> IPhoneCapture:
                return replace(capture, missing_sources=(), unavailable_reasons=(), source_diagnostics=())

            next_output = io.StringIO()
            self.assertEqual(0, agent.run_connect(io.StringIO('{"action":"disconnect"}\n'),
                             next_output, readable, diagnostic_history=DiagnosticHistory(sessions)))
            next_review = next(json.loads(line) for line in next_output.getvalue().splitlines()
                               if json.loads(line)["kind"] == "capture")
            self.assertNotIn("sourceDiagnostics", next_review)
            self.assertEqual(history, DiagnosticHistory(sessions).load())

    def test_history_storage_failure_preserves_current_review_and_hides_raw_error(self) -> None:
        capture = self.parsed()

        async def collector(**_kwargs: object) -> IPhoneCapture:
            return capture

        with tempfile.TemporaryDirectory() as temporary:
            history = DiagnosticHistory(Path(temporary).resolve() / "sessions")
            with patch.object(history, "record", side_effect=DiagnosticsStorageError()):
                output = io.StringIO()
                self.assertEqual(0, agent.run_connect(io.StringIO('{"action":"disconnect"}\n'),
                                 output, collector, diagnostic_history=history))
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertIn({"kind": "diagnostic-storage", "state": "failed",
                           "code": "diagnostics_unavailable"}, events)
            self.assertEqual([expected()], next(item for item in events if item["kind"] == "capture")["sourceDiagnostics"])
            self.assertNotIn(temporary, output.getvalue())

    def test_cli_injects_durable_history_in_both_real_entry_points(self) -> None:
        for mode, target in (("connect", "run_connect"), ("inspect", "run_admin")):
            with self.subTest(mode=mode), patch.object(agent.sys, "argv", ["helper", mode]), \
                 patch.object(agent, "DiagnosticHistory") as factory, \
                 patch.object(agent, target, return_value=0) as entry, \
                 patch.object(agent.signal, "signal"):
                self.assertEqual(0, agent.main())
                factory.assert_called_once_with()
                self.assertIs(factory.return_value, entry.call_args.kwargs["diagnostic_history"])

    def test_current_capture_rejects_history_private_keys_wrong_reason_and_readable_zero(self) -> None:
        capture = self.parsed()
        for changed in (
            replace(capture, missing_sources=(), unavailable_reasons=()),
            replace(capture, unavailable_reasons=(("messages", "source_invalid"),)),
            replace(capture, source_diagnostics=({**expected(), "source": "calls"},)),
            replace(capture, source_diagnostics=({**expected(), "privatePath": "/private/phone.db"},)),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                capture_source_diagnostics(changed)

    def test_required_contacts_capacity_keeps_local_failure_and_fails_closed(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as temporary, \
             patch("amplifai_phone.ios_backup._extract_database", return_value=True), \
             patch("amplifai_phone.ios_backup.read_contacts", side_effect=ContactsCapacityLimit("private row")):
            with self.assertRaises(ContactsCapacityLimit):
                _parse_entries(SimpleNamespace(is_encrypted=True), Path(temporary),
                               datetime.now(UTC), RETAINED_HISTORY_START,
                               payload_diagnostic_callback=events.append)
        self.assertEqual(1, len(events))
        self.assertEqual(("contacts", "source_capacity", "unavailable"),
                         tuple(events[0][key] for key in ("source", "reason", "result")))
        self.assertNotIn("private row", json.dumps(events))

    def test_pre_copy_sidecar_failure_keeps_actual_component_and_unknown_copy_counts(self) -> None:
        for malformed, reason in ((False, "selected_payload_missing"), (True, "selected_payload_size")):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw = root / "raw"
                raw.write_bytes(b"data")
                extracted = root / "extracted"
                extracted.mkdir()

                def entry(_domain: str, path: str) -> object:
                    if path in (DATABASES["contacts"], DATABASES["messages"]):
                        return SimpleNamespace(size=4, file_id="a" * 40, is_file=lambda: True,
                                               real_path=raw, encryption_key=b"")
                    if path == DATABASES["messages"] + "-wal":
                        return SimpleNamespace(size=True if malformed else 4, file_id="b" * 40,
                                               is_file=lambda: True, real_path=root / "private-wal",
                                               encryption_key=b"")
                    raise KeyError(path)

                backup = SimpleNamespace(is_encrypted=False, get_entry_by_domain_and_path=entry)
                with patch("amplifai_phone.backup_files.MIN_FREE_BYTES", 0), \
                     patch("amplifai_phone.ios_backup.read_contacts", return_value=SourceResult(1, (Contact(1, "Fictional", (), ()),), 0)):
                    capture = _parse_entries(backup, extracted, datetime.now(UTC), RETAINED_HISTORY_START)
                diagnostic = next(record for record in capture.source_diagnostics if record["source"] == "messages")
                self.assertEqual(("wal", reason, None, None),
                                 tuple(diagnostic[key] for key in ("component", "reason", "rawBytes", "copiedBytes")))
                self.assertNotIn("private-wal", json.dumps(capture.source_diagnostics))

    def test_error_facts_are_digest_bound_in_small_and_paged_handoffs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch("amplifai_phone.workspace.MIN_FREE_BYTES", 0):
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            try:
                store.add("contacts", Contact(1, "Fictional", ("+15551234567",), ()))
                capture = IPhoneCapture(SourceResult(1, store.collection("contacts"), 0),
                                        SourceResult(0, store.collection("calls"), 0),
                                        SourceResult(0, store.collection("messages"), 0),
                                        "2026-10-15T00:00:00Z", "2026-01-01T00:00:00Z", ("messages",),
                                        workspace, store, True, (("messages", "selected_payload_length"),))
                value = SimpleNamespace(**{**capture.__dict__, "source_diagnostics": (expected(),)})
                small = selected_metadata(value, {1})
                self.assertEqual(json.loads(small).get("sourceDiagnostics"), [expected()])
                first = PagedTransfer(value, {1}, directory / "first.sqlite3")
                changed_record = {**expected(), "rawBytes": 4128}
                changed = SimpleNamespace(**{**capture.__dict__, "source_diagnostics": (changed_record,)})
                second = PagedTransfer(changed, {1}, directory / "second.sqlite3")
                try:
                    self.assertEqual(first.manifest.get("sourceDiagnostics"), [expected()])
                    self.assertNotEqual(first.sha256, second.sha256)
                    self.assertNotEqual(hashlib.sha256(small).digest(),
                                        hashlib.sha256(selected_metadata(changed, {1})).digest())
                finally:
                    first.close()
                    second.close()
            finally:
                store.close()
                workspace.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
