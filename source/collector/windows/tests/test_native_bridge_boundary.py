"""Native source boundary: real NativeRun + loopback BridgeServer, synthetic authority/data.

No Tk, helper subprocess, device, hosted service, or account Save is exercised.
"""
from __future__ import annotations

import base64
import hashlib
import json
import unittest
from dataclasses import replace

from amplifai_phone.bridge import ACCOUNT_ORIGIN, BridgeServer
from amplifai_phone.metadata import Contact, Interaction, SourceResult
from amplifai_phone.paged_transfer import EMPTY_CHAIN
from native_run import (APPROVE_PATH, BIND_PATH, CLAIM_PATH, START_PATH, STATUS_PATH,
                        VERIFY_PATH, NativeRun, Outcome)
from native_setup import SetupProtocolError
import test_bridge
import test_bridge_run_timing as timing
from test_native_run import Clock, response
from test_native_setup import CLIENT, FIXTURE, LINK_TEXT

INTENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OTHER_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
CAPABILITY = base64.urlsafe_b64encode(b"D" * 32).rstrip(b"=").decode("ascii")
RELEASE = base64.urlsafe_b64encode(b"E" * 32).rstrip(b"=").decode("ascii")
WRONG_GRANT = base64.urlsafe_b64encode(b"X" * 32).rstrip(b"=").decode("ascii")


class SyntheticAuthority:
    """Only the remote authority is replaced; committed requests survive lost replies."""

    def __init__(self, native):
        self.native = native
        self.committed = {}
        self.lose_next = {"claim", "approve", "start", "status_continue", "bind"}
        self.proofs = []
        self.claimed = self.verified = False
        self.consumed = None
        self.consume_commits = 0

    def post(self, operation):
        action, body = operation.action, json.loads(operation.body)
        paths = {"claim": CLAIM_PATH, "approve": APPROVE_PATH, "start": START_PATH,
                 "bind": BIND_PATH, "verify": VERIFY_PATH}
        assert operation.path == paths.get(action, STATUS_PATH)
        if action in ("claim", "approve", "start", "bind"):
            prior = self.committed.get(action)
            if prior is not None and prior != operation.body:
                return Outcome("refused")
            predecessor = {"approve": "claim", "start": "approve", "bind": "start"}.get(action)
            assert predecessor is None or predecessor in self.committed
            self.committed[action] = operation.body
        if action == "approve":
            assert body["disclosure"] == FIXTURE["approveRequest"]["disclosure"]
            assert body["capability"] == json.loads(self.committed["claim"])["capability"]
        if action not in ("claim", "approve"):
            assert body["runId"] == FIXTURE["approveResponse"]["runId"]
            assert body["capability"] == json.loads(self.committed["approve"])["runCapability"]
        if action == "bind":
            assert body["operationId"] == json.loads(self.committed["start"])["operationId"]
        if action == "verify":
            assert self.claimed and body["intentId"] == INTENT
            assert all(body[key] == self.binding[key] for key in self.binding)
            self.verified = True
        result = Outcome("accepted", response(self.native, action))
        if action in self.lose_next:
            self.lose_next.remove(action)
            return Outcome("uncertain")
        return result

    @property
    def binding(self):
        body = json.loads(self.committed["bind"])
        return {key: body[key] for key in ("handoffId", "payloadSha256")}

    def account_proof(self, action, command):
        self.proofs.append((action, dict(command)))
        if "bind" not in self.committed:
            return None
        expected = {**self.binding, "intentId": INTENT, "capability": CAPABILITY}
        if action == "claim" and command == expected:
            self.claimed = True
            return {key: FIXTURE["approveResponse"][key]
                    for key in ("destinationEmail", "destinationCourse")}
        if action == "consume" and self.verified and command == {**expected, "releaseCode": RELEASE}:
            if self.consumed is None:
                self.consumed = dict(command)
                self.consume_commits += 1
                return None  # The server committed; the first upstream reply was lost.
            return {"consumed": command == self.consumed}
        return None


