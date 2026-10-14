import Foundation
import Combine
import Sparkle

@main
@MainActor
struct CompanionUpdaterChecks {
    static func main() async {
        configurationAndChannelChecks()
        delegatePathChecks()
        disabledUpdatesCannotContactFeedOrInstall()
        let model = CollectorModel()
        let updater = CompanionUpdater(bundleInformation: qualifiedInformation())
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
        await statusProcessChecks()
        await closedStatusPipeCannotTerminateWrapper()
    }

    static func qualifiedInformation(architecture requestedArchitecture: String? = nil) -> [String: Any] {
        let architecture = requestedArchitecture ?? CompanionUpdater.architecture
        return ["CFBundleIdentifier": architecture == "arm64" ? "ai.satoris.amplifai.phone.candidate" : "ai.satoris.amplifai.phone.intel-candidate",
         "CFBundleShortVersionString": "2026.10.14-rc25", "CFBundleVersion": "26101425",
         "SUFeedURL": architecture == "arm64" ? CompanionUpdater.armFeed : CompanionUpdater.intelFeed,
         "SUPublicEDKey": CompanionUpdater.publicKey, "SURequireSignedFeed": true,
         "SUVerifyUpdateBeforeExtraction": true, "AMPLIFaiQualifiedUpdateFeed": true]
    }

    private static func configurationAndChannelChecks() {
        for architecture in ["arm64", "x86_64"] {
            let info = qualifiedInformation(architecture: architecture)
            precondition(CompanionUpdater.isQualifiedConfiguration(info, architecture: architecture))
            precondition(!CompanionUpdater.isQualifiedConfiguration(info, architecture: architecture == "arm64" ? "x86_64" : "arm64"))
            var wrong = info
            wrong["SUFeedURL"] = "https://example.invalid/feed.xml"
            precondition(!CompanionUpdater.isQualifiedConfiguration(wrong, architecture: architecture))
            wrong = info
            wrong["SUPublicEDKey"] = "wrong-key"
            precondition(!CompanionUpdater.isQualifiedConfiguration(wrong, architecture: architecture))
            let channel = architecture == "arm64" ? "macos-arm64" : "macos-intel-test"
            precondition(CompanionUpdater.acceptsUpdate(channel: channel, hardwareRequirements: [architecture], build: "26101426", currentBuild: "26101425", architecture: architecture))
            precondition(!CompanionUpdater.acceptsUpdate(channel: channel, hardwareRequirements: [architecture], build: "26101424", currentBuild: "26101425", architecture: architecture))
            precondition(!CompanionUpdater.acceptsUpdate(channel: channel, hardwareRequirements: [architecture == "arm64" ? "x86_64" : "arm64"], build: "26101426", currentBuild: "26101425", architecture: architecture))
            precondition(!CompanionUpdater.acceptsUpdate(channel: "other-channel", hardwareRequirements: [architecture], build: "26101426", currentBuild: "26101425", architecture: architecture))
        }
        precondition(CompanionUpdater.acceptsUpdate(channel: nil, hardwareRequirements: [], build: "26101426", currentBuild: "26101425", architecture: "arm64"))
        precondition(CompanionUpdater.acceptsUpdate(channel: nil, hardwareRequirements: ["arm64"], build: "26101426", currentBuild: "26101425", architecture: "arm64"))
        precondition(!CompanionUpdater.acceptsUpdate(channel: nil, hardwareRequirements: ["x86_64"], build: "26101426", currentBuild: "26101425", architecture: "arm64"))
        precondition(!CompanionUpdater.acceptsUpdate(channel: nil, hardwareRequirements: [], build: "26101426", currentBuild: "26101425", architecture: "x86_64"))
        var legacyIntel = qualifiedInformation(architecture: "x86_64")
        legacyIntel.removeValue(forKey: "SUFeedURL")
        legacyIntel.removeValue(forKey: "AMPLIFaiQualifiedUpdateFeed")
        precondition(!CompanionUpdater.isQualifiedConfiguration(legacyIntel, architecture: "x86_64"))
    }

