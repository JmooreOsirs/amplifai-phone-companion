import SwiftUI

struct BrowserReturnControls: View {
    @ObservedObject var model: CollectorModel
    @Environment(\.returnToBrowser) private var returnToBrowser
    @EnvironmentObject private var session: BrowserReturnSession

    private var context: BrowserReturnSession.Context {
        .init(runOrSetupID: model.phoneRun?.runID ?? model.nativeSetup?.setupID,
              browserBundleID: model.returnBrowserBundleID,
              recoveryURL: model.phase == .completed ? model.completedReportReturnURL : model.accountContinueURL)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Button("Return to your account tab") {
                session.returnToAccount(using: returnToBrowser, context: context)
            }
            .buttonStyle(LimeButton())
            .disabled(session.busy)
            Text(session.message.isEmpty ? "Continue in your existing AMPLIFai account tab. If your browser does not come forward, switch to it manually." : session.message)
                .font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                .fixedSize(horizontal: false, vertical: true)
            if context.recoveryURL != nil {
                Button(session.recoverySent ? "Account link sent" : "Reopen account") {
                    session.recover(using: returnToBrowser, context: context)
                }
                .buttonStyle(SecondaryButton())
                .disabled(session.busy || session.recoverySent)
                Text("Use only if the original tab is closed or missing. Your browser may open a new tab or ask you to sign in.")
                    .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear { session.use(context) }
        .onChange(of: context) { _, value in session.use(value) }
        .onDisappear { session.invalidate() }
    }
}
