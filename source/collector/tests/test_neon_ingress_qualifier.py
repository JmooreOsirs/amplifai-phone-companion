"""Offline checks for the unapproved, synthetic ingress operator."""

from __future__ import annotations

import base64
import datetime
import hashlib
import http.client
import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from probes import neon_ingress_operator as operator
from probes import neon_ingress_qualifier as probe


class FakeWire:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.branch = {
            "id": probe.BRANCH,
            "project_id": probe.PROJECT,
            "current_state": "ready",
            "primary": False,
            "default": False,
        }
        self.buckets: list[dict] = []
        self.credentials: list[dict] = []
        self.now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        self.range_fault: str | None = None
        self.condition_supported = True
        self.digest_supported = True
        self.signed_header_supported = True
        self.inflight_finishes_late = True
        self.fail_delete_once = False
        self.fail_bucket_delete_once = False
        self.lose_put_response_once = False
        self.lose_credential_response_once = False
        self.lose_bucket_response_once = False
        self.sent = 0
        self.issued: dict[str, float] = {}

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def send(
        self, url, method, headers, body, cap, delay_after_headers=0, on_download=None
    ):
        reply = self._send(url, method, headers, body, cap, delay_after_headers)
        if on_download:
            on_download(len(reply.body))
        return reply

    def _send(self, url, method, headers, body, cap, delay_after_headers):
        self.sent += 1
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname == probe.CONTROL_HOST:
            return self._control(parsed.path, method, body)
        query = urllib.parse.parse_qs(parsed.query)
        when = self.issued.get(url)
        if when is None:
            raise AssertionError("unsigned fake URL")
        expiry = int(query["X-Amz-Expires"][0])
        if self.now > when + expiry:
            return probe.Reply(403, {}, b"")
        signed = set(query["X-Amz-SignedHeaders"][0].split(";"))
        key = parsed.path.removeprefix(f"/{probe.BUCKET}/")
        if key not in probe.KEYS:
            return probe.Reply(403, {}, b"")
        if delay_after_headers:
            self.advance(delay_after_headers)
            if not self.inflight_finishes_late:
                return probe.Reply(403, {}, b"")
        if method == "PUT":
            checksum = headers.get("x-amz-checksum-sha256")
            expected = base64.b64encode(hashlib.sha256(body).digest()).decode()
            if (
                self.signed_header_supported
                and checksum
                and key == probe.KEYS[1]
                and checksum != expected
            ):
                return probe.Reply(403, {}, b"")
            if self.digest_supported and checksum != expected:
                return probe.Reply(400, {}, b"")
            if (
                self.condition_supported
                and "if-none-match" in signed
                and key in self.objects
            ):
                return probe.Reply(412, {}, b"")
            self.objects[key] = bytes(body)
            if self.lose_put_response_once:
                self.lose_put_response_once = False
                raise OSError("synthetic transport response lost")
            return probe.Reply(200, {}, b"")
        if method == "DELETE":
            if self.fail_delete_once:
                self.fail_delete_once = False
                return probe.Reply(503, {}, b"")
            self.objects.pop(key, None)
            return probe.Reply(204, {}, b"")
        if method == "HEAD":
            return probe.Reply(200 if key in self.objects else 404, {}, b"")
        if key not in self.objects:
            return probe.Reply(404, {}, b"")
        start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
        content = self.objects[key][start : end + 1]
        status = {
            "status": 200,
            "forbidden": 403,
            "unsatisfiable": 416,
        }.get(self.range_fault, 206)
        range_value = f"bytes {start + (self.range_fault == 'header')}-{end}/{len(self.objects[key])}"
        if self.range_fault == "malformed":
            range_value = "private unsafe header text"
        if self.range_fault == "short":
            content = content[:-1]
        if self.range_fault == "corrupt":
            content = b"X" + content[1:]
        length = len(content) + 1 if self.range_fault == "length" else end - start + 1
        return probe.Reply(
            status,
            {"Content-Range": range_value, "Content-Length": str(length)},
            content[: cap + 1],
        )

    def _control(self, path, method, body):
        suffix = path.removeprefix(probe.CONTROL_BASE)
        if suffix == "" and method == "GET":
            value = {"branch": self.branch}
        elif suffix == "/buckets" and method == "GET":
            value = {"buckets": self.buckets}
        elif suffix == "/credentials" and method == "GET":
            value = {"credentials": self.credentials}
        elif suffix == "/credentials" and method == "POST":
            item = json.loads(body)
            credential = {
                "name": item["name"],
                "token_id": "nak_live_" + "a" * 32,
                "s3_secret_access_key": "nsk_live_" + "b" * 32,
                "branch_id": probe.BRANCH,
                "scopes": item["scopes"],
            }
            self.credentials.append(
                {
                    key: val
                    for key, val in credential.items()
                    if key != "s3_secret_access_key"
                }
            )
            if self.lose_credential_response_once:
                self.lose_credential_response_once = False
                raise OSError("synthetic credential response lost")
            value = {"credential": credential}
        elif suffix == "/buckets" and method == "POST":
            self.buckets.append(json.loads(body))
            if self.lose_bucket_response_once:
                self.lose_bucket_response_once = False
                raise OSError("synthetic bucket response lost")
            value = {"bucket": self.buckets[-1]}
        elif suffix == f"/buckets/{probe.BUCKET}" and method == "DELETE":
            if self.fail_bucket_delete_once:
                self.fail_bucket_delete_once = False
                return probe.Reply(503, {}, b"")
            if self.objects:
                return probe.Reply(409, {}, b"")
            self.buckets.clear()
            value = {}
        elif suffix.startswith("/credentials/") and method == "DELETE":
            for credential in self.credentials:
                credential["revoked_at"] = "synthetic-now"
            value = {}
        else:
            raise AssertionError("unexpected control path")
        return probe.Reply(200, {}, json.dumps(value).encode())


