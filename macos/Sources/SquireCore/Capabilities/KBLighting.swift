// Keyboard lighting (squirelib/capabilities/kb_lighting.py), Falchion RX LP: 51 2c, saved with 50 55. Every byte value
// comes from Armoury Crate captures on Windows (protocol.json `keyboard_writes`, capture_4 has every option) and was
// confirmed on the device via the Omni (owner, 2026-11-22). The tables (effects, speeds, gradients, directions, ...)
// are devices.json kb_lighting; which packet layout an effect uses is code (the groups below).
//
//   51 2c <effect> 00 <speed> <brightness %> <mode> ff ff <r g b> [<r2 g2 b2> <background r g b>]   one-colour effects
//   51 2c <effect> 02 <speed> <brightness> 00 <direction> <thickness> <n> <2 stops>, then 01 / 00 <up to 4 stops>
//   51 2c 07 01 <speed> <brightness> <mode> <direction> ff <3 RGB>, then 51 2c 07 00 <3 RGB>       quicksand

extension Capabilities {
    public struct KBLighting: Capability {
        public let prefixes: [[UInt8]]
        let writePrefix: [UInt8]                       // 51 2c
        public let save: [UInt8]                       // 50 55
        /// Effect id in 12 00 [9] -> name, every effect the keyboard reports (host-driven included).
        public let lightModes: [UInt8: String]
        /// Effect -> speeds (name, byte) in AC's order fast / normal / slow; larger = slower. Writable effects only.
        public let speeds: [String: [(String, UInt8)]]
        /// Every effect Squire writes, in the keyboard's id order, then off.
        public let effects: [String]
        public let offBrightness: Int
        public let colourModes: [(String, UInt8)]
        /// AC's presets in AC's order; the names are Squire's (same as squire.py).
        public let gradients: [(String, [(Int, String)])]
        public let gradientStops: ClosedRange<Int>
        /// Direction byte per effect, the first is the default.
        public let directions: [String: [(String, UInt8)]]
        public let thicknesses: [(String, UInt8)]
        public let quicksandScale: [String]
        public let quicksandModes: [(String, UInt8)]

        // Packet layouts per effect group: code, not data (each group is its own builder below).
        /// "off" is AC's black static at 4 %; the keyboard then reports static.
        public static let off = "off"
        public static let singleColour: Set<String> = ["static", "breathing", "color-cycle", "reactive", "starry-night",
                                                       "current", "raindrop"]
        /// One-colour effects with a colour mode, a second colour and a background.
        public static let optionEffects: Set<String> = ["starry-night", "current", "raindrop"]
        public static let secondColour = "#ff00ff"         // AC's second colour when switching to double
        public static let gradientEffects: Set<String> = ["rainbow", "ripple"]
        public static let directionAliases = ["left-to-right": "right", "right-to-left": "left"]
        public static let colourless: Set<String> = ["color-cycle", "rainbow", "ripple", "quicksand", off]

        init(_ model: DeviceModels.Model, _ params: DeviceModels.KBLighting) throws {
            func named(_ table: [DeviceModels.Named]) -> [(String, UInt8)] { table.map { ($0.name, $0.value.value) } }
            writePrefix = try model.requiredPrefix(params.write)
            save = try model.requiredPrefix(params.save)
            prefixes = model.writePrefixes(params.write) + model.writePrefixes(params.save)
            lightModes = Dictionary(uniqueKeysWithValues: params.effects.map { ($0.id.value, $0.name) })
            let writable = params.effects.filter(\.isWritable)
            speeds = Dictionary(uniqueKeysWithValues: writable.map { ($0.name, named($0.speeds ?? [])) })
            effects = writable.map(\.name) + [Self.off]
            offBrightness = params.offBrightness
            colourModes = named(params.colourModes)
            gradients = params.gradients.map { g in (g.name, g.stops.map { ($0.position, $0.rgb) }) }
            gradientStops = params.gradientStops.min...params.gradientStops.max
            directions = Dictionary(uniqueKeysWithValues: params.directions.map { ($0.effect, named($0.values)) })
            thicknesses = named(params.thickness)
            quicksandScale = params.quicksandScale
            quicksandModes = named(params.quicksandModes)
            for effect in writable.map(\.name) where speeds[effect]?.isEmpty ?? true {
                throw ModelError("\(model.id): kb_lighting effect \(effect) has no speeds")
            }
            guard quicksandScale.count == 6 else { throw ModelError("\(model.id): quicksand_scale needs 6 colours") }
        }

        static func lookup(_ table: [(String, UInt8)], _ name: String?) -> UInt8? { table.first { $0.0 == name }?.1 }

        /// The effect 12 00 [9] reports after `effect` was written.
        public static func effectRead(_ effect: String) -> String { effect == off ? "static" : effect }

