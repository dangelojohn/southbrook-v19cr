/** @odoo-module */
// SPDX-License-Identifier: LGPL-3.0-only
// Kitchen Jobs board — header intelligence band (KPI chips, DO NEXT,
// promoted filter chips + Legend + lens links) on top of the stock Kanban
// view. Governing rule (verbatim, CONTRACT_kitchen_jobs_board.md §0):
// "Absence of data never renders as good."
//
// Build contract: CONTRACT_kitchen_jobs_board.md §6. Field/RPC facts:
// recon_field_inventory.md. Visual language ported from
// mrp_process_explorer/static/src/explorer/mrp_explorer.scss (tokens
// renamed --skj-*, see kitchen_jobs_board.scss).
//
// v19 traps honoured here (see repo CLAUDE.md "Odoo traps" index):
// - NO `rpc` / `user` services (removed) — RPC goes through useService("orm").
// - Manual doAction({type:'ir.actions.act_window', ...}) MUST carry a
//   `views` key or the action silently fails to render a list/form.
// - OWL template expressions are kept to single-property reads / simple
//   comparisons; every display string is pre-composed in a JS getter
//   (owl_tokenizer_constraints.md).

import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { useService, useBus } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { Component, onWillStart, useState } from "@odoo/owl";

// ---------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------

// Lens links (CONTRACT §6): hardcoded action ids — cheap, fixed, well-known
// targets; an ir.model.data lookup RPC was judged not worth it for two
// static links per the contract's own guidance ("else hardcode ids
// 1536/1538 with comment").
const LENS_STATIONS_ACTION_ID = 1536; // Stations · Process Explorer
const LENS_MATERIAL_ACTION_ID = 1538; // Material Catalog

const DO_NEXT_COLLAPSE_KEY = "skj_do_next_collapsed";

// Promoted filter chip row (CONTRACT §4 + §6). `name` MUST match the
// <filter name="..."> in the search view arch exactly (SearchModel looks
// filters up by that name — search_arch_parser.js preserves it). `kpiKey`
// is the matching key under skj_board_intel().kpis used for the chip's
// count badge; `null` means the payload has no matching field for this
// filter today — the chip renders an honest "unwired" dash instead of a
// fabricated number (see KitchenJobsBoardHeader.filterChips below).
const FILTER_CHIPS = [
    { name: "blocked", label: _t("Blocked"), kpiKey: "blocked", cls: "skj-blocked" },
    { name: "review", label: _t("Needs Review"), kpiKey: "review", cls: "skj-review" },
    { name: "ready", label: _t("Ready"), kpiKey: "ready", cls: "skj-ok" },
    { name: "at_risk", label: _t("At Risk"), kpiKey: "at_risk", cls: "skj-atrisk" },
    { name: "missing_install_date", label: _t("Missing Install"), kpiKey: "missing_install", cls: "skj-unknown" },
    { name: "unassigned", label: _t("Unassigned"), kpiKey: "unassigned", cls: "skj-warn" },
    { name: "unscheduled", label: _t("Unscheduled WOs"), kpiKey: "unscheduled_wos", cls: "skj-warn" },
    { name: "late", label: _t("Past Deadline"), kpiKey: null, cls: "skj-late" },
];

// Legend colour-semantics table (CONTRACT §6 "Legend button ... colour
// semantics table"). Static copy; deliberately NOT payload-driven — the
// semantics themselves are a UI contract, not board data.
const LEGEND_ROWS = [
    { cls: "skj-chip skj-blocked", label: _t("Blocked"), copy: _t("A hard gate is failing right now (equipment down, missing spec, etc.).") },
    { cls: "skj-chip skj-atrisk", label: _t("At Risk"), copy: _t("Not yet blocked, but trending toward a miss (capacity, crew, material).") },
    { cls: "skj-chip skj-late", label: _t("Late"), copy: _t("Past its deadline. Distinct from At Risk on purpose — do not blur the two reds.") },
    { cls: "skj-chip skj-ok", label: _t("OK"), copy: _t("Gate passed / job ready.") },
    { cls: "skj-chip skj-unknown", label: _t("Unknown"), copy: _t("The value has never been computed. Never rendered as zero.") },
    { cls: "skj-unwired", label: _t("Unwired"), copy: _t("Present in the data model but not yet wired to this board.") },
];

// ---------------------------------------------------------------------
// Datetime helpers (provenance strip)
// ---------------------------------------------------------------------

/**
 * skj_board_intel() documents computed_at/server_now as ISO strings, but
 * Odoo's own `fields.Datetime.to_string()` / plain `str(datetime)` produces
 * a naive "YYYY-MM-DD HH:MM:SS" (space separator, no offset, UTC-implied).
 * Parse both shapes defensively rather than trusting `new Date(str)`
 * (Safari does not reliably parse the space-separated form).
 * @param {string|null|undefined} value
 * @returns {Date|null}
 */
