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
    @Published private(set) var saveRequested = false
    private var connectedHandoff = ""
    private(set) var reviewedIDs: Set<Int> = []
    /// Brings the simulated website window forward; set by the app delegate.
    var showWebsite: () -> Void = {}
    var runAgain: () -> Void = {}
    /// Closes this demo once the Mac has finished; set by the app delegate.
    var finishDemo: () -> Void = {}

    private var control: URL { Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("demo-control") }

    private func signal(_ step: String) -> Bool {
        try? FileManager.default.createDirectory(at: control, withIntermediateDirectories: true)
        return FileManager.default.createFile(atPath: control.appendingPathComponent("\(step)-\(model.handoffID)").path, contents: Data())
    }

    /// Every browser open from the production views lands here; nothing opens. The simulated website stays
    /// out of sight while the Mac asks for the account confirmation and appears only at the Mac's handoff.
    func intercept(_ url: URL) {
        intercepted.append(url.absoluteString)
    }

    /// The simulated website's one Save action; the fake helper then sends the account-save receipts.
    func saveOnWebsite() {
        if signal("save") { saveRequested = true }
    }

    func observe() {
        if model.phase == .reviewing && !model.selectionNeedsReview { reviewedIDs = model.selectedIDs }
        if model.handoffID.isEmpty { saveRequested = false }
        // Simulates the website connecting on arrival with continue=reviewed (the linked website change).
        if !model.handoffID.isEmpty && connectedHandoff != model.handoffID && !intercepted.isEmpty {
            let handoff = model.handoffID
            connectedHandoff = handoff
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                guard let self, self.model.handoffID == handoff else { return }
                _ = self.signal("connect")
            }
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
        .environment(\.returnToBrowser, ReturnToBrowserAction { session.showWebsite() })
        .onReceive(model.objectWillChange) { _ in DispatchQueue.main.async { session.observe() } }
    }
}

struct DemoBanner: View {
    @ObservedObject var session: DemoSession
    @ObservedObject var model: CollectorModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("FAKE DATA DEMO · fictional contacts · simulated iPhone and simulated website · no device, account, upload or network")
                .font(.system(size: 13, weight: .heavy))
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
}

/// Local fixture standing in for the AMPLIFai website after the linked website change. It is not the real
/// site: nothing here reaches a server, and its sample report is computed on this Mac from fictional data.
struct SimulatedWebsiteView: View {
    @ObservedObject var session: DemoSession
    @ObservedObject var model: CollectorModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("SIMULATED AMPLIFai WEBSITE · local fixture, not the real site · fictional data")
                .font(.system(size: 12, weight: .heavy)).foregroundStyle(.black)
                .padding(.horizontal, 14).padding(.vertical, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.yellow)
            VStack(alignment: .leading, spacing: 14) {
                Text("Signed in as \(FakeDataSet.email) · \(FakeDataSet.course)")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                content
            }
            .padding(24)
            Spacer(minLength: 0)
        }
        .frame(width: 560, alignment: .topLeading)
        .frame(minHeight: 520, alignment: .top)
    }

    @ViewBuilder private var content: some View {
        if model.handoffAcknowledged {
            SampleReportView(report: FakeDataSet.sampleReport(selected: session.reviewedIDs),
                             finished: model.phase == .completed && !model.helperRunning,
                             done: session.finishDemo, runAgain: session.runAgain)
        } else if model.accountSaveAwaitingBrowserReceipt || session.saveRequested {
            heading("Saving to your account…")
            ProgressView()
        } else if model.browserReceived, let report = FakeDataSet.sampleReport(selected: session.reviewedIDs) {
            heading("Your contacts from AMPLIFai Phone are ready")
            Text("\(report.shared) contacts · \(report.calls) calls · \(report.messages) messages")
            Text("Saving adds these to \(FakeDataSet.course). Your report opens next.")
                .foregroundStyle(.secondary)
            Button("Save and view report") { session.saveOnWebsite() }
                .keyboardShortcut(.defaultAction)
                .controlSize(.large)
        } else if model.destinationApproved {
            heading("Receiving from your Mac…")
            ProgressView()
        } else if !model.destinationIntentID.isEmpty {
            heading("Confirm on your Mac")
            Text("AMPLIFai Phone on your Mac is asking you to confirm this account before anything is shared.")
        } else if !model.handoffID.isEmpty {
            heading("Connecting to AMPLIFai Phone on this Mac…")
            ProgressView()
        } else {
            heading("Waiting for AMPLIFai Phone")
            Text("Choose contacts on your Mac, then Continue to account.")
        }
    }

    private func heading(_ text: String) -> some View {
        Text(text).font(.system(size: 22, weight: .bold))
    }
}

