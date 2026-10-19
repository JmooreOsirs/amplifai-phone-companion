import AppKit
import SwiftUI

/// FAKE DATA DEMO host for the production Mac views. It runs the real CollectorModel and ContentView,
/// launches only the bundled fake helper, intercepts every browser open and never starts the updater,
/// status service or diagnostic upload. The sample report is simulated here; no website report exists.
@main @MainActor
enum FakeDataDemoApp {
    private static let delegate = DemoDelegate()

    static func main() {
        // However it is opened (double-click, Finder, Terminal), the demo runs only inside its no-network sandbox.
        DemoSandbox.enterOrExit()
        if CommandLine.arguments.contains("--sandbox-self-test") {
            print("FAKE DATA DEMO sandbox self-test: network-outbound denied = \(DemoSandbox.networkDenied)")
            exit(DemoSandbox.networkDenied ? 0 : 3)
        }
        if let problem = DemoIsolation.problem() {
            FileHandle.standardError.write(Data("FAKE DATA DEMO refused to start: \(problem)\n".utf8))
            exit(2)
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        app.delegate = delegate
        app.mainMenu = DemoDelegate.menu()
        app.run()
    }
}

enum DemoSandbox {
    private typealias SandboxCheck = @convention(c) (pid_t, UnsafePointer<CChar>?, Int32) -> Int32
    private static let reexecMarker = "AMPLIFAI_FAKE_DEMO_SANDBOX_REEXEC"

    /// True only when the OS sandbox denies outbound network for this process.
    static var networkDenied: Bool {
        guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "sandbox_check") else { return false }
        return unsafeBitCast(symbol, to: SandboxCheck.self)(getpid(), "network-outbound", 0) != 0
    }

    static func enterOrExit() {
        if networkDenied { return }
        guard ProcessInfo.processInfo.environment[reexecMarker] == nil,
              let profile = Bundle.main.path(forResource: "deny-network", ofType: "sb"),
              let executable = Bundle.main.executablePath else {
            FileHandle.standardError.write(Data("FAKE DATA DEMO refused to start: no-network sandbox unavailable\n".utf8))
            exit(2)
        }
        setenv(reexecMarker, "1", 1)
        let arguments = ["/usr/bin/sandbox-exec", "-f", profile, executable] + CommandLine.arguments.dropFirst()
        var cArguments = arguments.map { strdup($0) } + [nil]
        execv(arguments[0], &cArguments)
        FileHandle.standardError.write(Data("FAKE DATA DEMO refused to start: could not enter the no-network sandbox\n".utf8))
        exit(2)
    }
}

enum DemoIsolation {
    static let identifierPrefix = "ai.satoris.amplifai.fake-data-demo."
    static let helperMarker = "amplifai-fake-data-helper"

    /// Refuse to run unless this is the separately identified demo bundle with only the fake helper.
    static func problem(bundle: Bundle = .main) -> String? {
        guard let identifier = bundle.bundleIdentifier, identifier.hasPrefix(identifierPrefix) else { return "not the demo bundle identity" }
        if bundle.bundlePath.hasPrefix("/Applications/") { return "demo must not run from /Applications" }
        guard let resources = bundle.resourceURL else { return "missing resources" }
        let marker = resources.appendingPathComponent("helper/FAKE_HELPER_MARKER")
        guard (try? String(contentsOf: marker, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) == helperMarker else {
            return "bundled helper is not the fake helper"
        }
        for real in ["uvx", "collector"] where FileManager.default.fileExists(atPath: resources.appendingPathComponent(real).path) {
            return "real collector runtime present: \(real)"
        }
        if SupportDiagnosticReporter.validatedBuild(bundle.infoDictionary) != nil { return "diagnostic upload build is valid" }
        if bundle.object(forInfoDictionaryKey: "SUFeedURL") != nil { return "update feed configured" }
        return nil
    }
}

@MainActor
final class DemoSession: ObservableObject {
    let model = CollectorModel(bundleInformation: ["CFBundleVersion": "fake-demo"])
    let updater = CompanionUpdater(enablePublishedUpdates: false, bundleInformation: [:])
    @Published private(set) var intercepted: [String] = []
    private(set) var reviewedIDs: Set<Int> = []
    var onSaved: ((FakeDataSet.SampleReport?) -> Void)?

    func intercept(_ url: URL) {
        intercepted.append(url.absoluteString)
    }

    func observe() {
        if model.phase == .reviewing && !model.selectionNeedsReview { reviewedIDs = model.selectedIDs }
        if model.handoffAcknowledged, let onSaved {
            self.onSaved = nil
            onSaved(FakeDataSet.sampleReport(selected: reviewedIDs))
        }
    }
}

struct DemoRoot: View {
    @ObservedObject var session: DemoSession
    @ObservedObject var model: CollectorModel

