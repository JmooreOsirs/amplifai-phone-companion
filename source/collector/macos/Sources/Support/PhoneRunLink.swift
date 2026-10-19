import Foundation

/// The app link the website opens after the owner's one Agree and collect:
/// `amplifai-phone://start?run=<lowercase uuid>&bootstrap=<43 base64url>`.
/// Exactly those two query items and nothing else; any other URL is ignored without a state change or prompt.
struct PhoneRunLink: Equatable {
    static let scheme = "amplifai-phone"
    private static let prefix = "amplifai-phone://start?"

    let runID: String
    let bootstrap: String

    init?(url: URL) { self.init(string: url.absoluteString) }

    init?(string: String) {
        guard string.utf8.count <= 200, string.hasPrefix(Self.prefix),
              string.unicodeScalars.allSatisfy({ $0.value > 32 && $0.value < 127 }),
              !string.contains("%"), !string.contains("+"), !string.contains("#"),
              let components = URLComponents(string: string),
              components.scheme == Self.scheme, components.host == "start",
              components.user == nil, components.password == nil, components.port == nil,
              components.path.isEmpty, components.fragment == nil,
              let items = components.queryItems, items.count == 2,
              Set(items.map(\.name)) == ["run", "bootstrap"],
              let run = items.first(where: { $0.name == "run" })?.value,
              let bootstrap = items.first(where: { $0.name == "bootstrap" })?.value,
              PhoneRunContract.isID(run), PhoneRunContract.isCapability(bootstrap) else { return nil }
        runID = run
        self.bootstrap = bootstrap
    }
}

/// A parsed link plus the browser that sent it (when it qualifies as a return browser).
struct PendingRunLink: Equatable {
    let link: PhoneRunLink
    let returnBrowserBundleID: String?
}

/// Which app the account pages reopen in. The app that sent the link is kept only when it is another app that
/// opens https URLs; otherwise the default browser is used. macOS cannot target a browser profile, window or tab.
enum ReturnBrowserChoice {
    static func accept(senderBundleID: String?, senderBundleURL: URL?, ownBundleID: String?, httpsHandlers: [URL]) -> String? {
        guard let identifier = senderBundleID, !identifier.isEmpty, identifier.utf8.count <= 255,
              identifier != ownBundleID, let senderBundleURL else { return nil }
        let sender = senderBundleURL.standardizedFileURL.resolvingSymlinksInPath()
        guard httpsHandlers.contains(where: { $0.standardizedFileURL.resolvingSymlinksInPath() == sender }) else { return nil }
        return identifier
    }
}

/// Holds at most one link until the model is attached and its start-up checks have finished, then hands it over
/// once. The model admits a link only when idle; otherwise it is dropped with no state change and no prompt.
@MainActor final class PhoneRunLinkInbox {
    private(set) var pending: PendingRunLink?
    private weak var model: CollectorModel?

    /// Returns false when the URL is not a valid run link (ignored).
    @discardableResult
    func receive(_ url: URL, returnBrowserBundleID: String?) -> Bool {
        guard let link = PhoneRunLink(url: url) else { return false }
        let item = PendingRunLink(link: link, returnBrowserBundleID: returnBrowserBundleID)
        if let model, model.readyForRunLinks {
            model.admitRunLink(item)
        } else {
            // One slot. A newer link replaces an older buffered one: the website reissues the bootstrap when the
            // owner opens the app again, and the earlier bootstrap then stops working.
            pending = item
        }
        return true
    }

    func attach(_ model: CollectorModel) {
        self.model = model
        model.onReadyForRunLinks = { [weak self] in self?.flush() }
        flush()
    }

    func flush() {
        guard let model, model.readyForRunLinks, let item = pending else { return }
        pending = nil
        model.admitRunLink(item)
    }
}
