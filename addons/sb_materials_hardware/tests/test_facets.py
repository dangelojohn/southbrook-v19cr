# SPDX-License-Identifier: LGPL-3.0-only
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestFacets(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Provider = self.env["materials.catalog.provider"]
        self.Facet = self.env["materials.catalog.facet"]
        self.cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_abr_disc")
        Tmpl = self.env["product.template"]
        for grit in ["80", "120", "220", "100"]:
            Tmpl.create({
                "name": "TEST Disc %s" % grit,
                "x_southbrook_tool_category_id": self.cat.id,
                "x_southbrook_grit": grit,
            })
        Tmpl.create({
            "name": "TEST Disc assorted",
            "x_southbrook_tool_category_id": self.cat.id,
            "x_southbrook_grit": "assorted",
        })

    def test_numeric_prefix_helper(self):
        f = self.Provider._numeric_prefix
        self.assertEqual(f("120"), 120.0)
        self.assertEqual(f("120 grit"), 120.0)
        self.assertIsNone(f("assorted"))
        self.assertIsNone(f(False))

    def _facet(self, key):
        payload = self.Provider.get_catalog(scope="tools", category_id=self.cat.id)
        return {f["key"]: f for f in payload["facets"]}[key]

    def test_enum_distinct_values_are_numerically_ordered(self):
        self.Facet.create({
            "name": "Grit", "category_id": self.cat.id,
            "field_name": "x_southbrook_grit", "facet_type": "enum_distinct",
        })
        values = [v["value"] for v in self._facet("x_southbrook_grit")["values"]]
        numeric = [v for v in values if v != "assorted"]
        self.assertEqual(numeric, ["80", "100", "120", "220"])

    def test_unparseable_value_sorts_last_and_is_kept(self):
        self.Facet.create({
            "name": "Grit", "category_id": self.cat.id,
            "field_name": "x_southbrook_grit", "facet_type": "enum_distinct",
        })
        values = [v["value"] for v in self._facet("x_southbrook_grit")["values"]]
        self.assertEqual(values[-1], "assorted")

    def test_enum_facet_uses_selection_labels(self):
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        self.env["product.template"].create({
            "name": "TEST Confirmat", "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_thread_type": "confirmat",
        })
        # Reuse the seeded facet_screws_thread instead of creating a duplicate
        facet = self.env["materials.catalog.facet"].search([
            ("category_id", "=", cat.id),
            ("field_name", "=", "x_southbrook_thread_type")
        ]) or self.Facet.create({
            "name": "Thread type", "category_id": cat.id,
            "field_name": "x_southbrook_thread_type", "facet_type": "enum",
        })
        payload = self.Provider.get_catalog(scope="tools", category_id=cat.id)
        facet = {f["key"]: f for f in payload["facets"]}["x_southbrook_thread_type"]
        labels = {v["value"]: v["label"] for v in facet["values"]}
        self.assertEqual(labels["confirmat"], "Confirmat")

    def test_range_facet_reports_min_and_max(self):
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        Tmpl = self.env["product.template"]
        for length in [30.0, 50.0, 70.0]:
            Tmpl.create({
                "name": "TEST Screw %s" % length,
                "x_southbrook_tool_category_id": cat.id,
                "x_southbrook_screw_length_mm": length,
            })
        # Reuse the seeded facet_screws_length instead of creating a duplicate
        facet = self.env["materials.catalog.facet"].search([
            ("category_id", "=", cat.id),
            ("field_name", "=", "x_southbrook_screw_length_mm")
        ]) or self.Facet.create({
            "name": "Length", "category_id": cat.id,
            "field_name": "x_southbrook_screw_length_mm", "facet_type": "range",
        })
        payload = self.Provider.get_catalog(scope="tools", category_id=cat.id)
        facet = {f["key"]: f for f in payload["facets"]}[
            "x_southbrook_screw_length_mm"]
        self.assertEqual(facet["min"], 30.0)
        self.assertEqual(facet["max"], 70.0)

    def test_range_facet_reports_none_when_no_data(self):
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screw_wood")
        self.Facet.create({
            "name": "Length", "category_id": cat.id,
            "field_name": "x_southbrook_screw_length_mm", "facet_type": "range",
        })
        payload = self.Provider.get_catalog(scope="tools", category_id=cat.id)
        facet = {f["key"]: f for f in payload["facets"]}[
            "x_southbrook_screw_length_mm"]
        self.assertIsNone(facet["min"])
        self.assertIsNone(facet["max"])

    def test_facet_declared_on_both_ancestor_and_category_is_not_duplicated(self):
        """Nearest-wins dedupe, same rule as _columns.

        The uniqueness constraint on materials.catalog.facet is scoped per
        category_id, so the same field_name CAN legitimately be declared on
        both an ancestor and the selected category. Only one entry may reach
        the payload, and it must be the nearer (child's) declaration.
        """
        parent = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        child = self.env.ref("southbrook_mrp_kitchen_tools.cat_screw_confirmat")
        self.env["product.template"].create({
            "name": "TEST Confirmat Dedup",
            "x_southbrook_tool_category_id": child.id,
            "x_southbrook_material_grade": "hardwood",
        })
        # Use material_grade (not seeded on cat_screws) to avoid collision
        self.Facet.create({
            "name": "Material grade (ancestor label)", "category_id": parent.id,
            "field_name": "x_southbrook_material_grade", "facet_type": "enum",
        })
        self.Facet.create({
            "name": "Material grade (child label)", "category_id": child.id,
            "field_name": "x_southbrook_material_grade", "facet_type": "enum",
        })
        payload = self.Provider.get_catalog(scope="tools", category_id=child.id)
        matches = [f for f in payload["facets"]
                   if f["key"] == "x_southbrook_material_grade"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["label"], "Material grade (child label)")

    def test_m2m_facet_sorts_alphabetically_by_label(self):
        """Relation-valued ("value" is a database id) facets must sort by
        label, not by the numeric-parseable id — otherwise chip order is
        arbitrary internal creation order.
        """
        Category = self.env["product.category"]
        # Created in an order whose ids do NOT match alphabetical order.
        zebra = Category.create({"name": "Zebra Material"})
        mango = Category.create({"name": "Mango Material"})
        apple = Category.create({"name": "Apple Material"})
        self.assertLess(zebra.id, mango.id)
        self.assertLess(mango.id, apple.id)

        Tmpl = self.env["product.template"]
        for material in (zebra, mango, apple):
            Tmpl.create({
                "name": "TEST Disc for %s" % material.name,
                "x_southbrook_tool_category_id": self.cat.id,
                "x_southbrook_compatible_material_ids": [(6, 0, [material.id])],
            })
        self.Facet.create({
            "name": "Compatible Material", "category_id": self.cat.id,
            "field_name": "x_southbrook_compatible_material_ids",
            "facet_type": "m2m",
        })
        facet = self._facet("x_southbrook_compatible_material_ids")
        self.assertEqual(facet["type"], "m2m")
        labels = [v["label"] for v in facet["values"]]
        self.assertEqual(labels, ["Apple Material", "Mango Material",
                                  "Zebra Material"])

    def test_string_facet_selection_degrades_naming_the_offending_key(self):
        """A string selection (instead of a list) must not be silently
        iterated character-by-character — that produces an OR of single
        characters matching nothing, with no hint the shape was wrong.
        Must degrade instead, with a reason that names the field.
        """
        payload = self.Provider.get_catalog(
            scope="tools", category_id=self.cat.id,
            facets={"x_southbrook_grit": "120"})
        self.assertFalse(payload["ok"])
        self.assertIn("x_southbrook_grit", payload["reason"])

    def test_list_selection_for_declared_range_facet_degrades(self):
        """A list where a {"min":, "max":} range dict was meant (e.g.
        [0, 100]) must not silently fall through to the enum/OR path and be
        interpreted as "equals 0 OR equals 100" — the facet is declared as
        a numeric range, so a list selection against it is rejected outright
        rather than guessed at.
        """
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        # facet_screws_length (seeded) declares x_southbrook_screw_length_mm
        # as facet_type="range" on this category.
        payload = self.Provider.get_catalog(
            scope="tools", category_id=cat.id,
            facets={"x_southbrook_screw_length_mm": [0, 100]})
        self.assertFalse(payload["ok"])
        self.assertIn("x_southbrook_screw_length_mm", payload["reason"])

    def test_flag_facet_reports_single_chip_with_count(self):
        """Plain Boolean flag facets keep the original single-chip
        behaviour. x_southbrook_hazardous (and the other three safety
        fields) is no longer Boolean as of the tri-state safety wave — see
        test_tristate_flag_facet_reports_one_chip_per_selection_value below
        for that shape — so this uses x_southbrook_expiry_required, which
        the safety wave deliberately leaves as a plain Boolean, to prove
        the single-chip branch still exists and still works.
        """
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screw_wood")
        Tmpl = self.env["product.template"]
        Tmpl.create({
            "name": "TEST Expiry-tracked Wood Screw 1",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_expiry_required": True,
        })
        Tmpl.create({
            "name": "TEST Expiry-tracked Wood Screw 2",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_expiry_required": True,
        })
        Tmpl.create({
            "name": "TEST Non-expiry Wood Screw",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_expiry_required": False,
        })
        self.Facet.create({
            "name": "Expiry required", "category_id": cat.id,
            "field_name": "x_southbrook_expiry_required", "facet_type": "flag",
        })
        payload = self.Provider.get_catalog(scope="tools", category_id=cat.id)
        facet = {f["key"]: f for f in payload["facets"]}[
            "x_southbrook_expiry_required"]
        self.assertEqual(facet["type"], "flag")
        self.assertEqual(len(facet["values"]), 1)
        self.assertEqual(facet["values"][0]["value"], True)
        self.assertEqual(facet["values"][0]["count"], 2)

    def test_tristate_flag_facet_reports_one_chip_per_selection_value(self):
        """The four safety fields are Selection('yes'/'no'/'not_assessed')
        as of southbrook_mrp_kitchen_tools's tri-state safety wave (see
        CONTRACT_tools_consumables.md B1 item 1). A facet declared
        facet_type="flag" over such a field must report one chip per
        selection value — including "not_assessed" — with its own count,
        never the old single-True-chip shape. Absence must never silently
        vanish from the count it belongs to.
        """
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_adhesives")
        Tmpl = self.env["product.template"]
        Tmpl.create({
            "name": "TEST Hazardous Adhesive",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_hazardous": "yes",
        })
        Tmpl.create({
            "name": "TEST Non-hazardous Adhesive",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_hazardous": "no",
        })
        Tmpl.create({
            "name": "TEST Unassessed Adhesive",
            "x_southbrook_tool_category_id": cat.id,
            # x_southbrook_hazardous intentionally left unset -> default
            # 'not_assessed'.
        })
        # facet_adh_hazard is already seeded on this category — reuse it
        # instead of creating a duplicate (category, field_name) pair.
        payload = self.Provider.get_catalog(scope="tools", category_id=cat.id)
        facet = {f["key"]: f for f in payload["facets"]}["x_southbrook_hazardous"]
        self.assertEqual(facet["type"], "flag")
        by_value = {v["value"]: v for v in facet["values"]}
        self.assertEqual(set(by_value), {"yes", "no", "not_assessed"})
        self.assertEqual(by_value["yes"]["count"], 1)
        self.assertEqual(by_value["yes"]["label"], "Yes")
        self.assertEqual(by_value["no"]["count"], 1)
        self.assertEqual(by_value["no"]["label"], "No")
        self.assertEqual(by_value["not_assessed"]["count"], 1)
        self.assertEqual(by_value["not_assessed"]["label"], "Not assessed")

    def test_tristate_not_assessed_chip_counts_a_bare_null_column_too(self):
        """A row that predates the tri-state migration (or was bulk-loaded
        without the field set) can be a genuine SQL NULL rather than the
        literal string "not_assessed" — the ORM default only applies going
        forward from the field definition, not retroactively to rows
        written around it. The "not assessed" chip must count that row too,
        not silently drop it from every chip's count.
        """
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_adhesives")
        tmpl = self.env["product.template"].create({
            "name": "TEST Null-hazard Adhesive",
            "x_southbrook_tool_category_id": cat.id,
        })
        self.env.cr.execute(
            "UPDATE product_template SET x_southbrook_hazardous = NULL "
            "WHERE id = %s", (tmpl.id,))
        self.env.invalidate_all()
        payload = self.Provider.get_catalog(scope="tools", category_id=cat.id)
        facet = {f["key"]: f for f in payload["facets"]}["x_southbrook_hazardous"]
        by_value = {v["value"]: v for v in facet["values"]}
        self.assertEqual(by_value["not_assessed"]["count"], 1)
        self.assertEqual(by_value["yes"]["count"], 0)
        self.assertEqual(by_value["no"]["count"], 0)

    def test_tristate_chip_filters_to_the_matching_domain_leaf(self):
        """Toggling the "yes" chip must narrow rows to exactly (field, '=',
        'yes') — proving the tri-state chip's `value` round-trips through
        the ordinary facet-selection domain builder unchanged.
        """
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_adhesives")
        Tmpl = self.env["product.template"]
        Tmpl.create({
            "name": "TEST Filter Hazardous Adhesive",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_hazardous": "yes",
        })
        Tmpl.create({
            "name": "TEST Filter Non-hazardous Adhesive",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_hazardous": "no",
        })
        payload = self.Provider.get_catalog(
            scope="tools", category_id=cat.id,
            facets={"x_southbrook_hazardous": ["yes"]})
        self.assertTrue(payload["ok"])
        names = {r["name"] for r in payload["rows"]}
        self.assertIn("TEST Filter Hazardous Adhesive", names)
        self.assertNotIn("TEST Filter Non-hazardous Adhesive", names)
