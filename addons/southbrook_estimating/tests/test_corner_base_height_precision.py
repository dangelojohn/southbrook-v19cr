# SPDX-License-Identifier: LGPL-3.0-only
"""Task 4 (2026-07-31, height-drift) — the last survivor of the
2026-07-30 corner-remediation "unit drift" class: base corner cabinets
persisted a height of 34.49" while every other base cabinet in the
catalogue is 34.50". Root cause was `_CORNER_HEIGHT_MM["base"]` /
`_SEED_RULES`' base-corner `height_mm` carrying the round-metric
876.0mm (34.488", rounds to 34.49") instead of the inch-exact 876.3mm
(25.4 * 34.5). The wall value (762.0mm = 25.4 * 30, exactly 30.00")
was already correct and is untouched by this fix.

Covers: the fixed constant is inch-exact, a resolved/derived base
corner's persisted height now rounds to 34.50 (not 34.49) both via
the legacy no-rule fallback and via the real seeded placement-rule
pipeline, the wall corner stays at exactly 30.00", and the placement-
rule seeder's legacy-payload upgrade recognises a LIVE 876.0mm
production row (post-Task-3 inch-exact legs, pre-Task-4 round-metric
height) as factory-state and self-heals it to 876.3 without touching
a genuinely hand-edited payload.
"""
from odoo.tests.common import TransactionCase, tagged
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E
from odoo.addons.southbrook_estimating.models.placement_rule import (
    _SEED_PAYLOADS_LEGACY,
)

_MM_PER_IN = 25.4


def _mm_to_in(mm):
    # Same 2dp convention used throughout placement_rule.py /
    # kitchen_layout_engine.py's corner-remediation comments
    # (25.4mm/in, round to 2 decimal places).
    return round(mm / _MM_PER_IN, 2)


