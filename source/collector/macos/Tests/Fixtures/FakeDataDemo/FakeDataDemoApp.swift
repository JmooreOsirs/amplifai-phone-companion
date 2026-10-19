import AppKit
import SwiftUI

/// FAKE DATA DEMO host for the production Mac views. It runs the real CollectorModel and ContentView with the
/// production run client over an in-process simulated run server, launches only the bundled fake helper,
/// intercepts every browser open and never starts the updater, status service or diagnostic upload. It declares
/// and registers no URL scheme: the simulated website hands its fictional app link to the model in-process.
/// The sample report is simulated here; no website report exists.
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
        // The demo never claims the production app link; its link is delivered in-process.
        if bundle.object(forInfoDictionaryKey: "CFBundleURLTypes") != nil { return "URL scheme declared" }
        return nil
    }
}

@MainActor
final class DemoSession: ObservableObject {
    /// Where the simulated website is in its own flow. It never asks for a Save consent.
    enum WebsiteStage: Int, Comparable {
        case agree, waitingForMac, connecting, receiving, saving, confirming, report
        static func < (left: Self, right: Self) -> Bool { left.rawValue < right.rawValue }
    }

    let server = SimulatedPhoneRunServer()
    let model: CollectorModel
    let inbox = PhoneRunLinkInbox()
    let updater = CompanionUpdater(enablePublishedUpdates: false, bundleInformation: [:])
    @Published private(set) var stage: WebsiteStage = .agree
    @Published private(set) var intercepted: [String] = []
    @Published private(set) var printRequested = false
    private(set) var reviewedIDs: Set<Int> = []
    private var connectedHandoff = ""
    private var savingHandoff = ""
    /// Brings the simulated website window forward; set by the app delegate.
    var showWebsite: () -> Void = {}
    /// Shows the Mac window when the fictional link arrives (standing in for the OS opening the app).
    var showMac: () -> Void = {}
    var runAgain: () -> Void = {}
    /// Closes this demo flow; set by the app delegate.
    var finishDemo: () -> Void = {}

    init() {
        model = CollectorModel(bundleInformation: ["CFBundleVersion": "fake-demo"],
                               runAuthority: PhoneRunAuthorityClient(transport: server,
                                                                     schedule: PhoneRunAuthorityClient.immediate))
        inbox.attach(model)
    }

    private var control: URL { Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("demo-control") }

    /// One file per simulated website step; the fake helper applies them only in the real bridge order.
    @discardableResult
    private func signal(_ step: FakeBridgeTimeline.WebsiteAction, _ handoff: String) -> Bool {
        try? FileManager.default.createDirectory(at: control, withIntermediateDirectories: true)
        return FileManager.default.createFile(atPath: control.appendingPathComponent("\(step.rawValue)-\(handoff)").path,
                                              contents: Data())
    }

    /// The simulated website's one upfront agreement for this collection.
    func agreeAndCollect() {
        guard stage == .agree else { return }
        let run = server.createRun(email: FakeDataSet.email, course: FakeDataSet.course)
        stage = .waitingForMac
        // Stands in for the browser opening amplifai-phone://start?...; nothing is registered with macOS.
        inbox.receive(server.appLink(run), returnBrowserBundleID: nil)
        showMac()
    }

    /// Every browser open from the production views lands here; nothing opens.
    func intercept(_ url: URL) {
        intercepted.append(url.absoluteString)
    }

    /// The Mac's handoff opens the account in the "browser": the simulated website comes forward.
    func openInBrowser(_ url: URL) {
        intercept(url)
        if url.query?.contains("continue=reviewed") == true && stage < .connecting { stage = .connecting }
        showWebsite()
    }

    func requestPrint() { printRequested = true }

