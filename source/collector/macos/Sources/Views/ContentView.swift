import SwiftUI

struct ContentView: View {
    @ObservedObject var model: CollectorModel
    @ObservedObject var updater: CompanionUpdater
    @State private var search = ""
    @State private var confirmingCleanup = false
    @State private var confirmingBrowserShare = false
    @State private var continueAfterBrowserShare = false
    @State private var destinationReviewRequestID: UUID?
    @State private var localCollectionChecked = false
    @State private var callHistoryPreparation: CollectorModel.CallHistoryPreparation?
    @State private var showingCollectionDetails = false
    @State private var showingFinderGuide = false
    @State private var showingUpdates = false
    @State private var confirmingRetainedReviewDiscard = false
    @State private var confirmingBulkMove = false
    @State private var showingGroupTools = false

    var body: some View {
        ScrollViewReader { setupScroll in
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                if !model.residues.isEmpty { residueNotice }
                if model.phase == .error || (model.phase == .idle && model.needsStorageReview) { statusPanel }
                if (!model.helperRunning || model.backupSettingCheckInProgress) &&
                    (model.phase == .idle || model.phase == .error || model.phase == .completed) {
                    if model.hasUnsavedReview { retainedReviewRecovery }
                    else { collectionApprovalPanel.id("collection-setup") }
                }
                if model.phase != .error && model.phase != .idle { statusPanel }
                if model.canInstallUpdate && callHistoryPreparation == nil && model.localCollectionApproval == nil { updatePanel }
                if model.phase == .passwordRequired { passwordPanel }
                if model.phase == .selecting || model.phase == .reviewing { contactPanel }
                if model.phase == .reviewing { reviewPanel }
            }
            .frame(maxWidth: 880)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .background(Brand.background)
        .foregroundStyle(Brand.body)
        .background(ActiveCompanionWindow(model: model, destinationReviewRequestID: destinationReviewRequestID)
            .frame(width: 0, height: 0))
        .onChange(of: model.collectionApprovalRunID) { _, _ in
            localCollectionChecked = false
            callHistoryPreparation = nil
            showingCollectionDetails = false
            showingFinderGuide = false
        }
        .onChange(of: model.backupEncryptionStatus) { _, status in
            if status != .encrypted && callHistoryPreparation == .confirmedEncryptedBackup {
                callHistoryPreparation = nil
                localCollectionChecked = false
            }
            if status == .checking { scrollToCollectionSetup(setupScroll) }
        }
        .onChange(of: callHistoryPreparation) { _, _ in
            scrollToCollectionSetup(setupScroll)
        }
        .onChange(of: model.localCollectionApproval) { _, _ in
            scrollToCollectionSetup(setupScroll)
        }
        .onChange(of: model.phase) { _, phase in
            if phase == .connecting { search = "" }
        }
        .onChange(of: model.accountNavigationID) { _, request in
            guard request != nil,
                  let url = URL(string: "https://amplifai-database-engine.vercel.app/phone/account?continue=reviewed") else { return }
            if !NSWorkspace.shared.open(url) { model.accountNavigationFailed() }
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
        .confirmationDialog(
            "Share selected relationship metadata with the account browser?",
            isPresented: $confirmingBrowserShare
        ) {
            Button(continueAfterBrowserShare ? "Continue to signed-in account" : "Create one-time browser pairing") {
                if continueAfterBrowserShare { model.reviewWorkspace(continueToAccount: true) }
                else { model.pairBrowser() }
            }
        } message: {
            Text("This opens a five-minute local handoff for selected contacts, phone numbers, and matching call/message metadata. No message bodies, backup files, or passwords are handed off. Saving to your account requires separate approval there. This companion keeps the local review until the matching account save is explicitly acknowledged.")
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

    private func scrollToCollectionSetup(_ reader: ScrollViewProxy) {
        guard !model.hasUnsavedReview, !model.helperRunning,
              model.phase == .idle || model.phase == .error || model.phase == .completed else { return }
        reader.scrollTo("collection-setup", anchor: .top)
    }

    private var header: some View {
        logoTile
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var logoTile: some View {
        Group {
            if let logo = Brand.logo() {
                Image(nsImage: logo)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 228, height: 64)
                    .padding(8)
                    .background(.white, in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityLabel("AMPLIFai By Nexus")
            } else {
                Text("AMPLIFai Phone")
                    .font(.custom("Arial", size: 16).weight(.bold))
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
                        .font(.custom("Arial", size: 16).weight(.bold))
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
                    .font(.custom("Arial", size: 16))
                    .foregroundStyle(Brand.body)
                Link("Open signed Mac download guide", destination: URL(string: "https://amplifai-database-engine.vercel.app/phone")!)
                    .buttonStyle(SecondaryButton())
                Button("Check for updates") { updater.checkNow() }
                    .buttonStyle(SecondaryButton())
                    .disabled(!model.canInstallUpdate || updater.state == .checking || updater.state == .installing)
            }
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
        case .manualOnly: return "Automatic update checks are off. Keep using this version, or use the signed Mac download guide when ready."
        case .checking: return "Checking the signed update feed. Your current app remains available."
        case .current: return "No newer compatible update was found."
        case .unavailable: return "The available update does not support this Mac or macOS version. Keep using this version."
        case .available(let version): return "Version \(version) is available. Choose Check for updates when you are ready to install."
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
                .font(.custom("Arial", size: 18).weight(.bold))
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

    private var collectionApprovalPanel: some View {
        VStack(alignment: .leading, spacing: 18) {
            if let approval = model.localCollectionApproval {
                Text("Ready to connect")
                    .font(.custom("Arial", size: 26).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                Text("Approved for this collection · backup \(backupSettingLabel). \(callHistoryChoiceSummary(approval.callHistoryPreparation))")
                Button("Connect iPhone") { model.connect() }
                    .buttonStyle(LimeButton())
                    .disabled(!model.canConnect)
                    .opacity(model.canConnect ? 1 : 0.55)
                Button("Change setup or consent") {
                    localCollectionChecked = false
                    callHistoryPreparation = nil
                    model.declineLocalCollection()
                }
                .buttonStyle(SecondaryButton())
                .keyboardShortcut(.cancelAction)
            } else if let choice = callHistoryPreparation {
                Text("Approve this collection")
                    .font(.custom("Arial", size: 26).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                HStack(alignment: .top, spacing: 12) {
                    Text("Backup: \(backupSettingLabel). \(callHistoryChoiceSummary(choice))")
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Change") {
                        callHistoryPreparation = nil
                        localCollectionChecked = false
                        showingFinderGuide = false
                    }
                    .buttonStyle(SecondaryButton())
                }
                collectionConsentStep
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { collectionApprovalActions }
                    VStack(alignment: .leading, spacing: 12) { collectionApprovalActions }
                }
            } else {
                Text("Prepare this iPhone")
                    .font(.custom("Arial", size: 26).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                Text("Check the iPhone's existing backup setting. This does not change it or guarantee every older call.")
                backupSettingCard
                if model.backupEncryptionStatus == .unencrypted {
                    Text("We will read available calls. This backup may not include them.")
                        .font(.custom("Arial", size: 16))
                        .foregroundStyle(Brand.body)
                    Button(showingFinderGuide ? "Hide Finder call-history steps" : "Prepare call history in Finder") {
                        showingFinderGuide.toggle()
                    }
                    .buttonStyle(SecondaryButton())
                    if showingFinderGuide { finderEncryptionGuide }
                }
                if model.backupEncryptionStatus != .notChecked && model.backupEncryptionStatus != .checking {
                    if model.backupEncryptionStatus == .encrypted {
                        Button("I have the backup password · continue") {
                            callHistoryPreparation = .confirmedEncryptedBackup
                        }
                        .buttonStyle(LimeButton())
                        .disabled(!model.canPrepareCollection)
                    }
                    if model.backupEncryptionStatus != .encrypted {
                        Button("Continue with available data") { continueWithAvailableData() }
                            .buttonStyle(LimeButton())
                            .disabled(!model.canPrepareCollection)
                    }
                }
            }
        }
        .font(.custom("Arial", size: 18))
        .panel()
    }

    private func callHistoryChoiceSummary(_ choice: CollectorModel.CallHistoryPreparation) -> String {
        switch choice {
        case .confirmedEncryptedBackup:
            return "Existing backup password needed locally."
        case .continueWithoutConfirmedCoverage:
            return "Available Calls will be read; others may be Unavailable."
        }
    }

    private func continueWithAvailableData() {
        callHistoryPreparation = .continueWithoutConfirmedCoverage
        showingFinderGuide = false
    }

    private var backupSettingCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("LOCAL BACKUP ENCRYPTION")
                .font(.custom("Arial", size: 14).weight(.bold))
                .foregroundStyle(Brand.muted)
            Text(backupSettingLabel)
                .font(.custom("Arial", size: 22).weight(.bold))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            Text(backupSettingDetail)
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.body)
            if model.backupEncryptionStatus == .notChecked {
                Button("Check iPhone setting") { checkBackupSetting() }
                    .buttonStyle(LimeButton())
                    .disabled(!model.canPrepareCollection || model.localCollectionApproval != nil)
            } else if model.backupEncryptionStatus != .checking {
                Button("Recheck iPhone setting") { checkBackupSetting() }
                    .buttonStyle(SecondaryButton())
                    .disabled(!model.canPrepareCollection || model.localCollectionApproval != nil)
            }
            if model.backupSettingCheckInProgress {
                Button("Cancel setting check") { model.cancelBackupEncryptionStatusCheck() }
                    .buttonStyle(SecondaryButton())
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Brand.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border, lineWidth: 1))
    }

    private func checkBackupSetting() {
        localCollectionChecked = false
        callHistoryPreparation = nil
        showingFinderGuide = false
        model.checkBackupEncryptionStatus()
    }

    private var backupSettingLabel: String {
        switch model.backupEncryptionStatus {
        case .notChecked: return "Not checked"
        case .checking: return "Checking…"
        case .encrypted: return "On"
        case .unencrypted: return "Off"
        case .unknown: return "Could not read"
        }
    }

    private var backupSettingDetail: String {
        switch model.backupEncryptionStatus {
        case .notChecked: return "Connect by USB, unlock the iPhone and approve Trust if asked."
        case .checking: return "Reading the existing backup setting. No backup or phone-data collection has started."
        case .encrypted: return "The phone reported encryption on. Keep its existing backup password; the collection will check again before starting."
        case .unencrypted: return "The phone reported encryption off."
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

    private var finderEncryptionGuide: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("To prepare call history in Finder")
                .font(.custom("Arial", size: 22).weight(.bold))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            ForEach(Array(finderEncryptionSteps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .top, spacing: 12) {
                    Text("\(index + 1).")
                        .font(.custom("Arial", size: 22).weight(.bold))
                        .foregroundStyle(Brand.lime)
                    Text(step).fixedSize(horizontal: false, vertical: true)
                }
            }
            Text("Finder can start a full backup and overwrite/encrypt previous local backups. Let it finish. Finder needs its own Mac storage beyond this app's 2 GiB reserve; do not remove other backups just to make room.")
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.body)
        }
    }

    private var finderEncryptionSteps: [String] {
        [
            "Connect the iPhone by USB.",
            "Unlock it and approve Trust on the iPhone if asked.",
            "Open Finder and select your iPhone under Locations.",
            "Open General.",
            "Turn on Encrypt local backup.",
            "Choose and keep the backup password yourself. Enter it locally in AMPLIFai if asked; it is never sent to the account.",
            "Let Finder finish its backup. If an existing password is unknown, ask the owner; this app cannot reset it.",
            "Return here and choose Recheck iPhone setting."
        ]
    }

    private var collectionConsentStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("USB collection temporarily receives a full backup, including unrelated data and message content, then discards unselected bytes. Only chosen metadata can reach your account after separate browser and source approval; message text and backup files do not.")
            Text("The password stays local and is not saved. Interrupted runs may leave marked temporary data. Safe diagnostics contain codes and counts, not records, content, passwords or paths.")
            Button(showingCollectionDetails ? "Hide exact collection details" : "Show exact collection details") {
                showingCollectionDetails.toggle()
            }
            .buttonStyle(SecondaryButton())
            if showingCollectionDetails {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Requires an Apple M-series Mac with macOS 14 or later, a data-capable USB cable and enough Mac storage for selected databases, parsing copies and a further 2 GiB reserve. Internet is needed later for diagnostic delivery, browser handoff and account save; the USB backup runs locally.")
                    Text("The local review can include phone numbers, dates, participants, call duration and message transport/direction. Physical source coverage varies. Changing Finder encryption after a collection requires another owner-approved capture.")
                    Text("This approval permits one local collection. It does not approve Apple's Trust prompt, supply a password, authorize browser sharing, or save account data. Browser transfer and optional source saves require separate confirmation.")
                    Text("Safe diagnostics contain build, random reference, failure category and stage, elapsed time, byte counts and optional numeric device status. They exclude phone records, backup files, paths and raw errors. Cleanup is attempted; marked residue must be inspected here.")
                    Text("Disclosure: \(CollectorModel.collectionDisclosureVersion)").textSelection(.enabled)
                }
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.muted)
            }
            if model.localCollectionApproval == nil {
                Toggle("I agree to this local collection and safe failure diagnostic", isOn: Binding(
                    get: { localCollectionChecked },
                    set: { checked in
                        localCollectionChecked = checked
                    }
                ))
                .toggleStyle(.checkbox)
                .disabled(!model.canPrepareCollection)
            }
        }
    }

    @ViewBuilder private var collectionApprovalActions: some View {
        Button("Agree to local collection") {
            model.approveLocalCollection(
                for: model.collectionApprovalRunID,
                checked: localCollectionChecked,
                callHistoryPreparation: callHistoryPreparation
            )
        }
        .buttonStyle(LimeButton())
        .disabled(!localCollectionChecked || callHistoryPreparation == nil || !model.canPrepareCollection ||
                  (callHistoryPreparation == .confirmedEncryptedBackup && model.backupEncryptionStatus != .encrypted))
        .opacity(localCollectionChecked && model.canPrepareCollection ? 1 : 0.55)
        Button("Decline") {
            localCollectionChecked = false
            callHistoryPreparation = nil
            model.declineLocalCollection()
        }
        .buttonStyle(SecondaryButton())
        .keyboardShortcut(.cancelAction)
    }

    private var retainedReviewRecovery: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your unsaved local review is retained")
                .font(.custom("Arial", size: 18).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text("A new collection cannot replace this recovery copy. Discard it explicitly before starting again. Temporary-data inspection and removal remain separate.")
            Button("Review discard options") { confirmingRetainedReviewDiscard = true }
                .buttonStyle(SecondaryButton())
        }
        .panel()
    }

    private var passwordPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Existing encrypted-backup password")
                .font(.custom("Arial", size: 22).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text("Use the password already set for this iPhone's encrypted computer backup, not its screen-unlock passcode. Ask its owner if you do not know it. This app never changes or resets encryption; the password stays in the local helper and is not saved.")
                .font(.custom("Arial", size: 18))
            SecureField("Backup password", text: $model.password)
                .textFieldStyle(.roundedBorder)
                .onSubmit { model.submitPassword() }
            Button("Continue locally") { model.submitPassword() }
                .buttonStyle(LimeButton())
                .disabled(model.password.isEmpty || model.password.count > 512)
        }
        .panel()
    }

    private var contactPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Choose people")
                .font(.custom("Arial", size: 24).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text("\(model.selectedIDs.count) of \(model.totalContacts) selected across all pages")
                .foregroundStyle(Brand.heading)
            Text("Main starts with people whose name and phone details look usable. Review holds organization-like or incomplete entries for your decision. Emoji and unusual formatting alone do not move anyone. Nothing changes on your phone.")
                .foregroundStyle(Brand.body)
            if let workspace = model.workspace {
                Text("Main \(workspace.mainCount) (\(model.selectedMainCount) selected) · Review \(workspace.reviewCount) (\(model.selectedReviewCount) selected)")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) { workspaceFilters(workspace) }
                    VStack(alignment: .leading, spacing: 10) { workspaceFilters(workspace) }
                }
                Button("Select all contacts · \(workspace.knownIDs.count)") { model.selectAllVerifiedContacts() }
                    .buttonStyle(LimeButton())
                    .disabled(!model.canSelectAllVerifiedContacts)
                Text("This deliberate action includes Main and Review contacts across every page, even when this view is filtered or searched. Review is never included automatically.")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) { workspaceSelectionActions(workspace) }
                    VStack(alignment: .leading, spacing: 10) { workspaceSelectionActions(workspace) }
                }
                Button(showingGroupTools
                       ? "Hide group organizing · \(model.markedGroupMoveIDs.count) marked"
                       : "Organize Main and Review · \(model.markedGroupMoveIDs.count) marked") {
                    showingGroupTools.toggle()
                }
                .buttonStyle(SecondaryButton())
                if showingGroupTools && workspace.group == .all {
                    Text("Open Main or Review to mark and move contacts between groups. Import selection stays separate.")
                        .font(.caption)
                        .foregroundStyle(Brand.muted)
                }
                if showingGroupTools && workspace.group != .all && workspace.filteredCount > 0 {
                    Text("\(model.markedGroupMoveIDs.count) marked for group movement · import selection is separate")
                        .font(.caption)
                        .foregroundStyle(Brand.muted)
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) { groupMoveActions(workspace) }
                        VStack(alignment: .leading, spacing: 10) { groupMoveActions(workspace) }
                    }
                    Text("Mark individual rows on any page, or mark every search match across all pages. Confirm before changing groups.")
                        .font(.caption)
                        .foregroundStyle(Brand.muted)
                }
                if model.canUndoGroupMove {
                    Button("Undo last group move") { model.undoContactMove() }
                        .buttonStyle(SecondaryButton())
                }
                TextField("Search \(workspace.group.rawValue) contacts", text: $search)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Search contacts in \(workspace.group.rawValue)")
                    .onChange(of: search) { _, value in
                        if value.unicodeScalars.count > 120 { search = String(value.unicodeScalars.prefix(120)) }
                        else { model.searchWorkspace(value) }
                    }
                Text("\(workspace.filteredCount) \(workspace.group.rawValue.lowercased()) contacts match across all pages · \(workspace.visible.count) shown")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(workspace.visible) { contact in
                            contactRow(contact, workspace: workspace)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(minHeight: 420, maxHeight: 640)
                if workspace.filteredCount == 0 {
                    Text("No contacts match this view and search. Try All, Main or Review, or clear the search.")
                        .foregroundStyle(Brand.muted)
                }
                HStack(spacing: 10) {
                    if workspace.hasPreviousPage {
                        Button("Previous contacts") { model.previousWorkspacePage() }
                            .buttonStyle(SecondaryButton())
                    }
                    if workspace.hasNextPage {
                        Button("Next contacts") { model.nextWorkspacePage() }
                            .buttonStyle(SecondaryButton())
                    }
                    if workspace.filteredCount > 0 {
                        Text("\(workspace.pageStart + 1)–\(workspace.pageStart + workspace.visible.count) of \(workspace.filteredCount)")
                            .font(.caption).foregroundStyle(Brand.muted)
                    }
                }
            } else if model.suggestionScanning {
                Text("Checking \(model.suggestionVisited) of \(model.totalContacts) contacts across all pages before grouping…")
                    .foregroundStyle(Brand.muted)
                Button("Stop and retry grouping") { model.cancelSuggestionScan() }
                    .buttonStyle(SecondaryButton())
            } else if model.totalContacts == 0 {
                Text("No usable contacts were present in the selected backup.")
                    .foregroundStyle(Brand.muted)
            } else {
                Text("The full contact review is not ready. Retry the local check before selecting or saving.")
                    .foregroundStyle(Brand.muted)
                Button("Retry contact check") { model.scanContactSuggestions() }
                    .buttonStyle(SecondaryButton())
            }
            if model.reviewPending {
                Text("Reviewing selected metadata…")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
            }
            if !model.errorMessage.isEmpty && model.phase == .selecting {
                Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled)
            }
            Button("Save and continue") {
                continueAfterBrowserShare = true
                confirmingBrowserShare = true
            }
                .buttonStyle(LimeButton())
                .disabled(model.selectedIDs.isEmpty || !model.canEditWorkspace)
                .opacity(model.selectedIDs.isEmpty || !model.canEditWorkspace ? 0.55 : 1)
            Text(model.accountSaveAwaitingBrowserReceipt
                 ? "Saving is being finalized. Finish in the browser before changing contacts."
                 : model.selectionChangePending
                 ? "Checking that changing contacts won’t interrupt a save… Your reviewed selection is still intact."
                 : !model.handoffID.isEmpty
                 ? "This selection is already linked to the account browser. Finish source choices there, or use Change selected contacts in Local review below."
                 : "Next, confirm the signed-in account and approve each source you choose to save. This button does not save account data by itself.")
                .font(.caption)
                .foregroundStyle(Brand.muted)
        }
        .panel()
        .onAppear { model.prepareWorkspace() }
    }

    @ViewBuilder private func workspaceFilters(_ workspace: ContactWorkspace) -> some View {
        if workspace.group == .all {
            Button("All · \(workspace.knownIDs.count)") { model.showContactGroup(.all) }
                .buttonStyle(LimeButton())
        } else {
            Button("All · \(workspace.knownIDs.count)") { model.showContactGroup(.all) }
                .buttonStyle(SecondaryButton())
        }
        if workspace.group == .main {
            Button("Main · \(workspace.mainCount)") { model.showContactGroup(.main) }
                .buttonStyle(LimeButton())
        } else {
            Button("Main · \(workspace.mainCount)") { model.showContactGroup(.main) }
                .buttonStyle(SecondaryButton())
        }
        if workspace.group == .review {
            Button("Review · \(workspace.reviewCount)") { model.showContactGroup(.review) }
                .buttonStyle(LimeButton())
        } else {
            Button("Review · \(workspace.reviewCount)") { model.showContactGroup(.review) }
                .buttonStyle(SecondaryButton())
        }
    }

    @ViewBuilder private func workspaceSelectionActions(_ workspace: ContactWorkspace) -> some View {
        Button("Clear all selected") { model.clearAllVerifiedContacts() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canClearAllVerifiedContacts)
        if workspace.group == .main {
            Button("Select all Main · \(workspace.mainCount)") { model.selectAllMain() }
                .buttonStyle(SecondaryButton())
                .disabled(!model.canSelectAllMain)
            Button("Clear Main selection") { model.clearMain() }
                .buttonStyle(SecondaryButton())
                .disabled(!model.canClearMain)
        } else if workspace.group == .review {
            Button("Select all Review · \(workspace.reviewCount)") { model.selectAllReview() }
                .buttonStyle(SecondaryButton())
                .disabled(!model.canSelectAllReview)
            Button("Clear Review selection") { model.clearReview() }
                .buttonStyle(SecondaryButton())
                .disabled(!model.canClearReview)
        }
    }

    @ViewBuilder private func groupMoveActions(_ workspace: ContactWorkspace) -> some View {
        let destination: ContactGroup = workspace.group == .main ? .review : .main
        Button("Move all \(workspace.filteredCount) matches to \(destination.rawValue)") {
            if model.beginBulkMove(to: destination) { confirmingBulkMove = true }
        }
        .buttonStyle(SecondaryButton())
        .disabled(!model.canEditWorkspace)
        Button("Mark all \(workspace.filteredCount) matches") { model.markAllMatchingForGroupMove() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canEditWorkspace)
        Button("Move \(model.markedGroupMoveIDs.count) marked to \(destination.rawValue)") {
            if model.beginMarkedBulkMove(to: destination) { confirmingBulkMove = true }
        }
        .buttonStyle(SecondaryButton())
        .disabled(!model.canEditWorkspace || model.markedGroupMoveIDs.isEmpty)
        if !model.markedGroupMoveIDs.isEmpty {
            Button("Clear group marks") { model.clearGroupMoveMarks() }
                .buttonStyle(SecondaryButton())
        }
    }

    private func contactRow(_ contact: ContactPreview, workspace: ContactWorkspace) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle(isOn: Binding(
                get: { model.selectedIDs.contains(contact.id) },
                set: { _ in model.toggle(contact.id) }
            )) {
                Text(contact.name.isEmpty ? "Unnamed contact" : contact.name)
                    .font(.custom("Arial", size: 15).weight(.bold))
                    .foregroundStyle(Brand.heading)
            }
            .toggleStyle(.checkbox)
            .disabled(!model.canEditWorkspace)
            Text("Group: \(workspace.isInReview(contact.id) ? "Review" : "Main")")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            Text(contact.phoneEnds.isEmpty
                 ? "No supported phone number"
                 : contact.phoneEnds.map { "•••• \($0)" }.joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(Brand.muted)
            if let reason = workspace.reason(for: contact.id) {
                Text("Review reason: \(reason). This is a local clue, not a relationship judgment.")
                    .font(.caption)
                    .foregroundStyle(Brand.body)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { contactGroupActions(contact, workspace: workspace) }
                VStack(alignment: .leading, spacing: 10) { contactGroupActions(contact, workspace: workspace) }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Brand.background, in: RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder private func contactGroupActions(_ contact: ContactPreview, workspace: ContactWorkspace) -> some View {
        if showingGroupTools && workspace.group != .all {
            Button(model.markedGroupMoveIDs.contains(contact.id) ? "Unmark group move" : "Mark for group move") {
                model.markContactForGroupMove(contact.id)
            }
            .buttonStyle(SecondaryButton())
            .accessibilityLabel("\(model.markedGroupMoveIDs.contains(contact.id) ? "Unmark" : "Mark") \(contact.name.isEmpty ? "unnamed contact" : contact.name) for group move")
            .disabled(!model.canEditWorkspace)
        }
        Button(workspace.isInReview(contact.id) ? "Move to Main" : "Move to Review") {
            model.moveContact(contact.id, to: workspace.isInReview(contact.id) ? .main : .review)
        }
        .buttonStyle(SecondaryButton())
        .disabled(!model.canEditWorkspace)
    }

    private var reviewPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Local review")
                .font(.custom("Arial", size: 18).weight(.bold))
                .foregroundStyle(Brand.heading)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 16)], alignment: .leading, spacing: 16) {
                metric(model.selectedIDs.count, "Selected contacts")
                metric(model.matchedCalls, "Matching calls", unavailable: model.missingSources.contains("calls"))
                metric(model.matchedMessages, "Matching messages", unavailable: model.missingSources.contains("messages"))
            }
            Text("The collector attempted all retained available history. The observed dates below do not prove complete phone history.")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            Text("Observed calls: \(model.missingSources.contains("calls") ? "unavailable" : dateRange(model.observedCallEarliest, model.observedCallLatest)).")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            Text("Observed messages: \(model.missingSources.contains("messages") ? "unavailable" : dateRange(model.observedMessageEarliest, model.observedMessageLatest)).")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            if !model.missingSources.isEmpty {
                ForEach(["calls", "messages"], id: \.self) { category in
                    if model.missingSources.contains(category) {
                        Text(model.unavailableDescription(category))
                            .foregroundStyle(Brand.muted)
                    }
                }
            }
            Text("A local preview is not an account save. Keep this recovery copy until the browser confirms the matching save. Disconnect or closing after confirmation discards this private local review.")
                .foregroundStyle(Brand.muted)
            Button(model.pairCode.isEmpty ? "Create local browser pairing" : "Reconnect current reviewed selection") {
                continueAfterBrowserShare = false
                confirmingBrowserShare = true
            }
                .buttonStyle(SecondaryButton())
                .disabled(model.selectionNeedsReview || model.selectionChangePending ||
                          model.accountSaveAwaitingBrowserReceipt || model.handoffAcknowledged)
                .opacity(model.selectionNeedsReview || model.selectionChangePending ||
                         model.accountSaveAwaitingBrowserReceipt || model.handoffAcknowledged ? 0.55 : 1)
            Text(model.accountSaveAwaitingBrowserReceipt
                 ? "Saving is being finalized. Finish in the browser before changing contacts or reconnecting."
                 : "Reconnect creates a fresh one-use link from this retained review. It does not collect from the phone again; an earlier browser connection may stop working.")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            if model.selectionChangePending {
                Text("Checking that changing contacts won’t interrupt a save…")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
            } else if model.canRequestSelectionChange {
                Button("Change selected contacts") { model.requestSelectionChange() }
                    .buttonStyle(SecondaryButton())
                Text("This checks whether the current one-use link can close safely. Your phone import stays in this app.")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
            }
            if !model.handoffNotice.isEmpty {
                Text(model.handoffNotice)
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
            }
            if !model.errorMessage.isEmpty {
                Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled)
            }
            if !model.pairCode.isEmpty {
                Text("In your signed-in account, choose Connect. Confirm the destination here before the reviewed preview transfers.")
                    .foregroundStyle(Brand.muted)
                if !model.destinationIntentID.isEmpty {
                    Text("Destination: \(model.destinationEmail) · \(model.destinationCourse)")
                        .foregroundStyle(Brand.heading)
                    if model.destinationApproved {
                        Text(model.accountSaveAwaitingBrowserReceipt
                             ? "The Mac received the first bound account-save receipt. Keep this review open until the browser confirms the final receipt."
                             : model.browserReceived
                             ? "The browser received the reviewed preview. Verify source choices there; the final account-save receipt has not arrived here."
                             : "Destination approved. Check the account browser for the reviewed preview. Approval alone does not prove the preview arrived or saved any source.")
                            .foregroundStyle(Brand.muted)
                    } else {
                        Button("Review destination approval") {
                            if model.pendingDestinationApproval != nil { destinationReviewRequestID = UUID() }
                        }
                            .buttonStyle(LimeButton())
                    }
                }
                Text("One-time code: \(model.pairCode.prefix(5))-\(model.pairCode.suffix(5))")
                    .font(.custom("Arial", size: 18).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .textSelection(.enabled)
                Text("The pairing code expires after five minutes. An active, authorized transfer stays open while pages are requested and for up to two hours without activity. Only the exact AMPLIFai account origin can request pages; page requests do not prove an account save.")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
                Button("Copy temporary code") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(model.pairCode, forType: .string)
                }
                .buttonStyle(SecondaryButton())
                Link("Open AMPLIFai account in browser", destination: URL(string: "https://amplifai-database-engine.vercel.app/phone/account")!)
                    .buttonStyle(SecondaryButton())
            }
        }
        .panel()
    }

    private func metric(_ value: Int, _ label: String, unavailable: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(unavailable ? "Unavailable" : value.formatted())
                .font(.custom("Arial", size: 28).weight(.bold))
                .foregroundStyle(Brand.lime)
            Text(label).font(.caption).foregroundStyle(Brand.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
            .padding(20)
            .background(Brand.panel, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Brand.border, lineWidth: 1))
    }
}

extension View {
    func panel() -> some View { modifier(PanelStyle()) }
}

struct LimeButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.custom("Arial", size: 14).weight(.bold))
            .foregroundStyle(Brand.background)
            .padding(.horizontal, 18)
            .frame(minHeight: 40)
            .background(Brand.lime.opacity(configuration.isPressed ? 0.8 : 1), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct SecondaryButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.custom("Arial", size: 14).weight(.bold))
            .foregroundStyle(Brand.heading)
            .padding(.horizontal, 18)
            .frame(minHeight: 40)
            .background(Brand.border.opacity(configuration.isPressed ? 0.8 : 1), in: RoundedRectangle(cornerRadius: 8))
    }
}
