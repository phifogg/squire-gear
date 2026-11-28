// Command allowlist, same as squire.py (ALLOWED_READS, is_allowed_write, packet), per device.
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
    /// The reply that acknowledges a command that is not echoed (per-key colours: c0 81 <n> 00 -> c0 82 <n> 00).
    public let reply: [UInt8]?

    init(_ bytes: [UInt8], isWrite: Bool = false, echoLength: Int? = nil, reply: [UInt8]? = nil) {
        self.bytes = bytes
        self.isWrite = isWrite
        self.echoLength = echoLength ?? bytes.count
        self.reply = reply
    }

    /// What the reply must start with: the acknowledgement, else the echoed command.
    public var expectedReply: [UInt8] { reply ?? Array(bytes.prefix(echoLength)) }

    public static let keyboardBattery = Command([0x12, 0x01])
    public static let mouseBattery = Command([0x12, 0x07])
    /// 50 03: stores the current settings in the mouse's flash. Only after an explicit Apply.
    public static let save = Command([0x50, 0x03], isWrite: true, echoLength: 2)

    // Mouse writes: built by the capabilities (Capabilities/, parameters from devices.json); these names stay for the
    // window, HIDReader and the tests. Every builder's bytes pass the mouse's whole allowlist.
    static var mouse: MouseCapabilities { .bundled }

    /// 51 28 zone 00 mode brightness r g b 00 00 00: set one LED zone (Capabilities.LEDZones).
    public static func setLED(_ zone: LEDZone, _ led: LEDState) throws -> Command {
        try mouse.checked(mouse.ledZones.command(zone, led))
    }

    /// Power-off minutes (nil = never) -> byte, as in the 12 07 reply [5].
    public static var powerOffCodes: [Int?: UInt8] { mouse.power.powerOffCodes }
    public static var lowWarningLevels: [Int] { mouse.power.warningLevels }
    public static var angleTuningDegrees: [Int] { mouse.sensor.tuningDegrees }

    /// 51 37 00 00 <power-off> 00 <warning %>: auto power-off and low-battery warning, always both.
    public static func setPower(_ power: PowerSetting) throws -> Command { try mouse.checked(mouse.power.command(power)) }

    /// 51 31 06 00 <0/1>
    public static func setAngleSnapping(_ on: Bool) throws -> Command { try mouse.checked(mouse.sensor.snappingCommand(on)) }

    /// 51 31 0b 00 <lo> <hi>: degrees, signed LE16.
    public static func setAngleTuning(_ degrees: Int) throws -> Command {
        try mouse.checked(mouse.sensor.tuningCommand(degrees))
    }

    public static var liftoffCodes: [String: UInt8] { mouse.surface.liftoffCodes }
    public static var noSurface: UInt8 { mouse.surface.noSurface }
    /// Armoury Crate's preset surfaces (id -> name), same as squire.SURFACES.
    public static var surfaces: [UInt8: String] { mouse.surface.surfaces }
    /// Parameters Armoury Crate writes with each preset; nil for "no surface".
    public static var surfaceParams: [UInt8: [UInt8]?] { mouse.surface.surfaceParams }
    /// None first, then the presets by name.
    public static var surfaceChoices: [UInt8] { mouse.surface.choices }

    /// 51 35 with a preset surface (or none) and its Armoury Crate parameters, keeping `liftoff`.
    public static func setSurface(_ id: UInt8, liftoff: String) throws -> Command {
        try mouse.checked(mouse.surface.surfaceCommand(id, liftoff: liftoff))
    }

    /// 51 31 04 00 <index>: polling rate.
    public static var pollingWriteHz: [Int: UInt8] { mouse.polling.writeHz }

    public static func setPolling(_ hz: Int) throws -> Command { try mouse.checked(mouse.polling.command(hz)) }

    public static var dpiStages: Int { mouse.dpi.stages }
    public static var dpiRange: ClosedRange<Int> { mouse.dpi.range }
    public static var dpiStep: Int { mouse.dpi.step }

    /// 51 31 <stage 0-based> 00 <lo> <hi> <r g b>: one stage (1-based), X and Y alike, plus its colour.
    public static func setDPI(stage: Int, _ value: DPIStage) throws -> Command {
        try mouse.checked(mouse.dpi.command(stage: stage, value))
    }

    /// 51 31 09 00 <stage>: the DPI stage in use (1-based).
    public static func setActiveStage(_ stage: Int) throws -> Command {
        try mouse.checked(mouse.dpi.activeStageCommand(stage))
    }

    /// 51 31 0a 00 <count>: stages on the DPI button.
    public static func setStageCount(_ count: Int) throws -> Command {
        try mouse.checked(mouse.dpi.stageCountCommand(count))
    }

    public static func surfaceName(_ id: UInt8?) -> String? { mouse.surface.name(id) }

    /// 51 35 00 00 <surface> <lift-off> 00 <p1 p2 p3>, or 51 35 ff 00 ff <lift-off> without a surface. Refuses an
    /// unknown surface (a custom calibration?) so it is never overwritten.
    public static func setLiftoff(_ liftoff: String, surfaceID: UInt8?, surfaceParams: [UInt8]?) throws -> Command {
        try mouse.checked(mouse.surface.liftoffCommand(liftoff, surfaceID: surfaceID, surfaceParams: surfaceParams))
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

    /// The read's bytes from devices.json (only confirmed reads pass Packet.build).
    public var command: Command {
        guard let bytes = MouseCapabilities.bundled.model.command(rawValue)?.bytes?.bytes else {
            fatalError("devices.json: no mouse read \(rawValue)")
        }
        return Command(bytes)
    }

    public static func led(_ zone: LEDZone) -> SettingsQuery {
        switch zone { case .logo: return .ledLogo; case .wheel: return .ledWheel; case .side: return .ledSide }
    }
}

