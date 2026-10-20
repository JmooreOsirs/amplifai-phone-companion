"""Bounded authority refresh using synthetic rows and real loopback HTTP only."""
from __future__ import annotations

import json
import unittest

from amplifai_phone.bridge import BridgeServer
import test_bridge
import test_bridge_recovery as recovery
import test_bridge_run_timing as timing
from test_bridge_run_timing import INTENT


class BridgeRefreshTest(unittest.TestCase):
    request = test_bridge.BridgeTest.request
    setUp = timing.BridgeRunTimingTest.setUp
    tearDown = timing.BridgeRunTimingTest.tearDown
    bridge = recovery.BridgeRecoveryTest.bridge
    connect = recovery.BridgeRecoveryTest.connect
    release = recovery.BridgeRecoveryTest.release
    authority = recovery.BridgeRecoveryTest.authority

    def released(self, tick):
        self.proofs = []
        bridge = self.bridge(tick, self.authority)
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertTrue(bridge.approve_destination(INTENT, True))
        status, _, raw = self.release(bridge)
        self.assertEqual(status, 200)
        return bridge, "Bearer " + json.loads(raw)["token"]

    def page(self, bridge, authorization, category="contacts", cursor=0, **options):
        status, _, raw = self.request(
            bridge, "GET", f"/v2/source/{category}?cursor={cursor}",
            authorization=authorization, **options,
        )
        return status, json.loads(raw)

    def refreshing(self, bridge, category="contacts", cursor=0):
        return 409, {
            "code": "phone_run_authority_refreshing",
            "handoffId": bridge.handoff_id,
            "payloadSha256": bridge.payload_sha256,
            "category": category,
            "cursor": cursor,
            "retryAfterMs": 250,
        }

    def test_refresh_retries_preserve_exact_transfer_and_resume_same_page(self):
        tick = [0.0]
        bridge, token = self.released(tick)
        deadline = bridge.transfer_expires_at
        dispositions = dict(bridge.source_dispositions)
        tick[0] = 10.0
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        for moment in (10.0, 10.25, 30.0, 74.99):
            tick[0] = moment
            self.assertEqual(self.page(bridge, token), self.refreshing(bridge))
            self.assertEqual(bridge.source_cursors["contacts"], 0)
            self.assertEqual(bridge.source_dispositions, dispositions)
            self.assertEqual(bridge.transfer_expires_at, deadline)
            self.assertEqual("Bearer " + bridge.token, token)
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "current"))
        first = self.page(bridge, token)
        self.assertEqual(first[0], 200)
        self.assertEqual(bridge.source_cursors["contacts"], 1)
        deadline = bridge.transfer_expires_at
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertEqual(self.page(bridge, token), self.refreshing(bridge))
        self.assertEqual(bridge.source_cursors["contacts"], 1)
        self.assertEqual(bridge.transfer_expires_at, deadline)
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "current"))
        self.assertEqual(self.page(bridge, token), first)

    def test_refresh_window_is_fixed_and_expiry_cannot_be_rearmed(self):
        tick = [10.0]
        bridge, token = self.released(tick)
        deadline = bridge.transfer_expires_at
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        tick[0] = 74.99
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertEqual(self.page(bridge, token), self.refreshing(bridge))
        tick[0] = 75.0
        self.assertFalse(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertEqual(self.page(bridge, token), (409, {}))
        tick[0] = 100.0
        self.assertFalse(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertEqual(self.page(bridge, token), (409, {}))
        self.assertEqual(bridge.transfer_expires_at, deadline)
        self.assertEqual(bridge.source_cursors["contacts"], 0)
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "current"))
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        tick[0] = 164.99
        self.assertEqual(self.page(bridge, token), self.refreshing(bridge))
        tick[0] = 165.0
        self.assertEqual(self.page(bridge, token), (409, {}))

    def test_only_valid_authenticated_pages_receive_retryable_response(self):
        tick = [0.0]
        bridge, token = self.released(tick)
        binding = {"handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256}
        self.assertEqual(self.request(bridge, "POST", "/v2/decline",
                                      {**binding, "category": "calls"}, authorization=token)[0], 200)
        deadline = bridge.transfer_expires_at
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        for authorization, category, cursor, options, expected in (
            (None, "contacts", 0, {}, 403),
            ("Bearer wrong", "contacts", 0, {}, 403),
            (token, "contacts", 0, {"origin": "https://untrusted.example"}, 403),
            (token, "contacts", 0, {"origin": None}, 403),
            (token, "contacts", 0, {"host": "localhost"}, 403),
            (token, "calls", 0, {}, 410),
            (token, "messages", 0, {}, 409),
            (token, "contacts", 1, {}, 409),
            (token, "unknown", 0, {}, 400),
            (token, "contacts", "00", {}, 400),
        ):
            with self.subTest(category=category, cursor=cursor, expected=expected, options=options):
                self.assertEqual(self.page(bridge, authorization, category, cursor, **options), (expected, {}))
        self.assertEqual(self.page(bridge, token), self.refreshing(bridge))
        self.assertEqual(bridge.source_cursors["contacts"], 0)
        self.assertEqual(bridge.transfer_expires_at, deadline)

    def test_transfer_expiry_overrides_live_refresh_window(self):
        tick = [0.0]
        bridge, token = self.released(tick)
        deadline = bridge.transfer_expires_at
        tick[0] = deadline - 1
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertEqual(self.page(bridge, token), self.refreshing(bridge))
        tick[0] = deadline
        self.assertEqual(self.page(bridge, token), (410, {}))
        self.assertEqual(bridge.transfer_expires_at, deadline)
        self.assertEqual(bridge.source_cursors["contacts"], 0)

    def test_stop_and_wrong_generation_cannot_start_or_revive_refresh(self):
        bridge, token = self.released([0.0])
        self.assertFalse(bridge.set_run_authority("wrong", "refreshing"))
        self.assertFalse(bridge.set_run_authority(bridge.handoff_id, "revoked"))
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "paused"))
        self.assertFalse(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertEqual(self.page(bridge, token), (409, {}))
        self.assertIsNone(bridge.close_for_repair())
        self.assertFalse(bridge.set_run_authority(bridge.handoff_id, "current"))
        self.assertFalse(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        replacement, new_token = self.released([0.0])
        self.assertNotEqual(replacement.handoff_id, bridge.handoff_id)
        self.assertEqual(replacement.payload_sha256, bridge.payload_sha256)
        self.assertFalse(replacement.set_run_authority(bridge.handoff_id, "current"))
        self.assertTrue(replacement.set_run_authority(replacement.handoff_id, "refreshing"))
        self.assertEqual(self.page(replacement, token), (403, {}))
        self.assertEqual(self.page(replacement, new_token), self.refreshing(replacement))

    def test_complete_and_receipt_retries_remain_idempotent_during_refresh(self):
        bridge, token = self.released([0.0])
        for category in ("contacts", "calls"):
            self.assertEqual(self.page(bridge, token, category)[0], 200)
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        for _ in range(2):
            self.assertEqual(self.request(bridge, "POST", "/v1/complete", authorization=token)[0], 200)
        self.assertEqual(self.page(bridge, token), (410, {}))
        binding = {"handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256}
        for endpoint, field in (("acknowledge-save", "saved"), ("confirm-ack-received", "received")):
            for _ in range(2):
                self.assertEqual(self.request(bridge, "POST", "/v1/" + endpoint,
                                              {**binding, field: True}, authorization=token)[0], 200)
        self.assertEqual(bridge.handoff_state(), "saved")
        self.assertEqual(bridge.close_for_repair(), "saved")

    def test_refresh_does_not_make_connect_approval_or_release_retryable(self):
        self.proofs = []
        bridge = self.bridge([0.0], self.authority)
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        status, _, raw = self.connect(bridge)
        self.assertEqual((status, json.loads(raw)), (409, {}))
        self.assertEqual(self.proofs, [])
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "current"))
        self.assertEqual(self.connect(bridge)[0], 200)
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        self.assertFalse(bridge.approve_destination(INTENT, True))
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "current"))
        self.assertTrue(bridge.approve_destination(INTENT, True))
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        status, _, raw = self.release(bridge)
        self.assertEqual((status, json.loads(raw)), (409, {}))
        self.assertIsNone(bridge.token)
        self.assertEqual(len(self.proofs), 1)

    def test_legacy_metadata_keeps_untyped_refresh_conflict(self):
        bridge = BridgeServer(test_bridge.fixture(), {1}, port=0)
        bridge.start()
        self.addCleanup(bridge.close)
        status, _, raw = self.request(bridge, "POST", "/v1/pair", {"code": bridge.code})
        self.assertEqual(status, 200)
        token = "Bearer " + json.loads(raw)["token"]
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "refreshing"))
        status, _, raw = self.request(bridge, "GET", "/v1/metadata", authorization=token)
        self.assertEqual((status, json.loads(raw)), (409, {}))
        self.assertFalse(bridge.used)
        self.assertTrue(bridge.set_run_authority(bridge.handoff_id, "current"))
        self.assertEqual(self.request(bridge, "GET", "/v1/metadata", authorization=token)[0], 200)


if __name__ == "__main__":
    unittest.main()
