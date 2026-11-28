"""Editor for the keyboard's power card: sleep and the low-battery warning."""
import squire


def can_edit_kb_sleep(k: "squire.KeyboardSettings") -> bool:
    """Only a sleep code Squire can write back (None means "never" only for ff, not for an unknown code)."""
    raw = k.raw.get("battery")
    try:
        return bytes.fromhex(raw)[6] in squire.KEYBOARD_SLEEP_CODES.values()
    except (TypeError, ValueError, IndexError):
        return k.sleep_minutes in squire.KEYBOARD_SLEEP_CODES


def can_edit_kb_warning(k: "squire.KeyboardSettings") -> bool:
    return k.low_warning in squire.KEYBOARD_WARNING_LEVELS


def build(page, prefs):
    """Battery, sleep and the low-battery warning (each editable only with a value Squire can write back)."""
    from ..pages.keyboard import kb_preview
    from ..widgets import Adw, Gtk, row
    from .power import POWER_OFF_CHOICES
    k = page.keyboard
    group = Adw.PreferencesGroup(title="Power", description="Battery")
    b = page.battery
    if b and b.percent is not None:
        group.add(row("Battery", f"{b.percent} %", "charging" if b.charging else ""))
    p, _ = kb_preview(k, page.pending, page.known)
    if can_edit_kb_sleep(k):
        sleep = Adw.ComboRow(title="Sleep after", model=Gtk.StringList.new(
            ["Never" if m is None else f"{m} min" for m in POWER_OFF_CHOICES]))
        sleep.set_selected(POWER_OFF_CHOICES.index(p.sleep_minutes))
        sleep.connect("notify::selected", lambda r, _p: page.on_edit("kb_sleep", POWER_OFF_CHOICES[r.get_selected()]))
        group.add(sleep)
    else:
        group.add(row("Sleep after", "Never" if k.sleep_minutes is None else f"{k.sleep_minutes} min",
                      "Not a value Squire can write back, so it is shown only"))
    if can_edit_kb_warning(k):
        levels = list(squire.LOW_WARNING_LEVELS) + [0]            # AC's order: 10..50 %, then never
        warn = Adw.ComboRow(title="Low-battery warning", subtitle="Squire's notification uses this level",
                            model=Gtk.StringList.new([squire.warning_text(w).capitalize() for w in levels]))
        warn.set_selected(levels.index(p.low_warning))
        warn.connect("notify::selected", lambda r, _p: page.on_edit("kb_warning", levels[r.get_selected()]))
        group.add(warn)
    else:
        group.add(row("Low-battery warning", squire.warning_text(k.low_warning),
                      "Not a level Squire can write back (10–50 % or never), so it is shown only"))
    prefs.add(group)
