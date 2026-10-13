import Foundation

struct ContactPreview: Decodable, Identifiable {
    let id: Int
    let name: String
    let phoneCount: Int
    let phoneEnds: [String]
    let nameFields: ContactNameFields?
    let organizationField: Bool?

    var nameFieldDescription: String {
        switch nameFields {
        case .firstAndLast: return "Source name: first and last fields filled."
        case .first: return "Source name: first field filled."
        case .last: return "Source name: last field filled."
        case .missing: return "Source name fields are empty."
        case .unknown, nil: return "Name-field details are unavailable for this display name."
        }
    }
}

enum ContactNameFields: String, Decodable {
    case firstAndLast = "first_and_last"
    case first, last, missing, unknown

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: value) ?? .unknown
    }
}

struct ResiduePreview: Decodable, Identifiable {
    let name: String
    let bytes: Int

    var id: String { name }
}

struct AgentEvent: Decodable {
    let kind: String
    let state: String?
    let transport: String?
    let value: Double?
    let stage: String?
    let receivedBytes: Int64?
    let retainedBytes: Int64?
    let discardedBytes: Int64?
    let filesReceived: Int64?
    let bytesPerSecond: Double?
    let elapsedSeconds: Double?
    let processedBytes: Int64?
    let contacts: [ContactPreview]?
    let totalContacts: Int?
    let query: String?
    let cursor: Int?
    let nextCursor: Int?
    let scanId: String?
    let requestId: String?
    let availableCalls: Int?
    let availableMessages: Int?
    let since: String?
    let missing: [String]?
    let backupEncrypted: Bool?
    let unavailableReasons: [String: String]?
    let selectedContacts: Int?
    let reviewId: String?
    let selectionSha256: String?
    let matchedCalls: Int?
    let matchedMessages: Int?
    let observedCallEarliest: String?
    let observedCallLatest: String?
    let observedMessageEarliest: String?
    let observedMessageLatest: String?
    let missingSources: [String]?
    let sessions: [ResiduePreview]?
    let count: Int?
    let code: String?
    let deviceCode: Int64?
    let cleanupRequired: Bool?
    let pairCode: String?
    let port: Int?
    let expiresInSeconds: Int?
    let handoffId: String?
    let intentId: String?
    let email: String?
    let course: String?
    let source: String?
    let component: String?
    let result: String?
    let declaredBytes: Int64?
    let rawBytes: Int64?
    let copiedBytes: Int64?
    let encrypted: Bool?
    let producerDigest: String?
    let sidecars: String?
}
