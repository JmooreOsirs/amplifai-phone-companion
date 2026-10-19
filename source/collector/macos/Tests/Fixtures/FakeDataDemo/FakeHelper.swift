import CryptoKit
import Foundation

/// FAKE DATA helper for the demo bundle. It speaks the companion's stdin/stdout JSON protocol with
/// fictional data and a simulated browser. It opens no device, socket, file for writing or URL.
@main
struct FakeHelper {
    private static let fast = FileManager.default.fileExists(atPath: URL(fileURLWithPath: CommandLine.arguments[0])
        .deletingLastPathComponent().appendingPathComponent("FAST_DEMO_TIMING").path)

    /// The demo bundle's folder; the demo app writes a per-link file there when Brian clicks the simulated Save.
    private static let demoControl = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("demo-control")

    private static func saveClicked(_ handoffID: String) -> Bool {
        fast || FileManager.default.fileExists(atPath: demoControl.appendingPathComponent("save-\(handoffID)").path)
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

    /// Simulated browser stages advance one per handoff-status poll (the app polls every two seconds).
    private enum Browser { case idle, linked, connecting, asked, approved, received, savePending, denied }

    private static func commands() {
        let all = FakeDataSet.contacts
        var reviewIDs: [Int] = []
        var handoffID = ""
        var intentID = ""
        var browser = Browser.idle
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
                intentID = UUID().uuidString.lowercased()
                browser = .linked
                emit(["kind": "pairing", "pairCode": String((0..<10).map { _ in "0123456789".randomElement()! }),
                      "port": 48751, "expiresInSeconds": 300, "handoffId": handoffID])
            case "handoff-status":
                guard command["handoffId"] as? String == handoffID else { continue }
                switch browser {
                case .linked where !fast:
                    // One visible beat for "you would choose Connect in your browser now".
                    browser = .connecting
                case .linked, .connecting:
                    browser = .asked
                    emit(["kind": "destination", "state": "pending", "handoffId": handoffID, "intentId": intentID,
                          "email": FakeDataSet.email, "course": FakeDataSet.course])
                case .approved:
                    browser = .received
                    emit(["kind": "handoff", "state": "received", "handoffId": handoffID])
                case .received:
                    // The real flow needs a per-source Save in the browser; the demo waits for its labelled click.
                    guard saveClicked(handoffID) else { continue }
                    browser = .savePending
                    emit(["kind": "handoff", "state": "saved_pending_browser_receipt", "handoffId": handoffID])
                case .savePending:
                    emit(["kind": "handoff", "state": "saved", "handoffId": handoffID])
                case .idle, .asked, .denied: break
                }
            case "destination-decision":
                guard command["handoffId"] as? String == handoffID, browser == .asked else { continue }
                let approved = command["approved"] as? Bool == true
                browser = approved ? .approved : .denied
                emit(["kind": "destination", "state": approved ? "approved" : "denied", "handoffId": handoffID, "intentId": intentID])
            case "revoke":
                handoffID = ""
                browser = .idle
                emit(["kind": "state", "state": "pairing_revoked"])
            case "finish":
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
