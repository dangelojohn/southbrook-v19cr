# SPDX-License-Identifier: LGPL-3.0-only
"""``days_persisting`` and the `chronic` filter's real signal.

Context (2026-07-31): the MI sweep cron re-observes still-open conditions
every 15 minutes and increments occurrence_count each time, so
occurrence_count counts SWEEPS, not distinct incidents. Measured production
2026-07-31: cutlist_divergence occurrence_count=2486 across 9 rows,
mi_blocker=2380 across 8 rows, quality_ncr=90, breakdown_alert=90 — every one
persisting ~26.5-26.7 days. That made the old occurrence_count>=5 `chronic`
filter match all 20 open rows, carrying zero information. days_persisting
(how long a condition has actually gone unresolved) replaces it.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestChronicPersistence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exc = cls.env["southbrook.command.exception"]
        cls.owner = cls.env.ref("base.user_admin")

    def _vals(self, **kw):
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

    def _set_create_date(self, record, dt_string):
        """create_date is not writable through the ORM — stamp it via SQL,
        same pattern used by test_dedupe_migration.py."""
        self.env.cr.execute(
            "UPDATE southbrook_command_exception SET create_date = %s "
            "WHERE id = %s",
            (dt_string, record.id),
        )
        record.invalidate_recordset()

    def test_days_persisting_matches_known_gap(self):
        """last_seen 10 days after create_date must compute to 10.0 days,
        not something derived from occurrence_count."""
        exc = self.Exc.create(self._vals(
            exception_type="cutlist_divergence",
            source_model="southbrook.mi.check",
            source_res_id=910001,
        ))
        self._set_create_date(exc, "2026-07-01 00:00:00")
        exc.write({"last_seen": "2026-07-11 00:00:00"})
        exc.invalidate_recordset()
        self.assertAlmostEqual(exc.days_persisting, 10.0, delta=0.01)

    def test_days_persisting_zero_when_last_seen_unset(self):
        """No last_seen (never observed by a sweep yet) must read 0.0, not
        raise and not go negative."""
        exc = self.Exc.create(self._vals(
            exception_type="cutlist_divergence",
            source_model="southbrook.mi.check",
            source_res_id=910002,
        ))
        self.assertFalse(exc.last_seen)
        self.assertEqual(exc.days_persisting, 0.0)

    def test_search_days_persisting_returns_only_the_old_row(self):
        """The unstored field must still be filterable: an old row and a
        fresh row exist, filtering by days_persisting >= threshold must
        surface only the old one."""
        old = self.Exc.create(self._vals(
            exception_type="cutlist_divergence",
            source_model="southbrook.mi.check",
            source_res_id=910003,
        ))
        fresh = self.Exc.create(self._vals(
            exception_type="cutlist_divergence",
            source_model="southbrook.mi.check",
            source_res_id=910004,
        ))
        self._set_create_date(
            old, fields.Datetime.to_string(fields.Datetime.now() - timedelta(days=15)))
        now = fields.Datetime.now()
        (old | fresh).write({"last_seen": now})
        (old | fresh).invalidate_recordset()

        found = self.Exc.search([
            ("id", "in", (old | fresh).ids),
            ("days_persisting", ">=", 7),
        ])
        self.assertEqual(found, old,
                          "only the row open 15 days should match >=7 days")

    def test_chronic_filter_ignores_sweep_count_regression(self):
        """REGRESSION PROOF: a row with occurrence_count=2000 but created
        only 1 day ago must NOT be matched by the chronic domain — proves
        the filter now keys on age, not on how many times the cron swept
        it. This is exactly the production shape the fix targets: high
        occurrence_count from 15-minute sweeps says nothing about age."""
        exc = self.Exc.create(self._vals(
            exception_type="cutlist_divergence",
            source_model="southbrook.mi.check",
            source_res_id=910005,
            occurrence_count=2000,
        ))
        self._set_create_date(
            exc, fields.Datetime.to_string(fields.Datetime.now() - timedelta(days=1)))
        exc.write({"last_seen": fields.Datetime.now()})
        exc.invalidate_recordset()

        self.assertEqual(exc.occurrence_count, 2000)

        # Same domain as the `chronic` search filter in
        # command_center_views.xml: [('days_persisting', '>=', 7)].
        found = self.Exc.search([
            ("id", "=", exc.id),
            ("days_persisting", ">=", 7),
        ])
        self.assertFalse(
            found,
            "a 1-day-old row must not read as chronic no matter how many "
            "times the sweep re-observed it")
