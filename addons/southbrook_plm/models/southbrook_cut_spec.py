# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.cut.spec — the ECO-governed parametric cut specification.

This model promotes the NF14 geometric constants out of
``southbrook_estimating/models/mrp_bom.py`` (where they live as module-level
Python constants) into versioned database records. The estimating panel-cut
math reads the *active* spec through the ``_get_cut_constants()`` seam (see
``mrp_bom.py`` in this module), so a shop lead can revise a reveal or a panel
thickness through an approved ECO without a code deploy.

Lifecycle:
    draft  -> a candidate spec authored with proposed values; not yet live.
    active -> exactly one record; the values the cut math currently reads.
    superseded -> a previously-active spec, retained for the audit trail.

Activation is performed by ``southbrook.eco.action_apply`` for ECOs of
target_kind == 'cut_spec' (it calls :meth:`action_activate`), never by
hand-editing ``state``.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Field name -> the _get_cut_constants() dict key it maps to. Single source
# of truth for both the seam dict and any future migration script. Keeping
# the names identical to the estimating module constants (lower-cased) makes
# the mapping mechanical.
CONSTANT_FIELDS = (
    "box_th",
    "back_th",
    "rabbet",
    "door_th",
    "door_reveal",
    "shelf_tol",
    "shelf_vent_gap",
    "toekick_h",
)


class SouthbrookCutSpec(models.Model):
    _name = "southbrook.cut.spec"
    _description = "Southbrook Parametric Cut Specification"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        required=True,
        default=lambda self: _("New Cut Spec"),
        tracking=True,
    )
    # Inverse of southbrook.eco.cut_spec_id. Powers the smart-button
    # count + click-through on the cut-spec form: which ECOs proposed
    # this spec (i.e. carry it as their candidate)? An applied ECO of
    # target_kind='cut_spec' is what activated this spec; subsequent
    # supersession is recorded via the chatter rather than a separate
    # field, so the eco_ids list is the canonical audit trail for
    # "where did this spec come from?"
    southbrook_eco_ids = fields.One2many(
        "southbrook.eco",
        "cut_spec_id",
        string="Engineering Change Orders",
    )
    southbrook_eco_count = fields.Integer(
        compute="_compute_southbrook_eco_count",
        string="ECO Count",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("superseded", "Superseded"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    active = fields.Boolean(
        default=True,
        help="Archived (active=False) specs are hidden but retained for audit.",
    )
    note = fields.Text(
        help="Provenance — which workbook revision / measurement these values "
        "came from. NF14 reminder: the seed spec values are ASSUMED until the "
        "canonical #8 workbook lands.",
    )

    # ---- The promoted NF14 constants (all millimetres). ----
    box_th = fields.Float(
        "Box / Carcass Thickness (mm)", default=15.875, required=True,
        help="Carcass material thickness — 5/8\" melamine standard.")
    back_th = fields.Float(
        "Back-Panel Thickness (mm)", default=6.35, required=True,
        help="Back panel material — 1/4\" hardboard.")
    rabbet = fields.Float(
        "Rabbet Depth (mm)", default=6.35, required=True,
        help="Back-panel capture groove routed into sides/top/bottom.")
    door_th = fields.Float(
        "Door Thickness (mm)", default=18.0, required=True,
        help="Door / drawer-front thickness — 3/4\" slab or 5-piece.")
    door_reveal = fields.Float(
        "Door Reveal (mm)", default=3.0, required=True,
        help="Uniform gap on all four door edges.")
    shelf_tol = fields.Float(
        "Shelf Tolerance (mm)", default=1.5, required=True,
        help="Hand-placement clearance subtracted from inside width.")
    shelf_vent_gap = fields.Float(
        "Shelf Ventilation Gap (mm)", default=12.7, required=True,
        help="1/2\" subtracted from depth at the back.")
    toekick_h = fields.Float(
        "Toe-Kick Height (mm)", default=101.6, required=True,
        help="4\" standard, integrated into side panels for base/sink/tall/vanity.")

    @api.constrains(*CONSTANT_FIELDS)
    def _check_positive(self):
        for spec in self:
            for fname in CONSTANT_FIELDS:
                if spec[fname] <= 0:
                    raise ValidationError(
                        _("Cut-spec value '%s' must be greater than zero.")
                        % spec._fields[fname].string
                    )

    @api.constrains("state")
    def _check_single_active(self):
        # Portable equivalent of a partial-unique index (avoids the btree_gist
        # dependency an EXCLUDE constraint would need). action_activate keeps
        # this invariant; the constraint is the backstop for manual edits.
        if self.filtered(lambda s: s.state == "active"):
            actives = self.search_count([("state", "=", "active")])
            if actives > 1:
                raise ValidationError(
                    _("Only one cut spec may be Active at a time.")
                )

    @api.model
    def _get_active(self):
        """Return the single active cut spec, or an empty recordset.

        The cut-math seam (mrp_bom._get_cut_constants) calls this; when no
        active spec exists (e.g. fresh install before seed, or all superseded)
        it returns empty and the seam falls back to the code defaults.
        """
        return self.search([("state", "=", "active")], limit=1)

    def _compute_southbrook_eco_count(self):
        for spec in self:
            spec.southbrook_eco_count = len(spec.southbrook_eco_ids)

    def action_open_southbrook_ecos(self):
        """Smart-button action: ECOs that reference this cut spec.

        Opens the southbrook.eco list filtered to this spec, with the
        default-create context pre-set so 'New' from this view auto-
        binds the candidate spec + cut-kind ECO type.
        """
        self.ensure_one()
        cut_kind_type = self.env["southbrook.eco.type"].search(
            [("target_kind", "=", "cut_spec")], limit=1
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Engineering Change Orders"),
            "res_model": "southbrook.eco",
            "view_mode": "list,form,kanban",
            "domain": [("cut_spec_id", "=", self.id)],
            "context": {
                "default_cut_spec_id": self.id,
                "default_eco_type_id": cut_kind_type.id if cut_kind_type else False,
            },
        }

    def constants_dict(self):
        """Return this spec as the dict shape the cut math consumes."""
        self.ensure_one()
        return {fname: self[fname] for fname in CONSTANT_FIELDS}

    def action_activate(self):
        """Make this spec the active one, superseding the current active spec.

        Called by southbrook.eco.action_apply. Idempotent for an already-active
        spec. Supersedes (does not delete) the prior active spec.
        """
        self.ensure_one()
        if self.state == "active":
            return True
        prior = self._get_active()
        if prior and prior != self:
            prior.write({"state": "superseded"})
            prior.message_post(
                body=_("Superseded by cut spec %s.") % self.display_name
            )
        self.write({"state": "active"})
        self.message_post(body=_("Activated as the live cut specification."))
        return True
