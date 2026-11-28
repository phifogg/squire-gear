// Keris II Origin settings. The parsers live in the capabilities (Capabilities/, offsets from devices.json); this file
// keeps the settings types. Indices relative to the echoed command (no report ID). Confirmed with the captures in
// protocol.json `mouse_settings`.
//
//   12 00     [10] profile (0-based), [11] active DPI stage (1-based), [18] stage count, firmware [4..6] (cable) or [14..16] (Omni; [4..6] = receiver)
//   12 04 00  [4..11] DPI (ff ff on this separate-X/Y mouse), [12] polling (low 3 bits), [14] debounce,
//             [16] angle snapping, [18..19] angle tuning (signed LE16)
//   12 04 02  X/Y per stage from [4] (lo/hi each, DPI = v * 50 + 50)
//   12 04 03  RGB per stage from [4]
//   12 03 NN  zone NN: [4] mode, [5] brightness %, [6..8] RGB
//   12 06     [7] lift-off (0 low, 1 high)
//   12 07     [5] power-off step, [6] low-battery warning %

public enum LEDZone: String, CaseIterable, Sendable {
    case logo, wheel, side
    /// The zone byte in 51 28 / 12 03 (devices.json led_zones).
    public var index: UInt8 {
        guard let index = MouseCapabilities.bundled.ledZones.zoneIndex[rawValue] else {
            fatalError("devices.json: no LED zone \(rawValue)")
        }
        return index
    }
}

public enum LEDMode: Hashable, Sendable {
    case `static`, breathing, colorCycle, rainbow, react, comet, battery, off
    case unknown(UInt8)

    public init(byte: UInt8) {
        switch byte {
        case 0x00: self = .static
        case 0x01: self = .breathing
        case 0x02: self = .colorCycle
        case 0x03: self = .rainbow
        case 0x04: self = .react
        case 0x05: self = .comet
        case 0x06: self = .battery
        case 0xF0: self = .off
        default: self = .unknown(byte)
        }
    }

    public var byte: UInt8 {
        switch self {
        case .static: return 0x00
        case .breathing: return 0x01
        case .colorCycle: return 0x02
        case .rainbow: return 0x03
        case .react: return 0x04
        case .comet: return 0x05
        case .battery: return 0x06
        case .off: return 0xF0
        case .unknown(let byte): return byte
        }
    }

    /// Effects Squire may write (devices.json led_zones, writable). No rainbow/comet: they need direction/speed bytes.
    public static var writable: [LEDMode] { MouseCapabilities.bundled.ledZones.writable }

    /// Names as in squire.py / protocol.json.
    public var name: String {
        switch self {
        case .static: return "static"
        case .breathing: return "breathing"
        case .colorCycle: return "color-cycle"
        case .rainbow: return "rainbow"
        case .react: return "react"
        case .comet: return "comet"
        case .battery: return "battery"
        case .off: return "off"
        case .unknown(let byte): return "0x" + ReplyParser.hex([byte])
        }
    }

    public init?(name: String) {
        guard let mode = ([.static, .breathing, .colorCycle, .rainbow, .react, .comet, .battery, .off] as [LEDMode])
            .first(where: { $0.name == name }) else { return nil }
        self = mode
    }

    public var title: String {
        switch self {
        case .colorCycle: return "Color cycle"
        case .unknown: return name
        default: return name.prefix(1).uppercased() + name.dropFirst()
        }
    }

    /// Only one-colour effects have a colour; cycle, rainbow and battery pick their own.
    public var showsColor: Bool { [.static, .breathing, .react, .comet].contains(self) }
}

public struct RGB: Hashable, Sendable {
    public var r: UInt8, g: UInt8, b: UInt8
    public init(_ r: UInt8, _ g: UInt8, _ b: UInt8) { self.r = r; self.g = g; self.b = b }

