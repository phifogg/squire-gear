"""Profile read (12 00, both devices): the active profile and the firmware versions. Read-only: Squire never sends
50 02 (switch profile).

Offsets per model in devices.json: `profile` (+ `profile_base`: 0 = the device counts from 0, shown 1-based), an
optional `profiles` count (a value outside 1..profiles reads as unknown), `firmware` = where the device's own version
is per connection, `receiver_firmware` = where the Omni receiver's is (Omni only). A version is 3 bytes
build / minor / major, shown major first like libratbag; all zero = not reported.
Keris II Origin (2026-11-12): [10] profile 0-based, mouse firmware [4..6] over the cable, via the Omni [4..6] = receiver
and the mouse at [14..16] (libratbag's [13..15] does not fit the Keris). Falchion RX LP (G-Helper AsusKeyboard.cs,
2026-11-13 / 2026-11-21): [10] profile 1-based of 5, firmware [11..13] via the Omni, [4..6] over the cable."""
from typing import Optional

from . import Capability


def version(p: bytes, offset: int) -> Optional[str]:
    return f"{p[offset + 2]:02X}.{p[offset + 1]:02X}.{p[offset]:02X}" if any(p[offset:offset + 3]) else None


class Profile(Capability):
    name = "profile"

    def parse(self, s, read: str, p: bytes) -> None:
        if read != self.params["read"]:
            return
        raw = p[self.params["profile"]]
        profile = raw + 1 - self.params["profile_base"]
        count = self.params.get("profiles")
        s.profile = profile if count is None or 1 <= profile <= count else None
        offset = self.params["firmware"].get(s.connection)
        if offset is not None:
            s.firmware = version(p, offset)
        if s.connection == "omni":
            s.receiver_firmware = version(p, self.params["receiver_firmware"])
