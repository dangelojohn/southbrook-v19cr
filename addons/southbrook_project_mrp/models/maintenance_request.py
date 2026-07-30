# SPDX-License-Identifier: LGPL-3.0-only
"""Make `project.task.equipment_blocked` notice maintenance tickets.

THE PROBLEM THIS SOLVES. `equipment_blocked` answers "is a work centre this job needs
sitting behind an open maintenance request". Its compute walks
project.task -> production_ids -> workorder_ids -> workcenter_id, then does a LIVE search
for maintenance.equipment on that work centre and filters its requests by
`stage_id.done`. Every hop after `workcenter_id` is a REVERSE relation, and
`@api.depends` can only express forward paths. So opening or closing a ticket changes
nothing the ORM watches, and the field could never be stored: it would freeze at whatever
it was when some unrelated write last touched the task.

Leaving it unstored had a real cost — the "Equipment Blocked" filter and its stat-button
click-through silently returned nothing, because a non-stored field with a `search=` hook
does not resolve in Odoo 19 (the hook returns a correct domain and the ORM discards it).
So the field was correct and unusable.

WHY THIS IS SAFE TO STORE NOW. The invalidation runs from the side that actually changes:
a maintenance request being created, closed, reopened, or re-linked to different
equipment. `modified(["production_ids"])` is the right call and not an approximation —
`Field.resolve_depends` yields every PREFIX of a dotted dependency, so `production_ids`
is registered as a trigger root for `equipment_blocked` exactly as it is for `job_at_risk`,
and marking it queues the field through Odoo's own protected recompute path.

The reverse lookup is indexed the whole way down — mrp.workorder.workcenter_id,
mrp.workorder.production_id and mrp.production.project_task_id all carry indexes — so this
compiles to nested indexed subqueries, not a scan of project.task. Ticket volume is
technicians opening and closing work, not barcode-scan frequency.

TWO GAPS REMAIN, BOTH DELIBERATE AND BOTH COVERED:
  * An admin flipping `done` on the maintenance.stage RECORD writes no request row, so
    nothing here fires. Rare, and the hourly readiness re-tick bounds the staleness to an
    hour rather than forever.
  * Equipment moving to a different work centre is a second reverse-relation change. That
    one is cheap to catch, so it is caught here too rather than left as a silent hole.
"""

from odoo import api, models


class MaintenanceRequest(models.Model):
    _inherit = "maintenance.request"

    # Only these two matter. The compute reads the request's stage (open vs done) and
    # which equipment it is against; it does not read anything else on the request, so
    # watching more fields would just cost recomputes for nothing.
    _SB_INVALIDATING_FIELDS = frozenset({"stage_id", "equipment_id"})

    def _sb_invalidate_equipment_blocked(self, extra_workcenters=None):
        """Queue every task whose work centres this request set touches."""
        workcenters = self.mapped("equipment_id.workcenter_id")
        if extra_workcenters:
            workcenters |= extra_workcenters
        if not workcenters:
            # Equipment with no work centre cannot block a job — nothing to do, and
            # building the domain anyway would run an `IN ()` query for no reason.
            return
        self.env["project.task"].sudo()._sb_recompute_for_workcenters(workcenters)

    def write(self, vals):
        # Capture the OLD work centres before the write: re-linking a request to
        # different equipment un-blocks the previous work centre's jobs, and after
        # super() that association is gone.
        old_workcenters = self.env["mrp.workcenter"].browse()
        if "equipment_id" in vals:
            old_workcenters = self.mapped("equipment_id.workcenter_id")
        res = super().write(vals)
        if self._SB_INVALIDATING_FIELDS & set(vals):
            self._sb_invalidate_equipment_blocked(old_workcenters)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        requests._sb_invalidate_equipment_blocked()
        return requests

    def unlink(self):
        workcenters = self.mapped("equipment_id.workcenter_id")
        res = super().unlink()
        if workcenters:
            self.env["project.task"].sudo()._sb_recompute_for_workcenters(workcenters)
        return res


class MaintenanceEquipment(models.Model):
    _inherit = "maintenance.equipment"

    def write(self, vals):
        """Moving equipment between work centres moves which jobs it blocks.

        The request-side hook cannot see this: no maintenance.request row changes when a
        machine is reassigned, yet the set of jobs affected by its open tickets changes on
        both sides. Both the old and the new work centre need requeuing.
        """
        old_workcenters = self.env["mrp.workcenter"].browse()
        if "workcenter_id" in vals:
            old_workcenters = self.mapped("workcenter_id")
        res = super().write(vals)
        if "workcenter_id" in vals:
            affected = old_workcenters | self.mapped("workcenter_id")
            if affected:
                self.env["project.task"].sudo()._sb_recompute_for_workcenters(affected)
        return res
