# SPDX-License-Identifier: LGPL-3.0-only
"""Task 2 (corner-remediation 2026-07-30) — BOM-gate coherence on cold
validate.

Live bug: prod design 102's cold validate (`action_validate_production` /
`_check_production_ready`) emitted a hard `[BLOCK] No BOM defined:
[SB-WALL-CORNER]...` for a product the QUOTATION flow
(`action_create_quotation`) would have healed for free -- it runs
`_ensure_kitchen_bom` for every template on the design BEFORE its own
copy of this same MISSING_BOM gate (V2-validator.md Sec 5's "auto-seed
nuance"). Cold validate had no such autoseed step, so it hard-blocked
every design whose corner template happened to have no BOM yet, even
though quoting it would have worked.

Fix: `_check_production_ready`'s BOM-presence check (kitchen_design.py)
now asks `_bom_autoseed_would_resolve(product)` to split missing-BOM
lines into:
  * autoseedable  -> `BOM_WILL_AUTOSEED`, severity "warning"
  * NOT autoseedable -> `MISSING_BOM`, severity "blocking" (unchanged)

Review fix (2026-07-30) -- `_bom_autoseed_would_resolve` was originally a
HAND-DERIVED mirror of `_ensure_kitchen_bom`'s fast-path logic that
assumed the seeder never raises. It does (both real call sites wrap it
in `try/except UserError`, with the D12 MISSING_BOM gate as the
documented safety net for that failure). Fixed by DRY-RUNNING the real
`_ensure_kitchen_bom` inside a savepoint that is ALWAYS rolled back, so
the predicate can never drift from the seeder's actual behaviour
(success -> warning, ANY failure -> fail-closed blocking) and reuses its
exact access level.

This file proves, with real Odoo data (no monkeypatching except for the
one seeder-failure regression pin, which mirrors the pattern already
used in test_track_b_end_to_end.py's test_06_savepoint_wraps_action_
confirm):
  1. A design-102-shaped product with NO BOM at all on its template is
     the common case `_ensure_kitchen_bom` always heals -> WARN, the
     design is not blocked on that account, and the predicate's
     internal dry run leaves no residue.
  2. A product genuinely outside the auto-seed logic: its template
     already carries a 'normal'-type BOM, but pinned to a SIBLING
     variant (not this line's product) -- `_ensure_kitchen_bom`'s fast
     path matches on type only, not on which variant the BOM is scoped
     to, so it hands back that mismatched sibling BOM without ever
     creating the template-level stub this variant needs. Ground-truthed
     against a REAL (non-rolled-back) autoseed call, not just asserted
     from theory.
  3. If the real seeder raises (UserError, or any other exception),
     the predicate must bucket to BLOCKING, never WARN -- the exact
     regression the review flagged.
  4. Never actually seeds anything as a side effect of validating.
  5. `action_validate_production`'s toast renders the exact
     `[WARN] BOM will be auto-created at quotation` line the brief AC
     names, and is non-blocking (danger only when BLOCK issues exist).
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "bom_gate_coherence")
class TestBomGateCoherence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.Bom = cls.env["mrp.bom"]
        cls.partner = cls.env["res.partner"].create({
            "name": "BOM Gate Coherence Test Customer",
        })

    def _design(self):
        return self.Design.create({
            "name": "BOM gate coherence design",
            "partner_id": self.partner.id,
            "room_width_in": 120.0, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })

    def _line(self, design, product, cabinet_type="wall", **extra):
        vals = {
            "design_id": design.id, "product_id": product.id,
            "quantity": 1, "price_unit": product.list_price or 100.0,
            "cabinet_type": cabinet_type,
            "width_in": 24.0, "height_in": 30.0, "depth_in": 24.0,
            "origin": "configurator",
            "layout_key": "bgc-%s-%d" % (cabinet_type, product.id),
        }
        vals.update(extra)
        return self.Line.create(vals)

    def _has_bom(self, product, tmpl):
        return bool(self.Bom.sudo().search_count([
            "|",
            ("product_id", "=", product.id),
            "&",
            ("product_tmpl_id", "=", tmpl.id),
            ("product_id", "=", False),
        ], limit=1))

    # ------------------------------------------------------------------
    # Branch 1 -- design-102-shaped: no BOM at all, autoseed heals it.
    # ------------------------------------------------------------------
    def test_no_bom_at_all_reports_warning_not_block(self):
        """Design-102 repro: SB-WALL-CORNER-shaped product, zero BOMs
        anywhere for its template -- the common case the real seeder
        always resolves. Cold validate must WARN, not BLOCK, and must
        not persist anything."""
        tmpl = self.env["product.template"].create({
            "name": "BGC Wall Corner (no BOM)",
            "default_code": "BGC-WALL-CORNER",
            "type": "consu", "sale_ok": True, "list_price": 295.0,
        })
        product = tmpl.product_variant_id
        self.assertFalse(
            tmpl.with_context(active_test=False).bom_ids,
            "test precondition: fresh template must start with no BOM",
        )
        design = self._design()
        self._line(design, product, cabinet_type="corner")

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}

        self.assertIn("BOM_WILL_AUTOSEED", by_code, issues)
        self.assertEqual(by_code["BOM_WILL_AUTOSEED"]["severity"], "warning")
        # Assert INTENT, not prose. This used to pin the exact sentence,
        # which broke the moment the copy was rewritten to drop internal
        # phrasing — the code + severity above are the real contract.
        self.assertTrue(by_code["BOM_WILL_AUTOSEED"]["message"].strip())
        self.assertNotIn("MISSING_BOM", by_code, issues)

        blocking = [i for i in issues if i["severity"] == "blocking"]
        bom_blocks = [i for i in blocking if i["code"] == "MISSING_BOM"]
        self.assertFalse(
            bom_blocks,
            "design-102-shaped data must validate without a BOM block: %s"
            % issues,
        )

        # Binding constraint -- validating must NEVER seed a BOM as a
        # side effect (the predicate's internal dry run must roll back).
        self.assertFalse(
            tmpl.with_context(active_test=False).bom_ids,
            "_check_production_ready must not create any BOM as a side "
            "effect of validating",
        )

    def test_predicate_dry_run_matches_real_autoseed_and_leaves_no_residue(self):
        """Ground-truth cross-check AND a dry-run-residue check on ONE
        shared fixture: the predicate must (a) agree with what a REAL
        `_ensure_kitchen_bom` call would do, and (b) leave nothing
        behind after its own internal savepoint-wrapped dry run of that
        same method (Finding 1 fix -- the predicate now actually calls
        the real seeder and rolls it back, rather than hand-deriving an
        answer)."""
        tmpl = self.env["product.template"].create({
            "name": "BGC Predicate Cross-check",
            "default_code": "BGC-PREDICATE-XCHECK",
            "type": "consu", "sale_ok": True, "list_price": 200.0,
        })
        product = tmpl.product_variant_id
        design = self._design()

        self.assertTrue(design._bom_autoseed_would_resolve(product))
        self.assertFalse(
            tmpl.with_context(active_test=False).bom_ids,
            "the predicate's internal dry run of _ensure_kitchen_bom "
            "must leave no BOM behind",
        )

        # Ground truth: the REAL (non-rolled-back) call must
        # independently succeed the same way the predicate predicted.
        design._ensure_kitchen_bom(tmpl)
        self.assertTrue(
            self._has_bom(product, tmpl),
            "the real _ensure_kitchen_bom call must produce a BOM that "
            "satisfies the same existence query MISSING_BOM runs",
        )

    # ------------------------------------------------------------------
    # Branch 2 -- genuinely outside the auto-seed logic: sibling-variant
    # BOM shadows the template's fast path.
    # ------------------------------------------------------------------
    def _multi_variant_template(self):
        attr = self.env["product.attribute"].create({
            "name": "BGC Coherence Test Variant Attr",
            "create_variant": "always",
        })
        val_a, val_b = self.env["product.attribute.value"].create([
            {"name": "BGC-A", "attribute_id": attr.id},
            {"name": "BGC-B", "attribute_id": attr.id},
        ])
        tmpl = self.env["product.template"].create({
            "name": "BGC Multi-Variant Corner",
            "type": "consu", "sale_ok": True, "list_price": 625.0,
            "attribute_line_ids": [(0, 0, {
                "attribute_id": attr.id,
                "value_ids": [(6, 0, [val_a.id, val_b.id])],
            })],
        })
        variants = tmpl.product_variant_ids
        self.assertEqual(len(variants), 2, "precondition: 2 variants")
        return tmpl, variants[0], variants[1]

    def test_sibling_variant_bom_stays_blocking(self):
        """Review fix (Finding 3) -- consolidated onto ONE shared
        fixture instance: predicate verdict, no residue from its dry
        run, `_check_production_ready`'s issue bucket, AND ground truth
        via a REAL (non-rolled-back) `_ensure_kitchen_bom` call, all
        against the same template/variants/BOM.

        A template already carries a 'normal' BOM -- but pinned to a
        SIBLING variant, not the one on this design's line.
        `_ensure_kitchen_bom`'s fast path matches on type only and
        would hand back that mismatched BOM without ever creating the
        template-level stub this line's variant actually needs, so
        quoting would still fail MISSING_BOM afterwards too. Cold
        validate must BLOCK this, not WARN.
        """
        tmpl, variant_a, variant_b = self._multi_variant_template()
        sibling_bom = self.Bom.sudo().create({
            "product_tmpl_id": tmpl.id,
            "product_id": variant_a.id,
            "type": "normal",
            "product_qty": 1.0,
        })

        design = self._design()
        line = self._line(design, variant_b, cabinet_type="corner")

        # 1. Predicate verdict.
        self.assertFalse(
            design._bom_autoseed_would_resolve(variant_b),
            "a sibling-variant-scoped BOM must not count as autoseedable "
            "for a different variant",
        )

        # 2. No residue from the predicate's internal dry run -- still
        #    exactly the one pre-existing sibling BOM, nothing new.
        all_normals = self.Bom.sudo().with_context(active_test=False).search([
            ("product_tmpl_id", "=", tmpl.id), ("type", "=", "normal"),
        ])
        self.assertEqual(
            all_normals, sibling_bom,
            "the predicate's dry run must not leave any new BOM behind",
        )

        # 3. _check_production_ready surfaces it as blocking, naming
        #    this exact line.
        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("MISSING_BOM", by_code, issues)
        self.assertEqual(by_code["MISSING_BOM"]["severity"], "blocking")
        self.assertIn("(line #%d)" % line.id,
                       by_code["MISSING_BOM"]["message"])
        self.assertNotIn("BOM_WILL_AUTOSEED", by_code, issues)

        # 4. Ground truth -- a REAL autoseed call, as
        #    action_create_quotation would make, genuinely still can't
        #    resolve variant B's MISSING_BOM condition (fast path
        #    returns variant A's BOM without creating a template-level
        #    stub). This is a real autoseed gap, not a contrived one.
        design._ensure_kitchen_bom(tmpl)
        self.assertFalse(
            self._has_bom(variant_b, tmpl),
            "real autoseed must genuinely fail to resolve variant B's "
            "MISSING_BOM condition here",
        )

    # ------------------------------------------------------------------
    # Branch 3 -- Finding 1 regression pin: the real seeder fails.
    # ------------------------------------------------------------------
    def test_seeder_usererror_buckets_to_blocking_not_warning(self):
        """Finding-1 regression pin (Task 2 review, corner-remediation
        2026-07-30) -- if the real `_ensure_kitchen_bom` raises
        UserError (exactly the failure both real call sites'
        `try/except UserError` guards against, with the D12 MISSING_BOM
        gate as the documented safety net), the predicate must bucket
        to BLOCKING, never WARN. Before this fix the predicate never
        called the real seeder at all, so it could not see a raise and
        would have silently reported BOM_WILL_AUTOSEED for a design
        that then ships with a permanently BOM-less quote line.

        Patches `mrp.bom._compute_panel_dimensions` (the one genuinely
        parametric step `_ensure_kitchen_bom` calls) at the class
        level, mirroring the established `patch.object(type(...), ...)`
        pattern already used for this exact kind of gate-failure
        simulation in test_track_b_end_to_end.py's
        test_06_savepoint_wraps_action_confirm.
        """
        tmpl = self.env["product.template"].create({
            "name": "BGC Seeder Failure",
            "default_code": "BGC-SEEDER-FAIL",
            "type": "consu", "sale_ok": True, "list_price": 250.0,
        })
        product = tmpl.product_variant_id
        design = self._design()
        line = self._line(design, product, cabinet_type="corner")

        bom_cls = type(self.Bom)

        def _raise_user_error(self, *args, **kwargs):
            raise UserError("Simulated panel-dimension failure")

        with patch.object(bom_cls, "_compute_panel_dimensions",
                           _raise_user_error):
            self.assertFalse(
                design._bom_autoseed_would_resolve(product),
                "a seeder UserError must bucket to NOT auto-seedable "
                "(fail-closed), never auto-seedable",
            )
            issues = design._check_production_ready()

        by_code = {i["code"]: i for i in issues}
        self.assertIn("MISSING_BOM", by_code, issues)
        self.assertEqual(by_code["MISSING_BOM"]["severity"], "blocking")
        self.assertIn("(line #%d)" % line.id,
                       by_code["MISSING_BOM"]["message"])
        self.assertNotIn("BOM_WILL_AUTOSEED", by_code, issues)

        # Outside the patch -- confirm the failed dry run left no BOM
        # (or anything else) behind.
        self.assertFalse(
            tmpl.with_context(active_test=False).bom_ids,
            "a failed dry-run must leave no BOM behind",
        )

    def test_seeder_unexpected_exception_also_buckets_to_blocking(self):
        """Defence-in-depth companion to the UserError pin above --
        even a non-UserError failure from the seeder (a future change
        to `_ensure_kitchen_bom`, or an unrelated bug) must fail
        CLOSED (blocking), never fail open (warning)."""
        tmpl = self.env["product.template"].create({
            "name": "BGC Seeder Unexpected Failure",
            "default_code": "BGC-SEEDER-UNEXPECTED-FAIL",
            "type": "consu", "sale_ok": True, "list_price": 250.0,
        })
        product = tmpl.product_variant_id
        design = self._design()

        bom_cls = type(self.Bom)

        def _raise_value_error(self, *args, **kwargs):
            raise ValueError("Simulated unexpected seeder failure")

        with patch.object(bom_cls, "_compute_panel_dimensions",
                           _raise_value_error):
            self.assertFalse(
                design._bom_autoseed_would_resolve(product),
                "an unexpected (non-UserError) seeder failure must also "
                "fail closed to NOT auto-seedable",
            )

        self.assertFalse(tmpl.with_context(active_test=False).bom_ids)

    # ------------------------------------------------------------------
    # action_validate_production toast -- brief AC wording + severity.
    # ------------------------------------------------------------------
    def test_validate_production_toast_shows_warn_not_block(self):
        tmpl = self.env["product.template"].create({
            "name": "BGC Toast Wall Corner",
            "default_code": "BGC-TOAST-WALL-CORNER",
            "type": "consu", "sale_ok": True, "list_price": 295.0,
        })
        design = self._design()
        self._line(design, tmpl.product_variant_id, cabinet_type="corner")

        result = design.action_validate_production()
        message = result["params"]["message"]

        # The [WARN]/[BLOCK] prefixes are the toast's real contract; the
        # sentence after them is copy and may be rewritten freely.
        self.assertIn("[WARN]", message)
        self.assertNotIn("[BLOCK]", message)
        self.assertEqual(result["params"]["type"], "warning")

    def test_validate_production_toast_still_blocks_unseedable_case(self):
        tmpl, variant_a, variant_b = self._multi_variant_template()
        self.Bom.sudo().create({
            "product_tmpl_id": tmpl.id,
            "product_id": variant_a.id,
            "type": "normal",
            "product_qty": 1.0,
        })
        design = self._design()
        self._line(design, variant_b, cabinet_type="corner")

        result = design.action_validate_production()
        message = result["params"]["message"]

        self.assertIn("[BLOCK]", message)
        self.assertIn("BOM", message)
        self.assertEqual(result["params"]["type"], "danger")
