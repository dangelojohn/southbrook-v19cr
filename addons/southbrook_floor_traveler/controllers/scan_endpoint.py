# SPDX-License-Identifier: LGPL-3.0-only
"""P8 — Workcenter scan endpoint.

POST /southbrook/api/floor-traveler/scan   (type=jsonrpc, unchanged contract)
  { "qr_payload": "sb-package:<id>", "workcenter_code": "<wc>" }

Returns:
  { "ok": True, "wo_id": int|null, "wo_state": "...", "scan_count": int }

The audit's load-bearing acceptance: a simulated scan -> button_finish
drives the existing tool-consumption debit *exactly once* — no
duplicate telemetry from re-implementing the debit here. We just call
the existing override.

GET /southbrook/api/floor-traveler/scan    (type=http, NEW — UX-P1)
  ?qr_payload=sb-package:<id>&workcenter_code=<wc>

UX-P1 (2026-07-31, ux-shopfloor.md #2, "worst finding") — before this
addition, the ONLY way a human ever saw the result of a floor scan was
whatever raw JSON their browser happened to render for a POST response
— frequently nothing at all, since a browser navigating a URL sends a
GET, and the endpoint only accepted POST. This file now serves BOTH:
the pre-existing type="jsonrpc" POST route for programmatic/API
callers (contract unchanged — same URL, same method, same response
shape), and a new type="http" GET route at the SAME path (mirroring
the established `/sb/qr/scan` GET-html/POST-json split in
southbrook_qr_kit/controllers/qr_scan.py) that renders a full-page,
high-contrast confirmation screen for a tablet/browser scan.

Both routes share one resolver, `_resolve_scan()`, so the two paths
can never disagree about what happened on a given scan — the JSON
handler maps the resolver's outcome onto the legacy response shape;
the HTML handler maps the same outcome onto a status-token-driven
page. New failure outcomes introduced here (`wrong_step`, `expired`)
are ADDITIVE to the JSON contract: existing callers that only read
`ok`/`error` are unaffected; the extra keys are new information, not
a change to established ones.
"""
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# UX-P1 — expiry only applies to the SIGNED qr_kit payload format
# (`sb://pkg/<id>?t=<ts>&s=<hmac>`), which is the only one carrying an
# issue timestamp. The legacy bare `sb-package:<id>` format (every
# floor_traveler PDF printed before qr_kit existed, and still the
# default today — see sb_production_package.py's `qr_payload` compute)
# has no timestamp and therefore never expires; that is a deliberate,
# documented property of that format, not an oversight.
_DEFAULT_TTL_HOURS = 720  # 30 days
_TTL_PARAM = "southbrook_floor_traveler.scan_qr_ttl_hours"
_TTL_MIN_HOURS = 1
_TTL_MAX_HOURS = 8760  # 1 year


