import Foundation
import Testing
@testable import SquireCore

private func devicesURL() -> URL {
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("devices.json")
}

private func devicesText() throws -> String { try String(contentsOf: devicesURL(), encoding: .utf8) }

private func models() throws -> DeviceModels { try DeviceModels.decode(Data(try devicesText().utf8)) }

private func keyboard() throws -> DeviceModels.Model { try #require(try models().model(.keyboard)) }
private func mouse() throws -> DeviceModels.Model { try #require(try models().model(.mouse)) }

private func named(_ table: [DeviceModels.Named]) -> [String] { table.map { "\($0.name)=\(ReplyParser.hex([$0.value.value]))" } }
private func named(_ table: [(String, UInt8)]) -> [String] { table.map { "\($0.0)=\(ReplyParser.hex([$0.1]))" } }

/// devices.json (repo root) decodes strictly; a broken copy does not.
@Suite struct DeviceModelDecodingTests {
    @Test func decodes() throws {
        let m = try models()
        #expect(m.vendorID.value == SquireProtocol.vendorID)
        #expect(m.devices.map(\.id) == ["falchion-rx-lp", "keris-ii-origin"])
    }

    @Test(arguments: [
        ("\"stages\": 4", "\"stages\": \"4\""),                       // number as a string
        ("\"pid\": \"1b04\"", "\"pid\": \"0x1b04\""),                 // not four hex digits
        ("\"bytes\": \"12 07\"", "\"bytes\": \"1207\""),              // not space-separated bytes
        ("\"writable\": true", "\"writable\": \"yes\""),              // bool as a string
        ("[14, \"ff0029\"]", "[\"14\", \"ff0029\"]"),                 // gradient stop position as a string
        ("\"read\": \"battery\", \"percent\": [4]", "\"percent\": [4]"),   // required field missing
    ])
    func refusesBrokenFile(_ change: (String, String)) throws {
        let text = try devicesText()
        #expect(text.contains(change.0))
        let broken = text.replacingOccurrences(of: change.0, with: change.1)
        #expect(throws: DecodingError.self) { try DeviceModels.decode(Data(broken.utf8)) }
    }
}

/// The copy SquireCore loads at runtime (Resources/devices.json, Bundle.module) and what it derives from it.
@Suite struct BundledModelTests {
    @Test func copyEqualsRootFile() throws {
        let url = try #require(DeviceModels.bundledURL)
        #expect(try Data(contentsOf: url) == (try Data(contentsOf: devicesURL())),
                "macos/Sources/SquireCore/Resources/devices.json differs from devices.json: copy the root file")
        #expect(DeviceModels.bundled.devices.map(\.id) == (try models()).devices.map(\.id))
    }

    /// Before / after stage 1b: the hand-written tables the app used until then, value for value.
    @Test func sameRoutesAsTheHandWrittenTables() {
        let before = [
            Target(device: .keyboard, name: "ROG Falchion RX LP", connection: .omni, productID: 0x1ACE,
                   reportID: 0x02, query: [0x12, 0x01], reportLengths: [64]),
            Target(device: .keyboard, name: "ROG Falchion RX LP", connection: .wired, productID: 0x1B04,
                   reportID: nil, query: [0x12, 0x01], reportLengths: [65]),
            Target(device: .mouse, name: "ROG Keris II Origin", connection: .omni, productID: 0x1ACE,
                   reportID: 0x03, query: [0x12, 0x07], reportLengths: [64]),
            Target(device: .mouse, name: "ROG Keris II Origin", connection: .wired, productID: 0x1C0C,
                   reportID: nil, query: [0x12, 0x07], reportLengths: [65, 64]),
        ]
        #expect(Target.all == before)
        #expect(Target.keyboardTargets == Array(before[0...1]))
        #expect(Target.mouseTargets == Array(before[2...3]))
        #expect([Target.omniKeyboard, .wiredKeyboard, .omniMouse, .wiredMouse] == before)
        #expect(SquireProtocol.vendorID == 0x0B05)
        #expect(Bluetooth.productIDs == [0x1B08: .keyboard, 0x1C0F: .mouse])
        #expect(Bluetooth.device(forName: "ROG FALCHION RX LOW PROFILE") == .keyboard)
        #expect(Bluetooth.device(forName: "ROG Keris II Origin") == .mouse)
        #expect(Bluetooth.device(forName: "ROG Harpe") == nil)
        #expect(Bluetooth.device(forName: nil) == nil)
    }

    @Test func sameAllowlistAsTheHandWrittenTables() {
        #expect(Allowlist.mouseReads == Set([[0x12, 0x07], [0x12, 0x00], [0x12, 0x04, 0x00], [0x12, 0x04, 0x02],
                                             [0x12, 0x04, 0x03], [0x12, 0x03, 0x00], [0x12, 0x03, 0x01],
                                             [0x12, 0x03, 0x02], [0x12, 0x06]]))
        #expect(Allowlist.keyboardReads == Set([[0x12, 0x01], [0x12, 0x00], [0x12, 0x12]]))
        #expect(Allowlist.mouseReads == Set([Command.mouseBattery.bytes] + SettingsQuery.allCases.map(\.command.bytes)))
        #expect(Allowlist.keyboardReads == Set([Command.keyboardBattery.bytes] + KeyboardQuery.allCases.map(\.command.bytes)))
        #expect(Allowlist.reportIDs == [.keyboard: 0x02, .mouse: 0x03])
        #expect(Set(Allowlist.writePrefixes[.keyboard] ?? []) == Set([[0x50, 0x55], [0x51, 0x2C], [0x51, 0x38],
                                                                    [0x51, 0x3A], [0xC0, 0x81]]))
        #expect(Set(Allowlist.writePrefixes[.mouse] ?? []) == Set([
            [0x50, 0x03], [0x51, 0x28], [0x51, 0x37], [0x51, 0x31, 0x06], [0x51, 0x31, 0x0B], [0x51, 0x35],
            [0x51, 0x31, 0x04], [0x51, 0x31, 0x00], [0x51, 0x31, 0x01], [0x51, 0x31, 0x02], [0x51, 0x31, 0x03],
            [0x51, 0x31, 0x09], [0x51, 0x31, 0x0A]]))
    }

    /// A command whose status is not "confirmed" is never sent, even when its bytes pass the device's check.
    @Test func unconfirmedWriteIsRefused() throws {
        let text = try devicesText()
        let led = "\"name\": \"led\", \"write\": true, \"prefixes\": [\"51 28\"], \"status\": \"confirmed\""
        #expect(text.contains(led))
        let unconfirmed = try DeviceModels.decode(Data(text.replacingOccurrences(
            of: led, with: led.replacingOccurrences(of: "\"confirmed\"", with: "\"captured\"")).utf8))
        let write = try Command.setLED(.logo, LEDState(mode: .static, brightness: 100, color: RGB(0, 0, 255)))
        #expect(Allowlist.isAllowedWrite(write.bytes, for: .mouse))
        let prefixes = try #require(unconfirmed.model(.mouse)).confirmedWritePrefixes
        #expect(!Allowlist.isAllowedWrite(write.bytes, for: .mouse, prefixes: prefixes))
        #expect(!Allowlist.isAllowedWrite(write.bytes, for: .keyboard))   // never the other device's command
    }
}

