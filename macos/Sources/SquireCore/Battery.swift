/// Battery state of one peripheral, as reported by the device.
public struct Battery: Equatable, Sendable {
    public enum Device: String, Sendable { case keyboard, mouse }
    public enum Connection: String, Sendable { case omni, wired }
    public enum Status: String, Sendable { case ok, asleep, noResponse = "no-response", error }

    public var device: Device
    public var name: String
    public var connection: Connection
    /// nil = not reachable (asleep, no response, error).
    public var percent: Int?
    public var charging: Bool
    public var status: Status
    /// Low-battery warning level (%) configured on the device, if the reply carried a plausible one.
    public var lowWarning: Int?
    public var raw: String

    public init(device: Device, name: String, connection: Connection, percent: Int? = nil,
                charging: Bool = false, status: Status = .ok, lowWarning: Int? = nil, raw: String = "") {
        self.device = device
        self.name = name
        self.connection = connection
        self.percent = percent
        self.charging = charging
        self.status = status
        self.lowWarning = lowWarning
        self.raw = raw
    }
}
