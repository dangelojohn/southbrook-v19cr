# SPDX-License-Identifier: LGPL-3.0-only
"""Central Command bootstrap controller.

One JSON round-trip (`/command_center/bootstrap`) serving all five dashboard
panels, per DELIVERABLE_5_DELIVERY.md §3. READ-ONLY: this endpoint performs
zero create/write on any business object. Every panel query is wrapped so a
field/model mismatch degrades that one panel to empty rather than 500-ing the
whole dashboard — a resilience requirement because the bootstrap runs on every
dashboard open.
"""
import logging

from odoo import _, http
from odoo.exceptions import AccessError
from odoo.http import request

_logger = logging.getLogger(__name__)


# Per-role default domains on southbrook.command.exception (DELIVERABLE_4_UI.md
# §3). Applied server-side on top of the always-open filter. Unknown role ->
# no extra narrowing (owner/broadest view).
def _domain_for_role(role):
    if role == "production_manager":
        return [("exception_type", "in",
                 ["mi_blocker", "job_blocked", "approval_gate_stall",
                  "bom_skip", "cutlist_divergence"])]
    if role == "shop_foreman":
        return [("exception_type", "in", ["mi_blocker", "breakdown_alert"])]
    if role == "purchasing_manager":
        return [("exception_type", "=", "po_delivery_risk")]
    if role == "warehouse_manager":
        return [("exception_type", "in", ["breakdown_alert", "po_delivery_risk"])]
    # owner / unknown -> high+critical cross-cut, no type narrowing
    return [("severity", "in", ["high", "critical"])]


_CC_GROUPS = (
    "southbrook_command_center.group_command_center_user",
    "southbrook_command_center.group_command_center_owner",
    "southbrook_command_center.group_command_center_production_manager",
    "southbrook_command_center.group_command_center_shop_foreman",
    "southbrook_command_center.group_command_center_purchasing_manager",
    "southbrook_command_center.group_command_center_warehouse_manager",
)


