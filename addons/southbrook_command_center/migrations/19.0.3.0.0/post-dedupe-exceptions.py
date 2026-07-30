# SPDX-License-Identifier: LGPL-3.0-only
"""Collapse the duplicate-exception backlog created by the keying bug.

Measured on production 2026-07-30: 38,910 open southbrook.command.exception
rows tracing to 9 manufacturing orders and 17 live southbrook.mi.check rows,
growing ~2,600/day. Cause: the uniqueness rule keyed on the mi.check row id,
which the MI sweep cron recreates every 15 minutes.

This elects one survivor per live MI condition, keys it, and archives the rest
with a NULL dedupe_key (NULLs do not collide in a unique index, so archived
rows stay in the table and stay auditable). Reversible: nothing is deleted.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Exc = env["southbrook.command.exception"].with_context(active_test=False)

    before = Exc.search_count([("source_model", "=", "southbrook.mi.check"),
                               ("active", "=", True)])

    survivors = set()
    for check in env["southbrook.mi.check"].search([]):
        mo = check.production_id
        etype = "cutlist_divergence" if check.category == "cut" else "mi_blocker"
        key = Exc._dedupe_key_for(etype, "southbrook.mi.check", check.id, check)
        if key in {s[1] for s in survivors}:
            continue  # two live checks share an identity — keep the first
        candidates = Exc.search([
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", etype),
            ("mo_id", "=", mo.id if mo else False),
        ], order="id asc")
        if not candidates:
            continue
        touched = candidates.filtered(lambda r: r.state != "new")
        winner = touched[0] if touched else candidates[0]
        winner.write({
            "dedupe_key": key,
            "source_res_id": check.id,
            "active": True if winner.state not in ("resolved", "dismissed") else winner.active,
        })
        survivors.add((winner.id, key))

    keep_ids = [s[0] for s in survivors]
    doomed = Exc.search([
        ("source_model", "=", "southbrook.mi.check"),
        ("id", "not in", keep_ids),
    ])
    archived = len(doomed)
    # Written in slices: a single write over ~39k rows on the QNAP is a long
    # transaction against a live database.
    for i in range(0, archived, 2000):
        doomed[i:i + 2000].write({"active": False, "dedupe_key": False})

    # Non-MI sources have stable ids; key them on the legacy triple.
    others = Exc.search([("source_model", "!=", "southbrook.mi.check"),
                         ("dedupe_key", "=", False)])
    for rec in others:
        rec.dedupe_key = Exc._dedupe_key_for(
            rec.exception_type, rec.source_model, rec.source_res_id)

    after = Exc.search_count([("active", "=", True)])
    _logger.warning(
        "command_center dedupe migration: %s MI rows open before, %s survivors "
        "keyed, %s archived, %s non-MI rows keyed, %s open after.",
        before, len(survivors), archived, len(others), after)
