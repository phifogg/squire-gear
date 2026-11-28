// Mouse polling rate (squirelib/capabilities/polling.py): 51 31 04 00 <index>, read back from 12 04 00, low 3 bits.
// Source: Armoury Crate capture 2026-11-12 (mouse_writes.armoury_crate), approved 2026-11-13. Armoury Crate shows
// 1000 Hz for raw 0x63, so the high nibble is not a rate.

extension Capabilities {
    public struct Polling: MouseCapability {
        public let prefixes: [[UInt8]]
        let writePrefix: [UInt8]                // 51 31 04
        /// Rates Squire writes (what Armoury Crate offers) -> index.
        public let writeHz: [Int: UInt8]
        /// Rate per index in the low 3 bits of the read.
        public let readRatesHz: [Int]
        let offset: Int

        init(_ model: DeviceModels.Model, _ params: DeviceModels.Polling) throws {
            writePrefix = try model.requiredPrefix(params.write)
            prefixes = model.writePrefixes(params.write)
            writeHz = Dictionary(uniqueKeysWithValues: params.rates.map { ($0.hz, $0.value.value) })
            readRatesHz = params.readRatesHz
            offset = try model.offset(params.offsets, "polling", "polling")
            guard readRatesHz.count == 8 else { throw ModelError("\(model.id): polling needs 8 read rates (3 bits)") }
        }

        public func command(_ hz: Int) throws -> [UInt8] {
            guard let index = writeHz[hz] else { throw CommandError.notAllowed("polling rate \(hz) Hz") }
            return writePrefix + [0x00, index]
        }

        public func allows(_ c: [UInt8]) -> Bool {
            c.count == 5 && c[3] == 0 && writeHz.values.contains(c[4])
        }

        public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
            guard read == "settings" else { return }
            s.pollingRaw = p[offset]
            s.pollingHz = readRatesHz[Int(p[offset] & 0x07)]
        }
    }
}

extension Capabilities.Polling {
    public func change(_ hz: Int) -> Change<MouseSettings> {
        Change(reads: ["settings"], build: { _ in [writeCommand(try command(hz))] },
               matches: { s, _ in s.pollingHz == hz }, apply: { $0.pollingHz = hz })
    }
}
