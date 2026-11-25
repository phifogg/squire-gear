import Foundation

// Falchion RX Low Profile writes, same as squire.py (keyboard_light_value / keyboard_light_packets /
// is_allowed_keyboard_write). Every byte value comes from Armoury Crate captures on Windows (protocol.json
// `keyboard_writes`, capture_4 has every option) and was confirmed on the device via the Omni (owner, 2026-11-22).
// G-Helper AsusKeyboard.cs (GPL-3.0, facts only) has the same 51 2c layout and 50 55; its 51 37 warning is the mouse's.
//
//   51 2c <effect> 00 <speed> <brightness %> <mode> ff ff <r g b> [<r2 g2 b2> <background r g b>]   one-colour effects
//   51 2c <effect> 02 <speed> <brightness> 00 <direction> <thickness> <n> <2 stops>, then 01 / 00 <up to 4 stops>
//   51 2c 07 01 <speed> <brightness> <mode> <direction> ff <3 RGB>, then 51 2c 07 00 <3 RGB>       quicksand
//   51 38 00 00 <sleep code>    51 3a 00 00 <warning %, 0 = never>    50 55 save

/// One lighting setting as Squire writes it. Field names and values are those of squire.KEYBOARD_LIGHT_FIELDS, so
/// the remembered lighting (keyboard-lighting.json) is shared with squire.py. A field the effect does not use is nil.
public struct KeyboardLight: Hashable, Sendable, Codable {
    public var effect: String
    public var brightness: Int
    public var color: String?        // "#rrggbb"
    public var speed: String?
    public var gradient: String?     // preset name or "POS:RRGGBB,..."
    public var direction: String?
    public var thickness: String?
    public var mode: String?         // single / double / random, or scale / random (quicksand)
    public var color2: String?
    public var background: String?  // nil = off

    public init(effect: String, brightness: Int = 100, color: String? = "#ffffff", speed: String? = nil,
                gradient: String? = nil, direction: String? = nil, thickness: String? = nil, mode: String? = nil,
                color2: String? = nil, background: String? = nil) {
        self.effect = effect
        self.brightness = brightness
        self.color = color
        self.speed = speed
        self.gradient = gradient
        self.direction = direction
        self.thickness = thickness
        self.mode = mode
        self.color2 = color2
        self.background = background
    }
}

