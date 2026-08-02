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
from odoo.exceptions import ValidationError


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

    # ------------------------------------------------------------------
    # Version collision guard
    # ------------------------------------------------------------------
    @api.constrains(
        "active", "southbrook_version", "product_tmpl_id", "product_id", "type"
    )
    def _check_southbrook_version_unique(self):
        # Backstops the "one active BoM per (template, version)" invariant
        # that southbrook.eco._apply_bom relies on. _apply_bom's FOR UPDATE
        # lock already prevents two CONCURRENT applies from racing to the
        # same version number, and as of the 2026-08-01 hardening pass it
        # computes the next version from the max across ALL rows (active or
        # archived) for the template rather than old_version + 1 — but this
        # constraint is the backstop for every OTHER path that could still
        # mint a collision: a manual DB fix, a data import, a copy() called
        # outside _apply_bom, a second module writing southbrook_version
        # directly.
        #
        # Deliberately scoped to southbrook_version > 1: version-1 is the
        # ordinary, un-ECO'd starting point, and the configurator/catalog
        # layer (southbrook_kitchen_mrp's mrp_bom_catalog.py) mass-generates
        # MANY version-1 BoMs per template as auto-seeded variant stubs
        # (code prefix "CatalogAutoBOM-*") — those are expected to coexist
        # at version 1 across different product_id variants of the same
        # template and are NOT what this guard protects against. Only
        # ECO-versioned heads (v >= 2, i.e. a version an ECO actually
        # produced via _apply_bom) are constrained here.
        for bom in self:
            if not bom.active or bom.southbrook_version <= 1:
                continue
            dupes = self.search_count(
                [
                    ("id", "!=", bom.id),
                    ("active", "=", True),
                    ("product_tmpl_id", "=", bom.product_tmpl_id.id),
                    ("product_id", "=", bom.product_id.id),
                    ("type", "=", bom.type),
                    ("southbrook_version", "=", bom.southbrook_version),
                ]
            )
            if dupes:
                raise ValidationError(
                    _(
                        "BoM %(name)s: Southbrook version %(v)s already "
                        "exists on another active BoM for the same "
                        "template / product / type. Versions must be "
                        "unique among active heads (v >= 2)."
                    )
                    % {"name": bom.display_name, "v": bom.southbrook_version}
                )

    @api.model
    def _get_cut_constants(self, cut_spec=None):
        """Override the estimating seam to read the active cut spec.

        Falls back to the estimating code defaults (super) when no spec is
        active, so behaviour is identical to a spec-less install.

        :param cut_spec: optional, explicit southbrook.cut.spec recordset.
            When truthy, it is used directly and ``_get_active()`` is NOT
            consulted at all — a caller that already resolved a specific
            spec (e.g. southbrook_kitchen_mrp's as-sold pin resolution)
            wants exactly that spec's constants, not whatever happens to
            be active today. When falsy/omitted, behaviour is completely
            unchanged from before this parameter existed: the active
            spec, or the estimating code defaults via super() when none
            is active.

        Sudo on the cut.spec read because this seam is called during
        BoM panel-dimension computation for ANY user that touches a
        BoM — including portal customers fetching their order payload
        via /southbrook/api/order/<id>. Cut specs are PLM-engineer
        data (ir.model.access.csv restricts write to PLM Approver and
        read to PLM User), so a portal user hits AccessError without
        sudo here. The customer never sees the spec values directly;
        they only observe the derived panel dimensions on their BoM
        preview tab. (Bug found 2026-06-01 by smoke_customer_flow.sh:
        portal AccessError on order payload fetch after submit.)
        """
        if cut_spec:
            return cut_spec.sudo().constants_dict()
        spec = self.env["southbrook.cut.spec"].sudo()._get_active()
        if spec:
            return spec.constants_dict()
        return super()._get_cut_constants()
