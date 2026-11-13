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


class EditTests(unittest.TestCase):
    def setUp(self):
        _, self.s = ui.demo_data()       # logo: colour cycle 50 %, #ff0000

    def test_all_light_zones_are_editable(self):
        self.assertEqual(set(ui.EDITABLE_ZONES), set(squire.LED_ZONES))
        self.assertNotIn("rainbow", ui.EDIT_MODES)

    def test_no_pending_change_means_nothing_to_send(self):
        self.assertEqual(ui.changed_zones(self.s, {}), [])
        self.assertEqual(ui.planned_packets(self.s, {}), [])
        same = {"logo": dict(self.s.leds["logo"])}
        self.assertEqual(ui.changed_zones(self.s, same), [])
        self.assertIn("Nothing is sent", ui.apply_status(self.s, same))

    def test_pending_change_previews_and_plans_exact_bytes(self):
        pending = {"logo": {"mode": "static", "brightness": 100, "color": "#3d8bff"}}
        preview = ui.preview_settings(self.s, pending)
        self.assertEqual(preview.leds["logo"]["mode"], "static")
        self.assertEqual(self.s.leds["logo"]["mode"], "color-cycle")     # original untouched
        self.assertEqual(ui.zone_summary(preview, "logo"), "Static · 100 %")
        self.assertEqual(ui.planned_packets(self.s, pending),
                         ["03 51 28 00 00 00 64 3d 8b ff 00 00 00 …", "03 50 03 …  (save)"])
        self.assertIn("Logo light", ui.apply_status(self.s, pending))

    def test_zone_byte_for_wheel_and_side(self):
        pending = {"wheel": {"mode": "static", "brightness": 100, "color": "#3d8bff"},
                   "side": {"mode": "off", "brightness": 50, "color": "#0000ff"}}
        lines = ui.planned_packets(self.s, pending)
        self.assertEqual(lines[0], "03 51 28 01 00 00 64 3d 8b ff 00 00 00 …")
        self.assertEqual(lines[1], "03 51 28 02 00 f0 32 00 00 ff 00 00 00 …")
        self.assertEqual(lines[2], "03 50 03 …  (save)")
        self.assertEqual(len(lines), 3)    # one save for all changed zones

    def test_edit_one_zone_and_back(self):
        pending = ui.edit_pending(self.s, {}, "logo", mode="static")
        self.assertEqual(ui.changed_zones(self.s, pending), ["logo"])
        self.assertEqual(ui.edit_pending(self.s, pending, "logo", mode="color-cycle"), {})

    def test_linked_edit_sets_every_zone_and_saves_once(self):
        pending = ui.edit_pending(self.s, {}, "logo", linked=True, mode="static", brightness=100, color="#3d8bff")
        self.assertEqual(sorted(ui.changed_zones(self.s, pending)), ["logo", "side", "wheel"])
        for zone in ("logo", "wheel", "side"):
            self.assertEqual(pending[zone], {"mode": "static", "brightness": 100, "color": "#3d8bff"})
        lines = ui.planned_packets(self.s, pending)
        self.assertEqual(sorted(line[:11] for line in lines[:3]), ["03 51 28 00", "03 51 28 01", "03 51 28 02"])
        self.assertEqual(lines[3], "03 50 03 …  (save)")
        self.assertEqual(len(lines), 4)

    def test_linking_copies_the_zone_and_skips_zones_that_already_match(self):
        import dataclasses
        logo = {"mode": "static", "brightness": 80, "color": "#f78013"}
        s = dataclasses.replace(self.s, leds={**self.s.leds, "logo": logo, "side": dict(logo)})   # wheel: colour cycle
        pending = ui.edit_pending(s, {}, "logo", linked=True)       # the switch turned on, no other change
        self.assertEqual(ui.changed_zones(s, pending), ["wheel"])     # side has it already, logo is unchanged
        self.assertEqual(pending["wheel"], logo)

    def test_effect_squire_cannot_write_is_not_linked(self):
        import dataclasses
        s = dataclasses.replace(self.s, leds={**self.s.leds, "logo": {"mode": "rainbow", "brightness": 50,
                                                                       "color": "#ff0000"}})
        self.assertFalse(ui.can_link(s, {}, "logo"))
        self.assertTrue(ui.can_link(s, {"logo": {"mode": "static", "brightness": 50, "color": "#ff0000"}}, "logo"))
        self.assertTrue(ui.can_link(self.s, {}, "wheel"))

    def test_power_and_sensor_edits_plan_exact_bytes(self):
        # demo: off after 3 min, warning 20 %, snapping off, tuning 0°
        pending = ui.edit_other(self.s, {}, "power", {"power_off_minutes": 10, "low_warning": 30})
        pending = ui.edit_other(self.s, pending, "angle_snapping", True)
        pending = ui.edit_other(self.s, pending, "angle_tuning", -5)
        pending = ui.edit_pending(self.s, pending, "logo", mode="static", brightness=100, color="#3d8bff")
        self.assertEqual(ui.changes(self.s, pending), ["logo", "power", "angle_snapping", "angle_tuning"])
        self.assertEqual(ui.planned_packets(self.s, pending),
                         ["03 51 28 00 00 00 64 3d 8b ff 00 00 00 …", "03 51 37 00 00 04 00 1e …",
                          "03 51 31 06 00 01 …", "03 51 31 0b 00 fb ff …", "03 50 03 …  (save)"])
        self.assertIn("Power, Angle snapping, Angle tuning", ui.apply_status(self.s, pending))
        preview = ui.preview_settings(self.s, pending)
        self.assertEqual((preview.power_off_minutes, preview.low_warning, preview.angle_snapping, preview.angle_tuning),
                         (10, 30, True, -5))
        self.assertEqual(ui.zone_summary(preview, "power"), "off after 10 min · warn at 30 %")   # the chip
        self.assertEqual(self.s.power_off_minutes, 3)       # original untouched

    def test_other_edit_back_to_the_mouse_value_is_dropped(self):
        pending = ui.edit_other(self.s, {}, "angle_snapping", True)
        self.assertEqual(ui.edit_other(self.s, pending, "angle_snapping", False), {})
        pending = ui.edit_other(self.s, {}, "power", {"power_off_minutes": None, "low_warning": 20})
        self.assertEqual(ui.planned_packets(self.s, pending)[0], "03 51 37 00 00 ff 00 14 …")   # never
        self.assertEqual(ui.edit_other(self.s, pending, "power", {"power_off_minutes": 3, "low_warning": 20}), {})

    def test_power_is_shown_only_for_a_warning_squire_cannot_write(self):
        import dataclasses
        self.assertTrue(ui.can_edit_power(self.s))
        self.assertFalse(ui.can_edit_power(dataclasses.replace(self.s, low_warning=15)))
        self.assertFalse(ui.can_edit_power(dataclasses.replace(self.s, low_warning=None)))

    def test_liftoff_edit_keeps_the_surface(self):
        import dataclasses
        # demo capture: no surface (ff), lift-off low
        self.assertEqual((self.s.surface_id, self.s.liftoff), (0xFF, "low"))
        pending = ui.edit_other(self.s, {}, "liftoff", "high")
        self.assertEqual(ui.planned_packets(self.s, pending), ["03 51 35 ff 00 ff 01 …", "03 50 03 …  (save)"])
        self.assertEqual(ui.zone_summary(ui.preview_settings(self.s, pending), "sensor"), "1000 Hz · lift-off high")
        self.assertIn("Lift-off distance", ui.apply_status(self.s, pending))
        sheath = dataclasses.replace(self.s, surface_id=0x01, surface_params="1c 34 16")
        self.assertEqual(ui.planned_packets(sheath, pending)[0], "03 51 35 00 00 01 01 00 1c 34 16 …")
        self.assertEqual(ui.edit_other(self.s, pending, "liftoff", "low"), {})

    def test_liftoff_is_shown_only_for_a_surface_squire_does_not_know(self):
        import dataclasses
        self.assertTrue(ui.can_edit_liftoff(self.s))
        self.assertFalse(ui.can_edit_liftoff(dataclasses.replace(self.s, surface_id=0x02, surface_params="1c 34 16")))
        self.assertFalse(ui.can_edit_liftoff(dataclasses.replace(self.s, surface_id=None)))

    def test_polling_and_surface_plan_exact_bytes(self):
        import dataclasses
        sheath = dataclasses.replace(self.s, surface_id=0x01, surface_params="1c 34 16")
        pending = ui.edit_other(sheath, {}, "polling_hz", 500)
        pending = ui.edit_other(sheath, pending, "surface_id", 0x0B)
        pending = ui.edit_other(sheath, pending, "liftoff", "high")
        self.assertEqual(ui.changes(sheath, pending), ["polling_hz", "surface_id", "liftoff"])
        # surface first with the lift-off the mouse has, then lift-off with the new surface
        self.assertEqual(ui.planned_packets(sheath, pending),
                         ["03 51 31 04 00 02 …", "03 51 35 00 00 0b 00 00 1a 32 20 …",
                          "03 51 35 00 00 0b 01 00 1a 32 20 …", "03 50 03 …  (save)"])
        preview = ui.preview_settings(sheath, pending)
        self.assertEqual((preview.polling_hz, preview.surface_id, preview.surface_params), (500, 0x0B, "1a 32 20"))
        self.assertEqual(ui.edit_other(sheath, {}, "surface_id", 0x01), {})
        self.assertEqual(ui.planned_packets(sheath, {"surface_id": 0xFF})[0], "03 51 35 ff 00 ff 00 …")

    def test_surface_choices(self):
        import dataclasses
        self.assertEqual(ui.SURFACE_CHOICES[0], 0xFF)
        self.assertEqual(set(ui.SURFACE_CHOICES), set(squire.SURFACE_PARAMS))
        self.assertTrue(ui.can_edit_surface(self.s))
        self.assertFalse(ui.can_edit_surface(dataclasses.replace(self.s, surface_id=0x02)))

    def test_dpi_edits_plan_exact_bytes(self):
        # demo: 400 / 800 / 650 / 3200, colours #ff0000 #c100ff #000000 #31ff00, active stage 4 of 4
        pending = ui.edit_other(self.s, {}, "dpi_3", {"dpi": 700, "color": "#000000"})
        pending = ui.edit_other(self.s, pending, "dpi_1", {"dpi": 400, "color": "#3d8bff"})    # colour only
        pending = ui.edit_dpi_stages(self.s, pending, active=3)
        self.assertEqual(ui.changes(self.s, pending), ["dpi_1", "dpi_3", "dpi_stage"])
        self.assertEqual(ui.planned_packets(self.s, pending),
                         ["03 51 31 00 00 07 00 3d 8b ff …", "03 51 31 02 00 0d 00 00 00 00 …",
                          "03 51 31 09 00 03 …", "03 50 03 …  (save)"])
        preview = ui.preview_settings(self.s, pending)
        self.assertEqual((preview.dpi, preview.dpi_stage, preview.dpi_colors[0]), ([400, 800, 700, 3200], 3, "#3d8bff"))
        self.assertEqual(ui.zone_summary(preview, "dpi"), "400 / 800 / 700 / 3200 DPI")
        self.assertEqual(self.s.dpi[2], 650)                                    # original untouched
        self.assertEqual(ui.edit_other(self.s, {}, "dpi_2", {"dpi": 800, "color": "#c100ff"}), {})

    def test_active_stage_and_count_stay_consistent(self):
        # fewer stages than the active one: the active stage follows, and is written first
        pending = ui.edit_dpi_stages(self.s, {}, count=2)
        self.assertEqual((pending["dpi_stage"], pending["dpi_stage_count"]), (2, 2))
        self.assertEqual(ui.planned_packets(self.s, pending)[:2], ["03 51 31 09 00 02 …", "03 51 31 0a 00 02 …"])
        # from 2 stages, choosing stage 4 raises the count, which is written first
        import dataclasses
        two = dataclasses.replace(self.s, dpi_stage=2, dpi_stage_count=2)
        pending = ui.edit_dpi_stages(two, {}, active=4)
        self.assertEqual(ui.planned_packets(two, pending)[:2], ["03 51 31 0a 00 04 …", "03 51 31 09 00 04 …"])
        self.assertEqual(ui.edit_dpi_stages(two, pending, active=2, count=2), {})

    def test_stage_with_separate_x_y_is_shown_only(self):
        import dataclasses
        self.assertTrue(all(ui.can_edit_dpi(self.s, n) for n in range(1, 5)))
        odd = dataclasses.replace(self.s, dpi_xy=[[400, 400], [800, 1200], [650, 650], [3200, 3200]])
        self.assertFalse(ui.can_edit_dpi(odd, 2))

    def test_wired_mouse_plans_unnumbered_packets(self):
        import dataclasses
        wired = dataclasses.replace(self.s, connection="wired")
        lines = ui.planned_packets(wired, {"logo": {"mode": "off", "brightness": 50, "color": "#ff0000"}})
        self.assertEqual(lines[0][:17], "00 51 28 00 00 f0")
        self.assertEqual(lines[1][:5], "00 50")


