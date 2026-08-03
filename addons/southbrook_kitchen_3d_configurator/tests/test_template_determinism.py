# SPDX-License-Identifier: LGPL-3.0-only
"""C6 — deterministic template resolution (2026-08-03 money-bug fix).

Required reading: e2_code_verification.md item 6 (E2E code verification,
2026-08-02/03) — three instantiations of the SAME shipped template
("U-Shape Kitchen 10' x 8'", 14 cabinets each, no user input differs)
priced out at $3,805.00, $3,805.00, and $3,175.00. Root cause:
`_resolve_slot()`'s two template-search rungs (models/kitchen_template.py
— archetype match, and the cabinet_type+exact-width fallback) both took
`exact[:1]` whenever more than one catalog `product.template` tied on
nominal width — the first record of whatever order `product.template.
search()` happened to return, not a business decision. The shipped
catalog genuinely contains such ties (data/demo_cabinets.xml: B24 "B24
Base Cabinet" $180 vs DB24 "DB24 3-Drawer Base" $210, both
cabinet_type=base, width=24in — the exact shape the U-shape template's
unpinned M1/M2/B3 base slots resolve through).

Fix: `_sb_resolve_template_tiebreak()` (kitchen_template.py) applies an
EXPLICIT total order — after each rung's own width-match scoring is
unchanged — of: `sequence` (the priority field `product.template`
already ships) -> lowest `list_price` (a commercial, not arbitrary,
tiebreak: the same kitchen never silently costs more) -> `default_code`
ascending -> `id` ascending (the last two exist purely to GUARANTEE a
strict total order). This module pins that both the raw tiebreak
function and the full instantiate-a-template path are now deterministic,
using SYNTHETIC catalog fixtures (same rationale as
test_slot_variant_width.py and test_placed_width_in.py) so these tests
stay green regardless of real-catalog data drift.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestTemplateDeterminism(TransactionCase):

    _code_seq = 0

    @classmethod
    def _next_code(cls):
        cls._code_seq += 1
        return "T-DET-%d" % cls._code_seq

    @classmethod
    def _mk_tmpl(cls, name, price, width_in=79.0, cabinet_type="base",
                 sequence=1, default_code=None):
        """A single-variant synthetic cabinet catalog `product.template`.
        No attribute lines -> Odoo auto-creates exactly one
        `product.product` variant, so this is a clean "one SKU, one
        price" fixture for template-level tiebreak testing (as opposed
        to test_slot_variant_width.py's multi-variant fixtures, which
        test the DIFFERENT "which variant of an already-chosen template"
        bug). `width_in` defaults to 79.0 -- well outside the real
        shipped catalog's base-cabinet range (15/18/24/30/33in per
        data/demo_cabinets.xml) so these fixtures can never accidentally
        collide with a real catalog neighbour.
        """
        vals = {
            "name": name, "type": "consu", "list_price": price,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": cabinet_type,
            "southbrook_width_in": width_in,
            "southbrook_height_in": 34.5, "southbrook_depth_in": 24.0,
            "sequence": sequence,
        }
        if default_code is not None:
            vals["default_code"] = default_code
        return cls.env["product.template"].create(vals)

    def _mk_kitchen_template(self):
        return self.env["southbrook.kitchen.template"].create({
            "name": "Determinism resolver host", "code": self._next_code(),
            "layout_shape": "straight",
        })

    def _mk_slot(self, ktpl, nominal_width_in, cabinet_type="base"):
        return self.env["southbrook.kitchen.template.line"].create({
            "template_id": ktpl.id, "slot_code": "B1",
            "cabinet_type": cabinet_type, "wall": "back", "run_seq": 10,
            "nominal_width_in": nominal_width_in,
        })

    # ── Direct unit tests of _sb_resolve_template_tiebreak ─────────────

    def test_lower_price_wins_when_sequence_ties(self):
        """Two templates, same sequence, tied width -> the CHEAPER one
        wins, regardless of creation order or alphabetical name order.
        The expensive template is named/coded to sort FIRST
        alphabetically and is created FIRST (i.e. it would win under the
        pre-fix `exact[:1]`/database-order behaviour) -- a pass here can
        only be explained by the price tiebreak actually firing."""
        expensive = self._mk_tmpl(
            "AAA Expensive Base", 900.0, default_code="T-DET-AAA")
        cheap = self._mk_tmpl(
            "ZZZ Cheap Base", 300.0, default_code="T-DET-ZZZ")
        ktpl = self._mk_kitchen_template()
        winner = ktpl._sb_resolve_template_tiebreak(expensive + cheap)
        self.assertEqual(winner.id, cheap.id)

    def test_lower_sequence_wins_even_with_higher_price(self):
        """The priority (`sequence`) field outranks price -- a catalog
        owner who sets a lower sequence on the pricier SKU gets that SKU,
        not the cheaper one."""
        preferred_but_pricier = self._mk_tmpl(
            "Preferred Base", 900.0, sequence=1,
            default_code="T-DET-PREF")
        cheaper_not_preferred = self._mk_tmpl(
            "Cheaper Base", 300.0, sequence=5,
            default_code="T-DET-CHEAP")
        ktpl = self._mk_kitchen_template()
        winner = ktpl._sb_resolve_template_tiebreak(
            preferred_but_pricier + cheaper_not_preferred)
        self.assertEqual(winner.id, preferred_but_pricier.id)

    def test_default_code_breaks_a_price_and_sequence_tie(self):
        a = self._mk_tmpl("Tie Base A", 400.0, default_code="T-DET-B")
        b = self._mk_tmpl("Tie Base B", 400.0, default_code="T-DET-A")
        ktpl = self._mk_kitchen_template()
        winner = ktpl._sb_resolve_template_tiebreak(a + b)
        self.assertEqual(winner.id, b.id, "lower default_code must win")

    def test_id_is_the_final_guarantee(self):
        """Sequence, price, AND default_code all tied (both left blank)
        -> the total order still resolves to exactly one record, the
        lower id (first created) -- proving the chain can never fail to
        pick a single winner."""
        first = self._mk_tmpl("Untied Base 1", 400.0)
        second = self._mk_tmpl("Untied Base 2", 400.0)
        self.assertLess(first.id, second.id)
        ktpl = self._mk_kitchen_template()
        winner = ktpl._sb_resolve_template_tiebreak(first + second)
        self.assertEqual(winner.id, first.id)

    def test_singleton_and_empty_input_are_passthrough(self):
        solo = self._mk_tmpl("Solo Base", 250.0)
        ktpl = self._mk_kitchen_template()
        self.assertEqual(
            ktpl._sb_resolve_template_tiebreak(solo).id, solo.id)
        empty = self.env["product.template"]
        self.assertFalse(ktpl._sb_resolve_template_tiebreak(empty))

    # ── Full _resolve_slot() path — the documented winner end to end ───

    def test_resolver_fallback_rung_prefers_cheaper_tied_template(self):
        """The same AAA/ZZZ trap as above, but exercised through the
        REAL resolver rung a template slot actually calls (not just the
        raw tiebreak helper) -- proves the fix reaches production code,
        not just the unit-testable function in isolation."""
        expensive = self._mk_tmpl(
            "AAA Pricier Tied Base", 515.0, width_in=91.0,
            default_code="T-DET-EXP")
        cheap = self._mk_tmpl(
            "ZZZ Cheaper Tied Base", 465.0, width_in=91.0,
            default_code="T-DET-CHP")
        # Sanity: prove Odoo's own default product.template search order
        # (`is_favorite desc, name`) would hand back the EXPENSIVE
        # template first -- so a pass below can only be explained by the
        # price tiebreak, never by accidental alphabetical luck.
        db_order_first = self.env["product.template"].search(
            [("id", "in", (expensive + cheap).ids)])[:1]
        self.assertEqual(
            db_order_first.id, expensive.id,
            "fixture must reproduce the database-order-picks-wrong hazard")
        ktpl = self._mk_kitchen_template()
        slot = self._mk_slot(ktpl, 91.0)
        resolved = ktpl._resolve_slot(slot, 91.0)
        self.assertEqual(resolved.product_tmpl_id.id, cheap.id)
        self.assertEqual(resolved.lst_price, 465.0)

    # ── action_instantiate() end to end — identical lines, identical $ ─

    def test_action_instantiate_is_deterministic_across_repeated_calls(self):
        """The C6 regression itself: instantiate the SAME template TWICE
        in one transaction and require an IDENTICAL resolved line set and
        an IDENTICAL total -- the exact invariant the live evidence
        (three instantiations of "U-Shape Kitchen 10' x 8'" totalling
        $3,805.00 / $3,805.00 / $3,175.00) violated."""
        expensive = self._mk_tmpl(
            "AAA Pricier Instantiate Base", 650.0, width_in=83.0,
            default_code="T-DET-INST-EXP")
        cheap = self._mk_tmpl(
            "ZZZ Cheaper Instantiate Base", 300.0, width_in=83.0,
            default_code="T-DET-INST-CHP")
        db_order_first = self.env["product.template"].search(
            [("id", "in", (expensive + cheap).ids)])[:1]
        self.assertEqual(
            db_order_first.id, expensive.id,
            "fixture must reproduce the database-order-picks-wrong hazard")

        tpl = self.env["southbrook.kitchen.template"].create({
            "name": "T-DET Instantiate Probe", "code": self._next_code(),
            "layout_shape": "straight",
            "default_room_width_in": 100.0, "default_room_depth_in": 96.0,
            "default_room_height_in": 96.0, "default_module_width_in": 83.0,
            "min_cabinet_count": 1,
        })
        self.env["southbrook.kitchen.template.line"].create({
            "template_id": tpl.id, "slot_code": "B1", "cabinet_type": "base",
            "wall": "back", "run_seq": 10, "nominal_width_in": 83.0,
        })

        design1 = tpl.action_instantiate()
        design2 = tpl.action_instantiate()

        def _line_set(design):
            return {
                (l.template_slot_code, l.product_id.default_code)
                for l in design.cabinet_line_ids
            }

        self.assertEqual(len(design1.cabinet_line_ids),
                          len(design2.cabinet_line_ids))
        self.assertEqual(_line_set(design1), _line_set(design2))
        self.assertEqual(design1.estimated_price, design2.estimated_price)
        # Not just equal to each other -- equal to the CORRECT (cheap)
        # total, proving both calls picked the cheap SKU, not that they
        # both happened to agree on the (wrong) expensive one.
        self.assertEqual(design1.estimated_price, 300.0)
        self.assertEqual(design2.estimated_price, 300.0)
        b1_1 = design1.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "B1")
        b1_2 = design2.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "B1")
        self.assertEqual(b1_1.product_id.product_tmpl_id.id, cheap.id)
        self.assertEqual(b1_2.product_id.product_tmpl_id.id, cheap.id)
