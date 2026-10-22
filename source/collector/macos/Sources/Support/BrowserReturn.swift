import AppKit
import SwiftUI

enum BrowserActivationResult: Equatable { case observed, unconfirmed, unavailable }

/// Returning activates the originating browser without navigating. Opening a URL is a separate recovery
/// operation: macOS cannot select the original tab, window or profile.
struct ReturnToBrowserAction {
    let perform: @MainActor (URL, String?, @escaping @MainActor (Bool) -> Void) -> Void
    private let activateExisting: @MainActor (String?, @escaping @MainActor (BrowserActivationResult) -> Void) -> Void

    init(activateExisting: @escaping @MainActor (String?, @escaping @MainActor (BrowserActivationResult) -> Void) -> Void = { _, done in done(.unavailable) },
         perform: @escaping @MainActor (URL, String?, @escaping @MainActor (Bool) -> Void) -> Void) {
        self.activateExisting = activateExisting
        self.perform = perform
    }

    @MainActor func activateOriginalBrowser(preferredBrowserBundleID: String?,
                                             completion: @escaping @MainActor (BrowserActivationResult) -> Void) {
        activateExisting(preferredBrowserBundleID, completion)
    }

    @MainActor func callAsFunction(_ url: URL, preferredBrowserBundleID: String?,
                                   completion: @escaping @MainActor (Bool) -> Void = { _ in }) {
        perform(url, preferredBrowserBundleID, completion)
    }

    static let system = ReturnToBrowserAction(activateExisting: {
        AppKitCompanionSurface.activateOriginalBrowser(bundleID: $0, completion: $1)
    }) { url, preferred, completion in
        guard url.scheme == "https", url.host == "amplifai.satoris.ai" else { completion(false); return }
        let workspace = NSWorkspace.shared
        var browser: URL?
        if let preferred, let candidate = workspace.urlForApplication(withBundleIdentifier: preferred),
           workspace.urlsForApplications(toOpen: url).contains(where: {
               $0.standardizedFileURL.resolvingSymlinksInPath() == candidate.standardizedFileURL.resolvingSymlinksInPath()
           }) {
            browser = candidate
        }
        guard let application = browser ?? workspace.urlForApplication(toOpen: url) else { completion(false); return }
        workspace.open([url], withApplicationAt: application, configuration: NSWorkspace.OpenConfiguration()) { _, error in
            DispatchQueue.main.async { MainActor.assumeIsolated { completion(error == nil) } }
        }
    }
}

@MainActor final class BrowserReturnSession: ObservableObject {
    struct Context: Equatable {
        let runOrSetupID: String?
        let browserBundleID: String?
        let recoveryURL: URL?
    }

    @Published private(set) var busy = false
    @Published private(set) var recoverySent = false
    @Published private(set) var message = ""
    private var context: Context?
    private var generation = 0

    func use(_ next: Context) {
        guard next != context else { return }
        context = next
        generation += 1
        busy = false
        recoverySent = false
        message = ""
    }

    func invalidate() {
        if busy { message = "" }
        generation += 1
        busy = false
    }

    func returnToAccount(using action: ReturnToBrowserAction, context: Context) {
        use(context)
        guard !busy else { return }
        busy = true
        message = "Returning to your browser…"
        generation += 1
        let token = generation
        action.activateOriginalBrowser(preferredBrowserBundleID: context.browserBundleID) { [weak self] result in
            guard let self, self.busy, self.generation == token else { return }
            self.busy = false
            switch result {
            case .observed:
                self.message = "Your browser came forward. Choose the AMPLIFai account tab you left open."
            case .unconfirmed:
                self.message = "The browser return could not be confirmed. Switch to your AMPLIFai account tab manually."
            case .unavailable:
                self.message = "Switch to the browser and AMPLIFai account tab you left open."
            }
        }
    }

    func recover(using action: ReturnToBrowserAction, context: Context) {
        use(context)
        guard !busy, !recoverySent, let url = context.recoveryURL else { return }
        busy = true
        message = "Opening your account link…"
        generation += 1
        let token = generation
        action(url, preferredBrowserBundleID: context.browserBundleID) { [weak self] opened in
            guard let self, self.busy, self.generation == token else { return }
            self.busy = false
            self.recoverySent = opened
            self.message = opened
                ? "Account link sent to your browser. Choose the AMPLIFai tab and sign in if asked."
                : "The account link could not open. Try again, or switch to your existing AMPLIFai account tab."
        }
    }
}

enum ReturnBrowser {
    /// The return browser for a run link: the app that sent the Apple Event, kept only when it is another
    /// application that opens https URLs.
    @MainActor static func bundleIdentifier(forSenderOf event: NSAppleEventDescriptor) -> String? {
        guard let pid = event.attributeDescriptor(forKeyword: AEKeyword(keySenderPIDAttr))?.int32Value, pid > 0,
              pid != ProcessInfo.processInfo.processIdentifier,
              let sender = NSRunningApplication(processIdentifier: pid),
              let probe = URL(string: CollectorModel.accountOrigin) else { return nil }
        return ReturnBrowserChoice.accept(senderBundleID: sender.bundleIdentifier, senderBundleURL: sender.bundleURL,
                                          ownBundleID: Bundle.main.bundleIdentifier,
                                          httpsHandlers: NSWorkspace.shared.urlsForApplications(toOpen: probe))
    }
}

private struct ReturnToBrowserKey: EnvironmentKey {
    static let defaultValue = ReturnToBrowserAction.system
}

extension EnvironmentValues {
    var returnToBrowser: ReturnToBrowserAction {
        get { self[ReturnToBrowserKey.self] }
        set { self[ReturnToBrowserKey.self] = newValue }
    }
}