    var body: some View {
        VStack(spacing: 0) {
            DemoBanner(session: session, model: model)
            ContentView(model: model, updater: session.updater)
        }
        .environment(\.openURL, OpenURLAction { url in
            session.intercept(url)
            return .handled
        })
        .onReceive(model.objectWillChange) { _ in DispatchQueue.main.async { session.observe() } }
    }
}

struct DemoBanner: View {
    @ObservedObject var session: DemoSession
    @ObservedObject var model: CollectorModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("FAKE DATA DEMO · fictional contacts · simulated iPhone and simulated browser · no device, account, upload or network")
                .font(.system(size: 13, weight: .heavy))
            Text(narration).font(.system(size: 13, weight: .semibold))
            if let last = session.intercepted.last {
                Text("Browser open intercepted, nothing opened: \(last)")
                    .font(.system(size: 12)).lineLimit(1).truncationMode(.middle)
            }
        }
        .foregroundStyle(.black)
        .padding(.horizontal, 16).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.yellow)
        .accessibilityElement(children: .combine)
    }

    private var narration: String {
        switch model.phase {
        case .idle:
            switch model.backupEncryptionStatus {
            case .notChecked: return "Step 1: click Check iPhone. A simulated iPhone answers in about a second."
            case .checking: return "Simulated iPhone check…"
            default: return "Tick the agreement, then Start collection. The collection is simulated and takes a few seconds."
            }
        case .connecting, .transferring, .processing, .passwordRequired:
            return "Simulated collection in progress: fake bytes, no iPhone."
        case .selecting:
            return "Choose contacts: try Include all, Main or Review, a search, single checkboxes, or none. Then Continue to account."
        case .reviewing:
            if model.accountSaveAwaitingBrowserReceipt || model.browserReceived {
                return "Simulated browser received the preview and is simulating Save. No account is saved."
            }
            if model.destinationApproved { return "Account confirmed on the Mac. Simulated browser receiving the preview…" }
            if model.pendingDestinationApproval != nil {
                return "The simulated account asks to connect. Confirm it in the Mac dialog with Share with this account."
            }
            if !model.pairCode.isEmpty { return "Simulated browser connecting in about two seconds…" }
            return "Preparing a simulated one-time link…"
        case .completing, .completed:
            return "Simulated Save confirmed. The SAMPLE REPORT opens in its own window."
        case .cancelling: return "Stopping the simulated helper…"
        case .error: return "Simulated stop. Use the recovery shown below."
        }
    }
}

struct SampleReportView: View {
    let report: FakeDataSet.SampleReport?
    let runAgain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("SAMPLE REPORT · FAKE DATA")
                .font(.system(size: 22, weight: .heavy))
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background(Color.yellow, in: RoundedRectangle(cornerRadius: 6))
                .foregroundStyle(.black)
            Text("Simulated on this Mac from fictional contacts and the selection you shared. No website, account, upload or saved report was involved. The real report is created by the AMPLIFai website only after an account Save; its layout and wording may differ.")
                .fixedSize(horizontal: false, vertical: true)
            if let report {
                Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 6) {
                    GridRow { Text("Contacts shared"); Text("\(report.shared) of \(report.total)").bold() }
                    GridRow { Text("Matching calls"); Text("\(report.calls)").bold() }
                    GridRow { Text("Matching messages"); Text("\(report.messages)").bold() }
                    GridRow { Text("Shared with no matching activity"); Text("\(report.withoutActivity)").bold() }
                    GridRow { Text("Not included (left unselected)"); Text("\(report.notIncluded)").bold() }
                }
                Text("Most active shared contacts (simulated)").font(.headline)
                ForEach(Array(report.mostActive.enumerated()), id: \.offset) { index, row in
                    Text("\(index + 1). \(row.name) · \(row.calls) calls · \(row.messages) messages")
                }
            } else {
                Text("No contacts were shared, so there is nothing to report.")
            }
            HStack {
                Button("Run the demo again", action: runAgain)
                Button("Quit demo") { NSApp.terminate(nil) }
            }
            .padding(.top, 6)
        }
        .padding(24)
        .frame(width: 560, alignment: .leading)
    }
}

@MainActor
final class DemoDelegate: NSObject, NSApplicationDelegate {
    private var windows: [NSWindow] = []
    private var sessions: [DemoSession] = []

    static func menu() -> NSMenu {
        let main = NSMenu()
        let item = NSMenuItem()
        main.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit FAKE DATA DEMO", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        return main
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        openSession()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func openSession() {
        let session = DemoSession()
        session.onSaved = { [weak self] report in self?.showReport(report) }
        sessions.append(session)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "AMPLIFai FAKE DATA DEMO — fictional contacts, no iPhone or account"
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: DemoRoot(session: session, model: session.model))
        window.center()
        window.makeKeyAndOrderFront(nil)
        windows.append(window)
    }

    private func showReport(_ report: FakeDataSet.SampleReport?) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 560),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "SAMPLE REPORT — FAKE DATA (no website or account)"
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: SampleReportView(report: report) { [weak self, weak window] in
            window?.close()
            self?.openSession()
        })
        window.center()
        window.makeKeyAndOrderFront(nil)
        windows.append(window)
    }
}
