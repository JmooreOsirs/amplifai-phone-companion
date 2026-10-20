import AppKit
import SwiftUI

struct CollectionStatusPanel: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.returnToBrowser) private var returnToBrowser
    @ObservedObject var model: CollectorModel
    @State private var confirmingDiscard = false
    @State private var copyStatus = ""
    @State private var showingTechnicalDetails = false

    private let stages = ["Connect iPhone", "Receive backup", "Read locally", "Clean up"]

    private var activeStage: Int {
        switch model.phase {
        case .connecting: return 0
        case .transferring: return 1
        case .processing: return 2
        default: return 3
        }
    }

    private var showsActivity: Bool {
        model.phase == .connecting || model.phase == .transferring ||
            model.phase == .processing || model.phase == .completing
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.section) {
            if showsActivity { activityWidget }
            else {
                Text(model.statusTitle)
                    .font(Brand.headingFont(model.phase == .error ? 26 : 21))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                Text(model.statusDetail).font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
            }
            if model.phase == .error {
                Text(model.settingCheckAfterCollectionFailure ? "Previous attempt: \(model.errorMessage)" : model.errorMessage)
                    .font(Brand.bodyFont(18))
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
                if !model.isCallHistorySetupFailure {
                    ForEach(Array(model.recoverySteps.enumerated()), id: \.offset) { index, step in
                        Text("\(index + 1). \(step)").font(Brand.bodyFont(16))
                    }
                } else {
                    Text("Check the iPhone again below. The read-only setting check comes before any new collection.")
                        .font(Brand.bodyFont(18))
                        .foregroundStyle(Brand.body)
                }
                Button(showingTechnicalDetails ? "Hide technical details" : "Show technical details and support code") {
                    showingTechnicalDetails.toggle()
                }
                .buttonStyle(SecondaryButton())
                if showingTechnicalDetails {
                    technicalDetails
                }
            }
            if !showsActivity {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { actions }
                    VStack(alignment: .leading, spacing: 12) { actions }
                }
            }
        }
        .panel()
        .onChange(of: model.safeSupportCode) { _, _ in copyStatus = "" }
        .task(id: model.safeSupportCode) { await model.reportDiagnosticIfNeeded() }
        .confirmationDialog("Discard the local unsaved review?", isPresented: $confirmingDiscard) {
            Button("Discard review and disconnect", role: .destructive) { model.disconnect() }
            Button("Keep review", role: .cancel) {}
        } message: {
            Text("Receiving a browser preview is not a saved account receipt. This discards the companion's recovery copy; it does not delete separately saved account data.")
        }
    }

    private var technicalDetails: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            if !model.failureContext.isEmpty {
                Text(model.failureContext).foregroundStyle(Brand.muted).textSelection(.enabled)
            }
            if let space = model.workspaceSpaceStatus {
                Text(space).foregroundStyle(Brand.body).textSelection(.enabled)
            }
            if model.diagnosticRetention == .retained {
                Text("These safe source details were retained on this Mac.").foregroundStyle(Brand.muted)
            }
            Button("Copy error details") {
                let details = [model.statusTitle, model.errorMessage, model.failureContext,
                               model.workspaceSpaceStatus ?? "",
                               model.safeSupportCode.isEmpty ? "" : "Support code: \(model.safeSupportCode)"]
                    .filter { !$0.isEmpty }.joined(separator: "\n")
                NSPasteboard.general.clearContents()
                copyStatus = NSPasteboard.general.setString(details, forType: .string)
                    ? "Error details copied." : "Copy failed. Select the error text or support code instead."
            }
            .buttonStyle(SecondaryButton())
            if !copyStatus.isEmpty { Text(copyStatus).foregroundStyle(Brand.muted) }
            if !model.safeSupportCode.isEmpty {
                Text("Only this bounded failure code is sent automatically: build, random reference, failure category, stage, elapsed time and byte counts. It excludes records, names, passwords and paths.")
                    .foregroundStyle(Brand.muted)
                if model.diagnosticDelivery == .sending {
                    Text("Sending safe diagnostic…").foregroundStyle(Brand.muted)
                } else if model.diagnosticDelivery == .sent {
                    Text("Safe diagnostic received. Keep this reference for support.").foregroundStyle(Brand.body)
                } else if model.diagnosticDelivery == .failed {
                    Text("The safe report was not delivered. Collection remains stopped.").foregroundStyle(Brand.body)
                    Button("Retry safe report") { Task { await model.reportDiagnosticIfNeeded(forceRetry: true) } }
                        .buttonStyle(SecondaryButton())
                }
                Button("Copy safe support code") {
                    NSPasteboard.general.clearContents()
                    copyStatus = NSPasteboard.general.setString(model.safeSupportCode, forType: .string)
                        ? "Support code copied." : "Copy failed. Select the support code instead."
                }
                .buttonStyle(SecondaryButton())
                Text(model.safeSupportCode).font(Brand.bodyFont(14)).textSelection(.enabled)
            }
        }
        .font(Brand.bodyFont(15))
        .brandControls()
    }

    private func clock(_ seconds: Int) -> String {
        String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    private var nextAction: String {
        switch model.phase {
        case .connecting: return "Next: receive the iPhone backup on this Mac. Keep the phone unlocked for Trust prompts."
        case .transferring: return "Next: read available sources locally, then choose which contacts to share."
        case .processing: return "Next: choose contacts, then Continue to save them to your approved account."
        default: return "Next: inspect receipt-confirmed sources in your signed-in account."
        }
    }

    private var activityWidget: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let elapsed = max(0, Int(context.date.timeIntervalSince(model.collectionStartedAt ?? context.date)))
            VStack(alignment: .leading, spacing: Brand.Space.item) {
                activityHeading(at: context.date)
                if model.phase != .completing {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: Brand.Space.item) { stopActions }
                        VStack(alignment: .leading, spacing: Brand.Space.item) { stopActions }
                    }
                }
                HStack(spacing: 10) {
                    Image(systemName: "iphone.gen3")
                    Rectangle().fill(Brand.blue).frame(maxWidth: .infinity).frame(height: 2)
                    Image(systemName: "desktopcomputer")
                }
                .font(.system(size: 26))
                .foregroundStyle(Brand.blue)
                .padding(.horizontal, 18)
                .accessibilityLabel("Local iPhone to Mac collection; this illustration is not a progress meter")
                stageChips
                if model.collectionStartedAt != nil || (model.transferIsKnown && model.phase != .completing) {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: Brand.Space.item)],
                              alignment: .leading, spacing: Brand.Space.item) {
                        if model.collectionStartedAt != nil { elapsedClock(elapsed) }
                        if model.transferIsKnown && model.phase != .completing {
                            metric("Received", CollectorModel.byteDescription(model.transferBytes))
                            metric("Retained locally", CollectorModel.byteDescription(model.retainedBytes))
                        }
                    }
                    .brandWell()
                }
                if model.phase == .transferring && model.progressIsKnown {
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 5).fill(Brand.border)
                            RoundedRectangle(cornerRadius: 5).fill(Brand.lime)
                                .frame(width: geometry.size.width * model.progress / 100)
                        }
                    }
                    .frame(height: 10)
                    .animation(reduceMotion ? nil : .easeOut(duration: 0.35), value: model.progress)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("iPhone-reported backup transfer")
                    .accessibilityValue("\(Int(model.progress)) percent")
                    Text("\(Int(model.progress))% reported by iPhone · backup transfer only")
                        .font(Brand.bodyFont(16).weight(.bold)).foregroundStyle(Brand.heading)
                } else if model.phase != .completing {
                    HStack(spacing: 7) {
                        ForEach(0..<3) { index in
                            Circle().fill(hasRecentMeasuredActivity(at: context.date) && !reduceMotion
                                          && index == Int(context.date.timeIntervalSince1970) % 3
                                          ? Brand.lime : Brand.blue.opacity(0.45))
                                .frame(width: 7, height: 7)
                        }
                        Text(activityLabel(at: context.date))
                            .foregroundStyle(Brand.muted)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(activityLabel(at: context.date))
                }
                if model.transferIsKnown && model.phase != .completing {
                    Text(model.phase == .processing
                         ? "Parsing copies: \(CollectorModel.byteDescription(model.processedBytes))"
                         : "Average received rate: \(CollectorModel.byteDescription(Int64(model.transferBytesPerSecond)))/s")
                        .foregroundStyle(Brand.muted)
                }
                if model.phase == .connecting || model.phase == .transferring {
                    if let estimate = model.backupRemainingEstimate() {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Estimated backup transfer remaining: \(estimate.lowerBound)–\(estimate.upperBound) min")
                                .font(Brand.bodyFont(15).weight(.bold)).foregroundStyle(Brand.heading)
                            Text("Based on recent iPhone progress. Reading sources and saving to your account take additional time.")
                                .font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
                        }
                    } else {
                        Text("Time remaining is not available yet. An estimate needs steady, measured backup progress.")
                            .font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
                    }
                }
                if model.phase == .processing && model.processedBytes == 0 {
                    Text("Waiting for the first local parsing measurement.").foregroundStyle(Brand.muted)
                }
                if model.phase == .completing {
                    Text("Account save acknowledged · finishing local cleanup").foregroundStyle(Brand.body)
                } else {
                    HStack(spacing: 8) {
                        Circle().fill(Brand.blue).frame(width: 7, height: 7)
                        Text(activityAge(at: context.date)).foregroundStyle(Brand.muted)
                    }
                }
                Text(nextAction).foregroundStyle(Brand.body)
                Text("Temporary local bytes are not saved account records.")
                    .font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
            }
            .font(Brand.bodyFont(15))
        }
    }

    private var stageChips: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 6)], spacing: 6) {
            ForEach(stages.indices, id: \.self) { index in
                Text(stages[index])
                    .font(Brand.bodyFont(13).weight(.bold))
                    .foregroundStyle(index == activeStage ? Brand.background : index < activeStage ? Brand.heading : Brand.muted)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .frame(maxWidth: .infinity, minHeight: 32)
                    .background(index == activeStage ? Brand.lime : Brand.background, in: Capsule())
                    .overlay(Capsule().stroke(index == activeStage ? Brand.lime : Brand.border, lineWidth: 1))
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.3), value: activeStage)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Stage \(activeStage + 1) of \(stages.count): \(stages[activeStage])")
    }

    @ViewBuilder private var stopActions: some View {
        Button("Stop on this Mac") { model.disconnect() }
            .buttonStyle(SecondaryButton()).frame(minHeight: 44)
        if let controlURL = model.accountControlURL {
            Button("Stop in account…") {
                returnToBrowser(controlURL, preferredBrowserBundleID: model.returnBrowserBundleID)
            }
            .buttonStyle(QuietAction())
            .help("Open your account's Stop control. Opening the account does not stop this collection by itself.")
        }
    }

    private func activityHeading(at date: Date) -> some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Text("ON THIS MAC · STAGE \(activeStage + 1) OF \(stages.count)")
                .font(Brand.bodyFont(13).weight(.bold)).tracking(0.8).foregroundStyle(Brand.muted)
            Text(activityTitle(at: date))
                .font(Brand.headingFont(23)).foregroundStyle(Brand.heading)
                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text(activityDetail(at: date)).font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
        }
    }

    private func activityTitle(at date: Date) -> String {
        switch model.collectionAttention(at: date) {
        case .connectionUnconfirmed: return "Connection status unavailable"
        case .backupWaiting: return "Waiting for backup data"
        case .backupStale: return "Needs attention"
        case nil:
            return model.phase == .processing && model.processedBytes == 0
                ? "Reading available sources" : model.statusTitle
        }
    }

    private func activityLabel(at date: Date) -> String {
        switch model.collectionAttention(at: date) {
        case .connectionUnconfirmed: return "No connection confirmation received"
        case .backupWaiting: return "No backup data measured yet"
        case .backupStale: return "No recent backup data measured"
        case nil:
            if model.phase == .processing { return "Reading contacts and activity locally" }
            if model.phase == .connecting { return "Waiting for iPhone connection confirmation" }
            return model.lastBackupActivityAt != nil ? "Receiving measured backup data" : "Waiting for iPhone backup data"
        }
    }

    private func activityDetail(at date: Date) -> String {
        switch model.collectionAttention(at: date) {
        case .connectionUnconfirmed:
            return "No iPhone connection has been confirmed in this attempt. Check the phone for a Trust/passcode prompt and look for the iPhone in Finder."
        case .backupWaiting:
            let observed = model.connectionConfirmedAt != nil
                ? "A \(model.connectionTransport) connection was confirmed; no backup data measured."
                : "No backup data measured."
            return "\(observed) Collection allows up to thirty minutes for initial readiness. Check for an iPhone passcode prompt. If Finder is backing up this iPhone, let it finish; its latest-backup date/time should update."
        case .backupStale:
            return "No new backup data has been measured for at least a minute. Check for an iPhone passcode prompt and inspect its current backup status in Finder."
        case nil: break
        }
        switch model.phase {
        case .connecting: return "Connect by USB, unlock your iPhone and tap Trust if asked."
        case .transferring: return model.lastBackupActivityAt != nil
            ? "Keep the cable connected. The backup is being read on this Mac."
            : model.connectionConfirmedAt != nil
            ? "A \(model.connectionTransport) connection was confirmed. Waiting for backup data; respond to any iPhone passcode prompt."
            : "Keep the cable connected while waiting for backup data from your iPhone."
        case .processing: return "Reading available contacts and activity, then cleaning up temporary backup files."
        default: return model.statusDetail
        }
    }

    private func hasRecentMeasuredActivity(at now: Date) -> Bool {
        guard model.phase == .transferring || model.phase == .processing,
              let last = model.lastCollectionActivityAt else { return false }
        return now.timeIntervalSince(last) < 30
    }

    private func elapsedClock(_ seconds: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(clock(seconds)).font(Brand.headingFont(28)).monospacedDigit().foregroundStyle(Brand.lime)
            Text("Elapsed · not time remaining").font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
        }
        .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
        .brandRaisedCard()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Observed elapsed time \(seconds / 60) minutes, \(seconds % 60) seconds")
    }

    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(value).font(Brand.headingFont(28)).monospacedDigit().foregroundStyle(Brand.heading)
                .minimumScaleFactor(0.8).lineLimit(1)
            Text(label.uppercased()).font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
        }
        .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
        .brandRaisedCard()
    }

    private func activityAge(at now: Date) -> String {
        func age(since date: Date) -> String {
            let seconds = max(0, Int(now.timeIntervalSince(date)))
            return seconds < 60 ? "\(seconds)s" : "\(seconds / 60)m \(seconds % 60)s"
        }
        if let last = model.lastCollectionActivityAt {
            return "Last measured data progress \(age(since: last)) ago"
        }
        if model.phase == .processing { return "Waiting for first measured data progress" }
        if let confirmed = model.connectionConfirmedAt {
            return "Connection confirmed \(age(since: confirmed)) ago · no backup data measured"
        }
        if let started = model.collectionStartedAt {
            return "Connection check started \(age(since: started)) ago · no confirmation received"
        }
        return "Waiting for connection confirmation"
    }

    @ViewBuilder private var actions: some View {
        if model.phase == .idle || model.phase == .error || model.phase == .completed {
            if model.needsStorageReview && !model.helperRunning {
                Button("Check temporary data") { model.inspectResidue() }.buttonStyle(SecondaryButton())
            }
            if model.phase == .completed {
                Button("Continue to Signed in Account") {
                    model.continueToSignedInAccount { url, browser in
                        returnToBrowser(url, preferredBrowserBundleID: browser)
                    }
                }
                    .buttonStyle(LimeButton())
                    .disabled(!model.canContinueToSignedInAccount)
                Button("Close") { NSApp.keyWindow?.performClose(nil) }
                    .buttonStyle(SecondaryButton())
            }
        } else if model.phase == .selecting || model.phase == .reviewing {
            Button("Discard review…") { confirmingDiscard = true }.buttonStyle(SecondaryButton())
        } else if model.phase == .passwordRequired {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Brand.Space.item) { stopActions }
                VStack(alignment: .leading, spacing: Brand.Space.item) { stopActions }
            }
        }
    }
}
