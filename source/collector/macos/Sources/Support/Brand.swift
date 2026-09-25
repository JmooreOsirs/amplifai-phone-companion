import AppKit
import SwiftUI

enum Brand {
    static let background = Color(red: 15 / 255, green: 21 / 255, blue: 37 / 255)
    static let panel = Color(red: 20 / 255, green: 29 / 255, blue: 48 / 255)
    static let border = Color(red: 38 / 255, green: 49 / 255, blue: 70 / 255)
    static let heading = Color(red: 237 / 255, green: 239 / 255, blue: 243 / 255)
    static let body = Color(red: 200 / 255, green: 207 / 255, blue: 217 / 255)
    static let muted = Color(red: 161 / 255, green: 169 / 255, blue: 181 / 255)
    static let lime = Color(red: 166 / 255, green: 230 / 255, blue: 61 / 255)
    static let blue = Color(red: 92 / 255, green: 135 / 255, blue: 223 / 255)

    static func logo() -> NSImage? {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("amplifai-logo.png") else {
            return nil
        }
        return NSImage(contentsOf: url)
    }
}
