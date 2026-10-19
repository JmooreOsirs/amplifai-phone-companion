import Foundation

@MainActor
enum SourceDiagnosticChecks {
    private enum Failure: Error { case check(String) }
    private static func require(_ condition: @autoclosure () -> Bool, _ reason: String) throws {
        guard condition() else { throw Failure.check(reason) }
    }
    static func record(_ source: String = "messages") -> [String: Any] {
        ["source": source, "component": "database", "reason": "selected_payload_length", "result": "length_mismatch",
         "declaredBytes": 8192, "rawBytes": 4112, "copiedBytes": 4096, "encrypted": true, "entryKeyPresent": true,
         "validationCode": "sidecar_present", "validationScope": "encrypted_messages_database_v1",
         "producerDigest": "matched", "copyDigest": "matched", "sidecars": "present"]
    }
    private static func event(_ object: [String: Any]) throws -> AgentEvent {
        try AgentEvent.decode(JSONSerialization.data(withJSONObject: object))
    }
    private static func rejected(_ object: [String: Any], _ reason: String) throws {
        do { _ = try event(object) } catch { return }
        throw Failure.check(reason)
    }
    private static func capture(_ diagnostics: [[String: Any]]? = nil, missing: [String] = ["messages"], reason: String = "selected_payload_length") -> [String: Any] {
        var object: [String: Any] = ["kind": "capture", "contacts": [["id": 1, "name": "Fictional", "phoneCount": 0, "phoneEnds": []]],
            "availableCalls": 0, "availableMessages": 0, "missing": missing,
            "unavailableReasons": missing.isEmpty ? [:] : ["messages": reason]]
        if let diagnostics { object["sourceDiagnostics"] = diagnostics }
        return object
    }
    static func run() throws {
        try strictWireRecords()
        try partialSuccessKeepsCurrentFacts()
        try historyRemainsSeparate()
        try supportUsesActualBundleBuild()
        print("Mac source diagnostic strict wire, partial success, history separation and actual-build support checks passed")
    }
    private static func strictWireRecords() throws {
        let valid = record()
        let decoded = try event(["kind": "capture", "sourceDiagnostics": [valid]])
        try require(decoded.sourceDiagnostics?.count == 1,
                    "Exact fourteen-field records must decode")
        for key in SourceDiagnostic.fields {
            var value = valid; value.removeValue(forKey: key)
            try rejected(["kind": "capture", "sourceDiagnostics": [value]], "Every diagnostic key is required, including nullables")
        }
        for key in ["privatePath", "phoneNumber", "messageText", "rawDigest"] {
            var value = valid; value[key] = "/private/owner/content"
            try rejected(["kind": "capture", "sourceDiagnostics": [value]], "Private extra fields must reject the record")
        }
        let badValues: [(String, Any)] = [("source", "owner@example.com"), ("component", "private-db"),
            ("reason", "raw exception"), ("result", "success"), ("declaredBytes", true), ("rawBytes", -1),
            ("copiedBytes", 9_007_199_254_740_992 as Int64), ("encrypted", 1), ("entryKeyPresent", "yes"),
            ("validationCode", "private error"), ("validationScope", "unknown scope"),
            ("producerDigest", String(repeating: "a", count: 64)), ("copyDigest", "absent"), ("sidecars", "maybe")]
        for (key, bad) in badValues {
            var value = valid; value[key] = bad
            try rejected(["kind": "capture", "sourceDiagnostics": [value]], "Invalid diagnostic type or enum must reject: \(key)")
        }
        let encoded = String(data: try JSONSerialization.data(withJSONObject: ["kind": "capture", "sourceDiagnostics": [valid]], options: .sortedKeys), encoding: .utf8)!
        do { _ = try AgentEvent.decode(Data(encoded.replacingOccurrences(of: "8192", with: "8192.0").utf8)); throw Failure.check("Float-shaped integers must not be coerced") }
        catch is SourceDiagnosticDecodingError {}
        try rejected(["kind": "capture", "sourceDiagnostics": [valid, valid]], "Duplicate source/component evidence must reject")
        try rejected(["kind": "capture", "sourceDiagnostics": Array(repeating: valid, count: 13)], "Diagnostic array must be bounded")
        var nullable = valid
        for key in ["declaredBytes", "rawBytes", "copiedBytes", "encrypted", "entryKeyPresent", "validationCode", "validationScope"] { nullable[key] = NSNull() }
        let unknown = try event(["kind": "capture", "sourceDiagnostics": [nullable]])
        try require(unknown.sourceDiagnostics?.first?.detail.contains("unknown") == true, "Null facts remain unknown")
        var selected = valid; selected["kind"] = "selected-payload-diagnostic"; selected["privatePath"] = "/private/owner"
        try rejected(selected, "Flattened diagnostic events must reject private keys too")
    }
    private static func partialSuccessKeepsCurrentFacts() throws {
        let model = CollectorModel(commandWriter: { _ in })
        model.phase = .processing
        var selected = record(); selected["kind"] = "selected-payload-diagnostic"
        model.receive(try event(selected))
        let known = model.currentSourceDiagnostics
        model.receive(try event(capture()))
        try require(model.phase == .selecting && model.currentSourceDiagnostics == known, "Partial capture must preserve current failure facts")
        try require(model.unavailableDescription("messages").contains("unavailable") && model.unavailableDescription("messages").contains("does not prove zero"), "Before Save, unavailable messages must not look like valid zero")
        model.receive(try event(capture([])))
        try require(model.currentSourceDiagnostics == known, "An empty capture array must not erase already observed current facts")
        model.receive(try event(["kind": "diagnostic-storage", "state": "failed", "code": "diagnostics_unavailable"]))
        try require(model.diagnosticRetention == .failed && model.currentSourceDiagnostics == known, "Retention failure must stay visible without discarding current details")
        model.receive(try event(capture([], missing: [])))
        try require(model.currentSourceDiagnostics.isEmpty && model.unavailableDescription("messages").isEmpty && model.availableMessages == 0, "Readable zero stays zero with no inferred failure")
        let inconsistent = CollectorModel(commandWriter: { _ in })
        inconsistent.receive(try event(capture([record()], reason: "unsupported_schema")))
        try require(inconsistent.phase == .error && inconsistent.contacts.isEmpty, "Wrong capture reason must not publish review facts")
    }
    private static func historyRemainsSeparate() throws {
        let attempt: [String: Any] = ["attemptId": "12345678-1234-4123-8123-123456789abc", "createdAt": 1_800_000_000, "sourceDiagnostics": [record()]]
        let server = SimulatedPhoneRunServer()
        let model = CollectorModel(commandWriter: { _ in }, runAuthority: PhoneRunFlowChecks.client(server))
        model.receive(try event(["kind": "residue", "sessions": [], "sourceDiagnosticHistory": [attempt]]))
        try require(model.sourceDiagnosticHistory.count == 1 && model.currentSourceDiagnostics.isEmpty, "Restored diagnostics must be history only")
        model.receive(try event(capture([], missing: [])))
        try require(model.currentSourceDiagnostics.isEmpty && model.sourceDiagnosticHistory.count == 1 && model.missingSources.isEmpty, "Previous failure cannot become a new capture failure")
        model.disconnect()
        model.phase = .processing
        var selected = record(); selected["kind"] = "selected-payload-diagnostic"
        model.receive(try event(selected))
        model.phase = .idle
        try require(!model.currentSourceDiagnostics.isEmpty, "Reset fixture must contain current failure evidence")
        try require(model.admitRunLink(PhoneRunFlowChecks.link(server, server.createRun())), "A new run link is admitted when idle")
        try require(model.startCollection(), "New collection setup must be available in fixture")
        model.receive(try event(["kind": "backup-encryption-status", "backupEncrypted": false]))
        model.finished(mode: "backup-encryption-status", code: 0)
        // The simulated helper wire records the launch; no process can start in this fixture.
        try require(!model.helperRunning && model.phase == .connecting && model.currentSourceDiagnostics.isEmpty &&
                    model.sourceDiagnosticHistory.count == 1, "New run clears only current facts")
        var invalid = attempt; invalid["attemptId"] = "12345678-1234-1123-8123-123456789abc"
        try rejected(["kind": "residue", "sourceDiagnosticHistory": [invalid]], "History identity must be canonical UUIDv4")
        invalid = attempt; invalid["createdAt"] = true
        try rejected(["kind": "residue", "sourceDiagnosticHistory": [invalid]], "History timestamp must be integer seconds")
        try rejected(["kind": "residue", "sourceDiagnosticHistory": [attempt, attempt]], "History IDs must be unique")
        try rejected(["kind": "residue", "sourceDiagnosticHistory": Array(repeating: attempt, count: 11)], "History attempts must be bounded")
        try rejected(["kind": "diagnostic-storage", "state": "failed", "code": "private path"], "Storage failure has one fixed safe code")
        model.receive(try event(["kind": "diagnostic-storage", "state": "retained", "attemptId": attempt["attemptId"]!]))
        try require(model.diagnosticRetention == .retained, "Validated retained status is displayed separately")
    }
    private static func supportUsesActualBundleBuild() throws {
        guard let actual = SupportDiagnosticReporter.validatedBuild(Bundle.main.infoDictionary) else { throw Failure.check("Test executable must embed current Info.plist") }
        try require(actual == SupportDiagnosticReporter.build, "Current bundle build must be the support build")
        for information in [["CFBundleVersion": actual], ["CFBundleVersion": "900000002"]] {
            let model = CollectorModel(bundleInformation: information)
            model.receive(try event(["kind": "error", "code": "selected_payload_length", "stage": "processing"]))
            let code = model.safeSupportCode
            try require(code.split(separator: "|").count == 9 && code.contains("|\(information["CFBundleVersion"]!)|"), "A1 must use actual supplied bundle build")
            guard let request = SupportDiagnosticReporter.request(for: code, bundleInformation: information), let body = request.httpBody else { throw Failure.check("Validated actual build must produce safe request") }
            let object = try JSONSerialization.jsonObject(with: body) as? [String: String]
            try require(object == ["code": code], "A1 body must retain its legacy single field")
            try require(SupportDiagnosticReporter.request(for: code + "|private", bundleInformation: information) == nil, "No source/private facts enter A1")
        }
        for value in ["", "0", "001", "123\n", "private/path", "1234567890123", true, 123] as [Any] {
            let information = ["CFBundleVersion": value]
            try require(SupportDiagnosticReporter.validatedBuild(information) == nil, "Unsafe bundle identity fails closed")
            let model = CollectorModel(bundleInformation: information)
            model.receive(try event(["kind": "error", "code": "collection_failed", "stage": "processing"]))
            try require(model.safeSupportCode.isEmpty, "No release fallback may manufacture a support build")
        }
    }
}

