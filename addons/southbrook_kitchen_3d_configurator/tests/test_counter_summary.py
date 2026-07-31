# SPDX-License-Identifier: LGPL-3.0-only
"""Task 7 (SBK-033, corner-remediation 2026-07-30) — ONE server-computed
counter summary.

V4-counters-templates.md claim 1 (CONFIRMED): four different places
computed their OWN count of "how many cabinets" for the same design
(header 9, sidebar 4+2, record 13, summary 10+2) because base_count/
wall_count were the ONLY per-type buckets anywhere — corner, tall, panel
and appliance lines were invisible to every count surface even though
they're real, priced, active lines.

This module pins the fix's contract on the server side:
  1. tall_count/corner_count/panel_count/appliance_count/unplaced_count/
     summary_total exist and are computed by the SAME _compute_totals
     method as the pre-existing base_count/wall_count/total_cabinets/
     filler_count (single source of truth — no parallel definition).
  2. IDENTITY: base_count + wall_count + tall_count + corner_count +
     filler_count + panel_count + appliance_count == len(active
     cabinet_line_ids), for EVERY active shipped template instantiated
     (the binding-constraint CI assertion) — cabinet_type is a closed
     Selection, so every active line has exactly one of these seven
     buckets.
  3. unplaced_count is a DIAGNOSTIC OVERLAY, not an eighth additive
     bucket: an is_unresolved line is already counted once under its
     own cabinet_type bucket (matching base_count/wall_count's existing,
     already-pinned behaviour — see test_invariant_matrix.py's own
     oracle) — adding it a second time would break the identity above.
  4. _get_counter_summary()'s dict mirrors the stored fields 1:1 (no
     drift between what's stored and what's shipped to the client).
  5. total_cabinets/filler_count/base_count/wall_count keep their
     EXACT pre-existing formulas (T6's filler-exclusion is untouched;
     test_totals_filler_exclusion.py and test_invariant_matrix.py
     already pin those — this file does not re-derive them).
"""
from odoo.tests import TransactionCase, tagged

from .test_corner_repair import ensure_repaired_corner
from .test_template_catalog import _TEST_CODE_PREFIXES

_BUCKET_FIELDS = (
    "base_count", "wall_count", "tall_count", "corner_count",
    "filler_count", "panel_count", "appliance_count",
)


