import AppKit

/// The AppKit side of `CompanionWindowSurface`, and the only file that may call AppKit window-ordering or app
/// activation APIs (a flow check enforces it). The director never calls the restore/activate/raise methods.
@MainActor final class AppKitCompanionSurface: CompanionWindowSurface {
    private weak var window: NSWindow?
    private weak var closeConfirmationSheet: NSWindow?
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
    func activateApp() { if window != nil { NSApp.activate() } }
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
        let sheet = alert.window
        closeConfirmationSheet = sheet
        alert.beginSheetModal(for: window) { [weak self, weak sheet] response in
            MainActor.assumeIsolated {
                guard let self, let sheet, self.closeConfirmationSheet === sheet else { return }
                self.closeConfirmationSheet = nil
                guard self.window != nil else { return }
                completion(response == .alertSecondButtonReturn)
            }
        }
    }

    func openInBrowser(_ url: URL, preferredBrowserBundleID: String?, completion: @escaping @MainActor (Bool) -> Void) {
        guard window != nil else { return }
        returnToBrowser(url, preferredBrowserBundleID: preferredBrowserBundleID) { [weak self] opened in
            guard self?.window != nil else { return }
            completion(opened)
        }
    }

    func invalidate() {
        guard let window else { return }
        self.window = nil
        let sheet = closeConfirmationSheet
        closeConfirmationSheet = nil
        if let sheet, sheet.sheetParent === window { window.endSheet(sheet, returnCode: .abort) }
        window.level = originalLevel
        window.hidesOnDeactivate = originalHides
    }
}
