import http.client
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time
import unittest

from amplifai_phone.companion_status import (
    ALLOWED_ORIGINS, CompanionIdentity, CompanionStatusServer, MAX_TOKENS,
    validated_status,
)

ORIGIN = "https://amplifai.satoris.ai"
OTHER_ORIGIN = "https://amplifai-database-engine.vercel.app"
IDENTITY = CompanionIdentity("macos", "arm64", "macos-arm64",
    "ai.satoris.amplifai.phone.candidate", "2026.10.14-rc25", "26101425")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.state = {"busy": False, "updateState": "current",
                      "availableVersion": None, "availableBuild": None}
        self.checks = 0
        self.server = CompanionStatusServer(IDENTITY, lambda: self.state, self.check, port=0)
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def check(self):
        self.checks += 1

    def request(self, method="GET", path="/v1/status", *, origin=ORIGIN, body=None,
                extra=(), content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=3)
        connection.putrequest(method, path, skip_host=True)
        connection.putheader("Host", f"127.0.0.1:{self.server.port}")
        if origin is not None:
            connection.putheader("Origin", origin)
        if body is not None:
            connection.putheader("Content-Type", content_type)
            connection.putheader("Content-Length", str(len(body)))
        for name, value in extra:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        status, headers, result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, headers, result

    def token(self, origin=ORIGIN):
        status, _, body = self.request(origin=origin)
        self.assertEqual(status, 200)
        return json.loads(body)["updateCheckToken"]

    def post(self, token, *, origin=ORIGIN, **extra):
        body = json.dumps({"schema": 1, "token": token, **extra}).encode()
        return self.request("POST", "/v1/check-update", origin=origin, body=body)

    def test_status_is_bounded_and_has_no_phone_or_path_fields(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertLess(len(body), 4096)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Access-Control-Allow-Origin"], ORIGIN)
        value = json.loads(body)
        self.assertEqual(set(value), set(IDENTITY.payload()) | set(self.state) |
                         {"updateCheckToken", "tokenExpiresInSeconds"})
        self.assertEqual(value["build"], "26101425")
        self.assertRegex(value["updateCheckToken"], r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(value["tokenExpiresInSeconds"], 60)

    def test_exact_origins_host_and_route(self):
        for origin in (None, "null", "https://evil.example", ORIGIN + ".evil.example"):
            with self.subTest(origin=origin):
                status, headers, _ = self.request(origin=origin)
                self.assertEqual(status, 403)
                self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request(origin=OTHER_ORIGIN)[0], 200)
        self.assertEqual(self.request(extra=(("Host", "evil.example"),))[0], 403)
        self.assertEqual(self.request(extra=(("Origin", ORIGIN),))[0], 403)
        self.assertEqual(self.request(path="/v1/status?extra=1")[0], 403)
        self.assertEqual(self.request(path="/v1/metadata")[0], 403)

    def test_private_network_preflight(self):
        status, headers, body = self.request("OPTIONS", extra=(
            ("Access-Control-Request-Method", "GET"),
            ("Access-Control-Request-Private-Network", "true"),
        ))
        self.assertEqual((status, body), (204, b""))
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        self.assertEqual(headers["Access-Control-Max-Age"], "0")
        self.assertEqual(self.request("OPTIONS", extra=(
            ("Access-Control-Request-Method", "GET"),
            ("Access-Control-Request-Headers", "x-arbitrary"),
        ))[0], 403)

    def test_capability_is_one_use_and_origin_bound(self):
        token = self.token()
        self.assertEqual(self.post(token)[0], 202)
        self.assertEqual(self.checks, 1)
        self.assertEqual(self.post(token)[0], 403)
        token = self.token()
        self.assertEqual(self.post(token, origin=OTHER_ORIGIN)[0], 403)
        self.assertEqual(self.checks, 1)
        token = self.token()
        self.server._tokens[token] = (ORIGIN, time.monotonic() - 1)
        self.assertEqual(self.post(token)[0], 403)

    def test_busy_and_queue_rejection_never_start_a_check(self):
        token = self.token()
        self.state = {**self.state, "busy": True, "updateState": "deferred"}
        status, _, body = self.post(token)
        self.assertEqual((status, json.loads(body)), (409, {"deferred": True}))
        self.assertEqual(self.checks, 0)
        self.assertEqual(self.post(token)[0], 403)
        self.state = {**self.state, "busy": False}
        self.server.request_update_check = lambda: False
        self.assertEqual(self.post(self.token())[0], 409)

    def test_malformed_and_extra_fields_fail_closed(self):
        token = self.token()
        for body in (b"{}", b"[]", b'{"schema":1,"schema":1,"token":"' + token.encode() + b'"}',
                     json.dumps({"schema": True, "token": token}).encode(), b"x" * 1025):
            with self.subTest(body=body[:30]):
                self.assertEqual(self.request("POST", "/v1/check-update", body=body)[0], 400)
        self.assertEqual(self.post(token, install=True)[0], 400)
        self.assertEqual(self.request("POST", "/v1/check-update", body=b"{}",
                                     content_type="text/plain")[0], 400)
        self.assertEqual(self.checks, 0)

    def test_bad_local_status_does_not_claim_current_or_dispatch(self):
        token = self.token()
        self.state = {**self.state, "phoneRecords": ["private"]}
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.post(token)[0], 503)
        self.assertEqual(self.checks, 0)

    def test_failed_native_dispatch_is_unavailable_and_consumes_capability(self):
        def unavailable():
            raise OSError("Test-owned parent pipe closed")

        self.server.request_update_check = unavailable
        token = self.token()
        self.assertEqual(self.post(token)[0], 503)
        self.assertEqual(self.post(token)[0], 403)

    def test_capability_storage_and_listener_ownership_are_bounded(self):
        for _ in range(MAX_TOKENS + 5):
            self.token()
        self.assertEqual(len(self.server._tokens), MAX_TOKENS)
        other = CompanionStatusServer(IDENTITY, lambda: self.state, self.check,
                                      port=self.server.port)
        with self.assertRaises(OSError):
            other.start()
        self.assertEqual(self.request()[0], 200)

    def test_identity_and_available_release_are_strict(self):
        for change in ({"platform": "linux"}, {"architecture": "x86_64"},
                       {"bundle_id": "arbitrary"}, {"version": "bad version"},
                       {"build": "0"}, {"build": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                CompanionIdentity(**{**vars(IDENTITY), **change})
        with self.assertRaises(ValueError):
            validated_status({**self.state, "availableVersion": "2026.10.14-rc25"})

    @unittest.skipUnless(hasattr(socket, "SO_EXCLUSIVEADDRUSE"), "Windows exclusive socket ownership")
    def test_windows_reuse_socket_cannot_take_running_companion_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                other.bind(("127.0.0.1", self.server.port))
        self.assertEqual(self.request()[0], 200)


class HelperEntryTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "Non-Windows entry refusal")
    def test_windows_installer_mode_refuses_other_platform_without_device_imports(self):
        collector = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-S", str(collector / "agent_entry.py"),
                                 "install-verified-update"], capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(collector)}, timeout=5)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (2, "", ""))

    def test_status_entrypoint_needs_no_device_sdk_and_stops_on_stdin_close(self):
        collector = Path(__file__).resolve().parents[1]
        environment = {**os.environ, "PYTHONPATH": str(collector)}
        command = [sys.executable, "-S", str(collector / "agent_entry.py"), "companion-status",
                   "--platform", IDENTITY.platform, "--architecture", IDENTITY.architecture,
                   "--channel", IDENTITY.channel, "--bundle-id", IDENTITY.bundle_id,
                   "--version", IDENTITY.version, "--build", IDENTITY.build]
        update = {"action": "set_status", "busy": False, "updateState": "manual",
                  "availableVersion": None, "availableBuild": None}
        result = subprocess.run(command, input=json.dumps(update) + "\n", capture_output=True,
                                text=True, env=environment, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout), {"event": "status_ready"})
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
