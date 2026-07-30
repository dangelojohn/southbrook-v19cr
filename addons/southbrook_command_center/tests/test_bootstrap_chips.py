# SPDX-License-Identifier: LGPL-3.0-only
"""The chips must be Process Explorer's numbers, and must carry their units."""
from odoo.tests import TransactionCase, tagged

CHIP_KEYS = ["orders", "running", "ready", "blocked", "late", "at_risk",
             "material_short", "capacity_over"]


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestBootstrapChips(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bridge = cls.env["southbrook.command.explorer_bridge"]
        cls.CC = cls.env["southbrook.command.center"]

    def test_eight_chips_with_units(self):
        chips = self.CC.truth_chips()
        self.assertEqual([c["key"] for c in chips], CHIP_KEYS)
        for c in chips:
            self.assertTrue(c["unit"], "every chip states its unit: %s" % c["key"])

    def test_chip_values_equal_the_explorer_payload(self):
        """The core guarantee: chips must equal Process Explorer's own payload.

        Three prior rounds built fixtures where the six compared values
        (orders, running, ready, blocked, late, at_risk) were NOT pairwise
        distinct, so a chip-wiring bug that swapped two keys read an equal
        number from both sides and passed anyway:

          - round 1: orders=1, running=0, ready=0, blocked=0, late=0,
            at_risk=0 (five of six were 0 == 0).
          - round 2: orders=1, running=1, ready=2, blocked=3, late=1,
            at_risk=0 (orders/running/late all 1).
          - round 3: orders=2, running=3, ready=2, blocked=1, late=1,
            at_risk=0 (orders collides with ready; blocked collides with
            late; at_risk still uncovered).

        This fixture is built so the six values are 5, 3, 6, 4, 2, 1 —
        a permutation of 1..6, so every pair differs and swapping ANY two
        chip keys is caught immediately. The distinctness itself is
        asserted below, before the equality checks run, so a future
        fixture edit that reintroduces a coincidental collision fails
        loudly instead of silently hollowing out this test again.

        Fixture layout (5 open MOs):
          - MO late_a, late_b: past deadline -> late=2. 1 work order each,
            left BLOCKED -> contributes 2 to blocked.
          - MO ok: far-future deadline, small routing durations -> neither
            late nor at-risk. 3 work orders, left READY -> contributes 3
            to ready.
          - MO shortage: far-future deadline, small routing durations, and
            a BoM component with no on-hand stock -> not late/at-risk, but
            IS material-short. 3 work orders: 2 READY + 1 BLOCKED ->
            contributes 2 to ready, 1 to blocked.
          - MO risk: near-future deadline (tomorrow) but 5 work orders on
            one dedicated work center at 600 minutes (10h) each = 50
            scheduled hours queued on that single station. That is both
            (a) impossible to finish by tomorrow -> at_risk=1, and
            (b) far more than a day's capacity on that station ->
            capacity_over=1 for that station. 3 PROGRESS + 1 READY + 1
            BLOCKED -> contributes 3 to running, 1 to ready, 1 to blocked.

        Totals: orders=5, late=2, at_risk=1, running=3, ready=3+2+1=6,
        blocked=2+1+1=4. material_short=1 (only "shortage"). capacity_over
        is asserted against a live recomputation from workcenters[], not a
        hardcoded number, since its exact value depends on the test
        database's working-hours calendar.

        ORDER-SENSITIVE FIXTURE: ``mrp.workorder.state`` is a stored
        compute (``_compute_state``, ``@api.depends('qty_ready')``, store
        =True) that collapses to 'blocked' whenever it re-fires with
        qty_ready == 0 (true for every fresh work order here). Direct
        ``wo.state = ...`` assignment below works ONLY because nothing
        after it touches qty_producing/qty_produced/reservation on these
        work orders before ``Bridge.fetch()`` is called — fetch() is a
        read-only query. If a future edit adds any write to these work
        orders (or their MOs) between the assignments and the fetch, the
        recompute can re-fire and collapse the asymmetry this test
        depends on.
        """
        from datetime import datetime, timedelta
        from odoo import fields

        today = fields.Date.context_today(self.env["res.users"])

        def _past(days):
            return datetime.combine(today - timedelta(days=days),
                                     datetime.min.time().replace(hour=12))

        def _future(days):
            return datetime.combine(today + timedelta(days=days),
                                     datetime.min.time().replace(hour=12))

        def _make_bom(product, workcenter, n_ops, minutes):
            bom = self.env["mrp.bom"].create({
                "product_tmpl_id": product.product_tmpl_id.id,
                "product_qty": 1.0,
                "type": "normal",
            })
            for i in range(n_ops):
                self.env["mrp.routing.workcenter"].create({
                    "bom_id": bom.id,
                    "name": "Op %d" % (i + 1),
                    "workcenter_id": workcenter.id,
                    "time_cycle_manual": minutes,
                })
            return bom

        def _confirm_and_set_states(product, bom, deadline, states):
            mo = self.env["mrp.production"].create({
                "product_id": product.id,
                "product_qty": 1.0,
                "bom_id": bom.id,
                "date_deadline": deadline,
            })
            # bypass_availability_gate: southbrook_premium_orchestration
            # gates action_confirm() on component reservation (SAMI PRD
            # MO-08) and would otherwise call action_assign() and raise
            # UserError for the deliberately-undersupplied "shortage" MO
            # below before mat_short can ever be observed.
            mo.with_context(bypass_availability_gate=True).action_confirm()
            wos = self.env["mrp.workorder"].search(
                [("production_id", "=", mo.id)], order="sequence, id"
            )
            self.assertEqual(len(wos), len(states),
                              "%s must generate %d work orders" % (mo.name, len(states)))
            for wo, state in zip(wos, states):
                wo.state = state  # direct assignment — see ORDER-SENSITIVE note above
            return mo

        # --- Work centers -------------------------------------------------
        # Both left on the company's default resource calendar (not
        # overridden) so hours_per_day is a real number for BOTH stations —
        # required for capacity_over (workcenters[].queued_days) to be
        # anything other than always-None/always-0.
        wc_normal = self.env["mrp.workcenter"].create({
            "name": "CC Chip Fixture — Normal", "code": "CC_WC_NORMAL",
        })
        wc_overload = self.env["mrp.workcenter"].create({
            "name": "CC Chip Fixture — Overload", "code": "CC_WC_OVERLOAD",
        })

        # --- Products -------------------------------------------------
        product = self.env["product.product"].create({
            "name": "CC Chip Fixture Product", "type": "consu",
        })
        component = self.env["product.product"].create({
            "name": "CC Chip Fixture Component (no stock on hand)",
            "type": "consu", "is_storable": True,
        })

        # --- BoMs: small/fast ops on wc_normal, one huge/slow on wc_overload
        bom_1op = _make_bom(product, wc_normal, n_ops=1, minutes=6)
        bom_3op = _make_bom(product, wc_normal, n_ops=3, minutes=6)
        bom_3op_shortage = _make_bom(product, wc_normal, n_ops=3, minutes=6)
        self.env["mrp.bom.line"].create({
            "bom_id": bom_3op_shortage.id,
            "product_id": component.id,
            "product_qty": 10.0,  # no stock exists anywhere for `component`
        })
        bom_5op_risk = _make_bom(product, wc_overload, n_ops=5, minutes=600.0)

        # --- MOs ------------------------------------------------------
        # late_a, late_b: past deadline -> late count. Left BLOCKED.
        _confirm_and_set_states(product, bom_1op, _past(2), ["blocked"])
        _confirm_and_set_states(product, bom_1op, _past(1), ["blocked"])

        # ok: far-future deadline, small durations -> neither late nor at-risk.
        _confirm_and_set_states(product, bom_3op, _future(90),
                                 ["ready", "ready", "ready"])

        # shortage: far-future deadline, small durations, undersupplied
        # component -> not late/at-risk, but IS material-short.
        _confirm_and_set_states(product, bom_3op_shortage, _future(90),
                                 ["ready", "ready", "blocked"])

        # risk: near-future deadline but 50 scheduled hours queued onto one
        # dedicated station -> at_risk AND capacity_over.
        _confirm_and_set_states(product, bom_5op_risk, _future(1),
                                 ["progress", "progress", "progress",
                                  "ready", "blocked"])

        # --- Act: fetch the payload ------------------------------------
        payload, _prov = self.Bridge.fetch()
        self.assertTrue(
            payload,
            "Process Explorer payload must be available after seeding five "
            "MOs; got: %s" % self.Bridge.fetch()[1]
        )
        self.assertIn("workload", payload, "Payload must have workload section")
        workload = payload["workload"]
        chips = {c["key"]: c["value"] for c in self.CC.truth_chips()}

        # --- The property that actually ends this: distinctness is
        # asserted BEFORE the equality checks, so the equality checks
        # below can never again be vacuous.
        compared = [chips["orders"], chips["running"], chips["ready"],
                    chips["blocked"], chips["late"], chips["at_risk"]]
        self.assertEqual(
            len(set(compared)), len(compared),
            "fixture must produce pairwise-distinct values for orders/running/"
            "ready/blocked/late/at_risk, otherwise two of the equality "
            "assertions below compare equal numbers regardless of whether the "
            "chip keys are wired correctly — a swapped-key bug (the exact "
            "failure mode that shipped to production: dashboard said 100%% "
            "on-time while Process Explorer showed 10 of 13 orders late) "
            "would pass unnoticed. Got: %s" % compared,
        )

        # --- The core guarantee: each chip equals Process Explorer's own
        # number for that same quantity.
        self.assertEqual(chips["orders"], workload["orders"],
                          "orders chip %r must equal workload orders %r"
                          % (chips["orders"], workload["orders"]))
        self.assertEqual(chips["running"], workload["progress"],
                          "running chip %r must equal workload progress %r"
                          % (chips["running"], workload["progress"]))
        self.assertEqual(chips["ready"], workload["ready"],
                          "ready chip %r must equal workload ready %r"
                          % (chips["ready"], workload["ready"]))
        self.assertEqual(chips["blocked"], workload["blocked"],
                          "blocked chip %r must equal workload blocked %r"
                          % (chips["blocked"], workload["blocked"]))
        self.assertEqual(chips["late"], workload["late"],
                          "late chip %r must equal workload late %r"
                          % (chips["late"], workload["late"]))
        self.assertEqual(chips["at_risk"], workload["at_risk"],
                          "at_risk chip %r must equal workload at_risk %r"
                          % (chips["at_risk"], workload["at_risk"]))

        # Sanity: the fixture is designed to make every one of the six
        # values non-zero (not just distinct) so a chip wired to a
        # constant 0 default is also caught.
        for key, value in zip(
            ["orders", "running", "ready", "blocked", "late", "at_risk"],
            compared,
        ):
            self.assertTrue(value, "%s must be non-zero in this fixture, got %r"
                             % (key, value))

        # --- material_short and capacity_over: verified against the
        # payload's own secondary structures (rows[].mat_short and
        # workcenters[].queued_days), not merely checked for "is not None".
        # This is real coverage, not the "not-None" placeholder round 3
        # claimed (round 3 asserted only assertIsNotNone; it never compared
        # against rows/workcenters at all, so a wiring bug reading the
        # wrong field would have passed).
        expected_material_short = sum(
            1 for r in payload.get("rows", []) if r.get("mat_short")
        )
        self.assertEqual(chips["material_short"], expected_material_short,
                          "material_short chip %r must equal the count of "
                          "payload rows with mat_short truthy %r"
                          % (chips["material_short"], expected_material_short))
        self.assertGreaterEqual(
            expected_material_short, 1,
            "fixture must produce at least one material-short row (the "
            "'shortage' MO), otherwise this equality is a vacuous 0 == 0"
        )

        over_days = float(self.env["ir.config_parameter"].sudo().get_param(
            "command_center.chips.capacity_over_days", 1.0
        ))
        expected_capacity_over = sum(
            1 for s in payload.get("workcenters", [])
            if (s.get("queued_days") or 0) > over_days
        )
        self.assertEqual(chips["capacity_over"], expected_capacity_over,
                          "capacity_over chip %r must equal the count of "
                          "workcenters over the %.1f-day threshold %r"
                          % (chips["capacity_over"], over_days, expected_capacity_over))
        self.assertGreaterEqual(
            expected_capacity_over, 1,
            "fixture must drive at least one work center over the capacity "
            "threshold (the 'risk' MO's 50 queued hours on its dedicated "
            "station), otherwise this equality is a vacuous 0 == 0"
        )

    def test_chips_degrade_to_none_not_zero(self):
        """With no payload a chip is unknown, not zero — zero is a claim."""
        chips = self.CC.truth_chips(payload={})
        for c in chips:
            self.assertIsNone(c["value"])
