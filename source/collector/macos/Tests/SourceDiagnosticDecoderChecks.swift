import Foundation

/// Standalone decoder regression checks. No app, helper, phone or UI is launched.
@main
struct SourceDiagnosticDecoderChecks {
    enum Failure: Error { case check(String) }

    static func require(_ condition: @autoclosure () -> Bool, _ reason: String) throws {
        guard condition() else { throw Failure.check(reason) }
    }

    static func decode(_ object: [String: Any]) throws -> AgentEvent {
        try AgentEvent.decode(JSONSerialization.data(withJSONObject: object))
    }

    static func main() throws {
        let record: [String: Any] = [
            "source": "messages", "component": "database", "reason": "selected_payload_length",
            "result": "length_mismatch", "declaredBytes": 8192, "rawBytes": 12304,
            "copiedBytes": 12288, "encrypted": true, "entryKeyPresent": true,
            "validationCode": "unsupported_size_growth", "validationScope": "encrypted_messages_database_v1",
            "producerDigest": "matched", "copyDigest": "matched", "sidecars": "absent"
        ]
        let capture = try decode(["kind": "capture", "sourceDiagnostics": [record]])
        try require(capture.sourceDiagnostics?.first?.validationCode == "unsupported_size_growth",
                    "Growth diagnostics must survive strict native decoding")
        try require(capture.sourceDiagnostics?.first?.result == "length_mismatch",
                    "Matched byte proofs must remain a failed size validation")
        var event = record; event["kind"] = "selected-payload-diagnostic"
        _ = try decode(event)
        let attempt: [String: Any] = ["attemptId": "12345678-1234-4123-8123-123456789abc",
                                    "createdAt": 1_800_000_000, "sourceDiagnostics": [record]]
        let history = try decode(["kind": "residue", "sourceDiagnosticHistory": [attempt]])
        try require(history.sourceDiagnosticHistory?.first?.sourceDiagnostics.first?.validationCode == "unsupported_size_growth",
                    "Historical failure evidence must retain the new code")
        var changedSnapshot = record
        changedSnapshot["validationCode"] = "validated_messages_one_page_growth"
        changedSnapshot["result"] = "unavailable"
        changedSnapshot["reason"] = "source_integrity"
        let changed = try decode(["kind": "capture", "sourceDiagnostics": [changedSnapshot]])
        try require(changed.sourceDiagnostics?.first?.validationCode == "validated_messages_one_page_growth" &&
                    changed.sourceDiagnostics?.first?.reason == "source_integrity",
                    "Later snapshot failure retains the bounded one-page validation evidence")
        for fields in [["validationCode": "unbounded-private-error"], ["messageBody": "synthetic-private-value"]] {
            var invalid = record
            for (key, value) in fields { invalid[key] = value }
            do {
                _ = try decode(["kind": "capture", "sourceDiagnostics": [invalid]])
            } catch { continue }
            throw Failure.check("Unknown codes and personal fields must reject")
        }
        print("PASS: 7 standalone source diagnostic decoder checks; no UI or device access")
    }
}
