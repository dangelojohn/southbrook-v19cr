# SPDX-License-Identifier: LGPL-3.0-only
"""
sale.order.line extension — Q21 zone field.

Per Q21 locked decision (Image Floor case study NF9 confirmation):
6-value selection plus a free-text zone_label that's only visible when
zone='other'. NO separate ORM model — zone is just a field on the line.

The Order Builder view in views/sale_order_views.xml groups lines by zone
via the standard `<group expand="1" string="Zone">` pattern. The
customer-facing spec sheet (Phase 1 QWeb report, custom routine #6)
also groups by zone for the print-out.
"""
import re

from odoo import api, fields, models


# Phase 3 Sprint B2 — live-compute defaults when the variant carries no
# resolved attribute dimensions (today's demo seed). These are the
# Excel-Mapping §3.3 "default" values per attribute envelope.
_SB_DEFAULT_HEIGHT_MM = 720.0   # base cabinet standard
_SB_DEFAULT_WALL_HEIGHT_MM = 760.0
_SB_DEFAULT_TALL_HEIGHT_MM = 2100.0
_SB_DEFAULT_DEPTH_MM = 580.0     # base
_SB_DEFAULT_WALL_DEPTH_MM = 310.0
_SB_DEFAULT_TALL_DEPTH_MM = 600.0
_SB_DEFAULT_WIDTH_MM = 600.0

# Width parser: matches '24"', '24 in', '24in', '24″', '24″ ' etc.
# Group 1 is the integer inches. Crucially this works on the line.name
# strings the demo seed uses ("Base 2-Door · Contemporary · ... · 30"").
_WIDTH_INCHES_RE = re.compile(r'(\d{1,3})\s*(?:"|″|in\b|in\.\b)', re.I)

# 1 inch = 25.4 mm. Cabinet widths almost always quoted in inches in
# North America; mm if European customer. mm pattern guards either way.
_WIDTH_MM_RE = re.compile(r'(\d{2,4})\s*mm\b', re.I)

# Family parser: looks at line.name OR the variant SKU for the
# standard 5 family tokens.
_FAMILY_RE = re.compile(
    r'\b(base|wall|tall|drawer|sink|vanity|pantry|island|corner)\b', re.I,
)

