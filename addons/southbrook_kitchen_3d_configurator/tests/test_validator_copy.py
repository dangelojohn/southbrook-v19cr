# SPDX-License-Identifier: LGPL-3.0-only
"""Copy review (2026-07-31) — regression pins for the `_check_production_
ready` message rewrite and the `kitchen.design.template.picker.
fit_summary` happy-path fix.

This is a copy-only change: no validator LOGIC, severity, or issue
`code` moved. These tests deliberately do NOT assertEqual any message
string — brittle string-equality tests on copy are a maintenance trap.
Instead they pin the INTENT the review's findings care about:

  - no message leaks an internal identifier (raw enum value, field
    name, or a key=value coordinate dump) that only makes sense to
    someone who has read the code;
  - every blocking issue's message is non-empty;
  - MOTION_ENVELOPE_COLLISION prefers the placement rule's human name
    over its internal taxonomy slug (Section B precedence fix);
  - UNRESOLVED_SLOT names the wall the empty spot is on;
  - CABINET_COLLISION shows the Zone field's own label, not the raw
    stored Selection value;
  - fit_summary's happy path renders nothing (redundant with the
    cabinet_count/count_max fields next to it); the failure path still
    carries its real, useful message.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E

MM = 25.4

# Copy review 2026-07-31 — internal vocabulary that must never leak into
# a message an end user reads: field/attribute names, raw Selection
# values, and key=value coordinate-dump fragments the old copy used to
# print verbatim (FOOTPRINT_COLLISION's "wall=%s, x=%.1f", z=%.1f",
# rot=%d°" being the worst offender).
_FORBIDDEN_TOKENS = (
    "run_seq", "layout_role", "template_slot_code", "base_run",
    "layout_key", "rot=", "x=", "z=",
)


def _assert_no_internal_tokens(test, issues):
    for issue in issues:
        message = issue["message"]
        test.assertTrue(
            message and message.strip(),
            "issue %s has an empty message" % issue.get("code"))
        for token in _FORBIDDEN_TOKENS:
            test.assertNotIn(
                token, message,
                "issue %s message leaked internal token %r: %s"
                % (issue.get("code"), token, message))


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "validator_copy")
class TestValidatorMessageTokenHygiene(TransactionCase):
    """One broad fixture exercising most of the geometry/data checks in
    `_check_production_ready` at once, scanned for leaked internal
    vocabulary. Extra issue codes beyond the ones explicitly asserted
    (e.g. MISSING_BOM/BOM_WILL_AUTOSEED, since none of these test
    products carry a BOM) are expected and harmless — the scan covers
    every issue the design actually produces, not just the named ones.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["product.product"]
        cls.base_a = P.create(
            {"name": "VC Base A", "default_code": "ZZ-VC-BASE-A",
             "list_price": 200.0})
        cls.base_b = P.create(
            {"name": "VC Base B", "default_code": "ZZ-VC-BASE-B",
             "list_price": 200.0})
        cls.wall_c = P.create(
            {"name": "VC Wall C", "default_code": "ZZ-VC-WALL-C",
             "list_price": 150.0})
        cls.tall_d = P.create(
            {"name": "VC Tall D", "default_code": "ZZ-VC-TALL-D",
             "list_price": 400.0})
        cls.filler_e = P.create(
            {"name": "VC Filler E", "default_code": "ZZ-VC-FILLER-E",
             "list_price": 40.0})
        cls.oob_f = P.create(
            {"name": "VC OOB F", "default_code": "ZZ-VC-OOB-F",
             "list_price": 200.0})
        cls.panel_g = P.create(
            {"name": "VC Panel G", "default_code": "ZZ-VC-PANEL-G",
             "list_price": 60.0})
        cls.wide_h = P.create(
            {"name": "VC Wide H", "default_code": "ZZ-VC-WIDE-H",
             "list_price": 500.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "Validator Copy Token Test"})

    def _make_design(self):
        return self.env["southbrook.kitchen.design"].create({
            "name": "Validator copy — token hygiene",
            "partner_id": self.partner.id,
            "room_width_in": 120.0, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })

    def test_no_internal_tokens_across_a_broad_issue_set(self):
        design = self._make_design()
        Line = self.env["southbrook.kitchen.design.line"]

        # FOOTPRINT_COLLISION — two base cabinets, same layer, real
        # solid overlap (19" of x-overlap).
        Line.create({
            "design_id": design.id, "product_id": self.base_a.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "back",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 0.0, "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator", "layout_key": "vc-fp-a",
        })
        Line.create({
            "design_id": design.id, "product_id": self.base_b.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "back",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 5.0, "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 1, "origin": "configurator", "layout_key": "vc-fp-b",
        })

        # VERTICAL_COLLISION — a wall cabinet's bottom sits inside a
        # tall cabinet's full vertical span, same x-range.
        Line.create({
            "design_id": design.id, "product_id": self.wall_c.id,
            "quantity": 1, "price_unit": 150.0, "cabinet_type": "wall",
            "zone": "wall", "wall": "back",
            "width_in": 24.0, "height_in": 30.0, "depth_in": 12.0,
            "x_position_in": 50.0, "y_position_in": 54.0,
            "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vc-vert-wall",
        })
        Line.create({
            "design_id": design.id, "product_id": self.tall_d.id,
            "quantity": 1, "price_unit": 400.0, "cabinet_type": "tall",
            "zone": "tall", "wall": "back",
            "width_in": 24.0, "height_in": 84.0, "depth_in": 24.0,
            "x_position_in": 50.0, "y_position_in": 0.0,
            "z_position_in": 40.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vc-vert-tall",
        })

        # ORPHAN_FILLER — isolated filler on an otherwise-empty wall.
        Line.create({
            "design_id": design.id, "product_id": self.filler_e.id,
            "quantity": 1, "price_unit": 40.0, "cabinet_type": "filler",
            "zone": "accessory", "wall": "front",
            "width_in": 6.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 0.0, "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator", "layout_key": "vc-orphan",
        })

        # OUT_OF_ROOM_BOUNDS — parked far outside the room (prod-debris
        # shape, matches test_validator_repair.py's design-99 fixture).
        Line.create({
            "design_id": design.id, "product_id": self.oob_f.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "left",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 100000.0, "z_position_in": 100000.0,
            "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator", "layout_key": "vc-oob",
        })

        # PANEL_WALL_ATTACH — panel far from every base/wall host's
        # side face.
        Line.create({
            "design_id": design.id, "product_id": self.panel_g.id,
            "quantity": 1, "price_unit": 60.0, "cabinet_type": "panel",
            "zone": "accessory", "wall": "front",
            "width_in": 18.0, "height_in": 34.5, "depth_in": 1.0,
            "x_position_in": 500.0, "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator", "layout_key": "vc-panel",
        })

        # UNREALISTIC_DIM + NON_STANDARD_WIDTH — an oversized,
        # non-modular single base cabinet.
        Line.create({
            "design_id": design.id, "product_id": self.wide_h.id,
            "quantity": 1, "price_unit": 500.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "right",
            "width_in": 90.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 10.0, "z_position_in": 10.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator", "layout_key": "vc-wide",
        })

        # UNRESOLVED_SLOT — a still-unresolved template placeholder.
        Line.create({
            "design_id": design.id, "product_id": self.base_a.id,
            "quantity": 1, "price_unit": 0.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "right",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 60.0, "z_position_in": 10.0, "rotation_deg": 0,
            "run_seq": 1, "origin": "configurator",
            "layout_key": "vc-unresolved",
            "is_unresolved": True, "template_slot_code": "M1",
        })

        issues = design._check_production_ready()
        codes = {i["code"] for i in issues}
        expected = {
            "FOOTPRINT_COLLISION", "VERTICAL_COLLISION", "ORPHAN_FILLER",
            "OUT_OF_ROOM_BOUNDS", "PANEL_WALL_ATTACH", "UNREALISTIC_DIM",
            "NON_STANDARD_WIDTH", "UNRESOLVED_SLOT",
        }
        self.assertTrue(
            expected.issubset(codes), (expected - codes, issues))

        _assert_no_internal_tokens(self, issues)

        blocking = [i for i in issues if i["severity"] == "blocking"]
        self.assertTrue(blocking)
        for issue in blocking:
            self.assertTrue(issue["message"].strip(), issue)

    def test_unresolved_slot_names_the_wall_not_the_raw_code(self):
        design = self._make_design()
        Line = self.env["southbrook.kitchen.design.line"]
        line = Line.create({
            "design_id": design.id, "product_id": self.base_a.id,
            "quantity": 1, "price_unit": 0.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "left",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vc-slot-wall",
            "is_unresolved": True, "template_slot_code": "M1",
        })
        issues = design._check_production_ready()
        hits = [i for i in issues if i["code"] == "UNRESOLVED_SLOT"]
        self.assertEqual(len(hits), 1, issues)
        message = hits[0]["message"]
        self.assertIn("left wall", message)
        # The raw slot code is not the leading identifier — Section B's
        # precedence fix: if it's shown at all, it trails the
        # wall-based sentence rather than opening the message.
        if "M1" in message:
            self.assertGreater(
                message.index("M1"), message.index("left wall"), message)
        self.assertIn("(line #%d)" % line.id, message)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "validator_copy")
