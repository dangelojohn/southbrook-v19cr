# SPDX-License-Identifier: LGPL-3.0-only
"""QA Bug 3 (2026-07-04): the backend sale.order form's inline "3D
Kitchen Preview" tab (get_kitchen_3d_payload) rendered a blank canvas
for orders whose lines were fully configured, priced cabinets.

Root cause: get_kitchen_3d_payload() resolved cabinet geometry purely
from a hardcoded 12-entry SKU lookup table (product_config_line.py
_SKU_DEFAULTS), with no fallback — any line whose product_tmpl_id.
default_code wasn't one of those exact strings was silently skipped
("continue"). Any order made of cabinets from the SEPARATE full-screen
"Open in 3D" configurator's catalog (southbrook_kitchen_3d_configurator,
flagged product.template.southbrook_is_cabinet=True, its own SKU codes)
had every line skipped, leaving an empty scene — even though the lines
were entirely valid and priced. This is a known limitation documented
in southbrook_elearning_internal's own training material.

Fix: fall back to a generic box (mirroring the same "hard defaults when
no SKU match" pattern product_config_line._extract_cabinet_inputs()
already uses for the single-cabinet wizard preview) for any line that's
plausibly a cabinet (southbrook_is_cabinet=True) but isn't one of the
12 hardcoded SB-* SKUs — while still skipping section/note lines and
genuinely non-cabinet products (delivery fees, etc.).
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "kitchen_3d_payload")
class TestKitchen3dPayloadOrder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "QA Bug3 Test Customer"})
        cls.sb_tmpl = cls.env.ref("southbrook_estimating.base_1dr")

    def _has_is_cabinet_field(self):
        return "southbrook_is_cabinet" in self.env["product.template"]._fields

    def test_recognized_sb_sku_renders_panels(self):
        """Baseline: an order line on one of the 12 hardcoded SB-* SKUs
        must still render panels exactly as before this fix."""
        variant = self.sb_tmpl.product_variant_ids[:1]
        if not variant:
            self.skipTest("base_1dr has no variant available")
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": variant.id,
                "product_uom_qty": 1,
            })],
        })
        payload = order.get_kitchen_3d_payload()
        self.assertTrue(payload.get("panels"),
                         "recognized SB SKU must still render panels")

    def test_other_catalog_cabinet_falls_back_to_generic_box(self):
        """Regression: a product NOT in _SKU_DEFAULTS but flagged
        southbrook_is_cabinet=True (the other configurator's catalog)
        must render a generic box, not be silently skipped."""
        if not self._has_is_cabinet_field():
            self.skipTest(
                "southbrook_kitchen_3d_configurator not installed in "
                "this test run — southbrook_is_cabinet field absent")
        other_tmpl = self.env["product.template"].create({
            "name": "QA Bug3 Other-Catalog Cabinet",
            "default_code": "B24",  # NOT in _SKU_DEFAULTS
            "southbrook_is_cabinet": True,
            "config_ok": False,
            "sale_ok": True,
        })
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": other_tmpl.product_variant_ids[:1].id,
                "product_uom_qty": 1,
            })],
        })
        payload = order.get_kitchen_3d_payload()
        self.assertTrue(
            payload.get("panels"),
            "a southbrook_is_cabinet=True product outside _SKU_DEFAULTS "
            "must still render a generic-box fallback, not be skipped")

    def test_post_repair_multivariant_template_resolves_real_sku_not_generic_box(self):
        """Task 23 (SBK-037) review finding 1, item 1 (second consumer)
        — get_kitchen_3d_payload() (this order-aggregate scene; the
        backend Order Builder 3D tab AND the website kitchen_viewport
        both consume it) used to read product_tmpl_id.default_code
        directly. Once repair_cabinet_variant_refs() splits a
        duplicate onto a genuinely multi-variant template (e.g. real-
        world "SB-WALL-2DR" -> "SB-WALL-2DR-24"/"SB-WALL-2DR-30"),
        product.template.default_code correctly reads blank (Odoo
        only mirrors a single variant) — a naive read fell straight
        through to the generic base/1-door/609mm box, silently wrong
        for a wall/2-door cabinet. Must resolve the REAL SB-WALL-2DR
        row via Session._resolve_template_sku(tmpl)."""
        Attribute = self.env["product.attribute"]
        Value = self.env["product.attribute.value"]
        AttrLine = self.env["product.template.attribute.line"]
        Product = self.env["product.product"]
        PTAV = self.env["product.template.attribute.value"]

        # create_variant='dynamic' — this catalogue's real convention
        # (data/attributes.xml: "Per Q6: all create_variant='dynamic'")
        # — never auto-materializes variants; created explicitly below,
        # exactly as corner_sku_repair.py's own repair does.
        attr = Attribute.create({
            "name": "TestAttr_T23SaleOrderMultiVariant",
            "create_variant": "dynamic",
        })
        val_a = Value.create({"name": "A", "attribute_id": attr.id})
        val_b = Value.create({"name": "B", "attribute_id": attr.id})
        tmpl = self.env["product.template"].create({
            "name": "Post-Repair Multi-Variant Wall 2-Door (sale.order)",
            "config_ok": True,
            "sale_ok": True,
            # Deliberately no default_code — constructs the post-
            # repair END STATE directly (two coded variants, blank
            # template field), same as the product_config_line.py
            # regression test.
        })
        AttrLine.create({
            "product_tmpl_id": tmpl.id,
            "attribute_id": attr.id,
            "value_ids": [(6, 0, (val_a | val_b).ids)],
        })
        ptav_a = PTAV.search([
            ("product_tmpl_id", "=", tmpl.id),
            ("product_attribute_value_id", "=", val_a.id),
        ], limit=1)
        ptav_b = PTAV.search([
            ("product_tmpl_id", "=", tmpl.id),
            ("product_attribute_value_id", "=", val_b.id),
        ], limit=1)
        variant_24 = Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav_a.id])],
            "default_code": "SB-WALL-2DR-24",
        })
        Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav_b.id])],
            "default_code": "SB-WALL-2DR-30",
        })
        tmpl.invalidate_recordset(["default_code"])
        self.assertFalse(
            tmpl.default_code,
            "sanity: a genuinely multi-variant template's default_code "
            "must read blank — the exact condition this fix guards "
            "against, not a fixture artifact")

        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": variant_24.id,
                "product_uom_qty": 1,
            })],
        })
        line = order.order_line[:1]
        payload = order.get_kitchen_3d_payload()

        Session = self.env["product.config.session"]
        expected = Session._SKU_DEFAULTS["SB-WALL-2DR"]
        fam, doors, drawers, w, h, d = expected

        self.assertTrue(
            payload.get("panels"),
            "must render real panels, not an empty/skipped scene")
        line_meta = payload["metadata"]["lines"].get(str(line.id))
        self.assertTrue(
            line_meta,
            "the line must appear in the per-line metadata index")
        self.assertEqual(
            line_meta["family"], fam,
            "must resolve the real SB-WALL-2DR row via "
            "_resolve_template_sku(tmpl), not silently fall through "
            "to the generic base/1-door box")
        self.assertEqual(line_meta["width_mm"], w)
        self.assertEqual(line_meta["height_mm"], h)
        self.assertEqual(line_meta["depth_mm"], d)
        self.assertNotEqual(
            (line_meta["width_mm"], line_meta["height_mm"],
             line_meta["depth_mm"]),
            (609, 762, 609),
            "must not silently render the generic base/1-door box "
            "dimensions for a real, recognized wall/2-door SKU")
        self.assertEqual(
            payload["metadata"]["line_count"], 1,
            "line_count must also resolve via the variant-code "
            "fallback, not undercount a post-repair multi-variant "
            "template's recognized line")

    def test_non_cabinet_product_still_skipped(self):
        """Regression companion: an ordinary non-cabinet product (no
        SB-* SKU match, not flagged southbrook_is_cabinet) must NOT
        get a generic box rendered for it — only recognized/cabinet-
        like lines should ever appear in the 3D scene."""
        plain_tmpl = self.env["product.template"].create({
            "name": "QA Bug3 Plain Non-Cabinet Product",
            "default_code": "DELIVERY-FEE",
            "config_ok": False,
            "sale_ok": True,
        })
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": plain_tmpl.product_variant_ids[:1].id,
                "product_uom_qty": 1,
            })],
        })
        payload = order.get_kitchen_3d_payload()
        self.assertFalse(
            payload.get("panels"),
            "a plain non-cabinet product must not render a generic box")

    def test_section_line_still_skipped(self):
        """Section/note lines (display_type set, no product) must never
        be treated as a cabinet-shaped fallback candidate."""
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "display_type": "line_section",
                "name": "QA Bug3 Section Header",
            })],
        })
        payload = order.get_kitchen_3d_payload()
        self.assertFalse(payload.get("panels"))
