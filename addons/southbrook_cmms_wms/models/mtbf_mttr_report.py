# SPDX-License-Identifier: LGPL-3.0-only
import logging
from datetime import datetime, time, timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SouthbrookCmmsMtbfMttrReport(models.Model):
    _name = "southbrook.cmms.mtbf_mttr_report"
    _description = "CMMS MTBF / MTTR Report"
    _inherit = ["mail.thread"]
    _order = "as_of_date desc, id desc"

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        default=lambda self: self._default_name(),
        tracking=True,
    )
    equipment_id = fields.Many2one(
        "maintenance.equipment",
        string="Equipment",
        required=True,
        ondelete="cascade",
        tracking=True,
    )
    as_of_date = fields.Date(
        string="As Of",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    period_days = fields.Integer(
        string="Period (days)",
        default=90,
        tracking=True,
    )
    breakdown_count = fields.Integer(
        string="Breakdowns",
        compute="_compute_metrics",
        store=True,
    )
    total_downtime_hours = fields.Float(
        string="Downtime (h)",
        compute="_compute_metrics",
        store=True,
    )
    total_runtime_hours = fields.Float(
        string="Runtime (h)",
        compute="_compute_metrics",
        store=True,
    )
    mtbf_hours = fields.Float(
        string="MTBF (h)",
        compute="_compute_metrics",
        store=True,
        help="Mean time between failures = runtime / max(1, breakdowns).",
    )
    mttr_hours = fields.Float(
        string="MTTR (h)",
        compute="_compute_metrics",
        store=True,
        help="Mean time to repair = downtime / max(1, breakdowns).",
    )
    availability_pct = fields.Float(
        string="Availability (%)",
        compute="_compute_metrics",
        store=True,
    )

    @api.model
    def _default_name(self):
        seq = self.env["ir.sequence"].next_by_code("southbrook.cmms.mtbf") or "0001"
        today = fields.Date.context_today(self)
        return "MTBF/%s/%s" % (today, seq)

    @api.depends("equipment_id", "as_of_date", "period_days")
    def _compute_metrics(self):
        """Count failures from breakdown alerts first, requests second.

        This model used to read `maintenance.request` alone, which had two
        consequences worth stating plainly:

        1. An alert only reached these numbers if somebody pressed Dispatch,
           because that is the sole code path creating a request. An alert
           logged and closed on the floor was invisible to MTBF, MTTR and
           availability.
        2. Request dates are Dates, so downtime was `(days) * 24` — a repair
           opened and closed the same day scored ZERO downtime and 100%
           availability, indistinguishable from no failure at all.

        Alerts carry `reported_at` / `fixed_at` Datetimes, so outages are now
        measured to the second. Corrective requests raised directly in the
        Maintenance app still count, at the Date precision they carry, unless
        an alert already accounts for them.
        """
        Alert = self.env["southbrook.cmms.breakdown_alert"].sudo()
        Request = self.env["maintenance.request"].sudo()
        now = fields.Datetime.now()
        for rec in self:
            if not rec.equipment_id or not rec.as_of_date or not rec.period_days:
                rec.breakdown_count = 0
                rec.total_downtime_hours = 0.0
                rec.total_runtime_hours = 0.0
                rec.mtbf_hours = 0.0
                rec.mttr_hours = 0.0
                rec.availability_pct = 0.0
                continue
            period_start = rec.as_of_date - timedelta(days=rec.period_days)
            period_hours = float(rec.period_days) * 24.0
            window_start = datetime.combine(period_start, time.min)
            window_end = datetime.combine(rec.as_of_date, time.max)

            alerts = Alert.search([
                ("equipment_id", "=", rec.equipment_id.id),
                ("reported_at", ">=", window_start),
                ("reported_at", "<=", window_end),
            ])
            downtime_h = 0.0
            for alert in alerts:
                if not alert.reported_at:
                    continue
                start = max(alert.reported_at, window_start)
                # An unfixed alert is still down: accrue to now, clamped to the
                # window so a back-dated report never counts future downtime.
                end = min(alert.fixed_at or now, window_end)
                if end > start:
                    downtime_h += (end - start).total_seconds() / 3600.0

            # Requests raised straight in the Maintenance app, with no alert.
            linked = [r for r in alerts.mapped("maintenance_request_id").ids if r]
            orphans = Request.search([
                ("equipment_id", "=", rec.equipment_id.id),
                ("maintenance_type", "=", "corrective"),
                ("request_date", ">=", period_start),
                ("request_date", "<=", rec.as_of_date),
                ("id", "not in", linked),
            ])
            for req in orphans:
                start = req.request_date
                end = req.close_date or fields.Date.context_today(rec)
                if start and end:
                    # Clamp the outage to the report window. A still-open request
                    # (end=today) on a BACK-DATED report otherwise counted
                    # downtime past as_of_date, exceeding period_hours and forcing
                    # runtime/MTBF to 0.
                    start = max(start, period_start)
                    end = min(end, rec.as_of_date)
                    if end >= start:
                        # request_date / close_date are Date in v19; days * 24 h.
                        delta_days = (end - start).days
                        downtime_h += max(0.0, delta_days) * 24.0

            # Downtime cannot exceed the window it is measured in.
            downtime_h = min(downtime_h, period_hours)
            rec.breakdown_count = len(alerts) + len(orphans)
            rec.total_downtime_hours = downtime_h
            rec.total_runtime_hours = max(0.0, period_hours - downtime_h)
            denom = max(1, rec.breakdown_count)
            rec.mtbf_hours = rec.total_runtime_hours / denom
            rec.mttr_hours = rec.total_downtime_hours / denom
            rec.availability_pct = (
                (rec.total_runtime_hours / period_hours) * 100.0
                if period_hours else 0.0
            )

    def action_compute(self):
        self.invalidate_recordset([
            "breakdown_count", "total_downtime_hours", "total_runtime_hours",
            "mtbf_hours", "mttr_hours", "availability_pct",
        ])
        self._compute_metrics()
        return True

    @api.model
    def _cron_generate_daily_reports(self):
        Equipment = self.env["maintenance.equipment"].sudo()
        today = fields.Date.context_today(self)
        created = self.env[self._name]
        for eq in Equipment.search([("active", "=", True)]):
            # Upsert one report per (equipment, day). The cron formerly created a
            # fresh row every run → unbounded growth, and the 30-day MI tile then
            # counted each equipment ~30x in its per-row average.
            rec = self.sudo().search([
                ("equipment_id", "=", eq.id),
                ("as_of_date", "=", today),
            ], limit=1)
            if rec:
                rec.action_compute()
            else:
                rec = self.sudo().create({
                    "equipment_id": eq.id,
                    "as_of_date": today,
                    "period_days": 90,
                })
            created |= rec
        _logger.info("CMMS daily MTBF/MTTR sweep: %d reports", len(created))
        return len(created)
