import Foundation
import IOKit
import IOKit.hid
import SquireCore

/// Talks to keyboard and mouse through IOKit. Every packet comes from `Packet.build`, i.e. only commands on the
/// allowlist in SquireCore (Commands.swift) are ever written: the battery queries, the mouse settings reads, and
/// the LED write + save, which the app sends only after an explicit Apply.
///
/// Safety rules (same as the Python version, see CLAUDE.md):
/// - the manager only matches vendor-defined usage pages (>= 0xFF00); the normal keyboard/mouse
///   input collections are never opened
/// - every wait is bounded (no blocking reads)
/// - one fresh IOHIDDevice per request (see `request`)
/// - one device session at a time, also across processes (DeviceLock, shared with squire.py)
public final class HIDReader {
    /// Vendor pages seen on the Omni receiver interface 2 (ff00-ff02), the wired keyboard interface 1 (ff00)
    /// and the wired mouse interface 0 (ff01).
    /// The ffc0-ffc2 pages belong to other interfaces and are deliberately not matched.
    static let vendorPages = [0xFF00, 0xFF01, 0xFF02]

    public struct Result {
        public var batteries: [Battery]
        public var diagnostics: [String]
    }

    public struct SettingsResult {
        public var settings: MouseSettings
        public var diagnostics: [String]
    }

    private final class ReplyBox { var replies: [[UInt8]] = [] }

    public init() {}

    // MARK: batteries

    public func read() -> Result {
        DeviceLock.run {
            var log: [String] = []
            let devices = listDevices(log: &log)
            var found: [Battery.Device: Battery] = [:]
            for target in Target.all {
                if found[target.device]?.percent != nil { continue }   // already have a value (e.g. via Omni)
                for device in candidates(devices, target) {
                    let battery = queryBattery(device, target: target, log: &log)
                    if found[target.device] == nil || (found[target.device]?.percent == nil && battery.percent != nil) {
                        found[target.device] = battery
                    }
                    // macOS lists one entry per collection of the same interface: a definitive answer
                    // ("ok" or "asleep") from the first one is enough.
                    if battery.status == .ok || battery.status == .asleep { break }
                }
            }
            return Result(batteries: [Battery.Device.keyboard, .mouse].compactMap { found[$0] }, diagnostics: log)
        }
    }

    private func queryBattery(_ device: IOHIDDevice, target: Target, log: inout [String]) -> Battery {
        let sent = request(device, target: target, command: target.command, log: &log)
        if let error = sent.error {
            return Battery(device: target.device, name: target.name, connection: target.connection,
                           status: .error, raw: error)
        }
        return ReplyParser.interpret(replies: sent.replies, target: target)
    }

    // MARK: mouse settings (read-only)

    /// Current settings of the Keris II Origin: battery query plus the read commands in `SettingsQuery`.
    /// Active profile only; never switches profiles.
    public func readMouseSettings() -> SettingsResult {
        DeviceLock.run {
            var log: [String] = []
            let devices = listDevices(log: &log)
            var result = MouseSettings(status: .notFound)
            for target in Target.mouseTargets {
                for device in candidates(devices, target) {
                    var settings = MouseSettings(name: target.name, connection: target.connection)
                    let battery = exchange(device, target: target, command: .mouseBattery, log: &log)
                    guard battery.status == .ok else {
                        result = settings
                        result.status = Self.settingsStatus(battery.status)
                        if battery.status == .asleep { break }   // definitive for this interface; try the other connection
                        continue
                    }
                    settings.raw["battery"] = ReplyParser.hex(battery.payload)
                    settings.applyBattery(battery.payload)
                    for query in SettingsQuery.allCases {
                        let ex = exchange(device, target: target, command: query.command, log: &log)
                        guard ex.status == .ok else { settings.raw[query.rawValue] = ex.status.rawValue; continue }
                        settings.raw[query.rawValue] = ReplyParser.hex(ex.payload)
                        settings.apply(query, ex.payload)
                    }
                    return SettingsResult(settings: settings, diagnostics: log)
                }
            }
            return SettingsResult(settings: result, diagnostics: log)
        }
    }

    // MARK: writes (only on an explicit click in the app)

    /// Sets one LED zone, then reads it back. See `write`.
    public func setLED(_ zone: LEDZone, _ led: LEDState, save: Bool) -> (result: WriteResult, diagnostics: [String]) {
        write(.led(zone, led), save: save)
    }

