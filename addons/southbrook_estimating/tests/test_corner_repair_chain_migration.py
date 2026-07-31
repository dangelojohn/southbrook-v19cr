# SPDX-License-Identifier: LGPL-3.0-only
"""T4b (corner-remediation-2026-07-30 follow-up) — deploy-blocking gap.

`repair_corner_sku()` (which chains `repair_corner_family_skus()` then
`repair_cabinet_variant_refs()` — see models/corner_sku_repair.py) was
invoked ONLY from migrations/19.0.9.1.0/post-migrate.py. Prod
southbrook_estimating is ALREADY at 19.0.9.1.0, so that migration will
NEVER re-fire on the upcoming upgrade (Odoo only runs a version
folder's script when the module's recorded version is strictly below
it) — the whole corner-catalog promotion would silently never run on
prod. migrations/19.0.9.2.0/post-migrate.py closes that gap by calling
the SAME already-idempotent chain again.

Loads the migration module directly (Odoo migration scripts are not a
regular importable package), mirroring southbrook_kitchen_3d_
configurator/tests/test_debris_repair_migration.py's established
pattern.

Deliberately does NOT assume this DB's corner catalog is still in its
"factory" (unpromoted) state by the time these tests run: on a REAL
`-u` harness run (as opposed to calling repair_corner_family_skus
directly the way test_corner_family_repair.py does), the EXISTING
19.0.9.1.0/post-migrate.py already fires during module LOADING —
before any test executes — and that IS the T4/T4a/SBK-037 promotion
this migration also runs. By test time the catalog may therefore
already be fully promoted, which is itself proof the underlying chain
works (covered exhaustively by test_corner_family_repair.py and
test_cabinet_variant_ref_repair.py) — this file's job is narrower: does
THIS migration's own entry point (the falsy-version guard, and calling
the chain a SECOND time end-to-end) behave correctly regardless of
which state it starts from. Every assertion here is therefore a
before/after snapshot comparison, never an assumption about the
starting state.
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged


def _load_migration_module():
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.9.2.0",
        "post-migrate.py",
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_estimating_19_0_9_2_0_post_migrate", path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "corner_catalog_truth", "t4b_migration")
class TestCornerRepairChainMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration_module()
        cls.Template = cls.env["product.template"]
        cls.Product = cls.env["product.product"]

    def _tmpl(self, default_code):
        # Variant-first resolution (see corner_sku_repair.py's
        # _resolve_tmpl docstring): product.template.default_code
        # reads back False once a template has more than one variant.
        variant = self.Product.search(
            [("default_code", "=", default_code)], limit=1)
        tmpl = (variant.product_tmpl_id if variant else
                self.Template.search(
                    [("default_code", "=", default_code)], limit=1))
        if not tmpl:
            self.skipTest(
                "%s not seeded on this DB — catalog_expansion / "
                "kitchen_3d_configurator demo data may not be "
                "installed here." % default_code)
        return tmpl

    def _has_blind(self):
        return bool(self.Template.search(
            [("default_code", "=", "SB-CORNER-BLIND")], limit=1))

    def _snapshot(self):
        """Comparable state across every layer of the chain, regardless
        of whether the catalog is currently factory-state or already
        promoted — used for before/after equality checks rather than
        asserting a specific starting state."""
        blind = self.Template.search(
            [("default_code", "=", "SB-CORNER-BLIND")], limit=1)
        blind_flags = None
        if blind and "southbrook_cabinet_type" in self.Template._fields:
            blind_flags = (blind.southbrook_cabinet_type,
                            bool(blind.southbrook_is_cabinet),
                            blind.southbrook_width_in,
                            blind.southbrook_height_in,
                            blind.southbrook_depth_in)
        corner = self.Template.search(
            [("default_code", "=", "SB-CORNER")], limit=1)
        return {
            "blind_rh_ids": sorted(self.Product.search(
                [("default_code", "=", "SB-CORNER-BLIND-R")]).ids),
            "diag_rh_ids": sorted(self.Product.search(
                [("default_code", "=", "SB-CORNER-DIAG-R")]).ids),
            "wallcorner_rh_ids": sorted(self.Product.search(
                [("default_code", "=", "SB-WALL-CORNER-R")]).ids),
            "lsusan_active_ids": sorted(self.Template.search(
                [("default_code", "=", "SB-CORNER-LSUSAN")]).ids),
            "corner_name": corner.name if corner else None,
            "blind_flags": blind_flags,
        }

    def test_migrate_noop_on_falsy_version(self):
        """migrate() guards on `if not version: return` — matches the
        sibling migrations' convention (e.g. southbrook_kitchen_3d_
        configurator's 19.0.5.34.1/post-migrate.py, its own test
        test_migrate_noop_on_falsy_version). There is nothing to
        migrate from when version is falsy, regardless of whether the
        catalog is already promoted or not — nothing may change."""
        if not self._has_blind():
            self.skipTest("SB-CORNER-BLIND not seeded on this DB.")
        before = self._snapshot()
        self.migration.migrate(self.env.cr, False)
        after = self._snapshot()
        self.assertEqual(
            before, after,
            "a falsy version must not run the repair chain at all")

    def test_migrate_promotes_and_reaches_stable_end_state(self):
        """AC: after migrate() with a real version, the corner catalog
        is fully promoted (RH twins materialized, LSUSAN archived, the
        SB-CORNER rename applied) — asserted as an END STATE, not a
        before/after delta, since the catalog may already have been
        promoted by the real 19.0.9.1.0 migration during THIS SAME
        harness run's module loading (which happens before any test
        executes) — that's expected on this harness, not a test bug,
        and is exactly the scenario T4b exists to also cover on a DB
        where 19.0.9.1.0 predates the upgrade and never re-fires."""
        if not self._has_blind():
            self.skipTest("SB-CORNER-BLIND not seeded on this DB.")

        self.migration.migrate(self.env.cr, "19.0.9.1.0")

        blind = self._tmpl("SB-CORNER-BLIND")
        rh = self.Product.search(
            [("default_code", "=", "SB-CORNER-BLIND-R")])
        self.assertTrue(
            rh, "the migration must materialize the BLIND RH twin")
        if "southbrook_cabinet_type" in self.Template._fields:
            self.assertEqual(blind.southbrook_cabinet_type, "corner")
            self.assertTrue(blind.southbrook_is_cabinet)

        self.assertFalse(
            self.Template.search(
                [("default_code", "=", "SB-CORNER-LSUSAN")]),
            "the migration must archive every active SB-CORNER-LSUSAN")

        corner = self._tmpl("SB-CORNER")
        self.assertEqual(corner.name, "Corner Base · Lazy-Susan 36")

    def test_second_call_is_idempotent(self):
        """AC: a second migrate() call is a no-op at every layer of the
        chain (T4a + T4 + SBK-037 each already guarantee this
        independently — see their own idempotency tests — this asserts
        it also holds through the migration entry point itself)."""
        if not self._has_blind():
            self.skipTest("SB-CORNER-BLIND not seeded on this DB.")

        self.migration.migrate(self.env.cr, "19.0.9.1.0")
        snapshot_before = self._snapshot()

        self.migration.migrate(self.env.cr, "19.0.9.1.0")
        snapshot_after = self._snapshot()

        self.assertEqual(
            snapshot_before, snapshot_after,
            "a second migrate() call must be a strict no-op")
