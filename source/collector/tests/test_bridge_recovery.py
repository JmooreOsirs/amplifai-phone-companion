"""Lost-response recovery using synthetic rows and real loopback HTTP only."""
from __future__ import annotations

import json
import unittest

from amplifai_phone.bridge import BridgeServer, TRANSFER_SECONDS
import test_bridge
import test_bridge_run_timing as timing
from test_bridge_run_timing import CAPABILITY, INTENT, RELEASE


class BridgeRecoveryTest(unittest.TestCase):
    request = test_bridge.BridgeTest.request
    setUp = timing.BridgeRunTimingTest.setUp
    tearDown = timing.BridgeRunTimingTest.tearDown

    def bridge(self, tick, proof):
        bridge = BridgeServer(self.capture, {1}, port=0, now=lambda: tick[0], account_proof=proof)
        bridge.start()
        self.addCleanup(bridge.close)
        return bridge

    def connect(self, bridge, **changes):
        return self.request(bridge, "POST", "/v3/connect", {
            "intentId": INTENT, "capability": CAPABILITY,
            "handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256, **changes,
        })

    def release(self, bridge, **changes):
        return self.request(bridge, "POST", "/v3/release", {
            "intentId": INTENT, "capability": CAPABILITY, "releaseCode": RELEASE, **changes,
        })

    def authority(self, action, command):
        self.proofs.append((action, dict(command)))
        return ({"destinationEmail": "owner@example.test", "destinationCourse": "Synthetic course"}
                if action == "claim" else {"consumed": True})

    def test_lost_connect_reply_replays_exact_claim_without_resetting_approval(self):
        self.proofs = []
        bridge = self.bridge([0.0], self.authority)
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertEqual(bridge.destination_state, "pending")
        self.assertTrue(bridge.approve_destination(INTENT, True))
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertEqual(bridge.destination_state, "approved")
        self.assertEqual(len(self.proofs), 3)
        self.assertTrue(all(proof == self.proofs[0] for proof in self.proofs))
        self.assertEqual(self.connect(bridge, capability="X" * 43)[0], 409)
        self.assertEqual(len(self.proofs), 3)

    def test_lost_local_release_reply_replays_same_token_with_fresh_exact_proof(self):
        self.proofs = []
        tick = [0.0]
        bridge = self.bridge(tick, self.authority)
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertTrue(bridge.approve_destination(INTENT, True))
        status, _, raw = self.release(bridge)
        self.assertEqual(status, 200)
        first = json.loads(raw)
        tick[0] = 301.0  # Pair code expired, but this exact released transfer is still live.
        status, _, raw = self.release(bridge)
        self.assertEqual(status, 200)
        replay = json.loads(raw)
        self.assertEqual(replay["token"], first["token"])
        self.assertEqual(replay["manifestJson"], first["manifestJson"])
        self.assertEqual(self.proofs[-1], self.proofs[-2])
        self.assertEqual(len(self.proofs), 3)
        self.assertEqual(self.release(bridge, releaseCode="X" * 43)[0], 403)
        self.assertEqual(self.release(bridge, capability="X" * 43)[0], 403)
        self.assertEqual(len(self.proofs), 3)
        tick[0] = TRANSFER_SECONDS + 1.0
        self.assertEqual(self.release(bridge)[0], 410)

    def test_committed_consume_with_lost_authority_reply_recovers_same_proof(self):
        self.proofs = []
        committed = []

        def proof(action, command):
            result = self.authority(action, command)
            if action == "consume":
                committed.append(dict(command))
                if len(committed) == 1:
                    return None
            return result

        bridge = self.bridge([0.0], proof)
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertTrue(bridge.approve_destination(INTENT, True))
        self.assertEqual(self.release(bridge)[0], 409)
        self.assertIsNone(bridge.token)
        self.assertEqual(self.release(bridge)[0], 200)
        self.assertEqual(committed[0], committed[1])

    def test_replay_never_discloses_token_when_current_proof_is_refused(self):
        self.proofs = []
        allow = [True]

        def proof(action, command):
            result = self.authority(action, command)
            return result if allow[0] else None

        bridge = self.bridge([0.0], proof)
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertTrue(bridge.approve_destination(INTENT, True))
        status, _, raw = self.release(bridge)
        self.assertEqual(status, 200)
        token = json.loads(raw)["token"]
        allow[0] = False
        status, _, raw = self.release(bridge)
        self.assertEqual(status, 409)
        self.assertNotIn(token.encode(), raw)
        self.assertEqual(bridge.token, token)

    def test_complete_and_receipt_retries_do_not_repeat_source_delivery(self):
        self.proofs = []
        bridge = self.bridge([0.0], self.authority)
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertTrue(bridge.approve_destination(INTENT, True))
        status, _, raw = self.release(bridge)
        self.assertEqual(status, 200)
        authorization = "Bearer " + json.loads(raw)["token"]
        self.assertEqual(self.request(bridge, "POST", "/v1/complete", authorization=authorization)[0], 409)
        for category in ("contacts", "calls"):
            self.assertEqual(self.request(bridge, "GET", f"/v2/source/{category}?cursor=0",
                                          authorization=authorization)[0], 200)
        for _ in range(2):
            self.assertEqual(self.request(bridge, "POST", "/v1/complete", authorization=authorization)[0], 200)
        self.assertEqual(bridge.handoff_state(), "received")
        binding = {"handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256}
        for _ in range(2):
            self.assertEqual(self.request(bridge, "POST", "/v1/acknowledge-save", {**binding, "saved": True},
                                          authorization=authorization)[0], 200)
        self.assertEqual(bridge.handoff_state(), "saved_pending_browser_receipt")
        self.assertEqual(self.request(bridge, "POST", "/v1/complete", authorization=authorization)[0], 200)
        self.assertEqual(self.request(bridge, "GET", "/v2/source/contacts?cursor=0",
                                      authorization=authorization)[0], 410)
        for _ in range(2):
            self.assertEqual(self.request(bridge, "POST", "/v1/confirm-ack-received", {**binding, "received": True},
                                          authorization=authorization)[0], 200)
        self.assertEqual(bridge.handoff_state(), "saved")


if __name__ == "__main__":
    unittest.main()
