"""Bounded loopback app identity and update checks, without phone data access."""

from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
import signal
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TextIO

STATUS_PORT = 48752
ALLOWED_ORIGINS = frozenset({
    "https://amplifai-database-engine.vercel.app", "https://amplifai.satoris.ai",
})
TOKEN_SECONDS = 60
MAX_TOKENS = 8
MAX_COMMAND_BYTES = 1024
MAX_RESPONSE_BYTES = 4096
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")
BUILD = re.compile(r"[1-9][0-9]{0,11}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
UPDATE_STATES = frozenset({
    "checking", "current", "available", "deferred", "installing", "failed", "manual",
})
IDENTITIES = frozenset({
    ("macos", "arm64", "macos-arm64", "ai.satoris.amplifai.phone.candidate"),
    ("macos", "x86_64", "macos-intel-test", "ai.satoris.amplifai.phone.intel-candidate"),
    ("windows", "x86_64", "windows-x64-test", "ai.satoris.amplifai.phone.windows-candidate"),
})


@dataclass(frozen=True)
class CompanionIdentity:
    platform: str
    architecture: str
    channel: str
    bundle_id: str
    version: str
    build: str

    def __post_init__(self) -> None:
        if (any(type(item) is not str for item in vars(self).values())
                or (self.platform, self.architecture, self.channel, self.bundle_id) not in IDENTITIES
                or not VERSION.fullmatch(self.version) or not BUILD.fullmatch(self.build)):
            raise ValueError("Invalid companion identity")

    def payload(self) -> dict[str, object]:
        return {"schema": 1, "product": "amplifai-phone", "platform": self.platform,
                "architecture": self.architecture, "channel": self.channel,
                "bundleId": self.bundle_id, "version": self.version, "build": self.build,
                "protocol": 1}


def validated_status(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "busy", "updateState", "availableVersion", "availableBuild",
    }:
        raise ValueError("Invalid status fields")
    version, build = value["availableVersion"], value["availableBuild"]
    if (type(value["busy"]) is not bool or value["updateState"] not in UPDATE_STATES
            or (version is None) != (build is None)
            or (version is not None and (type(version) is not str or not VERSION.fullmatch(version)))
            or (build is not None and (type(build) is not str or not BUILD.fullmatch(build)))):
        raise ValueError("Invalid status value")
    return dict(value)


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError("Duplicate field")
        value[key] = item
    return value


class _BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8
    allow_reuse_port = False

    def __init__(self, *args: object, **kwargs: object):
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows SO_REUSEADDR permits another listener to take this port.
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request: object, client_address: object) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: object, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class CompanionStatusServer:
    def __init__(self, identity: CompanionIdentity, status: Callable[[], dict[str, object]],
                 request_update_check: Callable[[], bool | None], *, port: int = STATUS_PORT,
                 native_setup_v2: bool = False):
        self.identity = identity
        self.status = status
        self.request_update_check = request_update_check
        self.port = port
        self.native_setup_v2 = native_setup_v2 is True and identity.platform == "windows"
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._httpd: _BoundedHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _issue_token(self, origin: str) -> str:
        with self._lock:
            now = time.monotonic()
            self._tokens = {key: value for key, value in self._tokens.items() if value[1] > now}
            while len(self._tokens) >= MAX_TOKENS:
                del self._tokens[next(iter(self._tokens))]
            token = secrets.token_urlsafe(32)
            self._tokens[token] = (origin, now + TOKEN_SECONDS)
            return token

    def _consume_token(self, token: str, origin: str) -> bool:
        with self._lock:
            candidate = self._tokens.pop(token, None)
        return (candidate is not None and candidate[1] > time.monotonic()
                and hmac.compare_digest(candidate[0], origin))

    def start(self) -> None:
        if self._httpd is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "AMPLIFai"
            sys_version = ""

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _origin(self) -> str | None:
                headers = getattr(self, "headers", None)
                origins = headers.get_all("Origin") if headers is not None else None
                return origins[0] if origins is not None and len(origins) == 1 else None

            def _authorized(self) -> bool:
                return (getattr(self, "headers", None) is not None
                        and self.client_address[0] == "127.0.0.1"
                        and self.headers.get_all("Host") == [f"127.0.0.1:{owner.port}"]
                        and self._origin() in ALLOWED_ORIGINS)

            def _reply(self, status: int, value: dict[str, object] | None = None,
                       *, preflight: str | None = None) -> None:
                body = b"" if status == 204 else json.dumps(value or {}, separators=(",", ":")).encode("ascii")
                if len(body) > MAX_RESPONSE_BYTES:
                    status, body = 503, b"{}"
                self.send_response(status)
                for name, text in (("Content-Type", "application/json; charset=utf-8"),
                                   ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                                   ("X-Content-Type-Options", "nosniff"), ("Connection", "close")):
                    self.send_header(name, text)
                if self._authorized():
                    self.send_header("Access-Control-Allow-Origin", self._origin())
                    self.send_header("Vary", "Origin")
                    if preflight:
                        self.send_header("Access-Control-Allow-Methods", preflight)
                        self.send_header("Access-Control-Allow-Headers", "Content-Type")
                        self.send_header("Access-Control-Allow-Private-Network", "true")
                        self.send_header("Access-Control-Max-Age", "0")
                self.end_headers()
                self.close_connection = True
                if body:
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError, TimeoutError):
                        self.close_connection = True

            def send_error(self, code: int, message: str | None = None,
                           explain: str | None = None) -> None:
                self._reply(code)

            def do_OPTIONS(self) -> None:
                method = {"/v1/status": "GET", "/v1/check-update": "POST"}.get(self.path)
                if self.path == "/v1/setup-capabilities" and (owner.identity.platform == "macos" or owner.native_setup_v2):
                    method = "GET"
                requested = self.headers.get_all("Access-Control-Request-Headers") or []
                headers = [name.strip().lower() for item in requested for name in item.split(",")]
                if (not self._authorized() or method is None
                        or self.headers.get_all("Access-Control-Request-Method") != [method]
                        or len(requested) > 1 or any(name != "content-type" for name in headers)):
                    self._reply(403)
                    return
                self._reply(204, preflight=method)

            def do_GET(self) -> None:
                if not self._authorized():
                    self._reply(403)
                    return
                if self.path == "/v1/setup-capabilities" and (owner.identity.platform == "macos" or owner.native_setup_v2):
                    identity = owner.identity.payload()
                    del identity["protocol"]
                    if owner.native_setup_v2:
                        identity["schema"] = 2
                    self._reply(200, {**identity, "protocols": ["phone-native-setup-v2" if owner.native_setup_v2 else "phone-native-setup-v1"]})
                    return
                if self.path != "/v1/status":
                    self._reply(403)
                    return
                try:
                    state = validated_status(owner.status())
                except (ValueError, TypeError):
                    self._reply(503)
                    return
                self._reply(200, {**owner.identity.payload(), **state,
                                 "updateCheckToken": owner._issue_token(self._origin()),
                                 "tokenExpiresInSeconds": TOKEN_SECONDS})

            def do_POST(self) -> None:
                if not self._authorized() or self.path != "/v1/check-update":
                    self._reply(403)
                    return
                lengths = self.headers.get_all("Content-Length")
                if (self.headers.get_all("Content-Type") != ["application/json"]
                        or self.headers.get_all("Transfer-Encoding") is not None
                        or lengths is None or len(lengths) != 1 or not lengths[0].isdigit()
                        or not 0 < int(lengths[0]) <= MAX_COMMAND_BYTES):
                    self._reply(400)
                    return
                try:
                    body = self.rfile.read(int(lengths[0]))
                    if len(body) != int(lengths[0]):
                        raise ValueError("Incomplete request")
                    value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
                    if (not isinstance(value, dict) or set(value) != {"schema", "token"}
                            or type(value["schema"]) is not int or value["schema"] != 1
                            or type(value["token"]) is not str or not TOKEN.fullmatch(value["token"])):
                        raise ValueError("Invalid request")
                except (ValueError, UnicodeError, TimeoutError):
                    self._reply(400)
                    return
                if not owner._consume_token(value["token"], self._origin()):
                    self._reply(403)
                    return
                try:
                    state = validated_status(owner.status())
                except (ValueError, TypeError):
                    self._reply(503)
                    return
                if state["busy"]:
                    self._reply(409, {"deferred": True})
                    return
                try:
                    accepted = owner.request_update_check()
                except (OSError, RuntimeError):
                    self._reply(503)
                    return
                if accepted is False:
                    self._reply(409, {"deferred": True})
                    return
                self._reply(202, {"checking": True})

        httpd = _BoundedHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = httpd.server_address[1]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._httpd, self._thread = None, None
        with self._lock:
            self._tokens.clear()


