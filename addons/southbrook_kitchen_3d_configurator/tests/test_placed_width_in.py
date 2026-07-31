# SPDX-License-Identifier: LGPL-3.0-only
"""Task 11 (corner-remediation 2026-07-30, SBK-024) — `placed_width_in()`.

Required reading: .superpowers/sdd/corner-verify/V5-partc.md §SBK-024.

`southbrook_width_in` (models/product_template.py) is a single TEMPLATE-
level Float — one number shared by every variant of that template. Every
placement/geometry call site in this addon used to read it unconditionally,
so a slot resolving to e.g. a 24in-Width variant of a 30in-nominal template
still placed at the template's nominal 30in (the live "SINK slot 30in
resolves to SB-BASE-2DR-24" evidence the previously-skipped
test_invariant_matrix.py::test_placed_width_matches_variant_width_attribute_
where_present carried).

This module pins `kitchen_design.placed_width_in()` directly against a
SYNTHETIC two-width cabinet family — decoupled from any incidental catalog
data drift on whichever DB runs the suite, so these fail under the pre-fix
`tmpl.southbrook_width_in`-only behavior regardless of what the real
catalog looks like on any given DB (same rationale as
test_corner_price_resolution.py's synthetic corner-family fixture) — plus
one placement-call-site integration smoke test
(`_create_line`, one of the ~9 wired call sites) proving the fix reaches an
actual persisted design line, not just the helper in isolation.
"""
import logging

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.models import kitchen_design as _kd
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    placed_width_in,
)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator")
class TestPlacedWidthIn(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.width_attr = cls.env["product.attribute"].create({
            "name": "Width", "create_variant": "always",
        })
        cls.val_18 = cls.env["product.attribute.value"].create({
            "name": "18 in", "attribute_id": cls.width_attr.id,
            "value_inches": 18.0, "value_mm": 457,
        })
        cls.val_24 = cls.env["product.attribute.value"].create({
            "name": "24 in", "attribute_id": cls.width_attr.id,
            "value_inches": 24.0, "value_mm": 609,
        })
        cls.tmpl = cls.env["product.template"].create({
            "name": "T11 Test Multi-Width Base", "type": "consu",
            "list_price": 300.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            # Deliberately a THIRD number, different from BOTH variant
            # widths below — proves the fix reads the variant, not
            # something merely "close enough" to it. If placed_width_in
            # ever regressed to reading this template field instead,
            # every assertion below fails loudly (30 != 18, 30 != 24).
            "southbrook_width_in": 30.0,
            "southbrook_height_in": 34.5,
            "southbrook_depth_in": 24.0,
        })
        attr_line = cls.env["product.template.attribute.line"].create({
            "product_tmpl_id": cls.tmpl.id,
            "attribute_id": cls.width_attr.id,
            "value_ids": [(6, 0, [cls.val_18.id, cls.val_24.id])],
        })
        ptavs = attr_line.product_template_value_ids
        ptav_18 = ptavs.filtered(
            lambda p: p.product_attribute_value_id == cls.val_18)
        ptav_24 = ptavs.filtered(
            lambda p: p.product_attribute_value_id == cls.val_24)
        variants = cls.tmpl.product_variant_ids
        cls.variant_18 = variants.filtered(
            lambda v: ptav_18 in v.product_template_attribute_value_ids)
        cls.variant_24 = variants.filtered(
            lambda v: ptav_24 in v.product_template_attribute_value_ids)
        cls.variant_18.default_code = "T11-BASE-18"
        cls.variant_24.default_code = "T11-BASE-24"

        # A product with NO Width attribute at all -- pure template-
        # level fallback case (e.g. a filler/decorative panel).
        cls.tmpl_no_width = cls.env["product.template"].create({
            "name": "T11 Test No-Width Panel", "type": "consu",
            "list_price": 50.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "panel",
            "southbrook_width_in": 12.0,
        })
        cls.variant_no_width = cls.tmpl_no_width.product_variant_id

    def test_variant_with_width_ptav_resolves_to_its_own_width(self):
        self.assertEqual(placed_width_in(self.variant_18), 18.0)
        self.assertEqual(placed_width_in(self.variant_24), 24.0)

    def test_multi_width_variants_of_same_template_resolve_independently(self):
        # AC: "an 18-in variant of a multi-width template places at 18"
        # -- both variants share ONE template (southbrook_width_in=30),
        # yet resolve to their OWN, different widths, neither of which
        # is the template's nominal 30.
        w18 = placed_width_in(self.variant_18)
        w24 = placed_width_in(self.variant_24)
        self.assertEqual(w18, 18.0)
        self.assertEqual(w24, 24.0)
        self.assertNotEqual(w18, w24)
        self.assertNotEqual(w18, self.tmpl.southbrook_width_in)
        self.assertNotEqual(w24, self.tmpl.southbrook_width_in)

    def test_no_width_ptav_falls_back_to_template_width(self):
        self.assertEqual(placed_width_in(self.variant_no_width), 12.0)

    def test_empty_recordset_returns_zero(self):
        empty = self.env["product.product"]
        self.assertEqual(placed_width_in(empty), 0.0)

    def test_fallback_logs_once_per_product(self):
        # A DIFFERENT no-width product than setUpClass's shared fixture
        # -- the "logged once per product id" state is a module-level
        # set that persists across test methods within the same worker
        # process, so re-using cls.variant_no_width here could
        # legitimately see zero new log records if an earlier test
        # already triggered its fallback.
        tmpl = self.env["product.template"].create({
            "name": "T11 Test Fallback-Log Panel", "type": "consu",
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "panel",
            "southbrook_width_in": 6.0,
        })
        variant = tmpl.product_variant_id
        captured = []

        # A hand-rolled handler rather than assertLogs(): Odoo's own
        # logging setup interferes with unittest's assertLogs handler
        # swap in this test runner (empirically: assertLogs reports
        # "no logs triggered" even though the fallback branch and its
        # `_logger.info(...)` call demonstrably execute — confirmed by
        # checking `_WIDTH_FALLBACK_LOGGED_IDS` membership directly).
        # Attaching a plain handler straight to the module's own
        # `_logger` sidesteps whatever that interaction is.
        class _Cap(logging.Handler):
            def emit(self, record):
                captured.append(record)

        h = _Cap()
        _kd._logger.addHandler(h)
        try:
            placed_width_in(variant)
            placed_width_in(variant)
            placed_width_in(variant)
        finally:
            _kd._logger.removeHandler(h)
        fallback_records = [
            r for r in captured if "placed_width_in" in r.getMessage()]
        self.assertEqual(
            len(fallback_records), 1,
            "fallback should log exactly once per product id regardless "
            "of call count, got: %s" % fallback_records)

    def test_placement_call_site_places_variant_at_its_own_width(self):
        """Integration smoke test: `_create_line` (one of the ~9 wired
        placement call sites) writes the VARIANT's own width onto a real
        persisted design line, not the shared template default."""
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T11 placement smoke test",
            "room_width_in": 96.0, "room_depth_in": 96.0,
            "room_height_in": 96.0,
        })
        line_18 = design._create_line(self.variant_18, 10, 0.0, 0.0, 0.0)
        line_24 = design._create_line(self.variant_24, 20, 18.0, 0.0, 0.0)
        self.assertEqual(line_18.width_in, 18.0)
        self.assertEqual(line_24.width_in, 24.0)
        # An explicit override (the filler-remainder use case) still
        # wins over even the variant's own width.
        line_override = design._create_line(
            self.variant_18, 30, 42.0, 0.0, 0.0, override_width=5.5)
        self.assertEqual(line_override.width_in, 5.5)
