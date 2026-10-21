import Foundation

/// Synthetic account/setup boundary behind the production native client. No sockets or customer operations.
@MainActor final class SimulatedPhoneSetupServer: PhoneRunTransport {
    let runs = SimulatedPhoneRunServer()
    let setupID = UUID().uuidString.lowercased()
    let ownerID = UUID().uuidString.lowercased()
    let enrollmentID = UUID().uuidString.lowercased()
    let courseID = UUID().uuidString.lowercased()
    let bootstrap = String(repeating: "B", count: 43)
    let snapshot = String(repeating: "ab", count: 32)
    var now: Date
    let expiresAt: Date
    var revoked = false
    var faults: [SimulatedPhoneRunServer.Fault] = []
    var transform: ((String, [String: Any]) -> [String: Any])?
    var holdReplies = false
    private var held: [() -> Void] = []
    private(set) var requests: [(path: String, body: Data)] = []
    private(set) var claimBytes: Data?
    private(set) var approvalBytes: Data?
    private(set) var receipt: [String: Any]?
    private(set) var runID: String?

    init(now: Date = Date(timeIntervalSince1970: floor(Date().timeIntervalSince1970))) {
        self.now = now
        expiresAt = now.addingTimeInterval(600)
        runs.now = { [weak self] in self?.now ?? .distantPast }
    }

    static func timestamp(_ date: Date) -> String { ISO8601DateFormatter().string(from: date) }
    static func data(_ fields: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys])
    }

    var link: PendingSetupLink {
        PendingSetupLink(link: PhoneSetupLink(string: "amplifai-phone://setup?setup=\(setupID)&bootstrap=\(bootstrap)")!,
                         returnBrowserBundleID: "com.example.synthetic-browser")
    }

    func claimFields(requestID: String) -> [String: Any] {
        ["protocol": "phone-native-setup-v1", "setupId": setupID, "requestId": requestID, "state": "pending_approval",
         "ownerId": ownerID, "enrollmentId": enrollmentID, "courseId": courseID,
         "destinationEmail": "synthetic.owner@example.invalid", "destinationCourse": "SYNTHETIC course",
         "categories": ["contacts", "calls", "messages"], "expiresAt": Self.timestamp(expiresAt),
         "setupSnapshotSha256": snapshot, "disclosure": [
            "version": "phone-run-v3",
            "sentences": ["AMPLIFai Phone creates a temporary iPhone backup on this Mac and extracts contact, call and message metadata. After you choose contacts and continue, their available metadata is saved to the account and course shown. Backup files and message content stay on your Mac. Interrupted cleanup can leave temporary files."],
            "agreement": "I approve this one phone collection and automatic saving of the contacts I choose, with their available call and message metadata, to this account and course.",
            "policyVersion": "account-2026-10-v7",
            "policySha256": "446be198f41a9c3d335b64f094b3ed2506810c0a5862de1422cd62fa9af5cf89",
            "privacyHref": "/experience/account-privacy?version=account-2026-10-v7#phone-collection-and-saving",
            "sha256": "e119713ec31ab224bcb940a14d8e69072c4b5a5f019ad3ce9188099c2cf12d7e"]]
    }

    func revoke() {
        revoked = true
        if let runID { runs.revoke(runID) }
    }

    func releaseHeldReplies() {
        holdReplies = false
        let callbacks = held
        held.removeAll()
        callbacks.forEach { $0() }
    }

    func post(path: String, body: Data, completion: @escaping @MainActor (PhoneRunTransportResult) -> Void) {
        guard path == PhoneSetupContract.claimPath || path == PhoneSetupContract.approvePath else {
            runs.post(path: path, body: body, completion: completion)
            return
        }
        requests.append((path, body))
        let fault = faults.isEmpty ? nil : faults.removeFirst()
        var result: PhoneRunTransportResult
        if case .dropRequest = fault { result = .failed }
        else {
            var (status, fields) = handle(path: path, body: body)
            if status == 200 || status == 201 { fields = transform?(path, fields) ?? fields }
            switch fault {
            case .loseReply: result = .failed
            case .malformed: result = .response(status: status, contentType: "application/json", body: Data("{".utf8))
            case .status(let replacement): result = .response(status: replacement, contentType: "application/json", body: Data("{}".utf8))
            default: result = .response(status: status, contentType: "application/json", body: Self.data(fields))
            }
        }
        if holdReplies { held.append { completion(result) } } else { completion(result) }
    }

    private func handle(path: String, body: Data) -> (Int, [String: Any]) {
        guard let fields = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              fields["setupId"] as? String == setupID, !revoked else { return (409, [:]) }
        if path == PhoneSetupContract.claimPath {
            guard now < expiresAt, fields["bootstrap"] as? String == bootstrap,
                  let requestID = fields["requestId"] as? String,
                  claimBytes == nil || claimBytes == body else { return (409, [:]) }
            claimBytes = body
            return (200, claimFields(requestID: requestID))
        }
        guard let claimBytes,
              let claim = try? JSONSerialization.jsonObject(with: claimBytes) as? [String: String],
              fields["capability"] as? String == claim["capability"],
              fields["setupSnapshotSha256"] as? String == snapshot else { return (409, [:]) }
        if let approvalBytes, let receipt {
            guard approvalBytes == body, let runID, let run = runs.run(runID),
                  !run.revoked, run.approvalCurrent, now < (run.expiresAt ?? .distantPast) else { return (409, [:]) }
            return (200, receipt)
        }
        guard now < expiresAt, let requestID = fields["requestId"] as? String,
              let capability = fields["runCapability"] as? String,
              let disclosure = fields["disclosure"] as? [String: String],
              disclosure == ["version": "phone-run-v3", "sha256": PhoneSetupDisclosure.pinned.sha256],
              let claimRequest = PhoneSetupClaimRequest(setupID: setupID, bootstrap: bootstrap,
                capability: claim["capability"]!, requestID: claim["requestId"]!),
              let setup = PhoneSetupContract.claim(Self.data(claimFields(requestID: claimRequest.requestID)), request: claimRequest)
        else { return (409, [:]) }
        let run = runs.createNativeRun(setup: setup, capability: capability, approvedAt: now)
        runID = run.runID
        let preparation: [String: Any] = ["protocol": "phone-start-v1", "approvalId": run.approvalID,
            "enrollmentId": enrollmentID, "destinationEmail": setup.email, "destinationCourse": setup.course,
            "categories": setup.categories, "disclosureVersion": "phone-run-v3", "expiresAt": Self.timestamp(run.expiresAt!),
            "state": "current", "operation": NSNull()]
        let receipt: [String: Any] = ["protocol": "phone-native-setup-v1", "setupId": setupID, "requestId": requestID,
            "setupSnapshotSha256": snapshot, "runId": run.runID, "approvalId": run.approvalID, "approvalLocation": "native",
            "disclosureVersion": "phone-run-v3", "disclosureSha256": PhoneSetupDisclosure.pinned.sha256,
            "ownerId": ownerID, "enrollmentId": enrollmentID, "courseId": courseID,
            "destinationEmail": setup.email, "destinationCourse": setup.course, "categories": setup.categories,
            "approvedAt": Self.timestamp(now), "expiresAt": Self.timestamp(run.expiresAt!), "preparation": preparation]
        approvalBytes = body
        self.receipt = receipt
        return (201, receipt)
    }
}
