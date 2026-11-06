"""Real fictional decrypt events through the Windows model and source callbacks.

Headless callback checks do not establish Tk layout or Windows/frozen execution.
"""
from __future__ import annotations

from datetime import UTC, datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from amplifai_phone.agent import run_connect
from amplifai_phone.ios_backup import parse_selected_backup
from session import ProtocolError
from native_run import Outcome
from native_setup_fixture import review_state
from synthetic_encrypted_backup_fixture import SYNTHETIC_PASSWORD, fixture
from test_gui_guards import source_window
from test_native_app import native_window
from test_native_run import started


def encrypted_events(variant="valid", entered=SYNTHETIC_PASSWORD):
    with tempfile.TemporaryDirectory(prefix="amplifai-fictional-ui-") as temporary:
        root = Path(temporary) / "backup"
        fixture(root, SYNTHETIC_PASSWORD, variant)

        async def collector(**kwargs):
            return parse_selected_backup(
                root, kwargs["password_provider"](),
                now=datetime(2026, 11, 6, tzinfo=UTC),
                transfer_callback=kwargs["transfer_callback"],
                payload_diagnostic_callback=kwargs["payload_diagnostic_callback"],
            )

        source = io.StringIO(json.dumps({"action": "password", "value": entered}) + "\n"
                             + json.dumps({"action": "disconnect"}) + "\n")
        sink = io.StringIO()
        result = run_connect(source, sink, collector)
        if entered in sink.getvalue():
            raise AssertionError("Fictional password echoed by helper")
        return result, [json.loads(line) for line in sink.getvalue().splitlines()]


def accept_capture_events(state, events):
    for event in events:
        if event == {"kind": "state", "state": "disconnected"}:
            break
        state.handle(event)


def contact_view(state, *, compact):
    view, _ = native_window()
    view.native, _ = started()
    view.state = state
    view.compact_layout = compact
    view._workspace_capture = True
    for name in ("workspace_coverage", "workspace_status", "review_button", "pair_button"):
        setattr(view, name, Mock())
    return view


