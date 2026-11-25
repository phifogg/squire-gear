import Foundation

// Logic of the keyboard page in the settings window, same as the kb_* functions in linux/squire_settings.py. UI-free
// so it can be tested. The keyboard reports only its effect, never colour or brightness: `known` is what Squire last
// saved to the active profile (KeyboardLightMemory), or nil.

/// A pending sleep value (minutes, nil = never).
public struct KeyboardSleep: Equatable, Hashable, Sendable {
    public var minutes: Int?
    public init(_ minutes: Int?) { self.minutes = minutes }
}

/// Keyboard edits not sent yet.
public struct KeyboardEdits: Equatable, Sendable {
    public var sleep: KeyboardSleep?
    public var warning: Int?            // %, 0 = never
    public var light: KeyboardLight?    // as edited (kept fields included); sent normalised
    /// Per-key colours {key id: "#rrggbb"}, every key of the layout; one of `light` and `keys` is pending, not both.
    public var keys: [UInt8: String]?
    public init() {}

    public var isEmpty: Bool { sleep == nil && warning == nil && light == nil && keys == nil }

    /// Apply order: sleep, warning, lighting (the last of these is saved), then per-key colours (never saved).
    public var changes: [KeyboardChange] {
        [sleep.map { KeyboardChange.sleep($0.minutes) }, warning.map { KeyboardChange.warning($0) },
         light.flatMap { try? KeyboardLighting.normalized($0) }.map { KeyboardChange.light($0) },
         keys.map { KeyboardChange.keys($0) }].compactMap { $0 }
    }

    /// The changes Apply saves (50 55 once, with the last of them).
    public var savedChanges: [KeyboardChange] { changes.filter(\.isSaved) }
}

public enum KeyboardEditing {
    /// What the lighting editor shows before any edit: what Squire saved, else the effect read with AC's defaults.
    public static func lightStart(_ k: KeyboardSettings, known: KeyboardLight?) -> KeyboardLight {
        if let known { return known }
        let effect = k.lightMode.flatMap { KeyboardLighting.speeds[$0] != nil ? $0 : nil } ?? "static"
        return (try? KeyboardLighting.normalized(KeyboardLight(effect: effect))) ?? KeyboardLight(effect: "static")
    }

    /// Whether the keyboard runs an effect Squire can write (else the editor starts from static).
    public static func isEditing(_ k: KeyboardSettings, _ edits: KeyboardEdits) -> Bool {
        edits.light != nil || k.lightMode.map { KeyboardLighting.speeds[$0] != nil } == true
    }

    /// Does a lighting value show as `mode` on the keyboard ("off" reads as static)?
    public static func sameEffect(_ light: KeyboardLight?, _ mode: String?) -> Bool {
        guard let light, let mode else { return false }
        return mode == light.effect || mode == KeyboardLighting.effectRead(light.effect)
    }

    /// A lighting edit. A new effect starts at its default speed, direction and colour mode; colours, gradient,
    /// thickness and background stay as chosen for a later effect that uses them. The edit is dropped only when it
    /// is back to what Squire knows the keyboard has.
    public static func editLight(_ k: KeyboardSettings, _ edits: KeyboardEdits, known: KeyboardLight?,
                                 _ change: (inout KeyboardLight) -> Void) -> KeyboardEdits {
        let old = edits.light ?? lightStart(k, known: known)
        var light = old
        change(&light)
        if light.effect != old.effect {                 // the new effect's defaults, unless this edit set them
            if light.speed == old.speed { light.speed = nil }
            if light.direction == old.direction { light.direction = nil }
            if light.mode == old.mode { light.mode = nil }
            if old.effect == KeyboardLighting.off {      // off fixed them at 4 % black
                if light.brightness == old.brightness { light.brightness = 100 }
                if light.color == old.color { light.color = "#ffffff" }
            }
        }
        guard let sent = try? KeyboardLighting.normalized(light) else { return edits }
        light.speed = sent.speed
        light.direction = sent.direction
        light.mode = sent.mode
        light.gradient = sent.gradient ?? light.gradient
        light.thickness = sent.thickness ?? light.thickness
        light.color2 = sent.color2 ?? light.color2
        if light.effect == KeyboardLighting.off {
            light.brightness = sent.brightness
            light.color = sent.color
        }
        var e = edits
        if let known, (try? KeyboardLighting.normalized(known)) == sent, sameEffect(light, k.lightMode) {
            e.light = nil
        } else {
            e.light = light
        }
        return e
    }

