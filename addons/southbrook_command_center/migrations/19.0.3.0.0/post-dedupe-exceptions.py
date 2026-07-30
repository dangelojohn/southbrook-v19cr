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

    # The NEW unique(dedupe_key) constraint is already built by init_models()
    # by the time post-migration scripts run (odoo/modules/loading.py:198,230),
    # but the OLD unique(source_model, source_res_id, exception_type) it
    # replaces is NOT dropped until ir.model.data._process_end() unlinks the
    # undeclared constraint record afterwards (loading.py:523; the DROP
    # CONSTRAINT itself only ever happens inside that unlink,
    # odoo/addons/base/models/ir_model.py:1873-1917). So while THIS function
    # runs, the legacy constraint is still enforced — and the election below
    # deliberately reuses `check.id` as the elected winner's source_res_id,
    # which is exactly the triple the newest sweep's row already holds.
    # Drop it explicitly, first, so that write never collides.
    cr.execute("ALTER TABLE southbrook_command_exception "
               "DROP CONSTRAINT IF EXISTS southbrook_command_exception_source_uniq")

    env = api.Environment(cr, SUPERUSER_ID, {})
    Exc = env["southbrook.command.exception"].with_context(active_test=False)

    before = Exc.search_count([("source_model", "=", "southbrook.mi.check"),
                               ("active", "=", True)])

    survivors = set()
    # Rows already elected as a survivor in THIS run. Two live checks can
    # share the same (mo_id, exception_type) candidate domain (etype only
    # encodes category, not check name) — without this exclusion, a second
    # check's election could re-select and overwrite the first check's
    # winner. Excluding claimed ids guarantees one live condition never
    # elects the same row twice.
    claimed_ids = set()
    for check in env["southbrook.mi.check"].search([]):
        mo = check.production_id
        etype = "cutlist_divergence" if check.category == "cut" else "mi_blocker"
        key = Exc._dedupe_key_for(etype, "southbrook.mi.check", check.id, check)
        if key in {s[1] for s in survivors}:
            continue  # two live checks share an identity — keep the first
        domain = [
            ("source_model", "=", "southbrook.mi.check"),
            ("exception_type", "=", etype),
            ("mo_id", "=", mo.id if mo else False),
        ]
        if claimed_ids:
            domain.append(("id", "not in", list(claimed_ids)))
        candidates = Exc.search(domain, order="id asc")
        if not candidates:
            continue
        # Elect only from genuinely-open, human-touched states. `resolved`
        # and `dismissed` are deliberately excluded here: electing one of
        # those as the survivor of a LIVE condition would bury it — the
        # next sweep finds it by key with active_test=False and correctly
        # never reactivates it, so the live blocker becomes permanently
        # invisible. Fall back to the oldest still-active candidate
        # (an untouched "new" row) rather than to any candidate at all.
        touched = candidates.filtered(
            lambda r: r.state in ("acknowledged", "in_progress"))
        active_candidates = candidates.filtered(lambda r: r.active)
        if touched:
            winner = touched[0]
        elif active_candidates:
            winner = active_candidates[0]
        else:
            # Every candidate in this bucket is resolved/dismissed — there
            # is no live row to elect. Leave this check unkeyed; the
            # cron-scan materializer will mint a fresh, correctly-keyed row
            # for it on its next pass.
            continue
        winner.write({
            "dedupe_key": key,
            "source_res_id": check.id,
            "active": True if winner.state not in ("resolved", "dismissed") else winner.active,
        })
        survivors.add((winner.id, key))
        claimed_ids.add(winner.id)

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
