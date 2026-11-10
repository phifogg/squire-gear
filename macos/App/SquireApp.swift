import SwiftUI

@main
struct SquireApp: App {
    @StateObject private var store = BatteryStore.shared

    init() {
        BatteryStore.shared.start()   // poll from launch, not only when the popover opens
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
