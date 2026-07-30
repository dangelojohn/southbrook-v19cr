# SPDX-License-Identifier: LGPL-3.0-only
"""Phase-2 unit tests — the merged materializer primitives + kill-switch that
the event-driven hooks depend on. The full hook create/write overrides against
real source records (mi.check / breakdown_alert / sale.order) are exercised in
the copy-DB scenario run (PHASE2_INTEGRATION_TEST_SCOPE.md Part B), since they
need real production fixtures; these tests pin the shared logic those hooks
call into.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestPhase2Materializer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exc = cls.env["southbrook.command.exception"]
        cls.ICP = cls.env["ir.config_parameter"]
        cls.owner = cls.env.ref("base.user_admin")

    def _vals(self, **kw):
        v = {
            "severity": "high", "severity_rank": 1,
            "owner_id": self.owner.id,
            "impact_summary": "orig", "recommended_action": "do x",
            "why_text": "because",
        }
        v.update(kw)
        return v

    # -- kill-switch ---------------------------------------------------
    def test_hooks_enabled_default_true(self):
        self.ICP.set_param("command_center.hooks_enabled", "1")
        self.assertTrue(self.Exc._cc_hooks_enabled())

    def test_hooks_disabled_when_not_one(self):
        self.ICP.set_param("command_center.hooks_enabled", "0")
        self.assertFalse(self.Exc._cc_hooks_enabled())
        self.ICP.set_param("command_center.hooks_enabled", "1")  # restore

    # -- upsert: create then idempotent update -------------------------
    def test_upsert_creates_then_updates_not_duplicates(self):
        a = self.Exc._upsert_exception("mi_blocker", "southbrook.mi.check", 5001,
                                       self._vals())
        b = self.Exc._upsert_exception("mi_blocker", "southbrook.mi.check", 5001,
                                       self._vals(impact_summary="updated"))
        self.assertEqual(a.id, b.id, "same key must upsert, not duplicate")
        self.assertEqual(a.impact_summary, "updated")

    def test_upsert_increments_occurrence_and_advances_last_seen(self):
        exc = self.Exc._upsert_exception("mi_blocker", "southbrook.mi.check", 5010,
                                         self._vals())
        self.assertEqual(exc.occurrence_count, 1, "a fresh row starts at 1")
        self.assertTrue(exc.last_seen, "a fresh row stamps last_seen")
        first_seen = exc.last_seen

        # Re-fire the same condition a few times (the cron re-evaluates every
        # 15 minutes; this simulates 3 sweeps finding the same open row).
        for _ in range(3):
            self.Exc._upsert_exception(
                "mi_blocker", "southbrook.mi.check", 5010,
                self._vals(impact_summary="refire"))
        exc.invalidate_recordset()

        self.assertEqual(exc.occurrence_count, 4,
                         "1 initial + 3 re-fires must equal exactly 4, "
                         "never merely > 1")
        self.assertGreaterEqual(exc.last_seen, first_seen,
                                "last_seen must advance, never go backwards")

        # No second row was minted for the same dedupe_key.
        matches = self.Exc.with_context(active_test=False).search([
            ("source_model", "=", "southbrook.mi.check"),
            ("source_res_id", "=", 5010),
        ])
        self.assertEqual(len(matches), 1,
                         "repeated upserts of the same condition must never "
                         "create a second row")

    def test_upsert_preserves_owner_and_state_on_open_row(self):
        exc = self.Exc._upsert_exception("mi_blocker", "southbrook.mi.check", 5002,
                                         self._vals())
        exc.action_acknowledge()  # human moves it forward + it's still open
        other = self.env.ref("base.user_root")
        # a re-fire tries to set a different owner + state via vals...
        self.Exc._upsert_exception(
            "mi_blocker", "southbrook.mi.check", 5002,
            self._vals(owner_id=other.id, impact_summary="refire"))
        exc.invalidate_recordset()
        self.assertEqual(exc.owner_id, self.owner, "owner must be preserved")
        self.assertEqual(exc.state, "acknowledged", "human state must be preserved")
        self.assertEqual(exc.impact_summary, "refire", "narrative is refreshed")

    # -- reactivate semantics (hooks vs cron) --------------------------
    def test_reactivate_true_reopens_resolved(self):
        exc = self.Exc._upsert_exception("bom_skip", "sale.order", 5003, self._vals())
        exc.action_resolve()
        self.assertFalse(exc.active)
        self.Exc._upsert_exception("bom_skip", "sale.order", 5003,
                                   self._vals(), reactivate=True)
        exc.invalidate_recordset()
        self.assertTrue(exc.active)
        self.assertEqual(exc.state, "new")

    def test_reactivate_false_keeps_resolved(self):
        exc = self.Exc._upsert_exception("mi_blocker", "southbrook.mi.check", 5004,
                                         self._vals())
        exc.action_resolve()
        # cron path (default reactivate=False) must NOT undo the resolution
        self.Exc._upsert_exception("mi_blocker", "southbrook.mi.check", 5004,
                                   self._vals(impact_summary="still there"))
        exc.invalidate_recordset()
        self.assertFalse(exc.active, "cron re-scan must not reopen a resolved row")
        self.assertEqual(exc.state, "resolved")

    # -- resolve helper ------------------------------------------------
    def test_resolve_exception(self):
        exc = self.Exc._upsert_exception("approval_gate_stall", "sale.order", 5005,
                                         self._vals())
        found = self.Exc._resolve_exception("approval_gate_stall", "sale.order", 5005)
        self.assertEqual(found, exc)
        exc.invalidate_recordset()
        self.assertEqual(exc.state, "resolved")
        self.assertFalse(exc.active)
        # no-op when none open
        self.assertFalse(
            self.Exc._resolve_exception("approval_gate_stall", "sale.order", 999999))

    # -- publish never raises (fail-soft) ------------------------------
    def test_publish_never_raises(self):
        exc = self.Exc._upsert_exception("mi_blocker", "southbrook.mi.check", 5006,
                                         self._vals())
        # Should not raise even though no bus consumer / Phase-3 not wired.
        exc._publish("sb_cc_exceptions", "exception_upsert",
                     {"exception_id": exc.id, "company_id": self.env.company.id})
