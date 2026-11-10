import sys
import types
import unittest

import squire as rb


class FakeDevice:
    def __init__(self, replies, write_result=None, fail_lengths=()):
        self.replies = list(replies)
        self.written = []
        self.write_result = write_result
        self.fail_lengths = fail_lengths
        self.closed = False

    def open_path(self, path):
        self.path = path

    def write(self, data):
        self.written.append(bytes(data))
        if len(data) in self.fail_lengths:
            return -1
        return len(data) if self.write_result is None else self.write_result

    def read(self, size, timeout):
        assert timeout > 0, "hidapi treats timeout 0 as blocking forever"
        if timeout == rb.DRAIN_TIMEOUT_MS:
            return []  # nothing stale
        return self.replies.pop(0) if self.replies else []

    def close(self):
        self.closed = True


def kb_reply(pct, charging=0, rid=0x02):
    payload = bytearray(63)
    payload[0:2] = b"\x12\x01"
    payload[5] = pct
    payload[8] = charging
    payload[10] = pct
    return list(([rid] if rid is not None else []) + list(payload))


def mouse_reply(pct, charging=0, rid=0x03):
    payload = bytearray(63)
    payload[0:2] = b"\x12\x07"
    payload[4] = pct
    payload[9] = charging
    return list(([rid] if rid is not None else []) + list(payload))


OMNI_KB, OMNI_MOUSE, WIRED_KB, WIRED_MOUSE = rb.TARGETS


class PacketTests(unittest.TestCase):
    def test_omni_keyboard_packet(self):
        p = rb.build_packet(OMNI_KB, 64)
        self.assertEqual(len(p), 64)
        self.assertEqual(p[:3], b"\x02\x12\x01")
        self.assertEqual(set(p[3:]), {0})

    def test_omni_mouse_packet(self):
        self.assertEqual(rb.build_packet(OMNI_MOUSE, 64)[:3], b"\x03\x12\x07")

    def test_wired_keyboard_packet_matches_falchion_light_framing(self):
        p = rb.build_packet(WIRED_KB, 65)
        self.assertEqual(len(p), 65)
        self.assertEqual(p[:3], b"\x00\x12\x01")

    def test_only_query_commands_are_ever_built(self):
        for t in rb.TARGETS:
            self.assertIn(t.query, (b"\x12\x01", b"\x12\x07"))


class QueryTests(unittest.TestCase):
    def test_keyboard_omni(self):
        b = rb.query(FakeDevice([kb_reply(82)]), OMNI_KB)
        self.assertEqual((b.percent, b.charging, b.status), (82, False, "ok"))

    def test_keyboard_without_report_id_in_reply(self):
        b = rb.query(FakeDevice([kb_reply(55, 1, rid=None)]), OMNI_KB)
        self.assertEqual((b.percent, b.charging), (55, True))

    def test_keyboard_fallback_to_byte5(self):
        r = kb_reply(70)
        r[1 + 10] = 0xFF  # invalid percentage byte
        self.assertEqual(rb.query(FakeDevice([r]), OMNI_KB).percent, 70)

    def test_mouse_omni_charging(self):
        b = rb.query(FakeDevice([mouse_reply(64, 1)]), OMNI_MOUSE)
        self.assertEqual((b.percent, b.charging), (64, True))

    def test_skips_unrelated_reports(self):
        event = [0x03, 0x12, 0x01, 0x05] + [0] * 60   # button event, not our echo
        b = rb.query(FakeDevice([event, mouse_reply(40)]), OMNI_MOUSE)
        self.assertEqual(b.percent, 40)

    def test_error_reply_means_asleep(self):
        b = rb.query(FakeDevice([[0x03, 0xFF, 0xAA] + [0] * 61]), OMNI_MOUSE)
        self.assertEqual((b.percent, b.status), (None, "asleep"))

    def test_zero_and_not_charging_means_asleep(self):
        self.assertEqual(rb.query(FakeDevice([mouse_reply(0)]), OMNI_MOUSE).status, "asleep")

    def test_zero_but_charging_is_valid(self):
        b = rb.query(FakeDevice([mouse_reply(0, 1)]), OMNI_MOUSE)
        self.assertEqual((b.percent, b.charging, b.status), (0, True, "ok"))

    def test_no_response(self):
        self.assertEqual(rb.query(FakeDevice([]), OMNI_KB).status, "no-response")

    def test_write_failure(self):
        self.assertEqual(rb.query(FakeDevice([], write_result=-1), OMNI_KB).status, "error")

    def test_wired_mouse_falls_back_to_64_bytes(self):
        dev = FakeDevice([mouse_reply(33, rid=None)], fail_lengths=(65,))
        b = rb.query(dev, WIRED_MOUSE)
        self.assertEqual([len(w) for w in dev.written], [65, 64])
        self.assertEqual(b.percent, 33)


