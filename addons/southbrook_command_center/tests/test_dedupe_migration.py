# SPDX-License-Identifier: LGPL-3.0-only
"""Migration 19.0.3.0.0: collapse the duplicate-exception backlog.

Seeds a synthetic version of the production shape: duplicates from the keying
bug, with the human-touched-one election rule verified.
"""
import importlib.util
import os

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestDedupeMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exc = cls.env["southbrook.command.exception"]
        cls.owner = cls.env.ref("base.user_admin")
        # Hermetic to the ambient environment: force the Phase-2 hooks
        # kill-switch OFF so creating southbrook.mi.check rows below never
        # auto-materializes an Exception via _sb_cc_materialize (that path
        # is exercised by test_phase2.py, not here). Every test in this file
        # constructs an exact synthetic duplicate-row shape and asserts an
        # exact count on it; that must hold regardless of what
        # command_center.hooks_enabled happens to be set to in whatever
        # database this suite runs against.
        cls.env["ir.config_parameter"].sudo().set_param(
            "command_center.hooks_enabled", "0")

    def _vals(self, **kw):
        """Build base exception values."""
        v = {
            "severity": "critical",
            "severity_rank": 0,
            "owner_id": self.owner.id,
            "impact_summary": "Missing cutlist",
            "recommended_action": "Link the cutlist",
            "why_text": "no cutlist on the MO",
        }
        v.update(kw)
        return v

    def _load_migration(self):
        """Import the migration module using importlib."""
        # __file__ is at .../southbrook_command_center/tests/test_dedupe_migration.py
        # dirname(__file__) = .../southbrook_command_center/tests
        # dirname(dirname(__file__)) = .../southbrook_command_center
        addon_path = os.path.dirname(os.path.dirname(__file__))
        migration_path = os.path.join(
            addon_path,
            "migrations",
            "19.0.3.0.0",
            "post-dedupe-exceptions.py",
        )
        spec = importlib.util.spec_from_file_location(
            "post_dedupe_exceptions",
            migration_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_migrate_elects_human_touched_survivor(self):
        """The migration elects one survivor per live condition, preferring a
        human-touched one. All others are archived with NULL dedupe_key."""
        # Create an MI check for the "cut" category (with required fields).
        mo = self.env["mrp.production"].search([], limit=1)

        check = self.env["southbrook.mi.check"].create({
            "name": "Missing cutlist",
            "message": "Cutlist divergence detected",  # required field
            "category": "cut",
            "severity": "blocker",
            "production_id": mo.id if mo else False,
        })

        # Create 12 duplicate exceptions for the same condition, with distinct
        # source_res_id values (simulating the keying bug where the sweep
        # recreates mi.check rows and mints a new exception each time).
        exceptions = []
        for i in range(12):
            exc = self.Exc.create({
                "exception_type": "cutlist_divergence",
                "source_model": "southbrook.mi.check",
                "source_res_id": 900000 + i,  # Distinct ids (old broken rows)
                "mo_id": mo.id,
                # dedupe_key remains NULL (this is the bug state)
                **self._vals(),
            })
            exceptions.append(exc)

        # One of them is human-touched (not "new").
        touched_one = exceptions[5]
        touched_one.action_acknowledge()  # State -> "acknowledged"
        self.assertEqual(touched_one.state, "acknowledged")

        # Count before migration.
        total_before = self.Exc.search_count([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
        ])
        self.assertEqual(total_before, 12)

        # Run the migration.
        migration = self._load_migration()
        migration.migrate(self.env.cr, "19.0.2.0.0")

        # Reload the exceptions (they're now modified).
        self.Exc.invalidate_recordset()

        # Assert: exactly one survivor with active=True.
        survivors = self.Exc.search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
            ("active", "=", True),
        ])
        self.assertEqual(len(survivors), 1,
                         "Should have exactly one active exception")
        survivor = survivors[0]

        # The survivor is the human-touched one.
        self.assertEqual(survivor.id, touched_one.id,
                         "Survivor must be the human-touched exception")
        self.assertEqual(survivor.state, "acknowledged")

        # The survivor has a non-null dedupe_key.
        self.assertTrue(survivor.dedupe_key,
                        "Survivor must have a dedupe_key")

        # The survivor's source_res_id matches the live check id.
        self.assertEqual(survivor.source_res_id, check.id,
                         "Survivor source_res_id must point to the live check")

        # No rows were deleted (total count unchanged).
        # Use with_context(active_test=False) to count all
        all_count_after = self.Exc.with_context(active_test=False).search_count([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
        ])
        self.assertEqual(all_count_after, 12,
                         "No rows should be deleted, only archived")

        # Every archived row has dedupe_key=NULL.
        archived = self.Exc.with_context(active_test=False).search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
            ("active", "=", False),
        ])
        self.assertEqual(len(archived), 11, "11 rows should be archived")
        for arc in archived:
            self.assertFalse(arc.dedupe_key,
                             f"Archived row {arc.id} must have NULL dedupe_key")

    def test_migrate_archives_orphan_mi_checks(self):
        """An MI-sourced exception with no live check is archived."""
        mo = self.env["mrp.production"].search([], limit=1)

        # Create an orphan exception (no live check for this id).
        orphan = self.Exc.create({
            "exception_type": "cutlist_divergence",
            "source_model": "southbrook.mi.check",
            "source_res_id": 999999,  # This check does not exist
            "mo_id": mo.id,
            **self._vals(),
        })
        orphan_id = orphan.id

        # Run migration.
        migration = self._load_migration()
        migration.migrate(self.env.cr, "19.0.2.0.0")

        self.Exc.invalidate_recordset()

        # The orphan must be archived (active=False).
        orphan = self.Exc.with_context(active_test=False).browse(orphan_id)
        self.assertFalse(orphan.active,
                         "Orphan exception must be archived")
        self.assertFalse(orphan.dedupe_key,
                         "Orphan exception must have NULL dedupe_key")

    def test_migrate_survives_source_res_id_collision(self):
        """C1 shape: production's real duplicates include one whose
        source_res_id equals the LIVE check's real id (the row minted by the
        most recent sweep) — the earlier test suite missed this because it
        only ever seeded synthetic high ids (900000+i) that never collide.
        While `migrate()` runs, the legacy unique(source_model, source_res_id,
        exception_type) constraint is still enforced (Odoo does not drop it
        until ir.model.data._process_end(), which runs AFTER post-migration
        scripts) — so the winner's write of `source_res_id = check.id` must
        not collide with a duplicate already holding that exact triple. This
        asserts the migration completes and elects correctly despite the
        collision, and that the legacy constraint is gone afterwards."""
        mo = self.env["mrp.production"].search([], limit=1)

        check = self.env["southbrook.mi.check"].create({
            "name": "Missing cutlist",
            "message": "Cutlist divergence detected",
            "category": "cut",
            "severity": "blocker",
            "production_id": mo.id if mo else False,
        })

        for i in range(5):
            self.Exc.create({
                "exception_type": "cutlist_divergence",
                "source_model": "southbrook.mi.check",
                "source_res_id": 900000 + i,
                "mo_id": mo.id,
                **self._vals(),
            })

        # The collider: source_res_id already equals the live check's real
        # id — exactly the triple the elected winner's write will target.
        collider = self.Exc.create({
            "exception_type": "cutlist_divergence",
            "source_model": "southbrook.mi.check",
            "source_res_id": check.id,
            "mo_id": mo.id,
            **self._vals(),
        })

        migration = self._load_migration()
        migration.migrate(self.env.cr, "19.0.2.0.0")  # must not raise

        self.Exc.invalidate_recordset()

        survivors = self.Exc.search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
            ("active", "=", True),
        ])
        self.assertEqual(len(survivors), 1,
                          "Exactly one survivor even with a colliding "
                          "source_res_id among the duplicates")
        self.assertEqual(survivors[0].source_res_id, check.id)
        self.assertTrue(survivors[0].dedupe_key)

        all_after = self.Exc.with_context(active_test=False).search_count([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
        ])
        self.assertEqual(all_after, 6, "No rows deleted, including the collider")

        # The legacy constraint must be gone. (It is already absent by the
        # time this test runs — Odoo drops it during module upgrade, well
        # before test execution — so this cannot reproduce the UniqueViolation
        # itself; it only confirms the migration's DROP CONSTRAINT IF EXISTS
        # left the schema in the expected post-migration shape.)
        self.env.cr.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = %s",
            ("southbrook_command_exception_source_uniq",),
        )
        self.assertFalse(
            self.env.cr.fetchone(),
            "Legacy unique(source_model, source_res_id, exception_type) "
            "constraint must not exist after migration",
        )

    def test_migrate_never_elects_resolved_survivor(self):
        """I1: the oldest human-touched row in the bucket is resolved, but a
        newer row is acknowledged (genuinely open). The acknowledged row must
        win — electing a resolved/dismissed row as the survivor of a LIVE
        condition would archive the winner, and the next sweep finds it by
        key with active_test=False and correctly never reactivates it,
        permanently burying a live blocker with no UI path to recover it."""
        mo = self.env["mrp.production"].search([], limit=1)

        check = self.env["southbrook.mi.check"].create({
            "name": "Missing cutlist",
            "message": "Cutlist divergence detected",
            "category": "cut",
            "severity": "blocker",
            "production_id": mo.id if mo else False,
        })

        # Oldest duplicate: a human resolved it. Must NEVER be elected.
        resolved_row = self.Exc.create({
            "exception_type": "cutlist_divergence",
            "source_model": "southbrook.mi.check",
            "source_res_id": 900100,
            "mo_id": mo.id,
            **self._vals(),
        })
        resolved_row.action_resolve()
        self.assertEqual(resolved_row.state, "resolved")
        self.assertFalse(resolved_row.active)

        # A few untouched duplicates in between.
        for i in range(3):
            self.Exc.create({
                "exception_type": "cutlist_divergence",
                "source_model": "southbrook.mi.check",
                "source_res_id": 900101 + i,
                "mo_id": mo.id,
                **self._vals(),
            })

        # Newer duplicate: genuinely open, human-acknowledged. Must win.
        acknowledged_row = self.Exc.create({
            "exception_type": "cutlist_divergence",
            "source_model": "southbrook.mi.check",
            "source_res_id": 900199,
            "mo_id": mo.id,
            **self._vals(),
        })
        acknowledged_row.action_acknowledge()
        self.assertEqual(acknowledged_row.state, "acknowledged")
        self.assertTrue(acknowledged_row.active)

        migration = self._load_migration()
        migration.migrate(self.env.cr, "19.0.2.0.0")

        self.Exc.invalidate_recordset()

        survivors = self.Exc.search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
            ("active", "=", True),
        ])
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0].id, acknowledged_row.id,
                          "The acknowledged row must win, never the resolved "
                          "one, even though the resolved row is older")
        self.assertTrue(survivors[0].active)
        self.assertEqual(survivors[0].source_res_id, check.id)

        resolved_row.invalidate_recordset()
        self.assertFalse(resolved_row.dedupe_key,
                          "The resolved row must never be elected/keyed")
        self.assertFalse(resolved_row.active)

    def test_migrate_two_live_checks_same_bucket_get_distinct_survivors(self):
        """I2: two live southbrook.mi.check rows on the SAME manufacturing
        order and in the SAME etype bucket (etype only encodes category, not
        check name, so both checks' candidate search domain is identical)
        must each get their own, distinct survivor — a row must never be
        elected as the survivor for two different live conditions."""
        mo = self.env["mrp.production"].search([], limit=1)

        check1 = self.env["southbrook.mi.check"].create({
            "name": "Missing cutlist A",
            "message": "Cutlist divergence detected on run A",
            "category": "cut",
            "severity": "blocker",
            "production_id": mo.id if mo else False,
        })
        check2 = self.env["southbrook.mi.check"].create({
            "name": "Missing cutlist B",
            "message": "Cutlist divergence detected on run B",
            "category": "cut",
            "severity": "blocker",
            "production_id": mo.id if mo else False,
        })

        for i in range(4):
            self.Exc.create({
                "exception_type": "cutlist_divergence",
                "source_model": "southbrook.mi.check",
                "source_res_id": 900400 + i,
                "mo_id": mo.id,
                **self._vals(),
            })
        for i in range(4):
            self.Exc.create({
                "exception_type": "cutlist_divergence",
                "source_model": "southbrook.mi.check",
                "source_res_id": 900500 + i,
                "mo_id": mo.id,
                **self._vals(),
            })

        migration = self._load_migration()
        migration.migrate(self.env.cr, "19.0.2.0.0")

        self.Exc.invalidate_recordset()

        key1 = self.Exc._dedupe_key_for(
            "cutlist_divergence", "southbrook.mi.check", check1.id, check1)
        key2 = self.Exc._dedupe_key_for(
            "cutlist_divergence", "southbrook.mi.check", check2.id, check2)
        self.assertNotEqual(key1, key2)

        survivors = self.Exc.search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
            ("active", "=", True),
        ])
        self.assertEqual(len(survivors), 2,
                          "Each live check must get its own survivor, "
                          "never sharing one and never doubling up")

        keys_found = set(survivors.mapped("dedupe_key"))
        self.assertEqual(keys_found, {key1, key2},
                          "Each survivor must be keyed to a distinct check")

        source_res_ids = sorted(survivors.mapped("source_res_id"))
        self.assertEqual(source_res_ids, sorted([check1.id, check2.id]),
                          "Each survivor's source_res_id must point to its "
                          "own check, never the other's")

        all_after = self.Exc.with_context(active_test=False).search_count([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
        ])
        self.assertEqual(all_after, 8, "No rows deleted")

    def test_migrate_backfills_survivor_occurrence_count_and_last_seen(self):
        """Before archiving, the migration must count the collapsed bucket:
        the survivor's occurrence_count becomes the EXACT number of rows
        collapsing into it (survivor included), and last_seen becomes the
        newest create_date among them — so a chronic condition reads "seen
        9 times, last at <ts>" instead of looking like a fresh incident."""
        mo = self.env["mrp.production"].search([], limit=1)

        check = self.env["southbrook.mi.check"].create({
            "name": "Missing cutlist",
            "message": "Cutlist divergence detected",
            "category": "cut",
            "severity": "blocker",
            "production_id": mo.id if mo else False,
        })

        # 9 historical duplicates for the same condition (the keying-bug
        # shape). Distinct source_res_id values, same (mo_id, etype) bucket.
        exceptions = []
        for i in range(9):
            exceptions.append(self.Exc.create({
                "exception_type": "cutlist_divergence",
                "source_model": "southbrook.mi.check",
                "source_res_id": 902000 + i,
                "mo_id": mo.id,
                **self._vals(),
            }))

        # create_date is not writable through the ORM; stamp distinct values
        # via SQL, deliberately out of creation order, so the newest value is
        # unambiguous and NOT simply "the last row created".
        dates = [
            "2026-06-01 00:00:00", "2026-06-15 00:00:00", "2026-07-20 09:30:00",
            "2026-06-10 00:00:00", "2026-05-01 00:00:00", "2026-06-25 00:00:00",
            "2026-06-05 00:00:00", "2026-06-12 00:00:00", "2026-06-18 00:00:00",
        ]
        newest = max(dates)
        for exc, dt in zip(exceptions, dates):
            self.env.cr.execute(
                "UPDATE southbrook_command_exception SET create_date = %s "
                "WHERE id = %s",
                (dt, exc.id),
            )

        migration = self._load_migration()
        migration.migrate(self.env.cr, "19.0.2.0.0")

        self.Exc.invalidate_recordset()

        survivors = self.Exc.search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", "cutlist_divergence"),
            ("mo_id", "=", mo.id),
            ("active", "=", True),
        ])
        self.assertEqual(len(survivors), 1)
        survivor = survivors[0]

        self.assertEqual(
            survivor.occurrence_count, 9,
            "occurrence_count must equal the exact number of rows collapsed "
            "into the survivor (survivor included), not merely > 1")
        self.assertEqual(
            fields.Datetime.to_string(survivor.last_seen), newest,
            "last_seen must be the newest create_date among the collapsed "
            "rows, not the survivor's own create_date")

    def test_migrate_keys_non_mi_sources(self):
        """Non-MI sources are keyed on the legacy triple."""
        # Create a breakdown_alert exception (non-MI source).
        breakdown = self.Exc.create({
            "exception_type": "breakdown_alert",
            "source_model": "southbrook.cmms.breakdown_alert",
            "source_res_id": 555555,
            **self._vals(),
        })

        # Run migration.
        migration = self._load_migration()
        migration.migrate(self.env.cr, "19.0.2.0.0")

        self.Exc.invalidate_recordset()

        # The non-MI exception should have a dedupe_key on the legacy triple.
        breakdown = self.Exc.browse(breakdown.id)
        self.assertIsNotNone(breakdown.dedupe_key)
        expected_key = "southbrook.cmms.breakdown_alert:555555:breakdown_alert"
        self.assertEqual(breakdown.dedupe_key, expected_key)
