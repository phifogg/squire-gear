import Foundation
import SquireCore
import SquireHID
import UserNotifications

/// Polls the mouse in the background and publishes the result. Read-only: see HIDMouseReader.
@MainActor
final class BatteryStore: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    static let shared = BatteryStore()

    static let pollInterval: TimeInterval = 60
    static let notifyKey = "notifyWhenLow"
    private static let stateKey = "notificationState"

    @Published private(set) var mouse: Battery?
    @Published private(set) var lastUpdate: Date?
    @Published private(set) var isRefreshing = false

    private var timer: Timer?
    private var notificationState: NotificationState = {
        UserDefaults.standard.data(forKey: BatteryStore.stateKey)
            .flatMap { try? JSONDecoder().decode(NotificationState.self, from: $0) } ?? NotificationState()
    }()

    func start() {
        guard timer == nil else { return }
        UserDefaults.standard.register(defaults: [Self.notifyKey: true])
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: Self.pollInterval, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        guard !isRefreshing else { return }
        isRefreshing = true
        // IOKit reads pump a run loop for up to ~1 s: keep them off the main thread.
        DispatchQueue.global(qos: .utility).async {
            let result = HIDMouseReader().read()
            DispatchQueue.main.async { self.apply(result.batteries) }
        }
    }

    private func apply(_ batteries: [Battery]) {
        isRefreshing = false
        lastUpdate = Date()
        mouse = batteries.first { $0.device == .mouse }
        guard UserDefaults.standard.bool(forKey: Self.notifyKey) else { return }
        let outcome = LowBattery.evaluate(batteries, state: notificationState)
        for battery in outcome.toNotify {
            let level = battery.lowWarning ?? SquireProtocol.defaultLowWarning
            let content = UNMutableNotificationContent()
            content.title = "\(battery.name): battery low"
            content.body = "\(battery.percent ?? 0)% left (device warning level \(level)%)"
            content.sound = .default
            UNUserNotificationCenter.current().add(
                UNNotificationRequest(identifier: "low-\(battery.device.rawValue)", content: content, trigger: nil))
        }
        if outcome.state != notificationState {
            notificationState = outcome.state
            if let data = try? JSONEncoder().encode(outcome.state) {
                UserDefaults.standard.set(data, forKey: Self.stateKey)
            }
        }
    }

    // Show banners even though the app is "active" (menu bar apps always are).
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
