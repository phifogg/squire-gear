"""Mouse power: auto power-off and low-battery warning, always both: 51 37 00 00 <power-off code> 00 <warning %>, read
back from the battery reply 12 07 [5] / [6].

Source, facts only: G-Helper AsusMouse.cs GetUpdateEnergySettingsPacket() and Models/KerisIIOrigin.cs
(HasAutoPowerOff, HasLowBatteryWarning) (GPL-3.0). Bytes approved by the owner 2026-11-12."""
from typing import Optional

from . import Capability, Change, byte


class Power(Capability):
    name = "power"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.write_prefix, = device.write_prefixes(self.params["write"])   # 51 37
        self.read_name = self.params["read"]                               # battery
        # minutes (None = never) -> byte, and back
        self.power_off_codes = {p["minutes"]: byte(p["value"]) for p in self.params["power_off"]}
        self.power_off_minutes = {code: minutes for minutes, code in self.power_off_codes.items()}
        self.warning_levels = tuple(self.params["warning_levels"])          # % (G-Helper: step 10, max 50)

    def command(self, power_off_minutes: Optional[int], low_warning: int) -> bytes:
        """51 37: auto power-off and low-battery warning, always both. Raises ValueError outside the allowlist."""
        if power_off_minutes not in self.power_off_codes:
            raise ValueError(f"power-off {power_off_minutes!r} min is not one of 1, 2, 3, 5, 10 or never")
        if low_warning not in self.warning_levels:
            raise ValueError(f"low-battery warning {low_warning!r} % is not one of {self.warning_levels}")
        return self.checked(self.write_prefix + bytes([0x00, 0x00, self.power_off_codes[power_off_minutes], 0x00,
                                                       low_warning]))

    def allows(self, command: bytes) -> bool:
        return (len(command) == 7 and command[2:4] == b"\0\0" and command[4] in self.power_off_codes.values()
                and command[5] == 0 and command[6] in self.warning_levels)

    def parse(self, s, read: str, p: bytes) -> None:
        if read == self.read_name:
            warning = self.owner["battery"].params["warning"]                 # [6]
            s.power_off_minutes = self.power_off_minutes.get(p[self.params["offset"]], None)   # [5]
            s.low_warning = p[warning] if 1 <= p[warning] <= 100 else None

    def change(self, power_off_minutes: Optional[int], low_warning: int) -> Change:
        command = self.command(power_off_minutes, low_warning)
        wanted = {"power_off_minutes": power_off_minutes, "low_warning": low_warning}
        return Change(command, (self.read_name,),
                      lambda s: {"power_off_minutes": s.power_off_minutes, "low_warning": s.low_warning},
                      lambda after, _: after == wanted)
