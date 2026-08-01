# SPDX-License-Identifier: LGPL-3.0-only
"""Task 36 (SBK-039) — dimensioned plan PDF: the server-side floor-plan
SVG the Plan PDF report embeds.

Why this is a NEW generator, not a reuse of an existing one (per the
brief's own steer, followed here): two candidates were checked first.

  - `southbrook.room.to_svg()` (southbrook_estimating/models/
    southbrook_room.py) IS an existing server-side floor-plan SVG
    renderer, already embedded in the Signature Spec Sheet report —
    but it draws a DIFFERENT model (`southbrook.room.wall.
    cabinet_line_ids`, keyed by `position_from_left_mm` /
    `sb_width_mm`) with a flat ASSUMED cabinet depth for every
    rectangle. It has no notion of the anchor-convention corner
    footprint this task requires (a corner unit's real footprint is
    not "a strip along one wall"), and `southbrook.kitchen.design` is
    only non-authoritatively MIRRORED onto a `southbrook.room` by a
    5-minute cron (Rec D Sprint 1) — not guaranteed present for a
    throwaway/just-instantiated design.
  - The kitchen design's own live preview (`kitchen_configurator.js`
    + `static/src/js/canvas/*.esm.js`) is a client-side Three.js/WebGL
    scene — no server-side geometry to reuse at all.

So this module builds the plan straight from the PERSISTED
`cabinet_line_ids` (x_position_in / z_position_in / rotation_deg — the
anchor convention, COORDINATE_CONTRACT.md) via `kitchen_layout_engine.
footprint_from_anchor_mm` — the SAME pure primitive the collision / OOB
/ remainder checks elsewhere in kitchen_design.py already call — so a
corner cabinet's drawn footprint always matches the engine's own math.
kitchen_layout_engine.py itself is untouched (read-only LAW).

Follows the same wkhtmltopdf-safe conventions `to_svg()` documents
(hex colors only, inline `font-family` on `<text>`, no flexbox) and the
same contrast-checked house palette as `kitchen_template.
_generate_thumbnail_svg`'s `_THUMB_FILLS`.

Pure / read-only: `_generate_plan_svg` never creates or writes any
record — it only reads already-persisted fields off `self` and its
`cabinet_line_ids`. The Plan PDF report (reports/kitchen_plan_report.
xml) is the only caller.
"""
import html as html_lib

from markupsafe import Markup

from odoo import models

from odoo.addons.southbrook_estimating.models import kitchen_layout_engine

_MM_PER_IN = 25.4

# Same hex fills as kitchen_template._THUMB_FILLS (contrast-checked
# against the #F4EFE7 floor fill used here too) — one house palette,
# not a second one invented for this report.
_PLAN_FILLS = {
    "base":      "#2F5C8A",
    "tall":      "#4A3B6B",
    "corner":    "#1F6B5A",
    "appliance": "#9A5B14",
    "filler":    "#6B6257",
    "panel":     "#6B6257",
}


def _fmt_in(value):
    """Room/cabinet dimensions in inches, trailing-zero-free (120.0 ->
    "120", 25.5 -> "25.5") — used for both the dimension-line labels
    here and the header/schedule table in the QWeb template, so the
    same value always reads identically everywhere in the document."""
    return "%g" % (value or 0.0)


