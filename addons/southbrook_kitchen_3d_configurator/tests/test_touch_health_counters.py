# SPDX-License-Identifier: LGPL-3.0-only
"""Touch editing v1 (2026-08-02 spec §4 item 3 / addendum item 3) —
minimal health-signal counters riding the existing save_design
round-trip.

`save_design` grows one new OPTIONAL kwarg, `touch_stats`
({gesture_rollbacks: int, gesture_abandons: int}). Contract pinned
here:

  1. Absent entirely -> the response is byte-identical to a call that
     never knew this kwarg existed (existing clients must not see any
     change at all).
  2. Present and valid -> validated via normalize_touch_stats and
     logged ONE line via _logger.info; response shape still unchanged
     (this is a log-only signal, no new model/table/response key).
  3. Malformed (wrong type, non-numeric, not a dict at all) -> ignored
     silently: no log line, no exception, normal save proceeds.
  4. Out-of-range ints are clamped, never rejected or passed through
     unbounded into the log line.

normalize_touch_stats itself (the model-layer validator) is pinned
directly too, same convention as this file's sibling
clamp_counter_thickness_in-style unit coverage elsewhere in this
module.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    TOUCH_STATS_MAX,
    normalize_touch_stats,
)


@contextmanager
def stubbed_request(env):
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env
    mock.session = {}
    mock.params = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


# ── Pure validator unit tests ────────────────────────────────────────────────
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "touch_editing")
class TestNormalizeTouchStats(TransactionCase):

    def test_absent_or_none_returns_empty(self):
        self.assertEqual(normalize_touch_stats(None), {})

    def test_non_dict_returns_empty(self):
        for bad in ("nope", 42, [1, 2], True):
            self.assertEqual(normalize_touch_stats(bad), {})

    def test_valid_ints_pass_through(self):
        self.assertEqual(
            normalize_touch_stats(
                {"gesture_rollbacks": 3, "gesture_abandons": 1}),
            {"gesture_rollbacks": 3, "gesture_abandons": 1},
        )

    def test_unknown_keys_dropped(self):
        self.assertEqual(
            normalize_touch_stats({"gesture_rollbacks": 2, "evil": 999}),
            {"gesture_rollbacks": 2},
        )

    def test_non_numeric_value_dropped(self):
        self.assertEqual(
            normalize_touch_stats(
                {"gesture_rollbacks": "not-a-number", "gesture_abandons": 2}),
            {"gesture_abandons": 2},
        )

    def test_negative_clamped_to_zero(self):
        self.assertEqual(
            normalize_touch_stats({"gesture_rollbacks": -5}),
            {"gesture_rollbacks": 0},
        )

    def test_over_cap_clamped(self):
        self.assertEqual(
            normalize_touch_stats({"gesture_rollbacks": TOUCH_STATS_MAX + 500}),
            {"gesture_rollbacks": TOUCH_STATS_MAX},
        )

    def test_missing_both_keys_returns_empty(self):
        self.assertEqual(normalize_touch_stats({"unrelated": 1}), {})

    def test_float_string_coerces_via_int_cast(self):
        # int("3") works; int("3.5") does NOT (ValueError) — dropped,
        # not rounded, matching this file's "never guess" convention.
        self.assertEqual(
            normalize_touch_stats({"gesture_rollbacks": "3"}),
            {"gesture_rollbacks": 3},
        )
        self.assertEqual(
            normalize_touch_stats({"gesture_rollbacks": "3.5"}),
            {},
        )


# ── save_design integration ──────────────────────────────────────────────────
@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "touch_editing")
class TestSaveDesignTouchStats(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Touch Health Counters Test Customer",
            "email": "kitchen3d.touchstats@southbrook.test",
        })
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Touch health counters test",
            "partner_id": cls.partner.id,
            "room_width_in": 144,
            "room_depth_in": 96,
            "room_height_in": 96,
        })
        cls.tmpl_base = cls.env["product.template"].create({
            "name": "Touch Stats Test Base Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl_base.southbrook_is_cabinet = True
        cls.tmpl_base.southbrook_cabinet_type = "base"
        cls.product_base = cls.tmpl_base.product_variant_id

    def _items(self):
        return [{
            "product_id":    self.product_base.id,
            "layout_key":    "touchstats-base-1",
            "wall":          "back",
            "x_position_in": 0,
            "y_position_in": 0,
            "z_position_in": 0,
            "rotation_deg":  0,
            "pinned":        False,
            "width_in":      24.0,
            "height_in":     34.5,
            "depth_in":      24.0,
            "price":         500.0,
        }]

    def _save(self, **kw):
        controller = ctrl_main.SouthbrookKitchenConfiguratorController()
        with stubbed_request(self.env):
            return controller.save_design(
                name=self.design.name,
                room={"width_in": 144, "depth_in": 96, "height_in": 96},
                items=self._items(),
                partner_id=self.partner.id,
                design_id=self.design.id,
                **kw,
            )

    def test_absent_touch_stats_is_byte_compatible(self):
        """No touch_stats kwarg at all (the pre-touch-v1 call shape) —
        response keys/behaviour must be exactly what an existing client
        already gets, and no [touch] log line fires."""
        with patch.object(ctrl_main, "_logger") as mock_logger:
            result = self._save()
        self.assertNotIn("error", result)
        self.assertEqual(
            set(result.keys()),
            {"id", "name", "placed", "relaid", "lines",
             "layout_warning", "summary"},
        )
        mock_logger.info.assert_not_called()

    def test_touch_stats_none_is_byte_compatible(self):
        """Explicit touch_stats=None (a client build that sends the key
        but nothing to report) behaves identically to omitting it."""
        with patch.object(ctrl_main, "_logger") as mock_logger:
            result = self._save(touch_stats=None)
        self.assertNotIn("error", result)
        mock_logger.info.assert_not_called()

    def test_valid_touch_stats_logs_one_line(self):
        with patch.object(ctrl_main, "_logger") as mock_logger:
            result = self._save(touch_stats={
                "gesture_rollbacks": 4, "gesture_abandons": 2,
            })
        self.assertNotIn("error", result)
        mock_logger.info.assert_called_once()
        args = mock_logger.info.call_args.args
        # First arg is the format string; remaining are %s substitutions
        # (design id, rollbacks, abandons) — assert the values landed,
        # not the exact wording.
        self.assertIn(self.design.id, args)
        self.assertIn(4, args)
        self.assertIn(2, args)

    def test_valid_touch_stats_does_not_change_response_shape(self):
        result_without = self._save()
        result_with = self._save(touch_stats={
            "gesture_rollbacks": 1, "gesture_abandons": 0,
        })
        self.assertEqual(set(result_without.keys()), set(result_with.keys()))

    def test_malformed_touch_stats_ignored_silently(self):
        """A non-dict touch_stats (e.g. a stray string/int from a buggy
        client build) must never raise and must never log."""
        with patch.object(ctrl_main, "_logger") as mock_logger:
            result = self._save(touch_stats="not-a-dict")
        self.assertNotIn("error", result)
        mock_logger.info.assert_not_called()

    def test_touch_stats_with_only_unknown_keys_does_not_log(self):
        with patch.object(ctrl_main, "_logger") as mock_logger:
            result = self._save(touch_stats={"totally_unrelated": 1})
        self.assertNotIn("error", result)
        mock_logger.info.assert_not_called()

    def test_out_of_range_touch_stats_clamped_in_log(self):
        with patch.object(ctrl_main, "_logger") as mock_logger:
            result = self._save(touch_stats={
                "gesture_rollbacks": TOUCH_STATS_MAX * 10,
                "gesture_abandons": -100,
            })
        self.assertNotIn("error", result)
        mock_logger.info.assert_called_once()
        args = mock_logger.info.call_args.args
        self.assertIn(TOUCH_STATS_MAX, args)
        self.assertIn(0, args)
