// Command allowlist, same as squire.py (ALLOWED_COMMANDS, is_allowed_write, packet).
//
// Reads are "get" requests (first byte 0x12) and must be on `Allowlist.reads`. Writes (0x50 / 0x51) are built
// only by the functions here, carry `isWrite`, and must pass `Allowlist.isAllowedWrite`. `Packet.build` is the
// only place the transport gets bytes from, so nothing else can ever reach a device.
//
// Sources, facts only: libratbag src/asus.c + driver-asus.c (MIT) and G-Helper app/Peripherals/Mouse (GPL-3.0).
// Confirmed on the Keris II Origin, see protocol.json `mouse_settings` and `mouse_writes`.

public struct Command: Equatable, Hashable, Sendable {
    public let bytes: [UInt8]
    public let isWrite: Bool
    /// How many leading bytes of the command the reply echoes (and is matched on).
    public let echoLength: Int

    init(_ bytes: [UInt8], isWrite: Bool = false, echoLength: Int? = nil) {
        self.bytes = bytes
        self.isWrite = isWrite
        self.echoLength = echoLength ?? bytes.count
    }

    public static let keyboardBattery = Command([0x12, 0x01])
    public static let mouseBattery = Command([0x12, 0x07])
    /// 50 03: stores the current settings in the mouse's flash. Only after an explicit Apply.
    public static let save = Command([0x50, 0x03], isWrite: true, echoLength: 2)

    /// 51 28 zone 00 mode brightness r g b 00 00 00: set one LED zone. Speed byte [11] stays 0: the Keris stores it
    /// but ignores it (tested 2026-11-10).
    public static func setLED(_ zone: LEDZone, _ led: LEDState) throws -> Command {
        guard LEDMode.writable.contains(led.mode) else { throw CommandError.notAllowed("LED effect \(led.mode.name)") }
        guard (0...100).contains(led.brightness) else { throw CommandError.notAllowed("brightness \(led.brightness)") }
        let bytes: [UInt8] = [0x51, 0x28, zone.index, 0x00, led.mode.byte, UInt8(led.brightness),
                              led.color.r, led.color.g, led.color.b, 0x00, 0x00, 0x00]
        guard Allowlist.isAllowedWrite(bytes) else { throw CommandError.notAllowed(ReplyParser.hex(bytes)) }
        return Command(bytes, isWrite: true, echoLength: 2)
    }

    // Power and sensor writes. Source, facts only: G-Helper AsusMouse.cs + Models/KerisIIOrigin.cs (GPL-3.0);
    // angle snapping also libratbag asus_set_angle_snapping(). Approved by the owner and confirmed on the device
    // 2026-11-12 (protocol.json mouse_writes.settings_writes).

    /// Power-off minutes (nil = never) -> byte, as in the 12 07 reply [5].
    public static let powerOffCodes: [Int?: UInt8] = [1: 0x00, 2: 0x01, 3: 0x02, 5: 0x03, 10: 0x04, nil: 0xFF]
    public static let lowWarningLevels = [10, 20, 30, 40, 50]          // % (G-Helper: step 10, max 50)
    public static let angleTuningDegrees = Array(stride(from: -20, through: 20, by: 5))

    /// 51 37 00 00 <power-off> 00 <warning %>: auto power-off and low-battery warning, always both.
    public static func setPower(_ power: PowerSetting) throws -> Command {
        guard let code = powerOffCodes[power.offMinutes] else {
            throw CommandError.notAllowed("power-off \(power.offMinutes.map(String.init) ?? "never") min")
        }
        guard lowWarningLevels.contains(power.lowWarning) else {
            throw CommandError.notAllowed("low-battery warning \(power.lowWarning) %")
        }
        return try checked([0x51, 0x37, 0x00, 0x00, code, 0x00, UInt8(power.lowWarning)])
    }

    /// 51 31 06 00 <0/1>
    public static func setAngleSnapping(_ on: Bool) throws -> Command {
        try checked([0x51, 0x31, 0x06, 0x00, on ? 0x01 : 0x00])
    }

