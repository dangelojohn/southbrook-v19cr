# SPDX-License-Identifier: LGPL-3.0-only
"""Migration 19.0.3.0.0: collapse the duplicate-exception backlog.

Seeds a synthetic version of the production shape: duplicates from the keying
bug, with the human-touched-one election rule verified.
"""
import importlib.util
import logging
import os

from odoo.tests import TransactionCase, tagged

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestDedupeMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exc = cls.env["southbrook.command.exception"]
        cls.owner = cls.env.ref("base.user_admin")

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
        self.assertIsNotNone(survivor.dedupe_key,
                             "Survivor must have a dedupe_key")
        self.assertNotEqual(survivor.dedupe_key, "",
                            "Survivor dedupe_key must not be empty")

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
