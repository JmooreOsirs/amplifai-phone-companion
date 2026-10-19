import AppKit
import SwiftUI

/// Brings the default browser forward without opening a new tab, so the owner returns to the account tab
/// that already holds the reviewed preview. A host can replace it (the fake-data demo shows its fixture).
struct ReturnToBrowserAction {
    let perform: @MainActor () -> Void

    @MainActor func callAsFunction() { perform() }

    static let system = ReturnToBrowserAction {
        guard let probe = URL(string: "https://amplifai.satoris.ai"),
              let browser = NSWorkspace.shared.urlForApplication(toOpen: probe) else { return }
        NSWorkspace.shared.openApplication(at: browser, configuration: NSWorkspace.OpenConfiguration())
    }
}

private struct ReturnToBrowserKey: EnvironmentKey {
    static let defaultValue = ReturnToBrowserAction.system
}

extension EnvironmentValues {
    var returnToBrowser: ReturnToBrowserAction {
        get { self[ReturnToBrowserKey.self] }
        set { self[ReturnToBrowserKey.self] = newValue }
    }
}
