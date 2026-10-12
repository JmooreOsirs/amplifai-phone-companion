"""Bounded exact signed-capability transport with a disposable HTTP object port."""

from __future__ import annotations

import hashlib
import time
import unittest
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from amplifai_phone.offline_cloud_capture import CaptureAdmission
from amplifai_phone.offline_cloud_signed_transport import (
    ExactCapabilityTransport,
    ObjectHttpReply,
)
from amplifai_phone.offline_grouped_object_vfs import RangeReply
from amplifai_phone.offline_object_vfs import ObjectVfsError
from amplifai_phone.offline_single_stage_capture import (
    AttemptCipherCache,
    ExactGrant,
    ProcessingRanges,
)

HOST = "br-round-haze-av0a2y4r.storage.c-11.us-east-1.aws.neon.tech"
BUCKET = "amplifai-offline-fixture"
SIGNATURE = "a" * 64


class Gateway:
    def __init__(self, admission, size, digest):
        self.bridge = type("Bridge", (), {"admission": admission})()
        self.admission = admission
        self.size = size
        self.digest = digest
        self.read_calls = 0

    def _signed_url(self, value, key, method):
        expected = (
            f"https://{HOST}/{BUCKET}/{key}?X-Amz-Expires=30&"
            f"X-Amz-SignedHeaders={'host%3Brange' if method == 'GET' else 'content-length%3Bhost'}"
            f"&X-Amz-Signature={SIGNATURE}"
        )
        if value != expected:
            raise ObjectVfsError("cloud_signed_location_changed")
        return value

    def read_grant(self, admission, category, role, index, group_id, start, end):
        self.read_calls += 1
        if (
            admission != self.admission
            or (category, role, index, group_id)
            != (
                "contacts",
                "main",
                0,
                self.group_id,
            )
            or start < 0
            or end >= self.size
        ):
            raise ObjectVfsError("cloud_read_denied")
        return (
            self.key,
            self.size,
            self.digest,
            self._signed_url(self.read_url, self.key, "GET"),
            time.time() + 30,
        )