function parseServerDatetime(value) {
    if (!value) {
        return null;
    }
    let text = String(value).trim();
    if (text.includes(" ") && !text.includes("T")) {
        text = text.replace(" ", "T");
    }
    if (!/[Zz]|[+-]\d{2}:?\d{2}$/.test(text)) {
        text += "Z";
    }
    const parsed = new Date(text);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * Coarse "N unit(s) ago" bucketing. Deliberately hand-rolled (not
 * Intl.RelativeTimeFormat) so the exact copy matches CONTRACT §6:
 * "computed <relative> ago · 15-min cron".
 * @param {number} ms non-negative milliseconds
 * @returns {string}
 */
function formatRelativeAge(ms) {
    const minutes = Math.floor(ms / 60000);
    if (minutes < 1) {
        return _t("just now");
    }
    if (minutes < 60) {
        return minutes === 1 ? _t("1 min") : _t("%s min", minutes);
    }
    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
        return hours === 1 ? _t("1 hr") : _t("%s hr", hours);
    }
    const days = Math.floor(hours / 24);
    return days === 1 ? _t("1 day") : _t("%s day", days);
}

// ---------------------------------------------------------------------
// Legend dialog
// ---------------------------------------------------------------------

export class KitchenJobsLegendDialog extends Component {
    static template = "southbrook_premium_orchestration.KitchenJobsLegendDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        pareto: { type: Array, optional: true },
        onLensStations: Function,
        onLensMaterial: Function,
    };

    setup() {
        this.legendRows = LEGEND_ROWS;
    }

    get paretoRows() {
        // pareto is already worst-first, zeros included (CONTRACT §5) —
        // rendered as-is, no client-side re-sorting or zero-filtering
        // ("absence of data never renders as good" applies to zero counts
        // too: a real 0 must still show as "0", never be hidden).
        return this.props.pareto || [];
    }

    get hasPareto() {
        return this.paretoRows.length > 0;
    }

    onLensStationsClick() {
        this.props.onLensStations();
        this.props.close();
    }

    onLensMaterialClick() {
        this.props.onLensMaterial();
        this.props.close();
    }
}

// ---------------------------------------------------------------------
// Header band component
// ---------------------------------------------------------------------

export class KitchenJobsBoardHeader extends Component {
    static template = "southbrook_premium_orchestration.KitchenJobsBoardHeader";
    static props = {
        state: Object,
        onToggleFilter: Function,
        onClearFilters: Function,
        onRunDoNext: Function,
        onToggleCollapse: Function,
        onOpenLegend: Function,
        isFilterActive: Function,
        isFilterMissing: Function,
        isAnyFilterActive: Function,
    };

    // ---- status -------------------------------------------------------

    get status() {
        return this.props.state.status;
    }

    get intel() {
        return this.props.state.intel;
    }

    get isLoading() {
        return this.status === "loading";
    }

    get isError() {
        return this.status === "error";
    }

    get showProvenance() {
        return !this.isLoading && !this.isError;
    }

    // ---- KPI row --------------------------------------------------------

    /**
     * Every entry is a fully pre-composed {key, text, cls} triple — the
     * template does nothing but t-foreach + t-esc, per the OWL tokenizer
     * constraint noted at the top of this file.
     */
    get kpiChips() {
        const intel = this.intel;
        if (!intel) {
            return [];
        }
        const k = intel.kpis || {};
        const chips = [];
        chips.push({ key: "total", text: _t("%s jobs", k.total), cls: "skj-chip" });
        chips.push({ key: "blocked", text: _t("%s blocked", k.blocked), cls: "skj-chip skj-blocked" });
        chips.push({ key: "undecided", text: _t("%s undecided", k.undecided), cls: "skj-chip skj-unknown" });
        chips.push({ key: "ready", text: _t("%s ready", k.ready), cls: "skj-chip skj-ok" });
        chips.push({ key: "at_risk", text: _t("%s at risk", k.at_risk), cls: "skj-chip skj-atrisk" });
        // max_days_late: 0 is a real ("late by zero days") value, distinct
        // from None ("no deadline in the data at all") — never collapse
        // the two with a truthiness check (odoo19_numeric_falsy_value_trap).
        if (k.max_days_late === null || k.max_days_late === undefined) {
            const coverage = Array.isArray(k.deadline_coverage) ? k.deadline_coverage : [0, 0];
            chips.push({
                key: "deadline",
                text: _t("%s/%s no deadline in data", coverage[0], coverage[1]),
                cls: "skj-chip skj-unknown",
            });
        } else {
            chips.push({ key: "late", text: _t("max +%sd late", k.max_days_late), cls: "skj-chip skj-late" });
        }
        chips.push({ key: "missing_install", text: _t("%s/%s no install date", k.missing_install, k.total), cls: "skj-chip skj-unknown" });
        chips.push({ key: "unassigned", text: _t("%s unassigned", k.unassigned), cls: "skj-chip skj-warn" });
        chips.push({ key: "unscheduled_wos", text: _t("%s WOs unscheduled", k.unscheduled_wos), cls: "skj-chip skj-warn" });
        chips.push({ key: "unassigned_wos", text: _t("%s WOs unassigned", k.unassigned_wos), cls: "skj-chip skj-warn" });
        return chips;
    }

