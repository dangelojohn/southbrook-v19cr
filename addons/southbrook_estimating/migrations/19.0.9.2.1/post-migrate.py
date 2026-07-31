# SPDX-License-Identifier: LGPL-3.0-only
"""Prod-audit follow-up (2026-07-31) — re-run the corner/cabinet-variant
repair chain again, this time carrying the new zero-variant duplicate-
template sweep (`archive_zero_variant_duplicate_templates` in
`models/corner_sku_repair.py`).

Same deploy-blocking-gap mechanism as `migrations/19.0.9.2.0/
post-migrate.py` (T4b): `repair_corner_sku()` is only ever invoked from
this module's own migration scripts (no post_init_hook, no cron, no
button — confirmed by grep). Odoo only runs a version-folder's script
when the module's currently-recorded version is STRICTLY BELOW that
folder's version (`odoo/modules/migration.py`); prod `southbrook_
estimating` is already at 19.0.9.2.0, so neither the 19.0.9.1.0 nor the
19.0.9.2.0 migration will ever fire again on the upcoming upgrade — the
new sweep would silently never run on prod without a NEW, higher-
numbered folder. This one.

Why the new sweep needed its own migration bump rather than piggy-
backing on a data/demo reload: `repair_corner_sku()` chains three
independently-idempotent repairs (`repair_corner_family_skus`,
`archive_catalog_family_duplicate_templates`, now also
`archive_zero_variant_duplicate_templates`, then
`repair_cabinet_variant_refs`) that all read/write live catalog rows
via the ORM — that's migration-script territory, not `data/` XML.

Ground truth this closes (live prod audit, 2026-07-31): four debris
product.template rows — SB-BASE-1DR (id 1094), SB-BASE-2DR (id 1095),
SB-WALL-1DR (id 1097), SB-WALL-CORNER (id 1098) — each carry a non-
empty TEMPLATE-level default_code, zero product.product variants, zero
southbrook.kitchen.design.line / sale.order.line / mrp.bom references,
and no xml_id. Their canonical twins (ids 38/39/36/236 respectively)
are real, multi-variant, xml_id-bearing templates with genuine sale/
design/BOM history and are never touched by this sweep (see the new
function's own docstring for the exact predicate).

`repair_corner_sku()` itself needed NO further changes for this fix
beyond the one extra call site added inside it — every step in the
chain is already independently idempotent and skip-if-missing, so
simply calling the SAME entry point again here (mirroring 19.0.9.2.0/
post-migrate.py's own structure exactly: falsy-version guard, single
call, single log line wrapping the returned summary) is correct and
safe regardless of how much of the chain already ran via an earlier
migration on any given database. The manifest `version` key is
deliberately NOT bumped in this commit (binding constraint) — a later
controller-owned version bump to 19.0.9.2.1 is what makes this folder
eligible to run at all; it must match this directory name exactly.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Matches the sibling migrations' convention (e.g.
    # southbrook_kitchen_3d_configurator/migrations/19.0.5.34.1/
    # post-migrate.py, and this module's own 19.0.9.1.0/19.0.9.2.0) —
    # `version` is falsy only when Odoo calls this outside a genuine
    # version-transition upgrade; there is nothing to migrate from in
    # that case.
    if not version:
        return
    from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
        repair_corner_sku,
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info(
        "[19.0.9.2.1] prod-audit re-run of the corner/cabinet-variant "
        "repair chain, now including archive_zero_variant_duplicate_"
        "templates (repair_corner_sku -> repair_corner_family_skus -> "
        "archive_catalog_family_duplicate_templates -> "
        "archive_zero_variant_duplicate_templates -> "
        "repair_cabinet_variant_refs): %s",
        repair_corner_sku(env),
    )
