import Foundation
import SwiftUI
import Darwin
import CryptoKit

@MainActor
final class CollectorModel: ObservableObject {
    private let contactPageSize = 200
    private let reviewPageSize = 1000
    private let maxContactQueryLength = 120
    private static let maxHelperCommandBytes = 600_000
    static let collectionDisclosureVersion = "native-local-collection-v2"
    private static let localCollectionScopes = ["contacts", "call-context", "message-context", "temporary-full-backup", "sanitized-failure-diagnostic"]
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
    enum DiagnosticDelivery: Equatable { case idle, sending, sent, failed }

    @Published var phase: Phase = .idle
    @Published var progress = 0.0
    @Published private(set) var progressIsKnown = false
    @Published private(set) var transferBytes: Int64 = 0
    @Published private(set) var retainedBytes: Int64 = 0
    @Published private(set) var transferFileCount: Int64 = 0
    @Published private(set) var transferBytesPerSecond = 0.0
    @Published private(set) var processedBytes: Int64 = 0
    @Published private(set) var transferIsKnown = false
    @Published private(set) var collectionStartedAt: Date?
    @Published private(set) var lastBackupActivityAt: Date?
    @Published private(set) var lastParsingActivityAt: Date?
    @Published private(set) var transferElapsedSeconds = 0
    @Published private(set) var diagnosticReference = ""
    @Published private(set) var errorElapsedSeconds = 0
    @Published private(set) var handoffID = ""
    @Published private(set) var handoffAcknowledged = false
    @Published private(set) var browserReceived = false
    @Published private(set) var selectionNeedsReview = true
    @Published var password = ""
    @Published var contacts: [ContactPreview] = []
    @Published private(set) var totalContacts = 0
    @Published private(set) var currentContactCursor = 0
    @Published private(set) var nextContactCursor: Int?
    @Published private(set) var contactQuery = ""
    @Published private(set) var contactSearchPending = false
    @Published var selectedIDs: Set<Int> = []
    @Published private(set) var reviewPending = false
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
    @Published private(set) var diagnosticDelivery: DiagnosticDelivery = .idle

    private var process: Process?
    private var inputHandle: FileHandle?
    private var outputBuffer = Data()
    private(set) var operationID: UUID?
    private var outputHandle: FileHandle?
    private var outputEnded = false
    private var exitCode: Int32?
    private var cancellationRequested = false
    private let commandWriter: ((Data) -> Void)?
    private var cancellationTask: Task<Void, Never>?
    private var handoffPollTask: Task<Void, Never>?
    private var contactSearchTask: Task<Void, Never>?
    private var expectedContactCursor = 0
    private var contactPageStarts: [Int] = []
    private var pendingReviewID: String?
    private var pendingReviewIDs: Set<Int>?
    private var pendingReviewPageIDs: [Int] = []
    private var pendingReviewNextCursor = 0
    private var pendingReviewSHA: String?
    private var reviewedSelectionIDs: Set<Int>?
    private var pairingRequested = false
    private var lastErrorCode: String?
    private var lastErrorStage: String?
    private var lastDeviceStatus: Int64?
    private var attemptedDiagnosticCode: String?
    private var backupProgressSamples: [(date: Date, percent: Double)] = []
    private let cancellationGraceNanoseconds: UInt64 = 15_000_000_000
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    init(commandWriter: ((Data) -> Void)? = nil) {
        self.commandWriter = commandWriter
    }

    private var commandReady: Bool { helperRunning || commandWriter != nil }

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

    var hasUnsavedReview: Bool { totalContacts > 0 && !handoffAcknowledged }
    var isSearchingContacts: Bool { contactSearchPending && contacts.isEmpty }
    var hasPreviousContactPage: Bool { !contactPageStarts.isEmpty }
    var activeFlow: Bool { helperRunning || hasUnsavedReview }

    static func byteDescription(_ value: Int64) -> String {
        let bytes = Double(value)
        for (unit, size) in [("TiB", 1_099_511_627_776.0), ("GiB", 1_073_741_824.0), ("MiB", 1_048_576.0), ("KiB", 1_024.0)] {
            if bytes >= size { return String(format: "%.1f %@", bytes / size, unit) }
        }
        return "\(value) B"
    }

