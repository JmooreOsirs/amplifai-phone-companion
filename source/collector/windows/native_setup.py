"""Strict Windows native setup v2 contract. Capabilities stay in process memory."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

ORIGIN = "https://amplifai.satoris.ai"
PROTOCOL = "phone-native-setup-v2"
CLAIM_PATH = "/api/v1/phone-platform-setups/claim"
APPROVE_PATH = "/api/v1/phone-platform-setups/approve"
CATEGORIES = ("contacts", "calls", "messages")
SETUP_SECONDS, RUN_SECONDS = 600, 43_200
DISCLOSURE = {
    "version": "phone-run-windows-v1",
    "sentences": ["AMPLIFai Phone creates a temporary iPhone backup on this Windows PC and extracts contact, call and message metadata. After you choose contacts and continue, their available metadata is saved to the account and course shown. Backup files and message content stay on this PC. Interrupted cleanup can leave temporary files."],
    "agreement": "I approve this one phone collection and automatic saving of the contacts I choose, with their available call and message metadata, to this account and course.",
    "policyVersion": "account-2026-10-v7",
    "policySha256": "446be198f41a9c3d335b64f094b3ed2506810c0a5862de1422cd62fa9af5cf89",
    "privacyHref": "/experience/account-privacy?version=account-2026-10-v7#phone-collection-and-saving",
    "supplementVersion": "phone-platforms-2026-10-v1",
    "supplementSha256": "995c62d8f67ea2ac5e295b50dfaf65f9e6bd0f6d3b962a91cfe3555bd1ad1667",
    "supplementHref": "/experience/phone-platform-privacy?version=phone-platforms-2026-10-v1",
    "platform": "windows", "channel": "windows-x64-test",
    "sha256": "795c0423c9c15c4c13e79145e75b93f42e329b53acd5c10d5eb387fa48cc0f23",
}
DISCLOSURE_FIELDS = ("version", "agreement", "policyVersion", "policySha256", "privacyHref",
                     "supplementVersion", "supplementSha256", "supplementHref", "platform", "channel")
IDENTITY = {"platform": "windows", "channel": "windows-x64-test", "architecture": "x86_64",
            "bundleId": "ai.satoris.amplifai.phone.windows-candidate"}


class SetupProtocolError(ValueError):
    """Malformed authority never grants collection or reveals response contents."""


def strict_object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise SetupProtocolError("Unexpected authority fields")
    return value


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise SetupProtocolError("Duplicate authority field")
        result[key] = value
    return result


def _invalid_json_number(_value):
    raise SetupProtocolError("Invalid JSON number")


def json_object(body: bytes, limit: int):
    if type(body) is not bytes or not 0 < len(body) <= limit:
        raise SetupProtocolError("Invalid authority response size")
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_json_number)
    except (UnicodeError, ValueError, RecursionError):
        raise SetupProtocolError("Invalid authority JSON") from None
    if type(value) is not dict:
        raise SetupProtocolError("Invalid authority object")
    return value


def request_body(value) -> bytes:
    result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(result) > 4096:
        raise SetupProtocolError("Authority request is too large")
    return result


def identifier(value) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value):
        raise SetupProtocolError("Invalid authority identifier")
    return value


def digest(value) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SetupProtocolError("Invalid authority digest")
    return value


def capability(value) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise SetupProtocolError("Invalid authority capability")
    decoded = base64.urlsafe_b64decode(value + "=")
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise SetupProtocolError("Noncanonical authority capability")
    return value


def timestamp(value) -> datetime:
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value):
        raise SetupProtocolError("Invalid authority time")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SetupProtocolError("Invalid authority time") from None


def categories(value) -> tuple[str, ...]:
    if (type(value) is not list or not value or any(type(item) is not str for item in value)
            or "contacts" not in value or value != [item for item in CATEGORIES if item in value]):
        raise SetupProtocolError("Invalid authority categories")
    return tuple(value)


def disclosure(value):
    strict_object(value, DISCLOSURE)
    if value != DISCLOSURE:
        raise SetupProtocolError("Windows disclosure does not match")
    fields = [value["version"], value["sentences"][0], *(value[key] for key in DISCLOSURE_FIELDS[1:])]
    if hashlib.sha256("\n".join(fields).encode("utf-8")).hexdigest() != value["sha256"]:
        raise SetupProtocolError("Windows disclosure digest does not match")
    return value


@dataclass(frozen=True)
class ClientIdentity:
    version: str
    build: str

    def __post_init__(self):
        if (type(self.version) is not str or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}", self.version)
                or type(self.build) is not str or not re.fullmatch(r"[0-9]{1,12}", self.build)):
            raise SetupProtocolError("Invalid Windows client identity")

    def payload(self):
        return {**IDENTITY, "version": self.version, "build": self.build}

    def validate(self, value):
        if strict_object(value, self.payload()) != self.payload():
            raise SetupProtocolError("Windows client identity changed")


@dataclass(frozen=True)
class SetupLink:
    setup_id: str
    bootstrap: str = field(repr=False)

    @classmethod
    def parse(cls, url: str):
        if type(url) is not str or not url.startswith("amplifai-phone://setup?") or len(url) > 512:
            raise SetupProtocolError("Invalid native setup link")
        if any(ord(char) <= 32 or ord(char) >= 127 for char in url) or "%" in url or "#" in url:
            raise SetupProtocolError("Invalid native setup link")
        try:
            parts = urlsplit(url)
            pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise SetupProtocolError("Invalid native setup link") from None
        if parts.netloc != "setup" or parts.path or len(pairs) != 3 or len(dict(pairs)) != 3:
            raise SetupProtocolError("Invalid native setup link")
        values = strict_object(dict(pairs), ("protocol", "setup", "bootstrap"))
        if values["protocol"] != PROTOCOL:
            raise SetupProtocolError("Unsupported native setup protocol")
        return cls(identifier(values["setup"]), capability(values["bootstrap"]))


@dataclass(frozen=True)
class ClaimRequest:
    link: SetupLink
    client: ClientIdentity
    capability: str = field(repr=False)
    request_id: str
    body: bytes = field(init=False, repr=False)

    def __post_init__(self):
        identifier(self.link.setup_id)
        capability(self.link.bootstrap)
        capability(self.capability)
        identifier(self.request_id)
        if self.link.bootstrap == self.capability:
            raise SetupProtocolError("Setup capabilities must differ")
        object.__setattr__(self, "body", request_body({"protocol": PROTOCOL, "setupId": self.link.setup_id,
            "bootstrap": self.link.bootstrap, "capability": self.capability, "requestId": self.request_id,
            "client": self.client.payload()}))

    @classmethod
    def create(cls, link, client):
        return cls(link, client, secrets.token_urlsafe(32), str(uuid.uuid4()))


SCOPE_FIELDS = ("ownerId", "enrollmentId", "courseId", "destinationEmail", "destinationCourse", "categories", "client")


def _encoded_length(value, encoding):
    if type(value) is not str:
        raise SetupProtocolError("Invalid destination text")
    try:
        return len(value.encode(encoding))
    except UnicodeError:
        raise SetupProtocolError("Invalid destination text") from None


@dataclass(frozen=True, repr=False)
class Scope:
    owner_id: str
    enrollment_id: str
    course_id: str
    email: str
    course: str
    categories: tuple[str, ...]
    client: ClientIdentity

    @classmethod
    def parse(cls, value, client):
        client.validate(value["client"])
        email, course = value["destinationEmail"], value["destinationCourse"]
        if (_encoded_length(email, "utf-8") > 254
                or not re.fullmatch(r"(?!\.)(?!.*\.\.)([A-Za-z0-9_'+.\-]*)[A-Za-z0-9_+\-]@([A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}", email)):
            raise SetupProtocolError("Invalid account destination")
        if (not 1 <= _encoded_length(course, "utf-16-le") // 2 <= 160
                or any(ord(char) < 32 or ord(char) == 127 for char in course)):
            raise SetupProtocolError("Invalid course destination")
        return cls(*(identifier(value[key]) for key in SCOPE_FIELDS[:3]), email, course, categories(value["categories"]), client)


@dataclass(frozen=True, repr=False)
class SetupClaim:
    setup_id: str
    request_id: str
    scope: Scope
    expires_at: datetime
    snapshot: str

    @classmethod
    def parse(cls, body: bytes, request: ClaimRequest):
        value = strict_object(json_object(body, 8192), ("protocol", "setupId", "requestId", "state", *SCOPE_FIELDS,
                                                      "expiresAt", "setupSnapshotSha256", "disclosure"))
        if (value["protocol"] != PROTOCOL or value["setupId"] != request.link.setup_id
                or value["requestId"] != request.request_id or value["state"] != "pending_approval"):
            raise SetupProtocolError("Setup claim does not match this request")
        disclosure(value["disclosure"])
        return cls(request.link.setup_id, request.request_id, Scope.parse(value, request.client),
                   timestamp(value["expiresAt"]), digest(value["setupSnapshotSha256"]))


@dataclass(frozen=True, repr=False)
class ApprovalRequest:
    claim: SetupClaim
    claim_request: ClaimRequest
    run_capability: str
    request_id: str
    body: bytes = field(init=False)

    def __post_init__(self):
        capability(self.run_capability)
        identifier(self.request_id)
        digest(self.claim.snapshot)
        if (self.claim.setup_id != self.claim_request.link.setup_id or self.claim.request_id != self.claim_request.request_id
                or self.claim.scope.client != self.claim_request.client or self.request_id == self.claim_request.request_id
                or self.run_capability in (self.claim_request.link.bootstrap, self.claim_request.capability)):
            raise SetupProtocolError("Approval does not match this setup")
        reference = {key: DISCLOSURE[key] for key in ("version", "sha256", "supplementVersion", "supplementSha256")}
        object.__setattr__(self, "body", request_body({"protocol": PROTOCOL, "setupId": self.claim.setup_id,
            "capability": self.claim_request.capability, "requestId": self.request_id, "runCapability": self.run_capability,
            "setupSnapshotSha256": self.claim.snapshot, "client": self.claim.scope.client.payload(), "disclosure": reference}))

    @classmethod
    def create(cls, claim, request):
        return cls(claim, request, secrets.token_urlsafe(32), str(uuid.uuid4()))


@dataclass(frozen=True, repr=False)
class ApprovedRun:
    setup_id: str
    run_id: str
    approval_id: str
    scope: Scope
    approved_at: datetime
    expires_at: datetime
    capability: str

    @classmethod
    def parse(cls, body: bytes, request: ApprovalRequest):
        value = strict_object(json_object(body, 16384), ("protocol", "setupId", "requestId", "setupSnapshotSha256",
            "runId", "approvalId", "approvalLocation", "disclosureVersion", "disclosureSha256", "supplementVersion",
            "supplementSha256", *SCOPE_FIELDS, "approvedAt", "expiresAt", "preparation"))
        expected = {"protocol": PROTOCOL, "setupId": request.claim.setup_id, "requestId": request.request_id,
                    "setupSnapshotSha256": request.claim.snapshot, "approvalLocation": "native",
                    "disclosureVersion": DISCLOSURE["version"], "disclosureSha256": DISCLOSURE["sha256"],
                    "supplementVersion": DISCLOSURE["supplementVersion"], "supplementSha256": DISCLOSURE["supplementSha256"]}
        if any(value[key] != item for key, item in expected.items()) or Scope.parse(value, request.claim.scope.client) != request.claim.scope:
            raise SetupProtocolError("Approval receipt changed this setup")
        approved_at, expires_at = timestamp(value["approvedAt"]), timestamp(value["expiresAt"])
        if not approved_at < request.claim.expires_at or not approved_at < expires_at <= approved_at + timedelta(seconds=RUN_SECONDS):
            raise SetupProtocolError("Approval receipt changed its lifetime")
        preparation = strict_object(value["preparation"], ("protocol", "approvalId", "enrollmentId", "destinationEmail",
            "destinationCourse", "categories", "disclosureVersion", "expiresAt", "state", "operation"))
        expected_preparation = {key: value[key] for key in ("approvalId", "enrollmentId", "destinationEmail", "destinationCourse",
                                                          "categories", "disclosureVersion", "expiresAt")}
        if preparation != {**expected_preparation, "protocol": "phone-start-v1", "state": "current", "operation": None}:
            raise SetupProtocolError("Prepared run does not match native approval")
        return cls(request.claim.setup_id, identifier(value["runId"]), identifier(value["approvalId"]), request.claim.scope,
                   approved_at, expires_at, request.run_capability)
