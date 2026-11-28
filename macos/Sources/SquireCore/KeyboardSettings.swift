// Falchion RX Low Profile settings, read-only. Same as squire.py parse_keyboard_settings; indices relative to the
// echoed command (no report ID). Source, facts only: G-Helper app/Peripherals/Keyboard/AsusKeyboard.cs (GPL-3.0).
// Confirmed with the captures in protocol.json `keyboard_settings` (owner, 2026-11-13).
//
//   12 00  [4..6] receiver firmware, [11..13] keyboard firmware (both Omni; wired: firmware at [4..6], 2026-11-21), [9] current lighting effect, [10] active profile (1-based, 1...5)
//   12 12  [5] factory layout id (25 = CH-DE, ISO)
//   12 01  [6] sleep timeout (mouse power-off coding, confirmed with Armoury Crate 2026-11-21), [9] low-battery warning %
//
// The LED colour cannot be read: no source knows a command for it, and 12 03 (the mouse's LED read) answers
// without a colour (protocol.json keyboard_settings.probes).

/// Read-only queries for the keyboard settings (approved by the owner 2026-11-13).
public enum KeyboardQuery: String, CaseIterable, Sendable {
    // raw values = the names in squire.py (KEYBOARD_SETTINGS_QUERIES) and protocol.json
    case profile, layout

    /// The read's bytes from devices.json (only confirmed reads pass Packet.build).
    public var command: Command {
        guard let bytes = KeyboardCapabilities.bundled.model.command(rawValue)?.bytes?.bytes else {
            fatalError("devices.json: no keyboard read \(rawValue)")
        }
        return Command(bytes)
    }
}

public struct KeyboardSettings: Equatable, Sendable {
    public typealias Status = MouseSettings.Status

    public var name: String
    public var connection: Battery.Connection?
    public var status: Status
    public var profile: Int?            // 1-based (01/02/05 = profile 1/2/5 confirmed)
    public var lightMode: String?       // effect the keyboard runs now (names as in squire.py)
    public var layoutID: Int?
    public var layout: String?          // e.g. CH-DE; nil for an id Squire does not know
    public var iso: Bool?
    public var sleepMinutes: Int?       // nil with status ok = never
    public var lowWarning: Int?         // 0 = never
    public var firmware: String?        // the keyboard's, 12 00 [11..13] via the Omni, [4..6] wired
    public var receiverFirmware: String?
    /// query name -> reply hex (or status), for the debug view and new captures
    public var raw: [String: String] = [:]

    public init(name: String = "ROG Falchion RX LP", connection: Battery.Connection? = nil, status: Status = .ok) {
        self.name = name
        self.connection = connection
        self.status = status
    }

    static var caps: KeyboardCapabilities { .bundled }
    /// Profiles on the keyboard (devices.json kb_profile).
    public static var profileCount: Int { caps.profile.profiles }
    /// Effect ids in 12 00 [9] (G-Helper KeyboardLightingMode); 0xf1 = the host drives the LEDs directly.
    public static var lightModes: [UInt8: String] { caps.lighting.lightModes }
    /// Factory layout ids in 12 12 [5] with a name (devices.json kb_layout).
    public static var layouts: [Int: String] { caps.layout.names }
    /// Layout ids G-Helper draws as ISO (an extra key left of Z, tall Enter).
    public static var isoLayouts: Set<Int> { caps.layout.iso }

    /// Fill from the battery reply (12 01): sleep and warning.
    public mutating func applyBattery(_ p: [UInt8], capabilities: KeyboardCapabilities = .bundled) {
        guard p.count >= 14 else { return }
        capabilities.parse("battery", p, into: &self)
    }

    /// Fill from one settings reply payload `p` (starting at the echoed command); each capability reads its part.
    public mutating func apply(_ query: KeyboardQuery, _ p: [UInt8], capabilities: KeyboardCapabilities = .bundled) {
        guard p.count >= 14 else { return }
        capabilities.parse(query.rawValue, p, into: &self)
    }

    /// The effect as shown in the window.
    public static func effectTitle(_ mode: String?) -> String {
        guard let mode else { return "–" }
        switch mode {
        case "host-driven": return "Controlled by an app"
        case "starry-night": return "Starry night"
        case "color-cycle": return "Color cycle"
        case "off": return "Off"
        default:
            let words = mode.replacingOccurrences(of: "-", with: " ")
            return words.prefix(1).uppercased() + words.dropFirst()
        }
    }

    /// "20 %", "never" (0) or "–" (not read).
    public static func warningText(_ level: Int?) -> String {
        guard let level else { return "–" }
        return level == 0 ? "never" : "\(level) %"
    }

    public var layoutText: String {
        guard let layoutID else { return "–" }
        return (layout ?? "Unknown (id \(layoutID))") + (iso == true ? " · ISO" : " · ANSI")
    }
}