class RealCaptureTests(unittest.TestCase):
    """Replies captured from real hardware, shared with the Swift app via protocol.json."""

    def test_vectors(self):
        import json
        from pathlib import Path
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())
        targets = {("keyboard", 2): OMNI_KB, ("mouse", 3): OMNI_MOUSE}
        for v in data["vectors"]:
            with self.subTest(device=v["device"]):
                reply = list(bytes.fromhex(v["reply"]))
                b = rb.query(FakeDevice([reply]), targets[(v["device"], v["report_id"])])
                self.assertEqual((b.percent, b.charging), (v["expect"]["percent"], v["expect"]["charging"]))


class EnumerateAndReadAllTests(unittest.TestCase):
    def setUp(self):
        self.devices = {}
        infos = [
            # macOS lists one entry per collection; same path for the vendor interface
            {"path": b"omni-if2", "product_id": rb.PID_OMNI, "usage_page": 0xFF00, "usage": 1, "interface_number": 2},
            {"path": b"omni-if2", "product_id": rb.PID_OMNI, "usage_page": 0xFF01, "usage": 1, "interface_number": 2},
            {"path": b"omni-if0", "product_id": rb.PID_OMNI, "usage_page": 0x0001, "usage": 6, "interface_number": 0},
        ]
        test = self

        class FakeHid(types.ModuleType):
            def enumerate(self, vid, pid):
                return [i for i in infos if pid in (0, i["product_id"])]

            def device(self):
                dev = FakeDevice([])
                orig_write = dev.write

                def write(data):
                    rid = data[0]
                    dev.replies = [kb_reply(90)] if rid == 0x02 else [mouse_reply(12)]
                    return orig_write(data)
                dev.write = write
                test.devices.setdefault("all", []).append(dev)
                return dev

        sys.modules["hid"] = FakeHid("hid")

    def tearDown(self):
        sys.modules.pop("hid", None)

    def test_paths_dedupe_and_skip_input_collections(self):
        import hid
        self.assertEqual(rb.enumerate_paths(hid, rb.PID_OMNI), [b"omni-if2"])

    def test_read_all_via_omni(self):
        results = rb.read_all()
        self.assertEqual([(b.device, b.percent) for b in results], [("keyboard", 90), ("mouse", 12)])
        self.assertTrue(all(d.closed for d in self.devices["all"]))
        self.assertTrue(all(d.path == b"omni-if2" for d in self.devices["all"]))


class SwiftBarTests(unittest.TestCase):
    def test_title_and_low_color(self):
        out = rb.swiftbar([
            rb.Battery("keyboard", "ROG Falchion RX LP", "omni", 82),
            rb.Battery("mouse", "ROG Keris II Origin", "omni", 12),
        ])
        first = out.splitlines()[0]
        self.assertIn("82%", first)
        self.assertIn("12%", first)
        self.assertIn("color=red", first)
        self.assertIn("sfimage=computermouse", out)

    def test_asleep_device(self):
        out = rb.swiftbar([rb.Battery("mouse", "ROG Keris II Origin", "omni", None, status="asleep")])
        self.assertIn(":computermouse: –", out.splitlines()[0])
        self.assertIn("asleep", out)

    def test_nothing_found(self):
        self.assertIn("No ASUS devices", rb.swiftbar([]))


if __name__ == "__main__":
    unittest.main()
