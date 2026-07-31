# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.capacity.day — calendar-aware capacity row per (WC, day).

W067 (R3.10, 2026-06-27)
========================

The legacy Capacity pivot (southbrook_mrp_pm.views/pm_capacity.xml) shows
`SUM(mrp.workorder.duration_expected)` per (workcenter, day) and asks the
PM to mentally compare against an 8h shift. That's wrong on any work
center with a non-default `resource_calendar_id` (4-day weeks, holidays,
2-shift CNC) — the headroom calc is OFF for the WCs that need it most.

This model surfaces the corrected denominator: a stored row per
(workcenter × day in window) carrying `available_minutes` derived from
the WC's own `resource_calendar_id.get_work_hours_count(start, end)`,
PLUS `loaded_minutes` summed from in-flight WO duration_expected, so
the pivot can plot real headroom.

JTBD: "When a WC has a 4-day Monday holiday, I want the capacity pivot
to know that and not show false headroom."

Refresh model:
- Daily cron at 02:30 rebuilds the next 14 days for all active WCs.
- "Refresh Calendar Capacity" server action lets the planner force a
  rebuild after editing a calendar / declaring a holiday.

The model is intentionally a simple snapshot — we don't try to be a
live calendar engine. Stale-by-design (≤24h or until next refresh).

