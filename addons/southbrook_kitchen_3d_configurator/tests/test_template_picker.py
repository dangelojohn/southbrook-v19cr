# SPDX-License-Identifier: LGPL-3.0-only
"""Templates T3 — the four-dropdown picker + thumbnails + compat."""
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestTemplatePicker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tpl = cls.env["southbrook.kitchen.template"]
        Slot = cls.env["southbrook.kitchen.template.line"]
        base = cls.env.ref("southbrook_estimating.base_2dr")
        sink = cls.env.ref("southbrook_estimating.sink_base")
        cls.tpl = Tpl.create({
            "name": "T3 Picker 10ft", "code": "T3-SW-10",
            "layout_shape": "straight",
            "default_room_width_in": 120.0, "default_room_depth_in": 96.0,
            "default_room_height_in": 96.0, "default_module_width_in": 24.0,
        })
        Slot.create([
            {"template_id": cls.tpl.id, "slot_code": "SINK",
             "cabinet_type": "base", "wall": "back", "run_seq": 10,
             "product_id": sink.product_variant_id.id},
            {"template_id": cls.tpl.id, "slot_code": "B1",
             "cabinet_type": "base", "wall": "back", "run_seq": 20,
             "repeat_ok": True, "product_id": base.product_variant_id.id},
            {"template_id": cls.tpl.id, "slot_code": "RANGE",
             "cabinet_type": "appliance", "appliance_type": "range",
             "wall": "back", "run_seq": 30, "nominal_width_in": 30.0},
        ])

    def test_confirm_instantiates_and_opens_configurator(self):
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id,
            "module_width_in": "24",
            "range_width_in": "36",
        })
        action = picker.action_create()
        # action_open_configurator returns the client action for the canvas
        self.assertEqual(action.get("tag"), "southbrook_kitchen_configurator")
        design = self.env["southbrook.kitchen.design"].search(
            [], order="id desc", limit=1)
        self.assertTrue(design.cabinet_line_ids)
        appl = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "appliance")
        self.assertEqual(appl.width_in, 36.0)

    def test_count_is_live_bounded(self):
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id, "module_width_in": "24"})
        self.assertGreater(picker.count_max, 0)
        picker.cabinet_count = picker.count_max + 5
        picker._onchange_parametrics()
        self.assertLessEqual(picker.cabinet_count, picker.count_max)

    def test_count_seeds_to_max_when_unset(self):
        """2026-07-31 — the field only clamped DOWNWARD, so it stayed 0
        in the UI while the helper text beside it read "N cabinet(s) ...
        (max N)". 0 was never wrong for the generator (it means "template
        default"), but the form showed a number that wasn't the one used.
        """
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id, "module_width_in": "24"})
        self.assertEqual(picker.cabinet_count, 0)
        picker._onchange_parametrics()
        self.assertGreater(picker.count_max, 0)
        self.assertEqual(picker.cabinet_count, picker.count_max)

    def test_count_seeding_never_overrides_a_real_choice(self):
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id, "module_width_in": "24"})
        picker._onchange_parametrics()
        chosen = max(1, picker.count_max - 1)
        picker.cabinet_count = chosen
        picker._onchange_parametrics()
        self.assertEqual(picker.cabinet_count, chosen)

    def test_shape_switch_resets_count_to_new_shape_max(self):
        """Task 24 (SBK-038) — Number of Cabinets is authoritative:
        picking a NEW shape must show THAT shape's own Max, never a
        stale count that merely still fits under whatever shape was
        selected before. Uses Form() (not a bare .create()/manual
        onchange call) because the defect only shows up through the
        real onchange machinery's `_origin` semantics — the other tests
        in this file call `_onchange_parametrics()` directly on an
        already-saved record, which never exercises a genuine
        "value changed from A to B" transition.
        """
        Tpl = self.env["southbrook.kitchen.template"]
        Slot = self.env["southbrook.kitchen.template.line"]
        base = self.env.ref("southbrook_estimating.base_2dr")
        sink = self.env.ref("southbrook_estimating.sink_base")
        big = Tpl.create({
            "name": "T24 Picker 14ft", "code": "T24-SW-14",
            "layout_shape": "straight",
            "default_room_width_in": 168.0, "default_room_depth_in": 96.0,
            "default_room_height_in": 96.0, "default_module_width_in": 24.0,
        })
        Slot.create([
            {"template_id": big.id, "slot_code": "SINK",
             "cabinet_type": "base", "wall": "back", "run_seq": 10,
             "product_id": sink.product_variant_id.id},
            {"template_id": big.id, "slot_code": "B1",
             "cabinet_type": "base", "wall": "back", "run_seq": 20,
             "repeat_ok": True, "product_id": base.product_variant_id.id},
        ])
        small_max = self.tpl.parametric_fit(module_width_in=24.0)["n_max"]
        big_max = big.parametric_fit(module_width_in=24.0)["n_max"]
        self.assertNotEqual(
            small_max, big_max,
            "fixture templates must differ in capacity for this test "
            "to mean anything")

        with Form(self.env["kitchen.design.template.picker"]) as f:
            f.template_id = self.tpl
            self.assertEqual(f.cabinet_count, small_max)
            f.template_id = big
            self.assertEqual(
                f.cabinet_count, big_max,
                "selecting a new shape must reset the count to THAT "
                "shape's own max, not leave the old shape's count in "
                "place")

    def test_cabinet_count_help_text_matches_actual_default_behavior(self):
        """2026-07-31 — the field's help text still said "Starts at the
        template's own default", describing the PRE-fix behavior (the
        natural, non-max count `action_create` falls back to only when
        the field is left at 0). Selecting a shape now seeds Number of
        Cabinets to the template's MAX instead (see
        test_count_seeds_to_max_when_unset) — a different, usually
        larger, number. Stale copy describing behavior the code no
        longer has."""
        help_text = self.env["kitchen.design.template.picker"]._fields[
            "cabinet_count"].help
        self.assertNotIn(
            "template's own default", help_text,
            "help text still describes the pre-Task-24 default "
            "behavior")
        self.assertIn("maximum", help_text.lower())

    def test_cabinet_size_flows_into_repeat_slot_width(self):
        """Task 24 (SBK-038) — Cabinet Size must be honoured in the
        widths of the generated (repeatable) module lines, not silently
        pinned to the wizard's 24in default. The underlying mechanism
        is already covered one layer down by
        test_template_instantiate.py::test_module_width_parametric;
        this closes the same gap at the picker itself, since the wizard
        is a separate, independently-editable surface over the same
        `action_instantiate` call.
        """
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id, "module_width_in": "30",
            "range_width_in": "30", "cabinet_count": 2})
        picker.action_create()
        design = self.env["southbrook.kitchen.design"].search(
            [], order="id desc", limit=1)
        b1 = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "B1")
        self.assertTrue(b1, "the repeat slot must have produced a line")
        self.assertEqual(b1.width_in, 30.0)

    def test_empty_preset_creates_plain_draft(self):
        # legacy-compat: 'empty' = plain draft design, no template consulted
        picker = self.env["kitchen.design.template.picker"].create(
            {"preset": "empty"})
        action = picker.action_create()
        self.assertEqual(action.get("tag"), "southbrook_kitchen_configurator")
        design = self.env["southbrook.kitchen.design"].search(
            [], order="id desc", limit=1)
        self.assertFalse(design.cabinet_line_ids)
        self.assertEqual(design.state, "draft")

    def test_24in_range_option_instantiates(self):
        # 2026-07-27 (John): 24" apartment-size range was missing from
        # the appliance dropdown. Pin: option exists and flows through
        # appliance_widths to the design line.
        field = self.env["kitchen.design.template.picker"]._fields[
            "range_width_in"]
        self.assertIn("24", [k for k, _ in field.selection])
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id,
            "module_width_in": "24",
            "range_width_in": "24",
        })
        picker.action_create()
        design = self.env["southbrook.kitchen.design"].search(
            [], order="id desc", limit=1)
        appl = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "appliance")
        self.assertEqual(appl.width_in, 24.0)

    def test_thumbnail_svg_generated(self):
        svg = self.tpl._generate_thumbnail_svg()
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("<rect", svg)

    def test_thumbnail_svg_is_well_formed_and_escapes_the_template_name(self):
        """The field renders with sanitize=False, and the template NAME
        is user-authored — the one place user text enters this markup.
        An unescaped quote or angle bracket would break out of the
        aria-label attribute."""
        import xml.etree.ElementTree as ET
        hostile = self.env["southbrook.kitchen.template"].create({
            "name": 'Bad " <script>x</script> & name',
            "code": "T3-ESCAPE", "layout_shape": "straight",
            "default_room_width_in": 120.0, "default_room_depth_in": 96.0,
            "default_room_height_in": 96.0, "default_module_width_in": 24.0,
        })
        svg = hostile._generate_thumbnail_svg()
        ET.fromstring(svg)                      # raises if malformed
        self.assertNotIn("<script>", svg)

    def test_thumbnail_svg_is_labelled_for_screen_readers(self):
        # The plan is informational, not decorative: it must say what it
        # shows rather than presenting as an unlabelled blob.
        svg = self.tpl._generate_thumbnail_svg()
        self.assertIn('role="img"', svg.split(">", 1)[0])
        self.assertIn("aria-label=", svg.split(">", 1)[0])
        self.assertIn("<title>", svg)

    def test_thumbnail_svg_shows_wall_cabinets_instead_of_dropping_them(self):
        """Wall cabinets used to be skipped entirely, so the plan
        silently omitted part of the kitchen. They now read as dashed
        outlines — above the floor run on a top view, but present."""
        self.env["southbrook.kitchen.template.line"].create({
            "template_id": self.tpl.id, "slot_code": "W1",
            "cabinet_type": "wall", "wall": "back", "run_seq": 40,
            "nominal_width_in": 24.0,
        })
        self.tpl.invalidate_recordset()
        svg = self.tpl._generate_thumbnail_svg()
        self.assertIn("stroke-dasharray", svg)

    def test_thumbnail_svg_root_carries_intrinsic_size(self):
        """2026-07-31 — the PREVIEW panel showed a heading and no body.

        The markup was always generated (and the field is sanitize=False,
        so nothing stripped it); the root just had no width/height, so
        the inline <svg> had no intrinsic size and collapsed to zero
        height in the form container. Pin both dimensions AND the
        viewBox — the viewBox alone is what regressed.
        """
        svg = self.tpl._generate_thumbnail_svg()
        root = svg.split(">", 1)[0]
        self.assertIn("width=", root)
        self.assertIn("height=", root)
        self.assertIn("viewBox=", root)