    private static func delegatePathChecks() {
        let model = CollectorModel()
        let updater = CompanionUpdater(bundleInformation: qualifiedInformation())
        updater.bind(model: model)
        let controller = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: updater, userDriverDelegate: nil)
        let item = SUAppcastItem.empty()
        model.phase = .processing
        do { try updater.updater(controller.updater, mayPerform: .updatesInBackground) }
        catch { preconditionFailure("A signed background check may discover/download while local work stays protected") }
        precondition(updater.state == .checking && !model.canInstallUpdate)
        do {
            try updater.updater(controller.updater, mayPerform: .updates)
            preconditionFailure("A manual Sparkle dialog must recheck the live gate")
        } catch {
            precondition((error as NSError).domain == CompanionUpdater.gateErrorDomain)
            updater.updater(controller.updater, didFinishUpdateCycleFor: .updates, error: error)
            precondition(updater.state == .deferred(nil))
        }
        precondition(!model.canTerminateForUpdate)
        precondition(!updater.responds(to: NSSelectorFromString("updaterShouldRelaunchApplication:")),
                     "A relaunch veto runs before Sparkle's deferral callback and must not discard it")
        var calls = 0
        precondition(updater.updater(controller.updater, shouldPostponeRelaunchForUpdate: item, untilInvokingBlock: { calls += 1 }))
        precondition(calls == 0)
        model.phase = .idle
        updater.resumeIfSafe()
        updater.resumeIfSafe()
        precondition(calls == 1 && model.updateInstallationInProgress)
        precondition(model.canTerminateForUpdate)
        model.endUpdateInstallation()
        model.phase = .reviewing
        precondition(updater.updater(controller.updater, willInstallUpdateOnQuit: item, immediateInstallationBlock: { calls += 1 }))
        precondition(calls == 1 && !model.canTerminateForUpdate)
        model.phase = .completed
        updater.resumeIfSafe()
        precondition(calls == 2 && model.updateInstallationInProgress)
        model.endUpdateInstallation()
        let startup = CompanionUpdater(bundleInformation: qualifiedInformation())
        model.setCompanionStatus(.starting)
        startup.startIfNeeded(model: model)
        precondition(startup.state == .deferred(nil), "The startup check waits for status ownership")
        model.setCompanionStatus(.anotherAppRunning)
        startup.resumeIfSafe()
        precondition(startup.state == .deferred(nil))
        precondition(CompanionStatusService.validIdentity(version: "2026.10.14-rc25+test", build: "26101425"))
        precondition(!CompanionStatusService.validIdentity(version: "2026_10", build: "26101425"))
        precondition(!CompanionStatusService.validIdentity(version: "2026.10", build: "0"))
        precondition(!CompanionUpdater.acceptsUpdate(channel: nil, hardwareRequirements: [], build: "026101426", currentBuild: "26101425", architecture: "arm64"))
        print("Actual Sparkle busy discovery, manual-dialog, relaunch and install-on-quit delegate paths passed")
    }

    private static func statusProcessChecks() async {
        guard CommandLine.arguments.count == 3 else { preconditionFailure("Status checks require their isolated helper fixtures") }
        let model = CollectorModel()
        let updater = CompanionUpdater(bundleInformation: qualifiedInformation())
        updater.bind(model: model)
        let service = CompanionStatusService()
        service.startIfNeeded(model: model, updater: updater, helperURL: URL(fileURLWithPath: CommandLine.arguments[1]))
        precondition(model.companionStatus == .starting && !model.canPrepareCollection)
        await waitForStatus(.ready, model: model)
        precondition(model.canPrepareCollection)
        model.phase = .reviewing
        precondition(service.receiveEvent(Data("{\"event\":\"update_check_requested\"}".utf8)))
        precondition(updater.state == .deferred(nil) && !model.canInstallUpdate)
        precondition(!service.receiveEvent(Data("{\"event\":\"update_check_requested\",\"path\":\"private\"}".utf8)))
        service.stop()
        let conflict = CompanionStatusService()
        model.phase = .idle
        conflict.startIfNeeded(model: model, updater: updater, helperURL: URL(fileURLWithPath: CommandLine.arguments[1]))
        precondition(conflict.receiveEvent(Data("{\"event\":\"status_unavailable\",\"code\":\"port_in_use\"}".utf8)))
        precondition(model.companionStatus == .anotherAppRunning && !model.canPrepareCollection && !model.canInstallUpdate)
        model.connect()
        precondition(!model.helperRunning, "A second running app cannot collect")
        conflict.stop()
        print("Mac status process admission, live check deferral, strict events and conflict gates passed")
    }

    private static func waitForStatus(_ expected: CollectorModel.CompanionStatus, model: CollectorModel) async {
        if model.companionStatus == expected { return }
        await withCheckedContinuation { continuation in
            var observation: AnyCancellable?
            var completed = false
            observation = model.$companionStatus.filter { $0 == expected }.prefix(1).sink { _ in
                completed = true
                observation?.cancel()
                observation = nil
                continuation.resume()
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
                precondition(completed, "Status child did not become ready")
            }
        }
    }

    private static func closedStatusPipeCannotTerminateWrapper() async {
        let model = CollectorModel()
        let updater = CompanionUpdater(bundleInformation: qualifiedInformation())
        let service = CompanionStatusService()
        service.startIfNeeded(model: model, updater: updater,
                              helperURL: URL(fileURLWithPath: CommandLine.arguments[2]))
        await waitForStatus(.unavailable, model: model)
        precondition(!model.canPrepareCollection && !model.canInstallUpdate,
                     "A failed status pipe must leave collection and installation gated")
        service.stop()
        print("Actual closed-status-stdin subprocess survived without SIGPIPE and blocked collection")
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
