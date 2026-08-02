# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.eco — the Engineering Change Order.

The single piece of genuine workflow logic in this module. An ECO moves
through the user-configurable ``southbrook.eco.stage`` pipeline; reaching a
stage flagged ``is_applied_stage`` commits the change via :meth:`action_apply`,
which branches on the ECO type's ``target_kind``:

    bom       -> copy the target mrp.bom to a new southbrook_version, archive
                 the prior version.
    cut_spec  -> activate the candidate southbrook.cut.spec (supersede current).
    rule      -> record-only: the code change lands via git; git_ref captures it.
    document  -> record-only: the engineering documents ride the chatter/links.

Approval gating: leaving a stage flagged ``approval_required`` demands the actor
be in ``southbrook_plm.group_southbrook_plm_approver``.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SouthbrookEco(models.Model):
    _name = "southbrook.eco"
    _description = "Southbrook Engineering Change Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, create_date desc, id desc"

    name = fields.Char(
        default=lambda self: _("New"),
        copy=False,
        readonly=True,
        index=True,
    )
    title = fields.Char(
        required=True,
        tracking=True,
        help="One-line summary of the change.",
    )
    description = fields.Html(
        help="What is changing and why. The engineering rationale.",
    )
    eco_type_id = fields.Many2one(
        "southbrook.eco.type",
        required=True,
        tracking=True,
        ondelete="restrict",
    )
    target_kind = fields.Selection(
        related="eco_type_id.target_kind",
        store=True,
        readonly=True,
    )
    stage_id = fields.Many2one(
        "southbrook.eco.stage",
        tracking=True,
        index=True,
        group_expand="_read_group_stage_ids",
        default=lambda self: self._default_stage(),
        copy=False,
    )
    # Related pass-through of the current stage's is_approved_stage flag,
    # purely so the form view can drive its Apply-button / warning
    # visibility off a plain field instead of an inline stage_id.is_
    # dotted-path (Odoo view domains/attrs can't dot into a m2o's field
    # without one of these). action_apply enforces the same condition
    # server-side (see below) — this field is a view modifier only, not
    # a security boundary.
    stage_is_approved = fields.Boolean(
        related="stage_id.is_approved_stage",
        string="Stage Is Approved",
    )
    state = fields.Selection(
        [
            ("open", "Open"),
            ("applied", "Applied"),
            ("rejected", "Rejected"),
        ],
        default="open",
        required=True,
        tracking=True,
        copy=False,
        help="Lifecycle outcome, distinct from the (configurable) Kanban stage.",
    )
    priority = fields.Selection(
        [("0", "Normal"), ("1", "High"), ("2", "Urgent")],
        default="0",
    )
    color = fields.Integer()
    user_id = fields.Many2one(
        "res.users",
        string="Responsible",
        default=lambda self: self.env.user,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
    )

    # ---- Targets (visibility driven by target_kind in the view). ----
    bom_id = fields.Many2one(
        "mrp.bom",
        string="Target BoM",
        help="The canonical template BoM this ECO versions.",
    )
    new_bom_id = fields.Many2one(
        "mrp.bom",
        string="New BoM Version",
        readonly=True,
        copy=False,
        help="The new BoM version created when this ECO was applied.",
    )
    cut_spec_id = fields.Many2one(
        "southbrook.cut.spec",
        string="Candidate Cut Spec",
        help="The draft cut spec this ECO activates on apply.",
    )
    git_ref = fields.Char(
        string="Git Reference",
        help="Commit SHA or PR URL for a construction-rule / code change "
        "(target kind = rule). The audit link to git, which remains the "
        "system of record for code-resident rules.",
    )
    document_ids = fields.Many2many(
        "ir.attachment",
        "southbrook_eco_attachment_rel",
        "eco_id",
        "attachment_id",
        string="Engineering Documents",
        help="Vendor cut sheets, hardware spec PDFs, shop drawings.",
    )
    document_count = fields.Integer(compute="_compute_document_count")

    # SAMI PRD PLM-02 (2026-06-26) — ECO impact analysis.
    # When the ECO applies, _scan_and_flag_affected_mos populates this
    # m2m with every live (confirmed/in_progress) MO whose bom_id
    # matches the versioned BoM. Each MO also gets a chatter post so
    # the floor lead knows their in-flight job is referencing an
    # archived BoM.
    affected_mo_ids = fields.Many2many(
        "mrp.production",
        "southbrook_eco_affected_mo_rel",
        "eco_id",
        "mo_id",
        string="Affected Live MOs",
        readonly=True,
        copy=False,
        help="Live MOs that referenced the BoM at the moment this "
             "ECO was applied. They keep working off the archived BoM "
             "version (no in-flight re-explosion); future MOs use the "
             "new version. This field is the audit trail of who got "
             "the heads-up.",
    )
    affected_mo_count = fields.Integer(
        string="Affected MOs",
        compute="_compute_affected_mo_count",
        store=True,
    )

    @api.depends("affected_mo_ids")
    def _compute_affected_mo_count(self):
        for eco in self:
            eco.affected_mo_count = len(eco.affected_mo_ids)

    # ---- Supersession chain. ----
    # An applied ECO is immutable audit history (see write()'s terminal-
    # write guard below) — it can never be reopened or edited to reflect a
    # later change of mind. The sanctioned way to walk an applied change
    # forward is action_create_superseding, which raises a fresh draft ECO
    # and links the two records both ways so the audit trail reads as a
    # chain rather than a dead end.
    supersedes_id = fields.Many2one(
        "southbrook.eco",
        string="Supersedes",
        readonly=True,
        copy=False,
        help="The applied ECO this one was raised to supersede.",
    )
    superseded_by_id = fields.Many2one(
        "southbrook.eco",
        string="Superseded By",
        readonly=True,
        copy=False,
    )

    # ---- Approval bookkeeping. ----
    approver_id = fields.Many2one(
        "res.users", readonly=True, copy=False, tracking=True
    )
    approval_date = fields.Datetime(readonly=True, copy=False)
    applied_date = fields.Datetime(readonly=True, copy=False)
    # Four-eyes break-glass (M4x hardening, 2026-08-01). action_approve and
    # action_apply both refuse to let an ECO's own Responsible (user_id) —
    # or, for approve, its creator — act on their own ECO; that defeats the
    # point of a second set of eyes. emergency_bypass is the explicit,
    # logged escape hatch for the genuinely urgent case where no other PLM
    # Approver is reachable. Every use posts a prominent chatter line
    # (including bypass_reason) so the audit trail shows exactly who
    # invoked it and why — it is a documented exception, not a silent one.
    emergency_bypass = fields.Boolean(
        string="Emergency Bypass",
        tracking=True,
        copy=False,
        help="Four-eyes break-glass: allows approve/apply by the ECO's own "
        "Responsible. Requires a reason; every use is logged to chatter.",
    )
    bypass_reason = fields.Char(tracking=True, copy=False)

    @api.constrains("emergency_bypass", "bypass_reason")
    def _check_bypass_reason(self):
        for eco in self:
            if eco.emergency_bypass and not eco.bypass_reason:
                raise ValidationError(
                    _(
                        "ECO %s: Emergency Bypass requires a reason — it's "
                        "a logged four-eyes exception, and the log needs "
                        "to say why no independent reviewer was available."
                    )
                    % eco.name
                )

    # ------------------------------------------------------------------
    # Target-required validation (mirrors the form view's per-target_kind
    # required attrs at the model level, so an RPC/import caller that
    # bypasses the view can't create an unappliable ECO).
    # ------------------------------------------------------------------
    @api.constrains("eco_type_id", "bom_id", "cut_spec_id", "git_ref")
    def _check_target_required(self):
        for eco in self:
            kind = eco.target_kind
            if kind == "bom" and not eco.bom_id:
                raise ValidationError(
                    _(
                        "ECO %s: target kind is 'BoM Version' — a Target "
                        "BoM is required."
                    )
                    % eco.name
                )
            if kind == "cut_spec" and not eco.cut_spec_id:
                raise ValidationError(
                    _(
                        "ECO %s: target kind is 'Cut Spec' — a Candidate "
                        "Cut Spec is required."
                    )
                    % eco.name
                )
            # 'rule' deliberately has NO draft-time git_ref requirement: a
            # construction-rule ECO is raised BEFORE the code change exists
            # (southbrook_premium_orchestration auto-proposes rule ECOs
            # from repeated shop-floor overrides, with no PR yet), and the
            # git_ref naturally arrives when the PR is opened. _apply_rule
            # enforces it at apply time, which is when it actually matters.
            # 'document' likewise has no draft-time requirement — a
            # document-kind ECO's attachment is enforced at apply time by
            # _apply_document instead (an engineer may still be gathering
            # documents while the ECO sits in review).

    # ------------------------------------------------------------------
    # Defaults / group_expand
    # ------------------------------------------------------------------
    @api.model
    def _default_stage(self):
        return self.env["southbrook.eco.stage"].search([], limit=1)

    @api.model
    def _read_group_stage_ids(self, stages, domain):
        return self.env["southbrook.eco.stage"].search([])

    def _compute_document_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    # ------------------------------------------------------------------
    # Naming
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southbrook.eco"
                ) or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Approval gating + terminal-stage write protection
    # ------------------------------------------------------------------
    # Fields an Applied/Rejected ECO may still take a write on. Kept
    # deliberately narrow: affected_mo_ids is populated by the (non-fatal,
    # best-effort) impact scan that runs after the terminal write commits
    # and by the manual re-scan button; color is pure Kanban UI grouping
    # with no audit value; message_main_attachment_id is chatter plumbing
    # Odoo itself writes when a message's main attachment changes. Every
    # other field on a terminal ECO is change-control history.
    _TERMINAL_WRITE_ALLOWLIST = {
        "affected_mo_ids",
        "color",
        "message_main_attachment_id",
    }

    def write(self, vals):
        # Immutability of terminal ECOs (Applied / Rejected). An applied or
        # rejected ECO IS the change-control audit trail — editing it after
        # the fact would let someone quietly rewrite what was actually
        # approved/applied/rejected. The sanctioned way to change course is
        # a NEW ECO (action_create_superseding for an applied one; Reset to
        # Draft for a rejected one). Lifecycle actions authorise their own
        # writes via the plm_lifecycle context flag (same flag the forge-
        # guard below already relies on), so this does not block
        # action_reset_draft / action_create_superseding themselves — only
        # a bare RPC/UI write against history.
        if not self.env.context.get("plm_lifecycle") and (
            set(vals) - self._TERMINAL_WRITE_ALLOWLIST
        ):
            if any(eco.state in ("applied", "rejected") for eco in self):
                raise UserError(
                    _(
                        "Applied / Rejected ECOs are immutable — raise a "
                        "superseding ECO instead of editing history."
                    )
                )
        new_stage = None
        if "stage_id" in vals:
            new_stage = self.env["southbrook.eco.stage"].browse(vals["stage_id"])
        # Terminal states/stages (Applied / Rejected) are reachable ONLY through
        # the gated action methods (action_apply / action_reject), which run the
        # change handlers, enforce PLM Approver rights, and set plm_lifecycle=True
        # on their write. A direct write of state=applied|rejected — or of
        # stage_id to a final stage — is a workflow-forge attempt and is refused.
        #
        # The prior guard only fired on the stage_id path and only when `state`
        # didn't already match, so `write({"stage_id": applied, "state":
        # "applied"})` slipped through: a non-approver (group_southbrook_plm_user
        # has perm_write) could forge an 'applied' ECO with NO handler run
        # (no BoM copy / cut-spec activation) and a blank approver_id. Gate on
        # the action-set context flag instead, covering both the state and the
        # stage_id paths. (Bug found by demo walkthrough 2026-06-01 part 9;
        # forge path found by the 2026-07-11 security audit.)
        hits_terminal = (
            vals.get("state") in ("applied", "rejected")
            or (new_stage is not None and new_stage.is_final)
        )
        if hits_terminal and not self.env.context.get("plm_lifecycle"):
            raise UserError(_(
                "An ECO reaches a terminal state (Applied / Rejected) only via "
                "the Apply or Reject actions — which run the ECO's change "
                "handlers and enforce PLM Approver rights — not by a direct "
                "edit of state or stage."))
        # A non-terminal advance OUT of an approval-required stage still needs
        # PLM Approver rights.
        if new_stage is not None:
            for eco in self:
                if (
                    eco.stage_id
                    and eco.stage_id != new_stage
                    and eco.stage_id.approval_required
                    and not self.env.user.has_group(
                        "southbrook_plm.group_southbrook_plm_approver"
                    )
                ):
                    raise UserError(
                        _(
                            "Advancing ECO %(eco)s out of stage '%(stage)s' "
                            "requires PLM Approver rights."
                        )
                        % {"eco": eco.name, "stage": eco.stage_id.name}
                    )
        return super().write(vals)

    # ------------------------------------------------------------------
    # Delete guard
    # ------------------------------------------------------------------
    @api.ondelete(at_uninstall=False)
    def _unlink_except_non_open(self):
        # Applied / Rejected ECOs are the change-control audit trail — the
        # entire point of this module. Deleting one would erase evidence of
        # what was approved, when, by whom, and what it did (BoM version /
        # cut-spec activation / etc.). A draft (state='open') ECO carries no
        # such history yet — deleting a mis-filed or duplicate draft is
        # harmless and stays allowed.
        for eco in self:
            if eco.state != "open":
                raise UserError(
                    _(
                        "ECO %s is Applied or Rejected — Applied / Rejected "
                        "ECOs are the change-control audit trail and cannot "
                        "be deleted."
                    )
                    % eco.name
                )

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_advance(self):
        """Move to the next stage in sequence (respecting approval gating).

        Walks ONLY non-terminal stages — an ECO at the Applied or
        Rejected stage cannot be "advanced" further. The walk also
        excludes is_final stages from the ordering so a sane workflow
        moves Draft → Under Review → Approved and stops, without
        accidentally landing in Rejected (which has the next-higher
        sequence after Applied). Bug found by demo walkthrough
        2026-06-01: advancing from Applied silently moved the ECO
        to Rejected.
        """
        for eco in self:
            if eco.stage_id and eco.stage_id.is_final:
                raise UserError(
                    _("ECO %s is in a terminal stage and cannot be advanced.")
                    % eco.name
                )
            # Only the non-terminal stages participate in advance order;
            # Applied + Rejected are reached via action_apply / action_reject.
            stages = self.env["southbrook.eco.stage"].search(
                [("is_final", "=", False)]
            )
            ordered = stages.sorted(lambda s: (s.sequence, s.id))
            idx = ordered.ids.index(eco.stage_id.id) if eco.stage_id else -1
            nxt = ordered[idx + 1] if 0 <= idx + 1 < len(ordered) else False
            if not nxt:
                raise UserError(
                    _("ECO %s is already at the last non-terminal stage; "
                      "use Approve/Apply or Reject to close it.") % eco.name
                )
            eco.stage_id = nxt
        return True

    def action_approve(self):
        """Stamp approver, four-eyes check, move to the Approved stage.

        Approval is now a distinct step with its own record (approver_id +
        approval_date + landing in a stage flagged is_approved_stage) that
        action_apply checks for — see the M4x hardening rationale on
        action_apply below. Each ECO is moved individually to the
        configured Approved stage rather than blanket-advancing the whole
        recordset one step (the old self.action_advance() call), because
        "the Approved stage" is not necessarily "the next stage in
        sequence" for every pipeline configuration.
        """
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may approve an ECO."))
        approved_stage = self.env["southbrook.eco.stage"].search(
            [("is_approved_stage", "=", True)], limit=1
        )
        for eco in self:
            if eco.state != "open":
                raise UserError(
                    _("ECO %s is not Open — only an open ECO can be approved.")
                    % eco.name
                )
            if eco.approver_id and eco.stage_id.is_approved_stage:
                raise UserError(_("ECO %s is already approved.") % eco.name)
            # Four-eyes: the ECO's own Responsible or its creator approving
            # their own change is self-approval — the approval gate exists
            # precisely so a second person reviews it. emergency_bypass is
            # the explicit, reason-logged break-glass for when genuinely no
            # other approver is reachable.
            is_self_review = self.env.user.id in (
                eco.user_id.id,
                eco.create_uid.id,
            )
            if is_self_review and not eco.emergency_bypass:
                raise UserError(
                    _(
                        "ECO %(eco)s: four-eyes rule — its own Responsible "
                        "or creator may not approve it. Have a different "
                        "PLM Approver review it, or — if genuinely urgent "
                        "and no other approver is reachable — set the "
                        "'Emergency Bypass' field (with a reason) and "
                        "approve again."
                    )
                    % {"eco": eco.name}
                )
            if is_self_review and eco.emergency_bypass:
                eco.message_post(
                    body=_(
                        "EMERGENCY BYPASS — approved by its own "
                        "Responsible/creator (%(user)s). Reason: "
                        "%(reason)s"
                    )
                    % {
                        "user": self.env.user.display_name,
                        "reason": eco.bypass_reason or _("(no reason given)"),
                    }
                )
            eco.write(
                {
                    "approver_id": self.env.user.id,
                    "approval_date": fields.Datetime.now(),
                }
            )
            eco.message_post(body=_("Approved by %s.") % self.env.user.display_name)
            if approved_stage:
                eco.write({"stage_id": approved_stage.id})
            else:
                # Backward-compat fallback: no stage in this pipeline
                # carries is_approved_stage (e.g. an install predating
                # this flag) — fall back to the old single-step advance
                # behaviour so approval still moves the ECO forward.
                eco.action_advance()
        return True

    def action_reject(self):
        # Rejecting an ECO is an approver decision (the PLM Approver group's
        # remit is "approve, apply, and reject"). Gate it so a plain PLM User
        # can't reject other people's ECOs. (Security audit 2026-07-11.)
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may reject an ECO."))
        # Use is_rejected_stage so we land in the Rejected stage
        # specifically — NOT the first is_final stage in sequence
        # order (which would be Applied, because seqs are
        # Applied=40, Rejected=50). Bug found by demo walkthrough
        # 2026-06-01: rejected ECOs were landing in Applied stage.
        # Backward-compat fallback: if no stage has is_rejected_stage
        # set (e.g. addons predating this flag), pick the
        # highest-sequence is_final stage so we still land in the
        # right ballpark.
        rejected_stage = self.env["southbrook.eco.stage"].search(
            [("is_rejected_stage", "=", True)], limit=1
        ) or self.env["southbrook.eco.stage"].search(
            [("is_final", "=", True)], order="sequence desc", limit=1
        )
        for eco in self:
            # Bug found by demo walkthrough 2026-06-01 part 10:
            # rejecting an already-applied ECO silently flipped the
            # state to 'rejected' even though the apply handlers had
            # already mutated the DB (BoM versioned, cut spec
            # activated). The audit trail lied about the outcome
            # while the real-world effect stayed in place.
            #
            # An applied change is committed. To undo it, raise a
            # NEW ECO that explicitly reverts (e.g. a new BoM-kind
            # ECO that rolls forward to the original spec). Don't
            # let action_reject masquerade as an undo.
            if eco.state == "applied":
                raise UserError(
                    _(
                        "ECO %s is already applied — its change is "
                        "committed (BoM versioned / cut spec activated / "
                        "etc.). Reject cannot undo the effect; raise a "
                        "new ECO to revert."
                    )
                    % eco.name
                )
            if eco.state == "rejected":
                # Idempotent no-op — re-rejecting a rejected ECO is
                # harmless but worth a chatter line so the action
                # is at least recorded.
                eco.message_post(
                    body=_("Re-reject no-op by %s (already rejected).")
                    % self.env.user.display_name
                )
                continue
            # Co-write state + stage_id in a SINGLE write call so the
            # terminal-stage-arrival guard (in write() above) sees
            # the matching state in vals and lets the write through.
            # Splitting these into two writes would trigger the
            # bypass-block — regression caught by 2026-06-01 part 9
            # follow-up.
            write_vals = {"state": "rejected"}
            if rejected_stage:
                write_vals["stage_id"] = rejected_stage.id
            eco = eco.with_context(plm_lifecycle=True)
            eco.write(write_vals)
            eco.message_post(body=_("Rejected by %s.") % self.env.user.display_name)
        return True

    def action_reset_draft(self):
        """Reset a REJECTED ECO back to the first stage as open.

        Clears the lifecycle-end fields so the audit trail doesn't
        carry stale 'this was approved by <Y>' markers from the prior
        pass. Bug found by demo walkthrough 2026-06-01: reset_draft
        left applied_date populated even though the ECO was no longer
        applied.

        Re-running action_approve / action_apply on the reset ECO
        will re-stamp these fields cleanly.

        Applied ECOs are OUT OF SCOPE for this action (M4x hardening,
        2026-08-01): they are immutable change-control history (see
        write()'s terminal-write guard) — reopening one would let the
        audit trail be quietly rewritten after the fact, exactly what
        that guard exists to prevent. Use action_create_superseding to
        raise a new ECO instead.
        """
        # Reset clears approver_id/approval_date — i.e. it can ERASE the
        # change-control provenance of a rejected ECO. Gate it on the
        # approver group so a plain PLM User can't wipe the audit trail.
        # The view button already carries groups=, but that's UI-only;
        # RPC ignores it. (Security audit 2026-07-11 — H2.)
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may reset an ECO to draft."))
        # Filter to non-terminal stages so we don't accidentally
        # land back in Applied/Rejected if the seeded Draft stage
        # has been renamed/reordered.
        first_stage = self.env["southbrook.eco.stage"].search(
            [("is_final", "=", False)], limit=1
        )
        for eco in self:
            if eco.state == "applied":
                raise UserError(
                    _(
                        "ECO %s is applied — applied ECOs are immutable "
                        "change-control history and cannot be reset to "
                        "draft. Use 'Create Superseding ECO' to raise a "
                        "new ECO instead."
                    )
                    % eco.name
                )
            # plm_lifecycle authorises this write past both the terminal-
            # write immutability guard (a rejected ECO IS terminal) and
            # the terminal-forge guard, since we've already run the
            # approver-group check that stands in for those guards' intent
            # here.
            eco.with_context(plm_lifecycle=True).write({
                "state": "open",
                "stage_id": first_stage.id,
                "applied_date": False,
                "approver_id": False,
                "approval_date": False,
            })
            eco.message_post(
                body=_("Reset to draft by %s.") % self.env.user.display_name
            )
        return True

    def action_create_superseding(self):
        """Raise a new draft ECO that supersedes this applied one.

        Applied ECOs are immutable (write() blocks edits, action_reset_draft
        refuses applied records) — the sanctioned way to walk an applied
        change forward is a NEW ECO, not rewriting the old one. No approver
        gate here: raising a draft commits nothing by itself (the successor
        still has to pass its own Approve + Apply), so any PLM User may
        start it.

        For a bom-kind ECO the successor targets the CURRENT head —
        new_bom_id (the version THIS ECO produced), falling back to bom_id
        if for some reason new_bom_id wasn't set — not the archived
        original bom_id. Re-targeting the archived version would be a
        historical curiosity, not a useful default for "the next change to
        what's actually live now". cut_spec_id/git_ref are carried forward
        the same way, for their OWN matching target_kind, rather than
        unconditionally forced to False: forcing them False unconditionally
        would leave a cut_spec/rule-kind successor with target_kind
        unchanged but its required target field empty, which immediately
        trips _check_target_required on creation (self.copy()'s vals
        include eco_type_id, which isn't copy=False, so the constraint
        fires at create time — not just at Apply). This keeps the method
        usable for all four target kinds.
        """
        self.ensure_one()
        if self.state != "applied":
            raise UserError(
                _("ECO %s is not applied — only an applied ECO can be superseded.")
                % self.name
            )
        new = self.copy(
            default={
                "supersedes_id": self.id,
                "title": self.title,
                # The raiser becomes the successor's Responsible — both
                # better semantics (they're driving the next change, the
                # original Responsible may be long gone) and required by
                # the PLM User record rule, whose create scope is
                # user_id == the acting user.
                "user_id": self.env.user.id,
                "bom_id": (
                    (self.new_bom_id.id or self.bom_id.id)
                    if self.target_kind == "bom"
                    else False
                ),
                "cut_spec_id": (
                    self.cut_spec_id.id if self.target_kind == "cut_spec" else False
                ),
                "git_ref": self.git_ref if self.target_kind == "rule" else False,
            }
        )
        # sudo() on the back-link: the PLM User record rule scopes a plain
        # user's writes to their OWN OPEN ECOs, and this write targets an
        # applied one (possibly someone else's). The method's own state
        # gate + the plm_lifecycle flag make this the sanctioned lifecycle
        # link, and any PLM User may raise a successor — so elevate for
        # exactly this one write.
        self.sudo().with_context(plm_lifecycle=True).write(
            {"superseded_by_id": new.id}
        )
        self.message_post(body=_("Superseded by %s.") % new.display_name)
        new.message_post(body=_("Raised to supersede %s.") % self.display_name)
        return {
            "type": "ir.actions.act_window",
            "name": _("Superseding ECO"),
            "res_model": "southbrook.eco",
            "res_id": new.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_apply(self):
        """Commit the ECO's change, branching on target_kind.

        Approval is now a hard prerequisite, not a de-facto fallback
        (M4x hardening, 2026-08-01): applying used to silently stamp
        whoever clicked Apply as the approver if action_approve had
        never run, which meant Apply alone was a complete bypass of the
        approval step — a PLM Approver could go straight Draft ->
        Applied. That's now refused: approver_id + approval_date must
        already be set (i.e. action_approve must have run), and — when
        the pipeline defines an Approved stage — the ECO must actually
        be sitting in it. Both checks fall back to permissive behaviour
        only when the pipeline predates these fields entirely, so an
        older install isn't hard-broken by upgrading this module.
        """
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may apply an ECO."))
        # search_count, not search+bool, so an empty pipeline (no stage
        # carries the flag at all) is unambiguous from "the flagged stage
        # just doesn't happen to be this ECO's stage" — see the fallback
        # comment below, mirroring the is_rejected_stage fallback pattern
        # already used elsewhere in this file.
        approved_stage_exists = bool(
            self.env["southbrook.eco.stage"].search_count(
                [("is_approved_stage", "=", True)]
            )
        )
        for eco in self:
            if eco.state == "applied":
                raise UserError(_("ECO %s is already applied.") % eco.name)
            # Bug found by demo walkthrough 2026-06-01 part 11:
            # applying a rejected ECO silently revived it (state=applied)
            # with no chatter audit. A rejection is a deliberate decision;
            # reviving requires explicit reset_draft so the audit trail
            # carries the 'reset to draft by X' chatter post.
            if eco.state == "rejected":
                raise UserError(
                    _(
                        "ECO %s was rejected. Use 'Reset to Draft' first "
                        "to revive it (which records the reset in the "
                        "audit trail), then re-approve and apply."
                    )
                    % eco.name
                )
            if not (eco.approver_id and eco.approval_date):
                raise UserError(
                    _(
                        "ECO %s has not been approved yet — use the "
                        "Approve action first. Apply no longer stamps a "
                        "de-facto approver."
                    )
                    % eco.name
                )
            # Backward-compat fallback: only enforce the Approved-stage
            # location when some stage in this pipeline actually carries
            # is_approved_stage. An install that hasn't configured that
            # flag on any stage keeps the pre-hardening behaviour (approval
            # bookkeeping alone gates Apply).
            if approved_stage_exists and not eco.stage_id.is_approved_stage:
                raise UserError(
                    _(
                        "ECO %s must pass approval and sit in the "
                        "Approved stage before it can be applied."
                    )
                    % eco.name
                )
            # Four-eyes on the applier too: the ECO's own Responsible
            # applying their own (already-approved-by-someone-else) change
            # is fine in principle, but we hold Apply to the same
            # break-glass discipline as Approve for symmetry — an
            # Approver who is ALSO the Responsible still needs the
            # explicit, logged bypass to push the button themselves.
            if self.env.user.id == eco.user_id.id and not eco.emergency_bypass:
                raise UserError(
                    _(
                        "ECO %(eco)s: four-eyes rule — its own "
                        "Responsible (%(resp)s) may not apply it. Have a "
                        "different PLM Approver apply it, or — if "
                        "genuinely urgent — set the 'Emergency Bypass' "
                        "field (with a reason) and apply again."
                    )
                    % {"eco": eco.name, "resp": eco.user_id.display_name}
                )
            if self.env.user.id == eco.user_id.id and eco.emergency_bypass:
                eco.message_post(
                    body=_(
                        "EMERGENCY BYPASS — applied by its own "
                        "Responsible (%(user)s). Reason: %(reason)s"
                    )
                    % {
                        "user": self.env.user.display_name,
                        "reason": eco.bypass_reason or _("(no reason given)"),
                    }
                )
            handler = getattr(eco, "_apply_%s" % (eco.target_kind or ""), None)
            if handler is None:
                raise UserError(
                    _("No apply handler for ECO type kind '%s'.")
                    % eco.target_kind
                )
            handler()
            # SAMI PRD PLM-02 — flag live MOs that were referencing the
            # versioned target. Non-fatal: failure to scan MUST NEVER
            # block the ECO apply itself.
            try:
                eco._scan_and_flag_affected_mos()
            except Exception:  # noqa: BLE001
                pass
            applied_stage = self.env["southbrook.eco.stage"].search(
                [("is_applied_stage", "=", True)], limit=1
            )
            now = fields.Datetime.now()
            write_vals = {
                "state": "applied",
                "applied_date": now,
                "stage_id": applied_stage.id if applied_stage else eco.stage_id.id,
            }
            # approver_id/approval_date are guaranteed set already (checked
            # above) — no de-facto stamping here. plm_lifecycle authorises
            # the terminal-state write past the write() forge-guard AND the
            # new terminal-write immutability guard — we are inside the
            # approver-gated action and have already run the change
            # handler.
            eco.with_context(plm_lifecycle=True).write(write_vals)
        return True

    # ------------------------------------------------------------------
    # Per-kind apply handlers
    # ------------------------------------------------------------------
    def _apply_bom(self):
        self.ensure_one()
        if not self.bom_id:
            raise ValidationError(
                _("ECO %s: a Target BoM is required to version a BoM.") % self.name
            )
        # The BoM copy/archive is a privileged action, but it is already
        # authorised by the ECO approval gate (action_apply requires the PLM
        # Approver group). A PLM Approver is not necessarily a Manufacturing
        # Administrator, so run the mrp.bom mutation with sudo() — the approval
        # IS the authorization. (NF: caught by test_bom_eco_versions_and_archives
        # on the live v19 install — AccessError creating mrp.bom otherwise.)
        old = self.bom_id.sudo()
        # Serialise concurrent applies against the same BoM. Without this,
        # two ECOs applying to the same bom_id both read version N, both create
        # an active version N+1, and both archive the original — leaving TWO
        # active BoMs at the same version and breaking the "one active BoM per
        # template" invariant the confirm-time snapshot relies on. FOR UPDATE
        # blocks the second apply until the first commits; we then re-read and
        # refuse if the BoM was archived out from under us. (Security audit
        # 2026-07-11 — H3/M2.)
        self.env.cr.execute(
            "SELECT id FROM mrp_bom WHERE id = %s FOR UPDATE", (old.id,))
        old.invalidate_recordset(["active", "southbrook_version"])
        if not old.active:
            raise UserError(_(
                "BoM %s is already archived — it was versioned by a concurrent "
                "ECO. Re-target this ECO at the current active version."
            ) % old.display_name)
        # Compute the new version from the HIGH-WATER MARK across every
        # mrp.bom (active or archived) sharing this template — not from
        # old.southbrook_version + 1. A template can fan out into more than
        # one archived history line (e.g. a BoM manually re-created, or a
        # branch that got superseded and re-diverged); old.southbrook_version
        # only knows about the one lineage old itself descends from. Taking
        # the max across ALL rows for the template means a historic fan-out
        # can never mint a version number that collides with a sibling
        # lineage's already-used number — the mrp.bom constrains added
        # alongside this hardening pass (see mrp_bom.py) would reject that
        # collision anyway, but computing it right here avoids ever hitting
        # that constraint in the first place. active_test=False so archived
        # rows count too. (M4x hardening, 2026-08-01.)
        all_versions = (
            self.env["mrp.bom"]
            .sudo()
            .with_context(active_test=False)
            .search([("product_tmpl_id", "=", old.product_tmpl_id.id)])
            .mapped("southbrook_version")
        )
        next_version = max(all_versions or [old.southbrook_version]) + 1
        new = old.copy(
            {
                "southbrook_version": next_version,
                "active": True,
            }
        )
        old.write({"active": False})
        self.new_bom_id = new.id
        self.message_post(
            body=_(
                "BoM versioned: %(old)s (v%(ov)s, archived) -> %(new)s (v%(nv)s)."
            )
            % {
                "old": old.display_name,
                "ov": old.southbrook_version,
                "new": new.display_name,
                "nv": new.southbrook_version,
            }
        )

    def _apply_cut_spec(self):
        self.ensure_one()
        if not self.cut_spec_id:
            raise ValidationError(
                _("ECO %s: a Candidate Cut Spec is required.") % self.name
            )
        self.cut_spec_id.action_activate()
        self.message_post(
            body=_("Cut spec %s activated.") % self.cut_spec_id.display_name
        )

    def _apply_rule(self):
        self.ensure_one()
        if not self.git_ref:
            raise ValidationError(
                _(
                    "ECO %s: a Git Reference (commit SHA or PR) is required for "
                    "a construction-rule change — git remains the system of "
                    "record for code-resident rules."
                )
                % self.name
            )
        self.message_post(
            body=_("Construction-rule change recorded against git ref %s.")
            % self.git_ref
        )

    def _apply_document(self):
        self.ensure_one()
        # Symmetry with the other three apply handlers: bom requires
        # bom_id, cut_spec requires cut_spec_id, rule requires git_ref.
        # A document ECO must carry at least one attached document or
        # it's a meaningless apply. Bug found by demo walkthrough
        # 2026-06-01 part 16: document-kind ECOs were applying with
        # zero attachments and posting a 'recorded 0 attachment(s)'
        # chatter line.
        if not self.document_ids:
            raise ValidationError(
                _(
                    "ECO %s: at least one Engineering Document must be "
                    "attached before this ECO can be applied. Use the "
                    "Documents tab to attach the relevant PDF(s)."
                )
                % self.name
            )
        self.message_post(
            body=_("Engineering-document update recorded (%d attachment(s)).")
            % len(self.document_ids)
        )

    # ------------------------------------------------------------------
    # Impact analysis (SAMI PRD PLM-02, 2026-06-26)
    # ------------------------------------------------------------------
    def _scan_and_flag_affected_mos(self):
        """Find live MOs that referenced the versioned target at the
        moment this ECO was applied, populate affected_mo_ids.

        Live MO states scanned: 'confirmed', 'progress', 'to_close'.
        Done / cancel MOs are skipped — they're finished.

        BoM-kind ECOs:
            MO bom_id == self.bom_id (the now-archived BoM)
        Cut-spec / Rule / Document-kind ECOs:
            Skipped — none directly bind to mrp.production.

        Chatter notification is handled by southbrook_mrp_pm's M20
        override of action_apply (which posts a richer Markup-wrapped
        notice with proper HTML rendering). This method ONLY populates
        the m2m so the ECO form's smart button can drill into the list.

        Idempotent: re-running writes the same m2m without duplication.
        """
        self.ensure_one()
        if self.target_kind != "bom" or not self.bom_id:
            return self.env["mrp.production"]
        affected = self.env["mrp.production"].search([
            ("bom_id", "=", self.bom_id.id),
            ("state", "in", ["confirmed", "progress", "to_close"]),
        ])
        if affected:
            self.affected_mo_ids = [(4, mo.id) for mo in affected]
        return affected

    def action_rescan_affected_mos(self):
        """Manual re-scan button — useful if an MO was created after
        the ECO was applied but the BoM reference was set retroactively
        to the archived version (unusual but possible)."""
        for eco in self:
            eco._scan_and_flag_affected_mos()
        return True

    def action_open_affected_mos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Affected Manufacturing Orders"),
            "res_model": "mrp.production",
            "view_mode": "list,form",
            "domain": [("id", "in", self.affected_mo_ids.ids)],
        }

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def action_open_documents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Engineering Documents"),
            "res_model": "ir.attachment",
            "view_mode": "kanban,list,form",
            "domain": [("id", "in", self.document_ids.ids)],
        }