class SignedTransportTest(unittest.TestCase):
    def setUp(self):
        ids = [str(uuid.uuid4()) for _ in range(6)]
        self.admission = CaptureAdmission(
            *ids,
            ("contacts",),
            "phone-cloud-source-v1",
            True,
        )
        self.group_id = str(uuid.uuid4())
        self.key = (
            f"cloud-v1/{self.admission.attempt_id}/contacts/main/0/{self.group_id}"
        )
        self.payload = b"synthetic-outer-gcm-ciphertext" * 5_000
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.gateway = Gateway(self.admission, len(self.payload), self.digest)
        self.gateway.group_id = self.group_id
        self.gateway.key = self.key
        self.gateway.read_url = self.url("GET")
        self.objects = {}
        self.put_calls = self.get_calls = 0
        self.bad_status = None
        self.short = False
        self.transport = ExactCapabilityTransport(
            self.admission, self.gateway, self.http
        )

    def url(self, method):
        signed_headers = "host%3Brange" if method == "GET" else "content-length%3Bhost"
        return (
            f"https://{HOST}/{BUCKET}/{self.key}?X-Amz-Expires=30&"
            f"X-Amz-SignedHeaders={signed_headers}&X-Amz-Signature={SIGNATURE}"
        )

    def http(self, method, url, headers, body):
        self.gateway._signed_url(url, self.key, method)
        if method == "PUT":
            self.put_calls += 1
            self.assertEqual(headers["Content-Length"], str(len(body)))
            self.objects[self.key] = body
            return ObjectHttpReply(204, None, 0, ())
        self.get_calls += 1
        start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
        payload = self.objects[self.key]
        fragment = payload[start : end + 1]
        if self.short:
            fragment = fragment[:-1]
        return ObjectHttpReply(
            self.bad_status or 206,
            f"bytes {start}-{end}/{len(payload)}",
            end - start + 1,
            (fragment[: 128 * 1024], fragment[128 * 1024 :])
            if len(fragment) > 128 * 1024
            else (fragment,),
        )

    def grant(self):
        return ExactGrant(
            self.key, len(self.payload), self.digest, time.time() + 30, self.url("PUT")
        )

    def test_exact_put_and_single_bounded_full_get(self):
        self.transport.put_granted(self.grant(), self.payload)
        self.assertEqual(self.transport.get_exact(self.key), self.payload)
        self.assertEqual((self.put_calls, self.get_calls), (1, 1))
        self.assertLessEqual(self.transport.peak_buffered_bytes, 1_048_704)
        self.assertEqual(
            self.transport.get_range(self.key, 5, 12, 8).body, self.payload[5:13]
        )

    def test_wrong_payload_location_response_and_short_range_fail_closed(self):
        with self.assertRaises(ObjectVfsError):
            self.transport.put_granted(self.grant(), self.payload[:-1])
        wrong = ExactGrant(
            self.key,
            len(self.payload),
            self.digest,
            time.time() + 30,
            self.url("PUT").replace(
                HOST, "br-other.storage.c-11.us-east-1.aws.neon.tech"
            ),
        )
        with self.assertRaises(ObjectVfsError):
            self.transport.put_granted(wrong, self.payload)
        self.transport.put_granted(self.grant(), self.payload)
        self.bad_status = 200
        with self.assertRaises(ObjectVfsError):
            self.transport.get_range(self.key, 0, 3, 4)
        self.bad_status = None
        self.short = True
        with self.assertRaises(ObjectVfsError):
            self.transport.get_range(self.key, 0, 3, 4)
        self.short = False
        self.objects[self.key] = b"changed" + self.payload[7:]
        with self.assertRaises(ObjectVfsError):
            self.transport.get_exact(self.key)
        with self.assertRaises(ObjectVfsError):
            self.transport.get_range("other/key", 0, 3, 4)

    def test_processing_reuses_bounded_authenticated_groups_before_grant_expiry(self):
        self.transport.put_granted(self.grant(), self.payload)

        class Worker:
            calls = 0
            revoked = False

            def processing_group(inner, admission, epoch, category, group_id):
                inner.calls += 1
                if inner.revoked:
                    raise ObjectVfsError("cloud_worker_revoked")
                if (admission, epoch, category, group_id) != (
                    self.admission,
                    1,
                    "contacts",
                    self.group_id,
                ):
                    raise ObjectVfsError("cloud_worker_stale")
                return self.key, len(self.payload), self.digest

        worker = Worker()
        ledger = SimpleNamespace(
            group=lambda _group_id: (
                None,
                None,
                len(self.payload),
                self.digest,
                None,
                self.key,
                "verified",
            )
        )
        link = SimpleNamespace(
            admission=self.admission,
            gateway=worker,
            backend=SimpleNamespace(ledgers={"contacts": ledger}, revoked=False),
            objects=self.transport,
        )
        ranges = ProcessingRanges(link, "contacts", 1)
        total = len(self.payload)
        first = ranges.get_group_range(self.group_id, 0, 7, total, 8)
        second = ranges.get_group_range(self.group_id, 8, 15, total, 8)
        self.assertEqual(
            (first.body, second.body), (self.payload[:8], self.payload[8:16])
        )
        self.assertEqual((worker.calls, self.get_calls), (3, 1))
        self.assertEqual(ranges.cache.current_bytes, total)
        self.assertLessEqual(ranges.peak_group_cache_bytes, 160 * 1024 * 1024)
        worker.revoked = True
        with self.assertRaises(ObjectVfsError):
            ranges.get_group_range(self.group_id, 0, 7, total, 8)
        self.assertEqual(ranges.cache.current_bytes, 0)
        worker.revoked = False
        link.backend.revoked = True
        with self.assertRaises(ObjectVfsError):
            ranges.get_group_range(self.group_id, 0, 7, total, 8)
        link.backend.revoked = False
        self.objects[self.key] = b"changed" + self.payload[7:]
        with (
            patch(
                "amplifai_phone.offline_single_stage_capture.time.monotonic",
                return_value=time.monotonic() + 30,
            ),
            self.assertRaises(ObjectVfsError),
        ):
            ranges.get_group_range(self.group_id, 0, 7, total, 8)

    def test_cancel_during_streamed_ciphertext_range_returns_no_partial_reply(self):
        self.transport.put_granted(self.grant(), self.payload)
        calls = 0

        def cancel() -> None:
            nonlocal calls
            calls += 1
            if calls >= 4:
                raise RuntimeError("synthetic_cancel")

        transport = ExactCapabilityTransport(
            self.admission, self.gateway, self.http, check_cancel=cancel
        )
        with self.assertRaisesRegex(ObjectVfsError, "cloud_transfer_cancelled"):
            transport.get_range(self.key, 0, len(self.payload) - 1, len(self.payload))

    def test_live_source_readers_share_one_attempt_budget_and_recheck_authority(self):
        admission = replace(self.admission, categories=("calls", "contacts"))
        cache = AttemptCipherCache(admission, budget_bytes=1_048_704)
        rows = {}
        for category in admission.categories:
            group_id = str(uuid.uuid4())
            body = (category.encode() * 100_000)[:700_000]
            key = f"cloud-v1/{admission.attempt_id}/{category}/main/0/{group_id}"
            rows[category] = (group_id, body, key, hashlib.sha256(body).hexdigest())
        calls = []
        worker = SimpleNamespace(revoked=False)

        def processing_group(current, epoch, category, group_id):
            if worker.revoked or current != admission or epoch != 1:
                raise ObjectVfsError("cloud_worker_revoked")
            expected_id, body, key, digest = rows[category]
            if group_id != expected_id:
                raise ObjectVfsError("cloud_worker_group_changed")
            return key, len(body), digest

        def read_object(key, start, end, _max_bytes):
            calls.append(key)
            for _id, body, expected, _digest in rows.values():
                if key == expected:
                    fragment = body[start : end + 1]
                    return RangeReply(
                        206,
                        f"bytes {start}-{end}/{len(body)}",
                        len(fragment),
                        fragment,
                        time.time() + 30,
                    )
            raise ObjectVfsError("cloud_object_unknown")

        ledgers = {
            category: SimpleNamespace(
                group=lambda _id, row=row: (
                    None,
                    None,
                    len(row[1]),
                    row[3],
                    None,
                    row[2],
                    "verified",
                )
            )
            for category, row in rows.items()
        }
        link = SimpleNamespace(
            admission=admission,
            processing_cache=cache,
            gateway=SimpleNamespace(processing_group=processing_group),
            backend=SimpleNamespace(ledgers=ledgers, revoked=False),
            objects=SimpleNamespace(get_range=read_object),
        )
        contacts = ProcessingRanges(link, "contacts", 1)
        call_history = ProcessingRanges(link, "calls", 1)
        for reader in (contacts, call_history):
            group_id, body, _key, _digest = rows[reader.category]
            self.assertEqual(
                reader.get_group_range(group_id, 0, 7, len(body), 8).body, body[:8]
            )
        self.assertIs(contacts.cache, call_history.cache)
        self.assertLessEqual(cache.peak_bytes, cache.budget_bytes)
        self.assertEqual(len(calls), 2)
        group_id, body, _key, _digest = rows["contacts"]
        self.assertEqual(
            contacts.get_group_range(group_id, 8, 15, len(body), 8).body, body[8:16]
        )
        self.assertEqual(len(calls), 3)  # The call-history reader evicted contacts.
        with patch(
            "amplifai_phone.offline_single_stage_capture.time.monotonic",
            return_value=time.monotonic() + 31,
        ):
            contacts.get_group_range(group_id, 0, 7, len(body), 8)
        self.assertEqual(len(calls), 4)
        for field in ("owner_id", "phone_id", "attempt_id"):
            foreign = replace(admission, **{field: str(uuid.uuid4())})
            with self.assertRaisesRegex(ObjectVfsError, "cache_attempt_changed"):
                ProcessingRanges(
                    SimpleNamespace(admission=foreign, processing_cache=cache),
                    "contacts",
                    1,
                )
        worker.revoked = True
        with self.assertRaises(ObjectVfsError):
            contacts.get_group_range(group_id, 0, 7, len(body), 8)
        self.assertEqual(cache.current_bytes, 0)

    def test_processing_rechecks_after_slow_or_revoked_group_response(self):
        self.transport.put_granted(self.grant(), self.payload)
        worker = SimpleNamespace(revoked=False)

        def processing_group(admission, epoch, category, group_id):
            if worker.revoked or (admission, epoch, category, group_id) != (
                self.admission,
                1,
                "contacts",
                self.group_id,
            ):
                raise ObjectVfsError("cloud_worker_revoked")
            return self.key, len(self.payload), self.digest

        def slow_read(key, start, end, max_bytes):
            reply = self.transport.get_range(key, start, end, max_bytes)
            return RangeReply(
                reply.status,
                reply.content_range,
                reply.content_length,
                reply.body,
                time.time() - 1,
            )

        link = SimpleNamespace(
            admission=self.admission,
            gateway=SimpleNamespace(processing_group=processing_group),
            backend=SimpleNamespace(
                ledgers={
                    "contacts": SimpleNamespace(
                        group=lambda _id: (
                            None,
                            None,
                            len(self.payload),
                            self.digest,
                            None,
                            self.key,
                            "verified",
                        )
                    )
                },
                revoked=False,
            ),
            objects=SimpleNamespace(get_range=slow_read),
        )
        ranges = ProcessingRanges(link, "contacts", 1)
        with self.assertRaisesRegex(ObjectVfsError, "read_grant_expired"):
            ranges.get_group_range(self.group_id, 0, 7, len(self.payload), 8)
        self.assertEqual(ranges.cache.current_bytes, 0)

        def revoked_read(key, start, end, max_bytes):
            reply = self.transport.get_range(key, start, end, max_bytes)
            worker.revoked = True
            return reply

        link.objects.get_range = revoked_read
        with self.assertRaisesRegex(ObjectVfsError, "worker_revoked"):
            ranges.get_group_range(self.group_id, 0, 7, len(self.payload), 8)
        self.assertEqual(ranges.cache.current_bytes, 0)


if __name__ == "__main__":
    unittest.main()