    /// Sleep or warning edits: kept pending, or dropped when back to what the keyboard has.
    public static func editSleep(_ k: KeyboardSettings, _ edits: KeyboardEdits, _ minutes: Int?) -> KeyboardEdits {
        var e = edits
        e.sleep = KeyboardChange.sleep(minutes).matches(k) ? nil : KeyboardSleep(minutes)
        return e
    }

    public static func editWarning(_ k: KeyboardSettings, _ edits: KeyboardEdits, _ percent: Int) -> KeyboardEdits {
        var e = edits
        e.warning = k.lowWarning == percent ? nil : percent
        return e
    }

    /// Only a sleep code Squire can write back (nil means "never" only for ff, not for an unknown code).
    public static func canEditSleep(_ k: KeyboardSettings) -> Bool {
        if let raw = k.raw["battery"]?.split(separator: " ").compactMap({ UInt8($0, radix: 16) }), raw.count > 6 {
            return Command.powerOffCodes.values.contains(raw[6])
        }
        return Command.powerOffCodes[k.sleepMinutes] != nil
    }

    public static func canEditWarning(_ k: KeyboardSettings) -> Bool {
        k.lowWarning.map { Command.keyboardWarningLevels.contains($0) } ?? false
    }

    /// (settings, lighting) as they will be after Apply, for the drawing and the cards.
    public static func preview(_ k: KeyboardSettings, _ edits: KeyboardEdits, known: KeyboardLight?)
        -> (settings: KeyboardSettings, light: KeyboardLight?) {
        var p = k
        if let sleep = edits.sleep { p.sleepMinutes = sleep.minutes }
        if let warning = edits.warning { p.lowWarning = warning }
        if let light = edits.light { p.lightMode = light.effect }   // "off" draws as off
        return (p, edits.light ?? known)
    }

    /// The lighting card: the effect, with the brightness when Squire knows it.
    public static func lightSummary(_ k: KeyboardSettings, light: KeyboardLight?) -> String {
        guard let light, sameEffect(light, k.lightMode) else { return KeyboardSettings.effectTitle(k.lightMode) }
        if light.effect == KeyboardLighting.off { return "Off" }
        return "\(KeyboardSettings.effectTitle(k.lightMode)) · \(light.brightness) %"
    }

    public static func applyStatus(_ edits: KeyboardEdits) -> String {
        let changes = edits.changes
        guard !changes.isEmpty else {
            return "Showing the values read from the keyboard. Nothing is sent until you click Apply."
        }
        let names = changes.map(\.title).joined(separator: ", ")
        if edits.savedChanges.isEmpty {
            return "Not sent yet: \(names). Apply sends them; the keyboard keeps them until it is switched off."
        }
        return "Not sent yet: \(names). Apply writes it to the keyboard, reads it back and saves it."
            + (edits.keys != nil ? " Per-key colors are sent but not saved." : "")
    }

    /// Exactly what Apply will send, for "Show bytes".
    public static func plannedPackets(_ k: KeyboardSettings, _ edits: KeyboardEdits) -> [String] {
        let omni = k.connection != .wired
        let reportID: UInt8? = omni ? 0x02 : nil
        let length = omni ? 64 : 65
        func packets(_ change: KeyboardChange) -> [String] {
            ((try? change.commands()) ?? []).compactMap { command in
                (try? Packet.build(device: .keyboard, reportID: reportID, command: command, length: length))
                    .map { ReplyParser.hex(Array($0.prefix(command.bytes.count + 1))) + " …" }
            }
        }
        var lines = edits.savedChanges.flatMap(packets)
        if !lines.isEmpty,
           let bytes = try? Packet.build(device: .keyboard, reportID: reportID, command: .keyboardSave, length: length) {
            lines.append(ReplyParser.hex(Array(bytes.prefix(3))) + " …  (save)")
        }
        if let keys = edits.keys {
            let perKey = packets(.keys(keys))
            lines += perKey
            if !perKey.isEmpty { lines[lines.count - 1] += "  (per-key colors, not saved)" }
        }
        return lines
    }

    // MARK: per-key colours (same as kb_* in linux/squire_settings.py)

