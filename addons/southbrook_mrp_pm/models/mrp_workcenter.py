# SPDX-License-Identifier: LGPL-3.0-only
"""mrp.workcenter extension — PM KPI computed fields.

M10 (Manufacturing PM JTBD 2026-06-01): the JTBD analysis called
out 'no consolidated PM dashboard'. The Odoo standard mrp module
has computed counts (workorder_ready_count etc.) but they're
generic shop-floor counters, not framed for the PM's daily walk.

This commit adds 4 computed scalars per work center that the M10
kanban dashboard renders as a tile per station. All four refresh
live (no stored cache) — the PM hits the dashboard and sees the
current state without a manual refresh.

  southbrook_pm_inflight_count    work orders at this station in
                                   blocked/ready/progress

  southbrook_pm_throughput_today   work orders at this station
                                   that finished today (state=done
                                   AND date_finished >= today 00:00)

  southbrook_pm_late_count         MOs with at least one work order
                                   at this station whose date_deadline
                                   has passed and which aren't done

  southbrook_pm_equipment_alerts   maintenance.equipment records at
                                   this station whose
                                   southbrook_condition is NOT 'good'
                                   (fair / watch / critical / offline)

Each is a depends-light computed field — the PM rebuild trigger is
hitting the dashboard, not Odoo's compute invalidation. The
@api.depends keys are intentionally loose because we don't want
mrp.workorder.write() events to trigger workcenter recomputes
across the whole shop on every barcode scan.
"""
from datetime import datetime, time

from odoo import _, api, fields, models


_ALERT_CONDITIONS = ("fair", "watch", "critical", "offline")


# Same gate states the floor portal uses. Keeping the constant local
# here avoids cross-module imports for what's effectively a literal.
# These are the REAL Odoo 19 CE mrp.workorder.state values — 'blocked'
# is the dependency-wait state (there is no 'pending'/'waiting'). The
# prior tokens dropped every blocked WO from the in-flight count.
_IN_FLIGHT_WO_STATES = ("blocked", "ready", "progress")


