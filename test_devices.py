"""devices.json: the checker accepts the real file and refuses broken ones; squire.py's tables come from it."""
import copy
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

import squire
from squirelib import capabilities, registry

ROOT = Path(__file__).parent
DATA = json.loads((ROOT / "devices.json").read_text())
PROTOCOL = json.loads((ROOT / "protocol.json").read_text())


def device(data, kind):
    return next(d for d in data["devices"] if d["kind"] == kind)


def command(data, kind, name):
    return next(c for c in device(data, kind)["commands"] if c["name"] == name)


class CheckerTests(unittest.TestCase):
    def assertProblem(self, data, fragment):
        problems = registry.check(data)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} not in {problems}")

    def broken(self):
        return copy.deepcopy(DATA)

    def test_devices_json_is_valid(self):
        self.assertEqual(registry.check(DATA), [])
        self.assertEqual([d.id for d in registry.load().devices], ["falchion-rx-lp", "keris-ii-origin"])

    def test_load_lists_every_problem(self):
        data = self.broken()
        data["vendor_id"] = "B05"
        device(data, "mouse")["kind"] = "trackball"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(registry.DevicesError) as raised:
                registry.load(path)
        self.assertEqual(len(raised.exception.problems), 2)

    def test_wrong_types(self):
        data = self.broken()
        device(data, "mouse")["capabilities"]["dpi"]["stages"] = "4"
        self.assertProblem(data, "dpi.stages")
        data = self.broken()
        device(data, "mouse")["capabilities"]["dpi"]["stages"] = True   # a bool is not a number here
        self.assertProblem(data, "dpi.stages")
        data = self.broken()
        device(data, "keyboard")["capabilities"]["battery"]["percent"] = [10, "5"]
        self.assertProblem(data, "percent[1]")

    def test_hex_formats(self):
        data = self.broken()
        device(data, "keyboard")["routes"][1]["pid"] = "0x1b04"
        self.assertProblem(data, "routes[1].pid")
        data = self.broken()
        command(data, "mouse", "battery")["bytes"] = "1207"
        self.assertProblem(data, "bytes")

    def test_ranges(self):
        data = self.broken()
        device(data, "mouse")["capabilities"]["dpi"]["min"] = 125     # not a multiple of the step
        self.assertProblem(data, "multiples of step")
        data = self.broken()
        device(data, "keyboard")["capabilities"]["kb_lighting"]["gradients"][0]["stops"] = [[14, "ff0029"]]
        self.assertProblem(data, "1 stops, not 2..7")
        data = self.broken()
        device(data, "keyboard")["capabilities"]["kb_lighting"]["gradients"][2]["stops"][1][0] = 10
        self.assertProblem(data, "positions must rise")
        data = self.broken()
        device(data, "keyboard")["capabilities"]["per_key"]["layouts"]["iso"][0][0] = "00"   # row 0 does not exist
        self.assertProblem(data, "outside the 15 columns")
        data = self.broken()
        device(data, "mouse")["capabilities"]["profile"]["firmware"]["omni"] = 62   # 3 bytes do not fit
        self.assertProblem(data, "profile.firmware.omni")
        data = self.broken()
        device(data, "mouse")["capabilities"]["profile"]["firmware"]["bluetooth"] = 4
        self.assertProblem(data, "unknown connection 'bluetooth'")
        data = self.broken()
        device(data, "keyboard")["capabilities"]["kb_profile"]["profile_base"] = 2
        self.assertProblem(data, "kb_profile.profile_base")
        data = self.broken()
        device(data, "mouse")["capabilities"]["dpi"]["offsets"]["dpi_xy"] = 49   # 4 stages x 4 bytes do not fit
        self.assertProblem(data, "dpi.offsets.dpi_xy")
        data = self.broken()
        device(data, "mouse")["capabilities"]["sensor"]["offsets"]["debounce_ms"] = 14
        self.assertProblem(data, "unknown offset 'debounce_ms'")
        data = self.broken()
        del device(data, "mouse")["capabilities"]["surface"]["offsets"]["params"]
        self.assertProblem(data, "missing 'params'")

    def test_unique_pids_ids_and_commands(self):
        data = self.broken()
        device(data, "mouse")["routes"][1]["pid"] = "1b04"
        self.assertProblem(data, "duplicate pid '1b04'")
        data = self.broken()
        device(data, "mouse")["id"] = "falchion-rx-lp"
        self.assertProblem(data, "duplicate id")
        data = self.broken()
        device(data, "mouse")["commands"].append({"name": "again", "bytes": "12 07", "status": "confirmed",
                                                  "approved": "2026-11-27"})
        self.assertProblem(data, "duplicate read '12 07'")
        data = self.broken()
        device(data, "mouse")["capabilities"]["surface"]["surfaces"][1]["id"] = "00"
        self.assertProblem(data, "duplicate id '00'")

    def test_every_capability_has_its_parameters(self):
        for kind in ("keyboard", "mouse"):
            for name, cap in DATA["devices"][0 if kind == "keyboard" else 1]["capabilities"].items():
                for key in cap:
                    with self.subTest(kind=kind, capability=name, missing=key):
                        data = self.broken()
                        del device(data, kind)["capabilities"][name][key]
                        self.assertNotEqual(registry.check(data), [])

    def test_unknown_capability_and_no_battery(self):
        data = self.broken()
        device(data, "mouse")["capabilities"]["teleport"] = {}
        self.assertProblem(data, "unknown capability 'teleport'")
        data = self.broken()
        del device(data, "mouse")["capabilities"]["battery"]
        self.assertProblem(data, "needs 'battery'")

    def test_references(self):
        data = self.broken()
        device(data, "mouse")["routes"][0]["receiver"] = "other-dongle"
        self.assertProblem(data, "no receiver 'other-dongle'")
        data = self.broken()
        device(data, "mouse")["capabilities"]["led_zones"]["write"] = "led_logo"    # a read, not a write
        self.assertProblem(data, "is not a write")
        data = self.broken()
        device(data, "keyboard")["capabilities"]["kb_lighting"]["directions"][0]["effect"] = "lava"
        self.assertProblem(data, "no effect 'lava'")

    def test_reads_are_get_requests_and_writes_are_not(self):
        data = self.broken()
        command(data, "keyboard", "layout")["bytes"] = "50 02"        # switch profile: never a read
        self.assertProblem(data, "get request")
        data = self.broken()
        command(data, "keyboard", "save")["prefixes"] = ["12 01"]
        self.assertProblem(data, "not a write")

    def test_confirmed_commands_need_an_approval_date(self):
        data = self.broken()
        del command(data, "mouse", "led")["approved"]
        self.assertProblem(data, "missing 'approved'")
        data = self.broken()
        command(data, "mouse", "led")["status"] = "maybe"
        self.assertProblem(data, "status")


