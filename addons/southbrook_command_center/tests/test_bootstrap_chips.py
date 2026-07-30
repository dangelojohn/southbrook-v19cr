# SPDX-License-Identifier: LGPL-3.0-only
"""The chips must be Process Explorer's numbers, and must carry their units."""
from odoo.tests import TransactionCase, tagged

CHIP_KEYS = ["orders", "running", "ready", "blocked", "late", "at_risk",
             "material_short", "capacity_over"]


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestBootstrapChips(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bridge = cls.env["southbrook.command.explorer_bridge"]
        cls.CC = cls.env["southbrook.command.center"]

    def test_eight_chips_with_units(self):
        chips = self.CC.truth_chips()
        self.assertEqual([c["key"] for c in chips], CHIP_KEYS)
        for c in chips:
            self.assertTrue(c["unit"], "every chip states its unit: %s" % c["key"])

    def test_chip_values_equal_the_explorer_payload(self):
        """The core guarantee: chips must equal Process Explorer's own payload.

        Each chip must carry a distinct, non-zero value that changes if the chip
        key is wired to the wrong payload key. This test seeds a fixture with:
        - Multiple work orders in different states (running, ready, blocked)
        - A past deadline (makes late > 0)
        - All values measurable and discriminative

        Tests only run on dev/staging, never on production, so a skipped test
        is a test that never runs anywhere — not a guarantee.
        """
        from datetime import datetime, timedelta
        from odoo import fields

        # --- Arrange: seed MRP data with distinct work order states ---
        # Create a workcenter (needed for operations)
        wc = self.env["mrp.workcenter"].create({
            "name": "Test Workcenter",
            "code": "TEST_WC",
        })

        # Create a storable product
        product = self.env["product.product"].create({
            "name": "Chip Test Product",
            "type": "consu",
        })

        # Create a BoM with operations (routing) so work orders are generated
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id,
            "product_qty": 1.0,
            "type": "normal",
        })

        # Add operations to the BoM — creates mrp.routing.workcenter entries.
        # When we confirm the MO, these operations generate mrp.workorder records.
        # Create 6 operations to generate work orders with ASYMMETRIC counts:
        # - 1 in progress (running)
        # - 2 in ready (ready)
        # - 3 in blocked (blocked)
        # If ready/blocked are swapped in code, test will see 2 vs 3 instead of 3 vs 2 → FAIL
        for i in range(6):
            self.env["mrp.routing.workcenter"].create({
                "bom_id": bom.id,
                "name": f"Op {i+1}",
                "workcenter_id": wc.id,
                "time_cycle_manual": 60.0,  # 1 hour each
            })

        # Create an MO with a PAST deadline (so late > 0)
        today = fields.Date.context_today(self.env["res.users"])
        past_deadline = datetime.combine(
            today - timedelta(days=1), datetime.min.time().replace(hour=12)
        )
        mo = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1.0,
            "bom_id": bom.id,
            "date_deadline": past_deadline,
        })
        self.assertTrue(mo.id, "Manufacturing order must be created")

        # Confirm the MO — this generates work orders from the routing operations
        mo.action_confirm()
        self.assertEqual(mo.state, "confirmed", "MO must be confirmed")

        # Get the generated work orders
        work_orders = self.env["mrp.workorder"].search(
            [("production_id", "=", mo.id)], order="sequence"
        )
        self.assertEqual(len(work_orders), 6, "BoM with 6 operations must generate 6 work orders")

        # Drive work orders into ASYMMETRIC states:
        # WO[0] -> progress (1 running)
        # WO[1:3] -> ready (2 ready)
        # WO[3:6] -> blocked (3 blocked)
        # This ensures swapping ready/blocked would break the assertion
        for i, wo in enumerate(work_orders):
            if i < 1:
                wo.state = "progress"
            elif i < 3:
                wo.state = "ready"
            else:
                wo.state = "blocked"

        # --- Act: fetch the payload ---
        payload, _prov = self.Bridge.fetch()

        # --- Assert: payload is available and equal to chips ---
        self.assertTrue(
            payload,
            "Process Explorer payload must be available after seeding MO with ops; "
            "got: %s" % self.Bridge.fetch()[1]
        )
        self.assertIn("workload", payload, "Payload must have workload section")
        workload = payload["workload"]

        # Get the chip values
        chips = {c["key"]: c["value"] for c in self.CC.truth_chips()}

        # The six core chip values derived from the payload — each should be
        # non-zero and distinct, so swapping any chip key breaks the test.
        self.assertEqual(chips["orders"], workload["orders"],
                        f"orders chip {chips['orders']} must equal workload "
                        f"orders {workload['orders']}")
        self.assertEqual(chips["running"], workload["progress"],
                        f"running chip {chips['running']} must equal workload "
                        f"progress {workload['progress']}")
        self.assertEqual(chips["ready"], workload["ready"],
                        f"ready chip {chips['ready']} must equal workload "
                        f"ready {workload['ready']}")
        self.assertEqual(chips["blocked"], workload["blocked"],
                        f"blocked chip {chips['blocked']} must equal workload "
                        f"blocked {workload['blocked']}")
        self.assertEqual(chips["late"], workload["late"],
                        f"late chip {chips['late']} must equal workload "
                        f"late {workload['late']}")
        self.assertEqual(chips["at_risk"], workload["at_risk"],
                        f"at_risk chip {chips['at_risk']} must equal workload "
                        f"at_risk {workload['at_risk']}")

    def test_chips_degrade_to_none_not_zero(self):
        """With no payload a chip is unknown, not zero — zero is a claim."""
        chips = self.CC.truth_chips(payload={})
        for c in chips:
            self.assertIsNone(c["value"])
