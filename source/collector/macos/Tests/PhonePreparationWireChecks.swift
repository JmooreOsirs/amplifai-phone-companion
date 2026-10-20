import Foundation

/// Synthetic authority wire checks. No network, device, helper or account operation.
@MainActor @main
enum PhonePreparationWireChecks {
    enum Failure: Error { case check(String) }
    static let run = "00000000-0000-4000-8000-000000000001"
    static let approval = "00000000-0000-4000-8000-000000000002"
    static let enrollment = "00000000-0000-4000-8000-000000000003"
    static let operation = "00000000-0000-4000-8000-000000000004"
    static let handoff = "00000000-0000-4000-8000-000000000005"
    static let capability = String(repeating: "a", count: 43)
    static let bootstrap = String(repeating: "b", count: 43)
    static let digest = String(repeating: "ab", count: 32)
    static let expiry = "2026-10-21T06:00:00.000Z"
    static let admitted = "2026-10-20T18:30:00.000Z"

    final class Transport: PhoneRunTransport {
        var replies: [PhoneRunTransportResult]
        var requests: [(path: String, body: Data)] = []
        init(_ replies: [PhoneRunTransportResult]) { self.replies = replies }
        func post(path: String, body: Data, completion: @escaping @MainActor (PhoneRunTransportResult) -> Void) {
            requests.append((path, body))
            completion(replies.isEmpty ? .failed : replies.removeFirst())
        }
    }

    static func data(_ fields: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    }

    static func statusFields(started: Bool = false) -> [String: Any] {
        ["protocol": "phone-start-v1", "approvalId": approval, "enrollmentId": enrollment,
         "destinationEmail": "synthetic.owner@example.invalid", "destinationCourse": "Synthetic course",
         "categories": ["messages", "contacts"], "disclosureVersion": "phone-run-v2", "expiresAt": expiry,
         "state": "current", "operation": started ? ["operationId": operation, "admittedAt": admitted] as Any : NSNull()]
    }

    static func bindingFields() -> [String: Any] {
        ["protocol": "phone-start-v1", "operationId": operation, "approvalId": approval, "enrollmentId": enrollment,
         "destinationEmail": "synthetic.owner@example.invalid", "destinationCourse": "Synthetic course",
         "categories": ["contacts", "messages"]]
    }

    static func response(_ fields: [String: Any]) throws -> PhoneRunTransportResult {
        .response(status: 200, contentType: "application/json; charset=utf-8", body: try data(fields))
    }

    static func client(_ transport: Transport) -> PhoneRunAuthorityClient {
        PhoneRunAuthorityClient(transport: transport, schedule: PhoneRunAuthorityClient.immediate,
                                admissionProtocol: .preparation)
    }

    static func require(_ value: @autoclosure () throws -> Bool, _ reason: String) throws {
        guard try value() else { throw Failure.check(reason) }
    }

    static func preparationTransportPaths() throws {
        let prefix = "/api/v1/phone-run-approvals/preparation/"
        for action in ["claim", "status", "start", "bind"] {
            let request = URLSessionPhoneRunTransport.request(path: prefix + action, body: Data("{}".utf8))
            try require(request != nil, "Preparation \(action) must have an allowlisted transport route")
            try require(request?.url?.host == PhoneRunContract.host && request?.url?.scheme == "https",
                        "Preparation routes use the fixed TLS authority")
            try require(request?.httpMethod == "POST" && request?.url?.query == nil &&
                        request?.value(forHTTPHeaderField: "Cookie") == nil &&
                        request?.value(forHTTPHeaderField: "Origin") == nil && request?.httpShouldHandleCookies == false,
                        "Native preparation sends POST without browser credentials or query")
        }
        for path in [prefix + "verify-intent", prefix + "claim/", prefix + "claim?fallback=legacy",
                     "https://other.invalid" + prefix + "claim"] {
            try require(URLSessionPhoneRunTransport.request(path: path, body: Data()) == nil,
                        "Unspecified preparation routes must be refused")
        }
        try require(URLSessionPhoneRunTransport.request(path: prefix + "start", body: Data(repeating: 0, count: 1025)) == nil,
                    "Preparation request bytes remain bounded")
    }

