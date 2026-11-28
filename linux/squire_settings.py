#!/usr/bin/env python3
"""Squire settings window for Linux (GTK 4 + libadwaita).

Devices on the left; the selected device drawn in the middle with clickable zones and callouts; the values for
the selected zone on the right. Shows what squire.read_mouse_settings reads; the three lights, power-off and
low-battery warning, angle snapping and tuning, polling rate, surface, lift-off distance and the DPI stages (value,
colour, active stage, stage count) can be changed, and are written only on an explicit Apply (read back, then one
save).
The keyboard page shows what squire.read_keyboard_settings reads (profile, lighting effect, layout, power); the
lighting (single-colour effects), sleep and low-battery warning can be changed the same way (Apply, read back, one
50 55). The keyboard cannot report its colour: the page shows what Squire last saved to the active profile.

The code is in settings/ next to this file. Runs as its own process because the tray is GTK 3 (AppIndicator) and GTK 3 and 4 cannot share a process.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    import squire
except ImportError:  # running from the repository: squire.py lives one folder up
    sys.path.insert(0, str(HERE.parent))
    import squire
sys.path.insert(0, str(HERE))
from squire_tray import unsnap_environment  # noqa: E402  (pure function, does not load GTK)

# The window lives in settings/: window.py (sidebar, apply bar, Apply), pages/ (per device kind), editors/ (one per
# capability), drawings/ (per model). Their GTK-free names stay importable from here (tests, tools).
from settings import common  # noqa: E402
from settings.common import (  # noqa: E402,F401
    APP_DIR, ROOT, bluetooth_status, swatch_svg)
from settings.pages.mouse import (  # noqa: E402,F401
    MODE_NAMES, ZONE_TITLES, ZONE_KINDS, mode_name, shows_color, zone_summary, demo_data, DPI_KEYS, OTHER_KEYS,
    OTHER_TITLES, current_other, preview_settings, changed_zones, changes, change_title, change_command,
    write_change, edit_other, apply_status, planned_packets)
from settings.pages.keyboard import (  # noqa: E402,F401
    demo_keyboard, KEYBOARD_EFFECT_NAMES, same_effect, keyboard_effect_name, keyboard_groups, KB_ZONE_TITLES,
    keyboard_zone_summary, KB_KEYS, KB_SAVED_KEYS, KB_CHANGE_TITLES, kb_light_sent, kb_current, kb_edit, kb_changes,
    kb_saved_changes, kb_key_colours, kb_command, kb_commands, kb_write, kb_planned_packets, kb_apply_status,
    kb_preview)
from settings.drawings.mouse import (  # noqa: E402,F401
    STAGE_W, STAGE_H, DRAWING, HOTSPOTS, CALLOUTS, LINES, CALLOUT_TITLES, CHIPS, _led_paint, SHIELD, mouse_svg)
from settings.drawings.keyboard import (  # noqa: E402,F401
    KEYBOARD_ROWS, KB_UNIT, KB_GAP, KB_PAD, KB_W, KB_H, keyboard_glow, keyboard_gradient_stops,
    kb_layout, kb_key_style, kb_key_at, keyboard_svg)
from settings.editors.led_zones import (  # noqa: E402,F401
    EDITABLE_ZONES, EDIT_MODES, edit_pending, can_link)
from settings.editors.dpi import (  # noqa: E402,F401
    can_edit_dpi, edit_dpi_stages)
from settings.editors.sensor import (  # noqa: E402,F401
    can_edit_liftoff, wrapping_list_factory, can_edit_surface, SURFACE_CHOICES)
from settings.editors.power import (  # noqa: E402,F401
    POWER_OFF_CHOICES, can_edit_power)
from settings.editors.kb_lighting import (  # noqa: E402,F401
    KB_EFFECTS, KB_GRADIENT_NAMES, KB_DIRECTION_NAMES, KB_SPEED_NAMES, KB_THICKNESS_NAMES, KB_MODE_NAMES,
    KB_PER_KEY, KB_EFFECT_CHOICES, KB_PER_KEY_NOTE, kb_light_start, kb_edit_light)
from settings.editors.per_key import (  # noqa: E402,F401
    kb_full_keys, kb_keys_start, kb_select_per_key, kb_paint, kb_shown_keys)
from settings.editors.kb_power import (  # noqa: E402,F401
    can_edit_kb_sleep, can_edit_kb_warning)



def run(argv: list[str]) -> int:
    unsnap_environment()            # before GTK loads: the VS Code snap's variables make it abort
    from settings.window import App
    return App().run(argv)


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"squire-settings {squire.__version__}")
        return 0
    args = sys.argv[1:]
    if "--screenshot" in args:
        i = args.index("--screenshot")
        common.SCREENSHOT.extend(a for a in args[i + 1:i + 5] if not a.startswith("-"))
        del args[i:i + 1 + len(common.SCREENSHOT)]
    common.DEMO = "--demo" in args
    return run([sys.argv[0]] + [a for a in args if a not in ("--debug", "--demo")])


if __name__ == "__main__":
    sys.exit(main())
