"""Editor for the mouse's power chip: auto power-off and the low-battery warning."""
import squire


POWER_OFF_CHOICES = [1, 2, 3, 5, 10, None]      # minutes, None = never


def can_edit_power(s: squire.MouseSettings) -> bool:
    """Only when the warning level read from the mouse is one Squire can write back."""
    return s.low_warning in squire.LOW_WARNING_LEVELS and s.power_off_minutes in squire.POWER_OFF_CODES


def build(page, group):
    """Battery, auto power-off and low-battery warning (always written together: 51 37)."""
    from ..pages.mouse import current_other
    from ..widgets import Adw, Gtk, row
    s = page.settings
    b = page.window.batteries.get("mouse")
    if b and b.percent is not None:
        group.add(row("Battery", f"{b.percent} %", "charging" if b.charging else ""))
    if can_edit_power(s):
        power = page.pending.get("power") or current_other(s, "power")
        off = Adw.ComboRow(title="Turn off after", model=Gtk.StringList.new(
            ["Never" if m is None else f"{m} min" for m in POWER_OFF_CHOICES]))
        off.set_selected(POWER_OFF_CHOICES.index(power["power_off_minutes"]))
        off.connect("notify::selected",
                    lambda r, _p: on_power(page, power_off_minutes=POWER_OFF_CHOICES[r.get_selected()]))
        group.add(off)
        levels = list(squire.LOW_WARNING_LEVELS)
        warn = Adw.ComboRow(title="Low-battery warning", subtitle="Squire's notification uses this level",
                            model=Gtk.StringList.new([f"{w} %" for w in levels]))
        warn.set_selected(levels.index(power["low_warning"]))
        warn.connect("notify::selected", lambda r, _p: on_power(page, low_warning=levels[r.get_selected()]))
        group.add(warn)
    else:
        group.add(row("Turn off after", "Never" if s.power_off_minutes is None else f"{s.power_off_minutes} min"))
        group.add(row("Low-battery warning", f"{s.low_warning} %",
                      "Not one Squire can write back (10–50 %), so power is shown only"))


def on_power(page, **change):
    from ..pages.mouse import current_other
    power = dict(page.pending.get("power") or current_other(page.settings, "power"))
    power.update(change)
    page.on_other("power", power)
