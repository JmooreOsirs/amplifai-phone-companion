import AppKit
import SwiftUI

struct ActiveCompanionWindow: NSViewRepresentable {
    @ObservedObject var model: CollectorModel
    let destinationReviewRequestID: UUID?

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }
    func makeNSView(context: Context) -> NSView {
        let view = WindowProbeView()
        view.onWindow = { [weak coordinator = context.coordinator] window in coordinator?.attach(window) }
        return view
    }
    func updateNSView(_ view: NSView, context: Context) {
        context.coordinator.model = model
        context.coordinator.attach(view.window)
        context.coordinator.update()
        context.coordinator.presentDestinationIfNeeded(manualRequestID: destinationReviewRequestID)
    }
    static func dismantleNSView(_ view: NSView, coordinator: Coordinator) { coordinator.restore() }

    @MainActor final class Coordinator: NSObject, NSWindowDelegate {
        weak var model: CollectorModel?
        weak var window: NSWindow?
        weak var previousDelegate: NSWindowDelegate?
        private var originalLevel: NSWindow.Level = .normal
        private var originalHides = false
        private var observers: [(NotificationCenter, NSObjectProtocol)] = []
        private var confirmingClose = false
        private var closeRequested = false
        private var activeDestinationAlert: NSAlert?
        private var activeDestinationRequest: CollectorModel.DestinationApprovalRequest?
        private var endingDestinationAlert = false
        private var lastPresentedDestinationID: String?
        private var lastManualReviewRequestID: UUID?
        private var manualReviewRequestID: UUID?
        private var presentationGeneration = 0

        init(model: CollectorModel) {
            self.model = model
        }
        func attach(_ candidate: NSWindow?) {
            guard let candidate, window !== candidate else { return }
            restore()
            window = candidate
            previousDelegate = candidate.delegate
            originalLevel = candidate.level
            originalHides = candidate.hidesOnDeactivate
            candidate.delegate = self
            let workspace = NSWorkspace.shared.notificationCenter
            observers.append((workspace, workspace.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.update() }
            }))
            for name in [NSWindow.willBeginSheetNotification, NSWindow.didEndSheetNotification] {
                let center = NotificationCenter.default
                observers.append((center, center.addObserver(forName: name, object: candidate, queue: .main) { [weak self] _ in
                    Task { @MainActor in
                        self?.update()
                        if name == NSWindow.didEndSheetNotification {
                            self?.presentDestinationIfNeeded(manualRequestID: self?.manualReviewRequestID)
                        }
                    }
                }))
            }
            update()
            presentDestinationIfNeeded(manualRequestID: manualReviewRequestID)
        }
        func update() {
            guard let window, let model else { return }
            if model.canSafelyClose || (closeRequested && !model.helperRunning) {
                window.level = originalLevel
                window.close()
                return
            }
            let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
            let nativeFront = front == Bundle.main.bundleIdentifier
            // Other apps may show their own permission sheets: restore normal stacking, including in the browser.
            window.level = model.activeFlow && nativeFront && window.attachedSheet == nil && NSApp.modalWindow == nil ? .floating : originalLevel
            window.hidesOnDeactivate = model.activeFlow ? false : originalHides
            // Normal status updates never take focus from the account browser.
        }
        func presentDestinationIfNeeded(manualRequestID: UUID?) {
            manualReviewRequestID = manualRequestID
            guard let window, let model else { return }
            let current = model.pendingDestinationApproval
            if let activeDestinationRequest, let activeDestinationAlert {
                if current != activeDestinationRequest && !endingDestinationAlert {
                    endingDestinationAlert = true
                    window.endSheet(activeDestinationAlert.window)
                }
                return
            }
            guard let current,
                  current.attentionID != lastPresentedDestinationID ||
                    (manualRequestID != nil && manualRequestID != lastManualReviewRequestID),
                  window.attachedSheet == nil, NSApp.modalWindow == nil else { return }
            let alert = NSAlert()
            alert.messageText = "Confirm the signed-in account destination"
            alert.informativeText = "Account: \(current.email). Course: \(current.course). These details came from AMPLIFai's authenticated server. Connecting sends only the reviewed preview to this browser; each source still needs separate Save approval in the account."
            alert.addButton(withTitle: "Connect to this account")
            alert.addButton(withTitle: "Deny this connection")
            alert.addButton(withTitle: "Keep for later")
            activeDestinationAlert = alert
            activeDestinationRequest = current
            lastPresentedDestinationID = current.attentionID
            lastManualReviewRequestID = manualRequestID
            let generation = presentationGeneration
            window.deminiaturize(nil)
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            alert.beginSheetModal(for: window) { [weak self] response in
                guard let self, self.presentationGeneration == generation else { return }
                self.activeDestinationAlert = nil
                self.activeDestinationRequest = nil
                self.endingDestinationAlert = false
                if response == .alertFirstButtonReturn {
                    model.decideAccountDestination(true, request: current)
                } else if response == .alertSecondButtonReturn {
                    model.decideAccountDestination(false, request: current)
                }
                self.presentDestinationIfNeeded(manualRequestID: self.manualReviewRequestID)
            }
        }
        func windowShouldClose(_ sender: NSWindow) -> Bool {
            guard let model else { return previousDelegate?.windowShouldClose?(sender) ?? true }
            guard !model.canSafelyClose && (model.hasUnsavedReview || model.helperRunning) else {
                return previousDelegate?.windowShouldClose?(sender) ?? true
            }
            guard !confirmingClose, sender.attachedSheet == nil else { return false }
            confirmingClose = true
            sender.level = originalLevel
            let alert = NSAlert()
            alert.messageText = model.hasUnsavedReview ? "Keep the unsaved local review?" : "Cancel this collection and close?"
            alert.informativeText = "The browser receiving a preview is not an account save. Closing discards this local recovery copy after the helper stops. App-created temporary data may need inspection on the next launch; other backups are never removed."
            alert.addButton(withTitle: "Keep window open")
            alert.addButton(withTitle: "Cancel and close")
            alert.beginSheetModal(for: sender) { [weak self] response in
                guard let self else { return }
                self.confirmingClose = false
                if response == .alertSecondButtonReturn {
                    self.closeRequested = true
                    model.disconnect()
                }
                self.update()
            }
            return false
        }
        func windowWillClose(_ notification: Notification) {
            model?.disconnect()
            previousDelegate?.windowWillClose?(notification)
        }
        override func responds(to selector: Selector!) -> Bool {
            super.responds(to: selector) || previousDelegate?.responds(to: selector) == true
        }
        override func forwardingTarget(for selector: Selector!) -> Any? {
            if previousDelegate?.responds(to: selector) == true { return previousDelegate }
            return super.forwardingTarget(for: selector)
        }
        func restore() {
            presentationGeneration += 1
            if let window, let activeDestinationAlert { window.endSheet(activeDestinationAlert.window) }
            activeDestinationAlert = nil
            activeDestinationRequest = nil
            endingDestinationAlert = false
            lastPresentedDestinationID = nil
            lastManualReviewRequestID = nil
            for (center, observer) in observers { center.removeObserver(observer) }
            observers = []
            if let window {
                window.level = originalLevel
                window.hidesOnDeactivate = originalHides
                if window.delegate === self { window.delegate = previousDelegate }
            }
            window = nil
            previousDelegate = nil
        }
    }

    final class WindowProbeView: NSView {
        var onWindow: ((NSWindow?) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            onWindow?(window)
        }
    }
}
