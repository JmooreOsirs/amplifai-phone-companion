import SwiftUI

struct NativeSetupView: View {
    @ObservedObject var model: CollectorModel
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Approve this phone collection")
                .font(Brand.headingFont(28)).foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            if let setup = model.nativeSetup {
                VerifiedStatusRow(verified: true, title: "Account: \(setup.email)", detail: "Course: \(setup.course)")
                Text("Available to save after you choose: \(setup.categories.joined(separator: ", ")).")
                    .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
                ForEach(setup.disclosure.sentences, id: \.self) { sentence in
                    Text(sentence).font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let privacy = setup.disclosure.privacyURL {
                    Button("Privacy and collection details") { openURL(privacy) }
                        .buttonStyle(QuietAction())
                }
            }
            stateContent
        }
    }

    @ViewBuilder private var stateContent: some View {
        switch model.nativeSetupState {
        case .claiming:
            progress("Checking the account and course from your browser…")
            Text("Nothing has been approved or collected.").font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
            notNow
        case .pendingApproval:
            if let setup = model.nativeSetup {
                TimelineView(.periodic(from: .now, by: 5)) { _ in
                    if model.now() < setup.expiresAt {
                        VStack(alignment: .leading, spacing: 18) {
                            Toggle(setup.disclosure.agreement, isOn: $model.nativeDisclosureAccepted)
                                .toggleStyle(.checkbox)
                                .font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
                                .fixedSize(horizontal: false, vertical: true)
                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: 12) { approvalActions }
                                VStack(alignment: .leading, spacing: 12) { approvalActions }
                            }
                            Text("After approval, you’ll connect and check your iPhone before starting the transfer.")
                                .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
                        }
                    } else {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("This setup expired. Return to your account tab and start a new phone setup.")
                                .font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
                            notNow
                            BrowserReturnControls(model: model)
                        }
                    }
                }
            }
        case .approving:
            progress("Confirming your approval with AMPLIFai…")
            Text("Collection stays off until the approval is confirmed. You can cancel this setup in your original account tab.")
                .font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
            BrowserReturnControls(model: model)
        case .claimUncertain, .approvalUncertain:
            Text(model.nativeSetupMessage).font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
            if model.nativeSetupState == .claimUncertain {
                Button("Try again") { model.retrySetupClaim() }.buttonStyle(LimeButton())
                notNow
            } else {
                Button("Retry approval") { model.retryNativeApproval() }.buttonStyle(LimeButton())
            }
            BrowserReturnControls(model: model)
        case .refused:
            Text(model.nativeSetupMessage).font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
            BrowserReturnControls(model: model)
        case .none: EmptyView()
        }
    }

    @ViewBuilder private var approvalActions: some View {
        Button("Approve collection") { model.approveNativeSetup() }
            .buttonStyle(LimeButton()).disabled(!model.canApproveNativeSetup)
        notNow
    }

    private var notNow: some View {
        Button("Not now") { model.dismissNativeSetup() }.buttonStyle(SecondaryButton())
    }

    private func progress(_ message: String) -> some View {
        HStack(spacing: 12) {
            ProgressView().controlSize(.small)
            Text(message).font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
        }
    }
}