@tagged("post_install", "-at_install", "southbrook", "southbrook_corner_height")
class TestCornerBaseHeightPrecision(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env["southbrook.placement.rule"]

    # ── 1. the constant itself ──────────────────────────────────────
    def test_corner_height_mm_base_is_inch_exact(self):
        self.assertEqual(E._CORNER_HEIGHT_MM["base"], 876.3)
        self.assertEqual(_mm_to_in(E._CORNER_HEIGHT_MM["base"]), 34.5)

    def test_corner_height_mm_wall_unchanged_and_inch_exact(self):
        # This fix must NOT touch the wall value — it was already
        # correct (762.0mm = 25.4 * 30 exactly).
        self.assertEqual(E._CORNER_HEIGHT_MM["wall"], 762.0)
        self.assertEqual(_mm_to_in(E._CORNER_HEIGHT_MM["wall"]), 30.0)

    def test_old_876_0_value_really_did_round_to_the_wrong_inch(self):
        # Pins the defect itself: the PRIOR constant genuinely rounds
        # to 34.49, not 34.50 — this was a real, not cosmetic, bug.
        self.assertEqual(_mm_to_in(876.0), 34.49)
        self.assertNotEqual(_mm_to_in(876.0), 34.5)

    # ── 2. a resolved/derived base corner persists the fixed height ──
    ROOM = {"width_mm": 5080.0, "depth_mm": 5080.0, "height_mm": 2438.0}

    @staticmethod
    def _cab(i, wall, seq, layer="base"):
        t = "wall" if layer == "wall" else "base"
        return {"id": "%s-%s-%d" % (t, wall, i), "width_mm": 610.0,
                "height_mm": 762.0 if layer == "wall" else 876.3,
                "depth_mm": 305.0 if layer == "wall" else 610.0,
                "family": t, "cabinet_type": t,
                "zone": "wall" if layer == "wall" else "base_run",
                "wall": wall, "run_seq": seq}

    def _l_kitchen(self):
        return ([self._cab(i, "back", i) for i in range(3)]
                + [self._cab(i, "left", i) for i in range(3)]
                + [self._cab(i, "back", i, "wall") for i in range(3)]
                + [self._cab(i, "left", i, "wall") for i in range(3)])

    def test_legacy_fallback_corner_persists_34_50_not_34_49(self):
        # No corner_rules given -> the engine's legacy-square fallback
        # path, which reads straight from _CORNER_HEIGHT_MM.
        r = E.resolve_and_layout(self._l_kitchen(), self.ROOM,
                                 auto_assign=False)
        base = {n["id"]: n for n in r["inserted"]}["corner-back-left-base"]
        self.assertEqual(base["height_mm"], 876.3)
        self.assertEqual(_mm_to_in(base["height_mm"]), 34.5)

    def test_wall_corner_persists_exactly_30_00(self):
        r = E.resolve_and_layout(self._l_kitchen(), self.ROOM,
                                 auto_assign=False)
        wall = {n["id"]: n for n in r["inserted"]}["corner-back-left-wall"]
        self.assertEqual(wall["height_mm"], 762.0)
        self.assertEqual(_mm_to_in(wall["height_mm"]), 30.0)

    def test_seeded_rule_driven_corner_persists_34_50(self):
        # Same assertion through the REAL Odoo rule pipeline: the
        # installed southbrook.placement.rule seed -> engine_dicts() ->
        # resolve_and_layout(corner_rules=...) — the exact path
        # production design lines are built from.
        rules = self.Rule.search([("tier", "=", "base")]).engine_dicts()
        self.assertTrue(rules, "no base-tier placement rules seeded")
        r = E.resolve_and_layout(self._l_kitchen(), self.ROOM,
                                 auto_assign=False, corner_rules=rules)
        base = {n["id"]: n for n in r["inserted"]}["corner-back-left-base"]
        self.assertEqual(_mm_to_in(base["height_mm"]), 34.5)
        self.assertNotEqual(_mm_to_in(base["height_mm"]), 34.49)

    # ── 3. the seeder's legacy-payload upgrade ──────────────────────
    def _find_susan(self):
        return self.Rule.search([
            ("corner_type_id", "=", "diagonal-corner-lazy-susan"),
            ("tier", "=", "base"),
        ], limit=1)

    def test_legacy_upgrade_recognises_current_876_0_production_payload(self):
        # This is the exact payload shape a LIVE production row carries
        # right now (post-Task-3 inch-exact 914.4mm legs, pre-Task-4
        # round-metric 876.0mm height): it must be listed in
        # _SEED_PAYLOADS_LEGACY so the seeder self-heals it on the next
        # -u instead of treating it as a hand-edited payload.
        current_prod_payload = {
            "leg_x_mm": 914.4, "leg_z_mm": 914.4, "height_mm": 876.0,
            "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0,
            "clearance_front_mm": 500.0,
        }
        legacy = _SEED_PAYLOADS_LEGACY[("diagonal-corner-lazy-susan", "base")]
        self.assertIn(current_prod_payload, legacy)

        rule = self._find_susan()
        self.assertTrue(rule, "seed rule 1 (diagonal susan) missing")
        rule.payload = current_prod_payload
        self.Rule._seed_default_rules()
        upgraded = self._find_susan()
        self.assertEqual(upgraded.payload["height_mm"], 876.3)
        self.assertEqual(upgraded.payload["leg_x_mm"], 914.4)
        self.assertEqual(_mm_to_in(upgraded.payload["height_mm"]), 34.5)

    def test_legacy_upgrade_does_not_clobber_hand_edited_payload(self):
        # A payload that matches NO legacy version (simulating a shop
        # that special-ordered a taller susan) must be left alone by
        # the height-drift upgrade too — the whole point of the
        # exact-match list is to distinguish real customisation from
        # factory state.
        rule = self._find_susan()
        self.assertTrue(rule, "seed rule 1 (diagonal susan) missing")
        hand_edited = {
            "leg_x_mm": 914.4, "leg_z_mm": 914.4, "height_mm": 900.0,
            "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0,
            "clearance_front_mm": 500.0,
        }
        rule.payload = hand_edited
        self.Rule._seed_default_rules()
        untouched = self._find_susan()
        self.assertEqual(untouched.payload, hand_edited)

    def test_seed_upgrade_then_rerun_is_noop(self):
        # Idempotency: the height upgrade fires exactly once (first
        # run), and a second run against the already-upgraded payload
        # changes nothing further.
        rule = self._find_susan()
        self.assertTrue(rule, "seed rule 1 (diagonal susan) missing")
        rule.payload = {
            "leg_x_mm": 914.4, "leg_z_mm": 914.4, "height_mm": 876.0,
            "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0,
            "clearance_front_mm": 500.0,
        }
        self.Rule._seed_default_rules()
        first = self._find_susan().payload
        self.assertEqual(first["height_mm"], 876.3)
        self.Rule._seed_default_rules()
        second = self._find_susan().payload
        self.assertEqual(second, first)
