import json
import logging
import math
import time

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
# The pure layout engine (source of truth for spatial arrangement). Imported
# here so the auto-arrange SERVICE lives on the model, not the HTTP
# controller — see COORDINATE_CONTRACT.md + the auto-arrange design doc.
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine


_logger = logging.getLogger(__name__)

# Inches -> millimetres, matching kitchen_layout_engine's metric-only
# geometry primitives (COORDINATE_CONTRACT.md). Reused by every
# _check_production_ready block below that hands persisted inch
# fields to the engine.
_MM_PER_IN = 25.4

# Task 9b (corner-remediation, 2026-07-30; review round 2) — single
# source of truth for the debris/sentinel position threshold. Shared by:
# controllers/main.py's save_design write-path guard (imports this
# constant directly — see that file's own comment), the debris-repair
# migration (migrations/19.0.5.34.1/post-migrate.py — imports this
# constant too; its own archival SQL stays self-contained, only the
# threshold VALUE is shared, per the review's explicit ask), and every
# RESET-TO-CANONICAL reactivation site that must not resurrect a debris
# line (SouthbrookKitchenDesignLine._is_debris_signature below, used by
# action_auto_arrange's reset, _transform_layout's reset, and
# controllers/main.py's save_design down-shift branch).
DEBRIS_THRESHOLD_IN = 9999

# Task 12 (corner-remediation 2026-07-30, SBK-021/022) — the ONE
# definition of the sane room-dimension range, shared by this model's
# own @api.constrains (SouthbrookKitchenDesign._check_room_bounds,
# below) and controllers/main.py's save_design pre-check, which
# duplicates the same numbers into a readable {error, error_code} JSON
# response BEFORE any create/write happens, so a bad payload never even
# reaches the ORM guard (matching the NO_CABINETS / ROOM_TOO_SMALL
# structured-error convention already used by this controller). Live
# evidence (V5-partc §SBK-021/022): room_width_in=99999 generated 8,332
# lines / ~$1.77M and persisted, because nothing anywhere on the write
# path checked a sane upper bound — `room_width_in`/`room_depth_in`/
# `room_height_in` were plain `fields.Float(required=True)` with zero
# `@api.constrains`.
#
# Deliberately a WRITE-time-only guard, never enforced on read: an
# already-persisted design above these bounds (e.g. any legacy
# room_width_in=99999 row) must keep LOADING untouched — only a NEW
# create() or a write() that touches one of these three fields is
# rejected. Odoo's @api.constrains semantics already give us this for
# free (it fires on create, and on write only when a listed field is
# part of the values dict) — no special-casing needed here.
ROOM_WIDTH_MIN_IN  = 12.0
ROOM_WIDTH_MAX_IN  = 480.0
ROOM_DEPTH_MIN_IN  = 12.0
ROOM_DEPTH_MAX_IN  = 480.0
ROOM_HEIGHT_MIN_IN = 84.0
ROOM_HEIGHT_MAX_IN = 144.0


class _BomAutoseedDryRun(Exception):
    """Internal control-flow sentinel for `_bom_autoseed_would_resolve`
    (Task 2, corner-remediation 2026-07-30) — raised unconditionally to
    force that method's `cr.savepoint()` block to roll back after a DRY
    RUN of the real `_ensure_kitchen_bom`, regardless of whether the
    seeder succeeded or raised. Never escapes that method; not a
    user-facing error."""


def _line_ref(*items):
    """Message hygiene (Task 1, corner-remediation 2026-07-30) — appends
    ' (line #<id>)' once per involved `southbrook.kitchen.design.line`
    so an issue naming a product that repeats across lines (e.g. two
    SB-WALL-CORNER lines at different junctions, V2-validator.md Claim 2)
    is still attributable to one specific record instead of only a
    display name. Accepts recordsets, bare ids, or (nested) iterables of
    either; falsy/empty args are skipped. Pure — no `self`, safe to call
    from anywhere in this module."""
    ids = []

    def _collect(item):
        if not item:
            return
        if hasattr(item, "ids"):
            ids.extend(item.ids)
        elif isinstance(item, (list, tuple, set)):
            for sub in item:
                _collect(sub)
        else:
            ids.append(item)

    for item in items:
        _collect(item)
    return "".join(" (line #%d)" % i for i in ids)


# ── Task 11 (corner-remediation 2026-07-30, SBK-024) ──────────────────────
# `southbrook_width_in` (models/product_template.py) is a single TEMPLATE-
# level Float — one number shared by every variant of that template. Every
# placement/geometry call site in this addon used to read it unconditionally,
# so a slot that resolved to e.g. SB-BASE-2DR-24 (Width PTAV = 24in) still
# placed at the template's nominal 30in. southbrook_estimating already knows
# how to read a real width pick correctly (product_config_line.py's
# `_extract_cabinet_inputs`, via `session.value_ids` for an in-progress
# config session) — this is the equivalent for an already-committed
# `product.product` variant, using the SAME dual-storage field
# (`product.attribute.value.value_inches`, southbrook_estimating/models/
# product_attribute_value.py) via the standard `product_template_
# attribute_value_ids -> product_attribute_value_id` traversal
# (mrp_bom.py:107-120 uses the identical path for lead_time_extra).
#
# Height/depth are deliberately NOT given the same treatment: verified
# (data/attributes.xml) there is no "Height" or "Depth" product.attribute
# in the catalog today — Width is the only per-variant dimension attribute.
# southbrook_height_in/southbrook_depth_in stay template-level truth.
#
# Corner-cell sizing is OUT OF SCOPE — corner footprints are owned by the
# placement-rule payloads in kitchen_layout_engine.py / the corner-specific
# write sites in this file (`_swap_derived_corner`), never this helper.
_WIDTH_FALLBACK_LOGGED_IDS = set()


def placed_width_in(product):
    """The width (inches) `product` (a `product.product` variant) should
    actually PLACE/render at.

    Precedence:
      1. The variant's own "Width" PTAV, read via the canonical
         `value_inches` field on the master `product.attribute.value`.
      2. `product.product_tmpl_id.southbrook_width_in` — the template's
         nominal default, for products with no Width pick at all (e.g.
         fillers/panels/accessories, or a config_ok template that never
         materialized a variant). Logged ONCE per product id so a
         catalog audit can find every SKU still relying on the guess.

    Accepts an empty recordset (returns 0.0, matching the pre-existing
    `tmpl.southbrook_width_in or 24.0` callers' own `or` fallback) or a
    multi-record set (uses the first record, mirroring every call site's
    existing single-product usage).
    """
    if not product:
        return 0.0
    product = product[:1]
    ptav = product.product_template_attribute_value_ids.filtered(
        lambda p: p.attribute_id.name == "Width")[:1]
    width_in = ptav.product_attribute_value_id.value_inches if ptav else 0.0
    if width_in:
        return float(width_in)
    if product.id not in _WIDTH_FALLBACK_LOGGED_IDS:
        _WIDTH_FALLBACK_LOGGED_IDS.add(product.id)
        _logger.info(
            "[placed_width_in] product %s (id=%d) carries no 'Width' "
            "attribute value (or its value_inches is unset) — falling "
            "back to template southbrook_width_in=%.2f. This message "
            "logs once per product id.",
            product.display_name, product.id,
            product.product_tmpl_id.southbrook_width_in or 0.0)
    return float(product.product_tmpl_id.southbrook_width_in or 0.0)


