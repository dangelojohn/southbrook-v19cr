# SPDX-License-Identifier: LGPL-3.0-only
from collections import Counter
from datetime import timedelta

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# project.task.stage names (lowercased) that imply production has begun / finished.
_STARTED_STAGE_HINTS = ("cutting", "machining", "assembly", "finishing")
_DELIVERY_STAGE_HINTS = ("delivery", "install")
# mrp.production states that mean the MO has actually started.
_MO_STARTED_STATES = ("progress", "to_close", "done")
_FAMILY_BY_PREFIX = {
    "SB-WALL": "Wall",
    "SB-BASE": "Base",
    "SB-DRAWER": "Drawer",
    "SB-SINK": "Sink Base",
    "SB-TALL": "Tall",
    "SB-CORNER": "Corner",
    "SB-VANITY": "Vanity",
    "SB-ACCESSORY": "Accessory",
    "SB-WORKTOP": "Worktop",
}
_REQUIRED_CABINET_SPEC_LABELS = {
    "x_southbrook_material_species": "Material/species",
    "x_southbrook_hardware_specs": "Hardware specs",
    "southbrook_door_style": "Door style",
    "southbrook_finish": "Finish",
}


def _first_meaningful_line(*texts):
    for text in texts:
        for line in (text or "").splitlines():
            line = line.strip()
            if line and not line.lower().startswith("no "):
                return line
    return ""


