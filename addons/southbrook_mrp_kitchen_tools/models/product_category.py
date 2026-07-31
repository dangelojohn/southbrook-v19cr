# SPDX-License-Identifier: LGPL-3.0-only
"""product.category extension — reverse substrate lens (safety
three-state remediation, 19.0.2.0.0).

Southbrook tool/consumable products can declare which BASE Odoo
material categories (product.category — MDF, plywood, melamine, etc.)
they are approved for, via x_southbrook_compatible_material_ids on
product.template (product_template.py, "Lifecycle / linkage" block).
That is a one-way lens: pick a tool, see what it is rated for.

This file adds the reverse lens on product.category itself: pick a
material category, see which tools/consumables the shop has approved
for it, via a smart button on the category form.

Honest-empty by design. No data seeds this relationship — compatibility
is engineering/vendor knowledge nobody has entered yet, the same
"deferred to the owner" posture as the coverage-rate fields on
product.template. The button therefore reads 0 for every category
today. That is correct, not broken: an empty count here is the finding
this remediation wave exists to surface, not a bug to paper over
("absence of data never renders as good" applies just as much to a
smart-button count as it does to a safety flag).
"""
from odoo import _, api, fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    x_southbrook_approved_consumable_count = fields.Integer(
        string="Approved Adhesives & Finishes",
        compute="_compute_x_southbrook_approved_consumable_count",
        help="Count of Southbrook tool/consumable products that "
             "declare this category as a compatible material via "
             "x_southbrook_compatible_material_ids. Zero means no "
             "compatibility data has been entered for this category "
             "yet — not that nothing is actually compatible.",
    )

    def _compute_x_southbrook_approved_consumable_count(self):
        Product = self.env["product.template"]
        for rec in self:
            rec.x_southbrook_approved_consumable_count = Product.search_count(
                [("x_southbrook_compatible_material_ids", "in", rec.id)],
            )

    def action_view_approved_consumables(self):
        """Smart-button target: every product.template that has
        declared ``self`` as a compatible material category. Empty
        today for every category — see module docstring above."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Approved Adhesives & Finishes — %s") % self.display_name,
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [("x_southbrook_compatible_material_ids", "in", self.id)],
            "context": {"create": False},
        }
