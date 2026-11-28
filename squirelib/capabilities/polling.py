"""Mouse polling rate: 51 31 04 00 <index> (125/250/500/1000 Hz), read back from 12 04 00 [12], low 3 bits.

Source: Armoury Crate capture 2026-11-12 (mouse_writes.armoury_crate), approved 2026-11-13. Armoury Crate shows
1000 Hz for raw 0x63, so the high nibble is not a rate (G-Helper reads it in "booster" mode)."""
from . import Capability, Change, byte

READ = "settings"   # 12 04 00


class Polling(Capability):
    name = "polling"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        prefix, = device.write_prefixes(self.params["write"])
        self.write_prefix, self.field = prefix[:2], prefix[2]                     # 51 31, 04
        self.write_hz = {r["hz"]: byte(r["value"]) for r in self.params["rates"]}  # what Armoury Crate offers
        self.read_rates_hz = tuple(self.params["read_rates_hz"])                  # index in the low 3 bits

    def command(self, hz: int) -> bytes:
        if hz not in self.write_hz:
            raise ValueError(f"polling rate {hz!r} Hz is not one of {sorted(self.write_hz)}")
        return self.checked(self.write_prefix + bytes([self.field, 0x00, self.write_hz[hz]]))

    def allows(self, command: bytes) -> bool:
        return len(command) == 5 and command[3] == 0 and command[4] in self.write_hz.values()

    def parse(self, s, read: str, p: bytes) -> None:
        if read == READ:
            s.polling_raw = p[12]
            s.polling_hz = self.read_rates_hz[p[12] & 0x07]

    def change(self, hz: int) -> Change:
        return Change(self.command(hz), (READ,), lambda s: {"polling_hz": s.polling_hz},
                      lambda after, _: after == {"polling_hz": hz})
