import Foundation
import Testing
@testable import SquireCore

private func protocolURL() -> URL {
    // macos/Tests/SquireCoreTests/<this file> -> repo root
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("protocol.json")
}

private func bytes(_ hex: String) -> [UInt8] { hex.split(separator: " ").compactMap { UInt8($0, radix: 16) } }

private func json() throws -> [String: Any] {
    try JSONSerialization.jsonObject(with: Data(contentsOf: protocolURL())) as! [String: Any]
}

/// protocol.json `mouse_settings`: the same captures and `expect` values the Python tests use.
@Suite struct MouseSettingsCaptureTests {
    @Test func everyCaptureMatchesItsExpectation() throws {
        let captures = (try json()["mouse_settings"] as! [String: Any])["captures"] as! [[String: Any]]
        #expect(captures.count >= 8)
        for capture in captures {
            let name = capture["command"] as! String
            let query = try #require(SettingsQuery(rawValue: name), "unknown command \(name)")
            var s = MouseSettings()
            s.apply(query, Array(bytes(capture["reply"] as! String).dropFirst()))   // drop report ID 03
            for (key, value) in capture["expect"] as! [String: Any] {
                switch key {
                case "profile": #expect(s.profile == value as? Int)
                case "dpi_stage": #expect(s.dpiStage == value as? Int)
                case "dpi_stage_count": #expect(s.dpiStageCount == value as? Int)
                case "firmware": #expect(s.firmware == value as? String)
                case "receiver_firmware": #expect(s.receiverFirmware == value as? String)
                case "polling_hz": #expect(s.pollingHz == value as? Int)
                case "debounce_ms": #expect(s.debounceMs == value as? Int)
                case "angle_snapping": #expect(s.angleSnapping == value as? Bool)
                case "angle_tuning": #expect(s.angleTuning == value as? Int)
                case "dpi": #expect(s.dpi == (value as! [Int]).map { Optional($0) })
                case "dpi_colors": #expect(s.dpiColors.map(\.hex) == value as! [String])
                case "liftoff": #expect(s.liftoff == value as? String)
                case "surface_id": #expect(s.surfaceID.map(Int.init) == value as? Int)
                case "surface_params": #expect(s.surfaceParams.map(ReplyParser.hex) == value as? String)
                case "logo", "wheel", "side":
                    let led = value as! [String: Any]
                    let state = s.leds[LEDZone(rawValue: key)!]
                    #expect(state?.mode.name == led["mode"] as? String, "\(key)")
                    #expect(state?.brightness == led["brightness"] as? Int, "\(key)")
                    #expect(state?.color.hex == led["color"] as? String, "\(key)")
                default: Issue.record("expect key \(key) not checked")
                }
            }
        }
    }

    /// Over the cable: no report ID, firmware at another place.
    @Test func wiredCapturesMatchTheirExpectation() throws {
        let captures = (try json()["mouse_settings_wired"] as! [String: Any])["captures"] as! [[String: Any]]
        #expect(!captures.isEmpty)
        for capture in captures {
            let query = try #require(SettingsQuery(rawValue: capture["command"] as! String))
            var s = MouseSettings(connection: .wired)
            s.apply(query, bytes(capture["reply"] as! String))
            let expect = capture["expect"] as! [String: Any]
            #expect(s.profile == expect["profile"] as? Int)
            #expect(s.dpiStage == expect["dpi_stage"] as? Int)
            #expect(s.dpiStageCount == expect["dpi_stage_count"] as? Int)
            #expect(s.firmware == expect["firmware"] as? String)
            #expect(s.receiverFirmware == nil && expect["receiver_firmware"] is NSNull)
        }
    }

    @Test func demoDataComesFromTheCaptures() throws {
        let demo = try ProtocolCaptures.load(protocolURL()).demo()
        let s = demo.settings
        #expect(s.status == .ok && s.profile == 1 && s.dpiStage == 4 && s.dpiStageCount == 4)
        #expect(s.activeDPI == [400, 800, 650, 3200])
        #expect(s.leds[.side]?.mode == .colorCycle)
        #expect(s.powerOffMinutes == 3 && s.lowWarning == 20)
        #expect(demo.batteries.map(\.device) == [.keyboard, .mouse])
    }

    @Test func summaries() throws {
        let s = try ProtocolCaptures.load(protocolURL()).demo().settings
        #expect(SettingsEditing.summary(s, .dpi) == "400 / 800 / 650 / 3200 DPI")
        #expect(SettingsEditing.summary(s, .logo) == "Color cycle · 50 %")
        #expect(SettingsEditing.summary(s, .sensor) == "1000 Hz · lift-off low")
        #expect(SettingsEditing.summary(s, .power) == "off after 3 min · warn at 20 %")
        #expect(SettingsEditing.summary(MouseSettings(), .wheel) == "not read")
    }
}