public enum KeyboardLighting {
    /// Effect -> speeds (name, byte) in AC's order fast / normal / slow; larger = slower. Static has none (ff).
    public static let speeds: [String: [(String, UInt8)]] = [
        "static": [("none", 0xFF)],
        "breathing": [("fast", 0x0F), ("normal", 0x1E), ("slow", 0x2A)],
        "color-cycle": [("fast", 0x12), ("normal", 0x1F), ("slow", 0x3E)],
        "reactive": [("fast", 0x08), ("normal", 0x0F), ("slow", 0x16)],
        "rainbow": [("fast", 0x23), ("normal", 0x3E), ("slow", 0x55)],
        "ripple": [("fast", 0x50), ("normal", 0x64), ("slow", 0x78)],
        "starry-night": [("fast", 0x0A), ("normal", 0x0F), ("slow", 0x19)],
        "quicksand": [("fast", 0x0C), ("normal", 0x0F), ("slow", 0x14)],
        "current": [("fast", 0x07), ("normal", 0x0A), ("slow", 0x0C)],
        "raindrop": [("fast", 0x0E), ("normal", 0x14), ("slow", 0x1B)],
    ]
    /// "off" is AC's black static at 4 %; the keyboard then reports static.
    public static let off = "off"
    public static let offBrightness = 4
    /// Every effect Squire writes, in the keyboard's id order, then off.
    public static let effects = ["static", "breathing", "color-cycle", "reactive", "rainbow", "ripple", "starry-night",
                                 "quicksand", "current", "raindrop", off]
    public static let singleColour: Set<String> = ["static", "breathing", "color-cycle", "reactive", "starry-night",
                                                   "current", "raindrop"]
    /// One-colour effects with a colour mode, a second colour and a background.
    public static let optionEffects: Set<String> = ["starry-night", "current", "raindrop"]
    public static let colourModes: [(String, UInt8)] = [("single", 0x00), ("double", 0x10), ("random", 0x01)]
    public static let secondColour = "#ff00ff"         // AC's second colour when switching to double
    public static let gradientEffects: Set<String> = ["rainbow", "ripple"]
    /// AC's 7 presets in AC's order; AC shows them without names, the names are Squire's (same as squire.py).
    public static let gradients: [(String, [(Int, String)])] = [
        ("red", [(14, "ff0029"), (100, "ff0029")]),
        ("blue-red", [(14, "0018f0"), (100, "ff0031")]),
        ("sunset", [(14, "febe3f"), (57, "fe3f7d"), (100, "f91d1d")]),
        ("lime-cyan", [(14, "d1fe3f"), (45, "00dcff"), (72, "00dcff"), (100, "d1fe3f")]),
        ("yellow-magenta", [(14, "f1ff00"), (36, "ff00ed"), (57, "f1ff00"), (79, "ff00ed"), (100, "f1ff00")]),
        ("pink-purple", [(14, "ff0091"), (32, "8c00ff"), (49, "4b00d9"), (66, "4b00d9"), (83, "8c00ff"), (100, "ff0091")]),
        ("spectrum", [(14, "f500ff"), (29, "0006ff"), (43, "00faff"), (57, "01ff00"), (72, "fff600"), (86, "ff7807"),
                      (100, "ff000d")]),
    ]
    public static let gradientStops = 2...7
    /// Direction byte per effect, the first is the default. Rainbow: compass in 45° steps (all confirmed on the device).
    public static let directions: [String: [(String, UInt8)]] = [
        "rainbow": [("right", 0x00), ("down-right", 0x01), ("down", 0x02), ("down-left", 0x03), ("left", 0x04),
                    ("up-left", 0x05), ("up", 0x06), ("up-right", 0x07), ("outwards", 0x08)],
        "ripple": [("none", 0xFF)],
        "quicksand": [("down", 0x02), ("up", 0x06)],
    ]
    public static let directionAliases = ["left-to-right": "right", "right-to-left": "left"]
    public static let thicknesses: [(String, UInt8)] = [("strong", 0x02), ("normal", 0x01), ("low", 0x00)]
    public static let quicksandScale = ["fff600", "01ff00", "00faff", "0006ff", "f500ff", "ff000d"]
    public static let quicksandModes: [(String, UInt8)] = [("scale", 0x00), ("random", 0x01)]
    public static let colourless: Set<String> = ["color-cycle", "rainbow", "ripple", "quicksand", off]

    static func lookup(_ table: [(String, UInt8)], _ name: String?) -> UInt8? { table.first { $0.0 == name }?.1 }

    /// The effect 12 00 [9] reports after `effect` was written.
    public static func effectRead(_ effect: String) -> String { effect == off ? "static" : effect }

    public static func effectID(_ effect: String) -> UInt8? {
        KeyboardSettings.lightModes.first { $0.value == effectRead(effect) && speeds[$0.value] != nil }?.key
    }

    /// "#rrggbb" in lower case, or nil if it is not one.
    public static func rgbHex(_ color: String?) -> String? {
        guard let color, let rgb = RGB(hex: color.trimmingCharacters(in: .whitespaces)) else { return nil }
        return rgb.hex.lowercased()
    }

    static func rgbBytes(_ color: String?) -> [UInt8] {
        guard let color, let rgb = RGB(hex: color) else { return [0, 0, 0] }
        return [rgb.r, rgb.g, rgb.b]
    }

