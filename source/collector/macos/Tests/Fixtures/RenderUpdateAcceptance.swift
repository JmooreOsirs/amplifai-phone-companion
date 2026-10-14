import AppKit
import Foundation
import Sparkle
import SwiftUI

@main
@MainActor
struct RenderUpdateAcceptance {
    static func main() throws {
        guard CommandLine.arguments.count == 2 else { fatalError("Supply the fixture output directory") }
        let output = URL(fileURLWithPath: CommandLine.arguments[1])
        NSApplication.shared.setActivationPolicy(.prohibited)
        let versions: [(String, CompanionUpdater.State)] = [
            ("manual", .manualOnly), ("current", .current), ("available", .available("2026.10.14-rc26")),
            ("deferred", .deferred("2026.10.14-rc26")), ("instance-conflict", .manualOnly)
        ]
        for (name, target) in versions {
            let model = CollectorModel(commandWriter: { _ in preconditionFailure("Visual fixture cannot start collection") })
            let updater = CompanionUpdater(bundleInformation: [
                "CFBundleIdentifier": "ai.satoris.amplifai.phone.candidate",
                "CFBundleShortVersionString": "2026.10.14-rc25", "CFBundleVersion": "26101425",
                "SUFeedURL": CompanionUpdater.armFeed, "SUPublicEDKey": CompanionUpdater.publicKey,
                "SURequireSignedFeed": true, "SUVerifyUpdateBeforeExtraction": true
            ])
            updater.bind(model: model)
            let controller = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: updater, userDriverDelegate: nil)
            switch target {
            case .current:
                updater.updaterDidNotFindUpdate(controller.updater, error: NSError(domain: SUSparkleErrorDomain, code: 1001,
                    userInfo: [SPUNoUpdateFoundReasonKey: NSNumber(value: 1)]))
            case .available: updater.updater(controller.updater, didFindValidUpdate: AmplifaiVisualUpdateItem())
            case .deferred:
                model.phase = .processing
                updater.postponeInstallIfNeeded(version: "2026.10.14-rc26") {
                    preconditionFailure("Visual fixture cannot install")
                }
            default: break
            }
            if name == "instance-conflict" { model.setCompanionStatus(.anotherAppRunning) }
            precondition(updater.state == target)
            for width in [375, 820] {
                let height = width == 375 ? 1500 : 1000
                try render(ContentView(model: model, updater: updater), width: width, height: height,
                           path: output.appendingPathComponent("current-\(name)-\(width).png"))
                try render(BaselineContentView(model: model, updater: updater), width: width, height: height,
                           path: output.appendingPathComponent("rc24-\(name)-\(width).png"))
            }
        }
        let receipt: [String: Any] = ["renderer": "offscreen NSHostingView bitmap", "states": versions.map(\.0),
            "widths": [375, 820], "reference": "cdfc1b9 ContentView with unchanged shared native primitives",
            "screenCaptured": false, "windowCreated": false, "appActivated": false,
            "collectorStarted": false, "ownerWorkspaceRead": false, "updateControllerStarted": false,
            "manualCurrentPanel": "existing collapsed state", "busyDeferredPanel": "existing hidden state"]
        try JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted, .sortedKeys])
            .write(to: output.appendingPathComponent("visual-receipt.json"), options: .atomic)
        print("Offscreen native update/status renders completed without a window or collection")
    }

    private static func render<V: View>(_ view: V, width: Int, height: Int, path: URL) throws {
        let hosting = NSHostingView(rootView: view.environment(\.colorScheme, .dark).frame(width: CGFloat(width), height: CGFloat(height)))
        hosting.frame = NSRect(x: 0, y: 0, width: width, height: height)
        hosting.layoutSubtreeIfNeeded()
        guard let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else {
            throw NSError(domain: "AMPLIFaiVisualFixture", code: 1)
        }
        hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
        guard let data = bitmap.representation(using: .png, properties: [:]) else {
            throw NSError(domain: "AMPLIFaiVisualFixture", code: 2)
        }
        try data.write(to: path, options: .atomic)
    }
}
