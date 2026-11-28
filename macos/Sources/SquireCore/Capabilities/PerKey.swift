// Per-key colours (squirelib/capabilities/per_key.py), Falchion RX LP "direct mode": c0 81 <n> 00 + n x <key id> <r g b>,
// acknowledged with c0 82 <n> 00 (not an echo). Key id = column * 8 + row. Never saved: the keyboard cannot store it.
// Sources, facts only: G-Helper AsusKeyboard.cs SetLedColors + AuraKeyboardLayouts.cs (GPL-3.0). Confirmed on the device
// via the Omni and over the cable (owner, 2026-11-25, protocol.json keyboard_writes.direct).

extension Capabilities {
    public struct PerKey: Capability {
        public let prefixes: [[UInt8]]
        public let write: [UInt8]               // c0 81
        public let ack: [UInt8]                 // c0 82
        /// At most this many keys per packet (15 would overflow the Omni's 64-byte report).
        public let perPacket: Int
        /// Every key id: columns x rows.
        public let keys: Set<UInt8>
        /// Key ids per drawn row and layout ("iso", "ansi").
        public let layouts: [String: [[UInt8]]]

        init(_ model: DeviceModels.Model, _ params: DeviceModels.PerKey) throws {
            write = try model.requiredPrefix(params.write)
            guard let ack = model.command(params.write)?.reply?.bytes else {
                throw ModelError("\(model.id): per_key has no reply")
            }
            self.ack = ack
            prefixes = model.writePrefixes(params.write)
            perPacket = params.perPacket
            keys = Set((0..<params.columns).flatMap { col in (params.rows.min...params.rows.max).map { UInt8(col * 8 + $0) } })
            layouts = params.layouts.mapValues { $0.map { $0.map(\.value) } }
        }

        public func allows(_ c: [UInt8]) -> Bool {
            guard c.count >= 4, c.starts(with: write) else { return false }
            let n = Int(c[2])
            return (1...perPacket).contains(n) && c[3] == 0 && c.count == 4 + 4 * n
                && (0..<n).allSatisfy { keys.contains(c[4 + 4 * $0]) }
        }

        /// Packets in key id order, `perPacket` keys each.
        public func packets(_ colours: [UInt8: RGB]) throws -> [[UInt8]] {
            let items = colours.sorted { $0.key < $1.key }
            guard !items.isEmpty else { throw CommandError.notAllowed("no keys") }
            return stride(from: 0, to: items.count, by: perPacket).map { start in
                let chunk = items[start..<min(start + perPacket, items.count)]
                return write + [UInt8(chunk.count), 0x00] + chunk.flatMap { [$0.key, $0.value.r, $0.value.g, $0.value.b] }
            }
        }
    }
}

extension Capabilities.PerKey {
    /// Packets with the acknowledgement each expects (c0 82 <n> 00).
    public func commands(_ colours: [UInt8: RGB]) throws -> [Command] {
        try packets(colours).map { writeCommand($0, echoLength: 4, reply: ack + [$0[2], 0x00]) }
    }

    /// {key id: "#rrggbb"}; keys that are not valid colours are dropped. Never saved (50 55 stores nothing). Nothing
    /// to read back: only that the keyboard still answers on the same profile (squire.py does the same).
    public func change(_ keys: [UInt8: String]) -> Change<KeyboardSettings> {
        Change(reads: ["profile"], isSaved: false,
               build: { _ in try commands(keys.compactMapValues { RGB(hex: $0) }) },
               matches: { k, before in k.profile != nil && (before.map { $0.profile == k.profile } ?? true) })
    }
}
