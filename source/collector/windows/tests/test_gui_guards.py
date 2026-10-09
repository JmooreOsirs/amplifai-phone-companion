"""Headless callback tests; they do not claim Tk rendering or packaged execution."""
from __future__ import annotations

import importlib.util
import hashlib
import queue
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session import Session


class DisabledWidget:
    """Only Tk's documented disabled insert/delete rule, not a renderer."""
    def __init__(self, values):
        self.values = list(values)
        self.state = "disabled"

    def configure(self, *, state):
        self.state = state

    def delete(self, *_args):
        if self.state == "normal":
            self.values.clear()

    def insert(self, _index, value):
        if self.state == "normal":
            self.values.append(value)

    def selection_set(self, _index):
        pass


def source_window():
    toolkit = types.ModuleType("tkinter")
    toolkit.END = "end"
    toolkit.messagebox = Mock()
    toolkit.ttk = types.ModuleType("tkinter.ttk")
    location = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("windows_callback_fixture", location)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"tkinter": toolkit}):
        spec.loader.exec_module(module)
    window = module.PhoneWindow.__new__(module.PhoneWindow)
    window.state = Session()
    window.state.begin("inspect")
    window.state.handle({"kind": "residue", "sessions": []})
    window.state.exited(0)
    window.state.approve(True)
    window.state.begin("connect")
    window.helper, window.window = Mock(), Mock()
    window.password = Mock()
    window.events = queue.Queue()
    window.refresh = Mock()
    window.contact_query = Mock()
    window.contact_page = Mock()
    window._rendering_contacts = False
    return window