class SouthbrookKitchenDesign(models.Model):
    _name = "southbrook.kitchen.design"
    _description = "Southbrook Kitchen Design"
    # 2026-06-28 — kitchen_design_views.xml renders `<chatter/>`, which
    # invokes `_get_thread_with_access` from mail.thread on every
    # form open. Without these mixins the RPC hits AttributeError
    # and the form fails to load.
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "write_date desc, id desc"
    _rec_name = "name"

    # ── Archive flag ────────────────────────────────────────────────────────────
    # 2026-07-01 task #49 — enables Odoo's standard archive/unarchive UX so
    # historical designs (state=quoted/ordered) can be hidden from the
    # default list without breaking the sale.order / mrp.production audit
    # trail via unlink. tracking=True posts a chatter note on state flip
    # (mail.thread is already inherited above); ir.rule / views filter on
    # active automatically per Odoo convention.
    active = fields.Boolean(default=True, tracking=True)

    # ── Identity ────────────────────────────────────────────────────────────────
    # D4 — default kept generic; auto-rename happens at save-time in
    # the controller using the resolved partner + room dims + date so
    # the tree view stops collecting identical "New Kitchen Design"
    # rows.
    name = fields.Char(
        string="Design Name",
        required=True,
        default="New Kitchen Design",
    )
    partner_id  = fields.Many2one("res.partner",  string="Customer", index=True)
    sale_order_id = fields.Many2one(
        "sale.order", string="Quotation", readonly=True, index=True)
    notes = fields.Text(string="Design Notes")

    # ── Recommendation D · Sprint 1 bridge field ────────────────────────
    # Points at the southbrook.room record the reconciliation cron
    # mirrors this design into. In Sprint 1 the design remains the
    # authoring record and rooms are the mirror; Sprint 2 flips the
    # direction; Sprint 3 retires this model entirely and only the
    # room + sale.order.line + product.config.session graph remains.
    room_id = fields.Many2one(
        "southbrook.room",
        string="Bridged Room",
        index=True,
        ondelete="set null",
        copy=False,
        help="Rec D Sprint 1 — non-authoring mirror onto the unified "
             "room + wall + line graph. Managed by the "
             "southbrook.design.reconcile cron. Do not set manually.",
    )

    # 2026-07-01 v19.0.4.19.0 — Kanban preview thumbnail.
    # Named x_kitchen_image (not kitchen_image) to adopt the existing
    # manual custom-field column already in the runtime DB without a
    # data migration. The pre-migrate at migrations/19.0.4.19.0/
    # deletes the ir_model_fields row so this code-defined field can
    # take ownership of the same column. fields.Image auto-creates
    # the thumbnail companions (x_kitchen_image_128/256/512/1024)
    # used by the list/kanban widgets.
    x_kitchen_image = fields.Image(
        string="Kitchen Preview",
        help="Screenshot of the 3D kitchen layout for at-a-glance identification.",
        max_width=800,
        max_height=600,
        attachment=True,
        store=True,
        copy=True,
    )

    # ── Room dimensions ─────────────────────────────────────────────────────────
    room_width_in  = fields.Float(string="Room Width (in)",  default=96.0,  required=True)
    room_depth_in  = fields.Float(string="Room Depth (in)",  default=96.0,  required=True)
    room_height_in = fields.Float(string="Room Height (in)", default=96.0,  required=True)

    # Task 12 (corner-remediation 2026-07-30, SBK-021/022) — Odoo 19: the
    # current constraint API (`@api.constrains`), NOT the deprecated
    # `_sql_constraints`. Bounds are ROOM_WIDTH_MIN_IN/MAX_IN etc.
    # (module-level, above) — the same numbers controllers/main.py's
    # save_design pre-check uses for its readable {error, error_code}
    # response. Fires on create() always, and on write() only when one
    # of these three fields is in the values dict — so an already-
    # persisted out-of-bounds design (e.g. legacy room_width_in=99999)
    # keeps loading/reading fine; only a NEW create/write of the room
    # dims themselves is rejected.
    @api.constrains("room_width_in", "room_depth_in", "room_height_in")
    def _check_room_bounds(self):
        for design in self:
            if not (ROOM_WIDTH_MIN_IN <= design.room_width_in <= ROOM_WIDTH_MAX_IN):
                raise ValidationError(
                    'Room width must be between %g" and %g" (got %g").'
                    % (ROOM_WIDTH_MIN_IN, ROOM_WIDTH_MAX_IN,
                       design.room_width_in))
            if not (ROOM_DEPTH_MIN_IN <= design.room_depth_in <= ROOM_DEPTH_MAX_IN):
                raise ValidationError(
                    'Room depth must be between %g" and %g" (got %g").'
                    % (ROOM_DEPTH_MIN_IN, ROOM_DEPTH_MAX_IN,
                       design.room_depth_in))
            if not (ROOM_HEIGHT_MIN_IN <= design.room_height_in <= ROOM_HEIGHT_MAX_IN):
                raise ValidationError(
                    'Room height must be between %g" and %g" (got %g").'
                    % (ROOM_HEIGHT_MIN_IN, ROOM_HEIGHT_MAX_IN,
                       design.room_height_in))

    # ── Layout lines ────────────────────────────────────────────────────────────
    cabinet_line_ids = fields.One2many(
        "southbrook.kitchen.design.line",
        "design_id",
        string="Cabinet Layout",
        copy=True,
    )

    # ── Computed summary ────────────────────────────────────────────────────────
    # T6 (kitchen templates): total_cabinets counts CABINETS only —
    # filler strips are carried separately in filler_count. Price keeps
    # filler lines (they are real BOM-reaching lines).
    total_cabinets   = fields.Integer(compute="_compute_totals", store=True)
    filler_count     = fields.Integer(compute="_compute_totals", store=True)
    estimated_price  = fields.Monetary(compute="_compute_totals", store=True)
    base_count       = fields.Integer(compute="_compute_totals", store=True)
    wall_count       = fields.Integer(compute="_compute_totals", store=True)
    # ── Task 7 (SBK-033, corner-remediation) — ONE server-computed
    # counter summary. V4-counters-templates.md claim 1 found FOUR
    # disagreeing totals for one design (header 9, sidebar 4+2, record
    # 13, summary 10+2) because base_count/wall_count were the ONLY
    # per-type buckets anywhere — corner/tall/panel/appliance lines
    # were invisible to every count surface even though they're real,
    # priced, active lines. These five fields complete the partition:
    # every cabinet_type value now has its own bucket, computed the
    # SAME way base_count/wall_count always have (inclusive of
    # is_unresolved GHOST lines — test_invariant_matrix.py's existing
    # oracle already pins that shape for base_count/wall_count; a
    # carve-out here would silently break it). Because cabinet_type is
    # a closed Selection (base/wall/tall/filler/panel/corner/
    # appliance), every active line has EXACTLY one of these seven
    # buckets, so base+wall+tall+corner+filler+panel+appliance always
    # equals len(active cabinet_line_ids) by construction — see
    # summary_total below and _get_counter_summary()'s docstring.
    tall_count       = fields.Integer(compute="_compute_totals", store=True)
    corner_count     = fields.Integer(compute="_compute_totals", store=True)
    panel_count      = fields.Integer(compute="_compute_totals", store=True)
    appliance_count  = fields.Integer(compute="_compute_totals", store=True)
    # Diagnostic OVERLAY, not an eighth additive bucket: counts how
    # many of the lines already tallied above (under their own
    # cabinet_type bucket) are is_unresolved GHOST placeholders (Task
    # 2) — e.g. "4 base cabinets, 1 still unresolved". Archived
    # debris/sentinel lines (_is_debris_signature) never reach here at
    # all — they're active=False, already excluded from
    # cabinet_line_ids by Odoo's default active_test, same as every
    # other bucket above.
    unplaced_count   = fields.Integer(compute="_compute_totals", store=True)
    # base+wall+tall+corner+filler+panel+appliance — the honest "how
    # many rows will I see" total, INCLUDING fillers. Deliberately
    # separate from the legacy total_cabinets field (unchanged below —
    # T6's filler-exclusion is a shipped decision, not touched here).
    summary_total    = fields.Integer(compute="_compute_totals", store=True)
    remainder_in     = fields.Float(
        string="Remainder (in)",
        compute="_compute_totals",
        store=True,
        digits=(6, 2),
        help="Unused wall space after filling with standard 24-in modules.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    # ── D8 / SBK-026 — Wall-cabinet Z alignment + soffit ────────────────────────
    soffit_height_in = fields.Float(
        string="Soffit Height (in)",
        default=0.0,
        digits=(6, 2),
        help=(
            "Bottom-of-soffit height (typical: 84\" for an 8' ceiling with a "
            "12\" soffit drop). Only consulted when wall_cab_top_alignment is "
            "'to_soffit'. 0 = unset — 'Up to soffit' is GATED until this is "
            "set to a positive value (SBK-026); it falls back to the fixed-"
            "gap height in the meantime rather than silently mispositioning "
            "the wall run."
        ),
    )
    wall_cab_top_alignment = fields.Selection(
        selection=[
            ("fixed_gap",  "Fixed 18\" gap above counter"),
            ("to_ceiling", "Up to ceiling"),
            ("to_soffit",  "Up to soffit"),
        ],
        string="Wall Cabinet Top",
        default="fixed_gap",
        help=(
            "Drives the wall cabinet's Y position (bottom-of-cab elevation).\n"
            "Fixed gap: industry standard 18\" between counter and wall-cab bottom.\n"
            "Up to ceiling: wall cab top touches ceiling (tall uppers).\n"
            "Up to soffit: wall cab top touches the soffit drop (8' ceiling "
            "specials) — requires Soffit Height to be set above 0."
        ),
    )
    # SBK-026 — countertop thickness feeding the fixed-gap formula
    # (base carcass + counter thickness + 18" gap). Previously a bare
    # local constant (1.5) in both controllers/main.py's /layout route
    # and this model's production-readiness check; now a real
    # design-level setting so a non-standard counter material (thicker
    # butcher block, thinner laminate) actually moves the wall run.
    counter_thickness_in = fields.Float(
        string="Counter Thickness (in)",
        default=1.5,
        digits=(6, 2),
        help=(
            "Countertop thickness used by the Fixed-gap Wall Cabinet Top "
            "formula: base carcass height + counter thickness + 18\" gap = "
            "wall cabinet bottom. Standard laminate/quartz is 1.5\"."
        ),
    )
    # SBK-026 — live helper text next to the Wall Cabinet Top selector,
    # read-only, always in sync with the SAME geometry
    # (_wall_cab_top_geometry) the auto-arrange feed and the
    # production-readiness check consult, so the form can never show a
    # number that disagrees with what a regenerate would actually write.
    wall_cab_bottom_in = fields.Float(
        string="Wall Cabinet Bottom (in)",
        compute="_compute_wall_cab_bottom_in",
        digits=(6, 2),
        help="Resulting bottom-of-wall-cabinet height for the current "
             "Wall Cabinet Top selection. Applies on the next "
             "Auto-arrange / regenerate — existing saved positions are "
             "not migrated in place.",
    )
    wall_cab_bottom_helper = fields.Char(
        string="Wall Cabinet Top Preview",
        compute="_compute_wall_cab_bottom_in",
        help="Human-readable preview of _compute_wall_cab_bottom_in, for "
             "display next to the Wall Cabinet Top selector.",
    )

    @api.depends("wall_cab_top_alignment", "soffit_height_in",
                 "counter_thickness_in", "room_height_in",
                 "cabinet_line_ids.cabinet_type", "cabinet_line_ids.height_in")
    def _compute_wall_cab_bottom_in(self):
        for design in self:
            geo = design._wall_cab_top_geometry()
            design.wall_cab_bottom_in = round(geo["y_in"], 2)
            top_in = geo["y_in"] + geo["wall_h"]
            if geo["alignment"] == "to_ceiling":
                mode_label = "up to ceiling"
            elif geo["alignment"] == "to_soffit":
                mode_label = "up to soffit"
            else:
                mode_label = "%.1f\" gap above counter" % geo["gap"]
            gated_note = (
                " (soffit height not set — using fixed-gap height)"
                if geo["soffit_gated_off"] else ""
            )
            design.wall_cab_bottom_helper = (
                "Bottom of cabinet: %.2f\" · Top: %.2f\" (%s)%s"
                % (geo["y_in"], top_in, mode_label, gated_note)
            )

    def _wall_cab_top_geometry(self):
        """Shared SBK-026 geometry: the Wall Cabinet Top selector resolved
        into an actual bottom-of-wall-cabinet elevation (inches), plus the
        inputs that produced it.

        Single source of truth for THREE consumers that must never
        disagree: `_wall_cab_zone_layout` (feeds the engine's zone table
        for action_auto_arrange / _place_lines_on_wall), the D12
        production-readiness soffit/ceiling check, and the live helper
        text on the Room panel.

        base_h / wall_h default to the standard 34.5"/30" carcass —
        matching kitchen_layout_engine._CORNER_HEIGHT_MM and the
        controller's /layout route — but widen to the tallest ACTUAL
        base/wall cabinet already on this design, same convention the
        pre-existing soffit/ceiling check used.

        `to_soffit` is GATED (SBK-026 item 3): with soffit_height_in <= 0
        it silently resolves as `fixed_gap` instead of collapsing the
        wall run to the industry-minimum clearance at Y=0-ish, which
        would read as a broken layout rather than an unset setting.
        """
        self.ensure_one()
        base_h, wall_h = 34.5, 30.0
        for dl in self.cabinet_line_ids:
            if dl.cabinet_type == "base":
                base_h = max(base_h, dl.height_in or base_h)
            elif dl.cabinet_type == "wall":
                wall_h = max(wall_h, dl.height_in or wall_h)

        ctr_t = self.counter_thickness_in or 1.5
        gap = 18.0
        min_in = base_h + ctr_t + gap
        soffit_in = self.soffit_height_in or 0.0
        requested = self.wall_cab_top_alignment or "fixed_gap"
        soffit_gated_off = requested == "to_soffit" and soffit_in <= 0

        if requested == "to_ceiling":
            alignment = "to_ceiling"
            eff_ceiling = self.room_height_in or 0.0
            y_in = max(min_in, eff_ceiling - wall_h)
        elif requested == "to_soffit" and soffit_in > 0:
            alignment = "to_soffit"
            eff_ceiling = soffit_in
            y_in = max(min_in, soffit_in - wall_h)
        else:
            # fixed_gap, or to_soffit gated off for lack of a soffit height.
            alignment = "fixed_gap"
            eff_ceiling = self.room_height_in or 0.0
            y_in = min_in

        return {
            "base_h": base_h, "wall_h": wall_h, "ctr_t": ctr_t, "gap": gap,
            "alignment": alignment, "y_in": y_in,
            "eff_ceiling": eff_ceiling, "soffit_gated_off": soffit_gated_off,
        }

    def _wall_cab_zone_layout(self):
        """Zone-layout override honouring this design's Wall Cabinet Top
        selector (SBK-026).

        `sale.order._ZONE_LAYOUT` is the single class-level source of
        truth for every OTHER zone (base_run/tall/island/accessory/
        other) and stays untouched — its "wall" entry (1400mm) is left
        in place as the module-level FALLBACK default for any caller
        that doesn't go through a `kitchen.design` (e.g. a bare
        `kitchen_layout_engine` unit test, or `sale.order`'s own
        line-based 3D payload, neither of which has a Wall Cabinet Top
        setting to honour). This method returns a COPY with only the
        "wall" entry's y_floor replaced by the resolved value, computed
        at the CALLER per the corner-remediation brief — the engine
        itself (kitchen_layout_engine.py) is unmodified.
        """
        self.ensure_one()
        SaleOrder = self.env["sale.order"]
        zone_layout = dict(SaleOrder._ZONE_LAYOUT)
        cursor_name, _default_y_mm, z_offset = zone_layout["wall"]
        y_in = self._wall_cab_top_geometry()["y_in"]
        zone_layout["wall"] = (cursor_name, y_in * _MM_PER_IN, z_offset)
        return zone_layout

    # ── D7 — Filler strategy ────────────────────────────────────────────────────
    filler_strategy = fields.Selection(
        selection=[
            ("split",  "Split (both ends)"),
            ("right",  "Right side"),
            ("left",   "Left side"),
            ("scribe", "Scribe (no filler)"),
        ],
        string="Filler Strategy",
        default="split",
        help=(
            "How to distribute the un-modular remainder across the cabinet run.\n"
            "Split: two half-width fillers, one at each end (most common).\n"
            "Right/Left: one filler at the chosen end.\n"
            "Scribe: no filler — the carpenter scribes the end cabinet on site."
        ),
    )

    # ── Workflow ────────────────────────────────────────────────────────────────
    state = fields.Selection(
        selection=[
            ("draft",      "Draft"),
            ("configured", "Configured"),
            ("quoted",     "Quoted"),
            ("ordered",    "Ordered"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )

    # ── default_get — portal-user partner autofill ─────────────────────────────
    # 2026-07-01 task #48 — pre-select partner_id for two entry paths:
    #   (a) partner-form "Create Kitchen Design" → context carries
    #       default_partner_id already; we just honour it explicitly here
    #       so the field pre-fills without a computed default.
    #   (b) portal-user opens /my/ frontend → self.env.user.share is True
    #       and their own partner is the only sensible pre-fill (they
    #       cannot design for someone else through the portal).
    # Internal users hit neither branch — vals["partner_id"] stays empty
    # so the customer picker (Agent C's canvas) drives selection instead.
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if "partner_id" in fields_list and not vals.get("partner_id"):
            ctx_partner = self.env.context.get("default_partner_id")
            if ctx_partner:
                vals["partner_id"] = ctx_partner
            elif self.env.user.share:
                # Portal user (share=True) — auto-set their own partner
                vals["partner_id"] = self.env.user.partner_id.id
        return vals

    # ── unlink guard — protect quoted / ordered designs ────────────────────────
    # 2026-07-01 task #49 — prevent accidental data loss. Designs linked
    # to a sale.order or mrp.production must remain queryable for
    # auditability; users archive instead of delete.
    def unlink(self):
        protected = self.filtered(lambda d: d.state in ("quoted", "ordered"))
        if protected:
            raise UserError(
                "Cannot delete kitchen design(s): %s\n\n"
                "Designs with state 'quoted' or 'ordered' must be archived "
                "instead. Use the ⚙️ Actions menu → Archive to hide them from "
                "the default list. Archived designs remain linked to their "
                "sale.order and mrp.production records for auditability."
                % ", ".join(protected.mapped("name"))
            )
        return super().unlink()

    # ── Bulk archive action ────────────────────────────────────────────────────
    # 2026-07-01 task #49 — wired to list-view multi-select bulk action
    # by Agent B (views/kitchen_design_views.xml). Flips active=False in
    # a single write() and reloads so the archived rows drop off the
    # (active=True) default filter.
    def action_archive_bulk(self):
        self.write({"active": False})
        return {"type": "ir.actions.client", "tag": "reload"}

    # ── Recent-partners picker feed ────────────────────────────────────────────
    # 2026-07-01 task #48 — feeds the 3D canvas customer picker's
    # "Recently used" autocomplete section. Sourced from the current
    # user's own recent designs (order=write_date desc) so the rep sees
    # partners they've been working with lately without having to re-type
    # partner names. Invoked from Agent C's territory via RPC.
    # v19.0.5.6.7 — public name (no leading underscore) so the method is
    # reachable via /web/dataset/call_kw from the 3D canvas. Odoo 17+ blocks
    # RPC to `_`-prefixed methods as a security convention.
    @api.model
    def get_recent_partners_for_picker(self, limit=10):
        """Feed the 3D canvas customer picker with a recency-sorted list."""
        # Own designs first
        own_designs = self.search([
            ("create_uid", "=", self.env.uid),
            ("partner_id", "!=", False),
        ], limit=50, order="write_date desc")
        partners = own_designs.mapped("partner_id")
        # Deduplicate while preserving order
        seen, out = set(), []
        for p in partners:
            if p.id not in seen:
                seen.add(p.id)
                out.append({
                    "id": p.id,
                    "name": p.name,
                    "channel": getattr(p, "channel", None) or "",
                })
            if len(out) >= limit:
                break
        return out

    # ── Walk-in customer partner ───────────────────────────────────────────────
    # 2026-07-01 task #48 — anonymous / walk-in quote support.
    # v19.0.5.6.7 — public name for RPC accessibility (see get_recent_partners
    # comment above).
    #
    # SBK-031 fix (P0, corner-remediation 2026-07-30) — this method is
    # reached via TWO different dispatch paths that Odoo's ORM treats
    # differently even though it stays `@api.model`:
    #
    #   1. JS call_kw (kitchen_configurator.js `_useWalkInCustomer`, the
    #      3D-canvas customer picker) — sends `args: []`. `call_kw`
    #      (odoo/service/model.py) sees `_api_model=True` and hands the
    #      bare, id-less recordset straight to `method(recs)` — no extra
    #      positional argument. This path just wants the partner id back
    #      so JS can `_setCustomer()` client-side.
    #
    #   2. The form "Walk-in" object button (kitchen_design_views.xml,
    #      `name="get_or_create_walkin_partner" type="object"`) — Odoo's
    #      button dispatch ALWAYS sends `args: [[active_id, ...]]`
    #      regardless of the target method's decorator (see
    #      addons/web/static/src/webclient/actions/action_service.js
    #      `doActionButton`: `let args = params.resId ? [[params.resId]]
    #      : [params.resIds]`). Because this method stays `@api.model`,
    #      `call_kw` does NOT strip that array off as "ids to browse" the
    #      way it would for a non-`@api.model` method — it forwards it
    #      untouched as a second positional argument. The old
    #      `def get_or_create_walkin_partner(self):` only accepted
    #      `self`, so every click crashed with "takes 1 positional
    #      argument but 2 were given" (SBK-031, 100% repro in prod).
    #
    #      Accepting that array as an optional `design_ids` parameter
    #      reconciles both dispatch paths without touching the JS call
    #      site or the button's `name=`/`type=` attributes. When present,
    #      the button path also ASSIGNS the resolved partner to the
    #      calling design(s) and reloads the form — the user is never
    #      told to go pick a partner manually. Never overwrites a design
    #      that already has a real partner_id (review fix, 2026-07-30) —
    #      the button's `invisible="partner_id"` only hides it
    #      client-side and this stays reachable by any call_button, so a
    #      stale form / double-click / crafted call must not silently
    #      replace a real customer with the walk-in one.
    #
    # Partner resolution — reuse, never duplicate. `southbrook_estimating`
    # demo data already seeds a real "Walk-in Retail" business partner
    # (demo/southbrook_demo_partners.xml, channel=retail, for the Q7
    # smoke-test customer-switch). Search for that name first; fall back
    # to the legacy "Walk-in Customer" name this method used to mint, so
    # any pre-existing showroom data keeps resolving to the same record.
    # Only mint a brand-new partner — named "Walk-in Retail" going
    # forward, matching the real business record and the
    # channel="Retail (Walk-in)" label — when NEITHER already exists.
    @api.model
    def get_or_create_walkin_partner(self, design_ids=None):
        Partner = self.env["res.partner"]
        walkin = Partner.search([("name", "=", "Walk-in Retail")], limit=1)
        if not walkin:
            walkin = Partner.search([("name", "=", "Walk-in Customer")], limit=1)
        if not walkin:
            walkin = Partner.create({
                "name": "Walk-in Retail",
                "is_company": True,
                "customer_rank": 1,
                "channel": "retail",
                "comment": "Generic partner for showroom walk-in quotes. "
                           "Replace with the real customer once identified.",
            })

        # Object-button path: `design_ids` is the `[[active_id, ...]]`
        # array Odoo's button dispatch always sends. Assign + reload
        # instead of returning a bare id the user can't act on.
        if design_ids:
            ids = (list(design_ids)
                   if isinstance(design_ids, (list, tuple))
                   else [design_ids])
            designs = self.env[self._name].browse(ids).exists()
            if designs:
                # Never clobber a REAL customer already on the design.
                # The button is only `invisible="partner_id"`
                # client-side (kitchen_design_views.xml) — this method
                # stays public-for-RPC, so a stale form, a double-click
                # racing the post-assign reload, or a crafted
                # call_button against a design that already carries a
                # real customer must not silently overwrite it with
                # the walk-in partner. Designs that already have a
                # partner are left untouched; still reload (a no-op
                # for them) rather than fall through to returning a
                # bare id the button dispatch can't act on.
                assignable = designs.filtered(lambda d: not d.partner_id)
                if assignable:
                    assignable.write({"partner_id": walkin.id})
                return {"type": "ir.actions.client", "tag": "reload"}

        return walkin.id

    # ── Open linked Sale Order (Agent B smart button target) ───────────────────
    # 2026-07-01 task #48 — wire target for the "Quote" oe_stat_button on the
    # design form. If sale_order_id is unset returns a warning notification
    # instead of a broken action.
    def action_open_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return {
                "type":  "ir.actions.client",
                "tag":   "display_notification",
                "params": {
                    "type":    "info",
                    "title":   "No quotation yet",
                    "message": "This design has no linked Sale Order. Click "
                               "'Create Quotation' or 'Create & Confirm →' to "
                               "create one.",
                    "sticky": False,
                },
            }
        return {
            "type":      "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id":    self.sale_order_id.id,
            "views":     [(False, "form")],
            "view_mode": "form",
            "target":    "current",
        }

    # ── Reset stale quote link (safety valve) ──────────────────────────────────
    # 2026-07-02 — Design #15 currently points at S01314, a stale draft
    # from an earlier failed attempt. Re-clicking "Create Quotation" or
    # "Create & Confirm →" today would try to reuse that link's
    # downstream logic. This provides a rep-visible unlink action that
    # clears sale_order_id (design falls back to state=configured) so
    # the design can be re-quoted. Ordered designs are protected — they
    # must stay linked for the manufacturing/delivery audit trail.
    def action_reset_quote_link(self):
        """Clear the sale_order_id link and revert state to configured.

        Used when a prior "Create Quotation" left the design pointing at
        a stale draft SO the rep decided not to pursue. Does NOT delete
        the underlying sale.order — that's a separate action from the SO
        surface.
        """
        self.ensure_one()
        if self.state == "ordered":
            raise UserError(
                "Cannot reset a design that has been ordered. Ordered "
                "designs must remain linked to their sale.order for the "
                "manufacturing / delivery audit trail."
            )
        prior = self.sale_order_id
        self.write({"sale_order_id": False, "state": "configured"})
        if prior:
            self.message_post(body=(
                "Quote link reset by %s. Previously linked to: %s. "
                "Design is now free to be re-quoted."
            ) % (self.env.user.name, prior.name))
        return {"type": "ir.actions.client", "tag": "reload"}

    # ── Send linked quotation by email (UX shortcut) ───────────────────────────
    def action_send_quote_email(self):
        """Open Odoo's standard email composer prepopulated with the
        linked sale.order quotation.

        2026-07-02 UX shortcut — replaces a 3-click nav (design →
        quote → Send by Email) with a single button on the design.
        Requires sale_order_id to be set. Delegates to the standard
        sale.order.action_quotation_send which knows the correct
        email template + mail composer args.
        """
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(
                "This design has no linked quotation yet. Click 'Create "
                "Quotation' or 'Create & Confirm →' first, then use this "
                "button to send it by email."
            )
        return self.sale_order_id.action_quotation_send()

    # ── Computed ────────────────────────────────────────────────────────────────
    # M1 fix (2026-07-06) — root cause: this compute only ever looked at
    # cabinet_line_ids (the design's own drag-editor layout). When cabinets
    # are added directly onto the linked sale.order's lines (bypassing
    # "Generate Layout"), cabinet_line_ids stays empty and the summary card
    # showed "0 cabinets / $0.00" even though the order itself has real
    # cabinet lines. Fix: depend on the order's cabinet-product line fields
    # too, and when the design has no configurator-authored lines of its
    # own, fall back to summing the linked sale.order's cabinet product
    # lines (southbrook_is_cabinet=True products only) so the summary is
    # never simply wrong. Does NOT touch cabinet_line_ids, Generate Layout,
    # or the drag-editor — this only affects the read-only summary fields.
    @api.depends(
        "cabinet_line_ids.quantity",
        "cabinet_line_ids.price_unit",
        "cabinet_line_ids.cabinet_type",
        "cabinet_line_ids.is_unresolved",
        "room_width_in",
        "sale_order_id",
        "sale_order_id.order_line.product_uom_qty",
        "sale_order_id.order_line.price_subtotal",
        "sale_order_id.order_line.product_id",
    )
    def _compute_totals(self):
        for design in self:
            lines = design.cabinet_line_ids
            if lines:
                base_lines      = lines.filtered(lambda l: l.cabinet_type == "base")
                wall_lines      = lines.filtered(lambda l: l.cabinet_type == "wall")
                tall_lines      = lines.filtered(lambda l: l.cabinet_type == "tall")
                corner_lines    = lines.filtered(lambda l: l.cabinet_type == "corner")
                panel_lines     = lines.filtered(lambda l: l.cabinet_type == "panel")
                appliance_lines = lines.filtered(lambda l: l.cabinet_type == "appliance")
                cab_lines = lines.filtered(
                    lambda l: l.cabinet_type != "filler")
                design.base_count      = sum(base_lines.mapped("quantity"))
                design.wall_count      = sum(wall_lines.mapped("quantity"))
                design.tall_count      = sum(tall_lines.mapped("quantity"))
                design.corner_count    = sum(corner_lines.mapped("quantity"))
                design.panel_count     = sum(panel_lines.mapped("quantity"))
                design.appliance_count = sum(appliance_lines.mapped("quantity"))
                design.total_cabinets  = sum(cab_lines.mapped("quantity"))
                design.filler_count    = sum(
                    (lines - cab_lines).mapped("quantity"))
                design.unplaced_count  = sum(
                    lines.filtered("is_unresolved").mapped("quantity"))
                design.summary_total   = (
                    design.base_count + design.wall_count
                    + design.tall_count + design.corner_count
                    + design.filler_count + design.panel_count
                    + design.appliance_count)
                design.estimated_price = sum(
                    l.quantity * l.price_unit for l in lines
                )
            else:
                # No configurator-origin cabinet lines yet — fall back to
                # the linked order's own cabinet product lines so the
                # summary card reflects reality instead of showing zero.
                order_lines = design.sale_order_id.order_line.filtered(
                    lambda l: l.product_id.product_tmpl_id.southbrook_is_cabinet
                )
                base_ol = order_lines.filtered(
                    lambda l: l.product_id.product_tmpl_id.southbrook_cabinet_type == "base"
                )
                wall_ol = order_lines.filtered(
                    lambda l: l.product_id.product_tmpl_id.southbrook_cabinet_type == "wall"
                )
                tall_ol = order_lines.filtered(
                    lambda l: l.product_id.product_tmpl_id.southbrook_cabinet_type == "tall"
                )
                corner_ol = order_lines.filtered(
                    lambda l: l.product_id.product_tmpl_id.southbrook_cabinet_type == "corner"
                )
                panel_ol = order_lines.filtered(
                    lambda l: l.product_id.product_tmpl_id.southbrook_cabinet_type == "panel"
                )
                appliance_ol = order_lines.filtered(
                    lambda l: l.product_id.product_tmpl_id.southbrook_cabinet_type == "appliance"
                )
                cab_ol = order_lines.filtered(
                    lambda l: l.product_id.product_tmpl_id
                    .southbrook_cabinet_type != "filler"
                )
                design.base_count      = sum(base_ol.mapped("product_uom_qty"))
                design.wall_count      = sum(wall_ol.mapped("product_uom_qty"))
                design.tall_count      = sum(tall_ol.mapped("product_uom_qty"))
                design.corner_count    = sum(corner_ol.mapped("product_uom_qty"))
                design.panel_count     = sum(panel_ol.mapped("product_uom_qty"))
                design.appliance_count = sum(appliance_ol.mapped("product_uom_qty"))
                design.total_cabinets  = sum(cab_ol.mapped("product_uom_qty"))
                design.filler_count    = sum(
                    (order_lines - cab_ol).mapped("product_uom_qty"))
                # Order lines carry no is_unresolved concept — this
                # fallback path only ever runs before the design has any
                # configurator-origin lines of its own.
                design.unplaced_count  = 0
                design.summary_total   = (
                    design.base_count + design.wall_count
                    + design.tall_count + design.corner_count
                    + design.filler_count + design.panel_count
                    + design.appliance_count)
                design.estimated_price = sum(order_lines.mapped("price_subtotal"))
            # Remainder: width minus base cabinet modules
            module_w = 24.0
            bp = design._find_cabinet_product("base", raise_if_missing=False)
            if bp:
                # Task 11 (SBK-024) — the variant's own Width, not the
                # template's shared nominal default.
                module_w = placed_width_in(bp) or 24.0
            n = max(0, int(math.floor(design.room_width_in / module_w)))
            design.remainder_in = max(0.0, design.room_width_in - n * module_w)

    # ── Task 7 (SBK-033) — the ONE summary object every surface renders ──
    def _get_counter_summary(self):
        """The single server-computed cabinet-count summary. The 3D
        header strip, the 3D sidebar metrics, and controllers/main.py's
        save/load/rearrange responses all source their numbers from
        here — none of them may independently re-derive a partition of
        cabinet_line_ids (that duplication is exactly what produced the
        four disagreeing totals V4-counters-templates.md claim 1
        documents for one design: header 9, sidebar 4+2, record 13,
        summary 10+2).

        Keys mirror the stored *_count fields 1:1 so the numbers shipped
        to the client can never drift from what's actually stored.
        `total` sums the seven cabinet_type buckets (base+wall+tall+
        corner+filler+panel+appliance) — every active line has exactly
        one cabinet_type, so `total` always equals
        len(active cabinet_line_ids) (or the order-line fallback's line
        count) by construction; see test_counter_summary.py's identity
        assertions. `unplaced_count` is a DIAGNOSTIC OVERLAY, not an
        eighth additive bucket — an is_unresolved GHOST line (Task 2) is
        already counted once under its own cabinet_type bucket above
        (matching how base_count/wall_count have always behaved —
        test_invariant_matrix.py's existing oracle pins that shape), so
        adding it a second time here would break the sum-of-parts
        identity. Archived debris/sentinel lines never reach this
        method at all: they're active=False, already excluded from
        cabinet_line_ids by Odoo's default active_test, same as every
        bucket above.

        `price`/`cabinet_price` mirror the historical /layout + client
        convention (non-filler line total, fillers broken out
        separately) — NOT the same figure as `estimated_price` (the
        stored field, unchanged, whole-design total INCLUDING fillers,
        still what the backend Summary group's Monetary field shows).
        """
        self.ensure_one()
        lines = self.cabinet_line_ids
        if lines:
            cabinet_price = sum(
                l.quantity * l.price_unit for l in lines
                if l.cabinet_type != "filler")
            filler_price = sum(
                l.quantity * l.price_unit for l in lines
                if l.cabinet_type == "filler")
        else:
            order_lines = self.sale_order_id.order_line.filtered(
                lambda l: l.product_id.product_tmpl_id.southbrook_is_cabinet
            )
            cabinet_price = sum(
                l.price_subtotal for l in order_lines
                if l.product_id.product_tmpl_id.southbrook_cabinet_type != "filler")
            filler_price = sum(
                l.price_subtotal for l in order_lines
                if l.product_id.product_tmpl_id.southbrook_cabinet_type == "filler")
        return {
            "base_count":      self.base_count,
            "wall_count":      self.wall_count,
            "tall_count":      self.tall_count,
            "corner_count":    self.corner_count,
            "filler_count":    self.filler_count,
            "panel_count":     self.panel_count,
            "appliance_count": self.appliance_count,
            "unplaced_count":  self.unplaced_count,
            "total":           self.summary_total,
            "price":           cabinet_price,
            "cabinet_price":   cabinet_price,
            "filler_price":    filler_price,
        }

    # ── Actions ─────────────────────────────────────────────────────────────────
    def action_generate_layout(self):
        """Regenerate the standard 24-in module layout from room dimensions."""
        for design in self:
            design._generate_standard_layout()
        return True

    # Corner-layer → catalog SKU. Base uses the generic lazy-susan-capable
    # SB-CORNER; wall uses SB-WALL-CORNER. (Style variants — blind/diagonal —
    # are a later refinement driven by run length + storage choice.)
    _CORNER_SKU = {"base": "SB-CORNER", "wall": "SB-WALL-CORNER"}

    def action_auto_arrange(self, sync=True):
        """Auto-distribute this design's cabinets across walls into an L/U
        layout, substituting a real corner cabinet at each inside corner,
        then mirror the result to the manufacturing sale.order.line.

        The SERVICE for the "Auto-arrange L/U" button. Pure orchestration —
        it owns no geometry or manufacturing math: spatial arrangement comes
        from kitchen_layout_engine, manufacturing sync from the reconcile
        model. Touches the LAYOUT domain (design lines) only; the reconcile
        mirror carries the corner PRODUCT to manufacturing so BOM/price/
        cutlist follow (strict domain separation).

        Runs atomically in a savepoint: any failure — including
        LayoutCapacityExceeded for an impossible layout — rolls the whole
        re-arrange back, so the design is never left half-modified. Callable
        from HTTP, tests, cron, or shell.

        Returns {corners, removed, inserted}. Raises LayoutCapacityExceeded
        when the cabinets cannot physically fit the room.
        """
        self.ensure_one()
        design = self.sudo()
        Line = self.env["southbrook.kitchen.design.line"].sudo()
        SaleOrder = self.env["sale.order"]
        IN = 1.0 / 25.4
        MM = 25.4
        _t0 = time.time()
        _logger.info("[auto-arrange] started design=%s", design.id)

        with self.env.cr.savepoint():
            # RESET TO CANONICAL — every run is a pure transformation of the
            # customer's canonical selection, never of the previous arranged
            # state. (1) delete all ephemeral DERIVED artifacts; (2) restore
            # (reactivate) any superseded canonical cabinets. This prevents
            # lineage chains (A+B→C, then C+D→E) and guarantees idempotence:
            # running N times yields identical state.
            ctx = design.with_context(active_test=False)
            # (a) restore superseded canonical cabinets FIRST (write only —
            #     no deletions), then (b) delete derived artifacts. Order
            #     matters: never re-filter a recordset across an unlink or we
            #     touch a deleted record (MissingError). Each access is fresh.
            # Task 9b (review fix; narrowed round 2) — EXCLUDE DEBRIS-
            # SIGNATURE lines from this blanket reactivation, NOT every
            # is_unresolved line. This reset exists to revive CANONICAL
            # cabinets that a previous arrangement run temporarily
            # superseded with a derived artifact (a real corner consuming
            # two runs' worth of cabinets, say) — a save_design
            # sentinel/debris line was never a corner-consumed canonical
            # selection, so it must not be swept back in here. But
            # is_unresolved is dual-purpose: a template GHOST placeholder
            # (Task 2) is also is_unresolved=True, active=True initially,
            # and CAN legitimately be archived by corner-absorption —
            # excluding it here too would permanently strand it,
            # breaking re-arrange's idempotence contract. Key the
            # exclusion on the full debris signature (archived +
            # is_unresolved + at/beyond DEBRIS_THRESHOLD_IN on x or z —
            # see _is_debris_signature), not on is_unresolved alone.
            ctx.cabinet_line_ids.filtered(
                lambda l: not l.active and not l._is_debris_signature()
            ).write({"active": True})
            ctx.cabinet_line_ids.filtered(
                lambda l: l.layout_role == "derived").unlink()
            design.invalidate_recordset(["cabinet_line_ids"])

            # Semantic cabinet list from the CANONICAL cabinets (now all
            # active/restored; skip server-placed fillers/panels).
            cabs = []
            for dl in design.cabinet_line_ids:
                if dl.origin != "configurator":
                    continue
                if dl.layout_role == "derived":
                    continue
                if dl.cabinet_type in ("filler", "panel", "corner"):
                    continue
                is_wall = dl.cabinet_type == "wall"
                cabs.append({
                    "id": dl.id,
                    "width_mm": (dl.width_in or 0) * MM,
                    "height_mm": (dl.height_in or 0) * MM,
                    "depth_mm": (dl.depth_in or 0) * MM,
                    "family": "wall" if is_wall else "base",
                    "cabinet_type": dl.cabinet_type,
                    "zone": dl.zone or ("wall" if is_wall else "base_run"),
                    "wall": dl.wall or "back",
                    "run_seq": dl.run_seq or 0,
                })
            if not cabs:
                return {"corners": 0, "removed": 0, "inserted": 0, "empty": True}

            room = {
                "width_mm":  (design.room_width_in or 0) * MM,
                "depth_mm":  (design.room_depth_in or 0) * MM,
                "height_mm": (design.room_height_in or 0) * MM,
            }
            # If the user already placed cabinets on specific walls (the
            # interactive flow), RESPECT that arrangement and just resolve
            # corners; otherwise auto-distribute a flat list across walls.
            manual = any((c.get("wall") or "back") != "back" for c in cabs)
            # M1 (2026-07-27) — rules-as-data: feed active junction rules
            # (southbrook.placement.rule, southbrook_estimating) to the
            # pure engine. The engine picks the corner cabinet whose
            # per-leg wall consumption fits this room (asymmetric blind
            # corners included) and tags the node with the rule's SKU;
            # with no rules (or none fitting) it falls back to the legacy
            # square + _CORNER_SKU below, so behavior degrades safely.
            Rule = self.env.get("southbrook.placement.rule")
            corner_rules = (
                Rule.sudo().search([
                    ("anchor_class", "=", "junction"),
                ]).engine_dicts() if Rule is not None else None)
            r = kitchen_layout_engine.resolve_and_layout(
                cabs, room,
                auto_assign=not manual,
                corner_rules=corner_rules,
                # SBK-026 — was the bare class constant (wall always at
                # 1400mm/55.12in regardless of the Wall Cabinet Top
                # selector). A per-design COPY with only "wall"'s
                # y_floor overridden honours wall_cab_top_alignment /
                # soffit_height_in / counter_thickness_in; every other
                # zone (base_run/tall/island/accessory/other) is
                # untouched, straight from the same class constant.
                zone_layout=design._wall_cab_zone_layout(),
                worktop_cursor=SaleOrder._WORKTOP_CURSOR,
                worktop_y=SaleOrder._WORKTOP_Y_FLOOR,
            )
            for diag in r.get("corner_diagnostics") or ():
                _logger.info("[auto-arrange] design=%s corner=%s/%s: %s",
                             design.id, diag["corner"], diag["layer"],
                             diag["reason"])
            finals = {c["id"]: c for c in r["cabinets"]}
            places = {p["id"]: p for p in r["placements"]}

            # 1) SUPERSEDE (archive — never delete) the canonical cabinets the
            #    corner replaced. They stay part of the customer's canonical
            #    selection and are restored on the next run; archiving hides
            #    them from the scene + manufacturing mirror in the meantime.
            #
            #    Idempotence fix (2026-07-26) — persist each archived
            #    cabinet's post-distribution wall/run_seq (from the
            #    engine's "assigned" list) BEFORE archiving. Without
            #    this, a replaced cabinet kept its PRE-distribution wall
            #    (usually "back"), so the next run's reset restored it
            #    onto the wrong wall and reconstructed a different —
            #    possibly overfull — run than the one just resolved
            #    (measured: run 2 of an idempotence cycle overflowing
            #    the back wall by one cabinet width and raising
            #    LayoutCapacityExceeded).
            assigned_by_id = {c["id"]: c for c in r.get("assigned", [])}
            if r["removed_ids"]:
                for dl in design.cabinet_line_ids.filtered(
                        lambda l: l.id in set(r["removed_ids"])):
                    a = assigned_by_id.get(dl.id)
                    vals = {"active": False}
                    if a is not None:
                        vals["wall"] = a.get("wall") or "back"
                        vals["run_seq"] = a.get("run_seq", 0)
                    dl.write(vals)
            # refresh the O2M so the survivor loop never sees an archived record
            design.invalidate_recordset(["cabinet_line_ids"])

            # 2) reposition the survivors
            for dl in design.cabinet_line_ids:
                cab = finals.get(dl.id)
                place = places.get(dl.id)
                if not cab or not place:
                    continue
                # C4 fix — the engine emits along-axis-CENTRED poses;
                # the persisted/rendered convention is a back-left-
                # bottom-corner ANCHOR (COORDINATE_CONTRACT.md). Convert
                # in the mm domain, BEFORE the *IN conversion below.
                anchor = kitchen_layout_engine.anchor_pose_mm(cab, place)
                dl.write({
                    "wall":          cab.get("wall", "back"),
                    "run_seq":       cab.get("run_seq", 0),
                    "x_position_in": anchor["x"] * IN,
                    "y_position_in": anchor["y"] * IN,
                    "z_position_in": anchor["z"] * IN,
                    "rotation_deg":  anchor["rotation_deg"],
                })

            # 3) insert a real corner cabinet per resolved corner
            for node in r["inserted"]:
                place = places[node["id"]]
                # C4 fix — same centre->anchor conversion as step 2. The
                # corner node dict IS the "cab" here (it carries its own
                # width_mm).
                anchor = kitchen_layout_engine.anchor_pose_mm(node, place)
                # M1 — a rule-resolved node carries the winning rule's
                # SKU; _CORNER_SKU stays the legacy-fallback mapping.
                code = (node.get("sku")
                        or self._CORNER_SKU.get(node["layer"], "SB-CORNER"))
                # T5 — single shared resolver (was inline here): exact
                # default_code match first (a multi-variant corner
                # template — LH + RH after the SB-CORNER repair — has NO
                # template-level default_code, since Odoo only relates it
                # for single-variant templates, so template-only search
                # would silently stop finding the corner SKU), then
                # hand-aware Hinge Side override. Also used by
                # kitchen_template._resolve_slot's fallback rung,
                # action_swap_product, and the reconcile mirror, so every
                # corner-pricing surface resolves the SAME product.product
                # for the same nominal SKU (V3-products.md claim 3).
                #
                # Task 6 (corner-remediation 2026-07-30, V3-products.md
                # claim 2) — this call is layer-agnostic on PURPOSE: the
                # pure engine tags `node["handed"]` identically for base
                # AND wall corners (kitchen_layout_engine._CORNER_RESOLVE
                # does not branch on layer), so once a template has more
                # than one materialized Hinge Side variant, this hand-
                # aware override already applies to SB-WALL-CORNER too —
                # no per-layer special case is needed here. Before the
                # catalog task backfilled an RH SB-WALL-CORNER twin, this
                # branch was a structural no-op for wall corners
                # (`len(tmpl.product_variant_ids) > 1` never true); it now
                # picks the RH wall variant at right-facing junctions the
                # same way the base-corner path always has. The safe
                # fallback (`variant = handed[:1] or variant` inside
                # `_sb_resolve_corner_variant`) stays for any corner
                # family that never gets an RH twin (e.g. a genuinely
                # symmetric wall corner) — `_check_production_ready`'s
                # CORNER_HAND_MISMATCH check (§3c) is the backstop if a
                # wrong-hand fallback ever ships.
                variant, tmpl = Line._sb_resolve_corner_variant(
                    code, hand=node.get("handed"))
                if not variant:
                    _logger.warning(
                        "[auto-arrange] corner SKU %s missing/no-variant — "
                        "skipping corner insert (design %s)", code, design.id)
                    continue
                Line.create({
                    "design_id":     design.id,
                    "product_id":    variant.id,
                    "quantity":      1,
                    "price_unit":    Line._sb_variant_price(variant, tmpl),
                    "cabinet_type":  "corner",
                    # C8 fix — corner lines omitted `zone`, so both
                    # layers silently persisted the field's old truthy
                    # default ("base_run") even for the wall-layer
                    # corner. Tag explicitly from the resolved layer.
                    "zone":          "wall" if node["layer"] == "wall" else "base_run",
                    "width_in":      node["width_mm"] * IN,
                    "height_in":     node["height_mm"] * IN,
                    "depth_in":      node["depth_mm"] * IN,
                    "x_position_in": anchor["x"] * IN,
                    "y_position_in": anchor["y"] * IN,
                    "z_position_in": anchor["z"] * IN,
                    "rotation_deg":  anchor["rotation_deg"],
                    "wall":          node["wall"],
                    # I2: a front-right standalone corner (both-high, no
                    # host run to join) omits run_seq from the engine's
                    # inserted node entirely — .get() with a sentinel default
                    # avoids a KeyError/500 on action_auto_arrange for that
                    # corner. No behaviour change for nodes that DO set it.
                    "run_seq":       node.get("run_seq", -1),
                    "pinned":        False,
                    "layout_key":    "corner-%s-%s-%s" % (
                        design.id, node["corner"], node["layer"]),
                    "origin":        "configurator",
                    # DERIVED artifact — deleted + regenerated every run.
                    "layout_role":   "derived",
                })

            # 3b) M3 — rule-demanded corner FILLER strips become real
            #     derived design lines so they reach the manufacturing
            #     mirror/BOM/cutlist (docs 08
            #     `blind-corner-filler-strip-separate-bom-line`). The
            #     engine already reserved their space (run offsets), so
            #     a missing filler product degrades to a geometric gap
            #     the installer scribes — never a collision.
            # NOTE: filler templates in the live catalog (e.g. FP3,
            # "Filler Panel 3in") carry southbrook_cabinet_type='filler'
            # but NOT southbrook_is_cabinet — they're accessories, not
            # droppable cabinets. Order by is_cabinet DESC so a properly
            # flagged filler wins if one ever exists, else FP3-class.
            filler_tmpl = self.env["product.template"].sudo().search(
                [("southbrook_cabinet_type", "=", "filler")],
                order="southbrook_is_cabinet desc, id", limit=1)
            filler_variant = False
            if filler_tmpl:
                filler_variant = (filler_tmpl.product_variant_id
                                  or filler_tmpl.product_variant_ids[:1])
            for node in r.get("fillers") or ():
                if not filler_variant:
                    _logger.warning(
                        "[auto-arrange] no filler product template — "
                        "skipping corner filler line %s (design %s); the "
                        "engine still reserved its space",
                        node["id"], design.id)
                    break
                place = places.get(node["id"])
                if place is None:
                    continue
                anchor = kitchen_layout_engine.anchor_pose_mm(node, place)
                Line.create({
                    "design_id":     design.id,
                    "product_id":    filler_variant.id,
                    "quantity":      1,
                    "price_unit":    (filler_variant.list_price
                                      or filler_tmpl.list_price),
                    "cabinet_type":  "filler",
                    "zone":          node.get("zone") or "accessory",
                    "width_in":      node["width_mm"] * IN,
                    "height_in":     node["height_mm"] * IN,
                    "depth_in":      node["depth_mm"] * IN,
                    "x_position_in": anchor["x"] * IN,
                    "y_position_in": anchor["y"] * IN,
                    "z_position_in": anchor["z"] * IN,
                    "rotation_deg":  anchor["rotation_deg"],
                    "wall":          node["wall"],
                    "run_seq":       -1,
                    "pinned":        False,
                    "layout_key":    "cornerfill-%s-%s-%s-%s" % (
                        design.id, node["corner"], node["layer"],
                        node["wall"]),
                    "origin":        "configurator",
                    "layout_role":   "derived",
                })

            # 4) mirror to the manufacturing model NOW (don't wait for the
            #    5-min reconcile cron) so BOM/price/cutlist reflect the corner.
            #    sync=False skips this — used by the CONTINUOUS interactive
            #    resolve (light, design-layer only) where the 5-min cron (or an
            #    explicit sync=True pass) catches the manufacturing side up.
            if sync:
                try:
                    self.env["southbrook.design.reconcile"].sudo()._reconcile_one(
                        design, {"created_orders": 0, "created_rooms": 0,
                                 "created_lines": 0, "mirrored": 0,
                                 "divergence": 0})
                except Exception:
                    # Do NOT swallow — re-raise so the savepoint rolls the
                    # whole re-arrange back. Never leave the design mutated but
                    # the manufacturing mirror stale.
                    _logger.exception("[auto-arrange] reconcile failed for "
                                      "design %s — rolling back", design.id)
                    raise

        _logger.info("[auto-arrange] finished design=%s corners=%d removed=%d "
                     "inserted=%d elapsed=%dms", design.id, len(r["corners"]),
                     len(r["removed_ids"]), len(r["inserted"]),
                     int((time.time() - _t0) * 1000))
        return {"corners": len(r["corners"]), "removed": len(r["removed_ids"]),
                "inserted": len(r["inserted"])}

    # ── PR4 (2026-07-12) — shared add-a-cabinet engine-placement helper ────
    # Extracted from the website's `_sb_place_line_on_wall`
    # (southbrook_estimating_website/controllers/main.py) so the backend
    # 3D configurator's `save_design` (southbrook_kitchen_3d_configurator/
    # controllers/main.py) can reach the SAME engine-delegation path
    # instead of persisting client-computed (back-wall-only) geometry for
    # cabinets on other walls. See docs/2026-07-12-renderer-contract.md
    # and docs/2026-07-12-add-cabinet-sequence.md: the engine, not the
    # client or a controller, decides a wall cabinet's pose.
    def _place_lines_on_wall(self, lines):
        """Place `lines` (a subset of self.cabinet_line_ids) onto their
        assigned walls via the pure `kitchen_layout_engine`.

        The engine is run over ALL of this design's configurator
        cabinets (excluding filler/panel, mirroring
        `action_auto_arrange`'s cab-list construction above) so a wall's
        run positions correctly account for cabinets already there —
        but only the passed `lines` are WRITTEN; every other line's
        pose is left untouched. Callers decide which lines to pass
        (e.g. only non-back-wall lines — back-wall placement is a
        different, client-owned concern that predates PR4 and must not
        change).

        Returns {line_id: {x_position_in, y_position_in, z_position_in,
        rotation_deg}} for the lines that were actually placed (a line
        the engine has no cabinet for — e.g. it isn't in
        self.cabinet_line_ids — is silently skipped, same as the
        original `_sb_place_line_on_wall`).
        """
        self.ensure_one()
        design = self.sudo()
        lines = lines.sudo()
        SaleOrder = self.env["sale.order"]
        MM, IN = 25.4, 1.0 / 25.4

        # ── C3 fix — reserve existing corner cells ──────────────────────
        # Without this, a wall that already has a DERIVED corner cabinet
        # (from action_auto_arrange) re-flows its run from the room
        # origin straight INTO the reserved corner cell, because layout()
        # was called with no wall_start_offsets. Build offsets from every
        # active corner line's layout_key ("corner-<design_id>-
        # <corner_name>-<layer>"; corner_name itself contains a hyphen,
        # e.g. "back-left", so parse from the known prefix/suffix rather
        # than a naive split) and reserve BOTH walls of that corner in
        # that (wall, layer) — mirrors kitchen_layout_engine's own
        # per-(wall, layer) offset keying (I1).
        corner_walls_by_name = {
            name: walls for name, walls, _xz in kitchen_layout_engine._CORNER_SPECS
        }
        wall_start_offsets = {}
        key_prefix = "corner-%s-" % design.id
        for dl in design.cabinet_line_ids:
            if dl.cabinet_type != "corner":
                continue
            key = dl.layout_key or ""
            layer = None
            corner_name = None
            if key.startswith(key_prefix):
                if key.endswith("-base"):
                    layer = "base"
                    corner_name = key[len(key_prefix):-len("-base")]
                elif key.endswith("-wall"):
                    layer = "wall"
                    corner_name = key[len(key_prefix):-len("-wall")]
            if layer is None or corner_name is None:
                _logger.debug(
                    "[_place_lines_on_wall] design %s: corner line %s has "
                    "an unparseable layout_key %r — skipping offset "
                    "reservation for it", design.id, dl.id, key)
                continue
            walls = corner_walls_by_name.get(corner_name)
            if not walls:
                _logger.debug(
                    "[_place_lines_on_wall] design %s: corner line %s "
                    "layout_key %r names unknown corner %r — skipping",
                    design.id, dl.id, key, corner_name)
                continue
            # M2 (2026-07-27) — PER-LEG reservation. The corner's persisted
            # anchor pose means: rot 0/180 → width_in spans the X axis and
            # depth_in spans Z; rot 90/270 → width spans Z, depth spans X.
            # Each wall of the pair is offset by the corner's extent along
            # THAT wall's axis, so an asymmetric blind corner (45" along
            # its host wall, 24" of the other) reserves 45/24 — not 45/45.
            # Symmetric corners reduce to the old single-width behavior.
            width_mm = (dl.width_in or 0) * MM
            depth_mm = (dl.depth_in or 0) * MM
            rot = int(round(dl.rotation_deg or 0)) % 360
            ext_x = width_mm if rot in (0, 180) else depth_mm
            ext_z = depth_mm if rot in (0, 180) else width_mm
            for w in walls:
                leg = ext_x if w in ("back", "front") else ext_z
                wall_start_offsets[(w, layer)] = max(
                    wall_start_offsets.get((w, layer), 0), leg)

        # M3 — a corner FILLER strip (derived, layout_key
        # "cornerfill-<design>-<corner>-<layer>-<wall>") extends its
        # wall's reservation past the corner cell by its own width, so a
        # newly added cabinet lands after corner + filler, not inside the
        # strip. Added ON TOP of the corner extents above (sum, not max —
        # the strip sits between the cell edge and the run start).
        fill_prefix = "cornerfill-%s-" % design.id
        for dl in design.cabinet_line_ids:
            if dl.cabinet_type != "filler" or dl.layout_role != "derived":
                continue
            key = dl.layout_key or ""
            if not key.startswith(fill_prefix):
                continue
            # A wall-layer strip carries zone="wall" (set at create from
            # the engine node); everything else is base-layer.
            layer = "wall" if dl.zone == "wall" else "base"
            w = dl.wall or "back"
            wall_start_offsets[(w, layer)] = (
                wall_start_offsets.get((w, layer), 0)
                + (dl.width_in or 0) * MM)

        cabs = []
        for dl in design.cabinet_line_ids:
            if dl.origin != "configurator":
                continue
            if dl.cabinet_type in ("filler", "panel"):
                continue
            # C3 fix — a DERIVED corner cabinet must not be fed into
            # layout() as an ordinary run member (its cell is reserved
            # via wall_start_offsets above instead); otherwise the run
            # re-flow shoves it out of its resolved corner position.
            if dl.cabinet_type == "corner" and dl.layout_role == "derived":
                continue
            is_wall = dl.cabinet_type == "wall"
            cabs.append({
                "id": dl.id,
                "width_mm": (dl.width_in or 0) * MM,
                "height_mm": (dl.height_in or 0) * MM,
                "depth_mm": (dl.depth_in or 0) * MM,
                "family": "wall" if is_wall else "base",
                "cabinet_type": dl.cabinet_type,
                "zone": dl.zone or ("wall" if is_wall else "base_run"),
                "wall": dl.wall or "back",
                "run_seq": dl.run_seq or 0,
            })
        if not cabs:
            return {}
        room = {
            "width_mm":  (design.room_width_in or 0) * MM,
            "depth_mm":  (design.room_depth_in or 0) * MM,
            "height_mm": (design.room_height_in or 0) * MM,
        }
        places = {p["id"]: p for p in kitchen_layout_engine.layout(
            cabs, room,
            wall_start_offsets=wall_start_offsets,
            # SBK-026 — same per-design override as action_auto_arrange
            # above; see _wall_cab_zone_layout's docstring.
            zone_layout=design._wall_cab_zone_layout(),
            worktop_cursor=SaleOrder._WORKTOP_CURSOR,
            worktop_y=SaleOrder._WORKTOP_Y_FLOOR)}
        cabs_by_id = {c["id"]: c for c in cabs}
        result = {}
        for line in lines:
            p = places.get(line.id)
            cab = cabs_by_id.get(line.id)
            if not p or not cab:
                continue
            # C4 fix — centre (engine) -> anchor (persisted/rendered)
            # conversion, mm domain, BEFORE the *IN conversion below.
            anchor = kitchen_layout_engine.anchor_pose_mm(cab, p)
            pose = {
                "x_position_in": anchor["x"] * IN,
                "y_position_in": anchor["y"] * IN,
                "z_position_in": anchor["z"] * IN,
                "rotation_deg":  anchor["rotation_deg"],
            }
            line.write(pose)
            result[line.id] = pose
        return result

    @api.model
    def action_open_from_sale_order(self, order_id):
        """Rec D Sprint 2c · reverse-lookup helper.

        Called by the "Open in 3D" button on sale.order form. Finds
        the linked design (via sale_order_id) and opens its
        configurator. If no design exists yet but the SO has a
        southbrook.room, delegates to room.action_open_kitchen_3d
        so the rep can still design against the room's dimensions.
        """
        order = self.env["sale.order"].browse(int(order_id or 0))
        if not order.exists():
            return False
        design = self.search([("sale_order_id", "=", order.id)], limit=1)
        if design:
            return design.action_open_configurator()
        room = order.room_ids[:1]
        if room:
            return room.action_open_kitchen_3d()
        # Neither design nor room — spin up a bare configurator.
        return {
            "type":   "ir.actions.client",
            "tag":    "southbrook_kitchen_configurator",
            "params": {
                "partner_id": order.partner_id.id if order.partner_id else False,
            },
        }

    def action_open_configurator(self):
        """Open the 3D Configurator pre-loaded with this design's dimensions.

        D3 — also forwards partner_id so the configurator resolves the
        channel pricelist (Dealer/Tradesperson/etc.) for live pricing,
        instead of always showing retail.
        """
        return {
            "type": "ir.actions.client",
            "tag":  "southbrook_kitchen_configurator",
            "params": {
                "design_id":              self.id,
                "design_name":            self.name,
                "room_width_in":          self.room_width_in,
                "room_depth_in":          self.room_depth_in,
                "room_height_in":         self.room_height_in,
                "partner_id":             self.partner_id.id if self.partner_id else False,
                "filler_strategy":        self.filler_strategy or "split",
                # SBK-026 — 0 means "unset" (to_soffit gated); do NOT
                # substitute a truthy fallback here or the client can
                # never tell an unset soffit from a genuinely-84in one.
                "soffit_height_in":       self.soffit_height_in or 0.0,
                "wall_cab_top_alignment": self.wall_cab_top_alignment or "fixed_gap",
                "counter_thickness_in":   self.counter_thickness_in or 1.5,
            },
        }

    # ── D12 — Production-readiness validation ──────────────────────────────────
    # Pre-quote checks that catch issues which would otherwise surface
    # in MO creation / cut-spec generation / on the shop floor. Returns
    # a flat list of {code, severity, message} dicts so the UI can
    # render them in priority order.
    #
    # Severities: blocking (refuses quote), warning (yellow), info.
    _STD_WIDTHS_IN = (6, 9, 12, 15, 18, 21, 24, 27, 30, 36, 42, 48)

    def _bom_autoseed_would_resolve(self, product):
        """True if `_ensure_kitchen_bom` (the quotation flow's autoseed,
        called from `action_create_quotation` before its own MISSING_BOM
        gate) would leave `product` with a BOM satisfying the same
        existence check the validator runs.

        Review fix (Task 2, corner-remediation 2026-07-30, Finding 1) —
        this used to be a HAND-DERIVED mirror of `_ensure_kitchen_bom`'s
        fast-path logic that assumed the seeder never raises. It does:
        both real call sites (`action_create_quotation`,
        kitchen_design.py ~2221/~2305) wrap `_ensure_kitchen_bom` in
        `try/except UserError`, and the comment at kitchen_design.py
        ~2366-2369 names the D12 MISSING_BOM blocking gate as the
        SAFETY NET for exactly that failure mode. A predicate blind to
        the seeder raising would WARN on a design whose quote then ships
        with a permanently BOM-less line — silently defeating that
        safety net.

        Fixed by actually DRY-RUNNING the real `_ensure_kitchen_bom`
        inside a savepoint that is ALWAYS rolled back (success or
        failure) — Odoo's `_FlushingSavepoint.rollback()` clears the
        cursor's ORM cache on rollback (`sql_db.py`), so no stale
        in-memory state survives either. This can never drift from
        `_ensure_kitchen_bom`'s real behaviour (including future changes
        to it) and reuses its exact access level (Finding 2 — no
        hand-rolled `sudo()` search standing in for the real fast path's
        `product_tmpl.bom_ids` read at the caller's access level).
        Still fully read-only from the caller's perspective: nothing
        persists past this method returning.
        """
        tmpl = product.product_tmpl_id
        if not tmpl:
            return False

        resolved = False
        try:
            with self.env.cr.savepoint():
                try:
                    self._ensure_kitchen_bom(tmpl)
                except UserError:
                    # Exactly the failure action_create_quotation's own
                    # try/except guards against — the seeder genuinely
                    # can't handle this product. Fail CLOSED: `resolved`
                    # stays False, so this bucket stays MISSING_BOM/
                    # blocking, never BOM_WILL_AUTOSEED/warning.
                    pass
                except Exception:
                    # Defence-in-depth for any non-UserError the seeder
                    # might raise (now or after a future change to it) —
                    # never fail open on an unexpected signal.
                    _logger.warning(
                        "_bom_autoseed_would_resolve: dry-run autoseed "
                        "raised an unexpected error for %s — treating "
                        "as NOT auto-seedable (fail-closed)",
                        tmpl.display_name, exc_info=True,
                    )
                else:
                    resolved = bool(self.env["mrp.bom"].sudo().search_count([
                        "|",
                        ("product_id", "=", product.id),
                        "&",
                        ("product_tmpl_id", "=", tmpl.id),
                        ("product_id", "=", False),
                    ], limit=1))
                # Unconditionally discard — this is a DRY RUN. Validating
                # must never persist a BOM (or the Manufacture-route
                # write, or anything else `_ensure_kitchen_bom` does) as
                # a side effect, regardless of which branch above ran.
                raise _BomAutoseedDryRun()
        except _BomAutoseedDryRun:
            pass
        return resolved

    def _check_production_ready(self):
        self.ensure_one()
        issues = []

        # 1) Empty design = blocking
        if not self.cabinet_line_ids:
            issues.append({
                "code":     "EMPTY_DESIGN",
                "severity": "blocking",
                "message":  "Design has no cabinets. Add at least one before quoting.",
            })
            return issues

        # 1b) Task 2 (kitchen templates) — unresolved template slots block
        # quoting: the honesty contract keeps them VISIBLE, and this check
        # keeps them un-quotable until a real product is chosen.
        for line in self.cabinet_line_ids.filtered("is_unresolved"):
            issues.append({
                "code":     "UNRESOLVED_SLOT",
                "severity": "blocking",
                "message":  ("Template slot '%s' has no matching product yet. "
                             "Swap the placeholder for a real cabinet before "
                             "quoting." % (line.template_slot_code
                                           or line.display_name))
                            + _line_ref(line),
            })

        # 2) Customer required — message polished 2026-07-01 task #48
        # from "Select a customer before quoting (needed for pricelist
        # resolution)." to a customer-safe phrasing that surfaces the
        # Walk-in escape hatch (the picker Agent C wires into the 3D
        # canvas resolves 'Walk-in' via get_or_create_walkin_partner).
        # SBK-031 (2026-07-30) — copy now names the button as it actually
        # reads on the form ("Walk-in", not a partner-picker option) and
        # doesn't hardcode a partner name, since the method may resolve
        # an existing "Walk-in Retail" OR legacy "Walk-in Customer"
        # record depending on what's already in the database.
        if not self.partner_id:
            issues.append({
                "code":     "MISSING_CUSTOMER",
                "severity": "blocking",
                "message":  "This design needs a customer before it can become a quote. "
                            "Assign a Customer at the top of the form (or click the "
                            "'Walk-in' button to one-click assign a walk-in customer).",
            })

        # 3) Collision detection — grouped by (wall, zone), comparing REAL
        #    anchor-convention footprints (kitchen_layout_engine.
        #    footprint_from_anchor_mm — the same primitive check #7 uses),
        #    with a 2mm tolerance. Skip fillers/panels — they intentionally
        #    bridge gaps / attach flush to a host's face.
        #
        #    C-fix (Task 1, corner-remediation 2026-07-30) — the prior
        #    "group by cabinet_type, sort by x_position_in, compare
        #    adjacent pairs" heuristic had no wall/zone/rotation awareness
        #    at all: a left-wall cabinet's x_position_in is CONSTANT
        #    across its whole run (cross-axis offset, not along-wall
        #    position), so any two same-type cabinets sharing a wall (or
        #    even opposite walls that happened to sort adjacently) tied
        #    on x and false-positived. Worse, a base-layer corner and its
        #    stacked wall-layer corner share the SAME x/z cell by design
        #    (different elevation only) and were guaranteed to collide —
        #    firing on every shipped L/U template with wall cabinets
        #    (V1-geometry.md / V2-validator.md Claims 1/2/6). Grouping by
        #    (wall, zone) first means a base-vs-wall-layer pair or a
        #    left-vs-right-wall pair is never even compared; the
        #    footprint math (not a coordinate heuristic) then still
        #    blocks a genuine same-wall same-zone overlap.
        # by_type still feeds checks #8/#9/#10 below (tall/wall vertical
        # collision, orphan filler, panel wall-attach) — unchanged from
        # before this rewrite, kept for those checks only; the collision
        # scan itself now runs on by_wall_zone (below).
        by_type = {}
        for line in self.cabinet_line_ids:
            by_type.setdefault(line.cabinet_type, []).append(line)

        # Shared with check #7 below — a line's vertical placement PLANE
        # (base-elevation vs wall-elevation; a corner line's plane comes
        # from its zone, C8, since the upper corner shares the base
        # corner's x/z cell at a different elevation). Code-review fix
        # (Finding 2, corner-remediation 2026-07-30): check #3's
        # (wall, zone) grouping is, by construction, always a SUBSET of
        # this layer grouping (every zone maps to exactly one layer via
        # this function) — so any pair check #3 finds overlapping is
        # ALSO a same-layer pair that check #7 independently re-detects
        # below with a richer message (exact x/z/rotation for both
        # cabinets, OBB-aware). Extracted once so both checks derive
        # "layer" identically instead of two copies silently drifting
        # apart, and so #3 can dedupe against #7 by construction rather
        # than by hoping the two stay in sync.
        def _footprint_layer(line):
            if line.cabinet_type == "wall" or (
                    line.cabinet_type == "corner" and line.zone == "wall"):
                return "wall"
            return "base"

        _CHECK3_MM = _MM_PER_IN
        by_wall_zone = {}
        for line in self.cabinet_line_ids:
            if line.cabinet_type in ("filler", "panel"):
                continue
            key = (line.wall or "back", line.zone or "base_run")
            by_wall_zone.setdefault(key, []).append(line)
        for (wall, zone), lines in by_wall_zone.items():
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    a, b = lines[i], lines[j]
                    cab_a = {"width_mm": (a.width_in or 0) * _CHECK3_MM,
                             "depth_mm": (a.depth_in or 0) * _CHECK3_MM}
                    cab_b = {"width_mm": (b.width_in or 0) * _CHECK3_MM,
                             "depth_mm": (b.depth_in or 0) * _CHECK3_MM}
                    place_a = {"x": (a.x_position_in or 0) * _CHECK3_MM,
                               "z": (a.z_position_in or 0) * _CHECK3_MM,
                               "rotation_deg": a.rotation_deg or 0}
                    place_b = {"x": (b.x_position_in or 0) * _CHECK3_MM,
                               "z": (b.z_position_in or 0) * _CHECK3_MM,
                               "rotation_deg": b.rotation_deg or 0}
                    fp_a = kitchen_layout_engine.footprint_from_anchor_mm(
                        cab_a, place_a)
                    fp_b = kitchen_layout_engine.footprint_from_anchor_mm(
                        cab_b, place_b)
                    if not kitchen_layout_engine.footprints_overlap(
                            fp_a, fp_b, eps=2.0):
                        continue
                    # Finding 2 dedupe — a same-layer overlap is, by
                    # construction (see _footprint_layer above), ALSO
                    # independently caught by check #7 below (same
                    # solid_overlap_from_anchor_mm primitive, OBB-aware,
                    # richer message: exact x/z/rotation for both
                    # cabinets). Skip here so one physical overlap never
                    # double-blocks under two codes. A same-(wall,zone)
                    # pair with DIFFERENT layers (e.g. a base line with
                    # zone manually overridden to "wall" — the field
                    # allows it) isn't covered by #7's layer grouping and
                    # still blocks here.
                    if (_footprint_layer(a) == _footprint_layer(b)
                            and kitchen_layout_engine.
                            solid_overlap_from_anchor_mm(
                                cab_a, place_a, cab_b, place_b)):
                        continue
                    issues.append({
                        "code":     "CABINET_COLLISION",
                        "severity": "blocking",
                        "message":  (
                            "Cabinets overlap on the %s wall (%s zone): "
                            "%s and %s occupy the same space"
                        ) % (wall, zone, a.product_id.display_name,
                             b.product_id.display_name) + _line_ref(a, b),
                    })

        # 3b) NEW — out-of-room bounds (V2 §4b). `obb_within_room` is the
        #     rotation-safe, anchor-convention in-room primitive — defined,
        #     unit-tested, but never wired into the validator, so a
        #     cabinet dragged outside the room in the 3D view and then
        #     pinned (taken out of auto-arrange's control) sailed through
        #     validation unchecked. Catches e.g. real prod debris parked
        #     at x=1,000,000" (design 99).
        _OOB_MM = _MM_PER_IN
        room_box = {"width_mm": (self.room_width_in or 0) * _OOB_MM,
                    "depth_mm": (self.room_depth_in or 0) * _OOB_MM}
        for line in self.cabinet_line_ids:
            if line.cabinet_type in ("filler", "panel"):
                continue
            cab = {"width_mm": (line.width_in or 0) * _OOB_MM,
                   "depth_mm": (line.depth_in or 0) * _OOB_MM}
            place = {"x": (line.x_position_in or 0) * _OOB_MM,
                     "z": (line.z_position_in or 0) * _OOB_MM,
                     "rotation_deg": line.rotation_deg or 0}
            if kitchen_layout_engine.obb_within_room(cab, place, room_box):
                continue
            issues.append({
                "code":     "OUT_OF_ROOM_BOUNDS",
                "severity": "blocking",
                "message":  (
                    "%s sits outside the room (%.0f\" x %.0f\") at "
                    "x=%.1f\", z=%.1f\""
                ) % (line.product_id.display_name, self.room_width_in or 0,
                     self.room_depth_in or 0, line.x_position_in or 0,
                     line.z_position_in or 0) + _line_ref(line),
            })

        # 3c) NEW — corner hand vs. junction mismatch (V2 §4d). The corner
        #     writer (kitchen_design.py ~line 665 region) picks a
        #     hand-matched variant when one exists, but silently falls
        #     back to whatever variant it already had
        #     (`variant = handed[:1] or variant`) when no matching-hand
        #     variant is found — no backstop existed anywhere in the
        #     validator (this is exactly the class of bug
        #     corner_sku_repair.py had to fix in prod: an LH-only
        #     template silently serving right-facing junctions). This
        #     derives the SAME expected hand the writer computes — from
        #     the corner name baked into a derived line's layout_key,
        #     via the same _CORNER_RESOLVE/_CORNER_STANDALONE tables the
        #     writer consults — and blocks if the ASSIGNED product's own
        #     Hinge Side attribute value (when it carries one at all)
        #     disagrees. Products with no Hinge Side PTAV (e.g.
        #     SB-WALL-CORNER, which has no L/R split) carry nothing to
        #     check and are silently skipped, not flagged.
        #
        #     Task 6 (corner-remediation 2026-07-30) — the ACTUAL
        #     _CORNER_RESOLVE/_CORNER_STANDALONE consultation now lives
        #     in ONE place, `_sb_corner_expected_hand()` on the LINE
        #     model, shared with `action_swap_product`'s corner-swap
        #     hand guard — this block still owns the layout_key-prefix
        #     filter + the message's `cname`, but never independently
        #     re-derives the expected hand from the tables itself.
        key_prefix_hand = "corner-%s-" % self.id
        for cl in self.cabinet_line_ids.filtered(
                lambda l: l.cabinet_type == "corner"
                and l.layout_role == "derived"
                and (l.layout_key or "").startswith(key_prefix_hand)):
            expected_hand = cl._sb_corner_expected_hand()
            if expected_hand not in ("L", "R"):
                continue
            cname = cl.layout_key[len(key_prefix_hand):].rsplit("-", 1)[0]
            prefix = "LH" if expected_hand == "L" else "RH"
            hinge_ptavs = cl.product_id.product_template_attribute_value_ids.filtered(
                lambda pt: pt.attribute_id.name == "Hinge Side")
            if not hinge_ptavs:
                continue
            if any((pt.name or "").startswith(prefix) for pt in hinge_ptavs):
                continue
            issues.append({
                "code":     "CORNER_HAND_MISMATCH",
                "severity": "blocking",
                "message":  (
                    "%s is the wrong hand for its %s junction (needs "
                    "%s) — the corner mechanism will not clear"
                ) % (cl.product_id.display_name, cname, prefix)
                            + _line_ref(cl),
            })

        # 4) BOM presence — required for MO generation. Check both
        #    template-level and variant-level BOMs.
        #
        #    Task 2 (corner-remediation 2026-07-30, V2-validator.md §5's
        #    "auto-seed nuance") — action_create_quotation ALSO runs
        #    this same existence check (D12 gate) but only AFTER first
        #    running _ensure_kitchen_bom for every template on the
        #    design (kitchen_design.py ~2216), which auto-seeds a
        #    template-level 'normal' BOM stub for free. Cold validate
        #    has no such autoseed step, so blindly BLOCKing here on a
        #    formality the quote flow heals unconditionally is
        #    validator/quote-flow incoherence — live repro: design 102's
        #    SB-WALL-CORNER. So: a missing BOM the quote flow WOULD
        #    autoseed reports as a WARNING here (never actually seeded —
        #    that would be a side effect of validating); a product
        #    autoseed genuinely can't resolve
        #    (`_bom_autoseed_would_resolve`) still BLOCKs.
        Bom = self.env["mrp.bom"]
        missing_block = {}
        missing_warn = {}
        for line in self.cabinet_line_ids:
            if line.cabinet_type in ("filler",):
                continue
            if not line.product_id:
                continue
            has_bom = Bom.sudo().search_count([
                "|",
                ("product_id", "=", line.product_id.id),
                "&",
                ("product_tmpl_id", "=", line.product_id.product_tmpl_id.id),
                ("product_id", "=", False),
            ], limit=1)
            if has_bom:
                continue
            bucket = (missing_warn
                      if self._bom_autoseed_would_resolve(line.product_id)
                      else missing_block)
            bucket.setdefault(line.product_id.display_name, []).append(line.id)
        if missing_block:
            parts = [name + _line_ref(missing_block[name])
                     for name in sorted(missing_block)]
            issues.append({
                "code":     "MISSING_BOM",
                "severity": "blocking",
                "message":  "No BOM defined: %s" % ", ".join(parts),
            })
        if missing_warn:
            parts = [name + _line_ref(missing_warn[name])
                     for name in sorted(missing_warn)]
            issues.append({
                "code":     "BOM_WILL_AUTOSEED",
                "severity": "warning",
                "message":  ("BOM will be auto-created at quotation: %s"
                             % ", ".join(parts)),
            })

        # 5) Non-standard widths — soft warning. Catches data-entry
        #    typos (37" instead of 36") that the shop floor can't
        #    nest cleanly.
        for line in self.cabinet_line_ids:
            if line.cabinet_type == "filler":
                continue
            if not line.width_in:
                continue
            wr = round(line.width_in)
            if abs(wr - line.width_in) < 0.01 and int(wr) not in self._STD_WIDTHS_IN:
                issues.append({
                    "code":     "NON_STANDARD_WIDTH",
                    "severity": "warning",
                    "message":  (
                        "%s is %d\" wide (non-standard; nesting/cutting may waste material)"
                    ) % (line.product_id.display_name, int(wr)) + _line_ref(line),
                })

        # 6) Soffit/ceiling clearance — replicates the controller check
        #    so the form-level validate button matches the configurator.
        #    SBK-026 — geometry now comes from the SAME
        #    `_wall_cab_top_geometry` helper the auto-arrange feed
        #    (`_wall_cab_zone_layout`) and the Room panel's live helper
        #    text consult, so this check can never disagree with what a
        #    regenerate actually writes. `_line_ref` still wants the
        #    SPECIFIC line responsible for the tallest base/wall cabinet
        #    (presentation only), so that lookup stays local.
        base_h_line = wall_h_line = None
        _tallest_base = _tallest_wall = 0.0
        for line in self.cabinet_line_ids:
            if line.cabinet_type == "base" and (line.height_in or 0) > _tallest_base:
                _tallest_base, base_h_line = line.height_in, line
            elif line.cabinet_type == "wall" and (line.height_in or 0) > _tallest_wall:
                _tallest_wall, wall_h_line = line.height_in, line
        geo = self._wall_cab_top_geometry()
        wz, wall_h, eff_top = geo["y_in"], geo["wall_h"], geo["eff_ceiling"]
        if wz + wall_h > eff_top + 0.01 and eff_top > 0:
            issues.append({
                "code":     "WALL_CAB_EXCEEDS_CEILING",
                "severity": "blocking",
                "message":  (
                    "Wall cabinet top reaches %.1f\" but %s sits at %.1f\""
                ) % (wz + wall_h,
                     "soffit" if geo["alignment"] == "to_soffit" else "ceiling",
                     eff_top) + _line_ref(wall_h_line, base_h_line),
            })

        # ── v19.0.4.22.0 audit P2#7 — extended production checks ──────

        # 7) Footprint collision — same-LAYER (base-elevation vs wall-
        #    elevation plane) cabinets whose PERSISTED, anchor-convention
        #    world AABBs overlap. C7 fix (v19.0.5.20.0): the prior check
        #    read y_position_in as the front-back axis — pre-PR3.0 that
        #    field WAS the depth axis, but the y/z field-semantics
        #    migration (migrations/19.0.5.18.0; docs/2026-07-12-
        #    canonical-coordinate-flow.md) repurposed y_position_in as
        #    ELEVATION, so this check went blind to every real corner
        #    clash (it compared elevations instead of depths) while
        #    still able to false-flag cabinets that merely differ in
        #    mount height. It also ignored rotation_deg entirely, so a
        #    90°-rotated left/right-wall run's true footprint was never
        #    considered. Rewritten against kitchen_layout_engine's shared
        #    footprint_from_anchor_mm/footprints_overlap — the SAME
        #    geometry math the renderer and the engine's own corner-
        #    detection use — so this validation and the actual persisted
        #    scene can never disagree about what overlaps.
        #    Code renamed Y_AXIS_COLLISION -> FOOTPRINT_COLLISION (grepped
        #    the repo first; nothing else referenced the old code).
        _CHECK7_MM = 25.4
        by_layer = {}
        for line in self.cabinet_line_ids:
            if line.cabinet_type in ("filler", "panel"):
                continue
            # A corner line's layer comes from its zone (C8 tags the
            # wall-layer corner zone='wall'): the upper corner shares
            # the base corner's x/z cell at a different ELEVATION, so
            # classifying both as "base" would false-flag the pair as
            # a blocking collision. (_footprint_layer, defined above
            # check #3, is the SAME classification — shared so #3's
            # Finding-2 dedupe can never silently drift from this.)
            layer = _footprint_layer(line)
            by_layer.setdefault(layer, []).append(line)
        for layer, lines in by_layer.items():
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    a, b = lines[i], lines[j]
                    cab_a = {"width_mm": (a.width_in or 0) * _CHECK7_MM,
                             "depth_mm": (a.depth_in or 0) * _CHECK7_MM}
                    cab_b = {"width_mm": (b.width_in or 0) * _CHECK7_MM,
                             "depth_mm": (b.depth_in or 0) * _CHECK7_MM}
                    place_a = {"x": (a.x_position_in or 0) * _CHECK7_MM,
                               "z": (a.z_position_in or 0) * _CHECK7_MM,
                               "rotation_deg": a.rotation_deg or 0}
                    place_b = {"x": (b.x_position_in or 0) * _CHECK7_MM,
                               "z": (b.z_position_in or 0) * _CHECK7_MM,
                               "rotation_deg": b.rotation_deg or 0}
                    # M6 — single dispatch entry point: exact AABB fast
                    # path for orthogonal pairs, SAT OBB narrow phase
                    # when either cabinet carries an arbitrary rotation
                    # (a manually placed unit on a non-90° wall). The
                    # old footprint_from_anchor_mm-only path silently
                    # judged a 45° cabinet by a wrong axis-aligned
                    # branch.
                    if not kitchen_layout_engine.solid_overlap_from_anchor_mm(
                            cab_a, place_a, cab_b, place_b):
                        continue
                    issues.append({
                        "code":     "FOOTPRINT_COLLISION",
                        "severity": "blocking",
                        "message":  (
                            "%s cabinets overlap: %s (wall=%s, x=%.1f\", "
                            "z=%.1f\", rot=%d°) vs %s (wall=%s, "
                            "x=%.1f\", z=%.1f\", rot=%d°)"
                        ) % (
                            layer.title(),
                            a.product_id.display_name, a.wall or "back",
                            a.x_position_in or 0, a.z_position_in or 0,
                            a.rotation_deg or 0,
                            b.product_id.display_name, b.wall or "back",
                            b.x_position_in or 0, b.z_position_in or 0,
                            b.rotation_deg or 0,
                        ) + _line_ref(a, b),
                    })

        # 8) Vertical (mount-height) collision — wall-cab vertical extent
        #    inside a tall's vertical extent. Bases don't clash with
        #    walls vertically (they live on different run rows).
        #
        #    PR3.0 (2026-07-12) — y/z field-semantics migration: mount-
        #    height/elevation now lives in y_position_in (was
        #    z_position_in pre-PR3.0). This check previously read
        #    z_position_in for exactly that reason (it predates the
        #    canonical COORDINATE_CONTRACT.md model) — an independent
        #    finding beyond the PR3 coordinate-contract matrix's
        #    renderer-only review scope. Left unfixed, migrating the
        #    persisted data without updating this reader would have
        #    collapsed both wall.z_position_in and tall.z_position_in to
        #    0 (walls no longer carry height there; talls never did —
        #    floor-standing), making every x-overlapping wall+tall pair
        #    falsely appear to collide vertically. Reading
        #    y_position_in instead restores (and keeps correct) the
        #    original vertical-extent comparison.
        for tall in by_type.get("tall", []):
            tx0 = tall.x_position_in
            tx1 = tx0 + (tall.width_in or 0)
            ty0 = tall.y_position_in or 0.0
            ty1 = ty0 + (tall.height_in or 0)
            for wall in by_type.get("wall", []):
                wx0 = wall.x_position_in
                wx1 = wx0 + (wall.width_in or 0)
                if wx1 <= tx0 + 0.01 or wx0 >= tx1 - 0.01:
                    continue
                wy0 = wall.y_position_in or 0.0
                wy1 = wy0 + (wall.height_in or 0)
                if wy1 <= ty0 + 0.01 or wy0 >= ty1 - 0.01:
                    continue
                issues.append({
                    "code":     "VERTICAL_COLLISION",
                    "severity": "blocking",
                    "message":  (
                        "Wall %s (bottom %.1f\") intrudes into tall "
                        "%s (top %.1f\") at x=%.1f\""
                    ) % (
                        wall.product_id.display_name, wy0,
                        tall.product_id.display_name, ty1,
                        tall.x_position_in,
                    ) + _line_ref(wall, tall),
                })

        # 9) Orphan filler — filler with no adjacent base/wall/tall
        #    within ±0.5" on either side. Fillers must bridge two
        #    cabinets, never sit alone.
        neighbours = [
            l for l in self.cabinet_line_ids
            if l.cabinet_type in ("base", "wall", "tall")
        ]
        for f in by_type.get("filler", []):
            fx0 = f.x_position_in or 0.0
            fx1 = fx0 + (f.width_in or 0)
            left = any(
                abs((c.x_position_in or 0) + (c.width_in or 0) - fx0) <= 0.5
                for c in neighbours
            )
            right = any(
                abs((c.x_position_in or 0) - fx1) <= 0.5 for c in neighbours
            )
            if not (left or right):
                issues.append({
                    "code":     "ORPHAN_FILLER",
                    "severity": "blocking",
                    "message":  (
                        "Filler %s at x=%.1f\" has no adjacent cabinet; "
                        "fillers must bridge two cabinets."
                    ) % (f.product_id.display_name, fx0) + _line_ref(f),
                })

        # 10) End-cap panel adjacency — twin of the controller-side
        #     _validate_end_cap_panel_placement (controllers/main.py),
        #     escalated from WARN to BLOCKING per the audit. Follow-up:
        #     extract the pure algorithm to an @api.model classmethod
        #     so client + server share one implementation.
        panels = by_type.get("panel", [])
        if panels:
            hosts = [
                l for l in self.cabinet_line_ids
                if l.cabinet_type in ("base", "wall")
            ]
            valid_x = []
            for h in hosts:
                hx = h.x_position_in or 0.0
                hw = h.width_in or 0.0
                valid_x.append(hx + hw)   # right side of host
                for p in panels:
                    valid_x.append(hx - (p.width_in or 0))
            for p in panels:
                px = p.x_position_in or 0.0
                if not any(abs(px - v) <= 0.5 for v in valid_x):
                    issues.append({
                        "code":     "PANEL_WALL_ATTACH",
                        "severity": "blocking",
                        "message":  (
                            "End-cap panel %s at x=%.1f\" isn't adjacent "
                            "to any base/wall side face; panels can't "
                            "attach to room walls."
                        ) % (p.product_id.display_name, px) + _line_ref(p),
                    })

        # 11) Unrealistic single-base width — warning (custom 60"+
        #     bases exist for eat-at-counter runs, but a 200" typo
        #     shouldn't burn the shop floor).
        for line in self.cabinet_line_ids:
            if line.cabinet_type != "base":
                continue
            if (line.width_in or 0) > 60.0:
                issues.append({
                    "code":     "UNREALISTIC_DIM",
                    "severity": "warning",
                    "message":  (
                        "%s is %.1f\" wide — over 60\" on a single base "
                        "is unusual; confirm not a typo."
                    ) % (line.product_id.display_name, line.width_in) + _line_ref(line),
                })

        # 12) M4 — corner MOTION envelope vs. solids. A corner rule may
        #     declare `clearance_front_mm`: the region its doors /
        #     mechanism (susan bifold, LeMans arm, blind pullout) sweep
        #     in front of the cell. Any SAME-layer solid inside that box
        #     blocks the mechanism (motion-vs-solid = blocking per
        #     docs/research/corner-engine/09-rule-engine-spec.md §5).
        # 13) M4 — rule-demanded corner filler strips must exist. The
        #     engine emits them as derived lines (M3); a user deleting
        #     one leaves a gap the BOM no longer covers → warning (the
        #     installer CAN scribe on site, so not blocking).
        Rule = self.env.get("southbrook.placement.rule")
        if Rule is not None:
            _MM12 = 25.4

            def _line_layer(line):
                if line.cabinet_type == "wall" or (
                        line.cabinet_type == "corner"
                        and line.zone == "wall"):
                    return "wall"
                return "base"

            def _anchor_place(line):
                return {"x": (line.x_position_in or 0) * _MM12,
                        "z": (line.z_position_in or 0) * _MM12,
                        "rotation_deg": line.rotation_deg or 0}

            def _cab(line):
                return {"width_mm": (line.width_in or 0) * _MM12,
                        "depth_mm": (line.depth_in or 0) * _MM12}

            # Task 1 pt.6 diagnosis (corner-remediation 2026-07-30) — the
            # motion envelope (below) extends a corner's OWN footprint
            # along its WALL-PRESENTATION rotation (_CORNER_RESOLVE_
            # PRESENTATION in kitchen_layout_engine.py — chosen purely so
            # the corner renders as "turning into" whichever run it was
            # tagged onto, NOT the true direction a diagonal lazy-susan/
            # pie-cut door actually swings, which bisects the junction).
            # Because a corner cell is flush against BOTH its walls at
            # once, that envelope's cross-axis span always covers the
            # corner's FULL leg extent on the presentation wall — which
            # geometrically reaches straight into whatever cabinet the
            # corner's OWN other-leg reservation legitimately placed
            # flush next to it (wall_start_offsets in
            # kitchen_layout_engine.resolve_and_layout — contiguous,
            # zero-gap, by construction; verified LIVE against the
            # U-10X8X10 template's four real corners, design ids
            # matching V1-geometry.md's design-102 shape — every hit was
            # a same-wall cabinet flush against the corner's own edge).
            # That is a validator-side modeling defect, not a real
            # clearance violation: the engine's own placement is correct
            # (zero overlaps, zero gaps, well inside the room —
            # V1-geometry.md §3).
            #
            # Code-review fix (Finding 1) — pure geometric touch/overlap
            # is NOT sufficient to identify "the run the corner made
            # room for": for rot=90, the corner's own edge (fp x1) is
            # numerically identical to the envelope's start (env x0), so
            # a genuine zero-standoff intruder sitting directly in the
            # swing path touches in EXACTLY the same pattern as the
            # legitimate neighbour — a pure-geometry exclusion would
            # silently swallow the worst-case violation (no other check
            # backstops it: flush footprints don't overlap, so check #7
            # can't catch it either). The identity that actually
            # distinguishes them is WHICH WALL the candidate belongs to.
            # `_CORNER_RESOLVE[cname]` gives the corner's two legs
            # (host_wall, other_wall); by construction one of the two
            # always equals the corner's own persisted `wall` (its
            # presentation) and the other is the wall whose run
            # continues in the envelope's facing direction (confirmed
            # live: back-left's facing neighbour is on "back" —
            # host_wall, since presentation="left"=other_wall;
            # back-right's facing neighbour is ALSO on "back" — there
            # other_wall, since presentation="right"=host_wall — i.e.
            # it's always "whichever leg isn't the presentation wall",
            # never a fixed host/other choice). A candidate qualifies
            # for exclusion ONLY when it is BOTH (a) tagged with that
            # specific facing wall AND (b) flush-touching (within 2mm)
            # the corner's own footprint with its extent overlapping on
            # the cross axis — i.e. provably the run cabinet the corner
            # reserved that wall's cursor for, not merely something
            # coincidentally positioned at the same coordinates.
            def _facing_neighbor_wall(corner_wall, cname):
                spec = kitchen_layout_engine._CORNER_RESOLVE.get(cname)
                if spec is None:
                    return None   # standalone (front-right) — no exclusion
                host_w, other_w, _end, _hand = spec
                if corner_wall == host_w:
                    return other_w
                if corner_wall == other_w:
                    return host_w
                return None

            def _is_corner_run_neighbor(fp_corner, fp_other, other_wall,
                                         facing_wall, tol_mm=2.0):
                if facing_wall is None or other_wall != facing_wall:
                    return False
                ax0, ax1, az0, az1 = fp_corner
                bx0, bx1, bz0, bz1 = fp_other
                x_touch = (abs(ax1 - bx0) <= tol_mm
                           or abs(bx1 - ax0) <= tol_mm)
                z_touch = (abs(az1 - bz0) <= tol_mm
                           or abs(bz1 - az0) <= tol_mm)
                x_span_overlaps = ax0 < bx1 - tol_mm and bx0 < ax1 - tol_mm
                z_span_overlaps = az0 < bz1 - tol_mm and bz0 < az1 - tol_mm
                return ((x_touch and z_span_overlaps)
                        or (z_touch and x_span_overlaps))

            rules_by_tmpl = {}
            for rr in Rule.sudo().search(
                    [("anchor_class", "=", "junction")]):
                rules_by_tmpl.setdefault(rr.product_tmpl_id.id, rr)
            corner_lines = self.cabinet_line_ids.filtered(
                lambda l: l.cabinet_type == "corner")
            key_prefix12 = "corner-%s-" % self.id
            for cl in corner_lines:
                rr = rules_by_tmpl.get(cl.product_id.product_tmpl_id.id)
                if rr is None:
                    continue
                payload = rr.payload or {}
                layer = _line_layer(cl)
                # cname (the corner's name, e.g. "back-left") is shared by
                # checks #12 and #13 below — only derivable for a DERIVED
                # corner line whose layout_key carries it.
                cname = None
                if (cl.layout_key or "").startswith(key_prefix12):
                    cname = (cl.layout_key[len(key_prefix12):]
                             .rsplit("-", 1)[0])
                # -- 12: motion envelope --
                # M6 — the envelope box math is orthogonal-only; a
                # manually rotated corner line (non-90° wall) skips THIS
                # check rather than being judged by a wrong branch (its
                # solid collisions stay fully covered by check #7's OBB
                # dispatch above). Check #13 below still applies.
                rot12 = (cl.rotation_deg or 0) % 360
                ortho12 = min(abs(rot12 - a)
                              for a in (0, 90, 180, 270, 360)) <= 1.0
                clearance = payload.get("clearance_front_mm") or 0
                if clearance > 0 and ortho12:
                    facing_wall = (_facing_neighbor_wall(cl.wall, cname)
                                   if cname else None)
                    fp_corner = kitchen_layout_engine.footprint_from_anchor_mm(
                        _cab(cl), _anchor_place(cl))
                    env_box = kitchen_layout_engine.\
                        motion_envelope_from_anchor_mm(
                            _cab(cl), _anchor_place(cl), clearance)
                    for other in self.cabinet_line_ids:
                        if other.id == cl.id:
                            continue
                        if other.cabinet_type in ("filler", "panel"):
                            continue
                        if _line_layer(other) != layer:
                            continue
                        fp = kitchen_layout_engine.footprint_from_anchor_mm(
                            _cab(other), _anchor_place(other))
                        if _is_corner_run_neighbor(
                                fp_corner, fp, other.wall, facing_wall):
                            continue
                        if kitchen_layout_engine.footprints_overlap(
                                env_box, fp):
                            issues.append({
                                "code":     "MOTION_ENVELOPE_COLLISION",
                                "severity": "blocking",
                                # V2-validator.md Claim 3 — the corner is
                                # the grammatical subject, the intruding
                                # neighbour named second (was: neighbour
                                # first, biasing any downstream consumer
                                # toward "blame the neighbour").
                                "message":  (
                                    "Corner cabinet %s (%s) cannot open: "
                                    "%s sits inside its %.1f\" door/"
                                    "mechanism clearance."
                                ) % (cl.product_id.display_name,
                                     rr.corner_type_id or rr.name,
                                     other.product_id.display_name,
                                     clearance / _MM12)
                                            + _line_ref(cl, other),
                            })
                # -- 13: demanded fillers present --
                fx = payload.get("filler_x_mm") or 0
                fz = payload.get("filler_z_mm") or 0
                if (fx <= 0 and fz <= 0) or cname is None:
                    continue
                spec12 = kitchen_layout_engine._CORNER_RESOLVE.get(cname)
                if spec12 is None:
                    continue   # standalone corner — engine emits none
                host_w, other_w, other_end12, _h = spec12
                expected_walls = [host_w] + (
                    [other_w] if other_end12 == "first" else [])
                for w in expected_walls:
                    fw = fx if w in ("back", "front") else fz
                    if fw <= 0:
                        continue
                    want_key = "cornerfill-%s-%s-%s-%s" % (
                        self.id, cname, layer, w)
                    if not self.cabinet_line_ids.filtered(
                            lambda l: l.layout_key == want_key):
                        issues.append({
                            "code":     "CORNER_FILLER_MISSING",
                            "severity": "warning",
                            "message":  (
                                "The %s corner (%s) requires a %.1f\" "
                                "filler on the %s wall but the strip is "
                                "missing — re-run auto-arrange or plan "
                                "to scribe on site."
                            ) % (cname, cl.product_id.display_name,
                                 fw / _MM12, w) + _line_ref(cl),
                        })

        return issues

    def action_validate_production(self):
        """Form-button validator. Surfaces all issues in a notification
        toast — sticky if any blocking, transient otherwise."""
        self.ensure_one()
        issues = self._check_production_ready()
        if not issues:
            return {
                "type": "ir.actions.client",
                "tag":  "display_notification",
                "params": {
                    "type":    "success",
                    "title":   "Production-Ready",
                    "message": "No blocking issues. Ready to quote.",
                    "sticky":  False,
                },
            }
        blocking = [i for i in issues if i["severity"] == "blocking"]
        lines = []
        for i in issues:
            tag = "BLOCK" if i["severity"] == "blocking" else "WARN"
            lines.append("[%s] %s" % (tag, i["message"]))
        return {
            "type": "ir.actions.client",
            "tag":  "display_notification",
            "params": {
                "type":    "danger" if blocking else "warning",
                "title":   "Production check: %d issue(s)" % len(issues),
                "message": "\n".join(lines),
                "sticky":  True,
            },
        }

    # ── D11 — Enriched line name helper ────────────────────────────────────────
    # Renders a single-line spec the customer can recognise on the
    # quote: "Wall 2-Door | 24"W x 30"H x 12"D | Wall | Shaker | Maple
    # Veneer". Without this every quote line read "[BASE-24] Base
    # Cabinet" with all the config detail buried.
    def _quote_line_name(self, line):
        parts = [line.product_id.display_name]
        tmpl  = line.product_id.product_tmpl_id
        # Cabinet type label (Base / Wall / Tall / etc.)
        type_sel = dict(line._fields["cabinet_type"].selection or [])
        if line.cabinet_type:
            parts.append(type_sel.get(line.cabinet_type, line.cabinet_type).title())
        # Dimensions: drop trailing zeros from floats for readability.
        parts.append("%g\"W x %g\"H x %g\"D" % (
            line.width_in or 0, line.height_in or 0, line.depth_in or 0,
        ))
        # Door style + material from the product template.
        door_sel = dict(tmpl._fields["southbrook_door_style"].selection or [])
        mat_sel  = dict(tmpl._fields["southbrook_material"].selection or [])
        if tmpl.southbrook_door_style:
            parts.append(door_sel.get(tmpl.southbrook_door_style,
                                       tmpl.southbrook_door_style))
        if tmpl.southbrook_material:
            parts.append(mat_sel.get(tmpl.southbrook_material,
                                       tmpl.southbrook_material))
        return " | ".join(p for p in parts if p)

    # ── D11 — Duplicate-and-Open action ────────────────────────────────────────
    def action_duplicate_and_open(self):
        """Copy this design as draft and open the 3D Configurator on
        the new record. One click instead of Duplicate -> open."""
        self.ensure_one()
        copy = self.copy({"name": "%s (Copy)" % self.name, "state": "draft"})
        return copy.action_open_configurator()

    # ── v19.0.5.6.12 — Template gallery (customer-onboarding) ─────────────
    # Biggest UX win for first-time users: instead of an empty canvas the
    # rep/customer picks one of four preset layouts (Empty / L / U /
    # Galley) which pre-seeds room dimensions AND (for the shaped
    # presets) fills the primary wall run with canonical cabinets from
    # southbrook_estimating. Users iterate from a starting point, not
    # from a blank page.
    #
    # Sizes below are inches to match the model's existing inch-native
    # fields (room_width_in / room_depth_in / room_height_in). The
    # room_length_mm equivalents from the design brief (3600×3000×2400 mm
    # etc.) are converted to nearest round US kitchen dimensions
    # (12'/10'/8') because Southbrook's shop floor cuts to inches.
    # DEPRECATED (templates T3, 2026-07-27): superseded by data-driven
    # southbrook.kitchen.template + action_instantiate. Kept ONLY because
    # test_track_b_end_to_end still exercises it; the picker no longer
    # consults it. Remove once track-b re-points to templates.
    _KITCHEN_TEMPLATE_PRESETS = {
        "empty": {
            "name":           "Empty Room",
            "description":    "Blank canvas — 12' × 10' room, no cabinets. "
                              "Best when the customer wants full creative "
                              "control from the first click.",
            "room_width_in":  144.0,   # 3658 mm ≈ 3600 mm target
            "room_depth_in":  120.0,   # 3048 mm ≈ 3000 mm target
            "room_height_in": 96.0,    # 2438 mm ≈ 2400 mm target
            "generate_layout": False,
            "starter_cabinets": [],
        },
        "l_shape": {
            "name":           "L-Shaped Kitchen",
            "description":    "12' × 12' room with an L-run of cabinets: "
                              "long primary wall + short return leg. Classic "
                              "starter layout for corner kitchens.",
            "room_width_in":  144.0,
            "room_depth_in":  144.0,
            "room_height_in": 96.0,
            "generate_layout": True,
            # xmlids are seeded via southbrook_estimating; missing xmlids
            # degrade to a chatter warning (see _apply_kitchen_template).
            "starter_cabinets": [
                # Primary wall (y=0) — base row of 5 cabinets (24" ea)
                ("southbrook_estimating.sink_base",  "base", 24.0,  0.0, 0.0,  0),
                ("southbrook_estimating.base_2dr",   "base", 48.0,  0.0, 0.0, 10),
                ("southbrook_estimating.drawer_bank","base", 72.0,  0.0, 0.0, 20),
                ("southbrook_estimating.base_1dr",   "base", 96.0,  0.0, 0.0, 30),
                ("southbrook_estimating.corner",     "base", 120.0, 0.0, 0.0, 40),
                # Wall cabinets over primary wall (mount height 54" —
                # PR3.0: lives in y_position_in, not z; z stays 0/flush).
                ("southbrook_estimating.wall_2dr",   "wall", 24.0,  54.0, 0.0, 50),
                ("southbrook_estimating.wall_2dr",   "wall", 72.0,  54.0, 0.0, 60),
                ("southbrook_estimating.wall_1dr",   "wall", 96.0,  54.0, 0.0, 70),
                # Short return leg (x=0, y offset)
                ("southbrook_estimating.tall_pantry","tall", 0.0,   30.0, 0.0, 80),
            ],
        },
        "u_shape": {
            "name":           "U-Shaped Kitchen",
            "description":    "12' × 10' room with cabinets on three walls: "
                              "prep zone + return legs. Best for busy family "
                              "kitchens and dedicated cooking work-triangles.",
            "room_width_in":  144.0,
            "room_depth_in":  120.0,
            "room_height_in": 96.0,
            "generate_layout": True,
            "starter_cabinets": [
                # Back wall (y=0) — 6 base cabinets, 24" each = 144"
                ("southbrook_estimating.base_1dr",   "base", 0.0,   0.0, 0.0,  0),
                ("southbrook_estimating.base_2dr",   "base", 24.0,  0.0, 0.0, 10),
                ("southbrook_estimating.sink_base",  "base", 48.0,  0.0, 0.0, 20),
                ("southbrook_estimating.drawer_bank","base", 72.0,  0.0, 0.0, 30),
                ("southbrook_estimating.base_2dr",   "base", 96.0,  0.0, 0.0, 40),
                ("southbrook_estimating.base_1dr",   "base", 120.0, 0.0, 0.0, 50),
                # Wall cabinets over back wall (mount height 54" — PR3.0:
                # lives in y_position_in, not z; z stays 0/flush).
                ("southbrook_estimating.wall_2dr",   "wall", 0.0,   54.0, 0.0, 60),
                ("southbrook_estimating.wall_2dr",   "wall", 48.0,  54.0, 0.0, 70),
                ("southbrook_estimating.wall_2dr",   "wall", 96.0,  54.0, 0.0, 80),
                # Left return leg
                ("southbrook_estimating.tall_pantry","tall", 0.0,   30.0, 0.0, 90),
                # Right return leg
                ("southbrook_estimating.tall_oven",  "tall", 120.0, 30.0, 0.0, 100),
            ],
        },
        "galley": {
            "name":           "Galley Kitchen",
            "description":    "12' × 8' narrow room with two parallel cabinet "
                              "runs. Efficient for apartments, secondary "
                              "kitchens, and butlers' pantries.",
            "room_width_in":  144.0,
            "room_depth_in":  96.0,
            "room_height_in": 96.0,
            "generate_layout": True,
            "starter_cabinets": [
                # Back run (y=0) — 6 base + 3 wall
                ("southbrook_estimating.sink_base",  "base", 0.0,   0.0, 0.0,  0),
                ("southbrook_estimating.base_2dr",   "base", 24.0,  0.0, 0.0, 10),
                ("southbrook_estimating.drawer_bank","base", 48.0,  0.0, 0.0, 20),
                ("southbrook_estimating.base_2dr",   "base", 72.0,  0.0, 0.0, 30),
                ("southbrook_estimating.base_1dr",   "base", 96.0,  0.0, 0.0, 40),
                ("southbrook_estimating.base_1dr",   "base", 120.0, 0.0, 0.0, 50),
                # Wall cabinets (mount height 54" — PR3.0: lives in
                # y_position_in, not z; z stays 0/flush).
                ("southbrook_estimating.wall_2dr",   "wall", 0.0,   54.0, 0.0, 60),
                ("southbrook_estimating.wall_2dr",   "wall", 48.0,  54.0, 0.0, 70),
                ("southbrook_estimating.wall_2dr",   "wall", 96.0,  54.0, 0.0, 80),
                # Front run (parallel, y offset — 12" gap for the aisle)
                ("southbrook_estimating.base_2dr",   "base", 24.0,  72.0, 0.0, 90),
                ("southbrook_estimating.base_2dr",   "base", 72.0,  72.0, 0.0, 100),
            ],
        },
    }

    @api.model
    def _get_preset_layout(self, preset):
        """Return the preset dict for `preset` or None if the key is
        unknown. Public helper so tests + the wizard can introspect the
        catalog without duplicating the source of truth.

        Preset keys: 'empty', 'l_shape', 'u_shape', 'galley'.
        """
        return self._KITCHEN_TEMPLATE_PRESETS.get(preset)

    def _apply_kitchen_template(self, preset):
        """Seed room dimensions + starter cabinets from a preset spec.

        Called after `create()` by the wizard. Missing cabinet xmlids
        (e.g. installs without southbrook_estimating catalog seeds) are
        skipped and reported in a single chatter note so the rep sees
        exactly which starters didn't land — the design still opens,
        just with fewer cabinets.
        """
        self.ensure_one()
        spec = self._get_preset_layout(preset)
        if not spec:
            raise UserError("Unknown kitchen template preset: %r" % preset)

        self.write({
            "room_width_in":  spec["room_width_in"],
            "room_depth_in":  spec["room_depth_in"],
            "room_height_in": spec["room_height_in"],
        })

        seeded, skipped = [], []
        Line = self.env["southbrook.kitchen.design.line"]
        for xmlid, cabinet_type, x, y, z, sequence in spec["starter_cabinets"]:
            tmpl = self.env.ref(xmlid, raise_if_not_found=False)
            if not tmpl:
                skipped.append(xmlid)
                continue
            variant = tmpl.product_variant_id
            if not variant:
                skipped.append(xmlid)
                continue
            Line.create({
                "design_id":     self.id,
                "sequence":      sequence,
                "product_id":    variant.id,
                "quantity":      1,
                "price_unit":    variant.lst_price or tmpl.list_price,
                "cabinet_type":  cabinet_type,
                # Task 11 (SBK-024) — the materialized variant's own
                # Width, not the shared template default.
                "width_in":      placed_width_in(variant) or 24.0,
                "height_in":     tmpl.southbrook_height_in or 34.5,
                "depth_in":      tmpl.southbrook_depth_in or 24.0,
                "x_position_in": x,
                "y_position_in": y,
                "z_position_in": z,
                "origin":        "configurator",
            })
            seeded.append(xmlid)

        # Chatter audit trail — critical for the "why did my L-shape
        # only get 3 cabinets" support ticket case.
        summary = "Applied template preset: %s" % spec["name"]
        details = []
        if seeded:
            details.append("Seeded %d starter cabinet(s)." % len(seeded))
        if skipped:
            details.append(
                "Skipped %d cabinet(s) with missing xmlids: %s"
                % (len(skipped), ", ".join(sorted(set(skipped))))
            )
        if not spec["starter_cabinets"]:
            details.append("No starter cabinets in this preset (blank canvas).")
        self.message_post(body="%s<br/>%s" % (summary, "<br/>".join(details)))

        # Move designs with starters straight to 'configured'; blank
        # canvas stays at 'draft'.
        if seeded:
            self.state = "configured"
        return {"seeded": seeded, "skipped": skipped}

    @api.model
    def action_open_template_gallery(self):
        """Open the "Start from template" wizard.

        Called from the Kitchen Designs list header button. Returns an
        ir.actions.act_window for the transient
        kitchen.design.template.picker so the rep sees the 4-preset
        radio card set instead of a blank Create form.
        """
        return {
            "type":      "ir.actions.act_window",
            "name":      "Start from Template",
            "res_model": "kitchen.design.template.picker",
            "view_mode": "form",
            "target":    "new",
            "context":   self.env.context,
        }

    # ── BOM autoseed (v19.0.5.6.0) ─────────────────────────────────────────────
    # Sprint 2d workstream that dodges the JS/canvas failure surface. The
    # goal is narrow: at quote-time, for every unique product.template on
    # the fresh SO, ensure a template-level mrp.bom stub exists. If one
    # already does, no-op — this method must be safely re-callable across
    # multiple designs that share templates.
    #
    # Panel geometry is computed via southbrook_estimating.mrp_bom.
    # _compute_panel_dimensions (the single canonical Custom Routine #1)
    # and persisted as a JSON blob on the note field until raw-material
    # product.product records exist. bom_line_ids stays empty; the shop
    # team fills it in manually or a follow-up commit wires it up.
    def _ensure_kitchen_bom(self, product_tmpl):
        """Idempotently seed a template-level mrp.bom stub.

        Returns the existing (or newly-created) mrp.bom record. Safe to
        call from a quote-time loop — repeated calls on the same
        template are no-ops.
        """
        self.ensure_one()
        if not product_tmpl:
            return self.env["mrp.bom"]

        Bom = self.env["mrp.bom"].sudo()

        # v19.0.5.6.5 (Track B P0-B, defense-in-depth) — attach the
        # Manufacture route on EVERY call, not just when we're about
        # to autoseed a new BOM. Runtime-created templates (e.g. from
        # the 3D configurator's fast-path) miss the noupdate="1"
        # canonical_catalog_routes.xml seed, so without this check
        # procurement.group would only spawn Delivery after SO
        # confirm — mrp.production would never be created. The guards
        # (raise_if_not_found=False + `if mfg_route`) let this degrade
        # silently on installs where mrp isn't loaded yet.
        mfg_route = self.env.ref(
            "mrp.route_warehouse0_manufacture", raise_if_not_found=False,
        )
        # Defense-in-depth — writes route on product.product variants (the
        # stock module defines route_ids on the variant, not the template;
        # template-level writes silently no-op in v19).
        if mfg_route:
            for variant in product_tmpl.product_variant_ids:
                if mfg_route.id not in variant.route_ids.ids:
                    variant.sudo().write({"route_ids": [(4, mfg_route.id)]})

        # Fast path — the template already carries a normal-type BOM.
        # active_test=False so we don't autoseed a duplicate on top of an
        # archived normal BOM (v19 default One2many context filters archived).
        existing = product_tmpl.with_context(active_test=False).bom_ids.filtered(
            lambda b: b.type == "normal"
        )
        if existing:
            return existing[:1]

        # Inches → mm (25.4). Templates carry the inch-native fields
        # exposed by southbrook_kitchen_3d_configurator.product_template;
        # _compute_panel_dimensions is metric per NF14.
        #
        # Task 11 (SBK-024) note — deliberately NOT switched to
        # `placed_width_in()`: this method is keyed on `product_tmpl`
        # (a `product.template`), and `mrp.bom` in this codebase is
        # seeded ONE PER TEMPLATE (`bom_ids` lives on product.template,
        # not product.product — see the `existing` lookup just above),
        # not per variant. There is no per-variant BOM to attach a
        # variant-resolved width to; making the cutlist variant-aware
        # would mean restructuring BOM generation itself, a separate,
        # larger change than placement geometry. Out of scope here.
        w_in = product_tmpl.southbrook_width_in or 24.0
        h_in = product_tmpl.southbrook_height_in or 34.5
        d_in = product_tmpl.southbrook_depth_in or 24.0
        width_mm = w_in * 25.4
        height_mm = h_in * 25.4
        depth_mm = d_in * 25.4

        ct = product_tmpl.southbrook_cabinet_type or "base"
        family_map = {
            "base":   "base",
            "wall":   "wall",
            "tall":   "tall",
            "corner": "corner",
            "filler": "accessory",
            "panel":  "accessory",
        }
        family = family_map.get(ct, "base")

        # Rule 3 (Southbrook_Excel_to_Odoo_Mapping.md §3.4) — width →
        # door count for base/wall/tall. Accessories emit no door.
        if family == "accessory":
            door_count = 0
        elif w_in >= 24.0:
            door_count = 2
        else:
            door_count = 1

        panel = Bom._compute_panel_dimensions(
            width_mm=width_mm,
            height_mm=height_mm,
            depth_mm=depth_mm,
            family=family,
            door_count=door_count,
            drawer_count=0,
            finished_sides="none",
        )

        default_code = product_tmpl.default_code or ("TMPL-%d" % product_tmpl.id)
        # v19.0.5.6.3 fix: mrp.bom has NO `note` field in Odoo v19 CE
        # (verified against official v19 mrp/models/mrp_bom.py). The panel
        # JSON was originally stashed there as a placeholder — dropped now
        # so Bom.create() no longer raises ValueError on the first-ever
        # autoseed. Panel geometry is deterministic from template dims +
        # family + door_count; the follow-up commit that materialises
        # bom_line_ids will recompute rather than re-read.
        bom = Bom.create({
            "product_tmpl_id": product_tmpl.id,
            "type":            "normal",
            "product_qty":     1.0,
            "code":            "KitchenAutoSeed-%s" % default_code,
            "bom_line_ids":    [],
        })
        _logger.info(
            "auto-seeded BOM for %s | panel=%s",
            product_tmpl.display_name,
            json.dumps(panel, default=str, sort_keys=True),
        )
        return bom

    def action_create_quotation(self):
        SaleOrder = self.env["sale.order"]
        for design in self:
            if not design.partner_id:
                raise UserError("Select a customer before creating a quotation.")
            # v19.0.5.6.4 fix — autoseed BEFORE the production-ready
            # gate. Previously (5.6.0-5.6.3) autoseed ran after SO
            # create, so the D12 MISSING_BOM gate rejected every design
            # whose cabinet template had no BOM — defeating the whole
            # point of Track B autoseed (users shouldn't have to hand-
            # create BOMs to get a quote). Now: seed missing template-
            # level BOM stubs first, then run the readiness check.
            design_templates = design.cabinet_line_ids.mapped(
                "product_id.product_tmpl_id"
            )
            for tmpl in design_templates:
                try:
                    design._ensure_kitchen_bom(tmpl)
                except UserError as e:
                    _logger.warning(
                        "BOM autoseed (pre-quote) skipped for %s on design %s: %s",
                        tmpl.display_name, design.name, e,
                    )
            # D12 — Gate on production-readiness. Refuses to spawn a
            # quote when any blocking issue (collision, missing BOM,
            # wall-cab over ceiling) would burn the customer or the
            # shop floor later. Runs AFTER autoseed so MISSING_BOM only
            # fires on templates the autoseed genuinely couldn't fix.
            issues = design._check_production_ready()
            blocking = [i for i in issues if i["severity"] == "blocking"]
            if blocking:
                msg = "\n".join("- %s" % i["message"] for i in blocking)
                raise UserError(
                    "Design isn't production-ready. Resolve these blocking "
                    "issues first:\n\n%s" % msg
                )
            # v19.0.4.22.0 audit P1#4 — Both `price_unit` AND
            # `pricelist_id` are set intentionally. The audit initially
            # flagged this as a double-discount landmine; a v19-source
            # investigation (see docs/southbrook_kitchen_audit_2026-07-01
            # .md §P1#4) proved Odoo's `_compute_price_unit` REPLACES
            # rather than stacks — an explicit `price_unit` in create()
            # is honoured and downstream recompute (from qty/partner/
            # product change) re-derives from `pricelist_id._get_
            # product_price(product, qty)` NOT from the current
            # `price_unit`. Zero arithmetic double-discount risk.
            #
            # Why keep BOTH:
            #   * `price_unit` — locks the design-time snapshot for
            #     `pricelist_refacing` customers whose pricelist has
            #     no items (priced via a Python routine, not pricelist
            #     items); a bare pricelist_id would auto-compute to
            #     `product.lst_price` (retail) — an accidental margin
            #     leak.
            #   * `pricelist_id` — needed for the rep's customer-flip
            #     workflow: swapping partner on the created SO triggers
            #     `_compute_price_unit` which re-derives from the new
            #     partner's channel. Standard Odoo semantics.
            vals = {
                "partner_id": design.partner_id.id,
                "origin":     design.name,
                "note":       design.notes or "",
                "order_line": [
                    (0, 0, {
                        "product_id":      line.product_id.id,
                        "name":            self._quote_line_name(line),
                        "product_uom_qty": line.quantity,
                        "price_unit":      line.price_unit,
                        # Task B3 (Materials geometry-writeback plan,
                        # Increment B) — carry this line's real per-
                        # instance cabinet dims (drag-resize/filler
                        # overrides included) onto the SO line so the
                        # material BoM can weigh the actual cut, not
                        # just the template's nominal size. {} (no-op)
                        # when the line has no real dims — never
                        # fabricated.
                        **line._sb_dims_mm(),
                    })
                    for line in design.cabinet_line_ids
                ],
            }
            # 2026-06-28 Tier-A — resolve the channel pricelist BEFORE
            # create so the order opens on the right pricelist (Dealer
            # −50% / Tradesperson tier-3 −35% / etc.) instead of the
            # default retail. Without this, a Dealer customer who saw
            # $1,036 in the Order Builder would see $1,594 here.
            pricelist = SaleOrder._resolve_channel_pricelist(design.partner_id)
            if pricelist:
                vals["pricelist_id"] = pricelist.id
            order = SaleOrder.create(vals)

            # ── BOM autoseed (v19.0.5.6.0) ─────────────────────────────
            # For every unique product_tmpl on the fresh SO, make sure
            # a template-level mrp.bom stub exists. Idempotent per
            # _ensure_kitchen_bom. Wrapped so per-template failures
            # don't block quote creation — the D12 pre-quote
            # MISSING_BOM gate already flags absent BOMs as blocking
            # upstream, so this is defence-in-depth on the forward path.
            templates = order.order_line.mapped("product_id.product_tmpl_id")
            for tmpl in templates:
                try:
                    design._ensure_kitchen_bom(tmpl)
                except UserError as e:
                    _logger.warning(
                        "BOM autoseed skipped for %s on SO %s: %s",
                        tmpl.display_name, order.name, e,
                    )

            design.sale_order_id = order.id
            design.state = "quoted"

            # Optional confirm-immediately flow driven by the
            # "Create & Confirm →" button (context={'confirm_
            # immediately': True}). Confirms the SO in place and
            # pivots the return action from SO form to mrp.production.
            #
            # v19.0.5.6.6 fix (Track B P0-A) — wrap action_confirm in a
            # savepoint. If the Production Approval gate raises UserError
            # (southbrook_mrp_pm.sale_order.action_confirm calls
            # `_check_production_approval_gate` BEFORE super()), the
            # bare call would roll the WHOLE request transaction —
            # including the autoseeded BOMs, SO create, and design
            # state="quoted". With the savepoint, only the confirm
            # attempt reverts; everything upstream persists so the rep
            # can navigate to the SO and click Request Production.
            if self.env.context.get("confirm_immediately"):
                # 2026-07-02 UX enhancement — Sales Managers get one-click confirm.
                # The Production Approval gate exists to protect against reps accidentally
                # spawning MOs; Sales Managers already have the authority (they can tick
                # force_production_release on the SO form manually anyway). Auto-setting
                # it here just removes a redundant click. If a rep clicks the button,
                # the savepoint below still gracefully catches the gate and falls back
                # to the SO form.
                is_sales_manager = self.env.user.has_group(
                    "sales_team.group_sale_manager"
                )
                # `force_production_release` is defined by southbrook_mrp_pm
                # (Tier 5), which this Tier-3 module must NOT hard-depend on
                # (that would invert the tier order). Guard the write so the
                # configurator still creates quotations when mrp_pm isn't
                # installed — the field just isn't set in that config.
                if is_sales_manager and "force_production_release" in order._fields:
                    order.sudo().write({"force_production_release": True})
                    _logger.info(
                        "Sales Manager %s auto-set force_production_release on %s "
                        "via kitchen_design.action_create_quotation",
                        self.env.user.name, order.name,
                    )
                    order.message_post(body=(
                        "Production Approval auto-released by Sales Manager %s via "
                        "Create & Confirm → button. Standard approval gate bypass — "
                        "Sales Manager can toggle the SO's Force Production Release "
                        "field back to false if they want to route through the normal "
                        "approval flow instead."
                    ) % self.env.user.name)
                confirm_error = None
                try:
                    with self.env.cr.savepoint():
                        order.action_confirm()
                except UserError as e:
                    confirm_error = str(e)
                    _logger.warning(
                        "action_create_quotation: auto-confirm blocked "
                        "for %s: %s", order.name, confirm_error,
                    )
                    order.message_post(body=(
                        "Auto-confirm blocked: %s\n\n"
                        "Order left in draft. Use Request Production → "
                        "Approve Production before confirming."
                    ) % confirm_error)
                if confirm_error:
                    # Fall back to SO form so the rep sees the draft +
                    # chatter message + Request Production button.
                    return {
                        "type":      "ir.actions.act_window",
                        "res_model": "sale.order",
                        "res_id":    order.id,
                        "views":     [(False, "form")],
                        "view_mode": "form",
                        "target":    "current",
                    }
                Mo = self.env["mrp.production"].sudo()
                mos = Mo.search([("origin", "=", order.name)])
                if len(mos) == 1:
                    return {
                        "type":      "ir.actions.act_window",
                        "res_model": "mrp.production",
                        "res_id":    mos.id,
                        "views":     [(False, "form")],
                        "view_mode": "form",
                        "target":    "current",
                    }
                # Zero-MO or multi-MO — send to origin-filtered list.
                return {
                    "type":      "ir.actions.act_window",
                    "name":      "Manufacturing Orders (%s)" % order.name,
                    "res_model": "mrp.production",
                    "views":     [(False, "list"), (False, "form")],
                    "view_mode": "list,form",
                    "domain":    [("origin", "=", order.name)],
                    "target":    "current",
                }
        return {
            "type":      "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id":    self.sale_order_id.id,
            "views":     [(False, "form")],
            "view_mode": "form",
            "target":    "current",
        }

    # ── Layout engine ────────────────────────────────────────────────────────────
    def _generate_standard_layout(self):
        """Fill the wall run with base + wall cabinet pairs, plus a filler if needed."""
        base_product = self._find_cabinet_product("base")
        wall_product = self._find_cabinet_product("wall")
        # Task 11 (SBK-024) — the resolved base variant's own Width.
        module_w = placed_width_in(base_product) or 24.0
        n = max(0, int(math.floor(self.room_width_in / module_w)))
        remainder = self.room_width_in - n * module_w

        self.cabinet_line_ids.unlink()
        seq = 10
        for i in range(n):
            x = i * module_w
            self._create_line(base_product, seq,      x, 0.0,  0.0)
            # PR3.0 — y/z field-semantics migration: mount height (54")
            # lives in y_position_in, not z; z stays 0 (flush to the
            # back wall).
            self._create_line(wall_product, seq + 5,  x, 54.0, 0.0)
            seq += 10

        # Filler panel for remainder
        if remainder >= 1.0:
            filler = self._find_cabinet_product("filler", raise_if_missing=False)
            if filler:
                self._create_line(filler, seq, n * module_w, 0.0, 0.0, qty=1,
                                   override_width=remainder)

        self.state = "configured"

    def _find_cabinet_product(self, cabinet_type, raise_if_missing=True):
        product = self.env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
            ("product_tmpl_id.southbrook_cabinet_type", "=", cabinet_type),
            ("sale_ok", "=", True),
        ], limit=1)
        if not product and raise_if_missing:
            raise UserError(
                "No saleable %s cabinet product found. "
                "Go to Kitchen 3D Configurator > Cabinet Products and add one." % cabinet_type
            )
        return product or self.env["product.product"]

    def _create_line(self, product, sequence, x, y, z, qty=1, override_width=None):
        tmpl = product.product_tmpl_id
        return self.env["southbrook.kitchen.design.line"].create({
            "design_id":      self.id,
            "sequence":       sequence,
            "product_id":     product.id,
            "quantity":       qty,
            "price_unit":     product.lst_price,
            "cabinet_type":   tmpl.southbrook_cabinet_type,
            # Task 11 (SBK-024) — an explicit override (e.g. a filler's
            # remainder width) always wins; otherwise the product's own
            # Width, not the shared template default.
            "width_in":       override_width or placed_width_in(product) or 24.0,
            "height_in":      tmpl.southbrook_height_in or 34.5,
            "depth_in":       tmpl.southbrook_depth_in or 24.0,
            "x_position_in":  x,
            "y_position_in":  y,
            "z_position_in":  z,
        })

    # ── Task 5 (kitchen templates) — layout manipulation API ──────────
    # Every action rewrites ONLY canonical semantics (wall/run_seq) and
    # re-derives ALL poses through action_auto_arrange — poses are never
    # transformed numerically. Savepoint-atomic: a transform that does
    # not fit raises and leaves the design untouched (the room is never
    # grown). Named action_flip_layout, NOT "mirror" —
    # southbrook.design.reconcile owns that word (manufacturing mirror).

    def action_reflow(self):
        """Thin alias — auto-arrange IS the reflow (never a second one)."""
        self.ensure_one()
        return self.action_auto_arrange(sync=True)

    def _transform_layout(self, wall_map, reverse_walls):
        """Rewrite canonical (wall, run_seq), then re-derive all poses.

        Restores archived canonicals first — the transform must see the
        full canonical set, exactly like the auto-arrange reset does."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                ctx = self.with_context(active_test=False)
                canon = ctx.cabinet_line_ids.filtered(
                    lambda l: l.layout_role == "canonical")
                # Task 9b (review fix; narrowed round 2) — same DEBRIS-
                # SIGNATURE exclusion as the action_auto_arrange reset
                # above (see _is_debris_signature and the round-2 note
                # there for why is_unresolved alone is the wrong key — it
                # would strand legitimate template GHOST placeholders,
                # which are also is_unresolved but must remain revivable).
                # A sentinel line defaults to layout_role="canonical" but
                # was never a real canonical selection, so it must not be
                # swept back into "active" by this reset either.
                canon.filtered(
                    lambda l: not l.active and not l._is_debris_signature()
                ).write({"active": True})
                by_wall = {}
                for line in canon:
                    by_wall.setdefault(line.wall or "back", []).append(line)
                for wall, lines in by_wall.items():
                    seqs = sorted({l.run_seq for l in lines})
                    remap = (dict(zip(seqs, reversed(seqs)))
                             if wall in reverse_walls else {})
                    for line in lines:
                        line.write({
                            "wall": wall_map[wall],
                            "run_seq": remap.get(line.run_seq, line.run_seq),
                        })
                return self.action_auto_arrange(sync=True)
        except kitchen_layout_engine.LayoutCapacityExceeded as e:
            raise UserError(
                "The transformed layout does not fit this room (%s). "
                "Nothing was changed — the room is never grown "
                "automatically." % e) from e

    def action_flip_layout(self, axis="x"):
        """Flip left<->right (axis='x') or back<->front (axis='z')."""
        self.ensure_one()
        if axis == "x":
            return self._transform_layout(
                {"back": "back", "front": "front",
                 "left": "right", "right": "left"},
                reverse_walls=("back", "front"))
        if axis == "z":
            return self._transform_layout(
                {"back": "front", "front": "back",
                 "left": "left", "right": "right"},
                reverse_walls=("left", "right"))
        raise UserError("axis must be 'x' or 'z'")

    def action_rotate_layout(self, quarters=1):
        """Rotate the whole layout clockwise by quarter turns. The room
        is NEVER resized — a rotation that does not fit raises."""
        self.ensure_one()
        cw = {"back": "right", "right": "front",
              "front": "left", "left": "back"}
        res = None
        for _ in range(int(quarters) % 4):
            res = self._transform_layout(cw, reverse_walls=())
        return res if res is not None else self.action_reflow()


# v19.0.4.22.0 audit P2#9 — Q21 zone lexicon (mirrors PUNCHLIST Q21
# on sale.order.line). Declared at module scope so search domains
# and future sale_order_line inheritance can reference the same list
# without duplicating the tuple.
ZONE_SELECTION = [
    ("base_run",  "Base Run"),
    ("wall",      "Wall"),
    ("tall",      "Tall"),
    ("island",    "Island"),
    ("accessory", "Accessory"),
    ("other",     "Other"),
]

_ZONE_FROM_CABINET_TYPE = {
    "base":   "base_run",
    "wall":   "wall",
    "tall":   "tall",
    "corner": "base_run",   # dominant case is base-run corner; user overrides for tall/wall corners
    "filler": "accessory",
    "panel":  "accessory",
    # Task 2 (kitchen templates): appliance spaces are floor-standing run
    # members priced at 0 — Q21 zone = accessory (see sale_order._ZONE_LAYOUT:
    # accessory shares the ground cursor with the base run).
    "appliance": "accessory",
}


class SouthbrookKitchenDesignLine(models.Model):
    _name = "southbrook.kitchen.design.line"
    _description = "Southbrook Kitchen Design Line"
    _order = "sequence, id"

    design_id = fields.Many2one(
        "southbrook.kitchen.design",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence    = fields.Integer(default=10)
    product_id  = fields.Many2one("product.product", required=True, string="Cabinet Product")
    quantity    = fields.Integer(default=1, required=True)
    price_unit  = fields.Monetary(required=True, string="Unit Price")
    currency_id = fields.Many2one(related="design_id.currency_id", store=True)

    # ── D9 — Stable identity for non-destructive save_design ───────────────
    # layout_key matches the JS side's per-cabinet key (e.g. "base-3",
    # "wall-1", "filler-L"). origin distinguishes lines created by the
    # 3D configurator (which can be wiped + recreated on save) from
    # lines created in the backend form (which must NEVER be wiped by
    # a 3D save).
    layout_key = fields.Char(
        string="Layout Key",
        index=True,
        help="Stable identifier matching the 3D configurator's per-cabinet key.",
    )
    origin = fields.Selection(
        selection=[
            ("configurator", "3D Configurator"),
            ("manual",       "Manual / Backend"),
        ],
        string="Source",
        default="manual",
        required=True,
        help="Lines from the configurator can be replaced on re-save; "
             "manual lines are preserved across configurator saves.",
    )

    cabinet_type = fields.Selection([
        ("base",   "Base Cabinet"),
        ("wall",   "Wall Cabinet"),
        ("tall",   "Tall Cabinet"),
        ("filler", "Filler Panel"),
        ("panel",  "Decorative Panel"),
        ("corner", "Corner Unit"),
        # Task 2 (kitchen templates): a customer-appliance stand-in (range/
        # fridge/dishwasher space). Flows through auto-arrange as a
        # floor-standing run member (NOT in the skip tuple) so the run packs
        # around it; priced 0; excluded from the drag catalog.
        ("appliance", "Appliance Space"),
    ], required=True)

    # ── Task 2 (kitchen templates) — template provenance + honesty flags ──
    template_slot_code = fields.Char(
        index=True,
        help="Slot code of the template line this design line was "
             "instantiated from (template provenance; empty for lines "
             "added manually).")
    appliance_type = fields.Selection([
        ("range", "Range / Stove"),
        ("fridge", "Refrigerator"),
        ("dishwasher", "Dishwasher"),
        ("hood", "Range Hood"),
        ("other", "Other Appliance"),
    ], help="Which appliance this Appliance Space line reserves room for.")
    is_unresolved = fields.Boolean(
        default=False,
        help="Honesty flag: the template slot could not be resolved to a "
             "real product. The line is a VISIBLE placeholder (never "
             "silently dropped or substituted) and blocks quoting via "
             "_check_production_ready (UNRESOLVED_SLOT).")

    # ── v19.0.4.22.0 audit P2#9 — Q21 zone tagging ─────────────────────
    # Mirrors the sale.order.line zone lexicon locked by PUNCHLIST Q21
    # so design lines can be aggregated per-zone in the Order Builder
    # without re-deriving from cabinet_type. Stored + indexed so it
    # groups/filters in views. readonly=False lets users override
    # (corner-wall cabinets, island bases, etc.).
    zone = fields.Selection(
        selection=ZONE_SELECTION,
        string="Zone",
        required=True,
        store=True,
        index=True,
        compute="_compute_zone",
        precompute=True,
        readonly=False,
        copy=True,
        # v19.0.5.20.0 C1 fix — NO default here. A truthy default made
        # `_compute_zone`'s "only backfill on empty" guard (below) never
        # fire on create(), so every wall/tall/filler/panel cabinet
        # silently persisted zone='base_run' forever. precompute=True is
        # what makes required=True safe without the default: a stored
        # computed field is normally computed AFTER the INSERT (which
        # would violate the NOT NULL column constraint when create()
        # omits zone — measured, 44 test errors); precompute runs
        # _compute_zone on the new records BEFORE the INSERT, so the
        # field is always populated from _ZONE_FROM_CABINET_TYPE (or the
        # caller's explicit value, which precompute respects). See
        # migrations/19.0.5.20.0/ for the backfill of rows already wrong
        # in the DB.
        help="Q21 zone grouping. Defaults from cabinet_type; override "
             "for island-mounted bases or corner-wall cabinets.",
    )
    zone_label = fields.Char(
        string="Zone Label",
        help="Free-text label surfaced when zone=Other "
             "(e.g. 'Butler's Pantry', 'Coffee Bar').",
    )

    width_in  = fields.Float(required=True, digits=(6, 2))
    height_in = fields.Float(required=True, digits=(6, 2))
    depth_in  = fields.Float(required=True, digits=(6, 2))

    # 3D placement coordinates (inches from room origin)
    x_position_in = fields.Float(string="X Position (in)", digits=(6, 2))
    y_position_in = fields.Float(string="Y Position (in)", digits=(6, 2))
    z_position_in = fields.Float(string="Z Position (in)", digits=(6, 2))

    # 2026-07-01 v19.0.4.20.0 — Smart-pinning (Option C).
    # `pinned` is flipped True the first time the user manually
    # drags or rotates this cabinet in the 3D configurator. Once
    # pinned, the client-side auto-pack (_recomputeLayoutFromItems)
    # and the server /layout regenerator both treat this line as
    # immovable: its x_position_in / z_position_in / rotation_deg
    # are the source of truth, and un-pinned neighbours pack around
    # it. `rotation_deg` is 0/90/180/270 (Y-axis) applied to the
    # cabinet's mesh in the scene and, when persisted, to the
    # nested BOM's rendered orientation.
    pinned = fields.Boolean(
        string="Manually Placed",
        default=False,
        copy=True,
        help="True when the user has manually dragged or rotated this "
             "cabinet; auto-layout will not move or reflow it.",
    )
    rotation_deg = fields.Float(
        string="Rotation (deg)",
        digits=(6, 2),
        default=0.0,
        copy=True,
        help="Y-axis rotation in degrees (0/90/180/270 in normal use).",
    )

    # ── P0.2 (2026-07-11) — Layout-domain wall assignment ───────────────
    # LAYOUT domain only. The manufacturing sale.order.line NEVER carries
    # wall/run info (strict domain separation — see the multi-wall design
    # doc). These are the semantic inputs the pure kitchen_layout_engine
    # consumes to derive x/y/z + rotation_deg. Default "back" reproduces
    # the historical single-run-along-the-back-wall layout, so every
    # existing design line initialises to "back" on column add and its
    # rendered position is unchanged.
    wall = fields.Selection(
        [("back", "Back"), ("left", "Left"),
         ("right", "Right"), ("front", "Front")],
        string="Wall",
        default="back",
        copy=True,
        help="Room wall this cabinet's run is placed along. A layout-domain "
             "input to the layout engine — not a manufacturing attribute.",
    )
    run_seq = fields.Integer(
        string="Run Sequence",
        default=0,
        copy=True,
        help="Order of this cabinet within its wall's run (lower = nearer "
             "the run's origin corner).",
    )

    # ── Arrangement lifecycle (2026-07-12) ──────────────────────────────
    # Auto-arrange is a pure transformation of the customer's CANONICAL
    # selection into a derived arranged layout. These two fields make that
    # explicit and keep the transformation from mutating its own input.
    #
    #   layout_role = canonical → part of the customer's selection; NEVER
    #                             deleted by arrangement (only hidden).
    #   layout_role = derived   → an artifact the arrangement created (e.g.
    #                             a corner cabinet). Ephemeral: every run
    #                             deletes all derived lines and re-derives.
    #
    #   active = False          → a CANONICAL line temporarily superseded
    #                             by a derived artifact (hidden from the
    #                             scene + manufacturing mirror via Odoo's
    #                             automatic active-record filtering). Reset
    #                             to canonical reactivates it.
    #
    # Deliberately NOT named after "corner" — future substitutions (blind
    # corners, fillers, appliance panels, U-shape transitions) reuse this
    # same generic lifecycle.
    layout_role = fields.Selection(
        [("canonical", "Canonical"), ("derived", "Derived")],
        string="Layout Role",
        default="canonical",
        required=True,
        copy=True,
        help="Canonical = the customer's own selection (preserved across "
             "re-arranges). Derived = an artifact created by auto-arrange "
             "(deleted and regenerated on every run).",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
        help="Superseded canonical cabinets are archived (active=False) so "
             "they are hidden from the scene and manufacturing mirror while "
             "a derived artifact stands in for them; a re-arrange restores "
             "them. Never a permanent delete.",
    )

    # ── Recommendation D · Sprint 1 bridge field ────────────────────────
    # Points at the sale.order.line the reconciliation cron mirrors
    # this design line into. See models/sale_order_line.py for the
    # target-side field surface.
    sale_order_line_id = fields.Many2one(
        "sale.order.line",
        string="Bridged Order Line",
        index=True,
        ondelete="set null",
        copy=False,
        help="Rec D Sprint 1 — non-authoring mirror onto the sale.order."
             "line slot. Managed by the southbrook.design.reconcile "
             "cron. Do not set manually.",
    )

    # Computed display
    position_label = fields.Char(
        string="Position",
        compute="_compute_position_label",
    )

    # ── Task 9b (review round 2) — the DEBRIS SIGNATURE, not is_unresolved
    # alone ────────────────────────────────────────────────────────────
    # is_unresolved is dual-purpose: template instantiation (Task 2) also
    # sets it on GHOST placeholder lines that are active=True, carry an
    # ordinary cabinet_type, and legitimately PARTICIPATE in arrange —
    # they can be archived by the corner-absorption/supersede step just
    # like any other canonical line, and MUST be revivable by the reset
    # steps below or re-arrange's idempotence contract (run N times ->
    # identical state) breaks (a ghost that gets permanently stranded
    # archived is state that never converges back).
    #
    # A save_design sentinel/debris line (controllers/main.py's write-
    # path guard) is a DIFFERENT, narrower thing: archived AND
    # is_unresolved AND parked at/beyond DEBRIS_THRESHOLD_IN on x or z.
    # Only THAT three-part signature must be excluded from reactivation.
    # A round-1 review fix used `not is_unresolved` alone, which over-
    # excluded every ghost too, permanently stranding them — this method
    # is the round-2 narrowing, used identically by all three
    # RESET-TO-CANONICAL reactivation sites (action_auto_arrange's reset
    # and _transform_layout's reset below, plus controllers/main.py's
    # save_design down-shift branch) so there is exactly one definition
    # of "is this line debris", not three ad-hoc copies.
    def _is_debris_signature(self):
        self.ensure_one()
        if self.active or not self.is_unresolved:
            return False
        return (abs(self.x_position_in or 0) >= DEBRIS_THRESHOLD_IN
                or abs(self.z_position_in or 0) >= DEBRIS_THRESHOLD_IN)

    # ── Task B3 (Materials geometry-writeback plan, Increment B) ────────
    # This line's own width_in/height_in/depth_in ARE the real per-
    # instance cabinet geometry — including any drag-resize or filler
    # override (_create_line's override_width, the room-depth drag
    # handle) — as opposed to product_tmpl_id.southbrook_width_in/etc,
    # which is only ever the template's nominal/default size. See
    # docs/geometry-writeback-investigation.md §4/§8 Fork A.
    def _sb_dims_mm(self):
        """This line's per-instance dims in mm (×25.4, rounded to the
        nearest int), keyed to match sale.order.line's sb_line_width_mm/
        sb_line_height_mm/sb_line_depth_mm (southbrook_kitchen_3d_
        configurator/models/sale_order_line.py).

        All-or-nothing soft-guard: returns {} unless width_in, height_in,
        AND depth_in are all truthy (>0) — mirrors the same all-or-
        nothing contract sb_material_mrp's `_panel_volume_mm3` already
        enforces on the read side (Task B2), so a partial/garbage record
        never produces a partial override downstream.
        """
        self.ensure_one()
        if not (self.width_in and self.height_in and self.depth_in):
            return {}
        return {
            "sb_line_width_mm":  round(self.width_in * 25.4),
            "sb_line_height_mm": round(self.height_in * 25.4),
            "sb_line_depth_mm":  round(self.depth_in * 25.4),
        }

    @api.depends("cabinet_type")
    def _compute_zone(self):
        for line in self:
            # Only backfill on empty; preserve any prior user override.
            if not line.zone:
                line.zone = _ZONE_FROM_CABINET_TYPE.get(
                    line.cabinet_type, "other",
                )

    @api.constrains("zone", "zone_label")
    def _check_zone_label_only_on_other(self):
        # Silently strip a stale label when the zone flips away from
        # 'other' — matches PUNCHLIST Q21 UX spec.
        for line in self:
            if line.zone != "other" and line.zone_label:
                line.zone_label = False

    @api.depends("cabinet_type", "x_position_in", "z_position_in",
                 "is_unresolved", "appliance_type", "width_in",
                 "template_slot_code")
    def _compute_position_label(self):
        type_map = {
            "base": "Base", "wall": "Wall", "tall": "Tall",
            "filler": "Filler", "panel": "Panel", "corner": "Corner",
            "appliance": "Appliance",
        }
        for line in self:
            # Task 2 (kitchen templates): honesty-first labels — an
            # unresolved slot SAYS so, an appliance space says what it is.
            if line.is_unresolved:
                line.position_label = "UNRESOLVED: %s" % (
                    line.template_slot_code or line.product_id.display_name)
                continue
            if line.cabinet_type == "appliance":
                line.position_label = 'APPLIANCE: %s %g"' % (
                    line.appliance_type or "space", line.width_in or 0)
                continue
            label = type_map.get(line.cabinet_type, line.cabinet_type)
            line.position_label = "%s @ X=%.0f\"" % (label, line.x_position_in)

    # ── T5 (corner remediation 2026-07-30) — ONE corner price-resolution
    # path (V3-products.md claim 3). Before this, the auto-arrange corner
    # writer resolved a variant by EXACT default_code match + hand-aware
    # Hinge Side override, while kitchen_template._resolve_slot's
    # fallback rung resolved template+width and took Odoo's arbitrary
    # "first" variant (`product_variant_id`) — two different
    # product.product rows for the "same" nominal SKU, each with its own
    # attribute price_extra, producing two different frozen prices for
    # what a user perceives as one cabinet. `_sb_resolve_corner_variant`
    # is now the SINGLE resolver (auto-arrange corner writer, the
    # template fallback rung, action_swap_product's price recompute, and
    # the reconcile freeze all call it); `_sb_variant_price` is the ONE
    # price formula. Semantic target is the pre-existing exact-
    # default_code path — the template path converges TO it.
    @api.model
    def _sb_resolve_corner_variant(self, code, hand=None):
        """Resolve ONE product.product for a corner SKU family.

        Precedence (unchanged from the original auto-arrange corner
        writer, now shared):
        1. Exact `default_code` match — lands on whichever specific
           materialized variant carries that code (e.g. the LH twin
           `corner_sku_repair.py` backfills `SB-CORNER` onto).
        2. If `hand` is "L"/"R" and the resolved template has more than
           one materialized variant, prefer the variant whose "Hinge
           Side" PTAV name starts with LH/RH.
        3. Otherwise fall back to the template's default/first variant.

        Returns (variant, tmpl) — both may be empty recordsets when
        `code` resolves to nothing in the catalog at all.
        """
        Product = self.env["product.product"].sudo()
        variant = Product.search([("default_code", "=", code)], limit=1)
        tmpl = variant.product_tmpl_id
        if not tmpl:
            tmpl = self.env["product.template"].sudo().search(
                [("default_code", "=", code)], limit=1)
        if tmpl:
            if hand in ("L", "R") and len(tmpl.product_variant_ids) > 1:
                prefix = "LH" if hand == "L" else "RH"
                handed = tmpl.product_variant_ids.filtered(
                    lambda v: any(
                        pt.attribute_id.name == "Hinge Side"
                        and (pt.name or "").startswith(prefix)
                        for pt in v.product_template_attribute_value_ids))
                variant = handed[:1] or variant
            if not variant:
                variant = (tmpl.product_variant_id
                           or tmpl.product_variant_ids[:1])
        return variant, tmpl

    @api.model
    def _sb_variant_price(self, variant, tmpl=None):
        """The ONE price formula for a resolved variant: `lst_price`
        (the variant's own base + attribute price_extra) with the
        template's `list_price` as a last-resort fallback.

        Deliberately NOT `variant.list_price` — `product.product`
        delegates `list_price` to `product.template` via `_inherits`,
        so it is IDENTICAL for every variant of the same template and
        silently drops attribute price_extra. `lst_price` is the
        computed field (`list_price + price_extra`) that actually
        differs per variant — the "$810-style" exact-variant number V3
        claim 3 identifies as the correct semantic target.
        """
        tmpl = tmpl or (variant.product_tmpl_id if variant else False)
        return ((variant.lst_price if variant else 0.0)
                or (tmpl.list_price if tmpl else 0.0))

    # Task 6 (corner-remediation 2026-07-30, V2-validator.md §4d /
    # V3-products.md claim 2) — ONE place that consults
    # kitchen_layout_engine._CORNER_RESOLVE / _CORNER_STANDALONE to
    # derive a derived corner LINE's expected hand from its layout_key
    # ("corner-<design_id>-<corner>-<layer>", set verbatim by
    # action_auto_arrange). Shared by _check_production_ready's
    # CORNER_HAND_MISMATCH check (§3c above) and action_swap_product's
    # corner-swap hand guard below — neither re-derives the table
    # lookup independently, so a design the writer builds and a swap
    # performed on it can never silently disagree about which hand a
    # junction needs.
    def _sb_corner_expected_hand(self):
        """Expected "L"/"R" for this derived corner line's junction, or
        None when this isn't a derived corner line with a resolvable
        layout_key (nothing to check — e.g. a manually-added line with
        cabinet_type='corner', which was never engine-placed at a real
        junction)."""
        self.ensure_one()
        if not (self.cabinet_type == "corner"
                and self.layout_role == "derived"):
            return None
        key = self.layout_key or ""
        prefix = "corner-%s-" % self.design_id.id
        if not key.startswith(prefix):
            return None
        cname = key[len(prefix):].rsplit("-", 1)[0]
        spec = kitchen_layout_engine._CORNER_RESOLVE.get(cname)
        if spec is not None:
            return spec[3]
        if cname in kitchen_layout_engine._CORNER_STANDALONE:
            # resolve_and_layout hardcodes "L" for the standalone
            # (both-high) front-right corner node.
            return "L"
        return None

    # ── Task 5 (kitchen templates) — line manipulation API ────────────
    # Server-side formalization of the client-only _swapSelectedProduct /
    # _updateSelectedWidth (kitchen_configurator.js) — the client paths
    # stay; these give templates/tests/RPC one canonical, savepoint-
    # atomic entry point. Derived lines are ENGINE-owned: manipulating
    # one directly would be overwritten by the next arrange, so it is
    # refused instead of silently accepted.

    def _guard_canonical(self):
        self.ensure_one()
        if self.layout_role != "canonical":
            raise UserError(
                "Derived lines (corners/fillers) are engine-owned — "
                "adjust the canonical run instead; the engine re-derives "
                "them on every arrange.")

    def _write_then_rearrange(self, vals):
        """Write canonical semantics, then re-derive all poses. A layout
        that no longer fits rolls the whole action back (honesty: the
        room is never grown)."""
        self._guard_canonical()
        try:
            with self.env.cr.savepoint():
                self.write(vals)
                res = self.design_id.action_auto_arrange(sync=True)
                # The engine "fits" impossible runs by SUPERSEDING
                # (archiving) what cannot be placed — accepting that
                # would silently disappear the very cabinet the user
                # just changed. Refuse instead (raises inside the
                # savepoint -> the whole action rolls back).
                if not self.active:
                    raise UserError(
                        "That change does not fit this room — the "
                        "layout engine had to drop this cabinet to lay "
                        "out the run. Nothing was changed; the room is "
                        "never grown automatically.")
                return res
        except kitchen_layout_engine.LayoutCapacityExceeded as e:
            raise UserError(
                "That change does not fit this room (%s). Nothing was "
                "changed — the room is never grown automatically." % e
            ) from e

    def action_swap_product(self, product_id):
        """Replace this line's product. Task 6 (corner-remediation
        2026-07-30, V3-products.md claim 4, "HIGH-1 money vector") — this
        used to take a plain `browse()` with NO domain gate at all, while
        every OTHER product-accepting surface (save_design's server-side
        re-validation, controllers/main.py:420-471; the /products catalog
        endpoint) gates on `southbrook_is_cabinet=True AND sale_ok=True`.
        A tampered client, stale id, or raw RPC call could otherwise swap
        a design line onto ANY `product.product` row in the database —
        archived, non-cabinet, or an unrelated priced item — the same
        money-vector class the save_design comment flags. Resolve
        through the identical catalog contract instead of trusting the
        caller's id.
        """
        self.ensure_one()
        product = self.env["product.product"].search([
            ("id", "=", int(product_id)),
            ("sale_ok", "=", True),
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
        ], limit=1)
        if not product:
            raise UserError(
                "That product isn't in the cabinet catalog (missing, "
                "archived, not sale_ok, or not tagged as a Southbrook "
                "cabinet) — swap refused.")
        tmpl = product.product_tmpl_id
        # A derived corner line stands for a JUNCTION, not a free
        # canonical slot: _guard_canonical's usual "engine-owned, adjust
        # the canonical run instead" refusal is right for width/position,
        # but here the user is legitimately choosing which corner STYLE
        # (lazy-susan/blind/diagonal/wall) fills this same junction —
        # see _swap_derived_corner's own docstring for why this can't
        # just go through _write_then_rearrange like every other line.
        if self.cabinet_type == "corner" and self.layout_role == "derived":
            return self._swap_derived_corner(product, tmpl)
        return self._write_then_rearrange({
            "product_id": product.id,
            # T5 — same shared formula every corner write site uses.
            "price_unit": self._sb_variant_price(product, tmpl),
            "cabinet_type": tmpl.southbrook_cabinet_type or self.cabinet_type,
            # Task 11 (SBK-024) — the newly-swapped-in variant's own
            # Width. (The derived-corner swap above is a SEPARATE path
            # — _swap_derived_corner — and stays on the corner-cell
            # sizing it already owns; not touched here.)
            "width_in": placed_width_in(product) or self.width_in,
            "height_in": tmpl.southbrook_height_in or self.height_in,
            "depth_in": tmpl.southbrook_depth_in or self.depth_in,
            "is_unresolved": False,
        })

    def _swap_derived_corner(self, product, tmpl):
        """Swap a DERIVED corner line's product in place — no
        `_write_then_rearrange`/`action_auto_arrange` round-trip, which
        would just delete this very line and regenerate it back to the
        engine's own default SKU (auto-arrange's docstring: derived
        corner lines are "DERIVED artifact — deleted + regenerated every
        run"), silently discarding whatever the user just chose. Only
        the junction's PLACEMENT (x/y/z/rotation/wall/run_seq/zone/
        layout_key) is preserved untouched; width/height/depth follow
        the new product like any other swap, since a different corner
        archetype genuinely has a different footprint.

        Restricted (Task 6 scope item 3):
        1. `tmpl.southbrook_cabinet_type` must be 'corner' — a base or
           wall cabinet can't stand in for a junction.
        2. The new product's own footprint (`southbrook_width_in` /
           `southbrook_depth_in`, when the template carries them) must
           fit within the cell this junction already reserved — this
           line's OWN current `width_in`/`depth_in`, set by the writer
           from the winning placement rule (or the legacy square).
           Missing dims on the target template are not validated (can't
           check what isn't there), matching the "or self.width_in"
           fallback convention every other swap/write site in this
           class uses.
        3. The junction's expected hand (`_sb_corner_expected_hand`,
           the SAME derivation `_check_production_ready`'s
           CORNER_HAND_MISMATCH check uses) must agree with the new
           product's own Hinge Side PTAV, when it carries one at all —
           a product with no Hinge Side split (e.g. SB-WALL-CORNER) has
           nothing to check and is always allowed, exactly like the
           validator's own skip rule.
        Both restrictions raise a UserError (actionable message,
        no silent substitution) rather than accepting a bad swap.
        """
        self.ensure_one()
        if tmpl.southbrook_cabinet_type != "corner":
            raise UserError(
                "%s is not a corner cabinet — only corner products can "
                "replace a corner junction. Swap refused."
                % tmpl.display_name)
        new_w = tmpl.southbrook_width_in or 0.0
        new_d = tmpl.southbrook_depth_in or 0.0
        cell_w = self.width_in or 0.0
        cell_d = self.depth_in or 0.0
        if (new_w and new_w > cell_w + 0.01) or (
                new_d and new_d > cell_d + 0.01):
            raise UserError(
                "%s (%.1f\" x %.1f\") is too large for this junction's "
                "reserved space (%.1f\" x %.1f\") — swap refused." % (
                    tmpl.display_name, new_w, new_d, cell_w, cell_d))
        expected_hand = self._sb_corner_expected_hand()
        if expected_hand in ("L", "R"):
            prefix = "LH" if expected_hand == "L" else "RH"
            hinge_ptavs = product.product_template_attribute_value_ids.filtered(
                lambda pt: pt.attribute_id.name == "Hinge Side")
            if hinge_ptavs and not any(
                    (pt.name or "").startswith(prefix) for pt in hinge_ptavs):
                raise UserError(
                    "%s is the wrong hand for this junction (needs %s) "
                    "— the corner mechanism will not clear. Swap "
                    "refused." % (product.display_name, prefix))
        self.write({
            "product_id": product.id,
            # T5 — same shared formula every corner write site uses.
            "price_unit": self._sb_variant_price(product, tmpl),
            "width_in": new_w or self.width_in,
            "height_in": tmpl.southbrook_height_in or self.height_in,
            "depth_in": new_d or self.depth_in,
        })
        return {"corners": 0, "removed": 0, "inserted": 0, "swapped": True}

    def action_set_width(self, width_in):
        return self._write_then_rearrange({"width_in": float(width_in)})

    def action_move(self, wall, run_seq):
        if wall not in ("back", "front", "left", "right"):
            raise UserError("wall must be back/front/left/right")
        # `sequence` mirrors run_seq: the engine's auto-assign path (all
        # cabinets on the back wall) orders by flat INPUT order — which
        # is this model's _order (sequence, id) — while the manual path
        # sorts by (run_seq, input idx). Writing both keeps the two
        # ordering keys agreeing on either path.
        return self._write_then_rearrange(
            {"wall": wall, "run_seq": int(run_seq),
             "sequence": int(run_seq)})


# ── v19.0.5.6.12 — Template gallery wizard ────────────────────────────────
# Transient model backing the "Start from Template" customer-onboarding
# gallery. Kept in this file (not a separate wizards/ subdir) because
# the preset catalog lives on SouthbrookKitchenDesign and the two models
# are always deployed together.
class SouthbrookKitchenDesignTemplatePicker(models.TransientModel):
    _name = "kitchen.design.template.picker"
    _description = "Kitchen Design Template Picker"

    # Task 3 (kitchen templates): the picker is now DATA-driven — pick a
    # southbrook.kitchen.template + the parametric knobs (count / module
    # width / appliance sizes), preview, confirm -> action_instantiate ->
    # open the configurator. UX ordering per user directive: room dims
    # live on the design/top frame; here shape (template) leads, then the
    # cabinet selections. Legacy `preset` kept ONLY for compat callers.
    _COMPAT_PRESET_CODES = {
        # Reconciled against the shipped T4 catalog (data/kitchen_templates.xml):
        #   galley  -> GAL-10 (shipped, active)
        #   l_shape -> L-10X8 (ships in T4a, after the SB-CORNER data repair)
        #   u_shape -> U-10X8X10 (SHIPPED 2026-07-27 — unblocked by the
        #              T4a corner inventory: LH + RH twin + wall corner.)
        "empty": None,
        "l_shape": "L-10X8",
        "u_shape": "U-10X8X10",
        "galley": "GAL-10",
    }

    template_id = fields.Many2one(
        "southbrook.kitchen.template", string="Kitchen Shape / Template",
        domain=[("active", "=", True)],
        help="Prebuilt sample kitchen to start from. Shapes whose minimum "
             "room dimensions don't fit your room won't instantiate — the "
             "room is never grown silently.")
    preset = fields.Selection(
        selection=[
            ("empty",   "Empty Room · Blank Canvas"),
            ("l_shape", "L-Shaped Kitchen"),
            ("u_shape", "U-Shaped Kitchen"),
            ("galley",  "Galley Kitchen"),
        ],
        string="Layout Preset (legacy)",
        help="Deprecated compat field — maps onto template codes via "
             "_COMPAT_PRESET_CODES. New UI uses template_id.",
    )
    cabinet_count = fields.Integer(
        string="Number of Cabinets", default=0,
        help="0 = template default. Live-clamped to what fits the room "
             "with the chosen module width and appliance sizes.")
    module_width_in = fields.Selection(
        [("18", '18"'), ("21", '21"'), ("24", '24"'), ("30", '30"')],
        string="Cabinet Size", default="24", required=True)
    range_width_in = fields.Selection(
        # 24" apartment-size range added 2026-07-27 (John: missing from
        # the sample-template picker).
        [("0", "No range"), ("24", '24"'), ("30", '30"'), ("36", '36"'),
         ("48", '48"')],
        string="Range / Stove", default="30")
    fridge_width_in = fields.Selection(
        [("0", "No fridge"), ("30", '30"'), ("33", '33"'), ("36", '36"')],
        string="Refrigerator", default="36")
    dishwasher_width_in = fields.Selection(
        [("0", "No dishwasher"), ("24", '24"')],
        string="Dishwasher", default="24")
    count_max = fields.Integer(
        string="Max Cabinets", compute="_compute_fit", readonly=True)
    fit_summary = fields.Text(
        string="Fit", compute="_compute_fit", readonly=True)
    template_preview = fields.Html(
        compute="_compute_fit", sanitize=False, readonly=True,
        help="Server-generated top-view SVG of the template — never user "
             "input, hence sanitize=False is safe.")
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer (optional)",
        help="Assign a customer up-front so pricing resolves against the "
             "channel pricelist immediately. Leave empty for a walk-in or "
             "showroom demo.",
    )

    def _appliance_widths(self):
        self.ensure_one()
        out = {}
        for atype, raw in (("range", self.range_width_in),
                           ("fridge", self.fridge_width_in),
                           ("dishwasher", self.dishwasher_width_in)):
            if raw and raw != "0":
                out[atype] = float(raw)
        return out

    @api.depends("template_id", "cabinet_count", "module_width_in",
                 "range_width_in", "fridge_width_in", "dishwasher_width_in")
    def _compute_fit(self):
        from markupsafe import Markup
        for rec in self:
            rec.count_max = 0
            rec.fit_summary = False
            rec.template_preview = False
            if not rec.template_id:
                continue
            fit = rec.template_id.parametric_fit(
                rec.cabinet_count or None,
                float(rec.module_width_in or "24"),
                rec._appliance_widths())
            rec.count_max = fit.get("n_max", 0)
            rec.fit_summary = fit["message"] if not fit["ok"] else (
                "%d cabinet(s) at %g\" fit this %g\" room (max %d)." % (
                    fit["count"], fit["module_width_in"],
                    rec.template_id.default_room_width_in, fit["n_max"]))
            rec.template_preview = Markup(
                rec.template_id._generate_thumbnail_svg())

    @api.onchange("template_id", "cabinet_count", "module_width_in",
                  "range_width_in", "fridge_width_in", "dishwasher_width_in")
    def _onchange_parametrics(self):
        # Selection widgets can't grey options in a transient — clamping +
        # fit_summary IS the spec's "constrain or flag" (never grow room).
        for rec in self:
            if rec.template_id and rec.cabinet_count and \
                    rec.count_max and rec.cabinet_count > rec.count_max:
                rec.cabinet_count = rec.count_max

    def action_create(self):
        """Instantiate the chosen template (or a plain empty design) and
        open the 3D configurator. Data-driven — the legacy preset dict is
        no longer consulted by this path (compat maps preset -> code)."""
        self.ensure_one()
        template = self.template_id
        if not template and self.preset and self.preset != "empty":
            code = self._COMPAT_PRESET_CODES.get(self.preset)
            template = code and self.env["southbrook.kitchen.template"].search(
                [("code", "=", code)], limit=1)
            if code and not template:
                # Mapped data template not loaded (e.g. L-10X8 ships only
                # after the corner-SKU repair). Honor the 5.6.12 legacy
                # onboarding contract instead of dead-ending the rep:
                # canonical room dims + starters-or-chatter (T4 fix — the
                # T3 rewrite raised UserError here and silently broke the
                # track_b preset regression pins).
                return self._action_create_legacy_preset()
        if not template:
            # Blank canvas — the legacy 'empty' spec still owns the
            # canonical blank-canvas room dims (5.6.12 pin).
            return self._action_create_legacy_preset()
        design = template.action_instantiate(
            partner_id=self.partner_id.id if self.partner_id else False,
            cabinet_count=self.cabinet_count or None,
            module_width_in=float(self.module_width_in or "24"),
            appliance_widths=self._appliance_widths(),
        )
        return design.action_open_configurator()

    def _action_create_legacy_preset(self):
        """Deprecated 5.6.12 fallback — used only when no data template
        can serve the request (preset='empty'/none, or a compat code
        whose template isn't loaded). Keeps the onboarding contract:
        canonical preset room dims land, starters seed or the skips are
        chatter-logged; the design still opens either way."""
        self.ensure_one()
        Design = self.env["southbrook.kitchen.design"]
        preset = self.preset or "empty"
        spec = Design._get_preset_layout(preset)
        design = Design.create({
            "name": spec["name"] if spec else "New Kitchen Design",
            "state": "draft",
            "partner_id": self.partner_id.id if self.partner_id else False,
        })
        if spec:
            design._apply_kitchen_template(preset)
        return design.action_open_configurator()
