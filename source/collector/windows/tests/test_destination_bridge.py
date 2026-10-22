"""Windows destination state against the shared bridge, using synthetic loopback only."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amplifai_phone.bridge import BridgeServer
from amplifai_phone.agent import _contact_page, review_snapshot
import test_bridge
import test_bridge_run_timing as timing
from test_session import inspected

ORIGINAL = "22222222-2222-4222-8222-222222222222"
REPLACEMENT = "33333333-3333-4333-8333-333333333333"
CAPABILITY = "A" * 43
RELEASE = "B" * 43


class DestinationBridgeTests(unittest.TestCase):
    request = test_bridge.BridgeTest.request
    setUp = timing.BridgeRunTimingTest.setUp

    def tearDown(self):
        try:
            if hasattr(self, "bridge"):
                self.bridge.close()
        finally:
            timing.BridgeRunTimingTest.tearDown(self)

    def start(self):
        self.allow_proof = True
        self.proofs = []

        def proof(action, command):
            self.proofs.append((action, dict(command)))
            if not self.allow_proof:
                return None
            return ({"destinationEmail": "owner@example.test", "destinationCourse": "Synthetic course"}
                    if action == "claim" else {"consumed": True})

        self.bridge = BridgeServer(self.capture, {1}, port=0, account_proof=proof)
        self.bridge.start()
        self.state = inspected()
        self.state.approve(True)
        self.state.begin("connect")
        self.state.handle({"kind": "capture", **_contact_page(self.capture.contacts.records, "", 0),
                           "availableCalls": len(self.capture.calls.records),
                           "availableMessages": len(self.capture.messages.records), "missing": []})
        self.state.update_visible_selection([1])
        command = self.state.review([1])
        snapshot = self.store.append_selection(command["reviewId"], 0, [1], 1)
        self.state.handle({"kind": "review-page", "reviewId": command["reviewId"], "nextCursor": 1})
        self.state.handle(json.loads(json.dumps({"kind": "review", "reviewId": command["reviewId"],
                                                **review_snapshot(self.capture, snapshot)})))
        self.state.pair()
        # Port 0 keeps this fixture away from any running customer companion.
        with patch("session.BRIDGE_PORT", self.bridge.port):
            self.state.handle({"kind": "pairing", "pairCode": self.bridge.code,
                               "port": self.bridge.port, "expiresInSeconds": 300,
                               "handoffId": self.bridge.handoff_id,
                               "payloadSha256": self.bridge.payload_sha256})

    def connect(self, intent):
        return self.request(self.bridge, "POST", "/v3/connect", {
            "intentId": intent, "capability": CAPABILITY,
            "handoffId": self.bridge.handoff_id, "payloadSha256": self.bridge.payload_sha256,
        })

    def release(self, intent):
        return self.request(self.bridge, "POST", "/v3/release", {
            "intentId": intent, "capability": CAPABILITY, "releaseCode": RELEASE,
        })

    def poll(self):
        self.assertIsNotNone(self.state.poll())
        self.state.handle({"kind": "handoff", "state": self.bridge.handoff_state(),
                           "handoffId": self.bridge.handoff_id})
        pending = self.bridge.pending_destination()
        if pending is not None:
            self.state.handle({"kind": "destination", "state": "pending",
                               "handoffId": self.bridge.handoff_id, **pending})

    def approve(self):
        command = self.state.decide_destination(True)
        self.assertEqual(command["handoffId"], self.bridge.handoff_id)
        self.assertTrue(self.bridge.approve_destination(command["intentId"], command["approved"]))
        self.state.handle({"kind": "destination", "state": "approved",
                           "handoffId": self.bridge.handoff_id, "intentId": command["intentId"]})

    def test_verified_replacement_requires_fresh_approval_and_exact_two_receipts(self):
        self.start()
        self.assertEqual(self.connect(ORIGINAL)[0], 200)
        self.poll()
        self.approve()
        self.assertEqual(self.connect(REPLACEMENT)[0], 200)
        self.poll()
        self.assertEqual(self.state.destination_intent_id, REPLACEMENT)
        self.assertFalse(self.state.destination_approved)
        self.assertIsNone(self.state.destination_requested_approval)
        self.assertFalse(self.bridge.approve_destination(ORIGINAL, True))
        self.assertEqual(self.release(ORIGINAL)[0], 403)
        self.assertEqual(self.release(REPLACEMENT)[0], 403)
        self.approve()
        status, _, raw = self.release(REPLACEMENT)
        self.assertEqual(status, 200)
        authorization = "Bearer " + json.loads(raw)["token"]
        for category in ("contacts", "calls"):
            self.assertEqual(self.request(self.bridge, "GET", f"/v2/source/{category}?cursor=0",
                                          authorization=authorization)[0], 200)
        self.assertEqual(self.request(self.bridge, "POST", "/v1/complete", authorization=authorization)[0], 200)
        self.poll()
        self.assertFalse(self.state.saved_acknowledged)
        binding = {"handoffId": self.bridge.handoff_id, "payloadSha256": self.bridge.payload_sha256}
        self.assertEqual(self.request(self.bridge, "POST", "/v1/acknowledge-save",
                                      {**binding, "payloadSha256": "0" * 64, "saved": True},
                                      authorization=authorization)[0], 409)
        self.assertEqual(self.request(self.bridge, "POST", "/v1/acknowledge-save", {**binding, "saved": True},
                                      authorization=authorization)[0], 200)
        self.poll()
        self.assertTrue(self.state.save_receipt_pending)
        self.assertFalse(self.state.saved_acknowledged)
        self.assertEqual(self.request(self.bridge, "POST", "/v1/confirm-ack-received",
                                      {**binding, "received": True}, authorization=authorization)[0], 200)
        self.poll()
        self.assertEqual(self.state.finish(), {"action": "finish", "handoffId": self.bridge.handoff_id})

    def test_superseded_decision_is_rejected_without_stopping_local_review(self):
        self.start()
        self.assertEqual(self.connect(ORIGINAL)[0], 200)
        self.poll()
        command = self.state.decide_destination(True)
        self.assertEqual(self.connect(REPLACEMENT)[0], 200)
        self.assertFalse(self.bridge.approve_destination(command["intentId"], command["approved"]))
        self.state.handle({"kind": "error", "code": "handoff_unconfirmed"})
        self.poll()
        self.assertEqual(self.state.phase, "reviewing")
        self.assertEqual(self.state.destination_intent_id, REPLACEMENT)
        self.assertTrue(self.state.can_decide_destination)
        self.assertFalse(self.state.destination_approved)
        self.assertEqual(self.release(REPLACEMENT)[0], 403)

    def test_unverified_replacement_and_consumed_grant_preserve_current_destination(self):
        self.start()
        self.assertEqual(self.connect(ORIGINAL)[0], 200)
        self.poll()
        self.approve()
        self.allow_proof = False
        self.assertEqual(self.connect(REPLACEMENT)[0], 409)
        self.poll()
        self.assertEqual(self.state.destination_intent_id, ORIGINAL)
        self.assertTrue(self.state.destination_approved)
        self.allow_proof = True
        status, _, raw = self.release(ORIGINAL)
        self.assertEqual(status, 200)
        token = json.loads(raw)["token"]
        status, _, replay = self.release(ORIGINAL)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(replay)["token"], token)
        self.assertEqual(self.proofs[-1], self.proofs[-2])
        self.assertEqual(self.connect(REPLACEMENT)[0], 410)
        self.poll()
        self.assertEqual(self.state.destination_intent_id, ORIGINAL)
        self.assertTrue(self.state.destination_approved)
        self.assertFalse(self.state.saved_acknowledged)


if __name__ == "__main__":
    unittest.main()