class NativeBridgeBoundaryTest(unittest.TestCase):
    def setUp(self):
        timing.BridgeRunTimingTest.setUp(self)
        self.bridge = None
        self.store.add("contacts", Contact(2, "Outside selection", ("+15559876543",), ()))
        self.store.add("calls", Interaction(2, "call", "call", "2026-09-01T00:00:00Z",
                                           "outgoing", ("+15559876543",), 20))
        self.capture = replace(self.capture,
            contacts=SourceResult(2, self.store.collection("contacts"), 0),
            calls=SourceResult(2, self.store.collection("calls"), 0))
        self.clock = Clock()
        self.native = NativeRun(CLIENT, now=lambda: self.clock.time, monotonic=lambda: self.clock.seconds)
        self.authority = SyntheticAuthority(self.native)

    def tearDown(self):
        directory = self.store.path.parent
        try:
            if self.bridge is not None:
                self.bridge.close()
                self.bridge.timer.join(timeout=2)
        finally:
            timing.BridgeRunTimingTest.tearDown(self)
        self.assertFalse(directory.exists())
        if self.bridge is not None:
            self.assertTrue(self.bridge.closed)
            self.assertFalse(self.bridge.thread.is_alive())
            self.assertFalse(self.bridge.timer.is_alive())
            self.assertFalse(self.bridge.transfer.path.exists())
            self.assertEqual(self.bridge.httpd.socket.fileno(), -1)

    def complete(self, operation):
        # The wrapper forwards native authority before and after its async callback.
        if self.bridge is not None:
            self.assertTrue(self.bridge.set_run_authority(self.bridge.handoff_id, self.native.authority))
        self.clock.advance(1)
        result = self.native.complete(operation, self.authority.post(operation))
        if self.bridge is not None:
            self.assertTrue(self.bridge.set_run_authority(self.bridge.handoff_id, self.native.authority))
        return result

    def http(self, method, path, body=None, *, token=None, expected=200):
        status, _, raw = test_bridge.BridgeTest.request(self, self.bridge, method, path, body,
            origin=ACCOUNT_ORIGIN, authorization=f"Bearer {token}" if token else None)
        self.assertEqual(status, expected, (method, path, raw))
        return json.loads(raw) if raw else None

    def assert_unreleased(self):
        self.assertIsNone(self.bridge.token)
        for category in ("contacts", "calls", "messages"):
            self.http("GET", f"/v2/source/{category}?cursor=0", token=CAPABILITY, expected=410)

    def retry_lost(self, operation, expected_phase, result=None):
        self.assertIsNone(self.complete(operation))
        self.assertEqual(self.native.phase, expected_phase)
        self.assertEqual(self.native.authority, "paused")
        retry = self.native.retry()
        self.assertEqual((retry.path, retry.body), (operation.path, operation.body))
        self.assertNotEqual(retry.token, operation.token)
        self.assertEqual(self.complete(retry), result)

    def test_native_approval_to_real_bridge_release_and_saved_receipt(self):
        self.retry_lost(self.native.claim_link(LINK_TEXT), "claim_uncertain")
        self.assertEqual(self.native.claim.scope.email, FIXTURE["claimResponse"]["destinationEmail"])
        with self.assertRaises(SetupProtocolError):
            self.native.approve()
        self.native.agree(True)
        self.retry_lost(self.native.approve(), "approval_uncertain")
        self.assertEqual(json.loads(self.authority.committed["approve"])["disclosure"]["version"],
                         "phone-run-windows-v1")
        self.assertFalse(self.native.started)
        with self.assertRaises(SetupProtocolError):
            self.native.continue_review((1,))
        with self.assertRaises(SetupProtocolError):
            self.native.start()
        # RC25 requires the current prepared run's read-only encryption check
        # before Start. Keep that gate in the end-to-end authority/bridge case,
        # rather than bypassing it with a stale pre-RC25 fixture.
        self.native.check_backup_setting()
        self.native.checked_backup_setting(True)
        self.retry_lost(self.native.start(), "start_uncertain", "start")
        self.assertTrue(self.native.started)
        self.assertEqual(self.native.call_history_preparation, "confirmedEncryptedBackup")

        selection = (1,)
        continued = self.native.continue_review(selection)
        self.assertIsNone(self.complete(continued))
        with self.assertRaises(SetupProtocolError):
            self.native.continue_review((2,))
        retry = self.native.retry()
        self.assertEqual(retry.body, continued.body)
        self.assertEqual(self.complete(retry), "pair")
        selected = self.store.append_selection(OTHER_ID, 0, list(self.native.continued_selection), 1)
        self.assertEqual(selected.sha256, hashlib.sha256(b"1\n").hexdigest())
        self.bridge = BridgeServer(self.capture, selected, port=0,
            now=lambda: self.clock.seconds, account_proof=self.authority.account_proof)
        self.bridge.start()
        discovered = self.http("GET", "/v3/discover")
        self.assertEqual(set(discovered), {"format", "handoffId", "payloadSha256", "expiresInSeconds"})
        self.assertEqual(discovered["format"], "account-connect-v1")
        self.assert_unreleased()
        bind = self.complete(self.native.pair_received(discovered["handoffId"], discovered["payloadSha256"]))
        self.assertIsNone(self.complete(bind))
        self.assertEqual(self.native.phase, "bind_uncertain")
        binding = {key: discovered[key] for key in ("handoffId", "payloadSha256")}
        connect = {**binding, "intentId": INTENT, "capability": CAPABILITY}
        self.http("POST", "/v3/connect", connect, expected=409)
        self.assertFalse(self.authority.proofs)
        self.assert_unreleased()
        with self.assertRaises(SetupProtocolError):
            self.native.pair_received(OTHER_ID, "f" * 64)
        retry_bind = self.complete(self.native.retry())
        self.assertEqual((retry_bind.path, retry_bind.body), (bind.path, bind.body))
        self.assertEqual(self.complete(retry_bind), "bound")
        self.assertEqual(self.authority.binding, binding)
        for changed in ({"handoffId": OTHER_ID}, {"payloadSha256": "f" * 64}):
            self.http("POST", "/v3/connect", {**connect, **changed}, expected=409)
        self.assertFalse(self.authority.proofs)

        self.http("POST", "/v3/connect", connect)
        self.http("POST", "/v3/connect", connect)  # Lost local connect reply: identical replay.
        self.assertEqual(self.authority.proofs[0], self.authority.proofs[1])
        self.assertEqual(self.http("GET", "/v3/status", token=CAPABILITY), {"state": "pending"})
        release = {"intentId": INTENT, "capability": CAPABILITY, "releaseCode": RELEASE}
        self.http("POST", "/v3/release", release, expected=403)
        self.assert_unreleased()
        destination = self.bridge.pending_destination()
        verify = self.complete(self.native.verify(destination["intentId"], destination["email"], destination["course"]))
        self.assertEqual(self.complete(verify), "destination_verified")
        self.assertFalse(self.bridge.approve_destination(OTHER_ID, True))
        self.assertTrue(self.bridge.approve_destination(self.native.intent_id, True))
        self.http("POST", "/v3/release", release, expected=409)
        self.assertEqual(self.authority.consume_commits, 1)
        self.assert_unreleased()
        proof_count = len(self.authority.proofs)
        for changed in ({"intentId": OTHER_ID}, {"capability": WRONG_GRANT}, {"releaseCode": WRONG_GRANT}):
            self.http("POST", "/v3/release", {**release, **changed}, expected=403)
        self.assertEqual(len(self.authority.proofs), proof_count)
        released = self.http("POST", "/v3/release", release)
        replayed = self.http("POST", "/v3/release", release)  # Discarded local reply; no new token.
        self.assertEqual(replayed, released)
        self.assertEqual(self.authority.consume_commits, 1)
        consumes = [proof for action, proof in self.authority.proofs if action == "consume"]
        self.assertEqual(consumes, [{**binding, **release}] * 3)
        self.http("POST", "/v3/connect", connect, expected=410)
        token = released["token"]
        self.assertEqual(released["format"], "paged-records-v2")
        self.assertTrue(released["confirmReceiptRequired"])
        manifest = json.loads(released["manifestJson"])
        self.assertEqual(hashlib.sha256(released["manifestJson"].encode("ascii")).hexdigest(),
                         self.native.payload_sha256)
        self.assertEqual(manifest["sampleContactNames"], ["Synthetic"])

        self.authority.lose_next.add("status")
        self.assertIsNone(self.complete(self.native.refresh()))
        self.http("GET", "/v2/source/contacts?cursor=0", token=token, expected=409)
        retry_verify = self.complete(self.native.retry())
        self.assertEqual((retry_verify.path, retry_verify.body), (verify.path, verify.body))
        self.assertEqual(self.complete(retry_verify), "destination_verified")
        for category in ("contacts", "calls"):
            page = self.http("GET", f"/v2/source/{category}?cursor=0", token=token)
            rows = json.loads(page["chunk"])
            self.assertEqual([row["sourceId"] for row in rows], [1])
            self.assertNotIn("+15559876543", page["chunk"])
            page_hash = hashlib.sha256(page["chunk"].encode("ascii")).hexdigest()
            self.assertEqual(page["pageSha256"], page_hash)
            self.assertIsNone(page["nextCursor"])
            source = manifest["sources"][category]
            self.assertEqual((source["rowsSeen"], source["rowsIncluded"], source["pageCount"]), (2, 1, 1))
            self.assertEqual(source["chainSha256"], hashlib.sha256((EMPTY_CHAIN + page_hash).encode("ascii")).hexdigest())
        self.http("POST", "/v1/complete", token=token)
        self.assertEqual(self.bridge.handoff_state(), "received")
        self.assertEqual(self.native.phase, "bound")
        self.assertIsNotNone(self.native.run)
        self.http("POST", "/v1/confirm-ack-received", {**binding, "received": True}, token=token, expected=409)
        for key, value in (("handoffId", OTHER_ID), ("payloadSha256", "f" * 64)):
            self.http("POST", "/v1/acknowledge-save", {**binding, key: value, "saved": True}, token=token, expected=409)
        self.http("POST", "/v1/acknowledge-save", {**binding, "saved": True}, token=token)
        self.assertEqual(self.bridge.handoff_state(), "saved_pending_browser_receipt")
        self.assertIsNotNone(self.native.run)
        self.http("POST", "/v1/confirm-ack-received", {**binding, "received": True}, token=token)
        self.assertEqual(self.bridge.handoff_state(), "saved")
        self.native.record_saved(binding["handoffId"])
        self.assertEqual(self.native.phase, "complete")
        self.assertEqual((self.native.completion.handoff_id, self.native.completion.payload_sha256),
                         (binding["handoffId"], binding["payloadSha256"]))
        for name in ("claim_request", "claim", "approval_request", "run", "pending", "start_body", "bind_body", "continued_selection"):
            self.assertIsNone(getattr(self.native, name), name)
        self.assertLess(self.clock.seconds, 60)


if __name__ == "__main__":
    unittest.main()
