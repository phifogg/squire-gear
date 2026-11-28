"""Keyboard low-battery warning: 51 3a 00 00 <%>, read back from 12 01 [9]. Armoury Crate offers exactly 10..50 % and
"never" (00, capture_4 block 1); 0 = never. G-Helper's 51 37 is wrong for the Falchion. Approved and confirmed on the
device via Omni 2026-11-22."""
from . import Capability, Change


class KbWarning(Capability):
    name = "kb_warning"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.write_prefix, = device.write_prefixes(self.params["write"])   # 51 3a
        self.read_name = self.params["read"]                               # battery (12 01)
        self.levels = tuple(self.params["levels"])

    def command(self, percent: int) -> bytes:
        """51 3a: low-battery warning at 10..50 %, 0 = never."""
        if percent not in self.levels:
            raise ValueError(f"low-battery warning {percent!r} % is not one of "
                             f"{tuple(sorted(x for x in self.levels if x))} or never")
        return self.checked(self.write_prefix + bytes([0x00, 0x00, percent]))

    def allows(self, command: bytes) -> bool:
        return len(command) == 5 and command[2:4] == b"\0\0" and command[4] in self.levels

    def parse(self, s, read: str, p: bytes) -> None:
        if read == self.read_name:
            warning = p[self.params["offset"]]                  # [9]
            s.low_warning = warning if warning <= 100 else None   # 0 = never (AC, 2026-11-22)

    def change(self, percent: int) -> Change:
        return Change(self.command(percent), (self.read_name,), lambda s: {"low_warning": s.low_warning},
                      lambda after, _: after == {"low_warning": percent})
