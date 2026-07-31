/** @odoo-module **/

import { Component, useState, onWillStart, onWillDestroy } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";

// One page is the server's own default limit (materials.catalog.provider
// .get_catalog's `limit=80`) — keeping the two in sync means a fresh load
// (no offset/limit sent) and a client-driven page both ask for the same
// page size.
const PAGE_SIZE = 80;

// A burst of keystrokes must produce one request, not one per keystroke
// (finding F2). 250-300ms sits comfortably above normal inter-keystroke
// timing without making the results bar feel unresponsive.
const SEARCH_DEBOUNCE_MS = 280;

export class MaterialsHardwareCatalog extends Component {
    static template = "sb_materials_hardware.Catalog";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            loading: true,
            ok: true,
            reason: "",
            categories: [],
            facets: [],
            columns: [],
            rows: [],
            total: 0,
            provenance: "",
            categoryId: null,
            selectedFacets: {},
            search: "",
            offset: 0,
            pageSize: PAGE_SIZE,
            selectedProductId: null,
            detail: this._emptyDetail(),
        });
        // Two guards that don't belong in reactive state: the debounce
        // timer handle, and a monotonically increasing request id so a
        // slow, superseded load() can never clobber a faster, later one
        // (finding F2 — "a rapid category change doesn't get overtaken by
        // a stale in-flight search response").
        this._searchTimer = null;
        this._requestId = 0;
        onWillStart(() => this.load());
        onWillDestroy(() => this._clearSearchTimer());
    }

    _clearSearchTimer() {
        if (this._searchTimer !== null) {
            browser.clearTimeout(this._searchTimer);
            this._searchTimer = null;
        }
    }

    _emptyDetail() {
        return {
            ok: false,
            reason: null,
            title: "—",
            subtitle: "",
            specs: [],
            engineering: [],
            badges: [],
        };
    }

    async load() {
        this.state.loading = true;
        const previousSelected = this.state.selectedProductId;
        // Stamp this call with a request id before the first await. Any
        // response that comes back once a later load() has already started
        // is stale and must be dropped on arrival (finding F2).
        const requestId = ++this._requestId;
        const payload = await this.orm.call(
            "materials.catalog.provider", "get_catalog", [], {
                scope: "tools",
                category_id: this.state.categoryId,
                facets: this.state.selectedFacets,
                search: this.state.search,
                offset: this.state.offset,
                limit: this.state.pageSize,
            });
        if (requestId !== this._requestId) {
            // A newer load() (category change, facet toggle, debounced
            // search) started after this one — its response, not ours,
            // gets to win.
            return;
        }
        Object.assign(this.state, payload, { loading: false });

        if (!this.state.ok) {
            this.state.selectedProductId = null;
            this.state.detail = this._emptyDetail();
            return;
        }

        const rows = this.state.rows || [];
        const stillPresent = previousSelected !== null &&
            rows.some((row) => row.product_id === previousSelected);
        if (stillPresent) {
            // Keep the current selection and its detail panel showing —
            // the panel stays put across facet/search changes that don't
            // drop the currently open row (finding U8).
            return;
        }
        if (rows.length) {
            await this.selectRow(rows[0].product_id);
        } else {
            this.state.selectedProductId = null;
            this.state.detail = this._emptyDetail();
        }
    }

    get topCategories() {
        return this.state.categories.filter((c) => !c.parent_id);
    }

    childrenOf(categoryId) {
        return this.state.categories.filter((c) => c.parent_id === categoryId);
    }

    // Catalog › Category › Subcategory — built from the category tree the
    // payload already carries, no extra server call (finding U12).
    get breadcrumb() {
        const categoryId = this.state.categoryId;
        if (!categoryId) {
            return null;
        }
        const current = this.state.categories.find((c) => c.id === categoryId);
        if (!current) {
            return null;
        }
        if (current.parent_id) {
            const parent = this.state.categories.find((c) => c.id === current.parent_id);
            return { top: parent ? parent.name : "—", sub: current.name };
        }
        return { top: current.name, sub: null };
    }

    // Echoes the active facet selections and search term in the results
    // bar, from state already on the client — no new data (finding U11).
    get filterEcho() {
        const bits = [];
        for (const facet of this.state.facets) {
            const chosen = this.state.selectedFacets[facet.key];
            if (!chosen) {
                continue;
            }
            if (facet.type === "range") {
                const min = chosen.min !== undefined ? chosen.min : "";
                const max = chosen.max !== undefined ? chosen.max : "";
                if (min !== "" || max !== "") {
                    bits.push(facet.label + " " + min + "–" + max);
                }
            } else if (chosen.length) {
                const labels = chosen.map((v) => {
                    const found = facet.values.find((val) => val.value === v);
                    return found ? found.label : v;
                });
                bits.push(facet.label + ": " + labels.join(", "));
            }
        }
        if (this.state.search) {
            bits.push('"' + this.state.search + '"');
        }
        return bits.length ? "· " + bits.join(" · ") : "";
    }

    isSelected(key, value) {
        const chosen = this.state.selectedFacets[key] || [];
        return chosen.includes(value);
    }

    // Chip class for one facet value: selection highlight plus a distinct
    // look for the tri-state safety facets' "not assessed" chip, so an
    // unassessed bucket never reads visually the same as a real yes/no
    // value (finding: tri-state facet rendering).
    chipClass(key, val) {
        const classes = ["o_matcat_chip"];
        if (this.isSelected(key, val.value)) {
            classes.push("o_on");
        }
        if (val.value === "not_assessed") {
            classes.push("o_chip_unset");
        }
        return classes.join(" ");
    }

    // Badges are server-driven {label, kind} pairs (kind: danger|ok|unset)
    // as of the tri-state safety wave. A plain string badge (a stale
    // client bundle talking to a server that hasn't restarted, or vice
    // versa) is tolerated rather than crashing: it renders with its own
    // text and the default "danger" kind, matching the pre-existing
    // single-style badge look.
    badgeLabel(badge) {
        return typeof badge === "string" ? badge : badge.label;
    }

    badgeKind(badge) {
        if (!badge || typeof badge === "string") {
            return "danger";
        }
        return badge.kind || "danger";
    }

    badgeClass(badge) {
        const classes = ["o_matcat_badge"];
        const kind = this.badgeKind(badge);
        if (kind === "ok") {
            classes.push("o_badge_ok");
        } else if (kind === "unset") {
            classes.push("o_badge_unset");
        }
        return classes.join(" ");
    }

    isRowSelected(productId) {
        return this.state.selectedProductId === productId;
    }

    rangeValue(key, bound) {
        const chosen = this.state.selectedFacets[key];
        return chosen && chosen[bound] !== undefined ? chosen[bound] : "";
    }

    // Column class helper shared by header and body cells: alignment plus
    // a monospace treatment for reference-style code/numeric columns
    // (finding U6).
    cellClass(col) {
        const classes = [];
        if (col.align === "right") {
            classes.push("o_right");
        }
        if (col.key === "default_code") {
            classes.push("o_mono");
        }
        return classes.join(" ");
    }

    // ---- pagination ---------------------------------------------------
    // Category, facet, range and search changes all narrow or widen the
    // result set, so any of them landing on an offset from the *previous*
    // query would silently strand the user on (say) page 3 of a new
    // 2-page result set (finding F1). Every one of those mutators resets
    // to the first page before reloading.
    _resetPage() {
        this.state.offset = 0;
    }

    get pageStart() {
        return this.state.total ? this.state.offset + 1 : 0;
    }

    get pageEnd() {
        return Math.min(this.state.offset + this.state.pageSize, this.state.total);
    }

    get hasMultiplePages() {
        return this.state.total > this.state.pageSize;
    }

    get hasPrevPage() {
        return this.state.offset > 0;
    }

    get hasNextPage() {
        return this.state.offset + this.state.pageSize < this.state.total;
    }

    async prevPage() {
        if (!this.hasPrevPage) {
            return;
        }
        this.state.offset = Math.max(0, this.state.offset - this.state.pageSize);
        await this.load();
    }

    async nextPage() {
        if (!this.hasNextPage) {
            return;
        }
        this.state.offset += this.state.pageSize;
        await this.load();
    }

    async selectCategory(categoryId) {
        this.state.categoryId = categoryId;
        this.state.selectedFacets = {};
        this._resetPage();
        await this.load();
    }

    async toggleFacet(key, value) {
        const chosen = this.state.selectedFacets[key] || [];
        const next = chosen.includes(value)
            ? chosen.filter((v) => v !== value)
            : chosen.concat([value]);
        if (next.length) {
            this.state.selectedFacets[key] = next;
        } else {
            delete this.state.selectedFacets[key];
        }
        this._resetPage();
        await this.load();
    }

    async setRangeBound(key, bound, ev) {
        const raw = ev.target.value;
        const current = Object.assign({}, this.state.selectedFacets[key]);
        if (raw === "") {
            delete current[bound];
        } else {
            current[bound] = Number(raw);
        }
        if (current.min === undefined && current.max === undefined) {
            delete this.state.selectedFacets[key];
        } else {
            this.state.selectedFacets[key] = current;
        }
        this._resetPage();
        await this.load();
    }

    async clearFilters() {
        this._clearSearchTimer();
        this.state.selectedFacets = {};
        this.state.search = "";
        this._resetPage();
        await this.load();
    }

    // Reflects every keystroke into state.search immediately (so the input
    // stays controlled and responsive), but only fires load() after the
    // debounce window has passed with no further input (finding F2) — a
    // burst of typing produces exactly one request.
    onSearchInput(ev) {
        this.state.search = ev.target.value;
        this._resetPage();
        this._clearSearchTimer();
        this._searchTimer = browser.setTimeout(() => {
            this._searchTimer = null;
            this.load();
        }, SEARCH_DEBOUNCE_MS);
    }

    async selectRow(productId) {
        this.state.selectedProductId = productId;
        const detail = await this.orm.call(
            "materials.catalog.provider", "get_detail", [productId]);
        detail.title = detail.title || "—";
        this.state.detail = detail;
    }

    cell(row, key) {
        const value = row[key];
        return value === null || value === undefined
            ? "—"
            : value;
    }
}

registry.category("actions").add(
    "sb_materials_hardware.catalog", MaterialsHardwareCatalog);
