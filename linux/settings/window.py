"""The settings window: devices on the left, the selected device's page (stage + editor) in the middle and on the
right, the apply bar below the editor. Generic over the pages (pages/): the window reads every device, shows a
status page when one cannot be read, and runs Apply the same way for each: write every change and read it back,
save once with the last saved change and only if all of them matched, stop at the first failure.

Imports GTK: only import it from squire_settings.run(), after unsnap_environment()."""
import sys
import threading

import squire

from . import common
from .pages.keyboard import KB_ZONE_TITLES, KeyboardPage, demo_keyboard
from .pages.mouse import MousePage, demo_data
from .widgets import Adw, Gdk, Gio, GLib, Gtk

APP_ID = "io.github.phifogg.Squire.Settings"

CSS = """
.squire-stage { background: alpha(@view_fg_color, 0.03); }
.squire-hotspot { min-width: 0; min-height: 0; padding: 0; background: transparent; box-shadow: none;
  border: 2px dashed alpha(@view_fg_color, 0.45); border-radius: 12px; }
.squire-hotspot:hover { background: alpha(@accent_bg_color, 0.12); }
.squire-hotspot.selected { border: 2px solid @accent_bg_color; background: alpha(@accent_bg_color, 0.16); }
.squire-callout { padding: 8px 10px; border-radius: 8px; }
.squire-callout.selected { outline: 2px solid @accent_bg_color; outline-offset: -2px; }
.squire-line { min-height: 0; border-top: 1px dashed alpha(@view_fg_color, 0.45); }
.squire-line.selected { border-top: 1px solid @accent_bg_color; }
/* the keyboard: a large hotspot over all keys gets only an outline, no tint (a tint would look like an LED colour) */
.squire-hotspot.outline-only.selected, .squire-hotspot.outline-only:hover { background: transparent; }
.squire-vline { min-width: 0; border-left: 1px dashed alpha(@view_fg_color, 0.45); }
.squire-vline.selected { border-left: 1px solid @accent_bg_color; }
.squire-bytes { font-family: monospace; font-size: 0.9em; }
"""


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Squire", default_width=1320, default_height=800)
        self.pages = {"mouse": MousePage(self), "keyboard": KeyboardPage(self)}
        self.batteries: dict = {}
        self.device = "mouse"
        self.loading = False
        self.applying = False
        self.apply_label = None

        self.content_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read again")
        self.refresh_button.connect("clicked", lambda _: self.refresh())
        header.pack_start(self.refresh_button)
        self.content_view.add_top_bar(header)
        self.banner = Adw.Banner(title=self.pages["mouse"].banner, revealed=True)
        self.content_view.add_top_bar(self.banner)
        split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=280)
        split.set_sidebar(self.build_sidebar())
        split.set_content(Adw.NavigationPage(title="Settings", child=self.content_view))
        self.toasts = Adw.ToastOverlay(child=split)
        self.set_content(self.toasts)
        self.show_status("Reading the mouse…", "Squire reads the current settings. This takes about a second.",
                         spinner=True)
        self.refresh()

    def page(self):
        return self.pages[self.device]

    # sidebar -----------------------------------------------------------------------------------------------
    def build_sidebar(self):
        self.device_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.device_rows = {}
        for device, page in self.pages.items():
            r = Adw.ActionRow(title=page.title, subtitle="…")
            r.add_prefix(Gtk.Image(icon_name=page.icon))
            r.device = device
            self.device_list.append(r)
            self.device_rows[device] = r
        self.device_list.connect("row-selected", self.on_device)
        self.device_list.select_row(self.device_rows["mouse"])
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar(show_title=True))
        toolbar.set_content(self.device_list)
        return Adw.NavigationPage(title="Devices", child=toolbar)

    def on_device(self, _box, r):
        if r is None:
            return
        self.device = r.device
        self.render()

    def update_sidebar(self):
        for device, r in self.device_rows.items():
            b = self.batteries.get(device)
            if b is None:
                text = "not found"
            elif b.percent is None:
                text = {"asleep": "asleep", "no-access": "no access"}.get(b.status, b.status)
            else:
                text = f"{squire.connection_label(b.connection)} · {b.percent} %" + \
                       (" · charging" if b.charging else "")
            r.set_subtitle(text)

    # reading -----------------------------------------------------------------------------------------------
    def refresh(self):
        if self.loading or self.applying:
            return
        self.loading = True
        self.refresh_button.set_sensitive(False)

        def worker():
            try:
                if common.DEMO:
                    batteries, settings = demo_data()
                else:
                    batteries = {b.device: b for b in squire.read_all()}
                    settings = squire.read_mouse_settings()
            except Exception as error:  # keep the window alive whatever the device does
                print(f"squire-settings: read failed: {error}", file=sys.stderr)
                batteries, settings = {}, squire.MouseSettings("ROG Keris II Origin", "", status="error")
            try:
                keyboard = demo_keyboard() if common.DEMO else squire.read_keyboard_settings()
            except Exception as error:
                print(f"squire-settings: keyboard read failed: {error}", file=sys.stderr)
                keyboard = squire.KeyboardSettings("ROG Falchion RX LP", "", status="error")
            GLib.idle_add(self.loaded, batteries, settings, keyboard)

        threading.Thread(target=worker, name="squire-read", daemon=True).start()

    def loaded(self, batteries, settings, keyboard=None):
        self.loading = False
        self.refresh_button.set_sensitive(True)
        self.batteries = batteries
        self.pages["mouse"].load(settings)
        self.pages["keyboard"].load(keyboard, common.DEMO)
        self.update_sidebar()
        self.render()
        if common.SCREENSHOT:
            GLib.timeout_add(800, self.capture)
        return False

    def capture(self):
        """--screenshot PATH [ZONE] [edit ...]: save the window as PNG and quit (development aid)."""
        path, zone = common.SCREENSHOT[0], (common.SCREENSHOT[1:] or [None])[0]
        flags = common.SCREENSHOT[2:]
        mouse, keyboard = self.pages["mouse"], self.pages["keyboard"]
        if zone in KB_ZONE_TITLES:       # a keyboard zone: kb_light, kb_profile, kb_layout, kb_power
            if keyboard.selected != zone:
                keyboard.selected = zone
                self.render()
            zone = "keyboard"
        if zone == "keyboard" and self.device != "keyboard":
            self.device_list.select_row(self.device_rows["keyboard"])
            GLib.timeout_add(400, self.capture)
            return False
        if zone == "keyboard" and keyboard.demo_edit(flags):                       # not sent
            self.render()
            self.bytes_toggle.set_active("perkey" not in flags)   # per-key: show the editor rows
            GLib.timeout_add(600, self.capture)
            return False
        if zone and zone != "keyboard" and zone != mouse.selected:
            mouse.select(zone)
            GLib.timeout_add(400, self.capture)
            return False
        if mouse.demo_edit(zone, flags):                                             # not sent
            self.render()
            self.bytes_toggle.set_active(True)
            GLib.timeout_add(600, self.capture)
            return False
        w, h = self.get_width(), self.get_height()
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(self).snapshot(snapshot, w, h)
        self.get_renderer().render_texture(snapshot.to_node(), None).save_to_png(path)
        print(f"saved {path}", file=sys.stderr)
        self.get_application().quit()
        return False

    # content -----------------------------------------------------------------------------------------------
    def show_status(self, title, description, icon="input-mouse-symbolic", spinner=False):
        page = Adw.StatusPage(title=title, description=description)
        if spinner:
            page.set_paintable(Adw.SpinnerPaintable.new(page))
        else:
            page.set_icon_name(icon)
        self.content_view.set_content(page)

    def render(self):
        page = self.page()
        self.banner.set_title(page.banner)
        data = page.data
        if data is None:
            return
        if data.status != "ok":
            # no settings over Bluetooth (no vendor HID collection there), but the device is connected: say so
            # instead of "not found" / "asleep"
            bluetooth = common.bluetooth_status(self.batteries.get(page.kind), page.noun)
            if bluetooth:
                self.banner.set_title("Connected over Bluetooth: battery level only, no settings.")
            title, text = bluetooth or page.status_texts.get(
                data.status, (f"Could not read the {page.noun}", data.status))
            self.show_status(title, text, icon=page.icon)
            return
        page.value_labels = {}
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        stage_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                          vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, hexpand=True)
        stage = page.build_stage()
        stage.set_halign(Gtk.Align.CENTER)
        stage_scroll.set_child(stage)
        stage_scroll.add_css_class("squire-stage")
        body.append(stage_scroll)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=False)
        column.set_size_request(360, -1)
        inspector = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        inspector.set_child(page.build_inspector())
        column.append(inspector)
        column.append(Gtk.Separator())
        column.append(self.build_apply_bar())
        body.append(column)
        self.content_view.set_content(body)
        self.update_preview()

    # apply bar ---------------------------------------------------------------------------------------------
    def build_apply_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        self.apply_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, css_classes=["dim-label", "caption"])
        bar.append(self.apply_label)
        buttons = Gtk.Box(spacing=8)
        self.bytes_toggle = Gtk.ToggleButton(label="Show bytes")
        buttons.append(self.bytes_toggle)
        buttons.append(Gtk.Box(hexpand=True))
        self.revert_button = Gtk.Button(label="Revert")
        self.revert_button.connect("clicked", lambda _b: self.revert())
        self.apply_button = Gtk.Button(label="Apply", css_classes=["suggested-action"])
        self.apply_button.connect("clicked", lambda _b: self.apply_changes())
        buttons.append(self.revert_button)
        buttons.append(self.apply_button)
        bar.append(buttons)
        self.bytes_label = Gtk.Label(xalign=0, selectable=True, wrap=True, max_width_chars=40,
                                     css_classes=["squire-bytes"])
        revealer = Gtk.Revealer(child=self.bytes_label)
        self.bytes_toggle.bind_property("active", revealer, "reveal-child")
        bar.append(revealer)
        return bar

    def update_preview(self):
        """After an edit: the page's drawing and cards, then the apply bar (status, buttons, planned bytes)."""
        page = self.page()
        if page.data is None or page.data.status != "ok":
            return
        page.update_preview()
        if self.apply_label is not None:
            dirty = bool(page.changes())
            self.apply_label.set_label("Applying…" if self.applying else page.apply_status())
            self.apply_button.set_sensitive(dirty and not self.applying)
            self.revert_button.set_sensitive(dirty and not self.applying)
            lines = page.planned_packets()
            self.bytes_label.set_label("\n".join(lines) if lines else "Nothing to send.")

    def revert(self):
        self.page().revert()
        self.render()

    # Apply -------------------------------------------------------------------------------------------------
    def apply_changes(self):
        page = self.page()
        keys = page.changes() if page.data else []
        if self.applying or not keys:
            return
        self.applying = True
        self.refresh_button.set_sensitive(False)
        self.update_preview()
        work = {key: page.pending[key] for key in keys}
        saved_keys = page.saved_changes(work)

        def worker():
            # write each change, save once with the last saved one, and only if all of them matched; stop at the
            # first failure (nothing more is written and nothing is saved). Unsaved changes (per-key colours) go
            # after the saved ones.
            results = {}
            for key in keys:
                if common.DEMO:   # demo: nothing is sent
                    results[key] = squire.WriteResult("ok", saved=key in saved_keys, message="demo")
                    continue
                try:
                    results[key] = page.write(key, work[key], save=bool(saved_keys) and key == saved_keys[-1])
                except Exception as error:  # keep the window alive
                    results[key] = squire.WriteResult("error", message=str(error))
                if results[key].status != "ok":
                    break
            saved = all(key in results and results[key].status == "ok" for key in saved_keys) \
                and (not saved_keys or results[saved_keys[-1]].saved)
            for key, r in results.items():
                if key in saved_keys:
                    r.saved = saved
            GLib.idle_add(self.apply_done, page, results, work)

        threading.Thread(target=worker, name="squire-write", daemon=True).start()

    def apply_done(self, page, results, work):
        self.applying = False
        self.refresh_button.set_sensitive(True)
        failed = [(key, result) for key, result in results.items() if not page.accept(key, work[key], result)]
        if failed:
            texts = page.failure_texts
            body = "\n".join(f"{page.change_title(k)}: {texts.get(r.status, r.status + ' ' + r.message)}"
                             + (f" ({r.message})" if r.status == "ok" and r.message else "")
                             for k, r in failed)
            dialog = Adw.AlertDialog(heading="Not applied", body=body)
            dialog.add_response("ok", "OK")
            dialog.present(self)
        else:
            self.toasts.add_toast(Adw.Toast(title=page.success_title(work)
                                            + (" (demo, nothing sent)" if common.DEMO else "")))
        self.render()
        return False


class App(Adw.Application):
    def __init__(self):
        # single instance, except for --screenshot runs, which must not just raise an open window
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE if common.SCREENSHOT
                         else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        if self.window is None:
            Gtk.Window.set_default_icon_name("squire-gear")   # X11; Wayland takes it from the .desktop entry
            self.window = Window(self)
        self.window.present()