def run_status(identity: CompanionIdentity, source: TextIO, sink: TextIO) -> int:
    state: dict[str, object] = {"busy": True, "updateState": "checking",
                                "availableVersion": None, "availableBuild": None}
    output_lock = threading.Lock()

    def emit(event: dict[str, str]) -> None:
        with output_lock:
            sink.write(json.dumps(event, separators=(",", ":")) + "\n")
            sink.flush()

    server = CompanionStatusServer(identity, lambda: state,
        lambda: emit({"event": "update_check_requested"}))
    try:
        server.start()
    except OSError as error:
        import errno
        emit({"event": "status_unavailable", "code": "port_in_use"
              if error.errno == errno.EADDRINUSE else "status_failed"})
        return 4 if error.errno == errno.EADDRINUSE else 1
    emit({"event": "status_ready"})
    try:
        while line := source.readline(MAX_COMMAND_BYTES + 1):
            if not line.endswith("\n") or len(line.encode("utf-8")) > MAX_COMMAND_BYTES:
                raise ValueError("Invalid command size")
            command = json.loads(line, object_pairs_hook=_unique_object)
            if not isinstance(command, dict) or command.pop("action", None) != "set_status":
                raise ValueError("Invalid command")
            state = validated_status(command)
        return 0
    except (ValueError, TypeError, UnicodeError):
        emit({"event": "status_unavailable", "code": "status_failed"})
        return 2
    except KeyboardInterrupt:
        return 0
    finally:
        server.stop()


def main(argv: list[str] | None = None) -> int:
    import sys
    parser = argparse.ArgumentParser()
    for name in ("platform", "architecture", "channel", "bundle-id", "version", "build"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        identity = CompanionIdentity(**vars(args))
    except ValueError:
        parser.error("invalid companion identity")
    def terminate(_number: int, _frame: object) -> None:
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, terminate)
    return run_status(identity, sys.stdin, sys.stdout)
