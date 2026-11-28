// Mouse sensor (squirelib/capabilities/sensor.py): angle snapping 51 31 06 00 <0/1>, angle tuning 51 31 0b 00 <lo> <hi>
// (signed LE16), read back from 12 04 00; that reply also carries the debounce index (read only: not approved).
// Source, facts only: G-Helper AsusMouse.cs + Models/KerisIIOrigin.cs (GPL-3.0); angle snapping also libratbag
// asus_set_angle_snapping(). Approved 2026-11-12.

extension Capabilities {
    public struct Sensor: MouseCapability {
        public let prefixes: [[UInt8]]
        let snappingPrefix: [UInt8]             // 51 31 06
        let tuningPrefix: [UInt8]               // 51 31 0b
        public let tuningDegrees: [Int]
        let debounceOffset: Int, snappingOffset: Int, tuningOffset: Int

        init(_ model: DeviceModels.Model, _ params: DeviceModels.Sensor) throws {
            snappingPrefix = try model.requiredPrefix("angle_snapping")
            tuningPrefix = try model.requiredPrefix("angle_tuning")
            prefixes = model.writePrefixes("angle_snapping") + model.writePrefixes("angle_tuning")
            let t = params.angleTuning
            tuningDegrees = Array(stride(from: t.min, through: t.max, by: t.step))
            debounceOffset = try model.offset(params.offsets, "debounce", "sensor")
            snappingOffset = try model.offset(params.offsets, "angle_snapping", "sensor")
            tuningOffset = try model.offset(params.offsets, "angle_tuning", "sensor")
        }

        public func snappingCommand(_ on: Bool) -> [UInt8] { snappingPrefix + [0x00, on ? 0x01 : 0x00] }

        /// Degrees, signed LE16.
        public func tuningCommand(_ degrees: Int) throws -> [UInt8] {
            guard tuningDegrees.contains(degrees) else { throw CommandError.notAllowed("angle tuning \(degrees)°") }
            let v = UInt16(bitPattern: Int16(degrees))
            return tuningPrefix + [0x00, UInt8(v & 0xFF), UInt8(v >> 8)]
        }

        public func allows(_ c: [UInt8]) -> Bool {
            guard c.count >= 4, c[3] == 0 else { return false }
            if c.starts(with: snappingPrefix) { return c.count == 5 && c[4] <= 1 }
            if c.starts(with: tuningPrefix) {
                return c.count == 6 && tuningDegrees.contains(Int(Int16(bitPattern: UInt16(u16(c, 4)))))
            }
            return false
        }

        public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
            guard read == "settings" else { return }
            let debounce = p[debounceOffset]
            s.debounceIndex = Int(debounce)
            s.debounceMs = (1...7).contains(debounce) ? 4 + 4 * Int(debounce) : nil
            s.angleSnapping = p[snappingOffset] == 1
            let angle = u16(p, tuningOffset)
            s.angleTuning = angle & 0x8000 != 0 ? angle - 0x10000 : angle
        }
    }
}

extension Capabilities.Sensor {
    public func snappingChange(_ on: Bool) -> Change<MouseSettings> {
        Change(reads: ["settings"], build: { _ in [writeCommand(snappingCommand(on))] },
               matches: { s, _ in s.angleSnapping == on }, apply: { $0.angleSnapping = on })
    }

    public func tuningChange(_ degrees: Int) -> Change<MouseSettings> {
        Change(reads: ["settings"], build: { _ in [writeCommand(try tuningCommand(degrees))] },
               matches: { s, _ in s.angleTuning == degrees }, apply: { $0.angleTuning = degrees })
    }
}
