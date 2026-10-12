import Combine
import Foundation
import Sparkle

@MainActor
final class CompanionUpdater: NSObject, ObservableObject, SPUUpdaterDelegate {
    enum State: Equatable {
        case updatesDisabled, manualOnly, checking, current, unavailable, available(String), deferred(String?), installing, failed
    }

    @Published private(set) var state: State = .manualOnly
    private weak var model: CollectorModel?
    private var observation: AnyCancellable?
    private var controller: SPUStandardUpdaterController?
    private var availableVersion: String?
    private var postponedInstall: (() -> Void)?
    let publishedUpdatesEnabled: Bool

    // The existing signed feed contains Apple Silicon releases. Intel candidates
    // must not contact it or schedule one of its installers. Enable Intel updates
    // only after a separate, architecture-qualified signed feed is provided.
    static var supportsPublishedUpdates: Bool {
        #if arch(arm64)
        return true
        #else
        return false
        #endif
    }

    init(enablePublishedUpdates: Bool = true) {
        publishedUpdatesEnabled = Self.supportsPublishedUpdates && enablePublishedUpdates
        super.init()
        if !publishedUpdatesEnabled { state = .updatesDisabled }
    }

    var currentVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    }

    func startIfNeeded(model: CollectorModel) {
        guard publishedUpdatesEnabled else {
            state = .updatesDisabled
            return
        }
        guard controller == nil else { return }
        bind(model: model)
        let updater = SPUStandardUpdaterController(
            startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil
        )
        controller = updater
        updater.startUpdater()
        let automaticChecksEnabled = updater.updater.automaticallyChecksForUpdates
        state = Self.startupState(automaticChecksEnabled: automaticChecksEnabled)
        if automaticChecksEnabled {
            updater.updater.checkForUpdatesInBackground()
        }
    }

    static func startupState(automaticChecksEnabled: Bool) -> State {
        automaticChecksEnabled ? .checking : .manualOnly
    }

    func bind(model: CollectorModel) {
        self.model = model
        observation = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.resumeIfSafe() }
        }
    }

    func checkNow() {
        guard publishedUpdatesEnabled else {
            state = .updatesDisabled
            return
        }
        guard model?.canInstallUpdate == true else {
            state = .deferred(availableVersion)
            return
        }
        state = .checking
        controller?.checkForUpdates(nil)
    }

    @discardableResult
    func postponeInstallIfNeeded(version: String, handler: @escaping () -> Void) -> Bool {
        guard publishedUpdatesEnabled else {
            state = .updatesDisabled
            return true
        }
        availableVersion = version
        postponedInstall = handler
        state = .deferred(version)
        resumeIfSafe()
        return true
    }

    func resumeIfSafe() {
        guard publishedUpdatesEnabled else {
            postponedInstall = nil
            state = .updatesDisabled
            return
        }
        guard let model, model.canInstallUpdate else {
            if let availableVersion { state = .deferred(availableVersion) }
            return
        }
        if let install = postponedInstall {
            guard model.beginUpdateInstallation() else { return }
            postponedInstall = nil
            state = .installing
            install()
        } else if let availableVersion, case .deferred = state {
            state = .available(availableVersion)
        }
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        guard publishedUpdatesEnabled else { return }
        availableVersion = item.displayVersionString
        state = model?.canInstallUpdate == true ? .available(item.displayVersionString) : .deferred(item.displayVersionString)
    }

    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: any Error) {
        guard publishedUpdatesEnabled else { return }
        availableVersion = nil
        model?.endUpdateInstallation()
        state = Self.noUpdateState(error)
    }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
        guard publishedUpdatesEnabled else { return }
        guard let error else { model?.endUpdateInstallation(); return }
        postponedInstall = nil
        model?.endUpdateInstallation()
        let failure = error as NSError
        guard failure.domain == SUSparkleErrorDomain else { state = .failed; return }
        if failure.code == 1001 { state = Self.noUpdateState(error) }
        else if failure.code == 4007 { state = availableVersion.map(State.available) ?? .current }
        else { state = .failed }
    }

    static func noUpdateState(_ error: Error) -> State {
        let failure = error as NSError
        guard failure.domain == SUSparkleErrorDomain, failure.code == 1001,
              let reason = failure.userInfo[SPUNoUpdateFoundReasonKey] as? NSNumber else { return .failed }
        switch reason.intValue {
        case 1, 2: return .current // latest in feed, or newer than its latest entry
        case 3, 4, 5: return .unavailable // incompatible OS or CPU
        default: return .failed
        }
    }

    func updater(_ updater: SPUUpdater, shouldPostponeRelaunchForUpdate item: SUAppcastItem,
                 untilInvokingBlock installHandler: @escaping () -> Void) -> Bool {
        postponeInstallIfNeeded(version: item.displayVersionString, handler: installHandler)
    }

    func updater(_ updater: SPUUpdater, willInstallUpdateOnQuit item: SUAppcastItem,
                 immediateInstallationBlock installHandler: @escaping () -> Void) -> Bool {
        scheduleInstallOnQuit(version: item.displayVersionString, handler: installHandler)
        return true
    }

    func scheduleInstallOnQuit(version: String, handler: @escaping () -> Void) {
        _ = postponeInstallIfNeeded(version: version, handler: handler)
    }
}
