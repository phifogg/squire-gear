import Foundation

// Logic of the settings window (same as linux/squire_settings.py), kept UI-free so it can be tested.

/// What can be selected in the window: the three light zones on the drawing, and three chips below it.
public enum SettingsZone: String, CaseIterable, Sendable, Identifiable {
    case wheel, logo, side, dpi, sensor, power
    public var id: String { rawValue }

    public var ledZone: LEDZone? { LEDZone(rawValue: rawValue) }

    public var title: String {
        switch self {
        case .wheel: return "Scroll wheel light"
        case .logo: return "Logo light"
        case .side: return "Side light"
        case .dpi: return "DPI stages (button on the underside)"
        case .sensor: return "Sensor"
        case .power: return "Power"
        }
    }

    /// Short title on the callout or chip.
    public var shortTitle: String {
        switch self {
        case .wheel: return "Scroll wheel"
        case .logo: return "Logo"
        case .side: return "Side"
        case .dpi: return "DPI"
        case .sensor: return "Sensor"
        case .power: return "Power"
        }
    }

    public var kind: String {
        switch self {
        case .wheel, .logo, .side: return "Lighting"
        case .dpi, .sensor: return "Performance"
        case .power: return "Battery"
        }
    }

    /// The DPI button is on the underside of the Keris II Origin: no hotspot on the top view, a chip instead.
    public static let chips: [SettingsZone] = [.dpi, .sensor, .power]
}

/// Pending edits besides the lights: power (51 37), the sensor's angle settings (51 31 06 / 0b) and lift-off (51 35).
public struct OtherEdits: Equatable, Sendable {
    public var power: PowerSetting?
    public var angleSnapping: Bool?
    public var angleTuning: Int?
    public var liftoff: String?
    public var surface: UInt8?
    public var polling: Int?
    public var dpi: [Int: DPIStage] = [:]   // 1-based stage
    public var activeStage: Int?
    public var stageCount: Int?
    public init(power: PowerSetting? = nil, angleSnapping: Bool? = nil, angleTuning: Int? = nil, liftoff: String? = nil,
                surface: UInt8? = nil, polling: Int? = nil) {
        self.power = power
        self.angleSnapping = angleSnapping
        self.angleTuning = angleTuning
        self.liftoff = liftoff
        self.surface = surface
        self.polling = polling
    }
    public var isEmpty: Bool {
        power == nil && angleSnapping == nil && angleTuning == nil && liftoff == nil && surface == nil && polling == nil
            && dpi.isEmpty && activeStage == nil && stageCount == nil
    }

    /// Fixed order, as in the Linux window: DPI stages, active stage and count, then the rest. The active stage goes
    /// after the count only when it is beyond the count the mouse has, so it stays on the button at every step.
    /// Surface before lift-off: the surface write keeps the lift-off the mouse has, then lift-off keeps the new one.
    func changes(_ s: MouseSettings) -> [SettingChange] {
        var stages: [SettingChange?] = [activeStage.map { .activeStage($0) }, stageCount.map { .stageCount($0) }]
        if let active = activeStage, active > (s.dpiStageCount ?? 0) { stages.reverse() }
        return (dpi.keys.sorted().map { SettingChange.dpi(stage: $0, dpi[$0]!) } + stages.compactMap { $0 }
                + [power.map { .power($0) }, angleSnapping.map { .angleSnapping($0) }, angleTuning.map { .angleTuning($0) },
                   polling.map { .polling($0) }, surface.map { .surface($0) }, liftoff.map { .liftoff($0) }].compactMap { $0 })
    }
}

public enum SettingsEditing {
    /// Short value under a callout or chip.
    public static func summary(_ s: MouseSettings, _ zone: SettingsZone) -> String {
        if let led = zone.ledZone {
            guard let state = s.leds[led] else { return "not read" }
            return state.mode == .off ? "Off" : "\(state.mode.title) · \(state.brightness) %"
        }
        switch zone {
        case .dpi:
            let stages = s.activeDPI
            return stages.isEmpty ? "not read" : stages.map(String.init).joined(separator: " / ") + " DPI"
        case .sensor:
            guard let hz = s.pollingHz else { return "not read" }
            return "\(hz) Hz · lift-off \(s.liftoff ?? "?")"
        case .power:
            let off = s.powerOffMinutes.map { "off after \($0) min" } ?? "never off"
            return "\(off) · warn at \(s.lowWarning.map(String.init) ?? "?") %"
        default:
            return ""
        }
    }

    /// The settings as they will be after Apply (for the drawing, the callouts and the chips).
    public static func preview(_ s: MouseSettings, pending: [LEDZone: LEDState], other: OtherEdits = .init()) -> MouseSettings {
        var p = s
        p.leds.merge(pending) { _, new in new }
        if let power = other.power {
            p.powerOffMinutes = power.offMinutes
            p.lowWarning = power.lowWarning
        }
        if let on = other.angleSnapping { p.angleSnapping = on }
        if let degrees = other.angleTuning { p.angleTuning = degrees }
        if let value = other.liftoff { p.liftoff = value }
        if let hz = other.polling { p.pollingHz = hz }
        for (stage, value) in other.dpi where stage - 1 < p.dpiXY.count && stage - 1 < p.dpiColors.count {
            p.dpiXY[stage - 1] = [value.dpi, value.dpi]
            p.dpiColors[stage - 1] = value.color
            if stage - 1 < p.dpi.count { p.dpi[stage - 1] = value.dpi }
        }
        if let stage = other.activeStage { p.dpiStage = stage }
        if let count = other.stageCount { p.dpiStageCount = count }
        if let id = other.surface {
            p.surfaceID = id
            p.surfaceParams = Command.surfaceParams[id] ?? nil
        }
        return p
    }

