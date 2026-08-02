# SPDX-License-Identifier: LGPL-3.0-only
"""Tests for the EFFECTIVITY + PINNING feature landing in parallel
alongside this file (2026-08-02).

Written to the CONTRACT for that pass, not to whatever partial state
models/southbrook_eco.py, models/southbrook_cut_spec.py, and
models/sale_order_line.py happen to be in at any given moment. As of this
writing:

  A. Typed effectivity (effectivity_type / effective_date / use_up_note,
     their @api.constrains, action_apply's future-date refusal, and the
     _cron_apply_due_ecos() / _execute_apply() split on southbrook.eco)
     were ALREADY landed — this section exercises real, present
     behaviour rather than restating the contract on faith.
  B. The cut-spec version chain (active_from / active_to /
     predecessor_id / successor_id on southbrook.cut.spec, and
     _get_spec_active_at()) had NOT landed yet — this section is
     expected to fail (AttributeError / missing-field KeyError) until
     the parallel pass adds it.
  C. Order-line pinning: the base action_confirm trigger and
     _capture_southbrook_version_snapshots()'s idempotency guard were
     ALREADY landed. The create()-time trigger for a line added to an
     ALREADY-confirmed order had NOT landed — that one test is expected
     to fail until it does.

Contract covered (see the effectivity+pinning brief for full text):

  A1-A6  southbrook.eco typed effectivity + scheduled-apply cron.
  B7-B9  southbrook.cut.spec version chain + windows.
  C10-13 sale.order.line pin fields + snapshot capture.

TransactionCase runs as SUPERUSER: superuser passes every has_group()
check, so every gating assertion below runs via .with_user() with a
real, non-superuser fixture user. ECOs are created via a bare
self.Eco.create(...) (no with_user), which makes their user_id/
create_uid the test's superuser — every fixture approver below is a
DIFFERENT user, so the four-eyes rule (an ECO's own Responsible/creator
may not approve or apply it) is satisfied automatically without needing
emergency_bypass anywhere in this file.

Run with:
    odoo-bin --test-enable --stop-after-init \\
        -i southbrook_plm -d <db>
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


# ======================================================================
# Section A — southbrook.eco typed effectivity (contract items 1-6)
# ======================================================================
@tagged("post_install", "-at_install")
class TestEcoEffectivityScheduling(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Eco = cls.env["southbrook.eco"]
        cls.type_rule = cls.env.ref("southbrook_plm.eco_type_rule")

        cls.approver = new_test_user(
            cls.env,
            login="plm_effcron_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )

    def _make_rule_eco(self, title, git_ref="cafe0001", **extra):
        """A rule-kind ECO is the lightest fixture that satisfies
        _check_target_required (git_ref isn't even required at draft
        time for 'rule') — use it for tests that exercise generic
        effectivity/lifecycle plumbing rather than a specific target
        kind's apply mechanics."""
        vals = {"title": title, "eco_type_id": self.type_rule.id, "git_ref": git_ref}
        vals.update(extra)
        return self.Eco.create(vals)

    # ------------------------------------------------------------------
    # Item 1 — @api.constrains on effectivity_type / effective_date /
    # use_up_note.
    # ------------------------------------------------------------------
    def test_date_type_without_effective_date_raises(self):
        with self.assertRaises(ValidationError):
            self._make_rule_eco("Date type, no date", effectivity_type="date")

    def test_use_up_type_without_note_raises(self):
        with self.assertRaises(ValidationError):
            self._make_rule_eco("Use-up type, no note", effectivity_type="use_up")

    def test_past_effective_date_on_open_eco_raises(self):
        eco = self._make_rule_eco("Past date on open ECO")
        past = fields.Datetime.now() - timedelta(days=1)
        with self.assertRaises(ValidationError):
            eco.write({"effectivity_type": "date", "effective_date": past})

    # ------------------------------------------------------------------
    # Item 2 — an approved, future-scheduled ECO refuses a manual Apply;
    # switching back to Immediate is the sanctioned escape hatch (moving
    # the date into the past is refused by the constraint exercised in
    # test_past_effective_date_on_open_eco_raises above).
    # ------------------------------------------------------------------
    def test_approved_scheduled_future_eco_blocks_manual_apply(self):
        future = fields.Datetime.now() + timedelta(days=7)
        eco = self._make_rule_eco(
            "Scheduled future ECO blocks apply",
            effectivity_type="date",
            effective_date=future,
        )
        eco.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_apply()
        self.assertNotEqual(eco.state, "applied")

    def test_switching_to_immediate_then_allows_apply(self):
        future = fields.Datetime.now() + timedelta(days=7)
        eco = self._make_rule_eco(
            "Scheduled, then switched to Immediate",
            effectivity_type="date",
            effective_date=future,
        )
        eco.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_apply()
        eco.with_user(self.approver).write({"effectivity_type": "immediate"})
        eco.with_user(self.approver).action_apply()
        self.assertEqual(eco.state, "applied")

    # ------------------------------------------------------------------
    # Item 3 — the hourly cron applies a DUE scheduled ECO.
    #
    # TECHNIQUE for making a scheduled ECO "due" in a test: the
    # @api.constrains checked in test_past_effective_date_on_open_eco_
    # raises forbids writing a past-or-now effective_date onto a
    # non-applied ECO, so the date cannot simply be rewound after the
    # fact. Instead this schedules the ECO for a real near-future moment
    # (now + 2h — legitimately in the future at both create() and
    # action_approve() time) and then patches fields.Datetime.now() to a
    # moment AFTER that scheduled time, but ONLY for the duration of the
    # cron call itself. The cron's own search domain
    # (effective_date <= now()) then sees the ECO as due, exactly as it
    # would in production two hours later — without ever writing a
    # constraint-violating date onto an open ECO. This is option (a) from
    # the contract (freeze/patch fields.Datetime.now via
    # unittest.mock.patch), chosen because option (b) (writing the date
    # itself into the past) is exactly what the constraint exists to
    # forbid.
    # ------------------------------------------------------------------
    def test_cron_applies_due_scheduled_eco(self):
        future = fields.Datetime.now() + timedelta(hours=2)
        eco = self._make_rule_eco(
            "Cron applies due ECO",
            git_ref="croncafe1",
            effectivity_type="date",
            effective_date=future,
        )
        eco.with_user(self.approver).action_approve()
        self.assertEqual(eco.state, "open")
        before_ids = set(eco.message_ids.ids)
        later = future + timedelta(hours=1)
        with patch.object(fields.Datetime, "now", return_value=later):
            self.Eco._cron_apply_due_ecos()
        self.assertEqual(eco.state, "applied")
        new_bodies = " ".join(
            (m.body or "").lower()
            for m in eco.message_ids.filtered(lambda m: m.id not in before_ids)
        )
        self.assertTrue(
            any(tok in new_bodies for tok in ("automatic", "scheduled")),
            "cron apply must post a chatter line mentioning automatic/"
            "scheduled application",
        )

    # ------------------------------------------------------------------
    # Item 4 — the cron does NOT apply a due-but-never-approved ECO.
    # ------------------------------------------------------------------
    def test_cron_skips_unapproved_due_eco(self):
        future = fields.Datetime.now() + timedelta(hours=2)
        eco = self._make_rule_eco(
            "Cron must skip unapproved ECO",
            git_ref="croncafe2",
            effectivity_type="date",
            effective_date=future,
        )
        self.assertFalse(eco.approver_id)
        later = future + timedelta(hours=1)
        with patch.object(fields.Datetime, "now", return_value=later):
            self.Eco._cron_apply_due_ecos()
        self.assertEqual(eco.state, "open")
        self.assertFalse(eco.approver_id)

    # ------------------------------------------------------------------
    # Item 5 — per-ECO savepoint isolation: a raising apply for the
    # first due ECO must not block the second.
    # ------------------------------------------------------------------
    def test_cron_isolates_failure_between_due_ecos(self):
        future1 = fields.Datetime.now() + timedelta(hours=2)
        future2 = future1 + timedelta(minutes=5)
        eco1 = self._make_rule_eco(
            "Cron isolation ECO 1 (forced to fail)",
            git_ref="croncafe3",
            effectivity_type="date",
            effective_date=future1,
        )
        eco2 = self._make_rule_eco(
            "Cron isolation ECO 2 (must still apply)",
            git_ref="croncafe4",
            effectivity_type="date",
            effective_date=future2,
        )
        eco1.with_user(self.approver).action_approve()
        eco2.with_user(self.approver).action_approve()

        self.assertTrue(
            hasattr(type(eco1), "_execute_apply"),
            "contract names the per-ECO commit method _execute_apply; "
            "adapt this test if the implementation renamed it",
        )
        real_execute_apply = type(eco1)._execute_apply
        call_state = {"count": 0}

        def flaky_execute_apply(rec):
            call_state["count"] += 1
            if call_state["count"] == 1:
                raise RuntimeError("Simulated failure on the first due ECO")
            return real_execute_apply(rec)

        # due are searched "effective_date asc" (per the contract), so
        # eco1 (future1 < future2) is the first one the cron touches —
        # the forced failure lands on it, not eco2.
        later = future2 + timedelta(hours=1)
        with patch.object(fields.Datetime, "now", return_value=later), patch.object(
            type(eco1), "_execute_apply", flaky_execute_apply
        ):
            self.Eco._cron_apply_due_ecos()

        eco1_after = self.Eco.browse(eco1.id)
        eco2_after = self.Eco.browse(eco2.id)
        self.assertEqual(
            eco1_after.state,
            "open",
            "the failed ECO must remain open, retried on the next run",
        )
        self.assertEqual(
            eco2_after.state,
            "applied",
            "the second due ECO must still apply despite the first's "
            "failure — per-ECO savepoints must isolate them",
        )

    # ------------------------------------------------------------------
    # Item 6 — an 'immediate' ECO is unaffected by any of the above.
    # ------------------------------------------------------------------
    def test_immediate_eco_apply_unaffected(self):
        eco = self._make_rule_eco("Immediate ECO unaffected", git_ref="croncafe5")
        self.assertEqual(eco.effectivity_type, "immediate")
        eco.with_user(self.approver).action_approve()
        eco.with_user(self.approver).action_apply()
        self.assertEqual(eco.state, "applied")


