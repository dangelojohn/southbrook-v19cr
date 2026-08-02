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
The real cause is wkhtmltopdf's header/body box math: the company
header renders into the page's reserved top-margin band and is NOT
clipped to it, so real (multi-line) header content taller than that
band overflows downward. Empirically verified on this stack's actual
wkhtmltopdf 0.12.6.1 (varying header_spacing at fixed margin_top, then
margin_top alone): margin_top ALONE controls where body content (our
title table) starts — header_spacing has no effect on that position on
this binary. At margin_top=20 the header overflowed past that band and
printed on top of the title. signature_spec_sheet.xml renders the
IDENTICAL company header at margin_top=28 with no reported collision,
so the fix matches that proven-safe margin_top rather than guessing a
new one (see reports/kitchen_plan_report.xml's paperformat comment).
test_04/05 pin the structural + config side of that fix; test_06/07
render actual PDFs via wkhtmltopdf to check the render isn't degraded
and doesn't regress the page count (both skip cleanly if the binary
isn't available in the harness).
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
        paperformat's margin_top, not the title's own positioning — see
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

    def test_05_paperformat_header_room_and_body_clearance(self):
        """Regression pin for the actual fix — FINAL empirical model
        (Task 37, fix round 2, established by rendering LIVE prod PDFs
        at 20/16, 28/16, 45/16 and 42/34 and visually inspecting them):

        wkhtmltopdf 0.12.6.1 draws the company header starting at
        (margin_top - header_spacing) from the page top, unclipped, and
        body content at margin_top — so the header's room is exactly
        HEADER_SPACING. With header_spacing=16 the ~30mm company block
        (logo + name + email + website) overprinted the first body rows
        at EVERY margin_top tried (20, 28, 45 — the collision just moved
        down the page intact). 42/34 was verified overlap-free on both
        pages of a live render. Odoo's stock invoice default (40/35)
        obeys the same rule, which is why standard reports don't
        collide.

        Two floors, both load-bearing:
        - header_spacing >= 34: the header's actual room; regressing it
          reintroduces the overlap regardless of margin_top.
        - margin_top >= header_spacing + 6: keeps a visible gap between
          the header's bottom and the title strip (and margin_top >=
          header_spacing is structurally required or the header band
          extends above the page top)."""
        report = self.env.ref(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan")
        pf = report.paperformat_id
        self.assertTrue(pf, "action_report_kitchen_plan must carry a "
                             "dedicated paperformat")
        self.assertGreaterEqual(
            pf.header_spacing or 0, 34,
            "header_spacing IS the company header's room on this "
            "wkhtmltopdf — the ~30mm company block needs >= 34mm "
            "(empirically verified overlap-free); regressing this "
            "reintroduces the header/title overprint on every page")
        self.assertGreaterEqual(
            (pf.margin_top or 0) - (pf.header_spacing or 0), 6,
            "margin_top - header_spacing is the visible gap between the "
            "header bottom and the first body row; keep >= 6mm")

    def test_06_pdf_smoke_renders_a_real_pdf(self):
        """Bakes an actual PDF via wkhtmltopdf (not the HTML-only
        `_render_qweb_html` path the other tests here use) and asserts
        it's a real, non-degraded, reasonably-sized PDF — NOT a visual
        check that the header/title collision is gone (this harness has
        no live HTTP listener, so wkhtmltopdf's own header/footer asset
        fetch fails and the company header sub-render comes out blank —
        confirmed via a throwaway probe render; see the task report).
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

    def test_07_pdf_page_count_stays_within_bound_for_a_fifteen_line_design(
            self):
        """Review fast-follow (Task 37, fix round 1) — the paperformat's
        own pre-existing comment says its tight margins exist so "the
        schedule table doesn't get crowded off a second page for a
        typical 8-15 line design"; the Task 37 fix spends 8mm/page of
        that margin_top budget (20 -> 28) raising the header collision
        fix. This measures whether that 8mm actually pushes a realistic
        15-line design onto more pages than before.

        Measured on this exact CI recipe (ci_t37) at margin_top=28: a
        15-line design (the 11 real L-10X8 lines + 4 synthetic clones of
        its first line, same product) renders to a SINGLE page. The
        bound below (<=2) is not the measured value with no headroom at
        all — it allows exactly one extra page beyond what was measured,
        so a genuine future regression (e.g. a much taller header, or a
        much longer schedule) still fails loud instead of the test being
        a tautology that can never fail.

        Uses `/Type /Page` (not `/Type /Pages`, the one Kids-array
        node every PDF has) as a raw byte count of page objects — no
        PDF library needed, same style of check as the %PDF-magic smoke
        test above."""
        IrActionsReport = self.env["ir.actions.report"]
        state = IrActionsReport.get_wkhtmltopdf_state()
        if state != "ok":
            self.skipTest(
                "wkhtmltopdf not usable in this harness (state=%r)" % state)

        Line = self.env["southbrook.kitchen.design.line"]
        existing_lines = self.design.cabinet_line_ids
        target_total = 15
        template_line = existing_lines[0]
        for i in range(max(0, target_total - len(existing_lines))):
            Line.create({
                "design_id": self.design.id,
                "product_id": template_line.product_id.id,
                "quantity": 1,
                "price_unit": template_line.price_unit,
                "cabinet_type": "base",
                "zone": "base_run",
                "wall": "back",
                "width_in": 12.0, "height_in": 34.5, "depth_in": 24.0,
                # Parked far outside the room on purpose: the report
                # only lists/draws cabinet_line_ids, it never validates
                # placement (that's `_check_production_ready`, a
                # different, unrelated code path), so realistic
                # positions aren't needed to test page count.
                "x_position_in": 2000.0 + i * 20.0, "z_position_in": 0.0,
                "rotation_deg": 0, "run_seq": 900 + i,
                "origin": "configurator",
                "layout_key": "t37-pagecount-%d" % i,
            })
        self.design.invalidate_recordset(["cabinet_line_ids"])
        line_count = len(self.design.cabinet_line_ids)
        self.assertGreaterEqual(
            line_count, target_total,
            "fixture setup must actually reach >= 15 lines")

        report = self.env.ref(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan")
        pdf_content, content_type = report.with_context(
            force_report_rendering=True,
        )._render_qweb_pdf(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan",
            self.design.ids,
        )
        self.assertEqual(content_type, "pdf")

        page_count = len(re.findall(rb"/Type\s*/Page(?!s)", pdf_content))
        self.assertGreaterEqual(
            page_count, 1, "must find at least one page object in the PDF")
        self.assertLessEqual(
            page_count, 2,
            "a %d-line design produced %d pages — measured 1 page at "
            "margin_top=28 on this CI recipe; if this design now spills "
            "further than that, the margin_top budget spent on the "
            "header-collision fix is pushing real designs onto more "
            "pages and the fix needs a rethink (e.g. a smaller header "
            "font, or clipping/reflowing the company header itself) "
            "rather than a loosened bound here" % (line_count, page_count))
