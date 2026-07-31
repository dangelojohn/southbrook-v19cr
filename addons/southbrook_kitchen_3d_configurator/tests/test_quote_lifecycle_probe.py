# SPDX-License-Identifier: LGPL-3.0-only
"""Task 29 (corner remediation 2026-07-30) — quote-lifecycle probe.

TESTS-AND-EVIDENCE ONLY. This file does not modify any production file
(another agent owns models/kitchen_design.py this round). Every surface
below was verified empirically against a live clone of prod (ci_qlife,
`pg_dump` of the `southbrook` database, four-module `-u` at HEAD
c73a4271) via a disposable probe file before being written up here —
the assertions encode what was actually OBSERVED, not what "should"
happen.

Five surfaces, one test class each:

  1. Double-quote idempotency — action_create_quotation() called twice
     on the same design reuses the same draft order (T27 fix).
  2. Post-quote design edit -> cron flow — add a line via the model
     API, remove another, then run cron_reconcile_designs(): the SAME
     draft SO gains/updates by sb_layout_key, no duplicates, removed
     design lines swept (reconcile.py steps 4-5).
  3. Customer-switch repricing (the locked Q7 smoke contract) — the
     demo partner is ABSENT on this DB (verified: no
     southbrook_estimating.demo_partner_tradesperson ir.model.data row
     in ci_qlife — this is a pg_dump of the live prod DB, which was
     never installed --with-demo). skipTest with that evidence for the
     literal locked-partner scenario; a companion pair of tests using
     test-only partners of the exact same channel/tier shape documents
     the SHIPPED semantics found:
       (a) the UI-equivalent path (Form, matching test_phase1_smoke.py's
           test_step_08 mechanism) correctly re-resolves pricelist_id
           via _onchange_partner_id_southbrook_pricelist, but corner-
           line price_unit/amount_total do NOT change — confirming the
           "design-time retail snapshot, mirror bypasses pricelist_id"
           architecture progress.md documents as reviewer-adjudicated
           (T5 fix round). PASS — this is the shipped, sanctioned
           behavior for corner lines specifically.
       (b) DEFECT: a plain ORM `order.write({"partner_id": ...})` (no
           Form/onchange involved — e.g. any backend script, RPC call,
           or automation that isn't going through the web client's
           onchange dispatch) leaves `pricelist_id` **False** instead
           of resolving to any pricelist at all. Root cause: stock
           Odoo's `sale.order._compute_pricelist_id` is a REAL
           `@api.depends('partner_id', 'company_id')` compute (fires
           on any write to those fields, verified against
           /usr/lib/python3/dist-packages/odoo/addons/sale/models/
           sale_order.py inside the sami-odoo:dev image) that sets
           `pricelist_id = partner.property_product_pricelist`.
           Southbrook's channel resolver
           (`_onchange_partner_id_southbrook_pricelist`) is declared
           ONLY as `@api.onchange` — it never runs outside Form/RPC
           onchange dispatch, so it never gets a chance to override
           stock's compute on a plain write(). Written as a FAILING
           assertion (not skipTest) — this is a real, reproducible gap
           for any non-UI partner-switch path.
  4. Spec-sheet report render — southbrook_estimating's
     action_report_signature_spec_sheet (bound to sale.order; the
     Signature Series customer-facing PDF/HTML) renders a corner-
     bearing (4 corner + $0 appliance) draft SO with no exception,
     non-empty output, all four corner display names present.
  5. Quote -> design archive interplay — archiving the design after
     quoting (`active=False`) does not crash the cron and does not
     duplicate SO lines. Documented mechanism: the cron's own
     `Design.search([("write_date", ">", last_run_iso)])` uses Odoo's
     default active-record filtering, so an archived design is simply
     excluded from the batch — the cron never touches it again. A
     direct `_reconcile_one()` call bypassing that filter (simulating
     a hypothetical non-cron caller) also does not crash and does not
     duplicate SO lines (existing lines still resolve via
     sale_order_line_id / sb_layout_key).

Fixture recipe (corner_tmpl/appliance_tmpl/_corner_bearing_design)
mirrors the sibling test_quote_reconcile_dedup.py's proven-working
minimal setup exactly, so this file never depends on the live SB-CORNER
catalog data.
"""
from odoo.tests import Form, TransactionCase, tagged


