# SPDX-License-Identifier: LGPL-3.0-only
"""Researched North American hardware cost pass (client-authorized,
2026-08-02) — applies real standard_price figures to the hinge/slide/
cam-lock/handle/corner-mechanism SKUs this addon owns.

See ``southbrook.hardware.catalog._southbrook_apply_researched_
hardware_costs`` (models/southbrook_hardware_catalog.py) for the full
fill-zeros-only contract, the per-SKU source citations, and why
x_pricing_pending is deliberately left untouched. Source doc:
scratchpad/retest/pricing-hardware.md.

Idempotent — safe to re-run by hand (e.g. from an odoo shell) if a
deploy needs to redo it; a SKU that already carries a non-zero
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
    result = env["southbrook.hardware.catalog"]._southbrook_apply_researched_hardware_costs()
    _logger.info(
        "southbrook_hardware_catalog 19.0.2.5.0 post-migrate: researched "
        "hardware cost seeder ran — applied=%s skipped_nonzero=%s "
        "skipped_no_product=%s",
        result.get("applied"), result.get("skipped_nonzero"),
        result.get("skipped_no_product"),
    )
