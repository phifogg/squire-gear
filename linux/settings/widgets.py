"""GTK helpers the window, pages and editors share. Importing this loads GTK 4 + libadwaita: pages and editors import
it inside the functions that build widgets, so their logic stays importable without GTK (tests), and GTK loads only
after squire_settings.run() restored the session environment (unsnap_environment)."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402,F401

from .common import swatch_svg  # noqa: E402


def texture(svg: str):
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(svg.encode()))


def row(title: str, value: str, subtitle: str = "", suffix=None):
    r = Adw.ActionRow(title=title, subtitle=subtitle)
    r.set_subtitle_selectable(True)
    if suffix is not None:
        r.add_suffix(suffix)
    label = Gtk.Label(label=value)
    label.set_selectable(True)
    r.add_suffix(label)
    return r


def swatch(color: str):
    image = Gtk.Image.new_from_paintable(texture(swatch_svg(color)))
    image.set_pixel_size(20)
    image.set_valign(Gtk.Align.CENTER)
    return image


def color_button(color: str, **props):
    """A colour button (no alpha) showing `color` (#rrggbb); read it back with button_color()."""
    button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER, **props)
    rgba = Gdk.RGBA()
    rgba.parse(color)
    button.set_rgba(rgba)
    return button


def button_color(button) -> str:
    c = button.get_rgba()
    return "#%02x%02x%02x" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))


def brightness_scale(value: int):
    """0..100 % slider with marks at the quarters."""
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_value(value)
    scale.set_digits(0)
    scale.set_draw_value(True)
    scale.set_value_pos(Gtk.PositionType.RIGHT)
    scale.set_size_request(180, -1)
    for mark in (0, 25, 50, 75, 100):
        scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
    return scale


def card(title: str, value: str, width: int, selected: bool, on_click):
    """A callout or chip: title + short value; returns (button, value label)."""
    button = Gtk.Button(css_classes=["card", "squire-callout"] + (["selected"] if selected else []))
    button.set_size_request(width, -1)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
    value_label = Gtk.Label(label=value, xalign=0, css_classes=["dim-label", "caption"], wrap=True)
    box.append(value_label)
    button.set_child(box)
    button.connect("clicked", lambda _b: on_click())
    return button, value_label


def stage_title(name: str, battery):
    """Device name and battery line, top left on the stage (connection etc. are in the Device group)."""
    title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    title.append(Gtk.Label(label=name, xalign=0, css_classes=["title-2"]))
    if battery and battery.percent is not None:
        title.append(Gtk.Label(label=f"Battery {battery.percent} %" + (" · charging" if battery.charging else ""),
                               xalign=0, css_classes=["dim-label"]))
    return title


def plain_stage(name: str, battery, cards: list, width: int = 660):
    """The stage of a model without a drawing: name and battery, then one card per setting."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=24, margin_start=32, margin_end=32)
    box.set_size_request(width - 64, -1)
    box.append(stage_title(name, battery))
    box.append(Gtk.Label(label="Squire has no drawing of this model yet. Pick a setting:", xalign=0,
                         css_classes=["dim-label"]))
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, min_children_per_line=3,
                       column_spacing=10, row_spacing=10, homogeneous=True)
    for card_button in cards:
        flow.append(card_button)
    box.append(flow)
    return box


def chip_row(hint: str, chips: list, width: int):
    """The hint and the chips below the drawing."""
    row_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
    for chip in chips:
        row_box.append(chip)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    column.set_size_request(width, -1)
    column.append(Gtk.Label(label=hint, css_classes=["dim-label"]))
    column.append(row_box)
    return column
