import Foundation
import Testing
@testable import SquireCore

private func protocolURL() -> URL {
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("protocol.json")
}

private func bytes(_ hex: String) -> [UInt8] { hex.split(separator: " ").compactMap { UInt8($0, radix: 16) } }

private func keyboardWrites() throws -> [String: Any] {
    let json = try JSONSerialization.jsonObject(with: Data(contentsOf: protocolURL())) as! [String: Any]
    return json["keyboard_writes"] as! [String: Any]
}

private func omni(_ command: Command) throws -> [UInt8] {
    try Packet.build(device: .keyboard, reportID: 0x02, command: command, length: 64)
}

/// A captured one-colour 51 2c packet (report ID at [0]) as the lighting Squire would send for it.
private func decodeSingle(_ raw: [UInt8]) -> KeyboardLight? {
    guard raw.count == 64, raw[1] == 0x51, raw[2] == 0x2C, let effect = KeyboardSettings.lightModes[raw[3]],
          KeyboardLighting.singleColour.contains(effect),
          let speed = KeyboardLighting.speeds[effect]?.first(where: { $0.1 == raw[5] })?.0 else { return nil }
    let hex = { (i: Int) in "#" + ReplyParser.hex(Array(raw[i..<i + 3])).replacingOccurrences(of: " ", with: "") }
    var light = KeyboardLight(effect: effect, brightness: Int(raw[6]), color: hex(10), speed: speed)
    if KeyboardLighting.optionEffects.contains(effect) {
        light.mode = KeyboardLighting.colourModes.first { $0.1 == raw[7] }?.0
        light.color2 = hex(13)
        light.background = hex(16)
    }
    return light
}

@Suite struct KeyboardWriteTests {
    /// protocol.json keyboard_writes.squire_writes: every write Squire sent and the owner saw on the device.
    @Test func squireWritesConfirmedOnTheDevice() throws {
        let writes = (try keyboardWrites()["squire_writes"] as! [String: Any])["writes"] as! [[String: Any]]
        var checked = 0
        for w in writes {
            let args = w["args"] as! [Any]
            let commands: [Command]
            switch w["function"] as! String {
            case "keyboard_sleep_command": commands = [try .keyboardSleep(args[0] as? Int)]
            case "keyboard_warning_command": commands = [try .keyboardWarning(args[0] as! Int)]
            default:
                let kw = w["kwargs"] as? [String: String] ?? [:]
                let at = { (i: Int) -> String? in args.count > i ? args[i] as? String : nil }
                let light = KeyboardLight(effect: args[0] as! String, brightness: args[1] as! Int,
                                          color: at(2) ?? "#ffffff", speed: at(3), gradient: at(4), direction: at(5),
                                          thickness: kw["thickness"], mode: kw["mode"], color2: kw["color2"],
                                          background: kw["background"])
                commands = try KeyboardLighting.commands(light)
            }
            let heads = ((w["packets"] as? [String]) ?? [w["packet"] as! String]).map(bytes)
            #expect(commands.count == heads.count, "\(w["name"]!)")
            for (command, head) in zip(commands, heads) {
                let packet = try omni(command)
                #expect(Array(packet.prefix(head.count)) == head, "\(w["name"]!)")
                #expect(packet.dropFirst(head.count).allSatisfy { $0 == 0 }, "\(w["name"]!)")
            }
            checked += 1
        }
        #expect(checked >= 25)
    }

    /// Every one-colour packet and warning Armoury Crate sent, byte for byte (all capture sessions).
    @Test func armouryCrateSingleColourPacketsAndWarnings() throws {
        let data = try keyboardWrites()
        var entries = data["writes"] as! [[String: Any]]
        entries += (data["capture_2"] as! [String: Any])["writes"] as! [[String: Any]]
        entries += (data["capture_3"] as! [String: Any])["effect_writes"] as! [[String: Any]]
        for (key, block) in data["capture_4"] as! [String: Any] where !key.hasPrefix("_") {
            entries += (block as! [String: Any])["writes"] as! [[String: Any]]
        }
        var checked = 0
        for w in entries {
            guard let hex = w["packet"] as? String else { continue }
            let raw = bytes(hex)
            if raw[1] == 0x51 && raw[2] == 0x3A {
                #expect(try omni(.keyboardWarning(Int(raw[5]))) == raw)
                checked += 1
            } else if let light = decodeSingle(raw) {
                let commands = try KeyboardLighting.commands(light)
                #expect(commands.count == 1)
                #expect(try omni(commands[0]) == raw, "\(w["name"]!)")
                checked += 1
            } else if raw[0] == 0x03 {   // AC's 51 36 to the mouse with "lighting off": never sent by Squire
                #expect(!Allowlist.isAllowedWrite(Array(raw[1..<7]), for: .mouse))
                #expect(!Allowlist.isAllowedWrite(Array(raw[1..<7]), for: .keyboard))
            }
        }
        #expect(checked >= 45)
    }

