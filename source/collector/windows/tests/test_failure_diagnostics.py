"""Safe, in-memory diagnostics survive the RC25 automatic-inspection path."""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session import (BACKUP_CHECK_MODE, FAILURE_CODES, FAILURE_STAGES,
                     FAILURE_EXCEPTION_CLASSES, FAILURE_REVIEW_STEPS, ProtocolError, Session)


def inspected() -> Session:
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    return state


def collecting() -> Session:
    state = inspected()
    state.approve(True)
    state.begin("connect")
    return state


def failed_processing() -> Session:
    state = collecting()
    state.handle({"kind": "transfer", "stage": "processing", "processedBytes": 1_181_116_006})
    state.handle({"kind": "error", "code": "selected_payload_crypto", "stage": "processing"})
    state.exited(1)
    return state


class FailureDiagnosticTests(unittest.TestCase):
    def test_observed_processing_failure_survives_automatic_clean_inspection(self):
        state = failed_processing()
        original = state.collection_failure
        self.assertEqual((original.code, original.stage, original.helper_exit_code),
                         ("selected_payload_crypto", "processing", 1))
        self.assertEqual(original.operation, "connect")
        observed = datetime.fromisoformat(original.occurred_at.replace("Z", "+00:00"))
        self.assertEqual(observed.tzinfo, timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - observed).total_seconds()), 10)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertIs(state.collection_failure, original)
        self.assertEqual(state.collection_outcome, "failed")
        self.assertFalse(state.saved_acknowledged)
        self.assertIn("no marked abandoned sessions remain", state.status)
        self.assertIn("selected_payload_crypto", state.failure_summary)
        self.assertIn("Helper-reported stage: processing", state.failure_summary)
        self.assertIn("Helper exit code: 1", state.failure_detail_text)
        self.assertIn(original.occurred_at, state.failure_detail_text)

    def test_cleanup_failures_are_separate_and_do_not_replace_collection_cause(self):
        state = failed_processing()
        original = state.collection_failure
        state.begin("inspect")
        state.handle({"kind": "error", "code": "workspace_unsafe"})
        state.exited(2)
        self.assertIs(state.collection_failure, original)
        self.assertEqual(state.operation_failure.code, "workspace_unsafe")
        self.assertEqual(state.operation_failure.operation, "inspect")
        self.assertEqual(state.operation_failure.helper_exit_code, 2)
        self.assertIn("Subsequent operation failure", state.failure_detail_text)
        self.assertIn("selected_payload_crypto", state.failure_summary)
        self.assertNotIn("workspace_unsafe", state.failure_summary)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertIs(state.collection_failure, original)
        self.assertIsNone(state.operation_failure)

    def test_residue_removal_and_reinspection_preserve_original_failure(self):
        state = failed_processing()
        original = state.collection_failure
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": [{"name": "session-" + "a" * 32, "bytes": 4096}]})
        state.exited(0)
        state.begin("clear-residue")
        state.handle({"kind": "cleared", "count": 1})
        state.exited(0)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertIs(state.collection_failure, original)
        self.assertIn("selected_payload_crypto", state.failure_detail_text)

    def test_only_a_new_approved_collection_resets_collection_diagnostics(self):
        state = failed_processing()
        original = state.collection_failure
        with self.assertRaises(ProtocolError):
            state.begin("connect")
        self.assertIs(state.collection_failure, original)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        state.begin(BACKUP_CHECK_MODE)
        state.handle({"kind": "backup-encryption-status", "backupEncrypted": True})
        state.exited(0)
        self.assertIs(state.collection_failure, original)
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        state.approve(True)
        state.begin("connect")
        self.assertIsNone(state.collection_failure)
        self.assertIsNone(state.operation_failure)
        self.assertEqual(state.failure_summary, "")
        self.assertEqual(state.failure_detail_text, "")

    def test_all_fixed_helper_stages_and_codes_remain_identifiable(self):
        for code in FAILURE_CODES:
            with self.subTest(code=code):
                state = collecting()
                state.handle({"kind": "error", "code": code, "stage": "processing"})
                self.assertEqual(state.collection_failure.code, code)
        for stage in FAILURE_STAGES:
            with self.subTest(stage=stage):
                state = collecting()
                state.handle({"kind": "error", "code": "collection_failed", "stage": stage})
                self.assertEqual(state.collection_failure.stage, stage)

    def test_unknown_untrusted_fields_are_not_retained_or_displayed(self):
        sentinel = "FICTIONAL_SECRET_C:/private/contact/token/password"
        for code in (None, False, 12, [], {}, sentinel, "x" * 100_000):
            with self.subTest(code_type=type(code).__name__):
                state = collecting()
                state.handle({"kind": "error", "code": code, "stage": sentinel,
                              "message": sentinel, "password": sentinel,
                              "path": sentinel, "token": sentinel, "deviceCode": sentinel,
                              "exceptionClass": sentinel, "reviewStep": sentinel,
                              "occurred_at": sentinel})
                record = asdict(state.collection_failure)
                self.assertEqual(record["code"], "unrecognized_error")
                self.assertEqual(record["stage"], "unknown")
                self.assertIsNone(record["device_code"])
                self.assertEqual(set(record), {"code", "stage", "occurred_at", "operation", "device_code",
                                               "helper_exit_code", "exception_class", "review_step"})
                self.assertIsNone(record["exception_class"])
                self.assertIsNone(record["review_step"])
                self.assertNotIn(sentinel, repr(record) + state.failure_detail_text + state.failure_summary)
                self.assertLess(len(state.failure_detail_text), 400)

    def test_review_failure_class_and_operation_survive_cleanup_without_sensitive_text(self):
        state = collecting()
        state.handle({"kind": "error", "code": "collection_failed", "stage": "review",
                      "exceptionClass": "unicode_encode", "reviewStep": "capture_packet",
                      "message": "FICTIONAL_PRIVATE_CONTACT", "traceback": "FICTIONAL_PRIVATE_PATH"})
        state.exited(1)
        original = state.collection_failure
        state.begin("inspect")
        state.handle({"kind": "residue", "sessions": []})
        state.exited(0)
        self.assertIs(state.collection_failure, original)
        self.assertEqual((original.exception_class, original.review_step), ("unicode_encode", "capture_packet"))
        self.assertIn("Failure class: unicode_encode", state.failure_detail_text)
        self.assertIn("Review operation: capture_packet", state.failure_detail_text)
        self.assertNotIn("FICTIONAL_PRIVATE", state.failure_detail_text)

    def test_failure_classification_accepts_only_fixed_enums_at_their_valid_boundary(self):
        for field, values in (("exceptionClass", FAILURE_EXCEPTION_CLASSES), ("reviewStep", FAILURE_REVIEW_STEPS)):
            for value in values:
                state = collecting()
                state.handle({"kind": "error", "code": "collection_failed", "stage": "review", field: value})
                attribute = "exception_class" if field == "exceptionClass" else "review_step"
                self.assertEqual(getattr(state.collection_failure, attribute), value)
            for value in (None, False, 12, [], {}, "FICTIONAL_PRIVATE", "x" * 100_000):
                state = collecting()
                state.handle({"kind": "error", "code": "collection_failed", "stage": "review", field: value})
                self.assertIsNone(getattr(state.collection_failure, attribute))
        state = collecting()
        state.handle({"kind": "error", "code": "protocol", "stage": "review",
                      "exceptionClass": "unicode_encode", "reviewStep": "capture_packet"})
        self.assertIsNone(state.collection_failure.exception_class)
        self.assertIsNone(state.collection_failure.review_step)
        state = collecting()
        state.handle({"kind": "error", "code": "protocol", "exceptionClass": "unicode_decode",
                      "message": "FICTIONAL_PRIVATE_BYTES"})
        self.assertEqual(state.collection_failure.exception_class, "unicode_decode")
        self.assertNotIn("FICTIONAL_PRIVATE_BYTES", state.failure_detail_text)
        state = collecting()
        state.handle({"kind": "error", "code": "collection_failed", "stage": "processing", "reviewStep": "capture_packet"})
        self.assertIsNone(state.collection_failure.review_step)

    def test_known_code_drops_raw_extra_fields_and_unreported_stage_is_not_inferred(self):
        state = collecting()
        state.handle({"kind": "transfer", "stage": "processing", "processedBytes": 100})
        state.handle({"kind": "error", "code": "protocol", "cleanupRequired": True,
                      "message": "FICTIONAL_PRIVATE_FRAME", "stage": "private stage"})
        self.assertEqual(state.collection_failure.code, "protocol")
        self.assertEqual(state.collection_failure.stage, "unknown")
        self.assertIn("Helper-reported stage: not reported", state.failure_summary)
        self.assertNotIn("FICTIONAL_PRIVATE_FRAME", state.failure_detail_text)

    def test_device_error_codes_are_bounded_ints_only_for_the_device_failure(self):
        for value in (-(2**31), 0, 2**32 - 1):
            state = collecting()
            state.handle({"kind": "error", "code": "device_backup_failed", "deviceCode": value})
            self.assertEqual(state.collection_failure.device_code, value)
        for value in (True, False, -(2**31) - 1, 2**32, "27", 2.0, None):
            with self.subTest(value=value):
                state = collecting()
                state.handle({"kind": "error", "code": "device_backup_failed", "deviceCode": value})
                self.assertIsNone(state.collection_failure.device_code)
        state = collecting()
        state.handle({"kind": "error", "code": "backup_password", "deviceCode": 27})
        self.assertIsNone(state.collection_failure.device_code)

    def test_first_failure_and_its_time_survive_follow_on_errors(self):
        state = collecting()
        state.handle({"kind": "error", "code": "contacts_schema", "stage": "processing"})
        original = state.collection_failure
        state.handle({"kind": "error", "code": "workspace_cleanup", "stage": "review"})
        state.cooperative_failure()
        self.assertIs(state.collection_failure, original)
        state.exited(1)
        self.assertEqual(state.collection_failure.code, "contacts_schema")
        self.assertEqual(state.collection_failure.occurred_at, original.occurred_at)

    def test_unexpected_exit_has_safe_diagnostic_and_bounded_exit_status(self):
        for exit_code in (0, 1, -(2**31), 2**32 - 1, 2**80):
            with self.subTest(exit_code=exit_code):
                state = collecting()
                state.exited(exit_code)
                self.assertEqual(state.collection_failure.code, "helper_stopped")
                self.assertEqual(state.collection_failure.stage, "unknown")
                self.assertEqual(state.collection_failure.helper_exit_code,
                                 exit_code if exit_code <= 2**32 - 1 else None)
        state = collecting()
        with self.assertRaises(ProtocolError):
            state.exited(True)
        self.assertIsNone(state.collection_failure)

    def test_explicit_stop_without_failure_does_not_invent_collection_error(self):
        state = collecting()
        state.request_stop()
        state.exited(130)
        self.assertEqual(state.collection_outcome, "stopped")
        self.assertIsNone(state.collection_failure)
        self.assertEqual(state.failure_detail_text, "")

    def test_protocol_failure_and_force_stop_have_safe_fixed_diagnostics(self):
        state = collecting()
        state.cooperative_failure()
        self.assertEqual(state.collection_failure.code, "helper_communication_failed")
        self.assertEqual(state.collection_failure.stage, "unknown")
        state = collecting()
        state.force_stopped()
        self.assertEqual(state.collection_failure.code, "force_stopped")

    def test_recoverable_handoff_errors_are_not_mislabeled_as_collection_failure(self):
        from test_session import BINDING, paired

        state = paired()
        state.handle({"kind": "destination", "state": "pending", "handoffId": BINDING,
                      "intentId": "22222222-2222-4222-8222-222222222222",
                      "email": "fictional@example.test", "course": "Fictional course"})
        state.decide_destination(True)
        state.handle({"kind": "error", "code": "handoff_unconfirmed"})
        self.assertTrue(state.reviewed)
        self.assertEqual(state.phase, "reviewing")
        self.assertIsNone(state.collection_failure)
        self.assertEqual(state.failure_summary, "")
        state = paired()
        state.pair()
        state.handle({"kind": "error", "code": "bridge_unavailable"})
        self.assertTrue(state.reviewed)
        self.assertTrue(state.can_pair)
        self.assertIsNone(state.collection_failure)
        self.assertEqual(state.failure_summary, "")


if __name__ == "__main__":
    unittest.main()
