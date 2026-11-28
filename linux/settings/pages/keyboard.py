"""The keyboard page's state logic: pending sleep / warning / lighting / per-key colours, what Apply writes and
saves, the bytes it sends, the summaries on the cards. No GTK."""
from typing import Optional

import squire

from ..common import ROOT


def demo_keyboard() -> "squire.KeyboardSettings":
    """Keyboard settings from the real captures in protocol.json (profile 1, CH-DE; sends nothing)."""
    import json
    data = json.loads((ROOT / "protocol.json").read_text())
    k = squire.KeyboardSettings("ROG Falchion RX LP", "omni")
    battery = next(v for v in data["vectors"] if v["device"] == "keyboard" and v["connection"] == "omni")
    squire.parse_keyboard_settings(k, "battery", bytes.fromhex(battery["reply"])[1:])
    seen = set()
    for capture in data["keyboard_settings"]["captures"]:
        if capture["command"] not in seen:        # the first capture of each command: profile 1
            seen.add(capture["command"])
            squire.parse_keyboard_settings(k, capture["command"], bytes.fromhex(capture["reply"])[1:])
    return k


KEYBOARD_EFFECT_NAMES = {"host-driven": "Controlled by an app", "starry-night": "Starry night",
                         "color-cycle": "Color cycle", "off": "Off", "per-key": "Per-key colors"}


def same_effect(light: Optional[dict], mode: Optional[str]) -> bool:
    """Whether a lighting value is what the keyboard reports as `mode` ("off" reads as static)."""
    return bool(light) and mode is not None and mode in (light["effect"], squire.keyboard_effect_read(light["effect"]))


def keyboard_effect_name(mode: Optional[str]) -> str:
    if not mode:
        return "–"
    return KEYBOARD_EFFECT_NAMES.get(mode, mode.replace("-", " ").capitalize())


def keyboard_groups(k: "squire.KeyboardSettings") -> list[tuple[str, list[tuple[str, str, str]]]]:
    """The keyboard page as (group title, [(row title, value, subtitle)]): read-only for now."""
    layout = k.layout or (f"Unknown (id {k.layout_id})" if k.layout_id is not None else "–")
    if k.iso is not None and k.layout_id is not None:
        layout += " · ISO" if k.iso else " · ANSI"
    sleep = "Never" if k.sleep_minutes is None else f"After {k.sleep_minutes} min"
    device = [("Connection", {"omni": "Omni receiver", "wired": "USB cable"}.get(k.connection, k.connection), "")]
    if k.firmware:
        device.append(("Firmware", k.firmware, ""))
    if k.receiver_firmware:
        device.append(("Receiver firmware", k.receiver_firmware, ""))
    return [
        ("Lighting", [
            ("Effect", keyboard_effect_name(k.light_mode), "Its color cannot be read yet"),
        ]),
        ("Profile", [
            ("Active profile", f"{k.profile} of 5" if k.profile else "–", "Switch profiles on the keyboard"),
        ]),
        ("Layout", [
            ("Layout", layout, "Set at the factory"),
        ]),
        ("Power", [
            ("Sleep", sleep, ""),
            ("Low-battery warning", squire.warning_text(k.low_warning).capitalize(), ""),
        ]),
        ("Device", device),
    ]


KB_ZONE_TITLES = {"kb_light": "Lighting", "kb_profile": "Profile", "kb_layout": "Layout", "kb_power": "Power"}


def keyboard_zone_summary(k: "squire.KeyboardSettings", zone: str, light: Optional[dict] = None) -> str:
    if zone == "kb_light":
        if same_effect(light, k.light_mode):
            if light["effect"] == squire.KEYBOARD_OFF:
                return "Off"
            return f"{keyboard_effect_name(k.light_mode)} · {light['brightness']} %"
        return keyboard_effect_name(k.light_mode)
    if zone == "kb_profile":
        return f"{k.profile} of 5" if k.profile else "not read"
    if zone == "kb_layout":
        if k.layout_id is None:
            return "not read"
        return (k.layout or f"id {k.layout_id}") + (" · ISO" if k.iso else " · ANSI")
    if zone == "kb_power":
        sleep = "never sleeps" if k.sleep_minutes is None else f"sleep {k.sleep_minutes} min"
        return f"{sleep} · " + ("never warns" if k.low_warning == 0 else f"warn at {squire.warning_text(k.low_warning)}")
    return ""


