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
    /// The native per-run local collection approval, now composed only from a verified server run approval.
    /// Earlier local-only versions (native-local-collection-v5 and before) never authorize a collection here.
    static let collectionDisclosureVersion = "native-run-v1"
    /// The only account origin the app opens (the helper bridge's ACCOUNT_ORIGIN).
    static let accountOrigin = "https://amplifai.satoris.ai"
    /// Timing, kept distinct (server values are enforced by the server; the app only stays truthful about them):
    /// - start-key bootstrap: one use, 10 minutes after the website issues it (the website's approval window is 1 hour);
    /// - Start claim replay: the exact claim is read back for 10 minutes after the claim, so Start must happen then;
    /// - durable run: 12 hours after the claim, covering a long collection, review and bind;
    /// - initial helper bridge: 5 minutes from `pair` (after review and Continue) to browser release;
    /// - website connect intent: 5 minutes from creation to consume;
    /// - active paged transfer: 2 hours without activity, refreshed by every page, keepalive and acknowledgement.
    nonisolated static let runStartWindow: TimeInterval = 10 * 60
    static var accountStartURL: URL { URL(string: accountOrigin + "/phone/account")! }
    static func accountContinueURL(enrollmentID: String) -> URL? {
        guard PhoneRunContract.isID(enrollmentID) else { return nil }
        var components = URLComponents()
        components.scheme = "https"
        components.host = "amplifai.satoris.ai"
        components.path = "/phone/account"
        components.queryItems = [URLQueryItem(name: "continue", value: "reviewed"),
                                 URLQueryItem(name: "enrollmentId", value: enrollmentID),
                                 URLQueryItem(name: "week", value: "phone")]
        return components.url
    }
    static let runRefusedMessage = "This approval can no longer start a collection. Nothing was collected. Approve again in your AMPLIFai account."
    static let runUncertainMessage = "Your approval could not be checked with AMPLIFai. Nothing was collected. Try again, or approve again in your AMPLIFai account."
    static let runExpiredMessage = "This approval has expired. Nothing was collected. Approve again in your AMPLIFai account."
    static let bindRefusedMessage = "Your AMPLIFai account did not accept this collection for saving. Nothing was sent. To try again, start a new collection from your AMPLIFai account."
    /// One bounded message for both helper expiries: the five-minute unclaimed pairing and the post-release
    /// two-hour transfer deadline (after which some sources may already be saved in the account).
    static let transferExpiredMessage = "This connection expired. Nothing more can be sent from this collection. Check your account for any sources already saved. Starting again needs a new collection."
    static let bindUncertainMessage = "Your AMPLIFai account has not confirmed this collection yet. Nothing was sent. Try again."
    static let missingRunMessage = "This collection has no approval from your AMPLIFai account. Nothing was sent. To try again, start a new collection from your AMPLIFai account."
    /// Link and navigation problems that a matching browser receipt for the same handoff makes obsolete.
    static let navigationFailedMessage = "The account page did not open automatically. Your review is kept on this Mac. Choose Continue in browser."
    static let bridgeUnavailableMessage = "Local browser pairing could not start. Keep the review here or try again. The previous one-use link may no longer work."
    static let handoffUnconfirmedMessage = "The browser has not confirmed this account save. Your local review remains available."
    private static let handoffScopedMessages: Set<String> = [navigationFailedMessage, bridgeUnavailableMessage, handoffUnconfirmedMessage]
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
        /// The verified server run this local approval was composed from, and its disclosure.
        let serverRunID: String
        let serverApprovalID: String
        let serverDisclosureVersion: String
    }
    /// The verified server run approval for this one collection. The capability is generated here and kept.
    struct PhoneRun: Equatable {
        let runID: String
        let capability: String
        /// The exact prepared claim, resent unchanged right before collection starts to revalidate the run.
        let claimRequest: PhoneRunClaimRequest
        let claim: PhoneRunClaim
        let returnBrowserBundleID: String?
        /// The course and categories the run saves to now; a replacement approval may update them, never the
        /// account or enrollment.
        var course: String
        var categories: [String]
        /// When this app first sent the claim (conservative: never later than the server's claim time).
        let claimedAt: Date
        var collectionStarted = false
        var boundHandoffID: String?
        var boundPayloadSha256: String?

        /// Start revalidation replays the exact claim, which the server accepts for 10 minutes after the claim.
        var startDeadline: Date { claimedAt.addingTimeInterval(CollectorModel.runStartWindow) }
        var email: String { claim.email }
        var enrollmentID: String { claim.enrollmentID }
    }
    enum RunApproval: Equatable { case none, claiming, uncertain, refused, approved }
    enum RunBinding: Equatable { case none, binding, bound, uncertain, refused }
    /// Revalidation of the run immediately before the helper launches.
    enum StartConfirmation: Equatable { case none, confirming, uncertain }
    struct AccountNavigation: Equatable {
        let id: UUID
        let url: URL
        let handoffID: String
    }
    enum Phase: Equatable {
        case idle, connecting, transferring, passwordRequired, processing
        case selecting, reviewing, completing, completed, cancelling, error
    }
    enum DiagnosticDelivery: Equatable { case idle, sending, sent, failed }

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
    private var finishAttemptCount = 0
    @Published private(set) var completionNeedsRetry = false
    private static let maximumFinishAttempts = 3
    @Published private(set) var destinationIntentID = ""
    @Published private(set) var destinationEmail = ""
    @Published private(set) var destinationCourse = ""
    @Published private(set) var destinationApproved = false
    /// The automatic decision sent for the current intent (true = approve after an exact verified match,
    /// false = deny after a refusal or mismatch), awaiting the helper's answer. Never sent twice for one intent.
    @Published private(set) var submittedDestinationDecision: Bool?
    var destinationDecisionPending: Bool { submittedDestinationDecision != nil }
    /// The intent this app approved after verification, kept so a matching receipt can reconcile a lost answer.
    private var approvedDestinationIntentID = ""
    /// Only definitive outcomes close an intent. Transport uncertainty retains the exact prepared request.
    private var verifiedDestinationIntentIDs: Set<String> = []
    private var pendingVerify: PhoneRunVerifyRequest?
    private var verifyAttemptCount = 0
    private var verifyRetryAfter = Date.distantPast
    private var verifyDeadline = Date.distantPast
    private static let maximumVerifyRounds = 3
    @Published private(set) var destinationVerificationPending = false
    /// Verification stayed uncertain after exact retries. It remains undecided and can be reconciled safely.
    @Published private(set) var destinationVerificationUncertain = false
    @Published private(set) var browserReceived = false
    @Published private(set) var accountSaveAwaitingBrowserReceipt = false
    private var closedDestinationIntentIDs: Set<String> = []
    @Published private(set) var accountNavigation: AccountNavigation?
    var accountNavigationID: UUID? { accountNavigation?.id }
    /// The one-approval run. A collection starts only from a verified, current, unstarted run.
    @Published private(set) var phoneRun: PhoneRun?
    @Published private(set) var runApproval: RunApproval = .none
    @Published private(set) var runApprovalMessage = ""
    @Published private(set) var runBinding: RunBinding = .none
    @Published private(set) var startConfirmation: StartConfirmation = .none
    private var startConfirmationToken: UUID?
    private var startPreparation: CallHistoryPreparation?
    private var pendingClaim: PhoneRunClaimRequest?
    private var pendingClaimBrowser: String?
    /// A different link that arrived while an approved run waits for Start: claimed with its own capability while
    /// the current grant stays in place, and it replaces the grant only if its claim succeeds.
    private var candidateClaim: PhoneRunClaimRequest?
    private var candidateClaimBrowser: String?
    private var candidateClaimStartedAt = Date.distantPast
    /// The candidate's claim was not confirmed; its exact request is kept for an identical retry.
    @Published private(set) var candidateUncertain = false
    @Published private(set) var candidateNotice = ""
    private var pendingClaimStartedAt = Date.distantPast
    private var pendingBind: PhoneRunBindRequest?
    private var pairingPayloadSha256 = ""
    private var startAfterSettingCheck = false
    private let runAuthority: RunAuthority?
    /// Clock seam for checks; the server remains the authority for every expiry.
    var now: () -> Date = Date.init
    /// Handoff status polling interval; checks shorten it.
    var handoffPollNanoseconds: UInt64 = 2_000_000_000
    /// Test seam: every helper event, in order, after it is applied.
    var eventObserver: ((AgentEvent) -> Void)?
    @Published private(set) var startupChecksFinished: Bool
    var onReadyForRunLinks: (() -> Void)?
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
    /// Without a `runAuthority` no approval can be claimed, bound or verified, so nothing can be collected or
    /// sent; the production app passes `PhoneRunAuthorityClient.production()`. Checks never reach the network.
    init(commandWriter: ((Data) -> Void)? = nil, bundleInformation: [String: Any]? = Bundle.main.infoDictionary,
         runAuthority: RunAuthority? = nil, awaitsStartupInspection: Bool = false) {
        self.commandWriter = commandWriter
        self.runAuthority = runAuthority
        startupChecksFinished = !awaitsStartupInspection
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

    /// A verified run for this collection that has not started and has not expired.
    var hasStartableRun: Bool { runStartWindowOpen(at: now()) }

    /// Before Start, a run is startable only inside the 10-minute claim replay window; once collection has
    /// started, the run stays valid for its 12 hours.
    func runStartWindowOpen(at date: Date) -> Bool {
        guard runApproval == .approved, let run = phoneRun else { return false }
        return !run.collectionStarted && date < run.startDeadline && date < run.claim.expiresAt
    }

    /// The Start window passed without Start: show the approve-again state instead of a Start that would fail.
    func refreshRunStartWindow() {
        guard runApproval == .approved, let run = phoneRun, !run.collectionStarted, !helperRunning,
              startConfirmation != .confirming, candidateClaim == nil,
              !startAfterSettingCheck, !hasStartableRun else { return }
        declineLocalCollection()
        refuseRun(Self.runRefusedMessage)
    }

    private var hasCurrentCollectionApproval: Bool {
        guard let approval = localCollectionApproval, hasStartableRun, let run = phoneRun else { return false }
        return approval.runID == collectionApprovalRunID &&
            approval.disclosureVersion == Self.collectionDisclosureVersion && approval.scopes == Self.localCollectionScopes &&
            approval.serverRunID == run.runID && approval.serverApprovalID == run.claim.approvalID &&
            approval.serverDisclosureVersion == PhoneRunContract.disclosureVersion
    }

    var canConnect: Bool { canPrepareCollection && hasCurrentCollectionApproval }

    /// Composes this run's local collection approval from the verified server run. There is no local consent
    /// control: the owner's one agreement was given in the account. Nothing old is reused, and the approval is
    /// consumed at launch, so it never authorizes a later collection.
    @discardableResult
    func composeRunCollectionApproval(callHistoryPreparation: CallHistoryPreparation) -> Bool {
        guard canPrepareCollection, hasStartableRun, candidateClaim == nil, let run = phoneRun,
              run.claim.disclosureVersion == PhoneRunContract.disclosureVersion,
              backupEncryptionStatus != .notChecked, backupEncryptionStatus != .checking else { return false }
        if callHistoryPreparation == .confirmedEncryptedBackup && backupEncryptionStatus != .encrypted { return false }
        localCollectionApproval = LocalCollectionApproval(
            runID: collectionApprovalRunID, disclosureVersion: Self.collectionDisclosureVersion,
            approvedAt: now(), scopes: Self.localCollectionScopes,
            callHistoryPreparation: callHistoryPreparation,
            serverRunID: run.runID, serverApprovalID: run.claim.approvalID,
            serverDisclosureVersion: PhoneRunContract.disclosureVersion
        )
        return true
    }

    // MARK: One-approval run

    /// Start-up checks (the first temporary-data inspection) have finished, so a buffered link may be applied.
    var readyForRunLinks: Bool { startupChecksFinished }

    /// A link is admitted only when nothing is running or in progress: no helper process, no collection,
    /// no review or handoff, and no claim in flight. A verified run that has not started may be replaced.
    var admitsRunLink: Bool {
        readyForRunLinks && runAuthority != nil && !helperRunning && process == nil && runApproval != .claiming &&
            !updateInstallationInProgress && !hasUnsavedReview && handoffID.isEmpty && !pairingRequested &&
            phoneRun?.collectionStarted != true && localCollectionApproval == nil && !startAfterSettingCheck &&
            startConfirmation != .confirming && candidateClaim == nil &&
            (phase == .idle || phase == .error || phase == .completed)
    }

    private func completeStartupChecks() {
        guard !startupChecksFinished else { return }
        startupChecksFinished = true
        onReadyForRunLinks?()
    }

    /// Redeems the link's one-use bootstrap with a fresh capability this app generates and keeps.
    @discardableResult
    func admitRunLink(_ item: PendingRunLink) -> Bool {
        guard admitsRunLink, let authority = runAuthority else { return false }
        // The same link again (the owner opened the app twice) is a duplicate: the grant, its capability and
        // any prepared claim stay exactly as they are.
        if let run = phoneRun, run.runID == item.link.runID, run.claimRequest.bootstrap == item.link.bootstrap { return false }
        if let pending = pendingClaim, pending.runID == item.link.runID, pending.bootstrap == item.link.bootstrap { return false }
        var capability = PhoneRunContract.newCapability()
        if capability == item.link.bootstrap { capability = PhoneRunContract.newCapability() }
        guard let capability, let request = PhoneRunClaimRequest(runID: item.link.runID, bootstrap: item.link.bootstrap,
                                                                 capability: capability) else { return false }
        if runApproval == .approved, let run = phoneRun, !run.collectionStarted {
            candidateClaim = request
            candidateClaimBrowser = item.returnBrowserBundleID
            candidateClaimStartedAt = now()
            candidateUncertain = false
            candidateNotice = ""
            authority.claim(request) { [weak self] outcome in self?.candidateClaimFinished(request, outcome) }
            return true
        }
        endPhoneRun()
        if phase == .completed || phase == .error { errorMessage = ""; phase = .idle }
        pendingClaim = request
        pendingClaimBrowser = item.returnBrowserBundleID
        pendingClaimStartedAt = now()
        runApproval = .claiming
        authority.claim(request) { [weak self] outcome in self?.claimFinished(request, outcome) }
        return true
    }

    /// Try again after an uncertain claim: the identical request body, the same capability.
    func retryRunClaim() {
        guard runApproval == .uncertain, let request = pendingClaim, let authority = runAuthority else { return }
        runApproval = .claiming
        runApprovalMessage = ""
        authority.claim(request) { [weak self] outcome in self?.claimFinished(request, outcome) }
    }

    private func claimFinished(_ request: PhoneRunClaimRequest, _ outcome: PhoneRunOutcome<PhoneRunClaim>) {
        guard pendingClaim == request, runApproval == .claiming else { return }
        switch outcome {
        case .accepted(let claim):
            guard claimIsCurrent(claim) else {
                refuseRun(Self.runRefusedMessage)
                return
            }
            phoneRun = PhoneRun(runID: request.runID, capability: request.capability, claimRequest: request, claim: claim,
                                returnBrowserBundleID: pendingClaimBrowser, course: claim.course,
                                categories: claim.categories, claimedAt: pendingClaimStartedAt)
            pendingClaim = nil
            runApprovalMessage = ""
            runApproval = .approved
            declineLocalCollection()
        case .refused:
            refuseRun(Self.runRefusedMessage)
        case .uncertain, .malformed:
            // An unreadable success can follow a committed one-use claim: keep the exact prepared request
            // (same run, bootstrap and capability bytes) so Try again reads the same claim back.
            runApproval = .uncertain
            runApprovalMessage = Self.runUncertainMessage
        }
    }

    private func claimIsCurrent(_ claim: PhoneRunClaim) -> Bool {
        claim.disclosureVersion == PhoneRunContract.disclosureVersion && claim.expiresAt > now() &&
            claim.expiresAt <= now().addingTimeInterval(PhoneRunContract.runLifetime + 3600)
    }

    /// Try the newer link's claim again with its identical bytes; the current grant stays as it is.
    func retryCandidateClaim() {
        guard candidateUncertain, let request = candidateClaim, let authority = runAuthority else { return }
        candidateUncertain = false
        candidateNotice = ""
        authority.claim(request) { [weak self] outcome in self?.candidateClaimFinished(request, outcome) }
    }

    private func discardCandidate() {
        candidateClaim = nil
        candidateClaimBrowser = nil
        candidateUncertain = false
    }

    private func candidateClaimFinished(_ request: PhoneRunClaimRequest, _ outcome: PhoneRunOutcome<PhoneRunClaim>) {
        guard candidateClaim == request else { return }
        // Replace only an approved run that is still idle; once the current grant started, the candidate is moot.
        guard runApproval == .approved, let current = phoneRun, !current.collectionStarted, !helperRunning,
              process == nil, !startAfterSettingCheck, startConfirmation != .confirming,
              localCollectionApproval == nil else {
            discardCandidate()
            return
        }
        switch outcome {
        case .accepted(let claim) where claimIsCurrent(claim):
            // The previous Start may have exhausted its replies. It did not launch a collection, so this
            // independently verified approval replaces it atomically and makes every old callback obsolete.
            startConfirmationToken = nil
            startConfirmation = .none
            startPreparation = nil
            phoneRun = PhoneRun(runID: request.runID, capability: request.capability, claimRequest: request, claim: claim,
                                returnBrowserBundleID: candidateClaimBrowser, course: claim.course,
                                categories: claim.categories, claimedAt: candidateClaimStartedAt)
            discardCandidate()
            candidateNotice = ""
            declineLocalCollection()
        case .accepted, .refused:
            // A definite answer: the candidate is discarded and the current grant is unchanged.
            discardCandidate()
            candidateNotice = "A newer approval link could not be used. The approval shown here is unchanged."
        case .uncertain, .malformed:
            // The candidate may have committed its one-use claim: keep its exact request for an identical retry.
            candidateUncertain = true
            candidateNotice = "A newer approval link could not be confirmed. The approval shown here is unchanged."
        }
    }

    private func refuseRun(_ message: String) {
        phoneRun = nil
        pendingClaim = nil
        startConfirmation = .none
        startConfirmationToken = nil
        startPreparation = nil
        runApproval = .refused
        runApprovalMessage = message
    }

    /// Not now: forget this verified, unstarted run. Nothing was collected.
    func dismissRunApproval() {
        guard phoneRun?.collectionStarted != true, !helperRunning else { return }
        endPhoneRun()
        declineLocalCollection()
    }

    /// Ends this app's use of the run: completion, discard, or a collection that stopped.
    private func endPhoneRun() {
        phoneRun = nil
        pendingClaim = nil
        pendingClaimBrowser = nil
        pendingBind = nil
        runApproval = .none
        runApprovalMessage = ""
        runBinding = .none
        startAfterSettingCheck = false
        startConfirmation = .none
        startConfirmationToken = nil
        startPreparation = nil
        discardCandidate()
        candidateNotice = ""
    }

    var returnBrowserBundleID: String? { phoneRun?.returnBrowserBundleID ?? pendingClaimBrowser }

    /// The run's categories describe what may be saved to the account. The unchanged helper still reads contacts,
    /// calls and message metadata for the local review; this label never claims a smaller local read.
    static func approvedToSaveLabel(_ categories: [String]) -> String {
        let names = PhoneRunContract.categories.filter(categories.contains).map { category -> String in
            switch category {
            case "contacts": return "chosen contacts"
            case "calls": return "their call metadata"
            default: return "their message metadata"
            }
        }
        return "Approved to save: " + names.joined(separator: ", ")
    }

    /// Continue can still pair and bind for this run.
    var canContinueToAccount: Bool {
        phoneRun?.collectionStarted == true && phoneRun?.boundHandoffID == nil && runBinding != .refused
    }

    /// The account page for this run's enrollment, or the account start page.
    var accountContinueURL: URL {
        phoneRun.flatMap { Self.accountContinueURL(enrollmentID: $0.enrollmentID) } ?? Self.accountStartURL
    }

    /// Start collection is an action, not consent: it runs the read-only iPhone setting check and then starts.
    /// An unencrypted or unconfirmed setting stops here for Finder guidance or an explicit available-data choice.
    @discardableResult
    func startCollection() -> Bool {
        refreshRunStartWindow()
        guard hasStartableRun, canPrepareCollection, localCollectionApproval == nil,
              candidateClaim == nil, startConfirmation == .none else { return false }
        guard beginBackupEncryptionStatusCheck() else { return false }
        startAfterSettingCheck = true
        launch(mode: "backup-encryption-status")
        return true
    }

    /// The owner chose to continue with available data after seeing Finder guidance. This is not new consent:
    /// the same verified account run still provides authority, and Start revalidates it immediately below.
    @discardableResult
    func startCollectionWithAvailableData() -> Bool {
        refreshRunStartWindow()
        guard backupEncryptionStatus == .unencrypted || backupEncryptionStatus == .unknown,
              !helperRunning, process == nil, hasStartableRun,
              canPrepareCollection, candidateClaim == nil, startConfirmation == .none else { return false }
        confirmRunThenStart(.continueWithoutConfirmedCoverage)
        return true
    }

    @discardableResult
    func startWithoutConfirmedCallHistory() -> Bool {
        guard backupEncryptionStatus == .unknown else { return false }
        return startCollectionWithAvailableData()
    }

    private func startAfterCheckIfReady() {
        guard startAfterSettingCheck else { return }
        startAfterSettingCheck = false
        guard !needsStorageReview, process == nil, let preparation = settingCallHistoryPreparation,
              backupEncryptionStatus == .encrypted else { return }
        guard hasStartableRun, canPrepareCollection else { refreshRunStartWindow(); return }
        confirmRunThenStart(preparation)
    }

    /// Right before the helper launches, the exact original claim is sent again (same run, bootstrap and
    /// capability bytes, no new endpoint). The server reads the same claim back only while the run is not revoked
    /// or expired, its approval is the newest and still current (session, enrollment, course, consent receipt,
    /// agreement, retention and reset fences unchanged), and the claim is at most 10 minutes old. So Start must
    /// happen within 10 minutes of the claim; otherwise the owner approves again in the account.
    private func confirmRunThenStart(_ preparation: CallHistoryPreparation) {
        guard let run = phoneRun, let authority = runAuthority else { return }
        let token = UUID()
        startConfirmationToken = token
        startPreparation = preparation
        startConfirmation = .confirming
        authority.claim(run.claimRequest) { [weak self] outcome in
            self?.runConfirmedForStart(token: token, runID: run.runID, outcome)
        }
    }

    /// Not confirmed: resend the identical claim bytes.
    func retryStartConfirmation() {
        guard startConfirmation == .uncertain, let preparation = startPreparation, hasStartableRun,
              candidateClaim == nil, canPrepareCollection, !helperRunning, process == nil else { return }
        confirmRunThenStart(preparation)
    }

    private func runConfirmedForStart(token: UUID, runID: String, _ outcome: PhoneRunOutcome<PhoneRunClaim>) {
        // A late answer for an earlier attempt, another run, or after cancel or Not now starts nothing.
        guard startConfirmationToken == token, startConfirmation == .confirming, let run = phoneRun,
              run.runID == runID, !run.collectionStarted, let preparation = startPreparation else { return }
        startConfirmationToken = nil
        switch outcome {
        case .accepted(let claim):
            let unchanged = claim.approvalID == run.claim.approvalID && claim.enrollmentID == run.claim.enrollmentID &&
                claim.email == run.claim.email && claim.course == run.claim.course &&
                claim.categories == run.claim.categories && claim.disclosureVersion == PhoneRunContract.disclosureVersion &&
                now() < claim.expiresAt
            guard unchanged else {
                declineLocalCollection()
                refuseRun(Self.runRefusedMessage)
                return
            }
            startConfirmation = .none
            startPreparation = nil
            guard canPrepareCollection, !helperRunning, process == nil,
                  composeRunCollectionApproval(callHistoryPreparation: preparation) else { return }
            connect()
        case .refused:
            declineLocalCollection()
            refuseRun(Self.runRefusedMessage)
        case .uncertain, .malformed:
            startConfirmation = .uncertain
        }
    }

    /// The read-only setting check reports the preparation facts; it cannot guarantee source availability.
    var settingCallHistoryPreparation: CallHistoryPreparation? {
        switch backupEncryptionStatus {
        case .encrypted: return .confirmedEncryptedBackup
        case .unencrypted, .unknown: return .continueWithoutConfirmedCoverage
        case .notChecked, .checking: return nil
        }
    }

    func declineLocalCollection() {
        localCollectionApproval = nil
        collectionApprovalRunID = UUID()
        backupEncryptionStatus = .notChecked
        backupEncryptionCheckCode = nil
    }

    @discardableResult
    func beginBackupEncryptionStatusCheck() -> Bool {
        guard canPrepareCollection, localCollectionApproval == nil, startConfirmation == .none,
              candidateClaim == nil else { return false }
        if phase == .error { settingCheckAfterCollectionFailure = true }
        backupEncryptionStatus = .checking
        backupEncryptionCheckCode = nil
        statusCheckFailureActive = true
        return true
    }

    func checkBackupEncryptionStatus() {
        guard beginBackupEncryptionStatusCheck() else { return }
        startAfterSettingCheck = false
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
    /// The current handoff is bound to this run on the server.
    var handoffBound: Bool {
        !handoffID.isEmpty && runBinding == .bound && phoneRun?.boundHandoffID == handoffID
    }
    /// The website owns the flow from the bound handoff until the run ends.
    var browserOwnsFlow: Bool { handoffBound || (phoneRun?.boundHandoffID != nil && hasUnsavedReview) }
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
    /// The run binds one handoff once, so a selection cannot change after a bind was attempted for it.
    var canRequestSelectionChange: Bool {
        phase == .reviewing && commandReady && !handoffID.isEmpty &&
            reviewedSelectionIDs == selectedIDs && !pairingRequested && !selectionChangePending &&
            !accountSaveAwaitingBrowserReceipt && !handoffAcknowledged &&
            runBinding == .none && phoneRun?.boundHandoffID == nil
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
    /// The run bound this handoff: the account opens and owns save, progress, the report and recovery. This does
    /// not wait for `received` (the helper reports it only after every page was delivered, after the website
    /// saved them). The helper keeps running for the receipts.
    var browserHandoffReady: Bool {
        phase == .reviewing && handoffBound && !handoffAcknowledged && errorMessage.isEmpty
    }
    /// Approve · Collect · Choose · Save & report: 0...3 is the current step, 4 means every step finished.
    /// A verified account agreement completes Approve even while the local setting check is idle or cancelled.
    var journeyStep: Int {
        switch phase {
        case .idle, .error: return hasUnsavedReview ? 2 : (runApproval == .approved && phoneRun != nil ? 1 : 0)
        case .cancelling: return backupSettingCheckInProgress && phoneRun == nil ? 0 : 1
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
              reviewedSelectionIDs == selectedIDs,
              // Only this run's started collection can pair, and the run binds one handoff once.
              phoneRun?.collectionStarted == true,
              runBinding == .none || runBinding == .uncertain, phoneRun?.boundHandoffID == nil else { return }
        // Keep the old bridge binding until the helper confirms replacement.
        // An account receipt can race this command and cause the helper to
        // reject replacement while the original browser ACK remains live.
        pairingRequested = true
        navigateOnPairing = automaticAccountNavigation
        send(["action": "pair"])
    }

    func accountNavigationFailed() {
        guard phase == .reviewing, !handoffID.isEmpty else { return }
        errorMessage = Self.navigationFailedMessage
    }

    /// After the helper's pairing: associate this handoff and its digest with the run, once.
    private func bindHandoff() {
        guard let run = phoneRun, run.collectionStarted, let authority = runAuthority,
              let request = PhoneRunBindRequest(runID: run.runID, capability: run.capability, handoffID: handoffID,
                                                payloadSha256: pairingPayloadSha256) else {
            refuseBinding(Self.missingRunMessage)
            return
        }
        pendingBind = request
        runBinding = .binding
        authority.bind(request) { [weak self] outcome in self?.bindFinished(request, outcome) }
    }

    /// Try again after an uncertain bind: the identical request for the same handoff.
    func retryRunBinding() {
        guard runBinding == .uncertain, let request = pendingBind, request.handoffID == handoffID,
              let authority = runAuthority else { return }
        runBinding = .binding
        errorMessage = ""
        authority.bind(request) { [weak self] outcome in self?.bindFinished(request, outcome) }
    }

    private func bindFinished(_ request: PhoneRunBindRequest, _ outcome: PhoneRunOutcome<PhoneRunDestination>) {
        guard pendingBind == request, runBinding == .binding, var run = phoneRun, run.runID == request.runID else { return }
        guard handoffID == request.handoffID, phase == .reviewing else {
            // The pairing ended while binding; a late answer cannot revive it.
            runBinding = .uncertain
            return
        }
        switch outcome {
        case .accepted(let destination):
            // Account and enrollment must be exactly the approved run's. A replacement approval in the account
            // may change the course label or narrow/alter categories; it is shown, never widened silently.
            guard destination.enrollmentID == run.enrollmentID, destination.email == run.email else {
                refuseBinding(Self.bindRefusedMessage)
                return
            }
            run.course = destination.course
            run.categories = destination.categories
            run.boundHandoffID = request.handoffID
            run.boundPayloadSha256 = request.payloadSha256
            phoneRun = run
            pendingBind = nil
            runBinding = .bound
            if navigateOnPairing, let url = Self.accountContinueURL(enrollmentID: run.enrollmentID) {
                accountNavigation = AccountNavigation(id: UUID(), url: url, handoffID: request.handoffID)
            }
            if !destinationIntentID.isEmpty { verifyDestination() }
        case .refused:
            refuseBinding(Self.bindRefusedMessage)
        case .uncertain, .malformed:
            // The bind may have committed: keep the same body and the helper pairing, revoke nothing.
            runBinding = .uncertain
            errorMessage = Self.bindUncertainMessage
        }
    }

    /// A definite refusal: close the helper's bridge so no browser can connect, keep the local review.
    private func refuseBinding(_ message: String) {
        pendingBind = nil
        resetHandoff()
        send(["action": "revoke"])
        runBinding = .refused
        errorMessage = message
    }

    /// The helper reported a pending connect intent: check it against the run before answering automatically.
    /// Polls can reconcile an uncertain response, with a bounded backoff and the identical request/capability.
    private func verifyDestination() {
        guard handoffBound, let run = phoneRun, let handoff = run.boundHandoffID, let digest = run.boundPayloadSha256,
              !destinationIntentID.isEmpty, !destinationApproved, submittedDestinationDecision == nil,
              !destinationVerificationPending, !verifiedDestinationIntentIDs.contains(destinationIntentID),
              let authority = runAuthority else { return }
        if pendingVerify == nil {
            guard let request = PhoneRunVerifyRequest(runID: run.runID, capability: run.capability,
                                                      intentID: destinationIntentID, handoffID: handoff,
                                                      payloadSha256: digest) else { return }
            pendingVerify = request
            verifyAttemptCount = 0
            verifyRetryAfter = .distantPast
            // The server enforces the actual intent expiry. This local ceiling cannot extend it.
            verifyDeadline = min(now().addingTimeInterval(5 * 60), run.claim.expiresAt)
        }
        guard let request = pendingVerify, request.runID == run.runID, request.intentID == destinationIntentID,
              request.handoffID == handoff, request.payloadSha256 == digest,
              verifyAttemptCount < Self.maximumVerifyRounds, now() >= verifyRetryAfter,
              now() < verifyDeadline else { return }
        verifyAttemptCount += 1
        destinationVerificationPending = true
        destinationVerificationUncertain = false
        let pendingEmail = destinationEmail
        let pendingCourse = destinationCourse
        authority.verifyIntent(request) { [weak self] outcome in
            self?.verifyFinished(request, pendingEmail: pendingEmail, pendingCourse: pendingCourse, outcome)
        }
    }

    /// An owner/browser-directed retry after automatic retries were exhausted. It keeps the immutable request;
    /// no fresh approval, handoff, capability or selection is created from an uncertain answer.
    var canRetryDestinationVerification: Bool {
        destinationVerificationUncertain && !destinationVerificationPending && !destinationApproved &&
            submittedDestinationDecision == nil && pendingVerify?.intentID == destinationIntentID &&
            !destinationIntentID.isEmpty && now() < verifyDeadline && handoffBound
    }

    func retryDestinationVerification() {
        guard canRetryDestinationVerification else { return }
        verifyAttemptCount = 0
        verifyRetryAfter = .distantPast
        verifyDestination()
    }

    private func clearDestinationVerification() {
        pendingVerify = nil
        verifyAttemptCount = 0
        verifyRetryAfter = .distantPast
        verifyDeadline = .distantPast
        destinationVerificationPending = false
        destinationVerificationUncertain = false
    }

    private func verifyFinished(_ request: PhoneRunVerifyRequest, pendingEmail: String, pendingCourse: String,
                                _ outcome: PhoneRunOutcome<PhoneRunDestination>) {
        guard pendingVerify == request, destinationVerificationPending,
              handoffID == request.handoffID, destinationIntentID == request.intentID,
              destinationEmail == pendingEmail, destinationCourse == pendingCourse,
              !destinationApproved, submittedDestinationDecision == nil, var run = phoneRun,
              run.runID == request.runID else { return }
        destinationVerificationPending = false
        switch outcome {
        case .accepted(let verified):
            verifiedDestinationIntentIDs.insert(request.intentID)
            let exact = now() < verifyDeadline && verified.email == pendingEmail && verified.course == pendingCourse &&
                verified.enrollmentID == run.enrollmentID && verified.email == run.email &&
                run.boundHandoffID == request.handoffID && run.boundPayloadSha256 == request.payloadSha256 &&
                pairingPayloadSha256 == request.payloadSha256
            if exact {
                run.course = verified.course
                run.categories = verified.categories
                phoneRun = run
                decideDestination(true, request)
            } else {
                decideDestination(false, request)
            }
        case .refused:
            verifiedDestinationIntentIDs.insert(request.intentID)
            // The website shows its one replacement approval; nothing native asks the owner.
            decideDestination(false, request)
        case .uncertain, .malformed:
            // Unknown is never a grant or denial. Repeated polling is bounded and never changes the body.
            destinationVerificationUncertain = true
            verifyRetryAfter = now().addingTimeInterval(Double(verifyAttemptCount * 4))
        }
    }

    private func decideDestination(_ approved: Bool, _ request: PhoneRunVerifyRequest) {
        submittedDestinationDecision = approved
        approvedDestinationIntentID = approved ? request.intentID : ""
        send(["action": "destination-decision", "handoffId": request.handoffID,
              "intentId": request.intentID, "approved": approved])
    }

    func disconnect() {
        declineLocalCollection()
        startAfterSettingCheck = false
        startConfirmationToken = nil
        if startConfirmation != .none { startConfirmation = .none; startPreparation = nil }
        // Discarding or cancelling a started collection ends its run; cancelling the pre-start check does not.
        if phoneRun?.collectionStarted == true { endPhoneRun() }
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
        finishAttemptCount = 0
        completionNeedsRetry = false
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
        submittedDestinationDecision = nil
        approvedDestinationIntentID = ""
        verifiedDestinationIntentIDs = []
        clearDestinationVerification()
        closedDestinationIntentIDs = []
        pairCode = ""
        pairPort = 0
        pairingPayloadSha256 = ""
        navigateOnPairing = false
        accountNavigation = nil
        if runBinding == .binding { runBinding = .uncertain }
    }

    /// Retrying finalization only asks for the same handoff's receipt. It never pairs again or re-saves sources.
    var canRetryAccountCompletion: Bool {
        completionNeedsRetry && phase == .reviewing && commandReady && handoffBound &&
            finishAttemptCount >= Self.maximumFinishAttempts
    }

    func retryAccountCompletion() {
        guard canRetryAccountCompletion else { return }
        finishAttemptCount = 0
        completionNeedsRetry = false
        handoffNotice = "Checking this account save's final receipt."
        errorMessage = ""
        send(["action": "handoff-status", "handoffId": handoffID])
        pollHandoff()
    }

    private func pollHandoff() {
        guard helperRunning, !handoffID.isEmpty, let operation = operationID else { return }
        let binding = handoffID
        handoffPollTask?.cancel()
        handoffPollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: self?.handoffPollNanoseconds ?? 2_000_000_000) } catch { return }
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
                fail("This local collection needs a current approval from your AMPLIFai account before connecting.")
                return
            }
            id = approval.runID
            requireEncryptedBackup = approval.callHistoryPreparation == .confirmedEncryptedBackup
            // The run authorizes exactly this one collection; it can never start another.
            phoneRun?.collectionStarted = true
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
        if commandWriter != nil {
            // Simulated helper wire (checks and fixtures): record the owned operation, spawn nothing.
            operationID = id
            activeHelperMode = mode
            return
        }
        guard let resources = Bundle.main.resourceURL else {
            fail("The app bundle is missing its local helper.")
            if mode == "inspect" { completeStartupChecks() }
            return
        }
        let portableHelper = resources.appendingPathComponent("helper/amplifai-agent")
        let uvxHelper = resources.appendingPathComponent("uvx")
        let module = resources.appendingPathComponent("collector")
        let portable = FileManager.default.isExecutableFile(atPath: portableHelper.path)
        guard portable || (FileManager.default.isExecutableFile(atPath: uvxHelper.path) &&
                           FileManager.default.fileExists(atPath: module.appendingPathComponent("amplifai_phone/agent.py").path)) else {
            fail("The local collector runtime is missing. Download and reinstall the signed Mac companion.")
            if mode == "inspect" { completeStartupChecks() }
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
            if mode == "inspect" { completeStartupChecks() }
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
        apply(event)
        eventObserver?(event)
    }

    private func apply(_ event: AgentEvent) {
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
                else {
                    clearPrivateReview()
                    resetHandoff()
                    if phoneRun?.collectionStarted == true { endPhoneRun() }
                    phase = .idle
                }
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
            let validCode = event.pairCode?.utf8.count == 10 &&
                event.pairCode?.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }) == true
            let handoff = event.handoffId.flatMap { UUID(uuidString: $0)?.uuidString.lowercased() } ?? ""
            let digest = event.payloadSha256 ?? ""
            // Every pairing is bound to the run, so it must be exactly the helper's real shape.
            guard pairingRequested, validCode, event.port == 48751, event.expiresInSeconds == 300,
                  PhoneRunContract.isID(handoff), PhoneRunContract.isDigest(digest) else {
                resetHandoff()
                send(["action": "revoke"])
                errorMessage = "The one-time account link was not ready. Your review is kept on this Mac. Choose Continue to try again."
                return
            }
            resetHandoff()
            errorMessage = ""
            navigateOnPairing = automaticNavigation
            pairCode = event.pairCode ?? ""
            pairPort = event.port ?? 0
            handoffID = handoff
            pairingPayloadSha256 = digest
            pollHandoff()
            // Nothing opens until the run accepts this handoff; then the account owns the flow.
            bindHandoff()
        case "destination":
            guard !handoffID.isEmpty, event.handoffId?.lowercased() == handoffID,
                  phase == .reviewing, !reviewPending, !selectionNeedsReview,
                  reviewedSelectionIDs == selectedIDs else { return }
            if event.state == "pending", let intent = event.intentId.flatMap(UUID.init(uuidString:)),
               let email = event.email, let course = event.course,
               PhoneRunContract.isEmail(email), PhoneRunContract.isCourse(course) {
                if destinationIntentID.isEmpty && !closedDestinationIntentIDs.contains(intent.uuidString.lowercased()) {
                    destinationIntentID = intent.uuidString.lowercased()
                    destinationEmail = email
                    destinationCourse = course
                    destinationApproved = false
                }
                // Answered automatically, once per intent, only after verify-intent matches exactly.
                verifyDestination()
            } else if event.state == "approved", event.intentId?.lowercased() == destinationIntentID,
                      !destinationIntentID.isEmpty {
                destinationApproved = true
                submittedDestinationDecision = nil
            } else if event.state == "denied", event.intentId?.lowercased() == destinationIntentID,
                      !destinationApproved {
                closedDestinationIntentIDs.insert(destinationIntentID)
                submittedDestinationDecision = nil
                approvedDestinationIntentID = ""
                clearDestinationVerification()
                destinationIntentID = ""
                destinationEmail = ""
                destinationCourse = ""
                destinationApproved = false
            }
        case "handoff":
            guard !handoffID.isEmpty, event.handoffId?.lowercased() == handoffID,
                  phase == .reviewing, !reviewPending, !selectionNeedsReview,
                  reviewedSelectionIDs == selectedIDs else { return }
            // A matching receipt can only follow an approved destination: it reconciles a lost
            // acknowledgement, but only for this app's verified approval of this intent on this handoff.
            if ["received", "saved_pending_browser_receipt", "saved"].contains(event.state ?? ""),
               !destinationIntentID.isEmpty, approvedDestinationIntentID == destinationIntentID {
                destinationApproved = true
                submittedDestinationDecision = nil
            }
            if ["received", "saved_pending_browser_receipt", "saved"].contains(event.state ?? ""),
               Self.handoffScopedMessages.contains(errorMessage) {
                // The browser demonstrably has this handoff, so only these link problems are obsolete.
                errorMessage = ""
            }
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
                // Limit: the helper reports `expired` both when the browser did not connect within the five-minute
                // pairing (PAIR_SECONDS) and when a released transfer went two hours without activity. A new
                // `pair` always creates a new handoffId, while the run binds one handoff and digest immutably
                // (identical retry only), so there is no same-run recovery: the run is spent. Nothing re-pairs,
                // rebinds, mints a capability, decides a destination or marks anything received.
                resetHandoff()
                if phoneRun != nil { endPhoneRun() }
                errorMessage = Self.transferExpiredMessage
                return
            }
            if event.state == "saved" {
                // A lost/uncertain finish response can return us to review while the same receipt is still
                // available. Bound automatic retries; explicit retry resumes this same handoff afterwards.
                guard finishAttemptCount < Self.maximumFinishAttempts else {
                    completionNeedsRetry = true
                    handoffNotice = "Your account save is confirmed. Retry finishing on this Mac."
                    return
                }
                finishAttemptCount += 1
                completionNeedsRetry = false
                pairingRequested = false
                navigateOnPairing = false
                selectionChangePending = false
                handoffNotice = ""
                handoffAcknowledged = true
                handoffPollTask?.cancel()
                handoffPollTask = nil
                phase = .completing
                if commandReady { send(["action": "finish", "handoffId": handoffID]) }
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
                errorMessage = Self.bridgeUnavailableMessage
            } else if event.code == "handoff_unconfirmed" {
                handoffAcknowledged = false
                submittedDestinationDecision = nil
                phase = .reviewing
                completionNeedsRetry = finishAttemptCount > 0
                errorMessage = Self.handoffUnconfirmedMessage
                // Finish had stopped polling. Restore receipt reconciliation without changing selection,
                // handoff, approval or save authority; the helper still validates the final ACK.
                pollHandoff()
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
            endPhoneRun()
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
        if mode == "backup-encryption-status" { startAfterCheckIfReady() }
        if mode == "inspect" { completeStartupChecks() }
    }

    private func fail(_ message: String) {
        let failedSettingCheck = backupEncryptionStatus == .checking && statusCheckFailureActive
        if !failedSettingCheck { settingCheckAfterCollectionFailure = false }
        declineLocalCollection()
        startAfterSettingCheck = false
        // A started collection that stopped can never be handed off: its run ends here.
        if phoneRun?.collectionStarted == true { endPhoneRun() }
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
            if browserOwnsFlow { return "Saving in your AMPLIFai account" }
            return "Your local review is ready"
        case .completing: return "Saved — finishing on this Mac"
        case .completed: return "Saved — your report is in your account"
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
            return "\(totalContacts) contacts, \(availableCalls) calls and \(availableMessages) messages were observed in available sources. Choose contacts, then Continue: only those are saved to the account you approved."
        case .reviewing:
            // Only what the helper has reported; nothing here claims pages received or saved early.
            if accountSaveAwaitingBrowserReceipt {
                return "Your account reported the save. This Mac keeps its private copy until your browser confirms the final receipt."
            }
            if browserReceived {
                return "Your browser has every selected page. This Mac keeps its private copy until your account confirms the save."
            }
            if browserOwnsFlow {
                return "Your AMPLIFai account in your browser is saving the contacts you chose. You can keep this window minimized."
            }
            return "Review the counts and observed dates. Continue saves the contacts you chose to the account you approved."
        case .completing:
            return "Your account confirmed the save. This window stays available until the owned helper finishes safely."
        case .completed:
            return "Your report is ready in your AMPLIFai account. This Mac's private copy was cleared."
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
