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
