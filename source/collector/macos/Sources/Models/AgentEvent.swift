import Foundation

struct ContactPreview: Decodable, Identifiable {
    let id: Int
    let name: String
    let phoneCount: Int
    let phoneEnds: [String]
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
    let availableCalls: Int?
    let availableMessages: Int?
    let since: String?
    let missing: [String]?
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
}