    /// The effect row: effects by name, then per-key colours, then off.
    public static let perKey = "per-key"
    public static let effectChoices: [String] =
        KeyboardLighting.effects.filter { $0 != KeyboardLighting.off }
            .sorted { KeyboardSettings.effectTitle($0).localizedCaseInsensitiveCompare(KeyboardSettings.effectTitle($1))
                      == .orderedAscending } + [perKey, KeyboardLighting.off]

    public static func effectChoiceTitle(_ choice: String) -> String {
        choice == perKey ? "Per-key colors" : KeyboardSettings.effectTitle(choice)
    }

    public static let perKeyNote = "The keyboard cannot store per-key colors: they are lost when it is switched off, "
        + "and its own profile switching is blocked until then. Save them as a preset in Squire to reuse them."

    /// Every key of the layout; keys not in `keys` off (black).
    public static func fullKeys(_ k: KeyboardSettings, _ keys: [UInt8: String]) -> [UInt8: String] {
        KeyPresets.full(keys, iso: k.iso)
    }

    /// Per-key colours before any edit: what this window last sent, else every key off.
    public static func keysStart(_ k: KeyboardSettings, active: [UInt8: String]?) -> [UInt8: String] {
        fullKeys(k, active ?? [:])
    }

    /// Choosing "Per-key colors" replaces a pending effect (one or the other is sent).
    public static func selectPerKey(_ k: KeyboardSettings, _ edits: KeyboardEdits, active: [UInt8: String]?)
        -> KeyboardEdits {
        var e = edits
        e.light = nil
        if e.keys == nil { e.keys = keysStart(k, active: active) }
        return e
    }

    /// Set some keys; the edit is dropped when it is back to what was last sent.
    public static func paint(_ k: KeyboardSettings, _ edits: KeyboardEdits, active: [UInt8: String]?,
                             _ changes: [UInt8: String]) -> KeyboardEdits {
        var keys = edits.keys ?? keysStart(k, active: active)
        for (key, colour) in changes where keys[key] != nil {
            if let hex = KeyboardLighting.rgbHex(colour) { keys[key] = hex }
        }
        var e = edits
        e.light = nil
        if let active, keys == fullKeys(k, active) {
            e.keys = nil
        } else {
            e.keys = keys
        }
        return e
    }

    /// The per-key colours the drawing shows, or nil (the effect's glow).
    public static func shownKeys(_ edits: KeyboardEdits, active: [UInt8: String]?, perKeyMode: Bool)
        -> [UInt8: String]? {
        if let keys = edits.keys { return keys }
        return perKeyMode && edits.light == nil ? active : nil
    }

    /// The key id under a point in drawing coordinates (KeyboardDrawing / keyboard_svg), or nil between keys.
    public static func keyAt(_ k: KeyboardSettings?, x: Double, y: Double,
                             unit: Double = 36, gap: Double = 4, pad: Double = 18) -> UInt8? {
        let rows = KeyboardLayout.rows(for: k), ids = KeyboardLayout.ids(for: k)
        let r = Int(((y - pad) / unit).rounded(.down))
        guard rows.indices.contains(r), (y - pad) - Double(r) * unit <= unit - gap else { return nil }
        var left = pad
        for (i, key) in rows[r].enumerated() {
            if x >= left && x < left + key.width * unit - gap { return ids[r][i] }
            left += key.width * unit
        }
        return nil
    }

    // Names shown in the editor (same as the Linux window).
    public static let gradientNames = ["red": "Red", "blue-red": "Blue to red", "sunset": "Sunset",
                                       "lime-cyan": "Lime and cyan", "yellow-magenta": "Yellow and magenta",
                                       "pink-purple": "Pink and purple", "spectrum": "Spectrum"]
    public static let directionNames = ["right": "Left to right", "left": "Right to left", "down": "Top to bottom",
                                        "up": "Bottom to top", "down-right": "To bottom right",
                                        "down-left": "To bottom left", "up-left": "To top left",
                                        "up-right": "To top right", "outwards": "From the center outwards",
                                        "none": "None"]
    public static let speedNames = ["fast": "Fast", "normal": "Normal", "slow": "Slow", "none": "None"]
    public static let thicknessNames = ["low": "Low", "normal": "Normal", "strong": "Strong"]
    public static let modeNames = ["single": "One color", "double": "Two colors", "random": "Random",
                                   "scale": "Color scale"]
}