class CommandCenterController(http.Controller):

    def _require_cc_member(self):
        """The routes are auth='user' (any session incl. portal). Require an
        internal Central Command member — otherwise factory_health / flow were
        served to non-members (and factory_health even WRITES the exec snapshot
        via get_or_create_today from a read endpoint)."""
        user = request.env.user
        if not user._is_internal() or not any(
                user.has_group(g) for g in _CC_GROUPS):
            raise AccessError(_("Central Command access required."))

    def _validated_company_id(self, company_id):
        """Never trust a client-supplied company_id — clamp it to a company the
        user is actually allowed in (no ir.rule scopes the exception model, and
        the client-returned cid also drives the bus-channel name)."""
        env = request.env
        if company_id:
            try:
                cid = int(company_id)
            except (TypeError, ValueError):
                return env.company.id
            if cid in env.user.company_ids.ids:
                return cid
        return env.company.id

    @http.route("/command_center/bootstrap", type="jsonrpc", auth="user")
    def bootstrap(self, company_id=None, role=None, **kw):
        self._require_cc_member()
        env = request.env
        cid = self._validated_company_id(company_id)
        payload, provenance = env["southbrook.command.explorer_bridge"].fetch()
        return {
            "company_id": cid,
            "role": role,
            "provenance": provenance,
            "chips": self._chips(env, payload),
            "factory_health": self._factory_health(env),
            "flow": self._flow(env, cid),
            "exceptions": self._exceptions(env, cid, role),
            "exceptions_total": self._exceptions_total(env, cid, role),
            "recommendations": self._recommendations(env, cid),
            "alerts": self._alerts(env, cid),
        }

    # True open-exception count for the panel header — independent of the
    # rendered list's limit, so the counter matches the Exceptions list view
    # and decrements correctly on acknowledge/resolve/dismiss.
    def _exceptions_total(self, env, cid, role):
        try:
            domain = [("company_id", "=", cid),
                      ("state", "not in", ("resolved", "dismissed"))]
            domain += _domain_for_role(role)
            return env["southbrook.command.exception"].search_count(domain)
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: exceptions_total failed")
            return 0

    # -- contextual help: semantic "learn this term" lookup ------------
    @http.route("/command_center/help_lookup", type="jsonrpc", auth="user")
    def help_lookup(self, term=None, **kw):
        self._require_cc_member()
        return self._help_lookup(term)

    def _help_lookup(self, term=None):
        """Return the single best training lesson for a jargon term, using the
        training hub's semantic find_training. Degrades to an eLearning search
        URL if the tool or a match is unavailable — never raises."""
        fallback = {"found": False, "name": term or "",
                    "url": "/slides?search=%s" % (term or "")}
        if not term:
            return {"found": False, "name": "", "url": "/slides"}
        try:
            from odoo.addons.southbrook_training_hub.tools.find_training import (
                find_training,
            )
            result = find_training(request.env, term, 1)
            items = (result or {}).get("items") or []
            if items and items[0].get("url"):
                return {"found": True, "name": items[0].get("name") or term,
                        "url": items[0]["url"]}
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: help_lookup failed for %s", term)
        return fallback

    # -- context bar ---------------------------------------------------
    def _chips(self, env, payload):
        try:
            return env["southbrook.command.center"].truth_chips(payload=payload)
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: chips panel failed")
            return []

    # -- panel 1 -------------------------------------------------------
    def _factory_health(self, env):
        try:
            return env["southbrook.command.center"].factory_health_score()
        except Exception:  # noqa: BLE001 - panel must degrade, never 500
            _logger.exception("command_center: factory_health panel failed")
            return {"score": None, "band": "unknown", "factors": [],
                    "explanation": "Factory Health unavailable."}

    # -- panel 2 (Critical Issues) -------------------------------------
    def _exceptions(self, env, cid, role):
        try:
            domain = [("company_id", "=", cid),
                      ("state", "not in", ("resolved", "dismissed"))]
            domain += _domain_for_role(role)
            return env["southbrook.command.exception"].search_read(
                domain=domain,
                fields=["name", "exception_type", "severity", "severity_rank",
                        "owner_id", "state", "impact_summary",
                        "recommended_action", "why_text", "source_model",
                        "source_res_id", "mo_id", "task_id", "sale_order_id",
                        "purchase_order_id", "hermes_recommendation_id",
                        "create_date"],
                order="severity_rank asc, create_date desc",
                limit=300,
            )
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: exceptions panel failed")
            return []

    # -- panel 3 (Production Flow) -------------------------------------
    def _flow(self, env, cid):
        out = {"mo_state_counts": [], "bottleneck": None, "oee": []}
        try:
            out["mo_state_counts"] = env["mrp.production"].read_group(
                [("state", "in", ("confirmed", "progress", "to_close")),
                 ("company_id", "=", cid)],
                ["state"], ["state"])
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: mo_state_counts failed")
        try:
            report = env["southbrook.mes_mps.bottleneck_report"].search(
                [], order="create_date desc", limit=1)
            if report:
                lines = report.bottleneck_line_ids if "bottleneck_line_ids" in report._fields else report.browse()
                out["bottleneck"] = {
                    "report_id": report.id,
                    "lines": [{"id": ln.id, "name": ln.display_name}
                              for ln in lines[:5]],
                }
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: bottleneck failed")
        return out

    # -- panel 4 (Hermes) ----------------------------------------------
    def _recommendations(self, env, cid=None):
        try:
            Rec = env["southbrook.hermes.recommendation"]
            domain = [("state", "in", ("draft", "ready"))]
            if cid and "company_id" in Rec._fields:
                domain.append(("company_id", "=", cid))
            return Rec.search_read(
                domain=domain,
                fields=["summary", "rationale", "priority", "state",
                        "recommendation_type", "proposed_action",
                        "source_model", "source_res_id"],
                order="priority desc, create_date desc", limit=25)
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: recommendations panel failed")
            return []

    # -- panel 5 (Alert Stream) ----------------------------------------
    def _alerts(self, env, cid):
        try:
            Evt = env["southbrook.ops.event"]
            domain = []
            if cid and "company_id" in Evt._fields:
                domain.append(("company_id", "=", cid))
            return Evt.search_read(
                domain=domain,
                fields=["event_type", "summary", "severity", "res_model",
                        "res_id", "create_date"],
                order="create_date desc", limit=30)
        except Exception:  # noqa: BLE001
            _logger.exception("command_center: alerts panel failed")
            return []
