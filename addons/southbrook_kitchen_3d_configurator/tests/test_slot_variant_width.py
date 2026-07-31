# SPDX-License-Identifier: LGPL-3.0-only
"""Kitchen Templates resolver — fallback-rung variant width fix.

Required reading: V5-partc.md live evidence (2026-07-30) — production
designs 107 (line 9083) and 108 (line 9107) both persisted a 24.00"-wide
cabinet for a slot whose nominal width was 30". Root cause:
`_resolve_slot()`'s last-resort "cabinet_type + template nominal width"
rung (models/kitchen_template.py) matched a candidate TEMPLATE on width
(e.g. SB-BASE-2DR, template-level `southbrook_width_in`=30.00) and then
took `.product_variant_id` — Odoo's arbitrary "first" materialized
variant of that template — completely blind to THAT variant's own width.
SB-BASE-2DR carries a 24in variant; the resolver could hand that back
for a 30in slot.

Fix: once the template is chosen (that selection is untouched — a
separate DATA decision per the task brief; the alphabetical tie-break
among several width-matching templates like SB-BASE-2DR/SB-VANITY/SB30
is deliberately left alone), pick the variant among its
`product_variant_ids` whose OWN width (`placed_width_in`, the same
SBK-024 helper `_slot_fixed_width`/`action_instantiate` already use for
this identical class of bug) best matches the slot's nominal width.
Exact match wins outright; else the closest; a genuine tie keeps the
variants' own stable/original ordering so results never flap between
runs.

This module pins that behaviour directly against a SYNTHETIC multi-width
cabinet family (same rationale as test_placed_width_in.py and
test_corner_price_resolution.py's synthetic fixtures) — decoupled from
whatever the real catalog's data happens to look like on any given DB.
The synthetic templates' NOMINAL (template-level) widths are deliberately
chosen well outside the real shipped catalog's base-cabinet range (15/18/
24/30/33in, per data/canonical_catalog_tag.xml + data/demo_cabinets.xml)
so the fallback rung's own `southbrook_is_cabinet` + `southbrook_
cabinet_type` + width search can only ever match the fixture just
created, never a real catalog neighbour — these tests must stay green
regardless of catalog data drift.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestSlotVariantWidth(TransactionCase):

    _code_seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The attribute name MUST be exactly "Width": `placed_width_in`
        # (kitchen_design.py) finds a variant's own width by filtering
        # `attribute_id.name == "Width"`. A decorated fixture name like
        # "Width T-SLOTWIDTH" silently misses that filter, so every
        # variant falls back to the TEMPLATE nominal, every candidate
        # scores an identical delta, and the tie-break hands back the
        # first variant — i.e. the fixture reproduces the pre-fix bug
        # and the test fails against correct code. Values below stay
        # uniquely named/sized, and TransactionCase rolls the whole
        # fixture back, so sharing the plain "Width" name with the real
        # catalog attribute is safe.
        cls.width_attr = cls.env["product.attribute"].create({
            "name": "Width", "create_variant": "always",
        })

    @classmethod
    def _mk_width_value(cls, inches):
        return cls.env["product.attribute.value"].create({
            "name": "%g in" % inches, "attribute_id": cls.width_attr.id,
            "value_inches": inches, "value_mm": int(round(inches * 25.4)),
        })

    @classmethod
    def _mk_multi_width_tmpl(cls, name, nominal_width_in, widths_in,
                              cabinet_type="base"):
        """A cabinet CATALOG template (product.template) whose TEMPLATE-
        level `southbrook_width_in` is `nominal_width_in` (what the
        fallback rung's template search matches on) but whose
        materialized VARIANTS each carry their own, independent "Width"
        PTAV — exactly the SB-BASE-2DR production shape. Returns
        `(tmpl, {width: variant})`.
        """
        tmpl = cls.env["product.template"].create({
            "name": name, "type": "consu", "list_price": 300.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": cabinet_type,
            "southbrook_width_in": nominal_width_in,
            "southbrook_height_in": 34.5, "southbrook_depth_in": 24.0,
        })
        values = [cls._mk_width_value(w) for w in widths_in]
        attr_line = cls.env["product.template.attribute.line"].create({
            "product_tmpl_id": tmpl.id,
            "attribute_id": cls.width_attr.id,
            "value_ids": [(6, 0, [v.id for v in values])],
        })
        ptavs = attr_line.product_template_value_ids
        variants_by_width = {}
        for w, v in zip(widths_in, values):
            ptav = ptavs.filtered(
                lambda p, v=v: p.product_attribute_value_id == v)
            variant = tmpl.product_variant_ids.filtered(
                lambda variant, ptav=ptav:
                ptav in variant.product_template_attribute_value_ids)
            variants_by_width[w] = variant
        return tmpl, variants_by_width

    @classmethod
    def _next_code(cls):
        cls._code_seq += 1
        return "T-SLOTWIDTH-%d" % cls._code_seq

    def _mk_kitchen_template(self):
        """The KITCHEN template (`southbrook.kitchen.template`) that
        hosts the slot under test — unrelated to the cabinet CATALOG
        template(s) `_mk_multi_width_tmpl` builds. `_resolve_slot` is
        called as a method on this record, but only ever reads `self.
        code` (for its log line) and `self.env` — never the slot's own
        `template_id` — so any kitchen template will do."""
        return self.env["southbrook.kitchen.template"].create({
            "name": "Resolver host", "code": self._next_code(),
            "layout_shape": "straight",
        })

    def _mk_slot(self, ktpl, nominal_width_in, cabinet_type="base",
                 product_id=None):
        vals = {
            "template_id": ktpl.id, "slot_code": "S1",
            "cabinet_type": cabinet_type, "wall": "back", "run_seq": 10,
            "nominal_width_in": nominal_width_in,
        }
        if product_id:
            vals["product_id"] = product_id
        return self.env["southbrook.kitchen.template.line"].create(vals)

    def test_fallback_rung_picks_matching_width_variant_not_arbitrary_one(self):
        """The live production defect, reproduced: a slot whose nominal
        matches the TEMPLATE's width must resolve to THAT width's own
        VARIANT, never an arbitrary sibling — regardless of which
        variant Odoo would hand back as `.product_variant_id` (the
        pre-fix arbitrary pick)."""
        catalog_tmpl, variants = self._mk_multi_width_tmpl(
            "T-SW Multi-Width Base", 44.0, [38.0, 41.0, 44.0])
        # Sanity: prove `.product_variant_id` (the pre-fix code path) is
        # NOT the 44in variant on this fixture, so a pass below can only
        # be explained by the fix actually scoring widths, not by luck.
        self.assertNotEqual(
            catalog_tmpl.product_variant_id.id, variants[44.0].id,
            "fixture must reproduce the arbitrary-pick hazard")
        ktpl = self._mk_kitchen_template()
        slot = self._mk_slot(ktpl, 44.0)
        resolved = ktpl._resolve_slot(slot, 44.0)
        self.assertEqual(resolved.id, variants[44.0].id)

    def test_exact_match_wins_over_a_closer_but_not_equal_variant(self):
        """An exact-width variant must win even when a numerically very
        close (but not equal) sibling exists — the resolver must not
        settle for "close enough" when a true match is available."""
        catalog_tmpl, variants = self._mk_multi_width_tmpl(
            "T-SW Exact-vs-Close Base", 54.0, [53.9, 54.0])
        ktpl = self._mk_kitchen_template()
        slot = self._mk_slot(ktpl, 54.0)
        resolved = ktpl._resolve_slot(slot, 54.0)
        self.assertEqual(resolved.id, variants[54.0].id)

    def test_no_exact_match_picks_the_closest_width(self):
        """No variant matches the nominal width exactly -> the closest
        one (62in, distance 2, beats 56in's distance 8 and 68in's
        distance 4) is chosen."""
        catalog_tmpl, variants = self._mk_multi_width_tmpl(
            "T-SW Closest Base", 64.0, [56.0, 62.0, 68.0])
        ktpl = self._mk_kitchen_template()
        slot = self._mk_slot(ktpl, 64.0)
        resolved = ktpl._resolve_slot(slot, 64.0)
        self.assertEqual(resolved.id, variants[62.0].id)

    def test_explicit_product_pin_bypasses_fallback_entirely(self):
        """Precedence unchanged: a slot with a hard product pin resolves
        to that exact pinned product and never reaches the width-scoring
        fallback rung at all — even when a multi-width template that
        would otherwise mismatch also exists in the catalog."""
        catalog_tmpl, variants = self._mk_multi_width_tmpl(
            "T-SW Pin-Bypass Base", 74.0, [68.0, 71.0, 74.0])
        pinned = variants[68.0]  # deliberately the "wrong" width
        ktpl = self._mk_kitchen_template()
        slot = self._mk_slot(ktpl, 74.0, product_id=pinned.id)
        resolved = ktpl._resolve_slot(slot, 74.0)
        self.assertEqual(resolved.id, pinned.id)
