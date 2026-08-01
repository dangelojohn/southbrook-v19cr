# SPDX-License-Identifier: LGPL-3.0-only
"""Task 15 (corner-remediation 2026-07-30, SBK-027) — wall-run / base-run
coordination.

Part (a): `_check_production_ready` gained two new checks (kitchen_design.py,
"#14"):
  - WALL_RUN_SHORTER_THAN_BASE (warning — see "review round 2" below) — a
    wall carrying at least one WALL cabinet (cabinet_type='wall',
    zone='wall') whose merged, anchor-convention covered length is less
    than that of its BASE cabinets (cabinet_type='base', zone='base_run').
    Corner cells (cabinet_type='corner') and appliance gaps (cabinet_type=
    'appliance') are excluded from BOTH sides of the comparison by
    construction (neither cabinet_type is 'base' or 'wall'), per the brief
    verbatim.
  - WALL_BASE_JOINT_MISALIGNED (warning) — once the wall run is long
    enough, a WALL-run interior seam (the touching boundary between two
    adjacent same-run cabinets) that has no matching BASE-run seam within
    0.5" is flagged.

Orchestrator ruling (2026-07-31, review round 1) — a wall with a base run
and ZERO wall cabinets at all produces NO issue of any severity. A first
cut BLOCKed on "wall coverage doesn't reach the base run" even when there
was no wall run at all to begin with, which fires on completely normal,
deliberately upper-cabinet-free walls (windows, range hoods, open
shelving) — this validator runs against live saved customer designs, and
a blocking rule must reflect real invalidity, not "this wall happens to
have no uppers". The brief's actual concern is coordinating two runs that
BOTH exist. See `test_wall_with_zero_wall_cabinets_is_not_flagged_at_all`.

Orchestrator ruling (2026-07-31, review round 2) — WALL_RUN_SHORTER_THAN_
BASE is downgraded from BLOCKING to WARNING. Both shipped templates
(U-10X8X10's 24" upper over the 30" SINK base; L-10X8's appliance-adjacent
legs) carry this shape DELIBERATELY — a shorter/absent upper run over a
sink or appliance is a normal, buildable kitchen. At blocking severity
this broke the deploy smoke's "only a missing-customer block on a fresh
shipped template" assertion AND made `action_create_quotation` raise
UserError for an ordinary fresh shipped-template instantiation — a false
invalid state on live data. `code`/`message` are unchanged (a future
severity flip is one word). See
`test_action_create_quotation_succeeds_with_only_the_new_warning`.

Reproduction (documented, not a shipped test — see task-15-report.md):
before this fix, none of `_check_production_ready`'s existing checks read
per-wall base-vs-wall RUN LENGTH at all (collision/footprint checks compare
individual footprints; `remainder_in` sums a single run's own occupancy,
never compares base against wall) — a base run of 36" under a wall run of
24" produced zero issues. Confirmed by running this exact fixture (test
`test_wall_run_shorter_than_base_run_warns` below, minus the assertion)
against the pre-Task-15 model.

Part (b) (generation — `_generate_standard_layout`, the "Generate Layout"
button's model method): fixed to tile the wall run at the WALL product's
OWN width across the base run's real length, instead of reusing the base
module's x-cursor for both cabinet types. Live-verified against the real
production catalog (a database clone, not committed here) that the OLD
code produced 11 FOOTPRINT_COLLISION + 1 OUT_OF_ROOM_BOUNDS on a 144"
room, because the resolved base product (12" module) and wall product
(24" wide) don't share a width — the wall loop reused the base's 12"
x-cursor for a 24"-wide cabinet, so consecutive wall cabinets overlapped
each other and ran past the room. `kitchen_layout_engine.py` (LAW) is not
touched — this method never called it.

Review round 2, IMPORTANT 2 — when the wall product's width doesn't evenly
divide the base run's length, a wall-side filler strip now covers the
leftover tail (mirroring the base run's own remainder-filler mechanism).
That filler is cabinet_type='filler', which — like every other check in
this module, and like `remainder_in`'s own `_base_run_eligible_lines`
convention — never counts as real cabinet_type=='wall' coverage, so the
tail can still legitimately surface WALL_RUN_SHORTER_THAN_BASE as a
(non-blocking) warning: physically covered, never silently short. See
`test_non_divisible_wall_width_tail_is_covered_and_warned`.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "wall_base_run_coordination")
class TestWallRunBaseRunCoordination(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["product.product"]
        cls.base_prod = P.create(
            {"name": "T15 Base", "default_code": "ZZ-T15-BASE",
             "list_price": 200.0})
        cls.wall_prod = P.create(
            {"name": "T15 Wall", "default_code": "ZZ-T15-WALL",
             "list_price": 150.0})
        cls.corner_prod = P.create(
            {"name": "T15 Corner", "default_code": "ZZ-T15-CORNER",
             "list_price": 600.0})
        cls.appl_prod = P.create(
            {"name": "T15 Appliance Space", "default_code": "ZZ-T15-APPL",
             "list_price": 0.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "T15 Wall/Base Run Coordination"})

    def _design(self, room_width_in=96.0):
        return self.env["southbrook.kitchen.design"].create({
            "name": "T15 design", "partner_id": self.partner.id,
            "room_width_in": room_width_in, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })

    def _line(self, design, product, cabinet_type, width_in, x_position_in,
              wall="back", layout_key=None, quantity=1, price_unit=None,
              height_in=34.5, depth_in=24.0):
        Line = self.env["southbrook.kitchen.design.line"]
        return Line.create({
            "design_id": design.id, "product_id": product.id,
            "quantity": quantity,
            "price_unit": price_unit if price_unit is not None
                          else product.lst_price,
            "cabinet_type": cabinet_type, "wall": wall,
            "width_in": width_in, "height_in": height_in,
            "depth_in": depth_in, "x_position_in": x_position_in,
            "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator",
            "layout_key": layout_key or ("t15-%s-%s" % (cabinet_type, x_position_in)),
        })

    # ── (a) WARN: wall run shorter than base run ────────────────────────

    def test_wall_run_shorter_than_base_run_warns(self):
        """The failing shape the brief asks to reproduce first: a 36"
        base cabinet under a 24" wall cabinet on the same wall, both
        starting flush at x=0. Before Task 15, `_check_production_ready`
        had no check that ever compared these two runs' lengths — this
        exact fixture produced zero issues. Orchestrator ruling (review
        round 2): WARNING, not blocking — see this file's module
        docstring."""
        design = self._design()
        self._line(design, self.base_prod, "base", 36.0, 0.0,
                    layout_key="t15-base-a")
        self._line(design, self.wall_prod, "wall", 24.0, 0.0,
                    layout_key="t15-wall-a")

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("WALL_RUN_SHORTER_THAN_BASE", by_code, issues)
        hit = by_code["WALL_RUN_SHORTER_THAN_BASE"]
        self.assertEqual(hit["severity"], "warning")
        self.assertIn("back", hit["message"])
        self.assertIn("36.0", hit["message"])
        self.assertIn("24.0", hit["message"])
        # message hygiene — no raw internal token leaks (matches the
        # convention test_validator_copy.py pins for every other check).
        for token in ("run_seq", "layout_role", "template_slot_code",
                      "base_run", "layout_key", "rot=", "x=", "z="):
            self.assertNotIn(token, hit["message"])

    def test_action_create_quotation_succeeds_with_only_the_new_warning(self):
        """Orchestrator ruling (review round 2) — WALL_RUN_SHORTER_THAN_
        BASE must NOT gate `action_create_quotation`. Shape mirrors the
        real U-10X8X10 shipped template's sink leg (a 30" base run under
        a 24" wall run) — synthetic, not the literal template, so this
        pin is independent of harness-DB catalog drift (verified live:
        the real U-10X8X10 template on this harness ALSO carries
        unrelated UNRESOLVED_SLOT issues from catalog drift, which would
        make a literal-template version of this test flaky for reasons
        having nothing to do with this rule). BOMs are pre-created (the
        existing funnel tests' convention — e.g. test_validator_repair.py
        — for isolating this from BOM-autoseed behaviour) and the
        fixture's own partner_id already satisfies MISSING_CUSTOMER, so
        the only issue on this design is the new warning."""
        design = self._design()
        Bom = self.env["mrp.bom"]
        Bom.create({"product_tmpl_id": self.base_prod.product_tmpl_id.id})
        Bom.create({"product_tmpl_id": self.wall_prod.product_tmpl_id.id})
        self._line(design, self.base_prod, "base", 30.0, 0.0,
                    layout_key="t15-funnel-base")
        self._line(design, self.wall_prod, "wall", 24.0, 0.0,
                    layout_key="t15-funnel-wall")

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("WALL_RUN_SHORTER_THAN_BASE", by_code, issues)
        self.assertEqual(by_code["WALL_RUN_SHORTER_THAN_BASE"]["severity"],
                          "warning")
        blocking = [i for i in issues if i["severity"] == "blocking"]
        self.assertFalse(blocking, blocking)

        design.action_create_quotation()   # must not raise UserError
        self.assertEqual(design.state, "quoted")
        self.assertTrue(design.sale_order_id)

    def test_wall_run_equal_or_longer_does_not_block(self):
        design = self._design()
        # Exactly equal.
        self._line(design, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-eq-base")
        self._line(design, self.wall_prod, "wall", 24.0, 0.0,
                    layout_key="t15-eq-wall")
        codes = [i["code"] for i in design._check_production_ready()]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)

        design2 = self._design()
        # Wall run longer than the base run underneath it.
        self._line(design2, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-longer-base")
        self._line(design2, self.wall_prod, "wall", 30.0, 0.0,
                    layout_key="t15-longer-wall")
        codes2 = [i["code"] for i in design2._check_production_ready()]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes2, codes2)

    def test_wall_with_no_base_run_is_not_compared(self):
        """A wall carrying wall cabinets but no base run at all has
        nothing to compare against — must not block."""
        design = self._design()
        self._line(design, self.wall_prod, "wall", 24.0, 0.0,
                    layout_key="t15-onlywall")
        codes = [i["code"] for i in design._check_production_ready()]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)

    def test_wall_with_zero_wall_cabinets_is_not_flagged_at_all(self):
        """Orchestrator ruling (2026-07-31, review round 1): a base run
        with NO wall cabinets at all on that wall is a deliberate,
        valid choice (window wall, range hood, open shelving) — not a
        coordination defect between two runs, since there is no second
        run to coordinate against. Must produce NEITHER
        WALL_RUN_SHORTER_THAN_BASE NOR WALL_BASE_JOINT_MISALIGNED,
        regardless of how long or how many-cabinet the base run is."""
        design = self._design()
        self._line(design, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-zerowall-base-a")
        self._line(design, self.base_prod, "base", 24.0, 24.0,
                    layout_key="t15-zerowall-base-b")
        codes = [i["code"] for i in design._check_production_ready()]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)
        self.assertNotIn("WALL_BASE_JOINT_MISALIGNED", codes, codes)

    # ── corner cells / appliance gaps excluded from the comparison ──────

    def test_corner_cell_excluded_from_base_run_length(self):
        """A corner cell sitting on the same wall as a base run must NOT
        inflate the base side of the comparison — only cabinet_type=='base'
        counts. Base run (excl. corner) = 24"; wall run = 24": must match,
        even though the corner cell alone is wider than the wall run."""
        design = self._design()
        self._line(design, self.corner_prod, "corner", 36.0, 0.0,
                    layout_key="t15-corner")
        self._line(design, self.base_prod, "base", 24.0, 36.0,
                    layout_key="t15-corner-base")
        self._line(design, self.wall_prod, "wall", 24.0, 36.0,
                    layout_key="t15-corner-wall")
        codes = [i["code"] for i in design._check_production_ready()]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)

    def test_appliance_gap_excluded_from_base_run_length_and_joints(self):
        """An appliance space sitting between two base cabinets must not
        (a) count toward the base run's length, nor (b) manufacture a
        false "interior joint" out of the gap it leaves. Base run (excl.
        appliance) = 24 + 24 = 48"; wall run mirrors the same two 24"
        spans with the same 30" gap in between — both sides must be
        judged purely on their own real cabinets, matching cleanly."""
        design = self._design(room_width_in=120.0)
        self._line(design, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-appl-base-a")
        self._line(design, self.appl_prod, "appliance", 30.0, 24.0,
                    layout_key="t15-appl-gap")
        self._line(design, self.base_prod, "base", 24.0, 54.0,
                    layout_key="t15-appl-base-b")
        self._line(design, self.wall_prod, "wall", 24.0, 0.0,
                    layout_key="t15-appl-wall-a")
        self._line(design, self.wall_prod, "wall", 24.0, 54.0,
                    layout_key="t15-appl-wall-b")
        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)
        self.assertNotIn("WALL_BASE_JOINT_MISALIGNED", codes, codes)

    # ── (a) WARN: interior seams misaligned > 0.5" ──────────────────────

    def test_joint_misalignment_warns_when_runs_match_in_length(self):
        """Both runs cover the same 48" total, but the base run's seam
        (at x=24) and the wall run's seam (at x=30) are 6" apart."""
        design = self._design()
        self._line(design, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-joint-base-a")
        self._line(design, self.base_prod, "base", 24.0, 24.0,
                    layout_key="t15-joint-base-b")
        self._line(design, self.wall_prod, "wall", 30.0, 0.0,
                    layout_key="t15-joint-wall-a")
        self._line(design, self.wall_prod, "wall", 18.0, 30.0,
                    layout_key="t15-joint-wall-b")

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", by_code, issues)
        self.assertIn("WALL_BASE_JOINT_MISALIGNED", by_code, issues)
        hit = by_code["WALL_BASE_JOINT_MISALIGNED"]
        self.assertEqual(hit["severity"], "warning")
        self.assertIn("back", hit["message"])
        for token in ("run_seq", "layout_role", "template_slot_code",
                      "base_run", "layout_key", "rot=", "x=", "z="):
            self.assertNotIn(token, hit["message"])

    def test_joint_within_half_inch_tolerance_does_not_warn(self):
        """Same shape as above, but the wall run's seam is only 0.2"
        off — inside the brief's 0.5" tolerance."""
        design = self._design()
        self._line(design, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-ok-base-a")
        self._line(design, self.base_prod, "base", 24.0, 24.0,
                    layout_key="t15-ok-base-b")
        self._line(design, self.wall_prod, "wall", 24.2, 0.0,
                    layout_key="t15-ok-wall-a")
        self._line(design, self.wall_prod, "wall", 23.8, 24.2,
                    layout_key="t15-ok-wall-b")
        codes = [i["code"] for i in design._check_production_ready()]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)
        self.assertNotIn("WALL_BASE_JOINT_MISALIGNED", codes, codes)

    def test_single_wall_cabinet_spanning_run_has_no_joints_to_compare(self):
        """A wall run made of ONE cabinet (no interior seam at all) has
        nothing to misalign against — must not warn, even though the
        base run underneath it has its own interior seam."""
        design = self._design()
        self._line(design, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-single-base-a")
        self._line(design, self.base_prod, "base", 24.0, 24.0,
                    layout_key="t15-single-base-b")
        self._line(design, self.wall_prod, "wall", 48.0, 0.0,
                    layout_key="t15-single-wall")
        codes = [i["code"] for i in design._check_production_ready()]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)
        self.assertNotIn("WALL_BASE_JOINT_MISALIGNED", codes, codes)

    def test_many_narrow_base_seams_under_one_wide_wall_seam_does_not_warn(self):
        """Direction matters: MORE, NARROWER base cabinets under FEWER,
        WIDER wall cabinets is completely normal (e.g. two 12" drawer
        banks under one 24" wall cabinet) — most of the base run's own
        interior seams necessarily fall mid-wall-cabinet and must NOT be
        flagged. Only a WALL seam with no matching base seam counts.
        Live-verified against the production catalog: SB-BASE-1DRW-TOP
        (12") under SB-WALL-2DR-24 (24") on a 144" wall — an earlier,
        buggy direction (iterating base seams first) flagged 6 of 11
        base seams here as "misaligned" even though every wall seam
        landed exactly on a base seam."""
        design = self._design(room_width_in=48.0)
        for i in range(4):
            self._line(design, self.base_prod, "base", 12.0, i * 12.0,
                        layout_key="t15-narrow-base-%d" % i)
        for j in range(2):
            self._line(design, self.wall_prod, "wall", 24.0, j * 24.0,
                        layout_key="t15-narrow-wall-%d" % j)
        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, codes)
        self.assertNotIn("WALL_BASE_JOINT_MISALIGNED", codes, codes)

    def test_wall_seam_not_over_any_base_seam_warns(self):
        """The genuine defect this check exists to catch: a wall run
        whose seam falls in a spot with no matching base division at
        all (2 base cabinets @24" vs 3 wall cabinets @16" — neither
        16" nor 32" lines up with the base run's one seam at 24")."""
        design = self._design(room_width_in=48.0)
        self._line(design, self.base_prod, "base", 24.0, 0.0,
                    layout_key="t15-nomatch-base-a")
        self._line(design, self.base_prod, "base", 24.0, 24.0,
                    layout_key="t15-nomatch-base-b")
        for j in range(3):
            self._line(design, self.wall_prod, "wall", 16.0, j * 16.0,
                        layout_key="t15-nomatch-wall-%d" % j)
        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", by_code, issues)
        self.assertIn("WALL_BASE_JOINT_MISALIGNED", by_code, issues)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "wall_base_run_coordination")