/// protocol.json `mouse_writes`: the exact bytes confirmed on the device.
@Suite struct WriteTests {
    @Test func ledWritesMatchTheConfirmedPackets() throws {
        let writes = (try json()["mouse_writes"] as! [String: Any])
        for write in writes["writes"] as! [[String: Any]] {
            let args = write["args"] as! [String: Any]
            let led = LEDState(mode: LEDMode(name: args["mode"] as! String)!, brightness: args["brightness"] as! Int,
                               color: RGB(hex: args["color"] as! String)!)
            let command = try Command.setLED(LEDZone(rawValue: args["zone"] as! String)!, led)
            let packet = try Packet.build(reportID: 0x03, command: command, length: 64)
            #expect(packet.count == 64)
            #expect(Array(packet.prefix(13)) == bytes(write["packet"] as! String), "\(write["name"]!)")
            #expect(packet.dropFirst(13).allSatisfy { $0 == 0 })
        }
        let save = (writes["save"] as! [String: Any])["packet"] as! String
        #expect(Array(try Packet.build(reportID: 0x03, command: .save, length: 64).prefix(3)) == bytes(save))
    }

    /// protocol.json mouse_writes.settings_writes: same vectors as the Python tests.
    @Test func powerAndSensorWritesMatchTheConfirmedPackets() throws {
        let section = (try json()["mouse_writes"] as! [String: Any])["settings_writes"] as! [String: Any]
        for write in section["writes"] as! [[String: Any]] {
            let args = write["args"] as! [Any]
            let command: Command
            switch write["function"] as! String {
            case "power_command":
                command = try .setPower(PowerSetting(offMinutes: args[0] as? Int, lowWarning: args[1] as! Int))
            case "angle_snapping_command": command = try .setAngleSnapping(args[0] as! Bool)
            case "angle_tuning_command": command = try .setAngleTuning(args[0] as! Int)
            case "polling_command": command = try .setPolling(args[0] as! Int)
            case "dpi_command":
                command = try .setDPI(stage: args[0] as! Int, DPIStage(dpi: args[1] as! Int, color: RGB(hex: args[2] as! String)!))
            case "active_stage_command": command = try .setActiveStage(args[0] as! Int)
            case "stage_count_command": command = try .setStageCount(args[0] as! Int)
            case "surface_command": command = try .setSurface(UInt8(args[0] as! Int), liftoff: args[1] as! String)
            case "liftoff_command":
                command = try .setLiftoff(args[0] as! String, surfaceID: UInt8(args[1] as! Int),
                                          surfaceParams: bytes(args[2] as! String))
            default: Issue.record("unknown function \(write["function"]!)"); continue
            }
            let head = bytes(write["packet"] as! String)
            let packet = try Packet.build(reportID: 0x03, command: command, length: 64)
            #expect(Array(packet.prefix(head.count)) == head, "\(write["name"]!)")
            #expect(packet.dropFirst(head.count).allSatisfy { $0 == 0 })
        }
    }

    @Test func powerAndSensorExactBytes() throws {
        #expect(try Command.setPower(PowerSetting(offMinutes: nil, lowWarning: 50)).bytes == [0x51, 0x37, 0, 0, 0xFF, 0, 0x32])
        #expect(try Command.setPower(PowerSetting(offMinutes: 1, lowWarning: 10)).bytes == [0x51, 0x37, 0, 0, 0x00, 0, 0x0A])
        #expect(try Command.setAngleSnapping(false).bytes == [0x51, 0x31, 0x06, 0x00, 0x00])
        #expect(try Command.setAngleTuning(-20).bytes == [0x51, 0x31, 0x0B, 0x00, 0xEC, 0xFF])
        #expect(try Command.setAngleTuning(15).bytes == [0x51, 0x31, 0x0B, 0x00, 0x0F, 0x00])
    }

    @Test func invalidPowerAndSensorValuesAreRefused() {
        #expect(throws: CommandError.self) { try Command.setPower(PowerSetting(offMinutes: 4, lowWarning: 20)) }
        #expect(throws: CommandError.self) { try Command.setPower(PowerSetting(offMinutes: 5, lowWarning: 25)) }
        #expect(throws: CommandError.self) { try Command.setPower(PowerSetting(offMinutes: 5, lowWarning: 60)) }
        #expect(throws: CommandError.self) { try Command.setAngleTuning(7) }
        #expect(throws: CommandError.self) { try Command.setAngleTuning(25) }
    }