class ModelTests(unittest.TestCase):
    def test_unconfirmed_commands_are_not_sent(self):
        data = copy.deepcopy(DATA)
        command(data, "mouse", "liftoff")["status"] = "unconfirmed"
        command(data, "mouse", "polling")["status"] = "unconfirmed"
        mouse = registry.Registry(data).by_kind("mouse")
        self.assertNotIn("liftoff", mouse.reads())
        self.assertIn("liftoff", mouse.reads(confirmed=False))
        self.assertNotIn(bytes([0x51, 0x31, 0x04]), mouse.confirmed_write_prefixes())

    def test_write_must_have_a_confirmed_prefix(self):
        polling = squire.polling_command(500)
        self.assertTrue(squire.is_allowed_write(polling, "mouse"))
        data = copy.deepcopy(DATA)
        command(data, "mouse", "polling")["status"] = "unconfirmed"
        caps = capabilities.for_device(registry.Registry(data).by_kind("mouse"))
        self.assertFalse(caps.is_allowed_write(polling))      # same bytes, but the model has not confirmed them
        with self.assertRaises(AssertionError):
            caps["polling"].command(500)                      # the builder refuses too
        self.assertTrue(caps.is_allowed_write(squire.angle_snapping_command(True)))   # the rest is unaffected

    def test_allowlist_is_the_capabilities(self):
        """Every confirmed write prefix belongs to a capability, and every write a builder makes is accepted by
        exactly the capability that owns its prefix (so a device's allowlist = its capabilities' commands)."""
        for kind, caps in squire.CAPABILITIES.items():
            for prefix in caps.confirmed_prefixes:
                self.assertIsNotNone(caps.owner_of(prefix), f"{kind} {prefix.hex(' ')} has no capability")
        self.assertIs(squire.CAPABILITIES["mouse"].owner_of(squire.polling_command(500)).name, "polling")
        self.assertIs(squire.CAPABILITIES["mouse"].owner_of(squire.dpi_command(1, 800, "#ff0000")).name, "dpi")
        self.assertIs(squire.CAPABILITIES["mouse"].owner_of(squire.angle_tuning_command(5)).name, "sensor")
        self.assertIsNone(squire.CAPABILITIES["keyboard"].owner_of(squire.polling_command(500)))
        self.assertEqual(squire.CAPABILITIES["mouse"].save_command, bytes([0x50, 0x03]))
        self.assertEqual(squire.CAPABILITIES["keyboard"].save_command, bytes([0x50, 0x55]))

    def test_every_capability_in_the_model_has_a_module(self):
        for device in squire.DEVICES.devices:
            for name in device.data["capabilities"]:
                self.assertIn(name, capabilities.CLASSES_BY_NAME, f"{device.id}: {name}")

    def test_profile_offsets_come_from_the_model(self):
        """Moving the profile / firmware offsets in devices.json moves what the parser reads (no hard-coded copy)."""
        p = bytes([0x12, 0x00, 0, 0, 0x08, 0x00, 0x07, 0, 0, 0, 0x02, 0, 0, 0, 0x0a, 0x00, 0x01, 0, 0, 0, 0x03] + [0] * 43)
        s = squire.MouseSettings("", "omni")
        squire.CAPABILITIES["mouse"]["profile"].parse(s, "profile", p)
        self.assertEqual((s.profile, s.firmware, s.receiver_firmware), (3, "01.00.0A", "07.00.08"))
        data = copy.deepcopy(DATA)
        device(data, "mouse")["capabilities"]["profile"].update(profile=20, profile_base=1, receiver_firmware=14,
                                                                firmware={"omni": 4, "wired": 4})
        self.assertEqual(registry.check(data), [])
        caps = capabilities.for_device(registry.Registry(data).by_kind("mouse"))
        s = squire.MouseSettings("", "omni")
        caps["profile"].parse(s, "profile", p)
        self.assertEqual((s.profile, s.firmware, s.receiver_firmware), (3, "07.00.08", "01.00.0A"))
        wired = squire.MouseSettings("", "wired")
        caps["profile"].parse(wired, "profile", p)
        self.assertEqual((wired.firmware, wired.receiver_firmware), ("07.00.08", None))   # no receiver on the cable

    def test_reply_offsets_come_from_the_model(self):
        """Every mouse reply parser reads its offsets from devices.json: shift all of them by one in a copy and a
        reply shifted by one byte parses to the same settings."""
        captures = json.loads((ROOT / "protocol.json").read_text())["mouse_settings"]["captures"]
        data = copy.deepcopy(DATA)
        caps_data = device(data, "mouse")["capabilities"]
        for name in ("led_zones", "dpi", "polling", "sensor", "surface"):
            caps_data[name]["offsets"] = {k: v + 1 for k, v in caps_data[name]["offsets"].items()}
        self.assertEqual(registry.check(data), [])
        shifted = capabilities.for_device(registry.Registry(data).by_kind("mouse"))
        checked = 0
        for capture in captures:
            read = capture["command"]
            if read in ("profile", "battery"):
                continue                                   # the profile capability has its own test
            p = bytes.fromhex(capture["reply"])[1:]        # payload from the echo on (Omni: report ID first)
            expected, got = squire.MouseSettings("", "omni"), squire.MouseSettings("", "omni")
            squire.parse_mouse_settings(expected, read, p)
            for cap in shifted:
                if cap.name not in ("battery", "profile", "power"):
                    cap.parse(got, read, p[:4] + b"\0" + p[4:])
            self.assertEqual(dataclasses.asdict(got), dataclasses.asdict(expected), read)
            self.assertNotEqual(dataclasses.asdict(got), dataclasses.asdict(squire.MouseSettings("", "omni")), read)
            checked += 1
        self.assertGreaterEqual(checked, 7)

    def test_profile_count_limits_the_profile(self):
        p = bytes([0x12, 0x00] + [0] * 8 + [6] + [0] * 53)       # keyboard reports profile 6 of 5
        s = squire.KeyboardSettings("", "omni")
        squire.parse_keyboard_settings(s, "profile", p)
        self.assertIsNone(s.profile)

    def test_never_switch_profile_or_debounce(self):
        for kind in ("keyboard", "mouse"):
            prefixes = squire.WRITE_PREFIXES[kind]
            self.assertFalse(any(bytes([0x50, 0x02]).startswith(p) or p.startswith(bytes([0x50, 0x02]))
                                 for p in prefixes))
        self.assertFalse(squire.is_allowed_write(bytes([0x51, 0x31, 0x05, 0x00, 0x01]), "mouse"))

    def test_routes_in_priority_order(self):
        mouse = squire.DEVICES.by_kind("mouse")
        self.assertEqual([r["connection"] for r in mouse.routes()], ["omni", "wired", "bluetooth"])
        omni = mouse.routes()[0]
        self.assertEqual((omni["pid"], omni["interface"], omni["report_lengths"]), ("1ace", 2, [64]))

    def test_targets_unchanged(self):
        self.assertEqual([(t.device, t.name, hex(t.pid), t.report_id, t.query.hex(), t.report_lens)
                          for t in squire.TARGETS], [
            ("keyboard", "ROG Falchion RX LP", "0x1ace", 0x02, "1201", (64,)),
            ("mouse", "ROG Keris II Origin", "0x1ace", 0x03, "1207", (64,)),
            ("keyboard", "ROG Falchion RX LP", "0x1b04", None, "1201", (65,)),
            ("mouse", "ROG Keris II Origin", "0x1c0c", None, "1207", (65, 64)),
        ])
        self.assertEqual(squire.HID_INTERFACES, {0x1ACE: 2, 0x1B04: 1, 0x1C0C: 0})

    def test_bluetooth_matches_protocol_json(self):
        devices = PROTOCOL["bluetooth"]["devices"]
        self.assertEqual({int(d["product_id"], 16): k for k, d in devices.items()}, squire.BLUETOOTH_PIDS)

    def test_by_kind_needs_exactly_one(self):
        data = copy.deepcopy(DATA)
        second = copy.deepcopy(device(data, "mouse"))
        second["id"] = "keris-ii-ace"
        data["devices"].append(second)
        with self.assertRaises(LookupError):
            registry.Registry(data).by_kind("mouse")


