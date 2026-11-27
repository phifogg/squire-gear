import CoreBluetooth
import Foundation
import IOKit
import IOKit.hid
import SquireCore

/// Battery level of keyboard and mouse when they are paired with the Mac directly over Bluetooth LE.
///
/// Reads only the standard Battery Level characteristic (180F / 2A19). No HID report is sent and the vendor
/// service FFF0 is never touched (see SquireCore/Bluetooth.swift). The peripherals are the ones macOS already
/// holds connected; Squire never scans or pairs.
///
/// CoreBluetooth asks for the Bluetooth permission the moment a central manager exists, so the manager is only
/// created once IOKit lists one of the Bluetooth product IDs (`pairedDevicesPresent`, registry lookup only,
/// nothing is opened). Its state lives on its own queue; call `read` from one background queue at a time
/// (BatteryStore uses a serial poll queue), never from the main thread.
public final class BluetoothReader: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate, @unchecked Sendable {
    public struct Result {
        public var batteries: [Battery]
        public var diagnostics: [String]
    }

    private let queue = DispatchQueue(label: "io.github.phifogg.squire.bluetooth")
    private var central: CBCentralManager?
    private var poweredOn = DispatchSemaphore(value: 0)
    // Everything below is only touched on `queue`.
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var pending: Set<UUID> = []
    private var results: [Battery.Device: Battery] = [:]
    private var log: [String] = []
    private var done: DispatchSemaphore?

    private static let serviceUUID = CBUUID(string: Bluetooth.batteryService)
    private static let levelUUID = CBUUID(string: Bluetooth.batteryLevel)

    public override init() { super.init() }

    /// True if the IORegistry lists a Falchion RX LP / Keris II Origin with its Bluetooth product ID.
    public static func pairedDevicesPresent() -> Bool {
        for pid in Bluetooth.productIDs.keys {
            let matching = IOServiceMatching(kIOHIDDeviceKey) as NSMutableDictionary
            matching[kIOHIDVendorIDKey] = Int(SquireProtocol.vendorID)
            matching[kIOHIDProductIDKey] = Int(pid)
            var iterator: io_iterator_t = 0
            guard IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else { continue }
            defer { IOObjectRelease(iterator) }
            let service = IOIteratorNext(iterator)
            if service != 0 {
                IOObjectRelease(service)
                return true
            }
        }
        return false
    }

    /// Blocks up to `timeout` seconds. Returns one Battery per device found over Bluetooth (none if no device is
    /// connected that way, Bluetooth is off or the permission was denied).
    public func read(timeout: TimeInterval = 3) -> Result {
        guard Self.pairedDevicesPresent() else {
            return Result(batteries: [], diagnostics: ["bluetooth: no ROG device with a Bluetooth product id in IOKit"])
        }
        if central == nil {
            central = CBCentralManager(delegate: self, queue: queue)
        }
        let deadline = DispatchTime.now() + timeout
        var state = CBManagerState.unknown
        queue.sync { state = central?.state ?? .unknown }
        if state != .poweredOn {
            if poweredOn.wait(timeout: deadline) == .timedOut {
                queue.sync { state = central?.state ?? .unknown }
                return Result(batteries: [], diagnostics: ["bluetooth: not available (state \(state.rawValue), "
                                                          + "permission \(CBManager.authorization.rawValue))"])
            }
        }

        let finished = DispatchSemaphore(value: 0)
        queue.sync { start(finished) }
        let timedOut = finished.wait(timeout: deadline) == .timedOut
        var result = Result(batteries: [], diagnostics: [])
        queue.sync {
            if timedOut {
                log.append("bluetooth: timeout, \(pending.count) peripheral(s) without a reply")
                for id in pending {
                    if let device = Bluetooth.device(forName: peripherals[id]?.name), results[device] == nil {
                        results[device] = Battery(device: device, name: Target.name(of: device),
                                                  connection: .bluetooth, status: .noResponse)
                    }
                }
            }
            result = Result(batteries: [Battery.Device.keyboard, .mouse].compactMap { results[$0] }, diagnostics: log)
            done = nil
            pending = []
        }
        return result
    }

    // MARK: on `queue`

    private func start(_ finished: DispatchSemaphore) {
        results = [:]
        log = []
        done = finished
        guard let central else { finished.signal(); return }
        let connected = central.retrieveConnectedPeripherals(withServices: [Self.serviceUUID])
            .filter { Bluetooth.device(forName: $0.name) != nil }
        log.append("bluetooth: \(connected.count) ROG peripheral(s) connected: "
                   + connected.map { $0.name ?? "?" }.joined(separator: ", "))
        pending = Set(connected.map(\.identifier))
        if pending.isEmpty { finished.signal(); return }
        for peripheral in connected {
            peripherals[peripheral.identifier] = peripheral
            peripheral.delegate = self
            if peripheral.state == .connected {
                readLevel(peripheral)
            } else {
                central.connect(peripheral)   // already linked by macOS: this only opens it for Squire
            }
        }
    }

    private func readLevel(_ peripheral: CBPeripheral) {
        if let level = peripheral.services?.first(where: { $0.uuid == Self.serviceUUID })?
            .characteristics?.first(where: { $0.uuid == Self.levelUUID }) {
            peripheral.readValue(for: level)
        } else if let service = peripheral.services?.first(where: { $0.uuid == Self.serviceUUID }) {
            peripheral.discoverCharacteristics([Self.levelUUID], for: service)
        } else {
            peripheral.discoverServices([Self.serviceUUID])
        }
    }

    private func finish(_ peripheral: CBPeripheral, _ battery: Battery?) {
        if let battery { results[battery.device] = battery }
        pending.remove(peripheral.identifier)
        if pending.isEmpty { done?.signal() }
    }

    private func fail(_ peripheral: CBPeripheral, _ message: String) {
        log.append("bluetooth: \(peripheral.name ?? "?"): \(message)")
        let battery = Bluetooth.device(forName: peripheral.name).map {
            Battery(device: $0, name: Target.name(of: $0), connection: .bluetooth, status: .error, raw: message)
        }
        finish(peripheral, battery)
    }

    // MARK: delegates (called on `queue`)

    public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn { poweredOn.signal() }
    }

    public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        readLevel(peripheral)
    }

    public func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        fail(peripheral, "connect failed: \(error.map { "\($0)" } ?? "?")")
    }

    public func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil, peripheral.services?.contains(where: { $0.uuid == Self.serviceUUID }) == true else {
            return fail(peripheral, "no battery service \(error.map { "(\($0))" } ?? "")")
        }
        readLevel(peripheral)
    }

    public func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard error == nil, service.characteristics?.contains(where: { $0.uuid == Self.levelUUID }) == true else {
            return fail(peripheral, "no battery level \(error.map { "(\($0))" } ?? "")")
        }
        readLevel(peripheral)
    }

    public func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard characteristic.uuid == Self.levelUUID, pending.contains(peripheral.identifier) else { return }
        if let error { return fail(peripheral, "read failed: \(error)") }
        let value = [UInt8](characteristic.value ?? Data())
        log.append("bluetooth: \(peripheral.name ?? "?") 2A19 = \(ReplyParser.hex(value))")
        finish(peripheral, Bluetooth.device(forName: peripheral.name).map { Bluetooth.battery($0, level: value) })
    }
}
