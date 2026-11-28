"""Editor for the keyboard lighting: effect, brightness, colours, gradient, direction, speed."""
from typing import Optional

import squire

from ..pages.keyboard import KEYBOARD_EFFECT_NAMES, kb_light_sent, same_effect


# --- keyboard editing (writes approved and confirmed on the device 2026-11-22) ---
# `kb_pending` holds "kb_sleep" (minutes, None = never), "kb_warning" (%, 0 = never) and "kb_light"
# (squire.KEYBOARD_LIGHT_FIELDS). The keyboard reports only the effect, never its colour or brightness:
# `known` is what Squire last saved to the active profile (squire.remembered_keyboard_light), or None.
KB_EFFECTS = squire.KEYBOARD_EFFECTS                       # every effect Squire writes, in the keyboard's id order
KB_GRADIENT_NAMES = {"red": "Red", "blue-red": "Blue to red", "sunset": "Sunset", "lime-cyan": "Lime and cyan",
                     "yellow-magenta": "Yellow and magenta", "pink-purple": "Pink and purple", "spectrum": "Spectrum"}
# Rainbow: the direction the colours move. Diagonals are assumed (AC's odd codes, capture_4); quicksand: down / up.
KB_DIRECTION_NAMES = {"right": "Left to right", "left": "Right to left", "down": "Top to bottom",
                      "up": "Bottom to top", "down-right": "To bottom right", "down-left": "To bottom left",
                      "up-left": "To top left", "up-right": "To top right", "outwards": "From the center outwards",
                      "none": "None"}
KB_SPEED_NAMES = {"fast": "Fast", "normal": "Normal", "slow": "Slow", "none": "None"}
KB_THICKNESS_NAMES = {"low": "Low", "normal": "Normal", "strong": "Strong"}
KB_MODE_NAMES = {"single": "One color", "double": "Two colors", "random": "Random", "scale": "Color scale"}
KB_PER_KEY = "per-key"                                      # the effect row's choice for per-key colours
# The effect row: effects by name, then per-key colours, then off
KB_EFFECT_CHOICES = sorted((e for e in KB_EFFECTS if e != squire.KEYBOARD_OFF),
                           key=lambda e: KEYBOARD_EFFECT_NAMES.get(e, e).casefold()) + [KB_PER_KEY, squire.KEYBOARD_OFF]
KB_PER_KEY_NOTE = ("The keyboard cannot store per-key colors: they are lost when it is switched off, and its own "
                   "profile switching is blocked until then. Save them as a preset in Squire to reuse them.")


def kb_light_start(k: "squire.KeyboardSettings", known: Optional[dict]) -> dict:
    """What the lighting editor shows before any edit: what Squire saved, else the effect read with AC's defaults."""
    if known:
        return dict(known)
    effect = k.light_mode if k.light_mode in squire.KEYBOARD_LIGHT_SPEEDS else "static"
    return squire.keyboard_light_value(effect, 100, "#ffffff")


def kb_edit_light(k: "squire.KeyboardSettings", pending: dict, known: Optional[dict], **changes) -> dict:
    """A lighting edit. A new effect starts at its default speed. The edit is dropped only when it is back to what
    Squire knows the keyboard has; without that knowledge every edit stays pending (the keyboard cannot be asked)."""
    light = dict(pending.get("kb_light") or kb_light_start(k, known))
    if changes.get("effect", light["effect"]) != light["effect"]:
        light["speed"] = light["direction"] = light["mode"] = None        # the new effect's defaults
        if light["effect"] == squire.KEYBOARD_OFF:                        # off fixed them at 4 % black
            light["brightness"], light["color"] = 100, "#ffffff"
    light.update(changes)
    sent = kb_light_sent(light)                           # validates
    # colours, gradient, thickness and background stay as chosen, for a later effect that uses them
    light = {**light, "speed": sent["speed"], "direction": sent["direction"], "mode": sent["mode"],
             **{f: sent[f] or light.get(f) for f in ("gradient", "thickness", "color2")}}
    if light["effect"] == squire.KEYBOARD_OFF:
        light.update(brightness=sent["brightness"], color=sent["color"])
    pending = dict(pending)
    if known and kb_light_sent(light) == kb_light_sent(known) and same_effect(light, k.light_mode):
        pending.pop("kb_light", None)
    else:
        pending["kb_light"] = light
    return pending


