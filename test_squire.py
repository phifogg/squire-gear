import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

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

    def test_error_followed_by_valid_reply_is_ok(self):
        # seen on real hardware: 02 ff aa immediately followed by the real reply
        b = rb.query(FakeDevice([[0x02, 0xFF, 0xAA] + [0] * 61, kb_reply(78)]), OMNI_KB)
        self.assertEqual((b.percent, b.status), (78, "ok"))

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
        targets = {("keyboard", 2): OMNI_KB, ("mouse", 3): OMNI_MOUSE, ("keyboard", None): WIRED_KB,
                   ("mouse", None): WIRED_MOUSE}
        for v in data["vectors"]:
            with self.subTest(device=v["device"]):
                reply = list(bytes.fromhex(v["reply"]))
                b = rb.query(FakeDevice([reply]), targets[(v["device"], v["report_id"])])
                self.assertEqual((b.percent, b.charging), (v["expect"]["percent"], v["expect"]["charging"]))
                self.assertEqual(b.low_warning, v["expect"]["low_warning"])


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

        self.hid = FakeHid("hid")

    def test_paths_dedupe_and_skip_input_collections(self):
        self.assertEqual(rb.enumerate_paths(self.hid, rb.PID_OMNI), [b"omni-if2"])

    def test_read_all_via_omni(self):
        results = rb.read_all(hid=self.hid)
        self.assertEqual([(b.device, b.percent) for b in results], [("keyboard", 90), ("mouse", 12)])
        self.assertTrue(all(d.closed for d in self.devices["all"]))
        self.assertTrue(all(d.path == b"omni-if2" for d in self.devices["all"]))


# Report descriptors read from /sys/class/hidraw/*/device/report_descriptor on Linux (Omni receiver)
OMNI_IF2_DESCRIPTOR = bytes.fromhex(
    "0602ff0901a101850109027508953f150026ff00810209037508953f150026ff009102c0"
    "0600ff0901a101850209027508953f150026ff00810209037508953f150026ff009102c0"
    "0601ff0901a101850309027508953f150026ff00810209037508953f150026ff009102c0")
OMNI_IF0_DESCRIPTOR = bytes.fromhex(  # boot keyboard: must never be opened
    "05010906a10185010508150025011901290595057501910295017503910305071500250119e029e775019508810275"
    "08950181010507150026ff0019002998750895068100c005010906a101850275019598150025010507190029988102c0")


class DescriptorTests(unittest.TestCase):
    def test_omni_vendor_interface(self):
        self.assertEqual(rb.descriptor_collections(OMNI_IF2_DESCRIPTOR),
                         [(0xFF02, 1), (0xFF00, 1), (0xFF01, 1)])

    def test_omni_keyboard_interface(self):
        self.assertEqual(rb.descriptor_collections(OMNI_IF0_DESCRIPTOR), [(1, 6), (1, 6)])

    def test_nested_collections_are_not_top_level(self):
        # page ff00, usage 1, collection(app) { usage 2, collection(logical) {} }, end
        d = bytes.fromhex("0600ff0901a1010902a102c0c0")
        self.assertEqual(rb.descriptor_collections(d), [(0xFF00, 1)])

    def test_extended_usage_carries_its_own_page(self):
        d = bytes.fromhex("0b06000100a101c0")  # usage 0x0001:0006 (4-byte), collection
        self.assertEqual(rb.descriptor_collections(d), [(1, 6)])

    def test_truncated_descriptor_does_not_crash(self):
        self.assertEqual(rb.descriptor_collections(b"\x06\x00"), [])


class HidrawBackendTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sysfs = self.root / "sys" / "class" / "hidraw"
        self.add_node(7, 0x1ACE, 0, OMNI_IF0_DESCRIPTOR)
        self.add_node(9, 0x1ACE, 2, OMNI_IF2_DESCRIPTOR)
        self.add_node(12, 0x1C0C, 0, bytes.fromhex("05010902a101c0" "0601ff0901a101c0"))  # mixed
        self.add_node(3, 0x1939, 2, bytes.fromhex("0672ff09a1a101c0"), vid=0x0B05)
        self.add_node(1, 0x12E0, 3, OMNI_IF2_DESCRIPTOR, vid=0x1038)
        self.backend = rb.HidrawBackend(self.sysfs, self.root / "dev")

    def add_node(self, n, pid, iface, descriptor, vid=rb.VID):
        usb_iface = self.root / "usb" / f"1-1:1.{iface}-{n}"
        hid_dev = usb_iface / f"0003:{vid:04X}:{pid:04X}.{n:04X}"
        hid_dev.mkdir(parents=True)
        (usb_iface / "bInterfaceNumber").write_text(f"{iface:02x}\n")
        (hid_dev / "uevent").write_text(f"DRIVER=hid-generic\nHID_ID=0003:0000{vid:04X}:0000{pid:04X}\n"
                                        f"HID_NAME=Test {pid:04x}\n")
        (hid_dev / "report_descriptor").write_bytes(descriptor)
        node = self.sysfs / f"hidraw{n}"
        node.mkdir(parents=True)
        (node / "device").symlink_to(hid_dev)

    def path(self, n):
        return str(self.root / "dev" / f"hidraw{n}").encode()

    def test_enumerate_filters_by_vid_pid_and_reads_interface(self):
        infos = self.backend.enumerate(rb.VID, rb.PID_OMNI)
        self.assertEqual([(i["path"], i["interface_number"]) for i in infos],
                         [(self.path(7), 0), (self.path(9), 2)])
        self.assertEqual(infos[1]["collections"], [(0xFF02, 1), (0xFF00, 1), (0xFF01, 1)])
        self.assertEqual(len(self.backend.enumerate(rb.VID, 0)), 4)   # not the SteelSeries node

    def test_only_pure_vendor_nodes_are_opened(self):
        self.assertEqual(rb.enumerate_paths(self.backend, rb.PID_OMNI), [self.path(9)])
        # interface mixing mouse input and a vendor collection: never opened
        self.assertEqual(rb.enumerate_paths(self.backend, rb.PID_KERIS_II_ORIGIN), [])

    def test_permission_error_reports_no_access(self):
        class Denied(FakeDevice):
            def open_path(self, path):
                raise PermissionError(13, "Permission denied")

        backend = self.backend
        backend.device = lambda: Denied([])
        results = rb.read_all(hid=backend)
        self.assertEqual([(b.device, b.status) for b in results], [("keyboard", "no-access"), ("mouse", "no-access")])


