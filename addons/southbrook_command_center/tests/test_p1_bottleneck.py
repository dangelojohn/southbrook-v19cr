# SPDX-License-Identifier: LGPL-3.0-only
"""P1 planner-UX fixes (2026-07-31, scratchpad/retest/p1-planner.md) —
Task 2: the "Bottleneck" panel on Central Command.

Pins two things:

  1. The controller bug fix. `_flow()` used to read
     `report.bottleneck_line_ids`, a field that has never existed on
     southbrook.mes_mps.bottleneck_report (the real field is
     `line_ids`) — so the defensive `"bottleneck_line_ids" in
     report._fields` guard always evaluated False and `lines` was
     always an empty recordset. The panel silently showed nothing
     regardless of real data. This test creates a real report + line
     and asserts the payload is non-empty.

  2. The panel is now actionable: each bottleneck line carries a real
     `workcenter_id` — the navigable record id the OWL template's
     onNavigate() click-through targets — not just a display label.
     Per the task brief, we assert the id the action targets, not its
     label text.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_command_center.controllers.main import (
    CommandCenterController,
)


@tagged("post_install", "-at_install", "southbrook_command_center", "p1_planner")
class TestP1Bottleneck(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ctrl = CommandCenterController()
        cls.Wc = cls.env["mrp.workcenter"]
        cls.Report = cls.env["southbrook.mes_mps.bottleneck_report"]
        cls.Line = cls.env["southbrook.mes_mps.bottleneck_line"]

        cls.wc_hot = cls.Wc.create({
            "name": "P1 Bottleneck WC",
            "code": "P1BWC",
        })
        cls.report = cls.Report.create({})
        cls.line_hot = cls.Line.create({
            "report_id": cls.report.id,
            "workcenter_id": cls.wc_hot.id,
            "load_pct": 132.0,
            "bottleneck_score": 500.0,
            "recommended_action": "add_shift",
        })

    def test_10_field_name_is_line_ids_not_bottleneck_line_ids(self):
        """Regression guard for the exact bug: the report model must
        NOT have a `bottleneck_line_ids` field (if someone adds one
        later without updating the controller comment, that's fine,
        but line_ids must always be the one actually populated)."""
        self.assertNotIn(
            "bottleneck_line_ids", self.report._fields,
            "southbrook.mes_mps.bottleneck_report has never had this "
            "field — if this now fails, the model changed underneath "
            "the fix and the controller's defensive check should be "
            "revisited.",
        )
        self.assertIn("line_ids", self.report._fields)

    def test_20_flow_bottleneck_lines_not_empty(self):
        """The historical bug: this list was always [] regardless of
        real report/line data. Must now surface the seeded line."""
        out = self.ctrl._flow(self.env, self.env.company.id)
        self.assertIsNotNone(out["bottleneck"], "bottleneck block must be populated")
        lines = out["bottleneck"]["lines"]
        self.assertEqual(
            len(lines), 1,
            "expected exactly the one seeded bottleneck line, got %r" % (lines,),
        )

    def test_30_line_carries_navigable_workcenter_id(self):
        """Task 2: assert the ACTION TARGET (a real mrp.workcenter id),
        not the label text — this is what onNavigate('mrp.workcenter',
        bl.workcenter_id) in central_command.xml actually opens."""
        out = self.ctrl._flow(self.env, self.env.company.id)
        line = out["bottleneck"]["lines"][0]
        self.assertIn("workcenter_id", line)
        self.assertEqual(line["workcenter_id"], self.wc_hot.id)
        # It must be a real, resolvable record — not just an int that
        # happens to match.
        self.assertTrue(self.Wc.browse(line["workcenter_id"]).exists())

    def test_40_severity_reflects_recommended_action(self):
        """add_shift (>=110% load) must escalate to 'blocked' so the
        row visually outranks a normal one, per the design-system
        planner rule (exceptions outrank normals)."""
        out = self.ctrl._flow(self.env, self.env.company.id)
        line = out["bottleneck"]["lines"][0]
        self.assertEqual(line["severity"], "blocked")

    def test_50_no_action_line_is_ok_severity(self):
        wc_calm = self.Wc.create({"name": "P1 Calm WC", "code": "P1CWC"})
        self.Line.create({
            "report_id": self.report.id,
            "workcenter_id": wc_calm.id,
            "load_pct": 40.0,
            "bottleneck_score": 10.0,
            "recommended_action": "no_action",
        })
        out = self.ctrl._flow(self.env, self.env.company.id)
        by_wc = {ln["workcenter_id"]: ln for ln in out["bottleneck"]["lines"]}
        self.assertEqual(by_wc[wc_calm.id]["severity"], "ok")

    def test_60_why_text_has_no_raw_enum_leakage(self):
        """Copy standard (southbrook-design-system skill): no raw
        enum values like 'add_shift' in user-facing text."""
        out = self.ctrl._flow(self.env, self.env.company.id)
        line = out["bottleneck"]["lines"][0]
        self.assertNotIn("add_shift", line["why"])
        self.assertIn("add a shift", line["why"])
