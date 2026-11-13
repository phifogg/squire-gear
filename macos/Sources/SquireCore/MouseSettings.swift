// Keris II Origin settings, read-only parsing. Same layout as squire.py parse_mouse_settings; indices relative to
// the echoed command (no report ID). Confirmed with the captures in protocol.json `mouse_settings`.
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
    public var index: UInt8 {
        switch self { case .logo: return 0; case .wheel: return 1; case .side: return 2 }
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

    /// Effects Squire may write (G-Helper KerisIIOrigin). No rainbow/comet: they need direction/speed bytes.
    public static let writable: [LEDMode] = [.static, .breathing, .colorCycle, .react, .battery, .off]

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
    /// brightness not at all when off (same as squire.led_matches).
    public func matches(_ actual: LEDState?) -> Bool {
        guard let actual, actual.mode == mode else { return false }
        if mode == .off { return true }
        if actual.brightness != brightness { return false }
        return ![LEDMode.static, .breathing, .react].contains(mode) || actual.color == color
    }
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

    public static let pollingRatesHz = [125, 250, 500, 1000, 2000, 4000, 8000, 16000]   // low 3 bits
    public static let powerOffMinutes: [UInt8: Int] = [0: 1, 1: 2, 2: 3, 3: 5, 4: 10]    // 0xff = never

    /// Stages the DPI button uses (count from 12 00, else all read).
    public var activeDPI: [Int] {
        Array(dpi.prefix(dpiStageCount ?? dpi.count)).compactMap { $0 }
    }

    /// Fill from the battery reply (12 07).
    public mutating func applyBattery(_ p: [UInt8]) {
        guard p.count >= 20 else { return }
        powerOffMinutes = Self.powerOffMinutes[p[5]]
        lowWarning = (1...100).contains(p[6]) ? Int(p[6]) : nil
    }

    /// Fill from one settings reply payload `p` (starting at the echoed command).
    public mutating func apply(_ query: SettingsQuery, _ p: [UInt8]) {
        guard p.count >= 20 else { return }
        func u16(_ i: Int) -> Int { Int(p[i]) | Int(p[i + 1]) << 8 }
        func dpiValue(_ raw: Int) -> Int { raw * 50 + 50 }   // devices store (DPI - 50) / 50
        switch query {
        case .profile:
            profile = Int(p[10]) + 1
            dpiStage = p[11] == 0 ? nil : Int(p[11])
            dpiStageCount = (1...4).contains(p[18]) ? Int(p[18]) : nil
            // Firmware: 3 bytes, build.minor.major (shown major first, like libratbag). On the Keris II Origin the
            // mouse's own version is at [4..6] over the cable; via the Omni [4..6] is the receiver and the mouse
            // moves to [14..16] (same bytes 09 00 01 = 01.00.09 both ways). libratbag's [13..15] is not it here.
            func version(_ i: Int) -> String? {
                p[i..<i + 3].allSatisfy { $0 == 0 } ? nil
                    : [p[i + 2], p[i + 1], p[i]].map { ReplyParser.hex([$0]).uppercased() }.joined(separator: ".")
            }
            if connection == .wired {
                firmware = version(4)
            } else {
                firmware = version(14)
                receiverFirmware = version(4)
            }
        case .settings:
            // ff ff = not in this reply: separate-X/Y mice (like the Keris) report DPI only in dpi_xy
            dpi = (0..<4).map { u16(4 + 2 * $0) == 0xFFFF ? nil : dpiValue(u16(4 + 2 * $0)) }
            pollingRaw = p[12]
            pollingHz = Self.pollingRatesHz[Int(p[12] & 0x07)]
            debounceIndex = Int(p[14])
            debounceMs = (1...7).contains(p[14]) ? 4 + 4 * Int(p[14]) : nil
            angleSnapping = p[16] == 1
            let angle = u16(18)
            angleTuning = angle & 0x8000 != 0 ? angle - 0x10000 : angle
        case .dpiXY:
            dpiXY = (0..<4).map { [dpiValue(u16(4 + 4 * $0)), dpiValue(u16(6 + 4 * $0))] }
            if dpi.isEmpty || dpi.contains(where: { $0 == nil }) { dpi = dpiXY.map { $0[0] } }
        case .dpiColors:
            dpiColors = (0..<4).map { RGB(p[4 + 3 * $0], p[5 + 3 * $0], p[6 + 3 * $0]) }
        case .ledLogo, .ledWheel, .ledSide:
            let zone: LEDZone = query == .ledLogo ? .logo : query == .ledWheel ? .wheel : .side
            leds[zone] = LEDState(mode: LEDMode(byte: p[4]), brightness: Int(p[5]), color: RGB(p[6], p[7], p[8]))
        case .liftoff:
            // [6] surface id (ff = none), [7] lift-off, [8..10] surface parameters (Armoury Crate captures)
            liftoff = p[7] == 0 ? "low" : p[7] == 1 ? "high" : "0x" + ReplyParser.hex([p[7]])
            surfaceID = p[6]
            surfaceParams = p[6] == Command.noSurface ? nil : Array(p[8...10])
        }
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

    /// The bytes. Lift-off needs the surface from `current` (the 12 06 read right before the write).
    public func command(current: MouseSettings? = nil) throws -> Command {
        switch self {
        case .led(let zone, let led): return try .setLED(zone, led)
        case .power(let power): return try .setPower(power)
        case .angleSnapping(let on): return try .setAngleSnapping(on)
        case .angleTuning(let degrees): return try .setAngleTuning(degrees)
        case .liftoff(let value):
            return try .setLiftoff(value, surfaceID: current?.surfaceID, surfaceParams: current?.surfaceParams)
        case .surface(let id):
            // never replace a surface Squire does not know: it may be a calibration
            guard let current, let now = current.surfaceID, Command.surfaceParams[now] != nil, let liftoff = current.liftoff else {
                throw CommandError.notAllowed("surface \(Command.surfaceName(current?.surfaceID) ?? "not read"): not replaced")
            }
            return try .setSurface(id, liftoff: liftoff)
        case .polling(let hz): return try .setPolling(hz)
        case .dpi(let stage, let value):
            if let current {   // a stage with separate X/Y is never flattened
                let i = stage - 1
                guard i < current.dpiXY.count else { throw CommandError.notAllowed("DPI stage \(stage) not read") }
                guard current.dpiXY[i][0] == current.dpiXY[i][1] else {
                    throw CommandError.notAllowed("stage \(stage) has X \(current.dpiXY[i][0]) / Y \(current.dpiXY[i][1])")
                }
            }
            return try .setDPI(stage: stage, value)
        case .activeStage(let stage):
            guard let count = current?.dpiStageCount, stage <= count else {
                throw CommandError.notAllowed("stage \(stage) is not on the DPI button")
            }
            return try .setActiveStage(stage)
        case .stageCount(let count):
            guard let active = current?.dpiStage, count >= active else {
                throw CommandError.notAllowed("\(count) stages: the active stage is beyond them")
            }
            return try .setStageCount(count)
        }
    }

    /// The settings as they are once this change is written (for planning a sequence of writes).
    public func applied(to s: MouseSettings) -> MouseSettings {
        var p = s
        switch self {
        case .led(let zone, let led): p.leds[zone] = led
        case .power(let power): p.powerOffMinutes = power.offMinutes; p.lowWarning = power.lowWarning
        case .angleSnapping(let on): p.angleSnapping = on
        case .angleTuning(let degrees): p.angleTuning = degrees
        case .liftoff(let value): p.liftoff = value
        case .surface(let id): p.surfaceID = id; p.surfaceParams = Command.surfaceParams[id] ?? nil
        case .polling(let hz): p.pollingHz = hz
        case .dpi(let stage, let value):
            let i = stage - 1
            if i < p.dpiXY.count { p.dpiXY[i] = [value.dpi, value.dpi] }
            if i < p.dpiColors.count { p.dpiColors[i] = value.color }
            if i < p.dpi.count { p.dpi[i] = value.dpi }
        case .activeStage(let stage): p.dpiStage = stage
        case .stageCount(let count): p.dpiStageCount = count
        }
        return p
    }

    /// Built from the value read just before the write (not from the window's older read).
    public var needsCurrent: Bool {
        switch self { case .liftoff, .surface, .dpi, .activeStage, .stageCount: return true; default: return false }
    }

    /// The read that shows the value: nil = the battery query 12 07.
    public var readBack: SettingsQuery? {
        switch self {
        case .led(let zone, _): return .led(zone)
        case .power: return nil
        case .angleSnapping, .angleTuning: return .settings
        case .liftoff, .surface: return .liftoff
        case .polling: return .settings
        case .dpi: return .dpiXY
        case .activeStage, .stageCount: return .profile
        }
    }

    /// Every read that shows the value (nil = 12 07). DPI needs X/Y and the stage colours.
    public var reads: [SettingsQuery?] {
        if case .dpi = self { return [.dpiXY, .dpiColors] }
        return [readBack]
    }

    /// Does the mouse (as read) already have this value? With `before`, lift-off also needs the surface unchanged.
    public func matches(_ s: MouseSettings, before: MouseSettings? = nil) -> Bool {
        switch self {
        case .led(let zone, let led): return led.matches(s.leds[zone])
        case .power(let power):
            return s.lowWarning == power.lowWarning && s.powerOffMinutes == power.offMinutes && s.status == .ok
        case .angleSnapping(let on): return s.angleSnapping == on
        case .angleTuning(let degrees): return s.angleTuning == degrees
        case .liftoff(let value):
            return s.liftoff == value
                && (before.map { $0.surfaceID == s.surfaceID && $0.surfaceParams == s.surfaceParams } ?? true)
        case .surface(let id):
            return s.surfaceID == id && s.surfaceParams == Command.surfaceParams[id] ?? nil
                && (before.map { $0.liftoff == s.liftoff } ?? true)
        case .polling(let hz): return s.pollingHz == hz
        case .dpi(let stage, let value):
            let i = stage - 1
            return i < s.dpiXY.count && i < s.dpiColors.count && s.dpiXY[i] == [value.dpi, value.dpi]
                && s.dpiColors[i] == value.color
        case .activeStage(let stage):
            return s.dpiStage == stage && (before.map { $0.dpiStageCount == s.dpiStageCount } ?? true)
        case .stageCount(let count):
            return s.dpiStageCount == count && (before.map { $0.dpiStage == s.dpiStage } ?? true)
        }
    }

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