class HidrawDeviceTests(unittest.TestCase):
    def setUp(self):
        self.r, self.w = os.pipe()
        self.dev = rb.HidrawDevice()
        self.dev.fd = self.r
        self.addCleanup(os.close, self.w)
        self.addCleanup(self.dev.close)

    def test_read_returns_one_report(self):
        os.write(self.w, b"\x02\x12\x01")
        self.assertEqual(self.dev.read(65, 50), [2, 0x12, 1])

    def test_read_times_out_empty(self):
        self.assertEqual(self.dev.read(65, 10), [])

    def test_zero_timeout_is_refused(self):
        with self.assertRaises(ValueError):
            self.dev.read(65, 0)


def settings_reply(command, fields, rid=0x03):
    """Reply that echoes `command`; fields = {index relative to the echo: byte}."""
    payload = bytearray(63)
    payload[0:len(command)] = command
    for index, value in fields.items():
        payload[index] = value
    return list(([rid] if rid is not None else []) + list(payload))


def le16(value):
    return {0: value & 0xFF, 1: value >> 8}


def dpi_raw(dpi):
    return (dpi - 50) // 50


class AllowlistTests(unittest.TestCase):
    def test_every_allowed_command_is_a_get_request(self):
        for command in rb.ALLOWED_COMMANDS:
            self.assertEqual(command[0], 0x12, command.hex(" "))

    def test_write_commands_are_refused(self):
        for command in (bytes([0x51, 0x28, 0x00]), bytes([0x50, 0x02, 0x01]), bytes([0x50, 0x03]),
                        bytes([0x51, 0x31, 0x00]), bytes([0x12, 0x99])):
            with self.subTest(command=command.hex(" ")), self.assertRaises(ValueError):
                rb.packet(0x03, command, 64)

    def test_exact_bytes_omni(self):
        expected = {
            "profile": "03 12 00", "settings": "03 12 04 00", "dpi_xy": "03 12 04 02",
            "dpi_colors": "03 12 04 03", "led_logo": "03 12 03 00", "led_wheel": "03 12 03 01",
            "led_side": "03 12 03 02", "liftoff": "03 12 06",
        }
        self.assertEqual(set(expected), set(rb.MOUSE_SETTINGS_QUERIES))
        for name, prefix in expected.items():
            with self.subTest(name=name):
                data = rb.packet(0x03, rb.MOUSE_SETTINGS_QUERIES[name], 64)
                head = bytes.fromhex(prefix)
                self.assertEqual(len(data), 64)
                self.assertEqual(data[:len(head)], head)
                self.assertEqual(set(data[len(head):]), {0})

    def test_exact_bytes_wired(self):
        data = rb.packet(None, rb.MOUSE_SETTINGS_QUERIES["led_side"], 65)
        self.assertEqual((len(data), data[:4]), (65, bytes.fromhex("00 12 03 02")))