    var transferSummary: String {
        "\(Self.byteDescription(transferBytes)) transferred · \(Self.byteDescription(retainedBytes)) retained locally"
    }

    var lastCollectionActivityAt: Date? {
        phase == .processing ? lastParsingActivityAt ?? lastBackupActivityAt : lastBackupActivityAt
    }

    func backupRemainingEstimate(at now: Date) -> ClosedRange<Int>? {
        guard phase == .transferring, progressIsKnown, progress >= 15, progress < 90,
              backupProgressSamples.count >= 3, let first = backupProgressSamples.first,
              let last = backupProgressSamples.last,
              (0...60).contains(now.timeIntervalSince(last.date)) else { return nil }
        let span = last.date.timeIntervalSince(first.date)
        let gain = last.percent - first.percent
        guard span >= 120, gain >= 5, abs(last.percent - progress) < 0.1 else { return nil }
        let rates = zip(backupProgressSamples, backupProgressSamples.dropFirst()).compactMap { earlier, later -> Double? in
            let seconds = later.date.timeIntervalSince(earlier.date)
            let percentage = later.percent - earlier.percent
            return seconds >= 30 && percentage > 0 ? percentage / seconds : nil
        }
        guard rates.count >= 2, let slowest = rates.min(), let fastest = rates.max(),
              slowest > 0, fastest / slowest <= 2 else { return nil }
        let remaining = 100 - progress
        let lower = Int((remaining / fastest / 60 * 0.75).rounded(.down))
        let upper = Int((remaining / slowest / 60 * 1.5).rounded(.up))
        guard upper > 0, upper <= 720 else { return nil }
        return max(1, lower)...max(2, upper)
    }

    func observeBackupProgress(_ value: Double, at date: Date) {
        guard value.isFinite, (0..<100).contains(value) else { return }
        if let previous = backupProgressSamples.last {
            if value < previous.percent { backupProgressSamples = [] }
            else if value <= previous.percent || date.timeIntervalSince(previous.date) < 30 { return }
        }
        backupProgressSamples.append((date, value))
        if backupProgressSamples.count > 8 { backupProgressSamples.removeFirst() }
    }

    var failureContext: String {
        guard phase == .error, let stage = lastErrorStage else { return "" }
        let operation: String
        switch stage {
        case "backup": operation = "backup transfer"
        case "processing": operation = "local metadata reading"
        case "review": operation = "local review or handoff"
        default: operation = "iPhone connection"
        }
        let transfer = transferIsKnown
            ? " Last observed: \(transferSummary). These are partial, temporary backup bytes—not an account save."
            : " No transfer bytes were observed by the companion."
        return "Stopped during \(operation).\(transfer)"
    }

    var safeSupportCode: String {
        guard phase == .error, !diagnosticReference.isEmpty else { return "" }
        let stage = lastErrorStage ?? "connecting"
        let code = lastErrorCode.flatMap { SupportDiagnosticReporter.reportCodes.contains($0) ? $0 : nil } ?? "collection_failed"
        let build = (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String)
            .flatMap { $0.range(of: #"^[0-9]{8}$"#, options: .regularExpression) != nil ? $0 : nil } ?? "26100808"
        return "A1|\(build)|\(diagnosticReference)|\(stage)|\(code)|\(errorElapsedSeconds)|\(transferBytes)|\(retainedBytes)|\(lastDeviceStatus.map(String.init) ?? "-")"
    }

    func reportDiagnosticIfNeeded(forceRetry: Bool = false) async {
        let code = safeSupportCode
        guard phase == .error, !code.isEmpty, diagnosticDelivery != .sending,
              forceRetry || attemptedDiagnosticCode != code else { return }
        attemptedDiagnosticCode = code
        diagnosticDelivery = .sending
        let delivered = await SupportDiagnosticReporter.send(code)
        guard phase == .error, safeSupportCode == code else { return }
        diagnosticDelivery = delivered ? .sent : .failed
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
        attemptedDiagnosticCode = nil
        diagnosticDelivery = .idle
        diagnosticReference = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8))
        transferElapsedSeconds = 0
        errorElapsedSeconds = 0
        lastDeviceStatus = nil
        contacts = []
        totalContacts = 0
        currentContactCursor = 0
        nextContactCursor = nil
        contactQuery = ""
        contactSearchPending = false
        contactSearchTask?.cancel()
        contactSearchTask = nil
        expectedContactCursor = 0
        contactPageStarts = []
        lastErrorCode = nil
        lastErrorStage = nil
        selectedIDs = []
        clearPendingReview()
        reviewedSelectionIDs = nil
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
        transferBytes = 0
        retainedBytes = 0
        transferFileCount = 0
        transferBytesPerSecond = 0
        processedBytes = 0
        transferIsKnown = false
        lastBackupActivityAt = nil
        lastParsingActivityAt = nil
        backupProgressSamples = []
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
        guard !reviewPending else { return }
        reviewedSelectionIDs = nil
        if pairingRequested || !pairCode.isEmpty {
            send(["action": "revoke"])
            pairCode = ""
        }
        resetHandoff()
        selectionNeedsReview = true
        phase = .selecting
        if selectedIDs.contains(id) {
            selectedIDs.remove(id)
        } else {
            selectedIDs.insert(id)
        }
    }

