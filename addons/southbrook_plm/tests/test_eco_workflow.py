# SPDX-License-Identifier: LGPL-3.0-only
"""Tests for the Southbrook PLM ECO workflow, cut-spec seam, and approval gating.

Run with:
    odoo-bin --test-enable --stop-after-init \\
        -i southbrook_plm -d <db>
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

# The estimating code defaults the seam falls back to (NF14 baseline).
ESTIMATING_DEFAULTS = {
    "box_th": 15.875,
    "back_th": 6.35,
    "rabbet": 6.35,
    "door_th": 18.0,
    "door_reveal": 3.0,
    "shelf_tol": 1.5,
    "shelf_vent_gap": 12.7,
    "toekick_h": 101.6,
}


@tagged("post_install", "-at_install")
class TestSouthbrookPlm(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Eco = cls.env["southbrook.eco"]
        cls.Spec = cls.env["southbrook.cut.spec"]
        cls.Bom = cls.env["mrp.bom"]
        cls.type_cut = cls.env.ref("southbrook_plm.eco_type_cut_spec")
        cls.type_bom = cls.env.ref("southbrook_plm.eco_type_bom")
        cls.type_rule = cls.env.ref("southbrook_plm.eco_type_rule")
        cls.seed_spec = cls.env.ref("southbrook_plm.cut_spec_nf14_seed")

        cls.approver = new_test_user(
            cls.env,
            login="plm_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )
        cls.plain_user = new_test_user(
            cls.env,
            login="plm_user",
            groups="southbrook_plm.group_southbrook_plm_user,base.group_user",
        )

    # ------------------------------------------------------------------
    # Cut-spec seam
    # ------------------------------------------------------------------
    def test_seed_spec_is_active_and_matches_defaults(self):
        self.assertEqual(self.seed_spec.state, "active")
        self.assertEqual(self.seed_spec.constants_dict(), ESTIMATING_DEFAULTS)

    def test_seam_reads_active_spec(self):
        # mrp.bom._get_cut_constants must return the active spec values.
        self.assertEqual(self.Bom._get_cut_constants(), ESTIMATING_DEFAULTS)

    def test_seam_falls_back_to_code_defaults_when_no_active(self):
        # Supersede the only active spec; seam should defer to super() defaults.
        self.seed_spec.write({"state": "superseded"})
        self.assertFalse(self.Spec._get_active())
        self.assertEqual(self.Bom._get_cut_constants(), ESTIMATING_DEFAULTS)

    def test_single_active_constraint(self):
        with self.assertRaises(ValidationError):
            self.Spec.create(
                {"name": "Second Active", "state": "active", **ESTIMATING_DEFAULTS}
            )

    def test_positive_value_constraint(self):
        with self.assertRaises(ValidationError):
            self.Spec.create(
                {"name": "Bad", **{**ESTIMATING_DEFAULTS, "door_th": 0.0}}
            )

    # ------------------------------------------------------------------
    # Cut-spec ECO end to end
    # ------------------------------------------------------------------
    def test_cut_spec_eco_apply_changes_seam(self):
        candidate = self.Spec.create(
            {
                "name": "Wider reveal",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 4.0},
            }
        )
        eco = self.Eco.create(
            {
                "title": "Bump door reveal to 4mm",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(self.approver).action_apply()

        self.assertEqual(eco.state, "applied")
        self.assertTrue(eco.applied_date)
        self.assertEqual(candidate.state, "active")
        self.assertEqual(self.seed_spec.state, "superseded")
        # The seam now reflects the new reveal.
        self.assertEqual(self.Bom._get_cut_constants()["door_reveal"], 4.0)

    def test_cut_spec_eco_requires_candidate(self):
        eco = self.Eco.create(
            {"title": "No candidate", "eco_type_id": self.type_cut.id}
        )
        with self.assertRaises(ValidationError):
            eco.with_user(self.approver).action_apply()

    def test_panel_math_follows_active_spec(self):
        # _compute_panel_dimensions must reflect an ECO-applied reveal change.
        before = self.Bom._compute_panel_dimensions(600, 720, 320, door_count=1)
        candidate = self.Spec.create(
            {
                "name": "Reveal 5",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 5.0},
            }
        )
        eco = self.Eco.create(
            {
                "title": "Reveal 5mm",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(self.approver).action_apply()
        after = self.Bom._compute_panel_dimensions(600, 720, 320, door_count=1)
        # door height = height - 2*reveal -> shrinks by 2*(5-3) = 4mm.
        self.assertEqual(before["door"][0] - after["door"][0], 4.0)

    # ------------------------------------------------------------------
    # BoM versioning ECO
    # ------------------------------------------------------------------
    def test_bom_eco_versions_and_archives(self):
        product = self.env["product.product"].create(
            {"name": "Test Cabinet", "type": "consu"}
        )
        bom = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0}
        )
        self.assertEqual(bom.southbrook_version, 1)
        eco = self.Eco.create(
            {
                "title": "Rev the test cabinet BoM",
                "eco_type_id": self.type_bom.id,
                "bom_id": bom.id,
            }
        )
        eco.with_user(self.approver).action_apply()

        self.assertEqual(eco.state, "applied")
        self.assertTrue(eco.new_bom_id)
        self.assertEqual(eco.new_bom_id.southbrook_version, 2)
        self.assertFalse(bom.active)
        self.assertTrue(eco.new_bom_id.active)

    # ------------------------------------------------------------------
    # Rule ECO
    # ------------------------------------------------------------------
    def test_rule_eco_requires_git_ref(self):
        eco = self.Eco.create(
            {"title": "Change width rule", "eco_type_id": self.type_rule.id}
        )
        with self.assertRaises(ValidationError):
            eco.with_user(self.approver).action_apply()

    def test_rule_eco_applies_with_git_ref(self):
        eco = self.Eco.create(
            {
                "title": "Change width rule",
                "eco_type_id": self.type_rule.id,
                "git_ref": "abc1234",
            }
        )
        eco.with_user(self.approver).action_apply()
        self.assertEqual(eco.state, "applied")

    # ------------------------------------------------------------------
    # Approval gating
    # ------------------------------------------------------------------
    def test_plain_user_cannot_apply(self):
        eco = self.Eco.create(
            {
                "title": "Doc update",
                "eco_type_id": self.env.ref("southbrook_plm.eco_type_document").id,
            }
        )
        with self.assertRaises(UserError):
            eco.with_user(self.plain_user).action_apply()

    def test_approval_required_stage_gating(self):
        review = self.env.ref("southbrook_plm.stage_review")
        approved = self.env.ref("southbrook_plm.stage_approved")
        eco = self.Eco.create(
            {
                "title": "Gated move",
                "eco_type_id": self.type_rule.id,
                "stage_id": review.id,
            }
        )
        # review stage has approval_required -> plain user blocked leaving it.
        with self.assertRaises(UserError):
            eco.with_user(self.plain_user).write({"stage_id": approved.id})
        # approver may move it.
        eco.with_user(self.approver).write({"stage_id": approved.id})
        self.assertEqual(eco.stage_id, approved)

    def test_eco_sequence_assigned(self):
        eco = self.Eco.create(
            {"title": "Seq check", "eco_type_id": self.type_rule.id}
        )
        self.assertTrue(eco.name.startswith("ECO/"))
