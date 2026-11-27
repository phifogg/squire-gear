"""devices.json: the checker accepts the real file and refuses broken ones; squire.py's tables come from it."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import squire
from squirelib import registry

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
        original = squire.WRITE_PREFIXES["mouse"]
        polling = squire.polling_command(500)
        try:
            squire.WRITE_PREFIXES["mouse"] = tuple(p for p in original if p != bytes([0x51, 0x31, 0x04]))
            self.assertFalse(squire.is_allowed_write(polling, "mouse"))
        finally:
            squire.WRITE_PREFIXES["mouse"] = original
        self.assertTrue(squire.is_allowed_write(polling, "mouse"))

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


class PackagingTests(unittest.TestCase):
    """squire.py does not start without devices.json and squirelib/: every installer must copy them."""

    def test_installers_copy_the_model(self):
        for script in ("install.sh", "linux/install.sh"):
            text = (ROOT / script).read_text()
            with self.subTest(script=script):
                self.assertIn("devices.json", text)
                self.assertIn("squirelib/*.py", text)

    def test_nfpm_has_every_squirelib_file(self):
        text = (ROOT / "linux/packaging/nfpm.yaml").read_text()
        self.assertIn("dst: /usr/share/squire-gear/devices.json", text)
        for path in sorted((ROOT / "squirelib").glob("*.py")):
            with self.subTest(file=path.name):
                self.assertIn(f"src: squirelib/{path.name}", text)
                self.assertIn(f"dst: /usr/share/squire-gear/squirelib/{path.name}", text)


if __name__ == "__main__":
    unittest.main()
