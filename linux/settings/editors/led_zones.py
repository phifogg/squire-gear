"""Editor for the mouse's LED zones: effect, brightness, colour, "Same effect on all zones"."""
import squire

from ..pages.mouse import mode_name, shows_color


# LED zones that can be changed (51 28 with zone 00 logo, 01 wheel, 02 side; logo confirmed on the device).
EDITABLE_ZONES = ("logo", "wheel", "side")
EDIT_MODES = list(squire.LED_WRITE_MODES)      # static, breathing, color-cycle, react, battery, off


def edit_pending(s: squire.MouseSettings, pending: dict, zone: str, linked: bool = False, **changes) -> dict:
    """An edit to one zone, or with `linked` to every light zone ("Same effect on all zones"): the zone's state
    plus the changes becomes pending for each target, or is dropped where it is back to what the mouse has.
    A linked edit without changes copies the zone to the others (the switch being turned on)."""
    led = dict(pending.get(zone) or s.leds[zone])
    led.update(changes)
    pending = dict(pending)
    for target in ([z for z in EDITABLE_ZONES if s.leds.get(z)] if linked else [zone]):
        current = s.leds[target]
        if squire.led_matches(led, current) and led["mode"] == current["mode"]:
            pending.pop(target, None)       # back to what the mouse has
        else:
            pending[target] = dict(led)
    return pending


def can_link(s: squire.MouseSettings, pending: dict, zone: str) -> bool:
    """Only an effect Squire can write can be copied to the other zones."""
    led = pending.get(zone) or s.leds.get(zone)
    return bool(led) and led["mode"] in EDIT_MODES


def build(page, group, zone):
    """One light zone: the editor for a zone Squire writes, else the values read."""
    from ..widgets import row, swatch
    s = page.settings
    if zone in EDITABLE_ZONES and s.leds.get(zone):
        build_editor(page, group, zone, page.pending.get(zone) or s.leds[zone])
        return
    led = s.leds.get(zone)
    if led:
        group.add(row("Effect", mode_name(led["mode"])))
        if led["mode"] != "off":
            group.add(row("Brightness", f"{led['brightness']} %"))
        if shows_color(led["mode"]):
            group.add(row("Color", led["color"], suffix=swatch(led["color"])))
    else:
        group.add(row("Not read", ""))


def build_editor(page, group, zone, led):
    """Effect, brightness, colour and "Same effect on all zones". page.update_preview() hides brightness and colour
    where the effect has none (page.brightness_row, page.color_row)."""
    from ..widgets import Adw, Gtk, brightness_scale, button_color, color_button
    linkable = can_link(page.settings, page.pending, zone)
    link = Adw.SwitchRow(title="Same effect on all zones",
                         subtitle="One edit sets logo, scroll wheel and side light" if linkable
                         else "Not for an effect Squire cannot write",
                         active=page.link_zones and linkable, sensitive=linkable)
    link.connect("notify::active", lambda r, _p: on_link(page, zone, r.get_active()))
    group.add(link)

    modes = Gtk.StringList.new([mode_name(m) for m in EDIT_MODES])
    effect = Adw.ComboRow(title="Effect", model=modes)
    effect.set_selected(EDIT_MODES.index(led["mode"]) if led["mode"] in EDIT_MODES
                        else Gtk.INVALID_LIST_POSITION)
    effect.connect("notify::selected",
                   lambda r, _p: page.on_edit(zone, mode=EDIT_MODES[r.get_selected()])
                   if r.get_selected() < len(EDIT_MODES) else None)
    group.add(effect)

    page.brightness_row = Adw.ActionRow(title="Brightness")
    scale = brightness_scale(led["brightness"])
    scale.connect("value-changed", lambda sc: page.on_edit(zone, brightness=int(round(sc.get_value()))))
    page.brightness_row.add_suffix(scale)
    group.add(page.brightness_row)

    page.color_row = Adw.ActionRow(title="Color")
    button = color_button(led["color"])
    button.connect("notify::rgba", lambda btn, _p: page.on_edit(zone, color=button_color(btn)))
    page.color_row.add_suffix(button)
    group.add(page.color_row)
    page.editor_zone = zone


def on_link(page, zone, on):
    page.link_zones = on
    if on:   # copy this zone to the others; from now on every edit sets all of them
        page.on_edit(zone)
