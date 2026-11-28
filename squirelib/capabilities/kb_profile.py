"""Keyboard profile read (Falchion RX LP): the profile read (profile.py: active profile, firmware) plus 12 00 [9], the
lighting effect the keyboard runs now (G-Helper's KeyboardLightingMode ids, kb_lighting; f1 = host-driven). G-Helper's
"reported == count -> profile 0" does not apply: profiles 1, 2 and 5 of 5 confirmed. Captures in protocol.json."""
from .profile import Profile


class KbProfile(Profile):
    name = "kb_profile"

    def parse(self, s, read: str, p: bytes) -> None:
        if read != self.params["read"]:
            return
        effect = p[self.params["effect"]]   # [9]
        s.light_mode = self.owner["kb_lighting"].modes.get(effect, f"0x{effect:02x}")
        super().parse(s, read, p)
