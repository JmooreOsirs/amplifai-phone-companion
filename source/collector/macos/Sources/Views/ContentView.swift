import SwiftUI

struct ContentView: View {
    @ObservedObject var model: CollectorModel
    @State private var search = ""
    @State private var confirmingCleanup = false
    @State private var confirmingBrowserShare = false

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
            "Share selected relationship metadata with the AMPLIFai pilot in this browser?",
            isPresented: $confirmingBrowserShare
        ) {
            Button("Create one-time browser pairing") { model.pairBrowser() }
        } message: {
            Text("This opens a five-minute, loopback-only handoff for the selected contacts, phone numbers, and matching call/message metadata. No message bodies, backup files, or passwords are handed off. The public browser receiver exists, but phone-to-browser pairing has not been validated yet.")
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
            Text("Local iPhone collector candidate")
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
        VStack(alignment: .leading, spacing: 16) {
            Text(statusTitle)
                .font(.custom("Arial", size: 21).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text(statusDetail)
                .font(.custom("Arial", size: 15))
            if model.phase == .transferring {
                ProgressView(value: model.progress, total: 100) {
                    Text("Local backup transfer\(model.connectionTransport.isEmpty ? "" : " over \(model.connectionTransport)")")
                }
                .tint(Brand.lime)
            }
            if model.phase == .error {
                Text(model.errorMessage)
                    .foregroundStyle(.red)
                    .accessibilityAddTraits(.isStaticText)
            }
            HStack(spacing: 12) {
                if model.phase == .idle || model.phase == .error {
                    Button("Connect iPhone") { model.connect() }
                        .buttonStyle(LimeButton())
                        .disabled(!model.canConnect)
                } else if model.phase == .connecting || model.phase == .transferring
                            || model.phase == .passwordRequired || model.phase == .processing {
                    Button("Cancel collection") { model.disconnect() }
                        .buttonStyle(SecondaryButton())
                } else if model.phase == .selecting || model.phase == .reviewing {
                    Button("Disconnect") { model.disconnect() }
                        .buttonStyle(SecondaryButton())
                }
            }
        }
        .panel()
    }

    private var passwordPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Existing encrypted-backup password")
                .font(.custom("Arial", size: 18).weight(.bold))
                .foregroundStyle(Brand.heading)
            Text("This is the password already set for this iPhone backup. It is sent only to the local collector process and is not saved.")
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
            Text("No metadata has been saved or uploaded. Disconnect clears this in-memory review.")
                .foregroundStyle(Brand.muted)
            Button("Create local browser pairing") { confirmingBrowserShare = true }
                .buttonStyle(SecondaryButton())
            if !model.errorMessage.isEmpty {
                Text(model.errorMessage).foregroundStyle(.red)
            }
            if !model.pairCode.isEmpty {
                Text("One-time code: \(model.pairCode.prefix(5))-\(model.pairCode.suffix(5))")
                    .font(.custom("Arial", size: 18).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .textSelection(.enabled)
                Text("Expires five minutes after creation at 127.0.0.1:\(model.pairPort), only from the exact AMPLIFai pilot origin. Public phone-to-browser pairing has not been validated yet.")
                    .font(.caption)
                    .foregroundStyle(Brand.muted)
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
            Text("Android collection is not available in this Mac candidate. No Android phone action is enabled.")
                .font(.custom("Arial", size: 13))
                .foregroundStyle(Brand.muted)
        }
    }

    private var privacyNote: some View {
        Text("Developer-only local candidate · No account, cloud upload, or persistent relationship database · Actual phone and Windows validation pending")
            .font(.custom("Arial", size: 12))
            .foregroundStyle(Brand.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var statusTitle: String {
        switch model.phase {
        case .idle: "Connect your iPhone"
        case .connecting: "Waiting for your iPhone"
        case .transferring: "Collecting locally"
        case .passwordRequired: "Backup password needed"
        case .processing: "Reading metadata locally"
        case .selecting: "Choose the people to review"
        case .reviewing: "Review is ready"
        case .cancelling: "Cancelling and cleaning up"
        case .error: "Collection stopped"
        }
    }

    private var statusDetail: String {
        switch model.phase {
        case .idle, .error:
            "For first use, connect one iPhone by USB, unlock it, and approve Apple's Trust prompt if shown. When no cable is present, an existing trusted Wi-Fi pairing may be used. If USB Trust fails, collection stops instead of switching transports."
        case .connecting:
            "Looking for one iPhone over USB first, or an existing trusted Wi-Fi pairing when no cable is present. Keep the phone unlocked and approve its Trust prompt if using USB. No phone data is sent to a server."
        case .transferring:
            "The phone may transfer more backup data than the helper retains. You can cancel."
        case .passwordRequired:
            "The phone's backup is encrypted. Enter its existing backup password to continue."
        case .processing:
            "The helper is reading allowlisted metadata and removing its temporary backup."
        case .selecting:
            "\(model.contacts.count) contacts available; \(model.availableCalls) calls and \(model.availableMessages) messages in the observed window."
        case .reviewing:
            "You can change the selection and review again, or disconnect."
        case .cancelling:
            "The helper is stopping. If the process crashes, leftover app-created data will be shown next launch."
        }
    }
}

private struct PanelStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
            .background(Brand.panel, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Brand.border, lineWidth: 1))
    }
}

private extension View {
    func panel() -> some View { modifier(PanelStyle()) }
}

private struct LimeButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.custom("Arial", size: 14).weight(.bold))
            .foregroundStyle(Brand.background)
            .padding(.horizontal, 18)
            .frame(minHeight: 40)
            .background(Brand.lime.opacity(configuration.isPressed ? 0.8 : 1), in: RoundedRectangle(cornerRadius: 8))
    }
}

private struct SecondaryButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.custom("Arial", size: 14).weight(.bold))
            .foregroundStyle(Brand.heading)
            .padding(.horizontal, 18)
            .frame(minHeight: 40)
            .background(Brand.border.opacity(configuration.isPressed ? 0.8 : 1), in: RoundedRectangle(cornerRadius: 8))
    }
}
