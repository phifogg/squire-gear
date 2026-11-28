// The capabilities of a keyboard model (devices.json), like squirelib DeviceCapabilities for kind "keyboard". Every
// capability Squire's keyboard code uses must be in the model; a model without one fails to load (ModelError).

public struct KeyboardCapabilities: Sendable {
    public let model: DeviceModels.Model
    public let profile: Capabilities.KBProfile
    public let layout: Capabilities.KBLayout
    public let lighting: Capabilities.KBLighting
    public let sleep: Capabilities.KBSleep
    public let warning: Capabilities.KBWarning
    public let perKey: Capabilities.PerKey
    /// Write allowlist: confirmed prefix + the owning capability's byte check.
    public let device: DeviceCapabilities
    let parsers: [any KeyboardCapability]

    public init(model: DeviceModels.Model) throws {
        let caps = model.capabilities
        func need<T>(_ value: T?, _ name: String) throws -> T {
            guard let value else { throw ModelError("\(model.id): no capability \(name)") }
            return value
        }
        self.model = model
        lighting = try Capabilities.KBLighting(model, try need(caps.kbLighting, "kb_lighting"))
        profile = Capabilities.KBProfile(try need(caps.kbProfile, "kb_profile"), lightModes: lighting.lightModes)
        layout = Capabilities.KBLayout(try need(caps.kbLayout, "kb_layout"))
        sleep = try Capabilities.KBSleep(model, try need(caps.kbSleep, "kb_sleep"))
        warning = try Capabilities.KBWarning(model, try need(caps.kbWarning, "kb_warning"))
        perKey = try Capabilities.PerKey(model, try need(caps.perKey, "per_key"))
        parsers = [profile, layout, sleep, warning]
        device = DeviceCapabilities(model: model, items: [profile, layout, lighting, sleep, warning, perKey])
    }

    /// The keyboard in the bundled devices.json.
    public static let bundled: KeyboardCapabilities = {
        do { return try KeyboardCapabilities(model: DeviceModels.bundled.required(.keyboard)) }
        catch { fatalError("devices.json: \(error)") }
    }()

    /// Command from bytes one of these capabilities built; it must pass the whole allowlist.
    func checked(_ bytes: [UInt8], echoLength: Int = 2, reply: [UInt8]? = nil) throws -> Command {
        guard device.isAllowedWrite(bytes) else { throw CommandError.notAllowed(ReplyParser.hex(bytes)) }
        return Command(bytes, isWrite: true, echoLength: echoLength, reply: reply)
    }

    /// The change's commands, each checked against the whole allowlist.
    public func commands(_ change: Change<KeyboardSettings>, current: KeyboardSettings? = nil) throws -> [Command] {
        try change.commands(current: current).map { c in
            guard device.isAllowedWrite(c.bytes) else { throw CommandError.notAllowed(ReplyParser.hex(c.bytes)) }
            return c
        }
    }

    public func parse(_ read: String, _ p: [UInt8], into k: inout KeyboardSettings) {
        for parser in parsers { parser.parse(read, p, into: &k) }
    }
}
