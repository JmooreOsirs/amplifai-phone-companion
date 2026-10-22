import AppKit

/// The AppKit side of `CompanionWindowSurface`, and the only file that may call AppKit window-ordering or app
/// activation APIs (a flow check enforces it). The director never calls the restore/activate/raise methods.
@MainActor final class AppKitCompanionSurface: CompanionWindowSurface {
    private static let sizedWindows = NSHashTable<NSWindow>.weakObjects()
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
        if !Self.sizedWindows.contains(window), let screen = window.screen,
           window.styleMask.contains(.resizable), window.frame.intersects(screen.visibleFrame) {
            Self.sizedWindows.add(window)
            let frame = CompanionOpeningGeometry.frame(current: window.frame, visible: screen.visibleFrame,
                                                       styleMask: window.styleMask)
            if frame != window.frame { window.setFrame(frame, display: false) }
        }
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

    static func activateOriginalBrowser(bundleID: String?,
                                        completion: @escaping @MainActor (BrowserActivationResult) -> Void) {
        guard let bundleID, let probe = URL(string: CollectorModel.accountOrigin),
              let browser = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first,
              !browser.isTerminated,
              ReturnBrowserChoice.accept(senderBundleID: browser.bundleIdentifier, senderBundleURL: browser.bundleURL,
                                         ownBundleID: Bundle.main.bundleIdentifier,
                                         httpsHandlers: NSWorkspace.shared.urlsForApplications(toOpen: probe)) == bundleID
        else { completion(.unavailable); return }
        let isFrontmost = { !browser.isTerminated && NSWorkspace.shared.frontmostApplication?.processIdentifier == browser.processIdentifier }
        if isFrontmost() { completion(.observed); return }
        NSApp.yieldActivation(to: browser)
        let requested = browser.activate(from: .current, options: [])
        observeBrowserActivation(requested: requested, isFrontmost: isFrontmost, completion: completion)
    }

    /// Observe the one explicit request for at most a second; never repeatedly activate an app.
    static func observeBrowserActivation(requested: Bool, remaining: Int = 10,
                                         isFrontmost: @escaping @MainActor () -> Bool,
                                         schedule: @escaping (@escaping @MainActor () -> Void) -> Void = { next in
                                             DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { MainActor.assumeIsolated { next() } }
                                         },
                                         completion: @escaping @MainActor (BrowserActivationResult) -> Void) {
        if isFrontmost() { completion(.observed); return }
        guard requested, remaining > 0 else { completion(.unconfirmed); return }
        schedule {
            observeBrowserActivation(requested: requested, remaining: remaining - 1,
                                     isFrontmost: isFrontmost, schedule: schedule, completion: completion)
        }
    }

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

enum CompanionOpeningGeometry {
    static let preferred = NSSize(width: 1180, height: 820)

    /// Enlarge an undersized restored window once, fit the display, and keep its top-left anchor when possible.
    static func frame(current: NSRect, visible: NSRect, styleMask: NSWindow.StyleMask) -> NSRect {
        let preferredFrame = NSWindow.frameRect(forContentRect: NSRect(origin: .zero, size: preferred), styleMask: styleMask)
        let width = min(visible.width, max(current.width, preferredFrame.width))
        let height = min(visible.height, max(current.height, preferredFrame.height))
        return NSRect(x: min(max(current.minX, visible.minX), visible.maxX - width),
                      y: min(max(current.maxY - height, visible.minY), visible.maxY - height),
                      width: width, height: height)
    }
}
