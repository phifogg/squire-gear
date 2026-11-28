"""Tests for the Linux settings window's logic (linux/squire_settings.py). No GTK and no hardware needed."""
import dataclasses
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


class BluetoothPageTests(unittest.TestCase):
    def test_page_only_for_a_bluetooth_level(self):
        ble = squire.Battery("keyboard", "ROG Falchion RX LP", "bluetooth", 58)
        title, text = ui.bluetooth_status(ble, "keyboard")
        self.assertEqual(title, "The keyboard is connected over Bluetooth")
        self.assertIn("Omni receiver or the USB cable", text)
        self.assertIsNone(ui.bluetooth_status(None, "keyboard"))
        self.assertIsNone(ui.bluetooth_status(squire.Battery("keyboard", "k", "omni", 58), "keyboard"))
        self.assertIsNone(ui.bluetooth_status(squire.Battery("keyboard", "k", "bluetooth", None, status="asleep"),
                                              "keyboard"))


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


class KeyboardEditTests(unittest.TestCase):
    """The keyboard page's editing logic: lighting, sleep, warning; colour known only from what Squire saved."""

    def kb(self, effect="raindrop", sleep_code=0x02, warn=20, profile=4):
        k = squire.KeyboardSettings("ROG Falchion RX LP", "omni")
        reply = bytearray(63)
        reply[0:2], reply[6], reply[9], reply[10] = b"\x12\x01", sleep_code, warn, 70
        squire.parse_keyboard_settings(k, "battery", bytes(reply))
        k.raw["battery"] = bytes(reply).hex(" ")
        k.light_mode, k.profile = effect, profile
        return k

    def test_unwritable_effect_starts_from_static_and_any_edit_is_pending(self):
        k = self.kb("host-driven")
        self.assertEqual(ui.kb_light_start(k, None),
                         {"effect": "static", "brightness": 100, "color": "#ffffff", "speed": "none",
                          "gradient": None, "direction": None, "thickness": None, "mode": None, "color2": None, "background": None})
        pending = ui.kb_edit_light(k, {}, None, effect="breathing")
        self.assertEqual(pending["kb_light"]["speed"], "normal")             # a new effect: its default speed
        self.assertEqual(ui.kb_planned_packets(k, pending),
                         ["02 51 2c 01 00 1e 64 00 ff ff ff ff ff …", "02 50 55 …  (save)"])

    def test_known_light_drops_an_edit_that_is_back_to_it(self):
        k = self.kb("static")
        known = {"effect": "static", "brightness": 50, "color": "#ff0000", "speed": "none"}
        pending = ui.kb_edit_light(k, {}, known, brightness=80)
        self.assertIn("kb_light", pending)
        self.assertEqual(ui.kb_edit_light(k, pending, known, brightness=50), {})
        # without knowledge the same values stay pending: the keyboard cannot be asked
        self.assertIn("kb_light", ui.kb_edit_light(k, {}, None, color="#ff0000", brightness=50))

    def test_colour_cycle_sends_no_colour_but_keeps_the_choice(self):
        k = self.kb("static")
        pending = ui.kb_edit_light(k, {}, None, color="#3d8bff")
        pending = ui.kb_edit_light(k, pending, None, effect="color-cycle")
        self.assertEqual(ui.kb_planned_packets(k, pending)[0], "02 51 2c 02 00 1f 64 00 ff ff 00 00 00 …")
        pending = ui.kb_edit_light(k, pending, None, effect="static")
        self.assertEqual(pending["kb_light"]["color"], "#3d8bff")

    def test_power_edits_and_apply_order(self):
        k = self.kb()
        pending = ui.kb_edit(k, {}, "kb_sleep", 5)
        self.assertEqual(ui.kb_edit(k, pending, "kb_sleep", 3), {})          # back to what the keyboard has
        pending = ui.kb_edit_light(k, ui.kb_edit(k, pending, "kb_warning", 10), None, effect="static")
        self.assertEqual(ui.kb_changes(pending), ["kb_sleep", "kb_warning", "kb_light"])
        lines = ui.kb_planned_packets(k, pending)
        self.assertEqual([line[:17] for line in lines],
                         ["02 51 38 00 00 03", "02 51 3a 00 00 0a", "02 51 2c 00 00 ff", "02 50 55 …  (save"])
        wired = dataclasses.replace(k, connection="wired")
        self.assertTrue(ui.kb_planned_packets(wired, pending)[0].startswith("00 51 38"))
        self.assertIn("Sleep, Low-battery warning, Lighting", ui.kb_apply_status(pending))

    def test_preview(self):
        k = self.kb("raindrop")
        pending = ui.kb_edit_light(k, ui.kb_edit(k, {}, "kb_sleep", None), None, effect="static", brightness=40)
        p, light = ui.kb_preview(k, pending, None)
        self.assertEqual((p.light_mode, p.sleep_minutes, light["brightness"]), ("static", None, 40))
        self.assertEqual(ui.keyboard_zone_summary(p, "kb_light", light), "Static · 40 %")
        self.assertEqual(ui.keyboard_zone_summary(p, "kb_power", light), "never sleeps · warn at 20 %")
        self.assertEqual(k.light_mode, "raindrop")                            # the read values stay untouched

    def test_glow_shows_a_known_colour_only_for_its_effect(self):
        k = self.kb("static")
        light = {"effect": "static", "brightness": 100, "color": "#ff0000", "speed": "none"}
        self.assertEqual(ui.keyboard_glow(k, light), ("#ff0000", 0.6))
        self.assertEqual(ui.keyboard_glow(self.kb("raindrop"), light), ("#cfd6e0", 0.22))   # stale: neutral
        self.assertEqual(ui.keyboard_glow(k, {**light, "brightness": 0}), ("none", 0.0))
        self.assertEqual(ui.keyboard_glow(self.kb("color-cycle"), {**light, "effect": "color-cycle"})[0], "url(#kb-cycle)")
        ET.fromstring(ui.keyboard_svg(k, light))

    def test_gradient_edits(self):
        k = self.kb("static")
        pending = ui.kb_edit_light(k, {}, None, color="#3d8bff")
        pending = ui.kb_edit_light(k, pending, None, effect="rainbow")
        light = pending["kb_light"]
        self.assertEqual((light["gradient"], light["direction"], light["speed"], light["color"]),
                         ("spectrum", "right", "normal", "#3d8bff"))      # the colour is kept for later
        pending = ui.kb_edit_light(k, pending, None, gradient="sunset", direction="right-to-left")
        self.assertEqual([line[:20] for line in ui.kb_planned_packets(k, pending)],
                         ["02 51 2c 04 02 3e 64", "02 51 2c 04 01 64 f9", "02 51 2c 04 00 …", "02 50 55 …  (save)"])
        pending = ui.kb_edit_light(k, pending, None, effect="ripple")           # ripple has no direction
        self.assertEqual((pending["kb_light"]["direction"], pending["kb_light"]["gradient"]), ("none", "sunset"))
        stops = ui.keyboard_gradient_stops({"effect": "rainbow", "gradient": "sunset", "direction": "right-to-left"})
        self.assertEqual(stops, [(0.0, "#f91d1d"), (0.43, "#fe3f7d"), (0.86, "#febe3f")])
        self.assertEqual(len(ui.keyboard_gradient_stops({"effect": "quicksand"})), 6)
        p, light = ui.kb_preview(k, pending, None)
        self.assertEqual(ui.keyboard_glow(p, light)[0], "url(#kb-cycle)")
        ET.fromstring(ui.keyboard_svg(p, light))

    def test_power_is_editable_only_with_values_squire_can_write_back(self):
        self.assertTrue(ui.can_edit_kb_sleep(self.kb(sleep_code=0xFF)))      # never
        self.assertFalse(ui.can_edit_kb_sleep(self.kb(sleep_code=0x07)))     # unknown code, also parsed as None
        self.assertTrue(ui.can_edit_kb_warning(self.kb(warn=50)))
        self.assertFalse(ui.can_edit_kb_warning(self.kb(warn=25)))


    def test_off_is_a_black_static_and_matches_the_static_read(self):
        k = self.kb("reactive")
        pending = ui.kb_edit_light(k, {}, None, effect="off")
        self.assertEqual(ui.kb_planned_packets(k, pending),
                         ["02 51 2c 00 00 ff 04 00 ff ff 00 00 00 …", "02 50 55 …  (save)"])
        p, light = ui.kb_preview(k, pending, None)
        self.assertEqual(ui.keyboard_glow(p, light), ("none", 0.0))
        known = squire.keyboard_light_value("off", 4)
        static = self.kb("static")
        self.assertEqual(ui.keyboard_zone_summary(static, "kb_light", known), "Off")
        self.assertEqual(ui.kb_edit_light(static, {}, known, effect="off"), {})      # nothing to send
        pending = ui.kb_edit_light(static, {}, known, effect="static")
        self.assertEqual(pending["kb_light"]["brightness"], 100)                     # not off's 4 %

    def test_colour_modes_second_colour_and_background(self):
        k = self.kb("raindrop")
        pending = ui.kb_edit_light(k, {}, None, effect="current", color="#ff0000", mode="double")
        self.assertEqual(ui.kb_planned_packets(k, pending)[0],
                         "02 51 2c 08 00 0a 64 10 ff ff ff 00 00 ff 00 ff 00 00 00 …")
        pending = ui.kb_edit_light(k, pending, None, background="#1bff00", color2="#00ff00")
        self.assertEqual(ui.kb_planned_packets(k, pending)[0],
                         "02 51 2c 08 00 0a 64 10 ff ff ff 00 00 00 ff 00 1b ff 00 …")
        pending = ui.kb_edit_light(k, pending, None, effect="quicksand")              # another kind of mode
        self.assertEqual((pending["kb_light"]["mode"], pending["kb_light"]["direction"]), ("scale", "down"))
        pending = ui.kb_edit_light(k, pending, None, mode="random", direction="up")
        self.assertEqual(ui.kb_planned_packets(k, pending)[0][:26], "02 51 2c 07 01 0f 64 01 06")
        pending = ui.kb_edit_light(k, pending, None, effect="starry-night")          # colours were kept
        light = pending["kb_light"]
        self.assertEqual((light["mode"], light["color2"], light["background"]), ("single", "#00ff00", "#1bff00"))

    def test_gradient_thickness_and_directions(self):
        k = self.kb("rainbow")
        pending = ui.kb_edit_light(k, {}, None, effect="rainbow", gradient="red", direction="outwards",
                                   thickness="low", speed="slow")
        self.assertEqual(ui.kb_planned_packets(k, pending)[0][:50],
                         "02 51 2c 04 02 55 64 00 08 00 02 0e ff 00 29 64 ff")
        self.assertEqual(set(ui.KB_DIRECTION_NAMES) >= set(squire.KEYBOARD_DIRECTIONS["rainbow"]), True)
        self.assertEqual(set(ui.KB_GRADIENT_NAMES), set(squire.KEYBOARD_GRADIENTS))
        mirrored = ui.keyboard_gradient_stops({"effect": "rainbow", "gradient": "blue-red", "direction": "left"})
        self.assertEqual(mirrored, [(0.0, "#ff0031"), (0.86, "#0018f0")])

    def test_warning_never(self):
        k = self.kb(warn=0)
        self.assertEqual((k.low_warning, ui.can_edit_kb_warning(k)), (0, True))
        self.assertIn("never warns", ui.keyboard_zone_summary(k, "kb_power"))
        self.assertEqual(dict(ui.keyboard_groups(k))["Power"][1][1], "Never")
        pending = ui.kb_edit(self.kb(), {}, "kb_warning", 0)
        self.assertEqual(ui.kb_planned_packets(k, pending), ["02 51 3a 00 00 00 …", "02 50 55 …  (save)"])


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
        self.assertEqual(v["Firmware"], "05.00.13")
        self.assertEqual(v["Receiver firmware"], "07.00.08")

    def test_effect_names(self):
        self.assertEqual(ui.keyboard_effect_name("starry-night"), "Starry night")
        self.assertEqual(ui.keyboard_effect_name("host-driven"), "Controlled by an app")
        self.assertEqual(ui.keyboard_effect_name("current"), "Current")
        self.assertEqual(ui.keyboard_effect_name("rainbow"), "Rainbow")
        self.assertEqual(ui.keyboard_effect_name(None), "–")

    def test_unknown_layout_and_missing_values(self):
        k = squire.KeyboardSettings("k", "wired", layout_id=0x63, iso=False)
        v = self.values(k)
        self.assertEqual(v["Layout"], "Unknown (id 99) · ANSI")
        self.assertEqual(v["Active profile"], "–")
        self.assertEqual(v["Connection"], "USB cable")
        self.assertNotIn("Receiver firmware", v)
        self.assertNotIn("Firmware", v)

    def test_every_zone_has_a_group_and_a_summary(self):
        k = ui.demo_keyboard()
        groups = dict(ui.keyboard_groups(k))
        for zone, title in ui.KB_ZONE_TITLES.items():
            self.assertIn(title, groups)
            self.assertTrue(ui.keyboard_zone_summary(k, zone))
        self.assertEqual(ui.keyboard_zone_summary(k, "kb_layout"), "CH-DE · ISO")


