# SPDX-License-Identifier: LGPL-3.0-only
"""Corner-orientation fix (2026-07-31) — regression suite for the
19.0.5.36.1 repair migration (migrations/19.0.5.36.1/post-migrate.py).

Loads the migration module directly (Odoo migration scripts are not a
regular importable package), mirroring test_debris_repair_migration.py /
test_corner_repair_chain_migration.py's established pattern.

Covers: the exact verified prod shape (design 108's back-left base + wall
corner lines, x=0/z=36in/rotation_deg=90 -> x=0/z=0/rotation_deg=0),
pinned lines left completely untouched (USER DATA), repair idempotence (a
second run changes nothing further), an already-correct row left as-is,
and the AABB safety net skipping (never corrupting) an asymmetric row
whose footprint would genuinely change.
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged


def _load_migration_module():
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.5.36.1",
        "post-migrate.py",
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_kitchen_3d_configurator_5_36_1_post_migrate", path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_orientation_migration")
class TestCornerOrientationRepairMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration_module()
        cls.partner = cls.env["res.partner"].create(
            {"name": "Corner Orientation Migration Test Customer"})
        cls.corner_prod = cls.env["product.product"].create(
            {"name": "COR-Migration Corner Base",
             "default_code": "ZZ-CORMIG-CORNER", "list_price": 625.0})
        cls.wall_corner_prod = cls.env["product.product"].create(
            {"name": "COR-Migration Corner Wall",
             "default_code": "ZZ-CORMIG-WCORNER", "list_price": 295.0})

    def _design(self, room_width_in=120.0, room_depth_in=96.0):
        return self.env["southbrook.kitchen.design"].create({
            "name": "Corner orientation migration test",
            "partner_id": self.partner.id,
            "room_width_in": room_width_in, "room_depth_in": room_depth_in,
            "room_height_in": 96.0,
        })

    def _corner_line(self, design, layout_key, product, **vals):
        base_vals = {
            "design_id": design.id, "product_id": product.id,
            "quantity": 1, "price_unit": product.list_price,
            "cabinet_type": "corner", "origin": "configurator",
            "layout_key": layout_key, "layout_role": "derived",
            "pinned": False,
        }
        base_vals.update(vals)
        return self.env["southbrook.kitchen.design.line"].create(base_vals)

    def test_design_108_shaped_back_left_lines_repaired_to_room_corner(self):
        """The exact verified prod shape (design 108, corner-remediation
        brief item 2, CLIENT EMPHASIS 2026-07-31): a base-layer AND a
        wall-layer (upper) back-left corner line — BOTH tiers, not just
        base — both persisted at the OLD wrong pose (x=0, z=<their own
        depth_in>, rotation_deg=90) -> both become (x=0, z=0,
        rotation_deg=0). The base and wall lines carry DIFFERENT
        y_position_in (real elevations, 0 vs 30in — a base corner sits on
        the floor, a wall corner is mounted above the counter) to prove
        the repair only ever touches x/z/rotation_deg."""
        design = self._design(room_width_in=120.0, room_depth_in=96.0)
        base_line = self._corner_line(
            design, "corner-%s-back-left-base" % design.id, self.corner_prod,
            zone="base_run", width_in=36.0, height_in=34.5, depth_in=36.0,
            wall="left", rotation_deg=90.0, y_position_in=0.0,
            x_position_in=0.0, z_position_in=36.0)
        wall_line = self._corner_line(
            design, "corner-%s-back-left-wall" % design.id,
            self.wall_corner_prod,
            zone="wall", width_in=24.0, height_in=30.0, depth_in=24.0,
            wall="left", rotation_deg=90.0, y_position_in=55.12,
            x_position_in=0.0, z_position_in=24.0)

        self.migration.migrate(self.env.cr, "19.0.5.36.0")
        base_line.invalidate_recordset()
        wall_line.invalidate_recordset()

        for line, want_y in ((base_line, 0.0), (wall_line, 55.12)):
            self.assertAlmostEqual(line.x_position_in, 0.0, places=2,
                                    msg=line.layout_key)
            self.assertAlmostEqual(line.z_position_in, 0.0, places=2,
                                    msg=line.layout_key)
            self.assertAlmostEqual(line.rotation_deg, 0.0, places=1,
                                    msg=line.layout_key)
            # y_position_in (elevation) is NEVER touched by this repair.
            self.assertAlmostEqual(line.y_position_in, want_y, places=2,
                                    msg=line.layout_key)

        # CLIENT EMPHASIS: the stacked base+wall pair at ONE junction
        # must agree in x/z/rotation_deg after repair — pinned directly,
        # not just inferred from both matching the same target
        # separately.
        self.assertAlmostEqual(base_line.x_position_in,
                               wall_line.x_position_in, places=2)
        self.assertAlmostEqual(base_line.z_position_in,
                               wall_line.z_position_in, places=2)
        self.assertAlmostEqual(base_line.rotation_deg,
                               wall_line.rotation_deg, places=1)
        self.assertNotEqual(
            base_line.y_position_in, wall_line.y_position_in,
            "base and wall corners must keep their OWN, different "
            "elevations — only x/z/rotation_deg are shared")

    def test_back_right_already_correct_line_is_a_noop(self):
        """back-right was already correct pre-fix (270 degrees) — a line
        already at its ROOM_CORNER_PLACEMENT-target pose must not be
        rewritten to a numerically-different-but-equivalent value (write
        timestamps etc. should not churn for rows the bug never
        touched)."""
        design = self._design(room_width_in=120.0, room_depth_in=96.0)
        line = self._corner_line(
            design, "corner-%s-back-right-base" % design.id,
            self.corner_prod,
            zone="base_run", width_in=36.0, height_in=34.5, depth_in=36.0,
            wall="right", rotation_deg=270.0,
            x_position_in=120.0, z_position_in=0.0)
        write_date_before = line.write_date

        self.migration.migrate(self.env.cr, "19.0.5.36.0")
        line.invalidate_recordset()

        self.assertAlmostEqual(line.x_position_in, 120.0, places=2)
        self.assertAlmostEqual(line.z_position_in, 0.0, places=2)
        self.assertAlmostEqual(line.rotation_deg, 270.0, places=1)
        self.assertEqual(line.write_date, write_date_before,
                          "an already-correct row must not be re-written")

    def test_pinned_corner_line_is_never_touched(self):
        """pinned=True is USER DATA — the migration must skip it even
        though it carries the exact verified-bug pose."""
        design = self._design(room_width_in=120.0, room_depth_in=96.0)
        line = self._corner_line(
            design, "corner-%s-back-left-base" % design.id, self.corner_prod,
            zone="base_run", width_in=36.0, height_in=34.5, depth_in=36.0,
            wall="left", rotation_deg=90.0,
            x_position_in=0.0, z_position_in=36.0, pinned=True)

        self.migration.migrate(self.env.cr, "19.0.5.36.0")
        line.invalidate_recordset()

        self.assertAlmostEqual(line.x_position_in, 0.0, places=2)
        self.assertAlmostEqual(line.z_position_in, 36.0, places=2)
        self.assertAlmostEqual(line.rotation_deg, 90.0, places=1)

    def test_migrate_noop_on_falsy_version(self):
        """migrate() guards on `if not version: return` — same convention
        as every sibling migration in this addon."""
        design = self._design(room_width_in=120.0, room_depth_in=96.0)
        line = self._corner_line(
            design, "corner-%s-back-left-base" % design.id, self.corner_prod,
            zone="base_run", width_in=36.0, height_in=34.5, depth_in=36.0,
            wall="left", rotation_deg=90.0,
            x_position_in=0.0, z_position_in=36.0)

        self.migration.migrate(self.env.cr, False)
        line.invalidate_recordset()

        self.assertAlmostEqual(line.x_position_in, 0.0, places=2)
        self.assertAlmostEqual(line.z_position_in, 36.0, places=2)
        self.assertAlmostEqual(line.rotation_deg, 90.0, places=1)

    def test_repair_is_idempotent(self):
        design = self._design(room_width_in=120.0, room_depth_in=96.0)
        line = self._corner_line(
            design, "corner-%s-back-left-base" % design.id, self.corner_prod,
            zone="base_run", width_in=36.0, height_in=34.5, depth_in=36.0,
            wall="left", rotation_deg=90.0,
            x_position_in=0.0, z_position_in=36.0)

        self.migration.migrate(self.env.cr, "19.0.5.36.0")
        line.invalidate_recordset()
        snapshot = (line.x_position_in, line.z_position_in,
                    line.rotation_deg)
        first_write_date = line.write_date

        self.migration.migrate(self.env.cr, "19.0.5.36.0")
        line.invalidate_recordset()

        self.assertEqual(
            (line.x_position_in, line.z_position_in, line.rotation_deg),
            snapshot)
        self.assertEqual(line.write_date, first_write_date,
                          "a second run must not re-write an already"
                          " repaired row")

    def test_asymmetric_footprint_break_is_skipped_not_corrupted(self):
        """Safety net: a corner line whose width_in != depth_in, planted
        at a pose that does NOT correspond to a real corner cell for
        those dimensions, must be left completely untouched rather than
        have its (x, z, rotation_deg) rewritten out from under its
        existing (possibly mislabeled) width_in/depth_in — the migration
        must never corrupt occupancy."""
        design = self._design(room_width_in=120.0, room_depth_in=96.0)
        line = self._corner_line(
            design, "corner-%s-back-left-base" % design.id, self.corner_prod,
            zone="base_run", width_in=36.0, height_in=34.5, depth_in=24.0,
            wall="left", rotation_deg=90.0,
            # Deliberately NOT a real anchor-pose value for these
            # (asymmetric) dimensions — repairing pose alone (without
            # also correcting width_in/depth_in, which this migration
            # never touches) would change the footprint.
            x_position_in=5.0, z_position_in=5.0)

        self.migration.migrate(self.env.cr, "19.0.5.36.0")
        line.invalidate_recordset()

        self.assertAlmostEqual(line.x_position_in, 5.0, places=2)
        self.assertAlmostEqual(line.z_position_in, 5.0, places=2)
        self.assertAlmostEqual(line.rotation_deg, 90.0, places=1)
        # width_in/depth_in are never touched by this migration, period.
        self.assertAlmostEqual(line.width_in, 36.0, places=2)
        self.assertAlmostEqual(line.depth_in, 24.0, places=2)
