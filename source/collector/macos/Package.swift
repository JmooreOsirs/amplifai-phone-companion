// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "AmplifaiPhone",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "AmplifaiPhone", targets: ["AmplifaiPhone"])],
    targets: [
        .executableTarget(
            name: "AmplifaiPhone",
            path: "Sources"
        )
    ]
)
