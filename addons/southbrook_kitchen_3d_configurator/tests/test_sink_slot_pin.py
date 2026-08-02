# SPDX-License-Identifier: LGPL-3.0-only
"""Catalog decision (2026-08-02) — SINK slot pinning to SB30.

Ground truth: every SINK-coded slot in the shipped T4 template catalog
(data/kitchen_templates.xml) was UNPINNED, so `_resolve_slot`'s
last-resort fallback rung (cabinet_type + template nominal width) could
land a SINK slot on ANY 30in-nominal base-cabinet template's variant —
in this catalog that's ambiguous between SB30 (the real $195 sink base)
and SB-BASE-2DR (a $395 24in-variant double-door base that also carries
a 30in-nominal template). Catalog decision: SINK always means SB30,
enforced by a hard `product_id` pin via the idempotent
`_pin_sink_slots_to_sb30` seeder (called from kitchen_templates.xml's
own `<function>` on every -u).

Covers:
  * the seeder itself — pins only unset SINK slots, never overwrites a
    hand pin, is idempotent;
  * the real shipped catalog — every SINK slot across all 8 kitchen
    templates carries the SB30 pin after install;
  * the resolver — `_resolve_slot` returns SB30's own variant for a
    SINK slot (hard-pin precedence, unaffected by the fallback rung).
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestSinkSlotPin(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Line = cls.env["southbrook.kitchen.template.line"]
        cls.sb30 = cls.env["product.product"].search(
            [("default_code", "=", "SB30")], limit=1)

    def setUp(self):
        super().setUp()
        if not self.sb30:
            self.skipTest(
                "SB30 not seeded on this DB (demo_cabinets.xml not "
                "loaded) — the pin seeder has nothing to resolve to.")

    def test_every_shipped_sink_slot_pinned_to_sb30(self):
        """After install, every SINK-coded slot in the real shipped
        catalog carries the SB30 hard pin (not left to the fallback
        rung)."""
        sink_slots = self.Line.search([("slot_code", "=", "SINK")])
        self.assertTrue(
            sink_slots, "expected at least one shipped SINK slot")
        for slot in sink_slots:
            self.assertEqual(
                slot.product_id.id, self.sb30.id,
                "SINK slot id=%s (template %s) must be pinned to SB30, "
                "got %r" % (
                    slot.id, slot.template_id.code,
                    slot.product_id.default_code))

    def test_resolver_returns_sb30_for_a_sink_slot(self):
        """`_resolve_slot` honours the pin (precedence: hard pin wins)
        and hands back SB30's own 30in variant — never a same-width
        sibling from a different template."""
        sink_slot = self.Line.search(
            [("slot_code", "=", "SINK")], limit=1)
        if not sink_slot:
            self.skipTest("no shipped SINK slot found.")
        ktpl = sink_slot.template_id
        resolved = ktpl._resolve_slot(sink_slot, 30.0)
        self.assertEqual(resolved.id, self.sb30.id)
        self.assertEqual(resolved.default_code, "SB30")
        self.assertEqual(resolved.lst_price, 195.00)

    def test_seeder_is_idempotent_and_does_not_overwrite_a_hand_pin(self):
        ktpl = self.env["southbrook.kitchen.template"].create({
            "name": "Sink-pin seeder probe", "code": "T-SINKPIN-PROBE",
            "layout_shape": "straight",
        })
        other_product = self.env["product.product"].search(
            [("default_code", "!=", "SB30"), ("default_code", "!=", False)],
            limit=1)
        self.assertTrue(
            other_product, "need at least one non-SB30 coded product "
            "on this DB to prove a hand pin survives")

        unpinned = self.Line.create({
            "template_id": ktpl.id, "slot_code": "SINK",
            "cabinet_type": "base", "wall": "back", "run_seq": 10,
            "nominal_width_in": 30.0,
        })
        hand_pinned = self.Line.create({
            "template_id": ktpl.id, "slot_code": "SINK",
            "cabinet_type": "base", "wall": "back", "run_seq": 20,
            "nominal_width_in": 30.0, "product_id": other_product.id,
        })

        pinned_count = self.Line._pin_sink_slots_to_sb30()
        self.assertGreaterEqual(pinned_count, 1)

        self.assertEqual(unpinned.product_id.id, self.sb30.id,
                          "the previously-unpinned slot must now be pinned")
        self.assertEqual(
            hand_pinned.product_id.id, other_product.id,
            "a slot that already carried a hand pin must never be "
            "overwritten by the seeder")

        # Idempotent — a second run changes nothing further for this
        # already-settled pair.
        second = self.Line._pin_sink_slots_to_sb30()
        self.assertEqual(unpinned.product_id.id, self.sb30.id)
        self.assertEqual(hand_pinned.product_id.id, other_product.id)
        self.assertNotIn(unpinned.id, self.Line.search(
            [("slot_code", "=", "SINK"), ("product_id", "=", False)]).ids)
        self.assertGreaterEqual(second, 0)
