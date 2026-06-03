# SPDX-License-Identifier: LGPL-3.0-only
"""Tests for the production-approval gate folded into southbrook_mrp_pm.

Covers the chokepoint guard on action_send_to_production() and the
request/approve/reject workflow. Reuses SouthbrookTestCase from
southbrook_estimating (partner factories + standard model attrs).

Runs on the QNAP southbrook stack via:
    odoo --test-enable --test-tags southbrook -i southbrook_mrp_pm ...
"""
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from odoo.addons.southbrook_estimating.tests.common import SouthbrookTestCase


@tagged("post_install", "-at_install", "southbrook")
class TestProductionApproval(SouthbrookTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.approver_group = cls.env.ref(
            "southbrook_mrp_pm.group_southbrook_production_approver"
        )
        # An approver user (Sales Manager + the approver toggle) and a
        # plain salesman who must NOT be able to approve.
        cls.approver = cls.env["res.users"].create({
            "name": "Approver Annie",
            "login": "approver_annie",
            "group_ids": [(4, cls.approver_group.id)],
        })
        cls.salesman = cls.env["res.users"].create({
            "name": "Rep Rick",
            "login": "rep_rick",
            "group_ids": [(4, cls.env.ref("sales_team.group_sale_salesman").id)],
        })

    def _confirmed_order(self):
        """A sale order forced into 'sale' state for gate testing.
        We write state directly rather than action_confirm() so the
        test doesn't depend on priceable order lines / stock setup."""
        order = self.Order.create({"partner_id": self._make_dealer().id})
        order.write({"state": "sale"})
        return order

    # ------------------------------------------------------------------
    # The chokepoint guard
    # ------------------------------------------------------------------
    def test_01_gate_blocks_unapproved_send_to_production(self):
        order = self._confirmed_order()
        self.assertEqual(order.production_approval_state, "none")
        with self.assertRaises(UserError):
            order.action_send_to_production()

    # ------------------------------------------------------------------
    # Request
    # ------------------------------------------------------------------
    def test_02_request_sets_pending_and_requester(self):
        order = self._confirmed_order()
        order.with_user(self.salesman).action_request_production()
        self.assertEqual(order.production_approval_state, "pending")
        self.assertEqual(order.production_requested_by, self.salesman)
        self.assertTrue(order.production_requested_date)

    def test_03_request_requires_confirmed_order(self):
        order = self.Order.create({"partner_id": self._make_dealer().id})
        self.assertEqual(order.state, "draft")
        with self.assertRaises(UserError):
            order.action_request_production()

    def test_04_cannot_request_twice(self):
        order = self._confirmed_order()
        order.action_request_production()
        with self.assertRaises(UserError):
            order.action_request_production()

    # ------------------------------------------------------------------
    # Approve / reject permissions
    # ------------------------------------------------------------------
    def test_05_non_approver_cannot_approve(self):
        order = self._confirmed_order()
        order.action_request_production()
        with self.assertRaises(UserError):
            order.with_user(self.salesman).action_approve_production()

    def test_06_approve_flips_state_and_records_approver(self):
        order = self._confirmed_order()
        order.action_request_production()
        order.with_user(self.approver).action_approve_production()
        self.assertEqual(order.production_approval_state, "approved")
        self.assertEqual(order.production_approved_by, self.approver)
        self.assertTrue(order.production_approved_date)

    def test_07_approved_order_passes_the_gate(self):
        """Once approved, the guard no longer raises (no lines → no
        MOs created, but the call must not be blocked)."""
        order = self._confirmed_order()
        order.action_request_production()
        order.with_user(self.approver).action_approve_production()
        # Idempotent re-call returns existing (empty) without raising.
        self.assertFalse(order.action_send_to_production())

    def test_08_reject_returns_to_resubmittable_state(self):
        order = self._confirmed_order()
        order.action_request_production()
        order.production_reject_reason = "Spec mismatch on door style."
        order.with_user(self.approver).action_reject_production()
        self.assertEqual(order.production_approval_state, "rejected")
        self.assertEqual(order.production_approved_by, self.approver)
        # Rejected orders can be re-requested — and the prior decision
        # (rejecter + timestamp + reason) must be cleared, not carried.
        order.action_request_production()
        self.assertEqual(order.production_approval_state, "pending")
        self.assertFalse(order.production_approved_by)
        self.assertFalse(order.production_approved_date)
        self.assertFalse(order.production_reject_reason)

    def test_09_cannot_approve_unless_pending(self):
        order = self._confirmed_order()  # state 'none'
        with self.assertRaises(UserError):
            order.with_user(self.approver).action_approve_production()
