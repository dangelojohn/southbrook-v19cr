# SPDX-License-Identifier: LGPL-3.0-only
"""sb.cutlist + sb.cutlist.line — the panel cut list for one MO.

Generation source of truth: shared.southbrook_dims.panel_cut_list — the
same module Odoo's G1 test imports and the FreeCAD bridge renders from.
Drift between cutlist geometry and rendered geometry is therefore
impossible by construction as long as G1 passes.

Toe-kick is integrated into the side panels and is NOT emitted as a
cutlist line. The shared.southbrook_dims.toe_kick() function returns a
metadata dict, never a panel tuple — and the generation code asserts
that contract by never iterating the toe_kick key.
"""
import json
import logging
from typing import Any, Dict, Optional

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


PANEL_NAMES = [
    ("side_L", "Side (Left)"),
    ("side_R", "Side (Right)"),
    ("top", "Top"),
    ("bottom", "Bottom"),
    ("back", "Back"),
    ("adjustable_shelf", "Adjustable Shelf"),
    ("door", "Door / Drawer Face"),
    # R3 (PR #31) — explicit drawer-front cut for drawer banks. The legacy
    # "door" panel is overloaded to mean "door or drawer face" but
    # door() in shared.southbrook_dims returns None for door_count==0
    # (drawer banks), leaving the cutlist short one row vs the
    # test_p1_auto_emit_cutlist + test_build_from_order_line expectation.
    # Emitting drawer_front explicitly closes that gap without disturbing
    # shared.southbrook_dims (G2-locked) or the "door" panel contract.
    ("drawer_front", "Drawer Front"),
]

SUBSTRATE_CHOICES = [
    ("melamine_white_5_8", "5/8\" White Melamine"),
    ("melamine_oak_5_8", "5/8\" Oak Melamine"),
    ("mdf_5_8", "5/8\" MDF"),
    ("hardboard_1_4", "1/4\" Hardboard (Backs)"),
    ("ply_3_4", "3/4\" Plywood (Doors)"),
]

GRAIN_DIRECTIONS = [
    ("with_grain", "With Grain (Length)"),
    ("cross_grain", "Cross Grain (Width)"),
    ("no_grain", "No Grain"),
]

CUTLIST_STATES = [
    ("draft", "Draft"),
    ("exported", "Exported to Nesting"),
    ("nested", "Nested"),
    ("done", "Done"),
]


# Substrate defaults per panel type. Backs are hardboard; doors are plywood
# (or whatever the configurator-resolved door spec says — future wiring);
# everything else is 5/8" white melamine carcass standard.
DEFAULT_SUBSTRATE_BY_PANEL = {
    "side_L": "melamine_white_5_8",
    "side_R": "melamine_white_5_8",
    "top": "melamine_white_5_8",
    "bottom": "melamine_white_5_8",
    "back": "hardboard_1_4",
    "adjustable_shelf": "melamine_white_5_8",
    "door": "ply_3_4",
    "drawer_front": "ply_3_4",
}