        public func effectID(_ effect: String) -> UInt8? {
            lightModes.first { $0.value == Self.effectRead(effect) && speeds[$0.value] != nil }?.key
        }

        /// (position %, "rrggbb") for a preset name or a custom gradient "POS:RRGGBB,..." (2...7 stops, 1...100 %
        /// ascending).
        public func stops(_ gradient: String?) throws -> [(Int, String)] {
            let name = gradient ?? "spectrum"
            if let preset = gradients.first(where: { $0.0 == name }) { return preset.1 }
            var result: [(Int, String)] = []
            for part in name.split(separator: ",") {
                let pieces = part.split(separator: ":").map { $0.trimmingCharacters(in: .whitespaces) }
                guard pieces.count == 2, let pos = Int(pieces[0]), let hex = KeyboardLighting.rgbHex(pieces[1]) else {
                    throw CommandError.notAllowed("gradient \(name)")
                }
                result.append((pos, String(hex.dropFirst())))
            }
            let positions = result.map(\.0)
            guard gradientStops.contains(result.count), positions.allSatisfy({ (1...100).contains($0) }),
                  positions == Array(Set(positions)).sorted() else {
                throw CommandError.notAllowed("gradient \(name): 2 to 7 stops at 1...100 %, ascending")
            }
            return result
        }

        /// A preset name, or the custom gradient written canonically.
        public func gradientName(_ gradient: String?) throws -> String {
            let name = gradient ?? "spectrum"
            if gradients.contains(where: { $0.0 == name }) { return name }
            return try stops(name).map { "\($0.0):\($0.1)" }.joined(separator: ",")
        }

        func pick(_ what: String, _ value: String?, _ table: [(String, UInt8)]) throws -> String {
            let value = value ?? table[0].0
            guard Self.lookup(table, value) != nil else { throw CommandError.notAllowed("\(what) \(value)") }
            return value
        }

        /// Validated and normalised, exactly like squire.keyboard_light_value: the speed actually sent, colours in
        /// lower case (#000000 for an effect without one), nil for every field the effect does not use. Options the
        /// effect does not use are ignored. "off" is fixed at 4 % black.
        public func normalized(_ light: KeyboardLight) throws -> KeyboardLight {
            typealias L = KeyboardLighting
            var l = light
            if l.effect == Self.off { l.brightness = offBrightness; l.color = "#000000" }
            let read = Self.effectRead(l.effect)
            guard let table = speeds[read] else { throw CommandError.notAllowed("keyboard effect \(l.effect)") }
            let speed = l.speed ?? (Self.lookup(table, "normal") != nil ? "normal" : table[0].0)
            guard Self.lookup(table, speed) != nil else { throw CommandError.notAllowed("speed \(speed) for \(l.effect)") }
            guard (0...100).contains(l.brightness) else { throw CommandError.notAllowed("brightness \(l.brightness)") }
            var out = KeyboardLight(effect: l.effect, brightness: l.brightness, color: "#000000", speed: speed)
            if !Self.colourless.contains(l.effect) {
                guard let color = L.rgbHex(l.color ?? "#ffffff") else { throw CommandError.notAllowed("colour \(l.color ?? "")") }
                out.color = color
            }
            if Self.gradientEffects.contains(l.effect) {
                out.gradient = try gradientName(l.gradient)
                out.direction = try pick("direction", l.direction.map { Self.directionAliases[$0] ?? $0 }, directions[l.effect]!)
                out.thickness = try pick("thickness", l.thickness, thicknesses)
            } else if l.effect == "quicksand" {
                out.direction = try pick("direction", l.direction.map { Self.directionAliases[$0] ?? $0 }, directions[l.effect]!)
                out.mode = try pick("colour mode", l.mode, quicksandModes)
            } else if Self.optionEffects.contains(l.effect) {
                out.mode = try pick("colour mode", l.mode, colourModes)
                if out.mode != "single" {
                    guard let c2 = L.rgbHex(l.color2 ?? Self.secondColour) else { throw CommandError.notAllowed("second colour") }
                    out.color2 = c2
                }
                if let bg = l.background {
                    guard let hex = L.rgbHex(bg) else { throw CommandError.notAllowed("background \(bg)") }
                    out.background = hex == "#000000" ? nil : hex
                }
            }
            return out
        }

