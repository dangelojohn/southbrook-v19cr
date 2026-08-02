# SPDX-License-Identifier: LGPL-3.0-only
"""Catalog decision (2026-08-02) — Item 4: SB-CORNER-LSUSAN's dims are
stuck at the generic new-template fallback (24x34.5x24in) rather than
its real ~36x34.5x36in pie-cut-bifold-susan footprint (docs/research/
corner-engine/09-rule-engine-spec.md §1, bom-research.md 2026-07-31).
The fix landed in `repair_corner_family_skus` (Item 5b, models/
corner_sku_repair.py) — same mirroring convention as 19.0.9.2.0's own
migration: `repair_corner_sku()` is only ever invoked from a version-
gated migration script or from the sibling southbrook_configurator_ux
module's own `build_catalog()` self-heal, so a plain `-u` of THIS
module at an unchanged version would never re-run the chain and the
LSUSAN dims fix would silently never apply on an already-migrated
database (prod is already past 19.0.9.2.2). A NEW, higher-numbered
migration folder is the only mechanism guaranteed to fire.

Every step in the chain is independently idempotent and skip-if-
missing (see corner_sku_repair.py's own module docstring) — calling
the SAME entry point again here is safe regardless of how much of the
chain already ran on any given database.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Matches the sibling migrations' convention (e.g. 19.0.9.2.0's own
    # post-migrate.py) — `version` is falsy only when Odoo calls this
    # outside a genuine version-transition upgrade.
    if not version:
        return
    from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
        repair_corner_sku,
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info(
        "[19.0.9.2.3] catalog decision 2026-08-02 Item 4 — re-run of the "
        "corner/cabinet-variant repair chain (repair_corner_sku -> "
        "repair_corner_family_skus -> repair_cabinet_variant_refs) to "
        "apply the new SB-CORNER-LSUSAN dims fix (Item 5b): %s",
        repair_corner_sku(env),
    )
