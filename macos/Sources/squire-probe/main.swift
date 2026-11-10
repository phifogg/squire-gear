import Foundation
import SquireCore
import SquireHID

// Read-only spike: prints what IOKit sees for the mouse and the parsed battery.
// Sends only the 12 07 query to vendor-defined collections.
let result = HIDMouseReader().read()
for line in result.diagnostics { print(line) }
print("---")
if result.batteries.isEmpty { print("No mouse found.") }
for b in result.batteries {
    let level = b.percent.map { "\($0)%" } ?? "–"
    print("\(b.name): \(level)\(b.charging ? " charging" : "") (\(b.connection.rawValue), \(b.status.rawValue), warning \(b.lowWarning.map(String.init) ?? "?")%)")
}