    // ---- provenance -----------------------------------------------------

    get provenanceCopy() {
        return (this.intel && this.intel.provenance && this.intel.provenance.copy) || "";
    }

    get provenanceLine() {
        const intel = this.intel;
        if (!intel || !intel.provenance) {
            return null;
        }
        const computedAt = parseServerDatetime(intel.provenance.computed_at);
        if (!computedAt) {
            return null; // "never computed" branch — rendered separately below
        }
        const serverNow = parseServerDatetime(intel.server_now) || new Date();
        const ageMs = Math.max(0, serverNow.getTime() - computedAt.getTime());
        const cadence = intel.provenance.cron_cadence || _t("15 min");
        return _t("computed %s ago · %s cron", formatRelativeAge(ageMs), cadence);
    }

    get provenanceNeverComputed() {
        return !!this.intel && !this.provenanceLine;
    }

    // ---- DO NEXT ----------------------------------------------------------

    get doNextCollapsed() {
        return this.props.state.doNextCollapsed;
    }

    get doNextItems() {
        const intel = this.intel;
        if (!intel || !Array.isArray(intel.do_next)) {
            return [];
        }
        // Server derivation caps this at 3 (CONTRACT §1); slice defensively
        // rather than trust the wire.
        return intel.do_next.slice(0, 3).map((item) => ({
            rank: item.rank,
            label: item.label,
            detail: item.detail,
            count: item.count,
            action: item.action || null,
        }));
    }

    get doNextEmpty() {
        return this.status !== "loading" && this.status !== "error" && this.doNextItems.length === 0;
    }

    // ---- filter chip row ----------------------------------------------------

    get filterChips() {
        const intel = this.intel;
        const kpis = intel && intel.kpis;
        return FILTER_CHIPS.map((def) => {
            const missing = this.props.isFilterMissing(def.name);
            const active = !missing && this.props.isFilterActive(def.name);
            let countText = "—";
            let countCls = "skj-fcount skj-unwired";
            if (kpis && def.kpiKey && kpis[def.kpiKey] !== undefined && kpis[def.kpiKey] !== null) {
                countText = String(kpis[def.kpiKey]);
                countCls = "skj-fcount";
            }
            let cls = "skj-fchip " + def.cls;
            if (active) {
                cls += " is-active";
            }
            if (missing) {
                cls += " is-missing";
            }
            return {
                name: def.name,
                label: def.label,
                cls,
                countText,
                countCls,
                active,
                missing,
                // aria-pressed must be the literal string "true"/"false" —
                // OWL's t-att-* drops the attribute entirely on a raw
                // `false` boolean, which would silently erase the "not
                // pressed" state for screen readers (confirmed against
                // stock convention: web/static/src/webclient/
                // switch_company_menu/switch_company_item.xml, which
                // ternary-stringifies for the exact same reason).
                ariaPressed: active ? "true" : "false",
                title: missing ? _t("filter missing") : undefined,
            };
        });
    }

    get allChipActive() {
        return !this.props.isAnyFilterActive();
    }

    get allChipAriaPressed() {
        return this.allChipActive ? "true" : "false";
    }

    // ---- handlers -----------------------------------------------------------

    onFilterChipClick(chip) {
        if (chip.missing) {
            return; // disabled — CONTRACT §6: "never throw"
        }
        this.props.onToggleFilter(chip.name);
    }

    onAllChipClick() {
        this.props.onClearFilters();
    }

    onDoNextCardClick(item) {
        this.props.onRunDoNext(item);
    }

    onCollapseClick() {
        this.props.onToggleCollapse();
    }

    onLegendClick() {
        this.props.onOpenLegend();
    }
}

// ---------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------

export class KitchenJobsKanbanController extends KanbanController {
    static template = "southbrook_premium_orchestration.KitchenJobsKanbanView";
    static components = { ...KanbanController.components, KitchenJobsBoardHeader };

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.dialogService = useService("dialog");

        this.skjState = useState({
            status: "loading", // "loading" | "ready" | "error"
            intel: null,
            doNextCollapsed: this._skjLoadCollapsed(),
        });
        this._skjRequestSeq = 0;

