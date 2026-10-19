import CryptoKit
import Foundation

/// FAKE DATA helper for the demo bundle. It speaks the companion's stdin/stdout JSON protocol with
/// fictional data and a simulated browser. It opens no device, socket, file for writing or URL.
@main
struct FakeHelper {
    private static let fast = FileManager.default.fileExists(atPath: URL(fileURLWithPath: CommandLine.arguments[0])
        .deletingLastPathComponent().appendingPathComponent("FAST_DEMO_TIMING").path)

    /// The demo bundle's folder; the simulated website writes one file per website step and link there.
    private static let demoControl = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("demo-control")

    private static func websiteDid(_ step: String, _ handoffID: String) -> Bool {
        fast || FileManager.default.fileExists(atPath: demoControl.appendingPathComponent("\(step)-\(handoffID)").path)
    }

    private static func emit(_ object: [String: Any]) {
        guard var data = try? JSONSerialization.data(withJSONObject: object) else { return }
        data.append(10)
        FileHandle.standardOutput.write(data)
    }

    private static func pause(_ seconds: Double) {
        Thread.sleep(forTimeInterval: fast ? min(seconds, 0.05) : seconds)
    }

    static func main() {
        signal(SIGTERM) { _ in
            let message: StaticString = "{\"kind\":\"state\",\"state\":\"cancelled\"}\n"
            _ = write(1, message.utf8Start, message.utf8CodeUnitCount)
            _exit(130)
        }
        switch CommandLine.arguments.dropFirst().first ?? "" {
        case "inspect": emit(["kind": "residue", "sessions": []])
        case "clear-residue": emit(["kind": "cleared"])
        case "backup-encryption-status":
            // Simulated iPhone: no device is contacted. Encryption Off keeps the demo free of password entry.
            Thread.sleep(forTimeInterval: fast ? 0.6 : 1.0)
            emit(["kind": "backup-encryption-status", "backupEncrypted": false])
        case "connect": connect()
        default:
            emit(["kind": "error", "code": "collection_failed"])
            exit(1)
        }
    }

    private static func connect() {
        emit(["kind": "state", "state": "connecting"])
        pause(0.8)
        emit(["kind": "connection", "transport": "usb"])
        var received: Int64 = 0
        var retained: Int64 = 0
        for step in 1...10 {
            received += 268_435_456
            retained += 12_582_912
            emit(["kind": "progress", "value": Double(step * 9)])
            emit(["kind": "transfer", "stage": "backup", "receivedBytes": received, "retainedBytes": retained,
                  "discardedBytes": received - retained, "filesReceived": step * 140,
                  "bytesPerSecond": 268_435_456.0 / 0.45, "elapsedSeconds": Double(step) * 0.45])
            pause(0.45)
        }
        emit(["kind": "progress", "value": 100.0])
        emit(["kind": "transfer", "stage": "processing", "processedBytes": retained])
        pause(0.8)
        let all = FakeDataSet.contacts
        emit(["kind": "capture", "query": "", "cursor": 0, "totalContacts": all.count, "nextCursor": 200,
              "contacts": all.prefix(200).map(FakeDataSet.preview),
              "availableCalls": FakeDataSet.availableCalls, "availableMessages": FakeDataSet.availableMessages,
              "since": "2025-10-19", "missing": [], "backupEncrypted": false, "unavailableReasons": [:]])
        commands()
    }

