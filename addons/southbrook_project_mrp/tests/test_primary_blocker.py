# SPDX-License-Identifier: LGPL-3.0-only
"""`southbrook_release_primary_blocker`: WHAT is blocking, not just THAT one is.

`southbrook_production_release_reason` is a non-stored Char holding prose ("Missing
Final site measurements, CAD approved, ... Sign-off cannot clear: Install due date
confirmed."). Measured on production: all 12 kitchen jobs sit in
`southbrook_production_release_state='blocked'`, and a Floor Manager could not group,
pivot, or count by which gate was actually the problem — only open each job and read a
sentence. This mirrors test_release_gate_kind.py's fixture and testing style: the
ordering rule is tested directly against `_southbrook_primary_blocker_key` with literal
`missing` lists (the same way that file drives `_southbrook_release_gate_kind`), and the
stored/groupable regression uses the same shape as that file's
`test_readiness_flags_are_groupable`.

THE ORDERING RULE under test: the primary blocker is the first item in `missing` that is
NOT in ATTESTABLE_GATES — the first gate a sign-off cannot clear — because that is the
one requiring shop-floor or planning action, not a signature. A job can carry several
outstanding attestable gates (CAD approved, BoM verified, ...) alongside one operational
gate (no install date, no linked MOs, ...); naming the attestable one first would send
the Floor Manager chasing a signature while the real bottleneck sits untouched. Only
when EVERY missing item is attestable — the job is purely a stretch of signatures away
from ready, i.e. `review` per `_southbrook_release_gate_kind` — does this fall back to
the first missing item.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "project_mrp")
class TestPrimaryBlocker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create(
            {"name": "Primary Blocker Jobs"})
        cls.task = cls.env["project.task"].create(
            {"name": "Primary blocker job", "project_id": cls.project.id})

    # ------------------------------------------------------- the ordering rule

    def test_missing_only_attestable_gates_reports_the_first_attestable_one(self):
        """Every outstanding gate is a signature away, so gate-check order decides."""
        missing = ["CAD approved", "Cutlist approved", "BoM verified"]
        self.assertEqual(
            self.task._southbrook_primary_blocker_key(missing), "cad_approved",
            "with nothing but attestable gates outstanding, the first missing item "
            "in gate-check order is the primary blocker")

    def test_a_non_attestable_gate_wins_even_behind_attestable_ones(self):
        """The true bottleneck must win over an earlier-listed attestable gate.

        CAD approved is checked before the install date in
        `_southbrook_missing_production_release_items`, and it is missing here too —
        but a signature clears it. Nothing clears a missing install date except someone
        confirming one, so it must be named as the primary blocker, not CAD.
        """
        missing = ["CAD approved", "Install due date confirmed", "BoM verified"]
        self.assertEqual(
            self.task._southbrook_primary_blocker_key(missing), "install_due_date",
            "the first NON-attestable gate must win over earlier attestable ones")

    def test_nothing_missing_is_none(self):
        self.assertEqual(self.task._southbrook_primary_blocker_key([]), "none")

    def test_an_unmapped_missing_item_falls_back_to_other_without_crashing(self):
        """A gate string absent from `_PRIMARY_BLOCKER_KEY_BY_MISSING_ITEM` must not
        raise. If a future gate is added to `_southbrook_missing_production_release_items`
        without a matching map entry, the compute must still run for all 12 jobs — it
        degrades to 'other' rather than breaking the whole board."""
        self.assertEqual(
            self.task._southbrook_primary_blocker_key(
                ["Some future gate nobody mapped yet"]),
            "other")

    # --------------------------------------------------- the compute + storage

    def test_a_freshly_created_job_stores_a_primary_blocker_consistent_with_missing(self):
        """End-to-end: the stored value must agree with the pure function fed this
        SAME job's current missing-items list, not a stale snapshot."""
        task = self.task
        task.invalidate_recordset()
        missing = task._southbrook_missing_production_release_items()
        expected = task._southbrook_primary_blocker_key(missing)
        self.assertEqual(task.southbrook_release_primary_blocker, expected)

    def test_primary_blocker_is_stored_and_groupable(self):
        """Un-storing this silently breaks the Floor Analytics pivot/group-by again —
        the exact failure mode test_release_gate_kind.py already pins for the sibling
        analytics fields on this model (test_readiness_flags_are_stored_so_they_can_be_
        searched / test_readiness_flags_are_groupable)."""
        self.assertTrue(
            self.env["project.task"]._fields[
                "southbrook_release_primary_blocker"].store,
            "must stay stored so the Floor Analytics pivot/group-by can resolve it")
        result = self.env["project.task"]._read_group(
            [("id", "=", self.task.id)],
            groupby=["southbrook_release_primary_blocker"],
            aggregates=["__count"])
        self.assertTrue(result, "read_group must return at least one bucket")
        _value, count = result[0]
        self.assertEqual(count, 1)
