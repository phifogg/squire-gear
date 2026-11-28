// Mouse lift-off + surface (squirelib/capabilities/surface.py): 51 35 00 00 <surface id> <lift-off> 00 <p1 p2 p3>, or
// 51 35 ff 00 ff <lift-off> for "no surface". Read: 12 06 surface id (ff = none), lift-off, 3 surface parameters.
// Source: Armoury Crate captures on Windows (USBPcap, 2026-11-12, protocol.json mouse_writes.armoury_crate). Approved
// 2026-11-13. A lift-off write carries the surface id and parameters read from 12 06 just before, so the surface stays
// (G-Helper's 51 35 ff 00 ff <l> drops it). Only Armoury Crate's preset ids: an unknown one (a custom calibration?) is
// never overwritten.

extension Capabilities {
    public struct Surface: MouseCapability {
        public let prefixes: [[UInt8]]
        let writePrefix: [UInt8]                    // 51 35
        let read: String                            // liftoff (12 06)
        public let noSurface: UInt8                 // "Default (No calibration)" in Armoury Crate
        public let liftoffCodes: [String: UInt8]
        let liftoffNames: [UInt8: String]
        /// Armoury Crate's preset surfaces, id -> name.
        public let surfaces: [UInt8: String]
        /// Parameters Armoury Crate writes with each preset (captured 2026-11-12); nil for "no surface".
        public let surfaceParams: [UInt8: [UInt8]?]
        /// None first, then the presets by name.
        public let choices: [UInt8]
        let surfaceOffset: Int, liftoffOffset: Int, paramsOffset: Int

        init(_ model: DeviceModels.Model, _ params: DeviceModels.Surface) throws {
            writePrefix = try model.requiredPrefix(params.write)
            prefixes = model.writePrefixes(params.write)
            read = params.read
            noSurface = params.noSurface.value
            liftoffCodes = Dictionary(uniqueKeysWithValues: params.liftoff.map { ($0.name, $0.value.value) })
            liftoffNames = Dictionary(uniqueKeysWithValues: params.liftoff.map { ($0.value.value, $0.name) })
            surfaces = Dictionary(uniqueKeysWithValues: params.surfaces.map { ($0.id.value, $0.name) })
            var table: [UInt8: [UInt8]?] = [params.noSurface.value: nil]
            for s in params.surfaces { table[s.id.value] = s.params.bytes }
            surfaceParams = table
            let names = surfaces
            choices = [noSurface] + names.keys.sorted { names[$0]! < names[$1]! }
            surfaceOffset = try model.offset(params.offsets, "surface", "surface")
            liftoffOffset = try model.offset(params.offsets, "liftoff", "surface")
            paramsOffset = try model.offset(params.offsets, "params", "surface")
        }

        public func name(_ id: UInt8?) -> String? {
            guard let id else { return nil }
            if id == noSurface { return "None (no calibration)" }
            return surfaces[id] ?? "unknown (0x\(ReplyParser.hex([id])))"
        }

        /// Lift-off with the surface as read (id + parameters). Refuses an unknown surface so it is never overwritten.
        public func liftoffCommand(_ liftoff: String, surfaceID: UInt8?, surfaceParams params: [UInt8]?) throws -> [UInt8] {
            guard let code = liftoffCodes[liftoff] else { throw CommandError.notAllowed("lift-off \(liftoff)") }
            if surfaceID == noSurface { return writePrefix + [noSurface, 0x00, noSurface, code] }
            guard let id = surfaceID, surfaces[id] != nil, let params, params.count == 3 else {
                throw CommandError.notAllowed("surface \(name(surfaceID) ?? "not read"): lift-off not written, so the surface setting is kept")
            }
            return writePrefix + [0x00, 0x00, id, code, 0x00] + params
        }

        /// A preset surface (or none) with its Armoury Crate parameters, keeping `liftoff`.
        public func surfaceCommand(_ id: UInt8, liftoff: String) throws -> [UInt8] {
            guard let params = surfaceParams[id] else { throw CommandError.notAllowed("surface 0x\(ReplyParser.hex([id]))") }
            return try liftoffCommand(liftoff, surfaceID: id, surfaceParams: params)
        }

        public func allows(_ c: [UInt8]) -> Bool {
            let codes = Set(liftoffCodes.values)
            if c.count == 6 { return c[2] == noSurface && c[3] == 0 && c[4] == noSurface && codes.contains(c[5]) }
            return c.count == 10 && c[2] == 0 && c[3] == 0 && surfaces[c[4]] != nil && codes.contains(c[5]) && c[6] == 0
        }

        public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
            guard read == self.read else { return }
            let lift = p[liftoffOffset], surface = p[surfaceOffset]
            s.liftoff = liftoffNames[lift] ?? "0x" + ReplyParser.hex([lift])
            s.surfaceID = surface
            s.surfaceParams = surface == noSurface ? nil : Array(p[paramsOffset..<paramsOffset + 3])
        }
    }
}
