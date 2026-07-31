# SPDX-License-Identifier: LGPL-3.0-only
"""Kitchen Jobs board remediation (contract §8, BlindSpot review of
action-1298 — see scratchpad/CONTRACT_kitchen_jobs_board.md).

Covers:
  * skj_board_intel's payload shape (every key always present, zeros
    render, the SAFETY base-domain AND) and its DO NEXT rank-1 derivation
    from a real equipment_blocked -> maintenance.request chain.
  * The three card fields (skj_customer_display, skj_days_late,
    skj_identity_needs_naming — contract §2).
  * readiness_decision's group_expand=True (southbrook_project_mrp), which
    this suite exercises through web_read_group — the same entry point the
    Kitchen Jobs kanban itself uses to build its columns; formatted_
    read_group/plain _read_group do NOT honour group_expand, only
    web_read_group's read_group_expand=True context does.
  * The 19.0.5.0.0 partner_id backfill migration helper, loaded directly
    (Odoo migration scripts aren't a regular importable package) and run
    twice to prove idempotency.

Fixtures build real MO/work-order/work-center/maintenance-request chains
rather than hand-assigning the stored compute fields (readiness_decision,
equipment_blocked, ...) directly — matching
southbrook_project_mrp/tests/test_integration.py's own style, and because
those fields have no inverse: a manual write only "sticks" until something
re-triggers their compute, which is fragile compared to driving the real
condition.
"""
import importlib.util
import os
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


def _load_backfill_migration():
    path = os.path.join(
        os.path.dirname(__file__), "..", "migrations", "19.0.5.0.0",
        "post-migrate.py",
    )
    spec = importlib.util.spec_from_file_location(
        "southbrook_premium_orchestration_5_0_0_post_migrate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install", "southbrook", "premium_orchestration",
        "kitchen_jobs_board")