    /// capture_4 rainbow presets (slow, centre outwards, thickness strong), chunk by chunk.
    @Test func armouryCrateGradientPresets() throws {
        let rainbow = ((try keyboardWrites()["capture_4"] as! [String: Any])["rainbow"] as! [String: Any])["writes"]
            as! [[String: Any]]
        var checked = 0
        for (n, preset) in KeyboardLighting.gradients.enumerated() {
            let light = KeyboardLight(effect: "rainbow", brightness: 50, speed: "slow", gradient: preset.0, direction: "outwards")
            let commands = try KeyboardLighting.commands(light)
            for w in rainbow where (w["name"] as! String).hasPrefix("preset \(n + 1) ") {
                let raw = bytes(w["packet"] as! String)
                let match = commands.first(where: { $0.bytes[3] == raw[4] })
                let command = try #require(match)
                #expect(try omni(command) == raw, "\(w["name"]!)")
                checked += 1
            }
        }
        #expect(checked >= 12)
    }

    @Test func offAndOptions() throws {
        let off = try KeyboardLighting.commands(KeyboardLight(effect: "off", brightness: 80, color: "#ff0000"))
        #expect(off.map(\.bytes) == [[0x51, 0x2C, 0x00, 0x00, 0xFF, 0x04, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00]])
        #expect(KeyboardLighting.effectRead("off") == "static")
        let single = try KeyboardLighting.commands(KeyboardLight(effect: "raindrop", color2: "#00ff00"))[0].bytes
        #expect(Array(single[12..<15]) == [0, 0, 0])
        let double = try KeyboardLighting.normalized(KeyboardLight(effect: "current", mode: "double", background: "#000000"))
        #expect(double.color2 == "#ff00ff" && double.background == nil)
        let custom = try KeyboardLighting.normalized(KeyboardLight(effect: "ripple", gradient: "14:FF0000, 100:0000FF"))
        #expect(custom.gradient == "14:ff0000,100:0000ff" && custom.direction == "none" && custom.thickness == "strong")
        let legacy = try KeyboardLighting.normalized(KeyboardLight(effect: "rainbow", direction: "right-to-left"))
        #expect(legacy.direction == "left")
        #expect(try KeyboardLighting.commands(KeyboardLight(effect: "rainbow")).map { $0.echoLength } == [4, 4, 4])
        for bad in [KeyboardLight(effect: "host-driven"), KeyboardLight(effect: "static", brightness: 101),
                    KeyboardLight(effect: "reactive", speed: "none"), KeyboardLight(effect: "current", mode: "scale"),
                    KeyboardLight(effect: "quicksand", direction: "left"), KeyboardLight(effect: "rainbow", thickness: "thick"),
                    KeyboardLight(effect: "rainbow", gradient: "14:ff0000"), KeyboardLight(effect: "rainbow", gradient: "50:ff0000,14:00ff00"),
                    KeyboardLight(effect: "rainbow", gradient: "0:ff0000,100:00ff00"), KeyboardLight(effect: "raindrop", background: "#12345")] {
            #expect(throws: CommandError.self) { try KeyboardLighting.commands(bad) }
        }
    }