class TestCabinetCollisionZoneLabel(TransactionCase):
    """CABINET_COLLISION used to print the raw stored zone VALUE
    ("base_run") instead of the label the Zone field itself displays
    ("Base Run") — the same "prefer the internal code" pattern the
    review flagged twice more (MOTION_ENVELOPE_COLLISION,
    UNRESOLVED_SLOT), just via a dict lookup miss rather than an `or`
    precedence bug."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_prod = cls.env["product.product"].create(
            {"name": "VC Zone Base", "default_code": "ZZ-VC-ZONE-BASE",
             "list_price": 200.0})
        cls.wall_prod = cls.env["product.product"].create(
            {"name": "VC Zone Wall", "default_code": "ZZ-VC-ZONE-WALL",
             "list_price": 150.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "Validator Copy Zone Test"})

    def test_zone_label_not_raw_value(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "Validator copy — zone label",
            "partner_id": self.partner.id,
            "room_width_in": 120.0, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })
        Line = self.env["southbrook.kitchen.design.line"]
        # A base cabinet and a WALL-typed cabinet, both manually tagged
        # zone="base_run" and placed to solidly overlap. A wall-typed
        # line is ALWAYS footprint-layer "wall" regardless of its own
        # zone field, so this pair has DIFFERENT layers — check #7's
        # same-layer dedupe does not apply, and check #3
        # (CABINET_COLLISION) fires on its own.
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "back",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 0.0, "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator", "layout_key": "vc-zone-a",
        })
        Line.create({
            "design_id": design.id, "product_id": self.wall_prod.id,
            "quantity": 1, "price_unit": 150.0, "cabinet_type": "wall",
            "zone": "base_run", "wall": "back",
            "width_in": 24.0, "height_in": 30.0, "depth_in": 12.0,
            "x_position_in": 0.0, "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator", "layout_key": "vc-zone-b",
        })
        issues = design._check_production_ready()
        hits = [i for i in issues if i["code"] == "CABINET_COLLISION"]
        self.assertEqual(len(hits), 1, issues)
        message = hits[0]["message"]
        self.assertIn("Base Run", message)
        self.assertNotIn("base_run", message)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "validator_copy")
class TestMotionEnvelopeMessagePrefersHumanName(TransactionCase):
    """Section B precedence fix — MOTION_ENVELOPE_COLLISION used to read
    `rr.corner_type_id or rr.name`, which ALWAYS won with the internal
    taxonomy slug since every seeded placement rule sets corner_type_id.
    Pins that the rule's own human-authored name now wins, mirroring
    test_collision_matrix.py's motion-envelope fixture shape."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Template = cls.env["product.template"]
        cls.corner_tmpl = Template.search(
            [("default_code", "=", "SB-CORNER")], limit=1)
        if not cls.corner_tmpl:
            cls.corner_tmpl = Template.create(
                {"name": "Corner Base", "default_code": "SB-CORNER",
                 "list_price": 625.0})
        cls.base_prod = cls.env["product.product"].create(
            {"name": "VC Motion Base", "default_code": "ZZ-VC-MOTION",
             "list_price": 200.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "Validator Copy Motion Test"})

    def test_message_prefers_rule_name_over_slug(self):
        rule = self.env["southbrook.placement.rule"].create({
            "name": "Diagonal Susan Base Corner 36x36",
            "product_tmpl_id": self.corner_tmpl.id,
            "corner_type_id": "diagonal-corner-lazy-susan-vc-test",
            "anchor_class": "junction", "tier": "base", "sequence": 1,
            "payload": {
                "leg_x_mm": 914.0, "leg_z_mm": 914.0, "height_mm": 876.0,
                "min_leg_x_mm": 914.0, "min_leg_z_mm": 914.0,
                "clearance_front_mm": 254.0,   # 10in
            },
        })
        design = self.env["southbrook.kitchen.design"].create({
            "name": "Validator copy — motion envelope",
            "partner_id": self.partner.id,
            "room_width_in": 200.0, "room_depth_in": 200.0,
            "room_height_in": 96.0, "state": "draft",
        })
        Line = self.env["southbrook.kitchen.design.line"]
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "wall": "back", "run_seq": 0, "origin": "configurator",
            "layout_key": "vc-motion-back-0",
        })
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "wall": "left", "run_seq": 0, "origin": "configurator",
            "layout_key": "vc-motion-left-0",
        })
        design.action_auto_arrange()

        corner_line = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(len(corner_line), 1, corner_line)
        # Corner-orientation fix (2026-07-31): back-left presents at
        # rotation_deg=0 (kitchen_layout_engine.ROOM_CORNER_PLACEMENT),
        # was 90 pre-fix. The envelope box below is computed dynamically
        # from corner_line's ACTUAL rotation, so nothing else in this
        # test depends on the specific value.
        self.assertEqual(corner_line.rotation_deg, 0)

        # Compute the SAME envelope box the validator computes, so the
        # planted intruder's position is geometrically guaranteed
        # correct rather than hand-derived.
        cab = {"width_mm": corner_line.width_in * MM,
               "depth_mm": corner_line.depth_in * MM}
        place = {"x": corner_line.x_position_in * MM,
                 "z": corner_line.z_position_in * MM,
                 "rotation_deg": corner_line.rotation_deg}
        ex0, ex1, ez0, ez1 = E.motion_envelope_from_anchor_mm(
            cab, place, 254.0)
        plant_x0_mm = ex0 + (ex1 - ex0) * 0.25
        plant_z0_mm = ez0 + (ez1 - ez0) * 0.25
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": (ex1 - ex0) * 0.5 / MM, "height_in": 34.5,
            "depth_in": (ez1 - ez0) * 0.5 / MM,
            "x_position_in": plant_x0_mm / MM,
            "z_position_in": plant_z0_mm / MM,
            "rotation_deg": 0, "wall": "back", "run_seq": -1,
            "origin": "configurator", "layout_key": "vc-motion-plant",
        })

        issues = design._check_production_ready()
        hits = [i for i in issues if i["code"] == "MOTION_ENVELOPE_COLLISION"]
        self.assertEqual(len(hits), 1, issues)
        message = hits[0]["message"]
        self.assertIn(rule.name, message)
        self.assertNotIn(rule.corner_type_id, message)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "validator_copy")
