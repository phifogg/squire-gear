// Bluetooth LE facts (confirmed on macOS, 2026-11-26, both devices paired directly, no Omni):
//
//   keyboard  0b05:1b08 "ROG FALCHION RX LOW PROFILE"   mouse  0b05:1c0f "ROG KERIS II ORIGIN"
//
// The HID-over-GATT report descriptors carry only the normal input collections (pages 01/07/08/09/0c), no
// vendor page, so the 12 01 / 12 07 queries have no way in. The GATT table has the standard Battery Service
// 180F (2A19 Battery Level, read + notify, one byte 0..100), Device Information 180A, and a vendor service
// FFF0 (FFF1 write, FFF2 + FFF4 read/notify). Squire reads 2A19 only; FFF0 is never touched (bytes unknown).
// Over Bluetooth there is no charging flag and no low-battery warning level.

public enum Bluetooth {
    public static let falchionRXLowProfilePID: UInt16 = 0x1B08
    public static let kerisIIOriginPID: UInt16 = 0x1C0F
    public static let productIDs: [UInt16: Battery.Device] = [
        falchionRXLowProfilePID: .keyboard,
        kerisIIOriginPID: .mouse,
    ]

    /// GATT UUIDs (16-bit, as strings for CBUUID). Only the Battery Service is read.
    public static let batteryService = "180F"
    public static let batteryLevel = "2A19"

    /// Device for an advertised / GAP name ("ROG FALCHION RX LOW PROFILE", "ROG KERIS II ORIGIN").
    public static func device(forName name: String?) -> Battery.Device? {
        let upper = (name ?? "").uppercased()
        if upper.contains("FALCHION RX LOW PROFILE") { return .keyboard }
        if upper.contains("KERIS II ORIGIN") { return .mouse }
        return nil
    }

    /// Battery from a 2A19 value. Empty or > 100 = error, 0 = asleep (same rule as the HID replies).
    public static func battery(_ device: Battery.Device, level: [UInt8]) -> Battery {
        var battery = Battery(device: device, name: Target.name(of: device), connection: .bluetooth,
                              raw: ReplyParser.hex(level))
        guard let value = level.first, value <= 100 else {
            battery.status = .error
            return battery
        }
        if value == 0 {
            battery.status = .asleep
        } else {
            battery.percent = Int(value)
        }
        return battery
    }

    /// HID wins when it has a value (Omni or cable); otherwise the Bluetooth reading, if any; otherwise HID's status.
    public static func merge(hid: [Battery], bluetooth: [Battery]) -> [Battery] {
        [Battery.Device.keyboard, .mouse].compactMap { device in
            let wired = hid.first { $0.device == device }
            if wired?.percent != nil { return wired }
            if let ble = bluetooth.first(where: { $0.device == device && $0.percent != nil }) { return ble }
            return wired ?? bluetooth.first { $0.device == device }
        }
    }
}

extension Battery.Connection {
    /// Shown in the popover and the settings window.
    public var label: String {
        switch self {
        case .omni: return "Omni receiver"
        case .wired: return "Wired"
        case .bluetooth: return "Bluetooth"
        }
    }
}

extension Target {
    public static func name(of device: Battery.Device) -> String {
        (device == .keyboard ? omniKeyboard : omniMouse).name
    }
}