    @Test func everythingElseStaysRefused() {
        for hex in ["51 2c 00 00 0f 32 00 ff ff ff 00 00",   // static with a speed
                    "51 2c 02 00 1f 32 00 ff ff ff 00 00",   // colour cycle with a colour
                    "51 2c 00 00 ff 65 00 ff ff ff 00 00",   // 101 %
                    "51 2c 09 00 14 32 00 ff ff ff ff ff 00 ff 00 00 00 00",   // single with a second colour
                    "51 2c 09 00 14 32 02 ff ff ff ff ff 00 00 00 00 00 00",   // colour mode 02
                    "51 2c 07 01 0f 32 00 04 ff ff f6 00 01 ff 00 00 fa ff",   // quicksand direction 04
                    "51 2c 04 02 3e 32 00 09 02 07 0e f5 00 ff 1d 00 06 ff",   // direction 09
                    "51 2c 04 02 3e 32 00 00 03 07 0e f5 00 ff 1d 00 06 ff",   // thickness 03
                    "51 2c 05 02 64 32 00 00 02 07 0e f5 00 ff 1d 00 06 ff",   // ripple with a direction
                    "51 38 00 00 05", "51 3a 00 00 3c", "51 3a 00 00 19", "50 03", "50 02 01",
                    "51 37 00 00 03 00 14", "51 28 00 00 00 64 ff 00 00 00 00 00"] {
            #expect(!Allowlist.isAllowedWrite(bytes(hex), for: .keyboard), "\(hex)")
        }
        #expect(throws: CommandError.self) { try Packet.build(device: .mouse, reportID: 0x03, command: .keyboardSave, length: 64) }
        #expect(try omni(.keyboardSave).prefix(3) == [0x02, 0x50, 0x55])
        #expect(try Command.keyboardWarning(0).bytes == [0x51, 0x3A, 0, 0, 0])
        #expect(try Command.keyboardSleep(nil).bytes == [0x51, 0x38, 0, 0, 0xFF])
    }

    @Test func warningNever() {
        var k = KeyboardSettings(connection: .omni)
        k.applyBattery(bytes("12 01 00 00 00 45 02 01 00 00 45 82 0f 00"))
        #expect(k.lowWarning == 0 && KeyboardZone.power.summary(k).contains("never warns"))
        let parsed = ReplyParser.parse(.keyboard, bytes("02 12 01 00 00 00 45 02 01 00 00 45 82 0f 00"), base: 1)
        #expect(parsed.lowWarning == 0)
        let b = Battery(device: .keyboard, name: "k", connection: .omni, percent: 3, lowWarning: 0)
        #expect(LowBattery.evaluate([b], state: .init()).toNotify.isEmpty)   // set to never: Squire is quiet too
        #expect(KeyboardChange.warning(0).matches(k))
    }

    @Test func rememberedLightingIsSharedWithPythonAndMigrated() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("squire-\(UUID().uuidString)/kb.json")
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        // as squire.py version 1 wrote it (nulls included)
        try Data("""
        {"profiles": {"4": {"effect": "reactive", "brightness": 75, "color": "#f6f5f4", "speed": "fast",
                            "gradient": null, "direction": null},
                      "2": {"effect": "off", "brightness": 4, "color": "#000000", "speed": "none"}}}
        """.utf8).write(to: url)
        #expect(KeyboardLightMemory.remembered(profile: 4, effect: "reactive", url: url)?.speed == "normal")
        #expect(KeyboardLightMemory.remembered(profile: 4, effect: "raindrop", url: url) == nil)   // stale
        #expect(KeyboardLightMemory.remembered(profile: 2, effect: "static", url: url)?.effect == "off")
        let light = try KeyboardLighting.normalized(KeyboardLight(effect: "reactive", brightness: 50, speed: "fast"))
        KeyboardLightMemory.remember(light, profile: 1, url: url)
        let json = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        let profiles = json["profiles"] as! [String: [String: Any]]
        #expect(json["version"] as? Int == 2)
        #expect(profiles["4"]?["speed"] as? String == "normal" && profiles["1"]?["speed"] as? String == "fast")
    }
}

/// The keyboard page's editing logic, same cases as KeyboardEditTests in test_squire_settings.py.
@Suite struct KeyboardEditingTests {
    private func kb(_ effect: String = "raindrop", warning: UInt8 = 20) -> KeyboardSettings {
        var k = KeyboardSettings(connection: .omni)
        var reply = [UInt8](repeating: 0, count: 63)
        reply[0] = 0x12; reply[1] = 0x01; reply[6] = 0x02; reply[9] = warning; reply[10] = 70
        k.applyBattery(reply)
        k.raw["battery"] = ReplyParser.hex(reply)
        k.lightMode = effect
        k.profile = 4
        return k
    }

