# SPDX-License-Identifier: LGPL-3.0-only
"""Task 36 (SBK-039) — dimensioned plan PDF, first SBK-039 capability.

Render tests (mirrors the pattern in southbrook_estimating/tests/
test_qweb_reports.py — render to HTML, not PDF, so no wkhtmltopdf
binary is needed) plus the hard "zero side effects" AC: printing a
plan must never create or write any record.

L-10X8 is the shipped flagship template (data/kitchen_templates.xml) —
the brief's own example fixture ("an L-10X8-instantiated throwaway
design"). Its CORNER slot needs a real SB-CORNER variant to resolve
against; `ensure_repaired_corner` (shared with test_corner_repair.py /
test_corner_price_resolution.py) brings a bare test DB to that state,
exactly like TestCornerPricingConvergence does.
"""
from unittest.mock import patch

from markupsafe import escape

from odoo.models import BaseModel
from odoo.tests import TransactionCase, tagged

from .test_corner_repair import ensure_repaired_corner


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "plan_report_t36")
class TestKitchenPlanReport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_repaired_corner(cls.env)
        cls.partner = cls.env["res.partner"].create(
            {"name": "T36 Plan PDF Test Customer"})
        tpl = cls.env["southbrook.kitchen.template"].search(
            [("code", "=", "L-10X8")], limit=1)
        assert tpl, "L-10X8 flagship must ship active"
        cls.design = tpl.action_instantiate(partner_id=cls.partner.id)
        assert cls.design.cabinet_line_ids, (
            "L-10X8 must instantiate at least one cabinet line "
            "for this test fixture to be meaningful")

    def _render(self):
        content, _content_type = self.env["ir.actions.report"]._render_qweb_html(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan",
            self.design.ids,
        )
        return content.decode() if isinstance(content, bytes) else content

    # ------------------------------------------------------------------
    # Action wiring
    # ------------------------------------------------------------------

    def test_01_report_action_binds_kitchen_design(self):
        r = self.env.ref(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan")
        self.assertEqual(r.model, "southbrook.kitchen.design")
        self.assertEqual(r.report_type, "qweb-pdf")
        self.assertEqual(r.binding_type, "report")

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    def test_02_renders_header_plan_and_schedule(self):
        html = self._render()

        # Header: design name. t-out HTML-escapes the value (the L-10X8
        # fixture name carries an apostrophe -> "&#39;"), so compare
        # against the same escaped form QWeb itself produces rather
        # than the raw string.
        self.assertIn(str(escape(self.design.name)), html)

        # >= 1 dimension value matching the room width (the overall
        # width dimension line label AND the header's "Room:" line both
        # use this same %g-formatted value).
        width_needle = "%g" % (self.design.room_width_in or 0.0)
        self.assertIn(width_needle, html)

        # The floor plan itself: a self-contained inline SVG.
        self.assertIn("<svg", html)

        # Every line's ref (product_id.default_code — the LINE's own
        # resolved variant, never a template-level default_code)
        # appears in the schedule.
        refs = [
            line.product_id.default_code
            for line in self.design.cabinet_line_ids
            if line.product_id.default_code
        ]
        self.assertGreaterEqual(
            len(refs), 1,
            "fixture must carry at least one line with a real ref for "
            "this assertion to be meaningful")
        for ref in refs:
            self.assertIn(ref, html, "line ref %r missing from schedule" % ref)

        # The stored total — design.estimated_price, NOT a value
        # re-derived from pricelist logic in the report.
        total_needle = "%.2f" % (self.design.estimated_price or 0.0)
        self.assertIn(total_needle, html)

    # ------------------------------------------------------------------
    # Zero side effects (hard AC)
    # ------------------------------------------------------------------

    def test_03_render_has_zero_side_effects(self):
        Design = self.env["southbrook.kitchen.design"]
        Line = self.env["southbrook.kitchen.design.line"]

        # Make sure every stored/computed field is flushed to the DB
        # BEFORE the measurement window, so the render can't be
        # "credited" with a write that actually happened earlier
        # (e.g. a pending recompute from setUpClass's instantiate).
        self.env.flush_all()

        design_count_before = Design.search_count([])
        line_count_before = Line.search_count([])
        design_write_date_before = self.design.write_date
        line_write_dates_before = {
            l.id: l.write_date for l in self.design.cabinet_line_ids}

        write_calls = []
        original_write = BaseModel.write

        def _tracking_write(self_, vals):
            write_calls.append((self_._name, tuple(self_.ids), dict(vals)))
            return original_write(self_, vals)

        with patch.object(BaseModel, "write", _tracking_write):
            html = self._render()

        self.assertTrue(html, "report must still render something")
        self.assertEqual(
            write_calls, [],
            "report render must not call write() on any record: %r"
            % (write_calls,))

        # Belt-and-braces: even a write that bypassed the public
        # write() method (e.g. a stored-compute flush) would still
        # show up as a changed write_date or a changed row count once
        # the cache is dropped and re-read from the DB.
        self.env.invalidate_all()
        self.assertEqual(Design.search_count([]), design_count_before,
                          "no southbrook.kitchen.design record may be created")
        self.assertEqual(Line.search_count([]), line_count_before,
                          "no southbrook.kitchen.design.line record may be created")
        self.assertEqual(self.design.write_date, design_write_date_before,
                          "design write_date must not change")
        for line in self.design.cabinet_line_ids:
            self.assertEqual(
                line.write_date, line_write_dates_before.get(line.id),
                "line %d write_date must not change" % line.id)
