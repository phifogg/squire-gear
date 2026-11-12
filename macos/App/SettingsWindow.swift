import AppKit
import SwiftUI
import SquireCore

/// The settings window, opened from "Settings…" in the popover. Same layout as the Linux window: devices on the
/// left; the selected device drawn in the middle with clickable zones and callouts; the selected zone's settings
/// on the right, with Show bytes / Revert / Apply below.
///
/// An AppKit-managed window (not a SwiftUI `Window` scene): it opens only on request, never at launch.
@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    static let shared = SettingsWindowController()
    private var window: NSWindow?

    func show() {
        if window == nil {
            let host = NSHostingController(rootView: SettingsView(store: SettingsStore.shared))
            let window = NSWindow(contentViewController: host)
            window.title = "Squire"
            window.styleMask = [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]
            window.setContentSize(NSSize(width: 1320, height: 800))
            window.contentMinSize = NSSize(width: 1000, height: 640)
            window.isReleasedWhenClosed = false
            window.delegate = self
            window.center()
            window.setFrameAutosaveName("SquireSettings")
            self.window = window
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        SettingsStore.shared.refresh()
    }

    /// `--screenshot PATH`: save the window as PNG (development aid, like the Linux window's --screenshot).
    func screenshot(to path: String) {
        guard let view = window?.contentView, let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }
}

struct SettingsView: View {
    @ObservedObject var store: SettingsStore

    var body: some View {
        NavigationSplitView {
            List(selection: $store.device) {
                DeviceSidebarRow(device: .mouse, battery: store.batteries[.mouse], loaded: store.settings != nil)
                    .tag(Battery.Device.mouse)
                DeviceSidebarRow(device: .keyboard, battery: store.batteries[.keyboard], loaded: store.settings != nil)
                    .tag(Battery.Device.keyboard)
            }
            .navigationSplitViewColumnWidth(min: 220, ideal: 250, max: 300)
        } detail: {
            VStack(spacing: 0) {
                header
                Divider()
                content
            }
        }
        .overlay(alignment: .bottom) {
            if let toast = store.toast {
                Text(toast)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.regularMaterial, in: Capsule())
                    .shadow(radius: 4)
                    .padding(.bottom, 24)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: store.toast)
        .alert("Not applied", isPresented: Binding(get: { store.failure != nil }, set: { if !$0 { store.failure = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(store.failure ?? "")
        }
    }

    private var header: some View {
        VStack(spacing: 0) {
            HStack {
                Button {
                    store.refresh()
                } label: {
                    Label("Read again", systemImage: "arrow.clockwise")
                }
                .disabled(store.loading || store.applying)
                .help("Read the settings from the mouse again")
                if store.loading && store.settings != nil { ProgressView().controlSize(.small) }
                Spacer()
                if store.demo { Text("Demo: protocol.json captures, nothing is sent").font(.caption).foregroundStyle(.secondary) }
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            Text("The lights can be changed; DPI, sensor and power are shown only so far. Nothing is sent until you click Apply.")
                .font(.callout)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6).padding(.horizontal, 12)
                .background(Color.accentColor.opacity(0.15))
        }
    }

    @ViewBuilder private var content: some View {
        if store.device == .keyboard {
            let battery = store.batteries[.keyboard]?.percent.map { "Battery \($0) %. " } ?? ""
            StatusPage(systemImage: "keyboard", title: "Keyboard settings come later",
                       text: "\(battery)Squire starts with the mouse; the keyboard follows.")
        } else if let settings = store.settings {
            if settings.status == .ok {
                MouseSettingsView(store: store, settings: settings)
            } else {
                let (title, text) = Self.statusTexts[settings.status] ?? ("Could not read the mouse", settings.status.rawValue)
                StatusPage(systemImage: "computermouse", title: title, text: text)
            }
        } else {
            StatusPage(systemImage: nil, title: "Reading the mouse…",
                       text: "Squire reads the current settings. This takes about a second.")
        }
    }

    static let statusTexts: [MouseSettings.Status: (String, String)] = [
        .asleep: ("The mouse is asleep", "Move the mouse, then read again."),
        .noResponse: ("No answer from the mouse", "Move the mouse, then read again."),
        .notFound: ("Mouse not found", "Plug in the Omni receiver or the USB cable."),
        .noAccess: ("No access to the receiver", "macOS refused to open the receiver."),
    ]
}

struct DeviceSidebarRow: View {
    let device: Battery.Device
    let battery: Battery?
    let loaded: Bool

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(device == .mouse ? "ROG Keris II Origin" : "ROG Falchion RX LP")
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }
        } icon: {
            Image(systemName: device == .mouse ? "computermouse" : "keyboard")
        }
        .padding(.vertical, 3)
    }