class PerKeyTests(unittest.TestCase):
    """Per-key colours in the keyboard page: painting, presets and what Apply sends (never saved)."""
    iso = squire.KeyboardSettings("k", "omni", layout_id=25, layout="CH-DE", iso=True, light_mode="starry-night",
                                  sleep_minutes=3, low_warning=20)

    def test_key_at_matches_the_drawing(self):
        u, pad = ui.KB_UNIT, ui.KB_PAD
        at = lambda col, row: ui.kb_key_at(self.iso, pad + col * u + 5, pad + row * u + 5)  # noqa: E731
        self.assertEqual(at(0, 0), 0x01)                     # Esc
        self.assertEqual(at(15, 0), 0x71)                    # Insert, top right (confirmed)
        self.assertEqual(at(15, 4), 0x75)                    # bottom right
        self.assertEqual(at(0, 4), 0x05)                     # left Ctrl
        self.assertEqual(ui.kb_key_at(self.iso, pad + 13.6 * u, pad + 1.5 * u), 0x6B)   # ISO Enter, top half
        self.assertEqual(ui.kb_key_at(self.iso, pad + 14.2 * u, pad + 2.5 * u), 0x6B)   # and its lower half
        self.assertIsNone(ui.kb_key_at(self.iso, 2, 2))                                 # frame
        self.assertIsNone(ui.kb_key_at(self.iso, pad + u - 2, pad + 5))                 # gap between keys
        for layout in ("iso", "ansi"):
            for r, row in enumerate(ui.KEYBOARD_ROWS[layout]):
                with self.subTest(layout=layout, row=r):
                    self.assertEqual(len(row), len(squire.KEYBOARD_KEY_IDS[layout][r]))

    def test_legends_follow_the_key_ids(self):
        root = ET.fromstring(ui.keyboard_svg(self.iso))
        legends = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")]
        self.assertEqual(legends[0], "Esc")
        for legend in ("Ins", "Del", "PgUp", "PgDn"):
            self.assertIn(legend, legends)
        self.assertNotIn("End", legends)

    def test_select_paint_and_drop(self):
        pending = ui.kb_edit_light(self.iso, {}, None, effect="static")
        pending = ui.kb_select_per_key(self.iso, pending, None, "#000000")
        self.assertNotIn("kb_light", pending)                       # one or the other is sent
        self.assertEqual(set(pending["kb_keys"].values()), {"#000000"})
        self.assertEqual(len(pending["kb_keys"]), 69)
        pending = ui.kb_paint(self.iso, pending, None, {0x01: "#FF0000"})
        self.assertEqual(pending["kb_keys"][0x01], "#ff0000")
        active = dict(pending["kb_keys"])
        # after Apply: painting back to what was sent drops the edit
        changed = ui.kb_paint(self.iso, {}, active, {0x09: "#00ff00"})
        self.assertEqual(changed["kb_keys"][0x09], "#00ff00")
        self.assertEqual(ui.kb_paint(self.iso, changed, active, {0x09: "#000000"}), {})
        self.assertEqual(ui.kb_shown_keys({}, active, True), active)
        self.assertIsNone(ui.kb_shown_keys({}, active, False))

    def test_apply_saves_first_then_sends_keys_unsaved(self):
        pending = ui.kb_edit(self.iso, {}, "kb_sleep", 5)
        pending = ui.kb_select_per_key(self.iso, pending, None, "#000000")
        pending = ui.kb_paint(self.iso, pending, None, {0x01: "#ff0000"})
        self.assertEqual(ui.kb_changes(pending), ["kb_sleep", "kb_keys"])
        self.assertEqual(ui.kb_saved_changes(pending), ["kb_sleep"])
        lines = ui.kb_planned_packets(self.iso, pending)
        self.assertEqual([line[:11] for line in lines],
                         ["02 51 38 00", "02 50 55 … ", "02 c0 81 0e", "02 c0 81 0e", "02 c0 81 0e", "02 c0 81 0e",
                          "02 c0 81 0d"])
        self.assertTrue(lines[2].startswith("02 c0 81 0e 00 01 ff 00 00 02 00 00 00"))
        self.assertTrue(lines[-1].endswith("(per-key colors, not saved)"))
        only_keys = {"kb_keys": pending["kb_keys"]}
        self.assertNotIn("50 55", " ".join(ui.kb_planned_packets(self.iso, only_keys)))
        self.assertIn("until it is switched off", ui.kb_apply_status(only_keys))
        self.assertIn("not saved", ui.kb_apply_status(pending))
        for command in ui.kb_commands("kb_keys", pending["kb_keys"]):
            self.assertTrue(squire.is_allowed_write(command, "keyboard"))

    def test_effect_choices_sorted_by_name(self):
        names = [ui.keyboard_effect_name(e) for e in ui.KB_EFFECT_CHOICES]
        self.assertEqual(names[-2:], ["Per-key colors", "Off"])
        self.assertEqual(names[:-2], sorted(names[:-2], key=str.casefold))
        self.assertEqual(set(ui.KB_EFFECT_CHOICES), set(ui.KB_EFFECTS) | {ui.KB_PER_KEY})

    def test_drawing_with_key_colours(self):
        keys = ui.kb_full_keys(self.iso, {0x01: "#ff0000", 0x71: "#ffff00"})
        svg = ui.keyboard_svg(self.iso, None, keys)
        ET.fromstring(svg)
        self.assertIn('style="fill:#ff0000"', svg)
        self.assertIn("on-light", svg)                       # yellow key: dark legend
        self.assertIn("on-color", svg)                       # red key: white legend
        self.assertNotIn("kb-glow)", svg)                    # no effect glow over per-key colours
        self.assertEqual(ui.kb_key_style("#000000"), ("", ""))


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


