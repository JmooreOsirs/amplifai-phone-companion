import SwiftUI

struct ContentView: View {
    @ObservedObject var model: CollectorModel
    @ObservedObject var updater: CompanionUpdater
    /// The system default opens the browser; a host can intercept it (the fake-data demo does).
    @Environment(\.openURL) private var openURL
    @State private var search = ""
    @State private var confirmingCleanup = false
    @State private var showingConnectionHelp = false
    @State private var showingShareDetails = false
    @Environment(\.returnToBrowser) private var returnToBrowser
    @State private var showingFinderGuide = false
    @State private var showingUpdates = false
    @State private var confirmingRetainedReviewDiscard = false
    @State private var confirmingBulkMove = false
    @State private var showingGroupTools = false
    @State private var showingCollectionDetails = false
    @State private var showingSourceDetails = false
    @State private var showingPreviousSourceDetails = false

    @MainActor init(model: CollectorModel, updater: CompanionUpdater, showingGroupTools: Bool = false) {
        self.model = model
        self.updater = updater
        _search = State(initialValue: model.workspace?.query ?? "")
        _showingGroupTools = State(initialValue: showingGroupTools)
    }

    var body: some View {
        GeometryReader { geometry in
        ScrollViewReader { setupScroll in
        Group {
        if usesContactWorkspace {
                VStack(alignment: .leading, spacing: Brand.Space.item) {
                    phaseHeader
                    collectionNotices
                    if model.phase == .reviewing && !model.handoffNotice.isEmpty {
                        Text(model.handoffNotice).font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                    }
                    contactPanel(wellHeight: nil, compact: geometry.size.width < 600 || geometry.size.height < 760)
                        .frame(maxHeight: .infinity, alignment: .top)
                }
                .padding(geometry.size.width < 600 || geometry.size.height < 760 ? 16 : Brand.Space.page)
                .frame(maxWidth: Brand.Layout.workspaceMaximum, maxHeight: .infinity)
                .frame(maxWidth: .infinity)
                .safeAreaInset(edge: .bottom, spacing: 0) { contactActionDock(compact: geometry.size.width < 600) }
        } else {
        ScrollView {
            VStack(alignment: .leading, spacing: Brand.Space.section) {
                phaseHeader
                collectionNotices
                if model.phase == .error || model.phase == .completed || (model.phase == .idle && model.needsStorageReview) { statusPanel }
                // A confirmed save ends on its own summary; starting again means closing and reopening.
                if (!model.helperRunning || model.backupSettingCheckInProgress) &&
                    (model.phase == .idle || model.phase == .error) {
                    if model.hasUnsavedReview { retainedReviewRecovery }
                    else if !runNeedsAccountAction { collectionApprovalPanel.id("collection-setup") }
                }
                if ![.error, .idle, .selecting, .reviewing, .completed, .passwordRequired].contains(model.phase) { statusPanel }
                if model.canInstallUpdate && model.backupEncryptionStatus == .notChecked && model.localCollectionApproval == nil && !showingFinderGuide { updatePanel }
                if model.phase == .passwordRequired { passwordPanel }
                if model.phase == .reviewing { reviewPanel }
            }
            .frame(maxWidth: contentMaximumWidth)
            .padding(geometry.size.width < 600 ? 16 : Brand.Space.page)
            .frame(maxWidth: .infinity)
        }
        }
        }
        .background(Brand.background)
        .font(Brand.bodyFont(16))
        .tint(Brand.lime)
        .foregroundStyle(Brand.body)
        .background(ActiveCompanionWindow(model: model, returnToBrowser: returnToBrowser)
            .frame(width: 0, height: 0))
        .onChange(of: model.runApproval) { _, _ in
            showingFinderGuide = false
            scrollToCollectionSetup(setupScroll)
        }
        .onChange(of: model.backupEncryptionStatus) { _, _ in
            scrollToCollectionSetup(setupScroll)
        }
        .onChange(of: model.localCollectionApproval) { _, _ in
            scrollToCollectionSetup(setupScroll)
        }
        .onChange(of: model.phase) { _, phase in
            if phase == .connecting { search = "" }
            setupScroll.scrollTo("phase-heading", anchor: .top)
        }
        .onChange(of: model.canInstallUpdate) { _, safe in
            if safe { updater.resumeIfSafe() }
            else { showingUpdates = false }
        }
        .onDisappear { model.declineLocalCollection() }
        .confirmationDialog(
            "Remove previous temporary backups created by this app?",
            isPresented: $confirmingCleanup
        ) {
            Button("Remove app-created temporary backups", role: .destructive) {
                model.clearResidue()
            }
        } message: {
            Text("Only marked, abandoned AMPLIFai sessions are targeted. No other phone or computer backups are touched.")
        }
        .confirmationDialog("Discard the retained local unsaved review?", isPresented: $confirmingRetainedReviewDiscard) {
            Button("Discard review and disconnect", role: .destructive) { model.disconnect() }
            Button("Keep review", role: .cancel) {}
        } message: {
            Text("Receiving a browser preview is not a saved account receipt. This discards the companion's private local recovery copy; separately saved account data and marked temporary backups are not deleted.")
        }
        .confirmationDialog(model.pendingBulkMoveTitle, isPresented: $confirmingBulkMove) {
            Button("Move these contacts") { _ = model.confirmBulkMove() }
            Button("Keep current groups", role: .cancel) { model.cancelBulkMove() }
        } message: {
            Text(model.pendingBulkMoveDetail)
        }
        .onChange(of: confirmingBulkMove) { _, presented in
            if !presented { model.cancelBulkMove() }
        }
        }
        }
    }