    /// Everything Apply will write, in a fixed order: changed light zones, then power, angle snapping, angle tuning.
    public static func changes(_ s: MouseSettings, pending: [LEDZone: LEDState], other: OtherEdits = .init()) -> [SettingChange] {
        changedZones(s, pending: pending).map { .led($0, pending[$0]!) } + other.changes(s).filter { !$0.matches(s) }
    }

    /// An edit to power or the sensor: kept pending, or dropped when it is back to what the mouse has.
    public static func edit(_ other: OtherEdits, _ s: MouseSettings, change: (inout OtherEdits) -> Void) -> OtherEdits {
        var o = other
        change(&o)
        if let power = o.power, SettingChange.power(power).matches(s) { o.power = nil }
        if let on = o.angleSnapping, SettingChange.angleSnapping(on).matches(s) { o.angleSnapping = nil }
        if let degrees = o.angleTuning, SettingChange.angleTuning(degrees).matches(s) { o.angleTuning = nil }
        if let value = o.liftoff, SettingChange.liftoff(value).matches(s) { o.liftoff = nil }
        if let id = o.surface, SettingChange.surface(id).matches(s) { o.surface = nil }
        if let hz = o.polling, SettingChange.polling(hz).matches(s) { o.polling = nil }
        for (stage, value) in o.dpi where SettingChange.dpi(stage: stage, value).matches(s) { o.dpi[stage] = nil }
        if let stage = o.activeStage, stage == s.dpiStage { o.activeStage = nil }
        if let count = o.stageCount, count == s.dpiStageCount { o.stageCount = nil }
        return o
    }

    /// A stage with separate X/Y values is shown only: the write sets both alike.
    public static func canEditDPI(_ s: MouseSettings, stage: Int) -> Bool {
        let i = stage - 1
        return i < s.dpiXY.count && i < s.dpiColors.count && s.dpiXY[i][0] == s.dpiXY[i][1]
    }

    /// Active stage or stage count; they stay consistent (a stage beyond the count raises the count, a count below
    /// the active stage lowers the active stage). Same as editDPIStages in the Linux window.
    public static func editDPIStages(_ other: OtherEdits, _ s: MouseSettings, active: Int? = nil, count: Int? = nil) -> OtherEdits {
        let p = preview(s, pending: [:], other: other)
        var newActive = active ?? p.dpiStage ?? 1
        var newCount = count ?? p.dpiStageCount ?? Command.dpiStages
        if active != nil, newActive > newCount { newCount = newActive }
        if count != nil, newActive > newCount { newActive = newCount }
        return edit(other, s) { $0.activeStage = newActive; $0.stageCount = newCount }
    }

    /// Surface can be changed only from one Squire knows (or none): an unknown one may be a calibration.
    public static func canEditSurface(_ s: MouseSettings) -> Bool {
        guard let id = s.surfaceID, Command.surfaceParams[id] != nil, let l = s.liftoff else { return false }
        return Command.liftoffCodes[l] != nil
    }

    public static func canEditPolling(_ s: MouseSettings) -> Bool {
        s.pollingHz.map { Command.pollingWriteHz[$0] != nil } ?? false
    }

    /// Lift-off can be edited only with a surface Squire knows (or none): an unknown one may be a calibration.
    public static func canEditLiftoff(_ s: MouseSettings) -> Bool {
        guard let value = s.liftoff, Command.liftoffCodes[value] != nil else { return false }
        return (try? Command.setLiftoff(value, surfaceID: s.surfaceID, surfaceParams: s.surfaceParams)) != nil
    }

    /// Power can be edited only when the warning level read from the mouse is one Squire can write back.
    public static func canEditPower(_ s: MouseSettings) -> Bool {
        s.lowWarning.map { Command.lowWarningLevels.contains($0) } ?? false
    }

    /// Zones whose pending value differs from what the mouse has, in a fixed order.
    public static func changedZones(_ s: MouseSettings, pending: [LEDZone: LEDState]) -> [LEDZone] {
        LEDZone.allCases.filter { zone in pending[zone].map { !$0.matches(s.leds[zone]) } ?? false }
    }

    /// An edit to one zone: keeps it pending, or drops it when it is back to what the mouse has.
    public static func edit(_ pending: [LEDZone: LEDState], zone: LEDZone, current: LEDState,
                            change: (inout LEDState) -> Void) -> [LEDZone: LEDState] {
        var pending = pending
        var led = pending[zone] ?? current
        change(&led)
        pending[zone] = led.matches(current) && led.mode == current.mode ? nil : led
        return pending
    }