class SbCutlist(models.Model):
    _name = "sb.cutlist"
    _description = "Southbrook Cabinet Cut List"
    _inherit = ["southbrook.qr.mixin"]
    _order = "id desc"
    _qr_kind = "cutlist"

    name = fields.Char(required=True, default=lambda self: _("New"))
    mo_id = fields.Many2one(
        comodel_name="mrp.production",
        string="Manufacturing Order",
        ondelete="cascade",
        index=True,
    )
    state = fields.Selection(
        CUTLIST_STATES,
        default="draft",
        tracking=True,
        required=True,
    )
    line_ids = fields.One2many(
        comodel_name="sb.cutlist.line",
        inverse_name="cutlist_id",
        string="Cut List Lines",
    )
    line_count = fields.Integer(compute="_compute_line_count", store=True)

    # Nesting round-trip stash. Stored as JSON so the cutting/nesting
    # division can carry sheet-yield + waste metrics back without us
    # needing a model schema for every nesting tool variant.
    nesting_result_json = fields.Text(string="Nesting Result (JSON)")

    # SAMI PRD IOT-07 (2026-06-26) — parsed nesting metrics from
    # from_nesting_result. Stored so dashboards + cost roll-ups don't
    # have to re-parse the JSON each time.
    nesting_sheets_used = fields.Integer(string="Sheets Used", readonly=True)
    nesting_yield_pct = fields.Float(
        string="Yield %", readonly=True, digits=(8, 2),
        help="Percentage of board material that became panels. From "
             "the last from_nesting_result ingest.",
    )
    nesting_waste_pct = fields.Float(
        string="Waste %", readonly=True, digits=(8, 2))
    nesting_scrap_ids = fields.Many2many(
        "stock.scrap",
        "sb_cutlist_scrap_rel", "cutlist_id", "scrap_id",
        string="Scrap Records",
        readonly=True,
        help="stock.scrap records auto-created by from_nesting_result "
             "from the actual Accucutt offcut data.",
    )

    # 2026-08 cut-spec wiring — traceability: which ECO-governed
    # southbrook.cut.spec (if any) drove this cutlist's geometry.
    #
    # Deliberately a plain Integer + Char snapshot, NOT a Many2one to
    # southbrook.cut.spec, even though that model lives in a file this
    # task owns (sb_cutlist.py). southbrook_kitchen_mrp does NOT depend
    # on southbrook_plm — they are sibling addons (see this addon's own
    # __manifest__.py: 'depends' has no southbrook_plm entry), by design,
    # so sb.production.package._resolve_cut_spec() can run on a database
    # that never installed PLM at all. A Many2one field's comodel is
    # validated at field-setup time with a hard assertion
    # (`assert self.comodel_name in model.pool`, odoo/orm/
    # fields_relational.py) — if southbrook.cut.spec isn't registered,
    # that assertion fails and THIS MODULE FAILS TO LOAD, for every
    # database that has kitchen_mrp without PLM. A plain id/name capture
    # gives real traceability (join to southbrook.cut.spec by id when
    # PLM is present) without that hard-dependency risk, and without
    # editing this addon's manifest (out of scope for this pass).
    cut_spec_used_id = fields.Integer(
        string="Cut Spec Used (ID)",
        readonly=True,
        copy=False,
        help="Database id of the southbrook.cut.spec record whose "
             "constants drove this cutlist's geometry, or 0/empty when "
             "no ECO-governed spec was resolved (the module-default "
             "NF14 constants were used instead — today's behaviour). "
             "When southbrook_plm is installed, resolve the actual "
             "record with env['southbrook.cut.spec'].browse(id).",
    )
    cut_spec_used_name = fields.Char(
        string="Cut Spec Used",
        readonly=True,
        copy=False,
        help="Display name of the cut spec captured at generation time "
             "— a human-readable snapshot alongside cut_spec_used_id.",
    )

    @api.depends("line_ids")
    def _compute_line_count(self):
        for r in self:
            r.line_count = len(r.line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sb.cutlist"
                ) or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Generation from panel_cut_list (shared.southbrook_dims)
    # ------------------------------------------------------------------
    @api.model
    def generate_lines_from_panel_dict(
        self,
        cutlist,
        panel_dict: Dict[str, Any],
        drawer_count: int = 0,
        constants: Optional[Dict[str, Any]] = None,
    ):
        """Create sb.cutlist.line records from a shared.southbrook_dims
        panel_cut_list() output dict.

        toe_kick is INTENTIONALLY SKIPPED — its value in the panel dict
        is a metadata descriptor (dict), not a (length, width, thickness)
        tuple, and emitting it as a cutlist line would create a phantom
        panel that the shop floor would then try to cut. That contract is
        enforced here AND in the corresponding G1 test.

        drawer_count (R3 PR #31) — when > 0, a single drawer_front line
        is emitted in addition to the base panels. Geometry is derived
        from the cabinet bounding box (side_L gives height/depth, top
        gives inside_width) using the canonical DOOR_REVEAL convention.
        This closes the test_p1_auto_emit_cutlist + test_build_from_order_line
        gap (cutlist used to stop at 6 records for drawer banks because
        shared.southbrook_dims.door() returns None for door_count==0)
        without disturbing shared.southbrook_dims (G2-locked).

        constants (2026-08 cut-spec wiring): optional dict, same shape as
        southbrook.cut.spec.constants_dict() / shared.southbrook_dims'
        `constants` override — used ONLY for the drawer_front geometry
        below (the other panel keys are already-computed tuples handed
        in via panel_dict, which panel_cut_list() computed with whatever
        constants its own caller passed it — nothing further to do for
        those here). Falls back to the same literal reveal/thickness
        this method has always used when a key is absent or `constants`
        is None, so default output is unchanged.
        """
        Line = self.env["sb.cutlist.line"]
        keys_to_emit = ("side_L", "side_R", "top", "bottom", "back",
                        "adjustable_shelf", "door")
        shelf_count = int(panel_dict.get("shelf_count") or 0)

        for key in keys_to_emit:
            value = panel_dict.get(key)
            if value is None:
                continue
            if not isinstance(value, tuple) or len(value) != 3:
                # Defensive: shared.southbrook_dims schema is locked, but
                # a regression elsewhere would surface here cleanly.
                raise UserError(_(
                    "Panel '%(panel)s' has unexpected shape %(value)s — "
                    "expected (length_mm, width_mm, thickness_mm)."
                ) % {"panel": key, "value": value})

            length_mm, width_mm, thickness_mm = value
            qty = 1
            if key == "adjustable_shelf" and shelf_count > 0:
                qty = shelf_count

            Line.create({
                "cutlist_id": cutlist.id,
                "panel_name": key,
                "qty": qty,
                "length_mm": length_mm,
                "width_mm": width_mm,
                "thickness_mm": thickness_mm,
                "substrate": DEFAULT_SUBSTRATE_BY_PANEL.get(
                    key, "melamine_white_5_8"
                ),
                "grain_dir": "with_grain" if key in (
                    "side_L", "side_R", "door"
                ) else "no_grain",
                "edge_banding_config": json.dumps(
                    self._default_edge_banding(key)
                ),
            })

        # R3 — drawer-front emission for drawer banks (door_count==0,
        # drawer_count>0). Derive geometry locally from the panel_dict's
        # already-validated tuples so we never re-import the G2-locked
        # shared module constants here.
        if drawer_count and drawer_count > 0:
            side = panel_dict.get("side_L")
            top_panel = panel_dict.get("top")
            if (side and top_panel
                    and isinstance(side, tuple) and len(side) == 3
                    and isinstance(top_panel, tuple) and len(top_panel) == 3):
                cabinet_height_mm = side[0]
                # top tuple is (inside_width_mm, depth_mm, thickness_mm).
                cabinet_inside_width_mm = top_panel[0]
                # Uniform reveal between drawer faces and at top/bottom
                # mirrors shared.southbrook_dims.DOOR_REVEAL without
                # importing it. Each face: (cabinet_h - (N+1)*reveal) / N.
                # 2026-08 cut-spec wiring: read from the caller's
                # `constants` dict when supplied (same door_reveal/
                # door_th keys southbrook.cut.spec.constants_dict() /
                # shared.southbrook_dims use), falling back to these
                # same literals — the historical hardcode — so default
                # output is byte-identical when constants is None or
                # omits a key.
                c = constants or {}
                reveal_mm = c.get("door_reveal", 3.0)
                door_th_mm = c.get("door_th", 18.0)
                face_height_mm = (
                    cabinet_height_mm - (drawer_count + 1) * reveal_mm
                ) / drawer_count
                # Reuse the inside-width measurement — drawer faces span
                # the full width minus side-reveal (handled in inside_width
                # already; no further subtraction needed).
                face_width_mm = cabinet_inside_width_mm
                Line.create({
                    "cutlist_id": cutlist.id,
                    "panel_name": "drawer_front",
                    "qty": drawer_count,
                    "length_mm": face_height_mm,
                    "width_mm": face_width_mm,
                    "thickness_mm": door_th_mm,
                    "substrate": DEFAULT_SUBSTRATE_BY_PANEL.get(
                        "drawer_front", "ply_3_4"),
                    "grain_dir": "with_grain",
                    "edge_banding_config": json.dumps(
                        self._default_edge_banding("drawer_front")
                    ),
                })

    def _default_edge_banding(self, panel_name: str) -> Dict[str, bool]:
        """Phase-1 edge-banding default per panel. Per-edge precision
        lands when the Accucutt-style nest spec is finalised (Module 4
        is the structural seam, not the substantive precision)."""
        if panel_name in ("top", "bottom"):
            return {"front": True, "back": False, "left": False, "right": False}
        if panel_name in ("side_L", "side_R"):
            return {"front": True, "back": False, "left": False, "right": False}
        if panel_name in ("door", "drawer_front"):
            # Drawer faces band all four edges like doors.
            return {"front": True, "back": True, "left": True, "right": True}
        return {"front": False, "back": False, "left": False, "right": False}

    # ------------------------------------------------------------------
    # Nesting interface (round-trip stub)
    # ------------------------------------------------------------------
    def to_nesting_envelope(self) -> dict:
        """Return a deterministic JSON envelope the cutting/nesting
        division can consume. Versioned so future schema changes don't
        silently break consumers.

        2026-06-25 — schema bumped to v2 (additive over v1). v2 adds:
          - reference_origin: declares the (0,0,0) corner convention so
            downstream nesters don't have to guess. Standard cabinet-
            industry convention: bottom-front-left, with X=width,
            Y=height, Z=depth (positive into the cabinet).
          - mo (object) instead of mo_id (int) — name + product so
            partners can categorize without an Odoo round-trip.
          - units (string) at top level — explicit "mm" so partners
            who do per-batch unit-toggling parse correctly.

        Pre-existing v1 fields kept verbatim for back-compat.
        """
        self.ensure_one()
        mo_block = None
        if self.mo_id:
            mo_block = {
                "id": self.mo_id.id,
                "name": self.mo_id.name,
                "product": (self.mo_id.product_id.name
                            if self.mo_id.product_id else None),
                "product_default_code": (self.mo_id.product_id.default_code
                                         if self.mo_id.product_id else None),
            }
        return {
            "schema": "southbrook.nesting.v2",
            "units": "mm",
            "reference_origin": {
                "anchor": "bottom_front_left",
                "x_axis": "width",
                "y_axis": "height",
                "z_axis": "depth_into_cabinet",
            },
            "cutlist_id": self.id,
            "cutlist_name": self.name,
            # v1 compatibility: keep both mo_id (int) and mo (object).
            "mo_id": self.mo_id.id if self.mo_id else None,
            "mo": mo_block,
            "panels": [
                {
                    "panel_name": ln.panel_name,
                    "qty": ln.qty,
                    "length_mm": ln.length_mm,
                    "width_mm": ln.width_mm,
                    "thickness_mm": ln.thickness_mm,
                    "substrate": ln.substrate,
                    "grain_dir": ln.grain_dir,
                    "edge_banding": json.loads(ln.edge_banding_config or "{}"),
                    # Reserved for v3 (per HOMAG_NESTING_EXPORT_AUDIT):
                    # "bore_positions": [...],
                    # "edge_banding_tape_skus": {...}.
                    # Emitted as null so partners can negotiate ahead.
                    "bore_positions": None,
                    "edge_banding_tape_skus": None,
                }
                for ln in self.line_ids
            ],
        }

    def from_nesting_result(self, payload: dict) -> None:
        """Accept a nesting result, parse top-level metrics, create
        stock.scrap records for actual offcut waste, advance to nested.

        Expected payload shape (v1, plus v2 fields tolerated):
          {
            "schema": "southbrook.nesting.v1" | "southbrook.nesting.v2",
            "sheets_used": int,
            "yield_pct": float,
            "waste_pct": float,
            "offcuts": [                            # SAMI PRD IOT-07
              {
                "product_id": int (optional),       # Odoo product.product
                "substrate": str (optional),        # falls back to bom substrate
                "qty_sqm": float,
                "location_id": int (optional),      # source stock location
                "reason": str (optional),           # description
              },
              ...
            ]
          }

        Schema acceptance:
          - "southbrook.nesting.v1" — original, accepted forever
          - "southbrook.nesting.v2" — current envelope, accepted
          - anything else — UserError

        Stock.scrap creation rules:
          - Skipped silently if `offcuts` key is missing (back-compat).
          - For each offcut, resolve product_id (or substrate→product
            via env.ref fallback). Skip silently if neither resolves —
            we don't want a typo in one row to abort the rest.
          - scrap_qty = qty_sqm (caller's responsibility to choose UOM).
          - origin = "Nesting result: <cutlist name>"
          - production_id linked when self.mo_id is set so the scrap
            attributes against the right MO.
        """
        self.ensure_one()
        if not isinstance(payload, dict):
            raise UserError(_("Nesting result must be a JSON object."))
        schema = payload.get("schema")
        if schema not in ("southbrook.nesting.v1", "southbrook.nesting.v2"):
            raise UserError(_(
                "Nesting result schema mismatch: expected "
                "southbrook.nesting.v1 or v2, got %s"
            ) % schema)
        # Parse top-level metrics into stored fields.
        write_vals = {
            "nesting_result_json": json.dumps(payload),
            "state": "nested",
        }
        if isinstance(payload.get("sheets_used"), int):
            write_vals["nesting_sheets_used"] = payload["sheets_used"]
        if isinstance(payload.get("yield_pct"), (int, float)):
            write_vals["nesting_yield_pct"] = float(payload["yield_pct"])
        if isinstance(payload.get("waste_pct"), (int, float)):
            write_vals["nesting_waste_pct"] = float(payload["waste_pct"])
        # SAMI PRD IOT-07 — create scrap records per offcut.
        Scrap = self.env["stock.scrap"]
        scrap_ids = []
        offcuts = payload.get("offcuts") or []
        if isinstance(offcuts, list):
            for entry in offcuts:
                if not isinstance(entry, dict):
                    continue
                product = self._resolve_offcut_product(entry)
                if not product:
                    continue
                qty = entry.get("qty_sqm") or 0.0
                if qty <= 0:
                    continue
                vals = {
                    "product_id": product.id,
                    "scrap_qty": float(qty),
                    "origin": _("Nesting result: %s") % (self.name or "?"),
                }
                if self.mo_id:
                    vals["production_id"] = self.mo_id.id
                loc_id = entry.get("location_id")
                if isinstance(loc_id, int):
                    vals["location_id"] = loc_id
                try:
                    scrap = Scrap.create(vals)
                    scrap_ids.append(scrap.id)
                except Exception as exc:  # noqa: BLE001
                    # One bad entry shouldn't abort the rest, but DO surface it
                    # — a silent pass let the cutlist flip to 'nested' with zero
                    # scrap and no trace (e.g. the user lacks stock.scrap create
                    # rights). Log so the gap is observable. (Audit LOW-4.)
                    _logger.warning(
                        "Nesting offcut scrap create failed for cutlist %s "
                        "(product=%s): %s", self.name or self.id,
                        product.default_code or product.id, exc)
        if scrap_ids:
            write_vals["nesting_scrap_ids"] = [(6, 0, scrap_ids)]
        self.write(write_vals)

    def _resolve_offcut_product(self, entry):
        """Best-effort product lookup for an offcut entry.

        Order of resolution:
          1. entry['product_id'] — direct Odoo product.product ID
          2. entry['substrate'] — string slug; tries env.ref(
             'southbrook_kitchen_mrp.product_substrate_<slug>')
          3. Returns False if neither resolves — caller should skip
             this entry cleanly.
        """
        self.ensure_one()
        Product = self.env["product.product"]
        pid = entry.get("product_id")
        if isinstance(pid, int):
            p = Product.browse(pid).exists()
            if p:
                return p
        substrate = entry.get("substrate")
        if isinstance(substrate, str) and substrate:
            try:
                return self.env.ref(
                    "southbrook_kitchen_mrp.product_substrate_%s" % substrate,
                    raise_if_not_found=False,
                )
            except Exception:  # noqa: BLE001
                pass
        return False


class SbCutlistLine(models.Model):
    _name = "sb.cutlist.line"
    _description = "Southbrook Cabinet Cut List Line"
    _order = "cutlist_id, sequence, id"

    cutlist_id = fields.Many2one(
        comodel_name="sb.cutlist",
        ondelete="cascade",
        required=True,
        index=True,
    )
    sequence = fields.Integer(default=10)
    panel_name = fields.Selection(PANEL_NAMES, required=True)
    qty = fields.Integer(default=1, required=True)
    length_mm = fields.Float(string="Length (mm)", digits=(8, 3))
    width_mm = fields.Float(string="Width (mm)", digits=(8, 3))
    thickness_mm = fields.Float(string="Thickness (mm)", digits=(8, 3))
    substrate = fields.Selection(
        SUBSTRATE_CHOICES, default="melamine_white_5_8",
    )
    grain_dir = fields.Selection(GRAIN_DIRECTIONS, default="no_grain")
    edge_banding_config = fields.Text(
        string="Edge Banding Config (JSON)",
        help="Per-edge banding flags as JSON: "
             "{'front': bool, 'back': bool, 'left': bool, 'right': bool}",
    )