    @Test(arguments: ["51 31 00 00 07 00", "51 31 04 00 04", "51 31 04 00 63", "51 31 05 00 02", "51 31 09 00 05", "51 31 0a 00 00",
                      "51 31 00 00 48 03 ff 00 00", "51 31 00 00 00 00 ff 00 00", "51 31 04 00 07 00 ff 00 00",
                      "51 35 ff 00 ff 02", "51 35 00 00 02 00 00 1c 34 16", "51 35 00 00 01 00 00 1c 34",
                      "51 35 00 00 01 00 01 1c 34 16", "51 31 06 00 02", "51 31 0b 00 07 00", "51 37 00 00 05 00 14",
                      "51 37 00 01 03 00 14", "50 02 01"])
    func everythingElseStaysRefused(hex: String) {
        #expect(!Allowlist.isAllowedWrite(bytes(hex)))
        #expect(throws: CommandError.self) {
            try Packet.build(reportID: 0x03, command: Command(bytes(hex), isWrite: true), length: 64)
        }
    }

    /// Same as test_squire.py LiftoffWriteTests: the surface read from 12 06 is written back unchanged.
    @Test func liftoffKeepsTheSurface() throws {
        #expect(try Command.setLiftoff("high", surfaceID: 0x01, surfaceParams: [0x1C, 0x34, 0x16]).bytes
                == bytes("51 35 00 00 01 01 00 1c 34 16"))
        #expect(try Command.setLiftoff("low", surfaceID: 0x0B, surfaceParams: [0x1A, 0x32, 0x20]).bytes
                == bytes("51 35 00 00 0b 00 00 1a 32 20"))
        #expect(try Command.setLiftoff("high", surfaceID: 0xFF, surfaceParams: nil).bytes == bytes("51 35 ff 00 ff 01"))
        #expect(throws: CommandError.self) { try Command.setLiftoff("high", surfaceID: 0x02, surfaceParams: [1, 2, 3]) }
        #expect(throws: CommandError.self) { try Command.setLiftoff("high", surfaceID: nil, surfaceParams: nil) }
        #expect(throws: CommandError.self) { try Command.setLiftoff("medium", surfaceID: 0xFF, surfaceParams: nil) }
        #expect(throws: CommandError.self) { try SettingChange.liftoff("high").command() }   // no read: nothing to keep
        #expect(SettingChange.liftoff("high").needsCurrent && SettingChange.liftoff("high").readBack == .liftoff)
    }

    /// protocol.json mouse_writes.armoury_crate: Squire builds the same packets Armoury Crate sent, and parses its reads.
    @Test func liftoffMatchesTheArmouryCrateCaptures() throws {
        let section = (try json()["mouse_writes"] as! [String: Any])["armoury_crate"] as! [String: Any]
        let captures = section["captures"] as! [[String: Any]]
        let sheath = captures.first { $0["name"] as? String == "lift-off low -> high (surface ROG Sheath)" }!
        let command = try Command.setLiftoff("high", surfaceID: 0x01, surfaceParams: [0x1C, 0x34, 0x16])
        #expect(Array(try Packet.build(reportID: 0x03, command: command, length: 64).prefix(11))
                == bytes((sheath["packets"] as! [String])[0]))
        var s = MouseSettings()
        let read = bytes(sheath["read_after"] as! String)
        s.apply(.liftoff, Array((read + [UInt8](repeating: 0, count: 64 - read.count)).dropFirst()))
        #expect(s.liftoff == "high" && s.surfaceID == 0x01 && s.surfaceParams == [0x1C, 0x34, 0x16])
        let presets = captures.compactMap { $0["surfaces"] as? [[String: Any]] }.first!
        for preset in presets {
            let id = UInt8(preset["id"] as! String, radix: 16)!
            let params = (preset["params"] as? String).map(bytes)
            let name = (preset["name"] as! String).replacingOccurrences(of: " (second tile, same name)", with: " (2)")
            #expect(Command.surfaceName(id) == (id == 0xFF ? "None (no calibration)" : name))
            let c = try Command.setLiftoff("low", surfaceID: id, surfaceParams: params)
            let expected = preset["packet"] as? String ?? "03 51 35 00 00 \(preset["id"]!) 00 00 \(preset["params"]!)"
            #expect(Array(try Packet.build(reportID: 0x03, command: c, length: 64).prefix(c.bytes.count + 1)) == bytes(expected))
        }
        #expect(Command.surfaces.count == presets.count - 1)
    }

    /// Same as test_squire.py test_polling_write / test_surface_keeps_the_liftoff.
    @Test func pollingAndSurfaceExactBytes() throws {
        #expect(try Command.setPolling(1000).bytes == bytes("51 31 04 00 03"))
        #expect(try Command.setPolling(125).bytes == bytes("51 31 04 00 00"))
        #expect(throws: CommandError.self) { try Command.setPolling(2000) }
        #expect(try Command.setSurface(0x0B, liftoff: "high").bytes == bytes("51 35 00 00 0b 01 00 1a 32 20"))
        #expect(try Command.setSurface(0xFF, liftoff: "low").bytes == bytes("51 35 ff 00 ff 00"))
        #expect(throws: CommandError.self) { try Command.setSurface(0x02, liftoff: "low") }
        var unknown = MouseSettings()
        unknown.liftoff = "low"; unknown.surfaceID = 0x02; unknown.surfaceParams = [1, 2, 3]
        #expect(throws: CommandError.self) { try SettingChange.surface(0x01).command(current: unknown) }   // a calibration?
        #expect(Command.surfaceChoices.first == 0xFF && Set(Command.surfaceChoices) == Set(Command.surfaceParams.keys))
    }

    @Test func surfaceParamsMatchTheCapturedPresets() throws {
        let section = (try json()["mouse_writes"] as! [String: Any])["armoury_crate"] as! [String: Any]
        let presets = (section["captures"] as! [[String: Any]]).compactMap { $0["surfaces"] as? [[String: Any]] }.first!
        #expect(presets.count == Command.surfaceParams.count)
        for preset in presets {
            let id = UInt8(preset["id"] as! String, radix: 16)!
            #expect(Command.surfaceParams[id] == .some((preset["params"] as? String).map(bytes)), "\(preset["name"]!)")
        }
    }

    /// Same as test_squire.py DpiWriteTests.
    @Test func dpiExactBytes() throws {
        #expect(try Command.setDPI(stage: 3, DPIStage(dpi: 700, color: RGB(0x00, 0x3D, 0xFF))).bytes
                == bytes("51 31 02 00 0d 00 00 3d ff"))
        #expect(try Command.setDPI(stage: 1, DPIStage(dpi: 42000, color: RGB(0xFF, 0, 0))).bytes
                == bytes("51 31 00 00 47 03 ff 00 00"))
        #expect(try Command.setDPI(stage: 1, DPIStage(dpi: 100, color: RGB(0xFF, 0, 0))).bytes
                == bytes("51 31 00 00 01 00 ff 00 00"))
        #expect(try Command.setActiveStage(3).bytes == bytes("51 31 09 00 03"))
        #expect(try Command.setStageCount(4).bytes == bytes("51 31 0a 00 04"))
        for (stage, dpi) in [(0, 800), (5, 800), (1, 825), (1, 50), (1, 42050)] {
            #expect(throws: CommandError.self) { try Command.setDPI(stage: stage, DPIStage(dpi: dpi, color: RGB(0, 0, 0))) }
        }
        #expect(throws: CommandError.self) { try Command.setActiveStage(5) }
        #expect(throws: CommandError.self) { try Command.setStageCount(0) }
    }

    @Test func dpiMatchesTheArmouryCrateCapture() throws {
        let section = (try json()["mouse_writes"] as! [String: Any])["armoury_crate"] as! [String: Any]
        let capture = (section["captures"] as! [[String: Any]]).first { ($0["name"] as! String).hasPrefix("DPI stage 3") }!
        let after = capture["after"] as! [String: Any]
        let dpi = after["dpi"] as! [Int], colors = after["dpi_colors"] as! [String]
        var commands = try (0..<4).map { try Command.setDPI(stage: $0 + 1, DPIStage(dpi: dpi[$0], color: RGB(hex: colors[$0])!)) }
        commands += [try Command.setStageCount(4), try Command.setActiveStage(after["dpi_stage"] as! Int)]
        let packets = Set(capture["packets"] as! [String])
        for c in commands {
            let head = Array(try Packet.build(reportID: 0x03, command: c, length: 64).prefix(c.bytes.count + 1))
            #expect(packets.contains(ReplyParser.hex(head)), "\(ReplyParser.hex(head))")
        }
    }

    @Test func dpiChecksAgainstTheMouseAsRead() throws {
        var s = MouseSettings()
        s.dpiXY = [[400, 400], [800, 1200], [650, 650], [3200, 3200]]
        s.dpiColors = Array(repeating: RGB(0, 0, 0), count: 4)
        s.dpiStage = 2; s.dpiStageCount = 3
        let blue = DPIStage(dpi: 900, color: RGB(0, 0, 0xFF))
        #expect(throws: CommandError.self) { try SettingChange.dpi(stage: 2, blue).command(current: s) }   // X != Y
        #expect(try SettingChange.dpi(stage: 3, blue).command(current: s).bytes == bytes("51 31 02 00 11 00 00 00 ff"))
        #expect(throws: CommandError.self) { try SettingChange.activeStage(4).command(current: s) }   // beyond the count
        #expect(throws: CommandError.self) { try SettingChange.stageCount(1).command(current: s) }    // below the active
        #expect(try SettingChange.stageCount(2).command(current: s).bytes == bytes("51 31 0a 00 02"))
        #expect(SettingChange.dpi(stage: 1, blue).reads == [.dpiXY, .dpiColors])
        var after = s
        after.dpiXY[2] = [900, 900]; after.dpiColors[2] = RGB(0, 0, 0xFF)
        #expect(SettingChange.dpi(stage: 3, blue).matches(after) && !SettingChange.dpi(stage: 3, blue).matches(s))
    }

    @Test func liftoffReadBackMustKeepTheSurface() {
        var before = MouseSettings()
        before.liftoff = "low"; before.surfaceID = 0x01; before.surfaceParams = [0x1C, 0x34, 0x16]
        var after = before
        after.liftoff = "high"
        #expect(SettingChange.liftoff("high").matches(after, before: before))
        after.surfaceID = 0xFF; after.surfaceParams = nil      // surface lost, like G-Helper's packet
        #expect(!SettingChange.liftoff("high").matches(after, before: before))
    }

    @Test func settingChangeReadBackAndMatch() {
        var s = MouseSettings()
        s.powerOffMinutes = nil; s.lowWarning = 20; s.angleSnapping = false; s.angleTuning = -5
        #expect(SettingChange.power(PowerSetting(offMinutes: nil, lowWarning: 20)).matches(s))
        #expect(!SettingChange.power(PowerSetting(offMinutes: 3, lowWarning: 20)).matches(s))
        #expect(SettingChange.angleSnapping(false).matches(s) && !SettingChange.angleSnapping(true).matches(s))
        #expect(SettingChange.angleTuning(-5).matches(s))
        #expect(SettingChange.power(PowerSetting(offMinutes: 3, lowWarning: 20)).readBack == nil)   // 12 07
        #expect(SettingChange.angleTuning(0).readBack == .settings)
    }

    @Test func wiredWriteIsUnnumbered65Bytes() throws {
        let command = try Command.setLED(.wheel, LEDState(mode: .static, brightness: 100, color: RGB(0, 0, 0xFF)))
        let packet = try Packet.build(reportID: nil, command: command, length: 65)
        #expect(packet.count == 65)
        #expect(Array(packet.prefix(13)) == [0x00, 0x51, 0x28, 0x01, 0x00, 0x00, 0x64, 0x00, 0x00, 0xFF, 0, 0, 0])
    }

    @Test(arguments: [LEDMode.rainbow, .comet, .unknown(0x07)])
    func effectsWithoutKnownBytesAreRefused(mode: LEDMode) {
        #expect(throws: CommandError.self) {
            try Command.setLED(.logo, LEDState(mode: mode, brightness: 50, color: RGB(1, 2, 3)))
        }
    }

    @Test func brightnessAbove100IsRefused() {
        #expect(throws: CommandError.self) {
            try Command.setLED(.logo, LEDState(mode: .static, brightness: 101, color: RGB(1, 2, 3)))
        }
    }
}

