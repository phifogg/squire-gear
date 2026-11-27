// Route layer (same as squirelib/routes.py): how one device is reached. The Omni receiver is a route both devices
// share (report IDs 02 / 03 on one interface); each device is tried via the Omni first, then on the cable (a device
// on the cable answers ff aa on the receiver). Bluetooth has no vendor collection: Bluetooth.merge fills in a device
// without a HID value. DeviceModelTests checks every route against devices.json.

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

    public static let omniMouse = Target(
        device: .mouse, name: "ROG Keris II Origin", connection: .omni,
        productID: SquireProtocol.omniReceiverPID, reportID: 0x03,
        query: [0x12, 0x07], reportLengths: [64])

    /// Wired report size 65 confirmed 2026-11-10; 64 kept as a harmless fallback.
    public static let wiredMouse = Target(
        device: .mouse, name: "ROG Keris II Origin", connection: .wired,
        productID: SquireProtocol.kerisIIOriginPID, reportID: nil,
        query: [0x12, 0x07], reportLengths: [65, 64])

    public static let mouseTargets = [omniMouse, wiredMouse]

    public static let omniKeyboard = Target(
        device: .keyboard, name: "ROG Falchion RX LP", connection: .omni,
        productID: SquireProtocol.omniReceiverPID, reportID: 0x02,
        query: [0x12, 0x01], reportLengths: [64])

    /// Wired keyboard confirmed 2026-11-10: 65-byte write, reply without report ID.
    public static let wiredKeyboard = Target(
        device: .keyboard, name: "ROG Falchion RX LP", connection: .wired,
        productID: SquireProtocol.falchionRXLowProfilePID, reportID: nil,
        query: [0x12, 0x01], reportLengths: [65])

    public static let keyboardTargets = [omniKeyboard, wiredKeyboard]
    /// Query order: Omni first, wired as fallback (a device on the cable answers `ff aa` on the receiver).
    public static let all = keyboardTargets + mouseTargets

    /// Report ID byte followed by the query, zero-padded to `length`.
    public func packet(length: Int) -> [UInt8] {
        SquireProtocol.frame(reportID: reportID, bytes: query, length: length)
    }
}
