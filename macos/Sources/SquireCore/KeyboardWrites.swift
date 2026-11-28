import Foundation

// Falchion RX Low Profile writes, same as squire.py (keyboard_light_value / keyboard_light_packets /
// is_allowed_keyboard_write). The builders and byte checks live in the capabilities (Capabilities/KBLighting.swift,
// KeyboardBasics.swift, PerKey.swift); this file keeps KeyboardLight, the old names, KeyboardChange and the memory. Every byte value comes from Armoury Crate captures on Windows (protocol.json
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

/// The keyboard lighting tables and builders under their old names; the capability (Capabilities.KBLighting, tables
/// from devices.json kb_lighting) does the work.
public enum KeyboardLighting {
    typealias Cap = Capabilities.KBLighting
    static var cap: Cap { KeyboardCapabilities.bundled.lighting }

    public static var speeds: [String: [(String, UInt8)]] { cap.speeds }
    public static let off = Cap.off
    public static var offBrightness: Int { cap.offBrightness }
    public static var effects: [String] { cap.effects }
    public static let singleColour = Cap.singleColour
    public static let optionEffects = Cap.optionEffects
    public static var colourModes: [(String, UInt8)] { cap.colourModes }
    public static let secondColour = Cap.secondColour
    public static let gradientEffects = Cap.gradientEffects
    public static var gradients: [(String, [(Int, String)])] { cap.gradients }
    public static var gradientStops: ClosedRange<Int> { cap.gradientStops }
    public static var directions: [String: [(String, UInt8)]] { cap.directions }
    public static let directionAliases = Cap.directionAliases
    public static var thicknesses: [(String, UInt8)] { cap.thicknesses }
    public static var quicksandScale: [String] { cap.quicksandScale }
    public static var quicksandModes: [(String, UInt8)] { cap.quicksandModes }
    public static let colourless = Cap.colourless

    /// The effect 12 00 [9] reports after `effect` was written.
    public static func effectRead(_ effect: String) -> String { Cap.effectRead(effect) }
    public static func effectID(_ effect: String) -> UInt8? { cap.effectID(effect) }

    /// "#rrggbb" in lower case, or nil if it is not one.
    public static func rgbHex(_ color: String?) -> String? {
        guard let color, let rgb = RGB(hex: color.trimmingCharacters(in: .whitespaces)) else { return nil }
        return rgb.hex.lowercased()
    }

    static func rgbBytes(_ color: String?) -> [UInt8] {
        guard let color, let rgb = RGB(hex: color) else { return [0, 0, 0] }
        return [rgb.r, rgb.g, rgb.b]
    }

    public static func stops(_ gradient: String?) throws -> [(Int, String)] { try cap.stops(gradient) }
    public static func gradientName(_ gradient: String?) throws -> String { try cap.gradientName(gradient) }
    public static func normalized(_ light: KeyboardLight) throws -> KeyboardLight { try cap.normalized(light) }

    /// Every 51 2c chunk, in the order they are sent, each checked against the keyboard's allowlist.
    public static func commands(_ light: KeyboardLight) throws -> [Command] {
        let chunks = try cap.chunks(light)
        // a chunk is echoed as command + effect + chunk index: a late echo of the previous one is not taken for it
        let echo = chunks.count > 1 ? 4 : 2
        return try chunks.map { try KeyboardCapabilities.bundled.checked($0, echoLength: echo) }
    }
}

extension Command {
    /// 50 55: stores the keyboard's settings (the mouse uses 50 03). Only after an explicit Apply.
    public static var keyboardSave: Command { Command(KeyboardLighting.cap.save, isWrite: true, echoLength: 2) }
    /// Keyboard low-battery warning levels: AC offers 10...50 % and never (0).
    public static var keyboardWarningLevels: [Int] { KeyboardCapabilities.bundled.warning.levels }

    /// 51 38 00 00 <code>: sleep after 1/2/3/5/10 min, nil = never (mouse power-off coding).
    public static func keyboardSleep(_ minutes: Int?) throws -> Command {
        let k = KeyboardCapabilities.bundled
        return try k.checked(k.sleep.command(minutes))
    }

    /// 51 3a 00 00 <%>: low-battery warning, 0 = never.
    public static func keyboardWarning(_ percent: Int) throws -> Command {
        let k = KeyboardCapabilities.bundled
        return try k.checked(k.warning.command(percent))
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
