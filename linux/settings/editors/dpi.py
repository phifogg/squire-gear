"""Editor for the DPI stages: value + colour per stage, active stage, stage count."""
from typing import Optional

import squire

from ..pages.mouse import edit_other, preview_settings


def can_edit_dpi(s: squire.MouseSettings, stage: int) -> bool:
    """A stage with separate X/Y values is shown only: the write sets both alike."""
    i = stage - 1
    return i < len(s.dpi_xy) and i < len(s.dpi_colors) and s.dpi_xy[i][0] == s.dpi_xy[i][1]


def edit_dpi_stages(s: squire.MouseSettings, pending: dict, active: Optional[int] = None,
                    count: Optional[int] = None) -> dict:
    """Active stage or stage count. They stay consistent: a stage beyond the count raises the count, a count below
    the active stage lowers the active stage."""
    p = preview_settings(s, pending)
    new_active = active if active is not None else p.dpi_stage
    new_count = count if count is not None else p.dpi_stage_count
    if active is not None and new_active > new_count:
        new_count = new_active
    if count is not None and new_active > new_count:
        new_active = new_count
    pending = edit_other(s, pending, "dpi_stage", new_active)
    return edit_other(s, pending, "dpi_stage_count", new_count)


def build(page, group):
    """A value + colour row per stage (shown only for a stage with separate X/Y), the active stage and the count."""
    from ..widgets import Adw, Gtk, button_color, color_button, row, swatch
    s = page.settings
    p = preview_settings(s, page.pending)
    count = p.dpi_stage_count or len(p.dpi)
    stages = list(range(1, squire.DPI_STAGES + 1))
    for stage in stages:
        i = stage - 1
        if i >= len(p.dpi):
            break
        sub = "active" if p.dpi_stage == stage else "" if stage <= count else "not on the DPI button"
        if not can_edit_dpi(s, stage):
            xy = s.dpi_xy[i] if i < len(s.dpi_xy) else None
            if xy:
                sub = (sub + " · " if sub else "") + f"X {xy[0]} / Y {xy[1]}, shown only"
            color = s.dpi_colors[i] if i < len(s.dpi_colors) else "none"
            group.add(row(f"Stage {stage}", f"{p.dpi[i]} DPI", sub, suffix=swatch(color)))
            continue
        spin = Adw.SpinRow(title=f"Stage {stage}", subtitle=sub, digits=0, snap_to_ticks=True,
                           adjustment=Gtk.Adjustment(value=p.dpi[i], lower=squire.DPI_MIN,
                                                     upper=squire.DPI_MAX, step_increment=squire.DPI_STEP,
                                                     page_increment=squire.DPI_STEP * 10))
        button = color_button(p.dpi_colors[i], tooltip_text="Stage colour")

        def on_dpi(_w, _p, stage=stage, spin=spin, button=button):
            value = int(round(spin.get_value() / squire.DPI_STEP)) * squire.DPI_STEP
            page.on_other(f"dpi_{stage}", {"dpi": value, "color": button_color(button)})
        spin.connect("notify::value", on_dpi)
        button.connect("notify::rgba", on_dpi)
        spin.add_suffix(button)
        group.add(spin)
    active = Adw.ComboRow(title="Active stage", subtitle="The stage in use now",
                          model=Gtk.StringList.new([f"Stage {n}" for n in stages]))
    active.set_selected((p.dpi_stage or 1) - 1)
    active.connect("notify::selected", lambda r, _p: on_stages(page, active=r.get_selected() + 1))
    group.add(active)
    number = Adw.ComboRow(title="Stages on the DPI button", subtitle="The button cycles through these",
                          model=Gtk.StringList.new([str(n) for n in stages]))
    number.set_selected(count - 1)
    number.connect("notify::selected", lambda r, _p: on_stages(page, count=r.get_selected() + 1))
    group.add(number)


def on_stages(page, active=None, count=None):
    before = preview_settings(page.settings, page.pending)
    if (active or before.dpi_stage) == before.dpi_stage and (count or before.dpi_stage_count) == before.dpi_stage_count:
        return      # the combo was set by render(), not by the user
    page.pending = edit_dpi_stages(page.settings, page.pending, active=active, count=count)
    page.window.render()   # the other combo and the stage notes may follow
