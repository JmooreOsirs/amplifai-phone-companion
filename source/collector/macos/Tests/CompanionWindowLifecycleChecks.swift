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

    static func detachPreservesAnotherOwnersSheet() throws {
        let model = CollectorModel(commandWriter: { _ in })
        let coordinator = ActiveCompanionWindow.Coordinator(model: model)
        let host = window()
        let view = probe(coordinator)
        host.contentView = view
        let foreignSheet = window()
        host.beginSheet(foreignSheet)
        try require(host.attachedSheet === foreignSheet, "An unrelated component owns an attached AppKit sheet")
        ActiveCompanionWindow.dismantleNSView(view, coordinator: coordinator)
        drain()
        try require(host.attachedSheet === foreignSheet && foreignSheet.sheetParent === host,
                    "Adapter teardown must leave another component's sheet attached")
        host.endSheet(foreignSheet)
        foreignSheet.close()
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

    static func browserReturnDoesNotNavigate() throws {
        var activated: [String?] = []
        var opened: [URL] = []
        var results: [BrowserActivationResult] = []
        let action = ReturnToBrowserAction(activateExisting: { browser, done in
            activated.append(browser)
            done(browser == "com.example.original-browser" ? .observed : .unavailable)
        }) { url, _, completion in
            opened.append(url)
            completion(true)
        }
        for browser in ["com.example.original-browser", nil, "com.example.closed-browser"] {
            action.activateOriginalBrowser(preferredBrowserBundleID: browser) { results.append($0) }
        }
        try require(results == [.observed, .unavailable, .unavailable],
                    "Only an observed original browser reports success; missing senders fail closed")
        try require(activated.count == 3 && opened.isEmpty, "Returning never opens a URL, even after activation failure")
        let recovery = URL(string: CollectorModel.accountOrigin + "/phone/account")!
        action(recovery, preferredBrowserBundleID: "com.example.original-browser")
        try require(opened == [recovery], "Only explicit recovery opens its supplied account destination")
        let inert = ReturnToBrowserAction { _, _, _ in }
        inert.activateOriginalBrowser(preferredBrowserBundleID: "com.example.original-browser") { results.append($0) }
        try require(results.last == .unavailable,
                    "Injected URL handlers must not inherit live browser activation")
        ReturnToBrowserAction.system.activateOriginalBrowser(preferredBrowserBundleID: nil) { results.append($0) }
        try require(results.last == .unavailable,
                    "The system action never chooses a default browser when the sender is unknown")
    }

    static func browserActivationNeedsObservedForeground() throws {
        var pending: [@MainActor () -> Void] = []
        var frontmost = false
        var results: [BrowserActivationResult] = []
        AppKitCompanionSurface.observeBrowserActivation(requested: true, isFrontmost: { frontmost },
                                                        schedule: { pending.append($0) }, completion: { results.append($0) })
        try require(results.isEmpty && pending.count == 1, "An accepted request is not proof of activation")
        frontmost = true
        pending.removeFirst()()
        try require(results == [.observed] && pending.isEmpty, "Observed browser activation finishes once")
        frontmost = false
        results = []
        AppKitCompanionSurface.observeBrowserActivation(requested: true, isFrontmost: { false },
                                                        schedule: { pending.append($0) }, completion: { results.append($0) })
        var steps = 0
        while !pending.isEmpty && steps < 20 { steps += 1; pending.removeFirst()() }
        try require(steps == 10 && pending.isEmpty && results == [.unconfirmed],
                    "The bounded observer stops without repeated activation or false success")
    }

    static func browserRecoveryFencesDuplicateAndLateCallbacks() throws {
        let session = BrowserReturnSession()
        let first = BrowserReturnSession.Context(runOrSetupID: "run-one", browserBundleID: "original",
            recoveryURL: URL(string: CollectorModel.accountOrigin + "/phone/account?phoneRunId=first")!)
        let second = BrowserReturnSession.Context(runOrSetupID: "run-two", browserBundleID: "original",
            recoveryURL: URL(string: CollectorModel.accountOrigin + "/phone/account?phoneRunId=second")!)
        var activation: [@MainActor (BrowserActivationResult) -> Void] = []
        var opened: [URL] = []
        var completions: [@MainActor (Bool) -> Void] = []
        let action = ReturnToBrowserAction(activateExisting: { _, done in activation.append(done) }) { url, _, done in
            opened.append(url); completions.append(done)
        }
        session.returnToAccount(using: action, context: first)
        session.returnToAccount(using: action, context: first)
        session.recover(using: action, context: first)
        try require(session.busy && activation.count == 1 && opened.isEmpty,
                    "Repeated clicks cannot interleave activation and account opens")
        activation[0](.unconfirmed)
        try require(!session.busy && session.message.contains("could not be confirmed"),
                    "Unconfirmed activation has truthful manual recovery feedback")
        session.recover(using: action, context: first)
        session.recover(using: action, context: first)
        try require(opened == [first.recoveryURL!], "One explicit recovery opens one exact account URL")
        activation[0](.observed)
        try require(session.busy, "An old activation callback cannot complete a newer recovery")
        completions[0](true)
        completions[0](false)
        session.recover(using: action, context: first)
        try require(session.recoverySent && opened.count == 1 && session.message.contains("link sent"),
                    "Successful dispatch is not page-load evidence and cannot spam new tabs")
        session.recover(using: action, context: second)
        session.use(.init(runOrSetupID: nil, browserBundleID: "original", recoveryURL: nil))
        completions[1](true)
        try require(!session.busy && !session.recoverySent && session.message.isEmpty,
                    "A replaced or refused run ignores late recovery success")
        session.recover(using: action, context: .init(runOrSetupID: nil, browserBundleID: "original", recoveryURL: nil))
        try require(opened.count == 2, "A refused setup never opens a generic account URL")
        session.returnToAccount(using: action, context: second)
        session.invalidate()
        session.use(second)
        activation.last?(.observed)
        try require(!session.busy && session.message.isEmpty,
                    "Disappearing and returning to the same context cannot retain a canceled Returning message")
    }

    static func openingGeometryFitsDisplay() throws {
        let display = NSRect(x: 100, y: 40, width: 1440, height: 900)
        let small = NSRect(x: 200, y: 300, width: 375, height: 600)
        let fitted = CompanionOpeningGeometry.frame(current: small, visible: display, styleMask: [.titled, .resizable])
        let content = NSWindow.contentRect(forFrameRect: fitted, styleMask: [.titled, .resizable])
        try require(content.width >= 1180 && content.height >= 820 && display.contains(fitted),
                    "An undersized restored window opens with useful review space inside the display")
        let compactDisplay = NSRect(x: -1280, y: 0, width: 820, height: 640)
        let compact = CompanionOpeningGeometry.frame(current: small, visible: compactDisplay, styleMask: [.titled, .resizable])
        try require(compact == compactDisplay, "Small displays bound the opening frame, including negative display origins")
        let roomy = NSRect(x: 120, y: 60, width: 1300, height: 870)
        try require(CompanionOpeningGeometry.frame(current: roomy, visible: display, styleMask: [.titled, .resizable]) == roomy,
                    "A larger usable opening frame is preserved")
    }

    static func openingGeometryRunsOnceWithoutShowingWindow() throws {
        guard let visible = NSScreen.main?.visibleFrame else { throw Failure.check("A display is required for the sizing check") }
        let host = NSWindow(contentRect: NSRect(x: visible.minX + 20, y: visible.minY + 20, width: 375, height: 600),
                            styleMask: [.titled, .resizable], backing: .buffered, defer: false)
        host.isReleasedWhenClosed = false
        let original = host.frame
        let action = ReturnToBrowserAction { _, _, completion in completion(false) }
        let first = AppKitCompanionSurface(window: host, returnToBrowser: action)
        try require(host.frame == CompanionOpeningGeometry.frame(current: original, visible: visible, styleMask: host.styleMask),
                    "The real surface applies the bounded opening frame before the window is shown")
        host.setContentSize(NSSize(width: 500, height: 600))
        let resized = host.frame
        let second = AppKitCompanionSurface(window: host, returnToBrowser: action)
        try require(host.frame == resized, "Reattaching the surface preserves the owner's later resize")
        try require(!host.isVisible, "Sizing must not show or order the test window")
        first.invalidate()
        second.invalidate()
        host.close()
    }

    static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.prohibited)
        do {
            try browserReturnDoesNotNavigate()
            try browserActivationNeedsObservedForeground()
            try browserRecoveryFencesDuplicateAndLateCallbacks()
            try openingGeometryFitsDisplay()
            try openingGeometryRunsOnceWithoutShowingWindow()
            try ownerCloseInvalidatesBeforeCallbacks()
            try detachReattachAndDismantle()
            try cancelThenCloseAndQueuedUpdates()
            try lateBrowserReplyAfterDetach()
            try dismantledConfirmationCannotDiscard()
            try detachPreservesAnotherOwnersSheet()
            try repeatedRepresentableDestruction()
            try require(!application.isActive, "The harness must not activate the application")
            print("PASS: real unshown AppKit lifecycle and once-only sizing checks; 40 SwiftUI close/reopen cycles; no helper/device/account/browser")
        } catch {
            fputs("FAIL: \(error)\n", stderr)
            exit(1)
        }
    }
}