class TrayLinkTests(unittest.TestCase):
    def test_tray_opens_the_settings_script_next_to_it(self):
        command = tray.settings_command()
        self.assertEqual(Path(command[1]).name, "squire_settings.py")
        self.assertTrue(Path(command[1]).is_file())


if __name__ == "__main__":
    unittest.main()


class KeyboardPageTests(unittest.TestCase):
    """The read-only keyboard page (no GTK needed)."""

    def values(self, k):
        return {name: value for _, rows in ui.keyboard_groups(k) for name, value, _ in rows}

    def test_demo_shows_the_captured_profile_1(self):
        v = self.values(ui.demo_keyboard())
        self.assertEqual(v["Active profile"], "1 of 5")
        self.assertEqual(v["Effect"], "Reactive")
        self.assertEqual(v["Layout"], "CH-DE · ISO")
        self.assertEqual(v["Low-battery warning"], "20 %")
        self.assertEqual(v["Receiver firmware"], "07.00.08")

    def test_effect_names(self):
        self.assertEqual(ui.keyboard_effect_name("starry-night"), "Starry night")
        self.assertEqual(ui.keyboard_effect_name("host-driven"), "Controlled by an app")
        self.assertEqual(ui.keyboard_effect_name("current"), "Current")
        self.assertEqual(ui.keyboard_effect_name(None), "–")

    def test_unknown_layout_and_missing_values(self):
        k = squire.KeyboardSettings("k", "wired", layout_id=0x63, iso=False)
        v = self.values(k)
        self.assertEqual(v["Layout"], "Unknown (id 99) · ANSI")
        self.assertEqual(v["Active profile"], "–")
        self.assertEqual(v["Connection"], "USB cable")
        self.assertNotIn("Receiver firmware", v)

    def test_every_zone_has_a_group_and_a_summary(self):
        k = ui.demo_keyboard()
        groups = dict(ui.keyboard_groups(k))
        for zone, title in ui.KB_ZONE_TITLES.items():
            self.assertIn(title, groups)
            self.assertTrue(ui.keyboard_zone_summary(k, zone))
        self.assertEqual(ui.keyboard_zone_summary(k, "kb_layout"), "CH-DE · ISO")


