import AppKit
import Foundation

// Packaging-only deterministic reuse of the accepted logo; no new artwork or letterforms.
@main
struct PrepareAppIcon {
    enum Failure: Error { case invalidInput, existingOutput, rasterization, iconutil }
    static func main() throws {
        guard CommandLine.arguments.count == 3,
              let source = NSImage(contentsOfFile: CommandLine.arguments[1]),
              source.size.width > 0, source.size.height > 0 else { throw Failure.invalidInput }
        let output = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
        var directory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: output.path, isDirectory: &directory), directory.boolValue else {
            throw Failure.invalidInput
        }
        let iconset = output.appendingPathComponent("AmplifaiPhone.iconset", isDirectory: true)
        let icon = output.appendingPathComponent("AmplifaiPhone.icns")
        guard !FileManager.default.fileExists(atPath: iconset.path), !FileManager.default.fileExists(atPath: icon.path) else {
            throw Failure.existingOutput
        }
        try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: false)
        for size in [16, 32, 128, 256, 512] {
            for scale in [1, 2] {
                let pixels = size * scale
                guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
                    let context = NSGraphicsContext(bitmapImageRep: bitmap) else { throw Failure.rasterization }
                NSGraphicsContext.saveGraphicsState()
                NSGraphicsContext.current = context
                NSColor(calibratedRed: 15 / 255, green: 21 / 255, blue: 37 / 255, alpha: 1).setFill()
                NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: pixels, height: pixels), xRadius: CGFloat(pixels) / 8, yRadius: CGFloat(pixels) / 8).fill()
                let width = CGFloat(pixels) * 0.88
                let height = min(CGFloat(pixels) * 0.72, width * source.size.height / source.size.width)
                source.draw(in: NSRect(x: (CGFloat(pixels) - width) / 2, y: (CGFloat(pixels) - height) / 2, width: width, height: height),
                    from: .zero, operation: .sourceOver, fraction: 1)
                NSGraphicsContext.restoreGraphicsState()
                guard let png = bitmap.representation(using: .png, properties: [:]) else { throw Failure.rasterization }
                let name = "icon_\(size)x\(size)\(scale == 2 ? "@2x" : "").png"
                try png.write(to: iconset.appendingPathComponent(name), options: .withoutOverwriting)
            }
        }
        let tool = Process()
        tool.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
        tool.arguments = ["-c", "icns", iconset.path, "-o", icon.path]
        try tool.run()
        tool.waitUntilExit()
        guard tool.terminationStatus == 0 else { throw Failure.iconutil }
        print(icon.path)
    }
}
