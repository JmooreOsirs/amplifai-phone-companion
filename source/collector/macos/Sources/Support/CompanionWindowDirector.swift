import Foundation

/// Every way the Mac window or app could change focus goes through this surface. The AppKit implementation is
/// `AppKitCompanionSurface`, the only file allowed to call AppKit activation APIs; checks substitute a recorder.
@MainActor protocol CompanionWindowSurface: AnyObject {
    var isNativeAppFrontmost: Bool { get }
    var hasAttachedSheet: Bool { get }
    func setFloating(_ floating: Bool)
    func setKeepsVisibleWhenInactive(_ keep: Bool)
    func miniaturize()
    /// Never called by the director: after the handoff the website keeps foreground ownership.
    func deminiaturize()
    /// Never called by the director: after the handoff the website keeps foreground ownership.
    func activateApp()
    /// Never called by the director: after the handoff the website keeps foreground ownership.
    func makeKeyAndOrderFront()
    /// Never called by the director: after the handoff the website keeps foreground ownership.
    func orderFront()
    /// Only for the owner's own close click.
    func presentCloseConfirmation(title: String, detail: String, completion: @escaping @MainActor (Bool) -> Void)
    func openInBrowser(_ url: URL, preferredBrowserBundleID: String?, completion: @escaping @MainActor (Bool) -> Void)
}

/// One active surface. After the run's handoff is bound, the Mac opens the account in the return browser and
/// minimizes its window once. From then on nothing here restores, activates or raises the window, or presents
/// a sheet, for any status, receipt, save, helper exit, completion or error. The owner may reopen it themselves.
@MainActor final class CompanionWindowDirector {
    private(set) var handedOffHandoffID = ""
    private var openedNavigationID: UUID?

    var handedOff: Bool { !handedOffHandoffID.isEmpty }

    func update(model: CollectorModel, surface: CompanionWindowSurface) {
        // Floating only while the owner is working in this app before the handoff; never over the browser.
        surface.setFloating(model.activeFlow && !model.browserOwnsFlow && !handedOff &&
                            surface.isNativeAppFrontmost && !surface.hasAttachedSheet)
        surface.setKeepsVisibleWhenInactive(model.activeFlow)
        guard let navigation = model.accountNavigation, navigation.id != openedNavigationID,
              model.browserHandoffReady, navigation.handoffID == model.handoffID else { return }
        openedNavigationID = navigation.id
        surface.openInBrowser(navigation.url, preferredBrowserBundleID: model.returnBrowserBundleID) {
            [weak self, weak model, weak surface] opened in
            guard let self, let model, let surface else { return }
            // A late answer may arrive after the flow was cancelled, replaced or completed: act only when this
            // exact navigation and handoff are still current and the handoff still belongs to the browser.
            guard model.accountNavigation?.id == navigation.id, model.handoffID == navigation.handoffID,
                  model.browserHandoffReady, self.openedNavigationID == navigation.id else { return }
            guard opened else {
                // The window is still the active surface here, so the owner sees the recovery in place.
                model.accountNavigationFailed()
                return
            }
            guard self.handedOffHandoffID != navigation.handoffID else { return }
            self.handedOffHandoffID = navigation.handoffID
            surface.miniaturize()
        }
    }
}
