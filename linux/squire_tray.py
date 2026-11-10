#!/usr/bin/env python3
"""Squire tray app for Linux: battery of the ROG keyboard and mouse in the top bar (AppIndicator).

Same features as the macOS menu bar app: one top bar entry with both devices, one menu with
battery bars, connection and warning level, low-battery notification, launch at login, a debug submenu,
refresh and quit. Reads every 60 seconds, off the main thread. Read-only: uses squire.read_all().
"""
import fcntl
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import squire
except ImportError:  # running from the repository: squire.py lives one folder up
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import squire

from squire import Battery

APP_ID = "squire-gear"
DEBUG = "--debug" in sys.argv[1:]
POLL_SECONDS = 60
# device, symbol in the top bar label, name shown before the first read
DEVICES = (("keyboard", "\u2328\ufe0e", "ROG Falchion RX LP"),
           ("mouse", "\U0001f5b1\ufe0e", "ROG Keris II Origin"))
TEXT_STYLE = "\ufe0e"    # variation selector: draw symbols as monochrome text, never as colour emoji
BOLT = "\u26a1" + TEXT_STYLE
ICON = "squire-gear-symbolic"   # shield with blade, linux/icons/
STATUS_TEXT = {
    "asleep": "Asleep / not connected",
    "no-response": "No response",
    "no-access": "No access: install the udev rule (see README)",
    "error": "Error",
}


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


SETTINGS_FILE = config_dir() / APP_ID / "settings.json"
AUTOSTART_FILE = config_dir() / "autostart" / f"{APP_ID}.desktop"


# --- pure helpers (tested without GTK) ---------------------------------------------------------------------

def warning_level(b: Battery) -> int:
    return b.low_warning if b.low_warning is not None else squire.DEFAULT_LOW_WARNING


def panel_label(b: Optional[Battery]) -> str:
    if b is None or b.percent is None:
        return "–"
    return f"{b.percent}%" + (BOLT if b.charging else "")


def panel_text(batteries: dict) -> str:
    """Top bar label for both devices, like the Mac menu bar: "⌨ 78%  🖱 80%"."""
    return "  ".join(f"{symbol} {panel_label(batteries.get(device))}" for device, symbol, _ in DEVICES)


PANEL_GUIDE = "  ".join(f"{symbol} 100%{BOLT}" for _, symbol, _ in DEVICES)  # widest label, keeps the width stable


def icon_dir() -> str:
    """Folder with the top bar icon: linux/icons in the repository, icons/ next to the installed script."""
    return str(Path(__file__).resolve().parent / "icons")


def bar(percent: int, width: int = 10) -> str:
    filled = max(0, min(width, round(percent * width / 100)))
    return "▰" * filled + "▱" * (width - filled)


def device_lines(b: Optional[Battery], fallback_name: str) -> list[str]:
    """Menu lines for one device: name, bar + percentage, connection + warning level."""
    if b is None:
        return [fallback_name, "Not found"]
    if b.percent is None:
        return [b.name, STATUS_TEXT.get(b.status, b.status)]
    state = " charging" if b.charging else (" low" if b.percent <= warning_level(b) else "")
    connection = "Wired" if b.connection == "wired" else "Omni receiver"
    return [b.name,
            f"{bar(b.percent)}  {b.percent}%{(' ' + BOLT) if b.charging else ''}{state}",
            f"{connection} · warning at {warning_level(b)}%"]


def load_settings(path: Path = SETTINGS_FILE) -> dict:
    settings = {"notify_when_low": True}
    data = squire._load_state(path)
    if isinstance(data.get("notify_when_low"), bool):
        settings["notify_when_low"] = data["notify_when_low"]
    return settings


def save_settings(settings: dict, path: Path = SETTINGS_FILE) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(squire.json.dumps(settings))
    except OSError:
        pass


