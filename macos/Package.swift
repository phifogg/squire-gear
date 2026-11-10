// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Squire",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "SquireCore", targets: ["SquireCore"]),
        .library(name: "SquireHID", targets: ["SquireHID"]),
    ],
    targets: [
        // UI-free protocol logic: packets, reply parsing, low-battery rules. No IOKit, no AppKit.
        .target(name: "SquireCore"),
        // IOKit transport: finds the vendor-defined HID collections and sends the read-only query.
        .target(name: "SquireHID", dependencies: ["SquireCore"],
                swiftSettings: [.swiftLanguageMode(.v5)]),   // C callbacks + run loop, no concurrency model
        // Spike / debugging tool: prints what IOKit sees and the parsed battery.
        .executableTarget(name: "squire-probe", dependencies: ["SquireCore", "SquireHID"],
                          swiftSettings: [.swiftLanguageMode(.v5)]),
        .testTarget(name: "SquireCoreTests", dependencies: ["SquireCore"]),
    ]
)
