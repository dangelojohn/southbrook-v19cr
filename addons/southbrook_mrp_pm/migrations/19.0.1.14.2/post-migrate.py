# SPDX-License-Identifier: LGPL-3.0-only
"""Researched North American fastener cost pass (client-authorized,
2026-08-02) — applies real standard_price figures to 2 of the 5
fastener products seeded in data/products_mfg.xml.

See ``product.product._southbrook_apply_researched_fastener_costs``
(models/product_fastener_pricing.py) for the full fill-zeros-only
contract and per-SKU source citations. Source doc:
scratchpad/retest/pricing-hardware.md.

Idempotent — safe to re-run by hand (e.g. from an odoo shell) if a
deploy needs to redo it; a product that already carries a non-zero
standard_price (this method's own prior write, or a concurrent
Marathon warehouse/brand pricing session's write) is left untouched.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    result = env["product.product"]._southbrook_apply_researched_fastener_costs()
    _logger.info(
        "southbrook_mrp_pm 19.0.1.14.2 post-migrate: researched "
        "fastener cost seeder ran — applied=%s skipped_nonzero=%s "
        "skipped_no_product=%s",
        result.get("applied"), result.get("skipped_nonzero"),
        result.get("skipped_no_product"),
    )
