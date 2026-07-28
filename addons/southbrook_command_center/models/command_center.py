# SPDX-License-Identifier: LGPL-3.0-only
"""``southbrook.command.center`` — read-time scoring library.

Implements Deliverable 3 (DELIVERABLE_3_SCORING.md). This is an
``AbstractModel`` (no table, no stored fields, no ``@api.depends``) so that
the module budget stays at exactly one real model
(``southbrook.command.exception``, Deliverable 2). Every score is a plain
Python method that takes recordsets/env and returns a dict — there is no
declarative dependency graph here to get wrong, which is the whole point:
Odoo 19 silently breaks the registry on a bad ``@api.depends`` field name,
and these methods sidestep that trap entirely by not being computed fields.

Every threshold/weight is read from ``ir.config_parameter`` via
``self.env['ir.config_parameter'].sudo().get_param('command_center.<key>',
<default>)`` — see ``data/command_center_params.xml`` for the seeded
defaults (all "flagged for owner sign-off" in Deliverable 3 / OQ-3..OQ-6).

Return shape for all four public methods (frozen contract):
    {
        "score": int | float | None,
        "band": str,
        "factors": [ {"label": str, "value": ..., "points": float|None,
                       "of": float|None, "source": str}, ... ],
        "explanation": str,
    }
"""
from datetime import timedelta

from odoo import models


def _clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def _get_param(env, key, default):
    """Read ``command_center.<key>`` from ir.config_parameter, cast to the
    type of ``default`` (int/float/str). Falls back to ``default`` on any
    missing/malformed value rather than raising."""
    raw = env["ir.config_parameter"].sudo().get_param(
        f"command_center.{key}", default
    )
    if raw is default:
        return default
    try:
        if isinstance(default, bool):
            return str(raw).strip().lower() in ("1", "true", "yes")
        if isinstance(default, int):
            return int(float(raw))
        if isinstance(default, float):
            return float(raw)
    except (TypeError, ValueError):
        return default
    return raw


