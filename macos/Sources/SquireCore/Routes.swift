// Route layer (same as squirelib/routes.py): how one device is reached. The Omni receiver is a route both devices
// share (report IDs 02 / 03 on one interface); each device is tried via the Omni first, then on the cable (a device
// on the cable answers ff aa on the receiver). Bluetooth has no vendor collection: Bluetooth.merge fills in a device
// without a HID value. The routes are read from devices.json (DeviceModels.bundled).

/// One way of querying one device: connection, PID, report ID, packet lengths.
public struct Target: Equatable, Sendable {
    public let device: Battery.Device
    public let name: String
    public let connection: Battery.Connection
    public let productID: UInt16
    /// nil = unnumbered report (leading 0x00 in the write, no report ID in the reply).
    public let reportID: UInt8?
    public let query: [UInt8]
    /// Candidate total write sizes (including the report-ID byte), tried in order.
    public let reportLengths: [Int]

    /// Every HID route from devices.json (DeviceModels.bundled), in file order: per device the Omni first, the cable
    /// as fallback (a device on the cable answers `ff aa` on the receiver).
    public static let all: [Target] = {
        do { return try DeviceModels.bundled.hidTargets() } catch { fatalError("devices.json: \(error)") }
    }()

    public static let keyboardTargets = all.filter { $0.device == .keyboard }
    public static let mouseTargets = all.filter { $0.device == .mouse }

    public static let omniKeyboard = route(.keyboard, .omni)
    public static let wiredKeyboard = route(.keyboard, .wired)
    public static let omniMouse = route(.mouse, .omni)
    public static let wiredMouse = route(.mouse, .wired)

    static func route(_ device: Battery.Device, _ connection: Battery.Connection) -> Target {
        guard let target = all.first(where: { $0.device == device && $0.connection == connection }) else {
            fatalError("devices.json: no \(connection.rawValue) route for the \(device.rawValue)")
        }
        return target
    }

    /// Report ID byte followed by the query, zero-padded to `length`.
    public func packet(length: Int) -> [UInt8] {
        SquireProtocol.frame(reportID: reportID, bytes: query, length: length)
    }
}
