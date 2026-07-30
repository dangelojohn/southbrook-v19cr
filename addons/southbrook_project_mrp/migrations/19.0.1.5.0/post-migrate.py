# -*- coding: utf-8 -*-
"""Populate the three readiness fields that just became stored.

WHY THEY ARE NOW STORED, since "make it stored" reads like a performance tweak and
this was a correctness fix:

`job_at_risk` and `install_date_missing` were non-stored computes with `search=` hooks
pointing at `_search_boolean_compute`. That helper works — verified in a shell against
production, it returns `[('id','in',[182,201,198])]`, the three genuinely at-risk jobs.
Odoo calls it, receives that domain, and still returns zero rows; the same
`('id','in',[182,201,198])` passed directly returns three. So the Install Risk queue was
structurally empty and reported "No jobs at install risk" over a fleet where every job
was High or Critical — a false all-clear on the surface people use to decide whether a
kitchen can be installed.

Storing the fields removes the search hook from the path entirely. It also fixes what a
search hook never could: these fields can now be grouped, aggregated and sorted, which is
why the release queue's pager counted stage groups instead of rows.

Odoo initialises a newly-stored computed column during the update, but only where its
depends are already loaded. `install_date_missing` depends on `job_install_due`, which
depends on the MO rollup, so the order matters and a partial pass would leave a column
that looks populated and is wrong. Recomputing explicitly here is cheap on 35 records and
removes the ordering question.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    Task = env["project.task"].with_context(active_test=False)

    tasks = Task.search([])
    if not tasks:
        return

    # Order matters: the rollup feeds job_install_due, which feeds install_date_missing.
    tasks.invalidate_recordset()
    tasks._compute_mrp_status()
    tasks._compute_phase3_queue_flags()

    # The rest of the fields that became stored in this release. Each was previously a
    # non-stored compute with a `search=` hook that silently matched nothing, so every
    # filter and stat-button click-through built on them returned zero rows over
    # correctly-counted data.
    tasks._compute_southbrook_specs_complete()
    tasks._compute_crew()
    tasks._compute_workcenter_load()
    tasks._compute_workorder_rollup()
    tasks._compute_material_readiness()
    tasks.flush_recordset()

    # southbrook_production_release_state is STORED, so changing the compute that
    # decides between `blocked` and the newly-emitted `review` does not by itself
    # revisit the rows already on disk. Without this, every existing job keeps the
    # `blocked` it was given when the compute was binary, and the Production Release
    # Queue looks exactly as broken as before the fix.
    tasks._compute_southbrook_production_release()
    tasks.flush_recordset()

    at_risk = tasks.filtered("job_at_risk")
    missing = tasks.filtered("install_date_missing")
    newly_stored = {
        "specs_complete": len(tasks.filtered("southbrook_specs_complete")),
        "needs_cad_cutlist": len(tasks.filtered("cad_cutlist_review_required")),
        "pm_stage_mismatch": len(tasks.filtered("pm_stage_mismatch")),
        "crew_gap": len(tasks.filtered("crew_gap")),
        "over_capacity": len(tasks.filtered("workcenter_over_capacity")),
        "material_at_risk": len(tasks.filtered("material_at_risk")),
    }
    _logger.info("southbrook_project_mrp: newly-stored readiness flags %s", newly_stored)

    by_state = {}
    for task in tasks:
        key = task.southbrook_production_release_state or "unset"
        by_state[key] = by_state.get(key, 0) + 1
    _logger.info(
        "southbrook_project_mrp: recomputed readiness on %s task(s) — "
        "%s at risk, %s missing an install date, release states %s",
        len(tasks), len(at_risk), len(missing), by_state)

    # If this reports zero at-risk jobs, the recompute did not take and Install Risk is
    # still lying. Say so loudly rather than let a silent all-clear ship twice.
    if not at_risk and not missing:
        _logger.warning(
            "southbrook_project_mrp: no task flagged at-risk or missing an install date "
            "after recompute. Verify before trusting the Install Risk queue.")
