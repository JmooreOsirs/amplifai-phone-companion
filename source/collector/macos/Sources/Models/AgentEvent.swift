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
    let contacts: [ContactPreview]?
    let availableCalls: Int?
    let availableMessages: Int?
    let since: String?
    let missing: [String]?
    let selectedContacts: Int?
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
    let pairCode: String?
    let port: Int?
    let expiresInSeconds: Int?
}