    func observe() {
        if model.phase == .reviewing && !model.selectionNeedsReview { reviewedIDs = model.selectedIDs }
        // Simulates the website connecting on arrival with continue=reviewed.
        if stage == .connecting, model.handoffBound, connectedHandoff != model.handoffID {
            let handoff = model.handoffID
            connectedHandoff = handoff
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
                guard let self, self.model.handoffID == handoff else { return }
                self.signal(.connect, handoff)
            }
        }
        if model.destinationApproved, !model.handoffID.isEmpty, savingHandoff != model.handoffID {
            savingHandoff = model.handoffID
            saveAutomatically(model.handoffID)
        }
        if model.handoffAcknowledged && stage < .report { stage = .report }
    }

    /// The upfront approval covers saving: release, read every page, save, then finalize, with progress and
    /// no checkbox or Save consent. The helper reports received only after complete.
    private func saveAutomatically(_ handoff: String) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.stage = .receiving
            self.signal(.release, handoff)
            try? await Task.sleep(nanoseconds: 700_000_000)
            self.signal(.pages, handoff)
            try? await Task.sleep(nanoseconds: 1_800_000_000)
            guard self.model.handoffID == handoff else { return }
            self.stage = .saving
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            self.signal(.complete, handoff)
            self.stage = .confirming
            self.signal(.acknowledge, handoff)
            self.signal(.confirm, handoff)
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
            session.openInBrowser(url)
            return .handled
        })
        .environment(\.returnToBrowser, ReturnToBrowserAction { url, _, completion in
            session.openInBrowser(url)
            completion(true)
        })
        .onReceive(model.objectWillChange) { _ in DispatchQueue.main.async { session.observe() } }
    }
}

struct DemoBanner: View {
    @ObservedObject var session: DemoSession
    @ObservedObject var model: CollectorModel

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("FAKE DATA DEMO · fictional contacts · simulated iPhone, website and approval · no device, account, upload or network")
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

/// Local fixture standing in for the AMPLIFai website with the one upfront approval. It is not the real site:
/// nothing here reaches a server, and its sample report is computed on this Mac from fictional data.
struct SimulatedWebsiteView: View {
    @ObservedObject var session: DemoSession
    @ObservedObject var model: CollectorModel

    private let steps = ["Agree", "Collect on Mac", "Receive", "Save", "Report"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("SIMULATED AMPLIFai WEBSITE · local fixture, not the real site · fictional data")
                .font(.system(size: 12, weight: .heavy)).foregroundStyle(.black)
                .padding(.horizontal, 14).padding(.vertical, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.yellow)
            VStack(alignment: .leading, spacing: 14) {
                Text("Signed in as \(FakeDataSet.email) · \(FakeDataSet.course) · FAKE DATA")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                Text(stepLine).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                content
            }
            .padding(24)
            Spacer(minLength: 0)
        }
        .frame(width: 600, alignment: .topLeading)
        .frame(minHeight: 560, alignment: .top)
    }

    private var currentStep: Int {
        switch session.stage {
        case .agree: return 0
        case .waitingForMac: return 1
        case .connecting, .receiving: return 2
        case .saving, .confirming: return 3
        case .report: return 4
        }
    }

    private var stepLine: String {
        steps.enumerated().map { index, step in index < currentStep ? "✓ \(step)" : index == currentStep ? "● \(step)" : step }
            .joined(separator: "  ·  ")
    }

    @ViewBuilder private var content: some View {
        switch session.stage {
        case .agree:
            heading("Agree and collect")
            Text("FAKE DATA account · \(FakeDataSet.email) · \(FakeDataSet.course)")
                .font(.system(size: 13, weight: .semibold))
            VStack(alignment: .leading, spacing: 6) {
                Text("• One collection on this Mac, started from this account, for the course shown.")
                Text("• AMPLIFai Phone makes a temporary local backup of the iPhone and reads contacts, call history and message metadata from it. Message text never leaves the Mac.")
                Text("• After you choose people in AMPLIFai Phone, those contacts and their available call and message metadata are saved automatically to this account and course. Nothing you leave out is saved.")
                Text("• A different account or course, a later collection or anything else needs a new approval. iPhone Trust and your backup password are still asked on your devices.")
            }
            .font(.system(size: 13))
            .fixedSize(horizontal: false, vertical: true)
            Text("FAKE DATA: this approval is simulated inside the demo app. No account, server or upload is involved.")
                .font(.system(size: 12)).foregroundStyle(.secondary)
            Button("Agree and collect") { session.agreeAndCollect() }
                .keyboardShortcut(.defaultAction)
                .controlSize(.large)
        case .waitingForMac:
            heading("Continue in AMPLIFai Phone on your Mac")
            Text("Start collection there, choose contacts, then Continue. This page then saves them automatically.")
                .foregroundStyle(.secondary)
        case .connecting:
            progress("Connecting to AMPLIFai Phone on this Mac…")
        case .receiving:
            progress("Receiving the contacts you chose…")
        case .saving:
            progress("Saving to \(FakeDataSet.course)…")
        case .confirming:
            progress("Confirming the save with your Mac…")
        case .report:
            SampleReportView(report: FakeDataSet.sampleReport(selected: session.reviewedIDs),
                             finished: model.phase == .completed && !model.helperRunning,
                             printRequested: session.printRequested, print: session.requestPrint,
                             done: session.finishDemo, runAgain: session.runAgain)
        }
    }