    public init?(hex: String) {
        let digits = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        guard digits.count == 6, let value = UInt32(digits, radix: 16) else { return nil }
        self.init(UInt8(value >> 16 & 0xFF), UInt8(value >> 8 & 0xFF), UInt8(value & 0xFF))
    }

    public var hex: String { "#" + ReplyParser.hex([r, g, b]).replacingOccurrences(of: " ", with: "") }
    public var isBlack: Bool { r == 0 && g == 0 && b == 0 }
}

public struct LEDState: Hashable, Sendable {
    public var mode: LEDMode
    public var brightness: Int   // 0-100 %
    public var color: RGB

    public init(mode: LEDMode, brightness: Int, color: RGB) {
        self.mode = mode
        self.brightness = brightness
        self.color = color
    }

    /// Does the device state `actual` show what was asked for? Colour only counts for one-colour effects,
    /// brightness not at all when off (Capabilities.LEDZones.matches).
    public func matches(_ actual: LEDState?) -> Bool { Capabilities.LEDZones.matches(self, actual) }
}

public struct MouseSettings: Equatable, Sendable {
    public enum Status: String, Sendable {
        case ok, asleep, noResponse = "no-response", noAccess = "no-access", error, notFound = "not-found"
    }

    public var name: String
    public var connection: Battery.Connection?
    public var status: Status
    public var profile: Int?            // 1-based
    public var dpiStage: Int?           // active stage, 1-based
    public var dpiStageCount: Int?      // stages the DPI button cycles through
    public var dpi: [Int?] = []
    public var dpiXY: [[Int]] = []
    public var dpiColors: [RGB] = []
    public var pollingHz: Int?
    public var pollingRaw: UInt8?
    public var debounceIndex: Int?
    public var debounceMs: Int?
    public var angleSnapping: Bool?
    public var angleTuning: Int?
    public var liftoff: String?         // low | high
    public var surfaceID: UInt8?        // preset surface (Command.surfaces), 0xff = none
    public var surfaceParams: [UInt8]?  // its 3 parameter bytes, nil without a surface
    public var leds: [LEDZone: LEDState] = [:]
    public var powerOffMinutes: Int?    // nil with status ok = never
    public var lowWarning: Int?
    public var firmware: String?            // the mouse's own firmware
    public var receiverFirmware: String?    // the Omni receiver's (Omni only)
    /// query name -> reply hex (or status), for the debug view and new captures
    public var raw: [String: String] = [:]

    public init(name: String = "ROG Keris II Origin", connection: Battery.Connection? = nil, status: Status = .ok) {
        self.name = name
        self.connection = connection
        self.status = status
    }

    /// Polling rate per index in the low 3 bits (devices.json polling.read_rates_hz).
    public static var pollingRatesHz: [Int] { MouseCapabilities.bundled.polling.readRatesHz }
    /// Power-off byte -> minutes (0xff = never has no entry).
    public static var powerOffMinutes: [UInt8: Int] { MouseCapabilities.bundled.power.powerOffMinutes }

    /// Stages the DPI button uses (count from 12 00, else all read).
    public var activeDPI: [Int] {
        Array(dpi.prefix(dpiStageCount ?? dpi.count)).compactMap { $0 }
    }

    /// Fill from the battery reply (12 07): power-off and warning (Capabilities.Power).
    public mutating func applyBattery(_ p: [UInt8], capabilities: MouseCapabilities = .bundled) {
        guard p.count >= 20 else { return }
        capabilities.power.parse(capabilities.power.read, p, into: &self)
    }

    /// Fill from one settings reply payload `p` (starting at the echoed command); each capability reads its part.
    public mutating func apply(_ query: SettingsQuery, _ p: [UInt8], capabilities: MouseCapabilities = .bundled) {
        guard p.count >= 20 else { return }
        capabilities.parse(query.rawValue, p, into: &self)
    }
}