class DecryptionReviewUiTests(unittest.TestCase):
    def test_crypto_optional_source_is_explicit_before_continue_in_both_layouts(self):
        variants = [(f"{source}_{failure}", [source])
                    for source in ("calls", "messages")
                    for failure in ("key", "wal_key", "shm_key")]
        variants.append(("both_optional_keys", ["calls", "messages"]))
        for variant, missing in variants:
            with self.subTest(variant=variant):
                result, events = encrypted_events(variant)
                self.assertEqual(result, 0)
                state = source_window().state
                accept_capture_events(state, events)
                state.update_visible_selection([1])
                self.assertEqual(state.missing, missing)
                self.assertTrue(state.can_review)
                self.assertFalse(state.saved_acknowledged)
                for compact in (False, True):
                    view = contact_view(state, compact=compact)
                    type(view).refresh_workspace(view)
                    visible = view.workspace_status.set.call_args.args[0]
                    expected_compact = " and ".join(source.title() if index == 0 else source
                                                    for index, source in enumerate(missing)) + " unavailable"
                    if compact:
                        self.assertEqual(visible, expected_compact)
                    for source in missing:
                        if not compact:
                            self.assertIn(source.title() + " unavailable", visible)
                        self.assertNotIn(source.title() + ": 0 readable", visible)
                        self.assertIn(source.title() + " unavailable: encrypted source could not be validated",
                                      state.coverage_status)
                    if not compact:
                        self.assertIn("Continue", visible)
                self.assertNotIn(SYNTHETIC_PASSWORD, repr(vars(state)))

    def test_required_crypto_failure_has_no_review_or_automatic_retry(self):
        for variant, entered, code in (
            ("manifest_key", SYNTHETIC_PASSWORD, "backup_control_invalid"),
            ("contacts_key", SYNTHETIC_PASSWORD, "selected_payload_crypto"),
            ("contacts_wal_key", SYNTHETIC_PASSWORD, "selected_payload_crypto"),
            ("contacts_shm_key", SYNTHETIC_PASSWORD, "selected_payload_crypto"),
            ("valid", "Wrong fictional password", "backup_password"),
        ):
            with self.subTest(variant=variant, code=code):
                result, events = encrypted_events(variant, entered)
                self.assertEqual(result, 1)
                state = source_window().state
                accept_capture_events(state, events)
                self.assertEqual(state.phase, "error")
                self.assertEqual(state.collection_failure.code, code)
                self.assertEqual(state.collection_failure.stage, "processing")
                self.assertFalse(state._capture_received)
                self.assertFalse(state.can_review)
                self.assertFalse(state.saved_acknowledged)
                state.exited(result)
                state.approve(True)
                self.assertFalse(state.can_connect)
                with self.assertRaises(ProtocolError):
                    state.begin("connect")

    def test_review_cannot_relabel_crypto_unavailable_sources_as_readable_zero(self):
        _result, events = encrypted_events("both_optional_keys")
        state = source_window().state
        accept_capture_events(state, events)
        state.update_visible_selection([1])
        command = state.review([1])
        state.handle({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
        packet = {"kind": "review", "reviewId": command["reviewId"],
                  "selection_sha256": state._pending_review_sha,
                  "selected_contacts": 1, "matched_calls": 0, "matched_messages": 0,
                  "missing_sources": []}
        with self.assertRaises(ProtocolError):
            state.handle(packet)
        self.assertFalse(state.reviewed)
        view = contact_view(state, compact=True)
        view.events.put({**packet, "missing_sources": ["calls", "messages"]})
        view.pump()
        self.assertTrue(state.reviewed)
        self.assertIn("Calls unavailable", state.status)
        self.assertIn("Messages unavailable", state.status)
        self.assertNotIn("0 matching", state.status)
        type(view).refresh_workspace(view)
        visible = view.workspace_status.set.call_args.args[0]
        self.assertEqual(visible, "Calls and messages unavailable")
        self.assertFalse(state.saved_acknowledged)

    def test_large_review_keeps_only_the_bounded_warning_in_compact_continue_copy(self):
        for missing, expected in ((["calls"], "Calls unavailable"),
                                  (["messages"], "Messages unavailable"),
                                  (["calls", "messages"], "Calls and messages unavailable")):
            with self.subTest(missing=missing):
                state = review_state(missing=missing, unavailable_reason="selected_payload_crypto")
                self.assertEqual(state.total_contacts, 1_205)
                self.assertEqual(len(state.selected_ids), 1_205)
                self.assertTrue(state.reviewed)
                view = contact_view(state, compact=True)
                type(view).refresh_workspace(view)
                self.assertEqual(view.workspace_status.set.call_args.args[0], expected)
                view.pair_button.grid.assert_called_once()
                self.assertFalse(state.saved_acknowledged)

    def test_compact_warning_does_not_replace_guarded_confirmation_recovery(self):
        state = review_state(missing=["calls", "messages"], unavailable_reason="selected_payload_crypto")
        view = contact_view(state, compact=True)
        operation = view.native.continue_review(tuple(sorted(state.selected_ids)))
        view.native.complete(operation, Outcome("uncertain"))
        type(view).refresh_workspace(view)
        visible = view.workspace_status.set.call_args.args[0]
        self.assertIn("Calls unavailable", visible)
        self.assertIn("Messages unavailable", visible)
        self.assertIn("Confirmation unavailable", visible)
        self.assertIn("Details & recovery", visible)

    def test_clean_inspection_and_new_approval_retry_has_only_new_coverage(self):
        state = source_window().state
        result, events = encrypted_events("calls_key")
        self.assertEqual(result, 0)
        accept_capture_events(state, events)
        self.assertEqual(state.missing, ["calls"])
        state.request_stop()
        state.exited(0)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertFalse(state.can_connect)
        state.approve(True)
        state.begin("connect")
        self.assertFalse(state._capture_received)
        self.assertEqual(state.contacts, [])
        self.assertEqual(state.selected_ids, set())
        self.assertEqual(state.missing, [])
        self.assertEqual(state.unavailable_reasons, {})
        self.assertEqual(state.source_counts, (0, 0))
        self.assertEqual(state.source_diagnostics, [])
        self.assertIsNone(state.collection_failure)
        result, events = encrypted_events()
        self.assertEqual(result, 0)
        accept_capture_events(state, events)
        self.assertEqual(state.source_counts, (1, 1))
        self.assertEqual(state.missing, [])
        self.assertNotIn("unavailable", state.coverage_status)
        self.assertFalse(state.saved_acknowledged)

    def test_password_widget_is_cleared_after_successful_private_pipe_send(self):
        view = source_window()
        view.password.get.return_value = SYNTHETIC_PASSWORD
        view.helper.stdin = io.StringIO()
        view.state.handle({"kind": "state", "state": "password_required"})
        view.send_password()
        self.assertEqual(json.loads(view.helper.stdin.getvalue()),
                         {"action": "password", "value": SYNTHETIC_PASSWORD})
        view.password.delete.assert_called_once()
        view.password.configure.assert_called_with(state="disabled")
        self.assertNotIn(SYNTHETIC_PASSWORD, repr(vars(view.state)))


if __name__ == "__main__":
    unittest.main()
