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
    # Approval gating on stage change
    # ------------------------------------------------------------------
    def write(self, vals):
        if "stage_id" in vals:
            new_stage = self.env["southbrook.eco.stage"].browse(vals["stage_id"])
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
        """Move to the next stage in sequence (respecting approval gating)."""
        for eco in self:
            stages = self.env["southbrook.eco.stage"].search([])
            ordered = stages.sorted(lambda s: (s.sequence, s.id))
            idx = ordered.ids.index(eco.stage_id.id) if eco.stage_id else -1
            nxt = ordered[idx + 1] if 0 <= idx + 1 < len(ordered) else False
            if not nxt:
                raise UserError(_("ECO %s is already at the last stage.") % eco.name)
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
        rejected_stage = self.env["southbrook.eco.stage"].search(
            [("is_final", "=", True)], limit=1
        )
        for eco in self:
            eco.state = "rejected"
            if rejected_stage:
                eco.stage_id = rejected_stage
            eco.message_post(body=_("Rejected by %s.") % self.env.user.display_name)
        return True

    def action_reset_draft(self):
        first_stage = self.env["southbrook.eco.stage"].search([], limit=1)
        for eco in self:
            eco.write({"state": "open", "stage_id": first_stage.id})
        return True

    def action_apply(self):
        """Commit the ECO's change, branching on target_kind."""
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may apply an ECO."))
        for eco in self:
            if eco.state == "applied":
                raise UserError(_("ECO %s is already applied.") % eco.name)
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
            eco.write(
                {
                    "state": "applied",
                    "applied_date": fields.Datetime.now(),
                    "stage_id": applied_stage.id if applied_stage else eco.stage_id.id,
                }
            )
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
