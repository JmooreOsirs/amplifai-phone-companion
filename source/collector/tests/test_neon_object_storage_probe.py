"""Offline changed-seam checks for the unapproved Neon storage probe."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.parse
from dataclasses import replace
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from probes import neon_object_storage_probe as probe


class MissingObject(Exception):
    response: ClassVar[dict] = {"Error": {"Code": "404"}}


class ForbiddenBranch(Exception):
    response: ClassVar[dict] = {"ResponseMetadata": {"HTTPStatusCode": 403}}


class FakeSibling:
    def list_buckets(self):
        raise ForbiddenBranch


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[str, dict] = {}
        self.range_fault: str | None = None
        self.part_fault: int | None = None
        self.part_calls = 0

    def list_buckets(self):
        return {"Buckets": []}

    def head_bucket(self, *, Bucket):
        assert Bucket == probe.BUCKET
        return {}

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys):
        assert Bucket == probe.BUCKET and Prefix == probe.PREFIX and MaxKeys == 10
        return {
            "Contents": [{"Key": key} for key in self.objects if key.startswith(Prefix)]
        }

    def list_multipart_uploads(self, *, Bucket, Prefix, MaxUploads):
        assert Bucket == probe.BUCKET and Prefix == probe.PREFIX and MaxUploads == 10
        return {
            "Uploads": [
                {"Key": item["key"], "UploadId": upload_id}
                for upload_id, item in self.uploads.items()
                if item["key"].startswith(Prefix)
            ]
        }

    def put_object(self, *, Bucket, Key, Body):
        assert Bucket == probe.BUCKET
        self.objects[Key] = bytes(Body)
        return {}

    def head_object(self, *, Bucket, Key):
        assert Bucket == probe.BUCKET
        if Key not in self.objects:
            raise MissingObject
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, *, Bucket, Key, Range):
        assert Bucket == probe.BUCKET
        if Key not in self.objects:
            raise MissingObject
        start, end = map(int, Range.removeprefix("bytes=").split("-"))
        source = self.objects[Key]
        content = source[start : end + 1]
        fault = self.range_fault
        if fault == "short":
            content = content[:-1]
        status = 200 if fault == "status" else 206
        content_range = f"bytes {start + (fault == 'range')}-{end}/{len(source)}"
        return {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "ContentRange": content_range,
            "ContentLength": len(content) + 1 if fault == "length" else end - start + 1,
            "Body": io.BytesIO(content),
        }

    def create_multipart_upload(self, *, Bucket, Key):
        assert Bucket == probe.BUCKET
        self.uploads["synthetic-upload-1"] = {"key": Key, "parts": {}}
        return {"UploadId": "synthetic-upload-1"}

    def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        assert Bucket == probe.BUCKET and self.uploads[UploadId]["key"] == Key
        self.part_calls += 1
        if self.part_fault == PartNumber:
            raise KeyboardInterrupt
        self.uploads[UploadId]["parts"][PartNumber] = bytes(Body)
        return {"ETag": f'"synthetic-etag-{PartNumber}"'}

    def list_parts(self, *, Bucket, Key, UploadId, MaxParts):
        assert Bucket == probe.BUCKET and MaxParts == 10
        assert self.uploads[UploadId]["key"] == Key
        return {
            "Parts": [
                {"PartNumber": number, "ETag": f'"synthetic-etag-{number}"'}
                for number in sorted(self.uploads[UploadId]["parts"])
            ]
        }

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload):
        assert Bucket == probe.BUCKET
        ordered = MultipartUpload["Parts"]
        assert [part["PartNumber"] for part in ordered] == [1, 2, 3]
        self.objects[Key] = b"".join(
            self.uploads[UploadId]["parts"][part["PartNumber"]] for part in ordered
        )
        del self.uploads[UploadId]
        return {}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        assert Bucket == probe.BUCKET and self.uploads[UploadId]["key"] == Key
        del self.uploads[UploadId]
        return {}

    def delete_object(self, *, Bucket, Key):
        assert Bucket == probe.BUCKET
        self.objects.pop(Key, None)
        return {}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        assert operation in ("put_object", "get_object")
        assert Params["Bucket"] == probe.BUCKET
        return (
            f"{probe.ENDPOINT}/{probe.BUCKET}/{Params['Key']}?"
            + urllib.parse.urlencode(
                {
                    "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
                    "X-Amz-Expires": ExpiresIn,
                    "X-Amz-Signature": "synthetic-signature-not-a-credential",
                }
            )
        )


class NeonProbeOfflineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="amplifai-probe-test-")
        self.addCleanup(self.temporary.cleanup)
        self.ledger = probe.ProbeLedger(Path(self.temporary.name) / "ledger.json")
        self.ledger.save()
        self.client = FakeS3()
        self.transport = probe.Transport(self.client, self.ledger)

    def code(self, expected, action):
        with self.assertRaises(probe.ProbeFailure) as caught:
            action()
        self.assertEqual(str(caught.exception), expected)

    def test_full_synthetic_transport_and_cleanup(self):
        signed_calls = 0

        def signed_http(ledger, _url, method, *, body=None, range_header=None, cap=0):
            nonlocal signed_calls
            ledger.request()
            signed_calls += 1
            if method == "PUT":
                self.client.objects[probe.KEY_PRESIGNED] = body
                return 200, {}, b""
            if signed_calls == 2:
                data = self.client.objects[probe.KEY_PRESIGNED]
                ledger.download(len(data))
                self.assertEqual(range_header, f"bytes=0-{len(data) - 1}")
                self.assertEqual(cap, len(data))
                return (
                    206,
                    {"Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}"},
                    data,
                )
            return 403, {}, b""

        with patch.object(probe, "_signed_http", side_effect=signed_http):
            probe.run(
                self.client,
                FakeSibling(),
                self.ledger,
                wait=lambda seconds: self.assertEqual(seconds, 12),
            )
        self.assertEqual(self.client.part_calls, 4)
        self.assertLessEqual(self.ledger.requests, probe.MAX_REQUESTS - 16)
        self.assertLess(self.ledger.peak_retained, probe.MAX_RETAINED)
        self.assertLess(self.ledger.downloaded, probe.MAX_DOWNLOADED)
        self.transport.cleanup()
        self.assertEqual(self.client.objects, {})
        self.assertEqual(self.client.uploads, {})
        self.assertEqual(
            self.ledger.cleanup, "objects_and_uploads_not_visible_bucket_still_exists"
        )
        self.assertEqual(
            probe.ProbeLedger.load(self.ledger.path).keys_started,
            list(probe.KEYS[:1]) + [probe.KEY_PRESIGNED, probe.KEY_MULTIPART],
        )

    def test_strict_bounded_range_rejects_status_header_short_and_length(self):
        self.client.objects[probe.KEY_AAD] = b"0123456789"
        for fault, code in (
            ("status", "probe_range_status"),
            ("range", "probe_range_header"),
            ("short", "probe_range_short_or_long"),
            ("length", "probe_range_length"),
        ):
            with self.subTest(fault=fault):
                self.client.range_fault = fault
                self.code(
                    code, lambda: self.transport.read_range(probe.KEY_AAD, 2, 7, 10)
                )
        self.client.range_fault = None
        self.assertEqual(self.transport.read_range(probe.KEY_AAD, 2, 7, 10), b"234567")
        self.code(
            "probe_range_oversize",
            lambda: self.transport.read_range(
                probe.KEY_AAD, 0, probe.RANGE_BYTES, probe.RANGE_BYTES + 1
            ),
        )

    def test_binding_crypto_revocation_ttl_and_url_scope(self):
        source = probe.binding()
        key = b"k" * 32
        payload = b"synthetic source bytes"
        sealed = probe.seal_synthetic(payload, key, b"n" * 12)
        self.assertEqual(
            probe.open_synthetic(sealed, key, source, len(payload)), payload
        )
        for changed in (
            replace(source, owner_id="5dfd97e5-ef82-4e49-8e1b-5a5f2433bcfa"),
            replace(source, phone_id="ffda4fd9-9cde-45f7-8bbc-6ac41bf5f802"),
            replace(source, attempt_id="0ecc2d47-8a14-440f-b0f5-686307ea75c1"),
            replace(source, source="calls"),
            replace(source, source_set_id="4e69ec3b-573f-4fd8-8324-f182cd99d8be"),
        ):
            self.code(
                "probe_binding_denied",
                lambda changed=changed: probe.open_synthetic(
                    sealed, key, changed, len(payload)
                ),
            )
        corrupt = bytearray(sealed)
        corrupt[-1] ^= 1
        self.code(
            "probe_ciphertext_invalid",
            lambda: probe.open_synthetic(bytes(corrupt), key, source, len(payload)),
        )
        self.code(
            "probe_ciphertext_invalid",
            lambda: probe.open_synthetic(sealed, b"w" * 32, source, len(payload)),
        )
        guard = probe.GrantGuard(source, 60)
        guard.admit(source)
        guard.revoked = True
        self.code("probe_grant_revoked", lambda: guard.admit(source))
        self.code(
            "probe_grant_expired", lambda: probe.GrantGuard(source, 0).admit(source)
        )
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": probe.BUCKET, "Key": probe.KEY_AAD},
            ExpiresIn=10,
        )
        probe._safe_presigned_url(url, probe.KEY_AAD, 10)
        self.code(
            "probe_url_host",
            lambda: probe._safe_presigned_url(
                url.replace("neon.tech", "evil.test"), probe.KEY_AAD, 10
            ),
        )
        self.code(
            "probe_url_path_style",
            lambda: probe._safe_presigned_url(
                url.replace(f"/{probe.BUCKET}/", "/wrong/"), probe.KEY_AAD, 10
            ),
        )

    def test_cancellation_restart_cleanup_and_scope(self):
        payload = b"x" * probe.MULTIPART_BYTES
        self.client.part_fault = 2
        with self.assertRaises(KeyboardInterrupt):
            self.transport.multipart(probe.KEY_MULTIPART, payload)
        self.assertTrue(self.client.uploads)
        recovered = probe.ProbeLedger.load(self.ledger.path)
        probe.Transport(self.client, recovered).cleanup()
        self.assertEqual(self.client.uploads, {})
        self.assertEqual(self.client.objects, {})
        bad = json.loads(self.ledger.path.read_text())
        bad["branch"] = "br-restless-sea-avk4ijyb"
        self.ledger.path.write_text(json.dumps(bad))
        self.code(
            "probe_ledger_scope_invalid",
            lambda: probe.ProbeLedger.load(self.ledger.path),
        )

    def test_budgets_and_existing_prefix_fail_closed(self):
        self.client.objects[probe.KEY_AAD] = b"old"
        self.code("probe_prefix_not_empty", self.transport.assert_empty_prefix)
        self.code(
            "probe_storage_budget",
            lambda: self.ledger.add_object(probe.KEY_AAD, probe.MAX_RETAINED + 1),
        )
        self.code(
            "probe_egress_budget",
            lambda: self.ledger.download(probe.MAX_DOWNLOADED + 1),
        )
        self.ledger.requests = probe.MAX_REQUESTS - 16
        self.code("probe_request_budget", self.ledger.request)

    def test_sibling_namespace_denial_is_required_before_writes(self):
        self.code(
            "probe_sibling_scope_failed",
            lambda: probe.assert_sibling_denied(self.client, self.ledger),
        )
        probe.assert_sibling_denied(FakeSibling(), self.ledger)
        self.assertEqual(self.client.objects, {})

    def test_default_command_is_local_plan_only(self):
        with (
            patch.object(sys, "argv", ["neon_object_storage_probe.py"]),
            patch.object(
                probe, "_client", side_effect=AssertionError("network attempted")
            ),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            self.assertEqual(probe.main(), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["mode"], "offline_plan")
        self.assertEqual(plan["branch"], probe.BRANCH)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