class FloorTravelerScanAPI(http.Controller):

    # ------------------------------------------------------------------
    # Programmatic / API path — UNCHANGED CONTRACT
    # ------------------------------------------------------------------
    @http.route(
        "/southbrook/api/floor-traveler/scan",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def floor_traveler_scan(self, qr_payload=None, workcenter_code=None, **kw):
        """Receive a scan event from the shop floor.

        :param qr_payload: the QR's decoded text. Accepts BOTH formats:
            - Legacy:  "sb-package:<id>"  (pre-qr_kit, all current
              floor_traveler PDFs use this)
            - qr_kit:  "sb://pkg/<id>?t=<ts>&s=<hmac>"  (signed
              payload for the unified /sb/qr/scan controller; lets
              external scanners that already speak qr_kit's wire
              format hit this endpoint without an upgrade)
        :param workcenter_code: free-text identifier of the scanning
            station (e.g. "PANEL-SAW-01").

        Unified audit (added 2026-06-26): every scan now ALSO writes
        to southbrook.qr.scan.log (when qr_kit is installed) so the
        legacy + qr_kit scan paths feed one search/group/dashboard
        instead of two.
        """
        env = request.env
        outcome = self._resolve_scan(env, qr_payload, workcenter_code)
        return self._outcome_to_json(outcome)

    # ------------------------------------------------------------------
    # Human / browser path — NEW (UX-P1)
    # ------------------------------------------------------------------
    @http.route(
        "/southbrook/api/floor-traveler/scan",
        type="http",
        auth="user",
        methods=["GET"],
        website=False,
        csrf=False,
    )
    def floor_traveler_scan_page(self, qr_payload=None, workcenter_code=None,
                                 **kw):
        """Same scan, rendered for a person instead of a program.

        A GET at the same URL the JSON endpoint POSTs to — matching
        the split already established by southbrook_qr_kit's
        `/sb/qr/scan` (GET=html, POST=jsonrpc). Every outcome (success
        AND every failure case) gets a full-page, high-contrast,
        arm's-length-legible card; nothing here ever hands a browser a
        raw JSON blob or a Python traceback.
        """
        env = request.env
        outcome = self._resolve_scan(env, qr_payload, workcenter_code)
        body = self._render_scan_result_html(env, outcome)
        return request.make_response(
            body, headers=[("Content-Type", "text/html; charset=utf-8")])

    # ------------------------------------------------------------------
    # Shared core — the ONE place scan outcomes are decided
    # ------------------------------------------------------------------
    def _resolve_scan(self, env, qr_payload, workcenter_code):
        """Resolve a scan to a single outcome dict. Both entry points
        above call this and only this — they never re-implement any
        of the decision logic, so JSON and HTML can never disagree.

        Returns a dict always carrying:
          outcome: one of "forbidden", "invalid_payload", "not_found",
                   "expired", "wrong_step", "already_done", "ok"
          ok:      bool — True only for "already_done" and "ok"
                   ("already_done" is informational, not a rejection —
                   it mirrors the pre-existing JSON contract where a
                   no-next-workorder scan already returned ok:true
                   with wo_id: null).
        Present when relevant: error (short technical string, JSON
        contract only), package/wo (recordsets), extra (dict of
        outcome-specific display data for the HTML page).
        """
        caller = env.user
        # SECURITY: unchanged from the pre-existing gate — auth="user"
        # admits any session; require an internal mrp operator so a
        # portal dealer/customer can't finish production by guessing a
        # package id (legacy sb-package:<id> is unsigned).
        if not caller._is_internal() \
                or not caller.has_group("mrp.group_mrp_user"):
            self._log_unified(env, qr_payload, "access_denied",
                              error="caller not an internal mrp operator",
                              workcenter_code=workcenter_code)
            return {"outcome": "forbidden", "ok": False,
                    "error": "forbidden"}

        if not qr_payload:
            self._log_unified(env, qr_payload, "error",
                              error="empty qr_payload",
                              workcenter_code=workcenter_code)
            return {"outcome": "invalid_payload", "ok": False,
                    "error": "empty qr_payload"}

        package_id, expired, ttl_hours = self._resolve_package_id(
            env, qr_payload)
        if expired:
            self._log_unified(env, qr_payload, "expired",
                              error="signed qr_payload past TTL",
                              workcenter_code=workcenter_code)
            return {"outcome": "expired", "ok": False, "error": "expired",
                    "extra": {"ttl_hours": ttl_hours}}
        if package_id is None:
            self._log_unified(env, qr_payload, "error",
                              error="unrecognized payload format",
                              workcenter_code=workcenter_code)
            return {"outcome": "invalid_payload", "ok": False, "error":
                    "invalid qr_payload (need 'sb-package:<id>' or "
                    "signed 'sb://pkg/<id>')"}

        Package = env["sb.production.package"].sudo()
        package = Package.browse(package_id).exists()
        if not package:
            self._log_unified(env, qr_payload, "record_not_found",
                              kind="pkg", ident=str(package_id),
                              error=f"package id={package_id} not found",
                              workcenter_code=workcenter_code)
            return {"outcome": "not_found", "ok": False,
                    "error": "package not found",
                    "extra": {"package_id": package_id}}

        next_wo = package._sbk_next_workorder()

        # UX-P1 — out-of-sequence / wrong-station guard. Previously the
        # endpoint finished WHATEVER the next ready/progress workorder
        # was regardless of the workcenter_code the caller declared —
        # a scan at the wrong station would silently advance the wrong
        # step. Only enforced when both a candidate WO and a caller-
        # declared station exist; a caller that never sends
        # workcenter_code keeps the old permissive behaviour (matches
        # existing tests that call record_scan()/the endpoint without
        # a station code).
        if next_wo and workcenter_code:
            wc = next_wo.workcenter_id
            candidates = {
                v for v in (
                    (wc.code or "").strip().upper(),
                    (wc.name or "").strip().upper(),
                ) if v
            }
            given = (workcenter_code or "").strip().upper()
            if candidates and given not in candidates:
                self._log_unified(env, qr_payload, "error", kind="pkg",
                                  ident=str(package.id),
                                  target_model="sb.production.package",
                                  target_id=package.id,
                                  error=(f"wrong station: scanned at "
                                         f"'{workcenter_code}', expected "
                                         f"'{wc.display_name}'"),
                                  workcenter_code=workcenter_code)
                return {"outcome": "wrong_step", "ok": False,
                        "error": "wrong station", "package": package,
                        "extra": {"expected_workcenter": wc.display_name,
                                  "given_workcenter": workcenter_code}}

        wo = package.record_scan(workcenter_code=workcenter_code)

        self._log_unified(env, qr_payload, "ok", kind="pkg",
                          ident=str(package.id),
                          target_model="sb.production.package",
                          target_id=package.id,
                          workcenter_code=workcenter_code)

        outcome = "ok" if next_wo else "already_done"
        return {"outcome": outcome, "ok": True, "package": package,
                "wo": wo}

    def _outcome_to_json(self, outcome):
        """Map a resolver outcome onto the pre-existing JSON response
        shape. `ok`/`already_done` reuse the exact legacy keys
        (package_id/wo_id/wo_state/scan_count); failure outcomes keep
        the legacy {ok, error} shape and only ADD new keys for the two
        new failure kinds (wrong_step, expired) — never remove or
        rename an existing one."""
        kind = outcome["outcome"]
        if kind in ("ok", "already_done"):
            package = outcome["package"]
            wo = outcome.get("wo")
            log = self._read_scan_log(package)
            return {
                "ok": True,
                "package_id": package.id,
                "wo_id": wo.id if wo else None,
                "wo_state": wo.state if wo else None,
                "scan_count": len(log),
            }
        result = {"ok": False, "error": outcome.get("error", kind)}
        extra = outcome.get("extra") or {}
        if kind == "wrong_step":
            result["expected_workcenter"] = extra.get("expected_workcenter")
        elif kind == "expired":
            result["ttl_hours"] = extra.get("ttl_hours")
        elif kind == "not_found":
            result["package_id"] = extra.get("package_id")
        return result

    def _read_scan_log(self, package):
        try:
            return json.loads(package.x_scan_log_json or "[]")
        except (TypeError, ValueError):
            return []

    # ------------------------------------------------------------------
    # HTML rendering — the "real response" this task exists to add
    # ------------------------------------------------------------------
    # UX-P1 copy standard (southbrook-design-system skill): every card
    # says what happened AND what to do next, in the user's vocabulary
    # — no internal codes, no model names, no raw enum values. Every
    # entry maps to an --sb-status-* token class (see scan_result.scss)
    # for its colour; "ok" wins ok, "already_done" wins info
    # (nothing is wrong, there's just nothing left to do), everything
    # else is a hard stop (blocked) — the endpoint refused to act.
    _OUTCOME_COPY = {
        "ok": {
            "token": "ok",
            "badge": "CONFIRMED",
            "title": "Scan confirmed",
        },
        "already_done": {
            "token": "info",
            "badge": "ALREADY DONE",
            "title": "Nothing left to scan",
        },
        "wrong_step": {
            "token": "blocked",
            "badge": "WRONG STATION",
            "title": "This isn't the next step",
            "next": ("Bring the package to {expected}, or ask your "
                     "supervisor if the routing changed."),
        },
        "expired": {
            "token": "blocked",
            "badge": "EXPIRED",
            "title": "This traveler has expired",
            "next": "Print a fresh traveler for this job before continuing.",
        },
        "not_found": {
            "token": "blocked",
            "badge": "NOT FOUND",
            "title": "Package not found",
            "next": ("Check you scanned the right sheet. If this job is "
                     "still active, ask your supervisor to reissue the "
                     "traveler."),
        },
        "invalid_payload": {
            "token": "blocked",
            "badge": "CODE NOT RECOGNIZED",
            "title": "This code isn't recognized",
            "next": ("Reprint the traveler for this job, or ask your "
                     "supervisor for a new one."),
        },
        "forbidden": {
            "token": "blocked",
            "badge": "NOT SIGNED IN",
            "title": "This scan didn't count",
            "next": ("Your account isn't set up as a shop-floor operator. "
                     "Ask your supervisor to add you, or scan from a "
                     "shared station login."),
        },
    }

    def _render_scan_result_html(self, env, outcome):
        """Render the QWeb confirmation page for one outcome.

        Renders via `ir.ui.view._render_template` directly (string
        result) rather than `request.render(...)` — v19's
        `request.render` returns a LAZY Response whose body is empty
        until `flatten()`/serialization; `southbrook_qr_kit`'s
        `_render_floor_shell` hit this exact trap (see its own
        comments) and the fix is the same here: render eagerly to a
        string, prepend the DOCTYPE, hand it to `make_response`."""
        kind = outcome["outcome"]
        copy = self._OUTCOME_COPY[kind]
        package = outcome.get("package")
        wo = outcome.get("wo")
        extra = outcome.get("extra") or {}

        detail_rows = []
        if package is not None:
            detail_rows.append({"label": "Package", "value":
                                package.name or "-"})
            if package.mo_id:
                detail_rows.append({"label": "Job", "value":
                                    package.mo_id.name or "-"})
        if kind == "ok" and wo is not None:
            detail_rows.append({"label": "Step completed", "value":
                                wo.name or "-"})
            if wo.workcenter_id:
                detail_rows.append({"label": "Station", "value":
                                    wo.workcenter_id.display_name})
        if kind == "wrong_step":
            detail_rows.append({"label": "Expected station", "value":
                                extra.get("expected_workcenter") or "-"})
            detail_rows.append({"label": "You scanned at", "value":
                                extra.get("given_workcenter") or "-"})
        if kind == "not_found" and extra.get("package_id") is not None:
            detail_rows.append({"label": "Scanned code", "value":
                                "package #%s" % extra["package_id"]})
        if kind == "expired" and extra.get("ttl_hours"):
            days = max(1, int(extra["ttl_hours"]) // 24)
            detail_rows.append({"label": "Valid for", "value":
                                "%d day(s) after printing" % days})

        next_step_line = ""
        if kind == "ok" and package is not None:
            upcoming = package._sbk_next_workorder()
            if upcoming:
                next_step_line = "Next step: %s" % (upcoming.name or "-")
                if upcoming.workcenter_id:
                    next_step_line += " (%s)" % (
                        upcoming.workcenter_id.display_name)
            else:
                next_step_line = "This was the last step for this job."

        next_text = copy.get("next", "")
        if "{expected}" in next_text:
            next_text = next_text.format(
                expected=extra.get("expected_workcenter") or "the "
                "correct station")

        body = env["ir.ui.view"]._render_template(
            "southbrook_floor_traveler.scan_result",
            {
                "token": copy["token"],
                "badge": copy["badge"],
                "title": copy["title"],
                "detail_rows": detail_rows,
                "next_text": next_text,
                "next_step_line": next_step_line,
            },
        )
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        if not body.lstrip().lower().startswith("<!doctype"):
            body = "<!DOCTYPE html>\n" + body
        return body

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_package_id(self, env, qr_payload):
        """Accept either legacy 'sb-package:<id>' or qr_kit
        'sb://pkg/<id>?...'. Returns (package_id_or_None, expired_bool,
        ttl_hours_or_None).

        Only the signed qr_kit format carries an issue timestamp, so
        `expired` can only ever be True for that format — see the
        module docstring."""
        if qr_payload.startswith("sb-package:"):
            try:
                return int(qr_payload.split(":", 1)[1]), False, None
            except (TypeError, ValueError):
                return None, False, None
        if qr_payload.startswith("sb://"):
            Payload = env.get("southbrook.qr.payload")
            if not Payload:
                return None, False, None  # qr_kit not installed
            try:
                parsed = Payload.sudo().parse(qr_payload)
                if not (parsed.get("kind") == "pkg"
                        and parsed.get("valid_signature")):
                    return None, False, None
                ttl_hours = self._scan_ttl_hours(env)
                if ttl_hours:
                    age_seconds = time.time() - float(parsed.get("ts") or 0)
                    if age_seconds > ttl_hours * 3600:
                        return None, True, ttl_hours
                return int(parsed["ident"]), False, ttl_hours
            except Exception:  # noqa: BLE001
                return None, False, None
        return None, False, None

    def _scan_ttl_hours(self, env):
        """TTL (hours) for the SIGNED qr_kit payload format only.
        0 = no expiry. Clamped [1, 8760] the same way the qr_kit
        client_uuid TTL is clamped, for the same reason: an ops admin
        can tune this via ir.config_parameter without a redeploy."""
        raw = env["ir.config_parameter"].sudo().get_param(
            _TTL_PARAM, str(_DEFAULT_TTL_HOURS))
        try:
            hours = int(raw)
        except (TypeError, ValueError):
            hours = _DEFAULT_TTL_HOURS
        if hours <= 0:
            return 0
        return max(_TTL_MIN_HOURS, min(hours, _TTL_MAX_HOURS))

    def _log_unified(self, env, qr_payload, result, kind="",
                     ident="", target_model="", target_id=0,
                     error="", workcenter_code=""):
        """Best-effort write to southbrook.qr.scan.log. Wrapped in
        try/except so the legacy scan endpoint NEVER fails because
        the qr_kit log model is missing/unavailable."""
        try:
            if "southbrook.qr.scan.log" not in env:
                return
            env["southbrook.qr.scan.log"].sudo().create({
                # Record the REAL caller — the create runs sudo (so create_uid
                # would be OdooBot), which destroys attribution on a production-
                # mutation audit log.
                "user_id": env.uid,
                "kind": kind,
                "ident": ident,
                "action": "scan",  # legacy endpoint is always "scan"
                "result": result,
                "target_model": target_model,
                "target_id": target_id,
                "error_message": error[:255] if error else "",
                "payload": (qr_payload or "")[:500],
                "source_ip": request.httprequest.remote_addr,
                "user_agent":
                    (request.httprequest.headers.get("User-Agent") or "")[:255],
            })
        except Exception:  # noqa: BLE001
            _logger.warning(
                "floor-traveler scan: unified log write failed (non-fatal)")
