# SPDX-License-Identifier: LGPL-3.0-only
"""Batch-2 security-hardening contract tests for southbrook.eco /
southbrook.cut.spec.

Written to the CONTRACT for the parallel hardening pass landing alongside
this file (2026-08-02), not to whatever partial state models/southbrook_eco.py,
models/southbrook_cut_spec.py, models/southbrook_eco_stage.py, or
models/mrp_bom.py happen to be in at any given moment. Expect failures until
that pass lands the corresponding guards — that's the point of writing tests
first.

Contract covered (see the security-hardening batch brief for full text):

  1. Lifecycle sentinel — guards must compare `is PLM_LIFECYCLE` (the
     in-process singleton importable from
     odoo.addons.southbrook_plm.models.plm_lifecycle), not a forgeable
     `plm_lifecycle=True` context flag.
  2. Approved-stage gate fails closed when no stage carries
     is_approved_stage; at most one stage may carry the flag.
  3. Bare write of stage_id into the is_approved_stage stage is blocked
     even for an approver — only action_approve may place an ECO there.
  4. No double action_create_superseding on the same applied ECO.
  5. emergency_bypass / bypass_reason are approver-only writes, server-side;
     arming posts a chatter line.
  6. southbrook.cut.spec.action_activate() is ECO-lifecycle-only; a bare
     `state` write is blocked outside lifecycle authorisation; `active` and
     geometry writes still work.
  7. mrp.bom / southbrook.cut.spec are FK-restricted against unlink while
     referenced by an open ECO's bom_id / cut_spec_id.
  8. action_create_superseding hygiene: empty document_ids, non-identical
     (stub) description on the successor.
  9. Cut-spec apply posts a geometry-delta chatter message.
  10. Idempotency/conflict signal: a second approve of an already-approved
      ECO raises rather than silently re-stamping.

Plus: the impact-scan savepoint/logging fix — a raising
_scan_and_flag_affected_mos must never block an apply.

TransactionCase runs as SUPERUSER: superuser passes every has_group() check
and every @api.constrains / @api.ondelete / explicit Python guard still
fires for it — so every gating assertion below runs via .with_user() with a
real, non-superuser fixture user, and the immutability/FK-restrict
assertions are exercised directly against superuser where the contract says
the guard is state-based, not group-based.

Run with:
    odoo-bin --test-enable --stop-after-init \\
        -i southbrook_plm -d <db>
"""
import base64
from unittest.mock import patch

import psycopg2

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.southbrook_plm.models.plm_lifecycle import PLM_LIFECYCLE


