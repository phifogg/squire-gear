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

/// The Swift tables until the app reads devices.json itself: each must match the file squire.py reads.
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
        #expect(SquireProtocol.omniReceiverPID == m.receiver("rog-omni")?.pid.value)
        #expect(SquireProtocol.falchionRXLowProfilePID == (try keyboard()).route(.wired)?.pid?.value)
        #expect(SquireProtocol.kerisIIOriginPID == (try mouse()).route(.wired)?.pid?.value)
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
        #expect(profile.profile == MouseSettings.profileOffset)
        #expect(profile.profileBase == MouseSettings.profileBase)
        #expect(profile.profiles == nil)
        #expect(profile.firmware.omni == MouseSettings.firmwareOffsetOmni)
        #expect(profile.firmware.wired == MouseSettings.firmwareOffsetWired)
        #expect(profile.receiverFirmware == MouseSettings.receiverFirmwareOffset)

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
        #expect(profile.effect == KeyboardSettings.effectOffset)
        #expect(profile.profile == KeyboardSettings.profileOffset)
        #expect(profile.profileBase == KeyboardSettings.profileBase)
        #expect(profile.firmware.omni == KeyboardSettings.firmwareOffsetOmni)
        #expect(profile.firmware.wired == KeyboardSettings.firmwareOffsetWired)
        #expect(profile.receiverFirmware == KeyboardSettings.receiverFirmwareOffset)
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
