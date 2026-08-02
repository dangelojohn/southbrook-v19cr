# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.eco.type — what kind of change an ECO represents.

The ``target_kind`` selection is the load-bearing field: it drives which
target field the ECO form shows and which branch ``action_apply`` takes.

    bom       -> versions a canonical template mrp.bom
    cut_spec  -> activates a southbrook.cut.spec (the NF14 constants)
    rule      -> a config_rules.xml / code change; ECO records the git ref
    document  -> an engineering-document update only
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SouthbrookEcoType(models.Model):
    _name = "southbrook.eco.type"
    _description = "Southbrook ECO Type"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    target_kind = fields.Selection(
        [
            ("bom", "Template BoM"),
            ("cut_spec", "Parametric Cut Spec"),
            ("rule", "Construction Rule (code / git)"),
            ("document", "Engineering Document"),
        ],
        required=True,
        default="bom",
        help="Determines which target the ECO governs and how it is applied.",
    )
    description = fields.Text()
    eco_count = fields.Integer(compute="_compute_eco_count")

    # ------------------------------------------------------------------
    # ECO workflow hygiene (2026-08-02). Prod had ECOs sitting in Draft
    # and in Under Review for 61 days apiece with ZERO scheduled
    # activities and nobody notified — the pipeline had no heartbeat.
    # These two fields drive southbrook.eco's heartbeat: the moment an
    # ECO of this type leaves Draft, southbrook.eco._schedule_review_
    # activity schedules a "Review ECO" activity for every reviewer here,
    # with a deadline of review_sla_days out; if it's still unapproved
    # once that many days have passed, southbrook.eco._cron_escalate_
    # stale_ecos posts an escalation notice. See both of those, in
    # models/southbrook_eco.py, for the full mechanics.
    # ------------------------------------------------------------------
    review_sla_days = fields.Integer(
        default=7,
        help="How many days an ECO of this type may sit unapproved "
        "(counted from the moment it leaves Draft — southbrook.eco."
        "date_submitted, falling back to its creation date for ECOs "
        "raised before that field existed) before the daily stale-ECO "
        "cron (southbrook.eco._cron_escalate_stale_ecos) posts an "
        "escalation notice to chatter. Also used as the deadline "
        "(today + this many days) on the initial 'Review ECO' activity "
        "scheduled for each reviewer below.",
    )
    reviewer_ids = fields.Many2many(
        "res.users",
        string="Default Reviewers",
        help="Who gets the 'Review ECO' activity when an ECO of this "
        "type leaves Draft, and who gets tagged in the SLA escalation "
        "notice if it then sits unapproved past Review SLA (Days). "
        "EVERY user selected here is expected to hold PLM Approver "
        "rights (southbrook_plm.group_southbrook_plm_approver) — a "
        "reviewer who cannot actually approve the ECO is a dead end. "
        "This is validated below at save time rather than as a domain "
        "on the field itself: a domain expressing 'must be in group X' "
        "needs a computed domain referencing the group's xmlid, which "
        "is fiddly to wire cleanly here — the constraint below is just "
        "as reliable and names the offending user(s) directly. If left "
        "empty, no review activity is scheduled for ECOs of this type — "
        "the ECO posts a chatter line saying so instead of guessing a "
        "substitute reviewer nobody actually agreed to.",
    )

    @api.constrains("reviewer_ids")
    def _check_reviewers_are_approvers(self):
        # has_group(), not a res.groups.users relation lookup — has_group
        # is the stable public API every other group check in this
        # module family already uses (see southbrook_eco.py's many
        # self.env.user.has_group(...) calls), and it is immune to the
        # v19 res.users field rename that touched the OTHER side of this
        # relationship (groups_id -> group_ids) — see
        # odoo19_res_users_group_ids_rename.md. Iterating per-reviewer
        # and calling has_group on each is a little more verbose than a
        # single relational lookup would be, but it can't be silently
        # broken by that rename.
        for eco_type in self:
            non_approvers = eco_type.reviewer_ids.filtered(
                lambda u: not u.has_group(
                    "southbrook_plm.group_southbrook_plm_approver"
                )
            )
            if non_approvers:
                raise ValidationError(
                    _(
                        "ECO Type '%(type)s': Default Reviewers must all "
                        "hold PLM Approver rights — %(users)s do not. A "
                        "reviewer who cannot approve the ECO is a dead "
                        "end for the review activity this field drives."
                    )
                    % {
                        "type": eco_type.name,
                        "users": ", ".join(
                            non_approvers.mapped("display_name")
                        ),
                    }
                )

    def _compute_eco_count(self):
        data = self.env["southbrook.eco"]._read_group(
            [("eco_type_id", "in", self.ids)],
            groupby=["eco_type_id"],
            aggregates=["__count"],
        )
        mapped = {t.id: c for t, c in data}
        for rec in self:
            rec.eco_count = mapped.get(rec.id, 0)

    def action_view_ecos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "southbrook.eco",
            "view_mode": "kanban,list,form",
            "domain": [("eco_type_id", "=", self.id)],
            "context": {"default_eco_type_id": self.id},
        }
