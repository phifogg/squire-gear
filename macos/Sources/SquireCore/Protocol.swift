// Protocol facts (byte positions relative to the echoed command, i.e. without report ID).
// Confirmed on hardware, see ../../../../protocol.json and CLAUDE.md. Source of the original
// facts: G-Helper (GPL-3.0), protocol facts only, no code copied.
//
//   mouse     12 07 -> [4] battery %, [6] low-battery warning %, [9] charging
//   keyboard  12 01 -> [10] battery % (copy at [5]), [9] low-battery warning %, [8] charging
//   error reply  ff aa -> device asleep / command not supported
//
// Only commands on the allowlist in Commands.swift are ever sent: the battery queries above, the mouse settings
// reads, and the two approved writes (51 28 set LED, 50 03 save), the writes only after an explicit Apply.

public enum SquireProtocol {
    public static let vendorID: UInt16 = 0x0B05
    public static let omniReceiverPID: UInt16 = 0x1ACE
    public static let falchionRXLowProfilePID: UInt16 = 0x1B04   // keyboard, wired
    public static let kerisIIOriginPID: UInt16 = 0x1C0C          // mouse, wired
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

    public static let omniKeyboard = Target(
        device: .keyboard, name: "ROG Falchion RX LP", connection: .omni,
        productID: SquireProtocol.omniReceiverPID, reportID: 0x02,
        query: [0x12, 0x01], reportLengths: [64])

    /// Wired keyboard confirmed 2026-11-10: 65-byte write, reply without report ID.
    public static let wiredKeyboard = Target(
        device: .keyboard, name: "ROG Falchion RX LP", connection: .wired,
        productID: SquireProtocol.falchionRXLowProfilePID, reportID: nil,
        query: [0x12, 0x01], reportLengths: [65])

    public static let keyboardTargets = [omniKeyboard, wiredKeyboard]
    /// Query order: Omni first, wired as fallback (a device on the cable answers `ff aa` on the receiver).
    public static let all = keyboardTargets + mouseTargets

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

    /// Percent, charging, warning level. `base` is the echo index.
    public static func parse(_ device: Battery.Device, _ reply: [UInt8], base: Int)
        -> (percent: Int?, charging: Bool, lowWarning: Int?) {
        let p = Array(reply[base...])
        func byte(_ index: Int) -> UInt8? { p.count > index ? p[index] : nil }
        switch device {
        case .keyboard:
            // [10] carries the percentage; [5] is a copy (confirmed 2026-11-10)
            var percent = byte(10).flatMap { $0 <= 100 ? Int($0) : nil }
            if percent == nil { percent = byte(5).flatMap { $0 <= 100 ? Int($0) : nil } }
            let warning = byte(9).flatMap { (1...100).contains($0) ? Int($0) : nil }
            return (percent, (byte(8) ?? 0) != 0, warning)
        case .mouse:
            let percent = byte(4).flatMap { $0 <= 100 ? Int($0) : nil }
            let warning = byte(6).flatMap { (1...100).contains($0) ? Int($0) : nil }
            return (percent, (byte(9) ?? 0) != 0, warning)
        }
    }

    /// Turns the replies read after one query into a `Battery`. Pure: the IO layer feeds the replies in.
    public static func interpret(replies: [[UInt8]], target: Target) -> Battery {
        var result = Battery(device: target.device, name: target.name, connection: target.connection)
        var sawError = false
        for reply in replies {
            if reply.isEmpty { break }   // read timeout
            if isError(reply, reportID: target.reportID) {
                // The receiver sometimes sends `ff aa` right before the real reply (seen on the keyboard,
                // 2026-11-10): keep looking; it only means "asleep" if no valid reply follows.
                sawError = true
                result.raw = hex(reply)
                continue
            }
            guard let base = echoIndex(in: reply, query: target.query) else { continue }  // other traffic
            result.raw = hex(reply)
            let parsed = parse(target.device, reply, base: base)
            guard let percent = parsed.percent, !(percent == 0 && !parsed.charging) else {
                result.status = .asleep   // 0 % and not charging = treat as disconnected
                return result
            }
            result.percent = percent
            result.charging = parsed.charging
            result.lowWarning = parsed.lowWarning
            return result
        }
        result.status = sawError ? .asleep : .noResponse
        return result
    }

    /// First reply that echoes `echo`, same rules as squire.exchange: `ff aa` is provisional (a valid reply may
    /// follow); other traffic is skipped; an empty reply is a read timeout.
    public static func exchange(replies: [[UInt8]], echo: [UInt8], reportID: UInt8?) -> Exchange {
        var sawError = false, raw = ""
        for reply in replies {
            if reply.isEmpty { break }
            if isError(reply, reportID: reportID) { sawError = true; raw = hex(reply); continue }
            guard let base = echoIndex(in: reply, query: echo) else { continue }
            return Exchange(status: .ok, reply: reply, base: base, raw: hex(reply))
        }
        return Exchange(status: sawError ? .asleep : .noResponse, raw: raw)
    }

    public static func hex(_ bytes: [UInt8]) -> String {
        bytes.map { byte in
            let digits = String(byte, radix: 16)
            return digits.count == 1 ? "0" + digits : digits
        }.joined(separator: " ")
    }
}

/// One request/reply pair.
public struct Exchange: Equatable, Sendable {
    public enum Status: String, Sendable { case ok, asleep, noResponse = "no-response", error }
    public var status: Status
    public var reply: [UInt8] = []
    /// Index of the echoed command in `reply`.
    public var base = 0
    public var raw = ""

    public init(status: Status, reply: [UInt8] = [], base: Int = 0, raw: String = "") {
        self.status = status
        self.reply = reply
        self.base = base
        self.raw = raw
    }

    /// Reply from the echoed command on (indices as in protocol.json notes, without report ID).
    public var payload: [UInt8] { Array(reply.dropFirst(base)) }
}
