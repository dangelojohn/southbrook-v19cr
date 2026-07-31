# SPDX-License-Identifier: LGPL-3.0-only
"""Task 9 — archive sentinel-parked ("debris") design lines.

Prod fact (design 99, verified read-only 2026-07-30): 5 ACTIVE
southbrook.kitchen.design.line rows sitting at x_position_in=1,000,000 —
priced, counted in totals, and invisible (rendered far outside the room /
camera frustum, so nobody notices).

Producer (read-only investigation, this task — NOT fixed here; see the
Task 9 report for why): kitchen_configurator.js's `_addCabinetFromProduct`
uses `sortX = ... : 1e6` as a "no explicit target position, append at the
end" sort-key sentinel. `_recomputeLayoutFromItems`'s `packRow` pass
normally overwrites that sentinel with a real position before save — EXCEPT
it is skipped entirely once a design is multi-wall or already carries a
corner cabinet (the `multiWall` guard, kitchen_configurator.js ~line 1382:
"once a design spans 2+ walls ... geometry belongs to the server-side
kitchen_layout_engine ... Repacking locally from x=0 here would shove the
back-wall cabinets straight into that reserved corner cell"). The save
endpoint then persists the client's raw x_position_in verbatim for
back-wall lines (controllers/main.py ~line 502, "Back-wall lines are LEFT
ALONE" ~line 544-549) — so the sentinel reaches the DB, active=True.

Task 1 shipped an OUT_OF_ROOM_BOUNDS validator (kitchen_design.py, action-
confirm-gated) that now BLOCKS this class of line going forward. It does
not retroactively clean up rows that predate it (or rows on designs that
never reach action_confirm). This migration is that retroactive repair:
archive (active=False) any design line whose abs(x_position_in) or
abs(z_position_in) is >= 9999in — comfortably above any real room
dimension (rooms top out in the low hundreds of inches) and an order of
magnitude below the verified 1,000,000 sentinel, so the threshold only
ever catches debris, never a legitimate placement.

Scope is deliberately position-only: no cabinet_type filter, no
layout_role distinction, no design-level precondition — the sentinel can
land on any line type via _addCabinetFromProduct, and this migration's job
is purely "hide anything sitting essentially at infinity", not to reason
about why it got there. A line already active=False is left active=False
(idempotent no-op; never reactivated). In-bounds lines are never touched.

Fires on 5.34.0 -> 5.34.1 upgrade. Idempotent — the WHERE clause only ever
matches active=True debris; re-running finds nothing left to archive.
"""
import logging

_logger = logging.getLogger(__name__)

# Inches. See docstring above for why this threshold is safe: it sits
# ~2-3 orders of magnitude above any real room dimension and ~2 orders
# of magnitude below the verified prod sentinel (1,000,000).
_DEBRIS_THRESHOLD_IN = 9999


def migrate(cr, version):
    if not version:
        return
    if not _table_exists(cr, "southbrook_kitchen_design_line"):
        _logger.warning(
            "5.34.1 post-migrate: southbrook_kitchen_design_line table "
            "not found — skipping debris design-line archival.")
        return
    _archive_debris_lines(cr)


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
            "5.34.1 post-migrate: no active debris design lines found "
            "(abs(x_position_in)/abs(z_position_in) >= %s\") — nothing to "
            "archive.",
            _DEBRIS_THRESHOLD_IN,
        )
        return

    total = sum(count for _design_id, count in rows)
    _logger.info(
        "5.34.1 post-migrate: archiving %d debris design line(s) across "
        "%d design(s) (threshold: abs(x_position_in) or "
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
           SET active = false
         WHERE active = true
           AND (abs(x_position_in) >= %(t)s OR abs(z_position_in) >= %(t)s)
        """,
        {"t": _DEBRIS_THRESHOLD_IN},
    )
    _logger.info(
        "5.34.1 post-migrate: archived %d debris design line(s).",
        cr.rowcount,
    )
