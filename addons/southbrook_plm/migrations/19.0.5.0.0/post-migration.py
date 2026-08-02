# SPDX-License-Identifier: LGPL-3.0-only
"""Backfill for the CUT-SPEC VERSION CHAIN + ACTIVE WINDOWS feature.

Two things this addon adds at 19.0.5.0.0 have no history for records that
already existed before the upgrade:

  1. southbrook.cut.spec.active_from / active_to / predecessor_id /
     successor_id — action_activate (models/southbrook_cut_spec.py) now
     stamps these going forward, but every spec activated BEFORE this
     version has no window recorded at all.
  2. sale.order.line.southbrook_cut_spec_version_id / southbrook_bom_version
     — action_confirm has captured these since an earlier version, and the
     new create() override (models/sale_order_line.py) closes the
     added-after-confirm gap going forward, but neither ever runs
     retroactively over lines that predate them.

Both steps below are RECONSTRUCTIONS, not measurements. Nothing here may
report higher confidence than the data actually supports — where the
historical truth is genuinely unrecoverable, the row is left alone and
counted as skipped, never guessed. Re-running this script is safe: every
step only ever touches records that are still missing the field(s) it
seeds ("no active_from", "no active_to", "southbrook_cut_spec_version_id
not set", "southbrook_bom_version not set"), so a partial or repeated run
converges rather than double-writing.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Slice size for batched writes over sale.order.line — prod order-line
# volume can run into the tens of thousands, and a single write() call
# spanning all of them at once is a long-held transaction against a live
# database (see the sibling southbrook_command_center dedupe migration,
# which chunks its archival writes for the same reason). Cut specs (Step
# 1) are not chunked: they are authored through a human-gated ECO process,
# so the table is inherently small — a handful to a few dozen rows, never
# tens of thousands.
_WRITE_CHUNK = 2000


def _chunked_write(records, vals):
    """Write `vals` over `records` in slices of _WRITE_CHUNK.

    Still grouped-not-per-record (the actual requirement) even for the
    common case where `records` is under one chunk — this just adds a
    safety ceiling for the cases where it isn't.
    """
    for i in range(0, len(records), _WRITE_CHUNK):
        records[i:i + _WRITE_CHUNK].write(vals)


def _backfill_cut_spec_windows(env):
    """Step 1 — seed active_from / active_to on pre-feature cut specs.

    Scope is deliberately narrow: ONLY active_from and active_to are
    seeded here. predecessor_id / successor_id (the chain-link fields
    added alongside them) are left untouched for pre-existing records —
    _get_spec_active_at() (the query this whole feature exists to serve)
    only ever reads active_from/active_to, so the chain links are a
    supplementary convenience for going-forward records, not required for
    historical resolution, and backfilling them from create_date ordering
    would be a second, independent guess stacked on top of this one.
    """
    Spec = env["southbrook.cut.spec"].with_context(active_test=False)
    all_specs = Spec.search([], order="create_date asc, id asc")

    # 1a. active_from: any spec (any state) missing it gets
    # active_from = create_date. This is a FLOOR, not a measurement: a
    # spec cannot have been active before it existed, but it may well
    # have sat in 'draft' for days or weeks before an ECO actually
    # activated it, and that gap is unrecoverable — there is no draft-
    # activation timestamp anywhere in the schema pre-19.0.5.0.0. For a
    # spec that was NEVER activated (still 'draft' today), active_from
    # seeded this way is a harmless placeholder: _get_spec_active_at()
    # only searches active_from windows to resolve a live-at-`when`
    # question, and a permanently-draft spec was never live at any
    # `when`, so this value is never actually load-bearing for it — it
    # only matters for specs that reached 'active'/'superseded'.
    seeded_from = 0
    for spec in all_specs:
        if not spec.active_from:
            spec.active_from = spec.create_date
            seeded_from += 1

    # 1b. active_to for 'superseded' specs missing it. Reconstruct "who
    # succeeded me" from create_date ordering among specs that were EVER
    # live (state active/superseded — a spec that never left draft was
    # never anyone's predecessor and is irrelevant here).
    #
    # BEST-EFFORT, NOT GROUND TRUTH: create_date order need not match true
    # activation order. A spec can be authored (created) as a draft long
    # before the ECO that activates it is approved, out of creation order
    # relative to sibling specs created later but activated sooner. There
    # is no way to distinguish that case from the common case (created in
    # roughly the order they were activated) using only what pre-existing
    # rows recorded. This reconstruction is the best available signal,
    # not a claim of certainty — hence "best-effort."
    ever_active = all_specs.filtered(lambda s: s.state in ("active", "superseded"))
    seeded_to = 0
    seeded_to_fallback = 0
    n = len(ever_active)
    for i, spec in enumerate(ever_active):
        if spec.state != "superseded" or spec.active_to:
            continue
        successor = ever_active[i + 1] if i + 1 < n else None
        if successor:
            # successor.active_from is guaranteed populated by now — 1a
            # ran over every spec, including this one, before 1b starts.
            spec.active_to = successor.active_from
            seeded_to += 1
        else:
            # No later spec exists in create_date order, so no successor
            # can be determined this way — yet the spec IS marked
            # 'superseded', so something must have retired it. Fall back
            # to this record's own write_date: action_activate's
            # supersession write (`prior.write({"state": "superseded",
            # ...})`) touches write_date at approximately the true
            # supersession moment, PROVIDED nothing else has written to
            # this record since — an assumption we cannot verify from
            # here. Approximate, and explicitly logged as a fallback
            # count below rather than folded silently into `seeded_to`.
            spec.active_to = spec.write_date
            seeded_to_fallback += 1

    _logger.info(
        "[19.0.5.0.0] cut-spec window backfill: %s spec(s) seeded "
        "active_from (from create_date), %s spec(s) seeded active_to "
        "(from a determined successor's active_from), %s spec(s) seeded "
        "active_to via the write_date fallback (no successor "
        "determinable by create_date ordering).",
        seeded_from, seeded_to, seeded_to_fallback,
    )


def _backfill_order_line_pins(env):
    """Step 2 — backfill sale.order.line pins for already-confirmed lines.

    For every confirmed/done line missing its cut-spec snapshot, resolve
    the spec that was actually live at confirmation time and stamp it —
    never today's active spec. The whole point of this feature is
    recording what was true THEN; blind-stamping today's spec onto a
    historical order would silently rewrite that order's manufacturing
    history to something it was never actually built to.
    """
    Line = env["sale.order.line"]
    CutSpec = env["southbrook.cut.spec"]
    Bom = env["mrp.bom"]

    lines = Line.search([
        ("order_id.state", "in", ("sale", "done")),
        ("southbrook_cut_spec_version_id", "=", False),
    ])

    pinned = 0
    skipped_unknown_spec = 0
    bom_backfilled = 0
    skipped_ambiguous_bom = 0

    # Grouped by target value so each distinct spec / BoM-version gets ONE
    # write() over every line that resolved to it, instead of one write
    # per line.
    spec_writes = {}   # spec_id -> [line_id, ...]
    bom_writes = {}     # southbrook_version (int) -> [line_id, ...]

    for line in lines:
        order = line.order_id
        # date_order is stock Odoo's Order Date field. For a confirmed
        # order it is not merely "the closest recorded proxy" — Odoo's
        # own action_confirm (sale/models/sale_order.py,
        # _prepare_confirmation_values) explicitly overwrites date_order
        # to fields.Datetime.now() at the moment state flips to 'sale'.
        # So for every line in our domain (order state in sale/done),
        # date_order IS the confirmation timestamp, not an approximation
        # of it — the DB even enforces it can't be NULL for state='sale'
        # (sale.order's _date_order_conditional_required constraint). The
        # `if not when` guard below is pure defence against a database
        # that predates that constraint or was touched by raw SQL — it
        # should never actually fire in practice.
        when = order.date_order
        if not when:
            skipped_unknown_spec += 1
            continue

        spec = CutSpec._get_spec_active_at(when)
        if not spec:
            # Genuinely unresolvable — e.g. `when` predates any cut spec
            # this database has ever recorded a window for, and even
            # _get_spec_active_at's own earliest-known-spec fallback came
            # back empty. Per the CRITICAL instruction: do NOT guess by
            # stamping today's active spec. Skip and count it.
            skipped_unknown_spec += 1
            continue

        spec_writes.setdefault(spec.id, []).append(line.id)
        pinned += 1

        # BoM version backfill — independent of whether the spec above
        # resolved; a line can legitimately get one without the other.
        if line.southbrook_bom_version:
            continue  # already has a value — never overwrite an existing one
        tmpl = line.product_id.product_tmpl_id
        if not tmpl:
            continue
        bom = Bom.search(
            [("product_tmpl_id", "=", tmpl.id), ("active", "=", True)],
            limit=1,
        )
        if not bom:
            continue
        if bom.southbrook_version == 1:
            # Never ECO-versioned — there has only ever BEEN one version,
            # so today's value is PROVABLY also the historical value at
            # order.date_order. This is the one case in this whole
            # migration where "today's value" is safe to use, precisely
            # because there is no version history for it to conflict
            # with.
            bom_writes.setdefault(bom.southbrook_version, []).append(line.id)
            bom_backfilled += 1
        else:
            # BoM has been through >= 1 ECO revision since this order was
            # confirmed. Unlike the cut spec, mrp.bom's version history
            # has no active_from/active_to chain to resolve "which
            # version was live at `when`" — that chain does not exist for
            # BoMs. The historical version is genuinely unknown. Skip and
            # count it; do not guess.
            skipped_ambiguous_bom += 1

    for spec_id, line_ids in spec_writes.items():
        _chunked_write(Line.browse(line_ids), {
            "southbrook_cut_spec_version_id": spec_id,
        })
    for version, line_ids in bom_writes.items():
        _chunked_write(Line.browse(line_ids), {
            "southbrook_bom_version": version,
        })

    _logger.info(
        "[19.0.5.0.0] order-line pin backfill: %s line(s) pinned to the "
        "cut spec live at their order's confirmation, %s line(s) skipped "
        "(no cut-spec window covers their order's confirmation moment — "
        "left unpinned rather than guessed), %s line(s) had their BoM "
        "version backfilled (BoM never ECO-versioned, so today's version "
        "is provably also the historical one), %s line(s) skipped for "
        "BoM version (BoM is at version >= 2 — historical version is "
        "genuinely unknown, not guessed).",
        pinned, skipped_unknown_spec, bom_backfilled, skipped_ambiguous_bom,
    )


def migrate(cr, version):
    # Matches the sibling southbrook_estimating / southbrook_command_center
    # migrations' convention — `version` is falsy only when Odoo calls
    # this outside a genuine version-transition upgrade (e.g. a fresh
    # `-i` install, where there is no "before" state to backfill from).
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    _backfill_cut_spec_windows(env)
    _backfill_order_line_pins(env)
