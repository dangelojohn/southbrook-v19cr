# SPDX-License-Identifier: LGPL-3.0-only
"""Variant-BoM versioning lock tests (Step 3).

Proves that:
  - action_confirm captures the active cut-spec id on every line
  - action_confirm captures the active BoM's southbrook_version
  - A cut-spec ECO applied AFTER confirm does NOT change the snapshot
  - A cut-spec ECO applied BEFORE confirm IS reflected in the snapshot
  - The snapshots are idempotent under re-confirm (the original
    snapshot survives)

See docs/CUSTOMER_TO_MANUFACTURING_FLOW.md §4 for the architecture.
"""
from odoo.tests import TransactionCase, new_test_user, tagged


ESTIMATING_DEFAULTS = {
    "box_th": 15.875,
    "back_th": 6.35,
    "rabbet": 6.35,
    "door_th": 18.0,
    "door_reveal": 3.0,
    "shelf_tol": 1.5,
    "shelf_vent_gap": 12.7,
    "toekick_h": 101.6,
}


@tagged("post_install", "-at_install")
class TestVariantBomSnapshot(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Order = cls.env["sale.order"]
        cls.Spec = cls.env["southbrook.cut.spec"]
        cls.Eco = cls.env["southbrook.eco"]
        cls.Bom = cls.env["mrp.bom"]
        cls.Partner = cls.env["res.partner"]
        cls.Product = cls.env["product.product"]
        cls.seed_spec = cls.env.ref("southbrook_plm.cut_spec_nf14_seed")
        cls.type_cut = cls.env.ref("southbrook_plm.eco_type_cut_spec")

        cls.approver = new_test_user(
            cls.env,
            login="snapshot_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,"
                   "base.group_user,sales_team.group_sale_salesman",
        )
        cls.partner = cls.Partner.create(
            {"name": "Snapshot Test Partner"}
        )
        cls.product = cls.Product.create(
            {"name": "Snapshot Test Cabinet", "type": "consu"}
        )
        # Pre-existing BoM at v1 for the BoM-version snapshot test.
        cls.bom = cls.Bom.create(
            {
                "product_tmpl_id": cls.product.product_tmpl_id.id,
                "product_qty": 1.0,
            }
        )

    def _make_order(self):
        return self.Order.create(
            {
                "partner_id": self.partner.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 1.0,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )

    # ------------------------------------------------------------------
    # Write-side tests
    # ------------------------------------------------------------------
    def test_confirm_captures_cut_spec_snapshot(self):
        """action_confirm writes the active cut spec id on every line.
        """
        order = self._make_order()
        line = order.order_line
        # Pre-confirm: snapshot empty.
        self.assertFalse(line.southbrook_cut_spec_version_id)

        order.action_confirm()

        self.assertEqual(
            line.southbrook_cut_spec_version_id, self.seed_spec,
        )

    def test_confirm_captures_bom_version_snapshot(self):
        """action_confirm writes the active BoM's southbrook_version."""
        order = self._make_order()
        line = order.order_line
        self.assertEqual(line.southbrook_bom_version, 0)

        order.action_confirm()

        self.assertEqual(
            line.southbrook_bom_version, self.bom.southbrook_version
        )

    # ------------------------------------------------------------------
    # Read-side / temporal-ordering tests — the structural keystone
    # ------------------------------------------------------------------
    def test_eco_apply_after_confirm_does_NOT_change_snapshot(self):
        """The whole point: an order confirmed on cut-spec v1 must
        keep v1 even after an ECO activates a new spec.
        """
        order = self._make_order()
        order.action_confirm()
        line = order.order_line
        original_spec = line.southbrook_cut_spec_version_id
        self.assertEqual(original_spec, self.seed_spec)

        # Apply a cut-spec ECO that activates a candidate.
        candidate = self.Spec.create(
            {
                "name": "Post-confirm candidate",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 4.0},
            }
        )
        eco = self.Eco.create(
            {
                "title": "Post-confirm reveal bump",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(self.approver).action_apply()

        # Active spec changed — but the line's snapshot did NOT.
        self.assertEqual(self.Spec._get_active(), candidate)
        self.assertEqual(
            line.southbrook_cut_spec_version_id, original_spec
        )

    def test_eco_apply_before_confirm_IS_reflected_in_snapshot(self):
        """The other side: an ECO applied BEFORE confirm means the
        order locks against the NEW spec, not the old one.
        """
        # Apply ECO first.
        candidate = self.Spec.create(
            {
                "name": "Pre-confirm candidate",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 4.5},
            }
        )
        eco = self.Eco.create(
            {
                "title": "Pre-confirm reveal bump",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(self.approver).action_apply()
        # Now create + confirm the order.
        order = self._make_order()
        order.action_confirm()

        # Snapshot captures the NEW spec.
        self.assertEqual(
            order.order_line.southbrook_cut_spec_version_id, candidate
        )

    def test_snapshot_is_idempotent_under_recapture(self):
        """Defensive: even if _capture_southbrook_version_snapshots
        is called twice, the original snapshot is preserved.
        """
        order = self._make_order()
        order.action_confirm()
        line = order.order_line
        original_spec = line.southbrook_cut_spec_version_id

        # Activate a new spec mid-stream (which the order's snapshot
        # should ignore on re-capture).
        candidate = self.Spec.create(
            {
                "name": "Idempotency candidate",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 5.0},
            }
        )
        eco = self.Eco.create(
            {
                "title": "For idempotency test",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(self.approver).action_apply()

        # Re-run the capture — original snapshot survives.
        line._capture_southbrook_version_snapshots()
        self.assertEqual(
            line.southbrook_cut_spec_version_id, original_spec
        )
