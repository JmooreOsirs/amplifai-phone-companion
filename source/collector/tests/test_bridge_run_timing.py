"""Timing of the unchanged bridge for the one-approval phone run (synthetic only).

The real BridgeServer with an injected clock, a synthetic paged capture, an ephemeral port (0) and a fake account
proof: no device, helper process, network or account. It shows that the five-minute window starts at pair (so a
long collection before it never matters), and that a released paged transfer can take far longer than five minutes
under the two-hour inactivity window refreshed by each page.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from amplifai_phone.bridge import PAIR_SECONDS, TRANSFER_SECONDS, BridgeServer
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace
import test_bridge

INTENT = "11111111-1111-4111-8111-111111111111"
CAPABILITY = "A" * 43
RELEASE = "B" * 43


def account_proof(action: str, _command: dict[str, str]) -> dict[str, object]:
    return ({"destinationEmail": "owner@example.test", "destinationCourse": "Synthetic course"}
            if action == "claim" else {"consumed": True})


class BridgeRunTimingTest(unittest.TestCase):
    # Reuse the existing request helper without collecting BridgeTest's own tests here.
    request = test_bridge.BridgeTest.request

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="amplifai-run-timing-test-")
        self.workspace = SessionWorkspace(Path(self.temporary.name) / "sessions")
        directory = self.workspace.__enter__()
        self.store = RecordStore(directory / "sanitized.sqlite3")
        self.store.add("contacts", Contact(1, "Synthetic", ("+15551234567",), ()))
        self.store.add("calls", Interaction(1, "call", "call", "2026-09-01T00:00:00Z", "incoming", ("+15551234567",), 30))
        self.capture = IPhoneCapture(SourceResult(1, self.store.collection("contacts"), 0),
                                     SourceResult(1, self.store.collection("calls"), 0),
                                     SourceResult(0, self.store.collection("messages"), 0),
                                     "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (), self.workspace, self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.workspace.__exit__(None, None, None)
        self.temporary.cleanup()

    def paired(self, tick: list[float]) -> BridgeServer:
        """`pair` after review: the bridge (and its five-minute window) is created at the current clock."""
        bridge = BridgeServer(self.capture, {1}, port=0, now=lambda: tick[0], account_proof=account_proof)
        bridge.start()
        return bridge

    def connect_and_approve(self, bridge: BridgeServer) -> None:
        command = {"intentId": INTENT, "capability": CAPABILITY,
                   "handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256}
        self.assertEqual(self.request(bridge, "POST", "/v3/connect", command)[0], 200)
        self.assertTrue(bridge.approve_destination(INTENT, True))

    def release(self, bridge: BridgeServer) -> tuple[int, bytes]:
        status, _, body = self.request(bridge, "POST", "/v3/release",
                                       {"intentId": INTENT, "capability": CAPABILITY, "releaseCode": RELEASE})
        return status, body

    def test_window_starts_at_pair_and_release_must_follow_within_five_minutes(self) -> None:
        self.assertEqual(PAIR_SECONDS, 300)
        # A 35-minute collection happened before pair; the clock is already far past five minutes from any claim.
        tick = [35 * 60.0]
        bridge = self.paired(tick)
        try:
            self.assertEqual(bridge.expires_at, 35 * 60.0 + 300)
            tick[0] += 10
            self.connect_and_approve(bridge)
            tick[0] = 35 * 60.0 + 299
            status, body = self.release(bridge)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["format"], "paged-records-v2")
            self.assertEqual(bridge.handoff_state(), "waiting")
        finally:
            bridge.close()
        tick = [0.0]
        late = self.paired(tick)
        try:
            tick[0] = 10
            self.connect_and_approve(late)
            tick[0] = 301
            self.assertEqual(self.release(late)[0], 410)
            self.assertEqual(late.handoff_state(), "expired")
        finally:
            late.close()

    def test_released_transfer_over_fifteen_minutes_completes_and_acknowledges(self) -> None:
        self.assertEqual(TRANSFER_SECONDS, 7200)
        tick = [0.0]
        bridge = self.paired(tick)
        try:
            tick[0] = 30
            self.connect_and_approve(bridge)
            tick[0] = 240
            status, body = self.release(bridge)
            self.assertEqual(status, 200)
            token = f"Bearer {json.loads(body)['token']}"
            tick[0] = 240 + 5 * 60
            self.assertEqual(self.request(bridge, "GET", "/v2/source/contacts?cursor=0", authorization=token)[0], 200)
            self.assertEqual(bridge.handoff_state(), "waiting")
            tick[0] = 240 + 10 * 60
            self.assertEqual(self.request(bridge, "POST", "/v2/keepalive", authorization=token)[0], 200)
            tick[0] = 240 + 15 * 60
            self.assertEqual(self.request(bridge, "GET", "/v2/source/calls?cursor=0", authorization=token)[0], 200)
            self.assertEqual(bridge.handoff_state(), "waiting")
            self.assertEqual(self.request(bridge, "POST", "/v1/complete", authorization=token)[0], 200)
            self.assertEqual(bridge.handoff_state(), "received")
            binding = {"handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256}
            self.assertEqual(self.request(bridge, "POST", "/v1/acknowledge-save", {**binding, "saved": True},
                                          authorization=token)[0], 200)
            self.assertEqual(bridge.handoff_state(), "saved_pending_browser_receipt")
            self.assertEqual(self.request(bridge, "POST", "/v1/confirm-ack-received", {**binding, "received": True},
                                          authorization=token)[0], 200)
            self.assertEqual(bridge.handoff_state(), "saved")
        finally:
            bridge.close()

    def test_released_transfer_expires_after_two_hours_without_activity(self) -> None:
        tick = [0.0]
        bridge = self.paired(tick)
        try:
            tick[0] = 30
            self.connect_and_approve(bridge)
            tick[0] = 60
            status, body = self.release(bridge)
            self.assertEqual(status, 200)
            token = f"Bearer {json.loads(body)['token']}"
            tick[0] = 60 + 3600
            self.assertEqual(self.request(bridge, "GET", "/v2/source/contacts?cursor=0", authorization=token)[0], 200)
            tick[0] = 60 + 3600 + 7199
            self.assertEqual(bridge.handoff_state(), "waiting")
            tick[0] = 60 + 3600 + 7201
            self.assertEqual(bridge.handoff_state(), "expired")
            self.assertEqual(self.request(bridge, "GET", "/v2/source/calls?cursor=0", authorization=token)[0], 410)
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