class MouseSettingsParseTests(unittest.TestCase):
    def parse(self, name, fields):
        s = rb.MouseSettings("m", "omni")
        command = rb.MOUSE_QUERY if name == "battery" else rb.MOUSE_SETTINGS_QUERIES[name]
        rb.parse_mouse_settings(s, name, bytes(settings_reply(command, fields, rid=None)))
        return s

    def test_profile(self):
        s = self.parse("profile", {4: 0x02, 6: 0x07, 10: 2, 11: 3, 12: 0xFF, 13: 0xFF, 14: 0x09, 16: 0x01})
        self.assertEqual((s.profile, s.dpi_stage, s.firmware, s.receiver_firmware), (3, 3, "01.00.09", "07.00.02"))
        self.assertIsNone(self.parse("profile", {11: 0}).dpi_stage)

    def test_settings(self):
        fields = {}
        for i, dpi in enumerate((400, 800, 650, 3200)):   # values libratbag read from this mouse
            fields.update({4 + 2 * i + k: v for k, v in le16(dpi_raw(dpi)).items()})
        fields.update({12: 3, 14: 1, 16: 1})
        fields.update({18 + k: v for k, v in le16(-5 & 0xFFFF).items()})
        s = self.parse("settings", fields)
        self.assertEqual(s.dpi, [400, 800, 650, 3200])
        self.assertEqual((s.polling_hz, s.debounce_index, s.angle_snapping, s.angle_tuning), (1000, 1, True, -5))

    def test_max_dpi(self):
        s = self.parse("settings", {4 + k: v for k, v in le16(dpi_raw(42000)).items()})
        self.assertEqual(s.dpi[0], 42000)

    def test_dpi_xy_and_colors(self):
        s = self.parse("dpi_xy", {**{4 + k: v for k, v in le16(dpi_raw(800)).items()},
                                  **{6 + k: v for k, v in le16(dpi_raw(1600)).items()}})
        self.assertEqual(s.dpi_xy[0], [800, 1600])
        s = self.parse("dpi_colors", {4: 0xFF, 5: 0x00, 6: 0x00, 7: 0x00, 8: 0x00, 9: 0xFF})
        self.assertEqual(s.dpi_colors[:2], ["#ff0000", "#0000ff"])

    def test_led(self):
        s = self.parse("led_side", {4: 0x01, 5: 75, 6: 0x3D, 7: 0x8B, 8: 0xFF})
        self.assertEqual(s.leds["side"], {"mode": "breathing", "brightness": 75, "color": "#3d8bff"})
        self.assertEqual(self.parse("led_logo", {4: 0xF0}).leds["logo"]["mode"], "off")
        self.assertEqual(self.parse("led_logo", {4: 0x42}).leds["logo"]["mode"], "0x42")  # unknown: raw

    def test_liftoff_and_power(self):
        self.assertEqual(self.parse("liftoff", {7: 1}).liftoff, "high")
        s = self.parse("battery", {4: 80, 5: 2, 6: 20})
        self.assertEqual((s.power_off_minutes, s.low_warning), (3, 20))
        self.assertIsNone(self.parse("battery", {4: 80, 5: 0xFF, 6: 20}).power_off_minutes)

    def test_real_battery_capture_gives_power_settings(self):
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())
        omni_mouse = next(v for v in data["vectors"] if v["device"] == "mouse" and v["report_id"] == 3)
        s = rb.MouseSettings("m", "omni")
        rb.parse_mouse_settings(s, "battery", bytes.fromhex(omni_mouse["reply"])[1:])
        self.assertEqual((s.power_off_minutes, s.low_warning), (3, 20))


class RealMouseSettingsCaptureTests(unittest.TestCase):
    """protocol.json mouse_settings: real replies from the Keris II Origin."""

    def test_wired_captures(self):
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())["mouse_settings_wired"]
        for capture in data["captures"]:
            s = rb.MouseSettings("m", "wired")
            reply = bytes.fromhex(capture["reply"])
            command = rb.MOUSE_SETTINGS_QUERIES[capture["command"]]
            self.assertEqual(rb.find_echo(reply, command), 0, capture["command"])   # no report ID over the cable
            rb.parse_mouse_settings(s, capture["command"], reply)
            for key, value in capture["expect"].items():
                with self.subTest(command=capture["command"], key=key):
                    self.assertEqual(getattr(s, key), value)

    def test_captures(self):
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())["mouse_settings"]
        s = rb.MouseSettings("m", "omni")
        for capture in data["captures"]:      # in reading order: settings before dpi_xy
            reply = bytes.fromhex(capture["reply"])
            command = rb.MOUSE_SETTINGS_QUERIES[capture["command"]]
            base = rb.find_echo(reply, command)
            self.assertEqual(base, 1, capture["command"])
            rb.parse_mouse_settings(s, capture["command"], reply[base:])
        for capture in data["captures"]:
            for key, value in capture["expect"].items():
                with self.subTest(command=capture["command"], key=key):
                    actual = s.leds.get(key) if key in rb.LED_ZONES else getattr(s, key)
                    self.assertEqual(actual, value)


class ReadMouseSettingsTests(unittest.TestCase):
    """Whole read against a fake Omni receiver: only allowlisted get requests go out."""

    def make_backend(self, battery_pct=80):
        sent = []
        replies = {
            rb.MOUSE_QUERY: settings_reply(rb.MOUSE_QUERY, {4: battery_pct, 5: 2, 6: 20}),
            rb.MOUSE_SETTINGS_QUERIES["profile"]: settings_reply(rb.MOUSE_SETTINGS_QUERIES["profile"], {10: 0, 11: 2}),
            rb.MOUSE_SETTINGS_QUERIES["settings"]: settings_reply(rb.MOUSE_SETTINGS_QUERIES["settings"], {4: 7, 12: 3}),
            rb.MOUSE_SETTINGS_QUERIES["led_wheel"]: settings_reply(rb.MOUSE_SETTINGS_QUERIES["led_wheel"], {4: 2, 5: 100}),
        }

        class Dev(FakeDevice):
            def write(self, data):
                sent.append(bytes(data))
                command = next((c for c in sorted(rb.ALLOWED_COMMANDS, key=len, reverse=True)
                                if bytes(data[1:1 + len(c)]) == c), None)
                self.replies = [replies[command]] if command in replies else []
                return len(data)

        class Backend:
            def enumerate(self, vid, pid):
                if pid != rb.PID_OMNI:
                    return []
                return [{"path": b"omni-if2", "product_id": pid, "usage_page": 0xFF00, "interface_number": 2}]

            def device(self):
                return Dev([])

        return Backend(), sent

    def test_reads_current_profile_only_with_get_requests(self):
        backend, sent = self.make_backend()
        s = rb.read_mouse_settings(hid=backend)
        self.assertEqual((s.status, s.connection, s.profile, s.dpi_stage, s.dpi[0], s.polling_hz),
                         ("ok", "omni", 1, 2, 400, 1000))
        self.assertEqual(s.leds["wheel"]["mode"], "color-cycle")
        self.assertEqual(s.raw["led_logo"], "no-response")     # missing reply: kept, not fatal
        self.assertTrue(all(packet[0] == 0x03 and packet[1] == 0x12 for packet in sent))
        self.assertEqual(len(sent), 1 + len(rb.MOUSE_SETTINGS_QUERIES))

    def test_asleep_mouse_sends_nothing_else(self):
        backend, sent = self.make_backend(battery_pct=0)

        class Asleep(FakeDevice):
            def write(self, data):
                sent.append(bytes(data))
                self.replies = [[0x03, 0xFF, 0xAA] + [0] * 61]
                return len(data)
        backend.device = lambda: Asleep([])
        s = rb.read_mouse_settings(hid=backend)
        self.assertEqual(s.status, "asleep")
        self.assertEqual(len(sent), 1)   # only the battery query



