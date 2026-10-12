import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: CollectorModel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        if let resource = Bundle.main.resourceURL?.appendingPathComponent("AmplifaiPhone.icns"),
           let icon = NSImage(contentsOf: resource) {
            NSApp.applicationIconImage = icon
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        model?.canTerminateForUpdate == false ? .terminateCancel : .terminateNow
    }
}

@main
struct AmplifaiPhoneApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = CollectorModel()
    @StateObject private var updater = CompanionUpdater()

    var body: some Scene {
        WindowGroup("AMPLIFai Phone", id: "collector") {
            ContentView(model: model, updater: updater)
                .frame(minWidth: 375, minHeight: 600)
                .onAppear {
                    delegate.model = model
                    model.inspectResidue()
                    updater.startIfNeeded(model: model)
                }
                .onDisappear { model.disconnect() }
        }
        .defaultSize(width: 820, height: 680)
    }
}
