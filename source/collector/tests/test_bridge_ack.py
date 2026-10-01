"""Synthetic loopback proof only: browser receipt assertion is not cloud-save proof."""
from __future__ import annotations

import hashlib
import http.client
import json
import unittest
from uuid import UUID

from amplifai_phone.bridge import BridgeServer
from test_bridge import fixture


class SavedAcknowledgementTest(unittest.TestCase):
    def request(self, bridge, path, body=None, token=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=2)
        headers = {"Host": f"127.0.0.1:{bridge.port}", "Origin": origin or bridge.origin}
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
            for _ in range(2):
                self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, token), (200, {"acknowledged": True}))
            self.assertEqual(bridge.handoff_state(), "saved")
        finally:
            bridge.close()

    def test_expired_binding_cannot_be_acknowledged(self):
        tick = [100.0]
        bridge = BridgeServer(fixture(), {1}, port=0, now=lambda: tick[0])
        bridge.start()
        try:
            _, pairing = self.request(bridge, "/v1/pair", {"code": bridge.code})
            self.request(bridge, "/v1/metadata", token=pairing["token"])
            tick[0] += 301
            body = {"handoffId": pairing["handoffId"], "payloadSha256": pairing["payloadSha256"], "saved": True}
            self.assertEqual(self.request(bridge, "/v1/acknowledge-save", body, pairing["token"])[0], 410)
            self.assertFalse(bridge.acknowledged_saved)
            self.assertEqual(bridge.handoff_state(), "expired")
        finally:
            bridge.close()