class GuiGuardsTests(unittest.TestCase):
    def test_all_page_controls_expose_exact_progress_without_early_selection(self):
        window = source_window()
        for name in ("approve", "connect", "agreement", "decline_button", "contacts", "review_button",
                     "search_entry", "search_button", "previous_button", "next_button", "select_all_button",
                     "cancel_select_all_button", "clear_all_button", "pair_button", "password_button",
                     "inspect", "clear", "stop_button", "force_button", "progress", "copy_code_button",
                     "status", "transfer_status", "coverage_status", "pair_code", "destination_status"):
            setattr(window, name, Mock())
        window.checked = Mock()
        window.checked.get.return_value = False
        window.state.handle({"kind": "capture", "contacts": [
            {"id": index + 1, "name": f"Person {index + 1}",
             "phoneCount": 1, "phoneEnds": ["0123"]} for index in range(200)],
            "query": "", "cursor": 0, "nextCursor": 200, "totalContacts": 201,
            "availableCalls": 0, "availableMessages": 0, "missing": []})
        window.state.update_visible_selection([1])
        window.state.select_all_contacts()
        type(window).refresh(window)
        self.assertEqual(window.state.selected_ids, {1})
        self.assertIn("Selection remains 1", window.contact_page.set.call_args.args[0])
        window.select_all_button.configure.assert_called_with(state="disabled")
        window.cancel_select_all_button.configure.assert_called_with(state="normal")
        window.review_button.configure.assert_called_with(state="disabled")

    def test_account_destination_dialog_uses_helper_confirmed_values_and_does_not_save(self):
        window = source_window()
        window.state.handle({"kind": "capture", "contacts": [
            {"id": 1, "name": "Avery Lindqvist", "phoneCount": 1, "phoneEnds": ["0123"]}],
            "query": "", "cursor": 0, "nextCursor": None, "totalContacts": 1,
            "availableCalls": 0, "availableMessages": 0, "missing": []})
        window.state.update_visible_selection([1])
        command = window.state.review([1])
        window.state.handle({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
        window.state.handle({"kind": "review", "reviewId": command["reviewId"],
                             "selection_sha256": hashlib.sha256(b"1\n").hexdigest(),
                             "selected_contacts": 1, "matched_calls": 0,
                             "matched_messages": 0, "missing_sources": []})
        window.state.pair()
        handoff = "11111111-1111-4111-8111-111111111111"
        intent = "22222222-2222-4222-8222-222222222222"
        window.state.handle({"kind": "pairing", "pairCode": "1234567890", "port": 48751,
                             "expiresInSeconds": 300, "handoffId": handoff,
                             "payloadSha256": "a" * 64})
        dialog = window.pump.__globals__["messagebox"]
        dialog.askyesno.return_value = True
        window.events.put({"kind": "destination", "state": "pending", "handoffId": handoff,
                           "intentId": intent, "email": "confirmed@example.test", "course": "Course"})
        window.pump()
        self.assertIn("confirmed@example.test", dialog.askyesno.call_args.args[1])
        window.helper.write.assert_any_call({"action": "destination-decision", "handoffId": handoff,
                                             "intentId": intent, "approved": True})
        self.assertFalse(window.state.saved_acknowledged)
        window.copy_pair_code()
        window.window.clipboard_append.assert_called_once_with("1234567890")

    def test_window_ready_only_after_clean_completed_inspection(self):
        window = source_window()
        title = window.close.__globals__["window_title"]
        state = Session()
        state.begin("inspect")
        self.assertFalse(title(state).endswith(" · ready"))
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertTrue(title(state).endswith(" · ready"))
        state.begin("inspect")
        self.assertFalse(title(state).endswith(" · ready"))
        state.exited(1)
        self.assertTrue(title(state).endswith(" · inspection failed"))
        state = Session()
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": [{"name": "session-" + "a" * 32, "bytes": 0}]})
        state.exited(0)
        self.assertTrue(title(state).endswith(" · temporary data remains"))

    def test_private_write_failure_requests_cooperative_stop_not_termination(self):
        window = source_window()
        window.helper.write.side_effect = OSError("private helper detail")
        window.send({"action": "revoke"})
        window.helper.close_input.assert_called_once()
        window.helper.force_stop.assert_not_called()
        self.assertTrue(window.state.needs_inspection)
        self.assertIn("unconfirmed", window.state.status)
        self.assertNotIn("private helper detail", window.state.status)

    def test_invalid_model_packet_requests_cooperative_stop_not_termination(self):
        window = source_window()
        window.events.put({"kind": "unknown", "private": "must-not-be-displayed"})
        window.pump()
        window.helper.close_input.assert_called_once()
        window.helper.force_stop.assert_not_called()
        self.assertNotIn("must-not-be-displayed", window.state.status)
        window.window.after.assert_called_once()

    def test_private_password_write_failure_clears_widget_and_requests_stop(self):
        window = source_window()
        window.password = Mock()
        window.password.get.return_value = "synthetic-only"
        window.state.handle({"kind": "state", "state": "password_required"})
        window.helper.stdin.write.side_effect = OSError("private pipe detail")
        window.send_password()
        window.password.delete.assert_called_once()
        window.helper.close_input.assert_called_once()
        window.helper.force_stop.assert_not_called()
        self.assertNotIn("synthetic-only", repr(vars(window.state)))

    def test_manual_force_stop_requires_warning_and_the_same_pending_helper(self):
        window = source_window()
        window.state.request_stop()
        dialog = window.force.__globals__["messagebox"]
        dialog.askyesno.return_value = True
        window.force()
        dialog.askyesno.assert_called_once()
        self.assertIn("bypasses graceful cleanup", dialog.askyesno.call_args.args[1])
        window.helper.force_stop.assert_called_once()
        self.assertIn("requested", window.state.status)
        self.assertIn("unconfirmed", window.state.status)

    def test_force_confirmation_never_targets_a_replacement_helper(self):
        window = source_window()
        window.state.request_stop()
        dialog = window.force.__globals__["messagebox"]
        def replacement(*_args):
            window.helper.process = Mock()
            return True
        dialog.askyesno.side_effect = replacement
        window.force()
        window.helper.force_stop.assert_not_called()

    def test_native_storage_error_closes_input_without_erasing_specific_recovery_state(self):
        window = source_window()
        window.events.put({"kind": "error", "code": "workspace_marker"})
        window.pump()
        window.helper.close_input.assert_called_once()
        window.helper.force_stop.assert_not_called()
        self.assertIn("empty or marker-only", window.state.status)
        self.assertIn("No phone data was written", window.state.status)

    def test_stale_review_packet_does_not_render_old_output_counts(self):
        window = source_window()
        window.state.handle({"kind": "capture", "contacts": [
            {"id": 1, "name": "Avery Lindqvist", "phoneCount": 1, "phoneEnds": ["0123"]}],
            "query": "", "cursor": 0, "nextCursor": None, "totalContacts": 1,
            "availableCalls": 2, "availableMessages": 3, "missing": []})
        window.state.update_visible_selection([1])
        command = window.state.review([1])
        window.state.invalidate_selection()
        window.events.put({"kind": "review", "reviewId": command["reviewId"],
                           "selection_sha256": hashlib.sha256(b"1\n").hexdigest(),
                           "selected_contacts": 1, "matched_calls": 2,
                           "matched_messages": 3, "missing_sources": []})
        window.pump()
        self.assertFalse(window.state.reviewed)
        self.assertNotIn("matching calls", window.state.status)

    def test_capture_replaces_disabled_contact_rows_before_selection_is_enabled(self):
        window = source_window()
        window.contacts = DisabledWidget(["previous local review"])
        window.events.put({"kind": "capture", "contacts": [
            {"id": 1, "name": "Avery Lindqvist", "phoneCount": 1, "phoneEnds": ["0123"]}],
            "query": "", "cursor": 0, "nextCursor": None, "totalContacts": 1,
            "availableCalls": 2, "availableMessages": 3, "missing": []})
        window.pump()
        self.assertEqual(window.contacts.values, ["Avery Lindqvist · phone ending 0123"])

    def test_failure_clears_an_already_disabled_password_widget(self):
        window = source_window()
        window.password = DisabledWidget(["synthetic-only"])
        window.helper.write.side_effect = OSError("private pipe detail")
        window.send({"action": "revoke"})
        self.assertEqual(window.password.values, [])
        self.assertEqual(window.password.state, "disabled")


if __name__ == "__main__":
    unittest.main()
