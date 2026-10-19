import AppKit
import SwiftUI

/// Real AppKit windows, representable, coordinator and surface; production model with synthetic events/wire.
/// Windows stay offscreen and application activation is prohibited. No helper, browser, device or account.
@main
@MainActor
enum CompanionWindowLifecycleChecks {
    enum Failure: Error { case check(String) }

    final class PreviousDelegate: NSObject, NSWindowDelegate {
        var closeCount = 0
        func windowWillClose(_ notification: Notification) { closeCount += 1 }
    }

    static func require(_ value: @autoclosure () -> Bool, _ reason: String) throws {
        guard value() else { throw Failure.check(reason) }
    }

    static func window() -> NSWindow {
        let result = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 1180, height: 820),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        result.isReleasedWhenClosed = false
        result.animationBehavior = .none
        return result
    }

    static func drain() {
        // A queued sentinel, rather than a fixed sleep, drains callbacks already waiting on the main queue.
        var completed = false
        DispatchQueue.main.async { completed = true }
        while !completed { _ = RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.1)) }
    }

    static func probe(_ coordinator: ActiveCompanionWindow.Coordinator) -> ActiveCompanionWindow.WindowProbeView {
        let result = ActiveCompanionWindow.WindowProbeView()
        result.onWindow = { [weak coordinator] in coordinator?.attach($0) }
        return result
    }

    static func ownerCloseInvalidatesBeforeCallbacks() throws {
        let (model, _, wire, _) = try PhoneRunFlowChecks.boundModel()
        let coordinator = ActiveCompanionWindow.Coordinator(model: model)
        coordinator.returnToBrowser = ReturnToBrowserAction { _, _, _ in }
        let previous = PreviousDelegate()
        let host = window()
        host.delegate = previous
        host.contentView = probe(coordinator)
        try require(host.delegate === coordinator, "Real window attachment installs the coordinator")
        model.receive(try PhoneRunFlowChecks.handoffEvent("saved"))
        try require(wire.last?["action"] as? String == "finish", "The synthetic receipt reaches production finish handling")
        model.finished(mode: "connect", code: 0)
        coordinator.update()
        try require(model.canSafelyClose && coordinator.window === host,
                    "Confirmed completion stays attached until the owner's close")
        host.close()
        try require(coordinator.window == nil, "Owner close must invalidate the adapter before later model updates")
        try require(host.delegate === previous && previous.closeCount == 1,
                    "Closing restores and forwards the previous delegate exactly once")
        coordinator.attach(host)
        coordinator.update()
        try require(host.delegate === previous && coordinator.window == nil, "A closed window cannot be reattached")
    }

    static func detachReattachAndDismantle() throws {
        let model = CollectorModel(commandWriter: { _ in })
        let coordinator = ActiveCompanionWindow.Coordinator(model: model)
        let previous = PreviousDelegate()
        let first = window()
        first.delegate = previous
        let view = probe(coordinator)
        first.contentView = view
        first.contentView = nil
        try require(coordinator.window == nil && first.delegate === previous,
                    "Detaching the probe restores the first window without waiting for destruction")
        let second = window()
        second.contentView = view
        try require(coordinator.window === second, "The same live probe can attach to a new window")
        ActiveCompanionWindow.dismantleNSView(view, coordinator: coordinator)
        try require(view.onWindow == nil && coordinator.window == nil,
                    "Dismantling clears the probe callback as well as the window")
        coordinator.attach(first)
        coordinator.update()
        try require(coordinator.window == nil, "A dismantled representable rejects late attachments")
        first.close()
        second.close()
    }

    static func cancelThenCloseAndQueuedUpdates() throws {
        let model = CollectorModel(commandWriter: { _ in })
        try PhoneRunFlowChecks.capture(model)
        let coordinator = ActiveCompanionWindow.Coordinator(model: model)
        let host = window()
        host.contentView = probe(coordinator)
        // Exercise the actual offscreen AppKit confirmation and choose its Cancel and close response.
        try require(!coordinator.windowShouldClose(host), "An unsaved review requires an owner close decision")
        guard let sheet = host.attachedSheet else { throw Failure.check("The real close confirmation is attached") }
        host.endSheet(sheet, returnCode: .alertFirstButtonReturn)
        drain()
        try require(coordinator.window === host && model.hasUnsavedReview,
                    "Keep window open preserves the unsaved review and live adapter")
        try require(!coordinator.windowShouldClose(host), "The owner can subsequently request cancel and close")
        guard let cancelSheet = host.attachedSheet else { throw Failure.check("The second close confirmation is attached") }
        host.endSheet(cancelSheet, returnCode: .alertSecondButtonReturn)
        drain()
        try require(coordinator.window == nil && model.phase == .idle && !model.hasUnsavedReview,
                    "Confirmed cancel discards the synthetic review and closes after the helper is stopped")
        model.phase = .error
        DispatchQueue.main.async { coordinator.update() }
        NSWorkspace.shared.notificationCenter.post(name: NSWorkspace.didActivateApplicationNotification, object: nil)
        drain()
        try require(coordinator.window == nil && host.delegate !== coordinator,
                    "Queued model/workspace events cannot revive a closed adapter")
    }

    static func lateBrowserReplyAfterDetach() throws {
        let (model, _, _, _) = try PhoneRunFlowChecks.boundModel()
        var answers: [@MainActor (Bool) -> Void] = []
        let coordinator = ActiveCompanionWindow.Coordinator(model: model)
        coordinator.returnToBrowser = ReturnToBrowserAction { _, _, completion in answers.append(completion) }
        let first = window()
        let view = probe(coordinator)
        first.contentView = view
        try require(answers.count == 1, "The actual surface passes navigation to the inert browser boundary")
        first.contentView = nil
        answers[0](false)
        try require(model.errorMessage.isEmpty, "A detached surface drops an obsolete browser-open failure")
        let second = window()
        second.contentView = view
        try require(answers.count == 2, "A replacement surface can resume a navigation still pending at detach")
        answers[0](true)
        try require(!second.isMiniaturized, "An obsolete success cannot minimize the replacement window")
        ActiveCompanionWindow.dismantleNSView(view, coordinator: coordinator)
        answers[1](false)
        try require(model.errorMessage.isEmpty, "Dismantling also invalidates pending browser replies")
        first.close()
        second.close()
    }

    static func dismantledConfirmationCannotDiscard() throws {
        let model = CollectorModel(commandWriter: { _ in })
        try PhoneRunFlowChecks.capture(model)
        let coordinator = ActiveCompanionWindow.Coordinator(model: model)
        let host = window()
        let view = probe(coordinator)
        host.contentView = view
        try require(!coordinator.windowShouldClose(host) && host.attachedSheet != nil,
                    "An actual confirmation can be pending when SwiftUI dismantles its view")
        ActiveCompanionWindow.dismantleNSView(view, coordinator: coordinator)
        drain()
        try require(host.attachedSheet == nil && coordinator.window == nil && model.hasUnsavedReview,
                    "Dismantling ends the sheet and invalidates its answer without discarding the review")
        host.close()
    }

    static func repeatedRepresentableDestruction() throws {
        for iteration in 0..<40 {
            weak var releasedWindow: NSWindow?
            try autoreleasepool {
                let model = CollectorModel(commandWriter: { _ in })
                let host = window()
                releasedWindow = host
                let hosting = NSHostingView(rootView: ActiveCompanionWindow(model: model,
                    returnToBrowser: ReturnToBrowserAction { _, _, completion in completion(false) }))
                host.contentView = hosting
                hosting.layoutSubtreeIfNeeded()
                drain()
                try require(host.delegate is ActiveCompanionWindow.Coordinator,
                            "SwiftUI creates the real representable coordinator (iteration \(iteration))")
                model.phase = .completed
                host.close()
                model.phase = .error
                hosting.layoutSubtreeIfNeeded()
                drain()
                host.contentView = nil
            }
            drain()
            try require(releasedWindow == nil, "Closed windows are released, not retained to mask teardown (iteration \(iteration))")
        }
    }

    static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.prohibited)
        do {
            try ownerCloseInvalidatesBeforeCallbacks()
            try detachReattachAndDismantle()
            try cancelThenCloseAndQueuedUpdates()
            try lateBrowserReplyAfterDetach()
            try dismantledConfirmationCannotDiscard()
            try repeatedRepresentableDestruction()
            try require(!application.isActive, "The harness must not activate the application")
            print("PASS: real offscreen AppKit lifecycle checks; 40 SwiftUI close/reopen cycles; no helper/device/account/browser")
        } catch {
            fputs("FAIL: \(error)\n", stderr)
            exit(1)
        }
    }
}