class DeviceLockTests(unittest.TestCase):
    def test_second_process_waits_for_the_first(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            code = ("import fcntl,sys; f=open(sys.argv[1],'w'); "
                    "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)")
            with rb.device_lock(path):
                busy = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
            free = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
            self.assertNotEqual(busy.returncode, 0)
            self.assertEqual(free.returncode, 0)

    def test_unusable_lock_path_does_not_block_reading(self):
        with rb.device_lock(Path("/proc/forbidden/lock")) as lock:
            self.assertIsNone(lock.handle)


class LedWriteTests(unittest.TestCase):
    def test_exact_bytes_static_red_logo(self):
        command = rb.led_command("logo", "static", 100, "#ff0000")
        self.assertEqual(command.hex(" "), "51 28 00 00 00 64 ff 00 00 00 00 00")
        data = rb.packet(0x03, command, 64, write=True)
        self.assertEqual((len(data), data[:13].hex(" ")), (64, "03 51 28 00 00 00 64 ff 00 00 00 00 00"))
        self.assertEqual(set(data[13:]), {0})

    def test_exact_bytes_restore_cycle_and_zones(self):
        self.assertEqual(rb.led_command("logo", "color-cycle", 50, "#ff0000").hex(" "), "51 28 00 00 02 32 ff 00 00 00 00 00")
        self.assertEqual(rb.led_command("wheel", "off", 0)[2], 1)
        self.assertEqual(rb.led_command("side", "off", 0)[2:5], bytes([2, 0, 0xF0]))

    def test_speed_byte_is_not_sent(self):
        # tested on the device: the Keris ignores it, so the allowlist keeps [9..11] at 00
        command = rb.led_command("logo", "color-cycle", 50, "#ff0000")
        self.assertEqual(command[9:], b"\0\0\0")
        self.assertFalse(rb.is_allowed_write(command[:11] + b"\x05"))

    def test_invalid_values_are_refused(self):
        for args in (("bottom", "static", 50), ("logo", "rainbow", 50), ("logo", "static", 101),
                     ("logo", "static", -1), ("logo", "static", 50, "#12345")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                rb.led_command(*args)

    def test_reads_cannot_send_writes_and_writes_need_the_flag(self):
        command = rb.led_command("logo", "static", 100, "#ff0000")
        with self.assertRaises(ValueError):
            rb.packet(0x03, command, 64)                    # write without write=True
        with self.assertRaises(ValueError):
            rb.packet(0x03, rb.MOUSE_QUERY, 64, write=True)  # a read is not a write
        tampered = command[:9] + b"\x01\x00\x00"           # direction byte set: still refused
        with self.assertRaises(ValueError):
            rb.packet(0x03, tampered, 64, write=True)
        self.assertEqual(rb.packet(0x03, rb.SAVE_COMMAND, 64, write=True)[:3].hex(" "), "03 50 03")
        with self.assertRaises(ValueError):
            rb.packet(0x03, bytes([0x50, 0x02, 0x01]), 64, write=True)   # profile switch: never

    def test_dry_run_touches_no_device(self):
        class NoBackend:
            def enumerate(self, *a):
                raise AssertionError("dry run must not enumerate")
        result = rb.set_led("logo", "static", 100, "#ff0000", dry_run=True, hid=NoBackend())
        self.assertEqual(result.status, "dry-run")
        self.assertTrue(result.sent[0].startswith("03 51 28 00 00 00 64 ff 00 00"))
        self.assertEqual(len(rb.set_led("logo", "static", 100, "#ff0000", save=True, dry_run=True).sent), 3)


class RealWriteVectorTests(unittest.TestCase):
    """protocol.json mouse_writes: the exact bytes confirmed on the device must not change."""

    def test_confirmed_writes(self):
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())["mouse_writes"]["writes"]
        for w in data:
            with self.subTest(name=w["name"]):
                a = w["args"]
                command = rb.led_command(a["zone"], a["mode"], a["brightness"], a["color"])
                head = bytes.fromhex(w["packet"])
                packet = rb.packet(0x03, command, 64, write=True)
                self.assertEqual(packet[:len(head)], head)
                self.assertEqual(set(packet[len(head):]), {0})
                self.assertTrue(rb.led_matches(a, w["read_back"]))


class RealSettingsWriteVectorTests(unittest.TestCase):
    """protocol.json mouse_writes.settings_writes: power and sensor writes confirmed on the device."""

    def test_confirmed_writes(self):
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())["mouse_writes"]["settings_writes"]
        for w in data["writes"]:
            with self.subTest(name=w["name"]):
                command = getattr(rb, w["function"])(*w["args"])
                head = bytes.fromhex(w["packet"])
                packet = rb.packet(0x03, command, 64, write=True)
                self.assertEqual(packet[:len(head)], head)
                self.assertEqual(set(packet[len(head):]), {0})


