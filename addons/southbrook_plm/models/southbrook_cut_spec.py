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
from odoo.exceptions import UserError, ValidationError

from .plm_lifecycle import is_plm_lifecycle_authorised

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

    # W079 (R4.S8) — auto-incrementing version stamp on the
    # parametric cut spec.
    #
    # JTBD: "Two cabinets built under different cut specs are
    # distinguishable in warranty." The asbuilt record snapshots
    # this version at seal time (see x_sbk_cut_spec_version_at_build
    # on southbrook.asbuilt) so a 3-year-later warranty trace
    # answers "what reveal / thickness was this cabinet cut to?"
    # without forensic reconstruction.
    #
    # Increment trigger (write() override below): the version
    # bumps when ANY field in CONSTANT_FIELDS changes. Chatter
    # posts, name edits, state transitions, note edits, and the
    # active flag do NOT bump — those are bookkeeping, not
    # geometric truth. This matches the spec's "significant
    # fields" constraint and avoids one-bump-per-chatter-post.
    version = fields.Integer(
        string="Version",
        default=1,
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
        help="Auto-increments on every write that changes a "
             "geometric constant (box_th / back_th / rabbet / "
             "door_th / door_reveal / shelf_tol / shelf_vent_gap / "
             "toekick_h). Non-geometric edits (name, note, state, "
             "active) do NOT bump the version. The asbuilt record "
             "snapshots this value at seal time so warranty trace "
             "can answer 'what cut spec produced this cabinet'.",
    )

    def _plm_lifecycle_authorised(self):
        """Identity-checked lifecycle gate — see plm_lifecycle.py (FIX 1,
        2026-08-02 blindspot pass) for the full JSON-RPC-forgery rationale.
        Duplicated as a one-liner on southbrook.eco too (the two models
        don't share a base class); both delegate to the same module-level
        ``is_plm_lifecycle_authorised()`` so the actual ``is`` comparison
        is written exactly once.
        """
        return is_plm_lifecycle_authorised(self.env)

    def write(self, vals):
        # FIX 6 (2026-08-02 blindspot pass): hand-editing `state` is
        # exactly what this model's own docstring forbids — "Activation is
        # performed by southbrook.eco.action_apply ... never by hand-
        # editing state" — but nothing actually enforced that before this
        # pass. ir.model.access.csv gives PLM Approver perm_write on this
        # model (they need to author/edit draft specs), which also meant
        # perm_write to set state='active' directly on a bare write() /
        # RPC call: a live geometry change with ZERO ECO trail, the exact
        # failure mode action_activate's own new gate (below) closes for
        # the button/RPC path into action_activate. This closes the same
        # hole for a DIRECT write of `state`, bypassing action_activate
        # altogether.
        #
        # Deliberately NOT gated here:
        #   - `active` (archiving a draft/superseded spec is ordinary
        #     housekeeping, not a lifecycle event — ondelete/archive still
        #     works unauthorised, as it must).
        #   - CONSTANT_FIELDS / the W079 version-bump path above: a DRAFT
        #     spec is freely editable while it's being authored (that's
        #     the whole workflow — propose values, then raise the ECO that
        #     activates them); gating `state` alone is sufficient because
        #     a draft can't affect the live cut math until activation.
        if "state" in vals and not self._plm_lifecycle_authorised():
            raise UserError(
                _(
                    "Cut spec 'state' cannot be hand-edited — activation "
                    "runs only through action_activate, which is itself "
                    "reachable only via an approved cut-spec Engineering "
                    "Change Order. Raise or apply an ECO instead."
                )
            )
        # W079 — bump version when a CONSTANT_FIELDS value actually
        # changes. We compare per-record to avoid bumping when the
        # caller writes the same value (idempotent write should be a
        # no-op for the version). Cheap because CONSTANT_FIELDS is 8
        # primitives.
        bumped_ids = set()
        for fname in CONSTANT_FIELDS:
            if fname not in vals:
                continue
            new = vals[fname]
            for rec in self:
                if rec[fname] != new:
                    bumped_ids.add(rec.id)
        result = super().write(vals)
        if bumped_ids and "version" not in vals:
            # Re-fetch to use the post-super state; super-call may
            # have validated / mutated. Then a single grouped write
            # for the bumped subset.
            for rec in self.browse(list(bumped_ids)):
                # Direct write of the integer; recursion is not a
                # concern because version is NOT in CONSTANT_FIELDS
                # and the trigger above only fires on CONSTANT_FIELDS
                # keys.
                super(SouthbrookCutSpec, rec).write({
                    "version": (rec.version or 1) + 1,
                })
        return result

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

    # ------------------------------------------------------------------
    # Delete guard
    # ------------------------------------------------------------------
    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft(self):
        # Active and superseded specs are geometry history: the asbuilt
        # record snapshots a spec's version at seal time (see the W079
        # rationale on the `version` field above) so a warranty claim years
        # later can answer "what reveal / thickness was this cabinet cut
        # to?" — deleting the spec that answer depends on defeats that
        # entirely. Only a draft (never activated, no downstream asbuilt
        # could possibly reference it) is safe to delete outright; retiring
        # an active/superseded spec goes through `active = False`
        # (archive), which keeps the row and its version number intact.
        for spec in self:
            if spec.state != "draft":
                raise UserError(
                    _(
                        "Cut spec %s is %s — active/superseded specs are "
                        "geometry history needed for warranty trace and "
                        "cannot be deleted. Archive it instead (set "
                        "Active = False)."
                    )
                    % (spec.display_name, spec.state)
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

        FIX 6 (2026-08-02 blindspot pass): this is a bare public method, so
        RPC ignores the form's Activate-button restrictions, and any PLM
        Approver's perm_write on this model let them call it directly —
        producing a live, geometry-changing spec activation with ZERO ECO
        trail, contradicting this whole model's reason for existing (see
        the module docstring: "a shop lead can revise a reveal or a panel
        thickness through an approved ECO ... never by hand-editing
        state"). Gated on the same lifecycle sentinel southbrook.eco uses
        for its own terminal-write authorisation: only
        southbrook.eco._apply_cut_spec — which calls this with
        ``with_context(plm_lifecycle=PLM_LIFECYCLE)`` — may reach this
        line. A direct call (button, RPC, shell) without that context is
        refused and pointed at raising a cut-spec ECO instead.
        """
        self.ensure_one()
        if not self._plm_lifecycle_authorised():
            raise UserError(
                _(
                    "Cut spec %s cannot be activated directly — activation "
                    "must be reached through an approved cut-spec "
                    "Engineering Change Order (target kind 'Cut Spec'), "
                    "not by calling Activate directly. A direct activation "
                    "would change live cut geometry with no ECO trail."
                )
                % self.display_name
            )
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
