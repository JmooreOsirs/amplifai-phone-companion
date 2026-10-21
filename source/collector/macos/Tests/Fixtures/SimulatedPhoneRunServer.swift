import Foundation

/// SYNTHETIC FIXTURE: an in-process stand-in for the website's phone-run approval routes (claim, bind,
/// verify-intent), following the written contract. It opens no socket and reaches no server. Checks and the
/// FAKE DATA demo plug it into the production `PhoneRunAuthorityClient` as its transport, so the real request
/// bytes, exact retries and strict parsing are exercised.
@MainActor final class SimulatedPhoneRunServer: PhoneRunTransport {
    struct Run {
        let runID: String
        var approvalID: String
        let bootstrap: String
        let email: String
        var enrollmentID: String
        var disclosureVersion: String
        var course: String
        var categories: [String]
        var bootstrapExpiresAt: Date
        var capability: String?
        var claimedAt: Date?
        var expiresAt: Date?
        var revoked = false
        /// False once the account, course, scope, session or a fence changed: exact claim retries are refused.
        var approvalCurrent = true
        var boundHandoffID: String?
        var boundPayloadSha256: String?
        var preparationMode = false
        var operationID: String?
        var admittedAt: Date?
    }
    /// The destination a website connect intent recorded (the signed-in account and course).
    struct Intent {
        let email: String
        let course: String
    }
    enum Fault {
        /// The request never arrives.
        case dropRequest
        /// The request is processed (and commits) but its reply is lost.
        case loseReply
        /// The request is processed, then this status is returned instead.
        case status(Int)
        /// The request is processed, then a 200 with an unreadable body is returned.
        case malformed
    }

    private(set) var runs: [String: Run] = [:]
    private var approvalScopes: [String: [String]] = [:]
    private var consumedEnrollment: [String: String] = [:]
    var intents: [String: Intent] = [:]
    private(set) var requests: [(path: String, body: Data)] = []
    var faults: [Fault] = []
    var faultsByPath: [String: [Fault]] = [:]
    /// Replaces the next successful reply object for a path (mismatch checks).
    var replyOverrides: [String: [String: Any]] = [:]
    var replyTransforms: [String: ([String: Any]) -> [String: Any]] = [:]
    var now: () -> Date = Date.init
    /// When set, replies are held until `releaseHeldReplies()` (late and stale reply checks).
    var holdReplies = false
    var holdPaths: Set<String> = []
    var replyObserver: ((String) -> Void)?
    private var held: [(path: String, deliver: () -> Void)] = []
    func heldReplyCount(for path: String) -> Int { held.filter { $0.path == path }.count }

    var claimCount: Int { requests.filter { $0.path == PhoneRunContract.claimPath }.count }
    var statusCount: Int { requests.filter { $0.path == PhoneRunContract.statusPath }.count }
    var bindCount: Int { requests.filter { $0.path == PhoneRunContract.bindPath }.count }
    var verifyCount: Int { requests.filter { $0.path == PhoneRunContract.verifyIntentPath }.count }

    /// The website's own step after the owner's Agree and collect: a run with a one-use bootstrap.
    func createRun(email: String = "synthetic.owner@example.invalid", course: String = "SYNTHETIC course",
                   categories: [String] = ["contacts", "calls", "messages"],
                   disclosureVersion: String = PhoneRunContract.disclosureVersion) -> (runID: String, bootstrap: String) {
        let runID = UUID().uuidString.lowercased()
        let bootstrap = PhoneRunContract.newCapability() ?? String(repeating: "B", count: 43)
        let approvalID = UUID().uuidString.lowercased()
        approvalScopes[approvalID] = categories
        runs[runID] = Run(runID: runID, approvalID: approvalID, bootstrap: bootstrap, email: email,
                          enrollmentID: UUID().uuidString.lowercased(), disclosureVersion: disclosureVersion,
                          course: course, categories: categories,
                          bootstrapExpiresAt: now().addingTimeInterval(10 * 60))
        return (runID, bootstrap)
    }

    func appLink(_ run: (runID: String, bootstrap: String)) -> URL {
        URL(string: "amplifai-phone://start?run=\(run.runID)&bootstrap=\(run.bootstrap)")!
    }

    /// Native setup fixture admission: the real server creates this only in its atomic consent transaction.
    func createNativeRun(setup: PhoneSetupClaim, capability: String, approvedAt: Date) -> Run {
        let created = createRun(email: setup.email, course: setup.course, categories: setup.categories,
                                disclosureVersion: PhoneSetupContract.disclosureVersion)
        var run = runs[created.runID]!
        run.enrollmentID = setup.enrollmentID
        run.capability = capability
        run.claimedAt = approvedAt
        run.expiresAt = approvedAt.addingTimeInterval(PhoneRunContract.runLifetime)
        run.preparationMode = true
        runs[created.runID] = run
        return run
    }

