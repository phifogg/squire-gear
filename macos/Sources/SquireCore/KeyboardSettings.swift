// Falchion RX Low Profile settings, read-only. Same as squire.py parse_keyboard_settings; indices relative to the
// echoed command (no report ID). Source, facts only: G-Helper app/Peripherals/Keyboard/AsusKeyboard.cs (GPL-3.0).
// Confirmed with the captures in protocol.json `keyboard_settings` (owner, 2026-11-13).
//
//   12 00  [4..6] receiver firmware (Omni), [9] current lighting effect, [10] active profile (1-based, 1...5)
//   12 12  [5] factory layout id (25 = CH-DE, ISO)
//   12 01  [6] sleep timeout (mouse coding assumed, not confirmed), [9] low-battery warning %
//
// The LED colour cannot be read: no source knows a command for it, and 12 03 (the mouse's LED read) answers
// without a colour (protocol.json keyboard_settings.probes).

/// Read-only queries for the keyboard settings (approved by the owner 2026-11-13).
public enum KeyboardQuery: String, CaseIterable, Sendable {
    // raw values = the names in squire.py (KEYBOARD_SETTINGS_QUERIES) and protocol.json
    case profile, layout

    public var command: Command {
        switch self {
        case .profile: return Command([0x12, 0x00])
        case .layout: return Command([0x12, 0x12])
        }
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
    public var sleepMinutes: Int?       // nil with status ok = never; coding not confirmed
    public var lowWarning: Int?
    public var receiverFirmware: String?
    /// query name -> reply hex (or status), for the debug view and new captures
    public var raw: [String: String] = [:]

    public init(name: String = "ROG Falchion RX LP", connection: Battery.Connection? = nil, status: Status = .ok) {
        self.name = name
        self.connection = connection
        self.status = status
    }

    public static let profileCount = 5

    /// Effect ids in 12 00 [9] (G-Helper KeyboardLightingMode); 0xf1 = the host drives the LEDs directly.
    public static let lightModes: [UInt8: String] = [
        0x00: "static", 0x01: "breathing", 0x02: "color-cycle", 0x03: "reactive", 0x04: "wave", 0x05: "ripple",
        0x06: "starry-night", 0x07: "quicksand", 0x08: "current", 0x09: "raindrop", 0xF1: "host-driven",
    ]
    /// Factory layout ids in 12 12 [5]: 3/4/5/17 from G-Helper, 25 = CH-DE confirmed by the owner.
    public static let layouts: [Int: String] = [3: "DE", 4: "UK", 5: "FR", 17: "US", 25: "CH-DE"]
    /// Layout ids G-Helper draws as ISO (an extra key left of Z, tall Enter).
    public static let isoLayouts: Set<Int> = [3, 4, 5, 7, 8, 11, 12, 15, 18, 19, 20, 23, 25]

    /// Fill from the battery reply (12 01).
    public mutating func applyBattery(_ p: [UInt8]) {
        guard p.count >= 14 else { return }
        sleepMinutes = MouseSettings.powerOffMinutes[p[6]]
        lowWarning = (1...100).contains(p[9]) ? Int(p[9]) : nil
    }

    /// Fill from one settings reply payload `p` (starting at the echoed command).
    public mutating func apply(_ query: KeyboardQuery, _ p: [UInt8]) {
        guard p.count >= 14 else { return }
        switch query {
        case .profile:
            lightMode = Self.lightModes[p[9]] ?? "0x" + ReplyParser.hex([p[9]])
            profile = (1...Self.profileCount).contains(Int(p[10])) ? Int(p[10]) : nil
            if connection != .wired, p[4..<7].contains(where: { $0 != 0 }) {
                receiverFirmware = [p[6], p[5], p[4]].map { ReplyParser.hex([$0]).uppercased() }.joined(separator: ".")
            }
        case .layout:
            layoutID = Int(p[5])
            layout = Self.layouts[Int(p[5])]
            iso = Self.isoLayouts.contains(Int(p[5]))
        }
    }

    /// The effect as shown in the window.
    public static func effectTitle(_ mode: String?) -> String {
        guard let mode else { return "–" }
        switch mode {
        case "host-driven": return "Controlled by an app"
        case "starry-night": return "Starry night"
        case "color-cycle": return "Color cycle"
        default:
            let words = mode.replacingOccurrences(of: "-", with: " ")
            return words.prefix(1).uppercased() + words.dropFirst()
        }
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
            return "\(sleep) · warn at \(k.lowWarning.map(String.init) ?? "–") %"
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
            return [("Sleep", k.sleepMinutes.map { "After \($0) min" } ?? "Never", "Not confirmed yet"),
                    ("Low-battery warning", k.lowWarning.map { "\($0) %" } ?? "–", "")]
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

    private static let row0: [Key] = [Key("§")] + keys("1234567890") + [Key("'"), Key("^"), Key("⌫", 2), Key("Del")]
    private static let row4: [Key] = [Key("Ctrl", 1.25), Key("❖", 1.25), Key("Alt", 1.25), Key("", 6.25), Key("Alt"),
                                      Key("Fn"), Key("Ctrl"), Key("←"), Key("↓"), Key("→")]

    public static let iso: [[Key]] = [
        row0,
        [Key("Tab", 1.5)] + keys("QWERTZUIOPü¨") + [Key(isoEnter, 1.5), Key("PgUp")],
        [Key("Caps", 1.75)] + keys("ASDFGHJKLöä$") + [Key(isoEnterLow, 1.25), Key("PgDn")],
        [Key("⇧", 1.25), Key("<")] + keys("YXCVBNM,.-") + [Key("⇧", 1.75), Key("↑"), Key("End")],
        row4,
    ]

    public static let ansi: [[Key]] = [
        [Key("`")] + keys("1234567890") + [Key("-"), Key("="), Key("⌫", 2), Key("Del")],
        [Key("Tab", 1.5)] + keys("QWERTYUIOP[]") + [Key("\\", 1.5), Key("PgUp")],
        [Key("Caps", 1.75)] + keys("ASDFGHJKL;'") + [Key("Enter", 2.25), Key("PgDn")],
        [Key("⇧", 2.25)] + keys("ZXCVBNM,./") + [Key("⇧", 1.75), Key("↑"), Key("End")],
        row4,
    ]

    /// ISO unless the keyboard reports an ANSI layout.
    public static func rows(for k: KeyboardSettings?) -> [[Key]] { k?.iso == false ? ansi : iso }
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
