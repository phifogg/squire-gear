import Foundation
import SquireCore
import SquireHID
import UserNotifications

/// Polls keyboard and mouse in the background and publishes the result. Read-only: see HIDMouseReader.
@MainActor
final class BatteryStore: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    static let shared = BatteryStore()

    static let pollInterval: TimeInterval = 60
    static let notifyKey = "notifyWhenLow"
    private static let stateKey = "notificationState"

    @Published private(set) var keyboard: Battery?
    @Published private(set) var mouse: Battery?
    @Published private(set) var lastUpdate: Date?
    @Published private(set) var isRefreshing = false
    /// Notification permission as macOS reports it (shown in the hidden debug section).
    @Published private(set) var notificationStatus = "unknown"

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
            let result = HIDReader().read()
            DispatchQueue.main.async { self.apply(result.batteries) }
        }
    }

    private func apply(_ batteries: [Battery]) {
        isRefreshing = false
        lastUpdate = Date()
        keyboard = batteries.first { $0.device == .keyboard }
        mouse = batteries.first { $0.device == .mouse }
        guard UserDefaults.standard.bool(forKey: Self.notifyKey) else { return }
        let outcome = LowBattery.evaluate(batteries, state: notificationState)
        for battery in outcome.toNotify {
            let level = battery.lowWarning ?? SquireProtocol.defaultLowWarning
            post(title: "\(battery.name): battery low",
                 body: "\(battery.percent ?? 0)% left (device warning level \(level)%)",
                 identifier: "low-\(battery.device.rawValue)")
        }
        saveState(outcome.state)
    }

    private func post(title: String, body: String, identifier: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
    }

    private func saveState(_ state: NotificationState) {
        guard state != notificationState else { return }
        notificationState = state
        if let data = try? JSONEncoder().encode(state) {
            UserDefaults.standard.set(data, forKey: Self.stateKey)
        }
    }

    // MARK: hidden debug helpers (hold Option while opening the popover)

    /// Same banner path as a real low-battery alert, without a real low battery and without touching the state.
    func sendTestNotification() {
        post(title: "Squire test: battery low", body: "This is a test notification. No real battery is low.",
             identifier: "test-\(UUID().uuidString)")
        refreshNotificationStatus()
    }

    /// Re-arms the real alert: forget which devices were already notified.
    func resetNotificationState() {
        saveState(NotificationState())
    }

    func refreshNotificationStatus() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let text: String
            switch settings.authorizationStatus {
            case .authorized, .provisional: text = "allowed (\(settings.alertStyle == .none ? "alert style: none" : "banners/alerts"))"
            case .denied: text = "denied: enable Squire in System Settings → Notifications"
            case .notDetermined: text = "not asked yet"
            @unknown default: text = "unknown"
            }
            DispatchQueue.main.async { self.notificationStatus = text }
        }
    }

    // Show banners even though the app is "active" (menu bar apps always are).
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
