# SPDX-License-Identifier: LGPL-3.0-only
"""Fix (design-naming, prod fact 2026-07-31) regression tests.

Defect: `southbrook.kitchen.design.name` is a plain Char, auto-set
ONCE at first save (controllers/main.py's `_auto_name`, gated by
`if not design_id` in `save_design`). A later room resize updated
`room_width_in`/`room_depth_in` in the same write() path but never
regenerated the name — so the design tree/landing page could show a
name that flatly contradicts the record's real dimensions. Prod fact
(verified read-only): design 103 was named "Walk-in - 12x30 -
2026-07-30" while its ACTUAL room dims were 54x54; 4 of the last 11
designs (98/100/101/103) showed the same drift.

Two halves are pinned here:

1. `TestDesignAutoNameRegeneration` — the LIVE write-path fix in
   `save_design`: a resize regenerates the name ONLY when the
   currently-persisted name is still recognisably auto-generated
   (`is_auto_generated_name()`); a human rename permanently opts a
   design out, even across later resizes; first-save behaviour is
   byte-for-byte unchanged.

2. `TestStaleAutoNameBackfillMigration` —
   migrations/19.0.5.35.1/post-migrate.py, the one-time backfill for
   rows that went stale BEFORE the write-path fix existed. Loads the
   migration module directly (Odoo migration folders are not a
   regular importable package — same technique as the sibling
   test_debris_repair_migration.py in this test suite).
"""
import importlib.util
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)


@contextmanager
def stubbed_request(env, user=None):
    """Same pattern as test_save_design_acl.py's stubbed_request —
    lets save_design() run outside an actual HTTP request."""
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env if user is None else env(user=user.id)
    mock.session = {}
    mock.params = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


def _load_migration_module():
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.5.35.1",
        "post-migrate.py",
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_kitchen_3d_configurator_5_35_1_post_migrate", path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "design_naming")
class TestDesignAutoNameRegeneration(TransactionCase):
    """save_design's resize-path name regeneration
    (controllers/main.py)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Design Naming Test Customer",
            "email": "kitchen3d.naming@southbrook.test",
        })

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _save(self, design_id, name, width, depth, height=96):
        controller = self._controller()
        with stubbed_request(self.env):
            return controller.save_design(
                name=name,
                room={
                    "width_in": width, "depth_in": depth,
                    "height_in": height,
                },
                items=[],
                partner_id=self.partner.id,
                design_id=design_id,
            )

    def _expected_auto_name(self, width, depth):
        """Builds the name via the SAME shared formatter save_design
        itself calls (auto_name_str) — not a hand-typed second copy of
        the format — so this asserts "matches what the real generator
        produces for these inputs", not just "looks similar"."""
        today = fields.Date.context_today(self.env.user).strftime(
            "%Y-%m-%d")
        return ctrl_main.auto_name_str(
            self.partner.display_name, width, depth, today)

    # ------------------------------------------------------------------
    def test_new_design_first_save_name_unchanged(self):
        """Requirement 3 — a brand-new design's first-save name must
        be exactly what _auto_name() has always produced; this fix
        must not alter first-save behaviour at all."""
        result = self._save(False, "New Kitchen Design", 120, 96)
        design = self.env["southbrook.kitchen.design"].browse(result["id"])

        self.assertTrue(
            ctrl_main.is_auto_generated_name(design.name),
            "a brand-new design must still get an auto-generated name")
        self.assertEqual(design.name, self._expected_auto_name(120, 96))

    def test_resize_of_auto_named_design_regenerates_name(self):
        """A design whose name is still auto-generated: resizing the
        room must regenerate the name to match the NEW dimensions."""
        result = self._save(False, "New Kitchen Design", 120, 96)
        design_id = result["id"]
        design = self.env["southbrook.kitchen.design"].browse(design_id)
        original_name = design.name
        self.assertTrue(ctrl_main.is_auto_generated_name(original_name))

        # Real client contract (kitchen_configurator.js): every
        # autosave resends the CURRENTLY displayed name verbatim
        # alongside whatever room dims are live in state — here that's
        # the unchanged original_name plus new dims.
        self._save(design_id, original_name, 144, 108)
        design.invalidate_recordset()

        self.assertNotEqual(
            design.name, original_name,
            "resizing an auto-named design must regenerate the name")
        self.assertEqual(design.name, self._expected_auto_name(144, 108))
        self.assertEqual(design.room_width_in, 144)
        self.assertEqual(design.room_depth_in, 108)

    def test_resize_of_manually_renamed_design_leaves_name_untouched(self):
        """A human rename permanently opts a design out of
        auto-regeneration — a later resize must never clobber it."""
        result = self._save(False, "New Kitchen Design", 120, 96)
        design_id = result["id"]
        design = self.env["southbrook.kitchen.design"].browse(design_id)

        manual_name = "Smith Family Reno — DO NOT AUTO-RENAME"
        self._save(design_id, manual_name, 120, 96)
        design.invalidate_recordset()
        self.assertEqual(design.name, manual_name)
        self.assertFalse(ctrl_main.is_auto_generated_name(design.name))

        # Client resends the (manual) current name unchanged, plus new
        # dims — a plain resize of an already-renamed design.
        self._save(design_id, manual_name, 144, 108)
        design.invalidate_recordset()

        self.assertEqual(
            design.name, manual_name,
            "a manually-renamed design's name must NEVER be clobbered "
            "by a resize, even though the room dims did change")
        self.assertEqual(design.room_width_in, 144)
        self.assertEqual(design.room_depth_in, 108)

    def test_simultaneous_rename_and_resize_manual_name_wins(self):
        """Edge case guarded by condition (b) in save_design: a single
        request that BOTH renames AND resizes must persist the human's
        new name outright, never the freshly-derived auto name (even
        though the OLD stored name was still auto-shaped going into
        this call)."""
        result = self._save(False, "New Kitchen Design", 120, 96)
        design_id = result["id"]
        design = self.env["southbrook.kitchen.design"].browse(design_id)
        self.assertTrue(ctrl_main.is_auto_generated_name(design.name))

        brand_new_manual_name = "Totally Custom Name, Set This Save"
        self._save(design_id, brand_new_manual_name, 144, 108)
        design.invalidate_recordset()

        self.assertEqual(
            design.name, brand_new_manual_name,
            "a simultaneous rename+resize must persist the human's "
            "chosen name, not an auto-regenerated one")
        self.assertEqual(design.room_width_in, 144)
        self.assertEqual(design.room_depth_in, 108)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "design_naming")
