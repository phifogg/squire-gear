import Foundation
import SquireCore
import SquireHID

/// State of the settings window. Reads when the window opens or on "Read again"; writes only on Apply.
/// Same behaviour as linux/squire_settings.py.
@MainActor
final class SettingsStore: ObservableObject {
    static let shared = SettingsStore()

    /// `--demo`: show the captures from protocol.json (bundled); nothing is read or written.
    let demo = CommandLine.arguments.contains("--demo")

    @Published private(set) var settings: MouseSettings?
    @Published private(set) var keyboard: KeyboardSettings?
    /// The selected part of the keyboard page (keys = lighting, or a chip).
    @Published var keyboardZone: KeyboardZone = .light
    @Published private(set) var batteries: [Battery.Device: Battery] = [:]
    @Published var device: Battery.Device? = .mouse
    @Published var selected: SettingsZone = .logo
    @Published private(set) var pending: [LEDZone: LEDState] = [:]
    /// Pending power and sensor edits.
    @Published private(set) var other = OtherEdits()
    @Published private(set) var loading = false
    @Published private(set) var applying = false
    @Published var toast: String?
    @Published var failure: String?
    @Published var showBytes = false

    /// Keyboard edits not sent yet, and the lighting Squire last saved to the active profile (the keyboard cannot
    /// report its colour or brightness; KeyboardLightMemory, shared with squire.py).
    @Published private(set) var keyboardEdits = KeyboardEdits()
    @Published private(set) var keyboardKnown: KeyboardLight?
    /// Per-key colours this window last sent (the keyboard cannot report them), the brush colour a click paints,
    /// the key preset last loaded or saved, and the user's presets (keyboard-keys.json, shared with squire.py).
    @Published private(set) var keyboardKeysActive: [UInt8: String]?
    @Published var keyboardBrush = "#ff0000"
    @Published private(set) var keyboardPreset: String?
    @Published private(set) var userKeyPresets: [String: [UInt8: String]] = KeyPresets.load()

    var preview: MouseSettings? { settings.map { SettingsEditing.preview($0, pending: pending, other: other) } }
    var changes: [SettingChange] { settings.map { SettingsEditing.changes($0, pending: pending, other: other) } ?? [] }
    /// Apply, Revert and Show bytes act on the device shown.
    var isDirty: Bool { device == .keyboard ? !keyboardEdits.changes.isEmpty : !changes.isEmpty }

    func refresh() {
        guard !loading, !applying else { return }
        loading = true
        let demo = demo
        DispatchQueue.global(qos: .userInitiated).async {
            var batteries: [Battery] = []
            var settings: MouseSettings
            var keyboard: KeyboardSettings
            if demo, let demoData = Self.demoData() {
                (batteries, settings, keyboard) = demoData
            } else if demo {
                settings = MouseSettings(status: .error)
                keyboard = KeyboardSettings(status: .error)
            } else {
                // IOKit reads pump a run loop for up to a second or so: keep them off the main thread.
                batteries = HIDReader().read().batteries
                settings = HIDReader().readMouseSettings().settings
                keyboard = HIDReader().readKeyboardSettings().settings
            }
            DispatchQueue.main.async {
                self.loading = false
                // Over Bluetooth only the menu bar's reading exists (no settings): show that level in the sidebar.
                let bluetooth = demo ? [] : [BatteryStore.shared.keyboard, BatteryStore.shared.mouse]
                    .compactMap { $0 }.filter { $0.connection == .bluetooth }
                let merged = Bluetooth.merge(hid: batteries, bluetooth: bluetooth)
                self.batteries = Dictionary(uniqueKeysWithValues: merged.map { ($0.device, $0) })
                self.settings = settings
                self.keyboard = keyboard
                self.keyboardKnown = demo ? nil
                    : KeyboardLightMemory.remembered(profile: keyboard.profile, effect: keyboard.lightMode)
                // keep pending edits only for zones the mouse still reports
                self.pending = self.pending.filter { settings.leds[$0.key] != nil }
                self.other = SettingsEditing.edit(self.other, settings) { _ in }   // drop what now matches
            }
        }
    }

    private nonisolated static func demoData() -> (batteries: [Battery], settings: MouseSettings, keyboard: KeyboardSettings)? {
        guard let url = Bundle.main.url(forResource: "protocol", withExtension: "json"),
              let captures = try? ProtocolCaptures.load(url) else { return nil }
        let demo = captures.demo()
        return (demo.batteries, demo.settings, captures.demoKeyboard())
    }

    // MARK: editing

    func led(_ zone: LEDZone) -> LEDState? { pending[zone] ?? settings?.leds[zone] }

