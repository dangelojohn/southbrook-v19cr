# SPDX-License-Identifier: LGPL-3.0-only
"""Task 26 (corner remediation 2026-07-30) — quote-then-reconcile
duplication-risk fix.

Finding (`.superpowers/sdd/corner-remediation-2026-07-30/opportunity-
trace.md` §2): `action_create_quotation` (kitchen_design.py, mirror
loop) creates `sale.order.line` records from `cabinet_line_ids` but
never set `sb_layout_key`/`sb_layout_origin` on the new SO lines nor
wrote `sale_order_line_id` back onto the design lines. The reconcile
cron (`southbrook.design.reconcile._reconcile_one`, models/
reconcile.py) fires on any write_date advance — including the very
`design.sale_order_id = order.id` write `action_create_quotation`
itself makes — and matches design lines to SO lines by
`dline.sale_order_line_id` first, falling back to a search on
`(order_id, sb_layout_origin='configurator', sb_layout_key=...)`.
With neither set, that search always missed, so the very next
reconcile tick after a design was quoted directly (never touched by
the cron before) `create()`d a full second set of SO lines —
proven by a repro against a real U-shape corner design pre-fix: 14
SO lines after quoting -> 28 after one reconcile tick.

Fix: the mirror loop now stamps `sb_layout_key` (design line's own
`layout_key`, falling back to the exact same `design-{id}-line-{id}`
formula reconcile.py itself uses) and `sb_layout_origin=
'configurator'` on every SO line created from a configurator-origin
design line, and writes `sale_order_line_id` back onto the design
line right after `SaleOrder.create()` — matching reconcile's own key
convention exactly so the very next tick recognizes these lines as
already mirrored and does not duplicate them.

This file:
  1. Proves the direct-quote path itself is shaped correctly (draft
     SO, corner + appliance line counts and pricing, BOM autoseed,
     `design.state == 'quoted'`) — the "D-shaped" assertions from
     opportunity-trace.md §2's assertion list.
  2. Proves the actual regression: quoting a corner-bearing design and
     then running a reconcile tick (both the raw `_reconcile_one` and
     the cron entrypoint `cron_reconcile_designs`) creates NO new SO
     lines and leaves every design line's `sale_order_line_id` +
     every SO line's `sb_layout_key`/`sb_layout_origin` intact.

Task 27 (same finding, wider scope — flagged by the probe agent's live
validation of Task 26's fix): `action_create_quotation` unconditionally
called `SaleOrder.create()` even when `design.sale_order_id` was
already set to a DRAFT order — which happens routinely, because
`southbrook.design.reconcile._reconcile_one` (triggered synchronously
by `action_auto_arrange(sync=True)`) lazily creates a draft sale.order
the moment a partnered design has none yet (reconcile.py step 1). A
user who auto-arranges (assigning the order) and THEN clicks "Create
Quotation" got a SECOND, orphaned draft SO — `design.sale_order_id`
flips to the new one and the reconcile-made order is never referenced
or cleaned up again. Reproduced: 2 stray draft SOs across two runs.

Fix: `action_create_quotation` now reuses an existing DRAFT
`sale_order_id` in place — same linkage-keyed mirror as the fresh-
create path (`_sb_quote_line_vals`/`_sb_quote_line_key`, factored out
for exactly this reuse). A non-draft linked order still falls through
to the always-create path (editing a confirmed SO from a "Create
Quotation" click would be wrong). `TestQuoteReuseExistingReconcileOrder`
below proves the reuse: zero orphan sale.orders, same order id,
after auto_arrange(sync=True) creates the order and
action_create_quotation is then called.

Uses fresh, test-only product templates (not the live corner catalog)
so this test never depends on the SB-CORNER/SB-WALL-CORNER data-repair
fixtures — style follows the sibling `test_m1_m2_reconcile_fixes.py`,
`test_bom_gate_coherence.py`, and `test_auto_arrange_service.py` (whose
`_make_design` fixture recipe `TestQuoteReuseExistingReconcileOrder`
below borrows, since it's the proven-working minimal setup for
`action_auto_arrange`).
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "quote_reconcile_dedup")
class TestQuoteReconcileDedup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.Template = cls.env["product.template"]
        cls.Partner = cls.env["res.partner"]
        cls.Bom = cls.env["mrp.bom"]
        cls.Reconcile = cls.env["southbrook.design.reconcile"]

        cls.partner = cls.Partner.create({
            "name": "T26 Dedup Test Customer",
            "email": "t26.customer@southbrook.test",
        })

        cls.corner_tmpl = cls._make_cabinet_tmpl(
            "T26 Corner Cabinet", "corner", 625.0)
        cls.corner_variant = cls.corner_tmpl.product_variant_id

        cls.appliance_tmpl = cls._make_cabinet_tmpl(
            "T26 Appliance Space", "appliance", 0.0)
        cls.appliance_variant = cls.appliance_tmpl.product_variant_id

    @classmethod
    def _make_cabinet_tmpl(cls, name, cabinet_type, price):
        tmpl = cls.Template.create({
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

    def _design(self):
        # Generous room (within the 12-480"/12-480"/84-144" bounds
        # _check_room_bounds enforces) so 5 cabinets spaced 90" apart
        # along the back wall never trip OUT_OF_ROOM_BOUNDS or the
        # collision checks — this test is about the quote/reconcile
        # mirror, not geometry.
        return self.Design.create({
            "name":            "T26 Dedup Test Design",
            "partner_id":      self.partner.id,
            "room_width_in":   460.0,
            "room_depth_in":   100.0,
            "room_height_in":  96.0,
            "state":           "draft",
        })

    def _corner_bearing_design(self):
        """4 corner lines + 1 appliance line, all origin=configurator,
        mirroring the opportunity-trace's "U design (4 corner + 1
        appliance)" shape. Corner lines carry distinct authored
        price_unit values so the "not re-priced at quote time"
        assertion is non-trivial. The first corner line and the
        appliance line set an explicit layout_key; the other three
        corner lines leave it blank to exercise reconcile's/the fix's
        `design-{id}-line-{id}` fallback formula too.
        """
        design = self._design()
        corner_prices = [600.0, 625.0, 650.0, 675.0]
        for i, price in enumerate(corner_prices):
            self.Line.create({
                "design_id":     design.id,
                "product_id":    self.corner_variant.id,
                "quantity":      1,
                "price_unit":    price,
                "cabinet_type":  "corner",
                "origin":        "configurator",
                "width_in":      36.0,
                "height_in":     34.5,
                "depth_in":      24.0,
                "x_position_in": i * 90.0,
                "z_position_in": 0.0,
                "layout_key":    "t26-corner-%d" % i if i == 0 else False,
            })
        self.Line.create({
            "design_id":      design.id,
            "product_id":     self.appliance_variant.id,
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
            "layout_key":     "t26-appliance-0",
        })
        design.invalidate_recordset()
        return design

    def _stats(self):
        return {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                "created_lines": 0, "divergence": 0, "errors": 0}

    # ── 1) The direct-quote path shape ─────────────────────────────────

    def test_quote_shape_draft_so_corner_and_appliance_lines_bom_seeded(self):
        design = self._corner_bearing_design()
        self.assertEqual(len(design.cabinet_line_ids), 5)

        design.action_create_quotation()

        order = design.sale_order_id
        self.assertTrue(order, "action_create_quotation must set sale_order_id")
        self.assertEqual(order.origin, design.name)
        self.assertEqual(order.state, "draft",
                          "no confirm_immediately in context -> SO stays draft")
        self.assertEqual(design.state, "quoted")

        self.assertEqual(
            len(order.order_line), 5,
            "5 SO lines must mirror the 5 active design lines "
            "(4 corner + 1 appliance)",
        )

        appliance_lines = order.order_line.filtered(
            lambda l: l.product_id.product_tmpl_id.southbrook_cabinet_type
            == "appliance"
        )
        self.assertEqual(len(appliance_lines), 1)
        self.assertEqual(appliance_lines.price_unit, 0.0)

        corner_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(len(corner_lines), 4)
        for dline in corner_lines:
            self.assertTrue(
                dline.sale_order_line_id,
                "T26 fix — sale_order_line_id must be written back onto "
                "every configurator-origin design line at quote time",
            )
            self.assertEqual(
                dline.sale_order_line_id.price_unit, dline.price_unit,
                "corner SO line price must equal the design line's "
                "authored price_unit verbatim — quote time never calls "
                "_sb_variant_price (only the reconcile cron's draft_order "
                "branch does)",
            )

        # BOM autoseed ran (pre-quote AND post-create passes) for both
        # distinct templates referenced by the 5 lines.
        for tmpl in (self.corner_tmpl, self.appliance_tmpl):
            self.assertTrue(
                self.Bom.sudo().search_count([
                    ("product_tmpl_id", "=", tmpl.id),
                    ("type", "=", "normal"),
                ]),
                "%s must have an mrp.bom after action_create_quotation"
                % tmpl.display_name,
            )

    # ── 2) The regression: reconcile must not duplicate ────────────────

    def test_quote_then_reconcile_tick_creates_no_duplicate_lines(self):
        design = self._corner_bearing_design()
        design.action_create_quotation()
        order = design.sale_order_id

        self.assertEqual(len(order.order_line), 5)
        # Sanity — the fix's stamping, asserted directly before touching
        # reconcile at all.
        for dline in design.cabinet_line_ids:
            self.assertTrue(dline.sale_order_line_id)
            sol = dline.sale_order_line_id
            self.assertEqual(sol.sb_layout_origin, "configurator")
            expected_key = dline.layout_key or (
                "design-%d-line-%d" % (design.id, dline.id))
            self.assertEqual(sol.sb_layout_key, expected_key)

        # action_create_quotation's own `design.sale_order_id = order.id`
        # write already advanced design.write_date — exactly the trigger
        # condition that used to spawn the duplicate set. Run the raw
        # per-design tick directly first.
        stats = self._stats()
        self.Reconcile._reconcile_one(design, stats)

        order.invalidate_recordset()
        self.assertEqual(
            stats["created_lines"], 0,
            "T26 regression — reconcile must recognize all 5 lines as "
            "already mirrored via sale_order_line_id, not create a "
            "second set",
        )
        self.assertEqual(
            len(order.order_line), 5,
            "SO line count must be unchanged by the reconcile tick",
        )

        # And the cron entrypoint (the real 5-min tick path), which
        # additionally proves the write_date watermark logic picks this
        # design up.
        design.invalidate_recordset()
        self.Reconcile.cron_reconcile_designs()
        order.invalidate_recordset()
        self.assertEqual(
            len(order.order_line), 5,
            "cron_reconcile_designs() must also leave the SO line count "
            "unchanged",
        )

        # Running it again (idempotency, matching the sibling M2 tests'
        # convention) must still be a no-op.
        design.invalidate_recordset()
        stats2 = self._stats()
        self.Reconcile._reconcile_one(design, stats2)
        order.invalidate_recordset()
        self.assertEqual(stats2["created_lines"], 0)
        self.assertEqual(len(order.order_line), 5)

    def test_manual_origin_design_line_is_never_stamped_or_swept(self):
        """A manual/backend-authored design line quoted alongside
        configurator lines must NOT get sb_layout_origin='configurator'
        stamped on its mirrored SO line — reconcile's stale-line sweep
        (step 5 of _reconcile_one) deletes any SO line tagged
        sb_layout_origin='configurator' whose key isn't in the design's
        CURRENT configurator-origin key set, and a manual line's key is
        never in that set. Stamping it anyway would make a legitimate
        manually-added quote line vanish on the next reconcile tick.
        """
        design = self._corner_bearing_design()
        manual_line = self.Line.create({
            "design_id":     design.id,
            "product_id":    self.corner_variant.id,
            "quantity":      1,
            "price_unit":    999.0,
            "cabinet_type":  "corner",
            "origin":        "manual",
            "width_in":      36.0,
            "height_in":     34.5,
            "depth_in":      24.0,
            "x_position_in": 410.0,
            "z_position_in": 0.0,
        })
        design.invalidate_recordset()

        design.action_create_quotation()
        order = design.sale_order_id
        self.assertEqual(len(order.order_line), 6)

        self.assertFalse(
            manual_line.sale_order_line_id,
            "the fix only back-links configurator-origin design lines",
        )
        manual_sol = order.order_line.filtered(
            lambda l: l.price_unit == 999.0)
        self.assertEqual(len(manual_sol), 1)
        self.assertNotEqual(
            manual_sol.sb_layout_origin, "configurator",
            "a manual-origin design line's mirrored SO line must not be "
            "tagged sb_layout_origin='configurator' — it would become "
            "eligible for reconcile's configurator-only stale-line sweep",
        )
        self.assertFalse(manual_sol.sb_layout_key)

        # Reconcile must still leave everything alone: the 5
        # configurator lines resolve via sale_order_line_id (no-op), and
        # the manual line is skipped outright by reconcile's `if dline.
        # origin != "configurator": continue` guard — never swept either,
        # since the sweep only searches sb_layout_origin='configurator'.
        stats = self._stats()
        self.Reconcile._reconcile_one(design, stats)
        order.invalidate_recordset()
        self.assertEqual(stats["created_lines"], 0)
        self.assertEqual(len(order.order_line), 6)
        self.assertIn(manual_sol, order.order_line)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "quote_reconcile_dedup")
class TestQuoteReuseExistingReconcileOrder(TransactionCase):
    """Task 27 — action_create_quotation must reuse a draft sale.order
    that reconcile already lazily created (via action_auto_arrange(
    sync=True)) instead of spawning a second, orphaned one.

    Fixture recipe borrowed verbatim from the sibling
    test_auto_arrange_service.py's `_make_design`/setUp (the proven-
    working minimal input for `action_auto_arrange`).
    """

    def setUp(self):
        super().setUp()
        Template = self.env["product.template"]
        for code, name, price in [("SB-CORNER", "Corner Base", 625.0),
                                   ("SB-WALL-CORNER", "Corner Wall", 295.0)]:
            if not Template.search([("default_code", "=", code)], limit=1):
                Template.create({"name": name, "default_code": code,
                                  "list_price": price})
        self.base_prod = self.env["product.product"].create({
            "name": "T27 Test Base 24", "default_code": "ZZ-T27-B24",
            "list_price": 200.0,
        })
        self.partner = self.env["res.partner"].create({
            "name": "T27 Reuse Test Customer",
        })
        self.Design = self.env["southbrook.kitchen.design"]
        self.Line = self.env["southbrook.kitchen.design.line"]
        self.SaleOrder = self.env["sale.order"]

    def _make_design(self, n=10, width_in=24.0, room_w=140.0, room_d=200.0):
        design = self.Design.create({
            "name": "T27 Reuse Test Design", "partner_id": self.partner.id,
            "room_width_in": room_w, "room_depth_in": room_d,
            "room_height_in": 96.0, "state": "draft"})
        for i in range(n):
            self.Line.create({
                "design_id": design.id, "product_id": self.base_prod.id,
                "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
                "width_in": width_in, "height_in": 34.5, "depth_in": 24.0,
                "sequence": i * 10, "origin": "configurator",
                "layout_key": "t27-%d" % i,
            })
        return design

    def test_reconcile_created_order_is_reused_not_orphaned(self):
        design = self._make_design()
        self.assertFalse(design.sale_order_id)

        # action_auto_arrange defaults to sync=True (matching the real
        # "Auto-arrange L/U" button) and, via reconcile.py step 1,
        # lazily creates a draft sale.order for this now-partnered
        # design.
        design.action_auto_arrange()
        reconcile_order = design.sale_order_id
        self.assertTrue(
            reconcile_order,
            "reconcile._reconcile_one must lazily create a draft SO for "
            "a partnered design with no sale_order_id yet",
        )
        self.assertEqual(reconcile_order.state, "draft")

        orders_before = self.SaleOrder.search_count(
            [("origin", "=", design.name)])
        self.assertEqual(
            orders_before, 1,
            "sanity — exactly one order exists before action_create_"
            "quotation is ever called",
        )

        design.action_create_quotation()

        self.assertEqual(
            design.sale_order_id.id, reconcile_order.id,
            "T27 fix — action_create_quotation must REUSE the draft SO "
            "reconcile already created, not spawn a second one",
        )
        orders_after = self.SaleOrder.search_count(
            [("origin", "=", design.name)])
        self.assertEqual(
            orders_before, orders_after,
            "zero orphan sale.orders — the count must be unchanged by "
            "action_create_quotation reusing the existing draft order",
        )
        self.assertEqual(orders_after, 1)
        self.assertEqual(design.state, "quoted")

        # And it's a real, usable quotation, not an empty shell —
        # reused-order lines mirror the design's (now arranged) active
        # cabinet lines 1:1.
        self.assertEqual(
            len(reconcile_order.order_line),
            len(design.cabinet_line_ids),
        )

    def test_non_draft_linked_order_still_falls_through_to_create(self):
        """Existing convention preserved: if the linked order is no
        longer draft, action_create_quotation must NOT try to mutate it
        — it creates a fresh order instead, exactly as it always has
        for that case. State forced directly via write() to isolate
        this from the (unrelated, gate-heavy) confirm workflow — this
        test is only about action_create_quotation's own draft-vs-not
        branch."""
        design = self._make_design()
        design.action_auto_arrange()
        first_order = design.sale_order_id
        self.assertEqual(first_order.state, "draft")
        first_order.sudo().write({"state": "sale"})

        design.action_create_quotation()

        self.assertNotEqual(
            design.sale_order_id.id, first_order.id,
            "a confirmed linked order must not be reused/mutated by "
            "action_create_quotation",
        )
        self.assertEqual(design.sale_order_id.state, "draft")
