"""Synthetic authority boundaries; never contact an account or collect a device."""
import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from native_run import (BIND_PATH, CLAIM_PATH, STATUS_PATH, VERIFY_PATH,
                        NativeRun, NativeTransport, Operation, Outcome)
from native_setup import SetupProtocolError, request_body, timestamp
from test_native_setup import CLIENT, FIXTURE, LINK_TEXT

HANDOFF = "99999999-9999-4999-8999-999999999999"
INTENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class Clock:
    def __init__(self):
        self.time = timestamp("2026-10-21T22:00:00Z")
        self.seconds = 0

    def advance(self, seconds):
        self.time += timedelta(seconds=seconds)
        self.seconds += seconds


def response(machine, action):
    if action == "claim":
        value = copy.deepcopy(FIXTURE["claimResponse"])
        value["requestId"] = machine.claim_request.request_id
    elif action == "approve":
        value = copy.deepcopy(FIXTURE["approveResponse"])
        value["requestId"] = machine.approval_request.request_id
    elif action == "bind":
        value = {key: FIXTURE["approveResponse"][key] for key in
                 ("approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories")}
        value.update(protocol="phone-start-v1", operationId=machine.operation_id)
    elif action == "verify":
        value = {key: FIXTURE["approveResponse"][key] for key in
                 ("approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories")}
    else:
        value = copy.deepcopy(FIXTURE["approveResponse"]["preparation"])
        if machine.operation_id:
            value["operation"] = {"operationId": machine.operation_id, "admittedAt": "2026-10-21T22:00:00Z"}
    return request_body(value)


def accept(machine, operation):
    return machine.complete(operation, Outcome("accepted", response(machine, operation.action)))


def prepared():
    clock = Clock()
    machine = NativeRun(CLIENT, now=lambda: clock.time, monotonic=lambda: clock.seconds)
    accept(machine, machine.claim_link(LINK_TEXT))
    machine.agree(True)
    accept(machine, machine.approve())
    return machine, clock


def started():
    machine, clock = prepared()
    assert accept(machine, machine.start()) == "start"
    return machine, clock


def bound():
    machine, clock = started()
    assert accept(machine, machine.continue_review((1, 4))) == "pair"
    operation = accept(machine, machine.pair_received(HANDOFF, "d" * 64))
    assert operation.path == BIND_PATH
    assert accept(machine, operation) == "bound"
    return machine, clock


