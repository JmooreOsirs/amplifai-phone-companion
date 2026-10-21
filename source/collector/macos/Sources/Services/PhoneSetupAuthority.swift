import CryptoKit
import Foundation

/// Pending setup never grants collection. Only the matching native approval receipt can create a usable run.
enum PhoneSetupContract {
    static let wireProtocol = "phone-native-setup-v1"
    static let host = "amplifai.satoris.ai"
    static let claimPath = "/api/v1/phone-setups/claim"
    static let approvePath = "/api/v1/phone-setups/approve"
    static let claimResponseBytes = 8192
    static let approvalResponseBytes = 16384
    static let lifetime: TimeInterval = 10 * 60
    static let disclosureVersion = "phone-run-v3"

    static func responseLimit(path: String) -> Int? {
        switch path {
        case claimPath: return claimResponseBytes
        case approvePath: return approvalResponseBytes
        default: return nil
        }
    }

    static func utcDate(_ raw: Any?) -> Date? {
        guard let text = raw as? String, text.hasSuffix("Z") else { return nil }
        return PhoneRunContract.preparationDate(text)
    }

    static func canonicalCategories(_ raw: Any?) -> [String]? {
        guard let values = raw as? [String], let canonical = PhoneRunContract.categories(raw),
              values == canonical else { return nil }
        return canonical
    }

    /// Foundation keeps the last duplicate JSON key. Consent receipts must not have two interpretations.
    private static func object(_ body: Data?, keys: Set<String>, maximumBytes: Int) -> [String: Any]? {
        guard let fields = PhoneRunContract.object(body, keys: keys, maximumBytes: maximumBytes), let body else { return nil }
        let bytes = Array(body)
        var objects: [Set<String>?] = []
        var cursor = 0
        while cursor < bytes.count {
            switch bytes[cursor] {
            case 123: objects.append(Set<String>()) // {
            case 91: objects.append(nil) // [
            case 125, 93: objects.removeLast()
            case 34:
                let start = cursor
                cursor += 1
                while cursor < bytes.count && bytes[cursor] != 34 {
                    if bytes[cursor] == 92 { cursor += 1 }
                    cursor += 1
                }
                var next = cursor + 1
                while next < bytes.count && [9, 10, 13, 32].contains(bytes[next]) { next += 1 }
                if next < bytes.count && bytes[next] == 58 {
                    guard let last = objects.indices.last, var seen = objects[last],
                          let key = try? JSONSerialization.jsonObject(with: Data(bytes[start...cursor]), options: .fragmentsAllowed) as? String,
                          seen.insert(key).inserted else { return nil }
                    objects[last] = seen
                }
            default: break
            }
            cursor += 1
        }
        return fields
    }

    static func claim(_ body: Data?, request: PhoneSetupClaimRequest) -> PhoneSetupClaim? {
        guard let fields = object(body, keys: [
            "protocol", "setupId", "requestId", "state", "ownerId", "enrollmentId", "courseId",
            "destinationEmail", "destinationCourse", "categories", "expiresAt", "setupSnapshotSha256", "disclosure"
        ], maximumBytes: claimResponseBytes),
              fields["protocol"] as? String == wireProtocol,
              fields["setupId"] as? String == request.setupID,
              fields["requestId"] as? String == request.requestID,
              fields["state"] as? String == "pending_approval",
              let owner = fields["ownerId"] as? String, PhoneRunContract.isID(owner),
              let enrollment = fields["enrollmentId"] as? String, PhoneRunContract.isID(enrollment),
              let courseID = fields["courseId"] as? String, PhoneRunContract.isID(courseID),
              let email = fields["destinationEmail"] as? String, email.utf8.count <= 254, PhoneRunContract.isEmail(email),
              let course = fields["destinationCourse"] as? String, PhoneRunContract.isCourse(course),
              let categories = canonicalCategories(fields["categories"]),
              let expiresAt = utcDate(fields["expiresAt"]),
              let snapshot = fields["setupSnapshotSha256"] as? String, PhoneRunContract.isDigest(snapshot),
              let disclosure = PhoneSetupDisclosure.parse(fields["disclosure"]) else { return nil }
        return PhoneSetupClaim(setupID: request.setupID, requestID: request.requestID, ownerID: owner,
                               enrollmentID: enrollment, courseID: courseID, email: email, course: course,
                               categories: categories, expiresAt: expiresAt, snapshotSha256: snapshot,
                               disclosure: disclosure)
    }

