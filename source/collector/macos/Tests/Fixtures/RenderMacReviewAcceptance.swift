import AppKit
import Foundation
import SwiftUI

@main @MainActor
struct RenderMacReviewAcceptance {
    private enum FixtureError: Error { case unexpectedState(String) }

    static func main() throws {
        let output = URL(fileURLWithPath: CommandLine.arguments[1])
        NSApplication.shared.setActivationPolicy(.prohibited)
        var timings: [String: Double] = [:]
        var stateEvidence: [String: [String: Any]] = [:]
        let reviewStates = ["pending-review", "local-review", "handoff", "save-receipt", "selection-change"]
        let allStates = ["main", "all-exceptions", "review", "last-page", "search", "empty", "loading"] + reviewStates + ["setup", "password", "error", "completed", "no-selection", "unavailable", "connecting", "transferring", "processing", "completing", "cancelling"]
        let states = CommandLine.arguments.count > 2 ? Array(CommandLine.arguments.dropFirst(2)) : allStates
        guard !states.isEmpty, states.allSatisfy({ allStates.contains($0) }) else { throw CocoaError(.validationMissingMandatoryProperty) }
        for state in states {
            var commands: [Data] = []
            let model = CollectorModel(commandWriter: { commands.append($0) })
            let updater = CompanionUpdater(bundleInformation: [:])
            let start = Date()
            if !["setup", "password", "error", "completed", "connecting", "transferring", "processing", "completing", "cancelling"].contains(state) {
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
                        if state != "local-review" {
                            let handoffID = "00000000-0000-4000-8000-0000000000aa"
                            let intentID = "00000000-0000-4000-8000-0000000000bb"
                            model.pairBrowser(automaticAccountNavigation: false)
                            try receive(["kind": "pairing", "pairCode": "1234567890", "port": 48751,
                                         "expiresInSeconds": 300, "handoffId": handoffID])
                            try receive(["kind": "destination", "state": "pending", "handoffId": handoffID,
                                         "intentId": intentID, "email": "synthetic@example.invalid", "course": "Synthetic review"])
                            try receive(["kind": "destination", "state": "approved", "handoffId": handoffID,
                                         "intentId": intentID])
                            try receive(["kind": "handoff", "state": "received", "handoffId": handoffID])
                            guard model.handoffID == handoffID, model.destinationApproved, model.browserReceived,
                                  !model.canEditWorkspace, !model.handoffAcknowledged else {
                                throw FixtureError.unexpectedState("\(state): synthetic handoff did not lock the review")
                            }
                            if state == "save-receipt" {
                                try receive(["kind": "handoff", "state": "saved_pending_browser_receipt", "handoffId": handoffID])
                                guard model.accountSaveAwaitingBrowserReceipt, !model.handoffAcknowledged,
                                      !model.canRequestSelectionChange else {
                                    throw FixtureError.unexpectedState("Save receipt must await the final browser receipt")
                                }
                            }
                            if state == "selection-change" {
                                model.requestSelectionChange()
                                guard model.selectionChangePending, !model.canEditWorkspace,
                                      model.handoffID == handoffID, model.selectedIDs == Set(selected) else {
                                    throw FixtureError.unexpectedState("Selection change must await helper revocation")
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
            else if state == "processing" { model.phase = .processing }
            else if state == "completing" { model.phase = .completing }
            else if state == "cancelling" { model.phase = .cancelling }
            guard model.accountNavigationID == nil, !model.helperRunning else {
                throw FixtureError.unexpectedState("Fixture must not request account navigation or start the helper")
            }
            stateEvidence[state] = ["phase": String(describing: model.phase), "reviewPending": model.reviewPending,
                "selectionNeedsReview": model.selectionNeedsReview, "canEditWorkspace": model.canEditWorkspace,
                "handoffPresent": !model.handoffID.isEmpty, "browserReceived": model.browserReceived,
                "saveReceiptPending": model.accountSaveAwaitingBrowserReceipt,
                "selectionChangePending": model.selectionChangePending, "accountNavigationRequested": false]
            timings[state] = Date().timeIntervalSince(start)
            for (width, height) in [(375, 844), (768, 900), (1440, 1000), (1180, 680)] {
                try render(ContentView(model: model, updater: updater), width: width, height: height,
                           path: output.appendingPathComponent("mac-\(state)-\(width)x\(height).png"))
                if let workspace = model.workspace, ["main", "review", "last-page", "search", "empty"].contains(state), width != 1180 {
                    try render(ContactReviewView(model: model, workspace: workspace, wellHeight: 322,
                               search: .constant(workspace.query), showingGroupTools: .constant(state == "review"), confirmingBulkMove: .constant(false))
                               .padding(20).background(Brand.panel).foregroundStyle(Brand.body).tint(Brand.lime),
                               width: width, height: 1000, path: output.appendingPathComponent("board-\(state)-\(width).png"))
                }
            }
        }
        let receipt: [String: Any] = ["renderer": "offscreen NSHostingView bitmap of production views", "contacts": 5501,
            "viewports": [[375, 844], [768, 900], [1440, 1000], [1180, 680]], "states": states, "fixturePreparationSeconds": timings,
            "stateEvidence": stateEvidence,
            "font": NSFont(name: "OpenSans-Regular", size: 16)?.fontName ?? "missing",
            "screenCaptured": false, "windowCreated": false, "appActivated": false,
            "collectorStarted": false, "ownerWorkspaceRead": false, "updateControllerStarted": false]
        try JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted, .sortedKeys])
            .write(to: output.appendingPathComponent("mac-review-render-receipt.json"))
        print("Rendered synthetic Mac states without a window, collection or account action")
    }

    private static func render<V: View>(_ view: V, width: Int, height: Int, path: URL) throws {
        let hosting = NSHostingView(rootView: view.environment(\.colorScheme, .dark)
            .frame(width: CGFloat(width), height: CGFloat(height)))
        hosting.frame = NSRect(x: 0, y: 0, width: width, height: height)
        hosting.layoutSubtreeIfNeeded()
        guard let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { throw CocoaError(.coderInvalidValue) }
        hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
        guard let data = bitmap.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
        try data.write(to: path, options: .atomic)

    }
}
