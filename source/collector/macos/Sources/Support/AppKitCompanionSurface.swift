import AppKit

/// The AppKit side of `CompanionWindowSurface`, and the only file that may call AppKit window-ordering or app
/// activation APIs (a flow check enforces it). The director never calls the restore/activate/raise methods.
@MainActor final class AppKitCompanionSurface: CompanionWindowSurface {
    private weak var window: NSWindow?
    private let originalLevel: NSWindow.Level
    private let originalHides: Bool
    var returnToBrowser: ReturnToBrowserAction

    init(window: NSWindow, returnToBrowser: ReturnToBrowserAction) {
        self.window = window
        originalLevel = window.level
        originalHides = window.hidesOnDeactivate
        self.returnToBrowser = returnToBrowser
    }

    var isNativeAppFrontmost: Bool {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier == Bundle.main.bundleIdentifier
    }
    var hasAttachedSheet: Bool { window?.attachedSheet != nil || NSApp.modalWindow != nil }

    func setFloating(_ floating: Bool) {
        let level: NSWindow.Level = floating ? .floating : originalLevel
        if window?.level != level { window?.level = level }
    }
    func setKeepsVisibleWhenInactive(_ keep: Bool) {
        let hides = keep ? false : originalHides
        if window?.hidesOnDeactivate != hides { window?.hidesOnDeactivate = hides }
    }
    func miniaturize() { window?.miniaturize(nil) }
    func deminiaturize() { window?.deminiaturize(nil) }
    func activateApp() { NSApp.activate() }
    func makeKeyAndOrderFront() { window?.makeKeyAndOrderFront(nil) }
    func orderFront() { window?.orderFront(nil) }

    func presentCloseConfirmation(title: String, detail: String, completion: @escaping @MainActor (Bool) -> Void) {
        guard let window else { completion(false); return }
        window.level = originalLevel
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "Keep window open")
        alert.addButton(withTitle: "Cancel and close")
        alert.beginSheetModal(for: window) { response in
            MainActor.assumeIsolated { completion(response == .alertSecondButtonReturn) }
        }
    }

    func openInBrowser(_ url: URL, preferredBrowserBundleID: String?, completion: @escaping @MainActor (Bool) -> Void) {
        returnToBrowser(url, preferredBrowserBundleID: preferredBrowserBundleID, completion: completion)
    }

    func restore() {
        guard let window else { return }
        window.level = originalLevel
        window.hidesOnDeactivate = originalHides
    }
}