/// Per device, like squire.py (ALLOWED_READS, is_allowed_write(command, device), REPORT_IDS): the keyboard never
/// gets a mouse command and the other way round, since the same bytes can mean something else on the other device.
public enum Allowlist {
    /// Confirmed reads per device, from devices.json (status "confirmed"; anything else is never sent).
    public static let mouseReads: Set<[UInt8]> = DeviceModels.bundled.required(.mouse).confirmedReads
    public static let keyboardReads: Set<[UInt8]> = DeviceModels.bundled.required(.keyboard).confirmedReads
    /// All reads of both devices (for checks over both).
    public static let reads: Set<[UInt8]> = mouseReads.union(keyboardReads)
    /// Report ID of each device on the Omni receiver (devices.json omni routes); wired, both are unnumbered (nil).
    public static let reportIDs: [Battery.Device: UInt8] = Dictionary(uniqueKeysWithValues: Target.all.compactMap { t in
        t.connection == .omni ? t.reportID.map { (t.device, $0) } : nil
    })
    /// Confirmed write prefixes per device, from devices.json. A write must start with one of them AND pass the
    /// byte check of its device below (squire.is_allowed_write: confirmed prefix + the capability's allows()).
    public static let writePrefixes: [Battery.Device: [[UInt8]]] = [
        .mouse: DeviceModels.bundled.required(.mouse).confirmedWritePrefixes,
        .keyboard: DeviceModels.bundled.required(.keyboard).confirmedWritePrefixes,
    ]

    public static func reads(for device: Battery.Device) -> Set<[UInt8]> {
        device == .mouse ? mouseReads : keyboardReads
    }

    public static func isAllowedWrite(_ c: [UInt8], for device: Battery.Device) -> Bool {
        isAllowedWrite(c, for: device, prefixes: writePrefixes[device, default: []])
    }

    /// The gate with explicit prefixes (tests: a model whose command is not confirmed refuses it).
    static func isAllowedWrite(_ c: [UInt8], for device: Battery.Device, prefixes: [[UInt8]]) -> Bool {
        switch device {
        case .mouse: return MouseCapabilities.bundled.device.isAllowedWrite(c, confirmed: prefixes)
        case .keyboard: return KeyboardCapabilities.bundled.device.isAllowedWrite(c, confirmed: prefixes)
        }
    }

    public static func isAllowed(_ command: Command, for device: Battery.Device) -> Bool {
        command.isWrite ? isAllowedWrite(command.bytes, for: device) : reads(for: device).contains(command.bytes)
    }
}

public enum Packet {
    /// Report ID (0x00 = unnumbered) + command, zero-padded to `length`. Refuses anything not on the allowlist;
    /// a read can never carry write bytes because `isWrite` decides which list is checked. The list is the
    /// device's own, and the report ID must be that device's on the Omni (or nil, wired).
    public static func build(device: Battery.Device, reportID: UInt8?, command: Command, length: Int) throws -> [UInt8] {
        if let reportID, reportID != Allowlist.reportIDs[device] {
            throw CommandError.notAllowed("report ID \(reportID) for the \(device.rawValue)")
        }
        guard Allowlist.isAllowed(command, for: device) else {
            throw CommandError.notAllowed((command.isWrite ? "write " : "") + ReplyParser.hex(command.bytes)
                                          + " for the \(device.rawValue)")
        }
        return SquireProtocol.frame(reportID: reportID, bytes: command.bytes, length: length)
    }
}

extension Target {
    /// The battery query of this target as an allowlisted command.
    public var command: Command { device == .keyboard ? .keyboardBattery : .mouseBattery }
}
