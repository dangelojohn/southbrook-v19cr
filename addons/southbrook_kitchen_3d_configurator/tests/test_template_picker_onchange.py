# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-051 — the "not enough wall" guard rail must not be the crash.

`kitchen.design.template.picker._onchange_parametrics` clamps an over-large
cabinet count down to what the chosen shape can actually hold, and returns a
warning explaining the reduction. That warning is built with `_()`, and `_`
was never imported in `models/kitchen_design.py`.

So the ONLY branch that produced a warning was the only branch that could not
run: a new designer who typed a large cabinet count got a full-screen Odoo
traceback (`NameError: name '_' is not defined`) on roughly their second
interaction with the product. The rail meant to catch over-specification was
itself the failure.

It survived because no test ever exercised the warning branch — every existing
test drove the clamp path with values that never triggered a notice. These
tests walk the branches, and the first one asserts the exact thing that was
broken: that a warning is returned rather than an exception raised.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "template_picker")
class TestTemplatePickerOnchange(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Picker = cls.env["kitchen.design.template.picker"]
        cls.template = cls.env["southbrook.kitchen.template"].search(
            [("active", "=", True)], limit=1)

    def _picker(self, **vals):
        base = {"template_id": self.template.id}
        base.update(vals)
        return self.Picker.new(base)

    def test_01_over_large_count_warns_instead_of_raising(self):
        """The regression itself. Before SBK-051 this raised NameError."""
        if not self.template:
            self.skipTest("no active kitchen template seeded")

        picker = self._picker()
        picker._onchange_parametrics()          # settle count_max
        allowed = picker.count_max
        if not allowed:
            self.skipTest("template reports no fit; nothing to clamp against")

        # Mark the shape as already seen so the "shape changed" branch does
        # not simply reset the count before the clamp can be reached.
        picker.last_seen_template_id = picker.template_id
        picker.cabinet_count = allowed + 91     # the reviewer typed 99

        result = picker._onchange_parametrics()

        self.assertIsInstance(
            result, dict,
            "an over-large cabinet count must return a warning dict, not "
            "raise — this is the guard rail, it cannot be the crash")
        self.assertIn("warning", result)
        self.assertTrue(result["warning"].get("title"))
        self.assertTrue(result["warning"].get("message"))
        self.assertEqual(
            picker.cabinet_count, allowed,
            "the count must actually be clamped to what fits")

    def test_02_a_count_within_capacity_is_left_alone_and_silent(self):
        if not self.template:
            self.skipTest("no active kitchen template seeded")

        picker = self._picker()
        picker._onchange_parametrics()
        allowed = picker.count_max
        if not allowed:
            self.skipTest("template reports no fit")

        picker.last_seen_template_id = picker.template_id
        picker.cabinet_count = allowed
        result = picker._onchange_parametrics()

        self.assertFalse(
            result,
            "a count that fits must not warn — a rail that cries wolf gets "
            "ignored, which is how the crash went unnoticed")
        self.assertEqual(picker.cabinet_count, allowed)

    def test_03_switching_shape_resets_the_count_without_warning(self):
        if not self.template:
            self.skipTest("no active kitchen template seeded")

        picker = self._picker()
        picker.last_seen_template_id = False    # simulate a fresh shape pick
        result = picker._onchange_parametrics()

        self.assertFalse(result, "picking a shape is not an error condition")
        self.assertEqual(
            picker.cabinet_count, picker.count_max,
            "a newly picked shape seeds the count from its own capacity")

    def test_04_every_warning_branch_can_build_its_message(self):
        """Broader guard: exercise the onchange across a sweep of counts on
        every active template, and assert none of them raise. A missing
        import in a message is invisible until the exact branch runs, so the
        cheapest protection is to run them all."""
        templates = self.env["southbrook.kitchen.template"].search(
            [("active", "=", True)])
        if not templates:
            self.skipTest("no active kitchen templates seeded")

        for template in templates:
            for count in (0, 1, 2, 8, 25, 99, 500):
                picker = self.Picker.new({
                    "template_id": template.id,
                    "cabinet_count": count,
                })
                picker.last_seen_template_id = picker.template_id
                try:
                    picker._onchange_parametrics()
                except Exception as exc:        # noqa: BLE001
                    self.fail(
                        f"_onchange_parametrics raised {type(exc).__name__} "
                        f"for template {template.display_name!r} with "
                        f"cabinet_count={count}: {exc}")
