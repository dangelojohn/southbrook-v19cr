# SPDX-License-Identifier: LGPL-3.0-only
"""audit_facet_coverage() and the "does this ever go permanently red"
ratchet.

Two independent concerns live in this file:

1. TestFacetCoverageMechanism proves the AUDIT MECHANISM itself is
   correct: a facet a fixture actually populates is reported populated,
   with a real distinct_count; a facet nobody has ever touched is reported
   unpopulated, with distinct_count 0. Ordinary TransactionCase isolation
   (each test method's writes roll back) keeps these fixtures from
   touching anything else.

2. TestFacetCoverageRatchet is the review's "test that fails on
   zero-populated facets", implemented the way the team agreed on instead
   of a single always-failing assertion (see the class docstring below for
   why a naive version of that request is either permanently red from day
   one, forever, or has to hand-wave which facets are exempt). It creates
   NO fixtures of its own — it audits exactly what southbrook_mrp_kitchen_
   tools + sb_materials_hardware's own seed/demo data already produce, and
   compares that to a pinned, hand-written expected set.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestFacetCoverageMechanism(TransactionCase):
    def setUp(self):
        super().setUp()
        # The CONCRETE provider — audit_facet_coverage is per-provider by
        # design (each scope audits its own declarations); the abstract
        # materials.catalog.provider has no such method.
        self.Provider = self.env["tools.catalog.provider"]
        self.Facet = self.env["materials.catalog.facet"]
        self.cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screw_wood")

    def _audit_by_key(self):
        return {row["key"]: row for row in self.Provider.audit_facet_coverage()}

    def test_populated_facet_reported_populated(self):
        self.env["product.template"].create({
            "name": "TEST Coverage Confirmat",
            "x_southbrook_tool_category_id": self.cat.id,
            "x_southbrook_thread_type": "confirmat",
        })
        self.Facet.create({
            "name": "TEST Thread coverage", "category_id": self.cat.id,
            "field_name": "x_southbrook_thread_type", "facet_type": "enum",
        })
        audit = self._audit_by_key()
        key = "%s::x_southbrook_thread_type" % self.cat.name
        self.assertIn(key, audit)
        self.assertTrue(audit[key]["populated"])
        self.assertEqual(audit[key]["field_name"], "x_southbrook_thread_type")
        self.assertGreater(audit[key]["distinct_count"], 0)

    def test_untouched_facet_reported_unpopulated(self):
        self.Facet.create({
            "name": "TEST Untouched coverage", "category_id": self.cat.id,
            "field_name": "x_southbrook_material_grade",
            "facet_type": "enum_distinct",
        })
        audit = self._audit_by_key()
        key = "%s::x_southbrook_material_grade" % self.cat.name
        self.assertIn(key, audit)
        self.assertFalse(audit[key]["populated"])
        self.assertEqual(audit[key]["distinct_count"], 0)

    def test_range_facet_populated_only_when_min_or_max_is_real(self):
        key = "%s::x_southbrook_screw_length_mm" % self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_screws").name
        audit_before = self._audit_by_key()
        self.assertIn(key, audit_before)
        self.assertFalse(audit_before[key]["populated"])
        cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        self.env["product.template"].create({
            "name": "TEST Coverage Screw",
            "x_southbrook_tool_category_id": cat.id,
            "x_southbrook_screw_length_mm": 40.0,
        })
        audit_after = self._audit_by_key()
        self.assertTrue(audit_after[key]["populated"])


# ---------------------------------------------------------------------
# The ratchet.
#
# EXPECTED_UNPOPULATED_FACETS pins the exact set of "<category name>::
# <field_name>" keys that audit_facet_coverage() reports unpopulated
# TODAY, with nothing created by this test beyond what module install
# already seeds: the 62 facet declarations in data/catalog_declarations.
# xml, plus southbrook_mrp_kitchen_tools's own demo data (8 demo tool/
# consumable products — see that module's demo/tool_assets_demo.xml).
#
# WHY A PIN, NOT A LIVE "MUST BE ZERO" ASSERTION: the taxonomy in this
# module ships ahead of real classified product data on purpose —
# "declarations are data" (see the module README) means a facet is cheap
# to declare long before anyone has classified a product into it. Most of
# the 62 seeded facets (screws, adhesives, fasteners, abrasives — 30
# facets across four whole categories) therefore have ZERO backing
# products right now, with no demo data anywhere near them. A literal
# "assert every facet has data" would be red from the day this module
# shipped and stay red until someone seeds vendor data across the entire
# taxonomy — a permanently-failing test nobody can act on, which trains
# everyone to ignore it. A pin does something more useful: it makes today's
# emptiness an explicit, visible, and CHANGEABLE fact. Any NEW empty facet
# (a taxonomy addition nobody seeded data for) fails this test the moment
# it's declared. Any facet that gains its first real data point ALSO fails
# this test — not because that's bad, but because leaving a stale pin
# entry in place would silently under-report the alarm the next time a
# genuinely new gap appears. Either direction forces a deliberate,
# reviewed edit to this literal set; the test can never be made to pass
# by changing production code, only by consciously updating what's pinned
# and saying why.
#
# ENVIRONMENT NOTE (learned the hard way — the first version of this pin
# was derived statically from demo/tool_assets_demo.xml and was wrong in
# both directions): demo products only exist on demo-enabled databases.
# The canonical validation environment for this repo is the sami dev copy
# of the PRODUCTION database (no demo data), which is also what prod
# itself will report. On that environment the real signal sources are:
# the 11 classified cutting-tool products (which carry tool_family and
# directness but ZERO numeric specs — they are $1 placeholders), the four
# chemical products the 19.0.2.0.0 migration classifies, and the
# tri-state safety facets, which are populated BY CONSTRUCTION the moment
# any product exists in their category (a not_assessed default is real,
# queryable data — audit_facet_coverage asks "does this declaration have
# zero rows behind it", not "has anyone verified anything"). If this test
# ever runs on a demo-enabled DB and disagrees, the pin below is the
# prod-parity truth; prefer running it where the data is real.
#
# UPDATED 2026-08-03: count reduced 47 → 45. Two facets in the Adhesives
# category now have data from the consumables data-completion wave:
# x_southbrook_compatible_material_ids and x_southbrook_glue_type were
# classified into their categories and are no longer unpopulated.
EXPECTED_UNPOPULATED_FACETS = frozenset({
    # MEASURED, not derived: this exact set is what
    # tools.catalog.provider.audit_facet_coverage() reported on the
    # 2026-08-03 dev copy of the production DB. The pin now tracks
    # the real improved state after products were classified into the
    # Adhesives category facets.
    "Abrasives and Surface Preparation::x_southbrook_compatible_material_ids",
    "Abrasives and Surface Preparation::x_southbrook_grit",
    "Abrasives and Surface Preparation::x_southbrook_preferred_vendor_id",
    "Adhesives, Glues, Sealants::x_southbrook_cure_time_min",
    "Adhesives, Glues, Sealants::x_southbrook_directness",
    "Adhesives, Glues, Sealants::x_southbrook_open_time_min",
    "Adhesives, Glues, Sealants::x_southbrook_preferred_vendor_id",
    "Adhesives, Glues, Sealants::x_southbrook_shelf_life_days",
    "Adhesives, Glues, Sealants::x_southbrook_tool_family",
    "CNC Router Bits::x_southbrook_coating",
    "CNC Router Bits::x_southbrook_compatible_material_ids",
    "CNC Router Bits::x_southbrook_cutting_diameter_mm",
    "CNC Router Bits::x_southbrook_cutting_length_mm",
    "CNC Router Bits::x_southbrook_preferred_vendor_id",
    "CNC Router Bits::x_southbrook_shank_diameter_mm",
    "Drill and Boring Bits::x_southbrook_cutting_diameter_mm",
    "Drill and Boring Bits::x_southbrook_material_grade",
    "Drill and Boring Bits::x_southbrook_overall_length_mm",
    "Drill and Boring Bits::x_southbrook_preferred_vendor_id",
    "Drill and Boring Bits::x_southbrook_shank_diameter_mm",
    "Fasteners::x_southbrook_compatible_material_ids",
    "Fasteners::x_southbrook_directness",
    "Fasteners::x_southbrook_preferred_vendor_id",
    "Fasteners::x_southbrook_screw_length_mm",
    "Fasteners::x_southbrook_tool_family",
    "Finishing and Paint Tools::x_southbrook_directness",
    "Finishing and Paint Tools::x_southbrook_preferred_vendor_id",
    "Finishing and Paint Tools::x_southbrook_shelf_life_days",
    "Finishing and Paint Tools::x_southbrook_tool_family",
    "Saw Blades::x_southbrook_blade_diameter_mm",
    "Saw Blades::x_southbrook_bore_size_mm",
    "Saw Blades::x_southbrook_coating",
    "Saw Blades::x_southbrook_compatible_material_ids",
    "Saw Blades::x_southbrook_material_grade",
    "Saw Blades::x_southbrook_preferred_vendor_id",
    "Saw Blades::x_southbrook_tooth_count",
    "Screws::x_southbrook_compatible_material_ids",
    "Screws::x_southbrook_directness",
    "Screws::x_southbrook_drive_type",
    "Screws::x_southbrook_head_type",
    "Screws::x_southbrook_preferred_vendor_id",
    "Screws::x_southbrook_screw_length_mm",
    "Screws::x_southbrook_screw_size",
    "Screws::x_southbrook_thread_type",
    "Screws::x_southbrook_tool_family",
})


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestFacetCoverageRatchet(TransactionCase):
    """Creates nothing. Audits exactly what's already seeded/demoed.

    See the EXPECTED_UNPOPULATED_FACETS comment above for the full
    reasoning this pin implements — this is the agreed replacement for a
    single "some facet must have data" assertion that would otherwise
    either be permanently red (most of this taxonomy has no data yet, by
    design) or have to hand-wave an exemption list that silently rots.
    """

    def test_unpopulated_facets_match_the_pinned_set(self):
        rows = self.env["tools.catalog.provider"].audit_facet_coverage()
        actual_unpopulated = {row["key"] for row in rows if not row["populated"]}
        self.assertEqual(
            actual_unpopulated, EXPECTED_UNPOPULATED_FACETS,
            "Facet coverage has changed since this pin was written. A key "
            "present in the pin but no longer in the actual set means that "
            "facet now has data -- shrink the pin, that's a real "
            "improvement. A key in the actual set but not in the pin means "
            "either a newly-declared facet shipped with no data behind it, "
            "or an existing one lost its only data -- investigate which, "
            "then update the pin (with a comment) either way. Never edit "
            "this test to pass without making that call.")