class TestGenerateStandardLayoutTargetsBaseRunLength(TransactionCase):
    """Part (b) — `_generate_standard_layout` (the "Generate Layout"
    button). Reproduces the production defect with a synthetic base/wall
    pair whose widths don't match (12" base module vs 24" wall cabinet —
    the exact shape found live in the production catalog), bypassing
    `_find_cabinet_product`'s uncontrolled global catalog search via
    `unittest.mock.patch.object` so the test doesn't depend on whatever
    other "base"/"wall"-typed products happen to already exist in the
    test database. `_find_cabinet_product` is read-only on an Odoo
    recordset instance (ORM-enforced), so it's patched at the CLASS
    level via `unittest.mock.patch.object`, scoped to each call with a
    `with` block."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["product.product"]
        cls.base12 = P.create(
            {"name": "T15 Gen Base 12", "default_code": "ZZ-T15GEN-BASE12",
             "list_price": 150.0})
        cls.wall24 = P.create(
            {"name": "T15 Gen Wall 24", "default_code": "ZZ-T15GEN-WALL24",
             "list_price": 180.0})
        cls.base12.product_tmpl_id.write({
            "southbrook_width_in": 12.0, "southbrook_height_in": 34.5,
            "southbrook_depth_in": 24.0, "southbrook_cabinet_type": "base",
            "southbrook_is_cabinet": True})
        cls.wall24.product_tmpl_id.write({
            "southbrook_width_in": 24.0, "southbrook_height_in": 30.0,
            "southbrook_depth_in": 12.0, "southbrook_cabinet_type": "wall",
            "southbrook_is_cabinet": True})
        cls.filler12 = P.create(
            {"name": "T15 Gen Filler", "default_code": "ZZ-T15GEN-FILLER",
             "list_price": 20.0})
        cls.filler12.product_tmpl_id.write({
            "southbrook_width_in": 3.0, "southbrook_height_in": 34.5,
            "southbrook_depth_in": 1.0, "southbrook_cabinet_type": "filler",
            "southbrook_is_cabinet": True})

    def _design(self, room_width_in):
        return self.env["southbrook.kitchen.design"].create({
            "name": "T15 gen design", "room_width_in": room_width_in,
            "room_depth_in": 96.0, "room_height_in": 96.0,
        })

    def _generate_with(self, design, base_product, wall_product,
                       filler_product=None):
        empty = self.env["product.product"]

        def _fake_find(_self, cabinet_type, raise_if_missing=True):
            if cabinet_type == "base":
                return base_product
            if cabinet_type == "wall":
                return wall_product
            if cabinet_type == "filler" and filler_product is not None:
                return filler_product
            return empty
        Design = type(design)
        with patch.object(Design, "_find_cabinet_product",
                           side_effect=_fake_find, autospec=True):
            design._generate_standard_layout()

    def test_mismatched_widths_no_longer_collide_or_overflow(self):
        """144" room, 12" base module (12 cabinets, exact fit) paired
        with a 24" wall cabinet: before the fix, the wall loop reused
        the base's 12" x-cursor for a 24"-wide cabinet, so 11 pairs
        overlapped and the last one ran past the room. After the fix,
        6 wall cabinets tile 0..144 with no overlap."""
        design = self._design(room_width_in=144.0)
        self._generate_with(design, self.base12, self.wall24)

        base_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "base")
        wall_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "wall")
        self.assertEqual(len(base_lines), 12, base_lines.mapped("x_position_in"))
        self.assertEqual(len(wall_lines), 6, wall_lines.mapped("x_position_in"))
        wall_xs = sorted(wall_lines.mapped("x_position_in"))
        self.assertEqual(wall_xs, [0.0, 24.0, 48.0, 72.0, 96.0, 120.0])
        for wl in wall_lines:
            self.assertEqual(wl.y_position_in, 54.0)
            self.assertEqual(wl.z_position_in, 0.0)
        for bl in base_lines:
            self.assertEqual(bl.y_position_in, 0.0)
            self.assertEqual(bl.z_position_in, 0.0)

        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("FOOTPRINT_COLLISION", codes, issues)
        self.assertNotIn("OUT_OF_ROOM_BOUNDS", codes, issues)
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, issues)

    def test_matching_widths_144_wall_fills_fully(self):
        """AC: '144in wall fills fully in the default flow' — when base
        and wall products share a width, both runs fill exactly and no
        new rule (or any other geometry rule) blocks."""
        base24 = self.env["product.product"].create(
            {"name": "T15 Gen Base 24b", "default_code": "ZZ-T15GEN-BASE24B",
             "list_price": 150.0})
        base24.product_tmpl_id.write({
            "southbrook_width_in": 24.0, "southbrook_height_in": 34.5,
            "southbrook_depth_in": 24.0, "southbrook_cabinet_type": "base",
            "southbrook_is_cabinet": True})
        design = self._design(room_width_in=144.0)
        self._generate_with(design, base24, self.wall24)

        base_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "base")
        wall_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "wall")
        self.assertEqual(len(base_lines), 6)
        self.assertEqual(len(wall_lines), 6)
        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("WALL_RUN_SHORTER_THAN_BASE", codes, issues)
        self.assertNotIn("WALL_BASE_JOINT_MISALIGNED", codes, issues)
        self.assertNotIn("FOOTPRINT_COLLISION", codes, issues)
        self.assertNotIn("OUT_OF_ROOM_BOUNDS", codes, issues)

    def test_non_divisible_wall_width_tail_is_covered_and_warned(self):
        """Review round 2, IMPORTANT 2 — a wall product whose width
        doesn't evenly divide the base run's length must not leave the
        tail silently uncovered. A 132"-style room: 24" base module (5
        cabinets, a 120" base run, 12" base-side remainder at x=120)
        paired with a 32" wall cabinet (120" / 32" = 3 remainder 24"):
        the NEW wall-side filler must physically cover the 24" tail at
        x=96, and the residual REAL wall-cabinet shortfall (96" of real
        wall cabinet vs. 120" of base) must still surface as a
        (non-blocking) WARNING — covered physically, never silently
        short."""
        base24 = self.env["product.product"].create(
            {"name": "T15 Gen Base 24c", "default_code": "ZZ-T15GEN-BASE24C",
             "list_price": 150.0})
        base24.product_tmpl_id.write({
            "southbrook_width_in": 24.0, "southbrook_height_in": 34.5,
            "southbrook_depth_in": 24.0, "southbrook_cabinet_type": "base",
            "southbrook_is_cabinet": True})
        wall32 = self.env["product.product"].create(
            {"name": "T15 Gen Wall 32", "default_code": "ZZ-T15GEN-WALL32",
             "list_price": 200.0})
        wall32.product_tmpl_id.write({
            "southbrook_width_in": 32.0, "southbrook_height_in": 30.0,
            "southbrook_depth_in": 12.0, "southbrook_cabinet_type": "wall",
            "southbrook_is_cabinet": True})

        design = self._design(room_width_in=132.0)
        self._generate_with(design, base24, wall32,
                            filler_product=self.filler12)

        base_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "base")
        wall_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "wall")
        filler_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "filler")
        self.assertEqual(len(base_lines), 5, base_lines.mapped("x_position_in"))
        self.assertEqual(len(wall_lines), 3, wall_lines.mapped("x_position_in"))
        # Two fillers: the base run's own pre-existing remainder filler
        # (12" at x=120, floor elevation) and the NEW wall-side tail
        # filler (24" at x=96, wall elevation).
        self.assertEqual(len(filler_lines), 2, filler_lines.mapped(
            lambda l: (l.x_position_in, l.width_in, l.y_position_in)))

        base_filler = filler_lines.filtered(lambda l: l.y_position_in == 0.0)
        wall_filler = filler_lines.filtered(lambda l: l.y_position_in == 54.0)
        self.assertEqual(len(base_filler), 1, filler_lines)
        self.assertEqual(len(wall_filler), 1, filler_lines)
        self.assertAlmostEqual(base_filler.width_in, 12.0, places=2)
        self.assertAlmostEqual(base_filler.x_position_in, 120.0, places=2)
        self.assertAlmostEqual(wall_filler.width_in, 24.0, places=2)
        self.assertAlmostEqual(wall_filler.x_position_in, 96.0, places=2)
        self.assertEqual(wall_filler.z_position_in, 0.0)

        # Physically: the wall run's real cabinets + the new filler tile
        # the FULL base_run_length (120") with no gap and no overlap.
        wall_and_filler = wall_lines | wall_filler
        spans = sorted((l.x_position_in, l.x_position_in + l.width_in)
                       for l in wall_and_filler)
        self.assertAlmostEqual(spans[0][0], 0.0, places=2)
        self.assertAlmostEqual(spans[-1][1], 120.0, places=2)
        for (_lo, hi), (lo2, _hi2) in zip(spans, spans[1:]):
            self.assertAlmostEqual(hi, lo2, places=2)

        # But the check still WARNS: only REAL cabinet_type=='wall'
        # coverage (96") counts against the base run (120") — the
        # filler is deliberately excluded from the comparison, same
        # convention as `remainder_in`'s own base-side filler exclusion.
        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("WALL_RUN_SHORTER_THAN_BASE", by_code, issues)
        self.assertEqual(by_code["WALL_RUN_SHORTER_THAN_BASE"]["severity"],
                          "warning")