class _QuoteLifecycleFixtureMixin:
    """Shared fixture builders — corner-bearing design recipe borrowed
    from test_quote_reconcile_dedup.py's _corner_bearing_design (same
    module, Task 26), so all five surfaces exercise an identical
    "4 corner + $0 appliance" shape.
    """

    def _make_cabinet_tmpl(self, name, cabinet_type, price):
        tmpl = self.env["product.template"].create({
            "name": name,
            "type": "consu",
            "sale_ok": True,
            "list_price": price,
        })
        tmpl.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": cabinet_type,
            "southbrook_width_in":     36.0,
            "southbrook_height_in":    34.5,
            "southbrook_depth_in":     24.0,
        })
        return tmpl

    def _corner_bearing_design(self, partner, tag):
        Design = self.env["southbrook.kitchen.design"]
        Line = self.env["southbrook.kitchen.design.line"]

        corner_tmpl = self._make_cabinet_tmpl(
            "T29 Corner Cabinet %s" % tag, "corner", 625.0)
        corner_variant = corner_tmpl.product_variant_id
        appliance_tmpl = self._make_cabinet_tmpl(
            "T29 Appliance Space %s" % tag, "appliance", 0.0)
        appliance_variant = appliance_tmpl.product_variant_id

        design = Design.create({
            "name":            "T29 Probe Design %s" % tag,
            "partner_id":      partner.id,
            "room_width_in":   460.0,
            "room_depth_in":   100.0,
            "room_height_in":  96.0,
            "state":           "draft",
        })
        corner_prices = [600.0, 625.0, 650.0, 675.0]
        for i, price in enumerate(corner_prices):
            Line.create({
                "design_id":     design.id,
                "product_id":    corner_variant.id,
                "quantity":      1,
                "price_unit":    price,
                "cabinet_type":  "corner",
                "origin":        "configurator",
                "width_in":      36.0,
                "height_in":     34.5,
                "depth_in":      24.0,
                "x_position_in": i * 90.0,
                "z_position_in": 0.0,
                "layout_key":    "t29-%s-corner-%d" % (tag, i),
            })
        Line.create({
            "design_id":      design.id,
            "product_id":     appliance_variant.id,
            "quantity":       1,
            "price_unit":     0.0,
            "cabinet_type":   "appliance",
            "appliance_type": "range",
            "origin":         "configurator",
            "width_in":       30.0,
            "height_in":      34.5,
            "depth_in":       24.0,
            "x_position_in":  4 * 90.0,
            "z_position_in":  0.0,
            "layout_key":     "t29-%s-appliance-0" % tag,
        })
        design.invalidate_recordset()
        return design