/// The parts of the keyboard page: the keys (lighting) and three chips. Same as KB_ZONE_TITLES on Linux.
public enum KeyboardZone: String, CaseIterable, Sendable, Identifiable {
    case light, profile, layout, power
    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .light: return "Lighting"
        case .profile: return "Profile"
        case .layout: return "Layout"
        case .power: return "Power"
        }
    }

    public static let chips: [KeyboardZone] = [.profile, .layout, .power]

    /// Short value under the callout or chip.
    public func summary(_ k: KeyboardSettings) -> String {
        switch self {
        case .light: return KeyboardSettings.effectTitle(k.lightMode)
        case .profile: return k.profile.map { "\($0) of \(KeyboardSettings.profileCount)" } ?? "not read"
        case .layout: return k.layoutID == nil ? "not read" : k.layoutText
        case .power:
            let sleep = k.sleepMinutes.map { "sleep \($0) min" } ?? "never sleeps"
            return "\(sleep) · " + (k.lowWarning == 0 ? "never warns" : "warn at \(KeyboardSettings.warningText(k.lowWarning))")
        }
    }

    /// Rows for the inspector: (title, value, subtitle). Shown only.
    public func rows(_ k: KeyboardSettings) -> [(String, String, String)] {
        switch self {
        case .light: return [("Effect", KeyboardSettings.effectTitle(k.lightMode), "Its color cannot be read yet")]
        case .profile:
            return [("Active profile", k.profile.map { "\($0) of \(KeyboardSettings.profileCount)" } ?? "–",
                     "Switch profiles on the keyboard")]
        case .layout: return [("Layout", k.layoutText, "Set at the factory")]
        case .power:
            return [("Sleep", k.sleepMinutes.map { "After \($0) min" } ?? "Never", ""),
                    ("Low-battery warning", KeyboardSettings.warningText(k.lowWarning).capitalized, "")]
        }
    }
}

/// Key rows of the drawing (own neutral artwork): a 65 % board, 16 units per row. ISO rows follow the CH-DE layout
/// the owner's keyboard reports; ANSI rows are the US shape. Same as KEYBOARD_ROWS in linux/squire_settings.py.
public enum KeyboardLayout {
    public struct Key: Equatable, Sendable {
        public let legend: String
        public let width: Double
        public init(_ legend: String, _ width: Double = 1) { self.legend = legend; self.width = width }
    }

    /// The ISO Enter spans rows 2 and 3: drawn once from the top part; the lower part only takes up its space.
    public static let isoEnter = "ISO-ENTER"
    public static let isoEnterLow = "ISO-ENTER-LOW"

    private static func keys(_ legends: String) -> [Key] { legends.map { Key(String($0)) } }

    // Top left is Esc, the right column Ins / Del / PgUp / PgDn (G-Helper's key list; Insert confirmed 2026-11-25).
    private static let row0: [Key] = [Key("Esc")] + keys("1234567890") + [Key("'"), Key("^"), Key("⌫", 2), Key("Ins")]
    private static let row4: [Key] = [Key("Ctrl", 1.25), Key("❖", 1.25), Key("Alt", 1.25), Key("", 6.25), Key("Alt"),
                                      Key("Fn"), Key("Ctrl"), Key("←"), Key("↓"), Key("→")]

    public static let iso: [[Key]] = [
        row0,
        [Key("Tab", 1.5)] + keys("QWERTZUIOPü¨") + [Key(isoEnter, 1.5), Key("Del")],
        [Key("Caps", 1.75)] + keys("ASDFGHJKLöä$") + [Key(isoEnterLow, 1.25), Key("PgUp")],
        [Key("⇧", 1.25), Key("<")] + keys("YXCVBNM,.-") + [Key("⇧", 1.75), Key("↑"), Key("PgDn")],
        row4,
    ]

    public static let ansi: [[Key]] = [
        [Key("Esc")] + keys("1234567890") + [Key("-"), Key("="), Key("⌫", 2), Key("Ins")],
        [Key("Tab", 1.5)] + keys("QWERTYUIOP[]") + [Key("\\", 1.5), Key("Del")],
        [Key("Caps", 1.75)] + keys("ASDFGHJKL;'") + [Key("Enter", 2.25), Key("PgUp")],
        [Key("⇧", 2.25)] + keys("ZXCVBNM,./") + [Key("⇧", 1.75), Key("↑"), Key("PgDn")],
        row4,
    ]

    /// ISO unless the keyboard reports an ANSI layout.
    public static func rows(for k: KeyboardSettings?) -> [[Key]] { k?.iso == false ? ansi : iso }

    // Key id of each key, row by row, as drawn (same count per row as `iso` / `ansi`): devices.json per_key.layouts,
    // same as squire.KEYBOARD_KEY_IDS (G-Helper AuraKeyboardLayouts.cs "FalchionRX" / "FalchionRXISO", facts only).
    // ISO Enter spans two rows and has one id (6b), listed in both.
    public static var isoIDs: [[UInt8]] { layoutIDs("iso") }
    public static var ansiIDs: [[UInt8]] { layoutIDs("ansi") }

    private static func layoutIDs(_ name: String) -> [[UInt8]] {
        guard let ids = KeyboardCapabilities.bundled.perKey.layouts[name] else { fatalError("devices.json: no per_key layout \(name)") }
        return ids
    }

    public static func ids(for k: KeyboardSettings?) -> [[UInt8]] { k?.iso == false ? ansiIDs : isoIDs }

    /// Every key id of the layout once, in drawing order (squire.keyboard_key_ids).
    public static func keyIDs(iso: Bool?) -> [UInt8] {
        var seen: [UInt8] = []
        for row in iso == false ? ansiIDs : isoIDs { for id in row where !seen.contains(id) { seen.append(id) } }
        return seen
    }
}

extension ProtocolCaptures {
    /// Keyboard settings as the demo shows them: the first capture of each command (profile 1) and the battery
    /// vector. Sends nothing.
    public func demoKeyboard() -> KeyboardSettings {
        var k = KeyboardSettings(connection: .omni)
        if let v = vectors.first(where: { $0.device == "keyboard" && $0.reportId == Int(Target.omniKeyboard.reportID!) }) {
            k.applyBattery(Array(v.bytes.dropFirst()))
        }
        var seen = Set<String>()
        for capture in keyboardCaptures where seen.insert(capture.command).inserted {
            guard let query = KeyboardQuery(rawValue: capture.command) else { continue }
            k.apply(query, Array(capture.bytes.dropFirst()))
        }
        return k
    }
}
