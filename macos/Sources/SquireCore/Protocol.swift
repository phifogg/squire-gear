// Protocol facts (byte positions relative to the echoed command, i.e. without report ID).
// Confirmed on hardware, see ../../../../protocol.json and CLAUDE.md. Source of the original
// facts: G-Helper (GPL-3.0), protocol facts only, no code copied.
//
//   mouse  12 07 -> [4] battery %, [6] low-battery warning %, [9] charging
//   error reply  ff aa -> device asleep / command not supported
//
// Read-only: the only command Squire ever sends to the mouse is the 12 07 query.

public enum SquireProtocol {
    public static let vendorID: UInt16 = 0x0B05
    public static let omniReceiverPID: UInt16 = 0x1ACE
    public static let kerisIIOriginPID: UInt16 = 0x1C0C   // wired
    /// Only vendor-defined HID collections (usage page >= 0xFF00) may be opened.
    public static let minVendorUsagePage = 0xFF00
    /// Used when a device does not report a plausible warning level.
    public static let defaultLowWarning = 20
    /// IOKit/hidapi: a timeout of 0 means "block forever". Always read with a timeout > 0.
    public static let readTimeoutMs = 400
}

/// One way of querying one device.
public struct Target: Equatable, Sendable {
    public let device: Battery.Device
    public let name: String
    public let connection: Battery.Connection
    public let productID: UInt16
    /// nil = unnumbered report (leading 0x00 in the write, no report ID in the reply).
    public let reportID: UInt8?
    public let query: [UInt8]
    /// Candidate total write sizes (including the report-ID byte), tried in order.
    public let reportLengths: [Int]

    public static let omniMouse = Target(
        device: .mouse, name: "ROG Keris II Origin", connection: .omni,
        productID: SquireProtocol.omniReceiverPID, reportID: 0x03,
        query: [0x12, 0x07], reportLengths: [64])

    /// Wired report size 65 confirmed 2026-11-10; 64 kept as a harmless fallback.
    public static let wiredMouse = Target(
        device: .mouse, name: "ROG Keris II Origin", connection: .wired,
        productID: SquireProtocol.kerisIIOriginPID, reportID: nil,
        query: [0x12, 0x07], reportLengths: [65, 64])

    public static let mouseTargets = [omniMouse, wiredMouse]

    /// Report ID byte followed by the query, zero-padded to `length`.
    public func packet(length: Int) -> [UInt8] {
        var bytes: [UInt8] = [reportID ?? 0x00] + query
        if bytes.count < length { bytes += [UInt8](repeating: 0, count: length - bytes.count) }
        return bytes
    }
}

public enum ReplyParser {
    /// Index where the echoed command starts (0 or 1, depending on the report-ID byte).
    public static func echoIndex(in reply: [UInt8], query: [UInt8]) -> Int? {
        for base in 0...1 where reply.count >= base + query.count
            && Array(reply[base..<base + query.count]) == query {
            return base
        }
        return nil
    }

    public static func isError(_ reply: [UInt8], reportID: UInt8?) -> Bool {
        for base in 0...1 where reply.count >= base + 2 && reply[base] == 0xFF && reply[base + 1] == 0xAA {
            if base == 1, let reportID, reply[0] != reportID { continue }
            return true
        }
        return false
    }

    /// Mouse reply: percent, charging, warning level. `base` is the echo index.
    public static func parseMouse(_ reply: [UInt8], base: Int) -> (percent: Int?, charging: Bool, lowWarning: Int?) {
        let p = Array(reply[base...])
        let percent = p.count > 4 && p[4] <= 100 ? Int(p[4]) : nil
        let charging = p.count > 9 && p[9] != 0
        let warning = p.count > 6 && (1...100).contains(p[6]) ? Int(p[6]) : nil
        return (percent, charging, warning)
    }

    /// Turns the replies read after one query into a `Battery`. Pure: the IO layer feeds the replies in.
    public static func interpret(replies: [[UInt8]], target: Target) -> Battery {
        var result = Battery(device: target.device, name: target.name, connection: target.connection)
        for reply in replies {
            if reply.isEmpty { break }   // read timeout
            if isError(reply, reportID: target.reportID) {
                result.status = .asleep
                result.raw = hex(reply)
                return result
            }
            guard let base = echoIndex(in: reply, query: target.query) else { continue }  // other traffic
            result.raw = hex(reply)
            let parsed = parseMouse(reply, base: base)
            guard let percent = parsed.percent, !(percent == 0 && !parsed.charging) else {
                result.status = .asleep   // 0 % and not charging = treat as disconnected
                return result
            }
            result.percent = percent
            result.charging = parsed.charging
            result.lowWarning = parsed.lowWarning
            return result
        }
        result.status = .noResponse
        return result
    }

    public static func hex(_ bytes: [UInt8]) -> String {
        bytes.map { byte in
            let digits = String(byte, radix: 16)
            return digits.count == 1 ? "0" + digits : digits
        }.joined(separator: " ")
    }
}
