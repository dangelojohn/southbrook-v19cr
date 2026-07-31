# SPDX-License-Identifier: LGPL-3.0-only
"""Fix (design-naming) — one-time backfill for stale auto-generated
kitchen design names.

Defect (confirmed against production data): `southbrook.kitchen.design
.name` is a plain Char, auto-set ONCE at first save
(controllers/main.py's `_auto_name`, gated by `if not design_id` in
`save_design`). A later room resize updates `room_width_in`/
`room_depth_in` in the same write() path but — before this fix —
never regenerated the name, so the design tree/landing page could show
a name that flatly contradicts the record's real dimensions.

Prod fact (verified read-only): design 103 was named "Walk-in - 12x30
- 2026-07-30" while its ACTUAL room dims were 54x54. Four of the last
eleven designs (98/100/101/103) showed the same drift.

The write path (controllers/main.py's save_design) now regenerates the
name on every resize that lands a room-dimension change, but ONLY when
the currently-stored name is still recognisably auto-generated —
never touching a design a human has manually renamed. This migration
is the one-time backfill for rows that went stale BEFORE that write-
path fix existed: for every design whose stored name matches the
auto-name shape but whose embedded width/depth disagree with the
design's CURRENT room_width_in/room_depth_in, the name is regenerated
(today's date, current dims, current partner). Rows that were ever
hand-renamed (name doesn't match the auto-name shape at all) are left
completely untouched, per the same "never clobber a human rename"
contract as the write-path fix.

AUTO_NAME_RE / auto_name_str() / is_auto_generated_name() are imported
directly from controllers/main.py — the ONE definition of what an auto
name looks like and how it's built (see that module's own docstring
for the format). There is no second, hand-copied pattern here.

Idempotent: a name this migration regenerates is itself auto-shaped
AND its freshly-embedded width/depth agree with the (unchanged)
current dims, so a second run finds nothing left to rename for that
row. Defensive: guards on the table and the specific columns this
migration reads/writes existing before touching anything, following
the same style as the 19.0.5.34.1 debris-repair migration in this
same addon.

Fires on the 19.0.5.35.2 upgrade. (Was authored for 19.0.5.35.1, but a
concurrent session shipped its own 5.35.1 to prod first — an equal version
never re-runs migrations, so this was moved up to 5.35.2 to actually fire.)
"""
import datetime
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.southbrook_kitchen_3d_configurator.controllers.main import (
    AUTO_NAME_RE,
    auto_name_str,
)

_logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ("name", "room_width_in", "room_depth_in", "partner_id")


def migrate(cr, version):
    if not version:
        return
    if not _table_exists(cr, "southbrook_kitchen_design"):
        _logger.warning(
            "5.35.1 post-migrate: southbrook_kitchen_design table not "
            "found — skipping stale auto-name backfill.")
        return
    if not _columns_exist(cr, "southbrook_kitchen_design", _REQUIRED_COLUMNS):
        _logger.warning(
            "5.35.1 post-migrate: southbrook_kitchen_design is missing "
            "one or more expected columns (%s) — skipping stale "
            "auto-name backfill.",
            ", ".join(_REQUIRED_COLUMNS),
        )
        return
    _backfill_stale_auto_names(cr)


def _table_exists(cr, table):
    cr.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    )
    return bool(cr.fetchone())


def _columns_exist(cr, table, columns):
    cr.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    )
    have = {row[0] for row in cr.fetchall()}
    return set(columns) <= have


def _backfill_stale_auto_names(cr):
    """ORM-based (not raw-SQL UPDATE, unlike the 19.0.5.34.1 debris
    migration): each candidate row needs its own freshly-formatted
    name, and going through design.write() keeps this on the exact
    same code path as a live user-triggered resize regeneration (no
    risk of a raw SQL UPDATE drifting from what the ORM's own `name`
    field constraints — if any are ever added later — would enforce).
    active_test=False so an archived design's stale name is corrected
    too, matching the sibling 19.0.5.32.0 totals-recompute migration's
    own active_test=False convention in this addon.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    Design = env["southbrook.kitchen.design"]
    designs = Design.with_context(active_test=False).search(
        [("name", "!=", False)])

    today_str = datetime.date.today().isoformat()
    renamed = []
    for design in designs:
        name = design.name
        m = AUTO_NAME_RE.match(name or "")
        if not m:
            continue  # never auto-named, or hand-renamed — leave alone

        stored_w = int(m.group("width"))
        stored_d = int(m.group("depth"))
        cur_w = int(design.room_width_in or 0)
        cur_d = int(design.room_depth_in or 0)
        if stored_w == cur_w and stored_d == cur_d:
            continue  # name already agrees with current dims — nothing stale

        pname = (
            (design.partner_id and design.partner_id.display_name)
            or "Walk-in"
        )
        new_name = auto_name_str(
            pname, design.room_width_in, design.room_depth_in, today_str)
        if new_name == name:
            continue  # defensive: no actual change to make

        design.write({"name": new_name})
        renamed.append((design.id, name, new_name))

    if not renamed:
        _logger.info(
            "5.35.1 post-migrate: no stale auto-named kitchen designs "
            "found (stored name auto-shaped but disagreeing with "
            "current room dims) — 0 rows renamed.")
        return

    _logger.info(
        "5.35.1 post-migrate: renamed %d stale auto-named kitchen "
        "design(s) whose stored name disagreed with their current "
        "room dimensions: %s",
        len(renamed),
        ", ".join(
            "design %s: %r -> %r" % (design_id, old_name, new_name)
            for design_id, old_name, new_name in renamed
        ),
    )
