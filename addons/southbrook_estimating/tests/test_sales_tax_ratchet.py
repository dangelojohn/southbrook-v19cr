# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-042 — no saleable product may ship without a sales tax.

On 2026-08-03, 157 of 172 active saleable templates (91%) had no sales
tax linked, so their order lines computed no GST while lines beside them
on the same quotation did. $99,280 of subtotal across 94 orders was
affected, including every quotation already sent to a customer.

The migration at `19.0.9.3.0/post-backfill-sales-tax.py` repaired the
catalog. This test is the ratchet that stops it happening again — the
same shape as the facet ratchet added on 2026-07-31 for the tools
taxonomy, and for the same reason: a data gap that renders as a plausible
number is invisible until someone adds up a quote by hand.

The rule being pinned is deliberately narrow. It is NOT "every product
must charge 5% GST" — this database has a 0% GST tax precisely so a
zero-rated product can say so explicitly. The rule is that a saleable
product must carry *some* deliberate answer, because an empty set means
nobody decided.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_estimating", "sb_data_ratchet")
class TestSalesTaxRatchet(TransactionCase):

    def test_every_saleable_product_carries_a_sales_tax(self):
        offenders = self.env["product.template"].search([
            ("sale_ok", "=", True),
            ("active", "=", True),
            ("taxes_id", "=", False),
        ])
        if not offenders:
            return

        listing = "\n".join(
            f"  - [{t.id}] {t.display_name} "
            f"({t.categ_id.complete_name or 'no category'})"
            for t in offenders[:25]
        )
        more = "" if len(offenders) <= 25 else f"\n  ... and {len(offenders) - 25} more"
        self.fail(
            f"{len(offenders)} saleable product(s) have no sales tax linked, "
            f"so their order lines will silently compute no tax while lines "
            f"beside them on the same quotation do:\n{listing}{more}\n\n"
            f"An empty tax set is not the same as zero-rated. If a product "
            f"genuinely should not carry GST, link the 0% GST tax so the "
            f"decision is recorded; if it should, link the company default."
        )

    def test_a_new_saleable_product_inherits_the_company_default(self):
        """The field default exists in Odoo core; this asserts it is
        actually reachable here, since the original defect was that the
        catalog seed bypassed it."""
        company = self.env.company
        if not company.account_sale_tax_id:
            self.skipTest("company has no default sales tax configured")

        tmpl = self.env["product.template"].create({
            "name": "SBK042 Tax Default Probe",
            "type": "consu",
            "sale_ok": True,
            "list_price": 100.0,
        })
        self.assertTrue(
            tmpl.taxes_id,
            "a newly created saleable product must pick up the company's "
            "default sales tax — if this fails, the backfill migration will "
            "be undone by the next batch of seeded products")
