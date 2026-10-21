import AppKit
import SwiftUI
import CoreText

enum Brand {
    static let background = Color(red: 15 / 255, green: 21 / 255, blue: 37 / 255)
    static let panel = Color(red: 20 / 255, green: 29 / 255, blue: 48 / 255)
    static let border = Color(red: 38 / 255, green: 49 / 255, blue: 70 / 255)
    static let heading = Color(red: 237 / 255, green: 239 / 255, blue: 243 / 255)
    static let body = Color(red: 200 / 255, green: 207 / 255, blue: 217 / 255)
    static let muted = Color(red: 161 / 255, green: 169 / 255, blue: 181 / 255)
    static let lime = Color(red: 166 / 255, green: 230 / 255, blue: 61 / 255)
    static let blue = Color(red: 92 / 255, green: 135 / 255, blue: 223 / 255)

    enum Space {
        static let item: CGFloat = 12
        static let section: CGFloat = 20
        static let page: CGFloat = 28
    }
    enum Radius {
        static let checkbox: CGFloat = 5
        static let control: CGFloat = 8
        static let card: CGFloat = 10
        static let panel: CGFloat = 12
    }
    enum Control {
        static let checkboxSize: CGFloat = 22
        static let compactHeight: CGFloat = 32
        static let disabledOpacity: Double = 0.55
        static let focusWidth: CGFloat = 3
        static let focusGap: CGFloat = 2
    }
    enum Depth {
        static let wellShadow = Color.black.opacity(0.35)
        static let cardShadow = Color.black.opacity(0.22)
        static let cardHighlight = Color.white.opacity(0.05)
        static let selectionHalo = Brand.lime.opacity(0.35)
    }
    enum Layout {
        static let workspaceMaximum: CGFloat = 1440
        static let contactMinimum: CGFloat = 220
    }
    // Google's card gradient mixes 10% heading into the panel at its raised edge.
    static let raisedSurface = LinearGradient(colors: [
        Color(red: 41.7 / 255, green: 50 / 255, blue: 67.5 / 255), panel
    ], startPoint: .top, endPoint: .bottom)

    private static let bodyFontName: String = {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("Fonts/OpenSans.ttf") else { return "Arial" }
        CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        return NSFont(name: "OpenSans-Regular", size: 16) == nil ? "Arial" : "OpenSans-Regular"
    }()

    static func bodyFont(_ size: CGFloat) -> Font { .custom(bodyFontName, size: size) }
    static func headingFont(_ size: CGFloat) -> Font { .custom("Arial", size: size).weight(.bold) }

    static func logo() -> NSImage? {
        guard let url = Bundle.main.resourceURL?.appendingPathComponent("amplifai-logo.png") else {
            return nil
        }
        return NSImage(contentsOf: url)
    }
}
