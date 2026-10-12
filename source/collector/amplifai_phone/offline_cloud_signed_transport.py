"""Offline exact-capability ciphertext transport; no provider credentials or HTTP client.

An injected HTTP port permits a disposable local object server rehearsal. A future
network client must preserve the pinned host/path, no-redirect and bounded-body
contract; this module is not imported by the released collector.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .offline_cloud_browser_bridge import BrowserGateway
from .offline_cloud_capture import CaptureAdmission
from .offline_grouped_object_vfs import RangeReply
from .offline_object_vfs import ObjectVfsError
from .offline_single_stage_capture import ExactGrant

MAX_RANGE = 128 * 1024
MAX_GROUP = 1_048_704


@dataclass(frozen=True)
class ObjectHttpReply:
    status: int
    content_range: str | None
    content_length: int
    chunks: Iterable[bytes]


ObjectHttpPort = Callable[[str, str, dict[str, str], bytes | None], ObjectHttpReply]


class ExactCapabilityTransport:
    """One exact encrypted object per grant; URLs and ciphertext stay out of JSON."""

    def __init__(
        self,
        admission: CaptureAdmission,
        gateway: BrowserGateway,
        http: ObjectHttpPort,
        check_cancel: Callable[[], None] | None = None,
    ) -> None:
        if gateway.bridge.admission != admission:
            raise ObjectVfsError("cloud_handoff_attempt_changed")
        self.admission = admission
        self.gateway = gateway
        self.http = http
        self.check_cancel = check_cancel
        self.put_calls = 0
        self.range_calls = 0
        self.response_bytes = 0
        self.peak_buffered_bytes = 0

    def _check(self) -> None:
        if self.check_cancel is not None:
            try:
                self.check_cancel()
            except Exception as error:
                raise ObjectVfsError("cloud_transfer_cancelled") from error

    def put_granted(self, grant: ExactGrant, payload: bytes) -> None:
        self._check()
        if (
            grant.upload_url is None
            or len(payload) != grant.cipher_bytes
            or len(payload) > MAX_GROUP
            or (hashlib.sha256(payload).hexdigest() != grant.cipher_sha256)
        ):
            raise ObjectVfsError("cloud_upload_grant_changed")
        self.gateway._signed_url(grant.upload_url, grant.object_key, "PUT")
        self.peak_buffered_bytes = max(self.peak_buffered_bytes, len(payload))
        reply = self.http(
            "PUT",
            grant.upload_url,
            {
                "Content-Length": str(len(payload)),
                "Content-Type": "application/octet-stream",
            },
            payload,
        )
        self.put_calls += 1
        self._check()
        if reply.status not in (200, 201, 204) or reply.content_length != 0:
            raise ObjectVfsError("cloud_upload_unconfirmed")
        if any(reply.chunks):
            raise ObjectVfsError("cloud_upload_response_invalid")

    def put_exact(self, _key: str, _payload: bytes, _digest: str) -> None:
        raise ObjectVfsError("cloud_upload_grant_required")

    def get_range(self, key: str, start: int, end: int, max_bytes: int) -> RangeReply:
        self._check()
        parts = key.split("/") if isinstance(key, str) else []
        try:
            valid = (
                len(parts) == 6
                and parts[0] == "cloud-v1"
                and parts[1] == self.admission.attempt_id
                and parts[2] in self.admission.categories
                and parts[3] in ("main", "wal")
                and int(parts[4]) >= 0
                and str(uuid.UUID(parts[5])) == parts[5]
                and type(start) is int
                and type(end) is int
                and type(max_bytes) is int
                and 0 <= start <= end
                and end - start + 1 <= min(max_bytes, MAX_GROUP)
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ObjectVfsError("cloud_range_identity_invalid")
        actual_key, total, _digest, url, expires_at = self.gateway.read_grant(
            self.admission,
            parts[2],
            parts[3],
            int(parts[4]),
            parts[5],
            start,
            end,
        )
        if actual_key != key or end >= total:
            raise ObjectVfsError("cloud_range_identity_changed")
        reply = self.http("GET", url, {"Range": f"bytes={start}-{end}"}, None)
        self._check()
        self.range_calls += 1
        expected = end - start + 1
        if (
            reply.status != 206
            or reply.content_range != f"bytes {start}-{end}/{total}"
            or reply.content_length != expected
        ):
            raise ObjectVfsError("cloud_range_response_invalid")
        chunks = []
        seen = 0
        for chunk in reply.chunks:
            self._check()
            if (
                not isinstance(chunk, bytes)
                or not chunk
                or len(chunk) > MAX_RANGE
                or seen + len(chunk) > expected
            ):
                raise ObjectVfsError("cloud_range_response_invalid")
            seen += len(chunk)
            chunks.append(chunk)
        if seen != expected:
            raise ObjectVfsError("cloud_range_short")
        self._check()
        body = b"".join(chunks)
        self.response_bytes += len(body)
        self.peak_buffered_bytes = max(self.peak_buffered_bytes, len(body))
        return RangeReply(206, reply.content_range, len(body), body, expires_at)

    def get_exact(self, key: str) -> bytes:
        # A descriptor-only grant supplies size; the single data GET stays <=1 MiB.
        parts = key.split("/") if isinstance(key, str) else []
        if len(parts) != 6:
            raise ObjectVfsError("cloud_range_identity_invalid")
        _, total, digest, _, _ = self.gateway.read_grant(
            self.admission, parts[2], parts[3], int(parts[4]), parts[5], 0, 0
        )
        if not 1 <= total <= MAX_GROUP:
            raise ObjectVfsError("cloud_group_size_invalid")
        body = self.get_range(key, 0, total - 1, MAX_GROUP).body
        if hashlib.sha256(body).hexdigest() != digest:
            raise ObjectVfsError("cloud_group_integrity_invalid")
        self.peak_buffered_bytes = max(self.peak_buffered_bytes, len(body))
        return body
