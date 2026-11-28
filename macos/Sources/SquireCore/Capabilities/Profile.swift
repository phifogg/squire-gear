// Profile read 12 00 (squirelib/capabilities/profile.py): active profile, the device's firmware per connection, the
// Omni receiver's firmware. Read only: 50 02 (switch profile) is never sent.
// Keris II Origin (2026-11-12): [10] profile 0-based, mouse firmware [4..6] over the cable, via the Omni [4..6] = the
// receiver and the mouse at [14..16] (libratbag's [13..15] does not fit the Keris).

extension Capabilities {
    public struct Profile: MouseCapability {
        public let prefixes: [[UInt8]] = []
        let read: String
        let profileOffset: Int, profileBase: Int, profiles: Int?
        let firmwareOmni: Int?, firmwareWired: Int?, receiverFirmware: Int

        init(_ params: DeviceModels.Profile) {
            read = params.read
            profileOffset = params.profile
            profileBase = params.profileBase
            profiles = params.profiles
            firmwareOmni = params.firmware.omni
            firmwareWired = params.firmware.wired
            receiverFirmware = params.receiverFirmware
        }

        public func allows(_ c: [UInt8]) -> Bool { false }

        /// Active profile, shown 1-based; outside 1...profiles (when the model has a count) = unknown.
        func profile(_ p: [UInt8]) -> Int? {
            let number = Int(p[profileOffset]) + 1 - profileBase
            guard let profiles else { return number }
            return (1...profiles).contains(number) ? number : nil
        }

        /// (device firmware, receiver firmware); no connection reads like the Omni.
        func firmware(_ p: [UInt8], _ connection: Battery.Connection?) -> (String?, String?) {
            if connection == .wired { return (firmwareWired.flatMap { firmwareVersion(p, $0) }, nil) }
            return (firmwareOmni.flatMap { firmwareVersion(p, $0) }, firmwareVersion(p, receiverFirmware))
        }

        public func parse(_ read: String, _ p: [UInt8], into s: inout MouseSettings) {
            guard read == self.read else { return }
            s.profile = profile(p)
            let (firmware, receiver) = self.firmware(p, s.connection)
            s.firmware = firmware
            if s.connection != .wired { s.receiverFirmware = receiver }
        }
    }
}
