"""Headless native setup callbacks. Synthetic authority never reaches a network."""
import queue
import unittest
from unittest.mock import Mock, patch

from native_run import NativeRun, Outcome
from native_setup import DISCLOSURE
from test_gui_guards import source_window
from test_native_run import Clock, INTENT, accept, prepared, response, started
from test_native_setup import CLIENT, LINK_TEXT
from test_session import BINDING, inspected, reviewed


class SyntheticWorker:
    def __init__(self):
        self.results = queue.Queue()
        self.operations = []
        self.waiting = None

    @property
    def idle(self):
        return self.waiting is None and self.results.empty()

    def submit(self, operation):
        if not self.idle:
            return False
        self.waiting = operation
        self.operations.append(operation)
        return True

    def respond(self, native, outcome=None):
        operation, self.waiting = self.waiting, None
        self.results.put((operation, outcome or Outcome("accepted", response(native, operation.action))))

    def close(self):
        pass


def native_window():
    view = source_window()
    view.state = inspected()
    clock = Clock()
    view.native = NativeRun(CLIENT, now=lambda: clock.time, monotonic=lambda: clock.seconds)
    view.native_worker = SyntheticWorker()
    view.delivery = None
    view._native_authority_sent = None
    view.status_owned = True
    view.update_state = "current"
    for name in ("native_checked", "native_status", "native_scope", "native_disclosure", "native_agreement",
                 "native_approve", "native_start", "native_retry", "native_cancel", "native_account_button",
                 "open_account_button", "account_details_label", "contacts"):
        setattr(view, name, Mock())
    view.native_checked.get.return_value = False
    view.show_workspace = Mock()
    return view, clock


def tick(view, outcome=None):
    view.native_worker.respond(view.native, outcome)
    view.pump_native()


