"""Tests for the Linux tray app's logic (linux/squire_tray.py). No GTK and no hardware needed."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / "linux"))
import squire_tray as tray  # noqa: E402
from squire import Battery  # noqa: E402


def kb(pct, charging=False, warning=20, status="ok", connection="omni"):
    return Battery("keyboard", "ROG Falchion RX LP", connection, pct, charging, status, low_warning=warning)


class LabelTests(unittest.TestCase):
    def test_panel_label(self):
        self.assertEqual(tray.panel_label(kb(78)), "78%")
        self.assertEqual(tray.panel_label(kb(78, charging=True)), "78%" + tray.BOLT)
        self.assertEqual(tray.panel_label(kb(None, status="asleep")), "–")
        self.assertEqual(tray.panel_label(None), "–")

    def test_panel_text_shows_both_devices_in_one_label(self):
        text = tray.panel_text({"keyboard": kb(78, charging=True)})
        self.assertEqual(text, f"\u2328\ufe0e 78%{tray.BOLT}  \U0001f5b1\ufe0e –")
        self.assertEqual(tray.panel_text({}), "\u2328\ufe0e –  \U0001f5b1\ufe0e –")

    def test_icon_ships_with_the_app(self):
        self.assertTrue((Path(tray.icon_dir()) / f"{tray.ICON}.svg").is_file())

    def test_bolt_is_text_style_not_emoji(self):
        self.assertTrue(tray.BOLT.endswith("︎"))

    def test_bar(self):
        self.assertEqual(tray.bar(0), "▱" * 10)
        self.assertEqual(tray.bar(78), "▰" * 8 + "▱" * 2)
        self.assertEqual(tray.bar(100), "▰" * 10)

    def test_device_lines(self):
        self.assertEqual(tray.device_lines(kb(78), "x"),
                         ["ROG Falchion RX LP", "▰▰▰▰▰▰▰▰▱▱  78%", "Omni receiver · warning at 20%"])
        self.assertIn("charging", tray.device_lines(kb(50, charging=True), "x")[1])
        self.assertIn("low", tray.device_lines(kb(20), "x")[1])            # at the device warning level
        self.assertNotIn("low", tray.device_lines(kb(25), "x")[1])
        self.assertIn("low", tray.device_lines(kb(25, warning=30), "x")[1])
        self.assertEqual(tray.device_lines(kb(50, connection="wired"), "x")[2], "Wired · warning at 20%")
        self.assertEqual(tray.device_lines(kb(50, warning=None), "x")[2], "Omni receiver · warning at 20%")

    def test_unreachable_device_lines(self):
        self.assertEqual(tray.device_lines(None, "ROG Keris II Origin"), ["ROG Keris II Origin", "Not found"])
        self.assertEqual(tray.device_lines(kb(None, status="asleep"), "x")[1], "Asleep / not connected")
        self.assertIn("udev", tray.device_lines(kb(None, status="no-access"), "x")[1])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_defaults_and_roundtrip(self):
        path = self.dir / "a" / "settings.json"
        self.assertEqual(tray.load_settings(path), {"notify_when_low": True})
        tray.save_settings({"notify_when_low": False}, path)
        self.assertEqual(tray.load_settings(path), {"notify_when_low": False})

    def test_corrupt_settings_fall_back(self):
        path = self.dir / "settings.json"
        path.write_text('{"notify_when_low": "no"}')
        self.assertEqual(tray.load_settings(path), {"notify_when_low": True})
        path.write_text("garbage")
        self.assertEqual(tray.load_settings(path), {"notify_when_low": True})

    def test_launch_at_login_writes_and_removes_entry(self):
        path = self.dir / "autostart" / "squire-gear.desktop"
        self.assertTrue(tray.set_launch_at_login(True, path))
        entry = path.read_text()
        self.assertIn("X-GNOME-Autostart-enabled=true", entry)
        self.assertIn(str(Path(tray.__file__).resolve()), entry)
        self.assertFalse(tray.set_launch_at_login(False, path))
        self.assertFalse(tray.set_launch_at_login(False, path))   # already gone: no error

    def test_packaged_launcher_is_used_for_autostart(self):
        with mock.patch.dict(os.environ, {"SQUIRE_EXEC": "/usr/bin/squire-tray"}):
            self.assertEqual(tray.launch_command(), '"/usr/bin/squire-tray"')

    def test_exec_line_quotes_special_characters(self):
        with mock.patch.object(tray.sys, "executable", '/opt/my "py"/python3'), \
                mock.patch.dict(os.environ, {"SQUIRE_EXEC": ""}):
            self.assertTrue(tray.launch_command().startswith('"/opt/my \\"py\\"/python3" "'))


class EnvironmentTests(unittest.TestCase):
    def test_unsnap_restores_session_values(self):
        env = {
            "XDG_DATA_DIRS": "/snap/code/1/usr/share:/usr/share",
            "XDG_DATA_DIRS_VSCODE_SNAP_ORIG": "/usr/share/ubuntu:/usr/share",
            "GDK_PIXBUF_MODULE_FILE": "/snap/code/1/x",
            "GDK_PIXBUF_MODULE_FILE_VSCODE_SNAP_ORIG": "",
            "GSETTINGS_SCHEMA_DIR": "/home/u/snap/code/1/.local/share/glib-2.0/schemas",
            "GTK_PATH": "/usr/lib/gtk-3.0",   # not from a snap: kept
        }
        with mock.patch.dict(os.environ, env, clear=True):
            tray.unsnap_environment()
            self.assertEqual(dict(os.environ), {"XDG_DATA_DIRS": "/usr/share/ubuntu:/usr/share",
                                                "GTK_PATH": "/usr/lib/gtk-3.0"})

    def test_single_instance(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            first = tray.single_instance_lock()
            self.assertIsNotNone(first)
            self.assertIsNone(tray.single_instance_lock())
            first.close()
            again = tray.single_instance_lock()
            self.assertIsNotNone(again)
            again.close()


class PackagingTests(unittest.TestCase):
    """linux/packaging/nfpm.yaml must match the repository and the paths the app expects."""
    ROOT = Path(__file__).parent

    def setUp(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        self.config = yaml.safe_load((self.ROOT / "linux/packaging/nfpm.yaml").read_text())
        self.contents = {(c["dst"], c.get("packager")): c for c in self.config["contents"]}

    def test_every_source_exists(self):
        for c in self.config["contents"]:
            with self.subTest(src=c["src"]):
                self.assertTrue((self.ROOT / c["src"]).is_file())
        for script in self.config["scripts"].values():
            self.assertTrue((self.ROOT / script).is_file())

    def test_tray_finds_its_icon_next_to_the_installed_script(self):
        dsts = {dst for dst, _ in self.contents}
        script = "/usr/share/squire-gear/squire_tray.py"
        self.assertIn(script, dsts)
        self.assertIn(f"{Path(script).parent}/icons/{tray.ICON}.svg", dsts)

    def test_launchers_point_to_installed_files(self):
        dsts = {dst for dst, _ in self.contents}
        for launcher in ("squire", "squire-tray", "squire-settings"):
            text = (self.ROOT / "linux/packaging" / launcher).read_text()
            target = text.split("/usr/bin/python3 ")[1].split()[0]
            self.assertIn(target, dsts)
            self.assertIn(f"/usr/bin/{launcher}", dsts)
            self.assertEqual(self.contents[(f"/usr/bin/{launcher}", None)]["file_info"]["mode"], 0o755)

    def test_udev_rule_is_the_same_file_as_install_sh_uses(self):
        self.assertEqual(self.contents[("/usr/lib/udev/rules.d/70-squire-gear.rules", None)]["src"],
                         "linux/70-squire-gear.rules")

    def test_deb_and_rpm_both_have_dependencies(self):
        for packager in ("deb", "rpm"):
            depends = " ".join(self.config["overrides"][packager]["depends"])
            self.assertIn("appindicator", depends)


if __name__ == "__main__":
    unittest.main()
