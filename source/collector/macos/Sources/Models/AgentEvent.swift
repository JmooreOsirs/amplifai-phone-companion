import Foundation
import CoreFoundation

enum SourceDiagnosticDecodingError: Error { case invalid }

/// The failure-only, nonpersonal v1 wire record. Raw event preflight also checks
/// JSON number kinds before Foundation can coerce 1.0 into an integer.
struct SourceDiagnostic: Decodable, Equatable, Identifiable {
    static let maximumByteCount: Int64 = 9_007_199_254_740_991
    static let fields: Set<String> = ["source", "component", "reason", "result", "declaredBytes", "rawBytes", "copiedBytes", "encrypted", "entryKeyPresent", "validationCode", "validationScope", "producerDigest", "copyDigest", "sidecars"]
    static let reasons: Set<String> = ["absent", "selected_payload_missing", "selected_payload_invalid", "selected_payload_size", "selected_payload_identity", "selected_payload_file_type", "selected_payload_length", "selected_payload_crypto", "source_integrity", "unsupported_schema", "source_capacity", "source_invalid", "selected_contacts_integrity", "contacts_schema"]
    private static let validationCodes: Set<String> = ["insufficient_manifest", "active_wal", "active_journal", "active_shm", "unsafe", "checksum_mismatch", "structural_invalid", "timed_out", "unavailable", "unsupported_tokenizer", "validated_contacts_tables", "contacts_relationship_invalid", "contacts_types_invalid", "validated_messages_database", "copy_checksum_mismatch", "sidecar_present", "schema_unsupported", "validated_call_database"]
    private static let validationScopes: Set<String> = ["contacts_tables_v1", "messages_database_v1", "encrypted_contacts_tables_v1", "encrypted_calls_database_v1", "encrypted_messages_database_v1"]

    let source: String
    let component: String
    let reason: String
    let result: String
    let declaredBytes: Int64?
    let rawBytes: Int64?
    let copiedBytes: Int64?
    let encrypted: Bool?
    let entryKeyPresent: Bool?
    let validationCode: String?
    let validationScope: String?
    let producerDigest: String
    let copyDigest: String
    let sidecars: String
    var id: String { source + ":" + component }
    var sourceLabel: String { source == "contacts" ? "Contacts" : source == "calls" ? "Call history" : "Messages" }

    var detail: String {
        func count(_ value: Int64?) -> String { value.map { "\($0) B" } ?? "unknown" }
        func flag(_ value: Bool?) -> String { value.map { $0 ? "yes" : "no" } ?? "unknown" }
        return "\(sourceLabel) · \(component): \(reason) (\(result)). " +
            "Manifest: \(count(declaredBytes)); received: \(count(rawBytes)); local copy: \(count(copiedBytes)). " +
            "Encrypted: \(flag(encrypted)); entry key present: \(flag(entryKeyPresent)). " +
            "Validation: \(validationCode ?? "unknown"); scope: \(validationScope ?? "unknown"). " +
            "Producer digest: \(producerDigest); copy digest: \(copyDigest); sidecars: \(sidecars)."
    }

    init(record: [String: Any]) throws {
        guard Set(record.keys) == Self.fields else { throw SourceDiagnosticDecodingError.invalid }
        func text(_ key: String, _ allowed: Set<String>) throws -> String {
            guard let value = record[key] as? String, allowed.contains(value) else { throw SourceDiagnosticDecodingError.invalid }
            return value
        }
        func optionalText(_ key: String, _ allowed: Set<String>) throws -> String? {
            if record[key] is NSNull { return nil }
            return try text(key, allowed)
        }
        func count(_ key: String) throws -> Int64? {
            if record[key] is NSNull { return nil }
            return try Self.integer(record[key])
        }
        func flag(_ key: String) throws -> Bool? {
            if record[key] is NSNull { return nil }
            guard let value = record[key] as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { throw SourceDiagnosticDecodingError.invalid }
            return value.boolValue
        }
        source = try text("source", ["contacts", "calls", "messages"])
        component = try text("component", ["database", "wal", "shm", "journal"])
        reason = try text("reason", Self.reasons)
        result = try text("result", ["length_mismatch", "copy_failed", "unavailable"])
        declaredBytes = try count("declaredBytes")
        rawBytes = try count("rawBytes")
        copiedBytes = try count("copiedBytes")
        encrypted = try flag("encrypted")
        entryKeyPresent = try flag("entryKeyPresent")
        validationCode = try optionalText("validationCode", Self.validationCodes)
        validationScope = try optionalText("validationScope", Self.validationScopes)
        producerDigest = try text("producerDigest", ["matched", "mismatched", "absent", "unknown"])
        copyDigest = try text("copyDigest", ["matched", "mismatched", "unknown"])
        sidecars = try text("sidecars", ["present", "absent", "unknown"])
    }

    init(from decoder: Decoder) throws {
        guard let record = try DiagnosticJSON(from: decoder).value as? [String: Any] else { throw SourceDiagnosticDecodingError.invalid }
        try self.init(record: record)
    }

    static func integer(_ raw: Any?) throws -> Int64 {
        guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              "cCsSiIlLqQ".contains(String(cString: number.objCType)),
              number.doubleValue >= 0, number.doubleValue <= Double(maximumByteCount) else { throw SourceDiagnosticDecodingError.invalid }
        return number.int64Value
    }

