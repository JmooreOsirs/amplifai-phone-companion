import SwiftUI

struct CollectionStatusPanel: View {
    @ObservedObject var model: CollectorModel
    @State private var confirmingDiscard = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(model.statusTitle)
                .font(.custom("Arial", size: 21).weight(.bold))
                .foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            Text(model.statusDetail).font(.custom("Arial", size: 15))
            if model.phase == .connecting || model.phase == .transferring || model.phase == .processing || model.phase == .completing {
                if model.phase == .transferring && model.progressIsKnown {
                    ProgressView(value: model.progress, total: 100) {
                        Text("iPhone-reported backup transfer\(model.connectionTransport.isEmpty ? "" : " · \(model.connectionTransport)")")
                    } currentValueLabel: {
                        Text("\(Int(model.progress))% of backup transfer")
                    }
                    .tint(Brand.lime)
                } else {
                    ProgressView(model.phase == .processing ? "Finishing metadata reading and temporary cleanup" : "Waiting for observed progress")
                        .tint(Brand.lime)
                }
                Text(model.progressExplanation).font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                if model.transferIsKnown {
                    Text(model.transferSummary).font(.custom("Arial", size: 13)).foregroundStyle(Brand.body)
                    if model.phase == .transferring {
                        Text(model.transferActivity).font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                    }
                }
                if model.phase == .processing && model.processedBytes > 0 {
                    Text("Local parsing copies processed: \(CollectorModel.byteDescription(model.processedBytes))")
                        .font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                }
                if let started = model.collectionStartedAt {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let seconds = max(0, Int(context.date.timeIntervalSince(started)))
                        Text("Observed elapsed time: \(seconds / 60)m \(seconds % 60)s · not a remaining-time estimate")
                            .font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                    }
                }
            }
            if model.phase == .error {
                Text(model.errorMessage).foregroundStyle(.red).accessibilityAddTraits(.isStaticText)
                if !model.failureContext.isEmpty {
                    Text(model.failureContext).font(.custom("Arial", size: 13)).foregroundStyle(Brand.muted)
                }
                ForEach(Array(model.recoverySteps.enumerated()), id: \.offset) { index, step in
                    Text("\(index + 1). \(step)").font(.custom("Arial", size: 14))
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { actions }
                VStack(alignment: .leading, spacing: 12) { actions }
            }
        }
        .panel()
        .confirmationDialog("Discard the local unsaved review?", isPresented: $confirmingDiscard) {
            Button("Discard review and disconnect", role: .destructive) { model.disconnect() }
            Button("Keep review", role: .cancel) {}
        } message: {
            Text("Receiving a browser preview is not a saved account receipt. This discards the companion's recovery copy; it does not delete separately saved account data.")
        }
    }

    @ViewBuilder private var actions: some View {
        if model.phase == .idle || model.phase == .error || model.phase == .completed {
            Button("Connect iPhone") { model.connect() }.buttonStyle(LimeButton()).disabled(!model.canConnect)
            if model.needsStorageReview && !model.helperRunning {
                Button("Check temporary data") { model.inspectResidue() }.buttonStyle(SecondaryButton())
            }
        } else if model.phase == .selecting || model.phase == .reviewing {
            Button("Disconnect") { confirmingDiscard = true }.buttonStyle(SecondaryButton())
        } else if model.phase != .cancelling && model.phase != .completing {
            Button("Cancel collection") { model.disconnect() }.buttonStyle(SecondaryButton())
        }
    }
}
