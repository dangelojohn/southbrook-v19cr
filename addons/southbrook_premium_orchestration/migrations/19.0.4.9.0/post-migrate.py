# -*- coding: utf-8 -*-
"""Collapse the duplicate MI engine state rows.

Production held three: id 1 with every real statistic (last run 2026-07-30 19:42:59,
170 evaluations across 10 MOs x 17 checks, 90 blockers, 19 ms) and ids 2 and 3 empty.
`_get_singleton` used `search([], limit=1)`, so the engine wrote to id 1 while the menu
opened one of the empties — which is why `Run Engine Now` was reported as a silent no-op
and the engine as "silently dead". It was neither. It was writing somewhere nobody was
looking.

`_get_singleton` now collapses duplicates itself, so this migration only cleans the rows
that already exist. It keeps whichever row actually holds a run rather than the lowest
id, because the statistics are the thing worth preserving.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    Engine = env["southbrook.mi.engine.state"]

    rows = Engine.search([], order="id")
    if len(rows) <= 1:
        _logger.info("MI engine: %s state row(s); nothing to collapse", len(rows))
        return

    ran = rows.filtered("last_run_at").sorted("last_run_at", reverse=True)
    keep = ran[0] if ran else rows[0]
    drop = rows - keep
    _logger.warning(
        "MI engine: collapsing %s duplicate state row(s) %s; keeping id %s "
        "(last run %s, %s evaluations)",
        len(drop), drop.ids, keep.id, keep.last_run_at or "never",
        keep.last_run_check_count)
    drop.unlink()