    /// "Same effect on all zones": every edit sets logo, scroll wheel and side light.
    @Published private(set) var linkZones = false

    func edit(_ zone: LEDZone, _ change: (inout LEDState) -> Void) {
        guard let settings else { return }
        pending = SettingsEditing.edit(settings, pending, zone: zone, linked: linkZones, change: change)
    }

    func canLink(_ zone: LEDZone) -> Bool {
        settings.map { SettingsEditing.canLink($0, pending: pending, zone: zone) } ?? false
    }

    /// Turning it on copies this zone to the others.
    func setLinkZones(_ on: Bool, from zone: LEDZone) {
        linkZones = on
        if on { edit(zone) { _ in } }
    }

    /// Power or sensor edit.
    func editOther(_ change: (inout OtherEdits) -> Void) {
        guard let settings else { return }
        other = SettingsEditing.edit(other, settings, change: change)
    }

    /// Active stage or stage count (they stay consistent).
    func editDPIStages(active: Int? = nil, count: Int? = nil) {
        guard let settings else { return }
        other = SettingsEditing.editDPIStages(other, settings, active: active, count: count)
    }

    /// Power as it will be after Apply (pending edit, else what the mouse has).
    var power: PowerSetting? {
        if let power = other.power { return power }
        guard let s = settings, let warning = s.lowWarning else { return nil }
        return PowerSetting(offMinutes: s.powerOffMinutes, lowWarning: warning)
    }

    func revert() {
        if device == .keyboard {
            keyboardEdits = KeyboardEdits()
            return
        }
        pending.removeAll()
        other = OtherEdits()
        linkZones = false
    }

    var applyStatus: String {
        if applying { return "Applying…" }
        if device == .keyboard { return KeyboardEditing.applyStatus(keyboardEdits) }
        guard let settings else { return "" }
        return SettingsEditing.applyStatus(settings, pending: pending, other: other)
    }

    var plannedPackets: [String] {
        if device == .keyboard { return keyboard.map { KeyboardEditing.plannedPackets($0, keyboardEdits) } ?? [] }
        return settings.map { SettingsEditing.plannedPackets($0, pending: pending, other: other) } ?? []
    }

    // MARK: per-key colours

    /// Per-key colours are being edited, or were sent last and no effect is pending.
    var perKeyMode: Bool {
        keyboardEdits.keys != nil || (keyboardKeysActive != nil && keyboardEdits.light == nil)
    }

    /// The colours the editor works on: the pending edit, else what was sent last, else all keys off.
    var currentKeys: [UInt8: String] {
        keyboardEdits.keys ?? keyboard.map { KeyboardEditing.keysStart($0, active: keyboardKeysActive) } ?? [:]
    }

    /// What the drawing shows: per-key colours, or nil for the effect's glow.
    var shownKeys: [UInt8: String]? {
        KeyboardEditing.shownKeys(keyboardEdits, active: keyboardKeysActive, perKeyMode: perKeyMode)
    }

    var builtinKeyPresets: [String: [UInt8: String]] { KeyPresets.builtin(iso: keyboard?.iso) }

    /// The effect row: an effect, or "Per-key colors".
    func selectEffect(_ choice: String) {
        guard let keyboard else { return }
        if choice == KeyboardEditing.perKey {
            if !perKeyMode { keyboardEdits = KeyboardEditing.selectPerKey(keyboard, keyboardEdits, active: keyboardKeysActive) }
            return
        }
        let leaving = perKeyMode
        keyboardEdits.keys = nil
        editKeyboardLight { $0.effect = choice }
        // leaving per-key colours: the effect is sent even if it is the one Squire knows
        if leaving && keyboardEdits.light == nil && keyboardKeysActive != nil {
            keyboardEdits.light = KeyboardEditing.lightStart(keyboard, known: keyboardKnown)
        }
    }

    func paintKeys(_ changes: [UInt8: String]) {
        guard let keyboard else { return }
        let base = keyboardEdits.keys == nil ? currentKeys : [:]
        keyboardEdits = KeyboardEditing.paint(keyboard, keyboardEdits, active: keyboardKeysActive,
                                              base.merging(changes) { _, new in new })
    }

    func paintKey(_ id: UInt8) {
        if currentKeys[id] != keyboardBrush.lowercased() { paintKeys([id: keyboardBrush]) }
    }

    func loadKeyPreset(_ name: String) {
        guard let keyboard, let keys = userKeyPresets[name] ?? builtinKeyPresets[name] else { return }
        keyboardPreset = name
        paintKeys(KeyboardEditing.fullKeys(keyboard, keys))
    }