class SetLedTests(unittest.TestCase):
    """set_led against a fake mouse that keeps an LED state and answers like the real one."""

    def make(self, awake=True):
        state = {"logo": [0x02, 50, 0xFF, 0x00, 0x00]}
        sent = []

        class Dev(FakeDevice):
            def write(self, data):
                data = bytes(data)
                sent.append(data)
                cmd = data[1:]
                if not awake:
                    self.replies = [[0x03, 0xFF, 0xAA] + [0] * 61]
                elif cmd[:2] == b"\x12\x07":
                    self.replies = [settings_reply(rb.MOUSE_QUERY, {4: 80})]
                elif cmd[:3] == b"\x12\x03\x00":
                    mode, bright, r, g, b = state["logo"]
                    self.replies = [settings_reply(cmd[:3], {4: mode, 5: bright, 6: r, 7: g, 8: b})]
                elif cmd[:2] == b"\x51\x28":
                    state["logo"] = list(cmd[4:9])
                    self.replies = [settings_reply(cmd[:2], {})]
                elif cmd[:2] == b"\x50\x03":
                    self.replies = [settings_reply(cmd[:2], {})]
                return len(data)

        class Backend:
            def enumerate(self, vid, pid):
                return [{"path": b"omni", "product_id": pid, "usage_page": 0xFF00, "interface_number": 2}] \
                    if pid == rb.PID_OMNI else []

            def device(self):
                return Dev([])

        return Backend(), sent, state

    def test_write_then_read_back(self):
        backend, sent, state = self.make()
        result = rb.set_led("logo", "static", 100, "#ff0000", hid=backend)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.before, {"mode": "color-cycle", "brightness": 50, "color": "#ff0000"})
        self.assertEqual(result.after, {"mode": "static", "brightness": 100, "color": "#ff0000"})
        self.assertFalse(result.saved)
        # battery (awake?), read before, the write, read back: and no save without asking
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07", "12 03", "51 28", "12 03"])

    def test_save_only_when_asked(self):
        backend, sent, _ = self.make()
        result = rb.set_led("logo", "static", 100, "#ff0000", save=True, hid=backend)
        self.assertTrue(result.saved)
        self.assertEqual(sent[-1][1:3].hex(" "), "50 03")

    def test_asleep_mouse_gets_no_write(self):
        backend, sent, state = self.make(awake=False)
        result = rb.set_led("logo", "static", 100, "#ff0000", hid=backend)
        self.assertEqual(result.status, "asleep")
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07"])

    def test_mismatch_is_reported_and_not_saved(self):
        backend, sent, state = self.make()
        orig = backend.device

        def stubborn():
            dev = orig()
            write = dev.write

            def ignore_led(data):
                if bytes(data)[1:3] == b"\x51\x28":
                    sent.append(bytes(data))
                    dev.replies = []
                    return len(data)
                return write(data)
            dev.write = ignore_led
            return dev
        backend.device = stubborn
        result = rb.set_led("logo", "static", 100, "#ff0000", save=True, hid=backend)
        self.assertEqual(result.status, "mismatch")
        self.assertFalse(result.saved)
        self.assertNotIn("50 03", [p[1:3].hex(" ") for p in sent])


