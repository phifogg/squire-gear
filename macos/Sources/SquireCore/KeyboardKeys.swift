import Foundation

// Per-key colours of the Falchion RX LP ("direct mode"), same as squire.py (keyboard_direct_packets,
// is_allowed_keyboard_write, key presets). Sources, facts only: G-Helper (GPL-3.0) AsusKeyboard.cs SetLedColors and
// AuraKeyboardLayouts.cs "FalchionRX" / "FalchionRXISO"; OpenRGB (GPL-2.0) has the same packet for the original Falchion.
// Confirmed on the device via the Omni and over the cable (owner, 2026-11-25, protocol.json keyboard_writes.direct):
//
//   c0 81 <n> 00 then n x <key id> <r g b>, n 1...14 (15 would overflow the Omni's 64-byte report)
//   reply c0 82 <n> 00 (not an echo); each packet must be acknowledged before the next
//
// Key id = column * 8 + row, columns 0...14, rows 1...5. The effect animation stops and the keyboard's own profile
// switching is blocked until it is switched off; an effect write (51 2c) ends it. The keyboard cannot store per-key
// colours (50 55 stores nothing), so Squire never saves them and keeps presets of its own.

public enum KeyboardDirect {
    static var cap: Capabilities.PerKey { KeyboardCapabilities.bundled.perKey }
    public static var write: [UInt8] { cap.write }
    public static var ack: [UInt8] { cap.ack }
    public static var perPacket: Int { cap.perPacket }
    /// 15 columns x rows 1...5 (01...75).
    public static var keys: Set<UInt8> { cap.keys }

    /// Same rule as squire.is_allowed_keyboard_write for c0 81.
    public static func isAllowed(_ c: [UInt8]) -> Bool { cap.allows(c) }

    /// c0 81 <n> 00 + n x <id> <r g b>, at most 14 keys per packet, in key id order.
    public static func commands(_ colours: [UInt8: RGB]) throws -> [Command] {
        try cap.commands(colours).map { c in
            guard KeyboardCapabilities.bundled.device.isAllowedWrite(c.bytes) else {
                throw CommandError.notAllowed(ReplyParser.hex(c.bytes))
            }
            return c
        }
    }

    /// {key id: "#rrggbb"} -> what is sent; keys that are not valid colours are dropped.
    public static func colours(_ keys: [UInt8: String]) -> [UInt8: RGB] {
        keys.compactMapValues { RGB(hex: $0) }
    }
}

/// Per-key setups kept by Squire (the keyboard cannot store them): keyboard-keys.json in the state dir, shared with
/// squire.py ({"version": 1, "presets": {name: {"keys": {"01": "#rrggbb"}}}}), plus Squire's default presets.
public enum KeyPresets {
    public static let version = 1