@Suite struct AllowlistTests {
    @Test func everyReadIsAGetRequest() {
        #expect(Allowlist.reads.count == 11)   // 2 battery + 8 mouse settings + keyboard 12 12 (12 00 is shared)
        #expect(Allowlist.reads.allSatisfy { $0.first == 0x12 })
    }

    @Test func profileSwitchIsNeverAllowed() {
        #expect(!Allowlist.isAllowedWrite([0x50, 0x02]))
        #expect(!Allowlist.isAllowedWrite([0x50, 0x02, 0x01]))
    }

    @Test func writeBytesAreRefusedOnTheReadPath() {
        // a write command marked as a read must not pass, and a read marked as a write must not either
        #expect(throws: CommandError.self) { try Packet.build(reportID: 3, command: Command([0x50, 0x03]), length: 64) }
        #expect(throws: CommandError.self) {
            try Packet.build(reportID: 3, command: Command([0x12, 0x07], isWrite: true), length: 64)
        }
        #expect(throws: CommandError.self) { try Packet.build(reportID: 3, command: Command([0x12, 0x05]), length: 64) }
    }

    @Test func malformedLEDWritesAreRefused() {
        let ok: [UInt8] = [0x51, 0x28, 0x00, 0x00, 0x00, 0x64, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
        #expect(Allowlist.isAllowedWrite(ok))
        var zone = ok; zone[2] = 0x03; #expect(!Allowlist.isAllowedWrite(zone))
        var speed = ok; speed[11] = 0x05; #expect(!Allowlist.isAllowedWrite(speed))
        var bright = ok; bright[5] = 101; #expect(!Allowlist.isAllowedWrite(bright))
        #expect(!Allowlist.isAllowedWrite(Array(ok.dropLast())))
    }

    @Test func batteryPacketsGoThroughTheAllowlist() throws {
        for target in Target.all {
            for length in target.reportLengths {
                #expect(try Packet.build(reportID: target.reportID, command: target.command, length: length)
                        == target.packet(length: length))
            }
        }
    }
}