    func searchContacts(_ query: String) {
        guard phase == .selecting || phase == .reviewing, commandReady,
              !reviewPending,
              query.unicodeScalars.count <= maxContactQueryLength else { return }
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed != contactQuery else { return }
        contactSearchTask?.cancel()
        contactQuery = trimmed
        contacts = []
        currentContactCursor = 0
        nextContactCursor = nil
        expectedContactCursor = 0
        contactPageStarts = []
        contactSearchPending = true
        contactSearchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled else { return }
            self?.send(["action": "contacts", "query": trimmed, "cursor": 0])
        }
    }

    func loadMoreContacts() {
        guard (phase == .selecting || phase == .reviewing), commandReady,
              !reviewPending, !contactSearchPending,
              let cursor = nextContactCursor else { return }
        contactPageStarts.append(currentContactCursor)
        expectedContactCursor = cursor
        contactSearchPending = true
        send(["action": "contacts", "query": contactQuery, "cursor": cursor])
    }

    func previousContacts() {
        guard (phase == .selecting || phase == .reviewing), commandReady,
              !reviewPending, !contactSearchPending,
              let cursor = contactPageStarts.popLast() else { return }
        expectedContactCursor = cursor
        contactSearchPending = true
        send(["action": "contacts", "query": contactQuery, "cursor": cursor])
    }

    func review() {
        guard phase == .selecting || phase == .reviewing else { return }
        guard commandReady, !reviewPending, !contactSearchPending,
              !selectedIDs.isEmpty else { return }
        let reviewID = UUID().uuidString.lowercased()
        pendingReviewID = reviewID
        pendingReviewIDs = selectedIDs
        pendingReviewPageIDs = selectedIDs.sorted()
        pendingReviewNextCursor = 0
        pendingReviewSHA = Self.selectionDigest(pendingReviewPageIDs)
        reviewedSelectionIDs = nil
        reviewPending = true
        selectionNeedsReview = true
        phase = .selecting
        resetHandoff()
        sendReviewPage()
    }

    static func selectionDigest(_ ids: [Int]) -> String {
        var digest = SHA256()
        for id in ids {
            digest.update(data: Data("\(id)\n".utf8))
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func sendReviewPage() {
        guard let reviewID = pendingReviewID,
              pendingReviewNextCursor < pendingReviewPageIDs.count else { return }
        let start = pendingReviewNextCursor
        let end = min(start + reviewPageSize, pendingReviewPageIDs.count)
        pendingReviewNextCursor = end
        send(["action": "review-page", "reviewId": reviewID,
              "cursor": start, "total": pendingReviewPageIDs.count,
              "ids": Array(pendingReviewPageIDs[start..<end])])
    }

    private func clearPendingReview() {
        reviewPending = false
        pendingReviewID = nil
        pendingReviewIDs = nil
        pendingReviewPageIDs = []
        pendingReviewNextCursor = 0
        pendingReviewSHA = nil
    }

    func pairBrowser() {
        guard phase == .reviewing, !selectedIDs.isEmpty,
              !reviewPending, !selectionNeedsReview,
              reviewedSelectionIDs == selectedIDs else { return }
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
        totalContacts = 0
        currentContactCursor = 0
        nextContactCursor = nil
        contactQuery = ""
        contactSearchPending = false
        contactSearchTask?.cancel()
        contactSearchTask = nil
        expectedContactCursor = 0
        contactPageStarts = []
        selectedIDs = []
        clearPendingReview()
        reviewedSelectionIDs = nil
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

    static func encodedHelperCommand(_ command: [String: Any]) -> Data? {
        guard let data = try? JSONSerialization.data(withJSONObject: command),
              data.count + 1 <= maxHelperCommandBytes else { return nil }
        return data + Data([10])
    }

    private func send(_ command: [String: Any]) {
        guard let data = Self.encodedHelperCommand(command) else {
            fail("The local connection is unavailable.")
            return
        }
        if let commandWriter {
            commandWriter(data)
            return
        }
        guard let inputHandle else {
            fail("The local connection is unavailable.")
            return
        }
        do {
            try inputHandle.write(contentsOf: data)
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

    private func validContactPage(_ page: [ContactPreview], total: Int, cursor: Int,
                                  next: Int?, query: String) -> Bool {
        guard (0...1_000_000).contains(total), (0...total).contains(cursor),
              page.count <= contactPageSize, query.unicodeScalars.count <= maxContactQueryLength,
              Set(page.map(\.id)).count == page.count,
              page.allSatisfy({ contact in
                  contact.id >= 0 && contact.name.unicodeScalars.count <= 240 &&
                  (0...20).contains(contact.phoneCount) &&
                  contact.phoneEnds.count == contact.phoneCount &&
                  contact.phoneEnds.allSatisfy({ $0.count == 4 && $0.allSatisfy(\.isNumber) })
              }) else { return false }
        if let next {
            guard next >= cursor + page.count, next <= total,
                  page.count == contactPageSize else { return false }
            if query.isEmpty && next != cursor + page.count { return false }
        } else if query.isEmpty && cursor + page.count != total { return false }
        return true
    }

    private func rejectContactPage() {
        process?.terminate()
        needsStorageReview = true
        awaitHelperExit()
        fail("The local helper returned an incomplete contact page. No metadata was saved. Check temporary data before reconnecting.")
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
                observeBackupProgress(value, at: Date())
                progress = value
                progressIsKnown = true
                phase = value == 100 ? .processing : .transferring
            } else { progressIsKnown = false; phase = .transferring }
        case "transfer":
            guard phase == .connecting || phase == .transferring || phase == .processing else { return }
            if event.stage == "backup" {
                guard let received = event.receivedBytes, let retained = event.retainedBytes,
                      let discarded = event.discardedBytes, let files = event.filesReceived,
                      let rate = event.bytesPerSecond, let elapsed = event.elapsedSeconds,
                      received >= transferBytes, received <= 9_007_199_254_740_991,
                      retained >= retainedBytes, retained <= received, discarded >= 0,
                      discarded == received - retained, files >= transferFileCount,
                      rate.isFinite, rate >= 0, rate <= 1_099_511_627_776,
                      elapsed.isFinite, elapsed > 0 else { return }
                if received > transferBytes || files > transferFileCount { lastBackupActivityAt = Date() }
                transferBytes = received
                retainedBytes = retained
                transferFileCount = files
                transferBytesPerSecond = rate
                if elapsed <= 172_800 { transferElapsedSeconds = Int(elapsed) }
                transferIsKnown = true
                phase = .transferring
            } else if event.stage == "processing", let bytes = event.processedBytes,
                      bytes >= processedBytes, bytes <= 9_007_199_254_740_991 {
                if bytes > processedBytes { lastParsingActivityAt = Date() }
                processedBytes = bytes
                phase = .processing
            }
        case "connection":
            connectionTransport = event.transport == "wifi" ? "Wi-Fi" : "USB"
        case "capture":
            let firstPage = event.contacts ?? []
            let total = event.totalContacts ?? firstPage.count
            guard (event.query ?? "").isEmpty, (event.cursor ?? 0) == 0,
                  validContactPage(firstPage, total: total, cursor: 0,
                                   next: event.nextCursor, query: "") else {
                rejectContactPage()
                return
            }
            declineLocalCollection()
            contacts = firstPage
            totalContacts = total
            currentContactCursor = 0
            nextContactCursor = event.nextCursor
            contactQuery = ""
            contactSearchPending = false
            expectedContactCursor = event.nextCursor ?? 0
            contactPageStarts = []
            availableCalls = event.availableCalls ?? 0
            availableMessages = event.availableMessages ?? 0
            since = event.since ?? ""
            missingSources = event.missing ?? []
            selectionNeedsReview = true
            phase = .selecting
        case "contacts":
            guard phase == .selecting || phase == .reviewing else { return }
            guard event.query == contactQuery, event.cursor == expectedContactCursor else { return }
            guard let page = event.contacts, event.totalContacts == totalContacts,
                  validContactPage(page, total: totalContacts, cursor: expectedContactCursor,
                                   next: event.nextCursor, query: contactQuery) else {
                rejectContactPage()
                return
            }
            contacts = page
            currentContactCursor = expectedContactCursor
            nextContactCursor = event.nextCursor
            contactSearchPending = false
            expectedContactCursor = event.nextCursor ?? 0
        case "review-page":
            guard reviewPending, event.reviewId == pendingReviewID,
                  event.nextCursor == pendingReviewNextCursor else { return }
            sendReviewPage()
        case "review":
            guard let pendingReviewID, let pendingReviewIDs else { return }
            guard let eventReviewID = event.reviewId else {
                clearPendingReview()
                reviewedSelectionIDs = nil
                selectionNeedsReview = true
                phase = .selecting
                errorMessage = "The local helper did not confirm this selection. Reinstall the matching signed companion before retrying review."
                return
            }
            guard eventReviewID == pendingReviewID else { return }
            let expectedSHA = pendingReviewSHA
            let expectedCount = pendingReviewPageIDs.count
            clearPendingReview()
            guard selectedIDs == pendingReviewIDs,
                  event.selectedContacts == pendingReviewIDs.count,
                  event.selectedContacts == expectedCount,
                  event.selectionSha256 == expectedSHA else {
                reviewedSelectionIDs = nil
                selectionNeedsReview = true
                phase = .selecting
                errorMessage = "The selected contacts changed during review. Review them again before pairing."
                return
            }
            reviewedSelectionIDs = pendingReviewIDs
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
            guard phase == .reviewing, !reviewPending, !selectionNeedsReview,
                  reviewedSelectionIDs == selectedIDs else { return }
            pairingRequested = false
            errorMessage = ""
            pairCode = event.pairCode ?? ""
            pairPort = event.port ?? 0
            handoffID = event.handoffId.flatMap { UUID(uuidString: $0)?.uuidString.lowercased() } ?? ""
            pollHandoff()
        case "handoff":
            guard !handoffID.isEmpty, event.handoffId?.lowercased() == handoffID,
                  phase == .reviewing, !reviewPending, !selectionNeedsReview,
                  reviewedSelectionIDs == selectedIDs else { return }
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
            // A later cleanup/error frame must not replace the first terminal collection cause.
            if phase == .error && lastErrorCode != nil { return }
            lastErrorCode = event.code
            lastErrorStage = ["connecting", "backup", "processing", "review"].contains(event.stage ?? "") ? event.stage : nil
            lastDeviceStatus = event.code == "device_backup_failed" && event.deviceCode.map({ (-2_147_483_648...4_294_967_295).contains($0) }) == true ? event.deviceCode : nil
            if event.code == "selection" {
                clearPendingReview()
                reviewedSelectionIDs = nil
                selectionNeedsReview = true
                phase = .selecting
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
                let deviceStatus: String
                if event.code == "device_backup_failed", let code = event.deviceCode,
                   (-2_147_483_648...4_294_967_295).contains(code) {
                    deviceStatus = " Device status: \(code)."
                } else {
                    deviceStatus = ""
                }
                fail(Self.message(for: event.code) + deviceStatus + cleanup)
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
        contactSearchTask?.cancel()
        contactSearchTask = nil
        contactSearchPending = false
        clearPendingReview()
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
        if mode == "connect" && code == 0 && !handoffAcknowledged && phase != .error && phase != .idle {
            fail("The local helper stopped before this account save was confirmed. Keep this review and reconnect before trying again.")
        }
        if mode == "connect" && needsStorageReview { inspectResidue() }
    }

    private func fail(_ message: String) {
        declineLocalCollection()
        clearPendingReview()
        reviewedSelectionIDs = nil
        if diagnosticReference.isEmpty { diagnosticReference = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8)) }
        let wallElapsed = collectionStartedAt.map { Int(max(0, min(172_800, Date().timeIntervalSince($0)))) } ?? 0
        errorElapsedSeconds = max(transferElapsedSeconds, wallElapsed)
        if lastErrorStage == nil {
            switch phase {
            case .transferring: lastErrorStage = "backup"
            case .processing, .completing: lastErrorStage = "processing"
            case .selecting, .reviewing: lastErrorStage = "review"
            default: lastErrorStage = "connecting"
            }
        }
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
        case .idle, .connecting:
            return "Connect one iPhone by USB and unlock it to establish the connection. Respond to Apple's Trust/passcode prompt on the phone if shown. Existing trusted Wi-Fi pairing is used only when no cable is present. A later screen lock alone does not prove a failure; no security bypass is performed."
        case .error:
            return "This collection did not produce a verified completion. Review the failure and last observed transfer below before starting a new approved attempt."
        case .transferring:
            return "Unrelated full-backup bytes are streamed and discarded; selected source databases stay local and temporary. Keep the cable connected and respond to any phone prompts. Data-aware guards allow larger active transfers; 15 minutes without any device response or sustained very slow progress can stop the session."
        case .passwordRequired:
            return "Use the existing password for this iPhone's encrypted computer backup. It is not your phone passcode. This app does not enable, disable or reset encryption."
        case .processing:
            return "The helper is finishing metadata reading and temporary-backup cleanup. Backup progress does not mean your review or account save is complete."
        case .selecting:
            return "\(contacts.count) contacts, \(availableCalls) calls and \(availableMessages) messages were observed in available sources. Choose contacts, then review their matching metadata."
        case .reviewing:
            return browserReceived ? "A preview alone is not an account save. If you saved in the account, keep this review open until the browser confirms the matching saved receipt." : "Review the counts and observed dates. Creating a pairing only permits local transfer; cloud saving requires separate approval in your account."
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
        case "workspace_low_space": return ["Free space for retained source databases, their parsing copies and a further 2 GiB reserve on the temporary-storage volume.", "Keep other backups intact, then reconnect."]
        case "backup_host_space": return ["The iPhone refused the host-space answer before a verified backup completion. Check available space on the Mac's temporary-storage volume; do not delete other backups just to satisfy this app.", "If a normal Finder backup works on this Mac but collection repeats this code, share the safe reference with support before another long retry."]
        case "workspace_size_limit": return ["This helper reported an older backup-size limit. Update the signed companion before retrying."]
        case "source_capacity_limit", "source_read_capacity_limit", "contacts_capacity_limit", "backup_control_frame_limit", "backup_control_metadata_limit", "backup_control_path_limit":
            return ["A bounded control or source-reading safety limit was reached, not the former backup-size ceiling.", "Share the safe support code before another long run; do not reset phone encryption or remove other backups."]
        case "backup_control_invalid", "selected_payload_missing", "selected_payload_invalid", "contacts_schema", "unsupported_schema":
            return ["Share the safe support code and companion build with support before repeating a long collection.", "Do not reset backup encryption, remove other backups or treat the partial transfer as saved data."]
        case "workspace_unsafe": return ["Stop and contact support. Do not change permissions or delete an unverified folder."]
        case "workspace_unavailable", "workspace": return ["Reopen the signed companion and choose Check temporary data.", "If access still fails, reinstall the signed companion or contact support."]
        case "backup_stalled", "backup_time_limit", "backup_no_file_progress", "connection_timeout", "phone_connection", "trust_required":
            return ["Connect one phone by USB, keep it unlocked, and approve Trust on the phone if shown.", "Check the cable and use the phone's normal controls. Reconnect after the helper has stopped."]
        case "connection_lost": return ["Check the direct USB cable and port and respond to any iPhone Trust prompt.", "After the helper stops, compare a normal Finder backup on the same Mac. If Finder succeeds but this fails again, share the companion version and last transfer details with support."]
        case "device_backup_failed": return ["Note the device status and companion version.", "If the same iPhone cannot complete a normal Finder backup on this Mac, resolve that device backup issue first. Otherwise, share the status with support before repeating collection."]
        case "collection_failed": return ["Note the companion version and last observed transfer stage.", "Contact support before repeating the same long collection; the cause was not classified."]
        default: return []
        }
    }

    static func message(for code: String?) -> String {
        switch code {
        case "phone_connection": return "Use one previously paired iPhone on this network, or connect it by USB, unlock it, and try again."
        case "trust_required": return "Unlock the iPhone and approve its Trust prompt, then try again."
        case "backup_password": return "The existing encrypted-backup password was not accepted. Check it and retry."
        case "unsupported_schema": return "This iPhone backup format is not supported safely yet. No metadata was saved."
        case "backup_control_invalid": return "Required iPhone backup control metadata was missing or invalid. No metadata was saved."
        case "selected_payload_missing": return "A required selected source listed in the iPhone backup was unavailable. No metadata was saved."
        case "selected_payload_invalid": return "A selected iPhone source failed a size, type or decryption-integrity check. No metadata was saved."
        case "contacts_schema": return "This iPhone's contact database could not be interpreted safely. No metadata was saved."
        case "workspace_low_space": return "There is not enough free space for the next write, local parsing copies and the 2 GiB reserve. Free space on the temporary-storage volume, then retry."
        case "backup_host_space": return "The iPhone refused the backup receiver's space preflight. No metadata was saved. Check available Mac space and share the safe reference if it repeats."
        case "workspace_size_limit": return "This helper reported an older backup-size limit. Update the signed companion before retrying."
        case "source_capacity_limit": return "The backup control data or metadata count exceeded a supported safety bound. Collection stopped without uploading metadata. Contact support with the companion version."
        case "backup_control_frame_limit": return "An iPhone backup control frame exceeded the companion's safe parsing bound. No metadata was saved. Share the safe support code before another long run."
        case "backup_control_metadata_limit": return "A backup control file exceeded the companion's safe parsing bound. No metadata was saved. Share the safe support code before another long run."
        case "backup_control_path_limit": return "An iPhone backup path exceeded the companion's safe path bound. No metadata was saved. Share the safe support code before another long run."
        case "contacts_capacity_limit": return "The required contact source exceeded a safe local reader bound. No metadata was saved. Share the safe support code before another long run."
        case "source_read_capacity_limit": return "A phone source exceeded a safe local reader bound. No metadata was saved. Share the safe support code before another long run."
        case "workspace_unsafe": return "The app's temporary-storage folder could not be verified as private and app-owned. Nothing there was changed. Contact support; do not change folder permissions or delete other backups."
        case "workspace_unavailable", "workspace": return "The app could not access its private temporary storage. Reopen the app and check temporary data. If it still fails, reinstall the signed companion or contact support."
        case "workspace_cleanup", "cleanup_incomplete": return "Temporary phone data could not be fully removed. Check temporary data before reconnecting."
        case "connection_timeout", "timeout": return "The iPhone connection timed out. Keep it unlocked, check the cable or trusted Wi-Fi connection, then retry."
        case "connection_lost": return "The iPhone connection ended before backup completion. No metadata was saved. Check the cable and port, then reconnect after the helper stops."
        case "device_backup_failed": return "The iPhone reported a backup failure. No metadata was saved."
        case "backup_stalled": return "No backup data arrived for 15 minutes. Collection stopped. Check the cable, unlock the iPhone, then reconnect."
        case "backup_no_file_progress": return "The iPhone kept responding but no backup file data arrived for one hour. Collection stopped without saving metadata. Check the phone's normal backup status before retrying."
        case "backup_time_limit": return "The backup made too little file-data progress within its data-aware time budget. Collection stopped. Use a direct USB cable and retry; if it repeats, contact support."
        case "cancelled": return "Collection was cancelled. No metadata was saved."
        default: return "Collection stopped without a verified completion receipt. The cause was not classified; note the stage below before retrying."
        }
    }
}
