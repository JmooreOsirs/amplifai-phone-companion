import Foundation
import SwiftUI

@MainActor
final class CollectorModel: ObservableObject {
    private let maxSelectedContacts = 25_000
    enum Phase: Equatable {
        case idle, connecting, transferring, passwordRequired, processing
        case selecting, reviewing, cancelling, error
    }

    @Published var phase: Phase = .idle
    @Published var progress = 0.0
    @Published var password = ""
    @Published var contacts: [ContactPreview] = []
    @Published var selectedIDs: Set<Int> = []
    @Published var availableCalls = 0
    @Published var availableMessages = 0
    @Published var matchedCalls = 0
    @Published var matchedMessages = 0
    @Published var observedCallEarliest = ""
    @Published var observedCallLatest = ""
    @Published var observedMessageEarliest = ""
    @Published var observedMessageLatest = ""
    @Published var since = ""
    @Published var connectionTransport = ""
    @Published var pairCode = ""
    @Published var pairPort = 0
    @Published var missingSources: [String] = []
    @Published var residues: [ResiduePreview] = []
    @Published var errorMessage = ""
    @Published private(set) var helperRunning = false

    private var process: Process?
    private var inputHandle: FileHandle?
    private var outputBuffer = Data()
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    var canConnect: Bool {
        !helperRunning && residues.isEmpty && (phase == .idle || phase == .error)
    }

    func inspectResidue() {
        guard process == nil else { return }
        launch(mode: "inspect")
    }

    func clearResidue() {
        guard process == nil && !residues.isEmpty else { return }
        launch(mode: "clear-residue")
    }

    func connect() {
        guard canConnect else { return }
        contacts = []
        selectedIDs = []
        availableCalls = 0
        availableMessages = 0
        matchedCalls = 0
        matchedMessages = 0
        observedCallEarliest = ""
        observedCallLatest = ""
        observedMessageEarliest = ""
        observedMessageLatest = ""
        missingSources = []
        progress = 0
        connectionTransport = ""
        pairCode = ""
        pairPort = 0
        errorMessage = ""
        phase = .connecting
        launch(mode: "connect")
    }

    func submitPassword() {
        guard phase == .passwordRequired, !password.isEmpty, password.count <= 512 else { return }
        let submitted = password
        password = ""
        send(["action": "password", "value": submitted])
        phase = .processing
    }

    func toggle(_ id: Int) {
        if !pairCode.isEmpty {
            send(["action": "revoke"])
            pairCode = ""
        }
        if selectedIDs.contains(id) {
            selectedIDs.remove(id)
        } else if selectedIDs.count < maxSelectedContacts {
            selectedIDs.insert(id)
        }
    }

    func review() {
        guard phase == .selecting || phase == .reviewing else { return }
        guard !selectedIDs.isEmpty && selectedIDs.count <= maxSelectedContacts else { return }
        send(["action": "review", "ids": selectedIDs.sorted()])
    }

    func pairBrowser() {
        guard phase == .reviewing, !selectedIDs.isEmpty else { return }
        send(["action": "pair"])
    }

    func disconnect() {
        guard let process else { return }
        pairCode = ""
        if phase == .selecting || phase == .reviewing {
            send(["action": "disconnect"])
        } else if process.isRunning {
            phase = .cancelling
            process.terminate()
        }
    }

