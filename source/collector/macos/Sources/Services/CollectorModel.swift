import Foundation
import SwiftUI
import Darwin

@MainActor
final class CollectorModel: ObservableObject {
    private let maxSelectedContacts = 25_000
    static let collectionDisclosureVersion = "native-local-collection-v1"
    private static let localCollectionScopes = ["contacts", "call-context", "message-context", "temporary-full-backup"]
    struct LocalCollectionApproval: Equatable {
        let runID: UUID
        let disclosureVersion: String
        let approvedAt: Date
        let scopes: [String]
    }
    enum Phase: Equatable {
        case idle, connecting, transferring, passwordRequired, processing
        case selecting, reviewing, completing, completed, cancelling, error
    }

    @Published var phase: Phase = .idle
    @Published var progress = 0.0
    @Published private(set) var progressIsKnown = false
    @Published private(set) var collectionStartedAt: Date?
    @Published private(set) var handoffID = ""
    @Published private(set) var handoffAcknowledged = false
    @Published private(set) var browserReceived = false
    @Published private(set) var selectionNeedsReview = true
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
    @Published private(set) var needsStorageReview = false
    @Published private(set) var collectionApprovalRunID = UUID()
    @Published private(set) var localCollectionApproval: LocalCollectionApproval?

    private var process: Process?
    private var inputHandle: FileHandle?
    private var outputBuffer = Data()
    private(set) var operationID: UUID?
    private var outputHandle: FileHandle?
    private var outputEnded = false
    private var exitCode: Int32?
    private var cancellationRequested = false
    private var cancellationTask: Task<Void, Never>?
    private var handoffPollTask: Task<Void, Never>?
    private var pairingRequested = false
    private var lastErrorCode: String?
    private let cancellationGraceNanoseconds: UInt64 = 15_000_000_000
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    var canPrepareCollection: Bool {
        !helperRunning && process == nil && !needsStorageReview && residues.isEmpty && !hasUnsavedReview &&
            (phase == .idle || phase == .error || phase == .completed)
    }

    private var hasCurrentCollectionApproval: Bool {
        guard let approval = localCollectionApproval else { return false }
        return approval.runID == collectionApprovalRunID &&
            approval.disclosureVersion == Self.collectionDisclosureVersion && approval.scopes == Self.localCollectionScopes
    }

    var canConnect: Bool { canPrepareCollection && hasCurrentCollectionApproval }

    @discardableResult
    func approveLocalCollection(for runID: UUID, checked: Bool) -> Bool {
        guard canPrepareCollection, runID == collectionApprovalRunID else { return false }
        guard checked else { declineLocalCollection(); return false }
        localCollectionApproval = LocalCollectionApproval(
            runID: runID, disclosureVersion: Self.collectionDisclosureVersion,
            approvedAt: Date(), scopes: Self.localCollectionScopes
        )
        return true
    }

    func declineLocalCollection() {
        localCollectionApproval = nil
        collectionApprovalRunID = UUID()
    }

    var canSafelyClose: Bool {
        handoffAcknowledged && phase == .completed && !helperRunning && !needsStorageReview && residues.isEmpty
    }

    var hasUnsavedReview: Bool { !contacts.isEmpty && !handoffAcknowledged }
    var activeFlow: Bool { helperRunning || hasUnsavedReview }

    var progressExplanation: String {
        "The percentage is reported by the iPhone for backup transfer, not the entire collection or account save. A remaining-time estimate is unavailable."
    }

    func inspectResidue() {
        guard process == nil else { return }
        needsStorageReview = true
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
        progressIsKnown = false
        collectionStartedAt = Date()
        resetHandoff()
        lastErrorCode = nil
        selectionNeedsReview = true
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
        if pairingRequested || !pairCode.isEmpty {
            send(["action": "revoke"])
            pairCode = ""
        }
        resetHandoff()
        selectionNeedsReview = true
        phase = .selecting
        if selectedIDs.contains(id) {
            selectedIDs.remove(id)
        } else if selectedIDs.count < maxSelectedContacts {
            selectedIDs.insert(id)
        }
    }

