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
                case "polling_hz": #expect(s.pollingHz == value as? Int)
                case "debounce_ms": #expect(s.debounceMs == value as? Int)
                case "angle_snapping": #expect(s.angleSnapping == value as? Bool)
                case "angle_tuning": #expect(s.angleTuning == value as? Int)
                case "dpi": #expect(s.dpi == (value as! [Int]).map { Optional($0) })
                case "dpi_colors": #expect(s.dpiColors.map(\.hex) == value as! [String])
                case "liftoff": #expect(s.liftoff == value as? String)
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
        #expect(Allowlist.reads.count == 10)
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
