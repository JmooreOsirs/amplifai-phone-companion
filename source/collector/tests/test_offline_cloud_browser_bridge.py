"""Exact local browser broker admission and capture, without account cookies."""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict

import test_offline_cloud_capture as capture_fixture
import test_offline_single_stage_capture as stage_fixture
from amplifai_phone.backup_watchdog import BackupWatchdog
from amplifai_phone.offline_cloud_browser_bridge import (
    ACTIVE_INACTIVITY_SECONDS,
    BrowserGateway,
    CloudBrowserBridge,
    CloudBrowserBridgeError,
)
from amplifai_phone.offline_object_vfs import ObjectVfsError
from amplifai_phone.offline_single_stage_capture import SingleStageCaptureLink
from amplifai_phone.workspace import SessionWorkspace


def http(bridge, path, *, method="GET", body=None, token=None, origin=None, host=None):
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Origin": origin or bridge.origin}
    if host is not None:
        headers["Host"] = host
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{bridge.port}{path}",
        data=payload,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            data = response.read()
            return response.status, json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        error.read()
        return error.code, None


def envelope(admission, start, terminal):
    value = asdict(admission)
    return {
        "ownerId": value["owner_id"],
        "phoneId": value["phone_id"],
        "attemptId": value["attempt_id"],
        "actionId": value["action_id"],
        "collectionReceiptId": value["collection_receipt_id"],
        "cloudConsentId": value["consent_receipt_id"],
        "categories": list(value["categories"]),
        "disclosureVersion": value["disclosure_version"],
        "startChallenge": start,
        "terminalChallenge": terminal,
    }


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.admission = capture_fixture.CaptureFixture(
            "test_real_dispatch_projection_readback_and_parser_parity"
        )
        self.admission.setUp()
        self.addCleanup(self.admission.temporary.cleanup)
        self.addCleanup(self.admission.tearDown)
        self.cancelled = False
        self.tick = [time.monotonic()]
        self.bridge = CloudBrowserBridge(
            lambda: setattr(self, "cancelled", True),
            port=0,
            now=lambda: self.tick[0],
            poll_seconds=0.02,
        )
        self.bridge.start()
        self.addCleanup(self.bridge.close)
        self.start = str(uuid.uuid4())
        self.terminal = str(uuid.uuid4())

    def pair(self):
        status, data = http(
            self.bridge,
            "/cloud/v1/pair",
            method="POST",
            body={
                "code": self.bridge.code,
                "admission": envelope(
                    self.admission.admission, self.start, self.terminal
                ),
            },
        )
        self.assertEqual(status, 200)
        return data["token"]

    def test_exact_origin_consent_code_one_use_and_expiry(self):
        input_value = {
            "code": self.bridge.code,
            "admission": envelope(self.admission.admission, self.start, self.terminal),
        }
        self.assertEqual(
            http(
                self.bridge,
                "/cloud/v1/pair",
                method="POST",
                body=input_value,
                origin="https://other.invalid",
            )[0],
            403,
        )
        self.assertEqual(
            http(
                self.bridge,
                "/cloud/v1/pair",
                method="POST",
                body=input_value,
                host=f"localhost:{self.bridge.port}",
            )[0],
            403,
        )
        wrong = {
            **input_value,
            "admission": {**input_value["admission"], "ownerId": "forged"},
        }
        self.assertEqual(
            http(self.bridge, "/cloud/v1/pair", method="POST", body=wrong)[0], 422
        )
        self.assertEqual(
            http(
                self.bridge,
                "/cloud/v1/pair",
                method="POST",
                body={**input_value, "code": "0" * 10},
            )[0],
            403,
        )
        token = self.pair()
        self.assertEqual(
            http(self.bridge, "/cloud/v1/pair", method="POST", body=input_value)[0], 410
        )
        self.assertEqual(http(self.bridge, "/cloud/v1/next", token="wrong")[0], 403)
        self.assertEqual(http(self.bridge, "/cloud/v1/next", token=token)[0], 204)
        self.tick[0] += ACTIVE_INACTIVITY_SECONDS + 1
        self.assertEqual(http(self.bridge, "/cloud/v1/next", token=token)[0], 410)

    def test_typed_reply_retry_and_cancel_revoke_pending(self):
        token = self.pair()
        gateway = BrowserGateway(self.bridge)
        response = []
        worker = threading.Thread(
            target=lambda: response.append(
                gateway.reserve(
                    self.admission.admission,
                    "contacts",
                    "main",
                    0,
                    str(uuid.uuid4()),
                    "a" * 24,
                    25,
                    "b" * 64,
                )
            )
        )
        worker.start()
        try:
            deadline = time.monotonic() + 3
            while True:
                status, request = http(self.bridge, "/cloud/v1/next", token=token)
                if status == 200 or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            self.assertEqual(status, 200)
            self.assertEqual(request["operation"], "reserve")
            data = {
                "requestId": request["requestId"],
                "attemptId": request["attemptId"],
                "operation": "reserve",
                "response": {
                    "objectKey": f"cloud-v1/{request['attemptId']}/contacts/main/0/{request['input']['groupId']}"
                },
            }
            self.assertEqual(
                http(
                    self.bridge,
                    "/cloud/v1/reply",
                    method="POST",
                    token=token,
                    body={**data, "attemptId": str(uuid.uuid4())},
                )[0],
                409,
            )
            self.assertEqual(
                http(
                    self.bridge,
                    "/cloud/v1/reply",
                    method="POST",
                    token=token,
                    body=data,
                )[0],
                200,
            )
            self.assertEqual(
                http(
                    self.bridge,
                    "/cloud/v1/reply",
                    method="POST",
                    token=token,
                    body=data,
                )[0],
                200,
            )
            self.assertEqual(
                http(
                    self.bridge,
                    "/cloud/v1/reply",
                    method="POST",
                    token=token,
                    body={**data, "response": {}},
                )[0],
                409,
            )
        finally:
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(response, [data["response"]["objectKey"]])
        self.assertEqual(
            http(self.bridge, "/cloud/v1/cancel", method="POST", token=token)[0], 200
        )
        self.assertTrue(self.cancelled)
        with self.assertRaises(CloudBrowserBridgeError):
            gateway.start(
                self.admission.admission, self.start, "a" * 64, "A1B2C3D4E5F6", "a" * 32
            )

    def test_empty_server_receipt_then_cancel_pending_start_fail_closed(self):
        token = self.pair()
        gateway = BrowserGateway(self.bridge)
        failures = []

        def start():
            try:
                gateway.start(
                    self.admission.admission,
                    self.start,
                    "a" * 64,
                    "A1B2C3D4E5F6",
                    "a" * 32,
                )
            except (ObjectVfsError, CloudBrowserBridgeError) as error:
                failures.append(str(error))

        first = threading.Thread(target=start)
        first.start()
        deadline = time.monotonic() + 3
        while True:
            status, request = http(self.bridge, "/cloud/v1/next", token=token)
            if status == 200 or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        self.assertEqual(status, 200)
        self.assertEqual(
            http(
                self.bridge,
                "/cloud/v1/reply",
                method="POST",
                token=token,
                body={
                    "requestId": request["requestId"],
                    "operation": "start",
                    "attemptId": request["attemptId"],
                    "response": {},
                },
            )[0],
            200,
        )
        first.join(timeout=3)
        self.assertFalse(first.is_alive())
        self.assertEqual(failures, ["cloud_handoff_receipt_invalid"])

        second = threading.Thread(target=start)
        second.start()
        deadline = time.monotonic() + 3
        while True:
            status, _request = http(self.bridge, "/cloud/v1/next", token=token)
            if status == 200 or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        self.assertEqual(status, 200)
        self.assertEqual(
            http(self.bridge, "/cloud/v1/cancel", method="POST", token=token)[0], 200
        )
        second.join(timeout=3)
        self.assertFalse(second.is_alive())
        self.assertEqual(
            failures, ["cloud_handoff_receipt_invalid", "cloud_handoff_broker_lost"]
        )
        self.assertTrue(self.cancelled)

    def test_cancel_during_pending_group_grant_denies_all_later_replies(self):
        token = self.pair()
        failures = []

        def grant():
            try:
                self.bridge.exchange(
                    "grant",
                    {
                        "attemptId": self.admission.admission.attempt_id,
                        "phoneId": self.admission.admission.phone_id,
                        "category": "contacts",
                        "role": "main",
                        "groupIndex": 0,
                        "groupId": str(uuid.uuid4()),
                    },
                )
            except CloudBrowserBridgeError as error:
                failures.append(error.code)

        worker = threading.Thread(target=grant)
        worker.start()
        deadline = time.monotonic() + 3
        while True:
            status, request = http(self.bridge, "/cloud/v1/next", token=token)
            if status == 200 or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        self.assertEqual(status, 200)
        self.assertEqual(request["operation"], "grant")
        self.assertEqual(
            http(self.bridge, "/cloud/v1/cancel", method="POST", token=token)[0], 200
        )
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, ["cloud_handoff_broker_lost"])
        self.assertEqual(
            http(
                self.bridge,
                "/cloud/v1/reply",
                method="POST",
                token=token,
                body={
                    "requestId": request["requestId"],
                    "attemptId": request["attemptId"],
                    "operation": "grant",
                    "response": {"objectKey": "forged"},
                },
            )[0],
            410,
        )


