# SPDX-License-Identifier: LGPL-3.0-only
"""U-shape template (U-10X8X10) — the two-corner flagship.

Unblocked by the T4a corner inventory. Geometry proven by engine
probes before authoring: back run = corner 36 + 24 + 24 + corner 36 =
exactly 120"; legs flush at 96"; the back UPPER run carries five 24"
slots so it OVERLAPS both wall-corner cells (an exact-touch run is not
detected — engine detection needs geometric overlap). Fit-side, the
end-aware corner claims absorb the leading AND trailing buffer slots
(back-left sits at the back run's leading end, back-right at its
trailing end).
"""
from odoo.tests import TransactionCase, tagged

from .test_corner_repair import ensure_repaired_corner


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestUShapeTemplate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_repaired_corner(cls.env)
        cls.tpl = cls.env["southbrook.kitchen.template"].search(
            [("code", "=", "U-10X8X10")], limit=1)

    def test_ships_active(self):
        self.assertTrue(self.tpl, "U-10X8X10 must ship")
        self.assertTrue(self.tpl.active)
        self.assertEqual(self.tpl.layout_shape, "u_shape")

    def test_end_aware_fit_accepts_the_double_corner_back(self):
        fit = self.tpl.parametric_fit()
        self.assertTrue(fit["ok"], fit["message"])
        # 8 floor cabinets authored (4 back + 2 left + 2 right,
        # appliance excluded); no repeat slots — count is exact.
        self.assertEqual(fit["count"], 8)

    def test_instantiates_with_all_four_corners(self):
        design = self.tpl.action_instantiate()
        corners = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner")
        self.assertEqual(len(corners), 4,
                         "2 base + 2 wall corners must derive")
        # NOTE: mapped() through the m2o DEDUPLICATES products — count
        # lines per code, not mapped values (both wall corners share
        # one product).
        by_code = lambda c: corners.filtered(  # noqa: E731
            lambda l: l.product_id.default_code == c)
        self.assertEqual(len(by_code("SB-CORNER")), 1)
        self.assertEqual(len(by_code("SB-CORNER-R")), 1,
                         "back-right junction must pick the RH twin")
        self.assertEqual(len(by_code("SB-WALL-CORNER")), 2)
        # The promised kitchen survives substitution.
        survivors = set(design.cabinet_line_ids.mapped(
            "template_slot_code"))
        for kept in ("SINK", "RANGE", "M1", "M2", "B3",
                     "W1", "W2", "W2B", "W3", "W4"):
            self.assertIn(kept, survivors,
                          "%s must survive the corner substitution" % kept)
        self.assertFalse(design.cabinet_line_ids.filtered("is_unresolved"))
        self.assertGreater(design.estimated_price, 0.0)
        self.assertEqual(design.room_width_in, 120.0)  # never grown
        # Task 1 (corner-remediation 2026-07-30) — the real issue-dict key
        # is "severity", not "blocking" (V2-validator.md Claim 1): the
        # original `i.get("blocking")` was always None, so this assertion
        # was a silent no-op tautology that never actually exercised
        # _check_production_ready. Customer/BOM gates are genuinely out
        # of scope here (this design carries no partner_id, and this bare
        # test DB may not seed every catalog BOM) — every OTHER blocking
        # code (collision, out-of-room, hand-mismatch, ...) must still be
        # empty on a freshly instantiated + arranged shipped template.
        blocking = [i for i in design._check_production_ready()
                    if i["severity"] == "blocking"]
        # Task 15 (SBK-027) — U-10X8X10's left leg is SINK (30") under
        # W3 (24") — a real, pre-existing wall/base run-length gap (a
        # sink commonly has no matching upper), independent of and not
        # a regression from this pin's own corner-substitution concern.
        # Surfaced deliberately by the new check; tracked in the
        # options report (task-15-report.md), not fixed here.
        # Orchestrator ruling (2026-07-31, review round 1) narrowed the
        # check to only fire when a wall run EXISTS but falls short —
        # W3 is a real (non-zero) wall cabinet that genuinely falls
        # short of SINK's 30", so this exclusion is still required.
        geometry_blocks = [i for i in blocking
                           if i["code"] not in ("MISSING_CUSTOMER",
                                                 "MISSING_BOM",
                                                 "WALL_RUN_SHORTER_THAN_BASE")]
        self.assertFalse(
            geometry_blocks,
            "a shipped template must instantiate with no geometry/hand/"
            "bounds blocking issues: %s" % geometry_blocks)

    def test_flip_keeps_both_corners_hands_swapped(self):
        design = self.tpl.action_instantiate()
        design.action_flip_layout(axis="x")
        corners = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner")
        self.assertEqual(len(corners), 4,
                         "flip must keep all four corners valid")
        codes = corners.mapped("product_id.default_code")
        self.assertIn("SB-CORNER", codes)
        self.assertIn("SB-CORNER-R", codes)

    def test_compat_u_shape_preset_now_resolves(self):
        # The legacy 'u_shape' preset was the last compat code without a
        # shipped template (honest fallback until now).
        picker = self.env["kitchen.design.template.picker"]
        code = picker._COMPAT_PRESET_CODES["u_shape"]
        self.assertEqual(code, "U-10X8X10")
        wizard = picker.create({"preset": "u_shape"})
        action = wizard.action_create()
        self.assertEqual(action.get("tag"), "southbrook_kitchen_configurator")
        design = self.env["southbrook.kitchen.design"].search(
            [], order="id desc", limit=1)
        self.assertEqual(design.room_width_in,
                         self.tpl.default_room_width_in)
        self.assertTrue(design.cabinet_line_ids)
