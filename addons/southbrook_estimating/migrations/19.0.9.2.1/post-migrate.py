# SPDX-License-Identifier: LGPL-3.0-only
"""Prod-audit follow-up (2026-07-31) — run the new zero-variant
duplicate-template sweep (`archive_zero_variant_duplicate_templates` in
`models/corner_sku_repair.py`) on prod. Archive-only; see SCOPE below
for why this deliberately does NOT re-run the wider repair chain.

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

SCOPE (corrected after the first prod attempt failed): this migration
calls ONLY `archive_zero_variant_duplicate_templates`, not the full
`repair_corner_sku()` chain. The chain's variant-CREATING step already
ran under 19.0.9.2.0; re-running it against catalog data a concurrent
session had since changed raised "Configurable Products cannot have
duplicates (identical attribute values)" and the deploy gate correctly
refused the restart. The sweep is purely subtractive — it archives
inert rows and creates nothing — so it cannot trip that constraint.

The manifest `version` is 19.0.9.2.1 and must keep matching this
directory name exactly, or Odoo will never run this folder.
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
    # DEPLOY-GATE FIX (2026-07-31, second attempt). This originally called
    # `repair_corner_sku()` — the WHOLE chain — and the prod cold upgrade
    # died on:
    #     ValidationError: Configurable Products cannot have duplicates
    #                      (identical attribute values)
    # The gate refused the restart, so prod was never switched over.
    #
    # Root cause: the chain's first step, `repair_corner_family_skus()`,
    # CREATES variants (the "-R" hand twins). It already ran on prod under
    # 19.0.9.2.0. Between that run and this one, a concurrent session
    # shipped corner-swap work of its own, so re-running a variant-CREATING
    # repair against changed catalog data can mint a combination that
    # already exists — which is precisely what the constraint caught.
    # (Verified beforehand that prod currently holds NO duplicate
    # attribute-value combinations on any config_ok template, so the
    # duplicate is created BY the upgrade, not pre-existing.)
    #
    # This folder exists for ONE new capability: the zero-variant
    # duplicate-template sweep. So call only that. It is purely
    # subtractive — it archives inert, unreferenced, zero-variant
    # templates and creates nothing — so it cannot violate the variant
    # constraint at all. The rest of the chain already ran and is not
    # this migration's job to redo.
    from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
        archive_zero_variant_duplicate_templates,
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info(
        "[19.0.9.2.1] zero-variant duplicate-template sweep "
        "(archive-only; the variant-creating repairs already ran under "
        "19.0.9.2.0 and are deliberately NOT re-run here): %s",
        archive_zero_variant_duplicate_templates(env),
    )
