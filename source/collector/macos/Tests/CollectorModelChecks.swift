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
        try AgentEvent.decode(Data(json.utf8))
    }

    static func main() async {
        do {
            try runChecks()
            try await approvedLaunchUsesExactRun()
            print("macOS recovery, progress, connection freshness, run-approval and update-deferral checks passed")
            if CommandLine.arguments.dropFirst().first != "--quick-only" {
                guard CommandLine.arguments.count == 3 else {
                    throw RecoveryCheckError.failed("Default checks require the pinned Python interpreter and collector source path")
                }
                try await AuthorityRefreshIntegrationChecks.run(python: CommandLine.arguments[1],
                    collector: URL(fileURLWithPath: CommandLine.arguments[2]).resolvingSymlinksInPath())
            }
        } catch {
            FileHandle.standardError.write(Data("macOS recovery check failed: \(error)\n".utf8))
            Darwin.exit(1)
        }
    }

    private static func runChecks() throws {
        try uncheckedCollectionCannotStart()
        try explicitApprovalBindsFreshRun()
        try checkedBackupSettingGatesCallCoverageChoice()
        try uncheckedAndStaleApprovalFailClosed()
        try declineStartsFreshUncheckedRun()
        try directLaunchRequiresApproval()
        try storageRecoveryInvalidatesApproval()
        try terminalEventsInvalidateApproval()
        try residueInspectionDoesNotRequireConsent()
        try storageReasons()
        try encryptionPreflightReasons()
        try timeoutReasons()
        try deviceFailureIsActionableWithoutPrivateDetails()
        try automaticDiagnosticUsesOnlySafeCode()
        try cleanupRequiresInspection()
        try emptyResidueClearsGate()
        try inspectedResidueBlocksConnection()
        try cancellationClearsPrivateReview()
        try backupEstimateRejectsPercentageOnly()
        try backupEstimateNeedsStableMeasuredProgress()
        try activityTimestampNeedsRealDataProgress()
        try confirmedConnectionAdvancesToBackupWait()
        try connectionEvidenceRequiresCurrentKnownEvent()
        try connectionAndBackupFreshnessRequireNewEvidence()
        try updaterCannotReplaceActiveOrUnsavedWork()
    }

    private static func confirmedConnectionAdvancesToBackupWait() throws {
        let model = CollectorModel()
        let connectedAt = Date(timeIntervalSince1970: 1_800_000_000)
        model.now = { connectedAt }
        model.phase = .connecting
        model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
        model.receive(try event(#"{"kind":"connection","transport":"usb"}"#))
        try require(model.phase == .transferring && model.connectionTransport == "USB",
                    "A confirmed USB connection must advance to Receive backup without waiting for file bytes")
        try require(model.connectionConfirmedAt == connectedAt,
                    "The connection confirmation needs its own timestamp")
        try require(!model.transferIsKnown && !model.progressIsKnown && model.lastCollectionActivityAt == nil,
                    "A connection confirmation must not invent backup progress")
    }

    private static func connectionEvidenceRequiresCurrentKnownEvent() throws {
        for json in [#"{"kind":"connection"}"#, #"{"kind":"connection","transport":"unknown"}"#,
                     #"{"kind":"connection","transport":"USB"}"#] {
            let model = CollectorModel()
            model.phase = .connecting
            model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
            model.receive(try event(json))
            try require(model.phase == .connecting && model.connectionTransport.isEmpty && model.connectionConfirmedAt == nil,
                        "An absent or unknown transport cannot be presented as a confirmed USB connection")
        }
        for phase: CollectorModel.Phase in [.idle, .error, .cancelling, .processing, .reviewing] {
            let model = CollectorModel()
            model.phase = phase
            model.receive(try event(#"{"kind":"connection","transport":"usb"}"#))
            try require(model.phase == phase && model.connectionConfirmedAt == nil,
                        "A late connection frame cannot advance a settled, stopping or later-stage model")
        }
        let model = CollectorModel()
        model.phase = .connecting
        model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
        model.handleHelperOutput(Data(#"{"kind":"connection","transport":"usb"}"#.utf8) + Data([10]),
                                 operation: UUID(), mode: "connect")
        try require(model.phase == .connecting && model.connectionConfirmedAt == nil,
                    "An old helper operation cannot confirm the current phone connection")
        model.receive(try event(#"{"kind":"connection","transport":"wifi"}"#))
        try require(model.phase == .transferring && model.connectionTransport == "Wi-Fi",
                    "A valid trusted Wi-Fi event must retain its actual transport")
        try require(model.localCollectionApproval == nil && model.phoneRun == nil && !model.canConnect,
                    "Connection presentation must not create collection or retry authority")
    }

    private static func connectionAndBackupFreshnessRequireNewEvidence() throws {
        let model = CollectorModel()
        var clock = Date(timeIntervalSince1970: 1_800_000_000)
        model.now = { clock }
        model.phase = .connecting
        model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
        let started = clock
        try require(model.collectionAttention(at: started.addingTimeInterval(59.999)) == nil,
                    "A new connection attempt should have a bounded waiting interval")
        clock = started.addingTimeInterval(60)
        model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
        try require(model.collectionStartedAt == started &&
                    model.collectionAttention(at: clock) == .connectionUnconfirmed,
                    "Repeated connecting frames cannot renew the connection-check freshness")
        model.receive(try event(#"{"kind":"connection","transport":"usb"}"#))
        let confirmed = clock
        try require(model.collectionAttention(at: confirmed.addingTimeInterval(59.999)) == nil,
                    "A new confirmed connection starts the backup-wait observation interval")
        clock = confirmed.addingTimeInterval(60)
        model.receive(try event(#"{"kind":"connection","transport":"wifi"}"#))
        model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
        try require(model.phase == .transferring && model.connectionConfirmedAt == confirmed &&
                    model.connectionTransport == "USB" && model.collectionAttention(at: clock) == .backupWaiting,
                    "Repeated connection/state frames cannot regress the stage, change transport or hide stale backup wait")
        model.receive(try event(#"{"kind":"progress","value":10}"#))
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":0,"retainedBytes":0,"discardedBytes":0,"filesReceived":0,"elapsedSeconds":60,"bytesPerSecond":0}"#))
        try require(model.lastBackupActivityAt == nil && model.collectionAttention(at: clock) == .backupWaiting,
                    "A device percentage or zero counters cannot clear missing-file-data attention")
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":1024,"retainedBytes":512,"discardedBytes":512,"filesReceived":1,"elapsedSeconds":61,"bytesPerSecond":16}"#))
        let measured = clock
        try require(model.lastBackupActivityAt == measured && model.connectionConfirmedAt == confirmed &&
                    model.collectionAttention(at: measured.addingTimeInterval(59.999)) == nil,
                    "Real backup progress clears backup attention without rewriting connection evidence")
        clock = measured.addingTimeInterval(60)
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":1024,"retainedBytes":512,"discardedBytes":512,"filesReceived":1,"elapsedSeconds":121,"bytesPerSecond":8}"#))
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":512,"retainedBytes":256,"discardedBytes":256,"filesReceived":1,"elapsedSeconds":122,"bytesPerSecond":4}"#))
        try require(model.lastBackupActivityAt == measured && model.collectionAttention(at: clock) == .backupStale,
                    "Repeated or regressed counters cannot make stalled data progress look fresh")
        model.phase = .passwordRequired
        try require(model.collectionAttention(at: clock) == nil,
                    "Backup attention must not override the explicit password-required state")
        model.phase = .cancelling
        try require(model.collectionAttention(at: clock) == nil &&
                    model.statusTitle == "Stopping and checking temporary data",
                    "Freshness presentation must not replace truthful pending Stop state")
        model.phase = .error
        try require(model.collectionAttention(at: clock) == nil,
                    "A terminal failure keeps its specific recovery state")
    }

    private static func updaterCannotReplaceActiveOrUnsavedWork() throws {
        let model = CollectorModel()
        try require(model.canInstallUpdate, "An untouched idle app may install a verified update")
        for phase: CollectorModel.Phase in [.connecting, .transferring, .passwordRequired,
                                            .processing, .selecting, .reviewing, .completing, .cancelling] {
            model.phase = phase
            try require(!model.canInstallUpdate, "An active local or browser phase cannot install an update")
        }
        model.phase = .error
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":1,"name":"Ada","phoneCount":1,"phoneEnds":["4567"]}],"totalContacts":1,"nextCursor":null,"availableCalls":0,"availableMessages":0}"#))
        model.phase = .error
        try require(model.hasUnsavedReview && !model.canInstallUpdate,
                    "An error must preserve the unsaved review before allowing replacement")
        model.phase = .completed
        try require(!model.canInstallUpdate,
                    "A completed phase without a bound saved ACK cannot permit replacement")
        model.disconnect()
        try require(model.canInstallUpdate,
                    "After explicit discard and cleanup, a settled app may install")
    }

    @MainActor private final class BackupEstimateFixture {
        let model = CollectorModel()
        var uptime: TimeInterval = 1000
        var wall = Date(timeIntervalSince1970: 1_800_000_000)
        var bytes: Int64 = 0
        init() {
            model.phase = .transferring
            model.monotonicNow = { [weak self] in self?.uptime ?? 0 }
            model.now = { [weak self] in self?.wall ?? Date() }
        }
        func pulse(at time: TimeInterval, percent: Double? = nil, addBytes: Bool = true, filesOnly: Bool = false) throws {
            uptime = time
            if addBytes { bytes += 1024 }
            let files = filesOnly ? Int(time) : Int(bytes / 1024)
            model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":\#(bytes),"retainedBytes":0,"discardedBytes":\#(bytes),"filesReceived":\#(files),"elapsedSeconds":1,"bytesPerSecond":0}"#))
            if let percent { model.receive(try event(#"{"kind":"progress","value":\#(percent)}"#)) }
        }
        func steady() throws {
            let start = uptime
            for step in 0...4 {
                try pulse(at: start + Double(step) * 30, percent: step.isMultiple(of: 2) ? 20 + Double(step) * 2.5 : nil)
                if step < 4 {
                    try require(model.backupRemainingEstimate() == nil, "Early or fewer than three samples cannot create an ETA")
                }
            }
        }
    }

    private static func backupEstimateRejectsPercentageOnly() throws {
        for mode in ["percentage-only", "zero-bytes", "files-only"] {
            let fixture = BackupEstimateFixture()
            for step in 0...2 {
                fixture.uptime = 1000 + Double(step) * 60
                if mode != "percentage-only" {
                    try fixture.pulse(at: fixture.uptime, addBytes: false, filesOnly: mode == "files-only")
                }
                fixture.model.receive(try event(#"{"kind":"progress","value":\#(20 + step * 5)}"#))
            }
            try require(fixture.model.backupRemainingEstimate() == nil,
                        "\(mode) cannot fabricate an estimate without measured payload bytes")
        }
    }

    private static func backupEstimateNeedsStableMeasuredProgress() throws {
        let fixture = BackupEstimateFixture()
        try fixture.steady()
        guard let range = fixture.model.backupRemainingEstimate() else {
            throw RecoveryCheckError.failed("Three stable samples spanning 120 seconds with continuous payload bytes qualify a backup-only estimate")
        }
        try require(range.lowerBound > 0 && range.upperBound > range.lowerBound,
                    "A measured backup estimate must have a positive, visible range")
        for jump in [-86_400.0, 172_800.0] {
            fixture.wall = fixture.wall.addingTimeInterval(jump)
            try require(fixture.model.backupRemainingEstimate() == range, "Wall-clock changes cannot alter the measured rate")
        }
        fixture.uptime += 59.999
        try require(fixture.model.backupRemainingEstimate() == range, "Recent measured data permits the existing bounded range")
        fixture.uptime = 1180
        try require(fixture.model.backupRemainingEstimate() == nil, "Exactly sixty seconds without payload bytes withdraws the estimate")
        try fixture.pulse(at: 1180, percent: 35)
        try require(fixture.model.backupRemainingEstimate() == nil, "Resumed bytes cannot revive the pre-stall rate")
        try fixture.pulse(at: 1210)
        try fixture.pulse(at: 1240, percent: 40)
        try fixture.pulse(at: 1270)
        try require(fixture.model.backupRemainingEstimate() == nil, "Two fresh samples after a stall are insufficient")
        try fixture.pulse(at: 1300, percent: 45)
        try require(fixture.model.backupRemainingEstimate() != nil, "A new measured 120-second window can qualify after resume")

        for interruption in ["unknown", "regression", "clock-reset", "clock-invalid", "clock-forward-back", "disconnect"] {
            let check = BackupEstimateFixture()
            try check.steady()
            if interruption == "unknown" { check.model.receive(try event(#"{"kind":"progress"}"#)) }
            if interruption == "regression" { check.model.receive(try event(#"{"kind":"progress","value":25}"#)) }
            if interruption == "clock-reset" { check.uptime = 0 }
            if interruption == "clock-invalid" { check.uptime = .nan }
            if interruption == "clock-forward-back" { check.uptime += 86_400 }
            if interruption == "disconnect" { check.model.disconnect() }
            try require(check.model.backupRemainingEstimate() == nil, "\(interruption) withdraws the old estimate")
            check.uptime = 1120
            check.model.phase = .transferring
            check.model.receive(try event(#"{"kind":"progress","value":30}"#))
            try require(check.model.backupRemainingEstimate() == nil, "\(interruption) cannot restore the former rate from one percentage")
        }
        for percentages in [[20.0, 21, 40], [20, 21, 23], [5, 10, 14], [80, 85, 90]] {
            let check = BackupEstimateFixture()
            for step in 0...4 {
                try check.pulse(at: 1000 + Double(step) * 30,
                                percent: step.isMultiple(of: 2) ? percentages[step / 2] : nil)
            }
            try require(check.model.backupRemainingEstimate() == nil,
                        "Unstable rates, low gain, early progress and final-tail uncertainty cannot create an estimate")
        }
        for phase: CollectorModel.Phase in [.processing, .reviewing, .completing, .completed, .cancelling] {
            fixture.model.phase = phase
            try require(fixture.model.backupRemainingEstimate() == nil,
                        "Backup estimates do not claim remaining processing, saving or Stop time")
        }
    }

    private static func activityTimestampNeedsRealDataProgress() throws {
        let model = CollectorModel()
        model.phase = .transferring
        try require(model.lastCollectionActivityAt == nil, "No file bytes cannot look like observed data progress")
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":1024,"retainedBytes":512,"discardedBytes":512,"filesReceived":1,"elapsedSeconds":2,"bytesPerSecond":512}"#))
        guard let backupActivity = model.lastCollectionActivityAt else {
            throw RecoveryCheckError.failed("An increasing file transfer should record real activity")
        }
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":1024,"retainedBytes":512,"discardedBytes":512,"filesReceived":1,"elapsedSeconds":3,"bytesPerSecond":341}"#))
        try require(model.lastCollectionActivityAt == backupActivity, "Repeated counters cannot refresh last data activity")
        model.receive(try event(#"{"kind":"transfer","stage":"processing","processedBytes":4096}"#))
        try require(model.lastParsingActivityAt != nil && model.lastCollectionActivityAt == model.lastParsingActivityAt,
                    "Local parsing must report its own measured activity")
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

    /// A model whose one-approval run was claimed through the production client and a simulated server.
    private static func approvedModel(commandWriter: ((Data) -> Void)? = nil) throws -> (CollectorModel, SimulatedPhoneRunServer) {
        let server = SimulatedPhoneRunServer()
        let model = CollectorModel(commandWriter: commandWriter,
                                   runAuthority: PhoneRunAuthorityClient(transport: server, schedule: PhoneRunAuthorityClient.immediate))
        let run = server.createRun()
        guard let link = PhoneRunLink(url: server.appLink(run)), model.admitRunLink(PendingRunLink(link: link, returnBrowserBundleID: nil)),
              model.runApproval == .approved else { throw RecoveryCheckError.failed("Synthetic run claim must succeed") }
        return (model, server)
    }

    private static func explicitApprovalBindsFreshRun() throws {
        let (model, _) = try approvedModel()
        let run = model.collectionApprovalRunID
        let before = Date()
        try require(model.canPrepareCollection && !model.canConnect, "A verified run is ready to start, not already approved locally")
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage),
                    "No local approval before the read-only iPhone check")
        try observeBackupSetting(model, encrypted: true)
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .confirmedEncryptedBackup),
                    "The verified run composes this run's local approval")
        guard let receipt = model.localCollectionApproval, let server = model.phoneRun else {
            throw RecoveryCheckError.failed("The composed approval needs a local receipt")
        }
        try require(receipt.runID == run && receipt.disclosureVersion == CollectorModel.collectionDisclosureVersion &&
                    receipt.disclosureVersion == "native-run-v1", "Approval must bind the current local run and the run disclosure")
        try require(receipt.serverRunID == server.runID && receipt.serverApprovalID == server.claim.approvalID &&
                    receipt.serverDisclosureVersion == "phone-run-v2" && receipt.serverRunID != run.uuidString.lowercased(),
                    "The local approval records the distinct server run and its disclosure")
        try require(receipt.approvedAt >= before && receipt.approvedAt <= Date(), "Approval timestamp must be action time")
        try require(receipt.scopes == ["contacts", "call-context", "message-context", "temporary-full-backup", "sanitized-failure-diagnostic"], "Local approval scopes must describe collection and its bounded automatic failure report")
        try require(receipt.callHistoryPreparation == .confirmedEncryptedBackup, "The checked call-history coverage is bound")
        try require(model.canConnect && !model.helperRunning && model.collectionStartedAt == nil, "Composing alone does not collect")
        try require(model.pairCode.isEmpty && !model.handoffAcknowledged, "Local collection approval cannot pair or save")
    }

    private static func observeBackupSetting(_ model: CollectorModel, encrypted: Bool) throws {
        try require(model.beginBackupEncryptionStatusCheck(), "A fresh owner action must begin the read-only setting check")
        model.receive(try event("{\"kind\":\"backup-encryption-status\",\"backupEncrypted\":\(encrypted)}"))
        model.refreshRunStatus()
    }

    private static func checkedBackupSettingGatesCallCoverageChoice() throws {
        let (model, _) = try approvedModel()
        for invalid in ["0", "1", "\"true\""] {
            try require((try? event("{\"kind\":\"backup-encryption-status\",\"backupEncrypted\":\(invalid)}")) == nil,
                        "Numeric and string values cannot bridge into a literal JSON backup-setting Bool")
        }
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .confirmedEncryptedBackup),
                    "Owner attestation alone cannot substitute for the read-only setting check")
        try require(model.beginBackupEncryptionStatusCheck(), "A current precollection check should start")
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage),
                    "A pending check cannot be treated as known coverage")
        model.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":false}"#))
        try require(model.backupEncryptionStatus == .unencrypted, "Literal false must be shown as encryption off")
        model.refreshRunStatus()
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .confirmedEncryptedBackup),
                    "A false phone setting cannot authorize encrypted-call collection")
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage),
                    "Available data may be collected when encryption is off")
        model.declineLocalCollection()
        try require(model.backupEncryptionStatus == .notChecked, "Back must invalidate the old phone reading")
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage),
                    "The next attempt cannot reuse the previous phone setting")
        try require(model.beginBackupEncryptionStatusCheck(), "The new attempt must recheck")
        model.receive(try event(#"{"kind":"error","code":"backup_encryption_unknown"}"#))
        try require(model.backupEncryptionStatus == .unknown && model.backupEncryptionCheckCode == "backup_encryption_unknown",
                    "An unreadable setting must remain unknown, not false or approved")
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .confirmedEncryptedBackup),
                    "Unknown cannot authorize expected encrypted Calls")
    }

    private static func uncheckedAndStaleApprovalFailClosed() throws {
        let model = CollectorModel()
        try observeBackupSetting(model, encrypted: false)
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage) &&
                    model.localCollectionApproval == nil && !model.canConnect,
                    "Without a verified run there is no collection approval, old or new")
        let (approved, _) = try approvedModel()
        approved.dismissRunApproval()
        try observeBackupSetting(approved, encrypted: false)
        try require(!approved.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage) &&
                    !approved.helperRunning, "A dismissed run cannot receive a late approval")
    }

    private static func declineStartsFreshUncheckedRun() throws {
        let (model, _) = try approvedModel()
        let run = model.collectionApprovalRunID
        try observeBackupSetting(model, encrypted: false)
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage), "Fixture approval must compose")
        model.declineLocalCollection()
        try require(model.collectionApprovalRunID != run && model.localCollectionApproval == nil && !model.canConnect, "Decline or Back must invalidate the old approval")
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
        let (model, _) = try approvedModel()
        let run = model.collectionApprovalRunID
        try observeBackupSetting(model, encrypted: false)
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage), "Fixture approval must compose")
        model.receive(try event(#"{"kind":"error","code":"workspace_cleanup","cleanupRequired":true}"#))
        try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run, "Cleanup failure must invalidate pending approval")
        model.receive(try event(#"{"kind":"residue","sessions":[]}"#))
        try require(model.canPrepareCollection && !model.canConnect, "Successful inspection cannot restore an old approval")
        try require(model.localCollectionApproval == nil && !model.canConnect, "The pre-recovery receipt cannot be reused")
    }

    private static func terminalEventsInvalidateApproval() throws {
        for state in ["cancelled", "disconnected"] {
            let (model, _) = try approvedModel()
            let run = model.collectionApprovalRunID
            try observeBackupSetting(model, encrypted: false)
            try require(model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage), "Fixture approval must compose")
            model.receive(try event(#"{"kind":"state","state":"\#(state)"}"#))
            try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run && !model.canConnect, "Terminal helper state cannot retain local approval")
        }
        let (model, _) = try approvedModel()
        let run = model.collectionApprovalRunID
        try observeBackupSetting(model, encrypted: false)
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage), "Fixture approval must compose")
        model.finished(mode: "connect", code: 1)
        try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run && !model.canConnect, "Helper completion cannot reuse pending approval")
        try observeBackupSetting(model, encrypted: false)
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage), "An unstarted run composes a fresh approval for its retry")
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
        let source = "#!/bin/sh\nif [ \"$1\" = \"connect\" ]; then\n IFS= read -r command\nelif [ \"$1\" = \"backup-encryption-status\" ]; then\n /bin/sleep 0.2\n printf '%s\\n' '{\"kind\":\"backup-encryption-status\",\"backupEncrypted\":true}'\nelse\n printf '%s\\n' '{\"kind\":\"residue\",\"sessions\":[]}'\nfi\n"
        try Data(source.utf8).write(to: helper, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)

        let settingModel = CollectorModel()
        settingModel.checkBackupEncryptionStatus()
        guard let firstCheck = settingModel.operationID else {
            throw RecoveryCheckError.failed("The read-only setting check must launch a distinct owned helper")
        }
        let firstDeadline = Date().addingTimeInterval(3)
        while settingModel.helperRunning && Date() < firstDeadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        try require(!settingModel.helperRunning && settingModel.backupEncryptionStatus == .encrypted,
                    "A clean literal-bool check may show On only after helper exit")
        settingModel.checkBackupEncryptionStatus()
        guard let secondCheck = settingModel.operationID else {
            throw RecoveryCheckError.failed("Recheck must launch another owned helper")
        }
        try require(secondCheck != firstCheck, "Recheck cannot reuse the consent run as its process callback token")
        settingModel.handleHelperOutput(
            Data(#"{"kind":"backup-encryption-status","backupEncrypted":false}"#.utf8) + Data([10]),
            operation: firstCheck, mode: "backup-encryption-status"
        )
        settingModel.handleHelperTermination(1, operation: firstCheck, mode: "backup-encryption-status")
        try require(settingModel.operationID == secondCheck && settingModel.helperRunning &&
                    settingModel.backupEncryptionStatus == .checking,
                    "Old output and old termination must not settle or tear down the new check")
        let secondDeadline = Date().addingTimeInterval(3)
        while settingModel.helperRunning && Date() < secondDeadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        try require(settingModel.backupEncryptionStatus == .encrypted,
                    "Only the current clean helper result may qualify an encrypted choice")

        let abnormal = CollectorModel()
        try require(abnormal.beginBackupEncryptionStatusCheck(), "Synthetic abnormal check must start")
        abnormal.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#))
        abnormal.finished(mode: "backup-encryption-status", code: 1)
        try require(abnormal.backupEncryptionStatus == .unknown && !abnormal.canConnect,
                    "A status frame followed by abnormal helper exit cannot qualify Calls")

        let failedCheck = CollectorModel()
        try require(failedCheck.beginBackupEncryptionStatusCheck(), "Synthetic check failure must start")
        failedCheck.receive(try event(#"{"kind":"error","code":"workspace_low_space"}"#))
        failedCheck.finished(mode: "backup-encryption-status", code: 1)
        try require(failedCheck.backupEncryptionStatus == .unknown &&
                    failedCheck.backupEncryptionCheckCode == "workspace_low_space" &&
                    !failedCheck.canConnect,
                    "A bounded error frame must survive its normal nonzero helper exit")

        let cancelledCheck = CollectorModel()
        cancelledCheck.checkBackupEncryptionStatus()
        guard let cancelledID = cancelledCheck.operationID else {
            throw RecoveryCheckError.failed("Synthetic cancellation requires a live read-only helper")
        }
        try require(cancelledCheck.backupSettingCheckInProgress,
                    "A running setting check must remain visible and cancellable")
        cancelledCheck.cancelBackupEncryptionStatusCheck()
        try require(cancelledCheck.phase == .cancelling && cancelledCheck.backupSettingCheckInProgress &&
                    cancelledCheck.journeyStep == 0,
                    "Cancelling the pre-agreement iPhone check must stay on Agree, not mark it finished")
        let cancelDeadline = Date().addingTimeInterval(3)
        while cancelledCheck.helperRunning && Date() < cancelDeadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        cancelledCheck.handleHelperOutput(
            Data(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#.utf8) + Data([10]),
            operation: cancelledID, mode: "backup-encryption-status"
        )
        try require(!cancelledCheck.helperRunning && cancelledCheck.phase == .idle &&
                    !cancelledCheck.canConnect && cancelledCheck.canPrepareCollection,
                    "Cancelling the read-only check must settle idle after residue inspection, not retain stale On")

        let (model, _) = try approvedModel()
        let run = model.collectionApprovalRunID
        try observeBackupSetting(model, encrypted: false)
        try require(model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage), "Synthetic helper launch requires the composed run approval")
        model.connect()
        try require(model.helperRunning && model.operationID == run, "The actual Process launch must use the approved local run UUID")
        try require(model.localCollectionApproval == nil && model.collectionApprovalRunID != run, "Launching must consume, not recycle, the receipt")
        model.disconnect()
        let deadline = Date().addingTimeInterval(3)
        while model.helperRunning && Date() < deadline { await Task.yield() }
        try require(!model.helperRunning, "The owned synthetic helper and subsequent inspection must drain")
        try require(model.localCollectionApproval == nil && !model.canConnect && model.phoneRun == nil,
                    "Cancellation ends the run; nothing restores approval after actual helper exit")
        let started = model.collectionStartedAt
        model.connect()
        try require(!model.helperRunning && model.collectionStartedAt == started, "A fresh retry cannot capture without another approval")

        // One Start checks the iPhone with the real helper path, revalidates status and launches exactly that run.
        let (single, singleServer) = try approvedModel()
        try require(single.startCollection(), "Start collection runs the read-only check")
        let checkDeadline = Date().addingTimeInterval(3)
        while (single.phase != .connecting || !single.helperRunning) && Date() < checkDeadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        let singleRun = single.operationID
        try require(single.helperRunning && single.phase == .connecting && single.journeyStep == 1 &&
                    singleServer.claimCount == 1 && singleServer.statusCount == 1 && single.phoneRun?.collectionStarted == true,
                    "After the check and current capability status, one Start launches the run and moves to Collect")
        try require(single.localCollectionApproval == nil && single.collectionApprovalRunID != singleRun,
                    "Starting consumes the one-run approval; nothing authorizes a later transfer")
        single.receive(try event(#"{"kind":"connection","transport":"usb"}"#))
        let connectionBeforeStop = single.connectionConfirmedAt
        single.disconnect()
        single.receive(try event(#"{"kind":"connection","transport":"wifi"}"#))
        try require(single.phase == .cancelling && single.helperRunning &&
                    single.connectionConfirmedAt == connectionBeforeStop &&
                    single.collectionAttention(at: Date().addingTimeInterval(600)) == nil,
                    "Stop must stay pending until the owned helper exits; late connection events cannot revive collection")
        let singleDeadline = Date().addingTimeInterval(3)
        while single.helperRunning && Date() < singleDeadline { await Task.yield() }
        try require(!single.helperRunning, "The synthetic collection helper must stop on Cancel")
        try require(!single.startCollection() && !single.helperRunning && single.phoneRun == nil,
                    "A consumed run cannot start another collection")

        // Actual Process lifecycle with the in-process authority: periodic status revocation stops the owned
        // synthetic helper, without a phone, account request, or a new collection permission.
        let (polled, pollingServer) = try approvedModel()
        polled.runStatusPollNanoseconds = 20_000_000
        try require(polled.startCollection(), "Polling fixture starts with verified authority")
        let pollStartDeadline = Date().addingTimeInterval(3)
        while polled.phoneRun?.collectionStarted != true && Date() < pollStartDeadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        guard let polledRun = polled.phoneRun?.runID else {
            throw RecoveryCheckError.failed("Synthetic polling collection did not start")
        }
        pollingServer.revoke(polledRun)
        let revokeDeadline = Date().addingTimeInterval(3)
        while (polled.phoneRun != nil || polled.helperRunning) && Date() < revokeDeadline {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        try require(pollingServer.statusCount >= 2 && polled.phoneRun == nil && !polled.helperRunning &&
                    polled.runStatus == .revoked && !polled.startCollection(),
                    "Periodic current-status polling terminates the owned helper on explicit revocation")
    }

    private static func storageReasons() throws {
        let codes = [
            "workspace_low_space", "workspace_size_limit",
            "workspace_unsafe", "workspace_unavailable", "source_capacity_limit",
            "backup_control_frame_limit", "backup_control_metadata_limit",
            "backup_control_path_limit", "contacts_capacity_limit", "source_read_capacity_limit",
        ]
        let messages = codes.map { CollectorModel.message(for: $0) }
        try require(Set(messages).count == codes.count, "Storage failures need distinct recovery messages")
        try require(messages[0].contains("2 GiB"), "Low-space recovery must name the actual 2 GiB limit")
        try require(messages[1].contains("older") && !messages[1].contains("1 GiB"), "Legacy helper recovery must request an update, not claim a current 1 GiB ceiling")
        try require(messages[4].contains("metadata count"), "Metadata capacity must be distinct from storage and format failures")
        try require(!messages[2].contains("2 GiB"), "Unsafe ownership must not be described as low space")
        try require(!messages[3].contains("2 GiB"), "Storage access failure must not be described as low space")

        for code in codes {
            let model = CollectorModel()
            model.receive(try event(#"{"kind":"error","code":"\#(code)"}"#))
            try require(model.phase == .error, "Storage event must enter its error state")
            try require(model.errorMessage == CollectorModel.message(for: code), "Storage event lost its recovery reason")
        }
    }

    private static func encryptionPreflightReasons() throws {
        for code in ["backup_encryption_disabled", "backup_encryption_unknown", "backup_encryption_changed"] {
            let model = CollectorModel()
            model.receive(try event("{\"kind\":\"error\",\"code\":\"\(code)\",\"stage\":\"connecting\"}"))
            try require(model.errorMessage == CollectorModel.message(for: code), "The preflight reason must remain specific in local review")
            try require(!model.errorMessage.contains("cause was not classified"), "Encryption preflight must not fall through to a generic error")
            try require(model.safeSupportCode.contains("|\(code)|"), "The safe diagnostic must retain the exact preflight reason")
            try require(!model.recoverySteps.isEmpty, "The owner needs a next step after a stopped preflight")
        }
    }

    private static func timeoutReasons() throws {
        let connection = CollectorModel.message(for: "connection_timeout").lowercased()
        let stalled = CollectorModel.message(for: "backup_stalled").lowercased()
        let elapsed = CollectorModel.message(for: "backup_time_limit").lowercased()
        let controlOnly = CollectorModel.message(for: "backup_no_file_progress").lowercased()

        try require(Set([connection, stalled, elapsed, controlOnly]).count == 4, "Connection, stall, control-only and elapsed failures need distinct messages")
        for obsoleteLimit in ["one hour", "1 hour", "1h"] {
            try require(!connection.contains(obsoleteLimit), "Connection timeout cannot claim a one-hour backup")
        }
        try require(stalled.contains("waiting limit") && !stalled.contains("15 minutes") && !stalled.contains("30 minutes"),
                    "The shared stall code cannot distinguish initial readiness from active-transfer inactivity")
        let stoppedModel = CollectorModel()
        stoppedModel.phase = .transferring
        try require(stoppedModel.statusDetail.contains("up to 30 minutes") &&
                    stoppedModel.statusDetail.contains("15 minutes without new activity") &&
                    !stoppedModel.statusDetail.contains("very slow"),
                    "Transfer guidance distinguishes initial readiness from later inactivity without a generic slow-speed cutoff")
        stoppedModel.receive(try event(#"{"kind":"error","code":"backup_stalled","stage":"backup"}"#))
        let stalledGuidance = ([stoppedModel.errorMessage] + stoppedModel.recoverySteps).joined(separator: " ").lowercased()
        try require(stalled.contains("stopped without saving metadata") &&
                    stalledGuidance.contains("finder") && stalledGuidance.contains("passcode") &&
                    stalledGuidance.contains("latest-backup date/time") && !stalledGuidance.contains("check the cable"),
                    "Backup silence needs a factual stopped result plus normal backup recovery guidance, without asserting a cable failure")
        try require(elapsed.contains("data-aware") && !elapsed.contains("four-hour safety limit"), "Active transfer must not claim an absolute four-hour ceiling")
        try require(controlOnly.contains("one hour") && controlOnly.contains("kept responding"), "Live control-only expiry must not claim wire silence")
    }

    private static func deviceFailureIsActionableWithoutPrivateDetails() throws {
        let model = CollectorModel()
        model.phase = .transferring
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":1024,"retainedBytes":0,"discardedBytes":1024,"filesReceived":1,"elapsedSeconds":1800,"bytesPerSecond":1}"#))
        model.receive(try event(#"{"kind":"error","code":"device_backup_failed","stage":"backup","deviceCode":205}"#))
        try require(model.phase == .error, "Device failure must stop collection")
        try require(model.errorMessage.contains("iPhone reported") && model.errorMessage.contains("205"), "Device status must be actionable without raw phone text")
        try require(model.failureContext.contains("1.0 KiB") && model.failureContext.contains("partial"), "Error view must retain last observed transfer and clarify no metadata save")
        try require(!model.failureContext.contains("phone owner"), "No private device reason may be rendered")
        let fields = model.safeSupportCode.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        try require(fields.count == 9 && fields[0] == "A1" && fields[1] == SupportDiagnosticReporter.build, "Support code must declare its bounded schema and exact build")
        try require(fields[2].range(of: #"^[A-F0-9]{8}$"#, options: .regularExpression) != nil, "A fresh opaque report reference is required before browser pairing")
        try require(fields[3...8].elementsEqual(["backup", "device_backup_failed", "1800", "1024", "0", "205"]), "Pre-pair failure report must preserve safe first code, stage, elapsed, bytes and device status")
        try require(!model.safeSupportCode.contains("phone owner"), "Support code cannot include private phone text")
        model.receive(try event(#"{"kind":"error","code":"workspace_cleanup","stage":"processing"}"#))
        try require(model.safeSupportCode == fields.joined(separator: "|"), "A later cleanup error cannot replace the first terminal device failure or report reference")
        let unknown = CollectorModel()
        unknown.phase = .transferring
        unknown.receive(try event(#"{"kind":"error","code":"private phone owner reason","stage":"backup"}"#))
        try require(unknown.safeSupportCode.contains("|collection_failed|"), "Unexpected helper text must be reduced to a safe failure category")
        try require(!unknown.safeSupportCode.contains("phone owner"), "Unexpected helper text cannot appear in the support code")
    }

    private static func automaticDiagnosticUsesOnlySafeCode() throws {
        let model = CollectorModel()
        model.receive(try event(#"{"kind":"error","code":"backup_host_space","stage":"connecting"}"#))
        let code = model.safeSupportCode
        guard let request = SupportDiagnosticReporter.request(for: code),
              let body = request.httpBody,
              let parsed = try JSONSerialization.jsonObject(with: body) as? [String: String] else {
            throw RecoveryCheckError.failed("A bounded native diagnostic request is required")
        }
        try require(request.url?.absoluteString == "https://amplifai-database-engine.vercel.app/api/v1/companion-diagnostics", "Diagnostic endpoint must be fixed HTTPS")
        try require(parsed == ["code": code] && code.contains("|backup_host_space|"), "Only the safe support code may leave the app")
        try require(SupportDiagnosticReporter.request(for: code + "|phone owner@example.com") == nil, "Extra private fields must be rejected locally")
        try require(SupportDiagnosticReporter.request(for: code.replacingOccurrences(of: "backup_host_space", with: "owner_private_error")) == nil, "Raw helper reason must never be sent")
        for category in ["backup_control_invalid", "selected_payload_missing", "selected_payload_invalid", "selected_payload_size", "selected_payload_identity", "selected_payload_file_type", "selected_payload_length", "selected_payload_crypto", "selected_contacts_integrity", "contacts_schema", "backup_control_frame_limit", "backup_control_metadata_limit", "backup_control_path_limit", "contacts_capacity_limit", "source_read_capacity_limit"] {
            let candidate = code.replacingOccurrences(of: "backup_host_space", with: category)
            try require(SupportDiagnosticReporter.request(for: candidate) != nil, "Specific processing failures must retain their safe diagnostic category")
            let processing = CollectorModel()
            processing.receive(try event("{\"kind\":\"error\",\"code\":\"\(category)\",\"stage\":\"processing\"}"))
            try require(processing.safeSupportCode.contains("|\(category)|"), "Processing category must survive in the support code")
            try require(!processing.errorMessage.contains("cause was not classified"), "Processing category must have a specific owner-facing explanation")
        }
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
        try require(model.workspaceSpaceStatus?.contains("Application Support volume") == true &&
                    model.workspaceSpaceStatus?.contains("further 2 GiB reserve") == true,
                    "A storage failure must explain the current Mac volume measurement and preserve the reserve")
    }

    private static func emptyResidueClearsGate() throws {
        let model = CollectorModel()
        model.receive(try event(#"{"kind":"error","code":"workspace_cleanup","cleanupRequired":true}"#))
        try require(!model.canConnect, "Cleanup failure must block before inspection")
        model.receive(try event(#"{"kind":"residue","sessions":[]}"#))

        try require(!model.needsStorageReview, "An empty inspection receipt must clear the storage review gate")
        try require(model.residues.isEmpty, "Empty inspection must contain no residue")
        try require(model.canPrepareCollection && !model.canConnect, "An empty verified inspection permits a fresh unchecked retry")
        try require(!model.composeRunCollectionApproval(callHistoryPreparation: .continueWithoutConfirmedCoverage),
                    "Retry after inspection still needs a verified run")
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
