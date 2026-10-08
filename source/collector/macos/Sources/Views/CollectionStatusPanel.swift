import SwiftUI

struct CollectionStatusPanel: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ObservedObject var model: CollectorModel
    @State private var confirmingDiscard = false

    private let stages = ["Connect", "Receive", "Read", "Review & save"]

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
        VStack(alignment: .leading, spacing: 16) {
            if showsActivity { activityWidget }
            else {
                Text(model.statusTitle)
                    .font(.custom("Arial", size: 21).weight(.bold))
                    .foregroundStyle(Brand.heading)
                    .accessibilityAddTraits(.isHeader)
                Text(model.statusDetail).font(.custom("Arial", size: 15))
            }
            if model.phase == .error {
                Text(model.errorMessage).foregroundStyle(.red).accessibilityAddTraits(.isStaticText)
                if !model.failureContext.isEmpty {
                    Text(model.failureContext).font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                }
                if !model.safeSupportCode.isEmpty {
                    Text("The app sends this bounded failure code automatically. It contains the build, random reference, failure category, elapsed time and byte counts, not phone records or passwords.")
                        .font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                    if model.diagnosticDelivery == .sending {
                        Text("Sending safe diagnostic…").font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                    } else if model.diagnosticDelivery == .sent {
                        Text("Safe diagnostic received. Keep the reference below for support.").font(.custom("Arial", size: 13)).foregroundStyle(Brand.body)
                    } else if model.diagnosticDelivery == .failed {
                        Text("Automatic report could not be delivered. Copy the code or retry; collection remains stopped safely.").font(.custom("Arial", size: 13)).foregroundStyle(Brand.body)
                        Button("Retry safe report") { Task { await model.reportDiagnosticIfNeeded(forceRetry: true) } }
                            .buttonStyle(SecondaryButton())
                    }
                    Button("Copy safe support code") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(model.safeSupportCode, forType: .string)
                    }.buttonStyle(SecondaryButton())
                    Text(model.safeSupportCode).font(.custom("Arial", size: 12)).textSelection(.enabled)
                }
                ForEach(Array(model.recoverySteps.enumerated()), id: \.offset) { index, step in
                    Text("\(index + 1). \(step)").font(.custom("Arial", size: 14))
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
        .task(id: model.safeSupportCode) { await model.reportDiagnosticIfNeeded() }
        .confirmationDialog("Discard the local unsaved review?", isPresented: $confirmingDiscard) {
            Button("Discard review and disconnect", role: .destructive) { model.disconnect() }
            Button("Keep review", role: .cancel) {}
        } message: {
            Text("Receiving a browser preview is not a saved account receipt. This discards the companion's recovery copy; it does not delete separately saved account data.")
        }
    }

    private func clock(_ seconds: Int) -> String {
        String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    private var nextAction: String {
        switch model.phase {
        case .connecting: return "Next: receive the iPhone backup on this Mac. Keep the phone unlocked for Trust prompts."
        case .transferring: return "Next: read available sources locally, then choose contacts to review."
        case .processing: return "Next: review observed sources and choose contacts before any browser handoff."
        default: return "Next: inspect receipt-confirmed sources in your signed-in account."
        }
    }

    private var activityWidget: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let elapsed = max(0, Int(context.date.timeIntervalSince(model.collectionStartedAt ?? context.date)))
            VStack(alignment: .leading, spacing: 18) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 24) { activityHeading; Spacer(minLength: 0); elapsedClock(elapsed) }
                    VStack(alignment: .leading, spacing: 12) { activityHeading; elapsedClock(elapsed) }
                }
                if model.phase != .completing {
                    Button("Cancel collection") { model.disconnect() }.buttonStyle(SecondaryButton())
                }
                HStack(spacing: 6) {
                    ForEach(stages.indices, id: \.self) { index in
                        Text(stages[index])
                            .font(.custom("Arial", size: 12).weight(index == activeStage ? .bold : .regular))
                            .foregroundStyle(index == activeStage ? Brand.background : index < activeStage ? Brand.heading : Brand.muted)
                            .frame(maxWidth: .infinity, minHeight: 32)
                            .background(index == activeStage ? Brand.lime : index < activeStage ? Brand.blue.opacity(0.35) : Brand.panel,
                                        in: RoundedRectangle(cornerRadius: 7))
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Stage \(activeStage + 1) of \(stages.count): \(stages[activeStage])")
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
                        .font(.custom("Arial", size: 13).weight(.bold)).foregroundStyle(Brand.heading)
                } else if model.phase != .completing {
                    HStack(spacing: 7) {
                        ForEach(0..<3) { index in
                            Circle().fill(reduceMotion || index == Int(context.date.timeIntervalSince1970) % 3
                                          ? Brand.lime : Brand.blue.opacity(0.45))
                                .frame(width: 7, height: 7)
                        }
                        Text(model.phase == .processing ? "Reading source metadata locally"
                             : model.phase == .connecting ? "Waiting for iPhone connection" : "Waiting for backup measurement")
                            .foregroundStyle(Brand.muted)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(model.phase == .processing ? "Reading source metadata locally"
                                        : model.phase == .connecting ? "Waiting for iPhone connection" : "Waiting for backup measurement")
                }
                if model.transferIsKnown && model.phase != .completing {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 145), spacing: 10)], spacing: 10) {
                        metric("Received", CollectorModel.byteDescription(model.transferBytes))
                        metric("Retained locally", CollectorModel.byteDescription(model.retainedBytes))
                        metric(model.phase == .processing ? "Parsing copies" : "Average received", model.phase == .processing
                               ? CollectorModel.byteDescription(model.processedBytes)
                               : "\(CollectorModel.byteDescription(Int64(model.transferBytesPerSecond)))/s")
                    }
                }
                if model.phase == .transferring {
                    if let estimate = model.backupRemainingEstimate(at: context.date) {
                        Text("Observed backup pace suggests ~\(estimate.lowerBound)–\(estimate.upperBound) min remaining if it holds. Parsing, review and account save are additional.")
                            .foregroundStyle(Brand.body)
                    } else {
                        Text("No reliable backup-time estimate yet. Device percentage is not whole-process progress.")
                            .foregroundStyle(Brand.muted)
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
                    .font(.custom("Arial", size: 12)).foregroundStyle(Brand.muted)
            }
            .font(.custom("Arial", size: 13))
            .padding(18)
            .background(Brand.background.opacity(0.65), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Brand.border, lineWidth: 1))
        }
    }

    private var activityHeading: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("LOCAL PHONE PROCESS · \(activeStage + 1) OF \(stages.count)")
                .font(.custom("Arial", size: 11).weight(.bold)).tracking(1.2).foregroundStyle(Brand.lime)
            Text(model.statusTitle)
                .font(.custom("Arial", size: 23).weight(.bold)).foregroundStyle(Brand.heading)
                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
        }
    }

    private func elapsedClock(_ seconds: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(clock(seconds)).font(.custom("Arial", size: 32).weight(.bold)).monospacedDigit().foregroundStyle(Brand.heading)
            Text("Elapsed · not time remaining").font(.custom("Arial", size: 11)).foregroundStyle(Brand.muted)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Observed elapsed time \(seconds / 60) minutes, \(seconds % 60) seconds")
    }

    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label.uppercased()).font(.custom("Arial", size: 10).weight(.bold)).tracking(0.8).foregroundStyle(Brand.muted)
            Text(value).font(.custom("Arial", size: 19).weight(.bold)).monospacedDigit().foregroundStyle(Brand.heading)
                .minimumScaleFactor(0.8).lineLimit(1)
        }
        .frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
        .padding(10)
        .background(Brand.panel, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border, lineWidth: 1))
    }

    private func activityAge(at now: Date) -> String {
        guard let last = model.lastCollectionActivityAt else { return "Waiting for first measured data progress" }
        let seconds = max(0, Int(now.timeIntervalSince(last)))
        let age = seconds < 60 ? "\(seconds)s" : "\(seconds / 60)m \(seconds % 60)s"
        return "Last measured data progress \(age) ago"
    }

    @ViewBuilder private var actions: some View {
        if model.phase == .idle || model.phase == .error || model.phase == .completed {
            Button("Connect iPhone") { model.connect() }.buttonStyle(LimeButton()).disabled(!model.canConnect)
            if model.needsStorageReview && !model.helperRunning {
                Button("Check temporary data") { model.inspectResidue() }.buttonStyle(SecondaryButton())
            }
        } else if model.phase == .selecting || model.phase == .reviewing {
            Button("Disconnect") { confirmingDiscard = true }.buttonStyle(SecondaryButton())
        } else if model.phase == .passwordRequired {
            Button("Cancel collection") { model.disconnect() }.buttonStyle(SecondaryButton())
        }
    }
}