    private var subtitle: String {
        var text: String
        if let battery {
            if let percent = battery.percent {
                text = (battery.connection == .wired ? "Wired" : "Omni receiver") + " · \(percent) %"
                    + (battery.charging ? " · charging" : "")
            } else {
                text = battery.status == .asleep ? "asleep" : battery.status.rawValue
            }
        } else {
            text = loaded ? "not found" : "…"
        }
        return device == .keyboard ? text + " · settings later" : text
    }
}

struct StatusPage: View {
    let systemImage: String?
    let title: String
    let text: String

    var body: some View {
        VStack(spacing: 12) {
            if let systemImage {
                Image(systemName: systemImage).font(.system(size: 48)).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
            Text(title).font(.title2).bold()
            Text(text).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - mouse page

struct MouseSettingsView: View {
    @ObservedObject var store: SettingsStore
    let settings: MouseSettings

    var body: some View {
        HStack(spacing: 0) {
            // The stage has a fixed layout (same coordinates as on Linux); scale it down to fit narrow windows.
            GeometryReader { geo in
                let scale = min(1, geo.size.width / (StageView.size.width + 16), geo.size.height / (StageView.size.height + 16))
                StageView(store: store, settings: settings, preview: store.preview ?? settings)
                    .scaleEffect(scale)
                    .frame(width: geo.size.width, height: geo.size.height)
            }
            .background(Color.primary.opacity(0.03))
            Divider()
            VStack(spacing: 0) {
                InspectorView(store: store, settings: settings)
                Divider()
                ApplyBar(store: store)
            }
            .frame(width: 360)
        }
    }
}

/// Fixed 660 x 660 layout, same coordinates as the Linux window.
struct StageView: View {
    @ObservedObject var store: SettingsStore
    let settings: MouseSettings
    let preview: MouseSettings

    static let size = CGSize(width: 660, height: 660)
    static let drawing = CGPoint(x: 230, y: 110)
    // Only what is visible from above gets a hotspot (the DPI button is on the underside).
    static let hotspots: [(SettingsZone, CGRect)] = [
        (.wheel, CGRect(x: 314, y: 148, width: 32, height: 58)),
        (.logo, CGRect(x: 296, y: 322, width: 68, height: 68)),
        (.side, CGRect(x: 226, y: 278, width: 32, height: 140)),
    ]
    static let callouts: [(SettingsZone, CGPoint)] = [
        (.wheel, CGPoint(x: 470, y: 152)), (.logo, CGPoint(x: 470, y: 332)), (.side, CGPoint(x: 48, y: 324)),
    ]
    static let lines: [(SettingsZone, CGPoint, CGFloat)] = [
        (.wheel, CGPoint(x: 346, y: 176), 124), (.logo, CGPoint(x: 364, y: 356), 106), (.side, CGPoint(x: 198, y: 348), 28),
    ]

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.clear
            VStack(alignment: .leading, spacing: 4) {
                Text(settings.name).font(.title2).bold()
                if let b = store.batteries[.mouse], let percent = b.percent {
                    Text("Battery \(percent) %" + (b.charging ? " · charging" : "")).foregroundStyle(.secondary)
                }
            }
            .offset(x: 32, y: 24)

            MouseDrawing(settings: preview)
                .offset(x: Self.drawing.x, y: Self.drawing.y)

            ForEach(Self.lines, id: \.0) { zone, start, width in
                let selected = zone == store.selected
                Path { p in p.move(to: start); p.addLine(to: CGPoint(x: start.x + width, y: start.y)) }
                    .stroke(selected ? Color.accentColor : Color.primary.opacity(0.45),
                            style: StrokeStyle(lineWidth: 1, dash: selected ? [] : [3, 3]))
            }

            ForEach(Self.hotspots, id: \.0) { zone, rect in
                Hotspot(selected: zone == store.selected) { store.selected = zone }
                    .frame(width: rect.width, height: rect.height)
                    .help(zone.title)
                    .accessibilityLabel(zone.title)
                    .offset(x: rect.minX, y: rect.minY)
            }

            ForEach(Self.callouts, id: \.0) { zone, origin in
                ZoneCard(zone: zone, value: SettingsEditing.summary(preview, zone), selected: zone == store.selected,
                         width: 150) { store.selected = zone }
                    .offset(x: origin.x, y: origin.y)
            }

            VStack(spacing: 10) {
                Text("Click a highlighted part, or one of these:").foregroundStyle(.secondary)
                HStack(spacing: 10) {
                    ForEach(SettingsZone.chips) { zone in
                        ZoneCard(zone: zone, value: SettingsEditing.summary(preview, zone),
                                 selected: zone == store.selected, width: 190) { store.selected = zone }
                    }
                }
            }
            .frame(width: Self.size.width - 64)
            .offset(x: 32, y: 520)
        }
        .frame(width: Self.size.width, height: Self.size.height, alignment: .topLeading)
    }
}

struct Hotspot: View {
    let selected: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.accentColor.opacity(selected ? 0.16 : hovering ? 0.12 : 0.001))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(selected ? Color.accentColor : Color.primary.opacity(0.45),
                                      style: StrokeStyle(lineWidth: 2, dash: selected ? [] : [5, 4]))
                )
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

struct ZoneCard: View {
    let zone: SettingsZone
    let value: String
    let selected: Bool
    let width: CGFloat
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 2) {
                Text(zone.shortTitle).font(.headline)
                Text(value).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 10).padding(.vertical, 8)
            .frame(width: width, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 8)
                .strokeBorder(selected ? Color.accentColor : Color.primary.opacity(0.12), lineWidth: selected ? 2 : 1))
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }
}