class TestStaleAutoNameBackfillMigration(TransactionCase):
    """migrations/19.0.5.35.1/post-migrate.py — the one-time backfill
    for designs that went stale before the write-path fix existed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration_module()
        cls.partner = cls.env["res.partner"].create({
            "name": "Migration Naming Test Customer",
            "email": "kitchen3d.naming.migration@southbrook.test",
        })

    def _make_design(self, name, width, depth):
        return self.env["southbrook.kitchen.design"].create({
            "name": name,
            "partner_id": self.partner.id,
            "room_width_in": width,
            "room_depth_in": depth,
            "room_height_in": 96,
        })

    def _stale_name(self, width, depth):
        # A synthetic "auto name" whose EMBEDDED dims disagree with
        # whatever room_width_in/room_depth_in the row is actually
        # created with below — the exact prod shape (design 103).
        return "%s - %dx%d - 2026-07-30" % (
            self.partner.display_name, width, depth)

    # ------------------------------------------------------------------
    def test_renames_stale_row_leaves_manual_and_agreeing_rows_alone(self):
        stale = self._make_design(self._stale_name(12, 30), 54, 54)
        manual = self._make_design("Smith Reno — keep me", 60, 40)
        agrees = self._make_design(self._stale_name(60, 40), 60, 40)
        self.env.flush_all()

        self.migration.migrate(self.env.cr, "19.0.5.35.0")

        stale.invalidate_recordset()
        manual.invalidate_recordset()
        agrees.invalidate_recordset()

        self.assertTrue(
            ctrl_main.is_auto_generated_name(stale.name),
            "regenerated name must still be auto-shaped")
        self.assertNotEqual(stale.name, self._stale_name(12, 30))
        m = ctrl_main.AUTO_NAME_RE.match(stale.name)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group("width")), 54)
        self.assertEqual(int(m.group("depth")), 54)

        self.assertEqual(
            manual.name, "Smith Reno — keep me",
            "a manually-named row must never be touched by the "
            "migration")
        self.assertEqual(
            agrees.name, self._stale_name(60, 40),
            "a row whose embedded dims already agree with current "
            "dims is not stale — must be left alone")

    def test_migration_idempotent_second_run_is_noop(self):
        stale = self._make_design(self._stale_name(12, 30), 54, 54)
        self.env.flush_all()

        self.migration.migrate(self.env.cr, "19.0.5.35.0")
        stale.invalidate_recordset()
        renamed_once = stale.name
        self.assertNotEqual(renamed_once, self._stale_name(12, 30))

        self.migration.migrate(self.env.cr, "19.0.5.35.0")
        stale.invalidate_recordset()

        self.assertEqual(
            stale.name, renamed_once,
            "re-running the migration must be a no-op once the "
            "embedded dims already agree with current dims")

    def test_migration_skips_gracefully_when_table_missing(self):
        self.assertFalse(
            self.migration._table_exists(self.env.cr, "no_such_table_xyz"))

    def test_migrate_noop_on_falsy_version(self):
        stale = self._make_design(self._stale_name(12, 30), 54, 54)
        self.env.flush_all()

        self.migration.migrate(self.env.cr, False)

        stale.invalidate_recordset()
        self.assertEqual(
            stale.name, self._stale_name(12, 30),
            "migrate() must no-op when version is falsy")
