"""LED zones (Keris II Origin: logo 0, wheel 1, side 2): 51 28 <zone> 00 <mode> <brightness %> <r g b> 00 00 00, read
with 12 03 <zone>, saved with 50 03.

Sources, facts only: libratbag asus_set_led()/asus_save_profile() (MIT, tested on this mouse in the owner's fork) and
G-Helper GetUpdateLightingModePacket()/GetSaveProfilePacket() (GPL-3.0). Effects the Keris supports (G-Helper
KerisIIOrigin); f0 = off. No rainbow/comet: they need direction/speed bytes Squire does not send (devices.json:
writable false). No speed byte: tested 2026-11-10, the Keris stores [11] = 05/09 but breathing and colour cycle keep
the same tempo (3 pulses in 10 s either way). Bytes [9..11] stay 00."""
from typing import Optional

from . import Capability, Change, byte


class LedZones(Capability):
    name = "led_zones"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.write_prefix, = device.write_prefixes(self.params["write"])   # 51 28
        self.save, = device.write_prefixes(self.params["save"])            # 50 03: stores the settings in flash
        self.zone_index = {z["name"]: byte(z["index"]) for z in self.params["zones"]}
        self.zones = tuple(z["name"] for z in self.params["zones"])
        self.write_modes = {m["name"]: byte(m["value"]) for m in self.params["modes"] if m["writable"]}
        self.modes = {byte(m["value"]): m["name"] for m in self.params["modes"]}

    def command(self, zone: str, mode: str, brightness: int, color: str = "#000000") -> bytes:
        """51 28 command for one LED zone. Raises ValueError for anything outside the allowlist."""
        if zone not in self.zone_index:
            raise ValueError(f"unknown LED zone {zone!r}")
        if mode not in self.write_modes:
            raise ValueError(f"LED effect {mode!r} is not supported")
        if not 0 <= int(brightness) <= 100:
            raise ValueError("brightness must be 0-100 %")
        color_bytes = bytes.fromhex(color.lstrip("#"))
        if len(color_bytes) != 3:
            raise ValueError(f"colour {color!r} is not #rrggbb")
        return self.checked(self.write_prefix + bytes([self.zone_index[zone], 0x00, self.write_modes[mode],
                                                       int(brightness)]) + color_bytes + bytes([0x00, 0x00, 0x00]))

    def allows(self, command: bytes) -> bool:
        if command == self.save:
            return True
        return (len(command) == 12 and command[:2] == self.write_prefix and command[2] in self.zone_index.values()
                and command[3] == 0 and command[4] in self.write_modes.values() and command[5] <= 100
                and command[9:] == b"\0\0\0")

    def parse(self, s, read: str, p: bytes) -> None:
        if read.startswith("led_"):
            s.leds[read[4:]] = {"mode": self.modes.get(p[4], f"0x{p[4]:02x}"), "brightness": p[5],
                                "color": "#" + p[6:9].hex()}
            s.led_speed_raw[read[4:]] = p[12]    # [10..12] direction, random, speed (G-Helper); no effect on the Keris

    @staticmethod
    def matches(wanted: dict, actual: Optional[dict]) -> bool:
        if not actual or actual["mode"] != wanted["mode"]:
            return False
        if wanted["mode"] == "off":
            return True
        if actual["brightness"] != wanted["brightness"]:
            return False
        return wanted["mode"] not in ("static", "breathing", "react") or actual["color"] == wanted["color"]

    def change(self, zone: str, mode: str, brightness: int, color: str = "#000000") -> Change:
        command = self.command(zone, mode, brightness, color)
        wanted = {"mode": mode, "brightness": int(brightness), "color": "#" + command[6:9].hex()}
        return Change(command, (f"led_{zone}",), lambda s: s.leds.get(zone),
                      lambda after, _: self.matches(wanted, after))