# Apply order: the saved ones first (the last of them carries the save), per-key colours after them, never saved
KB_KEYS = ("kb_sleep", "kb_warning", "kb_light", "kb_keys")
KB_SAVED_KEYS = ("kb_sleep", "kb_warning", "kb_light")
KB_CHANGE_TITLES = {"kb_sleep": "Sleep", "kb_warning": "Low-battery warning", "kb_light": "Lighting",
                    "kb_keys": "Per-key colors"}


def kb_light_sent(light: dict) -> dict:
    """The lighting as it is sent (an effect without a colour of its own carries none)."""
    return squire.keyboard_light_from(light)


def kb_current(k: "squire.KeyboardSettings", key: str, known: Optional[dict]):
    return {"kb_sleep": k.sleep_minutes, "kb_warning": k.low_warning}.get(key, known)


def kb_edit(k: "squire.KeyboardSettings", pending: dict, key: str, value, known: Optional[dict] = None) -> dict:
    """A sleep or warning edit: kept pending, or dropped when it is back to what the keyboard has."""
    pending = dict(pending)
    if value == kb_current(k, key, known):
        pending.pop(key, None)
    else:
        pending[key] = value
    return pending


def kb_changes(pending: dict) -> list[str]:
    return [key for key in KB_KEYS if key in pending]


def kb_saved_changes(pending: dict) -> list[str]:
    """The changes Apply saves (50 55 once, with the last of them); per-key colours are not among them."""
    return [key for key in KB_SAVED_KEYS if key in pending]


def kb_key_colours(value: dict) -> dict:
    """{key id: "#rrggbb"} -> what set_keyboard_key_colours sends, {key id: (r, g, b)}."""
    return {key: tuple(bytes.fromhex(color[1:])) for key, color in value.items()}


def kb_command(key: str, value) -> bytes:
    if key == "kb_sleep":
        return squire.keyboard_sleep_command(value)
    if key == "kb_warning":
        return squire.keyboard_warning_command(value)
    raise ValueError("lighting: use kb_commands")


def kb_commands(key: str, value) -> list:
    """Every packet one change sends (a gradient is three, quicksand two, per-key colours up to five)."""
    if key == "kb_keys":
        return squire.keyboard_direct_packets(kb_key_colours(value))
    if key == "kb_light":
        return squire.keyboard_light_packets(squire.keyboard_light_from(value))
    return [kb_command(key, value)]


def kb_write(key: str, value, save: bool) -> "squire.WriteResult":
    if key == "kb_keys":
        return squire.set_keyboard_key_colours(kb_key_colours(value))      # never saved
    if key == "kb_sleep":
        return squire.set_keyboard_sleep(value, save=save)
    if key == "kb_warning":
        return squire.set_keyboard_warning(value, save=save)
    v = squire.keyboard_light_from(value)
    return squire.set_keyboard_light(v["effect"], v["brightness"], v["color"], v["speed"], v["gradient"],
                                     v["direction"], save=save, thickness=v["thickness"], mode=v["mode"],
                                     color2=v["color2"], background=v["background"])


def kb_planned_packets(k: "squire.KeyboardSettings", pending: dict) -> list[str]:
    """Exactly what Apply will send to the keyboard, for "Show bytes"."""
    report_id = 0x02 if k.connection == "omni" else None
    length = 64 if k.connection == "omni" else 65
    lines = []
    for key in kb_saved_changes(pending):
        for command in kb_commands(key, pending[key]):
            lines.append(squire.packet(report_id, command, length, write=True,
                                       device="keyboard")[:len(command) + 1].hex(" ") + " …")
    if lines:
        lines.append(squire.packet(report_id, squire.KEYBOARD_SAVE_COMMAND, length, write=True,
                                   device="keyboard")[:3].hex(" ") + " …  (save)")
    if "kb_keys" in pending:
        for command in kb_commands("kb_keys", pending["kb_keys"]):
            lines.append(squire.packet(report_id, command, length, write=True,
                                       device="keyboard")[:len(command) + 1].hex(" ") + " …")
        lines[-1] += "  (per-key colors, not saved)"
    return lines


