# SPDX-License-Identifier: LGPL-3.0-only
"""Task 9b (corner-remediation, 2026-07-30) — server-side sentinel guard
on save_design's line-write path.

Background (task-9-report.md's "producer" section): the client's 1e6
sort-key sentinel (kitchen_configurator.js `_addCabinetFromProduct`'s
`sortX` fallback) is meant to be overwritten by a real packed position
before save, but `packRow` is skipped whenever `multiWall` is true —
exactly the corner/L-shape/U-shape kitchens this remediation project is
about. Before this fix, save_design's line-write path
(controllers/main.py) persisted incoming x/z_position_in verbatim, so
the still-unconsumed sentinel landed in the DB as an active, priced,
"real" line — the confirmed prod fact (design 99,
x_position_in=1,000,000, priced, counted, invisible).

This suite covers the write-path guard directly (not the migration,
which repairs debris already in the DB — see test_debris_repair_
migration.py): any incoming line whose x or z position is at/beyond the
debris threshold (either axis, either sign) must never be persisted as
an active, positioned-for-billing, priced line. It's written archived +
is_unresolved + $0 instead — a real, inspectable row, never silently
dropped, never billed, never counted.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)


@contextmanager
def stubbed_request(env):
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env
    mock.session = {}
    mock.params = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "sentinel_write_guard")
class TestSentinelWriteGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.partner = cls.env["res.partner"].create({
            "name": "Sentinel Guard Test Customer",
            "email": "sentinel.guard@southbrook.test",
        })
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Sentinel-guard test",
            "partner_id": cls.partner.id,
            "room_width_in": 144,
            "room_depth_in": 96,
            "room_height_in": 96,
        })

        cls.tmpl = cls.env["product.template"].create({
            "name": "Sentinel Guard Test Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl.southbrook_is_cabinet = True
        cls.tmpl.southbrook_cabinet_type = "base"
        cls.product = cls.tmpl.product_variant_id
        assert cls.product, "expected auto-created variant"

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _save(self, items, design_id=None):
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.save_design(
                name=self.design.name,
                room={"width_in": 144, "depth_in": 96, "height_in": 96},
                items=items,
                partner_id=self.partner.id,
                design_id=self.design.id if design_id is None else design_id,
            )
        # Active-only view (what the client/UI/totals see):
        active_lines = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        # Unscoped view (what's actually in the DB, archived or not):
        all_lines = self.env["southbrook.kitchen.design.line"].with_context(
            active_test=False
        ).search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        return result, active_lines, all_lines

    def _line_by_key(self, all_lines, key):
        line = all_lines.filtered(lambda l: l.layout_key == key)
        self.assertEqual(len(line), 1, f"expected exactly one line for {key!r}")
        return line

    # ── Core: an x-sentinel is archived, unresolved, unpriced ─────────
    def test_x_sentinel_persisted_archived_unresolved_unpriced(self):
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "sentinel-x-1",
            "x_position_in": 1000000,
            "z_position_in": 0,
        }])
        self.assertNotIn("error", result)
        line = self._line_by_key(all_lines, "sentinel-x-1")
        self.assertFalse(line.active, "sentinel line must be archived")
        self.assertTrue(line.is_unresolved, "sentinel line must be flagged is_unresolved")
        self.assertEqual(line.price_unit, 0.0, "sentinel line must never be priced")
        self.assertEqual(
            len(active_lines), 0,
            "the sentinel line must not appear in the active/default view",
        )

    # ── Core: a z-sentinel is caught too (not just x) ──────────────────
    def test_z_sentinel_persisted_archived_unresolved_unpriced(self):
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "sentinel-z-1",
            "x_position_in": 0,
            "z_position_in": 1000000,
        }])
        self.assertNotIn("error", result)
        line = self._line_by_key(all_lines, "sentinel-z-1")
        self.assertFalse(line.active)
        self.assertTrue(line.is_unresolved)
        self.assertEqual(line.price_unit, 0.0)

    # ── abs() semantics: a negative sentinel is caught too ─────────────
    def test_negative_sentinel_persisted_archived(self):
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "sentinel-neg-1",
            "x_position_in": -1000000,
            "z_position_in": 0,
        }])
        self.assertNotIn("error", result)
        line = self._line_by_key(all_lines, "sentinel-neg-1")
        self.assertFalse(line.active)
        self.assertTrue(line.is_unresolved)

    # ── Boundary: exactly at threshold is a sentinel; just under is not ─
    def test_exact_threshold_boundary(self):
        result, active_lines, all_lines = self._save([
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-boundary-at",
                "x_position_in": 9999.0,
                "z_position_in": 0,
            },
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-boundary-under",
                "x_position_in": 9998.99,
                "z_position_in": 0,
            },
        ])
        self.assertNotIn("error", result)
        at_line = self._line_by_key(all_lines, "sentinel-boundary-at")
        under_line = self._line_by_key(all_lines, "sentinel-boundary-under")
        self.assertFalse(at_line.active, "exactly 9999 must be treated as sentinel")
        self.assertTrue(at_line.is_unresolved)
        self.assertTrue(under_line.active, "9998.99 must NOT be treated as sentinel")
        self.assertFalse(under_line.is_unresolved)
        self.assertEqual(under_line.price_unit, 500.0)

    # ── Normal lines in the same batch are completely unaffected ───────
    def test_normal_lines_unaffected_when_mixed_with_sentinel(self):
        result, active_lines, all_lines = self._save([
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-mix-normal",
                "x_position_in": 24,
                "z_position_in": 0,
            },
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-mix-debris",
                "x_position_in": 1000000,
                "z_position_in": 0,
            },
        ])
        self.assertNotIn("error", result)
        normal = self._line_by_key(all_lines, "sentinel-mix-normal")
        debris = self._line_by_key(all_lines, "sentinel-mix-debris")
        self.assertTrue(normal.active)
        self.assertFalse(normal.is_unresolved)
        self.assertEqual(normal.price_unit, 500.0)
        self.assertEqual(normal.x_position_in, 24)
        self.assertFalse(debris.active)
        self.assertTrue(debris.is_unresolved)
        self.assertEqual(debris.price_unit, 0.0)
        self.assertEqual(len(active_lines), 1)
        self.assertEqual(active_lines.layout_key, "sentinel-mix-normal")

    # ── Design totals exclude the sentinel line entirely ────────────────
    def test_design_totals_exclude_sentinel_line(self):
        self._save([
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-totals-normal",
                "x_position_in": 24,
                "z_position_in": 0,
            },
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-totals-debris",
                "x_position_in": 0,
                "z_position_in": 1000000,
            },
        ])
        self.design.invalidate_recordset()
        self.assertEqual(
            self.design.total_cabinets, 1,
            "sentinel line must not count toward total_cabinets",
        )
        self.assertEqual(
            self.design.base_count, 1,
            "sentinel line must not count toward base_count",
        )
        self.assertEqual(
            self.design.estimated_price, 500.0,
            "sentinel line must not contribute to estimated_price",
        )

    # ── The write-side guard never fails the whole save ─────────────────
    def test_sentinel_does_not_fail_whole_save(self):
        """A sentinel item must not reject the batch the way an invalid
        `wall` does (test_wall_persistence.py's validate-all-items-first
        contract) — it's a per-line write-time clamp, not a payload
        validation error."""
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "sentinel-no-fail",
            "x_position_in": 1000000,
            "z_position_in": 1000000,
        }])
        self.assertNotIn("error", result)
        self.assertEqual(result.get("id"), self.design.id)
        self.assertEqual(len(all_lines), 1, "the item must still land as a real row")

    # ── Review round (Finding 1, CRITICAL) ──────────────────────────────
    # A multi-wall save (2+ walls) makes save_design call
    # action_auto_arrange(sync=False) IN THE SAME REQUEST (walls_used >=
    # 2) — exactly the multiWall scenario the sentinel originates from
    # (task-9-report.md's producer section: packRow is skipped once
    # multiWall is true). Before the review-round fix,
    # action_auto_arrange's RESET-TO-CANONICAL step
    # (kitchen_design.py, `ctx.cabinet_line_ids.filtered(lambda l: not
    # l.active)...write({"active": True})`) blanket-reactivated EVERY
    # archived line, including the sentinel THIS SAME save had just
    # archived a few lines above — then handed it a real engine pose,
    # completely undoing the guard in the one scenario it exists for.
    # Fixed by excluding is_unresolved lines from that reactivation
    # (same fix mirrored in `_transform_layout`'s reset for
    # flip/rotate). Recipe (2-wall corner resolution) mirrors
    # test_wall_placement_engine.py's
    # test_save_response_includes_placed_poses_for_wall_lines fixture
    # (1 back + 1 left cabinet), which is known to trigger real corner
    # resolution on this harness (baseline-green, not on the red list).
    def test_sentinel_survives_multiwall_auto_arrange(self):
        result, active_lines, all_lines = self._save([
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-mw-left",
                "x_position_in": 999.0,
                "z_position_in": 0.0,
                "wall":          "left",
            },
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-mw-back",
                "x_position_in": 5.0,
                "z_position_in": 0.0,
                "wall":          "back",
            },
            {
                "product_id":    self.product.id,
                "layout_key":    "sentinel-mw-debris",
                "x_position_in": 1000000.0,
                "z_position_in": 0.0,
                "wall":          "back",
            },
        ])
        self.assertNotIn("error", result, result)
        # Sanity: this save must actually have spanned 2+ walls and
        # triggered action_auto_arrange, or this test isn't exercising
        # the bug at all.
        self.assertTrue(
            result.get("relaid"), "expected corner resolution to have "
            "run (walls_used >= 2) — fixture didn't reach the code path "
            "under test: %r" % result)
        sentinel = self._line_by_key(all_lines, "sentinel-mw-debris")
        self.assertFalse(
            sentinel.active,
            "sentinel line must still be archived AFTER "
            "action_auto_arrange's reset step, not reactivated")
        self.assertTrue(sentinel.is_unresolved)
        self.assertEqual(sentinel.price_unit, 0.0)
        self.assertEqual(
            sentinel.x_position_in, 1000000.0,
            "sentinel must never have been handed a real engine pose")
        self.assertNotIn(
            "sentinel-mw-debris",
            {p["layout_key"] for p in result.get("placed") or []},
            "sentinel must not appear in the placed/engine-posed echo")

    # ── Review round (Finding 3a) — the warning names design + product ──
    def test_sentinel_warning_logs_design_and_product(self):
        logger_name = (
            "odoo.addons.southbrook_kitchen_3d_configurator.controllers.main"
        )
        with self.assertLogs(logger_name, level="WARNING") as cm:
            self._save([{
                "product_id":    self.product.id,
                "layout_key":    "sentinel-log-1",
                "x_position_in": 1000000.0,
                "z_position_in": 0.0,
            }])
        matching = [msg for msg in cm.output if "sentinel" in msg.lower()]
        self.assertTrue(matching, cm.output)
        self.assertIn(str(self.design.id), matching[0], matching[0])
        self.assertIn(str(self.product.id), matching[0], matching[0])
        self.assertIn("sentinel-log-1", matching[0], matching[0])

    # ── Review round (Finding 3b) — resave-in-bounds orphan is documented
    def test_resave_same_layout_key_in_bounds_leaves_old_row_archived(self):
        """Pins the 'benign duplicate' claim: a layout_key first saved as
        a sentinel (archived) then resubmitted in-bounds does NOT revive
        the old archived row — existing_by_key only ever contains ACTIVE
        configurator lines, so the resave creates a NEW row under the
        same layout_key. The old archived debris row is left orphaned
        (harmless: permanently excluded from totals/price/placement,
        still a real, inspectable row) — the same archive-never-delete
        posture the Task 9 migration already established."""
        self._save([{
            "product_id":    self.product.id,
            "layout_key":    "sentinel-resave-1",
            "x_position_in": 1000000.0,
            "z_position_in": 0.0,
        }])
        result2, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "sentinel-resave-1",
            "x_position_in": 30.0,
            "z_position_in": 0.0,
        }])
        self.assertNotIn("error", result2, result2)
        same_key_rows = all_lines.filtered(
            lambda l: l.layout_key == "sentinel-resave-1")
        self.assertEqual(
            len(same_key_rows), 2,
            "expected the old archived debris row PLUS a new active row "
            "under the same layout_key")
        archived_rows = same_key_rows.filtered(lambda l: not l.active)
        active_rows = same_key_rows.filtered(lambda l: l.active)
        self.assertEqual(len(archived_rows), 1, "old debris row must remain")
        self.assertTrue(archived_rows.is_unresolved)
        self.assertEqual(archived_rows.price_unit, 0.0)
        self.assertEqual(archived_rows.x_position_in, 1000000.0)
        self.assertEqual(len(active_rows), 1, "new in-bounds row must exist")
        self.assertFalse(active_rows.is_unresolved)
        self.assertEqual(active_rows.x_position_in, 30.0)
        self.assertEqual(active_rows.price_unit, 500.0)
