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
from datetime import datetime, time as dt_time

from odoo import _, api, fields, models
from odoo.osv.expression import AND

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

# ------------------------------------------------------------------
# Kitchen Jobs board — skj_board_intel (contract §1, BlindSpot review of
# action-1298, docs at scratchpad/CONTRACT_kitchen_jobs_board.md). These
# stay at module scope, not inline in the method, so the gate taxonomy and
# the DO NEXT ranking are readable and testable independently of the RPC
# plumbing around them.
# ------------------------------------------------------------------

# The board only ever shows kitchen-orchestration spines — tasks with no
# originating sale.order are administrative/private tasks that happen to
# share the model. ANDed onto the caller's domain on every skj_board_intel
# call, never trusted from the client alone (SAFETY, contract §1).
_SKJ_BASE_DOMAIN = [("x_southbrook_sale_order_id", "!=", False)]

# Verbatim provenance copy (contract §1/§6). Kept server-side so every
# consumer of this payload — the OWL header today, anything else later —
# reads identical wording instead of each hardcoding its own paraphrase.
_SKJ_PROVENANCE_COPY = (
    "Counts and gates are computed from live sale, project and "
    "manufacturing data every 15 minutes; the timestamp above is the last "
    "recompute. A dash or 'not computed' means the value has never been "
    "produced — it is never rendered as zero. Cost estimates and "
    "customer tier are present on records but not yet wired to this board."
)

# Gate taxonomy — fixed, worst-first order per contract §5 (verified STORED
# as of commit 7ebb787; see scratchpad/recon_field_inventory.md). Each
# tuple is (gate key, display label, the domain leaf beyond the board
# domain that means "this gate has tripped"). The gate key deliberately
# matches the underlying field name, so grepping either side finds the
# other.
_SKJ_GATE_TAXONOMY = (
    ("equipment_blocked", "Equipment down",
     [("equipment_blocked", "=", True)]),
    ("material_at_risk", "Material at risk",
     [("material_at_risk", "=", True)]),
    ("workcenter_over_capacity", "Station over capacity",
     [("workcenter_over_capacity", "=", True)]),
    ("unscheduled_workorder_count", "Unscheduled work orders",
     [("unscheduled_workorder_count", ">", 0)]),
    ("crew_gap", "No crew reserved",
     [("crew_gap", "=", True)]),
    ("cad_cutlist_review_required", "CAD/cutlist review",
     [("cad_cutlist_review_required", "=", True)]),
    ("southbrook_specs_complete", "Specs not confirmed",
     [("southbrook_specs_complete", "=", False)]),
    ("install_date_missing", "No install date",
     [("install_date_missing", "=", True)]),
    ("pm_stage_mismatch", "Stage mismatch",
     [("pm_stage_mismatch", "=", True)]),
    ("job_at_risk", "At risk",
     [("job_at_risk", "=", True)]),
)

# Gate -> existing search-view filter name (views/kitchen_jobs_views.xml
# §4), used by DO NEXT rank-3 to build a "filter" action instead of an
# act_window. Only the two gates that already have a dedicated board chip
# are listed; every other gate renders with filter_name=None, which the
# OWL header reads as "no one-click chip for this yet", not an error.
_SKJ_GATE_FILTER_NAME = {
    "install_date_missing": "missing_install_date",
    "job_at_risk": "at_risk",
}

# Gates already surfaced by DO NEXT ranks 1-2 (equipment, unscheduled WOs)
# — never re-picked as the rank-3 "top remaining gate" even when their own
# count is nonzero, so DO NEXT never lists the same problem twice.
_SKJ_GATE_EXCLUDED_FROM_RANK3 = ("equipment_blocked", "unscheduled_workorder_count")