class TestKitchenJobsBoard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.partner = cls.env["res.partner"].create({
            "name": "Kitchen Jobs Board Customer",
        })
        cls.project = cls.env["project.project"].create({
            "name": "Kitchen Jobs Board Tests",
        })
        # A manufacturable product + BoM, mirroring southbrook_project_mrp/
        # tests/test_integration.py's `_make_mo` — the DO NEXT rank-1 and
        # group_expand tests need a real MO/WO/workcenter chain, not a
        # hand-set equipment_blocked/readiness_decision flag.
        cls.component = cls.env["product.product"].create({
            "name": "SKJ Test Component", "type": "consu", "is_storable": True,
        })
        cls.finished = cls.env["product.product"].create({
            "name": "SB-BASE-1DR-SKJ", "type": "consu", "is_storable": True,
        })
        cls.bom = cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.finished.product_tmpl_id.id,
            "product_qty": 1.0,
            "bom_line_ids": [(0, 0, {
                "product_id": cls.component.id, "product_qty": 1.0,
            })],
        })

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------
    def _make_so(self, partner=None):
        return self.env["sale.order"].create({
            "partner_id": (partner or self.partner).id,
        })

    def _make_task(self, name="SKJ Task", so=None, partner_id=False, **vals):
        """A bare kitchen-shaped task: linked to a sale.order (so the SKJ
        base domain picks it up) unless the caller explicitly omits ``so``
        to test the no-customer / non-kitchen-task edge cases."""
        task_vals = {
            "name": name,
            "project_id": self.project.id,
            "partner_id": partner_id,
        }
        if so is not None:
            task_vals["x_southbrook_sale_order_id"] = so.id
        task_vals.update(vals)
        return self.Task.create(task_vals)

    def _make_mo(self, task):
        mo = self.env["mrp.production"].create({
            "product_id": self.finished.id, "product_qty": 1.0,
            "bom_id": self.bom.id,
        })
        mo.project_task_id = task.id
        return mo

    # ------------------------------------------------------------------
    # skj_board_intel — payload shape, SAFETY domain, zeros render
    # ------------------------------------------------------------------
    def test_payload_keys_present_and_total_matches_count(self):
        so = self._make_so()
        self._make_task(so=so, name="Job A")
        self._make_task(so=so, name="Job B")

        for domain in ([], [("readiness_decision", "=", "blocked")]):
            with self.subTest(domain=domain):
                result = self.Task.skj_board_intel(domain)
                for key in ("kpis", "do_next", "pareto", "equipment",
                            "provenance", "server_now"):
                    self.assertIn(key, result, "missing top-level key %s" % key)
                for key in (
                    "total", "blocked", "review", "ready", "undecided",
                    "at_risk", "missing_install", "unassigned",
                    "unscheduled_wos", "unscheduled_native",
                    "unassigned_wos", "max_days_late",
                    "late", "deadline_coverage",
                ):
                    self.assertIn(key, result["kpis"], "missing kpis key %s" % key)
                for key in ("computed_at", "cron_cadence", "copy"):
                    self.assertIn(key, result["provenance"])
                self.assertIn("blocked_workcenters", result["equipment"])

                # SAFETY (contract §1): the caller's domain is ANDed onto
                # the base domain server-side, never trusted alone — so
                # kpis.total must equal a search_count built the same way.
                expected_total = self.Task.search_count(
                    [("x_southbrook_sale_order_id", "!=", False)] + domain)
                self.assertEqual(result["kpis"]["total"], expected_total)

    def test_zero_counts_render_when_nothing_matches(self):
        # No task in this DB matches an impossible id — the payload must
        # still carry the full kpis/pareto shape with zeros, never a
        # missing key ("absence of data never renders as good").
        result = self.Task.skj_board_intel([("id", "=", 0)])
        self.assertEqual(result["kpis"]["total"], 0)
        self.assertEqual(result["kpis"]["blocked"], 0)
        self.assertEqual(result["kpis"]["late"], 0)
        self.assertIsNone(result["kpis"]["max_days_late"])
        self.assertEqual(len(result["pareto"]), 10)
        for entry in result["pareto"]:
            self.assertEqual(entry["count"], 0)
        self.assertEqual(result["equipment"]["blocked_workcenters"], [])
        self.assertEqual(result["do_next"], [])

    def test_unscheduled_respects_both_schedules(self):
        # af0b386 two-schedule finding: a WO dated only in
        # mrp_shop_floor_control's date_planned_start_wo has no native
        # leave, so the stored unscheduled_workorder_count calls it
        # unscheduled — but the board's headline count and DO NEXT must
        # NOT, or they tell the shop to schedule work a planner already
        # dated. Only runs the SFC half when that module's field exists.
        so = self._make_so()
        task = self._make_task(so=so, name="Two Schedules")
        mo = self._make_mo(task)
        # This module's own action_confirm override gates on component
        # availability (models/mrp_production.py) — the fixture has no
        # stock, so use the override's documented manager bypass.
        mo.with_context(bypass_availability_gate=True).action_confirm()
        wos = mo.workorder_ids.filtered(
            lambda w: w.state in ("blocked", "ready") and not w.leave_id)
        if not wos:
            self.skipTest("fixture produced no plannable work order")
        wo = wos[0]

        result = self.Task.skj_board_intel([("id", "=", task.id)])
        self.assertGreaterEqual(
            result["kpis"]["unscheduled_wos"], 1,
            "a WO with no date in either schedule must count as unscheduled")
        self.assertEqual(task.skj_unscheduled_wo_count,
                         result["kpis"]["unscheduled_wos"])

        if "date_planned_start_wo" not in wo._fields:
            self.skipTest("mrp_shop_floor_control not installed here — "
                          "native half asserted above, SFC half not testable")
        before = result["kpis"]["unscheduled_wos"]
        wo.date_planned_start_wo = fields.Datetime.now()
        task.invalidate_recordset()
        result = self.Task.skj_board_intel([("id", "=", task.id)])
        self.assertEqual(
            result["kpis"]["unscheduled_wos"], before - 1,
            "an SFC-dated WO is scheduled, whatever the native column says")
        rank2 = [i for i in result["do_next"]
                 if i["action"]["res_model"] == "mrp.workorder"]
        for item in rank2:
            self.assertNotIn(
                wo.id, item["action"]["domain"][0][2],
                "DO NEXT must never list an SFC-dated WO as needing a date")

    def test_pareto_includes_all_ten_gates(self):
        so = self._make_so()
        self._make_task(so=so)
        result = self.Task.skj_board_intel([])
        gate_keys = {entry["gate"] for entry in result["pareto"]}
        self.assertEqual(gate_keys, {
            "equipment_blocked", "material_at_risk",
            "workcenter_over_capacity", "unscheduled_workorder_count",
            "crew_gap", "cad_cutlist_review_required",
            "southbrook_specs_complete", "install_date_missing",
            "pm_stage_mismatch", "job_at_risk",
        })
        self.assertEqual(len(result["pareto"]), 10)

    # ------------------------------------------------------------------
    # max_days_late / late — contract §1 (amended)
    # ------------------------------------------------------------------
    def test_max_days_late_none_without_deadlines(self):
        so = self._make_so()
        self._make_task(so=so, name="No Deadline Job")
        result = self.Task.skj_board_intel([])
        self.assertIsNone(result["kpis"]["max_days_late"])
        self.assertEqual(result["kpis"]["late"], 0)

    def test_max_days_late_and_late_count_reflect_past_deadline(self):
        so = self._make_so()
        past = fields.Datetime.now() - timedelta(days=3)
        late_task = self._make_task(
            so=so, name="Late Job", date_deadline=past)
        # A second, on-time task in the same domain — "late" must count
        # only the one task actually past its deadline.
        self._make_task(so=so, name="On Time Job")

        result = self.Task.skj_board_intel([])
        # ±1 tolerance: context_today() vs a datetime taken a moment
        # earlier can straddle a day boundary in CI.
        self.assertIn(result["kpis"]["max_days_late"], (2, 3, 4))
        self.assertIn(late_task.skj_days_late, (2, 3, 4))
        self.assertEqual(result["kpis"]["late"], 1)

    # ------------------------------------------------------------------
    # skj_customer_display / skj_days_late / skj_identity_needs_naming
    # ------------------------------------------------------------------
    def test_skj_customer_display_falls_back_to_so_partner(self):
        so = self._make_so()
        task = self._make_task(so=so, name="Customer Fallback Job")
        self.assertEqual(task.skj_customer_display, self.partner.name)

    def test_skj_customer_display_false_when_neither_side_has_one(self):
        task = self.Task.create({
            "name": "No Customer Job", "project_id": self.project.id,
        })
        self.assertFalse(task.skj_customer_display)

    def test_skj_days_late_zero_without_deadline(self):
        task = self._make_task(name="No Deadline")
        self.assertEqual(task.skj_days_late, 0)

    def test_skj_identity_needs_naming(self):
        tbd_task = self._make_task(name="Kitchen Cabinet Set - Custom (TBD) [SO001]")
        self.assertTrue(tbd_task.skj_identity_needs_naming)
        stub_task = self._make_task(name="Job: Smith remake")
        self.assertTrue(stub_task.skj_identity_needs_naming)
        named_task = self._make_task(name="Shaker Maple Kitchen - Jones")
        self.assertFalse(named_task.skj_identity_needs_naming)

    # ------------------------------------------------------------------
    # group_expand — southbrook_project_mrp readiness_decision
    # ------------------------------------------------------------------
    def test_group_expand_shows_all_readiness_columns_when_all_blocked(self):
        so = self._make_so()
        task = self._make_task(so=so, name="Blocked Job")
        wc = self.env["mrp.workcenter"].create({"name": "SKJ Panel Saw"})
        mo = self._make_mo(task)
        self.env["mrp.workorder"].create({
            "name": "Cut panels", "production_id": mo.id,
            "workcenter_id": wc.id, "duration_expected": 12.0,
        })
        task.invalidate_recordset()
        self.assertEqual(
            task.readiness_decision, "blocked",
            "precondition: an MO with an unscheduled WO must read as blocked "
            "(pinned by southbrook_project_mrp's own "
            "test_manufacturing_readiness_decision_blocks_unscheduled_job)")

        # web_read_group + read_group_expand=True context — group_expand is
        # only honoured under that context (web/models/models.py:908), and
        # it is the WEB CLIENT that sends it on every grouped kanban read
        # (web/static/src/model/relational_model/relational_model.js:855),
        # not web_read_group itself. The test mirrors exactly what the
        # Kitchen Jobs kanban sends.
        result = self.Task.with_context(read_group_expand=True).web_read_group(
            [("id", "=", task.id)],
            groupby=["readiness_decision"],
            aggregates=["__count"],
        )
        seen = {g["readiness_decision"] for g in result["groups"]}
        self.assertTrue(
            {"ready", "review", "blocked"}.issubset(seen),
            "group_expand=True must always surface all readiness columns, "
            "even when every matching task is blocked; got %s" % seen,
        )

    # ------------------------------------------------------------------
    # DO NEXT rank-1 — equipment blocked by an open maintenance request
    # ------------------------------------------------------------------
    def test_do_next_rank1_equipment_blocked_maintenance(self):
        so = self._make_so()
        task = self._make_task(so=so, name="Equipment Blocked Job")
        wc = self.env["mrp.workcenter"].create({"name": "SKJ CNC Router"})
        equipment = self.env["maintenance.equipment"].create({
            "name": "SKJ CNC Router Unit", "workcenter_id": wc.id,
        })
        mo = self._make_mo(task)
        self.env["mrp.workorder"].create({
            "name": "Route parts", "production_id": mo.id,
            "workcenter_id": wc.id, "duration_expected": 18.0,
        })
        request = self.env["maintenance.request"].create({
            "name": "Spindle fault", "equipment_id": equipment.id,
        })
        task.invalidate_recordset()
        self.assertTrue(
            task.equipment_blocked,
            "precondition: an open maintenance request on the task's "
            "workcenter must flip equipment_blocked "
            "(pinned by southbrook_project_mrp's own "
            "test_equipment_readiness_rollup)")

        result = self.Task.skj_board_intel([("id", "=", task.id)])
        self.assertTrue(result["do_next"], "DO NEXT must be non-empty")
        rank1 = result["do_next"][0]
        self.assertEqual(rank1["rank"], 1)
        self.assertIn(wc.name, rank1["label"])
        self.assertEqual(rank1["count"], 1)
        self.assertEqual(rank1["action"]["type"], "act_window")
        self.assertEqual(rank1["action"]["res_model"], "maintenance.request")
        self.assertEqual(rank1["action"]["domain"], [("id", "=", request.id)])

        # And the Pareto/kpis agree it's the same job.
        self.assertEqual(result["equipment"]["blocked_workcenters"][0]["name"], wc.name)
        equipment_gate = next(
            g for g in result["pareto"] if g["gate"] == "equipment_blocked")
        self.assertEqual(equipment_gate["count"], 1)

    # ------------------------------------------------------------------
    # 19.0.5.0.0 migration — partner_id backfill, idempotent
    # ------------------------------------------------------------------
    def test_migration_backfill_partner_id_idempotent(self):
        migration = _load_backfill_migration()
        so = self._make_so()
        orphan = self._make_task(so=so, name="Orphan Partner Job")
        self.assertFalse(orphan.partner_id, "precondition: task starts with no partner")

        first_count = migration._backfill_task_partner_id(self.env)
        orphan.invalidate_recordset()
        self.assertEqual(orphan.partner_id, self.partner)
        self.assertGreaterEqual(first_count, 1)

        second_count = migration._backfill_task_partner_id(self.env)
        self.assertEqual(
            second_count, 0,
            "a second run must touch zero rows — every orphan the first "
            "run found now has partner_id set")

    # ------------------------------------------------------------------
    # sale_order.py — spine creation fills partner_id when unset
    # ------------------------------------------------------------------
    def test_kitchen_task_vals_sets_partner_id_from_order(self):
        so = self._make_so()
        vals = so._kitchen_task_vals(self.project)
        self.assertEqual(vals.get("partner_id"), self.partner.id)
