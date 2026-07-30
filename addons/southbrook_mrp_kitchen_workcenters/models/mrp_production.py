# SPDX-License-Identifier: LGPL-3.0-only
"""mrp.production — kitchen-project linkage + roll-up totals.

Adds the planner-facing context the kitchen shop floor needs that
upstream Odoo MRP doesn't carry: which kitchen project / room /
customer-facing cabinet the MO is producing, the install due date
the customer is holding us to, a complexity_factor that flows
into the M2 duration formula, and rolled-up estimated / actual /
variance minutes summed across the MO's work orders.

The x_sbk_kitchen_project_id reuses sb.kitchen.project from
southbrook_kitchen_workspace per the locked decision in the M0
discovery (no parallel project model).
"""
import logging

from odoo import _, api, fields, models


_logger = logging.getLogger(__name__)


PRIORITY_LEVELS = [
    ("urgent", "Urgent (rush)"),
    ("high", "High"),
    ("normal", "Normal"),
    ("low", "Low"),
]


class MrpProduction(models.Model):
    _inherit = ["mrp.production", "southbrook.qr.mixin"]
    _qr_kind = "mo"

    x_sbk_kitchen_project_id = fields.Many2one(
        "sb.kitchen.project",
        string="Kitchen Project",
        index=True,
        tracking=True,
        help="The Southbrook kitchen project this MO is producing for. "
             "Reuses sb.kitchen.project from southbrook_kitchen_workspace.",
    )
    x_sbk_kitchen_room = fields.Char(
        string="Room / Zone",
        tracking=True,
        help="Free-text room label (e.g. 'Kitchen', 'Island', 'Pantry', "
             "'Master Bath'). Free-text rather than enum because every "
             "project's room map is different.",
    )
    x_sbk_cabinet_code = fields.Char(
        string="Cabinet Code",
        tracking=True,
        help="Customer-facing cabinet identifier (e.g. 'B30', 'W2430', "
             "'TP3084'). Shown on the spec sheet and shop copy.",
    )
    x_sbk_install_due_date = fields.Date(
        string="Install Due",
        tracking=True,
        help="The date the customer expects on-site install. Drives "
             "scheduling priority — distinct from Odoo's date_planned "
             "which is the production start date.",
    )
    x_sbk_complexity_factor = fields.Float(
        string="Complexity Factor",
        default=1.0,
        digits=(4, 2),
        help="Multiplier flowed into the operation-template duration "
             "formula. 1.00 = standard. 1.25 = e.g. ornate moulding + "
             "non-standard finish. 0.85 = simple slab construction. "
             "Operates on the per-unit portion only; setup / changeover "
             "are unaffected.",
    )
    x_sbk_priority_level = fields.Selection(
        PRIORITY_LEVELS,
        string="Kitchen Priority",
        default="normal",
        tracking=True,
        help="Southbrook-specific priority. Distinct from upstream "
             "priority — that one drives Odoo's scheduler; this one "
             "drives the shop-floor sort order.",
    )

    # ------------------------------------------------------------------
    # Rolled-up workorder metrics
    # ------------------------------------------------------------------
    x_sbk_total_estimated_min = fields.Float(
        string="Total Estimated (min)",
        compute="_compute_x_sbk_totals",
        store=True,
        digits=(10, 2),
    )
    x_sbk_total_actual_min = fields.Float(
        string="Total Actual (min)",
        compute="_compute_x_sbk_totals",
        store=True,
        digits=(10, 2),
    )
    x_sbk_total_variance_min = fields.Float(
        string="Total Variance (min)",
        compute="_compute_x_sbk_totals",
        store=True,
        digits=(10, 2),
        help="actual − estimated. Positive = over budget.",
    )

    # SAMI PRD MO-09 (2026-06-26) — real-time MO cost roll-up.
    # Sums the per-WO labor + rework + downtime cost fields shipped in
    # 19.0.4.x. Pure labor — material cost is rolled up by Odoo native
    # mrp.production.extra_cost / cost_amount. The variance_pct is the
    # supervisor's at-a-glance signal (positive = over budget).
    x_sbk_total_estimated_cost = fields.Float(
        string="Total Estimated Cost",
        compute="_compute_x_sbk_total_costs",
        store=True,
        digits="Product Price",
    )
    x_sbk_total_actual_cost = fields.Float(
        string="Total Actual Cost",
        compute="_compute_x_sbk_total_costs",
        store=True,
        digits="Product Price",
        help="Sum of WO labor cost + rework cost + downtime cost. "
             "Native Odoo material cost is rolled up separately on "
             "the standard 'Costs' tab.",
    )
    x_sbk_total_cost_variance = fields.Float(
        string="Total Cost Variance",
        compute="_compute_x_sbk_total_costs",
        store=True,
        digits="Product Price",
        help="actual_cost − estimated_cost. Positive = over budget.",
    )
    x_sbk_cost_variance_pct = fields.Float(
        string="Variance %",
        compute="_compute_x_sbk_total_costs",
        store=True,
        digits=(8, 2),
        help="Percentage delta of actual vs estimated cost. Supervisor "
             "at-a-glance signal: >10% = investigate, >25% = ECO.",
    )

    @api.depends(
        "workorder_ids.x_sbk_kitchen_expected_min",
        "workorder_ids.duration",
    )
    def _compute_x_sbk_totals(self):
        for mo in self:
            est = sum(mo.workorder_ids.mapped("x_sbk_kitchen_expected_min"))
            act = sum(mo.workorder_ids.mapped("duration"))
            mo.x_sbk_total_estimated_min = est
            mo.x_sbk_total_actual_min = act
            mo.x_sbk_total_variance_min = act - est

    @api.depends(
        "workorder_ids.x_sbk_estimated_cost",
        "workorder_ids.x_sbk_actual_cost",
        "workorder_ids.x_sbk_rework_cost",
        "workorder_ids.x_sbk_downtime_cost",
    )
    def _compute_x_sbk_total_costs(self):
        for mo in self:
            wos = mo.workorder_ids
            est_cost = sum(wos.mapped("x_sbk_estimated_cost"))
            act_cost = (
                sum(wos.mapped("x_sbk_actual_cost"))
                + sum(wos.mapped("x_sbk_rework_cost"))
                + sum(wos.mapped("x_sbk_downtime_cost"))
            )
            mo.x_sbk_total_estimated_cost = est_cost
            mo.x_sbk_total_actual_cost = act_cost
            mo.x_sbk_total_cost_variance = act_cost - est_cost
            mo.x_sbk_cost_variance_pct = (
                ((act_cost - est_cost) / est_cost * 100.0)
                if est_cost else 0.0
            )

    def action_sbk_recalc_all_workorder_durations(self):
        """Bulk recompute kitchen-formula expected duration across all
        of this MO's work orders. The per-WO button handles a single
        operation; this one is for the planner who just changed the
        complexity factor and wants every operation re-estimated at
        once."""
        for mo in self:
            mo.workorder_ids.action_sbk_recalc_kitchen_duration()
        return True

    # SAMI PRD W-08 (2026-06-26) — auto-create as-built on MO done.
    def button_mark_done(self):
        result = super().button_mark_done()
        # Only spawn an as-built when the MO actually transitions to
        # 'done' AND no as-built exists for it yet (idempotent).
        Asbuilt = self.env["southbrook.asbuilt"]
        for mo in self:
            if mo.state != "done":
                continue
            try:
                existing = Asbuilt.search(
                    [("production_id", "=", mo.id)], limit=1)
                if existing:
                    continue
                Asbuilt.sudo().create({
                    "production_id": mo.id,
                    "built_at": fields.Datetime.now(),
                    "built_by": self.env.user.id,
                })
            except Exception as exc:  # noqa: BLE001
                # W052 (MFG-REVIEW-R1.10) — never block an MO
                # mark_done on an as-built failure, BUT do not let
                # the failure disappear silently either. Log the full
                # traceback to the server log AND post a chatter line
                # on the MO so a supervisor sees the gap and can
                # re-run as-built creation manually.
                _logger.exception(
                    "Auto as-built creation failed for MO %s (id=%s)",
                    mo.name, mo.id,
                )
                try:
                    mo.message_post(
                        body=_(
                            "Automatic as-built record creation "
                            "failed: %s. Please create the as-built "
                            "manually from the As-Built menu, or "
                            "investigate the server log for the "
                            "full traceback."
                        ) % exc,
                        message_type="comment",
                        subtype_xmlid="mail.mt_note",
                    )
                except Exception:  # noqa: BLE001
                    # Chatter post itself failing would be exotic;
                    # log it but absolutely never block mark_done.
                    _logger.exception(
                        "Chatter post for asbuilt failure also failed "
                        "on MO %s (id=%s)", mo.name, mo.id,
                    )
        return result

    def write(self, vals):
        """Push an install-date change through to the linked kitchen job.

        `project.task.job_install_due` is a stored MIN() over this field, but it cannot
        declare `production_ids.x_sbk_install_due_date` as a dependency: that field lives
        here, and southbrook_project_mrp is a SIBLING module, not a descendant. Naming it
        there makes that module fail to load anywhere this one is absent — it raises
        "Dependency field 'x_sbk_install_due_date' not found in model mrp.production" at
        registry build time.

        So the module that owns the field owns the trigger. Without this, editing the one
        field whose entire purpose is to drive install-risk would leave the task's stored
        value untouched, and the Install Risk board would keep reporting "install date
        missing" for a job whose date had just been entered — indefinitely, until some
        unrelated write to the MO happened to fire the recompute.
        """
        res = super().write(vals)
        if "x_sbk_install_due_date" in vals:
            tasks = self.mapped("project_task_id")
            if tasks:
                tasks.modified(["production_ids"])
        return res
