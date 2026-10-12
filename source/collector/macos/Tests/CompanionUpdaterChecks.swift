import Foundation
import Sparkle

@main
@MainActor
struct CompanionUpdaterChecks {
    static func main() {
        disabledUpdatesCannotContactFeedOrInstall()
        #if arch(x86_64)
        let intelUpdater = CompanionUpdater()
        precondition(!intelUpdater.publishedUpdatesEnabled && intelUpdater.state == .updatesDisabled,
                     "Intel builds must never enable the Apple Silicon feed")
        print("Intel updater is isolated from the Apple Silicon feed and installer")
        return
        #else
        let model = CollectorModel()
        let updater = CompanionUpdater()
        updater.bind(model: model)
        precondition(updater.state == .manualOnly, "No updater request must not look like a failed check")
        precondition(CompanionUpdater.startupState(automaticChecksEnabled: false) == .manualOnly,
                     "Disabled automatic checks need a separate manual state")
        precondition(CompanionUpdater.startupState(automaticChecksEnabled: true) == .checking,
                     "Enabled automatic checks may show checking only when one starts")
        var installCalls = 0
        model.phase = .processing
        precondition(updater.postponeInstallIfNeeded(version: "2026.10.09-rc1") { installCalls += 1 })
        precondition(updater.state == .deferred("2026.10.09-rc1"))
        updater.resumeIfSafe()
        precondition(installCalls == 0, "Active parsing cannot release the updater")
        model.phase = .idle
        updater.resumeIfSafe()
        updater.resumeIfSafe()
        precondition(installCalls == 1, "A postponed installer must release exactly once")
        precondition(model.updateInstallationInProgress && !model.canPrepareCollection && !model.canInstallUpdate,
                     "A released installer must atomically block fresh collection")
        model.connect()
        precondition(!model.helperRunning && model.phase == .idle,
                     "A late collection attempt cannot race a pending relaunch")
        model.endUpdateInstallation()
        precondition(model.canPrepareCollection, "A failed or cancelled installation must restore collection admission")

        model.phase = .reviewing
        updater.scheduleInstallOnQuit(version: "2026.10.09-rc2") { installCalls += 1 }
        precondition(installCalls == 1, "Install-on-quit cannot replace an unsaved review")
        model.phase = .completed
        updater.resumeIfSafe()
        precondition(installCalls == 2, "A settled review may release the scheduled installer once")
        precondition(model.updateInstallationInProgress)
        model.endUpdateInstallation()

        func noUpdate(_ reason: Int?) -> CompanionUpdater.State {
            var info: [String: Any] = [:]
            if let reason { info[SPUNoUpdateFoundReasonKey] = NSNumber(value: reason) }
            return CompanionUpdater.noUpdateState(NSError(domain: SUSparkleErrorDomain, code: 1001, userInfo: info))
        }
        precondition(noUpdate(1) == .current && noUpdate(2) == .current)
        precondition(noUpdate(3) == .unavailable && noUpdate(4) == .unavailable && noUpdate(5) == .unavailable)
        precondition(noUpdate(nil) == .failed)
        precondition(CompanionUpdater.noUpdateState(NSError(domain: NSURLErrorDomain, code: 1001)) == .failed)
        print("Mac updater deferral, install-on-quit and no-update reason checks passed")
        #endif
    }

    private static func disabledUpdatesCannotContactFeedOrInstall() {
        let model = CollectorModel()
        let updater = CompanionUpdater(enablePublishedUpdates: false)
        precondition(!updater.publishedUpdatesEnabled && updater.state == .updatesDisabled)
        updater.startIfNeeded(model: model)
        updater.checkNow()
        precondition(updater.state == .updatesDisabled,
                     "Unsupported builds must not create a controller or begin a manual check")
        var installCalls = 0
        updater.bind(model: model)
        updater.postponeInstallIfNeeded(version: "arm64-example") { installCalls += 1 }
        updater.scheduleInstallOnQuit(version: "arm64-example") { installCalls += 1 }
        updater.resumeIfSafe()
        precondition(installCalls == 0 && !model.updateInstallationInProgress,
                     "Neither deferred nor quit-time installation may replace an Intel candidate")
        precondition(updater.state == .updatesDisabled)
    }
}
