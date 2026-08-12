# SPDX-License-Identifier: LGPL-3.0-only
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestColumns(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Provider = self.env["materials.catalog.provider"]
        self.Column = self.env["materials.catalog.column"]
        self.parent = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        self.child = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_screw_confirmat")

    def _keys(self, category):
        payload = self.Provider.get_catalog(
            scope="tools", category_id=category.id)
        return [c["key"] for c in payload["columns"]]

    def test_generic_columns_always_present(self):
        keys = self._keys(self.child)
        self.assertEqual(keys[0], "name")
        self.assertIn("default_code", keys)

    def test_declared_column_appears(self):
        # Use a field name not in seeded declarations to avoid uniqueness conflict
        self.Column.create({
            "name": "Material Grade", "category_id": self.parent.id,
            "field_name": "x_southbrook_material_grade", "align": "left",
        })
        self.assertIn("x_southbrook_material_grade", self._keys(self.parent))

    def test_ancestor_columns_inherited_by_child(self):
        # Use a field name not in seeded declarations to avoid uniqueness conflict
        self.Column.create({
            "name": "Material Grade", "category_id": self.parent.id,
            "field_name": "x_southbrook_material_grade",
        })
        self.assertIn("x_southbrook_material_grade", self._keys(self.child))

    def test_child_declaration_overrides_ancestor_label(self):
        # Use a field name not in seeded declarations to avoid uniqueness conflict
        # on the parent, but test the override mechanism on (cat_screw_confirmat, x_southbrook_screw_length_mm)
        self.Column.create({
            "name": "Material Grade", "category_id": self.parent.id,
            "field_name": "x_southbrook_material_grade",
        })
        self.Column.create({
            "name": "Screw material", "category_id": self.child.id,
            "field_name": "x_southbrook_material_grade",
        })
        payload = self.Provider.get_catalog(
            scope="tools", category_id=self.child.id)
        labels = {c["key"]: c["label"] for c in payload["columns"]}
        self.assertEqual(labels["x_southbrook_material_grade"], "Screw material")


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestAggregateCategoryDeclarations(TransactionCase):
    """Selecting a category ABOVE every declared level.

    Regression: parent_path only walks upward, so an ancestor of the
    declared categories resolved NOTHING — "Cutting Tools" rendered 11
    products with only the two generic columns and zero facets, which
    reads as missing data when the data is present one level down. The
    rows in view are already `child_of category`, so the declarations
    describing them are the ones on that subtree.
    """

    def setUp(self):
        super().setUp()
        self.Provider = self.env["materials.catalog.provider"]
        self.Column = self.env["materials.catalog.column"]
        # Roots that sit above every seeded declaration.
        self.cutting = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_cutting_tools")
        self.fasteners = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_fasteners_assembly")
        # Declared level, and a deeper leaf, both inside cutting tools.
        self.saw = self.env.ref("southbrook_mrp_kitchen_tools.cat_saw_blades")
        self.deep = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_drill_hinge_35")
        self.screws = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        # Own fixtures, so this class does not depend on whatever product
        # data the host database happens to carry. These tests run in the
        # shared prod-parity suite as well as on a cold install.
        self.env["product.template"].create({
            "name": "TEST Aggregate Saw Blade",
            "x_southbrook_tool_category_id": self.saw.id,
            "x_southbrook_blade_diameter_mm": 305.0,
        })
        self.env["product.template"].create({
            "name": "TEST Aggregate Boring Bit",
            "x_southbrook_tool_category_id": self.deep.id,
            "x_southbrook_shank_diameter_mm": 10.0,
        })

    def _payload(self, category):
        return self.Provider.get_catalog(scope="tools", category_id=category.id)

    def _keys(self, category):
        return [c["key"] for c in self._payload(category)["columns"]]

    def test_aggregate_category_gets_spec_columns(self):
        keys = self._keys(self.cutting)
        self.assertGreater(
            len([k for k in keys if k.startswith("x_southbrook_")]), 0,
            "an ancestor of the declared categories must still render the "
            "spec columns that describe the rows it aggregates")

    def test_aggregate_columns_span_sibling_subtrees(self):
        """Saw-blade AND router-bit fields both reach a Cutting Tools view."""
        keys = set(self._keys(self.cutting))
        self.assertIn("x_southbrook_blade_diameter_mm", keys)   # saw blades
        self.assertIn("x_southbrook_shank_diameter_mm", keys)   # router/drill

    def test_aggregate_category_gets_facets(self):
        self.assertTrue(
            self._payload(self.cutting)["facets"],
            "faceting across an aggregate category is the point of it")

    def test_aggregate_category_rows_and_columns_agree(self):
        """Every declared column key must be a key the rows actually carry.

        Guards the failure this whole change is about: a column the grid
        renders a header for but no row supplies, which is what a dash in
        every cell actually looks like from the frontend's side.
        """
        payload = self._payload(self.cutting)
        self.assertTrue(payload["rows"], "setUp fixtures must be in scope")
        row_keys = set().union(*(set(r.keys()) for r in payload["rows"]))
        for col in payload["columns"]:
            self.assertIn(
                col["key"], row_keys,
                "column %r is declared but absent from the row payload"
                % col["key"])

    def test_broadest_label_wins_across_sibling_subtrees(self):
        """Opposite tie-break to the ancestor case, and deliberately so.

        Aggregating siblings, the general label should survive rather than
        one subtree's deeper override winning by sort accident.
        """
        self.Column.create({
            "name": "Broad label", "category_id": self.saw.id,
            "field_name": "x_southbrook_disposal_notes",
        })
        self.Column.create({
            "name": "Deep label", "category_id": self.deep.id,
            "field_name": "x_southbrook_disposal_notes",
        })
        labels = {c["key"]: c["label"]
                  for c in self._payload(self.cutting)["columns"]}
        self.assertEqual(labels["x_southbrook_disposal_notes"], "Broad label")

    def test_declared_category_is_unchanged_by_the_fallback(self):
        """The fallback fires ONLY when ancestor resolution is empty.

        Screws declares its own columns, so its shape must not shift — in
        particular it must NOT absorb its sibling subtrees' fields.
        """
        keys = set(self._keys(self.screws))
        self.assertIn("x_southbrook_thread_type", keys)
        self.assertNotIn("x_southbrook_blade_diameter_mm", keys)

    def test_fasteners_root_also_recovers(self):
        keys = set(self._keys(self.fasteners))
        self.assertIn("x_southbrook_screw_length_mm", keys)