    /// 51 31 0b 00 <lo> <hi>: degrees, signed LE16, -20...20 in steps of 5.
    public static func setAngleTuning(_ degrees: Int) throws -> Command {
        guard angleTuningDegrees.contains(degrees) else { throw CommandError.notAllowed("angle tuning \(degrees)°") }
        let v = UInt16(bitPattern: Int16(degrees))
        return try checked([0x51, 0x31, 0x0B, 0x00, UInt8(v & 0xFF), UInt8(v >> 8)])
    }

    // Lift-off and surface share 51 35. Source: Armoury Crate captures on Windows (USBPcap, 2026-11-12, protocol.json
    // mouse_writes.armoury_crate). Approved 2026-11-13. A lift-off write always carries the surface id and
    // parameters read from 12 06 just before, so the surface stays (G-Helper's 51 35 ff 00 ff <l> drops it).

    public static let liftoffCodes: [String: UInt8] = ["low": 0x00, "high": 0x01]
    public static let noSurface: UInt8 = 0xFF
    /// Armoury Crate's preset surfaces (id -> name), same as squire.SURFACES.
    public static let surfaces: [UInt8: String] = [
        0x00: "ROG Whetstone", 0x01: "ROG Sheath", 0x07: "ROG Scabbard", 0x08: "ROG Strix Edge", 0x0A: "Balteus",
        0x0B: "Balteus Qi", 0x0C: "ROG Scabbard II", 0x0D: "ROG Sheath Gundam LTD",
        0x0E: "ROG Strix Edge Call of Duty Black Ops 4 Edition", 0x0F: "ROG Sheath BLK LTD", 0x10: "ROG Sheath PNK LTD",
        0x11: "ROG Sheath Electro Punk", 0x12: "ROG Hone Ace Aim Lab Edition", 0x13: "ROG Scabbard II EVA Edition",
        0x14: "ROG Moonstone Ace L", 0x15: "ROG Moonstone Ace L (2)", 0x16: "ROG Hone Ace XXL",
        0x17: "ROG Scabbard II Arctic Grey XXL", 0x18: "TUF Gaming P1 Hatsune Miku Edition", 0x19: "ROG Sheath II XXL",
    ]

    /// Parameters Armoury Crate writes with each preset (captured 2026-11-12); nil for "no surface".
    public static let surfaceParams: [UInt8: [UInt8]?] = {
        let common: [UInt8] = [0x1C, 0x34, 0x16], balteus: [UInt8] = [0x1A, 0x32, 0x20]
        var table: [UInt8: [UInt8]?] = [noSurface: nil, 0x0A: balteus, 0x0B: balteus, 0x14: [0x37, 0x2B, 0x0E],
                                        0x15: [0x18, 0x35, 0x23], 0x17: [0x21, 0x23, 0x12], 0x18: [0x1C, 0x42, 0x24],
                                        0x19: [0x1E, 0x34, 0x19]]
        for id in surfaces.keys where table[id] == nil { table[id] = common }
        return table
    }()

    /// None first, then the presets by name.
    public static let surfaceChoices: [UInt8] = [noSurface] + surfaces.keys.sorted { surfaces[$0]! < surfaces[$1]! }

    /// 51 35 with a preset surface (or none) and its Armoury Crate parameters, keeping `liftoff`.
    public static func setSurface(_ id: UInt8, liftoff: String) throws -> Command {
        guard let params = surfaceParams[id] else { throw CommandError.notAllowed("surface 0x\(ReplyParser.hex([id]))") }
        return try setLiftoff(liftoff, surfaceID: id, surfaceParams: params)
    }

    /// 51 31 04 00 <index>: polling rate (Armoury Crate capture; it offers 125...1000 Hz for the Keris). Approved 2026-11-13.
    public static let pollingWriteHz: [Int: UInt8] = [125: 0x00, 250: 0x01, 500: 0x02, 1000: 0x03]

    public static func setPolling(_ hz: Int) throws -> Command {
        guard let index = pollingWriteHz[hz] else { throw CommandError.notAllowed("polling rate \(hz) Hz") }
        return try checked([0x51, 0x31, 0x04, 0x00, index])
    }

    // DPI. Source: Armoury Crate captures (mouse_writes.armoury_crate) and G-Helper AsusMouse.cs + KerisIIOrigin.cs
    // (MaxDPI 42000, 4 stages, stage count changeable, DPI colours; GPL-3.0, facts only). Approved 2026-11-13.
    public static let dpiStages = 4
    public static let dpiRange = 100...42_000
    public static let dpiStep = 50

