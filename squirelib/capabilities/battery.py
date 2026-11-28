"""Battery: level, charging flag and the device's low-battery warning level from one get request (keyboard 12 01,
mouse 12 07). Reply layout per device in devices.json: percent offsets in order of preference (the Falchion RX LP
also carries it at [5], confirmed 2026-11-10), charging flag, warning level and its lowest plausible value
(keyboard 0 = never warn, AC 2026-11-22). Read-only: no writes."""
from typing import Optional

from . import Capability


class Battery(Capability):
    name = "battery"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.read_name = self.params["read"]
        self.query = device.read(self.read_name)     # also the "is it awake" check before every session

    def reading(self, reply: bytes, base: int) -> tuple:
        """(percent or None, charging) from a reply whose echo starts at `base`."""
        p = reply[base:]
        pct = next((p[i] for i in self.params["percent"] if len(p) > i and p[i] <= 100), None)
        charging = len(p) > self.params["charging"] and p[self.params["charging"]] != 0
        return pct, charging

    def warning(self, reply: bytes, base: int) -> Optional[int]:
        """Low-battery warning level (%) configured on the device, or None if absent/implausible."""
        p = reply[base:]
        index = self.params["warning"]
        value = p[index] if len(p) > index else None
        return value if value is not None and self.params["warning_min"] <= value <= 100 else None
