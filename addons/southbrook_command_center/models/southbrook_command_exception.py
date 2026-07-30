# SPDX-License-Identifier: LGPL-3.0-only
"""``southbrook.command.exception`` — Central Command's one new model.

Implements Deliverable 2 (DELIVERABLE_2_DATA_MODEL.md) field-for-field.

Design invariants this file must never violate (see Deliverable 2 §"Why no
``@api.depends``..." and the repo-wide Odoo 19 CE traps this deployment has
hit before):

* ``severity`` / ``severity_rank`` / ``impact_summary`` / ``recommended_action``
  / ``why_text`` are written IMPERATIVELY by a sync/materializer method
  (out of scope for this file — see the class docstring below), never by
  ``@api.depends`` against a foreign model. A wrong field name in
  ``@api.depends`` silently breaks the whole registry on this deployment.
* ``source_record`` is ``store=False`` and MUST NEVER be referenced in a view
  domain (Odoo 19 rejects non-stored computed fields used in domains — this
  is enforced by convention/review here, not by the ORM).
* The uniqueness rule uses ``models.Constraint`` (NOT the legacy
  ``_sql_constraints``, which Odoo 19 silently ignores).
* ``res.users`` access elsewhere in this addon uses ``group_ids``, never the
  removed ``groups_id`` name (N/A directly to this model, called out here
  per the frozen-contract instructions).

Scope note: this file implements the schema + the exception's own workflow
(``action_acknowledge`` / ``action_resolve`` / ``action_dismiss``) only. The
``_sync_from_source()`` materializer described in Deliverable 2 §2 (which
reads the seven heterogeneous source models and writes the imperative
fields) is deliberately NOT implemented here — it is a separate, larger
surface explicitly out of this file's scope. ``SEVERITY_HARMONIZATION`` below
is provided as a documented starting point for whoever implements that
method next, so the mapping table doesn't have to be re-derived.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


# Per-source severity vocabularies harmonized onto this model's own
# low/medium/high/critical scale (Deliverable 2 §2, `severity` row).
# Not consumed anywhere in this file — provided for the future
# `_sync_from_source()` implementation so the three source vocabularies
# (verified: southbrook.mi.check.severity, southbrook.cmms.breakdown_alert
# .severity, southbrook.ncr.severity are three distinct Selection value
# sets) are harmonized identically wherever that method is written.
SEVERITY_HARMONIZATION = {
    # southbrook.mi.check.severity -> (severity, severity_rank)
    "mi_check": {
        "info": ("low", 3),
        "warning": ("medium", 2),
        "blocker": ("critical", 0),
    },
    # southbrook.cmms.breakdown_alert.severity -> passthrough (same vocabulary)
    "breakdown_alert": {
        "low": ("low", 3),
        "medium": ("medium", 2),
        "high": ("high", 1),
        "critical": ("critical", 0),
    },
    # southbrook.ncr.severity -> minor/major/critical
    "ncr": {
        "minor": ("low", 3),
        "major": ("medium", 2),
        "critical": ("critical", 0),
    },
}


class SouthbrookCommandException(models.Model):
    _name = "southbrook.command.exception"
    _description = "Central Command — accountable exception aggregation"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "severity_rank desc, create_date desc"

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name = fields.Char(
        default=lambda self: _("New"),
        copy=False,
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Taxonomy / source pointer
    # ------------------------------------------------------------------
    exception_type = fields.Selection(
        [
            ("mi_blocker", "MI Blocker"),
            ("breakdown_alert", "Breakdown Alert"),
            ("quality_ncr", "Quality NCR"),
            ("job_blocked", "Job Blocked"),
            ("approval_gate_stall", "Approval Gate Stall"),
            ("bom_skip", "BoM Skip"),
            ("cutlist_divergence", "Cutlist Divergence"),
            ("po_delivery_risk", "PO Delivery Risk"),
        ],
        required=True,
        index=True,
        tracking=True,
    )
    source_model = fields.Char(required=True, index=True)
    source_res_id = fields.Integer(required=True, index=True)

    # NEW: convenience accessor only. depends() is intra-model (source_model,
    # source_res_id both live on THIS record) — never a cross-model depends,
    # so this cannot hit the "wrong field name silently breaks registry" trap
    # described in Deliverable 2. store=False by design; NEVER reference this
    # field inside a <field name="domain">.
    source_record = fields.Reference(
        selection=[
            ("mrp.production", "Manufacturing Order"),
            ("project.task", "Job / Task"),
            ("sale.order", "Sales Order"),
            ("purchase.order", "Purchase Order"),
            ("southbrook.mi.check", "MI Check"),
            ("southbrook.cmms.breakdown_alert", "Breakdown Alert"),
            ("southbrook.ncr", "NCR"),
        ],
        compute="_compute_source_record",
        store=False,
    )

    @api.depends("source_model", "source_res_id")
    def _compute_source_record(self):
        for rec in self:
            if rec.source_model and rec.source_res_id:
                rec.source_record = f"{rec.source_model},{rec.source_res_id}"
            else:
                rec.source_record = False

    # ------------------------------------------------------------------
    # Reference-only FKs — pure pointers, no source field values copied
    # ------------------------------------------------------------------
    mo_id = fields.Many2one("mrp.production", string="Manufacturing Order")
    task_id = fields.Many2one("project.task", string="Job")
    sale_order_id = fields.Many2one("sale.order", string="Sales Order")
    purchase_order_id = fields.Many2one("purchase.order", string="Purchase Order")
    workcenter_id = fields.Many2one("mrp.workcenter", string="Workcenter")

    # ------------------------------------------------------------------
    # Harmonized severity — written imperatively by the (not-yet-implemented
    # here) sync method, never by @api.depends against a foreign model.
    # ------------------------------------------------------------------
    severity = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        required=True,
        default="low",
        tracking=True,
    )
    severity_rank = fields.Integer(
        default=3,
        index=True,
        help="Lower = more severe (0=critical .. 3=low), matching the "
             "existing southbrook.mi.check.severity_rank convention. "
             "Written imperatively alongside `severity`.",
    )

    # ------------------------------------------------------------------
    # Ownership / workflow
    # ------------------------------------------------------------------
    # NOTE (OQ-8, DELIVERABLE_8_OPEN_QUESTIONS.md): Deliverable 2 declares
    # this field `required`, but OQ-8 is an OPEN, owner-level policy
    # question about whether a source with no natural responsible user
    # (e.g. southbrook.cmms.breakdown_alert) should route to a role-queue
    # placeholder user or be left null pending manual triage — which is in
    # tension with `required=True`. This file implements the field EXACTLY
    # as specified in Deliverable 2 (required=True); whoever implements the
    # materializer MUST always resolve a concrete owner_id (e.g. a
    # role-queue placeholder res.users record) at create time until OQ-8 is
    # formally ratified. Do not relax `required` without re-reading OQ-8.
    owner_id = fields.Many2one("res.users", required=True, tracking=True)

    state = fields.Selection(
        [
            ("new", "New"),
            ("acknowledged", "Acknowledged"),
            ("in_progress", "In Progress"),
            ("resolved", "Resolved"),
            ("dismissed", "Dismissed"),
        ],
        default="new",
        required=True,
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Rule-based narrative fields — imperative, never @api.depends
    # ------------------------------------------------------------------
    impact_summary = fields.Char(size=280)
    recommended_action = fields.Text()
    why_text = fields.Text()

    # ------------------------------------------------------------------
    # Hermes link — pure FK, never mirrors Hermes's own fields
    # ------------------------------------------------------------------
    hermes_recommendation_id = fields.Many2one(
        "southbrook.hermes.recommendation",
        string="Filed Recommendation",
    )

    # ------------------------------------------------------------------
    # Timestamps (own workflow only — feeds Success Definition #2)
    # ------------------------------------------------------------------
    acknowledged_date = fields.Datetime(readonly=True)
    resolved_date = fields.Datetime(readonly=True)
    dismissed_date = fields.Datetime(readonly=True)

    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    # ------------------------------------------------------------------
    # P0 alerting spine (E2E review 2026-07-06) — push, not pull.
    # `alert_notified` flips True once an exception has been pushed to its
    # owner by the email-digest cron, so each NEW high/critical issue is
    # emailed exactly once (no re-spam) while nothing is silently missed.
    # ------------------------------------------------------------------
    alert_notified = fields.Boolean(default=False, copy=False, index=True)
    alert_notified_date = fields.Datetime(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Stable business identity (2026-07-30).
    #
    # The original uniqueness rule keyed on (source_model, source_res_id,
    # exception_type). source_res_id is a southbrook.mi.check row id, and the
    # MI sweep cron DELETES AND RECREATES those rows every 15 minutes — so the
    # ids churn, the key never matches twice, and every sweep minted a fresh
    # exception. Measured 2026-07-30: 38,910 open rows tracing to 9 MOs and 17
    # live checks, growing ~2,600/day.
    #
    # dedupe_key holds what is actually stable about the condition (which MO,
    # which category, which check), so a re-fire updates one row. Archived
    # historical duplicates carry NULL, and Postgres does not collide NULLs in
    # a unique index — which is what lets them stay in the table for audit.
    # ------------------------------------------------------------------
    dedupe_key = fields.Char(
        index=True,
        copy=False,
        help="Stable identity of the underlying business condition. One open "
             "exception per key. NULL on archived historical duplicates.",
    )

    _dedupe_key_uniq = models.Constraint(
        "unique(dedupe_key)",
        "An exception already exists for this business condition.",
    )

    # ------------------------------------------------------------------
    # Recurrence signal (2026-07-30, alongside dedupe_key).
    #
    # Collapsing the keying-bug backlog to one survivor per condition throws
    # away "this has re-fired 2,000 times since 5 July" unless something
    # captures it explicitly. Written IMPERATIVELY by _upsert_exception() in
    # command_center_materialize.py (never @api.depends against a foreign
    # model — see the module docstring's design invariants) and backfilled
    # once, on the collapsed bucket, by the 19.0.3.0.0 migration.
    #
    # Deliberately NOT mail.thread-tracked, even though this model inherits
    # mail.thread. A live blocker re-fires every ~15 minutes forever — that
    # churn is exactly why dedupe_key exists — so a tracked field here would
    # write a mail.message tracking row every 15 minutes for the entire life
    # of the condition. That is the same flood the migration's archive write
    # (untracked fields only, 2000-row slices) was built to avoid at
    # collapse time; tracking these fields would just reintroduce it going
    # forward instead of once at migration time. Plain stored fields:
    # severity/state changes are still tracked and remain the audit trail.
    # ------------------------------------------------------------------
    occurrence_count = fields.Integer(
        default=1,
        copy=False,
        help="Number of times this business condition has been observed. "
             "A re-fire of the same dedupe_key increments this instead of "
             "minting a new row.",
    )
    last_seen = fields.Datetime(
        copy=False,
        help="When this business condition was most recently observed.",
    )

    # ------------------------------------------------------------------
    # Create: sequence-backed name (falls back gracefully if no
    # ir.sequence with this code has been registered by a later data file)
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == _("New"):
                seq = self.env["ir.sequence"].next_by_code(
                    "southbrook.command.exception"
                )
                vals["name"] = seq or _("New")
        records = super().create(vals_list)
        for rec in records:
            if rec.name in (_("New"), False):
                rec.name = f"CC-{rec.id:06d}"
        return records

    # ------------------------------------------------------------------
    # Stable key helper — maps business conditions to identity tokens.
    # ------------------------------------------------------------------
    @api.model
    def _dedupe_key_for(self, exception_type, source_model, source_res_id,
                        source_record=None):
        """Stable identity for the condition this exception describes.

        For southbrook.mi.check the row id is worthless as an identity (the
        sweep recreates rows), so the key is built from what survives a sweep:
        the manufacturing order, the check category, and the check name.
        Every other source has stable ids and keeps the legacy triple.
        """
        if source_model == "southbrook.mi.check" and source_record is not None:
            mo = getattr(source_record, "production_id", False)
            mo_part = str(mo.id) if mo else "0"
            category = (getattr(source_record, "category", "") or "").strip().lower()
            name = (getattr(source_record, "name", "") or "").strip().lower()[:120]
            return "mi:%s:%s:%s:%s" % (mo_part, category, name, exception_type)
        return "%s:%s:%s" % (source_model, source_res_id, exception_type)

    # ------------------------------------------------------------------
    # Own-workflow action methods — the only fields this model may ever
    # write to itself outside the (out-of-scope-here) sync method.
    # ------------------------------------------------------------------
    def action_acknowledge(self):
        self.write(
            {
                "state": "acknowledged",
                "acknowledged_date": fields.Datetime.now(),
            }
        )
        return True

    def action_start_progress(self):
        """Not in Deliverable 2's three named actions, but required to
        reach 'in_progress' from the UI without skipping the state
        machine; does not stamp a new timestamp field (none defined for
        this transition in Deliverable 2)."""
        self.write({"state": "in_progress"})
        return True

    def action_resolve(self):
        self.write(
            {
                "state": "resolved",
                "resolved_date": fields.Datetime.now(),
                "active": False,
            }
        )
        return True

    def action_dismiss(self):
        # Dismiss is NOT a resolution — stamp dismissed_date, never
        # resolved_date (a New->Dismissed record was never resolved).
        self.write(
            {
                "state": "dismissed",
                "dismissed_date": fields.Datetime.now(),
                "active": False,
            }
        )
        return True

    def action_reopen(self):
        """Undo a resolve/dismiss — return the exception to the open queue.
        Powers the dashboard's undo-toast (misclick recovery)."""
        self.write(
            {
                "state": "new",
                "active": True,
                "resolved_date": False,
                "dismissed_date": False,
                "acknowledged_date": False,
            }
        )
        return True

    # ------------------------------------------------------------------
    # P0 alerting spine — email-digest cron (E2E review 2026-07-06).
    #
    # Central Command already MATERIALIZES exceptions every 15 min, but
    # nothing ever told a human: monitoring was pull-only, so an issue
    # could sit unseen until someone happened to open the dashboard (the
    # review found MOs stalled 18-34 days with zero alert). This cron
    # PUSHES every new high/critical open exception to its owner by email,
    # exactly once. It only READS already-materialized rows, so — unlike
    # the Phase-2 write hooks — enabling it carries no event-hook risk.
    # ------------------------------------------------------------------
    @api.model
    def _cron_send_alert_digest(self):
        pending = self.search([
            ("state", "in", ("new", "acknowledged", "in_progress")),
            ("severity", "in", ("high", "critical")),
            ("alert_notified", "=", False),
        ])
        if not pending:
            return True
        now = fields.Datetime.now()
        type_labels = dict(self._fields["exception_type"].selection)
        # One digest per owner, not one notification per exception. We push
        # via message_notify (not raw mail.mail): it lands in the owner's
        # in-app Discuss Inbox immediately — no SMTP required — AND emails
        # them once an outgoing mail server is configured. Gate on
        # partner_id (always present for a user), not email.
        by_owner = {}
        for exc in pending:
            owner = exc.owner_id
            if owner and owner.partner_id:
                by_owner.setdefault(owner.id, (owner, self.browse()))
                by_owner[owner.id] = (owner, by_owner[owner.id][1] | exc)
        MAX_ROWS = 25
        for owner, excs in by_owner.values():
            shown = excs.sorted(lambda e: e.severity_rank)[:MAX_ROWS]
            extra = len(excs) - len(shown)
            rows = "".join(
                "<tr>"
                "<td style='padding:4px 10px;border-bottom:1px solid #eee'>"
                "<strong>%s</strong></td>"
                "<td style='padding:4px 10px;border-bottom:1px solid #eee'>%s</td>"
                "<td style='padding:4px 10px;border-bottom:1px solid #eee'>%s</td>"
                "</tr>" % (
                    (exc.severity or "").upper(),
                    type_labels.get(exc.exception_type, exc.exception_type or ""),
                    (exc.impact_summary or exc.name or "").replace(
                        "&", "&amp;").replace("<", "&lt;"),
                )
                for exc in shown
            )
            if extra > 0:
                rows += (
                    "<tr><td colspan='3' style='padding:6px 10px;color:#6b665e'>"
                    "…and %s more — open Central Command for the full queue."
                    "</td></tr>" % extra
                )
            body = (
                "<div style='font-family:Roboto,Arial,sans-serif;color:#1a1814'>"
                "<h2 style='color:#4a2b1d;margin:0 0 8px'>Central Command — "
                "%(n)s alert(s) need your attention</h2>"
                "<p style='margin:0 0 12px'>New high/critical issues are open "
                "in the plant. Open Central Command for the full queue and to "
                "acknowledge or resolve them.</p>"
                "<table style='border-collapse:collapse;font-size:13px'>"
                "<tr><th style='text-align:left;padding:4px 10px'>Severity</th>"
                "<th style='text-align:left;padding:4px 10px'>Type</th>"
                "<th style='text-align:left;padding:4px 10px'>Impact</th></tr>"
                "%(rows)s</table></div>"
            ) % {"n": len(excs), "rows": rows}
            # message_notify posts to the owner's Inbox (and emails when
            # SMTP is up). Anchor it on the most-severe exception so the
            # notification links straight to a real record.
            try:
                shown[:1].message_notify(
                    partner_ids=owner.partner_id.ids,
                    subject=(
                        "[Central Command] %s alert(s) need attention"
                        % len(excs)),
                    body=body,
                )
            except Exception:  # noqa: BLE001 — one owner must not abort the run
                _logger.exception(
                    "Central Command alert digest failed for owner %s",
                    owner.login)
                continue
            # Mark this owner's batch pushed so we never re-notify the same
            # issues; genuinely new exceptions (default alert_notified=False)
            # surface on the next run.
            excs.write({"alert_notified": True, "alert_notified_date": now})
        return True
