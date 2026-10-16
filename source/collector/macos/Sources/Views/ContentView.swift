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
    @State private var finderGuideStep = 0
    @AccessibilityFocusState private var finderGuideHeadingFocused: Bool
    @State private var showingUpdates = false
    @State private var confirmingRetainedReviewDiscard = false
    @State private var confirmingBulkMove = false
    @State private var showingGroupTools = false

    var body: some View {
        ScrollViewReader { setupScroll in
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                if model.companionStatus == .anotherAppRunning || model.companionStatus == .unavailable { companionStatusNotice }
                if !model.residues.isEmpty { residueNotice }
                if model.diagnosticRetention == .failed {
                    Text("Safe source details could not be retained or restored on this Mac. Copy any current source details before closing.")
                        .font(.custom("Arial", size: 16))
                        .foregroundStyle(Brand.body)
                        .panel()
                }
                if !model.sourceDiagnosticHistory.isEmpty { previousSourceDetails }
                if model.phase == .error || (model.phase == .idle && model.needsStorageReview) { statusPanel }
                if (!model.helperRunning || model.backupSettingCheckInProgress) &&
                    (model.phase == .idle || model.phase == .error || model.phase == .completed) {
                    if model.hasUnsavedReview { retainedReviewRecovery }
                    else { collectionApprovalPanel.id("collection-setup") }
                }
                if model.phase != .error && model.phase != .idle { statusPanel }
                if model.canInstallUpdate && callHistoryPreparation == nil && model.localCollectionApproval == nil && !showingFinderGuide { updatePanel }
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
                  let url = URL(string: "https://amplifai.satoris.ai/phone/account?continue=reviewed") else { return }
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
                .font(.custom("Arial", size: 18).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text(model.companionStatus == .anotherAppRunning
                 ? "Close the other AMPLIFai Phone app, then reopen this one. Collection stays paused while the other app owns the local connection."
                 : "Reopen this app to restore its local connection before collecting. If this continues, reinstall the matching Mac companion from the download guide.")
                .font(.custom("Arial", size: 16))
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
                Text("Approve collection")
                    .font(.custom("Arial", size: 26).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                HStack(alignment: .top, spacing: 12) {
                    Text("Backup \(backupSettingLabel) · \(callHistoryChoiceSummary(choice))")
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
            } else if showingFinderGuide && model.backupEncryptionStatus == .unencrypted {
                Text("Backup encryption is Off. Finder setup is optional; this app will still read any available Calls.")
                    .font(.custom("Arial", size: 16))
                    .foregroundStyle(Brand.body)
                Button("Back to collection choices") { showingFinderGuide = false }
                    .buttonStyle(SecondaryButton())
                finderEncryptionGuide
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
                    Button("Prepare call history in Finder") {
                        finderGuideStep = 0
                        showingFinderGuide = true
                    }
                    .buttonStyle(SecondaryButton())
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
            } else if model.backupEncryptionStatus != .checking && !showingFinderGuide {
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
        let step = finderEncryptionSteps[finderGuideStep]
        return VStack(alignment: .leading, spacing: 12) {
            Text("To prepare call history in Finder")
                .font(.custom("Arial", size: 22).weight(.bold))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            Text("Step \(finderGuideStep + 1) of \(finderEncryptionSteps.count) · Finder guide")
                .font(.custom("Arial", size: 16).weight(.bold))
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
                .font(.custom("Arial", size: 22).weight(.bold))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
                .accessibilityFocused($finderGuideHeadingFocused)
            Text(step.detail)
                .font(.custom("Arial", size: 18))
                .foregroundStyle(Brand.body)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if finderGuideStep == 2 {
                Text("Turning encryption on starts a Finder backup and can overwrite and encrypt earlier local backups. Finder needs separate Mac storage beyond this app's 2 GiB reserve. Let that backup finish.")
                    .font(.custom("Arial", size: 16))
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
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.body)
                .textSelection(.enabled)
            }
            .font(.custom("Arial", size: 16))
        }
        .onChange(of: finderGuideStep) { _, _ in finderGuideHeadingFocused = true }
    }

    @ViewBuilder private var finderIllustration: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("FINDER GUIDE · ILLUSTRATION")
                .font(.custom("Arial", size: 12).weight(.bold))
                .foregroundStyle(Brand.muted)
            switch finderGuideStep {
            case 0:
                HStack(spacing: 10) {
                    Image(systemName: "face.smiling").font(.system(size: 30)).foregroundStyle(Brand.blue)
                    Text("Finder in the Dock").font(.custom("Arial", size: 18).weight(.bold))
                    Spacer(minLength: 0)
                    Image(systemName: "iphone.gen3").font(.system(size: 30)).foregroundStyle(Brand.lime)
                }
            case 1:
                HStack(alignment: .top, spacing: 0) {
                    VStack(alignment: .leading, spacing: 9) {
                        Text("Locations").font(.custom("Arial", size: 14).weight(.bold))
                        Text("▸ This iPhone").foregroundStyle(Brand.lime)
                        Text("◇ AMPLIFai Phone disk").foregroundStyle(Brand.muted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Rectangle().fill(Brand.border).frame(width: 1)
                    Text("Select the iPhone device")
                        .font(.custom("Arial", size: 16))
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
                    .font(.custom("Arial", size: 15).weight(.bold))
                    Rectangle().fill(Brand.border).frame(height: 1)
                    Text("Backups").font(.custom("Arial", size: 16).weight(.bold))
                    HStack(spacing: 8) {
                        Image(systemName: "square").foregroundStyle(Brand.lime)
                        Text("Encrypt local backup")
                    }
                    .font(.custom("Arial", size: 16))
                }
            default:
                VStack(alignment: .leading, spacing: 8) {
                    Label("Finder backup finishes", systemImage: "clock")
                    Label("Return and recheck", systemImage: "arrow.right")
                }
                .font(.custom("Arial", size: 16).weight(.bold))
            }
        }
        .font(.custom("Arial", size: 16))
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

    private var collectionConsentStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            consentFact("externaldrive", "This Mac temporarily receives full backup bytes, including unrelated data and message content. Unselected bytes are discarded.")
            consentFact("checkmark.shield", "Only chosen metadata may reach your account after separate browser and source approvals. Message text and backup files stay here.")
            consentFact("lock.doc", "The backup password stays local and unsaved. Interrupted runs may leave marked temporary data. Safe diagnostics send codes and counts, never records, content, passwords or paths.")
            Button(showingCollectionDetails ? "Hide exact collection details" : "Show exact collection details") {
                showingCollectionDetails.toggle()
            }
            .buttonStyle(SecondaryButton())
            if showingCollectionDetails {
                VStack(alignment: .leading, spacing: 10) {
                    Text(collectionSystemRequirements)
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

    private func consentFact(_ symbol: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 20))
                .foregroundStyle(Brand.blue)
                .frame(width: 24)
                .accessibilityHidden(true)
            Text(detail).font(.custom("Arial", size: 18)).fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var collectionApprovalActions: some View {
        Button("Agree to collection") {
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
        VStack(alignment: .leading, spacing: 20) {
            currentSourceDetails
            if let workspace = model.workspace {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 24) {
                        contactSelectionSummary.frame(width: 240, alignment: .leading)
                        contactWorkspaceContent(workspace).frame(minWidth: 400)
                    }
                    VStack(alignment: .leading, spacing: 20) {
                        contactSelectionSummary
                        contactWorkspaceContent(workspace)
                    }
                }
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
        .font(.custom("Arial", size: 18))
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
                    .font(.custom("Arial", size: 16))
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
                            .font(.custom("Arial", size: 16).weight(.bold))
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
        .font(.custom("Arial", size: 16))
        .panel()
    }

    private var contactSelectionSummary: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Choose contacts")
                .font(.custom("Arial", size: 28).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text("Main contacts start selected. Select or deselect a whole group or search at once, then uncheck exceptions.")
            VStack(alignment: .leading, spacing: 4) {
                Text("\(model.selectedIDs.count) selected")
                    .font(.custom("Arial", size: 24).weight(.bold))
                    .foregroundStyle(Brand.lime)
                Text("\(model.totalContacts) available across all pages")
                    .font(.custom("Arial", size: 16))
                    .foregroundStyle(Brand.muted)
            }
            Button("Next · \(model.selectedIDs.count) selected") {
                continueAfterBrowserShare = true
                confirmingBrowserShare = true
            }
            .buttonStyle(LimeButton())
            .disabled(model.selectedIDs.isEmpty || !model.canEditWorkspace)
            .opacity(model.selectedIDs.isEmpty || !model.canEditWorkspace ? 0.55 : 1)
            if model.selectedIDs.isEmpty {
                Text("Select at least one contact to continue.")
                    .foregroundStyle(Brand.muted)
            }
            Text(model.accountSaveAwaitingBrowserReceipt
                 ? "Saving is being finalized. Finish in the browser before changing contacts."
                 : model.selectionChangePending
                 ? "Checking that changing contacts won’t interrupt a save… Your reviewed selection is still intact."
                 : !model.handoffID.isEmpty
                 ? "This selection is linked to the account browser. Finish source choices there, or use Change selected contacts in Local review."
                 : "Next: confirm the account and source choices. Continuing does not save by itself.")
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.muted)
            if model.reviewPending {
                Text("Reviewing selected metadata…")
                    .foregroundStyle(Brand.muted)
            }
            if !model.errorMessage.isEmpty && model.phase == .selecting {
                Text(model.errorMessage).foregroundStyle(.red).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func contactWorkspaceContent(_ workspace: ContactWorkspace) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Toggle(isOn: Binding(
                get: { !workspace.knownIDs.isEmpty && workspace.knownIDs.isSubset(of: model.selectedIDs) },
                set: { on in if on { model.selectAllVerifiedContacts() } else { model.clearAllVerifiedContacts() } }
            )) {
                HStack(spacing: 10) {
                    Text("Include all \(workspace.knownIDs.count) contacts")
                        .font(.custom("Arial", size: 18).weight(.bold))
                        .foregroundStyle(Brand.heading)
                    Spacer()
                    Text("\(model.selectedIDs.count) selected")
                        .font(.custom("Arial", size: 16))
                        .foregroundStyle(Brand.muted)
                }
            }
            .toggleStyle(.checkbox)
            .disabled(!model.canEditWorkspace)
            .padding(12)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border, lineWidth: 1))
            .accessibilityLabel("Include all \(workspace.knownIDs.count) contacts, \(model.selectedIDs.count) selected")
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { workspaceFilters(workspace) }
                VStack(alignment: .leading, spacing: 8) { workspaceFilters(workspace) }
            }
            TextField("Search \(workspace.group.rawValue) contacts", text: $search)
                .textFieldStyle(.plain)
                .font(.custom("Arial", size: 18))
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .background(Brand.background, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border, lineWidth: 1))
                .accessibilityLabel("Search contacts in \(workspace.group.rawValue)")
                .onChange(of: search) { _, value in
                    if value.unicodeScalars.count > 120 { search = String(value.unicodeScalars.prefix(120)) }
                    else { model.searchWorkspace(value) }
                }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { scopeSelectionRow(workspace) }
                VStack(alignment: .leading, spacing: 10) { scopeSelectionRow(workspace) }
            }
            .padding(12)
            .background(Brand.background, in: RoundedRectangle(cornerRadius: 8))
            Text("Select all and Deselect all cover every contact in the scope, on every page. Uncheck exceptions below. Nothing is saved until you confirm Save in the browser.")
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.muted)
                .fixedSize(horizontal: false, vertical: true)
            if workspace.filteredCount == 0 {
                Text("No matches here. Your selections are kept. Clear the search or choose another group.")
                    .foregroundStyle(Brand.muted)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(workspace.visible) { contact in
                            contactRow(contact, workspace: workspace)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 300)
                HStack(spacing: 10) {
                    if workspace.hasPreviousPage {
                        Button("Previous page") { model.previousWorkspacePage() }
                            .buttonStyle(SecondaryButton())
                            .accessibilityLabel("Previous page of contacts")
                    }
                    if workspace.hasNextPage {
                        Button("Next page") { model.nextWorkspacePage() }
                            .buttonStyle(SecondaryButton())
                            .accessibilityLabel("Next page of contacts")
                    }
                    Text("\(workspace.pageStart + 1)–\(workspace.pageStart + workspace.visible.count) of \(workspace.filteredCount) matches")
                        .font(.custom("Arial", size: 16))
                        .foregroundStyle(Brand.muted)
                }
            }
            Button(showingGroupTools
                   ? "Hide organizing · \(model.markedGroupMoveIDs.count) marked"
                   : "Organize groups · \(model.markedGroupMoveIDs.count) marked") {
                showingGroupTools.toggle()
            }
            .buttonStyle(SecondaryButton())
            if showingGroupTools {
                Text("Marks are for moving groups, not selecting contacts to save. Moving to Review clears their selection; Undo restores it.")
                    .font(.custom("Arial", size: 16))
                    .foregroundStyle(Brand.muted)
                if workspace.group == .all {
                    Text("Choose Main or Review to mark a group move, or move one contact below.")
                        .font(.custom("Arial", size: 16))
                        .foregroundStyle(Brand.muted)
                } else if workspace.filteredCount > 0 {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) { groupMoveActions(workspace) }
                        VStack(alignment: .leading, spacing: 10) { groupMoveActions(workspace) }
                    }
                }
            }
            if model.canUndoGroupMove {
                Button("Undo last group move") { model.undoContactMove() }
                    .buttonStyle(SecondaryButton())
            }
            DisclosureGroup("About Main and Review") {
                Text("Main starts with a display name and supported phone number. Review holds missing details or possible organization clues for your decision. One name is enough. These are local suggestions; nothing changes on your phone.")
                    .font(.custom("Arial", size: 16))
                    .foregroundStyle(Brand.muted)
                    .padding(.top, 8)
            }
        }
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

    @ViewBuilder private func scopeSelectionRow(_ workspace: ContactWorkspace) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Scope: \(workspace.scopeLabel)")
                .font(.custom("Arial", size: 16).weight(.bold))
                .foregroundStyle(Brand.heading)
                .fixedSize(horizontal: false, vertical: true)
            Text("\(model.selectedScopedCount) of \(workspace.filteredCount) selected")
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.muted)
        }
        .accessibilityElement(children: .combine)
        Spacer(minLength: 8)
        Button("Select all") { model.selectScopedContacts() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canSelectScopedContacts)
            .accessibilityLabel("Select all \(workspace.filteredCount) contacts in this scope")
        Button("Deselect all") { model.clearScopedContacts() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canClearScopedContacts)
            .accessibilityLabel("Deselect all \(workspace.filteredCount) contacts in this scope")
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
                VStack(alignment: .leading, spacing: 4) {
                    Text(contact.name.isEmpty ? "Unnamed contact" : contact.name)
                        .font(.custom("Arial", size: 18).weight(.bold))
                        .foregroundStyle(Brand.heading)
                    Text(contact.phoneEnds.isEmpty
                         ? "No supported phone number"
                         : contact.phoneEnds.map { "•••• \($0)" }.joined(separator: " · "))
                        .font(.custom("Arial", size: 18))
                        .foregroundStyle(Brand.muted)
                }
            }
            .toggleStyle(.checkbox)
            .accessibilityLabel("Include \(contact.name.isEmpty ? "unnamed contact" : contact.name) in selected contacts")
            .disabled(!model.canEditWorkspace)
            Text(workspace.isInReview(contact.id) ? "Review" : "Main")
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.muted)
            if let reason = workspace.reason(for: contact.id) {
                Text(reason)
                    .font(.custom("Arial", size: 16))
                    .foregroundStyle(Brand.body)
            }
            DisclosureGroup("Source details") {
                VStack(alignment: .leading, spacing: 6) {
                    Text(contact.nameFieldDescription)
                    if contact.organizationField == true {
                        Text("Organization field supplied. This is a hint, not verified business identity.")
                    }
                }
                .font(.custom("Arial", size: 16))
                .foregroundStyle(Brand.muted)
                .padding(.top, 6)
            }
            .font(.custom("Arial", size: 16))
            if showingGroupTools {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) { contactGroupActions(contact, workspace: workspace) }
                    VStack(alignment: .leading, spacing: 10) { contactGroupActions(contact, workspace: workspace) }
                }
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
            currentSourceDetails
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
                Link("Open AMPLIFai account in browser", destination: URL(string: "https://amplifai.satoris.ai/phone/account")!)
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
