# SPDX-License-Identifier: LGPL-3.0-only
"""Recount stored KPI snapshots after the unresolved-breakdown fix.

`open_breakdowns_count` counted `state = 'open'` only, so a breakdown that had
been dispatched and not yet fixed dropped out of the tile. Every stored
snapshot therefore under-reports. They are recomputed here rather than left to
disagree with the fixed code.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    tiles = env["southbrook.cmms.mi_tiles"].sudo().search([])
    if not tiles:
        return
    tiles.invalidate_recordset(["open_breakdowns_count"])
    tiles._compute_tiles()
    env.flush_all()
    _logger.info(
        "CMMS 19.0.5.0.0: recounted %d KPI snapshots (dispatched breakdowns "
        "now count as unresolved)", len(tiles))