    /// (position %, "rrggbb") for a preset name or a custom gradient "POS:RRGGBB,..." (2...7 stops, 1...100 % ascending).
    public static func stops(_ gradient: String?) throws -> [(Int, String)] {
        let name = gradient ?? "spectrum"
        if let preset = gradients.first(where: { $0.0 == name }) { return preset.1 }
        var result: [(Int, String)] = []
        for part in name.split(separator: ",") {
            let pieces = part.split(separator: ":").map { $0.trimmingCharacters(in: .whitespaces) }
            guard pieces.count == 2, let pos = Int(pieces[0]), let hex = rgbHex(pieces[1]) else {
                throw CommandError.notAllowed("gradient \(name)")
            }
            result.append((pos, String(hex.dropFirst())))
        }
        let positions = result.map(\.0)
        guard gradientStops.contains(result.count), positions.allSatisfy({ (1...100).contains($0) }),
              positions == Array(Set(positions)).sorted() else {
            throw CommandError.notAllowed("gradient \(name): 2 to 7 stops at 1...100 %, ascending")
        }
        return result
    }

    /// A preset name, or the custom gradient written canonically.
    public static func gradientName(_ gradient: String?) throws -> String {
        let name = gradient ?? "spectrum"
        if gradients.contains(where: { $0.0 == name }) { return name }
        return try stops(name).map { "\($0.0):\($0.1)" }.joined(separator: ",")
    }

    static func pick(_ what: String, _ value: String?, _ table: [(String, UInt8)]) throws -> String {
        let value = value ?? table[0].0
        guard lookup(table, value) != nil else { throw CommandError.notAllowed("\(what) \(value)") }
        return value
    }

    /// Validated and normalised, exactly like squire.keyboard_light_value: the speed actually sent, colours in lower
    /// case (#000000 for an effect without one), nil for every field the effect does not use. Options the effect
    /// does not use are ignored. "off" is fixed at 4 % black.
    public static func normalized(_ light: KeyboardLight) throws -> KeyboardLight {
        var l = light
        if l.effect == off { l.brightness = offBrightness; l.color = "#000000" }
        let read = effectRead(l.effect)
        guard let table = speeds[read] else { throw CommandError.notAllowed("keyboard effect \(l.effect)") }
        let speed = l.speed ?? (lookup(table, "normal") != nil ? "normal" : table[0].0)
        guard lookup(table, speed) != nil else { throw CommandError.notAllowed("speed \(speed) for \(l.effect)") }
        guard (0...100).contains(l.brightness) else { throw CommandError.notAllowed("brightness \(l.brightness)") }
        var out = KeyboardLight(effect: l.effect, brightness: l.brightness, color: "#000000", speed: speed)
        if !colourless.contains(l.effect) {
            guard let color = rgbHex(l.color ?? "#ffffff") else { throw CommandError.notAllowed("colour \(l.color ?? "")") }
            out.color = color
        }
        if gradientEffects.contains(l.effect) {
            out.gradient = try gradientName(l.gradient)
            out.direction = try pick("direction", l.direction.map { directionAliases[$0] ?? $0 }, directions[l.effect]!)
            out.thickness = try pick("thickness", l.thickness, thicknesses)
        } else if l.effect == "quicksand" {
            out.direction = try pick("direction", l.direction.map { directionAliases[$0] ?? $0 }, directions[l.effect]!)
            out.mode = try pick("colour mode", l.mode, quicksandModes)
        } else if optionEffects.contains(l.effect) {
            out.mode = try pick("colour mode", l.mode, colourModes)
            if out.mode != "single" {
                guard let c2 = rgbHex(l.color2 ?? secondColour) else { throw CommandError.notAllowed("second colour") }
                out.color2 = c2
            }
            if let bg = l.background {
                guard let hex = rgbHex(bg) else { throw CommandError.notAllowed("background \(bg)") }
                out.background = hex == "#000000" ? nil : hex
            }
        }
        return out
    }

