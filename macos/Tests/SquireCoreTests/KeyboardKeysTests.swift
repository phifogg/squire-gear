import Foundation
import Testing
@testable import SquireCore

private func protocolURL() -> URL {
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("protocol.json")
}

private func bytes(_ hex: String) -> [UInt8] { hex.split(separator: " ").compactMap { UInt8($0, radix: 16) } }

/// "01=ff0000,09=00ff00" -> {key id: "#rrggbb"} (the "keys" field of protocol.json keyboard_writes.direct).
private func keySpec(_ spec: String) -> [UInt8: String] {
    var keys: [UInt8: String] = [:]
    for part in spec.split(separator: ",") {
        let kv = part.split(separator: "=")
        if kv[0] == "all" {
            for key in KeyboardDirect.keys { keys[key] = "#" + kv[1] }
        } else {
            keys[UInt8(kv[0], radix: 16)!] = "#" + kv[1]
        }
    }
    return keys
}

private let iso = KeyboardSettings(connection: .omni)

@Suite struct KeyboardKeysTests {
    /// protocol.json keyboard_writes.direct: every per-key send the owner saw on the device (Omni and wired).
    @Test func sendsConfirmedOnTheDevice() throws {
        let json = try JSONSerialization.jsonObject(with: Data(contentsOf: protocolURL())) as! [String: Any]
        let writes = ((json["keyboard_writes"] as! [String: Any])["direct"] as! [String: Any])["writes"] as! [[String: Any]]
        var checked = 0
        for w in writes {
            let spec = w["keys"] as! String
            let wired = (w["connection"] as? String) == "wired"
            let heads = (w["packets"] as? [String]) ?? [w["packet"] as! String]
            if spec.hasPrefix("00=") {          // row 0 does not exist on the RX LP
                #expect(!KeyboardDirect.isAllowed(Array(bytes(heads[0]).dropFirst())))
                continue
            }
            let commands = try KeyboardDirect.commands(KeyboardDirect.colours(keySpec(spec)))
            #expect(commands.count == heads.count)
            for (command, head) in zip(commands, heads) {
                let packet = try Packet.build(device: .keyboard, reportID: wired ? nil : 0x02, command: command,
                                              length: wired ? 65 : 64)
                #expect(Array(packet.prefix(bytes(head).count)) == bytes(head))
                #expect(packet.dropFirst(bytes(head).count).allSatisfy { $0 == 0 })
                #expect(command.expectedReply == [0xC0, 0x82, command.bytes[2], 0x00])
            }
            checked += 1
        }
        #expect(checked >= 6)
    }

