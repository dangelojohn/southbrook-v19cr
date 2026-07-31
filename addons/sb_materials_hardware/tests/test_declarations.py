# SPDX-License-Identifier: LGPL-3.0-only
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestDeclarations(TransactionCase):
    def setUp(self):
        super().setUp()
        # Use cat_screw_wood (not seeded) to avoid conflicts with seeded cat_screws data
        self.cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screw_wood")

    def test_facet_created(self):
        facet = self.env["materials.catalog.facet"].create({
            "name": "Thread type",
            "category_id": self.cat.id,
            "field_name": "x_southbrook_thread_type",
            "facet_type": "enum",
        })
        self.assertEqual(facet.sequence, 10)
        self.assertTrue(facet.active)

    def test_column_created(self):
        col = self.env["materials.catalog.column"].create({
            "name": "Length (mm)",
            "category_id": self.cat.id,
            "field_name": "x_southbrook_screw_length_mm",
            "align": "right",
        })
        self.assertTrue(col.sortable)

    @mute_logger("odoo.sql_db")
    def test_facet_unique_per_category(self):
        vals = {
            "name": "Thread type",
            "category_id": self.cat.id,
            "field_name": "x_southbrook_thread_type",
            "facet_type": "enum",
        }
        self.env["materials.catalog.facet"].create(vals)
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.env["materials.catalog.facet"].create(dict(vals))

    def test_field_name_must_exist_on_product_template(self):
        with self.assertRaises(ValidationError):
            self.env["materials.catalog.facet"].create({
                "name": "Nonsense",
                "category_id": self.cat.id,
                "field_name": "x_southbrook_not_a_real_field",
                "facet_type": "enum",
            })

    def test_seeded_declarations_exist_for_screws(self):
        facets = self.env["materials.catalog.facet"].search([
            ("category_id", "=",
             self.env.ref("southbrook_mrp_kitchen_tools.cat_screws").id)])
        fields_declared = set(facets.mapped("field_name"))
        self.assertIn("x_southbrook_thread_type", fields_declared)
        self.assertIn("x_southbrook_head_type", fields_declared)
        self.assertIn("x_southbrook_drive_type", fields_declared)
        self.assertIn("x_southbrook_compatible_material_ids", fields_declared)

    def test_seeded_declarations_exist_for_adhesives(self):
        facets = self.env["materials.catalog.facet"].search([
            ("category_id", "=",
             self.env.ref("southbrook_mrp_kitchen_tools.cat_adhesives").id)])
        self.assertIn("x_southbrook_glue_type", set(facets.mapped("field_name")))

    def test_every_seeded_declaration_names_a_real_field(self):
        tmpl_fields = self.env["product.template"]._fields
        for model in ("materials.catalog.facet", "materials.catalog.column"):
            for rec in self.env[model].search([]):
                self.assertIn(rec.field_name, tmpl_fields,
                              "%s declares missing field %s" % (model, rec.field_name))


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestSearchSynonymDeclaration(TransactionCase):
    """materials.catalog.search.synonym itself — creation, uniqueness and
    the seeded "solvent" mapping. Search-time integration (does typing
    "solvent" actually widen get_catalog's rows) lives in
    tests/test_search_synonym.py, not here.
    """

    def setUp(self):
        super().setUp()
        self.thinner = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_paint_thinner")
        self.degreaser = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_paint_degreaser")

    def test_synonym_created(self):
        syn = self.env["materials.catalog.search.synonym"].create({
            "term": "TEST thinner-ish",
            "category_ids": [(6, 0, [self.thinner.id])],
        })
        self.assertTrue(syn.active)
        self.assertEqual(syn.display_name, "TEST thinner-ish")

    @mute_logger("odoo.sql_db")
    def test_synonym_term_unique(self):
        vals = {"term": "TEST duplicate-term",
                "category_ids": [(6, 0, [self.thinner.id])]}
        self.env["materials.catalog.search.synonym"].create(vals)
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.env["materials.catalog.search.synonym"].create(dict(vals))

    def test_solvent_synonym_seeded_with_both_forms_and_both_categories(self):
        Synonym = self.env["materials.catalog.search.synonym"]
        for term in ("solvent", "solvents"):
            syn = Synonym.search([("term", "=", term)])
            self.assertTrue(syn, "expected a seeded synonym for %r" % term)
            self.assertEqual(set(syn.category_ids.ids),
                              {self.thinner.id, self.degreaser.id})
