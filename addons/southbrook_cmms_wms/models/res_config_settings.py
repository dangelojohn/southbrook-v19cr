# SPDX-License-Identifier: LGPL-3.0-only
from odoo import fields, models

from .maintenance_equipment import DEFAULT_MTBF_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sb_default_expected_mtbf = fields.Float(
        string="Default expected MTBF",
        config_parameter=DEFAULT_MTBF_PARAM,
        help="Fallback target in hours, used only for machines with no figure "
             "of their own. Leave at zero to compare against nothing until a "
             "real target is entered — machines with no target then read "
             "'Not set' rather than showing a made-up percentage.",
    )