    /// Stores the current colours as a preset in Squire (never on the keyboard); nil, or the reason it failed.
    func saveKeyPreset(_ name: String) -> String? {
        do {
            try KeyPresets.save(name, currentKeys)
        } catch {
            return "\(error)"
        }
        userKeyPresets = KeyPresets.load()
        keyboardPreset = name.trimmingCharacters(in: .whitespaces)
        show(toast: "Preset \u{201C}\(keyboardPreset ?? name)\u{201D} saved in Squire")
        return nil
    }

    func deleteKeyPreset() {
        guard let name = keyboardPreset, userKeyPresets[name] != nil else { return }
        _ = try? KeyPresets.delete(name)
        userKeyPresets = KeyPresets.load()
        keyboardPreset = nil
    }

    // MARK: keyboard editing

    /// The lighting as the editor shows it: the pending edit, else what Squire saved, else the effect read.
    var keyboardLight: KeyboardLight? {
        keyboard.map { keyboardEdits.light ?? KeyboardEditing.lightStart($0, known: keyboardKnown) }
    }

    var keyboardPreview: (settings: KeyboardSettings, light: KeyboardLight?)? {
        keyboard.map { KeyboardEditing.preview($0, keyboardEdits, known: keyboardKnown) }
    }

    func editKeyboardLight(_ change: (inout KeyboardLight) -> Void) {
        guard let keyboard else { return }
        keyboardEdits = KeyboardEditing.editLight(keyboard, keyboardEdits, known: keyboardKnown, change)
    }

    func editKeyboardSleep(_ minutes: Int?) {
        guard let keyboard else { return }
        keyboardEdits = KeyboardEditing.editSleep(keyboard, keyboardEdits, minutes)
    }

    func editKeyboardWarning(_ percent: Int) {
        guard let keyboard else { return }
        keyboardEdits = KeyboardEditing.editWarning(keyboard, keyboardEdits, percent)
    }

    /// Same as the mouse: sleep, warning, then the lighting; every chunk echoed, read back, and one 50 55 with the
    /// last of them, only if all earlier ones matched. Per-key colours go last and are never saved.
    private func applyKeyboard() {
        let work = keyboardEdits.changes
        guard !work.isEmpty else { return }
        applying = true
        let demo = demo
        let savedWork = work.filter(\.isSaved)
        DispatchQueue.global(qos: .userInitiated).async {
            var results: [(KeyboardChange, KeyboardWriteResult)] = []
            for change in work {
                let result = demo
                    ? KeyboardWriteResult(status: .ok, saved: change.isSaved, message: "demo")   // nothing is sent
                    : HIDReader().writeKeyboard(change, save: change == savedWork.last).result
                results.append((change, result))
                if result.status != .ok { break }
            }
            let savedResults = results.filter { $0.0.isSaved }
            let saved = savedResults.count == savedWork.count && savedResults.allSatisfy { $0.1.status == .ok }
                && (savedWork.isEmpty || savedResults.last?.1.saved == true)
            for i in results.indices where results[i].0.isSaved { results[i].1.saved = saved }
            DispatchQueue.main.async { self.keyboardApplyDone(results, onlyKeys: savedWork.isEmpty) }
        }
    }

    private func keyboardApplyDone(_ results: [(KeyboardChange, KeyboardWriteResult)], onlyKeys: Bool) {
        applying = false
        var failed: [(KeyboardChange, KeyboardWriteResult)] = []
        for (change, result) in results {
            if case .keys(let keys) = change {
                if result.status == .ok {
                    keyboardKeysActive = keys
                    keyboardEdits.keys = nil
                } else {
                    failed.append((change, result))
                }
                continue
            }
            guard result.status == .ok, result.saved else { failed.append((change, result)); continue }
            switch change {
            case .sleep(let minutes):
                keyboard?.sleepMinutes = minutes
                if let raw = result.after?.raw["battery"] { keyboard?.raw["battery"] = raw }
                keyboardEdits.sleep = nil
            case .warning(let percent):
                keyboard?.lowWarning = percent
                if let raw = result.after?.raw["battery"] { keyboard?.raw["battery"] = raw }
                keyboardEdits.warning = nil
            case .light(let light):
                keyboard?.lightMode = KeyboardLighting.effectRead(light.effect)
                if !demo, let profile = result.after?.profile ?? keyboard?.profile {
                    KeyboardLightMemory.remember(light, profile: profile)
                }
                keyboardKnown = light
                keyboardEdits.light = nil
                keyboardKeysActive = nil          // the effect replaces per-key colours (confirmed 2026-11-25)
            case .keys:
                break                             // handled above
            }
        }
        if failed.isEmpty {
            show(toast: (onlyKeys ? "Sent to the keyboard (not stored on it)" : "Saved to the keyboard")
                 + (demo ? " (demo, nothing sent)" : ""))
            return
        }
        let texts: [WriteResult.Status: String] = [
            .asleep: "The keyboard is asleep. Press a key and click Apply again.",
            .ok: "Written to the keyboard's memory but not saved (lost when it is switched off).",
            .mismatch: "The keyboard did not take the new value (read back differs). Nothing was saved.",
            .noResponse: "The keyboard did not answer. Press a key and click Apply again.",
        ]
        failure = failed.map { (change: KeyboardChange, result: KeyboardWriteResult) -> String in
            let text: String = texts[result.status] ?? "\(result.status.rawValue) \(result.message)"
            let detail: String = result.status == .ok && !result.message.isEmpty ? " (\(result.message))" : ""
            return "\(change.title): \(text)\(detail)"
        }.joined(separator: "\n")
    }