    /// An edit to one zone, or with `linked` to every light zone ("Same effect on all zones"): the zone's state
    /// plus the change becomes pending for each target, or is dropped where it is back to what the mouse has.
    /// A linked edit without a change copies the zone to the others (the switch being turned on).
    public static func edit(_ s: MouseSettings, _ pending: [LEDZone: LEDState], zone: LEDZone, linked: Bool,
                            change: (inout LEDState) -> Void = { _ in }) -> [LEDZone: LEDState] {
        guard var led = pending[zone] ?? s.leds[zone] else { return pending }
        change(&led)
        var pending = pending
        for target in linked ? LEDZone.allCases.filter({ s.leds[$0] != nil }) : [zone] {
            let current = s.leds[target]!
            pending[target] = led.matches(current) && led.mode == current.mode ? nil : led
        }
        return pending
    }

    /// Only an effect Squire can write can be copied to the other zones.
    public static func canLink(_ s: MouseSettings, pending: [LEDZone: LEDState], zone: LEDZone) -> Bool {
        (pending[zone] ?? s.leds[zone]).map { LEDMode.writable.contains($0.mode) } ?? false
    }

    public static func applyStatus(_ s: MouseSettings, pending: [LEDZone: LEDState], other: OtherEdits = .init()) -> String {
        let all = changes(s, pending: pending, other: other)
        guard !all.isEmpty else {
            return "Showing the values read from the mouse. Nothing is sent until you click Apply."
        }
        let names = all.map(\.title).joined(separator: ", ")
        return "Not sent yet: \(names). Apply writes it to the mouse, reads it back and saves it."
    }

    /// Exactly what Apply will send, for "Show bytes".
    public static func plannedPackets(_ s: MouseSettings, pending: [LEDZone: LEDState], other: OtherEdits = .init()) -> [String] {
        let omni = s.connection != .wired
        let reportID: UInt8? = omni ? 0x03 : nil
        let length = omni ? 64 : 65
        var lines: [String] = []
        // each write sees the mouse as the earlier ones left it (HIDReader reads it again right before each write):
        // lift-off keeps a new surface, an active stage may need the new count first
        var current = s
        for change in changes(s, pending: pending, other: other) {
            defer { current = change.applied(to: current) }
            guard let command = try? change.command(current: current),
                  let bytes = try? Packet.build(reportID: reportID, command: command, length: length) else { continue }
            lines.append(ReplyParser.hex(Array(bytes.prefix(command.bytes.count + 1))) + " …")
        }
        if !lines.isEmpty, let bytes = try? Packet.build(reportID: reportID, command: .save, length: length) {
            lines.append(ReplyParser.hex(Array(bytes.prefix(3))) + " …  (save)")
        }
        return lines
    }
}

/// The real captures in protocol.json: the shared test vectors, and the data the window's demo mode shows.
public struct ProtocolCaptures: Decodable, Sendable {
    public struct Vector: Decodable, Sendable {
        public let device: String
        public let connection: String
        public let reportId: Int?
        public let reply: String
        enum CodingKeys: String, CodingKey { case device, connection, reportId = "report_id", reply }
        public var bytes: [UInt8] { reply.split(separator: " ").compactMap { UInt8($0, radix: 16) } }
    }
    public struct SettingsCapture: Decodable, Sendable {
        public let command: String
        public let reply: String
        public var bytes: [UInt8] { reply.split(separator: " ").compactMap { UInt8($0, radix: 16) } }
    }
    struct MouseSettingsSection: Decodable, Sendable { let captures: [SettingsCapture] }

    public let vectors: [Vector]
    let mouseSettings: MouseSettingsSection
    let keyboardSettings: MouseSettingsSection?
    public var settingsCaptures: [SettingsCapture] { mouseSettings.captures }
    public var keyboardCaptures: [SettingsCapture] { keyboardSettings?.captures ?? [] }
    enum CodingKeys: String, CodingKey {
        case vectors, mouseSettings = "mouse_settings", keyboardSettings = "keyboard_settings"
    }

    public static func load(_ url: URL) throws -> ProtocolCaptures {
        try JSONDecoder().decode(ProtocolCaptures.self, from: Data(contentsOf: url))
    }

    /// Batteries and mouse settings as the demo shows them (Omni captures; sends nothing).
    public func demo() -> (batteries: [Battery], settings: MouseSettings) {
        var s = MouseSettings(connection: .omni)
        var batteries: [Battery] = []
        for target in [Target.omniKeyboard, .omniMouse] {
            guard let v = vectors.first(where: { $0.device == target.device.rawValue && $0.reportId == Int(target.reportID!) })
            else { continue }
            batteries.append(ReplyParser.interpret(replies: [v.bytes], target: target))
            if target.device == .mouse { s.applyBattery(Array(v.bytes.dropFirst())) }
        }
        for capture in settingsCaptures {
            guard let query = SettingsQuery(rawValue: capture.command) else { continue }
            s.apply(query, Array(capture.bytes.dropFirst()))
        }
        return (batteries, s)
    }
}
