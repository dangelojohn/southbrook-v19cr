# SPDX-License-Identifier: LGPL-3.0-only
"""Re-window every stored snapshot after two boundary fixes.

19.0.4.0.0 changes what "the window" means in two places:

* Reliability snapshots now convert the user-timezone calendar day to UTC
  before comparing against alert timestamps. Previously every boundary was
  off by the UTC offset, so an outage logged near midnight landed in the
  wrong day's snapshot.
* KPI snapshots now window off the row's own `as_of` instead of today, and
  the 30-day lookback finally has an upper bound. The row seeded at install
  carried figures computed at the 19.0.3.0.0 upgrade while claiming to
  describe its install date — the first point of any trend line was wrong.

Both are recomputes, not schema changes: no row is created or destroyed.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    reports = env["southbrook.cmms.mtbf_mttr_report"].sudo().search([])
    if reports:
        reports.action_compute()
        _logger.info(
            "CMMS 19.0.4.0.0: re-windowed %d reliability snapshots (tz-correct "
            "day boundaries)", len(reports))

    tiles = env["southbrook.cmms.mi_tiles"].sudo().search([])
    if tiles:
        tiles.invalidate_recordset([
            "mtbf_avg_30d", "mttr_avg_30d", "open_breakdowns_count",
            "service_contracts_expiring_60d_count",
            "oversize_pickings_pending_count", "landed_cost_value_30d",
        ])
        tiles._compute_tiles()
        _logger.info(
            "CMMS 19.0.4.0.0: recomputed %d KPI snapshots against their own "
            "as_of", len(tiles))

    env.flush_all()
