import Foundation
import Darwin

private enum RecoveryCheckError: Error {
    case failed(String)
}

@main
@MainActor
struct CollectorModelChecks {
    private static func require(_ condition: @autoclosure () -> Bool, _ reason: String) throws {
        guard condition() else { throw RecoveryCheckError.failed(reason) }
    }

    private static func event(_ json: String) throws -> AgentEvent {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(AgentEvent.self, from: Data(json.utf8))
    }

    static func main() async {
        do {
            try runChecks()
            try await approvedLaunchUsesExactRun()
            print("15 macOS recovery and local-approval checks passed")
        } catch {
            FileHandle.standardError.write(Data("macOS recovery check failed: \(error)\n".utf8))
            Darwin.exit(1)
        }
    }

    private static func runChecks() throws {
        try uncheckedCollectionCannotStart()
        try explicitApprovalBindsFreshRun()
        try uncheckedAndStaleApprovalFailClosed()
        try declineStartsFreshUncheckedRun()
        try directLaunchRequiresApproval()
        try storageRecoveryInvalidatesApproval()
        try terminalEventsInvalidateApproval()
        try residueInspectionDoesNotRequireConsent()
        try storageReasons()
        try timeoutReasons()
        try cleanupRequiresInspection()
        try emptyResidueClearsGate()
        try inspectedResidueBlocksConnection()
        try cancellationClearsPrivateReview()
    }

    private static func uncheckedCollectionCannotStart() throws {
        let model = CollectorModel()
        model.connect()
        try require(model.collectionStartedAt == nil, "Unchecked collection must stop before a capture attempt")
        try require(!model.helperRunning && model.phase == .idle, "Unchecked collection cannot launch or advance the collector")
        model.disconnect()
        model.connect()
        try require(model.collectionStartedAt == nil, "A fresh retry cannot infer approval from an earlier attempt")
    }

    private static func explicitApprovalBindsFreshRun() throws {
        let model = CollectorModel()
        let run = model.collectionApprovalRunID
        let before = Date()
        try require(model.canPrepareCollection && !model.canConnect, "A fresh app must be ready to ask, not already approved")
        try require(model.approveLocalCollection(for: run, checked: true), "Explicit checked agreement should approve only the prepared run")
        guard let receipt = model.localCollectionApproval else { throw RecoveryCheckError.failed("Explicit agreement needs a local receipt") }
        try require(receipt.runID == run && receipt.disclosureVersion == CollectorModel.collectionDisclosureVersion, "Approval must bind the actual current run and disclosure")
        try require(receipt.approvedAt >= before && receipt.approvedAt <= Date(), "Approval timestamp must be operator-action time")
        try require(receipt.scopes == ["contacts", "call-context", "message-context", "temporary-full-backup"], "Local approval scopes must describe the actual local collection")
        try require(model.canConnect && !model.helperRunning && model.collectionStartedAt == nil, "Agree unlocks Connect; it must not collect automatically")
        try require(model.pairCode.isEmpty && !model.handoffAcknowledged, "Local collection approval cannot approve browser transfer or account save")
    }

    private static func uncheckedAndStaleApprovalFailClosed() throws {
        let model = CollectorModel()
        let oldRun = model.collectionApprovalRunID
        try require(!model.approveLocalCollection(for: oldRun, checked: false), "Unchecked approval cannot create a receipt")
        try require(model.localCollectionApproval == nil && !model.canConnect, "Unchecked approval cannot unlock collection")
        try require(!model.approveLocalCollection(for: oldRun, checked: true), "An invalidated run cannot receive late agreement")
        try require(!model.approveLocalCollection(for: UUID(), checked: true), "An unrelated run cannot receive agreement")
        try require(model.localCollectionApproval == nil && !model.helperRunning, "Stale agreement must not launch or synthesize approval")
    }

    private static func declineStartsFreshUncheckedRun() throws {
        let model = CollectorModel()
        let run = model.collectionApprovalRunID
        try require(model.approveLocalCollection(for: run, checked: true), "Fixture agreement must be explicit")
        model.declineLocalCollection()
        try require(model.collectionApprovalRunID != run && model.localCollectionApproval == nil && !model.canConnect, "Decline or Back must invalidate the old agreement")
        model.connect()
        try require(model.collectionStartedAt == nil && !model.helperRunning, "Decline cannot collect or reuse its old receipt")
    }

    private static func directLaunchRequiresApproval() throws {
        let model = CollectorModel()
        model.phase = .connecting
        model.launch(mode: "connect")
        try require(!model.helperRunning && model.operationID == nil, "The actual launch seam must reject programmatic unapproved capture")
        try require(model.localCollectionApproval == nil && model.errorMessage.contains("local collection"), "Launch must explain missing local approval, not probe the helper")
    }

