import SwiftUI

/// Instructions, not a device-status meter. Only the model's read result can confirm readiness.
struct PhoneConnectionGuide: View {
    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            HStack(spacing: 12) {
                Image(systemName: "iphone.gen3").font(.system(size: 38))
                Image(systemName: "cable.connector.horizontal")
                    .font(.system(size: 36)).frame(maxWidth: .infinity)
                Image(systemName: "laptopcomputer").font(.system(size: 44))
            }
            .foregroundStyle(Brand.blue)
            .padding(.horizontal, 24)
            .accessibilityHidden(true)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: Brand.Space.section) { instructions }
                VStack(alignment: .leading, spacing: Brand.Space.item) { instructions }
            }
        }
        .brandControls()
    }

    @ViewBuilder private var instructions: some View {
        instruction(1, "Connect", "Use a data-capable USB cable.")
        instruction(2, "Unlock", "Keep your iPhone unlocked to connect.")
        instruction(3, "Trust", "If asked, tap Trust on your iPhone.")
    }

    private func instruction(_ number: Int, _ title: String, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(number). \(title)").font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
            Text(detail).font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

struct FinderEncryptionGuide: View {
    @ObservedObject var model: CollectorModel
    let onClose: () -> Void
    @State private var step: Int
    @AccessibilityFocusState private var headingFocused: Bool

    init(model: CollectorModel, initialStep: Int = 0, onClose: @escaping () -> Void) {
        self.model = model
        self.onClose = onClose
        _step = State(initialValue: min(3, max(0, initialStep)))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Text("Set up encrypted backup")
                .font(Brand.headingFont(28)).foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader)
            Text("Finder guide · \(step + 1) of 4")
                .font(Brand.headingFont(14)).foregroundStyle(Brand.lime)
            illustration
            Text(steps[step].title)
                .font(Brand.headingFont(22)).foregroundStyle(Brand.heading)
                .accessibilityAddTraits(.isHeader).accessibilityFocused($headingFocused)
            Text(steps[step].detail)
                .font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            if step == 2 {
                Text("This starts a Finder backup and overwrites and encrypts earlier local backups. Keep the password: AMPLIFai cannot recover it. Finder needs its own free storage.")
                    .font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if step == 3 { recheck }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Brand.Space.item) { navigation }
                VStack(alignment: .leading, spacing: Brand.Space.item) { navigation }
            }
            DisclosureGroup("iPhone missing in Finder?") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("1. In Finder, choose View → Show Sidebar. Expand Locations.")
                    Text("2. Choose Finder → Settings → Sidebar. Under Locations, enable CDs, DVDs, and iOS Devices.")
                    Text("3. Reconnect with a data-capable USB cable, unlock your iPhone and answer any Allow or Trust prompt.")
                    Link("Apple's device connection help", destination: URL(string: "https://support.apple.com/108643")!)
                        .buttonStyle(QuietAction())
                }
                .font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                .fixedSize(horizontal: false, vertical: true).padding(.top, 8)
            }
            .font(Brand.bodyFont(14))
            Link("Apple's encrypted backup guide", destination: URL(string: "https://support.apple.com/108353")!)
                .buttonStyle(QuietAction())
        }
        .onChange(of: step) { _, _ in headingFocused = true }
    }

    @ViewBuilder private var navigation: some View {
        if step > 0 {
            Button("Back") { step -= 1 }.buttonStyle(SecondaryButton())
        }
        if step < 3 {
            Button("Next") { step += 1 }.buttonStyle(LimeButton())
        }
        if step != 3 || model.backupEncryptionStatus != .encrypted {
            Button("Back to collection", action: onClose).buttonStyle(QuietAction())
        }
    }

    @ViewBuilder private var recheck: some View {
        switch model.backupEncryptionStatus {
        case .checking:
            Label("Checking the iPhone's setting…", systemImage: "clock")
                .font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
            Text("No collection has started.").font(Brand.bodyFont(14)).foregroundStyle(Brand.muted)
            Button("Cancel check") { model.cancelBackupEncryptionStatusCheck() }
                .buttonStyle(SecondaryButton()).disabled(!model.backupSettingCheckInProgress)
        case .encrypted:
            VerifiedStatusRow(verified: true, title: "Encrypted backup is on",
                              detail: "Call history may be available. Full history is not guaranteed.")
            Button("Back to collection", action: onClose).buttonStyle(LimeButton())
        default:
            if model.backupEncryptionStatus == .unencrypted {
                Text("Last check: encryption off").font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
            } else if model.backupEncryptionStatus == .unknown {
                Text("The setting was not confirmed. Check the cable, unlock and Trust, then try again.")
                    .font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
            }
            Button("Recheck encryption") { model.checkBackupEncryptionStatus() }
                .buttonStyle(LimeButton()).disabled(!model.canPrepareCollection)
        }
    }

    private var steps: [(title: String, detail: String)] {
        [
            ("Connect, unlock and trust", "Connect your iPhone to this Mac. Unlock it and tap Trust if asked, then open Finder."),
            ("Select your iPhone", "Choose your iPhone under Locations in Finder's sidebar. The AMPLIFai Phone installer disk is a different item."),
            ("Choose Encrypt local backup", "On the General tab, find Backups and select Encrypt local backup. Create a password you can keep; this changes the setting in Finder."),
            ("Let Finder finish, then recheck", "Return here after the Finder backup finishes. Recheck reads the setting without collecting. Even encrypted backups may not contain every call.")
        ]
    }

    private var illustration: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Text("ILLUSTRATION · USE FINDER TO CHANGE THE SETTING")
                .font(Brand.headingFont(11)).foregroundStyle(Brand.muted)
            switch step {
            case 0:
                PhoneConnectionGuide()
            case 1:
                HStack(alignment: .top, spacing: Brand.Space.section) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Locations").font(Brand.headingFont(14)).foregroundStyle(Brand.muted)
                        Label("Your iPhone", systemImage: "iphone.gen3")
                            .font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
                            .padding(10).background(Brand.blue.opacity(0.2), in: RoundedRectangle(cornerRadius: Brand.Radius.control))
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "iphone.gen3").font(.system(size: 54)).foregroundStyle(Brand.blue)
                }
            case 2:
                VStack(alignment: .leading, spacing: 12) {
                    Text("General").font(Brand.headingFont(15)).foregroundStyle(Brand.heading)
                    Rectangle().fill(Brand.border).frame(height: 1)
                    Text("Backups").font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
                    Label("Encrypt local backup", systemImage: "square")
                        .font(Brand.bodyFont(16)).foregroundStyle(Brand.body)
                }
            default:
                HStack(spacing: Brand.Space.section) {
                    Image(systemName: "lock.iphone").font(.system(size: 48)).foregroundStyle(Brand.blue)
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Wait for Finder to finish", systemImage: "clock")
                        Label("Recheck in AMPLIFai Phone", systemImage: "arrow.clockwise")
                    }
                    .font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 124, alignment: .leading)
        .background(Brand.background, in: RoundedRectangle(cornerRadius: Brand.Radius.control))
        .overlay(RoundedRectangle(cornerRadius: Brand.Radius.control).stroke(Brand.border, lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Finder illustration: \(steps[step].title). Follow the instructions below.")
    }
}
