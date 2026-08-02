# SPDX-License-Identifier: LGPL-3.0-only
"""Acceptance tests for wiring the ECO-governed southbrook.cut.spec into
the ACTUAL manufacturing cutlist path (2026-08).

Before this pass, southbrook.cut.spec was reachable via the
mrp.bom._get_cut_constants() seam (southbrook_estimating, overridden by
southbrook_plm) but the real cutlist that feeds nesting/CAM/HOMAG
(sb_production_package.generate_from_mo -> shared.southbrook_dims.
panel_cut_list -> sb_cutlist.generate_lines_from_panel_dict) never called
that seam at all — it read shared/southbrook_dims.py's own module-level
constants unconditionally, and sb_cutlist.py additionally hardcoded
door_reveal/door_th inline for the drawer-front branch. An ECO-approved
cut-spec revision therefore never reached the geometry that actually goes
to the saw.

This file proves the fix is SAFE (parity), EFFECTIVE (the override changes
output by exactly the formula-predicted amount), and does what it exists
to do (an as-sold pin survives a later ECO), without regressing any
pre-existing, not-yet-rewired call site (legacy/no-PLM safety).

Tagged to run with a plain ``-u southbrook_kitchen_mrp`` — this addon does
NOT depend on southbrook_plm (see sb_production_package.py's
_resolve_cut_spec docstring for why), so tests 1, 2 and 4 need nothing
beyond shared.southbrook_dims + this addon's own models. Test 3 needs
southbrook_plm's ECO/cut-spec machinery and skips cleanly (not a false
pass) when that addon is not installed in the target database.
"""
from odoo.tests import TransactionCase, new_test_user, tagged

from southbrook_dims import (
    BACK_TH,
    BOX_TH,
    DOOR_REVEAL,
    DOOR_TH,
    RABBET,
    SHELF_TOL,
    SHELF_VENT_GAP,
    TOEKICK_H,
    panel_cut_list,
)

_MODULE_CONSTANTS = {
    "box_th": BOX_TH,
    "back_th": BACK_TH,
    "rabbet": RABBET,
    "door_th": DOOR_TH,
    "door_reveal": DOOR_REVEAL,
    "shelf_tol": SHELF_TOL,
    "shelf_vent_gap": SHELF_VENT_GAP,
    "toekick_h": TOEKICK_H,
}

# Representative geometries/families/door counts for the parity sweep —
# deliberately varied (1-door, 2-door, drawer-bank shape with door_count=0,
# a family without a toe-kick, a couple of shelf-count boundaries).
_PARITY_CASES = [
    (600, 720, 580, "base", 1),
    (600, 720, 580, "base", 2),
    (900, 900, 580, "wall", 2),
    (450, 900, 610, "tall", 1),
    (600, 720, 580, "base", 0),
    (600, 600, 580, "sink", 1),
    (760, 2100, 580, "tall", 2),
]


@tagged("post_install", "-at_install", "southbrook", "kitchen_mrp",
        "cut_spec_wiring")