    /// Every 51 2c chunk, in the order they are sent (one, three for a gradient, two for quicksand).
    public static func commands(_ light: KeyboardLight) throws -> [Command] {
        let v = try normalized(light)
        let effect = effectRead(v.effect)
        guard let id = effectID(v.effect), let speed = lookup(speeds[effect]!, v.speed) else {
            throw CommandError.notAllowed("keyboard effect \(v.effect)")
        }
        let b = UInt8(v.brightness)
        var chunks: [[UInt8]]
        if singleColour.contains(effect) {
            let head: [UInt8] = [0x51, 0x2C, id, 0x00, speed, b, 0x00, 0xFF, 0xFF]
            var c: [UInt8] = head + rgbBytes(v.color)
            if optionEffects.contains(effect) {
                c[6] = lookup(colourModes, v.mode) ?? 0
                c += rgbBytes(v.color2)
                c += rgbBytes(v.background)
            }
            chunks = [c]
        } else if effect == "quicksand" {
            let scale: [UInt8] = quicksandScale.flatMap { rgbBytes("#" + $0) }
            let mode: UInt8 = lookup(quicksandModes, v.mode) ?? 0
            let direction: UInt8 = lookup(directions[effect] ?? [], v.direction) ?? 0x02
            let head: [UInt8] = [0x51, 0x2C, id, 0x01, speed, b, mode, direction, 0xFF]
            let tail: [UInt8] = [0x51, 0x2C, id, 0x00]
            chunks = [head + Array(scale[0..<9]), tail + Array(scale[9..<18])]
        } else {
            let packed: [[UInt8]] = try stops(v.gradient).map { [UInt8($0.0)] + rgbBytes("#" + $0.1) }
            func flat(_ range: Range<Int>) -> [UInt8] {
                packed.indices.filter { range.contains($0) }.flatMap { packed[$0] }
            }
            let direction: UInt8 = lookup(directions[effect] ?? [], v.direction) ?? 0x00
            let thickness: UInt8 = lookup(thicknesses, v.thickness) ?? 0x02
            let first: [UInt8] = [0x51, 0x2C, id, 0x02, speed, b, 0x00, direction, thickness, UInt8(packed.count)]
            let second: [UInt8] = [0x51, 0x2C, id, 0x01]
            let third: [UInt8] = [0x51, 0x2C, id, 0x00]
            chunks = [first + flat(0..<2), second + flat(2..<6), third + flat(6..<10)]
        }
        // a chunk is echoed as command + effect + chunk index: a late echo of the previous one is not taken for it
        let echo = chunks.count > 1 ? 4 : 2
        return try chunks.map { c in
            guard Allowlist.isAllowedKeyboardWrite(c) else { throw CommandError.notAllowed(ReplyParser.hex(c)) }
            return Command(c, isWrite: true, echoLength: echo)
        }
    }

    static func stopsOK(_ s: ArraySlice<UInt8>) -> Bool {
        s.count % 4 == 0 && stride(from: s.startIndex, to: s.endIndex, by: 4).allSatisfy { (1...100).contains(s[$0]) }
    }
}

extension Command {
    /// 50 55: stores the keyboard's settings (the mouse uses 50 03). Only after an explicit Apply.
    public static let keyboardSave = Command([0x50, 0x55], isWrite: true, echoLength: 2)
    /// Keyboard low-battery warning levels: AC offers 10...50 % and never (0).
    public static let keyboardWarningLevels = [10, 20, 30, 40, 50, 0]

    /// 51 38 00 00 <code>: sleep after 1/2/3/5/10 min, nil = never (mouse power-off coding).
    public static func keyboardSleep(_ minutes: Int?) throws -> Command {
        guard let code = powerOffCodes[minutes] else {
            throw CommandError.notAllowed("sleep \(minutes.map(String.init) ?? "never") min")
        }
        return try keyboardChecked([0x51, 0x38, 0x00, 0x00, code])
    }

