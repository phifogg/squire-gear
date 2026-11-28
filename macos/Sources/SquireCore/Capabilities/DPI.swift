// Mouse DPI (squirelib/capabilities/dpi.py): per stage 51 31 <stage 0-based> 00 <lo> <hi> <r g b> (X and Y alike +
// the stage colour, value (DPI - 50) / 50), active stage 51 31 09 00 <1..count>, stage count 51 31 0a 00 <1..stages>.
// Read: 12 04 00 DPI per stage (ff ff on this separate-X/Y mouse), 12 04 02 X/Y, 12 04 03 colours, 12 00 active stage
// and count.
// Source: Armoury Crate captures (mouse_writes.armoury_crate) and G-Helper AsusMouse.cs + KerisIIOrigin.cs (GPL-3.0,
// facts only). Approved 2026-11-13. Squire writes only the changed field, never the whole block like Armoury Crate.

extension Capabilities {
    public struct DPI: MouseCapability {
        public let prefixes: [[UInt8]]
        let writePrefix: [UInt8]                // 51 31
        let activeStageField: UInt8             // 09
        let stageCountField: UInt8              // 0a
        public let stages: Int
        public let range: ClosedRange<Int>
        public let step: Int
        let activeStageOffset: Int, stageCountOffset: Int, dpiOffset: Int, dpiXYOffset: Int, dpiColorsOffset: Int

        init(_ model: DeviceModels.Model, _ params: DeviceModels.DPI) throws {
            writePrefix = Array(try model.requiredPrefix(params.write).prefix(2))
            activeStageField = try model.requiredPrefix("active_stage")[2]
            stageCountField = try model.requiredPrefix("stage_count")[2]
            prefixes = [params.write, "active_stage", "stage_count"].flatMap(model.writePrefixes)
            stages = params.stages
            range = params.min...params.max
            step = params.step
            activeStageOffset = try model.offset(params.offsets, "active_stage", "dpi")
            stageCountOffset = try model.offset(params.offsets, "stage_count", "dpi")
            dpiOffset = try model.offset(params.offsets, "dpi", "dpi")
            dpiXYOffset = try model.offset(params.offsets, "dpi_xy", "dpi")
            dpiColorsOffset = try model.offset(params.offsets, "dpi_colors", "dpi")
        }

        /// One stage (1-based), X and Y alike, plus its colour.
        public func command(stage: Int, _ value: DPIStage) throws -> [UInt8] {
            guard (1...stages).contains(stage) else { throw CommandError.notAllowed("DPI stage \(stage)") }
            guard range.contains(value.dpi), value.dpi % step == 0 else { throw CommandError.notAllowed("\(value.dpi) DPI") }
            let v = UInt16((value.dpi - 50) / 50)
            return writePrefix + [UInt8(stage - 1), 0x00, UInt8(v & 0xFF), UInt8(v >> 8), value.color.r, value.color.g,
                                  value.color.b]
        }

        /// The stage in use (1-based).
        public func activeStageCommand(_ stage: Int) throws -> [UInt8] {
            guard (1...stages).contains(stage) else { throw CommandError.notAllowed("DPI stage \(stage)") }
            return writePrefix + [activeStageField, 0x00, UInt8(stage)]
        }

        /// Stages on the DPI button.
        public func stageCountCommand(_ count: Int) throws -> [UInt8] {
            guard (1...stages).contains(count) else { throw CommandError.notAllowed("DPI stage count \(count)") }
            return writePrefix + [stageCountField, 0x00, UInt8(count)]
        }

        public func allows(_ c: [UInt8]) -> Bool {
            guard c.count >= 4, c[3] == 0 else { return false }
            if c[2] == activeStageField || c[2] == stageCountField {
                return c.count == 5 && (1...stages).contains(Int(c[4]))
            }
            if Int(c[2]) < stages {
                guard c.count == 9 else { return false }
                let dpi = u16(c, 4) * 50 + 50
                return range.contains(dpi) && dpi % step == 0
            }
            return false
        }

        public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
            func dpiValue(_ raw: Int) -> Int { raw * 50 + 50 }   // devices store (DPI - 50) / 50
            switch read {
            case "profile":
                let stage = p[activeStageOffset], count = p[stageCountOffset]
                s.dpiStage = stage == 0 ? nil : Int(stage)
                s.dpiStageCount = (1...stages).contains(Int(count)) ? Int(count) : nil
            case "settings":
                // ff ff = not in this reply: separate-X/Y mice (like the Keris) report DPI only in dpi_xy
                s.dpi = (0..<stages).map { u16(p, dpiOffset + 2 * $0) == 0xFFFF ? nil : dpiValue(u16(p, dpiOffset + 2 * $0)) }
            case "dpi_xy":
                let at = dpiXYOffset
                s.dpiXY = (0..<stages).map { [dpiValue(u16(p, at + 4 * $0)), dpiValue(u16(p, at + 2 + 4 * $0))] }
                if s.dpi.isEmpty || s.dpi.contains(where: { $0 == nil }) { s.dpi = s.dpiXY.map { $0[0] } }
            case "dpi_colors":
                let at = dpiColorsOffset
                s.dpiColors = (0..<stages).map { RGB(p[at + 3 * $0], p[at + 1 + 3 * $0], p[at + 2 + 3 * $0]) }
            default:
                break
            }
        }
    }
}
