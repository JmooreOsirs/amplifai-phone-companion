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
            try await journey(.all)
            try await journey(.subset)
            try await journey(.none)
            try await cancellationAndRecovery()
            print("FAKE DATA demo isolation, selection-driven sample report, journey, cancellation and recovery checks passed")
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

    enum Selection { case all, subset, none }

    private static func journey(_ selection: Selection) async throws {
        let model = CollectorModel(bundleInformation: ["CFBundleVersion": "fake-demo"])
        model.checkBackupEncryptionStatus()
        try await wait(5, "simulated iPhone check") { !model.helperRunning }
        try require(model.backupEncryptionStatus == .unencrypted && model.journeyStep == 0, "Simulated check reports Off on Agree")
        try require(model.agreeAndStartCollection(for: model.collectionApprovalRunID, checked: true), "Start must launch the fake helper")
        try await wait(10, "simulated collection") { model.phase == .selecting }
        try require(model.totalContacts == 312 && model.journeyStep == 2, "Simulated collection reaches Choose")
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
        model.reviewWorkspace(continueToAccount: true)
        if selection == .none {
            try require(!model.reviewPending && model.phase == .selecting && model.accountNavigationID == nil,
                        "No selection cannot continue to the account")
            model.disconnect()
            try await wait(5, "discard with no selection") { !model.helperRunning && model.phase == .idle }
            return
        }
        try await wait(10, "local review and one-time link") { model.phase == .reviewing && !model.pairCode.isEmpty }
        let expected = FakeDataSet.sampleReport(selected: chosen)
        try require(model.matchedCalls == expected?.calls && model.matchedMessages == expected?.messages,
                    "Review counts come from the chosen fictional contacts")
        try require(model.accountNavigationID != nil && !model.handoffAcknowledged,
                    "A browser open is requested (the demo intercepts it) but nothing is saved")
        try await wait(6, "simulated browser asks to connect") { model.pendingDestinationApproval != nil }
        try require(!model.destinationApproved, "Sharing waits for the Mac confirmation")
        guard let request = model.pendingDestinationApproval else { throw Failure.check("destination") }
        try require(request.email == FakeDataSet.email && request.course == FakeDataSet.course, "Only the fictional account is shown")
        model.decideAccountDestination(true, request: request)
        try require(model.destinationDecisionPending && model.pendingDestinationApproval == nil,
                    "One decision: Confirming shows until the helper acknowledges")
        try await wait(6, "helper acknowledges the sharing choice") { model.destinationApproved }
        try require(!model.destinationDecisionPending, "Acknowledged choice moves to Sending")
        try await wait(6, "simulated browser receipt") { model.browserReceived }
        try require(model.browserHandoffReady, "After the confirmation and receipt, Save belongs to the website")
        try await wait(12, "simulated browser receipt and Save") { model.handoffAcknowledged }
        try require(!model.browserHandoffReady, "A confirmed save ends the handoff")
        try await wait(6, "fake helper exit after Save") { model.phase == .completed && !model.helperRunning }
        try require(model.selectedIDs.isEmpty && model.journeyStep == 4, "Completion clears the private review")
    }

    private static func cancellationAndRecovery() async throws {
        let cancelled = CollectorModel(bundleInformation: ["CFBundleVersion": "fake-demo"])
        cancelled.checkBackupEncryptionStatus()
        cancelled.cancelBackupEncryptionStatusCheck()
        try require(cancelled.journeyStep == 0, "Cancelling the simulated check stays on Agree")
        try await wait(5, "check cancellation settles") { !cancelled.helperRunning && cancelled.phase == .idle }

        let stopped = CollectorModel(bundleInformation: ["CFBundleVersion": "fake-demo"])
        stopped.checkBackupEncryptionStatus()
        try await wait(5, "check") { !stopped.helperRunning }
        try require(stopped.agreeAndStartCollection(for: stopped.collectionApprovalRunID, checked: true), "start")
        try await wait(5, "first simulated connection event") { !stopped.connectionTransport.isEmpty }
        stopped.disconnect()
        try await wait(20, "cancel collection") { !stopped.helperRunning && stopped.phase == .idle }
        try require(stopped.errorMessage.isEmpty && !stopped.hasUnsavedReview && stopped.journeyStep == 0,
                    "Cancel collection returns to Agree without an error or retained data")

        let revoked = CollectorModel(bundleInformation: ["CFBundleVersion": "fake-demo"])
        revoked.checkBackupEncryptionStatus()
        try await wait(5, "check") { !revoked.helperRunning }
        _ = revoked.agreeAndStartCollection(for: revoked.collectionApprovalRunID, checked: true)
        try await wait(10, "collection") { revoked.phase == .selecting }
        revoked.prepareWorkspace()
        try await wait(10, "scan") { revoked.workspace != nil }
        let kept = revoked.selectedIDs
        revoked.reviewWorkspace(continueToAccount: true)
        try await wait(10, "link") { revoked.phase == .reviewing && !revoked.pairCode.isEmpty }
        revoked.requestSelectionChange()
        try await wait(5, "revocation") { revoked.handoffID.isEmpty && !revoked.selectionChangePending }
        try require(revoked.selectedIDs == kept && revoked.handoffNotice.contains("Continue to account") &&
                    revoked.canEditWorkspace, "Change selected contacts closes the link and keeps the review editable")
        revoked.disconnect()
        try await wait(5, "discard") { !revoked.helperRunning && revoked.phase == .idle }
    }
}
