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

/// Pending edits besides the lights: power (51 37) and the sensor's angle settings (51 31 06 / 0b).
public struct OtherEdits: Equatable, Sendable {
    public var power: PowerSetting?
    public var angleSnapping: Bool?
    public var angleTuning: Int?
    public init(power: PowerSetting? = nil, angleSnapping: Bool? = nil, angleTuning: Int? = nil) {
        self.power = power
        self.angleSnapping = angleSnapping
        self.angleTuning = angleTuning
    }
    public var isEmpty: Bool { power == nil && angleSnapping == nil && angleTuning == nil }

    var changes: [SettingChange] {
        [power.map { .power($0) }, angleSnapping.map { .angleSnapping($0) }, angleTuning.map { .angleTuning($0) }]
            .compactMap { $0 }
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
        return p
    }

    /// Everything Apply will write, in a fixed order: changed light zones, then power, angle snapping, angle tuning.
    public static func changes(_ s: MouseSettings, pending: [LEDZone: LEDState], other: OtherEdits = .init()) -> [SettingChange] {
        changedZones(s, pending: pending).map { .led($0, pending[$0]!) } + other.changes.filter { !$0.matches(s) }
    }

    /// An edit to power or the sensor: kept pending, or dropped when it is back to what the mouse has.
    public static func edit(_ other: OtherEdits, _ s: MouseSettings, change: (inout OtherEdits) -> Void) -> OtherEdits {
        var o = other
        change(&o)
        if let power = o.power, SettingChange.power(power).matches(s) { o.power = nil }
        if let on = o.angleSnapping, SettingChange.angleSnapping(on).matches(s) { o.angleSnapping = nil }
        if let degrees = o.angleTuning, SettingChange.angleTuning(degrees).matches(s) { o.angleTuning = nil }
        return o
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
        for change in changes(s, pending: pending, other: other) {
            guard let command = try? change.command(),
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
    public var settingsCaptures: [SettingsCapture] { mouseSettings.captures }
    enum CodingKeys: String, CodingKey { case vectors, mouseSettings = "mouse_settings" }

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