    /// 51 31 <stage 0-based> 00 <lo> <hi> <r g b>: one stage (1-based), X and Y alike, plus its colour.
    public static func setDPI(stage: Int, _ value: DPIStage) throws -> Command {
        guard (1...dpiStages).contains(stage) else { throw CommandError.notAllowed("DPI stage \(stage)") }
        guard dpiRange.contains(value.dpi), value.dpi % dpiStep == 0 else { throw CommandError.notAllowed("\(value.dpi) DPI") }
        let v = UInt16((value.dpi - 50) / 50)
        return try checked([0x51, 0x31, UInt8(stage - 1), 0x00, UInt8(v & 0xFF), UInt8(v >> 8),
                            value.color.r, value.color.g, value.color.b])
    }

    /// 51 31 09 00 <stage>: the DPI stage in use (1-based).
    public static func setActiveStage(_ stage: Int) throws -> Command {
        guard (1...dpiStages).contains(stage) else { throw CommandError.notAllowed("DPI stage \(stage)") }
        return try checked([0x51, 0x31, 0x09, 0x00, UInt8(stage)])
    }

    /// 51 31 0a 00 <count>: stages on the DPI button.
    public static func setStageCount(_ count: Int) throws -> Command {
        guard (1...dpiStages).contains(count) else { throw CommandError.notAllowed("DPI stage count \(count)") }
        return try checked([0x51, 0x31, 0x0A, 0x00, UInt8(count)])
    }

    public static func surfaceName(_ id: UInt8?) -> String? {
        guard let id else { return nil }
        if id == noSurface { return "None (no calibration)" }
        return surfaces[id] ?? "unknown (0x\(ReplyParser.hex([id])))"
    }

    /// 51 35 00 00 <surface> <lift-off> 00 <p1 p2 p3>, or 51 35 ff 00 ff <lift-off> without a surface. Refuses an
    /// unknown surface (a custom calibration?) so it is never overwritten.
    public static func setLiftoff(_ liftoff: String, surfaceID: UInt8?, surfaceParams: [UInt8]?) throws -> Command {
        guard let code = liftoffCodes[liftoff] else { throw CommandError.notAllowed("lift-off \(liftoff)") }
        if surfaceID == noSurface { return try checked([0x51, 0x35, noSurface, 0x00, noSurface, code]) }
        guard let id = surfaceID, surfaces[id] != nil, let params = surfaceParams, params.count == 3 else {
            throw CommandError.notAllowed("surface \(surfaceName(surfaceID) ?? "not read"): lift-off not written, so the surface setting is kept")
        }
        return try checked([0x51, 0x35, 0x00, 0x00, id, code, 0x00] + params)
    }

    private static func checked(_ bytes: [UInt8]) throws -> Command {
        guard Allowlist.isAllowedWrite(bytes) else { throw CommandError.notAllowed(ReplyParser.hex(bytes)) }
        return Command(bytes, isWrite: true, echoLength: 2)
    }
}

/// One DPI stage as 51 31 <stage> writes it: the value for X and Y, and the stage colour.
public struct DPIStage: Equatable, Hashable, Sendable {
    public var dpi: Int
    public var color: RGB
    public init(dpi: Int, color: RGB) {
        self.dpi = dpi
        self.color = color
    }
}

/// Power settings written together by 51 37.
public struct PowerSetting: Equatable, Hashable, Sendable {
    public var offMinutes: Int?     // nil = never
    public var lowWarning: Int
    public init(offMinutes: Int?, lowWarning: Int) {
        self.offMinutes = offMinutes
        self.lowWarning = lowWarning
    }
}

public enum CommandError: Error, Equatable, CustomStringConvertible {
    case notAllowed(String)
    public var description: String {
        switch self { case .notAllowed(let what): return "not on the allowlist: \(what)" }
    }
}

/// Read-only queries for the Keris II Origin settings (active profile only; `50 02` switch profile is never sent).
public enum SettingsQuery: String, CaseIterable, Sendable {
    // raw values = the names in squire.py and protocol.json
    case profile, settings, dpiXY = "dpi_xy", dpiColors = "dpi_colors"
    case ledLogo = "led_logo", ledWheel = "led_wheel", ledSide = "led_side", liftoff

