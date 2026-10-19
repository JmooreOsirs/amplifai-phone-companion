import Darwin
import Foundation

/// Drives the production CollectorModel through its real helper launch path against the compiled
/// FAKE DATA helper placed next to this executable. No device, browser, account or network is used.
@main @MainActor
enum FakeDemoChecks {
    private enum Failure: Error { case check(String) }
    private static func require(_ value: @autoclosure () -> Bool, _ reason: String) throws {
        guard value() else { throw Failure.check(reason) }
    }
    private static func wait(_ seconds: Double, _ reason: String, until done: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(seconds)
        while !done() {
            guard Date() < deadline else { throw Failure.check("Timed out: \(reason)") }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    static func main() async {
        do {
            try dataSetIsFictionalAndSelectionDriven()
            try bridgeTimelineFollowsRealOrder()
            try bridgeTimelineClockRules()
            try await journey(.all)
            try await journey(.subset)
            try await journey(.none)
            try await cancellationAndRecovery()
            print("FAKE DATA demo isolation, real bridge order and clock, selection-driven sample report, one-approval journey, cancellation and recovery checks passed")
        } catch {
            FileHandle.standardError.write(Data("FAKE DATA demo check failed: \(error)\n".utf8))
            exit(1)
        }
    }

    private static func dataSetIsFictionalAndSelectionDriven() throws {
        let contacts = FakeDataSet.contacts
        try require(contacts.count == 312 && Set(contacts.map(\.name)).count == 312, "312 unique fictional names")
        try require(FakeDataSet.email.hasSuffix("@example.invalid"), "Demo account email uses a reserved invalid domain")
        try require(SupportDiagnosticReporter.validatedBuild(["CFBundleVersion": "fake-demo"]) == nil,
                    "The demo build identity can never form a diagnostic upload")
        let all = FakeDataSet.sampleReport(selected: Set(contacts.map(\.id)))
        let subset = FakeDataSet.sampleReport(selected: [1, 2, 3, 26])
        try require(all?.shared == 312 && all?.notIncluded == 0 && all?.calls == FakeDataSet.availableCalls,
                    "Selecting everyone reports every fictional interaction")
        try require(subset?.shared == 4 && subset?.notIncluded == 308 && subset?.withoutActivity == 1 &&
                    subset?.calls == contacts.filter { [1, 2, 3, 26].contains($0.id) }.reduce(0) { $0 + $1.calls },
                    "A subset report counts only the chosen contacts")
        try require(FakeDataSet.sampleReport(selected: []) == nil, "No selection produces no report")
    }

    /// The fake helper's phases follow bridge.py: never received before complete, saved only after confirm.
    private static func bridgeTimelineFollowsRealOrder() throws {
        var timeline = FakeBridgeTimeline(pairedAt: 0)
        try require(!timeline.apply(.release, at: 1) && !timeline.apply(.complete, at: 1) && !timeline.apply(.acknowledge, at: 1),
                    "Nothing is released, completed or acknowledged before connect and approval")
        try require(timeline.apply(.connect, at: 1) && timeline.destinationPending && timeline.handoffState == "waiting",
                    "Connect makes a pending destination")
        try require(!timeline.apply(.release, at: 2), "Release needs the app's approval")
        try require(timeline.decide(approved: true, at: 2) && !timeline.decide(approved: true, at: 2), "One decision per intent")
        try require(timeline.apply(.release, at: 3) && timeline.handoffState == "waiting",
                    "After release the manifest is out but handoff_state is still waiting (used is false)")
        try require(!timeline.apply(.complete, at: 4) && !timeline.apply(.acknowledge, at: 4) && timeline.handoffState == "waiting",
                    "No complete or save receipt before every page")
        try require(timeline.apply(.page, at: 5) && timeline.apply(.pages, at: 6) && timeline.handoffState == "waiting",
                    "Every page delivered is still not received")
        try require(!timeline.apply(.acknowledge, at: 7), "No save receipt before complete")
        try require(timeline.apply(.complete, at: 8) && timeline.handoffState == "received", "Only complete reports received")
        try require(!timeline.apply(.confirm, at: 9), "No final receipt before the first")
        try require(timeline.apply(.acknowledge, at: 9) && timeline.handoffState == "saved_pending_browser_receipt" &&
                    timeline.apply(.confirm, at: 10) && timeline.handoffState == "saved", "saved_pending, then saved")
        var denied = FakeBridgeTimeline(pairedAt: 0)
        denied.apply(.connect, at: 1)
        denied.decide(approved: false, at: 2)
        try require(!denied.apply(.release, at: 3) && denied.apply(.connect, at: 4) && denied.connectAttempt == 2,
                    "A denied intent releases nothing; the website may connect once more with a new intent")
    }

    /// bridge.py clock rules: five minutes from pair to release, then two hours refreshed by every page.
    private static func bridgeTimelineClockRules() throws {
        // The five-minute window starts at pair after review, so a 35-minute collection before it never expires it.
        let pairedAfterLongCollection: TimeInterval = 35 * 60
        var onTime = FakeBridgeTimeline(pairedAt: pairedAfterLongCollection)
        try require(onTime.pairDeadline == pairedAfterLongCollection + 300, "The deadline is pair time + 300 s, not claim time")
        onTime.apply(.connect, at: pairedAfterLongCollection + 10)
        onTime.decide(approved: true, at: pairedAfterLongCollection + 20)
        try require(onTime.apply(.release, at: pairedAfterLongCollection + 299), "Release at pair + 4 min 59 s works")
        var late = FakeBridgeTimeline(pairedAt: 0)
        late.apply(.connect, at: 10)
        late.decide(approved: true, at: 20)
        try require(!late.apply(.release, at: 301) && late.handoffState == "expired", "Release at pair + 5 min 01 s is refused (expired)")
        var unclaimed = FakeBridgeTimeline(pairedAt: 0)
        try require(unclaimed.expireIfDue(at: 300) && unclaimed.handoffState == "expired", "An unreleased bridge closes at five minutes")
        // A 15-minute paged transfer after release completes inside the refreshed two-hour window.
        var transfer = FakeBridgeTimeline(pairedAt: 0)
        transfer.apply(.connect, at: 10)
        transfer.decide(approved: true, at: 20)
        transfer.apply(.release, at: 240)
        for minute in 1...14 { try require(transfer.apply(.page, at: 240 + Double(minute) * 60), "Page at \(minute) min") }
        try require(transfer.apply(.pages, at: 240 + 15 * 60) && transfer.apply(.complete, at: 240 + 15 * 60 + 5) &&
                    transfer.handoffState == "received" && transfer.apply(.acknowledge, at: 240 + 15 * 60 + 10) &&
                    transfer.apply(.confirm, at: 240 + 15 * 60 + 15) && transfer.handoffState == "saved",
                    "A 15-minute transfer completes: received, saved_pending, saved")
        var idle = FakeBridgeTimeline(pairedAt: 0)
        idle.apply(.connect, at: 10)
        idle.decide(approved: true, at: 20)
        idle.apply(.release, at: 60)
        idle.apply(.page, at: 60 + 3600)
        try require(!idle.expireIfDue(at: 60 + 3600 + 7199) && idle.expireIfDue(at: 60 + 3600 + 7200) &&
                    idle.handoffState == "expired", "Over two hours without activity after the last page expires")
    }

    enum Selection { case all, subset, none }

    private static func runModel() -> (CollectorModel, SimulatedPhoneRunServer, PhoneRunLinkInbox) {
        let server = SimulatedPhoneRunServer()
        let model = CollectorModel(bundleInformation: ["CFBundleVersion": "fake-demo"],
                                   runAuthority: PhoneRunAuthorityClient(transport: server, schedule: PhoneRunAuthorityClient.immediate))
        model.handoffPollNanoseconds = 150_000_000
        let inbox = PhoneRunLinkInbox()
        inbox.attach(model)
        return (model, server, inbox)
    }

    private static func approvedRun(_ model: CollectorModel, _ server: SimulatedPhoneRunServer, _ inbox: PhoneRunLinkInbox) throws {
        let run = server.createRun(email: FakeDataSet.email, course: FakeDataSet.course)
        // The simulated website's fictional link, delivered in-process: no URL scheme is registered.
        try require(inbox.receive(server.appLink(run), returnBrowserBundleID: nil) && model.runApproval == .approved &&
                    model.phoneRun?.email == FakeDataSet.email && model.phoneRun?.course == FakeDataSet.course,
                    "The fictional link is claimed and shows the FAKE DATA account and course")
    }

    private static func journey(_ selection: Selection) async throws {
        let (model, server, inbox) = runModel()
        try require(!model.startCollection() && model.journeyStep == 0, "Nothing starts before the website's approval")
        try approvedRun(model, server, inbox)
        try require(model.startCollection(), "Start collection runs the simulated iPhone check, then starts")
        try await wait(10, "simulated collection") { model.phase == .selecting }
        try require(model.totalContacts == 312 && model.journeyStep == 2 && server.claimCount == 2,
                    "Simulated collection reaches Choose after the exact claim replay")
        model.prepareWorkspace()
        try await wait(10, "full contact scan") { model.workspace != nil }
        guard let workspace = model.workspace else { throw Failure.check("workspace") }
        try require(model.selectedIDs == workspace.mainIDs && workspace.reviewCount == 20,
                    "Main starts selected; the 20 no-phone or LLC contacts wait in Review")
        switch selection {
        case .all: model.selectAllVerifiedContacts()
        case .subset:
            model.clearAllVerifiedContacts()
            model.showContactGroup(.all)
            for id in [1, 2, 3] { model.toggle(id) }
        case .none: model.clearAllVerifiedContacts()
        }
        let chosen = model.selectedIDs
        // Every helper event in order, and the window surface the director drives.
        var states: [String] = []
        let surface = RecordingCompanionSurface()
        let director = CompanionWindowDirector()
        model.eventObserver = { event in
            if event.kind == "handoff", let state = event.state { states.append(state) }
            if event.kind == "destination", let state = event.state { states.append("destination:" + state) }
            director.update(model: model, surface: surface)
        }
        model.reviewWorkspace(continueToAccount: true)
        if selection == .none {
            try require(!model.reviewPending && model.phase == .selecting && model.accountNavigationID == nil,
                        "No selection cannot continue to the account")
            model.disconnect()
            try await wait(5, "discard with no selection") { !model.helperRunning && model.phase == .idle }
            try require(model.phoneRun == nil, "Discarding ends the run")
            return
        }
        try await wait(10, "bound handoff") { model.handoffBound }
        director.update(model: model, surface: surface)
        let expected = FakeDataSet.sampleReport(selected: chosen)
        try require(model.matchedCalls == expected?.calls && model.matchedMessages == expected?.messages,
                    "Review counts come from the chosen fictional contacts")
        try require(model.browserHandoffReady && surface.calls == ["open", "miniaturize"] && states.isEmpty &&
                    surface.openedURLs.first?.query?.contains("enrollmentId=\(model.phoneRun!.enrollmentID)") == true,
                    "The handoff happens right after the bind, before any destination or received")
        try await wait(20, "simulated website saves") { model.handoffAcknowledged }
        try await wait(6, "fake helper exit after the save") { model.phase == .completed && !model.helperRunning }
        director.update(model: model, surface: surface)
        guard let approved = states.firstIndex(of: "destination:approved"),
              let received = states.firstIndex(of: "received"),
              let pendingSave = states.firstIndex(of: "saved_pending_browser_receipt"),
              let saved = states.firstIndex(of: "saved") else { throw Failure.check("missing phases: \(states)") }
        try require(states.firstIndex(of: "destination:pending")! < approved &&
                    states[approved..<received].filter { $0 == "waiting" }.count >= 2 &&
                    received < pendingSave && pendingSave < saved && !states[..<approved].contains("received"),
                    "Real phase order: approved, waiting through release and pages, received only after complete, then saved_pending and saved: \(states)")
        try require(surface.focusCallCount == 0 && surface.miniaturizeCount == 1,
                    "Zero activation, restore or sheet calls across the whole website-owned flow")
        try require(model.selectedIDs.isEmpty && model.journeyStep == 4 && model.phoneRun == nil,
                    "Completion clears the private review and ends the run")
    }

    private static func cancellationAndRecovery() async throws {
        let (cancelled, cancelledServer, cancelledInbox) = runModel()
        try approvedRun(cancelled, cancelledServer, cancelledInbox)
        try require(cancelled.startCollection(), "start")
        cancelled.cancelBackupEncryptionStatusCheck()
        try require(cancelled.journeyStep == 0, "Cancelling the simulated check stays on Approve")
        try await wait(5, "check cancellation settles") { !cancelled.helperRunning && cancelled.phase == .idle }
        try require(cancelled.runApproval == .approved && cancelled.phoneRun?.collectionStarted == false,
                    "Cancelling the pre-start check keeps the approved run")

        let (stopped, stoppedServer, stoppedInbox) = runModel()
        try approvedRun(stopped, stoppedServer, stoppedInbox)
        try require(stopped.startCollection(), "start")
        try await wait(5, "first simulated connection event") { !stopped.connectionTransport.isEmpty }
        stopped.disconnect()
        try await wait(20, "cancel collection") { !stopped.helperRunning && stopped.phase == .idle }
        try require(stopped.errorMessage.isEmpty && !stopped.hasUnsavedReview && stopped.journeyStep == 0 &&
                    stopped.phoneRun == nil && !stopped.startCollection(),
                    "Cancel collection returns to Approve without an error or retained data, and the run is spent")
    }
}
