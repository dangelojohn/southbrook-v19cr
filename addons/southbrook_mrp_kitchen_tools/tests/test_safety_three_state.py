# SPDX-License-Identifier: LGPL-3.0-only
"""Safety three-state remediation (19.0.2.0.0) — contract §B1, BlindSpot
review of the tools/consumables catalog (see
scratchpad/CONTRACT_tools_consumables.md).

Covers:
  * x_southbrook_hazardous / _flammable / _requires_ventilation /
    _msds_required default to 'not_assessed' on create, never a
    boolean-shaped False.
  * The 19.0.2.0.0 migration helpers (pre-migrate's boolean-column
    rename guard, post-migrate's backfill + chemical-product
    classification), loaded directly via importlib — Odoo migration
    scripts aren't a regular importable package (dotted version
    directory names, hyphenated filenames) — following the
    southbrook_premium_orchestration 19.0.5.0.0/post-migrate.py
    precedent (tests/test_kitchen_jobs_board.py). Each helper is run
    twice to prove idempotency.
  * _check_safety_assessed_before_release: blocks marking an
    adhesive/finishing product available/new while any of the four
    fields is still 'not_assessed'; allows it once all four are
    assessed; never fires outside the cat_adhesives/cat_finishing
    subtree or before the product reaches a released lifecycle state;
    fires on write (a state transition), not just create.
  * _onchange_tool_category sets 'yes' (never a bare True) when the
    category asserts hazardous/msds_required, and never invents a
    value for flammable/requires_ventilation (no category-level
    source for those exists).
"""
import importlib.util
import os

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


def _load_migration(filename):
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.2.0.0", filename,
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_mrp_kitchen_tools_2_0_0_%s"
        % filename.replace("-", "_").replace(".py", ""),
        path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook", "kitchen_tools",
        "safety_three_state")
