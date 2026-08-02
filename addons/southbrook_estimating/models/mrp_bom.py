# SPDX-License-Identifier: LGPL-3.0-only
"""
mrp.bom extension — completes custom routine #1 in commit 8.

The lead-time rollup (commit 5) and the parametric panel-dimension math
(commit 8) both live in this file as the single mrp.bom extension.

Routine #1 status: DONE as of commit 8. The function is
`_compute_panel_dimensions(width_mm, height_mm, depth_mm, ...)`.

Geometric conventions are documented in PUNCHLIST.md NF14. ALL constants
named in this module are ASSUMED until the canonical #8 workbook lands;
the named-constant pattern lets the workbook's actual values swap in
mechanically.

Returns the panel cut list as a dict of tuples
`(length_mm, width_mm, thickness_mm)` per panel, plus hardware counts +
edge-banding scalar. The cut list is consumed at MO creation time
(commit 8 wires it; current commit ships the function ready for use).

NF11 reminder: lead_time_extra lives on product.attribute.value (master),
not on product.template.attribute.value (variant). The rollup walks
sale.order.line -> product.product -> product.template.attribute.value
-> product_attribute_value_id -> lead_time_extra on the master.
"""
from odoo import api, fields, models


# ----------------------------------------------------------------------
# Geometric constants — per NF14, ALL ASSUMED until #8 confirms.
# ----------------------------------------------------------------------
# Box (carcass) material thickness — 5/8" melamine standard.
BOX_TH = 15.875
# Back panel material — 1/4" hardboard.
BACK_TH = 6.35
# Rabbet depth for back-panel capture (groove routed into sides/top/bottom).
RABBET = 6.35
# Door (or drawer-front) thickness — 3/4" slab/5-piece standard.
DOOR_TH = 18.0
# Door reveal — uniform gap on all four door edges.
DOOR_REVEAL = 3.0
# Shelf tolerance — hand-placement clearance subtracted from inside_width.
SHELF_TOL = 1.5
# Shelf ventilation gap — 1/2" subtracted from depth at the back.
SHELF_VENT_GAP = 12.7
# Toe-kick height — 4" standard, integrated into side panels for base/sink/tall/vanity.
TOEKICK_H = 101.6

