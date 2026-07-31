# -*- coding: utf-8 -*-
"""Backfill `wc_type`, so labour stops posting to the Machine Run account.

WHY THIS MATTERS. `wc_type` has exactly one consumer anywhere —
mrp_product_costing/models/mrp_workorder.py, which does:

    if workcenter.wc_type == "H":   variable_account = labour_cost_account
    else:                          variable_account = machine_run_cost_account

It was NULL on 13 of 14 work centres, so the `else` fired on nearly every work order and
labour has been posting to the Machine Run account across almost the whole shop. That is a
live financial-accuracy defect, and it was found while tracing why a work-centre form would
not save — it is not in the Kitchen Ops audit at all.

WHY THE MAPPING IS PER WORK CENTRE, NOT PER STATION TYPE. Asked whether `sanding` and
`finishing` were Man or Machine, the honest answer came back "both are plausible". For
`finishing` that is not indecision — it is correct, because `finishing` covers TWO
different stations that genuinely differ:

    PAINT  Paint Booth      operator stands there and sprays        -> H (Man)
    CURE   Cure/Dry Room    an oven runs unattended                 -> M (Machine)

A station-type mapping cannot express that; a per-work-centre mapping can. `sanding` has
only one station, Sanding Prep, and prep sanding in a cabinet shop is hand and orbital work
rather than a wide-belt line, so it is Man.

WHAT THIS DELIBERATELY DOES NOT TOUCH. Only NULLs are filled. SB-EDGE (Edge Bander) already
carries 'H' — a value a human set. An edge bander is a machine, so 'H' looks wrong and its
cost is posting to labour, but overwriting somebody's explicit entry on a financial field
is not a migration's job. It is logged as a warning for a human to confirm.
"""

import logging

_logger = logging.getLogger(__name__)

# H = Man (labour account) · M = Machine (machine-run account)
WC_TYPE_BY_CODE = {
    "ENG01":       ("H", "design review — desk work, no machine time"),
    "SB-SAW":      ("M", "panel saw / CNC nesting — machine-time dominant"),
    "SB-CNC-BORE": ("M", "CNC boring — machine-time dominant"),
    "CNC02":       ("M", "CNC router — machine-time dominant"),
    "DOOR-SHOP":   ("M", "tagged station=cnc — machine-time dominant"),
    "SAND":        ("H", "sanding PREP — hand and orbital, not a wide-belt line"),
    "PAINT":       ("H", "paint booth — operator-driven spraying"),
    "CURE":        ("M", "cure/dry room — an oven running unattended"),
    "SB-ASSY":     ("H", "carcass assembly — manual"),
    "SB-DOOR":     ("H", "door hanging — manual"),
    "SB-HW":       ("H", "hardware fitting — manual"),
    "SB-QC":       ("H", "quality inspection — manual"),
    "SB-PACK":     ("H", "pack and label — manual"),
    # SB-EDGE deliberately absent — see the docstring.
}


def migrate(cr, version):
    if not version:
        return

    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    Workcenter = env["mrp.workcenter"].with_context(active_test=False)

    if "wc_type" not in Workcenter._fields:
        _logger.warning(
            "southbrook_workcenter_type_compat: wc_type is not in the registry — "
            "mrp_product_costing is not installed here. Nothing to backfill.")
        return

    filled, skipped = 0, []
    for code, (wc_type, why) in WC_TYPE_BY_CODE.items():
        wc = Workcenter.search([("code", "=", code)], limit=1)
        if not wc:
            continue
        if wc.wc_type:
            # Someone set this deliberately. Leave it and say so.
            skipped.append("%s already %s" % (code, wc.wc_type))
            continue
        wc.wc_type = wc_type
        filled += 1
        _logger.info("wc_type: %s -> %s (%s)", code, wc_type, why)

    remaining = Workcenter.search([("wc_type", "=", False)])
    _logger.info(
        "southbrook_workcenter_type_compat: filled wc_type on %s work centre(s); "
        "skipped %s; %s still unset",
        filled, skipped or "none", len(remaining))

    # The one value a human set, which looks wrong. Flagged, never overwritten.
    edge = Workcenter.search([("code", "=", "SB-EDGE")], limit=1)
    if edge and edge.wc_type == "H":
        _logger.warning(
            "wc_type: SB-EDGE (Edge Bander) is set to 'H' (Man), so its cost posts to the "
            "LABOUR account. An edge bander is a machine. This value was set by hand and "
            "has NOT been changed — confirm with finance whether it should be 'M'.")
    if remaining:
        _logger.warning(
            "wc_type: still unset on %s — their work-order cost defaults to the MACHINE "
            "RUN account: %s", len(remaining), remaining.mapped("code"))