# ═══════════════════════════════════════════════════════════════════
# Surface 1 — double-quote idempotency
# ═══════════════════════════════════════════════════════════════════
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "quote_lifecycle_probe")
class TestSurface1DoubleQuoteIdempotency(_QuoteLifecycleFixtureMixin,
                                          TransactionCase):
    """action_create_quotation() called twice in a row on the same
    design must reuse the SAME draft order (T27's existing-draft-reuse
    branch), never spawn a second one, and leave the SO line count
    unchanged. Verified empirically via probe: order1.id == order2.id,
    n1 == n2 == 5, exactly 1 sale.order with origin=design.name both
    before and after the second call. PASS.
    """

    def test_double_quote_reuses_same_draft_order(self):
        partner = self.env["res.partner"].create({
            "name": "T29 Surface1 Customer",
        })
        design = self._corner_bearing_design(partner, "s1")
        Order = self.env["sale.order"]

        design.action_create_quotation()
        order1 = design.sale_order_id
        self.assertTrue(order1)
        self.assertEqual(order1.state, "draft")
        n1 = len(order1.order_line)
        self.assertEqual(n1, 5, "4 corner + 1 appliance line expected")
        count1 = Order.search_count([("origin", "=", design.name)])
        self.assertEqual(
            count1, 1, "sanity — exactly one order after the first quote")
        line_ids_1 = design.cabinet_line_ids.mapped("sale_order_line_id.id")

        # Second call, same design, nothing changed in between.
        design.action_create_quotation()
        order2 = design.sale_order_id

        self.assertEqual(
            order1.id, order2.id,
            "second action_create_quotation() call must reuse the SAME "
            "draft order (T27 fix), not spawn a second one",
        )
        order2.invalidate_recordset()
        n2 = len(order2.order_line)
        self.assertEqual(
            n2, n1,
            "SO line count must be stable across the second call — no "
            "duplicate lines created",
        )
        count2 = Order.search_count([("origin", "=", design.name)])
        self.assertEqual(
            count2, 1,
            "still exactly ONE sale.order with this origin — no second, "
            "orphaned order",
        )
        design.invalidate_recordset()
        line_ids_2 = design.cabinet_line_ids.mapped("sale_order_line_id.id")
        self.assertEqual(
            sorted(line_ids_1), sorted(line_ids_2),
            "the design lines must still point at the SAME sale.order.line "
            "records — the mirror updated in place, it did not recreate",
        )


# ═══════════════════════════════════════════════════════════════════
# Surface 2 — post-quote design edit -> reconcile cron
# ═══════════════════════════════════════════════════════════════════
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "quote_lifecycle_probe")
class TestSurface2PostQuoteEditReconcile(_QuoteLifecycleFixtureMixin,
                                          TransactionCase):
    """Quote a corner-bearing design, then edit it directly via the
    model API (add one cabinet line, unlink one existing corner line),
    then run the cron entrypoint. Verified empirically via probe: the
    SAME draft SO gains the new line (matched/created by sb_layout_key),
    the swept line's mirrored SO line is gone, the 3 untouched
    configurator lines are unchanged, and a second cron tick is a
    no-op (idempotent). PASS.
    """

    def test_add_and_remove_line_then_reconcile_updates_in_place(self):
        partner = self.env["res.partner"].create({
            "name": "T29 Surface2 Customer",
        })
        design = self._corner_bearing_design(partner, "s2")
        design.action_create_quotation()
        order = design.sale_order_id
        self.assertEqual(len(order.order_line), 5)

        # Capture the 4 untouched lines' mirrored SO-line ids up front —
        # these must survive the cron tick unchanged (no recreation).
        untouched_lines = design.cabinet_line_ids.filtered(
            lambda l: l.layout_key != "t29-s2-corner-0")
        self.assertEqual(len(untouched_lines), 4)
        untouched_sol_ids_before = sorted(
            untouched_lines.mapped("sale_order_line_id.id"))

        # 1) Add a cabinet line via the model API (simulating a
        #    post-quote design edit).
        base_tmpl = self._make_cabinet_tmpl("T29 New Base s2", "base", 400.0)
        new_line = self.env["southbrook.kitchen.design.line"].create({
            "design_id":     design.id,
            "product_id":    base_tmpl.product_variant_id.id,
            "quantity":      1,
            "price_unit":    400.0,
            "cabinet_type":  "base",
            "origin":        "configurator",
            "width_in":      24.0,
            "height_in":     34.5,
            "depth_in":      24.0,
            "x_position_in": 500.0,
            "z_position_in": 0.0,
            "layout_key":    "t29-s2-new-base",
        })

        # 2) Remove one existing corner design line (unlink — a real
        #    removal, not an archive).
        removed_line = design.cabinet_line_ids.filtered(
            lambda l: l.layout_key == "t29-s2-corner-0")
        self.assertTrue(removed_line)
        removed_sol_id = removed_line.sale_order_line_id.id
        self.assertTrue(removed_sol_id, "sanity — the line was mirrored")
        removed_line.unlink()
        design.invalidate_recordset()
        self.assertEqual(
            len(design.cabinet_line_ids), 5,
            "3 remaining corner + 1 appliance + 1 new base",
        )

        # 3) Run the cron entrypoint.
        Reconcile = self.env["southbrook.design.reconcile"]
        Reconcile.cron_reconcile_designs()
        order.invalidate_recordset()

        self.assertEqual(
            len(order.order_line), 5,
            "SO line count must match the design's 5 current active "
            "configurator lines — the new one gained, the removed one "
            "swept, no duplicates of the untouched 4",
        )
        new_line.invalidate_recordset()
        self.assertTrue(
            new_line.sale_order_line_id,
            "the newly-added design line must be mirrored onto a SO line",
        )
        self.assertEqual(new_line.sale_order_line_id.order_id.id, order.id)
        self.assertEqual(new_line.sale_order_line_id.sb_layout_key,
                          "t29-s2-new-base")
        self.assertEqual(new_line.sale_order_line_id.sb_layout_origin,
                          "configurator")

        self.assertEqual(
            self.env["sale.order.line"].search_count(
                [("id", "=", removed_sol_id)]),
            0,
            "the removed design line's mirrored SO line must be swept "
            "(reconcile.py step 5 stale-line unlink)",
        )

        untouched_sol_ids_after = sorted(
            untouched_lines.mapped("sale_order_line_id.id"))
        self.assertEqual(
            untouched_sol_ids_before, untouched_sol_ids_after,
            "the 4 untouched configurator lines must resolve to the SAME "
            "SO lines they already had — updated in place by "
            "sale_order_line_id, never recreated",
        )

        # 4) Idempotency — a second tick must not change anything.
        design.invalidate_recordset()
        Reconcile.cron_reconcile_designs()
        order.invalidate_recordset()
        self.assertEqual(
            len(order.order_line), 5,
            "a second cron tick must be a no-op — still 5 lines",
        )


