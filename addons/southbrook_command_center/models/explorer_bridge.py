# SPDX-License-Identifier: LGPL-3.0-only
"""Single source of factory truth, shared with the MRP Process Explorer.

Command Center and Process Explorer used to compute the same quantities from
different queries and disagree: on 2026-07-30 the dashboard reported 100%
on-time delivery while Process Explorer showed 10 of 13 orders past deadline,
and they counted 14 vs 8 work centers.

Reading Process Explorer's own payload makes agreement structural rather than
tested. The dependency is deliberately SOFT — mrp_process_explorer lives in a
different repository with no settled home, so this must not appear in the
manifest. When it is absent the caller degrades and says so; the screen never
reports numbers from a source other than the one it names.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

_PROVENANCE_LIVE = "mrp.process.explorer.get_data() — shared with Process Explorer"


class SouthbrookCommandExplorerBridge(models.AbstractModel):
    _name = "southbrook.command.explorer_bridge"
    _description = "Central Command — shared factory-truth reader"

    @api.model
    def fetch(self):
        """Return ``(payload, provenance)``.

        ``payload`` is Process Explorer's ``get_data()`` result, or ``{}`` when
        it cannot be trusted. ``provenance`` always names the real source.
        """
        Explorer = self.env.get("mrp.process.explorer")
        if Explorer is None:
            return {}, "fallback: mrp_process_explorer is not installed"
        try:
            data = Explorer.sudo().get_data()
        except Exception:  # noqa: BLE001 — degrade, never 500 the dashboard
            _logger.exception("command_center: explorer bridge query failed")
            return {}, "fallback: Process Explorer query failed"
        if not isinstance(data, dict) or not data.get("ok"):
            reason = (data or {}).get("reason", "unknown")
            return {}, "fallback: Process Explorer returned no data (%s)" % reason
        return data, _PROVENANCE_LIVE
