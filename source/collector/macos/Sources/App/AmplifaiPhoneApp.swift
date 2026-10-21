import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: CollectorModel?
    weak var statusService: CompanionStatusService?
    /// Holds at most one run link until the model is attached and its start-up checks finish.
    let runLinks = PhoneRunLinkInbox()
    /// Reopens the collector window when a link arrives after the owner closed it (the app keeps running).
    var reopenCollectorWindow: (() -> Void)?

    /// Registered before launch finishes, so a link that cold-starts the app is captured, and so SwiftUI never
    /// receives the URL (it would open a second window).
    func applicationWillFinishLaunching(_ notification: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleGetURL(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        if let resource = Bundle.main.resourceURL?.appendingPathComponent("AmplifaiPhone.icns"),
           let icon = NSImage(contentsOf: resource) {
            NSApp.applicationIconImage = icon
        }
    }

    @objc func handleGetURL(_ event: NSAppleEventDescriptor, withReplyEvent reply: NSAppleEventDescriptor) {
        guard let text = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue,
              text.utf8.count <= 200, let url = URL(string: text) else { return }
        // The sending app is kept as the return browser only if it is another app that opens https URLs.
        let browser = ReturnBrowser.bundleIdentifier(forSenderOf: event)
        guard runLinks.receive(url, returnBrowserBundleID: browser) else { return }
        let visible = NSApp.windows.contains { $0.canBecomeMain && ($0.isVisible || $0.isMiniaturized) }
        if !visible { reopenCollectorWindow?() }
    }

    func attach(model: CollectorModel) {
        self.model = model
        runLinks.attach(model)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        model?.canTerminateForUpdate == false ? .terminateCancel : .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) { statusService?.stop() }
}

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
                delegate.attach(model: model)
            }
    }
}
