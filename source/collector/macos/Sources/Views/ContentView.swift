import SwiftUI

struct ContentView: View {
    @ObservedObject var model: CollectorModel
    @State private var search = ""
    @State private var confirmingCleanup = false
    @State private var confirmingBrowserShare = false
    @State private var localCollectionChecked = false
    @State private var confirmingRetainedReviewDiscard = false

    private var visibleContacts: [ContactPreview] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return model.contacts }
        return model.contacts.filter { $0.name.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                if !model.residues.isEmpty { residueNotice }
                if !model.helperRunning && (model.phase == .idle || model.phase == .error || model.phase == .completed) {
                    if model.hasUnsavedReview { retainedReviewRecovery }
                    else { collectionApprovalPanel }
                }
                statusPanel
                if model.phase == .passwordRequired { passwordPanel }
                if model.phase == .selecting || model.phase == .reviewing { contactPanel }
                if model.phase == .reviewing { reviewPanel }
                platformNote
                privacyNote
            }
            .frame(maxWidth: 880)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .background(Brand.background)
        .foregroundStyle(Brand.body)
        .background(ActiveCompanionWindow(model: model).frame(width: 0, height: 0))
        .onChange(of: model.collectionApprovalRunID) { _, _ in localCollectionChecked = false }
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
            Button("Create one-time browser pairing") { model.pairBrowser() }
        } message: {
            Text("This opens a five-minute local handoff for selected contacts, phone numbers, and matching call/message metadata. No message bodies, backup files, or passwords are handed off. Saving to your account requires separate approval there. This companion keeps the local review until the matching account save is explicitly acknowledged.")
        }
        .confirmationDialog("Discard the retained local unsaved review?", isPresented: $confirmingRetainedReviewDiscard) {
            Button("Discard review and disconnect", role: .destructive) { model.disconnect() }
            Button("Keep review", role: .cancel) {}
        } message: {
            Text("Receiving a browser preview is not a saved account receipt. This discards the companion's in-memory recovery copy; separately saved account data and marked temporary backups are not deleted.")
        }
    }

    private var header: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 20) {
                logoTile
                headerCopy
                Spacer(minLength: 0)
            }
            VStack(alignment: .leading, spacing: 16) {
                logoTile
                headerCopy
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
                    .frame(width: 228, height: 64)
                    .padding(8)
                    .background(.white, in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityLabel("AMPLIFai By Nexus")
            }
        }
    }

    private var headerCopy: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Phone companion")
                .font(.custom("Arial", size: 28).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text("Private iPhone collection on this Mac")
                .font(.custom("Arial", size: 14))
                .foregroundStyle(Brand.muted)
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
        VStack(alignment: .leading, spacing: 12) {
            Text("Before collecting locally")
                .font(.custom("Arial", size: 18).weight(.bold))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            Text("Requirements: an Apple M-series Mac running macOS 14 or later, a data-capable USB cable, an iPhone unlocked for initial connection and Trust, and real free Mac storage for selected databases, parsing copies and a further 2 GiB reserve. Internet is needed later for failure reporting, browser handoff and account save; the USB backup itself runs locally.")
            Text("This Mac reads available contacts and retained call/message context. After collection, choose the people whose metadata you review. Context includes phone numbers, dates, participants, call duration, and message transport/direction—not message text in the review or browser handoff.")
            Text("iPhone capture temporarily receives full-backup bytes, including unrelated data and message content. Unselected files are discarded while streaming; selected source databases remain private until parsing finishes. Storage must fit those databases, parsing copies and a further 2 GiB reserve. Cleanup is attempted; an interruption can leave marked temporary phone data that you must inspect here.")
            Text("Agreeing permits this local collection only. It does not approve Apple's Trust prompt, provide an encrypted-backup password, permit browser sharing, or save anything to an account. Browser transfer has a separate confirmation; each optional source save needs its own approval in your account.")
            Text("If collection fails, this app automatically sends only a safe diagnostic code to AMPLIFai/PostHog: build, random reference, failure category and stage, elapsed time, byte counts and optional numeric device status. It never sends phone records, names, message content, passwords, backup files, paths or raw errors. Delivery status and a retry appear after a failure; a failed report never blocks a new collection.")
            Toggle("I agree to this collection and its safe failure diagnostic", isOn: Binding(
                get: { localCollectionChecked },
                set: { checked in
                    localCollectionChecked = checked
                    if !checked { model.declineLocalCollection() }
                }
            ))
            .toggleStyle(.checkbox)
            .disabled(!model.canPrepareCollection)
            if model.localCollectionApproval != nil {
                Text("Approved for the next collection only. Choose Connect iPhone when ready.")
                    .foregroundStyle(Brand.muted)
                Button("Back") {
                    localCollectionChecked = false
                    model.declineLocalCollection()
                }
                .buttonStyle(SecondaryButton())
                .keyboardShortcut(.cancelAction)
            } else {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { collectionApprovalActions }
                    VStack(alignment: .leading, spacing: 12) { collectionApprovalActions }
                }
            }
            Text("Disclosure: \(CollectorModel.collectionDisclosureVersion)")
                .font(.custom("Arial", size: 12))
                .foregroundStyle(Brand.muted)
                .textSelection(.enabled)
        }
        .font(.custom("Arial", size: 14))
        .panel()
    }

    @ViewBuilder private var collectionApprovalActions: some View {
        Button("Agree to local collection") {
            model.approveLocalCollection(for: model.collectionApprovalRunID, checked: localCollectionChecked)
        }
        .buttonStyle(LimeButton())
        .disabled(!localCollectionChecked || !model.canPrepareCollection)
        Button("Decline") {
            localCollectionChecked = false
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
                .font(.custom("Arial", size: 18).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text("Use the password already set for this iPhone's encrypted computer backup, not its screen-unlock passcode. Ask its owner if you do not know it. This app never changes or resets encryption; the password stays in the local helper and is not saved.")
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
            HStack {
                Text("Choose contacts")
                    .font(.custom("Arial", size: 18).weight(.bold))
                    .foregroundStyle(Brand.heading)
                Spacer()
                Text("\(model.selectedIDs.count) selected")
                    .foregroundStyle(Brand.muted)
            }
            Text("Choose the people to review. Only matching call and message metadata from retained available history is included.")
            TextField("Search contacts", text: $search)
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel("Search contacts")
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(visibleContacts) { contact in
                        Toggle(isOn: Binding(
                            get: { model.selectedIDs.contains(contact.id) },
                            set: { _ in model.toggle(contact.id) }
                        )) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(contact.name.isEmpty ? "Unnamed contact" : contact.name)
                                    .foregroundStyle(Brand.heading)
                                Text(contact.phoneEnds.isEmpty
                                     ? "No supported phone number"
                                     : contact.phoneEnds.map { "•••• \($0)" }.joined(separator: " · "))
                                    .font(.caption)
                                    .foregroundStyle(Brand.muted)
                            }
                        }
                        .toggleStyle(.checkbox)
                        .padding(.vertical, 3)
                    }
                }
            }
            .frame(maxHeight: 220)
            if model.contacts.isEmpty {
                Text("No usable contacts were present in the selected backup.")
                    .foregroundStyle(Brand.muted)
            } else if visibleContacts.isEmpty {
                Text("No contacts match this search.")
                    .foregroundStyle(Brand.muted)
            }
            if !model.errorMessage.isEmpty && model.phase == .selecting {
                Text(model.errorMessage).foregroundStyle(.red)
            }
            Button("Review selected metadata") { model.review() }
                .buttonStyle(LimeButton())
                .disabled(model.selectedIDs.isEmpty)
        }
        .panel()
    }

    private var reviewPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Local review")
                .font(.custom("Arial", size: 18).weight(.bold))
                .foregroundStyle(Brand.heading)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 16)], alignment: .leading, spacing: 16) {
                metric(model.selectedIDs.count, "Selected contacts")
                metric(model.matchedCalls, "Matching calls")
                metric(model.matchedMessages, "Matching messages")
            }
            Text("The collector attempted all retained available history. The observed dates below do not prove complete phone history.")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            Text("Observed calls: \(dateRange(model.observedCallEarliest, model.observedCallLatest)).")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            Text("Observed messages: \(dateRange(model.observedMessageEarliest, model.observedMessageLatest)).")
                .font(.caption)
                .foregroundStyle(Brand.muted)
            if !model.missingSources.isEmpty {
                Text("Unavailable backup sources: \(model.missingSources.joined(separator: ", ")). Counts are partial.")
                    .foregroundStyle(Brand.muted)
            }
            Text("A local preview is not an account save. Keep this recovery copy until the browser confirms the matching save. Disconnect or closing after confirmation discards this in-memory review.")
                .foregroundStyle(Brand.muted)
            Button("Create local browser pairing") { confirmingBrowserShare = true }
                .buttonStyle(SecondaryButton())
                .disabled(model.selectionNeedsReview || model.handoffAcknowledged)
            if !model.errorMessage.isEmpty {
                Text(model.errorMessage).foregroundStyle(.red)
            }
            if !model.pairCode.isEmpty {
                Text("One-time code: \(model.pairCode.prefix(5))-\(model.pairCode.suffix(5))")
                    .font(.custom("Arial", size: 18).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .textSelection(.enabled)
                Text("Expires five minutes after creation. Only the exact AMPLIFai account origin can receive it. Receiving the preview does not approve a cloud save.")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
                Link("Open AMPLIFai account in browser", destination: URL(string: "https://amplifai-database-engine.vercel.app/phone/account")!)
                    .buttonStyle(SecondaryButton())
            }
        }
        .panel()
    }

    private func metric(_ value: Int, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value.formatted())
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

    private var platformNote: some View {
        HStack(spacing: 8) {
            Circle().fill(Brand.blue).frame(width: 7, height: 7)
            Text("This Mac companion reads iPhone backups. Android uses its separate signed Android app from account setup.")
                .font(.custom("Arial", size: 13))
                .foregroundStyle(Brand.muted)
        }
    }

    private var privacyNote: some View {
        Text("Mac companion · Selected relationship context, not message content · Unselected full-backup bytes are streamed and discarded · Private sources and parsing copies need disk space plus a 2 GiB reserve · Physical coverage varies; Windows is unsupported")
            .font(.custom("Arial", size: 12))
            .foregroundStyle(Brand.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
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
