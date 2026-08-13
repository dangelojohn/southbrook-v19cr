# SPDX-License-Identifier: LGPL-3.0-only
"""Remove the install seed that became a test record in production.

`mi_tiles_default` was seeded so the Snapshot screen would not open empty. It
was renamed "Smoke" during June testing and `noupdate="1"` preserved that
through every upgrade since — leaving a smoke-test row as the earliest point
of the KPI history, which any trend line would draw first.

The record is dropped here and no longer seeded at all; a post_init_hook
writes a real snapshot on fresh installs instead. Removing it from the data
file alone is not enough to be sure it goes: this unlinks it explicitly.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Tiles = env["southbrook.cmms.mi_tiles"].sudo()
    seed = env.ref("southbrook_cmms_wms.mi_tiles_default", raise_if_not_found=False)
    if seed:
        label, as_of = seed.label, seed.as_of
        seed.unlink()
        _logger.info(
            "CMMS 19.0.6.0.0: removed the seeded KPI snapshot (label=%r, "
            "as_of=%s); history now starts at a real snapshot", label, as_of)
    else:
        _logger.info("CMMS 19.0.6.0.0: no seeded KPI snapshot to remove")

    # Removing the seed must not leave the Snapshot screen empty — that is what
    # the seed was for. post_init_hook only fires on install, so an upgrade
    # whose only row WAS the seed needs a real one written here.
    if not Tiles.search_count([]):
        Tiles._cron_snapshot()
        _logger.info("CMMS 19.0.6.0.0: wrote the first real KPI snapshot")
    env.flush_all()
