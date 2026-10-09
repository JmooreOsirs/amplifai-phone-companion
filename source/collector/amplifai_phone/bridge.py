"""Local-only, one-use browser handoff candidate for selected phone metadata.

No phone collection or public-site integration starts this server automatically.
The owner must first select contacts and explicitly approve a browser handoff.
"""

from __future__ import annotations

import hashlib
import http.client
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
from .paged_transfer import PagedTransfer
from .record_store import SelectionSnapshot

PILOT_ORIGIN = "https://amplifai-database-engine.vercel.app"
BRIDGE_PORT = 48751
MAX_REQUEST_BYTES = 1024
MAX_TRANSFER_BYTES = 32 * 1024 * 1024
PAGE_BYTES = 128 * 1024
PAIR_SECONDS = 5 * 60
TRANSFER_SECONDS = 2 * 60 * 60
MAX_PAIR_ATTEMPTS = 5
CONNECT_API_HOST = "amplifai-database-engine.vercel.app"
CONNECT_API_PATH = "/api/v1/phone-connect-intents/"
CONNECT_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{43}\Z")
CONNECT_ID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z")


def verify_account_intent(action: str, command: dict[str, str]) -> dict[str, object] | None:
    """Fixed TLS Production authority; no browser cookie, account ID or arbitrary URL."""
    if action not in ("claim", "consume"):
        return None
    body = json.dumps(command, separators=(",", ":")).encode("ascii")
    if len(body) > 1024:
        return None
    connection = http.client.HTTPSConnection(CONNECT_API_HOST, timeout=5)
    try:
        connection.request("POST", CONNECT_API_PATH + action, body,
                           {"Content-Type": "application/json", "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200 or not response.getheader("Content-Type", "").startswith("application/json"):
            return None
        if int(response.getheader("Content-Length", "0")) > 2048:
            return None
        encoded = response.read(2049)
        if len(encoded) > 2048:
            return None
        value = json.loads(encoded)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        connection.close()


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
    included = {
        "contacts": len(contacts),
        "calls": sum(item.kind == "call" for item in interactions),
        "messages": sum(item.kind == "message" for item in interactions),
    }
    sources = {
        "contacts": capture.contacts,
        "calls": capture.calls,
        "messages": capture.messages,
    }
    if any(source.rows_seen < included[kind] for kind, source in sources.items()):
        raise BridgeError("Source coverage counts are inconsistent")
    payload = {
        "schema": 1,
        "since": capture.since,
        "collectedAt": capture.collected_at,
        "missingSources": list(capture.missing_sources),
        "backupEncrypted": capture.backup_encrypted,
        "unavailableReasons": dict(capture.unavailable_reasons),
        "sourceStats": {
            kind: {"rowsSeen": source.rows_seen, "rowsIncluded": included[kind]}
            for kind, source in sources.items()
        },
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
        ids: set[int] | SelectionSnapshot,
        *,
        port: int = BRIDGE_PORT,
        origin: str = PILOT_ORIGIN,
        now: Any = time.monotonic,
        account_proof: Any = verify_account_intent,
    ):
        self.handoff_id = str(uuid4())
        if isinstance(ids, SelectionSnapshot) and (
            capture.record_store is not ids.store or ids.store.selection_review_id != ids.review_id
        ):
            raise BridgeError("The reviewed selection changed")
        self.transfer = PagedTransfer(
            capture, ids, capture.review_workspace.directory / f"handoff-{self.handoff_id}.sqlite3"
        ) if capture.review_workspace is not None and capture.record_store is not None else None
        self.payload = selected_metadata(capture, ids) if self.transfer is None else b""
        self.payload_sha256 = self.transfer.sha256 if self.transfer is not None else hashlib.sha256(self.payload).hexdigest()
        self.acknowledged_saved = False
        self.browser_confirmed_saved = False
        self.origin = origin
        self.now = now
        self.account_proof = account_proof
        self.expires_at = now() + PAIR_SECONDS
        self.transfer_expires_at: float | None = None
        self.code = f"{secrets.randbelow(10**10):010d}"
        self.token: str | None = None
        self.attempts = 0
        self.used = False
        self.connecting = False
        self.releasing = False
        self.destination: dict[str, str] | None = None
        self.destination_state = "waiting"
        self.next_cursor = 0
        self.source_cursors = {"contacts": 0, "calls": 0, "messages": 0}
        self.source_dispositions = {
            category: ("delivered" if self.transfer is not None and
                       self.transfer.manifest["sources"][category]["pageCount"] == 0 else "pending")
            for category in self.source_cursors
        }
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
                source_path = re.fullmatch(r"/v2/source/(contacts|calls|messages)\?cursor=(0|[1-9][0-9]{0,9})", self.path)
                if not self._authorized_origin() or (self.path not in ("/v1/pair", "/v1/complete", "/v1/acknowledge-save", "/v1/confirm-ack-received", "/v2/decline", "/v2/keepalive", "/v3/discover", "/v3/status", "/v3/connect", "/v3/release") and metadata_path is None and source_path is None):
                    self._reply(403)
                    return
                expected_method = "GET" if metadata_path is not None or source_path is not None or self.path in ("/v3/discover", "/v3/status") else "POST"
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
                if self.path == "/v3/connect":
                    self._connect_account()
                    return
                if self.path == "/v3/release":
                    self._release_account()
                    return
                if self.path == "/v1/complete":
                    self._complete()
                    return
                if self.path == "/v2/keepalive" and bridge.transfer is not None:
                    self._keepalive()
                    return
                if self.path == "/v2/decline" and bridge.transfer is not None:
                    self._decline_source()
                    return
                if self.path == "/v1/acknowledge-save":
                    self._save_command(confirm_received=False)
                    return
                if self.path == "/v1/confirm-ack-received":
                    self._save_command(confirm_received=True)
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
                    if bridge.destination_state in ("pending", "approved", "released"):
                        self._reply(409)
                        return
                    bridge.attempts += 1
                    if bridge.attempts > MAX_PAIR_ATTEMPTS or not hmac.compare_digest(
                        command["code"], bridge.code
                    ):
                        self._reply(403)
                        return
                    bridge.token = secrets.token_urlsafe(32)
                    bridge.transfer_expires_at = bridge.now() + TRANSFER_SECONDS
                    body = json.dumps(bridge._pair_response(), separators=(",", ":")).encode()
                self._reply(200, body)

            def _json_command(self, fields: set[str]) -> dict[str, str] | None:
                if self.headers.get("Transfer-Encoding") or self.headers.get_all("Content-Type") != ["application/json"]:
                    return None
                lengths = self.headers.get_all("Content-Length")
                try:
                    length = int(lengths[0]) if lengths is not None and len(lengths) == 1 else 0
                except ValueError:
                    return None
                if not 0 < length <= MAX_REQUEST_BYTES:
                    return None
                try:
                    command = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return None
                return command if isinstance(command, dict) and set(command) == fields and all(
                    isinstance(value, str) for value in command.values()) else None

            def _connect_account(self) -> None:
                command = self._json_command({"intentId", "capability", "handoffId", "payloadSha256"})
                if (command is None or CONNECT_ID.fullmatch(command["intentId"]) is None
                        or CONNECT_CAPABILITY.fullmatch(command["capability"]) is None):
                    self._reply(400)
                    return
                with bridge.lock:
                    if bridge.closed or bridge.now() >= bridge.expires_at or bridge.token is not None or bridge.used:
                        self._reply(410)
                        return
                    if (bridge.connecting or bridge.releasing or bridge.destination_state in ("pending", "approved", "released")
                            or command["handoffId"] != bridge.handoff_id
                            or command["payloadSha256"] != bridge.payload_sha256):
                        self._reply(409)
                        return
                    bridge.connecting = True
                try:
                    result = bridge.account_proof("claim", command)
                except Exception:
                    result = None
                with bridge.lock:
                    bridge.connecting = False
                    if bridge.closed or bridge.now() >= bridge.expires_at or bridge.token is not None:
                        self._reply(410)
                        return
                    email = result.get("destinationEmail") if isinstance(result, dict) else None
                    course = result.get("destinationCourse") if isinstance(result, dict) else None
                    if (not isinstance(email, str) or len(email) > 254 or "@" not in email or
                            not isinstance(course, str) or not 0 < len(course) <= 160 or
                            any(ord(char) < 32 for char in email + course)):
                        self._reply(409)
                        return
                    bridge.destination = {**command, "email": email, "course": course}
                    bridge.destination_state = "pending"
                self._reply(200, b'{"pending":true}')

            def _release_account(self) -> None:
                command = self._json_command({"intentId", "capability", "releaseCode"})
                if (command is None or CONNECT_ID.fullmatch(command["intentId"]) is None or
                        CONNECT_CAPABILITY.fullmatch(command["capability"]) is None or
                        CONNECT_CAPABILITY.fullmatch(command["releaseCode"]) is None):
                    self._reply(400)
                    return
                with bridge.lock:
                    pending = bridge.destination
                    if bridge.closed or bridge.now() >= bridge.expires_at or bridge.token is not None:
                        self._reply(410)
                        return
                    if (pending is None or bridge.destination_state != "approved" or bridge.releasing or
                            pending["intentId"] != command["intentId"] or not hmac.compare_digest(
                                pending["capability"], command["capability"])):
                        self._reply(403)
                        return
                    bridge.releasing = True
                    proof = {**command, "handoffId": bridge.handoff_id, "payloadSha256": bridge.payload_sha256}
                try:
                    result = bridge.account_proof("consume", proof)
                except Exception:
                    result = None
                with bridge.lock:
                    bridge.releasing = False
                    if bridge.closed or bridge.now() >= bridge.expires_at or bridge.token is not None:
                        self._reply(410)
                        return
                    if not isinstance(result, dict) or result.get("consumed") is not True:
                        self._reply(409)
                        return
                    bridge.token = secrets.token_urlsafe(32)
                    bridge.transfer_expires_at = bridge.now() + TRANSFER_SECONDS
                    bridge.destination_state = "released"
                    body = json.dumps(bridge._pair_response(), separators=(",", ":")).encode("ascii")
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
                    if (bridge.now() >= (bridge.transfer_expires_at if bridge.transfer is not None else bridge.expires_at)
                            or bridge.closed):
                        self._reply(410)
                        return
                    if bridge.token is None or not hmac.compare_digest(
                        authorizations[0], f"Bearer {bridge.token}"
                    ):
                        self._reply(403)
                        return
                    if bridge.transfer is not None:
                        complete = all(
                            bridge.source_dispositions[category] in ("delivered", "declined")
                            for category in bridge.source_cursors
                        )
                    else:
                        page_count = (len(bridge.payload) + PAGE_BYTES - 1) // PAGE_BYTES
                        complete = bridge.next_cursor >= page_count
                    if not complete:
                        self._reply(409)
                        return
                    bridge.used = True
                    if bridge.transfer is not None:
                        bridge.transfer_expires_at = bridge.now() + TRANSFER_SECONDS
                    dispositions = dict(bridge.source_dispositions) if bridge.transfer is not None else None
                body = ({"completed": True, "sources": dispositions} if dispositions is not None
                        else {"completed": True})
                self._reply(200, json.dumps(body, separators=(",", ":")).encode("ascii"))

            def _keepalive(self) -> None:
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") not in (None, "0"):
                    self._reply(400)
                    return
                authorizations = self.headers.get_all("Authorization")
                with bridge.lock:
                    if bridge.closed or bridge.browser_confirmed_saved or bridge.transfer_expires_at is None or bridge.now() >= bridge.transfer_expires_at:
                        self._reply(410)
                        return
                    if authorizations is None or len(authorizations) != 1 or bridge.token is None or not hmac.compare_digest(
                        authorizations[0], f"Bearer {bridge.token}"
                    ):
                        self._reply(403)
                        return
                    bridge.transfer_expires_at = bridge.now() + TRANSFER_SECONDS
                self._reply(200, b'{"active":true}')

            def _save_command(self, *, confirm_received: bool) -> None:
                """The browser confirms its received ACK before the helper may finish."""
                if self.headers.get("Transfer-Encoding") or self.headers.get_all("Content-Type") != ["application/json"]:
                    self._reply(415)
                    return
                authorizations = self.headers.get_all("Authorization")
                with bridge.lock:
                    if bridge.closed or (bridge.transfer is not None and bridge.transfer_expires_at is not None
                                         and bridge.now() >= bridge.transfer_expires_at) or (
                                             bridge.transfer is None and not bridge.used and bridge.now() >= bridge.expires_at):
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
                decision = "received" if confirm_received else "saved"
                if (not isinstance(command, dict) or set(command) != {"handoffId", "payloadSha256", decision}
                        or command.get(decision) is not True
                        or not isinstance(command.get("handoffId"), str)
                        or not isinstance(command.get("payloadSha256"), str)):
                    self._reply(400)
                    return
                with bridge.lock:
                    # The immutable pairing binds this assertion to exactly the reviewed metadata.
                    if bridge.closed or (bridge.transfer is not None and bridge.transfer_expires_at is not None
                                         and bridge.now() >= bridge.transfer_expires_at) or (
                                             bridge.transfer is None and not bridge.used and bridge.now() >= bridge.expires_at):
                        self._reply(410)
                        return
                    if not bridge.used or command["handoffId"] != bridge.handoff_id or command["payloadSha256"] != bridge.payload_sha256:
                        self._reply(409)
                        return
                    if confirm_received:
                        if not bridge.acknowledged_saved:
                            self._reply(409)
                            return
                        # Finish only after the final response has been written; a
                        # broken connection keeps the exact retryable review open.
                        self._reply(200, b'{"received":true}')
                        bridge.browser_confirmed_saved = True
                        return
                    bridge.acknowledged_saved = True
                self._reply(200, b'{"acknowledged":true}')

            def _decline_source(self) -> None:
                if self.headers.get("Transfer-Encoding") or self.headers.get_all("Content-Type") != ["application/json"]:
                    self._reply(415)
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
                if (not isinstance(command, dict) or set(command) != {"handoffId", "payloadSha256", "category"}
                        or command.get("category") not in bridge.source_cursors):
                    self._reply(400)
                    return
                authorizations = self.headers.get_all("Authorization")
                with bridge.lock:
                    if (bridge.closed or bridge.used or bridge.transfer_expires_at is None
                            or bridge.now() >= bridge.transfer_expires_at):
                        self._reply(410)
                        return
                    if authorizations is None or len(authorizations) != 1 or bridge.token is None or not hmac.compare_digest(
                        authorizations[0], f"Bearer {bridge.token}"
                    ):
                        self._reply(403)
                        return
                    if command["handoffId"] != bridge.handoff_id or command["payloadSha256"] != bridge.payload_sha256:
                        self._reply(409)
                        return
                    category = command["category"]
                    if bridge.source_dispositions[category] == "declined":
                        bridge.transfer_expires_at = bridge.now() + TRANSFER_SECONDS
                        self._reply(200, b'{"declined":true}')
                        return
                    if bridge.source_cursors[category] != 0:
                        self._reply(409)
                        return
                    bridge.source_dispositions[category] = "declined"
                    bridge.transfer_expires_at = bridge.now() + TRANSFER_SECONDS
                self._reply(200, b'{"declined":true}')

            def do_GET(self) -> None:
                if not self._authorized_origin():
                    self._reply(403)
                    return
                if self.path == "/v3/discover":
                    with bridge.lock:
                        if bridge.closed or bridge.now() >= bridge.expires_at or bridge.token is not None:
                            self._reply(410)
                            return
                        body = json.dumps({"format": "account-connect-v1", "handoffId": bridge.handoff_id,
                            "payloadSha256": bridge.payload_sha256,
                            "expiresInSeconds": max(1, int(bridge.expires_at - bridge.now()))},
                            separators=(",", ":")).encode("ascii")
                    self._reply(200, body)
                    return
                if self.path == "/v3/status":
                    authorizations = self.headers.get_all("Authorization")
                    with bridge.lock:
                        if bridge.closed or bridge.now() >= bridge.expires_at:
                            self._reply(410)
                            return
                        pending = bridge.destination
                        if (pending is None or authorizations is None or len(authorizations) != 1 or
                                not hmac.compare_digest(authorizations[0], "Bearer " + pending["capability"])):
                            self._reply(403)
                            return
                        state = bridge.destination_state
                    self._reply(200, json.dumps({"state": state}, separators=(",", ":")).encode("ascii"))
                    return
                source_match = re.fullmatch(r"/v2/source/(contacts|calls|messages)\?cursor=(0|[1-9][0-9]{0,9})", self.path)
                if source_match is not None and bridge.transfer is not None:
                    self._source_page(source_match.group(1), int(source_match.group(2)))
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

            def _source_page(self, category: str, cursor: int) -> None:
                authorizations = self.headers.get_all("Authorization")
                if authorizations is None or len(authorizations) != 1:
                    self._reply(403)
                    return
                with bridge.lock:
                    if (bridge.closed or bridge.used or bridge.transfer_expires_at is None
                            or bridge.now() >= bridge.transfer_expires_at):
                        self._reply(410)
                        return
                    if bridge.token is None or not hmac.compare_digest(
                        authorizations[0], f"Bearer {bridge.token}"
                    ):
                        self._reply(403)
                        return
                    if bridge.source_dispositions[category] == "declined":
                        self._reply(410)
                        return
                    if cursor > bridge.source_cursors[category]:
                        self._reply(409)
                        return
                    page = bridge.transfer.page(category, cursor)
                    if page is None:
                        self._reply(409)
                        return
                    bridge.source_cursors[category] = max(bridge.source_cursors[category], cursor + 1)
                    if bridge.source_cursors[category] == bridge.transfer.manifest["sources"][category]["pageCount"]:
                        bridge.source_dispositions[category] = "delivered"
                    bridge.transfer_expires_at = bridge.now() + TRANSFER_SECONDS
                self._reply(200, json.dumps(page, separators=(",", ":")).encode("ascii"))

        try:
            self.httpd = LocalHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            if self.transfer is not None:
                self.transfer.close()
            raise
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.timer = threading.Timer(PAIR_SECONDS, self._expire_unclaimed)
        self.timer.daemon = True

    def _pair_response(self) -> dict[str, object]:
        if self.token is None:
            raise BridgeError("No authorized browser handoff")
        response: dict[str, object] = {
            "token": self.token, "handoffId": self.handoff_id,
            "payloadSha256": self.payload_sha256, "confirmReceiptRequired": True,
            "expiresInSeconds": max(1, int(self.expires_at - self.now())),
        }
        if self.transfer is not None:
            response.update({"format": "paged-records-v2", "manifestJson": json.dumps(
                self.transfer.manifest, ensure_ascii=True, separators=(",", ":"))})
        return response

    def pending_destination(self) -> dict[str, str] | None:
        with self.lock:
            if (self.closed or self.now() >= self.expires_at or self.destination_state != "pending"
                    or self.destination is None):
                return None
            return {"intentId": self.destination["intentId"], "email": self.destination["email"],
                    "course": self.destination["course"]}

    def approve_destination(self, intent_id: str, approved: bool) -> bool:
        with self.lock:
            if (self.closed or self.now() >= self.expires_at or self.destination_state != "pending"
                    or self.destination is None or self.destination["intentId"] != intent_id):
                return False
            self.destination_state = "approved" if approved else "denied"
            return True

    def start(self) -> None:
        self.thread.start()
        self.timer.start()

    def handoff_state(self) -> str:
        with self.lock:
            if self.browser_confirmed_saved:
                return "saved"
            if self.acknowledged_saved:
                return "saved_pending_browser_receipt"
            deadline = self.transfer_expires_at if self.token is not None and self.transfer is not None else self.expires_at
            if self.closed or ((self.transfer is not None or not self.used) and self.now() >= deadline):
                return "expired"
            return "received" if self.used else "waiting"

    def _expire_unclaimed(self) -> None:
        # The code and metadata transfer expire in five minutes. Once transferred,
        # this process-bound, exact-origin ACK endpoint remains available until
        # the user finishes, revokes, disconnects, or closes the companion.
        with self.lifecycle_lock, self.lock:
            if self.closed or self.used or self.token is not None:
                return
            self.closed = True
        self.timer.cancel()
        self._shutdown()
        if self.transfer is not None:
            self.transfer.close()

    def _shutdown(self) -> None:
        if self.thread.is_alive():
            self.httpd.shutdown()
            self.thread.join(timeout=2)
        self.httpd.server_close()

    def close(self) -> None:
        with self.lifecycle_lock:
            if self.closed:
                if self.transfer is not None:
                    self.transfer.close()
                return
            self.closed = True
        self.timer.cancel()
        self._shutdown()
        if self.transfer is not None:
            self.transfer.close()
