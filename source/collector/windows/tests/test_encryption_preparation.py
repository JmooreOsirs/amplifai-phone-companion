"""Real Windows callbacks and run authority; synthetic phone and network boundaries."""
import json
import sys
import unittest
from unittest.mock import patch

import helper_process
from native_run import Outcome
from native_setup import SetupProtocolError
from session import BACKUP_CHECK_MODE
from test_native_app import native_window, tick
from test_native_run import prepared, response


def ready():
    view, _ = native_window()
    view.native, clock = prepared()
    view.guide_step = 0
    view.render_guide = lambda: None
    return view, clock


def finish_check(view, encrypted, *, code=0):
    if encrypted is not None:
        view.events.put({"kind": BACKUP_CHECK_MODE, "backupEncrypted": encrypted})
    view.events.put({"kind": "process-exit", "code": code})
    view.pump()
    view.events.put({"kind": "residue", "sessions": []})
    view.events.put({"kind": "process-exit", "code": 0})
    view.pump()


class EncryptionPreparationTests(unittest.TestCase):
    def test_start_checks_automatically_then_revalidates_same_run_before_encrypted_launch(self):
        view, _ = ready()
        run = view.native.run
        view.start_native()
        self.assertEqual(view.state.mode, BACKUP_CHECK_MODE)
        view.helper.start.assert_called_once_with(BACKUP_CHECK_MODE)
        self.assertEqual(view.native_worker.operations, [])
        finish_check(view, True)
        self.assertEqual(view.native.run, run)
        self.assertEqual(view.native.phase, "starting")
        request = json.loads(view.native_worker.waiting.body)
        self.assertEqual(set(request), {"runId", "capability", "expectedApprovalId", "operationId"})
        self.assertEqual(request["runId"], run.run_id)
        self.assertEqual(request["expectedApprovalId"], run.approval_id)
        self.assertEqual(view.state.mode, "inspect")
        tick(view)
        view.helper.start.assert_called_with("connect", require_encrypted_backup=True)
        self.assertEqual(view.native.call_history_preparation, "confirmedEncryptedBackup")
        self.assertFalse(view.state.saved_acknowledged)

    def test_off_and_unknown_require_deliberate_available_data_choice(self):
        for setting in (False, None):
            with self.subTest(setting=setting):
                view, _ = ready()
                view.start_native()
                finish_check(view, setting)
                self.assertEqual(view.native.phase, "prepared")
                self.assertEqual(view.native_worker.operations, [])
                self.assertIn("call history", view.native.message.lower())
                self.assertEqual(view.guide_step, 2)
                view.start_with_available_data()
                tick(view)
                view.helper.start.assert_called_with("connect", require_encrypted_backup=False)
                self.assertEqual(view.native.call_history_preparation, "continueWithoutConfirmedCoverage")

    def test_old_on_result_never_authorizes_a_new_start_without_a_check(self):
        view, _ = ready()
        view.state.checked_backup_setting = True
        view.start_native()
        self.assertEqual(view.state.mode, BACKUP_CHECK_MODE)
        self.assertEqual(view.native_worker.operations, [])
        finish_check(view, False)
        self.assertEqual(view.native.phase, "prepared")
        self.assertEqual(view.native_worker.operations, [])

    def test_readonly_recheck_does_not_create_a_start_intent(self):
        view, _ = ready()
        view.check_backup_setting()
        finish_check(view, True)
        self.assertEqual(view.native_worker.operations, [])
        self.assertEqual(view.native.phase, "prepared")
        view.start_native()
        self.assertEqual(view.state.mode, BACKUP_CHECK_MODE)

    def test_failed_check_cannot_use_its_earlier_on_packet(self):
        view, _ = ready()
        view.start_native()
        finish_check(view, True, code=1)
        self.assertEqual(view.native.phase, "prepared")
        self.assertEqual(view.native.backup_setting, "unknown")
        self.assertEqual(view.native_worker.operations, [])

    def test_check_error_keeps_approval_but_never_auto_starts(self):
        view, _ = ready()
        view.start_native()
        view.events.put({"kind": "error", "code": "no_device"})
        view.pump()
        finish_check(view, None, code=1)
        self.assertEqual(view.native.phase, "prepared")
        self.assertEqual(view.native_worker.operations, [])

    def test_cancel_expire_and_replaced_run_discard_check_completion(self):
        for action in ("cancel", "expire", "replace"):
            with self.subTest(action=action):
                view, clock = ready()
                view.start_native()
                if action == "cancel":
                    view.cancel_native()
                elif action == "expire":
                    clock.advance(86400)
                else:
                    view.native, _ = prepared()
                finish_check(view, True)
                self.assertEqual(view.native_worker.operations, [])
                self.assertNotEqual(view.state.mode, "connect")
                if action == "replace":
                    view.start_with_available_data()
                    self.assertEqual(view.native_worker.operations, [])

    def test_remote_replacement_and_late_cancellation_cannot_launch(self):
        for action in ("refused", "replacement_required", "cancel", "owner_lost"):
            with self.subTest(action=action):
                view, _ = ready()
                view.start_native()
                finish_check(view, True)
                if action == "cancel":
                    view.cancel_native()
                    tick(view)
                elif action == "owner_lost":
                    view.status_owned = False
                    tick(view)
                elif action == "refused":
                    tick(view, Outcome("refused"))
                else:
                    body = json.loads(response(view.native, "start"))
                    body["state"] = action
                    tick(view, Outcome("accepted", json.dumps(body).encode()))
                self.assertNotEqual(view.state.mode, "connect")

    def test_uncertain_start_retry_keeps_operation_and_encryption_guard(self):
        view, _ = ready()
        view.start_native()
        finish_check(view, True)
        body = view.native_worker.waiting.body
        tick(view, Outcome("uncertain"))
        view.native_action(view.native.retry)
        self.assertEqual(view.native_worker.waiting.body, body)
        tick(view)
        view.helper.start.assert_called_with("connect", require_encrypted_backup=True)

    def test_partial_choice_is_unavailable_before_a_current_check_and_after_expiry(self):
        view, clock = ready()
        view.state.checked_backup_setting = False
        view.start_with_available_data()
        self.assertEqual(view.native_worker.operations, [])
        view.start_native()
        finish_check(view, False)
        clock.advance(86400)
        view.start_with_available_data()
        self.assertEqual(view.native_worker.operations, [])

    def test_native_run_cannot_start_without_current_preparation(self):
        run, _ = prepared()
        with self.assertRaises(SetupProtocolError):
            run.start()

    def test_password_stays_on_existing_private_pipe_after_encrypted_admission(self):
        view, _ = ready()
        view.start_native()
        finish_check(view, True)
        tick(view)
        view.events.put({"kind": "needs-password"})
        view.pump()
        view.password.get.return_value = "synthetic-local-backup-password"
        view.send_password()
        view.helper.stdin.write.assert_called_once()
        self.assertNotIn("synthetic-local-backup-password", repr(view.native_worker.operations))
        view.password.delete.assert_called()


class EncryptedHelperBoundaryTests(unittest.TestCase):
    def test_only_connect_may_receive_the_existing_encrypted_backup_guard(self):
        for required in (False, True):
            with self.subTest(required=required):
                helper = helper_process.HelperProcess([sys.executable], lambda _: None)
                with patch.object(helper_process.subprocess, "Popen") as spawn, patch.object(helper_process.threading, "Thread"):
                    helper.start("connect", require_encrypted_backup=required)
                self.assertEqual(spawn.call_args.args[0], [sys.executable, "connect"] + (["--require-encrypted-backup"] if required else []))
                self.assertFalse(spawn.call_args.kwargs["shell"])
        for mode in ("inspect", "clear-residue", BACKUP_CHECK_MODE):
            helper = helper_process.HelperProcess([sys.executable], lambda _: None)
            with self.assertRaises(ValueError):
                helper.start(mode, require_encrypted_backup=True)


if __name__ == "__main__":
    unittest.main()
