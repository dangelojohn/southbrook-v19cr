# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-060 — variant BOMs must outrank template-wide wildcards. Durably.

THE DEFECT
----------
Odoo resolves a product's BOM with ORDER BY sequence, product_id, id — and
Postgres sorts a NULL `sequence` LAST in an ascending sort. The hand-seeded
template-wide "Canonical" BOMs carried `sequence = 0` while the generated
variant-specific `CatalogAutoBOM-Variant-*` BOMs carried NULL. So the
wildcard won for EVERY variant on every affected template:

    16 wildcard BOMs were shadowing 106 variant BOMs across the catalog.

That was not cosmetic. The shadowing BOM carries doubled quantities on
several hardware lines (2 sheets of ply where the variant says 1, 4 door
bumpers where it says 2), and it hands a right-hand corner the LEFT-hand
cut list. An E2E reviewer caught it via the handedness label — the one case
where the wrong BOM has a visibly wrong name.

WHY A MIGRATION, WHEN THE DATA WAS ALREADY FIXED
------------------------------------------------
The 2026-08-03 repair was applied directly to the production database
through the ORM (variants sequence=10, wildcards sequence=1000, verified:
109 variants resolving to their own BOM, 0 mismatches). But a raw data
change is not durable here — this project has recorded history of a module
upgrade reverting 37 DB-side assignments. This migration makes the same
repair land on every database, idempotently, every time the module
upgrades past 19.0.1.9.1. The generator itself now also stamps
sequence=10 on the variant BOMs it creates, so the two halves agree.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not touch archived BOMs (they never participate in resolution),
does not delete the wildcards (a variant with no BOM of its own must still
fall through to one), and does not use NULL (Odoo's Integer field cannot
store it through the ORM, and resolution should not depend on Postgres
null-ordering semantics in the first place).
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Bom = env["mrp.bom"]

    wildcards = Bom.search([("product_id", "=", False)])
    demoted = promoted = 0
    for wildcard in wildcards:
        variants = Bom.search([
            ("product_tmpl_id", "=", wildcard.product_tmpl_id.id),
            ("product_id", "!=", False),
        ])
        if not variants:
            # A lone template-wide BOM shadows nothing; leave it alone.
            continue
        stale = variants.filtered(lambda b: b.sequence != 10)
        if stale:
            stale.write({"sequence": 10})
            promoted += len(stale)
        if wildcard.sequence != 1000:
            wildcard.write({"sequence": 1000})
            demoted += 1

    # Prove the invariant this migration exists to hold: every variant that
    # has its own BOM resolves to it. Log loudly rather than raise — a
    # failed upgrade at midnight is worse than a loud warning, and the
    # ratchet test pins the same invariant in the suite.
    mismatches = []
    for variant_bom in Bom.search([("product_id", "!=", False)]):
        resolved = Bom._bom_find(variant_bom.product_id)[variant_bom.product_id]
        if resolved != variant_bom:
            mismatches.append((variant_bom.product_id.default_code,
                               variant_bom.id, resolved.id if resolved else None))

    if mismatches:
        _logger.warning(
            "[SBK-060] %d variant BOM(s) still not winning resolution after "
            "the precedence repair: %s", len(mismatches), mismatches[:10])
    else:
        _logger.info(
            "[SBK-060] BOM precedence repaired: %d wildcard(s) demoted, %d "
            "variant BOM(s) promoted; every variant-specific BOM now wins "
            "resolution for its own variant.", demoted, promoted)
