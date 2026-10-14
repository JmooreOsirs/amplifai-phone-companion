"""Synthetic Windows model/pipe/callback cases; no Tk render, Apple device, or account proof."""
import hashlib
import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helper_process import HelperProcess
from session import BACKUP_CHECK_MODE, ProtocolError, Session, contact_source_details
from test_session import inspected, reviewed, paired
from test_gui_guards import source_window


def capture(state, **details):
    state.handle({"kind": "capture", "contacts": [{"id": 7, "name": "Ada Lovelace",
                  "phoneCount": 1, "phoneEnds": ["0123"], **details}], "query": "", "cursor": 0,
                  "nextCursor": None, "totalContacts": 1, "availableCalls": 0,
                  "availableMessages": 0, "missing": ["calls"], "backupEncrypted": False,
                  "unavailableReasons": {"calls": "absent"}})


class VideoParityTests(unittest.TestCase):
    def test_on_off_only_after_exact_result_and_clean_terminal_exit(self):
        for result in (True, False):
            with self.subTest(result=result):
                state = inspected()
                state.approve(True)
                state.begin(BACKUP_CHECK_MODE)
                self.assertIsNone(state.approval)
                self.assertIsNone(state.checked_backup_setting)
                state.handle({"kind": BACKUP_CHECK_MODE, "backupEncrypted": result})
                self.assertIsNone(state.checked_backup_setting)
                self.assertIn("checking", state.backup_check_status)
                state.exited(0)
                self.assertIs(state.checked_backup_setting, result)
                self.assertIsNone(state.backup_encrypted)  # setting is not captured coverage
                self.assertTrue(state.needs_inspection)
                self.assertFalse(state.can_connect)
                state.begin("inspect")
                state.handle({"kind": "residue", "sessions": []})
                state.exited(0)
                self.assertTrue(state.can_check_backup_setting)
                self.assertFalse(state.can_connect)  # never creates fresh collection consent
                self.assertIs(state.checked_backup_setting, result)

    def test_unknown_for_no_result_error_nonzero_stop_or_cleanup_failure(self):
        for failure in ("empty", "nonzero", "stopped", "cleanup"):
            with self.subTest(failure=failure):
                state = inspected(); state.begin(BACKUP_CHECK_MODE)
                if failure != "empty":
                    state.handle({"kind": BACKUP_CHECK_MODE, "backupEncrypted": True})
                if failure == "stopped": state.request_stop()
                if failure == "cleanup": state.handle({"kind": "error", "code": "workspace_marker", "cleanupRequired": True})
                state.exited(1 if failure == "nonzero" else 0)
                self.assertIsNone(state.checked_backup_setting)
                self.assertIn("Unknown", state.backup_check_status)
                self.assertTrue(state.needs_inspection)

    def test_read_only_check_cannot_replace_capture_review_pairing_or_cleanup(self):
        for state in (reviewed(), paired(), Session()):
            before = (list(state.contacts), set(state.selected_ids), state.selection, state.handoff_id, state.phase)
            with self.assertRaises(ProtocolError): state.begin(BACKUP_CHECK_MODE)
            self.assertEqual(before, (state.contacts, state.selected_ids, state.selection, state.handoff_id, state.phase))

    def test_result_rejects_pseudo_booleans_duplicates_wrong_mode_and_stale_exit(self):
        for value in (None, 0, 1, "false", {}, []):
            state = inspected(); state.begin(BACKUP_CHECK_MODE)
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                state.handle({"kind": BACKUP_CHECK_MODE, "backupEncrypted": value})
            state.exited(1)
            self.assertIsNone(state.checked_backup_setting)
        state = inspected(); state.begin(BACKUP_CHECK_MODE)
        packet = {"kind": BACKUP_CHECK_MODE, "backupEncrypted": True}
        with self.assertRaises(ProtocolError): state.handle({**packet, "device": "untrusted"})
        state.handle(packet)
        with self.assertRaises(ProtocolError): state.handle(packet)
        state.cooperative_failure(); state.exited(0)
        self.assertIsNone(state.checked_backup_setting)
        with self.assertRaises(ProtocolError): state.handle(packet)
        state.begin("inspect")
        with self.assertRaises(ProtocolError): state.handle(packet)

    def test_actual_private_pipe_accepts_only_read_only_mode_and_terminal_event(self):
        events = queue.Queue()
        helper = HelperProcess([sys.executable, str(Path(__file__).with_name("fake_helper.py"))], events.put)
        state = inspected(); state.begin(BACKUP_CHECK_MODE)
        helper.start(BACKUP_CHECK_MODE)
        try:
            while True:
                event = events.get(timeout=3)
                if event["kind"] == "process-exit":
                    state.exited(event["code"]); break
                state.handle(event)
        finally:
            helper.thread.join(timeout=3)
        self.assertFalse(state.checked_backup_setting)
        self.assertIsNone(state.backup_encrypted)
        self.assertFalse(state._capture_received)
        self.assertTrue(state.needs_inspection)
        with self.assertRaises(ValueError): helper.start("enable-encryption")

    def test_pump_runs_inspection_after_check_and_keeps_result_separate(self):
        window = source_window(); window.state = inspected()
        window.start(BACKUP_CHECK_MODE)
        window.helper.start.assert_called_once_with(BACKUP_CHECK_MODE)
        for event in ({"kind": BACKUP_CHECK_MODE, "backupEncrypted": True},
                      {"kind": "process-exit", "code": 0},
                      {"kind": "residue", "sessions": []}, {"kind": "process-exit", "code": 0}):
            window.events.put(event)
        window.pump()
        self.assertEqual([call.args[0] for call in window.helper.start.call_args_list], [BACKUP_CHECK_MODE, "inspect"])
        self.assertTrue(window.state.checked_backup_setting)
        self.assertTrue(window.state.inspected)
        self.assertFalse(window.state.can_connect)
        self.assertFalse(window.state._capture_received)
        window.helper.write.assert_not_called()

    def test_source_details_preserve_combined_names_unknown_hints_and_selection(self):
        for details, expected in (({}, "unavailable"), ({"nameFields": "first"}, "first field filled"),
                                  ({"nameFields": "last"}, "last field filled"),
                                  ({"nameFields": "first_and_last"}, "first and last fields filled"),
                                  ({"nameFields": "missing"}, "fields are empty"),
                                  ({"nameFields": "future-format"}, "unavailable")):
            state = inspected(); state.approve(True); state.begin("connect"); capture(state, **details)
            state.update_visible_selection([7])
            before = set(state.selected_ids)
            text = contact_source_details(state.contacts[0])
            self.assertIn(expected, text)
            self.assertEqual(state.selected_ids, before)
            self.assertFalse(state.reviewed)
            self.assertEqual(state.contacts[0]["name"], "Ada Lovelace")
        self.assertIn("hint, not verified", contact_source_details({"organizationField": True}))
        self.assertIn("field is empty", contact_source_details({"organizationField": False}))
        self.assertIn("unavailable", contact_source_details({"organizationField": None}))

    def test_malformed_source_hints_do_not_replace_existing_page(self):
        for details in ({"nameFields": {}}, {"nameFields": 1}, {"organizationField": 1}, {"organizationField": "true"}):
            state = inspected(); state.approve(True); state.begin("connect")
            with self.subTest(details=details), self.assertRaises(ProtocolError): capture(state, **details)
            self.assertEqual(state.contacts, [])
            self.assertFalse(state._capture_received)

    def test_source_detail_window_does_not_select_review_pair_or_save(self):
        window = source_window(); capture(window.state, nameFields="first", organizationField=True)
        window.state.update_visible_selection([7])
        window.contacts = Mock(); window.contacts.index.return_value = 0
        window.label = Mock(); window.readable_text = Mock(return_value=Mock())
        toolkit = type(window).show_source_details.__globals__["tk"]
        widgets = type(window).show_source_details.__globals__["ttk"]
        before = (set(window.state.selected_ids), window.state.reviewed, window.state.selection)
        with patch.object(toolkit, "Toplevel", create=True), patch.object(widgets, "Frame", create=True), patch.object(widgets, "Button", create=True):
            window.show_source_details()
        detail = window.readable_text.return_value.insert.call_args.args[1]
        self.assertIn("first field", detail)
        self.assertIn("not verified business", detail)
        self.assertEqual(before, (window.state.selected_ids, window.state.reviewed, window.state.selection))
        window.helper.write.assert_not_called()
        window.close_source_details()
        self.assertIsNone(window.source_details_window)

    def test_guide_navigation_never_starts_helper_or_changes_phone(self):
        window = source_window()
        window.guide_step = 0
        window.guide_heading, window.guide_text = Mock(), Mock()
        window.guide_back, window.guide_next = Mock(), Mock()
        for direction in (-1, 1, 1, 1, 1): window.move_guide(direction)
        self.assertEqual(window.guide_step, 3)
        self.assertIn("4 of 4", window.guide_heading.set.call_args.args[0])
        window.helper.start.assert_not_called()
        window.helper.write.assert_not_called()

    def test_status_uses_observed_stage_without_simulating_percentage_or_completion(self):
        state = inspected(); state.approve(True); state.begin("connect")
        self.assertIn("first received byte", state.activity_stage)
        self.assertIn("No percentage", state.progress_caption)
        state.handle({"kind": "transfer", "stage": "backup", "receivedBytes": 2048,
                      "retainedBytes": 1024, "discardedBytes": 1024, "filesReceived": 2,
                      "elapsedSeconds": 2.0, "bytesPerSecond": 1024.0})
        self.assertIn("2 of 4", state.activity_stage)
        self.assertIn("2s", state.activity_measurement)
        self.assertIn("1,024 bytes/s", state.activity_measurement)
        self.assertIsNone(state.progress)
        state.handle({"kind": "progress", "value": 37})
        self.assertIn("37%", state.progress_caption)
        state.handle({"kind": "transfer", "stage": "processing", "processedBytes": 128})
        self.assertIn("3 of 4", state.activity_stage)
        self.assertNotIn("37%", state.progress_caption)
        self.assertEqual(state.activity_measurement, "")
        capture(state)
        self.assertIn("4 of 4", state.activity_stage)
        self.assertIn("Calls unavailable", state.coverage_status)
        self.assertFalse(state.saved_acknowledged)

    def test_review_summary_does_not_turn_missing_calls_into_measured_zero(self):
        window = source_window(); capture(window.state)
        window.state.update_visible_selection([7])
        command = window.state.review([7])
        window.events.put({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
        window.events.put({"kind": "review", "reviewId": command["reviewId"], "selected_contacts": 1,
                           "selection_sha256": hashlib.sha256(b"7\n").hexdigest(), "matched_calls": 0,
                           "matched_messages": 0, "missing_sources": ["calls"]})
        window.pump()
        self.assertIn("Calls unavailable", window.state.status)
        self.assertNotIn("0 matching calls", window.state.status)
        self.assertIn("0 matching messages", window.state.status)


if __name__ == "__main__": unittest.main()