class PageTests(unittest.TestCase):
    """The page classes the window runs Apply with (settings/pages): order, save rule, taking over results."""

    class FakeWindow:
        batteries: dict = {}

        def update_preview(self):
            pass

        def render(self):
            pass

    def setUp(self):
        from settings.pages.keyboard import KeyboardPage
        from settings.pages.mouse import MousePage
        _, s = ui.demo_data()
        self.mouse = MousePage(self.FakeWindow(), squire.DEVICES.by_kind("mouse"))
        self.mouse.load(s)
        self.keyboard = KeyboardPage(self.FakeWindow(), squire.DEVICES.by_kind("keyboard"))
        self.keyboard.load(ui.demo_keyboard())          # common.DEMO is off: known comes from the saved file
        self.keyboard.known = None

    def test_every_mouse_change_is_saved(self):
        self.mouse.pending = ui.edit_other(self.mouse.settings, {}, "polling_hz", 500)
        self.mouse.pending = ui.edit_other(self.mouse.settings, self.mouse.pending, "angle_snapping", True)
        work = {key: self.mouse.pending[key] for key in self.mouse.changes()}
        self.assertEqual(self.mouse.saved_changes(work), ["angle_snapping", "polling_hz"])

    def test_per_key_colours_are_never_saved(self):
        k = self.keyboard.keyboard
        pending = ui.kb_edit(k, {}, "kb_sleep", 10)
        pending = ui.kb_select_per_key(k, pending, None, "#000000")
        self.keyboard.pending = pending
        work = {key: pending[key] for key in self.keyboard.changes()}
        self.assertEqual(list(work), ["kb_sleep", "kb_keys"])
        self.assertEqual(self.keyboard.saved_changes(work), ["kb_sleep"])

    def test_a_saved_result_is_taken_over(self):
        page = self.mouse
        page.pending = {"polling_hz": 500}
        self.assertTrue(page.accept("polling_hz", 500, squire.WriteResult("ok", saved=True)))
        self.assertEqual((page.settings.polling_hz, page.settings.polling_raw & 0x07, page.pending), (500, 2, {}))

    def test_an_unsaved_or_failed_result_stays_pending(self):
        page = self.mouse
        page.pending = {"polling_hz": 500}
        self.assertFalse(page.accept("polling_hz", 500, squire.WriteResult("ok", saved=False)))
        self.assertFalse(page.accept("polling_hz", 500, squire.WriteResult("mismatch")))
        self.assertEqual((page.settings.polling_hz, page.pending), (1000, {"polling_hz": 500}))

    def test_per_key_colours_count_when_sent(self):
        page = self.keyboard
        keys = ui.kb_keys_start(page.keyboard, None, "#ff0000")
        page.pending = {"kb_keys": keys}
        self.assertTrue(page.accept("kb_keys", keys, squire.WriteResult("ok", saved=False)))
        self.assertEqual((page.keys_active, page.pending), (keys, {}))
        self.assertTrue(page.per_key_mode())

    def test_a_saved_effect_ends_per_key_mode(self):
        page = self.keyboard
        page.keys_active = ui.kb_keys_start(page.keyboard, None, "#ff0000")
        light = squire.keyboard_light_value("static", 50, "#00ff00")
        page.pending = {"kb_light": light}
        self.assertTrue(page.accept("kb_light", light, squire.WriteResult("ok", saved=True)))
        self.assertEqual((page.keyboard.light_mode, page.keys_active), ("static", None))
        self.assertEqual(page.known, ui.kb_light_sent(light))


