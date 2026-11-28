// Mouse power (squirelib/capabilities/power.py): 51 37 00 00 <power-off> 00 <warning %>, always both, read back from the
// battery reply 12 07.
// Source, facts only: G-Helper AsusMouse.cs GetUpdateEnergySettingsPacket() and Models/KerisIIOrigin.cs (GPL-3.0).
// Approved 2026-11-12.

extension Capabilities {
    public struct Power: MouseCapability {
        public let prefixes: [[UInt8]]
        let writePrefix: [UInt8]                // 51 37
        let read: String                        // battery (12 07)
        /// Power-off minutes (nil = never) -> byte, and back (never has no entry).
        public let powerOffCodes: [Int?: UInt8]
        public let powerOffMinutes: [UInt8: Int]
        public let warningLevels: [Int]
        let offset: Int, warningOffset: Int

        init(_ model: DeviceModels.Model, _ params: DeviceModels.Power) throws {
            writePrefix = try model.requiredPrefix(params.write)
            prefixes = model.writePrefixes(params.write)
            read = params.read
            powerOffCodes = Dictionary(uniqueKeysWithValues: params.powerOff.map { ($0.minutes, $0.value.value) })
            powerOffMinutes = Dictionary(uniqueKeysWithValues: params.powerOff.compactMap { p in p.minutes.map { (p.value.value, $0) } })
            warningLevels = params.warningLevels
            offset = params.offset
            warningOffset = model.capabilities.battery.warning
        }

        public func command(_ power: PowerSetting) throws -> [UInt8] {
            guard let code = powerOffCodes[power.offMinutes] else {
                throw CommandError.notAllowed("power-off \(power.offMinutes.map(String.init) ?? "never") min")
            }
            guard warningLevels.contains(power.lowWarning) else {
                throw CommandError.notAllowed("low-battery warning \(power.lowWarning) %")
            }
            return writePrefix + [0x00, 0x00, code, 0x00, UInt8(power.lowWarning)]
        }

        public func allows(_ c: [UInt8]) -> Bool {
            c.count == 7 && c[2] == 0 && c[3] == 0 && powerOffCodes.values.contains(c[4]) && c[5] == 0
                && warningLevels.contains(Int(c[6]))
        }

        public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
            guard read == self.read else { return }
            s.powerOffMinutes = powerOffMinutes[p[offset]]
            s.lowWarning = (1...100).contains(p[warningOffset]) ? Int(p[warningOffset]) : nil
        }
    }
}

extension Capabilities.Power {
    /// Power-off and warning together, read back from the battery reply; only counts while the mouse answers.
    public func change(_ power: PowerSetting) -> Change<MouseSettings> {
        Change(reads: [read], build: { _ in [writeCommand(try command(power))] },
               matches: { s, _ in s.lowWarning == power.lowWarning && s.powerOffMinutes == power.offMinutes && s.status == .ok },
               apply: { $0.powerOffMinutes = power.offMinutes; $0.lowWarning = power.lowWarning })
    }
}
