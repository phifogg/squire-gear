"""Keyboard profile read (Falchion RX LP): 12 00 [9] current lighting effect, [10] active profile (1-based, profiles 1,
2 and 5 of 5 confirmed; G-Helper's "reported == count -> profile 0" does not apply), firmware: via the Omni [4..6] =
receiver and [11..13] = keyboard, over the cable [4..6] = keyboard (Windows, 2026-11-21). Read-only: Squire never
sends 50 02 (switch profile). Source: G-Helper AsusKeyboard.cs (facts only), captures in protocol.json."""
from . import Capability


class KbProfile(Capability):
    name = "kb_profile"

    def parse(self, s, read: str, p: bytes) -> None:
        if read != self.params["read"]:
            return
        effect, profile = p[self.params["effect"]], p[self.params["profile"]]   # [9], [10]
        s.light_mode = self.owner["kb_lighting"].modes.get(effect, f"0x{effect:02x}")
        s.profile = profile if 1 <= profile <= self.params["profiles"] else None
        if s.connection == "omni" and any(p[4:7]):
            s.receiver_firmware = f"{p[6]:02X}.{p[5]:02X}.{p[4]:02X}"
        fw = p[11:14] if s.connection == "omni" else p[4:7]
        if any(fw):
            s.firmware = f"{fw[2]:02X}.{fw[1]:02X}.{fw[0]:02X}"
