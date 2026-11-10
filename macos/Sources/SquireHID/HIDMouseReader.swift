import Foundation
import IOKit
import IOKit.hid
import SquireCore

/// Reads the mouse battery through IOKit. Read-only: the only report ever written is the `12 07` query.
///
/// Safety rules (same as the Python version, see CLAUDE.md):
/// - the manager only matches vendor-defined usage pages (>= 0xFF00); the normal keyboard/mouse
///   input collections are never opened
/// - every wait is bounded (no blocking reads)
public final class HIDMouseReader {
    /// Vendor pages seen on the Omni receiver interface 2 (ff00-ff02) and the wired mouse interface 0 (ff01).
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
        log.append("Input Monitoring before open: \(Self.describe(access))")

        let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
        let matching = Self.vendorPages.map {
            [kIOHIDVendorIDKey: Int(SquireProtocol.vendorID), kIOHIDDeviceUsagePageKey: $0] as CFDictionary
        }
        IOHIDManagerSetDeviceMatchingMultiple(manager, matching as CFArray)
        IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
        let opened = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        log.append("IOHIDManagerOpen: \(Self.describe(opened))")
        log.append("Input Monitoring after open: \(Self.describe(IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)))")
        defer {
            IOHIDManagerUnscheduleFromRunLoop(manager, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
            IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        }
        guard opened == kIOReturnSuccess else { return Result(batteries: [], diagnostics: log) }

        pump(0.3)   // let the manager enumerate
        let devices = (IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice>) ?? []
        for device in devices {
            log.append("device pid=\(String(Self.intProperty(device, kIOHIDProductIDKey) ?? 0, radix: 16)) "
                       + "name=\(Self.stringProperty(device, kIOHIDProductKey) ?? "?") "
                       + "transport=\(Self.stringProperty(device, kIOHIDTransportKey) ?? "?") "
                       + "pages=\(Self.usagePages(device).map { String($0, radix: 16) })")
        }

        var batteries: [Battery] = []
        for target in Target.mouseTargets {
            if batteries.contains(where: { $0.percent != nil }) { break }   // already have a value
            for device in devices where Self.intProperty(device, kIOHIDProductIDKey) == Int(target.productID) {
                guard Self.usagePages(device).contains(where: { $0 >= SquireProtocol.minVendorUsagePage }) else {
                    continue   // never touch non-vendor collections
                }
                let battery = query(device, target: target, log: &log)
                if batteries.isEmpty || (batteries[0].percent == nil && battery.percent != nil) {
                    batteries = [battery]
                }
                if battery.percent != nil { break }
            }
        }
        return Result(batteries: batteries, diagnostics: log)
    }

    private func query(_ device: IOHIDDevice, target: Target, log: inout [String]) -> Battery {
        var battery = Battery(device: target.device, name: target.name, connection: target.connection)
        let opened = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
        log.append("[\(target.connection.rawValue)] IOHIDDeviceOpen: \(Self.describe(opened))")
        guard opened == kIOReturnSuccess else { battery.status = .error; battery.raw = "open failed"; return battery }
        defer { IOHIDDeviceClose(device, IOOptionBits(kIOHIDOptionsTypeNone)) }

        let box = ReplyBox()
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 65)
        defer { buffer.deallocate() }
        IOHIDDeviceScheduleWithRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
        defer { IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue) }
        IOHIDDeviceRegisterInputReportCallback(device, buffer, 65, { context, _, _, _, _, report, length in
            guard let context else { return }
            Unmanaged<ReplyBox>.fromOpaque(context).takeUnretainedValue().replies
                .append(Array(UnsafeBufferPointer(start: report, count: length)))
        }, Unmanaged.passUnretained(box).toOpaque())
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
            if box.replies.contains(where: { ReplyParser.isError($0, reportID: target.reportID)
                                             || ReplyParser.echoIndex(in: $0, query: target.query) != nil }) { break }
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
