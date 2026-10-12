"""Offline browser-led cloud handoff before DeviceLink capture.

The existing released metadata bridge starts after collection. This separate
candidate never imports into the released collector. Only exact, short-lived
server-signed object capabilities may cross it; cookies, passwords, source
bytes and provider credentials never do.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from .bridge import (
    BRIDGE_PORT,
    MAX_PAIR_ATTEMPTS,
    PAIR_SECONDS,
    PILOT_ORIGIN,
    LocalHTTPServer,
)
from .offline_cloud_capture import CaptureAdmission, CaptureSeamError
from .offline_object_vfs import ObjectVfsError
from .offline_single_stage_capture import AuthenticatedGateway, ExactGrant

ACTIVE_INACTIVITY_SECONDS = 2 * 60 * 60
OPERATION_SECONDS = 30
MAX_CONTROL_BYTES = 8 * 1024
OPERATIONS = frozenset(
    {
        "start",
        "reserve",
        "grant",
        "verify",
        "read",
        "terminal",
        "dispose",
        "ambiguity",
        "seal",
        "processing_group",
        "revoke",
        "cleanup",
    }
)


class CloudBrowserBridgeError(CaptureSeamError):
    pass


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise CloudBrowserBridgeError("cloud_handoff_identity_invalid")
    return value


def _pairing_admission(candidate: object) -> tuple[CaptureAdmission, str, str]:
    if not isinstance(candidate, dict) or set(candidate) != {
        "ownerId",
        "phoneId",
        "attemptId",
        "actionId",
        "collectionReceiptId",
        "cloudConsentId",
        "categories",
        "disclosureVersion",
        "startChallenge",
        "terminalChallenge",
    }:
        raise CloudBrowserBridgeError("cloud_handoff_identity_invalid")
    categories = candidate["categories"]
    if not isinstance(categories, list) or not all(
        isinstance(value, str) for value in categories
    ):
        raise CloudBrowserBridgeError("cloud_handoff_identity_invalid")
    try:
        admission = CaptureAdmission(
            *(
                _uuid(candidate[name])
                for name in (
                    "ownerId",
                    "phoneId",
                    "attemptId",
                    "actionId",
                    "collectionReceiptId",
                    "cloudConsentId",
                )
            ),
            tuple(categories),
            candidate["disclosureVersion"],
            True,
        )
        start = _uuid(candidate["startChallenge"])
        terminal = _uuid(candidate["terminalChallenge"])
    except (ValueError, TypeError, CaptureSeamError) as error:
        raise CloudBrowserBridgeError("cloud_handoff_identity_invalid") from error
    if start == terminal:
        raise CloudBrowserBridgeError("cloud_handoff_identity_invalid")
    return admission, start, terminal


class CloudBrowserBridge:
    """One-use local code, exact Origin/Host, and one typed in-flight operation."""

    def __init__(
        self,
        on_cancel: Callable[[], None],
        *,
        origin: str = PILOT_ORIGIN,
        port: int = BRIDGE_PORT,
        now: Callable[[], float] = time.monotonic,
        poll_seconds: float = 5.0,
    ) -> None:
        if not 0 < poll_seconds <= 5:
            raise ValueError("cloud_handoff_poll_invalid")
        self.origin = origin
        self.now = now
        self.poll_seconds = poll_seconds
        self.on_cancel = on_cancel
        self.code = f"{secrets.randbelow(10**10):010d}"
        self.expires_at = now() + PAIR_SECONDS
        self.active_deadline: float | None = None
        self.attempts = 0
        self.token: str | None = None
        self.admission: CaptureAdmission | None = None
        self.start_challenge: str | None = None
        self.terminal_challenge: str | None = None
        self.pending: dict | None = None
        self.reply: dict | None = None
        self.last_reply: tuple[str, str] | None = None
        self.closed = False
        self.condition = threading.Condition()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(6)

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _origin(self) -> bool:
                return (
                    self.client_address[0] == "127.0.0.1"
                    and self.headers.get_all("Host") == [f"127.0.0.1:{bridge.port}"]
                    and self.headers.get_all("Origin") == [bridge.origin]
                )

            def _reply(self, status: int, value: object = None) -> None:
                body = (
                    b""
                    if value is None
                    else json.dumps(
                        value, separators=(",", ":"), ensure_ascii=True
                    ).encode("ascii")
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if self._origin():
                    self.send_header("Access-Control-Allow-Origin", bridge.origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _body(self) -> dict | None:
                if self.headers.get("Transfer-Encoding") or self.headers.get_all(
                    "Content-Type"
                ) != ["application/json"]:
                    return None
                lengths = self.headers.get_all("Content-Length")
                if lengths is None or len(lengths) != 1:
                    return None
                try:
                    length = int(lengths[0])
                    if not 0 < length <= MAX_CONTROL_BYTES:
                        return None
                    value = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    return None
                return value if isinstance(value, dict) else None

            def _token(self) -> bool:
                values = self.headers.get_all("Authorization")
                return bool(
                    values is not None
                    and len(values) == 1
                    and bridge.token is not None
                    and hmac.compare_digest(values[0], f"Bearer {bridge.token}")
                )

            def do_OPTIONS(self) -> None:
                allowed = {
                    "/cloud/v1/pair": "POST",
                    "/cloud/v1/next": "GET",
                    "/cloud/v1/reply": "POST",
                    "/cloud/v1/cancel": "POST",
                }
                if not self._origin() or self.headers.get_all(
                    "Access-Control-Request-Method"
                ) != [allowed.get(self.path)]:
                    self._reply(403)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", bridge.origin)
                self.send_header("Access-Control-Allow-Methods", allowed[self.path])
                self.send_header(
                    "Access-Control-Allow-Headers", "Authorization, Content-Type"
                )
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Access-Control-Max-Age", "0")
                self.send_header("Content-Length", "0")
                self.send_header("Vary", "Origin")
                self.end_headers()

            def do_GET(self) -> None:
                if not self._origin() or self.path != "/cloud/v1/next":
                    self._reply(403)
                    return
                with bridge.condition:
                    if bridge.closed or bridge._expired():
                        self._reply(410)
                    elif not self._token():
                        self._reply(403)
                    else:
                        if bridge.pending is None or bridge.reply is not None:
                            bridge.condition.wait(timeout=bridge.poll_seconds)
                        if bridge.closed or bridge._expired():
                            self._reply(410)
                        elif bridge.pending is None or bridge.reply is not None:
                            self._reply(204)
                        else:
                            self._reply(200, bridge.pending)

            def do_POST(self) -> None:
                if not self._origin():
                    self._reply(403)
                    return
                if self.path == "/cloud/v1/pair":
                    self._pair()
                elif self.path == "/cloud/v1/reply":
                    self._accept_reply()
                elif self.path == "/cloud/v1/cancel":
                    self._cancel()
                else:
                    self._reply(403)

            def _pair(self) -> None:
                body = self._body()
                if (
                    body is None
                    or set(body) != {"code", "admission"}
                    or not isinstance(body["code"], str)
                ):
                    self._reply(400)
                    return
                try:
                    admission, start, terminal = _pairing_admission(body["admission"])
                except CloudBrowserBridgeError:
                    self._reply(422)
                    return
                with bridge.condition:
                    if bridge.closed or bridge._expired() or bridge.token is not None:
                        self._reply(410)
                        return
                    bridge.attempts += 1
                    if bridge.attempts > MAX_PAIR_ATTEMPTS or not hmac.compare_digest(
                        body["code"], bridge.code
                    ):
                        self._reply(403)
                        return
                    bridge.admission = admission
                    bridge.start_challenge = start
                    bridge.terminal_challenge = terminal
                    bridge.token = secrets.token_urlsafe(32)
                    bridge.active_deadline = bridge.now() + ACTIVE_INACTIVITY_SECONDS
                    self._reply(
                        200,
                        {
                            "token": bridge.token,
                            "sessionId": str(uuid.uuid4()),
                            "expiresInSeconds": ACTIVE_INACTIVITY_SECONDS,
                        },
                    )
                    bridge.condition.notify_all()

            def _accept_reply(self) -> None:
                body = self._body()
                if (
                    body is None
                    or set(body) != {"requestId", "operation", "attemptId", "response"}
                    or not isinstance(body["response"], dict)
                ):
                    self._reply(400)
                    return
                with bridge.condition:
                    if bridge.closed or bridge._expired():
                        self._reply(410)
                        return
                    if not self._token():
                        self._reply(403)
                        return
                    if (
                        bridge.admission is None
                        or body["attemptId"] != bridge.admission.attempt_id
                    ):
                        self._reply(409)
                        return
                    fingerprint = json.dumps(
                        body, sort_keys=True, separators=(",", ":")
                    )
                    if (
                        bridge.last_reply is not None
                        and body["requestId"] == bridge.last_reply[0]
                    ):
                        self._reply(
                            200 if fingerprint == bridge.last_reply[1] else 409,
                            {"accepted": True},
                        )
                        return
                    if (
                        bridge.pending is None
                        or bridge.reply is not None
                        or any(
                            body[name] != bridge.pending[name]
                            for name in ("requestId", "operation", "attemptId")
                        )
                    ):
                        self._reply(409)
                        return
                    bridge.reply = body["response"]
                    bridge.last_reply = (body["requestId"], fingerprint)
                    bridge.active_deadline = bridge.now() + ACTIVE_INACTIVITY_SECONDS
                    bridge.condition.notify_all()
                    self._reply(200, {"accepted": True})

            def _cancel(self) -> None:
                if self.headers.get("Content-Length") not in (None, "0"):
                    self._reply(400)
                    return
                with bridge.condition:
                    if not self._token():
                        self._reply(403)
                        return
                    bridge.closed = True
                    bridge.condition.notify_all()
                bridge.on_cancel()
                self._reply(200, {"revokedLocally": True, "saved": False})

        self.httpd = LocalHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def _expired(self) -> bool:
        deadline = self.active_deadline if self.token is not None else self.expires_at
        return deadline is not None and self.now() >= deadline

    def start(self) -> None:
        self.thread.start()

    def exchange(self, operation: str, payload: dict) -> dict:
        if operation not in OPERATIONS or payload.get("attemptId") != (
            self.admission.attempt_id if self.admission else None
        ):
            raise CloudBrowserBridgeError("cloud_handoff_scope_invalid")
        with self.condition:
            if (
                self.closed
                or self._expired()
                or self.token is None
                or self.pending is not None
            ):
                raise CloudBrowserBridgeError("cloud_handoff_unavailable")
            self.pending = {
                "requestId": str(uuid.uuid4()),
                "operation": operation,
                "attemptId": payload["attemptId"],
                "input": payload,
            }
            self.reply = None
            self.condition.notify_all()
            deadline = time.monotonic() + OPERATION_SECONDS
            while self.reply is None and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.closed = True
                    self.condition.notify_all()
                    break
                self.condition.wait(timeout=min(remaining, 0.25))
            result = self.reply
            self.pending = None
            self.reply = None
            if result is None:
                raise CloudBrowserBridgeError("cloud_handoff_broker_lost")
            if set(result) == {"errorCode"}:
                raise CloudBrowserBridgeError("cloud_handoff_server_denied")
            return result

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        if self.thread.is_alive():
            self.httpd.shutdown()
            self.thread.join(timeout=2)
        self.httpd.server_close()


class BrowserGateway(AuthenticatedGateway):
    """Typed native port; the signed-in browser executes each server operation."""

    def __init__(
        self,
        bridge: CloudBrowserBridge,
        *,
        storage_host: str | None = None,
        storage_bucket: str | None = None,
    ) -> None:
        if (storage_host is None) != (storage_bucket is None) or (
            storage_host is not None
            and (
                re.fullmatch(
                    r"br-[a-z0-9-]+\.storage\.c-[0-9]+\.[a-z0-9-]+\.aws\.neon\.tech",
                    storage_host,
                )
                is None
                or re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", storage_bucket or "")
                is None
            )
        ):
            raise ValueError("cloud_storage_location_invalid")
        self.bridge = bridge
        self.storage_host = storage_host
        self.storage_bucket = storage_bucket

    def _signed_url(self, value: object, key: str, method: str) -> str:
        if (
            not isinstance(value, str)
            or not self.storage_host
            or not self.storage_bucket
        ):
            raise ObjectVfsError("cloud_signed_location_unavailable")
        url = urlsplit(value)
        expiry = parse_qs(url.query).get("X-Amz-Expires", [])
        headers = parse_qs(url.query).get("X-Amz-SignedHeaders", [])
        signatures = parse_qs(url.query).get("X-Amz-Signature", [])
        required = "content-length" if method == "PUT" else "range"
        if (
            url.scheme != "https"
            or url.netloc != self.storage_host
            or url.path != f"/{self.storage_bucket}/{key}"
            or url.fragment
            or url.username
            or url.password
            or len(expiry) != 1
            or not expiry[0].isdigit()
            or not 1 <= int(expiry[0]) <= 60
            or len(headers) != 1
            or not {"host", required} <= set(headers[0].lower().split(";"))
            or len(signatures) != 1
            or re.fullmatch(r"[0-9a-f]{64}", signatures[0]) is None
        ):
            raise ObjectVfsError("cloud_signed_location_changed")
        return value

    def _request(self, admission: CaptureAdmission, operation: str, **fields) -> dict:
        if admission != self.bridge.admission:
            raise ObjectVfsError("cloud_handoff_attempt_changed")
        return self.bridge.exchange(
            operation,
            {
                "attemptId": admission.attempt_id,
                "phoneId": admission.phone_id,
                **fields,
            },
        )

    def start(
        self, admission, challenge, claim_sha256, capture_mac, session_nonce
    ) -> None:
        if challenge != self.bridge.start_challenge:
            raise ObjectVfsError("cloud_handoff_challenge_changed")
        result = self._request(
            admission,
            "start",
            challenge=challenge,
            claimSha256=claim_sha256,
            captureMac=capture_mac,
            sessionNonce=session_nonce,
        )
        if set(result) != {"repeated"} or type(result["repeated"]) is not bool:
            raise ObjectVfsError("cloud_handoff_receipt_invalid")

    def reserve(
        self,
        admission,
        category,
        role,
        group_index,
        group_id,
        first_nonce,
        cipher_bytes,
        cipher_sha256,
    ) -> str:
        result = self._request(
            admission,
            "reserve",
            category=category,
            role=role,
            groupIndex=group_index,
            groupId=group_id,
            firstNonce=first_nonce,
            cipherBytes=cipher_bytes,
            cipherSha256=cipher_sha256,
        )
        if set(result) != {"objectKey"}:
            raise ObjectVfsError("cloud_handoff_group_changed")
        key = result.get("objectKey")
        expected = f"cloud-v1/{admission.attempt_id}/{category}/{role}/{group_index}/{group_id}"
        if key != expected:
            raise ObjectVfsError("cloud_handoff_group_changed")
        return key

    def grant(self, admission, category, role, group_index, group_id) -> ExactGrant:
        result = self._request(
            admission,
            "grant",
            category=category,
            role=role,
            groupIndex=group_index,
            groupId=group_id,
        )
        if set(result) not in (
            {"objectKey", "cipherBytes", "cipherSha256", "expiresAt"},
            {"objectKey", "cipherBytes", "cipherSha256", "expiresAt", "uploadUrl"},
        ):
            raise ObjectVfsError("cloud_handoff_grant_invalid")
        try:
            grant = ExactGrant(
                result["objectKey"],
                result["cipherBytes"],
                result["cipherSha256"],
                result["expiresAt"],
                self._signed_url(result["uploadUrl"], result["objectKey"], "PUT")
                if "uploadUrl" in result
                else None,
            )
        except (KeyError, TypeError) as error:
            raise ObjectVfsError("cloud_handoff_grant_invalid") from error
        if (
            grant.object_key
            != f"cloud-v1/{admission.attempt_id}/{category}/{role}/{group_index}/{group_id}"
            or type(grant.cipher_bytes) is not int
            or not 1 <= grant.cipher_bytes <= 1_048_704
            or not isinstance(grant.cipher_sha256, str)
            or len(grant.cipher_sha256) != 64
            or any(
                character not in "0123456789abcdef" for character in grant.cipher_sha256
            )
            or type(grant.expires_at) not in (int, float)
        ):
            raise ObjectVfsError("cloud_handoff_grant_invalid")
        return grant

    def read_grant(
        self,
        admission: CaptureAdmission,
        category: str,
        role: str,
        group_index: int,
        group_id: str,
        start: int,
        end: int,
    ) -> tuple[str, int, str, str, float]:
        result = self._request(
            admission,
            "read",
            category=category,
            role=role,
            groupIndex=group_index,
            groupId=group_id,
            start=start,
            end=end,
        )
        key = f"cloud-v1/{admission.attempt_id}/{category}/{role}/{group_index}/{group_id}"
        if (
            set(result)
            != {
                "objectKey",
                "cipherBytes",
                "cipherSha256",
                "start",
                "end",
                "readUrl",
                "expiresAt",
            }
            or result["objectKey"] != key
            or result["start"] != start
            or result["end"] != end
            or type(result["cipherBytes"]) is not int
            or not 1 <= result["cipherBytes"] <= 1_048_704
            or type(result["cipherSha256"]) is not str
            or len(result["cipherSha256"]) != 64
            or any(c not in "0123456789abcdef" for c in result["cipherSha256"])
            or type(result["expiresAt"]) not in (int, float)
            or not time.time() < result["expiresAt"] <= time.time() + 31
        ):
            raise ObjectVfsError("cloud_range_grant_invalid")
        return (
            key,
            result["cipherBytes"],
            result["cipherSha256"],
            self._signed_url(result["readUrl"], key, "GET"),
            result["expiresAt"],
        )

    def verify(self, admission, object_key, cipher_bytes, cipher_sha256) -> None:
        result = self._request(
            admission,
            "verify",
            objectKey=object_key,
            cipherBytes=cipher_bytes,
            cipherSha256=cipher_sha256,
        )
        if set(result) != {"repeated"} or type(result["repeated"]) is not bool:
            raise ObjectVfsError("cloud_handoff_receipt_invalid")

    def terminal(
        self, admission, challenge, claim_sha256, source_set_sha256, inventory_sha256
    ) -> None:
        if challenge != self.bridge.terminal_challenge:
            raise ObjectVfsError("cloud_handoff_challenge_changed")
        result = self._request(
            admission,
            "terminal",
            challenge=challenge,
            claimSha256=claim_sha256,
            sourceSetSha256=source_set_sha256,
            selectedInventorySha256=inventory_sha256,
            successfulDeviceLink=True,
        )
        if set(result) != {"repeated"} or type(result["repeated"]) is not bool:
            raise ObjectVfsError("cloud_handoff_receipt_invalid")

    def dispose(
        self,
        admission,
        category,
        disposition,
        group_count,
        cipher_bytes,
        descriptor_sha256,
    ) -> None:
        self._request(
            admission,
            "dispose",
            category=category,
            disposition=disposition,
            groupCount=group_count,
            cipherBytes=cipher_bytes,
            descriptorSha256=descriptor_sha256,
        )

    def capture_ambiguity(
        self,
        admission,
        category,
        reason,
        event_count,
        generation_count,
        evidence_sha256,
        group_count,
        descriptor_sha256,
    ) -> None:
        result = self._request(
            admission,
            "ambiguity",
            category=category,
            reason=reason,
            eventCount=event_count,
            generationCount=generation_count,
            evidenceSha256=evidence_sha256,
            groupCount=group_count,
            descriptorSha256=descriptor_sha256,
        )
        if set(result) != {"repeated"} or type(result["repeated"]) is not bool:
            raise ObjectVfsError("cloud_handoff_receipt_invalid")

    def seal(self, admission) -> None:
        result = self._request(admission, "seal")
        if set(result) != {"sealed"} or result["sealed"] is not True:
            raise ObjectVfsError("cloud_handoff_receipt_invalid")

    def processing_group(self, admission, epoch, category, group_id):
        result = self._request(
            admission,
            "processing_group",
            epoch=epoch,
            category=category,
            groupId=group_id,
        )
        try:
            return result["objectKey"], result["cipherBytes"], result["cipherSha256"]
        except KeyError as error:
            raise ObjectVfsError("cloud_handoff_group_invalid") from error

    def revoke(self, admission) -> None:
        if not self.bridge.closed:
            result = self._request(admission, "revoke")
            if set(result) != {"revoked"} or result["revoked"] is not True:
                raise ObjectVfsError("cloud_handoff_revoke_unverified")

    def cleanup(self, admission, _objects) -> bool:
        result = self._request(admission, "cleanup")
        return result.get("erased") is True
