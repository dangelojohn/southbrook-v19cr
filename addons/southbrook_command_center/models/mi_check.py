# SPDX-License-Identifier: LGPL-3.0-only
"""``southbrook.mi.check`` — Central Command materialize-on-create hook.

Phase-2 event-driven materialization. Gated by the ``command_center.hooks_enabled``
kill-switch (``_cc_hooks_enabled()``); when off, this override is a pure
pass-through and the cron-scan materializer still populates exceptions.

Verified against ``addons/southbrook_manufacturing_intelligence/models/mi_check.py``:
severity (info/warning/blocker), category (incl. ``cut``), production_id
(mrp.production, optional), message/recommendation/name. No base ``create``
override exists, so ``_inherit`` is safe. A ``category == 'cut'`` check
materializes as ``cutlist_divergence`` (DELIVERABLE_6_WORKFLOW.md §3), else
``mi_blocker``.
"""
import logging

from odoo import api, models

from .southbrook_command_exception import SEVERITY_HARMONIZATION

_logger = logging.getLogger(__name__)


class SouthbrookMiCheck(models.Model):
    _inherit = "southbrook.mi.check"

    @api.model_create_multi
    def create(self, vals_list):
        checks = super().create(vals_list)
        if not self.env["southbrook.command.exception"]._cc_hooks_enabled():
            return checks
        for check in checks:
            try:
                check._sb_cc_materialize()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Central Command materialize failed for "
                    "southbrook.mi.check %s — non-fatal", check.id,
                )
        return checks

    def _sb_cc_materialize(self):
        self.ensure_one()
        severity, severity_rank = SEVERITY_HARMONIZATION["mi_check"].get(
            self.severity, ("medium", 2)
        )
        exception_type = (
            "cutlist_divergence" if self.category == "cut" else "mi_blocker"
        )
        production = self.production_id
        company_id = (
            production.company_id.id
            if production and production.company_id
            else self.env.company.id
        )
        Exception = self.env["southbrook.command.exception"]
        owner_id = Exception._default_owner_id(production or self)
        production_name = production.name if production else "(no linked MO)"
        impact_summary = (
            "MI check '%s' (%s/%s) on %s: %s"
            % (self.name, self.category, self.severity, production_name,
               (self.message or "")[:120])
        )[:280]
        why_text = (
            "southbrook.mi.check %s: category=%s severity=%s message=%s"
            % (self.name, self.category, self.severity, self.message or "")
        )
        vals = {
            "severity": severity,
            "severity_rank": severity_rank,
            "owner_id": owner_id,
            "mo_id": production.id if production else False,
            "company_id": company_id,
            "impact_summary": impact_summary,
            "recommended_action": (
                self.recommendation
                or "Review MI check on the shop floor and resolve the "
                   "underlying condition."
            ),
            "why_text": why_text,
        }
        key = Exception._dedupe_key_for(
            exception_type, "southbrook.mi.check", self.id, self)
        exc = Exception._upsert_exception(
            exception_type, "southbrook.mi.check", self.id, vals,
            dedupe_key=key
        )
        exc._publish(
            "sb_cc_exceptions",
            "exception_upsert",
            {
                "exception_id": exc.id,
                "exception_type": exception_type,
                "severity": severity,
                "severity_rank": severity_rank,
                "state": exc.state,
                "owner_id": owner_id,
                "mo_id": production.id if production else False,
                "task_id": False,
                "company_id": company_id,
                "write_date": exc.write_date.isoformat() if exc.write_date else None,
            },
        )
