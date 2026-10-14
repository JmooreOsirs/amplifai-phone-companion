import Combine
import Foundation
import Sparkle

@MainActor
final class CompanionUpdater: NSObject, ObservableObject, SPUUpdaterDelegate {
    enum State: Equatable {
        case updatesDisabled, manualOnly, checking, current, unavailable, available(String), deferred(String?), installing, failed
    }

    static let publicKey = "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs="
    static let armFeed = "https://amplifai-database-engine.vercel.app/phone/macos/appcast.xml"
    static let intelFeed = "https://amplifai-database-engine.vercel.app/phone/macos/intel-appcast.xml"
    static let gateErrorDomain = "ai.satoris.amplifai.phone.update-gate"
    static var architecture: String {
        #if arch(arm64)
        return "arm64"
        #else
        return "x86_64"
        #endif
    }
    static var channel: String { architecture == "arm64" ? "macos-arm64" : "macos-intel-test" }
    static var bundleID: String {
        architecture == "arm64" ? "ai.satoris.amplifai.phone.candidate" : "ai.satoris.amplifai.phone.intel-candidate"
    }

    @Published private(set) var state: State = .manualOnly
    @Published private(set) var availableBuild: String?
    private weak var model: CollectorModel?
    private var observation: AnyCancellable?
    private var controller: SPUStandardUpdaterController?
    private var availableVersion: String?
    private var postponedInstall: (() -> Void)?
    private var startupPending = false
    private var pendingManualCheck = false
    private let information: [String: Any]
    let publishedUpdatesEnabled: Bool

    init(enablePublishedUpdates: Bool = true, bundleInformation: [String: Any]? = nil) {
        let info = bundleInformation ?? Bundle.main.infoDictionary ?? [:]
        information = info
        publishedUpdatesEnabled = enablePublishedUpdates && Self.isQualifiedConfiguration(info, architecture: Self.architecture)
        super.init()
        if !publishedUpdatesEnabled { state = .updatesDisabled }
    }

    static func isQualifiedConfiguration(_ info: [String: Any], architecture: String) -> Bool {
        guard info["SUPublicEDKey"] as? String == publicKey,
              info["SURequireSignedFeed"] as? Bool == true,
              info["SUVerifyUpdateBeforeExtraction"] as? Bool == true else { return false }
        switch architecture {
        case "arm64":
            return info["CFBundleIdentifier"] as? String == "ai.satoris.amplifai.phone.candidate" &&
                info["SUFeedURL"] as? String == armFeed
        case "x86_64":
            return info["CFBundleIdentifier"] as? String == "ai.satoris.amplifai.phone.intel-candidate" &&
                info["SUFeedURL"] as? String == intelFeed && info["AMPLIFaiQualifiedUpdateFeed"] as? Bool == true
        default: return false
        }
    }

    var currentVersion: String { information["CFBundleShortVersionString"] as? String ?? "Unknown" }
    var currentBuild: String { information["CFBundleVersion"] as? String ?? "0" }
    var statusUpdateState: String {
        switch state {
        case .updatesDisabled, .manualOnly: return "manual"
        case .checking: return "checking"
        case .current: return "current"
        case .unavailable, .failed: return "failed"
        case .available: return "available"
        case .deferred: return "deferred"
        case .installing: return "installing"
        }
    }
    var statusAvailableVersion: String? { availableVersion }

    func startIfNeeded(model: CollectorModel) {
        bind(model: model)
        guard publishedUpdatesEnabled else { state = .updatesDisabled; return }
        guard controller == nil else { return }
        startupPending = true
        startWhenSafe()
    }

    private func startWhenSafe() {
        guard startupPending, let model else { return }
        guard model.canCheckForUpdatesInBackground else { state = .deferred(nil); return }
        startupPending = false
        let updater = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        controller = updater
        do { try updater.updater.start() }
        catch { controller = nil; state = .failed; return }
        let automaticChecksEnabled = updater.updater.automaticallyChecksForUpdates
        state = Self.startupState(automaticChecksEnabled: automaticChecksEnabled)
        // Info.plist supplies defaults. Sparkle's persisted user preferences are never overwritten here.
        if automaticChecksEnabled { updater.updater.checkForUpdatesInBackground() }
    }

    static func startupState(automaticChecksEnabled: Bool) -> State {
        automaticChecksEnabled ? .checking : .manualOnly
    }