class KeyboardDrawingTests(unittest.TestCase):
    def keys(self, svg):
        root = ET.fromstring(svg)     # also checks that it is valid XML
        ns = "{http://www.w3.org/2000/svg}"
        return [t.text for t in root.iter(ns + "text")], root

    def test_iso_rows_are_16_units_with_the_extra_key(self):
        for name, rows in ui.KEYBOARD_ROWS.items():
            for i, row in enumerate(rows):
                with self.subTest(layout=name, row=i):
                    self.assertAlmostEqual(sum(w for _, w in row), 16)
        legends, _ = self.keys(ui.keyboard_svg(ui.demo_keyboard()))
        self.assertIn("<", legends)          # the ISO key next to left shift
        self.assertIn("Z", legends)          # CH-DE: Z where US has Y
        self.assertIn("↵", legends)          # the tall ISO Enter

    def test_ansi_shape_for_an_ansi_layout(self):
        k = squire.KeyboardSettings("k", "omni", layout_id=17, layout="US", iso=False, light_mode="static")
        legends, _ = self.keys(ui.keyboard_svg(k))
        self.assertNotIn("<", legends)
        self.assertIn("Enter", legends)

    def test_glow_only_when_lit(self):
        lit = ui.keyboard_svg(squire.KeyboardSettings("k", "omni", light_mode="reactive"))
        dark = ui.keyboard_svg(squire.KeyboardSettings("k", "omni"))
        self.assertIn("kb-glow)", lit)
        self.assertNotIn("kb-glow)", dark)
