import AppKit
import SwiftUI

/// Returning activates the originating browser without navigating. Opening a URL is a separate recovery
/// operation: macOS cannot select the original tab, window or profile.
struct ReturnToBrowserAction {
    let perform: @MainActor (URL, String?, @escaping @MainActor (Bool) -> Void) -> Void
    private let activateExisting: @MainActor (String?) -> Bool

    init(activateExisting: @escaping @MainActor (String?) -> Bool = { _ in false },
         perform: @escaping @MainActor (URL, String?, @escaping @MainActor (Bool) -> Void) -> Void) {
        self.activateExisting = activateExisting
        self.perform = perform
    }

    @MainActor func activateOriginalBrowser(preferredBrowserBundleID: String?) -> Bool {
        activateExisting(preferredBrowserBundleID)
    }

    @MainActor func callAsFunction(_ url: URL, preferredBrowserBundleID: String?,
                                   completion: @escaping @MainActor (Bool) -> Void = { _ in }) {
        perform(url, preferredBrowserBundleID, completion)
    }

    static let system = ReturnToBrowserAction(activateExisting: {
        AppKitCompanionSurface.activateOriginalBrowser(bundleID: $0)
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