    static func approval(_ body: Data?, request: PhoneSetupApprovalRequest) -> PhoneSetupApprovalReceipt? {
        let setup = request.setup
        guard let fields = object(body, keys: [
            "protocol", "setupId", "requestId", "setupSnapshotSha256", "runId", "approvalId", "approvalLocation",
            "disclosureVersion", "disclosureSha256", "ownerId", "enrollmentId", "courseId", "destinationEmail",
            "destinationCourse", "categories", "approvedAt", "expiresAt", "preparation"
        ], maximumBytes: approvalResponseBytes),
              fields["protocol"] as? String == wireProtocol,
              fields["setupId"] as? String == setup.setupID,
              fields["requestId"] as? String == request.requestID,
              fields["setupSnapshotSha256"] as? String == setup.snapshotSha256,
              fields["approvalLocation"] as? String == "native",
              fields["disclosureVersion"] as? String == disclosureVersion,
              fields["disclosureSha256"] as? String == setup.disclosure.sha256,
              fields["ownerId"] as? String == setup.ownerID,
              fields["enrollmentId"] as? String == setup.enrollmentID,
              fields["courseId"] as? String == setup.courseID,
              fields["destinationEmail"] as? String == setup.email,
              fields["destinationCourse"] as? String == setup.course,
              canonicalCategories(fields["categories"]) == setup.categories,
              let runID = fields["runId"] as? String, PhoneRunContract.isID(runID),
              let approvalID = fields["approvalId"] as? String, PhoneRunContract.isID(approvalID),
              let approvedAt = utcDate(fields["approvedAt"]), approvedAt < setup.expiresAt,
              let expiresAt = utcDate(fields["expiresAt"]), approvedAt < expiresAt,
              expiresAt <= approvedAt.addingTimeInterval(PhoneRunContract.runLifetime),
              let nested = fields["preparation"] as? [String: Any],
              utcDate(nested["expiresAt"]) == expiresAt,
              canonicalCategories(nested["categories"]) == setup.categories,
              let preparationBytes = try? JSONSerialization.data(withJSONObject: nested),
              let preparation = PhoneRunContract.preparationStatus(preparationBytes),
              preparation.state == .current, preparation.operation == nil else { return nil }
        let expected = PhoneRunClaim(approvalID: approvalID, enrollmentID: setup.enrollmentID, email: setup.email,
                                     course: setup.course, categories: setup.categories,
                                     disclosureVersion: disclosureVersion, expiresAt: expiresAt, startExpiresAt: nil)
        guard preparation.claim == expected else { return nil }
        return PhoneSetupApprovalReceipt(setupID: setup.setupID, requestID: request.requestID,
                                          runID: runID, approvedAt: approvedAt, preparation: preparation)
    }
}

struct PhoneSetupDisclosure: Equatable {
    let version: String
    let sentences: [String]
    let agreement: String
    let policyVersion: String
    let policySha256: String
    let privacyHref: String
    let sha256: String

