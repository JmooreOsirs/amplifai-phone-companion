import AppKit
import SwiftUI

/// Instructions, not a device-status meter. Only the model's read result can confirm readiness.
struct PhoneConnectionGuide: View {
    var checkInProgress = false
    var settingConfirmed = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            PhoneConnectionIllustration(reduceMotion: reduceMotion)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: Brand.Space.section, alignment: .topLeading)],
                      alignment: .leading, spacing: Brand.Space.item) {
                instructions
            }
        }
        .brandControls()
    }

    @ViewBuilder private var instructions: some View {
        instruction(1, "Connect", "Use a data-capable USB cable.")
        instruction(2, "Unlock", "Keep your iPhone unlocked to connect.")
        if checkInProgress {
            instruction(3, "Trust if asked", "Tap Trust and enter your iPhone passcode only if prompted. An already-trusted iPhone may not ask.")
        } else if settingConfirmed {
            instruction(3, "Continue below", "Your backup setting is checked. Keep the iPhone connected.")
        } else {
            instruction(3, "Check iPhone below", "Start the check here. Answer any Trust prompt on your iPhone when it appears.")
        }
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

struct BackupPasswordHelp: View {
    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            HStack(alignment: .top, spacing: Brand.Space.item) {
                Image(systemName: "externaldrive.badge.person.crop")
                    .font(.system(size: 30)).foregroundStyle(Brand.blue).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Encrypted backup? Keep its password ready.")
                        .font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
                    Text("Use the password chosen for encrypted computer backups. Your iPhone passcode unlocks the phone instead.")
                        .font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                }
            }
            DisclosureGroup("About this password") {
                VStack(alignment: .leading, spacing: Brand.Space.item) {
                    Label("Finder → your iPhone → General → Encrypt local backup", systemImage: "lock.rectangle")
                        .font(Brand.bodyFont(14).weight(.semibold)).foregroundStyle(Brand.heading)
                    Text("That checkbox shows whether encryption is on; Finder does not reveal the password. AMPLIFai cannot recover or reset it.")
                    Text("If you forgot it, read Apple's guidance before changing device settings. A new password cannot unlock an older encrypted backup.")
                    Link("Apple's forgotten backup password guidance", destination: URL(string: "https://support.apple.com/108313")!)
                        .buttonStyle(QuietAction())
                }
                .font(Brand.bodyFont(14)).foregroundStyle(Brand.body).padding(.top, 8)
            }
            .font(Brand.bodyFont(14))
        }
        .fixedSize(horizontal: false, vertical: true)
        .brandWell()
    }
}

/// A bounded instructional animation, separate from measured connection status.
struct PhoneConnectionIllustration: View {
    let reduceMotion: Bool
    @State private var demonstrationStep = 3

    var body: some View {
        HStack(spacing: 12) {
            HStack(spacing: 3) {
                Image(systemName: "laptopcomputer").font(.system(size: 28))
                Image(systemName: "cable.connector.horizontal").font(.system(size: 20))
                    .offset(y: demonstrationStep == 0 ? 12 : 0)
                    .opacity(demonstrationStep == 0 ? 0.45 : 1)
                Image(systemName: "iphone.gen3").font(.system(size: 28))
            }
            .frame(maxWidth: .infinity)
            HStack(spacing: 4) {
                Image(systemName: "iphone.gen3").font(.system(size: 36))
                ZStack {
                    Image(systemName: "lock").opacity(demonstrationStep < 2 ? 1 : 0)
                    Image(systemName: "lock.open").opacity(demonstrationStep >= 2 ? 1 : 0)
                }
                .font(.system(size: 25))
            }
            .frame(maxWidth: .infinity)
            ZStack(alignment: .bottomTrailing) {
                Image(systemName: "checkmark.shield").font(.system(size: 40))
                Image(systemName: "hand.tap").font(.system(size: 23))
                    .foregroundStyle(Brand.lime)
                    .offset(x: demonstrationStep < 3 ? 15 : 5, y: demonstrationStep < 3 ? 15 : 5)
                    .opacity(demonstrationStep < 3 ? 0.3 : 1)
            }
            .frame(maxWidth: .infinity)
        }
        .foregroundStyle(Brand.blue)
        .frame(height: 60)
        .accessibilityHidden(true)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.55), value: demonstrationStep)
        .task(id: reduceMotion) {
            // One short instructional sequence, then a static frame; never a readiness signal.
            demonstrationStep = reduceMotion ? 3 : 0
            guard !reduceMotion else { return }
            do {
                for step in 1...3 {
                    try await Task.sleep(for: .milliseconds(1100))
                    try Task.checkCancellation()
                    demonstrationStep = step
                }
            } catch { /* Leaving the guide cancels its illustration. */ }
        }
    }
}

struct FinderEncryptionGuide: View {
    @ObservedObject var model: CollectorModel
    let onClose: () -> Void
    @State private var step: Int
    @State private var finderOpenFailed = false
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
            if step < 2 {
                Button("Open Finder") {
                    finderOpenFailed = !NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: NSHomeDirectory())
                }
                .buttonStyle(SecondaryButton())
                if finderOpenFailed {
                    Text("Open Finder from the Dock, then choose your iPhone under Locations.")
                        .font(Brand.bodyFont(14)).foregroundStyle(Brand.body)
                }
            }
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
            Button(step == 0 ? "Next: select your iPhone" : step == 1 ? "Next: backup setting" : "Next: finish and recheck") { step += 1 }
                .buttonStyle(LimeButton())
        }
        if step != 3 || model.backupEncryptionStatus != .encrypted {
            Button("Back to preparation", action: onClose).buttonStyle(QuietAction())
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
                              detail: "The setting is confirmed. Check that Finder has finished before collecting. Call history may be available; full history is not guaranteed.")
            Button("Back to preparation", action: onClose).buttonStyle(LimeButton())
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
            ("Connect, unlock and open Finder", "Connect your iPhone to this Mac with a data-capable USB cable and unlock it. Open Finder below, then choose Next. You do not need to wait for a Trust prompt first."),
            ("Select your iPhone and allow access", "Choose your iPhone under Locations in Finder's sidebar. If Finder asks, click Trust. On your iPhone, tap Trust and enter its passcode if prompted. The AMPLIFai Phone installer disk is a different item."),
            ("Choose Encrypt local backup", "On the General tab, find Backups and select Encrypt local backup. Create a password you can keep; this changes the setting in Finder."),
            ("Let Finder finish, then recheck", "Wait for the backup progress in Finder to finish and its latest-backup date/time to update. Return here and recheck. This reads the encryption setting; it cannot confirm Finder's backup has finished.")
        ]
    }

    private var illustration: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Text("EXAMPLE ONLY · FOLLOW THE STEPS BELOW IN FINDER")
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
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "iphone.gen3").font(.system(size: 54)).foregroundStyle(Brand.blue)
                }
            case 2:
                VStack(alignment: .leading, spacing: 12) {
                    Text("General").font(Brand.headingFont(15)).foregroundStyle(Brand.heading)
                    Rectangle().fill(Brand.border).frame(height: 1)
                    Text("Backups").font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
                    Label("Find “Encrypt local backup”", systemImage: "lock")
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