class PowerAndSensorWriteTests(unittest.TestCase):
    """51 37 (power-off + warning) and 51 31 06 / 0b (angle snapping / tuning): approved 2026-11-12."""

    def test_exact_bytes(self):
        self.assertEqual(rb.power_command(5, 20).hex(" "), "51 37 00 00 03 00 14")
        self.assertEqual(rb.power_command(None, 50).hex(" "), "51 37 00 00 ff 00 32")
        self.assertEqual(rb.power_command(1, 10).hex(" "), "51 37 00 00 00 00 0a")
        self.assertEqual(rb.angle_snapping_command(True).hex(" "), "51 31 06 00 01")
        self.assertEqual(rb.angle_snapping_command(False).hex(" "), "51 31 06 00 00")
        self.assertEqual(rb.angle_tuning_command(-20).hex(" "), "51 31 0b 00 ec ff")
        self.assertEqual(rb.angle_tuning_command(15).hex(" "), "51 31 0b 00 0f 00")
        data = rb.packet(None, rb.power_command(3, 20), 65, write=True)
        self.assertEqual((len(data), data[:8].hex(" ")), (65, "00 51 37 00 00 02 00 14"))
        self.assertEqual(set(data[8:]), {0})

    def test_invalid_values_are_refused(self):
        for call in (lambda: rb.power_command(4, 20), lambda: rb.power_command(0, 20),
                     lambda: rb.power_command(5, 25), lambda: rb.power_command(5, 0), lambda: rb.power_command(5, 60),
                     lambda: rb.angle_tuning_command(7), lambda: rb.angle_tuning_command(25)):
            with self.assertRaises(ValueError):
                call()

    def test_everything_else_stays_refused(self):
        for hex_ in ("51 31 00 00 07 00",        # DPI stage 1
                     "51 31 04 00 04",           # polling 2000 Hz: not offered for the Keris
                     "51 31 04 00 63",           # polling: index only, never the raw read value
                     "51 31 05 00 02",           # debounce
                     "51 31 09 00 01",           # active DPI stage
                     "51 31 0a 00 04",           # DPI stage count
                     "51 35 ff 00 ff 02",        # lift-off: only low/high
                     "51 35 00 00 02 00 00 1c 34 16",   # surface id 02: not an Armoury Crate preset
                     "51 35 00 00 01 00 00 1c 34",      # surface parameters cut short
                     "51 35 00 00 01 00 01 1c 34 16",   # [6] set (the echo has it, a write does not)
                     "51 31 06 00 02",           # angle snapping: only 0/1
                     "51 31 0b 00 07 00",        # angle tuning: only steps of 5
                     "51 37 00 00 05 00 14",     # power-off code 5 does not exist
                     "51 37 00 01 03 00 14",     # reserved byte set
                     "50 02 01"):                # profile switch: never
            with self.subTest(command=hex_):
                self.assertFalse(rb.is_allowed_write(bytes.fromhex(hex_)))
                with self.assertRaises(ValueError):
                    rb.packet(0x03, bytes.fromhex(hex_), 64, write=True)

    def make(self, awake=True):
        """Fake mouse on the Omni that keeps power-off code, warning, snapping and tuning like the real one."""
        state = {"off": 0x02, "warn": 20, "snap": 0, "tune": 0, "poll": 0x63}
        sent = []

        class Dev(FakeDevice):
            def write(self, data):
                data = bytes(data)
                sent.append(data)
                cmd = data[1:]
                if not awake:
                    self.replies = [[0x03, 0xFF, 0xAA] + [0] * 61]
                elif cmd[:2] == b"\x12\x07":
                    self.replies = [settings_reply(rb.MOUSE_QUERY, {4: 80, 5: state["off"], 6: state["warn"]})]
                elif cmd[:3] == b"\x12\x04\x00":
                    t = state["tune"] & 0xFFFF
                    self.replies = [settings_reply(cmd[:3], {12: state["poll"], 14: 2, 16: state["snap"], 18: t & 0xFF, 19: t >> 8})]
                elif cmd[:2] == b"\x51\x37":
                    state["off"], state["warn"] = cmd[4], cmd[6]
                    self.replies = [settings_reply(cmd[:2], {})]
                elif cmd[:3] == b"\x51\x31\x04":
                    state["poll"] = (state["poll"] & 0xF8) | cmd[4]     # the high nibble stays, like the real one
                    self.replies = [settings_reply(cmd[:2], {})]
                elif cmd[:3] == b"\x51\x31\x06":
                    state["snap"] = cmd[4]
                    self.replies = [settings_reply(cmd[:2], {})]
                elif cmd[:3] == b"\x51\x31\x0b":
                    state["tune"] = int.from_bytes(cmd[4:6], "little", signed=True)
                    self.replies = [settings_reply(cmd[:2], {})]
                elif cmd[:2] == b"\x50\x03":
                    self.replies = [settings_reply(cmd[:2], {})]
                return len(data)

        class Backend:
            def enumerate(self, vid, pid):
                return [{"path": b"omni", "product_id": pid, "usage_page": 0xFF00, "interface_number": 2}] \
                    if pid == rb.PID_OMNI else []

            def device(self):
                return Dev([])

        return Backend(), sent, state

    def test_power_write_reads_back_and_saves(self):
        backend, sent, state = self.make()
        result = rb.set_power(10, 30, save=True, hid=backend)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.before, {"power_off_minutes": 3, "low_warning": 20})
        self.assertEqual(result.after, {"power_off_minutes": 10, "low_warning": 30})
        self.assertTrue(result.saved)
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07", "12 07", "51 37", "12 07", "50 03"])
        self.assertEqual(rb.set_power(None, 30, hid=backend).after["power_off_minutes"], None)   # never

    def test_angle_writes_read_back(self):
        backend, sent, state = self.make()
        self.assertEqual(rb.set_angle_snapping(True, hid=backend).after, {"angle_snapping": True})
        result = rb.set_angle_tuning(-15, hid=backend)
        self.assertEqual((result.status, result.before, result.after),
                         ("ok", {"angle_tuning": 0}, {"angle_tuning": -15}))
        self.assertNotIn("50 03", [p[1:3].hex(" ") for p in sent])     # no save without asking

    def test_polling_write(self):
        self.assertEqual(rb.polling_command(1000).hex(" "), "51 31 04 00 03")
        self.assertEqual(rb.polling_command(125).hex(" "), "51 31 04 00 00")
        for hz in (2000, 8000, 300):
            with self.assertRaises(ValueError):
                rb.polling_command(hz)
        backend, sent, state = self.make()
        result = rb.set_polling(500, save=True, hid=backend)
        self.assertEqual((result.status, result.before, result.after, result.saved),
                         ("ok", {"polling_hz": 1000}, {"polling_hz": 500}, True))
        self.assertEqual(state["poll"], 0x62)
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07", "12 04", "51 31", "12 04", "50 03"])

    def test_mismatch_is_not_saved(self):
        backend, sent, state = self.make()
        orig = backend.device

        def stubborn():
            dev = orig()
            write = dev.write

            def ignore(data):
                if bytes(data)[1:3] == b"\x51\x37":
                    sent.append(bytes(data))
                    dev.replies = []
                    return len(data)
                return write(data)
            dev.write = ignore
            return dev
        backend.device = stubborn
        result = rb.set_power(5, 20, save=True, hid=backend)
        self.assertEqual(result.status, "mismatch")
        self.assertNotIn("50 03", [p[1:3].hex(" ") for p in sent])

    def test_asleep_mouse_gets_no_write(self):
        backend, sent, _ = self.make(awake=False)
        self.assertEqual(rb.set_angle_snapping(True, save=True, hid=backend).status, "asleep")
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07"])


