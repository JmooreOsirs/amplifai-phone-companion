import Foundation
import Combine
import CryptoKit

/// Production model/Process/agent/bridge/page/ACK integration. Only the capture and external
/// account transports are synthetic. The default 30-second poll and 65-second watchdog run unchanged.
@MainActor
enum AuthorityRefreshIntegrationChecks {
    enum Failure: Error { case check(String) }
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        guard condition() else { throw Failure.check(message) }
    }

    @MainActor final class Signals {
        var observers: [UUID: @MainActor () -> Void] = [:]
        func emit() { for observer in Array(observers.values) { observer() } }

        func until(_ description: String, seconds: Double = 15,
                   _ condition: @escaping @MainActor () -> Bool) async throws {
            if condition() { return }
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                let id = UUID()
                var completed = false
                var deadline: Task<Void, Never>?
                @MainActor func finish(_ error: Error?) {
                    guard !completed else { return }
                    completed = true
                    observers[id] = nil
                    deadline?.cancel()
                    if let error { continuation.resume(throwing: error) }
                    else { continuation.resume() }
                }
                observers[id] = { if condition() { finish(nil) } }
                deadline = Task { @MainActor in
                    do { try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000)) }
                    catch { return }
                    finish(Failure.check("Timed out: " + description))
                }
                observers[id]?()
            }
        }
    }

    @MainActor final class Fixture {
        let server = SimulatedPhoneRunServer()
        let model: CollectorModel
        let signals = Signals()
        var handoffEvents = 0
        var observation: AnyCancellable?
        let runID: String
        let statusPath: String
        var statusCount: Int { server.requests.filter { $0.path == statusPath }.count }
        init(admissionProtocol: PhoneRunAdmissionProtocol = .legacy) throws {
            statusPath = admissionProtocol == .preparation ? PhoneRunContract.preparationStatusPath : PhoneRunContract.statusPath
            model = CollectorModel(runAuthority: PhoneRunAuthorityClient(transport: server,
                                                                        admissionProtocol: admissionProtocol))
            let run = server.createRun()
            runID = run.runID
            try require(model.admitRunLink(PendingRunLink(link: PhoneRunLink(url: server.appLink(run))!,
                                                        returnBrowserBundleID: nil)), "Synthetic approval admitted")
            model.eventObserver = { [weak self] event in
                guard let self else { return }
                if event.kind == "handoff" { self.handoffEvents += 1 }
                self.signals.emit()
            }
            observation = model.objectWillChange.sink { [weak self] _ in
                Task { @MainActor in self?.signals.emit() }
            }
            server.replyObserver = { [weak self] path in
                guard let self else { return }
                self.signals.emit()
            }
        }
        func throughPipe() async throws {
            let observed = handoffEvents
            try await signals.until("next ordered helper handoff-status response", seconds: 6) {
                self.handoffEvents > observed
            }
        }
        func stop() async throws {
            model.disconnect()
            try await signals.until("owned helper cleanup", seconds: 20) { !self.model.helperRunning }
        }
    }

    struct Reply {
        let status: Int
        let data: Data
        var object: [String: Any] { (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:] }
    }
    static let intent = "11111111-1111-4111-8111-111111111111"
    static let capability = String(repeating: "A", count: 43)
    static let releaseCode = String(repeating: "B", count: 43)
    static let configuration: URLSessionConfiguration = {
        let value = URLSessionConfiguration.ephemeral
        value.httpCookieStorage = nil
        value.urlCredentialStorage = nil
        value.urlCache = nil
        value.timeoutIntervalForRequest = 5
        value.timeoutIntervalForResource = 10
        return value
    }()
    static let session = URLSession(configuration: configuration)

    static func request(_ method: String, _ path: String, token: String? = nil,
                        body: [String: Any]? = nil) async throws -> Reply {
        // Called only after our owned Process emitted a valid pairing with its generated handoff.
        var request = URLRequest(url: URL(string: "http://127.0.0.1:48751" + path)!)
        request.httpMethod = method
        request.setValue(CollectorModel.accountOrigin, forHTTPHeaderField: "Origin")
        if let token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.url == request.url else {
            throw Failure.check("Unexpected local bridge response")
        }
        return Reply(status: http.statusCode, data: data)
    }

    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func shellQuote(_ text: String) -> String { "'" + text.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    static func installHelper(resources: URL, python: String, collector: URL, small: Bool) throws {
        let helper = resources.appendingPathComponent("helper", isDirectory: true)
        try FileManager.default.createDirectory(at: helper, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let script = resources.appendingPathComponent("AuthorityRefreshHelper.py")
        if !FileManager.default.fileExists(atPath: script.path) {
            try FileManager.default.copyItem(at: collector.appendingPathComponent("macos/Tests/Fixtures/AuthorityRefreshHelper.py"), to: script)
        }
        let source = "#!/bin/sh\nexec " + shellQuote(python) + " " + shellQuote(script.path) + " " +
            shellQuote(resources.appendingPathComponent("refresh-data").path) + " \"$@\"" + (small ? " --small-capture" : "") + "\n"
        let executable = helper.appendingPathComponent("amplifai-agent")
        try Data(source.utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
    }

    static func run(python: String, collector: URL) async throws {
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        guard let resources = Bundle.main.resourceURL?.resolvingSymlinksInPath(),
              resources == executable.deletingLastPathComponent(),
              resources.path.hasPrefix(FileManager.default.temporaryDirectory.resolvingSymlinksInPath().path + "/") else {
            throw Failure.check("Real-helper integration requires the isolated check executable directory")
        }
        let module = resources.appendingPathComponent("collector")
        try FileManager.default.createSymbolicLink(at: module, withDestinationURL: collector)
        defer {
            try? FileManager.default.removeItem(at: module)
            try? FileManager.default.removeItem(at: resources.appendingPathComponent("AuthorityRefreshHelper.py"))
            try? FileManager.default.removeItem(at: resources.appendingPathComponent("refresh-data"))
            try? FileManager.default.removeItem(at: resources.appendingPathComponent("helper"))
        }
        try installHelper(resources: resources, python: python, collector: collector, small: false)
        let fixture = try Fixture(admissionProtocol: .preparation)
        do { try await fullTransfer(fixture) }
        catch { try? await fixture.stop(); throw error }
        try installHelper(resources: resources, python: python, collector: collector, small: true)
        try await terminalCallbacks()
        print("Preparation-mode real 30-second refresh integration passed: 2 periodic cycles, 65-second watchdog, 311149 rows/3114 pages, exact operation/retries, receipt gates; legacy terminal callbacks passed")
    }

    static func pair(_ fixture: Fixture, contacts: Int) async throws -> (String, [String: Any]) {
        let model = fixture.model
        try require(model.startCollection(), "Start uses the real synthetic-capture helper")
        try await fixture.signals.until("real capture", seconds: 90) { model.phase == .selecting || model.phase == .error }
        try require(model.phase == .selecting && model.totalContacts == contacts,
                    "Synthetic capture reached native review: \(model.errorMessage)")
        model.selectedIDs = Set(1...contacts)
        model.review()
        try await fixture.signals.until("real paged selection review", seconds: 60) { model.phase == .reviewing || model.phase == .error }
        try require(model.phase == .reviewing, "Production agent accepted the complete reviewed selection")
        model.pairBrowser()
        try await fixture.signals.until("real immutable handoff bound", seconds: 90) { model.handoffBound || !model.errorMessage.isEmpty }
        try require(model.handoffBound && model.pairPort == 48751, "Owned helper bound its fixed port: \(model.errorMessage)")
        fixture.server.intents[intent] = .init(email: "synthetic.owner@example.invalid", course: "SYNTHETIC course")
        let bound = model.phoneRun!
        let connect = try await request("POST", "/v3/connect", body: ["intentId": intent, "capability": capability,
            "handoffId": model.handoffID, "payloadSha256": bound.boundPayloadSha256!])
        try require(connect.status == 200, "Authenticated synthetic browser connect reached the production bridge")
        try await fixture.signals.until("native destination verification") { model.destinationApproved }
        let release = try await request("POST", "/v3/release", body: [
            "intentId": intent, "capability": capability, "releaseCode": releaseCode])
        guard release.status == 200, let token = release.object["token"] as? String,
              let raw = release.object["manifestJson"] as? String,
              let manifest = try JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any] else {
            throw Failure.check("Exact authorized release must return a paged manifest")
        }
        try require(digest(Data(raw.utf8)) == bound.boundPayloadSha256, "Manifest retains the bound payload digest")
        fixture.server.consume(fixture.runID)
        return (token, manifest)
    }

    static func fullTransfer(_ fixture: Fixture) async throws {
        let model = fixture.model
        try require(model.runStatusPollNanoseconds == 30_000_000_000,
                    "Integration must execute production timing unchanged")
        let (token, manifest) = try await pair(fixture, contacts: 5134)
        guard let sources = manifest["sources"] as? [String: [String: Any]] else { throw Failure.check("Missing sources") }
        let expected = ["contacts": (5134, 52), "calls": (3711, 38), "messages": (302304, 3024)]
        for (category, values) in expected {
            try require(sources[category]?["rowsSeen"] as? Int == values.0 &&
                        sources[category]?["rowsIncluded"] as? Int == values.0 &&
                        sources[category]?["pageCount"] as? Int == values.1,
                        "Observed and transported cardinality must be explicit for \(category)")
        }
        print("Volume: observed=5134 contacts/3711 calls/302304 messages; transported=311149 records in 3114 pages (52/38/3024)")
        let original = model.phoneRun!
        try require(original.admissionProtocol == .preparation && original.admittedOperation != nil &&
                    original.admittedOperation?.operationID == model.operationID?.uuidString.lowercased() &&
                    original.admittedOperation?.operationID == fixture.server.run(fixture.runID)?.operationID,
                    "Full transfer retains the exact preparation Start admitted by the server and launched by the helper")
        let selection = model.selectedIDs
        let binding: [String: Any] = ["handoffId": model.handoffID, "payloadSha256": original.boundPayloadSha256!]
        let premature = try await request("POST", "/v1/complete", token: token)
        let prematureSave = try await request("POST", "/v1/acknowledge-save", token: token,
                                            body: binding.merging(["saved": true]) { _, new in new })
        try require(premature.status == 409 && prematureSave.status == 409 && !model.handoffAcknowledged,
                    "No completion or save acknowledgement before all source pages")
        var rows = 0
        var pages = 0
        var chains = ["contacts": String(repeating: "0", count: 64), "calls": String(repeating: "0", count: 64),
                      "messages": String(repeating: "0", count: 64)]
        func readPage(_ category: String, _ cursor: Int) async throws -> Data {
            let response = try await request("GET", "/v2/source/\(category)?cursor=\(cursor)", token: token)
            guard response.status == 200, let chunk = response.object["chunk"] as? String,
                  let sha = response.object["pageSha256"] as? String,
                  let records = try JSONSerialization.jsonObject(with: Data(chunk.utf8)) as? [[String: Any]] else {
                throw Failure.check("Valid current page failed: \(category):\(cursor), HTTP \(response.status)")
            }
            try require(response.object["category"] as? String == category &&
                        response.object["cursor"] as? Int == cursor && digest(Data(chunk.utf8)) == sha,
                        "Page identity and payload SHA must match")
            chains[category] = digest(Data((chains[category]! + sha).utf8))
            rows += records.count
            pages += 1
            return response.data
        }
        for category in ["contacts", "calls"] {
            for cursor in 0..<expected[category]!.1 { _ = try await readPage(category, cursor) }
        }
        var cursor = 0
        var lastPage = Data()
        var periodicTimes: [Date] = []
        for cycle in 0..<2 {
            let end = cycle == 0 ? 100 : 200
            while cursor < end { lastPage = try await readPage("messages", cursor); cursor += 1 }
            let count = fixture.statusCount
            fixture.server.holdPaths = [fixture.statusPath]
            try await fixture.signals.until("production 30-second status tick \(cycle + 1)", seconds: 36) {
                fixture.server.heldReplyCount(for: fixture.statusPath) == 1
            }
            periodicTimes.append(Date())
            try require(String(describing: model.runStatus) == "refreshing", "Previously current exact authority enters refreshing")
            model.refreshRunStatus() // A second consumer joins; it cannot start another request or extend the deadline.
            try require(fixture.statusCount == count + 1, "Same-run concurrent consumers share one request")
            try await fixture.throughPipe()
            let path = "/v2/source/messages?cursor=\(cursor)"
            let denied = try await request("GET", path, token: token)
            try require(denied.status == 409 && Set(denied.object.keys) ==
                        ["code", "handoffId", "payloadSha256", "category", "cursor", "retryAfterMs"] &&
                        denied.object["code"] as? String == "phone_run_authority_refreshing" &&
                        denied.object["handoffId"] as? String == original.boundHandoffID &&
                        denied.object["payloadSha256"] as? String == original.boundPayloadSha256 &&
                        denied.object["category"] as? String == "messages" && denied.object["cursor"] as? Int == cursor &&
                        denied.object["retryAfterMs"] as? Int == 250,
                        "Refreshing denies records with only exact-page typed retry metadata")
            let skipped = try await request("GET", "/v2/source/messages?cursor=\(cursor + 1)", token: token)
            let badAuth = try await request("GET", path, token: "wrong")
            try require(skipped.status == 409 && skipped.object["code"] == nil && badAuth.status == 403,
                        "Refresh never hides a bad cursor or invalid credential")
            fixture.server.releaseHeldReplies(path: fixture.statusPath)
            try await fixture.throughPipe()
            let replay = try await request("GET", "/v2/source/messages?cursor=\(cursor - 1)", token: token)
            try require(replay.status == 200 && replay.data == lastPage, "Exact previous page replay is byte-identical after refresh")
            _ = try await readPage("messages", cursor)
            cursor += 1
            try require(model.phoneRun == original && model.selectedIDs == selection && model.runStatus == .current,
                        "Refresh preserves generation, admitted operation, handoff, selection and every approval field")
            print("Production periodic cycle \(cycle + 1): typed denial, unchanged cursor, exact replay, current recovery")
        }
        try require(periodicTimes[1].timeIntervalSince(periodicTimes[0]) >= 29,
                    "Two periodic checks must be separated by the real 30-second scheduler")

        // Hold a successful response beyond the independent watchdog. No shortened clock or mock timer.
        fixture.server.holdPaths = [fixture.statusPath]
        let watchdogStart = Date()
        model.refreshRunStatus()
        try await fixture.throughPipe()
        try await fixture.signals.until("independent 65-second model watchdog", seconds: 70) { model.runStatus == .uncertain }
        try require(Date().timeIntervalSince(watchdogStart) >= 64, "Production watchdog duration was exercised")
        try await fixture.throughPipe()
        let paused = try await request("GET", "/v2/source/messages?cursor=\(cursor)", token: token)
        try require(paused.status == 409 && paused.object["code"] == nil, "Expired refresh is terminal paused")
        fixture.server.releaseHeldReplies()
        try require(model.runStatus == .uncertain, "Late successful response cannot restore expired transient authority")
        model.refreshRunStatus()
        try await fixture.throughPipe()
        try require(model.runStatus == .current && model.phoneRun == original,
                    "A new exact preparation status check recovers the same operation and paused authority")
        while cursor < expected["messages"]!.1 { _ = try await readPage("messages", cursor); cursor += 1 }
        for category in expected.keys {
            try require(chains[category] == sources[category]?["chainSha256"] as? String,
                        "Every transmitted page contributes to the original \(category) chain")
        }
        try require(rows == 311149 && pages == 3114, "All raw records and all pages arrived exactly once in the assembled data")
        let complete = try await request("POST", "/v1/complete", token: token)
        let duplicate = try await request("POST", "/v1/complete", token: token)
        try require(complete.status == 200 && duplicate.status == 200, "Complete is exact-transfer idempotent")
        try await fixture.throughPipe()
        try require(model.browserReceived && !model.handoffAcknowledged && model.phase != .completed,
                    "Delivery alone never completes the account save")
        let save = try await request("POST", "/v1/acknowledge-save", token: token,
                                     body: binding.merging(["saved": true]) { _, new in new })
        try require(save.status == 200, "Synthetic account save acknowledgement accepted")
        try await fixture.throughPipe()
        try require(model.accountSaveAwaitingBrowserReceipt && model.phase != .completed && model.helperRunning &&
                    !model.canContinueToSignedInAccount,
                    "Native remains open and completed-report navigation unavailable until the final browser receipt")
        let receipt = try await request("POST", "/v1/confirm-ack-received", token: token,
                                        body: binding.merging(["received": true]) { _, new in new })
        try require(receipt.status == 200, "Final receipt reached the real bridge")
        try await fixture.signals.until("real helper finish after final receipt") { model.phase == .completed && !model.helperRunning }
        let expectedReport = CollectorModel.accountReportURL(enrollmentID: original.enrollmentID,
            handoffID: original.boundHandoffID!, payloadSha256: original.boundPayloadSha256!)
        try require(expectedReport != nil && model.completedReportReturnURL == expectedReport &&
                    model.phoneRun == nil && model.localCollectionApproval == nil,
                    "Clean real-helper completion retains only the exact report route after retiring run authority")
        let legacyPaths = [PhoneRunContract.claimPath, PhoneRunContract.statusPath, PhoneRunContract.bindPath]
        try require(!fixture.server.requests.contains { legacyPaths.contains($0.path) } &&
                    fixture.server.requests.filter { $0.path == PhoneRunContract.startPath }.count == 1,
                    "Preparation integration never falls back to legacy or admits a second operation")
    }

    static func terminalCallbacks() async throws {
        for terminal in ["stop", "revoked", "expired"] {
            let fixture = try Fixture()
            let model = fixture.model
            do {
                _ = try await pair(fixture, contacts: 1)
                fixture.server.holdPaths = [PhoneRunContract.statusPath]
                model.refreshRunStatus()
                if terminal == "stop" {
                    try await fixture.stop()
                } else if terminal == "revoked" {
                    // Retain a duplicate old successful transport callback, then revoke under a new check.
                    fixture.server.releaseHeldReplies(keepingForReplay: true)
                    fixture.server.revoke(fixture.runID)
                    model.refreshRunStatus()
                    try await fixture.signals.until("revoked helper stopped") { !model.helperRunning && model.runStatus == .revoked }
                } else {
                    let expiry = model.phoneRun!.claim.expiresAt
                    model.now = { expiry.addingTimeInterval(1) }
                    // An expired local grant is decisive even while a former current response is held.
                    model.refreshRunStatus()
                    try await fixture.signals.until("expired helper stopped") { !model.helperRunning && model.runStatus == .expired }
                }
                fixture.server.releaseHeldReplies()
                try require(model.phoneRun == nil && model.handoffID.isEmpty && !model.helperRunning,
                            "Late current callback after \(terminal) cannot restore a run or helper")
            } catch { try? await fixture.stop(); throw error }
        }
    }
}
