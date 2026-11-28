"""Top view of the mouse (own neutral artwork, no ASUS marks) and the stage layout around it: hotspots, callouts,
connector lines, chips. No GTK."""
from typing import Optional

import squire


# Stage layout in px (same as the design proposal): drawing, hotspots, callouts, connector lines.
STAGE_W, STAGE_H = 660, 660
DRAWING = (230, 110, 200, 370)
# Only what is visible from above gets a hotspot. The DPI button is on the underside of the Keris II Origin,
# so DPI is a chip below the drawing, next to sensor and power.
HOTSPOTS = {"wheel": (314, 148, 32, 58), "logo": (296, 322, 68, 68), "side": (226, 278, 32, 140)}
CALLOUTS = {"wheel": (470, 152), "logo": (470, 332), "side": (48, 324)}
LINES = {"wheel": (346, 176, 124), "logo": (364, 356, 106), "side": (198, 348, 28)}
CALLOUT_TITLES = {"wheel": "Scroll wheel", "logo": "Logo", "side": "Side"}
CHIPS = (("dpi", "DPI"), ("sensor", "Sensor"), ("power", "Power"))


def _led_paint(led: Optional[dict], gradient_id: str) -> tuple[str, float]:
    """SVG fill and opacity for a zone, as the mouse would roughly look."""
    if not led or led["mode"] == "off" or led["brightness"] == 0:
        return "none", 0.0
    opacity = round(0.35 + led["brightness"] / 100 * 0.65, 2)
    if led["mode"] in ("color-cycle", "rainbow"):
        return f"url(#{gradient_id})", opacity
    if led["mode"] == "battery":
        return "#3ccf7a", opacity
    return led["color"], opacity


# The Squire shield with a blade (same shape as linux/icons/squire-gear-symbolic.svg, 16x16): the logo zone.
SHIELD = ("M8 0.8 L14.2 3 V7.8 C14.2 11.4 11.6 13.9 8 15.2 C4.4 13.9 1.8 11.4 1.8 7.8 V3 Z "
          "M8 3 L7.1 4.4 V9.2 H5.4 V10.4 H7.1 V12.4 H8.9 V10.4 H10.6 V9.2 H8.9 V4.4 Z")


def mouse_svg(s: squire.MouseSettings) -> str:
    """Top view of the mouse (own neutral artwork, no ASUS marks) with the three light zones."""
    wheel, wheel_o = _led_paint(s.leds.get("wheel"), "cycle")
    logo, logo_o = _led_paint(s.leds.get("logo"), "cycle")
    side, side_o = _led_paint(s.leds.get("side"), "cycle-v")
    body = ("M100 12 C 150 12 182 44 188 114 C 194 184 196 254 186 304 C 176 344 140 358 100 358 "
            "C 60 358 24 344 14 304 C 4 254 6 184 12 114 C 18 44 50 12 100 12 Z")
    strip = "M13 178 C 7 220 7 262 15 300"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="200" height="370" viewBox="0 0 200 370">


<defs>
<filter id="glow" x="-60%" y="-20%" width="220%" height="140%"><feGaussianBlur stdDeviation="6"/></filter>
<filter id="logo-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.6"/></filter>
<linearGradient id="body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2f37"/><stop offset="1" stop-color="#1c2026"/></linearGradient>
<linearGradient id="cycle" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff5a5a"/><stop offset="0.5" stop-color="#3ccf7a"/><stop offset="1" stop-color="#3d8bff"/></linearGradient>
<linearGradient id="cycle-v" x1="0" y1="178" x2="0" y2="300" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#ff5a5a"/><stop offset="0.5" stop-color="#3ccf7a"/><stop offset="1" stop-color="#3d8bff"/></linearGradient>
</defs>
<path d="{body}" fill="url(#body)" stroke="#3a404a" stroke-width="1.5"/>
<path d="{strip}" fill="none" stroke="{side}" stroke-width="12" stroke-linecap="round" opacity="{side_o}" filter="url(#glow)"/>
<path d="{strip}" fill="none" stroke="{side}" stroke-width="4" stroke-linecap="round" opacity="{side_o}"/>
<path d="M100 14 V 134" stroke="#0f1115" stroke-width="2"/>
<path d="M14 146 C 60 158 140 158 186 146" fill="none" stroke="#0f1115" stroke-width="2"/>
<rect x="89" y="46" width="22" height="46" rx="11" fill="#14171b" stroke="#3a404a"/>
<rect x="93" y="52" width="14" height="34" rx="7" fill="{wheel}" opacity="{wheel_o}"/>
<g transform="translate(76 222) scale(3)">
<path d="{SHIELD}" fill="{logo}" opacity="{round(logo_o * 0.7, 2)}" filter="url(#logo-glow)"/>
<path d="{SHIELD}" fill="#14171b" fill-rule="evenodd" stroke="#3a404a" stroke-width="0.35"/>
<path d="{SHIELD}" fill="{logo}" fill-rule="evenodd" opacity="{logo_o}"/>
</g>
</svg>"""