def kb_apply_status(pending: dict) -> str:
    keys = kb_changes(pending)
    if not keys:
        return "Showing the values read from the keyboard. Nothing is sent until you click Apply."
    names = ", ".join(KB_CHANGE_TITLES[key] for key in keys)
    if keys == ["kb_keys"]:
        return f"Not sent yet: {names}. Apply sends them; the keyboard keeps them until it is switched off."
    tail = " Per-key colors are sent but not saved." if "kb_keys" in keys else ""
    return f"Not sent yet: {names}. Apply writes it to the keyboard, reads it back and saves it.{tail}"


def kb_preview(k: "squire.KeyboardSettings", pending: dict, known: Optional[dict]):
    """(settings, lighting or None) as they will be after Apply, for the drawing and the cards."""
    import dataclasses
    light = pending.get("kb_light") or known
    p = dataclasses.replace(k, sleep_minutes=pending.get("kb_sleep", k.sleep_minutes),
                            low_warning=pending.get("kb_warning", k.low_warning))
    if "kb_light" in pending:
        p = dataclasses.replace(p, light_mode=pending["kb_light"]["effect"])   # "off" draws as off
    return p, light


class KeyboardPage:
    """The keyboard page: the drawing (one lighting hotspot over the keys, or click/drag painting for per-key
    colours) with the Lighting card above, the Profile / Layout / Power cards below and, on the right, the editor of
    the selected card (kb_lighting with per_key, kb_power) or the values read. The keyboard reports only its effect,
    never its colour or brightness: `known` is what Squire last saved to the active profile."""
    kind = "keyboard"
    noun = "keyboard"
    title = "ROG Falchion RX LP"
    icon = "input-keyboard-symbolic"
    banner = "Lighting, sleep and the low-battery warning can be changed. Nothing is sent until you click Apply."
    status_texts = {
        "asleep": ("The keyboard is asleep", "Press a key, then read again."),
        "no-response": ("No answer from the keyboard", "Press a key, then read again."),
        "no-access": ("No access to the receiver", "Install the udev rule (see the README), then "
                                                  "re-plug the receiver."),
        "not-found": ("Keyboard not found", "Plug in the Omni receiver or the USB cable."),
    }
    failure_texts = {"asleep": "The keyboard is asleep. Press a key and click Apply again.",
                     "ok": "Written to the keyboard's memory but not saved (lost when it is switched off).",
                     "mismatch": "The keyboard did not take the new value (read back differs). Nothing was saved.",
                     "no-response": "The keyboard did not answer. Press a key and click Apply again."}

    def __init__(self, window):
        self.window = window
        self.keyboard: Optional[squire.KeyboardSettings] = None
        self.selected = "kb_light"
        self.pending: dict = {}        # edits not sent yet (kb_sleep, kb_warning, kb_light, kb_keys)
        self.known: Optional[dict] = None          # the lighting Squire last saved to the active profile
        self.background: Optional[str] = None      # last background colour, kept while the switch is off
        self.value_labels: dict = {}
        self.keys_active: Optional[dict] = None    # per-key colours this window last sent (not readable)
        self.brush = "#ff0000"                     # colour a click on a key paints
        self.preset: Optional[str] = None          # key preset last loaded or saved
        self.drawing = None

    @property
    def data(self):
        return self.keyboard

    def load(self, keyboard, demo: bool):
        self.keyboard = keyboard
        ok = keyboard is not None and keyboard.status == "ok"
        self.known = squire.remembered_keyboard_light(keyboard.profile, keyboard.light_mode) \
            if ok and not demo else None

    # stage ---------------------------------------------------------------------------------------------------
    def select(self, zone):
        self.selected = zone
        self.window.render()

    def summary(self, preview, zone, light) -> str:
        if zone == "kb_light" and self.per_key_mode():
            return "Per-key colors"      # that the keyboard cannot store them: the inspector says so
        return keyboard_zone_summary(preview, zone, light)

    def card(self, zone, width):
        from ..widgets import card
        preview, light = kb_preview(self.keyboard, self.pending, self.known)
        button, label = card(KB_ZONE_TITLES[zone], self.summary(preview, zone, light), width,
                             zone == self.selected, lambda: self.select(zone))
        self.value_labels[zone] = label
        return button

    def shown_keys(self):
        from ..editors.per_key import kb_shown_keys
        return kb_shown_keys(self.pending, self.keys_active, self.per_key_mode())

    def build_stage(self):
        from ..drawings.keyboard import KB_H, KB_PAD, KB_W, keyboard_svg
        from ..drawings.mouse import STAGE_H, STAGE_W
        from ..widgets import Gdk, Gtk, chip_row, stage_title, texture
        k = self.keyboard
        fixed = Gtk.Fixed()
        fixed.set_size_request(STAGE_W, STAGE_H)
        fixed.put(stage_title(k.name, self.window.batteries.get("keyboard")), 32, 24)

        x, y = (STAGE_W - KB_W) // 2, 230
        per_key = self.per_key_mode()
        drawing = Gtk.Picture.new_for_paintable(texture(keyboard_svg(
            *kb_preview(k, self.pending, self.known), self.shown_keys())))
        self.drawing = drawing
        drawing.set_size_request(KB_W, KB_H)
        drawing.set_can_shrink(True)
        drawing.set_alternative_text("Top view of the keyboard")
        fixed.put(drawing, x, y)
        painting = per_key and self.selected == "kb_light"
        if painting:
            # click or drag over the keys to paint them
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", lambda _g, px, py: self.on_drawing_drag(drawing, px, py))

            def on_update(g, dx, dy):
                ok, sx, sy = g.get_start_point()
                if ok:
                    self.on_drawing_drag(drawing, sx + dx, sy + dy)
            drag.connect("drag-update", on_update)
            drawing.add_controller(drag)
            drawing.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        else:
            # the keys are the lighting zone: one hotspot over all of them, with a callout above
            spot = Gtk.Button(css_classes=["squire-hotspot", "outline-only"]
                              + (["selected"] if self.selected == "kb_light" else []), tooltip_text="Lighting")
            spot.update_property([Gtk.AccessibleProperty.LABEL], ["Lighting"])
            spot.set_size_request(KB_W - 2 * KB_PAD + 8, KB_H - 2 * KB_PAD + 8)
            spot.connect("clicked", lambda _b: self.select("kb_light"))
            fixed.put(spot, x + KB_PAD - 4, y + KB_PAD - 4)
        line = Gtk.Box(css_classes=["squire-vline"] + (["selected"] if self.selected == "kb_light" else []))
        line.set_size_request(1, 34)
        fixed.put(line, STAGE_W // 2, y - 20)
        fixed.put(self.card("kb_light", 170), STAGE_W // 2 - 85, y - 76)

        chips = [self.card(zone, 190) for zone in ("kb_profile", "kb_layout", "kb_power")]
        fixed.put(chip_row("Click the keys, or one of these:", chips, STAGE_W - 64), 32, 520)
        return fixed

    def on_drawing_drag(self, picture, x, y):
        """Paint the key under the pointer (drawing coordinates follow the picture's scale and centring)."""
        from ..drawings.keyboard import KB_H, KB_W, kb_key_at
        w, h = picture.get_width(), picture.get_height()
        scale = min(w / KB_W, h / KB_H) if w and h else 1
        x, y = (x - (w - KB_W * scale) / 2) / scale, (y - (h - KB_H * scale) / 2) / scale
        key = kb_key_at(self.keyboard, x, y)
        if key is not None and self.current_keys().get(key) != self.brush:
            self.paint({key: self.brush})

    # inspector: one editor per capability --------------------------------------------------------------------
    def build_inspector(self):
        from ..editors import kb_lighting, kb_power
        from ..widgets import Adw, row
        k = self.keyboard
        page = Adw.PreferencesPage(hexpand=False)
        groups = dict(keyboard_groups(k))
        title = KB_ZONE_TITLES[self.selected]
        if self.selected == "kb_light":
            kb_lighting.build(self, page)
        elif self.selected == "kb_power":
            kb_power.build(self, page)
        else:
            group = Adw.PreferencesGroup(title=title, description="Shown only")
            for name, value, subtitle in groups[title]:
                group.add(row(name, value, subtitle))
            page.add(group)
        device = Adw.PreferencesGroup(title="Device")
        for name, value, subtitle in groups["Device"]:
            device.add(row(name, value, subtitle))
        page.add(device)
        return page

    # edits ---------------------------------------------------------------------------------------------------
    def per_key_mode(self) -> bool:
        """Per-key colours are being edited, or were sent last and no effect is pending."""
        return "kb_keys" in self.pending or (self.keys_active is not None and "kb_light" not in self.pending)

    def current_keys(self) -> dict:
        from ..editors.per_key import kb_keys_start
        return self.pending.get("kb_keys") or kb_keys_start(self.keyboard, self.keys_active)

    def paint(self, changes):
        from ..editors.per_key import kb_paint
        current = {} if "kb_keys" in self.pending else self.current_keys()
        self.pending = kb_paint(self.keyboard, self.pending, self.keys_active, {**current, **changes})
        self.window.update_preview()

    def on_edit(self, key, value):
        self.pending = kb_edit(self.keyboard, self.pending, key, value, self.known)
        self.window.update_preview()

    def on_light(self, **changes):
        from ..editors.kb_lighting import kb_edit_light
        self.pending = kb_edit_light(self.keyboard, self.pending, self.known, **changes)
        self.window.update_preview()

    def on_light_rerender(self, **changes):
        self.on_light(**changes)
        self.window.render()   # e.g. a second colour row appears or goes

    def update_preview(self):
        """The drawing and the cards follow the pending edits."""
        from ..drawings.keyboard import keyboard_svg
        from ..widgets import texture
        preview, light = kb_preview(self.keyboard, self.pending, self.known)
        if self.drawing is not None:
            self.drawing.set_paintable(texture(keyboard_svg(preview, light, self.shown_keys())))
        for zone, label in self.value_labels.items():
            label.set_label(self.summary(preview, zone, light))

    def revert(self):
        self.pending.clear()

    # Apply (the window runs it: saved changes first, 50 55 once with the last of them; per-key colours last,
    # never saved) ---------------------------------------------------------------------------------------------
    def changes(self) -> list:
        return kb_changes(self.pending)

    def saved_changes(self, work: dict) -> list:
        return kb_saved_changes(work)

    def apply_status(self) -> str:
        return kb_apply_status(self.pending)

    def planned_packets(self) -> list:
        return kb_planned_packets(self.keyboard, self.pending)

    def change_title(self, key) -> str:
        return KB_CHANGE_TITLES[key]

    def write(self, key, value, save) -> "squire.WriteResult":
        return kb_write(key, value, save)

    def success_title(self, work) -> str:
        return "Sent to the keyboard (not stored on it)" if list(work) == ["kb_keys"] else "Saved to the keyboard"

    def accept(self, key, value, result) -> bool:
        """Take over a written change; False = it failed (shown in the dialog)."""
        if key == "kb_keys":
            if result.status != "ok":
                return False
            self.keys_active = value
            self.pending.pop(key, None)
            return True
        if not (result.status == "ok" and result.saved):
            return False
        if key == "kb_sleep":
            self.keyboard.sleep_minutes = value
        elif key == "kb_warning":
            self.keyboard.low_warning = value
        else:
            self.keyboard.light_mode = value["effect"]
            self.known = kb_light_sent(value)   # set_keyboard_light remembered it as well
            self.keys_active = None             # the effect replaces per-key colours
        self.pending.pop(key, None)
        return True

    def demo_edit(self, flags) -> bool:
        """--screenshot ... keyboard edit [perkey|rainbow|raindrop]: a sample edit (not sent); True when made."""
        from ..editors.per_key import kb_paint, kb_select_per_key
        if "edit" not in flags or {"kb_light", "kb_keys"} & set(self.pending):
            return False
        self.selected = "kb_light"
        if "perkey" in flags:
            self.pending = kb_select_per_key(self.keyboard, self.pending, None, "#000000")
            self.preset = "Fixed rainbow"                 # shows a long preset name
            self.pending = kb_paint(self.keyboard, self.pending, None, {
                0x01: "#ff0000", 0x1A: "#ff8000", 0x12: "#ff8000", 0x22: "#ff8000", 0x13: "#ff8000",
                0x71: "#3d8bff", 0x75: "#ffff00", 0x6D: "#ffff00", 0x65: "#ffff00", 0x6C: "#ffff00"})
        elif "rainbow" in flags:
            self.on_light(effect="rainbow", gradient="sunset", direction="left")
        elif "raindrop" in flags:
            self.on_light(effect="raindrop", mode="double", color="#3d8bff", color2="#ff00ff",
                          background="#1bff00")
        else:
            self.on_light(effect="static", brightness=80, color="#3d8bff")
        return True