class NativeRunTests(unittest.TestCase):
    def test_claim_and_agreement_never_start_collection(self):
        clock = Clock()
        run = NativeRun(CLIENT, now=lambda: clock.time, monotonic=lambda: clock.seconds)
        with self.assertRaises(SetupProtocolError):
            run.start()
        self.assertIsNone(accept(run, run.claim_link(LINK_TEXT)))
        with self.assertRaises(SetupProtocolError):
            run.approve()
        run.agree(True)
        self.assertIsNone(accept(run, run.approve()))
        self.assertFalse(run.started)
        self.assertEqual(run.phase, "prepared")
        self.assertEqual(accept(run, run.start()), "start")
        with self.assertRaises(SetupProtocolError):
            run.start()

    def test_approval_loss_retries_exact_bytes_after_setup_ttl_without_extension(self):
        run, clock = prepared()
        # Recreate an uncertain response from the same approval request.
        run.run = None
        run.phase = "approval_uncertain"
        body = run.approval_request.body
        clock.advance(601)
        retry = run.retry()
        self.assertEqual(retry.body, body)
        self.assertIsNone(accept(run, retry))
        self.assertEqual(run.phase, "prepared")
        self.assertEqual(run.run.expires_at, timestamp(FIXTURE["approveResponse"]["expiresAt"]))
        self.assertFalse(run.started)

    def test_claim_replay_uses_same_capability_and_new_callback_token(self):
        clock = Clock()
        run = NativeRun(CLIENT, now=lambda: clock.time, monotonic=lambda: clock.seconds)
        first = run.claim_link(LINK_TEXT)
        run.complete(first, Outcome("uncertain"))
        retry = run.retry()
        self.assertEqual(retry.body, first.body)
        self.assertNotEqual(retry.token, first.token)
        self.assertIsNone(accept(run, first))
        self.assertEqual(run.phase, "claiming")
        accept(run, retry)
        self.assertEqual(run.phase, "pending_approval")

    def test_start_loss_replays_same_operation_once_and_late_success_is_ignored(self):
        run, clock = prepared()
        first = run.start()
        clock.advance(21)
        self.assertTrue(run.timeout())
        self.assertFalse(run.started)
        retry = run.retry()
        self.assertEqual(retry.body, first.body)
        self.assertIsNone(accept(run, first))
        self.assertEqual(accept(run, retry), "start")
        self.assertIsNone(accept(run, retry))

    def test_status_cannot_adopt_an_unrequested_start(self):
        run, _ = prepared()
        operation = run.refresh()
        value = json.loads(response(run, "status"))
        value["operation"] = {"operationId": HANDOFF, "admittedAt": "2026-10-21T22:00:00Z"}
        run.complete(operation, Outcome("accepted", request_body(value)))
        self.assertFalse(run.started)
        self.assertEqual(run.authority, "paused")

    def test_continue_requires_current_started_review_and_survives_response_loss(self):
        run, _ = prepared()
        with self.assertRaises(SetupProtocolError):
            run.continue_review((1, 4))
        accept(run, run.start())
        operation = run.continue_review((1, 4))
        run.complete(operation, Outcome("uncertain"))
        with self.assertRaises(SetupProtocolError):
            run.continue_review((1, 7))
        self.assertEqual(accept(run, run.continue_review((1, 4))), "pair")
        with self.assertRaises(SetupProtocolError):
            run.continue_review((1, 4))

    def test_selection_change_retires_pending_continue_callback(self):
        run, _ = started()
        operation = run.continue_review((1, 4))
        run.selection_changed()
        self.assertIsNone(accept(run, operation))
        self.assertEqual(accept(run, run.continue_review((2, 9))), "pair")

    def test_bind_loss_rechecks_current_authority_and_reuses_exact_binding(self):
        run, _ = started()
        accept(run, run.continue_review((1, 4)))
        first = accept(run, run.pair_received(HANDOFF, "d" * 64))
        run.complete(first, Outcome("uncertain"))
        status = run.retry()
        self.assertEqual(status.path, STATUS_PATH)
        retry = accept(run, status)
        self.assertEqual(retry.body, first.body)
        self.assertEqual(accept(run, retry), "bound")

    def test_destination_requires_bound_review_matching_scope_and_current_intent(self):
        run, _ = bound()
        self.assertIsNone(run.verify(INTENT, "other@example.test", run.run.scope.course))
        self.assertEqual(run.authority, "paused")
        status = run.verify(INTENT, run.run.scope.email, run.run.scope.course)
        first = accept(run, status)
        self.assertEqual(first.path, VERIFY_PATH)
        self.assertEqual(json.loads(first.body)["payloadSha256"], "d" * 64)
        run.supersede_intent()
        self.assertIsNone(accept(run, first))
        status = run.verify(HANDOFF, run.run.scope.email, run.run.scope.course)
        self.assertEqual(accept(run, accept(run, status)), "destination_verified")

    def test_revocation_expiry_and_cancel_block_late_responses(self):
        for state in ("revoked", "expired", "replacement_required"):
            run, _ = started()
            operation = run.refresh()
            value = json.loads(response(run, "status"))
            value.update(state=state, operation=None)
            run.complete(operation, Outcome("accepted", request_body(value)))
            self.assertEqual(run.phase, "refused")
            self.assertTrue(run.stop_requested)
        run, clock = started()
        operation = run.refresh()
        clock.advance(43201)
        self.assertTrue(run.timeout())
        self.assertIsNone(accept(run, operation))
        self.assertFalse(run.current)
        run, _ = started()
        operation = run.refresh()
        run.cancel()
        self.assertIsNone(accept(run, operation))
        self.assertTrue(run.stop_requested)

    def test_wrong_scope_or_malformed_success_pauses_without_permission(self):
        for key, value in (("destinationEmail", "other@example.test"), ("categories", ["contacts"]),
                           ("disclosureVersion", "phone-run-macos-v1"), ("expiresAt", "2026-10-23T10:00:00Z")):
            run, _ = started()
            operation = run.refresh()
            changed = json.loads(response(run, "status"))
            changed[key] = value
            run.complete(operation, Outcome("accepted", request_body(changed)))
            self.assertEqual(run.authority, "paused")
        run, _ = prepared()
        operation = run.start()
        run.complete(operation, Outcome("accepted", b"{}"))
        self.assertFalse(run.started)
        self.assertEqual(run.phase, "start_uncertain")


class NativeTransportTests(unittest.TestCase):
    def post(self, status=200, body=b"{}", headers=None, path=CLAIM_PATH):
        response = Mock(status=status)
        values = {"Content-Type": "application/json", **(headers or {})}
        response.getheader.side_effect = lambda key, default=None: values.get(key, default)
        response.read.return_value = body
        connection = Mock(sock=None)
        connection.getresponse.return_value = response
        with patch("native_run.http.client.HTTPSConnection", return_value=connection) as factory:
            result = NativeTransport().post(Operation("claim", path, b"{}"))
        return result, connection, factory

    def test_fixed_origin_bounded_body_no_cookie_or_redirect(self):
        result, connection, factory = self.post()
        self.assertEqual(result.state, "accepted")
        self.assertEqual(factory.call_args.args, ("amplifai.satoris.ai",))
        self.assertEqual(connection.request.call_args.args[:3], ("POST", CLAIM_PATH, b"{}"))
        self.assertEqual(set(connection.request.call_args.args[3]), {"Content-Type", "Accept", "Cache-Control"})
        connection.getresponse.return_value.read.assert_called_once_with(8193)
        connection.close.assert_called_once()
        for status in (201, 301, 302, 307, 429, 500, 503):
            self.assertEqual(self.post(status=status)[0].state, "uncertain")
        for status in (400, 401, 403, 404, 409, 410):
            self.assertEqual(self.post(status=status)[0].state, "refused")

    def test_non_json_compression_oversize_truncation_and_browser_routes_rejected(self):
        for headers in ({"Content-Type": "text/html"}, {"Content-Encoding": "gzip"},
                        {"Content-Length": "99999"}, {"Content-Length": "-1"}, {"Content-Length": "8"}):
            self.assertEqual(self.post(headers=headers)[0].state, "uncertain")
        self.assertEqual(self.post(body=b"x" * 8193)[0].state, "uncertain")
        for path in ("https://evil.test/claim", "/api/v1/phone-platform-setups", "/api/v1/phone-platform-setups/revoke"):
            result, _, factory = self.post(path=path)
            self.assertEqual(result.state, "uncertain")
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
