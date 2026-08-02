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
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .plm_lifecycle import PLM_LIFECYCLE, is_plm_lifecycle_authorised
from .southbrook_cut_spec import CONSTANT_FIELDS

_logger = logging.getLogger(__name__)


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
        # FIX 7 (2026-08-02 blindspot pass): Odoo's Many2one default is
        # ondelete='set null'. Deleting the target BoM out from under an
        # open ECO would silently blank a required field (_check_target_
        # required demands bom_id for a bom-kind ECO) with no trace of
        # what happened — the ECO just quietly becomes invalid the next
        # time someone tries to apply it, no chatter, no error at delete
        # time. 'restrict' surfaces the conflict at the moment of the
        # deletion attempt instead, matching eco_type_id's existing
        # ondelete='restrict' above.
        ondelete="restrict",
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
        # FIX 7 (2026-08-02 blindspot pass): same rationale as bom_id
        # above — the default ondelete='set null' would let a deleted
        # candidate spec silently empty a required field on an open ECO.
        ondelete="restrict",
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
    # Typed effectivity (2026-08-02). Industry practice for change-control
    # systems that actually run a factory floor (researched against Odoo
    # Enterprise mrp_plm, Arena, Oracle Agile/PLM, and PTC Windchill) treats
    # "when does an approved change take effect" as its OWN typed decision,
    # distinct from "is it approved". Before this, an approved Southbrook
    # ECO applied the instant a PLM Approver clicked Apply, or not at all —
    # fine for a code fix, wrong for a change that a make-to-order cabinet
    # shop needs to cut in at a predictable, communicated moment.
    #
    #   immediate — the default, and the ONLY behaviour that existed
    #               before this field. action_apply commits the change
    #               the moment it's clicked, exactly as before.
    #   date      — the change is scheduled for a specific moment (e.g.
    #               "the new box spec starts on jobs confirmed from
    #               Monday"). For a make-to-order shop, ORDER-CONFIRMATION
    #               DATE is the natural cut-in point — not "today" and not
    #               an arbitrary calendar date picked in the abstract —
    #               but this field stores a plain effective_date rather
    #               than hard-wiring that semantics, so a scheduled ECO
    #               can also model a vendor-communicated hardware
    #               availability date, a plant shutdown week, etc. The
    #               hourly cron (_cron_apply_due_ecos, below) applies it
    #               automatically once effective_date arrives.
    #   use_up    — the change cuts in once EXISTING STOCK of a superseded
    #               material is exhausted, not on any calendar date. This
    #               type exists because geometry constants (rabbet depth,
    #               reveal, etc.) are very often driven by a HARDWARE
    #               SUBSTITUTION — e.g. a new hinge cup diameter changes
    #               the boring pattern — and the shop should keep building
    #               to the OLD geometry as long as the OLD hardware is
    #               still on the shelf, then cut over. There is no
    #               reliable calendar date for "the shelf ran out"; it is
    #               a stock-level event a human on the floor confirms. This
    #               module deliberately does NOT watch stock.quant levels
    #               and auto-apply a use_up ECO — use_up_note is
    #               informational only, and a human still clicks Apply
    #               once they've confirmed exhaustion (action_apply's
    #               existing checks apply unchanged; there is no cron path
    #               for use_up).
    effectivity_type = fields.Selection(
        [
            ("immediate", "Immediate — apply on approval"),
            ("date", "Scheduled — apply on a date"),
            ("use_up", "Use-up — apply when existing stock is exhausted"),
        ],
        default="immediate",
        required=True,
        tracking=True,
        help="WHEN an approved ECO takes effect, as distinct from WHETHER "
        "it is approved. Immediate applies the instant Apply is clicked, "
        "exactly like every ECO before this field existed. Scheduled "
        "defers to effective_date (the hourly cron applies it "
        "automatically once due). Use-up defers to a stock-exhaustion "
        "event a human confirms on the floor — geometry constants are "
        "often driven by a hardware substitution, so the change should "
        "cut in once the old hardware is consumed, not on a calendar "
        "date; nothing automated watches stock for this type, a human "
        "still applies it.",
    )
    effective_date = fields.Datetime(
        tracking=True,
        copy=False,
        help="When a Scheduled ECO becomes live. The cron applies it at "
        "or after this moment; until then the change is approved but NOT "
        "in force.",
    )
    use_up_note = fields.Char(
        tracking=True,
        help="Which stock must be exhausted before this change cuts in. "
        "Informational only — nothing in this module watches inventory "
        "levels; a human confirms exhaustion on the floor and then "
        "applies the ECO manually, same as any other apply.",
    )
    # effectivity_state is a STORED compute, which immediately runs into a
    # basic limitation: a stored field only recomputes when one of its
    # @api.depends fields changes on the record — it has no way to notice
    # that wall-clock time has simply passed. A pure "pending vs due"
    # split (has effective_date arrived RIGHT NOW) cannot live in a stored
    # compute without something re-triggering it every time "now" crosses
    # effective_date, which nothing here does. Rather than pretend
    # otherwise with a compute that quietly goes stale, this field never
    # assigns "due" itself — "due" exists in the selection purely so the
    # value is self-documenting (and available to any future non-stored
    # display use), while the ACTUAL pending-vs-due distinction is
    # resolved where it belongs: _cron_apply_due_ecos' search domain,
    # which re-evaluates effective_date <= now() fresh on every run. The
    # compute below treats every approved + scheduled + unapplied ECO as
    # "pending" — which stays TRUE right up until the cron (or a human,
    # via action_apply once effective_date has passed) actually applies
    # it, at which point state flips to applied and this recomputes to
    # in_force. No wall-clock claim is ever stored as fact.
    effectivity_state = fields.Selection(
        [
            ("not_scheduled", "Not Scheduled"),
            ("pending", "Pending — Scheduled, Not Yet Due"),
            ("due", "Due"),
            ("in_force", "In Force"),
        ],
        string="Effectivity State",
        compute="_compute_effectivity_state",
        store=True,
        help="not_scheduled: Immediate or Use-up type, or a Scheduled ECO "
        "not yet approved. pending: approved and Scheduled, effective_date "
        "in the future (or the compute simply can't know — see the field "
        "comment). due: reserved for display use; this stored field never "
        "assigns it — the cron's search domain is the actual source of "
        "truth for 'is it due right now'. in_force: state == applied.",
    )

    @api.depends("state", "effectivity_type", "effective_date", "approver_id")
    def _compute_effectivity_state(self):
        for eco in self:
            if eco.state == "applied":
                eco.effectivity_state = "in_force"
            elif (
                eco.effectivity_type == "date"
                and eco.effective_date
                and eco.approver_id
            ):
                eco.effectivity_state = "pending"
            else:
                eco.effectivity_state = "not_scheduled"

    @api.constrains("effectivity_type", "effective_date", "use_up_note", "state")
    def _check_effectivity(self):
        for eco in self:
            if eco.effectivity_type == "date":
                if not eco.effective_date:
                    raise ValidationError(
                        _(
                            "ECO %s: Effectivity is 'Scheduled' — an "
                            "Effective Date is required."
                        )
                        % eco.name
                    )
                # Future-date requirement is skipped once the ECO is
                # applied: applying is exactly the moment effective_date
                # is EXPECTED to have arrived (the cron only ever applies
                # a due ECO, and a human applying early is refused by
                # action_apply's own check, below), and "state" is
                # deliberately in this constraint's @api.depends because
                # the terminal write that flips state to 'applied' would
                # otherwise re-run this check against a now-past date and
                # fail the very apply it just performed. Applied ECOs are
                # immutable history anyway (see write()'s terminal-write
                # guard) — allowing a past date on them is what keeps that
                # history writable by the lifecycle write path at all.
                if (
                    eco.state != "applied"
                    and eco.effective_date <= fields.Datetime.now()
                ):
                    raise ValidationError(
                        _(
                            "ECO %(eco)s: Effective Date must be in the "
                            "future — %(date)s has already passed (or is "
                            "now). A Scheduled ECO cannot be scheduled "
                            "into the past."
                        )
                        % {"eco": eco.name, "date": eco.effective_date}
                    )
            if eco.effectivity_type == "use_up" and not eco.use_up_note:
                raise ValidationError(
                    _(
                        "ECO %s: Effectivity is 'Use-up' — a Use-up Note "
                        "is required so a human on the floor knows which "
                        "stock must be exhausted before this change cuts "
                        "in."
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
    # Lifecycle sentinel helper (FIX 1, 2026-08-02 blindspot pass)
    # ------------------------------------------------------------------
    def _plm_lifecycle_authorised(self):
        """True only for a write/create authorised by THIS module's own
        lifecycle actions — never by a caller-supplied context value.

        See plm_lifecycle.py's module docstring for the full JSON-RPC-
        forgery rationale: the old guards below tested
        ``self.env.context.get("plm_lifecycle")`` for plain truthiness,
        which a bare RPC call could forge by attaching
        ``{"context": {"plm_lifecycle": true}}`` to ANY write()/create().
        This compares by object IDENTITY against a module-level singleton
        that only importable, in-process Python can reference — no JSON
        payload can fabricate it. Every call site in this file that used
        to read the context key directly, or set it to ``True``, now goes
        through this helper / the ``PLM_LIFECYCLE`` singleton instead.
        """
        return is_plm_lifecycle_authorised(self.env)

    # ------------------------------------------------------------------
    # Naming
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Terminal-state forge guard, create-side. write() already
            # refuses a direct flip to applied/rejected outside the gated
            # actions — but a bare RPC create({'state': 'applied'}) would
            # mint terminal history with no handler run and no approval at
            # all (spotted 2026-08-02: the old demo XML did exactly this).
            # Same escape hatch as write(): the lifecycle sentinel (FIX 1 —
            # identity-checked, not the old truthiness read of the bare
            # context key, which a JSON-RPC caller could forge).
            if vals.get("state") in ("applied", "rejected") and not (
                self._plm_lifecycle_authorised()
            ):
                raise UserError(_(
                    "An ECO cannot be CREATED in a terminal state "
                    "(Applied / Rejected) — terminal states are reached "
                    "only via the Apply or Reject actions."))
            # FIX 5 (2026-08-02 blindspot pass): emergency_bypass /
            # bypass_reason are the four-eyes break-glass — the form view
            # already restricts them to the PLM Approver group, but RPC
            # ignores views entirely, and the ACL gives a plain PLM User
            # perm_write (they need to author their own draft ECOs), which
            # also meant perm_write on THESE two fields. A plain user
            # could create their own ECO with emergency_bypass=True armed
            # from the start and unlock self-approval/self-apply on it —
            # the exact thing the four-eyes rule exists to prevent.
            if (
                ("emergency_bypass" in vals or "bypass_reason" in vals)
                and not self.env.user.has_group(
                    "southbrook_plm.group_southbrook_plm_approver"
                )
            ):
                raise UserError(
                    _(
                        "Emergency Bypass is a PLM Approver-only field — "
                        "only a PLM Approver may set Emergency Bypass or "
                        "Bypass Reason."
                    )
                )
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southbrook.eco"
                ) or _("New")
        records = super().create(vals_list)
        # FIX 5 continued: arming the bypass is logged separately from
        # USING it. action_approve/action_apply already post an
        # "EMERGENCY BYPASS — approved/applied by ..." line at the moment
        # the flag is exercised to self-approve/self-apply, but an ECO can
        # be CREATED with it already armed (and possibly never used, or
        # used much later) — the audit trail should show who armed it and
        # why regardless of whether it's ever exercised.
        for rec, vals in zip(records, vals_list):
            if vals.get("emergency_bypass"):
                rec.message_post(
                    body=_(
                        "Emergency Bypass ARMED at creation by %(user)s. "
                        "Reason: %(reason)s"
                    )
                    % {
                        "user": self.env.user.display_name,
                        "reason": rec.bypass_reason or _("(no reason given)"),
                    }
                )
        return records

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
        # FIX 5 (2026-08-02 blindspot pass): emergency_bypass / bypass_reason
        # are the four-eyes break-glass — the form view restricts them to
        # the PLM Approver group, but RPC ignores views, and the ACL gives
        # a plain PLM User perm_write (needed so they can author their own
        # draft ECOs), which also covers THESE two fields. Without this
        # guard a plain user could write emergency_bypass=True + a reason
        # onto their own draft and unlock self-approval / self-apply —
        # exactly what the four-eyes rule exists to prevent. Checked here,
        # server-side, independent of the view.
        if (
            ("emergency_bypass" in vals or "bypass_reason" in vals)
            and not self.env.user.has_group(
                "southbrook_plm.group_southbrook_plm_approver"
            )
        ):
            raise UserError(
                _(
                    "Emergency Bypass is a PLM Approver-only field — only "
                    "a PLM Approver may set Emergency Bypass or Bypass "
                    "Reason."
                )
            )
        # Capture, BEFORE the write, which records are about to flip
        # emergency_bypass False -> True — arming the bypass is logged
        # separately from USING it (action_approve/action_apply already
        # post an "EMERGENCY BYPASS — approved/applied by ..." line at the
        # moment it's exercised, but an armed-and-never-used bypass, or one
        # armed well before it's used, deserves its own audit line). Must
        # be computed before super().write() runs, or every record in
        # `self` would already read emergency_bypass=True and "newly"
        # armed would be indistinguishable from "already armed".
        newly_armed = self.browse()
        if vals.get("emergency_bypass") is True:
            newly_armed = self.filtered(lambda e: not e.emergency_bypass)
        # Immutability of terminal ECOs (Applied / Rejected). An applied or
        # rejected ECO IS the change-control audit trail — editing it after
        # the fact would let someone quietly rewrite what was actually
        # approved/applied/rejected. The sanctioned way to change course is
        # a NEW ECO (action_create_superseding for an applied one; Reset to
        # Draft for a rejected one). Lifecycle actions authorise their own
        # writes via the lifecycle sentinel (FIX 1 — identity-checked, not
        # the old truthiness read of the bare context key, which a
        # JSON-RPC caller could forge), so this does not block
        # action_reset_draft / action_create_superseding themselves — only
        # a bare RPC/UI write against history.
        if not self._plm_lifecycle_authorised() and (
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
        # change handlers, enforce PLM Approver rights, and pass the lifecycle
        # sentinel on their write. A direct write of state=applied|rejected — or
        # of stage_id to a final stage — is a workflow-forge attempt and is
        # refused.
        #
        # The prior guard only fired on the stage_id path and only when `state`
        # didn't already match, so `write({"stage_id": applied, "state":
        # "applied"})` slipped through: a non-approver (group_southbrook_plm_user
        # has perm_write) could forge an 'applied' ECO with NO handler run
        # (no BoM copy / cut-spec activation) and a blank approver_id. Gate on
        # the sentinel instead, covering both the state and the
        # stage_id paths. (Bug found by demo walkthrough 2026-06-01 part 9;
        # forge path found by the 2026-07-11 security audit.)
        hits_terminal = (
            vals.get("state") in ("applied", "rejected")
            or (new_stage is not None and new_stage.is_final)
        )
        if hits_terminal and not self._plm_lifecycle_authorised():
            raise UserError(_(
                "An ECO reaches a terminal state (Applied / Rejected) only via "
                "the Apply or Reject actions — which run the ECO's change "
                "handlers and enforce PLM Approver rights — not by a direct "
                "edit of state or stage."))
        # FIX 3 (2026-08-02 blindspot pass): gate stage ENTRY into the
        # Approved stage, not just terminal-stage entry/exit (handled
        # above). is_approved_stage is deliberately NOT is_final (an
        # approved ECO still has to pass through Apply), so the
        # hits_terminal guard above never fires for it — a bare
        # write({"stage_id": approved.id}) from a plain PLM User (RPC,
        # dragging the kanban card, clicking the statusbar) sailed straight
        # past every check above and landed the ECO in the Approved stage
        # with approver_id/approval_date left blank. action_apply's own
        # approved-stage check then blocked Apply, but the pipeline itself
        # was already lying: an ECO could sit in "Approved" having never
        # actually been approved. Only action_approve may place an ECO in
        # the Approved stage, and it must do so with BOTH PLM Approver
        # rights AND the lifecycle sentinel — a bare write can forge
        # neither.
        if (
            new_stage is not None
            and new_stage.is_approved_stage
            and not (
                self._plm_lifecycle_authorised()
                and self.env.user.has_group(
                    "southbrook_plm.group_southbrook_plm_approver"
                )
            )
        ):
            raise UserError(
                _(
                    "An ECO enters the Approved stage only via the "
                    "Approve action — which stamps approver_id / "
                    "approval_date and enforces PLM Approver rights and "
                    "the four-eyes rule — not by a direct edit of "
                    "stage_id."
                )
            )
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
        result = super().write(vals)
        # FIX 5 continued: post the "armed" chatter line AFTER the write
        # commits (so bypass_reason — possibly set in this same vals dict
        # — is already on the record when we read it back).
        for eco in newly_armed:
            eco.message_post(
                body=_(
                    "Emergency Bypass ARMED by %(user)s. Reason: %(reason)s"
                )
                % {
                    "user": self.env.user.display_name,
                    "reason": eco.bypass_reason or _("(no reason given)"),
                }
            )
        return result

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

        FIX 2 (2026-08-02 blindspot pass): the pipeline MUST define exactly
        one stage flagged is_approved_stage or this method now refuses to
        run at all — see the matching fail-closed rationale on
        action_apply below. The old behaviour fell back to
        self.action_advance() when no stage carried the flag, which was
        permissive exactly where it should have been strict: an admin who
        forgot to flag a stage (or unset it on every stage) got a
        pipeline that silently kept working via the old advance-by-
        sequence path, never surfacing that "Approved" wasn't actually
        being recorded anywhere is_approved_stage-shaped. Combined with the
        new at-most-one constraint on southbrook.eco.stage, a pipeline is
        now always in exactly one of two states: zero flagged stages
        (refused here) or exactly one (used here) — never ambiguous.
        """
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may approve an ECO."))
        approved_stage = self.env["southbrook.eco.stage"].search(
            [("is_approved_stage", "=", True)], limit=1
        )
        if not approved_stage:
            raise UserError(
                _(
                    "No ECO stage is flagged as the Approved stage — "
                    "Approve cannot record where an approved ECO should "
                    "land. Flag exactly one stage as 'Approved Stage' "
                    "before approving any ECO."
                )
            )
        for eco in self:
            # FIX 9 (2026-08-02 blindspot pass): serialise concurrent
            # approvals of the SAME ECO. Without a row lock here, two
            # approvers clicking Approve on the same ECO at the same
            # moment both pass the state/already-approved checks below,
            # both compute their own approver_id/approval_date, and the
            # SECOND write simply overwrites the first's stamp — last-
            # write-wins, no conflict signal, and the audit trail records
            # the wrong approver as if the race never happened. FOR UPDATE
            # blocks the second approval until the first's transaction
            # commits (or rolls back); invalidate_recordset then forces
            # the checks below to read the POST-COMMIT state rather than a
            # stale ORM cache read. Mirrors the exact FOR UPDATE +
            # invalidate pattern _apply_bom already uses for the same
            # class of race (see FIX 8 on _apply_bom, below).
            self.env.cr.execute(
                "SELECT id FROM southbrook_eco WHERE id = %s FOR UPDATE",
                (eco.id,),
            )
            eco.invalidate_recordset(
                ["state", "approver_id", "approval_date", "stage_id"]
            )
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
            # approved_stage is guaranteed non-empty here (checked, fail-
            # closed, before this loop) — no fallback branch needed
            # anymore. The lifecycle sentinel is required on THIS write:
            # write()'s FIX 3 guard refuses a bare entry into an
            # is_approved_stage-flagged stage from anyone but this exact,
            # sanctioned call site.
            eco.with_context(plm_lifecycle=PLM_LIFECYCLE).write(
                {"stage_id": approved_stage.id}
            )
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
            # FIX 1 (2026-08-02 blindspot pass): pass the identity-checked
            # sentinel, not a bare True — see plm_lifecycle.py.
            eco = eco.with_context(plm_lifecycle=PLM_LIFECYCLE)
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
            # The lifecycle sentinel (FIX 1 — identity-checked, not a bare
            # True a JSON-RPC caller could forge) authorises this write
            # past both the terminal-write immutability guard (a rejected
            # ECO IS terminal) and the terminal-forge guard, since we've
            # already run the approver-group check that stands in for
            # those guards' intent here. first_stage is never
            # is_approved_stage-flagged in a sane pipeline (it's the FIRST
            # non-final stage), so the FIX 3 stage-entry guard on write()
            # is a non-issue here too.
            eco.with_context(plm_lifecycle=PLM_LIFECYCLE).write({
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

        FIX 11 (2026-08-02 blindspot pass): the successor does NOT inherit
        the predecessor's description or attachments. copy() otherwise
        carries the FULL description/document_ids forward (neither is
        copy=False), so an approver reviewing the successor could read the
        OLD rationale/cut-sheets and sign off a change while thinking
        they're reading about the NEW one. The description default below
        is a short stub linking back to the predecessor by name instead of
        duplicating its text; document_ids is explicitly cleared.
        emergency_bypass / bypass_reason are already copy=False (verified
        against the field definitions above) — copy() drops them from the
        successor's vals entirely, so no explicit clear is needed for
        those two; noted here rather than duplicated.
        """
        self.ensure_one()
        # FIX 4 (2026-08-02 blindspot pass): action_create_superseding used
        # to be callable repeatedly on the same applied ECO. superseded_by_id
        # is a plain Many2one — nothing stopped a second call from silently
        # OVERWRITING it with a second successor, orphaning the first
        # successor from the audit chain (self.superseded_by_id now points
        # only at the second one; the first is left dangling with a
        # supersedes_id pointing back at self but nothing pointing forward
        # to it). Refuse outright and name the existing successor so the
        # user works on THAT ECO instead of minting a competing one.
        if self.superseded_by_id:
            raise UserError(
                _(
                    "ECO %(eco)s has already been superseded by "
                    "%(succ)s — work on that ECO instead of creating "
                    "another successor."
                )
                % {"eco": self.name, "succ": self.superseded_by_id.display_name}
            )
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
                # FIX 11: stale-evidence guard — see the docstring above.
                # A short stub, not the predecessor's own description,
                # so the approver is prompted to state the NEW rationale
                # rather than re-reading (and unintentionally re-approving)
                # the OLD one.
                "description": _(
                    "<p>Supersedes %s. State the NEW rationale here.</p>"
                )
                % self.name,
                # FIX 11: clear engineering documents — the predecessor's
                # vendor cut sheets / spec PDFs answered the OLD change;
                # carrying them forward would let an approver sign off the
                # new ECO while looking at attachments for a different one.
                "document_ids": [(5, 0, 0)],
            }
        )
        # sudo() on the back-link: the PLM User record rule scopes a plain
        # user's writes to their OWN OPEN ECOs, and this write targets an
        # applied one (possibly someone else's). The method's own state
        # gate + the lifecycle sentinel make this the sanctioned lifecycle
        # link, and any PLM User may raise a successor — so elevate for
        # exactly this one write.
        self.sudo().with_context(plm_lifecycle=PLM_LIFECYCLE).write(
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
        already be set (i.e. action_approve must have run), and the ECO
        must actually be sitting in the Approved stage.

        FIX 2 (2026-08-02 blindspot pass): the Approved-stage check below
        used to be PERMISSIVE when no stage carried is_approved_stage at
        all ("an older install isn't hard-broken") — but that made it
        trivially defeatable going forward: any approver could unset the
        flag on every stage and every ECO became applyable straight from
        Draft, with no error, no chatter, nothing to notice. Fail-closed
        now: Apply refuses outright when no stage is flagged, instead of
        silently permitting. Combined with the new at-most-one constraint
        on southbrook.eco.stage, a pipeline is always in exactly one of
        two states — zero flagged stages (refused) or exactly one (used)
        — never the ambiguous "flag drifted away and nobody noticed"
        state the old fallback allowed.

        Typed effectivity (2026-08-02): every check in this method still
        runs, unweakened, exactly as before. What changed is what happens
        AFTER the checks pass — the actual commit (handler dispatch,
        impact scan, terminal write) now lives in _execute_apply(), which
        this method calls per-record once its own checks are satisfied.
        This method also gained one more check of its own: a Scheduled
        ECO (effectivity_type == 'date') whose effective_date hasn't
        arrived yet refuses Apply outright rather than jumping the queue
        — see the comment at that check, below, for why. The split exists
        so _cron_apply_due_ecos can call _execute_apply() directly once a
        Scheduled ECO's effective_date arrives, replaying an
        already-recorded approval decision on schedule without an acting
        human identity to check four-eyes/group membership against.
        """
        if not self.env.user.has_group(
            "southbrook_plm.group_southbrook_plm_approver"
        ):
            raise UserError(_("Only a PLM Approver may apply an ECO."))
        # search_count, not search+bool, purely so the intent below reads
        # as "is there at least one" rather than relying on bool()'s
        # implicit truthiness of a recordset.
        approved_stage_exists = bool(
            self.env["southbrook.eco.stage"].search_count(
                [("is_approved_stage", "=", True)]
            )
        )
        if not approved_stage_exists:
            raise UserError(
                _(
                    "No ECO stage is flagged as the Approved stage — Apply "
                    "cannot verify that an ECO has actually passed "
                    "approval. Flag exactly one stage as 'Approved Stage' "
                    "before applying any ECO."
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
            # approved_stage_exists is guaranteed True here (checked,
            # fail-closed, before this loop) — the location check is now
            # unconditional; there is no more "pipeline predates the flag"
            # branch to fall back to.
            if not eco.stage_id.is_approved_stage:
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
            # Typed effectivity (2026-08-02): a Scheduled ECO
            # (effectivity_type == 'date') that hasn't reached its
            # effective_date yet is approved but NOT YET IN FORCE — that
            # is the entire point of scheduling it instead of applying
            # immediately. A human clicking Apply early must be told,
            # not silently allowed to jump the queue; the escape hatch is
            # explicit (clear/advance the date, or switch to Immediate).
            # _cron_apply_due_ecos (below) applies a due Scheduled ECO
            # automatically once effective_date arrives, and it does so
            # by calling _execute_apply() directly — NOT this method — so
            # there is no "caller is the cron" branch to special-case
            # here: any execution that reaches this line is, by
            # construction, a human or API caller going through this
            # gated entry point, never the cron.
            if (
                eco.effectivity_type == "date"
                and eco.effective_date
                and eco.effective_date > fields.Datetime.now()
            ):
                raise UserError(
                    _(
                        "ECO %(eco)s is scheduled for %(date)s and will "
                        "apply automatically then — it is approved but "
                        "not yet in force. To apply it now instead, clear "
                        "or move up its Effective Date, or switch "
                        "Effectivity to Immediate, then Apply again."
                    )
                    % {"eco": eco.name, "date": eco.effective_date}
                )
            eco._execute_apply()
        return True

    def _execute_apply(self):
        """Actually commit an ECO's change: handler dispatch, the
        savepoint-wrapped impact scan, and the terminal write. ensure_one().

        Split out of action_apply (2026-08-02, typed effectivity) so the
        scheduled-effectivity cron (_cron_apply_due_ecos, below) can reuse
        the EXACT SAME commit path a human Apply click uses — same handler
        dispatch, same non-fatal impact scan, same terminal-write sentinel
        — without also re-running action_apply's identity/authorisation
        checks (PLM Approver group, already-applied/rejected, approval
        prerequisite, approved-stage location, four-eyes). Those checks
        are about WHO DECIDES to apply an ECO; for a scheduled ECO that
        decision was already made and recorded (approver_id /
        approval_date stamped) when a second person approved it, possibly
        days or weeks before the cron ever runs. The cron has no acting
        human identity to check four-eyes or group membership against —
        it is executing a recorded decision on a timer, not making a new
        one — so it deliberately calls straight into this method.

        Do NOT call this directly for a human-initiated apply. Go through
        action_apply, which is what actually enforces every one of the
        checks above before it calls this.
        """
        self.ensure_one()
        handler = getattr(self, "_apply_%s" % (self.target_kind or ""), None)
        if handler is None:
            raise UserError(
                _("No apply handler for ECO type kind '%s'.")
                % self.target_kind
            )
        handler()
        # SAMI PRD PLM-02 — flag live MOs that were referencing the
        # versioned target. Non-fatal: failure to scan MUST NEVER
        # block the ECO apply itself.
        #
        # FIX 10 (2026-08-02 blindspot pass): the bare try/except here
        # was documented "non-fatal" but wasn't actually safe against
        # every failure mode — a DB-level error (e.g. an
        # IntegrityError from the m2m write) poisons the CURSOR, not
        # just raises a Python exception, and every subsequent
        # statement on that cursor (including the terminal-state write
        # below, and this whole ECO's apply) then fails too, rolling
        # back the ECO's own already-committed change handler right
        # along with it. `with self.env.cr.savepoint():` scopes any
        # such failure to a rollback of ONLY the scan, leaving the
        # cursor usable for everything after it — the exact pattern
        # southbrook_plm_productgraph's ECO bridge already uses for
        # this identical class of problem (see
        # _trigger_pg_release there). Logged via the module logger
        # instead of a silent `pass` so an operator can actually see
        # that the scan failed, even though the apply itself proceeds.
        try:
            with self.env.cr.savepoint():
                self._scan_and_flag_affected_mos()
        except Exception:  # noqa: BLE001
            _logger.exception(
                "southbrook_plm: impact scan failed for ECO %s — the "
                "apply itself is unaffected (informational only).",
                self.name,
            )
        applied_stage = self.env["southbrook.eco.stage"].search(
            [("is_applied_stage", "=", True)], limit=1
        )
        now = fields.Datetime.now()
        write_vals = {
            "state": "applied",
            "applied_date": now,
            "stage_id": applied_stage.id if applied_stage else self.stage_id.id,
        }
        # approver_id/approval_date are guaranteed set already — either
        # checked by action_apply above, or by _cron_apply_due_ecos'
        # search domain, which only selects ECOs that already carry both.
        # No de-facto stamping here. The lifecycle sentinel (FIX 1)
        # authorises the terminal-state write past the write() forge-guard
        # AND the terminal-write immutability guard — we are inside either
        # the approver-gated action or the cron's own recorded-decision
        # replay, and have already run the change handler.
        self.with_context(plm_lifecycle=PLM_LIFECYCLE).write(write_vals)

    # ------------------------------------------------------------------
    # Scheduled-effectivity cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_apply_due_ecos(self, limit=100):
        """Hourly cron: apply Scheduled (effectivity_type == 'date') ECOs
        whose effective_date has arrived.

        Deliberately calls _execute_apply() directly, NOT action_apply().
        action_apply's checks (PLM Approver group membership, four-eyes,
        emergency-bypass logging) are about WHO DECIDES to apply an ECO —
        that decision was already made and recorded (approver_id +
        approval_date) when a second, independent person approved this
        ECO, possibly long before this cron run. The cron is executing a
        recorded decision on schedule, not making a new one, and it has
        no acting human identity to run those checks against even if it
        wanted to. It must NOT re-run them.
        It MUST, however, verify the approval was genuinely recorded
        before touching anything — that's what the search domain below
        does (approver_id / approval_date set, and — when the pipeline
        defines one — parked in the flagged Approved stage). Skipping
        that verification would let a scheduled-but-never-approved ECO
        apply itself purely because a calendar date arrived, which would
        be a real approval bypass, not a scheduling convenience.

        Each ECO is applied inside its own savepoint + try/except so one
        broken ECO (a bad handler, a stale target) cannot abort the whole
        batch — the remaining due ECOs still get their chance this run,
        and the failed one is simply retried on the next run untouched
        (its state is still 'open', so it stays in next run's domain).
        """
        approved_stage_exists = bool(
            self.env["southbrook.eco.stage"].search_count(
                [("is_approved_stage", "=", True)]
            )
        )
        domain = [
            ("state", "=", "open"),
            ("effectivity_type", "=", "date"),
            ("effective_date", "<=", fields.Datetime.now()),
            ("approver_id", "!=", False),
            ("approval_date", "!=", False),
        ]
        # Mirror action_apply's approved-stage check — and mirror its
        # FAIL-CLOSED posture too. An earlier draft of this cron made the
        # check permissive when no stage carries is_approved_stage, on the
        # reasoning that a silent cron refusing everything is worse than
        # one trusting approver_id/approval_date alone. That reasoning is
        # wrong here, and dangerously so: the whole point of the 2026-08-02
        # fail-closed fix is that un-flagging the stages must never widen
        # what can be applied. A permissive cron reopens exactly that hole
        # on a delay — approve an ECO while the flag is set, schedule it,
        # unset the flag, and the cron applies it with no stage evidence.
        # Refusing loudly is the correct behaviour; the log line below is
        # the "something to look at" that the permissive version was
        # reaching for.
        if not approved_stage_exists:
            _logger.error(
                "southbrook_plm: scheduled-ECO cron is standing down — no "
                "southbrook.eco.stage carries is_approved_stage, so an "
                "approved-stage gate cannot be evidenced. Flag exactly one "
                "stage as the Approved stage; scheduled ECOs will apply on "
                "the next run. (Refusing rather than applying on "
                "approver_id alone: un-flagging stages must never widen "
                "what can be applied.)"
            )
            return 0
        domain.append(("stage_id.is_approved_stage", "=", True))
        due = self.search(domain, order="effective_date asc", limit=limit)
        # Returned so the cron log / a caller can see how much actually
        # landed, and so the stood-down path above (return 0) is
        # type-consistent with a normal run rather than returning True.
        applied = 0
        for eco in due:
            try:
                with self.env.cr.savepoint():
                    eco._execute_apply()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "southbrook_plm: scheduled-effectivity apply failed "
                    "for ECO %s (effective_date %s) — will be retried on "
                    "the next cron run.",
                    eco.name,
                    eco.effective_date,
                )
                eco.message_post(
                    body=_(
                        "Scheduled apply failed (effective date "
                        "%(date)s) — will be retried automatically on "
                        "the next cron run. See server logs for the "
                        "error."
                    )
                    % {"date": eco.effective_date}
                )
                continue
            eco.message_post(
                body=_(
                    "Applied automatically on its scheduled effective "
                    "date (%(date)s)."
                )
                % {"date": eco.effective_date}
            )
            applied += 1
        return applied

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
        # Serialise concurrent applies against the same TEMPLATE, not just
        # this one BoM row.
        #
        # The 2026-08-01 hardening pass locked only `old`'s row, then
        # computed next_version from max(southbrook_version) across every
        # mrp.bom sharing old.product_tmpl_id. That left a gap: two ECOs
        # applying concurrently against DIFFERENT SIBLING VARIANT BoMs of
        # the SAME template (e.g. old_A = 24" white BoM, old_B = 30" white
        # BoM, both product_tmpl_id=T) each take a row lock on their OWN
        # row — those locks don't conflict with each other at all — so
        # both pass the archived-check, both read the same max() across T,
        # both compute the same next_version, and the second COPY then
        # dies on mrp.bom's version-uniqueness constraint (see
        # mrp_bom.py._check_southbrook_version_unique). Not a silent
        # corruption like the original H3/M2 gap, but still a hard,
        # confusing failure for a legitimate concurrent apply.
        #
        # Fix (M4x hardening, 2026-08-02 blindspot pass): lock EVERY
        # mrp.bom row for the template — not just `old`'s — before
        # computing max(). The ordering below is deliberate and must stay
        # in this sequence:
        #
        #   1. LOCK      — SELECT ... FOR UPDATE across ALL rows for the
        #                   template, in a deterministic id order (ORDER
        #                   BY id) so two concurrent applies against
        #                   different sibling rows always attempt to
        #                   acquire the SAME lock sequence and block each
        #                   other rather than deadlocking against each
        #                   other in opposite orders.
        #   2. INVALIDATE — drop the ORM cache for exactly the rows we
        #                   just locked, so the checks/reads in steps 3-4
        #                   see the POST-LOCK, POST-COMMIT database state
        #                   rather than a stale pre-lock cache read.
        #   3. CHECK      — old.active, unchanged from the 2026-08-01 pass
        #                   (refuse if a concurrent apply already archived
        #                   `old` out from under us).
        #   4. COMPUTE    — max(southbrook_version) across the (now
        #                   lock-held, cache-fresh) template rows.
        #
        # Only after all four does the copy()/archive below run. The
        # second apply in a concurrent pair blocks at step 1 until the
        # first's transaction commits or rolls back, so it computes its
        # max() against a template that already includes the first
        # apply's new row — no collision is possible.
        self.env.cr.execute(
            "SELECT id FROM mrp_bom WHERE product_tmpl_id = %s ORDER BY id "
            "FOR UPDATE",
            (old.product_tmpl_id.id,),
        )
        locked_ids = [row[0] for row in self.env.cr.fetchall()]
        self.env["mrp.bom"].browse(locked_ids).invalidate_recordset(
            ["active", "southbrook_version"]
        )
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
        # rows count too. (M4x hardening, 2026-08-01; lock widened to the
        # whole template on 2026-08-02 — see above.)
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
        # FIX 12 (2026-08-02 blindspot pass): _scan_and_flag_affected_mos
        # returns early for every target_kind other than 'bom' — cut specs
        # don't bind to mrp.production directly — so activating a new cut
        # spec (which changes panel-cut geometry for EVERY future job
        # built against it) used to notify nobody at all. Capture the
        # CURRENTLY active spec's values before action_activate supersedes
        # it, so we can diff old vs new and post a delta. Deliberately
        # read via sudo() + _get_active(), matching mrp_bom.py's
        # _get_cut_constants() seam (cut specs are PLM-restricted reads).
        prior_spec = self.env["southbrook.cut.spec"].sudo()._get_active()
        prior_values = prior_spec.constants_dict() if prior_spec else {}
        new_values = self.cut_spec_id.constants_dict()
        # FIX 6 (2026-08-02 blindspot pass): action_activate now refuses to
        # run unless it was reached with the lifecycle sentinel — this is
        # the one sanctioned call site. See southbrook.cut.spec.
        # action_activate's own docstring for the full rationale (bare
        # public method + RPC ignoring view restrictions = a live spec
        # activation with zero ECO trail).
        self.cut_spec_id.with_context(
            plm_lifecycle=PLM_LIFECYCLE
        ).action_activate()
        self.message_post(
            body=_("Cut spec %s activated.") % self.cut_spec_id.display_name
        )
        # Post the geometry delta. Deliberately defensive and entirely
        # separate from the activation above: this notice is informational
        # only (same non-fatal contract as _scan_and_flag_affected_mos /
        # action_apply's impact scan) and must never block or unwind an
        # apply that has already committed the real change.
        try:
            changed = {
                fname: (prior_values.get(fname), new_values.get(fname))
                for fname in CONSTANT_FIELDS
                if prior_values.get(fname) != new_values.get(fname)
            }
            if changed:
                delta_lines = "".join(
                    "<li>%s: %s -> %s</li>" % (fname, old_val, new_val)
                    for fname, (old_val, new_val) in changed.items()
                )
                self.message_post(
                    body=_(
                        "Cut-spec geometry delta (affects every future "
                        "cut job): <ul>%s</ul>"
                    )
                    % delta_lines
                )
            elif prior_spec:
                self.message_post(
                    body=_(
                        "Cut spec activated with no changed geometric "
                        "constants versus the prior active spec."
                    )
                )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "southbrook_plm: cut-spec geometry-delta notice failed for "
                "ECO %s (informational only — activation already "
                "committed).",
                self.name,
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
        # sudo() on the MO search + the m2m write. A PLM Approver is NOT
        # necessarily a Manufacturing user — the two groups are unrelated —
        # so this read raised AccessError for exactly the people the module
        # expects to apply ECOs. The failure was invisible: action_apply
        # swallows scan errors by design (the impact scan must never block a
        # committed change), so every apply by a non-mrp approver silently
        # produced an EMPTY affected-MO list. That is the root cause of the
        # 2026-08-01 review's "affected_mo_count reads 0 on every ECO".
        #
        # Elevating is correct here on the same reasoning already applied to
        # _apply_bom's BoM copy and mrp_bom._get_cut_constants' spec read:
        # the ECO approval gate IS the authorization, and this is a
        # read-only impact analysis whose ONLY side effect is populating a
        # readonly audit m2m on the ECO itself. It grants no MO access to
        # the user — the smart button that drills into the list is still
        # subject to that user's own mrp.production rights.
        # (2026-08-02: found by the blindspot pass's own test run.)
        affected = self.env["mrp.production"].sudo().search([
            ("bom_id", "=", self.bom_id.id),
            ("state", "in", ["confirmed", "progress", "to_close"]),
        ])
        if affected:
            self.sudo().affected_mo_ids = [(4, mo.id) for mo in affected]
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
