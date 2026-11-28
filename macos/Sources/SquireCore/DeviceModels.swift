import Foundation

// devices.json (repo root): the device models, shared with squire.py (squirelib/registry.py checks the file in
// Python). Decoding is strict on types and formats: a hex value that is not hex, a number given as a string or a
// missing required field fails, so the Swift tests catch a broken file. Unknown keys are ignored here; the Python
// checker refuses them for capabilities. For now the tests hold the Swift tables to this file; the app does not
// read it at runtime yet.

/// A byte written as two hex digits ("0f").
public struct HexByte: Codable, Hashable, Sendable {
    public let value: UInt8
    public init(_ value: UInt8) { self.value = value }
    public init(from decoder: Decoder) throws {
        let text = try decoder.singleValueContainer().decode(String.self)
        guard text.count == 2, text == text.lowercased(), let value = UInt8(text, radix: 16) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath,
                                                    debugDescription: "\(text) is not two hex digits"))
        }
        self.value = value
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(ReplyParser.hex([value]))
    }
}

/// A USB id written as four hex digits ("1b04").
public struct HexID: Codable, Hashable, Sendable {
    public let value: UInt16
    public init(from decoder: Decoder) throws {
        let text = try decoder.singleValueContainer().decode(String.self)
        guard text.count == 4, text == text.lowercased(), let value = UInt16(text, radix: 16) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath,
                                                    debugDescription: "\(text) is not four hex digits"))
        }
        self.value = value
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(String(format: "%04x", value))
    }
}

/// Bytes written like protocol.json ("12 04 00").
public struct HexBytes: Codable, Hashable, Sendable {
    public let bytes: [UInt8]
    public init(from decoder: Decoder) throws {
        let text = try decoder.singleValueContainer().decode(String.self)
        let parts = text.split(separator: " ", omittingEmptySubsequences: false)
        let bytes = parts.compactMap { $0.count == 2 && $0 == $0.lowercased() ? UInt8($0, radix: 16) : nil }
        guard !parts.isEmpty, bytes.count == parts.count else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath,
                                                    debugDescription: "\(text) is not hex bytes"))
        }
        self.bytes = bytes
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(ReplyParser.hex(bytes))
    }
}

public struct DeviceModels: Codable, Sendable {
    public let schema: Int
    public let vendorID: HexID
    public let receivers: [Receiver]
    public let devices: [Model]

    enum CodingKeys: String, CodingKey { case schema, vendorID = "vendor_id", receivers, devices }

