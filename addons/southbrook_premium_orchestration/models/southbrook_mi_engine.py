# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 1.3 — Southbrook MI Engine body.

Activates the southbrook.mi.engine model (currently a bare AbstractModel
holding helper methods) into a singleton-backed orchestrator that records
its own last-run telemetry and refires stage-gate mi.checks against in-flight
manufacturing orders on an hourly cron schedule.

The underlying southbrook_manufacturing_intelligence addon owns the model;
we extend it via _inherit. Switching the base class from AbstractModel to
Model promotes it to a stored model so the singleton + telemetry fields
can persist across runs (the helper @api.model classmethods on the parent
continue to function — they don't depend on a recordset).
"""
import logging
import time

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SouthbrookMiEngineState(models.Model):
    # The parent `southbrook.mi.engine` model in southbrook_manufacturing_intelligence
    # is an AbstractModel — Odoo 19 forbids _inherit-ing it as a Model. We use a
    # separate stored model for the singleton telemetry and call into the
    # abstract parent via env when we need its helpers.
    _name = "southbrook.mi.engine.state"
    _description = "Southbrook MI Engine — Singleton State + Cron Orchestrator"

    name = fields.Char(
        default="Southbrook Manufacturing Intelligence",
        readonly=True,
    )
    last_run_at = fields.Datetime(
        string="Last Run At",
        readonly=True,
        help="Timestamp of the most recent _cron_refire_gates execution.",
    )
    last_run_check_count = fields.Integer(
        string="Last Run Evaluations",
        readonly=True,
        help="Number of (MO × check) evaluations attempted on the last run.",
    )
    last_run_blockers = fields.Integer(
        string="Last Run Blockers",
        readonly=True,
    )
    last_run_warnings = fields.Integer(
        string="Last Run Warnings",
        readonly=True,
    )
    last_run_duration_ms = fields.Integer(
        string="Last Run Duration (ms)",
        readonly=True,
    )
    mo_in_flight_count = fields.Integer(
        string="MOs In Flight",
        compute="_compute_mo_in_flight_count",
    )
    rule_count = fields.Integer(
        string="Active Gate Rules",
        compute="_compute_rule_count",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    def _compute_mo_in_flight_count(self):
        Production = self.env["mrp.production"]
        count = Production.search_count(
            [("state", "in", ("confirmed", "progress"))]
        )
        for rec in self:
            rec.mo_in_flight_count = count

    def _compute_rule_count(self):
        Check = self.env["southbrook.mi.check"]
        # is_gate is owned by sibling agents; tolerate its absence so the
        # compute never breaks the form view in cold-install gap scenarios.
        if "is_gate" in Check._fields:
            count = Check.search_count([("is_gate", "=", True)])
        else:
            count = Check.search_count([])
        for rec in self:
            rec.rule_count = count

    # ------------------------------------------------------------------
    # Singleton resolver
    # ------------------------------------------------------------------
    @api.model
    def _get_singleton(self):
        """The one engine-state row. Creates it if absent, collapses it if not.

        This model is a singleton in intent only — nothing stopped a second row being
        created, and `search([], limit=1)` then silently picked whichever was lowest.
        In production that produced three rows: id 1 holding every real statistic, ids
        2 and 3 empty. The menu opened one of the empties, so `Run Engine Now` appeared
        to do nothing at all — it ran correctly and wrote its results to a row the user
        was not looking at. The engine was never dead; it was writing to a different
        record, which is the same class of defect as every other competing-truth bug
        in this app.

        `order="id"` makes the choice deterministic rather than incidental.
        """
        engines = self.search([], order="id")
        if not engines:
            return self.create({})
        primary = engines[0]
        extras = engines[1:]
        if extras:
            # Keep whichever row actually holds a run, not merely the lowest id.
            ran = engines.filtered("last_run_at").sorted("last_run_at", reverse=True)
            if ran and ran[0] != primary:
                primary = ran[0]
                extras = engines - primary
            _logger.warning(
                "MI engine: found %s duplicate state row(s) %s; keeping %s and "
                "removing the rest. A second row makes Run Engine Now look dead.",
                len(extras), extras.ids, primary.id)
            extras.unlink()
        return primary


    # ------------------------------------------------------------------
    # Evaluation dispatch
    # ------------------------------------------------------------------
    @api.model
    def _evaluate_check_against_mo(self, check, mo):
        """Dispatch to whatever evaluator the mi.check model exposes.

        The mi.check API surface is owned by other agents and has shifted
        across phases. Try the documented entry points in order and fall
        back to None so the cron stays best-effort.
        """
        for method_name in ("_evaluate_against", "_evaluate_for_mo", "evaluate"):
            method = getattr(check, method_name, None)
            if callable(method):
                return method(mo)
        return None

    @staticmethod
    def _severity_of(result, check):
        """Normalise a return value into ('blocker' | 'warning' | other)."""
        sev = None
        if isinstance(result, dict):
            sev = result.get("severity") or result.get("status")
        elif isinstance(result, str):
            sev = result
        if not sev and check is not None:
            sev = getattr(check, "severity", None)
        return (sev or "").lower()

    # ------------------------------------------------------------------
    # Hourly cron entry point
    # ------------------------------------------------------------------
    @api.model
    def _cron_refire_gates(self):
        """Refire every active gate check against every in-flight MO.

        Never raises: a single misbehaving check must not stall the cron and
        starve the rest of the run. Returns a summary dict for callers
        (action_run_now uses it for the on-demand button feedback).
        """
        started_ms = int(time.time() * 1000)
        engine = self._get_singleton()

        mos = self.env["mrp.production"].search(
            [("state", "in", ("confirmed", "progress"))]
        )
        Check = self.env["southbrook.mi.check"]
        if "is_gate" in Check._fields:
            checks = Check.search([("is_gate", "=", True)])
        else:
            checks = Check.search([])

        evaluated = 0
        blockers = 0
        warnings = 0

        for mo in mos:
            for check in checks:
                evaluated += 1
                try:
                    result = self._evaluate_check_against_mo(check, mo)
                except Exception as err:  # noqa: BLE001 — cron must not raise
                    _logger.warning(
                        "MI engine: evaluation of check %s against MO %s "
                        "raised %s; counted as a no-op.",
                        check.id,
                        mo.id,
                        err,
                    )
                    continue
                sev = self._severity_of(result, check)
                if sev == "blocker":
                    blockers += 1
                elif sev == "warning":
                    warnings += 1

        duration_ms = int(time.time() * 1000) - started_ms
        engine.write(
            {
                "last_run_at": fields.Datetime.now(),
                "last_run_check_count": evaluated,
                "last_run_blockers": blockers,
                "last_run_warnings": warnings,
                "last_run_duration_ms": duration_ms,
            }
        )
        _logger.info(
            "MI engine refire: %d MOs × %d checks = %d evaluations, "
            "%d blockers, %d warnings, %d ms",
            len(mos),
            len(checks),
            evaluated,
            blockers,
            warnings,
            duration_ms,
        )
        return {
            "checked": evaluated,
            "blockers": blockers,
            "warnings": warnings,
            "duration_ms": duration_ms,
        }

    # ------------------------------------------------------------------
    # Form button — on-demand kick
    # ------------------------------------------------------------------
    def action_run_now(self):
        """Trigger _cron_refire_gates from the form view button.

        Returns a reload, not just a toast. The previous version wrote the statistics
        correctly and returned a notification, so the form kept rendering the values it
        had loaded with — indistinguishable, from the user's seat, from a no-op.
        """
        self.ensure_one()
        summary = self._cron_refire_gates()
        # Make sure the row the user is looking at is the row that was written.
        primary = self._get_singleton()
        if primary != self:
            return {
                "type": "ir.actions.act_window",
                "res_model": self._name,
                "res_id": primary.id,
                "view_mode": "form",
                "target": "current",
            }
        self.invalidate_recordset()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "MI Engine",
                "message": (
                    "Refired %(checked)s evaluations in %(duration_ms)s ms "
                    "(%(blockers)s blockers, %(warnings)s warnings)."
                ) % summary,
                "type": "success" if not summary["blockers"] else "warning",
                "sticky": False,
            },
        }
