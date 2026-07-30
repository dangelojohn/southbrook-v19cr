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
from datetime import datetime, timedelta

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
    # 1. Factory Health Score (out of available points)
    # ------------------------------------------------------------------
    def factory_health_score(self):
        """Score out of AVAILABLE points.

        A component with no observations behind it is marked "no data" and
        excluded from the denominator. Scoring absent data as zero deflates
        (visible); scoring it as perfect inflates (invisible). On 2026-07-30
        this screen did both at once: OEE banked 0 of 30 with no snapshots,
        on-time delivery banked 25 of 25 with zero completed shipments.
        """
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
        min_coverage_points = get("min_coverage_points", 50)

        payload, provenance = env["southbrook.command.explorer_bridge"].fetch()

        factors = []

        def _factor(label, of, value, component, source,
                    no_data_reason=None):
            live = no_data_reason is None
            factors.append({
                "label": label,
                "value": value if live else None,
                "points": round(of / 100.0 * component, 1) if live else None,
                "of": of,
                "source": source,
                "data_state": "live" if live else "no_data",
                "no_data_reason": no_data_reason,
            })

        # --- OEE: no data unless a snapshot exists within the 7d window ---
        oee_reason = None
        oee_component = 0.0
        Snap = env.get("southbrook.mes_mps.oee_snapshot")
        if Snap is None:
            oee_reason = "no OEE snapshots recorded"
        else:
            # Guard must match the 7d window that avg_oee_last_7d reads
            last_week = datetime.now().date() - timedelta(days=7)
            if Snap.sudo().search_count([("shift_date", ">=", last_week)]) == 0:
                oee_reason = "no OEE snapshots in the last 7 days"
            else:
                try:
                    tiles = env["southbrook.mes_mps.mi_tiles"].new({})
                    oee_component = _clamp((tiles.avg_oee_last_7d or 0.0) * 100)
                except Exception:  # noqa: BLE001
                    oee_reason = "OEE tiles unavailable"
        _factor("OEE (7d avg)", w_oee * 100,
                f"{oee_component:.0f}%", oee_component,
                "southbrook.mes_mps.oee_snapshot", oee_reason)

        # --- On-time delivery: read Process Explorer's own measure ---
        ontime = (payload.get("ontime") or {}) if payload else {}
        otd_reason = None
        otd_component = 0.0
        if not payload:
            otd_reason = "Process Explorer payload unavailable"
        elif not ontime.get("total"):
            otd_reason = "no closed orders in the window to measure"
        else:
            otd_component = _clamp(ontime.get("pct") or 0.0)
        _factor("On-Time Delivery", w_otd * 100,
                f"{otd_component:.0f}% of {ontime.get('total') or 0} closed orders",
                otd_component, "mrp.process.explorer.get_data().ontime",
                otd_reason)

        # --- First-pass yield: needs closed orders in the 7d window ---
        fpy_reason = None
        fpy_component = 0.0
        # Guard must match the 7d window and company scope that fpy_pct_7d reads
        seven_days_ago = datetime.now() - timedelta(days=7)
        closed_in_window = env["mrp.production"].search_count([
            ("state", "=", "done"),
            ("date_finished", ">=", seven_days_ago),
            ("company_id", "=", env.company.id),
        ])
        if closed_in_window == 0:
            fpy_reason = "no closed manufacturing orders in the last 7 days"
        else:
            try:
                snapshot = env["southbrook.exec_dashboard.snapshot"].get_or_create_today()
                fpy_component = _clamp(snapshot.fpy_pct_7d or 0.0)
            except Exception:  # noqa: BLE001
                fpy_reason = "exec dashboard snapshot unavailable"
        _factor("First-Pass Yield (7d)", w_fpy * 100,
                f"{fpy_component:.0f}%", fpy_component,
                "southbrook.exec_dashboard.snapshot.fpy_pct_7d", fpy_reason)

        # --- Open MI blockers: always measurable ---
        open_blocker_count = env["southbrook.mi.check"].search_count(
            [("severity", "=", "blocker")])
        _factor("Open MI blockers", w_blocker * 100,
                open_blocker_count,
                _clamp(100 - pts_per_blocker * open_blocker_count),
                "southbrook.mi.check (severity=blocker)")

        # --- Capacity: work centers CARRYING WORK, matching Process Explorer.
        # The old denominator was every active work center (14); Process
        # Explorer counts those with open work (8). Same query now.
        stations = (payload.get("workcenters") or []) if payload else []
        cap_reason = None
        cap_component = 0.0
        cap_value = None
        if not payload:
            cap_reason = "Process Explorer payload unavailable"
        elif not stations:
            cap_reason = "no work centers carrying open work"
        else:
            over_days = _get_param(env, "chips.capacity_over_days", 1.0)
            overloaded = sum(1 for s in stations
                             if (s.get("queued_days") or 0) > over_days)
            cap_component = _clamp(100 * (1 - overloaded / len(stations)))
            cap_value = f"{overloaded}/{len(stations)} carrying work"
        _factor("Work centers overloaded", w_capacity * 100,
                cap_value, cap_component,
                "mrp.process.explorer.get_data().workcenters", cap_reason)

        score = round(sum(f["points"] for f in factors if f["points"] is not None))
        available = round(sum(f["of"] for f in factors
                              if f["data_state"] == "live"))
        no_data_points = round(sum(f["of"] for f in factors
                                   if f["data_state"] == "no_data"))

        if not available or available < min_coverage_points:
            band = "insufficient"
            coverage_pct = round(available) if available else 0
        else:
            pct = score / available * 100
            coverage_pct = round(available)
            band = ("green" if pct >= band_green_min
                    else "amber" if pct >= band_amber_min else "red")

        missing = ", ".join(f["label"] for f in factors
                            if f["data_state"] == "no_data") or "none"
        explanation = (
            f"Factory Health {score} of {available} available points ({band}).\n"
            f" {no_data_points} points are unscored — no data for: {missing}.\n"
            " A component with no observations is never scored 0 and never "
            "scored full marks.\n"
            " Click any line to open the records that produced it."
        )

        return {
            "score": score,
            "available": available,
            "coverage_pct": coverage_pct,
            "band": band,
            "factors": factors,
            "explanation": explanation,
            "provenance": provenance,
        }

    # ------------------------------------------------------------------
    # 2. Truth chips — the pinned context bar
    # ------------------------------------------------------------------
    def truth_chips(self, payload=None):
        """The eight click-to-filter counts, read from Process Explorer.

        Units are explicit: ``orders`` counts manufacturing orders while
        ``running``/``ready``/``blocked`` count work orders. Rendered in one
        row without units they imply a total that does not exist.

        A chip with no payload behind it is ``None``, never 0 — zero is a
        claim about the factory, and we would not have grounds for it.
        """
        if payload is None:
            payload, _prov = self.env["southbrook.command.explorer_bridge"].fetch()
        workload = (payload or {}).get("workload") or {}
        rows = (payload or {}).get("rows") or []
        stations = (payload or {}).get("workcenters") or []
        have = bool(payload)
        over_days = _get_param(self.env, "chips.capacity_over_days", 1.0)

        def _v(value):
            return value if have else None

        return [
            {"key": "orders", "label": "orders", "unit": "orders",
             "value": _v(workload.get("orders", 0))},
            {"key": "running", "label": "running", "unit": "steps",
             "value": _v(workload.get("progress", 0))},
            {"key": "ready", "label": "ready", "unit": "steps",
             "value": _v(workload.get("ready", 0))},
            {"key": "blocked", "label": "blocked", "unit": "steps",
             "value": _v(workload.get("blocked", 0))},
            {"key": "late", "label": "late", "unit": "orders",
             "value": _v(workload.get("late", 0))},
            {"key": "at_risk", "label": "at-risk", "unit": "orders",
             "value": _v(workload.get("at_risk", 0))},
            {"key": "material_short", "label": "material-short", "unit": "orders",
             "value": _v(sum(1 for r in rows if r.get("mat_short")))},
            {"key": "capacity_over", "label": "capacity-over", "unit": "stations",
             "value": _v(sum(1 for s in stations
                             if (s.get("queued_days") or 0) > over_days))},
        ]

    # ------------------------------------------------------------------
    # 3. schedule_confidence (per project.task)
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
    # 4. Job margin (per project.task)
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
    # 5. PO delivery-risk (rule-based, NOT a forecast)
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
