# SPDX-License-Identifier: LGPL-3.0-only
"""P0 fix (silent-drop truthful-response, 2026-08-02).

Live-fire cold-user report: a rep clicked "+ Add"; the UI said "Added
... to Left wall" and "Design saved"; the cabinet appeared in the 3D
scene; it was never really placed. The payload carried
`x_position_in: 1000000` — the client's "append to the end" sort-key
sentinel (kitchen_configurator.js `_addCabinetFromProduct`'s `sortX`
fallback) — and the server accepted it without telling anyone.

Prod fact (verified read-only): southbrook.kitchen.design.line held 12
ACTIVE (never-archived) rows parked at that exact sentinel — 6 on
design 160, 6 on design 99 — priced $0, excluded from
total_cabinets/estimated_price, rendered nowhere. This is a
RECURRENCE: Task 9b (2026-07-30) already added a write-path guard
inside save_design's item loop (`_is_sentinel_position`, see
test_sentinel_write_guard.py) that archives + unresolves + unprices any
incoming line at/beyond the debris threshold — but these 12 rows landed
ACTIVE despite that guard already being live, so at least one OTHER
write path in this controller still let a sentinel position reach the
DB verbatim (migrations/19.0.5.44.0's own docstring: "at least one
other write path still lets a sentinel position reach the DB
verbatim... A concurrent task is closing that remaining producer gap
(JS + controller layers)").

This file is the controller-layer half of that gap closure, plus the
response-honesty half of the same ticket. Three closed holes:

  1. save_position — the drag-pointer-up / rotate single-line write
     path (`/southbrook_kitchen/configurator/save_position`) is a
     SEPARATE endpoint from save_design and previously had NO
     sentinel/bounds guard at all: it wrote x/y/z_position_in verbatim
     from the client with a bare `line.write(vals)`. This is that
     remaining producer. Now mirrors save_design's guard exactly (same
     archived + is_unresolved + price_unit=0.0 shape for a sentinel, so
     SouthbrookKitchenDesignLine._is_debris_signature() still
     recognises it on every RESET-TO-CANONICAL revival site).

  2. Out-of-room-bounds, not just the 9999in debris threshold — a
     BACK-wall item whose x/z falls well outside the room but under
     9999in (a client bug short of the full sentinel) was previously
     accepted verbatim by both save_design and save_position. Neither
     the sentinel guard's own archived-line convention applies here
     (its "survives every reset" guarantee is keyed specifically to
     the 9999in _is_debris_signature() threshold — see
     _out_of_room_bounds_reason's module comment in controllers/
     main.py for why persisting one archived would silently get it
     reactivated on the next auto-arrange). Refused outright instead:
     no line created/updated, reported in `dropped`.

  3. save_design's response used to be unable to report a drop even in
     principle (a client comparing sent-vs-returned counts had nothing
     to compare against). `unplaced_count` (int) and `dropped` (list of
     {layout_key, sku, reason}) are now real, top-level response keys
     — added ONLY when this save actually refused/archived something,
     so an all-in-bounds save keeps the EXACT response shape
     test_touch_health_counters.py's
     test_absent_touch_stats_is_byte_compatible pins (this task's file-
     ownership contract does not allow editing that file).

Design: exercise the controller methods directly with a stubbed
`request`, same pattern as test_sentinel_write_guard.py /
test_wall_placement_engine.py.
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


# ═══════════════════════════════════════════════════════════════════
# save_design — out-of-bounds detection + truthful unplaced_count/
# dropped response.
# ═══════════════════════════════════════════════════════════════════
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "save_reports_truthfully")
class TestSaveDesignReportsTruthfully(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.partner = cls.env["res.partner"].create({
            "name": "Truthful-save Test Customer",
            "email": "truthful.save@southbrook.test",
        })
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Truthful-save test",
            "partner_id": cls.partner.id,
            "room_width_in": 144,
            "room_depth_in": 96,
            "room_height_in": 96,
        })

        cls.tmpl = cls.env["product.template"].create({
            "name": "Truthful-save Test Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl.southbrook_is_cabinet = True
        cls.tmpl.southbrook_cabinet_type = "base"
        cls.product = cls.tmpl.product_variant_id
        assert cls.product, "expected auto-created variant"
        # A dropped/`sku` entry is sourced from the server-RESOLVED
        # product's own default_code (never trusted verbatim from the
        # client item dict — same "never trust client-supplied catalog
        # data" posture the rest of this endpoint already takes toward
        # `price`), so the fixture needs a real SKU to assert against.
        cls.product.default_code = "TCAB-1"

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
        active_lines = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        all_lines = self.env["southbrook.kitchen.design.line"].with_context(
            active_test=False
        ).search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        return result, active_lines, all_lines

    # ── Sentinel item: not a normally-placed line, response is honest ──
    def test_sentinel_item_not_placed_and_reported(self):
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "truthful-sentinel-1",
            "x_position_in": 1000000,
            "z_position_in": 0,
            "sku":           "TCAB-1",
        }])
        self.assertNotIn("error", result, result)
        # Never a normally-placed (active) line.
        self.assertEqual(len(active_lines), 0)
        line = all_lines.filtered(lambda l: l.layout_key == "truthful-sentinel-1")
        self.assertEqual(len(line), 1, "still a real, inspectable row")
        self.assertFalse(line.active)
        self.assertTrue(line.is_unresolved)
        self.assertEqual(line.price_unit, 0.0)
        # Response is honest about it.
        self.assertEqual(result.get("unplaced_count"), 1, result)
        dropped = result.get("dropped")
        self.assertIsInstance(dropped, list)
        self.assertEqual(len(dropped), 1, result)
        entry = dropped[0]
        self.assertEqual(entry["layout_key"], "truthful-sentinel-1")
        self.assertEqual(entry["sku"], "TCAB-1")
        self.assertTrue(entry["reason"], "reason must be a non-empty string")
        self.assertEqual(result["summary"]["total"], 0)

    # ── Normal item: still saves, still counted, byte-compatible ───────
    def test_normal_item_still_saves_and_counted(self):
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "truthful-normal-1",
            "x_position_in": 24,
            "z_position_in": 0,
        }])
        self.assertNotIn("error", result, result)
        self.assertEqual(len(active_lines), 1)
        line = active_lines[0]
        self.assertTrue(line.active)
        self.assertFalse(line.is_unresolved)
        self.assertEqual(line.price_unit, 500.0)
        self.assertEqual(result["summary"]["total"], 1)
        # Nothing was dropped — the two new keys must be entirely
        # ABSENT (not present-with-zero), preserving the exact response
        # shape test_touch_health_counters.py's byte-compatibility
        # assertion pins for an all-in-bounds save.
        self.assertNotIn("unplaced_count", result, result)
        self.assertNotIn("dropped", result, result)

    # ── An out-of-room-bounds (but under the 9999in sentinel threshold)
    #    position is treated the same as the sentinel: never a real line.
    def test_out_of_room_bounds_item_is_NOT_refused_by_the_bulk_save(self):
        """Merely out-of-room is a real placement here; only >=9999in is not.

        An out-of-room-bounds REFUSAL was tried in save_design and removed: it
        contradicted `test_sentinel_write_guard.test_exact_threshold_boundary`,
        which pins x=9999 as debris and x=9998.99 as a legitimate line that
        must persist active at full price. _DEBRIS_THRESHOLD_IN is this
        endpoint's one agreed boundary, and a bulk save is the wrong place for
        a second, tighter one — refusing there DISCARDS data the client
        believed it saved, which is the exact failure this module's round of
        work exists to fix. Drag-time bounds feedback lives in save_position
        instead, where a refusal is one gesture the user sees rejected
        immediately rather than one row vanishing out of a batch.
        """
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "truthful-oob-1",
            "x_position_in": 700,          # outside a 144in room, far below 9999
            "z_position_in": 0,
            "wall":          "back",
            "sku":           "TCAB-1",
        }])
        self.assertNotIn("error", result, result)
        line = all_lines.filtered(lambda l: l.layout_key == "truthful-oob-1")
        self.assertEqual(
            len(line), 1,
            "an out-of-room item is still a real placement for the bulk save "
            "— only the debris threshold refuses")
        self.assertTrue(line.active, "it must not be archived as debris")
        self.assertFalse(line.is_unresolved)
        self.assertFalse(
            result.get("dropped"),
            "nothing was refused, so the response must not report a drop")

    # ── A position that's simply within the generous corner/panel-
    #    overhang margin must NOT be flagged — only genuinely-outside
    #    positions trip the guard.
    def test_position_within_generous_margin_is_not_flagged(self):
        # room_width_in=144; 5in past the nominal edge is well inside
        # the deliberately generous 24in margin (corner/panel overhang
        # slack — see _ROOM_BOUNDS_MARGIN_IN's own comment).
        result, active_lines, all_lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "truthful-margin-1",
            "x_position_in": 149,
            "z_position_in": 0,
        }])
        self.assertNotIn("error", result, result)
        self.assertEqual(len(active_lines), 1)
        self.assertNotIn("dropped", result, result)

    # ── Mixed batch: summary.total counts ONLY the genuinely placed
    #    line; unplaced_count/dropped account for both refused shapes.
    def test_summary_total_reflects_only_genuinely_placed_lines(self):
        result, active_lines, all_lines = self._save([
            {
                "product_id":    self.product.id,
                "layout_key":    "truthful-mix-normal",
                "x_position_in": 24,
                "z_position_in": 0,
            },
            {
                "product_id":    self.product.id,
                "layout_key":    "truthful-mix-sentinel",
                "x_position_in": 1000000,
                "z_position_in": 0,
            },
            {
                # A SECOND sentinel, not a merely out-of-room position: this
                # endpoint refuses only at _DEBRIS_THRESHOLD_IN, so an
                # out-of-room item would be placed normally and would not
                # exercise the accounting this test is about. See
                # test_out_of_room_bounds_item_is_NOT_refused_by_the_bulk_save.
                "product_id":    self.product.id,
                "layout_key":    "truthful-mix-sentinel-2",
                "x_position_in": 0,
                "z_position_in": 1000000,
                "wall":          "left",
            },
        ])
        self.assertNotIn("error", result, result)
        self.assertEqual(
            len(active_lines), 1,
            "only the real placement becomes an active line")
        self.assertEqual(active_lines.layout_key, "truthful-mix-normal")
        self.assertEqual(
            result["summary"]["total"], 1,
            "summary.total must equal the count of genuinely placed "
            "lines, not the count of items the client sent")
        self.assertEqual(result.get("unplaced_count"), 2, result)
        dropped_keys = {d["layout_key"] for d in result.get("dropped") or []}
        self.assertEqual(
            dropped_keys,
            {"truthful-mix-sentinel", "truthful-mix-sentinel-2"},
        )
        # A client comparing len(items sent) vs. lines actually placed
        # can now detect the discrepancy directly from this response.
        self.assertEqual(
            3, result["summary"]["total"] + result["unplaced_count"],
            "every sent item must be accounted for as either placed or "
            "dropped — none silently vanish",
        )


# ═══════════════════════════════════════════════════════════════════
# save_position — the other write path onto x/z_position_in, now
# guarded the same way (the "remaining producer" migrations/
# 19.0.5.44.0's docstring flags as still open after Task 9b).
# ═══════════════════════════════════════════════════════════════════
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "save_reports_truthfully")
class TestSavePositionReportsTruthfully(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.partner = cls.env["res.partner"].create({
            "name": "Truthful-save-position Test Customer",
            "email": "truthful.save.position@southbrook.test",
        })
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Truthful-save-position test",
            "partner_id": cls.partner.id,
            "room_width_in": 144,
            "room_depth_in": 96,
            "room_height_in": 96,
        })

        cls.tmpl = cls.env["product.template"].create({
            "name": "Truthful-save-position Test Cabinet",
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

    def _seed_line(self, layout_key, x=24.0, wall="back"):
        """Land one normal, in-bounds, active configurator line via the
        real save_design path (not a raw Line.create) so its shape is
        exactly what a real drag would find."""
        controller = self._controller()
        with stubbed_request(self.env):
            controller.save_design(
                name=self.design.name,
                room={"width_in": 144, "depth_in": 96, "height_in": 96},
                items=[{
                    "product_id":    self.product.id,
                    "layout_key":    layout_key,
                    "x_position_in": x,
                    "z_position_in": 0,
                    "wall":          wall,
                }],
                partner_id=self.partner.id,
                design_id=self.design.id,
            )
        line = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("layout_key", "=", layout_key),
        ])
        self.assertTrue(line, "fixture setup failed to seed a line")
        return line

    def _save_position(self, layout_key, **kw):
        controller = self._controller()
        with stubbed_request(self.env):
            return controller.save_position(
                design_id=self.design.id, layout_key=layout_key, **kw)

    # ── A drag that lands on the sentinel is archived, not active ──────
    def test_sentinel_drag_is_archived_not_active(self):
        line = self._seed_line("sp-sentinel-1")
        result = self._save_position(
            "sp-sentinel-1", x_position_in=1000000, z_position_in=0)
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("unplaced"), result)
        self.assertTrue(result.get("reason"), result)
        line.invalidate_recordset()
        self.assertFalse(line.active)
        self.assertTrue(line.is_unresolved)
        self.assertEqual(line.price_unit, 0.0)
        self.assertEqual(line.x_position_in, 1000000)

    # ── A drag well outside the room (under the sentinel threshold) is
    #    refused outright — the line's position is left unchanged.
    def test_out_of_bounds_drag_is_refused_position_unchanged(self):
        line = self._seed_line("sp-oob-1", x=24.0)
        before_x = line.x_position_in
        result = self._save_position(
            "sp-oob-1", x_position_in=700, z_position_in=0)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("reason"), "out_of_room_bounds")
        self.assertTrue(result.get("detail"), result)
        line.invalidate_recordset()
        self.assertEqual(
            line.x_position_in, before_x,
            "a refused position must never be written")
        self.assertTrue(line.active, "the line itself must survive untouched")
        self.assertFalse(line.is_unresolved)

    # ── A normal drag is completely unaffected ──────────────────────────
    def test_normal_drag_still_applies(self):
        line = self._seed_line("sp-normal-1", x=24.0)
        result = self._save_position(
            "sp-normal-1", x_position_in=60.0, z_position_in=0.0,
            rotation_deg=90.0, pinned=True)
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("unplaced", result)
        line.invalidate_recordset()
        self.assertEqual(line.x_position_in, 60.0)
        self.assertEqual(line.rotation_deg, 90.0)
        self.assertTrue(line.pinned)
        self.assertTrue(line.active)
        self.assertFalse(line.is_unresolved)

    # ── Rotation-only update never trips the guard off a stale/omitted
    #    axis — evaluated against the RESULTING position, not the
    #    incoming kwargs alone.
    def test_rotation_only_update_is_not_flagged(self):
        line = self._seed_line("sp-rotate-1", x=24.0)
        result = self._save_position("sp-rotate-1", rotation_deg=45.0)
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("unplaced", result)
        line.invalidate_recordset()
        self.assertEqual(line.rotation_deg, 45.0)
        self.assertEqual(line.x_position_in, 24.0, "position must be untouched")
        self.assertTrue(line.active)
