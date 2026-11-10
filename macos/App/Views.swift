import ServiceManagement
import SwiftUI
import SquireCore

/// Same thresholds as the Python/SwiftBar version: red at or below the device's warning level,
/// orange up to 30 %, green while charging.
func batteryColor(for battery: Battery) -> Color {
    guard let percent = battery.percent else { return .gray }
    if battery.charging { return .green }
    if percent <= (battery.lowWarning ?? SquireProtocol.defaultLowWarning) { return .red }
    return percent <= 30 ? .orange : .accentColor
}

struct MenuBarLabel: View {
    @ObservedObject var store: BatteryStore

    // MenuBarExtra only shows a single image or plain text: inline SF Symbols in Text are dropped and a
    // second HStack child is ignored. So the whole label is drawn into one template NSImage, which macOS
    // tints for light/dark mode.
    var body: some View {
        Image(nsImage: rendered)
    }

    private var rendered: NSImage {
        let content = HStack(spacing: 8) {
            part("keyboard", store.keyboard)
            part("computermouse", store.mouse)
        }
        .font(.system(size: 13, weight: .medium))
        .foregroundStyle(.black)   // template images use only the alpha channel
        .fixedSize()
        let renderer = ImageRenderer(content: content)
        renderer.scale = NSScreen.main?.backingScaleFactor ?? 2
        let image = renderer.nsImage ?? NSImage()
        image.isTemplate = true
        return image
    }

    private func part(_ symbol: String, _ battery: Battery?) -> some View {
        HStack(spacing: 3) {
            Image(systemName: symbol)
            Text(text(battery))
        }
    }

    private func text(_ battery: Battery?) -> String {
        guard let percent = battery?.percent else { return "–" }
        return "\(percent)%" + (battery?.charging == true ? "⚡︎" : "")
    }
}

struct PopoverView: View {
    @ObservedObject var store: BatteryStore
    @AppStorage(BatteryStore.notifyKey) private var notifyWhenLow = true
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if store.keyboard == nil && store.mouse == nil {
                Label(store.lastUpdate == nil ? "Looking for devices…" : "No ASUS devices found",
                      systemImage: "keyboard")
                    .foregroundStyle(.secondary)
            }
            if let keyboard = store.keyboard { DeviceRow(battery: keyboard) }
            if let mouse = store.mouse { DeviceRow(battery: mouse) }
            Divider()
            Toggle("Notify when low", isOn: $notifyWhenLow)
            Toggle("Launch at login", isOn: $launchAtLogin)
                .onChange(of: launchAtLogin) { enabled in
                    do {
                        if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                    } catch {
                        launchAtLogin = SMAppService.mainApp.status == .enabled
                    }
                }
            Divider()
            HStack {
                Button("Refresh now") { store.refresh() }.disabled(store.isRefreshing)
                Spacer()
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
        }
        .padding(14)
        .frame(width: 280)
    }
}

struct DeviceRow: View {
    let battery: Battery

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(battery.name, systemImage: battery.device == .keyboard ? "keyboard" : "computermouse")
                .font(.headline)
            if let percent = battery.percent {
                HStack {
                    ProgressView(value: Double(percent), total: 100).tint(batteryColor(for: battery))
                    Text("\(percent)%\(battery.charging ? " ⚡︎" : "")").monospacedDigit()
                }
                Text("\(connection) · warning at \(battery.lowWarning ?? SquireProtocol.defaultLowWarning)%")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text(statusText).foregroundStyle(.secondary)
            }
        }
    }

    private var connection: String { battery.connection == .wired ? "Wired" : "Omni receiver" }

    private var statusText: String {
        switch battery.status {
        case .asleep: return "Asleep / not connected"
        case .noResponse: return "No response"
        case .error: return "Error"
        case .ok: return "–"
        }
    }
}
