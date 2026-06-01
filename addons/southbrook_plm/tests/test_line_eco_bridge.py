# SPDX-License-Identifier: LGPL-3.0-only
"""sale.order.line → ECO bridge tests (Step 4).

Proves:
  - action_raise_eco returns an ir.actions.act_window
  - The action's context pre-fills eco_type_id (bom-kind), bom_id
    (active BoM for this line's template), title, and description
  - Lines with no associated BoM get the action without bom_id (the
    engineer scopes it after opening)

See docs/CUSTOMER_TO_MANUFACTURING_FLOW.md §3.3.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLineEcoBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Order = cls.env["sale.order"]
        cls.Eco = cls.env["southbrook.eco"]
        cls.Bom = cls.env["mrp.bom"]
        cls.Partner = cls.env["res.partner"]
        cls.Product = cls.env["product.product"]
        cls.type_bom = cls.env.ref("southbrook_plm.eco_type_bom")

        cls.partner = cls.Partner.create({"name": "Bridge Test Partner"})
        cls.product = cls.Product.create(
            {"name": "Bridge Test Cabinet", "type": "consu"}
        )
        cls.bom = cls.Bom.create(
            {
                "product_tmpl_id": cls.product.product_tmpl_id.id,
                "product_qty": 1.0,
            }
        )

    def _make_order(self, with_bom=True):
        product = self.product if with_bom else self.Product.create(
            {"name": "No-BoM Product", "type": "consu"}
        )
        return self.Order.create(
            {
                "partner_id": self.partner.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "product_uom_qty": 2.0,
                            "price_unit": 250.0,
                        },
                    ),
                ],
            }
        )

    def test_action_shape(self):
        """The action is an act_window pointing at southbrook.eco."""
        order = self._make_order()
        action = order.order_line.action_raise_eco()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "southbrook.eco")
        self.assertEqual(action["view_mode"], "form")
        self.assertEqual(action["target"], "current")

    def test_action_prefills_bom_kind_eco_type(self):
        """default_eco_type_id is the unique bom-kind ECO type."""
        order = self._make_order()
        action = order.order_line.action_raise_eco()
        self.assertEqual(
            action["context"]["default_eco_type_id"], self.type_bom.id
        )

    def test_action_prefills_bom_id_when_bom_exists(self):
        """default_bom_id is the active BoM for the line's template."""
        order = self._make_order()
        action = order.order_line.action_raise_eco()
        self.assertEqual(action["context"]["default_bom_id"], self.bom.id)

    def test_action_omits_bom_id_when_no_bom(self):
        """A line whose template has no BoM yields default_bom_id=False —
        the engineer scopes the ECO after opening.
        """
        order = self._make_order(with_bom=False)
        action = order.order_line.action_raise_eco()
        self.assertFalse(action["context"]["default_bom_id"])
        # Other defaults still present.
        self.assertEqual(
            action["context"]["default_eco_type_id"], self.type_bom.id
        )

    def test_action_title_cites_order_and_product(self):
        """The auto-title weaves the order ref + product display name
        so the engineer sees the source at a glance in the Kanban card.
        """
        order = self._make_order()
        action = order.order_line.action_raise_eco()
        title = action["context"]["default_title"]
        self.assertIn(order.name, title)
        self.assertIn(self.product.display_name, title)

    def test_action_description_includes_qty(self):
        """The auto-description carries enough context for the engineer
        to scope the change without re-keying.
        """
        order = self._make_order()
        action = order.order_line.action_raise_eco()
        desc = action["context"]["default_description"]
        self.assertIn(order.name, desc)
        self.assertIn(self.product.display_name, desc)
        self.assertIn("2.0", desc)
