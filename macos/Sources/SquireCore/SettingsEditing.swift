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

    /// The settings as they will be after Apply (for the drawing and the callouts).
    public static func preview(_ s: MouseSettings, pending: [LEDZone: LEDState]) -> MouseSettings {
        var p = s
        p.leds.merge(pending) { _, new in new }
        return p
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

    public static func applyStatus(_ s: MouseSettings, pending: [LEDZone: LEDState]) -> String {
        let zones = changedZones(s, pending: pending)
        guard !zones.isEmpty else {
            return "Showing the values read from the mouse. Nothing is sent until you click Apply."
        }
        let names = zones.compactMap { SettingsZone(rawValue: $0.rawValue)?.title }.joined(separator: ", ")
        return "Not sent yet: \(names). Apply writes it to the mouse, reads it back and saves it."
    }

    /// Exactly what Apply will send, for "Show bytes".
    public static func plannedPackets(_ s: MouseSettings, pending: [LEDZone: LEDState]) -> [String] {
        let omni = s.connection != .wired
        let reportID: UInt8? = omni ? 0x03 : nil
        let length = omni ? 64 : 65
        var lines: [String] = []
        for zone in changedZones(s, pending: pending) {
            guard let command = try? Command.setLED(zone, pending[zone]!),
                  let bytes = try? Packet.build(reportID: reportID, command: command, length: length) else { continue }
            lines.append(ReplyParser.hex(Array(bytes.prefix(13))) + " …")
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