    /// 51 3a 00 00 <%>: low-battery warning, 0 = never.
    public static func keyboardWarning(_ percent: Int) throws -> Command {
        guard keyboardWarningLevels.contains(percent) else { throw CommandError.notAllowed("warning \(percent) %") }
        return try keyboardChecked([0x51, 0x3A, 0x00, 0x00, UInt8(percent)])
    }

    private static func keyboardChecked(_ bytes: [UInt8]) throws -> Command {
        guard Allowlist.isAllowedKeyboardWrite(bytes) else { throw CommandError.notAllowed(ReplyParser.hex(bytes)) }
        return Command(bytes, isWrite: true, echoLength: 2)
    }
}

extension Allowlist {
    /// Same rules as squire.is_allowed_keyboard_write.
    public static func isAllowedKeyboardWrite(_ c: [UInt8]) -> Bool {
        typealias L = KeyboardLighting
        if c == Command.keyboardSave.bytes { return true }
        if c.starts(with: [0x51, 0x38]) {
            return c.count == 5 && c[2] == 0 && c[3] == 0 && Command.powerOffCodes.values.contains(c[4])
        }
        if c.starts(with: [0x51, 0x3A]) {
            return c.count == 5 && c[2] == 0 && c[3] == 0 && Command.keyboardWarningLevels.contains(Int(c[4]))
        }
        if c.starts(with: KeyboardDirect.write) { return KeyboardDirect.isAllowed(c) }   // KeyboardKeys.swift
        guard c.count >= 4, c[0] == 0x51, c[1] == 0x2C, let effect = KeyboardSettings.lightModes[c[2]],
              let table = L.speeds[effect] else { return false }
        let speeds = Set(table.map(\.1))
        if L.optionEffects.contains(effect) {
            return c.count == 18 && c[3] == 0 && speeds.contains(c[4]) && c[5] <= 100
                && L.colourModes.contains { $0.1 == c[6] } && c[7] == 0xFF && c[8] == 0xFF
                && (c[6] != 0x00 || (c[12] == 0 && c[13] == 0 && c[14] == 0))
        }
        if L.singleColour.contains(effect) {
            return c.count == 12 && c[3] == 0 && speeds.contains(c[4]) && c[5] <= 100 && c[6] == 0 && c[7] == 0xFF
                && c[8] == 0xFF && (!L.colourless.contains(effect) || (c[9] == 0 && c[10] == 0 && c[11] == 0))
        }
        if effect == "quicksand" {
            if c[3] == 0x01 {
                return c.count == 18 && speeds.contains(c[4]) && c[5] <= 100 && L.quicksandModes.contains { $0.1 == c[6] }
                    && L.directions["quicksand"]!.contains { $0.1 == c[7] } && c[8] == 0xFF
            }
            return c[3] == 0x00 && c.count == 13
        }
        switch c[3] {
        case 0x02:
            guard c.count == 18 else { return false }
            return speeds.contains(c[4]) && c[5] <= 100 && c[6] == 0
                && L.directions[effect]!.contains { $0.1 == c[7] } && L.thicknesses.contains { $0.1 == c[8] }
                && L.gradientStops.contains(Int(c[9])) && L.stopsOK(c[10...])
        case 0x01, 0x00:
            return c.count - 4 <= 16 && L.stopsOK(c[4...])
        default:
            return false
        }
    }
}

/// One keyboard write the settings window can make, read back like squire.set_keyboard_*.
public enum KeyboardChange: Equatable, Hashable, Sendable {
    case sleep(Int?)          // minutes, nil = never
    case warning(Int)         // %, 0 = never
    case light(KeyboardLight)
    /// Per-key colours {key id: "#rrggbb"} (KeyboardKeys.swift): never saved, the keyboard cannot store them.
    case keys([UInt8: String])

    public func commands() throws -> [Command] {
        switch self {
        case .sleep(let minutes): return [try .keyboardSleep(minutes)]
        case .warning(let percent): return [try .keyboardWarning(percent)]
        case .light(let light): return try KeyboardLighting.commands(light)
        case .keys(let keys): return try KeyboardDirect.commands(KeyboardDirect.colours(keys))
        }
    }

