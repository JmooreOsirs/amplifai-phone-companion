import AppKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: CollectorModel?
    weak var statusService: CompanionStatusService?
    weak var updater: CompanionUpdater?
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

    func attach(model: CollectorModel, updater: CompanionUpdater) {
        self.model = model
        self.updater = updater
        runLinks.beforeDelivery = { [weak model, weak updater] in
            guard let model else { return }
            updater?.applicationOpened(model: model)
        }
        runLinks.attach(model)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if let model { updater?.applicationOpened(model: model) }
        if !flag, let reopenCollectorWindow {
            reopenCollectorWindow()
            return false // The existing SwiftUI collector window owns reopening.
        }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        model?.canTerminateForUpdate == false ? .terminateCancel : .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) { statusService?.stop() }
}

