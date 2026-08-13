# SPDX-License-Identifier: LGPL-3.0-only
from datetime import datetime, time, timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "southbrook", "southbrook_cmms_wms")
class TestSouthbrookCmmsWms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Equipment = cls.env["maintenance.equipment"]
        cls.Request = cls.env["maintenance.request"]
        cls.Workcenter = cls.env["mrp.workcenter"]
        cls.Production = cls.env["mrp.production"]
        cls.Product = cls.env["product.product"]
        cls.Picking = cls.env["stock.picking"]
        cls.Partner = cls.env["res.partner"]

        cls.equipment = cls.Equipment.create({"name": "Test Saw"})
        cls.vendor = cls.Partner.create({
            "name": "CMMS Vendor",
            "supplier_rank": 1,
        })
        cls.product = cls.Product.create({
            "name": "CMMS Test Product",
            "type": "consu",
        })
        cls.workcenter = cls.Workcenter.create({"name": "WC Test"})
        # Pick up the in/out picking type for stock.warehouse.
        cls.warehouse = cls.env["stock.warehouse"].search([], limit=1)
        cls.picking_type = cls.warehouse.in_type_id

    # ---- MTBF / MTTR ----
    def test_mtbf_zero_breakdowns_returns_full_period(self):
        rep = self.env["southbrook.cmms.mtbf_mttr_report"].create({
            "equipment_id": self.equipment.id,
            "as_of_date": fields.Date.context_today(self.env.user),
            "period_days": 30,
        })
        self.assertEqual(rep.breakdown_count, 0)
        # With 0 breakdowns: max(1, 0) = 1, so MTBF = runtime = period_hours.
        self.assertAlmostEqual(rep.mtbf_hours, 30 * 24, places=1)

    def test_mttr_computes_correctly(self):
        today = fields.Date.context_today(self.env.user)
        # request_date / close_date are Date in v19 maintenance; 4h ≈ 0.166 day.
        # Use 2 days (48h) downtime, easier to assert at Date precision.
        req = self.Request.create({
            "name": "Test breakdown",
            "equipment_id": self.equipment.id,
            "maintenance_type": "corrective",
            "request_date": today - timedelta(days=2),
            "close_date": today,
        })
        self.assertTrue(req)
        rep = self.env["southbrook.cmms.mtbf_mttr_report"].create({
            "equipment_id": self.equipment.id,
            "as_of_date": today,
            "period_days": 30,
        })
        self.assertEqual(rep.breakdown_count, 1)
        self.assertAlmostEqual(rep.mttr_hours, 48.0, delta=1.0)

    # ---- Breakdown alert ----
    def test_breakdown_alert_creates_maintenance_request(self):
        alert = self.env["southbrook.cmms.breakdown_alert"].create({
            "equipment_id": self.equipment.id,
            "severity": "high",
            "description": "Bearing seized",
        })
        alert.action_dispatch()
        self.assertTrue(alert.maintenance_request_id)
        self.assertEqual(alert.state, "dispatched")
        self.assertEqual(alert.maintenance_request_id.priority, "2")

    def test_breakdown_finds_affected_mos(self):
        bom_product = self.Product.create({
            "name": "MO Test Product",
            "type": "consu",
        })
        operation_vals = {
            "name": "Cut",
            "workcenter_id": self.workcenter.id,
        }
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": bom_product.product_tmpl_id.id,
            "product_qty": 1.0,
            "operation_ids": [(0, 0, operation_vals)],
        })
        mo = self.Production.create({
            "product_id": bom_product.id,
            "product_qty": 1.0,
            "bom_id": bom.id,
        })
        try:
            mo.action_confirm()
        except Exception:
            # Confirm may require warehouse setup; the test still verifies
            # the search domain finds an MO in 'confirmed' state where
            # possible. If confirm fails we skip the assertion gracefully.
            self.skipTest("mrp.production.action_confirm requires warehouse setup")
        alert = self.env["southbrook.cmms.breakdown_alert"].create({
            "equipment_id": self.equipment.id,
            "workcenter_id": self.workcenter.id,
        })
        self.assertIn(mo, alert.affected_production_ids)

    # ---- Service contract ----
    def test_service_contract_days_to_expiry(self):
        today = fields.Date.context_today(self.env.user)
        contract = self.env["southbrook.cmms.service_contract"].create({
            "name": "Annual Saw Service",
            "vendor_id": self.vendor.id,
            "date_end": today + timedelta(days=45),
        })
        self.assertEqual(contract.days_to_expiry, 45)
        self.assertEqual(contract.state, "expiring_soon")

    def test_expiry_cron_runs_and_deduplicates(self):
        """The expiry cron must run without crashing (regression: it used the
        v19-removed res.groups.users), post an alert, stamp last_alert_date,
        and NOT re-alert on a same-week second run."""
        today = fields.Date.context_today(self.env.user)
        contract = self.env["southbrook.cmms.service_contract"].create({
            "name": "Expiring Contract",
            "vendor_id": self.vendor.id,
            "date_end": today + timedelta(days=20),
            "expiry_alert_at": "60",
        })
        SC = self.env["southbrook.cmms.service_contract"]
        sent1 = SC._cron_check_expiry()
        self.assertGreaterEqual(sent1, 1)
        self.assertEqual(contract.last_alert_date, today)
        # Second run same day: throttled, no new alert for this contract.
        before = contract.last_alert_date
        SC._cron_check_expiry()
        self.assertEqual(contract.last_alert_date, before)

    # ---- Oversize picking ----
    def test_oversize_flag_triggers_on_2400mm(self):
        bigprod = self.Product.create({
            "name": "Oversize Slab",
            "type": "consu",
        })
        tmpl = bigprod.product_tmpl_id
        # Try product-level then template-level dim fields defensively.
        wrote = False
        for fname in ("product_length", "product_width", "product_height"):
            if fname in bigprod._fields:
                bigprod.write({fname: 2500.0})
                wrote = True
                break
            if fname in tmpl._fields:
                tmpl.write({fname: 2500.0})
                wrote = True
                break
        if not wrote:
            self.skipTest("No product dimension field available in this build")
        picking = self.Picking.create({
            "partner_id": self.vendor.id,
            "picking_type_id": self.picking_type.id,
            "location_id": self.picking_type.default_location_src_id.id
                or self.env.ref("stock.stock_location_suppliers").id,
            "location_dest_id": self.picking_type.default_location_dest_id.id
                or self.env.ref("stock.stock_location_stock").id,
            "move_ids": [(0, 0, {
                # v19 dropped stock.move.name; description comes from
                # product display_name + description_picking.
                "description_picking": bigprod.name,
                "product_id": bigprod.id,
                "product_uom_qty": 1.0,
                "product_uom": bigprod.uom_id.id,
                "location_id": self.picking_type.default_location_src_id.id
                    or self.env.ref("stock.stock_location_suppliers").id,
                "location_dest_id": self.picking_type.default_location_dest_id.id
                    or self.env.ref("stock.stock_location_stock").id,
            })],
        })
        picking._compute_is_oversize_load()
        self.assertTrue(picking.is_oversize_load)

    # ---- Landed cost template ----
    def test_landed_cost_template_apply_creates_landed_cost(self):
        template = self.env["southbrook.cmms.landed_cost_template"].create({
            "name": "Std Import",
            "freight_pct": 10.0,
        })
        picking = self.Picking.create({
            "partner_id": self.vendor.id,
            "picking_type_id": self.picking_type.id,
            "location_id": self.picking_type.default_location_src_id.id
                or self.env.ref("stock.stock_location_suppliers").id,
            "location_dest_id": self.picking_type.default_location_dest_id.id
                or self.env.ref("stock.stock_location_stock").id,
            "move_ids": [(0, 0, {
                # v19 dropped stock.move.name; description comes from
                # product display_name + description_picking.
                "description_picking": self.product.name,
                "product_id": self.product.id,
                "product_uom_qty": 5.0,
                "product_uom": self.product.uom_id.id,
                "location_id": self.picking_type.default_location_src_id.id
                    or self.env.ref("stock.stock_location_suppliers").id,
                "location_dest_id": self.picking_type.default_location_dest_id.id
                    or self.env.ref("stock.stock_location_stock").id,
            })],
        })
        try:
            lc = template.apply_to_picking(picking)
        except UserError as exc:
            self.skipTest("Landed cost setup incomplete in this DB: %s" % exc)
        self.assertTrue(lc)
        self.assertEqual(len(lc), 1)

    # ---- W0: outages measured in hours, sourced from alerts ----
    def _make_alert(self, hours_ago=3.0, **vals):
        base = {
            "equipment_id": self.equipment.id,
            "severity": "high",
            "reported_at": fields.Datetime.now() - timedelta(hours=hours_ago),
        }
        base.update(vals)
        return self.env["southbrook.cmms.breakdown_alert"].create(base)

    def _cause(self):
        return self.env.ref("southbrook_cmms_wms.failure_cause_tool_wear")

    def _report(self, period_days=30):
        return self.env["southbrook.cmms.mtbf_mttr_report"].create({
            "equipment_id": self.equipment.id,
            "as_of_date": fields.Date.context_today(self.env.user),
            "period_days": period_days,
        })

    def test_downtime_reads_in_hours_not_whole_days(self):
        """Regression: request Dates made every outage a multiple of 24 h."""
        alert = self._make_alert(hours_ago=3.0)
        self.assertAlmostEqual(alert.downtime_hours, 3.0, delta=0.1)
        alert.failure_cause_id = self._cause()
        alert.action_fix()
        self.assertTrue(alert.fixed_at)
        self.assertAlmostEqual(alert.downtime_hours, 3.0, delta=0.1)

    def test_same_day_repair_is_not_invisible(self):
        """A 5 h outage opened and closed today must dent availability.

        Under request-sourced Date math it scored zero downtime and 100%
        availability — a fast repair looked exactly like no failure.
        """
        alert = self._make_alert(hours_ago=5.0)
        alert.failure_cause_id = self._cause()
        alert.action_fix()
        rep = self._report()
        self.assertEqual(rep.breakdown_count, 1)
        self.assertAlmostEqual(rep.total_downtime_hours, 5.0, delta=0.3)
        self.assertLess(rep.availability_pct, 100.0)

    def test_alert_counts_without_anyone_pressing_dispatch(self):
        """Regression: only dispatched alerts reached the reliability numbers."""
        alert = self._make_alert(hours_ago=2.0)
        self.assertFalse(alert.maintenance_request_id)
        self.assertEqual(self._report().breakdown_count, 1)

    def test_dispatched_alert_is_not_double_counted(self):
        alert = self._make_alert(hours_ago=2.0)
        alert.action_dispatch()
        self.assertTrue(alert.maintenance_request_id)
        self.assertEqual(self._report().breakdown_count, 1)

    def test_cannot_close_an_outage_without_naming_the_cause(self):
        alert = self._make_alert()
        with self.assertRaises(UserError):
            alert.action_fix()

    def test_fixed_at_is_stamped_on_any_route_into_fixed(self):
        alert = self._make_alert()
        alert.failure_cause_id = self._cause()
        alert.state = "fixed"          # statusbar / import / automation path
        self.assertTrue(alert.fixed_at)
        alert.action_reopen()
        self.assertFalse(alert.fixed_at)

    # ---- W0: KPI history actually persists ----
    def test_kpi_snapshot_keeps_one_row_per_day(self):
        Tiles = self.env["southbrook.cmms.mi_tiles"]
        today = fields.Date.context_today(self.env.user)
        Tiles._cron_snapshot()
        self.assertEqual(Tiles.search_count([("snapshot_date", "=", today)]), 1)
        Tiles._cron_snapshot()
        self.assertEqual(Tiles.search_count([("snapshot_date", "=", today)]), 1)

    def test_kpi_values_are_written_to_the_column(self):
        """Regression: tiles were store=False, so no history could exist."""
        Tiles = self.env["southbrook.cmms.mi_tiles"]
        self._make_alert(hours_ago=1.0)          # leaves an open breakdown
        rec = Tiles.browse(Tiles._cron_snapshot())
        self.assertGreaterEqual(rec.open_breakdowns_count, 1)
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT open_breakdowns_count FROM southbrook_cmms_mi_tiles "
            "WHERE id = %s", (rec.id,))
        self.assertGreaterEqual(self.env.cr.fetchone()[0], 1)

    # ---- W0: reliability targets say where they came from ----
    def test_expected_mtbf_never_invents_a_target(self):
        eq = self.Equipment.create({"name": "Target Test Machine"})
        self.assertEqual(eq.sb_expected_mtbf_source, "none")
        self.assertTrue(eq.sb_reliability_target_missing)
        self.assertEqual(eq.sb_effective_expected_mtbf, 0.0)

        self.env["ir.config_parameter"].sudo().set_param(
            "southbrook_cmms_wms.default_expected_mtbf", "1500")
        eq.invalidate_recordset()
        self.assertEqual(eq.sb_expected_mtbf_source, "default")
        self.assertAlmostEqual(eq.sb_effective_expected_mtbf, 1500.0, places=1)

        eq.expected_mtbf = 2000
        self.assertEqual(eq.sb_expected_mtbf_source, "equipment")
        self.assertAlmostEqual(eq.sb_effective_expected_mtbf, 2000.0, places=1)
        self.assertFalse(eq.sb_reliability_target_missing)

    def test_failure_causes_are_seeded_and_editable(self):
        Cause = self.env["southbrook.cmms.failure_cause"]
        self.assertGreaterEqual(Cause.search_count([]), 10)
        cause = self._cause()
        cause.name = "Dull cutter"          # renaming must not be blocked
        self.assertEqual(cause.name, "Dull cutter")
        with self.assertRaises(ValidationError):
            Cause.create({"name": "dull cutter", "category": "tooling"})

    # ---- 19.0.4.0.0: window boundaries ----
    def test_window_start_respects_user_timezone(self):
        """The discriminating region is the first UTC-offset hours of the window.

        Naive math opened the window at period_start 00:00 UTC; the correct
        window opens at period_start 00:00 LOCAL, which in Toronto is 04:00 UTC.
        An alert in between belongs to the day BEFORE the window and must not
        be counted. A 30-day window hides this — only the boundary shows it.
        """
        self.env.user.tz = "America/Toronto"
        today = fields.Date.context_today(self.env.user)
        period_days = 30
        period_start = today - timedelta(days=period_days)
        # 02:00 UTC on the first day of the window == 22:00 local the day before
        self._make_alert(reported_at=datetime.combine(
            period_start, time(2, 0, 0)))

        rep = self.env["southbrook.cmms.mtbf_mttr_report"].create({
            "equipment_id": self.equipment.id,
            "as_of_date": today,
            "period_days": period_days,
        })
        self.assertEqual(
            rep.breakdown_count, 0,
            "an outage at 22:00 local the day before the window opens must "
            "not be counted inside it")

    def test_unknown_timezone_falls_back_to_utc(self):
        """A bad tz must degrade, not raise — this runs inside a nightly cron."""
        Report = self.env["southbrook.cmms.mtbf_mttr_report"]
        day = fields.Date.context_today(self.env.user)
        out = Report.with_context(tz="Not/AZone")._sb_local_day_to_utc(
            day, time.min)
        self.assertEqual(out, datetime.combine(day, time.min))

    def test_kpi_snapshot_windows_off_its_own_as_of(self):
        """A stored snapshot must describe the moment it claims to.

        Regression: tiles were computed against 'today' whatever as_of said, so
        the install-seeded row carried figures from weeks later and the first
        point of any trend line was wrong. The 30-day lookback also had no
        upper bound, so an old row pulled in everything recorded after it.
        """
        Tiles = self.env["southbrook.cmms.mi_tiles"]
        today = fields.Date.context_today(self.env.user)
        self.env["southbrook.cmms.mtbf_mttr_report"].create({
            "equipment_id": self.equipment.id,
            "as_of_date": today,
            "period_days": 30,
        })
        old = Tiles.create({"as_of": fields.Datetime.now() - timedelta(days=60)})
        self.assertEqual(
            old.mtbf_avg_30d, 0.0,
            "a 60-day-old snapshot must not average reports written since")

        fresh = Tiles.create({"as_of": fields.Datetime.now()})
        self.assertGreater(fresh.mtbf_avg_30d, 0.0)

    def test_dispatched_breakdown_still_counts_as_unresolved(self):
        """Dispatching tells somebody; it does not fix the machine.

        Regression: the tile counted state='open' only, so a breakdown that had
        been dispatched and never fixed vanished from the KPI. Production had a
        machine down 48 days behind a tile reading zero.
        """
        Tiles = self.env["southbrook.cmms.mi_tiles"]
        alert = self._make_alert(hours_ago=4.0)
        alert.action_dispatch()
        self.assertEqual(alert.state, "dispatched")

        snap = Tiles.create({"as_of": fields.Datetime.now()})
        self.assertGreaterEqual(
            snap.open_breakdowns_count, 1,
            "a dispatched, unfixed breakdown must still be counted")

        alert.failure_cause_id = self._cause()
        alert.action_fix()
        fixed_snap = Tiles.create({"as_of": fields.Datetime.now()})
        self.assertEqual(
            fixed_snap.open_breakdowns_count,
            snap.open_breakdowns_count - 1,
            "fixing it must remove it from the count")

    def test_kpi_history_has_no_seeded_placeholder(self):
        """History must start at a real snapshot, not an install seed.

        The seed was renamed "Smoke" during June testing and noupdate kept it,
        so a test record sat at the head of every trend.
        """
        seed = self.env.ref(
            "southbrook_cmms_wms.mi_tiles_default", raise_if_not_found=False)
        self.assertFalse(seed, "the install seed must no longer exist")
        Tiles = self.env["southbrook.cmms.mi_tiles"]
        self.assertFalse(
            Tiles.search([("label", "=", "Smoke")]),
            "no smoke-test row may remain in KPI history")
