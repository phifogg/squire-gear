import Foundation
import IOKit
import IOKit.hid
import SquireCore

/// Device sessions for keyboard and mouse (batteries, settings reads, verified writes) on top of IOKitTransport.
/// Every packet comes from `Packet.build`, i.e. only commands on the allowlist in SquireCore (Commands.swift) are
/// ever written: the battery queries, the mouse and keyboard settings reads, and the writes + save, which the app
/// sends only after an explicit Apply. One device session at a time, also across processes (DeviceLock, shared
/// with squire.py). Transport rules (vendor collections only, bounded waits, one fresh IOHIDDevice per request)
/// are in IOKitTransport.
public final class HIDReader {
    private let transport = IOKitTransport()

    public struct Result {
        public var batteries: [Battery]
        public var diagnostics: [String]
    }

    public struct SettingsResult {
        public var settings: MouseSettings
        public var diagnostics: [String]
    }

    public struct KeyboardSettingsResult {
        public var settings: KeyboardSettings
        public var diagnostics: [String]
    }

    public init() {}

    // MARK: batteries

    public func read() -> Result {
        DeviceLock.run {
            var log: [String] = []
            let devices = transport.listDevices(log: &log)
            var found: [Battery.Device: Battery] = [:]
            for target in Target.all {
                if found[target.device]?.percent != nil { continue }   // already have a value (e.g. via Omni)
                for device in transport.candidates(devices, productID: target.productID) {
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
            let devices = transport.listDevices(log: &log)
            var result = MouseSettings(status: .notFound)
            for target in Target.mouseTargets {
                for device in transport.candidates(devices, productID: target.productID) {
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

    // MARK: keyboard settings (read-only)

    /// Current settings of the Falchion RX LP: battery query plus the read commands in `KeyboardQuery`.
    /// Active profile only; never switches profiles.
    public func readKeyboardSettings() -> KeyboardSettingsResult {
        DeviceLock.run {
            var log: [String] = []
            let devices = transport.listDevices(log: &log)
            var result = KeyboardSettings(status: .notFound)
            for target in Target.keyboardTargets {
                for device in transport.candidates(devices, productID: target.productID) {
                    var settings = KeyboardSettings(name: target.name, connection: target.connection)
                    let battery = exchange(device, target: target, command: .keyboardBattery, log: &log)
                    guard battery.status == .ok else {
                        result = settings
                        result.status = Self.settingsStatus(battery.status)
                        if battery.status == .asleep { break }   // definitive for this interface; try the other connection
                        continue
                    }
                    settings.raw["battery"] = ReplyParser.hex(battery.payload)
                    settings.applyBattery(battery.payload)
                    for query in KeyboardQuery.allCases {
                        let ex = exchange(device, target: target, command: query.command, log: &log)
                        guard ex.status == .ok else { settings.raw[query.rawValue] = ex.status.rawValue; continue }
                        settings.raw[query.rawValue] = ReplyParser.hex(ex.payload)
                        settings.apply(query, ex.payload)
                    }
                    return KeyboardSettingsResult(settings: settings, diagnostics: log)
                }
            }
            return KeyboardSettingsResult(settings: result, diagnostics: log)
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
            let devices = transport.listDevices(log: &log)
            var result = WriteResult(status: .notFound)
            for target in Target.mouseTargets {
                for device in transport.candidates(devices, productID: target.productID) {
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
                        // saved only when the mouse echoes 50 03; ff aa or silence = not confirmed (as squire.py)
                        result.saved = saved.status == .ok
                        if !result.saved {
                            result.message = "written, but the save (50 03) was not confirmed: "
                                + (saved.status == .asleep ? "it answered ff aa" : saved.status == .noResponse ? "no reply" : saved.raw)
                        }
                    }
                    return (result, log)
                }
            }
            return (result, log)
        }
    }

    /// Keyboard writes, same as squire.write_verified for the keyboard: checks the keyboard is awake, reads the value,
    /// sends every chunk in order (each must be echoed, a gradient is three), reads it back once, and with `save`
    /// sends 50 55, but only if the read-back matched. Unsaved changes are lost when the keyboard is switched off.
    public func writeKeyboard(_ change: KeyboardChange, save: Bool) -> (result: KeyboardWriteResult, diagnostics: [String]) {
        let commands: [Command]
        do { commands = try change.commands() } catch {
            return (KeyboardWriteResult(status: .error, message: "\(error)"), [])
        }
        return DeviceLock.run {
            var log: [String] = []
            let devices = transport.listDevices(log: &log)
            var result = KeyboardWriteResult(status: .notFound)
            for target in Target.keyboardTargets {
                for device in transport.candidates(devices, productID: target.productID) {
                    let awake = exchange(device, target: target, command: .keyboardBattery, log: &log)
                    guard awake.status == .ok else {
                        result = KeyboardWriteResult(status: Self.writeStatus(awake.status),
                                                     message: "keyboard not awake: nothing written")
                        if awake.status == .asleep { break }
                        continue
                    }
                    result = KeyboardWriteResult(status: .error)
                    result.before = readKeyboard(device, target: target, query: change.readBack, log: &log)
                    for command in commands {
                        let written = exchange(device, target: target, command: command, log: &log)
                        result.sent.append(Self.describe(target: target, command: command, prefix: command.bytes.count + 1))
                        // several chunks, or a command that is acknowledged: each must be answered before the next
                        if written.status == .error || ((commands.count > 1 || command.reply != nil) && written.status != .ok) {
                            result.message = written.status == .error ? written.raw
                                : "chunk \(ReplyParser.hex(Array(command.bytes.prefix(4)))): \(written.status.rawValue); nothing saved"
                            return (result, log)
                        }
                    }
                    result.after = readKeyboard(device, target: target, query: change.readBack, log: &log)
                    result.status = result.after.map { change.matches($0, before: result.before) } == true ? .ok : .mismatch
                    if result.status == .ok && save && change.isSaved {
                        let saved = exchange(device, target: target, command: .keyboardSave, log: &log)
                        result.sent.append(Self.describe(target: target, command: .keyboardSave, prefix: 3))
                        // saved only when the keyboard echoes 50 55 (as squire.py)
                        result.saved = saved.status == .ok
                        if !result.saved {
                            result.message = "written, but the save (50 55) was not confirmed: "
                                + (saved.status == .asleep ? "it answered ff aa" : saved.status == .noResponse ? "no reply" : saved.raw)
                        }
                    }
                    return (result, log)
                }
            }
            return (result, log)
        }
    }

    /// One keyboard read: the battery query 12 01 (query nil) or a settings read; nil if it failed.
    private func readKeyboard(_ device: IOHIDDevice, target: Target, query: KeyboardQuery?,
                              log: inout [String]) -> KeyboardSettings? {
        var k = KeyboardSettings(connection: target.connection)
        let ex = exchange(device, target: target, command: query?.command ?? .keyboardBattery, log: &log)
        guard ex.status == .ok else { return nil }
        if let query {
            k.apply(query, ex.payload)
            k.raw[query.rawValue] = ReplyParser.hex(ex.payload)
        } else {
            k.applyBattery(ex.payload)
            k.raw["battery"] = ReplyParser.hex(ex.payload)
        }
        return k
    }

    /// Reads the value `change` touches; nil if the read failed.
    private func readBack(_ device: IOHIDDevice, target: Target, change: SettingChange, log: inout [String]) -> MouseSettings? {
        var s = MouseSettings(connection: target.connection)
        for query in change.reads {
            let ex = exchange(device, target: target, command: query?.command ?? .mouseBattery, log: &log)
            guard ex.status == .ok else { return nil }
            if let query { s.apply(query, ex.payload) } else { s.applyBattery(ex.payload) }
        }
        return s
    }

    private static func led(_ change: SettingChange, _ s: MouseSettings?) -> LEDState? {
        if case .led(let zone, _) = change { return s?.leds[zone] }
        return nil
    }

    // MARK: protocol

    private func exchange(_ device: IOHIDDevice, target: Target, command: Command, log: inout [String]) -> Exchange {
        let sent = request(device, target: target, command: command, log: &log)
        if let error = sent.error { return Exchange(status: .error, raw: error) }
        return ReplyParser.exchange(replies: sent.replies, echo: command.expectedReply, reportID: target.reportID)
    }

    /// One command on a fresh device object. Built first: a command that is not on the allowlist never gets as
    /// far as opening the device. The wait ends at the first reply that echoes the command.
    private func request(_ listed: IOHIDDevice, target: Target, command: Command,
                         log: inout [String]) -> (replies: [[UInt8]], error: String?) {
        let packets: [[UInt8]]
        do {
            packets = try target.reportLengths.map { try Packet.build(device: target.device, reportID: target.reportID, command: command, length: $0) }
        } catch {
            log.append("[\(target.connection.rawValue)] refused: \(error)")
            return ([], "refused: \(error)")
        }
        let echo = Array(command.bytes.prefix(command.echoLength))
        // An `ff aa` alone does not end the wait: a valid reply may follow.
        return transport.request(listed, reportID: target.reportID, packets: packets,
                                 timeoutMs: SquireProtocol.readTimeoutMs, label: target.connection.rawValue,
                                 logBytes: command.isWrite ? 13 : 5,
                                 done: { replies in replies.contains { ReplyParser.echoIndex(in: $0, query: echo) != nil } },
                                 log: &log)
    }

    // MARK: helpers

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
        guard let packet = try? Packet.build(device: target.device, reportID: target.reportID, command: command,
                                             length: target.reportLengths[0]) else { return "refused" }
        return ReplyParser.hex(Array(packet.prefix(prefix)))
    }
}
