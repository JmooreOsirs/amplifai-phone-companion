import Combine
import Foundation

@MainActor
final class CompanionStatusService: ObservableObject {
    private static let maxEventBytes = 4096
    private weak var model: CollectorModel?
    private weak var updater: CompanionUpdater?
    private var observations: [AnyCancellable] = []
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var outputBuffer = Data()
    private var lastStatus: Data?
    private var generation = UUID()
    private var ready = false

    func startIfNeeded(model: CollectorModel, updater: CompanionUpdater, helperURL: URL? = nil) {
        guard process == nil, observations.isEmpty else { return }
        self.model = model
        self.updater = updater
        model.setCompanionStatus(.starting)
        let helper = helperURL ?? Bundle.main.resourceURL?.appendingPathComponent("helper/amplifai-agent")
        guard let helper, FileManager.default.isExecutableFile(atPath: helper.path),
              Self.validIdentity(version: updater.currentVersion, build: updater.currentBuild) else {
            model.setCompanionStatus(.unavailable)
            return
        }
        let runner = Process()
        let stdin = Pipe()
        let stdout = Pipe()
        runner.executableURL = helper
        runner.arguments = ["companion-status", "--platform", "macos", "--architecture", CompanionUpdater.architecture,
                            "--channel", CompanionUpdater.channel, "--bundle-id", CompanionUpdater.bundleID,
                            "--version", updater.currentVersion, "--build", updater.currentBuild]
        runner.standardInput = stdin
        runner.standardOutput = stdout
        runner.standardError = FileHandle.nullDevice
        // Status mode is self-contained and must not inherit collector configuration or credentials.
        runner.environment = ["PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "PYTHONNOUSERSITE": "1"]
        let id = UUID()
        generation = id
        process = runner
        input = stdin.fileHandleForWriting
        output = stdout.fileHandleForReading
        outputBuffer = Data()
        lastStatus = nil
        ready = false
        output?.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            if bytes.isEmpty { handle.readabilityHandler = nil }
            Task { @MainActor [weak self] in self?.receive(bytes, generation: id) }
        }
        runner.terminationHandler = { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.generation == id else { return }
                self.releaseHandles()
                self.process = nil
                if self.model?.companionStatus != .anotherAppRunning { self.model?.setCompanionStatus(.unavailable) }
            }
        }
        observations = [model.objectWillChange.sink { [weak self] _ in self?.scheduleStatus() },
                        updater.objectWillChange.sink { [weak self] _ in self?.scheduleStatus() }]
        do { try runner.run() }
        catch {
            releaseHandles()
            process = nil
            observations.removeAll()
            model.setCompanionStatus(.unavailable)
        }
    }

    static func validIdentity(version: String, build: String) -> Bool {
        version.range(of: "^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$", options: .regularExpression) != nil &&
            build.range(of: "^[1-9][0-9]{0,11}$", options: .regularExpression) != nil
    }

    private func scheduleStatus() {
        DispatchQueue.main.async { [weak self] in self?.publishStatus() }
    }

    private func publishStatus() {
        guard ready, let model, let updater, let input else { return }
        var status: [String: Any] = ["action": "set_status", "busy": !model.canInstallUpdate,
                                   "updateState": updater.statusUpdateState,
                                   "availableVersion": NSNull(), "availableBuild": NSNull()]
        if let version = updater.statusAvailableVersion, let build = updater.availableBuild {
            status["availableVersion"] = version
            status["availableBuild"] = build
        }
        guard let encoded = try? JSONSerialization.data(withJSONObject: status, options: [.sortedKeys]),
              encoded.count <= 1024, encoded != lastStatus else { return }
        do {
            try input.write(contentsOf: encoded + Data([10]))
            lastStatus = encoded
        } catch {
            model.setCompanionStatus(.unavailable)
            stop()
        }
    }

    func receive(_ bytes: Data, generation id: UUID) {
        guard generation == id else { return }
        guard !bytes.isEmpty else { return }
        guard bytes.count <= Self.maxEventBytes, outputBuffer.count + bytes.count <= Self.maxEventBytes else {
            failProtocol()
            return
        }
        outputBuffer.append(bytes)
        while let newline = outputBuffer.firstIndex(of: 10) {
            let line = Data(outputBuffer[..<newline])
            outputBuffer.removeSubrange(...newline)
            guard receiveEvent(line) else { failProtocol(); return }
        }
    }

    @discardableResult
    func receiveEvent(_ line: Data) -> Bool {
        guard line.count <= Self.maxEventBytes,
              let event = try? JSONSerialization.jsonObject(with: line) as? [String: String],
              let name = event["event"] else { return false }
        switch name {
        case "status_ready":
            guard event.count == 1, !ready else { return false }
            ready = true
            model?.setCompanionStatus(.ready)
            publishStatus()
        case "update_check_requested":
            guard event.count == 1, ready else { return false }
            // The capability requests a check only. The updater rechecks collection/review safety now.
            updater?.checkNow()
            publishStatus()
        case "status_unavailable":
            guard event.count == 2, let code = event["code"], code == "port_in_use" || code == "status_failed" else { return false }
            model?.setCompanionStatus(code == "port_in_use" ? .anotherAppRunning : .unavailable)
            ready = false
        default: return false
        }
        return true
    }

    private func failProtocol() {
        model?.setCompanionStatus(.unavailable)
        stop()
    }

    private func releaseHandles() {
        ready = false
        output?.readabilityHandler = nil
        try? output?.close()
        try? input?.close()
        input = nil
        output = nil
        outputBuffer = Data()
        lastStatus = nil
    }

    func stop() {
        generation = UUID()
        observations.removeAll()
        releaseHandles()
        // Only the separately owned status child is stopped; collection uses its existing lifecycle.
        if let process, process.isRunning { process.terminate() }
        process = nil
    }
}
