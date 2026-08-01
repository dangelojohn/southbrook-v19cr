# SPDX-License-Identifier: LGPL-3.0-only
"""Task 4a (kitchen templates) — SB-CORNER data repair + L-10X8 flagship.

The corner unit is the whole point of the template feature ("difficult
to quote and build from scratch"). Live prod carries ONE known-bad
variant (Width='33 in' vs every other data point saying 36; LH only) —
the 19.0.9.1.0 southbrook_estimating migration repairs it. A bare test
DB has ZERO variants (config_ok suppresses auto-creation), so these
tests first RECREATE the known-bad prod state, then run the real
repair helper — everything downstream exercises the repaired catalog
exactly as prod will be after the migration.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    repair_corner_sku,
)


def _corner_ptav(env, tmpl, attr_name, prefix):
    return env["product.template.attribute.value"].search([
        ("product_tmpl_id", "=", tmpl.id),
        ("attribute_id.name", "=", attr_name),
        ("name", "=like", prefix + "%"),
    ], limit=1)


def ensure_repaired_corner(env):
    """Bring a bare DB's SB-CORNER to the repaired prod state.

    Recreates the known-bad live variant (33"/LH) when no variant
    exists, then runs the real T4a repair. Shared with the catalog
    suite (L-10X8 is active shipped data and needs a corner variant).
    """
    tmpl = env.ref("southbrook_estimating.corner")
    if not tmpl.product_variant_ids:
        combo = _corner_ptav(env, tmpl, "Width", "33") \
            | _corner_ptav(env, tmpl, "Hinge Side", "LH")
        env["product.product"].create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, combo.ids)],
            "default_code": "SB-CORNER",
        })
    repair_corner_sku(env)
    return tmpl


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestCornerSkuRepair(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corner_tmpl = ensure_repaired_corner(cls.env)

    def test_repair_fixes_width_and_creates_rh_twin(self):
        tmpl = self.corner_tmpl
        p33 = _corner_ptav(self.env, tmpl, "Width", "33")
        p36 = _corner_ptav(self.env, tmpl, "Width", "36")
        variants = tmpl.product_variant_ids
        self.assertGreaterEqual(len(variants), 2, "LH + RH twin expected")
        self.assertFalse(variants.filtered(
            lambda v: p33 in v.product_template_attribute_value_ids),
            "no variant may keep the known-bad 33\" width")
        self.assertTrue(variants.filtered(
            lambda v: p36 in v.product_template_attribute_value_ids),
            "the repaired variant carries the 36\" width")
        codes = set(variants.mapped("default_code"))
        self.assertIn("SB-CORNER", codes)
        self.assertIn("SB-CORNER-R", codes)

    def test_repair_is_idempotent(self):
        summary = repair_corner_sku(self.env)
        self.assertEqual(
            summary, {"width_fixed": 0, "codes_set": 0, "rh_created": False,
                      "wall_corner_created": False},
            "second run must be a strict no-op")

    def _instantiate_l(self):
        tpl = self.env["southbrook.kitchen.template"].search(
            [("code", "=", "L-10X8")], limit=1)
        self.assertTrue(tpl, "L-10X8 flagship must ship active")
        return tpl.action_instantiate()

    def test_l10x8_instantiates_with_engine_corner(self):
        design = self._instantiate_l()
        # The CORNER preference slot creates NO canonical line …
        self.assertFalse(design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "CORNER"))
        # … the ENGINE derives the corner, hand-picked LH for back-left.
        corner = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner" and l.zone == "base_run")
        self.assertEqual(len(corner), 1)
        self.assertEqual(corner.product_id.default_code, "SB-CORNER")
        # The wall-layer corner (SB-WALL-CORNER, seeded by the repair)
        # substitutes the junction uppers instead of vanishing them.
        wall_corner = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner" and l.zone == "wall")
        self.assertEqual(wall_corner.product_id.default_code,
                         "SB-WALL-CORNER")
        # The promised kitchen SURVIVES substitution: only the lead
        # buffers and the first uppers are absorbed by the corners.
        active_codes = set(design.cabinet_line_ids.mapped(
            "template_slot_code"))
        for kept in ("SINK", "RANGE", "W1B", "W2B"):
            self.assertIn(kept, active_codes,
                          "%s must survive the corner substitution" % kept)
        self.assertFalse(design.cabinet_line_ids.filtered("is_unresolved"))
        self.assertGreater(design.estimated_price, 0.0)

    def test_l10x8_no_geometry_hand_blocks(self):
        """Task 1 pin (corner-remediation 2026-07-30) — a freshly
        instantiated + arranged L-shape template produces no collision/
        bounds/hand blocks. Customer/BOM gates are out of scope for this
        pin (see the equivalent U-shape pin in test_u_shape_template.py)
        — this design carries no partner_id and this bare test DB may
        not seed every catalog BOM."""
        if len(self.corner_tmpl.product_variant_ids) < 2:
            # Known Harness-B data drift (baseline-tests.md): this DB
            # clone's pre-existing SB-CORNER variant state isn't the
            # recreatable "known-bad 33in/LH" shape ensure_repaired_corner
            # bootstraps from, so the repair can't produce the RH twin
            # here (see TestCornerSkuRepair.
            # test_repair_fixes_width_and_creates_rh_twin, itself a
            # documented baseline red on this harness). Without an RH
            # twin, some L-10X8 slots can't resolve at all — a catalog-
            # completeness gap outside this pin's scope (geometry/hand/
            # bounds checks), not a validator defect.
            self.skipTest(
                "corner repair did not produce an LH+RH twin on this DB "
                "(known Harness-B data drift)")
        design = self._instantiate_l()
        blocking = [i for i in design._check_production_ready()
                    if i["severity"] == "blocking"]
        geometry_blocks = [i for i in blocking
                           if i["code"] not in ("MISSING_CUSTOMER",
                                                 "MISSING_BOM")]
        self.assertFalse(
            geometry_blocks,
            "L-10X8 must instantiate with no geometry/hand/bounds "
            "blocking issues: %s" % geometry_blocks)

    def test_flip_swaps_corner_hand(self):
        design = self._instantiate_l()
        design.action_flip_layout(axis="x")
        # back+left flips to back+right: the junction is now right-handed
        # and the engine must pick the RH twin created by the repair.
        corner = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner" and l.zone == "base_run")
        self.assertEqual(len(corner), 1, "flip must keep the corner valid")
        self.assertEqual(corner.product_id.default_code, "SB-CORNER-R")

    def test_flip_preserves_wall_layer_corner(self):
        """Task 32 (corner-remediation follow-up, ticket from T6) — pins
        the defect T6's implementer recorded but did not fix: flipping
        L-10X8 drops the WALL-layer corner line (SB-WALL-CORNER)
        entirely — 0 lines, not a wrong-hand line — while the base-layer
        corner correctly survives and swaps hand. Root cause (verified
        via odoo-shell instrumentation against kitchen_layout_engine's
        pure functions directly, not guessed): the engine's layout()
        accumulates each (wall, layer) run's cursor independently,
        compacting only that layer's own members from 0 — so a wall-
        layer run several members shorter than its sibling base run
        (e.g. no upper above an appliance space) falls short of the
        room's true far edge once a flip makes that junction need the
        "last" end of the run instead of the trivially-reached "first"
        end. detect_corners then correctly reports (by its own,
        unchanged, off-limits geometric test) that the wall layer never
        reaches the junction, so no wall corner node is ever inserted.

        Assertions deliberately avoid hardcoding a "-R"/"-L" default_code
        (test_flip_swaps_corner_hand's own hardcoded "SB-CORNER-R" is
        ITSELF one of this DB's 3 documented baseline reds — the repair
        does not produce a working coded twin here) — instead checking
        existence + the mirrored junction/position via layout_key/wall/
        rotation_deg, and hand correctness via the SAME
        CORNER_HAND_MISMATCH validator block every other corner-hand
        test in this suite relies on (skips cleanly when a product
        carries no Hinge Side split, exactly like the validator itself).

        Skip guard deliberately checks THIS pin's own precondition (a
        wall-layer corner must exist pre-flip at all) rather than
        borrowing test_l10x8_no_geometry_hand_blocks's SB-CORNER (base
        family) twin-count check — SB-WALL-CORNER is a separate template
        from SB-CORNER and this harness's catalog drift affects them
        independently (confirmed via odoo-shell probe: on this CI DB
        SB-CORNER has only 1 materialized variant — the documented
        baseline red — while SB-WALL-CORNER already carries a working
        LH+RH pair); gating on the wrong template would skip a pin this
        harness can actually run.
        """
        design = self._instantiate_l()

        def _wall_corner(d):
            return d.cabinet_line_ids.filtered(
                lambda l: l.layout_role == "derived"
                and l.cabinet_type == "corner" and l.zone == "wall")

        pre = _wall_corner(design)
        if len(pre) != 1:
            # Catalog-completeness gap outside this pin's scope (mirrors
            # test_l10x8_no_geometry_hand_blocks's own skip pattern) —
            # SB-WALL-CORNER couldn't resolve at all on this harness, so
            # there is nothing meaningful to flip-and-check.
            self.skipTest(
                "L-10X8 did not instantiate with a wall-layer corner on "
                "this DB (catalog-completeness gap, not this pin's "
                "concern)")
        self.assertEqual(pre.layout_key, "corner-%s-back-left-wall" % design.id)
        # Review round 1, MINOR 6 — baseline captured BEFORE the flip so
        # the post-flip assertion below can tell "pre-existing, catalog-
        # driven UNRESOLVED_SLOT noise" (this DB's SB-CORNER twin drift
        # leaves M1/B2/M2 as unresolved GHOST placeholders — confirmed
        # via odoo-shell probe to be IDENTICAL before and after a flip,
        # nothing this fix could touch) apart from a genuinely NEW issue
        # the fix might introduce.
        pre_unresolved_slots = set(
            design.cabinet_line_ids.filtered("is_unresolved")
            .mapped("template_slot_code"))

        design.action_flip_layout(axis="x")

        post = _wall_corner(design)
        self.assertEqual(
            len(post), 1,
            "flip must keep the wall-layer corner — it must be re-derived "
            "or mirrored onto the new (back-right) junction, not dropped")
        self.assertEqual(
            post.layout_key, "corner-%s-back-right-wall" % design.id,
            "the wall corner must move to the MIRRORED junction, not stay "
            "at the pre-flip one")
        # back-right's presentation is the right wall's own convention
        # (kitchen_layout_engine._CORNER_RESOLVE_PRESENTATION) — same
        # table the base-layer corner's own placement already honours.
        self.assertEqual(post.wall, "right")
        self.assertEqual(post.rotation_deg, 270)

        # Review round 1, MINOR 6 — assert the FULL blocking list (minus
        # MISSING_CUSTOMER/MISSING_BOM, the two exceptions
        # test_l10x8_no_geometry_hand_blocks already establishes are out
        # of scope on a bare test DB with no partner_id/BOM data, and
        # UNRESOLVED_SLOT — but ONLY for the SAME slots already unresolved
        # pre-flip, verified via the strict subset check below, never a
        # blanket exclusion), not just CORNER_HAND_MISMATCH, so a stray
        # FOOTPRINT_COLLISION from the backfill's geometry — or anything
        # else new — fails this test instead of going unnoticed.
        post_unresolved_slots = set(
            design.cabinet_line_ids.filtered("is_unresolved")
            .mapped("template_slot_code"))
        self.assertLessEqual(
            post_unresolved_slots, pre_unresolved_slots,
            "flip must not create NEW unresolved template slots: %s"
            % (post_unresolved_slots - pre_unresolved_slots))
        blocking = [i for i in design._check_production_ready()
                    if i["severity"] == "blocking"]
        geometry_blocks = [i for i in blocking
                           if i["code"] not in ("MISSING_CUSTOMER",
                                                 "MISSING_BOM",
                                                 "UNRESOLVED_SLOT")]
        self.assertFalse(
            geometry_blocks,
            "the flipped design must carry no geometry/hand/collision "
            "blocking issues: %s" % geometry_blocks)