    @Test func allowlist() throws {
        #expect(Allowlist.isAllowedWrite(bytes("c0 81 01 00 74 ff ff ff"), for: .keyboard))
        #expect(!Allowlist.isAllowedWrite(bytes("c0 81 01 00 74 ff ff ff"), for: .mouse))
        for bad in ["c0 81 01 00 00 ff ff ff", "c0 81 01 00 06 ff ff ff", "c0 81 01 00 79 ff ff ff",
                    "c0 81 02 00 01 ff ff ff", "c0 81 00 00", "c0 81 01 01 01 ff ff ff"] {
            #expect(!Allowlist.isAllowedWrite(bytes(bad), for: .keyboard), "\(bad)")
        }
        let fifteen = bytes("c0 81 0f 00") + Array(repeating: bytes("01 ff ff ff"), count: 15).flatMap { $0 }
        #expect(!KeyboardDirect.isAllowed(fifteen))           // one over the Omni's 64 bytes
        #expect(throws: (any Error).self) { try Packet.build(device: .mouse, reportID: 0x03,
                                                              command: Command(bytes("c0 81 01 00 01 ff 00 00"), isWrite: true),
                                                              length: 64) }
    }

    @Test func packetsOfFourteenInKeyOrder() throws {
        let all = Dictionary(uniqueKeysWithValues: KeyboardLayout.keyIDs(iso: true).map { ($0, RGB(1, 2, 3)) })
        let commands = try KeyboardDirect.commands(all)
        #expect(commands.map { $0.bytes[2] } == [14, 14, 14, 14, 13])
        #expect(Array(commands[0].bytes.prefix(8)) == bytes("c0 81 0e 00 01 01 02 03"))
        #expect(KeyboardChange.keys([0x01: "#ff0000"]).isSaved == false)
    }

    @Test func keyIDsMatchTheDrawing() {
        #expect(KeyboardLayout.keyIDs(iso: true).count == 69)
        #expect(KeyboardLayout.keyIDs(iso: false).count == 68)
        for (rows, ids) in [(KeyboardLayout.iso, KeyboardLayout.isoIDs), (KeyboardLayout.ansi, KeyboardLayout.ansiIDs)] {
            #expect(rows.map(\.count) == ids.map(\.count))
        }
        #expect(KeyboardLayout.iso[0][0].legend == "Esc")
        #expect(KeyboardLayout.iso[0].last?.legend == "Ins")
        let u = 36.0, pad = 18.0
        #expect(KeyboardEditing.keyAt(iso, x: pad + 5, y: pad + 5) == 0x01)                    // Esc
        #expect(KeyboardEditing.keyAt(iso, x: pad + 15 * u + 5, y: pad + 5) == 0x71)           // Insert
        #expect(KeyboardEditing.keyAt(iso, x: pad + 15 * u + 5, y: pad + 4 * u + 5) == 0x75)   // bottom right
        #expect(KeyboardEditing.keyAt(iso, x: pad + 14.2 * u, y: pad + 2.5 * u) == 0x6B)       // ISO Enter, lower part
        #expect(KeyboardEditing.keyAt(iso, x: 2, y: 2) == nil)
        #expect(KeyboardEditing.keyAt(iso, x: pad + u - 2, y: pad + 5) == nil)                 // gap
    }

    /// The same colours as squire.builtin_key_presets (values printed by squire.py, 2026-11-25).
    @Test func squireDefaultsMatchPython() {
        let expected: [String: [UInt8: String]] = [
            "Fixed rainbow": [0x01: "#ff0000", 0x23: "#66ff00", 0x3B: "#00ffcc", 0x6C: "#ff00cc", 0x75: "#ff0066", 0x4B: "#0066ff"],
            "Gaming": [0x01: "#202020", 0x23: "#202020", 0x6C: "#ff0000", 0x75: "#ff0000", 0x12: "#ff0000", 0x35: "#ff8000"],
            "Typing": [0x01: "#ff8c00", 0x23: "#0000ff", 0x3B: "#0000ff", 0x6C: "#ff8c00"],
            "Modifiers": [0x01: "#0000ff", 0x23: "#ffffff", 0x6C: "#0000ff", 0x4B: "#ffffff"],
            "Ocean": [0x01: "#0030ff", 0x23: "#0098e4", 0x6C: "#00cbd6", 0x75: "#00ffc8"],
            "Fixed sunset": [0x01: "#ff7a00", 0x23: "#de5749", 0x3B: "#c53d80", 0x6C: "#9209ed", 0x75: "#8a00ff", 0x4B: "#b42ca4"],
        ]
        for iso in [true, false] {
            let presets = KeyPresets.builtin(iso: iso)
            #expect(Set(presets.keys) == Set(KeyPresets.builtinNames))
            for (name, keys) in expected {
                for (key, colour) in keys { #expect(presets[name]?[key] == colour, "\(name) \(key)") }
                #expect(presets[name]?.count == (iso ? 69 : 68))
            }
        }
    }

    @Test func presetsFileSharedWithPython() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent("keyboard-keys.json")
        #expect(KeyPresets.load(url).isEmpty)
        try KeyPresets.save(" Mine ", [0x01: "#FF0000", 0x0A: "#00ff00"], url: url)
        #expect(KeyPresets.load(url) == ["Mine": [0x01: "#ff0000", 0x0A: "#00ff00"]])
        let stored = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        #expect((stored["version"] as? Int) == 1)
        #expect(((stored["presets"] as! [String: Any])["Mine"] as! [String: Any])["keys"] as? [String: String]
                == ["01": "#ff0000", "0a": "#00ff00"])
        #expect(throws: (any Error).self) { try KeyPresets.save("gaming", [0x01: "#ff0000"], url: url) }
        #expect(throws: (any Error).self) { try KeyPresets.save("x", [0x00: "#ff0000"], url: url) }
        #expect(try KeyPresets.delete("Mine", url: url))
        #expect(try !KeyPresets.delete("Mine", url: url))
        // a file written by squire.py, with one broken entry
        try ##"{"version": 1, "presets": {"ok": {"keys": {"01": "#ff0000"}}, "bad": {"keys": {"00": "#ff0000"}}}}"##
            .write(to: url, atomically: true, encoding: .utf8)
        #expect(KeyPresets.load(url) == ["ok": [0x01: "#ff0000"]])
    }

    @Test func editingAndApplyOrder() throws {
        var edits = KeyboardEditing.editLight(iso, KeyboardEdits(), known: nil) { $0.effect = "static" }
        edits = KeyboardEditing.selectPerKey(iso, edits, active: nil)
        #expect(edits.light == nil)
        #expect(edits.keys?.count == 69 && Set(edits.keys!.values) == ["#000000"])
        edits = KeyboardEditing.paint(iso, edits, active: nil, [0x01: "#FF0000"])
        #expect(edits.keys?[0x01] == "#ff0000")
        let active = edits.keys!
        let changed = KeyboardEditing.paint(iso, KeyboardEdits(), active: active, [0x09: "#00ff00"])
        #expect(changed.keys?[0x09] == "#00ff00")
        #expect(KeyboardEditing.paint(iso, changed, active: active, [0x09: "#000000"]).keys == nil)

        edits = KeyboardEditing.editSleep(iso, edits, 5)
        #expect(edits.changes.map(\.title) == ["Sleep", "Per-key colors"])
        #expect(edits.savedChanges.map(\.title) == ["Sleep"])
        let lines = KeyboardEditing.plannedPackets(iso, edits)
        #expect(lines.map { String($0.prefix(11)) }
                == ["02 51 38 00", "02 50 55 … ", "02 c0 81 0e", "02 c0 81 0e", "02 c0 81 0e", "02 c0 81 0e", "02 c0 81 0d"])
        #expect(lines.last?.hasSuffix("(per-key colors, not saved)") == true)
        var onlyKeys = KeyboardEdits()
        onlyKeys.keys = active
        #expect(!KeyboardEditing.plannedPackets(iso, onlyKeys).joined().contains("50 55"))
        #expect(KeyboardEditing.applyStatus(onlyKeys).contains("until it is switched off"))
    }

    @Test func effectChoicesSorted() {
        let names = KeyboardEditing.effectChoices.map(KeyboardEditing.effectChoiceTitle)
        #expect(Array(names.suffix(2)) == ["Per-key colors", "Off"])
        let effects = Array(names.dropLast(2))
        #expect(effects == effects.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending })
    }
}
