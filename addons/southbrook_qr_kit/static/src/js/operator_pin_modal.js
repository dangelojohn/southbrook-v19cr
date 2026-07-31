/** @odoo-module **/
// SPDX-License-Identifier: LGPL-3.0-only
//
// W035 (R8.14, 2026-06-27) — Operator PIN gate.
//
// Mounts a top-bar operator badge + numeric-keypad modal on every
// page (backend + frontend bundles). The modal:
//
//   * Auto-shows on tablet load when no operator is bound and the
//     URL is under /odoo, /web or /sb/ (i.e. backend or scan UI).
//   * Shows on click of the badge (Switch Operator).
//   * Closes on a successful POST to /sb/qr/identify; updates the
//     badge with the resolved employee name.
//
// Security:
//   * PIN value lives only in the modal's input element. We never
//     localStorage it, never put it in the URL, never echo it back.
//   * Employee NAME is cached in sessionStorage (per-tab) so a hard
//     reload doesn't re-prompt mid-shift; it's cleared on Switch.
//   * Session-side timeout is enforced server-side by the controller.

(function () {
    "use strict";

    // Skip on auth pages, error pages, public POD page (its own UX).
    const path = window.location.pathname || "";
    const SKIP_PATHS = [
        "/web/login",
        "/web/signup",
        "/web/reset_password",
        "/sb/qr/pod",       // public POD capture has its own UI
    ];
    if (SKIP_PATHS.some((p) => path.startsWith(p))) {
        return;
    }

    const BADGE_ID = "sbk-op-badge";
    const MODAL_ID = "sbk-op-modal";
    const NAME_CACHE_KEY = "sbk.operator.name";

    function postJSON(url, params) {
        return fetch(url, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            credentials: "same-origin",
            body: JSON.stringify({
                jsonrpc: "2.0",
                method: "call",
                params: params || {},
            }),
        }).then((r) => r.json()).then((j) => j.result || {});
    }

    function getCachedName() {
        try {
            return window.sessionStorage.getItem(NAME_CACHE_KEY) || "";
        } catch (e) {
            return "";
        }
    }
    function setCachedName(name) {
        try {
            if (name) {
                window.sessionStorage.setItem(NAME_CACHE_KEY, name);
            } else {
                window.sessionStorage.removeItem(NAME_CACHE_KEY);
            }
        } catch (e) {
            // noop — private mode
        }
    }

    function renderBadge(name) {
        let badge = document.getElementById(BADGE_ID);
        if (!badge) {
            badge = document.createElement("div");
            badge.id = BADGE_ID;
            badge.className = "sbk-op-badge";
            document.body.appendChild(badge);
            badge.addEventListener("click", () => showModal(true));
        }
        if (name) {
            badge.textContent = "Op: " + name + " (switch)";
            badge.classList.add("sbk-op-badge--bound");
        } else {
            badge.textContent = "No operator — tap to PIN in";
            badge.classList.remove("sbk-op-badge--bound");
        }
    }

    function buildModalHtml() {
        return (
            '<div class="sbk-op-modal-backdrop" id="' + MODAL_ID + '">' +
            '  <div class="sbk-op-modal-card" role="dialog" aria-modal="true" aria-labelledby="sbk-op-modal-title">' +
            '    <h2 id="sbk-op-modal-title">Enter your PIN</h2>' +
            '    <div class="sbk-op-modal-display"><input id="sbk-op-pin" type="password" inputmode="numeric" pattern="[0-9]*" autocomplete="off" maxlength="12" /></div>' +
            '    <div class="sbk-op-modal-status" id="sbk-op-status" aria-live="polite"></div>' +
            '    <div class="sbk-op-modal-keypad">' +
            "      " + [1, 2, 3, 4, 5, 6, 7, 8, 9].map(
                (n) => '<button type="button" class="sbk-op-key" data-d="' + n + '">' + n + "</button>"
            ).join("") +
            '      <button type="button" class="sbk-op-key sbk-op-key--util" data-act="clear">Clear</button>' +
            '      <button type="button" class="sbk-op-key" data-d="0">0</button>' +
            '      <button type="button" class="sbk-op-key sbk-op-key--util" data-act="back">⌫</button>' +
            "    </div>" +
            '    <div class="sbk-op-modal-actions">' +
            '      <button type="button" class="sbk-op-btn sbk-op-btn--cancel" data-act="cancel">Cancel</button>' +
            '      <button type="button" class="sbk-op-btn sbk-op-btn--ok" data-act="ok">Sign In</button>' +
            "    </div>" +
            "  </div>" +
            "</div>"
        );
    }

    function showModal(force) {
        if (!force && document.getElementById(MODAL_ID)) {
            return;
        }
        const existing = document.getElementById(MODAL_ID);
        if (existing) {
            existing.remove();
        }
        const wrap = document.createElement("div");
        wrap.innerHTML = buildModalHtml();
        const node = wrap.firstElementChild;
        document.body.appendChild(node);

        const input = node.querySelector("#sbk-op-pin");
        const status = node.querySelector("#sbk-op-status");

        function setStatus(msg, isErr) {
            status.textContent = msg || "";
            status.className = "sbk-op-modal-status" + (isErr ? " sbk-op-modal-status--err" : "");
        }

        function close() {
            node.remove();
        }

        function submit() {
            const pin = (input.value || "").trim();
            if (!pin) {
                setStatus("Enter your PIN.", true);
                return;
            }
            setStatus("Checking…", false);
            postJSON("/sb/qr/identify", {pin}).then((res) => {
                if (res && res.ok && res.employee) {
                    setCachedName(res.employee.name);
                    renderBadge(res.employee.name);
                    close();
                } else {
                    setStatus((res && res.error) || "PIN rejected.", true);
                    input.value = "";
                    input.focus();
                }
            }).catch((err) => {
                setStatus("Network error: " + err, true);
            });
        }

        node.addEventListener("click", (ev) => {
            const t = ev.target;
            if (!(t instanceof HTMLElement)) {
                return;
            }
            if (t.classList.contains("sbk-op-modal-backdrop")) {
                // Click outside card = cancel
                close();
                return;
            }
            const d = t.getAttribute("data-d");
            const act = t.getAttribute("data-act");
            if (d !== null) {
                if (input.value.length < 12) {
                    input.value = input.value + d;
                }
                input.focus();
            } else if (act === "back") {
                input.value = input.value.slice(0, -1);
                input.focus();
            } else if (act === "clear") {
                input.value = "";
                input.focus();
            } else if (act === "cancel") {
                close();
            } else if (act === "ok") {
                submit();
            }
        });

        input.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") {
                ev.preventDefault();
                submit();
            } else if (ev.key === "Escape") {
                close();
            }
        });

        input.focus();
    }

    function switchOperator() {
        postJSON("/sb/qr/switch-operator", {}).then(() => {
            setCachedName("");
            renderBadge("");
            showModal(true);
        });
    }

    function init() {
        // Show cached name immediately for visual continuity, then
        // re-sync against server in background.
        const cached = getCachedName();
        renderBadge(cached);
        postJSON("/sb/qr/whoami", {}).then((res) => {
            if (res && res.ok) {
                if (res.employee && res.employee.name) {
                    setCachedName(res.employee.name);
                    renderBadge(res.employee.name);
                } else {
                    setCachedName("");
                    renderBadge("");
                    // Auto-prompt on first load if no operator bound
                    // and we're on a floor surface.
                    //
                    // UX-P1 (2026-07-31, ux-shopfloor.md #1) — the
                    // standalone Bin-Scan / Load-Unit tablets are
                    // served at /southbrook/floor/bin-scan and
                    // /southbrook/floor/load-unit (see
                    // southbrook_qr_kit/controllers/qr_scan.py), which
                    // matched NONE of the three prefixes below. Even
                    // once this asset was bundled, the auto-prompt for
                    // the exact screens this feature exists for never
                    // fired — an operator had to notice and tap the
                    // small badge manually. Added the floor-shell
                    // prefix so the primary surface gets the same
                    // auto-prompt as /odoo, /web and /sb/.
                    const isFloor =
                        path.startsWith("/odoo") ||
                        path.startsWith("/web") ||
                        path.startsWith("/sb/") ||
                        path.startsWith("/southbrook/floor/");
                    if (isFloor && !cached) {
                        showModal(false);
                    }
                }
            }
        }).catch(() => {
            // Network down — leave the badge as-is. The scan log
            // will fall back to env.user.employee_id server-side.
        });

        // Expose hooks for other UI components.
        window.SbkOperator = {
            showModal: () => showModal(true),
            switchOperator,
            getCachedName,
        };

        // Click on badge → switch flow (defined later via badge listener
        // but we also wire the explicit method here for callers).
        const badge = document.getElementById(BADGE_ID);
        if (badge) {
            badge.addEventListener("click", switchOperator);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