# ═══════════════════════════════════════════════════════════════════
# Surface 3 — customer-switch repricing (locked Q7 smoke contract)
# ═══════════════════════════════════════════════════════════════════
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "quote_lifecycle_probe")
class TestSurface3CustomerSwitchRepricing(_QuoteLifecycleFixtureMixin,
                                           TransactionCase):

    def test_locked_demo_partner_scenario_skipped_demo_data_absent(self):
        """CLAUDE.md's locked Q7 acceptance criterion names a specific
        seeded partner: southbrook_estimating.demo_partner_tradesperson
        (channel=tradesperson, tradesperson_tier=3, "Demo Tradesperson
        (Tier 3)"). ci_qlife is a pg_dump clone of the LIVE prod
        `southbrook` database — verified by direct SQL query against
        this exact DB before writing this test:

            select module,name from ir_model_data
            where name in ('demo_partner_tradesperson',
                            'demo_partner_walkin_retail')
              and module='southbrook_estimating';
            -- returns 0 rows on ci_qlife

        Prod was never installed --with-demo, so neither
        demo_partner_tradesperson nor demo_partner_walkin_retail (both
        declared noupdate="1" in demo/southbrook_demo_partners.xml)
        exist here. skipTest with that evidence rather than fabricating
        the partner under the demo xml_id (which would test something
        that isn't actually true of this DB) — the companion tests
        below use test-only partners of the identical channel/tier
        shape to still produce real evidence about the shipped
        repricing semantics.
        """
        demo_partner = self.env.ref(
            "southbrook_estimating.demo_partner_tradesperson",
            raise_if_not_found=False,
        )
        demo_walkin = self.env.ref(
            "southbrook_estimating.demo_partner_walkin_retail",
            raise_if_not_found=False,
        )
        if not (demo_partner and demo_walkin):
            self.skipTest(
                "SKIP + evidence: southbrook_estimating.demo_partner_"
                "tradesperson / demo_partner_walkin_retail are absent "
                "from this DB (ci_qlife = pg_dump of live prod "
                "`southbrook`, installed without --demo). Verified via "
                "ir_model_data query returning 0 rows for both xml_ids "
                "before this test ran. The locked Q7 scenario cannot be "
                "exercised against the literal seeded partner on this "
                "clone; see the companion tests in this class for "
                "equivalent-shape evidence using test-only partners."
            )
        # Would exercise the literal locked scenario here if the demo
        # partners were present — not reached on this DB.

    def test_ui_equivalent_switch_resolves_pricelist_but_pins_corner_price(self):
        """Companion evidence (demo data absent, see test above) using
        a test-only tradesperson-tier-3 partner + Form (the UI-
        equivalent onchange dispatch mechanism test_phase1_smoke.py's
        locked test_step_08 also uses via `with Form(order) as f:
        f.partner_id = ...`).

        Verified empirically via probe (before this file was written):
        switching order.partner_id via Form correctly re-resolves
        pricelist_id (Retail (List Price) -> Contractor Tier 3 (-35%))
        through _onchange_partner_id_southbrook_pricelist. But the
        corner line's price_unit and the order's amount_total are
        UNCHANGED — they stay pinned at the design-time snapshot value
        `_sb_quote_line_vals` wrote at quote time. This matches
        progress.md's T5 fix-round note verbatim: "Channel-pricing risk
        ADJUDICATED fine by reviewer (design-time retail snapshot +
        SO-level pricelist is the shipped architecture)... mirror
        bypasses pricelist_id (pre-existing systemic)." PASS — this
        documents the sanctioned, shipped behavior; it is NOT the
        "every line re-prices" claim in CLAUDE.md's Q7 acceptance text
        taken literally for CORNER lines specifically — that claim was
        proven true only for a plain (non-corner) product line in
        test_phase1_smoke.py's own fixture, which never sets an
        explicit price_unit at line-creation time and so computes cold
        from whichever pricelist is active at that moment.
        """
        retail = self.env["res.partner"].create({
            "name": "T29 S3 Walk-in", "channel": "retail",
        })
        trades = self.env["res.partner"].create({
            "name": "T29 S3 Tradesperson T3",
            "channel": "tradesperson",
            "tradesperson_tier": "3",
        })
        design = self._corner_bearing_design(retail, "s3-form")
        design.action_create_quotation()
        order = design.sale_order_id
        corner_line = order.order_line.filtered(
            lambda l: l.price_unit == 600.0)
        self.assertEqual(len(corner_line), 1)

        pricelist_before = order.pricelist_id
        price_unit_before = corner_line.price_unit
        total_before = order.amount_total

        with Form(order) as f:
            f.partner_id = trades
        order = f.save()
        order.invalidate_recordset()
        corner_line.invalidate_recordset()

        self.assertNotEqual(
            order.pricelist_id.id, pricelist_before.id,
            "the header pricelist_id MUST re-resolve on a Form-driven "
            "partner switch (_onchange_partner_id_southbrook_pricelist)",
        )
        self.assertEqual(
            order.pricelist_id,
            self.env.ref("southbrook_estimating.pricelist_tradesperson_tier_3"),
            "must resolve specifically to the Tier-3 (-35%) sub-pricelist",
        )
        self.assertEqual(
            corner_line.price_unit, price_unit_before,
            "SHIPPED SEMANTICS: the corner line's price_unit stays pinned "
            "to its design-time snapshot across a partner/pricelist "
            "switch — it is never re-derived from pricelist_id",
        )
        self.assertEqual(
            order.amount_total, total_before,
            "consequently the order total does not move either, despite "
            "the header pricelist changing — 'SO-level pricelist "
            "discount' is not automatic for corner-mirrored lines",
        )

    def test_plain_orm_write_partner_switch_resolves_pricelist(self):
        """FIXED (Task 31, was DEFECT per Task 29's probe finding). A
        plain ORM `order.write({"partner_id": ...})` — i.e. any backend
        script, server action, or RPC call that sets partner_id WITHOUT
        going through the web client's onchange dispatch — used to
        actively BLANK pricelist_id to False instead of resolving it.

        Root cause (verified against the sami-odoo:dev image's vendored
        Odoo source, /usr/lib/python3/dist-packages/odoo/addons/sale/
        models/sale_order.py): stock's `_compute_pricelist_id` is a
        real `@api.depends('partner_id', 'company_id')` compute — it
        fires on every write to partner_id, unlike Southbrook's
        `_onchange_partner_id_southbrook_pricelist`, which is declared
        `@api.onchange` only and therefore NEVER runs outside a Form/
        onchange RPC. Stock's compute sets
        `pricelist_id = partner.property_product_pricelist`, which is
        empty for a partner with no configured pricelist — so the
        write used to silently leave the order with NO pricelist at
        all.

        Task 31 fix: southbrook_estimating/models/sale_order.py now
        overrides `_compute_pricelist_id` itself (not just the
        onchange) — it runs stock's compute first, then overwrites
        with the channel-resolved pricelist for any partner that
        carries a Southbrook channel, so channel resolution is
        authoritative on the WRITE path too. The assertion below is
        UNCHANGED from Task 29's probe — it always encoded the correct
        expected behavior (pricelist_id stays resolved across a
        partner switch, whatever the mechanism); it now passes against
        the fixed code instead of documenting a gap.
        """
        trades = self.env["res.partner"].create({
            "name": "T29 S3 Tradesperson Plain-write",
            "channel": "tradesperson",
            "tradesperson_tier": "3",
        })
        retail = self.env["res.partner"].create({
            "name": "T29 S3 Walk-in Plain-write", "channel": "retail",
        })
        design = self._corner_bearing_design(retail, "s3-write")
        design.action_create_quotation()
        order = design.sale_order_id
        self.assertTrue(
            order.pricelist_id, "sanity — order has a pricelist before switch")

        order.write({"partner_id": trades.id})
        order.invalidate_recordset()

        self.assertTrue(
            order.pricelist_id,
            "DEFECT (Task 29 probe) — a plain ORM write() of "
            "sale.order.partner_id leaves pricelist_id BLANK (observed: "
            "False) instead of resolving to any pricelist. Southbrook's "
            "channel resolver only runs via @api.onchange (Form/UI "
            "dispatch); it never intercepts stock's own "
            "_compute_pricelist_id, which DOES fire on a plain write "
            "and sets pricelist_id from partner.property_product_"
            "pricelist (empty here). Any non-UI partner-switch path — "
            "scripts, server actions, RPC integrations — silently "
            "breaks pricelist resolution on the order.",
        )


