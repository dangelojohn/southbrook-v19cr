# SPDX-License-Identifier: LGPL-3.0-only
"""Task 25 (corner-remediation 2026-07-30) — the invariant fixture suite.

ONE entrypoint asserting the cross-cutting invariants that must hold for
EVERY active shipped kitchen template, on top of (never duplicating) the
per-feature regression pins earlier tasks already shipped:
  - test_template_catalog.py       — every active shipped template
                                      instantiates fully resolved
  - test_zone_corner_l_shape_fixes.py, test_u_shape_template.py,
    test_corner_price_resolution.py — per-template corner geometry/price
  - test_totals_filler_exclusion.py, test_m1_m2_reconcile_fixes.py
                                      — totals/reconcile semantics
  - test_cabinet_variant_ref_repair.py (southbrook_estimating)
                                      — catalogue hygiene repair itself

This module does not re-derive placement math of its own: every footprint
check goes through `kitchen_layout_engine`'s own primitives
(`footprint_from_anchor_mm`, `footprints_overlap`, `wall_graph`) using the
SAME anchor convention (rot 90 persists the high edge, rot 270 the low
edge) the engine and every prior task's tests already use — see
test_zone_corner_l_shape_fixes.py / kitchen_design.py's `_check_production_
ready` check #3/#3b/#7 for the pattern this mirrors.

Per the task-25 brief: any invariant that genuinely fails on current HEAD
is `skipTest`-ed with the concrete evidence, never silently dropped and
never "fixed" here (this file may not touch production code) — see the
task-25 report for the live table.
"""
import re
from collections import Counter

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E
from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    _is_corner_family_code,
)

from .test_corner_repair import ensure_repaired_corner
from .test_template_catalog import _TEST_CODE_PREFIXES

_MM = 25.4                  # inches -> millimetres
_EPS_MM = 2.0                # generic float-safety tolerance (matches the
                              # production validator's own eps, e.g. check
                              # #3's footprints_overlap(eps=2.0))
_WALL_TOUCH_EPS_MM = 25.4     # 1" — a cabinet's footprint counts as "on"
                              # a wall face within this tolerance (reveals/
                              # scribes are real, small, and legitimate)
_RUN_GAP_TOLERANCE_MM = 10.0  # max unexplained gap between two cabinets
                              # sorted consecutively within the same
                              # (wall, zone) run — "adjacency by footprint"

_CORNER_SHAPES = ("l_shape", "u_shape", "g_shape")
_CORNER_ISSUE_CODES = ("CORNER_HAND_MISMATCH", "CABINET_COLLISION",
                        "OUT_OF_ROOM_BOUNDS", "MOTION_ENVELOPE_COLLISION")


def _cab_mm(line):
    return {"width_mm": (line.width_in or 0) * _MM,
            "depth_mm": (line.depth_in or 0) * _MM}


def _place_mm(line):
    return {"x": (line.x_position_in or 0) * _MM,
            "z": (line.z_position_in or 0) * _MM,
            "rotation_deg": line.rotation_deg or 0}


def _footprint(line):
    """The line's real, ANCHOR-convention world footprint — the exact
    primitive `_check_production_ready` uses (kitchen_design.py checks
    #3/#3b/#7), never a parallel model."""
    return E.footprint_from_anchor_mm(_cab_mm(line), _place_mm(line))


def _layer_of_line(line):
    """Base vs. wall(upper) vertical layer — mirrors kitchen_design.py's
    own nested `_footprint_layer`/`_line_layer` helpers (a corner line's
    layer comes from its zone, since the upper corner shares the base
    corner's x/z cell at a different elevation)."""
    if line.cabinet_type == "wall" or (
            line.cabinet_type == "corner" and line.zone == "wall"):
        return "wall"
    return "base"


def _merge_intervals(intervals):
    """Union length (mm) covered by a list of (lo, hi) intervals. Generic
    interval-merge arithmetic over footprints the engine itself already
    computed — not a placement model."""
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    for lo, hi in ordered[1:]:
        if lo <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return sum(hi - lo for lo, hi in merged)


