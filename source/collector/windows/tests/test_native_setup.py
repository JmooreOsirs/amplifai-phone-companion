"""Pinned synthetic v2 exchanges only; no server, device or release admission."""
import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from native_setup import (
    ApprovalRequest, ApprovedRun, ClaimRequest, ClientIdentity, DISCLOSURE, SetupClaim,
    SetupLink, SetupProtocolError, capability, categories, disclosure, identifier, json_object,
    request_body, timestamp,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/native-setup-v2.json").read_text())
CLIENT = ClientIdentity(**{key: FIXTURE["claimRequest"]["client"][key] for key in ("version", "build")})
LINK = SetupLink(FIXTURE["claimRequest"]["setupId"], FIXTURE["claimRequest"]["bootstrap"])
LINK_TEXT = f"amplifai-phone://setup?protocol=phone-native-setup-v2&setup={LINK.setup_id}&bootstrap={LINK.bootstrap}"


def claimed():
    value = FIXTURE["claimRequest"]
    request = ClaimRequest(LINK, CLIENT, value["capability"], value["requestId"])
    return request, SetupClaim.parse(request_body(FIXTURE["claimResponse"]), request)


def approving():
    request, claim = claimed()
    value = FIXTURE["approveRequest"]
    return ApprovalRequest(claim, request, value["runCapability"], value["requestId"])


class NativeSetupContractTests(unittest.TestCase):
    def test_pinned_windows_golden_claim_approval_and_nested_preparation(self):
        request, claim = claimed()
        self.assertEqual(json.loads(request.body), FIXTURE["claimRequest"])
        approval = approving()
        self.assertEqual(json.loads(approval.body), FIXTURE["approveRequest"])
        run = ApprovedRun.parse(request_body(FIXTURE["approveResponse"]), approval)
        self.assertEqual(run.scope, claim.scope)
        self.assertEqual(run.capability, FIXTURE["approveRequest"]["runCapability"])
        self.assertEqual(run.run_id, FIXTURE["approveResponse"]["runId"])

    def test_exact_windows_disclosure_text_hash_links_and_supplement(self):
        self.assertEqual(disclosure(FIXTURE["claimResponse"]["disclosure"]), DISCLOSURE)
        for key in DISCLOSURE:
            changed = copy.deepcopy(DISCLOSURE)
            changed[key] = ["Different"] if key == "sentences" else "Different"
            with self.subTest(key=key), self.assertRaises(SetupProtocolError):
                disclosure(changed)
        with self.assertRaises(SetupProtocolError):
            disclosure({**DISCLOSURE, "extra": True})

    def test_link_rejects_extra_duplicate_encoded_missing_and_foreign_values(self):
        self.assertEqual(SetupLink.parse(LINK_TEXT), LINK)
        for changed in (LINK_TEXT + "&extra=true", LINK_TEXT + "&setup=" + LINK.setup_id,
                        LINK_TEXT.replace("protocol=phone-native-setup-v2", "protocol=phone-native-setup-v1"),
                        LINK_TEXT.replace("://setup?", "://setup/?"), LINK_TEXT.replace("amplifai-phone:", "https:"),
                        LINK_TEXT + "#fragment", " " + LINK_TEXT, LINK_TEXT.replace("setup=", "setup=%20"),
                        LINK_TEXT.replace("bootstrap=", "bootstrap"), LINK_TEXT + "&badfield",
                        LINK_TEXT.replace(LINK.bootstrap, "A" * 42 + "B")):
            with self.subTest(link_shape=changed[:30]), self.assertRaises(SetupProtocolError):
                SetupLink.parse(changed)

    def test_capability_requires_exact_canonical_32_bytes(self):
        self.assertEqual(capability("A" * 43), "A" * 43)
        for value in ("A" * 42, "A" * 44, "A" * 42 + "B", "A" * 42 + "=", "A" * 42 + "+", 1, None):
            with self.subTest(value_type=type(value)), self.assertRaises(SetupProtocolError):
                capability(value)

    def test_request_ids_and_all_three_capabilities_are_independent(self):
        request, claim = claimed()
        with self.assertRaises(SetupProtocolError):
            replace(request, capability=LINK.bootstrap)
        for changed in ({"run_capability": LINK.bootstrap}, {"run_capability": request.capability},
                        {"request_id": request.request_id}, {"claim": replace(claim, snapshot="x")}):
            with self.subTest(changed=list(changed)), self.assertRaises(SetupProtocolError):
                replace(approving(), **changed)
        first, second = ClaimRequest.create(LINK, CLIENT), ClaimRequest.create(LINK, CLIENT)
        self.assertNotEqual(first.capability, second.capability)
        self.assertNotEqual(first.request_id, second.request_id)

    def test_scope_identity_and_request_cannot_change_during_claim(self):
        request, _ = claimed()
        for key, changed in (("protocol", "phone-native-setup-v1"), ("setupId", FIXTURE["claimRequest"]["requestId"]),
                             ("requestId", FIXTURE["approveRequest"]["requestId"]), ("state", "approved"),
                             ("categories", ["calls", "contacts"]), ("ownerId", "not-an-id"),
                             ("setupSnapshotSha256", "A" * 64), ("destinationEmail", "not an address"),
                             ("destinationCourse", "line\nbreak"), ("destinationCourse", "\ud800"),
                             ("destinationEmail", "\ud800")):
            value = copy.deepcopy(FIXTURE["claimResponse"])
            value[key] = changed
            with self.subTest(key=key), self.assertRaises(SetupProtocolError):
                SetupClaim.parse(json.dumps(value).encode(), request)
        for key, changed in (("platform", "android"), ("channel", "macos-arm64"), ("architecture", "universal"),
                             ("bundleId", "other"), ("version", "changed"), ("build", "1")):
            value = copy.deepcopy(FIXTURE["claimResponse"])
            value["client"][key] = changed
            with self.subTest(identity=key), self.assertRaises(SetupProtocolError):
                SetupClaim.parse(request_body(value), request)

    def test_approval_receipt_is_bound_to_every_visible_scope_and_reference(self):
        request = approving()
        for key in ("setupId", "requestId", "setupSnapshotSha256", "approvalLocation", "disclosureVersion",
                    "disclosureSha256", "supplementVersion", "supplementSha256", "ownerId", "enrollmentId",
                    "courseId", "destinationEmail", "destinationCourse", "categories", "client"):
            value = copy.deepcopy(FIXTURE["approveResponse"])
            value[key] = "changed"
            with self.subTest(key=key), self.assertRaises(SetupProtocolError):
                ApprovedRun.parse(request_body(value), request)

    def test_nested_preparation_must_match_approval_and_be_unstarted(self):
        request = approving()
        for key in FIXTURE["approveResponse"]["preparation"]:
            value = copy.deepcopy(FIXTURE["approveResponse"])
            value["preparation"][key] = "changed"
            with self.subTest(key=key), self.assertRaises(SetupProtocolError):
                ApprovedRun.parse(request_body(value), request)
        value = copy.deepcopy(FIXTURE["approveResponse"])
        value["preparation"]["extra"] = True
        with self.assertRaises(SetupProtocolError):
            ApprovedRun.parse(request_body(value), request)

    def test_approval_cannot_extend_twelve_hours_or_be_created_after_setup_expiry(self):
        request = approving()
        for field, value in (("expiresAt", "2026-10-22T10:00:01.000Z"),
                             ("approvedAt", "2026-10-21T22:10:00.000Z"),
                             ("expiresAt", "2026-10-21T21:59:59.000Z")):
            changed = copy.deepcopy(FIXTURE["approveResponse"])
            changed[field] = value
            if field == "expiresAt":
                changed["preparation"][field] = value
            with self.subTest(field=field), self.assertRaises(SetupProtocolError):
                ApprovedRun.parse(request_body(changed), request)

    def test_duplicate_fields_invalid_json_extra_fields_and_bounds_fail_closed(self):
        request, _ = claimed()
        original = request_body(FIXTURE["claimResponse"])
        duplicate = b'{"protocol":"phone-native-setup-v2",' + original[1:]
        nested = original.replace(b'"platform":"windows"', b'"platform":"windows","platform":"windows"', 1)
        for body in (duplicate, nested, original + b" " * 8192, b"\xff", b"[]", b'{"a":NaN}',
                     request_body({**FIXTURE["claimResponse"], "extra": True})):
            with self.subTest(length=len(body)), self.assertRaises(SetupProtocolError):
                SetupClaim.parse(body, request)
        with self.assertRaises(SetupProtocolError):
            json_object(b"", 8192)

    def test_dates_categories_and_identifiers_do_not_normalize_invalid_input(self):
        for value in ("2026-02-30T00:00:00Z", "2026-10-21T22:00:00", "2026-10-21T22:00:00Zextra"):
            with self.assertRaises(SetupProtocolError):
                timestamp(value)
        for value in ([], ["calls"], ["contacts", "contacts"], ["contacts", "messages", "calls"], ["contacts", "other"]):
            with self.assertRaises(SetupProtocolError):
                categories(value)
        for value in (FIXTURE["approveResponse"]["approvalId"].upper(), "0" * 36, "00000000-0000-0000-0000-000000000000"):
            with self.assertRaises(SetupProtocolError):
                identifier(value)

    def test_pending_replay_bytes_and_repr_do_not_leak_capabilities_or_scope(self):
        request, _ = claimed()
        approval = approving()
        self.assertEqual(approval.body, approving().body)
        for secret in (LINK.bootstrap, request.capability, approval.run_capability, "fixture@example.test"):
            self.assertNotIn(secret, repr(request))
            self.assertNotIn(secret, repr(approval))


if __name__ == "__main__":
    unittest.main()
