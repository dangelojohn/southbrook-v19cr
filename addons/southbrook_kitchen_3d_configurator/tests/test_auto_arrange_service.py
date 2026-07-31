# SPDX-License-Identifier: LGPL-3.0-only
"""Persistence-level invariants for design.action_auto_arrange().

Auto-arrange is a pure transformation of the customer's CANONICAL selection
into a derived arranged layout. These tests pin the lifecycle guarantees the
geometry tests can't see: idempotence, no canonical loss, no derived
accumulation, atomic rollback, and a mirror that reflects only visible lines.
"""
from unittest.mock import patch

from odoo.tests.common import TransactionCase
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E


class TestAutoArrangeService(TransactionCase):

    def setUp(self):
        super().setUp()
        Template = self.env["product.template"]
        for code, name, price in [("SB-CORNER", "Corner Base", 625.0),
                                  ("SB-WALL-CORNER", "Corner Wall", 295.0)]:
            if not Template.search([("default_code", "=", code)], limit=1):
                Template.create({"name": name, "default_code": code,
                                 "list_price": price})
        self.base_prod = self.env["product.product"].create(
            {"name": "Test Base 24", "default_code": "ZZ-TEST-B24",
             "list_price": 200.0})
        self.partner = self.env["res.partner"].create({"name": "AA Test"})

    def _make_design(self, n, width_in=24.0, room_w=140.0, room_d=200.0):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "AA test", "partner_id": self.partner.id,
            "room_width_in": room_w, "room_depth_in": room_d,
            "room_height_in": 96.0, "state": "draft"})
        Line = self.env["southbrook.kitchen.design.line"]
        for i in range(n):
            Line.create({
                "design_id": design.id, "product_id": self.base_prod.id,
                "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
                "width_in": width_in, "height_in": 34.5, "depth_in": 24.0,
                "sequence": i * 10, "origin": "configurator",
                "layout_key": "aa-%d" % i})
        return design

    def _active_snapshot(self, design):
        return sorted(
            (l.layout_key, l.product_id.default_code, round(l.x_position_in, 2),
             round(l.z_position_in, 2), l.rotation_deg, l.wall, l.layout_role)
            for l in design.cabinet_line_ids)

    def _all_lines(self, design):
        return design.with_context(active_test=False).cabinet_line_ids

    def test_first_run_produces_L(self):
        d = self._make_design(10)
        res = d.action_auto_arrange()
        self.assertEqual(res["inserted"], 1)
        self.assertEqual(res["removed"], 2)
        corners = d.cabinet_line_ids.filtered(lambda l: l.layout_role == "derived")
        self.assertEqual(len(corners), 1)
        self.assertEqual(corners.product_id.product_tmpl_id.default_code,
                         "SB-CORNER")

    def test_idempotent_active_lines(self):
        d = self._make_design(10)
        d.action_auto_arrange()
        snap = self._active_snapshot(d)
        for _ in range(4):                 # run 4 more times → identical
            d.action_auto_arrange()
            self.assertEqual(self._active_snapshot(d), snap)

    def test_active_count_stable_after_second_run(self):
        d = self._make_design(10)
        d.action_auto_arrange()
        n1 = len(d.cabinet_line_ids)
        d.action_auto_arrange()
        self.assertEqual(len(d.cabinet_line_ids), n1)   # 8 canonical + 1 corner

    def _canonical_intrinsic(self, design):
        """Identity + intrinsic properties of the canonical selection —
        excludes derived arrangement state (position/rotation/active) and
        metadata (write timestamps). This is what MUST NOT change."""
        return sorted(
            (l.layout_key, l.product_id.id, round(l.width_in, 3),
             round(l.height_in, 3), round(l.depth_in, 3), l.cabinet_type,
             l.origin, l.quantity)
            for l in self._all_lines(design).filtered(
                lambda l: l.layout_role == "canonical"))

    def test_canonical_integrity_across_cycles(self):
        # Compile → reset → compile … the customer's canonical selection is
        # byte-for-byte unchanged (catches accidental mutation of the source
        # records, distinct from output stability).
        d = self._make_design(10)
        original = self._canonical_intrinsic(d)
        self.assertEqual(len(original), 10)
        for _ in range(5):
            d.action_auto_arrange()
            self.assertEqual(self._canonical_intrinsic(d), original)

    def test_no_canonical_line_is_ever_lost(self):
        d = self._make_design(10)
        canon = lambda: len(self._all_lines(d).filtered(
            lambda l: l.layout_role == "canonical"))
        self.assertEqual(canon(), 10)
        for _ in range(3):
            d.action_auto_arrange()
            self.assertEqual(canon(), 10)   # superseded, never deleted

    def test_derived_artifacts_never_accumulate(self):
        d = self._make_design(10)
        for _ in range(5):
            d.action_auto_arrange()
            derived = self._all_lines(d).filtered(
                lambda l: l.layout_role == "derived")
            self.assertEqual(len(derived), 1)   # exactly one, every run

    def test_mirror_reflects_only_visible_lines(self):
        d = self._make_design(10)
        d.action_auto_arrange()
        d.action_auto_arrange()             # twice — catch mirror duplication
        self.assertTrue(d.sale_order_id)
        so_lines = d.sale_order_id.order_line
        # exactly one corner SO line (no duplication across runs)
        corner_so = so_lines.filtered(
            lambda l: l.product_id.product_tmpl_id.default_code == "SB-CORNER")
        self.assertEqual(len(corner_so), 1)
        # SO line count == visible (active) configurator design lines
        visible = d.cabinet_line_ids.filtered(lambda l: l.origin == "configurator")
        self.assertEqual(len(so_lines), len(visible))

    def test_rollback_on_injected_failure_restores_state(self):
        d = self._make_design(10)
        d.action_auto_arrange()             # arrange once
        before = self._active_snapshot(d)
        Recon = type(self.env["southbrook.design.reconcile"])
        with patch.object(Recon, "_reconcile_one",
                          side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                d.action_auto_arrange()
        # savepoint rolled the whole re-arrange back → unchanged
        self.assertEqual(self._active_snapshot(d), before)

    def test_capacity_exceeded_raises_and_rolls_back(self):
        d = self._make_design(25, room_w=96.0, room_d=96.0)
        before = self._active_snapshot(d)
        with self.assertRaises(E.LayoutCapacityExceeded):
            d.action_auto_arrange()
        self.assertEqual(self._active_snapshot(d), before)


class TestAutoArrangeCornerRuleSkuResolution(TransactionCase):
    """Task 28 (corner-remediation 2026-07-30) — full-stack regression for
    the prod-only "narrow room still picks the susan corner" defect.

    Live evidence: room_width_in=40 (X leg too short for the susan's
    45in min-leg) must select the blind corner (min-leg 27in/54in, both
    satisfied). `kitchen_layout_engine._select_corner_rule` DID pick the
    blind rule correctly — the leg-length gate itself was never broken.
    The bug was downstream: the blind rule's linked product template had
    gone multi-variant (a real prod shape — see kitchen_design.py's T5
    `_sb_resolve_corner_variant` docstring, "LH + RH after the SB-CORNER
    repair"), which forces Odoo's `product.template.default_code` to
    False, so `southbrook.placement.rule.engine_dicts()` reported
    `sku=""` for the correctly-selected blind rule and action_auto_arrange
    silently fell back to its hardcoded legacy default (SB-CORNER, the
    susan) instead. This test reproduces that exact shape end-to-end
    through the real ORM/action path (not the pure engine, which
    test_layout_engine.py already pins) and asserts the SKU that actually
    lands on the design line.
    """

    def setUp(self):
        super().setUp()
        Template = self.env["product.template"]
        Rule = self.env["southbrook.placement.rule"]

        # Isolation (mirrors test_room_bounds_save_and_autofill_cap.py's
        # competitor-neutralizing pattern): a full-catalog harness may
        # already carry the REAL seeded susan/blind junction rules —
        # left active, they'd also fit this room and could win the
        # engine's sequence/rule_id tie-break instead of this fixture's
        # rules, making the SKU assertion below depend on incidental
        # catalog state rather than the multi-variant fix under test.
        Rule.search([("anchor_class", "=", "junction"),
                     ("tier", "=", "base")]).write({"active": False})

        self.tmpl_susan = Template.create({
            "name": "Corner Base · Lazy-Susan (T28 fixture)",
            "default_code": "T28-SB-CORNER", "list_price": 625.0})
        self.tmpl_blind = Template.create({
            "name": "Corner Base · Blind (T28 fixture)", "list_price": 545.0})
        # A real Hinge Side attribute split (LH/RH) is what actually
        # flips a corner template multi-variant on prod (Task 6, "hand-
        # aware wall corners") — the exact prod shape (template 223,
        # variants 1104/1422). Two variants with no distinguishing
        # attribute would collide on the same empty combination_indices,
        # so this mirrors the real mechanism rather than hand-crafting a
        # bare second product.product.
        hinge = self.env["product.attribute"].create(
            {"name": "Hinge Side (T28 fixture)", "create_variant": "always"})
        lh_val = self.env["product.attribute.value"].create(
            {"name": "LH (T28 fixture)", "attribute_id": hinge.id})
        rh_val = self.env["product.attribute.value"].create(
            {"name": "RH (T28 fixture)", "attribute_id": hinge.id})
        self.env["product.template.attribute.line"].create({
            "product_tmpl_id": self.tmpl_blind.id, "attribute_id": hinge.id,
            "value_ids": [(6, 0, [lh_val.id, rh_val.id])],
        })
        self.blind_variant = self.tmpl_blind.product_variant_ids[:1]
        self.blind_variant.default_code = "T28-SB-CORNER-BLIND"
        # The RH twin mirrors prod's variant 1422 — no code assigned yet.
        self.tmpl_blind.invalidate_recordset(["default_code"])
        self.assertFalse(self.tmpl_blind.default_code,
            "sanity: fixture must reproduce Odoo's multi-variant blanking")

        Rule.create({
            "name": "T28 susan", "product_tmpl_id": self.tmpl_susan.id,
            "corner_type_id": "diagonal-corner-lazy-susan",
            "anchor_class": "junction", "tier": "base", "sequence": 10,
            "payload": {"leg_x_mm": 914.4, "leg_z_mm": 914.4,
                        "height_mm": 876.0, "min_leg_x_mm": 1143.0,
                        "min_leg_z_mm": 1143.0}})
        Rule.create({
            "name": "T28 blind", "product_tmpl_id": self.tmpl_blind.id,
            "corner_type_id": "blind-corner-basic",
            "anchor_class": "junction", "tier": "base", "sequence": 20,
            "payload": {"leg_x_mm": 609.6, "leg_z_mm": 1143.0,
                        "height_mm": 876.0, "min_leg_x_mm": 685.8,
                        "min_leg_z_mm": 1371.6, "host_leg": "z"}})

        self.base_prod = self.env["product.product"].create(
            {"name": "T28 Test Base 24", "default_code": "T28-ZZ-BASE24",
             "list_price": 200.0})
        self.partner = self.env["res.partner"].create({"name": "T28 Test"})

    def test_narrow_room_picks_blind_not_susan_through_multi_variant_template(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T28 narrow-corner probe", "partner_id": self.partner.id,
            "room_width_in": 40.0,   # X leg too short for susan's 45in
            "room_depth_in": 200.0,  # Z leg generous — blind's 54in fits
            "room_height_in": 96.0, "state": "draft"})
        Line = self.env["southbrook.kitchen.design.line"]
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "wall": "back", "run_seq": 0, "origin": "configurator",
            "layout_role": "canonical"})
        for i in range(3):
            Line.create({
                "design_id": design.id, "product_id": self.base_prod.id,
                "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
                "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
                "wall": "left", "run_seq": i, "origin": "configurator",
                "layout_role": "canonical"})

        design.action_auto_arrange(sync=False)
        corners = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner" and l.zone == "base_run")
        codes = corners.mapped("product_id.default_code")
        self.assertIn("T28-SB-CORNER-BLIND", codes,
            "narrow room must place the blind corner, got %s" % codes)
        self.assertNotIn("T28-SB-CORNER", codes,
            "the susan's own min-leg gate must have excluded it, got %s" % codes)
