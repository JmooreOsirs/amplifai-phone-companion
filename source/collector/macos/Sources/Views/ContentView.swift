import SwiftUI

struct ContentView: View {
    @ObservedObject var model: CollectorModel
    @ObservedObject var updater: CompanionUpdater
    /// The system default opens the browser; a host can intercept it (the fake-data demo does).
    @Environment(\.openURL) private var openURL
    @State private var search = ""
    @State private var confirmingCleanup = false
    @State private var showingConnectionHelp = false
    @State private var showingReviewedContacts = false
    @State private var destinationPromptVisible = false
    @State private var destinationReviewRequestID: UUID?
    @State private var localCollectionChecked = false
    @State private var showingCollectionDetails = false
    @State private var showingFinderGuide = false
    @State private var finderGuideStep = 0
    @AccessibilityFocusState private var finderGuideHeadingFocused: Bool
    @State private var showingUpdates = false
    @State private var confirmingRetainedReviewDiscard = false
    @State private var confirmingBulkMove = false
    @State private var showingGroupTools = false

    @MainActor init(model: CollectorModel, updater: CompanionUpdater, initialCollectionAgreement: Bool = false,
                    initialDestinationPromptVisible: Bool = false) {
        self.model = model
        self.updater = updater
        _search = State(initialValue: model.workspace?.query ?? "")
        _localCollectionChecked = State(initialValue: initialCollectionAgreement)
        _destinationPromptVisible = State(initialValue: initialDestinationPromptVisible)
    }

