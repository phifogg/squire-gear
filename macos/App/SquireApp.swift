import SwiftUI
import SquireCore

@main
struct SquireApp: App {
    @StateObject private var store = BatteryStore.shared

    init() {
        BatteryStore.shared.start()   // poll from launch, not only when the popover opens
        Self.handleLaunchArguments()
    }

    /// Development aids: `--settings` opens the settings window at launch; `--screenshot PATH [ZONE] [edit] [linked]`
    /// also saves it as PNG and quits (`linked`: the edit with "Same effect on all zones" on); `--demo` shows the
    /// protocol.json captures and sends nothing.
    private static func handleLaunchArguments() {
        let args = CommandLine.arguments
        guard args.contains("--settings") || args.contains("--screenshot") else { return }
        DispatchQueue.main.async {
            SettingsWindowController.shared.show()
            guard let i = args.firstIndex(of: "--screenshot"), i + 1 < args.count else { return }
            if i + 2 < args.count, let zone = SettingsZone(rawValue: args[i + 2]) { SettingsStore.shared.selected = zone }
            if args.contains("edit") {   // sample edit, never sent: static blue on the selected light
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    let store = SettingsStore.shared
                    store.device = .mouse
                    if let zone = store.selected.ledZone {
                        if args.contains("linked") { store.setLinkZones(true, from: zone) }
                        store.edit(zone) { $0 = LEDState(mode: .static, brightness: 100, color: RGB(0x3D, 0x8B, 0xFF)) }
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