/// The capabilities read their reply offsets from the model, not from constants.
@Suite struct MouseCapabilityModelTests {
    private func shiftedModel() throws -> DeviceModels.Model {
        // every reply offset +1, in a copy of the file
        var json = try JSONSerialization.jsonObject(with: Data(try devicesText().utf8)) as! [String: Any]
        var devices = json["devices"] as! [[String: Any]]
        let i = devices.firstIndex { $0["kind"] as? String == "mouse" }!
        var caps = devices[i]["capabilities"] as! [String: Any]
        for (name, value) in caps {
            guard var cap = value as? [String: Any] else { continue }
            if var offsets = cap["offsets"] as? [String: Int] {
                for (k, v) in offsets { offsets[k] = v + 1 }
                cap["offsets"] = offsets
            }
            if name == "profile" {
                cap["profile"] = (cap["profile"] as! Int) + 1
                cap["receiver_firmware"] = (cap["receiver_firmware"] as! Int) + 1
                cap["firmware"] = (cap["firmware"] as! [String: Int]).mapValues { $0 + 1 }
            }
            if name == "power" { cap["offset"] = (cap["offset"] as! Int) + 1 }
            if name == "battery" { cap["warning"] = (cap["warning"] as! Int) + 1 }
            caps[name] = cap
        }
        devices[i]["capabilities"] = caps
        json["devices"] = devices
        let data = try JSONSerialization.data(withJSONObject: json)
        return try #require(try DeviceModels.decode(data).model(.mouse))
    }