class TestFitSummaryHappyPath(TransactionCase):
    """Section D — on success, `kitchen.design.template.picker.
    fit_summary` used to restate cabinet_count/count_max, both already
    shown a few fields above it on the same form
    (views/kitchen_design_views.xml). The happy path now renders
    nothing; the failure path (real information: why it doesn't fit)
    is unchanged."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tpl = cls.env["southbrook.kitchen.template"]
        Slot = cls.env["southbrook.kitchen.template.line"]
        base = cls.env.ref("southbrook_estimating.base_2dr")
        sink = cls.env.ref("southbrook_estimating.sink_base")
        cls.tpl = Tpl.create({
            "name": "Validator Copy Fit Summary 10ft", "code": "VC-FIT-10",
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
        ])

    def test_success_renders_nothing(self):
        # SBK-052 — the appliance selectors default to a range, a fridge
        # and a dishwasher, and this fixture template authors no appliance
        # slot at all, so all three would now (correctly) be reported as
        # unseatable. That notice is real information, not the restatement
        # of cabinet_count/count_max this test was written to forbid — so
        # the happy path is asserted with nothing requested.
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id, "module_width_in": "24",
            "range_width_in": "0", "fridge_width_in": "0",
            "dishwasher_width_in": "0",
        })
        self.assertGreater(picker.count_max, 0)
        self.assertFalse(picker.fit_summary)

    def test_success_still_reports_an_appliance_it_cannot_seat(self):
        """SBK-052 — a fit can succeed and still lose the designer their
        refrigerator. This template has no appliance slots, so a requested
        fridge is never looked up by any code path; before this it was
        discarded in silence and the chatter said "0 dropped"."""
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id, "module_width_in": "24",
            "range_width_in": "0", "fridge_width_in": "36",
            "dishwasher_width_in": "0",
        })
        self.assertGreater(picker.count_max, 0)
        self.assertTrue(
            picker.fit_summary,
            "an appliance that cannot be seated must be reported even when "
            "the layout itself fits")
        self.assertIn("refrigerator", picker.fit_summary)

    def test_failure_still_carries_the_real_message(self):
        picker = self.env["kitchen.design.template.picker"].create({
            "template_id": self.tpl.id, "module_width_in": "24",
            "cabinet_count": 9999,
        })
        self.assertGreater(picker.count_max, 0)
        self.assertTrue(picker.fit_summary)
        self.assertIn("cannot fit", picker.fit_summary)
