// Mouse LED zones (squirelib/capabilities/led_zones.py): 51 28 <zone> 00 <mode> <brightness %> <r g b> 00 00 00, read
// with 12 03 <zone>, saved with 50 03.
// Sources, facts only: libratbag asus_set_led()/asus_save_profile() (MIT) and G-Helper GetUpdateLightingModePacket()/
// GetSaveProfilePacket() (GPL-3.0). No rainbow/comet writes: they need direction/speed bytes (devices.json writable
// false). No speed byte: the Keris stores [11] but ignores it (tested 2026-11-10); bytes [9..11] stay 00.

extension Capabilities {
    public struct LEDZones: MouseCapability {
        public let prefixes: [[UInt8]]
        public let writePrefix: [UInt8]                 // 51 28
        public let save: [UInt8]                        // 50 03: stores the settings in flash
        public let zoneIndex: [String: UInt8]           // logo -> 00
        public let zoneReads: [String: String]          // led_logo -> logo
        /// Effects Squire may write, in devices.json order.
        public let writable: [LEDMode]
        let modeOffset: Int, brightnessOffset: Int, colorOffset: Int

        init(_ model: DeviceModels.Model, _ params: DeviceModels.LEDZones) throws {
            writePrefix = try model.requiredPrefix(params.write)
            save = try model.requiredPrefix(params.save)
            prefixes = model.writePrefixes(params.write) + model.writePrefixes(params.save)
            zoneIndex = Dictionary(uniqueKeysWithValues: params.zones.map { ($0.name, $0.index.value) })
            zoneReads = Dictionary(uniqueKeysWithValues: params.zones.map { ($0.read, $0.name) })
            writable = params.modes.filter(\.writable).map { LEDMode(byte: $0.value.value) }
            modeOffset = try model.offset(params.offsets, "mode", "led_zones")
            brightnessOffset = try model.offset(params.offsets, "brightness", "led_zones")
            colorOffset = try model.offset(params.offsets, "color", "led_zones")
        }

        /// One zone.
        public func command(_ zone: LEDZone, _ led: LEDState) throws -> [UInt8] {
            guard let index = zoneIndex[zone.rawValue] else { throw CommandError.notAllowed("LED zone \(zone.rawValue)") }
            guard writable.contains(led.mode) else { throw CommandError.notAllowed("LED effect \(led.mode.name)") }
            guard (0...100).contains(led.brightness) else { throw CommandError.notAllowed("brightness \(led.brightness)") }
            return writePrefix + [index, 0x00, led.mode.byte, UInt8(led.brightness), led.color.r, led.color.g, led.color.b,
                                  0x00, 0x00, 0x00]
        }

        public func allows(_ c: [UInt8]) -> Bool {
            if c == save { return true }
            return c.count == 12 && c.starts(with: writePrefix) && zoneIndex.values.contains(c[2]) && c[3] == 0
                && writable.contains { $0.byte == c[4] } && c[5] <= 100 && c[9] == 0 && c[10] == 0 && c[11] == 0
        }

        public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
            guard let name = zoneReads[read], let zone = LEDZone(rawValue: name) else { return }
            let c = colorOffset
            s.leds[zone] = LEDState(mode: LEDMode(byte: p[modeOffset]), brightness: Int(p[brightnessOffset]),
                                    color: RGB(p[c], p[c + 1], p[c + 2]))
        }
    }
}
