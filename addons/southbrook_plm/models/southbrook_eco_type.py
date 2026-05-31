# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.eco.type — what kind of change an ECO represents.

The ``target_kind`` selection is the load-bearing field: it drives which
target field the ECO form shows and which branch ``action_apply`` takes.

    bom       -> versions a canonical template mrp.bom
    cut_spec  -> activates a southbrook.cut.spec (the NF14 constants)
    rule      -> a config_rules.xml / code change; ECO records the git ref
    document  -> an engineering-document update only
"""
from odoo import fields, models


class SouthbrookEcoType(models.Model):
    _name = "southbrook.eco.type"
    _description = "Southbrook ECO Type"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    target_kind = fields.Selection(
        [
            ("bom", "Template BoM"),
            ("cut_spec", "Parametric Cut Spec"),
            ("rule", "Construction Rule (code / git)"),
            ("document", "Engineering Document"),
        ],
        required=True,
        default="bom",
        help="Determines which target the ECO governs and how it is applied.",
    )
    description = fields.Text()
    eco_count = fields.Integer(compute="_compute_eco_count")

    def _compute_eco_count(self):
        data = self.env["southbrook.eco"]._read_group(
            [("eco_type_id", "in", self.ids)],
            groupby=["eco_type_id"],
            aggregates=["__count"],
        )
        mapped = {t.id: c for t, c in data}
        for rec in self:
            rec.eco_count = mapped.get(rec.id, 0)

    def action_view_ecos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "southbrook.eco",
            "view_mode": "kanban,list,form",
            "domain": [("eco_type_id", "=", self.id)],
            "context": {"default_eco_type_id": self.id},
        }