    /// Simulated browser stages follow the real bridge order (FakeBridgeTimeline) and advance at most one
    /// website step per handoff-status poll, so the app can never see a phase early.
    private static func commands() {
        let all = FakeDataSet.contacts
        var reviewIDs: [Int] = []
        var handoffID = ""
        var authorityCurrent = false
        var timeline = FakeBridgeTimeline()
        // Monotonic, like bridge.py's time.monotonic.
        func now() -> TimeInterval { ProcessInfo.processInfo.systemUptime }
        while let line = readLine(strippingNewline: true) {
            guard let data = line.data(using: .utf8),
                  let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let action = command["action"] as? String else { continue }
            switch action {
            case "contacts":
                let query = (command["query"] as? String ?? "").trimmingCharacters(in: .whitespaces)
                let cursor = command["cursor"] as? Int ?? 0
                let matches = query.isEmpty ? all : all.filter { $0.name.localizedCaseInsensitiveContains(query) }
                let end = min(cursor + 200, matches.count)
                var event: [String: Any] = ["kind": "contacts", "query": query, "cursor": cursor, "totalContacts": all.count,
                                            "nextCursor": end < matches.count ? end : NSNull(),
                                            "contacts": cursor < end ? matches[cursor..<end].map(FakeDataSet.preview) : []]
                if let scanID = command["scanId"] as? String { event["scanId"] = scanID }
                emit(event)
            case "contact-ids":
                let ids = command["ids"] as? [Int] ?? []
                emit(["kind": "contact-ids", "requestId": command["requestId"] as? String ?? "", "totalContacts": all.count,
                      "contacts": ids.compactMap { id in all.first { $0.id == id }.map(FakeDataSet.preview) }])
            case "review-page":
                let cursor = command["cursor"] as? Int ?? 0
                let total = command["total"] as? Int ?? 0
                let ids = command["ids"] as? [Int] ?? []
                let reviewID = command["reviewId"] as? String ?? ""
                if cursor == 0 { reviewIDs = [] }
                reviewIDs += ids
                emit(["kind": "review-page", "reviewId": reviewID, "nextCursor": cursor + ids.count])
                if cursor + ids.count >= total {
                    var digest = SHA256()
                    for id in reviewIDs { digest.update(data: Data("\(id)\n".utf8)) }
                    let report = FakeDataSet.sampleReport(selected: Set(reviewIDs))
                    let active = (report?.calls ?? 0) + (report?.messages ?? 0) > 0
                    emit(["kind": "review", "reviewId": reviewID, "selectedContacts": reviewIDs.count,
                          "selectionSha256": digest.finalize().map { String(format: "%02x", $0) }.joined(),
                          "matchedCalls": report?.calls ?? 0, "matchedMessages": report?.messages ?? 0,
                          "observedCallEarliest": active ? FakeDataSet.observedEarliest : "",
                          "observedCallLatest": active ? FakeDataSet.observedLatest : "",
                          "observedMessageEarliest": active ? FakeDataSet.observedEarliest : "",
                          "observedMessageLatest": active ? FakeDataSet.observedLatest : "",
                          "missingSources": []])
                }
            case "pair":
                handoffID = UUID().uuidString.lowercased()
                authorityCurrent = false
                // The five-minute window starts here, at pair after review, never at the claim or collection.
                timeline = FakeBridgeTimeline(pairedAt: now())
                emit(["kind": "pairing", "pairCode": String((0..<10).map { _ in "0123456789".randomElement()! }),
                      "port": 48751, "expiresInSeconds": 300, "handoffId": handoffID,
                      "payloadSha256": FakeBridgeTimeline.payloadDigest(reviewIDs)])
            case "run-authority":
                guard command["handoffId"] as? String == handoffID,
                      let state = command["state"] as? String, ["paused", "current"].contains(state) else { continue }
                authorityCurrent = state == "current"
            case "handoff-status":
                guard command["handoffId"] as? String == handoffID else {
                    emit(["kind": "error", "code": "handoff_unconfirmed"])
                    continue
                }
                timeline.expireIfDue(at: now())
                if let next = timeline.nextWebsiteAction, authorityCurrent, websiteDid(next.rawValue, handoffID) {
                    timeline.apply(next, at: now())
                }
                emit(["kind": "handoff", "state": timeline.handoffState, "handoffId": handoffID])
                if timeline.destinationPending {
                    emit(["kind": "destination", "state": "pending", "handoffId": handoffID,
                          "intentId": FakeBridgeTimeline.intentID(forHandoff: handoffID, attempt: timeline.connectAttempt),
                          "email": FakeDataSet.email, "course": FakeDataSet.course])
                }
            case "destination-decision":
                let intent = FakeBridgeTimeline.intentID(forHandoff: handoffID, attempt: timeline.connectAttempt)
                guard authorityCurrent, command["handoffId"] as? String == handoffID, command["intentId"] as? String == intent,
                      let approved = command["approved"] as? Bool, timeline.decide(approved: approved, at: now()) else {
                    emit(["kind": "error", "code": "handoff_unconfirmed"])
                    continue
                }
                emit(["kind": "destination", "state": approved ? "approved" : "denied", "handoffId": handoffID, "intentId": intent])
            case "revoke":
                // Like close_for_repair(): a receipted handoff is kept, anything earlier is closed.
                if ["saved", "saved_pending_browser_receipt"].contains(timeline.handoffState) {
                    emit(["kind": "handoff", "state": timeline.handoffState, "handoffId": handoffID])
                    continue
                }
                handoffID = ""
                timeline = FakeBridgeTimeline(pairedAt: now())
                emit(["kind": "state", "state": "pairing_revoked"])
            case "finish":
                guard command["handoffId"] as? String == handoffID, timeline.handoffState == "saved" else {
                    emit(["kind": "error", "code": "handoff_unconfirmed"])
                    continue
                }
                emit(["kind": "state", "state": "completed"])
                exit(0)
            case "disconnect":
                emit(["kind": "state", "state": "disconnected"])
                exit(0)
            default: continue
            }
        }
        exit(0)
    }
}
