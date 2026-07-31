# SPDX-License-Identifier: LGPL-3.0-only
"""Deploy-gate hotfix (2026-07-31) — archive_catalog_family_duplicate_
templates() in models/corner_sku_repair.py.

Ground truth (live-verified on the four-module deploy gate's DB, tmpl
13790): once a corner-family template (SB-CORNER, SB-CORNER-BLIND,
SB-CORNER-DIAG, SB-WALL-CORNER) gets a materialized "-R" hand twin,
its TEMPLATE-level default_code correctly blanks (Odoo only mirrors a
single variant onto the template field). A caller that re-enters the
repair chain on a LATER pass with a fresh env (this module's own two
stacked migrations, 19.0.9.1.0 then 19.0.9.2.0, both calling
repair_corner_sku in the SAME upgrade run) then found nothing via an
active-only, template-only search and minted a SECOND, single-variant,
attribute-less template carrying the same bare corner-family code —
this sweep cleans that up (and any future recurrence).

These tests exercise the sweep directly, against a canonicalized
starting state (the full repair chain run once in setUpClass, so every
corner-family code resolves to exactly one real template before each
test injects its own controlled duplicate).
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    archive_catalog_family_duplicate_templates,
    repair_corner_sku,
)


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "corner_catalog_truth")
class TestArchiveCatalogFamilyDuplicateTemplates(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["product.template"]
        cls.Product = cls.env["product.product"]
        # Canonicalize: run the full repair chain once so every
        # corner-family code resolves to exactly one real template
        # (with its RH twin) before any test injects its own
        # controlled duplicate.
        repair_corner_sku(cls.env)
        cls.env.flush_all()

    def _real_tmpl(self, base_code):
        variant = self.Product.search(
            [("default_code", "=", base_code)], limit=1)
        if not variant:
            self.skipTest("%s not seeded on this DB." % base_code)
        return variant.product_tmpl_id

    def _make_impostor(self, base_code, name):
        """A single-variant, attribute-less duplicate template
        carrying `base_code` on its own lone variant — the exact
        shape the deploy-gate bug (and any future recurrence)
        produces."""
        tmpl = self.Template.create({"name": name, "type": "consu"})
        variant = tmpl.product_variant_ids[:1]
        variant.default_code = base_code
        return tmpl, variant

    def test_impostor_archived_real_untouched(self):
        real = self._real_tmpl("SB-WALL-CORNER")
        impostor, _impostor_variant = self._make_impostor(
            "SB-WALL-CORNER", "Wall Corner 24")

        summary = archive_catalog_family_duplicate_templates(self.env)
        self.assertGreaterEqual(summary["duplicate_templates_archived"], 1)

        impostor.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertFalse(impostor.active, "the impostor must be archived")
        self.assertTrue(
            real.active, "the real template must never be touched")
        self.assertTrue(
            self.Product.search([("default_code", "=", "SB-WALL-CORNER-R")]),
            "the RH twin must remain intact")

    def test_idempotent_second_call_is_noop(self):
        self._make_impostor("SB-WALL-CORNER", "Wall Corner 24 Dup")
        first = archive_catalog_family_duplicate_templates(self.env)
        self.assertGreaterEqual(first["duplicate_templates_archived"], 1)

        second = archive_catalog_family_duplicate_templates(self.env)
        self.assertEqual(
            second,
            {"duplicate_templates_archived": 0, "skipped_has_references": 0},
            "second call must be a strict no-op: %s" % second)

    def test_impostor_with_real_reference_is_skipped_not_archived(self):
        """Defensive guard: a duplicate that (implausibly, but just in
        case) picked up a real sale-order-line reference must never be
        silently archived — flagged for human review instead."""
        impostor, impostor_variant = self._make_impostor(
            "SB-WALL-CORNER", "Wall Corner 24 Referenced")
        partner = self.env["res.partner"].create(
            {"name": "Hotfix Test Partner"})
        self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {
                "product_id": impostor_variant.id, "product_uom_qty": 1,
            })],
        })

        summary = archive_catalog_family_duplicate_templates(self.env)
        self.assertGreaterEqual(summary["skipped_has_references"], 1)

        impostor.invalidate_recordset(["active"])
        self.assertTrue(
            impostor.active,
            "a duplicate carrying a real order-line reference must "
            "never be archived automatically")

    def test_blind_and_diag_duplicate_shape_also_archived(self):
        """The gate's specific failure was SB-WALL-CORNER, but
        SB-CORNER-BLIND and SB-CORNER-DIAG carry the identical latent
        risk (their templates also go multi-variant via RH twins) —
        probe both."""
        for base_code in ("SB-CORNER-BLIND", "SB-CORNER-DIAG"):
            real = self._real_tmpl(base_code)
            impostor, _variant = self._make_impostor(
                base_code, "%s impostor probe" % base_code)

            summary = archive_catalog_family_duplicate_templates(self.env)
            self.assertGreaterEqual(
                summary["duplicate_templates_archived"], 1, base_code)

            impostor.invalidate_recordset(["active"])
            real.invalidate_recordset(["active"])
            self.assertFalse(
                impostor.active,
                "%s: impostor must be archived" % base_code)
            self.assertTrue(
                real.active, "%s: real template untouched" % base_code)

    def test_sb_corner_duplicate_shape_also_archived(self):
        """SB-CORNER itself (the operative diagonal-lazy-susan carcass,
        T4a's own repair target) carries the same risk once it has an
        RH twin."""
        real = self._real_tmpl("SB-CORNER")
        impostor, _variant = self._make_impostor(
            "SB-CORNER", "SB-CORNER impostor probe")

        summary = archive_catalog_family_duplicate_templates(self.env)
        self.assertGreaterEqual(summary["duplicate_templates_archived"], 1)

        impostor.invalidate_recordset(["active"])
        real.invalidate_recordset(["active"])
        self.assertFalse(impostor.active)
        self.assertTrue(real.active)