class TestSafetyThreeState(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env["product.template"]
        cls.cat_adhesives = cls.env.ref(
            "southbrook_mrp_kitchen_tools.cat_adhesives")
        cls.cat_paint_lacquer = cls.env.ref(
            "southbrook_mrp_kitchen_tools.cat_paint_lacquer")
        cls.cat_saw_blades = cls.env.ref(
            "southbrook_mrp_kitchen_tools.cat_saw_blades")

    # ──────────────────────────────────────────────────────────────────
    # Default value
    # ──────────────────────────────────────────────────────────────────
    def test_default_not_assessed_on_create(self):
        product = self.Product.create({"name": "Bare Safety Fields Product"})
        for field_name in (
            "x_southbrook_hazardous", "x_southbrook_flammable",
            "x_southbrook_requires_ventilation", "x_southbrook_msds_required",
        ):
            self.assertEqual(
                product[field_name], "not_assessed",
                "%s must default to 'not_assessed', not a boolean-shaped "
                "falsy value" % field_name)

    # ──────────────────────────────────────────────────────────────────
    # Onchange — sets 'yes', never invents a value with no source
    # ──────────────────────────────────────────────────────────────────
    def test_onchange_sets_yes_not_true(self):
        product = self.Product.new({"name": "Onchange Glue"})
        product.x_southbrook_tool_category_id = self.cat_adhesives
        product._onchange_tool_category()
        self.assertEqual(product.x_southbrook_hazardous, "yes")
        self.assertEqual(product.x_southbrook_msds_required, "yes")
        # southbrook.tool.category carries no flammable / requires_
        # ventilation policy fields at all (tool_category.py) — the
        # onchange must never invent a value for either; they stay at
        # the field default.
        self.assertEqual(product.x_southbrook_flammable, "not_assessed")
        self.assertEqual(
            product.x_southbrook_requires_ventilation, "not_assessed")

    def test_onchange_never_auto_sets_no(self):
        # cat_saw_blades.hazardous / .msds_required are both False —
        # the onchange must leave the product's fields at their
        # 'not_assessed' default rather than writing 'no', because a
        # falsy category flag proves nothing about a specific product.
        product = self.Product.new({"name": "Onchange Blade"})
        product.x_southbrook_tool_category_id = self.cat_saw_blades
        product._onchange_tool_category()
        self.assertEqual(product.x_southbrook_hazardous, "not_assessed")
        self.assertEqual(product.x_southbrook_msds_required, "not_assessed")

    # ──────────────────────────────────────────────────────────────────
    # Release-gate constraint
    # ──────────────────────────────────────────────────────────────────
    def test_constraint_blocks_release_with_unassessed_field(self):
        with self.assertRaises(ValidationError):
            self.Product.create({
                "name": "Blocked Glue",
                "x_southbrook_tool_category_id": self.cat_adhesives.id,
                "x_southbrook_tool_lifecycle_state": "available",
                "x_southbrook_hazardous": "yes",
                "x_southbrook_flammable": "yes",
                "x_southbrook_requires_ventilation": "yes",
                # x_southbrook_msds_required left at 'not_assessed' default
            })

    def test_constraint_blocks_direct_jump_to_in_use(self):
        # A Selection write can jump straight to any value — gating only
        # "available"/"new" would leave a one-write bypass ("set it to
        # In Use directly"). The gate covers every in-service state.
        with self.assertRaises(ValidationError):
            self.Product.create({
                "name": "Bypass Glue",
                "x_southbrook_tool_category_id": self.cat_adhesives.id,
                "x_southbrook_tool_lifecycle_state": "in_use",
                # all four safety fields left at 'not_assessed' default
            })

    def test_constraint_allows_out_of_service_states_unassessed(self):
        # Parking an unassessed chemical as retired/broken is legitimate —
        # the gate covers entering service, not leaving it.
        product = self.Product.create({
            "name": "Retired Mystery Solvent",
            "x_southbrook_tool_category_id": self.cat_adhesives.id,
            "x_southbrook_tool_lifecycle_state": "retired",
        })
        self.assertEqual(product.x_southbrook_hazardous, "not_assessed")

    def test_constraint_blocks_release_in_finishing_subtree_child(self):
        # cat_paint_lacquer is a CHILD of cat_finishing, not the root —
        # proves child_of coverage via parent_path, not just an exact
        # root match.
        with self.assertRaises(ValidationError):
            self.Product.create({
                "name": "Blocked Paint",
                "x_southbrook_tool_category_id": self.cat_paint_lacquer.id,
                "x_southbrook_tool_lifecycle_state": "available",
            })

    def test_constraint_allows_release_when_all_four_assessed(self):
        product = self.Product.create({
            "name": "Released Glue",
            "x_southbrook_tool_category_id": self.cat_adhesives.id,
            "x_southbrook_tool_lifecycle_state": "available",
            "x_southbrook_hazardous": "yes",
            "x_southbrook_flammable": "no",
            "x_southbrook_requires_ventilation": "no",
            "x_southbrook_msds_required": "yes",
        })
        self.assertEqual(product.x_southbrook_tool_lifecycle_state, "available")

    def test_constraint_allows_release_outside_gated_categories(self):
        # cat_saw_blades sits outside cat_adhesives/cat_finishing — an
        # unassessed MSDS/hazard flag on a saw blade is not a live
        # safety question, so the constraint must not fire even though
        # the product is released.
        product = self.Product.create({
            "name": "Released Blade",
            "x_southbrook_tool_category_id": self.cat_saw_blades.id,
            "x_southbrook_tool_lifecycle_state": "available",
        })
        self.assertEqual(product.x_southbrook_hazardous, "not_assessed")

    def test_constraint_allows_unassessed_when_not_yet_released(self):
        product = self.Product.create({
            "name": "Draft Glue",
            "x_southbrook_tool_category_id": self.cat_adhesives.id,
            "x_southbrook_tool_lifecycle_state": "needs_cleaning",
        })
        self.assertEqual(
            product.x_southbrook_tool_lifecycle_state, "needs_cleaning")

    def test_constraint_fires_on_write_transition_to_released(self):
        # Prove the gate fires on a state TRANSITION (write), not just
        # at create — a product can sit unassessed indefinitely while
        # not yet released, but the moment it is marked available it
        # must be blocked.
        product = self.Product.create({
            "name": "Not Yet Released Glue",
            "x_southbrook_tool_category_id": self.cat_adhesives.id,
        })
        self.assertFalse(product.x_southbrook_tool_lifecycle_state)
        with self.assertRaises(ValidationError):
            product.write({"x_southbrook_tool_lifecycle_state": "available"})

    # ──────────────────────────────────────────────────────────────────
    # 19.0.2.0.0 migration — boolean rename guard (pre-migrate)
    # ──────────────────────────────────────────────────────────────────
    def test_premigrate_rename_guard_skips_non_boolean_and_missing(self):
        pre_migrate = _load_migration("pre-migrate.py")
        cr = self.env.cr
        # The live column is already varchar (Selection) in this test
        # DB — the guard must refuse to touch it.
        self.assertFalse(
            pre_migrate._rename_boolean_column_to_legacy(
                cr, "x_southbrook_hazardous"))
        # A column that does not exist at all is also a no-op, not an
        # error.
        self.assertFalse(
            pre_migrate._rename_boolean_column_to_legacy(
                cr, "x_southbrook_does_not_exist_anywhere"))

    def test_premigrate_renames_a_real_boolean_column(self):
        pre_migrate = _load_migration("pre-migrate.py")
        cr = self.env.cr
        probe = "x_southbrook_test_legacy_probe"
        cr.execute(f'ALTER TABLE product_template ADD COLUMN "{probe}" boolean')
        try:
            self.assertTrue(
                pre_migrate._rename_boolean_column_to_legacy(cr, probe))
            cr.execute(
                """SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'product_template'
                   AND column_name = %s""",
                (f"{probe}_bool_legacy",),
            )
            self.assertTrue(cr.fetchone(), "renamed column must exist")
            cr.execute(
                """SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'product_template'
                   AND column_name = %s""",
                (probe,),
            )
            self.assertFalse(cr.fetchone(), "old column name must be gone")
        finally:
            cr.execute(f'ALTER TABLE product_template '
                       f'DROP COLUMN IF EXISTS "{probe}"')
            cr.execute(f'ALTER TABLE product_template '
                       f'DROP COLUMN IF EXISTS "{probe}_bool_legacy"')

    # ──────────────────────────────────────────────────────────────────
    # 19.0.2.0.0 migration — backfill (post-migrate), idempotent
    # ──────────────────────────────────────────────────────────────────
    def test_migration_backfill_maps_true_yes_false_not_assessed_idempotent(self):
        post_migrate = _load_migration("post-migrate.py")
        cr = self.env.cr

        product_true = self.Product.create({"name": "Legacy True Product"})
        product_false = self.Product.create({"name": "Legacy False Product"})

        legacy = "x_southbrook_hazardous_bool_legacy"
        cr.execute(f'ALTER TABLE product_template ADD COLUMN "{legacy}" boolean')
        cr.execute(
            f'UPDATE product_template SET "{legacy}" = TRUE WHERE id = %s',
            (product_true.id,),
        )
        cr.execute(
            f'UPDATE product_template SET "{legacy}" = FALSE WHERE id = %s',
            (product_false.id,),
        )
        # every other row (incl. product_false's siblings created by
        # other tests in this class) is left NULL by the bare ADD
        # COLUMN — post-migrate must treat NULL the same as False.

        first_counts = post_migrate._backfill_safety_three_state(self.env)
        self.assertIn("yes", first_counts["x_southbrook_hazardous"])
        self.assertGreaterEqual(first_counts["x_southbrook_hazardous"]["yes"], 1)

        product_true.invalidate_recordset()
        product_false.invalidate_recordset()
        self.assertEqual(product_true.x_southbrook_hazardous, "yes")
        self.assertEqual(product_false.x_southbrook_hazardous, "not_assessed")

        cr.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_name = 'product_template' AND column_name = %s""",
            (legacy,),
        )
        self.assertFalse(cr.fetchone(), "legacy column must be dropped")

        # Idempotent: legacy column is gone, so a second run must be a
        # pure no-op that does not stomp product_true's 'yes' back to
        # 'not_assessed'.
        second_counts = post_migrate._backfill_safety_three_state(self.env)
        self.assertEqual(
            second_counts["x_southbrook_hazardous"], {"skipped": True})
        product_true.invalidate_recordset()
        self.assertEqual(product_true.x_southbrook_hazardous, "yes")

    # ──────────────────────────────────────────────────────────────────
    # 19.0.2.0.0 migration — chemical product classification, idempotent
    # ──────────────────────────────────────────────────────────────────
    def test_classify_chemical_products_idempotent(self):
        post_migrate = _load_migration("post-migrate.py")
        product = self.Product.create({"name": "PUR Glue"})
        self.assertFalse(product.x_southbrook_tool_category_id)

        first_touched = post_migrate._classify_chemical_products(self.env)
        product.invalidate_recordset()
        self.assertIn("PUR Glue", first_touched)
        self.assertEqual(
            product.x_southbrook_tool_category_id,
            self.env.ref("southbrook_mrp_kitchen_tools.cat_glue_pur"),
        )

        second_touched = post_migrate._classify_chemical_products(self.env)
        self.assertNotIn(
            "PUR Glue", second_touched,
            "second run must not re-touch an already-classified product")

    def test_classify_chemical_products_never_overwrites_existing_category(self):
        post_migrate = _load_migration("post-migrate.py")
        # A product that happens to share one of the four exact names
        # but was already deliberately classified elsewhere — the
        # migration must match on name AND category IS NULL, never
        # clobber a human decision.
        product = self.Product.create({
            "name": "Cabinet Paint White",
            "x_southbrook_tool_category_id": self.cat_saw_blades.id,
        })
        touched = post_migrate._classify_chemical_products(self.env)
        self.assertNotIn("Cabinet Paint White", touched)
        product.invalidate_recordset()
        self.assertEqual(product.x_southbrook_tool_category_id, self.cat_saw_blades)

    def test_classify_eva_glue_cartridge_falls_back_to_adhesives_root(self):
        # cat_glue_eva's seeded name is "EVA Hot Melt Glue Pellets" —
        # pellet-specific — so "EVA Glue Cartridge" (a different form
        # factor) must land on the cat_adhesives ROOT, never that leaf.
        post_migrate = _load_migration("post-migrate.py")
        product = self.Product.create({"name": "EVA Glue Cartridge"})
        post_migrate._classify_chemical_products(self.env)
        product.invalidate_recordset()
        self.assertEqual(
            product.x_southbrook_tool_category_id, self.cat_adhesives)

    def test_classify_spray_booth_filter_uses_the_real_leaf(self):
        # cat_paint_booth_filter ("Booth Filters") exists under
        # cat_finishing — the contract's root-fallback is only for when
        # NO booth/filter leaf exists, so this must use the leaf.
        post_migrate = _load_migration("post-migrate.py")
        product = self.Product.create({"name": "Spray Booth Filter"})
        post_migrate._classify_chemical_products(self.env)
        product.invalidate_recordset()
        self.assertEqual(
            product.x_southbrook_tool_category_id,
            self.env.ref("southbrook_mrp_kitchen_tools.cat_paint_booth_filter"),
        )
