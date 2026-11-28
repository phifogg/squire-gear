// The capabilities of a mouse model (devices.json), like squirelib DeviceCapabilities for kind "mouse". Every
// capability Squire's mouse code uses must be in the model; a model without one fails to load (ModelError).

public struct MouseCapabilities: Sendable {
    public let model: DeviceModels.Model
    public let profile: Capabilities.Profile
    public let ledZones: Capabilities.LEDZones
    public let dpi: Capabilities.DPI
    public let polling: Capabilities.Polling
    public let sensor: Capabilities.Sensor
    public let surface: Capabilities.Surface
    public let power: Capabilities.Power
    /// Write allowlist: confirmed prefix + the owning capability's byte check.
    public let device: DeviceCapabilities
    /// Parsers in devices.json order.
    let parsers: [any MouseCapability]

    public init(model: DeviceModels.Model) throws {
        let caps = model.capabilities
        func need<T>(_ value: T?, _ name: String) throws -> T {
            guard let value else { throw ModelError("\(model.id): no capability \(name)") }
            return value
        }
        self.model = model
        profile = Capabilities.Profile(try need(caps.profile, "profile"))
        ledZones = try Capabilities.LEDZones(model, try need(caps.ledZones, "led_zones"))
        dpi = try Capabilities.DPI(model, try need(caps.dpi, "dpi"))
        polling = try Capabilities.Polling(model, try need(caps.polling, "polling"))
        sensor = try Capabilities.Sensor(model, try need(caps.sensor, "sensor"))
        surface = try Capabilities.Surface(model, try need(caps.surface, "surface"))
        power = try Capabilities.Power(model, try need(caps.power, "power"))
        parsers = [profile, ledZones, dpi, polling, sensor, surface, power]
        device = DeviceCapabilities(model: model, items: parsers)
    }

    /// The mouse in the bundled devices.json.
    public static let bundled: MouseCapabilities = {
        do { return try MouseCapabilities(model: DeviceModels.bundled.required(.mouse)) }
        catch { fatalError("devices.json: \(error)") }
    }()

    /// A command one of these capabilities built: it must pass the whole allowlist (confirmed prefix included).
    public func checked(_ bytes: [UInt8]) throws -> Command {
        guard device.isAllowedWrite(bytes) else { throw CommandError.notAllowed(ReplyParser.hex(bytes)) }
        return Command(bytes, isWrite: true, echoLength: 2)
    }

    /// Fill the settings from one read reply (payload from the echo on); `read` = the devices.json read name.
    public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
        for parser in parsers { parser.parse(read, p, into: &s) }
    }
}
