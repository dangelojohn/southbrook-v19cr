# SPDX-License-Identifier: LGPL-3.0-only
from odoo.exceptions import ValidationError
import json

from odoo.tests import HttpCase, TransactionCase, tagged
from psycopg2 import IntegrityError
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestCommandException(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Exc = cls.env["southbrook.command.exception"]
        cls.Center = cls.env["southbrook.command.center"]
        cls.owner = cls.env.ref("base.user_admin")

    def _make(self, **kw):
        vals = {
            "exception_type": "mi_blocker",
            "source_model": "southbrook.mi.check",
            "source_res_id": 1,
            "severity": "critical",
            "severity_rank": 0,
            "owner_id": self.owner.id,
            "impact_summary": "Test blocker",
        }
        vals.update(kw)
        return self.Exc.create(vals)

    def test_create_and_name(self):
        rec = self._make()
        self.assertTrue(rec.name, "name must be auto-populated")
        self.assertEqual(rec.state, "new")

    def test_owner_required(self):
        with self.assertRaises(Exception):
            self.Exc.create({
                "exception_type": "mi_blocker",
                "source_model": "southbrook.mi.check",
                "source_res_id": 99,
                # no owner_id -> required violation
            })

    @mute_logger("odoo.sql_db")
    def test_source_uniqueness(self):
        # The uniqueness constraint is now on dedupe_key, not the 3-tuple.
        # Direct create() calls without a dedupe_key result in NULL, which is
        # not unique-checked in PostgreSQL. The real enforcement happens in
        # _upsert_exception, which is tested in test_phase2.py.
        # This test verifies that two records with the same dedupe_key violate
        # the constraint.
        key = "test:42:foo"
        self.Exc.create({
            "exception_type": "mi_blocker",
            "source_model": "southbrook.mi.check",
            "source_res_id": 42,
            "severity": "critical",
            "severity_rank": 0,
            "owner_id": self.owner.id,
            "impact_summary": "Test blocker",
            "dedupe_key": key,
        })
        with self.assertRaises(IntegrityError):
            self.Exc.create({
                "exception_type": "mi_blocker",
                "source_model": "southbrook.mi.check",
                "source_res_id": 43,
                "severity": "critical",
                "severity_rank": 0,
                "owner_id": self.owner.id,
                "impact_summary": "Test blocker",
                "dedupe_key": key,
            })

    def test_ack_resolve_transitions(self):
        rec = self._make(source_res_id=7)
        rec.action_acknowledge()
        self.assertEqual(rec.state, "acknowledged")
        self.assertTrue(rec.acknowledged_date)
        rec.action_resolve()
        self.assertEqual(rec.state, "resolved")
        self.assertTrue(rec.resolved_date)
        self.assertFalse(rec.active)

    def test_dismiss(self):
        rec = self._make(source_res_id=8)
        rec.action_dismiss()
        self.assertEqual(rec.state, "dismissed")
        self.assertFalse(rec.active)
        self.assertTrue(rec.dismissed_date, "dismiss stamps dismissed_date")
        self.assertFalse(rec.resolved_date,
                         "New->Dismissed must NOT set resolved_date")

    def test_reopen_undo(self):
        # action_reopen powers the dashboard undo-toast: a resolved/dismissed
        # exception returns to the open queue with timestamps cleared.
        rec = self._make(source_res_id=9)
        rec.action_resolve()
        self.assertEqual(rec.state, "resolved")
        self.assertFalse(rec.active)
        rec.action_reopen()
        self.assertEqual(rec.state, "new")
        self.assertTrue(rec.active)
        self.assertFalse(rec.resolved_date)
        # a dismissed one reopens too
        rec2 = self._make(source_res_id=10)
        rec2.action_dismiss()
        rec2.action_reopen()
        self.assertEqual(rec2.state, "new")
        self.assertTrue(rec2.active)

    # -- scoring: must return the contract dict and never raise ---------
    def test_factory_health_shape(self):
        out = self.Center.factory_health_score()
        for key in ("score", "band", "factors", "explanation"):
            self.assertIn(key, out)

    def test_schedule_confidence_shape(self):
        task = self.env["project.task"].search([], limit=1)
        out = self.Center.schedule_confidence(task)
        self.assertIn("band", out)
        self.assertIn("score", out)

    def test_job_margin_shape(self):
        task = self.env["project.task"].search([], limit=1)
        out = self.Center.job_margin(task)
        self.assertIn("band", out)

    # -- provenance guard: a missing cost must never read as profit -----
    #
    # Measured on production 2026-07-27: 319 of 321 active variants had a
    # NULL standard_price, so job_estimated_cost summed to 0.0 and
    # (revenue - 0)/revenue banded every job green. These tests pin the
    # behaviour that stops that.

    class _FakeOrder:
        name = "SO-TEST"
        amount_untaxed = 10000.0

    class _FakeTask:
        """Duck-typed stand-in — job_margin reads via getattr throughout."""
        def __init__(self, order, industrial=0.0, estimated=0.0,
                     coverage=None, missing=0):
            self.source_order_id = order
            self.job_industrial_cost = industrial
            self.job_estimated_cost = estimated
            if coverage is not None:
                self.job_cost_coverage = coverage
            self.job_cost_missing_count = missing

    def test_margin_unknown_when_no_cost_loaded(self):
        task = self._FakeTask(self._FakeOrder(), industrial=0.0,
                              estimated=0.0, coverage=0.0, missing=6)
        out = self.Center.job_margin(task)
        self.assertEqual(
            out["band"], "unknown",
            "A job with no cost basis must not be scored — it is unknown, "
            "not 100% margin.")
        self.assertIsNone(out["score"])
        self.assertIn("cost_not_loaded", out["explanation"])
        self.assertNotEqual(
            out["band"], "green",
            "This is the exact failure being guarded: zero cost banding green.")

    def test_margin_scores_normally_with_full_coverage(self):
        task = self._FakeTask(self._FakeOrder(), industrial=6000.0,
                              coverage=1.0, missing=0)
        out = self.Center.job_margin(task)
        self.assertEqual(out["band"], "green",
                         "40% margin on fully-costed work is genuinely green.")
        self.assertAlmostEqual(out["score"], 40.0, places=1)

    def test_partial_coverage_never_bands_green(self):
        """Half the costs missing inflates margin — hold at amber."""
        task = self._FakeTask(self._FakeOrder(), industrial=1000.0,
                              coverage=0.5, missing=3)
        out = self.Center.job_margin(task)
        self.assertEqual(
            out["band"], "amber",
            "90% margin computed from half the costs must not read green.")
        self.assertIn("WARNING", out["explanation"])
        self.assertIn("understated", out["explanation"])

    def test_partial_coverage_still_reports_red_when_bad(self):
        """The cap only prevents false GREEN; a genuine red stays red."""
        task = self._FakeTask(self._FakeOrder(), industrial=9500.0,
                              coverage=0.5, missing=3)
        out = self.Center.job_margin(task)
        self.assertEqual(out["band"], "red",
                         "5% margin is red regardless of coverage.")

    def test_coverage_surfaced_as_a_factor(self):
        task = self._FakeTask(self._FakeOrder(), industrial=6000.0,
                              coverage=0.75, missing=1)
        out = self.Center.job_margin(task)
        labels = [f["label"] for f in out["factors"]]
        self.assertIn("Cost coverage", labels,
                      "Coverage must be visible on the card, not just in "
                      "the explanation text.")

    def test_po_delivery_risk_shape(self):
        po = self.env["purchase.order"].search([], limit=1)
        out = self.Center.po_delivery_risk(po)
        self.assertIn("band", out)

    # -- materializer: idempotent, writes only its own model ------------
    def test_materialize_idempotent(self):
        # Two scans must not create duplicate exceptions for the same source.
        self.Exc._scan_and_materialize()
        before = self.Exc.with_context(active_test=False).search_count([])
        self.Exc._scan_and_materialize()
        after = self.Exc.with_context(active_test=False).search_count([])
        self.assertEqual(before, after, "re-scan must upsert, not duplicate")


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestCommandCenterAuth(HttpCase):
    """The bootstrap route serves factory-health/flow and (via
    get_or_create_today) writes the exec snapshot — it must reject callers who
    are not internal Central Command members (regression for the missing route
    group gate)."""

    def _bootstrap(self):
        return self.url_open(
            "/command_center/bootstrap",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {}}),
            headers={"Content-Type": "application/json"},
        )

    def test_non_member_is_forbidden(self):
        self.env["res.users"].create({
            "name": "CC Plain User",
            "login": "cc_plain_user",
            "password": "cc_plain_user",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.authenticate("cc_plain_user", "cc_plain_user")
        resp = self._bootstrap()
        body = json.loads(resp.text)
        # jsonrpc surfaces the AccessError as an error envelope (no result).
        self.assertIn("error", body)
        self.assertNotIn("result", body)

    def test_cc_member_is_allowed(self):
        self.env["res.users"].create({
            "name": "CC Member",
            "login": "cc_member",
            "password": "cc_member",
            "group_ids": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref(
                    "southbrook_command_center.group_command_center_user").id,
            ])],
        })
        self.authenticate("cc_member", "cc_member")
        resp = self._bootstrap()
        body = json.loads(resp.text)
        self.assertIn("result", body)
        self.assertIn("factory_health", body["result"])
