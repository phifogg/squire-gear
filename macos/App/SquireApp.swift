import SwiftUI
import SquireCore

@main
struct SquireApp: App {
    @StateObject private var store = BatteryStore.shared

    init() {
        BatteryStore.shared.start()   // poll from launch, not only when the popover opens
        Self.handleLaunchArguments()
    }

    /// Development aids: `--settings` opens the settings window at launch; `--screenshot PATH [ZONE|keyboard] [edit] [linked]`
    /// (`keyboard edit`: a sample lighting edit on the keyboard page, `keyboard keys`: a per-key preset; never sent)
    /// also saves it as PNG and quits (`linked`: the edit with "Same effect on all zones" on); `--demo` shows the
    /// protocol.json captures and sends nothing.
    private static func handleLaunchArguments() {
        let args = CommandLine.arguments
        guard args.contains("--settings") || args.contains("--screenshot") else { return }
        DispatchQueue.main.async {
            SettingsWindowController.shared.show()
            guard let i = args.firstIndex(of: "--screenshot"), i + 1 < args.count else { return }
            if i + 2 < args.count, let zone = SettingsZone(rawValue: args[i + 2]) { SettingsStore.shared.selected = zone }
            if i + 2 < args.count, args[i + 2] == "keyboard" { SettingsStore.shared.device = .keyboard }
            if args.contains("keys") {   // sample per-key edit, never sent: the "Fixed rainbow" default preset
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    let store = SettingsStore.shared
                    store.keyboardZone = .light
                    store.selectEffect(KeyboardEditing.perKey)
                    store.loadKeyPreset("Fixed rainbow")
                    store.showBytes = true
                }
            }
            if args.contains("edit") {   // sample edit, never sent: static blue on the selected light
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    let store = SettingsStore.shared
                    if i + 2 < args.count, args[i + 2] == "keyboard" {   // sample: raindrop, two colours, green background
                        store.keyboardZone = .light
                        store.editKeyboardLight {
                            $0.effect = "raindrop"; $0.mode = "double"; $0.color = "#3d8bff"; $0.color2 = "#ff00ff"
                            $0.background = "#1bff00"
                        }
                        store.showBytes = true
                        return
                    }
                    store.device = .mouse
                    if let zone = store.selected.ledZone {
                        if args.contains("linked") { store.setLinkZones(true, from: zone) }
                        store.edit(zone) { $0 = LEDState(mode: .static, brightness: 100, color: RGB(0x3D, 0x8B, 0xFF)) }
                    } else if store.selected == .power {   // sample: off after 10 min, warn at 30 %
                        store.editOther { $0.power = PowerSetting(offMinutes: 10, lowWarning: 30) }
                    } else if store.selected == .dpi {     // sample: stage 3 700 DPI in blue, active
                        store.editOther { $0.dpi[3] = DPIStage(dpi: 700, color: RGB(0x00, 0x3D, 0xFF)) }
                        store.editDPIStages(active: 3)
                    } else if store.selected == .sensor {  // sample: snapping on, tuning -5°, lift-off high
                        store.editOther { $0.angleSnapping = true; $0.angleTuning = -5; $0.liftoff = "high" }
                    }
                    store.showBytes = true
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                SettingsWindowController.shared.screenshot(to: args[i + 1])
                NSApplication.shared.terminate(nil)
            }
        }
    }

    var body: some Scene {
        MenuBarExtra {
            PopoverView(store: store)
        } label: {
            MenuBarLabel(store: store)
        }
        .menuBarExtraStyle(.window)
    }
}
