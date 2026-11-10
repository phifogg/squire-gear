/// Which devices have already been notified. Persisted by the app between runs.
public struct NotificationState: Equatable, Codable, Sendable {
    public var notified: Set<String>
    public init(notified: Set<String> = []) { self.notified = notified }
}

public enum LowBattery {
    /// Notify once per device when its battery falls to the device's own warning level.
    /// The flag resets when the device charges or recovers above the level. Asleep devices keep their state.
    public static func evaluate(_ batteries: [Battery], state: NotificationState)
        -> (toNotify: [Battery], state: NotificationState) {
        var state = state
        var toNotify: [Battery] = []
        for battery in batteries {
            guard let percent = battery.percent else { continue }
            let level = battery.lowWarning ?? SquireProtocol.defaultLowWarning
            let key = battery.device.rawValue
            if battery.charging || percent > level {
                state.notified.remove(key)
            } else if !state.notified.contains(key) {
                toNotify.append(battery)
                state.notified.insert(key)
            }
        }
        return (toNotify, state)
    }
}