    static func strictStatusAndStart() throws {
        let fields = statusFields()
        let parsed = PhoneRunContract.preparationStatus(try data(fields))
        try require(parsed?.claim.approvalID == approval && parsed?.claim.startExpiresAt == nil && parsed?.operation == nil,
                    "A preparation claim has no Start authority or legacy Start deadline")
        try require(parsed?.claim.categories == ["contacts", "messages"], "Approved categories have canonical order")
        for timestamp in ["2026-10-21T06:00:00Z", "2026-10-21T00:00:00-06:00", "2026-10-21T11:30:00+05:30"] {
            var changed = fields
            changed["expiresAt"] = timestamp
            try require(PhoneRunContract.preparationStatus(try data(changed))?.claim.expiresAt == parsed?.claim.expiresAt,
                        "Valid ISO8601 whole seconds and timezone offsets retain the same expiry")
        }
        let started = PhoneRunContract.startedPreparation(try data(statusFields(started: true)))
        try require(started?.operation?.operationID == operation && started?.operation?.admittedAt == PhoneRunContract.date(admitted),
                    "An admitted Start preserves its exact operation and admission time")
        try require(PhoneRunContract.startedPreparation(try data(fields)) == nil, "A null operation cannot admit Start")
        for state in ["current", "replacement_required", "expired", "revoked"] {
            var changed = fields
            changed["state"] = state
            try require(PhoneRunContract.preparationStatus(try data(changed))?.state.rawValue == state,
                        "Preparation preserves every authority state for the model")
        }
        for key in fields.keys {
            var changed = fields
            changed.removeValue(forKey: key)
            try require(PhoneRunContract.preparationStatus(try data(changed)) == nil, "Missing \(key) is refused")
        }
        let invalid: [(String, Any)] = [
            ("protocol", "phone-start-v2"), ("protocol", NSNull()), ("disclosureVersion", "phone-run-v1"),
            ("approvalId", "invalid"), ("enrollmentId", enrollment.uppercased() + "x"),
            ("destinationEmail", "owner\n@example.invalid"), ("destinationCourse", ""),
            ("categories", []), ("categories", ["calls"]), ("categories", ["contacts", "contacts"]),
            ("categories", ["contacts", "unknown"]), ("categories", ["contacts", 1]),
            ("expiresAt", "not-a-date"), ("expiresAt", "2026-02-30T18:00:00Z"),
            ("expiresAt", "2026-10-21T06:00:00Zjunk"), ("expiresAt", 123), ("state", "pending"),
            ("operation", false), ("operation", []), ("operation", [:]),
            ("operation", ["operationId": operation]),
            ("operation", ["operationId": "bad", "admittedAt": admitted]),
            ("operation", ["operationId": operation, "admittedAt": "bad"]),
            ("operation", ["operationId": operation, "admittedAt": "2026-10-20T18:30:00Zjunk"]),
            ("operation", ["operationId": operation, "admittedAt": expiry]),
            ("operation", ["operationId": operation, "admittedAt": admitted, "extra": true]),
            ("startExpiresAt", expiry), ("extra", true)
        ]
        for (key, value) in invalid {
            var changed = fields
            changed[key] = value
            try require(PhoneRunContract.preparationStatus(try data(changed)) == nil, "Invalid \(key) is refused")
        }
        try require(PhoneRunContract.preparationStatus(nil) == nil &&
                    PhoneRunContract.preparationStatus(Data(repeating: 32, count: 2049)) == nil &&
                    PhoneRunContract.preparationStatus(Data("[]".utf8)) == nil,
                    "Missing, oversized and non-object preparation responses are refused")
    }

