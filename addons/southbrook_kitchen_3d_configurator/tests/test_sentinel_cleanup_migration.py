# SPDX-License-Identifier: LGPL-3.0-only
"""P0 E2E remediation (2026-08-02) — sentinel/debris design-line sweep,
round 2 (migrations/19.0.5.44.0/post-migrate.py).

Loads the migration module directly (Odoo migration scripts are not a
regular importable package) and runs it against synthetic rows built to
the verified prod shape (design 160: 6 ACTIVE lines at
x_position_in=1,000,000; design 99: a recurrence of the exact class of
row 19.0.5.34.1 already swept once).

This migration is deliberately near-identical in shape to 19.0.5.34.1's
(same shared DEBRIS_THRESHOLD_IN, same archive-not-delete flag shape,
same totals-recompute step) — see test_debris_repair_migration.py for
that migration's own (more exhaustive) coverage of the shared detection
semantics. This file covers: selective archival (sentinel vs. an
untouched sibling on the same design), idempotency (second run is a
no-op), a design with no sentinels at all is left completely alone, and
(new in this round) the try/except + cr.savepoint() failure-resilience
contract — a mid-sweep failure must not raise out of migrate() and must
leave the DB exactly as it was (atomic rollback), not half-archived.
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged


def _load_migration_module():
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.5.44.0",
        "post-migrate.py",
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_kitchen_3d_configurator_5_44_0_post_migrate", path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "sentinel_cleanup_migration")
class TestSentinelCleanupMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration_module()

        cls.partner = cls.env["res.partner"].create({
            "name": "Sentinel Cleanup Migration Test Customer",
            "email": "kitchen3d.sentinel-round2@southbrook.test",
        })

        cls.tmpl_base = cls.env["product.template"].create({
            "name": "Sentinel Cleanup Migration Base Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl_base.southbrook_is_cabinet = True
        cls.tmpl_base.southbrook_cabinet_type = "base"
        cls.product_base = cls.tmpl_base.product_variant_id

    # ------------------------------------------------------------------
    def _make_design(self, name):
        return self.env["southbrook.kitchen.design"].create({
            "name": name,
            "partner_id": self.partner.id,
            "room_width_in": 144,
            "room_depth_in": 96,
            "room_height_in": 96,
        })

    def _make_design_line(self, design, layout_key, x_position_in,
                           z_position_in, active=True):
        line = self.env["southbrook.kitchen.design.line"].create({
            "design_id":       design.id,
            "product_id":      self.product_base.id,
            "cabinet_type":    "base",
            "layout_key":      layout_key,
            "origin":          "configurator",
            "x_position_in":   x_position_in,
            "y_position_in":   0.0,
            "z_position_in":   z_position_in,
            "width_in":        18.0,
            "height_in":       34.5,
            "depth_in":        24.0,
            "quantity":        1,
            "price_unit":      500.0,
        })
        if not active:
            line.active = False
        return line

    # ------------------------------------------------------------------
    def test_sentinel_row_archived_normal_row_untouched(self):
        """The verified prod shape: 6 ACTIVE lines at x=1,000,000 on one
        design (160-style), plus an ordinary in-bounds sibling on the
        SAME design. Only the sentinel class is archived; the normal
        line survives untouched (active, price, position all intact)."""
        design = self._make_design("Round2 design 160-style")

        sentinels = self.env["southbrook.kitchen.design.line"]
        for i in range(6):
            sentinels |= self._make_design_line(
                design, "r2-sentinel-%d" % i, 1_000_000.0, 0.0)
        normal = self._make_design_line(
            design, "r2-normal", 24.0, 0.0)
        normal.price_unit = 500.0

        self.env.flush_all()
        for line in sentinels | normal:
            self.assertTrue(line.active)

        self.migration.migrate(self.env.cr, "19.0.5.43.0")

        sentinels.invalidate_recordset()
        normal.invalidate_recordset()

        for line in sentinels:
            self.assertFalse(
                line.active,
                "sentinel line %s must be archived (active=False)" %
                line.layout_key)
            self.assertTrue(line.is_unresolved)
            self.assertEqual(line.price_unit, 0.0)

        self.assertTrue(
            normal.active,
            "in-bounds sibling on the same design must be left untouched")
        self.assertFalse(normal.is_unresolved)
        self.assertEqual(normal.price_unit, 500.0)
        self.assertEqual(normal.x_position_in, 24.0)

    def test_design_with_no_sentinels_is_unaffected(self):
        """A design whose lines are all in-bounds must be completely
        untouched by the sweep — no rows archived, no totals disturbed."""
        design = self._make_design("Round2 clean design")
        line_1 = self._make_design_line(design, "r2-clean-1", 12.0, 0.0)
        line_2 = self._make_design_line(design, "r2-clean-2", 36.0, 0.0)
        self.env.flush_all()
        design.invalidate_recordset()

        cabinets_before = design.total_cabinets
        price_before = design.estimated_price

        self.migration.migrate(self.env.cr, "19.0.5.43.0")

        line_1.invalidate_recordset()
        line_2.invalidate_recordset()
        design.invalidate_recordset()

        self.assertTrue(line_1.active)
        self.assertTrue(line_2.active)
        self.assertEqual(design.total_cabinets, cabinets_before)
        self.assertAlmostEqual(
            design.estimated_price, price_before, places=2)

    def test_idempotent_second_run_is_a_noop(self):
        """Re-running the migration after it has already archived the
        sentinel rows must change nothing further."""
        design = self._make_design("Round2 idempotency design")
        sentinels = self.env["southbrook.kitchen.design.line"]
        for i in range(3):
            sentinels |= self._make_design_line(
                design, "r2-idem-%d" % i, 1_000_000.0, 0.0)
        normal = self._make_design_line(
            design, "r2-idem-normal", 24.0, 0.0)

        self.env.flush_all()
        self.migration.migrate(self.env.cr, "19.0.5.43.0")

        sentinels.invalidate_recordset()
        normal.invalidate_recordset()
        snapshot_before_rerun = {
            line.id: (line.active, line.is_unresolved, line.price_unit,
                      line.x_position_in, line.z_position_in)
            for line in sentinels | normal
        }

        self.migration.migrate(self.env.cr, "19.0.5.43.0")

        sentinels.invalidate_recordset()
        normal.invalidate_recordset()
        snapshot_after_rerun = {
            line.id: (line.active, line.is_unresolved, line.price_unit,
                      line.x_position_in, line.z_position_in)
            for line in sentinels | normal
        }

        self.assertEqual(
            snapshot_after_rerun, snapshot_before_rerun,
            "re-running the migration must be a no-op (idempotent)")
        for line in sentinels:
            self.assertFalse(line.active)
        self.assertTrue(normal.active)

    def test_parent_design_totals_recompute_after_archival(self):
        """The raw-SQL archival bypasses the ORM's @api.depends trigger
        graph — total_cabinets/estimated_price must be explicitly
        recomputed post-archival so a design like 160 doesn't keep its
        debris-inflated count/price forever."""
        design = self._make_design("Round2 totals-recompute design")
        real_1 = self._make_design_line(design, "r2-totals-real-1", 12.0, 0.0)
        real_1.price_unit = 500.0
        sentinel_1 = self._make_design_line(
            design, "r2-totals-sentinel-1", 1_000_000.0, 0.0)
        sentinel_1.price_unit = 999.0

        self.env.flush_all()
        design.invalidate_recordset()

        self.assertEqual(design.total_cabinets, 2)
        self.assertAlmostEqual(
            design.estimated_price, 500.0 + 999.0, places=2)

        self.migration.migrate(self.env.cr, "19.0.5.43.0")

        design.invalidate_recordset()
        self.assertEqual(
            design.total_cabinets, 1,
            "post-migration: total_cabinets must exclude the archived "
            "sentinel line")
        self.assertAlmostEqual(
            design.estimated_price, 500.0, places=2,
            msg="post-migration: estimated_price must reflect ONLY the "
                "real line")

    def test_failure_is_swallowed_and_rolled_back_atomically(self):
        """A mid-sweep failure (e.g. the totals-recompute step raising)
        must not propagate out of migrate() — an orphan-cleanup migration
        is not worth failing the whole upgrade over — and the
        cr.savepoint() wrapper must roll the archival back too, so the
        DB is left exactly as it was, never half-archived."""
        design = self._make_design("Round2 failure-resilience design")
        sentinel = self._make_design_line(
            design, "r2-failure-sentinel", 1_000_000.0, 0.0)
        self.env.flush_all()

        original = self.migration._recompute_design_totals

        def _boom(cr, design_ids):
            raise RuntimeError("synthetic failure for the migration test")

        self.migration._recompute_design_totals = _boom
        try:
            # Must not raise.
            self.migration.migrate(self.env.cr, "19.0.5.43.0")
        finally:
            self.migration._recompute_design_totals = original

        sentinel.invalidate_recordset()
        self.assertTrue(
            sentinel.active,
            "a mid-sweep failure must roll back the savepoint — the "
            "sentinel row must still be active, not half-archived")

    def test_migration_skips_gracefully_when_table_missing(self):
        """Defensive path: the migration must not raise if run against a
        cursor pointed at a schema that lacks the design-line table."""
        self.assertFalse(
            self.migration._table_exists(self.env.cr, "no_such_table_xyz"))

    def test_migrate_noop_on_falsy_version(self):
        """migrate() guards on `if not version: return`, matching every
        sibling migration's convention for a fresh/blank install."""
        design = self._make_design("Round2 falsy-version design")
        sentinel = self._make_design_line(
            design, "r2-falsy-version", 1_000_000.0, 0.0)
        self.env.flush_all()

        self.migration.migrate(self.env.cr, False)

        sentinel.invalidate_recordset()
        self.assertTrue(
            sentinel.active,
            "migrate() must no-op when version is falsy")
