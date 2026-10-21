import Foundation
import Darwin

@MainActor @main enum PhoneSetupChecks {
    typealias Flow = PhoneRunFlowChecks

    static func fixture() -> (CollectorModel, SimulatedPhoneSetupServer, Flow.Wire) {
        let server = SimulatedPhoneSetupServer()
        let wire = Flow.Wire()
        let authority = PhoneRunAuthorityClient(transport: server, schedule: PhoneRunAuthorityClient.immediate,
                                               admissionProtocol: .preparation)
        let model = CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority: authority)
        model.now = { server.now }
        return (model, server, wire)
    }

    static func main() {
        do {
            try strictLinksAndTransport()
            try strictClaimAndReceipt()
            try explicitConsentAndCompleteFlow()
            try exactUncertainRecovery()
            try mismatchesStayDisabled()
            try cancellationAndStaleCallbacks()
            try legacyAndRestartIsolation()
            print("PASS: native setup strict wire, pinned disclosure, explicit consent, exact uncertain recovery, cancellation fences, legacy isolation and synthetic selected Save receipt")
        } catch {
            FileHandle.standardError.write(Data("Native setup check failed: \(error)\n".utf8))
            Darwin.exit(1)
        }
    }

    static func disabled(_ model: CollectorModel, _ wire: Flow.Wire, _ reason: String) throws {
        let before = wire.commands.count
        model.checkBackupEncryptionStatus()
        try Flow.require(!model.startCollection() && !model.canConnect && model.phoneRun == nil &&
                         wire.commands.count == before, reason)
    }

    static func strictLinksAndTransport() throws {
        let server = SimulatedPhoneSetupServer()
        let link = "amplifai-phone://setup?setup=\(server.setupID)&bootstrap=\(server.bootstrap)"
        try Flow.require(PhoneSetupLink(string: link) != nil && PhoneRunLink(string: link) == nil,
                         "Setup is a distinct protocol; the legacy parser cannot claim it")
        for bad in [link + "&course=other", link + "#x", link + "&setup=" + server.setupID,
                    link.replacingOccurrences(of: "?setup", with: "/?setup"),
                    link.replacingOccurrences(of: "setup?", with: "setup:443?"),
                    link.replacingOccurrences(of: "bootstrap=B", with: "bootstrap=%42"),
                    link.replacingOccurrences(of: "//setup", with: "//user@setup"),
                    link.replacingOccurrences(of: "amplifai-phone", with: "https"),
                    link.replacingOccurrences(of: "&bootstrap", with: "+&bootstrap")] {
            try Flow.require(PhoneSetupLink(string: bad) == nil, "Malformed or widened setup links are ignored")
        }
        for (path, limit) in [(PhoneSetupContract.claimPath, 8192), (PhoneSetupContract.approvePath, 16384)] {
            let request = URLSessionPhoneRunTransport.request(path: path, body: Data("{}".utf8))
            try Flow.require(request?.url?.host == "amplifai.satoris.ai" && request?.url?.scheme == "https" &&
                             request?.url?.query == nil && request?.httpShouldHandleCookies == false &&
                             request?.value(forHTTPHeaderField: "Cookie") == nil &&
                             URLSessionPhoneRunTransport.route(path: path)?.maximumResponseBytes == limit,
                             "Setup uses only the agreed fixed authority and route-specific response cap")
            try Flow.require(URLSessionPhoneRunTransport.request(path: path + "?origin=evil", body: Data()) == nil &&
                             URLSessionPhoneRunTransport.request(path: path, body: Data(repeating: 0, count: 1025)) == nil,
                             "Arbitrary routes, destinations and oversized requests are refused")
        }
        try Flow.require(PhoneSetupDisclosure.pinned.computedSha256 == PhoneSetupDisclosure.pinned.sha256,
                         "The approved disclosure hash binds the exact sentence, agreement and policy bytes")
    }

    static func strictClaimAndReceipt() throws {
        let (model, server, _) = fixture()
        try Flow.require(model.admitSetupLink(server.link), "Setup is admitted")
        let rawClaim = try JSONSerialization.jsonObject(with: server.claimBytes!) as! [String: String]
        let claimRequest = PhoneSetupClaimRequest(setupID: server.setupID, bootstrap: server.bootstrap,
            capability: rawClaim["capability"]!, requestID: rawClaim["requestId"]!)!
        let fields = server.claimFields(requestID: claimRequest.requestID)
        let setup = model.nativeSetup!
        let original = String(decoding: SimulatedPhoneSetupServer.data(fields), as: UTF8.self)
        for (key, value) in [("setupId", server.setupID), ("setup\\u0049d", server.setupID),
                             ("ownerId", server.ownerID)] {
            let duplicate = "{\"\(key)\":\"\(value)\"," + original.dropFirst()
            try Flow.require(PhoneSetupContract.claim(Data(duplicate.utf8), request: claimRequest) == nil,
                             "Duplicate or escaped-alias consent keys are refused")
        }
        let duplicateDisclosure = original.replacingOccurrences(of: "\"disclosure\":{", with: "\"disclosure\":{\"version\":\"phone-run-v3\",")
        try Flow.require(PhoneSetupContract.claim(Data(duplicateDisclosure.utf8), request: claimRequest) == nil,
                         "Nested duplicate disclosure keys are refused")
        for key in fields.keys {
            var missing = fields; missing.removeValue(forKey: key)
            try Flow.require(PhoneSetupContract.claim(SimulatedPhoneSetupServer.data(missing), request: claimRequest) == nil,
                             "Every claim binding is required: \(key)")
        }
        var oversized = SimulatedPhoneSetupServer.data(fields)
        oversized.append(Data(repeating: 32, count: 8193))
        try Flow.require(PhoneSetupContract.claim(oversized, request: claimRequest) == nil,
                         "Claim parsing enforces the response cap independently of transport")
        for (key, replacement): (String, Any) in [
            ("categories", ["messages", "contacts"]), ("categories", ["contacts", "contacts"]),
            ("expiresAt", "2026-02-30T00:00:00Z"), ("expiresAt", "2026-10-21T00:00:00+00:00"),
            ("ownerId", "not-an-id"), ("setupId", UUID().uuidString.lowercased()),
            ("requestId", UUID().uuidString.lowercased()), ("destinationEmail", String(repeating: "ü", count: 126) + "@x.y"),
            ("unexpected", true)
        ] {
            var bad = fields; bad[key] = replacement
            try Flow.require(PhoneSetupContract.claim(SimulatedPhoneSetupServer.data(bad), request: claimRequest) == nil,
                             "Invalid claim field refused: \(key)")
        }
        for key in ["version", "sentences", "agreement", "policyVersion", "policySha256", "privacyHref", "sha256"] {
            var bad = fields
            var disclosure = bad["disclosure"] as! [String: Any]
            disclosure[key] = key == "sentences" ? ["Changed disclosure"] : "changed"
            bad["disclosure"] = disclosure
            try Flow.require(PhoneSetupContract.claim(SimulatedPhoneSetupServer.data(bad), request: claimRequest) == nil,
                             "Unpinned disclosure field refused: \(key)")
        }
        model.nativeDisclosureAccepted = true
        model.approveNativeSetup()
        let rawApproval = try JSONSerialization.jsonObject(with: server.approvalBytes!) as! [String: Any]
        let request = PhoneSetupApprovalRequest(setup: setup, claimRequest: claimRequest,
            requestID: rawApproval["requestId"] as! String, runCapability: rawApproval["runCapability"] as! String)!
        try Flow.require(request.body == server.approvalBytes && request.runCapability != claimRequest.capability &&
                         request.runCapability != claimRequest.bootstrap, "Capabilities and exact request bytes stay distinct")
        for key in server.receipt!.keys {
            var bad = server.receipt!; bad.removeValue(forKey: key)
            try Flow.require(PhoneSetupContract.approval(SimulatedPhoneSetupServer.data(bad), request: request) == nil,
                             "Every approval receipt binding is required: \(key)")
        }
        var tooLarge = SimulatedPhoneSetupServer.data(server.receipt!)
        tooLarge.append(Data(repeating: 32, count: 16385))
        try Flow.require(PhoneSetupContract.approval(tooLarge, request: request) == nil,
                         "Approval parsing enforces its own response cap")
    }

    static func explicitConsentAndCompleteFlow() throws {
        let (model, server, wire) = fixture()
        try Flow.require(model.admitSetupLink(server.link) && model.nativeSetupState == .pendingApproval &&
                         !model.canInstallUpdate && !model.beginUpdateInstallation(), "Setup defers updates and waits for consent")
        try disabled(model, wire, "Claim, status and preparation cannot capture before native consent")
        model.approveNativeSetup()
        try Flow.require(server.approvalBytes == nil, "An unchecked agreement cannot approve")
        try Flow.require(!model.admitSetupLink(server.link), "Reopening the same setup cannot create a second claim")
        model.nativeDisclosureAccepted = true
        model.approveNativeSetup()
        model.approveNativeSetup()
        try Flow.require(model.runApproval == .approved && model.nativeSetupState == .none &&
                         model.phoneRun?.claim.disclosureVersion == "phone-run-v3" &&
                         model.phoneRun?.claimRequest == nil && wire.commands.isEmpty &&
                         server.requests.filter { $0.path == PhoneSetupContract.approvePath }.count == 1,
                         "One deliberate agreement creates one confirmed run and does not start capture")
        let surface = RecordingCompanionSurface()
        let director = CompanionWindowDirector()
        server.now = server.now.addingTimeInterval(45 * 60)
        try Flow.require(model.hasStartableRun, "Committed approval outlives pending setup TTL without re-approval")
        try Flow.start(model)
        try Flow.capture(model)
        try Flow.reviewed(model, wire)
        try Flow.paired(model, wire)
        try Flow.pending(model)
        try Flow.require(wire.decisions.last?["approved"] as? Bool == true && model.selectedIDs == [3],
                         "Native consent continues through the exact chosen-contact handoff and destination check")
        for state in ["received", "saved_pending_browser_receipt", "saved"] {
            model.receive(try Flow.handoffEvent(state))
            director.update(model: model, surface: surface)
        }
        model.finished(mode: "connect", code: 0)
        director.update(model: model, surface: surface)
        try Flow.require(model.canContinueToSignedInAccount && surface.openedURLs.isEmpty &&
                         surface.focusCallCount == 0 && surface.miniaturizeCount == 0,
                         "Final receipt enables account return without automatic browser navigation or a new approval")
    }

    static func exactUncertainRecovery() throws {
        let (model, server, wire) = fixture()
        server.faults = [.loseReply, .malformed, .dropRequest]
        model.admitSetupLink(server.link)
        try Flow.require(model.nativeSetupState == .claimUncertain, "Lost claim replies remain unconfirmed")
        try disabled(model, wire, "Uncertain claim cannot start capture")
        model.retrySetupClaim()
        let claims = server.requests.filter { $0.path == PhoneSetupContract.claimPath }
        try Flow.require(model.nativeSetupState == .pendingApproval && claims.count == 4 &&
                         claims.allSatisfy { $0.body == claims.first!.body }, "Claim retry reuses exact request and capability")
        server.faults = [.loseReply, .dropRequest, .malformed]
        model.nativeDisclosureAccepted = true
        model.approveNativeSetup()
        try Flow.require(model.nativeSetupState == .approvalUncertain && !model.canInstallUpdate,
                         "A possibly committed approval stays pending and blocks update replacement")
        model.dismissNativeSetup()
        model.dismissRunApproval()
        try Flow.require(model.nativeSetupState == .approvalUncertain, "Not now cannot discard uncertain approval recovery")
        try disabled(model, wire, "Uncertain approval cannot start capture")
        server.now = server.expiresAt.addingTimeInterval(10)
        model.retryNativeApproval()
        let approvals = server.requests.filter { $0.path == PhoneSetupContract.approvePath }
        try Flow.require(model.runApproval == .approved && approvals.count == 4 &&
                         approvals.allSatisfy { $0.body == approvals.first!.body } && server.runs.runs.count == 1,
                         "After setup expiry the exact approval retry recovers one existing still-current run")
        let (unexpected, unexpectedServer, noCapture) = fixture()
        unexpected.admitSetupLink(unexpectedServer.link)
        unexpectedServer.faults = [.status(204), .status(202), .status(204)]
        unexpected.nativeDisclosureAccepted = true
        unexpected.approveNativeSetup()
        try Flow.require(unexpected.nativeSetupState == .approvalUncertain, "Unexpected success statuses retain recovery")
        try disabled(unexpected, noCapture, "Unexpected success status never grants collection")
        unexpected.retryNativeApproval()
        try Flow.require(unexpected.runApproval == .approved && unexpectedServer.runs.runs.count == 1,
                         "Exact retry recovers after an invalid success status")
    }

    static func mismatchesStayDisabled() throws {
        for field in ["ownerId", "courseId", "enrollmentId", "destinationEmail", "destinationCourse", "setupId", "requestId",
                      "setupSnapshotSha256", "approvalLocation", "disclosureVersion", "disclosureSha256", "categories",
                      "expiresAt", "approvedAt", "preparation.approvalId", "preparation.enrollmentId", "preparation.operation",
                      "preparation.state", "preparation.categories", "preparation.expiresAt", "preparation.disclosureVersion"] {
            let (model, server, wire) = fixture()
            model.admitSetupLink(server.link)
            server.transform = { _, fields in
                var changed = fields
                if field.hasPrefix("preparation.") {
                    let nestedKey = String(field.dropFirst("preparation.".count))
                    var nested = fields["preparation"] as! [String: Any]
                    nested[nestedKey] = nestedKey == "categories" ? ["contacts"] : "changed"
                    changed["preparation"] = nested
                } else { changed[field] = field == "categories" ? ["contacts"] : "changed" }
                return changed
            }
            model.nativeDisclosureAccepted = true
            model.approveNativeSetup()
            try Flow.require(model.nativeSetupState == .approvalUncertain, "Mismatched receipt stays unconfirmed: \(field)")
            try disabled(model, wire, "Mismatched receipt never enables capture: \(field)")
            server.transform = nil
            model.retryNativeApproval()
            try Flow.require(model.runApproval == .approved && server.runs.runs.count == 1,
                             "Correct exact retry recovers the original run after malformed success")
        }
        for condition in ["expiry", "revoked"] {
            let (model, server, wire) = fixture()
            model.admitSetupLink(server.link)
            if condition == "expiry" { server.now = server.expiresAt } else { server.revoke() }
            model.nativeDisclosureAccepted = true
            model.approveNativeSetup()
            try disabled(model, wire, "Expired or revoked setup cannot capture")
            try Flow.require(server.runs.runs.isEmpty, "Expired or revoked setup grants no run")
        }
    }

    static func cancellationAndStaleCallbacks() throws {
        let (model, server, wire) = fixture()
        server.holdReplies = true
        model.admitSetupLink(server.link)
        model.dismissNativeSetup()
        server.releaseHeldReplies()
        try Flow.require(model.nativeSetupState == .none && model.phoneRun == nil && wire.commands.isEmpty,
                         "Late claim after Not now cannot resurrect a setup")

        let (approved, authority, commands) = fixture()
        approved.admitSetupLink(authority.link)
        approved.nativeDisclosureAccepted = true
        authority.holdReplies = true
        approved.approveNativeSetup()
        authority.revoke()
        approved.dismissNativeSetup()
        try Flow.require(approved.nativeSetupState == .approving && !approved.startCollection(),
                         "Approval in flight cannot be discarded or used for capture")
        authority.releaseHeldReplies()
        try Flow.require(commands.commands.isEmpty, "A delayed approval receipt alone never starts collection")
        _ = approved.startCollection()
        try Flow.require(approved.phoneRun?.collectionStarted != true && commands.count("connect") == 0,
                         "Server cancel after approval wins the next admission check and cannot launch capture")
    }

    static func legacyAndRestartIsolation() throws {
        let (wrongLocation, legacyServer, noCapture) = fixture()
        let legacyV2 = legacyServer.runs.createRun()
        legacyServer.runs.replyTransforms[PhoneRunContract.preparationClaimPath] = { fields in
            var changed = fields
            changed["disclosureVersion"] = "phone-run-v3"
            return changed
        }
        wrongLocation.admitRunLink(Flow.link(legacyServer.runs, legacyV2))
        try Flow.require(wrongLocation.runApproval == .refused && wrongLocation.phoneRun == nil && noCapture.commands.isEmpty,
                         "A legacy claim cannot be relabeled as native v3 consent")
        let (model, server, wire) = fixture()
        try Flow.require(model.phoneRun == nil && model.nativeSetup == nil && !model.startCollection() && wire.commands.isEmpty,
                         "Plain launch discovers no account or persisted capability")
        let legacy = server.runs.createRun()
        model.admitRunLink(Flow.link(server.runs, legacy))
        let before = model.phoneRun
        try Flow.require(model.runApproval == .approved && !model.admitSetupLink(server.link) && model.phoneRun == before &&
                         server.requests.isEmpty, "New setup cannot displace an existing approved v2 run")
        model.dismissRunApproval()
        model.admitSetupLink(server.link)
        let restarted = CollectorModel(commandWriter: { _ in }, runAuthority:
            PhoneRunAuthorityClient(transport: server, schedule: PhoneRunAuthorityClient.immediate, admissionProtocol: .preparation))
        restarted.admitSetupLink(server.link)
        try Flow.require(restarted.nativeSetupState == .refused && restarted.phoneRun == nil,
                         "A restarted process cannot replay a consumed bootstrap with a new capability")
    }
}
