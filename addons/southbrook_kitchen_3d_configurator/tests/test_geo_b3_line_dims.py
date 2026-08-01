# SPDX-License-Identifier: LGPL-3.0-only
"""Task B3 (Materials geometry-writeback plan, Increment B) — thread the
3D-configurator's per-placement cabinet dimensions through to the
material BoM line, so drag-resized/filler cabinets weigh correctly.

Two seams are proven here, matching the boundary documented in
docs/sdd-briefs/geo-B3-report.md:

  1. design.line -> sale.order.line (FULLY wired, real end-to-end):
     `action_create_quotation` carries a design line's real per-instance
     width_in/height_in/depth_in (including drag-resize/filler
     overrides) onto the created SO line's new sb_line_width_mm/
     height_mm/depth_mm fields, ×25.4, rounded to the nearest int.

  2. sale.order.line -> mrp.bom.line (the narrow, direct mapping
     primitive, `_sb_apply_dims_to_bom_line`): tested directly against
     real records, NOT through the full quote/BoM-autoseed chain. See
     that method's docstring (models/sale_order_line.py) and the B3
     report for why `kitchen_design._ensure_kitchen_bom`'s existing
     BoM-autoseed path is a genuinely unsafe place to wire this
     automatically — it resolves ONE `mrp.bom` per product template,
     shared across every design/order that reuses that template
     (most visibly the filler-panel SKU, reused across many rooms with
     a DIFFERENT remainder width each time), so a naive auto-wire would
     make one order's cut width silently win for every other order's
     filler. That is a manual-verification boundary, not a gap in this
     module's own dims-threading contract.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "sb_geo")
class TestGeoB3LineDims(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.Template = cls.env["product.template"]
        cls.Partner = cls.env["res.partner"]

        cls.partner = cls.Partner.create({
            "name": "Geo-B3 Test Customer",
            "email": "geo.b3@southbrook.test",
        })

        cls.tmpl_resizable = cls.Template.create({
            "name":       "Geo-B3 Resizable Base",
            "type":       "consu",
            "sale_ok":    True,
            "list_price": 80.0,
        })
        cls.tmpl_resizable.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in":     24.0,  # nominal/default template width
            "southbrook_height_in":    34.5,
            "southbrook_depth_in":     24.0,
        })

    def _make_design(self):
        return self.Design.create({
            "name":           "Geo-B3 Test Design",
            "partner_id":     self.partner.id,
            "room_width_in":  120,
            "room_depth_in":  96,
            "room_height_in": 96,
        })

    # ------------------------------------------------------------------
    # 1) design.line -> sale.order.line (full E2E through
    #    action_create_quotation)
    # ------------------------------------------------------------------
    def test_action_create_quotation_carries_drag_resized_dims_to_so_line(self):
        """RED before this task: action_create_quotation dropped width_in/
        height_in/depth_in entirely (kitchen_design.py:1490-1496). GREEN
        after: a base-cabinet line whose width_in was overridden at
        placement time (e.g. a 7.25" drag-resize, NOT the template's 24"
        nominal default) produces an SO line with
        sb_line_width_mm == round(7.25 * 25.4), proving the OVERRIDE
        reached the SO line, not the template default.
        """
        design = self._make_design()
        overridden_width_in = 7.25
        line = self.Line.create({
            "design_id":      design.id,
            "product_id":     self.tmpl_resizable.product_variant_id.id,
            "quantity":       1,
            "price_unit":     self.tmpl_resizable.list_price,
            "cabinet_type":   "base",
            "width_in":       overridden_width_in,
            "height_in":      34.5,
            "depth_in":       24.0,
            "x_position_in":  0,
            "y_position_in":  0,
            "z_position_in":  0,
        })
        self.assertNotEqual(
            line.width_in, self.tmpl_resizable.southbrook_width_in,
            "test precondition: the line's width must genuinely diverge "
            "from the template's nominal default, or this test can't "
            "distinguish 'override carried through' from 'template "
            "default leaked through'",
        )

        design.action_create_quotation()

        self.assertTrue(design.sale_order_id, "quotation must be created")
        so_line = design.sale_order_id.order_line.filtered(
            lambda l: l.product_id == line.product_id
        )
        self.assertEqual(len(so_line), 1)
        self.assertEqual(
            so_line.sb_line_width_mm, round(overridden_width_in * 25.4),
        )
        self.assertEqual(so_line.sb_line_height_mm, round(34.5 * 25.4))
        self.assertEqual(so_line.sb_line_depth_mm, round(24.0 * 25.4))
        # And explicitly NOT the template's nominal default (regression
        # pin against "silently falls back to product_tmpl_id.southbrook_
        # width_in" — the exact gap this task closes).
        self.assertNotEqual(
            so_line.sb_line_width_mm,
            round(self.tmpl_resizable.southbrook_width_in * 25.4),
        )

    def test_design_line_dims_mm_soft_guard(self):
        """_sb_dims_mm() is all-or-nothing: {} unless width_in, height_in,
        AND depth_in are all truthy — never a partial/garbage override.
        """
        design = self._make_design()
        line = self.Line.create({
            "design_id":      design.id,
            "product_id":     self.tmpl_resizable.product_variant_id.id,
            "quantity":       1,
            "price_unit":     self.tmpl_resizable.list_price,
            "cabinet_type":   "base",
            "width_in":       0.0,
            "height_in":      34.5,
            "depth_in":       24.0,
            "x_position_in":  0,
            "y_position_in":  0,
            "z_position_in":  0,
        })
        self.assertEqual(line._sb_dims_mm(), {})

        line.write({"width_in": 7.25})
        dims = line._sb_dims_mm()
        self.assertEqual(dims, {
            "sb_line_width_mm":  round(7.25 * 25.4),
            "sb_line_height_mm": round(34.5 * 25.4),
            "sb_line_depth_mm":  round(24.0 * 25.4),
        })

    # ------------------------------------------------------------------
    # 2) sale.order.line -> mrp.bom.line (the narrow, direct seam —
    #    real records, not the full autoseed chain; see module docstring)
    # ------------------------------------------------------------------
    def test_apply_dims_to_bom_line_narrow_seam(self):
        """`sale.order.line._sb_apply_dims_to_bom_line` copies this SO
        line's captured sb_line_* mm dims onto a SPECIFIC, caller-
        supplied mrp.bom.line — proven directly with real
        sale.order.line + mrp.bom.line records. Skips (does not fail)
        when sb_material_mrp isn't installed in this test DB, since
        `mrp.bom.line.sb_line_width_mm` is defined there, not in this
        module (no hard manifest dependency between the two).
        """
        BomLine = self.env["mrp.bom.line"]
        if "sb_line_width_mm" not in BomLine._fields:
            self.skipTest(
                "sb_material_mrp not installed in this test DB -- "
                "mrp.bom.line.sb_line_width_mm unavailable"
            )

        design = self._make_design()
        overridden_width_in = 7.25
        line = self.Line.create({
            "design_id":      design.id,
            "product_id":     self.tmpl_resizable.product_variant_id.id,
            "quantity":       1,
            "price_unit":     self.tmpl_resizable.list_price,
            "cabinet_type":   "base",
            "width_in":       overridden_width_in,
            "height_in":      34.5,
            "depth_in":       24.0,
            "x_position_in":  0,
            "y_position_in":  0,
            "z_position_in":  0,
        })
        design.action_create_quotation()
        so_line = design.sale_order_id.order_line.filtered(
            lambda l: l.product_id == line.product_id
        )
        self.assertEqual(len(so_line), 1)

        # A real mrp.bom.line, deliberately NOT drawn from
        # kitchen_design._ensure_kitchen_bom's shared autoseed BOM (see
        # module docstring for why that source would be unsafe) —
        # a standalone bom + component the caller owns outright.
        component = self.env["product.product"].create({
            "name": "Geo-B3 Raw Material", "type": "consu",
        })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": component.product_tmpl_id.id,
        })
        bom_line = BomLine.create({
            "bom_id": bom.id, "product_id": component.id, "product_qty": 1,
        })
        self.assertEqual(bom_line.sb_line_width_mm, 0)

        so_line._sb_apply_dims_to_bom_line(bom_line)

        self.assertEqual(
            bom_line.sb_line_width_mm, round(overridden_width_in * 25.4),
        )
        self.assertEqual(bom_line.sb_line_height_mm, round(34.5 * 25.4))
        self.assertEqual(bom_line.sb_line_depth_mm, round(24.0 * 25.4))

    def test_apply_dims_to_bom_line_soft_guard_no_dims(self):
        """When the SO line carries no captured override (e.g. a manual/
        backend line never touched by the 3D configurator), applying to
        a bom.line must be a no-op, not a zeroing write."""
        BomLine = self.env["mrp.bom.line"]
        if "sb_line_width_mm" not in BomLine._fields:
            self.skipTest(
                "sb_material_mrp not installed in this test DB -- "
                "mrp.bom.line.sb_line_width_mm unavailable"
            )

        order = self.env["sale.order"].create({"partner_id": self.partner.id})
        so_line = self.env["sale.order.line"].create({
            "order_id":   order.id,
            "product_id": self.tmpl_resizable.product_variant_id.id,
            "product_uom_qty": 1,
        })
        self.assertEqual(so_line.sb_line_width_mm, 0)

        component = self.env["product.product"].create({
            "name": "Geo-B3 Raw Material (guard)", "type": "consu",
        })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": component.product_tmpl_id.id,
        })
        bom_line = BomLine.create({
            "bom_id": bom.id, "product_id": component.id, "product_qty": 1,
            "sb_line_width_mm": 111, "sb_line_height_mm": 222,
            "sb_line_depth_mm": 333,
        })

        so_line._sb_apply_dims_to_bom_line(bom_line)

        # Untouched -- the SO line had no real override to apply.
        self.assertEqual(bom_line.sb_line_width_mm, 111)
        self.assertEqual(bom_line.sb_line_height_mm, 222)
        self.assertEqual(bom_line.sb_line_depth_mm, 333)