@Suite struct ExchangeTests {
    private func reply(_ head: [UInt8]) -> [UInt8] { head + [UInt8](repeating: 0, count: 64 - head.count) }

    @Test func provisionalErrorThenReply() {
        let ex = ReplyParser.exchange(replies: [reply([0x03, 0xFF, 0xAA]), reply([0x03, 0x12, 0x03, 0x01, 0x00, 0x02])],
                                      echo: [0x12, 0x03, 0x01], reportID: 0x03)
        #expect(ex.status == .ok)
        #expect(ex.base == 1)
        #expect(Array(ex.payload.prefix(3)) == [0x12, 0x03, 0x01])
    }

    @Test func onlyErrorMeansAsleep() {
        #expect(ReplyParser.exchange(replies: [reply([0x03, 0xFF, 0xAA])], echo: [0x12, 0x00], reportID: 0x03).status == .asleep)
        #expect(ReplyParser.exchange(replies: [], echo: [0x12, 0x00], reportID: 0x03).status == .noResponse)
    }

    @Test func ledReplyOfAnotherZoneIsSkipped() {
        let ex = ReplyParser.exchange(replies: [reply([0x03, 0x12, 0x03, 0x00])], echo: [0x12, 0x03, 0x01], reportID: 0x03)
        #expect(ex.status == .noResponse)
    }
}

