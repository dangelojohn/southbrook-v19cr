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

    # ---- Approval bookkeeping. ----
    approver_id = fields.Many2one(
        "res.users", readonly=True, copy=False, tracking=True
    )
    approval_date = fields.Datetime(readonly=True, copy=False)
    applied_date = fields.Datetime(readonly=True, copy=False)

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
    def write(self, vals):
        if "stage_id" in vals:
            new_stage = self.env["southbrook.eco.stage"].browse(vals["stage_id"])
            # Terminal-stage guard: Applied / Rejected are reachable
            # only via action_apply / action_reject (both of which
            # always co-write `state` to the matching terminal value).
            # A direct write of stage_id to a terminal stage WITHOUT
            # a matching state move is a workflow-bypass attempt —
            # it would land the ECO in Applied without running any
            # apply handler (no BoM copy, no cut-spec activation, no
            # chatter audit), producing a paradoxical
            # 'stage=Applied, state=open' record. Block it.
            # Bug found by demo walkthrough 2026-06-01 part 9.
            if new_stage.is_final:
                expected_state = (
                    "rejected" if new_stage.is_rejected_stage else "applied"
                )
                if vals.get("state") != expected_state:
                    raise UserError(
                        _(
                            "Cannot write stage '%(stage)s' directly on ECO "
                            "%(eco)s — terminal stages are reachable only "
                            "via the Apply or Reject buttons, which run the "
                            "ECO's change handlers + write the matching "
                            "lifecycle state."
                        )
                        % {
                            "stage": new_stage.name,
                            "eco": ", ".join(self.mapped("name")) or "(new)",
                        }
                    )
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
        """Stamp approver and advance one stage. Approver group enforced."""
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may approve an ECO."))
        for eco in self:
            eco.write(
                {
                    "approver_id": self.env.user.id,
                    "approval_date": fields.Datetime.now(),
                }
            )
            eco.message_post(body=_("Approved by %s.") % self.env.user.display_name)
        self.action_advance()
        return True

    def action_reject(self):
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
            eco.write(write_vals)
            eco.message_post(body=_("Rejected by %s.") % self.env.user.display_name)
        return True

    def action_reset_draft(self):
        """Reset an applied/rejected ECO back to the first stage as open.

        Clears the lifecycle-end fields so the audit trail doesn't
        carry stale 'this was applied at <X>' / 'approved by <Y>'
        markers from the prior pass. Bug found by demo walkthrough
        2026-06-01: reset_draft left applied_date populated even
        though the ECO was no longer applied.

        Re-running action_approve / action_apply on the reset ECO
        will re-stamp these fields cleanly.
        """
        # Filter to non-terminal stages so we don't accidentally
        # land back in Applied/Rejected if the seeded Draft stage
        # has been renamed/reordered.
        first_stage = self.env["southbrook.eco.stage"].search(
            [("is_final", "=", False)], limit=1
        )
        for eco in self:
            eco.write({
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

    def action_apply(self):
        """Commit the ECO's change, branching on target_kind.

        Approver stamping: if the ECO was never run through
        :meth:`action_approve` (which sets ``approver_id`` + ``approval_date``
        explicitly), the user who applies it becomes the de-facto
        approver and gets stamped here. If a prior ``action_approve``
        already set those fields, they are preserved — the audit
        trail correctly records that approval and application were
        separate actions (and possibly different users).
        """
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may apply an ECO."))
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
            handler = getattr(eco, "_apply_%s" % (eco.target_kind or ""), None)
            if handler is None:
                raise UserError(
                    _("No apply handler for ECO type kind '%s'.")
                    % eco.target_kind
                )
            handler()
            applied_stage = self.env["southbrook.eco.stage"].search(
                [("is_applied_stage", "=", True)], limit=1
            )
            now = fields.Datetime.now()
            write_vals = {
                "state": "applied",
                "applied_date": now,
                "stage_id": applied_stage.id if applied_stage else eco.stage_id.id,
            }
            # Only stamp approver_id + approval_date if a prior
            # action_approve hasn't already filled them in.
            if not eco.approver_id:
                write_vals["approver_id"] = self.env.user.id
                write_vals["approval_date"] = now
            eco.write(write_vals)
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
        new = old.copy(
            {
                "southbrook_version": old.southbrook_version + 1,
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
