"""Editor for the sensor chip: polling rate, lift-off distance, surface, angle snapping and tuning."""
import squire


def can_edit_liftoff(s: squire.MouseSettings) -> bool:
    """Only with a surface Squire knows (or none): an unknown one could be a calibration it must not touch."""
    try:
        squire.liftoff_command("low", s.surface_id, s.surface_params)
        return s.liftoff in squire.LIFTOFF_CODES
    except ValueError:
        return False


def wrapping_list_factory(row):
    """Popover rows for a ComboRow whose items are long: wrap instead of ellipsizing, with the check mark on the
    current choice that the default list has."""
    from ..widgets import Gtk
    factory, bound = Gtk.SignalListItemFactory(), set()

    def mark(item):
        item.get_child().get_last_child().set_opacity(1 if item.get_position() == row.get_selected() else 0)

    def setup(_f, item):
        box = Gtk.Box(spacing=12)
        box.append(Gtk.Label(xalign=0, wrap=True, max_width_chars=32, width_chars=24, hexpand=True))
        box.append(Gtk.Image(icon_name="object-select-symbolic"))
        item.set_child(box)

    def bind(_f, item):
        item.get_child().get_first_child().set_label(item.get_item().get_string())
        bound.add(item)
        mark(item)

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    factory.connect("unbind", lambda _f, item: bound.discard(item))
    row.connect("notify::selected", lambda *_: [mark(i) for i in bound])
    return factory


def can_edit_surface(s: squire.MouseSettings) -> bool:
    """Only replace a surface Squire knows (or none), and only with a lift-off it can write back."""
    return s.surface_id in squire.SURFACE_PARAMS and s.liftoff in squire.LIFTOFF_CODES


# Surface choices: none first, then the presets by name
SURFACE_CHOICES = [squire.NO_SURFACE] + sorted(squire.SURFACES, key=lambda i: squire.SURFACES[i])


def build(page, group):
    """Polling rate, lift-off, surface, angle snapping and tuning (each editable only with values Squire can write
    back), and the button response (read only)."""
    from ..pages.mouse import preview_settings
    from ..widgets import Adw, Gtk, row
    s = page.settings
    p = preview_settings(s, page.pending)
    if s.polling_hz in squire.POLLING_WRITE_HZ:
        rates = sorted(squire.POLLING_WRITE_HZ)
        poll = Adw.ComboRow(title="Polling rate", subtitle="Reports per second",
                            model=Gtk.StringList.new([f"{hz} Hz" for hz in rates]))
        poll.set_selected(rates.index(p.polling_hz))
        poll.connect("notify::selected", lambda r, _p: page.on_other("polling_hz", rates[r.get_selected()]))
        group.add(poll)
    else:
        group.add(row("Polling rate", f"{s.polling_hz} Hz", f"raw 0x{s.polling_raw or 0:02x}"))
    if can_edit_liftoff(s):
        choices = list(squire.LIFTOFF_CODES)
        lift = Adw.ComboRow(title="Lift-off distance", subtitle="The surface setting stays as it is",
                            model=Gtk.StringList.new([c.capitalize() for c in choices]))
        lift.set_selected(choices.index(p.liftoff))
        lift.connect("notify::selected", lambda r, _p: page.on_other("liftoff", choices[r.get_selected()]))
        group.add(lift)
    else:
        group.add(row("Lift-off distance", (s.liftoff or "").capitalize(),
                      "Shown only: the surface is not one Squire knows, so it is left alone"))
    if can_edit_surface(s):
        # the names are long (up to 47 characters): no subtitle, so the value on the right gets the
        # room; the list wraps instead of cutting names off
        surf = Adw.ComboRow(title="Surface",
                            tooltip_text="Mouse pad preset; the lift-off stays as it is",
                            model=Gtk.StringList.new([squire.surface_name(i) for i in SURFACE_CHOICES]))
        surf.set_list_factory(wrapping_list_factory(surf))
        surf.set_selected(SURFACE_CHOICES.index(p.surface_id))
        surf.connect("notify::selected",
                     lambda r, _p: page.on_other("surface_id", SURFACE_CHOICES[r.get_selected()]))
        group.add(surf)
    else:
        group.add(row("Surface", squire.surface_name(s.surface_id) or "–",
                      "Not a preset Squire knows (a calibration?), so it is left alone"))
    if p.angle_snapping is not None:
        snap = Adw.SwitchRow(title="Angle snapping", active=p.angle_snapping,
                             subtitle="Straightens nearly horizontal and vertical movements")
        snap.connect("notify::active", lambda r, _p: page.on_other("angle_snapping", r.get_active()))
        group.add(snap)
    if p.angle_tuning is not None:
        degrees = list(squire.ANGLE_TUNING_DEGREES)
        if p.angle_tuning not in degrees:
            degrees.append(p.angle_tuning)          # shown, not offered as a write
        tune = Adw.ComboRow(title="Angle tuning", subtitle="Rotates the sensor's axes, in steps of 5°",
                            model=Gtk.StringList.new([f"{d}°" for d in degrees]))
        tune.set_selected(degrees.index(p.angle_tuning))
        tune.connect("notify::selected", lambda r, _p: page.on_other("angle_tuning", degrees[r.get_selected()])
                     if degrees[r.get_selected()] in squire.ANGLE_TUNING_DEGREES else None)
        group.add(tune)
    group.add(row("Button response", f"{s.debounce_ms} ms" if s.debounce_ms else f"index {s.debounce_index}"))
