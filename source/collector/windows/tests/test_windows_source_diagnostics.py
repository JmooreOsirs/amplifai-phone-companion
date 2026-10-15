"""Windows source diagnostics regression and IR-16 reference contract.

Authority: the existing app.py coverage label, source-details button and read-only
details window. Diagnostic summaries inherit ttk.Label, wraplength 820, left
justification and the existing 8px spacing. Disclosure inherits TButton and the
contact-details Toplevel/readable_text/Close/Escape pattern. Arial, palette,
scrolling and window minimums remain the existing primitives; long diagnostic
text reuses the contact list's ttk.Scrollbar. Current facts and
previous-run history have separate labels; no history enters capture or Save.
Headless tests establish behavior, not native Windows rendering.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session import ProtocolError, Session


def diagnostic(**changes):
    return {"source": "messages", "component": "database", "reason": "selected_payload_length",
            "result": "length_mismatch", "declaredBytes": 8192, "rawBytes": 4112, "copiedBytes": 4096,
            "encrypted": True, "entryKeyPresent": True, "validationCode": "checksum_mismatch",
            "validationScope": "encrypted_messages_database_v1", "producerDigest": "matched",
            "copyDigest": "unknown", "sidecars": "absent", **changes}


def connecting():
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    state.approve(True)
    state.begin("connect")
    return state


def capture(**changes):
    return {"kind": "capture", "contacts": [{"id": 1, "name": "Synthetic contact", "phoneCount": 0,
            "phoneEnds": []}], "query": "", "cursor": 0, "nextCursor": None, "totalContacts": 1,
            "availableCalls": 0, "availableMessages": 0, "missing": ["messages"],
            "unavailableReasons": {"messages": "selected_payload_length"}, **changes}


ATTEMPT = "11111111-1111-4111-8111-111111111111"


def history(facts=None):
    return [{"attemptId": ATTEMPT, "createdAt": 1_792_041_600,
             "sourceDiagnostics": [diagnostic() if facts is None else facts]}]


class SourceDiagnosticTests(unittest.TestCase):
    def test_real_diagnostic_event_survives_partial_capture_without_protocol_failure(self):
        state = connecting()
        facts = diagnostic()
        self.assertIsNone(state.handle({"kind": "selected-payload-diagnostic", **facts}))
        state.handle(capture(sourceDiagnostics=[facts]))
        self.assertEqual([facts], state.source_diagnostics)
        self.assertTrue(state.can_select)
        self.assertIn("Messages unavailable", state.coverage_status)
        self.assertFalse(state.saved_acknowledged)

    def test_partial_capture_keeps_failure_facts_when_optional_array_is_omitted(self):
        state = connecting()
        state.handle({"kind": "selected-payload-diagnostic", **diagnostic()})
        state.handle(capture())
        self.assertEqual([diagnostic()], state.source_diagnostics)
        self.assertIn("8,192 / 4,112 / 4,096", state.coverage_status)
        self.assertIn("checksum mismatch", state.diagnostic_details)
        self.assertIn("entry key present: yes", state.diagnostic_details)

    def test_capture_rejects_private_or_unrelated_diagnostics_before_loading_contacts(self):
        for facts in (diagnostic(privatePath="/private/phone/messages.db"),
                      diagnostic(source="calls"), diagnostic(reason="source_invalid"),
                      diagnostic(rawBytes=True), diagnostic(copyDigest="private raw hash")):
            with self.subTest(facts=facts):
                state = connecting()
                with self.assertRaises(ProtocolError):
                    state.handle(capture(sourceDiagnostics=[facts]))
                self.assertEqual([], state.contacts)
                self.assertEqual([], state.source_diagnostics)
                self.assertFalse(state._capture_received)

    def test_unrecognized_event_fields_never_enter_display_or_state(self):
        state = connecting()
        with self.assertRaises(ProtocolError):
            state.handle({"kind": "selected-payload-diagnostic", **diagnostic(), "messageBody": "private body"})
        self.assertEqual([], state.source_diagnostics)
        self.assertNotIn("private", state.diagnostic_details)

    def test_restored_previous_run_never_changes_a_current_readable_zero(self):
        state = Session()
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": [], "sourceDiagnosticHistory": history()})
        state.exited(0)
        self.assertIn("Previous run diagnostics", state.coverage_status)
        self.assertEqual([], state.source_diagnostics)
        state.approve(True)
        state.begin("connect")
        state.handle(capture(missing=[], unavailableReasons={}))
        self.assertIn("Messages: 0 readable phone entries", state.coverage_status)
        self.assertNotIn("Messages unavailable", state.coverage_status)
        self.assertEqual([], state.source_diagnostics)
        self.assertEqual(history(), state.source_diagnostic_history)
        state.update_visible_selection([1])
        command = state.review([1])
        self.assertNotIn("sourceDiagnostic", json.dumps(command))
        self.assertIn("Historical evidence only", state.diagnostic_details)

    def test_malformed_history_does_not_complete_inspection_or_leak_private_context(self):
        invalid = history()
        invalid[0]["sourceDiagnostics"][0]["privatePath"] = "/private/phone/messages.db"
        noncanonical = history()
        noncanonical[0]["attemptId"] = "{" + ATTEMPT + "}"
        for value in (invalid, noncanonical, [{"attemptId": ATTEMPT, "createdAt": 1, "diagnostics": [diagnostic()]}]):
            with self.subTest(value=value):
                state = Session()
                state.begin("inspect")
                with self.assertRaises(ProtocolError):
                    state.handle({"kind": "residue", "sessions": [], "sourceDiagnosticHistory": value})
                self.assertFalse(state.inspected)
                self.assertEqual([], state.source_diagnostic_history)
                self.assertNotIn("private", state.coverage_status)

    def test_fatal_facts_survive_exit_and_inspection_until_next_collection(self):
        state = connecting()
        facts = diagnostic(source="contacts", validationScope="encrypted_contacts_tables_v1")
        state.handle({"kind": "selected-payload-diagnostic", **facts})
        state.handle({"kind": "diagnostic-storage", "state": "retained", "attemptId": ATTEMPT})
        state.handle({"kind": "error", "code": "selected_payload_length"})
        state.exited(1)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": [], "sourceDiagnosticHistory": history(facts)})
        state.exited(0)
        self.assertEqual([facts], state.source_diagnostics)
        self.assertEqual([], state.previous_diagnostics)
        state.approve(True)
        state.begin("connect")
        self.assertEqual([], state.source_diagnostics)
        self.assertEqual(history(facts), state.previous_diagnostics)
        self.assertEqual("", state.diagnostic_storage_status)

    def test_storage_failure_is_visible_without_destroying_valid_partial_capture(self):
        state = connecting()
        state.handle({"kind": "selected-payload-diagnostic", **diagnostic()})
        state.handle({"kind": "diagnostic-storage", "state": "failed", "code": "diagnostics_unavailable"})
        state.handle(capture(sourceDiagnostics=[diagnostic()]))
        self.assertTrue(state.can_select)
        self.assertIn("could not be retained locally", state.coverage_status)
        self.assertEqual([diagnostic()], state.source_diagnostics)
        for bad in ({"state": "failed", "code": "private exception"},
                    {"state": "failed", "code": "diagnostics_unavailable", "path": "/private/path"},
                    {"state": "retained", "attemptId": "not-a-uuid"}):
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                state.handle({"kind": "diagnostic-storage", **bad})

    def test_storage_receipt_is_still_accepted_after_cooperative_stop(self):
        state = connecting()
        state.request_stop()
        state.handle({"kind": "diagnostic-storage", "state": "retained", "attemptId": ATTEMPT})
        self.assertIn("retained locally", state.diagnostic_storage_status)

    def test_queued_diagnostic_does_not_request_stop_and_disclosure_excludes_contact_rows(self):
        from test_gui_guards import source_window
        from unittest.mock import Mock
        window = source_window()
        window.render_contacts = Mock()
        facts = diagnostic()
        window.events.put({"kind": "selected-payload-diagnostic", **facts})
        window.events.put(capture(sourceDiagnostics=[facts]))
        window.pump()
        window.helper.close_input.assert_not_called()
        self.assertTrue(window.state.can_select)
        window.show_readonly_details = Mock()
        window.show_source_diagnostics()
        content = window.show_readonly_details.call_args.args[2]
        self.assertIn("Current collection source details", content)
        self.assertNotIn("Synthetic contact", content)
        self.assertTrue(window.show_readonly_details.call_args.kwargs["scrollable"])


if __name__ == "__main__":
    unittest.main()
