# SPDX-License-Identifier: LGPL-3.0-only
"""QA follow-up (2026-07-05): action_confirm() must enforce the same
hard config-rule checks (Rule 1 series/door, Rule 2 Maple/Contractor)
that the customer portal's preflight check already enforced client-side
for its own "Send to Manufacturing" button. Before this fix, the
standard backend Sales Order "Confirm" button had no equivalent guard —
a rep could push an invalid line combination straight into a real
Manufacturing Order.

STALE FIXTURE REPAIRED 2026-08-03 (SBK-049)
-------------------------------------------
These tests used to simulate an invalid line by writing the offending
text into `sale.order.line.name`, on the stated reasoning that the name
is user-editable free text and the validator scanned it.

A code review on the very same day (2026-07-05) changed the validator to
do the opposite — scan ONLY the variant's real
`product.template.attribute.value` records and never `line.name` —
precisely because the name is user-editable: a rep typing
"Contractor: rush job" into the description of a legitimate woodgrain
line must not hard-block Confirm.

So three of these tests have been failing since the day they were
written. Nobody saw it, because an unrelated SCSS defect was crashing
asset pregeneration and aborting the entire post_install phase, while
the runner printed the earlier at_install count as though the suite had
passed. 871 of 1007 tests were not running.

The fixtures now build real variants carrying real attribute values,
which is what the validator actually reads. The assertions are
unchanged — the rules being enforced are the same ones.
"""
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import SouthbrookTestCase


@tagged("post_install", "-at_install", "southbrook", "rule_enforcement")
class TestActionConfirmHardValidation(SouthbrookTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmpl = cls.env.ref("southbrook_estimating.base_1dr")
        cls.partner = cls.Partner.create({"name": "Confirm Guard Test Customer"})
        cls.variant = cls.Product.create({"product_tmpl_id": cls.tmpl.id})

        # SBK-049 — real attribute-carrying variants, because that is what
        # southbrook_hard_validation_issues() actually reads.
        Attribute = cls.env["product.attribute"]
        Value = cls.env["product.attribute.value"]

        def _attr(name, values):
            attr = Attribute.create({"name": name, "create_variant": "always"})
            return attr, {
                v: Value.create({"name": v, "attribute_id": attr.id})
                for v in values
            }

        a_series, v_series = _attr("Series", ["Contractor Series"])
        a_box, v_box = _attr("Box Material", ["Maple (Hard)", "White Melamine"])
        a_door, v_door = _attr(
            "Door Style", ["Five-Piece Woodgrain", "Thermofoil Slab"])

        def _variant(name, chosen):
            tmpl = cls.env["product.template"].create({
                "name": name,
                "type": "consu",
                "sale_ok": True,
                "list_price": 300.0,
                "attribute_line_ids": [
                    (0, 0, {"attribute_id": a.id, "value_ids": [(6, 0, [v.id])]})
                    for a, v in chosen
                ],
            })
            return tmpl.product_variant_ids[:1]

        cls.variant_maple_contractor = _variant(
            "Guard · Maple box on Contractor", [
                (a_series, v_series["Contractor Series"]),
                (a_box, v_box["Maple (Hard)"]),
            ])
        cls.variant_contractor_fivepiece = _variant(
            "Guard · Five-piece door on Contractor", [
                (a_series, v_series["Contractor Series"]),
                (a_door, v_door["Five-Piece Woodgrain"]),
            ])
        cls.variant_clean = _variant(
            "Guard · Compliant Contractor cabinet", [
                (a_series, v_series["Contractor Series"]),
                (a_box, v_box["White Melamine"]),
                (a_door, v_door["Thermofoil Slab"]),
            ])

    def _make_order(self, line_name, variant=None):
        order = self.Order.create({
            "partner_id": self.partner.id,
            # southbrook_mrp_pm gates action_confirm on production
            # approval for any order with cabinet lines — pre-approve so
            # these tests exercise OUR hard-validation guard specifically,
            # not that unrelated (and already-tested) approval gate.
            "production_approval_state": "approved",
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": (variant or self.variant).id,
            "name": line_name,
            "product_uom_qty": 1,
        })
        return order

    def test_confirm_blocked_on_maple_contractor_line(self):
        order = self._make_order(
            "Base Cabinet - Contractor Series, Maple Box",
            variant=self.variant_maple_contractor)
        with self.assertRaises(
            UserError,
            msg="action_confirm must block a Maple + Contractor line, "
                "same as the portal's Send-to-Manufacturing preflight",
        ) as ctx:
            order.action_confirm()
        msg = str(ctx.exception)
        self.assertIn("Maple", msg)
        self.assertIn("Contractor", msg)
        self.assertNotEqual(
            order.state, "sale",
            "order must NOT have been confirmed when a hard rule fires",
        )

    def test_confirm_blocked_on_contractor_five_piece_line(self):
        order = self._make_order(
            "Wall Cabinet - Contractor Series, Five-Piece Door",
            variant=self.variant_contractor_fivepiece)
        with self.assertRaises(UserError) as ctx:
            order.action_confirm()
        self.assertIn("slab", str(ctx.exception).lower())
        self.assertNotEqual(order.state, "sale")

    def test_confirm_succeeds_on_clean_line(self):
        """Regression companion: a line with no rule violation must
        still confirm normally — the guard must not over-block."""
        order = self._make_order(
            "Base Cabinet - Contractor Series, White Melamine Box",
            variant=self.variant_clean)
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_hard_validation_issues_method_shape(self):
        order = self._make_order(
            "Base Cabinet - Contractor Series, Maple Box",
            variant=self.variant_maple_contractor)
        issues = order.southbrook_hard_validation_issues()
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["code"], "box_series_incompatible")
        self.assertEqual(issue["line_id"], order.order_line.id)
        self.assertIn("Maple", issue["message"])

    def test_hard_validation_issues_empty_for_clean_order(self):
        order = self._make_order("Base Cabinet - Standard")
        self.assertEqual(order.southbrook_hard_validation_issues(), [])