@tagged("post_install", "-at_install")
class TestSouthbrookPlmGovernance2(TransactionCase):
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

        # Three independent approvers so the double-approve test (item 10)
        # can show a DIFFERENT approver still gets refused on the second
        # call, not merely "the same approver can't re-approve".
        cls.approver = new_test_user(
            cls.env,
            login="plm_gov2_approver",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )
        cls.approver2 = new_test_user(
            cls.env,
            login="plm_gov2_approver2",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )
        cls.approver3 = new_test_user(
            cls.env,
            login="plm_gov2_approver3",
            groups="southbrook_plm.group_southbrook_plm_approver,base.group_user",
        )
        cls.plain_user = new_test_user(
            cls.env,
            login="plm_gov2_user",
            groups="southbrook_plm.group_southbrook_plm_user,base.group_user",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _make_rule_eco(self, title, git_ref="cafed00d0", **extra):
        """A rule-kind ECO is the lightest fixture that satisfies
        _check_target_required (git_ref isn't even required at draft time
        for 'rule', but action_apply's _apply_rule needs it) — use it for
        tests exercising generic lifecycle plumbing rather than a specific
        target kind's apply mechanics."""
        vals = {"title": title, "eco_type_id": self.type_rule.id, "git_ref": git_ref}
        vals.update(extra)
        return self.Eco.create(vals)

    def _approve_and_apply(self, eco, approver=None):
        approver = approver or self.approver
        eco.with_user(approver).action_approve()
        eco.with_user(approver).action_apply()
        return eco

    def _make_bom_eco(self, title, approver_default=None, **extra):
        product = self.env["product.product"].create(
            {"name": "%s Cabinet" % title, "type": "consu"}
        )
        bom = self.Bom.create(
            {"product_tmpl_id": product.product_tmpl_id.id, "product_qty": 1.0}
        )
        vals = {"title": title, "eco_type_id": self.type_bom.id, "bom_id": bom.id}
        vals.update(extra)
        eco = self.Eco.create(vals)
        return eco, bom

    # ==================================================================
    # Contract item 1 — lifecycle sentinel (the forgery-defence tests)
    # ==================================================================
    def test_forged_true_context_write_blocked_on_applied_eco(self):
        """The single most important test in this file: a forged boolean
        `True` must NOT satisfy the lifecycle guard on an already-applied
        ECO — only the actual PLM_LIFECYCLE sentinel object may authorise
        a write past it."""
        eco = self._make_rule_eco("Forgery defence: boolean True")
        self._approve_and_apply(eco)
        self.assertEqual(eco.state, "applied")
        with self.assertRaises(UserError):
            eco.with_context(plm_lifecycle=True).write(
                {"title": "Forged via boolean True"}
            )
        self.assertNotEqual(eco.title, "Forged via boolean True")

    def test_sentinel_context_write_allowed_on_applied_eco(self):
        eco = self._make_rule_eco("Sentinel authorises write")
        self._approve_and_apply(eco)
        eco.with_context(plm_lifecycle=PLM_LIFECYCLE).write(
            {"title": "Authorised via sentinel"}
        )
        self.assertEqual(eco.title, "Authorised via sentinel")

    def test_forged_true_context_create_in_terminal_state_blocked(self):
        for state in ("applied", "rejected"):
            with self.subTest(state=state):
                with self.assertRaises(UserError):
                    self.Eco.with_context(plm_lifecycle=True).create(
                        {
                            "title": "Forged create %s" % state,
                            "eco_type_id": self.type_rule.id,
                            "git_ref": "forge%s01" % state[:3],
                            "state": state,
                        }
                    )

    def test_sentinel_context_create_in_terminal_state_allowed(self):
        for state in ("applied", "rejected"):
            with self.subTest(state=state):
                eco = self.Eco.with_context(plm_lifecycle=PLM_LIFECYCLE).create(
                    {
                        "title": "Sentinel create %s" % state,
                        "eco_type_id": self.type_rule.id,
                        "git_ref": "sentin%s01" % state[:3],
                        "state": state,
                    }
                )
                self.assertEqual(eco.state, state)

    # ==================================================================
    # Contract item 2 — approved-stage gate fails closed
    # ==================================================================
    def test_approved_stage_gate_fails_closed_for_approve(self):
        self.stage_approved.write({"is_approved_stage": False})
        eco = self._make_rule_eco("No approved stage configured, approve")
        with self.assertRaises(UserError):
            eco.with_user(self.approver2).action_approve()
        self.assertFalse(eco.approver_id)

    def test_approved_stage_gate_fails_closed_for_apply(self):
        eco = self._make_rule_eco("No approved stage configured, apply")
        eco.with_user(self.approver2).action_approve()
        self.assertEqual(eco.stage_id, self.stage_approved)
        # Reconfigure the pipeline out from under the already-approved ECO.
        self.stage_approved.write({"is_approved_stage": False})
        with self.assertRaises(UserError):
            eco.with_user(self.approver2).action_apply()
        self.assertNotEqual(eco.state, "applied")

    def test_only_one_stage_may_carry_is_approved_stage(self):
        with self.assertRaises(ValidationError):
            self.stage_review.write({"is_approved_stage": True})

    # ==================================================================
    # Contract item 3 — stage entry gated (only action_approve may land
    # an ECO in the Approved stage)
    # ==================================================================
    def test_bare_write_into_approved_stage_blocked_even_for_approver(self):
        eco = self._make_rule_eco("Bare write into approved stage")
        with self.assertRaises(UserError):
            eco.with_user(self.approver).write({"stage_id": self.stage_approved.id})
        self.assertNotEqual(eco.stage_id, self.stage_approved)

    def test_action_approve_lands_eco_in_approved_stage(self):
        eco = self._make_rule_eco("Approve lands in approved stage")
        eco.with_user(self.approver2).action_approve()
        self.assertEqual(eco.stage_id, self.stage_approved)

    # ==================================================================
    # Contract item 4 — no double supersede
    # ==================================================================
    def test_no_double_supersede(self):
        eco, _bom = self._make_bom_eco("Supersede once only")
        self._approve_and_apply(eco)
        action = eco.with_user(self.approver).action_create_superseding()
        first_successor = self.Eco.browse(action["res_id"])
        with self.assertRaises(UserError):
            eco.with_user(self.approver).action_create_superseding()
        self.assertEqual(eco.superseded_by_id, first_successor)

    # ==================================================================
    # Contract item 5 — emergency bypass is approver-only, server-side
    # ==================================================================
    def test_plain_user_cannot_set_emergency_bypass_on_own_open_eco(self):
        eco = self.Eco.with_user(self.plain_user).create(
            {
                "title": "Plain user's own ECO (bypass attempt)",
                "eco_type_id": self.type_document.id,
            }
        )
        with self.assertRaises(UserError):
            eco.with_user(self.plain_user).write(
                {
                    "emergency_bypass": True,
                    "bypass_reason": "Trying to self-serve a bypass.",
                }
            )
        self.assertFalse(eco.emergency_bypass)

    def test_plain_user_cannot_set_bypass_reason_alone(self):
        eco = self.Eco.with_user(self.plain_user).create(
            {
                "title": "Plain user's own ECO (reason-only attempt)",
                "eco_type_id": self.type_document.id,
            }
        )
        with self.assertRaises(UserError):
            eco.with_user(self.plain_user).write(
                {"bypass_reason": "Just setting the reason field."}
            )
        self.assertFalse(eco.bypass_reason)

    def test_approver_may_set_emergency_bypass_and_it_posts_chatter(self):
        eco = self._make_rule_eco("Approver arms the bypass")
        before_ids = set(eco.message_ids.ids)
        eco.with_user(self.approver).write(
            {
                "emergency_bypass": True,
                "bypass_reason": "Solo on-call, no other approver reachable.",
            }
        )
        self.assertTrue(eco.emergency_bypass)
        new_bodies = " ".join(
            (m.body or "").lower()
            for m in eco.message_ids.filtered(lambda m: m.id not in before_ids)
        )
        self.assertTrue(
            "bypass" in new_bodies or "emergency" in new_bodies,
            "arming emergency bypass must post a chatter line mentioning it",
        )

    # ==================================================================
    # Contract item 6 — cut-spec activation is ECO-lifecycle-only
    # ==================================================================
    def test_cut_spec_action_activate_direct_call_blocked(self):
        candidate = self.Spec.create({"name": "Direct activate blocked"})
        with self.assertRaises(UserError):
            candidate.with_user(self.approver).action_activate()
        self.assertEqual(candidate.state, "draft")

    def test_cut_spec_action_activate_with_sentinel_context_succeeds(self):
        candidate = self.Spec.create({"name": "Sentinel activate succeeds"})
        candidate.with_context(plm_lifecycle=PLM_LIFECYCLE).action_activate()
        self.assertEqual(candidate.state, "active")

    def test_cut_spec_eco_apply_activates_spec_end_to_end(self):
        candidate = self.Spec.create(
            {"name": "ECO-driven activation", "door_reveal": 3.5}
        )
        eco = self.Eco.create(
            {
                "title": "Activate candidate via ECO",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        eco.with_user(self.approver2).action_approve()
        eco.with_user(self.approver2).action_apply()
        self.assertEqual(eco.state, "applied")
        self.assertEqual(candidate.state, "active")
        self.assertEqual(self.seed_spec.state, "superseded")

    def test_cut_spec_bare_state_write_blocked_outside_lifecycle(self):
        candidate = self.Spec.create({"name": "Bare state write blocked"})
        with self.assertRaises(UserError):
            candidate.with_user(self.approver).write({"state": "active"})
        self.assertEqual(candidate.state, "draft")

    def test_cut_spec_archive_and_geometry_write_still_work(self):
        candidate = self.Spec.create(
            {"name": "Archive and geometry still work", "door_reveal": 3.0}
        )
        v0 = candidate.version
        candidate.with_user(self.approver).write({"active": False})
        self.assertFalse(candidate.active)
        candidate.with_user(self.approver).write({"door_reveal": 4.0})
        self.assertEqual(candidate.version, v0 + 1)

    # ==================================================================
    # Contract item 7 — target FK restrict
    # ==================================================================
    def test_unlink_bom_referenced_by_open_eco_restricted(self):
        eco, bom = self._make_bom_eco("FK restrict bom target")
        with self.assertRaises(psycopg2.IntegrityError), mute_logger(
            "odoo.sql_db"
        ):
            bom.unlink()
        self.assertTrue(bom.exists())
        self.assertEqual(eco.bom_id, bom)

    def test_unlink_draft_cut_spec_referenced_by_open_eco_restricted(self):
        candidate = self.Spec.create({"name": "FK restrict cut-spec target"})
        eco = self.Eco.create(
            {
                "title": "FK restrict cut-spec target ECO",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        self.assertEqual(candidate.state, "draft")
        with self.assertRaises(psycopg2.IntegrityError), mute_logger(
            "odoo.sql_db"
        ):
            candidate.unlink()
        self.assertTrue(candidate.exists())
        self.assertEqual(eco.cut_spec_id, candidate)

    # ==================================================================
    # Contract item 8 — superseding hygiene
    # ==================================================================
    def test_superseding_successor_has_empty_documents_and_stub_description(self):
        eco, _bom = self._make_bom_eco(
            "Supersede hygiene predecessor",
            description="<p>Detailed engineering rationale for this revision.</p>",
        )
        attachment = self.env["ir.attachment"].create(
            {
                "name": "spec-sheet.pdf",
                "datas": base64.b64encode(b"%PDF-1.4 fake spec sheet contents"),
                "res_model": "southbrook.eco",
                "res_id": eco.id,
            }
        )
        eco.write({"document_ids": [(4, attachment.id)]})
        self.assertTrue(eco.document_ids)
        self._approve_and_apply(eco)

        action = eco.with_user(self.approver).action_create_superseding()
        new_eco = self.Eco.browse(action["res_id"])

        self.assertFalse(new_eco.document_ids)
        self.assertNotEqual(new_eco.description, eco.description)

    # ==================================================================
    # Contract item 9 — cut-spec apply posts a geometry-delta chatter
    # ==================================================================
    def test_cut_spec_apply_posts_geometry_delta_chatter(self):
        # Every OTHER geometric field is left at its model default, which
        # mirrors the seed spec exactly (see ESTIMATING_DEFAULTS in
        # test_eco_workflow.py) — only door_reveal actually changes, so the
        # "unchanged fields are not listed" half of the assertion is
        # meaningful rather than vacuous.
        candidate = self.Spec.create(
            {"name": "Geometry delta candidate", "door_reveal": 4.0}
        )
        eco = self.Eco.create(
            {
                "title": "Bump door reveal 3mm to 4mm",
                "eco_type_id": self.type_cut.id,
                "cut_spec_id": candidate.id,
            }
        )
        before_ids = set(eco.message_ids.ids)
        eco.with_user(self.approver2).action_approve()
        eco.with_user(self.approver2).action_apply()
        new_bodies = " ".join(
            (m.body or "").lower()
            for m in eco.message_ids.filtered(lambda m: m.id not in before_ids)
        )
        self.assertIn("4", new_bodies, "chatter must mention the new value")
        self.assertTrue(
            any(tok in new_bodies for tok in ("reveal", "door_reveal", "door reveal")),
            "chatter must mention the changed field by a reveal-ish label",
        )
        # box_th (15.875) is untouched — an unchanged field must not be
        # listed in the delta. 15.875 is a distinctive-enough value that it
        # can only appear here if the (wrong) implementation dumps every
        # field rather than just the delta.
        self.assertNotIn("15.875", new_bodies)

    # ==================================================================
    # Contract item 10 — idempotency/conflict signal on double approve
    # ==================================================================
    def test_second_approve_of_already_approved_eco_by_different_approver_raises(
        self,
    ):
        eco = self._make_rule_eco("Double approve guard")
        eco.with_user(self.approver2).action_approve()
        self.assertEqual(eco.approver_id, self.approver2)
        with self.assertRaises(UserError):
            eco.with_user(self.approver3).action_approve()
        self.assertEqual(eco.approver_id, self.approver2)

    # ==================================================================
    # ALSO — impact-scan failure must never block an apply
    # ==================================================================
    def test_impact_scan_failure_does_not_block_bom_apply(self):
        eco, _bom = self._make_bom_eco("Impact scan raises, apply must still land")
        eco.with_user(self.approver2).action_approve()
        with patch.object(
            type(eco), "_scan_and_flag_affected_mos", side_effect=RuntimeError("boom")
        ):
            eco.with_user(self.approver2).action_apply()
        self.assertEqual(eco.state, "applied")
        self.assertTrue(eco.new_bom_id)