# ═══════════════════════════════════════════════════════════════════
# Surface 4 — spec-sheet report rendering
# ═══════════════════════════════════════════════════════════════════
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "quote_lifecycle_probe")
class TestSurface4SpecSheetReportRendering(_QuoteLifecycleFixtureMixin,
                                            TransactionCase):
    """southbrook_estimating.action_report_signature_spec_sheet (bound
    to sale.order, the Signature Series customer-facing spec sheet —
    see southbrook_estimating/reports/signature_spec_sheet.xml) must
    render a corner-bearing (4 corner + $0 appliance) draft SO without
    raising, with non-empty output, and with every corner cabinet's
    display name present in the rendered HTML. Verified empirically
    via probe: html_len=11515, corner_names_present=[True, True, True,
    True]. PASS.
    """

    def test_signature_spec_sheet_renders_corner_bearing_draft_so(self):
        partner = self.env["res.partner"].create({
            "name": "T29 Surface4 Customer",
        })
        design = self._corner_bearing_design(partner, "s4")
        design.action_create_quotation()
        order = design.sale_order_id

        report_ref = "southbrook_estimating.action_report_signature_spec_sheet"
        content, content_type = self.env["ir.actions.report"]._render_qweb_html(
            report_ref, [order.id]
        )
        html = content.decode() if isinstance(content, bytes) else content

        self.assertTrue(html, "rendered report output must be non-empty")
        self.assertIn("Signature Spec Sheet", html)
        self.assertIn(partner.name, html)

        corner_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(
            len(corner_lines), 4,
            "sanity — the fixture design has 4 corner lines",
        )
        # This fixture's 4 corner lines share ONE product (same recipe as
        # the sibling test_quote_reconcile_dedup.py — a single synthetic
        # corner template, not 4 distinct catalog SKUs), so
        # `.mapped("product_id.display_name")` naturally collapses to 1
        # name (Many2one mapped() coalesces to a recordset/set). Assert
        # per-line directly instead, and separately confirm the report
        # actually rendered 4 distinct cabinet-line rows (Qty column
        # 4x "1") for the 4 corner lines + 1 appliance = 5 total.
        corner_product_name = corner_lines[0].product_id.display_name
        self.assertTrue(corner_product_name)
        for line in corner_lines:
            self.assertEqual(line.product_id.display_name, corner_product_name)
        self.assertIn(
            corner_product_name, html,
            "the corner cabinet's display name must appear in the "
            "rendered spec sheet",
        )
        self.assertEqual(
            len(order.order_line), 5,
            "sanity — 4 corner + 1 appliance SO lines mirror the design",
        )
        self.assertEqual(
            html.count(corner_product_name), 4,
            "the corner cabinet's name must appear once per corner SO "
            "line (4 occurrences) in the rendered cabinets table",
        )


