# SPDX-License-Identifier: LGPL-3.0-only
"""
Bridge `southbrook.eco` → `pg.release`.

This is the single file in the southbrook codebase that knows about
ProductGraph. ProductGraph generic addons (``product_graph_*``) deliberately
do not know about Southbrook — Decision D1 in
``~/product_graph_v19/DECISIONS.md`` is the directional rule.

The bridge wraps :meth:`southbrook.eco.action_apply` without replacing it.
The PLM side keeps doing exactly what it did before (state machine, kind
dispatch via ``_apply_<target_kind>``, ``applied_date`` stamp, chatter).
This module just appends one extra side-effect when the operator linked
the ECO to a ``pg.ebom``: create a ``pg.release`` and execute it, which
is the ONLY supported path for ProductGraph to write a fresh ``mrp.bom``.

Failure mode (Mfg Governance §8): if the pg.release fails for any reason,
the ECO STAYS APPLIED. We do NOT roll back the ECO — the PLM side already
completed, possibly with its own ``mrp.bom`` copy or ``cut_spec`` version.
Reverting that here would corrupt the PLM audit trail. Instead we post a
chatter note flagging the failure and the Approver can retry the release
manually from the EBOM form.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SouthbrookEco(models.Model):
    _inherit = "southbrook.eco"

    # ──────────────────────────────────────────────────────────────────
    # ProductGraph linkage
    # ──────────────────────────────────────────────────────────────────
    pg_ebom_id = fields.Many2one(
        "pg.ebom",
        string="ProductGraph EBOM",
        domain="[('state','=','released')]",
        copy=False,
        help="Optional pointer to a released ProductGraph EBOM. When set, "
             "applying this ECO will also create and execute a pg.release "
             "against that EBOM, minting a fresh mrp.bom via the "
             "ProductGraph governance gate. Independent of the ECO's "
             "target_kind / Target BoM.",
    )
    pg_release_id = fields.Many2one(
        "pg.release",
        string="Triggered ProductGraph Release",
        readonly=True,
        copy=False,
        help="Read-only handle to the pg.release record created when this "
             "ECO was applied. Empty if the ECO had no pg_ebom_id or if "
             "the bridge release failed.",
    )
    pg_auto_release = fields.Boolean(
        string="Auto-trigger ProductGraph Release on Apply",
        default=True,
        help="When checked AND pg_ebom_id is set, the bridge fires a "
             "pg.release after the PLM apply step succeeds. Uncheck for "
             "ECOs where you want PLM to apply without touching MRP.",
    )

    # ──────────────────────────────────────────────────────────────────
    # The override — runs after super, never before
    # ──────────────────────────────────────────────────────────────────
    def action_apply(self):
        # 1. Let the PLM side do its whole job first.
        result = super().action_apply()

        # 2. Optionally fire a ProductGraph release per ECO.
        for eco in self:
            if not eco._should_trigger_pg_release():
                continue
            eco._trigger_pg_release()

        return result

    # ──────────────────────────────────────────────────────────────────
    # Helpers (separated for testability + clear failure semantics)
    # ──────────────────────────────────────────────────────────────────
    def _should_trigger_pg_release(self):
        self.ensure_one()
        if not self.pg_auto_release:
            return False
        if not self.pg_ebom_id:
            return False
        if self.pg_release_id:
            # Idempotency — already triggered once.
            return False
        if self.pg_ebom_id.state != "released":
            self.message_post(body=_(
                "ProductGraph release skipped: EBOM <b>%(ebom)s</b> is in "
                "state <code>%(state)s</code> (must be released).",
                ebom=self.pg_ebom_id.name,
                state=self.pg_ebom_id.state,
            ))
            return False
        return True

    def _trigger_pg_release(self):
        """Create + execute a pg.release. Failures DO NOT roll back the ECO."""
        self.ensure_one()
        reason = (
            f"Southbrook ECO {self.name}: "
            f"{(self.title or _('untitled')).strip()[:200]}"
        )
        try:
            # Savepoint so a DB-LEVEL failure (IntegrityError etc. from
            # action_execute_release) rolls back ONLY the pg.release and leaves
            # the cursor usable. Without it, a DB error poisons the cursor, the
            # message_post below then also fails, and the whole transaction —
            # INCLUDING the authoritative ECO apply from super() — rolls back,
            # defeating this method's "failures DO NOT roll back the ECO"
            # contract. (2026-07-11 review.)
            with self.env.cr.savepoint():
                release = self.env["pg.release"].create({
                    "ebom_id": self.pg_ebom_id.id,
                    "release_reason": reason,
                })
                release.action_execute_release()
        except Exception as exc:
            _logger.exception(
                "southbrook_plm_productgraph: pg.release failed for ECO %s",
                self.name,
            )
            self.message_post(body=_(
                "<strong>ProductGraph release FAILED</strong>: %(err)s.<br/>"
                "The ECO remains in <code>applied</code> state. Retry the "
                "release manually from EBOM <b>%(ebom)s</b> → Release to "
                "MRP, or investigate the failure and re-apply.",
                err=exc,
                ebom=self.pg_ebom_id.name,
            ))
            return
        # plm_lifecycle: super().action_apply() has already moved the ECO to
        # 'applied', and PLM's terminal-write immutability guard (2026-08-01
        # hardening) blocks bare writes against applied ECOs. This write IS
        # part of the sanctioned apply lifecycle — the same flag the apply
        # action itself uses.
        self.with_context(plm_lifecycle=True).write({"pg_release_id": release.id})
        self.message_post(body=_(
            "ProductGraph release <b>%(rel)s</b> executed — "
            "mrp.bom <b>%(bom)s</b> created. Frozen open MOs: %(n)d.",
            rel=release.name,
            bom=release.mrp_bom_id.display_name,
            n=release.frozen_mo_count,
        ))

    # ──────────────────────────────────────────────────────────────────
    # Smart button — open the resulting release
    # ──────────────────────────────────────────────────────────────────
    def action_view_pg_release(self):
        self.ensure_one()
        if not self.pg_release_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("ProductGraph Release"),
            "res_model": "pg.release",
            "res_id": self.pg_release_id.id,
            "view_mode": "form",
        }
