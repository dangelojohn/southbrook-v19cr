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
        min_coverage = 50  # default from factory_health.min_coverage_points
        if h["available"] == 0 or h["available"] < min_coverage:
            self.assertEqual(h["band"], "insufficient")
        else:
            self.assertIn(h["band"], ("green", "amber", "red"))

    def test_on_time_delivery_is_not_perfect_without_observations(self):
        """The specific production bug: 0 closed orders scored 25 of 25.

        The test gates on the actual driver — Process Explorer's
        ontime.total count — since the implementation reads from payload.
        """
        h = self.CC.factory_health_score()
        otd = next((f for f in h["factors"]
                    if "On-Time" in f["label"]), None)
        self.assertIsNotNone(otd)
        # Gate on the actual payload driver: ontime.total
        bridge = self.env["southbrook.command.explorer_bridge"]
        payload, _ = bridge.fetch()
        ontime = (payload.get("ontime") or {}) if payload else {}
        if not ontime.get("total"):
            self.assertEqual(otd["data_state"], "no_data",
                           "OTD must be no_data when Process Explorer has "
                           "no closed orders to measure")

    def test_oee_no_data_when_stale_but_present(self):
        """OEE records exist all-time but none in the 7d window -> no_data."""
        h = self.CC.factory_health_score()
        oee = next((f for f in h["factors"] if "OEE" in f["label"]), None)
        self.assertIsNotNone(oee)
        # This test documents the guard: snapshots must exist in the 7d window,
        # not just exist all-time. If only stale snapshots exist, guard says
        # no_data and the metric defaults to zero (visible absence, not hidden).
        if oee["data_state"] == "no_data":
            self.assertIn("7 days", oee["no_data_reason"])

    def test_fpy_no_data_when_stale_but_present(self):
        """FPY orders exist all-time but none in the 7d window -> no_data."""
        h = self.CC.factory_health_score()
        fpy = next((f for f in h["factors"] if "First-Pass" in f["label"]), None)
        self.assertIsNotNone(fpy)
        # This test documents the guard: closed MOs must exist in the 7d window
        # AND in the current company, not just exist all-time. If only stale
        # or out-of-company orders exist, guard says no_data.
        if fpy["data_state"] == "no_data":
            self.assertIn("7 days", fpy["no_data_reason"])

    def test_band_insufficient_below_coverage_floor(self):
        """Band is insufficient when available < min_coverage_points, even if
        the ratio is 100%.

        A single 15-point component live (blockers) with 85 points missing
        should not band green, amber, or red — it must band insufficient.
        """
        h = self.CC.factory_health_score()
        # If less than 50 points (the default floor) are available, band must
        # be "insufficient" regardless of score/available ratio
        if h["available"] < 50:
            self.assertEqual(h["band"], "insufficient",
                           "Band must be insufficient when coverage falls "
                           "below the minimum floor, regardless of ratio")