def _skj_iso(value):
    """Render an Odoo Datetime (naive, always UTC by Odoo convention) as an
    ISO-8601 string with an explicit 'Z', so the OWL header can hand it
    straight to `new Date()` without reimplementing that convention
    client-side. None in, None out — provenance.computed_at is legitimately
    None when no task in the domain has ever been scored."""
    if not value:
        return None
    return fields.Datetime.to_datetime(value).isoformat() + "Z"


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
        self._sb_cc_route_release_flip(prev_release, cur_release)

    def _sb_cc_route_release_flip(self, previous_release, current_release):
        """Route a release-state flip into the Command Center exception queue.

        Detection already existed — this method's caller has been diffing release state
        every thirty minutes since it was written. What did not exist was delivery: it
        posted a chatter note and stopped, so whether the person who could act ever found
        out depended on them being a follower of that task and reading Chatter.

        Meanwhile `southbrook.command.exception` already carries the whole apparatus —
        owner assignment, severity ranking, `alert_notified` dedup so nothing re-spams,
        and a thirty-minute digest that delivers one grouped `message_notify` per owner to
        the Inbox and to email. And its taxonomy has always included a `job_blocked`
        type that NOTHING has ever created. A landing spot with a working conveyor behind
        it, never connected.

        So this is a wire, not a mechanism. Building a second digest here would have
        duplicated `_cron_send_alert_digest` exactly.

        Guarded on the model being present rather than on a manifest dependency:
        southbrook_command_center depends on southbrook_project_mrp, not on this module,
        so declaring it the other way would invert the graph. When Command Center is not
        installed the flip still posts its chatter note and nothing here runs.
        """
        self.ensure_one()
        if "southbrook.command.exception" not in self.env:
            return
        Exception_ = self.env["southbrook.command.exception"]
        if not Exception_._cc_hooks_enabled():
            return
        if previous_release == current_release:
            return

        if current_release != "blocked":
            # Recovered, or moved to review where a signature is all that is left.
            # Resolving rather than leaving it open is what stops the queue filling with
            # exceptions for jobs that unblocked themselves.
            Exception_._resolve_exception("job_blocked", "project.task", self.id)
            return

        blocking = self.southbrook_production_release_reason or ""
        owner = self.user_ids[:1] or self.project_id.user_id or self.env.user
        Exception_._upsert_exception(
            "job_blocked", "project.task", self.id,
            {
                "severity": "high",
                "severity_rank": 1,
                "owner_id": owner.id,
                "task_id": self.id,
                "company_id": self.company_id.id or self.env.company.id,
                "impact_summary": (
                    "%s is blocked for production release and cannot be scheduled."
                    % (self.display_name or "This job"))[:280],
                "recommended_action": (
                    "Open the job's Production Release tab and clear the outstanding "
                    "gates. Items a sign-off cannot clear need the shop floor, not an "
                    "approver — the reason line names which are which."
                ),
                "why_text": ("release state %s -> %s. %s"
                             % (previous_release or "(unset)", current_release, blocking)),
            },
            reactivate=True,
        )

    # ------------------------------------------------------------------
    # Kitchen Jobs board — card-level display fields (contract §2).
    # All three are UNSTORED: cards only ever render 12-100 tasks at once,
    # so there is no read_group/search cost worth trading store=True away
    # for — and skipping store=True means these can NEVER be reached for
    # by a search domain later without a ValueError, which is exactly the
    # discipline the payload contract in skj_board_intel below requires.
    # ------------------------------------------------------------------
    skj_customer_display = fields.Char(
        string="Customer (Board Display)",
        compute="_compute_skj_customer_display",
        help="Card line 1 customer name. Falls back to the originating "
             "sale.order's partner when the task itself carries none — a "
             "kitchen job spine created before sale_order.py started "
             "filling partner_id can have it unset even though the order "
             "plainly has a customer (see _kitchen_task_vals). False, not "
             "'', when neither side has one, so the board can render an "
             "explicit 'no customer in data' marker instead of a blank "
             "cell that looks like a rendering bug.",
    )

    @api.depends("partner_id.name", "x_southbrook_sale_order_id.partner_id.name")
    def _compute_skj_customer_display(self):
        for task in self:
            task.skj_customer_display = (
                task.partner_id.name
                or task.x_southbrook_sale_order_id.partner_id.name
                or False
            )

    skj_days_late = fields.Integer(
        string="Days Late",
        compute="_compute_skj_days_late",
        help="0 when there is no deadline or the deadline hasn't passed — "
             "the board never shows a negative 'late' number. date_deadline "
             "is a Datetime (odoo/addons/project/models/project_task.py), "
             "so this compares dates in the current user's context, not "
             "raw datetimes across timezones.",
    )

    @api.depends("date_deadline")
    def _compute_skj_days_late(self):
        today = fields.Date.context_today(self)
        for task in self:
            if not task.date_deadline:
                task.skj_days_late = 0
                continue
            days_late = (today - task.date_deadline.date()).days
            task.skj_days_late = days_late if days_late > 0 else 0

    skj_identity_needs_naming = fields.Boolean(
        string="Needs Naming",
        compute="_compute_skj_identity_needs_naming",
        help="True when the task's own name still carries a placeholder — "
             "'TBD' anywhere (the auto-name sale_order.py's "
             "_build_kitchen_task_name falls back to when it can't infer "
             "a door style/species from the order line), or a hand-typed "
             "name that never got past the generic 'Job: S...' stub. "
             "Surfaces jobs that need a human to type a real name before "
             "they reach the shop floor.",
    )

    @api.depends("name")
    def _compute_skj_identity_needs_naming(self):
        for task in self:
            name = task.name or ""
            task.skj_identity_needs_naming = (
                "TBD" in name or name.startswith("Job: S"))

    # ------------------------------------------------------------------
    # Kitchen Jobs board — skj_board_intel RPC (contract §1)
    # ------------------------------------------------------------------
    @api.model
    def skj_board_intel(self, domain):
        """Single RPC serving the Kitchen Jobs board header: KPI chips,
        DO NEXT, blocker Pareto, and provenance.

        ``domain`` is the board's CURRENT search domain, as sent by the OWL
        controller from ``searchModel.domain`` — already reflecting
        whichever chips/filters the user has active. It is never trusted
        alone: this method ANDs it onto ``_SKJ_BASE_DOMAIN`` before running
        anything, so a caller cannot widen the board past kitchen-
        orchestration spines by editing the client-side domain (SAFETY,
        contract §1).

        project.task counts run under the calling user's own rights — task
        ACLs already gate what they can see, and there is no reason this
        RPC should surface tasks the board itself wouldn't render. MO /
        work-order / maintenance-request aggregates (the equipment
        payload, and the DO NEXT rank-2 work-order domain) run sudo() —
        same posture as the Process Explorer bridge
        (southbrook_command_center/models/explorer_bridge.py): a user who
        can see a blocked task must be able to see WHY it's blocked even
        without direct read rights on maintenance.request.

        Every key below is ALWAYS present, and a zero or a None is a real
        answer, never an omission — "absence of data never renders as
        good" is the review's governing rule this whole payload exists to
        satisfy.
        """
        combined_domain = AND([_SKJ_BASE_DOMAIN, domain or []])
        tasks = self.search(combined_domain)
        task_ids = tasks.ids

        kpis = self._skj_kpis(combined_domain, tasks)
        equipment = self._skj_equipment_payload(tasks)
        pareto = self._skj_pareto(combined_domain)
        do_next = self._skj_do_next(kpis, equipment, pareto, task_ids)

        recomputed_ats = [
            d for d in tasks.mapped("readiness_last_recomputed_at") if d]
        computed_at = max(recomputed_ats) if recomputed_ats else None

        return {
            "kpis": kpis,
            "do_next": do_next,
            "pareto": pareto,
            "equipment": equipment,
            "provenance": {
                "computed_at": _skj_iso(computed_at),
                "cron_cadence": "15 min",
                "copy": _SKJ_PROVENANCE_COPY,
            },
            "server_now": _skj_iso(fields.Datetime.now()),
        }

    def _skj_kpis(self, combined_domain, tasks):
        """Header KPI chip counts (contract §1).

        Stored fields (readiness_decision, job_at_risk,
        install_date_missing, user_ids, date_deadline,
        unscheduled_workorder_count) go through search_count/_read_group
        against the DB — including ``late``, added by contract amendment
        after this method's first draft. unassigned_wos sums
        unassigned_workorder_count — UNSTORED — so it is summed by
        iterating ``tasks`` in Python instead; that field must never reach
        a search domain or read_group (contract §1)."""
        total = len(tasks)
        blocked = self.search_count(
            AND([combined_domain, [("readiness_decision", "=", "blocked")]]))
        review = self.search_count(
            AND([combined_domain, [("readiness_decision", "=", "review")]]))
        ready = self.search_count(
            AND([combined_domain, [("readiness_decision", "=", "ready")]]))
        # "info" and NULL both mean "not computed yet" — the header
        # collapses them into one "undecided" bucket. (The kanban
        # group_expand added in southbrook_project_mrp for contract §2
        # still shows NULL as its own column; that's a grouping concern,
        # not a KPI-count concern, so the two are allowed to disagree.)
        undecided = self.search_count(AND([combined_domain, [
            "|", ("readiness_decision", "=", False),
            ("readiness_decision", "=", "info"),
        ]]))
        at_risk = self.search_count(
            AND([combined_domain, [("job_at_risk", "=", True)]]))
        missing_install = self.search_count(
            AND([combined_domain, [("install_date_missing", "=", True)]]))
        unassigned = self.search_count(
            AND([combined_domain, [("user_ids", "=", False)]]))
        with_deadline = self.search_count(
            AND([combined_domain, [("date_deadline", "!=", False)]]))
        # Contract amendment (§1): "late" = date_deadline strictly before
        # TODAY's midnight — the same threshold skj_days_late uses (a
        # deadline dated today, even if the time-of-day has passed, is not
        # "late"; skj_days_late would report 0 for it too). Computed as a
        # datetime bound rather than comparing .date() per-row so this can
        # run as one search_count against the stored column, matching the
        # rest of this method's "stored fields -> search_count" rule.
        today = fields.Date.context_today(self)
        late_threshold = datetime.combine(today, dt_time.min)
        late = self.search_count(
            AND([combined_domain, [("date_deadline", "<", late_threshold)]]))

        rows = self._read_group(
            combined_domain, aggregates=["unscheduled_workorder_count:sum"])
        unscheduled_wos = (rows[0][0] or 0) if rows else 0

        # UNSTORED — never a search domain or read_group target. tasks is
        # already the fetched recordset for this domain, so mapped() here
        # is a Python-side sum over records already in memory.
        unassigned_wos = sum(tasks.mapped("unassigned_workorder_count"))

        days_late_values = [dl for dl in tasks.mapped("skj_days_late") if dl > 0]
        max_days_late = max(days_late_values) if days_late_values else None

        return {
            "total": total,
            "blocked": blocked,
            "review": review,
            "ready": ready,
            "undecided": undecided,
            "at_risk": at_risk,
            "missing_install": missing_install,
            "unassigned": unassigned,
            "unscheduled_wos": unscheduled_wos,
            "unassigned_wos": unassigned_wos,
            "max_days_late": max_days_late,
            "late": late,
            "deadline_coverage": [with_deadline, total],
        }

    def _skj_pareto(self, combined_domain):
        """Blocker Pareto — one entry per gate in _SKJ_GATE_TAXONOMY,
        always all ten (zeros included, contract §5). Ordered by count
        descending; Python's sort is stable, so gates tied on count keep
        the taxonomy's worst-first order as the tie-break."""
        entries = [
            {
                "gate": gate,
                "label": label,
                "count": self.search_count(AND([combined_domain, gate_domain])),
            }
            for gate, label, gate_domain in _SKJ_GATE_TAXONOMY
        ]
        entries.sort(key=lambda entry: entry["count"], reverse=True)
        return entries

    def _skj_equipment_payload(self, tasks):
        """Equipment gate detail (contract §1).

        For every task in ``tasks`` flagged equipment_blocked, walk
        workorder -> workcenter -> maintenance.equipment -> open
        maintenance.request exactly like southbrook_project_mrp's own
        ``_compute_equipment_readiness`` does — that method is the single
        source of truth for what "equipment blocked" means; re-deriving
        from it here (rather than only reading the boolean) is what lets
        the board name WHICH work center and WHICH request, not just that
        something, somewhere, is down.

        sudo() throughout: a user who can see a blocked kitchen task must
        be able to see why, even without direct read rights on
        maintenance.equipment / maintenance.request (same posture as the
        Process Explorer bridge)."""
        blocked_tasks = tasks.filtered("equipment_blocked").sudo()
        if not blocked_tasks:
            return {"blocked_workcenters": []}

        Equipment = self.env["maintenance.equipment"].sudo()
        workcenters = blocked_tasks.production_ids.workorder_ids.workcenter_id
        entries = []
        for wc in workcenters.sorted("name"):
            equipment = Equipment.search([("workcenter_id", "=", wc.id)])
            open_requests = equipment.mapped("maintenance_ids").filtered(
                lambda req: not req.stage_id.done)
            if not open_requests:
                continue
            jobs_blocked = len(blocked_tasks.filtered(
                lambda t, wc=wc: wc in t.production_ids.workorder_ids.workcenter_id))
            top_request = open_requests.sorted("id")[:1]
            entries.append({
                "id": wc.id,
                "name": wc.name,
                "request_id": top_request.id if top_request else None,
                "request_name": top_request.name if top_request else None,
                "jobs_blocked": jobs_blocked,
            })
        entries.sort(key=lambda entry: (-entry["jobs_blocked"], entry["name"]))
        return {"blocked_workcenters": entries}

    def _skj_do_next(self, kpis, equipment, pareto, task_ids):
        """Deterministic DO NEXT ranking (contract §1). Equipment first —
        it blocks work outright and the fix is a single maintenance
        action. Unscheduled work orders second — a scheduling gap is one
        click to fix in the Work Orders view. Then whichever remaining
        gate affects the most jobs. Never more than 3 items; ranks are
        always 1..N with no gaps, regardless of which earlier steps
        fired."""
        items = []

        blocked_wcs = equipment["blocked_workcenters"]
        if blocked_wcs:
            top_wc = blocked_wcs[0]
            items.append({
                "rank": len(items) + 1,
                "label": "Clear %s maintenance request" % top_wc["name"],
                "detail": "unblocks %d jobs" % top_wc["jobs_blocked"],
                "count": top_wc["jobs_blocked"],
                "action": {
                    "type": "act_window",
                    "res_model": "maintenance.request",
                    "domain": [("id", "=", top_wc["request_id"])],
                    "filter_name": None,
                },
            })

        unscheduled_wos = kpis["unscheduled_wos"]
        if unscheduled_wos > 0:
            items.append({
                "rank": len(items) + 1,
                "label": "Schedule %d work orders" % unscheduled_wos,
                "detail": "no planned start date set",
                "count": unscheduled_wos,
                "action": {
                    "type": "act_window",
                    "res_model": "mrp.workorder",
                    # Backlink VERIFIED: mrp.production.project_task_id
                    # (southbrook_project_mrp/models/mrp_production.py:8).
                    "domain": [
                        ("date_start", "=", False),
                        ("state", "not in", ["done", "cancel"]),
                        ("production_id.project_task_id", "in", task_ids),
                    ],
                    "filter_name": None,
                },
            })

        for gate in pareto:
            if gate["gate"] in _SKJ_GATE_EXCLUDED_FROM_RANK3 or gate["count"] <= 0:
                continue
            items.append({
                "rank": len(items) + 1,
                "label": "Clear %s on %d jobs" % (gate["label"], gate["count"]),
                "detail": "%d jobs affected" % gate["count"],
                "count": gate["count"],
                "action": {
                    "type": "filter",
                    "res_model": None,
                    "domain": None,
                    "filter_name": _SKJ_GATE_FILTER_NAME.get(gate["gate"]),
                },
            })
            break

        return items