def _variant_width_in(product):
    """The variant's OWN 'Width' product.attribute.value, parsed to
    inches (e.g. PTAV name "24 in" -> 24.0), or None when the product
    carries no Width attribute at all (nothing to check for it)."""
    ptavs = product.product_template_attribute_value_ids.filtered(
        lambda p: p.attribute_id.name == "Width")
    if not ptavs:
        return None
    m = re.search(r"[\d.]+", ptavs[0].name or "")
    return float(m.group()) if m else None


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "invariant_matrix")
class TestInvariantMatrix(TransactionCase):
    """The regression matrix: every invariant below is asserted for
    EVERY active shipped kitchen template (the T4 starter catalog,
    excluding the T1-/T2-/T3-/T5- test-authored fixtures other tasks'
    suites created — the exact filter test_template_catalog.py uses)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Brings the DB's SB-CORNER family AND every cabinet variant's
        # default_code to the "post-repair" state in one call — repair_
        # corner_sku() internally calls repair_corner_family_skus (T4)
        # then repair_cabinet_variant_refs (T23/SBK-037), in that order
        # (corner_sku_repair.py:107-123).
        ensure_repaired_corner(cls.env)
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.partner = cls.env["res.partner"].create(
            {"name": "T25 Invariant Matrix Customer"})
        cls.templates = cls.env["southbrook.kitchen.template"].search(
            []).filtered(lambda t: not t.code.startswith(_TEST_CODE_PREFIXES))
        # The engine's OWN zone->cursor table (kitchen_layout_engine.
        # _cabinet_run_key's authoritative source, per action_auto_
        # arrange's zone_layout=SaleOrder._ZONE_LAYOUT) — the adjacency
        # invariant below groups runs by this, not by zone alone.
        cls.zone_layout = cls.env["sale.order"]._ZONE_LAYOUT

    def setUp(self):
        super().setUp()
        self.assertGreaterEqual(
            len(self.templates), 4,
            "T25 matrix expects the T4 starter catalog to ship >=4 active "
            "templates (see test_template_catalog.py)")

    # ── geometry + corner + reconciliation, per active template ────────

    def test_invariants_hold_for_every_active_template(self):
        for tpl in self.templates:
            with self.subTest(template=tpl.code):
                design = tpl.action_instantiate(partner_id=self.partner.id)
                lines = design.cabinet_line_ids
                self.assertTrue(lines, "%s: must instantiate lines" % tpl.code)

                room = {"width_mm": (design.room_width_in or 0) * _MM,
                        "depth_mm": (design.room_depth_in or 0) * _MM}

                # -- geometry: all actives in-bounds --------------------
                for line in lines:
                    x0, x1, z0, z1 = _footprint(line)
                    self.assertGreaterEqual(
                        x0, -_EPS_MM, "%s/%s out of bounds (x0)"
                        % (tpl.code, line.layout_key))
                    self.assertGreaterEqual(
                        z0, -_EPS_MM, "%s/%s out of bounds (z0)"
                        % (tpl.code, line.layout_key))
                    self.assertLessEqual(
                        x1, room["width_mm"] + _EPS_MM,
                        "%s/%s out of bounds (x1)" % (tpl.code, line.layout_key))
                    self.assertLessEqual(
                        z1, room["depth_mm"] + _EPS_MM,
                        "%s/%s out of bounds (z1)" % (tpl.code, line.layout_key))

                # -- geometry: no same-wall/zone overlap among actives --
                # Same (wall, zone) grouping _check_production_ready's
                # check #3 uses; fillers/panels intentionally bridge
                # gaps / attach flush and are excluded there too.
                by_wall_zone = {}
                for line in lines:
                    if line.cabinet_type in ("filler", "panel"):
                        continue
                    key = (line.wall or "back", line.zone or "base_run")
                    by_wall_zone.setdefault(key, []).append(line)
                for (wall, zone), members in by_wall_zone.items():
                    fps = [(m, _footprint(m)) for m in members]
                    for i in range(len(fps)):
                        for j in range(i + 1, len(fps)):
                            la, fa = fps[i]
                            lb, fb = fps[j]
                            self.assertFalse(
                                E.footprints_overlap(fa, fb, eps=2.0),
                                "%s wall=%s zone=%s: %s overlaps %s"
                                % (tpl.code, wall, zone, la.layout_key,
                                   lb.layout_key))

                # -- geometry: adjacency by footprint -------------------
                # A physical run is (wall, CURSOR) — not (wall, zone):
                # kitchen_layout_engine._cabinet_run_key + sale.order.
                # _ZONE_LAYOUT are the engine's own primitives for this
                # (base_run/tall/accessory all share the "ground" cursor
                # on purpose, e.g. an appliance space sits IN the base
                # run between two cabinets — grouping by zone alone
                # fragments one physical run into false "gaps"). Fillers
                # are real run members here (they close gaps by design);
                # panels (flush end-caps) and corners (span both legs,
                # not a single-axis run member) are excluded, same as
                # the overlap check's exclusions / check #7's OBB path.
                by_run = {}
                for line in lines:
                    if line.cabinet_type in ("panel", "corner"):
                        continue
                    cursor = self.zone_layout.get(
                        line.zone or "base_run", ("ground", 0, 0))[0]
                    by_run.setdefault(
                        (line.wall or "back", cursor), []).append(line)
                for (wall, cursor), members in by_run.items():
                    axis_is_x = wall in ("back", "front")
                    spans = sorted(
                        ((_footprint(m)[0], _footprint(m)[1]) if axis_is_x
                         else (_footprint(m)[2], _footprint(m)[3]), m)
                        for m in members)
                    for i in range(len(spans) - 1):
                        (_, hi_a), la = spans[i]
                        (lo_b, _), lb = spans[i + 1]
                        gap = lo_b - hi_a
                        self.assertLessEqual(
                            gap, _RUN_GAP_TOLERANCE_MM,
                            "%s wall=%s cursor=%s: unexplained %.1fmm gap "
                            "between %s and %s"
                            % (tpl.code, wall, cursor, gap, la.layout_key,
                               lb.layout_key))

                # -- geometry: occupied + remainder == wall length -----
                # "occupied" = the union (mm) of every active line's
                # footprint that actually touches a given wall's face;
                # "remainder" is what's left. The identity only holds
                # when occupied never exceeds the physical wall — i.e.
                # this is the along-wall overflow guard, computed via
                # the SAME footprint_from_anchor_mm primitive as above.
                for seg in E.wall_graph(room)["segments"]:
                    wall = seg["id"]
                    length = seg["length_mm"]
                    axis_is_x = wall in ("back", "front")
                    intervals = []
                    for line in lines:
                        x0, x1, z0, z1 = _footprint(line)
                        touches = (
                            (wall == "back" and z0 <= _WALL_TOUCH_EPS_MM)
                            or (wall == "front" and
                                z1 >= room["depth_mm"] - _WALL_TOUCH_EPS_MM)
                            or (wall == "left" and x0 <= _WALL_TOUCH_EPS_MM)
                            or (wall == "right" and
                                x1 >= room["width_mm"] - _WALL_TOUCH_EPS_MM)
                        )
                        if not touches:
                            continue
                        intervals.append((x0, x1) if axis_is_x else (z0, z1))
                    occupied = _merge_intervals(intervals)
                    remainder = max(0.0, length - occupied)
                    self.assertAlmostEqual(
                        occupied + remainder, length, delta=_EPS_MM,
                        msg="%s wall %s: occupied(%.1f)+remainder(%.1f) != "
                            "wall length(%.1f) — the run overflowed the "
                            "physical wall" % (tpl.code, wall, occupied,
                                                remainder, length))

                # -- geometry: junctions occupied-or-flagged ------------
                if tpl.layout_shape in _CORNER_SHAPES:
                    derived_corners = lines.filtered(
                        lambda l: l.layout_role == "derived"
                        and l.cabinet_type == "corner")
                    corner_issues = [
                        i for i in design._check_production_ready()
                        if i["code"] in _CORNER_ISSUE_CODES]
                    self.assertTrue(
                        derived_corners or corner_issues,
                        "%s: corner-bearing shape has NEITHER a derived "
                        "corner cabinet NOR a flagged geometry issue — a "
                        "junction went silently unresolved" % tpl.code)

                # -- corner: per-junction base+wall corner, hand match --
                corners = lines.filtered(
                    lambda l: l.layout_role == "derived"
                    and l.cabinet_type == "corner")
                if tpl.layout_shape in _CORNER_SHAPES:
                    self.assertTrue(
                        corners,
                        "%s: corner-bearing shape produced no derived "
                        "corner cabinet" % tpl.code)
                hand_mismatches = [
                    i for i in design._check_production_ready()
                    if i["code"] == "CORNER_HAND_MISMATCH"]
                self.assertFalse(
                    hand_mismatches,
                    "%s: hand mismatch(es) at instantiation: %s"
                    % (tpl.code, hand_mismatches))
                key_prefix = "corner-%s-" % design.id
                for cl in corners:
                    # layer suffix on the layout_key ("...-base"/"...
                    # -wall") must agree with the persisted zone (the
                    # exact C8 regression class: corner lines silently
                    # keeping the truthy zone default).
                    if (cl.layout_key or "").startswith(key_prefix):
                        layer = cl.layout_key[len(key_prefix):].rsplit(
                            "-", 1)[-1]
                        expected_zone = "wall" if layer == "wall" else "base_run"
                        self.assertEqual(
                            cl.zone, expected_zone,
                            "%s corner %s: zone=%r disagrees with its own "
                            "layout_key layer suffix (%r)"
                            % (tpl.code, cl.layout_key, cl.zone, layer))
                    # -- corner: price == the unified path's formula ---
                    expected_price = self.Line._sb_variant_price(
                        cl.product_id, cl.product_id.product_tmpl_id)
                    self.assertEqual(
                        cl.price_unit, expected_price,
                        "%s corner %s: price_unit (%.2f) != unified "
                        "_sb_variant_price (%.2f)"
                        % (tpl.code, cl.layout_key, cl.price_unit,
                           expected_price))

                # -- reconciliation: base+wall+corner+filler(+appliance)
                #    vs total_cabinets, AS SHIPPED (total EXCLUDES
                #    fillers — deliberate, test_totals_filler_exclusion.
                #    py's contract) --------------------------------------
                non_filler = lines.filtered(lambda l: l.cabinet_type != "filler")
                filler = lines - non_filler
                self.assertEqual(
                    design.total_cabinets, sum(non_filler.mapped("quantity")),
                    "%s: total_cabinets disagrees with the non-filler line "
                    "quantity sum" % tpl.code)
                self.assertEqual(
                    design.filler_count, sum(filler.mapped("quantity")),
                    "%s: filler_count disagrees with the filler line "
                    "quantity sum" % tpl.code)
                self.assertEqual(
                    design.base_count,
                    sum(lines.filtered(lambda l: l.cabinet_type == "base")
                        .mapped("quantity")),
                    "%s: base_count disagrees with actual base lines" % tpl.code)
                self.assertEqual(
                    design.wall_count,
                    sum(lines.filtered(lambda l: l.cabinet_type == "wall")
                        .mapped("quantity")),
                    "%s: wall_count disagrees with actual wall lines" % tpl.code)

                # -- reconciliation: estimated_price == sum(line subtotals)
                expected_price = sum(l.quantity * l.price_unit for l in lines)
                self.assertAlmostEqual(
                    design.estimated_price, expected_price, places=2,
                    msg="%s: estimated_price != sum of line subtotals"
                        % tpl.code)

                # -- reconciliation: instantiation chatter counts
                #    reconcile with len(cabinet_line_ids) ----------------
                chatter = design.message_ids.filtered(
                    lambda m: "Instantiated from template" in (m.body or ""))
                self.assertTrue(
                    chatter, "%s: no instantiation chatter message" % tpl.code)
                m = re.search(r"(\d+)\s+line\(s\)\s+written",
                              chatter[0].body or "")
                self.assertTrue(
                    m, "%s: chatter missing 'N line(s) written': %r"
                       % (tpl.code, chatter[0].body))
                self.assertEqual(
                    int(m.group(1)), len(lines),
                    "%s: chatter-claimed written count (%s) != "
                    "len(cabinet_line_ids) (%d)"
                    % (tpl.code, m.group(1), len(lines)))

                # -- corner: regenerate-twice byte-identical (modulo
                #    ids/timestamps) ---------------------------------
                def _snapshot(design=design):
                    return sorted(
                        (l.layout_key, l.product_id.id, l.quantity,
                         round(l.price_unit, 4), round(l.width_in or 0, 3),
                         round(l.height_in or 0, 3), round(l.depth_in or 0, 3),
                         round(l.x_position_in or 0, 3),
                         round(l.y_position_in or 0, 3),
                         round(l.z_position_in or 0, 3), l.rotation_deg or 0,
                         l.wall, l.zone, l.cabinet_type)
                        for l in design.cabinet_line_ids)
                before = _snapshot()
                design.action_auto_arrange(sync=True)
                after = _snapshot()
                self.assertEqual(
                    before, after,
                    "%s: regenerating the same template twice is not "
                    "byte-identical (modulo ids/timestamps)" % tpl.code)

    # ── catalogue invariants (repaired-catalogue state, not per-design) ─

    def test_engine_placeable_corner_skus_are_panel_visible(self):
        """Every active corner-family variant (SB-CORNER[-R],
        SB-WALL-CORNER[-R], SB-CORNER-BLIND[-R], SB-CORNER-DIAG[-R] —
        `corner_sku_repair._is_corner_family_code`) must satisfy the
        /products drag-panel contract post-repair: sale_ok=True AND its
        template is southbrook_is_cabinet=True (controllers/main.py's
        catalog contract, ~line 504-507)."""
        Product = self.env["product.product"]
        corner_variants = Product.search([("active", "=", True)]).filtered(
            lambda p: _is_corner_family_code(p.default_code))
        self.assertTrue(
            corner_variants,
            "no corner-family variants found post-repair — repair itself "
            "may have regressed")
        for v in corner_variants:
            with self.subTest(code=v.default_code):
                self.assertTrue(
                    v.sale_ok, "%s is not sale_ok — not panel-visible"
                    % v.default_code)
                self.assertTrue(
                    v.product_tmpl_id.southbrook_is_cabinet,
                    "%s's template is not southbrook_is_cabinet — not "
                    "panel-visible" % v.default_code)

    def test_cabinet_variant_default_codes_unique_and_nonempty_post_repair(self):
        """AC from task-23/SBK-037: every active cabinet-family variant
        carries a unique, non-empty default_code after repair_corner_sku
        (which chains repair_cabinet_variant_refs)."""
        Product = self.env["product.product"]
        cabinet_variants = Product.search([
            ("active", "=", True),
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
        ])
        self.assertTrue(cabinet_variants, "no cabinet variants found")
        codes = []
        for v in cabinet_variants:
            code = (v.default_code or "").strip()
            self.assertTrue(
                code, "cabinet variant id=%s (%s) has an empty "
                "default_code post-repair" % (v.id, v.display_name))
            codes.append(code)
        dupes = {c: n for c, n in Counter(codes).items() if n > 1}
        self.assertFalse(
            dupes, "duplicate cabinet default_code(s) post-repair: %s"
            % dupes)

    def test_placed_width_matches_variant_width_attribute_where_present(self):
        """Catalogue invariant: a design line's placed width_in should
        match its own product variant's 'Width' product.attribute.value
        wherever the variant actually carries one.

        KNOWN OPEN TICKET (per the task-25 brief) — southbrook_width_in
        lives on `product.template` (ONE float shared by every variant
        of that template, models/product_template.py), so it structurally
        cannot represent a specific multi-variant cabinet's own Width
        PTAV. If this reproduces on HEAD, skipTest carries the concrete
        evidence rather than either deleting the check or "fixing"
        production code from a test file.
        """
        mismatches = []
        checked = 0
        for tpl in self.templates:
            design = tpl.action_instantiate(partner_id=self.partner.id)
            for line in design.cabinet_line_ids:
                variant_w = _variant_width_in(line.product_id)
                if variant_w is None:
                    continue
                checked += 1
                if abs((line.width_in or 0) - variant_w) > 0.05:
                    mismatches.append(
                        "%s/%s (product %s): placed width_in=%.2f vs "
                        "variant Width attribute=%.2f"
                        % (tpl.code, line.layout_key,
                           line.product_id.default_code,
                           line.width_in or 0, variant_w))
        if mismatches:
            self.skipTest(
                "KNOWN OPEN TICKET — variant Width attribute not wired to "
                "placed width_in (%d/%d checked lines mismatched); "
                "evidence: %s"
                % (len(mismatches), checked, "; ".join(mismatches[:10])))
