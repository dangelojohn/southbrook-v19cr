# SPDX-License-Identifier: LGPL-3.0-only
"""Task 4 — corner catalog truth: SB-CORNER-BLIND / SB-CORNER-DIAG /
SB-WALL-CORNER promoted to real SKUs, SB-CORNER renamed to reflect what
it actually is, SB-CORNER-LSUSAN archived as a duplicate.

See models/corner_sku_repair.py::repair_corner_family_skus for the
ground truth citation and .superpowers/sdd/corner-remediation-2026-07-30/
task-4-brief.md for the requirements this covers.

Unlike test_corner_repair.py's ensure_repaired_corner() (which recreates
a known-bad state before repairing, because a bare DB has zero SB-CORNER
variants), these tests run the repair directly against whatever corner
catalog state the target DB already carries — the whole point of
"resolve-then-verify, skip-if-missing" is that the repair is safe to run
from an unknown starting state. On a DB where southbrook_kitchen_3d_
configurator (the southbrook_cabinet_type/southbrook_is_cabinet fields)
isn't installed, the classification/panel-domain assertions degrade to
a skip rather than a false failure.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models import corner_sku_repair
from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    repair_corner_family_skus,
)

_FAMILY_CODES = ("SB-CORNER", "SB-CORNER-BLIND", "SB-CORNER-DIAG",
                  "SB-WALL-CORNER")


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "corner_catalog_truth")
class TestCornerFamilyRepair(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["product.template"]
        cls.Product = cls.env["product.product"]
        cls.has_cabinet_fields = (
            "southbrook_cabinet_type" in cls.Template._fields)
        cls.first_summary = repair_corner_family_skus(cls.env)

    def _tmpl(self, default_code):
        # Same trap the repair itself guards against: product.template
        # .default_code reads back False once the template has more
        # than one variant (Odoo only mirrors it for the single-variant
        # case), so a template-level search alone would go blind for
        # every family member the moment its RH twin gets materialized.
        # Resolve via the still-unique variant code first.
        variant = self.Product.search(
            [("default_code", "=", default_code)], limit=1)
        tmpl = (variant.product_tmpl_id if variant else
                self.Template.search(
                    [("default_code", "=", default_code)], limit=1))
        if not tmpl:
            self.skipTest(
                "%s not seeded on this DB — catalog_expansion / "
                "kitchen_3d_configurator demo data may not be "
                "installed here." % default_code)
        return tmpl

    def test_panel_corner_filter_matches_all_four_families(self):
        """AC: southbrook_cabinet_type='corner' AND southbrook_is_cabinet
        =True matches SB-CORNER + SB-CORNER-BLIND + SB-CORNER-DIAG +
        SB-WALL-CORNER (the exact domain the drag/add/swap panel uses,
        per V3-products.md Claim 4)."""
        if not self.has_cabinet_fields:
            self.skipTest(
                "southbrook_kitchen_3d_configurator not installed — "
                "southbrook_cabinet_type/is_cabinet don't exist here.")
        # Identify by resolved tmpl id, not by default_code: a
        # multi-variant family member's default_code reads back False
        # at the template level (see _tmpl's docstring), so comparing
        # default_code strings against the domain search's .mapped()
        # would silently mismatch for every family member with an RH
        # twin already materialized.
        matched_ids = set(self.Template.search([
            ("southbrook_cabinet_type", "=", "corner"),
            ("southbrook_is_cabinet", "=", True),
        ]).ids)
        for code in _FAMILY_CODES:
            tmpl = self._tmpl(code)
            self.assertIn(
                tmpl.id, matched_ids,
                "%s (tmpl id=%s) must satisfy the panel's Corner filter "
                "domain (southbrook_cabinet_type='corner', "
                "southbrook_is_cabinet=True)" % (code, tmpl.id))

    def test_each_family_member_has_an_rh_variant(self):
        """AC: each has an RH variant — resolved via the attribute-value
        combination (product_variant_count can read 0 even when a
        variant exists, per the Odoo 19 config_ok trap), never by
        assuming the template's default variant_id."""
        for code in ("SB-CORNER-BLIND", "SB-CORNER-DIAG", "SB-WALL-CORNER"):
            tmpl = self._tmpl(code)
            rh_variants = tmpl.product_variant_ids.filtered(
                lambda v: any(
                    ptav.attribute_id.name == "Hinge Side"
                    and (ptav.name or "").startswith("RH")
                    for ptav in v.product_template_attribute_value_ids))
            self.assertTrue(
                rh_variants,
                "%s must have a materialized RH (Hinge Side) variant "
                "after the repair" % code)
            self.assertTrue(
                rh_variants[0].default_code,
                "the RH twin must carry a resolvable default_code")

    def _family_variants(self, tmpl, code):
        """The variants the repair is allowed to MODIFY — the family's
        own seeded rows (family code or its -R twin) or the sole
        variant. Wizard-created dynamic config variants (code-less rows
        on a multi-variant template) are live user data the repair must
        leave alone (prod fact 2026-07-31: user-configured 33in
        blind/diag variants; the old unscoped relink rewrote one into
        an exact duplicate of the seeded row and blocked the deploy),
        so the width-promotion assertions only bind the seeded rows."""
        if len(tmpl.product_variant_ids) == 1:
            return tmpl.product_variant_ids
        ok = {code, code + "-R"}
        return tmpl.product_variant_ids.filtered(
            lambda v: (v.default_code or "") in ok)

    def test_blind_width_promoted_to_45(self):
        """Item 1 — SB-CORNER-BLIND's Width relinks 33->45 (a real,
        correct blind-corner-base width missing from the shared Width
        vocabulary until this repair adds it). Binds the family's own
        seeded rows only — see _family_variants."""
        tmpl = self._tmpl("SB-CORNER-BLIND")
        variants = self._family_variants(tmpl, "SB-CORNER-BLIND")
        self.assertTrue(variants, "SB-CORNER-BLIND must have variants")
        for v in variants:
            widths = v.product_template_attribute_value_ids.filtered(
                lambda p: p.attribute_id.name == "Width")
            self.assertTrue(widths, "every variant must carry a Width")
            self.assertTrue(
                all((w.name or "").startswith("45") for w in widths),
                "no seeded SB-CORNER-BLIND variant may keep a non-45in "
                "width after the repair (got %s)" % widths.mapped("name"))

    def test_diag_width_promoted_to_36(self):
        """Item 2 — SB-CORNER-DIAG's Width relinks 33->36. Binds the
        family's own seeded rows only — see _family_variants."""
        tmpl = self._tmpl("SB-CORNER-DIAG")
        variants = self._family_variants(tmpl, "SB-CORNER-DIAG")
        self.assertTrue(variants, "SB-CORNER-DIAG must have variants")
        for v in variants:
            widths = v.product_template_attribute_value_ids.filtered(
                lambda p: p.attribute_id.name == "Width")
            self.assertTrue(
                all((w.name or "").startswith("36") for w in widths),
                "no seeded SB-CORNER-DIAG variant may keep a non-36in "
                "width after the repair (got %s)" % widths.mapped("name"))

    def test_wall_corner_dims_and_type(self):
        """Item 3 — SB-WALL-CORNER: W24 x H30 x D24, tagged corner (not
        wall) so it satisfies the same Corner panel filter as the other
        three families."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl = self._tmpl("SB-WALL-CORNER")
        self.assertEqual(tmpl.southbrook_cabinet_type, "corner")
        self.assertTrue(tmpl.southbrook_is_cabinet)
        self.assertEqual(tmpl.southbrook_width_in, 24.0)
        self.assertEqual(tmpl.southbrook_height_in, 30.0)
        self.assertEqual(tmpl.southbrook_depth_in, 24.0)

    def test_sb_corner_renamed(self):
        """Item 4 — SB-CORNER (the operative diagonal-lazy-susan
        carcass) is renamed; default_code and any existing SO/design
        lines are untouched (name-only change)."""
        tmpl = self._tmpl("SB-CORNER")
        self.assertEqual(tmpl.name, "Corner Base · Lazy-Susan 36")
        # default_code itself was never written by the repair. Can't
        # assert the TEMPLATE-level field literally equals "SB-CORNER"
        # here — once SB-CORNER has 2 variants (the T4a RH twin), Odoo
        # blanks the template-level mirror by design (see _tmpl's
        # docstring) — so assert on the still-resolvable VARIANT code
        # that _tmpl() itself just used to find this template.
        self.assertTrue(
            self.Product.search([
                ("product_tmpl_id", "=", tmpl.id),
                ("default_code", "=", "SB-CORNER"),
            ], limit=1),
            "a SB-CORNER-coded variant must still resolve to this "
            "template — default_code must never change")

    def test_lsusan_archived(self):
        """Item 5 — SB-CORNER-LSUSAN is archived (active=False), not
        deleted (never destroy data on a repair). Asserts no ACTIVE
        record with this default_code exists (rather than picking one
        arbitrary record) so the panel can never double-list it."""
        any_record = self.Template.with_context(active_test=False).search(
            [("default_code", "=", "SB-CORNER-LSUSAN")], limit=1)
        if not any_record:
            self.skipTest("SB-CORNER-LSUSAN not seeded on this DB.")
        still_active = self.Template.search(
            [("default_code", "=", "SB-CORNER-LSUSAN")])
        self.assertFalse(
            still_active,
            "SB-CORNER-LSUSAN must be archived as a duplicate of "
            "SB-CORNER — found active record(s) %s" % still_active.ids)

    def test_repair_is_idempotent(self):
        """AC: running the repair twice changes nothing on the second
        pass — every counter in the second summary must be zero/false."""
        second = repair_corner_family_skus(self.env)
        self.assertEqual(
            second,
            {"flags_set": 0, "width_values_created": 0, "width_fixed": 0,
             "codes_set": 0, "lh_created": 0, "lh_combo_backfilled": 0,
             "rh_created": 0, "renamed": False, "lsusan_archived": False},
            "second run must be a strict no-op: %s" % second)

    def test_sql_fallback_writes_match_orm_path(self):
        """Code review finding 2 — the raw-SQL fallback branch inside
        _write_cabinet_flags (module-level _write_cabinet_flags_sql)
        only ever executes in PRODUCTION at the exact moment kitchen_3d
        _configurator's fields aren't registered in Template._fields
        yet — a state a TransactionCase test can't reproduce, since the
        full registry (fields included) is always loaded by the time
        any test runs, so `has_cabinet_fields` is always True there and
        the ORM branch is the one that naturally fires. Call the SQL
        path directly — forcing exactly the branch that would otherwise
        have zero CI coverage — and prove it writes the identical
        values the ORM path (`tmpl.write(flags)`) would for the same
        input, including its own idempotency and its unknown-column
        skip guard."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        flags = {
            "southbrook_cabinet_type": "corner",
            "southbrook_is_cabinet": True,
            "southbrook_width_in": 42.0,
            "southbrook_height_in": 33.0,
            "southbrook_depth_in": 22.0,
        }
        orm_tmpl = self.Template.create(
            {"name": "T4 SQL-fallback probe (ORM)"})
        sql_tmpl = self.Template.create(
            {"name": "T4 SQL-fallback probe (SQL)"})

        orm_tmpl.write(flags)

        changed = corner_sku_repair._write_cabinet_flags_sql(
            self.env, sql_tmpl, flags)
        self.assertTrue(
            changed, "SQL fallback must report a change on first write")
        # The SQL path deliberately doesn't invalidate the ORM cache
        # (see its own docstring) — a caller reading the field back via
        # the ORM afterward is responsible for that itself.
        sql_tmpl.invalidate_recordset(list(flags.keys()))

        for field in flags:
            self.assertEqual(
                sql_tmpl[field], orm_tmpl[field],
                "SQL fallback wrote a different %s than the ORM path "
                "for the same input" % field)

        # Idempotent: identical values a second time report no change
        # (the IS DISTINCT FROM guard).
        self.assertFalse(
            corner_sku_repair._write_cabinet_flags_sql(
                self.env, sql_tmpl, flags),
            "re-running the SQL fallback with identical values must "
            "be a no-op")

        # Unknown/fabricated column name is skipped, not attempted —
        # attempting it would raise a SQL error instead of degrading
        # gracefully the way the ORM's `"x" in Template._fields` guard
        # does for the primary path.
        self.assertFalse(
            corner_sku_repair._write_cabinet_flags_sql(
                self.env, sql_tmpl, {"not_a_real_column": True}),
            "an unrecognised column must be skipped, not written")

    def test_lsusan_archive_self_heals_a_reappeared_duplicate(self):
        """Minor (code review) — corroborates the report's self-heal
        claim: if something else (e.g. southbrook_configurator_ux's
        catalog_expansion, on its own next upgrade — see corner_sku_
        repair.py's Item 5 comment) re-mints a fresh ACTIVE
        SB-CORNER-LSUSAN duplicate after this repair already archived
        the original, the NEXT call to the repair archives that one
        too, not just the first one it ever saw."""
        reappeared = self.Template.create({
            "name": "Corner Base · Lazy-Susan",
            "default_code": "SB-CORNER-LSUSAN",
        })
        self.assertTrue(reappeared.active)

        summary = repair_corner_family_skus(self.env)
        self.assertTrue(
            summary["lsusan_archived"],
            "repair must report archiving the reappeared duplicate")
        self.assertFalse(
            reappeared.active,
            "a duplicate created after the first archive must also "
            "get archived on the next repair call")
        self.assertFalse(
            self.Template.search(
                [("default_code", "=", "SB-CORNER-LSUSAN")]),
            "no active SB-CORNER-LSUSAN record may remain")


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "corner_catalog_truth")
class TestRepairCornerSkuWallCornerHotfix(TransactionCase):
    """Deploy-gate hotfix (2026-07-31) — repair_corner_sku()'s own
    SB-WALL-CORNER seed-if-missing check (T4a, top of corner_sku_
    repair.py) used an active-only, template-only search. Once
    repair_corner_family_skus() (chained from the SAME call) gives the
    real template an RH twin, its TEMPLATE-level default_code
    correctly blanks (Odoo only mirrors a single variant) once that
    compute is genuinely persisted and re-read — which is exactly what
    happens when repair_corner_sku() is called a SECOND time with a
    fresh env, as it is on prod/CI (southbrook_estimating's
    19.0.9.1.0 migration, then its 19.0.9.2.0 migration, both calling
    repair_corner_sku in the same upgrade run). The old check then
    found nothing and minted a second "Wall Corner 24" template,
    single-variant, carrying the same bare SB-WALL-CORNER code — the
    deploy-gate-caught duplicate (tmpl 13790 on the gate DB, later
    "adopted"/renamed by catalog_expansion.build_catalog() to look
    like the real one).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env["product.product"]
        cls.Template = cls.env["product.template"]

    def test_second_call_does_not_remint_wall_corner(self):
        from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
            repair_corner_sku,
        )
        repair_corner_sku(self.env)
        real = self.Product.search(
            [("default_code", "=", "SB-WALL-CORNER")], limit=1
        ).product_tmpl_id
        self.assertTrue(real, "the real SB-WALL-CORNER template must resolve")
        self.assertTrue(
            self.Product.search([("default_code", "=", "SB-WALL-CORNER-R")]),
            "sanity: the RH twin must already exist")

        # Force the exact precondition the gate hit: the template-
        # level default_code genuinely blank in the DB. Odoo's own
        # stored compute eventually reaches this once a template has
        # more than one variant, but the EXACT recompute timing is
        # DB-state-dependent (e.g. already "healed" back non-blank by
        # catalog_expansion.build_catalog()'s own end-of-row
        # `tmpl.write({"default_code": sku})` on a DB where that
        # module already ran) and not worth fighting in a test — pin
        # the observable end-state directly via raw SQL, the same
        # technique test_cabinet_variant_ref_repair.py's own
        # test_empty_default_code_filled_from_sibling_when_template_
        # code_blank uses for the identical class of precondition.
        self.env.cr.execute(
            "UPDATE product_template SET default_code = NULL WHERE id = %s",
            (real.id,))
        real.invalidate_recordset(["default_code"])
        self.assertFalse(
            real.default_code,
            "sanity: the real template must read blank — the exact "
            "condition the gate hit")

        second_summary = repair_corner_sku(self.env)
        self.assertFalse(
            second_summary["wall_corner_created"],
            "the second call must not mint a duplicate SB-WALL-CORNER "
            "template — exactly the deploy-gate-caught bug")

        all_wall_corner_tmpl_ids = set(
            self.Product.search([("default_code", "=", "SB-WALL-CORNER")])
            .mapped("product_tmpl_id").ids)
        self.assertEqual(
            all_wall_corner_tmpl_ids, {real.id},
            "no second template carrying SB-WALL-CORNER may exist")

        # Third call for good measure — idempotent, not just "safe
        # exactly once more".
        third_summary = repair_corner_sku(self.env)
        self.assertFalse(third_summary["wall_corner_created"])


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "corner_catalog_truth")
class TestWizardVariantsSurviveRepair(TransactionCase):
    """Deploy-gate regression (2026-07-31, prod): a user configured a
    33in blind and a 33in diagonal corner in the LIVE wizard
    (product.product 1422/1423 — code-less dynamic config variants,
    create_uid a real user). The unscoped stale-width relink treated
    the 33in diag as the known-bad seeded row and rewrote its Width
    PTAV 33->36, minting an exact value-combination duplicate of the
    seeded 36in variant — the OCA duplicate constraint refused it and
    every cold upgrade died at configurator_ux's build_catalog
    self-heal (data/catalog_expansion.xml:13).

    Pins both halves of the fix in repair_corner_family_skus:
    - SCOPING (_repair_eligible): wizard variants — code-less rows on a
      multi-variant template — are never modified, collision or not;
    - COLLISION GUARD (_variant_with_value_set): no rewrite/create may
      ever produce a value combination another variant (active or
      archived) already carries.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env["product.product"]
        cls.PTAV = cls.env["product.template.attribute.value"]
        # Settle the catalog first — seeded rows repaired, twins built.
        repair_corner_family_skus(cls.env)
        variant = cls.Product.search(
            [("default_code", "=", "SB-CORNER-DIAG")], limit=1)
        if not variant:
            return  # individual tests skip below
        cls.diag_tmpl = variant.product_tmpl_id
        cls.diag_seed = variant

    def _ptav(self, tmpl, attr_name, prefix):
        return self.PTAV.search([
            ("product_tmpl_id", "=", tmpl.id),
            ("attribute_id.name", "=", attr_name),
            ("name", "=like", prefix + "%"),
        ], limit=1)

    def _require_diag(self):
        if not getattr(self, "diag_tmpl", False):
            self.skipTest("SB-CORNER-DIAG not seeded on this DB.")
        p33 = self._ptav(self.diag_tmpl, "Width", "33")
        p36 = self._ptav(self.diag_tmpl, "Width", "36")
        if not (p33 and p36):
            self.skipTest("DIAG Width 33/36 PTAVs not present here.")
        return p33, p36

    def test_wizard_variant_colliding_rewrite_is_skipped(self):
        """The exact prod shape: a code-less wizard variant whose combo
        equals the seeded 36in variant's except Width=33. The old code
        rewrote it 33->36 (= exact duplicate of the seed) and raised
        ValidationError; the fixed repair leaves it untouched and
        completes."""
        p33, p36 = self._require_diag()
        seed_combo = self.diag_seed.product_template_attribute_value_ids
        if p36 not in seed_combo:
            self.skipTest("seeded DIAG variant is not on 36in here.")
        wizard_combo = (seed_combo - p36) | p33
        wizard = self.Product.create({
            "product_tmpl_id": self.diag_tmpl.id,
            "product_template_attribute_value_ids":
                [(6, 0, wizard_combo.ids)],
        })
        self.assertFalse(wizard.default_code or "")

        # Must not raise, and must not touch the wizard variant.
        repair_corner_family_skus(self.env)

        self.assertIn(
            p33, wizard.product_template_attribute_value_ids,
            "the user's wizard-configured 33in width must survive the "
            "repair unchanged (old code rewrote it into a duplicate of "
            "the seeded 36in variant)")
        self.assertIn(
            p36, self.diag_seed.product_template_attribute_value_ids,
            "the seeded variant must be untouched")

    def test_wizard_variant_width_never_rewritten_even_without_collision(self):
        """Scoping, not just collision-dodging: a wizard variant whose
        33->36 rewrite would NOT collide with anything must still be
        left alone — user data is out of repair scope entirely."""
        p33, p36 = self._require_diag()
        seed_combo = self.diag_seed.product_template_attribute_value_ids
        if p36 not in seed_combo:
            self.skipTest("seeded DIAG variant is not on 36in here.")
        # Differ from the seed on a second axis too (drop one non-Width
        # value), so the post-rewrite combo would be unique — the old
        # unscoped code WOULD have rewritten this one successfully.
        non_width = (seed_combo - p36).filtered(
            lambda p: p.attribute_id.name != "Hinge Side")[-1:]
        if not non_width:
            self.skipTest("no second attribute axis available here.")
        wizard_combo = (seed_combo - p36 - non_width) | p33
        wizard = self.Product.create({
            "product_tmpl_id": self.diag_tmpl.id,
            "product_template_attribute_value_ids":
                [(6, 0, wizard_combo.ids)],
        })

        repair_corner_family_skus(self.env)

        self.assertIn(
            p33, wizard.product_template_attribute_value_ids,
            "a code-less wizard variant on a multi-variant template is "
            "user data — the width repair must never rewrite it, even "
            "when the rewrite would not collide")

    def test_rh_twin_creation_guarded_against_existing_combo(self):
        """The RH-twin creation must never mint a variant whose value
        combination an existing (even archived) variant already
        carries — same identity the OCA constraint enforces."""
        p33, p36 = self._require_diag()
        rh = self._ptav(self.diag_tmpl, "Hinge Side", "RH")
        if not rh:
            self.skipTest("DIAG RH PTAV not present here.")
        twin = self.Product.search(
            [("default_code", "=", "SB-CORNER-DIAG-R")], limit=1)
        if not twin:
            self.skipTest("RH twin not materialized on this DB.")
        # Archive the twin: detection-by-active-variants goes blind, so
        # only the value-set collision guard stands between the repair
        # and a duplicate create.
        twin.active = False

        repair_corner_family_skus(self.env)  # must not raise

        twins = self.Product.with_context(active_test=False).search([
            ("product_tmpl_id", "=", self.diag_tmpl.id),
            ("default_code", "=", "SB-CORNER-DIAG-R"),
        ])
        self.assertEqual(
            len(twins), 1,
            "no second RH twin may be minted while an archived variant "
            "already carries the combination (got %s)" % twins.ids)
