import Foundation
import Testing
@testable import SquireCore

/// Mouse reply as the device sends it: [reportID?] 12 07 .. with fields at the documented offsets.
private func mouseReply(percent: UInt8, charging: Bool = false, warning: UInt8 = 20,
                        reportID: UInt8? = 0x03) -> [UInt8] {
    var payload = [UInt8](repeating: 0, count: 63)
    payload[0] = 0x12; payload[1] = 0x07
    payload[4] = percent
    payload[6] = warning
    payload[9] = charging ? 1 : 0
    return (reportID.map { [$0] } ?? []) + payload
}

@Suite struct PacketTests {
    @Test func omniMousePacket() {
        let packet = Target.omniMouse.packet(length: 64)
        #expect(packet.count == 64)
        #expect(Array(packet.prefix(3)) == [0x03, 0x12, 0x07])
        #expect(packet.dropFirst(3).allSatisfy { $0 == 0 })
    }

    @Test func wiredMousePacketIsUnnumbered() {
        let packet = Target.wiredMouse.packet(length: 65)
        #expect(packet.count == 65)
        #expect(Array(packet.prefix(3)) == [0x00, 0x12, 0x07])
    }

    @Test func onlyTheDocumentedQueryIsEverBuilt() {
        for target in Target.mouseTargets {
            #expect(target.query == [0x12, 0x07])
        }
    }

    @Test func readTimeoutIsNeverZero() {
        #expect(SquireProtocol.readTimeoutMs > 0)   // 0 = block forever
    }
}

@Suite struct InterpretTests {
    private let omni = Target.omniMouse
    private let wired = Target.wiredMouse

    @Test func omniMouse() {
        let b = ReplyParser.interpret(replies: [mouseReply(percent: 64, charging: true)], target: omni)
        #expect(b.percent == 64)
        #expect(b.charging)
        #expect(b.status == .ok)
        #expect(b.lowWarning == 20)
    }

    @Test func wiredReplyHasNoReportID() {
        let b = ReplyParser.interpret(replies: [mouseReply(percent: 76, charging: true, reportID: nil)], target: wired)
        #expect(b.percent == 76)
        #expect(b.charging)
    }

    @Test func skipsUnrelatedReports() {
        let event: [UInt8] = [0x03, 0x12, 0x01, 0x05] + [UInt8](repeating: 0, count: 60)
        let b = ReplyParser.interpret(replies: [event, mouseReply(percent: 40)], target: omni)
        #expect(b.percent == 40)
    }

    @Test func errorReplyMeansAsleep() {
        let reply: [UInt8] = [0x03, 0xFF, 0xAA] + [UInt8](repeating: 0, count: 61)
        let b = ReplyParser.interpret(replies: [reply], target: omni)
        #expect(b.percent == nil)
        #expect(b.status == .asleep)
    }

    @Test func zeroAndNotChargingMeansAsleep() {
        #expect(ReplyParser.interpret(replies: [mouseReply(percent: 0)], target: omni).status == .asleep)
    }

    @Test func zeroButChargingIsValid() {
        let b = ReplyParser.interpret(replies: [mouseReply(percent: 0, charging: true)], target: omni)
        #expect(b.percent == 0)
        #expect(b.charging)
        #expect(b.status == .ok)
    }

    @Test func noResponse() {
        #expect(ReplyParser.interpret(replies: [], target: omni).status == .noResponse)
        #expect(ReplyParser.interpret(replies: [[]], target: omni).status == .noResponse)
    }

    @Test func implausiblePercentIsAsleep() {
        #expect(ReplyParser.interpret(replies: [mouseReply(percent: 200)], target: omni).status == .asleep)
    }

    @Test(arguments: [0, 101, 255])
    func implausibleWarningLevelIsNil(value: Int) {
        let b = ReplyParser.interpret(replies: [mouseReply(percent: 50, warning: UInt8(value))], target: omni)
        #expect(b.lowWarning == nil)
    }
}

@Suite struct LowBatteryTests {
    private func mouse(_ percent: Int?, charging: Bool = false, warning: Int? = 20,
                       status: Battery.Status = .ok) -> Battery {
        Battery(device: .mouse, name: "ROG Keris II Origin", connection: .omni, percent: percent,
                charging: charging, status: status, lowWarning: warning)
    }

    @Test func notifiesAtDeviceLevelNotAFixedOne() {
        let result = LowBattery.evaluate([mouse(25, warning: 30)], state: .init())
        #expect(result.toNotify.count == 1)
    }

    @Test func noNotificationAboveDeviceLevel() {
        #expect(LowBattery.evaluate([mouse(25, warning: 20)], state: .init()).toNotify.isEmpty)
    }

    @Test func notifiesOnceUntilRecovered() {
        var state = NotificationState()
        var sent = 0
        for battery in [mouse(19), mouse(18), mouse(18, charging: true), mouse(17)] {
            let result = LowBattery.evaluate([battery], state: state)
            sent += result.toNotify.count
            state = result.state
        }
        #expect(sent == 2)
    }

    @Test func asleepDeviceKeepsState() {
        var state = LowBattery.evaluate([mouse(10)], state: .init()).state
        state = LowBattery.evaluate([mouse(nil, warning: nil, status: .asleep)], state: state).state
        #expect(LowBattery.evaluate([mouse(9)], state: state).toNotify.isEmpty)
    }

    @Test func fallsBackToDefaultWithoutDeviceLevel() {
        let result = LowBattery.evaluate([mouse(SquireProtocol.defaultLowWarning, warning: nil)], state: .init())
        #expect(result.toNotify.count == 1)
    }

    @Test func stateRoundTripsThroughJSON() throws {
        let state = NotificationState(notified: ["mouse"])
        let decoded = try JSONDecoder().decode(NotificationState.self, from: JSONEncoder().encode(state))
        #expect(decoded == state)
    }
}

/// Real replies captured from hardware, shared with the Python implementation via protocol.json.
@Suite struct RealCaptureTests {
    struct Vectors: Decodable {
        struct Vector: Decodable {
            struct Expect: Decodable {
                let percent: Int
                let charging: Bool
                let lowWarning: Int?
                enum CodingKeys: String, CodingKey { case percent, charging, lowWarning = "low_warning" }
            }
            let device: String
            let connection: String
            let reportId: Int?
            let reply: String
            let expect: Expect
            enum CodingKeys: String, CodingKey { case device, connection, reportId = "report_id", reply, expect }
        }
        let vectors: [Vector]
    }

    static func load() throws -> Vectors {
        // macos/Tests/SquireCoreTests/<this file> -> repo root
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        return try JSONDecoder().decode(Vectors.self, from: Data(contentsOf: root.appendingPathComponent("protocol.json")))
    }

    @Test func mouseVectors() throws {
        let mouseVectors = try Self.load().vectors.filter { $0.device == "mouse" }
        #expect(mouseVectors.count >= 3)   // omni, omni charging, wired charging
        for vector in mouseVectors {
            let bytes = vector.reply.split(separator: " ").compactMap { UInt8($0, radix: 16) }
            let target = vector.connection == "wired" ? Target.wiredMouse : Target.omniMouse
            let battery = ReplyParser.interpret(replies: [bytes], target: target)
            #expect(battery.percent == vector.expect.percent, "\(vector.connection)")
            #expect(battery.charging == vector.expect.charging, "\(vector.connection)")
            #expect(battery.lowWarning == vector.expect.lowWarning, "\(vector.connection)")
        }
    }
}
