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
archive (active=False, is_unresolved=True, price_unit=0.0 — see the
Task 9b review-round note below) any design line whose abs(x_position_in)
or abs(z_position_in) is >= 9999in — comfortably above any real room
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

Fix round 1 (code review) — stale stored aggregates: the archival above is
raw SQL against the cursor, which bypasses the ORM's dependency-trigger
graph entirely. southbrook.kitchen.design's total_cabinets/base_count/
wall_count/filler_count/estimated_price/remainder_in are STORED computed
fields (`_compute_totals`, @api.depends on cabinet_line_ids.quantity/
.price_unit/.cabinet_type — NOT on .active, so even an ORM `write({"active":
False})` on the lines would not retrigger this compute via the standard
dependency graph). Left alone, a design like 99 would keep its
debris-inflated count/price forever post-migration — the exact symptom
this task exists to close. Fix: after the archival UPDATE, explicitly
recompute `_compute_totals()` for every design touched (collected from the
same GROUP BY used for logging) and flush, forcing a fresh read of
cabinet_line_ids (now correctly excluding the newly-archived debris).

Task 9b review round (2026-07-30) — flag-shape alignment with the
write-path guard: Task 9b's server-side save_design guard (controllers/
main.py) archives a NEWLY incoming sentinel line with three fields, not
one — `active=False`, `is_unresolved=True`, `price_unit=0.0` — so an
admin who unarchives a debris row to inspect it sees an honest,
unpriced, flagged-as-unresolved placeholder rather than something that
still LOOKS like a real, priced $999 cabinet. This migration originally
set only `active=False`, leaving existing debris rows (already in the
DB before this migration ever runs) in that stale, misleading shape
even after archival. Extended the same UPDATE to also set
`is_unresolved = true, price_unit = 0.0` on every row it archives — no
behavioral change to totals (rows are already excluded from
`cabinet_line_ids` by `active=False` alone, so `_compute_totals` was
already correct without this; this is purely about the archived row's
own displayed/inspected shape matching the write-path guard's
contract). Idempotency is unaffected: the WHERE clause still only ever
matches `active = true` rows, so a second run finds nothing left to
touch regardless of what the SET list contains.

Task 9b review round 2 (2026-07-30) — the threshold VALUE now has
exactly one definition. Previously this module carried its own literal
`_DEBRIS_THRESHOLD_IN = 9999`, independent from controllers/main.py's
identical-by-hand-copy constant — two numbers that happened to agree,
not one shared source. Imports `DEBRIS_THRESHOLD_IN` from
models/kitchen_design.py instead (same constant the write-path guard
and all three RESET-TO-CANONICAL reactivation sites now share — see
that constant's docstring). Safe to import here: post-migrate scripts
run AFTER this addon's own Python/model code is loaded into the
registry for the `-u` cycle that triggers them, so the model module is
already fully importable by the time `migrate()` runs; the archival SQL
itself stays entirely self-contained (raw cursor UPDATE, no ORM/model
coupling) — only the threshold INTEGER is shared, not any model logic.
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
    if not _table_exists(cr, "southbrook_kitchen_design_line"):
        _logger.warning(
            "5.34.1 post-migrate: southbrook_kitchen_design_line table "
            "not found — skipping debris design-line archival.")
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
            "5.34.1 post-migrate: no active debris design lines found "
            "(abs(x_position_in)/abs(z_position_in) >= %s\") — nothing to "
            "archive.",
            _DEBRIS_THRESHOLD_IN,
        )
        return []

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
           SET active = false,
               is_unresolved = true,
               price_unit = 0.0
         WHERE active = true
           AND (abs(x_position_in) >= %(t)s OR abs(z_position_in) >= %(t)s)
        """,
        {"t": _DEBRIS_THRESHOLD_IN},
    )
    _logger.info(
        "5.34.1 post-migrate: archived %d debris design line(s) "
        "(active=false, is_unresolved=true, price_unit=0.0 — aligned "
        "with the Task 9b write-path guard's flag shape).",
        cr.rowcount,
    )
    return [design_id for design_id, _count in rows if design_id]


def _recompute_design_totals(cr, design_ids):
    """Force a fresh `_compute_totals()` for every design that had debris
    archived, since the SQL UPDATE above bypasses the ORM's @api.depends
    trigger graph (which doesn't cover `.active` for this compute anyway
    — see the module docstring). Idempotent: recomputing from the
    now-current cabinet_line_ids is a no-op on a second run, since the
    debris is already excluded (active=False) by then.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    designs = env["southbrook.kitchen.design"].browse(design_ids).exists()
    if not designs:
        return
    # The cursor-level UPDATE above never touched the ORM cache. A
    # record-scoped `designs.invalidate_recordset()` is NOT enough here:
    # the design's `cabinet_line_ids` (a plain One2many, not stored) gets
    # its id-list cached on first access and — verified empirically while
    # testing this fix — that cached id-list survives invalidating just
    # the design records; the debris line ids stayed in it even after
    # invalidate_recordset(), because the archived rows themselves (their
    # `.active` value) were written outside the ORM and were never part
    # of what got invalidated. `env.invalidate_all()` clears the whole
    # transaction-wide cache instead, which is the reliable way to force
    # every subsequent read (including cabinet_line_ids's underlying
    # search) back to the database. This runs at most once per migration
    # (a handful of designs), so the blunt invalidation cost is a non-issue.
    env.invalidate_all()
    designs._compute_totals()
    env.flush_all()
    _logger.info(
        "5.34.1 post-migrate: recomputed stored totals (total_cabinets, "
        "base_count, wall_count, filler_count, estimated_price, "
        "remainder_in) for %d design(s) after debris archival: %s",
        len(designs), designs.ids,
    )