# ======================================================================
# Section B — southbrook.cut.spec version chain + windows
# (contract items 7-9)
# ======================================================================
@tagged("post_install", "-at_install")
class TestCutSpecVersionChain(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Eco = cls.env["southbrook.eco"]
        cls.Spec = cls.env["southbrook.cut.spec"]
        cls.type_cut = cls.env.ref("southbrook_plm.eco_type_cut_spec")
        cls.seed_spec = cls.env.ref("southbrook_plm.cut_spec_nf14_seed")

        cls.approver = new_test_user(
            cls.env,
            login="plm_cutchain_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )
        cls.approver2 = new_test_user(
            cls.env,
            login="plm_cutchain_approver2",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )

    def _activate_via_eco(self, candidate, approver, apply_at=None):
        """Raise + approve + apply a cut-spec ECO that activates
        `candidate`. When `apply_at` is given, fields.Datetime.now() is
        frozen to it for the duration of the apply call so the resulting
        active_from/active_to stamps are deterministic — avoiding any
        flakiness from two real activations landing in the same
        wall-clock second (Datetime fields are second-precision)."""
        eco = self.Eco.create(
            {
                "title": "Activate %s" % candidate.name,
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(approver).action_approve()
        if apply_at is not None:
            with patch.object(fields.Datetime, "now", return_value=apply_at):
                eco.with_user(approver).action_apply()
        else:
            eco.with_user(approver).action_apply()
        return eco

    # ------------------------------------------------------------------
    # Item 7
    # ------------------------------------------------------------------
    def test_activation_links_predecessor_successor_and_abuts_windows(self):
        old = self.seed_spec
        candidate = self.Spec.create(
            {"name": "Chain: second-gen spec", "door_reveal": 3.5}
        )
        eco = self._activate_via_eco(candidate, self.approver)
        self.assertEqual(eco.state, "applied")
        self.assertEqual(old.state, "superseded")
        self.assertEqual(candidate.state, "active")
        self.assertEqual(
            old.active_to,
            candidate.active_from,
            "predecessor's active_to and successor's active_from must be "
            "stamped from the SAME `now` so the windows abut exactly",
        )
        self.assertEqual(old.successor_id, candidate)
        self.assertEqual(candidate.predecessor_id, old)

    # ------------------------------------------------------------------
    # Item 8 — a self-contained two-hop chain (spec A then spec B) with
    # frozen activation timestamps, so the "inside the old window" /
    # "after the switch" probes are deterministic rather than relying on
    # the seed spec's own (never explicitly activated, so possibly unset)
    # active_from.
    # ------------------------------------------------------------------
    def test_get_spec_active_at_resolves_inside_and_after_window(self):
        t_a = fields.Datetime.now() + timedelta(days=1)
        t_b = t_a + timedelta(days=1)

        spec_a = self.Spec.create(
            {"name": "Chain A: probe window", "door_reveal": 3.2}
        )
        self._activate_via_eco(spec_a, self.approver, apply_at=t_a)
        self.assertEqual(spec_a.state, "active")
        self.assertEqual(spec_a.active_from, t_a)

        spec_b = self.Spec.create(
            {"name": "Chain B: probe window", "door_reveal": 3.6}
        )
        self._activate_via_eco(spec_b, self.approver2, apply_at=t_b)
        self.assertEqual(spec_a.state, "superseded")
        self.assertEqual(spec_b.state, "active")
        self.assertEqual(spec_a.active_to, t_b)

        inside_a = t_a + timedelta(hours=1)
        self.assertEqual(self.Spec._get_spec_active_at(inside_a), spec_a)

        after_switch = t_b + timedelta(hours=1)
        self.assertEqual(self.Spec._get_spec_active_at(after_switch), spec_b)

    # ------------------------------------------------------------------
    # Item 9 — a datetime before ANY window must not raise. The exact
    # fallback (earliest spec vs empty recordset) is left to the
    # implementation; only "doesn't raise, returns at most one record"
    # is asserted, per the contract's "sensible ... per the documented
    # fallback" wording.
    # ------------------------------------------------------------------
    def test_get_spec_active_at_before_any_window_does_not_raise(self):
        long_ago = fields.Datetime.now() - timedelta(days=3650)
        result = self.Spec._get_spec_active_at(long_ago)
        self.assertIn(
            len(result),
            (0, 1),
            "must return a single spec or an empty recordset, never "
            "raise or return multiple records, for a datetime before any "
            "recorded window",
        )
        if result:
            self.assertEqual(result._name, "southbrook.cut.spec")


# ======================================================================
# Section C — sale.order.line pin fields + snapshot capture
# (contract items 10-13)
# ======================================================================
@tagged("post_install", "-at_install")
class TestOrderLinePinning(TransactionCase):
    """Fixture note: a full Southbrook cabinet template (dynamic-variant
    product_configurator flow) is deliberately NOT built here — it is
    heavy machinery this contract doesn't need. The cut-spec half of
    pinning only needs any confirmed order line; the BoM-version half
    only needs a plain product + a matching mrp.bom, exactly like the
    _make_bom_eco fixture the other PLM test files already use.
    Deliberately NOT covered here: a line whose product has no matching
    mrp.bom at all (already documented on
    _capture_southbrook_version_snapshots() as "BoM version stays at 0",
    not independently re-asserted), and the customer-portal / config-
    session path into order creation (out of scope for a PLM-layer test).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Inert on a stack without southbrook_mrp_pm installed; mirrors
        # southbrook_estimating's own SouthbrookTestCase base so this
        # file behaves the same whether or not that addon is present.
        cls.env = cls.env(
            context={**cls.env.context, "bypass_production_approval": True}
        )
        cls.Eco = cls.env["southbrook.eco"]
        cls.Spec = cls.env["southbrook.cut.spec"]
        cls.Bom = cls.env["mrp.bom"]
        cls.Order = cls.env["sale.order"]
        cls.OrderLine = cls.env["sale.order.line"]
        cls.Partner = cls.env["res.partner"]
        cls.Product = cls.env["product.product"]

        cls.type_cut = cls.env.ref("southbrook_plm.eco_type_cut_spec")
        cls.seed_spec = cls.env.ref("southbrook_plm.cut_spec_nf14_seed")

        cls.approver = new_test_user(
            cls.env,
            login="plm_pin_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )

    def _confirm_order(self, order):
        # Field-guarded exactly like southbrook_estimating's own
        # test_analytics_capture.py: southbrook_mrp_pm's SO->MO
        # production-approval gate is not a dependency of southbrook_plm,
        # but if it happens to be installed alongside in the test DB,
        # pre-approve so THAT unrelated gate never masks a pinning
        # assertion here.
        if "production_approval_state" in order._fields:
            order.production_approval_state = "approved"
        order.action_confirm()

    def _make_order_with_line(self, product=None):
        partner = self.Partner.create({"name": "Pin Test Partner"})
        product = product or self.Product.create(
            {"name": "Pin Test Cabinet", "type": "consu", "list_price": 500.0}
        )
        order = self.Order.create({"partner_id": partner.id})
        line = self.OrderLine.create(
            {
                "order_id": order.id,
                "product_id": product.id,
                "product_uom_qty": 1.0,
            }
        )
        return order, line

    # ------------------------------------------------------------------
    # Item 10
    # ------------------------------------------------------------------
    def test_confirm_pins_line_to_currently_active_spec(self):
        order, line = self._make_order_with_line()
        self.assertFalse(line.southbrook_cut_spec_version_id)
        self._confirm_order(order)
        self.assertEqual(order.state, "sale")
        self.assertEqual(line.southbrook_cut_spec_version_id, self.seed_spec)

    def test_confirm_pins_bom_version_snapshot(self):
        # The BoM-version half of the same trigger — see class docstring
        # for why this uses a bare product + mrp.bom rather than a full
        # cabinet template.
        product = self.Product.create(
            {"name": "Pin Test BoM Cabinet", "type": "consu"}
        )
        bom = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0}
        )
        self.assertEqual(bom.southbrook_version, 1)
        order, line = self._make_order_with_line(product=product)
        self._confirm_order(order)
        self.assertEqual(line.southbrook_bom_version, 1)

    # ------------------------------------------------------------------
    # Item 11 — NEW trigger: create() stamps a line added to an order
    # already in state 'sale'/'done'.
    # ------------------------------------------------------------------
    def test_line_added_to_already_confirmed_order_pinned_at_create(self):
        order, first_line = self._make_order_with_line()
        self._confirm_order(order)
        self.assertEqual(order.state, "sale")
        self.assertTrue(first_line.southbrook_cut_spec_version_id)

        second_product = self.Product.create(
            {"name": "Added After Confirm Cabinet", "type": "consu"}
        )
        second_line = self.OrderLine.create(
            {
                "order_id": order.id,
                "product_id": second_product.id,
                "product_uom_qty": 1.0,
            }
        )
        self.assertEqual(
            second_line.southbrook_cut_spec_version_id,
            self.seed_spec,
            "a line created on an order already in state sale/done must "
            "be pinned at create time, not left waiting for a re-confirm "
            "that will never happen",
        )

    # ------------------------------------------------------------------
    # Item 12 — draft-order lines are deliberately left unpinned.
    # ------------------------------------------------------------------
    def test_line_on_draft_order_not_pinned(self):
        order, line = self._make_order_with_line()
        self.assertEqual(order.state, "draft")
        self.assertFalse(line.southbrook_cut_spec_version_id)
        self.assertFalse(line.southbrook_bom_version)

    # ------------------------------------------------------------------
    # Item 13 — idempotency: an ECO applied AFTER an order was sold must
    # never re-spec that already-sold order.
    # ------------------------------------------------------------------
    def test_pin_idempotent_against_a_later_cut_spec_eco(self):
        order, line = self._make_order_with_line()
        self._confirm_order(order)
        original_spec = line.southbrook_cut_spec_version_id
        self.assertEqual(original_spec, self.seed_spec)

        new_spec = self.Spec.create(
            {"name": "Post-sale spec revision", "door_reveal": 4.2}
        )
        eco = self.Eco.create(
            {
                "title": "Post-sale spec revision ECO",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": new_spec.id,
            }
        )
        eco.with_user(self.approver).action_approve()
        eco.with_user(self.approver).action_apply()
        self.assertEqual(new_spec.state, "active")

        # Re-running the capture on an already-pinned line must be a
        # no-op.
        line._capture_southbrook_version_snapshots()
        self.assertEqual(line.southbrook_cut_spec_version_id, original_spec)
        self.assertNotEqual(line.southbrook_cut_spec_version_id, new_spec)
