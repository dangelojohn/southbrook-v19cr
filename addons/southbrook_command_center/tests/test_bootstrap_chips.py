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
        payload, _prov = self.Bridge.fetch()
        if not payload:
            self.skipTest("Process Explorer payload unavailable here")
        workload = payload["workload"]
        chips = {c["key"]: c["value"] for c in self.CC.truth_chips()}
        self.assertEqual(chips["orders"], workload["orders"])
        self.assertEqual(chips["running"], workload["progress"])
        self.assertEqual(chips["ready"], workload["ready"])
        self.assertEqual(chips["blocked"], workload["blocked"])
        self.assertEqual(chips["late"], workload["late"])
        self.assertEqual(chips["at_risk"], workload["at_risk"])

    def test_chips_degrade_to_none_not_zero(self):
        """With no payload a chip is unknown, not zero — zero is a claim."""
        chips = self.CC.truth_chips(payload={})
        for c in chips:
            self.assertIsNone(c["value"])
