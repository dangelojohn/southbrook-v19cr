# SPDX-License-Identifier: LGPL-3.0-only
"""Recompute every reliability snapshot against the new metric source.

19.0.3.0.0 changes where MTBF / MTTR / availability come from: breakdown
alerts with Datetime precision, instead of corrective maintenance requests
with Date precision. Without this pass the table would hold two populations —
rows written before the change and rows after — that are not comparable, and
the trends built on top of them would be meaningless.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    reports = env["southbrook.cmms.mtbf_mttr_report"].sudo().search([])
    if not reports:
        _logger.info("CMMS 19.0.3.0.0: no reliability snapshots to recompute")
        return
    reports.action_compute()
    env.flush_all()
    _logger.info(
        "CMMS 19.0.3.0.0: recomputed %d reliability snapshots against "
        "alert-sourced downtime", len(reports))
