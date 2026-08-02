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

Task 37 (2026-08-02) — header/title collision fix. Visually confirmed on
a live render: the title strip ("<design name> / Dimensioned Plan /
PLAN #NN") overlapped web.external_layout's company header (logo,
address, VAT) on every page. The title strip was never mispositioned —
it's a plain in-flow <table>, same as southbrook_estimating's
signature_spec_sheet.xml title strip under the same web.external_layout.
The real cause is wkhtmltopdf's header/body box math: the dedicated
paperformat's margin_top(20) + header_spacing(16) = 36mm of clearance
was too little for this company's real (multi-line) header content,
so the header overflowed its reserved band and printed on top of the
title table, which starts at exactly that clearance from the page top.
signature_spec_sheet.xml renders the IDENTICAL company header at
margin_top(28) + header_spacing(16) = 44mm with no reported collision,
so the fix matches that proven-safe number rather than guessing a new
one (see reports/kitchen_plan_report.xml's paperformat comment).
test_04/05 pin the structural + config side of that fix; test_06 is
the real end-to-end smoke: an actual wkhtmltopdf PDF render (skipped
cleanly if the binary isn't available in the harness).
"""
import re
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

        # Review fast-follow (T36 Important 1): also trap create() on
        # EVERY model — the row-count/write_date belt below is scoped
        # to the two design models, so a stray create on an unrelated
        # model (attachment, log record, ...) would otherwise slip
        # through the "zero side effects" claim.
        create_calls = []
        original_create = BaseModel.create

        def _tracking_create(self_, vals_list):
            create_calls.append((self_._name,))
            return original_create(self_, vals_list)

        with patch.object(BaseModel, "write", _tracking_write), \
                patch.object(BaseModel, "create", _tracking_create):
            html = self._render()

        self.assertTrue(html, "report must still render something")
        self.assertEqual(
            write_calls, [],
            "report render must not call write() on any record: %r"
            % (write_calls,))
        self.assertEqual(
            create_calls, [],
            "report render must not create records on ANY model: %r"
            % (create_calls,))

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

    # ------------------------------------------------------------------
    # Task 37 — header/title collision fix
    # ------------------------------------------------------------------

    def test_04_title_block_is_in_normal_flow_not_absolutely_positioned(self):
        """Structural pin: the title strip (design name / "Dimensioned
        Plan" / "PLAN #NN") must never be pulled out of normal document
        flow into the header band — no `position: absolute`/`fixed` and
        no negative top margin anywhere in the rendered page body. This
        was already true before the Task 37 fix (the real cause was the
        paperformat's clearance, not the title's own positioning — see
        test_05), but pinning it here means a future edit that DOES try
        to "fix" this by absolutely-positioning the title will fail
        loudly instead of silently reintroducing the collision."""
        html = self._render()

        # Scope to the report's own body markup — the standalone <style>
        # a wkhtmltopdf/browser stylesheet might carry for unrelated
        # chrome doesn't matter here; we only care about how THIS
        # report's own inline styles position its own content.
        self.assertIn('southbrook-kitchen-plan', html)
        forbidden = re.compile(
            r'position\s*:\s*(absolute|fixed)|margin-top\s*:\s*-', re.I)
        self.assertIsNone(
            forbidden.search(html),
            "title/page markup must stay in normal flow — no "
            "position:absolute/fixed or negative top margin")

    def test_05_paperformat_gives_the_company_header_clearance(self):
        """Regression pin for the actual fix: the dedicated paperformat's
        margin_top + header_spacing (the vertical clearance wkhtmltopdf
        gives the company header before body content — our title table
        — starts printing) must be at least as generous as
        southbrook_estimating's signature_spec_sheet.xml paperformat,
        which renders the SAME company header (same web.external_layout,
        no override) with no reported collision. Matching-or-exceeding
        that proven-safe number is the fix; regressing below it
        reintroduces the header/title overlap on this company's real
        (multi-line) header content."""
        report = self.env.ref(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan")
        pf = report.paperformat_id
        self.assertTrue(pf, "action_report_kitchen_plan must carry a "
                             "dedicated paperformat")
        clearance = (pf.margin_top or 0) + (pf.header_spacing or 0)

        sibling_pf = self.env.ref(
            "southbrook_estimating.paperformat_southbrook_invoice_a4")
        sibling_clearance = (
            (sibling_pf.margin_top or 0) + (sibling_pf.header_spacing or 0))

        self.assertGreaterEqual(
            clearance, sibling_clearance,
            "plan report's header clearance (%.0fmm) must be >= the "
            "proven-safe sibling report's clearance (%.0fmm) — they "
            "render the identical company header" % (
                clearance, sibling_clearance))

    def test_06_pdf_smoke_renders_a_real_pdf(self):
        """The real, end-to-end verification: bake an actual PDF via
        wkhtmltopdf (not the HTML-only `_render_qweb_html` path the
        other tests here use) and assert it's a real, non-trivial PDF.
        `--test-enable` normally makes `_render_qweb_pdf` silently fall
        back to HTML (see `_pre_render_qweb_pdf`) — `force_report_
        rendering=True` overrides that, matching what a live Print
        click actually does. Skips cleanly (not an error) if
        wkhtmltopdf isn't usable in this harness."""
        IrActionsReport = self.env["ir.actions.report"]
        state = IrActionsReport.get_wkhtmltopdf_state()
        if state != "ok":
            self.skipTest(
                "wkhtmltopdf not usable in this harness (state=%r)" % state)

        report = self.env.ref(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan")
        pdf_content, content_type = report.with_context(
            force_report_rendering=True,
        )._render_qweb_pdf(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan",
            self.design.ids,
        )
        self.assertEqual(content_type, "pdf")
        self.assertTrue(
            pdf_content.startswith(b"%PDF"),
            "must be a real PDF, not an HTML fallback")
        # >20KB (decimal) per the task brief — confirmed live on this
        # exact CI recipe (ci_t37, this docker command): an L-10X8
        # plan PDF lands around ~20.3KB even in this harness, which has
        # no live HTTP listener for wkhtmltopdf's own asset fetch (a
        # --stop-after-init test run never opens one), so the company
        # header's logo/CSS don't load and the real PDF is SMALLER here
        # than a live Print click would produce. 20000 is therefore a
        # real floor, not an inflated one.
        self.assertGreater(
            len(pdf_content), 20000,
            "plan PDF for an L-10X8 design should be over 20KB (inline "
            "SVG floor plan + cabinet schedule); a near-empty PDF "
            "suggests the render silently failed/degraded")