    @Test func shiftedOffsetsParseAShiftedReply() throws {
        let shifted = try MouseCapabilities(model: try shiftedModel())
        let captures = try ProtocolCaptures.load(devicesURL().deletingLastPathComponent().appendingPathComponent("protocol.json"))
        var plain = MouseSettings(connection: .omni), moved = MouseSettings(connection: .omni)
        for capture in captures.settingsCaptures {
            let query = try #require(SettingsQuery(rawValue: capture.command))
            let p = Array(capture.bytes.dropFirst())
            plain.apply(query, p)
            moved.apply(query, Array(p.prefix(4)) + [0xEE] + Array(p.dropFirst(4)), capabilities: shifted)
        }
        let battery = Array(try #require(captures.vectors.first { $0.device == "mouse" && $0.reportId == 3 }).bytes.dropFirst())
        plain.applyBattery(battery)
        moved.applyBattery(Array(battery.prefix(4)) + [0xEE] + Array(battery.dropFirst(4)), capabilities: shifted)
        #expect(plain.dpiXY.count == 4 && plain.leds.count == 3 && plain.surfaceID != nil && plain.lowWarning != nil)
        #expect(moved == plain)
    }

    @Test func keyboardShiftedOffsetsParseAShiftedReply() throws {
        var json = try JSONSerialization.jsonObject(with: Data(try devicesText().utf8)) as! [String: Any]
        var devices = json["devices"] as! [[String: Any]]
        let i = devices.firstIndex { $0["kind"] as? String == "keyboard" }!
        var caps = devices[i]["capabilities"] as! [String: Any]
        var profile = caps["kb_profile"] as! [String: Any]
        for key in ["effect", "profile", "receiver_firmware"] { profile[key] = (profile[key] as! Int) + 1 }
        profile["firmware"] = (profile["firmware"] as! [String: Int]).mapValues { $0 + 1 }
        caps["kb_profile"] = profile
        for (name, key) in [("kb_layout", "layout"), ("kb_sleep", "offset"), ("kb_warning", "offset")] {
            var cap = caps[name] as! [String: Any]
            cap[key] = (cap[key] as! Int) + 1
            caps[name] = cap
        }
        devices[i]["capabilities"] = caps
        json["devices"] = devices
        let model = try #require(try DeviceModels.decode(JSONSerialization.data(withJSONObject: json)).model(.keyboard))
        let shifted = try KeyboardCapabilities(model: model)
        let captures = try ProtocolCaptures.load(devicesURL().deletingLastPathComponent().appendingPathComponent("protocol.json"))
        func shift(_ p: [UInt8]) -> [UInt8] { Array(p.prefix(4)) + [0xEE] + Array(p.dropFirst(4)) }
        for connection in [Battery.Connection.omni, .wired] {
            var plain = KeyboardSettings(connection: connection), moved = KeyboardSettings(connection: connection)
            for capture in captures.keyboardCaptures {
                let query = try #require(KeyboardQuery(rawValue: capture.command))
                let p = Array(capture.bytes.dropFirst())
                plain.apply(query, p)
                moved.apply(query, shift(p), capabilities: shifted)
            }
            let battery = Array(try #require(captures.vectors.first { $0.device == "keyboard" && $0.reportId == 2 }).bytes.dropFirst())
            plain.applyBattery(battery)
            moved.applyBattery(shift(battery), capabilities: shifted)
            #expect(plain.profile != nil && plain.layoutID != nil && plain.lightMode != nil && plain.firmware != nil)
            #expect(moved == plain)
        }
    }

    @Test func modelWithoutACapabilityIsRefused() throws {
        let text = try devicesText().replacingOccurrences(of: "\"polling\": {", with: "\"polling_gone\": {")
        let model = try #require(try DeviceModels.decode(Data(text.utf8)).model(.mouse))
        #expect(throws: ModelError.self) { try MouseCapabilities(model: model) }
    }
}

/// Capability tables still written in Swift: each must match the file squire.py reads.
@Suite struct DeviceModelConsistencyTests {
    @Test func targets() throws {
        let m = try models()
        for target in Target.all {
            let model = try #require(m.model(target.device))
            let route = try #require(model.route(target.connection))
            #expect(target.name == model.name)
            let battery = try #require(model.command(model.capabilities.battery.read)?.bytes)
            #expect(target.query == battery.bytes)
            if let receiverID = route.receiver {
                let receiver = try #require(m.receiver(receiverID))
                #expect(target.productID == receiver.pid.value)
                #expect(target.reportLengths == receiver.reportLengths)
                #expect(target.reportID == route.reportID?.value)
            } else {
                #expect(target.productID == route.pid?.value)
                #expect(target.reportLengths == route.reportLengths)
                #expect(target.reportID == nil)
            }
        }
    }

    @Test func bluetooth() throws {
        let m = try models()
        var ids: [UInt16: Battery.Device] = [:]
        for kind in [Battery.Device.keyboard, .mouse] {
            let route = try #require(m.model(kind)?.route(.bluetooth))
            ids[try #require(route.pid?.value)] = kind
            #expect(Bluetooth.device(forName: "ROG " + (route.gattName ?? "")) == kind)
        }
        #expect(Bluetooth.productIDs == ids)
    }

    @Test func allowlists() throws {
        #expect(Allowlist.keyboardReads == (try keyboard()).confirmedReads)
        #expect(Allowlist.mouseReads == (try mouse()).confirmedReads)
        #expect(Allowlist.reportIDs[.keyboard] == (try keyboard()).route(.omni)?.reportID?.value)
        #expect(Allowlist.reportIDs[.mouse] == (try mouse()).route(.omni)?.reportID?.value)
        #expect((try mouse()).command("save")?.prefixes?.map(\.bytes) == [Command.save.bytes])
        #expect((try keyboard()).command("save")?.prefixes?.map(\.bytes) == [Command.keyboardSave.bytes])
        let perKey = try #require((try keyboard()).command("per_key"))
        #expect(perKey.prefixes?.map(\.bytes) == [KeyboardDirect.write])
        #expect(perKey.reply?.bytes == KeyboardDirect.ack)
    }

    @Test func mouseParameters() throws {
        let caps = (try mouse()).capabilities
        let profile = try #require(caps.profile)
        #expect(profile.read == "profile")
        #expect(profile.profiles == nil)

        let leds = try #require(caps.ledZones)
        #expect(leds.zones.map(\.name) == LEDZone.allCases.map(\.rawValue))
        #expect(leds.zones.map(\.index.value) == LEDZone.allCases.map(\.index))
        for mode in leds.modes { #expect(LEDMode(byte: mode.value.value).name == mode.name) }
        #expect(leds.modes.filter(\.writable).map(\.value.value) == LEDMode.writable.map(\.byte))

        let dpi = try #require(caps.dpi)
        #expect(dpi.stages == Command.dpiStages)
        #expect(dpi.min...dpi.max == Command.dpiRange)
        #expect(dpi.step == Command.dpiStep)

        let polling = try #require(caps.polling)
        #expect(polling.readRatesHz == MouseSettings.pollingRatesHz)
        #expect(Dictionary(uniqueKeysWithValues: polling.rates.map { ($0.hz, $0.value.value) }) == Command.pollingWriteHz)

        let tuning = try #require(caps.sensor).angleTuning
        #expect(Array(stride(from: tuning.min, through: tuning.max, by: tuning.step)) == Command.angleTuningDegrees)

        let power = try #require(caps.power)
        #expect(Dictionary(uniqueKeysWithValues: power.powerOff.map { ($0.minutes, $0.value.value) }) == Command.powerOffCodes)
        #expect(Dictionary(uniqueKeysWithValues: power.powerOff.compactMap { p in p.minutes.map { (p.value.value, $0) } })
                == MouseSettings.powerOffMinutes)
        #expect(power.warningLevels == Command.lowWarningLevels)

        let surface = try #require(caps.surface)
        #expect(Dictionary(uniqueKeysWithValues: surface.liftoff.map { ($0.name, $0.value.value) }) == Command.liftoffCodes)
        #expect(surface.noSurface.value == Command.noSurface)
        #expect(Dictionary(uniqueKeysWithValues: surface.surfaces.map { ($0.id.value, $0.name) }) == Command.surfaces)
        var params: [UInt8: [UInt8]?] = [surface.noSurface.value: nil]
        for s in surface.surfaces { params[s.id.value] = s.params.bytes }
        #expect(params == Command.surfaceParams)
    }

    @Test func keyboardParameters() throws {
        let caps = (try keyboard()).capabilities
        let profile = try #require(caps.kbProfile)
        #expect(profile.profiles == KeyboardSettings.profileCount)
        let mouseCaps = (try mouse()).capabilities
        #expect(mouseCaps.kbProfile == nil)
        #expect(caps.profile == nil)

        let layout = try #require(caps.kbLayout)
        #expect(Dictionary(uniqueKeysWithValues: layout.layouts.compactMap { l in l.name.map { (l.id, $0) } })
                == KeyboardSettings.layouts)
        #expect(Set(layout.layouts.filter(\.iso).map(\.id)) == KeyboardSettings.isoLayouts)

        let light = try #require(caps.kbLighting)
        #expect(Dictionary(uniqueKeysWithValues: light.effects.map { ($0.id.value, $0.name) }) == KeyboardSettings.lightModes)
        let writable = light.effects.filter(\.isWritable)
        #expect(writable.map(\.name) + [KeyboardLighting.off] == KeyboardLighting.effects)
        #expect(Set(writable.map(\.name)) == Set(KeyboardLighting.speeds.keys))
        for effect in writable {
            #expect(named(effect.speeds ?? []) == named(KeyboardLighting.speeds[effect.name] ?? []), "\(effect.name)")
        }
        #expect(light.offBrightness == KeyboardLighting.offBrightness)
        #expect(named(light.colourModes) == named(KeyboardLighting.colourModes))
        #expect(light.gradientStops.min...light.gradientStops.max == KeyboardLighting.gradientStops)
        #expect(light.gradients.map(\.name) == KeyboardLighting.gradients.map(\.0))
        for (gradient, swift) in zip(light.gradients, KeyboardLighting.gradients) {
            #expect(gradient.stops.map { "\($0.position):\($0.rgb)" } == swift.1.map { "\($0.0):\($0.1)" })
        }
        #expect(Set(light.directions.map(\.effect)) == Set(KeyboardLighting.directions.keys))
        for d in light.directions {
            #expect(named(d.values) == named(KeyboardLighting.directions[d.effect] ?? []), "\(d.effect)")
        }
        #expect(named(light.thickness) == named(KeyboardLighting.thicknesses))
        #expect(light.quicksandScale == KeyboardLighting.quicksandScale)
        #expect(named(light.quicksandModes) == named(KeyboardLighting.quicksandModes))

        let sleep = try #require(caps.kbSleep)
        #expect(Dictionary(uniqueKeysWithValues: sleep.powerOff.map { ($0.minutes, $0.value.value) }) == Command.powerOffCodes)
        #expect(Set(try #require(caps.kbWarning).levels) == Set(Command.keyboardWarningLevels))

        let perKey = try #require(caps.perKey)
        #expect(perKey.perPacket == KeyboardDirect.perPacket)
        #expect(perKey.layouts["iso"]?.map { $0.map(\.value) } == KeyboardLayout.isoIDs)
        #expect(perKey.layouts["ansi"]?.map { $0.map(\.value) } == KeyboardLayout.ansiIDs)
        let keys = (0..<perKey.columns).flatMap { col in (perKey.rows.min...perKey.rows.max).map { UInt8(col * 8 + $0) } }
        #expect(Set(keys) == KeyboardDirect.keys)
    }
}
