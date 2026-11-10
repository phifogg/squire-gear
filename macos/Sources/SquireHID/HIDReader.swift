import Foundation
import IOKit
import IOKit.hid
import SquireCore

/// Reads keyboard and mouse batteries through IOKit. Read-only: the only reports ever written are the
/// `12 01` (keyboard) and `12 07` (mouse) queries.
///
/// Safety rules (same as the Python version, see CLAUDE.md):
/// - the manager only matches vendor-defined usage pages (>= 0xFF00); the normal keyboard/mouse
///   input collections are never opened
/// - every wait is bounded (no blocking reads)
public final class HIDReader {
    /// Vendor pages seen on the Omni receiver interface 2 (ff00-ff02), the wired keyboard interface 1 (ff00)
    /// and the wired mouse interface 0 (ff01).
    /// The ffc0-ffc2 pages belong to other interfaces and are deliberately not matched.
    static let vendorPages = [0xFF00, 0xFF01, 0xFF02]

    public struct Result {
        public var batteries: [Battery]
        public var diagnostics: [String]
    }

    private final class ReplyBox { var replies: [[UInt8]] = [] }

    public init() {}

    public func read() -> Result {
        var log: [String] = []
        let access = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)
        log.append("Input Monitoring: \(Self.describe(access))")

        let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        let matching = Self.vendorPages.map {
            [kIOHIDVendorIDKey: Int(SquireProtocol.vendorID), kIOHIDDeviceUsagePageKey: $0] as CFDictionary
        }
        IOHIDManagerSetDeviceMatchingMultiple(manager, matching as CFArray)
        // The manager is only used to find devices; it is never opened. Every query talks to its own,
        // freshly created IOHIDDevice (see query), like hidapi does.
        let devices = (IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice>) ?? []
        for device in devices {
            log.append("device pid=\(String(Self.intProperty(device, kIOHIDProductIDKey) ?? 0, radix: 16)) "
                       + "name=\(Self.stringProperty(device, kIOHIDProductKey) ?? "?") "
                       + "transport=\(Self.stringProperty(device, kIOHIDTransportKey) ?? "?") "
                       + "pages=\(Self.usagePages(device).map { String($0, radix: 16) }) "
                       + "maxIn=\(Self.intProperty(device, kIOHIDMaxInputReportSizeKey) ?? -1) "
                       + "maxOut=\(Self.intProperty(device, kIOHIDMaxOutputReportSizeKey) ?? -1)")
        }

        var found: [Battery.Device: Battery] = [:]
        for target in Target.all {
            if found[target.device]?.percent != nil { continue }   // already have a value (e.g. via Omni)
            for device in devices where Self.intProperty(device, kIOHIDProductIDKey) == Int(target.productID) {
                guard Self.usagePages(device).contains(where: { $0 >= SquireProtocol.minVendorUsagePage }) else {
                    continue   // never touch non-vendor collections
                }
                let battery = query(device, target: target, log: &log)
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

    static let reportBufferSize = 65   // max input report is 64 bytes on all known devices

    /// Owns everything the input-report callback can touch (box + buffer) and frees it only after `transact`
    /// has returned, i.e. after the private IOHIDDevice that holds the callback registration is gone.
    ///
    /// Why: IOKit keeps input-report callbacks in a per-device set and calls every entry for each report.
    /// Unregistering (callback nil, with or without the original context) did NOT remove the entry, so
    /// re-using one IOHIDDevice for several queries left stale entries whose freed context was later
    /// called: use-after-free crashes (reproduced with `squire-probe --repeat`, 2026-11-10). A device
    /// object that is used for exactly one query and then released cannot call back any more.
    private func query(_ listed: IOHIDDevice, target: Target, log: inout [String]) -> Battery {
        let box = ReplyBox()
        let context = Unmanaged.passRetained(box)
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: Self.reportBufferSize)
        defer {
            context.release()
            buffer.deallocate()
        }
        return transact(listed, target: target, box: box, context: context.toOpaque(), buffer: buffer, log: &log)
    }

    private func transact(_ listed: IOHIDDevice, target: Target, box: ReplyBox, context: UnsafeMutableRawPointer,
                          buffer: UnsafeMutablePointer<UInt8>, log: inout [String]) -> Battery {
        var battery = Battery(device: target.device, name: target.name, connection: target.connection)
        // Fresh object for this one query, never the manager's shared one.
        let service = IOHIDDeviceGetService(listed)
        guard service != MACH_PORT_NULL, let device = IOHIDDeviceCreate(kCFAllocatorDefault, service) else {
            battery.status = .error; battery.raw = "create failed"; return battery
        }
        let opened = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
        log.append("[\(target.connection.rawValue)] IOHIDDeviceOpen: \(Self.describe(opened))")
        guard opened == kIOReturnSuccess else { battery.status = .error; battery.raw = "open failed"; return battery }

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
        for length in target.reportLengths {
            let packet = target.packet(length: length)
            // Same framing as hidapi: report ID 0 means "unnumbered", the leading 0x00 is not sent.
            let reportID = Int(target.reportID ?? 0)
            let payload = target.reportID == nil ? Array(packet.dropFirst()) : packet
            let result = payload.withUnsafeBufferPointer {
                IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, reportID, $0.baseAddress!, $0.count)
            }
            log.append("[\(target.connection.rawValue)] > \(packet.prefix(4).map { String($0, radix: 16) }.joined(separator: " ")) "
                       + "… (\(length) bytes) -> \(Self.describe(result))")
            if result == kIOReturnSuccess { written = true; break }
        }
        guard written else { battery.status = .error; battery.raw = "write failed"; return battery }

        let deadline = Date().addingTimeInterval(Double(SquireProtocol.readTimeoutMs) / 1000)
        while Date() < deadline {
            pump(0.02)
            // Stop at the first echoed reply. An `ff aa` alone does not end the wait: a valid reply may follow.
            if box.replies.contains(where: { ReplyParser.echoIndex(in: $0, query: target.query) != nil }) { break }
        }
        for reply in box.replies {
            log.append("[\(target.connection.rawValue)] < \(ReplyParser.hex(reply))")
        }
        return ReplyParser.interpret(replies: box.replies, target: target)
    }

    // MARK: helpers

    private func pump(_ seconds: TimeInterval) {
        CFRunLoopRunInMode(.defaultMode, seconds, false)
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