class FakeSigner:
    def __init__(self, actual, wire):
        self.actual, self.wire = actual, wire

    def generate_presigned_url(self, *args, **kwargs):
        url = self.actual.generate_presigned_url(*args, **kwargs)
        self.wire.issued[url] = self.wire.now
        return url


class IngressProbeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ingress-qualifier-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "ledger.json"
        self.ledger = probe.Ledger(self.path)
        self.ledger.save()
        self.wire = FakeWire()
        self.signer = FakeSigner(
            probe._make_signer("nak_live_" + "a" * 32, "nsk_live_" + "b" * 32),
            self.wire,
        )
        self.s3 = probe.S3Probe(self.signer, self.wire, self.ledger)

    def test_full_fake_rehearsal_preserves_tombstone_and_no_secret(self):
        probe.exercise(self.s3, wait=self.wire.advance)
        self.s3.cleanup()
        self.assertEqual(self.ledger.observations["condition"], "observed_supported")
        self.assertEqual(
            self.ledger.observations["signed_header"], "observed_supported"
        )
        self.assertEqual(
            self.ledger.observations["payload_digest"], "observed_supported"
        )
        self.assertEqual(self.ledger.observations["strict_range"], "observed_supported")
        self.assertEqual(self.ledger.observations["resurrection"], "observed_supported")
        self.assertEqual(
            self.ledger.observations["expired_grant"], "observed_supported"
        )
        self.assertEqual(self.ledger.cleanup, "currently_absent_tombstone_pending")
        self.assertFalse(self.wire.objects)
        self.assertLessEqual(self.ledger.s3_attempts, probe.MAX_S3_ATTEMPTS)
        self.assertLessEqual(self.ledger.downloaded, probe.MAX_DOWNLOADED)
        saved = self.path.read_text()
        self.assertNotIn("X-Amz-", saved)
        self.assertNotIn("nsk_live_", saved)
        self.assertIsNone(json.loads(saved)["hosted_ingress_barrier"])

    def test_provider_semantics_can_fail_without_becoming_pass(self):
        self.wire.condition_supported = False
        self.wire.digest_supported = False
        self.wire.inflight_finishes_late = False
        probe.exercise(self.s3, wait=self.wire.advance)
        self.assertEqual(self.ledger.observations["condition"], "observed_unsupported")
        self.assertEqual(
            self.ledger.observations["payload_digest"], "observed_unsupported"
        )
        self.assertEqual(
            self.ledger.observations["late_inflight"], "observed_ambiguous"
        )
        self.s3.cleanup()

    def test_strict_range_faults_and_digest(self):
        payload = probe.synthetic_bytes(b"range-negative", probe.RANGE_BYTES)
        self.assertEqual(self.s3.put(probe.KEYS[3], payload).status, 200)
        for fault, code in (
            ("status", "range_status"),
            ("header", "range_header"),
            ("length", "range_length"),
            ("short", "range_length"),
        ):
            self.wire.range_fault = fault
            before = self.ledger.downloaded
            with self.assertRaisesRegex(probe.ProbeFailure, code):
                self.s3.range(probe.KEYS[3], 0, 127, len(payload))
            self.assertGreater(self.ledger.downloaded, before)
        self.wire.range_fault = "corrupt"
        with self.assertRaisesRegex(probe.ProbeFailure, "range_digest"):
            self.s3.verify(probe.KEYS[3], payload)

    def test_range_failure_journals_safe_response_before_rejection(self):
        payload = probe.synthetic_bytes(b"range-observation", probe.RANGE_BYTES)
        self.assertEqual(self.s3.put(probe.KEYS[3], payload).status, 200)
        for fault, status in (("status", 200), ("forbidden", 403), ("unsatisfiable", 416)):
            self.wire.range_fault = fault
            with self.subTest(fault=fault), self.assertRaisesRegex(
                probe.ProbeFailure, "range_status"
            ):
                self.s3.range(probe.KEYS[3], 7, 14, len(payload))
            observed = probe.Ledger.load(self.path).last_range_response
            self.assertEqual(observed["status"], status)
            self.assertEqual(observed["requested_start"], 7)
            self.assertEqual(observed["requested_end"], 14)
            self.assertEqual(observed["expected_total"], len(payload))
            self.assertEqual(observed["returned_content_range"], f"bytes 7-14/{len(payload)}")
            self.assertEqual(observed["returned_content_length"], 8)
            self.assertEqual(observed["body_bytes_read"], 8)
        self.wire.range_fault = "malformed"
        with self.assertRaisesRegex(probe.ProbeFailure, "range_header"):
            self.s3.range(probe.KEYS[3], 7, 14, len(payload))
        self.assertEqual(
            probe.Ledger.load(self.path).last_range_response["returned_content_range"],
            "invalid",
        )
        self.assertNotIn("private unsafe header text", self.path.read_text())
        self.wire.range_fault = None
        self.assertEqual(self.s3.range(probe.KEYS[3], 7, 14, len(payload)), payload[7:15])
        self.assertEqual(probe.Ledger.load(self.path).last_range_response["status"], 206)
        saved = self.path.read_text()
        self.assertNotIn("X-Amz-", saved)
        self.assertNotIn(probe.KEYS[3], saved.split('"last_range_response"')[1])
        unsafe = json.loads(saved)
        unsafe["last_range_response"]["status"] = "403 secret"
        self.path.write_text(json.dumps(unsafe))
        with self.assertRaisesRegex(probe.ProbeFailure, "ledger_range_response"):
            probe.Ledger.load(self.path)

    def test_http_error_exposes_only_range_metadata_without_error_body(self):
        from email.message import Message
        from urllib.error import HTTPError

        metadata = Message()
        metadata["Content-Range"] = "bytes */131072"
        metadata["Content-Length"] = "128"
        metadata["X-Secret"] = "do-not-record"
        error = HTTPError(probe.ENDPOINT, 416, "denied", metadata, io.BytesIO(b"private"))

        class FailingOpener:
            def open(self, *_args, **_kwargs):
                raise error

        with patch("urllib.request.build_opener", return_value=FailingOpener()):
            reply = probe.HTTPSWire().send(probe.ENDPOINT, "GET", {}, None, 16)
        self.assertEqual(reply.status, 416)
        self.assertEqual(reply.headers["Content-Range"], "bytes */131072")
        self.assertEqual(reply.headers["Content-Length"], "128")
        self.assertNotIn("X-Secret", reply.headers)
        self.assertEqual(reply.body, b"")
        self.ledger.record_range_response(7, 14, 131072, reply)
        self.assertEqual(
            probe.Ledger.load(self.path).last_range_response["returned_content_range"],
            "bytes */131072",
        )
        self.assertEqual(
            probe.Ledger.load(self.path).last_range_response["body_bytes_read"], 0
        )

    def test_real_request_serializes_the_exact_signed_range_without_network(self):
        class RangeResponse:
            status = 206
            headers = {
                "Content-Range": "bytes 7-14/128",
                "Content-Length": "8",
            }

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _cap):
                return b"12345678"

        case = self

        class CapturingOpener:
            def open(self, request, **_kwargs):
                case.assertEqual(request.get_header("Range"), "bytes=7-14")
                signed = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(request.full_url).query
                )["X-Amz-SignedHeaders"][0]
                case.assertIn("range", signed.split(";"))
                return RangeResponse()

        with patch("urllib.request.build_opener", return_value=CapturingOpener()):
            actual = probe.S3Probe(
                self.signer.actual, probe.HTTPSWire(), self.ledger
            ).range(probe.KEYS[3], 7, 14, 128)
        self.assertEqual(actual, b"12345678")

    def test_budget_counts_before_wire_and_preserves_inventory(self):
        self.ledger.s3_attempts = probe.MAX_S3_ATTEMPTS - probe.RESERVED_S3_CLEANUP
        self.ledger.save()
        before = self.wire.sent
        with self.assertRaisesRegex(probe.ProbeFailure, "s3_budget"):
            self.s3.put(probe.KEYS[0], b"synthetic")
        self.assertEqual(self.wire.sent, before)
        self.assertEqual(self.ledger.inventory[probe.KEYS[0]], len(b"synthetic"))
        self.ledger.phase = "cleanup"
        self.s3.cleanup()
        self.assertFalse(self.wire.objects)

    def test_lost_put_response_keeps_exact_cleanup_key_and_attempt_count(self):
        self.wire.lose_put_response_once = True
        with self.assertRaises(OSError):
            self.s3.put(probe.KEYS[0], b"synthetic")
        self.assertEqual(self.ledger.s3_attempts, 1)
        self.assertEqual(self.ledger.inventory, {probe.KEYS[0]: 9})
        self.assertIn(probe.KEYS[0], self.wire.objects)
        self.s3.cleanup()
        self.assertFalse(self.wire.objects)
        self.assertEqual(self.ledger.s3_attempts, 3)

    def test_oversize_and_partial_body_bytes_are_accounted_before_rejection(self):
        self.ledger.reserve_response(8)
        with self.assertRaisesRegex(probe.ProbeFailure, "response_oversize"):
            probe.HTTPSWire._read(io.BytesIO(b"123456789"), 8, self.ledger.receive)
        self.assertEqual(self.ledger.downloaded, 9)

        class Partial:
            def read(self, _amount):
                raise http.client.IncompleteRead(b"abc", 5)

        with self.assertRaisesRegex(probe.ProbeFailure, "response_incomplete"):
            probe.HTTPSWire._read(Partial(), 8, self.ledger.receive)
        self.assertEqual(self.ledger.downloaded, 12)

    def test_download_cleanup_reserve_and_absolute_restart_deadline(self):
        self.ledger.downloaded = probe.MAX_DOWNLOADED - probe.RESERVED_DOWNLOAD_CLEANUP
        self.ledger.save()
        before = self.wire.sent
        with self.assertRaisesRegex(probe.ProbeFailure, "download_budget"):
            self.s3.range(probe.KEYS[0], 0, 0, 1)
        self.assertEqual(self.wire.sent, before)
        self.ledger.begin_cleanup()
        self.assertEqual(self.s3._send("HEAD", probe.KEYS[0], cap=0).status, 404)
        self.ledger.start_epoch = probe.time.time() - (
            probe.ACTIVE_SECONDS + probe.CLEANUP_SECONDS + 1
        )
        self.ledger.cleanup_start_epoch = None
        self.ledger.save()
        loaded = probe.Ledger.load(self.path)
        before = self.wire.sent
        with self.assertRaisesRegex(probe.ProbeFailure, "operator_deadline"):
            loaded.attempt("s3")
        self.assertEqual(self.wire.sent, before)
        loaded.attempt("control", emergency_revoke=True)

    def test_control_and_escrow_restart_cleanup(self):
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        escrow = operator.CredentialEscrow(
            self.path.with_suffix(".sealed"),
            "synthetic recovery phrase longer than twenty four",
        )
        access_id, secret = control.provision(escrow)
        self.assertEqual((access_id, secret), escrow.read())
        self.assertNotIn(secret, self.path.read_text())
        self.assertNotIn(secret.encode(), escrow.path.read_bytes())
        self.assertEqual(len(self.wire.buckets), 1)
        self.ledger = probe.Ledger.load(self.path)
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        control.cleanup_resources(objects_currently_absent=True)
        self.assertFalse(self.wire.buckets)
        self.assertTrue(self.wire.credentials[0]["revoked_at"])
        self.assertLessEqual(self.ledger.control_attempts, probe.MAX_CONTROL_ATTEMPTS)

    def test_failed_sweep_keeps_scoped_credential_for_bounded_retry(self):
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        escrow = operator.CredentialEscrow(
            self.path.with_suffix(".sealed"),
            "synthetic recovery phrase longer than twenty four",
        )
        control.provision(escrow)
        self.assertEqual(self.s3.put(probe.KEYS[0], b"synthetic").status, 200)
        self.wire.fail_delete_once = True
        with self.assertRaisesRegex(probe.ProbeFailure, "delete_failed"):
            self.s3.cleanup()
        control.cleanup_resources(objects_currently_absent=False)
        self.assertEqual(self.ledger.cleanup, "retry_required_before_deadline")
        self.assertFalse(self.wire.credentials[0].get("revoked_at"))
        self.ledger = probe.Ledger.load(self.path)
        access_id, secret = escrow.read()
        self.s3 = probe.S3Probe(
            FakeSigner(probe._make_signer(access_id, secret), self.wire),
            self.wire,
            self.ledger,
        )
        self.s3.cleanup()
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        control.cleanup_resources(objects_currently_absent=True)
        self.assertFalse(self.wire.objects)
        self.assertFalse(self.wire.buckets)
        self.assertTrue(self.wire.credentials[0]["revoked_at"])

    def test_lost_control_create_responses_reconcile_exact_resources(self):
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        escrow = operator.CredentialEscrow(
            self.path.with_suffix(".sealed"),
            "synthetic recovery phrase longer than twenty four",
        )
        self.wire.lose_credential_response_once = True
        with self.assertRaises(OSError):
            control.provision(escrow)
        self.assertTrue(self.ledger.credential_create_attempted)
        self.assertFalse(escrow.path.exists())
        control.cleanup_resources(objects_currently_absent=True)
        self.assertTrue(self.wire.credentials[0]["revoked_at"])

        self.wire.credentials.clear()
        self.ledger = probe.Ledger(self.path)
        self.ledger.save()
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        self.wire.lose_bucket_response_once = True
        with self.assertRaises(OSError):
            control.provision(escrow)
        self.assertTrue(self.ledger.bucket_create_attempted)
        self.assertTrue(escrow.path.exists())
        control.cleanup_resources(objects_currently_absent=True)
        self.assertFalse(self.wire.buckets)
        self.assertTrue(self.wire.credentials[0]["revoked_at"])

    def test_cleanup_deadline_revokes_even_with_visible_object(self):
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        escrow = operator.CredentialEscrow(
            self.path.with_suffix(".sealed"),
            "synthetic recovery phrase longer than twenty four",
        )
        control.provision(escrow)
        self.ledger.inventory_before_put(probe.KEYS[0], 1)
        self.wire.objects[probe.KEYS[0]] = b"x"
        self.ledger.phase = "cleanup"
        self.ledger.start_epoch = probe.time.time() - (
            probe.ACTIVE_SECONDS + probe.CLEANUP_SECONDS + 1
        )
        self.ledger.cleanup_start_epoch = None
        self.ledger.save()
        control.cleanup_resources(objects_currently_absent=False)
        self.assertEqual(
            self.ledger.cleanup, "manual_object_recovery_credential_revoked"
        )
        self.assertTrue(self.wire.credentials[0]["revoked_at"])
        self.assertTrue(self.wire.buckets)

    def test_bucket_delete_retry_retains_temporary_credential(self):
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        escrow = operator.CredentialEscrow(
            self.path.with_suffix(".sealed"),
            "synthetic recovery phrase longer than twenty four",
        )
        control.provision(escrow)
        self.wire.fail_bucket_delete_once = True
        control.cleanup_resources(objects_currently_absent=True)
        self.assertEqual(self.ledger.cleanup, "retry_required_before_deadline")
        self.assertFalse(self.wire.credentials[0].get("revoked_at"))
        control.cleanup_resources(objects_currently_absent=True)
        self.assertFalse(self.wire.buckets)
        self.assertTrue(self.wire.credentials[0]["revoked_at"])

    def test_cross_bucket_or_unapproved_control_path_is_denied_before_request(self):
        before = self.wire.sent
        with self.assertRaisesRegex(probe.ProbeFailure, "signed_url_key"):
            probe._safe_url(
                "https://br-round-haze-av0a2y4r.storage.c-11.us-east-1.aws.neon.tech/"
                "wrong-bucket/key?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=10"
                "&X-Amz-Signature=synthetic&X-Amz-SignedHeaders=host",
                probe.KEYS[0],
                {"host"},
            )
        control = operator.NeonControl("synthetic-bearer", self.ledger, self.wire)
        with self.assertRaisesRegex(probe.ProbeFailure, "control_path"):
            control.call("GET", "/../main")
        self.assertEqual(self.wire.sent, before)

    def test_no_approved_identity_or_tty_never_reaches_provider(self):
        with (
            patch(
                "sys.argv",
                [
                    "probe",
                    "--approved-live",
                    "--approved-branch",
                    "wrong",
                    "--approved-bucket",
                    probe.BUCKET,
                    "--ledger",
                    str(self.path),
                ],
            ),
            self.assertRaisesRegex(probe.ProbeFailure, "approval_identity"),
        ):
            operator.main()
        self.assertEqual(self.wire.sent, 0)


if __name__ == "__main__":
    unittest.main()
