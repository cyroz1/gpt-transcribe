// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "GPTTranscribeMac",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "GPTTranscribeMac", targets: ["GPTTranscribeMac"]),
    ],
    targets: [
        .executableTarget(
            name: "GPTTranscribeMac",
            path: "Sources/GPTTranscribeMac"
        ),
        .testTarget(
            name: "GPTTranscribeMacTests",
            dependencies: ["GPTTranscribeMac"],
            path: "Tests/GPTTranscribeMacTests"
        ),
    ]
)
