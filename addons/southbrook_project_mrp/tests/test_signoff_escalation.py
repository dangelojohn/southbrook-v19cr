# SPDX-License-Identifier: LGPL-3.0-only
"""Sign-off latency escalation: the timestamp, and the cron that acts on it.

`southbrook_release_review_since` times how long a job has sat in `review` —
waiting ONLY on a human signature, per `_southbrook_release_gate_kind`. That
classification (which gates are attestable, which are operational, and which
bucket a mix of both falls into) is the entire subject of
test_release_gate_kind.py and is NOT re-litigated here.

What IS new here: the stamp/clear/preserve lifecycle of the timestamp itself, and
`_cron_escalate_signoff_latency` — the forcing function that turns a stale
timestamp into a visible mail.activity. A timestamp nobody acts on is just a
number; without the cron, a job could sit in review for a week and nothing would
ever tell the person holding the pen.

Rather than wiring up a fully-gated kitchen job (MOs, work orders, crew,
equipment, cutlist...) to arrive at `review` "for real", these tests patch
`_southbrook_release_gate_kind` at the CLASS level to force a chosen state and
then call the compute directly — the same direct-compute-call pattern
test_release_gate_kind.py already uses. Class-level, not instance-level: the
compute's own `for task in self:` loop yields FRESH browse objects on every pass
(BaseModel.__iter__ constructs a new instance per id even for a singleton
recordset), so patching an attribute on the one Python object we happen to hold
would not be seen by the object the loop actually iterates over.
"""
from datetime import timedelta
from unittest import mock

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "project_mrp")
class TestSignoffEscalation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create(
            {"name": "Signoff Escalation Jobs"})
        cls.pm = cls.env.ref("base.user_admin")

    def _new_task(self):
        return self.env["project.task"].create({
            "name": "Signoff job",
            "project_id": self.project.id,
            "user_ids": [(6, 0, [self.pm.id])],
        })

    def _force_state(self, task, state):
        """Drive the compute to `state` without wiring up the full readiness
        plumbing that produces it for real (see module docstring)."""
        with mock.patch.object(
                type(task), "_southbrook_release_gate_kind", return_value=state):
            task._compute_southbrook_production_release()
        # Flush rather than rely on the in-memory cache alone: the next thing a
        # test does is often a direct write() (aging the timestamp) or a fresh
        # search() from the cron, both of which must see what the compute just
        # decided, not whatever was last committed to the column.
        task.flush_recordset()

    def _age_review_since(self, task, hours):
        """Simulate a job that has sat in review for `hours` hours by writing the
        stored timestamp directly — exactly what the escalation cron reads."""
        task.write({
            "southbrook_release_review_since":
                fields.Datetime.now() - timedelta(hours=hours),
        })

    def _activities_on(self, task):
        return self.env["mail.activity"].search([
            ("res_model", "=", "project.task"), ("res_id", "=", task.id)])

    # ------------------------------------------------------- the timestamp itself

    def test_entering_review_stamps_review_since(self):
        task = self._new_task()
        self.assertFalse(task.southbrook_release_review_since)
        self._force_state(task, "review")
        self.assertEqual(task.southbrook_production_release_state, "review")
        self.assertTrue(task.southbrook_release_review_since)

    def test_leaving_review_clears_review_since(self):
        task = self._new_task()
        self._force_state(task, "review")
        self.assertTrue(task.southbrook_release_review_since)
        self._force_state(task, "ready")
        self.assertEqual(task.southbrook_production_release_state, "ready")
        self.assertFalse(task.southbrook_release_review_since)

    def test_staying_in_review_preserves_the_original_stamp(self):
        """The hourly retick cron re-runs this compute on every open kitchen job
        whether or not anything actually changed. If that re-stamped the clock,
        no job could ever be measurably "long" in review — every hourly pass
        would reset it back to "just entered"."""
        task = self._new_task()
        self._force_state(task, "review")
        first_stamp = task.southbrook_release_review_since
        # Simulate the retick cron running again with nothing having changed.
        self._force_state(task, "review")
        self.assertEqual(task.southbrook_release_review_since, first_stamp)

    # ------------------------------------------------------------------ the cron

    def test_stale_review_job_is_escalated_exactly_once(self):
        task = self._new_task()
        self._force_state(task, "review")
        self._age_review_since(task, 25)  # past the 24h default threshold
        self.assertFalse(self._activities_on(task))

        self.env["project.task"]._cron_escalate_signoff_latency()
        activities = self._activities_on(task)
        self.assertEqual(
            len(activities), 1,
            "a job 25h in review, past the 24h default threshold, must be "
            "escalated")
        self.assertIn("Sign-off overdue", activities.summary or "")
        self.assertEqual(activities.user_id, self.pm)

        # A second run with nothing actioned in between must not pile on a
        # second activity — the first one is still OPEN.
        self.env["project.task"]._cron_escalate_signoff_latency()
        self.assertEqual(
            len(self._activities_on(task)), 1,
            "an already-open sign-off-overdue activity must suppress a "
            "duplicate on the next hourly run")

    def test_fresh_review_job_is_not_escalated(self):
        task = self._new_task()
        self._force_state(task, "review")
        self._age_review_since(task, 1)

        self.env["project.task"]._cron_escalate_signoff_latency()
        self.assertFalse(
            self._activities_on(task),
            "1h in review is well under the 24h default escalation threshold")

    def test_threshold_honours_the_config_parameter(self):
        task = self._new_task()
        self._force_state(task, "review")
        self._age_review_since(task, 2)
        # Left at the 24h default, 2h in review would NOT escalate (mirrors
        # test_fresh_review_job_is_not_escalated) — tightening the threshold via
        # config is what should flip that.
        self.env["ir.config_parameter"].sudo().set_param(
            "southbrook_project_mrp.review_signoff_escalation_hours", "1.0")

        self.env["project.task"]._cron_escalate_signoff_latency()
        self.assertTrue(
            self._activities_on(task),
            "a 1h configured threshold must escalate a job that is 2h into "
            "review")
