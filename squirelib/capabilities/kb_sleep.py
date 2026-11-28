"""Keyboard sleep timeout: 51 38 00 00 <code>, read back from 12 01 [6]. The code is the mouse's power-off coding
(00..04 = 1/2/3/5/10 min, ff never), kept per device in devices.json; Armoury Crate offers exactly these (2026-11-21).
Approved and confirmed on the device via Omni 2026-11-22."""
from typing import Optional

from . import Capability, Change, byte


class KbSleep(Capability):
    name = "kb_sleep"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.write_prefix, = device.write_prefixes(self.params["write"])   # 51 38
        self.read_name = self.params["read"]                               # battery (12 01)
        self.codes = {x["minutes"]: byte(x["value"]) for x in self.params["power_off"]}   # minutes (None = never)
        self.minutes = {code: minutes for minutes, code in self.codes.items()}

    def command(self, minutes: Optional[int]) -> bytes:
        """51 38: sleep after 1/2/3/5/10 min, None = never."""
        if minutes not in self.codes:
            raise ValueError(f"sleep {minutes!r} min is not one of 1, 2, 3, 5, 10 or never")
        return self.checked(self.write_prefix + bytes([0x00, 0x00, self.codes[minutes]]))

    def allows(self, command: bytes) -> bool:
        return len(command) == 5 and command[2:4] == b"\0\0" and command[4] in self.codes.values()

    def parse(self, s, read: str, p: bytes) -> None:
        if read == self.read_name:
            s.sleep_minutes = self.minutes.get(p[self.params["offset"]])   # [6]

    def change(self, minutes: Optional[int]) -> Change:
        return Change(self.command(minutes), (self.read_name,), lambda s: {"sleep_minutes": s.sleep_minutes},
                      lambda after, _: after == {"sleep_minutes": minutes})
