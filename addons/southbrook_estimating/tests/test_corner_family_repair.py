# SPDX-License-Identifier: LGPL-3.0-only
"""Task 4 — corner catalog truth: SB-CORNER-BLIND / SB-CORNER-DIAG /
SB-WALL-CORNER promoted to real SKUs, SB-CORNER renamed to reflect what
it actually is, SB-CORNER-LSUSAN archived as a duplicate.

See models/corner_sku_repair.py::repair_corner_family_skus for the
ground truth citation and .superpowers/sdd/corner-remediation-2026-07-30/
task-4-brief.md for the requirements this covers.

Unlike test_corner_repair.py's ensure_repaired_corner() (which recreates
a known-bad state before repairing, because a bare DB has zero SB-CORNER
variants), these tests run the repair directly against whatever corner
catalog state the target DB already carries — the whole point of
"resolve-then-verify, skip-if-missing" is that the repair is safe to run
from an unknown starting state. On a DB where southbrook_kitchen_3d_
configurator (the southbrook_cabinet_type/southbrook_is_cabinet fields)
isn't installed, the classification/panel-domain assertions degrade to
a skip rather than a false failure.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    repair_corner_family_skus,
)

_FAMILY_CODES = ("SB-CORNER", "SB-CORNER-BLIND", "SB-CORNER-DIAG",
                  "SB-WALL-CORNER")


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "corner_catalog_truth")
class TestCornerFamilyRepair(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["product.template"]
        cls.Product = cls.env["product.product"]
        cls.has_cabinet_fields = (
            "southbrook_cabinet_type" in cls.Template._fields)
        cls.first_summary = repair_corner_family_skus(cls.env)

    def _tmpl(self, default_code):
        # Same trap the repair itself guards against: product.template
        # .default_code reads back False once the template has more
        # than one variant (Odoo only mirrors it for the single-variant
        # case), so a template-level search alone would go blind for
        # every family member the moment its RH twin gets materialized.
        # Resolve via the still-unique variant code first.
        variant = self.Product.search(
            [("default_code", "=", default_code)], limit=1)
        tmpl = (variant.product_tmpl_id if variant else
                self.Template.search(
                    [("default_code", "=", default_code)], limit=1))
        if not tmpl:
            self.skipTest(
                "%s not seeded on this DB — catalog_expansion / "
                "kitchen_3d_configurator demo data may not be "
                "installed here." % default_code)
        return tmpl

    def test_panel_corner_filter_matches_all_four_families(self):
        """AC: southbrook_cabinet_type='corner' AND southbrook_is_cabinet
        =True matches SB-CORNER + SB-CORNER-BLIND + SB-CORNER-DIAG +
        SB-WALL-CORNER (the exact domain the drag/add/swap panel uses,
        per V3-products.md Claim 4)."""
        if not self.has_cabinet_fields:
            self.skipTest(
                "southbrook_kitchen_3d_configurator not installed — "
                "southbrook_cabinet_type/is_cabinet don't exist here.")
        # Identify by resolved tmpl id, not by default_code: a
        # multi-variant family member's default_code reads back False
        # at the template level (see _tmpl's docstring), so comparing
        # default_code strings against the domain search's .mapped()
        # would silently mismatch for every family member with an RH
        # twin already materialized.
        matched_ids = set(self.Template.search([
            ("southbrook_cabinet_type", "=", "corner"),
            ("southbrook_is_cabinet", "=", True),
        ]).ids)
        for code in _FAMILY_CODES:
            tmpl = self._tmpl(code)
            self.assertIn(
                tmpl.id, matched_ids,
                "%s (tmpl id=%s) must satisfy the panel's Corner filter "
                "domain (southbrook_cabinet_type='corner', "
                "southbrook_is_cabinet=True)" % (code, tmpl.id))

    def test_each_family_member_has_an_rh_variant(self):
        """AC: each has an RH variant — resolved via the attribute-value
        combination (product_variant_count can read 0 even when a
        variant exists, per the Odoo 19 config_ok trap), never by
        assuming the template's default variant_id."""
        for code in ("SB-CORNER-BLIND", "SB-CORNER-DIAG", "SB-WALL-CORNER"):
            tmpl = self._tmpl(code)
            rh_variants = tmpl.product_variant_ids.filtered(
                lambda v: any(
                    ptav.attribute_id.name == "Hinge Side"
                    and (ptav.name or "").startswith("RH")
                    for ptav in v.product_template_attribute_value_ids))
            self.assertTrue(
                rh_variants,
                "%s must have a materialized RH (Hinge Side) variant "
                "after the repair" % code)
            self.assertTrue(
                rh_variants[0].default_code,
                "the RH twin must carry a resolvable default_code")

    def test_blind_width_promoted_to_45(self):
        """Item 1 — SB-CORNER-BLIND's Width relinks 33->45 (a real,
        correct blind-corner-base width missing from the shared Width
        vocabulary until this repair adds it)."""
        tmpl = self._tmpl("SB-CORNER-BLIND")
        variants = tmpl.product_variant_ids
        self.assertTrue(variants, "SB-CORNER-BLIND must have variants")
        for v in variants:
            widths = v.product_template_attribute_value_ids.filtered(
                lambda p: p.attribute_id.name == "Width")
            self.assertTrue(widths, "every variant must carry a Width")
            self.assertTrue(
                all((w.name or "").startswith("45") for w in widths),
                "no SB-CORNER-BLIND variant may keep a non-45in width "
                "after the repair (got %s)" % widths.mapped("name"))

    def test_diag_width_promoted_to_36(self):
        """Item 2 — SB-CORNER-DIAG's Width relinks 33->36."""
        tmpl = self._tmpl("SB-CORNER-DIAG")
        variants = tmpl.product_variant_ids
        self.assertTrue(variants, "SB-CORNER-DIAG must have variants")
        for v in variants:
            widths = v.product_template_attribute_value_ids.filtered(
                lambda p: p.attribute_id.name == "Width")
            self.assertTrue(
                all((w.name or "").startswith("36") for w in widths),
                "no SB-CORNER-DIAG variant may keep a non-36in width "
                "after the repair (got %s)" % widths.mapped("name"))

    def test_wall_corner_dims_and_type(self):
        """Item 3 — SB-WALL-CORNER: W24 x H30 x D24, tagged corner (not
        wall) so it satisfies the same Corner panel filter as the other
        three families."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl = self._tmpl("SB-WALL-CORNER")
        self.assertEqual(tmpl.southbrook_cabinet_type, "corner")
        self.assertTrue(tmpl.southbrook_is_cabinet)
        self.assertEqual(tmpl.southbrook_width_in, 24.0)
        self.assertEqual(tmpl.southbrook_height_in, 30.0)
        self.assertEqual(tmpl.southbrook_depth_in, 24.0)

    def test_sb_corner_renamed(self):
        """Item 4 — SB-CORNER (the operative diagonal-lazy-susan
        carcass) is renamed; default_code and any existing SO/design
        lines are untouched (name-only change)."""
        tmpl = self._tmpl("SB-CORNER")
        self.assertEqual(tmpl.name, "Corner Base · Lazy-Susan 36")
        # default_code itself was never written by the repair. Can't
        # assert the TEMPLATE-level field literally equals "SB-CORNER"
        # here — once SB-CORNER has 2 variants (the T4a RH twin), Odoo
        # blanks the template-level mirror by design (see _tmpl's
        # docstring) — so assert on the still-resolvable VARIANT code
        # that _tmpl() itself just used to find this template.
        self.assertTrue(
            self.Product.search([
                ("product_tmpl_id", "=", tmpl.id),
                ("default_code", "=", "SB-CORNER"),
            ], limit=1),
            "a SB-CORNER-coded variant must still resolve to this "
            "template — default_code must never change")

    def test_lsusan_archived(self):
        """Item 5 — SB-CORNER-LSUSAN is archived (active=False), not
        deleted (never destroy data on a repair). Asserts no ACTIVE
        record with this default_code exists (rather than picking one
        arbitrary record) so the panel can never double-list it."""
        any_record = self.Template.with_context(active_test=False).search(
            [("default_code", "=", "SB-CORNER-LSUSAN")], limit=1)
        if not any_record:
            self.skipTest("SB-CORNER-LSUSAN not seeded on this DB.")
        still_active = self.Template.search(
            [("default_code", "=", "SB-CORNER-LSUSAN")])
        self.assertFalse(
            still_active,
            "SB-CORNER-LSUSAN must be archived as a duplicate of "
            "SB-CORNER — found active record(s) %s" % still_active.ids)

    def test_repair_is_idempotent(self):
        """AC: running the repair twice changes nothing on the second
        pass — every counter in the second summary must be zero/false."""
        second = repair_corner_family_skus(self.env)
        self.assertEqual(
            second,
            {"flags_set": 0, "width_values_created": 0, "width_fixed": 0,
             "codes_set": 0, "lh_created": 0, "lh_combo_backfilled": 0,
             "rh_created": 0, "renamed": False, "lsusan_archived": False},
            "second run must be a strict no-op: %s" % second)
