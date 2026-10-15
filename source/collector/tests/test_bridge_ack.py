"""Synthetic loopback proof only: browser receipt assertion is not cloud-save proof."""
from __future__ import annotations

import hashlib
import http.client
import json
import threading
import unittest
from uuid import UUID

from amplifai_phone.bridge import PILOT_ORIGIN, BridgeServer
from test_bridge import fixture


class SavedAcknowledgementTest(unittest.TestCase):
    def test_local_repair_and_first_receipt_share_an_atomic_boundary(self):
        bridge = BridgeServer(fixture(), {1}, port=0)
        bridge.start()
        outcome = []
        entered = threading.Event()
        try:
            def repair() -> None:
                entered.set()
                outcome.append(bridge.close_for_repair())

            with bridge.lock:
                worker = threading.Thread(target=repair)
                worker.start()
                self.assertTrue(entered.wait(1))
                bridge.acknowledged_saved = True
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(outcome, ["saved_pending_browser_receipt"])
            self.assertFalse(bridge.closed)
        finally:
            bridge.close()

        unsaved = BridgeServer(fixture(), {1}, port=0)
        unsaved.start()
        try:
            self.assertIsNone(unsaved.close_for_repair())
            self.assertTrue(unsaved.closed)
        finally:
            unsaved.close()

    def request(self, bridge, path, body=None, token=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=2)
        headers = {"Host": f"127.0.0.1:{bridge.port}", "Origin": origin or PILOT_ORIGIN}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request("GET" if path == "/v1/metadata" else "POST", path,
                           json.dumps(body).encode() if body is not None else None, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_delivery_is_not_saved_and_ack_is_bound_authenticated_and_retryable(self):
        bridge = BridgeServer(fixture(), {1}, port=0)
        bridge.start()
        try:
            status, pairing = self.request(bridge, "/v1/pair", {"code": bridge.code})
            self.assertEqual(status, 200)
            self.assertEqual(str(UUID(pairing["handoffId"])), bridge.handoff_id)
            self.assertEqual(pairing["payloadSha256"], hashlib.sha256(bridge.payload).hexdigest())
            body = {"handoffId": pairing["handoffId"], "payloadSha256": pairing["payloadSha256"], "saved": True}
            token = pairing["token"]
            self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, token)[0], 409)
            self.assertEqual(self.request(bridge, "/v1/metadata", token=token)[0], 200)
            self.assertEqual(bridge.handoff_state(), "received")
            self.assertFalse(bridge.acknowledged_saved)
            self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, "wrong")[0], 403)
            self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, token, "https://evil.test")[0], 403)
            for field, value in (("handoffId", "00000000-0000-4000-8000-000000000000"), ("payloadSha256", "0" * 64)):
                self.assertEqual(self.request(bridge, "/v1/acknowledge-save", {**body, field: value}, token)[0], 409)
            for invalid in ({**body, "saved": "true"}, {**body, "extra": True}, {"saved": True}):
                self.assertEqual(self.request(bridge, "/v1/acknowledge-save", invalid, token)[0], 400)
            self.assertFalse(bridge.acknowledged_saved)
            confirmation = {"handoffId": pairing["handoffId"], "payloadSha256": pairing["payloadSha256"], "received": True}
            self.assertEqual(self.request(bridge, "/v1/confirm-ack-received", confirmation, token)[0], 409)
            for _ in range(2):
                self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, token), (200, {"acknowledged": True}))
            self.assertEqual(bridge.handoff_state(), "saved_pending_browser_receipt")
            self.assertEqual(bridge.close_for_repair(), "saved_pending_browser_receipt")
            self.assertFalse(bridge.closed)
            self.assertEqual(self.request(bridge, "/v1/confirm-ack-received", confirmation, "wrong")[0], 403)
            self.assertEqual(self.request(bridge, "/v1/confirm-ack-received", {**confirmation, "received": False}, token)[0], 400)
            self.assertEqual(self.request(bridge, "/v1/confirm-ack-received", confirmation, token), (200, {"received": True}))
            self.assertEqual(bridge.handoff_state(), "saved")
            self.assertEqual(bridge.close_for_repair(), "saved")
            self.assertFalse(bridge.closed)
        finally:
            bridge.close()

    def test_completed_transfer_can_be_acknowledged_after_pair_code_expires(self):
        tick = [100.0]
        bridge = BridgeServer(fixture(), {1}, port=0, now=lambda: tick[0])
        bridge.start()
        try:
            _, pairing = self.request(bridge, "/v1/pair", {"code": bridge.code})
            self.request(bridge, "/v1/metadata", token=pairing["token"])
            tick[0] += 301
            body = {"handoffId": pairing["handoffId"], "payloadSha256": pairing["payloadSha256"], "saved": True}
            self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, pairing["token"])[0], 200)
            self.assertTrue(bridge.acknowledged_saved)
            self.assertEqual(bridge.handoff_state(), "saved_pending_browser_receipt")
            confirmation = {"handoffId": pairing["handoffId"], "payloadSha256": pairing["payloadSha256"], "received": True}
            self.assertEqual(self.request(bridge, "/v1/confirm-ack-received", confirmation, pairing["token"])[0], 200)
            self.assertEqual(bridge.handoff_state(), "saved")
        finally:
            bridge.close()

    def test_expired_untransferred_pair_cannot_be_used_for_saved_ack(self):
        tick = [100.0]
        bridge = BridgeServer(fixture(), {1}, port=0, now=lambda: tick[0])
        bridge.start()
        try:
            _, pairing = self.request(bridge, "/v1/pair", {"code": bridge.code})
            tick[0] += 301
            body = {"handoffId": pairing["handoffId"], "payloadSha256": pairing["payloadSha256"], "saved": True}
            self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, pairing["token"])[0], 410)
            self.assertFalse(bridge.acknowledged_saved)
        finally:
            bridge.close()

    def test_pair_timer_does_not_close_an_already_transferred_review(self):
        bridge = BridgeServer(fixture(), {1}, port=0)
        bridge.start()
        try:
            _, pairing = self.request(bridge, "/v1/pair", {"code": bridge.code})
            self.assertEqual(self.request(bridge, "/v1/metadata", token=pairing["token"])[0], 200)
            bridge._expire_unclaimed()
            self.assertFalse(bridge.closed)
            body = {"handoffId": pairing["handoffId"], "payloadSha256": pairing["payloadSha256"], "saved": True}
            self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, pairing["token"])[0], 200)
        finally:
            bridge.close()

    def test_pair_timer_still_closes_an_untransferred_review(self):
        bridge = BridgeServer(fixture(), {1}, port=0)
        bridge.start()
        bridge._expire_unclaimed()
        self.assertTrue(bridge.closed)
        self.assertEqual(bridge.handoff_state(), "expired")