/// Outcome of one LED write (+ optional save), as squire.WriteResult.
/// One write the settings window can make: the command, where it is read back, and whether the read-back matches.
public enum SettingChange: Equatable, Hashable, Sendable {
    case led(LEDZone, LEDState)
    case power(PowerSetting)
    case angleSnapping(Bool)
    case angleTuning(Int)
    case liftoff(String)        // low | high; keeps the surface
    case surface(UInt8)         // preset id or Command.noSurface; keeps the lift-off
    case polling(Int)           // Hz
    case dpi(stage: Int, DPIStage)  // 1-based stage; X and Y alike
    case activeStage(Int)       // 1-based
    case stageCount(Int)

    /// The capability's description of this write: commands, reads, read-back check (Capabilities/).
    public var change: Change<MouseSettings> {
        let m = MouseCapabilities.bundled
        switch self {
        case .led(let zone, let led): return m.ledZones.change(zone, led)
        case .power(let power): return m.power.change(power)
        case .angleSnapping(let on): return m.sensor.snappingChange(on)
        case .angleTuning(let degrees): return m.sensor.tuningChange(degrees)
        case .liftoff(let value): return m.surface.liftoffChange(value)
        case .surface(let id): return m.surface.surfaceChange(id)
        case .polling(let hz): return m.polling.change(hz)
        case .dpi(let stage, let value): return m.dpi.change(stage: stage, value)
        case .activeStage(let stage): return m.dpi.activeStageChange(stage)
        case .stageCount(let count): return m.dpi.stageCountChange(count)
        }
    }

    /// The bytes. Lift-off needs the surface from `current` (the 12 06 read right before the write).
    public func command(current: MouseSettings? = nil) throws -> Command {
        try MouseCapabilities.bundled.commands(change, current: current)[0]
    }

    /// The settings as they are once this change is written (for planning a sequence of writes).
    public func applied(to s: MouseSettings) -> MouseSettings { change.applied(to: s) }

    /// Built from the value read just before the write (not from the window's older read).
    public var needsCurrent: Bool { change.needsCurrent }

    /// Every read that shows the value (nil = 12 07). DPI needs X/Y and the stage colours.
    public var reads: [SettingsQuery?] { change.reads.map { SettingsQuery(rawValue: $0) } }

    /// The read that shows the value: nil = the battery query 12 07.
    public var readBack: SettingsQuery? { reads.first ?? nil }

    /// Does the mouse (as read) already have this value? With `before`, lift-off also needs the surface unchanged.
    public func matches(_ s: MouseSettings, before: MouseSettings? = nil) -> Bool { change.matches(s, before: before) }

    /// Short name for messages.
    public var title: String {
        switch self {
        case .led(let zone, _): return SettingsZone(rawValue: zone.rawValue)?.title ?? zone.rawValue
        case .power: return "Power"
        case .angleSnapping: return "Angle snapping"
        case .angleTuning: return "Angle tuning"
        case .liftoff: return "Lift-off distance"
        case .surface: return "Surface"
        case .polling: return "Polling rate"
        case .dpi(let stage, _): return "DPI stage \(stage)"
        case .activeStage: return "Active DPI stage"
        case .stageCount: return "DPI stages"
        }
    }
}

public struct WriteResult: Equatable, Sendable {
    public enum Status: String, Sendable {
        case ok, mismatch, asleep, noResponse = "no-response", noAccess = "no-access", notFound = "not-found"
        case error
    }
    public var status: Status
    public var sent: [String] = []
    public var before: LEDState?
    public var after: LEDState?
    /// The settings as read back after the write (only the fields of that read are filled).
    public var readBack: MouseSettings?
    public var saved = false
    public var message = ""

    public init(status: Status, sent: [String] = [], before: LEDState? = nil, after: LEDState? = nil,
                saved: Bool = false, message: String = "") {
        self.status = status
        self.sent = sent
        self.before = before
        self.after = after
        self.saved = saved
        self.message = message
    }
}
