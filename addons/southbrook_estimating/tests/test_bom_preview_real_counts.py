# SPDX-License-Identifier: LGPL-3.0-only
"""H-6 / H-9 (2026-08-05) — the BoM Preview must report the real BOM.

sb_bom_preview_html used to render sale.order.line.sb_panel_count /
sb_door_count — a SYNTHETIC southbrook_dims.panel_cut_list geometry
formula that never consulted the resolved mrp.bom at all. Every line
showed a fabricated "8 Panels / 1 Door" figure, including a line whose
real BOM has zero components (an appliance-space void). This covers the
fixed preview: a real component count when a BOM resolves with lines,
an honest dash for a genuine data gap (no BOM, or an empty BOM), and
"Not manufactured" for appliance-space / filler / decorative-panel
lines — never a fabricated number in any case.

sb_panel_count/sb_door_count themselves are untouched by this fix (see
sale_order.py's _SB_NOT_MANUFACTURED_CABINET_TYPES comment for the list
of other real consumers) — only what this preview renders changed.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "bom_preview")
class TestBomPreviewRealCounts(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "BoM Preview Test Customer"})

    def _make_product(self, name):
        return self.env["product.product"].create({
            "name": name, "type": "consu", "list_price": 100.0,
        })

    def test_resolved_bom_shows_real_component_count_not_synthetic(self):
        product = self._make_product("BoM Preview Real Cabinet")
        component = self._make_product("BoM Preview Raw Component")
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id,
            "product_id": False,
            "code": "CatalogAutoBOM-Variant-TEST",
            "bom_line_ids": [
                (0, 0, {"product_id": component.id, "product_qty": 3}),
                (0, 0, {"product_id": component.id, "product_qty": 2}),
            ],
        })
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": product.id, "product_uom_qty": 1,
                "name": "Real BoM line",
            })],
        })
        line = order.order_line[:1]
        self.assertTrue(line.sb_bom_id, "sanity: BOM must resolve")
        self.assertEqual(line.sb_bom_id.id, bom.id)

        preview_html = order.sb_bom_preview_html
        self.assertIn("2 components", preview_html)
        # Never the fabricated synthetic figures this fix removes.
        self.assertNotIn("Panels", preview_html)
        self.assertNotIn("Doors", preview_html)
        # Never the raw generator-prefixed code — shop-facing label only.
        self.assertNotIn("CatalogAutoBOM-Variant-TEST", preview_html)
        self.assertIn("BoM Preview Real Cabinet", preview_html)

    def test_no_bom_shows_dash_not_fabricated_count(self):
        product = self._make_product("BoM Preview No-BoM Cabinet")
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": product.id, "product_uom_qty": 1,
                "name": "No BoM line",
            })],
        })
        line = order.order_line[:1]
        self.assertFalse(line.sb_bom_id)
        preview_html = order.sb_bom_preview_html
        self.assertIn("no BoM defined yet", preview_html)
        self.assertIn("&mdash;", preview_html)

    def test_empty_bom_shows_dash_not_fabricated_count(self):
        product = self._make_product("BoM Preview Empty-BoM Cabinet")
        self.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id,
            "product_id": False,
        })
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": product.id, "product_uom_qty": 1,
                "name": "Empty BoM line",
            })],
        })
        line = order.order_line[:1]
        self.assertTrue(
            line.sb_bom_id, "sanity: the empty BOM shell must still "
            "resolve as the line's BOM")
        self.assertFalse(line.sb_bom_id.bom_line_ids)
        preview_html = order.sb_bom_preview_html
        self.assertIn("no BoM defined yet", preview_html)
        self.assertIn("&mdash;", preview_html)

    def _has_cabinet_type_field(self):
        return (
            "southbrook_cabinet_type"
            in self.env["product.template"]._fields
        )

    def test_appliance_void_shows_not_manufactured(self):
        if not self._has_cabinet_type_field():
            self.skipTest(
                "southbrook_kitchen_3d_configurator not installed in "
                "this test run — southbrook_cabinet_type field absent")
        tmpl = self.env["product.template"].create({
            "name": "BoM Preview Appliance Void",
            "southbrook_cabinet_type": "appliance",
            "config_ok": False, "sale_ok": True,
        })
        variant = tmpl.product_variant_ids[:1]
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": variant.id, "product_uom_qty": 1,
                "name": "Appliance space line",
            })],
        })
        preview_html = order.sb_bom_preview_html
        self.assertIn("Not manufactured", preview_html)
        self.assertNotIn("Panels", preview_html)
        self.assertNotIn("Doors", preview_html)

    def test_row_number_is_running_index_not_sequence(self):
        """The "#" column must enumerate rows, not echo line.sequence
        (which was a constant 10 on every row before this fix)."""
        p1 = self._make_product("BoM Preview Row A")
        p2 = self._make_product("BoM Preview Row B")
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [
                (0, 0, {"product_id": p1.id, "product_uom_qty": 1,
                         "name": "Row A", "sequence": 10}),
                (0, 0, {"product_id": p2.id, "product_uom_qty": 1,
                         "name": "Row B", "sequence": 10}),
            ],
        })
        preview_html = order.sb_bom_preview_html
        self.assertIn("<td>1</td>", preview_html)
        self.assertIn("<td>2</td>", preview_html)
