# SPDX-License-Identifier: LGPL-3.0-only
"""Full-BoM-for-every-cabinet task (2026-07-31) — run the extended
catalog-wide BoM generator on upgrade.

19.0.1.7.0 is the version that removes the config_ok hard-skip from
mrp_bom_catalog.py's ``_southbrook_generate_catalog_boms`` (see that
module's docstring "ARCHITECTURE CHANGE" section) and fills the 11
empty "Canonical" routing-carrier BoM shells + creates BoMs for the
remaining BoM-less cabinet templates + per-variant BoMs for config_ok
templates' real materialized variants.

Odoo only runs a version-folder's migration script when the module's
currently-recorded version is STRICTLY BELOW that folder's version
(``odoo/modules/migration.py``) — so this needs its own folder rather
than piggy-backing on data reload, and needs to fire exactly once per
real upgrade. The generator itself is fully idempotent (re-running it
finds every BoM it already created/filled and leaves those alone), so
this script is also safe to re-run by hand if a deploy needs to redo it
(e.g. ``env['mrp.bom']._southbrook_generate_catalog_boms()`` from an
``odoo shell``).

This does NOT replace the nightly cron
(``cron_southbrook_catalog_bom_generator``) — that keeps sweeping newly
materialized variants / newly added cabinet templates on every
database going forward. This script's only job is to make sure a
database upgrading past 19.0.1.7.0 gets the benefit immediately rather
than waiting for the next nightly run.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    Bom = env["mrp.bom"]
    if "southbrook_is_cabinet" not in env["product.template"]._fields:
        _logger.info(
            "southbrook_kitchen_mrp 19.0.1.7.0 post-migrate: "
            "southbrook_is_cabinet field not present (southbrook_kitchen_"
            "3d_configurator not installed) — nothing to do."
        )
        return

    result = Bom._southbrook_generate_catalog_boms()
    _logger.info(
        "southbrook_kitchen_mrp 19.0.1.7.0 post-migrate: catalog BoM "
        "generator ran — created=%s shell_filled=%s variant_created=%s "
        "variant_updated=%s skipped_existing_bom=%s skipped_empty=%s",
        result.get("created"), result.get("shell_filled"),
        result.get("variant_created"), result.get("variant_updated"),
        result.get("skipped_existing_bom"), result.get("skipped_empty"),
    )
