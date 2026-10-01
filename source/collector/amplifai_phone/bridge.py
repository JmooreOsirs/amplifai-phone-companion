"""Local-only, one-use browser handoff candidate for selected phone metadata.

No phone collection or public-site integration starts this server automatically.
The owner must first select contacts and explicitly approve a browser handoff.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

from .ios_backup import IPhoneCapture
from .metadata import interactions_for_contacts, select_people

PILOT_ORIGIN = "https://amplifai-database-engine.vercel.app"
BRIDGE_PORT = 48751
MAX_REQUEST_BYTES = 1024
MAX_TRANSFER_BYTES = 32 * 1024 * 1024
PAGE_BYTES = 128 * 1024
PAIR_SECONDS = 5 * 60
MAX_PAIR_ATTEMPTS = 5


class BridgeError(ValueError):
    pass


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def selected_metadata(capture: IPhoneCapture, ids: set[int]) -> bytes:
    """Serialize a strict allowlist, never source DBs, message bodies or passwords."""
    if not ids:
        raise BridgeError("Select at least one contact")
    contacts = select_people(capture.contacts, ids)
    interactions = interactions_for_contacts(
        contacts, (capture.calls, capture.messages)
    )
    payload = {
        "schema": 1,
        "since": capture.since,
        "collectedAt": capture.collected_at,
        "missingSources": list(capture.missing_sources),
        "contacts": [
            {"sourceId": item.source_id, "name": item.name, "phones": list(item.phones)}
            for item in contacts
        ],
        "interactions": [
            {
                "sourceId": item.source_id,
                "kind": item.kind,
                "transport": item.transport,
                "occurredAt": item.occurred_at,
                "direction": item.direction,
                "participants": list(item.participants),
                "durationSeconds": item.duration_seconds,
            }
            for item in interactions
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_TRANSFER_BYTES:
        raise BridgeError("Selected metadata exceeds the local handoff limit")
    return encoded


class BridgeServer:
    """Exact-origin, exact-Host loopback server with expiring one-use code/token."""

    def __init__(
        self,
        capture: IPhoneCapture,
        ids: set[int],
        *,
        port: int = BRIDGE_PORT,
        origin: str = PILOT_ORIGIN,
        now: Any = time.monotonic,
    ):
        self.payload = selected_metadata(capture, ids)
        self.handoff_id = str(uuid4())
        self.payload_sha256 = hashlib.sha256(self.payload).hexdigest()
        self.acknowledged_saved = False
        self.origin = origin
        self.now = now
        self.expires_at = now() + PAIR_SECONDS
        self.code = f"{secrets.randbelow(10**10):010d}"
        self.token: str | None = None
        self.attempts = 0
        self.used = False
        self.next_cursor = 0
        self.lock = threading.Lock()
        self.lifecycle_lock = threading.Lock()
        self.closed = False
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _authorized_origin(self) -> bool:
                return (
                    self.client_address[0] == "127.0.0.1"
                    and self.headers.get_all("Host") == [f"127.0.0.1:{bridge.port}"]
                    and self.headers.get_all("Origin") == [bridge.origin]
                )

            def _reply(self, status: int, body: bytes = b"{}") -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if self._authorized_origin():
                    self.send_header("Access-Control-Allow-Origin", bridge.origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:
                metadata_path = re.fullmatch(r"/v1/metadata(?:\?cursor=(0|[1-9][0-9]{0,3}))?", self.path)
                if not self._authorized_origin() or (self.path not in ("/v1/pair", "/v1/complete", "/v1/acknowledge-save") and metadata_path is None):
                    self._reply(403)
                    return
                expected_method = "GET" if metadata_path is not None else "POST"
                if self.headers.get_all("Access-Control-Request-Method") != [
                    expected_method
                ]:
                    self._reply(403)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", bridge.origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers", "Authorization, Content-Type"
                )
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Max-Age", "0")
                self.send_header("Content-Length", "0")
                self.send_header("Vary", "Origin")
                self.end_headers()

            def do_POST(self) -> None:
                if not self._authorized_origin():
                    self._reply(403)
                    return
                if self.path == "/v1/complete":
                    self._complete()
                    return
                if self.path == "/v1/acknowledge-save":
                    self._acknowledge_save()
                    return
                if self.path != "/v1/pair":
                    self._reply(403)
                    return
                if self.headers.get("Transfer-Encoding") or self.headers.get_all(
                    "Content-Type"
                ) != ["application/json"]:
                    self._reply(415)
                    return
                lengths = self.headers.get_all("Content-Length")
                if lengths is None or len(lengths) != 1:
                    self._reply(400)
                    return
                try:
                    length = int(lengths[0])
                except ValueError:
                    self._reply(400)
                    return
                if not 0 < length <= MAX_REQUEST_BYTES:
                    self._reply(413)
                    return
                try:
                    command = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._reply(400)
                    return
                if (
                    not isinstance(command, dict)
                    or set(command) != {"code"}
                    or not isinstance(command["code"], str)
                ):
                    self._reply(400)
                    return
                with bridge.lock:
                    if (
                        bridge.now() >= bridge.expires_at
                        or bridge.used
                        or bridge.token is not None
                    ):
                        self._reply(410)
                        return
                    bridge.attempts += 1
                    if bridge.attempts > MAX_PAIR_ATTEMPTS or not hmac.compare_digest(
                        command["code"], bridge.code
                    ):
                        self._reply(403)
                        return
                    bridge.token = secrets.token_urlsafe(32)
                    body = json.dumps(
                        {
                            "token": bridge.token,
                            "handoffId": bridge.handoff_id,
                            "payloadSha256": bridge.payload_sha256,
                            "expiresInSeconds": max(
                                0, int(bridge.expires_at - bridge.now())
                            ),
                        }
                    ).encode()
                self._reply(200, body)

            def _complete(self) -> None:
                if self.headers.get("Transfer-Encoding"):
                    self._reply(415)
                    return
                if self.headers.get("Content-Length") not in (None, "0"):
                    self._reply(400)
                    return
                authorizations = self.headers.get_all("Authorization")
                if authorizations is None or len(authorizations) != 1:
                    self._reply(403)
                    return
                with bridge.lock:
                    if bridge.now() >= bridge.expires_at or bridge.closed:
                        self._reply(410)
                        return
                    if bridge.token is None or not hmac.compare_digest(
                        authorizations[0], f"Bearer {bridge.token}"
                    ):
                        self._reply(403)
                        return
                    page_count = (len(bridge.payload) + PAGE_BYTES - 1) // PAGE_BYTES
                    if bridge.next_cursor < page_count:
                        self._reply(409)
                        return
                    bridge.used = True
                self._reply(200, b'{"completed":true}')

            def _acknowledge_save(self) -> None:
                """The trusted browser must assert a real validated save, never delivery."""
                if self.headers.get("Transfer-Encoding") or self.headers.get_all("Content-Type") != ["application/json"]:
                    self._reply(415)
                    return
                authorizations = self.headers.get_all("Authorization")
                with bridge.lock:
                    if bridge.now() >= bridge.expires_at or bridge.closed:
                        self._reply(410)
                        return
                    if (authorizations is None or len(authorizations) != 1 or bridge.token is None
                            or not hmac.compare_digest(authorizations[0].encode("utf-8"), f"Bearer {bridge.token}".encode("ascii"))):
                        self._reply(403)
                        return
                lengths = self.headers.get_all("Content-Length")
                try:
                    length = int(lengths[0]) if lengths is not None and len(lengths) == 1 else 0
                except ValueError:
                    length = 0
                if not 0 < length <= MAX_REQUEST_BYTES:
                    self._reply(413 if length > MAX_REQUEST_BYTES else 400)
                    return
                try:
                    command = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._reply(400)
                    return
                if (not isinstance(command, dict) or set(command) != {"handoffId", "payloadSha256", "saved"}
                        or command.get("saved") is not True
                        or not isinstance(command.get("handoffId"), str)
                        or not isinstance(command.get("payloadSha256"), str)):
                    self._reply(400)
                    return
                with bridge.lock:
                    # The immutable pairing binds this assertion to exactly the reviewed metadata.
                    if bridge.now() >= bridge.expires_at or bridge.closed:
                        self._reply(410)
                        return
                    if not bridge.used or command["handoffId"] != bridge.handoff_id or command["payloadSha256"] != bridge.payload_sha256:
                        self._reply(409)
                        return
                    bridge.acknowledged_saved = True
                self._reply(200, b'{"acknowledged":true}')

            def do_GET(self) -> None:
                if not self._authorized_origin():
                    self._reply(403)
                    return
                match = re.fullmatch(r"/v1/metadata(?:\?cursor=(0|[1-9][0-9]{0,3}))?", self.path)
                if match is None:
                    self._reply(400)
                    return
                cursor = int(match.group(1) or "0")
                authorizations = self.headers.get_all("Authorization")
                if authorizations is None or len(authorizations) != 1:
                    self._reply(403)
                    return
                authorization = authorizations[0]
                with bridge.lock:
                    if bridge.now() >= bridge.expires_at or bridge.used:
                        self._reply(410)
                        return
                    if bridge.token is None or not hmac.compare_digest(
                        authorization, f"Bearer {bridge.token}"
                    ):
                        self._reply(403)
                        return
                    page_count = (len(bridge.payload) + PAGE_BYTES - 1) // PAGE_BYTES
                    if cursor >= page_count or cursor > bridge.next_cursor:
                        self._reply(409)
                        return
                    if page_count == 1:
                        bridge.used = True
                        body = bridge.payload
                    else:
                        start = cursor * PAGE_BYTES
                        end = min(start + PAGE_BYTES, len(bridge.payload))
                        next_cursor = cursor + 1 if end < len(bridge.payload) else None
                        body = json.dumps({
                            "format": "chunked-json-v1",
                            "cursor": cursor,
                            "chunk": bridge.payload[start:end].decode("ascii"),
                            "nextCursor": next_cursor,
                            "totalBytes": len(bridge.payload),
                        }, separators=(",", ":")).encode()
                        bridge.next_cursor = max(bridge.next_cursor, cursor + 1)
                self._reply(200, body)

        self.httpd = LocalHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.timer = threading.Timer(PAIR_SECONDS, self.close)
        self.timer.daemon = True

    def start(self) -> None:
        self.thread.start()
        self.timer.start()

    def handoff_state(self) -> str:
        with self.lock:
            if self.acknowledged_saved:
                return "saved"
            if self.now() >= self.expires_at or self.closed:
                return "expired"
            return "received" if self.used else "waiting"

    def close(self) -> None:
        with self.lifecycle_lock:
            if self.closed:
                return
            self.closed = True
        self.timer.cancel()
        if self.thread.is_alive():
            self.httpd.shutdown()
            self.thread.join(timeout=2)
        self.httpd.server_close()
