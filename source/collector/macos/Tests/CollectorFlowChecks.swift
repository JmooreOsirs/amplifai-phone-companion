import Foundation
import Darwin

@main
@MainActor
struct CollectorFlowChecks {
    private enum Failure: Error { case check(String) }
    private static func require(_ value: @autoclosure () -> Bool, _ reason: String) throws {
        guard value() else { throw Failure.check(reason) }
    }
    private static func event(_ json: String) throws -> AgentEvent {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(AgentEvent.self, from: Data(json.utf8))
    }
    private static func emittedReviewID(_ commands: [Data]) throws -> String {
        guard let command = commands.last,
              let object = try JSONSerialization.jsonObject(with: command) as? [String: Any],
              object["action"] as? String == "review-page",
              let reviewID = object["reviewId"] as? String else {
            throw Failure.check("Review must send a bound request through the helper wire")
        }
        return reviewID
    }
    private static func reviewEvent(_ reviewID: String, ids: [Int]) throws -> AgentEvent {
        let digest = CollectorModel.selectionDigest(ids)
        return try event(#"{"kind":"review","reviewId":"\#(reviewID)","selected_contacts":\#(ids.count),"selectionSha256":"\#(digest)"}"#)
    }
    static func main() {
        do { try checks(); print("macOS flow and byte-progress checks passed") }
        catch { FileHandle.standardError.write(Data("macOS flow check failed: \(error)\n".utf8)); Darwin.exit(1) }
    }
    private static func checks() throws {
        try pagedContactReview()
        try maximumReviewCommand()
        try reviewResponseBinding()
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
        try require(!model.progressIsKnown, "Connection must not invent a percentage")
        try require(model.statusDetail.contains("Trust"), "Connection must explain the owner-operated Trust step")
        model.receive(try event(#"{"kind":"progress"}"#))
        try require(!model.progressIsKnown, "Missing progress is indeterminate, not zero")
        model.receive(try event(#"{"kind":"progress","value":42}"#))
        try require(model.progressIsKnown && model.progress == 42, "Observed backup progress must survive")
        try require(model.backupRemainingEstimate(at: Date()) == nil, "One device percentage cannot create a time estimate")
        model.receive(try event(#"{"kind":"progress","value":100}"#))
        try require(model.phase == .processing && !model.statusTitle.contains("ready"), "100% backup is not finished metadata review")
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":2147483648,"retainedBytes":1073741824,"discardedBytes":1073741824,"filesReceived":9,"bytesPerSecond":1048576,"elapsedSeconds":10}"#))
        try require(model.phase == .transferring && model.transferIsKnown && model.transferBytes == 2147483648,
                    "Byte progress must advance even after a stale 100% report")
        try require(model.transferSummary.contains("2.0 GiB") && model.transferSummary.contains("1.0 GiB"), "Transferred and retained bytes must remain distinct")
        model.receive(try event(#"{"kind":"transfer","stage":"backup","receivedBytes":-1,"retainedBytes":0,"discardedBytes":-1,"filesReceived":10,"bytesPerSecond":0,"elapsedSeconds":11}"#))
        try require(model.transferBytes == 2147483648, "Negative or inconsistent progress must not overwrite observed evidence")
        model.receive(try event(#"{"kind":"transfer","stage":"processing","processedBytes":1234567890}"#))
        try require(model.phase == .processing && model.processedBytes == 1234567890, "Parsing is a separate observed stage, not account completion")
        model.receive(try event(#"{"kind":"state","state":"password_required"}"#))
        try require(model.statusDetail.contains("existing") && !model.statusDetail.contains("Apple ID"), "Password recovery must name the existing backup password")

        let id = "00000000-0000-4000-8000-000000000001"
        let other = "00000000-0000-4000-8000-000000000002"
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        model.selectedIDs = [7]
        model.review()
        let reviewID = try emittedReviewID(commands)
        model.receive(try reviewEvent(reviewID, ids: [7]))
        model.receive(try event(#"{"kind":"pairing","pairCode":"0000000000","port":48751,"handoffId":"\#(id)"}"#))
        model.receive(try event(#"{"kind":"handoff","state":"received","handoffId":"\#(id)"}"#))
        try require(!model.canSafelyClose && model.contacts.count == 1 && model.selectedIDs == [7], "Transfer delivery must preserve the native recovery copy")
        model.receive(try event(#"{"kind":"handoff","state":"saved","handoffId":"\#(other)"}"#))
        try require(!model.handoffAcknowledged && !model.canSafelyClose, "A different handoff cannot close this run")
        model.receive(try event(#"{"kind":"handoff","state":"saved","handoffId":"\#(id)"}"#))
        try require(model.handoffAcknowledged && !model.canSafelyClose, "A matching save acknowledgement must still await helper exit")
        model.finished(mode: "connect", code: 0)
        try require(model.canSafelyClose && model.contacts.isEmpty && model.pairCode.isEmpty, "Only acknowledged clean completion can release the native copy")

        let recovery = CollectorModel()
        recovery.receive(try event(#"{"kind":"error","code":"workspace_cleanup","cleanupRequired":true}"#))
        try require(!recovery.canSafelyClose && recovery.recoverySteps.contains(where: { $0.contains("Check temporary data") }), "Unresolved cleanup cannot be auto-closed")

        var retainedCommands: [Data] = []
        let retained = CollectorModel(commandWriter: { retainedCommands.append($0) })
        retained.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        retained.selectedIDs = [7]
        retained.review()
        let retainedReviewID = try emittedReviewID(retainedCommands)
        retained.receive(try reviewEvent(retainedReviewID, ids: [7]))
        retained.receive(try event(#"{"kind":"pairing","pairCode":"0000000000","handoffId":"\#(id)"}"#))
        retained.receive(try event(#"{"kind":"handoff","state":"expired","handoffId":"\#(id)"}"#))
        try require(retained.handoffID.isEmpty && retained.contacts.count == 1 && retained.selectedIDs == [7] && !retained.canSafelyClose,
                    "Expired pairing must stop the old binding and preserve explicit recovery")
        retained.finished(mode: "connect", code: 1)
        try require(retained.hasUnsavedReview, "Unexpected helper exit must retain unsaved review")
        retained.disconnect()
        try require(retained.contacts.isEmpty && retained.selectedIDs.isEmpty && retained.phase == .idle,
                    "Explicit discard after helper exit must permit recovery without a stranded review")

        var changedCommands: [Data] = []
        let changed = CollectorModel(commandWriter: { changedCommands.append($0) })
        changed.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        changed.selectedIDs = [7]
        changed.review()
        let changedReviewID = try emittedReviewID(changedCommands)
        changed.receive(try reviewEvent(changedReviewID, ids: [7]))
        changed.toggle(7)
        changed.receive(try event(#"{"kind":"pairing","pairCode":"0000000000","handoffId":"\#(id)"}"#))
        try require(changed.handoffID.isEmpty && changed.pairCode.isEmpty && !changed.handoffAcknowledged,
                    "Late pairing response cannot revive a selection changed after review")
    }

    private static func reviewResponseBinding() throws {
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"A","phoneCount":1,"phoneEnds":["0000"]},{"id":8,"name":"B","phoneCount":1,"phoneEnds":["1111"]}]}"#))
        model.selectedIDs = [7]
        model.receive(try event(#"{"kind":"review","reviewId":"00000000-0000-4000-8000-000000000000","selected_contacts":1}"#))
        try require(model.phase == .selecting && model.selectionNeedsReview,
                    "Unsolicited review cannot promote a selection to pairing")
        model.review()
        let reviewID = try emittedReviewID(commands)
        model.toggle(7)
        try require(model.selectedIDs == [7] && model.reviewPending,
                    "Selection toggles must freeze during the pending helper review")
        model.receive(try event(#"{"kind":"review","reviewId":"00000000-0000-4000-8000-000000000000","selected_contacts":1}"#))
        try require(model.reviewPending && model.phase == .selecting,
                    "A stale review response must not complete the current selection")
        model.selectedIDs = [8] // Simulate an indirect model mutation despite the frozen UI.
        model.receive(try reviewEvent(reviewID, ids: [7]))
        try require(!model.reviewPending && model.phase == .selecting && model.selectionNeedsReview,
                    "The exact reply cannot approve contacts different from the request snapshot")
        let beforePairing = commands.count
        model.pairBrowser()
        try require(commands.count == beforePairing && model.handoffID.isEmpty,
                    "Rejected review must not create a browser handoff")

        model.review()
        let retryID = try emittedReviewID(commands)
        model.receive(try reviewEvent(retryID, ids: [8]))
        try require(model.phase == .reviewing && !model.selectionNeedsReview,
                    "A fresh bound review should recover the selected contacts")
    }

    private static func pagedContactReview() throws {
        let first = (1...200).map { id in
            #"{"id":\#(id),"name":"Synthetic \#(id)","phoneCount":0,"phoneEnds":[]}"#
        }.joined(separator: ",")
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[\#(first)],"totalContacts":201,"query":"","cursor":0,"nextCursor":200}"#))
        try require(model.phase == .selecting && model.contacts.count == 200 &&
                    model.totalContacts == 201 && model.nextContactCursor == 200 && model.hasUnsavedReview,
                    "A bounded first preview page must retain exact total and unsaved recovery state")
        model.selectedIDs = [7]
        model.loadMoreContacts()
        try require(model.contactSearchPending && model.hasPreviousContactPage,
                    "Next page must preserve a path back before awaiting the helper")
        try require(commands.count == 1, "Next page must issue one helper command")
        let nextCommand = try JSONSerialization.jsonObject(with: commands[0]) as? [String: Any]
        try require(nextCommand?["cursor"] as? Int == 200,
                    "Next-page request must send its exact cursor through the bounded wire")
        model.receive(try event(#"{"kind":"contacts","contacts":[{"id":201,"name":"Synthetic 201","phoneCount":0,"phoneEnds":[]}],"totalContacts":201,"query":"","cursor":200,"nextCursor":null}"#))
        try require(model.contacts.count == 1 && model.contacts.last?.id == 201 &&
                    model.currentContactCursor == 200 && model.nextContactCursor == nil && model.selectedIDs == [7],
                    "The next contact page must replace the view without dropping earlier selection")
        model.receive(try event(#"{"kind":"contacts","contacts":[{"id":999,"name":"Stale","phoneCount":0,"phoneEnds":[]}],"totalContacts":201,"query":"old","cursor":0,"nextCursor":null}"#))
        try require(model.contacts.count == 1 && model.selectedIDs == [7],
                    "A stale query response must not replace the current review")
        model.previousContacts()
        try require(commands.count == 2, "Previous page must issue one additional helper command")
        let previousCommand = try JSONSerialization.jsonObject(with: commands[1]) as? [String: Any]
        try require(previousCommand?["cursor"] as? Int == 0,
                    "Previous-page request must send its exact cursor through the bounded wire")
        model.receive(try event(#"{"kind":"contacts","contacts":[\#(first)],"totalContacts":201,"query":"","cursor":0,"nextCursor":200}"#))
        try require(model.contacts.count == 200 && model.currentContactCursor == 0 &&
                    !model.hasPreviousContactPage && model.selectedIDs == [7],
                    "Previous page must restore only the bounded view and retain selected IDs")
        let incomplete = CollectorModel()
        incomplete.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic","phoneCount":0,"phoneEnds":[]}],"totalContacts":2,"query":"","cursor":0,"nextCursor":null}"#))
        try require(incomplete.phase == .error && !incomplete.canSafelyClose,
                    "An incomplete contact preview must fail closed before selection")
    }

    private static func maximumReviewCommand() throws {
        let ids = (0..<1000).map { Int.max - $0 }
        guard let wire = CollectorModel.encodedHelperCommand([
            "action": "review-page", "ids": ids, "cursor": 0, "total": 25_001,
            "reviewId": "00000000-0000-4000-8000-000000000001"
        ]) else {
            throw Failure.check("A selected-ID page must fit the helper's command bound")
        }
        try require(wire.count <= 600_000 && wire.last == 10,
                    "Selection page bytes must include the newline within the helper's exact 600 KB limit")
        guard let decoded = try JSONSerialization.jsonObject(with: wire) as? [String: Any],
              let recovered = decoded["ids"] as? [Int] else {
            throw Failure.check("The large review command must remain valid JSON")
        }
        try require(recovered == ids, "No selected contact ID may be dropped or reordered within a page")
        try require(CollectorModel.encodedHelperCommand(["action": "review-page", "ids": ids, "padding": String(repeating: "x", count: 600_000)]) == nil,
                    "Oversized commands must fail closed without truncation")

        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":1,"name":"First","phoneCount":0,"phoneEnds":[]}]}"#))
        model.selectedIDs = Set(1...25_001)
        model.review()
        try require(commands.count == 1, "Large selections start with one bounded helper page")
        let reviewID = try emittedReviewID(commands)
        let first = try JSONSerialization.jsonObject(with: commands[0]) as? [String: Any]
        try require(first?["cursor"] as? Int == 0 && first?["total"] as? Int == 25_001 &&
                    (first?["ids"] as? [Int])?.count == 1000,
                    "Large review must preserve exact total and a bounded first page")
        model.receive(try event(#"{"kind":"review-page","reviewId":"00000000-0000-4000-8000-000000000000","nextCursor":1000}"#))
        try require(commands.count == 1, "Stale page acknowledgement cannot advance selection")
        model.receive(try event(#"{"kind":"review-page","reviewId":"\#(reviewID)","nextCursor":1000}"#))
        let second = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any]
        try require(commands.count == 2 && second?["cursor"] as? Int == 1000 &&
                    (second?["ids"] as? [Int])?.count == 1000,
                    "Exact page acknowledgement must advance one bounded selection page")
    }
}
