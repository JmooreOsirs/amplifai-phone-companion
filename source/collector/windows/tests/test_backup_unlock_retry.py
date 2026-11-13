"""Windows retry controls use fictional passwords and headless callbacks only."""
import io
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import helper_process
from guided_layout import refresh_collection
from journey import journey
from session import ProtocolError
from test_gui_guards import source_window
from test_native_app import native_window
from test_native_run import started
from test_session import captured


def prompt(attempt):
    return {"kind": "state", "state": "password_required", "attempt": attempt,
            "maxAttempts": 3, **({"reason": "backup_unlock_failed"} if attempt > 1 else {})}


def submitted_first(state):
    state.handle(prompt(1))
    state.write_password("Wrong fictional password", io.StringIO())


class BackupUnlockRetryTests(unittest.TestCase):
    def test_windows_helper_opts_in_only_for_collection(self):
        for mode in ("connect", "inspect", "clear-residue", "backup-encryption-status"):
            with self.subTest(mode=mode):
                helper = helper_process.HelperProcess([sys.executable], lambda _: None)
                with patch.object(helper_process.subprocess, "Popen") as spawn, patch.object(helper_process.threading, "Thread"):
                    helper.start(mode)
                expected = [sys.executable, mode] + (["--retry-backup-unlock"] if mode == "connect" else [])
                self.assertEqual(spawn.call_args.args[0], expected)

    def test_each_attempt_requires_an_explicit_exact_password_send_and_clears_widget(self):
        view = source_window()
        view.helper.stdin = io.StringIO()
        values = ["Wrong fictional password", "  café 漢字 🔐  ", "cafe\u0301"]
        for attempt, value in enumerate(values, 1):
            before = view.helper.stdin.getvalue()
            view.state.handle(prompt(attempt))
            self.assertEqual(view.state.password_attempt, attempt)
            self.assertIn(f"Attempt {attempt} of 3", view.state.password_prompt)
            self.assertEqual(view.helper.stdin.getvalue(), before)
            view.password.get.return_value = value
            view.send_password()
            self.assertEqual(view.state.phase, "processing")
            self.assertNotIn(value, repr(vars(view.state)))
            self.assertEqual(json.loads(view.helper.stdin.getvalue().splitlines()[-1])["value"], value)
        self.assertEqual(view.password.delete.call_count, 3)
        view.password.configure.assert_called_with(state="disabled")

    def test_retry_copy_reaches_existing_collection_controls(self):
        state = source_window().state
        submitted_first(state)
        state.handle(prompt(2))
        view = SimpleNamespace(state=state, space=lambda value: value, native_active=True,
                               collection_stage_labels=[Mock() for _ in range(4)])
        for name in ("collection_eyebrow", "collection_heading", "collection_detail", "collection_illustration",
                     "collection_illustration_caption", "collection_metric_value", "collection_metric_label",
                     "collection_metric_well", "activity", "collection_next"):
            setattr(view, name, Mock())
        refresh_collection(view)
        text = view.collection_detail.configure.call_args.kwargs["text"]
        self.assertIn("Attempt 2 of 3", text)
        self.assertIn("backup-key issue", text)
        self.assertIn("already on this PC", text)
        self.assertIn("no new backup", text)
        state.write_password("Another fictional password", io.StringIO())
        state.handle(prompt(3))
        refresh_collection(view)
        self.assertIn("Attempt 3 of 3", view.collection_detail.configure.call_args.kwargs["text"])

    def test_malformed_or_stale_retry_metadata_is_rejected_without_state_changes(self):
        invalid = [prompt(2), {**prompt(1), "attempt": True}, {**prompt(1), "maxAttempts": 4},
                   {**prompt(1), "maxAttempts": True}, {**prompt(1), "reason": "backup_unlock_failed"},
                   {**prompt(1), "extra": "private"}, {"kind": "state", "state": "password_required", "attempt": 1}]
        for event in invalid:
            with self.subTest(event=event):
                state = source_window().state
                with self.assertRaises(ProtocolError):
                    state.handle(event)
                self.assertEqual(state.phase, "connecting")
        for event in (prompt(1), prompt(3), {**prompt(2), "reason": "password_incorrect"},
                      {key: value for key, value in prompt(2).items() if key != "reason"},
                      {"kind": "state", "state": "password_required"}):
            with self.subTest(event=event):
                state = source_window().state
                submitted_first(state)
                with self.assertRaises(ProtocolError):
                    state.handle(event)
                self.assertEqual(state.password_attempt, 1)
                self.assertEqual(state.phase, "processing")

    def test_legacy_single_prompt_is_supported_without_retry_authority(self):
        state = source_window().state
        state.handle({"kind": "state", "state": "password_required"})
        self.assertNotIn("of 3", state.password_prompt)
        state.write_password("Fictional legacy password", io.StringIO())
        with self.assertRaises(ProtocolError):
            state.handle(prompt(2))

    def test_stop_exit_error_and_capture_never_reopen_password_input(self):
        for terminal in ("stop", "exit", "error", "capture"):
            with self.subTest(terminal=terminal):
                state = source_window().state
                submitted_first(state)
                if terminal == "stop":
                    state.request_stop()
                    state.handle(prompt(2))
                    self.assertEqual(state.phase, "cancelling")
                else:
                    if terminal == "exit":
                        state.exited(1)
                    elif terminal == "error":
                        state.handle({"kind": "error", "code": "backup_password", "stage": "processing"})
                    else:
                        captured(state)
                    with self.assertRaises(ProtocolError):
                        state.handle(prompt(2))
                output = io.StringIO()
                with self.assertRaises(ProtocolError):
                    state.write_password("Never sent fictional value", output)
                self.assertEqual(output.getvalue(), "")

    def test_native_authority_expiry_closes_retry_pipe_and_clears_input(self):
        view, _ = native_window()
        view.native, _ = started()
        view.state = source_window().state
        submitted_first(view.state)
        view.state.handle(prompt(2))
        view.native.run_deadline = -1
        view.pump_native()
        self.assertTrue(view.state._stopping)
        view.helper.close_input.assert_called_once()
        view.password.delete.assert_called_once()
        view.password.get.return_value = "Never sent fictional value"
        view.send_password()
        view.helper.stdin.write.assert_not_called()

    def test_native_authority_is_rechecked_at_submit_before_the_next_pump(self):
        for fault in ("expired", "refused", "cancelled", "paused"):
            with self.subTest(fault=fault):
                view, _ = native_window()
                view.native, _ = started()
                view.state = source_window().state
                submitted_first(view.state)
                view.state.handle(prompt(2))
                if fault == "expired":
                    view.native.run_deadline = -1
                elif fault == "refused":
                    view.native._refused()
                elif fault == "cancelled":
                    view.native.cancel()
                else:
                    view.native.authority = "paused"
                view.password.get.return_value = "Never sent fictional value"
                view.send_password()
                view.helper.stdin.write.assert_not_called()
                self.assertTrue(view.state._stopping)
                view.helper.close_input.assert_called_once()
                view.password.delete.assert_called_once()
                self.assertIn("No password was sent", view.state.status)

    def test_fourth_prompt_is_rejected_and_new_approved_collection_resets_counter(self):
        state = source_window().state
        for attempt in (1, 2, 3):
            state.handle(prompt(attempt))
            state.write_password("Fictional password", io.StringIO())
        with self.assertRaises(ProtocolError):
            state.handle(prompt(4))
        state.handle({"kind": "error", "code": "backup_password", "stage": "processing"})
        state.exited(1)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        state.approve(True)
        state.begin("connect")
        self.assertEqual(state.password_attempt, 0)
        state.handle(prompt(1))
        self.assertEqual(state.password_attempt, 1)

    def test_unlock_failure_copy_survives_automatic_cleanup_inspection(self):
        view, _ = native_window()
        view.native, _ = started()
        state = view.state = source_window().state
        state.handle({"kind": "error", "code": "backup_password", "stage": "processing"})
        self.assertIn("password may not match", state.status)
        self.assertIn("backup keys", state.status)
        self.assertNotIn("device permissions", state.status)
        view.native.fail()
        state.exited(1)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        current = journey(state, view.native)
        self.assertEqual(current.heading, "Backup could not be unlocked")
        self.assertIn("password may not match", current.detail)
        self.assertIn("backup keys", current.detail)
        self.assertFalse(state.can_connect)
        self.assertFalse(state.saved_acknowledged)

    def test_guided_render_matrix_covers_retries_and_exhausted_failure(self):
        from native_setup_fixture import scenarios
        from render_guided_journey import EXPECTED_STAGE
        cases = {name: (native, state) for name, native, state in scenarios()}
        for name, attempt in (("password", 1), ("password-retry", 2), ("password-final-attempt", 3)):
            self.assertIn(name, cases)
            native, state = cases[name]
            self.assertEqual(state.password_attempt, attempt)
            self.assertEqual(journey(state, native).stage, EXPECTED_STAGE[name])
        self.assertIn("backup-unlock-exhausted-clean", cases)
        native, state = cases["backup-unlock-exhausted-clean"]
        self.assertEqual(state.password_attempt, 3)
        self.assertEqual(journey(state, native).heading, "Backup could not be unlocked")
        self.assertFalse(state.needs_inspection)


if __name__ == "__main__":
    unittest.main()
