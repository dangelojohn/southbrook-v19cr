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
now asks `_bom_autoseed_would_resolve(product)` -- a READ-ONLY predicate
that mirrors `_ensure_kitchen_bom`'s own decision logic without ever
creating anything -- to split missing-BOM lines into:
  * autoseedable  -> `BOM_WILL_AUTOSEED`, severity "warning"
  * NOT autoseedable -> `MISSING_BOM`, severity "blocking" (unchanged)

This file proves both branches with real Odoo data (no monkeypatching):
  1. A design-102-shaped product with NO BOM at all on its template is
     the common case `_ensure_kitchen_bom` always heals -> WARN, and the
     design is not blocked on that account.
  2. A product genuinely outside the auto-seed logic: its template
     already carries a 'normal'-type BOM, but pinned to a SIBLING
     variant (not this line's product). `_ensure_kitchen_bom`'s own
     fast path (`bom_ids.filtered(lambda b: b.type == "normal")`)
     matches on type only, not on which variant the BOM is scoped to,
     so it would hand back that mismatched sibling BOM without ever
     creating the template-level stub this variant needs -- MISSING_BOM
     would survive the quote flow's autoseed call unchanged. That case
     must still BLOCK.
  3. Never actually seeds anything as a side effect of validating.
  4. `action_validate_production`'s toast renders the exact
     `[WARN] BOM will be auto-created at quotation` line the brief AC
     names, and is non-blocking (danger only when BLOCK issues exist).
"""
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

    # ------------------------------------------------------------------
    # Branch 1 -- design-102-shaped: no BOM at all, autoseed heals it.
    # ------------------------------------------------------------------
    def test_no_bom_at_all_reports_warning_not_block(self):
        """Design-102 repro: SB-WALL-CORNER-shaped product, zero BOMs
        anywhere for its template. This is exactly the shape
        `_ensure_kitchen_bom` always resolves (fresh template-level
        stub, no raise paths in that method) -- cold validate must
        WARN, not BLOCK."""
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
        self.assertIn("BOM will be auto-created at quotation",
                       by_code["BOM_WILL_AUTOSEED"]["message"])
        self.assertNotIn("MISSING_BOM", by_code, issues)

        blocking = [i for i in issues if i["severity"] == "blocking"]
        bom_blocks = [i for i in blocking if i["code"] == "MISSING_BOM"]
        self.assertFalse(
            bom_blocks,
            "design-102-shaped data must validate without a BOM block: %s"
            % issues,
        )

        # Binding constraint -- validating must NEVER seed a BOM as a
        # side effect.
        self.assertFalse(
            tmpl.with_context(active_test=False).bom_ids,
            "_check_production_ready must not create any BOM as a side "
            "effect of validating",
        )

    def test_predicate_matches_real_autoseed_outcome_for_no_bom_case(self):
        """Cross-check: actually RUN `_ensure_kitchen_bom` (the real
        quotation-flow autoseed) on the same no-BOM template and prove
        the resulting BOM satisfies the validator's own MISSING_BOM
        existence query -- i.e. the predicate's "yes" isn't just an
        assertion, it matches ground truth."""
        tmpl = self.env["product.template"].create({
            "name": "BGC Predicate Cross-check",
            "default_code": "BGC-PREDICATE-XCHECK",
            "type": "consu", "sale_ok": True, "list_price": 200.0,
        })
        product = tmpl.product_variant_id
        design = self._design()

        self.assertTrue(design._bom_autoseed_would_resolve(product))

        design._ensure_kitchen_bom(tmpl)
        has_bom = self.Bom.sudo().search_count([
            "|",
            ("product_id", "=", product.id),
            "&",
            ("product_tmpl_id", "=", tmpl.id),
            ("product_id", "=", False),
        ], limit=1)
        self.assertTrue(
            has_bom,
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
        """A template already carries a 'normal' BOM -- but pinned to a
        SIBLING variant, not the one on this design's line.
        `_ensure_kitchen_bom`'s fast path
        (`bom_ids.filtered(lambda b: b.type == "normal")`) matches on
        type only and would hand back that mismatched BOM without ever
        creating the template-level stub this line's variant actually
        needs, so quoting would still fail MISSING_BOM afterwards too.
        Cold validate must BLOCK this, not WARN."""
        tmpl, variant_a, variant_b = self._multi_variant_template()
        self.Bom.sudo().create({
            "product_tmpl_id": tmpl.id,
            "product_id": variant_a.id,
            "type": "normal",
            "product_qty": 1.0,
        })

        design = self._design()
        line = self._line(design, variant_b, cabinet_type="corner")

        self.assertFalse(
            design._bom_autoseed_would_resolve(variant_b),
            "a sibling-variant-scoped BOM must not count as autoseedable "
            "for a different variant",
        )

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}

        self.assertIn("MISSING_BOM", by_code, issues)
        self.assertEqual(by_code["MISSING_BOM"]["severity"], "blocking")
        self.assertIn("(line #%d)" % line.id,
                       by_code["MISSING_BOM"]["message"])
        self.assertNotIn("BOM_WILL_AUTOSEED", by_code, issues)

    def test_sibling_variant_case_really_would_survive_real_autoseed(self):
        """Ground-truth check for the branch-2 scenario: actually call
        the real `_ensure_kitchen_bom` (as `action_create_quotation`
        would) on the shadowed template and prove MISSING_BOM is STILL
        true for the sibling variant afterwards -- i.e. this is a
        genuine autoseed gap, not a contrived one."""
        tmpl, variant_a, variant_b = self._multi_variant_template()
        self.Bom.sudo().create({
            "product_tmpl_id": tmpl.id,
            "product_id": variant_a.id,
            "type": "normal",
            "product_qty": 1.0,
        })
        design = self._design()

        design._ensure_kitchen_bom(tmpl)

        has_bom_for_b = self.Bom.sudo().search_count([
            "|",
            ("product_id", "=", variant_b.id),
            "&",
            ("product_tmpl_id", "=", tmpl.id),
            ("product_id", "=", False),
        ], limit=1)
        self.assertFalse(
            has_bom_for_b,
            "real autoseed must genuinely fail to resolve variant B's "
            "MISSING_BOM condition here (fast-path returns variant A's "
            "BOM without creating a template-level stub)",
        )

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

        self.assertIn("[WARN] BOM will be auto-created at quotation",
                       message)
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

        self.assertIn("[BLOCK] No BOM defined", message)
        self.assertEqual(result["params"]["type"], "danger")