    /// Whether Apply saves it (50 55). Per-key colours are not: 50 55 stores nothing (tested 2026-11-25).
    public var isSaved: Bool {
        if case .keys = self { return false }
        return true
    }

    /// The read that shows the value: nil = the battery query 12 01, else 12 00 (effect only: colour and brightness
    /// cannot be read from this keyboard; per-key colours not at all, only that the profile stayed).
    public var readBack: KeyboardQuery? {
        switch self {
        case .light, .keys: return .profile
        default: return nil
        }
    }

    /// Does the keyboard (as read) show this value? For lighting only the effect is readable, and the profile must
    /// not have changed in between.
    public func matches(_ k: KeyboardSettings, before: KeyboardSettings? = nil) -> Bool {
        switch self {
        case .sleep(let minutes):
            // compare the code itself where the raw reply is known: an unknown code is not "never"
            if let raw = k.raw["battery"].map({ $0.split(separator: " ").compactMap { UInt8($0, radix: 16) } }),
               raw.count > 6 {
                return raw[6] == Command.powerOffCodes[minutes]
            }
            return k.sleepMinutes == minutes
        case .warning(let percent): return k.lowWarning == percent
        case .light(let light):
            return k.lightMode == KeyboardLighting.effectRead(light.effect) && k.profile != nil
                && (before.map { $0.profile == k.profile } ?? true)
        case .keys:
            // nothing to read back: only that the keyboard still answers on the same profile (squire.py does the same)
            return k.profile != nil && (before.map { $0.profile == k.profile } ?? true)
        }
    }

    public var title: String {
        switch self {
        case .sleep: return "Sleep"
        case .warning: return "Low-battery warning"
        case .light: return "Lighting"
        case .keys: return "Per-key colors"
        }
    }
}

/// Outcome of one keyboard write (+ optional save 50 55), as squire.WriteResult.
public struct KeyboardWriteResult: Equatable, Sendable {
    public var status: WriteResult.Status
    public var sent: [String] = []
    /// The value read right before and after the write (only the fields of that read are filled).
    public var before: KeyboardSettings?
    public var after: KeyboardSettings?
    public var saved = false
    public var message = ""

    public init(status: WriteResult.Status, sent: [String] = [], saved: Bool = false, message: String = "") {
        self.status = status
        self.sent = sent
        self.saved = saved
        self.message = message
    }
}

/// The lighting Squire last saved per profile, shared with squire.py: keyboard-lighting.json in the state dir
/// (~/Library/Application Support/squire-gear on macOS). Version 2: reactive's 0f is "normal" (it was "fast").
public enum KeyboardLightMemory {
    public static let version = 2

    public static var defaultURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/squire-gear/keyboard-lighting.json")
    }

    struct File: Codable {
        var version: Int?
        var profiles: [String: KeyboardLight]
    }

    static func load(_ url: URL) -> File? {
        guard let data = try? Data(contentsOf: url), var file = try? JSONDecoder().decode(File.self, from: data) else {
            return nil
        }
        if (file.version ?? 1) < 2 {
            for (key, light) in file.profiles where light.effect == "reactive" && light.speed == "fast" {
                file.profiles[key]?.speed = "normal"
            }
        }
        return file
    }

    /// What Squire saved to this profile, if the keyboard still runs that effect (else it is stale).
    public static func remembered(profile: Int?, effect: String?, url: URL = defaultURL) -> KeyboardLight? {
        guard let profile, let effect, let stored = load(url)?.profiles[String(profile)],
              let value = try? KeyboardLighting.normalized(stored),
              KeyboardLighting.effectRead(value.effect) == effect else { return nil }
        return value
    }

    public static func remember(_ light: KeyboardLight, profile: Int, url: URL = defaultURL) {
        var file = load(url) ?? File(version: version, profiles: [:])
        file.version = version
        file.profiles[String(profile)] = light
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(file) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }
}
