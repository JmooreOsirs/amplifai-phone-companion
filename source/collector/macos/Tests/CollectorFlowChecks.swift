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
        try AgentEvent.decode(Data(json.utf8))
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
        try SourceDiagnosticChecks.run()
        try architecturePreservesSessionNamespace()
        try optionalSourceReasonCopy()
        try boundedSelectedPayloadContext()
        try pagedContactReview()
        try allPageContactSelection()
        try multiPageAllSelectionReview()
        try localContactSuggestions()
        try mainReviewWorkspace()
        try allGroupWorkspaceActions()
        try scopedSelectionAtScale()
        try reviewedAccountContinuation()
        try singleAgreementStartGates()
        try simplifiedJourneyEndToEnd()
        try destinationDecisionRecovery()
        try staleDestinationEventsCannotClearApprovedRequest()
        try firstReceiptDuringReconnectKeepsOldBinding()
        try selectionChangeWaitsForSafeHelperRevocation()
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

    private static func architecturePreservesSessionNamespace() throws {
        let environment = CollectorModel.helperEnvironment(modulePath: "/synthetic/collector")
        try require(environment["PYTHONPATH"] == "/synthetic/collector",
                    "Helper must retain its bundled collector module path")
        #if arch(x86_64)
        try require(CollectorModel.sessionProductName == "AMPLIFai Phone Intel Test" &&
                    environment["AMPLIFAI_INTEL_CANDIDATE"] == "1",
                    "Intel helper and free-space display must use the isolated Intel sessions")
        #else
        try require(CollectorModel.sessionProductName == "AMPLIFai Phone Candidate" &&
                    environment["AMPLIFAI_INTEL_CANDIDATE"] == nil,
                    "ARM rebuild must retain the accepted sessions namespace")
        #endif
        try require(!SupportDiagnosticReporter.build.isEmpty &&
                    SupportDiagnosticReporter.build == (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String),
                    "Diagnostics must use this executable's actual validated bundle metadata")
        let code = "A1|\(SupportDiagnosticReporter.build)|ABCD1234|connecting|phone_connection|0|0|0|-"
        try require(SupportDiagnosticReporter.request(for: code) != nil,
                    "The architecture's diagnostic build must be accepted locally")
    }

    private static func boundedSelectedPayloadContext() throws {
        let model = CollectorModel()
        model.phase = .processing
        model.receive(try event(#"{"kind":"selected-payload-diagnostic","source":"contacts","component":"database","reason":"selected_payload_length","result":"length_mismatch","declaredBytes":8192,"rawBytes":4112,"copiedBytes":4096,"encrypted":true,"entryKeyPresent":true,"validationCode":"structural_invalid","validationScope":"encrypted_contacts_tables_v1","producerDigest":"mismatched","copyDigest":"unknown","sidecars":"absent"}"#))
        model.receive(try event(#"{"kind":"error","code":"selected_payload_length","stage":"processing"}"#))
        try require(model.failureContext.contains("Contacts · database") &&
                    model.failureContext.contains("8192") && model.failureContext.contains("mismatched"),
                    "Copyable local failure context must retain bounded source and byte evidence")
        try require(!model.failureContext.contains("/Users/") &&
                    !model.safeSupportCode.contains("8192"),
                    "Private paths and payload counts must not enter the support code")
        let rejected = CollectorModel()
        rejected.phase = .processing
        if let invalid = try? event(#"{"kind":"selected-payload-diagnostic","source":"/private/source","component":"database","result":"length_mismatch","declaredBytes":8192,"encrypted":true,"producerDigest":"matched","sidecars":"absent"}"#) {
            rejected.receive(invalid)
        }
        try require(rejected.selectedPayloadContext.isEmpty,
                    "Unexpected source identities must never enter the local error view")
    }

    private static func localContactSuggestions() throws {
        try require(ContactSuggestionKind.matches("Prince") == [], "Name markers must be whole words")
        try require(ContactSuggestionKind.matches("Alex 3") == [], "A digit is not an emoji suggestion")
        try require(ContactSuggestionKind.matches("Acme LLC") == [.organization], "Only a visible name marker suggests an organization")
        try require(ContactSuggestionKind.matches("Alex 😀") == [.emoji], "Emoji names are suggestions, not exclusions")
        try require(ContactSuggestionKind.matches(" Jane  Smith ") == [.formatting], "Whitespace reasons must be grounded")

        func rows(_ start: Int, _ end: Int) -> String {
            (start...end).map { id -> String in
                let name = id == 1 ? "Acme LLC" : id == 201 ? "Alex 😀" : id == 401 ? " Jane  Smith " : "Person \(id)"
                return #"{"id":\#(id),"name":"\#(name)","phoneCount":0,"phoneEnds":[]}"#
            }.joined(separator: ",")
        }
        func response(_ scanID: String, _ start: Int, _ end: Int, _ next: Int?) throws -> AgentEvent {
            let nextValue = next.map(String.init) ?? "null"
            return try event(#"{"kind":"contacts","scanId":"\#(scanID)","query":"","cursor":\#(start - 1),"totalContacts":401,"nextCursor":\#(nextValue),"contacts":[\#(rows(start,end))]}"#)
        }
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":401,"nextCursor":200,"contacts":[\#(rows(1,200))]}"#))
        model.selectedIDs = [1, 2, 201, 401]
        model.scanContactSuggestions()
        guard let first = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any],
              let scanID = first["scanId"] as? String else { throw Failure.check("Scan must carry a generation token") }
        try require(model.suggestionScanning && !model.suggestionsComplete, "Partial pages cannot be presented as complete")
        model.receive(try response(scanID, 1, 200, 200))
        try require(model.suggestionVisited == 200 && model.selectedIDs.count == 4, "Scanning must be read-only")
        model.receive(try response("00000000-0000-4000-8000-000000000000", 201, 400, 400))
        try require(model.suggestionVisited == 200, "A stale scan response must not advance coverage")
        model.receive(try response(scanID, 201, 400, 400))
        model.receive(try response(scanID, 401, 401, nil))
        try require(model.suggestionsComplete && model.suggestionVisited == 401 &&
                    model.suggestionMatchCount(.organization) == 1 &&
                    model.suggestionMatchCount(.emoji) == 1 &&
                    model.suggestionMatchCount(.formatting) == 1,
                    "All immutable pages must contribute exact suggestion counts")
        try require(model.suggestionAffectedCount(.organization) == 1 && model.selectedIDs.count == 4,
                    "A suggestion must not deselect contacts automatically")
        model.deselectSuggestionGroup(.organization)
        try require(model.selectedIDs.count == 4, "A group must be inspected before it can change selection")
        model.inspectSuggestion(.organization)
        guard let inspect = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any],
              let requestID = inspect["requestId"] as? String else { throw Failure.check("Inspection must have a one-use request ID") }
        try require(inspect["action"] as? String == "contact-ids" && inspect["ids"] as? [Int] == [1],
                    "Inspection must request bounded exact matching IDs")
        model.receive(try event(#"{"kind":"contact-ids","requestId":"00000000-0000-4000-8000-000000000000","totalContacts":401,"contacts":[{"id":1,"name":"Wrong","phoneCount":0,"phoneEnds":[]}]}"#))
        try require(model.suggestionPagePending, "A stale inspect reply must not unlock group action")
        model.receive(try event(#"{"kind":"contact-ids","requestId":"\#(requestID)","totalContacts":401,"contacts":[{"id":1,"name":"Acme LLC","phoneCount":0,"phoneEnds":[]}]}"#))
        model.deselectSuggestionGroup(.organization)
        try require(model.selectedIDs == [2, 201, 401] && model.suggestionUndoCount == 1 && model.selectionNeedsReview,
                    "Explicit group deselect must affect only matching selected IDs and invalidate old review")
        model.toggle(201)
        model.undoSuggestionDeselect()
        try require(model.selectedIDs == [1, 2, 401] && model.suggestionUndoCount == 0,
                    "Undo must restore only group-changed IDs, preserving unrelated manual edits")
        model.inspectSuggestion(.formatting)
        let formattingCommand = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        let formattingID = formattingCommand["requestId"] as! String
        model.receive(try event(#"{"kind":"contact-ids","requestId":"\#(formattingID)","totalContacts":401,"contacts":[{"id":401,"name":" Jane  Smith ","phoneCount":0,"phoneEnds":[]}]}"#))
        model.deselectSuggestionGroup(.formatting)
        try require(!model.selectedIDs.contains(401) && model.suggestionUndoCount == 1,
                    "The second group action should replace the single undo token")
        model.toggle(401)
        try require(model.selectedIDs.contains(401) && model.suggestionUndoCount == 0,
                    "A manual override of the same ID must not be undone later")

        var canceledCommands: [Data] = []
        let canceled = CollectorModel(commandWriter: { canceledCommands.append($0) })
        canceled.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":401,"nextCursor":200,"contacts":[\#(rows(1,200))]}"#))
        canceled.selectedIDs = [1]
        canceled.scanContactSuggestions()
        let canceledCommand = try JSONSerialization.jsonObject(with: canceledCommands.last!) as! [String: Any]
        let canceledID = canceledCommand["scanId"] as! String
        canceled.receive(try response(canceledID, 1, 200, 200))
        canceled.cancelSuggestionScan()
        canceled.receive(try response(canceledID, 201, 400, 400))
        try require(!canceled.suggestionsComplete && canceled.suggestionMatches.isEmpty && canceled.selectedIDs == [1],
                    "Cancel must discard partial matches and ignore late pages")
        canceled.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":1,"contacts":[{"id":9,"name":"New review","phoneCount":0,"phoneEnds":[]}]}"#))
        try require(!canceled.suggestionsComplete && canceled.selectedIDs.isEmpty,
                    "Replacement capture must invalidate suggestion generation and old selection")

        var duplicateCommands: [Data] = []
        let duplicate = CollectorModel(commandWriter: { duplicateCommands.append($0) })
        duplicate.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":401,"nextCursor":200,"contacts":[\#(rows(1,200))]}"#))
        duplicate.selectedIDs = [1]
        duplicate.scanContactSuggestions()
        let duplicateCommand = try JSONSerialization.jsonObject(with: duplicateCommands.last!) as! [String: Any]
        let duplicateID = duplicateCommand["scanId"] as! String
        duplicate.receive(try response(duplicateID, 1, 200, 200))
        let duplicateRows = rows(201, 399) + #",{"id":1,"name":"Acme LLC","phoneCount":0,"phoneEnds":[]}"#
        duplicate.receive(try event(#"{"kind":"contacts","scanId":"\#(duplicateID)","query":"","cursor":200,"totalContacts":401,"nextCursor":400,"contacts":[\#(duplicateRows)]}"#))
        try require(duplicate.phase == .error && !duplicate.suggestionsComplete &&
                    duplicate.selectedIDs == [1] && duplicate.suggestionMatches.isEmpty,
                    "A duplicated stable ID across pages must stop inspection without partial group action")

        var mismatchCommands: [Data] = []
        let mismatch = CollectorModel(commandWriter: { mismatchCommands.append($0) })
        mismatch.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":1,"contacts":[{"id":7,"name":"Acme LLC","phoneCount":0,"phoneEnds":[]}]}"#))
        mismatch.selectedIDs = [7]
        mismatch.scanContactSuggestions()
        let mismatchCommand = try JSONSerialization.jsonObject(with: mismatchCommands.last!) as! [String: Any]
        let mismatchID = mismatchCommand["scanId"] as! String
        mismatch.receive(try event(#"{"kind":"contacts","scanId":"\#(mismatchID)","query":"","cursor":0,"totalContacts":1,"contacts":[{"id":7,"name":"Acme LLC","phoneCount":0,"phoneEnds":[]}]}"#))
        mismatch.inspectSuggestion(.organization)
        let inspectCommand = try JSONSerialization.jsonObject(with: mismatchCommands.last!) as! [String: Any]
        let inspectID = inspectCommand["requestId"] as! String
        mismatch.receive(try event(#"{"kind":"contact-ids","requestId":"\#(inspectID)","totalContacts":1,"contacts":[{"id":8,"name":"Wrong","phoneCount":0,"phoneEnds":[]}]}"#))
        try require(mismatch.phase == .error && mismatch.selectedIDs == [7] && !mismatch.suggestionsComplete,
                    "An inspect reply for a different ID must fail closed before deselection")

        let organizationRows = (1...201).map { id in
            #"{"id":\#(id),"name":"Acme LLC \#(id)","phoneCount":0,"phoneEnds":[]}"#
        }
        var inspectionCommands: [Data] = []
        let pagedInspection = CollectorModel(commandWriter: { inspectionCommands.append($0) })
        pagedInspection.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":201,"nextCursor":200,"contacts":[\#(organizationRows.prefix(200).joined(separator: ","))]}"#))
        pagedInspection.scanContactSuggestions()
        let pageScan = try JSONSerialization.jsonObject(with: inspectionCommands.last!) as! [String: Any]
        let pageScanID = pageScan["scanId"] as! String
        pagedInspection.receive(try event(#"{"kind":"contacts","scanId":"\#(pageScanID)","query":"","cursor":0,"totalContacts":201,"nextCursor":200,"contacts":[\#(organizationRows.prefix(200).joined(separator: ","))]}"#))
        pagedInspection.receive(try event(#"{"kind":"contacts","scanId":"\#(pageScanID)","query":"","cursor":200,"totalContacts":201,"contacts":[\#(organizationRows[200])]}"#))
        try require(pagedInspection.suggestionMatchCount(.organization) == 201 &&
                    pagedInspection.suggestionHasMultiplePages(.organization),
                    "Group counts must include a match beyond the first inspection page")
        pagedInspection.inspectSuggestion(.organization)
        let firstInspect = try JSONSerialization.jsonObject(with: inspectionCommands.last!) as! [String: Any]
        try require((firstInspect["ids"] as? [Int])?.count == 200,
                    "The first inspection request must stay within one bounded page")
        let firstInspectID = firstInspect["requestId"] as! String
        pagedInspection.receive(try event(#"{"kind":"contact-ids","requestId":"\#(firstInspectID)","totalContacts":201,"contacts":[\#(organizationRows.prefix(200).joined(separator: ","))]}"#))
        pagedInspection.nextSuggestionPage()
        let lastInspect = try JSONSerialization.jsonObject(with: inspectionCommands.last!) as! [String: Any]
        try require(lastInspect["ids"] as? [Int] == [201],
                    "Next inspection must request only the remaining stable ID")
        let lastInspectID = lastInspect["requestId"] as! String
        pagedInspection.receive(try event(#"{"kind":"contact-ids","requestId":"\#(lastInspectID)","totalContacts":201,"contacts":[\#(organizationRows[200])]}"#))
        try require(pagedInspection.suggestionVisible.count == 1 &&
                    pagedInspection.suggestionPageCursor == 200 && !pagedInspection.hasNextSuggestionPage,
                    "Only the final inspection page may be retained after navigation")
    }

    private static func mainReviewWorkspace() throws {
        func rows(_ first: Int, _ last: Int) -> String {
            (first...last).map { id -> String in
                let name = id == 1 ? "Acme LLC" : id == 201 ? "" :
                    id == 202 ? "Alex 😀" : id == 203 ? " Jane  Smith " : "Person \(id)"
                return #"{"id":\#(id),"name":"\#(name)","phoneCount":1,"phoneEnds":["0000"]}"#
            }.joined(separator: ",")
        }
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":405,"nextCursor":200,"contacts":[\#(rows(1, 200))]}"#))
        model.prepareWorkspace()
        try require(model.workspace == nil && model.suggestionScanning,
                    "An incomplete contact scan cannot expose Main or Review")
        model.selectedIDs = [3]
        model.reviewWorkspace()
        try require(commands.count == 1, "A partial scan cannot send a reviewed selection")
        let first = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        let scanID = first["scanId"] as! String
        model.receive(try event(#"{"kind":"contacts","scanId":"00000000-0000-4000-8000-000000000000","query":"","cursor":0,"totalContacts":405,"nextCursor":200,"contacts":[\#(rows(1, 200))]}"#))
        try require(model.workspace == nil && model.suggestionVisited == 0,
                    "A stale scan generation cannot establish review groups")
        for (cursor, end) in [(0, 200), (200, 400), (400, 405)] {
            let next = end == 405 ? "null" : String(end)
            model.receive(try event(#"{"kind":"contacts","scanId":"\#(scanID)","query":"","cursor":\#(cursor),"totalContacts":405,"nextCursor":\#(next),"contacts":[\#(rows(cursor + 1, end))]}"#))
        }
        guard let workspace = model.workspace else {
            throw Failure.check("The full verified snapshot must establish group counts")
        }
        try require(workspace.mainCount == 403 && workspace.reviewCount == 2 &&
                    !workspace.isInReview(202) && !workspace.isInReview(203),
                    "Only organization clues or missing required details start in Review")
        model.selectAllMain()
        try require(model.selectedIDs.count == 403 && !model.selectedIDs.contains(1) &&
                    !model.selectedIDs.contains(201),
                    "Select all Main includes every verified page and excludes Review")
        model.showContactGroup(.review)
        model.toggle(1)
        model.clearMain()
        try require(model.selectedIDs == [1],
                    "Clear Main must preserve a deliberately included Review contact")
        model.searchWorkspace("Acme")
        try require(model.workspace?.filteredCount == 1 && model.workspace?.visible.first?.id == 1,
                    "Search remains within the selected group without losing stable IDs")
        model.showContactGroup(.main)
        model.searchWorkspace("")
        try require(model.workspace?.hasNextPage == true, "Main workspace must page beyond 200 rows")
        model.nextWorkspacePage()
        try require(model.workspace?.pageStart == 200 && model.workspace?.visible.count == 200,
                    "Main page two must show the correct verified slice")
        model.toggle(203)
        model.moveContact(202, to: .review)
        try require(model.workspace?.reviewCount == 3 && model.canUndoGroupMove,
                    "A user move changes only the local classification")
        model.undoContactMove()
        try require(model.workspace?.reviewCount == 2 && model.selectedIDs == [1, 203],
                    "Undo restores only the moved contact and leaves other manual selections intact")
        model.moveContact(203, to: .review)
        try require(model.selectedIDs == [1] && model.workspace?.reviewCount == 3,
                    "Moving a selected Main contact to Review explicitly removes it from selection")
        model.undoContactMove()
        try require(model.selectedIDs == [1, 203] && model.workspace?.reviewCount == 2,
                    "Undo restores that contact's prior selection without changing the other Review choice")
        model.reviewWorkspace()
        let command = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(command["action"] as? String == "review-page" &&
                    command["ids"] as? [Int] == [1, 203],
                    "Main and manually included Review IDs enter the unchanged immutable review protocol")

        var abortedCommands: [Data] = []
        let aborted = CollectorModel(commandWriter: { abortedCommands.append($0) })
        aborted.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":405,"nextCursor":200,"contacts":[\#(rows(1, 200))]}"#))
        aborted.prepareWorkspace()
        aborted.cancelSuggestionScan()
        aborted.receive(try event(#"{"kind":"contacts","scanId":"\#(scanID)","query":"","cursor":0,"totalContacts":405,"nextCursor":200,"contacts":[\#(rows(1, 200))]}"#))
        try require(aborted.workspace == nil && !aborted.canSelectAllMain && abortedCommands.count == 1,
                    "Cancellation must not publish a partial group or bulk-selection action")
        model.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":1,"contacts":[{"id":900,"name":"Replacement","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        try require(model.workspace == nil && model.selectedIDs.isEmpty && model.selectionNeedsReview,
                    "A replacement capture discards old group and selected-ID authority")
    }

    private static func scopedSelectionAtScale() throws {
        let total = 5_501
        func name(_ id: Int) -> String { id > 4_800 ? "Acme LLC \(id)" : id % 50 == 0 ? "Smith \(id)" : "Person \(id)" }
        let rows = (1...total).map { #"{"id":\#($0),"name":"\#(name($0))","phoneCount":1,"phoneEnds":["0000"]}"# }
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":\#(total),"nextCursor":200,"contacts":[\#(rows.prefix(200).joined(separator: ","))]}"#))
        model.prepareWorkspace()
        let scan = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        let scanID = scan["scanId"] as! String
        for cursor in stride(from: 0, to: total, by: 200) {
            let end = min(cursor + 200, total)
            let next = end == total ? "null" : String(end)
            if cursor == 5_400 {
                try require(model.workspace == nil && model.selectedIDs.isEmpty && model.suggestionVisited == 5_400,
                            "The partial last page must verify before the full workspace or default selection is exposed")
            }
            model.receive(try event(#"{"kind":"contacts","scanId":"\#(scanID)","query":"","cursor":\#(cursor),"totalContacts":\#(total),"nextCursor":\#(next),"contacts":[\#(rows[cursor..<end].joined(separator: ","))]}"#))
        }
        guard let ready = model.workspace else { throw Failure.check("All 5,501 contacts must build one workspace") }
        try require(ready.knownIDs.count == total && ready.mainCount == 4_800 && ready.reviewCount == 701 &&
                    model.selectedIDs == ready.mainIDs,
                    "5,501 contacts start with all 4,800 Main selected and 701 Review unselected across 28 pages")
        try require(model.canSelectAllVerifiedContacts && model.canClearAllVerifiedContacts,
                    "Include all and clearing everything are available from the default selection")

        // Select all, then uncheck exceptions on the first and last page.
        model.selectAllVerifiedContacts()
        try require(model.selectedIDs.count == total, "Select all covers every page of 5,501 contacts")
        model.showContactGroup(.all)
        model.toggle(7)
        for _ in 0..<27 { model.nextWorkspacePage() }
        try require(model.workspace?.pageStart == 5_400 && model.workspace?.visible.count == 101 &&
                    model.workspace?.visible.first?.id == 5_401 && model.workspace?.visible.last?.id == total &&
                    model.workspace?.hasNextPage == false,
                    "The last page exposes all 101 remaining contacts, including contact 5,501")
        model.toggle(total)
        try require(model.selectedIDs.count == 5_499 && !model.selectedIDs.contains(7) && !model.selectedIDs.contains(total),
                    "Unchecking exceptions after select all removes only those contacts")
        model.previousWorkspacePage()
        try require(model.workspace?.pageStart == 5_200 && model.workspace?.visible.count == 200,
                    "Previous page returns from a partial last page to the preceding full page")
        model.nextWorkspacePage()
        try require(model.workspace?.pageStart == 5_400 && model.selectedIDs.count == 5_499 &&
                    !model.selectedIDs.contains(7) && !model.selectedIDs.contains(total),
                    "Returning to the last page preserves first-page and last-page exclusions")
        model.clearAllVerifiedContacts()
        try require(model.selectedIDs.isEmpty && !model.canClearScopedContacts, "Clear selection empties every page")

        // A group scope covers all of its pages and is labeled.
        model.showContactGroup(.main)
        try require(model.workspace?.isScoped == true && model.workspace?.hasNextPage == true &&
                    model.workspace?.scopeLabel == "Main contacts · 4800 across all pages",
                    "The Main scope is labeled and spans more than one page")
        model.selectScopedContacts()
        try require(model.selectedIDs.count == 4_800 && model.selectedScopedCount == 4_800 &&
                    model.selectedIDs.isDisjoint(with: Set(4_801...total)) && !model.canSelectScopedContacts,
                    "Selecting the Main scope selects all 4,800 Main contacts and no Review contact")
        model.markContactForGroupMove(1)
        model.nextWorkspacePage()
        model.markContactForGroupMove(201)
        try require(model.markedGroupMoveIDs == [1, 201] && model.selectedIDs == ready.mainIDs,
                    "Marks span pages without changing the 4,800-contact import selection")

        // A search scope covers every matching contact on every page.
        model.searchWorkspace("person")
        try require(model.workspace?.filteredCount == 4_704 && model.workspace?.hasNextPage == true &&
                    model.workspace?.scopeLabel == "Main contacts matching “person” · 4704 across all pages" &&
                    model.workspace?.pageStart == 0 && model.markedGroupMoveIDs == [1, 201],
                    "A Main search counts every match, resets its page and preserves group marks")
        model.clearScopedContacts()
        try require(model.selectedIDs.count == 96 && model.selectedIDs.allSatisfy { $0 % 50 == 0 },
                    "Clearing a search scope keeps only selections outside it")
        model.searchWorkspace("SMITH")
        try require(model.workspace?.filteredCount == 96 && model.selectedScopedCount == 96 &&
                    model.markedGroupMoveIDs == [1, 201],
                    "Search ignores case and preserves marks hidden by the current query")
        model.toggle(100)
        try require(model.selectedIDs.count == 95 && !model.selectedIDs.contains(100), "A search helps uncheck one exception")
        model.searchWorkspace("")
        model.showContactGroup(.review)
        try require(model.markedGroupMoveIDs.isEmpty && model.selectedIDs.count == 95,
                    "Changing group clears organizing marks while preserving selected contacts")
        for _ in 0..<3 { model.nextWorkspacePage() }
        try require(model.workspace?.pageStart == 600 && model.workspace?.visible.count == 101 &&
                    model.workspace?.visible.last?.id == total,
                    "Review has its own partial last page without dropping the final contact")
        model.selectScopedContacts()
        try require(model.selectedIDs.count == 796 && model.selectedScopedCount == 701,
                    "Selecting from Review's last page adds all 701 Review contacts to earlier choices")
        let selectionBeforeEmptySearch = model.selectedIDs
        model.searchWorkspace("No such contact")
        model.prepareWorkspace()
        try require(model.workspace?.filteredCount == 0 && model.workspace?.visible.isEmpty == true &&
                    !model.canSelectScopedContacts && !model.canClearScopedContacts &&
                    model.selectedIDs == selectionBeforeEmptySearch,
                    "Empty search and repeated workspace preparation retain selections without reapplying defaults")
        model.searchWorkspace("")
        try require(model.workspace?.pageStart == 0 && model.selectedIDs == selectionBeforeEmptySearch,
                    "Clearing an empty search restores page one without changing the selection")

        // Select all minus exceptions reaches the helper as one exact, multi-page review.
        model.clearAllVerifiedContacts()
        model.selectAllVerifiedContacts()
        model.toggle(7)
        model.toggle(total)
        let expected = (1...total).filter { $0 != 7 && $0 != total }
        model.reviewWorkspace()
        let reviewID = try emittedReviewID(commands)
        for start in stride(from: 0, to: expected.count, by: 1_000) {
            let page = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any]
            let end = min(start + 1_000, expected.count)
            try require(page?["cursor"] as? Int == start && page?["total"] as? Int == expected.count &&
                        (page?["ids"] as? [Int]) == Array(expected[start..<end]),
                        "Review page at \(start) must carry exactly the next selected IDs")
            model.receive(try event(#"{"kind":"review-page","reviewId":"\#(reviewID)","nextCursor":\#(end)}"#))
        }
        model.receive(try reviewEvent(reviewID, ids: expected))
        try require(model.phase == .reviewing && !model.selectionNeedsReview && model.selectedIDs == Set(expected),
                    "The exact 5,499-contact review across six helper pages authorizes the next step")
        model.showContactGroup(.all)
        model.searchWorkspace("Person")
        model.nextWorkspacePage()
        model.previousWorkspacePage()
        try require(model.phase == .reviewing && !model.selectionNeedsReview && model.selectedIDs == Set(expected) &&
                    model.workspace?.pageStart == 0,
                    "Browsing groups, search and pages after local review preserves the reviewed selection")
    }

    private static func allGroupWorkspaceActions() throws {
        let rows = (1...1205).map { id in
            #"{"id":\#(id),"name":"\#(id <= 600 ? "Person" : id <= 1200 ? "Acme LLC" : "Person") \#(id)","phoneCount":1,"phoneEnds":["0000"]}"#
        }
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":1205,"nextCursor":200,"contacts":[\#(rows.prefix(200).joined(separator: ","))]}"#))
        model.prepareWorkspace()
        try require(!model.canSelectAllVerifiedContacts && !model.beginBulkMove(to: .review),
                    "Incomplete pages cannot enable all-contact selection or batch movement")
        let scan = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        let scanID = scan["scanId"] as! String
        for cursor in stride(from: 0, to: rows.count, by: 200) {
            let end = min(cursor + 200, rows.count)
            let next = end == rows.count ? "null" : String(end)
            model.receive(try event(#"{"kind":"contacts","scanId":"\#(scanID)","query":"","cursor":\#(cursor),"totalContacts":1205,"nextCursor":\#(next),"contacts":[\#(rows[cursor..<end].joined(separator: ","))]}"#))
        }
        try require(model.workspace?.mainCount == 605 && model.workspace?.reviewCount == 600 &&
                    model.selectedIDs == model.workspace?.mainIDs,
                    "Main starts selected and Review unselected after all 1,205 stable IDs are verified")
        model.selectAllMain()
        try require(model.selectedIDs.count == 605 && !model.selectedIDs.contains(601),
                    "Main-only action must not silently select Review")
        model.selectAllVerifiedContacts()
        try require(model.selectedIDs.count == 1205 && model.selectedIDs.contains(1205),
                    "Explicit all-contact action covers both groups and the last page")
        model.showContactGroup(.all)
        try require(model.workspace?.filteredCount == 1205 && model.workspace?.hasNextPage == true,
                    "All is a single paged filter over the complete workspace")
        model.clearAllVerifiedContacts()
        try require(model.selectedIDs.isEmpty, "Clear all removes only import selection")
        model.showContactGroup(.main)
        model.searchWorkspace("Person")
        model.nextWorkspacePage()
        model.selectAllVerifiedContacts()
        try require(model.selectedIDs.count == 1205 && model.selectedIDs.contains(601),
                    "Explicit all-contact action ignores active Main search and page without truncating Review")
        model.clearAllVerifiedContacts()
        model.showContactGroup(.review)
        model.selectAllReview()
        try require(model.selectedIDs.count == 600 && !model.selectedIDs.contains(1205),
                    "Review-only action chooses every Review page")
        model.clearReview()
        try require(model.selectedIDs.isEmpty, "Review-only clear restores empty import choice")
        model.showContactGroup(.main)
        model.markContactForGroupMove(1)
        model.nextWorkspacePage()
        model.markContactForGroupMove(201)
        try require(model.markedGroupMoveIDs == [1, 201] && model.selectedIDs.isEmpty,
                    "Separate group marks persist across pages without changing import selection")
        try require(model.beginMarkedBulkMove(to: .review) && model.pendingBulkMoveCount == 2,
                    "Marked movement binds exactly two stable IDs")
        model.markContactForGroupMove(201)
        try require(!model.confirmBulkMove() && model.markedGroupMoveIDs == [1],
                    "Changing marked IDs after preparation revokes that exact batch")
        model.markContactForGroupMove(201)
        try require(model.beginMarkedBulkMove(to: .review), "Current marks can be prepared again")
        model.cancelBulkMove()
        try require(model.markedGroupMoveIDs == [1, 201] && !model.confirmBulkMove(),
                    "Cancel preserves group marks but changes no classification")
        try require(model.beginMarkedBulkMove(to: .review) && model.confirmBulkMove() &&
                    model.workspace?.reviewCount == 602 && model.markedGroupMoveIDs.isEmpty,
                    "Confirmed marked move applies both pages atomically")
        model.undoContactMove()
        try require(model.workspace?.reviewCount == 600 && model.selectedIDs.isEmpty,
                    "Marked-group Undo restores both prior memberships without import changes")
        model.showContactGroup(.review)
        model.searchWorkspace("Acme")
        try require(model.beginBulkMove(to: .main) && model.pendingBulkMoveCount == 600,
                    "Batch scope includes all matching Review pages")
        model.cancelBulkMove()
        try require(!model.confirmBulkMove() && model.workspace?.reviewCount == 600,
                    "Cancel must preserve the prior group and selection")
        try require(model.beginBulkMove(to: .main), "A current batch can be prepared again")
        model.searchWorkspace("Person")
        try require(!model.confirmBulkMove() && model.workspace?.reviewCount == 600,
                    "Changing the search before confirmation must fail closed")
        model.searchWorkspace("Acme")
        try require(model.beginBulkMove(to: .main) && model.confirmBulkMove() &&
                    model.workspace?.reviewCount == 0 && model.selectedIDs.isEmpty,
                    "Confirmed batch changes classification without choosing import rows")
        model.undoContactMove()
        try require(model.workspace?.reviewCount == 600 && model.selectedIDs.isEmpty,
                    "One Undo restores all moved Review IDs")
        model.showContactGroup(.main)
        model.searchWorkspace("Person")
        model.selectAllMain()
        model.markAllMatchingForGroupMove()
        try require(model.markedGroupMoveIDs.count == 605 && model.selectedIDs.count == 605,
                    "Mark-all uses every matching page while keeping import selection separate")
        try require(model.beginBulkMove(to: .review) && model.pendingBulkMoveCount == 605 && model.confirmBulkMove(),
                    "Main batch includes every matching page")
        try require(model.workspace?.mainCount == 0 && model.selectedIDs.isEmpty,
                    "Moving chosen Main entries to Review removes their import selection")
        model.toggle(601)
        model.undoContactMove()
        try require(model.workspace?.mainCount == 605 && model.selectedIDs.count == 606 &&
                    model.selectedIDs.contains(601) && model.selectedIDs.contains(1205),
                    "Undo restores only affected IDs and preserves an unrelated manual choice")
        model.reviewWorkspace()
        let review = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(review["action"] as? String == "review-page" &&
                    (review["ids"] as? [Int])?.count == 606,
                    "Changed bulk selection still enters the bound immutable review protocol")
        model.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":1,"contacts":[{"id":9000,"name":"Replacement","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        try require(model.workspace == nil && !model.confirmBulkMove() && !model.canUndoGroupMove,
                    "Replacement revokes pending batch and Undo authority")

        let replaced = CollectorModel(commandWriter: { _ in })
        replaced.receive(try event(#"{"kind":"capture","contacts":[{"id":1,"name":"Person One","phoneCount":1,"phoneEnds":["0000"]},{"id":2,"name":"Person Two","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        replaced.prepareWorkspace()
        try require(replaced.beginBulkMove(to: .review), "A current complete generation may prepare a batch")
        replaced.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Replacement","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        try require(!replaced.confirmBulkMove() && replaced.workspace == nil,
                    "A new source generation cannot commit an old pending group move")

        let affected = CollectorModel(commandWriter: { _ in })
        affected.receive(try event(#"{"kind":"capture","contacts":[{"id":1,"name":"Person One","phoneCount":1,"phoneEnds":["0000"]},{"id":2,"name":"Person Two","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        affected.prepareWorkspace()
        affected.selectAllMain()
        affected.moveContact(1, to: .review)
        affected.toggle(1)
        try require(!affected.canUndoGroupMove && affected.selectedIDs == [1, 2],
                    "An affected ID's later manual choice cannot be overwritten by group Undo")
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

    private static func reviewedAccountContinuation() throws {
        let handoff = "00000000-0000-4000-8000-0000000000aa"
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic Person","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        model.prepareWorkspace()
        model.selectAllMain()
        model.reviewWorkspace(continueToAccount: true)
        let reviewID = try emittedReviewID(commands)
        try require(model.accountNavigationID == nil, "The account must not open before immutable review confirmation")
        model.receive(try reviewEvent(reviewID, ids: [3]))
        let pair = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(pair["action"] as? String == "pair" && model.accountNavigationID == nil,
                    "Confirmed review may request a one-use local pairing but cannot navigate before a valid pairing response")
        model.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48752,"expiresInSeconds":300,"handoffId":"\#(handoff)"}"#))
        try require(model.accountNavigationID == nil && model.handoffID.isEmpty &&
                    model.selectedIDs == [3] && !model.handoffAcknowledged,
                    "A wrong local port must revoke automatic navigation without losing the reviewed selection")
        let revoke = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(revoke["action"] as? String == "revoke", "An invalid automatic pairing must revoke the helper handoff")
        model.reviewWorkspace(continueToAccount: true)
        let retryID = try emittedReviewID(commands)
        model.receive(try reviewEvent(retryID, ids: [3]))
        model.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"expiresInSeconds":300,"handoffId":"\#(handoff)"}"#))
        try require(model.accountNavigationID != nil && model.handoffID == handoff &&
                    model.pairCode == "1234567890" && !model.handoffAcknowledged,
                    "Only an exact one-use pairing can request account navigation; navigation is not account Save")
        model.accountNavigationFailed()
        try require(model.errorMessage.contains("Open AMPLIFai account") && model.selectedIDs == [3],
                    "Browser launch failure must preserve manual recovery and selected IDs")
        model.receive(try event(#"{"kind":"handoff","state":"expired","handoffId":"\#(handoff)"}"#))
        try require(model.accountNavigationID == nil && model.pairCode.isEmpty && model.selectedIDs == [3],
                    "Expiry must clear navigation and code while retaining the local unsaved review")
    }

    /// Agree step: one Start action still needs a finished read-only check, the owner's checkbox and this run.
    private static func singleAgreementStartGates() throws {
        let model = CollectorModel()
        try require(model.settingCallHistoryPreparation == nil &&
                    !model.agreeAndStartCollection(for: model.collectionApprovalRunID, checked: true) &&
                    model.localCollectionApproval == nil && model.phase == .idle && model.collectionStartedAt == nil,
                    "Start must wait for the read-only iPhone check")
        try require(model.beginBackupEncryptionStatusCheck(), "The iPhone check must begin")
        try require(model.settingCallHistoryPreparation == nil &&
                    !model.agreeAndStartCollection(for: model.collectionApprovalRunID, checked: true) &&
                    model.collectionStartedAt == nil,
                    "A pending check cannot start collection")
        model.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":true}"#))
        try require(model.settingCallHistoryPreparation == .confirmedEncryptedBackup,
                    "Encryption On selects encrypted call coverage without a second question")
        try require(!model.agreeAndStartCollection(for: UUID(), checked: true) && model.localCollectionApproval == nil &&
                    model.collectionStartedAt == nil,
                    "An unrelated run cannot start")
        let run = model.collectionApprovalRunID
        try require(!model.agreeAndStartCollection(for: run, checked: false) && model.localCollectionApproval == nil &&
                    model.collectionApprovalRunID != run && model.backupEncryptionStatus == .notChecked &&
                    model.collectionStartedAt == nil,
                    "An unchecked agreement declines and requires a fresh check")
        try require(model.beginBackupEncryptionStatusCheck(), "A fresh run must check again")
        model.receive(try event(#"{"kind":"backup-encryption-status","backupEncrypted":false}"#))
        try require(model.settingCallHistoryPreparation == .continueWithoutConfirmedCoverage,
                    "Encryption Off reads available data only")
        model.declineLocalCollection()
        try require(model.beginBackupEncryptionStatusCheck(), "Another fresh run must check again")
        model.receive(try event(#"{"kind":"error","code":"phone_connection"}"#))
        try require(model.backupEncryptionStatus == .unknown &&
                    model.settingCallHistoryPreparation == .continueWithoutConfirmedCoverage,
                    "An unconfirmed setting can only start without confirmed call history")
        try require(model.pairCode.isEmpty && !model.handoffAcknowledged && model.accountNavigationID == nil,
                    "Collection agreement cannot pair a browser, navigate or save")
        try require(model.journeyStep == 0, "Checking the iPhone is still the Agree step")
        let stepping = CollectorModel()
        for (phase, step) in [(CollectorModel.Phase.connecting, 1), (.transferring, 1), (.passwordRequired, 1),
                              (.processing, 1), (.cancelling, 1), (.selecting, 2), (.reviewing, 3),
                              (.completing, 3), (.completed, 4), (.error, 0), (.idle, 0)] {
            stepping.phase = phase
            try require(stepping.journeyStep == step, "Journey step for \(phase) must be \(step)")
        }
    }

    /// One sheet decision at a time, for Share and Not this account, with lost or rejected helper answers.
    private static func destinationDecisionRecovery() throws {
        final class Sent { var commands: [Data] = [] }
        let handoff = "00000000-0000-4000-8000-0000000000aa"
        let intent = "00000000-0000-4000-8000-0000000000bb"
        func awaitingDecision() throws -> (CollectorModel, Sent, CollectorModel.DestinationApprovalRequest) {
            let sent = Sent()
            let model = CollectorModel(commandWriter: { sent.commands.append($0) })
            model.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic Person","phoneCount":1,"phoneEnds":["0000"]}]}"#))
            model.selectedIDs = [3]
            model.review()
            model.receive(try reviewEvent(try emittedReviewID(sent.commands), ids: [3]))
            model.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"handoffId":"\#(handoff)"}"#))
            model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(handoff)","intentId":"\#(intent)","email":"synthetic@example.invalid","course":"Synthetic course"}"#))
            guard let request = model.pendingDestinationApproval else { throw Failure.check("A destination must await the owner") }
            return (model, sent, request)
        }
        func received(_ state: String, _ id: String = handoff) throws -> AgentEvent {
            try event(#"{"kind":"handoff","state":"\#(state)","handoffId":"\#(id)"}"#)
        }

        // Not this account cannot be sent twice, and its acknowledgement closes the request.
        let (declined, declinedSent, declinedRequest) = try awaitingDecision()
        declined.decideAccountDestination(false, request: declinedRequest)
        let afterDecline = declinedSent.commands.count
        try require(declined.submittedDestinationDecision == false && declined.pendingDestinationApproval == nil,
                    "A submitted Not this account shows Declining and offers no second decision")
        declined.decideAccountDestination(false, request: declinedRequest)
        declined.decideAccountDestination(true, request: declinedRequest)
        try require(declinedSent.commands.count == afterDecline, "Neither choice can be sent again while one is pending")
        declined.receive(try received("received"))
        try require(!declined.destinationApproved, "A receipt cannot turn a submitted decline into approval")
        declined.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        try require(declined.submittedDestinationDecision == nil && declined.destinationIntentID.isEmpty && !declined.destinationApproved,
                    "The denied acknowledgement clears the pending choice and closes the request")

        // A rejected or lost confirmation must not strand Confirming; the owner can decide once more.
        let (unconfirmed, _, unconfirmedRequest) = try awaitingDecision()
        unconfirmed.decideAccountDestination(true, request: unconfirmedRequest)
        unconfirmed.receive(try event(#"{"kind":"error","code":"handoff_unconfirmed"}"#))
        try require(unconfirmed.submittedDestinationDecision == nil && unconfirmed.phase == .reviewing &&
                    unconfirmed.selectedIDs == [3] && unconfirmed.pendingDestinationApproval == unconfirmedRequest,
                    "handoff_unconfirmed clears Confirming, keeps the review and allows one fresh decision")

        // Share, approval answer lost, handoff_unconfirmed, then a matching receipt: the recorded Share still reconciles.
        let (late, _, lateRequest) = try awaitingDecision()
        late.decideAccountDestination(true, request: lateRequest)
        late.receive(try event(#"{"kind":"error","code":"handoff_unconfirmed"}"#))
        try require(late.submittedDestinationDecision == nil && late.destinationShareUnconfirmed && !late.destinationApproved,
                    "After handoff_unconfirmed the page says sharing has not been confirmed, not that nothing was shared")
        late.receive(try received("received"))
        try require(late.destinationApproved && late.browserReceived && late.pendingDestinationApproval == nil &&
                    !late.destinationShareUnconfirmed && !late.handoffAcknowledged,
                    "A later matching receipt reconciles the recorded Share without offering it again or counting a save")

        // A lost approved acknowledgement is reconciled only by a matching receipt after the owner's Share.
        let (lost, _, lostRequest) = try awaitingDecision()
        lost.decideAccountDestination(true, request: lostRequest)
        lost.receive(try received("received", "00000000-0000-4000-8000-0000000000ff"))
        try require(!lost.destinationApproved && lost.submittedDestinationDecision == true,
                    "A receipt for another handoff changes nothing")
        lost.receive(try received("received"))
        try require(lost.destinationApproved && lost.submittedDestinationDecision == nil && lost.browserReceived &&
                    !lost.handoffAcknowledged && !lost.accountSaveAwaitingBrowserReceipt,
                    "A matching receipt confirms the Share choice but is not a save")
        let (lostSave, _, lostSaveRequest) = try awaitingDecision()
        lostSave.decideAccountDestination(true, request: lostSaveRequest)
        lostSave.receive(try received("saved_pending_browser_receipt"))
        try require(lostSave.destinationApproved && lostSave.accountSaveAwaitingBrowserReceipt && !lostSave.handoffAcknowledged,
                    "A first save receipt reconciles the Share choice and still waits for the final receipt")
        let (undecided, _, _) = try awaitingDecision()
        undecided.receive(try received("received"))
        try require(!undecided.destinationApproved && undecided.pendingDestinationApproval != nil,
                    "Without the owner's Share, a receipt never approves the destination")
    }

    /// Synthetic Choose → Share → saved-report path, plus cancellation and recovery, with no helper or browser.
    private static func simplifiedJourneyEndToEnd() throws {
        let total = 450
        // Contacts 401...450 have no supported phone number, so they start in Review.
        let rows = (1...total).map { id -> String in
            #"{"id":\#(id),"name":"Synthetic Person \#(id)","phoneCount":\#(id > 400 ? 0 : 1),"phoneEnds":[\#(id > 400 ? "" : #""0000""#)]}"#
        }
        func loaded(_ model: CollectorModel, lastCommand: () -> Data?) throws {
            model.receive(try event(#"{"kind":"capture","query":"","cursor":0,"totalContacts":\#(total),"nextCursor":200,"contacts":[\#(rows.prefix(200).joined(separator: ","))]}"#))
            model.prepareWorkspace()
            let scan = try JSONSerialization.jsonObject(with: lastCommand()!) as! [String: Any]
            let scanID = scan["scanId"] as! String
            for cursor in stride(from: 0, to: total, by: 200) {
                let end = min(cursor + 200, total)
                let next = end == total ? "null" : String(end)
                model.receive(try event(#"{"kind":"contacts","scanId":"\#(scanID)","query":"","cursor":\#(cursor),"totalContacts":\#(total),"nextCursor":\#(next),"contacts":[\#(rows[cursor..<end].joined(separator: ","))]}"#))
            }
        }
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        try loaded(model) { commands.last }
        guard let workspace = model.workspace else { throw Failure.check("The synthetic workspace must build") }
        try require(workspace.mainCount == 400 && workspace.reviewCount == 50 && model.selectedIDs == workspace.mainIDs,
                    "Choose opens with the 400 Main contacts selected and 50 Review contacts waiting")
        try require(model.journeyStep == 2, "Choose is the current step once contacts are ready")

        // Select all, individual exceptions, then a partial scope.
        model.selectAllVerifiedContacts()
        try require(model.selectedIDs.count == total, "Include all covers every page")
        model.toggle(2)
        model.toggle(450)
        try require(model.selectedIDs.count == total - 2 && !model.selectedIDs.contains(2) && !model.selectedIDs.contains(450),
                    "Individual exceptions remove only those contacts")
        model.showContactGroup(.review)
        model.clearScopedContacts()
        try require(model.selectedIDs.count == 399 && model.selectedReviewCount == 0 && model.selectedScopedCount == 0,
                    "Deselect all in Review removes only the Review scope")
        model.showContactGroup(.main)
        model.markContactForGroupMove(5)
        try require(model.markedGroupMoveIDs == [5] && model.selectedIDs.contains(5) && model.selectedIDs.count == 399,
                    "A move mark is not an inclusion choice")
        model.clearGroupMoveMarks()

        // Share: the reviewed selection pairs once, then account navigation is requested.
        let shared = model.selectedIDs.sorted()
        model.reviewWorkspace(continueToAccount: true)
        let reviewID = try emittedReviewID(commands)
        for cursor in stride(from: 0, to: shared.count, by: 1000) {
            model.receive(try event(#"{"kind":"review-page","reviewId":"\#(reviewID)","nextCursor":\#(min(cursor + 1000, shared.count))}"#))
        }
        try require(model.accountNavigationID == nil, "Nothing opens before the local review is confirmed")
        model.receive(try event(#"{"kind":"review","reviewId":"\#(reviewID)","selected_contacts":\#(shared.count),"selectionSha256":"\#(CollectorModel.selectionDigest(shared))","matched_calls":12,"matched_messages":34}"#))
        try require(model.phase == .reviewing && model.matchedCalls == 12 && model.matchedMessages == 34,
                    "The local review summary keeps its matching call and message counts")
        let pair = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(pair["action"] as? String == "pair", "Share requests one one-time link after review")
        let handoff = "00000000-0000-4000-8000-0000000000aa"
        let intent = "00000000-0000-4000-8000-0000000000bb"
        model.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"expiresInSeconds":300,"handoffId":"\#(handoff)"}"#))
        try require(model.accountNavigationID != nil && !model.canEditWorkspace && !model.handoffAcknowledged,
                    "A valid one-time link opens the account and locks the selection, without saving")
        let beforeDestination = commands.count
        model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(handoff)","intentId":"\#(intent)","email":"synthetic@example.invalid","course":"Synthetic course"}"#))
        guard let destination = model.pendingDestinationApproval else {
            throw Failure.check("The account and course must wait for confirmation on this Mac")
        }
        try require(commands.count == beforeDestination && !model.destinationApproved,
                    "No destination decision is sent without the owner")
        try require(!model.browserHandoffReady, "The browser waits while the Mac asks for the account confirmation")
        model.decideAccountDestination(true, request: destination)
        let decision = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(decision["action"] as? String == "destination-decision" && decision["approved"] as? Bool == true,
                    "Only the owner's confirmation approves the destination")
        let afterDecision = commands.count
        try require(model.destinationDecisionPending && model.pendingDestinationApproval == nil && !model.destinationApproved,
                    "After Share, the page shows Confirming and no second decision is offered")
        model.decideAccountDestination(true, request: destination)
        try require(commands.count == afterDecision, "A second decision for the same request is never sent")
        model.receive(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(intent)"}"#))
        try require(model.destinationApproved && !model.destinationDecisionPending,
                    "The helper's acknowledgement ends Confirming and starts Sending")
        try require(!model.browserHandoffReady, "The Mac keeps the screen until the browser has the preview")
        model.receive(try event(#"{"kind":"handoff","state":"received","handoffId":"\#(handoff)"}"#))
        try require(model.browserHandoffReady, "After the confirmation and the receipt, the Mac hands over to the website")
        model.receive(try event(#"{"kind":"handoff","state":"saved_pending_browser_receipt","handoffId":"\#(handoff)"}"#))
        try require(model.accountSaveAwaitingBrowserReceipt && !model.handoffAcknowledged && model.phase == .reviewing,
                    "A first save receipt still waits for the browser's final receipt")
        try require(model.journeyStep == 3, "Share & report is the current step until the save is confirmed")
        model.receive(try event(#"{"kind":"handoff","state":"saved","handoffId":"\#(handoff)"}"#))
        try require(model.handoffAcknowledged && model.phase == .completing,
                    "The matching final receipt finishes the run")
        try require(!model.browserHandoffReady, "A confirmed save ends the handoff")
        model.finished(mode: "connect", code: 0)
        try require(model.phase == .completed && model.contacts.isEmpty && model.selectedIDs.isEmpty &&
                    model.statusTitle == "Saved in your account" && model.statusDetail.contains("report is ready"),
                    "Completion clears the private review and points to the saved report")
        try require(model.journeyStep == 4, "Every step shows finished only after the confirmed save")

        // Cancellation: discarding during Choose clears the private review.
        var cancelCommands: [Data] = []
        let cancelled = CollectorModel(commandWriter: { cancelCommands.append($0) })
        try loaded(cancelled) { cancelCommands.last }
        cancelled.disconnect()
        try require(cancelled.phase == .idle && cancelled.selectedIDs.isEmpty && cancelled.contacts.isEmpty &&
                    !cancelled.hasUnsavedReview,
                    "Discarding during Choose clears the local review")

        // Recovery: a revoked link keeps the review and names the current Share action.
        var recoveryCommands: [Data] = []
        let recovery = CollectorModel(commandWriter: { recoveryCommands.append($0) })
        try loaded(recovery) { recoveryCommands.last }
        let kept = recovery.selectedIDs.sorted()
        recovery.reviewWorkspace(continueToAccount: false)
        let recoveryReviewID = try emittedReviewID(recoveryCommands)
        recovery.receive(try event(#"{"kind":"review-page","reviewId":"\#(recoveryReviewID)","nextCursor":\#(kept.count)}"#))
        recovery.receive(try event(#"{"kind":"review","reviewId":"\#(recoveryReviewID)","selected_contacts":\#(kept.count),"selectionSha256":"\#(CollectorModel.selectionDigest(kept))"}"#))
        recovery.pairBrowser()
        recovery.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"expiresInSeconds":300,"handoffId":"\#(handoff)"}"#))
        recovery.requestSelectionChange()
        recovery.receive(try event(#"{"kind":"state","state":"pairing_revoked"}"#))
        try require(recovery.handoffID.isEmpty && recovery.selectedIDs == Set(kept) &&
                    recovery.handoffNotice.contains("Continue to account") && !recovery.handoffNotice.contains("Save and continue"),
                    "A revoked link keeps the review and names the current Share action, not stale wording")
        recovery.reviewWorkspace(continueToAccount: true)
        let retryID = try emittedReviewID(recoveryCommands)
        recovery.receive(try event(#"{"kind":"review-page","reviewId":"\#(retryID)","nextCursor":\#(kept.count)}"#))
        recovery.receive(try event(#"{"kind":"review","reviewId":"\#(retryID)","selected_contacts":\#(kept.count),"selectionSha256":"\#(CollectorModel.selectionDigest(kept))"}"#))
        recovery.receive(try event(#"{"kind":"pairing","pairCode":"bad","port":48751,"expiresInSeconds":300,"handoffId":"\#(handoff)"}"#))
        try require(recovery.errorMessage.contains("Continue to account") && !recovery.errorMessage.contains("Save and continue") &&
                    recovery.selectedIDs == Set(kept) && recovery.accountNavigationID == nil && !recovery.accountLinkPending,
                    "A failed automatic link keeps the review and names the current recovery action")
        // The share panel's Continue to account retries the same one-time link and navigation path.
        recovery.pairBrowser(automaticAccountNavigation: true)
        try require(recovery.accountLinkPending, "Continue to account requests one new link")
        recovery.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"expiresInSeconds":300,"handoffId":"\#(handoff)"}"#))
        try require(!recovery.accountLinkPending && recovery.accountNavigationID != nil && recovery.handoffID == handoff &&
                    !recovery.handoffAcknowledged && recovery.pendingDestinationApproval == nil,
                    "A retried link opens the account but still needs the account confirmation on this Mac")
    }

    private static func staleDestinationEventsCannotClearApprovedRequest() throws {
        let handoff = "00000000-0000-4000-8000-0000000000aa"
        let nextHandoff = "00000000-0000-4000-8000-0000000000dd"
        let current = "00000000-0000-4000-8000-0000000000bb"
        let stale = "00000000-0000-4000-8000-0000000000cc"
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic Person","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        model.selectedIDs = [3]
        model.review()
        model.receive(try reviewEvent(try emittedReviewID(commands), ids: [3]))
        model.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"handoffId":"\#(handoff)"}"#))
        model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(handoff)","intentId":"\#(current)","email":"test@example.invalid","course":"Synthetic course"}"#))
        guard let displayed = model.pendingDestinationApproval else {
            throw Failure.check("The current verified destination must be reviewable")
        }
        let beforeStaleAction = commands.count
        let wrongDisplay = CollectorModel.DestinationApprovalRequest(handoffID: handoff, intentID: stale,
                                                                      email: "other@example.invalid", course: "Other")
        model.decideAccountDestination(true, request: wrongDisplay)
        try require(commands.count == beforeStaleAction, "An older or wrong displayed account cannot approve the live intent")
        model.decideAccountDestination(true, request: displayed)
        let approval = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(approval["action"] as? String == "destination-decision" &&
                    approval["handoffId"] as? String == handoff && approval["intentId"] as? String == current &&
                    approval["approved"] as? Bool == true,
                    "Approval must name only the exact displayed handoff and intent")
        model.receive(try event(#"{"kind":"destination","state":"approved","handoffId":"\#(handoff)","intentId":"\#(current)"}"#))
        try require(model.pendingDestinationApproval == nil && model.destinationApproved,
                    "Approval must dismiss the prompt while retaining its bound destination")
        model.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(handoff)","intentId":"\#(stale)"}"#))
        model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(handoff)","intentId":"\#(stale)","email":"other@example.invalid","course":"Other"}"#))
        try require(model.destinationIntentID == current && model.destinationApproved,
                    "Late denied or pending events cannot clear or replace the approved destination")
        model.receive(try event(#"{"kind":"handoff","state":"received","handoffId":"\#(handoff)"}"#))
        try require(model.statusTitle.contains("reviewed preview") && !model.handoffAcknowledged,
                    "A received local preview is distinct from the final account-save ACK")
        model.receive(try event(#"{"kind":"handoff","state":"saved_pending_browser_receipt","handoffId":"\#(handoff)"}"#))
        try require(model.accountSaveAwaitingBrowserReceipt && !model.handoffAcknowledged &&
                    model.statusDetail.contains("final receipt"),
                    "A first native save receipt must remain pending until the browser confirms it")
        let beforeReceiptReconnect = commands.count
        model.pairBrowser()
        try require(commands.count == beforeReceiptReconnect && model.accountSaveAwaitingBrowserReceipt &&
                    model.handoffID == handoff,
                    "Reconnect cannot replace the exact bridge after its first bound account-save receipt")
        model.receive(try event(#"{"kind":"handoff","state":"expired","handoffId":"\#(handoff)"}"#))
        try require(model.pendingDestinationApproval == nil && model.selectedIDs == [3] && !model.handoffAcknowledged,
                    "Expiry clears the prompt and preserves the reviewed selection")
        let beforeExpiredAction = commands.count
        model.decideAccountDestination(true, request: displayed)
        try require(commands.count == beforeExpiredAction, "Expired dialog buttons cannot approve a later pairing")
        model.pairBrowser()
        let reconnect = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any]
        try require(model.selectedIDs == [3] && !model.selectionNeedsReview &&
                    reconnect?["action"] as? String == "pair",
                    "Reconnect must request a new local handoff from the retained review without recollection")
        model.receive(try event(#"{"kind":"pairing","pairCode":"9876543210","port":48751,"handoffId":"\#(nextHandoff)"}"#))
        model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(nextHandoff)","intentId":"\#(stale)","email":"new@example.invalid","course":"Next"}"#))
        let beforeOldPrompt = commands.count
        model.decideAccountDestination(true, request: displayed)
        try require(commands.count == beforeOldPrompt && model.pendingDestinationApproval?.intentID == stale,
                    "A button from an old prompt cannot approve a new handoff")
        if let next = model.pendingDestinationApproval { model.decideAccountDestination(false, request: next) }
        model.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(nextHandoff)","intentId":"\#(stale)"}"#))
        try require(model.pendingDestinationApproval == nil && model.selectedIDs == [3],
                    "A current denial clears only that prompt and preserves the review")
        model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(nextHandoff)","intentId":"\#(stale)","email":"new@example.invalid","course":"Next"}"#))
        try require(model.pendingDestinationApproval == nil,
                    "A delayed pending event cannot resurrect an explicitly denied intent")
        model.receive(try event(#"{"kind":"destination","state":"pending","handoffId":"\#(nextHandoff)","intentId":"\#(current)","email":"third@example.invalid","course":"Next"}"#))
        try require(model.pendingDestinationApproval?.intentID == current,
                    "A separate server-confirmed intent may be presented after denial")
        model.receive(try event(#"{"kind":"destination","state":"denied","handoffId":"\#(nextHandoff)","intentId":"\#(stale)"}"#))
        try require(model.pendingDestinationApproval?.intentID == current,
                    "A late denial cannot clear the newer prompt on the same local handoff")
    }

    private static func firstReceiptDuringReconnectKeepsOldBinding() throws {
        let handoff = "00000000-0000-4000-8000-0000000000aa"
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        model.selectedIDs = [3]
        model.review()
        model.receive(try reviewEvent(try emittedReviewID(commands), ids: [3]))
        model.pairBrowser()
        model.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"handoffId":"\#(handoff)"}"#))
        model.receive(try event(#"{"kind":"handoff","state":"received","handoffId":"\#(handoff)"}"#))
        model.pairBrowser()
        let replacement = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(replacement["action"] as? String == "pair" && model.handoffID == handoff &&
                    model.pairCode == "1234567890" && model.browserReceived,
                    "An attempted replacement keeps the old preview and token binding until the helper confirms it")
        model.receive(try event(#"{"kind":"handoff","state":"saved_pending_browser_receipt","handoffId":"\#(handoff)"}"#))
        try require(model.accountSaveAwaitingBrowserReceipt && model.handoffID == handoff &&
                    model.pairCode == "1234567890",
                    "A first receipt racing Reconnect remains bound to the original browser handoff")
        let beforeRetry = commands.count
        model.pairBrowser()
        try require(commands.count == beforeRetry,
                    "A helper refusal at the first receipt must prevent another Reconnect command")
        model.receive(try event(#"{"kind":"handoff","state":"saved","handoffId":"\#(handoff)"}"#))
        try require(model.handoffAcknowledged && model.phase == .completing,
                    "The old bridge can still deliver its final bound ACK after a refused replacement")

        var failedCommands: [Data] = []
        let failed = CollectorModel(commandWriter: { failedCommands.append($0) })
        failed.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        failed.selectedIDs = [3]
        failed.review()
        failed.receive(try reviewEvent(try emittedReviewID(failedCommands), ids: [3]))
        failed.pairBrowser()
        failed.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"handoffId":"\#(handoff)"}"#))
        failed.pairBrowser()
        failed.receive(try event(#"{"kind":"error","code":"bridge_unavailable"}"#))
        try require(failed.handoffID.isEmpty && failed.pairCode.isEmpty &&
                    failed.selectedIDs == [3] && !failed.selectionNeedsReview,
                    "A failed replacement cannot advertise the now-closed old link or discard the reviewed selection")
    }

    private static func selectionChangeWaitsForSafeHelperRevocation() throws {
        let handoff = "00000000-0000-4000-8000-0000000000dd"
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        model.prepareWorkspace()
        model.selectedIDs = [3]
        model.review()
        model.receive(try reviewEvent(emittedReviewID(commands), ids: [3]))
        model.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"handoffId":"\#(handoff)"}"#))
        try require(!model.canEditWorkspace && model.canRequestSelectionChange,
                    "An active reviewed handoff locks import selection but offers explicit change")
        let beforeEdit = commands.count
        model.toggle(3)
        model.clearAllVerifiedContacts()
        model.review()
        try require(commands.count == beforeEdit && model.selectedIDs == [3] &&
                    model.handoffID == handoff && model.canRequestSelectionChange,
                    "Direct edits and review cannot close or mutate an active handoff")
        model.requestSelectionChange()
        let revoke = try JSONSerialization.jsonObject(with: commands.last!) as! [String: Any]
        try require(revoke["action"] as? String == "revoke" && model.selectionChangePending &&
                    model.selectedIDs == [3] && model.handoffID == handoff,
                    "An explicit selection change must wait for a helper-accepted revoke")
        let whilePending = commands.count
        model.pairBrowser()
        model.requestSelectionChange()
        try require(commands.count == whilePending, "Pending revocation cannot race another pairing or revoke")
        model.receive(try event(#"{"kind":"handoff","state":"saved_pending_browser_receipt","handoffId":"\#(handoff)"}"#))
        try require(!model.selectionChangePending && model.accountSaveAwaitingBrowserReceipt &&
                    model.handoffID == handoff && model.selectedIDs == [3] &&
                    model.handoffNotice.contains("finish in the browser") && model.errorMessage.isEmpty,
                    "A first account receipt racing revoke wins and preserves the original binding")
        model.receive(try event(#"{"kind":"state","state":"pairing_revoked"}"#))
        model.toggle(3)
        model.requestSelectionChange()
        try require(model.handoffID == handoff && model.selectedIDs == [3] &&
                    model.accountSaveAwaitingBrowserReceipt && commands.count == whilePending,
                    "A late revoke result cannot erase a receipted handoff or unlock contact edits")
        model.receive(try event(#"{"kind":"handoff","state":"saved","handoffId":"\#(handoff)"}"#))
        try require(model.handoffAcknowledged && model.phase == .completing,
                    "The same saved binding can complete after an edit request was refused")

        var successfulCommands: [Data] = []
        let successful = CollectorModel(commandWriter: { successfulCommands.append($0) })
        successful.receive(try event(#"{"kind":"capture","contacts":[{"id":3,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        successful.prepareWorkspace()
        successful.selectedIDs = [3]
        successful.review()
        successful.receive(try reviewEvent(emittedReviewID(successfulCommands), ids: [3]))
        successful.receive(try event(#"{"kind":"pairing","pairCode":"1234567890","port":48751,"handoffId":"\#(handoff)"}"#))
        successful.requestSelectionChange()
        successful.receive(try event(#"{"kind":"state","state":"pairing_revoked"}"#))
        try require(successful.handoffID.isEmpty && successful.pairCode.isEmpty &&
                    successful.canEditWorkspace && successful.selectedIDs == [3] &&
                    !successful.selectionNeedsReview && successful.errorMessage.isEmpty &&
                    successful.handoffNotice.contains("link is closed"),
                    "Only accepted revocation opens editing without discarding the retained import")
        successful.toggle(3)
        try require(successful.selectedIDs.isEmpty && successful.selectionNeedsReview,
                    "The first post-unlock edit invalidates the old reviewed selection")
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
        try require(model.statusDetail.hasPrefix("201 contacts,"),
                    "The status must report all observed contacts, not only the loaded page")
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
        try require(model.statusDetail.hasPrefix("201 contacts,"),
                    "Paging must not rewrite the observed-contact total as the one-contact page")
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

    private static func allPageContactSelection() throws {
        func page(_ range: ClosedRange<Int>) -> String {
            range.map { id in
                #"{"id":\#(id),"name":"Synthetic \#(id)","phoneCount":0,"phoneEnds":[]}"#
            }.joined(separator: ",")
        }
        let first = page(1...200)
        let second = page(201...400)
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[\#(first)],"totalContacts":401,"query":"","cursor":0,"nextCursor":200}"#))
        model.selectedIDs = [7]
        model.selectAllContacts()
        try require(model.selectingAllContacts && model.selectedIDs == [7] && commands.count == 1,
                    "Select all must preserve the prior selection until every page verifies")
        model.toggle(7)
        model.review()
        try require(model.selectedIDs == [7] && commands.count == 1,
                    "A bulk selection must freeze edits and review")
        model.receive(try event(#"{"kind":"contacts","contacts":[\#(first)],"totalContacts":401,"query":"","cursor":0,"nextCursor":200}"#))
        try require(model.selectAllVisited == 200 && commands.count == 2 && model.selectedIDs == [7],
                    "The first page is counted, but does not become a partial selection")
        model.receive(try event(#"{"kind":"contacts","contacts":[\#(second)],"totalContacts":401,"query":"","cursor":200,"nextCursor":400}"#))
        try require(model.selectAllVisited == 400 && commands.count == 3,
                    "The second bounded page must advance the exact cursor")
        model.receive(try event(#"{"kind":"contacts","contacts":[{"id":401,"name":"Last","phoneCount":0,"phoneEnds":[]}],"totalContacts":401,"query":"","cursor":400,"nextCursor":null}"#))
        try require(!model.selectingAllContacts && model.selectedIDs == Set(1...401) &&
                    model.selectionNeedsReview && model.phase == .selecting,
                    "Select all must include every page and still require a bound local review")
        model.review()
        let command = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any]
        try require(command?["action"] as? String == "review-page" && command?["total"] as? Int == 401 &&
                    (command?["ids"] as? [Int]) == Array(1...401),
                    "All-page selection must reuse the unchanged UUID-bound selected-ID review protocol")
        let reviewID = try emittedReviewID(commands)
        model.receive(try reviewEvent(reviewID, ids: Array(1...401)))
        model.clearAllContacts()
        try require(model.selectedIDs.isEmpty && model.selectionNeedsReview && model.phase == .selecting,
                    "Clear all must revoke the reviewed selection without changing captured contacts")

        var cancelledCommands: [Data] = []
        let cancelled = CollectorModel(commandWriter: { cancelledCommands.append($0) })
        cancelled.receive(try event(#"{"kind":"capture","contacts":[\#(first)],"totalContacts":401,"query":"","cursor":0,"nextCursor":200}"#))
        cancelled.selectedIDs = [7]
        cancelled.selectAllContacts()
        cancelled.cancelSelectAllContacts()
        cancelled.receive(try event(#"{"kind":"contacts","contacts":[\#(first)],"totalContacts":401,"query":"","cursor":0,"nextCursor":200}"#))
        try require(!cancelled.selectingAllContacts && cancelled.selectedIDs == [7] && cancelledCommands.count == 1,
                    "Cancellation waits for the in-flight page, then keeps the prior selection")

        let duplicate = CollectorModel(commandWriter: { _ in })
        duplicate.receive(try event(#"{"kind":"capture","contacts":[\#(first)],"totalContacts":201,"query":"","cursor":0,"nextCursor":200}"#))
        duplicate.selectAllContacts()
        duplicate.receive(try event(#"{"kind":"contacts","contacts":[\#(first)],"totalContacts":201,"query":"","cursor":0,"nextCursor":200}"#))
        duplicate.receive(try event(#"{"kind":"contacts","contacts":[{"id":7,"name":"Duplicate","phoneCount":0,"phoneEnds":[]}],"totalContacts":201,"query":"","cursor":200,"nextCursor":null}"#))
        try require(duplicate.phase == .error && !duplicate.selectingAllContacts && duplicate.selectedIDs.isEmpty,
                    "Duplicate IDs across pages must fail closed, never silently shrink Select all")

        let large = CollectorModel(commandWriter: { _ in })
        large.receive(try event(#"{"kind":"capture","contacts":[\#(first)],"totalContacts":1000001,"query":"","cursor":0,"nextCursor":200}"#))
        try require(large.phase == .selecting && large.totalContacts == 1000001,
                    "Preview admission must not impose the obsolete million-contact aggregate limit")
    }

    private static func multiPageAllSelectionReview() throws {
        func page(_ start: Int, _ end: Int) -> String {
            (start...end).map { id in
                #"{"id":\#(id),"name":"Synthetic \#(id)","phoneCount":0,"phoneEnds":[]}"#
            }.joined(separator: ",")
        }
        var commands: [Data] = []
        let model = CollectorModel(commandWriter: { commands.append($0) })
        model.receive(try event(#"{"kind":"capture","contacts":[\#(page(1, 200))],"totalContacts":1205,"query":"","cursor":0,"nextCursor":200}"#))
        model.selectAllContacts()
        for cursor in stride(from: 0, through: 1200, by: 200) {
            let end = min(cursor + 200, 1205)
            model.receive(try event(#"{"kind":"contacts","contacts":[\#(page(cursor + 1, end))],"totalContacts":1205,"query":"","cursor":\#(cursor),"nextCursor":\#(end == 1205 ? "null" : String(end))}"#))
        }
        try require(model.selectedIDs == Set(1...1205), "All-page selection must preserve more than one review command's IDs")
        model.review()
        let reviewID = try emittedReviewID(commands)
        let first = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any]
        try require(first?["cursor"] as? Int == 0 && (first?["ids"] as? [Int]) == Array(1...1000),
                    "The first review command must contain exactly the first 1000 selected IDs")
        model.receive(try event(#"{"kind":"review-page","reviewId":"\#(reviewID)","nextCursor":999}"#))
        let stillFirst = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any]
        try require(stillFirst?["cursor"] as? Int == 0,
                    "A wrong acknowledgement must not advance the selection")
        model.receive(try event(#"{"kind":"review-page","reviewId":"\#(reviewID)","nextCursor":1000}"#))
        let second = try JSONSerialization.jsonObject(with: commands.last!) as? [String: Any]
        try require(second?["cursor"] as? Int == 1000 && (second?["ids"] as? [Int]) == Array(1001...1205),
                    "The second review command must carry the remaining selected IDs")
        model.receive(try event(#"{"kind":"review-page","reviewId":"\#(reviewID)","nextCursor":1205}"#))
        model.receive(try reviewEvent(reviewID, ids: Array(1...1205)))
        try require(model.phase == .reviewing && !model.selectionNeedsReview,
                    "Only the exact complete multi-page review may authorize pairing")
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

    private static func optionalSourceReasonCopy() throws {
        let precise = CollectorModel()
        precise.receive(try event(#"{"kind":"capture","contacts":[{"id":1,"name":"Synthetic","phoneCount":0,"phoneEnds":[]}],"missing":["calls"],"backupEncrypted":false,"unavailableReasons":{"calls":"selected_payload_length"}}"#))
        let detail = precise.unavailableDescription("calls")
        try require(detail.contains("length did not match") && detail.contains("selected_payload_length") &&
                    detail.contains("does not prove zero history"),
                    "Precise optional-source cause must reach the local review without claiming zero history")
        try require(!detail.contains("unencrypted backup did not contain call history"),
                    "A listed but invalid Call source must not be called absent because the backup is unencrypted")

        let old = CollectorModel()
        old.receive(try event(#"{"kind":"capture","contacts":[{"id":1,"name":"Synthetic","phoneCount":0,"phoneEnds":[]}],"missing":["calls"],"backupEncrypted":false,"unavailableReasons":{"calls":"source_integrity"}}"#))
        try require(old.unavailableDescription("calls").contains("integrity check"),
                    "Older companion reason codes must remain readable")
        let unknown = CollectorModel()
        unknown.receive(try event(#"{"kind":"capture","contacts":[{"id":1,"name":"Synthetic","phoneCount":0,"phoneEnds":[]}],"missing":["calls"],"unavailableReasons":{"calls":"private path or data"}}"#))
        try require(!unknown.unavailableDescription("calls").contains("private path"),
                    "Unrecognized helper reason text must never appear in the local review")
    }
}
