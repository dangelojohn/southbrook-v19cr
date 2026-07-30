# SPDX-License-Identifier: LGPL-3.0-only
"""project.task — Premium Orchestration readiness cron + telemetry.

Phase 1.1 closes the loop by ensuring the readiness compute that
southbrook_project_mrp already implements actually runs on a schedule
against every open kitchen task.

The actual scoring logic lives in southbrook_project_mrp.project_task —
specifically ``action_recompute_readiness_lines`` (confirmed via grep
across the addon, line 2239 of that file). We don't reimplement it;
this addon only adds:

  1. ``readiness_last_recomputed_at`` — a Datetime stamp on the task,
     so the dashboard view in views/kitchen_jobs_views.xml can sort
     "most stale" first.
  2. ``_cron_recompute_readiness_all`` — an @api.model entry point the
     ir.cron in data/ir_cron.xml fires every 15 minutes. Picks all
     open kitchen tasks (linked to a sale.order and not in a closed
     state) and triggers the recompute; if the readiness_decision
     flips between runs, drops a chatter note on the task.

Defensive symmetry with sale_order.py: we look up the readiness
compute method via getattr() rather than direct call, so the addon
remains installable if a future southbrook_project_mrp release renames
the public method. Both common spellings (``action_recompute_readiness``
and the v19-shipped ``action_recompute_readiness_lines``) are probed.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


# Stage states that count as "done" — we skip these in the cron picker,
# both because re-scoring a closed task is wasteful and because we don't
# want chatter spam on finished jobs. Mirrors the values used by
# southbrook_project's project.project rollup compute.
_CLOSED_TASK_STATES = ("1_done", "1_canceled")

# Public method names on southbrook_project_mrp.project_task that
# trigger a readiness recompute. The currently-shipped name is the
# *_lines variant; the earlier draft used the unsuffixed name. We try
# both so this addon survives a rename without a manifest bump.
_READINESS_RECOMPUTE_METHODS = (
    "action_recompute_readiness_lines",
    "action_recompute_readiness",
    "_recompute_readiness",
)


class ProjectTask(models.Model):
    _inherit = "project.task"


    @api.model
    def default_get(self, fields_list):
        """Land a hand-created kitchen job in the kitchen project, not Private.

        `Kitchen Jobs -> New` passed no default project, so the task was created with
        project_id unset — which in Odoo is a PRIVATE task, and private tasks carry the
        personal Inbox/Today/This Week pipeline instead of Design & Quote -> ... ->
        Delivery & Install. The first thing a user saw on the flagship board was the
        wrong process.

        The project and stage are resolved with the SAME helpers the sale-order confirm
        path already uses, rather than a second lookup that could disagree with it: a
        job typed in by hand and a job raised from a confirmed order must land in the
        same place, or the board has two populations that only look alike. Both helpers
        are stateless and safe to call on an empty recordset.

        Gated on an explicit context flag so this only fires from the Kitchen Ops board.
        Every other way of creating a task — including a genuine private task — is
        untouched.
        """
        values = super().default_get(fields_list)
        if not self.env.context.get("kitchen_job_intake"):
            return values
        if values.get("project_id"):
            return values
        SaleOrder = self.env["sale.order"]
        project = SaleOrder._resolve_kitchen_project()
        if not project:
            # No project configured and none exists. Leaving it unset is honest; the
            # alternative is inventing a project, which is an admin decision.
            _logger.warning(
                "kitchen_job_intake: no kitchen project resolved. Set the config "
                "parameter southbrook_premium.default_kitchen_project_id.")
            return values
        values["project_id"] = project.id
        if "stage_id" in fields_list and not values.get("stage_id"):
            stage = SaleOrder._resolve_design_quote_stage(project)
            if stage:
                values["stage_id"] = stage.id
        return values

    # ------------------------------------------------------------------
    # Telemetry field — last successful recompute timestamp
    # ------------------------------------------------------------------
    # Datetime not Date — the readiness cron runs every 15 minutes and we
    # want sub-day resolution on the "most stale first" dashboard sort.
    # Not tracked: chatter spam on a per-cron-tick field would drown the
    # actual status flips this module also logs (see below).
    readiness_last_recomputed_at = fields.Datetime(
        string="Readiness Last Recomputed",
        copy=False,
        readonly=True,
        help="Stamp set by the Premium Orchestration readiness cron each "
             "time a recompute completes for this task. Used by the Kitchen "
             "Jobs dashboard to sort 'most stale' first and as the cron's "
             "own progress marker.",
    )

    # ------------------------------------------------------------------
    # Cron entry point — readiness recompute fan-out
    # ------------------------------------------------------------------
    @api.model
    def _cron_recompute_readiness_all(self):
        """Recompute readiness for every open kitchen task.

        "Open kitchen task" = a project.task that
            * carries an ``x_southbrook_sale_order_id`` link (i.e. it is
              an orchestration spine, not an internal admin task), AND
            * is not in a closed state (done / cancelled).

        For each task: snapshot ``readiness_decision`` before, call the
        recompute, stamp ``readiness_last_recomputed_at``, and — if the
        decision flipped — log a chatter note so the kitchen ops queue
        sees the transition (ready → blocked, blocked → ready, etc.)."""
        # Skip the entire body if the link field hasn't been declared in
        # this DB (e.g. unit-test workspace that only loaded mail/sale).
        if "x_southbrook_sale_order_id" not in self._fields:
            return 0

        domain = [
            ("x_southbrook_sale_order_id", "!=", False),
            ("state", "not in", _CLOSED_TASK_STATES),
        ]
        tasks = self.search(domain)
        now = fields.Datetime.now()
        flipped = 0
        for task in tasks:
            # Per-row isolation — this cron runs as root over every open kitchen
            # job; without this a single raising task aborts the whole sweep
            # (the analytics + DQ crons already isolate per row).
            try:
                previous = task._readiness_snapshot()
                task._invoke_readiness_recompute()
                task.readiness_last_recomputed_at = now
                current = task._readiness_snapshot()
                if previous != current and any(previous) and any(current):
                    task._log_readiness_flip(previous, current)
                    flipped += 1
            except Exception:  # noqa: BLE001 — one bad task must not abort the sweep
                _logger.exception(
                    "readiness sweep: task %s recompute failed", task.id)
        return len(tasks)

    # ------------------------------------------------------------------
    # Defensive recompute dispatcher
    # ------------------------------------------------------------------
    def _invoke_readiness_recompute(self):
        """Call whichever readiness recompute method this DB ships.

        Tries the candidate method names in priority order. Silent no-op
        if none of them exist (e.g. southbrook_project_mrp absent on a
        CE-only test DB) so we don't crash the cron tick."""
        self.ensure_one()
        for method_name in _READINESS_RECOMPUTE_METHODS:
            method = getattr(self, method_name, None)
            if callable(method):
                method()
                return True
        return False

    def _readiness_snapshot(self):
        """Return a tuple of (decision, release_state) for flip detection.

        Both fields are sourced from southbrook_project_mrp; we tolerate
        either being absent on a degraded DB and return None in that slot
        — the caller filters out None tuples before posting a flip note."""
        self.ensure_one()
        decision = None
        release = None
        if "readiness_decision" in self._fields:
            decision = self.readiness_decision
        if "southbrook_production_release_state" in self._fields:
            release = self.southbrook_production_release_state
        return (decision, release)

    def _log_readiness_flip(self, previous, current):
        """Post a chatter note describing the readiness transition."""
        self.ensure_one()
        prev_decision, prev_release = previous
        cur_decision, cur_release = current
        body = _(
            "Premium Orchestration: readiness recompute changed status.<br/>"
            "Decision: <b>%(prev_d)s</b> &rarr; <b>%(cur_d)s</b><br/>"
            "Release state: <b>%(prev_r)s</b> &rarr; <b>%(cur_r)s</b>",
        ) % {
            "prev_d": prev_decision or _("(unset)"),
            "cur_d": cur_decision or _("(unset)"),
            "prev_r": prev_release or _("(unset)"),
            "cur_r": cur_release or _("(unset)"),
        }
        self.message_post(body=body)