class LayerTests(unittest.TestCase):
    """Stage 2: transport and protocol know nothing about devices; routes come from devices.json."""

    def test_transport_and_protocol_load_without_the_device_model(self):
        import subprocess
        import sys
        code = ("import sys; import squirelib.transport, squirelib.transport.bluez, squirelib.protocol; "
                "print(sorted(m for m in sys.modules if m.startswith('squirelib')))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertNotIn("squirelib.registry", out.stdout)
        self.assertNotIn("squirelib.routes", out.stdout)

    def test_targets_carry_connection_and_interface_from_the_routes(self):
        self.assertEqual([(t.device, t.connection, t.interface) for t in squire.TARGETS], [
            ("keyboard", "omni", 2), ("mouse", "omni", 2), ("keyboard", "wired", 1), ("mouse", "wired", 0)])

    def test_exchange_writes_only_what_build_returns(self):
        from squirelib import protocol

        class Dev:
            def __init__(self):
                self.written, self.replies = [], [bytes([0x03, 0x12, 0x07, 0x00, 0x50])]

            def read(self, size, timeout):
                assert timeout > 0
                return [] if timeout == squire.DRAIN_TIMEOUT_MS else (self.replies.pop(0) if self.replies else [])

            def write(self, data):
                self.written.append(bytes(data))
                return len(data)

        dev, target = Dev(), squire.TARGETS[1]
        ex = protocol.exchange(dev, target, b"\x12\x07", lambda n: b"built:%d" % n)
        self.assertEqual((ex.status, ex.base, dev.written), ("ok", 1, [b"built:64"]))


class PackagingTests(unittest.TestCase):
    """squire.py does not start without devices.json and squirelib/: every installer must copy them."""

    def test_installers_copy_the_model(self):
        for script in ("install.sh", "linux/install.sh"):
            text = (ROOT / script).read_text()
            with self.subTest(script=script):
                self.assertIn("devices.json", text)
                self.assertIn("find squirelib -name '*.py'", text)   # subpackages too

    def test_nfpm_has_every_squirelib_file(self):
        text = (ROOT / "linux/packaging/nfpm.yaml").read_text()
        self.assertIn("dst: /usr/share/squire-gear/devices.json", text)
        for path in sorted((ROOT / "squirelib").rglob("*.py")):
            name = path.relative_to(ROOT / "squirelib").as_posix()
            with self.subTest(file=name):
                self.assertIn(f"src: squirelib/{name}", text)
                self.assertIn(f"dst: /usr/share/squire-gear/squirelib/{name}", text)


if __name__ == "__main__":
    unittest.main()