@Suite struct EditingTests {
    private func settings() throws -> MouseSettings { try ProtocolCaptures.load(protocolURL()).demo().settings }

    @Test func editAndRevertBySettingBack() throws {
        let s = try settings()
        let current = s.leds[.logo]!
        var pending = SettingsEditing.edit([:], zone: .logo, current: current) { $0.mode = .static }
        #expect(SettingsEditing.changedZones(s, pending: pending) == [.logo])
        pending = SettingsEditing.edit(pending, zone: .logo, current: current) { $0.mode = .colorCycle }
        #expect(pending.isEmpty)
    }

    @Test func colourIsIgnoredForMultiColourEffects() throws {
        let s = try settings()
        let pending = SettingsEditing.edit([:], zone: .wheel, current: s.leds[.wheel]!) { $0.color = RGB(1, 2, 3) }
        #expect(SettingsEditing.changedZones(s, pending: pending).isEmpty)
    }

    /// Same plan as test_squire_settings.py test_power_and_sensor_edits_plan_exact_bytes.
    @Test func powerAndSensorEditsPlanExactBytes() throws {
        let s = try settings()   // off after 3 min, warning 20 %, snapping off, tuning 0°
        var other = SettingsEditing.edit(OtherEdits(), s) {
            $0.power = PowerSetting(offMinutes: 10, lowWarning: 30)
            $0.angleSnapping = true
            $0.angleTuning = -5
        }
        let blue = LEDState(mode: .static, brightness: 100, color: RGB(0x3D, 0x8B, 0xFF))
        #expect(SettingsEditing.plannedPackets(s, pending: [.logo: blue], other: other)
                == ["03 51 28 00 00 00 64 3d 8b ff 00 00 00 …", "03 51 37 00 00 04 00 1e …",
                    "03 51 31 06 00 01 …", "03 51 31 0b 00 fb ff …", "03 50 03 …  (save)"])
        #expect(SettingsEditing.applyStatus(s, pending: [:], other: other).contains("Power, Angle snapping, Angle tuning"))
        let preview = SettingsEditing.preview(s, pending: [:], other: other)
        #expect(SettingsEditing.summary(preview, .power) == "off after 10 min · warn at 30 %")
        // back to the mouse's values: dropped
        other = SettingsEditing.edit(other, s) {
            $0.power = PowerSetting(offMinutes: 3, lowWarning: 20)
            $0.angleSnapping = false
            $0.angleTuning = 0
        }
        #expect(other.isEmpty)
        #expect(SettingsEditing.canEditPower(s))
        var odd = s
        odd.lowWarning = 15
        #expect(!SettingsEditing.canEditPower(odd))
    }

    /// Same plan as test_squire_settings.py test_liftoff_edit_keeps_the_surface.
    @Test func liftoffEditPlansExactBytes() throws {
        let s = try settings()   // demo capture: no surface, lift-off low
        #expect(s.surfaceID == 0xFF && s.liftoff == "low" && SettingsEditing.canEditLiftoff(s))
        let other = SettingsEditing.edit(OtherEdits(), s) { $0.liftoff = "high" }
        #expect(SettingsEditing.plannedPackets(s, pending: [:], other: other) == ["03 51 35 ff 00 ff 01 …", "03 50 03 …  (save)"])
        #expect(SettingsEditing.summary(SettingsEditing.preview(s, pending: [:], other: other), .sensor) == "1000 Hz · lift-off high")
        var sheath = s
        sheath.surfaceID = 0x01; sheath.surfaceParams = [0x1C, 0x34, 0x16]
        #expect(SettingsEditing.plannedPackets(sheath, pending: [:], other: other).first == "03 51 35 00 00 01 01 00 1c 34 16 …")
        #expect(SettingsEditing.edit(other, s) { $0.liftoff = "low" }.isEmpty)
        var unknown = s
        unknown.surfaceID = 0x02; unknown.surfaceParams = [0x1C, 0x34, 0x16]
        #expect(!SettingsEditing.canEditLiftoff(unknown))
    }

    /// Same plan as test_squire_settings.py test_polling_and_surface_plan_exact_bytes.
    @Test func pollingAndSurfacePlanExactBytes() throws {
        var s = try settings()
        s.surfaceID = 0x01; s.surfaceParams = [0x1C, 0x34, 0x16]
        let other = SettingsEditing.edit(OtherEdits(), s) { $0.polling = 500; $0.surface = 0x0B; $0.liftoff = "high" }
        #expect(SettingsEditing.plannedPackets(s, pending: [:], other: other)
                == ["03 51 31 04 00 02 …", "03 51 35 00 00 0b 00 00 1a 32 20 …",
                    "03 51 35 00 00 0b 01 00 1a 32 20 …", "03 50 03 …  (save)"])
        let preview = SettingsEditing.preview(s, pending: [:], other: other)
        #expect(preview.pollingHz == 500 && preview.surfaceID == 0x0B && preview.surfaceParams == [0x1A, 0x32, 0x20])
        #expect(SettingsEditing.edit(OtherEdits(), s) { $0.surface = 0x01 }.isEmpty)
        #expect(SettingsEditing.canEditSurface(s) && SettingsEditing.canEditPolling(s))
        #expect(SettingsEditing.plannedPackets(s, pending: [:], other: OtherEdits(surface: 0xFF)).first == "03 51 35 ff 00 ff 00 …")
    }

    /// Same plan as test_squire_settings.py test_dpi_edits_plan_exact_bytes.
    @Test func dpiEditsPlanExactBytes() throws {
        let s = try settings()   // 400 / 800 / 650 / 3200, #ff0000 #c100ff #000000 #31ff00, stage 4 of 4
        var other = SettingsEditing.edit(OtherEdits(), s) {
            $0.dpi[3] = DPIStage(dpi: 700, color: RGB(0, 0, 0))
            $0.dpi[1] = DPIStage(dpi: 400, color: RGB(0x3D, 0x8B, 0xFF))   // colour only
        }
        other = SettingsEditing.editDPIStages(other, s, active: 3)
        #expect(SettingsEditing.plannedPackets(s, pending: [:], other: other)
                == ["03 51 31 00 00 07 00 3d 8b ff …", "03 51 31 02 00 0d 00 00 00 00 …",
                    "03 51 31 09 00 03 …", "03 50 03 …  (save)"])
        let preview = SettingsEditing.preview(s, pending: [:], other: other)
        #expect(SettingsEditing.summary(preview, .dpi) == "400 / 800 / 700 / 3200 DPI")
        #expect(SettingsEditing.edit(OtherEdits(), s) { $0.dpi[2] = DPIStage(dpi: 800, color: RGB(0xC1, 0x00, 0xFF)) }.isEmpty)
    }

    @Test func activeStageAndCountStayConsistent() throws {
        let s = try settings()
        let fewer = SettingsEditing.editDPIStages(OtherEdits(), s, count: 2)
        #expect(fewer.activeStage == 2 && fewer.stageCount == 2)
        #expect(Array(SettingsEditing.plannedPackets(s, pending: [:], other: fewer).prefix(2))
                == ["03 51 31 09 00 02 …", "03 51 31 0a 00 02 …"])
        var two = s
        two.dpiStage = 2; two.dpiStageCount = 2
        let more = SettingsEditing.editDPIStages(OtherEdits(), two, active: 4)
        #expect(Array(SettingsEditing.plannedPackets(two, pending: [:], other: more).prefix(2))
                == ["03 51 31 0a 00 04 …", "03 51 31 09 00 04 …"])
        #expect(SettingsEditing.editDPIStages(more, two, active: 2, count: 2).isEmpty)
    }

    @Test func linkedEditSetsEveryZone() throws {
        let s = try settings()
        let blue = LEDState(mode: .static, brightness: 100, color: RGB(0x3D, 0x8B, 0xFF))
        let pending = SettingsEditing.edit(s, [:], zone: .logo, linked: true) { $0 = blue }
        #expect(SettingsEditing.changedZones(s, pending: pending) == [.logo, .wheel, .side])
        #expect(LEDZone.allCases.allSatisfy { pending[$0] == blue })
        let lines = SettingsEditing.plannedPackets(s, pending: pending)
        #expect(lines.count == 4 && lines.last == "03 50 03 …  (save)")
        // not linked: only the edited zone
        #expect(Array(SettingsEditing.edit(s, [:], zone: .side, linked: false) { $0 = blue }.keys) == [.side])
    }

    @Test func linkingCopiesTheZoneAndSkipsZonesThatAlreadyMatch() throws {
        var s = try settings()   // all three: colour cycle 50 %
        let orange = LEDState(mode: .static, brightness: 80, color: RGB(0xF7, 0x80, 0x13))
        s.leds[.logo] = orange
        s.leds[.side] = orange
        let pending = SettingsEditing.edit(s, [:], zone: .logo, linked: true)   // the switch turned on
        #expect(SettingsEditing.changedZones(s, pending: pending) == [.wheel])
        #expect(pending[.wheel] == orange)
    }

    @Test func effectSquireCannotWriteIsNotLinked() throws {
        var s = try settings()
        s.leds[.logo] = LEDState(mode: .rainbow, brightness: 50, color: RGB(0xFF, 0, 0))
        #expect(!SettingsEditing.canLink(s, pending: [:], zone: .logo))
        #expect(SettingsEditing.canLink(s, pending: [.logo: LEDState(mode: .static, brightness: 50, color: RGB(0xFF, 0, 0))],
                                        zone: .logo))
        #expect(SettingsEditing.canLink(s, pending: [:], zone: .wheel))
    }

    @Test func plannedPacketsWriteEachZoneAndSaveOnce() throws {
        let s = try settings()
        let blue = LEDState(mode: .static, brightness: 100, color: RGB(0x3D, 0x8B, 0xFF))
        let lines = SettingsEditing.plannedPackets(s, pending: [.side: blue, .logo: blue])
        #expect(lines == ["03 51 28 00 00 00 64 3d 8b ff 00 00 00 …",
                          "03 51 28 02 00 00 64 3d 8b ff 00 00 00 …",
                          "03 50 03 …  (save)"])
        #expect(SettingsEditing.plannedPackets(s, pending: [:]).isEmpty)
        #expect(SettingsEditing.applyStatus(s, pending: [.side: blue]).hasPrefix("Not sent yet: Side light."))
    }

    @Test func wiredPlannedPacketsAreUnnumbered() throws {
        var s = try settings()
        s.connection = .wired
        let lines = SettingsEditing.plannedPackets(s, pending: [.logo: LEDState(mode: .off, brightness: 0, color: RGB(0, 0, 0))])
        #expect(lines.first == "00 51 28 00 00 f0 00 00 00 00 00 00 00 …")
        #expect(lines.last == "00 50 03 …  (save)")
    }

    @Test func previewShowsPendingValues() throws {
        let s = try settings()
        let off = LEDState(mode: .off, brightness: 50, color: RGB(0, 0, 0))
        #expect(SettingsEditing.summary(SettingsEditing.preview(s, pending: [.logo: off]), .logo) == "Off")
    }
}
