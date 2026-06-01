# SPDX-License-Identifier: LGPL-3.0-only
"""sale.order.line — variant-BoM versioning lock (Step 3).

Adds two read-only snapshot fields captured at action_confirm time so
in-flight orders are immune to ECO applies that happen after their
confirmation. See docs/CUSTOMER_TO_MANUFACTURING_FLOW.md §4 for the
full rationale.

Lives in southbrook_plm (not southbrook_estimating) because the
snapshot fields reference southbrook.cut.spec — which is a PLM-owned
model. Estimating's sale.order.action_confirm calls the capture method
via duck-typing — if PLM is installed, the capture runs; if not, the
hasattr-guarded extension is silently absent.
"""
from odoo import fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    southbrook_cut_spec_version_id = fields.Many2one(
        "southbrook.cut.spec",
        string="Cut Spec Snapshot",
        readonly=True,
        copy=False,
        ondelete="restrict",
        help="The southbrook.cut.spec record that was active when this "
        "sale.order.line was confirmed. Manufacturing reads the cut "
        "constants from this snapshot, NOT from the currently-active "
        "spec, so an ECO that activates a new cut spec mid-day does "
        "not change the panel cuts of orders confirmed before it.",
    )
    southbrook_bom_version = fields.Integer(
        string="BoM Version Snapshot",
        readonly=True,
        copy=False,
        help="The mrp.bom.southbrook_version of the line's BoM at "
        "confirmation. Mirrors the cut-spec snapshot pattern: a "
        "BoM-kind ECO that bumps the version mid-day does not "
        "retroactively change the structure of orders already "
        "confirmed.",
    )

    def _capture_southbrook_version_snapshots(self):
        """Write the cut-spec + BoM version snapshots on this line.

        Called from sale.order.action_confirm (in this addon). The
        snapshot is idempotent: if a line is somehow re-confirmed
        (which Odoo's action_confirm guards against, but defensive
        coding never hurts), the existing snapshot is preserved.

        For non-Southbrook lines (no Southbrook cabinet template + no
        configured BoM) the cut-spec snapshot is still captured —
        every kitchen line in an order shares one cut spec at confirm
        time — but the BoM version stays at 0.
        """
        # NOTE: the .sudo() recordset of a model is falsy when empty
        # (which it always is at class-handle time), so
        # `if Spec else False` short-circuits the lookup entirely.
        # _get_active() handles the no-spec case itself, so just call
        # it directly. (Bug found by test-run 2026-06-01.)
        active_spec = self.env["southbrook.cut.spec"].sudo()._get_active()
        Bom = self.env["mrp.bom"].sudo()
        for line in self:
            if line.southbrook_cut_spec_version_id:
                # Already snapshotted — preserve the original.
                continue
            vals = {}
            if active_spec:
                vals["southbrook_cut_spec_version_id"] = active_spec.id
            tmpl = line.product_id.product_tmpl_id
            if tmpl:
                # Pick the active BoM on this template (the canonical
                # one that's currently shipping). _apply_bom's archive
                # pattern guarantees there's at most one active per
                # template at a time.
                bom = Bom.search(
                    [
                        ("product_tmpl_id", "=", tmpl.id),
                        ("active", "=", True),
                    ],
                    limit=1,
                )
                if bom and bom.southbrook_version:
                    vals["southbrook_bom_version"] = bom.southbrook_version
            if vals:
                line.write(vals)