    public var command: Command {
        switch self {
        case .profile: return Command([0x12, 0x00])
        case .settings: return Command([0x12, 0x04, 0x00])
        case .dpiXY: return Command([0x12, 0x04, 0x02])
        case .dpiColors: return Command([0x12, 0x04, 0x03])
        case .ledLogo: return Command([0x12, 0x03, 0x00])
        case .ledWheel: return Command([0x12, 0x03, 0x01])
        case .ledSide: return Command([0x12, 0x03, 0x02])
        case .liftoff: return Command([0x12, 0x06])
        }
    }

    public static func led(_ zone: LEDZone) -> SettingsQuery {
        switch zone { case .logo: return .ledLogo; case .wheel: return .ledWheel; case .side: return .ledSide }
    }
}

public enum Allowlist {
    public static let reads: Set<[UInt8]> = Set([Command.keyboardBattery, .mouseBattery].map(\.bytes)
                                                 + SettingsQuery.allCases.map(\.command.bytes))

    public static func isAllowedWrite(_ c: [UInt8]) -> Bool {
        if c == Command.save.bytes { return true }
        if c.starts(with: [0x51, 0x35]) {
            let codes = Set(Command.liftoffCodes.values)
            if c.count == 6 { return c[2] == Command.noSurface && c[3] == 0 && c[4] == Command.noSurface && codes.contains(c[5]) }
            return c.count == 10 && c[2] == 0 && c[3] == 0 && Command.surfaces[c[4]] != nil && codes.contains(c[5]) && c[6] == 0
        }
        if c.starts(with: [0x51, 0x37]) {
            return c.count == 7 && c[2] == 0 && c[3] == 0 && Command.powerOffCodes.values.contains(c[4]) && c[5] == 0
                && Command.lowWarningLevels.contains(Int(c[6]))
        }
        if c.starts(with: [0x51, 0x31]) {
            guard c.count >= 4, c[3] == 0 else { return false }
            switch c[2] {
            case 0x06: return c.count == 5 && c[4] <= 1
            case 0x04: return c.count == 5 && Command.pollingWriteHz.values.contains(c[4])
            case 0x09, 0x0A: return c.count == 5 && (1...Command.dpiStages).contains(Int(c[4]))
            case 0x00..<UInt8(Command.dpiStages):
                let dpi = (Int(c.count > 5 ? c[4] : 0) | Int(c.count > 5 ? c[5] : 0) << 8) * 50 + 50
                return c.count == 9 && Command.dpiRange.contains(dpi) && dpi % Command.dpiStep == 0
            case 0x0B: return c.count == 6 && Command.angleTuningDegrees.contains(Int(Int16(bitPattern: UInt16(c[4]) | UInt16(c[5]) << 8)))
            default: return false   // debounce and anything else: not approved
            }
        }
        return c.count == 12 && c[0] == 0x51 && c[1] == 0x28 && LEDZone.allCases.contains { $0.index == c[2] }
            && c[3] == 0 && LEDMode.writable.contains { $0.byte == c[4] } && c[5] <= 100
            && c[9] == 0 && c[10] == 0 && c[11] == 0
    }

    public static func isAllowed(_ command: Command) -> Bool {
        command.isWrite ? isAllowedWrite(command.bytes) : reads.contains(command.bytes)
    }
}

public enum Packet {
    /// Report ID (0x00 = unnumbered) + command, zero-padded to `length`. Refuses anything not on the allowlist;
    /// a read can never carry write bytes because `isWrite` decides which list is checked.
    public static func build(reportID: UInt8?, command: Command, length: Int) throws -> [UInt8] {
        guard Allowlist.isAllowed(command) else {
            throw CommandError.notAllowed((command.isWrite ? "write " : "") + ReplyParser.hex(command.bytes))
        }
        var bytes: [UInt8] = [reportID ?? 0x00] + command.bytes
        if bytes.count < length { bytes += [UInt8](repeating: 0, count: length - bytes.count) }
        return bytes
    }
}

extension Target {
    /// The battery query of this target as an allowlisted command.
    public var command: Command { device == .keyboard ? .keyboardBattery : .mouseBattery }
}