class NativeAppTests(unittest.TestCase):
    def test_native_disclosure_disabled_theme_drift_cannot_pass_style_gate(self):
        import app
        from render_contact_workspace import native_disclosure_states
        style = Mock()
        colors = {"background": app.BACKGROUND, "foreground": app.MUTED}
        style.map.side_effect = lambda _name, key: [("disabled", colors[key])]
        style.lookup.side_effect = lambda _name, key, **_kwargs: colors[key]
        result = native_disclosure_states(style)
        self.assertEqual(result["resolved"]["disabled selected"], colors)
        style.lookup.side_effect = lambda _name, key, **_kwargs: "#d9d9d9" if key == "background" else colors[key]
        with self.assertRaisesRegex(RuntimeError, "disabled color changed"):
            native_disclosure_states(style)
        style.map.return_value, style.map.side_effect = [], None
        with self.assertRaisesRegex(RuntimeError, "disabled map changed"):
            native_disclosure_states(style)

    def test_render_scroll_synchronizes_cached_heights_and_keeps_strict_overflow_gate(self):
        from native_setup_fixture import require_full_text_scroll, text_scroll_evidence
        widget, root = Mock(), Mock()
        calls = []
        def count(*args):
            calls.append(args)
            return 900 if "update" in args else (160,)
        def move(fraction):
            if fraction == 1:
                self.assertIn(("1.0", "end", "update", "ypixels"), calls)
        widget.count.side_effect = count
        widget.yview_moveto.side_effect = move
        widget.yview.side_effect = [(0.0, 0.3), (0.7, 1.0)]
        widget.winfo_height.return_value = 300
        widget.winfo_width.return_value = 800
        widget.dlineinfo.return_value = (10, 270, 300, 18, 13)
        widget.bbox.return_value = (305, 270, 5, 18)
        evidence = text_scroll_evidence(root, widget)
        evidence["containsFullScopeAndError"] = True
        require_full_text_scroll(evidence)
        self.assertEqual((evidence["cachedContentPixels"], evidence["contentPixels"]), (160, 900))
        for changed in ({"after": [0.0, 1.0]}, {"contentPixels": 160}, {"lastLine": None},
                        {"lastLine": (10, 295, 300, 18, 13)}, {"containsFullScopeAndError": False}):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                require_full_text_scroll({**evidence, **changed})

    def test_fresh_delivery_claim_and_personal_approval_do_not_start_helper(self):
        view, clock = native_window()
        factory = lambda _client: NativeRun(CLIENT, now=lambda: clock.time, monotonic=lambda: clock.seconds)
        with patch.dict(view.receive_setup.__globals__, NativeRun=factory):
            self.assertEqual(view.receive_setup(LINK_TEXT), "accepted")
        tick(view)
        view.helper.start.assert_not_called()
        self.assertFalse(view.native.accepted)
        view.native_checked.get.return_value = True
        view.approve_native()
        tick(view)
        self.assertEqual(view.native.phase, "prepared")
        view.helper.start.assert_not_called()
        view.start_native()
        view.helper.start.assert_not_called()
        tick(view)
        view.helper.start.assert_called_once_with("connect")
        self.assertTrue(view.state.running)
        self.assertIsNone(view.state.approval)

    def test_duplicate_delivery_does_not_mint_request_or_reapprove(self):
        view, _ = native_window()
        view.native, _ = prepared()
        request = view.native.approval_request.body
        self.assertEqual(view.receive_setup(LINK_TEXT), "duplicate")
        self.assertEqual(view.native.approval_request.body, request)
        self.assertEqual(view.native_worker.operations, [])
        self.assertFalse(view.native.started)

    def test_busy_existing_collection_rejects_setup_and_keeps_selection(self):
        view, _ = native_window()
        view.state = reviewed()
        self.assertEqual(view.receive_setup(LINK_TEXT), "busy")
        self.assertEqual(view.state.selected_ids, {1})
        self.assertEqual(view.native_worker.operations, [])

    def test_legacy_agree_cannot_bypass_active_native_setup(self):
        view, _ = native_window()
        view.native, _ = prepared()
        view.checked.get.return_value = True
        view.agree()
        self.assertIsNone(view.state.approval)
        self.assertFalse(view.native.started)

    def test_start_response_loss_and_late_response_never_duplicate_helper(self):
        view, _ = native_window()
        view.native, _ = prepared()
        view.start_native()
        first = view.native_worker.waiting
        tick(view, Outcome("uncertain"))
        view.helper.start.assert_not_called()
        view.native_action(view.native.retry)
        self.assertEqual(first.body, view.native_worker.waiting.body)
        tick(view)
        view.handle_native_result(view.native.complete(first, Outcome("accepted", response(view.native, "start"))))
        view.helper.start.assert_called_once_with("connect")

    def test_continue_freezes_exact_selection_before_pair_or_bind_and_retries_exact(self):
        view, _ = native_window()
        view.native, _ = started()
        view.state = reviewed()
        view.pair()
        self.assertTrue(view.native_selection_locked)
        self.assertEqual(view.native.continued_selection, (1,))
        view.helper.write.assert_not_called()
        view.clear_all_contacts()
        view.select_all_contacts()
        view.selection_changed()
        self.assertEqual(view.state.selected_ids, {1})
        tick(view, Outcome("uncertain"))
        view.pair()
        tick(view)
        view.helper.write.assert_called_once_with({"action": "pair"})
        event = {"kind": "pairing", "pairCode": "1234567890", "port": 48751,
                 "expiresInSeconds": 300, "handoffId": BINDING, "payloadSha256": "a" * 64}
        view.events.put(event)
        view.pump()
        self.assertEqual(view.native.handoff_id, BINDING)
        self.assertIn({"action": "run-authority", "handoffId": BINDING, "state": "paused"},
                      [call.args[0] for call in view.helper.write.call_args_list])
        tick(view)  # status -> bind
        first = view.native_worker.waiting
        tick(view, Outcome("uncertain"))
        view.native_action(view.native.retry)
        tick(view)
        self.assertEqual(first.body, view.native_worker.waiting.body)
        with patch.dict(view.handle_native_result.__globals__, webbrowser=Mock()):
            tick(view)
        self.assertEqual(view.native.phase, "bound")
        self.assertFalse(view.state.destination_approved)

    def bound_view(self):
        view, _ = native_window()
        view.native, _ = started()
        view.state = reviewed()
        accept(view.native, view.native.continue_review((1,)))
        view.state.pair()
        view.state.handle({"kind": "pairing", "pairCode": "1234567890", "port": 48751,
                          "expiresInSeconds": 300, "handoffId": BINDING, "payloadSha256": "a" * 64})
        accept(view.native, accept(view.native, view.native.pair_received(BINDING, "a" * 64)))
        return view

    def destination(self, view, intent=INTENT, email="fixture@example.test"):
        view.state.handle({"kind": "destination", "state": "pending", "handoffId": BINDING,
                           "intentId": intent, "email": email, "course": "Synthetic platform course"})
        view.native_destination()

    def test_wrong_destination_never_gets_native_approval(self):
        view = self.bound_view()
        self.destination(view, email="other@example.test")
        self.assertEqual(view.native.authority, "paused")
        self.assertEqual(view.native_worker.operations, [])
        self.assertIsNone(view.state.destination_requested_approval)

    def test_superseded_verify_response_cannot_approve_old_intent(self):
        view = self.bound_view()
        self.destination(view)
        tick(view)  # status -> verify
        old = view.native_worker.waiting
        next_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        self.destination(view, next_id)
        tick(view)  # obsolete verify -> new intent status
        self.assertIsNone(view.state.destination_requested_approval)
        self.assertEqual(view.native.intent_id, next_id)
        tick(view)
        tick(view)
        decisions = [call.args[0] for call in view.helper.write.call_args_list
                     if call.args[0]["action"] == "destination-decision"]
        self.assertEqual(decisions, [{"action": "destination-decision", "handoffId": BINDING,
                                     "intentId": next_id, "approved": True}])
        self.assertIsNone(view.native.complete(old, Outcome("accepted", response(view.native, "verify"))))
        self.assertFalse(view.state.saved_acknowledged)

    def test_cancel_stops_local_work_without_native_server_revoke(self):
        view = self.bound_view()
        with patch.object(view.stop.__globals__["messagebox"], "askyesno", return_value=True):
            view.cancel_native()
        self.assertEqual(view.native.phase, "cancelled")
        view.helper.close_input.assert_called_once()
        self.assertEqual(view.native_worker.operations, [])
        self.assertTrue(view.state._stopping)

    def test_local_failure_retires_inflight_start_before_late_success(self):
        view, _ = native_window()
        view.native, _ = prepared()
        view.start_native()
        view.cooperative_failure()
        tick(view)
        self.assertEqual(view.native.phase, "cancelled")
        view.helper.start.assert_not_called()

    def test_received_and_pending_receipt_cannot_finish_native_collection(self):
        view = self.bound_view()
        for state in ("received", "saved_pending_browser_receipt", "saved"):
            view.state.poll()
            view.events.put({"kind": "handoff", "state": state, "handoffId": BINDING})
            view.pump()
            commands = [call.args[0]["action"] for call in view.helper.write.call_args_list]
            self.assertEqual("finish" in commands, state == "saved")
        self.assertEqual(view.native.phase, "complete")
        self.assertEqual(commands.count("finish"), 1)
        self.assertIsNone(view.native.run)
        self.assertEqual(view.native.completion.handoff_id, BINDING)
        browser = Mock()
        with patch.dict(view.open_account.__globals__, webbrowser=browser):
            view.pair()
        browser.open.assert_called_once_with(view.native.completion.url)
        self.assertNotIn("continue=", browser.open.call_args.args[0])

    def test_bound_success_does_not_open_a_duplicate_browser_tab(self):
        view = self.bound_view()
        browser = Mock()
        with patch.dict(view.handle_native_result.__globals__, webbrowser=browser):
            view.handle_native_result("bound")
            browser.open.assert_not_called()
            view.pair()  # Explicit missing-tab recovery only; no new pair or bind.
        browser.open.assert_called_once_with(view.native.continue_url)
        self.assertEqual(view.native_worker.operations, [])
        self.assertEqual(view.native.phase, "bound")

    def test_pending_receipt_blocks_browser_continue_and_account_uses_control_route(self):
        view = self.bound_view()
        view.state.save_receipt_pending = True
        browser = Mock()
        with patch.dict(view.open_account.__globals__, webbrowser=browser):
            view.pair()
            browser.open.assert_not_called()
            view.open_account()
        url = browser.open.call_args.args[0]
        self.assertIn("phoneRunControl=" + view.native.run.run_id, url)
        self.assertNotIn("continue=", url)

    def test_expired_handoff_requires_new_setup_and_preserves_local_review(self):
        view = self.bound_view()
        view.state.poll()
        view.events.put({"kind": "handoff", "state": "expired", "handoffId": BINDING})
        view.pump()
        self.assertEqual(view.native.phase, "handoff_expired")
        self.assertTrue(view.native_selection_locked)
        self.assertFalse(view.native.current)
        self.assertEqual(view.state.selected_ids, {1})
        self.assertFalse(view.state._stopping)
        view.pair()
        self.assertEqual(view.native_worker.operations, [])

    def test_full_scope_disclosure_and_long_error_stay_in_scrollable_text(self):
        view, _ = native_window()
        view.native, _ = prepared()
        view.native.message = "Synthetic recovery detail. " * 40
        view.refresh_native()
        text = view.native_disclosure.insert.call_args.args[1]
        self.assertIn("fixture@example.test", text)
        self.assertIn(DISCLOSURE["sentences"][0], text)
        self.assertIn(view.native.message, text)
        self.assertLess(len(view.native_status.set.call_args.args[0]), 180)

    def test_contact_workspace_exposes_native_retry_without_hiding_source_coverage(self):
        view, _ = native_window()
        view.native, _ = started()
        view.state = reviewed()
        operation = view.native.continue_review((1,))
        view.native.complete(operation, Outcome("uncertain"))
        for name in ("workspace_coverage", "workspace_status", "review_button", "pair_button"):
            setattr(view, name, Mock())
        view._workspace_capture = True
        type(view).refresh_workspace(view)
        self.assertIn("Setup", view.workspace_status.set.call_args.args[0])
        self.assertIn("readable", view.workspace_coverage.set.call_args.args[0])
        view.refresh_native()
        self.assertEqual(view.native_retry.configure.call_args.kwargs["state"], "normal")
        view.native_action(view.native.retry)
        self.assertEqual(view.native_worker.waiting.action, "status_continue")


if __name__ == "__main__":
    unittest.main()
