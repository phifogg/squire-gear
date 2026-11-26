import Foundation
import Testing
@testable import SquireCore

@Suite struct BluetoothTests {
    @Test func namesAsMacOSShowsThem() {
        #expect(Bluetooth.device(forName: "ROG FALCHION RX LOW PROFILE") == .keyboard)
        #expect(Bluetooth.device(forName: "ROG KERIS II ORIGIN") == .mouse)
        #expect(Bluetooth.device(forName: "MX Master 3") == nil)
        #expect(Bluetooth.device(forName: nil) == nil)
    }

    @Test func productIDsSeenOnTheMac() {
        #expect(Bluetooth.productIDs[0x1B08] == .keyboard)
        #expect(Bluetooth.productIDs[0x1C0F] == .mouse)
    }

    /// Values read on 2026-11-26: mouse 34 (52 %), keyboard 3c (60 %).
    @Test func batteryLevel() {
        let mouse = Bluetooth.battery(.mouse, level: [0x34])
        #expect(mouse.percent == 52 && mouse.connection == .bluetooth && mouse.status == .ok)
        #expect(!mouse.charging && mouse.lowWarning == nil)
        #expect(Bluetooth.battery(.keyboard, level: [0x3c]).percent == 60)
        #expect(Bluetooth.battery(.keyboard, level: [0x3c]).name == "ROG Falchion RX LP")
    }

    @Test func implausibleLevels() {
        #expect(Bluetooth.battery(.mouse, level: []).status == .error)
        #expect(Bluetooth.battery(.mouse, level: [101]).status == .error)
        let zero = Bluetooth.battery(.mouse, level: [0])
        #expect(zero.status == .asleep && zero.percent == nil)
    }

    @Test func hidValueWins() {
        let omni = Battery(device: .mouse, name: "m", connection: .omni, percent: 80)
        let ble = Bluetooth.battery(.mouse, level: [0x34])
        #expect(Bluetooth.merge(hid: [omni], bluetooth: [ble]) == [omni])
    }

    @Test func bluetoothFillsAsleepOrMissing() {
        let asleep = Battery(device: .mouse, name: "m", connection: .omni, status: .asleep)
        let mouse = Bluetooth.battery(.mouse, level: [0x34])
        let keyboard = Bluetooth.battery(.keyboard, level: [0x3c])
        #expect(Bluetooth.merge(hid: [asleep], bluetooth: [mouse, keyboard]) == [keyboard, mouse])
    }

    @Test func hidStatusKeptWithoutBluetoothValue() {
        let asleep = Battery(device: .keyboard, name: "k", connection: .omni, status: .asleep)
        let noReply = Battery(device: .mouse, name: "m", connection: .bluetooth, status: .noResponse)
        #expect(Bluetooth.merge(hid: [asleep], bluetooth: [noReply]) == [asleep, noReply])
        #expect(Bluetooth.merge(hid: [], bluetooth: []) == [])
    }

    @Test func connectionLabels() {
        #expect(Battery.Connection.omni.label == "Omni receiver")
        #expect(Battery.Connection.wired.label == "Wired")
        #expect(Battery.Connection.bluetooth.label == "Bluetooth")
    }
}

/// protocol.json `bluetooth.captures`: 2A19 values read on the Mac.
@Suite struct BluetoothCaptureTests {
    @Test func batteryLevelCaptures() throws {
        let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("protocol.json")
        let root = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        let ble = root["bluetooth"] as! [String: Any]
        let devices = ble["devices"] as! [String: [String: String]]
        for (key, info) in devices {
            let pid = UInt16(info["product_id"]!, radix: 16)!
            #expect(Bluetooth.productIDs[pid]?.rawValue == key)
            #expect(Bluetooth.device(forName: info["name"]) == Battery.Device(rawValue: key))
        }
        let levels = (ble["captures"] as! [[String: Any]]).filter { $0["characteristic"] as? String == "2A19" }
        #expect(levels.count == 2)
        for capture in levels {
            let device = Battery.Device(rawValue: capture["device"] as! String)!
            let value = (capture["value"] as! String).split(separator: " ").compactMap { UInt8($0, radix: 16) }
            let expect = capture["expect"] as! [String: Int]
            #expect(Bluetooth.battery(device, level: value).percent == expect["percent"])
        }
    }
}
