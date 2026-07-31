# SPDX-License-Identifier: LGPL-3.0-only
"""Prod-audit follow-up (2026-07-31) — archive_zero_variant_duplicate_
templates() in models/corner_sku_repair.py.

Ground truth (live prod audit, 2026-07-31): four debris product.template
rows sit alongside their real, canonical twins — SB-BASE-1DR (debris id
1094 vs. real id 38, 8 variants), SB-BASE-2DR (1095 vs. 39, 10
variants), SB-WALL-1DR (1097 vs. 36, 2 variants), SB-WALL-CORNER (1098
vs. 236, 2 variants). Every debris row carries a non-empty TEMPLATE-
level default_code, ZERO product.product variants, ZERO
southbrook.kitchen.design.line / sale.order.line / mrp.bom references,
and no xml_id — minted by an earlier buggy `catalog_expansion.
build_catalog()` pass (fixed at HEAD, variant-first resolution) that
created a fresh template because its existence check searched for the
code in the wrong place.

The sibling sweep `archive_catalog_family_duplicate_templates()` (see
test_catalog_family_duplicate_archive.py) cannot see any of these: it
enumerates duplicates VIA VARIANTS (`Product.search` by default_code,
deriving template ids from the matches), so a template with ZERO
variants of its own never contributes a row to that search — and it
only ever looks at `_CORNER_FAMILY_BASE_CODES` in the first place, so
SB-BASE-1DR/-2DR/SB-WALL-1DR are out of its scope entirely regardless.
`archive_zero_variant_duplicate_templates()` is the template-first,
code-list-agnostic sweep that closes both gaps.

Every plain "duplicate" fixture in this file is built via `_make_zero_
variant_dup`, which reproduces the ACTUAL production mechanism: a
template carrying a `create_variant='dynamic'` attribute line, whose
default_code is set at `create()` time. Odoo does not auto-materialize
a product.product for a dynamic-only attribute — a fact this same test
suite already relies on and asserts elsewhere (test_a5_template_code.
py's `test_known_q8_templates_get_codes`: "create_variant='dynamic' …
means nothing is auto-materialized — a bare/CI-minimal DB can carry a
Q8 template with zero physical variants at all"). Every fixture self-
checks its own preconditions (zero variants, code intact) so a broken
assumption fails loudly at fixture-build time rather than silently
invalidating a later assertion.
"""
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models import corner_sku_repair
from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    archive_catalog_family_duplicate_templates,
    archive_zero_variant_duplicate_templates,
)


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "corner_catalog_truth", "zero_variant_duplicate_archive")
class TestArchiveZeroVariantDuplicateTemplates(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["product.template"]
        cls.Product = cls.env["product.product"]
        cls.IMD = cls.env["ir.model.data"]
        # A single shared "dynamic" attribute — create_variant='dynamic'
        # means Odoo does NOT auto-materialize a product.product for a
        # template that only carries this attribute (see module
        # docstring) — the same mechanism the real debris-minting bug
        # relied on.
        cls.dynamic_attr = cls.env["product.attribute"].create({
            "name": "ZVD Test Dynamic Attr", "create_variant": "dynamic",
        })
        cls.dynamic_value = cls.env["product.attribute.value"].create({
            "name": "ZVD Test Value", "attribute_id": cls.dynamic_attr.id,
        })

    # ------------------------------------------------------------------
    # Fixture builders
    # ------------------------------------------------------------------
    def _make_zero_variant_dup(self, code, name):
        """A duplicate TEMPLATE carrying `code` on its own template-
        level default_code but with ZERO product.product variants —
        the exact debris shape verified on prod (see module docstring).
        Self-checks its own preconditions."""
        tmpl = self.Template.create({
            "name": name, "type": "consu", "default_code": code,
            "attribute_line_ids": [(0, 0, {
                "attribute_id": self.dynamic_attr.id,
                "value_ids": [(6, 0, [self.dynamic_value.id])],
            })],
        })
        self.assertEqual(
            len(tmpl.product_variant_ids), 0,
            "fixture setup: expected create_variant='dynamic' to leave "
            "zero materialized variants")
        self.assertEqual(
            tmpl.default_code, code,
            "fixture setup: expected default_code to survive template "
            "creation with zero materialized variants")
        return tmpl

    def _make_real_twin(self, code, with_xmlid=False):
        """A "real" template carrying `code` on its (lone, active)
        variant's default_code — a better record by the sweep's own
        predicate (>=1 variant)."""
        tmpl = self.Template.create({"name": "Real twin %s" % code,
                                      "type": "consu"})
        variant = tmpl.product_variant_ids[:1]
        variant.default_code = code
        if with_xmlid:
            self.IMD.create({
                "module": "southbrook_estimating",
                "name": "test_zvd_xmlid_%s" % tmpl.id,
                "model": "product.template",
                "res_id": tmpl.id,
                "noupdate": False,
            })
        return tmpl

    def _make_referenced_dup(self, code, name):
        """A duplicate whose single variant carries a real business
        reference and was later archived — reproduces "zero ACTIVE
        variants, but a real reference still hangs off an inactive
        one" without relying on any create_variant timing. Returns
        (tmpl, archived_variant)."""
        tmpl = self.Template.create({"name": name, "type": "consu"})
        variant = tmpl.product_variant_ids[:1]
        variant.default_code = code
        return tmpl, variant

    def _archive_variant_restore_code(self, tmpl, variant, code):
        variant.active = False
        self.env.cr.execute(
            "UPDATE product_template SET default_code = %s WHERE id = %s",
            (code, tmpl.id))
        tmpl.invalidate_recordset()
        self.assertEqual(len(tmpl.product_variant_ids), 0)
        self.assertEqual(tmpl.default_code, code)

    # ------------------------------------------------------------------
    def test_zero_variant_duplicate_archived_canonical_twin_untouched(self):
        real = self._make_real_twin("ZVD-BASE-1DR", with_xmlid=True)
        dup = self._make_zero_variant_dup(
            "ZVD-BASE-1DR", "Debris Base 1DR")

        summary = archive_zero_variant_duplicate_templates(self.env)
        self.assertGreaterEqual(summary["duplicate_templates_archived"], 1)

        dup.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertFalse(dup.active, "the zero-variant debris row must "
                          "be archived")
        self.assertTrue(
            real.active, "the canonical (variant-bearing) twin must "
            "never be touched")

    def test_duplicate_with_variants_not_archived(self):
        """A "duplicate" that itself carries >=1 variant is a real
        record by the sweep's own predicate — never archived, even
        though it shares a code with another active template."""
        real = self._make_real_twin("ZVD-HAS-VARIANT", with_xmlid=True)
        other_with_variant = self._make_real_twin("ZVD-HAS-VARIANT")

        archive_zero_variant_duplicate_templates(self.env)

        other_with_variant.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertTrue(
            other_with_variant.active,
            "a template with its own variant must never be archived "
            "by this sweep")
        self.assertTrue(real.active)

    def test_duplicate_with_xmlid_not_archived(self):
        """A zero-variant "duplicate" that itself carries an xml_id is
        a real record — never archived."""
        real = self._make_real_twin("ZVD-HAS-XMLID")
        dup_with_xmlid = self._make_zero_variant_dup(
            "ZVD-HAS-XMLID", "Zero-variant but xml_id-bearing")
        self.IMD.create({
            "module": "southbrook_estimating",
            "name": "test_zvd_dup_xmlid_%s" % dup_with_xmlid.id,
            "model": "product.template",
            "res_id": dup_with_xmlid.id,
            "noupdate": False,
        })

        archive_zero_variant_duplicate_templates(self.env)

        dup_with_xmlid.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertTrue(
            dup_with_xmlid.active,
            "a zero-variant template with its own xml_id must never "
            "be archived")
        self.assertTrue(real.active)

    def test_duplicate_referenced_by_sale_order_line_not_archived(self):
        code = "ZVD-REF-SOL"
        real = self._make_real_twin(code, with_xmlid=True)
        dup, variant = self._make_referenced_dup(
            code, "Debris referenced by SOL")
        partner = self.env["res.partner"].create(
            {"name": "ZVD SOL Test Partner"})
        self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {
                "product_id": variant.id, "product_uom_qty": 1,
            })],
        })
        self._archive_variant_restore_code(dup, variant, code)

        summary = archive_zero_variant_duplicate_templates(self.env)

        dup.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertTrue(
            dup.active,
            "a duplicate carrying a real sale-order-line reference "
            "(even via an archived variant) must never be archived")
        self.assertTrue(real.active)
        # Not asserted via summary["skipped_has_references"] specifically
        # (dup's archived variant already excludes it from the "zero
        # ACTIVE variants" candidate pool too) — the outcome under test
        # is the safety property itself: a referenced record survives.
        self.assertEqual(summary["duplicate_templates_archived"], 0)

    def test_duplicate_referenced_by_design_line_not_archived(self):
        if "southbrook.kitchen.design.line" not in self.env.registry.models:
            self.skipTest(
                "southbrook.kitchen.design.line not installed on this "
                "DB (southbrook_kitchen_3d_configurator not present).")
        code = "ZVD-REF-DESIGNLINE"
        real = self._make_real_twin(code, with_xmlid=True)
        dup, variant = self._make_referenced_dup(
            code, "Debris referenced by design line")
        partner = self.env["res.partner"].create(
            {"name": "ZVD Design Line Test Partner"})
        design = self.env["southbrook.kitchen.design"].create({
            "name": "ZVD design-line reference test",
            "partner_id": partner.id,
            "room_width_in": 144, "room_depth_in": 96,
            "room_height_in": 96,
        })
        self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id, "product_id": variant.id,
            "cabinet_type": "base", "layout_key": "zvd-ref-line",
            "origin": "configurator",
            "x_position_in": 12.0, "y_position_in": 0.0,
            "z_position_in": 0.0, "width_in": 18.0, "height_in": 34.5,
            "depth_in": 24.0, "quantity": 1, "price_unit": 500.0,
        })
        self._archive_variant_restore_code(dup, variant, code)

        archive_zero_variant_duplicate_templates(self.env)

        dup.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertTrue(
            dup.active,
            "a duplicate carrying a real design-line reference (even "
            "via an archived variant) must never be archived")
        self.assertTrue(real.active)

    def test_duplicate_referenced_by_bom_not_archived(self):
        """The one reference type reachable at TRUE zero variants (a
        BOM can target `product_tmpl_id` directly with no variant at
        all) — this is the only sub-case that actually exercises the
        BOM branch of the has_references guard on a candidate that
        also passes the "zero variants" gate."""
        code = "ZVD-REF-BOM"
        real = self._make_real_twin(code, with_xmlid=True)
        dup = self._make_zero_variant_dup(code, "Debris referenced by BOM")
        self.env["mrp.bom"].create({
            "product_tmpl_id": dup.id,
            "product_qty": 1.0,
        })

        summary = archive_zero_variant_duplicate_templates(self.env)

        dup.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertTrue(
            dup.active,
            "a zero-variant duplicate carrying a real BOM reference "
            "must never be archived")
        self.assertTrue(real.active)
        self.assertGreaterEqual(summary["skipped_has_references"], 1)

    def test_unique_default_code_never_archived(self):
        """A template whose default_code is not shared by anything
        else is never touched, however inert it otherwise looks."""
        lonely = self._make_zero_variant_dup(
            "ZVD-UNIQUE-LONELY", "Unique code, zero variants, inert")

        archive_zero_variant_duplicate_templates(self.env)

        lonely.invalidate_recordset(["active"])
        self.assertTrue(
            lonely.active,
            "a template with no duplicate sharing its code must never "
            "be archived")

    def test_idempotent_second_run_is_noop(self):
        real = self._make_real_twin("ZVD-IDEMPOTENT", with_xmlid=True)
        dup = self._make_zero_variant_dup(
            "ZVD-IDEMPOTENT", "Debris idempotency probe")

        first = archive_zero_variant_duplicate_templates(self.env)
        self.assertGreaterEqual(first["duplicate_templates_archived"], 1)

        dup.invalidate_recordset(["active"])
        self.assertFalse(dup.active)

        second = archive_zero_variant_duplicate_templates(self.env)
        self.assertEqual(
            second,
            {"duplicate_templates_archived": 0,
             "skipped_has_references": 0, "skipped_no_better_twin": 0},
            "second call must be a strict no-op: %s" % second)

        dup.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertFalse(dup.active, "archived row must stay archived")
        self.assertTrue(real.active)

    def test_zero_variant_corner_family_duplicate_regression(self):
        """Pinned regression — the SB-WALL-CORNER / prod-id-1098 shape.
        `archive_catalog_family_duplicate_templates()` (the pre-
        existing, variant-enumeration-based sweep) IS corner-family-
        code-aware ("SB-WALL-CORNER" is in `_CORNER_FAMILY_BASE_CODES`)
        but STILL misses a zero-variant corner-family duplicate,
        because it only ever discovers duplicates via `Product.search`
        on the code — a template with no variant of its own never
        contributes a row to that search, regardless of whether its
        code is in scope. `archive_zero_variant_duplicate_templates()`
        catches exactly this shape.

        The probe code is SYNTHETIC but is injected into
        `_CORNER_FAMILY_BASE_CODES` for the duration, so the old
        sweep's code-list scan genuinely considers it — proving the gap
        is the zero-variant enumeration blind spot, not merely an
        out-of-scope code. Using the literal "SB-WALL-CORNER" here
        instead would NOT prove that: the real catalog already ships
        its own SB-WALL-CORNER template, so the old sweep would see
        two variant-bearing templates for that code, resolve the
        genuine catalog row as "real" via its `min(tmpl_ids)`
        tie-break, and archive THIS fixture's synthetic twin as the
        impostor — failing the test for a reason that has nothing to
        do with the behaviour under test. The "real" twin is built with
        `_make_real_twin` (rather than by canonicalizing the actual
        corner catalog via `repair_corner_sku`) so this test does not
        depend on that chain's own — unrelated —
        `catalog_expansion.build_catalog()`-driven attribute wiring
        (out of this addon's scope) to materialize a second variant."""
        probe_code = "ZVD-WALL-CORNER"
        real = self._make_real_twin(probe_code, with_xmlid=True)
        debris = self._make_zero_variant_dup(
            probe_code, "Debris ZVD-WALL-CORNER (1098-shape)")

        # RED: the pre-existing variant-based sweep must NOT see it —
        # this is the actual gap on prod. Inject the probe code so that
        # sweep is genuinely in scope for it.
        with patch.object(
            corner_sku_repair, "_CORNER_FAMILY_BASE_CODES",
            frozenset(corner_sku_repair._CORNER_FAMILY_BASE_CODES
                      | {probe_code}),
        ):
            archive_catalog_family_duplicate_templates(self.env)
        debris.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertTrue(
            debris.active,
            "the variant-based sweep must NOT catch a zero-variant "
            "corner-family duplicate (this is the documented gap)")
        self.assertTrue(real.active)

        # GREEN: the new template-first sweep does.
        summary = archive_zero_variant_duplicate_templates(self.env)
        self.assertGreaterEqual(summary["duplicate_templates_archived"], 1)

        debris.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertFalse(
            debris.active,
            "the zero-variant corner-family debris row must now be "
            "archived")
        self.assertTrue(
            real.active,
            "the real corner-family twin must never be touched")
