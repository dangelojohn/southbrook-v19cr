# SPDX-License-Identifier: LGPL-3.0-only
"""P1 planner-UX fixes (2026-07-31, scratchpad/retest/p1-planner.md) —
Task 1: the permanently-red "Late" tile on the PM Dashboard kanban.

ROOT CAUSE (see dashboard.scss / views/pm_dashboard.xml headers for the
full note): the QWeb kanban card template hardcoded the class
`o_sb_pm_dash_late` on the Late tile's <button> regardless of the
record's southbrook_pm_late_count value, so dashboard.scss's
`.o_sb_pm_dash_late .o_sb_pm_dash_value { color: red; }` rule applied
unconditionally. The comment in dashboard.scss admitted the tile was
"permanent" and blamed a (false) QWeb-kanban limitation.

The fix moves the branching into a real computed field,
mrp.workcenter.southbrook_pm_late_state ('ok' | 'late'), and the
template now interpolates that field's raw_value into the class
instead of hardcoding it. This test pins the field-level behaviour:
a zero-late day must compute 'ok', not 'late'.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_pm", "p1_planner")
class TestP1LateTile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wc = cls.env["mrp.workcenter"]
        cls.Production = cls.env["mrp.production"]
        cls.Workorder = cls.env["mrp.workorder"]
        cls.product = cls.env["product.product"].create({
            "name": "P1 Late Tile product",
            "type": "consu",
        })
        cls.bom = cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.product.product_tmpl_id.id,
            "product_qty": 1.0,
        })

    def _wc(self, code):
        return self.Wc.create({"name": "P1 Late Tile WC %s" % code, "code": code})

    def _mo_with_workorder_at(self, wc, date_deadline):
        mo = self.Production.create({
            "product_id": self.product.id,
            "product_qty": 1.0,
            "bom_id": self.bom.id,
            "date_deadline": date_deadline,
        })
        self.Workorder.create({
            "name": "P1 WO",
            "production_id": mo.id,
            "workcenter_id": wc.id,
            "product_uom_id": self.product.uom_id.id,
        })
        return mo

    def test_10_zero_late_jobs_yields_ok_state_not_late(self):
        """Pins the exact behaviour the task asked for: a design/day
        with zero late jobs must render 'ok', never 'late'."""
        wc = self._wc("P1OK")
        self.assertEqual(wc.southbrook_pm_late_count, 0)
        self.assertEqual(
            wc.southbrook_pm_late_state, "ok",
            "zero late MOs must compute the 'ok' tile state, not 'late'",
        )

    def test_20_a_late_mo_flips_state_to_late(self):
        from odoo import fields
        from datetime import timedelta

        wc = self._wc("P1LATE")
        past = fields.Datetime.now() - timedelta(days=1)
        self._mo_with_workorder_at(wc, past)
        wc.invalidate_recordset()
        self.assertGreater(wc.southbrook_pm_late_count, 0)
        self.assertEqual(
            wc.southbrook_pm_late_state, "late",
            "a real overdue MO at this workcenter must flip the tile "
            "state to 'late'",
        )

    def test_30_late_state_is_selection_with_exactly_two_values(self):
        """Guards against a future edit silently widening this field
        into something the SCSS/QWeb pair (o_sb_pm_dash_ok /
        o_sb_pm_dash_late classes) doesn't know how to colour."""
        field = self.Wc._fields["southbrook_pm_late_state"]
        self.assertEqual(field.type, "selection")
        self.assertEqual(set(dict(field.selection)), {"ok", "late"})