// MARK: - inspector

struct InspectorView: View {
    @ObservedObject var store: SettingsStore
    let settings: MouseSettings

    var body: some View {
        let zone = store.selected
        Form {
            Section {
                if let led = zone.ledZone {
                    if let state = store.led(led) {
                        LEDEditor(store: store, zone: led, state: state)
                    } else {
                        InfoRow("Not read", "")
                    }
                } else if zone == .dpi {
                    dpiRows
                } else if zone == .sensor {
                    sensorRows
                } else if zone == .power {
                    powerRows
                }
            } header: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(zone.title).font(.headline)
                    Text(zone.kind).font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("Device") {
                InfoRow("Connection", settings.connection == .wired ? "Wired" : "Omni receiver")
                InfoRow("Profile", settings.profile.map(String.init) ?? "–", subtitle: "Squire reads the active profile only")
                InfoRow("Firmware", settings.firmware ?? "–")
            }
        }
        .formStyle(.grouped)
    }

    @ViewBuilder private var dpiRows: some View {
        let count = settings.dpiStageCount ?? settings.dpi.count
        ForEach(0..<min(count, settings.dpi.count), id: \.self) { i in
            let xy = i < settings.dpiXY.count ? settings.dpiXY[i] : nil
            let subtitle = [settings.dpiStage == i + 1 ? "active" : nil,
                            xy.flatMap { $0[0] != $0[1] ? "X \($0[0]) / Y \($0[1])" : nil }]
                .compactMap { $0 }.joined(separator: " · ")
            InfoRow("Stage \(i + 1)", settings.dpi[i].map { "\($0) DPI" } ?? "–", subtitle: subtitle,
                    swatch: i < settings.dpiColors.count ? settings.dpiColors[i] : nil)
        }
        InfoRow("Stages on the DPI button", "\(count)")
    }

    @ViewBuilder private var sensorRows: some View {
        InfoRow("Polling rate", settings.pollingHz.map { "\($0) Hz" } ?? "–",
                subtitle: "raw 0x\(ReplyParser.hex([settings.pollingRaw ?? 0])); 8000 Hz mode not confirmed yet")
        InfoRow("Lift-off distance", settings.liftoff.map { $0.prefix(1).uppercased() + $0.dropFirst() } ?? "–")
        InfoRow("Angle snapping", settings.angleSnapping == true ? "On" : "Off")
        InfoRow("Angle tuning", "\(settings.angleTuning ?? 0)°")
        InfoRow("Button response", settings.debounceMs.map { "\($0) ms" } ?? "index \(settings.debounceIndex ?? 0)")
    }

    @ViewBuilder private var powerRows: some View {
        if let b = store.batteries[.mouse], let percent = b.percent {
            InfoRow("Battery", "\(percent) %", subtitle: b.charging ? "charging" : "")
        }
        InfoRow("Turn off after", settings.powerOffMinutes.map { "\($0) min" } ?? "Never")
        InfoRow("Low-battery warning", settings.lowWarning.map { "\($0) %" } ?? "–",
                subtitle: "Squire's notification uses this level")
    }
}

struct LEDEditor: View {
    @ObservedObject var store: SettingsStore
    let zone: LEDZone
    let state: LEDState

    /// The writable effects, plus the current one if the mouse has an effect Squire cannot write (shown, not offered).
    private var modes: [LEDMode] {
        LEDMode.writable.contains(state.mode) ? LEDMode.writable : LEDMode.writable + [state.mode]
    }