    private func launch(mode: String) {
        guard process == nil else { return }
        guard let resources = Bundle.main.resourceURL else {
            fail("The app bundle is missing its local helper.")
            return
        }
        let portableHelper = resources.appendingPathComponent("helper/amplifai-agent")
        let uvxHelper = resources.appendingPathComponent("uvx")
        let module = resources.appendingPathComponent("collector")
        let portable = FileManager.default.isExecutableFile(atPath: portableHelper.path)
        guard portable || (FileManager.default.isExecutableFile(atPath: uvxHelper.path) &&
                           FileManager.default.fileExists(atPath: module.appendingPathComponent("amplifai_phone/agent.py").path)) else {
            fail("The local collector runtime is missing. Rebuild the app.")
            return
        }

        let runner = Process()
        let input = Pipe()
        let output = Pipe()
        runner.executableURL = portable ? portableHelper : uvxHelper
        runner.arguments = portable ? [mode] : [
            "--offline", "--from", "pymobiledevice3==10.4.0",
            "python", "-m", "amplifai_phone.agent", mode
        ]
        runner.environment = [
            "HOME": NSHomeDirectory(),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": module.path,
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_OFFLINE": "1"
        ]
        runner.standardInput = input
        runner.standardOutput = output
        runner.standardError = FileHandle.nullDevice
        outputBuffer = Data()
        inputHandle = input.fileHandleForWriting
        process = runner
        helperRunning = true

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            guard !bytes.isEmpty else {
                handle.readabilityHandler = nil
                return
            }
            Task { @MainActor [weak self] in self?.consume(bytes) }
        }
        runner.terminationHandler = { [weak self] finished in
            Task { @MainActor [weak self] in
                self?.finished(mode: mode, code: finished.terminationStatus)
            }
        }
        do {
            try runner.run()
            output.fileHandleForWriting.closeFile()
            input.fileHandleForReading.closeFile()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            output.fileHandleForWriting.closeFile()
            input.fileHandleForReading.closeFile()
            process = nil
            inputHandle = nil
            helperRunning = false
            fail("The local helper could not start. Rebuild the app runtime.")
        }
    }

    private func send(_ command: [String: Any]) {
        guard let inputHandle,
              let data = try? JSONSerialization.data(withJSONObject: command),
              data.count <= 4095 else {
            fail("The local connection is unavailable.")
            return
        }
        do {
            try inputHandle.write(contentsOf: data + Data([10]))
        } catch {
            fail("The local connection closed. Connect again.")
        }
    }

    private func consume(_ bytes: Data) {
        guard !bytes.isEmpty else { return }
        outputBuffer.append(bytes)
        if outputBuffer.count > 16 * 1024 * 1024 {
            process?.terminate()
            fail("The collector returned too much data for review.")
            return
        }
        while let newline = outputBuffer.firstIndex(of: 10) {
            let line = Data(outputBuffer[..<newline])
            outputBuffer.removeSubrange(...newline)
            guard let event = try? decoder.decode(AgentEvent.self, from: line) else {
                process?.terminate()
                fail("The local helper returned an invalid response.")
                return
            }
            receive(event)
        }
    }

    private func receive(_ event: AgentEvent) {
        switch event.kind {
        case "state":
            switch event.state {
            case "connecting": phase = .connecting
            case "password_required": phase = .passwordRequired
            case "processing": phase = .processing
            case "disconnected", "cancelled": phase = .idle
            case "pairing_revoked": pairCode = ""
            default: break
            }
        case "progress":
            progress = max(0, min(100, event.value ?? 0))
            phase = .transferring
        case "connection":
            connectionTransport = event.transport == "wifi" ? "Wi-Fi" : "USB"
        case "capture":
            contacts = event.contacts ?? []
            availableCalls = event.availableCalls ?? 0
            availableMessages = event.availableMessages ?? 0
            since = event.since ?? ""
            missingSources = event.missing ?? []
            phase = .selecting
        case "review":
            pairCode = ""
            matchedCalls = event.matchedCalls ?? 0
            matchedMessages = event.matchedMessages ?? 0
            observedCallEarliest = event.observedCallEarliest ?? ""
            observedCallLatest = event.observedCallLatest ?? ""
            observedMessageEarliest = event.observedMessageEarliest ?? ""
            observedMessageLatest = event.observedMessageLatest ?? ""
            missingSources = event.missingSources ?? missingSources
            phase = .reviewing
        case "pairing":
            pairCode = event.pairCode ?? ""
            pairPort = event.port ?? 0
        case "residue":
            residues = event.sessions ?? []
        case "cleared":
            residues = []
            phase = .idle
        case "error":
            if event.code == "selection" {
                errorMessage = "Choose supported contacts from this phone."
            } else if event.code == "bridge_unavailable" {
                errorMessage = "Local browser pairing could not start. Keep the review here or try again."
            } else {
                fail(Self.message(for: event.code))
            }
        default:
            process?.terminate()
            fail("The local helper returned an unknown response.")
        }
    }

    private func finished(mode: String, code: Int32) {
        process = nil
        inputHandle = nil
        outputBuffer = Data()
        helperRunning = false
        if mode == "connect" && code != 0 && phase != .error && phase != .idle {
            fail(code == 130 ? "Collection was cancelled." : "Collection stopped. Check the phone and try again.")
        }
    }

    private func fail(_ message: String) {
        errorMessage = message
        phase = .error
    }

    private static func message(for code: String?) -> String {
        switch code {
        case "phone_connection": return "Use one previously paired iPhone on this network, or connect it by USB, unlock it, and try again."
        case "trust_required": return "Unlock the iPhone and approve its Trust prompt, then try again."
        case "backup_password": return "The existing encrypted-backup password was not accepted. Check it and retry."
        case "unsupported_schema": return "This iPhone backup format is not supported safely yet. No metadata was saved."
        case "workspace": return "Private temporary storage is unavailable or below the free-space limit."
        case "timeout": return "The backup took longer than one hour. Reconnect and try again."
        default: return "Collection stopped. No metadata was saved. Reconnect and try again."
        }
    }
}
