# SPDX-License-Identifier: LGPL-3.0-only
"""T4b (corner-remediation-2026-07-30 follow-up) — deploy-blocking gap:
re-run the full corner/cabinet-variant repair chain on the upcoming
upgrade.

Pre-deploy verification found: `repair_corner_sku()` — which chains
`repair_corner_family_skus()` then `repair_cabinet_variant_refs()`, see
`models/corner_sku_repair.py` — was invoked ONLY from
`migrations/19.0.9.1.0/post-migrate.py`. Odoo's migration framework only
runs a version-folder's script when the module's currently-recorded
version is STRICTLY BELOW that folder's version
(`odoo/modules/migration.py`); prod `southbrook_estimating` is ALREADY
at 19.0.9.1.0, so that migration will NEVER re-fire on the upcoming
upgrade — the whole corner-catalog promotion (SB-CORNER-BLIND/-DIAG/
-WALL-CORNER classification flags + RH twins, the SB-CORNER rename,
the SB-CORNER-LSUSAN archive, and the SBK-037 cabinet-variant-ref
dedup/empty-fill) would silently never run on prod at all.

`post_init_hook` is not a substitute either — confirmed (Task 4's own
report, 2026-07-30) that it only fires on `-i`, never `-u`, in this
Odoo version. The only mechanism guaranteed to fire on the upcoming
upgrade is a NEW, higher-numbered migration folder — this one. The
manifest `version` key is deliberately NOT bumped in this commit
(binding constraint) — the controller's own version bump to 19.0.9.2.0
is what makes THIS folder eligible to run at all; it must match this
directory name exactly.

`repair_corner_sku()` itself needed NO changes for this fix: every step
in the chain (T4a's own SB-CORNER repair, T4's
`repair_corner_family_skus`, SBK-037's `repair_cabinet_variant_refs`)
is already independently idempotent and skip-if-missing (resolve-then-
verify, by default_code, never substituting a different product
family), so simply calling the SAME entry point again here — mirroring
19.0.9.1.0/post-migrate.py's own structure exactly (falsy-version
guard, single call, single log line wrapping the returned summary) — is
correct and safe regardless of how much of the chain already ran via
the 19.0.9.1.0 migration on any given database.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Matches the sibling migrations' convention (e.g.
    # southbrook_kitchen_3d_configurator/migrations/19.0.5.34.1/
    # post-migrate.py) — `version` is falsy only when Odoo calls this
    # outside a genuine version-transition upgrade (e.g. a test harness
    # exercising the no-op path directly); there is nothing to migrate
    # from in that case.
    if not version:
        return
    from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
        repair_corner_sku,
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    _logger.info(
        "[19.0.9.2.0] T4b deploy-blocking-gap re-run of the corner/"
        "cabinet-variant repair chain (repair_corner_sku -> "
        "repair_corner_family_skus -> repair_cabinet_variant_refs): %s",
        repair_corner_sku(env),
    )
