import AppKit
import SwiftUI

/// Connects the collector window to `CompanionWindowDirector`. There is no destination sheet: the run's
/// destination is verified against the server and answered automatically. After the handoff the window is
/// minimized once and never restored, activated or raised by the app; only the owner reopens it.
struct ActiveCompanionWindow: NSViewRepresentable {
    @ObservedObject var model: CollectorModel
    /// Opens the account in the return browser; a host can replace it (the fake-data demo does).
    var returnToBrowser: ReturnToBrowserAction = .system

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }
    func makeNSView(context: Context) -> NSView {
        let view = WindowProbeView()
        view.onWindow = { [weak coordinator = context.coordinator] window in coordinator?.attach(window) }
        return view
    }
    func updateNSView(_ view: NSView, context: Context) {
        context.coordinator.model = model
        context.coordinator.returnToBrowser = returnToBrowser
        context.coordinator.update()
    }
    static func dismantleNSView(_ view: NSView, coordinator: Coordinator) {
        (view as? WindowProbeView)?.onWindow = nil
        coordinator.invalidate()
    }

    @MainActor final class Coordinator: NSObject, NSWindowDelegate {
        weak var model: CollectorModel?
        weak var window: NSWindow?
        weak var previousDelegate: NSWindowDelegate?
        private weak var closingWindow: NSWindow?
        private var surface: AppKitCompanionSurface?
        private let director = CompanionWindowDirector()
        private var observers: [(NotificationCenter, NSObjectProtocol)] = []
        private var attachmentID = UUID()
        private var dismantled = false
        private var confirmingClose = false
        private var closeRequested = false
        var returnToBrowser: ReturnToBrowserAction = .system {
            didSet { surface?.returnToBrowser = returnToBrowser }
        }

        init(model: CollectorModel) {
            self.model = model
        }
        deinit {
            for (center, observer) in observers { center.removeObserver(observer) }
        }
        func attach(_ candidate: NSWindow?) {
            guard !dismantled else { return }
            guard let candidate else { restore(); return }
            guard candidate !== closingWindow, window !== candidate else { return }
            restore()
            window = candidate
            surface = AppKitCompanionSurface(window: candidate, returnToBrowser: returnToBrowser)
            previousDelegate = candidate.delegate
            candidate.delegate = self
            let workspace = NSWorkspace.shared.notificationCenter
            observers.append((workspace, workspace.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.update() }
            }))
            update()
        }
        func update() {
            guard let window, let model, let surface else { return }
            // A confirmed save stays on screen as "Saved" until the owner closes it.
            if closeRequested && !model.helperRunning {
                window.close()
                return
            }
            director.update(model: model, surface: surface)
        }
        func windowShouldClose(_ sender: NSWindow) -> Bool {
            guard !dismantled, sender === window else { return true }
            guard let model else { return previousDelegate?.windowShouldClose?(sender) ?? true }
            guard !model.canSafelyClose && (model.hasUnsavedReview || model.helperRunning) else {
                return previousDelegate?.windowShouldClose?(sender) ?? true
            }
            guard !confirmingClose, sender.attachedSheet == nil, let surface else { return false }
            confirmingClose = true
            let attachment = attachmentID
            // Only the owner's own close click shows this sheet.
            surface.presentCloseConfirmation(
                title: model.hasUnsavedReview ? "Keep the unsaved local review?" : "Cancel this collection and close?",
                detail: "Until your account confirms the save, this Mac keeps a private recovery copy. Closing discards it after the helper stops. App-created temporary data may need inspection on the next launch; other backups are never removed."
            ) { [weak self] cancelAndClose in
                guard let self, self.attachmentID == attachment, self.window != nil, !self.dismantled else { return }
                self.confirmingClose = false
                if cancelAndClose {
                    self.closeRequested = true
                    model.disconnect()
                }
                self.update()
            }
            return false
        }
        func windowWillClose(_ notification: Notification) {
            guard let closing = notification.object as? NSWindow, closing === window else { return }
            let delegate = previousDelegate
            let model = model
            closingWindow = closing
            // Disconnect publishes SwiftUI updates synchronously. Invalidate first so those updates cannot
            // register a new weak reference to this NSWindow while AppKit is tearing it down.
            restore()
            model?.disconnect()
            delegate?.windowWillClose?(notification)
        }
        override func responds(to selector: Selector!) -> Bool {
            super.responds(to: selector) || previousDelegate?.responds(to: selector) == true
        }
        override func forwardingTarget(for selector: Selector!) -> Any? {
            if previousDelegate?.responds(to: selector) == true { return previousDelegate }
            return super.forwardingTarget(for: selector)
        }
        func invalidate() {
            dismantled = true
            restore()
        }
        func restore() {
            attachmentID = UUID()
            director.detachSurface()
            let oldWindow = window
            let oldSurface = surface
            let delegate = previousDelegate
            surface = nil
            window = nil
            previousDelegate = nil
            confirmingClose = false
            closeRequested = false
            let oldObservers = observers
            observers = []
            for (center, observer) in oldObservers { center.removeObserver(observer) }
            oldSurface?.invalidate()
            if let oldWindow, oldWindow.delegate === self { oldWindow.delegate = delegate }
        }
    }

    final class WindowProbeView: NSView {
        var onWindow: ((NSWindow?) -> Void)?
        private var awaitingAttachment = false
        override func viewWillMove(toWindow newWindow: NSWindow?) {
            awaitingAttachment = newWindow != nil
            if newWindow == nil { onWindow?(nil) }
            super.viewWillMove(toWindow: newWindow)
        }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            // Only an actual move into a window admits attachment. SwiftUI updates and destruction-time
            // callbacks may still expose the old view.window; neither is an attachment authority.
            guard awaitingAttachment else { return }
            awaitingAttachment = false
            onWindow?(window)
        }
    }
}
