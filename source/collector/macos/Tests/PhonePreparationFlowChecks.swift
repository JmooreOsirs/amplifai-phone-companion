import Foundation
import Darwin

/// Synthetic transport/helper checks. No device, browser, network, or account persistence.
@main @MainActor struct PhonePreparationFlowChecks {
    typealias Flow = PhoneRunFlowChecks
    static func main() {
        do {
            try preparationAndExplicitStart()
            try exactStartRecovery()
            try startRefusalsAndFences()
            try stopAndStatusCannotStart()
            try operationBoundHandoff()
            try preparationContinueStaysNative()
            print("macOS preparation admission, operation fences, recovery and handoff checks passed")
        } catch {
            FileHandle.standardError.write(Data("macOS preparation flow check failed: \(error)\n".utf8))
            Darwin.exit(1)
        }
    }

    static func model(_ server: SimulatedPhoneRunServer, _ wire: Flow.Wire) -> CollectorModel {
        CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority:
            PhoneRunAuthorityClient(transport: server, schedule: PhoneRunAuthorityClient.immediate,
                                    admissionProtocol: .preparation))
    }

    static func approved() throws -> (CollectorModel, SimulatedPhoneRunServer, Flow.Wire, String) {
        let server = SimulatedPhoneRunServer()
        let wire = Flow.Wire()
        let model = model(server, wire)
        let run = server.createRun()
        try Flow.require(model.admitRunLink(Flow.link(server, run)) && model.runApproval == .approved,
                         "A preparation-mode app claims a valid bootstrap")
        try Flow.require(server.requests.first?.path == PhoneRunContract.preparationClaimPath &&
                         !server.requests.contains { $0.path == PhoneRunContract.claimPath },
                         "New native must use the additive preparation claim without legacy fallback")
        return (model, server, wire, run.runID)
    }

    static func startBodies(_ server: SimulatedPhoneRunServer) -> [Data] {
        server.requests.filter { $0.path == PhoneRunContract.startPath }.map(\.body)
    }

    static func preparationAndExplicitStart() throws {
        let (model, server, wire, runID) = try approved()
        let originalExpiry = model.phoneRun!.claim.expiresAt
        let later = Date().addingTimeInterval(45 * 60)
        model.now = { later }; server.now = { later }
        model.checkBackupEncryptionStatus()
        model.receive(try Flow.event(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#))
        model.finished(mode: "backup-encryption-status", code: 0)
        model.refreshRunStatus()
        try Flow.require(model.hasStartableRun && model.phoneRun?.collectionStarted == false &&
                         startBodies(server).isEmpty && wire.count("connect") == 0 &&
                         !model.composeRunCollectionApproval(callHistoryPreparation: .confirmedEncryptedBackup),
                         "Preparation beyond thirty minutes and status do not grant collection authority")
        try Flow.start(model)
        let body = try JSONSerialization.jsonObject(with: startBodies(server).first!) as! [String: String]
        try Flow.require(model.phoneRun?.collectionStarted == true && model.phase == .connecting &&
                         startBodies(server).count == 1 && model.phoneRun?.claim.expiresAt == originalExpiry &&
                         model.operationID?.uuidString.lowercased() == body["operationId"] &&
                         server.run(runID)?.operationID == body["operationId"],
                         "One explicit Start admits and launches exactly one operation without extending expiry")
        try Flow.require(!model.startCollection(), "A started run cannot admit a second collection")
        model.disconnect()

        let (expired, expiryServer, _, _) = try approved()
        let deadline = expired.phoneRun!.claim.expiresAt
        expired.now = { deadline }; expiryServer.now = { deadline }
        try Flow.require(!expired.startCollection() && startBodies(expiryServer).isEmpty,
                         "The fixed twelve-hour expiry cannot be renewed by preparation or Start")
    }

    static func exactStartRecovery() throws {
        for committed in [false, true] {
            let (model, server, _, _) = try approved()
            server.faultsByPath[PhoneRunContract.startPath] = committed
                ? [.loseReply, .dropRequest, .dropRequest] : [.dropRequest, .dropRequest, .dropRequest]
            try Flow.start(model)
            if !committed {
                try Flow.require(model.startConfirmation == .uncertain && model.phoneRun?.collectionStarted == false,
                                 "An absent admission remains uncertain and cannot launch")
                model.retryStartConfirmation()
            }
            let bodies = startBodies(server)
            try Flow.require(model.phoneRun?.collectionStarted == true && bodies.count >= 3 &&
                             bodies.allSatisfy { $0 == bodies.first },
                             "Lost Start replies and explicit retry retain the exact operation and request bytes")
            model.disconnect()
        }
    }

    static func startRefusalsAndFences() throws {
        for change in ["approval", "enrollment", "email", "course", "categories", "expiry", "operation", "admittedAt", "revoked", "replacement"] {
            let (model, server, _, runID) = try approved()
            let claim = model.phoneRun!.claim
            if change == "revoked" { server.revoke(runID) }
            else if change == "replacement" { server.changeApproval(runID) }
            else {
                server.replyTransforms[PhoneRunContract.startPath] = { original in
                    var reply = original
                    if change == "approval" { reply["approvalId"] = UUID().uuidString.lowercased() }
                    if change == "enrollment" { reply["enrollmentId"] = UUID().uuidString.lowercased() }
                    if change == "email" { reply["destinationEmail"] = "other@example.invalid" }
                    if change == "course" { reply["destinationCourse"] = "Other course" }
                    if change == "categories" { reply["categories"] = ["contacts"] }
                    if change == "expiry" { reply["expiresAt"] = claim.expiresAt.addingTimeInterval(3600).ISO8601Format() }
                    if change == "operation" || change == "admittedAt" {
                        var operation = original["operation"] as! [String: String]
                        if change == "operation" { operation["operationId"] = UUID().uuidString.lowercased() }
                        else { operation["admittedAt"] = claim.expiresAt.ISO8601Format() }
                        reply["operation"] = operation
                    }
                    return reply
                }
            }
            try Flow.start(model)
            try Flow.require(model.phoneRun?.collectionStarted != true && model.phase != .connecting &&
                             model.localCollectionApproval == nil,
                             "Start refuses \(change) without creating collection authority")
            model.disconnect()
        }
        let server = SimulatedPhoneRunServer(); let wire = Flow.Wire(); let unavailable = model(server, wire)
        server.faultsByPath[PhoneRunContract.preparationClaimPath] = [.status(404)]
        unavailable.admitRunLink(Flow.link(server, server.createRun()))
        try Flow.require(unavailable.phoneRun == nil && server.requests.count == 1 &&
                         server.requests[0].path == PhoneRunContract.preparationClaimPath,
                         "An unavailable additive endpoint fails closed without legacy claim")
    }

    static func stopAndStatusCannotStart() throws {
        let (model, server, _, _) = try approved()
        server.holdPaths = [PhoneRunContract.startPath]
        try Flow.start(model)
        try Flow.require(model.startConfirmation == .confirming && model.phoneRun?.collectionStarted == false,
                         "A held Start response grants no local collection")
        model.disconnect()
        server.releaseHeldReplies()
        try Flow.require(model.phoneRun == nil && model.phase == .idle && !model.hasStartableRun,
                         "Stop retires preparation and fences the late admitted operation")

        let (observing, observerServer, _, runID) = try approved()
        observerServer.forceOperation(runID, operationID: UUID().uuidString.lowercased())
        observing.refreshRunStatus()
        try Flow.require(observing.phoneRun?.collectionStarted != true && observing.phase != .connecting &&
                         startBodies(observerServer).isEmpty && !observing.canRetryApprovalAdoption,
                         "Status cannot adopt or start an operation without pending explicit Start")
        observing.disconnect()

        let (changed, changedServer, _, changedRun) = try approved()
        changedServer.holdPaths = [PhoneRunContract.startPath]
        try Flow.start(changed)
        changedServer.changeApproval(changedRun)
        changed.refreshRunStatus()
        changedServer.releaseHeldReplies()
        try Flow.require(changed.phoneRun?.collectionStarted != true && changed.phase != .connecting &&
                         changed.runStatus == .replacementRequired,
                         "An older Start success cannot override a newer observed authority replacement")
        changed.disconnect()

        for fence in ["replacement", "revoked", "expired"] {
            let (binding, bindServer, bindWire, bindRun) = try approved()
            try Flow.start(binding); try Flow.capture(binding); try Flow.reviewed(binding, bindWire)
            bindServer.holdPaths = [PhoneRunContract.preparationBindPath]
            try Flow.paired(binding, bindWire)
            if fence == "replacement" { bindServer.changeApproval(bindRun) }
            else if fence == "revoked" { bindServer.revoke(bindRun) }
            else {
                let expired = binding.phoneRun!.claim.expiresAt.addingTimeInterval(1)
                binding.now = { expired }; bindServer.now = { expired }
            }
            binding.refreshRunStatus()
            let commands = bindWire.commands.count
            bindServer.releaseHeldReplies()
            try Flow.require(!binding.handoffBound && binding.accountNavigation == nil &&
                             bindWire.commands.count == commands &&
                             (fence != "replacement" || (binding.runStatus == .replacementRequired && binding.selectedIDs == [3])),
                             "A held bind cannot defeat \(fence), reopen bridge authority or discard replacement review")
            binding.disconnect()
        }
    }

    static func preparationContinueStaysNative() throws {
        let (model, server, wire, _) = try approved()
        try Flow.start(model)
        try Flow.capture(model)
        let surface = RecordingCompanionSurface()
        let director = CompanionWindowDirector()
        director.update(model: model, surface: surface)
        model.selectedIDs = [3]
        model.review(continueToAccount: true)
        guard let reviewID = wire.last?["reviewId"] as? String else {
            throw Flow.Failure.check("Selected-contact Continue must review the exact selection")
        }
        let selection = CollectorModel.selectionDigest([3])
        model.receive(try Flow.event(#"{"kind":"review","reviewId":"\#(reviewID)","selected_contacts":1,"selectionSha256":"\#(selection)"}"#))
        try Flow.require(wire.last?["action"] as? String == "pair", "Accepted review binds the prepared browser handoff")
        model.receive(try Flow.pairingEvent())
        director.update(model: model, surface: surface)
        try Flow.require(model.handoffBound && model.accountNavigation == nil && surface.openedURLs.isEmpty &&
                         surface.miniaturizeCount == 0 && !director.handedOff,
                         "Preparation Continue and successful bind leave native visible without automatic browser navigation")
        let recovery = model.accountContinueURL
        try Flow.require(recovery == CollectorModel.accountContinueURL(enrollmentID: model.phoneRun!.enrollmentID),
                         "Explicit pending-save recovery retains the exact reviewed-transfer account route")
        try Flow.pending(model)
        for state in ["received", "saved_pending_browser_receipt", "saved"] {
            model.receive(try Flow.handoffEvent(state))
            director.update(model: model, surface: surface)
        }
        model.finished(mode: "connect", code: 0)
        director.update(model: model, surface: surface)
        try Flow.require(model.canContinueToSignedInAccount && surface.openedURLs.isEmpty &&
                         surface.miniaturizeCount == 0 && surface.focusCallCount == 0,
                         "Preparation receipt and clean completion never open or minimize a window automatically")
        let requests = server.requests.count
        let commands = wire.commands.count
        var explicitReturns = 0
        try Flow.require(model.continueToSignedInAccount { url, browser in
            explicitReturns += 1
            surface.openInBrowser(url, preferredBrowserBundleID: browser) { _ in }
        } && explicitReturns == 1 && surface.openedURLs == [model.completedReportReturnURL!] &&
            server.requests.count == requests && wire.commands.count == commands,
                         "Only the final explicit owner click opens the exact completed report without new authority")
    }

    static func operationBoundHandoff() throws {
        let (model, server, wire, runID) = try approved()
        try Flow.start(model)
        try Flow.capture(model); try Flow.reviewed(model, wire)
        server.faultsByPath[PhoneRunContract.preparationBindPath] = [.loseReply]
        try Flow.paired(model, wire)
        let binds = server.requests.filter { $0.path == PhoneRunContract.preparationBindPath }
        try Flow.require(model.handoffBound && binds.count == 2 && binds[0].body == binds[1].body &&
                         !server.requests.contains { $0.path == PhoneRunContract.bindPath },
                         "The reviewed payload binds to the admitted operation with exact retries and no legacy downgrade")
        try Flow.require(model.statusTitle == "Waiting for your account to save" && model.statusDetail.contains("reopen") &&
                         !model.statusDetail.contains("is saving"),
                         "A bound handoff cannot claim that a possibly closed browser is saving")
        try Flow.pending(model)
        try Flow.require(wire.decisions.last?["approved"] as? Bool == true,
                         "The unchanged verify-intent route accepts the admitted operation's bound handoff")
        server.replace(runID, course: "Replacement", categories: ["contacts"])
        model.refreshRunStatus()
        let count = server.requests.count
        model.retryApprovalAdoption()
        try Flow.require(model.runStatus == .replacementRequired && !model.canRetryApprovalAdoption &&
                         server.requests.count == count && model.selectedIDs == [3],
                         "A new-mode replacement cannot adopt authority or change the reviewed selection")
        model.disconnect()
    }
}