class SouthbrookKitchenDesignPlanReport(models.Model):
    _inherit = "southbrook.kitchen.design"

    def _generate_plan_svg(self, width_px=720, height_px=520):
        """Return a Markup-wrapped top-down, dimensioned floor-plan SVG.

        Room outline + overall width/depth dimension lines (derived
        from room_width_in/room_depth_in — never hand-placed) plus one
        rectangle per active, sized cabinet line, projected through
        footprint_from_anchor_mm so corner units render their real
        footprint rather than a nominal along-wall strip. Every
        user-supplied substring (product ref) is html-escaped before
        insertion, matching to_svg()'s own safety contract, so the
        Markup wrap here is trusted the same way.
        """
        self.ensure_one()
        room_w_in = max(self.room_width_in or 0.0, 1.0)
        room_d_in = max(self.room_depth_in or 0.0, 1.0)

        margin = 56  # room for the two dimension lines + their labels
        avail_w = max(int(width_px) - margin * 2, 1)
        avail_h = max(int(height_px) - margin * 2, 1)
        scale = min(avail_w / room_w_in, avail_h / room_d_in)
        draw_w = room_w_in * scale
        draw_h = room_d_in * scale
        off_x = (int(width_px) - draw_w) / 2
        off_y = margin + max((avail_h - draw_h) / 2, 0.0)

        def _proj(x_in, z_in):
            return (off_x + x_in * scale, off_y + z_in * scale)

        parts = [
            '<svg width="%d" height="%d" viewBox="0 0 %d %d" '
            'xmlns="http://www.w3.org/2000/svg" role="img" '
            'aria-label="Dimensioned floor plan">'
            % (int(width_px), int(height_px), int(width_px), int(height_px)),
            '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        ]

        rx0, ry0 = _proj(0.0, 0.0)
        rx1, ry1 = _proj(room_w_in, room_d_in)
        parts.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
            'fill="#F4EFE7" stroke="#3D2F22" stroke-width="2"/>'
            % (rx0, ry0, rx1 - rx0, ry1 - ry0)
        )

        lines = self.cabinet_line_ids.filtered(
            lambda l: (l.width_in or 0) > 0 and (l.depth_in or 0) > 0
        )
        # Wall cabinets hang ABOVE the floor run — drawn as a dashed
        # outline (matches kitchen_template._generate_thumbnail_svg's
        # top-view convention for the same reason) and drawn LAST so
        # the outline stays visible over whatever floor cabinet sits
        # beneath it.
        lines = lines.sorted(key=lambda l: (l.cabinet_type == "wall", l.sequence))

        for line in lines:
            cab = {
                "width_mm": (line.width_in or 0) * _MM_PER_IN,
                "depth_mm": (line.depth_in or 0) * _MM_PER_IN,
            }
            place = {
                "x": (line.x_position_in or 0) * _MM_PER_IN,
                "z": (line.z_position_in or 0) * _MM_PER_IN,
                "rotation_deg": line.rotation_deg or 0,
            }
            x0mm, x1mm, z0mm, z1mm = kitchen_layout_engine.footprint_from_anchor_mm(
                cab, place
            )
            p0 = _proj(x0mm / _MM_PER_IN, z0mm / _MM_PER_IN)
            p1 = _proj(x1mm / _MM_PER_IN, z1mm / _MM_PER_IN)
            x, y = min(p0[0], p1[0]), min(p0[1], p1[1])
            w, h = abs(p1[0] - p0[0]), abs(p1[1] - p0[1])
            fill = _PLAN_FILLS.get(line.cabinet_type, _PLAN_FILLS["base"])
            if line.cabinet_type == "wall":
                parts.append(
                    '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                    'fill="none" stroke="%s" stroke-width="1.5" '
                    'stroke-dasharray="4 2"/>' % (x, y, w, h, fill)
                )
            else:
                parts.append(
                    '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                    'fill="%s" stroke="#3D2F22" stroke-width="1"/>'
                    % (x, y, w, h, fill)
                )
            ref = (line.product_id.default_code or line.product_id.display_name or "")[:12]
            if ref:
                parts.append(
                    '<text x="%.1f" y="%.1f" text-anchor="middle" '
                    'dominant-baseline="central" '
                    'style="font-family: Helvetica, sans-serif; '
                    'font-size: 8px; fill: #FFFFFF;">%s</text>'
                    % (x + w / 2, y + h / 2, html_lib.escape(ref))
                )

        # ── Overall dimension lines — derived straight from
        # room_width_in / room_depth_in, never hand-placed. Width along
        # the top (with end ticks), depth down the left (rotated
        # label), each labelled in inches.
        dim_y = ry0 - 20
        parts.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
            'stroke="#3D2F22" stroke-width="1"/>' % (rx0, dim_y, rx1, dim_y)
        )
        for tx in (rx0, rx1):
            parts.append(
                '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                'stroke="#3D2F22" stroke-width="1"/>'
                % (tx, dim_y - 4, tx, dim_y + 4)
            )
        parts.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" '
            'style="font-family: Helvetica, sans-serif; font-size: 11px; '
            'fill: #3D2F22;">%s&quot;</text>'
            % ((rx0 + rx1) / 2, dim_y - 6, _fmt_in(room_w_in))
        )

        dim_x = rx0 - 20
        parts.append(
            '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
            'stroke="#3D2F22" stroke-width="1"/>' % (dim_x, ry0, dim_x, ry1)
        )
        for ty in (ry0, ry1):
            parts.append(
                '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                'stroke="#3D2F22" stroke-width="1"/>'
                % (dim_x - 4, ty, dim_x + 4, ty)
            )
        mid_x, mid_y = dim_x - 8, (ry0 + ry1) / 2
        parts.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" '
            'transform="rotate(-90 %.1f %.1f)" '
            'style="font-family: Helvetica, sans-serif; font-size: 11px; '
            'fill: #3D2F22;">%s&quot;</text>'
            % (mid_x, mid_y, mid_x, mid_y, _fmt_in(room_d_in))
        )

        parts.append("</svg>")
        return Markup("".join(parts))
