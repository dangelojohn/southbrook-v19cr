# SPDX-License-Identifier: LGPL-3.0-only
"""materials.catalog.search.synonym — the "solvent" word maps onto
categories the taxonomy doesn't itself name (see data/
catalog_search_synonyms.xml and tools_catalog_provider._synonym_
category_ids for why: the seed tree deliberately has no "Solvents" node).
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestSearchSynonym(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Provider = self.env["materials.catalog.provider"]
        self.finishing = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_finishing")
        self.thinner = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_paint_thinner")
        self.degreaser = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_paint_degreaser")
        self.screws = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        Tmpl = self.env["product.template"]
        # Deliberately named so name/default_code ilike would NOT match
        # "solvent" — the only way these can surface for that search term
        # is the synonym widening.
        self.thinner_product = Tmpl.create({
            "name": "TEST Lacquer Reducer 1L",
            "default_code": "SB-FIN-REDUCER-1L",
            "x_southbrook_tool_category_id": self.thinner.id,
        })
        self.degreaser_product = Tmpl.create({
            "name": "TEST Surface Prep Wipe",
            "default_code": "SB-FIN-WIPE",
            "x_southbrook_tool_category_id": self.degreaser.id,
        })
        self.unrelated_finishing_product = Tmpl.create({
            "name": "TEST Cabinet Lacquer Gloss",
            "default_code": "SB-FIN-LACQUER-GLOSS",
            "x_southbrook_tool_category_id": self.env.ref(
                "southbrook_mrp_kitchen_tools.cat_paint_lacquer").id,
        })

    def _names(self, category, search):
        payload = self.Provider.get_catalog(
            scope="tools", category_id=category.id, search=search)
        self.assertTrue(payload["ok"])
        return {r["name"] for r in payload["rows"]}

    def test_synonym_widens_search_to_mapped_categories(self):
        names = self._names(self.finishing, "solvent")
        self.assertIn("TEST Lacquer Reducer 1L", names)
        self.assertIn("TEST Surface Prep Wipe", names)
        self.assertNotIn("TEST Cabinet Lacquer Gloss", names)

    def test_synonym_match_is_case_insensitive(self):
        names = self._names(self.finishing, "Solvent")
        self.assertIn("TEST Lacquer Reducer 1L", names)

    def test_synonym_match_is_trimmed(self):
        names = self._names(self.finishing, "  solvent  ")
        self.assertIn("TEST Lacquer Reducer 1L", names)

    def test_plural_form_is_seeded_separately(self):
        names = self._names(self.finishing, "solvents")
        self.assertIn("TEST Lacquer Reducer 1L", names)
        self.assertIn("TEST Surface Prep Wipe", names)

    def test_synonym_is_an_exact_match_not_a_substring(self):
        """"solvent" must not fuzzy-match "solvent-based" via ilike-style
        substring rules — the synonym lookup is an exact (case-insensitive)
        match, and "solvent-based" instead falls through to the ordinary
        name/reference ilike search, which none of the fixtures match.
        """
        names = self._names(self.finishing, "solvent-based")
        self.assertNotIn("TEST Lacquer Reducer 1L", names)
        self.assertNotIn("TEST Surface Prep Wipe", names)

    def test_synonym_widening_stays_inside_the_browsed_category(self):
        """The synonym only OR-widens the row search — it must not escape
        the category the user is actually browsing. Searching "solvent"
        while browsing Screws (unrelated to Finishing) must not resurrect
        the thinner/degreaser products.
        """
        names = self._names(self.screws, "solvent")
        self.assertNotIn("TEST Lacquer Reducer 1L", names)
        self.assertNotIn("TEST Surface Prep Wipe", names)

    def test_unmapped_term_falls_back_to_plain_text_search(self):
        """A search term with no synonym record must behave exactly as
        before — plain ilike over name/default_code, no widening, no
        crash.
        """
        names = self._names(self.finishing, "Reducer")
        self.assertIn("TEST Lacquer Reducer 1L", names)
        self.assertNotIn("TEST Surface Prep Wipe", names)

    def test_synonym_does_not_perturb_facets_or_category_rail(self):
        """The synonym widens ROWS only — facets and the category rail
        answer "what does this category contain", a different question,
        and must be computed the same with or without a synonym match.
        """
        plain = self.Provider.get_catalog(
            scope="tools", category_id=self.finishing.id, search="")
        widened = self.Provider.get_catalog(
            scope="tools", category_id=self.finishing.id, search="solvent")
        self.assertEqual(
            {f["key"] for f in plain["facets"]},
            {f["key"] for f in widened["facets"]})
        self.assertEqual(
            {c["id"] for c in plain["categories"]},
            {c["id"] for c in widened["categories"]})