    func review() {
        guard phase == .selecting || phase == .reviewing else { return }
        guard !selectedIDs.isEmpty && selectedIDs.count <= maxSelectedContacts else { return }
        resetHandoff()
        send(["action": "review", "ids": selectedIDs.sorted()])
    }

    func pairBrowser() {
        guard phase == .reviewing, !selectedIDs.isEmpty, !selectionNeedsReview else { return }
        resetHandoff()
        pairingRequested = true
        send(["action": "pair"])
    }

    func disconnect() {
        declineLocalCollection()
        guard let process else {
            // Explicit owner discard remains usable after an unexpected helper exit.
            clearPrivateReview()
            resetHandoff()
            errorMessage = ""
            phase = .idle
            return
        }
        cancellationRequested = true
        resetHandoff()
        needsStorageReview = true
        if phase == .selecting || phase == .reviewing {
            send(["action": "disconnect"])
        } else if process.isRunning {
            process.terminate()
        }
        clearPrivateReview()
        phase = .cancelling
        awaitHelperExit()
    }

    private func clearPrivateReview() {
        password = ""
        contacts = []
        selectedIDs = []
        pairCode = ""
        pairPort = 0
        matchedCalls = 0
        matchedMessages = 0
        availableCalls = 0
        availableMessages = 0
        observedCallEarliest = ""
        observedCallLatest = ""
        observedMessageEarliest = ""
        observedMessageLatest = ""
        selectionNeedsReview = true
    }

    private func resetHandoff() {
        handoffPollTask?.cancel()
        handoffPollTask = nil
        pairingRequested = false
        handoffID = ""
        handoffAcknowledged = false
        browserReceived = false
        pairCode = ""
        pairPort = 0
    }

