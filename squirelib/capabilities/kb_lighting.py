"""Keyboard lighting (Falchion RX LP): 51 2c <effect> ..., saved with 50 55 (the mouse uses 50 03). Only the effect can
be read back (12 00 [9], parsed by kb_profile); colour and brightness are not readable on this keyboard.

Source: Armoury Crate captures on Windows (USBPcap via the Omni, 2026-11-21 and 2026-11-22, protocol.json
keyboard_writes, capture_4 has every option); G-Helper AsusKeyboard.cs (GPL-3.0, facts only) has the same 51 2c layout
and 50 55. Only byte values AC sent. Effect ids are G-Helper's KeyboardLightingMode; 0xf1 = the host drives the LEDs.

Layouts:
- single colour (static, breathing, colour cycle, reactive, starry night, current, raindrop): one
  51 2c <effect> 00 <speed> <brightness %> 00 ff ff <r g b>; starry night, current and raindrop also have [6] colour
  mode, [12..14] second colour (00 00 00 unless double) and [15..17] background (00 00 00 = off). Random keeps both
  colours in the packet.
- gradients (rainbow, ripple): three chunks, [3] = 02, 01, 00. First: <speed> <brightness> 00 <direction> <thickness>
  <stop count>, then 4 bytes per stop (position %, R, G, B): 2 stops in the first chunk, up to 4 in the next one, the rest
  in the last (AC sends every chunk, unused ones as zeros).
- quicksand: two chunks ([3] = 01, 00): <speed> <brightness> <mode> <direction> ff, then 6 RGB triples without
  positions, 3 per chunk. Mode 00 = the colour scale, 01 = random (AC still sends the scale). Only AC's default scale.
"off" is not an effect of its own: AC's lighting switch sends static, 4 %, black (capture_4 block 6), and the keyboard
then reads 00 static. (AC also sends an unknown 51 36 to the mouse; Squire does not.)"""
from typing import Optional

from . import Capability, Change, byte, rgb

OFF = "off"
SINGLE_COLOUR = ("static", "breathing", "color-cycle", "reactive", "starry-night", "current", "raindrop")
OPTION_EFFECTS = ("starry-night", "current", "raindrop")
GRADIENT_EFFECTS = ("rainbow", "ripple")
SECOND_COLOUR = "#ff00ff"               # AC's second colour when switching to double
DIRECTION_ALIASES = {"left-to-right": "right", "right-to-left": "left"}    # names used before 2026-11-22
COLOURLESS = frozenset(["color-cycle", "rainbow", "ripple", "quicksand", OFF])  # no colour to pick
# The fields of a lighting value (KbLighting.value); a field the effect does not use is None.
FIELDS = ("effect", "brightness", "color", "speed", "gradient", "direction", "thickness", "mode", "color2",
          "background")


def _pick(name: str, value: Optional[str], choices: dict) -> str:
    value = value or next(iter(choices))
    if value not in choices:
        raise ValueError(f"{name} {value!r}: only {', '.join(choices)}")
    return value


def _stops_ok(stops: bytes) -> bool:
    return len(stops) % 4 == 0 and all(1 <= stops[i] <= 100 for i in range(0, len(stops), 4))


