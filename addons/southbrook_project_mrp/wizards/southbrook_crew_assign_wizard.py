# SPDX-License-Identifier: LGPL-3.0-only
"""Assign crew to a kitchen job's manufacturing work.

WHY THIS NEEDED A NEW FIELD BEFORE IT COULD EXIST. `crew_gap` had two halves. The MO half
reads `mrp.production.user_id`, which is a real writable Responsible field. The work-order
half read `working_user_ids` / `last_working_user_id`, which are NOT assignment fields —
both are non-stored computes over the shop-floor clock log, with no inverse, so they
cannot be written at all. The work-order half was therefore asking "has anybody clocked
on", which is false for every job that has not physically started.

So the gap was unclosable by design: a planner could assign every operator and the flag
stayed on. `mrp.workorder.southbrook_assigned_user_id` is the missing planning fact, and
this wizard is what writes it.

WHAT IT DELIBERATELY DOES NOT DO. It never touches `working_user_ids` or
`last_working_user_id`. Those record what actually happened at the machine; writing them
to make a badge go green would put fiction into an attendance log.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SouthbrookCrewAssignWizard(models.TransientModel):
    _name = "southbrook.crew.assign.wizard"
    _description = "Assign crew to a kitchen job's manufacturing work"

    task_ids = fields.Many2many("project.task", string="Jobs", required=True)
    job_count = fields.Integer(readonly=True)
    unassigned_mo_count = fields.Integer(
        string="Manufacturing orders with no responsible", readonly=True)
    unassigned_wo_count = fields.Integer(
        string="Work orders with no assigned operator", readonly=True)
    user_id = fields.Many2one(
        "res.users", string="Assign To", required=True,
        domain=lambda self: [
            ("all_group_ids", "in", self.env.ref("mrp.group_mrp_user").id)],
        help="Only users who can work manufacturing are offered — the same domain Odoo "
             "itself puts on a manufacturing order's Responsible.")
    overwrite_existing = fields.Boolean(
        string="Also reassign work that already has someone",
        default=False,
        help="Off by default. A planner's existing assignment is a decision; replacing "
             "it silently across a whole job is how that decision gets lost.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        task_ids = self.env.context.get("active_ids") or []
        tasks = self.env["project.task"].browse(task_ids).exists()
        if not tasks:
            raise UserError(_("Select at least one job."))
        values["task_ids"] = [(6, 0, tasks.ids)]
        values["job_count"] = len(tasks)
        mos = tasks.mapped("production_ids").filtered(
            lambda m: m.state not in ("done", "cancel"))
        wos = mos.mapped("workorder_ids").filtered(
            lambda w: w.state not in ("done", "cancel"))
        values["unassigned_mo_count"] = len(mos.filtered(lambda m: not m.user_id))
        values["unassigned_wo_count"] = len(
            wos.filtered(lambda w: not w.southbrook_assigned_user_id))
        return values

    def action_assign(self):
        self.ensure_one()
        if not self.env.user.has_group("mrp.group_mrp_user"):
            raise UserError(_(
                "Assigning manufacturing crew needs the Manufacturing / User group."))

        mos = self.task_ids.mapped("production_ids").filtered(
            lambda m: m.state not in ("done", "cancel"))
        wos = mos.mapped("workorder_ids").filtered(
            lambda w: w.state not in ("done", "cancel"))
        if not self.overwrite_existing:
            mos = mos.filtered(lambda m: not m.user_id)
            wos = wos.filtered(lambda w: not w.southbrook_assigned_user_id)
        if not mos and not wos:
            raise UserError(_(
                "Nothing to assign. Every open manufacturing order and work order on "
                "the selected job(s) already has someone; tick the reassign box to "
                "replace them."))

        if mos:
            mos.write({"user_id": self.user_id.id})
        if wos:
            wos.write({"southbrook_assigned_user_id": self.user_id.id})

        # One chatter line per job, naming what changed. The five release booleans in this
        # codebase were criticised for recording a sign-off with no signer; an assignment
        # that leaves no trace of who did it or what it touched has the same problem.
        for task in self.task_ids:
            task_mos = mos.filtered(lambda m, t=task: m.project_task_id == t)
            task_wos = wos.filtered(lambda w, t=task: w.production_id in task_mos)
            task.message_post(body=_(
                "Crew assigned: %(user)s — %(mo)s manufacturing order(s), "
                "%(wo)s work order(s), by %(actor)s.",
                user=self.user_id.display_name,
                mo=len(task_mos), wo=len(task_wos),
                actor=self.env.user.display_name))

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Crew assigned"),
                "message": _(
                    "%(user)s is now on %(mo)s manufacturing order(s) and %(wo)s work "
                    "order(s). The crew gap clears on the next readiness recompute.",
                    user=self.user_id.display_name, mo=len(mos), wo=len(wos)),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