    var body: some View {
        Picker("Effect", selection: Binding(get: { state.mode }, set: { mode in store.edit(zone) { $0.mode = mode } })) {
            ForEach(modes, id: \.self) { mode in
                Text(LEDMode.writable.contains(mode) ? mode.title : "\(mode.title) (read only)").tag(mode)
            }
        }
        if state.mode != .off {
            LabeledContent("Brightness") {
                HStack {
                    Slider(value: Binding(get: { Double(state.brightness) },
                                          set: { value in store.edit(zone) { $0.brightness = Int(value.rounded()) } }),
                           in: 0...100)   // no step: on macOS a step draws a tick mark per value
                        .frame(width: 150)
                    Text("\(state.brightness) %").monospacedDigit().frame(width: 44, alignment: .trailing)
                }
            }
        }
        if state.mode.showsColor {
            LabeledContent("Color") {
                LEDColorWell(color: state.color) { rgb in store.edit(zone) { $0.color = rgb } }
                    .frame(width: 44, height: 24)
            }
        }
    }
}

struct InfoRow: View {
    let title: String
    let value: String
    var subtitle = ""
    var swatch: RGB?

    init(_ title: String, _ value: String, subtitle: String = "", swatch: RGB? = nil) {
        self.title = title
        self.value = value
        self.subtitle = subtitle
        self.swatch = swatch
    }

    var body: some View {
        LabeledContent {
            HStack(spacing: 8) {
                if let swatch { Swatch(color: swatch) }
                Text(value).textSelection(.enabled)
            }
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                if !subtitle.isEmpty { Text(subtitle).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            }
        }
    }
}

/// Colour dot; black (= no light) as a dashed ring.
struct Swatch: View {
    let color: RGB

    var body: some View {
        Group {
            if color.isBlack {
                Circle().strokeBorder(Color.secondary, style: StrokeStyle(lineWidth: 1.5, dash: [3, 2]))
            } else {
                Circle().fill(Color(color)).overlay(Circle().strokeBorder(Color.black.opacity(0.2)))
            }
        }
        .frame(width: 18, height: 18)
    }
}

// MARK: - apply bar

struct ApplyBar: View {
    @ObservedObject var store: SettingsStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(store.applyStatus).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Toggle("Show bytes", isOn: $store.showBytes).toggleStyle(.button)
                Spacer()
                Button("Revert") { store.revert() }
                    .disabled(!store.isDirty || store.applying)
                Button("Apply") { store.apply() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!store.isDirty || store.applying)
            }
            if store.showBytes {
                let lines = store.plannedPackets
                Text(lines.isEmpty ? "Nothing to send." : lines.joined(separator: "\n"))
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(12)
    }
}

// MARK: - colour well

/// Colour well for an LED colour. Replaces SwiftUI's ColorPicker, which wrote every edit back into the colour
/// panel as 8-bit sRGB: the panel's cursor then jumped to the clamped colour, so parts of the wheel outside sRGB
/// seemed unreachable. Here the well only gets a new colour when the stored one really differs (Revert, another
/// zone), and the panel opens next to the settings window instead of where it was last left.
struct LEDColorWell: NSViewRepresentable {
    let color: RGB
    let onChange: (RGB) -> Void

    func makeNSView(context: Context) -> PlacedColorWell {
        let well = PlacedColorWell()
        well.color = color.nsColor
        well.target = context.coordinator
        well.action = #selector(Coordinator.changed(_:))
        return well
    }

    func updateNSView(_ well: PlacedColorWell, context: Context) {
        context.coordinator.onChange = onChange
        if RGB(well.color) != color { well.color = color.nsColor }
    }

    func makeCoordinator() -> Coordinator { Coordinator(onChange: onChange) }

    final class Coordinator: NSObject {
        var onChange: (RGB) -> Void
        init(onChange: @escaping (RGB) -> Void) { self.onChange = onChange }
        @objc func changed(_ sender: NSColorWell) { onChange(RGB(sender.color)) }
    }
}

final class PlacedColorWell: NSColorWell {
    override func activate(_ exclusive: Bool) {
        let panel = NSColorPanel.shared
        panel.showsAlpha = false
        super.activate(exclusive)
        guard let window, let screen = window.screen ?? NSScreen.main else { return }
        // right of the settings window if there is room, else over its right edge; top-aligned, on screen
        let visible = screen.visibleFrame
        var frame = panel.frame
        frame.origin.x = window.frame.maxX + 8
        if frame.maxX > visible.maxX { frame.origin.x = window.frame.maxX - frame.width - 16 }
        frame.origin.y = window.frame.maxY - frame.height - 40
        frame.origin.x = min(max(frame.origin.x, visible.minX), visible.maxX - frame.width)
        frame.origin.y = min(max(frame.origin.y, visible.minY), visible.maxY - frame.height)
        panel.setFrameOrigin(frame.origin)
    }
}
