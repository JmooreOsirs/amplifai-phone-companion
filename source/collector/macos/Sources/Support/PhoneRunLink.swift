import Foundation

/// The app link the website opens after the owner's one Agree and collect:
/// `amplifai-phone://start?run=<lowercase uuid>&bootstrap=<43 base64url>`.
/// Exactly those two query items and nothing else; any other URL is ignored without a state change or prompt.
struct PhoneRunLink: Equatable {
    static let scheme = "amplifai-phone"

    let runID: String
    let bootstrap: String

    init?(url: URL) { self.init(string: url.absoluteString) }

    init?(string: String) {
        guard let fields = PhoneLinkFields(string, action: "start", idKey: "run") else { return nil }
        runID = fields.id
        bootstrap = fields.bootstrap
    }
}

struct PhoneSetupLink: Equatable {
    let setupID: String
    let bootstrap: String

    init?(url: URL) { self.init(string: url.absoluteString) }

    init?(string: String) {
        guard let fields = PhoneLinkFields(string, action: "setup", idKey: "setup") else { return nil }
        setupID = fields.id
        bootstrap = fields.bootstrap
    }
}

private struct PhoneLinkFields {
    let id: String
    let bootstrap: String

    init?(_ string: String, action: String, idKey: String) {
        guard string.utf8.count <= 200, string.hasPrefix("amplifai-phone://\(action)?"),
              string.unicodeScalars.allSatisfy({ $0.value > 32 && $0.value < 127 }),
              !string.contains("%"), !string.contains("+"), !string.contains("#"),
              let components = URLComponents(string: string),
              components.scheme == PhoneRunLink.scheme, components.host == action,
              components.user == nil, components.password == nil, components.port == nil,
              components.path.isEmpty, components.fragment == nil,
              let items = components.queryItems, items.count == 2,
              Set(items.map(\.name)) == [idKey, "bootstrap"],
              let id = items.first(where: { $0.name == idKey })?.value,
              let bootstrap = items.first(where: { $0.name == "bootstrap" })?.value,
              PhoneRunContract.isID(id), PhoneRunContract.isCapability(bootstrap) else { return nil }
        self.id = id
        self.bootstrap = bootstrap
    }
}

/// A parsed link plus the browser that sent it (when it qualifies as a return browser).
struct PendingRunLink: Equatable {
    let link: PhoneRunLink
    let returnBrowserBundleID: String?
}

struct PendingSetupLink: Equatable {
    let link: PhoneSetupLink
    let returnBrowserBundleID: String?
}

enum PendingPhoneLink: Equatable {
    case run(PendingRunLink)
    case setup(PendingSetupLink)
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
    private(set) var pending: PendingPhoneLink?
    private weak var model: CollectorModel?

    /// Returns false when the URL is not a supported phone link (ignored).
    @discardableResult
    func receive(_ url: URL, returnBrowserBundleID: String?) -> Bool {
        let item: PendingPhoneLink
        if let link = PhoneRunLink(url: url) {
            item = .run(PendingRunLink(link: link, returnBrowserBundleID: returnBrowserBundleID))
        } else if let link = PhoneSetupLink(url: url) {
            item = .setup(PendingSetupLink(link: link, returnBrowserBundleID: returnBrowserBundleID))
        } else { return false }
        if let model, model.readyForRunLinks {
            deliver(item, to: model)
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
        deliver(item, to: model)
    }

    private func deliver(_ item: PendingPhoneLink, to model: CollectorModel) {
        switch item {
        case .run(let link): model.admitRunLink(link)
        case .setup(let link): model.admitSetupLink(link)
        }
    }
}
