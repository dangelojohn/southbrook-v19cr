# SPDX-License-Identifier: LGPL-3.0-only
"""P0 E2E remediation (2026-08-02) — sentinel/debris design-line sweep,
round 2.

Prod fact (verified read-only, 2026-08-02): southbrook.kitchen.design.line
holds 12 ACTIVE rows parked at the client's "append to the end" sort-key
sentinel — 6 on design 160 and 6 on design 99 — priced, counted in
total_cabinets/estimated_price, and rendered nowhere (a million inches
outside the room / camera frustum). Design 160 claims 14 cabinets / $3,805
while its own line table holds 26 rows.

This is a RECURRENCE, not a first sighting. The 19.0.5.34.1 migration
(Task 9, 2026-07-30) already swept this exact class of row off design 99
once (5 rows, that time). Task 9b's save_design write-path guard
(controllers/main.py's `_is_sentinel_position` check, gating the endpoint
most "+ Add" saves go through) closed the most obvious producer, but these
12 rows exist despite that guard already being live — so at least one
other write path still lets a sentinel position reach the DB verbatim. A
concurrent task is closing that remaining producer gap (JS + controller
layers); this migration is deliberately scoped to the consumer side only —
sweep whatever is ALREADY persisted, regardless of which path put it
there. Re-running this class of migration when the same symptom
resurfaces is the expected shape of a defense-in-depth fix, not a sign the
first one was wrong.

Detection reuses the ONE shared threshold this addon already standardized
on (Task 9b review round 2): `DEBRIS_THRESHOLD_IN` (models/kitchen_
design.py, currently 9999in) — the same constant controllers/main.py's
write-path guard and `SouthbrookKitchenDesignLine._is_debris_signature`
already key off. 9999in is comfortably above any real room dimension
(rooms top out in the low hundreds of inches) and two orders of magnitude
below the verified 1,000,000in client sentinel
(kitchen_configurator.js's `sortX = ... : 1e6`), so it catches the exact
sentinel value AND any other grossly-out-of-room coordinate on either
axis, without this migration inventing a second, independent magic
number. Scope is deliberately position-only — no cabinet_type filter, no
layout_role distinction: the sentinel can land on any line type, and this
migration's job is "archive anything sitting essentially at infinity", not
to reason about how it got there.

ARCHIVE, not delete: `southbrook.kitchen.design.line.active` exists
(default=True; see the model's own "Never a permanent delete" field help)
and reversible beats destructive — a shop may want to see what was lost.
Same three-field shape the write-path guard already uses (Task 9b), so an
admin who unarchives a row — old sweep or this one — sees one consistent,
honest, unpriced, flagged-unresolved placeholder rather than something
that still looks like a real priced cabinet: `active=False,
is_unresolved=True, price_unit=0.0`. A row already active=False is left
untouched (never reactivated).

Idempotent: the WHERE clause only ever matches `active = true` debris; a
second run finds nothing left to archive and is a no-op.

New in this round — the whole sweep runs inside `cr.savepoint()` guarded
by a broad `try/except`. An orphan-cleanup migration is not worth failing
an entire upgrade over: if anything here raises, the savepoint rolls back
cleanly (archival and totals-recompute stay atomic — never half-applied,
matching the 19.0.5.34.1 fix-round-1 concern about stale stored
aggregates), a loud warning is logged, and the `-u` cycle continues. The
existing debris rows are simply left for a follow-up attempt.

Totals recompute: same reachable, safe path 19.0.5.34.1 already proved out
— `southbrook.kitchen.design._compute_totals()` (total_cabinets/
base_count/wall_count/filler_count/estimated_price/remainder_in are
STORED computed fields whose @api.depends does not cover `.active`, so a
raw-SQL archival UPDATE never retriggers them on its own). Called directly
on the affected designs after an `env.invalidate_all()` — a plain
recordset-scoped invalidate is not enough; `cabinet_line_ids`' cached
id-list survives that and keeps citing the now-archived rows (verified
empirically by the 19.0.5.34.1 fix). Runs at most once per migration (a
handful of designs), so the blunt whole-transaction invalidation cost is a
non-issue.

Fires on the 19.0.5.44.0 upgrade.
"""
import logging

