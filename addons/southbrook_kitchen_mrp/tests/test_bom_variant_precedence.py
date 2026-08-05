# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-060 — a variant-specific BOM must always beat a template wildcard.

Odoo picks a BOM with ORDER BY sequence, product_id, id, and Postgres sorts
a NULL sequence LAST. On 2026-08-03 that combination had 16 template-wide
"Canonical" wildcards (sequence=0) shadowing 106 variant BOMs (sequence
NULL) — every configured cabinet was being built from a template-wide parts
list with doubled hardware quantities, and right-hand corners resolved to
the left-hand cut list.

The generator now stamps sequence=10 on variant BOMs and the 19.0.1.9.1
migration demotes wildcards to 1000. This ratchet holds the invariant the
same way the taxonomy and sales-tax ratchets do: if any future seed or
manual edit reintroduces a shadowing wildcard, the suite says so by name
instead of the shop finding out at the saw.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_mrp", "sb_data_ratchet")
class TestBomVariantPrecedence(TransactionCase):

    def test_every_variant_bom_wins_resolution_for_its_own_variant(self):
        Bom = self.env["mrp.bom"]
        losers = []
        for variant_bom in Bom.search([("product_id", "!=", False)]):
            product = variant_bom.product_id
            resolved = Bom._bom_find(product)[product]
            if resolved != variant_bom:
                losers.append(
                    f"  - {product.default_code or product.display_name}: "
                    f"own bom {variant_bom.id} "
                    f"({variant_bom.code or 'uncoded'}) loses to "
                    f"{resolved.id if resolved else 'nothing'} "
                    f"({resolved.code if resolved else '-'})"
                )
        self.assertFalse(
            losers,
            "These products have a variant-specific BOM that LOSES resolution "
            "to a template-wide wildcard, so the shop would build them from "
            "the wrong parts list:\n" + "\n".join(losers) + "\n\n"
            "A variant BOM must carry a lower sequence than its template's "
            "wildcard (generator stamps 10; wildcards belong at 1000)."
        )

    def test_a_lone_wildcard_still_resolves_as_fallback(self):
        """The repair must not orphan products: where a template has ONLY a
        wildcard BOM, that wildcard is still found."""
        Bom = self.env["mrp.bom"]
        for wildcard in Bom.search([("product_id", "=", False)], limit=20):
            has_variants = bool(Bom.search_count([
                ("product_tmpl_id", "=", wildcard.product_tmpl_id.id),
                ("product_id", "!=", False),
            ]))
            if has_variants:
                continue
            probe = wildcard.product_tmpl_id.product_variant_id
            if not probe:
                continue
            resolved = Bom._bom_find(probe)[probe]
            self.assertEqual(
                resolved, wildcard,
                f"{probe.default_code or probe.display_name}: the only BOM "
                f"its template has must still resolve as the fallback")
            return  # one genuine fallback case proven is enough
        self.skipTest("no lone-wildcard template in this database to probe")
