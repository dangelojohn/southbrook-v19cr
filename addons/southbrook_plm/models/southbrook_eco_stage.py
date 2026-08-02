# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.eco.stage — the user-configurable ECO Kanban pipeline.

Stages are data, not code: a Manufacturing/PLM admin can add, reorder, or
rename them. Two flags carry behaviour:

* ``approval_required`` — leaving this stage requires the actor to be in the
  PLM Approver group (enforced in southbrook.eco.write).
* ``is_applied_stage`` — reaching this stage is the point at which the ECO's
  change is committed (BoM version bump / cut-spec activation). The Apply
  button moves the ECO here.
* ``is_approved_stage`` — the stage an ECO lands in once action_approve has
  run. Apply is gated on the ECO sitting here (see southbrook.eco.action_apply)
  so a pipeline that never routes an ECO through Approve can't slip straight
  to Applied.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SouthbrookEcoStage(models.Model):
    _name = "southbrook.eco.stage"
    _description = "Southbrook ECO Stage"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    fold = fields.Boolean(
        "Folded in Kanban",
        help="Collapse this column in the Kanban pipeline by default.",
    )
    approval_required = fields.Boolean(
        help="Advancing an ECO out of this stage requires PLM Approver rights.",
    )
    is_applied_stage = fields.Boolean(
        "Applied Stage",
        help="Reaching this stage commits the ECO's change (BoM version bump "
        "or cut-spec activation).",
    )
    is_approved_stage = fields.Boolean(
        "Approved Stage",
        help="An ECO must be sitting in a stage with this flag (i.e. it has "
        "passed approval) before Apply is permitted.",
    )
    is_final = fields.Boolean(
        "Final/Closed Stage",
        help="Terminal stage — the ECO is done (applied or rejected).",
    )
    is_rejected_stage = fields.Boolean(
        "Rejected Stage",
        help="Distinguishes the Rejected terminal stage from the Applied "
        "terminal stage. action_reject moves the ECO here.",
    )

    # ------------------------------------------------------------------
    # Delete guard
    # ------------------------------------------------------------------
    @api.ondelete(at_uninstall=False)
    def _unlink_except_referenced_by_eco(self):
        # A stage is UI-configurable data, but an ECO's stage_id is load-
        # bearing: the approval/applied/rejected flags on the CURRENT stage
        # are what action_approve / action_apply / write()'s guards inspect.
        # Deleting a stage out from under a live ECO would leave stage_id
        # pointing at nothing (or, worse, get silently nulled by the FK),
        # which strands the ECO with no is_approved_stage/is_applied_stage
        # signal at all and makes it inapplicable/unapprovable without a
        # trace of why. Reassign or move the ECOs first.
        for stage in self:
            eco_count = self.env["southbrook.eco"].search_count(
                [("stage_id", "=", stage.id)]
            )
            if eco_count:
                raise UserError(
                    _(
                        "Stage '%(stage)s' is referenced by %(count)d "
                        "Engineering Change Order(s) and cannot be deleted. "
                        "Move those ECOs to another stage first."
                    )
                    % {"stage": stage.name, "count": eco_count}
                )
