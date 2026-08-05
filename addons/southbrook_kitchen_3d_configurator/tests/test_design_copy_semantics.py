# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-061 — what a duplicate design must and must not inherit.

Three copy defects, all found by E2E reviewers duplicating a quoted design:

* the copy stayed BOUND to the original's issued quotation
  (sale_order_id was not in the copy blacklist), so Create & Confirm on
  the copy would write into a document already in front of a customer;
* the stock Duplicate menu produced a copy born "quoted" with no quote
  of its own (state was only reset by the one custom button);
* derived corner lines kept ``corner-<OLD id>-*`` layout keys, and the
  corner-clearance validator parses the design id out of that key to
  exempt a corner's own run-neighbour — so a byte-identical copy
  reported four phantom BLOCKERS the original never showed.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "design_copy")
class TestDesignCopySemantics(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Copy Test Customer"})
        tmpl = cls.env["product.template"].create({
            "name": "Copy Test Base Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 400.0,
        })
        tmpl.write({
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 24.0,
        })
        cls.variant = tmpl.product_variant_id

        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Copy Semantics Original",
            "partner_id": cls.partner.id,
            "room_width_in": 120.0,
            "room_depth_in": 96.0,
        })
        cls.line = cls.env["southbrook.kitchen.design.line"].create({
            "design_id":    cls.design.id,
            "product_id":   cls.variant.id,
            "quantity":     1,
            "price_unit":   400.0,
            "cabinet_type": "base",
            "origin":       "configurator",
            "wall":         "back",
            "width_in":     24.0,
            "height_in":    34.5,
            "depth_in":     24.0,
            "layout_key":   "corner-%d-back-left-base" % cls.design.id,
        })
        order = cls.env["sale.order"].create({"partner_id": cls.partner.id})
        cls.design.write({"state": "quoted"})
        # sale_order_id is readonly on the form, not in code
        cls.design.sudo().sale_order_id = order.id

    def test_01_copy_sheds_the_quotation_link_and_resets_state(self):
        copy = self.design.copy()
        self.assertFalse(
            copy.sale_order_id,
            "a duplicate must never stay bound to the original's issued "
            "quotation — Create & Confirm on it would write into a document "
            "already in front of a customer")
        self.assertEqual(
            copy.state, "draft",
            "any copy path yields a new draft, not a phantom 'quoted' "
            "design with no quote of its own")
        # The original is untouched.
        self.assertTrue(self.design.sale_order_id)
        self.assertEqual(self.design.state, "quoted")

    def test_02_id_bearing_layout_keys_follow_the_copy(self):
        copy = self.design.copy()
        keys = copy.with_context(active_test=False).cabinet_line_ids.mapped(
            "layout_key")
        stale = [k for k in keys if k and ("-%d-" % self.design.id) in k]
        self.assertFalse(
            stale,
            f"these copied lines still carry the ORIGINAL design's id in "
            f"their layout key, which silently disables the corner "
            f"validator's neighbour exemption on the copy: {stale}")
        expected = "corner-%d-back-left-base" % copy.id
        self.assertIn(
            expected, keys,
            "the key must be rewritten to the copy's own id, never blanked "
            "— reconcile's idempotency matching depends on keys existing")

    def test_03_non_id_keys_are_left_verbatim(self):
        self.line.layout_key = "tpl-U-10X8X10-SINK-11"
        copy = self.design.copy()
        self.assertIn(
            "tpl-U-10X8X10-SINK-11",
            copy.with_context(active_test=False).cabinet_line_ids.mapped(
                "layout_key"),
            "template-slot keys carry no design id and must copy unchanged")