class CaptureWithBrowserBroker(unittest.IsolatedAsyncioTestCase):
    async def test_current_broker_receipts_gate_capture_before_source_read(self):
        fixture = capture_fixture.CaptureFixture(
            "test_real_dispatch_projection_readback_and_parser_parity"
        )
        fixture.setUp()
        self.addCleanup(fixture.temporary.cleanup)
        self.addCleanup(fixture.tearDown)
        objects = stage_fixture.FinalObjects(
            fixture.root / "broker-final-objects", fixture.admission.attempt_id
        )
        journal = stage_fixture.Journal(fixture.admission, objects)
        bridge = CloudBrowserBridge(
            lambda: fixture.authority.revoke(), port=0, poll_seconds=0.02
        )
        bridge.start()
        self.addCleanup(bridge.close)
        status, paired = http(
            bridge,
            "/cloud/v1/pair",
            method="POST",
            body={
                "code": bridge.code,
                "admission": envelope(
                    fixture.admission,
                    journal.start_challenge,
                    journal.terminal_challenge,
                ),
            },
        )
        self.assertEqual(status, 200)
        token = paired["token"]
        index_root = fixture.root / "broker-private-index"
        index_root.mkdir(mode=0o700)
        workspace = SessionWorkspace(fixture.root / "broker-workspace")
        with workspace:
            watchdog = BackupWatchdog()
            watchdog.start()
            wire = capture_fixture.Wire(fixture.transcript(fixture.inputs()))
            link = SingleStageCaptureLink(
                wire,
                workspace,
                watchdog,
                fixture.admission,
                fixture.authority,
                fixture.object_key,
                fixture.receipt_key,
                BrowserGateway(bridge),
                objects,
                index_root,
                journal.start_challenge,
                journal.terminal_challenge,
                "A1B2C3D4E5F6",
            )
            original_start = journal.start
            denied = True
            operations = []
            active = True

            def browser():
                nonlocal denied, active
                while active:
                    state, request = http(bridge, "/cloud/v1/next", token=token)
                    if state == 410:
                        break
                    if state != 200:
                        time.sleep(0.005)
                        continue
                    operation = request["operation"]
                    values = request["input"]
                    operations.append(operation)
                    if operation == "start":
                        if denied:
                            response = {"errorCode": "consent_stale"}
                            denied = False
                        else:
                            original_start(
                                fixture.admission,
                                values["challenge"],
                                values["claimSha256"],
                                values["captureMac"],
                                values["sessionNonce"],
                            )
                            response = {}
                    elif operation == "revoke":
                        journal.revoke(fixture.admission)
                        response = {}
                    else:
                        response = {"errorCode": "unsupported"}
                    http(
                        bridge,
                        "/cloud/v1/reply",
                        method="POST",
                        token=token,
                        body={
                            "requestId": request["requestId"],
                            "operation": operation,
                            "attemptId": request["attemptId"],
                            "response": response,
                        },
                    )

            broker = threading.Thread(target=browser, daemon=True)
            broker.start()
            try:
                with self.assertRaises(CloudBrowserBridgeError):
                    await link.run_capture()
                self.assertEqual(wire.largest_read, 0)
                self.assertEqual(objects.keys(), ())
                self.assertEqual(operations, ["start"])
            finally:
                active = False
                broker.join(timeout=3)
                bridge.close()


if __name__ == "__main__":
    unittest.main()