    private var usesContactWorkspace: Bool {
        model.phase == .selecting || (model.phase == .reviewing && model.canEditWorkspace && !model.handoffNotice.isEmpty)
    }

    private var phaseHeader: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            header.id("phase-heading")
            JourneySteps(current: model.journeyStep)
        }
    }

    @ViewBuilder private var collectionNotices: some View {
        if showsRunStatusNotice { runStatusNotice }
        if model.companionStatus == .anotherAppRunning || model.companionStatus == .unavailable { companionStatusNotice }
        if !model.residues.isEmpty { residueNotice }
        if model.diagnosticRetention == .failed {
            Text("Safe source details could not be retained or restored on this Mac. Copy any current source details before closing.")
                .font(Brand.bodyFont(16)).foregroundStyle(Brand.body).panel()
        }
        if !usesContactWorkspace && !model.sourceDiagnosticHistory.isEmpty { previousSourceDetails }
    }

    private func scrollToCollectionSetup(_ reader: ScrollViewProxy) {
        guard !model.hasUnsavedReview, !model.helperRunning,
              model.phase == .idle || model.phase == .error || model.phase == .completed else { return }
        reader.scrollTo("collection-setup", anchor: .top)
    }

    private var contentMaximumWidth: CGFloat {
        switch model.phase {
        case .selecting, .reviewing: return Brand.Layout.workspaceMaximum
        case .idle, .error, .passwordRequired, .completed: return 880
        default: return 1180
        }
    }

    private var header: some View {
        HStack(spacing: Brand.Space.section) {
            logoTile
            Spacer(minLength: 0)
            if model.phase == .selecting || model.phase == .reviewing {
                Button(model.phase == .reviewing ? "Disconnect" : "Discard review…") { confirmingRetainedReviewDiscard = true }
                    .buttonStyle(SecondaryButton())
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var logoTile: some View {
        Group {
            if let logo = Brand.logo() {
                Image(nsImage: logo)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 164, height: 46)
                    .padding(8)
                    .background(.white, in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityLabel("AMPLIFai By Nexus")
            } else {
                Text("AMPLIFai Phone")
                    .font(Brand.headingFont(16))
                    .foregroundStyle(Brand.heading)
            }
        }
    }

    private var updatePanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                showingUpdates.toggle()
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("App updates · \(updater.currentVersion)")
                        .font(Brand.headingFont(16))
                        .foregroundStyle(Brand.heading)
                    Spacer(minLength: 0)
                    Image(systemName: showingUpdates || updateNeedsAttention ? "chevron.up" : "chevron.down")
                        .foregroundStyle(Brand.muted)
                }
                .frame(maxWidth: .infinity, minHeight: 32)
            }
            .buttonStyle(.plain)
            .accessibilityValue(showingUpdates || updateNeedsAttention ? "Expanded" : "Collapsed")
            if showingUpdates || updateNeedsAttention {
                Text(updateStatus)
                    .font(Brand.bodyFont(16))
                    .foregroundStyle(Brand.body)
                if updater.publishedUpdatesEnabled {
                    Link("Open signed Mac download guide", destination: URL(string: "https://amplifai.satoris.ai/phone")!)
                        .buttonStyle(SecondaryButton())
                    Button("Check for updates") { updater.checkNow() }
                        .buttonStyle(SecondaryButton())
                        .disabled(!model.canInstallUpdate || updater.state == .checking || updater.state == .installing)
                }
            }
        }
        .panel()
    }

    private var companionStatusNotice: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(model.companionStatus == .anotherAppRunning ? "Another AMPLIFai Phone app is running" : "App connection is unavailable")
                .font(Brand.headingFont(18))
                .foregroundStyle(Brand.heading)
            Text(model.companionStatus == .anotherAppRunning
                 ? "Close the other AMPLIFai Phone app, then reopen this one. Collection stays paused while the other app owns the local connection."
                 : "Reopen this app to restore its local connection before collecting. If this continues, reinstall the matching Mac companion from the download guide.")
                .font(Brand.bodyFont(16))
                .foregroundStyle(Brand.body)
        }
        .panel()
    }

    private var updateNeedsAttention: Bool {
        switch updater.state {
        case .available, .deferred: return true
        default: return false
        }
    }

    private var updateStatus: String {
        switch updater.state {
        case .updatesDisabled: return "Automatic updates are unavailable for this build. Use the Mac download guide for a compatible installer."
        case .manualOnly: return "Automatic update checks are off. Keep using this version, or use the signed Mac download guide when ready."
        case .checking: return "Checking the signed update feed. Your current app remains available."
        case .current: return "No newer compatible update was found."
        case .unavailable: return "The available update does not support this Mac or macOS version. Keep using this version."
        case .available(let version): return "Version \(version) is available. Automatic updates install after local work finishes when enabled in your update settings. Choose Check for updates to review it now."
        case .deferred(let version):
            let label = version.map { "Version \($0)" } ?? "Update checking"
            return "\(label) is deferred until local collection and any unsaved review are finished."
        case .installing: return "Installing the verified update after local work finished safely."
        case .failed: return "The signed update feed could not be checked. Keep using this version. When local work is finished, use the signed Mac download guide for the current installer."
        }
    }
    private var residueNotice: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Previous temporary backup needs attention")
                .font(Brand.headingFont(18))
                .foregroundStyle(Brand.heading)
            Text("\(model.residues.count) app-created session(s) remained after an interrupted run. They may contain source phone data. Connect stays disabled until you choose whether to clear them.")
            Button("Review and remove app temporary data") {
                confirmingCleanup = true
            }
            .buttonStyle(SecondaryButton())
        }
        .panel()
    }

    private var statusPanel: some View {
        CollectionStatusPanel(model: model)
    }

    private var runNeedsAccountAction: Bool {
        [.expired, .revoked, .replacementRequired].contains(model.runStatus)
    }

    private var showsRunStatusNotice: Bool {
        model.approvalAdoptionPending || model.canRetryApprovalAdoption || runNeedsAccountAction || (model.startConfirmation == .none &&
            (model.runStatus == .uncertain || (model.runStatus == .checking && !model.runStatusMessage.isEmpty)))
    }

    private var runStatusNotice: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            if model.approvalAdoptionPending {
                progressLine("Checking your updated approval…")
            } else if model.runStatus == .checking {
                progressLine("Rechecking this approval…")
            } else {
                Text(runStatusTitle).font(Brand.headingFont(20)).foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                Text(model.canRetryApprovalAdoption
                     ? "Your updated approval wasn't confirmed. Your collection and selected contacts are still on this Mac."
                     : model.runStatusMessage)
                    .font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
                if model.canRetryApprovalAdoption {
                    Button("Retry approval check") { model.retryApprovalAdoption() }
                        .buttonStyle(SecondaryButton())
                } else if model.runStatus == .uncertain {
                    Button("Recheck approval") { model.refreshRunStatus() }
                        .buttonStyle(SecondaryButton()).disabled(!model.canRefreshRunStatus)
                } else {
                    Button("Open your AMPLIFai account") {
                        returnToBrowser(model.accountContinueURL, preferredBrowserBundleID: model.returnBrowserBundleID)
                    }
                        .buttonStyle(LimeButton())
                }
            }
        }
        .panel()
    }

    private var runStatusTitle: String {
        switch model.runStatus {
        case .expired: return "This approval expired"
        case .revoked: return "Stopped in your account"
        case .replacementRequired: return "Check this collection in your account"
        default: return "Approval check needs a retry"
        }
    }

    private var collectionApprovalPanel: some View {
        VStack(alignment: .leading, spacing: 18) {
            if showingFinderGuide {
                FinderEncryptionGuide(model: model) { showingFinderGuide = false }
            } else if model.runApproval == .approved, let run = model.phoneRun {
                // Re-evaluate the negotiated preparation/Start deadline without renewing authority.
                TimelineView(.periodic(from: .now, by: 5)) { context in
                    if run.collectionStarted || model.runStartWindowOpen(at: context.date) {
                        approvedRunContent(run, now: context.date)
                    } else {
                        staleRunContent
                    }
                }
            } else {
                runStartContent
            }
        }
        .font(Brand.bodyFont(18))
        .panel()
    }

    /// Without a verified run nothing can be collected: the one agreement happens in the account.
    @ViewBuilder private var runStartContent: some View {
        Text("Collect phone data with your AMPLIFai account")
            .font(Brand.headingFont(28))
            .foregroundStyle(Brand.heading)
            .accessibilityAddTraits(.isHeader)
        switch model.runApproval {
        case .claiming:
            progressLine("Checking your approval with AMPLIFai…")
            Text("Nothing is collected until this Mac confirms the approval from your account.")
                .font(Brand.bodyFont(16))
        case .uncertain:
            Text(model.runApprovalMessage).font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { uncertainClaimActions }
                VStack(alignment: .leading, spacing: 12) { uncertainClaimActions }
            }
        case .refused:
            Text(model.runApprovalMessage).font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
            Button("Approve again in your account") { openAccountStart() }
                .buttonStyle(LimeButton())
        case .none, .approved:
            Text("Approve this collection in your account, then return here to connect your iPhone.")
                .font(Brand.bodyFont(16))
            Button("Start in your AMPLIFai account") { openURL(CollectorModel.accountStartURL) }
                .buttonStyle(LimeButton())
            privacyDetails
        }
    }

    @ViewBuilder private var uncertainClaimActions: some View {
        Button("Try again") { model.retryRunClaim() }
            .buttonStyle(LimeButton())
        Button("Open your AMPLIFai account") { openAccountStart() }
            .buttonStyle(SecondaryButton())
    }

    private func openAccountStart() {
        returnToBrowser(CollectorModel.accountStartURL, preferredBrowserBundleID: model.returnBrowserBundleID)
    }

    @ViewBuilder private var staleRunContent: some View {
        Text("Collect phone data with your AMPLIFai account")
            .font(Brand.headingFont(28))
            .foregroundStyle(Brand.heading)
            .accessibilityAddTraits(.isHeader)
        Text(CollectorModel.runRefusedMessage).font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
        Button("Approve again in your account") { openAccountStart() }
            .buttonStyle(LimeButton())
            .onAppear { model.refreshRunStartWindow() }
    }

    private func approvedRunContent(_ run: CollectorModel.PhoneRun, now: Date) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Prepare your iPhone")
                .font(Brand.headingFont(28))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            VerifiedStatusRow(verified: true, title: "Account: \(run.email)",
                              detail: "Course: \(run.course) · \(CollectorModel.approvedToSaveLabel(run.categories))")
            if !model.candidateNotice.isEmpty {
                Text(model.candidateNotice).font(Brand.bodyFont(15)).foregroundStyle(Brand.muted)
            }
            if model.candidateUncertain {
                Button("Try the newer approval again") { model.retryCandidateClaim() }
                    .buttonStyle(QuietAction())
            }
            PhoneConnectionGuide(checkInProgress: model.backupSettingCheckInProgress,
                                 settingConfirmed: [.encrypted, .unencrypted].contains(model.backupEncryptionStatus))
            BackupPasswordHelp()
            iPhoneCheckCard
            DisclosureGroup("Approval and privacy details") {
                VStack(alignment: .leading, spacing: 8) {
                    if !run.collectionStarted {
                        Text("Approval start window: about \(max(1, Int(ceil(run.startDeadline.timeIntervalSince(now) / 60)))) min remaining.")
                        Text("Approval expires at \(run.claim.expiresAt.formatted(date: .abbreviated, time: .shortened)).")
                    }
                    privacyDetails
                }
                .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted).padding(.top, 8)
            }
            .font(Brand.bodyFont(14))
        }
    }

    private var iPhoneCheckCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            if model.startConfirmation == .confirming {
                progressLine("Confirming your approval with AMPLIFai…")
            } else if model.startConfirmation == .uncertain {
                Text("Your approval was not confirmed, so collection has not started. Try again.")
                    .font(Brand.bodyFont(16))
                Button("Try again") { model.retryStartConfirmation() }
                    .buttonStyle(LimeButton())
            } else {
                setupCheckContent
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Brand.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border, lineWidth: 1))
    }

    @ViewBuilder private var setupCheckContent: some View {
        switch model.backupEncryptionStatus {
        case .checking:
            progressLine("Checking your iPhone…")
            Text("Keep your iPhone unlocked. If it asks to Trust this computer, tap Trust and enter the iPhone passcode. No backup or collection has started.")
                .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
            if model.backupSettingCheckInProgress {
                Button("Cancel check") { model.cancelBackupEncryptionStatusCheck() }
                    .buttonStyle(SecondaryButton())
            }
        case .unknown:
            VerifiedStatusRow(verified: false, title: "Backup setting not confirmed", detail: backupSettingDetail)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { unknownSettingActions }
                VStack(alignment: .leading, spacing: 12) { unknownSettingActions }
            }
            finderGuideAction
        case .unencrypted:
            VerifiedStatusRow(verified: false, title: "Backup encryption is off", detail: backupSettingDetail)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { unencryptedSettingActions }
                VStack(alignment: .leading, spacing: 12) { unencryptedSettingActions }
            }
        case .encrypted:
            VerifiedStatusRow(verified: true, title: "Backup encryption is on", detail: backupSettingDetail)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { startActions }
                VStack(alignment: .leading, spacing: 12) { startActions }
            }
        case .notChecked:
            Text("Connect and unlock your iPhone, then check it here. The check reads the backup setting without starting collection.")
                .font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { checkPhoneActions }
                VStack(alignment: .leading, spacing: 12) { checkPhoneActions }
            }
            finderGuideAction
        }
    }

    @ViewBuilder private var checkPhoneActions: some View {
        Button("Check connected iPhone") { model.checkBackupEncryptionStatus() }
            .buttonStyle(LimeButton())
            .disabled(!model.canPrepareCollection)
        Button("Not now") { model.dismissRunApproval() }
            .buttonStyle(SecondaryButton())
            .keyboardShortcut(.cancelAction)
    }

    @ViewBuilder private var startActions: some View {
        Button("Start collection") { model.startCollection() }
            .buttonStyle(LimeButton())
            .disabled(!model.canPrepareCollection || !model.hasStartableRun)
        Button("Not now") { model.dismissRunApproval() }
            .buttonStyle(SecondaryButton())
            .keyboardShortcut(.cancelAction)
    }

    @ViewBuilder private var unknownSettingActions: some View {
        Button("Recheck iPhone") { model.checkBackupEncryptionStatus() }
            .buttonStyle(LimeButton()).disabled(!model.canPrepareCollection)
        Button("Continue with available data") { model.startCollectionWithAvailableData() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canPrepareCollection || !model.hasStartableRun)
    }

    @ViewBuilder private var unencryptedSettingActions: some View {
        Button("Set up encryption in Finder") { showingFinderGuide = true }
            .buttonStyle(LimeButton())
        Button("Continue with available data") { model.startCollectionWithAvailableData() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canPrepareCollection || !model.hasStartableRun)
        Button("Recheck") { model.checkBackupEncryptionStatus() }
            .buttonStyle(QuietAction()).disabled(!model.canPrepareCollection)
    }

    private var finderGuideAction: some View {
        Button("Set up encrypted backup in Finder") { showingFinderGuide = true }
            .buttonStyle(QuietAction())
    }

    private var backupSettingDetail: String {
        switch model.backupEncryptionStatus {
        case .notChecked: return "Connect by USB, unlock the iPhone and approve Trust if asked."
        case .checking: return "Reading the existing backup setting. No backup or phone-data collection has started."
        case .encrypted: return "Call history may be available. Keep your existing backup password ready; full history is not guaranteed."
        case .unencrypted: return "Call history may be unavailable. Set up encryption in Finder, or continue with whatever data is available."
        case .unknown:
            switch model.backupEncryptionCheckCode {
            case "workspace_low_space":
                return "The Mac cannot meet this app's 2 GiB workspace reserve. No phone setting was read. Free space before collecting; Finder's own backup needs separate room."
            case "workspace_unavailable", "workspace_unsafe", "workspace":
                return "The app could not create its safe temporary workspace. Review app-created temporary data before collecting; no phone setting was read."
            case "phone_connection", "trust_required":
                return "Connect by USB, unlock the iPhone and establish Trust in Finder, then Recheck. No setting was confirmed."
            default:
                return "No backup setting was confirmed. Check the iPhone in Finder and Recheck; this is not proof that encryption is off or on."
            }
        }
    }

    private var privacyDetails: some View {
        Link("Privacy details", destination: privacyURL)
            .buttonStyle(QuietAction())
    }

    private var privacyURL: URL {
        model.approvedPrivacyURL ?? URL(string: "https://amplifai.satoris.ai/experience/account-privacy?version=account-2026-10-v7#phone-collection-and-saving")!
    }

    private var retainedReviewRecovery: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your unsaved local review is retained")
                .font(Brand.headingFont(18))
                .foregroundStyle(Brand.heading)
            Text("A new collection cannot replace this recovery copy. Discard it explicitly before starting again. Temporary-data inspection and removal remain separate.")
            Button("Discard review…") { confirmingRetainedReviewDiscard = true }
                .buttonStyle(SecondaryButton())
        }
        .panel()
    }

    private var passwordPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Existing encrypted-backup password")
                .font(Brand.headingFont(28))
                .foregroundStyle(Brand.heading)
            BackupPasswordHelp()
            Text("Your password stays on this Mac and is not saved.")
                .font(Brand.bodyFont(16))
            SecureField("Backup password", text: $model.password)
                .textFieldStyle(.plain)
                .padding(.horizontal, 14)
                .frame(minHeight: 44)
                .background(Brand.background, in: RoundedRectangle(cornerRadius: Brand.Radius.control))
                .overlay(RoundedRectangle(cornerRadius: Brand.Radius.control).stroke(Brand.border, lineWidth: 1))
                .onSubmit { model.submitPassword() }
            Button("Continue locally") { model.submitPassword() }
                .buttonStyle(LimeButton())
                .disabled(model.password.isEmpty || model.password.count > 512)
            Button("Stop on this Mac") { model.disconnect() }
                .buttonStyle(SecondaryButton())
        }
        .panel()
    }

    private func contactPanel(wellHeight: CGFloat?, compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            currentSourceDetails
            if let workspace = model.workspace {
                contactSelectionSummary(compact: compact)
                ContactReviewView(model: model, workspace: workspace, wellHeight: wellHeight, compact: compact,
                                  search: $search, showingGroupTools: $showingGroupTools,
                                  confirmingBulkMove: $confirmingBulkMove)
            } else {
                contactSelectionSummary(compact: compact)
                if model.suggestionScanning {
                    Text("Checking \(model.suggestionVisited) of \(model.totalContacts) contacts across all pages…")
                    Button("Stop and retry grouping") { model.cancelSuggestionScan() }
                        .buttonStyle(SecondaryButton())
                } else if model.totalContacts == 0 {
                    Text("No usable contacts were present in the selected backup.")
                } else {
                    Text("The full contact review is not ready. Retry the local check before selecting or saving.")
                    Button("Retry contact check") { model.scanContactSuggestions() }
                        .buttonStyle(SecondaryButton())
                }
            }
        }
        .font(Brand.bodyFont(18))
        .foregroundStyle(Brand.body)
        .panel()
        .onAppear { model.prepareWorkspace() }
    }

    @ViewBuilder private var currentSourceDetails: some View {
        if !model.missingSources.isEmpty || !model.currentSourceDiagnostics.isEmpty || !model.sourceDiagnosticHistory.isEmpty {
            HStack(spacing: Brand.Space.item) {
                if !model.missingSources.isEmpty {
                    Label(["calls", "messages"].filter { model.missingSources.contains($0) }
                        .map { $0 == "calls" ? "Calls unavailable" : "SMS activity unavailable" }.joined(separator: " · "),
                          systemImage: "exclamationmark.circle")
                        .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
                }
                Button { showingSourceDetails = true } label: {
                    if model.missingSources.isEmpty {
                        Label(model.currentSourceDiagnostics.isEmpty ? "Previous run source details" : "Source details",
                              systemImage: "info.circle").font(Brand.bodyFont(14))
                    } else {
                        Image(systemName: "info.circle").frame(width: 28, height: 28)
                    }
                }
                .buttonStyle(QuietAction()).accessibilityLabel("Current and previous source details")
                .popover(isPresented: $showingSourceDetails) {
                    ScrollView {
                    VStack(alignment: .leading, spacing: Brand.Space.item) {
                        ForEach(["calls", "messages"], id: \.self) { category in
                            if model.missingSources.contains(category) {
                                Text(model.unavailableDescription(category)).textSelection(.enabled)
                            }
                        }
                        ForEach(model.currentSourceDiagnostics) { record in
                            Text(record.detail).textSelection(.enabled)
                        }
                        if model.diagnosticRetention == .retained {
                            Text("These safe source details were retained on this Mac.")
                        }
                        if !model.sourceDiagnosticHistory.isEmpty {
                            Divider()
                            previousSourceHistory
                        }
                    }
                    .padding(Brand.Space.section)
                    }
                    .font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
                    .frame(width: 420, height: 320).background(Brand.panel)
                }
            }
        }
    }

    private var previousSourceDetails: some View {
        Button("Previous run source details") { showingPreviousSourceDetails = true }
            .buttonStyle(QuietAction())
            .popover(isPresented: $showingPreviousSourceDetails) {
                ScrollView { previousSourceHistory.padding(Brand.Space.section) }
                    .font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
                    .frame(width: 420, height: 320).background(Brand.panel)
            }
    }

    private var previousSourceHistory: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
                Text("Saved on this Mac for support. These previous results are not part of this collection or its account save.")
                    .foregroundStyle(Brand.muted)
                ForEach(model.sourceDiagnosticHistory) { attempt in
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Previous run · \(attempt.dateLabel)")
                            .font(Brand.headingFont(16))
                            .foregroundStyle(Brand.heading)
                        ForEach(attempt.sourceDiagnostics) { record in
                            Text(record.detail)
                                .foregroundStyle(Brand.muted)
                                .textSelection(.enabled)
                        }
                    }
                }
        }
    }

    private func contactActionDock(compact: Bool) -> some View {
        Group {
            if compact {
                VStack(alignment: .leading, spacing: Brand.Space.item) {
                    contactActionSummary
                    HStack(spacing: Brand.Space.item) {
                        if model.phoneRun != nil { accountStopAction }
                        Spacer(minLength: 0)
                        contactContinueAction
                    }
                }
            } else {
                HStack(spacing: Brand.Space.section) {
                    VStack(alignment: .leading, spacing: 4) {
                        contactActionSummary
                        if model.phoneRun != nil { accountStopAction }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    contactContinueAction
                }
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 12)
        .frame(maxWidth: Brand.Layout.workspaceMaximum, alignment: .leading)
        .frame(maxWidth: .infinity)
        .background(Brand.panel)
        .overlay(alignment: .top) { Rectangle().fill(Brand.border).frame(height: 1) }
        .fixedSize(horizontal: false, vertical: true)
        .layoutPriority(1)
    }

    private var contactActionSummary: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(contactActionHelp)
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
                .fixedSize(horizontal: false, vertical: true)
            if model.reviewPending {
                Text("Reviewing selected contacts and activity…").font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
            }
        }
    }

    @ViewBuilder private var contactContinueAction: some View {
        if model.canEditWorkspace {
            Button("Continue") { model.reviewWorkspace(continueToAccount: true) }
            .buttonStyle(LimeButton())
            .disabled(model.selectedIDs.isEmpty || !model.canContinueToAccount)
        } else {
            Text(contactActionStatus)
                .font(Brand.bodyFont(14).weight(.semibold))
                .foregroundStyle(Brand.heading)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var contactActionHelp: String {
        if model.suggestionScanning { return "Selection is available after all contacts are checked." }
        if model.selectedIDs.isEmpty { return "Select at least one contact to continue." }
        if model.accountSaveAwaitingBrowserReceipt {
            return model.isPreparationRun
                ? "Waiting for the final account receipt. Keep your AMPLIFai account tab open."
                : "Saving is being finalized. Finish in the browser before changing contacts."
        }
        if model.selectionChangePending { return "Checking that changing contacts won’t interrupt a save… Your reviewed selection is still intact." }
        if !model.handoffID.isEmpty { return "This selection is linked to your AMPLIFai account in the browser." }
        if let run = model.phoneRun {
            return "Continue saves \(model.selectedIDs.count.formatted()) contacts and their available activity to \(run.email) · \(run.course)."
        }
        return "\(model.selectedIDs.count.formatted()) contacts selected."
    }

    private var contactActionStatus: String {
        if model.reviewPending { return "Preparing your review…" }
        if model.suggestionScanning { return "Checking all contacts…" }
        if model.workspace == nil { return "Contact review is not ready" }
        if model.accountSaveAwaitingBrowserReceipt { return "Waiting for account Save receipt" }
        if model.selectionChangePending { return "Checking the current transfer…" }
        if !model.handoffID.isEmpty {
            return model.isPreparationRun ? "Waiting for your account to save" : "Continue in your account browser"
        }
        return "Local review is retained"
    }

    private func contactSelectionSummary(compact: Bool) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: Brand.Space.item) {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 20) { contactTitle; Spacer(minLength: 0); contactTitleCount }
                    VStack(alignment: .leading, spacing: 8) { contactTitle; contactTitleCount }
                }
                if compact {
                    Button { showingCollectionDetails = true } label: {
                        Image(systemName: "info.circle").frame(width: 28, height: 28)
                    }
                    .buttonStyle(QuietAction()).accessibilityLabel("Selection and collection details")
                }
            }
            if !compact {
                Text("Main contacts start selected; Review contacts wait for you. Only the contacts you choose are saved.")
                    .font(Brand.bodyFont(15))
                Button("Collection details") { showingCollectionDetails = true }.buttonStyle(QuietAction())
            }
            if !model.errorMessage.isEmpty && model.phase == .selecting {
                Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .popover(isPresented: $showingCollectionDetails) {
            VStack(alignment: .leading, spacing: Brand.Space.item) {
                Text("Main contacts start selected; Review contacts wait for you. Only the contacts you choose are saved.")
                Text(model.statusDetail).foregroundStyle(Brand.muted)
            }
            .font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
            .padding(Brand.Space.section).frame(width: 360).background(Brand.panel)
        }
    }

    private var contactTitle: some View {
        Text("Choose contacts").font(Brand.headingFont(28)).foregroundStyle(Brand.heading)
    }

    private var contactTitleCount: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(model.selectedIDs.count.formatted()) selected")
                .font(Brand.headingFont(24)).monospacedDigit().foregroundStyle(Brand.lime)
            Text("of \(model.totalContacts.formatted())")
                .font(Brand.bodyFont(16)).monospacedDigit().foregroundStyle(Brand.muted)
        }
        .accessibilityElement(children: .combine)
    }

    private var reviewPanel: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(handoffTitle)
                .font(Brand.headingFont(28))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            if let run = model.phoneRun {
                Text("\(run.email) · \(run.course)").font(Brand.bodyFont(16))
            }
            selectedSourceMetrics
            shareNextStep
            if !model.handoffNotice.isEmpty && !model.canRetryAccountCompletion {
                Text(model.handoffNotice).font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
            }
            if !model.errorMessage.isEmpty {
                Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled)
            }
            // Diagnostic dates and optional recovery stay separate from the normal browser handoff.
            DisclosureGroup("Details and options", isExpanded: $showingShareDetails) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Available history can be partial.")
                    Text("Observed calls: \(model.missingSources.contains("calls") ? "unavailable" : dateRange(model.observedCallEarliest, model.observedCallLatest)).")
                    Text("Observed SMS activity: \(model.missingSources.contains("messages") ? "unavailable" : dateRange(model.observedMessageEarliest, model.observedMessageLatest)).")
                    currentSourceDetails
                    privacyDetails
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 12) { reviewRecoveryActions }
                        VStack(alignment: .leading, spacing: 12) { reviewRecoveryActions }
                    }
                    if model.selectionChangePending {
                        Text("Checking that changing contacts won’t interrupt a save…")
                    }
                    connectionHelp
                }
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
                .padding(.top, 8)
            }
            .font(Brand.bodyFont(14))
        }
        .panel()
    }

    private var handoffTitle: String {
        if model.canRetryAccountCompletion { return "Finish on this Mac" }
        if model.accountSaveAwaitingBrowserReceipt { return "Waiting for the final receipt" }
        if model.browserReceived { return "Waiting for account Save" }
        if model.canRetryDestinationVerification { return "Account verification needs a retry" }
        if model.browserOwnsFlow {
            return model.isPreparationRun ? "Waiting for your account" : "Continue in your browser"
        }
        return "Ready to save"
    }

    private var handoffDetail: String {
        if model.canRetryAccountCompletion {
            return "Your account save is confirmed. Retry finishing to clear this Mac's private copy."
        }
        if model.accountSaveAwaitingBrowserReceipt {
            return "Your account reported the save. Waiting for the browser's final confirmation."
        }
        if model.browserReceived {
            return "Your browser has the selected data. Waiting for your account to confirm the save."
        }
        if model.canRetryDestinationVerification {
            return "The account check did not finish. Your selected data is retained; retry the same check."
        }
        if model.isPreparationRun {
            return "Keep your AMPLIFai account tab open so it can save your selected data. If the tab was closed, reopen it to resume this transfer. This Mac is waiting for confirmation."
        }
        return "The browser handles transfer, saving and your report. This Mac is waiting for confirmation."
    }

    private var selectedSourceMetrics: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: Brand.Space.item)],
                  alignment: .leading, spacing: Brand.Space.item) {
            metric(model.selectedIDs.count, "Selected contacts")
            if model.phoneRun?.categories.contains("calls") != false {
                metric(model.matchedCalls, "Matching calls", unavailable: model.missingSources.contains("calls"))
            }
            if model.phoneRun?.categories.contains("messages") != false {
                metric(model.matchedMessages, "Matching SMS activity", unavailable: model.missingSources.contains("messages"))
            }
        }
        .brandWell()
        .accessibilityLabel("Selected local data; saved totals are confirmed in your browser")
    }

    /// The browser confirms persistence. Preparation mode keeps this window available with an explicit
    /// account-reopen recovery action; completion never opens the browser automatically.
    @ViewBuilder private var shareNextStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            if model.canRetryAccountCompletion {
                Text(handoffDetail).foregroundStyle(Brand.body)
                Button("Retry finishing") { model.retryAccountCompletion() }
                    .buttonStyle(LimeButton())
            } else if model.browserOwnsFlow {
                Text(handoffDetail).foregroundStyle(Brand.body)
                if model.canRetryDestinationVerification {
                    Button("Retry account verification") { model.retryDestinationVerification() }
                        .buttonStyle(SecondaryButton())
                }
                BrowserReturnControls(model: model)
                if model.phoneRun != nil && !model.accountSaveAwaitingBrowserReceipt { accountStopAction }
            } else if model.runBinding == .binding || model.accountLinkPending {
                progressLine("Confirming with your AMPLIFai account…")
            } else if model.runBinding == .uncertain && !model.handoffID.isEmpty {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { uncertainBindActions }
                    VStack(alignment: .leading, spacing: 12) { uncertainBindActions }
                }
            } else if model.runBinding == .refused || !model.canContinueToAccount {
                // This collection cannot continue; a new collection starts from the account.
                Button("Start in your AMPLIFai account") { openAccountStart() }
                    .buttonStyle(LimeButton())
            } else {
                Text("Continue to your browser for saving and your report.")
                    .foregroundStyle(Brand.body)
                Button("Continue") { model.pairBrowser(automaticAccountNavigation: true) }
                    .buttonStyle(LimeButton())
                    .disabled(!canCreateLink)
            }
        }
        .brandControls()
    }

    @ViewBuilder private var uncertainBindActions: some View {
        Button("Try again") { model.retryRunBinding() }
            .buttonStyle(LimeButton())
    }

    @ViewBuilder private var accountStopAction: some View {
        if let controlURL = model.accountControlURL {
            Button("Stop in account…") {
                returnToBrowser(controlURL, preferredBrowserBundleID: model.returnBrowserBundleID)
            }
            .buttonStyle(QuietAction())
            .help("Open your account's Stop control. Opening the account does not stop this collection by itself.")
        }
    }

    private func progressLine(_ text: String) -> some View {
        HStack(spacing: 10) {
            ProgressView().controlSize(.small)
            Text(text).font(Brand.headingFont(18)).foregroundStyle(Brand.heading)
        }
    }

    private var canCreateLink: Bool {
        !(model.selectionNeedsReview || model.selectionChangePending ||
          model.accountSaveAwaitingBrowserReceipt || model.handoffAcknowledged || model.accountLinkPending) &&
            model.canContinueToAccount
    }

    @ViewBuilder private var reviewRecoveryActions: some View {
        if model.canRequestSelectionChange {
            Button("Change selected contacts") { model.requestSelectionChange() }
                .buttonStyle(SecondaryButton())
        }
        Button("Discard review…") { confirmingRetainedReviewDiscard = true }
            .buttonStyle(SecondaryButton())
    }

    /// The account link and the one-time code, closed until needed.
    private var connectionHelp: some View {
        DisclosureGroup("Having trouble connecting?", isExpanded: $showingConnectionHelp) {
            VStack(alignment: .leading, spacing: 12) {
                Text("If the browser did not open, open your AMPLIFai account here. This collection's approval binds one link that your browser must use within five minutes of Continue; after that, start a new collection from your account.")
                    .font(Brand.bodyFont(13))
                    .foregroundStyle(Brand.muted)
                Link("Open AMPLIFai account in a new browser tab", destination: model.accountContinueURL)
                    .buttonStyle(QuietAction())
                if !model.pairCode.isEmpty {
                    Text("One-time code: \(model.pairCode.prefix(5))-\(model.pairCode.suffix(5))")
                        .font(Brand.headingFont(18))
                        .foregroundStyle(Brand.heading)
                        .textSelection(.enabled)
                    Text("The one-time code expires after five minutes. An active, authorized transfer stays open while pages are requested and for up to two hours without activity. Only the exact AMPLIFai account origin can request pages; page requests do not prove an account save.")
                        .font(Brand.bodyFont(13))
                        .foregroundStyle(Brand.muted)
                    Button("Copy one-time code") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(model.pairCode, forType: .string)
                    }
                    .buttonStyle(SecondaryButton())
                }
            }
            .padding(.top, 8)
        }
        .font(Brand.bodyFont(14))
    }

    private func metric(_ value: Int, _ label: String, unavailable: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(unavailable ? "Unavailable" : value.formatted())
                .font(Brand.headingFont(28))
                .foregroundStyle(unavailable ? Brand.muted : Brand.lime)
            Text(label).font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .brandRaisedCard()
    }

    private func dateRange(_ earliest: String, _ latest: String) -> String {
        guard !earliest.isEmpty, !latest.isEmpty else { return "none in the available window" }
        return "\(earliest.prefix(10)) to \(latest.prefix(10)) (UTC)"
    }

}

struct PanelStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Brand.Space.section)
            .background(Brand.panel, in: RoundedRectangle(cornerRadius: Brand.Radius.panel))
            .overlay(RoundedRectangle(cornerRadius: Brand.Radius.panel).stroke(Brand.border, lineWidth: 1))
    }
}

extension View {
    func panel() -> some View { modifier(PanelStyle()) }
}

struct LimeButton: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Brand.headingFont(14))
            .foregroundStyle(isEnabled ? Brand.background : Brand.muted)
            .padding(.horizontal, 18)
            .frame(minHeight: 44)
            .background((isEnabled ? Brand.lime : Brand.border).opacity(configuration.isPressed ? 0.8 : 1),
                        in: RoundedRectangle(cornerRadius: Brand.Radius.control))
    }
}

struct SecondaryButton: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Brand.headingFont(14))
            .foregroundStyle(isEnabled ? Brand.heading : Brand.muted)
            .padding(.horizontal, 18)
            .frame(minHeight: 40)
            .background((isEnabled ? Brand.border : Brand.panel).opacity(configuration.isPressed ? 0.8 : 1),
                        in: RoundedRectangle(cornerRadius: Brand.Radius.control))
    }
}
