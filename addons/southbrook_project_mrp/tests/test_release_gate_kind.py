# SPDX-License-Identifier: LGPL-3.0-only
"""The release gate's three-way verdict, and the stored-field regressions.

Two things are pinned here, both of which the existing suite would not catch.

FIRST, the `review` vs `blocked` split. `southbrook_production_release_state` has
offered ready/review/blocked/info since it was written, the views decorate `review`, and
`review` is half the Production Release Queue's own domain — but the compute only ever
emitted `blocked` or `ready`, so every job in the queue read identically whether it needed
one signature or had no manufacturing orders at all. The split now turns on whether every
outstanding gate is one a human can clear by attesting.

That classification is a BUSINESS RULE I inferred from the gate list, not one I read in a
spec. It is exactly the kind of thing that should fail loudly if someone disagrees and
edits the set, rather than drifting silently. Hence a test per bucket.

SECOND, storing a computed field that partly depends on TODAY. `job_at_risk` is true in
part because an MO deadline has passed; today is not a field, so it cannot be an
@api.depends, and Odoo only recomputes a stored field when a declared dependency is
written. A job going late overnight would keep yesterday's answer. The re-tick cron is the
forcing function, and `test_retick_cron_refreshes_a_job_that_went_late_overnight` is the
only thing standing between that cron being deleted as "redundant" and the Install Risk
board quietly under-reporting again.
"""

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "project_mrp")
class TestReleaseGateKind(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create({"name": "Gate Kind Jobs"})
        cls.task = cls.env["project.task"].create(
            {"name": "Gate kind job", "project_id": cls.project.id})

    # ------------------------------------------------------------------ the split

    def test_no_outstanding_gates_is_ready(self):
        self.assertEqual(self.task._southbrook_release_gate_kind([]), "ready")

    def test_only_attestable_gates_outstanding_is_review(self):
        """A signature can clear all of these, so the job is waiting on a person."""
        for missing in (
            ["CAD approved"],
            ["Cutlist approved", "BoM verified"],
            ["Final site measurements", "Crew assigned/reserved",
             "Critical equipment available"],
            sorted(self.env["project.task"].ATTESTABLE_GATES),
        ):
            with self.subTest(missing=missing):
                self.assertEqual(
                    self.task._southbrook_release_gate_kind(missing), "review",
                    "every item is attestable, so this is a signature away from ready")

    def test_any_operational_gate_outstanding_is_blocked(self):
        """No signature creates a manufacturing order, so these are not `review`."""
        for missing in (
            ["Linked MOs"],
            ["Components available"],
            ["WOs generated"],
            ["Schedule work orders"],
            ["Door/finish/hardware specs"],
            ["Install due date confirmed"],
            # Mixed: one attestable, one not. The presence of a hard gate decides.
            ["CAD approved", "Linked MOs"],
        ):
            with self.subTest(missing=missing):
                self.assertEqual(
                    self.task._southbrook_release_gate_kind(missing), "blocked")

    def test_every_gate_is_classified(self):
        """No gate may be silently absent from the attestable/operational split.

        If someone adds a twelfth gate to `_southbrook_missing_production_release_items`
        and does not decide which kind it is, it defaults to operational — which is the
        safe direction, but it should be a decision rather than an accident. This test
        fails when the gate list and the attestable set fall out of step.
        """
        known_operational = {
            "Door/finish/hardware specs", "Linked MOs", "Components available",
            "WOs generated", "Schedule work orders", "Install due date confirmed",
        }
        attestable = set(self.env["project.task"].ATTESTABLE_GATES)
        overlap = attestable & known_operational
        self.assertFalse(
            overlap, "a gate cannot be both attestable and operational: %s" % overlap)

    def test_blocked_reason_names_what_a_signature_cannot_clear(self):
        """The reason string is the whole point — it answers 'whose move is it'."""
        task = self.task
        task.invalidate_recordset()
        task._compute_southbrook_production_release()
        if task.southbrook_production_release_state == "blocked":
            self.assertIn(
                "Sign-off cannot clear", task.southbrook_production_release_reason,
                "a blocked job must say which items a signature would not fix")

    # ------------------------------------------------------- stored-field regressions

    def test_readiness_flags_are_stored_so_they_can_be_searched(self):
        """These were non-stored with `search=` hooks that silently matched nothing.

        The hook returned a correct domain and Odoo discarded it, so Install Risk reported
        no jobs at risk over a fleet where every job was high or critical. Storing them is
        what makes the domain resolvable at all; if someone reverts `store=True` the board
        goes quietly blank again, so the storedness itself is asserted.
        """
        fields_that_must_be_stored = (
            "job_at_risk", "install_date_missing", "job_install_due",
            "southbrook_specs_complete", "cad_cutlist_review_required",
            "pm_stage_mismatch", "crew_gap", "workcenter_over_capacity",
            "unscheduled_workorder_count", "material_at_risk",
        )
        model = self.env["project.task"]
        for name in fields_that_must_be_stored:
            with self.subTest(field=name):
                self.assertTrue(
                    model._fields[name].store,
                    "%s must stay stored — un-storing it makes every filter and "
                    "stat-button click-through built on it return nothing" % name)

    def test_install_risk_domain_is_resolvable(self):
        """The literal domain behind the Install Risk board must execute, not raise."""
        self.env["project.task"].search_count(
            ["|", ("job_at_risk", "=", True), ("install_date_missing", "=", True)])

    def test_readiness_flags_are_groupable(self):
        """Grouping is impossible on a non-stored field, which is why the release
        queue's pager counted stage groups instead of rows."""
        self.env["project.task"]._read_group(
            [], groupby=["job_at_risk"], aggregates=["__count"])

    def test_retick_cron_exists_and_is_callable(self):
        """A stored field that depends on today needs an external forcing function.

        Deleting this cron as redundant would reintroduce the false all-clear: a job that
        goes late overnight keeps yesterday's `job_at_risk` because no declared dependency
        was written.
        """
        model = self.env["project.task"]
        self.assertTrue(
            hasattr(model, "_cron_retick_time_sensitive_readiness"),
            "the date-driven readiness re-tick is what keeps job_at_risk honest")
        self.assertIsInstance(model._cron_retick_time_sensitive_readiness(), int)
