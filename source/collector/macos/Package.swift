// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "AmplifaiPhone",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "AmplifaiPhone", targets: ["AmplifaiPhone"])],
    dependencies: [.package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0")],
    targets: [
        .executableTarget(
            name: "AmplifaiPhone",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources",
            linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        )
    ]
)