# Families that have a toe-kick (sides extend below bottom panel).
# Drawer banks were briefly added 2026-06-25 (R2 PR #9) but per spec they
# are NOT a toe-kick family in their own right — kept here strictly as a
# parity mirror of shared/southbrook_dims.TOEKICK_FAMILIES so the G1
# bom-contents gate (test_bom_contents.test_constants_parity) passes.
# Reverted 2026-06-30 (R3 PR #31) to match the canonical shared set.
TOEKICK_FAMILIES = frozenset({"base", "sink", "tall", "vanity"})


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    # ------------------------------------------------------------------
    # Cut-spec seam (PLM hook point).
    #
    # southbrook_plm.mrp_bom._get_cut_constants overrides this method
    # to read the active southbrook.cut.spec record. When no spec is
    # active, super() (this method) returns the NF14 baseline that
    # ships with the estimating addon — keeping the spec-less install
    # behaving exactly as it did pre-PLM.
    #
    # Per memory southbrook_plm_deploy, this is load-bearing
    # infrastructure: removing the seam causes
    # `AttributeError: 'super' object has no attribute '_get_cut_constants'`
    # on every BoM operation in any DB with southbrook_plm installed.
    # Restored 2026-06-11 after a refactor in a parallel session
    # silently dropped the method.
    # ------------------------------------------------------------------
    @api.model
    def _get_cut_constants(self, cut_spec=None):
        """Return the geometric constants used by panel-dimension math.

        Keys match the NF14 baseline + the southbrook.cut.spec model's
        constants_dict() output. Floats throughout (mm units except the
        last shelf gap pair which are already in mm).

        :param cut_spec: optional recordset exposing ``constants_dict()``
            (a southbrook.cut.spec record, typically). When truthy, its
            values are used directly and NOTHING else in this method
            runs — southbrook_estimating does not depend on
            southbrook_plm and never looks the model up itself; a caller
            that already resolved a specific (e.g. ECO-pinned) spec just
            hands it over here via duck typing. When falsy (the
            default), behaviour is completely unchanged: the NF14
            module-constant baseline below, or — when southbrook_plm is
            installed — its override of this same seam substitutes the
            active spec instead.
        """
        if cut_spec:
            return cut_spec.constants_dict()
        return {
            "box_th": BOX_TH,
            "back_th": BACK_TH,
            "rabbet": RABBET,
            "door_th": DOOR_TH,
            "door_reveal": DOOR_REVEAL,
            "shelf_tol": SHELF_TOL,
            "shelf_vent_gap": SHELF_VENT_GAP,
            "toekick_h": TOEKICK_H,
        }

    southbrook_lead_time_extra = fields.Float(
        string="Southbrook Lead-Time Extra (days)",
        compute="_compute_southbrook_lead_time_extra",
        store=True,
        help=(
            "Sum of lead_time_extra across all attribute values selected "
            "on the BoM's variant. Added to produce_delay at MO creation. "
            "Maple box contributes +14 days per Mapping section 3.5."
        ),
    )

    @api.depends(
        "product_id",
        "product_id.product_template_attribute_value_ids."
        "product_attribute_value_id.lead_time_extra",
    )
    def _compute_southbrook_lead_time_extra(self):
        for bom in self:
            if not bom.product_id:
                bom.southbrook_lead_time_extra = 0.0
                continue
            extras = bom.product_id.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id.lead_time_extra"
            )
            bom.southbrook_lead_time_extra = sum(extras or [0.0])

    # ------------------------------------------------------------------
    # produce_delay roll-up
    # ------------------------------------------------------------------
    # The base mrp.bom carries produce_delay on the product, not directly
    # on the BoM. We expose an effective_produce_delay that callers can read
    # to get base + southbrook bump in one. Commit 8 wires this into the MO
    # creation path.
    effective_produce_delay = fields.Float(
        string="Effective Produce Delay (days)",
        compute="_compute_effective_produce_delay",
        store=True,
    )

    @api.depends(
        # NF16 (caught at live install, refined 2026-05-30):
        # produce_delay in Odoo 19 lives directly on mrp.bom — NOT on
        # product.template or product.product. Confirmed by:
        #   grep produce_delay /usr/lib/python3/.../odoo/addons/mrp/models/
        #     → fields declared in mrp_bom.py line 89
        #   SELECT model FROM ir_model_fields WHERE name='produce_delay'
        #     → mrp.bom
        # The original Phase-1 code was wrong on BOTH the @depends path
        # AND the runtime read (which read bom.product_id.produce_delay
        # instead of bom.produce_delay). Fixed here.
        "produce_delay",
        "southbrook_lead_time_extra",
    )
    def _compute_effective_produce_delay(self):
        for bom in self:
            bom.effective_produce_delay = (
                bom.produce_delay + bom.southbrook_lead_time_extra
            )

    # ==================================================================
    # Custom routine #1 — `_compute_panel_dimensions` (Build Spec section 4)
    # ==================================================================
    # The single piece of genuine parametric math in Phase 1. Returns the
    # complete cut list for a parametric cabinet body from (W, H, D) plus
    # family + door_count + finished_sides.
    #
    # Formulas (per NF14 geometric conventions, frameless euro construction):
    #
    #   inside_width    = width_mm − 2 * BOX_TH
    #   side_L          = (height_mm, depth_mm, BOX_TH)
    #   side_R          = (height_mm, depth_mm, BOX_TH)
    #   top             = (inside_width, depth_mm, BOX_TH)
    #   bottom          = (inside_width, depth_mm, BOX_TH)
    #   back            = (inside_width + 2*RABBET,
    #                      height_mm − 2*BOX_TH + 2*RABBET,
    #                      BACK_TH)
    #   shelf           = (inside_width − SHELF_TOL,
    #                      depth_mm − BACK_TH − RABBET − SHELF_VENT_GAP,
    #                      BOX_TH)
    #   door (1-door)   = (height_mm − 2*DOOR_REVEAL,
    #                      width_mm  − 2*DOOR_REVEAL,
    #                      DOOR_TH)
    #   door (2-door)   = (height_mm − 2*DOOR_REVEAL,
    #                      (width_mm − 3*DOOR_REVEAL) / 2,
    #                      DOOR_TH)
    #
    # Hardware (Mapping section 3.5):
    #   hinge_pair_count        = door_count    (0 for drawer_bank)
    #   handle_count            = door_count    (or drawer_count for drawer_bank)
    #   drawer_slide_pair_count = drawer_count
    #
    # Edge banding (Phase-1 scalar; per-edge mapping deferred to Phase 4):
    #   based on finished_sides + visible front edges
    #
    # When canonical #8 workbook lands, named constants update; tests use
    # re-derivation style (assert against formula, not hardcoded number)
    # so they tolerate constant changes without expected-value updates.
    # ------------------------------------------------------------------
    @api.model
    def _compute_panel_dimensions(
        self,
        width_mm,
        height_mm,
        depth_mm,
        family="base",
        door_count=1,
        drawer_count=0,
        finished_sides="none",
        cut_spec=None,
    ):
        """Return the panel cut list + hardware counts for a parametric cabinet.

        Args:
            width_mm, height_mm, depth_mm: integer mm, the cabinet outer dims.
            family: 'base' | 'wall' | 'sink' | 'tall' | 'corner' | 'vanity'
                | 'drawer' | 'accessory' | 'worktop'. Drives toe-kick logic.
            door_count: 0, 1, or 2. From Rule 3 (width -> door count) or
                from the configured door_count attribute on the variant.
            drawer_count: 0 or N. Used for drawer_bank family.
            finished_sides: 'none' | 'left' | 'right' | 'both'. Drives
                edge-banding length.
            cut_spec: optional — forwarded verbatim to
                ``_get_cut_constants(cut_spec=cut_spec)`` (see that
                method). Falsy/omitted reproduces today's exact output.

        Returns:
            A dict with panel tuples and counts:
              'side_L', 'side_R', 'top', 'bottom', 'back', 'shelf', 'door':
                  each a tuple (length_mm, width_mm, thickness_mm)
                  (or None if the component isn't applicable to this cabinet)
              'shelf_count', 'door_count', 'drawer_count': integers
              'hinge_pair_count', 'handle_count', 'drawer_slide_pair_count': integers
              'edge_banding_length_mm': integer, Phase-1 scalar perimeter sum
        """
        # Phase-3 follow-up — read constants via the seam method so
        # southbrook_plm's active cut-spec flows into the math.
        # Without this indirection, ECO-applied spec changes (e.g.
        # DOOR_REVEAL 3.0 -> 5.0) leave the panel dimensions stuck at
        # the module-level defaults. The seam is contractual per
        # [[southbrook_plm_deploy]]; this resolves the test gap.
        c = self._get_cut_constants(cut_spec=cut_spec)
        box_th = c["box_th"]
        back_th = c["back_th"]
        rabbet = c["rabbet"]
        door_th = c["door_th"]
        door_reveal = c["door_reveal"]
        shelf_tol = c["shelf_tol"]
        shelf_vent_gap = c["shelf_vent_gap"]

        inside_width = width_mm - 2 * box_th

        # ---- Side panels (L and R) — full height + depth.
        side_L = (height_mm, depth_mm, box_th)
        side_R = (height_mm, depth_mm, box_th)

        # ---- Top + bottom panels — capture between sides (frameless).
        top = (inside_width, depth_mm, box_th)
        bottom = (inside_width, depth_mm, box_th)

        # ---- Back panel — captures into rabbet on side/top/bottom.
        back_l = inside_width + 2 * rabbet
        back_w = (height_mm - 2 * box_th) + 2 * rabbet
        back = (back_l, back_w, back_th)

        # ---- Shelf — quantity from height heuristic per NF14.
        if height_mm <= 600:
            shelf_count = 1
        elif height_mm <= 900:
            shelf_count = 2
        else:
            shelf_count = 3
        shelf_l = inside_width - shelf_tol
        shelf_w = depth_mm - back_th - rabbet - shelf_vent_gap
        shelf = (shelf_l, shelf_w, box_th) if shelf_count > 0 else None

        # ---- Door — sized from door_count and reveal.
        if door_count == 1:
            door = (
                height_mm - 2 * door_reveal,
                width_mm - 2 * door_reveal,
                door_th,
            )
        elif door_count == 2:
            door = (
                height_mm - 2 * door_reveal,
                (width_mm - 3 * door_reveal) / 2,
                door_th,
            )
        else:
            door = None

        # ---- Hardware counts (Mapping section 3.5).
        if family == "drawer":
            # Drawer_bank repurposes door_count as drawer-front count per
            # the Phase-1 simplification noted in NF14.
            drawer_count = drawer_count or door_count
            hinge_pair_count = 0
            handle_count = drawer_count
            drawer_slide_pair_count = drawer_count
        else:
            hinge_pair_count = door_count
            handle_count = door_count
            drawer_slide_pair_count = drawer_count

        # ---- Edge banding — Phase-1 scalar (Phase-4 per-edge).
        edge_banding_length_mm = self._compute_edge_banding_length(
            width_mm, height_mm, depth_mm, finished_sides, cut_spec=cut_spec
        )

        return {
            "side_L": side_L,
            "side_R": side_R,
            "top": top,
            "bottom": bottom,
            "back": back,
            "shelf": shelf,
            "shelf_count": shelf_count,
            "door": door,
            "door_count": door_count,
            "drawer_count": drawer_count,
            "hinge_pair_count": hinge_pair_count,
            "handle_count": handle_count,
            "drawer_slide_pair_count": drawer_slide_pair_count,
            "edge_banding_length_mm": edge_banding_length_mm,
        }

    @api.model
    def _compute_edge_banding_length(
        self, width_mm, height_mm, depth_mm, finished_sides, cut_spec=None
    ):
        """Phase-1 scalar edge-banding length per NF14.

        Computes total banding length as a sum of:
          - Both finished side panels' full perimeter (if finished_sides='both')
          - One finished side panel's full perimeter (if 'left' or 'right')
          - Top panel front edge (always visible)
          - Bottom panel front edge (always visible)

        Per-edge mapping is deferred to Phase 4 (Accucutt nest spec).

        Bug fixed here (same pass that added ``cut_spec``): this method
        used to read the module-level ``BOX_TH`` constant directly
        instead of going through the ``_get_cut_constants()`` seam like
        every other panel formula in ``_compute_panel_dimensions`` above
        — so an ECO-applied cut spec changed every panel dimension
        EXCEPT the edge-banding scalar, which silently stayed pinned to
        the code default. Routing through the seam (with the same
        ``cut_spec`` forwarding semantics: falsy/omitted -> unchanged
        output) closes that gap.
        """
        box_th = self._get_cut_constants(cut_spec=cut_spec)["box_th"]
        bands = 0

        # Side-panel perimeter banding for finished sides.
        side_perim = 2 * (height_mm + depth_mm)
        if finished_sides == "both":
            bands += 2 * side_perim
        elif finished_sides in ("left", "right"):
            bands += side_perim

        # Top + bottom front edges — always visible, always banded.
        inside_width = width_mm - 2 * box_th
        bands += 2 * inside_width

        return int(bands)