    private func pollHandoff() {
        guard helperRunning, !handoffID.isEmpty, let operation = operationID else { return }
        let binding = handoffID
        handoffPollTask?.cancel()
        handoffPollTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 2_000_000_000) } catch { return }
                guard let self, self.operationID == operation, self.handoffID == binding,
                      self.phase == .reviewing, !self.cancellationRequested else { return }
                self.send(["action": "handoff-status", "handoffId": binding])
            }
        }
    }

    private func awaitHelperExit() {
        guard cancellationTask == nil, let runner = process, let id = operationID else { return }
        cancellationTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(nanoseconds: self?.cancellationGraceNanoseconds ?? 15_000_000_000)
            } catch { return }
            guard let self, self.operationID == id else { return }
            self.needsStorageReview = true
            self.clearPrivateReview()
            if runner.isRunning {
                // Only this operation's owned helper is terminated; residue is inspected, never auto-deleted.
                let result = Darwin.kill(runner.processIdentifier, SIGKILL)
                let primary = self.errorMessage.isEmpty ? "" : self.errorMessage + " "
                self.fail(result == 0 || errno == ESRCH
                    ? primary + "The local helper did not finish cleanup. It was stopped; check temporary data before reconnecting."
                    : "The local helper could not be stopped. Quit the app before checking temporary data and reconnecting.")
            } else {
                self.outputHandle?.readabilityHandler = nil
                try? self.outputHandle?.close()
                self.outputEnded = true
                if let code = self.exitCode { self.finished(mode: "connect", code: code) }
            }
        }
    }

    func launch(mode: String) {
        guard process == nil else { return }
        let id: UUID
        if mode == "connect" {
            guard phase == .connecting, !needsStorageReview, residues.isEmpty, !hasUnsavedReview,
                  hasCurrentCollectionApproval, let approval = localCollectionApproval else {
                fail("Review and explicitly agree to this local collection before connecting.")
                return
            }
            id = approval.runID
        } else { id = UUID() }
        // Receipt consumption precedes filesystem/helper access and cannot authorize a retry.
        declineLocalCollection()
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
            fail("The local collector runtime is missing. Download and reinstall the signed Mac companion.")
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
        operationID = id
        outputEnded = false
        exitCode = nil
        cancellationRequested = false
        inputHandle = input.fileHandleForWriting
        outputHandle = output.fileHandleForReading
        process = runner
        helperRunning = true

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            if bytes.isEmpty { handle.readabilityHandler = nil }
            Task { @MainActor [weak self] in
                guard let self, self.operationID == id else { return }
                if bytes.isEmpty {
                    self.outputEnded = true
                    if let code = self.exitCode { self.finished(mode: mode, code: code) }
                } else { self.consume(bytes) }
            }
        }
        runner.terminationHandler = { [weak self] finished in
            Task { @MainActor [weak self] in
                guard let self, self.operationID == id else { return }
                self.exitCode = finished.terminationStatus
                if self.outputEnded { self.finished(mode: mode, code: finished.terminationStatus) }
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
            operationID = nil
            outputHandle = nil
            inputHandle = nil
            helperRunning = false
            fail("The local helper could not start. Reopen the app or reinstall the signed Mac companion.")
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
            needsStorageReview = true
            awaitHelperExit()
            fail("The collector returned too much data for review.")
            return
        }
        while let newline = outputBuffer.firstIndex(of: 10) {
            let line = Data(outputBuffer[..<newline])
            outputBuffer.removeSubrange(...newline)
            guard let event = try? decoder.decode(AgentEvent.self, from: line) else {
                process?.terminate()
                needsStorageReview = true
                awaitHelperExit()
                fail("The local helper returned an invalid response.")
                return
            }
            receive(event)
        }
    }

    func receive(_ event: AgentEvent) {
        if cancellationRequested && event.kind != "error" && event.kind != "state" { return }
        if cancellationRequested && event.kind == "state"
            && event.state != "cancelled" && event.state != "disconnected" { return }
        switch event.kind {
        case "state":
            switch event.state {
            case "connecting":
                collectionStartedAt = Date()
                progressIsKnown = false
                phase = .connecting
            case "password_required": phase = .passwordRequired
            case "processing": phase = .processing
            case "disconnected", "cancelled":
                declineLocalCollection()
                if handoffAcknowledged && !cancellationRequested { phase = .completing }
                else { clearPrivateReview(); resetHandoff(); phase = .idle }
            case "completed":
                if handoffAcknowledged { phase = .completing }
            case "pairing_revoked": resetHandoff()
            default: break
            }
        case "progress":
            if let value = event.value, value.isFinite, (0...100).contains(value) {
                progress = value
                progressIsKnown = true
                phase = value == 100 ? .processing : .transferring
            } else { progressIsKnown = false; phase = .transferring }
        case "connection":
            connectionTransport = event.transport == "wifi" ? "Wi-Fi" : "USB"
        case "capture":
            declineLocalCollection()
            contacts = event.contacts ?? []
            availableCalls = event.availableCalls ?? 0
            availableMessages = event.availableMessages ?? 0
            since = event.since ?? ""
            missingSources = event.missing ?? []
            selectionNeedsReview = true
            phase = .selecting
        case "review":
            pairCode = ""
            resetHandoff()
            selectionNeedsReview = false
            errorMessage = ""
            matchedCalls = event.matchedCalls ?? 0
            matchedMessages = event.matchedMessages ?? 0
            observedCallEarliest = event.observedCallEarliest ?? ""
            observedCallLatest = event.observedCallLatest ?? ""
            observedMessageEarliest = event.observedMessageEarliest ?? ""
            observedMessageLatest = event.observedMessageLatest ?? ""
            missingSources = event.missingSources ?? missingSources
            phase = .reviewing
        case "pairing":
            guard phase == .reviewing, !selectionNeedsReview else { return }
            pairingRequested = false
            errorMessage = ""
            pairCode = event.pairCode ?? ""
            pairPort = event.port ?? 0
            handoffID = event.handoffId.flatMap { UUID(uuidString: $0)?.uuidString.lowercased() } ?? ""
            pollHandoff()
        case "handoff":
            guard !handoffID.isEmpty, event.handoffId?.lowercased() == handoffID,
                  phase == .reviewing, !selectionNeedsReview else { return }
            if event.state == "received" { browserReceived = true }
            if event.state == "expired" {
                resetHandoff()
                errorMessage = "This browser pairing expired. Your local review remains available; create a new pairing when ready."
                return
            }
            if event.state == "saved" {
                handoffAcknowledged = true
                handoffPollTask?.cancel()
                handoffPollTask = nil
                phase = .completing
                if helperRunning { send(["action": "finish", "handoffId": handoffID]) }
            }
        case "residue":
            declineLocalCollection()
            residues = event.sessions ?? []
            needsStorageReview = false
        case "cleared":
            declineLocalCollection()
            residues = []
            needsStorageReview = false
            phase = .idle
        case "error":
            lastErrorCode = event.code
            if event.code == "selection" {
                errorMessage = "Choose supported contacts from this phone."
            } else if event.code == "bridge_unavailable" {
                errorMessage = "Local browser pairing could not start. Keep the review here or try again."
            } else if event.code == "handoff_unconfirmed" {
                handoffAcknowledged = false
                phase = .reviewing
                errorMessage = "The browser has not confirmed this account save. Your local review remains available."
            } else {
                if event.cleanupRequired == true {
                    needsStorageReview = true
                    clearPrivateReview()
                    awaitHelperExit()
                }
                let cleanup = event.cleanupRequired == true
                    ? " Temporary phone data may remain. Check temporary data before reconnecting." : ""
                fail(Self.message(for: event.code) + cleanup)
            }
        default:
            process?.terminate()
            needsStorageReview = true
            awaitHelperExit()
            fail("The local helper returned an unknown response.")
        }
    }

    func finished(mode: String, code: Int32) {
        declineLocalCollection()
        cancellationTask?.cancel()
        cancellationTask = nil
        outputHandle?.readabilityHandler = nil
        try? outputHandle?.close()
        outputHandle = nil
        operationID = nil
        process = nil
        try? inputHandle?.close()
        inputHandle = nil
        outputBuffer = Data()
        helperRunning = false
        handoffPollTask?.cancel()
        handoffPollTask = nil
        if mode == "connect" && code == 0 && handoffAcknowledged && !needsStorageReview {
            clearPrivateReview()
            phase = .completed
        }
        if mode == "connect" && code != 0 && phase != .error && phase != .idle {
            fail(code == 130 ? "Collection was cancelled." : "Collection stopped. Check the phone and try again.")
        }
        if mode == "connect" && needsStorageReview { inspectResidue() }
    }

    private func fail(_ message: String) {
        declineLocalCollection()
        errorMessage = message
        phase = .error
    }

    var statusTitle: String {
        switch phase {
        case .idle: return "Connect your iPhone"
        case .connecting: return "Unlock and trust your iPhone"
        case .transferring: return "Receiving the local backup"
        case .passwordRequired: return "Existing backup password needed"
        case .processing: return "Finishing local collection"
        case .selecting: return "Choose the people to review"
        case .reviewing: return browserReceived ? "Browser received the preview" : "Your local review is ready"
        case .completing: return "Account save acknowledged; finishing safely"
        case .completed: return "Saved in your account"
        case .cancelling: return "Stopping and checking temporary data"
        case .error: return "Collection stopped"
        }
    }

    var statusDetail: String {
        switch phase {
        case .idle, .connecting, .error:
            return "Connect one iPhone by USB, unlock it, and approve Apple's Trust prompt on the phone if shown. Existing trusted Wi-Fi pairing can be used only when no cable is present. No new wireless pairing or security bypass is performed."
        case .transferring:
            return "A full local backup can transfer unrelated bytes before they are discarded. Keep the phone connected. Productive transfer is not a stall; the guards are 15 minutes without backup bytes and four hours total."
        case .passwordRequired:
            return "Use the existing password for this iPhone's encrypted computer backup. It is not your phone passcode. This app does not enable, disable or reset encryption."
        case .processing:
            return "The helper is finishing metadata reading and temporary-backup cleanup. Backup progress does not mean your review or account save is complete."
        case .selecting:
            return "\(contacts.count) contacts, \(availableCalls) calls and \(availableMessages) messages were observed in available sources. Choose contacts, then review their matching metadata."
        case .reviewing:
            return browserReceived ? "Receiving a preview is not an account save. Keep this recovery copy until the browser explicitly acknowledges the matching saved run." : "Review the counts and observed dates. Creating a pairing only permits local transfer; cloud saving requires separate approval in your account."
        case .completing:
            return "The matching browser run acknowledged its account save. This window stays open until the owned helper finishes safely."
        case .completed:
            return "The matching save was acknowledged and the helper exited. Continue in the account browser to review your saved sources and results."
        case .cancelling:
            return "Wait for the owned helper to stop. If temporary phone data remains, inspect it here before reconnecting; cleanup never targets other backups."
        }
    }

    var recoverySteps: [String] {
        if needsStorageReview || !residues.isEmpty {
            return ["Choose Check temporary data, then review any marked app-created sessions.", "Remove only those sessions after explicit confirmation; do not delete other backups or change folder permissions."]
        }
        switch lastErrorCode {
        case "backup_password": return ["Check the existing encrypted computer-backup password with its owner.", "Reconnect and enter it only in this companion. Do not reset encryption to bypass this step."]
        case "workspace_low_space": return ["Free at least 2 GiB on the volume used by this app's temporary storage.", "Keep other backups intact, then reconnect."]
        case "workspace_size_limit": return ["This backup exceeds the 1 GiB retained-session guard; repeating the same backup will not remove that limit.", "Contact support for a supported collection path. No security or storage guard is bypassed."]
        case "workspace_unsafe": return ["Stop and contact support. Do not change permissions or delete an unverified folder."]
        case "workspace_unavailable", "workspace": return ["Reopen the signed companion and choose Check temporary data.", "If access still fails, reinstall the signed companion or contact support."]
        case "backup_stalled", "backup_time_limit", "connection_timeout", "phone_connection", "trust_required":
            return ["Connect one phone by USB, keep it unlocked, and approve Trust on the phone if shown.", "Check the cable and use the phone's normal controls. Reconnect after the helper has stopped."]
        default: return []
        }
    }

    static func message(for code: String?) -> String {
        switch code {
        case "phone_connection": return "Use one previously paired iPhone on this network, or connect it by USB, unlock it, and try again."
        case "trust_required": return "Unlock the iPhone and approve its Trust prompt, then try again."
        case "backup_password": return "The existing encrypted-backup password was not accepted. Check it and retry."
        case "unsupported_schema": return "This iPhone backup format is not supported safely yet. No metadata was saved."
        case "workspace_low_space": return "The Mac has less than 2 GiB free on the temporary-storage volume. Free space on that volume, then retry."
        case "workspace_size_limit": return "The retained temporary backup exceeded the 1 GiB safety limit. Collection stopped without saving metadata. This backup cannot be collected within the current limit."
        case "workspace_unsafe": return "The app's temporary-storage folder could not be verified as private and app-owned. Nothing there was changed. Contact support; do not change folder permissions or delete other backups."
        case "workspace_unavailable", "workspace": return "The app could not access its private temporary storage. Reopen the app and check temporary data. If it still fails, reinstall the signed companion or contact support."
        case "workspace_cleanup", "cleanup_incomplete": return "Temporary phone data could not be fully removed. Check temporary data before reconnecting."
        case "connection_timeout", "timeout": return "The iPhone connection timed out. Keep it unlocked, check the cable or trusted Wi-Fi connection, then retry."
        case "backup_stalled": return "No backup data arrived for 15 minutes. Collection stopped. Check the cable, unlock the iPhone, then reconnect."
        case "backup_time_limit": return "The backup reached the four-hour safety limit. Collection stopped without saving metadata. Try a wired connection; if it repeats, contact support."
        case "cancelled": return "Collection was cancelled. No metadata was saved."
        default: return "Collection stopped. No metadata was saved. Reconnect and try again."
        }
    }
}
