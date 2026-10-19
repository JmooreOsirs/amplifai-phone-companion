import AppKit
import SwiftUI

struct CollectionStatusPanel: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ObservedObject var model: CollectorModel
    @State private var confirmingDiscard = false
    @State private var copyStatus = ""
    @State private var showingTechnicalDetails = false
    @State private var cancelHovered = false

    private let stages = ["Connect iPhone", "Receive backup", "Read locally", "Clean up"]
    // Existing /experience messaging, shown as waiting content rather than collection progress.
    private let waitingLines = [
        "The system proposes. You decide.",
        "Relationship signals, not predictions.",
        "No new CRM. No starting over."
    ]

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
        case .processing: return "Next: choose which contacts to share. Nothing is shared until you confirm."
        default: return "Next: inspect receipt-confirmed sources in your signed-in account."
        }
    }

    private var activityWidget: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let elapsed = max(0, Int(context.date.timeIntervalSince(model.collectionStartedAt ?? context.date)))
            VStack(alignment: .leading, spacing: Brand.Space.section) {
                activityHeading
                if model.phase != .completing {
                    Button("Cancel collection") { model.disconnect() }
                        .buttonStyle(SecondaryButton())
                        .frame(minHeight: 44)
                        .onHover { inside in
                            guard inside != cancelHovered else { return }
                            if inside { NSCursor.pointingHand.push() } else { NSCursor.pop() }
                            cancelHovered = inside
                        }
                        .onDisappear {
                            if cancelHovered { NSCursor.pop(); cancelHovered = false }
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
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: Brand.Space.item)],
                          alignment: .leading, spacing: Brand.Space.item) {
                    elapsedClock(elapsed)
                    if model.transferIsKnown && model.phase != .completing {
                        metric("Received", CollectorModel.byteDescription(model.transferBytes))
                        metric("Retained locally", CollectorModel.byteDescription(model.retainedBytes))
                    }
                }
                .brandWell()
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
                            Circle().fill(reduceMotion || index == Int(context.date.timeIntervalSince1970) % 3
                                          ? Brand.lime : Brand.blue.opacity(0.45))
                                .frame(width: 7, height: 7)
                        }
                        Text(model.phase == .processing ? "Reading source metadata locally"
                             : model.phase == .connecting ? "Waiting for iPhone connection"
                             : model.transferIsKnown ? "Receiving measured backup bytes" : "Waiting for first received byte")
                            .foregroundStyle(Brand.muted)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(model.phase == .processing ? "Reading source metadata locally"
                                        : model.phase == .connecting ? "Waiting for iPhone connection"
                                        : model.transferIsKnown ? "Receiving measured backup bytes" : "Waiting for first received byte")
                }
                if model.transferIsKnown && model.phase != .completing {
                    Text(model.phase == .processing
                         ? "Parsing copies: \(CollectorModel.byteDescription(model.processedBytes))"
                         : "Average received rate: \(CollectorModel.byteDescription(Int64(model.transferBytesPerSecond)))/s")
                        .foregroundStyle(Brand.muted)
                }
                if model.phase == .transferring && !model.transferIsKnown {
                    Text("No backup bytes measured yet. The elapsed clock is not transfer progress.")
                        .foregroundStyle(Brand.muted)
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
                if model.phase == .connecting || model.phase == .transferring || model.phase == .processing {
                    Text("While you wait · \(waitingLines[reduceMotion ? 0 : max(0, elapsed / 12) % waitingLines.count])")
                        .font(Brand.bodyFont(16))
                        .foregroundStyle(Brand.muted)
                }
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

    private var activityHeading: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Text("COLLECTING ON THIS MAC · STAGE \(activeStage + 1) OF \(stages.count)")
                .font(Brand.bodyFont(13).weight(.bold)).tracking(0.8).foregroundStyle(Brand.muted)
            Text(model.phase == .transferring && !model.transferIsKnown
                 ? "Waiting for the first received byte" : model.phase == .processing && model.processedBytes == 0
                 ? "Reading available sources" : model.statusTitle)
                .font(Brand.headingFont(23)).foregroundStyle(Brand.heading)
                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text(model.statusDetail).font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
        }
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
        guard let last = model.lastCollectionActivityAt else { return "Waiting for first measured data progress" }
        let seconds = max(0, Int(now.timeIntervalSince(last)))
        let age = seconds < 60 ? "\(seconds)s" : "\(seconds / 60)m \(seconds % 60)s"
        return "Last measured data progress \(age) ago"
    }

    @ViewBuilder private var actions: some View {
        if model.phase == .idle || model.phase == .error || model.phase == .completed {
            if model.needsStorageReview && !model.helperRunning {
                Button("Check temporary data") { model.inspectResidue() }.buttonStyle(SecondaryButton())
            }
            if model.phase == .completed {
                // The report itself needs the account's course scope, so open the signed-in account page.
                Link("Open your account to view the saved report", destination: URL(string: "https://amplifai.satoris.ai/phone/account")!)
                    .buttonStyle(LimeButton())
            }
        } else if model.phase == .selecting || model.phase == .reviewing {
            Button("Discard review…") { confirmingDiscard = true }.buttonStyle(SecondaryButton())
        } else if model.phase == .passwordRequired {
            Button("Cancel collection") { model.disconnect() }.buttonStyle(SecondaryButton())
        }
    }
}