    static func strictBinding() throws {
        let fields = bindingFields()
        let parsed = PhoneRunContract.preparationBinding(try data(fields))
        try require(parsed?.operationID == operation && parsed?.destination.approvalID == approval &&
                    parsed?.destination.enrollmentID == enrollment, "Binding preserves operation and destination authority")
        for key in fields.keys {
            var changed = fields
            changed.removeValue(forKey: key)
            try require(PhoneRunContract.preparationBinding(try data(changed)) == nil, "Binding requires \(key)")
        }
        for (key, value) in [("protocol", "phone-start-v2" as Any), ("operationId", "bad"),
                             ("approvalId", NSNull()), ("categories", ["calls"]), ("extra", true)] {
            var changed = fields
            changed[key] = value
            try require(PhoneRunContract.preparationBinding(try data(changed)) == nil, "Binding rejects invalid \(key)")
        }
    }

    static func requestContracts() throws {
        let start = PhoneRunStartRequest(runID: run, capability: capability, expectedApprovalID: approval, operationID: operation)!
        let startFields = try JSONSerialization.jsonObject(with: start.body) as! [String: String]
        try require(startFields == ["runId": run, "capability": capability, "expectedApprovalId": approval, "operationId": operation],
                    "Start body contains only exact original approval and operation fields")
        let bind = PhoneRunPreparationBindRequest(runID: run, capability: capability, expectedApprovalID: approval,
                                                 operationID: operation, handoffID: handoff, payloadSha256: digest)!
        let bindFields = try JSONSerialization.jsonObject(with: bind.body) as! [String: String]
        try require(bindFields == startFields.merging(["handoffId": handoff, "payloadSha256": digest]) { _, new in new },
                    "Preparation bind binds exact admitted operation, handoff and payload")
        for index in 0..<4 {
            var values = [run, capability, approval, operation]
            values[index] = "invalid"
            try require(PhoneRunStartRequest(runID: values[0], capability: values[1], expectedApprovalID: values[2],
                                            operationID: values[3]) == nil, "Start validates every ID and capability")
            try require(PhoneRunPreparationBindRequest(runID: values[0], capability: values[1], expectedApprovalID: values[2],
                                                      operationID: values[3], handoffID: handoff, payloadSha256: digest) == nil,
                        "Bind validates every ID and capability")
        }
        try require(PhoneRunPreparationBindRequest(runID: run, capability: capability, expectedApprovalID: approval,
                                                  operationID: operation, handoffID: "invalid", payloadSha256: digest) == nil &&
                    PhoneRunPreparationBindRequest(runID: run, capability: capability, expectedApprovalID: approval,
                                                  operationID: operation, handoffID: handoff, payloadSha256: digest.uppercased()) == nil,
                    "Bind requires a UUID handoff and lowercase 64-character digest")
    }