    /// Writes every change, reads each one back, and saves (50 03) once with the last one, only if all earlier
    /// ones matched. Stops at the first failure: nothing more is written and nothing is saved.
    func apply() {
        guard !applying else { return }
        if device == .keyboard { return applyKeyboard() }
        let work = changes
        guard !work.isEmpty else { return }
        applying = true
        let demo = demo
        DispatchQueue.global(qos: .userInitiated).async {
            var results: [(SettingChange, WriteResult)] = []
            for (i, change) in work.enumerated() {
                let result = demo
                    ? Self.demoResult(change)   // nothing is sent
                    : HIDReader().write(change, save: i == work.count - 1).result
                results.append((change, result))
                if result.status != .ok { break }
            }
            let saved = results.count == work.count && results.allSatisfy { $0.1.status == .ok }
                && results.last?.1.saved == true
            for i in results.indices { results[i].1.saved = saved }
            DispatchQueue.main.async { self.applyDone(results) }
        }
    }

    private nonisolated static func demoResult(_ change: SettingChange) -> WriteResult {
        var r = WriteResult(status: .ok, saved: true, message: "demo")
        if case .led(_, let led) = change { r.after = led }
        r.readBack = change.applied(to: MouseSettings())
        return r
    }

    private func applyDone(_ results: [(SettingChange, WriteResult)]) {
        applying = false
        var failed: [(SettingChange, WriteResult)] = []
        for (change, result) in results {
            guard result.status == .ok, result.saved else { failed.append((change, result)); continue }
            settings = settings.map { change.applied(to: $0) }
            switch change {
            case .led(let zone, let led):
                settings?.leds[zone] = result.after ?? led
                pending[zone] = nil
            case .power: other.power = nil
            case .angleSnapping: other.angleSnapping = nil
            case .angleTuning: other.angleTuning = nil
            case .liftoff:
                if let back = result.readBack, back.surfaceID != nil {   // the surface as read back with it
                    settings?.surfaceID = back.surfaceID
                    settings?.surfaceParams = back.surfaceParams
                }
                other.liftoff = nil
            case .surface: other.surface = nil
            case .polling(let hz):
                if let raw = settings?.pollingRaw, let index = Command.pollingWriteHz[hz] { settings?.pollingRaw = raw & 0xF8 | index }
                other.polling = nil
            case .dpi(let stage, _): other.dpi[stage] = nil
            case .activeStage: other.activeStage = nil
            case .stageCount: other.stageCount = nil
            }
        }
        if failed.isEmpty {
            show(toast: "Saved to the mouse" + (demo ? " (demo, nothing sent)" : ""))
            return
        }
        let texts: [WriteResult.Status: String] = [
            .asleep: "The mouse is asleep. Move it and click Apply again.",
            .ok: "Written to the mouse's memory but not saved (lost when it is switched off).",
            .mismatch: "The mouse did not take the new value (read back differs). Nothing was saved.",
            .noResponse: "The mouse did not answer. Move it and click Apply again.",
        ]
        failure = failed.map { (change: SettingChange, result: WriteResult) -> String in
            let text: String = texts[result.status] ?? "\(result.status.rawValue) \(result.message)"
            let detail: String = result.status == .ok && !result.message.isEmpty ? " (\(result.message))" : ""
            return "\(change.title): \(text)\(detail)"
        }.joined(separator: "\n")
    }

    private func show(toast text: String) {
        toast = text
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            if self?.toast == text { self?.toast = nil }
        }
    }
}