    public static func decode(_ data: Data) throws -> DeviceModels {
        let models = try JSONDecoder().decode(DeviceModels.self, from: data)
        guard models.schema == 1 else {
            throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "schema \(models.schema)"))
        }
        return models
    }

    public func model(_ kind: Battery.Device) -> Model? {
        let found = devices.filter { $0.kind == kind.rawValue }
        return found.count == 1 ? found[0] : nil
    }

    public func receiver(_ id: String) -> Receiver? { receivers.first { $0.id == id } }

    public struct Receiver: Codable, Sendable {
        public let id: String
        public let name: String
        public let pid: HexID
        public let interface: Int
        public let reportLengths: [Int]
        enum CodingKeys: String, CodingKey { case id, name, pid, interface, reportLengths = "report_lengths" }
    }

    public struct Route: Codable, Sendable {
        public let connection: String
        public let receiver: String?          // Omni routes: the receiver's pid / interface / lengths
        public let reportID: HexByte?
        public let pid: HexID?                // wired and Bluetooth routes
        public let interface: Int?
        public let reportLengths: [Int]?
        public let gattName: String?          // Bluetooth: part of the GAP name
        enum CodingKeys: String, CodingKey {
            case connection, receiver, reportID = "report_id", pid, interface, reportLengths = "report_lengths"
            case gattName = "gatt_name"
        }
    }

    public struct Command: Codable, Sendable {
        public let name: String
        public let write: Bool?
        public let bytes: HexBytes?           // reads: exact bytes
        public let prefixes: [HexBytes]?      // writes: allowed prefixes
        public let reply: HexBytes?           // acknowledgement when the command is not echoed
        public let status: String
        public let approved: String?
        public var isWrite: Bool { write ?? false }
        public var isConfirmed: Bool { status == "confirmed" }
    }

    public struct Model: Codable, Sendable {
        public let id: String
        public let name: String
        public let kind: String
        public let routes: [Route]
        public let commands: [Command]
        public let capabilities: Capabilities

        public func route(_ connection: Battery.Connection) -> Route? {
            routes.first { $0.connection == connection.rawValue }
        }
        public func command(_ name: String) -> Command? { commands.first { $0.name == name } }
        /// Bytes of the confirmed reads (the read allowlist).
        public var confirmedReads: Set<[UInt8]> {
            Set(commands.filter { !$0.isWrite && $0.isConfirmed }.compactMap { $0.bytes?.bytes })
        }
        public var confirmedWritePrefixes: [[UInt8]] {
            commands.filter { $0.isWrite && $0.isConfirmed }.flatMap { $0.prefixes ?? [] }.map(\.bytes)
        }
    }

    public struct Capabilities: Codable, Sendable {
        public let battery: BatteryLayout
        public let ledZones: LEDZones?
        public let dpi: DPI?
        public let polling: Polling?
        public let sensor: Sensor?
        public let surface: Surface?
        public let power: Power?
        public let profile: Profile?
        public let kbProfile: KBProfile?
        public let kbLayout: KBLayout?
        public let kbLighting: KBLighting?
        public let kbSleep: KBSleep?
        public let kbWarning: KBWarning?
        public let perKey: PerKey?
        enum CodingKeys: String, CodingKey {
            case battery, ledZones = "led_zones", dpi, polling, sensor, surface, power, profile, kbProfile = "kb_profile"
            case kbLayout = "kb_layout", kbLighting = "kb_lighting", kbSleep = "kb_sleep", kbWarning = "kb_warning"
            case perKey = "per_key"
        }
    }

    public struct Named: Codable, Sendable {
        public let name: String
        public let value: HexByte
    }

    public struct Range: Codable, Sendable {
        public let min: Int
        public let max: Int
    }

    public struct PowerOff: Codable, Sendable {
        public let minutes: Int?              // nil = never
        public let value: HexByte
    }

    public struct BatteryLayout: Codable, Sendable {
        public let read: String
        public let percent: [Int]
        public let charging: Int
        public let warning: Int
        public let warningMin: Int
        enum CodingKeys: String, CodingKey { case read, percent, charging, warning, warningMin = "warning_min" }
    }

    public struct LEDZones: Codable, Sendable {
        public struct Zone: Codable, Sendable {
            public let name: String
            public let index: HexByte
            public let read: String
        }
        public struct Mode: Codable, Sendable {
            public let name: String
            public let value: HexByte
            public let writable: Bool
        }
        public let write: String
        public let save: String
        public let zones: [Zone]
        public let modes: [Mode]
        public let offsets: [String: Int]
    }

    public struct DPI: Codable, Sendable {
        public let write: String
        public let stages: Int
        public let min: Int
        public let max: Int
        public let step: Int
        public let offsets: [String: Int]
    }

    public struct Polling: Codable, Sendable {
        public struct Rate: Codable, Sendable {
            public let hz: Int
            public let value: HexByte
        }
        public let write: String
        public let readRatesHz: [Int]
        public let rates: [Rate]
        public let offsets: [String: Int]
        enum CodingKeys: String, CodingKey { case write, readRatesHz = "read_rates_hz", rates, offsets }
    }

    public struct Sensor: Codable, Sendable {
        public struct Tuning: Codable, Sendable {
            public let min: Int
            public let max: Int
            public let step: Int
        }
        public let angleTuning: Tuning
        public let offsets: [String: Int]
        enum CodingKeys: String, CodingKey { case angleTuning = "angle_tuning", offsets }
    }

    public struct Surface: Codable, Sendable {
        public struct Preset: Codable, Sendable {
            public let id: HexByte
            public let name: String
            public let params: HexBytes
        }
        public let write: String
        public let read: String
        public let liftoff: [Named]
        public let noSurface: HexByte
        public let surfaces: [Preset]
        public let offsets: [String: Int]
        enum CodingKeys: String, CodingKey { case write, read, liftoff, noSurface = "no_surface", surfaces, offsets }
    }

    public struct Power: Codable, Sendable {
        public let write: String
        public let read: String
        public let offset: Int
        public let powerOff: [PowerOff]
        public let warningLevels: [Int]
        enum CodingKeys: String, CodingKey {
            case write, read, offset, powerOff = "power_off", warningLevels = "warning_levels"
        }
    }

    /// Firmware offset per connection in the profile read (3 bytes build / minor / major).
    public struct FirmwareOffsets: Codable, Sendable {
        public let omni: Int?
        public let wired: Int?
    }

    /// The profile read (12 00): active profile (`profileBase` 0 = counted from 0), firmware, receiver firmware.
    public struct Profile: Codable, Sendable {
        public let read: String
        public let profile: Int
        public let profileBase: Int
        public let profiles: Int?
        public let firmware: FirmwareOffsets
        public let receiverFirmware: Int
        enum CodingKeys: String, CodingKey {
            case read, profile, profileBase = "profile_base", profiles, firmware, receiverFirmware = "receiver_firmware"
        }
    }

    public struct KBProfile: Codable, Sendable {
        public let read: String
        public let profiles: Int
        public let effect: Int
        public let profile: Int
        public let profileBase: Int
        public let firmware: FirmwareOffsets
        public let receiverFirmware: Int
        enum CodingKeys: String, CodingKey {
            case read, profiles, effect, profile, profileBase = "profile_base", firmware
            case receiverFirmware = "receiver_firmware"
        }
    }

    public struct KBLayout: Codable, Sendable {
        public struct Layout: Codable, Sendable {
            public let id: Int
            public let name: String?
            public let iso: Bool
        }
        public let read: String
        public let layout: Int
        public let layouts: [Layout]
    }

    public struct KBLighting: Codable, Sendable {
        public struct Effect: Codable, Sendable {
            public let name: String
            public let id: HexByte
            public let speeds: [Named]?
            public let writable: Bool?
            public var isWritable: Bool { writable ?? true }
        }
        /// [position %, "rrggbb"]
        public struct Stop: Codable, Sendable {
            public let position: Int
            public let rgb: String
            public init(from decoder: Decoder) throws {
                var c = try decoder.unkeyedContainer()
                position = try c.decode(Int.self)
                rgb = try c.decode(String.self)
                guard c.isAtEnd, rgb.count == 6, UInt32(rgb, radix: 16) != nil else {
                    throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath,
                                                            debugDescription: "not [position, rrggbb]"))
                }
            }
            public func encode(to encoder: Encoder) throws {
                var c = encoder.unkeyedContainer()
                try c.encode(position)
                try c.encode(rgb)
            }
        }
        public struct Gradient: Codable, Sendable {
            public let name: String
            public let stops: [Stop]
        }
        public struct Directions: Codable, Sendable {
            public let effect: String
            public let values: [Named]
        }
        public let write: String
        public let save: String
        public let effects: [Effect]
        public let offBrightness: Int
        public let colourModes: [Named]
        public let gradientStops: Range
        public let gradients: [Gradient]
        public let directions: [Directions]
        public let thickness: [Named]
        public let quicksandScale: [String]
        public let quicksandModes: [Named]
        enum CodingKeys: String, CodingKey {
            case write, save, effects, offBrightness = "off_brightness", colourModes = "colour_modes"
            case gradientStops = "gradient_stops", gradients, directions, thickness
            case quicksandScale = "quicksand_scale", quicksandModes = "quicksand_modes"
        }
    }

    public struct KBSleep: Codable, Sendable {
        public let write: String
        public let read: String
        public let offset: Int
        public let powerOff: [PowerOff]
        enum CodingKeys: String, CodingKey { case write, read, offset, powerOff = "power_off" }
    }

    public struct KBWarning: Codable, Sendable {
        public let write: String
        public let read: String
        public let offset: Int
        public let levels: [Int]
    }

    public struct PerKey: Codable, Sendable {
        public let write: String
        public let perPacket: Int
        public let columns: Int
        public let rows: Range
        public let layouts: [String: [[HexByte]]]
        enum CodingKeys: String, CodingKey { case write, perPacket = "per_packet", columns, rows, layouts }
    }
}
