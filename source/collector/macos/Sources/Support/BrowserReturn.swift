import AppKit
import SwiftUI

/// Opens an AMPLIFai account page in the browser that sent the run link, or the default browser. A host can
/// replace it (the FAKE DATA demo shows its simulated website instead).
///
/// Limitation: macOS opens the URL in that browser application only. It cannot choose a browser profile, window
/// or tab; the browser decides, usually a new tab in its frontmost window.
struct ReturnToBrowserAction {
    let perform: @MainActor (URL, String?, @escaping @MainActor (Bool) -> Void) -> Void

    @MainActor func callAsFunction(_ url: URL, preferredBrowserBundleID: String?,
                                   completion: @escaping @MainActor (Bool) -> Void = { _ in }) {
        perform(url, preferredBrowserBundleID, completion)
    }

    static let system = ReturnToBrowserAction { url, preferred, completion in
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
