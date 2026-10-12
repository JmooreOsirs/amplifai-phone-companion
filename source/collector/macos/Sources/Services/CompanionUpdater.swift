import Combine
import Foundation
import Sparkle

@MainActor
final class CompanionUpdater: NSObject, ObservableObject, SPUUpdaterDelegate {
    enum State: Equatable {
        case manualOnly, checking, current, unavailable, available(String), deferred(String?), installing, failed
    }

    @Published private(set) var state: State = .manualOnly
    private weak var model: CollectorModel?
    private var observation: AnyCancellable?
    private var controller: SPUStandardUpdaterController?
    private var availableVersion: String?
    private var postponedInstall: (() -> Void)?

    var currentVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
    }

    func startIfNeeded(model: CollectorModel) {
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
        guard model?.canInstallUpdate == true else {
            state = .deferred(availableVersion)
            return
        }
        state = .checking
        controller?.checkForUpdates(nil)
    }

    @discardableResult
    func postponeInstallIfNeeded(version: String, handler: @escaping () -> Void) -> Bool {
        availableVersion = version
        postponedInstall = handler
        state = .deferred(version)
        resumeIfSafe()
        return true
    }

    func resumeIfSafe() {
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
        availableVersion = item.displayVersionString
        state = model?.canInstallUpdate == true ? .available(item.displayVersionString) : .deferred(item.displayVersionString)
    }

    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: any Error) {
        availableVersion = nil
        model?.endUpdateInstallation()
        state = Self.noUpdateState(error)
    }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
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