from odoo import SUPERUSER_ID, api
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    DEBRIS_THRESHOLD_IN as _DEBRIS_THRESHOLD_IN,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    try:
        with cr.savepoint():
            _sweep_sentinel_debris(cr)
    except Exception:
        _logger.warning(
            "19.0.5.44.0 post-migrate: sentinel/debris design-line sweep "
            "failed and was rolled back (savepoint) — an orphan-cleanup "
            "migration is not worth failing this upgrade over. The "
            "existing debris rows are unaffected; investigate and re-run "
            "manually.",
            exc_info=True,
        )


def _sweep_sentinel_debris(cr):
    if not _table_exists(cr, "southbrook_kitchen_design_line"):
        _logger.warning(
            "19.0.5.44.0 post-migrate: southbrook_kitchen_design_line "
            "table not found — skipping sentinel/debris design-line "
            "sweep.")
        return
    design_ids = _archive_debris_lines(cr)
    if design_ids:
        _recompute_design_totals(cr, design_ids)


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    )
    return bool(cr.fetchone())


def _archive_debris_lines(cr):
    # Log design ids + per-design counts BEFORE the UPDATE, so the log
    # reflects exactly what is about to be archived.
    cr.execute(
        """
        SELECT design_id, count(*)
          FROM southbrook_kitchen_design_line
         WHERE active = true
           AND (abs(x_position_in) >= %(t)s OR abs(z_position_in) >= %(t)s)
         GROUP BY design_id
         ORDER BY design_id
        """,
        {"t": _DEBRIS_THRESHOLD_IN},
    )
    rows = cr.fetchall()
    if not rows:
        _logger.info(
            "19.0.5.44.0 post-migrate: no active sentinel/debris design "
            "lines found (abs(x_position_in)/abs(z_position_in) >= %s\") "
            "— nothing to archive.",
            _DEBRIS_THRESHOLD_IN,
        )
        return []

    total = sum(count for _design_id, count in rows)
    _logger.info(
        "19.0.5.44.0 post-migrate: archiving %d sentinel/debris design "
        "line(s) across %d design(s) (threshold: abs(x_position_in) or "
        "abs(z_position_in) >= %s\"): %s",
        total, len(rows), _DEBRIS_THRESHOLD_IN,
        ", ".join(
            "design %s: %d line(s)" % (design_id, count)
            for design_id, count in rows
        ),
    )

    cr.execute(
        """
        UPDATE southbrook_kitchen_design_line
           SET active = false,
               is_unresolved = true,
               price_unit = 0.0
         WHERE active = true
           AND (abs(x_position_in) >= %(t)s OR abs(z_position_in) >= %(t)s)
        """,
        {"t": _DEBRIS_THRESHOLD_IN},
    )
    _logger.info(
        "19.0.5.44.0 post-migrate: archived %d sentinel/debris design "
        "line(s) (active=false, is_unresolved=true, price_unit=0.0 — "
        "aligned with the Task 9b write-path guard's flag shape). "
        "Resulting active-debris count for this batch: 0.",
        cr.rowcount,
    )
    return [design_id for design_id, _count in rows if design_id]


def _recompute_design_totals(cr, design_ids):
    """Force a fresh `_compute_totals()` for every design that had
    sentinel/debris lines archived above, since the SQL UPDATE bypasses
    the ORM's @api.depends trigger graph entirely (see module docstring).
    Idempotent: recomputing from the now-current cabinet_line_ids is a
    no-op on a second run, since the debris is already excluded
    (active=False) by then.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    designs = env["southbrook.kitchen.design"].browse(design_ids).exists()
    if not designs:
        return
    # The cursor-level UPDATE above never touched the ORM cache, and a
    # record-scoped invalidate_recordset() is not enough — cabinet_
    # line_ids' cached id-list survives that (verified empirically by the
    # 19.0.5.34.1 fix). env.invalidate_all() forces every subsequent read
    # back to the database instead.
    env.invalidate_all()
    designs._compute_totals()
    env.flush_all()
    _logger.info(
        "19.0.5.44.0 post-migrate: recomputed stored totals "
        "(total_cabinets, base_count, wall_count, filler_count, "
        "estimated_price, remainder_in) for %d design(s) after "
        "sentinel/debris archival: %s",
        len(designs), designs.ids,
    )