    var body: some View {
        GeometryReader { geometry in
        ScrollViewReader { setupScroll in
        ScrollView {
            VStack(alignment: .leading, spacing: Brand.Space.section) {
                header
                JourneySteps(current: model.journeyStep)
                if model.companionStatus == .anotherAppRunning || model.companionStatus == .unavailable { companionStatusNotice }
                if !model.residues.isEmpty { residueNotice }
                if model.diagnosticRetention == .failed {
                    Text("Safe source details could not be retained or restored on this Mac. Copy any current source details before closing.")
                        .font(Brand.bodyFont(16))
                        .foregroundStyle(Brand.body)
                        .panel()
                }
                if !model.sourceDiagnosticHistory.isEmpty { previousSourceDetails }
                if model.phase == .error || model.phase == .completed || (model.phase == .idle && model.needsStorageReview) { statusPanel }
                // A confirmed save ends on its own summary; starting again means closing and reopening.
                if (!model.helperRunning || model.backupSettingCheckInProgress) &&
                    (model.phase == .idle || model.phase == .error) {
                    if model.hasUnsavedReview { retainedReviewRecovery }
                    else { collectionApprovalPanel.id("collection-setup") }
                }
                if ![.error, .idle, .selecting, .reviewing, .completed].contains(model.phase) { statusPanel }
                if model.canInstallUpdate && model.backupEncryptionStatus == .notChecked && model.localCollectionApproval == nil && !showingFinderGuide { updatePanel }
                if model.phase == .passwordRequired { passwordPanel }
                if model.phase == .selecting {
                    contactPanel(wellHeight: Brand.Layout.contactWellHeight(windowHeight: geometry.size.height))
                }
                if model.phase == .reviewing {
                    reviewPanel
                    if model.canEditWorkspace {
                        // No link is open, so the selection can change; an edit returns to Choose.
                        contactPanel(wellHeight: Brand.Layout.contactWellHeight(windowHeight: geometry.size.height))
                    } else {
                        // The locked selection stays available without pushing the next action below the fold.
                        DisclosureGroup("Show the \(model.selectedIDs.count.formatted()) selected contacts", isExpanded: $showingReviewedContacts) {
                            contactPanel(wellHeight: Brand.Layout.contactWellHeight(windowHeight: geometry.size.height))
                                .padding(.top, 12)
                        }
                        .font(Brand.bodyFont(15))
                    }
                }
            }
            .frame(maxWidth: contentMaximumWidth)
            .padding(geometry.size.width < 600 ? 16 : Brand.Space.page)
            .frame(maxWidth: .infinity)
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if model.phase == .selecting { contactActionDock }
        }
        .background(Brand.background)
        .font(Brand.bodyFont(16))
        .tint(Brand.lime)
        .foregroundStyle(Brand.body)
        .background(ActiveCompanionWindow(model: model, destinationReviewRequestID: destinationReviewRequestID,
                                          onDestinationPromptChange: { destinationPromptVisible = $0 })
            .frame(width: 0, height: 0))
        .onChange(of: model.collectionApprovalRunID) { _, _ in
            localCollectionChecked = false
            showingCollectionDetails = false
            showingFinderGuide = false
        }
        .onChange(of: model.backupEncryptionStatus) { _, status in
            // A new phone reading changes what the agreement describes, so it must be given again.
            localCollectionChecked = false
            if status != .unencrypted { showingFinderGuide = false }
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
                  let url = URL(string: "https://amplifai.satoris.ai/phone/account?continue=reviewed") else { return }
            openURL(url) { accepted in if !accepted { model.accountNavigationFailed() } }
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
            if model.phase == .selecting {
                Button("Discard review…") { confirmingRetainedReviewDiscard = true }
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

    private var collectionApprovalPanel: some View {
        VStack(alignment: .leading, spacing: 18) {
            if showingFinderGuide && model.backupEncryptionStatus == .unencrypted {
                Text("Optional: include call history")
                    .font(Brand.headingFont(28))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                Text("Backup encryption is Off. This Finder setup is optional; without it, this app still reads any available calls.")
                    .font(Brand.bodyFont(16))
                    .foregroundStyle(Brand.body)
                Button("Back to collection") { showingFinderGuide = false }
                    .buttonStyle(SecondaryButton())
                finderEncryptionGuide
            } else {
                Text("Collect phone data on this Mac")
                    .font(Brand.headingFont(28))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                Text("Copy this iPhone's backup to this Mac, read it here, then choose which contacts to share.")
                collectionFacts
                collectionDetails
                iPhoneCheckCard
            }
        }
        .font(Brand.bodyFont(18))
        .panel()
    }

    private var iPhoneCheckCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch model.backupEncryptionStatus {
            case .notChecked:
                Text("Connect the iPhone by USB, unlock it and choose Trust on the iPhone if asked. Checking only reads its backup setting; nothing is copied yet.")
                    .font(Brand.bodyFont(16))
                Button("Check iPhone") { checkBackupSetting() }
                    .buttonStyle(LimeButton())
                    .disabled(!model.canPrepareCollection || model.localCollectionApproval != nil)
            case .checking:
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Checking the iPhone… No backup or collection has started.")
                        .font(Brand.bodyFont(16))
                }
                if model.backupSettingCheckInProgress {
                    Button("Cancel check") { model.cancelBackupEncryptionStatusCheck() }
                        .buttonStyle(SecondaryButton())
                }
            case .encrypted, .unencrypted:
                VerifiedStatusRow(verified: true, title: "iPhone checked · backup encryption \(backupSettingLabel)",
                                  detail: backupSettingDetail)
                collectionConsentStep
                HStack(spacing: 16) {
                    Button("Check again") { checkBackupSetting() }
                        .buttonStyle(QuietAction())
                        .disabled(!model.canPrepareCollection)
                    if model.backupEncryptionStatus == .unencrypted {
                        Button("Optional: set up call history in Finder") {
                            finderGuideStep = 0
                            showingFinderGuide = true
                        }
                        .buttonStyle(QuietAction())
                    }
                }
            case .unknown:
                VerifiedStatusRow(verified: false, title: "Backup setting not confirmed", detail: backupSettingDetail)
                Button("Check iPhone again") { checkBackupSetting() }
                    .buttonStyle(LimeButton())
                    .disabled(!model.canPrepareCollection)
                Divider()
                collectionConsentStep
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Brand.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border, lineWidth: 1))
    }

    private func checkBackupSetting() {
        localCollectionChecked = false
        showingFinderGuide = false
        model.checkBackupEncryptionStatus()
    }

    private func startCollection() {
        model.agreeAndStartCollection(for: model.collectionApprovalRunID, checked: localCollectionChecked)
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
        case .encrypted: return "Calls can be included. When asked, enter this iPhone's existing backup password on this Mac. The setting is checked again when collection starts."
        case .unencrypted: return "Available data will be read. An unencrypted backup may not include call history."
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
        let step = finderEncryptionSteps[finderGuideStep]
        return VStack(alignment: .leading, spacing: 12) {
            Text("To prepare call history in Finder")
                .font(Brand.headingFont(22))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            Text("Step \(finderGuideStep + 1) of \(finderEncryptionSteps.count) · Finder guide")
                .font(Brand.headingFont(16))
                .foregroundStyle(Brand.lime)
            HStack(spacing: 6) {
                ForEach(finderEncryptionSteps.indices, id: \.self) { index in
                    Capsule()
                        .fill(index == finderGuideStep ? Brand.lime : index < finderGuideStep ? Brand.blue : Brand.border)
                        .frame(maxWidth: .infinity)
                        .frame(height: 5)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Finder guide, step \(finderGuideStep + 1) of \(finderEncryptionSteps.count)")
            finderIllustration
            Text(step.title)
                .font(Brand.headingFont(22))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
                .accessibilityFocused($finderGuideHeadingFocused)
            Text(step.detail)
                .font(Brand.bodyFont(18))
                .foregroundStyle(Brand.body)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if finderGuideStep == 2 {
                Text("Turning encryption on starts a Finder backup and can overwrite and encrypt earlier local backups. Finder needs separate Mac storage beyond this app's 2 GiB reserve. Let that backup finish.")
                    .font(Brand.bodyFont(16))
                    .foregroundStyle(Brand.body)
                    .textSelection(.enabled)
            }
            if finderGuideStep == 3 {
                Button("Recheck iPhone setting") { checkBackupSetting() }
                    .buttonStyle(LimeButton())
                    .disabled(!model.canPrepareCollection)
            }
            HStack(spacing: 12) {
                Button("Back") { finderGuideStep -= 1 }
                    .buttonStyle(SecondaryButton())
                    .disabled(finderGuideStep == 0)
                    .frame(minHeight: 44)
                if finderGuideStep < finderEncryptionSteps.count - 1 {
                    Button("Next") { finderGuideStep += 1 }
                        .buttonStyle(LimeButton())
                        .frame(minHeight: 44)
                }
            }
            DisclosureGroup("iPhone missing in Finder or password unknown?") {
                Text("Use a data-capable USB cable, unlock the iPhone and answer any normal Allow or Trust prompt yourself. In Finder, use View > Show Sidebar and check Locations. Keep an existing backup password; AMPLIFai cannot reset it or change encryption for you.")
                .font(Brand.bodyFont(16))
                .foregroundStyle(Brand.body)
                .textSelection(.enabled)
            }
            .font(Brand.bodyFont(16))
        }
        .onChange(of: finderGuideStep) { _, _ in finderGuideHeadingFocused = true }
    }

    @ViewBuilder private var finderIllustration: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("FINDER GUIDE · ILLUSTRATION")
                .font(Brand.headingFont(12))
                .foregroundStyle(Brand.muted)
            switch finderGuideStep {
            case 0:
                HStack(spacing: 10) {
                    Image(systemName: "face.smiling").font(.system(size: 30)).foregroundStyle(Brand.blue)
                    Text("Finder in the Dock").font(Brand.headingFont(18))
                    Spacer(minLength: 0)
                    Image(systemName: "iphone.gen3").font(.system(size: 30)).foregroundStyle(Brand.lime)
                }
            case 1:
                HStack(alignment: .top, spacing: 0) {
                    VStack(alignment: .leading, spacing: 9) {
                        Text("Locations").font(Brand.headingFont(14))
                        Text("▸ This iPhone").foregroundStyle(Brand.lime)
                        Text("◇ AMPLIFai Phone disk").foregroundStyle(Brand.muted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Rectangle().fill(Brand.border).frame(width: 1)
                    Text("Select the iPhone device")
                        .font(Brand.bodyFont(16))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.leading, 12)
                }
            case 2:
                VStack(alignment: .leading, spacing: 9) {
                    HStack(spacing: 12) {
                        Text("General").foregroundStyle(Brand.lime)
                        Text("Music").foregroundStyle(Brand.muted)
                        Text("Files").foregroundStyle(Brand.muted)
                    }
                    .font(Brand.headingFont(15))
                    Rectangle().fill(Brand.border).frame(height: 1)
                    Text("Backups").font(Brand.headingFont(16))
                    HStack(spacing: 8) {
                        Image(systemName: "square").foregroundStyle(Brand.lime)
                        Text("Encrypt local backup")
                    }
                    .font(Brand.bodyFont(16))
                }
            default:
                VStack(alignment: .leading, spacing: 8) {
                    Label("Finder backup finishes", systemImage: "clock")
                    Label("Return and recheck", systemImage: "arrow.right")
                }
                .font(Brand.headingFont(16))
            }
        }
        .font(Brand.bodyFont(16))
        .foregroundStyle(Brand.heading)
        .textSelection(.enabled)
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 104, alignment: .leading)
        .background(Brand.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border, lineWidth: 1))
    }

    private var finderEncryptionSteps: [(title: String, detail: String)] {
        [
            ("Open Finder", "Connect the unlocked iPhone with a data-capable USB cable. Decide any Allow or Trust prompt on the phone yourself, then open Finder on this Mac."),
            ("Select this iPhone", "In Finder's Locations sidebar, select the iPhone. The AMPLIFai Phone installer disk is a different item. If Locations is hidden, use View > Show Sidebar."),
            ("Prepare the local backup", "Open General, then Backups. Choose Encrypt local backup and set a password you can keep. AMPLIFai does not change this setting for you."),
            ("Finish, then recheck", "Let Finder finish its backup. Return to AMPLIFai and choose Recheck iPhone setting. Keep the existing backup password; enter it only in this Mac app if asked. Even an encrypted backup may not contain every older call.")
        ]
    }

    private var collectionSystemRequirements: String {
        #if arch(x86_64)
        let computer = "an Intel Mac"
        #else
        let computer = "an Apple M-series Mac"
        #endif
        return "Requires \(computer) with macOS 14 or later, a data-capable USB cable and enough Mac storage for selected databases, parsing copies and a further 2 GiB reserve. Internet is needed later for diagnostic delivery, browser handoff and account save; the USB backup runs locally."
    }

    /// Upfront summary of what this collection does; shown before the iPhone check and the agreement.
    private var collectionFacts: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 360), spacing: 20, alignment: .top)], alignment: .leading, spacing: 14) {
            consentFact("externaldrive", "Arrives on this Mac, temporarily",
                        "The iPhone's full backup, including message content. Only contacts, calls and messages are kept for this review; the rest is discarded as it arrives.")
            consentFact("lock.doc", "Stays on this Mac",
                        "Backup files, message text and the backup password, which is never saved.")
            consentFact("person.2", "Shared only when you choose",
                        "After you pick contacts and confirm Share: their names and phone numbers, plus matching call and message metadata (dates, participants, call duration, message direction and transport), go to your signed-in AMPLIFai account. Saving needs your approval there.")
            consentFact("lifepreserver", "If something fails",
                        "A safe code is sent: build, stage, failure type, elapsed time and byte counts. Never records, content, passwords or paths.")
        }
    }

    private var collectionDetails: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(showingCollectionDetails ? "Hide exact collection details" : "Show exact collection details") {
                showingCollectionDetails.toggle()
            }
            .buttonStyle(QuietAction())
            if showingCollectionDetails {
                VStack(alignment: .leading, spacing: 10) {
                    Text(collectionSystemRequirements)
                    Text("The local review can include phone numbers, dates, participants, call duration and message transport/direction. Physical source coverage varies. Changing Finder encryption after a collection requires another owner-approved capture.")
                    Text("This approval permits one local collection. It does not approve Apple's Trust prompt, supply a password, authorize browser sharing, or save account data. Browser transfer and optional source saves require separate confirmation.")
                    Text("Safe diagnostics contain build, random reference, failure category and stage, elapsed time, byte counts and optional numeric device status. They exclude phone records, backup files, paths and raw errors. Cleanup is attempted; marked residue must be inspected here.")
                    Text("Disclosure: \(CollectorModel.collectionDisclosureVersion)").textSelection(.enabled)
                }
                .font(Brand.bodyFont(16))
                .foregroundStyle(Brand.muted)
                .brandControls()
            }
        }
    }

    private var collectionConsentStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            if model.localCollectionApproval == nil {
                VStack(alignment: .leading, spacing: 6) {
                    Toggle("I agree to this one-time collection and the safe failure diagnostic", isOn: $localCollectionChecked)
                        .toggleStyle(BrandCheckbox())
                        .disabled(!model.canPrepareCollection)
                    Text("Applies to this collection only. Apple's Trust prompt, the backup password, sharing and Save each still ask you.")
                        .font(Brand.bodyFont(13))
                        .foregroundStyle(Brand.muted)
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { collectionApprovalActions }
                VStack(alignment: .leading, spacing: 12) { collectionApprovalActions }
            }
        }
    }

    private func consentFact(_ symbol: String, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 20))
                .foregroundStyle(Brand.blue)
                .frame(width: 24)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
                Text(detail).font(Brand.bodyFont(15)).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Unknown settings keep the recheck as the main action; starting anyway stays possible but secondary.
    @ViewBuilder private var collectionApprovalActions: some View {
        let startDisabled = !localCollectionChecked || !model.canPrepareCollection || model.settingCallHistoryPreparation == nil
        if model.backupEncryptionStatus == .unknown {
            Button("Start without confirmed call history") { startCollection() }
                .buttonStyle(SecondaryButton())
                .disabled(startDisabled)
        } else {
            Button("Start collection") { startCollection() }
                .buttonStyle(LimeButton())
                .disabled(startDisabled)
        }
        Button("Not now") {
            localCollectionChecked = false
            model.declineLocalCollection()
        }
        .buttonStyle(SecondaryButton())
        .keyboardShortcut(.cancelAction)
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
                .font(Brand.headingFont(22))
                .foregroundStyle(Brand.heading)
            Text("Use the password already set for this iPhone's encrypted computer backup, not its screen-unlock passcode. Ask its owner if you do not know it. This app never changes or resets encryption; the password stays in the local helper and is not saved.")
                .font(Brand.bodyFont(18))
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
        }
        .panel()
    }

    private func contactPanel(wellHeight: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            currentSourceDetails
            if let workspace = model.workspace {
                contactSelectionSummary
                ContactReviewView(model: model, workspace: workspace, wellHeight: wellHeight,
                                  search: $search, showingGroupTools: $showingGroupTools,
                                  confirmingBulkMove: $confirmingBulkMove)
            } else {
                contactSelectionSummary
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
        if !model.missingSources.isEmpty || !model.currentSourceDiagnostics.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(["calls", "messages"], id: \.self) { category in
                    if model.missingSources.contains(category) {
                        Text(model.unavailableDescription(category))
                            .foregroundStyle(Brand.muted)
                    }
                }
                if !model.currentSourceDiagnostics.isEmpty {
                    DisclosureGroup("Source details for this run") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(model.currentSourceDiagnostics) { record in
                                Text(record.detail).textSelection(.enabled)
                            }
                            if model.diagnosticRetention == .retained {
                                Text("These safe source details were retained on this Mac.")
                            }
                        }
                        .foregroundStyle(Brand.muted)
                        .padding(.top, 6)
                    }
                    .font(Brand.bodyFont(16))
                }
            }
        }
    }

    private var previousSourceDetails: some View {
        DisclosureGroup("Previous run source details") {
            VStack(alignment: .leading, spacing: 12) {
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
            .padding(.top, 6)
        }
        .font(Brand.bodyFont(16))
        .panel()
    }

    private var contactActionDock: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: Brand.Space.section) { contactActionContent }
            VStack(alignment: .leading, spacing: Brand.Space.item) { contactActionContent }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 12)
        .frame(maxWidth: Brand.Layout.workspaceMaximum, alignment: .leading)
        .frame(maxWidth: .infinity)
        .background(Brand.panel)
        .overlay(alignment: .top) { Rectangle().fill(Brand.border).frame(height: 1) }
    }

    @ViewBuilder private var contactActionContent: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(contactActionHelp)
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
                .fixedSize(horizontal: false, vertical: true)
            if model.reviewPending {
                Text("Reviewing selected metadata…").font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        if model.canEditWorkspace {
            Button("Continue to account") { model.reviewWorkspace(continueToAccount: true) }
            .buttonStyle(LimeButton())
            .disabled(model.selectedIDs.isEmpty)
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
        if model.accountSaveAwaitingBrowserReceipt { return "Saving is being finalized. Finish in the browser before changing contacts." }
        if model.selectionChangePending { return "Checking that changing contacts won’t interrupt a save… Your reviewed selection is still intact." }
        if !model.handoffID.isEmpty { return "This selection is linked to the account browser. Finish source choices there, or choose Change selected contacts below." }
        return "\(model.selectedIDs.count.formatted()) contacts selected. You will confirm the account before sharing. Save is approved separately in your account."
    }

    private var contactActionStatus: String {
        if model.reviewPending { return "Preparing your review…" }
        if model.suggestionScanning { return "Checking all contacts…" }
        if model.workspace == nil { return "Contact review is not ready" }
        if model.accountSaveAwaitingBrowserReceipt { return "Waiting for account Save receipt" }
        if model.selectionChangePending { return "Checking the current transfer…" }
        if !model.handoffID.isEmpty { return "Continue in your account browser" }
        return "Local review is retained"
    }

    private var contactSelectionSummary: some View {
        VStack(alignment: .leading, spacing: 12) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 20) { contactTitle; Spacer(minLength: 0); contactTitleCount }
                VStack(alignment: .leading, spacing: 8) { contactTitle; contactTitleCount }
            }
            Text("Main contacts start selected; Review contacts wait for you. Nothing is saved until you confirm in your account.")
                .font(Brand.bodyFont(15))
            DisclosureGroup("Collection details") {
                Text(model.statusDetail).font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
            }
            .font(Brand.bodyFont(13))
            if !model.errorMessage.isEmpty && model.phase == .selecting {
                Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var contactTitle: some View {
        Text("Choose contacts to share").font(Brand.headingFont(28)).foregroundStyle(Brand.heading)
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
        VStack(alignment: .leading, spacing: 14) {
            Text("Share with your account")
                .font(Brand.headingFont(28))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            Text(model.statusTitle)
                .font(Brand.headingFont(16))
                .foregroundStyle(Brand.heading)
            Text("Shared after you confirm the account: the \(model.selectedIDs.count.formatted()) selected contacts' names and phone numbers and their matching call and message metadata. Never shared: message text, backup files or passwords. Save is approved separately in your account.")
                .font(Brand.bodyFont(15))
            shareNextStep
            if !model.handoffNotice.isEmpty {
                Text(model.handoffNotice)
                    .font(Brand.bodyFont(13))
                    .foregroundStyle(Brand.muted)
            }
            if !model.errorMessage.isEmpty {
                Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled)
            }
            Text("Local review of the contacts you chose")
                .font(Brand.headingFont(16))
                .foregroundStyle(Brand.heading)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 16)], alignment: .leading, spacing: 16) {
                metric(model.selectedIDs.count, "Selected contacts")
                metric(model.matchedCalls, "Matching calls", unavailable: model.missingSources.contains("calls"))
                metric(model.matchedMessages, "Matching messages", unavailable: model.missingSources.contains("messages"))
            }
            .brandWell()
            Text("The collector attempted all retained available history. The observed dates below do not prove complete phone history.")
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
            Text("Observed calls: \(model.missingSources.contains("calls") ? "unavailable" : dateRange(model.observedCallEarliest, model.observedCallLatest)).")
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
            Text("Observed messages: \(model.missingSources.contains("messages") ? "unavailable" : dateRange(model.observedMessageEarliest, model.observedMessageLatest)).")
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
            currentSourceDetails
            Text("A shared preview is not an account save. This Mac keeps its private local review until your account confirms the matching save, then clears it.")
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { reviewRecoveryActions }
                VStack(alignment: .leading, spacing: 12) { reviewRecoveryActions }
            }
            if model.selectionChangePending {
                Text("Checking that changing contacts won’t interrupt a save…")
                    .font(Brand.bodyFont(13))
                    .foregroundStyle(Brand.muted)
            }
            connectionHelp
        }
        .panel()
    }

    /// The one normal next action for sharing; later gates stay on this Mac and in the account.
    @ViewBuilder private var shareNextStep: some View {
        VStack(alignment: .leading, spacing: 10) {
            if model.accountSaveAwaitingBrowserReceipt {
                progressLine("Saving — waiting for your browser to confirm")
                Text("Your account received the first save receipt. Keep this window open until the browser confirms the final receipt.")
                    .foregroundStyle(Brand.body)
            } else if !model.destinationIntentID.isEmpty {
                Text("Account: \(model.destinationEmail) · Course: \(model.destinationCourse)")
                    .foregroundStyle(Brand.heading)
                if model.destinationApproved {
                    if model.browserReceived {
                        Text("Preview received — finish Save in your browser")
                            .font(Brand.headingFont(18)).foregroundStyle(Brand.heading)
                        Text("Save each source in your AMPLIFai account. Nothing is saved until you do. This Mac keeps its review until your account confirms the save.")
                            .foregroundStyle(Brand.body)
                    } else {
                        progressLine("Sending the preview to your browser…")
                    }
                } else if let choice = model.submittedDestinationDecision {
                    progressLine(choice ? "Confirming your sharing choice…" : "Declining this account…")
                } else if destinationPromptVisible {
                    // The sheet is the only place to decide; nothing here competes with it.
                    Text("Answer the dialog on this Mac: Share with this account, or Not this account.")
                        .foregroundStyle(Brand.muted)
                } else {
                    Text(model.destinationShareUnconfirmed
                         ? "Sharing has not been confirmed. Review the account to try again."
                         : "Nothing has been shared yet. Review the account when you are ready.")
                        .foregroundStyle(Brand.body)
                    Button("Review account…") {
                        if model.pendingDestinationApproval != nil { destinationReviewRequestID = UUID() }
                    }
                    .buttonStyle(QuietAction())
                }
            } else if !model.pairCode.isEmpty {
                Text("In your browser, under Connect your reviewed phone, choose Connect. This Mac then asks you to confirm the account and course before anything is sent.")
                    .foregroundStyle(Brand.body)
                Link("Open AMPLIFai account in browser", destination: URL(string: "https://amplifai.satoris.ai/phone/account")!)
                    .buttonStyle(LimeButton())
            } else if model.accountLinkPending {
                Text("Opening your signed-in AMPLIFai account…")
                    .foregroundStyle(Brand.body)
            } else {
                Text("Open your signed-in AMPLIFai account to continue. You will confirm the account here before sharing.")
                    .foregroundStyle(Brand.body)
                Button("Continue to account") { model.pairBrowser(automaticAccountNavigation: true) }
                    .buttonStyle(LimeButton())
                    .disabled(!canCreateLink)
            }
        }
        .brandControls()
    }

    private func progressLine(_ text: String) -> some View {
        HStack(spacing: 10) {
            ProgressView().controlSize(.small)
            Text(text).font(Brand.headingFont(18)).foregroundStyle(Brand.heading)
        }
    }

    private var canCreateLink: Bool {
        !(model.selectionNeedsReview || model.selectionChangePending ||
          model.accountSaveAwaitingBrowserReceipt || model.handoffAcknowledged || model.accountLinkPending)
    }

    @ViewBuilder private var reviewRecoveryActions: some View {
        if model.canRequestSelectionChange {
            Button("Change selected contacts") { model.requestSelectionChange() }
                .buttonStyle(SecondaryButton())
        }
        Button("Discard review…") { confirmingRetainedReviewDiscard = true }
            .buttonStyle(SecondaryButton())
    }

    /// Manual pairing, the one-time code and link lifetime details, closed until needed.
    private var connectionHelp: some View {
        DisclosureGroup("Having trouble connecting?", isExpanded: $showingConnectionHelp) {
            VStack(alignment: .leading, spacing: 12) {
                Text("If the browser did not open or the link expired, create a new one-time link. It reuses this review and does not collect from the iPhone again. An earlier link may stop working.")
                    .font(Brand.bodyFont(13))
                    .foregroundStyle(Brand.muted)
                Button(model.pairCode.isEmpty ? "Create one-time link" : "Create a new one-time link") { model.pairBrowser() }
                    .buttonStyle(SecondaryButton())
                    .disabled(!canCreateLink)
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
                .foregroundStyle(Brand.lime)
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
