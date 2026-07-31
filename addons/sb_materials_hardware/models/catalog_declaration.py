# SPDX-License-Identifier: LGPL-3.0-only
"""Facet and column declarations.

Adding a facet or a column to the catalog is a DATA change: create a record.
No per-category branching exists anywhere in the provider or the frontend.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

FACET_TYPES = [
    ("enum", "Selection values"),
    ("enum_distinct", "Distinct stored values"),
    ("range", "Numeric range"),
    ("m2m", "Related records"),
    ("flag", "Boolean flag"),
]


class CatalogDeclarationMixin(models.AbstractModel):
    _name = "materials.catalog.declaration.mixin"
    _description = "Shared behaviour for catalog facet/column declarations"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    category_id = fields.Many2one(
        "southbrook.tool.category", required=True, ondelete="cascade",
        index=True,
        help="Declaration applies to this category and its descendants.")
    field_name = fields.Char(
        required=True,
        help="Field name on product.template, e.g. x_southbrook_grit.")
    active = fields.Boolean(default=True)

    @api.constrains("field_name")
    def _check_field_exists(self):
        tmpl_fields = self.env["product.template"]._fields
        for rec in self:
            if rec.field_name not in tmpl_fields:
                raise ValidationError(
                    "%s is not a field on product.template." % rec.field_name)


class CatalogFacet(models.Model):
    _name = "materials.catalog.facet"
    _description = "Catalog Facet Declaration"
    _inherit = "materials.catalog.declaration.mixin"
    _order = "sequence, id"

    facet_type = fields.Selection(FACET_TYPES, required=True, default="enum")

    _cat_field_uniq = models.Constraint(
        "unique(category_id, field_name)",
        "A facet for this field already exists on this category.")


class CatalogColumn(models.Model):
    _name = "materials.catalog.column"
    _description = "Catalog Column Declaration"
    _inherit = "materials.catalog.declaration.mixin"
    _order = "sequence, id"

    align = fields.Selection(
        [("left", "Left"), ("right", "Right")], default="left")
    sortable = fields.Boolean(default=True)

    _cat_field_uniq = models.Constraint(
        "unique(category_id, field_name)",
        "A column for this field already exists on this category.")


class CatalogSearchSynonym(models.Model):
    _name = "materials.catalog.search.synonym"
    _description = "Catalog search synonym"
    _order = "term"
    _rec_name = "term"

    # This is itself a declaration, same "declarations are data"
    # philosophy as the facet/column mixin above — but it does not inherit
    # that mixin: a synonym has no field_name, no facet_type, and its
    # target is a SET of categories (category_ids), not one category_id it
    # applies "at and below". It widens the free-text row search in
    # tools_catalog_provider._rows() when a typed word matches `term`
    # exactly (case-insensitive, trimmed) — see that method's comment for
    # why: the category tree deliberately has no "Solvents" node, so
    # "solvent" would otherwise search-miss entirely even though the
    # categories that answer it (Thinner / Reducer / Catalyst, Degreaser /
    # Surface Cleaner) already exist.
    term = fields.Char(
        required=True, index=True,
        help="The word a person actually types, e.g. 'solvent'. Matched "
             "case-insensitively and exactly (not a substring) against the "
             "search box.")
    category_ids = fields.Many2many(
        "southbrook.tool.category",
        "materials_catalog_search_synonym_category_rel",
        "synonym_id", "category_id",
        string="Widens search to",
        help="Rows whose tool category falls under any of these (and "
             "their descendants) are included when `term` is searched, in "
             "addition to the ordinary name/reference match.")
    active = fields.Boolean(default=True)

    _term_uniq = models.Constraint(
        "unique(term)", "This search term is already mapped to categories.")
