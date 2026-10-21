import AppKit
import Foundation
import SwiftUI

@main @MainActor
struct RenderMacReviewAcceptance {
    private enum FixtureError: Error { case unexpectedState(String) }
    private static var scrollEvidence: [String: [String: Any]] = [:]

    static func main() throws {
        let output = URL(fileURLWithPath: CommandLine.arguments[1])
        NSApplication.shared.setActivationPolicy(.prohibited)
        var timings: [String: Double] = [:]
        var stateEvidence: [String: [String: Any]] = [:]
        let shareStates = ["bind-uncertain", "bind-refused", "expired"]
        let reviewStates = ["pending-review", "local-review", "handoff", "save-receipt", "link-failed", "verify-uncertain", "finish-retry", "adoption-retry", "adoption-checking", "adoption-recovered"] + shareStates
        let runStates = ["run-claiming", "run-uncertain", "run-refused", "run-approved", "agree-checking", "agree-unknown", "encryption-off", "encryption-on", "status-uncertain", "status-expired", "status-revoked", "status-changed", "active-transfer"]
        let guideStates = ["finder-connect", "finder-device", "finder-encrypt", "finder-recheck"]
        let setupStates = ["setup"] + runStates + guideStates + ["password", "error", "completed", "connecting", "transferring", "transferring-progress", "processing", "completing", "cancelling"]
        let allStates = ["main", "organizing", "history", "all-selected", "all-exceptions", "review", "last-page", "search", "empty", "loading"] + reviewStates + ["no-selection", "unavailable"] + setupStates
        let viewports = [(375, 844), (768, 900), (1440, 1000), (1180, 820), (820, 640), (1180, 680)]
        let states = CommandLine.arguments.count > 2 ? Array(CommandLine.arguments.dropFirst(2)) : allStates
        guard !states.isEmpty, states.allSatisfy({ allStates.contains($0) }) else { throw CocoaError(.validationMissingMandatoryProperty) }
        for state in states {
            var commands: [Data] = []
            let server = SimulatedPhoneRunServer()
            let model = CollectorModel(commandWriter: { commands.append($0) },
                                       runAuthority: PhoneRunAuthorityClient(transport: server, schedule: PhoneRunAuthorityClient.immediate))
            let updater = CompanionUpdater(bundleInformation: [:])
            let start = Date()
            func admit() throws {
                let run = server.createRun(email: "synthetic.owner@example.invalid", course: "SYNTHETIC review course")
                guard let link = PhoneRunLink(url: server.appLink(run)) else { throw FixtureError.unexpectedState("link") }
                model.admitRunLink(PendingRunLink(link: link, returnBrowserBundleID: nil))
            }
            if !setupStates.contains(state) {
                // A synthetic run, started on the simulated helper wire.
                try admit()
                guard model.startCollection() else { throw FixtureError.unexpectedState("\(state): start") }
                model.receive(try AgentEvent.decode(Data(#"{"kind":"backup-encryption-status","backupEncrypted":false}"#.utf8)))
                model.finished(mode: "backup-encryption-status", code: 0)
                guard model.startCollectionWithAvailableData() else { throw FixtureError.unexpectedState("\(state): explicit available-data choice") }
                let names = ["Priya Natarajan", "Marcus Bell", "Elena Fischer", "Sam Okafor", "Dana Whitfield", "Lin Zhao", "Omar Haddad", "Noor Khalil", "Grace Liu", "Tomás Rivera", "Aisha Bello", "Ruth Abara"]
                let total = 5501
                let rows: [[String: Any]] = (1...total).map { id in
                    ["id": id, "name": id > 5129 ? "Community Services LLC \(id)" : "\(names[(id - 1) % names.count]) \(id)", "phoneCount": 1,
                     "phoneEnds": [String(format: "%04d", id)], "nameSource": "address_book"]
                }
                func receive(_ values: [String: Any]) throws {
                    model.receive(try AgentEvent.decode(JSONSerialization.data(withJSONObject: values)))
                }
                try receive(["kind": "capture", "query": "", "cursor": 0, "totalContacts": total, "nextCursor": 200, "contacts": Array(rows.prefix(200))])
                model.prepareWorkspace()
                let scan = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
                for cursor in stride(from: 0, to: state == "loading" ? 200 : total, by: 200) {
                    let end = min(cursor + 200, total)
                    try receive(["kind": "contacts", "scanId": scan["scanId"]!, "query": "", "cursor": cursor,
                                 "totalContacts": total, "nextCursor": end == total ? NSNull() : end, "contacts": Array(rows[cursor..<end])])
                }
                model.phase = .selecting
                if state == "all-selected" { model.selectAllVerifiedContacts() }
                if state == "all-exceptions" { model.showContactGroup(.all); model.selectAllVerifiedContacts(); model.toggle(7); model.toggle(total) }
                if state == "review" { model.showContactGroup(.review) }
                if state == "last-page" { model.showContactGroup(.all); for _ in 0..<27 { model.nextWorkspacePage() } }
                if state == "search" { model.searchWorkspace("Priya") }
                if state == "empty" { model.searchWorkspace("No matching person") }
                if state == "no-selection" { model.clearAllVerifiedContacts() }
                if state == "unavailable" { model.missingSources = ["calls", "messages"] }
                if reviewStates.contains(state) {
                    let selected = model.selectedIDs.sorted()
                    model.reviewWorkspace(continueToAccount: false)
                    guard model.reviewPending, model.phase == .selecting,
                          let firstCommand = commands.last,
                          let firstPage = try JSONSerialization.jsonObject(with: firstCommand) as? [String: Any],
                          let reviewID = firstPage["reviewId"] as? String else {
                        throw FixtureError.unexpectedState("\(state): local review did not start")
                    }
                    if state != "pending-review" {
                        for cursor in stride(from: 0, to: selected.count, by: 1000) {
                            let end = min(cursor + 1000, selected.count)
                            guard let command = commands.last,
                                  let page = try JSONSerialization.jsonObject(with: command) as? [String: Any],
                                  page["action"] as? String == "review-page",
                                  page["reviewId"] as? String == reviewID,
                                  page["cursor"] as? Int == cursor,
                                  page["total"] as? Int == selected.count,
                                  page["ids"] as? [Int] == Array(selected[cursor..<end]) else {
                                throw FixtureError.unexpectedState("\(state): selected-ID page differs")
                            }
                            try receive(["kind": "review-page", "reviewId": reviewID, "nextCursor": end])
                        }
                        try receive(["kind": "review", "reviewId": reviewID,
                                     "selected_contacts": selected.count,
                                     "selectionSha256": CollectorModel.selectionDigest(selected),
                                     "matched_calls": 42, "matched_messages": 87])
                        guard model.phase == .reviewing, !model.reviewPending, !model.selectionNeedsReview,
                              model.selectedIDs == Set(selected), model.handoffID.isEmpty else {
                            throw FixtureError.unexpectedState("\(state): exact local review was not accepted")
                        }
                        let handoffID = "00000000-0000-4000-8000-0000000000aa"
                        let pairing: [String: Any] = ["kind": "pairing", "pairCode": "1234567890", "port": 48751,
                                                      "expiresInSeconds": 300, "handoffId": handoffID,
                                                      "payloadSha256": String(repeating: "ab", count: 32)]
                        if state == "link-failed" {
                            // Automatic account link failure keeps the review and names the recovery action.
                            model.pairBrowser(automaticAccountNavigation: false)
                            var invalid = pairing
                            invalid["pairCode"] = "invalid"
                            try receive(invalid)
                            guard model.errorMessage.contains("Continue"), model.accountNavigationID == nil else {
                                throw FixtureError.unexpectedState("link-failed: recovery copy missing")
                            }
                        }
                        if state == "bind-uncertain" { server.faultsByPath[PhoneRunContract.bindPath] = [.malformed, .malformed, .malformed] }
                        if state == "bind-refused" { server.faultsByPath[PhoneRunContract.bindPath] = [.status(409)] }
                        if state != "local-review" && state != "link-failed" {
                            model.pairBrowser(automaticAccountNavigation: false)
                            try receive(pairing)
                            if ["handoff", "save-receipt", "expired", "verify-uncertain", "finish-retry"].contains(state) {
                                guard model.handoffBound, !model.canEditWorkspace, !model.handoffAcknowledged else {
                                    throw FixtureError.unexpectedState("\(state): synthetic handoff did not bind")
                                }
                                if state == "verify-uncertain" { server.faultsByPath[PhoneRunContract.verifyIntentPath] = [.malformed, .malformed, .malformed] }
                                try receive(["kind": "destination", "state": "pending", "handoffId": handoffID,
                                             "intentId": "00000000-0000-4000-8000-0000000000bb",
                                             "email": "synthetic.owner@example.invalid", "course": "SYNTHETIC review course"])
                                if state != "verify-uncertain" {
                                    try receive(["kind": "destination", "state": "approved", "handoffId": handoffID,
                                                 "intentId": "00000000-0000-4000-8000-0000000000bb"])
                                } else if !model.canRetryDestinationVerification {
                                    throw FixtureError.unexpectedState("verify-uncertain: exact retry not available")
                                }
                            }
                            if state == "save-receipt" {
                                try receive(["kind": "handoff", "state": "received", "handoffId": handoffID])
                                try receive(["kind": "handoff", "state": "saved_pending_browser_receipt", "handoffId": handoffID])
                                guard model.accountSaveAwaitingBrowserReceipt, !model.handoffAcknowledged else {
                                    throw FixtureError.unexpectedState("Save receipt must await the final browser receipt")
                                }
                            }
                            if state == "finish-retry" {
                                try receive(["kind": "handoff", "state": "received", "handoffId": handoffID])
                                for _ in 0..<3 {
                                    try receive(["kind": "handoff", "state": "saved", "handoffId": handoffID])
                                    try receive(["kind": "error", "code": "handoff_unconfirmed"])
                                }
                                try receive(["kind": "handoff", "state": "saved", "handoffId": handoffID])
                                guard model.canRetryAccountCompletion else { throw FixtureError.unexpectedState("finish-retry: exact receipt retry unavailable") }
                            }
                            if state == "expired" { try receive(["kind": "handoff", "state": "expired", "handoffId": handoffID]) }
                            if state.hasPrefix("adoption-") {
                                guard let original = model.phoneRun else { throw FixtureError.unexpectedState("adoption: missing run") }
                                server.replace(original.runID, course: "SYNTHETIC updated course", categories: ["contacts"])
                                server.faultsByPath[PhoneRunContract.bindPath] = [.loseReply, .malformed, .dropRequest]
                                model.refreshRunStatus()
                                guard model.canRetryApprovalAdoption, model.phoneRun?.claim == original.claim else {
                                    throw FixtureError.unexpectedState("adoption: retained exact approval before retry")
                                }
                                if state == "adoption-checking" {
                                    server.holdReplies = true
                                    model.retryApprovalAdoption()
                                    guard model.approvalAdoptionPending else { throw FixtureError.unexpectedState("adoption: checking") }
                                } else if state == "adoption-recovered" {
                                    model.retryApprovalAdoption()
                                    guard model.runStatus == .current, model.phoneRun?.categories == ["contacts"],
                                          model.handoffID == handoffID, model.selectedIDs == Set(selected) else {
                                        throw FixtureError.unexpectedState("adoption: same-handoff recovery")
                                    }
                                }
                            }
                        }
                    }
                }
            } else if state == "password" { model.phase = .passwordRequired }
            else if state == "error" { model.phase = .error; model.errorMessage = "The iPhone connection ended. Your source choices have not been saved." }
            else if state == "completed" { model.phase = .completed }
            else if state == "connecting" { model.phase = .connecting }
            else if state == "transferring" { model.phase = .transferring }
            else if state == "transferring-progress" {
                model.phase = .connecting
                model.receive(try AgentEvent.decode(Data(#"{"kind":"progress","value":42}"#.utf8)))
                model.receive(try AgentEvent.decode(Data(#"{"kind":"transfer","stage":"backup","receivedBytes":2147483648,"retainedBytes":402653184,"discardedBytes":1744830464,"filesReceived":812,"bytesPerSecond":31457280,"elapsedSeconds":68}"#.utf8)))
            }
            else if state == "run-claiming" {
                server.holdReplies = true
                try admit()
            }
            else if state == "run-uncertain" {
                server.faults = [.dropRequest, .dropRequest, .dropRequest]
                try admit()
            }
            else if state == "run-refused" {
                server.faults = [.status(409)]
                try admit()
            }
            else if state == "run-approved" || guideStates.contains(state) { try admit() }
            else if state.hasPrefix("status-") {
                try admit()
                guard let runID = model.phoneRun?.runID else { throw FixtureError.unexpectedState("status: missing run") }
                switch state {
                case "status-uncertain": server.faultsByPath[PhoneRunContract.statusPath] = [.malformed, .malformed, .malformed]
                case "status-expired": server.now = { Date().addingTimeInterval(13 * 60 * 60) }
                case "status-revoked": server.revoke(runID)
                default: server.changeApproval(runID)
                }
                model.refreshRunStatus()
            }
            else if state == "active-transfer" {
                try admit()
                guard model.startCollection() else { throw FixtureError.unexpectedState("active-transfer: check") }
                model.receive(try AgentEvent.decode(Data(#"{"kind":"backup-encryption-status","backupEncrypted":false}"#.utf8)))
                model.finished(mode: "backup-encryption-status", code: 0)
                guard model.startCollectionWithAvailableData() else { throw FixtureError.unexpectedState("active-transfer: explicit start") }
                model.phase = .transferring
            }
            else if state == "encryption-off" || state == "encryption-on" {
                try admit()
                model.checkBackupEncryptionStatus()
                let event: [String: Any] = ["kind": "backup-encryption-status", "backupEncrypted": state == "encryption-on"]
                model.receive(try AgentEvent.decode(JSONSerialization.data(withJSONObject: event)))
                model.finished(mode: "backup-encryption-status", code: 0)
            }
            else if state == "agree-checking" { try admit(); _ = model.startCollection() }
            else if state == "agree-unknown" {
                try admit()
                _ = model.startCollection()
                model.receive(try AgentEvent.decode(Data(#"{"kind":"error","code":"phone_connection"}"#.utf8)))
                model.finished(mode: "backup-encryption-status", code: 1)
            }
            else if state == "processing" { model.phase = .processing }
            else if state == "completing" { model.phase = .completing }
            else if state == "cancelling" { model.phase = .cancelling }
            if state == "history" {
                let record: [String: Any] = ["source": "messages", "component": "database", "reason": "selected_payload_length",
                    "result": "length_mismatch", "declaredBytes": 8192, "rawBytes": 4112, "copiedBytes": 4096,
                    "encrypted": true, "entryKeyPresent": true, "validationCode": "sidecar_present",
                    "validationScope": "encrypted_messages_database_v1", "producerDigest": "matched", "copyDigest": "matched", "sidecars": "present"]
                let attempt: [String: Any] = ["attemptId": "12345678-1234-4123-8123-123456789abc",
                                            "createdAt": 1_800_000_000, "sourceDiagnostics": [record]]
                model.receive(try AgentEvent.decode(JSONSerialization.data(withJSONObject:
                    ["kind": "residue", "sessions": [], "sourceDiagnosticHistory": [attempt]])))
            }
            guard model.accountNavigationID == nil, !model.helperRunning,
                  model.operationID == nil || ["connecting", "agree-checking", "active-transfer"].contains(state) || !setupStates.contains(state) else {
                throw FixtureError.unexpectedState("Fixture must not request account navigation or start the helper")
            }
            stateEvidence[state] = ["phase": String(describing: model.phase), "reviewPending": model.reviewPending,
                "selectionNeedsReview": model.selectionNeedsReview, "canEditWorkspace": model.canEditWorkspace,
                "handoffPresent": !model.handoffID.isEmpty, "browserReceived": model.browserReceived,
                "runApproval": String(describing: model.runApproval), "runBinding": String(describing: model.runBinding),
                "runStatus": String(describing: model.runStatus),
                "approvalAdoptionPending": model.approvalAdoptionPending,
                "canRetryApprovalAdoption": model.canRetryApprovalAdoption,
                "approvedToSave": model.phoneRun.map { CollectorModel.approvedToSaveLabel($0.categories) } ?? "",
                "saveReceiptPending": model.accountSaveAwaitingBrowserReceipt,
                "selectionChangePending": model.selectionChangePending, "accountNavigationRequested": false]
            timings[state] = Date().timeIntervalSince(start)
            for (width, height) in viewports {
                if let guideStep = guideStates.firstIndex(of: state) {
                    try render(ScrollView {
                        FinderEncryptionGuide(model: model, initialStep: guideStep, onClose: {})
                            .panel().frame(maxWidth: 880).padding(28)
                    }.background(Brand.background).tint(Brand.lime), width: width, height: height,
                               path: output.appendingPathComponent("mac-\(state)-\(width)x\(height).png"))
                } else {
                    try render(ContentView(model: model, updater: updater, showingGroupTools: state == "organizing"), width: width, height: height,
                               path: output.appendingPathComponent("mac-\(state)-\(width)x\(height).png"))
                }
                if let workspace = model.workspace, ["main", "review", "last-page", "search", "empty"].contains(state), [375, 768, 1440].contains(width) {
                    try render(ContactReviewView(model: model, workspace: workspace, wellHeight: 322,
                               search: .constant(workspace.query), showingGroupTools: .constant(state == "review"), confirmingBulkMove: .constant(false))
                               .padding(20).background(Brand.panel).foregroundStyle(Brand.body).tint(Brand.lime),
                               width: width, height: 1000, path: output.appendingPathComponent("board-\(state)-\(width).png"))
                }
            }
            if ["local-review", "handoff", "save-receipt"].contains(state) {
                try render(ContentView(model: model, updater: updater), width: 1440, height: 2400,
                           path: output.appendingPathComponent("mac-\(state)-overview-1440x2400.png"))
            }
            if ["setup", "run-approved", "agree-unknown", "completed", "error", "expired", "link-failed"].contains(state) {
                try render(ContentView(model: model, updater: updater),
                           width: 1180, height: 1500, path: output.appendingPathComponent("mac-\(state)-tall-1180x1500.png"))
            }
        }
        let receipt: [String: Any] = ["renderer": "offscreen NSHostingView bitmap of production views", "contacts": 5501,
            "viewports": viewports.map { [$0.0, $0.1] }, "states": states, "fixturePreparationSeconds": timings,
            "stateEvidence": stateEvidence, "contactScrollEvidence": scrollEvidence,
            "font": NSFont(name: "OpenSans-Regular", size: 16)?.fontName ?? "missing",
            "resolvedNativeStyle": [
                "bodyFont": NSFont(name: "OpenSans-Regular", size: 16)?.fontName ?? "missing",
                "headingFont": NSFont(name: "Arial-BoldMT", size: 28)?.fontName ?? "missing",
                "bodySize": 16, "headingSize": 28, "pagePadding": Brand.Space.page,
                "itemGap": Brand.Space.item, "sectionGap": Brand.Space.section,
                "controlRadius": Brand.Radius.control, "panelRadius": Brand.Radius.panel,
                "contactMinimum": Brand.Layout.contactMinimum,
                "backgroundSRGB": rgb(Brand.background), "panelSRGB": rgb(Brand.panel),
                "headingSRGB": rgb(Brand.heading), "bodySRGB": rgb(Brand.body),
                "mutedSRGB": rgb(Brand.muted), "limeSRGB": rgb(Brand.lime), "blueSRGB": rgb(Brand.blue)
            ],
            "guideSymbolsAvailable": ["iphone.gen3", "cable.connector.horizontal", "laptopcomputer", "lock.iphone"]
                .allSatisfy { NSImage(systemSymbolName: $0, accessibilityDescription: nil) != nil },
            "screenCaptured": false, "windowCreated": false, "appActivated": false,
            "collectorStarted": false, "ownerWorkspaceRead": false, "updateControllerStarted": false]
        try JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted, .sortedKeys])
            .write(to: output.appendingPathComponent("mac-review-render-receipt.json"))
        print("Rendered synthetic Mac states without a window, collection or account action")
    }

    private static func rgb(_ color: Color) -> [Double] {
        let resolved = NSColor(color).usingColorSpace(.sRGB)!
        return [resolved.redComponent, resolved.greenComponent, resolved.blueComponent].map { Double($0) }
    }

    private static func render<V: View>(_ view: V, width: Int, height: Int, path: URL) throws {
        let hosting = NSHostingView(rootView: view.environment(\.colorScheme, .dark)
            .frame(width: CGFloat(width), height: CGFloat(height))
            .overlay(alignment: .bottomLeading) {
                // Every image is labeled so a synthetic state cannot be mistaken for a real device or account.
                Text("SYNTHETIC FIXTURE · no iPhone, helper or account")
                    .font(.system(size: 11, weight: .bold)).foregroundStyle(.black)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Color.yellow, in: Capsule()).padding(8)
            })
        hosting.frame = NSRect(x: 0, y: 0, width: width, height: height)
        hosting.layoutSubtreeIfNeeded()
        guard let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { throw CocoaError(.coderInvalidValue) }
        hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
        guard let data = bitmap.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
        try data.write(to: path, options: .atomic)
        if ["main", "review", "organizing", "unavailable", "history"].contains(where: { path.lastPathComponent.hasPrefix("mac-\($0)-") }) {
            func descendants(_ view: NSView) -> [NSView] { view.subviews.flatMap { [$0] + descendants($0) } }
            let scrollViews = descendants(hosting).compactMap { $0 as? NSScrollView }
            guard scrollViews.count == 1, let scroll = scrollViews.first, let document = scroll.documentView else {
                throw FixtureError.unexpectedState("\(path.lastPathComponent): expected one contact scroll region, found \(scrollViews.count)")
            }
            let viewport = hosting.convert(scroll.bounds, from: scroll)
            guard hosting.bounds.contains(viewport), viewport.height >= 44 else {
                throw FixtureError.unexpectedState("\(path.lastPathComponent): contact viewport is outside the window or unusable: \(viewport); hosting: \(hosting.bounds)")
            }
            let before = scroll.contentView.bounds.origin
            scroll.contentView.scroll(to: NSPoint(x: before.x, y: before.y + 120))
            scroll.reflectScrolledClipView(scroll.contentView)
            hosting.layoutSubtreeIfNeeded()
            let after = scroll.contentView.bounds.origin
            guard before != after, hosting.convert(scroll.bounds, from: scroll) == viewport else {
                throw FixtureError.unexpectedState("\(path.lastPathComponent): scrolling must move contacts inside a fixed viewport; document: \(document.frame), bounds: \(document.bounds), clip: \(scroll.contentView.bounds), before: \(before), after: \(after)")
            }
            scrollEvidence[path.lastPathComponent] = ["scrollRegions": scrollViews.count,
                "viewport": [viewport.minX, viewport.minY, viewport.width, viewport.height],
                "scrolled": true, "fixedViewport": true]
        }
    }
}
