# SPDX-License-Identifier: LGPL-3.0-only
"""Researched North American sheet-goods cost pass (client-authorized,
2026-08-02) — applies real standard_price figures to the panel-
substrate + edge-banding placeholder products this addon owns.

See ``mrp.bom._southbrook_apply_researched_sheet_goods_costs``
(models/mrp_bom_catalog.py) for the full fill-zeros-only contract, the
per-substrate source citations, and the documented per-panel-not-
per-sheet unit caveat. Source doc:
scratchpad/retest/pricing-sheetgoods.md.

Idempotent — safe to re-run by hand (e.g. from an odoo shell) if a
deploy needs to redo it; a product that already carries a non-zero
standard_price (this method's own prior write, or a concurrent
Marathon warehouse/brand pricing session's write) is left untouched.
Also get-or-creates the placeholder products via the same stable-xmlid
helpers the catalog BoM generator itself uses, so this runs correctly
whether or not that generator has fired yet on a given database.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    result = env["mrp.bom"]._southbrook_apply_researched_sheet_goods_costs()
    _logger.info(
        "southbrook_kitchen_mrp 19.0.1.9.0 post-migrate: researched "
        "sheet-goods cost seeder ran — applied=%s skipped_nonzero=%s",
        result.get("applied"), result.get("skipped_nonzero"),
    )
