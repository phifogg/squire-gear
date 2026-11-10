import Foundation
import SquireCore
import SquireHID

// Read-only spike: prints what IOKit sees and the parsed batteries.
// Sends only the 12 01 / 12 07 queries to vendor-defined collections.
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

let result = HIDReader().read()
for line in result.diagnostics { print(line) }
print("---")
if result.batteries.isEmpty { print("No ASUS devices found.") }
for b in result.batteries {
    let level = b.percent.map { "\($0)%" } ?? "–"
    print("\(b.name): \(level)\(b.charging ? " charging" : "") (\(b.connection.rawValue), \(b.status.rawValue), warning \(b.lowWarning.map(String.init) ?? "?")%)")
}
