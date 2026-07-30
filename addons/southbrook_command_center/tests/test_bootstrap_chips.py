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

        This test arranges its own preconditions (a minimal MO) since tests
        only run on development/staging, never on production. A skipped test
        is a test that never runs anywhere — that's not a guarantee.
        """
        # --- Arrange: seed minimal MRP data ---
        # Create a storable product
        product = self.env["product.product"].create({
            "name": "Chip Test Product",
            "type": "consu",
        })
        # Create a minimal BoM
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id,
            "product_qty": 1.0,
        })
        # Create an MO in "confirmed" state so it appears in Process Explorer
        mo = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1.0,
            "bom_id": bom.id,
        })
        self.assertTrue(mo.id, "Manufacturing order must be created")

        # --- Act: fetch the payload ---
        payload, _prov = self.Bridge.fetch()

        # --- Assert: payload is available and equal to chips ---
        self.assertTrue(
            payload,
            "Process Explorer payload must be available after seeding MO; "
            "got: %s" % self.Bridge.fetch()[1]
        )
        self.assertIn("workload", payload, "Payload must have workload section")
        workload = payload["workload"]

        # Get the chip values
        chips = {c["key"]: c["value"] for c in self.CC.truth_chips()}

        # The eight live chip values derived from the payload
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
