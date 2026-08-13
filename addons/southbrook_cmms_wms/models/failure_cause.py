# SPDX-License-Identifier: LGPL-3.0-only
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SouthbrookCmmsFailureCause(models.Model):
    """Why a machine stopped.

    Without this, a CMMS can say a machine failed and for how long, but never
    why — which is the question that decides whether the answer is training, a
    tooling change, a spare on the shelf, or a replacement. Every downstream
    analysis (Pareto by cause, wear-out vs infant mortality, shift clustering)
    reads this model.
    """

    _name = "southbrook.cmms.failure_cause"
    _description = "CMMS Failure Cause"
    _order = "category, sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        help="Short token used in exports and chart labels, e.g. MECH-BEAR.")
    category = fields.Selection(
        [
            ("mechanical", "Mechanical"),
            ("electrical", "Electrical"),
            ("hydraulic_pneumatic", "Hydraulic / Pneumatic"),
            ("control_software", "Control / Software"),
            ("tooling", "Tooling"),
            ("operator", "Operation"),
            ("material", "Material"),
            ("external", "External"),
            ("unknown", "Not determined"),
        ],
        required=True,
        default="mechanical",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text()
    is_preventable = fields.Boolean(
        string="Preventable",
        help="Tick when a preventive routine could have avoided this failure. "
             "This is what turns a breakdown list into a maintenance-plan "
             "argument.",
    )
    alert_count = fields.Integer(
        string="Breakdowns",
        compute="_compute_alert_count",
    )

    def _compute_alert_count(self):
        counts = {}
        if self.ids:
            grouped = self.env["southbrook.cmms.breakdown_alert"].sudo()._read_group(
                [("failure_cause_id", "in", self.ids)],
                groupby=["failure_cause_id"],
                aggregates=["__count"],
            )
            counts = {cause.id: count for cause, count in grouped}
        for rec in self:
            rec.alert_count = counts.get(rec.id, 0)

    @api.constrains("name", "active")
    def _check_unique_name(self):
        # v19 deprecates _sql_constraints; enforce in Python instead.
        for rec in self:
            if not rec.active:
                continue
            clash = self.search_count([
                ("id", "!=", rec.id),
                ("name", "=ilike", rec.name),
            ])
            if clash:
                raise ValidationError(
                    self.env._("A failure cause named %s already exists. "
                               "Rename one of them so Pareto charts do not "
                               "split the same cause in two.", rec.name))

    def action_view_alerts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Breakdowns — %s", self.name),
            "res_model": "southbrook.cmms.breakdown_alert",
            "view_mode": "list,form",
            "domain": [("failure_cause_id", "=", self.id)],
        }
