import SwiftUI

struct BrowserReturnControls: View {
    @ObservedObject var model: CollectorModel
    @Environment(\.returnToBrowser) private var returnToBrowser
    @State private var message = ""

    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Button("Return to your account tab") {
                message = returnToBrowser.activateOriginalBrowser(preferredBrowserBundleID: model.returnBrowserBundleID)
                    ? "Choose the AMPLIFai tab you left open in that browser."
                    : "Switch to the browser and AMPLIFai tab you left open. If that tab is closed, reopen your account below."
            }
            .buttonStyle(LimeButton())
            Text(message.isEmpty ? "Continue in the AMPLIFai tab you left open. This button brings your browser forward; choose the tab if needed." : message)
                .font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                .fixedSize(horizontal: false, vertical: true)
            DisclosureGroup("Account tab closed or missing?") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Reopen your account only if the original tab is unavailable. Your browser may open a new tab or ask you to sign in.")
                        .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
                    Button("Reopen account") {
                        if model.phase == .completed {
                            model.continueToSignedInAccount { url, browser in openRecovery(url, browser: browser) }
                        } else if model.browserOwnsFlow || model.nativeSetupState != .none {
                            openRecovery(model.accountContinueURL, browser: model.returnBrowserBundleID)
                        }
                    }
                    .buttonStyle(SecondaryButton())
                }
                .padding(.top, 8)
            }
            .font(Brand.bodyFont(14))
        }
    }

    private func openRecovery(_ url: URL, browser: String?) {
        returnToBrowser(url, preferredBrowserBundleID: browser) { opened in
            if !opened { message = "The account could not open. Open AMPLIFai in your browser and sign in to continue." }
        }
    }
}