        onWillStart(() => this._skjLoadIntel());
        // Reload whenever the search domain/filters change. SearchModel
        // fires a bare "update" event (search_model.js) on every domain
        // recompute; skj_board_intel() re-ANDs the board base domain
        // server-side regardless (CONTRACT §1 SAFETY), so no client-side
        // domain massaging is needed here.
        useBus(this.env.searchModel, "update", () => this._skjLoadIntel());
    }

    // ---- intel loading ----------------------------------------------------

    async _skjLoadIntel() {
        const seq = ++this._skjRequestSeq;
        let result = null;
        let failed = false;
        try {
            const domain = this.env.searchModel.domain;
            result = await this.orm.call("project.task", "skj_board_intel", [domain]);
        } catch (error) {
            failed = true;
            console.error("[kitchen_jobs_board] skj_board_intel failed", error);
        }
        if (seq !== this._skjRequestSeq) {
            // A newer request has already started (or landed) — never let a
            // stale response overwrite fresher state.
            return;
        }
        if (failed || !result) {
            this.skjState.status = "error";
            this.skjState.intel = null;
            return;
        }
        this.skjState.intel = result;
        this.skjState.status = "ready";
    }

    // ---- localStorage (guarded) --------------------------------------------

    _skjLoadCollapsed() {
        try {
            return window.localStorage.getItem(DO_NEXT_COLLAPSE_KEY) === "1";
        } catch (error) {
            return false;
        }
    }

    _skjSaveCollapsed(value) {
        try {
            window.localStorage.setItem(DO_NEXT_COLLAPSE_KEY, value ? "1" : "0");
        } catch (error) {
            // private browsing / storage quota — never fatal.
        }
    }

    skjToggleDoNextCollapsed() {
        this.skjState.doNextCollapsed = !this.skjState.doNextCollapsed;
        this._skjSaveCollapsed(this.skjState.doNextCollapsed);
    }

    // ---- filter chip helpers (SearchModel lookup BY name — CONTRACT §4) ---

    _skjFindFilterItem(filterName) {
        const items = this.env.searchModel.getSearchItems(
            (item) => item.type === "filter" && item.name === filterName
        );
        return items.length ? items[0] : null;
    }

    skjIsFilterMissing(filterName) {
        return !this._skjFindFilterItem(filterName);
    }

    skjIsFilterActive(filterName) {
        const item = this._skjFindFilterItem(filterName);
        return !!item && !!item.isActive;
    }

    skjIsAnyFilterActive() {
        return FILTER_CHIPS.some((def) => this.skjIsFilterActive(def.name));
    }

    skjToggleFilter(filterName) {
        const item = this._skjFindFilterItem(filterName);
        if (!item) {
            return; // filter missing on this search view — never throw
        }
        this.env.searchModel.toggleSearchItem(item.id);
    }

    skjClearFilterChips() {
        for (const def of FILTER_CHIPS) {
            const item = this._skjFindFilterItem(def.name);
            if (item && item.isActive) {
                this.env.searchModel.toggleSearchItem(item.id);
            }
        }
    }

    // ---- DO NEXT action dispatch (CONTRACT §1/§6) --------------------------

    async skjRunDoNextAction(item) {
        const action = item && item.action;
        if (!action) {
            return;
        }
        if (action.type === "act_window") {
            if (!action.res_model) {
                return; // never throw on a malformed payload
            }
            await this.actionService.doAction({
                type: "ir.actions.act_window",
                res_model: action.res_model,
                domain: action.domain || [],
                // v19 trap: manual act_window doAction() silently no-ops
                // without an explicit `views` key.
                views: [[false, "list"], [false, "form"]],
                target: "current",
                name: item.label,
            });
        } else if (action.type === "filter" && action.filter_name) {
            this.skjToggleFilter(action.filter_name);
        }
    }

    // ---- Legend + lens links ------------------------------------------------

    skjOpenLensStations() {
        this.actionService.doAction(LENS_STATIONS_ACTION_ID);
    }

    skjOpenLensMaterial() {
        this.actionService.doAction(LENS_MATERIAL_ACTION_ID);
    }

    skjOpenLegend() {
        this.dialogService.add(KitchenJobsLegendDialog, {
            pareto: (this.skjState.intel && this.skjState.intel.pareto) || [],
            onLensStations: () => this.skjOpenLensStations(),
            onLensMaterial: () => this.skjOpenLensMaterial(),
        });
    }
}

// ---------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------

export const kitchenJobsKanbanView = {
    ...kanbanView,
    Controller: KitchenJobsKanbanController,
};

registry.category("views").add("kitchen_jobs_kanban", kitchenJobsKanbanView);
