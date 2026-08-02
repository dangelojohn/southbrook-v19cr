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

    def test_engine_dicts_sku_survives_multi_variant_template(self):
        """Task 28 (corner-remediation 2026-07-30) — root-cause regression.

        Live prod evidence: the blind-corner rule's product_tmpl_id had
        gone multi-variant (an LH variant carrying the real default_code
        plus an RH twin with none yet assigned) — Odoo's own
        `_compute_default_code` forces `product.template.default_code`
        to False whenever a template has more than one variant,
        regardless of what any individual variant's code is. Before this
        fix, `engine_dicts()` read that always-False template field
        directly, so a perfectly-matched rule silently reported
        `sku=""`, and the caller (kitchen_design.py) fell back to the
        WRONG hardcoded default corner SKU. `sku` must instead resolve
        through the template's variants — the ONE coded variant here,
        the simplest possible case of `_bare_family_default_code`'s
        rescue (see `test_engine_dicts_prefers_bare_family_code_over_
        repair_suffixed_sibling` below for the case where more than one
        variant is coded).
        """
        multi_tmpl = self.env["product.template"].create({
            "name": "Corner Base · Blind (multi-variant fixture)",
            "list_price": 545.0,
        })
        # A real Hinge Side attribute split (LH/RH) is what actually
        # flips a corner template multi-variant on prod (Task 6, "hand-
        # aware wall corners") — mirrors that shape exactly rather than
        # hand-crafting a second product.product (which Odoo rejects:
        # two variants with no distinguishing attribute collide on the
        # same empty `combination_indices`).
        hinge = self.env["product.attribute"].create(
            {"name": "Hinge Side (T28 fixture)", "create_variant": "always"})
        lh_val = self.env["product.attribute.value"].create(
            {"name": "LH (T28 fixture)", "attribute_id": hinge.id})
        rh_val = self.env["product.attribute.value"].create(
            {"name": "RH (T28 fixture)", "attribute_id": hinge.id})
        self.env["product.template.attribute.line"].create({
            "product_tmpl_id": multi_tmpl.id, "attribute_id": hinge.id,
            "value_ids": [(6, 0, [lh_val.id, rh_val.id])],
        })
        variant_with_code = multi_tmpl.product_variant_ids[:1]
        variant_with_code.default_code = "SB-CORNER-BLIND-FIXTURE"
        # The RH twin mirrors prod's variant 1422 — no code assigned yet.
        multi_tmpl.invalidate_recordset(["default_code"])
        self.assertFalse(
            multi_tmpl.default_code,
            "sanity: Odoo blanks template default_code once >1 variant exists")

        rule = self.Rule.create({
            "name": "Multi-variant blind fixture",
            "product_tmpl_id": multi_tmpl.id,
            "corner_type_id": "blind-corner-basic",
            "anchor_class": "junction",
            "tier": "base",
            "sequence": 20,
            "payload": _VALID_JUNCTION_PAYLOAD,
        })
        d = rule.engine_dicts()[0]
        self.assertEqual(
            d["sku"], "SB-CORNER-BLIND-FIXTURE",
            "engine_dicts() must resolve a real SKU from the variant "
            "that actually carries a code, not silently return ''")

    def test_engine_dicts_prefers_bare_family_code_over_repair_suffixed_sibling(self):
        """Fix-round (review finding 2) — when MORE than one variant of a
        multi-variant template carries a code, `engine_dicts()` must
        deterministically prefer the BARE family code over a repair-
        suffixed sibling on the same template (corner_sku_repair.py's
        "-R" hand-twin convention, e.g. "SB-CORNER-BLIND" bare vs
        "SB-CORNER-BLIND-R"; the same holds for repair_cabinet_variant_
        refs' generic "-<suffix>" dedup convention) — never an
        incidental first-in-recordset-order pick. Runs the pick twice
        with the coded variants created in each order (RH-then-LH and
        LH-then-RH) to prove the result does not depend on creation/
        recordset order, only on which code is bare.
        """
        for create_bare_first in (True, False):
            with self.subTest(create_bare_first=create_bare_first):
                tmpl = self.env["product.template"].create({
                    "name": "Corner Base · Blind (bare-vs-suffixed fixture)",
                    "list_price": 545.0,
                })
                hinge = self.env["product.attribute"].create(
                    {"name": "Hinge Side (T28b fixture)",
                     "create_variant": "always"})
                lh_val = self.env["product.attribute.value"].create(
                    {"name": "LH (T28b fixture)", "attribute_id": hinge.id})
                rh_val = self.env["product.attribute.value"].create(
                    {"name": "RH (T28b fixture)", "attribute_id": hinge.id})
                self.env["product.template.attribute.line"].create({
                    "product_tmpl_id": tmpl.id, "attribute_id": hinge.id,
                    "value_ids": [(6, 0, [lh_val.id, rh_val.id])],
                })
                variants = tmpl.product_variant_ids.sorted("id")
                self.assertEqual(len(variants), 2)
                bare_variant, suffixed_variant = variants[0], variants[1]
                if not create_bare_first:
                    bare_variant, suffixed_variant = (
                        suffixed_variant, bare_variant)
                # Both coded — the bare family code and a repair-suffixed
                # sibling, written in whichever id order this subTest
                # is exercising.
                suffixed_variant.default_code = "SB-CORNER-BLIND-FIXTURE-R"
                bare_variant.default_code = "SB-CORNER-BLIND-FIXTURE"
                tmpl.invalidate_recordset(["default_code"])
                self.assertFalse(tmpl.default_code, "sanity: multi-variant")

                rule = self.Rule.create({
                    "name": "Bare-vs-suffixed fixture rule",
                    "product_tmpl_id": tmpl.id,
                    "corner_type_id": "blind-corner-basic",
                    "anchor_class": "junction",
                    "tier": "base",
                    "sequence": 20,
                    "payload": _VALID_JUNCTION_PAYLOAD,
                })
                d = rule.engine_dicts()[0]
                self.assertEqual(
                    d["sku"], "SB-CORNER-BLIND-FIXTURE",
                    "the bare family code must win over the "
                    "repair-suffixed sibling regardless of variant id "
                    "order, got %r" % d["sku"])

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
        # Task 4 (height-drift) — 876.3 = 25.4 * 34.5 exactly. The prior
        # 876.0 was a round-metric approximation (34.488") that persisted
        # corners at 34.49" while every other base cabinet read 34.50".
        self.assertEqual(rule.payload["height_mm"], 876.3)
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

    def test_seeded_diagonal_last_resort_rule(self):
        """Catalog decision (2026-08-02) — SB-CORNER-DIAG's own
        last-resort rule (previously unreachable — no rule pointed at
        it at all). Skips gracefully when SB-CORNER-DIAG isn't seeded
        on this DB (sibling-module product, same as the blind/wall-
        corner seed rules)."""
        rule = self.Rule.search([
            ("corner_type_id", "=", "open-corner-shelf"),
            ("tier", "=", "base"),
        ], limit=1)
        if not rule:
            self.skipTest(
                "SB-CORNER-DIAG not seeded on this DB — "
                "southbrook_configurator_ux demo data may not be "
                "installed here.")
        self.assertEqual(rule.sequence, 30,
                          "must rank strictly AFTER susan (10) and blind (20)")
        self.assertEqual(rule.anchor_class, "junction")
        self.assertEqual(rule.product_tmpl_id.default_code
                          or self.env["product.product"].search(
                              [("product_tmpl_id", "=",
                                rule.product_tmpl_id.id)], limit=1
                          ).default_code,
                          "SB-CORNER-DIAG")
        # Same real geometry as the susan family (36x36in, 34.5in tall).
        self.assertEqual(rule.payload["leg_x_mm"], 914.4)
        self.assertEqual(rule.payload["leg_z_mm"], 914.4)
        self.assertEqual(rule.payload["height_mm"], 876.3)
        # The defining "last resort" property: min_leg == leg (no
        # swing-clearance margin), strictly smaller than both the susan
        # (1143.0) and the blind's host leg (1371.6) — this rule must be
        # able to fit rooms neither of those two can.
        self.assertEqual(rule.payload["min_leg_x_mm"], 914.4)
        self.assertEqual(rule.payload["min_leg_z_mm"], 914.4)
        susan = self._find_susan()
        self.assertTrue(susan, "susan rule must exist for this comparison")
        self.assertLess(rule.payload["min_leg_x_mm"],
                         susan.payload["min_leg_x_mm"])
        self.assertLess(rule.payload["min_leg_z_mm"],
                         susan.payload["min_leg_z_mm"])
        blind = self._find_blind()
        self.assertTrue(blind, "blind rule must exist for this comparison")
        self.assertLess(rule.payload["min_leg_z_mm"],
                         blind.payload["min_leg_z_mm"])

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