class TestCutSpecWiring(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Same defensive opt-out other kitchen_mrp/PLM test suites use —
        # inert unless southbrook_mrp_pm happens to be installed alongside.
        cls.env = cls.env(context={
            **cls.env.context, "bypass_production_approval": True})
        cls.Package = cls.env["sb.production.package"]
        cls.Product = cls.env["product.product"]
        cls.MO = cls.env["mrp.production"]
        cls.Order = cls.env["sale.order"]
        cls.OrderLine = cls.env["sale.order.line"]
        cls.Partner = cls.env["res.partner"].create({
            "name": "Cut-Spec Wiring Test Partner",
        })

    # ==================================================================
    # 1. PARITY — the safety net. constants=None must equal
    #    constants=<today's exact module constants> for every case.
    # ==================================================================
    def test_parity_constants_none_vs_explicit_module_defaults(self):
        for width, height, depth, family, door_count in _PARITY_CASES:
            default = panel_cut_list(
                width, height, depth, family=family, door_count=door_count)
            explicit = panel_cut_list(
                width, height, depth, family=family, door_count=door_count,
                constants=dict(_MODULE_CONSTANTS))
            self.assertEqual(
                default, explicit,
                f"parity broken for width={width} height={height} "
                f"depth={depth} family={family} door_count={door_count}")

    def test_parity_partial_override_dict_falls_back_per_key(self):
        """A dict missing keys must fall back to the module constant for
        each missing key, not crash and not silently zero anything."""
        default = panel_cut_list(600, 720, 580, family="base", door_count=1)
        partial = panel_cut_list(
            600, 720, 580, family="base", door_count=1,
            constants={"box_th": _MODULE_CONSTANTS["box_th"]})
        self.assertEqual(default, partial)

    # ==================================================================
    # 2. OVERRIDE TAKES EFFECT — door_reveal 3.0 -> 4.0 must change the
    #    door panel by exactly the formula-predicted delta, computed
    #    independently here (not copied from shared/southbrook_dims.py).
    # ==================================================================
    def test_override_door_reveal_changes_door_dims_by_formula(self):
        overridden = dict(_MODULE_CONSTANTS)
        overridden["door_reveal"] = _MODULE_CONSTANTS["door_reveal"] + 1.0
        delta = overridden["door_reveal"] - _MODULE_CONSTANTS["door_reveal"]
        width, height, depth = 600, 720, 580

        # door_count == 1: door = (height - 2*reveal, width - 2*reveal, door_th)
        default_1 = panel_cut_list(
            width, height, depth, family="base", door_count=1)
        override_1 = panel_cut_list(
            width, height, depth, family="base", door_count=1,
            constants=overridden)
        self.assertAlmostEqual(
            override_1["door"][0], default_1["door"][0] - 2 * delta)
        self.assertAlmostEqual(
            override_1["door"][1], default_1["door"][1] - 2 * delta)
        self.assertEqual(
            override_1["door"][2], default_1["door"][2],
            "door thickness must be unaffected by a door_reveal-only override")

        # door_count == 2: door = (height - 2*reveal, (width - 3*reveal)/2, door_th)
        default_2 = panel_cut_list(
            width, height, depth, family="base", door_count=2)
        override_2 = panel_cut_list(
            width, height, depth, family="base", door_count=2,
            constants=overridden)
        self.assertAlmostEqual(
            override_2["door"][0], default_2["door"][0] - 2 * delta)
        self.assertAlmostEqual(
            override_2["door"][1], default_2["door"][1] - 1.5 * delta)

        # Panels that do not depend on door_reveal must be byte-identical.
        for key in ("side_L", "side_R", "top", "bottom", "back",
                    "adjustable_shelf"):
            self.assertEqual(
                override_1[key], default_1[key],
                f"{key} must be unaffected by a door_reveal-only override")

    # ==================================================================
    # 3. PIN IS HONOURED — the actual point of the change: an as-sold
    #    pin must survive a later ECO that activates a different spec.
    #    Requires southbrook_plm; skips cleanly when absent (kitchen_mrp
    #    does not depend on it).
    # ==================================================================
    def test_pin_is_honoured_over_later_active_spec(self):
        CutSpec = self.env.get("southbrook.cut.spec")
        if CutSpec is None:
            self.skipTest(
                "southbrook_plm not installed in this database — the "
                "pin-honouring path is inert without it by design "
                "(see sb.production.package._resolve_cut_spec).")
        try:
            type_cut = self.env.ref("southbrook_plm.eco_type_cut_spec")
        except ValueError:
            self.skipTest("southbrook_plm ECO seed data not loaded.")

        Eco = self.env["southbrook.eco"]
        approver = new_test_user(
            self.env, login="cut_spec_wiring_test_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )

        # 1. Confirm an order line while today's active spec is still
        #    active — southbrook_plm's action_confirm hook pins the line
        #    to it (southbrook_cut_spec_version_id).
        product = self.Product.create({
            "name": "Pin Wiring Test Cabinet", "type": "consu",
        })
        order = self.Order.create({"partner_id": self.Partner.id})
        line = self.OrderLine.create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 1.0,
        })
        if "production_approval_state" in order._fields:
            order.production_approval_state = "approved"
        order.action_confirm()
        pinned_spec = line.southbrook_cut_spec_version_id
        self.assertTrue(
            pinned_spec, "line must be pinned to a cut spec at confirm")
        pinned_reveal = pinned_spec.door_reveal

        # 2. NOW raise + approve + apply a cut-spec ECO that activates a
        #    DIFFERENT spec. This must not retroactively re-pin the
        #    already-confirmed line (southbrook_plm's own idempotency
        #    guard — asserted here as a precondition, not the point of
        #    this test).
        new_reveal = pinned_reveal + 2.0
        new_spec = CutSpec.create({
            "name": "Cut-spec wiring test — post-pin revision",
            "door_reveal": new_reveal,
        })
        eco = Eco.create({
            "title": "Cut-spec wiring test ECO",
            "eco_type_id": type_cut.id,
            "cut_spec_id": new_spec.id,
        })
        eco.with_user(approver).action_approve()
        eco.with_user(approver).action_apply()
        self.assertEqual(new_spec.state, "active")
        self.assertNotEqual(
            line.southbrook_cut_spec_version_id, new_spec,
            "confirming an order must not be re-triggered by a later ECO "
            "— this is a precondition for the test below, not its point")

        # 3. Manufacture: build the production package from the pinned
        #    line. MO wired up by hand exactly like
        #    test_build_from_order_line.py's own fixture (no BoM
        #    required for generate_from_mo).
        mo = self.MO.create({
            "product_id": product.id,
            "product_qty": 1.0,
            "sale_line_id": line.id,
            "origin": order.name,
        })
        package = self.Package.build_from_order_line(line, mo=mo)
        self.assertTrue(package, "expected a production package")
        self.assertEqual(
            package.cutlist_id.cut_spec_used_id, pinned_spec.id,
            "cutlist traceability must record the PINNED spec, not the "
            "later-activated one")

        door_line = package.cutlist_id.line_ids.filtered(
            lambda l: l.panel_name == "door")
        self.assertTrue(door_line, "door panel expected for this cabinet")

        # Expected geometry from the PINNED reveal, computed from the
        # formula directly. This cabinet has no configured attributes, so
        # sb.production.package._resolve_dims_from_order_line's fallback
        # defaults apply: width_mm=600 (width>540 -> door_count=2),
        # height_mm=720 (the _DEFAULT_HEIGHT_MM fallback).
        width_mm, height_mm = 600.0, 720.0
        expected_length = height_mm - 2 * pinned_reveal
        expected_width = (width_mm - 3 * pinned_reveal) / 2
        self.assertAlmostEqual(
            door_line[0].length_mm, expected_length, delta=0.01)
        self.assertAlmostEqual(
            door_line[0].width_mm, expected_width, delta=0.01)

        # And explicitly NOT the later-activated spec's reveal.
        wrong_length = height_mm - 2 * new_reveal
        self.assertNotAlmostEqual(
            door_line[0].length_mm, wrong_length, delta=0.01,
            msg="cutlist must not have used the newly-active spec")

    # ==================================================================
    # 4. NO-PLM / LEGACY SAFETY — pre-existing, not-explicitly-rewired
    #    call sites must resolve no spec and keep producing exactly
    #    today's module-default geometry.
    # ==================================================================
    def test_resolve_cut_spec_empty_with_nothing_to_resolve_from(self):
        """Resolution order item 4 — no order_line, no mo: falsy,
        unconditionally, regardless of what southbrook_plm data exists
        elsewhere in this database."""
        self.assertFalse(self.Package._resolve_cut_spec())

    def test_legacy_mo_with_no_sale_line_resolves_nothing_and_matches_defaults(self):
        """An MO with no linked sale.order.line (the shape of every
        pre-existing generate_from_mo() call site — see
        test_production_package.py, test_dealer_acl.py,
        test_kd_export.py) must resolve no cut spec, and the emitted
        cutlist must match shared.southbrook_dims' module-default output
        exactly — the legacy path, byte-identical."""
        product = self.Product.create({
            "name": "Legacy Path Test Cabinet", "type": "consu",
            "is_storable": True,
        })
        self.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0,
        })
        mo = self.MO.create({"product_id": product.id, "product_qty": 1.0})

        self.assertFalse(
            self.Package._resolve_cut_spec(mo=mo),
            "an MO with no linked sale.order.line must resolve no cut spec")

        package = self.Package.generate_from_mo(
            mo, width_mm=600, height_mm=720, depth_mm=580,
            cabinet_family="base", door_count=2, drawer_count=0,
        )
        expected = panel_cut_list(600, 720, 580, family="base", door_count=2)
        emitted = {
            ln.panel_name: (ln.length_mm, ln.width_mm, ln.thickness_mm)
            for ln in package.cutlist_id.line_ids
        }
        for key in ("side_L", "side_R", "top", "bottom", "back", "door"):
            self.assertEqual(
                tuple(round(v, 3) for v in emitted[key]),
                tuple(round(v, 3) for v in expected[key]),
                f"legacy-path {key} panel must match module defaults exactly")

        # No spec was resolved, so the traceability snapshot stays empty.
        self.assertFalse(package.cutlist_id.cut_spec_used_id)
        self.assertFalse(package.cutlist_id.cut_spec_used_name)
