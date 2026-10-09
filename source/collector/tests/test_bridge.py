from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amplifai_phone.bridge import BridgeError, BridgeServer, selected_metadata
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, Interaction, SourceResult
from amplifai_phone.paged_transfer import EMPTY_CHAIN, PagedTransfer
from amplifai_phone.record_store import RecordStore, storage_failure
from amplifai_phone.workspace import SessionWorkspace, WorkspaceError


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
    def test_sqlite_full_and_io_errors_remain_storage_failures(self) -> None:
        full = sqlite3.OperationalError("database or disk is full")
        full.sqlite_errorcode = sqlite3.SQLITE_FULL
        io_error = sqlite3.OperationalError("disk I/O error")
        io_error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        self.assertEqual(storage_failure(full).code, "workspace_low_space")
        self.assertEqual(storage_failure(io_error).code, "workspace_unavailable")
        with tempfile.TemporaryDirectory(prefix="amplifai-sqlite-full-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            try:
                store.add("contacts", Contact(1, "Synthetic", ("+15551234567",), ()))
                capture = IPhoneCapture(SourceResult(1, store.collection("contacts"), 0),
                                        SourceResult(0, store.collection("calls"), 0),
                                        SourceResult(0, store.collection("messages"), 0),
                                        "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (), workspace, store)
                with (patch("amplifai_phone.paged_transfer.sqlite3.connect", side_effect=full),
                      self.assertRaises(WorkspaceError) as raised):
                    BridgeServer(capture, {1}, port=0)
                self.assertEqual(raised.exception.code, "workspace_low_space")
            finally:
                store.close()
                workspace.__exit__(None, None, None)

    def test_long_contact_name_survives_v2_page_without_manifest_clipping(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-long-name-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            long_name = "Full " + "N" * 320
            try:
                store.add("contacts", Contact(1, long_name, ("+15551234567",), ()))
                capture = IPhoneCapture(SourceResult(1, store.collection("contacts"), 0),
                                        SourceResult(0, store.collection("calls"), 0),
                                        SourceResult(0, store.collection("messages"), 0),
                                        "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (), workspace, store)
                transfer = PagedTransfer(capture, {1}, directory / "transfer.sqlite3")
                try:
                    self.assertEqual(transfer.manifest["sampleContactNames"], [])
                    self.assertEqual(json.loads(transfer.page("contacts", 0)["chunk"])[0]["name"], long_name)
                finally:
                    transfer.close()
            finally:
                store.close()
                workspace.__exit__(None, None, None)

    def test_contact_name_larger_than_one_page_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-name-limit-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            try:
                store.add("contacts", Contact(1, "N" * (128 * 1024), ("+15551234567",), ()))
                capture = IPhoneCapture(SourceResult(1, store.collection("contacts"), 0),
                                        SourceResult(0, store.collection("calls"), 0),
                                        SourceResult(0, store.collection("messages"), 0),
                                        "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (), workspace, store)
                transfer_path = directory / "transfer.sqlite3"
                with self.assertRaisesRegex(ValueError, "exceeds one transfer page"):
                    PagedTransfer(capture, {1}, transfer_path)
                self.assertFalse(transfer_path.exists())
            finally:
                store.close()
                workspace.__exit__(None, None, None)

    def test_private_paged_transfer_has_exact_counts_hashes_and_ordered_retries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-transfer-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            try:
                store.add("contacts", Contact(1, "Ada Synthetic", ("+15551234567",), ("private@example.test",)))
                for index in range(250):
                    store.add("calls", Interaction(index + 1, "call", "call", "2026-09-01T00:00:00Z",
                                                   "incoming", ("+15551234567",), 30))
                capture = IPhoneCapture(SourceResult(1, store.collection("contacts"), 0),
                                        SourceResult(260, store.collection("calls"), 10),
                                        SourceResult(0, store.collection("messages"), 0),
                                        "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (), workspace, store)
                with patch("amplifai_phone.bridge.MAX_TRANSFER_BYTES", 8):
                    bridge = BridgeServer(capture, {1}, port=0)
                bridge.start()
                try:
                    status, _, body = self.request(bridge, "POST", "/v1/pair", {"code": bridge.code})
                    self.assertEqual(status, 200)
                    paired = json.loads(body)
                    self.assertEqual(paired["format"], "paged-records-v2")
                    manifest = json.loads(paired["manifestJson"])
                    self.assertEqual(hashlib.sha256(paired["manifestJson"].encode("ascii")).hexdigest(), paired["payloadSha256"])
                    self.assertEqual(manifest["sources"]["calls"]["rowsIncluded"], 250)
                    self.assertEqual(manifest["sources"]["calls"]["rowsSeen"], 260)
                    self.assertNotIn("private@example.test", body.decode())
                    token = f"Bearer {paired['token']}"
                    status, _, _ = self.request(bridge, "GET", "/v2/source/calls?cursor=1", authorization=token)
                    self.assertEqual(status, 409)
                    status, _, _ = self.request(bridge, "POST", "/v1/complete", authorization=token)
                    self.assertEqual(status, 409)
                    for category in ("contacts", "calls"):
                        chain = EMPTY_CHAIN
                        cursor = 0
                        count = 0
                        while True:
                            path = f"/v2/source/{category}?cursor={cursor}"
                            status, _, body = self.request(bridge, "GET", path, authorization=token)
                            self.assertEqual(status, 200)
                            page = json.loads(body)
                            if cursor == 0:
                                repeat_status, _, repeated = self.request(bridge, "GET", path, authorization=token)
                                self.assertEqual((repeat_status, repeated), (200, body))
                            digest = hashlib.sha256(page["chunk"].encode("ascii")).hexdigest()
                            self.assertEqual(page["pageSha256"], digest)
                            chain = hashlib.sha256((chain + digest).encode("ascii")).hexdigest()
                            count += len(json.loads(page["chunk"]))
                            if page["nextCursor"] is None:
                                break
                            cursor = page["nextCursor"]
                        self.assertEqual(count, manifest["sources"][category]["rowsIncluded"])
                        self.assertEqual(chain, manifest["sources"][category]["chainSha256"])
                    status, _, body = self.request(bridge, "POST", "/v1/complete", authorization=token)
                    self.assertEqual((status, json.loads(body)), (200, {"completed": True,
                        "sources": {"contacts": "delivered", "calls": "delivered", "messages": "delivered"}}))
                finally:
                    bridge.close()
                self.assertFalse((directory / f"handoff-{bridge.handoff_id}.sqlite3").exists())
            finally:
                store.close()
                workspace.__exit__(None, None, None)

    def test_declined_source_is_terminal_and_cannot_be_replayed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-decline-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            try:
                store.add("contacts", Contact(1, "Synthetic", ("+15551234567",), ()))
                store.add("calls", Interaction(1, "call", "call", "2026-09-01T00:00:00Z",
                                               "incoming", ("+15551234567",), 30))
                capture = IPhoneCapture(SourceResult(1, store.collection("contacts"), 0),
                                        SourceResult(1, store.collection("calls"), 0),
                                        SourceResult(0, store.collection("messages"), 0),
                                        "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (), workspace, store)
                bridge = BridgeServer(capture, {1}, port=0)
                bridge.start()
                try:
                    status, _, body = self.request(bridge, "POST", "/v1/pair", {"code": bridge.code})
                    self.assertEqual(status, 200)
                    paired = json.loads(body)
                    token = f"Bearer {paired['token']}"
                    status, _, body = self.request(bridge, "POST", "/v2/decline",
                        {"handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256,
                         "category": "calls"}, authorization=token)
                    self.assertEqual((status, json.loads(body)), (200, {"declined": True}))
                    retry_status, _, retry_body = self.request(bridge, "POST", "/v2/decline",
                        {"handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256,
                         "category": "calls"}, authorization=token)
                    self.assertEqual((retry_status, retry_body), (status, body))
                    for _ in range(2):
                        status, _, _ = self.request(bridge, "GET", "/v2/source/calls?cursor=0", authorization=token)
                        self.assertEqual(status, 410)
                    status, _, _ = self.request(bridge, "POST", "/v1/complete", authorization=token)
                    self.assertEqual(status, 409)
                    status, _, _ = self.request(bridge, "GET", "/v2/source/contacts?cursor=0", authorization=token)
                    self.assertEqual(status, 200)
                    status, _, body = self.request(bridge, "POST", "/v1/complete", authorization=token)
                    self.assertEqual((status, json.loads(body)), (200, {"completed": True,
                        "sources": {"contacts": "delivered", "calls": "declined", "messages": "delivered"}}))
                finally:
                    bridge.close()
            finally:
                store.close()
                workspace.__exit__(None, None, None)

    def test_over_legacy_selection_and_256_part_source_remains_paged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-volume-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            try:
                total = 26_001
                review_id = "00000000-0000-4000-8000-000000000001"
                for source_id in range(1, total + 1):
                    store.add("contacts", Contact(source_id, f"Synthetic {source_id}",
                                                  (f"+1555{source_id:07d}",), ()))
                contacts = store.collection("contacts")
                calls = store.collection("calls")
                messages = store.collection("messages")
                snapshot = None
                for cursor in range(0, total, 1000):
                    snapshot = store.append_selection(review_id, cursor,
                        list(range(cursor + 1, min(total, cursor + 1000) + 1)), total)
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot.count, total)
                capture = IPhoneCapture(SourceResult(total, contacts, 0), SourceResult(0, calls, 0),
                                        SourceResult(0, messages, 0), "2026-09-23T00:00:00Z",
                                        "2026-03-23T00:00:00Z", (), workspace, store)
                with patch("amplifai_phone.paged_transfer.select_people", side_effect=AssertionError("whole selection")):
                    transfer = PagedTransfer(capture, snapshot, directory / "volume-handoff.sqlite3")
                try:
                    source = transfer.manifest["sources"]["contacts"]
                    self.assertEqual(source["rowsIncluded"], total)
                    self.assertGreater(source["pageCount"], 256)
                    self.assertIsNotNone(transfer.page("contacts", 256))
                finally:
                    transfer.close()
            finally:
                store.close()
                workspace.__exit__(None, None, None)

    def test_active_v2_transfer_outlives_pair_code_and_has_sliding_inactivity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-session-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            try:
                store.add("contacts", Contact(1, "Synthetic", ("+15551234567",), ()))
                store.add("calls", Interaction(1, "call", "call", "2026-09-01T00:00:00Z",
                                               "incoming", ("+15551234567",), 30))
                capture = IPhoneCapture(SourceResult(1, store.collection("contacts"), 0),
                                        SourceResult(1, store.collection("calls"), 0),
                                        SourceResult(0, store.collection("messages"), 0),
                                        "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (), workspace, store)
                tick = [0.0]
                bridge = BridgeServer(capture, {1}, port=0, now=lambda: tick[0])
                bridge.start()
                try:
                    status, _, body = self.request(bridge, "POST", "/v1/pair", {"code": bridge.code})
                    self.assertEqual(status, 200)
                    token = f"Bearer {json.loads(body)['token']}"
                    tick[0] = 301.0
                    status, _, _ = self.request(bridge, "GET", "/v2/source/contacts?cursor=0", authorization=token)
                    self.assertEqual(status, 200)
                    tick[0] = 7300.0
                    status, _, _ = self.request(bridge, "GET", "/v2/source/calls?cursor=0", authorization=token)
                    self.assertEqual(status, 200)
                    status, _, _ = self.request(bridge, "POST", "/v1/complete", authorization=token)
                    self.assertEqual(status, 200)
                    self.assertEqual(bridge.handoff_state(), "received")
                    tick[0] = 14_000.0
                    status, _, _ = self.request(bridge, "POST", "/v2/keepalive")
                    self.assertEqual(status, 403)
                    status, _, _ = self.request(bridge, "POST", "/v2/keepalive", authorization=token,
                                                origin="https://evil.test")
                    self.assertEqual(status, 403)
                    status, _, body = self.request(bridge, "POST", "/v2/keepalive", authorization=token)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body), {"active": True})
                    tick[0] = 14_501.0
                    self.assertEqual(bridge.handoff_state(), "received")
                    tick[0] = 21_201.0
                    self.assertEqual(self.request(bridge, "POST", "/v2/keepalive", authorization=token)[0], 410)
                    self.assertEqual(bridge.handoff_state(), "expired")
                finally:
                    bridge.close()
            finally:
                store.close()
                workspace.__exit__(None, None, None)

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
