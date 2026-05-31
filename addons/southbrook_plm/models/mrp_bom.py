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
from odoo import api, fields, models


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    southbrook_version = fields.Integer(
        string="Southbrook Version",
        default=1,
        copy=False,
        help="Incremented by an Engineering Change Order each time this "
        "template BoM is re-versioned. The prior version is archived.",
    )

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