    func bind(model: CollectorModel) {
        guard self.model !== model else { return }
        self.model = model
        observation = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.resumeIfSafe() }
        }
    }

    func checkNow() {
        guard publishedUpdatesEnabled else { state = .updatesDisabled; return }
        guard model?.canInstallUpdate == true else {
            pendingManualCheck = true
            state = .deferred(availableVersion)
            return
        }
        startWhenSafe()
        guard let controller, controller.updater.canCheckForUpdates else { return }
        pendingManualCheck = false
        state = .checking
        controller.checkForUpdates(nil)
    }

    @discardableResult
    func postponeInstallIfNeeded(version: String, handler: @escaping () -> Void) -> Bool {
        guard publishedUpdatesEnabled else { state = .updatesDisabled; return true }
        availableVersion = version
        postponedInstall = handler
        state = .deferred(version)
        resumeIfSafe()
        return true
    }

    func resumeIfSafe() {
        guard publishedUpdatesEnabled else { postponedInstall = nil; state = .updatesDisabled; return }
        if startupPending, model?.canCheckForUpdatesInBackground == true { startWhenSafe() }
        guard let model, model.canInstallUpdate else {
            if let availableVersion, !modelIsInstalling { state = .deferred(availableVersion) }
            return
        }
        if let install = postponedInstall {
            guard model.beginUpdateInstallation() else { return }
            postponedInstall = nil
            state = .installing
            install()
        } else if startupPending {
            startWhenSafe()
        } else if pendingManualCheck {
            checkNow()
        } else if let availableVersion, case .deferred = state {
            state = .available(availableVersion)
        }
    }
    private var modelIsInstalling: Bool { model?.updateInstallationInProgress == true }

    func feedURLString(for updater: SPUUpdater) -> String? {
        publishedUpdatesEnabled ? (Self.architecture == "arm64" ? Self.armFeed : Self.intelFeed) : nil
    }
    func allowedChannels(for updater: SPUUpdater) -> Set<String> { [Self.channel] }

    func updater(_ updater: SPUUpdater, mayPerform updateCheck: SPUUpdateCheck) throws {
        let safe = updateCheck == .updates ? model?.canInstallUpdate : model?.canCheckForUpdatesInBackground
        guard publishedUpdatesEnabled, safe == true else {
            state = publishedUpdatesEnabled ? .deferred(availableVersion) : .updatesDisabled
            throw NSError(domain: Self.gateErrorDomain, code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Finish local collection and review before checking for updates."])
        }
        state = .checking
    }

    static func acceptsUpdate(channel: String?, hardwareRequirements: Set<String>, build: String,
                              currentBuild: String, architecture: String) -> Bool {
        guard build.range(of: "^[1-9][0-9]{0,11}$", options: .regularExpression) != nil,
              let next = UInt64(build), let current = UInt64(currentBuild), next > current else { return false }
        let expectedChannel = architecture == "arm64" ? "macos-arm64" : "macos-intel-test"
        guard architecture == "arm64" || architecture == "x86_64" else { return false }
        // The retained ARM feed predates CPU/channel tags. Its fixed signed URL remains the authority.
        if architecture == "arm64", channel == nil {
            return hardwareRequirements.isEmpty || hardwareRequirements == ["arm64"]
        }
        return channel == expectedChannel && hardwareRequirements == [architecture]
    }

    func updater(_ updater: SPUUpdater, shouldProceedWithUpdate item: SUAppcastItem, updateCheck: SPUUpdateCheck) throws {
        guard publishedUpdatesEnabled,
              Self.acceptsUpdate(channel: item.channel, hardwareRequirements: item.hardwareRequirements,
                                 build: item.versionString, currentBuild: currentBuild, architecture: Self.architecture) else {
            state = .unavailable
            throw NSError(domain: Self.gateErrorDomain, code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "This update does not match this Mac's update channel or build."])
        }
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        guard publishedUpdatesEnabled else { return }
        availableVersion = item.displayVersionString
        availableBuild = item.versionString
        state = model?.canInstallUpdate == true ? .available(item.displayVersionString) : .deferred(item.displayVersionString)
    }

    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: any Error) {
        guard publishedUpdatesEnabled else { return }
        availableVersion = nil
        availableBuild = nil
        model?.endUpdateInstallation()
        state = Self.noUpdateState(error)
    }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
        guard publishedUpdatesEnabled else { return }
        guard let error else { model?.endUpdateInstallation(); return }
        let failure = error as NSError
        if failure.domain == Self.gateErrorDomain { return }
        postponedInstall = nil
        model?.endUpdateInstallation()
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
        case 1, 2: return .current
        case 3, 4, 5: return .unavailable
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
