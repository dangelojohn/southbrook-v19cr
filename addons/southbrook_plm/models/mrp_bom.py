# SPDX-License-Identifier: LGPL-3.0-only
"""mrp.bom extension for Southbrook PLM.

Two concerns:

1. **Versioning** — adds ``southbrook_version`` so an ECO of kind 'bom' can
   copy a template BoM to a new version and archive the prior one
   (southbrook.eco._apply_bom).

2. **The cut-spec seam override** — ``southbrook_estimating`` reads its NF14
   geometric constants through ``_get_cut_constants()`` (a seam it defines so
   it can run standalone, returning the code defaults). This module overrides
   that seam to return the *active* ``southbrook.cut.spec`` instead, so the
   panel-cut math is driven by ECO-approved data rather than module constants.
   When no active spec exists, we defer to ``super()`` (the code defaults), so
   installing this module never silently changes cut output until a spec is
   activated.
"""
from odoo import _, api, fields, models


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    southbrook_version = fields.Integer(
        string="Southbrook Version",
        default=1,
        copy=False,
        help="Incremented by an Engineering Change Order each time this "
        "template BoM is re-versioned. The prior version is archived.",
    )
    # Two One2many surfaces — both feed the smart-button count + the
    # filtered list view (action_open_southbrook_ecos). The combined
    # count tells a shop lead "everything ECO-related touching this
    # BoM, in either direction":
    #
    #   southbrook_eco_ids          — ECOs RAISED AGAINST this BoM
    #                                  (inverse of southbrook.eco.bom_id).
    #                                  The target side: "what's being
    #                                  changed about this BoM."
    #
    #   southbrook_eco_history_ids  — ECOs that PRODUCED this BoM
    #                                  (inverse of southbrook.eco.new_bom_id).
    #                                  The provenance side: "where did
    #                                  this BoM version come from."
    #
    # Without the second inverse, a freshly-versioned BoM (the new
    # active one created by _apply_bom) shows count=0 on its form,
    # making the audit trail effectively unreachable from the active
    # BoM — you'd have to find the archived prior version to see its
    # smart button. Bug found by demo walkthrough 2026-06-01.
    southbrook_eco_ids = fields.One2many(
        "southbrook.eco",
        "bom_id",
        string="Engineering Change Orders",
    )
    southbrook_eco_history_ids = fields.One2many(
        "southbrook.eco",
        "new_bom_id",
        string="Engineering Change Orders (provenance)",
    )
    southbrook_eco_count = fields.Integer(
        compute="_compute_southbrook_eco_count",
        string="ECO Count",
    )

    def _compute_southbrook_eco_count(self):
        for bom in self:
            # Union (|) handles the rare case where the same ECO
            # references this BoM as both bom_id AND new_bom_id —
            # we want to count it once.
            bom.southbrook_eco_count = len(
                bom.southbrook_eco_ids | bom.southbrook_eco_history_ids
            )

    def action_open_southbrook_ecos(self):
        """Smart-button action: ECOs touching this BoM in either direction.

        Opens the southbrook.eco list view filtered to ECOs where bom_id
        OR new_bom_id matches this BoM. The default-create context
        pre-sets bom_id (target side) and the bom-kind eco_type — that's
        the new-ECO use case, distinct from clicking through to read
        existing audit trail.
        """
        self.ensure_one()
        bom_kind_type = self.env["southbrook.eco.type"].search(
            [("target_kind", "=", "bom")], limit=1
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Engineering Change Orders"),
            "res_model": "southbrook.eco",
            "view_mode": "list,form,kanban",
            "domain": [
                "|",
                ("bom_id", "=", self.id),
                ("new_bom_id", "=", self.id),
            ],
            "context": {
                "default_bom_id": self.id,
                "default_eco_type_id": bom_kind_type.id if bom_kind_type else False,
            },
        }

    @api.model
    def _get_cut_constants(self):
        """Override the estimating seam to read the active cut spec.

        Falls back to the estimating code defaults (super) when no spec is
        active, so behaviour is identical to a spec-less install.
        """
        spec = self.env["southbrook.cut.spec"]._get_active()
        if spec:
            return spec.constants_dict()
        return super()._get_cut_constants()
