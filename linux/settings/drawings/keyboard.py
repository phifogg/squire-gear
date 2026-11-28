"""Top view of the keyboard (own neutral artwork, no ASUS marks): key rows per layout, the lighting glow, per-key
colours and the key under a click. No GTK."""
from typing import Optional

import squire

from ..pages.keyboard import same_effect


# --- keyboard drawing (own neutral artwork, no ASUS marks): a 65 % low-profile board, 16 units wide ---
# Rows as (legend, width in units); "" = a key without a legend. ISO rows follow the CH-DE layout the owner's
# keyboard reports (12 12 id 25); ANSI rows are the US shape. The ISO Enter spans rows 2 and 3.
_KB_ROW0 = [("Esc", 1), ("1", 1), ("2", 1), ("3", 1), ("4", 1), ("5", 1), ("6", 1), ("7", 1), ("8", 1), ("9", 1),
            ("0", 1), ("'", 1), ("^", 1), ("⌫", 2), ("Ins", 1)]
_KB_ROW4 = [("Ctrl", 1.25), ("❖", 1.25), ("Alt", 1.25), ("", 6.25), ("Alt", 1), ("Fn", 1), ("Ctrl", 1),
            ("←", 1), ("↓", 1), ("→", 1)]
KEYBOARD_ROWS = {
    "iso": [
        _KB_ROW0,
        [("Tab", 1.5)] + [(c, 1) for c in "QWERTZUIOPü¨"] + [("ISO-ENTER", 1.5), ("Del", 1)],
        [("Caps", 1.75)] + [(c, 1) for c in "ASDFGHJKLöä$"] + [("ISO-ENTER-LOW", 1.25), ("PgUp", 1)],
        [("⇧", 1.25), ("<", 1)] + [(c, 1) for c in "YXCVBNM,.-"] + [("⇧", 1.75), ("↑", 1), ("PgDn", 1)],
        _KB_ROW4,
    ],
    "ansi": [
        [("Esc", 1)] + _KB_ROW0[1:11] + [("-", 1), ("=", 1), ("⌫", 2), ("Ins", 1)],
        [("Tab", 1.5)] + [(c, 1) for c in "QWERTYUIOP[]"] + [("\\", 1.5), ("Del", 1)],
        [("Caps", 1.75)] + [(c, 1) for c in "ASDFGHJKL;'"] + [("Enter", 2.25), ("PgUp", 1)],
        [("⇧", 2.25)] + [(c, 1) for c in "ZXCVBNM,./"] + [("⇧", 1.75), ("↑", 1), ("PgDn", 1)],
        _KB_ROW4,
    ],
}
KB_UNIT, KB_GAP, KB_PAD = 36, 4, 18          # px per unit, gap between keys, frame around the keys
KB_W = round(16 * KB_UNIT + 2 * KB_PAD)      # 612
KB_H = round(5 * KB_UNIT + 2 * KB_PAD)       # 216


def keyboard_glow(k: "squire.KeyboardSettings", light: Optional[dict] = None) -> tuple[str, float]:
    """Fill and opacity of the glow under the keys. The keyboard cannot report its colour: with `light` (what Squire
    saved, or an edit not sent yet) the glow shows it, otherwise a lit effect is a neutral glow."""
    if k.light_mode in (None, "off"):
        return "none", 0.0
    if same_effect(light, k.light_mode):
        if light["brightness"] == 0 or light["effect"] == squire.KEYBOARD_OFF:
            return "none", 0.0
        opacity = round(0.2 + light["brightness"] / 100 * 0.4, 2)
        if light["effect"] in squire.KEYBOARD_COLOURLESS:
            return "url(#kb-cycle)", opacity
        return light["color"], opacity
    return "#cfd6e0", 0.22


def keyboard_gradient_stops(light: Optional[dict]) -> list[tuple[float, str]]:
    """Colour stops for the glow of an effect without one colour: the gradient sent (rainbow, ripple), quicksand's
    scale, or a plain spectrum (colour cycle). A leftward direction mirrors the gradient."""
    if light and light["effect"] in squire.KEYBOARD_GRADIENT_EFFECTS:
        try:
            gradient = squire.keyboard_gradient_stops(light.get("gradient"))
        except ValueError:
            gradient = squire.KEYBOARD_GRADIENTS["spectrum"]
        stops = [(pos / 100, "#" + rgb) for pos, rgb in gradient]
        direction = squire.KEYBOARD_DIRECTION_ALIASES.get(light.get("direction"), light.get("direction"))
        if direction in ("left", "up-left", "down-left"):
            stops = [(round(1 - pos, 2), color) for pos, color in reversed(stops)]
        return stops
    if light and light["effect"] == "quicksand":
        scale = squire.KEYBOARD_QUICKSAND_SCALE
        return [(round(i / (len(scale) - 1), 2), "#" + rgb) for i, rgb in enumerate(scale)]
    return [(0, "#ff5a5a"), (0.5, "#3ccf7a"), (1, "#3d8bff")]


def kb_layout(k: "squire.KeyboardSettings") -> str:
    return "ansi" if k.iso is False else "iso"


