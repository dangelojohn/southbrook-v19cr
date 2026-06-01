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

    # ------------------------------------------------------------------
    # mrp.bom ECO smart button (count + action)
    # ------------------------------------------------------------------
    def test_bom_eco_count_and_smart_button(self):
        """A BoM's southbrook_eco_count + action_open_southbrook_ecos
        should round-trip cleanly: zero ECOs => 0 count, raising two ECOs
        => count=2, action returns an ir.actions.act_window filtered to
        that BoM with the default-create context pre-set.
        """
        product = self.env["product.product"].create(
            {"name": "Smart-button test", "type": "consu"}
        )
        bom = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0}
        )

        # Baseline: no ECOs against this BoM.
        self.assertEqual(bom.southbrook_eco_count, 0)
        self.assertEqual(len(bom.southbrook_eco_ids), 0)

        # Raise two ECOs referencing the BoM.
        eco_a = self.Eco.create(
            {
                "title": "Smart-btn ECO A",
                "eco_type_id": self.type_bom.id,
                "bom_id": bom.id,
            }
        )
        eco_b = self.Eco.create(
            {
                "title": "Smart-btn ECO B",
                "eco_type_id": self.type_bom.id,
                "bom_id": bom.id,
            }
        )
        bom.invalidate_recordset(["southbrook_eco_ids", "southbrook_eco_count"])

        self.assertEqual(bom.southbrook_eco_count, 2)
        self.assertEqual(set(bom.southbrook_eco_ids.ids), {eco_a.id, eco_b.id})

        # Smart-button action shape.
        action = bom.action_open_southbrook_ecos()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "southbrook.eco")
        self.assertIn(("bom_id", "=", bom.id), action["domain"])
        self.assertEqual(action["context"]["default_bom_id"], bom.id)
        self.assertEqual(
            action["context"]["default_eco_type_id"], self.type_bom.id
        )

    # ------------------------------------------------------------------
    # Approver-stamp semantics on action_apply
    # ------------------------------------------------------------------
    def test_action_apply_stamps_approver_when_not_preset(self):
        """When action_apply runs without a prior action_approve, the
        applier is the de-facto approver — approver_id + approval_date
        get stamped on the ECO at apply time.
        """
        candidate = self.Spec.create(
            {
                "name": "Apply-stamp test",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 3.5},
            }
        )
        eco = self.Eco.create(
            {
                "title": "Apply without prior approve",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        self.assertFalse(eco.approver_id)
        self.assertFalse(eco.approval_date)

        eco.with_user(self.approver).action_apply()

        self.assertEqual(eco.approver_id, self.approver)
        self.assertTrue(eco.approval_date)
        self.assertEqual(eco.state, "applied")

    def test_document_eco_requires_attached_documents(self):
        """A document-kind ECO with empty document_ids must raise on
        action_apply. Symmetric to test_rule_eco_requires_git_ref +
        test_cut_spec_eco_requires_candidate. Bug found by demo
        walkthrough 2026-06-01 part 16: document ECOs were applying
        with zero attachments and posting 'recorded 0 attachment(s)'.
        """
        eco = self.Eco.create(
            {
                "title": "Phantom document ECO",
                "eco_type_id": self.env.ref(
                    "southbrook_plm.eco_type_document"
                ).id,
            }
        )
        with self.assertRaises(ValidationError):
            eco.with_user(self.approver).action_apply()

    def test_document_eco_applies_with_attached_documents(self):
        """Regression check: the new document validation must NOT
        block legitimate document ECOs with attached files.
        """
        attachment = self.env["ir.attachment"].create(
            {
                "name": "test-spec.pdf",
                "datas": "cGRm",  # 'pdf' base64
                "mimetype": "application/pdf",
            }
        )
        eco = self.Eco.create(
            {
                "title": "Real document ECO",
                "eco_type_id": self.env.ref(
                    "southbrook_plm.eco_type_document"
                ).id,
                "document_ids": [(4, attachment.id)],
            }
        )
        eco.with_user(self.approver).action_apply()
        self.assertEqual(eco.state, "applied")
        self.assertEqual(eco.document_count, 1)

    def test_action_reject_blocked_on_already_applied(self):
        """Rejecting an applied ECO must raise. Bug found by demo
        walkthrough 2026-06-01 part 10: action_reject silently
        flipped an applied ECO's state to 'rejected' while leaving
        the apply effects (BoM version bump, cut-spec activation)
        in place. To revert an applied change, raise a new ECO.
        """
        eco = self.Eco.create(
            {
                "title": "Apply then try to reject",
                "eco_type_id": self.type_rule.id,
                "git_ref": "abc1234",
            }
        )
        eco.with_user(self.approver).action_apply()
        self.assertEqual(eco.state, "applied")

        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_reject()
        # State + stage unchanged after the block.
        self.assertEqual(eco.state, "applied")
        self.assertEqual(
            eco.stage_id, self.env.ref("southbrook_plm.stage_applied")
        )

    def test_action_apply_blocked_on_previously_rejected(self):
        """Applying a rejected ECO must raise. Bug found by demo
        walkthrough 2026-06-01 part 11: action_apply silently
        revived a rejected ECO (state flipped to 'applied') with no
        chatter audit. Reviving requires explicit action_reset_draft.
        """
        eco = self.Eco.create(
            {
                "title": "Reject then try to apply",
                "eco_type_id": self.type_rule.id,
                "git_ref": "def4567",
            }
        )
        eco.with_user(self.approver).action_reject()
        self.assertEqual(eco.state, "rejected")

        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_apply()
        # State unchanged after the block.
        self.assertEqual(eco.state, "rejected")

    def test_revival_path_via_reset_draft_then_apply(self):
        """Regression check: after action_reset_draft revives a
        rejected ECO, action_apply must succeed cleanly. Confirms
        the 'apply on rejected' block from the previous test isn't
        permanent — there's a legitimate path forward.
        """
        eco = self.Eco.create(
            {
                "title": "Reject, reset, apply",
                "eco_type_id": self.type_rule.id,
                "git_ref": "ghi8901",
            }
        )
        eco.with_user(self.approver).action_reject()
        self.assertEqual(eco.state, "rejected")

        # Explicit revival via reset_draft.
        eco.action_reset_draft()
        self.assertEqual(eco.state, "open")

        # Now apply works.
        eco.with_user(self.approver).action_apply()
        self.assertEqual(eco.state, "applied")

    def test_direct_write_to_terminal_stage_is_blocked(self):
        """Writing stage_id to Applied or Rejected without a matching
        state move must raise — terminal stages are reachable only
        via action_apply / action_reject. Bug found by demo
        walkthrough 2026-06-01 part 9: direct write of stage_id
        bypassed the apply handlers, leaving the ECO in a
        paradoxical 'stage=Applied, state=open' state.
        """
        applied_stage = self.env.ref("southbrook_plm.stage_applied")
        rejected_stage = self.env.ref("southbrook_plm.stage_rejected")
        eco = self.Eco.create(
            {
                "title": "Bypass attempt",
                "eco_type_id": self.type_rule.id,
            }
        )
        # Block: direct write of stage_id=Applied.
        with self.assertRaises(UserError):
            eco.write({"stage_id": applied_stage.id})
        # Block: direct write of stage_id=Rejected.
        with self.assertRaises(UserError):
            eco.write({"stage_id": rejected_stage.id})
        # State unchanged.
        self.assertNotEqual(eco.stage_id, applied_stage)
        self.assertNotEqual(eco.stage_id, rejected_stage)

    def test_action_apply_still_allowed_to_write_terminal(self):
        """The action_apply / action_reject paths co-write
        stage_id + state to matching terminal values, which the new
        guard explicitly allows. Regression check: the bypass-block
        from test_direct_write_to_terminal_stage_is_blocked must NOT
        accidentally also block the legitimate apply flow.
        """
        eco = self.Eco.create(
            {
                "title": "Legit apply",
                "eco_type_id": self.type_rule.id,
                "git_ref": "abc1234",
            }
        )
        # action_apply writes stage_id=Applied AND state=applied in
        # the same write call — must succeed.
        eco.with_user(self.approver).action_apply()
        applied_stage = self.env.ref("southbrook_plm.stage_applied")
        self.assertEqual(eco.stage_id, applied_stage)
        self.assertEqual(eco.state, "applied")

        # And action_reject writes stage_id=Rejected AND
        # state=rejected — also must succeed.
        eco2 = self.Eco.create(
            {
                "title": "Legit reject",
                "eco_type_id": self.type_rule.id,
            }
        )
        eco2.with_user(self.approver).action_reject()
        rejected_stage = self.env.ref("southbrook_plm.stage_rejected")
        self.assertEqual(eco2.stage_id, rejected_stage)
        self.assertEqual(eco2.state, "rejected")

    def test_action_advance_blocked_at_applied_terminal(self):
        """action_advance from a terminal stage must raise. Bug found
        by demo walkthrough 2026-06-01: advancing from Applied
        silently moved the ECO to Rejected (next-higher sequence).
        """
        applied_stage = self.env.ref("southbrook_plm.stage_applied")
        eco = self.Eco.create(
            {
                "title": "Stuck at Applied",
                "eco_type_id": self.type_rule.id,
                "stage_id": applied_stage.id,
                "state": "applied",
            }
        )
        with self.assertRaises(UserError):
            eco.action_advance()
        # State unchanged.
        self.assertEqual(eco.stage_id, applied_stage)

    def test_action_advance_blocked_at_rejected_terminal(self):
        """Symmetric to the Applied-terminal test: advancing from
        Rejected must also raise.
        """
        rejected_stage = self.env.ref("southbrook_plm.stage_rejected")
        eco = self.Eco.create(
            {
                "title": "Stuck at Rejected",
                "eco_type_id": self.type_rule.id,
                "stage_id": rejected_stage.id,
                "state": "rejected",
            }
        )
        with self.assertRaises(UserError):
            eco.action_advance()
        self.assertEqual(eco.stage_id, rejected_stage)

    def test_action_reset_draft_clears_lifecycle_end_fields(self):
        """action_reset_draft must clear applied_date, approver_id,
        and approval_date so the audit trail doesn't carry stale
        markers from a prior pass. Bug found by demo walkthrough
        2026-06-01: reset_draft left applied_date populated.
        """
        candidate = self.Spec.create(
            {
                "name": "Reset-draft test",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 3.7},
            }
        )
        eco = self.Eco.create(
            {
                "title": "To be reset",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(self.approver).action_apply()
        # Confirm post-apply state has the lifecycle-end fields set.
        self.assertTrue(eco.applied_date)
        self.assertTrue(eco.approver_id)
        self.assertTrue(eco.approval_date)
        self.assertEqual(eco.state, "applied")

        eco.action_reset_draft()

        self.assertEqual(eco.state, "open")
        self.assertFalse(eco.applied_date)
        self.assertFalse(eco.approver_id)
        self.assertFalse(eco.approval_date)
        # The reset stage is a non-terminal one (i.e. Draft).
        self.assertFalse(eco.stage_id.is_final)

    def test_action_reject_lands_in_rejected_stage(self):
        """action_reject must move the ECO to the Rejected stage
        specifically — not to the first is_final stage in sequence
        order (which would be Applied). Bug found by demo walkthrough
        2026-06-01.
        """
        rejected_stage = self.env.ref("southbrook_plm.stage_rejected")
        applied_stage = self.env.ref("southbrook_plm.stage_applied")
        # Sanity-check the seed: both are is_final, only one is
        # is_rejected_stage.
        self.assertTrue(rejected_stage.is_final)
        self.assertTrue(applied_stage.is_final)
        self.assertTrue(rejected_stage.is_rejected_stage)
        self.assertFalse(applied_stage.is_rejected_stage)

        eco = self.Eco.create(
            {
                "title": "Reject me",
                "eco_type_id": self.type_rule.id,
            }
        )
        eco.action_reject()
        self.assertEqual(eco.state, "rejected")
        self.assertEqual(eco.stage_id, rejected_stage)
        self.assertNotEqual(eco.stage_id, applied_stage)

    def test_action_apply_preserves_prior_approver(self):
        """When action_approve ran first (recording who approved when),
        action_apply must NOT overwrite approver_id/approval_date —
        the audit trail correctly captures approval and application
        as distinct events.
        """
        candidate = self.Spec.create(
            {
                "name": "Two-step apply test",
                "state": "draft",
                **{**ESTIMATING_DEFAULTS, "door_reveal": 3.6},
            }
        )
        eco = self.Eco.create(
            {
                "title": "Approve then apply",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        # First step: explicit action_approve stamps approver fields.
        eco.with_user(self.approver).action_approve()
        original_approver = eco.approver_id
        original_approval_date = eco.approval_date
        self.assertEqual(original_approver, self.approver)
        self.assertTrue(original_approval_date)

        # Second step: action_apply must leave approver_id /
        # approval_date untouched.
        eco.with_user(self.approver).action_apply()

        self.assertEqual(eco.approver_id, original_approver)
        self.assertEqual(eco.approval_date, original_approval_date)
        self.assertEqual(eco.state, "applied")
        self.assertTrue(eco.applied_date)

    def test_bom_smart_button_counts_versioning_provenance(self):
        """After _apply_bom versions a BoM, the NEW (active) BoM must
        also show the ECO in its smart-button count via the new_bom_id
        inverse — not just the OLD (archived) bom which has the ECO
        via bom_id. Bug found by demo walkthrough 2026-06-01: the
        provenance smart-button on the active BoM was 0, leaving the
        audit trail effectively unreachable from the post-apply form.
        """
        product = self.env["product.product"].create(
            {"name": "Provenance test cabinet", "type": "consu"}
        )
        bom_v1 = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0}
        )
        eco = self.Eco.create(
            {
                "title": "Version-bump test",
                "eco_type_id": self.type_bom.id,
                "bom_id": bom_v1.id,
            }
        )
        # Pre-apply: smart-button on v1 = 1, no v2 yet.
        bom_v1.invalidate_recordset(
            ["southbrook_eco_ids", "southbrook_eco_history_ids",
             "southbrook_eco_count"]
        )
        self.assertEqual(bom_v1.southbrook_eco_count, 1)

        eco.with_user(self.approver).action_apply()

        bom_v2 = eco.new_bom_id
        self.assertTrue(bom_v2)
        self.assertNotEqual(bom_v1, bom_v2)
        # Bust the caches so the inverse fields recompute against
        # the post-apply DB state.
        (bom_v1 | bom_v2).invalidate_recordset(
            ["southbrook_eco_ids", "southbrook_eco_history_ids",
             "southbrook_eco_count"]
        )

        # Old BoM: ECO appears via bom_id (target side).
        self.assertIn(eco, bom_v1.southbrook_eco_ids)
        self.assertEqual(bom_v1.southbrook_eco_count, 1)
        # New BoM: same ECO appears via new_bom_id (provenance side).
        self.assertIn(eco, bom_v2.southbrook_eco_history_ids)
        self.assertEqual(bom_v2.southbrook_eco_count, 1)

        # Action surface: both BoMs' smart-button click-through
        # opens a list filtered by OR domain — same ECO visible from
        # either side.
        action_v1 = bom_v1.action_open_southbrook_ecos()
        action_v2 = bom_v2.action_open_southbrook_ecos()
        self.assertIn("|", action_v1["domain"])
        self.assertIn("|", action_v2["domain"])
        # The default-create context on each pre-fills bom_id with
        # ITS OWN id — clicking 'New' from v1 raises a fresh ECO
        # against v1; clicking 'New' from v2 against v2.
        self.assertEqual(action_v1["context"]["default_bom_id"], bom_v1.id)
        self.assertEqual(action_v2["context"]["default_bom_id"], bom_v2.id)

    def test_cut_spec_eco_count_and_smart_button(self):
        """Symmetric to the BoM smart-button test: a cut spec's
        southbrook_eco_count + action_open_southbrook_ecos round-trip
        the candidate-ECO audit trail. Two cut-kind ECOs referencing a
        draft spec yield count=2 + a filtered action shape with the
        cut-kind ECO type in the default-create context.
        """
        candidate = self.Spec.create(
            {
                "name": "Smart-btn candidate",
                "state": "draft",
                **ESTIMATING_DEFAULTS,
            }
        )

        # Baseline: no ECOs propose this spec yet.
        self.assertEqual(candidate.southbrook_eco_count, 0)
        self.assertEqual(len(candidate.southbrook_eco_ids), 0)

        eco_a = self.Eco.create(
            {
                "title": "Spec smart-btn ECO A",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco_b = self.Eco.create(
            {
                "title": "Spec smart-btn ECO B",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        candidate.invalidate_recordset(
            ["southbrook_eco_ids", "southbrook_eco_count"]
        )

        self.assertEqual(candidate.southbrook_eco_count, 2)
        self.assertEqual(
            set(candidate.southbrook_eco_ids.ids), {eco_a.id, eco_b.id}
        )

        action = candidate.action_open_southbrook_ecos()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "southbrook.eco")
        self.assertIn(("cut_spec_id", "=", candidate.id), action["domain"])
        self.assertEqual(
            action["context"]["default_cut_spec_id"], candidate.id
        )
        self.assertEqual(
            action["context"]["default_eco_type_id"], self.type_cut.id
        )