    func revoke(_ runID: String) { runs[runID]?.revoked = true }
    func changeApproval(_ runID: String) { runs[runID]?.approvalCurrent = false }
    /// A replacement approval in the account for the same owner and enrollment.
    func replace(_ runID: String, course: String, categories: [String], enrollmentID: String? = nil,
                 disclosureVersion: String? = nil) {
        guard var run = runs[runID], consumedEnrollment[runID] == nil || enrollmentID == nil || enrollmentID == run.enrollmentID else { return }
        run.approvalID = UUID().uuidString.lowercased()
        approvalScopes[run.approvalID] = categories
        run.course = course
        run.categories = categories
        run.enrollmentID = enrollmentID ?? run.enrollmentID
        run.disclosureVersion = disclosureVersion ?? run.disclosureVersion
        run.approvalCurrent = true
        runs[runID] = run
    }
    func consume(_ runID: String) { consumedEnrollment[runID] = runs[runID]?.enrollmentID }
    func run(_ runID: String) -> Run? { runs[runID] }
    /// Mismatch checks: the server holds a binding for another handoff or digest.
    func forceBinding(_ runID: String, handoffID: String, digest: String) {
        runs[runID]?.boundHandoffID = handoffID
        runs[runID]?.boundPayloadSha256 = digest
    }
    func forceOperation(_ runID: String, operationID: String) {
        runs[runID]?.operationID = operationID
        runs[runID]?.admittedAt = now()
    }

    /// `keepingForReplay` injects a duplicate late transport callback for generation-fence checks.
    func releaseHeldReplies(path: String? = nil, keepingForReplay: Bool = false) {
        let replies = held.filter { path == nil || $0.path == path }
        if !keepingForReplay { held.removeAll { path == nil || $0.path == path } }
        if let path { holdPaths.remove(path) }
        else { holdReplies = false; holdPaths = [] }
        for reply in replies { reply.deliver() }
    }

