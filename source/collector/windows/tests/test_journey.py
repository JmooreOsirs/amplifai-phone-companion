"""Presentation must preserve local review and never invent account-save progress."""
import unittest

from journey import journey
from native_run import Outcome
from test_native_run import HANDOFF, accept, bound, prepared, started
from test_session import BINDING, captured, inspected, paired, reviewed


class JourneyTests(unittest.TestCase):
    def test_terminal_recovery_has_one_action_until_cleanup_is_checked(self):
        native, _ = started()
        native.fail()
        state = inspected()
        state.needs_inspection = True
        self.assertEqual(journey(state, native).action, "inspect")
        state.begin("inspect")
        self.assertEqual(journey(state, native).action, "wait")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertEqual(journey(state, native).action, "return")
        state.residue = [{"marked": True}]
        self.assertEqual(journey(state, native).action, "clear")

    def test_historical_messages_diagnostic_does_not_name_current_failure(self):
        from test_windows_source_diagnostics import history
        native, _ = started()
        native.fail()
        state = inspected()
        state.collection_outcome = "failed"
        state.source_diagnostic_history = history()
        self.assertEqual(journey(state, native).heading, "Collection could not finish")
        state.saved_acknowledged = True
        self.assertEqual(journey(state, native).stage, "complete")

    def test_preparation_explains_app_owned_backup_without_manual_prerequisite(self):
        native, _ = prepared()
        text = journey(inspected(), native).detail
        self.assertIn("creates", text)
        self.assertIn("temporary backup", text)
        self.assertNotIn("finish any Apple Devices backup", text)

    def test_plain_launch_requires_account_but_existing_legacy_work_stays_recoverable(self):
        state = inspected()
        self.assertEqual(journey(state, None).stage, "account")
        state.approve(True)
        state.begin("connect")
        self.assertEqual(journey(state, None).stage, "collection")
        captured(state)
        self.assertEqual(journey(state, None).stage, "contacts")
        state = paired()
        before = (set(state.selected_ids), state.selection, state.handoff_id, state.destination_intent_id)
        result = journey(state, None)
        self.assertEqual(result.heading, "Connect your account")
        self.assertNotIn("Saving", result.heading)
        self.assertEqual(before, (state.selected_ids, state.selection, state.handoff_id, state.destination_intent_id))
        self.assertFalse(state.saved_acknowledged)

    def test_terminal_authority_cannot_be_hidden_by_retained_review(self):
        for action in ("cancel", "_refused", "handoff_expired"):
            with self.subTest(action=action):
                native, _ = bound()
                state = paired()
                getattr(native, action)()
                result = journey(state, native)
                self.assertEqual(result.stage, "attention")
                self.assertIn(native.message, result.detail)
                self.assertIn(result.action, {"return", "wait"})
                self.assertTrue(state.reviewed)
                self.assertFalse(state.saved_acknowledged)

    def test_paused_or_expired_review_never_implies_active_save(self):
        native, clock = bound()
        native.authority = "paused"
        self.assertEqual(journey(paired(), native).heading, "Confirmation paused")
        clock.advance(86_400)
        self.assertEqual(journey(paired(), native).heading, "Collection approval expired")

    def test_uncertain_continue_preserves_fixed_selection_and_recovery(self):
        native, _ = started()
        operation = native.continue_review((1,))
        native.complete(operation, Outcome("uncertain"))
        self.assertEqual(journey(reviewed(), native).stage, "attention")
        self.assertEqual(native.continued_selection, (1,))
        self.assertEqual(native.phase, "continue_uncertain")

    def test_uncertain_start_keeps_preparation_and_does_not_repeat_agreement(self):
        native, _ = prepared()
        native.check_backup_setting()
        native.checked_backup_setting(True)
        native.complete(native.start(), Outcome("uncertain"))
        self.assertEqual(journey(inspected(), native).stage, "prepare")
        self.assertFalse(native.started)

    def test_pending_binding_is_preparation_and_bound_waits_for_account(self):
        native, _ = started()
        operation = native.continue_review((1,))
        self.assertEqual(journey(reviewed(), native).heading, "Preparing your reviewed selection")
        accept(native, operation)
        native, _ = bound()
        self.assertEqual(journey(paired(), native).heading, "Continue in your account")

    def test_received_pending_and_matching_saved_ack_remain_distinct(self):
        for status, heading, stage in (
            ("received", "Waiting for account save", "save"),
            ("saved_pending_browser_receipt", "Waiting for save confirmation", "save"),
            ("saved", "Saved to your account", "complete"),
        ):
            with self.subTest(status=status):
                state = paired()
                state.poll()
                state.handle({"kind": "handoff", "state": status, "handoffId": BINDING})
                result = journey(state, None)
                self.assertEqual((result.heading, result.stage), (heading, stage))
                self.assertEqual(state.saved_acknowledged, status == "saved")
        native, _ = bound()
        native.record_saved(HANDOFF)
        self.assertEqual(journey(inspected(), native).stage, "complete")


if __name__ == "__main__":
    unittest.main()
