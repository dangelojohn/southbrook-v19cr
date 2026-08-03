# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-041 — force one full reconciliation pass after the multi-wall fix.

Until 19.0.5.47.0 the design→room reconciler created a single synthetic
wall per room and hardcoded `layout_shape = "straight"`, so every L-, U-
and galley-shaped kitchen was mirrored into the quote graph as a
one-wall straight run, and every sale.order.line was pinned to that one
wall.

The corrected reconciler heals a design the next time it runs — but it
only looks at designs whose `write_date` advanced past a stored
watermark, and the already-wrong rooms are, by definition, not being
edited. Clearing the watermark makes the next cron tick re-scan every
design once. There were 53 designs at the time of writing against a
batch limit of 200, so a single tick covers the backlog.

Deliberately does NOT rewrite rows directly. The reconciler is the one
place that knows how a design maps onto a room, and duplicating that
mapping in a migration is how the two drift apart.
"""

import logging

_logger = logging.getLogger(__name__)

_LAST_RUN_KEY = "southbrook.rec_d.design_reconcile.last_run"


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key = %s",
        (_LAST_RUN_KEY,),
    )
    _logger.info(
        "[SBK-041] cleared reconcile watermark %s (%d row(s)) — the next "
        "cron tick re-derives layout_shape and per-wall records for every "
        "existing design",
        _LAST_RUN_KEY, cr.rowcount,
    )