    func post(path: String, body: Data, completion: @escaping @MainActor (PhoneRunTransportResult) -> Void) {
        requests.append((path, body))
        let fault: Fault?
        if var pathFaults = faultsByPath[path], !pathFaults.isEmpty {
            fault = pathFaults.removeFirst()
            faultsByPath[path] = pathFaults
        } else { fault = faults.isEmpty ? nil : faults.removeFirst() }
        let deliver: (PhoneRunTransportResult) -> Void = { [weak self] result in
            guard let self else { return }
            if self.holdReplies || self.holdPaths.contains(path) {
                self.held.append((path, { completion(result) }))
            } else { completion(result) }
            self.replyObserver?(path)
        }
        if case .dropRequest = fault { deliver(.failed); return }
        var (status, object) = handle(path, body)
        if status == 200, let override = replyOverrides.removeValue(forKey: path) { object = override }
        if status == 200, let transform = replyTransforms.removeValue(forKey: path) { object = transform(object) }
        switch fault {
        case .loseReply: deliver(.failed)
        case .status(let code): deliver(.response(status: code, contentType: "application/json", body: Data("{}".utf8)))
        case .malformed: deliver(.response(status: 200, contentType: "application/json", body: Data(#"{"approvalId":"#.utf8)))
        default:
            let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
            deliver(.response(status: status, contentType: "application/json; charset=utf-8", body: data))
        }
    }

    private static func timestamp(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    private func claimFields(_ run: Run, includeStart: Bool) -> [String: Any] {
        var fields: [String: Any] = ["approvalId": run.approvalID, "enrollmentId": run.enrollmentID,
            "destinationEmail": run.email, "destinationCourse": run.course, "categories": run.categories,
            "disclosureVersion": run.disclosureVersion, "expiresAt": Self.timestamp(run.expiresAt ?? now())]
        if includeStart { fields["startExpiresAt"] = Self.timestamp((run.claimedAt ?? now()).addingTimeInterval(10 * 60)) }
        return fields
    }

    private func refusal(_ status: Int, _ code: String) -> (Int, [String: Any]) { (status, ["code": code]) }

    private func preparationFields(_ run: Run) -> [String: Any] {
        var fields = claimFields(run, includeStart: false)
        fields["protocol"] = "phone-start-v1"
        fields["state"] = run.revoked ? "revoked" : now() >= (run.expiresAt ?? now()) ? "expired" :
            run.approvalCurrent ? "current" : "replacement_required"
        fields["operation"] = run.operationID.map {
            ["operationId": $0, "admittedAt": Self.timestamp(run.admittedAt!)]
        } as Any? ?? NSNull()
        return fields
    }

    private func handle(_ path: String, _ body: Data) -> (Int, [String: Any]) {
        guard let fields = try? JSONSerialization.jsonObject(with: body) as? [String: String] else {
            return refusal(400, "phone_connect_invalid")
        }
        switch path {
        case PhoneRunContract.claimPath, PhoneRunContract.preparationClaimPath:
            guard Set(fields.keys) == ["runId", "bootstrap", "capability"], let runID = fields["runId"],
                  let bootstrap = fields["bootstrap"], let capability = fields["capability"],
                  PhoneRunContract.isCapability(capability), capability != bootstrap else {
                return refusal(400, "phone_connect_invalid")
            }
            guard var run = runs[runID] else { return refusal(404, "phone_run_unavailable") }
            let preparation = path == PhoneRunContract.preparationClaimPath
            guard run.capability == nil || run.preparationMode == preparation,
                  !preparation || run.disclosureVersion == "phone-run-v2" else { return refusal(409, "phone_run_changed") }
            guard run.bootstrap == bootstrap, !run.revoked, run.approvalCurrent else { return refusal(409, "phone_run_changed") }
            if let claimed = run.capability {
                // An exact retry reads the same claim back for ten minutes; anything else is refused.
                guard claimed == capability, let at = run.claimedAt,
                      run.disclosureVersion == "phone-run-v2" || now().timeIntervalSince(at) <= 10 * 60,
                      let expires = run.expiresAt, now() < expires else { return refusal(409, "phone_run_changed") }
            } else {
                guard now() < run.bootstrapExpiresAt else { return refusal(409, "phone_run_changed") }
                run.capability = capability
                run.claimedAt = now()
                run.expiresAt = now().addingTimeInterval(PhoneRunContract.runLifetime)
                run.preparationMode = preparation
                runs[runID] = run
            }
            return (200, preparation ? preparationFields(run) : claimFields(run, includeStart: run.disclosureVersion == "phone-run-v2"))
        case PhoneRunContract.statusPath, PhoneRunContract.preparationStatusPath:
            guard Set(fields.keys) == ["runId", "capability"], let runID = fields["runId"], let run = runs[runID],
                  run.capability == fields["capability"], let expires = run.expiresAt,
                  now() < expires.addingTimeInterval(24 * 60 * 60) else { return refusal(409, "phone_run_changed") }
            guard run.preparationMode == (path == PhoneRunContract.preparationStatusPath) else { return refusal(409, "phone_run_changed") }
            if run.preparationMode { return (200, preparationFields(run)) }
            var result = claimFields(run, includeStart: true)
            result["state"] = run.revoked ? "revoked" : now() >= expires ? "expired" :
                run.approvalCurrent ? "current" : "replacement_required"
            return (200, result)
        case PhoneRunContract.startPath:
            guard Set(fields.keys) == ["runId", "capability", "expectedApprovalId", "operationId"],
                  let runID = fields["runId"], var run = runs[runID], run.preparationMode,
                  run.capability == fields["capability"], run.approvalID == fields["expectedApprovalId"],
                  let operation = fields["operationId"], PhoneRunContract.isID(operation),
                  !run.revoked, run.approvalCurrent, let expiry = run.expiresAt, now() < expiry,
                  run.operationID == nil || run.operationID == operation else { return refusal(409, "phone_run_changed") }
            if run.operationID == nil { run.operationID = operation; run.admittedAt = now(); runs[runID] = run }
            return (200, preparationFields(run))
        case PhoneRunContract.preparationBindPath:
            guard Set(fields.keys) == ["runId", "capability", "expectedApprovalId", "operationId", "handoffId", "payloadSha256"],
                  let runID = fields["runId"], var run = runs[runID], run.preparationMode,
                  run.capability == fields["capability"], run.approvalID == fields["expectedApprovalId"],
                  let operation = run.operationID, operation == fields["operationId"],
                  let handoff = fields["handoffId"], let digest = fields["payloadSha256"],
                  PhoneRunContract.isID(handoff), PhoneRunContract.isDigest(digest),
                  !run.revoked, run.approvalCurrent, let expiry = run.expiresAt, now() < expiry,
                  run.boundHandoffID == nil || (run.boundHandoffID == handoff && run.boundPayloadSha256 == digest) else {
                return refusal(409, "phone_run_changed")
            }
            run.boundHandoffID = handoff
            run.boundPayloadSha256 = digest
            runs[runID] = run
            return (200, ["protocol": "phone-start-v1", "operationId": operation, "approvalId": run.approvalID,
                          "enrollmentId": run.enrollmentID, "destinationEmail": run.email, "destinationCourse": run.course,
                          "categories": run.categories])
        case PhoneRunContract.bindPath:
            guard Set(fields.keys) == ["runId", "capability", "handoffId", "payloadSha256", "previousApprovalId"],
                  let previous = fields["previousApprovalId"], let previousScopes = approvalScopes[previous],
                  let runID = fields["runId"], let handoff = fields["handoffId"], let digest = fields["payloadSha256"],
                  var run = runs[runID], !run.preparationMode, run.capability == fields["capability"], !run.revoked, run.approvalCurrent,
                  let expires = run.expiresAt, now() < expires else { return refusal(409, "phone_run_changed") }
            guard Set(run.categories).isSubset(of: Set(previousScopes)),
                  previous == run.approvalID || run.boundHandoffID != nil else { return refusal(409, "phone_run_changed") }
            if let bound = run.boundHandoffID {
                // Written once: only an identical retry succeeds.
                guard bound == handoff, run.boundPayloadSha256 == digest else { return refusal(409, "phone_run_changed") }
            } else {
                run.boundHandoffID = handoff
                run.boundPayloadSha256 = digest
                runs[runID] = run
            }
            var result = claimFields(run, includeStart: false)
            result["previousApprovalId"] = previous
            return (200, result)
        case PhoneRunContract.verifyIntentPath:
            guard Set(fields.keys) == ["runId", "capability", "intentId", "handoffId", "payloadSha256", "expectedApprovalId"],
                  let runID = fields["runId"], let intent = fields["intentId"], let run = runs[runID],
                  run.capability == fields["capability"], !run.revoked, run.approvalCurrent,
                  !run.preparationMode || run.operationID != nil,
                  fields["expectedApprovalId"] == run.approvalID,
                  let expires = run.expiresAt, now() < expires,
                  run.boundHandoffID == fields["handoffId"], run.boundPayloadSha256 == fields["payloadSha256"] else {
                return refusal(409, "phone_run_changed")
            }
            let destination = intents[intent] ?? Intent(email: run.email, course: run.course)
            return (200, ["approvalId": run.approvalID, "enrollmentId": run.enrollmentID, "destinationEmail": destination.email,
                          "destinationCourse": destination.course, "categories": run.categories])
        default:
            return refusal(404, "not_found")
        }
    }
}

/// SYNTHETIC FIXTURE: records every window and app call the director makes. Opening the browser can be answered
/// at once or held, to check late answers after a flow was cancelled or replaced.
@MainActor final class RecordingCompanionSurface: CompanionWindowSurface {
    private(set) var calls: [String] = []
    private(set) var openedURLs: [URL] = []
    var nativeFrontmost = true
    var sheetAttached = false
    var openSucceeds = true
    var holdOpenAnswers = false
    private var heldAnswers: [(@MainActor (Bool) -> Void)] = []

    static let focusCalls: Set<String> = ["deminiaturize", "activateApp", "makeKeyAndOrderFront", "orderFront", "sheet"]
    var focusCallCount: Int { calls.filter(Self.focusCalls.contains).count }
    var miniaturizeCount: Int { calls.filter { $0 == "miniaturize" }.count }

    var isNativeAppFrontmost: Bool { nativeFrontmost }
    var hasAttachedSheet: Bool { sheetAttached }
    func setFloating(_ floating: Bool) {}
    func setKeepsVisibleWhenInactive(_ keep: Bool) {}
    func miniaturize() { calls.append("miniaturize") }
    func deminiaturize() { calls.append("deminiaturize") }
    func activateApp() { calls.append("activateApp") }
    func makeKeyAndOrderFront() { calls.append("makeKeyAndOrderFront") }
    func orderFront() { calls.append("orderFront") }
    func presentCloseConfirmation(title: String, detail: String, completion: @escaping @MainActor (Bool) -> Void) {
        calls.append("sheet")
        completion(false)
    }
    func openInBrowser(_ url: URL, preferredBrowserBundleID: String?, completion: @escaping @MainActor (Bool) -> Void) {
        calls.append("open")
        openedURLs.append(url)
        if holdOpenAnswers { heldAnswers.append(completion) } else { completion(openSucceeds) }
    }
    func answerHeldOpens(_ opened: Bool) {
        let answers = heldAnswers
        heldAnswers = []
        for answer in answers { answer(opened) }
    }
}