    static func records(_ raw: Any) throws -> [SourceDiagnostic] {
        guard let values = raw as? [[String: Any]], values.count <= 12 else { throw SourceDiagnosticDecodingError.invalid }
        let records = try values.map { try Self(record: $0) }
        guard Set(records.map(\.id)).count == records.count else { throw SourceDiagnosticDecodingError.invalid }
        return records
    }
}

struct SourceDiagnosticAttempt: Decodable, Equatable, Identifiable {
    let attemptId: String
    let createdAt: Int64
    let sourceDiagnostics: [SourceDiagnostic]
    var id: String { attemptId }
    var dateLabel: String {
        guard createdAt <= 253_402_300_799 else { return "Date unavailable" }
        return Date(timeIntervalSince1970: Double(createdAt)).formatted(date: .abbreviated, time: .shortened)
    }
    static func validID(_ value: String) -> Bool {
        value.utf8.count == 36 && value.range(of: #"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"#, options: .regularExpression) != nil
    }
    init(record: [String: Any]) throws {
        guard Set(record.keys) == ["attemptId", "createdAt", "sourceDiagnostics"],
              let attempt = record["attemptId"] as? String, Self.validID(attempt),
              let diagnostics = record["sourceDiagnostics"] else { throw SourceDiagnosticDecodingError.invalid }
        attemptId = attempt
        createdAt = try SourceDiagnostic.integer(record["createdAt"])
        sourceDiagnostics = try SourceDiagnostic.records(diagnostics)
    }
    init(from decoder: Decoder) throws {
        guard let record = try DiagnosticJSON(from: decoder).value as? [String: Any] else { throw SourceDiagnosticDecodingError.invalid }
        try self.init(record: record)
    }
    static func records(_ raw: Any) throws -> [SourceDiagnosticAttempt] {
        guard let values = raw as? [[String: Any]], values.count <= 10 else { throw SourceDiagnosticDecodingError.invalid }
        let attempts = try values.map { try Self(record: $0) }
        guard Set(attempts.map(\.id)).count == attempts.count,
              zip(attempts, attempts.dropFirst()).allSatisfy({ $0.createdAt >= $1.createdAt }) else { throw SourceDiagnosticDecodingError.invalid }
        return attempts
    }
}

private struct DiagnosticJSON: Decodable {
    let value: Any
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { value = NSNull() }
        else if let object = try? container.decode([String: DiagnosticJSON].self) { value = object.mapValues(\.value) }
        else if let array = try? container.decode([DiagnosticJSON].self) { value = array.map(\.value) }
        else if let string = try? container.decode(String.self) { value = string }
        else if let bool = try? container.decode(Bool.self) { value = NSNumber(value: bool) }
        else if let number = try? container.decode(Int64.self) { value = NSNumber(value: number) }
        else { throw SourceDiagnosticDecodingError.invalid }
    }
}

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
    static func decode(_ data: Data) throws -> AgentEvent {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw SourceDiagnosticDecodingError.invalid }
        guard object["source_diagnostics"] == nil, object["source_diagnostic_history"] == nil else { throw SourceDiagnosticDecodingError.invalid }
        if object["kind"] as? String == "selected-payload-diagnostic" {
            var record = object
            record.removeValue(forKey: "kind")
            _ = try SourceDiagnostic(record: record)
        }
        if let records = object["sourceDiagnostics"] { _ = try SourceDiagnostic.records(records) }
        if let history = object["sourceDiagnosticHistory"] { _ = try SourceDiagnosticAttempt.records(history) }
        if object["kind"] as? String == "diagnostic-storage" {
            if object["state"] as? String == "failed" {
                guard Set(object.keys) == ["kind", "state", "code"], object["code"] as? String == "diagnostics_unavailable" else { throw SourceDiagnosticDecodingError.invalid }
            } else {
                guard Set(object.keys) == ["kind", "state", "attemptId"], object["state"] as? String == "retained",
                      let attempt = object["attemptId"] as? String, SourceDiagnosticAttempt.validID(attempt) else { throw SourceDiagnosticDecodingError.invalid }
            }
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Self.self, from: data)
    }

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
    let payloadSha256: String?
    let intentId: String?
    let email: String?
    let course: String?
    let source: String?
    let component: String?
    let result: String?
    let reason: String?
    let declaredBytes: Int64?
    let rawBytes: Int64?
    let copiedBytes: Int64?
    let encrypted: Bool?
    let entryKeyPresent: Bool?
    let validationCode: String?
    let validationScope: String?
    let producerDigest: String?
    let copyDigest: String?
    let sidecars: String?
    let sourceDiagnostics: [SourceDiagnostic]?
    let sourceDiagnosticHistory: [SourceDiagnosticAttempt]?
    let attemptId: String?

    var selectedSourceDiagnostic: SourceDiagnostic? {
        let record: [String: Any] = ["source": source as Any? ?? NSNull(), "component": component as Any? ?? NSNull(),
            "reason": reason as Any? ?? NSNull(), "result": result as Any? ?? NSNull(),
            "declaredBytes": declaredBytes as Any? ?? NSNull(), "rawBytes": rawBytes as Any? ?? NSNull(), "copiedBytes": copiedBytes as Any? ?? NSNull(),
            "encrypted": encrypted as Any? ?? NSNull(), "entryKeyPresent": entryKeyPresent as Any? ?? NSNull(),
            "validationCode": validationCode as Any? ?? NSNull(), "validationScope": validationScope as Any? ?? NSNull(),
            "producerDigest": producerDigest as Any? ?? NSNull(), "copyDigest": copyDigest as Any? ?? NSNull(), "sidecars": sidecars as Any? ?? NSNull()]
        return try? SourceDiagnostic(record: record)
    }
}
