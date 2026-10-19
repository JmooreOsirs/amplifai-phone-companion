import Foundation
import SwiftUI
import Darwin
import CryptoKit

@MainActor
final class CollectorModel: ObservableObject {
    private let contactPageSize = 200
    private let reviewPageSize = 1000
    private let maxContactQueryLength = 120
    private static let maxHelperCommandBytes = 600_000
    static let collectionDisclosureVersion = "native-local-collection-v5"
    private static let localCollectionScopes = ["contacts", "call-context", "message-context", "temporary-full-backup", "sanitized-failure-diagnostic"]
    enum CallHistoryPreparation: Equatable {
        case confirmedEncryptedBackup
        case continueWithoutConfirmedCoverage
    }
    enum BackupEncryptionStatus: Equatable {
        case notChecked, checking, encrypted, unencrypted, unknown
    }
    struct LocalCollectionApproval: Equatable {
        let runID: UUID
        let disclosureVersion: String
        let approvedAt: Date
        let scopes: [String]
        let callHistoryPreparation: CallHistoryPreparation
    }
    enum Phase: Equatable {
        case idle, connecting, transferring, passwordRequired, processing
        case selecting, reviewing, completing, completed, cancelling, error
    }
    enum DiagnosticDelivery: Equatable { case idle, sending, sent, failed }
    struct DestinationApprovalRequest: Equatable {
        let handoffID: String
        let intentID: String
        let email: String
        let course: String

        var attentionID: String { "\(handoffID):\(intentID)" }
    }

    @Published var phase: Phase = .idle
    @Published var progress = 0.0
    @Published private(set) var progressIsKnown = false
    @Published private(set) var transferBytes: Int64 = 0
    @Published private(set) var retainedBytes: Int64 = 0
    @Published private(set) var transferFileCount: Int64 = 0
    @Published private(set) var transferBytesPerSecond = 0.0
    @Published private(set) var processedBytes: Int64 = 0
    @Published private(set) var transferIsKnown = false
    @Published private(set) var collectionStartedAt: Date?
    @Published private(set) var lastBackupActivityAt: Date?
    @Published private(set) var lastParsingActivityAt: Date?
    @Published private(set) var transferElapsedSeconds = 0
    @Published private(set) var diagnosticReference = ""
    @Published private(set) var selectedPayloadContext = ""
    @Published private(set) var currentSourceDiagnostics: [SourceDiagnostic] = []
    @Published private(set) var sourceDiagnosticHistory: [SourceDiagnosticAttempt] = []
    enum DiagnosticRetention: Equatable { case unreported, retained, failed }
    @Published private(set) var diagnosticRetention: DiagnosticRetention = .unreported
    private let diagnosticBuild: String?
    @Published private(set) var errorElapsedSeconds = 0
    @Published private(set) var handoffID = ""
    @Published private(set) var handoffAcknowledged = false
    @Published private(set) var destinationIntentID = ""
    @Published private(set) var destinationEmail = ""
    @Published private(set) var destinationCourse = ""
    @Published private(set) var destinationApproved = false
    @Published private(set) var browserReceived = false
    @Published private(set) var accountSaveAwaitingBrowserReceipt = false
    private var closedDestinationIntentIDs: Set<String> = []
    @Published private(set) var accountNavigationID: UUID?
    @Published private(set) var selectionNeedsReview = true
    @Published var password = ""
    @Published var contacts: [ContactPreview] = []
    @Published private(set) var totalContacts = 0
    @Published private(set) var currentContactCursor = 0
    @Published private(set) var nextContactCursor: Int?
    @Published private(set) var contactQuery = ""
    @Published private(set) var contactSearchPending = false
    @Published var selectedIDs: Set<Int> = []
    private var mainPreselected = false
    @Published private(set) var selectingAllContacts = false
    @Published private(set) var selectAllCancelRequested = false
    @Published private(set) var selectAllVisited = 0
    @Published private(set) var suggestionScanning = false
    @Published private(set) var suggestionVisited = 0
    @Published private(set) var suggestionMatches: [ContactSuggestionKind: [Int]] = [:]
    @Published private(set) var suggestionViewing: ContactSuggestionKind?
    @Published private(set) var suggestionVisible: [ContactPreview] = []
    @Published private(set) var suggestionPagePending = false
    @Published private(set) var suggestionPageCursor = 0
    @Published private(set) var suggestionUndoKind: ContactSuggestionKind?
    @Published private(set) var workspace: ContactWorkspace?
    @Published private(set) var markedGroupMoveIDs: Set<Int> = []
    @Published private(set) var reviewPending = false
    @Published var availableCalls = 0
    @Published var availableMessages = 0
    @Published var matchedCalls = 0
    @Published var matchedMessages = 0
    @Published var observedCallEarliest = ""
    @Published var observedCallLatest = ""
    @Published var observedMessageEarliest = ""
    @Published var observedMessageLatest = ""
    @Published var since = ""
    @Published var connectionTransport = ""
    @Published var pairCode = ""
    @Published var pairPort = 0
    @Published var missingSources: [String] = []
    @Published private(set) var backupEncrypted: Bool?
    @Published private(set) var unavailableReasons: [String: String] = [:]
    @Published var residues: [ResiduePreview] = []
    @Published var errorMessage = ""
    @Published private(set) var helperRunning = false
    @Published private(set) var needsStorageReview = false
    @Published private(set) var collectionApprovalRunID = UUID()
    @Published private(set) var localCollectionApproval: LocalCollectionApproval?
    @Published private(set) var backupEncryptionStatus: BackupEncryptionStatus = .notChecked
    @Published private(set) var backupEncryptionCheckCode: String?
    @Published private(set) var statusCheckFailureActive = false
    @Published private(set) var settingCheckAfterCollectionFailure = false
    @Published private(set) var diagnosticDelivery: DiagnosticDelivery = .idle
    @Published private(set) var updateInstallationInProgress = false
    enum CompanionStatus: Equatable { case notStarted, starting, ready, anotherAppRunning, unavailable }
    @Published private(set) var companionStatus: CompanionStatus = .notStarted

    private var process: Process?
    private var activeHelperMode: String?
    private var inputHandle: FileHandle?
    private var outputBuffer = Data()
    private(set) var operationID: UUID?
    private var outputHandle: FileHandle?
    private var outputEnded = false
    private var exitCode: Int32?
    private var cancellationRequested = false
    private let commandWriter: ((Data) -> Void)?
    private var cancellationTask: Task<Void, Never>?
    private var handoffPollTask: Task<Void, Never>?
    private var contactSearchTask: Task<Void, Never>?
    private var expectedContactCursor = 0
    private var contactPageStarts: [Int] = []
    private var selectAllCursor = 0
    private var selectAllIDs: Set<Int> = []
    private var reviewGeneration = UUID()
    private var suggestionGeneration: UUID?
    private var suggestionScanID: String?
    private var suggestionScanCursor = 0
    private var suggestionSeenIDs: Set<Int> = []
    private var suggestionBuilding: [ContactSuggestionKind: [Int]] = [:]
    private var suggestionRequestID: String?
    private var suggestionExpectedIDs: [Int] = []
    private var suggestionUndoIDs: Set<Int> = []
    private var workspaceBuilding: [ContactPreview] = []
    private struct GroupMoveUndo {
        let ids: Set<Int>
        let previous: ContactGroup
        let destination: ContactGroup
        let selectedBefore: Set<Int>
        let generation: UUID
    }
    private struct PendingBulkMove {
        let ids: Set<Int>
        let source: ContactGroup
        let destination: ContactGroup
        let query: String?
        let generation: UUID
    }
    private var groupUndo: GroupMoveUndo?
    private var pendingBulkMove: PendingBulkMove?
    private var pendingReviewID: String?
    private var pendingReviewIDs: Set<Int>?
    private var pendingReviewPageIDs: [Int] = []
    private var pendingReviewNextCursor = 0
    private var pendingReviewSHA: String?
    private var reviewedSelectionIDs: Set<Int>?
    private var pairingRequested = false
    @Published private(set) var selectionChangePending = false
    @Published private(set) var handoffNotice = ""
    private var continueAfterReview = false
    private var navigateOnPairing = false
    private var lastErrorCode: String?
    private var lastErrorStage: String?
    private var lastDeviceStatus: Int64?
    private var attemptedDiagnosticCode: String?
    private var backupProgressSamples: [(date: Date, percent: Double)] = []
    private let cancellationGraceNanoseconds: UInt64 = 15_000_000_000
    init(commandWriter: ((Data) -> Void)? = nil, bundleInformation: [String: Any]? = Bundle.main.infoDictionary) {
        self.commandWriter = commandWriter
        diagnosticBuild = SupportDiagnosticReporter.validatedBuild(bundleInformation)
    }

    private var commandReady: Bool { helperRunning || commandWriter != nil }

    var canPrepareCollection: Bool {
        companionStatusAllowsWork && !updateInstallationInProgress && !helperRunning && process == nil && !needsStorageReview && residues.isEmpty && !hasUnsavedReview &&
            (phase == .idle || phase == .error || phase == .completed)
    }

    private var companionStatusAllowsWork: Bool { companionStatus == .notStarted || companionStatus == .ready }

    func setCompanionStatus(_ status: CompanionStatus) {
        if companionStatus != status { companionStatus = status }
    }

    private var hasCurrentCollectionApproval: Bool {
        guard let approval = localCollectionApproval else { return false }
        return approval.runID == collectionApprovalRunID &&
            approval.disclosureVersion == Self.collectionDisclosureVersion && approval.scopes == Self.localCollectionScopes
    }

    var canConnect: Bool { canPrepareCollection && hasCurrentCollectionApproval }

    @discardableResult
    func approveLocalCollection(for runID: UUID, checked: Bool, callHistoryPreparation: CallHistoryPreparation?) -> Bool {
        guard canPrepareCollection, runID == collectionApprovalRunID else { return false }
        guard checked else { declineLocalCollection(); return false }
        guard let callHistoryPreparation else { return false }
        guard backupEncryptionStatus != .notChecked, backupEncryptionStatus != .checking else { return false }
        if callHistoryPreparation == .confirmedEncryptedBackup && backupEncryptionStatus != .encrypted { return false }
        localCollectionApproval = LocalCollectionApproval(
            runID: runID, disclosureVersion: Self.collectionDisclosureVersion,
            approvedAt: Date(), scopes: Self.localCollectionScopes,
            callHistoryPreparation: callHistoryPreparation
        )
        return true
    }

    /// The read-only setting check decides call-history coverage, so the owner is not asked a second question.
    var settingCallHistoryPreparation: CallHistoryPreparation? {
        switch backupEncryptionStatus {
        case .encrypted: return .confirmedEncryptedBackup
        case .unencrypted, .unknown: return .continueWithoutConfirmedCoverage
        case .notChecked, .checking: return nil
        }
    }

    /// One owner action records agreement for this run only and starts it. Apple's Trust prompt,
    /// the existing backup password, browser sharing and account Save each still ask separately.
    @discardableResult
    func agreeAndStartCollection(for runID: UUID, checked: Bool) -> Bool {
        guard approveLocalCollection(for: runID, checked: checked,
                                     callHistoryPreparation: settingCallHistoryPreparation) else { return false }
        connect()
        return true
    }

