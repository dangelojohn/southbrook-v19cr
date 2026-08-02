# SPDX-License-Identifier: LGPL-3.0-only
"""W034 (R4.W5, 2026-06-27) — Report Engineering Issue from a WorkOrder.

JTBD
----
"When I find a problem at the CNC that needs an engineering change,
I want to photo + describe it from the WO form and have a draft ECO
auto-created with the WO context attached, so engineering sees it
with full traceability."

Behaviour
---------
- Operator clicks "Report Engineering Issue" on the WO form. A
  transient wizard opens prefilled with WO / production / cabinet
  context.
- Operator types a description, picks a severity, optionally attaches
  a photo and (if a known defect already exists on the WO) links the
  originating `southbrook.mi.check`.
- On submit the wizard creates a DRAFT `southbrook.eco` (never auto-
  approved per spec). The photo + a back-link to the WO are posted to
  the ECO's chatter; the ECO's `description` carries the full
  operator-supplied narrative plus the WO/MO context block.
- The ECO is created with `sudo()` because this wizard is exposed to
  `mrp.group_mrp_user` (shop floor), and `southbrook.eco` access is
  scoped to `southbrook_plm.group_southbrook_plm_user` / `_approver`
  only — an operator has no access rows on the model. For the same
  reason, submit does NOT redirect into the new ECO's form (that
  raised an AccessError right after a successful save — 2026-08-02
  blindspot pass). It instead posts a traceable "Engineering change
  reported" note to the WO chatter (which the operator CAN read) and
  returns a `display_notification` toast naming the ECO.

Target ECO model
----------------
PRIMARY: `southbrook.eco` (shipped by southbrook_plm; transitively
guaranteed in this addon's dep chain via southbrook_mrp_pm → southbrook_plm).
FALLBACK: a newer `pg.eco` model from a future product_graph_eco addon
is preferred when present. We feature-detect at runtime (no manifest
dependency change required) so the wizard keeps working when PG ECO
lands without re-touching this code.

The fallback is name-based — we only check `self.env` for the model and
delegate field naming to a thin dict; the chatter message attachment
flow works uniformly via `message_post`.

Constraints honoured
--------------------
- ECO is created in DRAFT stage (`southbrook_plm.stage_draft` for the
  southbrook.eco branch). Never auto-approved.
- ECO target_kind defaults to `document` so no BoM / cut-spec mutation
  fires on apply — the engineer triages and re-types the ECO before
  approval if a BoM revision is the real outcome.
- No raw SQL. Pure ORM.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


SEVERITY_SELECTION = [
    ("low", "Low — minor / cosmetic"),
    ("medium", "Medium — affects quality but not safety"),
    ("high", "High — blocker / safety / customer-visible"),
]

# Mapping of wizard severity to ECO priority. southbrook.eco.priority
# uses '0' Normal / '1' High / '2' Urgent.
_SEVERITY_TO_ECO_PRIORITY = {
    "low": "0",
    "medium": "1",
    "high": "2",
}


class SouthbrookWoRaiseEcoWizard(models.TransientModel):
    _name = "southbrook.wo.raise.eco.wizard"
    _description = "Report Engineering Issue from a Work Order (W034)"

    workorder_id = fields.Many2one(
        "mrp.workorder",
        string="Work Order",
        required=True,
        ondelete="cascade",
    )
    production_id = fields.Many2one(
        "mrp.production",
        string="Manufacturing Order",
        related="workorder_id.production_id",
        readonly=True,
        store=False,
    )
    workcenter_id = fields.Many2one(
        "mrp.workcenter",
        string="Work Center",
        related="workorder_id.workcenter_id",
        readonly=True,
        store=False,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        related="workorder_id.product_id",
        readonly=True,
        store=False,
    )
    cabinet_code = fields.Char(
        string="Cabinet",
        related="workorder_id.x_sbk_cabinet_code",
        readonly=True,
        store=False,
    )
    kitchen_room = fields.Char(
        string="Room",
        related="workorder_id.x_sbk_kitchen_room",
        readonly=True,
        store=False,
    )

    title = fields.Char(
        string="Issue Summary",
        required=True,
        help="One-line description engineering will see in the ECO "
             "list. Be specific — 'CNC drill hole 4mm off centre on "
             "wall cab' beats 'drill problem'.",
    )
    description = fields.Text(
        string="What happened?",
        required=True,
        help="Full narrative. What were you doing, what went wrong, "
             "what's the suspected root cause?",
    )
    severity = fields.Selection(
        SEVERITY_SELECTION,
        string="Severity",
        required=True,
        default="medium",
    )
    photo = fields.Binary(
        string="Photo (optional)",
        attachment=True,
        help="Snap a picture of the defect. Posted to the ECO chatter "
             "and visible to engineering when they triage.",
    )
    photo_filename = fields.Char(string="Photo Filename")
    mi_check_id = fields.Many2one(
        "southbrook.mi.check",
        string="Originating MI Check (optional)",
        domain="[('x_sbk_workorder_id', '=', workorder_id)]",
        help="If this defect was already logged as an MI check on the "
             "WO, pick it here so the ECO links back to it.",
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        wo_id = (
            self.env.context.get("default_workorder_id")
            or self.env.context.get("active_id")
        )
        if wo_id and (self.env.context.get("active_model") == "mrp.workorder"
                      or "default_workorder_id" in self.env.context):
            vals["workorder_id"] = wo_id
        return vals

    # ------------------------------------------------------------------
    # ECO target model resolution
    # ------------------------------------------------------------------
    def _resolve_eco_model(self):
        """Return (model_name, used_fallback_to_southbrook).

        Prefer pg.eco (newer/native) when present, fall back to
        southbrook.eco. The bool in the tuple is True when we landed
        on the legacy southbrook.eco path so the caller can record it
        on the WO chatter.
        """
        if "pg.eco" in self.env:
            return "pg.eco", False
        if "southbrook.eco" in self.env:
            return "southbrook.eco", True
        raise UserError(_(
            "No ECO model available — neither pg.eco nor southbrook.eco "
            "is installed. Install southbrook_plm (the legacy ECO model) "
            "or a product_graph_eco addon and try again."
        ))

    def _build_description_block(self):
        """Pre-pended context block embedded in the ECO description."""
        self.ensure_one()
        wo = self.workorder_id
        bits = [
            "<p><strong>%s</strong></p>" % _("Reported from the shop floor (W034)"),
            "<ul>",
            "<li><strong>%s:</strong> %s</li>" % (
                _("Work Order"), wo.display_name or wo.id),
            "<li><strong>%s:</strong> %s</li>" % (
                _("Manufacturing Order"),
                (wo.production_id.display_name if wo.production_id else "—")),
            "<li><strong>%s:</strong> %s</li>" % (
                _("Work Center"),
                (wo.workcenter_id.display_name if wo.workcenter_id else "—")),
            "<li><strong>%s:</strong> %s</li>" % (
                _("Product"),
                (wo.product_id.display_name if wo.product_id else "—")),
        ]
        if wo.x_sbk_cabinet_code:
            bits.append("<li><strong>%s:</strong> %s</li>" % (
                _("Cabinet"), wo.x_sbk_cabinet_code))
        if wo.x_sbk_kitchen_room:
            bits.append("<li><strong>%s:</strong> %s</li>" % (
                _("Room"), wo.x_sbk_kitchen_room))
        if self.mi_check_id:
            bits.append("<li><strong>%s:</strong> %s</li>" % (
                _("Originating MI Check"), self.mi_check_id.display_name))
        bits.append("<li><strong>%s:</strong> %s</li>" % (
            _("Reported by"), self.env.user.display_name))
        bits.append("</ul>")
        bits.append("<p><strong>%s</strong></p>" % _("Operator narrative"))
        # description is plain Text; wrap in <pre> so newlines survive
        # the HTML field on southbrook.eco.
        bits.append("<pre>%s</pre>" % (self.description or ""))
        return "".join(bits)

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------
    def action_submit(self):
        self.ensure_one()
        if not self.workorder_id:
            raise UserError(_(
                "No WorkOrder bound to this wizard — open it from a WO form."))

        model_name, used_southbrook_fallback = self._resolve_eco_model()
        eco_vals = self._prepare_eco_vals(model_name)

        # Create the ECO (draft per spec). sudo() is required: this wizard
        # is exposed to mrp.group_mrp_user (shop floor) and southbrook.eco's
        # access rows are scoped to southbrook_plm.group_southbrook_plm_user
        # / _approver only (see southbrook_plm/security/ir.model.access.csv)
        # — an operator has ZERO access rows on the model itself.
        Eco = self.env[model_name].sudo()
        eco = Eco.create(eco_vals)

        # Attach photo (if any) + post the WO back-link to ECO chatter.
        # This runs on the sudo'd `eco` recordset, so it doesn't need the
        # operator to have ECO access either.
        self._post_to_eco_chatter(eco)

        # Post a notice on the WO chatter pointing at the new ECO so the
        # operator + planners see the audit trail without leaving the WO.
        # This is the traceable record the operator CAN actually read —
        # see the return value below for why we don't redirect into the
        # ECO itself.
        self._post_to_wo_chatter(eco, model_name)

        _logger.info(
            "W034: ECO %s created (model=%s, fallback=%s) from WO %s by %s",
            eco.id, model_name, used_southbrook_fallback,
            self.workorder_id.id, self.env.user.login,
        )

        # Do NOT redirect into the ECO form (as this used to). southbrook.eco
        # grants read/write/create ONLY to southbrook_plm.group_
        # southbrook_plm_user / _approver — a plain mrp.group_mrp_user shop-
        # floor operator has no access row on the model at all. Returning an
        # act_window onto a record the operator's own (non-sudo) session
        # cannot read raised an AccessError immediately after a successful
        # save: the operator saw a crash and had no way to tell whether
        # their report was actually filed (2026-08-02 blindspot pass).
        #
        # Fix: close the wizard and hand back a toast confirmation naming
        # the ECO instead. The WO chatter note posted above is the durable,
        # operator-readable record carrying the actual link — this
        # notification is purely "yes, it worked" feedback for the person
        # who just clicked Submit.
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Engineering issue reported"),
                "message": _("%(eco)s created — %(title)s") % {
                    "eco": eco.display_name or eco.name,
                    "title": self.title,
                },
                "type": "success",
                "sticky": False,
            },
        }

    def _prepare_eco_vals(self, model_name):
        """Build the create vals dict, branching per target model.

        Both models accept `title` + `description`; `southbrook.eco`
        also wants `eco_type_id` (required, restrict ondelete) and
        respects `stage_id` default via _default_stage. We pin
        target_kind = document via eco_type_document so the ECO is
        record-only and apply won't mutate a BoM/cut-spec without
        explicit engineering re-typing.

        For pg.eco we pass a minimal vals dict; the future addon's
        defaults are expected to do the right thing. If pg.eco rejects
        a key the caller will surface the ORM error to the operator.
        """
        self.ensure_one()
        vals = {
            "title": self.title,
            "description": self._build_description_block(),
            "user_id": self.env.user.id,
            "priority": _SEVERITY_TO_ECO_PRIORITY.get(self.severity, "1"),
        }
        if model_name == "southbrook.eco":
            # Pin to the document type — record-only on apply, so a
            # draft ECO that nobody reviews never mutates a BoM.
            try:
                doc_type = self.env.ref(
                    "southbrook_plm.eco_type_document",
                    raise_if_not_found=False,
                )
            except Exception:  # noqa: BLE001
                doc_type = False
            if not doc_type:
                # Fall back to the first available type so create
                # doesn't blow up on the required FK.
                doc_type = self.env["southbrook.eco.type"].search([], limit=1)
            if not doc_type:
                raise UserError(_(
                    "No southbrook.eco.type configured — cannot draft "
                    "an ECO. Install southbrook_plm demo or seed at "
                    "least one ECO type."))
            vals["eco_type_id"] = doc_type.id
        return vals

    def _post_to_eco_chatter(self, eco):
        """Attach the photo (if any) + a WO back-link tag in the
        ECO's chatter so the engineer sees the photo on open."""
        self.ensure_one()
        body_lines = [
            "<p>%s</p>" % _("Filed from WO %(wo)s by %(user)s.") % {
                "wo": self.workorder_id.display_name,
                "user": self.env.user.display_name,
            },
        ]
        attachments = []
        if self.photo:
            attachments.append((
                self.photo_filename or "wo_issue_photo.jpg",
                self.photo,  # binary; message_post handles base64
            ))
        eco.message_post(
            body="".join(body_lines),
            attachments=attachments,
            subtype_xmlid="mail.mt_note",
        )

    def _post_to_wo_chatter(self, eco, model_name):
        """Post a back-link on the WO so the operator + planners can
        click through to the ECO they just raised.

        This is the ONLY record of the new ECO that the reporting
        operator can actually read afterwards — mrp.group_mrp_user has
        no access rows on southbrook.eco, but mrp.workorder chatter is
        theirs to read (this module restores mail.thread onto
        mrp.workorder specifically so this call works; see the class
        docstring above). action_submit no longer redirects into the
        ECO form for exactly that reason (2026-08-02 blindspot pass) —
        this note plus the ECO name/title is the traceable link."""
        self.ensure_one()
        link = (
            '<a href="#" data-oe-model="%(m)s" data-oe-id="%(i)s">%(n)s</a>'
        ) % {
            "m": model_name,
            "i": eco.id,
            "n": eco.display_name or eco.name or _("ECO"),
        }
        self.workorder_id.message_post(
            body=_(
                "Engineering change reported: %(link)s — %(title)s") % {
                    "link": link,
                    "title": self.title,
            },
            subtype_xmlid="mail.mt_note",
        )


class MrpWorkorder(models.Model):
    _inherit = "mrp.workorder"

    def action_sbk_raise_eco_wizard(self):
        """Button on the WO form opens the Report-Engineering-Issue
        wizard pre-bound to this WO."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Report Engineering Issue"),
            "res_model": "southbrook.wo.raise.eco.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_workorder_id": self.id,
                "active_id": self.id,
                "active_model": "mrp.workorder",
            },
        }
