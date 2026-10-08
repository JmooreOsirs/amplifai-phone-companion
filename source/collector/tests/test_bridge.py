from __future__ import annotations

import http.client
import json
import unittest
from unittest.mock import patch

from amplifai_phone.bridge import BridgeError, BridgeServer, selected_metadata
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult


def fixture() -> IPhoneCapture:
    return IPhoneCapture(
        SourceResult(
            2,
            (
                Contact(1, "Ada Synthetic", ("+15551234567",), ("ada@example.test",)),
                Contact(2, "Outside Selection", ("+15559876543",), ()),
            ),
            0,
        ),
        SourceResult(
            2,
            (
                Interaction(
                    1,
                    "call",
                    "call",
                    "2026-09-01T00:00:00Z",
                    "incoming",
                    ("+15551234567",),
                    60,
                ),
                Interaction(
                    2,
                    "call",
                    "call",
                    "2026-09-02T00:00:00Z",
                    "outgoing",
                    ("+15559876543",),
                    20,
                ),
            ),
            0,
        ),
        SourceResult(0, (), 0),
        "2026-09-23T00:00:00Z",
        "2026-03-23T00:00:00Z",
        ("messages",),
    )


class BridgeTest(unittest.TestCase):
    def test_empty_selection_and_oversize_payload_fail_before_listen(self) -> None:
        with self.assertRaises(BridgeError):
            BridgeServer(fixture(), set(), port=0)
        with (
            patch("amplifai_phone.bridge.MAX_TRANSFER_BYTES", 8),
            self.assertRaises(BridgeError),
        ):
            BridgeServer(fixture(), {1}, port=0)

    def test_allowlist_contains_only_selected_metadata(self) -> None:
        payload = json.loads(selected_metadata(fixture(), {1}))
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(
            [item["name"] for item in payload["contacts"]], ["Ada Synthetic"]
        )
        self.assertEqual([item["sourceId"] for item in payload["interactions"]], [1])
        self.assertNotIn("ada@example.test", json.dumps(payload))
        self.assertNotIn("Outside Selection", json.dumps(payload))
        self.assertEqual(payload["missingSources"], ["messages"])
        self.assertEqual(payload["sourceStats"], {
            "contacts": {"rowsSeen": 2, "rowsIncluded": 1},
            "calls": {"rowsSeen": 2, "rowsIncluded": 1},
            "messages": {"rowsSeen": 0, "rowsIncluded": 0},
        })

    def test_exact_origin_host_code_and_one_use_token(self) -> None:
        bridge = BridgeServer(fixture(), {1}, port=0)
        bridge.start()
        try:
            status, _, _ = self.request(
                bridge,
                "POST",
                "/v1/pair",
                {"code": bridge.code},
                origin="https://evil.test",
            )
            self.assertEqual(status, 403)
            status, _, _ = self.request(
                bridge, "POST", "/v1/pair", {"code": bridge.code}, host="evil.test"
            )
            self.assertEqual(status, 403)
            status, _, _ = self.request(
                bridge, "POST", "/v1/pair", {"code": bridge.code}, origin=None
            )
            self.assertEqual(status, 403)
            status, _, _ = self.request(
                bridge, "POST", "/v1/pair", {"code": "x" * 1100}
            )
            self.assertEqual(status, 413)
            status, headers, body = self.request(
                bridge, "POST", "/v1/pair", {"code": bridge.code}
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers["Access-Control-Allow-Origin"], bridge.origin)
            token = json.loads(body)["token"]
            status, _, _ = self.request(
                bridge, "GET", "/v1/metadata", authorization="Bearer wrong"
            )
            self.assertEqual(status, 403)
            status, _, body = self.request(
                bridge, "GET", "/v1/metadata", authorization=f"Bearer {token}"
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["contacts"][0]["name"], "Ada Synthetic")
            status, _, _ = self.request(
                bridge, "GET", "/v1/metadata", authorization=f"Bearer {token}"
            )
            self.assertEqual(status, 410)
        finally:
            bridge.close()

    def test_large_transfer_pages_are_ordered_retryable_and_revoke_on_completion(self) -> None:
        with patch("amplifai_phone.bridge.PAGE_BYTES", 100):
            bridge = BridgeServer(fixture(), {1}, port=0)
            bridge.start()
            try:
                status, _, body = self.request(bridge, "POST", "/v1/pair", {"code": bridge.code})
                self.assertEqual(status, 200)
                token = json.loads(body)["token"]
                status, _, _ = self.request(
                    bridge, "OPTIONS", "/v1/metadata?cursor=1", requested_method="GET"
                )
                self.assertEqual(status, 204)
                status, _, _ = self.request(
                    bridge, "GET", "/v1/metadata?cursor=2", authorization=f"Bearer {token}"
                )
                self.assertEqual(status, 409)
                chunks = []
                cursor = 0
                while True:
                    path = "/v1/metadata" if cursor == 0 else f"/v1/metadata?cursor={cursor}"
                    status, _, body = self.request(bridge, "GET", path, authorization=f"Bearer {token}")
                    self.assertEqual(status, 200)
                    page = json.loads(body)
                    self.assertEqual(page["format"], "chunked-json-v1")
                    self.assertEqual(page["cursor"], cursor)
                    if cursor == 0:
                        repeated_status, _, repeated = self.request(bridge, "GET", path, authorization=f"Bearer {token}")
                        self.assertEqual(repeated_status, 200)
                        self.assertEqual(repeated, body)
                    chunks.append(page["chunk"])
                    if page["nextCursor"] is None:
                        self.assertEqual(sum(len(chunk) for chunk in chunks), page["totalBytes"])
                        break
                    cursor = page["nextCursor"]
                self.assertEqual(json.loads("".join(chunks)), json.loads(selected_metadata(fixture(), {1})))
                status, _, _ = self.request(
                    bridge, "POST", "/v1/complete", authorization=f"Bearer {token}", transfer_encoding="chunked"
                )
                self.assertEqual(status, 415)
                status, _, _ = self.request(bridge, "POST", "/v1/complete", authorization=f"Bearer {token}")
                self.assertEqual(status, 200)
                status, _, _ = self.request(bridge, "POST", "/v1/complete", authorization=f"Bearer {token}")
                self.assertEqual(status, 200)
                status, _, _ = self.request(bridge, "GET", "/v1/metadata", authorization=f"Bearer {token}")
                self.assertEqual(status, 410)
            finally:
                bridge.close()

    def test_code_attempts_and_expiry_fail_closed(self) -> None:
        tick = [100.0]
        bridge = BridgeServer(fixture(), {1}, port=0, now=lambda: tick[0])
        bridge.start()
        try:
            for _ in range(5):
                status, _, _ = self.request(
                    bridge, "POST", "/v1/pair", {"code": "wrong"}
                )
                self.assertEqual(status, 403)
            status, _, _ = self.request(
                bridge, "POST", "/v1/pair", {"code": bridge.code}
            )
            self.assertEqual(status, 403)
        finally:
            bridge.close()

        bridge = BridgeServer(fixture(), {1}, port=0, now=lambda: tick[0])
        bridge.start()
        try:
            tick[0] += 301
            status, _, _ = self.request(
                bridge, "POST", "/v1/pair", {"code": bridge.code}
            )
            self.assertEqual(status, 410)
        finally:
            bridge.close()

    def test_preflight_requires_exact_origin(self) -> None:
        bridge = BridgeServer(fixture(), {1}, port=0)
        bridge.start()
        try:
            status, _, _ = self.request(
                bridge, "OPTIONS", "/v1/pair", origin="https://evil.test"
            )
            self.assertEqual(status, 403)
            status, headers, _ = self.request(bridge, "OPTIONS", "/v1/pair")
            self.assertEqual(status, 204)
            self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        finally:
            bridge.close()

    def request(
        self,
        bridge: BridgeServer,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
        *,
        origin: str | None = "https://amplifai-database-engine.vercel.app",
        host: str | None = None,
        authorization: str | None = None,
        requested_method: str = "POST",
        transfer_encoding: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=2)
        headers = {"Host": host or f"127.0.0.1:{bridge.port}"}
        if origin is not None:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
        if authorization is not None:
            headers["Authorization"] = authorization
        if transfer_encoding is not None:
            headers["Transfer-Encoding"] = transfer_encoding
        if method == "OPTIONS":
            headers["Access-Control-Request-Method"] = requested_method
        connection.request(
            method,
            path,
            json.dumps(body).encode() if body is not None else None,
            headers,
        )
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result


if __name__ == "__main__":
    unittest.main()
