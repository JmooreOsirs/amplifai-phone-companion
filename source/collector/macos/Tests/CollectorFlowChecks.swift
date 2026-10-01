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
    static func main() {
        do { try checks(); print("11 macOS flow checks passed") }
        catch { FileHandle.standardError.write(Data("macOS flow check failed: \(error)\n".utf8)); Darwin.exit(1) }
    }
    private static func checks() throws {
        let model = CollectorModel()
        model.receive(try event(#"{"kind":"state","state":"connecting"}"#))
        try require(!model.progressIsKnown, "Connection must not invent a percentage")
        try require(model.statusDetail.contains("Trust"), "Connection must explain the owner-operated Trust step")
        model.receive(try event(#"{"kind":"progress"}"#))
        try require(!model.progressIsKnown, "Missing progress is indeterminate, not zero")
        model.receive(try event(#"{"kind":"progress","value":42}"#))
        try require(model.progressIsKnown && model.progress == 42, "Observed backup progress must survive")
        try require(model.progressExplanation.contains("estimate") && model.progressExplanation.contains("unavailable"), "Unknown ETA must be explicit")
        model.receive(try event(#"{"kind":"progress","value":100}"#))
        try require(model.phase == .processing && !model.statusTitle.contains("ready"), "100% backup is not finished metadata review")
        model.receive(try event(#"{"kind":"state","state":"password_required"}"#))
        try require(model.statusDetail.contains("existing") && !model.statusDetail.contains("Apple ID"), "Password recovery must name the existing backup password")

        let id = "00000000-0000-4000-8000-000000000001"
        let other = "00000000-0000-4000-8000-000000000002"
        model.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        model.selectedIDs = [7]
        model.receive(try event(#"{"kind":"review","matchedCalls":1,"matchedMessages":0}"#))
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

        let retained = CollectorModel()
        retained.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        retained.selectedIDs = [7]
        retained.receive(try event(#"{"kind":"review"}"#))
        retained.receive(try event(#"{"kind":"pairing","pairCode":"0000000000","handoffId":"\#(id)"}"#))
        retained.receive(try event(#"{"kind":"handoff","state":"expired","handoffId":"\#(id)"}"#))
        try require(retained.handoffID.isEmpty && retained.contacts.count == 1 && retained.selectedIDs == [7] && !retained.canSafelyClose,
                    "Expired pairing must stop the old binding and preserve explicit recovery")
        retained.finished(mode: "connect", code: 1)
        try require(retained.hasUnsavedReview, "Unexpected helper exit must retain unsaved review")
        retained.disconnect()
        try require(retained.contacts.isEmpty && retained.selectedIDs.isEmpty && retained.phase == .idle,
                    "Explicit discard after helper exit must permit recovery without a stranded review")

        let changed = CollectorModel()
        changed.receive(try event(#"{"kind":"capture","contacts":[{"id":7,"name":"Synthetic","phoneCount":1,"phoneEnds":["0000"]}]}"#))
        changed.selectedIDs = [7]
        changed.receive(try event(#"{"kind":"review"}"#))
        changed.toggle(7)
        changed.receive(try event(#"{"kind":"pairing","pairCode":"0000000000","handoffId":"\#(id)"}"#))
        try require(changed.handoffID.isEmpty && changed.pairCode.isEmpty && !changed.handoffAcknowledged,
                    "Late pairing response cannot revive a selection changed after review")
    }
}
