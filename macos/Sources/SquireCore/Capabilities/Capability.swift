// Capability layer (same as squirelib/capabilities): what a device can do, one type per devices.json capability
// (led_zones, dpi, polling, ...). Each is built from the device's model entry and holds its write builders, the byte
// check for its writes (its part of the allowlist) and the parser for its part of the read replies.
// DeviceCapabilities puts a device's capabilities together: a write is allowed only with a prefix devices.json marks
// "confirmed" and when the capability that owns the prefix accepts the bytes.
//
// Sending is not done here: HIDReader runs a change in one device session (awake check, read, write, read back, save
// only on a match), and Packet.build checks every packet against the allowlist once more.

/// Namespace for the capability types (Capabilities.LEDZones, Capabilities.DPI, ...).
public enum Capabilities {}

public protocol Capability: Sendable {
    /// Prefixes of the write commands this capability sends (devices.json, any status): it answers for writes that
    /// start with one of them.
    var prefixes: [[UInt8]] { get }
    /// Byte check of a write that starts with one of `prefixes`.
    func allows(_ command: [UInt8]) -> Bool
}

/// A mouse capability: fills its fields of MouseSettings from one read reply (payload from the echo on).
public protocol MouseCapability: Capability {
    func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings)
}

/// A devices.json entry that a capability needs is missing or does not fit.
public struct ModelError: Error, CustomStringConvertible {
    public let description: String
    init(_ description: String) { self.description = description }
}

/// The write side of one device's capabilities: the confirmed prefixes and the owner of each write.
public struct DeviceCapabilities: Sendable {
    public let confirmedPrefixes: [[UInt8]]
    let items: [any Capability]

    init(model: DeviceModels.Model, items: [any Capability]) {
        confirmedPrefixes = model.confirmedWritePrefixes
        self.items = items
    }

    /// The capability whose write prefix the command starts with; the longest prefix wins (51 31 04 before 51 31).
    func owner(of command: [UInt8]) -> (any Capability)? {
        var best: (any Capability)?, length = 0
        for item in items {
            for prefix in item.prefixes where command.starts(with: prefix) && prefix.count > length {
                best = item
                length = prefix.count
            }
        }
        return best
    }

    /// Confirmed prefix first, then the owning capability's byte check (squire's is_allowed_write).
    public func isAllowedWrite(_ command: [UInt8], confirmed: [[UInt8]]? = nil) -> Bool {
        guard (confirmed ?? confirmedPrefixes).contains(where: { command.starts(with: $0) }) else { return false }
        return owner(of: command)?.allows(command) ?? false
    }
}

extension DeviceModels.Model {
    /// Prefixes of a write command, whatever its status (the confirmed ones are the allowlist).
    public func writePrefixes(_ name: String) -> [[UInt8]] {
        guard let command = command(name), command.isWrite else { return [] }
        return (command.prefixes ?? []).map(\.bytes)
    }

    func requiredPrefix(_ name: String) throws -> [UInt8] {
        guard let prefix = writePrefixes(name).first else { throw ModelError("\(id): no write command \(name)") }
        return prefix
    }

    func offset(_ offsets: [String: Int], _ name: String, _ capability: String) throws -> Int {
        guard let value = offsets[name] else { throw ModelError("\(id): \(capability) has no offset \(name)") }
        return value
    }
}

/// Little-endian 16-bit value at `i`.
func u16(_ p: [UInt8], _ i: Int) -> Int { Int(p[i]) | Int(p[i + 1]) << 8 }

/// 3 bytes build / minor / major, shown major first like libratbag; all zero = not reported.
func firmwareVersion(_ p: [UInt8], _ i: Int) -> String? {
    p[i..<i + 3].allSatisfy { $0 == 0 } ? nil
        : [p[i + 2], p[i + 1], p[i]].map { ReplyParser.hex([$0]).uppercased() }.joined(separator: ".")
}