    /// The one write path: checks the mouse is awake, reads the setting, writes, reads it back, and with `save`
    /// sends 50 03, but only if the read-back matched. Without save the change lives in the mouse's RAM only.
    public func write(_ change: SettingChange, save: Bool) -> (result: WriteResult, diagnostics: [String]) {
        // Lift-off is built from the 12 06 read below (it keeps the surface); everything else is checked up front.
        var prebuilt: Command? = nil
        if !change.needsCurrent {
            do { prebuilt = try change.command() } catch {
                return (WriteResult(status: .error, message: "\(error)"), [])
            }
        }
        return DeviceLock.run { [prebuilt] in
            var log: [String] = []
            let devices = listDevices(log: &log)
            var result = WriteResult(status: .notFound)
            for target in Target.mouseTargets {
                for device in candidates(devices, target) {
                    let awake = exchange(device, target: target, command: .mouseBattery, log: &log)
                    guard awake.status == .ok else {
                        result = WriteResult(status: Self.writeStatus(awake.status), message: "mouse not awake: nothing written")
                        if awake.status == .asleep { break }
                        continue
                    }
                    result = WriteResult(status: .error)
                    let before = readBack(device, target: target, change: change, log: &log)
                    result.before = Self.led(change, before)
                    let command: Command
                    if let prebuilt {
                        command = prebuilt
                    } else {
                        guard let before else {
                            result.message = "could not read the current value: nothing written"
                            return (result, log)
                        }
                        do { command = try change.command(current: before) } catch {
                            result.message = "\(error)"
                            return (result, log)
                        }
                    }
                    let written = exchange(device, target: target, command: command, log: &log)
                    result.sent.append(Self.describe(target: target, command: command, prefix: command.bytes.count + 1))
                    if written.status == .error { result.message = written.raw; return (result, log) }
                    let after = readBack(device, target: target, change: change, log: &log)
                    result.after = Self.led(change, after)
                    result.readBack = after
                    result.status = after.map { change.matches($0, before: before) } == true ? .ok : .mismatch
                    if result.status == .ok && save {
                        let saved = exchange(device, target: target, command: .save, log: &log)
                        result.sent.append(Self.describe(target: target, command: .save, prefix: 3))
                        result.saved = saved.status != .error
                    }
                    return (result, log)
                }
            }
            return (result, log)
        }
    }

    /// Reads the value `change` touches; nil if the read failed.
    private func readBack(_ device: IOHIDDevice, target: Target, change: SettingChange, log: inout [String]) -> MouseSettings? {
        let command = change.readBack?.command ?? .mouseBattery
        let ex = exchange(device, target: target, command: command, log: &log)
        guard ex.status == .ok else { return nil }
        var s = MouseSettings(connection: target.connection)
        if let query = change.readBack { s.apply(query, ex.payload) } else { s.applyBattery(ex.payload) }
        return s
    }

    private static func led(_ change: SettingChange, _ s: MouseSettings?) -> LEDState? {
        if case .led(let zone, _) = change { return s?.leds[zone] }
        return nil
    }

    // MARK: transport