class SidebarTests(unittest.TestCase):
    """The sidebar lists only the devices found (settings/pages: is_present, pick, sidebar_text)."""

    def battery(self, status="ok", percent=80, connection="omni"):
        return squire.Battery("mouse", "ROG Keris II Origin", connection, percent, status=status)

    def settings(self, status):
        return squire.MouseSettings("ROG Keris II Origin", "", status=status)

    def test_found_by_the_battery_read_in_any_state(self):
        from settings.pages import is_present
        for b in (self.battery(), self.battery("asleep", None), self.battery("no-access", None),
                  self.battery(connection="bluetooth")):
            with self.subTest(status=b.status, connection=b.connection):
                self.assertTrue(is_present(b, self.settings("not-found")))

    def test_not_found_on_both_reads_is_not_listed(self):
        from settings.pages import is_present
        self.assertFalse(is_present(None, self.settings("not-found")))
        self.assertFalse(is_present(None, None))
        self.assertTrue(is_present(None, self.settings("ok")))
        self.assertTrue(is_present(None, self.settings("error")))     # the page says the read failed

    def test_selection_stays_while_listed(self):
        from settings.pages import pick
        self.assertEqual(pick(["falchion-rx-lp", "keris-ii-origin"], "keris-ii-origin"), "keris-ii-origin")
        self.assertEqual(pick(["falchion-rx-lp"], "keris-ii-origin"), "falchion-rx-lp")
        self.assertEqual(pick(["falchion-rx-lp", "keris-ii-origin"], None), "falchion-rx-lp")
        self.assertIsNone(pick([], "keris-ii-origin"))

    def test_sidebar_text(self):
        from settings.pages import sidebar_text
        self.assertEqual(sidebar_text(self.battery()), "Omni receiver · 80 %")
        self.assertEqual(sidebar_text(self.battery("asleep", None)), "asleep")
        self.assertEqual(sidebar_text(self.battery("no-access", None)), "no access")
        self.assertEqual(sidebar_text(None), "not found")


