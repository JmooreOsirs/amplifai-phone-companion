import AppKit
import SwiftUI

@main
struct AmplifaiPhoneApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    // The fixed AMPLIFai run authority; links are applied only after the start-up temporary-data inspection.
    @StateObject private var model = CollectorModel(runAuthority: PhoneRunAuthorityClient.production(),
                                                     awaitsStartupInspection: true)
    @StateObject private var updater = CompanionUpdater()
    @StateObject private var statusService = CompanionStatusService()

    var body: some Scene {
        WindowGroup("AMPLIFai Phone", id: "collector") {
            CollectorWindowRoot(model: model, updater: updater, delegate: delegate)
                .frame(minWidth: 375, minHeight: 600)
                .onAppear {
                    delegate.statusService = statusService
                    statusService.startIfNeeded(model: model, updater: updater)
                    model.inspectResidue()
                    updater.startIfNeeded(model: model)
                }
                .onDisappear { model.disconnect() }
        }
        .defaultSize(width: CompanionOpeningGeometry.preferred.width, height: CompanionOpeningGeometry.preferred.height)
    }
}

private struct CollectorWindowRoot: View {
    @ObservedObject var model: CollectorModel
    @ObservedObject var updater: CompanionUpdater
    let delegate: AppDelegate
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        ContentView(model: model, updater: updater)
            .onAppear {
                let open = openWindow
                delegate.reopenCollectorWindow = { open(id: "collector") }
                delegate.attach(model: model, updater: updater)
            }
    }
}
