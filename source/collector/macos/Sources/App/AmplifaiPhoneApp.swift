import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct AmplifaiPhoneApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = CollectorModel()

    var body: some Scene {
        WindowGroup("AMPLIFai Phone", id: "collector") {
            ContentView(model: model)
                .frame(minWidth: 375, minHeight: 600)
                .onAppear { model.inspectResidue() }
                .onDisappear { model.disconnect() }
        }
        .defaultSize(width: 820, height: 680)
    }
}