    static func transportRetriesAndRefusals() throws {
        let claim = PhoneRunClaimRequest(runID: run, bootstrap: bootstrap, capability: capability)!
        let status = PhoneRunStatusRequest(runID: run, capability: capability)!
        let start = PhoneRunStartRequest(runID: run, capability: capability, expectedApprovalID: approval, operationID: operation)!
        let bind = PhoneRunPreparationBindRequest(runID: run, capability: capability, expectedApprovalID: approval,
                                                 operationID: operation, handoffID: handoff, payloadSha256: digest)!
        let transport = Transport([try response(statusFields()), try response(statusFields()),
                                   .failed, .response(status: 503, contentType: "application/problem+json", body: nil),
                                   try response(statusFields(started: true)), try response(bindingFields())])
        let authority = client(transport)
        var accepted: [Bool] = []
        authority.preparationClaim(claim) { if case .accepted = $0 { accepted.append(true) } }
        authority.preparationStatus(status) { if case .accepted = $0 { accepted.append(true) } }
        authority.start(start) { if case .accepted = $0 { accepted.append(true) } }
        authority.preparationBind(bind) { if case .accepted = $0 { accepted.append(true) } }
        try require(accepted.count == 4, "All four preparation operations use the strict production client parser")
        let prefix = "/api/v1/phone-run-approvals/preparation/"
        try require(transport.requests.map(\.path) == ["claim", "status", "start", "start", "start", "bind"].map { prefix + $0 },
                    "Retries never switch endpoint or protocol")
        try require(transport.requests.dropFirst(2).prefix(3).allSatisfy { $0.body == start.body },
                    "Lost Start and server-error replies retry identical operation bytes")

        let unavailable = Transport([.response(status: 404, contentType: "application/problem+json",
                                               body: try data(["code": "phone_run_unavailable"]))])
        let unavailableClient = client(unavailable)
        var refusal: PhoneRunOutcome<PhonePreparationStatus>?
        unavailableClient.preparationClaim(claim) { refusal = $0 }
        try require(refusal == .refused(status: 404, code: "phone_run_unavailable") && unavailable.requests.count == 1 &&
                    unavailable.requests[0].path == prefix + "claim", "Missing additive routes refuse without legacy fallback")

        for reply in [try response(statusFields()),
                      .response(status: 200, contentType: "text/html", body: try data(statusFields(started: true))),
                      .response(status: 200, contentType: "application/json", body: Data(repeating: 32, count: 2049))] {
            let malformed = Transport(Array(repeating: reply, count: 3))
            let malformedClient = client(malformed)
            var result: PhoneRunOutcome<PhonePreparationStatus>?
            malformedClient.start(start) { result = $0 }
            try require(result == .malformed && malformed.requests.count == 3 &&
                        malformed.requests.allSatisfy { $0.path == prefix + "start" && $0.body == start.body },
                        "Null Start, non-JSON and oversized success remain malformed after bounded exact retries")
        }
        let failed = Transport(Array(repeating: .failed, count: 3))
        let failedClient = client(failed)
        var uncertainty: PhoneRunOutcome<PhonePreparationBinding>?
        failedClient.preparationBind(bind) { uncertainty = $0 }
        try require(uncertainty == .uncertain && failed.requests.count == 3 &&
                    failed.requests.allSatisfy { $0.path == prefix + "bind" && $0.body == bind.body },
                    "Uncertain bind retries exactly and never grants destination authority")
    }

    static func legacyAndProductionSelection() throws {
        try require(PhoneRunAuthorityClient.production().admissionProtocol == .preparation,
                    "Production explicitly selects preparation admission")
        let transport = Transport([])
        try require(PhoneRunAuthorityClient(transport: transport).admissionProtocol == .legacy,
                    "Existing injected clients preserve legacy admission by default")
        var legacy = statusFields()
        legacy.removeValue(forKey: "protocol")
        legacy.removeValue(forKey: "operation")
        legacy["startExpiresAt"] = "2026-10-20T18:10:00Z"
        try require(PhoneRunContract.status(try data(legacy)) != nil && PhoneRunContract.preparationStatus(try data(legacy)) == nil,
                    "Legacy status remains valid only through its original decoder")
        legacy.removeValue(forKey: "state")
        try require(PhoneRunContract.claim(try data(legacy)) != nil, "Legacy v2 claim still requires its Start deadline")
        legacy.removeValue(forKey: "startExpiresAt")
        legacy["disclosureVersion"] = "phone-run-v1"
        try require(PhoneRunContract.claim(try data(legacy)) != nil, "Legacy v1 claim shape is preserved")
        try require(PhoneRunContract.status(try data(statusFields())) == nil &&
                    PhoneRunContract.claim(try data(statusFields())) == nil &&
                    PhoneRunContract.destination(try data(bindingFields())) == nil,
                    "Preparation success cannot be misread as legacy authority")
    }

    static func main() throws {
        try preparationTransportPaths()
        try strictStatusAndStart()
        try strictBinding()
        try requestContracts()
        try transportRetriesAndRefusals()
        try legacyAndProductionSelection()
        print("PASS: preparation routes, strict claim/status/Start/bind, exact request validation, bounded same-byte retries, no legacy fallback, legacy compatibility")
    }
}
