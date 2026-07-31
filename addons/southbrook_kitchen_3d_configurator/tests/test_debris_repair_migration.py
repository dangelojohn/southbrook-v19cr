# SPDX-License-Identifier: LGPL-3.0-only
"""Task 9 — debris repair migration
(migrations/19.0.5.34.1/post-migrate.py).

Loads the migration module directly (Odoo migration scripts are not a
regular importable package) and runs it against synthetic rows built to
the verified prod shape (design 99: 5 ACTIVE lines at
x_position_in=1,000,000).

Covers: selective archival (only the sentinel class, in-bounds lines
untouched), the x/z OR condition, the exact-threshold boundary
(abs() >= 9999), multi-design grouping, an already-archived debris row
staying archived (never reactivated), and idempotency (a second run is a
no-op).
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged


def _load_migration_module():
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.5.34.1",
        "post-migrate.py",
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_kitchen_3d_configurator_5_34_1_post_migrate", path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "debris_repair_migration")
class TestDebrisRepairMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration_module()

        cls.partner = cls.env["res.partner"].create({
            "name": "Task 9 Debris Migration Test Customer",
            "email": "kitchen3d.task9@southbrook.test",
        })

        cls.tmpl_base = cls.env["product.template"].create({
            "name": "Task 9 Debris Migration Base Cabinet",
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

    def _debris_count(self, ids):
        self.env.cr.execute(
            "SELECT count(*) FROM southbrook_kitchen_design_line "
            "WHERE active = true "
            "AND (abs(x_position_in) >= 9999 OR abs(z_position_in) >= 9999) "
            "AND id = ANY(%s)",
            (ids,),
        )
        return self.env.cr.fetchone()[0]

    # ------------------------------------------------------------------
    def test_selective_archival_x_and_z_sentinels_only(self):
        """The verified prod shape: 5 ACTIVE lines at x=1,000,000 on one
        design (99-style), plus a z-sentinel variant and ordinary
        in-bounds lines on the SAME design. Only the debris class is
        archived; in-bounds siblings are untouched."""
        design = self._make_design("Task9 design 99-style")

        debris_x = self.env["southbrook.kitchen.design.line"]
        for i in range(5):
            debris_x |= self._make_design_line(
                design, "t9-debris-x-%d" % i, 1_000_000.0, 0.0)

        # A z-axis sentinel variant — the OR branch of the WHERE clause.
        debris_z = self._make_design_line(
            design, "t9-debris-z", 0.0, 1_000_000.0)

        # In-bounds siblings on the same design — must survive untouched.
        normal_1 = self._make_design_line(
            design, "t9-normal-1", 24.0, 0.0)
        normal_2 = self._make_design_line(
            design, "t9-normal-2", 48.0, 0.0)

        self.env.flush_all()

        all_debris = debris_x | debris_z
        self.assertEqual(
            self._debris_count(all_debris.ids), 6,
            "pre-migration: all 6 synthetic debris rows must match the "
            "migration's own discriminator (red state)")
        self.assertEqual(self._debris_count(normal_1.ids), 0)
        self.assertEqual(self._debris_count(normal_2.ids), 0)

        for line in all_debris | normal_1 | normal_2:
            self.assertTrue(line.active)

        # ── run the migration ───────────────────────────────────────
        self.migration.migrate(self.env.cr, "19.0.5.34.0")

        all_debris.invalidate_recordset()
        normal_1.invalidate_recordset()
        normal_2.invalidate_recordset()

        for line in all_debris:
            self.assertFalse(
                line.active,
                "debris line %s must be archived (active=False) by the "
                "migration" % line.layout_key)
        self.assertTrue(
            normal_1.active,
            "in-bounds line must be left untouched by the migration")
        self.assertTrue(
            normal_2.active,
            "in-bounds line must be left untouched by the migration")
        # positions themselves are never rewritten — archival only.
        self.assertEqual(normal_1.x_position_in, 24.0)
        self.assertEqual(normal_2.x_position_in, 48.0)

        self.assertEqual(
            self._debris_count(all_debris.ids), 0,
            "post-migration: 0 rows should match the discriminator "
            "(6 -> 0 acceptance check, since they're now active=False)")

    def test_exact_threshold_boundary(self):
        """abs(...) >= 9999 is inclusive at exactly 9999 and excludes
        9998.99 — a real room is never anywhere near this range, but
        the boundary itself must behave exactly as specified."""
        design = self._make_design("Task9 boundary design")
        just_under = self._make_design_line(
            design, "t9-boundary-under", 9998.99, 0.0)
        exactly_at = self._make_design_line(
            design, "t9-boundary-at", 9999.0, 0.0)
        negative_over = self._make_design_line(
            design, "t9-boundary-neg", 0.0, -9999.5)

        self.env.flush_all()
        self.migration.migrate(self.env.cr, "19.0.5.34.0")

        just_under.invalidate_recordset()
        exactly_at.invalidate_recordset()
        negative_over.invalidate_recordset()

        self.assertTrue(
            just_under.active,
            "9998.99 is below the 9999 threshold — must NOT be archived")
        self.assertFalse(
            exactly_at.active,
            "exactly 9999 must be archived (>= is inclusive)")
        self.assertFalse(
            negative_over.active,
            "abs(-9999.5) >= 9999 — negative sentinels must be archived "
            "too")

    def test_already_archived_debris_stays_archived_not_reactivated(self):
        """A debris row that was manually archived before this migration
        ever ran must remain active=False — the migration only ever sets
        active=False, never flips it back to True."""
        design = self._make_design("Task9 pre-archived design")
        pre_archived = self._make_design_line(
            design, "t9-pre-archived", 1_000_000.0, 0.0, active=False)

        self.env.flush_all()
        self.migration.migrate(self.env.cr, "19.0.5.34.0")

        pre_archived.invalidate_recordset()
        self.assertFalse(pre_archived.active)

    def test_multi_design_grouping(self):
        """Debris spread across two separate designs is archived on
        both; each design's in-bounds lines are independently
        untouched."""
        design_a = self._make_design("Task9 design A")
        design_b = self._make_design("Task9 design B")

        debris_a = self._make_design_line(
            design_a, "t9-multi-a-debris", 1_000_000.0, 0.0)
        normal_a = self._make_design_line(
            design_a, "t9-multi-a-normal", 12.0, 0.0)
        debris_b1 = self._make_design_line(
            design_b, "t9-multi-b-debris-1", 1_000_000.0, 0.0)
        debris_b2 = self._make_design_line(
            design_b, "t9-multi-b-debris-2", 1_000_000.0, 0.0)
        normal_b = self._make_design_line(
            design_b, "t9-multi-b-normal", 36.0, 0.0)

        self.env.flush_all()
        self.migration.migrate(self.env.cr, "19.0.5.34.0")

        for line in (debris_a, debris_b1, debris_b2):
            line.invalidate_recordset()
            self.assertFalse(line.active)
        for line in (normal_a, normal_b):
            line.invalidate_recordset()
            self.assertTrue(line.active)

    def test_idempotent_second_run_is_a_noop(self):
        """Re-running the migration after it has already archived the
        debris must change nothing further (safe to run twice, per the
        binding constraint)."""
        design = self._make_design("Task9 idempotency design")
        debris = self.env["southbrook.kitchen.design.line"]
        for i in range(3):
            debris |= self._make_design_line(
                design, "t9-idem-%d" % i, 1_000_000.0, 0.0)
        normal = self._make_design_line(
            design, "t9-idem-normal", 24.0, 0.0)

        self.env.flush_all()
        self.migration.migrate(self.env.cr, "19.0.5.34.0")

        debris.invalidate_recordset()
        normal.invalidate_recordset()
        snapshot_before_rerun = {
            line.id: (line.active, line.x_position_in, line.z_position_in)
            for line in debris | normal
        }

        self.migration.migrate(self.env.cr, "19.0.5.34.0")

        debris.invalidate_recordset()
        normal.invalidate_recordset()
        snapshot_after_rerun = {
            line.id: (line.active, line.x_position_in, line.z_position_in)
            for line in debris | normal
        }

        self.assertEqual(
            snapshot_after_rerun, snapshot_before_rerun,
            "re-running the migration must be a no-op (idempotent)")
        for line in debris:
            self.assertFalse(line.active)
        self.assertTrue(normal.active)

    def test_migration_skips_gracefully_when_table_missing(self):
        """Defensive path: the migration must not raise if run against a
        cursor pointed at a schema that lacks the design-line table (a
        malformed/partial install)."""
        self.assertFalse(
            self.migration._table_exists(self.env.cr, "no_such_table_xyz"))

    def test_migrate_noop_on_falsy_version(self):
        """migrate() guards on `if not version: return` (matches the
        sibling 19.0.5.18.0 migration's convention for a fresh/blank
        install where there is nothing to migrate)."""
        design = self._make_design("Task9 falsy-version design")
        debris = self._make_design_line(
            design, "t9-falsy-version", 1_000_000.0, 0.0)
        self.env.flush_all()

        self.migration.migrate(self.env.cr, False)

        debris.invalidate_recordset()
        self.assertTrue(
            debris.active,
            "migrate() must no-op when version is falsy")