class KbLighting(Capability):
    name = "kb_lighting"

    def __init__(self, device, owner):
        super().__init__(device, owner)
        p = self.params
        self.write_prefix, = device.write_prefixes(p["write"])   # 51 2c
        self.save, = device.write_prefixes(p["save"])            # 50 55
        self.modes = {byte(e["id"]): e["name"] for e in p["effects"]}   # 12 00 [9] id -> name
        # Effect -> {speed name: byte}: AC's fast / normal / slow, own values per effect, larger = slower. Static: ff.
        self.speeds = {e["name"]: {x["name"]: byte(x["value"]) for x in e["speeds"]}
                       for e in p["effects"] if e.get("writable", True)}
        self.ids = {name: i for i, name in self.modes.items() if name in self.speeds}
        self.off_brightness = p["off_brightness"]
        self.effects = list(self.speeds) + [OFF]   # what Squire can set, in the keyboard's id order
        self.colour_modes = {x["name"]: byte(x["value"]) for x in p["colour_modes"]}
        # AC's 7 gradient presets in AC's order (capture_4 block 4). AC shows them without names; the names are Squire's.
        self.gradients = {g["name"]: tuple((pos, colour) for pos, colour in g["stops"]) for g in p["gradients"]}
        self.gradient_stops = (p["gradient_stops"]["min"], p["gradient_stops"]["max"])   # AC: 2..7, positions 1..100 %
        # Direction byte per effect. Rainbow: a compass in 45 degree steps (00 right, 02 down, 04 left, 06 up, 08 from
        # the centre outwards; the diagonals confirmed 2026-11-22). Ripple: none (ff). Quicksand: down / up. First = default.
        self.directions = {d["effect"]: {x["name"]: byte(x["value"]) for x in d["values"]} for d in p["directions"]}
        self.thickness = {x["name"]: byte(x["value"]) for x in p["thickness"]}   # Low / Normal / Strong (AC default)
        self.quicksand_scale = tuple(p["quicksand_scale"])
        self.quicksand_modes = {x["name"]: byte(x["value"]) for x in p["quicksand_modes"]}

    # --- values ---------------------------------------------------------------------------------------------------

    @staticmethod
    def effect_read(effect: str) -> str:
        """The effect 12 00 [9] reports after `effect` was written ("off" is a black static)."""
        return "static" if effect == OFF else effect

    def speed(self, effect: str, speed: Optional[str]) -> str:
        """The speed name for an effect: `speed`, or "normal" if it has one, else its only value."""
        speeds = self.speeds.get(self.effect_read(effect))
        if speeds is None:
            raise ValueError(f"keyboard effect {effect!r} is not one Squire writes ({', '.join(self.effects)})")
        if speed is None:
            speed = "normal" if "normal" in speeds else next(iter(speeds))
        if speed not in speeds:
            raise ValueError(f"speed {speed!r} for {effect}: only {', '.join(speeds)}")
        return speed

    def direction(self, effect: str, direction: Optional[str]) -> str:
        directions = self.directions[effect]
        direction = DIRECTION_ALIASES.get(direction, direction) or next(iter(directions))
        if direction not in directions:
            raise ValueError(f"direction {direction!r} for {effect}: only {', '.join(directions)}")
        return direction

    def gradient_stops_of(self, gradient: Optional[str]) -> tuple:
        """((position %, "rrggbb"), ...) for a preset name or a custom gradient "POS:RRGGBB,POS:RRGGBB,..." (2..7
        stops, positions 1..100 % ascending, like AC's editor)."""
        gradient = gradient or "spectrum"
        if gradient in self.gradients:
            return self.gradients[gradient]
        if ":" not in gradient:
            raise ValueError(f"gradient {gradient!r}: one of {', '.join(self.gradients)} or POS:RRGGBB,... ")
        try:
            stops = tuple((int(pos), rgb(colour, "gradient colour")[1:])
                          for pos, colour in (part.split(":") for part in gradient.split(",")))
        except ValueError as error:
            raise ValueError(f"gradient {gradient!r}: {error}") from None
        low, high = self.gradient_stops
        positions = [pos for pos, _ in stops]
        if not low <= len(stops) <= high:
            raise ValueError(f"gradient {gradient!r}: {low} to {high} stops")
        if not all(1 <= p <= 100 for p in positions) or positions != sorted(set(positions)):
            raise ValueError(f"gradient {gradient!r}: positions 1..100 %, ascending")
        return stops

    def gradient_name(self, gradient: Optional[str]) -> str:
        """A preset name, or the custom gradient written canonically (lower case, no spaces)."""
        gradient = gradient or "spectrum"
        if gradient in self.gradients:
            return gradient
        return ",".join(f"{pos}:{colour}" for pos, colour in self.gradient_stops_of(gradient))

    def value(self, effect: str, brightness: int, color: Optional[str] = "#ffffff", speed: Optional[str] = None,
              gradient: Optional[str] = None, direction: Optional[str] = None, *, thickness: Optional[str] = None,
              mode: Optional[str] = None, color2: Optional[str] = None, background: Optional[str] = None) -> dict:
        """The lighting as Squire writes it, validated and normalised (keys FIELDS): colours #rrggbb lower case
        (#000000 for an effect without one), the speed actually sent, and None for every field the effect does not
        use. `mode`: single / double / random (starry night, current, raindrop) or scale / random (quicksand).
        `background` None or #000000 = off. "off" is AC's black static at 4 %. Options the effect does not use are
        ignored (the window keeps them for a later effect)."""
        if effect == OFF:
            brightness, color = self.off_brightness, "#000000"
        speed = self.speed(effect, speed)          # also refuses an effect Squire does not write
        if not 0 <= int(brightness) <= 100:
            raise ValueError("brightness must be 0-100 %")
        value = dict.fromkeys(FIELDS)
        value.update(effect=effect, brightness=int(brightness), speed=speed,
                     color="#000000" if effect in COLOURLESS else rgb(color or "#ffffff"))
        if effect in GRADIENT_EFFECTS:
            value.update(gradient=self.gradient_name(gradient), direction=self.direction(effect, direction),
                         thickness=_pick("thickness", thickness, self.thickness))
        elif effect == "quicksand":
            value.update(direction=self.direction(effect, direction),
                         mode=_pick("colour mode", mode, self.quicksand_modes))
        elif effect in OPTION_EFFECTS:
            value["mode"] = _pick("colour mode", mode, self.colour_modes)
            if value["mode"] != "single":
                value["color2"] = rgb(color2 or SECOND_COLOUR, "second colour")
            bg = rgb(background, "background") if background else None
            value["background"] = None if bg == "#000000" else bg
        return value

    def value_from(self, stored: dict) -> dict:
        """value() from a dict with (some of) the FIELDS, e.g. a remembered or pending value."""
        options = {f: stored.get(f) for f in FIELDS[2:]}
        return self.value(stored["effect"], stored["brightness"], **options)

    # --- packets --------------------------------------------------------------------------------------------------

    def packets(self, value: dict) -> list:
        """Every 51 2c chunk for a normalised lighting value, in the order they are sent (one for a single-colour
        effect, three for a gradient, two for quicksand)."""
        effect = self.effect_read(value["effect"])
        eid = self.ids[effect]
        speed = self.speeds[effect][value["speed"]]
        brightness = value["brightness"]
        colour = lambda c: bytes.fromhex((c or "#000000").lstrip("#"))     # noqa: E731
        prefix = self.write_prefix
        if effect in SINGLE_COLOUR:
            command = prefix + bytes([eid, 0x00, speed, brightness, 0x00, 0xFF, 0xFF]) + colour(value["color"])
            if effect in OPTION_EFFECTS:
                command = (command[:6] + bytes([self.colour_modes[value["mode"]]]) + command[7:]
                           + colour(value["color2"]) + colour(value["background"]))
            commands = [command]
        elif effect == "quicksand":
            scale = [bytes.fromhex(c) for c in self.quicksand_scale]
            commands = [prefix + bytes([eid, 0x01, speed, brightness, self.quicksand_modes[value["mode"]],
                                        self.directions[effect][value["direction"]], 0xFF]) + b"".join(scale[:3]),
                        prefix + bytes([eid, 0x00]) + b"".join(scale[3:])]
        else:
            stops = self.gradient_stops_of(value["gradient"])
            packed = [bytes([pos]) + bytes.fromhex(c) for pos, c in stops]
            commands = [prefix + bytes([eid, 0x02, speed, brightness, 0x00, self.directions[effect][value["direction"]],
                                        self.thickness[value["thickness"]], len(stops)]) + b"".join(packed[:2]),
                        prefix + bytes([eid, 0x01]) + b"".join(packed[2:6]),
                        prefix + bytes([eid, 0x00]) + b"".join(packed[6:10])]
        for command in commands:
            self.checked(command)
        return commands

    def allows(self, command: bytes) -> bool:
        if command == self.save:
            return True
        if command[:2] != self.write_prefix or len(command) < 4:
            return False
        effect = self.modes.get(command[2])
        if effect not in self.speeds:
            return False
        speeds = self.speeds[effect].values()
        if effect in OPTION_EFFECTS:
            return (len(command) == 18 and command[3] == 0 and command[4] in speeds and command[5] <= 100
                    and command[6] in self.colour_modes.values() and command[7:9] == b"\xff\xff"
                    and (command[6] != self.colour_modes["single"] or command[12:15] == b"\0\0\0"))
        if effect in SINGLE_COLOUR:
            return (len(command) == 12 and command[3] == 0 and command[4] in speeds and command[5] <= 100
                    and command[6:9] == bytes([0x00, 0xFF, 0xFF])
                    and (effect not in COLOURLESS or command[9:] == b"\0\0\0"))
        if effect == "quicksand":
            if command[3] == 0x01:
                return (len(command) == 18 and command[4] in speeds and command[5] <= 100
                        and command[6] in self.quicksand_modes.values()
                        and command[7] in self.directions["quicksand"].values() and command[8] == 0xFF)
            return command[3] == 0x00 and len(command) == 13
        # gradient chunk: 02 = header + 2 stops, 01 / 00 = up to 4 stops each; positions 1..100 %
        chunk = command[3]
        if chunk == 0x02:
            head, stops = command[4:10], command[10:]
            low, high = self.gradient_stops
            return (len(head) == 6 and head[0] in speeds and head[1] <= 100 and head[2] == 0
                    and head[3] in self.directions[effect].values() and head[4] in self.thickness.values()
                    and low <= head[5] <= high and len(stops) == 8 and _stops_ok(stops))
        if chunk in (0x01, 0x00):
            return len(command[4:]) <= 16 and _stops_ok(command[4:])
        return False

    def change(self, value: dict) -> Change:
        """Write a normalised value; the read-back is the effect in 12 00 [9] (same profile, new effect)."""
        read = self.effect_read(value["effect"])
        return Change(self.packets(value), ("profile",), lambda s: {"light_mode": s.light_mode, "profile": s.profile},
                      lambda after, before: bool(after and before) and after == {**before, "light_mode": read})