    @Test func unwritableEffectStartsFromStatic() {
        let k = kb("host-driven")
        #expect(KeyboardEditing.lightStart(k, known: nil).effect == "static")
        #expect(!KeyboardEditing.isEditing(k, KeyboardEdits()))
        let e = KeyboardEditing.editLight(k, KeyboardEdits(), known: nil) { $0.effect = "breathing" }
        #expect(e.light?.speed == "normal")
        #expect(KeyboardEditing.plannedPackets(k, e) == ["02 51 2c 01 00 1e 64 00 ff ff ff ff ff …", "02 50 55 …  (save)"])
    }

    @Test func offIsABlackStaticAndMatchesTheStaticRead() {
        let e = KeyboardEditing.editLight(kb("reactive"), KeyboardEdits(), known: nil) { $0.effect = "off" }
        #expect(KeyboardEditing.plannedPackets(kb("reactive"), e)
                == ["02 51 2c 00 00 ff 04 00 ff ff 00 00 00 …", "02 50 55 …  (save)"])
        let known = try? KeyboardLighting.normalized(KeyboardLight(effect: "off"))
        let k = kb("static")
        #expect(KeyboardEditing.lightSummary(k, light: known) == "Off")
        #expect(KeyboardEditing.editLight(k, KeyboardEdits(), known: known) { $0.effect = "off" }.isEmpty)
        #expect(KeyboardEditing.editLight(k, KeyboardEdits(), known: known) { $0.effect = "static" }.light?.brightness == 100)
    }

    @Test func colourModesSecondColourAndBackground() {
        let k = kb()
        var e = KeyboardEditing.editLight(k, KeyboardEdits(), known: nil) {
            $0.effect = "current"; $0.color = "#ff0000"; $0.mode = "double"
        }
        #expect(KeyboardEditing.plannedPackets(k, e).first == "02 51 2c 08 00 0a 64 10 ff ff ff 00 00 ff 00 ff 00 00 00 …")
        e = KeyboardEditing.editLight(k, e, known: nil) { $0.background = "#1bff00"; $0.color2 = "#00ff00" }
        #expect(KeyboardEditing.plannedPackets(k, e).first == "02 51 2c 08 00 0a 64 10 ff ff ff 00 00 00 ff 00 1b ff 00 …")
        e = KeyboardEditing.editLight(k, e, known: nil) { $0.effect = "quicksand" }
        #expect(e.light?.mode == "scale" && e.light?.direction == "down")
        e = KeyboardEditing.editLight(k, e, known: nil) { $0.effect = "starry-night" }
        #expect(e.light?.mode == "single" && e.light?.color2 == "#00ff00" && e.light?.background == "#1bff00")
    }

    @Test func knownLightDropsAnEditBackToIt() {
        let k = kb("static")
        let known = try? KeyboardLighting.normalized(KeyboardLight(effect: "static", brightness: 50, color: "#ff0000"))
        var e = KeyboardEditing.editLight(k, KeyboardEdits(), known: known) { $0.brightness = 80 }
        #expect(e.light?.brightness == 80)
        e = KeyboardEditing.editLight(k, e, known: known) { $0.brightness = 50 }
        #expect(e.isEmpty)
    }

    @Test func warningNeverAndSleep() {
        let k = kb(warning: 0)
        #expect(KeyboardEditing.canEditWarning(k) && KeyboardEditing.canEditSleep(k))
        let e = KeyboardEditing.editWarning(kb(), KeyboardEdits(), 0)
        #expect(KeyboardEditing.plannedPackets(k, e) == ["02 51 3a 00 00 00 …", "02 50 55 …  (save)"])
        #expect(KeyboardEditing.editSleep(k, KeyboardEdits(), 3).isEmpty)        // 02 = 3 min already
        let never = KeyboardEditing.editSleep(k, KeyboardEdits(), nil)
        #expect(never.sleep == KeyboardSleep(nil) && never.changes == [.sleep(nil)])
        var unknown = k
        unknown.raw["battery"] = "12 01 00 00 00 45 05 01 00 14 45"
        #expect(!KeyboardEditing.canEditSleep(unknown))                          // code 05: not "never"
    }
}