# Door-count parser: '1-Door', '2-Door', '2 door', '3-drawer' (drawers
# count as doors for hinge/handle quantity purposes here).
_DOOR_COUNT_RE = re.compile(
    r'(\d)\s*[-– ]\s*(?:door|drawer)', re.I,
)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    zone = fields.Selection(
        selection=[
            ("base_run", "Base Run"),
            ("wall", "Wall"),
            ("tall", "Tall"),
            ("island", "Island"),
            ("accessory", "Accessory"),
            ("other", "Other"),
        ],
        string="Zone",
        default="base_run",
        help=(
            "Which kitchen zone this line belongs to. Drives the multi-zone "
            "grid in the Order Builder backend and the customer-facing "
            "spec-sheet PDF grouping. Q21 + NF9 (Richwood pattern)."
        ),
    )
    zone_label = fields.Char(
        string="Zone Label",
        help=(
            "Free-text label, visible only when zone='other'. Captures the "
            "long tail of zone names that don't fit the 5 named zones "
            "(e.g. 'Laundry', 'Mudroom', 'Bar')."
        ),
    )

    # ------------------------------------------------------------------
    # Room-First UX Phase 1.2 — optional wall placement.
    #
    # Both fields are copy=False so NF6 version-chain duplication does
    # not propagate stale placements onto the cloned order. is_positioned
    # is a derived boolean used by the Room Layout tab to filter unplaced
    # cabinets into the sidebar list. Per design note: position 0 (the
    # wall's left corner) IS a valid placement, so positioning is gated
    # on wall_id alone — not on a position > 0 sentinel.
    # ------------------------------------------------------------------
    wall_id = fields.Many2one(
        "southbrook.room.wall", string="Wall",
        copy=False, ondelete="set null", index=True,
        help="The wall this cabinet sits against. Optional.")
    position_from_left_mm = fields.Integer(
        string="Position From Left (mm)", copy=False,
        help="Cabinet's left edge distance from the wall's left corner.")
    is_positioned = fields.Boolean(
        compute="_compute_is_positioned", store=True)

    @api.depends("wall_id")
    def _compute_is_positioned(self):
        for rec in self:
            rec.is_positioned = bool(rec.wall_id)

    # ------------------------------------------------------------------
    # BoM Preview resolver (2026-07-01 E2E audit follow-up).
    #
    # Walks the canonical Odoo BoM lookup path:
    #   1. mrp.bom._bom_find(products=variant) — the standard resolver
    #      used by MO creation. Handles variant-vs-template and
    #      company-scoping automatically.
    #   2. Fallback to a manual search for a template-level BoM
    #      (product_id=False, product_tmpl_id=variant.product_tmpl_id)
    #      in case _bom_find's contract narrows in a future upstream.
    # Never raises — returns empty recordset when no BoM exists.
    # ------------------------------------------------------------------
    @api.depends("product_id", "product_id.product_tmpl_id")
    def _compute_sb_bom_id(self):
        Bom = self.env["mrp.bom"].sudo()
        for line in self:
            variant = line.product_id
            if not variant:
                line.sb_bom_id = False
                continue
            found = False
            try:
                # `_bom_find` in v19 returns a dict {product: bom}; safe
                # to call even when no BoM exists. Guarded so a stack
                # change in the OCA layer never breaks the Order Builder.
                bom_map = Bom._bom_find(products=variant)
                found = bom_map.get(variant) if bom_map else False
            except Exception:  # noqa: BLE001
                found = False
            if not found:
                # Manual fallback — variant-specific first, then template.
                found = Bom.search([
                    ("product_id", "=", variant.id),
                    ("product_tmpl_id", "=", variant.product_tmpl_id.id),
                ], limit=1, order="sequence asc")
                if not found:
                    found = Bom.search([
                        ("product_id", "=", False),
                        ("product_tmpl_id", "=", variant.product_tmpl_id.id),
                    ], limit=1, order="sequence asc")
            line.sb_bom_id = found or False

    # ------------------------------------------------------------------
    # Phase 3 Sprint B2 — live-compute BoM rollup (option (b) from
    # docs/PHASE_3_PLAN.md). The demo seed creates variants without a
    # product.config.session, so the panel/door numbers can't be read
    # from session metadata. Instead we derive them from:
    #   1. variant's product_template_attribute_value_ids when present
    #   2. line.name fallback parse (width, family, door count)
    #   3. hardcoded family defaults from southbrook_dims constants
    # Closes the gate-walk D4 zero-rollup gap that PHASE_2_TRACK_2_GATE
    # documented as a known Phase-1 limitation.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 2026-07-01 E2E audit follow-up — BoM preview surface (brief §2.2).
    # Resolves the mrp.bom that would drive the MO if this line were
    # confirmed. Non-stored (mrp.bom set changes independently of the
    # line's own state, and the value is cheap to recompute). Used by
    # the new "BoM Preview" tab on the Order Builder form.
    # ------------------------------------------------------------------
    sb_bom_id = fields.Many2one(
        "mrp.bom",
        string="Manufacturing BoM",
        compute="_compute_sb_bom_id",
        store=False,
        help="The mrp.bom that would be spawned from this line on "
             "Confirm. Variant-scoped BoM preferred; falls back to the "
             "template-level default. Empty when no BoM is defined for "
             "this product yet.",
    )

    sb_panel_count = fields.Integer(
        string="Panel Count",
        compute="_compute_sb_panel_rollup",
        store=False,
        help="Total panel pieces in the cabinet's cutlist (sides + top + "
             "bottom + back + shelves + door). Live-computed from "
             "variant attributes when present; falls back to parsing "
             "line.name when not (demo-seed friendly).",
    )
    sb_door_count = fields.Integer(
        string="Door Count",
        compute="_compute_sb_panel_rollup",
        store=False,
        help="Number of door/drawer fronts on this cabinet line.",
    )
    sb_width_mm = fields.Float(
        string="Width (mm)",
        compute="_compute_sb_panel_rollup",
        store=False,
        digits=(8, 1),
    )

    @api.depends(
        "product_id", "product_id.product_template_attribute_value_ids",
        "name", "product_uom_qty",
    )
    def _compute_sb_panel_rollup(self):
        for line in self:
            dims = line._sb_derive_dimensions()
            family = dims["family"]
            try:
                # Local import: panel_cut_list lives in /srv/shared via
                # PYTHONPATH. Wrap so a missing shared mount degrades
                # to (0, 0, 0) rather than crashing the order view.
                from southbrook_dims import panel_cut_list
                cut = panel_cut_list(
                    dims["width_mm"], dims["height_mm"],
                    dims["depth_mm"], family=family,
                    door_count=dims["door_count"],
                )
            except Exception:  # noqa: BLE001
                line.sb_panel_count = 0
                line.sb_door_count = 0
                line.sb_width_mm = dims["width_mm"]
                continue
            # Pieces per cabinet (single-cabinet line). Sides + top +
            # bottom + back + each shelf + each door front.
            pieces = 4 + 1  # sides L + R + top + bottom + back
            pieces += int(cut.get("shelf_count") or 0)
            if cut.get("door") and dims["door_count"]:
                pieces += int(dims["door_count"])
            qty = int(line.product_uom_qty or 1)
            line.sb_panel_count = pieces * qty
            line.sb_door_count = int(dims["door_count"]) * qty
            line.sb_width_mm = dims["width_mm"]

    def _sb_derive_dimensions(self):
        """Best-effort W/H/D + family + door_count derivation.

        Reads variant attributes first (Width, Family, Door Count if any
        of them are on the variant), then falls back to parsing the
        line's `name` string, then to hardcoded family defaults.

        Returns: dict with width_mm, height_mm, depth_mm, family,
        door_count. Never raises — callers can rely on a complete dict.
        """
        self.ensure_one()
        family = None
        width_mm = None
        door_count = None
        # 1. Variant attribute values — preferred when present.
        if self.product_id:
            for ptav in self.product_id.product_template_attribute_value_ids:
                attr_name = (ptav.attribute_id.name or "").lower()
                val_name = ptav.name or ""
                if "family" in attr_name and not family:
                    m = _FAMILY_RE.search(val_name)
                    family = m.group(1).lower() if m else val_name.lower()
                elif "width" in attr_name and not width_mm:
                    # Attribute values typically look like '24"' or '600mm'.
                    m = _WIDTH_MM_RE.search(val_name)
                    if m:
                        width_mm = float(m.group(1))
                    else:
                        m = _WIDTH_INCHES_RE.search(val_name)
                        if m:
                            width_mm = float(m.group(1)) * 25.4
                elif "door" in attr_name and not door_count:
                    m = re.search(r'\d', val_name)
                    if m:
                        door_count = int(m.group(0))
        # 2. Fall back to parsing line.name for whatever is still None.
        name = self.name or ""
        if not family:
            m = _FAMILY_RE.search(name)
            family = m.group(1).lower() if m else "base"
        # Normalize synonyms to the family enum southbrook_dims uses.
        if family in ("pantry", "tall"):
            family = "tall"
        elif family in ("drawer", "base", "sink", "island", "corner"):
            family = "base"
        elif family == "vanity":
            family = "vanity"
        elif family == "wall":
            family = "wall"
        else:
            family = "base"
        if not width_mm:
            m = _WIDTH_MM_RE.search(name)
            if m:
                width_mm = float(m.group(1))
            else:
                m = _WIDTH_INCHES_RE.search(name)
                if m:
                    width_mm = float(m.group(1)) * 25.4
        if not door_count:
            m = _DOOR_COUNT_RE.search(name)
            door_count = int(m.group(1)) if m else 1
        # 3. Final family-default fallbacks for H + D.
        if family == "wall":
            height_mm = _SB_DEFAULT_WALL_HEIGHT_MM
            depth_mm = _SB_DEFAULT_WALL_DEPTH_MM
        elif family == "tall":
            height_mm = _SB_DEFAULT_TALL_HEIGHT_MM
            depth_mm = _SB_DEFAULT_TALL_DEPTH_MM
        else:
            height_mm = _SB_DEFAULT_HEIGHT_MM
            depth_mm = _SB_DEFAULT_DEPTH_MM
        return {
            "width_mm": width_mm or _SB_DEFAULT_WIDTH_MM,
            "height_mm": height_mm,
            "depth_mm": depth_mm,
            "family": family,
            "door_count": door_count or 1,
        }

    # ------------------------------------------------------------------
    # Phase 1 sales-rep UX fix (2026-07-31) — per-line rule-violation
    # flag. sale.order.sb_hard_validation_issues_html (sale_order.py)
    # gives the order-level summary; this gives the order_line list a
    # way to flag the OFFENDING row directly (decoration-danger in
    # views/sale_order_views.xml). Computed from the exact same
    # sale.order.southbrook_hard_validation_issues() scan — not a
    # second rule engine, just a per-line lookup into its result.
    # ------------------------------------------------------------------
    sb_hard_validation_message = fields.Char(
        string="Rule Violation",
        compute="_compute_sb_hard_validation_message",
        store=False,
        help=(
            "Why this line cannot be confirmed as configured. Empty "
            "when the line is clean."
        ),
    )

    @api.depends(
        "order_id.order_line.product_id",
        "order_id.order_line.product_id.product_template_attribute_value_ids",
    )
    def _compute_sb_hard_validation_message(self):
        issues_by_order = {}
        for line in self:
            order = line.order_id
            if not order:
                line.sb_hard_validation_message = False
                continue
            if order.id not in issues_by_order:
                issues_by_order[order.id] = (
                    order.southbrook_hard_validation_issues())
            messages = [
                issue["message"] for issue in issues_by_order[order.id]
                if issue["line_id"] == line.id
            ]
            line.sb_hard_validation_message = (
                " | ".join(messages) if messages else False)

    @api.onchange("zone")
    def _onchange_zone_clear_label(self):
        """Clear zone_label when leaving the 'other' zone."""
        for line in self:
            if line.zone != "other":
                line.zone_label = False

    # ------------------------------------------------------------------
    # T1C8 — Click-to-edit entry point for the OWL kitchen viewport.
    #
    # When the sales rep clicks a cabinet in the 3D Kitchen Preview
    # canvas, the OWL component raycasts the mesh to find the line id,
    # then calls this method via JSON-RPC. The returned action dict is
    # dispatched by the OWL action service — typically opening the OCA
    # configurator wizard for the line's product so the rep can
    # reconfigure the cabinet (width, family, door style, etc.).
    # ------------------------------------------------------------------
    def action_reconfigure(self):
        """Launch the OCA configurator wizard for this line.

        Returns the action dict produced by
        product.template.action_southbrook_launch_3d_configurator()
        — which itself wraps OCA's configure_product(). The wizard
        opens with the line's product pre-selected and any existing
        config_session_id reused.

        If the line has no product or no template (free-text line,
        comment, etc.), returns False so the OWL component shows a
        gentle no-op rather than crashing.
        """
        self.ensure_one()
        tmpl = (
            self.product_id.product_tmpl_id
            if self.product_id and self.product_id.product_tmpl_id
            else None
        )
        if not tmpl:
            return False
        return tmpl.action_southbrook_launch_3d_configurator()

    # ------------------------------------------------------------------
    # QA fix (2026-07-04): the per-line "Reconfigure" gear icon
    # (product_configurator_sale's reconfigure_product, upstream OCA —
    # not patched in place) always jumps straight past the Select
    # Template step into the first attribute step (Construction &
    # Sizing) via create_config_wizard(click_next=True, the default).
    # That's correct once a line has GENUINE prior config choices to
    # re-open — but a line whose product_id was set WITHOUT ever going
    # through the configurator (config_session_id is False: e.g. a
    # directly-typed-in or demo-seeded line, exactly S01331's shape)
    # has nothing of its own to "re"-configure. Jumping ahead there
    # showed either a blank or an unrelated shared session with no
    # visible way to see/change which template was implied, which QA
    # flagged as confusing.
    #
    # For that specific case only, route through the SAME "start
    # fresh" path product.template.configure_product() already uses
    # (click_next=False, product_tmpl_id_readonly=True) so the wizard
    # lands on Select Template first, same as a genuinely-fresh
    # configure — while leaving the normal re-open-existing-session
    # behavior for lines that DO have config_session_id untouched.
    #
    # Round 2 fix (2026-07-04, live-browser-diagnosed regression): the
    # first attempt still passed product_id in extra_vals (copied from
    # the base reconfigure_product's own extra_vals). That's wrong for
    # THIS branch specifically: ProductConfigurator.get_state_selection()
    # (product_configurator/wizard/product_configurator.py:108) does
    # `steps = open_steps if wiz.product_id else steps + open_steps` --
    # whenever the wizard record has product_id set, "select" is
    # dropped from the statusbar's OWN option list entirely, even
    # though the record's actual state value is still 'select'. Result:
    # the backend was 100% correct (state='select', product_tmpl_id
    # pre-filled) but the statusbar widget had no matching option to
    # highlight, so no step showed as current. configure_product() (the
    # header button, confirmed working) never sets product_id at wizard-
    # creation time, only product_tmpl_id -- mirroring that exactly
    # (dropping product_id here) is the fix. order_line_id is enough
    # for action_config_done to write the final result back to the
    # right line; the final variant is resolved from the wizard's
    # value_ids at confirm time regardless of what product_id started
    # the wizard.
    # ------------------------------------------------------------------
    def reconfigure_product(self):
        self.ensure_one()
        if self.product_id and not self.config_session_id:
            extra_vals = {
                "order_id": self.order_id.id,
                "order_line_id": self.id,
            }
            return self.with_context(
                default_order_id=self.order_id.id,
                default_order_line_id=self.id,
                product_tmpl_id_readonly=True,
            ).product_id.product_tmpl_id.create_config_wizard(
                model_name="product.configurator.sale",
                extra_vals=extra_vals,
                click_next=False,
            )
        return super().reconfigure_product()
