"""Editor for per-key colours: brush, fill / off, presets kept by Squire."""
from typing import Optional

import squire


def kb_full_keys(k: "squire.KeyboardSettings", keys: dict) -> dict:
    """Every key of the layout, {key id: "#rrggbb"}; keys not in `keys` black."""
    return {key: keys.get(key, "#000000") for key in squire.keyboard_key_ids(k.iso)}


def kb_keys_start(k: "squire.KeyboardSettings", active: Optional[dict], brush: str = "#000000") -> dict:
    """Per-key colours before any edit: what this window last sent, else every key off (black)."""
    return kb_full_keys(k, active) if active else {key: brush for key in squire.keyboard_key_ids(k.iso)}


def kb_select_per_key(k: "squire.KeyboardSettings", pending: dict, active: Optional[dict], brush: str) -> dict:
    """Choosing "Per-key colors" replaces a pending effect (one or the other is sent)."""
    pending = {key: v for key, v in pending.items() if key != "kb_light"}
    pending.setdefault("kb_keys", kb_keys_start(k, active, brush))
    return pending


def kb_paint(k: "squire.KeyboardSettings", pending: dict, active: Optional[dict], changes: dict) -> dict:
    """Set some keys ({key id: "#rrggbb"}); the edit is dropped when it is back to what was last sent."""
    keys = dict(pending.get("kb_keys") or kb_keys_start(k, active, "#000000"))
    keys.update({key: squire._rgb(color) for key, color in changes.items() if key in keys})
    pending = {key: v for key, v in pending.items() if key != "kb_light"}
    if active and keys == kb_full_keys(k, active):
        pending.pop("kb_keys", None)
    else:
        pending["kb_keys"] = keys
    return pending


def kb_shown_keys(pending: dict, active: Optional[dict], per_key: bool) -> Optional[dict]:
    """The per-key colours the drawing shows, or None (the effect's glow)."""
    if "kb_keys" in pending:
        return pending["kb_keys"]
    return active if per_key and "kb_light" not in pending else None


def build(page, group):
    """Brush colour, fill / off, the preset list (Squire's defaults and the owner's) and save / delete."""
    from ..widgets import Adw, Gtk, button_color, color_button, row
    k = page.keyboard
    if page.keys_active is not None and "kb_keys" not in page.pending:
        group.add(row("On the keyboard", "Per-key colors",
                      "Sent from this window; Squire cannot tell whether the keyboard still shows them"))
    brush = Adw.ActionRow(title="Brush color",
                          subtitle="Click or drag over keys in the drawing to paint them" if page.drawing is not None
                          else "No drawing of this keyboard yet: Fill, Off and presets set the keys")
    button = color_button(page.brush)

    def on_brush(btn, _p):
        page.brush = button_color(btn)
    button.connect("notify::rgba", on_brush)
    brush.add_suffix(button)
    group.add(brush)

    fill = Adw.ActionRow(title="All keys", subtitle="Paint every key with the brush color, or turn all off")
    for label, color in (("Fill", None), ("Off", "#000000")):
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda _b, c=color: page.paint(
            {key: c or page.brush for key in squire.keyboard_key_ids(k.iso)}))
        fill.add_suffix(b)
    group.add(fill)

    presets = squire.all_key_presets(k.iso)
    names = list(presets)
    builtin = set(squire.builtin_key_presets(k.iso))
    # the chosen name goes below the title; in the list, Squire's defaults get a small second line
    choose = Adw.ComboRow(title="Preset", use_subtitle=True,
                          tooltip_text="Squire defaults, or your own saved on this computer",
                          model=Gtk.StringList.new(["–"] + names))
    factory = Gtk.SignalListItemFactory()

    def on_setup(_f, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(Gtk.Label(xalign=0))
        box.append(Gtk.Label(xalign=0, label="Squire default", css_classes=["caption", "dim-label"]))
        item.set_child(box)

    def on_bind(_f, item):
        name = item.get_item().get_string()
        box = item.get_child()
        box.get_first_child().set_label(name)
        box.get_last_child().set_visible(name in builtin)
    factory.connect("setup", on_setup)
    factory.connect("bind", on_bind)
    choose.set_list_factory(factory)
    choose.set_selected(names.index(page.preset) + 1 if page.preset in names else 0)

    def on_preset(r, _p):
        i = r.get_selected()
        if 0 < i <= len(names) and names[i - 1] != page.preset:
            page.preset = names[i - 1]
            page.paint(kb_full_keys(k, presets[page.preset]))
    choose.connect("notify::selected", on_preset)
    group.add(choose)

    manage = Adw.ActionRow(title="Manage presets")
    save = Gtk.Button(label="Save as…", valign=Gtk.Align.CENTER)
    save.connect("clicked", lambda _b: save_preset(page))
    manage.add_suffix(save)
    delete = Gtk.Button(label="Delete", valign=Gtk.Align.CENTER, css_classes=["destructive-action"],
                        sensitive=page.preset in names and page.preset not in builtin)
    delete.connect("clicked", lambda _b: delete_preset(page))
    manage.add_suffix(delete)
    group.add(manage)


def on_select(page):
    """"Per-key colors" chosen in the effect row."""
    if page.per_key_mode():
        return      # the combo was set by render(), not by the user
    page.pending = kb_select_per_key(page.keyboard, page.pending, page.keys_active, "#000000")
    page.window.render()


def save_preset(page):
    from ..widgets import Adw, Gtk
    window = page.window
    dialog = Adw.AlertDialog(heading="Save key preset",
                             body="Stored by Squire on this computer, not on the keyboard. "
                                  "The same name replaces a preset.")
    own = page.preset not in squire.builtin_key_presets(page.keyboard.iso)
    entry = Gtk.Entry(text=page.preset if page.preset and own else "", placeholder_text="Name",
                      activates_default=True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("save", "Save")
    dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("save")

    def on_response(_d, response):
        name = entry.get_text().strip()
        if response != "save" or not name:
            return
        try:
            squire.save_key_preset(name, page.current_keys())
        except (ValueError, OSError) as error:
            window.toasts.add_toast(Adw.Toast(title=f"Not saved: {error}"))
            return
        page.preset = name
        window.toasts.add_toast(Adw.Toast(title=f"Preset “{name}” saved in Squire"))
        window.render()
    dialog.connect("response", on_response)
    dialog.present(window)


def delete_preset(page):
    from ..widgets import Adw
    name = page.preset
    if not name:
        return
    dialog = Adw.AlertDialog(heading=f"Delete “{name}”?", body="Only the preset in Squire is deleted.")
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("delete", "Delete")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

    def on_response(_d, response):
        if response == "delete":
            squire.delete_key_preset(name)
            page.preset = None
            page.window.render()
    dialog.connect("response", on_response)
    dialog.present(page.window)