    func declineLocalCollection() {
        localCollectionApproval = nil
        collectionApprovalRunID = UUID()
        backupEncryptionStatus = .notChecked
        backupEncryptionCheckCode = nil
    }

    @discardableResult
    func beginBackupEncryptionStatusCheck() -> Bool {
        guard canPrepareCollection, localCollectionApproval == nil else { return false }
        if phase == .error { settingCheckAfterCollectionFailure = true }
        backupEncryptionStatus = .checking
        backupEncryptionCheckCode = nil
        statusCheckFailureActive = true
        return true
    }

    func checkBackupEncryptionStatus() {
        guard beginBackupEncryptionStatusCheck() else { return }
        launch(mode: "backup-encryption-status")
    }

    var backupSettingCheckInProgress: Bool {
        helperRunning && activeHelperMode == "backup-encryption-status"
    }

    func cancelBackupEncryptionStatusCheck() {
        guard backupSettingCheckInProgress else { return }
        disconnect()
    }

    var canSafelyClose: Bool {
        handoffAcknowledged && phase == .completed && !helperRunning && !needsStorageReview && residues.isEmpty
    }

    var hasUnsavedReview: Bool { totalContacts > 0 && !handoffAcknowledged }
    var pendingDestinationApproval: DestinationApprovalRequest? {
        guard phase == .reviewing, !handoffID.isEmpty, !destinationIntentID.isEmpty,
              !destinationEmail.isEmpty, !destinationCourse.isEmpty,
              !destinationApproved, !selectionNeedsReview,
              reviewedSelectionIDs == selectedIDs else { return nil }
        return DestinationApprovalRequest(handoffID: handoffID, intentID: destinationIntentID,
                                          email: destinationEmail, course: destinationCourse)
    }
    var isSearchingContacts: Bool { contactSearchPending && contacts.isEmpty }
    var hasPreviousContactPage: Bool { !contactPageStarts.isEmpty }
    var canSelectAllContacts: Bool {
        (phase == .selecting || phase == .reviewing) && commandReady && totalContacts > 0 &&
            selectedIDs.count < totalContacts && !contactSearchPending && !reviewPending &&
            !selectingAllContacts && !suggestionScanning && !suggestionPagePending &&
            handoffID.isEmpty && !pairingRequested && !selectionChangePending
    }
    var canScanContactSuggestions: Bool {
        (phase == .selecting || phase == .reviewing) && workspace == nil &&
            commandReady && totalContacts > 0 &&
            !contactSearchPending && !reviewPending && !selectingAllContacts &&
            handoffID.isEmpty && !pairingRequested && !selectionChangePending &&
            !suggestionScanning && !suggestionPagePending
    }
    var suggestionsComplete: Bool { suggestionGeneration == reviewGeneration && !suggestionScanning }
    func suggestionMatchCount(_ kind: ContactSuggestionKind) -> Int {
        suggestionsComplete ? suggestionMatches[kind, default: []].count : 0
    }
    func suggestionAffectedCount(_ kind: ContactSuggestionKind) -> Int {
        guard suggestionsComplete else { return 0 }
        return suggestionMatches[kind, default: []].reduce(0) { $0 + (selectedIDs.contains($1) ? 1 : 0) }
    }
    func suggestionHasMultiplePages(_ kind: ContactSuggestionKind) -> Bool {
        suggestionMatchCount(kind) > contactPageSize
    }
    var suggestionUndoCount: Int { suggestionUndoIDs.count }
    var canEditWorkspace: Bool {
        (phase == .selecting || phase == .reviewing) && commandReady && workspace != nil &&
            !suggestionScanning && !reviewPending && !suggestionPagePending && !selectingAllContacts &&
            handoffID.isEmpty && !pairingRequested && !selectionChangePending
    }
    var canRequestSelectionChange: Bool {
        phase == .reviewing && commandReady && !handoffID.isEmpty &&
            reviewedSelectionIDs == selectedIDs && !pairingRequested && !selectionChangePending &&
            !accountSaveAwaitingBrowserReceipt && !handoffAcknowledged
    }
    var canSelectAllMain: Bool {
        guard canEditWorkspace, let workspace else { return false }
        return !workspace.mainIDs.isSubset(of: selectedIDs)
    }
    var canClearMain: Bool {
        guard canEditWorkspace, let workspace else { return false }
        return !workspace.mainIDs.isDisjoint(with: selectedIDs)
    }
    var canSelectAllVerifiedContacts: Bool {
        guard canEditWorkspace, let workspace, workspace.knownIDs.count == totalContacts else { return false }
        return !workspace.knownIDs.isSubset(of: selectedIDs)
    }
    var canClearAllVerifiedContacts: Bool {
        canEditWorkspace && !selectedIDs.isEmpty
    }
    var canSelectAllReview: Bool {
        guard canEditWorkspace, let workspace else { return false }
        return !workspace.reviewIDs.isSubset(of: selectedIDs)
    }
    var canClearReview: Bool {
        guard canEditWorkspace, let workspace else { return false }
        return !workspace.reviewIDs.isDisjoint(with: selectedIDs)
    }
    var selectedMainCount: Int { selectedIDs.intersection(workspace?.mainIDs ?? []).count }
    var selectedReviewCount: Int { selectedIDs.intersection(workspace?.reviewIDs ?? []).count }
    var canSelectScopedContacts: Bool {
        guard canEditWorkspace, let workspace, workspace.filteredCount > 0 else { return false }
        return !workspace.filteredIDs.isSubset(of: selectedIDs)
    }
    var canClearScopedContacts: Bool {
        guard canEditWorkspace, let workspace, workspace.filteredCount > 0 else { return false }
        return !workspace.filteredIDs.isDisjoint(with: selectedIDs)
    }
    var selectedScopedCount: Int { selectedIDs.intersection(workspace?.filteredIDs ?? []).count }
    var pendingBulkMoveCount: Int { pendingBulkMove?.ids.count ?? 0 }
    var pendingBulkMoveTitle: String {
        guard let pendingBulkMove else { return "Move contact group?" }
        let scope = pendingBulkMove.query == nil ? "marked" : "matching"
        return "Move \(pendingBulkMove.ids.count) \(scope) \(pendingBulkMove.source.rawValue) contacts to \(pendingBulkMove.destination.rawValue)?"
    }
    var pendingBulkMoveDetail: String {
        guard let pendingBulkMove else { return "" }
        let scope = pendingBulkMove.query == nil
            ? "Only your \(pendingBulkMove.ids.count) marked contacts are included, even when some are hidden by the current page or search."
            : "All \(pendingBulkMove.ids.count) current search matches across every page are included."
        let selectionEffect = pendingBulkMove.destination == .review
            ? "Moving to Review removes those contacts from the import selection."
            : "Moving to Main does not automatically select contacts for import."
        return "\(scope) \(selectionEffect) You can Undo this group move."
    }
    var canUndoGroupMove: Bool {
        canEditWorkspace && groupUndo?.generation == reviewGeneration
    }
    var hasNextSuggestionPage: Bool {
        guard let kind = suggestionViewing, suggestionsComplete else { return false }
        return suggestionPageCursor + suggestionVisible.count < suggestionMatches[kind, default: []].count
    }
    var activeFlow: Bool { helperRunning || hasUnsavedReview }
    /// A one-time link was requested and the helper has not answered yet.
    var accountLinkPending: Bool { pairingRequested }
    /// Agree · Collect · Choose · Share & report: 0...3 is the current step, 4 means every step finished.
    /// Cancelling the read-only iPhone check happens before any agreement, so it stays on Agree.
    var journeyStep: Int {
        switch phase {
        case .idle, .error: return hasUnsavedReview ? 2 : 0
        case .cancelling: return backupSettingCheckInProgress ? 0 : 1
        case .connecting, .transferring, .passwordRequired, .processing: return 1
        case .selecting: return 2
        case .reviewing, .completing: return 3
        case .completed: return 4
        }
    }
    func unavailableDescription(_ category: String) -> String {
        let label = category == "calls" ? "Call history" : "Message history"
        guard missingSources.contains(category) else { return "" }
        let detail: String
        switch unavailableReasons[category] {
        case "absent":
            detail = category == "calls" && backupEncrypted == false
                ? "This unencrypted backup did not contain call history. Encrypted computer backups can include call history; changing that setting is your choice before a future collection."
                : "The selected backup did not contain this source."
        case "unsupported_schema": detail = "This backup uses a source format this version cannot read."
        case "source_capacity": detail = "The source could not be read within the local processing limit."
        case "selected_payload_missing":
            detail = "The backup listed this source, but its file was not received. Support detail: selected_payload_missing."
        case "selected_payload_size":
            detail = "The backup reported an invalid size for this selected source. Support detail: selected_payload_size."
        case "selected_payload_identity":
            detail = "The selected source had an invalid backup identity. Support detail: selected_payload_identity."
        case "selected_payload_file_type":
            detail = "The selected source was not a regular backup file. Support detail: selected_payload_file_type."
        case "selected_payload_length":
            detail = "The received source length did not match the backup manifest. Support detail: selected_payload_length."
        case "selected_payload_crypto":
            detail = "The encrypted selected source could not be validated. Support detail: selected_payload_crypto."
        case "selected_payload_invalid": detail = "The selected source failed its integrity check. Support detail: selected_payload_invalid."
        case "source_integrity": detail = "The selected source failed its integrity check."
        case "source_invalid": detail = "The selected source could not be read as a valid database."
        default: detail = "The cause was not available from this companion version."
        }
        return "\(label) unavailable. \(detail) This does not prove zero history."
    }
    var canTerminateForUpdate: Bool {
        let settled = phase == .idle || phase == .completed || phase == .error
        return settled && !helperRunning && process == nil && !needsStorageReview && residues.isEmpty &&
            !hasUnsavedReview && !reviewPending && !pairingRequested &&
            (handoffID.isEmpty || handoffAcknowledged)
    }
    var canInstallUpdate: Bool { companionStatusAllowsWork && canTerminateForUpdate && !updateInstallationInProgress }
    var canCheckForUpdatesInBackground: Bool { companionStatusAllowsWork && !updateInstallationInProgress }

    @discardableResult
    func beginUpdateInstallation() -> Bool {
        guard canInstallUpdate else { return false }
        updateInstallationInProgress = true
        declineLocalCollection()
        return true
    }

    func endUpdateInstallation() { updateInstallationInProgress = false }

    static func byteDescription(_ value: Int64) -> String {
        let bytes = Double(value)
        for (unit, size) in [("TiB", 1_099_511_627_776.0), ("GiB", 1_073_741_824.0), ("MiB", 1_048_576.0), ("KiB", 1_024.0)] {
            if bytes >= size { return String(format: "%.1f %@", bytes / size, unit) }
        }
        return "\(value) B"
    }

    var transferSummary: String {
        "\(Self.byteDescription(transferBytes)) transferred · \(Self.byteDescription(retainedBytes)) retained locally"
    }

    var lastCollectionActivityAt: Date? {
        phase == .processing ? lastParsingActivityAt ?? lastBackupActivityAt : lastBackupActivityAt
    }