class MrpWorkcenter(models.Model):
    _inherit = "mrp.workcenter"

    southbrook_pm_inflight_count = fields.Integer(
        string="In-Flight WOs",
        compute="_compute_southbrook_pm_kpis",
        help=(
            "Work orders queued at this station — blocked / ready / "
            "progress. The 'how busy is this station right "
            "now' metric."
        ),
    )

    southbrook_pm_throughput_today = fields.Integer(
        string="Done Today",
        compute="_compute_southbrook_pm_kpis",
        help=(
            "Work orders completed at this station since midnight. "
            "Coarse-grain throughput — fine-grain OEE comes once "
            "the timer-row infrastructure is in place."
        ),
    )

    southbrook_pm_late_count = fields.Integer(
        string="Late MOs",
        compute="_compute_southbrook_pm_kpis",
        help=(
            "Manufacturing orders with at least one work order at "
            "this station whose date_deadline has passed and which "
            "are not yet done."
        ),
    )

    # P1 fix (2026-07-31, see scratchpad/retest/p1-planner.md) — the
    # Late tile on the PM Dashboard kanban was hardcoding the class
    # `o_sb_pm_dash_late` directly in the QWeb card template
    # (views/pm_dashboard.xml), so dashboard.scss's
    # `.o_sb_pm_dash_late .o_sb_pm_dash_value { color: red; }` rule
    # applied unconditionally — the tile read as "late" even at
    # southbrook_pm_late_count == 0. dashboard.scss's own old comment
    # admitted this ("the kanban renderer doesn't pass conditional
    # class support without an OWL component") but that premise is
    # false: this addon's own mrp_production_kanban.xml already proves
    # a plain QWeb kanban `<t t-name="card">` CAN bind a class to a
    # record value via t-attf-class (see o_sb_mo_state_#{...} there).
    # Rather than push a ternary into the XML template (OWL's kanban
    # tokenizer is picky about inline expressions — see
    # owl_tokenizer_constraints.md), this field does the branching in
    # Python where it's trivially unit-testable, and the template just
    # interpolates the field's raw_value.
    southbrook_pm_late_state = fields.Selection(
        [("ok", "On Track"), ("late", "Late")],
        string="Late Tile State",
        compute="_compute_southbrook_pm_kpis",
        help=(
            "Drives the Late tile's colour on the PM Dashboard. 'ok' "
            "when southbrook_pm_late_count is 0, 'late' otherwise — "
            "a permanently-red tile trains the PM to ignore the one "
            "signal that should never be ignored, so this must never "
            "be a static/always-on class again."
        ),
    )

    southbrook_pm_equipment_alerts = fields.Integer(
        string="Equipment Alerts",
        compute="_compute_southbrook_pm_kpis",
        help=(
            "Equipment at this station whose condition is anything "
            "other than 'good' — fair / watch / critical / offline. "
            "Tap the workcenter to see which machines."
        ),
    )

    @api.depends_context("uid")
    def _compute_southbrook_pm_kpis(self):
        Wo = self.env["mrp.workorder"].sudo()
        Mo = self.env["mrp.production"].sudo()
        Eq = self.env["maintenance.equipment"].sudo()

        today_start = fields.Datetime.to_string(
            datetime.combine(fields.Date.context_today(self), time.min)
        )
        now = fields.Datetime.now()

        for wc in self:
            wc.southbrook_pm_inflight_count = Wo.search_count([
                ("workcenter_id", "=", wc.id),
                ("state", "in", list(_IN_FLIGHT_WO_STATES)),
            ])
            wc.southbrook_pm_throughput_today = Wo.search_count([
                ("workcenter_id", "=", wc.id),
                ("state", "=", "done"),
                ("date_finished", ">=", today_start),
            ])
            wc.southbrook_pm_late_count = Mo.search_count([
                ("state", "not in", ["done", "cancel"]),
                ("date_deadline", "<", now),
                ("workorder_ids.workcenter_id", "=", wc.id),
            ])
            wc.southbrook_pm_late_state = (
                "late" if wc.southbrook_pm_late_count else "ok"
            )
            wc.southbrook_pm_equipment_alerts = Eq.search_count([
                ("workcenter_id", "=", wc.id),
                ("southbrook_condition", "in", list(_ALERT_CONDITIONS)),
            ])

    # ==================================================================
    # W051 (R7.6) — equipment-alerts tile clickable + cross-link to MOs
    # ==================================================================
    #
    # Before this commit the equipment-alerts count on the PM dashboard
    # was a decorative integer — the PM saw "3 alerts" but had to
    # navigate manually to Maintenance, filter by workcenter, then
    # cross to MO impact. Three actions wire that path:
    #
    #   action_view_equipment_alerts     opens the alerted equipment
    #                                    list (Fair / Watch / Critical
    #                                    / Offline) for this workcenter.
    #                                    From there the existing smart
    #                                    button (M14) cross-links to
    #                                    impacted MOs.
    #   action_view_impacted_productions roll-up: opens every in-flight
    #                                    MO whose workorders use this
    #                                    workcenter (one extra click
    #                                    saved).
    #   action_view_inflight_workorders  opens the WO list for this
    #                                    workcenter — consistent with
    #                                    the In-Queue tile already
    #                                    treated as a counter.

    def action_view_equipment_alerts(self):
        """Open the list of alerted equipment (condition != good) at
        this workcenter. Drives the W051 click-through from the PM
        dashboard tile to the maintenance backlog."""
        self.ensure_one()
        Eq = self.env["maintenance.equipment"]
        # I2 (Round-2 misc fix): emit tuple values for `in` operators so
        # that test helpers which hash the domain (e.g. `{tuple(t) for t
        # in action["domain"]}`) don't blow up with `unhashable type:
        # list`. Odoo accepts both list and tuple values in domain leafs.
        domain = [
            ("workcenter_id", "=", self.id),
            ("southbrook_condition", "in", tuple(_ALERT_CONDITIONS)),
        ]
        return {
            "type": "ir.actions.act_window",
            "name": _("Equipment Alerts — %s") % self.name,
            "res_model": "maintenance.equipment",
            "view_mode": "list,form,kanban",
            "domain": domain,
            "context": {
                "search_default_group_by_southbrook_condition": 1,
                "default_workcenter_id": self.id,
            },
            "help": _(
                "<p>No equipment alerts on %s.</p>"
                "<p>This list shows machines whose southbrook_condition "
                "is anything other than 'good'. From here the Impacted "
                "MOs smart button on each equipment opens the in-flight "
                "manufacturing orders the alert is putting at risk.</p>"
            ) % self.name,
        }

    def action_view_impacted_productions(self):
        """Open in-flight MOs whose workorders use this workcenter.
        W051 (R7.6) cross-link — saves the PM going Equipment -> smart
        button -> Impacted MOs path when they just want the MO roll-up."""
        self.ensure_one()
        MO_STATES = ("draft", "confirmed", "progress", "to_close")
        return {
            "type": "ir.actions.act_window",
            "name": _("MOs at %s") % self.name,
            "res_model": "mrp.production",
            "view_mode": "list,form,kanban",
            "domain": [
                # See I2 note in action_view_equipment_alerts — tuple
                # values stay hashable when tests bucket the domain.
                ("state", "in", tuple(MO_STATES)),
                ("workorder_ids.workcenter_id", "=", self.id),
            ],
            "context": {
                "search_default_group_by_state": 1,
            },
        }

    def action_view_inflight_workorders(self):
        """Open the in-flight WO list for this workcenter. Companion to
        the In Queue tile — keeps the dashboard's interaction model
        consistent across all four tiles."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("In-Flight WOs — %s") % self.name,
            "res_model": "mrp.workorder",
            "view_mode": "list,form,kanban",
            "domain": [
                ("workcenter_id", "=", self.id),
                # See I2 note in action_view_equipment_alerts.
                ("state", "in", tuple(_IN_FLIGHT_WO_STATES)),
            ],
        }