def build(page, prefs):
    """The lighting group: effect (with per-key colours and off), then the rows the chosen effect has."""
    from ..pages.keyboard import keyboard_effect_name
    from ..widgets import Adw, Gtk, brightness_scale, row
    from . import per_key as per_key_editor
    k = page.keyboard
    light = page.pending.get("kb_light") or kb_light_start(k, page.known)
    known = page.known is not None
    group = Adw.PreferencesGroup(
        title="Lighting", description="The keyboard reports its effect but not its color or brightness. "
        + ("Shown: what Squire last saved to this profile." if known else
           "Squire has not saved a color to this profile, so Apply sends exactly what you set here."))
    if k.light_mode not in squire.KEYBOARD_LIGHT_SPEEDS:
        group.add(row("On the keyboard", keyboard_effect_name(k.light_mode),
                      "Squire cannot write this effect yet; pick one below to replace it"))
    names = Gtk.StringList.new([keyboard_effect_name(e) for e in KB_EFFECT_CHOICES])
    effect = Adw.ComboRow(title="Effect", model=names)
    editing = "kb_light" in page.pending or k.light_mode in squire.KEYBOARD_LIGHT_SPEEDS
    per_key = page.per_key_mode()
    effect.set_selected(KB_EFFECT_CHOICES.index(KB_PER_KEY) if per_key
                        else KB_EFFECT_CHOICES.index(light["effect"]) if editing
                        else Gtk.INVALID_LIST_POSITION)

    def on_effect_row(r, _p):
        i = r.get_selected()
        if i >= len(KB_EFFECT_CHOICES):
            return
        if KB_EFFECT_CHOICES[i] == KB_PER_KEY:
            per_key_editor.on_select(page)
        else:
            on_effect(page, KB_EFFECT_CHOICES[i])
    effect.connect("notify::selected", on_effect_row)
    group.add(effect)
    if per_key:
        group.set_description(KB_PER_KEY_NOTE)
        per_key_editor.build(page, group)
        prefs.add(group)
        return
    if editing and light["effect"] == squire.KEYBOARD_OFF:
        group.add(row("Off", "Black at 4 %", "How Armoury Crate turns the keyboard lighting off"))
    elif editing:
        bright = Adw.ActionRow(title="Brightness", subtitle="" if known or "kb_light" in page.pending
                               else "Not readable from the keyboard")
        scale = brightness_scale(light["brightness"])
        scale.connect("value-changed", lambda sc: page.on_light(brightness=int(round(sc.get_value()))))
        bright.add_suffix(scale)
        group.add(bright)
        subtitle = "" if known or "kb_light" in page.pending else "Not readable from the keyboard"
        options = light["effect"] in squire.KEYBOARD_OPTION_EFFECTS
        if options:
            add_choice(page, group, "Colors", list(squire.KEYBOARD_COLOUR_MODES), KB_MODE_NAMES,
                       light.get("mode") or "single", "mode")
        if light["effect"] not in squire.KEYBOARD_COLOURLESS:
            title = "First color" if options and light.get("mode") not in (None, "single") else "Color"
            group.add(color_row(page, title, light["color"], "color", subtitle))
        if options and light.get("mode") not in (None, "single"):
            group.add(color_row(page, "Second color", light.get("color2") or squire.KEYBOARD_SECOND_COLOUR,
                                "color2", "Random mode uses both colors too" if light.get("mode") == "random" else ""))
        if options:
            background = Adw.SwitchRow(title="Background", subtitle="Lights the keys that are not animated",
                                       active=bool(light.get("background")))
            background.connect("notify::active", lambda r, _p: page.on_light_rerender(
                background=(page.background or "#ffffff") if r.get_active() else None))
            group.add(background)
            if light.get("background"):
                page.background = light["background"]
                group.add(color_row(page, "Background color", light["background"], "background", ""))
        if light["effect"] in squire.KEYBOARD_GRADIENT_EFFECTS:
            names = list(squire.KEYBOARD_GRADIENTS)
            gradient = light.get("gradient") or "spectrum"
            labels = dict(KB_GRADIENT_NAMES)
            if gradient not in names:              # a custom gradient set with the CLI
                names.append(gradient)
                labels[gradient] = "Custom"
            add_choice(page, group, "Colors", names, labels, gradient, "gradient",
                       subtitle="Gradients from Armoury Crate")
            add_choice(page, group, "Thickness", list(squire.KEYBOARD_THICKNESS), KB_THICKNESS_NAMES,
                       light.get("thickness") or "strong", "thickness")
        elif light["effect"] == "quicksand":
            add_choice(page, group, "Colors", list(squire.KEYBOARD_QUICKSAND_MODES), KB_MODE_NAMES,
                       light.get("mode") or "scale", "mode",
                       subtitle="The six-color scale Armoury Crate uses by default")
        directions = list(squire.KEYBOARD_DIRECTIONS.get(light["effect"], ()))
        if len(directions) > 1:
            add_choice(page, group, "Direction", directions, KB_DIRECTION_NAMES,
                       light.get("direction") or directions[0], "direction")
        speeds = list(squire.KEYBOARD_LIGHT_SPEEDS[light["effect"]])
        if len(speeds) > 1:
            add_choice(page, group, "Speed", speeds, KB_SPEED_NAMES, light["speed"], "speed")
    prefs.add(group)


def add_choice(page, group, title, values, names, current, field, subtitle=""):
    """A combo row for one lighting field. Rows that change which other rows exist re-render."""
    from ..widgets import Adw, Gtk
    combo = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new([names[v] for v in values]))
    combo.set_selected(values.index(current) if current in values else 0)
    rerender = field == "mode"

    def on_selected(r, _p):
        if r.get_selected() < len(values):
            (page.on_light_rerender if rerender else page.on_light)(**{field: values[r.get_selected()]})
    combo.connect("notify::selected", on_selected)
    group.add(combo)


def color_row(page, title, color, field, subtitle):
    from ..widgets import Adw, button_color, color_button
    action = Adw.ActionRow(title=title, subtitle=subtitle)
    button = color_button(color)

    def on_color(btn, _p):
        value = button_color(btn)
        if field == "background":
            page.background = value
        page.on_light(**{field: value})
    button.connect("notify::rgba", on_color)
    action.add_suffix(button)
    return action


def on_effect(page, effect):
    current = (page.pending.get("kb_light") or kb_light_start(page.keyboard, page.known))["effect"]
    editing = "kb_light" in page.pending or page.keyboard.light_mode in squire.KEYBOARD_LIGHT_SPEEDS
    if page.per_key_mode():
        # leaving per-key colours: the effect is sent even if it is the one Squire knows
        page.pending.pop("kb_keys", None)
        page.on_light(effect=effect)
        if "kb_light" not in page.pending and page.keys_active is not None:
            page.pending["kb_light"] = dict(kb_light_start(page.keyboard, page.known))
        page.window.render()
        return
    if effect == current and editing:
        return      # the combo was set by render(), not by the user
    page.on_light(effect=effect)
    page.window.render()   # brightness, colour and speed rows follow the effect
