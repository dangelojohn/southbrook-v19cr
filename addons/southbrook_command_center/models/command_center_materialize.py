# SPDX-License-Identifier: LGPL-3.0-only
"""Safe scan-based materializer for ``southbrook.command.exception``.

DELIVERABLE_7_ROADMAP.md Phase 1/2 — the *low-risk* materialization path.
Instead of overriding ``create``/``write`` on live business models
(``sale.order``, ``project.task``, ``southbrook.mi.check`` …), which is the
intrusive Phase-2 surface that must be integration-tested before it touches
production writes, this reads the alerting sources and upserts Central
Command's OWN exception rows. It writes to no model other than
``southbrook.command.exception``.

Invoked by an ``ir.cron`` (>=15 min, per the no-fast-cron constraint) and by a
manual "Refresh Exceptions" button. Idempotent: keyed on the model's
``unique(source_model, source_res_id, exception_type)`` constraint, so a
re-scan updates rather than duplicates.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# southbrook.mi.check.severity -> (this model's severity, rank)
_MI_SEV = {"info": ("low", 3), "warning": ("medium", 2), "blocker": ("critical", 0)}
_BREAKDOWN_SEV = {"low": ("low", 3), "medium": ("medium", 2),
                  "high": ("high", 1), "critical": ("critical", 0)}
_NCR_SEV = {"minor": ("low", 3), "major": ("medium", 2), "critical": ("critical", 0)}


class SouthbrookCommandExceptionMaterialize(models.Model):
    _inherit = "southbrook.command.exception"

    # ------------------------------------------------------------------
    # Owner resolution (OQ-8): natural responsible user if resolvable,
    # else the admin user as a guaranteed non-null default (owner_id is
    # required). Never raises.
    # ------------------------------------------------------------------
    def _default_owner_id(self, source_record=None):
        for attr in ("user_id", "reported_by", "responsible_id"):
            user = getattr(source_record, attr, False) if source_record else False
            if user:
                return user.id
        # OQ-8 (resolved 2026-07-05): no natural owner -> configurable
        # fallback (default: production manager), then admin as last resort
        # so the required owner_id is never null.
        login = self.env["ir.config_parameter"].sudo().get_param(
            "command_center.default_owner_login", "")
        if login:
            fallback = self.env["res.users"].sudo().search(
                [("login", "=", login)], limit=1)
            if fallback:
                return fallback.id
        admin = self.env.ref("base.user_admin", raise_if_not_found=False)
        return admin.id if admin else self.env.uid

    # ------------------------------------------------------------------
    # Idempotent upsert keyed on the stable business identity.
    # ------------------------------------------------------------------
    def _upsert_exception(self, exception_type, source_model, source_res_id, vals,
                          reactivate=False, dedupe_key=None):
        """Idempotent create-or-update keyed on the stable business identity.

        On an EXISTING OPEN row only the narrative (severity/rank/impact/
        action/why) is refreshed — owner and workflow state set by a human are
        preserved. A resolved/dismissed row is reactivated ONLY when
        ``reactivate=True`` (event hooks: the source defect genuinely
        re-fired). The cron scan passes ``reactivate=False`` so it never undoes
        a human's resolution of a still-present blocker.

        ``source_res_id`` is refreshed to the live source row on every upsert,
        so click-through always opens a record that still exists — before this,
        18,240 rows pointed at deleted mi.check ids.
        """
        Exc = self.sudo().with_context(active_test=False)
        key = dedupe_key or self._dedupe_key_for(
            exception_type, source_model, source_res_id)
        existing = Exc.search([("dedupe_key", "=", key)], limit=1)
        if not existing:
            # Pre-migration rows have no key yet; fall back to the legacy triple
            # so an upgrade mid-flight never duplicates.
            existing = Exc.search([
                ("source_model", "=", source_model),
                ("source_res_id", "=", source_res_id),
                ("exception_type", "=", exception_type),
                ("dedupe_key", "=", False),
            ], limit=1)
        narrative = {k: vals[k] for k in
                     ("severity", "severity_rank", "impact_summary",
                      "recommended_action", "why_text") if k in vals}
        if existing:
            narrative["dedupe_key"] = key
            narrative["source_res_id"] = source_res_id
            if not existing.active and reactivate:
                existing.write({**narrative, "active": True, "state": "new"})
            else:
                existing.write(narrative)
            return existing
        create_vals = dict(vals)
        create_vals.update({
            "exception_type": exception_type,
            "source_model": source_model,
            "source_res_id": source_res_id,
            "dedupe_key": key,
        })
        return Exc.create(create_vals)

    def _resolve_exception(self, exception_type, source_model, source_res_id):
        """Mark the matching OPEN exception resolved (no-op if none). Used by
        hooks that observe a source record leave its exception-worthy state
        (e.g. approval granted / force-release set). Reuses action_resolve()."""
        existing = self.sudo().search([
            ("source_model", "=", source_model),
            ("source_res_id", "=", source_res_id),
            ("exception_type", "=", exception_type),
            ("active", "=", True),
        ], limit=1)
        if existing:
            existing.action_resolve()
        return existing

    @api.model
    def _cc_hooks_enabled(self):
        """Kill-switch for the event-driven Phase-2 hooks. When
        ``command_center.hooks_enabled`` != '1' every create/write override is
        a pure pass-through — an instant, uninstall-free disable/rollback. The
        cron-scan materializer is unaffected (always on)."""
        return self.env["ir.config_parameter"].sudo().get_param(
            "command_center.hooks_enabled", "1") == "1"

    def _publish(self, channel_suffix, ntype, payload):
        """``bus.bus._sendone`` on ``<channel_suffix>_<company_id>`` (per
        company, DELIVERABLE_5_DELIVERY.md §1). Always try/except-wrapped and
        logged — a bus failure must never roll back the business write it
        observes. (No delivery until Phase 3's Caddy websocket split, OQ-7;
        publishing is a cheap no-op until then.)"""
        try:
            company_id = payload.get("company_id") or self.env.company.id
            channel = "%s_%s" % (channel_suffix, company_id)
            self.env["bus.bus"].sudo()._sendone(channel, ntype, payload)
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Central Command bus publish failed (channel_suffix=%s, "
                "notification_type=%s) — non-fatal", channel_suffix, ntype)

    # ------------------------------------------------------------------
    # The scan. Each source wrapped independently so one failure never
    # aborts the others or raises out of the cron.
    # ------------------------------------------------------------------
    @api.model
    def _scan_and_materialize(self):
        created = 0
        created += self._scan_mi_blockers()
        created += self._scan_breakdown_alerts()
        created += self._scan_ncrs()
        _logger.info("command_center: materialize scan complete (%s upserts)", created)
        return created

    def _scan_mi_blockers(self):
        n = 0
        try:
            checks = self.env["southbrook.mi.check"].sudo().search(
                [("severity", "=", "blocker"), ("active", "=", True)], limit=500)
            for c in checks:
                is_cut = getattr(c, "category", False) in ("cut", "cutlist")
                etype = "cutlist_divergence" if is_cut else "mi_blocker"
                mo = getattr(c, "production_id", False)
                self._upsert_exception(etype, "southbrook.mi.check", c.id, {
                    "exception_type": etype,
                    "source_model": "southbrook.mi.check",
                    "source_res_id": c.id,
                    "severity": "critical", "severity_rank": 0,
                    "owner_id": self._default_owner_id(mo or c),
                    "mo_id": mo.id if mo else False,
                    "impact_summary": (c.name or "MI blocker")[:280],
                    "recommended_action": getattr(c, "recommendation", False) or
                        "Review the MI check and clear the blocker.",
                    "why_text": getattr(c, "message", False) or c.name or "",
                    "company_id": self.env.company.id,
                })
                n += 1
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: mi_blocker scan failed")
        return n

    def _scan_breakdown_alerts(self):
        n = 0
        try:
            alerts = self.env["southbrook.cmms.breakdown_alert"].sudo().search(
                [("severity", "in", ["high", "critical"])], limit=500)
            for a in alerts:
                sev, rank = _BREAKDOWN_SEV.get(a.severity, ("high", 1))
                wc = getattr(a, "workcenter_id", False)
                self._upsert_exception("breakdown_alert",
                                       "southbrook.cmms.breakdown_alert", a.id, {
                    "exception_type": "breakdown_alert",
                    "source_model": "southbrook.cmms.breakdown_alert",
                    "source_res_id": a.id,
                    "severity": sev, "severity_rank": rank,
                    "owner_id": self._default_owner_id(a),
                    "workcenter_id": wc.id if wc else False,
                    "impact_summary": (a.name or "Equipment breakdown")[:280],
                    "recommended_action": "Dispatch maintenance to the affected workcenter.",
                    "why_text": getattr(a, "description", False) or a.name or "",
                    "company_id": self.env.company.id,
                })
                n += 1
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: breakdown scan failed")
        return n

    def _scan_ncrs(self):
        n = 0
        try:
            ncrs = self.env["southbrook.ncr"].sudo().search(
                [("severity", "in", ["major", "critical"])], limit=500)
            for r in ncrs:
                sev, rank = _NCR_SEV.get(r.severity, ("medium", 2))
                mo = getattr(r, "production_id", False)
                self._upsert_exception("quality_ncr", "southbrook.ncr", r.id, {
                    "exception_type": "quality_ncr",
                    "source_model": "southbrook.ncr",
                    "source_res_id": r.id,
                    "severity": sev, "severity_rank": rank,
                    "owner_id": self._default_owner_id(mo or r),
                    "mo_id": mo.id if mo else False,
                    "impact_summary": (r.name or "Quality NCR")[:280],
                    "recommended_action": "Investigate the non-conformance and disposition.",
                    "why_text": getattr(r, "description", False) or r.name or "",
                    "company_id": self.env.company.id,
                })
                n += 1
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: ncr scan failed")
        return n

    # Cron entrypoint (defensive — a cron must never raise).
    @api.model
    def _cron_materialize_exceptions(self):
        try:
            self._scan_and_materialize()
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: materialize cron failed")

    # Manual button (from the exception list view header).
    def action_refresh_exceptions(self):
        self._scan_and_materialize()
        return True