class PlainPageTests(unittest.TestCase):
    """Zones come from the model's capabilities; a model without a drawing gets the plain page."""

    class Model:
        def __init__(self, model_id, capabilities):
            self.id, self.name, self.capabilities = model_id, "Some mouse", capabilities

        def has(self, capability):
            return capability in self.capabilities

        def capability(self, name):
            return self.capabilities[name]

    def test_zones_of_todays_models(self):
        from settings.pages.keyboard import keyboard_zones
        from settings.pages.mouse import mouse_zones
        self.assertEqual(mouse_zones(squire.DEVICES.by_kind("mouse")),
                         [("logo", "Logo"), ("wheel", "Scroll wheel"), ("side", "Side"), ("dpi", "DPI"),
                          ("sensor", "Sensor"), ("power", "Power")])
        self.assertEqual([z for z, _ in keyboard_zones(squire.DEVICES.by_kind("keyboard"))],
                         ["kb_light", "kb_profile", "kb_layout", "kb_power"])

    def test_todays_models_have_drawings(self):
        from settings.drawings import has_drawing
        for model in squire.DEVICES.devices:
            self.assertTrue(has_drawing(model), model.id)

    def test_a_new_model_gets_its_own_zones_and_no_drawing(self):
        from settings.drawings import has_drawing
        from settings.pages.mouse import MousePage, mouse_zones, zone_title
        model = self.Model("some-mouse", {"led_zones": {"zones": [{"name": "wheel"}, {"name": "rear"}]},
                                          "power": {}})
        self.assertEqual(mouse_zones(model), [("wheel", "Scroll wheel"), ("rear", "Rear"), ("power", "Power")])
        self.assertFalse(has_drawing(model))
        self.assertEqual(zone_title("rear"), "Rear light")
        self.assertEqual(MousePage(PageTests.FakeWindow(), model).selected, "wheel")   # no logo: the first zone


class LayeringTests(unittest.TestCase):
    """Only settings/widgets.py and settings/window.py load GTK; the rest stays importable without it."""

    def test_pages_editors_drawings_do_not_load_gtk(self):
        import subprocess
        code = ("import sys; sys.path[:0] = ['linux', '.']; import squire_settings, importlib, pathlib\n"
                "for p in sorted(pathlib.Path('linux/settings').rglob('*.py')):\n"
                "    name = '.'.join(p.relative_to('linux').with_suffix('').parts)\n"
                "    if not name.endswith(('widgets', 'window')): importlib.import_module(name.removesuffix('.__init__'))\n"
                "print('gi' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)


if __name__ == "__main__":
    unittest.main()
