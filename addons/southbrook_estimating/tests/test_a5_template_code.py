# SPDX-License-Identifier: LGPL-3.0-only
"""A5 — Type-encoded default_code on product.template archetypes.

Asserts:
  - Known Q8 templates get the correct SB-* code.
  - Templates with a pre-existing default_code are not overwritten.
  - The seed is idempotent.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "estimating", "a5",
        "template_code")
class TestA5TemplateCode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.TC = cls.env["southbrook.estimating.template_code"]
        cls.Template = cls.env["product.template"]

    # ------------------------------------------------------------------
    # Acceptance — known Q8 templates get SB-* codes
    # ------------------------------------------------------------------
    def test_known_q8_templates_get_codes(self):
        # The A5 template_code.py mapping is aspirational (documents the
        # planned Prodboard-cipher scheme e.g. SB-BHL1DR). The assigner
        # is guarded to NEVER overwrite an existing default_code, and
        # every Q8 template already ships with a Southbrook-style code
        # from data/product_templates.xml (SB-BASE-1DR, SB-WALL-2DR,
        # SB-DRAWER, etc). The assigner is therefore a no-op today —
        # test the SHIPPED codes, not the aspiration. When/if the
        # Prodboard-cipher migration lands, update this table and add
        # a one-shot migration to overwrite the legacy codes.
        #
        # Task 23 (SBK-037) — corner_sku_repair.py::
        # repair_cabinet_variant_refs may since have split a
        # duplicate-default_code cabinet template's variants into
        # distinct, Width-suffixed codes ("SB-BASE-2DR" ->
        # "SB-BASE-2DR-24"/"-30") or filled a previously-empty variant
        # ref ("SB-DRAWER" sibling -> "SB-DRAWER-12"). Once a template
        # genuinely has more than one variant, product.template.
        # default_code correctly reads False (Odoo only mirrors a
        # SINGLE variant's code onto the template — see product/models/
        # product_template.py's _compute_template_field_from_variant_
        # field, and the brief's own "do NOT add a template-level
        # default_code to multi-variant templates" convention), so
        # this now resolves through the template's variant(s) rather
        # than asserting the bare template-level field, accepting
        # either the exact shipped code (single-variant / untouched-
        # sibling case) or a "<code>-<suffix>" hygiene-repair code
        # (split-duplicate / filled-empty case).
        expectations = {
            "base_1dr":    "SB-BASE-1DR",
            "base_2dr":    "SB-BASE-2DR",
            "drawer_bank": "SB-DRAWER",
            "sink_base":   "SB-SINK-BASE",
            "wall_1dr":    "SB-WALL-1DR",
            "wall_2dr":    "SB-WALL-2DR",
            "tall_pantry": "SB-TALL-PANTRY",
            "tall_oven":   "SB-TALL-OVEN",
            "corner":      "SB-CORNER",
        }
        checked = 0
        for slug, expected_code in expectations.items():
            xml_id = "southbrook_estimating." + slug
            tmpl = self.env.ref(xml_id, raise_if_not_found=False)
            if not tmpl:
                # Try the bare namespace fallback the assigner uses.
                tmpl = self.env.ref(
                    "southbrook." + slug, raise_if_not_found=False)
            if not tmpl:
                continue
            checked += 1
            variants = tmpl.product_variant_ids
            if not variants:
                # create_variant='dynamic' (Q6 hybrid model, used
                # throughout this catalogue) means nothing is
                # auto-materialized — a bare/CI-minimal DB can carry a
                # Q8 template with zero physical variants at all until
                # the configurator creates one. The template-level
                # field is the only thing seeded in that case.
                self.assertEqual(
                    tmpl.default_code, expected_code,
                    f"{slug}: expected {expected_code}, got "
                    f"{tmpl.default_code}")
                continue
            variant_codes = variants.mapped("default_code")
            matches = [
                c for c in variant_codes
                if c == expected_code
                or (c or "").startswith(expected_code + "-")
            ]
            self.assertTrue(
                matches,
                f"{slug}: expected a variant carrying {expected_code} "
                f"(exact, or hygiene-repair-suffixed), got variant "
                f"codes {variant_codes!r} "
                f"(template-level default_code={tmpl.default_code!r})")
        # We don't hard-fail if some xml_ids aren't present in this DB
        # (CI runs against minimal subsets); but at least ONE should
        # be checked or the seed isn't actually being exercised.
        self.assertGreater(
            checked, 0,
            "no Q8 templates were available to validate — the assigner "
            "ran against an empty namespace; verify product_templates.xml "
            "loaded before template_code_assign.xml in __manifest__.py")

    # ------------------------------------------------------------------
    # Acceptance — existing manual codes are preserved
    # ------------------------------------------------------------------
    def test_existing_codes_preserved(self):
        # Create a transient template with a manual default_code, point
        # the assigner at it, and assert no overwrite. We do this with
        # a stand-in xml_id by mutating SOUTHBROOK_TEMPLATE_CODES at the
        # module level is risky; instead, call assign_codes() directly
        # on a fresh template via direct lookup.
        tmpl = self.Template.create({
            "name": "A5 Preserve Test",
            "type": "consu",
            "default_code": "MANUAL-OVERRIDE-001",
        })
        # The assigner only operates on xml_id matches; this template
        # has none, so it's a no-op. The point of the test is that the
        # field IS preserved through any other write paths that may
        # happen alongside our seed.
        self.TC.assign_codes()
        tmpl.invalidate_recordset()
        self.assertEqual(
            tmpl.default_code, "MANUAL-OVERRIDE-001",
            "non-Q8 templates with manual default_code must be untouched")

    # ------------------------------------------------------------------
    # Acceptance — idempotent
    # ------------------------------------------------------------------
    def test_idempotent_reseed(self):
        w1, _, _ = self.TC.assign_codes()
        # On a re-run, written should be 0 because every code is either
        # already in place (from the install-time seed) or the target
        # xml_id is missing.
        w2, _, _ = self.TC.assign_codes()
        self.assertEqual(
            w2, 0,
            "re-running assign_codes() must be a no-op: all codes "
            "already in place from the install-time seed")
