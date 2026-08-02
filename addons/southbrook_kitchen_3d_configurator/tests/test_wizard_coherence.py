# SPDX-License-Identifier: LGPL-3.0-only
"""FINDING 13 (E2E audit, 2026-08-02) — "Wizard problems": the picker
wizard's "Number of Cabinets" field silently snapped a typed 14 back to
8 with no explanation, the resulting design's line count didn't match
either number, and 84in of wall run was left unused with no mention of
it. Regression pins for the `southbrook.kitchen.template` side of the
fix (models/kitchen_template.py):

- `parametric_fit`/`action_instantiate` gain an opt-in `allow_clamp`
  kwarg — default False keeps every existing caller's hard-fail
  contract byte-for-byte (see test_template_instantiate.py's
  `test_no_silent_room_growth` / `test_appliance_reservation_bounds_
  repeat_growth`, both still expect UserError and are untouched here).
  With `allow_clamp=True`, an over-capacity request is honestly reduced
  instead — `fit["clamped"]`/`fit["requested_count"]` say so, and the
  Task 16 instantiation chatter names both numbers and the reason.
- `leftover_run_in` (same dict) reports wall run this fit's cabinets
  never claimed, reusing the round-robin allocator's own `remaining`
  arithmetic — surfaced in the same chatter line whenever it exceeds a
  quarter-module noise floor.
- `cabinet_count=0` (a literal, explicit ask for zero cabinets) is
  never conflated with "not given" — `is not None`, not `or`.

Fixture mirrors test_template_instantiate.py's T2-SW-10 (SINK fixed +
B1 repeat_ok + RANGE appliance + W1 wall, room 120x96, module 24in):
fixed[back] = SINK + RANGE, capacity[back] = floor((120-fixed)/24) = 2,
n_fixed_modules = 1 (SINK), so n_max = 1 + 2 = 3 — proven independently
by that file's own `test_appliance_reservation_bounds_repeat_growth`.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestWizardCoherence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tpl = cls.env["southbrook.kitchen.template"]
        Slot = cls.env["southbrook.kitchen.template.line"]
        base = cls.env.ref("southbrook_estimating.base_2dr")
        sink = cls.env.ref("southbrook_estimating.sink_base")
        wall = cls.env.ref("southbrook_estimating.wall_2dr")
        cls.tpl = Tpl.create({
            "name": "Wizard Coherence Straight 10ft", "code": "TWC-SW-10",
            "layout_shape": "straight",
            "default_room_width_in": 120.0, "default_room_depth_in": 96.0,
            "default_room_height_in": 96.0, "default_module_width_in": 24.0,
            "min_cabinet_count": 2,
        })
        Slot.create([
            {"template_id": cls.tpl.id, "slot_code": "SINK", "cabinet_type": "base",
             "wall": "back", "run_seq": 10, "product_id": sink.product_variant_id.id},
            {"template_id": cls.tpl.id, "slot_code": "B1", "cabinet_type": "base",
             "wall": "back", "run_seq": 20, "repeat_ok": True,
             "product_id": base.product_variant_id.id},
            {"template_id": cls.tpl.id, "slot_code": "RANGE",
             "cabinet_type": "appliance", "appliance_type": "range",
             "wall": "back", "run_seq": 30, "nominal_width_in": 30.0},
            {"template_id": cls.tpl.id, "slot_code": "W1", "cabinet_type": "wall",
             "wall": "back", "run_seq": 10,
             "product_id": wall.product_variant_id.id},
        ])

    # -- clamp honesty ---------------------------------------------------

    def test_clamped_request_records_a_warning_naming_requested_vs_actual(self):
        fit = self.tpl.parametric_fit(
            cabinet_count=6, module_width_in=24.0, allow_clamp=True)
        self.assertTrue(fit["ok"], fit["message"])
        self.assertTrue(fit["clamped"])
        self.assertEqual(fit["requested_count"], 6)
        self.assertEqual(fit["count"], 3, "n_max for this fixture is 3")
        self.assertIn("6", fit["message"])
        self.assertIn("3", fit["message"])

        design = self.tpl.action_instantiate(
            cabinet_count=6, module_width_in=24.0, allow_clamp=True)
        msg = design.message_ids.filtered(
            lambda m: "Instantiated from template" in (m.body or ""))
        self.assertEqual(len(msg), 1, "instantiation chatter must be posted")
        self.assertIn(
            "requested cabinet count 6 was reduced to 3 to fit this "
            "room's capacity", msg.body,
            "chatter must name both the requested and the actual count: %s"
            % msg.body)

    def test_unclamped_request_records_no_warning(self):
        # cabinet_count=3 sits exactly at this fixture's n_max=3 — never
        # exceeds capacity, so allow_clamp=True never has anything to do.
        fit = self.tpl.parametric_fit(
            cabinet_count=3, module_width_in=24.0, allow_clamp=True)
        self.assertTrue(fit["ok"], fit["message"])
        self.assertFalse(fit["clamped"])
        self.assertEqual(fit["requested_count"], 3)
        self.assertEqual(fit["count"], 3)

        design = self.tpl.action_instantiate(
            cabinet_count=3, module_width_in=24.0, allow_clamp=True)
        msg = design.message_ids.filtered(
            lambda m: "Instantiated from template" in (m.body or ""))
        self.assertEqual(len(msg), 1)
        self.assertNotIn("was reduced to", msg.body)
        self.assertNotIn("left unused", msg.body)

    # -- numeric-falsy guard ----------------------------------------------

    def test_literal_zero_cabinet_count_is_not_silently_defaulted(self):
        # Natural default for this fixture is 2 (1 fixed + 1 repeat). A
        # literal 0 must NEVER resolve to that default — it must be
        # honored as a genuine (too-low) request and rejected.
        natural = self.tpl.parametric_fit(module_width_in=24.0)
        self.assertEqual(natural["count"], 2)

        fit = self.tpl.parametric_fit(cabinet_count=0, module_width_in=24.0)
        self.assertFalse(fit["ok"])
        self.assertEqual(
            fit["count"], 0,
            "an explicit 0 must be honored, not silently replaced by the "
            "template's natural default (%d)" % natural["count"])
        self.assertIn("needs at least", fit["message"])

        with self.assertRaises(UserError):
            self.tpl.action_instantiate(cabinet_count=0, module_width_in=24.0)

    # -- leftover run surfacing --------------------------------------------

    def test_leftover_run_length_is_reported_when_the_generator_underfills(self):
        # No cabinet_count -> the template's natural default (2: 1 fixed
        # SINK + 1 B1 repeat instance) undershoots this fixture's back-
        # wall capacity of 2 module-widths by exactly 1 -- 24.0in unused.
        fit = self.tpl.parametric_fit(module_width_in=24.0)
        self.assertTrue(fit["ok"], fit["message"])
        self.assertFalse(fit["clamped"])
        self.assertEqual(fit["leftover_run_in"], 24.0)
        self.assertIn("24.0in of wall run left unused", fit["message"])

        design = self.tpl.action_instantiate(module_width_in=24.0)
        msg = design.message_ids.filtered(
            lambda m: "Instantiated from template" in (m.body or ""))
        self.assertEqual(len(msg), 1)
        self.assertIn("24.0in of wall run left unused", msg.body)

    def test_exact_fill_reports_no_leftover(self):
        # cabinet_count=3 (== n_max) consumes the back wall's entire
        # repeat capacity -- nothing left to report.
        fit = self.tpl.parametric_fit(cabinet_count=3, module_width_in=24.0)
        self.assertTrue(fit["ok"], fit["message"])
        self.assertEqual(fit["leftover_run_in"], 0.0)
        self.assertEqual(fit["message"], "")
