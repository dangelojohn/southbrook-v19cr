# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-042 — link the company's default sales tax to saleable products.

THE DEFECT
----------
On 2026-08-03 an E2E review noticed a quotation whose GST was 5% of only
part of its own subtotal. The catalog audit that followed found the
cause is not two products, it is nearly all of them:

    157 of 172 active saleable product templates (91%) had NO sales tax
    linked at all — every corner unit, every wall-cabinet variant, every
    vanity, all Hardware, all Lighting, all Fasteners, all CNC Tools.

    $99,280 of subtotal sits on order lines whose product carries no
    tax, across 94 orders — including all 13 quotations already sent to
    customers and 9 of the 12 confirmed orders.

Odoo's own line-level tax computation is correct and the configurator
code is correct: `sale.order.line.tax_ids` computes
`product.taxes_id` filtered by company, and it faithfully returned
nothing because `product.taxes_id` was empty. `product.template.taxes_id`
does carry a default of the company's sales tax, but a Python field
default only fires at record-creation time, and the catalog seed XML
(`data/product_templates.xml` and its siblings) never set it. Nothing
has backfilled it since.

WHY AN EMPTY TAX SET IS THE BUG, NOT A CHOICE
---------------------------------------------
This database has both a "5% GST" and a "0% GST" tax. So a product that
genuinely should not carry GST is expressible — as the 0% tax. An empty
set does not mean "zero-rated", it means nobody configured it. That is
the same distinction the 2026-07-31 safety work drew between "assessed
as safe" and "not assessed", and it is resolved the same way: make the
unconfigured state impossible to confuse with a decision.

WHAT THIS DOES — AND DELIBERATELY DOES NOT DO
---------------------------------------------
Links `res_company.account_sale_tax_id` (here: 5% GST, the company's own
configured default — nothing invented) to every active, saleable
template that has no sales tax. Idempotent: a template that already has
one is never touched, so re-running changes nothing.

It does NOT retotal any existing order. Odoo computes
`sale.order.line.tax_ids` from `product_id` and `company_id`, not from
`product.taxes_id`, so existing lines are not invalidated by this write.
That boundary is intentional:

    Future quotes become correct. The 13 quotations already sent to
    customers, and the 9 confirmed orders, keep the totals those
    customers were shown.

Retroactively re-totalling a document a customer has already received is
a commercial decision, not an engineering one, and it is flagged for
John rather than taken here. Nothing has been invoiced (`account_move`
has zero rows), so that decision is still fully open.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    company = env["res.company"].search([], order="id", limit=1)
    default_tax = company.account_sale_tax_id

    if not default_tax:
        _logger.warning(
            "[SBK-042] company %s has no account_sale_tax_id — skipping the "
            "sales-tax backfill rather than guessing which tax applies",
            company.display_name or "?",
        )
        return

    targets = env["product.template"].search([
        ("sale_ok", "=", True),
        ("active", "=", True),
        ("taxes_id", "=", False),
    ])
    if not targets:
        _logger.info("[SBK-042] no saleable product is missing a sales tax")
        return

    by_category = {}
    for tmpl in targets:
        key = tmpl.categ_id.complete_name or "(no category)"
        by_category[key] = by_category.get(key, 0) + 1

    # A mass tax write is commercially consequential and, once applied,
    # indistinguishable from a tax someone set deliberately. Record
    # exactly which templates this touched so it can be reverted
    # precisely rather than by guesswork.
    #
    # This is not hypothetical caution: rehearsing this migration on a
    # clone of the DEVELOPMENT database resolved the company default to
    # a 15% tax, not the 5% GST production carries. The rule is right in
    # both cases — apply *that company's* configured default — but it
    # means the rate is only ever as correct as the company record, and
    # a wrong one would need undoing across the whole catalog.
    env["ir.config_parameter"].sudo().set_param(
        "southbrook.sbk042.tax_backfill_touched_ids",
        ",".join(str(i) for i in targets.ids),
    )

    targets.write({"taxes_id": [(6, 0, default_tax.ids)]})

    _logger.info(
        "[SBK-042] linked '%s' (amount=%s, type=%s) to %d saleable "
        "product(s) that had none: %s. Reverting: the touched ids are in "
        "ir.config_parameter 'southbrook.sbk042.tax_backfill_touched_ids'. "
        "Existing order lines are NOT retotalled — tax_ids depends on "
        "product_id/company_id, so quotes already sent keep the totals the "
        "customer was shown.",
        default_tax.display_name, default_tax.amount,
        default_tax.amount_type, len(targets),
        ", ".join(f"{k}={v}" for k, v in sorted(by_category.items())),
    )
