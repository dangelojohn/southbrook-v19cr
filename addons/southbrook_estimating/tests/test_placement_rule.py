# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.placement.rule — M1 rules-as-data layer
(docs/research/corner-engine/09-rule-engine-spec.md §6-7).

Covers: the hand-rolled payload constraint (junction-anchor required
keys, min-leg >= leg, host_leg enum), engine_dicts()'s plain-dict
bridge shape, and that the 3 M1 seed rules land correctly via the
data/placement_rules.xml -> _seed_default_rules() install path.
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


_VALID_JUNCTION_PAYLOAD = {
    "leg_x_mm": 914.0,
    "leg_z_mm": 914.0,
    "height_mm": 876.0,
    "min_leg_x_mm": 1143.0,
    "min_leg_z_mm": 1143.0,
}


@tagged("post_install", "-at_install", "southbrook", "southbrook_placement_rule")
class TestPlacementRule(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env["southbrook.placement.rule"]
        cls.tmpl = cls.env.ref("southbrook_estimating.corner")

    def test_constraint_rejects_payload_missing_leg_x_mm(self):
        payload = dict(_VALID_JUNCTION_PAYLOAD)
        del payload["leg_x_mm"]
        with self.assertRaises(ValidationError):
            self.Rule.create({
                "name": "Missing leg_x_mm",
                "product_tmpl_id": self.tmpl.id,
                "corner_type_id": "diagonal-corner-lazy-susan",
                "anchor_class": "junction",
                "tier": "base",
                "payload": payload,
            })

    def test_constraint_rejects_invalid_host_leg(self):
        payload = dict(_VALID_JUNCTION_PAYLOAD, host_leg="q")
        with self.assertRaises(ValidationError):
            self.Rule.create({
                "name": "Bad host_leg",
                "product_tmpl_id": self.tmpl.id,
                "corner_type_id": "diagonal-corner-lazy-susan",
                "anchor_class": "junction",
                "tier": "base",
                "payload": payload,
            })

    def test_constraint_rejects_min_leg_below_leg(self):
        payload = dict(_VALID_JUNCTION_PAYLOAD, min_leg_x_mm=500.0)
        with self.assertRaises(ValidationError):
            self.Rule.create({
                "name": "min_leg_x_mm too small",
                "product_tmpl_id": self.tmpl.id,
                "corner_type_id": "diagonal-corner-lazy-susan",
                "anchor_class": "junction",
                "tier": "base",
                "payload": payload,
            })

    def test_constraint_accepts_valid_junction_payload(self):
        rule = self.Rule.create({
            "name": "Valid junction rule",
            "product_tmpl_id": self.tmpl.id,
            "corner_type_id": "diagonal-corner-lazy-susan",
            "anchor_class": "junction",
            "tier": "base",
            "payload": _VALID_JUNCTION_PAYLOAD,
        })
        self.assertTrue(rule.id)
        self.assertEqual(rule.payload["leg_x_mm"], 914.0)

    def test_constraint_skips_non_junction_anchor_classes(self):
        # anchor_class=free carries no required junction keys — an
        # empty-ish payload must not raise.
        rule = self.Rule.create({
            "name": "Free-anchored, no junction keys required",
            "product_tmpl_id": self.tmpl.id,
            "anchor_class": "free",
            "tier": "base",
            "payload": {"note": "island cabinet"},
        })
        self.assertTrue(rule.id)

    def test_engine_dicts_returns_sku_sequence_and_payload_keys(self):
        rule = self.Rule.create({
            "name": "engine_dicts fixture",
            "product_tmpl_id": self.tmpl.id,
            "corner_type_id": "diagonal-corner-lazy-susan",
            "anchor_class": "junction",
            "tier": "base",
            "sequence": 42,
            "payload": _VALID_JUNCTION_PAYLOAD,
        })
        dicts = rule.engine_dicts()
        self.assertEqual(len(dicts), 1)
        d = dicts[0]
        self.assertEqual(d["rule_id"], "spr-%d" % rule.id)
        self.assertEqual(d["sku"], self.tmpl.default_code)
        self.assertEqual(d["sku"], "SB-CORNER")
        self.assertEqual(d["tier"], "base")
        self.assertEqual(d["sequence"], 42)
        for key in ("leg_x_mm", "leg_z_mm", "height_mm",
                    "min_leg_x_mm", "min_leg_z_mm"):
            self.assertEqual(d[key], _VALID_JUNCTION_PAYLOAD[key])

    def test_seeded_susan_base_corner_rule(self):
        rule = self.Rule.search([
            ("corner_type_id", "=", "diagonal-corner-lazy-susan"),
            ("tier", "=", "base"),
        ], limit=1)
        self.assertTrue(rule, "seed rule 1 (diagonal susan) missing")
        self.assertEqual(rule.sequence, 10)
        self.assertEqual(rule.anchor_class, "junction")
        # Task 3 (corner-remediation) — inch-exact mm: 914.4 = 36in.
        self.assertEqual(rule.payload["leg_x_mm"], 914.4)
        self.assertEqual(rule.payload["leg_z_mm"], 914.4)
        self.assertEqual(rule.payload["height_mm"], 876.0)
        self.assertEqual(rule.payload["min_leg_x_mm"], 1143.0)
        self.assertEqual(rule.payload["min_leg_z_mm"], 1143.0)

    def test_seeded_blind_base_corner_rule(self):
        rule = self.Rule.search([
            ("corner_type_id", "=", "blind-corner-basic"),
            ("tier", "=", "base"),
        ], limit=1)
        self.assertTrue(rule, "seed rule 2 (blind corner) missing")
        self.assertEqual(rule.sequence, 20)
        self.assertEqual(rule.anchor_class, "junction")
        # Task 3 — inch-exact mm: 609.6 = 24in.
        self.assertEqual(rule.payload["leg_x_mm"], 609.6)
        self.assertEqual(rule.payload["leg_z_mm"], 1143.0)
        # M3 — blind seed carries the 3in filler; min_leg_x covers
        # leg + filler (685.8 = 609.6 + 76.2, inch-exact per Task 3).
        self.assertEqual(rule.payload["filler_x_mm"], 76.2)
        self.assertEqual(rule.payload["min_leg_x_mm"], 685.8)
        self.assertEqual(rule.payload["min_leg_z_mm"], 1371.6)
        self.assertEqual(rule.payload["host_leg"], "z")

    def test_seeded_pie_cut_wall_corner_rule(self):
        rule = self.Rule.search([
            ("corner_type_id", "=", "pie-cut-wall-corner"),
            ("tier", "=", "wall"),
        ], limit=1)
        self.assertTrue(rule, "seed rule 3 (pie-cut wall corner) missing")
        self.assertEqual(rule.sequence, 10)
        self.assertEqual(rule.anchor_class, "junction")
        # Task 3 — inch-exact mm: 609.6 = 24in.
        self.assertEqual(rule.payload["leg_x_mm"], 609.6)
        self.assertEqual(rule.payload["leg_z_mm"], 609.6)
        self.assertEqual(rule.payload["height_mm"], 762.0)
        self.assertEqual(rule.payload["min_leg_x_mm"], 762.0)
        self.assertEqual(rule.payload["min_leg_z_mm"], 762.0)

    def test_seed_is_idempotent(self):
        # Re-running the seeder (as -u would) must not create duplicates.
        before = self.Rule.search_count([])
        self.Rule._seed_default_rules()
        after = self.Rule.search_count([])
        self.assertEqual(before, after)

    def _find_susan(self):
        return self.Rule.search([
            ("corner_type_id", "=", "diagonal-corner-lazy-susan"),
            ("tier", "=", "base"),
        ], limit=1)

    def _find_blind(self):
        return self.Rule.search([
            ("corner_type_id", "=", "blind-corner-basic"),
            ("tier", "=", "base"),
        ], limit=1)

    def test_seed_upgrades_legacy_payload_in_place(self):
        # Task 3 (corner-remediation, 2026-07-30) — a rule still carrying
        # the exact pre-Task-3 M4 factory-state payload (round-metric
        # 914.0mm legs) is factory-state and must be upgraded in place
        # to the inch-exact 914.4mm payload.
        rule = self._find_susan()
        self.assertTrue(rule, "seed rule 1 (diagonal susan) missing")
        rule.payload = {
            "leg_x_mm": 914.0, "leg_z_mm": 914.0, "height_mm": 876.0,
            "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0,
            "clearance_front_mm": 500.0,
        }
        self.Rule._seed_default_rules()
        upgraded = self._find_susan()
        self.assertEqual(upgraded.payload["leg_x_mm"], 914.4)
        self.assertEqual(upgraded.payload["leg_z_mm"], 914.4)

    def test_seed_does_not_overwrite_hand_edited_payload(self):
        # A payload that does NOT exactly match any historical legacy
        # version (simulating a human hand-edit, e.g. a shop that
        # special-ordered a 900mm susan) must be left alone.
        rule = self._find_susan()
        self.assertTrue(rule, "seed rule 1 (diagonal susan) missing")
        hand_edited = {
            "leg_x_mm": 900.0, "leg_z_mm": 900.0, "height_mm": 876.0,
            "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0,
            "clearance_front_mm": 500.0,
        }
        rule.payload = hand_edited
        self.Rule._seed_default_rules()
        untouched = self._find_susan()
        self.assertEqual(untouched.payload, hand_edited)

    def test_seed_upgrade_then_rerun_is_noop(self):
        # Idempotency both ways: the upgrade fires exactly once (first
        # run), and a second run against the already-upgraded payload
        # changes nothing.
        rule = self._find_blind()
        self.assertTrue(rule, "seed rule 2 (blind corner) missing")
        rule.payload = {
            "leg_x_mm": 610.0, "leg_z_mm": 1143.0, "height_mm": 876.0,
            "filler_x_mm": 76.2, "min_leg_x_mm": 686.2,
            "min_leg_z_mm": 1372.0, "host_leg": "z",
            "clearance_front_mm": 450.0,
        }
        self.Rule._seed_default_rules()
        first = self._find_blind().payload
        self.assertEqual(first["leg_x_mm"], 609.6)
        self.assertEqual(first["min_leg_x_mm"], 685.8)
        self.assertEqual(first["min_leg_z_mm"], 1371.6)
        self.Rule._seed_default_rules()
        second = self._find_blind().payload
        self.assertEqual(second, first)