struct SampleReportView: View {
    let report: FakeDataSet.SampleReport?
    /// The Mac has shown Saved and its fake helper has exited, so the demo can close cleanly.
    let finished: Bool
    let done: () -> Void
    let runAgain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your saved phone report · SAMPLE")
                .font(.system(size: 22, weight: .bold))
            Text("A sample computed on this Mac from fictional contacts and the selection you shared. The real report is produced by the AMPLIFai website after an account Save; its layout may differ. No website, account or upload was involved.")
                .font(.system(size: 12)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let report {
                Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 6) {
                    GridRow { Text("Contacts shared"); Text("\(report.shared) of \(report.total)").bold() }
                    GridRow { Text("Matching calls"); Text("\(report.calls)").bold() }
                    GridRow { Text("Matching messages"); Text("\(report.messages)").bold() }
                    GridRow { Text("Shared with no matching activity"); Text("\(report.withoutActivity)").bold() }
                    GridRow { Text("Not included (left unselected)"); Text("\(report.notIncluded)").bold() }
                }
                Text("Most active shared contacts").font(.headline)
                ForEach(Array(report.mostActive.prefix(5).enumerated()), id: \.offset) { index, row in
                    Text("\(index + 1). \(row.name) · \(row.calls) calls · \(row.messages) messages")
                }
            }
            HStack(spacing: 16) {
                Button(finished ? "Done" : "Finishing on your Mac…", action: done)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .keyboardShortcut(.defaultAction)
                    .disabled(!finished)
                Button("Run the demo again", action: runAgain)
                    .buttonStyle(.link)
            }
            .padding(.top, 6)
        }
    }
}

@MainActor
final class DemoDelegate: NSObject, NSApplicationDelegate {
    private var windows: [NSWindow] = []
    private var sessions: [DemoSession] = []
    private var website: NSWindow?

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
        website?.close()
        website = nil
        let session = DemoSession()
        session.showWebsite = { [weak self, weak session] in
            guard let self, let session else { return }
            self.showWebsite(for: session)
        }
        session.runAgain = { [weak self] in self?.openSession() }
        sessions.append(session)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "AMPLIFai FAKE DATA DEMO — fictional contacts, no iPhone or account"
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: DemoRoot(session: session, model: session.model))
        window.center()
        window.makeKeyAndOrderFront(nil)
        windows.append(window)
        session.finishDemo = { [weak self, weak window] in
            self?.website?.close()
            window?.performClose(nil)
        }
    }

    /// The simulated website sits above the floating companion window, as a browser would.
    private func showWebsite(for session: DemoSession) {
        if website == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 560),
                                  styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "SIMULATED AMPLIFai WEBSITE — local fixture, not the real site"
            window.isReleasedWhenClosed = false
            window.contentView = NSHostingView(rootView: SimulatedWebsiteView(session: session, model: session.model))
            if let screen = NSScreen.main?.visibleFrame {
                window.setFrameTopLeftPoint(NSPoint(x: screen.maxX - 600, y: screen.maxY - 40))
            }
            website = window
            windows.append(window)
        }
        website?.level = NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)
        website?.makeKeyAndOrderFront(nil)
    }
}
