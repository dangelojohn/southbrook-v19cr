# SPDX-License-Identifier: LGPL-3.0-only
"""M4x hardening — governance-contract tests for southbrook.eco.

Covers the 2026-08-01/02 hardening pass: four-eyes approve/apply, the
emergency-bypass break-glass, terminal-ECO immutability (write + unlink),
action_reset_draft's rejected-only scope, action_create_superseding, the
mrp.bom version-collision guard, the cut-spec ondelete guard, target-required
@api.constrains, and the PLM User record-rule scoping to own-open ECOs.

TransactionCase runs as SUPERUSER: superuser passes every has_group() check,
so every gating assertion below runs via .with_user() with a real,
non-superuser fixture user. Superuser is exercised directly (no with_user)
ONLY where the contract explicitly says the guard fires for superuser too
(the @api.ondelete / write-immutability guards).

Some of these tests exercise contract surface that is still landing in
mrp_bom.py / southbrook_cut_spec.py in a parallel pass (BoM version-collision
uniqueness, cut-spec ondelete) — they are written to the CONTRACT, not to
whatever partial state those two files are in at any given moment.

Run with:
    odoo-bin --test-enable --stop-after-init \\
        -i southbrook_plm -d <db>
"""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSouthbrookPlmGovernance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Eco = cls.env["southbrook.eco"]
        cls.Bom = cls.env["mrp.bom"]
        cls.Spec = cls.env["southbrook.cut.spec"]

        cls.type_bom = cls.env.ref("southbrook_plm.eco_type_bom")
        cls.type_cut = cls.env.ref("southbrook_plm.eco_type_cut_spec")
        cls.type_rule = cls.env.ref("southbrook_plm.eco_type_rule")
        cls.type_document = cls.env.ref("southbrook_plm.eco_type_document")

        cls.stage_draft = cls.env.ref("southbrook_plm.stage_draft")
        cls.stage_review = cls.env.ref("southbrook_plm.stage_review")
        cls.stage_approved = cls.env.ref("southbrook_plm.stage_approved")
        cls.stage_applied = cls.env.ref("southbrook_plm.stage_applied")
        cls.stage_rejected = cls.env.ref("southbrook_plm.stage_rejected")

        cls.seed_spec = cls.env.ref("southbrook_plm.cut_spec_nf14_seed")

        # Two independent approvers (A/B) so four-eyes tests can prove BOTH
        # halves: the ECO's own Responsible/creator is blocked, and an
        # unrelated PLM Approver is not. Distinct logins from
        # test_eco_workflow.py's fixtures (separate test classes, but keep
        # res.users.login globally unique regardless).
        cls.approver = new_test_user(
            cls.env,
            login="plm_gov_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )
        cls.approver2 = new_test_user(
            cls.env,
            login="plm_gov_approver2",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )
        cls.plain_user = new_test_user(
            cls.env,
            login="plm_gov_user",
            groups="southbrook_plm.group_southbrook_plm_user,base.group_user",
        )
        cls.plain_user2 = new_test_user(
            cls.env,
            login="plm_gov_user2",
            groups="southbrook_plm.group_southbrook_plm_user,base.group_user",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_rule_eco(self, title, git_ref="cafebabe0", **extra):
        """A rule-kind ECO is the lightest fixture that satisfies
        _check_target_required (just needs git_ref) — use it for tests
        that exercise generic lifecycle plumbing rather than a specific
        target kind's apply mechanics."""
        vals = {"title": title, "eco_type_id": self.type_rule.id, "git_ref": git_ref}
        vals.update(extra)
        return self.Eco.create(vals)

    def _approve_and_apply(self, eco, approver=None):
        approver = approver or self.approver
        eco.with_user(approver).action_approve()
        eco.with_user(approver).action_apply()
        return eco

    # ------------------------------------------------------------------
    # Apply gating: must have actually gone through action_approve
    # ------------------------------------------------------------------
    def test_apply_from_draft_blocked(self):
        eco = self._make_rule_eco("Apply from draft blocked")
        self.assertEqual(eco.stage_id, self.stage_draft)
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_apply()
        self.assertNotEqual(eco.state, "applied")

    def test_apply_without_prior_approve_blocked(self):
        """Forcing stage_id into the Approved stage by a bare write (NOT via
        action_approve) must not satisfy Apply's gate — approver_id /
        approval_date must actually have been stamped by action_approve."""
        eco = self._make_rule_eco("Apply without approve blocked")
        eco.with_user(self.approver).write({"stage_id": self.stage_approved.id})
        self.assertFalse(eco.approver_id)
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_apply()
        self.assertNotEqual(eco.state, "applied")

    # ------------------------------------------------------------------
    # Four-eyes: approve
    # ------------------------------------------------------------------
    def test_self_approve_blocked_four_eyes(self):
        eco = self.Eco.with_user(self.approver).create({
            "title": "Self approve test",
            "eco_type_id": self.type_rule.id,
            "git_ref": "selfapprove1",
        })
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_approve()
        self.assertFalse(eco.approver_id)

    def test_independent_approver_may_approve_authors_eco(self):
        eco = self.Eco.with_user(self.approver).create({
            "title": "Independent approve test",
            "eco_type_id": self.type_rule.id,
            "git_ref": "indyapprove1",
        })
        eco.with_user(self.approver2).action_approve()
        self.assertEqual(eco.approver_id, self.approver2)
        self.assertTrue(eco.stage_id.is_approved_stage)

    def test_emergency_bypass_requires_reason(self):
        eco = self.Eco.with_user(self.approver).create({
            "title": "Bypass needs reason",
            "eco_type_id": self.type_rule.id,
            "git_ref": "bypassreason1",
        })
        with self.assertRaises(ValidationError):
            eco.with_user(self.approver).write({"emergency_bypass": True})
        # Setting both together (reason present) is fine.
        eco.with_user(self.approver).write({
            "emergency_bypass": True,
            "bypass_reason": "Solo on-call, no other approver reachable.",
        })
        self.assertTrue(eco.emergency_bypass)

    def test_emergency_bypass_permits_self_approve_and_posts_chatter(self):
        eco = self.Eco.with_user(self.approver).create({
            "title": "Bypass self approve",
            "eco_type_id": self.type_rule.id,
            "git_ref": "bypassapprove1",
            "emergency_bypass": True,
            "bypass_reason": "Solo on-call.",
        })
        before_ids = set(eco.message_ids.ids)
        eco.with_user(self.approver).action_approve()
        self.assertEqual(eco.approver_id, self.approver)
        self.assertTrue(eco.stage_id.is_approved_stage)
        new_bodies = eco.message_ids.filtered(
            lambda m: m.id not in before_ids
        ).mapped("body")
        self.assertTrue(
            any("EMERGENCY BYPASS" in (b or "") for b in new_bodies),
            "bypass use must post an EMERGENCY BYPASS chatter line",
        )

    # ------------------------------------------------------------------
    # Four-eyes: apply (checks eco.user_id specifically)
    # ------------------------------------------------------------------
    def test_self_apply_blocked_four_eyes(self):
        product = self.env["product.product"].create(
            {"name": "Self Apply FourEyes Cabinet", "type": "consu"})
        bom = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0})
        eco = self.Eco.with_user(self.approver).create({
            "title": "Self apply test",
            "eco_type_id": self.type_bom.id,
            "bom_id": bom.id,
        })
        # An independent approver clears the approve-side four-eyes check.
        eco.with_user(self.approver2).action_approve()
        self.assertTrue(eco.approver_id)
        self.assertTrue(eco.stage_id.is_approved_stage)
        # self.approver is eco.user_id (Responsible) — apply's own four-eyes
        # check blocks it even though approval already happened, and even
        # though self.approver has the PLM Approver group.
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_apply()
        self.assertNotEqual(eco.state, "applied")
        # emergency_bypass unblocks it and logs the break-glass use.
        eco.with_user(self.approver).write({
            "emergency_bypass": True,
            "bypass_reason": "No other approver reachable.",
        })
        before_ids = set(eco.message_ids.ids)
        eco.with_user(self.approver).action_apply()
        self.assertEqual(eco.state, "applied")
        new_bodies = eco.message_ids.filtered(
            lambda m: m.id not in before_ids
        ).mapped("body")
        self.assertTrue(
            any("EMERGENCY BYPASS" in (b or "") for b in new_bodies),
            "bypass use on apply must also post an EMERGENCY BYPASS chatter line",
        )

    # ------------------------------------------------------------------
    # Terminal-ECO write immutability
    # ------------------------------------------------------------------
    def test_terminal_write_blocked_outside_allowlist(self):
        eco = self._make_rule_eco("Terminal write guard")
        self._approve_and_apply(eco)
        self.assertEqual(eco.state, "applied")
        with self.assertRaises(UserError):
            eco.with_user(self.approver).write({"title": "Rewriting history"})
        # Superuser is not exempt either — the guard is state-based, not
        # group-based.
        with self.assertRaises(UserError):
            eco.write({"title": "Rewriting history as superuser"})

    def test_terminal_write_allowed_with_plm_lifecycle_context(self):
        eco = self._make_rule_eco("Terminal write lifecycle bypass")
        self._approve_and_apply(eco)
        eco.with_context(plm_lifecycle=True).write(
            {"title": "Provenance-authorised edit"}
        )
        self.assertEqual(eco.title, "Provenance-authorised edit")
        # Allowlisted fields (color) are fine even WITHOUT the context flag —
        # they're outside the guard entirely, not merely exempted by it.
        eco.write({"color": 3})
        self.assertEqual(eco.color, 3)

    # ------------------------------------------------------------------
    # Terminal-ECO unlink immutability
    # ------------------------------------------------------------------
    def test_terminal_unlink_blocked_normal_user(self):
        eco = self._make_rule_eco("Terminal unlink guard")
        self._approve_and_apply(eco)
        with self.assertRaises(UserError):
            eco.with_user(self.approver).unlink()
        self.assertTrue(eco.exists())

    def test_terminal_unlink_blocked_even_for_superuser(self):
        eco = self._make_rule_eco("Terminal unlink guard superuser")
        self._approve_and_apply(eco)
        with self.assertRaises(UserError):
            eco.unlink()  # cls.env is SUPERUSER_ID in TransactionCase
        self.assertTrue(eco.exists())

    def test_rejected_eco_unlink_blocked(self):
        """The guard is state != 'open', not state == 'applied' — a
        rejected ECO is equally protected."""
        eco = self._make_rule_eco("Reject then try unlink")
        eco.with_user(self.approver).action_reject()
        self.assertEqual(eco.state, "rejected")
        with self.assertRaises(UserError):
            eco.unlink()

    # ------------------------------------------------------------------
    # action_reset_draft: rejected-only scope
    # ------------------------------------------------------------------
    def test_reset_draft_blocked_for_applied_even_for_approver(self):
        eco = self._make_rule_eco("Reset applied blocked")
        self._approve_and_apply(eco)
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_reset_draft()
        self.assertEqual(eco.state, "applied")

    def test_reset_draft_works_for_rejected(self):
        eco = self._make_rule_eco("Reset rejected works")
        eco.with_user(self.approver).action_reject()
        self.assertEqual(eco.state, "rejected")
        eco.with_user(self.approver).action_reset_draft()
        self.assertEqual(eco.state, "open")
        self.assertFalse(eco.approver_id)
        self.assertFalse(eco.approval_date)
        self.assertFalse(eco.applied_date)

    # ------------------------------------------------------------------
    # action_create_superseding
    # ------------------------------------------------------------------
    def test_create_superseding_links_and_targets_new_head(self):
        product = self.env["product.product"].create(
            {"name": "Supersede Target Cabinet", "type": "consu"})
        bom = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0})
        eco = self.Eco.create({
            "title": "Rev the supersede-target BoM",
            "eco_type_id": self.type_bom.id,
            "bom_id": bom.id,
        })
        self._approve_and_apply(eco)
        self.assertEqual(eco.state, "applied")
        new_head = eco.new_bom_id
        self.assertTrue(new_head)

        action = eco.with_user(self.approver).action_create_superseding()
        self.assertEqual(action.get("type"), "ir.actions.act_window")
        self.assertEqual(action.get("res_model"), "southbrook.eco")
        new_eco = self.Eco.browse(action["res_id"])

        self.assertEqual(eco.superseded_by_id, new_eco)
        self.assertEqual(new_eco.supersedes_id, eco)
        self.assertEqual(new_eco.state, "open")
        # Targets the CURRENT head (the version THIS ECO produced) — not the
        # archived original bom_id.
        self.assertEqual(new_eco.bom_id, new_head)

    def test_create_superseding_blocked_unless_applied(self):
        eco = self._make_rule_eco("Not applied, cannot supersede")
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_create_superseding()

    # ------------------------------------------------------------------
    # mrp.bom version-collision uniqueness
    # ------------------------------------------------------------------
    def test_bom_version_collision_blocked(self):
        product = self.env["product.product"].create(
            {"name": "Version Collision Cabinet", "type": "consu"})
        tmpl = product.product_tmpl_id
        self.Bom.create({
            "product_tmpl_id": tmpl.id,
            "product_qty": 1.0,
            "southbrook_version": 2,
            "active": True,
        })
        with self.assertRaises(ValidationError):
            self.Bom.create({
                "product_tmpl_id": tmpl.id,
                "product_qty": 1.0,
                "southbrook_version": 2,
                "active": True,
            })

    # ------------------------------------------------------------------
    # Target-required @api.constrains
    # ------------------------------------------------------------------
    def test_bom_eco_requires_bom_id(self):
        with self.assertRaises(ValidationError):
            self.Eco.create(
                {"title": "No BoM target", "eco_type_id": self.type_bom.id}
            )
        # Write-time too: clearing bom_id off an existing bom-kind ECO.
        product = self.env["product.product"].create(
            {"name": "Target Required Cabinet", "type": "consu"})
        bom = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0})
        eco = self.Eco.create({
            "title": "Has BoM target",
            "eco_type_id": self.type_bom.id,
            "bom_id": bom.id,
        })
        with self.assertRaises(ValidationError):
            eco.write({"bom_id": False})

    # ------------------------------------------------------------------
    # southbrook.cut.spec ondelete guard
    # ------------------------------------------------------------------
    def test_cut_spec_ondelete_blocked_unless_draft(self):
        with self.assertRaises(UserError):
            self.Spec.browse(self.seed_spec.id).unlink()
        draft_spec = self.Spec.create({"name": "Disposable draft spec"})
        self.assertEqual(draft_spec.state, "draft")
        draft_spec.unlink()
        self.assertFalse(draft_spec.exists())

    # ------------------------------------------------------------------
    # Record-rule scoping: PLM User writes only own open ECOs
    # ------------------------------------------------------------------
    def test_plm_user_record_rule_scoping(self):
        eco = self.Eco.with_user(self.plain_user).create({
            "title": "Owned by plain_user",
            "eco_type_id": self.type_document.id,
        })
        self.assertEqual(eco.user_id, self.plain_user)
        # Owner may write their own open ECO.
        eco.with_user(self.plain_user).write({"title": "Edited by owner"})
        self.assertEqual(self.Eco.browse(eco.id).title, "Edited by owner")
        # A different PLM User (non-approver, non-owner) is scoped out by
        # the record rule, even though the ir.model.access.csv row grants
        # the group perm_write in general.
        with self.assertRaises(AccessError):
            eco.with_user(self.plain_user2).write({"title": "Sneaky edit"})