def kb_key_style(color: Optional[str]) -> tuple[str, str]:
    """(key attribute, legend class) for a key lit in `color` by per-key colours; black or None is an unlit key."""
    if not color or color == "#000000":
        return "", ""
    r, g, b = bytes.fromhex(color[1:])
    light = (0.299 * r + 0.587 * g + 0.114 * b) > 140             # light key: dark legend, else white
    return f' style="fill:{color}"', " on-light" if light else " on-color"


def kb_key_at(k: "squire.KeyboardSettings", x: float, y: float) -> Optional[int]:
    """The key id under (x, y) in drawing coordinates (keyboard_svg), or None between keys."""
    rows, ids = KEYBOARD_ROWS[kb_layout(k)], squire.KEYBOARD_KEY_IDS[kb_layout(k)]
    u, g, pad = KB_UNIT, KB_GAP, KB_PAD
    r = int((y - pad) // u)
    if not 0 <= r < len(rows) or (y - pad) - r * u > u - g:
        return None
    left = pad
    for i, (_legend, width) in enumerate(rows[r]):
        if left <= x < left + width * u - g:
            return ids[r][i]
        left += width * u
    return None


def keyboard_svg(k: "squire.KeyboardSettings", light: Optional[dict] = None, keys: Optional[dict] = None) -> str:
    """Top view of the keyboard, glowing in the lighting colour when Squire knows it (keyboard_glow), or with each
    key in its own colour (`keys` = {key id: "#rrggbb"}, per-key colours)."""
    from xml.sax.saxutils import escape
    rows = KEYBOARD_ROWS[kb_layout(k)]
    ids = squire.KEYBOARD_KEY_IDS[kb_layout(k)]
    fill, opacity = keyboard_glow(k, light) if keys is None else ("none", 0.0)
    stops = "".join(f'<stop offset="{pos}" stop-color="{color}"/>' for pos, color in keyboard_gradient_stops(light))
    u, g, pad = KB_UNIT, KB_GAP, KB_PAD
    shapes = []
    for r, row in enumerate(rows):
        x = pad
        for i, (legend, width) in enumerate(row):
            y, w, h = pad + r * u, width * u - g, u - g
            style, on = kb_key_style(keys.get(ids[r][i])) if keys is not None else ("", "")
            if legend == "ISO-ENTER":
                # one key over two rows: 1.5 units on top, 1.25 below (right-aligned)
                x2 = x + 0.25 * u
                d = (f"M{x + 3} {y} H{x + w - 3} Q{x + w} {y} {x + w} {y + 3} V{y + 2 * u - g - 3} "
                     f"Q{x + w} {y + 2 * u - g} {x + w - 3} {y + 2 * u - g} H{x2 + 3} Q{x2} {y + 2 * u - g} {x2} "
                     f"{y + 2 * u - g - 3} V{y + h} H{x + 3} Q{x} {y + h} {x} {y + h - 3} V{y + 3} Q{x} {y} {x + 3} {y} Z")
                shapes.append(f'<path d="{d}" class="key"{style}/>')
                shapes.append(f'<text x="{x + w / 2 + 4}" y="{y + u - 2}" class="legend{on}">↵</text>')
            elif legend != "ISO-ENTER-LOW":
                shapes.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" class="key"{style}/>')
                if legend:
                    size = " small" if len(legend) > 2 else ""
                    shapes.append(f'<text x="{x + w / 2}" y="{y + h / 2 + 4}" class="legend{size}{on}">'
                                  f'{escape(legend)}</text>')
            x += width * u
    glow = (f'<rect x="{pad - 4}" y="{pad - 4}" width="{16 * u - g + 8}" height="{5 * u - g + 8}" rx="8" '
            f'fill="{fill}" opacity="{opacity}" filter="url(#kb-glow)"/>') if opacity else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{KB_W}" height="{KB_H}" viewBox="0 0 {KB_W} {KB_H}">


<defs>
<filter id="kb-glow" x="-10%" y="-20%" width="120%" height="140%"><feGaussianBlur stdDeviation="7"/></filter>
<linearGradient id="kb-cycle" x1="0" y1="0" x2="1" y2="0">{stops}</linearGradient>
<linearGradient id="kb-body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2f37"/><stop offset="1" stop-color="#1c2026"/></linearGradient>
<style>
.key {{ fill: #14171b; stroke: #3a404a; stroke-width: 1; }}
.legend {{ fill: #9aa3ae; font: 11px sans-serif; text-anchor: middle; }}
.legend.small {{ font-size: 8px; }}
.legend.on-light {{ fill: #1c2026; }}
.legend.on-color {{ fill: #ffffff; }}
</style>
</defs>
<rect x="1" y="1" width="{KB_W - 2}" height="{KB_H - 2}" rx="12" fill="url(#kb-body)" stroke="#3a404a" stroke-width="1.5"/>
<rect x="5" y="{pad + u}" width="4" height="{3 * u}" rx="2" fill="#3a404a"/>
{glow}
{chr(10).join(shapes)}
</svg>"""