# ═══════════════════════════════════════════════════════════════════
# Surface 5 — quote -> design archive interplay
# ═══════════════════════════════════════════════════════════════════
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "quote_lifecycle_probe")
class TestSurface5QuoteThenArchiveInterplay(_QuoteLifecycleFixtureMixin,
                                             TransactionCase):
    """Archive the design (active=False) after quoting, then run the
    cron. Verified empirically via probe: no crash, no duplication.
    Documented mechanism: `cron_reconcile_designs`'s own
    `Design.search([("write_date", ">", last_run_iso)])` uses Odoo's
    default active-record filtering (no `active_test=False` in
    context), so an archived design is simply excluded from the batch
    it processes — graceful-by-omission, not a special case in the
    code. A direct `_reconcile_one()` call that bypasses that filter
    (simulating a hypothetical non-cron caller reaching an archived
    design) also does not crash and does not duplicate, because the
    existing SO lines still resolve via sale_order_line_id / sb_layout_
    key. PASS.
    """

    def test_archive_after_quote_cron_tick_is_graceful(self):
        partner = self.env["res.partner"].create({
            "name": "T29 Surface5 Customer",
        })
        design = self._corner_bearing_design(partner, "s5")
        design.action_create_quotation()
        order = design.sale_order_id
        n_before = len(order.order_line)
        self.assertEqual(n_before, 5)

        design.write({"active": False})
        design.invalidate_recordset()
        self.assertFalse(design.active)

        Reconcile = self.env["southbrook.design.reconcile"]
        # Must not raise.
        stats = Reconcile.cron_reconcile_designs()

        order.invalidate_recordset()
        self.assertEqual(
            len(order.order_line), n_before,
            "the cron tick must not duplicate SO lines for an archived "
            "design",
        )
        self.assertEqual(
            stats.get("created_lines", 0), 0,
            "an archived design must not surface as newly-mirrored — it "
            "is excluded from the cron's search domain entirely by "
            "Odoo's default active-record filtering",
        )

        # A direct _reconcile_one() call bypassing the cron's search
        # filter (active_test=False) must also be safe and idempotent.
        design_bypass = design.with_context(active_test=False)
        stats2 = {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                  "created_lines": 0, "divergence": 0, "errors": 0}
        Reconcile._reconcile_one(design_bypass, stats2)  # must not raise

        order.invalidate_recordset()
        self.assertEqual(
            len(order.order_line), n_before,
            "even a direct reconcile call on the archived design must "
            "not duplicate SO lines — existing lines still resolve via "
            "sale_order_line_id",
        )
        self.assertEqual(
            stats2["created_lines"], 0,
            "no new SO lines should be created for an already-quoted, "
            "now-archived design",
        )
