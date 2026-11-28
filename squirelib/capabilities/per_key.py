"""Per-key colours ("direct mode", Falchion RX LP): c0 81 <n> 00 + n x <key id> <r g b>, answered with c0 82 <n> 00
(not an echo); each packet must be acknowledged before the next. Nothing can be read back and it is never saved: 50 55
after it stores nothing (tested 2026-11-25, protocol.json keyboard_writes.direct).

Sources, facts only: G-Helper (GPL-3.0) AsusKeyboard.cs SetLedColors + AuraKeyboardLayouts.cs "FalchionRX" /
"FalchionRXISO" (its FalchionRXLowProfileOmni = this keyboard via the Omni, report ID 02); OpenRGB (GPL-2.0) has the same
packet for the original Falchion. At most 14 keys per packet: 15 would be 65 bytes with the report ID, one over the
Omni's 64. Key id = column * 8 + row, columns 0..14, rows 1..5 on the RX / RX LP (Esc 01, Tab 02, Caps 03, LShift 04,
LCtrl 05, 1 = 09, Q = 0a); the original Falchion starts at row 0 (OpenRGB), which is why 00 lit nothing."""
from typing import Optional

from .. import registry
from . import Capability, Change, byte, rgb


class PerKey(Capability):
    name = "per_key"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        self.write_prefix, = device.write_prefixes(self.params["write"])   # c0 81
        self.per_packet = self.params["per_packet"]
        command = next(c for c in device.data["commands"] if c["name"] == self.params["write"])
        self.ack = registry.hex_bytes(command["reply"])                    # c0 82
        rows = self.params["rows"]
        self.keys = tuple(col * 8 + row for col in range(self.params["columns"])
                          for row in range(rows["min"], rows["max"] + 1))   # 15 columns x rows 1..5 (01..75)
        # Key id of each key, row by row, left to right, as the settings windows draw them. ISO Enter spans two rows
        # and has one id (6b), listed in both. Right column: Ins 71, Del 72, PgUp 73, PgDn 74.
        self.layouts = {name: [[byte(k) for k in row] for row in layout_rows]
                        for name, layout_rows in self.params["layouts"].items()}

    def key_ids(self, iso: Optional[bool]) -> list:
        """Every key id of the layout once, in drawing order (ISO unless the keyboard reported ANSI)."""
        ids = []
        for row in self.layouts["ansi" if iso is False else "iso"]:
            ids += [key for key in row if key not in ids]
        return ids

    def keys_value(self, spec: str) -> dict:
        """Parse "all=ff0000" or "00=ff0000,08=00ff00" (hex key ids) into {key id: (r, g, b)}."""
        keys = {}
        for part in (p.strip() for p in spec.split(",") if p.strip()):
            name, sep, colour = part.partition("=")
            if not sep:
                raise ValueError(f"expected KEY=RRGGBB, got {part!r}")
            colour_bytes = tuple(bytes.fromhex(rgb(colour)[1:]))
            ids = self.keys if name.strip().lower() == "all" else (int(name, 16),)
            for key in ids:
                if key not in self.keys:
                    raise ValueError(f"key id {key:02x} is outside the 15 x 5 matrix (01..75)")
                keys[key] = colour_bytes
        if not keys:
            raise ValueError("no keys given")
        return keys

    def packets(self, keys: dict) -> list:
        """c0 81 <n> 00 + n x <id> <r g b>, at most 14 keys per packet, in key id order."""
        items = sorted(keys.items())
        commands = []
        for start in range(0, len(items), self.per_packet):
            chunk = items[start:start + self.per_packet]
            body = b"".join(bytes([key, *colour]) for key, colour in chunk)
            commands.append(self.write_prefix + bytes([len(chunk), 0]) + body)
        return commands

    def allows(self, command: bytes) -> bool:
        if len(command) < 4:
            return False
        n = command[2]
        return (1 <= n <= self.per_packet and command[3] == 0 and len(command) == 4 + 4 * n
                and all(command[4 + 4 * i] in self.keys for i in range(n)))

    def reply_expected(self, command: bytes) -> Optional[bytes]:
        if command[:2] == self.write_prefix and len(command) >= 4:
            return self.ack + command[2:4]
        return None

    def change(self, colours: dict) -> Change:
        """Nothing to read back except the effect in 12 00 [9]; the profile must stay the same."""
        return Change(self.packets(colours), ("profile",), lambda s: {"light_mode": s.light_mode, "profile": s.profile},
                      lambda after, before: bool(after and before) and after["profile"] == before["profile"])