    static let pinned = PhoneSetupDisclosure(
        version: "phone-run-v3",
        sentences: ["AMPLIFai Phone creates a temporary iPhone backup on this Mac and extracts contact, call and message metadata. After you choose contacts and continue, their available metadata is saved to the account and course shown. Backup files and message content stay on your Mac. Interrupted cleanup can leave temporary files."],
        agreement: "I approve this one phone collection and automatic saving of the contacts I choose, with their available call and message metadata, to this account and course.",
        policyVersion: "account-2026-10-v7",
        policySha256: "446be198f41a9c3d335b64f094b3ed2506810c0a5862de1422cd62fa9af5cf89",
        privacyHref: "/experience/account-privacy?version=account-2026-10-v7#phone-collection-and-saving",
        sha256: "e119713ec31ab224bcb940a14d8e69072c4b5a5f019ad3ce9188099c2cf12d7e")

    var computedSha256: String {
        let bytes = Data((sentences + [agreement, policyVersion, policySha256]).joined(separator: "\n").utf8)
        return SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }

    var privacyURL: URL? {
        guard self == Self.pinned else { return nil }
        return URL(string: "https://" + PhoneSetupContract.host + privacyHref)
    }

    static func parse(_ value: Any?) -> PhoneSetupDisclosure? {
        guard let fields = value as? [String: Any],
              Set(fields.keys) == ["version", "sentences", "agreement", "policyVersion", "policySha256", "privacyHref", "sha256"],
              fields["version"] as? String == pinned.version,
              fields["sentences"] as? [String] == pinned.sentences,
              fields["agreement"] as? String == pinned.agreement,
              fields["policyVersion"] as? String == pinned.policyVersion,
              fields["policySha256"] as? String == pinned.policySha256,
              fields["privacyHref"] as? String == pinned.privacyHref,
              fields["sha256"] as? String == pinned.sha256,
              pinned.computedSha256 == pinned.sha256 else { return nil }
        return pinned
    }
}

struct PhoneSetupClaim: Equatable {
    let setupID: String
    let requestID: String
    let ownerID: String
    let enrollmentID: String
    let courseID: String
    let email: String
    let course: String
    let categories: [String]
    let expiresAt: Date
    let snapshotSha256: String
    let disclosure: PhoneSetupDisclosure
}

struct PhoneSetupApprovalReceipt: Equatable {
    let setupID: String
    let requestID: String
    let runID: String
    let approvedAt: Date
    let preparation: PhonePreparationStatus
}

struct PhoneSetupClaimRequest: Equatable {
    let setupID: String
    let bootstrap: String
    let capability: String
    let requestID: String
    let body: Data

    init?(setupID: String, bootstrap: String, capability: String, requestID: String) {
        guard PhoneRunContract.isID(setupID), PhoneRunContract.isID(requestID),
              PhoneRunContract.isCapability(bootstrap), PhoneRunContract.isCapability(capability),
              bootstrap != capability,
              let body = PhoneRunContract.body(["setupId": setupID, "bootstrap": bootstrap,
                                                "capability": capability, "requestId": requestID]) else { return nil }
        self.setupID = setupID
        self.bootstrap = bootstrap
        self.capability = capability
        self.requestID = requestID
        self.body = body
    }
}

struct PhoneSetupApprovalRequest: Equatable {
    let setup: PhoneSetupClaim
    let requestID: String
    let runCapability: String
    let body: Data

    init?(setup: PhoneSetupClaim, claimRequest: PhoneSetupClaimRequest, requestID: String, runCapability: String) {
        guard setup.setupID == claimRequest.setupID, setup.requestID == claimRequest.requestID,
              setup.disclosure == .pinned, PhoneRunContract.isID(requestID), requestID != claimRequest.requestID,
              PhoneRunContract.isCapability(runCapability), runCapability != claimRequest.capability,
              runCapability != claimRequest.bootstrap,
              let body = PhoneRunContract.body([
                "setupId": setup.setupID, "capability": claimRequest.capability, "requestId": requestID,
                "runCapability": runCapability, "setupSnapshotSha256": setup.snapshotSha256,
                "disclosure": ["version": setup.disclosure.version, "sha256": setup.disclosure.sha256]
              ]) else { return nil }
        self.setup = setup
        self.requestID = requestID
        self.runCapability = runCapability
        self.body = body
    }
}
