# SPDX-License-Identifier: LGPL-3.0-only
"""Debt fix (2026-08-02) — thumbFamily explicit footprint fields.

Regression suite for the 19.0.5.39.1 backfill migration
(migrations/19.0.5.39.1/post-migrate.py), which writes
`product.template.southbrook_thumb_family` for every
`southbrook_is_cabinet` template whose field is currently unset, computing
the value with a literal Python port of `cabinet_thumb.esm.js`'s
`thumbFamily()` fallback logic.

Loads the migration module directly (Odoo migration scripts are not a
regular importable package), same pattern as this addon's
test_debris_repair_migration.py.

Covers: known-SKU correctness across all 11 families (including the
BLIND/DIAG/LSUSAN/WALL-CORNER code-keyword branches and the name-keyword
fallback), never overwriting a value a human already set (whether it
agrees with the inference or not), idempotency (a second run changes
nothing further), non-cabinet templates are never touched, and the
falsy-version no-op guard every migration in this addon follows.
"""
import importlib.util
import os

from odoo.tests import TransactionCase, tagged


def _load_migration_module():
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.5.39.1",
        "post-migrate.py",
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_kitchen_3d_configurator_5_39_1_post_migrate", path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "thumb_family_backfill_migration")
class TestThumbFamilyBackfillMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = _load_migration_module()

    # ------------------------------------------------------------------
    def _make_cabinet(self, name, cabinet_type, default_code=None,
                       thumb_family=None, active=True):
        vals = {
            "name": name,
            "type": "consu",
            "sale_ok": True,
            "list_price": 100.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": cabinet_type,
        }
        if default_code:
            vals["default_code"] = default_code
        tmpl = self.env["product.template"].create(vals)
        if thumb_family:
            tmpl.southbrook_thumb_family = thumb_family
        if not active:
            tmpl.active = False
        return tmpl

    def _make_noncabinet(self, name):
        return self.env["product.template"].create({
            "name": name,
            "type": "consu",
            "sale_ok": True,
            "list_price": 50.0,
            "southbrook_is_cabinet": False,
        })

    def _run(self, version="19.0.5.39.0"):
        self.migration.migrate(self.env.cr, version)

    # ── known-SKU correctness ────────────────────────────────────────
    def test_backfill_corner_code_keyword_branches(self):
        blind = self._make_cabinet(
            "Blind Corner Base 42", "corner", "SB-CORNER-BLIND-42")
        diag = self._make_cabinet(
            "Diagonal Corner Base 36", "corner", "SB-CORNER-DIAG-36")
        lsusan = self._make_cabinet(
            "Lazy Susan Corner 33", "corner", "SB-LSUSAN-33")
        wall_corner = self._make_cabinet(
            "Wall Corner 24", "corner", "SB-WALL-CORNER-24")
        # The shipped canonical "Corner Cabinet" shape: plain SB-CORNER,
        # no BLIND/DIAG/LSUSAN/WALL-CORNER keyword anywhere in the code
        # or name — falls to thumbFamily()'s generic corner default.
        plain = self._make_cabinet("Corner Cabinet", "corner", "SB-CORNER")

        self._run()
        for tmpl in (blind, diag, lsusan, wall_corner, plain):
            tmpl.invalidate_recordset()

        self.assertEqual(blind.southbrook_thumb_family, "cornerBlind")
        self.assertEqual(diag.southbrook_thumb_family, "cornerDiag")
        self.assertEqual(lsusan.southbrook_thumb_family, "cornerDiag")
        self.assertEqual(wall_corner.southbrook_thumb_family, "cornerL")
        self.assertEqual(
            plain.southbrook_thumb_family, "cornerL",
            "a bare SB-CORNER code (no recognised keyword) backfills to "
            "the same generic bucket thumbFamily()'s own fallback would "
            "show today — this migration snapshots current behaviour, "
            "it does not correct it")

    def test_backfill_corner_name_keyword_fallback_when_code_absent(self):
        blind = self._make_cabinet(
            "Blind Corner Cabinet 42", "corner", default_code=None)
        diag = self._make_cabinet(
            "Diagonal Corner Cabinet 36", "corner", default_code=None)

        self._run()
        blind.invalidate_recordset()
        diag.invalidate_recordset()

        self.assertEqual(blind.southbrook_thumb_family, "cornerBlind")
        self.assertEqual(diag.southbrook_thumb_family, "cornerDiag")

    def test_backfill_top_level_bucket_mapping(self):
        wall = self._make_cabinet("Wall Cabinet 24", "wall", "SB-WALL-24")
        tall = self._make_cabinet("Tall Pantry 24", "tall", "SB-TALL-24")
        panel = self._make_cabinet("End Panel 24", "panel", "SB-PANEL-24")
        filler = self._make_cabinet("Filler Strip", "filler", "SB-FILLER-3")

        self._run()
        for tmpl in (wall, tall, panel, filler):
            tmpl.invalidate_recordset()

        self.assertEqual(wall.southbrook_thumb_family, "wall")
        self.assertEqual(tall.southbrook_thumb_family, "tall")
        self.assertEqual(panel.southbrook_thumb_family, "panel")
        self.assertEqual(
            filler.southbrook_thumb_family, "panel",
            "fillers share the panel bucket, same as thumbFamily()")

    def test_backfill_base_bucket_name_keyword_fallback(self):
        sink = self._make_cabinet("Sink Base 36", "base", "SB-SINK-36")
        vanity = self._make_cabinet("Vanity Base 30", "base", "SB-VANITY-30")
        drawer = self._make_cabinet("Drawer Bank 24", "base", "SB-DRW-24")
        base2 = self._make_cabinet(
            "Base Cabinet 2-Door 30", "base", "SB-BASE2-30")
        base1 = self._make_cabinet("Base Cabinet 24", "base", "SB-BASE-24")

        self._run()
        for tmpl in (sink, vanity, drawer, base2, base1):
            tmpl.invalidate_recordset()

        self.assertEqual(sink.southbrook_thumb_family, "sink")
        self.assertEqual(vanity.southbrook_thumb_family, "vanity")
        self.assertEqual(drawer.southbrook_thumb_family, "drawer")
        self.assertEqual(base2.southbrook_thumb_family, "base2")
        self.assertEqual(base1.southbrook_thumb_family, "base1")

    # ── never overwrite a human's choice ─────────────────────────────
    def test_never_overwrites_a_preexisting_value_even_when_it_disagrees(self):
        """A rep already set this BLIND-coded product to cornerDiag
        (maybe it's genuinely a diagonal unit mis-coded) — the migration
        must leave that human choice completely untouched, even though
        it disagrees with what the inference would compute."""
        tmpl = self._make_cabinet(
            "Blind Corner Base 42", "corner", "SB-CORNER-BLIND-42",
            thumb_family="cornerDiag")

        self._run()
        tmpl.invalidate_recordset()

        self.assertEqual(
            tmpl.southbrook_thumb_family, "cornerDiag",
            "pre-set value must survive untouched, agreement with "
            "inference is irrelevant")

    def test_never_touches_non_cabinet_templates(self):
        tmpl = self._make_noncabinet("Random Non-Cabinet Product")
        self._run()
        tmpl.invalidate_recordset()
        self.assertFalse(tmpl.southbrook_thumb_family)

    def test_archived_cabinet_template_is_still_backfilled(self):
        """active_test=False — an archived cabinet keeps benefiting from
        an explicit, inspectable icon family."""
        tmpl = self._make_cabinet(
            "Archived Wall Cabinet 24", "wall", "SB-WALL-OLD-24",
            active=False)
        self._run()
        tmpl.invalidate_recordset()
        self.assertEqual(tmpl.southbrook_thumb_family, "wall")

    # ── idempotency / falsy-version guard ────────────────────────────
    def test_idempotent_second_run_changes_nothing_further(self):
        tmpl = self._make_cabinet(
            "Drawer Bank 24", "base", "SB-DRW-24")
        preset = self._make_cabinet(
            "Blind Corner Base 42", "corner", "SB-CORNER-BLIND-42",
            thumb_family="cornerDiag")

        self._run()
        tmpl.invalidate_recordset()
        preset.invalidate_recordset()
        after_first = (tmpl.southbrook_thumb_family, preset.southbrook_thumb_family)

        self._run()
        tmpl.invalidate_recordset()
        preset.invalidate_recordset()
        after_second = (tmpl.southbrook_thumb_family, preset.southbrook_thumb_family)

        self.assertEqual(after_first, after_second)
        self.assertEqual(after_first, ("drawer", "cornerDiag"))

    def test_migrate_noop_on_falsy_version(self):
        tmpl = self._make_cabinet("Drawer Bank 24", "base", "SB-DRW-24")
        self.migration.migrate(self.env.cr, False)
        tmpl.invalidate_recordset()
        self.assertFalse(
            tmpl.southbrook_thumb_family,
            "migrate() must no-op when version is falsy")
