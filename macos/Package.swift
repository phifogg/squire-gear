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
        // devices.json is a copy of the repo root file (SwiftPM cannot reach ../devices.json); the tests (Swift and
        // Python) fail when the two differ.
        .target(name: "SquireCore", resources: [.copy("Resources/devices.json")]),
        // IOKit transport: finds the vendor-defined HID collections and sends the read-only query.
        .target(name: "SquireHID", dependencies: ["SquireCore"],
                swiftSettings: [.swiftLanguageMode(.v5)]),   // C callbacks + run loop, no concurrency model
        // Spike / debugging tool: prints what IOKit sees and the parsed battery.
        // Info.plist embedded: CoreBluetooth (--bluetooth) terminates a binary without NSBluetoothAlwaysUsageDescription.
        .executableTarget(name: "squire-probe", dependencies: ["SquireCore", "SquireHID"],
                          exclude: ["Info.plist"],
                          swiftSettings: [.swiftLanguageMode(.v5)],
                          linkerSettings: [.unsafeFlags(["-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
                                                         "-Xlinker", "__info_plist",
                                                         "-Xlinker", "Sources/squire-probe/Info.plist"])]),
        .testTarget(name: "SquireCoreTests", dependencies: ["SquireCore"]),
    ]
)
