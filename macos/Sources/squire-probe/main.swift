import Foundation
import SquireCore
import SquireHID

// Read-only spike: prints what IOKit sees and the parsed batteries.
// Sends only the 12 01 / 12 07 queries to vendor-defined collections.
let result = HIDReader().read()
for line in result.diagnostics { print(line) }
print("---")
if result.batteries.isEmpty { print("No ASUS devices found.") }
for b in result.batteries {
    let level = b.percent.map { "\($0)%" } ?? "–"
    print("\(b.name): \(level)\(b.charging ? " charging" : "") (\(b.connection.rawValue), \(b.status.rawValue), warning \(b.lowWarning.map(String.init) ?? "?")%)")
}
