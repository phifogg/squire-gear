// Keyboard capabilities besides lighting and per-key colours (squirelib/capabilities kb_profile, kb_layout, kb_sleep,
// kb_warning). Falchion RX LP; sources: G-Helper AsusKeyboard.cs (GPL-3.0, facts only) and Armoury Crate captures
// (protocol.json keyboard_settings / keyboard_writes), confirmed on the device via the Omni 2026-11-22.

/// A keyboard capability: fills its fields of KeyboardSettings from one read reply (payload from the echo on).
public protocol KeyboardCapability: Capability {
    func parse(_ read: String, _ p: [UInt8], into k: inout KeyboardSettings)
}

extension Capabilities {
    /// 12 00: current lighting effect, active profile (1-based of `profiles`), firmware. Read only (50 02 never sent).
    public struct KBProfile: KeyboardCapability {
        public let prefixes: [[UInt8]] = []
        let read: String
        let effectOffset: Int
        public let profiles: Int
        let profile: Profile
        let lightModes: [UInt8: String]

        init(_ params: DeviceModels.KBProfile, lightModes: [UInt8: String]) {
            read = params.read
            effectOffset = params.effect
            profiles = params.profiles
            profile = Profile(DeviceModels.Profile(read: params.read, profile: params.profile, profileBase: params.profileBase,
                                                   profiles: params.profiles, firmware: params.firmware,
                                                   receiverFirmware: params.receiverFirmware))
            self.lightModes = lightModes
        }

        public func allows(_ c: [UInt8]) -> Bool { false }

        public func parse(_ read: String, _ p: [UInt8], into k: inout KeyboardSettings) {
            guard read == self.read else { return }
            let effect = p[effectOffset]
            k.lightMode = lightModes[effect] ?? "0x" + ReplyParser.hex([effect])
            k.profile = profile.profile(p)
            let (firmware, receiver) = profile.firmware(p, k.connection)
            if let receiver, k.connection != .wired { k.receiverFirmware = receiver }
            if let firmware { k.firmware = firmware }
        }
    }

    /// 12 12: factory layout id (25 = CH-DE confirmed, ISO).
    public struct KBLayout: KeyboardCapability {
        public let prefixes: [[UInt8]] = []
        let read: String
        let offset: Int
        public let names: [Int: String]
        public let iso: Set<Int>

        init(_ params: DeviceModels.KBLayout) {
            read = params.read
            offset = params.layout
            names = Dictionary(uniqueKeysWithValues: params.layouts.compactMap { l in l.name.map { (l.id, $0) } })
            iso = Set(params.layouts.filter(\.iso).map(\.id))
        }

        public func allows(_ c: [UInt8]) -> Bool { false }

        public func parse(_ read: String, _ p: [UInt8], into k: inout KeyboardSettings) {
            guard read == self.read else { return }
            let id = Int(p[offset])
            k.layoutID = id
            k.layout = names[id]
            k.iso = iso.contains(id)
        }
    }

    /// 51 38 00 00 <code>: sleep after 1/2/3/5/10 min or never (the mouse's power-off coding), read back 12 01 [6].
    public struct KBSleep: KeyboardCapability {
        public let prefixes: [[UInt8]]
        let writePrefix: [UInt8]
        let read: String
        let offset: Int
        public let codes: [Int?: UInt8]
        public let minutes: [UInt8: Int]

        init(_ model: DeviceModels.Model, _ params: DeviceModels.KBSleep) throws {
            writePrefix = try model.requiredPrefix(params.write)
            prefixes = model.writePrefixes(params.write)
            read = params.read
            offset = params.offset
            codes = Dictionary(uniqueKeysWithValues: params.powerOff.map { ($0.minutes, $0.value.value) })
            minutes = Dictionary(uniqueKeysWithValues: params.powerOff.compactMap { p in p.minutes.map { (p.value.value, $0) } })
        }

        public func command(_ minutes: Int?) throws -> [UInt8] {
            guard let code = codes[minutes] else {
                throw CommandError.notAllowed("sleep \(minutes.map(String.init) ?? "never") min")
            }
            return writePrefix + [0x00, 0x00, code]
        }

        public func allows(_ c: [UInt8]) -> Bool {
            c.count == 5 && c[2] == 0 && c[3] == 0 && codes.values.contains(c[4])
        }

        public func parse(_ read: String, _ p: [UInt8], into k: inout KeyboardSettings) {
            guard read == self.read else { return }
            k.sleepMinutes = minutes[p[offset]]
        }
    }

    /// 51 3a 00 00 <%>: low-battery warning, 0 = never, read back 12 01 [9].
    public struct KBWarning: KeyboardCapability {
        public let prefixes: [[UInt8]]
        let writePrefix: [UInt8]
        let read: String
        let offset: Int
        /// AC's levels: 10...50 % and never (0) last, the order the window lists them.
        public let levels: [Int]

        init(_ model: DeviceModels.Model, _ params: DeviceModels.KBWarning) throws {
            writePrefix = try model.requiredPrefix(params.write)
            prefixes = model.writePrefixes(params.write)
            read = params.read
            offset = params.offset
            levels = params.levels.filter { $0 != 0 } + params.levels.filter { $0 == 0 }
        }

        public func command(_ percent: Int) throws -> [UInt8] {
            guard levels.contains(percent) else { throw CommandError.notAllowed("warning \(percent) %") }
            return writePrefix + [0x00, 0x00, UInt8(percent)]
        }

        public func allows(_ c: [UInt8]) -> Bool {
            c.count == 5 && c[2] == 0 && c[3] == 0 && levels.contains(Int(c[4]))
        }

        public func parse(_ read: String, _ p: [UInt8], into k: inout KeyboardSettings) {
            guard read == self.read else { return }
            k.lowWarning = p[offset] <= 100 ? Int(p[offset]) : nil          // 0 = never (AC, 2026-11-22)
        }
    }
}

extension Capabilities.KBSleep {
    /// Read back from 12 01; compares the code itself where the raw reply is known (an unknown code is not "never").
    public func change(_ minutes: Int?) -> Change<KeyboardSettings> {
        Change(reads: [read], build: { _ in [writeCommand(try command(minutes))] },
               matches: { k, _ in
                   if let raw = k.raw["battery"].map({ $0.split(separator: " ").compactMap { UInt8($0, radix: 16) } }),
                      raw.count > offset {
                       return raw[offset] == codes[minutes]
                   }
                   return k.sleepMinutes == minutes
               })
    }
}

extension Capabilities.KBWarning {
    public func change(_ percent: Int) -> Change<KeyboardSettings> {
        Change(reads: [read], build: { _ in [writeCommand(try command(percent))] },
               matches: { k, _ in k.lowWarning == percent })
    }
}