    private func listDevices(log: inout [String]) -> [IOHIDDevice] {
        let access = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)
        log.append("Input Monitoring: \(Self.describe(access))")
        let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        let matching = Self.vendorPages.map {
            [kIOHIDVendorIDKey: Int(SquireProtocol.vendorID), kIOHIDDeviceUsagePageKey: $0] as CFDictionary
        }
        IOHIDManagerSetDeviceMatchingMultiple(manager, matching as CFArray)
        // The manager is only used to find devices; it is never opened. Every request talks to its own,
        // freshly created IOHIDDevice (see request), like hidapi does.
        let devices = Array((IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice>) ?? [])
        for device in devices {
            log.append("device pid=\(String(Self.intProperty(device, kIOHIDProductIDKey) ?? 0, radix: 16)) "
                       + "name=\(Self.stringProperty(device, kIOHIDProductKey) ?? "?") "
                       + "transport=\(Self.stringProperty(device, kIOHIDTransportKey) ?? "?") "
                       + "pages=\(Self.usagePages(device).map { String($0, radix: 16) }) "
                       + "maxIn=\(Self.intProperty(device, kIOHIDMaxInputReportSizeKey) ?? -1) "
                       + "maxOut=\(Self.intProperty(device, kIOHIDMaxOutputReportSizeKey) ?? -1)")
        }
        return devices
    }

    /// Vendor-defined collections of the target's product; never the keyboard/mouse input collections.
    private func candidates(_ devices: [IOHIDDevice], _ target: Target) -> [IOHIDDevice] {
        devices.filter {
            Self.intProperty($0, kIOHIDProductIDKey) == Int(target.productID)
                && Self.usagePages($0).contains(where: { $0 >= SquireProtocol.minVendorUsagePage })
        }
    }

    private func exchange(_ device: IOHIDDevice, target: Target, command: Command, log: inout [String]) -> Exchange {
        let sent = request(device, target: target, command: command, log: &log)
        if let error = sent.error { return Exchange(status: .error, raw: error) }
        return ReplyParser.exchange(replies: sent.replies, echo: Array(command.bytes.prefix(command.echoLength)),
                                    reportID: target.reportID)
    }

    static let reportBufferSize = 65   // max input report is 64 bytes on all known devices

    /// One command, one reply wait, on a fresh IOHIDDevice.
    ///
    /// Owns everything the input-report callback can touch (box + buffer) and frees it only after `transact`
    /// has returned, i.e. after the private IOHIDDevice that holds the callback registration is gone.
    ///
    /// Why: IOKit keeps input-report callbacks in a per-device set and calls every entry for each report.
    /// Unregistering (callback nil, with or without the original context) did NOT remove the entry, so
    /// re-using one IOHIDDevice for several queries left stale entries whose freed context was later
    /// called: use-after-free crashes (reproduced with `squire-probe --repeat`, 2026-11-10). A device
    /// object that is used for exactly one request and then released cannot call back any more.
    private func request(_ listed: IOHIDDevice, target: Target, command: Command,
                         log: inout [String]) -> (replies: [[UInt8]], error: String?) {
        let box = ReplyBox()
        let context = Unmanaged.passRetained(box)
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: Self.reportBufferSize)
        defer {
            context.release()
            buffer.deallocate()
        }
        let error = transact(listed, target: target, command: command, box: box, context: context.toOpaque(),
                             buffer: buffer, log: &log)
        return (box.replies, error)
    }

    /// Returns an error text, or nil when the packet was written (replies are in `box`).
    private func transact(_ listed: IOHIDDevice, target: Target, command: Command, box: ReplyBox,
                          context: UnsafeMutableRawPointer, buffer: UnsafeMutablePointer<UInt8>,
                          log: inout [String]) -> String? {
        // Build first: a command that is not on the allowlist never gets as far as opening the device.
        let packets: [[UInt8]]
        do {
            packets = try target.reportLengths.map { try Packet.build(reportID: target.reportID, command: command, length: $0) }
        } catch {
            log.append("[\(target.connection.rawValue)] refused: \(error)")
            return "refused: \(error)"
        }
        // Fresh object for this one request, never the manager's shared one.
        let service = IOHIDDeviceGetService(listed)
        guard service != MACH_PORT_NULL, let device = IOHIDDeviceCreate(kCFAllocatorDefault, service) else {
            return "create failed"
        }
        let opened = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
        if opened != kIOReturnSuccess {
            log.append("[\(target.connection.rawValue)] IOHIDDeviceOpen: \(Self.describe(opened))")
            return "open failed"
        }

        let runLoop: CFRunLoop = CFRunLoopGetCurrent()
        IOHIDDeviceScheduleWithRunLoop(device, runLoop, CFRunLoopMode.defaultMode.rawValue)
        IOHIDDeviceRegisterInputReportCallback(device, buffer, Self.reportBufferSize, { context, _, _, _, _, report, length in
            guard let context else { return }
            Unmanaged<ReplyBox>.fromOpaque(context).takeUnretainedValue().replies
                .append(Array(UnsafeBufferPointer(start: report, count: length)))
        }, context)
        defer {
            // Same order as hidapi's hid_close. The object itself is released when this function returns.
            IOHIDDeviceRegisterInputReportCallback(device, buffer, Self.reportBufferSize, nil, context)
            IOHIDDeviceUnscheduleFromRunLoop(device, runLoop, CFRunLoopMode.defaultMode.rawValue)
            IOHIDDeviceClose(device, IOOptionBits(kIOHIDOptionsTypeNone))
        }
        pump(0.01)
        box.replies.removeAll()   // drop stale input

        var written = false
        for packet in packets {
            // Same framing as hidapi: report ID 0 means "unnumbered", the leading 0x00 is not sent.
            let reportID = Int(target.reportID ?? 0)
            let payload = target.reportID == nil ? Array(packet.dropFirst()) : packet
            let result = payload.withUnsafeBufferPointer {
                IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, reportID, $0.baseAddress!, $0.count)
            }
            log.append("[\(target.connection.rawValue)] > \(ReplyParser.hex(Array(packet.prefix(command.isWrite ? 13 : 5)))) "
                       + "… (\(packet.count) bytes) -> \(Self.describe(result))")
            if result == kIOReturnSuccess { written = true; break }
        }
        guard written else { return "write failed" }

        let echo = Array(command.bytes.prefix(command.echoLength))
        let deadline = Date().addingTimeInterval(Double(SquireProtocol.readTimeoutMs) / 1000)
        while Date() < deadline {
            pump(0.02)
            // Stop at the first echoed reply. An `ff aa` alone does not end the wait: a valid reply may follow.
            if box.replies.contains(where: { ReplyParser.echoIndex(in: $0, query: echo) != nil }) { break }
        }
        for reply in box.replies {
            log.append("[\(target.connection.rawValue)] < \(ReplyParser.hex(reply))")
        }
        return nil
    }

    // MARK: helpers

    private func pump(_ seconds: TimeInterval) {
        CFRunLoopRunInMode(.defaultMode, seconds, false)
    }

    private static func settingsStatus(_ status: Exchange.Status) -> MouseSettings.Status {
        switch status {
        case .ok: return .ok
        case .asleep: return .asleep
        case .noResponse: return .noResponse
        case .error: return .error
        }
    }

    private static func writeStatus(_ status: Exchange.Status) -> WriteResult.Status {
        switch status {
        case .ok: return .ok
        case .asleep: return .asleep
        case .noResponse: return .noResponse
        case .error: return .error
        }
    }

    private static func describe(target: Target, command: Command, prefix: Int) -> String {
        guard let packet = try? Packet.build(reportID: target.reportID, command: command,
                                             length: target.reportLengths[0]) else { return "refused" }
        return ReplyParser.hex(Array(packet.prefix(prefix)))
    }

    private static func intProperty(_ device: IOHIDDevice, _ key: String) -> Int? {
        IOHIDDeviceGetProperty(device, key as CFString) as? Int
    }

    private static func stringProperty(_ device: IOHIDDevice, _ key: String) -> String? {
        IOHIDDeviceGetProperty(device, key as CFString) as? String
    }

    private static func usagePages(_ device: IOHIDDevice) -> [Int] {
        let pairs = IOHIDDeviceGetProperty(device, kIOHIDDeviceUsagePairsKey as CFString) as? [[String: Int]] ?? []
        return pairs.compactMap { $0[kIOHIDDeviceUsagePageKey] }
    }

    static func describe(_ access: IOHIDAccessType) -> String {
        switch access {
        case kIOHIDAccessTypeGranted: return "granted"
        case kIOHIDAccessTypeDenied: return "denied"
        case kIOHIDAccessTypeUnknown: return "unknown (not asked yet)"
        default: return "?(\(access.rawValue))"
        }
    }

    static func describe(_ result: IOReturn) -> String {
        result == kIOReturnSuccess ? "ok" : "error 0x" + String(UInt32(bitPattern: result), radix: 16)
    }
}

/// One device session at a time: battery poll, settings window and squire.py (SwiftBar plugin) must not
/// interleave their requests. Uses the same lock file as squire.device_lock on macOS (flock). Best effort:
/// if the file cannot be created, only the in-process lock applies.
enum DeviceLock {
    private static let inProcess = NSLock()

    static var path: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/squire-gear/squire-gear-device.lock")
    }

    static func run<T>(_ body: () -> T) -> T {
        inProcess.lock()
        defer { inProcess.unlock() }
        try? FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
        let fd = open(path.path, O_WRONLY | O_CREAT | O_CLOEXEC, 0o644)
        if fd >= 0 { flock(fd, LOCK_EX) }   // waits; a session takes about a second at most
        defer { if fd >= 0 { close(fd) } }  // closing releases the lock
        return body()
    }
}