class LiftoffWriteTests(unittest.TestCase):
    """51 35: lift-off low/high, keeping the surface read from 12 06 (approved 2026-11-13)."""

    def test_exact_bytes_keep_the_surface(self):
        self.assertEqual(rb.liftoff_command("high", 0x01, "1c 34 16").hex(" "), "51 35 00 00 01 01 00 1c 34 16")
        self.assertEqual(rb.liftoff_command("low", 0x0B, "1a 32 20").hex(" "), "51 35 00 00 0b 00 00 1a 32 20")
        self.assertEqual(rb.liftoff_command("high", 0xFF, None).hex(" "), "51 35 ff 00 ff 01")   # no surface

    def test_unknown_or_unread_surface_is_not_written(self):
        for args in (("high", 0x02, "1c 34 16"), ("high", None, None), ("high", 0x01, None), ("medium", 0x01, "1c 34 16")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                rb.liftoff_command(*args)

    def test_armoury_crate_captures(self):
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())["mouse_writes"]["armoury_crate"]
        captures = {c["name"]: c for c in data["captures"]}
        sheath = captures["lift-off low -> high (surface ROG Sheath)"]
        self.assertEqual(rb.packet(0x03, rb.liftoff_command("high", 0x01, "1c 34 16"), 64, write=True)[:11].hex(" "),
                         sheath["packets"][0])
        for name, (liftoff, sid, params) in {sheath["name"]: ("high", 0x01, "1c 34 16"),
                                             "surface ROG Sheath -> Balteus Qi": ("low", 0x0B, "1a 32 20")}.items():
            s = rb.MouseSettings("m", "omni")
            rb.parse_mouse_settings(s, "liftoff", bytes.fromhex(captures[name]["read_after"]).ljust(64, b"\0")[1:])
            self.assertEqual((s.liftoff, s.surface_id, s.surface_params), (liftoff, sid, params))
        presets = next(c for c in data["captures"] if "surfaces" in c)["surfaces"]
        for preset in presets:
            sid = int(preset["id"], 16)
            with self.subTest(surface=preset["name"]):
                self.assertEqual(rb.surface_name(sid), "None (no calibration)" if sid == 0xFF else preset["name"].replace(
                    " (second tile, same name)", " (2)"))
                command = rb.liftoff_command("low", sid, preset["params"])
                expected = preset.get("packet") or f"03 51 35 00 00 {preset['id']} 00 00 {preset['params']}"
                self.assertEqual(rb.packet(0x03, command, 64, write=True)[:len(command) + 1].hex(" "), expected)
        self.assertEqual(len(rb.SURFACES), len(presets) - 1)

    def make(self, surface=(0x01, b"\x1c\x34\x16"), liftoff=0, keep_surface=True):
        state = {"id": surface[0], "params": surface[1], "lift": liftoff}
        sent = []

        class Dev(FakeDevice):
            def write(self, data):
                data = bytes(data)
                sent.append(data)
                cmd = data[1:]
                if cmd[:2] == b"\x12\x07":
                    self.replies = [settings_reply(rb.MOUSE_QUERY, {4: 80, 5: 2, 6: 20})]
                elif cmd[:2] == b"\x12\x06":
                    none = state["id"] == 0xFF
                    self.replies = [settings_reply(cmd[:2], {4: 0 if none else 1, 5: 0xFF if none else 0, 6: state["id"],
                                                            7: state["lift"], 8: state["params"][0],
                                                            9: state["params"][1], 10: state["params"][2]})]
                elif cmd[:2] == b"\x51\x35":
                    state["lift"] = cmd[5]
                    if not keep_surface:          # like G-Helper's packet: the surface is gone
                        state["id"], state["params"] = 0xFF, b"\0\0\0"
                    elif cmd[2] == 0:
                        state["id"], state["params"] = cmd[4], cmd[7:10]
                    else:
                        state["id"], state["params"] = 0xFF, b"\0\0\0"
                    self.replies = [settings_reply(cmd[:2], {})]
                elif cmd[:2] == b"\x50\x03":
                    self.replies = [settings_reply(cmd[:2], {})]
                return len(data)

        class Backend:
            def enumerate(self, vid, pid):
                return [{"path": b"omni", "product_id": pid, "usage_page": 0xFF00, "interface_number": 2}] \
                    if pid == rb.PID_OMNI else []

            def device(self):
                return Dev([])

        return Backend(), sent, state

    def test_write_keeps_the_surface_reads_back_and_saves(self):
        backend, sent, state = self.make(surface=(0x0B, b"\x1a\x32\x20"))
        result = rb.set_liftoff("high", save=True, hid=backend)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.before, {"liftoff": "low", "surface_id": 0x0B, "surface_params": "1a 32 20"})
        self.assertEqual(result.after, {"liftoff": "high", "surface_id": 0x0B, "surface_params": "1a 32 20"})
        self.assertTrue(result.saved)
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07", "12 06", "51 35", "12 06", "50 03"])
        self.assertEqual(sent[2][:11].hex(" "), "03 51 35 00 00 0b 01 00 1a 32 20")

    def test_no_surface(self):
        backend, sent, state = self.make(surface=(0xFF, b"\0\0\0"))
        result = rb.set_liftoff("high", hid=backend)
        self.assertEqual((result.status, result.after["surface_id"], result.after["liftoff"]), ("ok", 0xFF, "high"))
        self.assertEqual(sent[2][:7].hex(" "), "03 51 35 ff 00 ff 01")

    def test_lost_surface_is_a_mismatch_and_not_saved(self):
        backend, sent, _ = self.make(keep_surface=False)
        result = rb.set_liftoff("high", save=True, hid=backend)
        self.assertEqual(result.status, "mismatch")
        self.assertNotIn("50 03", [p[1:3].hex(" ") for p in sent])

    def test_unknown_surface_sends_no_write(self):
        backend, sent, _ = self.make(surface=(0x02, b"\x1c\x34\x16"))
        with self.assertRaises(ValueError):
            rb.set_liftoff("high", hid=backend)
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07", "12 06"])

    def test_surface_keeps_the_liftoff(self):
        self.assertEqual(rb.surface_command(0x0B, "high").hex(" "), "51 35 00 00 0b 01 00 1a 32 20")
        self.assertEqual(rb.surface_command(rb.NO_SURFACE, "low").hex(" "), "51 35 ff 00 ff 00")
        with self.assertRaises(ValueError):
            rb.surface_command(0x02, "low")
        self.assertEqual(set(rb.SURFACE_PARAMS) - {rb.NO_SURFACE}, set(rb.SURFACES))
        backend, sent, state = self.make(liftoff=1)            # ROG Sheath, lift-off high
        result = rb.set_surface(0x14, save=True, hid=backend)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.after, {"liftoff": "high", "surface_id": 0x14, "surface_params": "37 2b 0e"})
        self.assertEqual(sent[2][:11].hex(" "), "03 51 35 00 00 14 01 00 37 2b 0e")
        self.assertTrue(result.saved)
        result = rb.set_surface(rb.NO_SURFACE, hid=backend)
        self.assertEqual(result.after, {"liftoff": "high", "surface_id": 0xFF, "surface_params": None})

    def test_unknown_surface_is_not_replaced(self):
        backend, sent, _ = self.make(surface=(0x02, b"\x1c\x34\x16"))
        with self.assertRaises(ValueError):
            rb.set_surface(0x01, hid=backend)
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07", "12 06"])

    def test_surface_presets_match_the_captured_params(self):
        data = json.loads((Path(__file__).parent / "protocol.json").read_text())["mouse_writes"]["armoury_crate"]
        presets = next(c for c in data["captures"] if "surfaces" in c)["surfaces"]
        self.assertEqual({int(s["id"], 16): s["params"] for s in presets}, rb.SURFACE_PARAMS)

    def test_dry_run_reads_but_writes_nothing(self):
        backend, sent, _ = self.make()
        result = rb.set_liftoff("high", save=True, dry_run=True, hid=backend)
        self.assertEqual(result.status, "dry-run")
        self.assertEqual([p[1:3].hex(" ") for p in sent], ["12 07", "12 06"])
        self.assertTrue(result.sent[0].startswith("03 51 35 00 00 01 01 00 1c 34 16"))
        self.assertTrue(result.sent[1].startswith("03 50 03"))


class LowBatteryNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "sub" / "notified.json"
        self.sent = []

    def run_check(self, *batteries):
        return rb.check_low_battery(list(batteries), self.state, lambda t, m: self.sent.append((t, m)))

    @staticmethod
    def mouse(pct, charging=False, warning=20, status="ok"):
        return rb.Battery("mouse", "ROG Keris II Origin", "omni", pct, charging, status, low_warning=warning)

    def test_warning_level_is_parsed_from_reply(self):
        r = mouse_reply(50)
        r[1 + 6] = 30
        self.assertEqual(rb.query(FakeDevice([r]), OMNI_MOUSE).low_warning, 30)
        k = kb_reply(50)
        k[1 + 9] = 15
        self.assertEqual(rb.query(FakeDevice([k]), OMNI_KB).low_warning, 15)

    def test_implausible_warning_level_is_none(self):
        for value in (0, 101, 255):
            r = mouse_reply(50)
            r[1 + 6] = value
            self.assertIsNone(rb.query(FakeDevice([r]), OMNI_MOUSE).low_warning)

    def test_notifies_at_device_level_not_a_fixed_one(self):
        self.assertEqual(self.run_check(self.mouse(25, warning=30)), ["mouse"])
        self.assertIn("device warning level 30%", self.sent[0][1])

    def test_no_notification_above_device_level(self):
        self.assertEqual(self.run_check(self.mouse(25, warning=20)), [])
        self.assertEqual(self.sent, [])

    def test_notifies_once_until_recovered(self):
        self.run_check(self.mouse(19))
        self.run_check(self.mouse(18))
        self.assertEqual(len(self.sent), 1)
        self.run_check(self.mouse(18, charging=True))   # charging resets
        self.run_check(self.mouse(17))
        self.assertEqual(len(self.sent), 2)

    def test_asleep_device_keeps_state(self):
        self.run_check(self.mouse(10))
        self.run_check(self.mouse(None, warning=None, status="asleep"))
        self.run_check(self.mouse(9))
        self.assertEqual(len(self.sent), 1)

    def test_falls_back_to_default_without_device_level(self):
        self.assertEqual(self.run_check(self.mouse(rb.DEFAULT_LOW_WARNING, warning=None)), ["mouse"])

    def test_corrupt_state_file_is_ignored(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("not json")
        self.assertEqual(self.run_check(self.mouse(5)), ["mouse"])


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