    func backupRemainingEstimate(at now: Date) -> ClosedRange<Int>? {
        guard phase == .transferring, progressIsKnown, progress >= 15, progress < 90,
              backupProgressSamples.count >= 3, let first = backupProgressSamples.first,
              let last = backupProgressSamples.last,
              (0...60).contains(now.timeIntervalSince(last.date)) else { return nil }
        let span = last.date.timeIntervalSince(first.date)
        let gain = last.percent - first.percent
        guard span >= 120, gain >= 5, abs(last.percent - progress) < 0.1 else { return nil }
        let rates = zip(backupProgressSamples, backupProgressSamples.dropFirst()).compactMap { earlier, later -> Double? in
            let seconds = later.date.timeIntervalSince(earlier.date)
            let percentage = later.percent - earlier.percent
            return seconds >= 30 && percentage > 0 ? percentage / seconds : nil
        }
        guard rates.count >= 2, let slowest = rates.min(), let fastest = rates.max(),
              slowest > 0, fastest / slowest <= 2 else { return nil }
        let remaining = 100 - progress
        let lower = Int((remaining / fastest / 60 * 0.75).rounded(.down))
        let upper = Int((remaining / slowest / 60 * 1.5).rounded(.up))
        guard upper > 0, upper <= 720 else { return nil }
        return max(1, lower)...max(2, upper)
    }

    func observeBackupProgress(_ value: Double, at date: Date) {
        guard value.isFinite, (0..<100).contains(value) else { return }
        if let previous = backupProgressSamples.last {
            if value < previous.percent { backupProgressSamples = [] }
            else if value <= previous.percent || date.timeIntervalSince(previous.date) < 30 { return }
        }
        backupProgressSamples.append((date, value))
        if backupProgressSamples.count > 8 { backupProgressSamples.removeFirst() }
    }

    var failureContext: String {
        guard phase == .error, let stage = lastErrorStage else { return "" }
        let operation: String
        switch stage {
        case "backup": operation = "backup transfer"
        case "processing": operation = "local metadata reading"
        case "review": operation = "local review or handoff"
        default: operation = "iPhone connection"
        }
        let transfer = transferIsKnown
            ? " Last observed: \(transferSummary). These are partial, temporary backup bytes—not an account save."
            : " No transfer bytes were observed by the companion."
        return "Stopped during \(operation).\(transfer)" +
            (selectedPayloadContext.isEmpty ? "" : " \(selectedPayloadContext)")
    }

    var safeSupportCode: String {
        guard phase == .error, !statusCheckFailureActive, !diagnosticReference.isEmpty else { return "" }
        let stage = lastErrorStage ?? "connecting"
        let code = lastErrorCode.flatMap { SupportDiagnosticReporter.reportCodes.contains($0) ? $0 : nil } ?? "collection_failed"
        guard let expectedBuild = diagnosticBuild else { return "" }
        return "A1|\(expectedBuild)|\(diagnosticReference)|\(stage)|\(code)|\(errorElapsedSeconds)|\(transferBytes)|\(retainedBytes)|\(lastDeviceStatus.map(String.init) ?? "-")"
    }

    var workspaceSpaceStatus: String? {
        guard phase == .error, lastErrorCode == "workspace_low_space" else { return nil }
        let root = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/\(Self.sessionProductName)/sessions", isDirectory: true)
        guard let attributes = try? FileManager.default.attributesOfFileSystem(forPath: root.path),
              let free = attributes[.systemFreeSize] as? NSNumber else {
            return "The app checks free space on this Mac's Application Support volume, not the iPhone. Current free space could not be measured here. Selected sources, parsing copies and a further 2 GiB reserve must fit."
        }
        return "Current free space on this Mac's Application Support volume: \(Self.byteDescription(free.int64Value)). This is measured now, not at the failed attempt; selected sources, parsing copies and a further 2 GiB reserve must fit."
    }

    static var sessionProductName: String {
        #if arch(x86_64)
        "AMPLIFai Phone Intel Test"
        #else
        "AMPLIFai Phone Candidate"
        #endif
    }

    static func helperEnvironment(modulePath: String) -> [String: String] {
        var environment = [
            "HOME": NSHomeDirectory(),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": modulePath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_OFFLINE": "1"
        ]
        #if arch(x86_64)
        environment["AMPLIFAI_INTEL_CANDIDATE"] = "1"
        #endif
        return environment
    }

    func reportDiagnosticIfNeeded(forceRetry: Bool = false) async {
        let code = safeSupportCode
        guard phase == .error, !code.isEmpty, diagnosticDelivery != .sending,
              forceRetry || attemptedDiagnosticCode != code else { return }
        attemptedDiagnosticCode = code
        diagnosticDelivery = .sending
        let delivered = await SupportDiagnosticReporter.send(code)
        guard phase == .error, safeSupportCode == code else { return }
        diagnosticDelivery = delivered ? .sent : .failed
    }

    func inspectResidue() {
        guard process == nil else { return }
        needsStorageReview = true
        launch(mode: "inspect")
    }

    func clearResidue() {
        guard process == nil && !residues.isEmpty else { return }
        launch(mode: "clear-residue")
    }

