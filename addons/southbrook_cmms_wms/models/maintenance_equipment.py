# SPDX-License-Identifier: LGPL-3.0-only
from odoo import api, fields, models

DEFAULT_MTBF_PARAM = "southbrook_cmms_wms.default_expected_mtbf"


class MaintenanceEquipment(models.Model):
    """Reliability targets, and an honest account of where each one came from.

    `expected_mtbf` ships empty on every machine. Everything that reads it —
    "vs Expected" columns, "% of target" badges, below-target filters — is
    therefore comparing against nothing. Rather than seed a plausible number
    (a fabricated target is indistinguishable from a measured one once it is
    in the field), the target stays empty and its ABSENCE is made visible.
    """

    _inherit = "maintenance.equipment"

    sb_effective_expected_mtbf = fields.Float(
        string="Expected MTBF (effective)",
        compute="_compute_sb_expected_mtbf",
        help="The target actually used in comparisons: this machine's own "
             "figure, else the plant default, else nothing.",
    )
    sb_expected_mtbf_source = fields.Selection(
        [
            ("equipment", "Set on this machine"),
            ("default", "Plant default"),
            ("none", "Not set"),
        ],
        string="Target source",
        compute="_compute_sb_expected_mtbf",
        help="Never let a reader mistake a fallback for a measurement.",
    )
    sb_reliability_target_missing = fields.Boolean(
        string="No reliability target",
        compute="_compute_sb_expected_mtbf",
        search="_search_target_missing",
    )

    @api.model
    def _sb_default_expected_mtbf(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(DEFAULT_MTBF_PARAM)
        try:
            return max(0.0, float(raw or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @api.depends("expected_mtbf")
    def _compute_sb_expected_mtbf(self):
        # The plant default lives in ir.config_parameter, which the ORM cannot
        # add to @api.depends — so this stays non-stored and re-reads.
        fallback = self._sb_default_expected_mtbf()
        for rec in self:
            if rec.expected_mtbf:
                rec.sb_effective_expected_mtbf = float(rec.expected_mtbf)
                rec.sb_expected_mtbf_source = "equipment"
            elif fallback:
                rec.sb_effective_expected_mtbf = fallback
                rec.sb_expected_mtbf_source = "default"
            else:
                rec.sb_effective_expected_mtbf = 0.0
                rec.sb_expected_mtbf_source = "none"
            rec.sb_reliability_target_missing = not rec.sb_effective_expected_mtbf

    def _search_target_missing(self, operator, value):
        if operator not in ("=", "!="):
            return []
        missing = self.search([]).filtered(
            lambda e: not e.sb_effective_expected_mtbf)
        wants_missing = (operator == "=") == bool(value)
        return [("id", "in" if wants_missing else "not in", missing.ids)]
