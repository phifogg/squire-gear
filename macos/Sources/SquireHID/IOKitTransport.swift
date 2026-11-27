import Foundation
import IOKit
import IOKit.hid
import SquireCore

/// Transport layer (same as squirelib/transport): lists the vendor-defined HID collections and moves bytes.
/// Knows nothing about ASUS commands: it writes the packets it is given (HIDReader builds them with Packet.build,
/// i.e. through the allowlist) and collects the input reports until `done` or the timeout.
///
/// Rules (see CLAUDE.md):
/// - only vendor-defined usage pages are matched; the normal keyboard/mouse input collections are never opened
/// - every wait is bounded (no blocking reads)
/// - one fresh IOHIDDevice per request (see `request`)
final class IOKitTransport {
    /// Vendor pages seen on the Omni receiver interface 2 (ff00-ff02), the wired keyboard interface 1 (ff00)
    /// and the wired mouse interface 0 (ff01).
    /// The ffc0-ffc2 pages belong to other interfaces and are deliberately not matched.
    static let vendorPages = [0xFF00, 0xFF01, 0xFF02]
    static let reportBufferSize = 65   // max input report is 64 bytes on all known devices

    private final class ReplyBox { var replies: [[UInt8]] = [] }

    func listDevices(log: inout [String]) -> [IOHIDDevice] {
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

    /// Vendor-defined collections of a product; never the keyboard/mouse input collections.
    func candidates(_ devices: [IOHIDDevice], productID: UInt16) -> [IOHIDDevice] {
        devices.filter {
            Self.intProperty($0, kIOHIDProductIDKey) == Int(productID)
                && Self.usagePages($0).contains(where: { $0 >= SquireProtocol.minVendorUsagePage })
        }
    }

    /// One request, one reply wait, on a fresh IOHIDDevice. `packets` are complete packets (report-ID byte first),
    /// tried in order until one is accepted; `done` ends the wait early (the protocol's "echo seen").
    /// `label` and `logBytes` only shape the diagnostics.
    ///
    /// Owns everything the input-report callback can touch (box + buffer) and frees it only after `transact`
    /// has returned, i.e. after the private IOHIDDevice that holds the callback registration is gone.
    ///
    /// Why: IOKit keeps input-report callbacks in a per-device set and calls every entry for each report.
    /// Unregistering (callback nil, with or without the original context) did NOT remove the entry, so
    /// re-using one IOHIDDevice for several queries left stale entries whose freed context was later
    /// called: use-after-free crashes (reproduced with `squire-probe --repeat`, 2026-11-10). A device
    /// object that is used for exactly one request and then released cannot call back any more.
    func request(_ listed: IOHIDDevice, reportID: UInt8?, packets: [[UInt8]], timeoutMs: Int, label: String,
                 logBytes: Int, done: ([[UInt8]]) -> Bool, log: inout [String]) -> (replies: [[UInt8]], error: String?) {
        let box = ReplyBox()
        let context = Unmanaged.passRetained(box)
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: Self.reportBufferSize)
        defer {
            context.release()
            buffer.deallocate()
        }
        let error = transact(listed, reportID: reportID, packets: packets, timeoutMs: timeoutMs, label: label,
                             logBytes: logBytes, done: done, box: box, context: context.toOpaque(), buffer: buffer,
                             log: &log)
        return (box.replies, error)
    }

    /// Returns an error text, or nil when a packet was written (replies are in `box`).
    private func transact(_ listed: IOHIDDevice, reportID: UInt8?, packets: [[UInt8]], timeoutMs: Int,
                          label: String, logBytes: Int, done: ([[UInt8]]) -> Bool, box: ReplyBox,
                          context: UnsafeMutableRawPointer, buffer: UnsafeMutablePointer<UInt8>,
                          log: inout [String]) -> String? {
        // Fresh object for this one request, never the manager's shared one.
        let service = IOHIDDeviceGetService(listed)
        guard service != MACH_PORT_NULL, let device = IOHIDDeviceCreate(kCFAllocatorDefault, service) else {
            return "create failed"
        }
        let opened = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
        if opened != kIOReturnSuccess {
            log.append("[\(label)] IOHIDDeviceOpen: \(Self.describe(opened))")
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
            let id = Int(reportID ?? 0)
            let payload = reportID == nil ? Array(packet.dropFirst()) : packet
            let result = payload.withUnsafeBufferPointer {
                IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, id, $0.baseAddress!, $0.count)
            }
            log.append("[\(label)] > \(ReplyParser.hex(Array(packet.prefix(logBytes)))) "
                       + "… (\(packet.count) bytes) -> \(Self.describe(result))")
            if result == kIOReturnSuccess { written = true; break }
        }
        guard written else { return "write failed" }

        let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1000)
        while Date() < deadline {
            pump(0.02)
            if done(box.replies) { break }
        }
        for reply in box.replies {
            log.append("[\(label)] < \(ReplyParser.hex(reply))")
        }
        return nil
    }

    private func pump(_ seconds: TimeInterval) {
        CFRunLoopRunInMode(.defaultMode, seconds, false)
    }

    static func intProperty(_ device: IOHIDDevice, _ key: String) -> Int? {
        IOHIDDeviceGetProperty(device, key as CFString) as? Int
    }

    static func stringProperty(_ device: IOHIDDevice, _ key: String) -> String? {
        IOHIDDeviceGetProperty(device, key as CFString) as? String
    }

    static func usagePages(_ device: IOHIDDevice) -> [Int] {
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