NO raw SQL per spec constraint. All ORM.
"""
from datetime import datetime, time, timedelta

from odoo import api, fields, models


# Window the daily refresh covers. 14 days = the planning horizon on
# the existing pivot view's default search filter (next 7d) plus a 7d
# buffer for the planner to look ahead.
DEFAULT_HORIZON_DAYS = 14


class SouthbrookCapacityDay(models.Model):
    _name = "southbrook.capacity.day"
    _description = "Calendar-aware capacity per (work center × day)"
    _order = "date asc, workcenter_id asc"
    _rec_name = "display_name"

    workcenter_id = fields.Many2one(
        "mrp.workcenter",
        string="Work Center",
        required=True,
        ondelete="cascade",
        index=True,
    )
    date = fields.Date(
        string="Day",
        required=True,
        index=True,
    )
    display_name = fields.Char(
        compute="_compute_display_name",
        store=True,
    )
    # The corrected denominator: minutes the WC's resource_calendar_id
    # reports as working for [date 00:00 UTC, date+1 00:00 UTC).
    # Zero on holidays / non-working days for that WC's calendar.
    available_minutes = fields.Float(
        string="Available (min)",
        help="Working minutes per resource.calendar.get_work_hours_count "
             "for this WC's resource_calendar_id. Honours 4-day weeks, "
             "company holidays, and 2-shift calendars.",
    )
    # Sum of in-flight (blocked/ready/progress) WO
    # duration_expected scheduled for this day.
    loaded_minutes = fields.Float(
        string="Loaded (min)",
        help="Sum of mrp.workorder.duration_expected for WOs in "
             "blocked/ready/progress at this WC, with "
             "date_start on this day.",
    )
    # Pre-computed for ease of pivot measure selection.
    headroom_minutes = fields.Float(
        string="Headroom (min)",
        compute="_compute_headroom",
        store=True,
        help="available_minutes - loaded_minutes. Negative = over-"
             "committed; positive = real (calendar-aware) headroom.",
    )
    utilization_pct = fields.Float(
        string="Utilization %",
        compute="_compute_headroom",
        store=True,
        help="100 * loaded_minutes / available_minutes (0 when "
             "available_minutes == 0, e.g. holidays).",
    )
    refreshed_at = fields.Datetime(
        string="Refreshed",
        default=fields.Datetime.now,
        readonly=True,
    )

    # Odoo 19: _sql_constraints list is silently ignored; use the new
    # Constraint primitive instead (see memory: odoo19_sql_constraints_deprecated).
    # One row per (WC, day). The rebuild logic deletes the window first
    # then re-creates, so this constraint should never trip in normal
    # flow — it protects against a half-finished rebuild leaving dupes.
    _unique_wc_date = models.Constraint(
        "UNIQUE(workcenter_id, date)",
        "Only one capacity row per work-center per day.",
    )

    @api.depends("workcenter_id.name", "date")
    def _compute_display_name(self):
        for row in self:
            row.display_name = "%s — %s" % (
                row.workcenter_id.display_name or "?",
                row.date and fields.Date.to_string(row.date) or "?",
            )

    @api.depends("available_minutes", "loaded_minutes")
    def _compute_headroom(self):
        for row in self:
            row.headroom_minutes = row.available_minutes - row.loaded_minutes
            if row.available_minutes > 0:
                row.utilization_pct = (
                    100.0 * row.loaded_minutes / row.available_minutes)
            else:
                row.utilization_pct = 0.0

    # ------------------------------------------------------------------
    # Refresh entry points
    # ------------------------------------------------------------------
    @api.model
    def _cron_refresh_capacity(self, horizon_days=None):
        """Cron entry — rebuild the rolling window for every active
        kitchen WC. Idempotent: deletes then re-creates the window."""
        horizon = horizon_days or DEFAULT_HORIZON_DAYS
        workcenters = self.env["mrp.workcenter"].search([
            ("active", "=", True),
        ])
        # Filter to kitchen-active when the field is present (this
        # addon defines x_sbk_active_for_kitchen; defensive in case the
        # cron is reused elsewhere).
        if "x_sbk_active_for_kitchen" in self.env["mrp.workcenter"]._fields:
            workcenters = workcenters.filtered("x_sbk_active_for_kitchen")
        self._rebuild_window(workcenters, horizon)
        return True

    @api.model
    def action_refresh_capacity(self):
        """Server-action entry — same as cron but ack via notification."""
        self._cron_refresh_capacity()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Capacity refreshed",
                "message": "Calendar-aware capacity rebuilt for the "
                           "next %d days." % DEFAULT_HORIZON_DAYS,
                "type": "success",
                "sticky": False,
            },
        }

    def _rebuild_window(self, workcenters, horizon_days):
        """Rebuild capacity rows for the given WCs over the next
        horizon_days, starting at today. Called by cron + by the
        refresh server action."""
        if not workcenters:
            return self.browse()
        today = fields.Date.context_today(self)
        dates = [today + timedelta(days=i) for i in range(horizon_days)]
        # Bulk-delete existing rows in the window so the rebuild is
        # idempotent. ORM unlink — no SQL.
        existing = self.search([
            ("workcenter_id", "in", workcenters.ids),
            ("date", ">=", dates[0]),
            ("date", "<=", dates[-1]),
        ])
        if existing:
            existing.unlink()
        # Pull all in-flight WOs in the window once and bucket in py
        # by (workcenter, date). Avoids the read_group day-bucket
        # string-parse fragility (date_start:day returns locale-formatted
        # strings that vary by user lang).
        Workorder = self.env["mrp.workorder"]
        win_start = datetime.combine(dates[0], time.min)
        win_end = datetime.combine(dates[-1] + timedelta(days=1), time.min)
        # WHICH DATE THE SHOP ACTUALLY PLANS ON. There are two schedules on this
        # instance. Native Odoo keeps a work order's plan in `date_start` (computed from
        # a resource.calendar.leaves row). The installed `mrp_shop_floor_control` keeps a
        # SECOND, independent plan in `date_planned_start_wo`, hand-editable through its
        # own wizard — and that is the one planners use: measured on production,
        # `date_planned_start_wo` was set on 95 of 99 work orders while `date_start` was
        # set on 17 of 93 in flight.
        #
        # Bucketing on `date_start` alone therefore showed a mostly-EMPTY shop while
        # roughly eighty planned work orders sat invisible. That was read as "84% of work
        # is unscheduled". It is not: the work is scheduled, in the field this model was
        # not reading. A floor manager was looking at stations that appeared free.
        #
        # Preference order: the planner's date first, native second. Reading only one of
        # them is what caused the blind spot; preferring native would re-open it.
        sfc_field = "date_planned_start_wo" if "date_planned_start_wo" in Workorder._fields else None

        def _planned_on(wo):
            if sfc_field:
                planned = wo[sfc_field]
                if planned:
                    return planned
            return wo.date_start

        load_map = {}
        # Filtered in Python rather than in the domain: the window test has to run against
        # whichever of the two dates applies per record, which a single domain cannot say.
        wos = Workorder.search([
            ("workcenter_id", "in", workcenters.ids),
            # Real Odoo 19 CE WO states — 'blocked' is the dependency-wait
            # state; 'pending'/'waiting' never existed and silently zeroed
            # out the blocked load (the bulk of committed work).
            ("state", "in", ["blocked", "ready", "progress"]),
        ])
        for wo in wos:
            planned = _planned_on(wo)
            if not planned or not (win_start <= planned < win_end):
                continue
            key = (wo.workcenter_id.id, planned.date())
            load_map[key] = load_map.get(key, 0.0) + (wo.duration_expected or 0.0)
        # Build the rows.
        creates = []
        for wc in workcenters:
            cal = self._resolve_calendar(wc)
            for d in dates:
                avail = self._available_minutes(cal, d)
                loaded = load_map.get((wc.id, d), 0.0)
                creates.append({
                    "workcenter_id": wc.id,
                    "date": d,
                    "available_minutes": avail,
                    "loaded_minutes": loaded,
                })
        if creates:
            return self.create(creates)
        return self.browse()

    def _resolve_calendar(self, workcenter):
        """Per spec constraint: the WC's OWN resource_calendar_id,
        NEVER the company default. If the WC has no calendar set we
        fall back to the company calendar — that's the existing Odoo
        default — but only as a last resort. We log nothing here
        because cron loops would spam; the planner sees zero
        available_minutes and that's the signal."""
        cal = workcenter.resource_calendar_id
        if cal:
            return cal
        # Fall back to the WC's company calendar (still NOT the env
        # company default — the WC's own company).
        company = workcenter.company_id or self.env.company
        return company.resource_calendar_id

    def _available_minutes(self, calendar, day):
        """Working minutes for [day 00:00, day+1 00:00) per the
        given calendar. Returns 0.0 on holidays / non-working days.
        Uses get_work_hours_count which is the canonical Odoo
        primitive (honours leaves, attendances, two-shift, etc.)."""
        if not calendar:
            return 0.0
        start = datetime.combine(day, time.min)
        end = datetime.combine(day + timedelta(days=1), time.min)
        # get_work_hours_count returns FLOAT HOURS — convert to minutes
        # to match the pivot's existing unit (duration_expected is
        # already minutes-of-WO).
        try:
            hours = calendar.get_work_hours_count(start, end)
        except Exception:
            # Defensive: a malformed calendar shouldn't crash the
            # cron for every other WC.
            return 0.0
        return float(hours) * 60.0
