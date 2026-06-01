# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.eco.stage — the user-configurable ECO Kanban pipeline.

Stages are data, not code: a Manufacturing/PLM admin can add, reorder, or
rename them. Two flags carry behaviour:

* ``approval_required`` — leaving this stage requires the actor to be in the
  PLM Approver group (enforced in southbrook.eco.write).
* ``is_applied_stage`` — reaching this stage is the point at which the ECO's
  change is committed (BoM version bump / cut-spec activation). The Apply
  button moves the ECO here.
"""
from odoo import fields, models


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
    is_final = fields.Boolean(
        "Final/Closed Stage",
        help="Terminal stage — the ECO is done (applied or rejected).",
    )
    is_rejected_stage = fields.Boolean(
        "Rejected Stage",
        help="Distinguishes the Rejected terminal stage from the Applied "
        "terminal stage. action_reject moves the ECO here.",
    )
