import Foundation
import SquireCore
import SquireHID

// Read-only diagnostics: prints what IOKit sees and the parsed batteries.
// Sends only read commands (12 ..) to vendor-defined collections; never writes.
// --settings: read the mouse settings (DPI, LEDs, sensor, power), like `squire.py --settings`.
// --repeat N: read N times in one process (what the app does every 60 s). Used to catch lifetime bugs
// in the IOKit callbacks that a single read never triggers.
if let index = CommandLine.arguments.firstIndex(of: "--repeat"),
   index + 1 < CommandLine.arguments.count, let count = Int(CommandLine.arguments[index + 1]) {
    for i in 1...max(1, count) {
        // Same threading as the app: every read on a GCD worker thread, main run loop idle in between.
        var r: HIDReader.Result!
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.global(qos: .utility).async { r = HIDReader().read(); done.signal() }
        while done.wait(timeout: .now() + 0.01) == .timedOut { RunLoop.current.run(until: Date().addingTimeInterval(0.01)) }
        print("\(i)/\(count): " + r.batteries.map { "\($0.device.rawValue) \($0.percent.map { "\($0)%" } ?? "– (\($0.status.rawValue))")" }.joined(separator: ", "))
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))   // let late callbacks arrive
    }
    exit(0)
}

if CommandLine.arguments.contains("--settings") {
    let r = HIDReader().readMouseSettings()
    if CommandLine.arguments.contains("--debug") { r.diagnostics.forEach { print($0) }; print("---") }
    let s = r.settings
    guard s.status == .ok else { print("\(s.name): \(s.status.rawValue)"); exit(0) }
    print("\(s.name) (\(s.connection?.rawValue ?? "?")), firmware \(s.firmware ?? "?")\(s.receiverFirmware.map { ", receiver \($0)" } ?? ""), profile \(s.profile.map(String.init) ?? "?")")
    print("  DPI stages:   " + s.dpi.enumerated().map { (s.dpiStage == $0.offset + 1 ? "*" : "") + ($0.element.map(String.init) ?? "?") }
        .joined(separator: ", ") + "   (* = active)")
    print("  DPI colors:   " + s.dpiColors.map(\.hex).joined(separator: ", "))
    print("  DPI button:   cycles through \(s.dpiStageCount.map(String.init) ?? "?") stages")
    print("  Polling rate: \(s.pollingHz ?? 0) Hz (raw 0x\(ReplyParser.hex([s.pollingRaw ?? 0])))   debounce "
          + (s.debounceMs.map { "\($0) ms" } ?? "index \(s.debounceIndex ?? -1)"))
    print("  Angle:        snapping \(s.angleSnapping == true ? "on" : "off"), tuning \(s.angleTuning ?? 0)°")
    print("  Lift-off:     \(s.liftoff ?? "?")")
    for zone in LEDZone.allCases {
        if let led = s.leds[zone] {
            print("  LED \(zone.rawValue.padding(toLength: 6, withPad: " ", startingAt: 0))    \(led.mode.name), \(led.brightness) %, \(led.color.hex)")
        }
    }
    print("  Power:        off after \(s.powerOffMinutes.map { "\($0) min" } ?? "never"), low-battery warning \(s.lowWarning.map(String.init) ?? "?") %")
    exit(0)
}

let result = HIDReader().read()
for line in result.diagnostics { print(line) }
print("---")
if result.batteries.isEmpty { print("No ASUS devices found.") }
for b in result.batteries {
    let level = b.percent.map { "\($0)%" } ?? "–"
    print("\(b.name): \(level)\(b.charging ? " charging" : "") (\(b.connection.rawValue), \(b.status.rawValue), warning \(b.lowWarning.map(String.init) ?? "?")%)")
}
