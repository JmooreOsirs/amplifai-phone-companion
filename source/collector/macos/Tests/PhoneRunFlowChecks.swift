import Foundation

/// One-approval phone run checks: app link, admission, claim, start revalidation, bind, automatic destination
/// decision, the handoff and the no-focus-bounce rule. Synthetic only: an in-process simulated run server behind
/// the production client, a simulated helper wire and a recording window surface. No network, device or UI.
@MainActor
enum PhoneRunFlowChecks {
    enum Failure: Error { case check(String) }
    static func require(_ value: @autoclosure () -> Bool, _ reason: String) throws {
        guard value() else { throw Failure.check(reason) }
    }
    static func event(_ json: String) throws -> AgentEvent { try AgentEvent.decode(Data(json.utf8)) }

    final class Wire {
        var commands: [Data] = []
        var actions: [[String: Any]] { commands.compactMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } }
        func count(_ action: String) -> Int { actions.filter { $0["action"] as? String == action }.count }
        var last: [String: Any]? { actions.last }
        var decisions: [[String: Any]] { actions.filter { $0["action"] as? String == "destination-decision" } }
    }

    nonisolated static let digest = String(repeating: "ab", count: 32)
    nonisolated static let otherDigest = String(repeating: "cd", count: 32)
    nonisolated static let handoff = "00000000-0000-4000-8000-0000000000aa"
    nonisolated static let otherHandoff = "00000000-0000-4000-8000-0000000000dd"
    nonisolated static let intent = "00000000-0000-4000-8000-0000000000bb"
    nonisolated static let email = "synthetic.owner@example.invalid"
    nonisolated static let course = "SYNTHETIC course"

    static func client(_ server: SimulatedPhoneRunServer) -> PhoneRunAuthorityClient {
        PhoneRunAuthorityClient(transport: server, schedule: PhoneRunAuthorityClient.immediate)
    }

    static func link(_ server: SimulatedPhoneRunServer, _ run: (runID: String, bootstrap: String)) -> PendingRunLink {
        PendingRunLink(link: PhoneRunLink(url: server.appLink(run))!, returnBrowserBundleID: "com.example.synthetic-browser")
    }

    /// A model whose run was claimed (approved, not started).
    static func approvedModel(_ server: SimulatedPhoneRunServer, _ wire: Wire,
                              categories: [String] = ["contacts", "calls", "messages"]) throws
        -> (CollectorModel, (runID: String, bootstrap: String)) {
        let model = CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority: client(server))
        let run = server.createRun(email: email, course: course, categories: categories)
        try require(model.admitRunLink(link(server, run)), "An idle app admits a valid run link")
        try require(model.runApproval == .approved && model.phoneRun?.runID == run.runID && model.journeyStep == 1,
                    "A verified claim approves exactly this run and completes the Approve step")
        return (model, run)
    }

    /// Start collection: read-only preflight, current capability status, then the same approved run connects.
    static func start(_ model: CollectorModel, encrypted: Bool = true) throws {
        try require(model.startCollection(), "Start collection runs the iPhone check")
        model.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":\#(encrypted)}"#))
        model.finished(mode: "backup-encryption-status", code: 0)
    }

    static func startedModel() throws -> (CollectorModel, SimulatedPhoneRunServer, Wire, (runID: String, bootstrap: String)) {
        let server = SimulatedPhoneRunServer()
        let wire = Wire()
        let (model, run) = try approvedModel(server, wire)
        try start(model)
        try require(model.phase == .connecting && model.phoneRun?.collectionStarted == true,
                    "Start collection launches exactly this run")
        return (model, server, wire, run)
    }

    static func capture(_ model: CollectorModel) throws {
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic Person","phoneCount":1,"phoneEnds":["0000"]}]}"#))
    }

    static func reviewed(_ model: CollectorModel, _ wire: Wire) throws {
        model.selectedIDs = [3]
        model.review()
        guard let page = wire.last, page["action"] as? String == "review-page", let reviewID = page["reviewId"] as? String else {
            throw Failure.check("Review must send a bound request through the helper wire")
        }
        let sha = CollectorModel.selectionDigest([3])
        model.receive(try event(#"{"kind":"review","reviewId":"\#(reviewID)","selected_contacts":1,"selectionSha256":"\#(sha)"}"#))
        try require(model.phase == .reviewing, "The reviewed selection is ready")
    }

    static func pairingEvent(_ handoffID: String = handoff, digest: String = digest, port: Int = 48751) throws -> AgentEvent {
        try event(#"{"kind":"pairing","pairCode":"1234567890","port":\#(port),"expiresInSeconds":300,"handoffId":"\#(handoffID)","payloadSha256":"\#(digest)"}"#)
    }

    static func paired(_ model: CollectorModel, _ wire: Wire, handoffID: String = handoff, digest: String = digest) throws {
        model.pairBrowser(automaticAccountNavigation: true)
        try require(wire.last?["action"] as? String == "pair", "Continue requests one helper pairing")
        model.receive(try pairingEvent(handoffID, digest: digest))
    }

    static func pending(_ model: CollectorModel, intentID: String = intent, email: String = email,
                        course: String = course, handoffID: String = handoff) throws {
        model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(handoffID)","intentId":"\#(intentID)","email":"\#(email)","course":"\#(course)"}"#))
    }

    static func handoffEvent(_ state: String, _ handoffID: String = handoff) throws -> AgentEvent {
        try event(#"{"kind":"handoff","state":"\#(state)","handoffId":"\#(handoffID)"}"#)
    }

    /// A started run with a bound handoff and an open account navigation request.
    static func boundModel() throws -> (CollectorModel, SimulatedPhoneRunServer, Wire, (runID: String, bootstrap: String)) {
        let (model, server, wire, run) = try startedModel()
        try capture(model)
        try reviewed(model, wire)
        try paired(model, wire)
        try require(model.handoffBound && model.accountNavigation != nil, "A first bind accepts the handoff")
        return (model, server, wire, run)
    }

    static func run() throws {
        try linkParsing()
        try returnBrowserChoice()
        try coldStartBufferAndIdleAdmission()
        try claimSuccessAndRefusals()
        try lostClaimReplyRetriesExactBody()
        try malformedClaimStaysUncertainAndRetries()
        try nothingBeforeVerifiedClaim()
        try legacyApprovalNeverAuthorizes()
        try startRevalidatesTheExactClaim()
        try duplicateAndCandidateLinks()
        try candidateClaimExactRetry()
        try replacementAfterUncertainStart()
        try candidateClaimBlocksOldStart()
        try availableDataRequiresExplicitAction()
        try startWindowIsTenMinutes()
        try longCollectionStillBinds()
        try bindOnceIdenticalRetryDifferentRefused()
        try malformedBindRetriesWithoutRevoke()
        try bindReplacementApproval()
        try verifiedIntentApprovesAutomatically()
        try intentMismatchesDeny()
        try uncertainVerifyLeavesUndecided()
        try verifyRetryBudgetAndRecovery()
        try lateVerifyAfterDiscardIsIgnored()
        try staleDestinationEventsKeepTheDecision()
        try currentStatusRecoveryAndVersionFences()
        try currentStatusPausesAndStopsAdmission()
        try healthyRefreshSharesAuthority()
        try authenticatedPendingSupersession()
        try explicitApprovalAdoption()
        try replacementCannotBroadenOrRewriteSameApproval()
        try longReleasedTransferCompletesWithoutFocus()
        try handoffAfterBindWithoutReceived()
        try finalAcknowledgementRetryKeepsHandoff()
        try noFocusBounceAfterHandoff()
        try lateBrowserOpenAnswersChangeNothing()
        try expiryBeforeAndAfterReleaseSpendsTheRun()
        try approvedToSaveLabel()
        try productionTransportShape()
        try activationCallsOnlyThroughSurface()
    }

    // MARK: App link and admission

    static func linkParsing() throws {
        let run = "0f8fad5b-d9cb-469f-a165-70867728950e"
        let bootstrap = String(repeating: "A", count: 21) + "-_" + String(repeating: "z", count: 20)
        let valid = "amplifai-phone://start?run=\(run)&bootstrap=\(bootstrap)"
        try require(PhoneRunLink(string: valid) == PhoneRunLink(string: "amplifai-phone://start?bootstrap=\(bootstrap)&run=\(run)") &&
                    PhoneRunLink(string: valid)?.runID == run && PhoneRunLink(string: valid)?.bootstrap == bootstrap,
                    "A link with exactly run and bootstrap parses")
        let rejected = [
            valid + "&extra=1", "amplifai-phone://start?run=\(run)", "amplifai-phone://start?bootstrap=\(bootstrap)",
            "amplifai-phone://start?run=\(run)&run=\(run)&bootstrap=\(bootstrap)",
            "amplifai-phone://start?run=\(run)&bootstrap=\(bootstrap.dropLast())",
            "amplifai-phone://start?run=\(run)&bootstrap=\(bootstrap)A",
            "amplifai-phone://start?run=\(run.uppercased())&bootstrap=\(bootstrap)",
            "amplifai-phone://start?run=\(run.dropLast())&bootstrap=\(bootstrap)",
            "amplifai-phone://start?run=\(run)&bootstrap=\(bootstrap.dropLast(2))%2F",
            "amplifai-phone://start?run=\(run)&bootstrap=\(bootstrap.dropLast())+",
            "AMPLIFAI-PHONE://start?run=\(run)&bootstrap=\(bootstrap)", "https://start?run=\(run)&bootstrap=\(bootstrap)",
            "amplifai-phone://open?run=\(run)&bootstrap=\(bootstrap)", "amplifai-phone://start/?run=\(run)&bootstrap=\(bootstrap)",
            "amplifai-phone://user@start?run=\(run)&bootstrap=\(bootstrap)", "amplifai-phone://start:1?run=\(run)&bootstrap=\(bootstrap)",
            valid + "#fragment", "amplifai-phone://start?run=\(run)&bootstrap=", "amplifai-phone://start",
            "amplifai-phone://open?journeyId=00000000-0000-4000-8000-000000000001"
        ]
        for text in rejected {
            try require(PhoneRunLink(string: text) == nil, "Link must be rejected: \(text)")
        }
        try require(PhoneRunContract.isCapability(PhoneRunContract.newCapability() ?? "") &&
                    PhoneRunContract.newCapability() != PhoneRunContract.newCapability(),
                    "A capability is 43 base64url characters from 32 random bytes")
    }

    static func returnBrowserChoice() throws {
        let browser = URL(fileURLWithPath: "/Applications/Synthetic Browser.app")
        try require(ReturnBrowserChoice.accept(senderBundleID: "com.example.browser", senderBundleURL: browser,
                                               ownBundleID: "ai.satoris.amplifai.phone.candidate",
                                               httpsHandlers: [browser]) == "com.example.browser",
                    "The browser that sent the link becomes the return browser")
        try require(ReturnBrowserChoice.accept(senderBundleID: "ai.satoris.amplifai.phone.candidate", senderBundleURL: browser,
                                               ownBundleID: "ai.satoris.amplifai.phone.candidate", httpsHandlers: [browser]) == nil,
                    "This app is never its own return browser")
        try require(ReturnBrowserChoice.accept(senderBundleID: "com.example.terminal", senderBundleURL: URL(fileURLWithPath: "/Applications/Terminal.app"),
                                               ownBundleID: "x", httpsHandlers: [browser]) == nil,
                    "A sender that cannot open https falls back to the default browser")
    }

    static func coldStartBufferAndIdleAdmission() throws {
        let server = SimulatedPhoneRunServer()
        let model = CollectorModel(commandWriter: { _ in }, runAuthority: client(server), awaitsStartupInspection: true)
        let inbox = PhoneRunLinkInbox()
        let first = server.createRun()
        let second = server.createRun()
        try require(!inbox.receive(URL(string: "https://amplifai.satoris.ai/phone")!, returnBrowserBundleID: nil) &&
                    inbox.pending == nil, "Anything but a run link is ignored")
        try require(inbox.receive(server.appLink(first), returnBrowserBundleID: nil) && inbox.pending?.link.runID == first.runID,
                    "A cold-start link waits for the model")
        inbox.receive(server.appLink(second), returnBrowserBundleID: nil)
        try require(inbox.pending?.link.runID == second.runID && server.claimCount == 0,
                    "At most one link is buffered; the newer one replaces it")
        inbox.attach(model)
        try require(inbox.pending != nil && server.claimCount == 0 && model.runApproval == .none,
                    "No claim before the start-up inspection finishes")
        model.inspectResidue()
        model.finished(mode: "inspect", code: 0)
        try require(inbox.pending == nil && server.claimCount == 1 && model.phoneRun?.runID == second.runID,
                    "The buffered link is applied once when the model is ready")
        inbox.flush()
        try require(server.claimCount == 1, "The buffer is one-shot")

        // A busy app ignores a link with no state change.
        let (busy, busyServer, _, busyRun) = try startedModel()
        let before = busy.phoneRun
        try require(!busy.admitRunLink(link(busyServer, busyServer.createRun())) && busy.phoneRun == before &&
                    busy.phoneRun?.runID == busyRun.runID && busyServer.claimCount == 1 && busyServer.statusCount == 1,
                    "A link during a collection is ignored")
        let claiming = SimulatedPhoneRunServer()
        claiming.holdReplies = true
        let pendingModel = CollectorModel(commandWriter: { _ in }, runAuthority: client(claiming))
        try require(pendingModel.admitRunLink(link(claiming, claiming.createRun())) && pendingModel.runApproval == .claiming,
                    "A claim is in flight")
        try require(!pendingModel.admitRunLink(link(claiming, claiming.createRun())) && claiming.claimCount == 1,
                    "A link while a claim is in flight is ignored")
        claiming.releaseHeldReplies()
        try require(pendingModel.runApproval == .approved, "The held claim completes")
    }

    // MARK: Claim

    static func claimSuccessAndRefusals() throws {
        let server = SimulatedPhoneRunServer()
        let wire = Wire()
        let (model, run) = try approvedModel(server, wire, categories: ["contacts", "messages"])
        guard let claimed = model.phoneRun,
              let body = try JSONSerialization.jsonObject(with: server.requests[0].body) as? [String: String] else {
            throw Failure.check("claim")
        }
        try require(server.requests[0].path == PhoneRunContract.claimPath && Set(body.keys) == ["runId", "bootstrap", "capability"] &&
                    body["runId"] == run.runID && body["bootstrap"] == run.bootstrap &&
                    body["capability"] == claimed.capability && claimed.capability != run.bootstrap,
                    "The claim sends only the run, the bootstrap and the app's own capability")
        try require(claimed.email == email && claimed.course == course && claimed.categories == ["contacts", "messages"] &&
                    claimed.claim.disclosureVersion == "phone-run-v2" && wire.commands.isEmpty && !model.helperRunning,
                    "The verified account, course and categories are shown; nothing is collected yet")

        func refused(_ prepare: (SimulatedPhoneRunServer, (runID: String, bootstrap: String)) -> PendingRunLink, _ reason: String) throws {
            let server = SimulatedPhoneRunServer()
            let model = CollectorModel(commandWriter: { _ in }, runAuthority: client(server))
            let run = server.createRun()
            model.admitRunLink(prepare(server, run))
            try require(model.runApproval == .refused && model.phoneRun == nil &&
                        model.runApprovalMessage.contains("Approve again in your AMPLIFai account") && !model.startCollection(),
                        reason)
        }
        try refused({ server, run in
            PendingRunLink(link: PhoneRunLink(string: "amplifai-phone://start?run=\(run.runID)&bootstrap=\(String(repeating: "F", count: 43))")!,
                           returnBrowserBundleID: nil)
        }, "A forged bootstrap is refused")
        try refused({ server, run in
            server.revoke(run.runID)
            return link(server, run)
        }, "A revoked run is refused")
        try refused({ server, run in
            server.changeApproval(run.runID)
            return link(server, run)
        }, "A changed approval is refused")
        try refused({ server, run in
            server.now = { Date().addingTimeInterval(11 * 60) }
            return link(server, run)
        }, "A stale bootstrap is refused")
        try refused({ _, _ in
            PendingRunLink(link: PhoneRunLink(string: "amplifai-phone://start?run=0f8fad5b-d9cb-469f-a165-70867728950e&bootstrap=\(String(repeating: "Q", count: 43))")!,
                           returnBrowserBundleID: nil)
        }, "An unknown run (404) is refused")
        // Replay: the same link from another app instance has a different capability.
        let replayModel = CollectorModel(commandWriter: { _ in }, runAuthority: client(server))
        replayModel.admitRunLink(link(server, run))
        try require(replayModel.runApproval == .refused && replayModel.phoneRun == nil,
                    "A replayed bootstrap with another capability is refused")
    }

    static func lostClaimReplyRetriesExactBody() throws {
        let server = SimulatedPhoneRunServer()
        server.faults = [.loseReply, .status(503)]
        let model = CollectorModel(commandWriter: { _ in }, runAuthority: client(server))
        model.admitRunLink(link(server, server.createRun()))
        try require(model.runApproval == .approved && server.claimCount == 3 &&
                    server.requests[0].body == server.requests[1].body && server.requests[1].body == server.requests[2].body,
                    "A lost reply and a 5xx retry the identical claim bytes, and the server reads the same claim back")

        let offline = SimulatedPhoneRunServer()
        offline.faults = [.dropRequest, .dropRequest, .dropRequest]
        let waiting = CollectorModel(commandWriter: { _ in }, runAuthority: client(offline))
        waiting.admitRunLink(link(offline, offline.createRun()))
        try require(waiting.runApproval == .uncertain && waiting.phoneRun == nil && !waiting.startCollection(),
                    "An unconfirmed claim never starts a collection")
        waiting.retryRunClaim()
        try require(waiting.runApproval == .approved && Set(offline.requests.map(\.body)).count == 1,
                    "Try again resends the identical bytes")
    }

    static func malformedClaimStaysUncertainAndRetries() throws {
        let server = SimulatedPhoneRunServer()
        server.faults = [.malformed, .malformed, .malformed]
        let wire = Wire()
        let model = CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority: client(server))
        model.admitRunLink(link(server, server.createRun()))
        try require(model.runApproval == .uncertain && model.phoneRun == nil && server.claimCount == 3,
                    "A malformed success after retries is uncertain, never success or refusal")
        model.retryRunClaim()
        try require(model.runApproval == .approved && Set(server.requests.map(\.body)).count == 1,
                    "Retry with the identical bytes reads the committed claim back")
        try start(model)
        try require(model.phase == .connecting && model.phoneRun?.collectionStarted == true,
                    "After the recovered claim, collection can start")
    }

    static func nothingBeforeVerifiedClaim() throws {
        let wire = Wire()
        let model = CollectorModel(commandWriter: { wire.commands.append($0) })
        try require(!model.admitRunLink(PendingRunLink(link: PhoneRunLink(string: "amplifai-phone://start?run=0f8fad5b-d9cb-469f-a165-70867728950e&bootstrap=\(String(repeating: "A", count: 43))")!,
                                                       returnBrowserBundleID: nil)),
                    "Without a run authority no link is claimed")
        try require(!model.startCollection() && !model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage),
                    "No collection approval exists without a verified run")
        model.connect()
        try require(model.operationID == nil && model.phase == .idle, "Connect does nothing without a verified run")
        try capture(model)
        try reviewed(model, wire)
        let before = wire.commands.count
        model.pairBrowser(automaticAccountNavigation: true)
        try require(wire.commands.count == before && model.accountNavigation == nil,
                    "No pairing is requested without this run's started collection")
        model.receive(try pairingEvent())
        try require(model.handoffID.isEmpty && model.accountNavigation == nil, "An unrequested pairing is never bound or opened")
    }

    static func legacyApprovalNeverAuthorizes() throws {
        try require(CollectorModel.collectionDisclosureVersion == "native-run-v1",
                    "The native disclosure is versioned for the run approval")
        let server = SimulatedPhoneRunServer()
        let (model, _) = try approvedModel(server, Wire())
        try require(!model.canConnect && model.localCollectionApproval == nil,
                    "A verified run alone does not compose an approval until Start")
    }

    // MARK: Start revalidation

    static func startRevalidatesTheExactClaim() throws {
        // Revoked between claim and Start: no helper launch.
        let server = SimulatedPhoneRunServer()
        let wire = Wire()
        let (model, run) = try approvedModel(server, wire)
        server.revoke(run.runID)
        try require(model.startCollection(), "The iPhone check runs")
        model.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#))
        model.finished(mode: "backup-encryption-status", code: 0)
        try require(server.claimCount == 1 && server.statusCount == 1 &&
                    server.requests[1].path == PhoneRunContract.statusPath,
                    "Start revalidates the original capability through current status")
        try require(model.phase == .idle && model.operationID == nil && model.phoneRun == nil &&
                    model.runApproval == .refused && model.runApprovalMessage.contains("Approve again"),
                    "A revoked run never launches the helper and asks to approve again")

        // Start after the 10-minute replay window: approve again.
        let late = SimulatedPhoneRunServer()
        var clock = Date()
        late.now = { clock }
        let lateModel = CollectorModel(commandWriter: { _ in }, runAuthority: client(late))
        lateModel.now = { clock }
        lateModel.admitRunLink(link(late, late.createRun()))
        clock = clock.addingTimeInterval(11 * 60)
        try require(!lateModel.startCollection() && lateModel.operationID == nil && lateModel.runApproval == .refused,
                    "Start more than ten minutes after the claim needs a new approval")
        // The check itself outlasts the window: the replay is refused by the server and nothing launches.
        let slow = SimulatedPhoneRunServer()
        var slowClock = Date()
        slow.now = { slowClock }
        let slowModel = CollectorModel(commandWriter: { _ in }, runAuthority: client(slow))
        slowModel.now = { slowClock }
        slowModel.admitRunLink(link(slow, slow.createRun()))
        slowClock = slowClock.addingTimeInterval(9 * 60)
        try require(slowModel.startCollection(), "Start inside the window runs the check")
        slowClock = slowClock.addingTimeInterval(2 * 60)
        slowModel.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#))
        slowModel.finished(mode: "backup-encryption-status", code: 0)
        try require(slowModel.operationID == nil && slowModel.runApproval == .refused && slow.claimCount == 1,
                    "A check that ends after the window launches nothing and asks to approve again")

        // A changed claim (course changed) does not start.
        let changed = SimulatedPhoneRunServer()
        let (changedModel, changedRun) = try approvedModel(changed, Wire())
        changed.replace(changedRun.runID, course: "Another course", categories: ["contacts"])
        try start(changedModel)
        try require(changedModel.operationID == nil && changedModel.runStatus == .replacementRequired && changedModel.phoneRun != nil,
                    "A changed approval pauses Start without silently adopting it")

        // Uncertain: not confirmed, Retry resends identical bytes.
        let flaky = SimulatedPhoneRunServer()
        let (flakyModel, _) = try approvedModel(flaky, Wire())
        flaky.faults = [.malformed, .loseReply, .dropRequest]
        try start(flakyModel)
        try require(flakyModel.startConfirmation == .uncertain && flakyModel.operationID == nil &&
                    flakyModel.phoneRun?.collectionStarted == false, "An unconfirmed revalidation does not start")
        flakyModel.retryStartConfirmation()
        try require(flakyModel.phase == .connecting && flakyModel.phoneRun?.collectionStarted == true &&
                    Set(flaky.requests.filter { $0.path == PhoneRunContract.statusPath }.map(\.body)).count == 1,
                    "Retry resends the identical status capability and starts")

        // A stale answer after Not now starts nothing.
        let held = SimulatedPhoneRunServer()
        let (heldModel, _) = try approvedModel(held, Wire())
        held.holdReplies = true
        try start(heldModel)
        try require(heldModel.startConfirmation == .confirming, "The revalidation is in flight")
        heldModel.dismissRunApproval()
        held.releaseHeldReplies()
        try require(heldModel.operationID == nil && heldModel.phase == .idle && heldModel.phoneRun == nil,
                    "A late revalidation answer after Not now starts nothing")
        // A stale answer after a cancelled check (owner cancel) starts nothing either.
        let cancelled = SimulatedPhoneRunServer()
        let (cancelledModel, _) = try approvedModel(cancelled, Wire())
        cancelled.holdReplies = true
        try start(cancelledModel)
        cancelledModel.disconnect()
        cancelled.releaseHeldReplies()
        try require(cancelledModel.operationID == nil && cancelledModel.phoneRun?.collectionStarted == false,
                    "A late revalidation answer after cancel starts nothing")
    }

    static func duplicateAndCandidateLinks() throws {
        let server = SimulatedPhoneRunServer()
        let (model, run) = try approvedModel(server, Wire())
        let grant = model.phoneRun
        try require(!model.admitRunLink(link(server, run)) && model.phoneRun == grant && server.claimCount == 1,
                    "The same link again is a duplicate: grant and capability unchanged, no claim")
        try start(model)
        try require(model.phase == .connecting, "Start still works after a duplicate link")

        let refusedServer = SimulatedPhoneRunServer()
        let (kept, _) = try approvedModel(refusedServer, Wire())
        let keptGrant = kept.phoneRun
        let other = refusedServer.createRun()
        refusedServer.revoke(other.runID)
        try require(kept.admitRunLink(link(refusedServer, other)), "A different link is claimed as a candidate")
        try require(kept.phoneRun == keptGrant && kept.runApproval == .approved && !kept.candidateNotice.isEmpty,
                    "A refused candidate leaves the original grant intact")

        let replacing = SimulatedPhoneRunServer()
        let (replaced, _) = try approvedModel(replacing, Wire())
        let newer = replacing.createRun(email: email, course: "Newer course")
        replaced.admitRunLink(link(replacing, newer))
        try require(replaced.phoneRun?.runID == newer.runID && replaced.phoneRun?.course == "Newer course" &&
                    replaced.runApproval == .approved, "A different link whose claim succeeds replaces the grant")
    }

    static func candidateClaimExactRetry() throws {
        for fault in [SimulatedPhoneRunServer.Fault.malformed, .loseReply] {
            let server = SimulatedPhoneRunServer()
            let (model, _) = try approvedModel(server, Wire())
            let grant = model.phoneRun
            let newer = server.createRun(email: email, course: "Newer course")
            server.faults = [fault, fault, fault]
            model.admitRunLink(link(server, newer))
            try require(model.candidateUncertain && model.phoneRun == grant && model.hasStartableRun && !model.startCollection(),
                        "An unconfirmed candidate retains both grants but blocks Start until settlement (\(fault))")
            let candidateBodies = server.requests.suffix(3).map(\.body)
            model.retryCandidateClaim()
            try require(Set(candidateBodies + [server.requests.last!.body]).count == 1,
                        "The candidate retry sends the identical bytes (\(fault))")
            try require(model.phoneRun?.runID == newer.runID && !model.candidateUncertain,
                        "A successful candidate retry replaces the grant (\(fault))")
        }
        let server = SimulatedPhoneRunServer()
        let (model, _) = try approvedModel(server, Wire())
        let grant = model.phoneRun
        let refused = server.createRun()
        server.faults = [.status(400)]
        model.admitRunLink(link(server, refused))
        try require(!model.candidateUncertain && model.phoneRun == grant && model.admitsRunLink,
                    "A refused candidate is discarded and the grant is intact")
    }

    static func replacementAfterUncertainStart() throws {
        for expired in [false, true] {
            let server = SimulatedPhoneRunServer()
            var clock = Date()
            server.now = { clock }
            let wire = Wire()
            let (model, _) = try approvedModel(server, wire)
            model.now = { clock }
            server.faults = [.dropRequest, .dropRequest, .dropRequest]
            try start(model)
            try require(model.startConfirmation == .uncertain && model.operationID == nil,
                        "Start uncertainty never launches the helper")
            if expired { clock = clock.addingTimeInterval(11 * 60) }
            let newer = server.createRun(email: email, course: "Replacement course")
            try require(model.admitRunLink(link(server, newer)), "An idle uncertain Start admits a fresh approval")
            try require(model.phoneRun?.runID == newer.runID && model.startConfirmation == .none &&
                        model.hasStartableRun && model.operationID == nil,
                        "A verified replacement settles atomically before or after the old Start deadline")
            try start(model)
            try require(model.phoneRun?.runID == newer.runID && model.phoneRun?.collectionStarted == true,
                        "Only the replacement can start")
        }
    }

    static func candidateClaimBlocksOldStart() throws {
        let server = SimulatedPhoneRunServer()
        let (model, old) = try approvedModel(server, Wire())
        let newer = server.createRun(email: email, course: "Replacement course")
        server.holdReplies = true
        model.admitRunLink(link(server, newer))
        try require(model.phoneRun?.runID == old.runID && !model.startCollection() && model.operationID == nil,
                    "An outstanding replacement claim cannot race an old Start")
        server.releaseHeldReplies()
        try require(model.phoneRun?.runID == newer.runID && model.hasStartableRun,
                    "The consumed candidate is retained through settlement")
    }

    static func availableDataRequiresExplicitAction() throws {
        let server = SimulatedPhoneRunServer()
        let wire = Wire()
        let (model, _) = try approvedModel(server, wire)
        try start(model, encrypted: false)
        try require(model.backupEncryptionStatus == .unencrypted && model.phoneRun?.collectionStarted == false &&
                    model.operationID == nil && server.claimCount == 1,
                    "Unencrypted preflight stops before collection for Finder guidance")
        try require(model.startCollectionWithAvailableData() && model.phoneRun?.collectionStarted == true &&
                    server.claimCount == 1 && server.statusCount == 1,
                    "Explicit available-data action revalidates and starts this same run")

        let (checkOnly, _) = try approvedModel(SimulatedPhoneRunServer(), Wire())
        checkOnly.checkBackupEncryptionStatus()
        checkOnly.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#))
        checkOnly.finished(mode: "backup-encryption-status", code: 0)
        try require(checkOnly.backupEncryptionStatus == .encrypted && checkOnly.phoneRun?.collectionStarted == false &&
                    checkOnly.operationID == nil, "Finder recheck remains read-only even after encryption is enabled")
    }

    static func startWindowIsTenMinutes() throws {
        let server = SimulatedPhoneRunServer()
        var clock = Date()
        server.now = { clock }
        let wire = Wire()
        let model = CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority: client(server))
        model.now = { clock }
        model.admitRunLink(link(server, server.createRun()))
        clock = clock.addingTimeInterval(10 * 60 + 1)
        try require(!model.hasStartableRun && !model.startCollection() && model.operationID == nil &&
                    model.runApproval == .refused && model.runApprovalMessage == CollectorModel.runRefusedMessage,
                    "Ten minutes and one second after the claim, Start is not offered and approve-again is shown")

        let late = SimulatedPhoneRunServer()
        var lateClock = Date()
        late.now = { lateClock }
        let lateWire = Wire()
        let lateModel = CollectorModel(commandWriter: { lateWire.commands.append($0) }, runAuthority: client(late))
        lateModel.now = { lateClock }
        lateModel.admitRunLink(link(late, late.createRun()))
        lateClock = lateClock.addingTimeInterval(9 * 60)
        try start(lateModel)
        try require(lateModel.phase == .connecting, "Start at nine minutes runs")
        lateClock = lateClock.addingTimeInterval(35 * 60)
        try capture(lateModel)
        try reviewed(lateModel, lateWire)
        try paired(lateModel, lateWire)
        try require(lateModel.handoffBound && lateModel.browserHandoffReady,
                    "After a 35-minute collection started at nine minutes, the bind succeeds")
    }

    // MARK: Bind

    static func longCollectionStillBinds() throws {
        let server = SimulatedPhoneRunServer()
        var clock = Date()
        server.now = { clock }
        let wire = Wire()
        let model = CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority: client(server))
        model.now = { clock }
        model.admitRunLink(link(server, server.createRun()))
        try start(model)
        // A 35-minute collection with progress, far past the helper's five-minute pairing window.
        for minute in stride(from: 5, through: 35, by: 5) {
            clock = clock.addingTimeInterval(5 * 60)
            model.receive(try event(#"{"kind":"progress","value":\#(min(99, minute * 2))}"#))
        }
        try capture(model)
        try reviewed(model, wire)
        try paired(model, wire)
        try require(model.handoffBound && model.browserHandoffReady && model.runApproval == .approved,
                    "Continue 35 minutes after the claim binds with no new approval (the run lasts 12 hours)")

        let longer = SimulatedPhoneRunServer()
        var later = Date()
        longer.now = { later }
        let longerWire = Wire()
        let longerModel = CollectorModel(commandWriter: { longerWire.commands.append($0) }, runAuthority: client(longer))
        longerModel.now = { later }
        longerModel.admitRunLink(link(longer, longer.createRun()))
        try start(longerModel)
        later = later.addingTimeInterval(11 * 60 * 60)
        try capture(longerModel)
        try reviewed(longerModel, longerWire)
        try paired(longerModel, longerWire)
        try require(longerModel.handoffBound, "An 11-hour collection still binds")

        let expired = SimulatedPhoneRunServer()
        var expiredClock = Date()
        expired.now = { expiredClock }
        let expiredWire = Wire()
        let expiredModel = CollectorModel(commandWriter: { expiredWire.commands.append($0) }, runAuthority: client(expired))
        expiredModel.now = { expiredClock }
        expiredModel.admitRunLink(link(expired, expired.createRun()))
        try start(expiredModel)
        expiredClock = expiredClock.addingTimeInterval(12 * 60 * 60 + 60)
        try capture(expiredModel)
        try reviewed(expiredModel, expiredWire)
        try paired(expiredModel, expiredWire)
        try require(!expiredModel.handoffBound && expiredModel.runStatus == .expired && expiredModel.phoneRun == nil &&
                    expiredWire.last?["action"] as? String == "disconnect" && expiredModel.accountNavigation == nil,
                    "After 12 hours status stops the local helper before bind; nothing opens")
    }

    static func bindOnceIdenticalRetryDifferentRefused() throws {
        let (model, server, wire, run) = try boundModel()
        guard let bind = server.requests.last(where: { $0.path == PhoneRunContract.bindPath }),
              let body = try JSONSerialization.jsonObject(with: bind.body) as? [String: String] else { throw Failure.check("bind") }
        try require(Set(body.keys) == ["runId", "capability", "handoffId", "payloadSha256", "previousApprovalId"] && body["runId"] == run.runID &&
                    body["handoffId"] == handoff && body["payloadSha256"] == digest && body["capability"] == model.phoneRun?.capability && body["previousApprovalId"] == model.phoneRun?.claim.approvalID,
                    "The bind names exactly this run, its capability, the helper handoff and its digest")
        try require(model.phoneRun?.boundHandoffID == handoff && model.collectionApprovalRunID.uuidString.lowercased() != run.runID &&
                    run.runID != handoff, "The server run, the local approval and the handoff stay distinct")
        let before = wire.commands.count
        model.pairBrowser(automaticAccountNavigation: true)
        try require(wire.commands.count == before, "A bound run never re-pairs")

        // Identical retry succeeds; a different handoff is refused.
        let lost = SimulatedPhoneRunServer()
        let lostWire = Wire()
        let (lostModel, _) = try approvedModel(lost, lostWire)
        try start(lostModel)
        try capture(lostModel)
        try reviewed(lostModel, lostWire)
        lost.faultsByPath[PhoneRunContract.bindPath] = [.loseReply]
        try paired(lostModel, lostWire)
        try require(lostModel.handoffBound && lost.bindCount == 2 &&
                    Set(lost.requests.filter { $0.path == PhoneRunContract.bindPath }.map(\.body)).count == 1,
                    "A lost bind reply retries the identical body and succeeds")
        let different = SimulatedPhoneRunServer()
        let differentWire = Wire()
        let (differentModel, differentRun) = try approvedModel(different, differentWire)
        try start(differentModel)
        try capture(differentModel)
        try reviewed(differentModel, differentWire)
        different.forceBinding(differentRun.runID, handoffID: otherHandoff, digest: otherDigest)
        try paired(differentModel, differentWire)
        try require(differentModel.runBinding == .refused && differentModel.handoffID.isEmpty &&
                    differentWire.last?["action"] as? String == "revoke" && differentModel.accountNavigation == nil &&
                    differentModel.errorMessage == CollectorModel.bindRefusedMessage && differentModel.selectedIDs == [3],
                    "A different handoff for an already-bound run is refused: revoked, nothing opens, review kept")
        let afterRefusal = differentWire.commands.count
        differentModel.pairBrowser(automaticAccountNavigation: true)
        try require(differentWire.commands.count == afterRefusal && !differentModel.canContinueToAccount,
                    "A refused run never re-pairs")
    }

    static func malformedBindRetriesWithoutRevoke() throws {
        let (model, server, wire, _) = try startedModel()
        try capture(model)
        try reviewed(model, wire)
        server.faultsByPath[PhoneRunContract.bindPath] = [.malformed, .malformed, .malformed]
        try paired(model, wire)
        try require(model.runBinding == .uncertain && model.handoffID == handoff && wire.count("revoke") == 0 &&
                    wire.decisions.isEmpty && model.accountNavigation == nil && model.errorMessage == CollectorModel.bindUncertainMessage,
                    "A malformed bind success is uncertain: the pairing is kept, nothing revoked, denied or opened")
        model.retryRunBinding()
        try require(model.handoffBound && model.accountNavigation != nil && model.errorMessage.isEmpty &&
                    Set(server.requests.filter { $0.path == PhoneRunContract.bindPath }.map(\.body)).count == 1,
                    "Retry resends the identical bind body and the handoff proceeds")
    }

    static func bindReplacementApproval() throws {
        let (model, server, wire, run) = try startedModel()
        try capture(model)
        try reviewed(model, wire)
        server.replace(run.runID, course: "Replacement course", categories: ["contacts", "calls"])
        try paired(model, wire)
        try require(!model.handoffBound && model.phoneRun != nil && model.runStatus == .replacementRequired && model.selectedIDs == [3],
                    "A replacement before the first immutable bind retains review for account recovery")
        let (other, otherServer, otherWire, _) = try startedModel()
        try capture(other)
        try reviewed(other, otherWire)
        var wrongBinding = bindingReply(other)
        wrongBinding["enrollmentId"] = "00000000-0000-4000-8000-0000000000ee"
        otherServer.replyOverrides[PhoneRunContract.bindPath] = wrongBinding
        try paired(other, otherWire)
        try require(other.runBinding == .refused && other.accountNavigation == nil,
                    "A bind reply for another enrollment is refused")
    }

    // MARK: Destination decision

    static func verifiedIntentApprovesAutomatically() throws {
        let (model, server, wire, _) = try boundModel()
        let surface = RecordingCompanionSurface()
        let director = CompanionWindowDirector()
        director.update(model: model, surface: surface)
        try pending(model)
        director.update(model: model, surface: surface)
        guard let decision = wire.decisions.last else { throw Failure.check("No automatic decision") }
        try require(decision["approved"] as? Bool == true && decision["intentId"] as? String == intent &&
                    decision["handoffId"] as? String == handoff && server.verifyCount == 1,
                    "An exact verified match approves automatically")
        try pending(model)
        try pending(model)
        try require(wire.decisions.count == 1 && server.verifyCount == 1, "Polling never verifies or decides twice")
        model.receive(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        try require(model.destinationApproved && surface.focusCallCount == 0 && !surface.calls.contains("sheet"),
                    "No native destination sheet or activation")
        // Lost decision answer reconciled only by a matching receipt.
        let (lost, _, _, _) = try boundModel()
        try pending(lost)
        lost.receive(try event(#"{"kind":"error","code":"handoff_unconfirmed"}"#))
        lost.receive(try handoffEvent("received", otherHandoff))
        try require(!lost.destinationApproved, "A receipt for another handoff changes nothing")
        lost.receive(try handoffEvent("received"))
        try require(lost.destinationApproved && lost.browserReceived && !lost.handoffAcknowledged,
                    "A matching receipt reconciles this app's verified approval, never counting a save")
    }

    static func intentMismatchesDeny() throws {
        func denied(_ prepare: (CollectorModel, SimulatedPhoneRunServer, (runID: String, bootstrap: String)) throws -> Void,
                    _ reason: String) throws {
            let (model, server, wire, run) = try boundModel()
            try prepare(model, server, run)
            try require(wire.decisions.count == 1 && wire.decisions[0]["approved"] as? Bool == false && !model.destinationApproved,
                        reason)
        }
        try denied({ model, server, _ in
            server.intents[intent] = .init(email: "someone.else@example.invalid", course: course)
            try pending(model)
        }, "Another signed-in account is denied")
        try denied({ model, _, _ in try pending(model, email: "someone.else@example.invalid") },
                   "A pending email that differs from the verified one is denied")
        try denied({ model, _, _ in try pending(model, course: "Other course") },
                   "A pending course that differs from the verified one is denied")
        try denied({ model, server, run in
            server.forceBinding(run.runID, handoffID: otherHandoff, digest: digest)
            try pending(model)
        }, "A server binding for another handoff is denied")
        try denied({ model, server, run in
            server.forceBinding(run.runID, handoffID: handoff, digest: otherDigest)
            try pending(model)
        }, "A server binding for another digest is denied")
        try denied({ model, server, _ in
            server.replyOverrides[PhoneRunContract.verifyIntentPath] = ["approvalId": model.phoneRun!.claim.approvalID, "enrollmentId": "00000000-0000-4000-8000-0000000000ee",
                "destinationEmail": email, "destinationCourse": course, "categories": ["contacts"]]
            try pending(model)
        }, "Another enrollment is denied")
        let (revoked, revokedServer, revokedWire, revokedRun) = try boundModel()
        revokedServer.revoke(revokedRun.runID)
        try pending(revoked)
        try require(revoked.phoneRun == nil && revoked.runStatus == .revoked && revokedWire.decisions.isEmpty,
                    "Revoked status stops the run before a destination can be admitted")
    }

    static func uncertainVerifyLeavesUndecided() throws {
        for faults in [[SimulatedPhoneRunServer.Fault.dropRequest, .dropRequest, .dropRequest],
                       [.status(500), .loseReply, .status(502)], [.malformed, .malformed, .malformed]] {
            let (model, server, wire, _) = try boundModel()
            server.faultsByPath[PhoneRunContract.verifyIntentPath] = faults
            try pending(model)
            try require(wire.decisions.isEmpty && model.destinationVerificationUncertain && !model.destinationApproved &&
                        server.verifyCount == 3, "An uncertain verify neither approves nor denies")
            try pending(model)
            try require(wire.decisions.isEmpty && server.verifyCount == 3, "Immediate duplicate polls respect the retry backoff")
        }
    }

    static func verifyRetryBudgetAndRecovery() throws {
        for fault in [SimulatedPhoneRunServer.Fault.dropRequest, .loseReply, .malformed] {
            let (model, server, wire, _) = try boundModel()
            var clock = Date()
            model.now = { clock }
            server.now = { clock }
            server.faultsByPath[PhoneRunContract.verifyIntentPath] = Array(repeating: fault, count: 9)
            try pending(model)
            for _ in 0..<2 {
                clock = clock.addingTimeInterval(15)
                try pending(model)
            }
            try require(server.verifyCount == 9 && wire.decisions.isEmpty && model.canRetryDestinationVerification,
                        "Three bounded rounds remain undecided after repeated uncertainty")
            clock = clock.addingTimeInterval(15)
            try pending(model)
            try require(server.verifyCount == 9, "Repeated polling cannot exceed the automatic budget")
            model.retryDestinationVerification()
            let bodies = server.requests.filter { $0.path == PhoneRunContract.verifyIntentPath }.map(\.body)
            try require(server.verifyCount == 10 && Set(bodies).count == 1 &&
                        wire.decisions.count == 1 && wire.decisions[0]["approved"] as? Bool == true,
                        "Recovery replays the exact intent, handoff, digest and capability once")
        }
        let (expired, server, wire, _) = try boundModel()
        var clock = Date()
        expired.now = { clock }
        server.faultsByPath[PhoneRunContract.verifyIntentPath] = [.dropRequest, .dropRequest, .dropRequest]
        try pending(expired)
        clock = clock.addingTimeInterval(301)
        expired.retryDestinationVerification()
        try pending(expired)
        try require(server.verifyCount == 3 && wire.decisions.isEmpty && !expired.canRetryDestinationVerification,
                    "A local retry never extends intent lifetime or authorizes an expired intent")
    }

    static func lateVerifyAfterDiscardIsIgnored() throws {
        let (model, server, wire, _) = try boundModel()
        server.holdReplies = true
        try pending(model)
        model.disconnect()
        server.releaseHeldReplies()
        try require(wire.decisions.isEmpty && model.phoneRun == nil && model.handoffID.isEmpty,
                    "A late successful verification after owner discard cannot release the old handoff")
    }

    static func finalAcknowledgementRetryKeepsHandoff() throws {
        let (model, server, wire, _) = try boundModel()
        try pending(model)
        let bound = model.phoneRun
        for attempt in 1...3 {
            model.receive(try handoffEvent("saved"))
            try require(model.phase == .completing && wire.count("finish") == attempt,
                        "The verified same-handoff receipt retries native finish within budget")
            model.receive(try event(#"{"kind":"error","code":"handoff_unconfirmed"}"#))
            try require(model.phase == .reviewing && !model.handoffAcknowledged && model.phoneRun == bound,
                        "An uncertain finish retains the exact run and handoff for reconciliation")
        }
        model.receive(try handoffEvent("saved"))
        try require(wire.count("finish") == 3 && model.canRetryAccountCompletion,
                    "Repeated receipts cannot loop finish indefinitely")
        model.retryAccountCompletion()
        try require(wire.last?["action"] as? String == "handoff-status" &&
                    wire.last?["handoffId"] as? String == handoff,
                    "Explicit retry checks only this transfer's existing receipt")
        model.receive(try handoffEvent("saved", otherHandoff))
        try require(wire.count("finish") == 3, "A stale other-handoff receipt cannot finish")
        model.receive(try handoffEvent("saved"))
        try require(wire.count("finish") == 4 && model.handoffAcknowledged && wire.count("pair") == 1 &&
                    server.bindCount == 1 && server.verifyCount == 1,
                    "Finalization recovery neither pairs, binds nor approves another transfer")
        model.finished(mode: "connect", code: 0)
        try require(model.phase == .completed && model.phoneRun == nil,
                    "Only the successful helper exit completes native cleanup")
    }

    static func bindingReply(_ model: CollectorModel) -> [String: Any] {
        let claim = model.phoneRun!.claim
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return ["previousApprovalId": claim.approvalID, "approvalId": claim.approvalID,
            "enrollmentId": claim.enrollmentID, "destinationEmail": claim.email, "destinationCourse": claim.course,
            "categories": claim.categories, "disclosureVersion": claim.disclosureVersion,
            "expiresAt": formatter.string(from: claim.expiresAt)]
    }

    static func explicitApprovalAdoption() throws {
        let (model, server, wire, run) = try boundModel()
        try pending(model)
        let original = model.phoneRun!
        let nextEnrollment = "00000000-0000-4000-8000-0000000000ee"
        server.replace(run.runID, course: "Owner replacement", categories: ["contacts"], enrollmentID: nextEnrollment)
        server.faultsByPath[PhoneRunContract.bindPath] = [.loseReply, .malformed, .dropRequest]
        model.refreshRunStatus()
        try require(model.phoneRun?.claim == original.claim && model.runStatus == .uncertain &&
                    model.handoffID == handoff && model.selectedIDs == [3] && model.canRetryApprovalAdoption,
                    "Status and lost bind replies never adopt replacement authority or discard captured work")
        let uncertainBodies = server.requests.filter { $0.path == PhoneRunContract.bindPath }.suffix(3).map(\.body)
        model.retryApprovalAdoption()
        let adopted = model.phoneRun!
        try require(adopted.claim.approvalID != original.claim.approvalID && adopted.enrollmentID == nextEnrollment &&
                    adopted.course == "Owner replacement" && adopted.categories == ["contacts"] &&
                    adopted.boundHandoffID == original.boundHandoffID && adopted.boundPayloadSha256 == original.boundPayloadSha256 &&
                    adopted.claim.expiresAt == original.claim.expiresAt && adopted.startDeadline == original.startDeadline &&
                    !model.hasStartableRun && model.runStatus == .current,
                    "Only explicit bind proof adopts a narrower owner replacement on the same captured handoff")
        try require(Set(uncertainBodies + [server.requests.last!.body]).count == 1 &&
                    model.destinationIntentID.isEmpty && !model.destinationApproved && wire.count("pair") == 1,
                    "Lost reply retry uses the persisted previous approval and invalidates obsolete pre-consume decisions")
        let nextIntent = "00000000-0000-4000-8000-0000000000cc"
        try pending(model, intentID: nextIntent, course: "Owner replacement")
        let verification = server.requests.last { $0.path == PhoneRunContract.verifyIntentPath }!
        let fields = try JSONSerialization.jsonObject(with: verification.body) as! [String: String]
        try require(fields["expectedApprovalId"] == adopted.claim.approvalID &&
                    wire.decisions.last?["intentId"] as? String == nextIntent && wire.decisions.last?["approved"] as? Bool == true,
                    "New verification is pinned to the persisted explicit approval identity")
        try pending(model)
        try require(model.destinationIntentID == nextIntent, "Superseded approval intent cannot return")

        server.consume(run.runID)
        model.receive(try handoffEvent("received"))
        let consumedEnrollment = model.phoneRun!.enrollmentID
        server.replace(run.runID, course: "Receipt recovery", categories: ["contacts"])
        model.refreshRunStatus()
        try require(model.phoneRun?.enrollmentID == consumedEnrollment && model.browserReceived &&
                    model.destinationIntentID == nextIntent && wire.count("pair") == 1,
                    "Consumed same-transfer recovery preserves its enrollment, handoff and receipt identity")
    }

    static func replacementCannotBroadenOrRewriteSameApproval() throws {
        let server = SimulatedPhoneRunServer()
        let wire = Wire()
        let (model, run) = try approvedModel(server, wire, categories: ["contacts"])
        try start(model)
        try capture(model)
        try reviewed(model, wire)
        try paired(model, wire)
        let original = model.phoneRun!
        server.replace(run.runID, course: course, categories: ["contacts", "calls"])
        model.refreshRunStatus()
        try require(model.phoneRun?.claim == original.claim && model.runStatus == .replacementRequired && model.handoffBound,
                    "Replacement cannot broaden the already captured run's save authority")

        let (same, sameServer, _, sameRun) = try boundModel()
        let before = same.phoneRun!.claim
        sameServer.changeApproval(sameRun.runID)
        var forged = bindingReply(same)
        forged["destinationCourse"] = "Silently rewritten"
        // A parsed success must still fail native identity checks, even if a server erroneously supplies it.
        sameServer.faultsByPath[PhoneRunContract.bindPath] = [.dropRequest, .dropRequest, .dropRequest]
        same.refreshRunStatus()
        sameServer.replace(sameRun.runID, course: course, categories: ["contacts"])
        sameServer.replyOverrides[PhoneRunContract.bindPath] = forged
        same.retryApprovalAdoption()
        try require(same.phoneRun?.claim == before && same.runStatus != .current,
                    "An unchanged approval ID cannot silently change course or scope")
    }

    static func currentStatusRecoveryAndVersionFences() throws {
        for version in ["phone-run-v1", "phone-run-v2"] {
            let server = SimulatedPhoneRunServer()
            var clock = Date()
            server.now = { clock }
            let model = CollectorModel(commandWriter: { _ in }, runAuthority: client(server))
            model.now = { clock }
            let run = server.createRun(disclosureVersion: version)
            server.faults = [.loseReply, .loseReply, .loseReply]
            model.admitRunLink(link(server, run))
            try require(model.runApproval == .uncertain && model.phoneRun == nil,
                        "A lost committed claim remains undecided")
            let originalCapability = server.run(run.runID)?.capability
            clock = clock.addingTimeInterval(11 * 60)
            model.retryRunClaim()
            try require(model.runApproval == .approved && model.phoneRun?.capability == originalCapability &&
                        model.phoneRun?.claim.disclosureVersion == version && !model.hasStartableRun,
                        "An exact committed claim recovers after ten minutes without extending Start (\(version))")
            try require(Set(server.requests.filter { $0.path == PhoneRunContract.claimPath }.map(\.body)).count == 1,
                        "Recovery preserves exact claim and capability bytes")
            try require(model.approvedPrivacyURL?.absoluteString.contains(version == "phone-run-v1" ? "v6" : "v7") == true,
                        "Privacy URL follows the actual immutable receipt version")
            let foreign = CollectorModel(commandWriter: { _ in }, runAuthority: client(server))
            foreign.admitRunLink(link(server, run))
            try require(foreign.phoneRun == nil && foreign.runApproval == .refused,
                        "Another capability cannot recover the committed claim")
        }
        let clockServer = SimulatedPhoneRunServer()
        let (clockModel, _) = try approvedModel(clockServer, Wire())
        guard let original = clockModel.phoneRun?.claim, let start = original.startExpiresAt else {
            throw Failure.check("v2 start deadline missing")
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        clockServer.replyOverrides[PhoneRunContract.statusPath] = ["approvalId": original.approvalID,
            "enrollmentId": original.enrollmentID, "destinationEmail": original.email, "destinationCourse": original.course,
            "categories": original.categories, "disclosureVersion": original.disclosureVersion,
            "expiresAt": formatter.string(from: original.expiresAt),
            "startExpiresAt": formatter.string(from: start.addingTimeInterval(60)), "state": "current"]
        clockModel.refreshRunStatus()
        try require(clockModel.runStatus == .uncertain && clockModel.phoneRun?.claim == original,
                    "A contradictory current status cannot renew the original Start deadline")
        let legacy = SimulatedPhoneRunServer()
        let wire = Wire()
        let model = CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority: client(legacy))
        model.admitRunLink(link(legacy, legacy.createRun(disclosureVersion: "phone-run-v1")))
        try require(model.beginBackupEncryptionStatusCheck(), "Legacy approved run can check its phone")
        model.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#))
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .confirmedEncryptedBackup),
                    "A claim and phone setting alone cannot bypass fresh current-status admission")
        model.refreshRunStatus()
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .confirmedEncryptedBackup) &&
                    model.localCollectionApproval?.serverDisclosureVersion == "phone-run-v1",
                    "An old combined run keeps its original disclosure; it is never relabeled v2")
        let collectionOnly = SimulatedPhoneRunServer()
        let refused = CollectorModel(commandWriter: { _ in }, runAuthority: client(collectionOnly))
        refused.admitRunLink(link(collectionOnly, collectionOnly.createRun(disclosureVersion: "phone-collection-v2")))
        try require(refused.phoneRun == nil && !refused.startCollection(),
                    "Collection-only receipt can never become autosave authority")
    }

    static func currentStatusPausesAndStopsAdmission() throws {
        let (model, server, wire, run) = try boundModel()
        var clock = Date()
        model.now = { clock }
        server.now = { clock }
        clock = clock.addingTimeInterval(35 * 60)
        model.refreshRunStatus()
        try require(model.runStatus == .current && model.phoneRun?.runID == run.runID,
                    "Current capability status works during collection beyond the Start window")
        server.faultsByPath[PhoneRunContract.statusPath] = [.dropRequest, .malformed, .status(503)]
        model.refreshRunStatus()
        try require(model.runStatus == .uncertain && model.handoffBound && model.selectedIDs == [3] &&
                    wire.last?["action"] as? String == "run-authority" && wire.last?["state"] as? String == "paused",
                    "Uncertain authority pauses the existing bridge and retains the immutable review")
        let binding = model.phoneRun
        model.refreshRunStatus()
        try require(model.runStatus == .current && model.phoneRun == binding &&
                    wire.last?["state"] as? String == "current" && wire.count("pair") == 1,
                    "Current status resumes the exact handoff without new authority or pairing")
        server.revoke(run.runID)
        model.refreshRunStatus()
        try require(model.runStatus == .revoked && model.phoneRun == nil && model.handoffID.isEmpty &&
                    wire.last?["action"] as? String == "disconnect",
                    "Explicit server revocation stops the local helper and all new admission")
        let (started, changed, changedWire, changedRun) = try startedModel()
        changed.changeApproval(changedRun.runID)
        started.refreshRunStatus()
        try require(started.runStatus == .replacementRequired && started.phoneRun?.runID == changedRun.runID &&
                    changedWire.count("disconnect") == 0,
                    "Changed authority pauses admission while the originally authorized capture is retained")
    }

    static func healthyRefreshSharesAuthority() throws {
        let (model, server, wire, _) = try boundModel()
        let original = model.phoneRun
        let before = server.statusCount
        server.holdPaths = [PhoneRunContract.statusPath]
        model.refreshRunStatus()
        try require(wire.last?["state"] as? String == "refreshing",
                    "A healthy current grant must refresh without a terminal delivery pause")
        try pending(model)
        try require(server.statusCount == before + 1 && model.destinationVerificationPending &&
                    !model.destinationVerificationUncertain && server.verifyCount == 0,
                    "Destination verification joins the exact in-flight status request")
        server.releaseHeldReplies(path: PhoneRunContract.statusPath)
        try require(server.verifyCount == 1 && wire.decisions.count == 1 &&
                    wire.decisions[0]["approved"] as? Bool == true && model.phoneRun == original,
                    "The joined verification proceeds once after current authority without changing the handoff")
        server.faultsByPath[PhoneRunContract.statusPath] = [.dropRequest, .dropRequest, .dropRequest]
        model.refreshRunStatus()
        server.holdPaths = [PhoneRunContract.statusPath]
        model.refreshRunStatus()
        try require(wire.last?["state"] as? String == "paused" && model.runStatus == .uncertain,
                    "Retrying uncertain authority cannot reopen a transient refresh window")
        model.disconnect()
        server.releaseHeldReplies()
        try require(model.phoneRun == nil && wire.last?["action"] as? String == "disconnect",
                    "A late current reply after Stop cannot resume a retired handoff")
    }

    static func authenticatedPendingSupersession() throws {
        let newer = "00000000-0000-4000-8000-0000000000cc"
        let (model, server, wire, _) = try boundModel()
        try pending(model)
        model.receive(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        try pending(model, intentID: newer)
        try require(model.destinationIntentID == newer && !model.destinationApproved && wire.decisions.count == 2 &&
                    wire.decisions.last?["intentId"] as? String == newer && server.verifyCount == 2,
                    "A fresh helper-authenticated pending intent supersedes the approved but unconsumed attempt")
        try pending(model)
        try require(model.destinationIntentID == newer && wire.decisions.count == 2,
                    "A closed old intent cannot be resurrected by a delayed helper event")

        let (waiting, waitingServer, waitingWire, _) = try boundModel()
        waitingServer.holdReplies = true
        try pending(waiting)
        try pending(waiting, intentID: newer)
        waitingServer.releaseHeldReplies()
        try require(waitingWire.decisions.count == 1 && waitingWire.decisions[0]["intentId"] as? String == newer &&
                    waiting.destinationIntentID == newer && waitingServer.verifyCount == 1,
                    "The shared current-status result verifies only the newest intent; its stale waiter cannot approve")
    }

    static func staleDestinationEventsKeepTheDecision() throws {
        let other = "00000000-0000-4000-8000-0000000000cc"
        // Once the approved transfer is received, late pending/denied events cannot replace it.
        let (model, server, wire, _) = try boundModel()
        try pending(model)
        model.receive(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        model.receive(try handoffEvent("received"))
        model.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        try pending(model)
        try pending(model, intentID: other, email: "late@example.invalid")
        model.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(handoff)","intentId":"\#(other)"}"#))
        try require(model.destinationApproved && model.destinationIntentID == intent && wire.decisions.count == 1 &&
                    server.verifyCount == 1, "Late denied or pending events cannot clear, reverse or replace the approval")

        // A denied (answered) intent is never resurrected; a new intent from the website is verified again.
        let (denied, _, deniedWire, _) = try boundModel()
        try pending(denied, email: "someone.else@example.invalid")
        try require(deniedWire.decisions.count == 1 && deniedWire.decisions[0]["approved"] as? Bool == false, "Mismatch is denied")
        denied.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        try pending(denied)
        try require(denied.destinationIntentID.isEmpty && deniedWire.decisions.count == 1,
                    "A denied intent is closed: a delayed pending cannot resurrect it")
        try pending(denied, intentID: other)
        try require(deniedWire.decisions.count == 2 && deniedWire.decisions[1]["approved"] as? Bool == true &&
                    deniedWire.decisions[1]["intentId"] as? String == other,
                    "A separate intent (the website's replacement) is verified and approved on its own")
        denied.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        try require(denied.destinationIntentID == other, "A late denial for the old intent cannot clear the newer one")
        // Events for another handoff are ignored.
        let before = deniedWire.decisions.count
        try pending(denied, intentID: "00000000-0000-4000-8000-0000000000ef", handoffID: otherHandoff)
        try require(deniedWire.decisions.count == before, "A pending for another handoff is ignored")
    }

    /// After release, a 15-minute paged transfer (synthetic clock) finishes with no focus change and no prompt.
    static func longReleasedTransferCompletesWithoutFocus() throws {
        let server = SimulatedPhoneRunServer()
        var clock = Date()
        server.now = { clock }
        let wire = Wire()
        let model = CollectorModel(commandWriter: { wire.commands.append($0) }, runAuthority: client(server))
        model.now = { clock }
        model.admitRunLink(link(server, server.createRun()))
        try start(model)
        try capture(model)
        try reviewed(model, wire)
        try paired(model, wire)
        let surface = RecordingCompanionSurface()
        let director = CompanionWindowDirector()
        director.update(model: model, surface: surface)
        try pending(model)
        model.receive(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        for _ in 0..<15 {
            clock = clock.addingTimeInterval(60)
            model.receive(try handoffEvent("waiting"))
            director.update(model: model, surface: surface)
        }
        for state in ["received", "saved_pending_browser_receipt", "saved"] {
            model.receive(try handoffEvent(state))
            director.update(model: model, surface: surface)
        }
        try require(wire.last?["action"] as? String == "finish" && model.handoffAcknowledged, "The save completes and finish is sent")
        model.receive(try event(#"{"kind":"state","state":"completed"}"#))
        model.finished(mode: "connect", code: 0)
        director.update(model: model, surface: surface)
        try require(model.phase == .completed && surface.focusCallCount == 0 && surface.miniaturizeCount == 1 &&
                    wire.decisions.count == 1, "No activation, restore, sheet or second decision during a long transfer")
    }

    // MARK: Handoff and focus

    static func handoffAfterBindWithoutReceived() throws {
        let (model, _, _, run) = try boundModel()
        try require(model.browserHandoffReady && !model.browserReceived && !model.destinationApproved,
                    "The website owns the flow right after the bind, before any destination or received")
        try require(model.accountNavigation?.url.absoluteString ==
                    "https://amplifai.satoris.ai/phone/account?continue=reviewed&enrollmentId=\(model.phoneRun!.enrollmentID)&week=phone" &&
                    model.accountNavigation?.handoffID == handoff && run.runID != model.phoneRun!.enrollmentID,
                    "The account opens on the run's enrollment at the fixed origin")
        try require(model.statusTitle == "Saving in your AMPLIFai account" && !model.statusDetail.contains("received") &&
                    !model.statusDetail.contains("saved"), "Passive status claims nothing before the helper reports it")
    }

    static func noFocusBounceAfterHandoff() throws {
        let (model, _, wire, _) = try boundModel()
        let surface = RecordingCompanionSurface()
        let director = CompanionWindowDirector()
        func step(_ event: AgentEvent?) {
            if let event { model.receive(event) }
            director.update(model: model, surface: surface)
        }
        step(nil)
        try require(surface.calls == ["open", "miniaturize"] &&
                    surface.openedURLs.first == model.accountNavigation?.url, "The handoff opens the account, then minimizes once")
        try pending(model)
        step(nil)
        step(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        for state in ["waiting", "waiting", "received", "saved_pending_browser_receipt"] { step(try handoffEvent(state)) }
        step(try event(#"{"kind":"error","code":"handoff_unconfirmed"}"#))
        step(try event(#"{"kind":"error","code":"bridge_unavailable"}"#))
        step(try handoffEvent("saved"))
        try require(wire.last?["action"] as? String == "finish", "Saved sends finish")
        step(try event(#"{"kind":"state","state":"completed"}"#))
        model.finished(mode: "connect", code: 0)
        step(nil)
        try require(model.phase == .completed && model.statusTitle == "Saved — your report is in your account",
                    "Completion shows Saved for an owner who reopens the window")
        try require(surface.focusCallCount == 0 && surface.miniaturizeCount == 1 && surface.calls.filter { $0 == "open" }.count == 1,
                    "No activate, deminiaturize, raise or sheet across pending, approved, received, saved and completion")

        // Errors and helper exit after the handoff never bring the window back.
        let (failing, _, _, _) = try boundModel()
        let failingSurface = RecordingCompanionSurface()
        let failingDirector = CompanionWindowDirector()
        failingDirector.update(model: failing, surface: failingSurface)
        for json in [#"{"kind":"error","code":"handoff_unconfirmed"}"#, #"{"kind":"error","code":"connection_lost"}"#] {
            failing.receive(try event(json))
            failingDirector.update(model: failing, surface: failingSurface)
        }
        failing.finished(mode: "connect", code: 1)
        failingDirector.update(model: failing, surface: failingSurface)
        try require(failingSurface.focusCallCount == 0 && failingSurface.miniaturizeCount == 1 && failing.phase == .error,
                    "Errors and an unexpected helper exit after the handoff leave the window minimized")
    }

    static func lateBrowserOpenAnswersChangeNothing() throws {
        for opened in [true, false] {
            let (model, _, _, _) = try boundModel()
            let surface = RecordingCompanionSurface()
            surface.holdOpenAnswers = true
            let director = CompanionWindowDirector()
            director.update(model: model, surface: surface)
            model.disconnect()
            surface.answerHeldOpens(opened)
            try require(surface.miniaturizeCount == 0 && model.errorMessage.isEmpty && model.phase == .idle,
                        "A late browser-open answer after cancel changes nothing (opened: \(opened))")
        }
        for opened in [true, false] {
            let (model, _, _, _) = try boundModel()
            let surface = RecordingCompanionSurface()
            surface.holdOpenAnswers = true
            let director = CompanionWindowDirector()
            director.update(model: model, surface: surface)
            model.receive(try handoffEvent("expired"))
            let message = model.errorMessage
            surface.answerHeldOpens(opened)
            try require(surface.miniaturizeCount == 0 && model.errorMessage == message,
                        "A late browser-open answer after the handoff changed does nothing (opened: \(opened))")
        }
        let (failed, _, _, _) = try boundModel()
        let surface = RecordingCompanionSurface()
        surface.openSucceeds = false
        CompanionWindowDirector().update(model: failed, surface: surface)
        try require(surface.miniaturizeCount == 0 && failed.errorMessage == CollectorModel.navigationFailedMessage,
                    "A browser that did not open keeps the window in front with recovery")
    }

    static func expiryBeforeAndAfterReleaseSpendsTheRun() throws {
        for released in [false, true] {
            let (model, server, wire, _) = try boundModel()
            let surface = RecordingCompanionSurface()
            let director = CompanionWindowDirector()
            director.update(model: model, surface: surface)
            if released {
                try pending(model)
                model.receive(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
            }
            let pairs = wire.count("pair")
            let binds = server.bindCount
            let decisions = wire.decisions.count
            model.receive(try handoffEvent("expired"))
            director.update(model: model, surface: surface)
            try require(model.errorMessage == CollectorModel.transferExpiredMessage &&
                        model.errorMessage.contains("Check your account for any sources already saved") &&
                        model.errorMessage.contains("new collection") && !model.errorMessage.contains("Nothing was saved"),
                        "Expiry names the restart truthfully (released: \(released))")
            model.pairBrowser(automaticAccountNavigation: true)
            director.update(model: model, surface: surface)
            try require(wire.count("pair") == pairs && server.bindCount == binds && wire.decisions.count == decisions &&
                        model.phoneRun == nil && !model.canContinueToAccount && surface.focusCallCount == 0 &&
                        surface.miniaturizeCount == 1 && model.selectedIDs == [3],
                        "An expired run-bound handoff spends the run: no new pair, bind or decision, no focus (released: \(released))")
            let next = server.createRun()
            try require(!model.admitRunLink(link(server, next)), "A new collection's link waits until the review is discarded")
            model.disconnect()
            try require(model.admitRunLink(link(server, next)) && model.runApproval == .approved,
                        "After discard, a fresh link for a new collection is admitted")
        }
    }

    static func approvedToSaveLabel() throws {
        try require(CollectorModel.approvedToSaveLabel(["messages", "contacts"]) == "Approved to save: chosen contacts, their message metadata" &&
                    CollectorModel.approvedToSaveLabel(["contacts", "calls", "messages"]).hasPrefix("Approved to save: "),
                    "Run categories are labelled as what may be saved")
    }

    static func productionTransportShape() throws {
        guard let request = URLSessionPhoneRunTransport.request(path: PhoneRunContract.claimPath, body: Data("{}".utf8)) else {
            throw Failure.check("request")
        }
        try require(request.url?.absoluteString == "https://amplifai-database-engine.vercel.app/api/v1/phone-run-approvals/claim" &&
                    request.httpMethod == "POST" && request.value(forHTTPHeaderField: "Content-Type") == "application/json" &&
                    request.value(forHTTPHeaderField: "Accept") == "application/json" && !request.httpShouldHandleCookies &&
                    request.value(forHTTPHeaderField: "Cookie") == nil && request.value(forHTTPHeaderField: "Origin") == nil,
                    "The production request targets only the fixed host with JSON and no cookie or origin")
        try require(URLSessionPhoneRunTransport.request(path: "/api/v1/other", body: Data()) == nil &&
                    URLSessionPhoneRunTransport.request(path: PhoneRunContract.bindPath, body: Data(count: 2000)) == nil,
                    "No other path and no oversized body can be sent")
        guard let statusRequest = PhoneRunStatusRequest(runID: handoff, capability: String(repeating: "C", count: 43)),
              let statusBody = try JSONSerialization.jsonObject(with: statusRequest.body) as? [String: String] else {
            throw Failure.check("status request")
        }
        try require(Set(statusBody.keys) == ["runId", "capability"] &&
                    URLSessionPhoneRunTransport.request(path: PhoneRunContract.statusPath, body: statusRequest.body)?.url?.host == PhoneRunContract.host,
                    "Status sends only the original run/capability to the fixed authority")
        try require(PhoneRunContract.privacyURL(disclosureVersion: "phone-run-v1")?.absoluteString ==
                    "https://amplifai.satoris.ai/experience/account-privacy?version=account-2026-10-v6" &&
                    PhoneRunContract.privacyURL(disclosureVersion: "phone-run-v2")?.absoluteString ==
                    "https://amplifai.satoris.ai/experience/account-privacy?version=account-2026-10-v7#phone-collection-and-saving",
                    "Each receipt links to its exact immutable notice with only a verified section anchor")
        let base: [String: Any] = ["approvalId": intent, "enrollmentId": handoff, "destinationEmail": email,
            "destinationCourse": course, "categories": ["contacts"], "disclosureVersion": "phone-run-v2",
            "expiresAt": "2026-10-19T23:00:00Z", "startExpiresAt": "2026-10-19T11:10:00Z", "state": "current"]
        let validStatusBody = try JSONSerialization.data(withJSONObject: base)
        try require(PhoneRunContract.status(validStatusBody)?.state == .current,
                    "Strict current status accepts its complete versioned identity")
        for (field, value) in [("state", "stopped"), ("disclosureVersion", "phone-collection-v2"),
                               ("startExpiresAt", "2026-10-20T00:00:00Z"), ("unexpected", "value")] {
            var malformed = base
            malformed[field] = value
            let malformedBody = try JSONSerialization.data(withJSONObject: malformed)
            try require(PhoneRunContract.status(malformedBody) == nil,
                        "Status rejects unknown state, collection-only authority, expanded Start expiry and extra keys")
        }
        let configuration = URLSessionPhoneRunTransport.configuration()
        try require(configuration.httpCookieStorage == nil && configuration.urlCredentialStorage == nil &&
                    configuration.urlCache == nil && configuration.httpCookieAcceptPolicy == .never &&
                    !configuration.httpShouldSetCookies, "The session is ephemeral with no cookies, cache or credentials")
        try require(PhoneRunAuthorityClient.isJSON("application/json; charset=utf-8") && !PhoneRunAuthorityClient.isJSON("text/html") &&
                    !PhoneRunAuthorityClient.isJSON("application/jsonp"), "Only a JSON reply is accepted")
        let oversized = Data(("{\"approvalId\":\"" + String(repeating: "a", count: 2100) + "\"}").utf8)
        try require(PhoneRunContract.claim(oversized) == nil && PhoneRunContract.destination(Data(#"{"enrollmentId":"0f8fad5b-d9cb-469f-a165-70867728950e","destinationEmail":"a@b.c","destinationCourse":"C","categories":["contacts"],"extra":1}"#.utf8)) == nil &&
                    PhoneRunContract.destination(Data(#"{"enrollmentId":"0f8fad5b-d9cb-469f-a165-70867728950e","destinationEmail":"a@b.c","destinationCourse":"C","categories":["calls"]}"#.utf8)) == nil &&
                    PhoneRunContract.destination(Data(#"{"enrollmentId":"0f8fad5b-d9cb-469f-a165-70867728950e","destinationEmail":"a@b.c","destinationCourse":1,"categories":["contacts"]}"#.utf8)) == nil,
                    "Strict parsing rejects oversized replies, unknown fields, missing contacts and wrong types")
    }

    /// Source scan: AppKit window-ordering and activation calls appear only in the surface adapter.
    static func activationCallsOnlyThroughSurface() throws {
        let sources = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources")
        guard let files = FileManager.default.enumerator(at: sources, includingPropertiesForKeys: nil)?
            .compactMap({ $0 as? URL }).filter({ $0.pathExtension == "swift" }), !files.isEmpty else {
            throw Failure.check("Sources not found for the activation scan")
        }
        // Call sites (receiver.call): protocol declarations without a receiver are allowed.
        let forbidden = [".activate(", ".deminiaturize(", ".makeKeyAndOrderFront(", ".orderFront(", ".orderFrontRegardless(",
                         ".beginSheetModal(", ".runModal(", ".requestUserAttention("]
        for file in files where file.lastPathComponent != "AppKitCompanionSurface.swift" {
            let text = try String(contentsOf: file, encoding: .utf8)
            for token in forbidden where text.contains(token) {
                throw Failure.check("\(file.lastPathComponent) calls \(token) outside AppKitCompanionSurface")
            }
            try require(!text.contains("NSAlert"), "\(file.lastPathComponent) presents an alert outside the surface")
        }
    }
}