    public static var defaultURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/squire-gear/keyboard-keys.json")
    }

    struct Entry: Codable { var keys: [String: String] }
    struct File: Codable {
        var version: Int?
        var presets: [String: Entry]
    }

    /// The user's presets, {name: {key id: "#rrggbb"}}; broken entries are skipped, a broken file reads as empty.
    public static func load(_ url: URL = defaultURL) -> [String: [UInt8: String]] {
        guard let data = try? Data(contentsOf: url), let file = try? JSONDecoder().decode(File.self, from: data) else {
            return [:]
        }
        var presets: [String: [UInt8: String]] = [:]
        for (name, entry) in file.presets where !name.trimmingCharacters(in: .whitespaces).isEmpty {
            var keys: [UInt8: String] = [:]
            var ok = true
            for (id, colour) in entry.keys {
                guard let key = UInt8(id, radix: 16), KeyboardDirect.keys.contains(key),
                      let hex = KeyboardLighting.rgbHex(colour) else { ok = false; break }
                keys[key] = hex
            }
            if ok { presets[name] = keys }
        }
        return presets
    }

    /// The user's presets sorted by name, as squire.key_presets lists them.
    public static func names(_ presets: [String: [UInt8: String]]) -> [String] {
        presets.keys.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    private static func write(_ presets: [String: [UInt8: String]], _ url: URL) throws {
        var file = File(version: version, presets: [:])
        for (name, keys) in presets {
            file.presets[name] = Entry(keys: Dictionary(uniqueKeysWithValues: keys.map {
                (ReplyParser.hex([$0.key]).lowercased(), $0.value)
            }))
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(file).write(to: url, options: .atomic)
    }

    /// Store (or replace) a named per-key setup. Only Squire's file changes, never the keyboard.
    public static func save(_ name: String, _ keys: [UInt8: String], url: URL = defaultURL) throws {
        let name = name.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { throw CommandError.notAllowed("a preset needs a name") }
        guard !builtin(iso: true).keys.contains(where: { $0.caseInsensitiveCompare(name) == .orderedSame }) else {
            throw CommandError.notAllowed("\(name) is a Squire default preset: choose another name")
        }
        var normalised: [UInt8: String] = [:]
        for (key, colour) in keys {
            guard KeyboardDirect.keys.contains(key), let hex = KeyboardLighting.rgbHex(colour) else {
                throw CommandError.notAllowed("a preset needs keys within 01...75 and #rrggbb colours")
            }
            normalised[key] = hex
        }
        guard !normalised.isEmpty else { throw CommandError.notAllowed("a preset needs at least one key") }
        var presets = load(url)
        presets[name] = normalised
        try write(presets, url)
    }

    @discardableResult
    public static func delete(_ name: String, url: URL = defaultURL) throws -> Bool {
        var presets = load(url)
        guard presets.removeValue(forKey: name) != nil else { return false }
        try write(presets, url)
        return true
    }

    // MARK: Squire's default presets (squire.builtin_key_presets)

    /// Same groups as squire.KEY_GROUPS.
    public static let groups: [String: Set<UInt8>] = [
        "wasd": [0x12, 0x0B, 0x13, 0x1B],
        "arrows": [0x6C, 0x65, 0x6D, 0x75],
        "nav": [0x71, 0x72, 0x73, 0x74],
        "home": [0x23, 0x3B],
        "game_mods": [0x35, 0x04, 0x05],
        "modifiers": [0x01, 0x02, 0x03, 0x04, 0x05, 0x0D, 0x15, 0x35, 0x4D, 0x55, 0x5D, 0x69, 0x6B, 0x6A, 0x64],
    ]
    /// In the order squire.py lists them.
    public static let builtinNames = ["Fixed rainbow", "Gaming", "Typing", "Modifiers", "Ocean", "Fixed sunset"]

    /// Halves rounded up, as squire.py does (int(x + 0.5)), so both give the same bytes.
    static func byte(_ x: Double) -> UInt8 { UInt8(max(0, min(255, (x + 0.5).rounded(.down)))) }

    /// colorsys.hsv_to_rgb(h, 1, 1) * 255.
    static func hue(_ h: Double) -> String {
        let h = h.truncatingRemainder(dividingBy: 1)
        let i = Int(h * 6), f = h * 6 - Double(i), q = 1 - f, t = f
        let rgb: [Double]
        switch i % 6 {
        case 0: rgb = [1, t, 0]
        case 1: rgb = [q, 1, 0]
        case 2: rgb = [0, 1, t]
        case 3: rgb = [0, q, 1]
        case 4: rgb = [t, 0, 1]
        default: rgb = [1, 0, q]
        }
        return RGB(byte(rgb[0] * 255), byte(rgb[1] * 255), byte(rgb[2] * 255)).hex.lowercased()
    }

    static func blend(_ a: String, _ b: String, _ t: Double) -> String {
        let ca = RGB(hex: a)!, cb = RGB(hex: b)!
        let mix = { (x: UInt8, y: UInt8) in byte(Double(x) + (Double(y) - Double(x)) * t) }
        return RGB(mix(ca.r, cb.r), mix(ca.g, cb.g), mix(ca.b, cb.b)).hex.lowercased()
    }

    /// {name: {key id: "#rrggbb"}} for the keys of this layout. Colours, not brightness: per-key mode has no
    /// brightness, so dimmed keys are dark colours.
    public static func builtin(iso: Bool?) -> [String: [UInt8: String]] {
        let ids = KeyboardLayout.keyIDs(iso: iso)
        func all(_ colour: (UInt8) -> String) -> [UInt8: String] {
            Dictionary(uniqueKeysWithValues: ids.map { ($0, colour($0)) })
        }
        func on(_ group: String, _ colour: String, _ base: [UInt8: String]) -> [UInt8: String] {
            var keys = base
            for id in ids where groups[group]!.contains(id) { keys[id] = colour }
            return keys
        }
        let gaming = on("game_mods", "#ff8000", on("arrows", "#ff0000", on("wasd", "#ff0000", all { _ in "#202020" })))
        let modifiers = on("arrows", "#0000ff", on("nav", "#0000ff", on("modifiers", "#0000ff", all { _ in "#ffffff" })))
        return [
            "Fixed rainbow": all { hue(Double($0 / 8) / 15) },
            "Gaming": gaming,
            "Typing": on("home", "#0000ff", all { _ in "#ff8c00" }),
            "Modifiers": modifiers,
            "Ocean": all { blend("#0030ff", "#00ffc8", Double(Int($0 % 8) - 1) / 4) },
            "Fixed sunset": all { blend("#ff7a00", "#8a00ff", Double($0 / 8) / 14) },
        ]
    }

    /// Every key of the layout, keys the preset does not name off (black), as squire.key_preset_colours.
    public static func full(_ keys: [UInt8: String], iso: Bool?) -> [UInt8: String] {
        Dictionary(uniqueKeysWithValues: KeyboardLayout.keyIDs(iso: iso).map { ($0, keys[$0] ?? "#000000") })
    }
}