def _identity_sum(design):
    return sum(getattr(design, f) for f in _BUCKET_FIELDS)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestCounterSummaryIdentity(TransactionCase):
    """The CI assertion: for each active template instantiated, summary
    parts sum to the active line count."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_repaired_corner(cls.env)
        cls.partner = cls.env["res.partner"].create(
            {"name": "T7 Counter Summary Customer"})
        cls.templates = cls.env["southbrook.kitchen.template"].search(
            []).filtered(lambda t: not t.code.startswith(_TEST_CODE_PREFIXES))

    def setUp(self):
        super().setUp()
        self.assertGreaterEqual(
            len(self.templates), 4,
            "expects the T4 starter catalog to ship >=4 active templates")

    def test_bucket_sum_equals_active_line_count_for_every_template(self):
        for tpl in self.templates:
            with self.subTest(template=tpl.code):
                design = tpl.action_instantiate(partner_id=self.partner.id)
                lines = design.cabinet_line_ids
                self.assertTrue(lines, "%s: must instantiate lines" % tpl.code)
                self.assertEqual(
                    _identity_sum(design), len(lines),
                    "%s: base+wall+tall+corner+filler+panel+appliance "
                    "(%d) must equal len(active cabinet_line_ids) (%d)"
                    % (tpl.code, _identity_sum(design), len(lines)))
                self.assertEqual(
                    design.summary_total, len(lines),
                    "%s: summary_total must equal len(active "
                    "cabinet_line_ids)" % tpl.code)
                self.assertEqual(
                    design.summary_total, _identity_sum(design),
                    "%s: summary_total must equal the bucket sum "
                    "(single source of truth, not a parallel figure)"
                    % tpl.code)
                # unplaced_count is an OVERLAY: it must never push the
                # bucket sum past len(lines) — an is_unresolved line is
                # already inside one of the seven buckets above.
                self.assertLessEqual(
                    design.unplaced_count, len(lines),
                    "%s: unplaced_count can't exceed the active line "
                    "count" % tpl.code)

    def test_get_counter_summary_mirrors_stored_fields(self):
        tpl = self.templates[0]
        design = tpl.action_instantiate(partner_id=self.partner.id)
        summary = design._get_counter_summary()
        self.assertEqual(summary["base_count"],      design.base_count)
        self.assertEqual(summary["wall_count"],      design.wall_count)
        self.assertEqual(summary["tall_count"],      design.tall_count)
        self.assertEqual(summary["corner_count"],    design.corner_count)
        self.assertEqual(summary["filler_count"],    design.filler_count)
        self.assertEqual(summary["panel_count"],     design.panel_count)
        self.assertEqual(summary["appliance_count"], design.appliance_count)
        self.assertEqual(summary["unplaced_count"],  design.unplaced_count)
        self.assertEqual(summary["total"],           design.summary_total)
        self.assertEqual(
            summary["total"], len(design.cabinet_line_ids),
            "the dict's `total` must equal the active line count too")


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestCounterSummaryUShapeFixture(TransactionCase):
    """AC (task-7-brief.md + SBK-033 expansion): a U-shape-shaped fixture
    shows base/wall/corner/appliance ALL non-zero — the exact class of
    line V4 claim 1 found invisible to every existing count surface."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_repaired_corner(cls.env)
        cls.tpl = cls.env["southbrook.kitchen.template"].search(
            [("code", "=", "U-10X8X10")], limit=1)

    def test_u_shape_shows_base_wall_corner_appliance_all_nonzero(self):
        self.assertTrue(self.tpl, "U-10X8X10 must ship")
        design = self.tpl.action_instantiate()

        self.assertGreater(design.base_count, 0, "base_count must be > 0")
        self.assertGreater(design.wall_count, 0, "wall_count must be > 0")
        self.assertGreater(design.corner_count, 0,
                            "corner_count must be > 0 (V4 claim 1's gap)")
        self.assertGreater(design.appliance_count, 0,
                            "appliance_count must be > 0 (RANGE survives, "
                            "Claim 3 of the same audit)")
        # NOT asserting unplaced_count == 0 here: on a clean DB
        # U-10X8X10 fully resolves at defaults (test_u_shape_template.py
        # pins that), but this shared harness's DB clone carries the
        # SAME documented corner-repair data drift baseline-tests.md
        # already flags for TestCornerSkuRepair/TestUShapeTemplate
        # (ensure_repaired_corner doesn't fully heal it here) — a slot
        # can legitimately fall back to the unresolved-slot stub on
        # this specific clone. The identity below must hold either way,
        # which is the actual contract this file exists to pin.

        self.assertEqual(
            _identity_sum(design), len(design.cabinet_line_ids),
            "identity must hold for the flagship two-corner template too")
        # "header == sidebar-total": summary_total is the ONE number every
        # surface (3D header, 3D sidebar sum, this backend form) renders.
        self.assertEqual(design.summary_total, len(design.cabinet_line_ids))


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestCounterSummaryUnresolvedOverlay(TransactionCase):
    """is_unresolved GHOST lines (Task 2) are counted once under their
    own cabinet_type bucket (matching base_count/wall_count's existing,
    already-shipped behaviour) AND flagged via unplaced_count — never
    double-counted, never dropped from the identity."""

    def _line_vals(self, design, product, ctype, seq, **extra):
        tmpl = product.product_tmpl_id
        vals = {
            "design_id":     design.id,
            "sequence":      seq,
            "product_id":    product.id,
            "quantity":      1,
            "price_unit":    product.lst_price,
            "cabinet_type":  ctype,
            "width_in":      tmpl.southbrook_width_in or 24.0,
            "height_in":     tmpl.southbrook_height_in or 34.5,
            "depth_in":      tmpl.southbrook_depth_in or 24.0,
            "origin":        "configurator",
        }
        vals.update(extra)
        return vals

    def test_unresolved_line_counted_once_not_double_counted(self):
        Design = self.env["southbrook.kitchen.design"]
        Line = self.env["southbrook.kitchen.design.line"]
        design = Design.create(
            {"name": "T7 unplaced overlay", "room_width_in": 120.0})

        base = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_b24"
        ).product_variant_id
        wall = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_w24"
        ).product_variant_id
        tall = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_t24"
        ).product_variant_id
        filler = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_fp3"
        ).product_variant_id
        appliance = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_appl_range"
        ).product_variant_id
        unresolved_stub = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_unresolved_slot"
        ).product_variant_id

        Line.create(self._line_vals(design, base, "base", 10))
        Line.create(self._line_vals(design, wall, "wall", 20))
        Line.create(self._line_vals(design, tall, "tall", 30))
        Line.create(self._line_vals(design, filler, "filler", 40))
        Line.create(self._line_vals(design, appliance, "appliance", 50,
                                     price_unit=0.0))
        # A GHOST placeholder: is_unresolved=True but still carries a
        # real cabinet_type (matches kitchen_template.py action_
        # instantiate's own shape — is_unresolved marks "no real product
        # resolved", not "no real type").
        Line.create(self._line_vals(
            design, unresolved_stub, "base", 60,
            price_unit=0.0, is_unresolved=True))

        lines = design.cabinet_line_ids
        self.assertEqual(len(lines), 6)

        # base_count counts BOTH base lines (the resolved one AND the
        # unresolved GHOST) — same shape base_count/wall_count have
        # always had (test_invariant_matrix.py's oracle), unchanged here.
        self.assertEqual(design.base_count, 2)
        self.assertEqual(design.wall_count, 1)
        self.assertEqual(design.tall_count, 1)
        self.assertEqual(design.filler_count, 1)
        self.assertEqual(design.appliance_count, 1)
        self.assertEqual(design.corner_count, 0)
        self.assertEqual(design.panel_count, 0)

        # The overlay: exactly 1 of those 6 lines is unresolved.
        self.assertEqual(design.unplaced_count, 1)

        # IDENTITY: the seven-bucket sum equals the active line count —
        # unplaced_count is NOT part of this sum (it's already inside
        # base_count above), so adding it again would overcount to 7.
        self.assertEqual(_identity_sum(design), 6)
        self.assertEqual(design.summary_total, 6)

        # total_cabinets (legacy, T6) stays exactly as-is: every
        # non-filler line counts, INCLUDING the unresolved one — 5 of
        # the 6 lines are non-filler.
        self.assertEqual(design.total_cabinets, 5)
        self.assertEqual(design.filler_count, 1)

    def test_counter_summary_price_split_matches_cabinet_vs_filler(self):
        Design = self.env["southbrook.kitchen.design"]
        Line = self.env["southbrook.kitchen.design.line"]
        design = Design.create(
            {"name": "T7 price split", "room_width_in": 96.0})

        base = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_b24"
        ).product_variant_id
        filler = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_fp3"
        ).product_variant_id

        Line.create(self._line_vals(design, base, "base", 10,
                                     price_unit=180.0))
        Line.create(self._line_vals(design, base, "base", 20,
                                     price_unit=180.0))
        Line.create(self._line_vals(design, filler, "filler", 30,
                                     price_unit=35.0))

        summary = design._get_counter_summary()
        self.assertEqual(summary["price"], 360.0,
                          "price mirrors the historical /layout + client "
                          "convention: cabinet-only, fillers excluded")
        self.assertEqual(summary["cabinet_price"], 360.0)
        self.assertEqual(summary["filler_price"], 35.0)
        # estimated_price (the pre-existing, UNCHANGED stored field)
        # stays whole-design-inclusive — fillers included there, unlike
        # the new dict's price/cabinet_price split.
        self.assertEqual(design.estimated_price, 395.0)
