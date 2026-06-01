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
from odoo import _, fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # ------------------------------------------------------------------
    # Step 4 — line → ECO bridge (defined first so action shows up
    # before the snapshot fields in the file order).
    # ------------------------------------------------------------------
    def action_raise_eco(self):
        """Open a draft ECO pre-filled from this line's context.

        Per Q-B draft assumption: auto-pick target_kind=bom (the most
        common case) with the cabinet's active BoM pre-filled. The
        engineer re-targets after opening if the issue is actually a
        rule or cut spec.

        Returns an ir.actions.act_window with default_* context so the
        form opens populated:
          eco_type_id = unique bom-kind ECO type
          bom_id      = active BoM for this line's template
          title       = 'Re: <order> line #N — <product>'
          description = HTML block citing order/line/product/spec/qty
        """
        self.ensure_one()
        bom_type = self.env["southbrook.eco.type"].search(
            [("target_kind", "=", "bom")], limit=1
        )
        bom = False
        if self.product_id and self.product_id.product_tmpl_id:
            bom = self.env["mrp.bom"].sudo().search(
                [
                    ("product_tmpl_id", "=", self.product_id.product_tmpl_id.id),
                    ("active", "=", True),
                ],
                limit=1,
            )
        product_label = (
            self.product_id.display_name if self.product_id else "(no product)"
        )
        title = _("Re: %(order)s line #%(seq)s — %(product)s") % {
            "order": self.order_id.name or "(new order)",
            "seq": self.sequence or 0,
            "product": product_label,
        }
        description = _(
            "<p>Raised from <strong>%(order)s</strong> line "
            "#%(seq)s.</p>"
            "<ul>"
            "<li>Product: %(product)s</li>"
            "<li>Spec: %(name)s</li>"
            "<li>Quantity: %(qty)s</li>"
            "</ul>"
        ) % {
            "order": self.order_id.name or "(new order)",
            "seq": self.sequence or 0,
            "product": product_label,
            "name": self.name or "—",
            "qty": self.product_uom_qty or 0.0,
        }
        return {
            "type": "ir.actions.act_window",
            "name": _("Raise an Engineering Change Order"),
            "res_model": "southbrook.eco",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_eco_type_id": bom_type.id if bom_type else False,
                "default_bom_id": bom.id if bom else False,
                "default_title": title,
                "default_description": description,
            },
        }

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
