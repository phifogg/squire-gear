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
    @Published private(set) var batteries: [Battery.Device: Battery] = [:]
    @Published var device: Battery.Device? = .mouse
    @Published var selected: SettingsZone = .logo
    @Published private(set) var pending: [LEDZone: LEDState] = [:]
    @Published private(set) var loading = false
    @Published private(set) var applying = false
    @Published var toast: String?
    @Published var failure: String?
    @Published var showBytes = false

    var preview: MouseSettings? { settings.map { SettingsEditing.preview($0, pending: pending) } }
    var isDirty: Bool { settings.map { !SettingsEditing.changedZones($0, pending: pending).isEmpty } ?? false }

    func refresh() {
        guard !loading, !applying else { return }
        loading = true
        let demo = demo
        DispatchQueue.global(qos: .userInitiated).async {
            var batteries: [Battery] = []
            var settings: MouseSettings
            if demo, let demoData = Self.demoData() {
                (batteries, settings) = demoData
            } else if demo {
                settings = MouseSettings(status: .error)
            } else {
                // IOKit reads pump a run loop for up to a second or so: keep them off the main thread.
                batteries = HIDReader().read().batteries
                settings = HIDReader().readMouseSettings().settings
            }
            DispatchQueue.main.async {
                self.loading = false
                self.batteries = Dictionary(uniqueKeysWithValues: batteries.map { ($0.device, $0) })
                self.settings = settings
                // keep pending edits only for zones the mouse still reports
                self.pending = self.pending.filter { settings.leds[$0.key] != nil }
            }
        }
    }

    private nonisolated static func demoData() -> (batteries: [Battery], settings: MouseSettings)? {
        guard let url = Bundle.main.url(forResource: "protocol", withExtension: "json"),
              let captures = try? ProtocolCaptures.load(url) else { return nil }
        return captures.demo()
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

    func revert() {
        pending.removeAll()
        linkZones = false
    }

    var applyStatus: String {
        guard let settings else { return "" }
        return applying ? "Applying…" : SettingsEditing.applyStatus(settings, pending: pending)
    }

    var plannedPackets: [String] {
        settings.map { SettingsEditing.plannedPackets($0, pending: pending) } ?? []
    }

    /// Writes every changed zone, reads each one back, and saves (50 03) once with the last one, only if all
    /// earlier ones matched. Stops at the first failure: nothing more is written and nothing is saved.
    func apply() {
        guard let settings, !applying else { return }
        let zones = SettingsEditing.changedZones(settings, pending: pending)
        guard !zones.isEmpty else { return }
        applying = true
        let work = zones.map { ($0, pending[$0]!) }
        let demo = demo
        DispatchQueue.global(qos: .userInitiated).async {
            var results: [(LEDZone, WriteResult)] = []
            for (i, (zone, led)) in work.enumerated() {
                let result = demo
                    ? WriteResult(status: .ok, after: led, saved: true, message: "demo")   // nothing is sent
                    : HIDReader().setLED(zone, led, save: i == work.count - 1).result
                results.append((zone, result))
                if result.status != .ok { break }
            }
            let saved = results.count == work.count && results.allSatisfy { $0.1.status == .ok }
                && results.last?.1.saved == true
            for i in results.indices { results[i].1.saved = saved }
            DispatchQueue.main.async { self.applyDone(results) }
        }
    }

    private func applyDone(_ results: [(LEDZone, WriteResult)]) {
        applying = false
        var failed: [(LEDZone, WriteResult)] = []
        for (zone, result) in results {
            if result.status == .ok, result.saved, let after = result.after {
                settings?.leds[zone] = after
                pending[zone] = nil
            } else {
                failed.append((zone, result))
            }
        }
        if failed.isEmpty {
            show(toast: "Saved to the mouse" + (demo ? " (demo, nothing sent)" : ""))
            return
        }
        let texts: [WriteResult.Status: String] = [
            .asleep: "The mouse is asleep. Move it and click Apply again.",
            .ok: "Written, but not saved because another change failed.",
            .mismatch: "The mouse did not take the new value (read back differs). Nothing was saved.",
            .noResponse: "The mouse did not answer. Move it and click Apply again.",
        ]
        failure = failed.map { zone, result in
            let title = SettingsZone(rawValue: zone.rawValue)?.title ?? zone.rawValue
            return "\(title): " + (texts[result.status] ?? "\(result.status.rawValue) \(result.message)")
        }.joined(separator: "\n")
    }

    private func show(toast text: String) {
        toast = text
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            if self?.toast == text { self?.toast = nil }
        }
    }
}