class SouthbrookCommandCenter(models.AbstractModel):
    _name = "southbrook.command.center"
    _description = "Central Command — read-time scoring functions (no ORM state)"

    # ------------------------------------------------------------------
    # 1. Factory Health Score (0-100)
    # ------------------------------------------------------------------
    def factory_health_score(self):
        env = self.env
        get = lambda key, default: _get_param(env, f"factory_health.{key}", default)

        w_oee = get("weight_oee", 0.30)
        w_otd = get("weight_otd", 0.25)
        w_fpy = get("weight_fpy", 0.20)
        w_blocker = get("weight_blocker", 0.15)
        w_capacity = get("weight_capacity", 0.10)
        pts_per_blocker = get("blocker_penalty_per_blocker", 15.0)
        band_green_min = get("band_green_min", 80)
        band_amber_min = get("band_amber_min", 60)

        # --- OEE / bottleneck tiles (REUSES southbrook.mes_mps.mi_tiles) ---
        avg_oee_last_7d = 0.0
        workcenters_overloaded_count = 0
        try:
            tiles = env["southbrook.mes_mps.mi_tiles"].new({})
            avg_oee_last_7d = tiles.avg_oee_last_7d or 0.0
            workcenters_overloaded_count = tiles.workcenters_overloaded_count or 0
        except Exception:
            # Defensive: mi_tiles is a verified model/field set at design
            # time, but never let a scoring panel 500 the whole screen.
            pass

        # --- Exec dashboard KPIs (REUSES southbrook.exec_dashboard.snapshot) ---
        otd_pct_30d = 0.0
        fpy_pct_7d = 0.0
        try:
            snapshot = env["southbrook.exec_dashboard.snapshot"].get_or_create_today()
            otd_pct_30d = snapshot.otd_pct_30d or 0.0
            fpy_pct_7d = snapshot.fpy_pct_7d or 0.0
        except Exception:
            pass

        # --- Open MI blockers (REUSES southbrook.mi.check.severity) ---
        open_blocker_count = env["southbrook.mi.check"].search_count(
            [("severity", "=", "blocker")]
        )

        # --- Capacity denominator (native mrp.workcenter) ---
        total_active_workcenters = env["mrp.workcenter"].search_count(
            [("active", "=", True)]
        )

        oee_component = _clamp(avg_oee_last_7d * 100)
        otd_component = _clamp(otd_pct_30d)
        fpy_component = _clamp(fpy_pct_7d)
        blocker_component = _clamp(100 - pts_per_blocker * open_blocker_count)
        if total_active_workcenters == 0:
            capacity_component = 100.0
            overloaded_ratio = 0.0
        else:
            overloaded_ratio = workcenters_overloaded_count / total_active_workcenters
            capacity_component = _clamp(100 * (1 - overloaded_ratio))

        oee_pts = w_oee * oee_component
        otd_pts = w_otd * otd_component
        fpy_pts = w_fpy * fpy_component
        blocker_pts = w_blocker * blocker_component
        capacity_pts = w_capacity * capacity_component

        score = round(oee_pts + otd_pts + fpy_pts + blocker_pts + capacity_pts)

        if score >= band_green_min:
            band = "green"
        elif score >= band_amber_min:
            band = "amber"
        else:
            band = "red"

        factors = [
            {
                "label": "OEE (7d avg)",
                "value": f"{oee_component:.0f}%",
                "points": round(oee_pts, 1),
                "of": w_oee * 100,
                "source": "southbrook.mes_mps.oee_snapshot (via mi_tiles)",
            },
            {
                "label": "On-Time Delivery (30d)",
                "value": f"{otd_component:.0f}%",
                "points": round(otd_pts, 1),
                "of": w_otd * 100,
                "source": "southbrook.exec_dashboard.snapshot.otd_pct_30d",
            },
            {
                "label": "First-Pass Yield (7d)",
                "value": f"{fpy_component:.0f}%",
                "points": round(fpy_pts, 1),
                "of": w_fpy * 100,
                "source": "southbrook.exec_dashboard.snapshot.fpy_pct_7d",
            },
            {
                "label": "Open MI blockers",
                "value": open_blocker_count,
                "points": round(blocker_pts, 1),
                "of": w_blocker * 100,
                "source": "southbrook.mi.check (severity=blocker)",
            },
            {
                "label": "Workcenters overloaded",
                "value": f"{workcenters_overloaded_count}/{total_active_workcenters}",
                "points": round(capacity_pts, 1),
                "of": w_capacity * 100,
                "source": "southbrook.mes_mps.workcenter_capacity.is_overloaded",
            },
        ]

        explanation = (
            f"Factory Health {score}/100 ({band})\n"
            f" - OEE {oee_component:.0f}% (7d avg) - {oee_pts:.1f} of {w_oee*100:.0f} pts\n"
            f" - On-Time Delivery {otd_component:.0f}% (30d) - {otd_pts:.1f} of {w_otd*100:.0f} pts\n"
            f" - First-Pass Yield {fpy_component:.0f}% (7d) - {fpy_pts:.1f} of {w_fpy*100:.0f} pts\n"
            f" - {open_blocker_count} open MI blocker(s) - {blocker_pts:.1f} of {w_blocker*100:.0f} pts\n"
            f" - {workcenters_overloaded_count}/{total_active_workcenters} workcenters "
            f"overloaded - {capacity_pts:.1f} of {w_capacity*100:.0f} pts\n"
            "Click any line to open its source view (MES/MPS Bottleneck Report, "
            "Exec Dashboard, MI Manager queue)."
        )

        return {"score": score, "band": band, "factors": factors, "explanation": explanation}

    # ------------------------------------------------------------------
    # 2. schedule_confidence (per project.task)
    # ------------------------------------------------------------------
    def schedule_confidence(self, task):
        env = self.env
        get = lambda key, default: _get_param(env, f"schedule_confidence.{key}", default)

        gp_over_capacity = get("gate_penalty_over_capacity", 20.0)
        gp_crew_gap = get("gate_penalty_crew_gap", 15.0)
        gp_equipment_blocked = get("gate_penalty_equipment_blocked", 25.0)
        gp_material_at_risk = get("gate_penalty_material_at_risk", 25.0)
        band_high_min = get("band_high_min", 75)
        band_medium_min = get("band_medium_min", 40)

        wo_total = getattr(task, "linked_wo_count", 0) or 0
        unscheduled = getattr(task, "unscheduled_wo_count", 0) or 0
        unassigned = getattr(task, "unassigned_wo_count", 0) or 0
        over_capacity = bool(getattr(task, "workcenter_over_capacity", False))
        crew_gap = bool(getattr(task, "crew_gap", False))
        equipment_blocked = bool(getattr(task, "equipment_blocked", False))
        material_at_risk = bool(getattr(task, "material_at_risk", False))

        factors = []

        if wo_total == 0:
            score = 0
            band = "low"
            explanation = (
                f"schedule_confidence 0/100 (low) - Job {task.name}\n"
                " No linked work orders yet - nothing to have schedule "
                "confidence in (reason=no_workorders)."
            )
            factors.append(
                {"label": "Linked work orders", "value": 0, "points": None,
                 "of": None, "source": "project.task.linked_wo_count"}
            )
            return {"score": score, "band": band, "factors": factors,
                    "explanation": explanation}

        unscheduled_ratio = unscheduled / wo_total
        unassigned_ratio = unassigned / wo_total
        base = 100 * (1 - 0.5 * unscheduled_ratio - 0.5 * unassigned_ratio)

        gate_penalty = 0.0
        gates = []
        if over_capacity:
            gate_penalty += gp_over_capacity
            gates.append(("workcenter_over_capacity", gp_over_capacity))
        if crew_gap:
            gate_penalty += gp_crew_gap
            gates.append(("crew_gap", gp_crew_gap))
        if equipment_blocked:
            gate_penalty += gp_equipment_blocked
            gates.append(("equipment_blocked", gp_equipment_blocked))
        if material_at_risk:
            gate_penalty += gp_material_at_risk
            gates.append(("material_at_risk", gp_material_at_risk))

        score = round(_clamp(base - gate_penalty))

        if score >= band_high_min:
            band = "high"
        elif score >= band_medium_min:
            band = "medium"
        else:
            band = "low"

        unscheduled_pct = unscheduled_ratio * 100
        unassigned_pct = unassigned_ratio * 100

        factors = [
            {
                "label": "Unscheduled work orders",
                "value": f"{unscheduled}/{wo_total} ({unscheduled_pct:.0f}%)",
                "points": None, "of": None,
                "source": "project.task.unscheduled_wo_count",
            },
            {
                "label": "Unassigned work orders",
                "value": f"{unassigned}/{wo_total} ({unassigned_pct:.0f}%)",
                "points": None, "of": None,
                "source": "project.task.unassigned_wo_count",
            },
        ]
        for gate_name, penalty in gates:
            factors.append(
                {"label": gate_name, "value": True, "points": -penalty,
                 "of": None, "source": f"project.task.{gate_name}"}
            )

        gate_lines = "".join(
            f" - {penalty:.0f} pts: {gate_name} = True (project.task.{gate_name})\n"
            for gate_name, penalty in gates
        )
        explanation = (
            f"schedule_confidence {score}/100 ({band}) - Job {task.name}\n"
            f" base {base:.0f}/100 from {unscheduled}/{wo_total} WOs unscheduled "
            f"({unscheduled_pct:.0f}%) and {unassigned}/{wo_total} unassigned "
            f"({unassigned_pct:.0f}%)\n"
            f"{gate_lines}"
            "Click to open the job's Kitchen Job Command Center tab for full "
            "gate detail (readiness_line_ids)."
        )

        return {"score": score, "band": band, "factors": factors, "explanation": explanation}

    # ------------------------------------------------------------------
    # 3. Job margin (per project.task)
    # ------------------------------------------------------------------
    def job_margin(self, task):
        env = self.env
        get_f = lambda key, default: _get_param(env, f"job_margin.{key}", default)

        at_risk_max_pct = get_f("at_risk_max_pct", 15.0)
        watch_max_pct = get_f("watch_max_pct", 25.0)
        revenue_field = env["ir.config_parameter"].sudo().get_param(
            "command_center.job_margin.revenue_field", "amount_untaxed"
        ) or "amount_untaxed"

        source_order = getattr(task, "source_order_id", False)
        if not source_order:
            return {
                "score": None, "band": "unknown",
                "factors": [{"label": "Source order", "value": None,
                             "points": None, "of": None,
                             "source": "project.task.source_order_id"}],
                "explanation": "Margin unavailable - no_source_order",
            }

        revenue = getattr(source_order, revenue_field, 0.0) or 0.0
        if not revenue:
            return {
                "score": None, "band": "unknown",
                "factors": [{"label": "Revenue", "value": 0,
                             "points": None, "of": None,
                             "source": f"sale.order.{revenue_field}"}],
                "explanation": "Margin unavailable - no_revenue",
            }

        actual_cost = getattr(task, "job_industrial_cost", 0.0) or 0.0
        estimated_cost = getattr(task, "job_estimated_cost", 0.0) or 0.0
        if actual_cost > 0:
            cost_basis = "actual"
            cost = actual_cost
        else:
            cost_basis = "estimated"
            cost = estimated_cost

        # Provenance guard (2026-07-27).
        #
        # A missing cost and a cost of zero are the same float by the time
        # they arrive here, and the arithmetic below cannot tell them apart:
        # (revenue - 0)/revenue is 100%, which bands green. Measured on
        # production that day, 319 of 321 active variants had a NULL
        # standard_price — the only two carrying a cost were "Mileage" and
        # "Event Registration" — so essentially every job was being reported
        # as near-perfect margin on a cost side nobody had entered.
        #
        # A dashboard that says "unknown" is useful. One that says "green"
        # because it has no data is worse than no dashboard, because it is
        # acted upon. Refuse to score rather than score a fiction.
        coverage = getattr(task, "job_cost_coverage", None)
        missing = getattr(task, "job_cost_missing_count", 0) or 0
        if cost <= 0:
            return {
                "score": None, "band": "unknown",
                "factors": [
                    {"label": "Revenue", "value": revenue, "points": None,
                     "of": None,
                     "source": f"sale.order.{revenue_field} on {source_order.name}"},
                    {"label": "Cost", "value": None, "points": None, "of": None,
                     "source": "project.task.job_industrial_cost / "
                               "job_estimated_cost — both zero"},
                ],
                "explanation": (
                    "Margin unavailable - cost_not_loaded\n"
                    f" Revenue {revenue:,.0f} is known, but no cost basis "
                    "exists for this job.\n"
                    f" {missing} of this job's manufacturing orders have "
                    "neither a planned cost nor a product standard cost.\n"
                    " This is NOT a 100% margin. Load product costs "
                    "(standard_price) or run costing on the MOs."
                ),
            }

        margin_amount = revenue - cost
        margin_pct = (margin_amount / revenue) * 100

        if margin_pct < at_risk_max_pct:
            band = "red"
        elif margin_pct <= watch_max_pct:
            band = "amber"
        else:
            band = "green"

        # Partial coverage understates cost, which inflates margin. Report
        # the number, but never let an incomplete cost basis show green —
        # a green banded on half the costs is the same error in miniature.
        partial = coverage is not None and 0 < coverage < 1.0
        if partial and band == "green":
            band = "amber"

        factors = [
            {"label": "Revenue", "value": revenue, "points": None, "of": None,
             "source": f"sale.order.{revenue_field} on {source_order.name}"},
            {"label": f"Cost ({cost_basis})", "value": cost, "points": None,
             "of": None,
             "source": "project.task.job_industrial_cost / job_estimated_cost"},
        ]
        if coverage is not None:
            factors.append(
                {"label": "Cost coverage", "value": round(coverage * 100, 0),
                 "points": None, "of": 100,
                 "source": "project.task.job_cost_coverage"})

        partial_note = ""
        if partial:
            partial_note = (
                f" WARNING: only {coverage * 100:.0f}% of this job's MOs carry "
                f"a cost ({missing} missing). Cost is understated, so the true "
                "margin is LOWER than shown. Band held at amber.\n")

        explanation = (
            f"Job margin: {margin_amount:,.0f} ({margin_pct:.1f}%) - "
            f"{cost_basis} cost basis\n"
            + partial_note +
            f" Revenue {revenue:,.0f} (sale.order.{revenue_field} on "
            f"{source_order.name})\n"
            f" - Cost {cost:,.0f} (project.task.job_industrial_cost / "
            "job_estimated_cost)\n"
            f" At-risk threshold: <{at_risk_max_pct:.0f}% red / "
            f"{at_risk_max_pct:.0f}-{watch_max_pct:.0f}% amber / "
            f">{watch_max_pct:.0f}% green\n"
            "Click to open the sale order or the MO cost report."
        )

        return {
            "score": round(margin_pct, 1), "band": band, "factors": factors,
            "explanation": explanation,
        }

    # ------------------------------------------------------------------
    # 4. PO delivery-risk (rule-based, NOT a forecast)
    # ------------------------------------------------------------------
    def po_delivery_risk(self, purchase_order, task=None):
        env = self.env
        get_i = lambda key, default: _get_param(env, f"po_delivery_risk.{key}", default)

        min_receipts = get_i("min_receipts", 5)
        grace_days = get_i("grace_days", 1)
        slack_threshold_days = get_i("slack_threshold_days", 3)
        band_red_min = get_i("band_red_min", 60.0)
        band_amber_min = get_i("band_amber_min", 25.0)

        vendor = getattr(purchase_order, "partner_id", False)

        # --- Step 1: vendor reliability ratio. OQ-1 RESOLVED (verified on
        # prod 2026-07-05): stock.picking.scheduled_date (planned) and
        # date_done (effective) both exist, so this computes a real ratio
        # (reliability_note="computed"). The _fields guard below is retained
        # as harmless insurance against non-standard stock variants. ---
        vendor_reliability_ratio = None
        reliability_note = "insufficient_history"
        n_receipts = 0
        if vendor:
            done_pickings = env["stock.picking"].search(
                [
                    ("partner_id", "=", vendor.id),
                    ("picking_type_id.code", "=", "incoming"),
                    ("state", "=", "done"),
                ]
            )
            n_receipts = len(done_pickings)
            if n_receipts >= min_receipts:
                scheduled_field = "scheduled_date"
                effective_field = None
                for candidate in ("date_done", "date"):
                    if candidate in done_pickings._fields:
                        effective_field = candidate
                        break
                if (
                    scheduled_field in done_pickings._fields
                    and effective_field
                ):
                    on_time = 0
                    for picking in done_pickings:
                        scheduled = getattr(picking, scheduled_field, False)
                        effective = getattr(picking, effective_field, False)
                        if scheduled and effective and (
                            effective <= scheduled + timedelta(days=grace_days)
                        ):
                            on_time += 1
                    vendor_reliability_ratio = on_time / n_receipts
                    reliability_note = "computed"
                else:
                    # OQ-1 unresolved in this environment: field names could
                    # not be confirmed. Degrade rather than guess.
                    reliability_note = "unverified_core_field"
            else:
                reliability_note = "insufficient_history"

        # --- Step 2: lead-time slack ---
        mo_need_by = None
        if task is not None:
            open_mos = task.production_ids.filtered(
                lambda mo: mo.state not in ("done", "cancel") and mo.date_deadline
            )
            if open_mos:
                mo_need_by = min(open_mos.mapped("date_deadline"))

        po_expected_receipt = None
        for candidate in ("date_planned",):
            if candidate in purchase_order._fields:
                po_expected_receipt = getattr(purchase_order, candidate, False) or None
                break
        if not po_expected_receipt and getattr(purchase_order, "order_line", False):
            dates = [
                line.date_planned
                for line in purchase_order.order_line
                if getattr(line, "date_planned", False)
            ]
            if dates:
                po_expected_receipt = min(dates)

        if mo_need_by is None or po_expected_receipt is None:
            return {
                "score": None, "band": "not_applicable",
                "factors": [
                    {"label": "Linked MO need-by", "value": mo_need_by,
                     "points": None, "of": None,
                     "source": "project.task.production_ids.date_deadline"},
                    {"label": "PO expected receipt", "value": po_expected_receipt,
                     "points": None, "of": None,
                     "source": "purchase.order(.line).date_planned"},
                ],
                "explanation": f"PO {purchase_order.name}: not linked to a "
                               "scheduled MO - no risk score computed.",
            }

        lead_time_slack_days = (mo_need_by - po_expected_receipt).days

        # --- Step 3: combine ---
        if lead_time_slack_days < 0:
            risk_score = 100.0
            band = "red"
        elif vendor_reliability_ratio is None:
            risk_score = 50.0
            band = "amber"
        else:
            reliability_penalty = (1 - vendor_reliability_ratio) * 100
            slack_buffer = min(lead_time_slack_days / slack_threshold_days, 1.0) \
                if slack_threshold_days else 1.0
            risk_score = reliability_penalty * (1 - slack_buffer)
            if risk_score > band_red_min:
                band = "red"
            elif risk_score >= band_amber_min:
                band = "amber"
            else:
                band = "green"

        ratio_pct = (
            f"{vendor_reliability_ratio * 100:.0f}"
            if vendor_reliability_ratio is not None else "n/a"
        )
        insufficiency_note = (
            f"\n [{reliability_note}: vendor has fewer than {min_receipts} "
            "recorded receipts or the core date fields could not be "
            "confirmed - reliability ratio not computed; defaulting to "
            "caution.]"
            if vendor_reliability_ratio is None else ""
        )

        factors = [
            {
                "label": "Vendor on-time ratio",
                "value": f"{ratio_pct}% over {n_receipts} receipts",
                "points": None, "of": None,
                "source": "stock.picking (scheduled vs effective date)",
            },
            {
                "label": "Lead-time slack",
                "value": f"{lead_time_slack_days} days",
                "points": None, "of": None,
                "source": "mrp.production.date_deadline vs purchase.order.date_planned",
            },
        ]

        explanation = (
            f"PO {purchase_order.name} delivery risk: {band} "
            f"({risk_score:.0f}/100)\n"
            f" Vendor {vendor.name if vendor else 'n/a'}: {ratio_pct}% on-time "
            f"over {n_receipts} receipts (stock.picking scheduled vs. "
            "effective date - see Open Questions)\n"
            f" Lead-time slack vs. need-by ({mo_need_by}): "
            f"{lead_time_slack_days} days "
            f"{'BEHIND' if lead_time_slack_days < 0 else 'buffer'}"
            f"{insufficiency_note}\n"
            "Click to open the PO, the vendor's receipt history, or the "
            "linked MO."
        )

        return {
            "score": round(risk_score), "band": band, "factors": factors,
            "explanation": explanation,
        }