    func connect() {
        guard canConnect else { return }
        settingCheckAfterCollectionFailure = false
        statusCheckFailureActive = false
        attemptedDiagnosticCode = nil
        diagnosticDelivery = .idle
        selectedPayloadContext = ""
        currentSourceDiagnostics = []
        diagnosticRetention = .unreported
        diagnosticReference = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8))
        transferElapsedSeconds = 0
        errorElapsedSeconds = 0
        lastDeviceStatus = nil
        contacts = []
        totalContacts = 0
        currentContactCursor = 0
        nextContactCursor = nil
        contactQuery = ""
        contactSearchPending = false
        contactSearchTask?.cancel()
        contactSearchTask = nil
        expectedContactCursor = 0
        contactPageStarts = []
        lastErrorCode = nil
        lastErrorStage = nil
        selectedIDs = []
        mainPreselected = false
        clearSelectAll()
        clearSuggestions()
        clearPendingReview()
        reviewedSelectionIDs = nil
        availableCalls = 0
        availableMessages = 0
        matchedCalls = 0
        matchedMessages = 0
        observedCallEarliest = ""
        observedCallLatest = ""
        observedMessageEarliest = ""
        observedMessageLatest = ""
        missingSources = []
        backupEncrypted = nil
        unavailableReasons = [:]
        progress = 0
        progressIsKnown = false
        transferBytes = 0
        retainedBytes = 0
        transferFileCount = 0
        transferBytesPerSecond = 0
        processedBytes = 0
        transferIsKnown = false
        lastBackupActivityAt = nil
        lastParsingActivityAt = nil
        backupProgressSamples = []
        collectionStartedAt = Date()
        resetHandoff()
        lastErrorCode = nil
        selectionNeedsReview = true
        connectionTransport = ""
        pairCode = ""
        pairPort = 0
        errorMessage = ""
        phase = .connecting
        launch(mode: "connect")
    }

    func submitPassword() {
        guard phase == .passwordRequired, !password.isEmpty, password.count <= 512 else { return }
        let submitted = password
        password = ""
        send(["action": "password", "value": submitted])
        phase = .processing
    }

    @discardableResult
    private func invalidateSelectionForEdit() -> Bool {
        guard handoffID.isEmpty, !pairingRequested, !selectionChangePending,
              !accountSaveAwaitingBrowserReceipt, !handoffAcknowledged else { return false }
        pendingBulkMove = nil
        continueAfterReview = false
        reviewedSelectionIDs = nil
        resetHandoff()
        selectionNeedsReview = true
        phase = .selecting
        return true
    }

    func requestSelectionChange() {
        guard canRequestSelectionChange else { return }
        selectionChangePending = true
        send(["action": "revoke"])
    }

    func toggle(_ id: Int) {
        if workspace == nil { prepareWorkspace() }
        guard (phase == .selecting || phase == .reviewing), !reviewPending,
              !selectingAllContacts, !suggestionScanning, !suggestionPagePending,
              workspace?.contains(id) == true else { return }
        guard invalidateSelectionForEdit() else { return }
        if groupUndo?.ids.contains(id) == true { groupUndo = nil }
        suggestionUndoIDs.remove(id)
        if suggestionUndoIDs.isEmpty { suggestionUndoKind = nil }
        if selectedIDs.contains(id) {
            selectedIDs.remove(id)
        } else {
            selectedIDs.insert(id)
        }
    }

    private func clearSelectAll() {
        selectingAllContacts = false
        selectAllCancelRequested = false
        selectAllVisited = 0
        selectAllCursor = 0
        selectAllIDs = []
    }

    private func clearSuggestions() {
        reviewGeneration = UUID()
        pendingBulkMove = nil
        groupUndo = nil
        markedGroupMoveIDs = []
        suggestionGeneration = nil
        suggestionScanning = false
        suggestionVisited = 0
        suggestionMatches = [:]
        suggestionViewing = nil
        suggestionVisible = []
        suggestionPagePending = false
        suggestionPageCursor = 0
        suggestionUndoKind = nil
        suggestionScanID = nil
        suggestionScanCursor = 0
        suggestionSeenIDs = []
        suggestionBuilding = [:]
        suggestionRequestID = nil
        suggestionExpectedIDs = []
        suggestionUndoIDs = []
        workspaceBuilding = []
    }

    func selectAllMain() {
        guard canSelectAllMain, let workspace else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.formUnion(workspace.mainIDs)
        groupUndo = nil
    }

    func selectAllVerifiedContacts() {
        guard canSelectAllVerifiedContacts, let workspace else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.formUnion(workspace.knownIDs)
        groupUndo = nil
    }

    func clearAllVerifiedContacts() {
        guard canClearAllVerifiedContacts else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs = []
        groupUndo = nil
    }

    func selectAllReview() {
        guard canSelectAllReview, let workspace else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.formUnion(workspace.reviewIDs)
        groupUndo = nil
    }

    func clearReview() {
        guard canClearReview, let workspace else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.subtract(workspace.reviewIDs)
        groupUndo = nil
    }

    func clearMain() {
        guard canClearMain, let workspace else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.subtract(workspace.mainIDs)
        groupUndo = nil
    }

    /// Selects every contact in the current group and search, on every page.
    func selectScopedContacts() {
        guard canSelectScopedContacts, let workspace else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.formUnion(workspace.filteredIDs)
        groupUndo = nil
    }

    /// Clears every contact in the current group and search, on every page.
    func clearScopedContacts() {
        guard canClearScopedContacts, let workspace else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.subtract(workspace.filteredIDs)
        groupUndo = nil
    }

    func showContactGroup(_ group: ContactGroup) {
        guard let workspace else { return }
        pendingBulkMove = nil
        if workspace.group != group { markedGroupMoveIDs = [] }
        var changed = workspace
        changed.show(group)
        self.workspace = changed
    }

    func searchWorkspace(_ query: String) {
        guard query.unicodeScalars.count <= maxContactQueryLength,
              let workspace else { return }
        pendingBulkMove = nil
        var changed = workspace
        changed.search(query)
        self.workspace = changed
    }

    func nextWorkspacePage() {
        guard let workspace else { return }
        var changed = workspace
        changed.nextPage()
        self.workspace = changed
    }

    func previousWorkspacePage() {
        guard let workspace else { return }
        var changed = workspace
        changed.previousPage()
        self.workspace = changed
    }

    func moveContact(_ id: Int, to group: ContactGroup) {
        guard canEditWorkspace, let workspace,
              workspace.contains(id) else { return }
        let previous: ContactGroup = workspace.isInReview(id) ? .review : .main
        guard previous != group else { return }
        let selectedBefore = selectedIDs.intersection([id])
        guard invalidateSelectionForEdit() else { return }
        var changed = workspace
        guard changed.move([id], from: previous, to: group) else { return }
        self.workspace = changed
        markedGroupMoveIDs.remove(id)
        if group == .review { selectedIDs.remove(id) }
        groupUndo = GroupMoveUndo(ids: [id], previous: previous, destination: group,
                                  selectedBefore: selectedBefore, generation: reviewGeneration)
    }

    @discardableResult
    func beginBulkMove(to destination: ContactGroup) -> Bool {
        guard canEditWorkspace, let workspace, workspace.group != .all,
              destination != .all, destination != workspace.group,
              !workspace.filteredIDs.isEmpty else { return false }
        pendingBulkMove = PendingBulkMove(ids: workspace.filteredIDs, source: workspace.group,
                                          destination: destination, query: workspace.query,
                                          generation: reviewGeneration)
        return true
    }

    func markContactForGroupMove(_ id: Int) {
        guard canEditWorkspace, let workspace, workspace.group != .all,
              workspace.visible.contains(where: { $0.id == id }),
              workspace.isInReview(id) == (workspace.group == .review) else { return }
        pendingBulkMove = nil
        if markedGroupMoveIDs.contains(id) { markedGroupMoveIDs.remove(id) }
        else { markedGroupMoveIDs.insert(id) }
    }

    func markAllMatchingForGroupMove() {
        guard canEditWorkspace, let workspace, workspace.group != .all else { return }
        pendingBulkMove = nil
        markedGroupMoveIDs.formUnion(workspace.filteredIDs)
    }

    func clearGroupMoveMarks() {
        pendingBulkMove = nil
        markedGroupMoveIDs = []
    }

    @discardableResult
    func beginMarkedBulkMove(to destination: ContactGroup) -> Bool {
        guard canEditWorkspace, let workspace, workspace.group != .all,
              destination != .all, destination != workspace.group,
              !markedGroupMoveIDs.isEmpty,
              markedGroupMoveIDs.isSubset(of: workspace.group == .review ? workspace.reviewIDs : workspace.mainIDs) else {
            return false
        }
        pendingBulkMove = PendingBulkMove(ids: markedGroupMoveIDs, source: workspace.group,
                                          destination: destination, query: nil,
                                          generation: reviewGeneration)
        return true
    }

    func cancelBulkMove() { pendingBulkMove = nil }

    @discardableResult
    func confirmBulkMove() -> Bool {
        guard let pending = pendingBulkMove else { return false }
        pendingBulkMove = nil
        guard canEditWorkspace, let workspace, pending.generation == reviewGeneration,
              workspace.group == pending.source else { return false }
        if let query = pending.query {
            guard workspace.query == query && workspace.filteredIDs == pending.ids else { return false }
        } else {
            guard markedGroupMoveIDs == pending.ids else { return false }
        }
        var changed = workspace
        guard changed.move(pending.ids, from: pending.source, to: pending.destination) else { return false }
        let selectedBefore = selectedIDs.intersection(pending.ids)
        guard invalidateSelectionForEdit() else { return false }
        self.workspace = changed
        markedGroupMoveIDs = []
        if pending.destination == .review { selectedIDs.subtract(pending.ids) }
        groupUndo = GroupMoveUndo(ids: pending.ids, previous: pending.source,
                                  destination: pending.destination, selectedBefore: selectedBefore,
                                  generation: reviewGeneration)
        return true
    }

    func undoContactMove() {
        guard canUndoGroupMove, let undo = groupUndo, let workspace else { return }
        var changed = workspace
        guard changed.move(undo.ids, from: undo.destination, to: undo.previous) else { return }
        guard invalidateSelectionForEdit() else { return }
        self.workspace = changed
        selectedIDs.subtract(undo.ids)
        selectedIDs.formUnion(undo.selectedBefore)
        groupUndo = nil
    }

    func scanContactSuggestions() {
        guard canScanContactSuggestions else { return }
        suggestionGeneration = nil
        suggestionMatches = [:]
        suggestionViewing = nil
        suggestionVisible = []
        suggestionScanning = true
        suggestionVisited = 0
        suggestionScanCursor = 0
        suggestionSeenIDs = []
        suggestionBuilding = [:]
        workspaceBuilding = []
        suggestionScanID = UUID().uuidString.lowercased()
        sendSuggestionScanPage()
    }

    /// Main contacts start selected once per capture; Review contacts wait for the owner's choice.
    private func applyDefaultSelection() {
        guard !mainPreselected, let workspace else { return }
        mainPreselected = true
        if selectedIDs.isEmpty && handoffID.isEmpty && phase == .selecting {
            selectedIDs = workspace.mainIDs
        }
    }

    func prepareWorkspace() {
        guard workspace == nil, !suggestionScanning,
              (phase == .selecting || phase == .reviewing), totalContacts > 0 else { return }
        if nextContactCursor == nil && currentContactCursor == 0 &&
            contactQuery.isEmpty && contacts.count == totalContacts {
            workspace = ContactWorkspace(previews: contacts, expectedCount: totalContacts)
            applyDefaultSelection()
            suggestionMatches = [:]
            for contact in contacts {
                for kind in ContactSuggestionKind.matches(contact.name) {
                    suggestionMatches[kind, default: []].append(contact.id)
                }
            }
            suggestionVisited = totalContacts
            suggestionGeneration = reviewGeneration
        } else {
            scanContactSuggestions()
        }
    }

    private func sendSuggestionScanPage() {
        guard let suggestionScanID else { return }
        send(["action": "contacts", "query": "", "cursor": suggestionScanCursor,
              "scanId": suggestionScanID])
    }

    func cancelSuggestionScan() {
        guard suggestionScanning else { return }
        suggestionScanning = false
        suggestionScanID = nil
        suggestionSeenIDs = []
        suggestionBuilding = [:]
        suggestionVisited = 0
        workspaceBuilding = []
    }

    func inspectSuggestion(_ kind: ContactSuggestionKind) {
        guard suggestionsComplete, !suggestionPagePending,
              !suggestionMatches[kind, default: []].isEmpty else { return }
        suggestionViewing = kind
        suggestionVisible = []
        requestSuggestionPage(at: 0)
    }

    private func requestSuggestionPage(at cursor: Int) {
        guard let kind = suggestionViewing, suggestionsComplete, !suggestionPagePending,
              cursor >= 0, cursor < suggestionMatches[kind, default: []].count else { return }
        let ids = suggestionMatches[kind, default: []]
        suggestionExpectedIDs = Array(ids[cursor..<min(cursor + contactPageSize, ids.count)])
        suggestionPageCursor = cursor
        let requestID = UUID().uuidString.lowercased()
        suggestionRequestID = requestID
        suggestionPagePending = true
        send(["action": "contact-ids", "ids": suggestionExpectedIDs,
              "requestId": requestID])
    }

    func nextSuggestionPage() {
        guard !suggestionPagePending, hasNextSuggestionPage else { return }
        requestSuggestionPage(at: suggestionPageCursor + suggestionVisible.count)
    }

    func previousSuggestionPage() {
        guard !suggestionPagePending, suggestionPageCursor > 0 else { return }
        requestSuggestionPage(at: max(0, suggestionPageCursor - contactPageSize))
    }

    func closeSuggestionInspection() {
        suggestionViewing = nil
        suggestionVisible = []
        suggestionPagePending = false
        suggestionRequestID = nil
        suggestionExpectedIDs = []
    }

    func deselectSuggestionGroup(_ kind: ContactSuggestionKind) {
        guard (phase == .selecting || phase == .reviewing), suggestionsComplete,
              suggestionViewing == kind, !suggestionPagePending, !reviewPending,
              !selectingAllContacts, !suggestionScanning else { return }
        let changed = Set(suggestionMatches[kind, default: []]).intersection(selectedIDs)
        guard !changed.isEmpty else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.subtract(changed)
        suggestionUndoIDs = changed
        suggestionUndoKind = kind
    }

    func undoSuggestionDeselect() {
        guard (phase == .selecting || phase == .reviewing), suggestionsComplete,
              suggestionUndoKind != nil, !suggestionUndoIDs.isEmpty,
              !suggestionPagePending, !reviewPending, !selectingAllContacts,
              !suggestionScanning else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs.formUnion(suggestionUndoIDs)
        suggestionUndoIDs = []
        suggestionUndoKind = nil
    }

    func selectAllContacts() {
        guard canSelectAllContacts else { return }
        guard invalidateSelectionForEdit() else { return }
        clearSelectAll()
        selectingAllContacts = true
        send(["action": "contacts", "query": "", "cursor": 0])
    }

    func cancelSelectAllContacts() {
        guard selectingAllContacts else { return }
        selectAllCancelRequested = true
    }

    func clearAllContacts() {
        guard (phase == .selecting || phase == .reviewing), !reviewPending,
              !selectingAllContacts, !suggestionScanning, !suggestionPagePending,
              !selectedIDs.isEmpty else { return }
        guard invalidateSelectionForEdit() else { return }
        selectedIDs = []
        suggestionUndoIDs = []
        suggestionUndoKind = nil
    }

    func searchContacts(_ query: String) {
        guard phase == .selecting || phase == .reviewing, commandReady,
              !reviewPending, !selectingAllContacts, !suggestionScanning, !suggestionPagePending,
              query.unicodeScalars.count <= maxContactQueryLength else { return }
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed != contactQuery else { return }
        contactSearchTask?.cancel()
        contactQuery = trimmed
        contacts = []
        currentContactCursor = 0
        nextContactCursor = nil
        expectedContactCursor = 0
        contactPageStarts = []
        contactSearchPending = true
        contactSearchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled else { return }
            self?.send(["action": "contacts", "query": trimmed, "cursor": 0])
        }
    }

    func loadMoreContacts() {
        guard (phase == .selecting || phase == .reviewing), commandReady,
              !reviewPending, !contactSearchPending, !selectingAllContacts, !suggestionScanning,
              !suggestionPagePending,
              let cursor = nextContactCursor else { return }
        contactPageStarts.append(currentContactCursor)
        expectedContactCursor = cursor
        contactSearchPending = true
        send(["action": "contacts", "query": contactQuery, "cursor": cursor])
    }

    func previousContacts() {
        guard (phase == .selecting || phase == .reviewing), commandReady,
              !reviewPending, !contactSearchPending, !selectingAllContacts, !suggestionScanning,
              !suggestionPagePending,
              let cursor = contactPageStarts.popLast() else { return }
        expectedContactCursor = cursor
        contactSearchPending = true
        send(["action": "contacts", "query": contactQuery, "cursor": cursor])
    }

    func review(continueToAccount: Bool = false) {
        guard phase == .selecting || phase == .reviewing else { return }
        guard commandReady, !reviewPending, !contactSearchPending, !selectingAllContacts,
              !suggestionScanning, !suggestionPagePending, handoffID.isEmpty,
              !pairingRequested, !selectionChangePending,
              !accountSaveAwaitingBrowserReceipt, !handoffAcknowledged,
              !selectedIDs.isEmpty else { return }
        let reviewID = UUID().uuidString.lowercased()
        pendingReviewID = reviewID
        pendingReviewIDs = selectedIDs
        pendingReviewPageIDs = selectedIDs.sorted()
        pendingReviewNextCursor = 0
        pendingReviewSHA = Self.selectionDigest(pendingReviewPageIDs)
        reviewedSelectionIDs = nil
        reviewPending = true
        selectionNeedsReview = true
        phase = .selecting
        resetHandoff()
        continueAfterReview = continueToAccount
        sendReviewPage()
    }

    func reviewWorkspace(continueToAccount: Bool = false) {
        guard let workspace, !suggestionScanning,
              selectedIDs.isSubset(of: workspace.knownIDs) else { return }
        review(continueToAccount: continueToAccount)
    }

    static func selectionDigest(_ ids: [Int]) -> String {
        var digest = SHA256()
        for id in ids {
            digest.update(data: Data("\(id)\n".utf8))
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func sendReviewPage() {
        guard let reviewID = pendingReviewID,
              pendingReviewNextCursor < pendingReviewPageIDs.count else { return }
        let start = pendingReviewNextCursor
        let end = min(start + reviewPageSize, pendingReviewPageIDs.count)
        pendingReviewNextCursor = end
        send(["action": "review-page", "reviewId": reviewID,
              "cursor": start, "total": pendingReviewPageIDs.count,
              "ids": Array(pendingReviewPageIDs[start..<end])])
    }

    private func clearPendingReview() {
        reviewPending = false
        pendingReviewID = nil
        pendingReviewIDs = nil
        pendingReviewPageIDs = []
        pendingReviewNextCursor = 0
        pendingReviewSHA = nil
    }

    func pairBrowser(automaticAccountNavigation: Bool = false) {
        guard phase == .reviewing, !selectedIDs.isEmpty, !selectingAllContacts,
              !suggestionScanning, !suggestionPagePending,
              !reviewPending, !selectionNeedsReview, !pairingRequested, !selectionChangePending,
              !accountSaveAwaitingBrowserReceipt, !handoffAcknowledged,
              reviewedSelectionIDs == selectedIDs else { return }
        // Keep the old bridge binding until the helper confirms replacement.
        // An account receipt can race this command and cause the helper to
        // reject replacement while the original browser ACK remains live.
        pairingRequested = true
        navigateOnPairing = automaticAccountNavigation
        send(["action": "pair"])
    }

    func accountNavigationFailed() {
        guard phase == .reviewing, !pairCode.isEmpty, !handoffID.isEmpty else { return }
        errorMessage = "The account page did not open automatically. Your local review and one-time link remain available. Choose Open AMPLIFai account in browser, or copy the one-time code under Having trouble connecting?"
    }

    func decideAccountDestination(_ approved: Bool, request: DestinationApprovalRequest) {
        guard pendingDestinationApproval == request else { return }
        send(["action": "destination-decision", "handoffId": request.handoffID,
              "intentId": request.intentID, "approved": approved])
    }

    func disconnect() {
        declineLocalCollection()
        guard let process else {
            // Explicit owner discard remains usable after an unexpected helper exit.
            clearPrivateReview()
            resetHandoff()
            errorMessage = ""
            phase = .idle
            return
        }
        cancellationRequested = true
        resetHandoff()
        needsStorageReview = true
        if phase == .selecting || phase == .reviewing {
            send(["action": "disconnect"])
        } else if process.isRunning {
            process.terminate()
        }
        clearPrivateReview()
        phase = .cancelling
        awaitHelperExit()
    }

    private func clearPrivateReview() {
        password = ""
        contacts = []
        totalContacts = 0
        currentContactCursor = 0
        nextContactCursor = nil
        contactQuery = ""
        contactSearchPending = false
        contactSearchTask?.cancel()
        contactSearchTask = nil
        expectedContactCursor = 0
        contactPageStarts = []
        selectedIDs = []
        clearSelectAll()
        clearSuggestions()
        mainPreselected = false
        workspace = nil
        groupUndo = nil
        clearPendingReview()
        reviewedSelectionIDs = nil
        pairCode = ""
        pairPort = 0
        matchedCalls = 0
        matchedMessages = 0
        availableCalls = 0
        availableMessages = 0
        observedCallEarliest = ""
        observedCallLatest = ""
        observedMessageEarliest = ""
        observedMessageLatest = ""
        selectionNeedsReview = true
    }

    private func resetHandoff() {
        handoffPollTask?.cancel()
        handoffPollTask = nil
        pairingRequested = false
        selectionChangePending = false
        handoffNotice = ""
        handoffID = ""
        handoffAcknowledged = false
        browserReceived = false
        accountSaveAwaitingBrowserReceipt = false
        destinationIntentID = ""
        destinationEmail = ""
        destinationCourse = ""
        destinationApproved = false
        closedDestinationIntentIDs = []
        pairCode = ""
        pairPort = 0
        navigateOnPairing = false
        accountNavigationID = nil
    }

    private func pollHandoff() {
        guard helperRunning, !handoffID.isEmpty, let operation = operationID else { return }
        let binding = handoffID
        handoffPollTask?.cancel()
        handoffPollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 2_000_000_000) } catch { return }
                guard let self, self.operationID == operation, self.handoffID == binding,
                      self.phase == .reviewing, !self.cancellationRequested else { return }
                self.send(["action": "handoff-status", "handoffId": binding])
            }
        }
    }

    private func awaitHelperExit() {
        guard cancellationTask == nil, let runner = process, let id = operationID else { return }
        cancellationTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(nanoseconds: self?.cancellationGraceNanoseconds ?? 15_000_000_000)
            } catch { return }
            guard let self, self.operationID == id else { return }
            self.needsStorageReview = true
            self.clearPrivateReview()
            if runner.isRunning {
                // Only this operation's owned helper is terminated; residue is inspected, never auto-deleted.
                let result = Darwin.kill(runner.processIdentifier, SIGKILL)
                let primary = self.errorMessage.isEmpty ? "" : self.errorMessage + " "
                self.fail(result == 0 || errno == ESRCH
                    ? primary + "The local helper did not finish cleanup. It was stopped; check temporary data before reconnecting."
                    : "The local helper could not be stopped. Quit the app before checking temporary data and reconnecting.")
            } else {
                self.outputHandle?.readabilityHandler = nil
                try? self.outputHandle?.close()
                self.outputEnded = true
                if let code = self.exitCode { self.finished(mode: "connect", code: code) }
            }
        }
    }

    func launch(mode: String) {
        guard process == nil else { return }
        let id: UUID
        let requireEncryptedBackup: Bool
        if mode == "connect" {
            guard companionStatusAllowsWork, phase == .connecting, !needsStorageReview, residues.isEmpty, !hasUnsavedReview,
                  hasCurrentCollectionApproval, let approval = localCollectionApproval else {
                fail("Review and explicitly agree to this local collection before connecting.")
                return
            }
            id = approval.runID
            requireEncryptedBackup = approval.callHistoryPreparation == .confirmedEncryptedBackup
        } else if mode == "backup-encryption-status" {
            guard canPrepareCollection, backupEncryptionStatus == .checking,
                  localCollectionApproval == nil else { return }
            // A Recheck retains the consent-run ID; callbacks must never share a process ID.
            id = UUID()
            requireEncryptedBackup = false
        } else {
            id = UUID()
            requireEncryptedBackup = false
        }
        // Receipt consumption precedes filesystem/helper access and cannot authorize a retry.
        if mode != "backup-encryption-status" { declineLocalCollection() }
        guard let resources = Bundle.main.resourceURL else {
            fail("The app bundle is missing its local helper.")
            return
        }
        let portableHelper = resources.appendingPathComponent("helper/amplifai-agent")
        let uvxHelper = resources.appendingPathComponent("uvx")
        let module = resources.appendingPathComponent("collector")
        let portable = FileManager.default.isExecutableFile(atPath: portableHelper.path)
        guard portable || (FileManager.default.isExecutableFile(atPath: uvxHelper.path) &&
                           FileManager.default.fileExists(atPath: module.appendingPathComponent("amplifai_phone/agent.py").path)) else {
            fail("The local collector runtime is missing. Download and reinstall the signed Mac companion.")
            return
        }

        let runner = Process()
        let input = Pipe()
        let output = Pipe()
        runner.executableURL = portable ? portableHelper : uvxHelper
        let arguments = portable ? [mode] : [
            "--offline", "--from", "pymobiledevice3==10.4.0",
            "python", "-m", "amplifai_phone.agent", mode
        ]
        runner.arguments = requireEncryptedBackup ? arguments + ["--require-encrypted-backup"] : arguments
        runner.environment = Self.helperEnvironment(modulePath: module.path)
        runner.standardInput = input
        runner.standardOutput = output
        runner.standardError = FileHandle.nullDevice
        outputBuffer = Data()
        operationID = id
        outputEnded = false
        exitCode = nil
        cancellationRequested = false
        inputHandle = input.fileHandleForWriting
        outputHandle = output.fileHandleForReading
        process = runner
        activeHelperMode = mode
        helperRunning = true

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            if bytes.isEmpty { handle.readabilityHandler = nil }
            Task { @MainActor [weak self] in
                self?.handleHelperOutput(bytes, operation: id, mode: mode)
            }
        }
        runner.terminationHandler = { [weak self] finished in
            Task { @MainActor [weak self] in
                self?.handleHelperTermination(finished.terminationStatus, operation: id, mode: mode)
            }
        }
        do {
            try runner.run()
            output.fileHandleForWriting.closeFile()
            input.fileHandleForReading.closeFile()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            output.fileHandleForWriting.closeFile()
            input.fileHandleForReading.closeFile()
            process = nil
            activeHelperMode = nil
            operationID = nil
            outputHandle = nil
            inputHandle = nil
            helperRunning = false
            fail("The local helper could not start. Reopen the app or reinstall the signed Mac companion.")
        }
    }

    // The operation token belongs to the helper process, not to the reusable consent run.
    // Late output and termination from a prior read-only check cannot settle a Recheck.
    func handleHelperOutput(_ bytes: Data, operation id: UUID, mode: String) {
        guard operationID == id else { return }
        if bytes.isEmpty {
            outputEnded = true
            if let code = exitCode { finished(mode: mode, code: code) }
        } else { consume(bytes) }
    }

    func handleHelperTermination(_ code: Int32, operation id: UUID, mode: String) {
        guard operationID == id else { return }
        exitCode = code
        if outputEnded { finished(mode: mode, code: code) }
    }

    static func encodedHelperCommand(_ command: [String: Any]) -> Data? {
        guard let data = try? JSONSerialization.data(withJSONObject: command),
              data.count + 1 <= maxHelperCommandBytes else { return nil }
        return data + Data([10])
    }

    private func send(_ command: [String: Any]) {
        guard let data = Self.encodedHelperCommand(command) else {
            fail("The local connection is unavailable.")
            return
        }
        if let commandWriter {
            commandWriter(data)
            return
        }
        guard let inputHandle else {
            fail("The local connection is unavailable.")
            return
        }
        do {
            try inputHandle.write(contentsOf: data)
        } catch {
            fail("The local connection closed. Connect again.")
        }
    }

    private func consume(_ bytes: Data) {
        guard !bytes.isEmpty else { return }
        if activeHelperMode == "backup-encryption-status" && backupEncryptionStatus != .checking { return }
        outputBuffer.append(bytes)
        if outputBuffer.count > 16 * 1024 * 1024 {
            process?.terminate()
            needsStorageReview = true
            awaitHelperExit()
            fail("The collector returned too much data for review.")
            return
        }
        while let newline = outputBuffer.firstIndex(of: 10) {
            let line = Data(outputBuffer[..<newline])
            outputBuffer.removeSubrange(...newline)
            guard let event = try? AgentEvent.decode(line) else {
                process?.terminate()
                needsStorageReview = true
                awaitHelperExit()
                fail("The local helper returned an invalid response.")
                return
            }
            receive(event)
        }
    }

    private func validContactPage(_ page: [ContactPreview], total: Int, cursor: Int,
                                  next: Int?, query: String) -> Bool {
        guard total >= 0, cursor >= 0, cursor <= total,
              page.count <= contactPageSize, query.unicodeScalars.count <= maxContactQueryLength,
              cursor <= total - page.count,
              Set(page.map(\.id)).count == page.count,
              page.allSatisfy(validContactPreview) else { return false }
        if let next {
            guard next >= cursor + page.count, next <= total,
                  page.count == contactPageSize else { return false }
            if query.isEmpty && next != cursor + page.count { return false }
        } else if query.isEmpty && cursor + page.count != total { return false }
        return true
    }

    private func validContactPreview(_ contact: ContactPreview) -> Bool {
        contact.id >= 0 && contact.name.unicodeScalars.count <= 240 &&
            (0...20).contains(contact.phoneCount) &&
            contact.phoneEnds.count == contact.phoneCount &&
            contact.phoneEnds.allSatisfy({ $0.count == 4 && $0.allSatisfy(\.isNumber) })
    }

    private func rejectContactPage() {
        process?.terminate()
        needsStorageReview = true
        awaitHelperExit()
        fail("The local helper returned an incomplete contact page. No metadata was saved. Check temporary data before reconnecting.")
    }

    func receive(_ event: AgentEvent) {
        if cancellationRequested && event.kind != "error" && event.kind != "state" { return }
        if cancellationRequested && event.kind == "state"
            && event.state != "cancelled" && event.state != "disconnected" { return }
        switch event.kind {
        case "state":
            switch event.state {
            case "connecting":
                collectionStartedAt = Date()
                progressIsKnown = false
                phase = .connecting
            case "password_required": phase = .passwordRequired
            case "processing": phase = .processing
            case "disconnected", "cancelled":
                declineLocalCollection()
                if handoffAcknowledged && !cancellationRequested { phase = .completing }
                else { clearPrivateReview(); resetHandoff(); phase = .idle }
            case "completed":
                if handoffAcknowledged { phase = .completing }
            case "pairing_revoked":
                guard selectionChangePending, !accountSaveAwaitingBrowserReceipt,
                      !handoffAcknowledged else { break }
                resetHandoff()
                errorMessage = ""
                handoffNotice = "The old browser link is closed. Your local review is still here. Change your selection, then choose Continue to account again."
            default: break
            }
        case "progress":
            if let value = event.value, value.isFinite, (0...100).contains(value) {
                observeBackupProgress(value, at: Date())
                progress = value
                progressIsKnown = true
                phase = value == 100 ? .processing : .transferring
            } else { progressIsKnown = false; phase = .transferring }
        case "transfer":
            guard phase == .connecting || phase == .transferring || phase == .processing else { return }
            if event.stage == "backup" {
                guard let received = event.receivedBytes, let retained = event.retainedBytes,
                      let discarded = event.discardedBytes, let files = event.filesReceived,
                      let rate = event.bytesPerSecond, let elapsed = event.elapsedSeconds,
                      received >= transferBytes, received <= 9_007_199_254_740_991,
                      retained >= retainedBytes, retained <= received, discarded >= 0,
                      discarded == received - retained, files >= transferFileCount,
                      rate.isFinite, rate >= 0, rate <= 1_099_511_627_776,
                      elapsed.isFinite, elapsed > 0 else { return }
                if received > transferBytes || files > transferFileCount { lastBackupActivityAt = Date() }
                transferBytes = received
                retainedBytes = retained
                transferFileCount = files
                transferBytesPerSecond = rate
                if elapsed <= 172_800 { transferElapsedSeconds = Int(elapsed) }
                transferIsKnown = true
                phase = .transferring
            } else if event.stage == "processing", let bytes = event.processedBytes,
                      bytes >= processedBytes, bytes <= 9_007_199_254_740_991 {
                if bytes > processedBytes { lastParsingActivityAt = Date() }
                processedBytes = bytes
                phase = .processing
            }
        case "connection":
            connectionTransport = event.transport == "wifi" ? "Wi-Fi" : "USB"
        case "selected-payload-diagnostic":
            guard phase == .processing || phase == .connecting,
                  let record = event.selectedSourceDiagnostic else { return }
            currentSourceDiagnostics.removeAll { $0.id == record.id }
            guard currentSourceDiagnostics.count < 12 else { return }
            currentSourceDiagnostics.append(record)
            selectedPayloadContext = currentSourceDiagnostics.map(\.detail).joined(separator: "\n")
        case "capture":
            let firstPage = event.contacts ?? []
            let total = event.totalContacts ?? firstPage.count
            guard (event.query ?? "").isEmpty, (event.cursor ?? 0) == 0,
                  validContactPage(firstPage, total: total, cursor: 0,
                                   next: event.nextCursor, query: "") else {
                rejectContactPage()
                return
            }
            let captureMissing = Set(event.missing ?? [])
            let captureReasons = event.unavailableReasons ?? [:]
            let suppliedDiagnostics = event.sourceDiagnostics ?? []
            guard suppliedDiagnostics.count <= 12,
                  Set(suppliedDiagnostics.map(\.id)).count == suppliedDiagnostics.count,
                  suppliedDiagnostics.allSatisfy({ $0.source != "contacts" && captureMissing.contains($0.source) && captureReasons[$0.source] == $0.reason }) else {
                fail("The local helper returned inconsistent source details.")
                return
            }
            var captureDiagnostics = currentSourceDiagnostics.filter {
                $0.source != "contacts" && captureMissing.contains($0.source) && captureReasons[$0.source] == $0.reason
            }
            for record in suppliedDiagnostics {
                captureDiagnostics.removeAll { $0.id == record.id }
                captureDiagnostics.append(record)
            }
            currentSourceDiagnostics = captureDiagnostics
            selectedPayloadContext = currentSourceDiagnostics.map(\.detail).joined(separator: "\n")
            declineLocalCollection()
            clearSuggestions()
            workspace = nil
            groupUndo = nil
            clearSelectAll()
            clearPendingReview()
            reviewedSelectionIDs = nil
            selectedIDs = []
            mainPreselected = false
            resetHandoff()
            contacts = firstPage
            totalContacts = total
            currentContactCursor = 0
            nextContactCursor = event.nextCursor
            contactQuery = ""
            contactSearchPending = false
            expectedContactCursor = event.nextCursor ?? 0
            contactPageStarts = []
            availableCalls = event.availableCalls ?? 0
            availableMessages = event.availableMessages ?? 0
            since = event.since ?? ""
            missingSources = event.missing ?? []
            backupEncrypted = event.backupEncrypted
            unavailableReasons = event.unavailableReasons ?? [:]
            selectionNeedsReview = true
            phase = .selecting
        case "contacts":
            guard phase == .selecting || phase == .reviewing else { return }
            if let eventScanID = event.scanId {
                guard suggestionScanning, eventScanID == suggestionScanID,
                      event.query == "", event.cursor == suggestionScanCursor else { return }
                guard let page = event.contacts, event.totalContacts == totalContacts,
                      validContactPage(page, total: totalContacts, cursor: suggestionScanCursor,
                                       next: event.nextCursor, query: "") else {
                    rejectContactPage()
                    return
                }
                for contact in page {
                    guard suggestionSeenIDs.insert(contact.id).inserted else {
                        rejectContactPage()
                        return
                    }
                    for kind in ContactSuggestionKind.matches(contact.name) {
                        suggestionBuilding[kind, default: []].append(contact.id)
                    }
                }
                workspaceBuilding.append(contentsOf: page)
                suggestionVisited += page.count
                if let next = event.nextCursor {
                    suggestionScanCursor = next
                    sendSuggestionScanPage()
                } else {
                    guard suggestionVisited == totalContacts && suggestionSeenIDs.count == totalContacts else {
                        rejectContactPage()
                        return
                    }
                    suggestionMatches = suggestionBuilding
                    suggestionGeneration = reviewGeneration
                    workspace = ContactWorkspace(previews: workspaceBuilding,
                                                expectedCount: totalContacts)
                    guard workspace != nil else { rejectContactPage(); return }
                    applyDefaultSelection()
                    suggestionScanning = false
                    suggestionScanID = nil
                    suggestionSeenIDs = []
                    suggestionBuilding = [:]
                    workspaceBuilding = []
                }
                return
            }
            if suggestionScanning {
                rejectContactPage()
                return
            }
            if selectingAllContacts {
                guard event.query == "", event.cursor == selectAllCursor else { return }
                if selectAllCancelRequested { clearSelectAll(); return }
                guard let page = event.contacts, event.totalContacts == totalContacts,
                      validContactPage(page, total: totalContacts, cursor: selectAllCursor,
                                       next: event.nextCursor, query: "") else {
                    rejectContactPage()
                    return
                }
                for contact in page {
                    guard selectAllIDs.insert(contact.id).inserted else {
                        rejectContactPage()
                        return
                    }
                }
                selectAllVisited += page.count
                if let next = event.nextCursor {
                    selectAllCursor = next
                    send(["action": "contacts", "query": "", "cursor": next])
                } else {
                    guard selectAllVisited == totalContacts && selectAllIDs.count == totalContacts else {
                        rejectContactPage()
                        return
                    }
                    selectedIDs = selectAllIDs
                    suggestionUndoIDs = []
                    suggestionUndoKind = nil
                    clearSelectAll()
                }
                return
            }
            guard event.query == contactQuery, event.cursor == expectedContactCursor else { return }
            guard let page = event.contacts, event.totalContacts == totalContacts,
                  validContactPage(page, total: totalContacts, cursor: expectedContactCursor,
                                   next: event.nextCursor, query: contactQuery) else {
                rejectContactPage()
                return
            }
            contacts = page
            currentContactCursor = expectedContactCursor
            nextContactCursor = event.nextCursor
            contactSearchPending = false
            expectedContactCursor = event.nextCursor ?? 0
        case "contact-ids":
            guard (phase == .selecting || phase == .reviewing), suggestionPagePending,
                  event.requestId == suggestionRequestID else { return }
            guard let page = event.contacts, event.totalContacts == totalContacts,
                  page.map(\.id) == suggestionExpectedIDs,
                  page.allSatisfy(validContactPreview) else {
                rejectContactPage()
                return
            }
            suggestionVisible = page
            suggestionPagePending = false
            suggestionRequestID = nil
            suggestionExpectedIDs = []
        case "review-page":
            guard reviewPending, event.reviewId == pendingReviewID,
                  event.nextCursor == pendingReviewNextCursor else { return }
            sendReviewPage()
        case "review":
            guard let pendingReviewID, let pendingReviewIDs else { return }
            guard let eventReviewID = event.reviewId else {
                continueAfterReview = false
                clearPendingReview()
                reviewedSelectionIDs = nil
                selectionNeedsReview = true
                phase = .selecting
                errorMessage = "The local helper did not confirm this selection. Reinstall the matching signed companion before retrying review."
                return
            }
            guard eventReviewID == pendingReviewID else { return }
            let expectedSHA = pendingReviewSHA
            let expectedCount = pendingReviewPageIDs.count
            clearPendingReview()
            guard selectedIDs == pendingReviewIDs,
                  event.selectedContacts == pendingReviewIDs.count,
                  event.selectedContacts == expectedCount,
                  event.selectionSha256 == expectedSHA else {
                continueAfterReview = false
                reviewedSelectionIDs = nil
                selectionNeedsReview = true
                phase = .selecting
                errorMessage = "The selected contacts changed during review. Review them again before pairing."
                return
            }
            reviewedSelectionIDs = pendingReviewIDs
            pairCode = ""
            resetHandoff()
            selectionNeedsReview = false
            errorMessage = ""
            matchedCalls = event.matchedCalls ?? 0
            matchedMessages = event.matchedMessages ?? 0
            observedCallEarliest = event.observedCallEarliest ?? ""
            observedCallLatest = event.observedCallLatest ?? ""
            observedMessageEarliest = event.observedMessageEarliest ?? ""
            observedMessageLatest = event.observedMessageLatest ?? ""
            missingSources = event.missingSources ?? missingSources
            phase = .reviewing
            if continueAfterReview {
                continueAfterReview = false
                pairBrowser(automaticAccountNavigation: true)
            }
        case "pairing":
            guard phase == .reviewing, !reviewPending, !selectionNeedsReview,
                  reviewedSelectionIDs == selectedIDs else { return }
            guard pairingRequested || handoffID.isEmpty else { return }
            let automaticNavigation = navigateOnPairing
            if automaticNavigation {
                let validCode = event.pairCode?.utf8.count == 10 &&
                    event.pairCode?.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }) == true
                guard pairingRequested, validCode, event.port == 48751,
                      event.expiresInSeconds == 300,
                      event.handoffId.flatMap(UUID.init(uuidString:)) != nil else {
                    resetHandoff()
                    send(["action": "revoke"])
                    errorMessage = "The one-time account link was not ready. Your local review is intact. Choose Continue to account to try again."
                    return
                }
            }
            resetHandoff()
            errorMessage = ""
            pairCode = event.pairCode ?? ""
            pairPort = event.port ?? 0
            handoffID = event.handoffId.flatMap { UUID(uuidString: $0)?.uuidString.lowercased() } ?? ""
            pollHandoff()
            if automaticNavigation { accountNavigationID = UUID() }
        case "destination":
            guard !handoffID.isEmpty, event.handoffId?.lowercased() == handoffID,
                  phase == .reviewing, !reviewPending, !selectionNeedsReview,
                  reviewedSelectionIDs == selectedIDs else { return }
            if event.state == "pending", let intent = event.intentId.flatMap(UUID.init(uuidString:)),
               let email = event.email, let course = event.course,
               email.contains("@"), email.count <= 254, !course.isEmpty, course.count <= 160 {
                if destinationIntentID.isEmpty && !closedDestinationIntentIDs.contains(intent.uuidString.lowercased()) {
                    destinationIntentID = intent.uuidString.lowercased()
                    destinationEmail = email
                    destinationCourse = course
                    destinationApproved = false
                }
            } else if event.state == "approved", event.intentId?.lowercased() == destinationIntentID,
                      !destinationIntentID.isEmpty {
                destinationApproved = true
            } else if event.state == "denied", event.intentId?.lowercased() == destinationIntentID,
                      !destinationApproved {
                closedDestinationIntentIDs.insert(destinationIntentID)
                destinationIntentID = ""
                destinationEmail = ""
                destinationCourse = ""
                destinationApproved = false
            }
        case "handoff":
            guard !handoffID.isEmpty, event.handoffId?.lowercased() == handoffID,
                  phase == .reviewing, !reviewPending, !selectionNeedsReview,
                  reviewedSelectionIDs == selectedIDs else { return }
            if event.state == "received" { browserReceived = true }
            if event.state == "saved_pending_browser_receipt" {
                pairingRequested = false
                navigateOnPairing = false
                if selectionChangePending {
                    selectionChangePending = false
                    errorMessage = ""
                    handoffNotice = "Saving started before the selection could change. Keep this review open and finish in the browser."
                }
                accountSaveAwaitingBrowserReceipt = true
            }
            if event.state == "expired" {
                resetHandoff()
                errorMessage = "This browser pairing expired. Your local review remains available; create a new pairing when ready."
                return
            }
            if event.state == "saved" {
                pairingRequested = false
                navigateOnPairing = false
                selectionChangePending = false
                handoffNotice = ""
                handoffAcknowledged = true
                handoffPollTask?.cancel()
                handoffPollTask = nil
                phase = .completing
                if helperRunning { send(["action": "finish", "handoffId": handoffID]) }
            }
        case "residue":
            declineLocalCollection()
            residues = event.sessions ?? []
            sourceDiagnosticHistory = event.sourceDiagnosticHistory ?? []
            needsStorageReview = false
        case "diagnostic-storage":
            if event.state == "failed", event.code == "diagnostics_unavailable" {
                diagnosticRetention = .failed
            } else if event.state == "retained", let attempt = event.attemptId,
                      SourceDiagnosticAttempt.validID(attempt) {
                diagnosticRetention = .retained
            }
        case "cleared":
            declineLocalCollection()
            residues = []
            needsStorageReview = false
            phase = .idle
        case "backup-encryption-status":
            guard backupEncryptionStatus == .checking, localCollectionApproval == nil else { return }
            if event.backupEncrypted == true { backupEncryptionStatus = .encrypted }
            else if event.backupEncrypted == false { backupEncryptionStatus = .unencrypted }
            else { backupEncryptionStatus = .unknown }
            statusCheckFailureActive = backupEncryptionStatus == .unknown
        case "error":
            if activeHelperMode == "backup-encryption-status" &&
                backupEncryptionStatus != .checking { return }
            if backupEncryptionStatus == .checking, localCollectionApproval == nil {
                backupEncryptionStatus = .unknown
                backupEncryptionCheckCode = event.code
                if event.cleanupRequired == true {
                    needsStorageReview = true
                    phase = .error
                    errorMessage = "The setting check left app-created temporary data to inspect before collecting."
                }
                return
            }
            if event.code == "backup_encryption_disabled" { backupEncryptionStatus = .unencrypted }
            if event.code == "backup_encryption_unknown" || event.code == "backup_encryption_changed" {
                backupEncryptionStatus = .unknown
            }
            // A later cleanup/error frame must not replace the first terminal collection cause.
            if phase == .error && lastErrorCode != nil { return }
            lastErrorCode = event.code
            lastErrorStage = ["connecting", "backup", "processing", "review"].contains(event.stage ?? "") ? event.stage : nil
            lastDeviceStatus = event.code == "device_backup_failed" && event.deviceCode.map({ (-2_147_483_648...4_294_967_295).contains($0) }) == true ? event.deviceCode : nil
            if event.code == "selection" {
                clearPendingReview()
                reviewedSelectionIDs = nil
                selectionNeedsReview = true
                phase = .selecting
                errorMessage = "Choose supported contacts from this phone."
            } else if event.code == "bridge_unavailable" {
                if pairingRequested { resetHandoff() }
                errorMessage = "Local browser pairing could not start. Keep the review here or try again. The previous one-use link may no longer work."
            } else if event.code == "handoff_unconfirmed" {
                handoffAcknowledged = false
                phase = .reviewing
                errorMessage = "The browser has not confirmed this account save. Your local review remains available."
            } else {
                if event.cleanupRequired == true {
                    needsStorageReview = true
                    clearPrivateReview()
                    awaitHelperExit()
                }
                let cleanup = event.cleanupRequired == true
                    ? " Temporary phone data may remain. Check temporary data before reconnecting." : ""
                let deviceStatus: String
                if event.code == "device_backup_failed", let code = event.deviceCode,
                   (-2_147_483_648...4_294_967_295).contains(code) {
                    deviceStatus = " Device status: \(code)."
                } else {
                    deviceStatus = ""
                }
                fail(Self.message(for: event.code) + deviceStatus + cleanup)
            }
        default:
            process?.terminate()
            needsStorageReview = true
            awaitHelperExit()
            fail("The local helper returned an unknown response.")
        }
    }

    func finished(mode: String, code: Int32) {
        if mode == "backup-encryption-status" {
            if code != 0 || backupEncryptionStatus == .checking {
                backupEncryptionStatus = .unknown
                // A normal error frame precedes the helper's nonzero exit. Keep its
                // bounded reason so setup can explain storage, USB or Trust failures.
                backupEncryptionCheckCode = backupEncryptionCheckCode.flatMap {
                    SupportDiagnosticReporter.reportCodes.contains($0) ? $0 : nil
                } ?? "collection_failed"
                statusCheckFailureActive = true
            }
        } else { declineLocalCollection() }
        cancellationTask?.cancel()
        cancellationTask = nil
        contactSearchTask?.cancel()
        contactSearchTask = nil
        contactSearchPending = false
        clearPendingReview()
        outputHandle?.readabilityHandler = nil
        try? outputHandle?.close()
        outputHandle = nil
        operationID = nil
        process = nil
        activeHelperMode = nil
        try? inputHandle?.close()
        inputHandle = nil
        outputBuffer = Data()
        helperRunning = false
        if mode == "backup-encryption-status" && phase == .cancelling { phase = .idle }
        handoffPollTask?.cancel()
        handoffPollTask = nil
        if mode == "connect" && code == 0 && handoffAcknowledged && !needsStorageReview {
            clearPrivateReview()
            phase = .completed
        }
        if mode == "connect" && code != 0 && phase != .error && phase != .idle {
            fail(code == 130 ? "Collection was cancelled." : "Collection stopped. Check the phone and try again.")
        }
        if mode == "connect" && code == 0 && !handoffAcknowledged && phase != .error && phase != .idle {
            fail("The local helper stopped before this account save was confirmed. Keep this review and reconnect before trying again.")
        }
        if mode == "connect" && needsStorageReview { inspectResidue() }
        if mode == "backup-encryption-status" && needsStorageReview { inspectResidue() }
    }

    private func fail(_ message: String) {
        let failedSettingCheck = backupEncryptionStatus == .checking && statusCheckFailureActive
        if !failedSettingCheck { settingCheckAfterCollectionFailure = false }
        declineLocalCollection()
        if failedSettingCheck {
            backupEncryptionStatus = .unknown
            backupEncryptionCheckCode = "collection_failed"
        }
        clearSelectAll()
        clearSuggestions()
        clearPendingReview()
        reviewedSelectionIDs = nil
        if diagnosticReference.isEmpty { diagnosticReference = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8)) }
        let wallElapsed = collectionStartedAt.map { Int(max(0, min(172_800, Date().timeIntervalSince($0)))) } ?? 0
        errorElapsedSeconds = max(transferElapsedSeconds, wallElapsed)
        if lastErrorStage == nil {
            switch phase {
            case .transferring: lastErrorStage = "backup"
            case .processing, .completing: lastErrorStage = "processing"
            case .selecting, .reviewing: lastErrorStage = "review"
            default: lastErrorStage = "connecting"
            }
        }
        errorMessage = message
        phase = .error
    }

    var statusTitle: String {
        switch phase {
        case .idle: return "Connect your iPhone"
        case .connecting: return "Unlock and trust your iPhone"
        case .transferring: return "Receiving the local backup"
        case .passwordRequired: return "Existing backup password needed"
        case .processing: return "Finishing local collection"
        case .selecting: return "Choose the people to review"
        case .reviewing:
            if accountSaveAwaitingBrowserReceipt { return "Waiting for the browser's final save confirmation" }
            return browserReceived ? "Browser received the reviewed preview" : "Your local review is ready"
        case .completing: return "Account save acknowledged; finishing safely"
        case .completed: return "Saved in your account"
        case .cancelling: return "Stopping and checking temporary data"
        case .error: return settingCheckAfterCollectionFailure ? "Previous collection stopped" : "Collection stopped"
        }
    }

    var isCallHistorySetupFailure: Bool {
        lastErrorCode == "backup_encryption_disabled" || lastErrorCode == "backup_encryption_unknown"
    }

    var statusDetail: String {
        switch phase {
        case .idle, .connecting:
            return "Connect one iPhone by USB and unlock it to establish the connection. Respond to Apple's Trust/passcode prompt on the phone if shown. Existing trusted Wi-Fi pairing is used only when no cable is present. A later screen lock alone does not prove a failure; no security bypass is performed."
        case .error:
            return settingCheckAfterCollectionFailure
                ? "The error below is from the earlier attempt. The setting check below shows the most recent read; it did not start a backup."
                : "This collection did not produce a verified completion. Review the failure and last observed transfer below before starting a new approved attempt."
        case .transferring:
            return "Unrelated full-backup bytes are streamed and discarded; selected source databases stay local and temporary. Keep the cable connected and respond to any phone prompts. Data-aware guards allow larger active transfers; 15 minutes without any device response or sustained very slow progress can stop the session."
        case .passwordRequired:
            return "Use the existing password for this iPhone's encrypted computer backup. It is not your phone passcode. This app does not enable, disable or reset encryption."
        case .processing:
            return "The helper is finishing metadata reading and temporary-backup cleanup. Backup progress does not mean your review or account save is complete."
        case .selecting:
            return "\(totalContacts) contacts, \(availableCalls) calls and \(availableMessages) messages were observed in available sources. Choose contacts, then share them with your account."
        case .reviewing:
            if accountSaveAwaitingBrowserReceipt {
                return "The Mac received the first bound account-save receipt. Keep this review open until the browser confirms the final receipt; an interrupted reply may need account readback."
            }
            return browserReceived ? "A received preview is not the final account-save acknowledgment. Verify each source in the account and keep this review open until the matching final receipt arrives." : "Review the counts and observed dates. A one-time link only permits a local transfer to your browser; saving needs separate approval in your account."
        case .completing:
            return "The matching browser run acknowledged its account save. This window stays open until the owned helper finishes safely."
        case .completed:
            return "Your account confirmed the matching save, the local helper finished, and this Mac's private review copy was cleared. Your saved phone report is in your AMPLIFai account."
        case .cancelling:
            return "Wait for the owned helper to stop. If temporary phone data remains, inspect it here before reconnecting; cleanup never targets other backups."
        }
    }

    var recoverySteps: [String] {
        if needsStorageReview || !residues.isEmpty {
            return ["Choose Check temporary data, then review any marked app-created sessions.", "Remove only those sessions after explicit confirmation; do not delete other backups or change folder permissions."]
        }
        switch lastErrorCode {
        case "backup_password": return ["Check the existing encrypted computer-backup password with its owner.", "Reconnect and enter it only in this companion. Do not reset encryption to bypass this step."]
        case "backup_encryption_disabled": return ["No backup started. This iPhone now reports local backup encryption off, although the earlier check reported it on.", "If Calls matter, choose Encrypt local backup for this iPhone in Finder and keep its password, then start a fresh collection. Finder may create its own full backup and needs Mac storage."]
        case "backup_encryption_unknown": return ["No backup started because this companion could not confirm the iPhone's backup encryption setting.", "Check the setting in Finder, then choose Check iPhone again. You can instead choose Start without confirmed call history."]
        case "backup_encryption_changed": return ["The completed backup did not confirm encryption after the earlier preflight; no preview was admitted.", "Check the iPhone's current Finder backup setting, then start a new collection. Do not treat this run as saved data."]
        case "workspace_low_space": return ["Free space for retained source databases, their parsing copies and a further 2 GiB reserve on the temporary-storage volume.", "Keep other backups intact, then reconnect."]
        case "backup_host_space": return ["The iPhone refused the host-space answer before a verified backup completion. Check available space on the Mac's temporary-storage volume; do not delete other backups just to satisfy this app.", "If a normal Finder backup works on this Mac but collection repeats this code, share the safe reference with support before another long retry."]
        case "workspace_size_limit": return ["This helper reported an older backup-size limit. Update the signed companion before retrying."]
        case "source_capacity_limit", "source_read_capacity_limit", "contacts_capacity_limit", "backup_control_frame_limit", "backup_control_metadata_limit", "backup_control_path_limit":
            return ["A bounded control or source-reading safety limit was reached, not the former backup-size ceiling.", "Share the safe support code before another long run; do not reset phone encryption or remove other backups."]
        case "backup_control_invalid", "selected_payload_missing", "selected_payload_invalid", "selected_payload_size", "selected_payload_identity", "selected_payload_file_type", "selected_payload_length", "selected_payload_crypto", "selected_contacts_integrity", "contacts_schema", "unsupported_schema":
            return ["Share the safe support code and companion build with support before repeating a long collection.", "Do not reset backup encryption, remove other backups or treat the partial transfer as saved data."]
        case "workspace_unsafe": return ["Stop and contact support. Do not change permissions or delete an unverified folder."]
        case "workspace_unavailable", "workspace": return ["Reopen the signed companion and choose Check temporary data.", "If access still fails, reinstall the signed companion or contact support."]
        case "backup_stalled", "backup_time_limit", "backup_no_file_progress", "connection_timeout", "phone_connection", "trust_required":
            return ["Connect one phone by USB, keep it unlocked, and approve Trust on the phone if shown.", "Check the cable and use the phone's normal controls. Reconnect after the helper has stopped."]
        case "connection_lost": return ["Check the direct USB cable and port and respond to any iPhone Trust prompt.", "After the helper stops, compare a normal Finder backup on the same Mac. If Finder succeeds but this fails again, share the companion version and last transfer details with support."]
        case "device_backup_failed": return ["Note the device status and companion version.", "If the same iPhone cannot complete a normal Finder backup on this Mac, resolve that device backup issue first. Otherwise, share the status with support before repeating collection."]
        case "collection_failed": return ["Note the companion version and last observed transfer stage.", "Contact support before repeating the same long collection; the cause was not classified."]
        default: return []
        }
    }

    static func message(for code: String?) -> String {
        switch code {
        case "phone_connection": return "Use one previously paired iPhone on this network, or connect it by USB, unlock it, and try again."
        case "trust_required": return "Unlock the iPhone and approve its Trust prompt, then try again."
        case "backup_password": return "The existing encrypted-backup password was not accepted. Check it and retry."
        case "backup_encryption_disabled": return "Collection stopped before backup: this iPhone reported local backup encryption off. Calls may be unavailable. Check Finder if Calls matter."
        case "backup_encryption_unknown": return "Collection stopped before backup: the iPhone's backup encryption setting could not be confirmed. Check Finder, then check the iPhone again or choose Start without confirmed call history."
        case "backup_encryption_changed": return "The completed backup did not confirm encryption. No local preview or account save was made. Check Finder before a new collection."
        case "unsupported_schema": return "This iPhone backup format is not supported safely yet. No metadata was saved."
        case "backup_control_invalid": return "Required iPhone backup control metadata was missing or invalid. No metadata was saved."
        case "selected_payload_missing": return "A required selected source listed in the iPhone backup was unavailable. No metadata was saved."
        case "selected_payload_invalid": return "A selected iPhone source failed a size, type or decryption-integrity check. No metadata was saved."
        case "selected_payload_size": return "A required iPhone source had an invalid size in the backup manifest. No metadata was saved."
        case "selected_payload_identity": return "A required iPhone source had an invalid identity in the backup manifest. No metadata was saved."
        case "selected_payload_file_type": return "A required iPhone source was not a regular file. No metadata was saved."
        case "selected_payload_length": return "A required iPhone source did not match its manifest length. No metadata was saved."
        case "selected_payload_crypto": return "A required encrypted iPhone source failed its decryption-integrity check. No metadata was saved."
        case "selected_contacts_integrity": return "The required iPhone contact database was invalid. No metadata was saved."
        case "contacts_schema": return "This iPhone's contact database could not be interpreted safely. No metadata was saved."
        case "workspace_low_space": return "There is not enough free space for the next write, local parsing copies and the 2 GiB reserve. Free space on the temporary-storage volume, then retry."
        case "backup_host_space": return "The iPhone refused the backup receiver's space preflight. No metadata was saved. Check available Mac space and share the safe reference if it repeats."
        case "workspace_size_limit": return "This helper reported an older backup-size limit. Update the signed companion before retrying."
        case "source_capacity_limit": return "The backup control data or metadata count exceeded a supported safety bound. Collection stopped without uploading metadata. Contact support with the companion version."
        case "backup_control_frame_limit": return "An iPhone backup control frame exceeded the companion's safe parsing bound. No metadata was saved. Share the safe support code before another long run."
        case "backup_control_metadata_limit": return "A backup control file exceeded the companion's safe parsing bound. No metadata was saved. Share the safe support code before another long run."
        case "backup_control_path_limit": return "An iPhone backup path exceeded the companion's safe path bound. No metadata was saved. Share the safe support code before another long run."
        case "contacts_capacity_limit": return "The required contact source exceeded a safe local reader bound. No metadata was saved. Share the safe support code before another long run."
        case "source_read_capacity_limit": return "A phone source exceeded a safe local reader bound. No metadata was saved. Share the safe support code before another long run."
        case "workspace_unsafe": return "The app's temporary-storage folder could not be verified as private and app-owned. Nothing there was changed. Contact support; do not change folder permissions or delete other backups."
        case "workspace_unavailable", "workspace": return "The app could not access its private temporary storage. Reopen the app and check temporary data. If it still fails, reinstall the signed companion or contact support."
        case "workspace_cleanup", "cleanup_incomplete": return "Temporary phone data could not be fully removed. Check temporary data before reconnecting."
        case "connection_timeout", "timeout": return "The iPhone connection timed out. Keep it unlocked, check the cable or trusted Wi-Fi connection, then retry."
        case "connection_lost": return "The iPhone connection ended before backup completion. No metadata was saved. Check the cable and port, then reconnect after the helper stops."
        case "device_backup_failed": return "The iPhone reported a backup failure. No metadata was saved."
        case "backup_stalled": return "No backup data arrived for 15 minutes. Collection stopped. Check the cable, unlock the iPhone, then reconnect."
        case "backup_no_file_progress": return "The iPhone kept responding but no backup file data arrived for one hour. Collection stopped without saving metadata. Check the phone's normal backup status before retrying."
        case "backup_time_limit": return "The backup made too little file-data progress within its data-aware time budget. Collection stopped. Use a direct USB cable and retry; if it repeats, contact support."
        case "cancelled": return "Collection was cancelled. No metadata was saved."
        default: return "Collection stopped without a verified completion receipt. The cause was not classified; note the stage below before retrying."
        }
    }
}
