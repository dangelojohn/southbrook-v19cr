# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 1 sales-rep UX fix (2026-07-31), Task 1 — surface hard
configuration-rule violations WHILE the order is being built, not only
as a blocking dialog at Confirm.

Persona: a sales rep builds a multi-line quote with the customer on
the phone and, before this fix, only discovers a rule violation when
they click Confirm.

`sale.order.southbrook_hard_validation_issues()` (models/sale_order.py)
is the single source of truth for the four hard config rules; this
suite pins that:

  * a draft order with a known rule violation exposes the SAME issue,
    with the SAME code, on the new non-stored compute fields
    (`sb_hard_validation_issue_count` / `sb_hard_validation_issues_html`
    on sale.order, `sb_hard_validation_message` on sale.order.line)
    BEFORE Confirm is ever clicked;
  * the SAME order still blocks AT Confirm with the SAME code —
    action_confirm()'s gate is unchanged; this is read-only visibility
    layered on top of the existing check, not a second rule engine;
  * the computed fields are empty/falsy for a clean order.

Per the southbrook-design-system skill ("test copy by intent, not
prose"): assertions target codes and structure, not exact message
text.

Variant construction note: real configurator flows can never produce a
Contractor+Maple / Contractor+Five-Piece variant — `product.config.
session.validate_configuration()` blocks that combination at the
source. `southbrook_hard_validation_issues()` exists specifically as a
defense-in-depth backstop for combinations that predate a rule or were
written directly via the ORM (see its docstring). These tests
reproduce that exact scenario: create a bare variant, then force its
`product_template_attribute_value_ids` directly (bypassing the config
engine's own guard) — mirroring "entered via direct ORM writes".
"""
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import SouthbrookTestCase


@tagged("post_install", "-at_install", "southbrook", "rule_enforcement", "views")
class TestPreConfirmHardValidation(SouthbrookTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmpl = cls.env.ref("southbrook_estimating.base_1dr")
        cls.partner = cls.Partner.create(
            {"name": "Pre-Confirm UX Test Customer"})

    def _ptav(self, attr_line_xmlid, value_xmlid):
        """Resolve the product.template.attribute.value for a given
        (attribute line, attribute value) pair on base_1dr."""
        line = self.env.ref(f"southbrook_estimating.{attr_line_xmlid}")
        value = self.env.ref(f"southbrook_estimating.{value_xmlid}")
        ptav = line.product_template_value_ids.filtered(
            lambda v, value=value: v.product_attribute_value_id == value)
        self.assertTrue(
            ptav, f"no product.template.attribute.value found for "
                  f"{attr_line_xmlid} = {value_xmlid} — fixture drifted")
        return ptav

    def _make_variant(self, *ptav_pairs):
        """Create a base_1dr variant and force exactly the given PTAVs
        onto it via direct ORM write — bypasses
        product.config.session.validate_configuration(), simulating a
        combination that predates a rule / was written directly.
        """
        variant = self.Product.create({"product_tmpl_id": self.tmpl.id})
        ptavs = self.env["product.template.attribute.value"]
        for attr_line_xmlid, value_xmlid in ptav_pairs:
            ptavs |= self._ptav(attr_line_xmlid, value_xmlid)
        variant.product_template_attribute_value_ids = [(6, 0, ptavs.ids)]
        return variant

    def _make_order(self, variant, line_name="Test Line"):
        order = self.Order.create({
            "partner_id": self.partner.id,
            # southbrook_mrp_pm gates action_confirm on a separate
            # production-approval workflow; pre-approve so this suite
            # exercises OUR hard-validation guard specifically.
            "production_approval_state": "approved",
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": variant.id,
            "name": line_name,
            "product_uom_qty": 1,
        })
        return order

    # ------------------------------------------------------------------
    # Clean order — every new field must be empty/falsy.
    # ------------------------------------------------------------------
    def test_01_clean_order_has_no_issues_pre_confirm(self):
        variant = self._make_variant(
            ("attr_line_base_1dr_series", "value_series_contemporary"),
            ("attr_line_base_1dr_box_material", "value_box_white_melamine"),
        )
        order = self._make_order(variant, "Base Cabinet - Clean Spec")
        self.assertEqual(order.southbrook_hard_validation_issues(), [])
        self.assertEqual(order.sb_hard_validation_issue_count, 0)
        self.assertFalse(order.sb_hard_validation_issues_html)
        self.assertFalse(order.order_line.sb_hard_validation_message)
        # Confirm must succeed cleanly.
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    # ------------------------------------------------------------------
    # Rule-violating order — issue must surface BEFORE Confirm.
    # ------------------------------------------------------------------
    def test_02_violation_exposed_before_confirm(self):
        variant = self._make_variant(
            ("attr_line_base_1dr_series", "value_series_contractor"),
            ("attr_line_base_1dr_box_material", "value_box_maple"),
        )
        order = self._make_order(
            variant, "Base Cabinet - Contractor Series, Maple Box")

        # Nothing has confirmed anything yet — order is still draft.
        self.assertEqual(order.state, "draft")

        issues = order.southbrook_hard_validation_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], "box_series_incompatible")

        # The SAME issue must be visible on the new pre-confirm fields.
        self.assertEqual(order.sb_hard_validation_issue_count, 1)
        self.assertTrue(order.sb_hard_validation_issues_html)
        html = str(order.sb_hard_validation_issues_html)
        self.assertIn("alert-danger", html)
        self.assertNotIn(
            "box_series_incompatible", html,
            "user-facing banner must not leak the internal issue code",
        )
        line = order.order_line
        self.assertTrue(
            line.sb_hard_validation_message,
            "the offending line must carry a non-empty "
            "sb_hard_validation_message before Confirm",
        )
        self.assertIn("Maple", line.sb_hard_validation_message)

        # Still draft — the pre-confirm surfacing must not itself block
        # or otherwise mutate order state.
        self.assertEqual(order.state, "draft")

    def test_03_same_order_still_blocks_at_confirm_same_code(self):
        """The gate in action_confirm() must be byte-for-byte
        unchanged: same method, same code, still blocking."""
        variant = self._make_variant(
            ("attr_line_base_1dr_series", "value_series_contractor"),
            ("attr_line_base_1dr_box_material", "value_box_maple"),
        )
        order = self._make_order(
            variant, "Base Cabinet - Contractor Series, Maple Box")

        pre_confirm_issues = order.southbrook_hard_validation_issues()

        with self.assertRaises(UserError) as ctx:
            order.action_confirm()
        self.assertNotEqual(order.state, "sale")

        post_confirm_issues = order.southbrook_hard_validation_issues()
        self.assertEqual(
            [i["code"] for i in pre_confirm_issues],
            [i["code"] for i in post_confirm_issues],
            "the pre-confirm surfacing and the confirm-time gate must "
            "report the identical set of codes — one source of truth",
        )
        self.assertEqual(post_confirm_issues[0]["code"],
                          "box_series_incompatible")
        # The raised UserError text must still name the blocked line
        # (unchanged confirm-time message contract).
        self.assertIn("Maple", str(ctx.exception))

    def test_04_series_door_style_violation_also_surfaces_pre_confirm(self):
        """Second hard rule (Rule 1 — series -> door style), same shape."""
        variant = self._make_variant(
            ("attr_line_base_1dr_series", "value_series_contractor"),
            ("attr_line_base_1dr_door_style",
             "value_door_five_piece_woodgrain"),
        )
        order = self._make_order(
            variant, "Base Cabinet - Contractor Series, Five-Piece Door")

        issues = order.southbrook_hard_validation_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], "series_door_incompatible")
        self.assertEqual(order.sb_hard_validation_issue_count, 1)
        self.assertIn(
            "series_door_incompatible",
            [i["code"] for i in order.southbrook_hard_validation_issues()],
        )
        self.assertTrue(order.order_line.sb_hard_validation_message)

        with self.assertRaises(UserError):
            order.action_confirm()

    # ------------------------------------------------------------------
    # Order Builder form must render the new banner + list decoration.
    # ------------------------------------------------------------------
    def test_05_order_form_arch_contains_preconfirm_banner(self):
        form_view = self.env.ref("sale.view_order_form")
        arch = self.env["sale.order"].get_view(form_view.id, "form")["arch"]
        self.assertIn('name="sb_hard_validation_issue_count"', arch)
        self.assertIn('name="sb_hard_validation_issues_html"', arch)
        self.assertIn('name="sb_hard_validation_message"', arch)
        self.assertIn(
            'decoration-danger="sb_hard_validation_message"', arch,
            "order_line list must flag the offending row",
        )