        /// Every 51 2c chunk, in the order they are sent (one, three for a gradient, two for quicksand).
        public func chunks(_ light: KeyboardLight) throws -> [[UInt8]] {
            let rgbBytes = KeyboardLighting.rgbBytes
            let v = try normalized(light)
            let effect = Self.effectRead(v.effect)
            guard let id = effectID(v.effect), let speed = Self.lookup(speeds[effect]!, v.speed) else {
                throw CommandError.notAllowed("keyboard effect \(v.effect)")
            }
            let b = UInt8(v.brightness)
            if Self.singleColour.contains(effect) {
                var c: [UInt8] = writePrefix + [id, 0x00, speed, b, 0x00, 0xFF, 0xFF] + rgbBytes(v.color)
                if Self.optionEffects.contains(effect) {
                    c[6] = Self.lookup(colourModes, v.mode) ?? 0
                    c += rgbBytes(v.color2)
                    c += rgbBytes(v.background)
                }
                return [c]
            }
            if effect == "quicksand" {
                let scale: [UInt8] = quicksandScale.flatMap { rgbBytes("#" + $0) }
                let mode: UInt8 = Self.lookup(quicksandModes, v.mode) ?? 0
                let direction: UInt8 = Self.lookup(directions[effect] ?? [], v.direction) ?? 0x02
                let head: [UInt8] = writePrefix + [id, 0x01, speed, b, mode, direction, 0xFF]
                return [head + Array(scale[0..<9]), writePrefix + [id, 0x00] + Array(scale[9..<18])]
            }
            let packed: [[UInt8]] = try stops(v.gradient).map { [UInt8($0.0)] + rgbBytes("#" + $0.1) }
            func flat(_ range: Range<Int>) -> [UInt8] { packed.indices.filter { range.contains($0) }.flatMap { packed[$0] } }
            let direction: UInt8 = Self.lookup(directions[effect] ?? [], v.direction) ?? 0x00
            let thickness: UInt8 = Self.lookup(thicknesses, v.thickness) ?? 0x02
            let first: [UInt8] = writePrefix + [id, 0x02, speed, b, 0x00, direction, thickness, UInt8(packed.count)]
            return [first + flat(0..<2), writePrefix + [id, 0x01] + flat(2..<6), writePrefix + [id, 0x00] + flat(6..<10)]
        }

        static func stopsOK(_ s: ArraySlice<UInt8>) -> Bool {
            s.count % 4 == 0 && stride(from: s.startIndex, to: s.endIndex, by: 4).allSatisfy { (1...100).contains(s[$0]) }
        }

        public func allows(_ c: [UInt8]) -> Bool {
            if c == save { return true }
            guard c.count >= 4, c.starts(with: writePrefix), let effect = lightModes[c[2]], let table = speeds[effect] else {
                return false
            }
            let speeds = Set(table.map(\.1))
            if Self.optionEffects.contains(effect) {
                return c.count == 18 && c[3] == 0 && speeds.contains(c[4]) && c[5] <= 100
                    && colourModes.contains { $0.1 == c[6] } && c[7] == 0xFF && c[8] == 0xFF
                    && (c[6] != 0x00 || (c[12] == 0 && c[13] == 0 && c[14] == 0))
            }
            if Self.singleColour.contains(effect) {
                return c.count == 12 && c[3] == 0 && speeds.contains(c[4]) && c[5] <= 100 && c[6] == 0 && c[7] == 0xFF
                    && c[8] == 0xFF && (!Self.colourless.contains(effect) || (c[9] == 0 && c[10] == 0 && c[11] == 0))
            }
            if effect == "quicksand" {
                if c[3] == 0x01 {
                    return c.count == 18 && speeds.contains(c[4]) && c[5] <= 100 && quicksandModes.contains { $0.1 == c[6] }
                        && (directions["quicksand"] ?? []).contains { $0.1 == c[7] } && c[8] == 0xFF
                }
                return c[3] == 0x00 && c.count == 13
            }
            switch c[3] {
            case 0x02:
                guard c.count == 18 else { return false }
                return speeds.contains(c[4]) && c[5] <= 100 && c[6] == 0
                    && (directions[effect] ?? []).contains { $0.1 == c[7] } && thicknesses.contains { $0.1 == c[8] }
                    && gradientStops.contains(Int(c[9])) && Self.stopsOK(c[10...])
            case 0x01, 0x00:
                return c.count - 4 <= 16 && Self.stopsOK(c[4...])
            default:
                return false
            }
        }
    }
}

extension Capabilities.KBLighting {
    /// Every 51 2c chunk, read back from 12 00: only the effect is readable (not colour or brightness), and the
    /// profile must not have changed in between.
    public func change(_ light: KeyboardLight) -> Change<KeyboardSettings> {
        Change(reads: ["profile"],
               build: { _ in
                   let chunks = try chunks(light)
                   // a chunk is echoed as command + effect + chunk index: a late echo of the previous one is not taken for it
                   let echo = chunks.count > 1 ? 4 : 2
                   return chunks.map { writeCommand($0, echoLength: echo) }
               },
               matches: { k, before in
                   k.lightMode == Self.effectRead(light.effect) && k.profile != nil
                       && (before.map { $0.profile == k.profile } ?? true)
               })
    }
}
