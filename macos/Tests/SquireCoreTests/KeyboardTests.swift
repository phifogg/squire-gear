import Foundation
import Testing
@testable import SquireCore

private func protocolURL() -> URL {
    // macos/Tests/SquireCoreTests/<this file> -> repo root
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("protocol.json")
}

private func bytes(_ hex: String) -> [UInt8] { hex.split(separator: " ").compactMap { UInt8($0, radix: 16) } }

/// protocol.json `keyboard_settings`: the same captures and `expect` values the Python tests use.
@Suite struct KeyboardSettingsCaptureTests {
    @Test func everyCaptureMatchesItsExpectation() throws {
        let json = try JSONSerialization.jsonObject(with: Data(contentsOf: protocolURL())) as! [String: Any]
        let captures = (json["keyboard_settings"] as! [String: Any])["captures"] as! [[String: Any]]
        #expect(captures.count >= 4)
        for capture in captures {
            let name = capture["command"] as! String
            let query = try #require(KeyboardQuery(rawValue: name), "unknown command \(name)")
            var k = KeyboardSettings(connection: .omni)
            let reply = bytes(capture["reply"] as! String)
            #expect(ReplyParser.echoIndex(in: reply, query: query.command.bytes) == 1)
            k.apply(query, Array(reply.dropFirst()))   // drop report ID 02
            for (key, value) in capture["expect"] as! [String: Any] {
                switch key {
                case "profile": #expect(k.profile == value as? Int)
                case "light_mode": #expect(k.lightMode == value as? String)
                case "receiver_firmware": #expect(k.receiverFirmware == value as? String)
                case "layout_id": #expect(k.layoutID == value as? Int)
                case "layout": #expect(k.layout == value as? String)
                case "iso": #expect(k.iso == value as? Bool)
                default: Issue.record("expect key \(key) not checked")
                }
            }
        }
    }

    @Test func demoShowsProfileOne() throws {
        let k = try ProtocolCaptures.load(protocolURL()).demoKeyboard()
        #expect(k.profile == 1)
        #expect(k.lightMode == "reactive")
        #expect(k.layoutText == "CH-DE · ISO")
        #expect(k.lowWarning == 20)
        #expect(k.receiverFirmware == "07.00.08")
        #expect(KeyboardZone.power.summary(k).hasSuffix("warn at 20 %"))
    }

    @Test func unknownValuesStayRaw() {
        var k = KeyboardSettings(connection: .omni)
        k.apply(.layout, [0x12, 0x12, 0, 0, 0x02, 0x63] + [UInt8](repeating: 0, count: 58))
        #expect(k.layoutID == 0x63 && k.layout == nil && k.iso == false)
        #expect(k.layoutText == "Unknown (id 99) · ANSI")
        k.apply(.profile, [0x12, 0x00] + [UInt8](repeating: 0, count: 7) + [0xF1, 0x01] + [UInt8](repeating: 0, count: 53))
        #expect(k.lightMode == "host-driven" && k.profile == 1 && k.receiverFirmware == nil)
        #expect(KeyboardSettings.effectTitle(k.lightMode) == "Controlled by an app")
        #expect(KeyboardSettings.effectTitle("starry-night") == "Starry night")
        #expect(KeyboardSettings.effectTitle("current") == "Current")
    }

    @Test func keyboardReadsAreGetRequestsOnTheAllowlist() throws {
        for query in KeyboardQuery.allCases {
            #expect(query.command.bytes[0] == 0x12)
            #expect(!query.command.isWrite)
            #expect(Allowlist.isAllowed(query.command))
            _ = try Packet.build(reportID: 0x02, command: query.command, length: 64)
        }
        // the mouse's LED read was probed once; it is not a keyboard read
        #expect(!KeyboardQuery.allCases.map(\.command.bytes).contains([0x12, 0x03, 0x00]))
    }
}

@Suite struct KeyboardLayoutTests {
    @Test func everyRowIsSixteenUnits() {
        for rows in [KeyboardLayout.iso, KeyboardLayout.ansi] {
            for row in rows { #expect(row.reduce(0) { $0 + $1.width } == 16) }
        }
    }

    @Test func isoForChDeAnsiForUs() {
        var ch = KeyboardSettings(); ch.iso = true
        var us = KeyboardSettings(); us.iso = false
        let isoLegends = KeyboardLayout.rows(for: ch).flatMap { $0.map(\.legend) }
        let ansiLegends = KeyboardLayout.rows(for: us).flatMap { $0.map(\.legend) }
        #expect(isoLegends.contains("<") && isoLegends.contains("Z") && isoLegends.contains(KeyboardLayout.isoEnter))
        #expect(!ansiLegends.contains("<") && ansiLegends.contains("Enter"))
        #expect(KeyboardLayout.rows(for: nil) == KeyboardLayout.iso)   // not read yet: ISO
    }
}
