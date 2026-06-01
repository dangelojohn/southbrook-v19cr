# SPDX-License-Identifier: LGPL-3.0-only
"""sale.order extension — confirm-time snapshot trigger.

Sole concern: extend action_confirm to capture the per-line cut-spec
and BoM version snapshots immediately after Odoo's standard
action_confirm completes. The capture itself lives on
sale.order.line; this file just wires the trigger.

See docs/CUSTOMER_TO_MANUFACTURING_FLOW.md §4 for the architecture.
"""
from odoo import models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        """Confirm the order, then write the version snapshots.

        Order matters: super().action_confirm() must run first so:

          1. The order's state is 'sale' (the snapshot represents
             a confirmed commitment, not a draft preview).
          2. Any MO created by Odoo's standard flow exists before
             the snapshot fires (so Shop Copy + manufacturing reads
             see the snapshot from the start).

        If super() raises (e.g. validation), no snapshot is written
        — the order isn't confirmed, so no in-flight commitment to
        protect.
        """
        result = super().action_confirm()
        for order in self:
            order.order_line._capture_southbrook_version_snapshots()
        return result