def launch_command() -> str:
    """Exec line for the autostart entry, quoted for .desktop files. The packaged launcher sets SQUIRE_EXEC
    (/usr/bin/squire-tray); otherwise this interpreter and this script."""
    def quote(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'
    if os.environ.get("SQUIRE_EXEC"):
        return quote(os.environ["SQUIRE_EXEC"])
    return f"{quote(sys.executable)} {quote(str(Path(__file__).resolve()))}"


def autostart_entry() -> str:
    return ("[Desktop Entry]\nType=Application\nName=Squire\n"
            "Comment=Battery of ROG keyboard and mouse in the top bar\n"
            f"Exec={launch_command()}\nIcon={APP_ID}\nTerminal=false\nNoDisplay=true\n"
            "X-GNOME-Autostart-enabled=true\n")


def set_launch_at_login(enabled: bool, path: Path = AUTOSTART_FILE) -> bool:
    try:
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(autostart_entry())
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass
    return path.exists()


def settings_command() -> list[str]:
    """The settings window (GTK 4) runs as its own process; it sits next to this script."""
    return [sys.executable, str(Path(__file__).resolve().parent / "squire_settings.py")]


def open_settings() -> None:
    import subprocess
    try:
        # Adw.Application is single-instance: a second start raises the open window
        subprocess.Popen(settings_command(), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        print(f"squire: cannot open settings: {error}", file=sys.stderr)


def unsnap_environment() -> None:
    """Started from a snap (e.g. the VS Code terminal), GTK 3 picks up the snap's GSettings schemas and
    aborts. Restore the session's own values before GTK loads."""
    for key in [k for k in os.environ if k.endswith("_VSCODE_SNAP_ORIG")]:
        original = os.environ.pop(key)
        name = key[:-len("_VSCODE_SNAP_ORIG")]
        if original:
            os.environ[name] = original
        else:
            os.environ.pop(name, None)
    for name in ("GSETTINGS_SCHEMA_DIR", "GTK_PATH", "GTK_EXE_PREFIX", "GTK_IM_MODULE_FILE", "GIO_MODULE_DIR"):
        if "/snap/" in os.environ.get(name, ""):
            os.environ.pop(name)


def single_instance_lock():
    """Returns an open lock file, or None if Squire is already running for this user."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or squire.state_dir())
    runtime.mkdir(parents=True, exist_ok=True)
    handle = open(runtime / f"{APP_ID}.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


# --- GTK app -------------------------------------------------------------------------------------------------

def run() -> int:
    unsnap_environment()
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator, Gio, GLib, Gtk

    class Notifier:
        """org.freedesktop.Notifications over D-Bus (works without a registered .desktop app id)."""

        def __init__(self):
            self.ids: dict[str, int] = {}
            try:
                self.proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, "org.freedesktop.Notifications",
                    "/org/freedesktop/Notifications", "org.freedesktop.Notifications", None)
            except GLib.Error:
                self.proxy = None

        def post(self, title: str, body: str, key: str = "") -> None:
            if self.proxy is None:
                return squire.send_notification(title, body)
            try:
                reply = self.proxy.call_sync(
                    "Notify", GLib.Variant("(susssasa{sv}i)", (
                        "Squire", self.ids.get(key, 0), APP_ID, title, body, [],
                        {"desktop-entry": GLib.Variant("s", APP_ID), "urgency": GLib.Variant("y", 1)}, -1)),
                    Gio.DBusCallFlags.NONE, 5000, None)
                if key:
                    self.ids[key] = reply.unpack()[0]   # replace instead of stacking per device
            except GLib.Error:
                squire.send_notification(title, body)

        def status(self) -> str:
            if self.proxy is None:
                return "no notification service"
            try:
                name, vendor, version, _ = self.proxy.call_sync(
                    "GetServerInformation", None, Gio.DBusCallFlags.NONE, 2000, None).unpack()
                return f"{name} {version}"
            except GLib.Error as error:
                return f"unavailable ({error.message})"

    class DeviceMenu:
        """The menu of the top bar entry, same items as the Mac popover."""

        def __init__(self, app: "TrayApp"):
            self.app = app
            self.menu = Gtk.Menu()
            self.rows: dict[str, list[Gtk.MenuItem]] = {}
            for device, _, _ in DEVICES:
                self.rows[device] = []
                for line in range(3):
                    item = Gtk.MenuItem(label="")
                    item.set_sensitive(line == 0)
                    self.menu.append(item)
                    self.rows[device].append(item)
                self.menu.append(Gtk.SeparatorMenuItem())

            settings_item = Gtk.MenuItem(label="Settings…")
            settings_item.connect("activate", lambda _: open_settings())
            self.menu.append(settings_item)
            self.menu.append(Gtk.SeparatorMenuItem())

            self.notify_item = Gtk.CheckMenuItem(label="Notify when low")
            self.notify_item.connect("toggled", self.on_notify)
            self.login_item = Gtk.CheckMenuItem(label="Launch at login")
            self.login_item.connect("toggled", self.on_login)
            self.menu.append(self.notify_item)
            self.menu.append(self.login_item)
            self.menu.append(Gtk.SeparatorMenuItem())

            debug = Gtk.Menu()
            self.debug_status = Gtk.MenuItem(label="")
            self.debug_status.set_sensitive(False)
            self.debug_update = Gtk.MenuItem(label="")
            self.debug_update.set_sensitive(False)
            test = Gtk.MenuItem(label="Send test notification")
            test.connect("activate", lambda _: app.send_test_notification())
            reset = Gtk.MenuItem(label="Reset notification state")
            reset.connect("activate", lambda _: app.reset_notification_state())
            for item in (self.debug_status, self.debug_update, Gtk.SeparatorMenuItem(), test, reset):
                debug.append(item)
            debug_item = Gtk.MenuItem(label="Debug")
            debug_item.set_submenu(debug)
            self.menu.append(debug_item)
            self.menu.append(Gtk.SeparatorMenuItem())

            self.refresh_item = Gtk.MenuItem(label="Refresh now")
            self.refresh_item.connect("activate", lambda _: app.refresh())
            quit_item = Gtk.MenuItem(label="Quit")
            quit_item.connect("activate", lambda _: app.quit())
            self.menu.append(self.refresh_item)
            self.menu.append(quit_item)
            self.menu.show_all()
            self.updating = False

        def update(self) -> None:
            app = self.app
            self.updating = True   # programmatic set_active must not count as a click
            for device, _, fallback in DEVICES:
                lines = device_lines(app.batteries.get(device), fallback)
                if app.last_update is None:
                    lines = [fallback, "Looking for devices…"]
                for item, text in zip(self.rows[device], lines + [""] * 3):
                    item.set_label(text)
                    item.set_visible(bool(text))
            self.notify_item.set_active(app.settings["notify_when_low"])
            self.login_item.set_active(AUTOSTART_FILE.exists())
            self.refresh_item.set_sensitive(not app.refreshing)
            self.debug_status.set_label(f"Notifications: {app.notification_status}")
            self.debug_update.set_label("Last update: " + (time.strftime("%H:%M:%S", time.localtime(app.last_update))
                                                           if app.last_update else "–"))
            self.updating = False

        def on_notify(self, item) -> None:
            if not self.updating:
                self.app.settings["notify_when_low"] = item.get_active()
                save_settings(self.app.settings)
                self.app.update_ui()

        def on_login(self, item) -> None:
            if not self.updating:
                set_launch_at_login(item.get_active())
                self.app.update_ui()

    class TrayApp:
        def __init__(self):
            self.settings = load_settings()
            self.batteries: dict[str, Battery] = {}
            self.last_update: Optional[float] = None
            self.refreshing = False
            self.notifier = Notifier()
            self.notification_status = self.notifier.status()
            self.indicator = AppIndicator.Indicator.new_with_path(
                APP_ID, ICON, AppIndicator.IndicatorCategory.HARDWARE, icon_dir())
            self.indicator.set_title("Squire")
            self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.menu = DeviceMenu(self)
            self.indicator.set_menu(self.menu.menu)
            self.update_ui()
            self.refresh()
            GLib.timeout_add_seconds(POLL_SECONDS, self.on_timer)

        def on_timer(self) -> bool:
            self.refresh()
            return True

        def refresh(self) -> None:
            if self.refreshing:
                return
            self.refreshing = True
            self.update_ui()

            def worker():
                try:
                    results = squire.read_all(debug=DEBUG)
                except Exception as error:  # keep the app alive whatever the device does
                    print(f"squire: read failed: {error}", file=sys.stderr)
                    results = []
                GLib.idle_add(self.apply, results)

            threading.Thread(target=worker, name="squire-read", daemon=True).start()

        def apply(self, results: list[Battery]) -> bool:
            self.refreshing = False
            self.last_update = time.time()
            if DEBUG:
                print("squire:", [(b.device, b.percent, b.charging, b.status) for b in results], file=sys.stderr)
            self.batteries = {b.device: b for b in results}
            self.notification_status = self.notifier.status()
            if self.settings["notify_when_low"]:
                squire.check_low_battery(results, notify=self.post_low)
            self.update_ui()
            return False

        def post_low(self, title: str, body: str) -> None:
            self.notifier.post(title, body, key=title)

        def update_ui(self) -> None:
            self.indicator.set_label(panel_text(self.batteries), PANEL_GUIDE)
            self.menu.update()

        def send_test_notification(self) -> None:
            self.notifier.post("Squire test: battery low", "This is a test notification. No real battery is low.")

        def reset_notification_state(self) -> None:
            try:
                squire.STATE_FILE.unlink(missing_ok=True)
            except OSError:
                pass

        def quit(self) -> None:
            Gtk.main_quit()

    TrayApp()
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        signal_add = GLibUnix.signal_add
    except (ImportError, ValueError):
        signal_add = GLib.unix_signal_add
    for signal in (2, 15):  # SIGINT, SIGTERM
        signal_add(GLib.PRIORITY_DEFAULT, signal, lambda: Gtk.main_quit() or False)
    Gtk.main()
    return 0


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"squire-tray {squire.__version__}")
        return 0
    lock = single_instance_lock()
    if lock is None:
        print("Squire is already running.", file=sys.stderr)
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
