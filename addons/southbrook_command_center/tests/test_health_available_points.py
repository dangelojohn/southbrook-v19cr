# SPDX-License-Identifier: LGPL-3.0-only
"""Absence of data is never a score — in either direction.

A component with no observations must not bank zero (which deflates and is at
least visible) and must not bank full marks (which inflates and is invisible).
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook_command_center")
class TestHealthAvailablePoints(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CC = cls.env["southbrook.command.center"]

    def test_shape(self):
        h = self.CC.factory_health_score()
        for key in ("score", "available", "coverage_pct", "band",
                    "factors", "provenance"):
            self.assertIn(key, h)

    def test_every_factor_declares_its_data_state(self):
        h = self.CC.factory_health_score()
        for f in h["factors"]:
            self.assertIn(f["data_state"], ("live", "no_data"), f["label"])
            if f["data_state"] == "no_data":
                self.assertTrue(f["no_data_reason"],
                                "a no-data factor must say why: %s" % f["label"])

    def test_no_data_factors_are_excluded_from_the_denominator(self):
        h = self.CC.factory_health_score()
        expected = sum(f["of"] for f in h["factors"] if f["data_state"] == "live")
        self.assertEqual(h["available"], expected,
                         "no-data components must not sit in the denominator")

    def test_no_data_factors_score_neither_zero_nor_full(self):
        h = self.CC.factory_health_score()
        for f in h["factors"]:
            if f["data_state"] == "no_data":
                self.assertIsNone(f["points"],
                                  "no-data must be None, not 0 and not full marks")

    def test_score_never_exceeds_available(self):
        h = self.CC.factory_health_score()
        self.assertLessEqual(h["score"], h["available"])

    def test_band_is_insufficient_when_nothing_is_measurable(self):
        h = self.CC.factory_health_score()
        if h["available"] == 0:
            self.assertEqual(h["band"], "insufficient")
        else:
            self.assertIn(h["band"], ("green", "amber", "red"))

    def test_on_time_delivery_is_not_perfect_without_observations(self):
        """The specific production bug: 0 outgoing pickings scored 25 of 25."""
        otd = next((f for f in self.CC.factory_health_score()["factors"]
                    if "On-Time" in f["label"]), None)
        self.assertIsNotNone(otd)
        picks = self.env["stock.picking"].search_count([
            ("state", "=", "done"), ("picking_type_code", "=", "outgoing")])
        if picks == 0:
            self.assertEqual(otd["data_state"], "no_data")