    private func heading(_ text: String) -> some View {
        Text(text).font(.system(size: 22, weight: .bold))
    }

    @ViewBuilder private func progress(_ text: String) -> some View {
        heading(text)
        ProgressView()
        Text("Saving was approved once, at the start. Nothing to confirm here.")
            .font(.system(size: 12)).foregroundStyle(.secondary)
    }
}

struct SampleReportView: View {
    let report: FakeDataSet.SampleReport?
    /// The Mac has shown Saved and its fake helper has exited, so the demo can close cleanly.
    let finished: Bool
    let printRequested: Bool
    let print: () -> Void
    let done: () -> Void
    let runAgain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Your saved phone report · SAMPLE")
                .font(.system(size: 22, weight: .bold))
            Text("A sample computed on this Mac from fictional contacts and the selection you chose. The real report is produced by the AMPLIFai website after the account save; its layout may differ. No website, account or upload was involved.")
                .font(.system(size: 12)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let report {
                Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 6) {
                    GridRow { Text("Contacts saved"); Text("\(report.shared) of \(report.total)").bold() }
                    GridRow { Text("Matching calls"); Text("\(report.calls)").bold() }
                    GridRow { Text("Matching messages"); Text("\(report.messages)").bold() }
                    GridRow { Text("Saved with no matching activity"); Text("\(report.withoutActivity)").bold() }
                    GridRow { Text("Not included (left unselected)"); Text("\(report.notIncluded)").bold() }
                }
                Text("Most active saved contacts").font(.headline)
                ForEach(Array(report.mostActive.prefix(5).enumerated()), id: \.offset) { index, row in
                    Text("\(index + 1). \(row.name) · \(row.calls) calls · \(row.messages) messages")
                }
            }
            HStack(spacing: 16) {
                Button(finished ? "Done — I reviewed these results" : "Finishing on your Mac…", action: done)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .keyboardShortcut(.defaultAction)
                    .disabled(!finished)
                Button("Print or save PDF", action: print)
                    .controlSize(.large)
                Button("Run the demo again", action: runAgain)
                    .buttonStyle(.link)
            }
            .padding(.top, 6)
            if printRequested {
                Text("Print requested. FAKE DATA demo: recorded only; nothing was printed, saved or sent.")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
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

    /// The demo starts on the simulated website's Agree and collect page. The Mac window appears when the
    /// fictional link arrives; after the handoff the demo never brings it back.
    private func openSession() {
        website?.close()
        website = nil
        let session = DemoSession()
        sessions.append(session)
        let mac = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        mac.title = "AMPLIFai FAKE DATA DEMO — fictional contacts, no iPhone or account"
        mac.isReleasedWhenClosed = false
        mac.contentView = NSHostingView(rootView: DemoRoot(session: session, model: session.model))
        mac.center()
        windows.append(mac)
        let site = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 600),
                            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        site.title = "SIMULATED AMPLIFai WEBSITE — local fixture, not the real site"
        site.isReleasedWhenClosed = false
        site.contentView = NSHostingView(rootView: SimulatedWebsiteView(session: session, model: session.model))
        if let screen = NSScreen.main?.visibleFrame {
            site.setFrameTopLeftPoint(NSPoint(x: screen.maxX - 640, y: screen.maxY - 40))
        }
        website = site
        windows.append(site)
        session.showMac = { [weak mac] in mac?.makeKeyAndOrderFront(nil) }
        session.showWebsite = { [weak site] in site?.makeKeyAndOrderFront(nil) }
        session.runAgain = { [weak self] in self?.openSession() }
        session.finishDemo = { [weak site, weak mac] in
            site?.close()
            mac?.close()
        }
        site.makeKeyAndOrderFront(nil)
    }
}
