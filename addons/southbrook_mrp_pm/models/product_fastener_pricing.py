# SPDX-License-Identifier: LGPL-3.0-only
"""Researched North American fastener costs (client-authorized,
2026-08-02 pricing pass) for the 5 fastener ``product.product`` records
seeded in ``data/products_mfg.xml``.

Full citations live in scratchpad/retest/pricing-hardware.md — read that
file in full before touching ``_RESEARCHED_FASTENER_COSTS`` below. Only
2 of the 5 fasteners here have a confirmed researched figure —
FAST-CONFIRMAT-50 and FAST-DOWEL-8MM. The other 3 are deliberately left
untouched this pass:

* FAST-CAM — pricing-hardware.md's own header names
  ``southbrook_hardware_catalog``'s MRH-CAMLOCK-KIT (not FAST-CAM) as
  the SKU its $0.55/set cam+bolt figure targets; see that addon's
  ``_southbrook_apply_researched_hardware_costs``.
* FAST-HANDLE-SCR / FAST-SLIDE-SCR — no distinct researched figure for
  either this pass.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    _inherit = "product.product"

    _RESEARCHED_FASTENER_COSTS = {
        "FAST-CONFIRMAT-50": {
            "price": 0.1924,
            "source": (
                "McFeely's - 7mm x 50mm Confirmat cabinet-assembly "
                "screw, $13.74 USD/100 x 1.40 = $19.24 CAD/100 -> "
                "$0.1924/ea, 2026-08-02, confidence: firm retail (USD "
                "converted)."
            ),
        },
        "FAST-DOWEL-8MM": {
            "price": 0.117,
            "source": (
                "Lee Valley Tools Canada - 8x30mm beech hardwood dowel "
                "pins, box of 100, $11.70 CAD -> $0.117/ea, 2026-08-02, "
                "confidence: firm (native CAD, no conversion - the "
                "most reliable single figure in the hardware research "
                "set)."
            ),
        },
    }

    @api.model
    def _southbrook_apply_researched_fastener_costs(self):
        """Idempotent, fill-zeros-only seeder — same fill-zeros-only
        contract as southbrook_hardware_catalog's sibling method (see
        that method's docstring for the full rationale): a product
        whose ``standard_price`` is already non-zero is SKIPPED, never
        overwritten, so a concurrent Marathon warehouse/brand pricing
        pass always wins over this retail-derived research. Re-running
        this method is a no-op once a SKU has a non-zero price.

        Company-currency note: figures are CAD; see the sibling
        method's docstring — same "verify before trusting" caveat
        applies here, logged but non-blocking.

        Returns {"applied": [...], "skipped_nonzero": [...],
        "skipped_no_product": [...]}.
        """
        company_currency = self.env.company.currency_id.name
        if company_currency != "CAD":
            _logger.warning(
                "Researched fastener cost seeder: company currency is "
                "%s, not CAD — the figures in _RESEARCHED_FASTENER_"
                "COSTS are CAD-denominated and are applied as-is to "
                "standard_price regardless. Verify res.company."
                "currency_id before trusting these numbers on this "
                "database (db=%s).",
                company_currency, self.env.cr.dbname,
            )

        Product = self.env["product.product"].sudo()
        applied, skipped_nonzero, skipped_no_product = [], [], []

        for sku, info in self._RESEARCHED_FASTENER_COSTS.items():
            product = Product.search([("default_code", "=", sku)], limit=1)
            if not product:
                skipped_no_product.append(sku)
                _logger.warning(
                    "Researched fastener cost seeder: SKU '%s' not "
                    "found in the installed catalog — skipped.", sku,
                )
                continue
            if product.standard_price:
                skipped_nonzero.append(sku)
                continue
            product.standard_price = info["price"]
            applied.append(sku)

        _logger.info(
            "Researched fastener cost seeder: applied=%s "
            "skipped_nonzero=%s skipped_no_product=%s",
            applied, skipped_nonzero, skipped_no_product,
        )
        return {
            "applied": applied,
            "skipped_nonzero": skipped_nonzero,
            "skipped_no_product": skipped_no_product,
        }
