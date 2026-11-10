"""Tests for the Linux settings window's logic (linux/squire_settings.py). No GTK and no hardware needed."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "linux"))
import squire_settings as ui  # noqa: E402
import squire_tray as tray  # noqa: E402
import squire  # noqa: E402


class DemoDataTests(unittest.TestCase):
    """--demo shows the real captures from protocol.json."""

    def setUp(self):
        self.batteries, self.s = ui.demo_data()

    def test_values_from_the_captures(self):
        s = self.s
        self.assertEqual((s.status, s.profile, s.dpi_stage, s.dpi_stage_count), ("ok", 1, 4, 4))
        self.assertEqual(s.dpi, [400, 800, 650, 3200])
        self.assertEqual(s.leds["side"]["mode"], "color-cycle")
        self.assertEqual((s.power_off_minutes, s.low_warning), (3, 20))

    def test_summaries(self):
        self.assertEqual(ui.zone_summary(self.s, "dpi"), "400 / 800 / 650 / 3200 DPI")
        self.assertEqual(ui.zone_summary(self.s, "logo"), "Color cycle · 50 %")
        self.assertEqual(ui.zone_summary(self.s, "sensor"), "1000 Hz · lift-off low")
        self.assertEqual(ui.zone_summary(self.s, "power"), "off after 3 min · warn at 20 %")


class DrawingTests(unittest.TestCase):
    def settings(self, **leds):
        s = squire.MouseSettings("m", "omni")
        s.leds = {zone: {"mode": mode, "brightness": 50, "color": "#3d8bff"} for zone, mode in leds.items()}
        return s

    def test_svg_is_valid_xml_at_the_layout_size(self):
        root = ET.fromstring(ui.mouse_svg(self.settings(logo="static", wheel="off", side="color-cycle")))
        self.assertEqual((root.get("width"), root.get("height")), (str(ui.DRAWING[2]), str(ui.DRAWING[3])))

    def test_logo_is_the_squire_shield_in_the_led_colour(self):
        svg = ui.mouse_svg(self.settings(logo="static"))
        self.assertIn(ui.SHIELD, svg)
        self.assertIn('fill="#3d8bff" fill-rule="evenodd"', svg)
        icon = (Path(ui.__file__).parent / "icons" / "squire-gear-symbolic.svg").read_text()
        self.assertEqual(" ".join(ui.SHIELD.split()), " ".join(icon.split('d="')[1].split('"')[0].split()))

    def test_zone_paint(self):
        self.assertEqual(ui._led_paint({"mode": "static", "brightness": 100, "color": "#ff0000"}, "g"), ("#ff0000", 1.0))
        self.assertEqual(ui._led_paint({"mode": "off", "brightness": 100, "color": "#ff0000"}, "g"), ("none", 0.0))
        self.assertEqual(ui._led_paint({"mode": "breathing", "brightness": 0, "color": "#ff0000"}, "g")[1], 0.0)
        self.assertEqual(ui._led_paint({"mode": "rainbow", "brightness": 50, "color": "#ff0000"}, "g")[0], "url(#g)")
        self.assertEqual(ui._led_paint(None, "g"), ("none", 0.0))

    def test_color_only_for_one_colour_effects(self):
        self.assertTrue(all(ui.shows_color(m) for m in ("static", "breathing", "react")))
        self.assertFalse(any(ui.shows_color(m) for m in ("color-cycle", "rainbow", "battery", "off")))

    def test_swatch_for_no_light_is_an_outline(self):
        self.assertIn('fill="none"', ui.swatch_svg("#000000"))
        self.assertIn('fill="#31ff00"', ui.swatch_svg("#31ff00"))

    def test_light_zones_are_on_the_drawing_the_rest_are_chips(self):
        zones = set(squire.LED_ZONES)
        self.assertEqual(set(ui.HOTSPOTS), zones)
        self.assertEqual(set(ui.CALLOUTS), zones)
        self.assertEqual(set(ui.LINES), zones)
        # the DPI button is on the underside: no hotspot on the top view
        self.assertEqual([zone for zone, _ in ui.CHIPS], ["dpi", "sensor", "power"])
        self.assertTrue(all(zone in ui.ZONE_TITLES for zone in set(ui.HOTSPOTS) | {z for z, _ in ui.CHIPS}))


class TrayLinkTests(unittest.TestCase):
    def test_tray_opens_the_settings_script_next_to_it(self):
        command = tray.settings_command()
        self.assertEqual(Path(command[1]).name, "squire_settings.py")
        self.assertTrue(Path(command[1]).is_file())


if __name__ == "__main__":
    unittest.main()