    private static func storageRecoveryInvalidatesApproval() throws {
        let model = CollectorModel()
        let run = model.collectionApprovalRunID
        try require(model.approveLocalCollection(for: run, checked: true), "Fixture agreement must be explicit")
        model.receive(try event(#"{"kind":"error","code":"workspace_cleanup","cleanupRequired":true}"#))
        try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run, "Cleanup failure must invalidate pending approval")
        model.receive(try event(#"{"kind":"residue","sessions":[]}"#))
        try require(model.canPrepareCollection && !model.canConnect, "Successful inspection cannot restore an old approval")
        try require(!model.approveLocalCollection(for: run, checked: true), "The pre-recovery receipt cannot be reused")
    }

    private static func terminalEventsInvalidateApproval() throws {
        for state in ["cancelled", "disconnected"] {
            let model = CollectorModel()
            let run = model.collectionApprovalRunID
            try require(model.approveLocalCollection(for: run, checked: true), "Fixture agreement must be explicit")
            model.receive(try event(#"{"kind":"state","state":"\#(state)"}"#))
            try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run && !model.canConnect, "Terminal helper state cannot retain local approval")
        }
        let model = CollectorModel()
        let run = model.collectionApprovalRunID
        try require(model.approveLocalCollection(for: run, checked: true), "Fixture agreement must be explicit")
        model.finished(mode: "connect", code: 1)
        try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run && !model.canConnect, "Helper completion cannot reuse pending approval")
        try require(model.approveLocalCollection(for: model.collectionApprovalRunID, checked: true), "Retry needs a new explicit agreement")
        model.disconnect()
        try require(model.localCollectionApproval == nil && !model.canConnect, "Explicit cancellation or restart must start unchecked")
    }

    private static func residueInspectionDoesNotRequireConsent() throws {
        let model = CollectorModel()
        model.inspectResidue()
        try require(model.needsStorageReview && !model.canConnect, "Recovery inspection remains available without agreeing to collection")
        try require(!model.errorMessage.contains("local collection"), "Inspection must reach its normal helper boundary, not an approval gate")
        try require(model.localCollectionApproval == nil, "Inspection must not grant approval")
    }

    private static func approvedLaunchUsesExactRun() async throws {
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        guard let resources = Bundle.main.resourceURL?.resolvingSymlinksInPath(),
              resources == executable.deletingLastPathComponent(),
              resources.path.hasPrefix(FileManager.default.temporaryDirectory.resolvingSymlinksInPath().path + "/") else {
            throw RecoveryCheckError.failed("Synthetic helper fixture requires the isolated check executable directory")
        }
        let fixture = resources.appendingPathComponent("helper")
        try FileManager.default.createDirectory(at: fixture, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: fixture) }
        let helper = fixture.appendingPathComponent("amplifai-agent")
        let source = "#!/bin/sh\nif [ \"$1\" = \"connect\" ]; then\n IFS= read -r command\nelse\n printf '%s\\n' '{\"kind\":\"residue\",\"sessions\":[]}'\nfi\n"
        try Data(source.utf8).write(to: helper, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)

        let model = CollectorModel()
        let run = model.collectionApprovalRunID
        try require(model.approveLocalCollection(for: run, checked: true), "Synthetic helper launch requires actual explicit approval")
        model.connect()
        try require(model.helperRunning && model.operationID == run, "The actual Process launch must use the approved run UUID")
        try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run, "Launching must consume, not recycle, the receipt")
        model.disconnect()
        let deadline = Date().addingTimeInterval(3)
        while model.helperRunning && Date() < deadline { await Task.yield() }
        try require(!model.helperRunning, "The owned synthetic helper and subsequent inspection must drain")
        try require(model.localCollectionApproval == nil && !model.canConnect, "Cancellation cannot restore approval after actual helper exit")
        let started = model.collectionStartedAt
        model.connect()
        try require(!model.helperRunning && model.collectionStartedAt == started, "A fresh retry cannot capture without another agreement")
    }

    private static func storageReasons() throws {
        let codes = [
            "workspace_low_space", "workspace_size_limit",
            "workspace_unsafe", "workspace_unavailable",
        ]
        let messages = codes.map { CollectorModel.message(for: $0) }
        try require(Set(messages).count == codes.count, "Storage failures need distinct recovery messages")
        try require(messages[0].contains("2 GiB"), "Low-space recovery must name the actual 2 GiB limit")
        try require(messages[1].contains("1 GiB"), "Size recovery must name the actual 1 GiB limit")
        try require(!messages[2].contains("2 GiB"), "Unsafe ownership must not be described as low space")
        try require(!messages[3].contains("2 GiB"), "Storage access failure must not be described as low space")

        for code in codes {
            let model = CollectorModel()
            model.receive(try event(#"{"kind":"error","code":"\#(code)"}"#))
            try require(model.phase == .error, "Storage event must enter its error state")
            try require(model.errorMessage == CollectorModel.message(for: code), "Storage event lost its recovery reason")
        }
    }

    private static func timeoutReasons() throws {
        let connection = CollectorModel.message(for: "connection_timeout").lowercased()
        let stalled = CollectorModel.message(for: "backup_stalled").lowercased()
        let elapsed = CollectorModel.message(for: "backup_time_limit").lowercased()

        try require(Set([connection, stalled, elapsed]).count == 3, "Connection, stall and elapsed failures need distinct messages")
        for obsoleteLimit in ["one hour", "1 hour", "1h"] {
            try require(!connection.contains(obsoleteLimit), "Connection timeout cannot claim a one-hour backup")
        }
        try require(stalled.range(of: #"15\s*min"#, options: .regularExpression) != nil, "Stall recovery must name the 15-minute policy")
        try require(elapsed.range(of: #"(4|four)[\s-]*hour"#, options: .regularExpression) != nil, "Elapsed recovery must name the four-hour policy")
    }

    private static func cleanupRequiresInspection() throws {
        let model = CollectorModel()
        try require(model.canPrepareCollection && !model.canConnect, "Idle model must allow a fresh approval, not unchecked collection")
        model.receive(try event(#"{"kind":"error","code":"workspace_low_space","cleanupRequired":true}"#))

        try require(model.needsStorageReview, "Cleanup failure must require storage inspection")
        try require(model.residues.isEmpty, "Cleanup event should not invent a residue receipt")
        try require(!model.canConnect, "An uninspected cleanup failure must block connection")
        try require(!model.canPrepareCollection, "An uninspected cleanup failure must block new approval")
        try require(model.errorMessage.hasPrefix(CollectorModel.message(for: "workspace_low_space")), "Cleanup failure must preserve the primary storage reason")
        try require(model.errorMessage.contains("Temporary phone data may remain. Check temporary data before reconnecting."), "Cleanup failure must explain the required temporary-data recovery")
    }

    private static func emptyResidueClearsGate() throws {
        let model = CollectorModel()
        model.receive(try event(#"{"kind":"error","code":"workspace_cleanup","cleanupRequired":true}"#))
        try require(!model.canConnect, "Cleanup failure must block before inspection")
        model.receive(try event(#"{"kind":"residue","sessions":[]}"#))

        try require(!model.needsStorageReview, "An empty inspection receipt must clear the storage review gate")
        try require(model.residues.isEmpty, "Empty inspection must contain no residue")
        try require(model.canPrepareCollection && !model.canConnect, "An empty verified inspection permits a fresh unchecked retry")
        try require(model.approveLocalCollection(for: model.collectionApprovalRunID, checked: true) && model.canConnect, "Retry after inspection requires explicit fresh approval")
    }

    private static func inspectedResidueBlocksConnection() throws {
        let model = CollectorModel()
        model.receive(try event(#"{"kind":"error","code":"workspace_cleanup","cleanupRequired":true}"#))
        model.receive(try event(#"{"kind":"residue","sessions":[{"name":"session-00000000000000000000000000000000","bytes":8}]}"#))

        try require(model.residues.count == 1, "Inspection receipt must retain the existing residue")
        try require(!model.canConnect, "Existing residue must block a new connection")
        try require(!model.canPrepareCollection, "Existing residue must block a new approval")
    }

    private static func cancellationClearsPrivateReview() throws {
        let model = CollectorModel()
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic fixture","phone_count":1,"phone_ends":["0000"]}],"available_calls":2,"available_messages":3}"#))
        try require(model.contacts.first?.phoneCount == 1, "Snake-case contact protocol must decode")
        model.selectedIDs = [7]
        model.password = "synthetic fixture"
        model.receive(try event(#"{"kind":"pairing","pairCode":"0000000000","port":48751}"#))
        model.receive(try event(#"{"kind":"state","state":"cancelled"}"#))

        try require(model.phase == .idle, "Cancellation must return to idle")
        try require(model.contacts.isEmpty, "Cancellation must clear contacts")
        try require(model.selectedIDs.isEmpty, "Cancellation must clear selected contacts")
        try require(model.password.isEmpty, "Cancellation must clear the transient password")
        try require(model.pairCode.isEmpty, "Cancellation must clear browser pairing")
    }
}
