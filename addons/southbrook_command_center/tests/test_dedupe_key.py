# SPDX-License-Identifier: LGPL-3.0-only
"""The keying contract: one exception per live business condition, no matter
how many times the source row is deleted and recreated underneath it."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestDedupeKey(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exc = cls.env["southbrook.command.exception"]
        cls.owner = cls.env.ref("base.user_admin")

    def _vals(self, **kw):
        v = {
            "severity": "critical", "severity_rank": 0,
            "owner_id": self.owner.id,
            "impact_summary": "Missing cutlist",
            "recommended_action": "Link the cutlist",
            "why_text": "no cutlist on the MO",
        }
        v.update(kw)
        return v

    def test_recreated_source_rows_do_not_duplicate(self):
        """The sweep deletes and recreates mi.check rows; ids churn. The same
        business condition must resolve to ONE exception row."""
        key = "mi:42:cut:missing cutlist:cutlist_divergence"
        first = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900001,
            self._vals(), dedupe_key=key)
        second = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900002,
            self._vals(impact_summary="Missing cutlist"), dedupe_key=key)
        self.assertEqual(first.id, second.id,
                         "a recreated source row must upsert, not duplicate")

    def test_source_res_id_tracks_the_live_row(self):
        """Click-through must open a row that still exists."""
        key = "mi:43:cut:missing cutlist:cutlist_divergence"
        exc = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900010,
            self._vals(), dedupe_key=key)
        self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900011,
            self._vals(), dedupe_key=key)
        exc.invalidate_recordset()
        self.assertEqual(exc.source_res_id, 900011,
                         "source_res_id must point at the live source row")

    def test_distinct_conditions_stay_distinct(self):
        a = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900020,
            self._vals(), dedupe_key="mi:50:cut:missing cutlist:cutlist_divergence")
        b = self.Exc._upsert_exception(
            "cutlist_divergence", "southbrook.mi.check", 900021,
            self._vals(), dedupe_key="mi:51:cut:missing cutlist:cutlist_divergence")
        self.assertNotEqual(a.id, b.id, "different MOs are different conditions")

    def test_legacy_callers_without_key_still_work(self):
        a = self.Exc._upsert_exception(
            "breakdown_alert", "southbrook.cmms.breakdown_alert", 900030,
            self._vals())
        b = self.Exc._upsert_exception(
            "breakdown_alert", "southbrook.cmms.breakdown_alert", 900030,
            self._vals(impact_summary="updated"))
        self.assertEqual(a.id, b.id)
        self.assertEqual(a.impact_summary, "updated")

    def test_dedupe_key_for_mi_check_is_stable_across_ids(self):
        Check = self.env["southbrook.mi.check"]
        rec = Check.new({"name": "Missing cutlist", "category": "cut"})
        k1 = self.Exc._dedupe_key_for(
            "cutlist_divergence", "southbrook.mi.check", 111, rec)
        k2 = self.Exc._dedupe_key_for(
            "cutlist_divergence", "southbrook.mi.check", 222, rec)
        self.assertEqual(k1, k2, "the key must not contain the row id")

    def test_scan_is_idempotent_across_recreated_checks(self):
        """Two sweeps over the same condition, with the mi.check row deleted
        and recreated between them, must leave ONE exception."""
        Check = self.env["southbrook.mi.check"]
        Exc = self.Exc.with_context(active_test=False)

        # Find or use a production order ID from existing data,
        # or use a non-existent ID for the source tracking
        mo = self.env["mrp.production"].search([], limit=1)
        mo_id = mo.id if mo else False

        def _count():
            return Exc.search_count([
                ("source_model", "=", "southbrook.mi.check"),
                ("exception_type", "=", "cutlist_divergence"),
                ("active", "=", True),
            ])

        # Create first check with id 9001
        first = Check.create({
            "name": "Missing cutlist (scan test)", "category": "cut",
            "severity": "blocker", "production_id": mo_id if mo_id else False,
            "message": "Cutlist is missing",
        })
        # Scan finds it and creates an exception
        self.Exc._scan_and_materialize()
        after_first = _count()
        self.assertGreater(after_first, 0, "first scan should create an exception")

        # Delete the check row and recreate with a different ID
        first.unlink()
        Check.create({
            "name": "Missing cutlist (scan test)", "category": "cut",
            "severity": "blocker", "production_id": mo_id if mo_id else False,
            "message": "Cutlist is missing",
        })
        # Scan again - should find the same condition via dedupe_key, not create a new exception
        self.Exc._scan_and_materialize()
        after_second = _count()

        self.assertEqual(after_second, after_first,
                         "a recreated check must not add a second exception")