class ProjectTask(models.Model):
    _inherit = "project.task"

    # The five release sign-offs, and the audit pair each one stamps.
    _RELEASE_SIGNOFFS = {
        "southbrook_release_cad_approved": "cad_approved",
        "southbrook_release_cutlist_approved": "cutlist_approved",
        "southbrook_release_bom_verified": "bom_verified",
        "southbrook_release_crew_reserved": "crew_reserved",
        "southbrook_release_equipment_available": "equipment_available",
    }
    _RELEASE_SIGNOFF_GROUP = "mrp.group_mrp_user"

    def write(self, vals):
        self._southbrook_check_readiness_stage_gate(vals)
        self._southbrook_check_release_signoff_rights(vals)
        vals = self._southbrook_stamp_release_signoffs(vals)
        return super().write(vals)

    def _southbrook_check_release_signoff_rights(self, vals):
        """Only a manufacturing user may assert a release sign-off.

        These five booleans gate production release, and until now anyone with ordinary
        write access to a task could flip one straight over RPC — no group, no signer, no
        trace beyond chatter. That is the "caller-declared privilege" shape: the system
        believed whoever asked.

        Gated on mrp.group_mrp_user rather than on the group whose NAME fits. The obvious
        candidate, southbrook_mrp_pm.group_floor_manager, has ten members and every one of
        them is a PORTAL user who cannot open a backend list at all; group_southbrook_
        production_approver has no members whatsoever. Gating on either would have locked
        out the people doing the work. group_mrp_manager implies group_mrp_user, so both
        manufacturing groups pass.
        """
        if self.env.su:
            return
        asserted = [f for f in self._RELEASE_SIGNOFFS if vals.get(f)]
        if not asserted:
            return
        if self.env.user.has_group(self._RELEASE_SIGNOFF_GROUP):
            return
        raise AccessError(_(
            "Production release sign-off requires the Manufacturing / User group. "
            "You tried to set: %(fields)s.\n\n"
            "This gate exists because these five booleans release a job to the shop "
            "floor. If you should be able to sign these off, ask an administrator to add "
            "you to Manufacturing / User — do not work around it by asking someone else "
            "to tick the box, because the record will then name them and not you.",
            fields=", ".join(asserted)))

    def _southbrook_stamp_release_signoffs(self, vals):
        """Record who signed and when, on the transition into True.

        Only on the flip TO True: re-saving a form that already had the box ticked must
        not restamp it with a later user, or the audit trail records whoever touched the
        record last rather than whoever made the decision.
        """
        asserted = [f for f in self._RELEASE_SIGNOFFS if vals.get(f)]
        if not asserted:
            return vals
        now = fields.Datetime.now()
        for field in asserted:
            suffix = self._RELEASE_SIGNOFFS[field]
            by_field = "southbrook_release_%s_by" % suffix
            at_field = "southbrook_release_%s_at" % suffix
            # Only stamp where it is not already a sign-off, i.e. at least one record in
            # this set is flipping rather than being re-saved.
            if any(not rec[field] for rec in self):
                vals.setdefault(by_field, self.env.user.id)
                vals.setdefault(at_field, now)
        return vals

    def _southbrook_check_readiness_stage_gate(self, vals):
        """Block kitchen jobs from entering production stages before ready."""
        if self.env.context.get("southbrook_skip_readiness_stage_gate"):
            return
        if "stage_id" not in vals or not vals.get("stage_id"):
            return
        stage = self.env["project.task.type"].browse(vals["stage_id"]).exists()
        if not stage:
            return
        stage_name = (stage.name or "").lower()
        production_stage = any(
            hint in stage_name
            for hint in (_STARTED_STAGE_HINTS + _DELIVERY_STAGE_HINTS)
        )
        if not production_stage:
            return
        for task in self:
            if not task._southbrook_is_kitchen_job():
                continue
            if task.manufacturing_readiness_state == "ready":
                continue
            reason = (
                _first_meaningful_line(
                    task.manufacturing_blocker_summary,
                    task.southbrook_production_release_reason,
                    task.manufacturing_warning_summary,
                )
                or "Manufacturing readiness is not ready."
            )
            raise UserError(_(
                "Cannot move '%(task)s' to '%(stage)s' while readiness is "
                "%(state)s. %(reason)s"
            ) % {
                "task": task.display_name,
                "stage": stage.display_name,
                "state": task.manufacturing_readiness_state or "unknown",
                "reason": reason,
            })

    def _southbrook_is_kitchen_job(self):
        self.ensure_one()
        if self.production_ids:
            return True
        if "x_southbrook_sale_order_id" in self._fields:
            return bool(self.x_southbrook_sale_order_id)
        return False

    # --- B1: a real person owns the job -------------------------------------
    pm_id = fields.Many2one(
        "res.users", string="Responsible PM", tracking=True,
        help="The production manager / PM accountable for this customer job. "
             "Distinct from per-task assignees; this is who the MRP manager "
             "holds responsible.")

    # --- T1.1: the job's Manufacturing Orders -------------------------------
    production_ids = fields.One2many(
        "mrp.production", "project_task_id", string="Manufacturing Orders",
        help="Every MO that makes up this customer job (base, worktop, …).")
    # R4 (2026-06-30): store=True so the SQL builder can resolve
    # `('production_count', '>', 0)` in the Bottleneck Contention domain
    # (project_task_views.xml line 842) and the kanban "Production Count"
    # group-by. v19 hard-rejects search/group-by on unstored computed
    # fields with `Cannot convert project.task.production_count to SQL
    # because it is not stored`. Same pattern as R3 PR #27's
    # `current_bottleneck_workcenter_id` fix.
    # The `_compute_mrp_status` chain already depends on `production_ids`
    # (line 679), which is the only source `len(mos)` reads — no depends
    # change required.
    production_count = fields.Integer(
        string="MO Count", compute="_compute_mrp_status", store=True)

    # --- T1.2 / B3: read-only build status, sourced from mrp/stock ----------
    mo_reference = fields.Char(
        string="MO Reference(s)", compute="_compute_mrp_status")
    mo_product_summary = fields.Char(
        string="Products / SKUs", compute="_compute_mrp_status")
    mo_state_summary = fields.Char(
        string="MO Status", compute="_compute_mrp_status",
        help="Aggregate of the linked MOs' states, e.g. '4 confirmed / 2 in progress'.")
    components_available = fields.Selection(
        [("none", "No MOs"),
         ("ready", "All components available"),
         ("partial", "Partially available"),
         ("waiting", "Waiting on components")],
        string="Components", compute="_compute_mrp_status",
        help="'All available' only when EVERY linked MO is fully reserved.")
    job_at_risk = fields.Boolean(
        string="At Risk", compute="_compute_mrp_status", store=True, index=True,
        help="Job is behind or blocked (components waiting on a started MO, "
             "or an MO past its deadline).")
    job_risk_reason = fields.Char(
        string="Risk Reason", compute="_compute_mrp_status")

    # --- B2: cost roll-ups (actual + estimated), shown with currency --------
    job_industrial_cost = fields.Monetary(
        string="Job Cost (Actual, from MOs)", compute="_compute_mrp_status",
        currency_field="company_currency_id",
        help="Sum of the linked MOs' actual industrial cost (Production Costs "
             "tab). Zero until the MOs are costed.")
    job_estimated_cost = fields.Monetary(
        string="Job Cost (Estimated)", compute="_compute_mrp_status",
        currency_field="company_currency_id",
        help="Estimated cost = sum of each MO's product standard cost × qty — "
             "meaningful even before the MOs are costed.")
    company_currency_id = fields.Many2one(
        related="company_id.currency_id", string="Company Currency")

    # --- T1.2 polish: surfaced from the MO's other tabs ---------------------
    # The customer's REQUESTED install date, captured at intake. Deliberately a separate
    # field from `job_install_due` below, not a replacement for it: that one is a MIN()
    # rollup over the linked MOs' own dates and is correct as a rollup — making it writable
    # would desync it the moment any MO moved. Before this field there was nowhere to
    # record what the customer asked for, because a brand-new job has no MOs yet.
    x_southbrook_target_install_date = fields.Date(
        string="Target Install Date",
        tracking=True,
        help="What the customer asked for, recorded at intake. The Earliest Install Due "
             "below is computed from the manufacturing orders once they exist; when the "
             "two disagree, that is a scheduling problem worth seeing.")

    job_install_due = fields.Date(
        string="Earliest Install Due", compute="_compute_mrp_status",
        store=True, index=True)
    job_cad_status = fields.Char(
        string="CAD Status", compute="_compute_mrp_status")
    job_next_action = fields.Char(
        string="Next Action", compute="_compute_mrp_status")

    # --- Phase 1: command-center context aliases ---------------------------
    # These fields intentionally reuse existing Southbrook/Odoo sources. They
    # provide stable names for the 9/10 Project command-center views without
    # introducing a parallel job model.
    source_order_id = fields.Many2one(
        "sale.order",
        string="Source Sales Order",
        compute="_compute_phase1_job_context",
        store=True,
        readonly=True,
        index=True,
    )
    source_order_name = fields.Char(
        string="Source",
        compute="_compute_phase1_job_context",
        store=True,
        readonly=True,
        index=True,
    )
    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        compute="_compute_phase1_job_context",
        store=True,
        readonly=True,
        index=True,
    )
    pm_phase = fields.Char(
        string="PM Phase",
        compute="_compute_phase1_job_context",
        store=True,
        readonly=True,
    )
    install_due_date = fields.Date(
        string="Install Due",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    manufacturing_reality = fields.Char(
        string="Manufacturing Reality",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    readiness_decision = fields.Selection(
        [
            ("ready", "Ready"),
            ("review", "Review"),
            ("blocked", "Blocked"),
            ("info", "Info"),
        ],
        string="Readiness Decision",
        compute="_compute_phase1_operational_context",
        store=True,
        readonly=True,
        # Kitchen Jobs board (contract §2): the kanban must render all four
        # columns even when a filtered domain has zero tasks in one or more
        # of them — e.g. "every open job is blocked" should still show
        # empty Ready/Review/Info columns, not silently collapse to one.
        # group_expand=True on a Selection field is a native Odoo feature
        # (see sale.order.state, fleet.vehicle.log.services.stage_id) —
        # no custom _group_expand_* method needed.
        group_expand=True,
    )
    readiness_score = fields.Integer(
        string="Readiness Score",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    risk_level = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        string="Risk",
        compute="_compute_phase1_operational_context",
        # store=True so the Floor Analytics pivot can group by the four-band
        # gradient (low/medium/high/critical) instead of the binary job_at_risk.
        # Safe to store: readiness_decision already rides this exact same compute
        # with store=True and no staleness bug, so the @api.depends is proven
        # complete. The compute's read of self.top_blocker is intra-method (it
        # reads an output THIS pass already assigned), not an external dependency,
        # so it needs no depends entry. Indexed because analytics filters on it.
        store=True,
        index=True,
        readonly=True,
    )
    risk_reason = fields.Char(
        string="Risk Reason",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    top_blocker = fields.Char(
        string="Top Blocker",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    next_best_action = fields.Char(
        string="Next Best Action",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    linked_mo_count = fields.Integer(
        string="MOs",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    linked_wo_count = fields.Integer(
        string="WOs",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    unscheduled_wo_count = fields.Integer(
        string="Unscheduled WOs",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    unassigned_wo_count = fields.Integer(
        string="Unassigned WOs",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    # R3 PR #27 fix (2026-06-30): store=True is required so the
    # W029 Bottleneck Contention view can put this column in a search
    # domain AND group-by it from the search filter. Odoo 19's SQL
    # builder raises
    #   ValueError: Cannot convert ... to SQL because it is not stored
    # for unstored compute fields the moment a user clicks Group-By in
    # the kanban — the test_w029_* cases pin both surfaces (action
    # domain + search-view group_by). Indexed because the group-by +
    # domain together hit the column on every planner refresh. The
    # @api.depends chain on the shared compute
    # (_compute_phase1_operational_context) already lists
    # production_ids.workorder_ids.workcenter_id and
    # production_ids.workorder_ids.duration_expected — the two source
    # fields _southbrook_current_bottleneck_workcenter() reads.
    current_bottleneck_workcenter_id = fields.Many2one(
        "mrp.workcenter",
        string="Current Bottleneck Work Center",
        compute="_compute_phase1_operational_context",
        store=True,
        index=True,
        readonly=True,
    )
    cabinet_family_summary = fields.Char(
        string="Cabinet Family Mix",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    southbrook_cabinet_family_progress = fields.Text(
        string="Cabinet Family Progress",
        compute="_compute_phase1_operational_context",
        readonly=True,
    )
    job_type = fields.Selection(
        [
            ("full_kitchen", "Full Kitchen"),
            ("vanity", "Vanity"),
            ("pantry", "Pantry"),
            ("repair", "Repair"),
            ("warranty", "Warranty / Remake"),
            ("single_cabinet", "Custom Single Cabinet"),
            ("worktop", "Worktop"),
        ],
        string="Job Type",
        default="full_kitchen",
        tracking=True,
    )
    southbrook_job_template_id = fields.Many2one(
        "southbrook.project.job.template",
        string="Job Template",
        domain="[('active', '=', True)]",
        tracking=True,
    )
    southbrook_door_style = fields.Selection(
        [
            ("shaker", "Shaker"),
            ("slab", "Slab"),
            ("raised_panel", "Raised Panel"),
            ("recessed_panel", "Recessed Panel"),
            ("custom", "Custom"),
        ],
        string="Door Style",
        tracking=True,
    )
    southbrook_finish = fields.Selection(
        [
            ("painted", "Painted"),
            ("stained", "Stained"),
            ("clear", "Clear Coat"),
            ("laminate", "Laminate / Melamine"),
            ("unfinished", "Unfinished"),
            ("custom", "Custom"),
        ],
        string="Finish",
        tracking=True,
    )
    southbrook_drawer_slide_type = fields.Selection(
        [
            ("undermount_soft_close", "Undermount Soft-Close"),
            ("side_mount_soft_close", "Side-Mount Soft-Close"),
            ("side_mount_standard", "Side-Mount Standard"),
            ("push_to_open", "Push-to-Open"),
            ("custom", "Custom"),
        ],
        string="Drawer Slide Type",
    )
    southbrook_hinge_type = fields.Selection(
        [
            ("concealed_soft_close", "Concealed Soft-Close"),
            ("concealed_standard", "Concealed Standard"),
            ("inset", "Inset"),
            ("specialty", "Specialty"),
            ("custom", "Custom"),
        ],
        string="Hinge Type",
    )
    southbrook_worktop_dependency = fields.Char(
        string="Counter / Worktop Dependency",
        help="Countertop, worktop, sink, or template dependency that affects "
             "release or install readiness.")
    southbrook_final_measurement_notes = fields.Text(
        string="Final Measurement Notes")
    southbrook_cad_package_link = fields.Char(
        string="CAD Package Link")
    southbrook_cutlist_reference = fields.Char(
        string="Cutlist Reference")
    southbrook_specs_complete = fields.Boolean(
        string="Cabinet Specs Complete",
        compute="_compute_southbrook_specs_complete",
        store=True,
        index=True,
        readonly=True,
    )
    southbrook_release_cad_approved = fields.Boolean(
        string="CAD Approved", tracking=True)
    southbrook_release_cutlist_approved = fields.Boolean(
        string="Cutlist Approved", tracking=True)
    southbrook_release_bom_verified = fields.Boolean(
        string="BoM Verified", tracking=True)
    southbrook_release_crew_reserved = fields.Boolean(
        string="Crew Assigned / Reserved", tracking=True)
    southbrook_release_equipment_available = fields.Boolean(
        string="Critical Equipment Available", tracking=True)
    southbrook_production_release_state = fields.Selection(
        [
            ("ready", "Ready"),
            ("review", "Review"),
            ("blocked", "Blocked"),
            ("info", "Info"),
        ],
        string="Production Release",
        compute="_compute_southbrook_production_release",
        # store=True → searchable via the indexed column; the custom search=
        # did a full-table search([]).filtered() and is removed (see the note
        # on manufacturing_readiness_state).
        store=True,
        readonly=True,
    )
    southbrook_production_release_reason = fields.Char(
        string="Production Release Reason",
        compute="_compute_southbrook_production_release",
        readonly=True,
    )
    southbrook_release_review_since = fields.Datetime(
        string="In Review Since",
        compute="_compute_southbrook_production_release",
        store=True,
        index=True,
        readonly=True,
        help="When this job most recently ENTERED `review` — a job waiting only on "
             "sign-offs. Cleared the moment it leaves `review` in either direction "
             "(back to blocked, or on to ready), so it always measures the CURRENT "
             "stretch of pure signature latency, not lifetime-in-review. Indexed "
             "because the sign-off escalation cron filters on it every hour.")
    cad_cutlist_review_required = fields.Boolean(
        string="Needs CAD / Cutlist",
        compute="_compute_phase3_queue_flags",
        store=True,
        index=True,
        readonly=True,
    )
    install_date_missing = fields.Boolean(
        string="Install Date Missing",
        compute="_compute_phase3_queue_flags",
        store=True,
        index=True,
        readonly=True,
    )
    # WHO signed, and WHEN. The five booleans above were criticised in the audit as
    # "governance theatre" — a sign-off with no signer, no timestamp and no role. The
    # timestamp and signer are the cheap half and carry no risk of locking anybody out, so
    # they are captured unconditionally in write() below.
    southbrook_release_cad_approved_by = fields.Many2one(
        "res.users", string="CAD Approved By", readonly=True, copy=False)
    southbrook_release_cad_approved_at = fields.Datetime(
        string="CAD Approved On", readonly=True, copy=False)
    southbrook_release_cutlist_approved_by = fields.Many2one(
        "res.users", string="Cutlist Approved By", readonly=True, copy=False)
    southbrook_release_cutlist_approved_at = fields.Datetime(
        string="Cutlist Approved On", readonly=True, copy=False)
    southbrook_release_bom_verified_by = fields.Many2one(
        "res.users", string="BoM Verified By", readonly=True, copy=False)
    southbrook_release_bom_verified_at = fields.Datetime(
        string="BoM Verified On", readonly=True, copy=False)
    southbrook_release_crew_reserved_by = fields.Many2one(
        "res.users", string="Crew Reserved By", readonly=True, copy=False)
    southbrook_release_crew_reserved_at = fields.Datetime(
        string="Crew Reserved On", readonly=True, copy=False)
    southbrook_release_equipment_available_by = fields.Many2one(
        "res.users", string="Equipment Confirmed By", readonly=True, copy=False)
    southbrook_release_equipment_available_at = fields.Datetime(
        string="Equipment Confirmed On", readonly=True, copy=False)

    southbrook_site_measurement_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("received", "Received"),
            ("waived", "Waived"),
        ],
        string="Site Measurement",
        default="pending",
        tracking=True,
    )
    southbrook_delivery_address = fields.Char(
        string="Delivery Address")
    southbrook_install_contact = fields.Char(
        string="Install Contact")
    southbrook_site_access_notes = fields.Text(
        string="Site Access Notes")
    southbrook_install_deficiency_notes = fields.Text(
        string="Install Deficiency / Punch Notes")
    southbrook_quality_issue_summary = fields.Text(
        string="Quality / Remake Summary",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_remake_task_ids = fields.Many2many(
        "project.task",
        "southbrook_project_mrp_remake_task_rel",
        "task_id",
        "remake_task_id",
        string="Warranty / Remake Tasks",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_remake_task_count = fields.Integer(
        string="Warranty / Remake Tasks",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_rework_workorder_ids = fields.Many2many(
        "mrp.workorder",
        "southbrook_project_mrp_rework_wo_rel",
        "task_id",
        "workorder_id",
        string="Rework Work Orders",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_rework_workorder_count = fields.Integer(
        string="Rework WOs",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_scrap_ids = fields.Many2many(
        "stock.scrap",
        "southbrook_project_mrp_scrap_rel",
        "task_id",
        "scrap_id",
        string="Scrap Records",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_scrap_count = fields.Integer(
        string="Scrap",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_unbuild_ids = fields.Many2many(
        "mrp.unbuild",
        "southbrook_project_mrp_unbuild_rel",
        "task_id",
        "unbuild_id",
        string="Unbuild / Remake Records",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_unbuild_count = fields.Integer(
        string="Unbuilds",
        compute="_compute_southbrook_quality_visibility",
        readonly=True,
    )
    southbrook_pack_label_complete = fields.Boolean(
        string="Pack / Label Complete", tracking=True)
    southbrook_qc_complete = fields.Boolean(
        string="QC Complete", tracking=True)
    southbrook_delivery_staged = fields.Boolean(
        string="Delivery Staged", tracking=True)
    southbrook_install_readiness_state = fields.Selection(
        [
            ("ready", "Ready"),
            ("review", "Review"),
            ("blocked", "Blocked"),
            ("info", "Info"),
        ],
        string="Install Readiness",
        compute="_compute_southbrook_install_readiness",
        # store=True → searchable via the indexed column; the custom search=
        # did a full-table search([]).filtered() and is removed (see the note
        # on manufacturing_readiness_state).
        store=True,
        readonly=True,
    )
    southbrook_install_readiness_reason = fields.Char(
        string="Install Readiness Reason",
        compute="_compute_southbrook_install_readiness",
        readonly=True,
    )
    pm_stage_mismatch = fields.Boolean(
        string="PM Stage Mismatch",
        compute="_compute_phase3_queue_flags",
        store=True,
        index=True,
        readonly=True,
    )

    # --- B4: ECO visibility -------------------------------------------------
    eco_count = fields.Integer(
        string="ECO Count", compute="_compute_eco",
        help="Engineering changes touching this job's MO BoMs.")
    eco_pending_count = fields.Integer(
        string="Pending ECOs", compute="_compute_eco",
        help="ECOs not yet applied (state=open) — a spec change in flight.")

    # --- B5: stage <-> MO coherence -----------------------------------------
    stage_mo_divergence = fields.Boolean(
        string="Stage/MO Mismatch", compute="_compute_stage_coherence",
        help="The project stage and the MOs' production states disagree "
             "(e.g. stage says Finishing but no MO has started).")
    stage_mo_note = fields.Char(
        string="Stage/MO Note", compute="_compute_stage_coherence")

    # --- TASK 5: Waste / variance rollup -----------------------------------
    # All sums read from the existing x_sbk_* Kitchen Metrics fields on
    # mrp.workorder. No costing engine — just aggregation across the
    # job's WOs.
    job_expected_min = fields.Float(
        string="Expected (min)", compute="_compute_kitchen_metrics_rollup")
    job_variance_min = fields.Float(
        string="Duration Variance (min)",
        compute="_compute_kitchen_metrics_rollup",
        help="Σ x_sbk_variance_min across the job's work orders.")
    job_actual_cost = fields.Monetary(
        string="Actual Cost (from WOs)",
        compute="_compute_kitchen_metrics_rollup",
        currency_field="company_currency_id")
    job_cost_variance = fields.Monetary(
        string="Cost Variance",
        compute="_compute_kitchen_metrics_rollup",
        currency_field="company_currency_id")
    job_rework_count = fields.Integer(
        string="Rework Count",
        compute="_compute_kitchen_metrics_rollup")
    job_rework_cost = fields.Monetary(
        string="Rework Cost",
        compute="_compute_kitchen_metrics_rollup",
        currency_field="company_currency_id")
    job_downtime_min = fields.Float(
        string="Downtime (min)",
        compute="_compute_kitchen_metrics_rollup")
    job_downtime_cost = fields.Monetary(
        string="Downtime Cost",
        compute="_compute_kitchen_metrics_rollup",
        currency_field="company_currency_id")

    # --- TASK 4: Crew indicator --------------------------------------------
    crew_summary = fields.Char(
        string="Crew (from MOs + WOs)",
        compute="_compute_crew",
        help="Unique users assigned across the job's MOs (Responsible) "
             "and work orders (working_user_ids / last_working_user_id). "
             "Read-only; no auto-assignment.")
    unassigned_mo_count = fields.Integer(
        string="# Unassigned MOs", compute="_compute_crew",
        help="MOs whose user_id (Responsible) is blank.")
    unassigned_workorder_count = fields.Integer(
        string="# Unassigned WOs", compute="_compute_crew",
        help="Work orders with no working_user_ids AND no "
             "last_working_user_id — i.e. nobody has ever touched them.")
    crew_gap = fields.Boolean(
        string="Crew Gap", compute="_compute_crew",
        store=True, index=True,
        help="At least one MO or WO has no operator assigned.")

    # --- TASK 3: Work-center load summary ----------------------------------
    workcenter_load_summary = fields.Text(
        string="Workcenter Load",
        compute="_compute_workcenter_load",
        help="Per-work-center booked minutes across the job's work orders, "
             "compared against the work center's daily capacity proxy "
             "(resource_calendar hours/day × 60). Operators read this to "
             "spot bottlenecks without opening a Gantt.")
    workcenter_over_capacity = fields.Boolean(
        string="WC Over Capacity",
        compute="_compute_workcenter_load",
        store=True, index=True,
        help="True when any work center's booked minutes exceed its "
             "daily capacity proxy.")

    # --- TASK 2: Work-order rollup -----------------------------------------
    workorder_ids = fields.Many2many(
        "mrp.workorder",
        compute="_compute_workorder_rollup",
        string="Work Orders",
        help="Every work order across the job's linked MOs.")
    workorder_count = fields.Integer(
        string="# Work Orders", compute="_compute_workorder_rollup")
    unscheduled_workorder_count = fields.Integer(
        string="# Not Scheduled", compute="_compute_workorder_rollup",
        store=True,
        help="Work orders with no planned start date — the MRP scheduler "
             "needs to set these in the Work Orders view.")
    workorder_summary = fields.Char(
        string="Work Orders Summary", compute="_compute_workorder_rollup",
        help="One-line summary, e.g. '47 WOs / 47 not scheduled'.")
    manufacturing_calculation_count = fields.Integer(
        string="# Calculations",
        compute="_compute_manufacturing_calculations",
        help="Manufacturing Intelligence checks linked to this job's MOs.")
    manufacturing_readiness_score = fields.Integer(
        string="Readiness Score", compute="_compute_manufacturing_readiness")
    manufacturing_readiness_state = fields.Selection(
        [("ready", "Ready"), ("review", "Review"), ("blocked", "Blocked")],
        string="Readiness Decision",
        compute="_compute_manufacturing_readiness",
        # store=True makes the column directly searchable; a custom search=
        # here forced search([]).filtered() over the whole task table on every
        # filter click (the most-used filter in the module), discarding the
        # index. Removed — the stored column handles all operators natively.
        store=True)
    manufacturing_waterfall_summary = fields.Text(
        string="Waterfall Readiness",
        compute="_compute_manufacturing_readiness")
    manufacturing_blocker_summary = fields.Text(
        string="Start Blockers",
        compute="_compute_manufacturing_readiness")
    manufacturing_warning_summary = fields.Text(
        string="Manager Review",
        compute="_compute_manufacturing_readiness")
    manufacturing_info_summary = fields.Text(
        string="Efficiency Prompts",
        compute="_compute_manufacturing_readiness")
    readiness_line_ids = fields.One2many(
        "southbrook.project.readiness.line",
        "task_id",
        string="Readiness Evidence",
        readonly=True,
        copy=False,
        help="Deterministic readiness checks with reason, evidence, and the "
             "recommended PM action.")
    readiness_line_count = fields.Integer(
        string="# Readiness Checks",
        compute="_compute_readiness_line_count")

    # --- TASK 6: Material / procurement readiness ----------------------------
    material_ready_count = fields.Integer(
        string="# MOs Available", compute="_compute_material_readiness")
    material_partial_count = fields.Integer(
        string="# MOs Partially Available",
        compute="_compute_material_readiness")
    material_unavailable_count = fields.Integer(
        string="# MOs Unavailable", compute="_compute_material_readiness")
    material_at_risk = fields.Boolean(
        string="Material At Risk", compute="_compute_material_readiness",
        store=True, index=True,
        help="At least one linked MO is not fully component-available.")
    material_readiness_summary = fields.Text(
        string="Material Readiness", compute="_compute_material_readiness")
    procurement_order_ids = fields.Many2many(
        "purchase.order",
        compute="_compute_material_readiness",
        string="Related Procurement",
        help="Purchase orders linked to this job's MOs or component moves.")
    procurement_count = fields.Integer(
        string="# Procurement Orders", compute="_compute_material_readiness")
    procurement_summary = fields.Text(
        string="Procurement Coverage", compute="_compute_material_readiness")

    # --- TASK 7: Tooling / equipment readiness -------------------------------
    maintenance_request_ids = fields.Many2many(
        "maintenance.request",
        compute="_compute_equipment_readiness",
        string="Open Maintenance Requests",
        help="Open maintenance requests on equipment attached to this job's "
             "work centers.")
    maintenance_request_count = fields.Integer(
        string="# Open Maintenance", compute="_compute_equipment_readiness")
    # Stored ONLY because models/maintenance_request.py now invalidates it from the side
    # that actually changes. The compute reaches maintenance.request through a REVERSE
    # relation no @api.depends can express, so on its own a stored value would freeze —
    # an intermittent false all-clear on equipment availability, the exact bug class this
    # release exists to remove. Do not add store=True to a field shaped like this without
    # the matching hook; do not delete the hook and leave this stored.
    equipment_blocked = fields.Boolean(
        string="Equipment Blocked", compute="_compute_equipment_readiness",
        store=True, index=True)
    equipment_readiness_summary = fields.Text(
        string="Equipment Readiness", compute="_compute_equipment_readiness")

    # ------------------------------------------------------------------------
    # `x_sbk_install_due_date` is the field job_install_due is a MIN() of, and it is
    # deliberately NOT listed here: it belongs to southbrook_mrp_kitchen_workcenters,
    # which is a SIBLING module, not an ancestor. Naming it makes this module refuse to
    # load anywhere that one is absent — verified, it raises "Dependency field
    # 'x_sbk_install_due_date' not found in model mrp.production" at registry build.
    # The trigger therefore lives in the module that owns the field, as a write() hook on
    # mrp.production. Without either, a user setting the install date on an MO would not
    # update the task, and Install Risk would keep reporting "install date missing" on a
    # job whose date had just been entered.
    #
    # NOTE ON TIME: this compute also compares MO deadlines against TODAY, and wall-clock
    # cannot be a dependency. Stored fields driven partly by "now" do not self-heal, so
    # `_cron_retick_time_sensitive_readiness` below forces the re-evaluation. Without it,
    # storing job_at_risk would silently reintroduce the false all-clear this branch
    # exists to remove.
    @api.depends("production_ids", "production_ids.state",
                 "production_ids.reservation_state", "production_ids.date_deadline")
    def _compute_mrp_status(self):
        today = fields.Date.context_today(self)
        for task in self:
            mos = task.production_ids
            task.production_count = len(mos)
            if not mos:
                task.mo_reference = ""
                task.mo_product_summary = ""
                task.mo_state_summary = ""
                task.components_available = "none"
                task.job_industrial_cost = 0.0
                task.job_estimated_cost = 0.0
                task.job_install_due = False
                task.job_cad_status = ""
                task.job_next_action = ""
                task.job_at_risk = False
                task.job_risk_reason = ""
                continue

            task.mo_reference = ", ".join(mos.mapped("name"))
            task.mo_product_summary = ", ".join(
                m.product_id.default_code or m.product_id.display_name
                for m in mos)

            # Readable state roll-up: "4 confirmed / 2 in progress".
            labels = dict(mos._fields["state"]._description_selection(self.env))
            states = Counter(mos.mapped("state"))
            task.mo_state_summary = " / ".join(
                "%d %s" % (n, labels.get(s, s)) for s, n in states.items())

            res = set(mos.mapped("reservation_state"))
            if res == {"assigned"}:
                task.components_available = "ready"
            elif "assigned" in res:
                task.components_available = "partial"
            else:
                task.components_available = "waiting"

            # Cost: actual (industrial) + estimated (standard cost × qty).
            # B2 fix (2026-06-11): industrial_cost reads 0.0 across all MOs
            # on Odoo 19 CE; the field carrying the "Full Direct" total the
            # operator sees on the Production Costs tab is planned_direct_cost
            # (e.g. $80.17 on WH/MO/00085, $674.42 across task #182's 6 MOs).
            # Prefer it; fall back to industrial_cost so any future costing
            # module that populates it isn't ignored.
            task.job_industrial_cost = sum(
                (getattr(m, "planned_direct_cost", 0.0)
                 or getattr(m, "industrial_cost", 0.0)
                 or 0.0)
                for m in mos)
            task.job_estimated_cost = sum(
                (m.product_id.standard_price or 0.0) * (m.product_qty or 0.0)
                for m in mos)

            # Soft pulls from CAD / Intelligence / Shop-Floor tabs.
            install = [d for d in
                       (getattr(m, "x_sbk_install_due_date", False) for m in mos)
                       if d]
            task.job_install_due = min(install) if install else False
            cad = {getattr(m, "x_cad_status", False) for m in mos} - {False, ""}
            task.job_cad_status = ", ".join(sorted(str(c) for c in cad))
            actions = [a for a in
                       (getattr(m, "x_mi_next_action", False) for m in mos) if a]
            task.job_next_action = actions[0] if actions else ""

            # At-risk: a STARTED MO that isn't fully reserved, or any MO late.
            reasons = []
            waiting = mos.filtered(
                lambda m: m.state in ("confirmed", "progress")
                and m.reservation_state != "assigned")
            if waiting:
                reasons.append("%d MO(s) waiting on components" % len(waiting))
            # FIX 1 (2026-06-11): mrp.production.date_deadline is a Datetime;
            # `today` is a Date — the bare `<` raised TypeError and broke
            # the WHOLE form (task #182 returned Odoo Server Error and
            # wouldn't open). Convert datetime → date before comparing, and
            # null-guard against MOs with no deadline set.
            late = mos.filtered(
                lambda m: m.state not in ("done", "cancel")
                and m.date_deadline
                and m.date_deadline.date() < today)
            if late:
                reasons.append("%d MO(s) past deadline" % len(late))
            task.job_risk_reason = "; ".join(reasons)
            task.job_at_risk = bool(reasons)

    @api.depends(
        "x_southbrook_sale_order_id",
        "x_southbrook_sale_order_id.name",
        "x_southbrook_sale_order_id.partner_id",
        "partner_id",
        "stage_id",
        "stage_id.name",
    )
    def _compute_phase1_job_context(self):
        for task in self:
            order = task.x_southbrook_sale_order_id
            task.source_order_id = order
            task.source_order_name = order.name or ""
            task.customer_id = order.partner_id or task.partner_id
            task.pm_phase = task.stage_id.name or ""

    @api.depends(
        "production_ids",
        "production_ids.name",
        "production_ids.product_id",
        "production_ids.product_id.default_code",
        "production_ids.state",
        "production_ids.date_deadline",
        "production_ids.workorder_ids",
        "production_ids.workorder_ids.state",
        "production_ids.workorder_ids.date_start",
        "production_ids.workorder_ids.workcenter_id",
        "production_ids.workorder_ids.duration_expected",
        "production_count",
        "mo_state_summary",
        "workorder_summary",
        "workorder_count",
        "unscheduled_workorder_count",
        "unassigned_workorder_count",
        "manufacturing_readiness_state",
        "manufacturing_readiness_score",
        "manufacturing_blocker_summary",
        "manufacturing_warning_summary",
        "manufacturing_info_summary",
        "job_install_due",
        "job_cad_status",
        "job_at_risk",
        "job_risk_reason",
        "material_at_risk",
        "crew_gap",
        "equipment_blocked",
        "workcenter_over_capacity",
        "stage_mo_divergence",
        "stage_mo_note",
        "source_order_id",
        "customer_id",
        "southbrook_specs_complete",
        "southbrook_production_release_state",
        "southbrook_production_release_reason",
        "southbrook_install_readiness_state",
        "southbrook_install_readiness_reason",
    )
    def _compute_phase1_operational_context(self):
        for task in self:
            task.install_due_date = task.job_install_due
            task.readiness_decision = task.manufacturing_readiness_state or "info"
            task.readiness_score = task.manufacturing_readiness_score or 0
            task.linked_mo_count = task.production_count
            task.linked_wo_count = task.workorder_count
            task.unscheduled_wo_count = task.unscheduled_workorder_count
            task.unassigned_wo_count = task.unassigned_workorder_count
            task.manufacturing_reality = task._southbrook_manufacturing_reality()
            task.top_blocker = _first_meaningful_line(
                task.manufacturing_blocker_summary,
                task.manufacturing_warning_summary,
                task.manufacturing_info_summary,
            )
            task.current_bottleneck_workcenter_id = (
                task._southbrook_current_bottleneck_workcenter()
            )
            task.cabinet_family_summary = task._southbrook_cabinet_family_summary()
            task.southbrook_cabinet_family_progress = (
                task._southbrook_cabinet_family_progress()
            )
            task.risk_level, task.risk_reason = task._southbrook_risk()
            task.next_best_action = task._southbrook_next_best_action()

    def _southbrook_manufacturing_reality(self):
        self.ensure_one()
        if not self.production_count:
            return "No linked manufacturing orders."
        parts = []
        if self.mo_state_summary:
            parts.append(self.mo_state_summary)
        if self.workorder_summary:
            parts.append(self.workorder_summary)
        if self.stage_mo_divergence and self.stage_mo_note:
            parts.append(self.stage_mo_note)
        return "; ".join(parts) or "%d linked MO(s)" % self.production_count

    def _southbrook_current_bottleneck_workcenter(self):
        self.ensure_one()
        totals = {}
        for wo in self.production_ids.mapped("workorder_ids"):
            wc = wo.workcenter_id
            if not wc:
                continue
            totals.setdefault(wc, 0.0)
            totals[wc] += wo.duration_expected or 0.0
        if not totals:
            return self.env["mrp.workcenter"]
        return max(totals.items(), key=lambda item: item[1])[0]

    def _southbrook_cabinet_family_summary(self):
        self.ensure_one()
        counts = Counter()
        for product in self.production_ids.mapped("product_id"):
            family = self._southbrook_product_family(product)
            if family:
                counts[family] += 1
        if not counts:
            return ""
        return ", ".join(
            "%s: %d" % (family, count)
            for family, count in sorted(counts.items())
        )

    def _southbrook_product_family(self, product):
        code = (
            product.default_code
            or product.name
            or product.display_name
            or ""
        )
        for prefix, family in _FAMILY_BY_PREFIX.items():
            if code.startswith(prefix):
                return family
        return ""

    def _southbrook_cabinet_family_progress(self):
        self.ensure_one()
        families = {}
        today = fields.Date.context_today(self)
        for mo in self.production_ids:
            family = self._southbrook_product_family(mo.product_id) or "Other"
            families.setdefault(family, self.env["mrp.production"])
            families[family] |= mo
        lines = []
        for family, mos in sorted(families.items()):
            wos = mos.mapped("workorder_ids")
            wo_done = len(wos.filtered(lambda wo: wo.state in ("done", "cancel")))
            active_wos = wos.filtered(lambda wo: wo.state not in ("done", "cancel"))
            current_station = active_wos[:1].workcenter_id.display_name or "None"
            late = any(
                mo.date_deadline
                and fields.Date.to_date(mo.date_deadline) < today
                and mo.state not in ("done", "cancel")
                for mo in mos
            )
            blocker = "None"
            if any(mo.reservation_state != "assigned" for mo in mos):
                blocker = "Components available"
            if not wos:
                blocker = "WOs generated"
            elif any(getattr(wo, "southbrook_not_scheduled", False) for wo in wos):
                blocker = "Schedule work orders"
            if late:
                blocker = "Late MO" if blocker == "None" else "%s; Late MO" % blocker
            qty = sum(mo.product_qty or 0.0 for mo in mos)
            completed_mos = len(mos.filtered(lambda mo: mo.state in ("done", "cancel")))
            lines.append(
                "%s: qty %s, MOs %d (%d complete), WOs %d/%d complete, "
                "current station %s, blocker %s, late %s"
                % (
                    family,
                    int(qty) if qty == int(qty) else qty,
                    len(mos),
                    completed_mos,
                    wo_done,
                    len(wos),
                    current_station,
                    blocker,
                    "Yes" if late else "No",
                )
            )
        return "\n".join(lines)

    def _southbrook_risk(self):
        self.ensure_one()
        if self.equipment_blocked:
            return "critical", self.top_blocker or "Equipment/tooling is blocking production."
        if self.material_at_risk:
            return "critical", self.top_blocker or "Components or procurement are blocking production."
        if self.stage_mo_divergence:
            return "high", self.stage_mo_note
        if self.job_at_risk:
            return "high", self.job_risk_reason or self.top_blocker
        if self.manufacturing_readiness_state == "blocked":
            return "high", self.top_blocker or "Production readiness is blocked."
        if (
            self.manufacturing_readiness_state == "review"
            or self.crew_gap
            or self.workcenter_over_capacity
            or not self.job_install_due
        ):
            return "medium", (
                self.top_blocker
                or self.job_risk_reason
                or "Manager review is required before release."
            )
        return "low", "No current production risk."

    def _southbrook_next_best_action(self):
        self.ensure_one()
        if not self.customer_id or not self.source_order_id:
            return "Add the customer and source sales order before releasing this job."
        if not self.production_count:
            return "Link or create manufacturing orders for this kitchen job."
        cad_status = (self.job_cad_status or "").lower()
        if cad_status and "done" not in cad_status:
            return "Approve CAD/cutlist before releasing production."
        if not self.southbrook_specs_complete:
            return "Confirm cabinet specs before releasing production."
        if self.southbrook_production_release_state != "ready":
            return self._southbrook_production_release_next_action()
        if self.material_at_risk:
            return "Resolve component shortages or linked procurement before release."
        if self.unscheduled_workorder_count:
            return (
                "Schedule %d work order(s) before advancing this job."
                % self.unscheduled_workorder_count
            )
        if self.crew_gap:
            return "Assign or reserve crew for unassigned MOs/work orders."
        if self.equipment_blocked:
            return "Resolve equipment/tooling maintenance blockers before release."
        if self.workcenter_over_capacity:
            return "Review overloaded work-center capacity before committing the schedule."
        if self.southbrook_install_readiness_state != "ready":
            return "Confirm install readiness before delivery."
        if self.stage_mo_divergence:
            return "Review the PM phase against the actual manufacturing state."
        return "Release or advance the job."

    def _southbrook_readiness_line_values(self):
        self.ensure_one()
        values = []

        def severity(status):
            if status == "blocked":
                return "blocker"
            if status == "review":
                return "warning"
            return "info"

        def line(check_key, name, status, reason, evidence="", action=""):
            values.append({
                "task_id": self.id,
                "sequence": len(values) * 10 + 10,
                "check_key": check_key,
                "name": name,
                "status": status,
                "severity": severity(status),
                "reason": reason,
                "evidence": evidence or "",
                "recommended_action": action or "",
            })

        missing_data = []
        if not self.customer_id:
            missing_data.append("customer")
        if not self.source_order_id:
            missing_data.append("source sales order")
        if missing_data:
            line(
                "data",
                "Data Completeness",
                "blocked",
                "Missing %s." % ", ".join(missing_data),
                self.name or "",
                "Add the missing job context before releasing this job.",
            )
        else:
            line(
                "data",
                "Data Completeness",
                "ready",
                "Customer and source sales order are linked.",
                "%s / %s" % (self.customer_id.display_name, self.source_order_name),
                "No action required.",
            )

        missing_specs = self._southbrook_missing_cabinet_specs()
        if missing_specs:
            line(
                "cabinet_specs",
                "Cabinet Specs",
                "review",
                "Missing %s." % ", ".join(missing_specs),
                self.name or "",
                "Confirm cabinet specs before releasing production.",
            )
        else:
            line(
                "cabinet_specs",
                "Cabinet Specs",
                "ready",
                "Required cabinet specs are complete.",
                ", ".join(
                    item for item in (
                        self.x_southbrook_material_species or "",
                        self.southbrook_door_style or "",
                        self.southbrook_finish or "",
                    )
                    if item
                ),
                "No action required.",
            )

        if not self.production_count:
            line(
                "mrp",
                "MRP Link",
                "blocked",
                "No linked manufacturing orders.",
                self.name or "",
                "Link or create manufacturing orders for this kitchen job.",
            )
        else:
            line(
                "mrp",
                "MRP Link",
                "ready",
                "%d linked MO(s)." % self.production_count,
                self.mo_reference or "",
                "No action required.",
            )

        release_state = self.southbrook_production_release_state or "info"
        if release_state != "ready":
            line(
                "production_release",
                "Production Release Checklist",
                release_state,
                self.southbrook_production_release_reason
                or "Production release checklist needs review.",
                self._southbrook_production_release_evidence(),
                self._southbrook_production_release_next_action(),
            )
        else:
            line(
                "production_release",
                "Production Release Checklist",
                "ready",
                "Production release checklist is complete.",
                self._southbrook_production_release_evidence(),
                "No action required.",
            )

        cad_status = self.job_cad_status or ""
        if cad_status and "done" not in cad_status.lower():
            line(
                "engineering",
                "Engineering / CAD",
                "review",
                "CAD/cutlist needs review.",
                cad_status,
                "Approve CAD/cutlist before releasing production.",
            )
        else:
            line(
                "engineering",
                "Engineering / CAD",
                "ready",
                "No open CAD issue surfaced.",
                cad_status or "No CAD blocker on linked MOs.",
                "No action required.",
            )

        if self.material_at_risk:
            line(
                "materials",
                "Materials / Purchasing",
                "blocked",
                "Components or procurement are not production-ready.",
                self.material_readiness_summary or self.procurement_summary or "",
                "Resolve component shortages or linked procurement before release.",
            )
        elif self.procurement_count:
            line(
                "materials",
                "Materials / Purchasing",
                "review",
                "%d linked procurement order(s) need review." % self.procurement_count,
                self.procurement_summary or "",
                "Confirm open procurement is not blocking production.",
            )
        else:
            line(
                "materials",
                "Materials / Purchasing",
                "ready",
                "Components and procurement are clear.",
                self.material_readiness_summary or self.procurement_summary or "",
                "No action required.",
            )

        if self.unscheduled_workorder_count:
            line(
                "scheduling",
                "Scheduling",
                "blocked",
                "%d work order(s) are not scheduled." % self.unscheduled_workorder_count,
                self.workorder_summary or "",
                "Schedule work orders before advancing this job.",
            )
        else:
            line(
                "scheduling",
                "Scheduling",
                "ready",
                "Work orders are scheduled.",
                self.workorder_summary or "No unscheduled work orders surfaced.",
                "No action required.",
            )

        if self.crew_gap:
            line(
                "crew",
                "Crew",
                "review",
                "Crew assignment or reservation is incomplete.",
                self.crew_summary or "(no crew assigned)",
                "Assign or reserve crew for unassigned MOs/work orders.",
            )
        else:
            line(
                "crew",
                "Crew",
                "ready",
                "Crew assignment is clear.",
                self.crew_summary or "No crew gap surfaced.",
                "No action required.",
            )

        if self.equipment_blocked:
            line(
                "equipment",
                "Equipment / Tooling",
                "blocked",
                "Open equipment/tooling maintenance condition.",
                self.equipment_readiness_summary or "",
                "Resolve equipment/tooling maintenance blockers before release.",
            )
        else:
            line(
                "equipment",
                "Equipment / Tooling",
                "ready",
                "Equipment/tooling is clear.",
                self.equipment_readiness_summary or "No equipment blocker surfaced.",
                "No action required.",
            )

        if self.workcenter_over_capacity:
            line(
                "capacity",
                "Production Capacity",
                "review",
                "Work-center load is over daily capacity.",
                self.workcenter_load_summary or "",
                "Review overloaded work-center capacity before committing the schedule.",
            )
        elif self.job_at_risk:
            line(
                "capacity",
                "Production Capacity",
                "review",
                self.job_risk_reason or "Job is at risk.",
                self.mo_state_summary or "",
                "Review capacity, deadlines, and late manufacturing orders.",
            )
        else:
            line(
                "capacity",
                "Production Capacity",
                "ready",
                "No capacity or deadline risk surfaced.",
                self.workcenter_load_summary or self.mo_state_summary or "",
                "No action required.",
            )

        install_state = self.southbrook_install_readiness_state or "info"
        if install_state != "ready":
            line(
                "install",
                "Delivery / Install",
                install_state,
                self.southbrook_install_readiness_reason
                or "Install readiness needs review.",
                self._southbrook_install_readiness_evidence(),
                "Confirm install readiness before delivery.",
            )
        else:
            line(
                "install",
                "Delivery / Install",
                "ready",
                "Install readiness checklist is complete.",
                self._southbrook_install_readiness_evidence(),
                "No action required.",
            )

        if not self.manufacturing_calculation_count:
            line(
                "calculations",
                "Calculations",
                "info",
                "Manufacturing Intelligence checks have not run for this job.",
                self.mo_reference or "",
                "Run or review Manufacturing Intelligence calculations.",
            )
        else:
            line(
                "calculations",
                "Calculations",
                "ready",
                "%d calculation/check record(s) are linked."
                % self.manufacturing_calculation_count,
                self.mo_reference or "",
                "No action required.",
            )

        if self.stage_mo_divergence:
            line(
                "stage_mismatch",
                "PM Phase / Manufacturing Reality",
                "review",
                self.stage_mo_note or "PM phase and manufacturing reality disagree.",
                self.manufacturing_reality or "",
                "Review the PM phase against the actual manufacturing state.",
            )
        else:
            line(
                "stage_mismatch",
                "PM Phase / Manufacturing Reality",
                "ready",
                "PM phase and manufacturing reality are aligned.",
                self.manufacturing_reality or "",
                "No action required.",
            )

        return values

    def _southbrook_missing_cabinet_specs(self):
        self.ensure_one()
        missing = []
        for field_name, label in _REQUIRED_CABINET_SPEC_LABELS.items():
            if not self[field_name]:
                missing.append(label)
        return missing

    @api.depends(
        "x_southbrook_material_species",
        "x_southbrook_hardware_specs",
        "southbrook_door_style",
        "southbrook_finish",
    )
    def _compute_southbrook_specs_complete(self):
        for task in self:
            task.southbrook_specs_complete = not bool(
                task._southbrook_missing_cabinet_specs())

    # Gates a human can clear by attesting, versus gates only the factory can clear.
    # The distinction is the whole point of the `review` state: a planner needs to know
    # whether a job is waiting on a signature or waiting on the shop.
    ATTESTABLE_GATES = frozenset({
        "Final site measurements",
        "CAD approved",
        "Cutlist approved",
        "BoM verified",
        "Crew assigned/reserved",
        "Critical equipment available",
    })

    def _southbrook_release_gate_kind(self, missing):
        """'review' when every outstanding gate is one a sign-off can clear.

        `blocked` and `review` were both in the selection, both decorated in the views,
        and `review` is half of the Production Release Queue's own domain — but the
        compute only ever emitted `blocked` or `ready`, so every one of the twelve live
        jobs read identically whether it needed one signature or had no manufacturing
        orders at all. A queue that cannot distinguish those is not a queue.
        """
        if not missing:
            return "ready"
        if set(missing) <= self.ATTESTABLE_GATES:
            return "review"
        return "blocked"

    def _southbrook_missing_production_release_items(self):
        self.ensure_one()
        missing = []
        cad_status = (self.job_cad_status or "").lower()
        cad_done_from_mos = bool(cad_status and "done" in cad_status)
        if self.southbrook_site_measurement_status not in ("received", "waived"):
            missing.append("Final site measurements")
        if not (self.southbrook_release_cad_approved or cad_done_from_mos):
            missing.append("CAD approved")
        if not self.southbrook_release_cutlist_approved:
            missing.append("Cutlist approved")
        if not self.southbrook_specs_complete:
            missing.append("Door/finish/hardware specs")
        if not self.southbrook_release_bom_verified:
            missing.append("BoM verified")
        if not self.production_count:
            missing.append("Linked MOs")
        if self.components_available != "ready":
            missing.append("Components available")
        if not self.workorder_count:
            missing.append("WOs generated")
        elif self.unscheduled_workorder_count:
            missing.append("Schedule work orders")
        if self.crew_gap and not self.southbrook_release_crew_reserved:
            missing.append("Crew assigned/reserved")
        if self.equipment_blocked or not self.southbrook_release_equipment_available:
            missing.append("Critical equipment available")
        if not self.job_install_due:
            missing.append("Install due date confirmed")
        return missing

    def _southbrook_production_release_evidence(self):
        self.ensure_one()
        return "\n".join(
            part for part in (
                "Site measurement: %s"
                % (self.southbrook_site_measurement_status or "pending"),
                "CAD: %s"
                % ("approved" if self.southbrook_release_cad_approved
                   else (self.job_cad_status or "not confirmed")),
                "Cutlist: %s"
                % ("approved" if self.southbrook_release_cutlist_approved
                   else "not confirmed"),
                "Specs: %s"
                % ("complete" if self.southbrook_specs_complete else "missing"),
                "BoM: %s"
                % ("verified" if self.southbrook_release_bom_verified
                   else "not verified"),
                "Components: %s" % (self.components_available or "unknown"),
                "MOs: %d" % self.production_count,
                "WOs: %d total / %d unscheduled"
                % (self.workorder_count, self.unscheduled_workorder_count),
                "Crew: %s"
                % ("reserved" if self.southbrook_release_crew_reserved
                   else ("gap" if self.crew_gap else "clear")),
                "Equipment: %s"
                % ("blocked" if self.equipment_blocked
                   else ("available" if self.southbrook_release_equipment_available
                         else "not confirmed")),
                "Install due: %s" % (self.job_install_due or "missing"),
            )
            if part
        )

    def _southbrook_production_release_next_action(self):
        self.ensure_one()
        missing = self._southbrook_missing_production_release_items()
        if not missing:
            return "Release or advance the job."
        first = missing[0]
        actions = {
            "Final site measurements": "Confirm final site measurements before release.",
            "CAD approved": "Approve CAD before releasing production.",
            "Cutlist approved": "Approve the cutlist before releasing production.",
            "Door/finish/hardware specs": "Confirm cabinet specs before releasing production.",
            "BoM verified": "Verify the BoM against the released cutlist.",
            "Linked MOs": "Link or create manufacturing orders for this kitchen job.",
            "Components available": "Resolve component shortages before release.",
            "WOs generated": "Generate work orders from the linked MOs.",
            "Schedule work orders": "Schedule work orders before advancing this job.",
            "Crew assigned/reserved": "Assign or reserve crew for critical operations.",
            "Critical equipment available": "Confirm critical equipment is available.",
            "Install due date confirmed": "Confirm the install due date before release.",
        }
        return actions.get(first, "Complete production release checklist.")

    @api.depends(
        "southbrook_site_measurement_status",
        "southbrook_release_cad_approved",
        "southbrook_release_cutlist_approved",
        "southbrook_release_bom_verified",
        "southbrook_release_crew_reserved",
        "southbrook_release_equipment_available",
        "job_cad_status",
        "southbrook_specs_complete",
        "production_count",
        "components_available",
        "workorder_count",
        "unscheduled_workorder_count",
        "crew_gap",
        "equipment_blocked",
        "job_install_due",
    )
    def _compute_southbrook_production_release(self):
        for task in self:
            missing = task._southbrook_missing_production_release_items()
            state = task._southbrook_release_gate_kind(missing)
            task.southbrook_production_release_state = state
            if state == "ready":
                task.southbrook_production_release_reason = (
                    "Production release checklist is complete.")
            elif state == "review":
                task.southbrook_production_release_reason = (
                    "Awaiting sign-off only: %s. Nothing on the shop floor is "
                    "outstanding." % ", ".join(missing))
            else:
                blocking = [m for m in missing if m not in task.ATTESTABLE_GATES]
                task.southbrook_production_release_reason = (
                    "Missing %s. Sign-off cannot clear: %s."
                    % (", ".join(missing), ", ".join(blocking)))

            # southbrook_release_review_since times the CURRENT stretch of pure
            # signature latency. It rides this compute rather than getting its own
            # @api.depends because it derives from the exact same inputs `state`
            # already reduced — a second compute would just re-run the same gate
            # walk to ask a question this one already answered.
            #
            # Reading the field we are in the middle of computing is normally the
            # trap that produces stale reads or recompute loops. It is safe here
            # because nothing else ever writes this field and it has no depends on
            # itself — the ONLY question is "did an earlier pass of THIS SAME
            # compute already start the clock", and the previously stored value is
            # the one place that answer lives. The hourly retick cron
            # (`_cron_retick_time_sensitive_readiness`) re-runs this compute on
            # every open kitchen job whether or not anything changed, which is
            # exactly what re-stamping-on-every-pass would defeat: a job sitting
            # in `review` for six hours must keep ITS ORIGINAL timestamp, not get
            # bumped forward every retick.
            if state == "review":
                task.southbrook_release_review_since = (
                    task.southbrook_release_review_since or fields.Datetime.now())
            else:
                task.southbrook_release_review_since = False

    def _southbrook_missing_install_items(self):
        self.ensure_one()
        missing = []
        if not self.job_install_due:
            missing.append("Install due date")
        if self.southbrook_site_measurement_status not in ("received", "waived"):
            missing.append("Site measurement")
        if not self.southbrook_delivery_address:
            missing.append("Delivery address")
        if not self.southbrook_install_contact:
            missing.append("Install contact")
        if not self.southbrook_pack_label_complete:
            missing.append("Pack/label complete")
        if not self.southbrook_qc_complete:
            missing.append("QC complete")
        if not self.southbrook_delivery_staged:
            missing.append("Delivery staged")
        return missing

    def _southbrook_install_readiness_evidence(self):
        self.ensure_one()
        return "\n".join(
            part for part in (
                "Install due: %s" % (self.job_install_due or "missing"),
                "Site measurement: %s"
                % (self.southbrook_site_measurement_status or "pending"),
                "Delivery address: %s"
                % (self.southbrook_delivery_address or "missing"),
                "Install contact: %s"
                % (self.southbrook_install_contact or "missing"),
                "Pack/label: %s"
                % ("complete" if self.southbrook_pack_label_complete else "missing"),
                "QC: %s" % ("complete" if self.southbrook_qc_complete else "missing"),
                "Delivery staged: %s"
                % ("yes" if self.southbrook_delivery_staged else "no"),
            )
            if part
        )

    @api.depends(
        "job_install_due",
        "southbrook_site_measurement_status",
        "southbrook_delivery_address",
        "southbrook_install_contact",
        "southbrook_pack_label_complete",
        "southbrook_qc_complete",
        "southbrook_delivery_staged",
    )
    def _compute_southbrook_install_readiness(self):
        for task in self:
            missing = task._southbrook_missing_install_items()
            if missing:
                task.southbrook_install_readiness_state = "review"
                task.southbrook_install_readiness_reason = (
                    "Missing %s." % ", ".join(missing))
            else:
                task.southbrook_install_readiness_state = "ready"
                task.southbrook_install_readiness_reason = (
                    "Install readiness checklist is complete.")

    @api.depends(
        "southbrook_install_deficiency_notes",
        "child_ids",
        "child_ids.name",
        "child_ids.job_type",
        "production_ids",
        "production_ids.name",
        "production_ids.workorder_ids",
        "production_ids.workorder_ids.name",
        "production_ids.workorder_ids.state",
        "job_rework_count",
        "job_rework_cost",
    )
    def _compute_southbrook_quality_visibility(self):
        Scrap = self.env["stock.scrap"] if "stock.scrap" in self.env else None
        Unbuild = self.env["mrp.unbuild"] if "mrp.unbuild" in self.env else None
        for task in self:
            wos = task.production_ids.mapped("workorder_ids")
            rework_wos = wos.filtered(
                lambda wo: bool(getattr(wo, "x_sbk_rework_count", 0))
                or "rework" in (wo.name or "").lower())
            remake_tasks = task.child_ids.filtered(
                lambda child: child.job_type == "warranty"
                or any(
                    token in (child.name or "").lower()
                    for token in ("remake", "warranty", "deficiency", "punch")
                ))
            scraps = task._southbrook_linked_scrap_records(Scrap)
            unbuilds = task._southbrook_linked_unbuild_records(Unbuild)

            task.southbrook_rework_workorder_ids = rework_wos
            task.southbrook_rework_workorder_count = len(rework_wos)
            task.southbrook_remake_task_ids = remake_tasks
            task.southbrook_remake_task_count = len(remake_tasks)
            task.southbrook_scrap_ids = scraps
            task.southbrook_scrap_count = len(scraps)
            task.southbrook_unbuild_ids = unbuilds
            task.southbrook_unbuild_count = len(unbuilds)

            parts = []
            if task.southbrook_install_deficiency_notes:
                parts.append("Deficiency notes recorded")
            if task.job_rework_count:
                parts.append(
                    "%d rework check(s), %s rework cost"
                    % (task.job_rework_count, task.job_rework_cost or 0.0))
            if rework_wos:
                parts.append("%d rework WO(s)" % len(rework_wos))
            if remake_tasks:
                parts.append(
                    "%d warranty/remake task(s)" % len(remake_tasks))
            if scraps:
                parts.append("%d scrap record(s)" % len(scraps))
            if unbuilds:
                parts.append("%d unbuild/remake record(s)" % len(unbuilds))
            task.southbrook_quality_issue_summary = (
                "; ".join(parts) or "No quality, remake, or deficiency issues surfaced."
            )

    def _southbrook_linked_scrap_records(self, Scrap):
        self.ensure_one()
        if not Scrap or not self.production_ids:
            return self.env["stock.scrap"]
        if "production_id" in Scrap._fields:
            return Scrap.search([("production_id", "in", self.production_ids.ids)])
        return self.env["stock.scrap"]

    def _southbrook_linked_unbuild_records(self, Unbuild):
        self.ensure_one()
        if not Unbuild or not self.production_ids:
            return self.env["mrp.unbuild"]
        if "mo_id" in Unbuild._fields:
            return Unbuild.search([("mo_id", "in", self.production_ids.ids)])
        if "production_id" in Unbuild._fields:
            return Unbuild.search([
                ("production_id", "in", self.production_ids.ids)])
        return self.env["mrp.unbuild"]

    @api.depends(
        "production_count",
        "job_cad_status",
        "material_at_risk",
        "procurement_count",
        "unscheduled_workorder_count",
        "crew_gap",
        "equipment_blocked",
        "workcenter_over_capacity",
        "job_at_risk",
        "job_install_due",
        "manufacturing_calculation_count",
        "stage_mo_divergence",
        "customer_id",
        "source_order_id",
        "x_southbrook_material_species",
        "x_southbrook_hardware_specs",
        "southbrook_door_style",
        "southbrook_finish",
        "southbrook_production_release_state",
        "southbrook_install_readiness_state",
    )
    def _compute_readiness_line_count(self):
        for task in self:
            task.readiness_line_count = len(task._southbrook_readiness_line_values())

    @api.depends("job_cad_status", "job_install_due", "stage_mo_divergence")
    def _compute_phase3_queue_flags(self):
        for task in self:
            cad_status = (task.job_cad_status or "").lower()
            task.cad_cutlist_review_required = bool(
                cad_status and "done" not in cad_status)
            task.install_date_missing = not bool(task.job_install_due)
            task.pm_stage_mismatch = bool(task.stage_mo_divergence)

    def action_southbrook_schedule_unscheduled_workorders(self):
        """Plan only work orders that no one has scheduled, in EITHER system.

        THERE ARE TWO SCHEDULES ON THIS INSTANCE, and that is the whole difficulty.
        Native Odoo stores a work order's plan as a `resource.calendar.leaves` row pointed
        at by `leave_id` (date_start/date_finished are computed from it). The installed
        `mrp_shop_floor_control` keeps a SECOND, independent plan in
        `date_planned_start_wo`, hand-editable through its own wizard.

        On production right now: 17 of 93 in-flight work orders carry a native leave, 95
        of 99 carry an SFC date, and ZERO are unscheduled in both. In other words every
        piece of in-flight work has already been planned by a person — just not in the
        field the native views read. An action guarded only on `leave_id`, which is the
        obvious reading of "unscheduled", would have re-planned seventy-six work orders
        that a planner had already dated.

        So this refuses to touch anything a human has dated anywhere, and on today's data
        it correctly does nothing. That is the right outcome: an action whose safe form is
        a no-op is telling you the queue is not the problem.

        It also deliberately does NOT call `mrp.production.button_plan()`:
          * `is_planned` is computed with `any()`, so a partly-dated MO is invisible to it
            — neither re-planned nor advanced.
          * it silently confirms draft MOs as a side effect.
          * `mrp_shop_floor_control` overrides it and re-runs its own `schedule_workorders()`
            for EVERY order passed in, planned or not, rewriting the SFC dates this method
            exists to protect.
        Core's per-work-order `_plan_workorder(replan=False)` carries the correct guard
        (`if self.leave_id: return`), searches the work centre for a free slot before
        creating the leave, and threads dependency order through `blocked_by_workorder_ids`.
        """
        self.ensure_one()
        if not self.env.user.has_group("mrp.group_mrp_user"):
            raise UserError(_(
                "Scheduling manufacturing work needs the Manufacturing / User group."))

        workorders = self.production_ids.mapped("workorder_ids")
        # `_plan_workorder` itself only acts on these two states; filtering here as well
        # keeps the counts honest in the message below.
        candidates = workorders.filtered(lambda w: w.state in ("blocked", "ready"))
        already_native = candidates.filtered("leave_id")
        already_sfc = candidates.filtered(
            lambda w: not w.leave_id and getattr(w, "date_planned_start_wo", False))
        unscheduled = candidates - already_native - already_sfc

        if not unscheduled:
            return self._sb_notify(
                _("Nothing to schedule"),
                _("%(n)s work order(s) are in flight and every one already has a planned "
                  "date — %(native)s in the native schedule, %(sfc)s in the shop-floor "
                  "schedule. Nothing here is waiting on a planner.",
                  n=len(candidates), native=len(already_native), sfc=len(already_sfc)),
                "warning")

        for workorder in unscheduled:
            workorder._plan_workorder(replan=False)

        self.message_post(body=_(
            "Scheduled %(n)s previously unplanned work order(s), by %(actor)s. "
            "%(skipped)s left untouched because a planner had already dated them.",
            n=len(unscheduled), actor=self.env.user.display_name,
            skipped=len(already_native) + len(already_sfc)))
        return self._sb_notify(
            _("Scheduled"),
            _("%(n)s work order(s) planned. %(skipped)s were left alone — they already "
              "had a date.", n=len(unscheduled),
              skipped=len(already_native) + len(already_sfc)),
            "success")

    def _sb_notify(self, title, message, kind):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "message": message, "type": kind,
                       "sticky": kind == "warning"},
        }

    @api.model
    def _sb_recompute_for_workcenters(self, workcenters):
        """Requeue readiness for every job touching these work centres.

        The reverse of `_compute_equipment_readiness`'s forward walk, and the reverse of
        the @api.depends it declares. Every hop is indexed — mrp.workorder.workcenter_id,
        mrp.workorder.production_id and mrp.production.project_task_id — so this is nested
        indexed subqueries rather than a scan.

        `modified(["production_ids"])` rather than calling the compute: Odoo registers
        every PREFIX of a dotted dependency as a trigger root, so `production_ids` queues
        equipment_blocked through the protected recompute path. Calling the compute
        directly would turn each field assignment into a full write().
        """
        if not workcenters:
            return 0
        tasks = self.sudo().search([
            ("production_ids.workorder_ids.workcenter_id", "in", workcenters.ids),
        ])
        if not tasks:
            return 0
        tasks.modified(["production_ids"])
        tasks.flush_recordset()
        return len(tasks)

    @api.model
    def _cron_retick_time_sensitive_readiness(self, limit=None):
        """Re-evaluate readiness that depends on the date, not on a write.

        `job_at_risk` is True partly because an MO deadline has PASSED. That is a
        comparison against today, and today is not a field, so it cannot appear in
        @api.depends. Odoo only recomputes a stored field when a declared dependency is
        written, which means a job that becomes late overnight — with nobody touching the
        MO — keeps yesterday's `False` forever and drops off the Install Risk board.

        That is the same false all-clear the board already suffered from for a different
        reason, and storing the field would have reintroduced it through the back door.
        A stored field driven by wall-clock time needs an external forcing function; this
        is it.

        Scoped to open kitchen jobs, mirroring the readiness cron, so this never walks
        every project.task on the instance.
        """
        tasks = self.search(
            [("x_southbrook_sale_order_id", "!=", False),
             ("production_ids", "!=", False)],
            limit=limit)
        if not tasks:
            return 0
        # modified() on the declared dependency is what puts the records back on the
        # to-compute list; calling the compute directly would bypass Odoo's protected
        # recompute path and turn every field assignment into a full write().
        tasks.modified(["production_ids"])
        tasks.flush_recordset()
        # The same re-tick also bounds the one gap the maintenance hooks cannot see: an
        # admin flipping `done` on a maintenance.stage RECORD writes no request row, so
        # nothing invalidates equipment_blocked. Rare, and an hour of staleness beats a
        # second hook on a config model nobody edits.
        late = tasks.filtered("job_at_risk")
        _logger.info(
            "southbrook_project_mrp: re-ticked %s kitchen job(s) for date-driven "
            "readiness; %s now at risk", len(tasks), len(late))
        # Chained on purpose, not a separate cron: the re-tick above is what just
        # refreshed southbrook_release_review_since for every job whose review-gate
        # inputs moved, so escalation reads current data instead of racing an
        # independently-scheduled job against this one for the same records.
        self._cron_escalate_signoff_latency()
        return len(tasks)

    @api.model
    def _cron_escalate_signoff_latency(self):
        """Turn stale sign-off latency into a visible activity, not a silent stall.

        A job in `review` is not waiting on the shop floor at all — every outstanding
        gate is one a human can clear by attesting (ATTESTABLE_GATES /
        `_southbrook_release_gate_kind`). `southbrook_release_review_since` times how
        long THIS stretch of review has run, but a timestamp nobody looks at is just
        data; it takes an actor. Below a threshold that is fine — sign-offs happen
        within a work day without anyone chasing them. Past it, every additional hour
        is pure latency on a job that is otherwise done, and the person who can clear
        it needs to be told, not left to notice on their own next time they open the
        queue.

        Threshold lives in ir.config_parameter rather than as a constant because "how
        many hours is acceptable before a sign-off is chased" is a business call
        Southbrook may retune without a code deploy — default 24.0h, one business day.
        """
        param = self.env["ir.config_parameter"].sudo().get_param(
            "southbrook_project_mrp.review_signoff_escalation_hours", "24.0")
        try:
            threshold_hours = float(param)
        except (TypeError, ValueError):
            _logger.warning(
                "southbrook_project_mrp: review_signoff_escalation_hours=%r is not "
                "numeric; falling back to the 24h default rather than skipping "
                "sign-off escalation for this run.", param)
            threshold_hours = 24.0

        cutoff = fields.Datetime.now() - timedelta(hours=threshold_hours)
        stale = self.search([
            ("southbrook_production_release_state", "=", "review"),
            ("southbrook_release_review_since", "!=", False),
            ("southbrook_release_review_since", "<", cutoff),
        ])
        if not stale:
            return 0

        activity_type = self.env.ref(
            "southbrook_project_mrp.mail_activity_type_signoff_overdue",
            raise_if_not_found=False)
        if not activity_type:
            _logger.warning(
                "southbrook_project_mrp: mail_activity_type_signoff_overdue not "
                "found (data file not loaded?) -- %s stale review job(s) got no "
                "escalation activity this run.", len(stale))
            return 0

        escalated = 0
        for task in stale:
            try:
                # Idempotent across hourly runs: an OPEN activity of this type on
                # this task means a previous run already escalated it and nobody
                # has actioned it yet. `mail.activity` rows are the open ones by
                # construction — marking one done deletes it (becomes a chatter
                # message) — so existence alone is the "still open" test.
                existing = self.env["mail.activity"].search([
                    ("res_model", "=", "project.task"),
                    ("res_id", "=", task.id),
                    ("activity_type_id", "=", activity_type.id),
                ], limit=1)
                if existing:
                    continue
                assignee = task.user_ids[:1]
                if not assignee:
                    # user_id is not a native project.task field (assignees are the
                    # user_ids m2m) but some deployments layer one on; read it
                    # defensively rather than assume it exists.
                    fallback = getattr(task, "user_id", False)
                    assignee = fallback if fallback else False
                if not assignee:
                    _logger.info(
                        "southbrook_project_mrp: task %s (%s) is overdue for "
                        "sign-off but has nobody assigned to escalate to -- "
                        "skipped this run.", task.id, task.name)
                    continue
                missing = task._southbrook_missing_production_release_items()
                task.activity_schedule(
                    "southbrook_project_mrp.mail_activity_type_signoff_overdue",
                    summary=_("Sign-off overdue: %s") % ", ".join(missing),
                    user_id=assignee.id,
                )
                escalated += 1
            except Exception as exc:  # noqa: BLE001 — one bad task can't sink the cron
                _logger.warning(
                    "southbrook_project_mrp: sign-off escalation failed for task "
                    "%s: %s -- left un-escalated, will retry next hour.",
                    task.id, exc)
        _logger.info(
            "southbrook_project_mrp: escalated %s/%s job(s) past the %.1fh "
            "sign-off threshold.", escalated, len(stale), threshold_hours)
        return escalated

    def _search_boolean_compute(self, field_name, operator, value):
        if operator not in ("=", "!="):
            return [("id", "=", 0)]
        desired = bool(value)
        if value in (False, 0, "0", "false", "False"):
            desired = False
        if operator == "!=":
            desired = not desired
        tasks = self.with_context(active_test=False).search([]).filtered(
            lambda task: bool(task[field_name]) == desired)
        return [("id", "in", tasks.ids)]

    def _search_integer_compute(self, field_name, operator, value):
        if operator not in ("=", "!=", ">", ">=", "<", "<="):
            return [("id", "=", 0)]
        try:
            expected = float(value or 0)
        except (TypeError, ValueError):
            return [("id", "=", 0)]

        def matches(actual):
            actual = float(actual or 0)
            if operator == "=":
                return actual == expected
            if operator == "!=":
                return actual != expected
            if operator == ">":
                return actual > expected
            if operator == ">=":
                return actual >= expected
            if operator == "<":
                return actual < expected
            return actual <= expected

        tasks = self.with_context(active_test=False).search([]).filtered(
            lambda task: matches(task[field_name]))
        return [("id", "in", tasks.ids)]

    def _search_selection_compute(self, field_name, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            return [("id", "=", 0)]
        values = value if operator in ("in", "not in") else [value]
        values = set(values)
        tasks = self.with_context(active_test=False).search([]).filtered(
            lambda task: task[field_name] in values)
        if operator in ("=", "in"):
            return [("id", "in", tasks.ids)]
        return [("id", "not in", tasks.ids)]

    def _search_southbrook_install_readiness_state(self, operator, value):
        return self._search_selection_compute(
            "southbrook_install_readiness_state", operator, value)

    def _search_southbrook_production_release_state(self, operator, value):
        return self._search_selection_compute(
            "southbrook_production_release_state", operator, value)

    @api.depends("production_ids", "production_ids.bom_id")
    def _compute_eco(self):
        has_eco = "southbrook.eco" in self.env
        Eco = self.env["southbrook.eco"] if has_eco else None
        for task in self:
            boms = task.production_ids.mapped("bom_id") if task.production_ids else None
            if not has_eco or not boms:
                task.eco_count = 0
                task.eco_pending_count = 0
                continue
            ecos = Eco.search([("bom_id", "in", boms.ids)])
            task.eco_count = len(ecos)
            task.eco_pending_count = len(ecos.filtered(lambda e: e.state == "open"))

    @api.depends("stage_id", "production_ids.state")
    def _compute_stage_coherence(self):
        for task in self:
            task.stage_mo_divergence = False
            task.stage_mo_note = ""
            mos = task.production_ids
            stage = (task.stage_id.name or "").lower()
            if not mos or not stage:
                continue
            if any(h in stage for h in _DELIVERY_STAGE_HINTS):
                if any(m.state != "done" for m in mos):
                    task.stage_mo_divergence = True
                    task.stage_mo_note = (
                        "Stage '%s' but not all MOs are done." % task.stage_id.name)
            elif any(h in stage for h in _STARTED_STAGE_HINTS):
                if not any(m.state in _MO_STARTED_STATES for m in mos):
                    task.stage_mo_divergence = True
                    task.stage_mo_note = (
                        "Stage '%s' but no MO has started production."
                        % task.stage_id.name)

    # --- TASK 2: Work-order rollup compute ---------------------------------
    @api.depends("production_ids", "production_ids.workorder_ids",
                 "production_ids.workorder_ids.date_start",
                 "production_ids.workorder_ids.state")
    def _compute_workorder_rollup(self):
        for task in self:
            wos = task.production_ids.mapped("workorder_ids")
            task.workorder_ids = wos
            task.workorder_count = len(wos)
            # "Not scheduled" = no planned/actual start date set yet. The
            # scheduler needs to set these in the Work Orders view.
            unscheduled = wos.filtered(lambda w: not w.date_start)
            task.unscheduled_workorder_count = len(unscheduled)
            if not wos:
                task.workorder_summary = ""
            elif not unscheduled:
                task.workorder_summary = "%d WOs all scheduled" % len(wos)
            else:
                task.workorder_summary = (
                    "%d WOs / %d not scheduled" % (len(wos), len(unscheduled)))

    @api.depends("production_ids")
    def _compute_manufacturing_calculations(self):
        Check = self.env["southbrook.mi.check"] if "southbrook.mi.check" in self.env else None
        for task in self:
            if not Check or not task.production_ids:
                task.manufacturing_calculation_count = 0
                continue
            task.manufacturing_calculation_count = Check.search_count(
                [("production_id", "in", task.production_ids.ids)])

    @api.depends(
        "production_count",
        "job_cad_status",
        "material_at_risk",
        "procurement_count",
        "unscheduled_workorder_count",
        "crew_gap",
        "equipment_blocked",
        "workcenter_over_capacity",
        "job_at_risk",
        "job_risk_reason",
        "job_install_due",
        "manufacturing_calculation_count",
        "stage_mo_divergence",
        "stage_mo_note",
        "customer_id",
        "source_order_id",
        "southbrook_specs_complete",
        "southbrook_production_release_state",
        "southbrook_production_release_reason",
        "southbrook_install_readiness_state",
        "southbrook_install_readiness_reason",
    )
    def _compute_manufacturing_readiness(self):
        for task in self:
            gates = []
            blockers = []
            warnings = []
            infos = []

            def gate(name, state, note):
                gates.append("%s: %s - %s" % (name, state, note))

            missing_data = []
            if not task.customer_id:
                missing_data.append("customer")
            if not task.source_order_id:
                missing_data.append("source sales order")
            if missing_data:
                gate(
                    "Data Completeness",
                    "BLOCKED",
                    "missing %s" % ", ".join(missing_data),
                )
                blockers.append(
                    "Data Completeness: missing %s" % ", ".join(missing_data))
            else:
                gate(
                    "Data Completeness",
                    "READY",
                    "customer and source sales order linked",
                )

            if not task.southbrook_specs_complete:
                missing_specs = task._southbrook_missing_cabinet_specs()
                gate(
                    "Cabinet Specs",
                    "REVIEW",
                    "missing %s" % ", ".join(missing_specs),
                )
                warnings.append(
                    "Cabinet Specs: confirm %s" % ", ".join(missing_specs))
            else:
                gate("Cabinet Specs", "READY", "required specs complete")

            if task.southbrook_production_release_state != "ready":
                gate(
                    "Production Release Checklist",
                    "BLOCKED",
                    task.southbrook_production_release_reason
                    or "production release checklist incomplete",
                )
                blockers.append(
                    "Production Release: %s"
                    % (task.southbrook_production_release_reason
                       or "checklist incomplete"))
            else:
                gate(
                    "Production Release Checklist",
                    "READY",
                    "release checklist complete",
                )

            if not task.production_count:
                gate("MRP Link", "BLOCKED", "no linked manufacturing orders")
                blockers.append("MRP Link: no linked manufacturing orders")
            else:
                gate("MRP Link", "READY", "%d linked MO(s)" % task.production_count)

            cad_status = task.job_cad_status or ""
            if cad_status and "done" not in cad_status.lower():
                gate("Engineering / CAD", "REVIEW", cad_status)
                warnings.append("Engineering / CAD: review CAD status")
            else:
                gate("Engineering / CAD", "READY", cad_status or "no open CAD issue")

            if task.material_at_risk:
                gate("Materials / Purchasing", "BLOCKED", "material is at risk")
                blockers.append("Materials / Purchasing: material shortfall")
            elif task.procurement_count:
                gate(
                    "Materials / Purchasing", "REVIEW",
                    "%d procurement order(s)" % task.procurement_count)
                warnings.append("Materials / Purchasing: review open procurement")
            else:
                gate("Materials / Purchasing", "READY", "components/procurement clear")

            if task.unscheduled_workorder_count:
                gate(
                    "Scheduling", "BLOCKED",
                    "%d work order(s) not scheduled" % task.unscheduled_workorder_count)
                blockers.append(
                    "Scheduling: %d work order(s) not scheduled"
                    % task.unscheduled_workorder_count)
            else:
                gate("Scheduling", "READY", "work orders scheduled")

            if task.crew_gap:
                gate("Crew", "REVIEW", "operator assignment gap")
                warnings.append("Crew: assign/reserve operators")
            else:
                gate("Crew", "READY", "crew assignment clear")

            if task.equipment_blocked:
                gate("Equipment / Tooling", "BLOCKED", "open maintenance condition")
                blockers.append("Equipment / Tooling: maintenance block")
            else:
                gate("Equipment / Tooling", "READY", "equipment clear")

            if task.workcenter_over_capacity:
                gate(
                    "Production Capacity", "REVIEW",
                    "work-center load over daily capacity")
                warnings.append(
                    "Production Capacity: review overloaded work center")
            elif task.job_at_risk:
                gate(
                    "Production Capacity", "REVIEW",
                    task.job_risk_reason or "job at risk")
                warnings.append("Production Capacity: review job risk")
            else:
                gate("Production Capacity", "READY", "no capacity/risk flag")

            if task.southbrook_install_readiness_state != "ready":
                gate(
                    "Delivery / Install",
                    "REVIEW",
                    task.southbrook_install_readiness_reason
                    or "install readiness needs review",
                )
                warnings.append(
                    "Delivery / Install: confirm install readiness")
            else:
                gate(
                    "Delivery / Install",
                    "READY",
                    "install readiness checklist complete")

            if not task.manufacturing_calculation_count:
                gate(
                    "Calculations",
                    "INFO",
                    "no Manufacturing Intelligence checks linked",
                )
                infos.append(
                    "Calculations: run/review Manufacturing Intelligence checks")
            else:
                gate(
                    "Calculations",
                    "READY",
                    "%d linked calculation/check record(s)"
                    % task.manufacturing_calculation_count,
                )

            if task.stage_mo_divergence:
                gate(
                    "PM Phase / Manufacturing Reality",
                    "REVIEW",
                    task.stage_mo_note
                    or "PM phase and manufacturing reality disagree",
                )
                warnings.append(
                    "PM Phase / Manufacturing Reality: review stage mismatch")
            else:
                gate(
                    "PM Phase / Manufacturing Reality",
                    "READY",
                    "stage matches manufacturing reality",
                )

            task.manufacturing_waterfall_summary = "\n".join(gates)
            task.manufacturing_blocker_summary = (
                "\n".join(blockers) or "No start blockers.")
            task.manufacturing_warning_summary = (
                "\n".join(warnings) or "No manager-review warnings.")
            task.manufacturing_info_summary = (
                "\n".join(infos) or "No efficiency prompts.")
            task.manufacturing_readiness_state = (
                "blocked" if blockers else ("review" if warnings else "ready"))
            penalty = len(blockers) * 25 + len(warnings) * 10
            score = max(0, min(100, 100 - penalty))
            caps = []
            if not task.production_count:
                caps.append(40)
            cad_status = (task.job_cad_status or "").lower()
            if cad_status and "done" not in cad_status:
                caps.append(55)
            if task.material_at_risk:
                caps.append(60)
            if task.unscheduled_workorder_count:
                caps.append(55)
            if task.equipment_blocked:
                caps.append(65)
            if task.southbrook_production_release_state != "ready":
                caps.append(65)
            if not task.job_install_due:
                caps.append(80)
            if caps:
                score = min([score] + caps)
            task.manufacturing_readiness_score = score

    def _search_manufacturing_readiness_state(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            return [("id", "=", 0)]
        values = value if operator in ("in", "not in") else [value]
        values = set(values)
        tasks = self.with_context(active_test=False).search([])
        matching = tasks.filtered(
            lambda task: task.manufacturing_readiness_state in values)
        if operator in ("=", "in"):
            return [("id", "in", matching.ids)]
        return [("id", "not in", matching.ids)]

    # --- TASK 5: Kitchen Metrics rollup compute ----------------------------
    # NB: The x_sbk_* Kitchen Metrics fields are added by a downstream
    # module (southbrook_kitchen_workspace), which this bridge does NOT
    # hard-depend on. @api.depends can only reference fields that exist
    # at registry-build time; listing them here would fail to upgrade
    # this bridge alone. Depend only on the workorder relation + state
    # — the metrics get written when WOs finish, which also flips state,
    # so the rollup re-fires at the right moments without a direct dep.
    @api.depends("production_ids.workorder_ids",
                 "production_ids.workorder_ids.state",
                 "production_ids.workorder_ids.duration")
    def _compute_kitchen_metrics_rollup(self):
        for task in self:
            wos = task.production_ids.mapped("workorder_ids")
            # Defensive sums using getattr — the x_sbk_* metric fields are
            # added by southbrook_kitchen_workspace; if uninstalled, the
            # rollup degrades cleanly to 0 rather than crashing the form.
            def _sum(field):
                return sum(
                    (getattr(w, field, 0.0) or 0.0) for w in wos)
            task.job_expected_min = _sum("x_sbk_kitchen_expected_min")
            task.job_variance_min = _sum("x_sbk_variance_min")
            task.job_actual_cost = _sum("x_sbk_actual_cost")
            task.job_cost_variance = _sum("x_sbk_cost_variance")
            task.job_rework_count = int(_sum("x_sbk_rework_count"))
            task.job_rework_cost = _sum("x_sbk_rework_cost")
            task.job_downtime_min = _sum("x_sbk_downtime_min")
            task.job_downtime_cost = _sum("x_sbk_downtime_cost")

    # --- TASK 4: Crew compute ----------------------------------------------
    @api.depends("production_ids", "production_ids.user_id",
                 "production_ids.workorder_ids",
                 "production_ids.workorder_ids.southbrook_assigned_user_id",
                 "production_ids.workorder_ids.working_user_ids",
                 "production_ids.workorder_ids.last_working_user_id")
    def _compute_crew(self):
        for task in self:
            mos = task.production_ids
            wos = mos.mapped("workorder_ids")
            crew = self.env["res.users"]
            crew |= mos.mapped("user_id")
            crew |= wos.mapped("southbrook_assigned_user_id")
            crew |= wos.mapped("working_user_ids")
            crew |= wos.mapped("last_working_user_id")
            # Filter to active internal users only (no bots / no portal).
            crew = crew.filtered(
                lambda u: u and not u.share and u.active)
            unassigned_mos = mos.filtered(lambda m: not m.user_id)
            # A work order counts as crewed when somebody is PLANNED to run it. The
            # timer fields still count — if a person is on the machine the question is
            # settled — but they are no longer the only way to answer it. Before this,
            # the gap asked "has anyone clocked on", which is false for all work that has
            # not started, so the flag was permanently true and unclosable.
            unassigned_wos = wos.filtered(
                lambda w: not w.southbrook_assigned_user_id
                and not w.working_user_ids
                and not w.last_working_user_id)
            task.unassigned_mo_count = len(unassigned_mos)
            task.unassigned_workorder_count = len(unassigned_wos)
            task.crew_gap = bool(unassigned_mos or unassigned_wos)
            if not crew:
                task.crew_summary = "" if not mos else "(no crew assigned)"
            else:
                task.crew_summary = ", ".join(
                    sorted(crew.mapped("name")))

    # --- TASK 3: Work-center load compute ----------------------------------
    # The capacity threshold is derived from the work centre's own efficiency and its
    # calendar's hours_per_day. Those were read but never declared, which was harmless
    # while the field was computed on the fly and is NOT harmless now that it is stored:
    # changing a work centre's efficiency or working hours would leave every task's
    # over-capacity flag frozen at its old value.
    @api.depends("production_ids.workorder_ids.workcenter_id",
                 "production_ids.workorder_ids.duration_expected",
                 "production_ids.workorder_ids.workcenter_id.time_efficiency",
                 "production_ids.workorder_ids.workcenter_id.resource_calendar_id",
                 "production_ids.workorder_ids.workcenter_id.resource_calendar_id.hours_per_day")
    def _compute_workcenter_load(self):
        for task in self:
            wos = task.production_ids.mapped("workorder_ids")
            if not wos:
                task.workcenter_load_summary = ""
                task.workcenter_over_capacity = False
                continue
            # Aggregate booked minutes per workcenter.
            booked = {}
            for wo in wos:
                wc = wo.workcenter_id
                if not wc:
                    continue
                booked.setdefault(wc, 0.0)
                booked[wc] += wo.duration_expected or 0.0
            lines = []
            any_over = False
            for wc, minutes in sorted(
                    booked.items(), key=lambda kv: -kv[1]):
                # Daily capacity proxy: resource_calendar hours/day × 60
                # multiplied by time_efficiency (stock Odoo's standard
                # capacity model — both are already on mrp.workcenter,
                # no rebuild). Fall back gracefully if absent.
                cap_min = 0.0
                cal = wc.resource_calendar_id
                hpd = getattr(cal, "hours_per_day", 0.0) or 0.0
                eff = (wc.time_efficiency or 100.0) / 100.0
                cap_min = hpd * 60.0 * eff
                if cap_min and minutes > cap_min:
                    any_over = True
                    flag = " ⚠ OVER"
                elif cap_min:
                    flag = " (%d min cap)" % int(cap_min)
                else:
                    flag = ""
                lines.append("%s: %d min booked%s" % (
                    wc.name, int(minutes), flag))
            task.workcenter_load_summary = "\n".join(lines)
            task.workcenter_over_capacity = any_over

    @api.depends("production_ids",
                 "production_ids.components_availability_state",
                 "production_ids.reservation_state",
                 "production_ids.purchase_order_count")
    def _compute_material_readiness(self):
        PurchaseLine = self.env["purchase.order.line"]
        StockMove = self.env["stock.move"]
        for task in self:
            mos = task.production_ids
            ready = partial = unavailable = 0
            lines = []
            for mo in mos.sorted("name"):
                state = (
                    getattr(mo, "components_availability_state", False)
                    or getattr(mo, "reservation_state", False)
                    or "unknown"
                )
                label = getattr(mo, "components_availability", False) or state
                if state in ("available", "assigned"):
                    ready += 1
                elif state in ("partially_available", "partial", "waiting"):
                    partial += 1
                else:
                    unavailable += 1
                lines.append("%s: %s" % (mo.name, label))

            # Odoo 19 removed the direct `production_id` field from
            # purchase.order.line. The linkage is now indirect via
            # stock.move: po_line.move_dest_ids → mo.move_raw_ids.
            # The move-based lookup below already covers what the old
            # `[("production_id", "in", mos.ids)]` search would return,
            # so start from an empty recordset and let the move path
            # populate it.
            po_lines = PurchaseLine.browse()
            raw_moves = mos.mapped("move_raw_ids")
            if raw_moves:
                po_lines |= raw_moves.mapped("created_purchase_line_ids")
                po_lines |= raw_moves.mapped("purchase_line_id")
                move_po_lines = PurchaseLine.search(
                    [("move_dest_ids", "in", raw_moves.ids)])
                po_lines |= move_po_lines
                related_moves = StockMove.search([
                    ("id", "in", raw_moves.mapped("move_orig_ids").ids),
                    ("purchase_line_id", "!=", False),
                ])
                po_lines |= related_moves.mapped("purchase_line_id")
            open_pos = po_lines.mapped("order_id").filtered(
                lambda po: po.state not in ("cancel", "done"))

            task.material_ready_count = ready
            task.material_partial_count = partial
            task.material_unavailable_count = unavailable
            task.material_at_risk = bool(partial or unavailable)
            task.material_readiness_summary = "\n".join(lines)
            task.procurement_order_ids = open_pos
            task.procurement_count = len(open_pos)
            if open_pos:
                task.procurement_summary = "\n".join(
                    "%s: %s" % (po.name, po.state) for po in open_pos.sorted("name"))
            else:
                task.procurement_summary = "No open linked procurement."

    @api.depends("production_ids.workorder_ids.workcenter_id")
    def _compute_equipment_readiness(self):
        # Odoo 19: mrp.workcenter.equipment_ids no longer exists. Walk the
        # forward path maintenance.equipment.workcenter_id instead, mirroring
        # the sibling pattern in mrp_workorder.py:81-90 (Pattern C). Per-wc
        # rollup is preserved so equipment_readiness_summary still emits one
        # line per workcenter.
        Equipment = self.env["maintenance.equipment"]
        Request = self.env["maintenance.request"]
        for task in self:
            workcenters = task.production_ids.mapped(
                "workorder_ids.workcenter_id")
            requests = Request.browse()
            lines = []
            for wc in workcenters.sorted("name"):
                equipment = Equipment.search([("workcenter_id", "=", wc.id)])
                wc_requests = equipment.mapped("maintenance_ids").filtered(
                    lambda req: not req.stage_id.done)
                requests |= wc_requests
                if wc_requests:
                    lines.append("%s: BLOCKED - %d open maintenance request(s)" %
                                 (wc.name, len(wc_requests)))
                else:
                    lines.append("%s: Ready" % wc.name)
            task.maintenance_request_ids = requests
            task.maintenance_request_count = len(requests)
            task.equipment_blocked = bool(requests)
            task.equipment_readiness_summary = "\n".join(lines)

    # --- actions ------------------------------------------------------------
    def action_view_workorders(self):
        """Open the Work Orders list filtered to this job's WOs so the
        MRP scheduler can set planned start dates in the native view."""
        self.ensure_one()
        wos = self.production_ids.mapped("workorder_ids")
        return {
            "type": "ir.actions.act_window",
            "name": "Work Orders — %s" % (self.name or self.display_name),
            "res_model": "mrp.workorder",
            "domain": [("id", "in", wos.ids)],
            "view_mode": "list,form,gantt,calendar",
            "context": {"create": False},
        }

    def action_view_workorders_can_start_today(self):
        self.ensure_one()
        wos = self.production_ids.mapped("workorder_ids").filtered(
            "southbrook_can_start_today")
        return {
            "type": "ir.actions.act_window",
            "name": "Can Start Today - %s" % (self.name or self.display_name),
            "res_model": "mrp.workorder",
            "domain": [("id", "in", wos.ids)],
            "view_mode": "list,form,gantt,calendar",
            "context": {"create": False},
        }

    def action_view_southbrook_rework_workorders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Rework Work Orders - %s" % (self.name or self.display_name),
            "res_model": "mrp.workorder",
            "domain": [("id", "in", self.southbrook_rework_workorder_ids.ids)],
            "view_mode": "list,form,gantt,calendar",
            "context": {"create": False},
        }

    def action_view_southbrook_remake_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Warranty / Remake Tasks - %s"
                    % (self.name or self.display_name),
            "res_model": "project.task",
            "domain": [("id", "in", self.southbrook_remake_task_ids.ids)],
            "view_mode": "list,form,kanban",
            "context": {
                "default_project_id": self.project_id.id,
                "default_parent_id": self.id,
                "default_job_type": "warranty",
            },
        }

    def action_view_southbrook_scrap_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Scrap Records - %s" % (self.name or self.display_name),
            "res_model": "stock.scrap",
            "domain": [("id", "in", self.southbrook_scrap_ids.ids)],
            "view_mode": "list,form",
            "context": {"create": False},
        }

    def action_view_southbrook_unbuild_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Unbuild / Remake Records - %s"
                    % (self.name or self.display_name),
            "res_model": "mrp.unbuild",
            "domain": [("id", "in", self.southbrook_unbuild_ids.ids)],
            "view_mode": "list,form",
            "context": {"create": False},
        }

    def action_view_manufacturing_calculations(self):
        self.ensure_one()
        if "southbrook.mi.check" not in self.env:
            raise UserError(
                "Manufacturing Intelligence is not installed on this database.")
        mos = self.production_ids
        for mo in mos:
            recompute = getattr(
                mo, "action_recompute_manufacturing_intelligence", None)
            if recompute:
                recompute()
        return {
            "type": "ir.actions.act_window",
            "name": "Calculations — %s" % (self.name or self.display_name),
            "res_model": "southbrook.mi.check",
            "domain": [("production_id", "in", mos.ids)],
            "view_mode": "list,form",
            "context": {
                "create": False,
                "search_default_group_category": 1,
            },
        }

    def action_recompute_readiness_lines(self):
        Line = self.env["southbrook.project.readiness.line"].sudo()
        for task in self:
            Line.search([("task_id", "=", task.id)]).unlink()
            values = task._southbrook_readiness_line_values()
            if values:
                Line.create(values)
        return True

    def action_view_readiness_lines(self):
        self.ensure_one()
        self.action_recompute_readiness_lines()
        return {
            "type": "ir.actions.act_window",
            "name": "Readiness Evidence - %s" % (self.name or self.display_name),
            "res_model": "southbrook.project.readiness.line",
            "domain": [("task_id", "=", self.id)],
            "view_mode": "list,form",
            "context": {"create": False, "edit": False},
        }

    def action_view_procurement_orders(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Procurement — %s" % (self.name or self.display_name),
            "res_model": "purchase.order",
            "domain": [("id", "in", self.procurement_order_ids.ids)],
            "view_mode": "list,form",
            "context": {"create": False},
        }

    def action_view_maintenance_requests(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Maintenance — %s" % (self.name or self.display_name),
            "res_model": "maintenance.request",
            "domain": [("id", "in", self.maintenance_request_ids.ids)],
            "view_mode": "list,form",
            "context": {"create": False},
        }

    def action_view_productions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": "Manufacturing Orders",
            "res_model": "mrp.production",
            "domain": [("id", "in", self.production_ids.ids)],
            "view_mode": "list,form", "context": {"create": False},
        }

    def action_apply_southbrook_job_template(self):
        Task = self.env["project.task"]
        Template = self.env["southbrook.project.job.template"]
        for task in self:
            template = task.southbrook_job_template_id
            if not template and task.job_type:
                template = Template.search([
                    ("job_type", "=", task.job_type),
                    ("active", "=", True),
                ], limit=1)
            if not template:
                raise UserError(
                    "Select a Southbrook job template or set a job type.")
            existing_names = set(task.child_ids.mapped("name"))
            for line in template.line_ids:
                if line.name in existing_names:
                    continue
                Task.create({
                    "name": line.name,
                    "project_id": task.project_id.id,
                    "parent_id": task.id,
                    "description": line.description or False,
                })
                existing_names.add(line.name)
            task.southbrook_job_template_id = template.id
        return True

    def action_view_ecos(self):
        self.ensure_one()
        boms = self.production_ids.mapped("bom_id")
        return {
            "type": "ir.actions.act_window", "name": "Engineering Changes",
            "res_model": "southbrook.eco",
            "domain": [("bom_id", "in", boms.ids)],
            "view_mode": "list,form", "context": {"create": False},
        }

    def action_link_productions_from_sale(self):
        Production = self.env["mrp.production"]
        for task in self:
            order = task.x_southbrook_sale_order_id
            if not order:
                continue
            mos = Production.search([
                ("sale_line_id.order_id", "=", order.id),
                ("project_task_id", "=", False)])
            mos.write({"project_task_id": task.id})
        return True
