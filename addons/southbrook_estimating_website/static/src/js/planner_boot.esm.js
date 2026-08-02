/** @odoo-module **/
//
// Phase 2 commit 4 (2026-05-31) — session create + attribute drawer.
//
// P2C3 ended at tile-click-shows-details. P2C4 adds a Configure CTA
// in the selection panel that creates a product.config.session and
// opens the attribute drawer, replacing the selection panel with a
// scrollable list of attributes + their available values. Value-pick
// + live pricing land in P2C5; this commit ships discovery only.
//
// Phase 2 commit 3 (2026-05-31) — /kitchen-planner OWL three-pane shell.
//
// P2C2 mounted a tiny placeholder inside the viewport pane. P2C3
// expands the OWL component to own the entire three-pane layout
// (rail + catalog + viewport), renders real catalog tiles from the
// /southbrook/api/kitchen-planner/state payload, and wires tile-click
// to a selection state.
//
// What this commit ships:
//   - <KitchenPlanner/> renders the full three-pane layout from state.
//   - Catalog pane: 12 real tiles from state.payload.catalog
//     (sorted by family then SKU for predictable order), each with
//     SKU + name + family + retail price + 80x80 placeholder thumb.
//   - Tile click → state.ui.selected_id (highlighted via
//     .o_kp_tile_selected class). Viewport pane shows the selected
//     cabinet's details (name, family, price). Click same tile to
//     deselect; click another to switch.
//   - Search input filters tiles by name/SKU/family substring.
//   - Loading / error states styled consistently with the rest.
//
// What this commit does NOT yet ship:
//   - Tile click → create product.config.session (P2C4 — wires the
//     OCA configurator session flow).
//   - Attribute drawer below the viewport for picking series / door /
//     hinge / etc. — P2C4.
//   - Live pricing as attributes change — P2C5.
//   - 2D-isometric SVG cabinet renders in the viewport — P2C4 or P2C5.
//   - 'Request a Price' CTA → sale.order.draft — P2C6.

import {
    Component,
    onMounted,
    useState,
    xml,
} from "@odoo/owl";
import { registry } from "@web/core/registry";

// ---------------------------------------------------------------------
// rpcCall — pure fetch + JSON-RPC envelope.
// ---------------------------------------------------------------------
async function rpcCall(url, params = {}) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params,
            id: Date.now(),
        }),
        credentials: "same-origin",
    });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
    }
    const body = await response.json();
    if (body.error) {
        const msg = body.error.data?.message || body.error.message
            || "RPC error";
        throw new Error(msg);
    }
    return body.result;
}

// ---------------------------------------------------------------------
// Currency formatter — derived from payload.currency.
// ---------------------------------------------------------------------
function fmtCurrency(value, currency) {
    if (value === null || value === undefined) return "—";
    const sym = (currency && currency.symbol) || "$";
    const dp = (currency && currency.decimal_places) ?? 2;
    const formatted = Number(value).toLocaleString("en-US", {
        minimumFractionDigits: dp,
        maximumFractionDigits: dp,
    });
    return (currency && currency.position === "after")
        ? `${formatted} ${sym}`
        : `${sym}${formatted}`;
}

// ---------------------------------------------------------------------
// Stable family display order — base, drawer, wall, tall, sink, corner,
// vanity, accessory, worktop. Matches Q21 zone order for first 4.
// ---------------------------------------------------------------------
const FAMILY_ORDER = [
    "base", "drawer", "wall", "tall",
    "sink", "corner", "vanity", "accessory", "worktop",
];

function sortCatalog(catalog) {
    return [...catalog].sort((a, b) => {
        const fa = FAMILY_ORDER.indexOf(a.family);
        const fb = FAMILY_ORDER.indexOf(b.family);
        if (fa !== fb) {
            return (fa === -1 ? 99 : fa) - (fb === -1 ? 99 : fb);
        }
        return (a.sku || "").localeCompare(b.sku || "");
    });
}

// ---------------------------------------------------------------------
// KitchenPlanner — root OWL component (P2C3: owns the 3-pane layout).
// ---------------------------------------------------------------------
class KitchenPlanner extends Component {
    static template = xml`
        <div class="o_kp_root">

            <!-- LEFT TOOL RAIL -->
            <aside class="o_kp_rail">
                <div class="o_kp_rail_logo">SB</div>
                <ul class="o_kp_rail_actions">
                    <li class="o_kp_rail_action" title="Select / Pan">
                        <span class="o_kp_rail_glyph">▤</span>
                    </li>
                    <li class="o_kp_rail_action" title="Add cabinet">
                        <span class="o_kp_rail_glyph">＋</span>
                    </li>
                    <li class="o_kp_rail_action" title="Dimensions">
                        <span class="o_kp_rail_glyph">↕</span>
                    </li>
                    <li class="o_kp_rail_action" title="Solid / Blueline">
                        <span class="o_kp_rail_glyph">▦</span>
                    </li>
                </ul>
            </aside>

            <!-- CENTRE CATALOG PANE -->
            <section class="o_kp_catalog">
                <header class="o_kp_catalog_header">
                    <h2 class="o_kp_catalog_title">Cabinets</h2>
                    <input class="o_kp_catalog_search"
                           type="search"
                           placeholder="Search cabinets…"
                           t-att-value="state.ui.search"
                           t-on-input="(ev) => state.ui.search = ev.target.value.toLowerCase()"/>
                </header>

                <div t-if="state.loading"
                     class="o_kp_state o_kp_loading">
                    Loading planner…
                </div>
                <div t-elif="state.error"
                     class="o_kp_state o_kp_error">
                    <strong>Could not load planner.</strong>
                    <div class="o_kp_error_msg" t-esc="state.error"/>
                    <button class="o_kp_retry" t-on-click="_retry">Retry</button>
                </div>
                <ul t-else=""
                    class="o_kp_catalog_tiles">
                    <li t-foreach="filteredCatalog" t-as="cab" t-key="cab.id"
                        t-att-class="{
                            'o_kp_tile': true,
                            'o_kp_tile_selected': cab.id === state.ui.selected_id,
                        }"
                        t-on-click="() => this._onTileClick(cab.id)">
                        <div class="o_kp_tile_thumb"
                             t-att-data-family="cab.family"/>
                        <div class="o_kp_tile_body">
                            <div class="o_kp_tile_name" t-esc="cab.name"/>
                            <div class="o_kp_tile_meta">
                                <t t-esc="cab.sku"/> ·
                                <t t-esc="_fmt(cab.list_price)"/>
                            </div>
                        </div>
                    </li>
                    <li t-if="filteredCatalog.length === 0"
                        class="o_kp_tile_none">
                        No cabinets match "<t t-esc="state.ui.search"/>".
                    </li>
                </ul>

                <footer class="o_kp_catalog_foot"
                        t-if="!state.loading &amp;&amp; !state.error">
                    <t t-esc="filteredCatalog.length"/> of
                    <t t-esc="state.payload.catalog.length"/> cabinets ·
                    Phase 2 commit 3
                </footer>
            </section>

            <!-- RIGHT VIEWPORT -->
            <main class="o_kp_viewport">
                <div t-if="state.loading || state.error"
                     class="o_kp_placeholder">
                    <h1 class="o_kp_placeholder_title">Design Your Kitchen</h1>
                </div>

                <!-- Drawer (session-active view) — P2C4 -->
                <div t-elif="state.session_loading"
                     class="o_kp_drawer">
                    <div class="o_kp_drawer_loading">Configuring…</div>
                </div>
                <div t-elif="state.session_error"
                     class="o_kp_drawer">
                    <div class="o_kp_state o_kp_error">
                        <strong>Could not configure cabinet.</strong>
                        <div class="o_kp_error_msg" t-esc="state.session_error"/>
                        <button class="o_kp_retry" t-on-click="_cancelSession">
                            Back to catalog
                        </button>
                    </div>
                </div>
                <div t-elif="state.session"
                     class="o_kp_drawer">
                    <header class="o_kp_drawer_head">
                        <button class="o_kp_drawer_back"
                                t-on-click="_cancelSession">
                            ← Back
                        </button>
                        <div class="o_kp_drawer_head_body">
                            <div class="o_kp_drawer_thumb"
                                 t-att-data-family="state.session.template.family"/>
                            <!-- 2026-08-02 UX Phase 2 vocab gate — this
                                 page has no dealer/rep mode (it's the
                                 standalone customer configurator, per
                                 CLAUDE.md §2.1); an unlabeled SKU code
                                 served no purpose to a homeowner
                                 (ux-customer.md audit §4/§7 #6). Dropped
                                 rather than relabeled — the cabinet name
                                 above already identifies it. -->
                            <div class="o_kp_drawer_head_meta">
                                <div class="o_kp_drawer_head_name"
                                     t-esc="state.session.template.name"/>
                            </div>
                            <!-- P2C5 — live total. Updated after every
                                 set-value RPC. -->
                            <div class="o_kp_drawer_total">
                                <div class="o_kp_drawer_total_retail">
                                    <span class="o_kp_drawer_total_label">Retail</span>
                                    <span class="o_kp_drawer_total_value mono"
                                          t-esc="_fmt(currentPrice)"/>
                                </div>
                                <div class="o_kp_drawer_total_channel"
                                     t-if="state.session.discount_pct">
                                    <span class="o_kp_drawer_total_label">
                                        Your price
                                        (<t t-esc="state.session.discount_pct"/>%)
                                    </span>
                                    <span class="o_kp_drawer_total_value mono"
                                          t-esc="_fmt(state.session.channel_total)"/>
                                </div>
                            </div>
                        </div>
                    </header>

                    <!-- P2C5 — rule-block banner if last write was
                         rejected by the OCA config rule engine. -->
                    <div t-if="state.session.rule_message"
                         class="o_kp_drawer_rule_alert">
                        <strong>Cannot pick that combination:</strong>
                        <span t-esc="state.session.rule_message"/>
                    </div>

                    <ul class="o_kp_drawer_attrs">
                        <li t-foreach="state.session.attributes" t-as="attr"
                            t-key="attr.attribute_id"
                            class="o_kp_drawer_attr">
                            <header class="o_kp_drawer_attr_head">
                                <h3 class="o_kp_drawer_attr_name"
                                    t-esc="attr.name"/>
                                <span class="o_kp_drawer_attr_count">
                                    <t t-esc="_attrSelectedSummary(attr)"/>
                                </span>
                            </header>
                            <ul class="o_kp_drawer_attr_values">
                                <li t-foreach="attr.values" t-as="v"
                                    t-key="v.value_id"
                                    t-att-class="{
                                        'o_kp_drawer_attr_value': true,
                                        'o_kp_drawer_attr_value_selected':
                                            _isSelected(v.value_id),
                                    }"
                                    t-att-title="v.name"
                                    t-on-click="() => this._onValueClick(attr, v)">
                                    <span class="o_kp_drawer_attr_value_dot"
                                          t-if="v.html_color"
                                          t-att-style="'background:' + v.html_color"/>
                                    <span class="o_kp_drawer_attr_value_name"
                                          t-esc="v.name"/>
                                    <span class="o_kp_drawer_attr_value_extra"
                                          t-if="v.price_extra">
                                        + <t t-esc="_fmt(v.price_extra)"/>
                                    </span>
                                </li>
                                <li t-if="attr.values.length === 0"
                                    class="o_kp_drawer_attr_value_none">
                                    No options exposed on this attribute.
                                </li>
                            </ul>
                        </li>
                        <li t-if="state.session.attributes.length === 0"
                            class="o_kp_drawer_attrs_none">
                            This cabinet has no configurable attributes.
                        </li>
                    </ul>

                    <footer class="o_kp_drawer_foot">
                        <span t-if="state.session.value_busy"
                              class="o_kp_drawer_saving">Saving…</span>
                        <button class="o_kp_btn o_kp_btn_secondary"
                                t-on-click="_cancelSession">
                            Cancel
                        </button>
                        <button class="o_kp_btn o_kp_btn_primary"
                                disabled="disabled"
                                title="Add to Kitchen lands in P2C6">
                            Add to Kitchen
                        </button>
                    </footer>
                </div>

                <div t-elif="selectedCabinet"
                     class="o_kp_selection">
                    <div class="o_kp_selection_thumb"
                         t-att-data-family="selectedCabinet.family"/>
                    <h1 class="o_kp_selection_name"
                        t-esc="selectedCabinet.name"/>
                    <!-- 2026-08-02 UX Phase 2 vocab gate — dropped SKU
                         (customer-only page, no rep audience here). -->
                    <dl class="o_kp_selection_meta">
                        <dt>Family</dt><dd t-esc="selectedCabinet.family"/>
                        <dt>Retail</dt>
                        <dd t-esc="_fmt(selectedCabinet.list_price)"/>
                    </dl>
                    <button class="o_kp_btn o_kp_btn_primary o_kp_btn_configure"
                            t-on-click="_configureSelected">
                        Configure ▸
                    </button>
                    <p class="o_kp_selection_foot">
                        Live pricing + add-to-kitchen lands in Phase 2
                        commits 5–6.
                    </p>
                </div>
                <div t-else="" class="o_kp_placeholder">
                    <h1 class="o_kp_placeholder_title">Design Your Kitchen</h1>
                    <p class="o_kp_placeholder_body">
                        Hello,
                        <t t-esc="state.payload.user.partner_name"/>. Pick
                        a cabinet from the catalog on the left to start.
                    </p>
                    <p class="o_kp_placeholder_meta">
                        <t t-esc="state.payload.catalog.length"/> cabinets ·
                        <t t-esc="state.payload.user.channel"/> ·
                        <t t-esc="state.payload.currency.name"/>
                    </p>
                </div>
            </main>
        </div>
    `;

    static props = {};

    setup() {
        this.state = useState({
            loading: true,
            error: null,
            payload: null,
            ui: {
                search: "",
                selected_id: null,
            },
            // P2C4 — session state.
            // null when no session is active; populated by
            // /southbrook/api/kitchen-planner/session/create with
            // { session_id, template: {...}, attributes: [...] }
            session: null,
            session_loading: false,
            session_error: null,
        });
        onMounted(() => this._load());
    }

    async _load() {
        try {
            this.state.loading = true;
            this.state.error = null;
            const result = await rpcCall(
                "/southbrook/api/kitchen-planner/state",
                {},
            );
            this.state.payload = result;
            this.state.loading = false;
        } catch (err) {
            this.state.loading = false;
            this.state.error = err.message || String(err);
        }
    }

    _retry = () => { this._load(); };

    _onTileClick = (id) => {
        if (this.state.ui.selected_id === id) {
            this.state.ui.selected_id = null;
        } else {
            this.state.ui.selected_id = id;
        }
    };

    // P2C4 — invoked from the "Configure ▸" CTA in the selection
    // panel. Creates a product.config.session on the server and
    // opens the attribute drawer with the discovered attributes.
    _configureSelected = async () => {
        const cab = this.selectedCabinet;
        if (!cab) return;
        this.state.session_loading = true;
        this.state.session_error = null;
        try {
            const result = await rpcCall(
                "/southbrook/api/kitchen-planner/session/create",
                { template_id: cab.id },
            );
            if (result.error) {
                this.state.session_error = result.error;
                this.state.session_loading = false;
                return;
            }
            // P2C5 — initial session state. Pricing fields default
            // to the template's list_price + retail discount via
            // the user's channel.
            this.state.session = {
                ...result,
                selected_values: [],
                value_busy: false,
                rule_message: null,
                price: result.template.list_price,
                channel_total: result.template.list_price,
                discount_pct: 0,
            };
            this.state.session_loading = false;
        } catch (err) {
            this.state.session_error = err.message || String(err);
            this.state.session_loading = false;
        }
    };

    // P2C5 — invoked when the user clicks a value chip in the
    // drawer. action is 'set' for radio/select/color, toggles
    // add/remove for multi.
    _onValueClick = async (attr, value) => {
        if (!this.state.session || this.state.session.value_busy) return;
        const isSelected = this._isSelected(value.value_id);
        let action = "set";
        if (attr.display_type === "multi") {
            action = isSelected ? "remove" : "add";
        }
        this.state.session.value_busy = true;
        this.state.session.rule_message = null;
        try {
            const result = await rpcCall(
                `/southbrook/api/kitchen-planner/session/${this.state.session.session_id}/set-value`,
                {
                    attribute_id: attr.attribute_id,
                    value_id: value.value_id,
                    action,
                },
            );
            if (result.error === "rule_blocked") {
                this.state.session.rule_message =
                    result.message || "Rule violation";
            } else if (result.ok) {
                this.state.session.selected_values =
                    result.selected_values || [];
                this.state.session.price = result.price;
                this.state.session.channel_total = result.channel_total;
                this.state.session.discount_pct = result.discount_pct;
            }
        } catch (err) {
            this.state.session.rule_message = err.message || String(err);
        }
        this.state.session.value_busy = false;
    };

    _isSelected(value_id) {
        return (this.state.session?.selected_values || []).includes(value_id);
    }

    _attrSelectedSummary(attr) {
        const sel = (this.state.session?.selected_values || []);
        const picked = attr.values.filter((v) => sel.includes(v.value_id));
        if (picked.length === 0) {
            return `${attr.values.length} options`;
        }
        if (attr.display_type === "multi") {
            return `${picked.length} of ${attr.values.length} picked`;
        }
        return picked.map((v) => v.name).join(", ");
    }

    get currentPrice() {
        return this.state.session?.price
            ?? this.state.session?.template?.list_price
            ?? 0;
    }

    _cancelSession = async () => {
        const sid = this.state.session?.session_id;
        // Optimistic teardown — close the drawer immediately;
        // fire-and-forget the cancel RPC so the user never waits.
        this.state.session = null;
        this.state.session_loading = false;
        this.state.session_error = null;
        if (sid) {
            rpcCall(
                `/southbrook/api/kitchen-planner/session/${sid}/cancel`,
                {},
            ).catch(() => {
                // Server-side cancel failure is non-blocking; the
                // session row sits in 'draft' state and the Phase-3
                // housekeeping job sweeps it.
            });
        }
    };

    _fmt(value) {
        return fmtCurrency(value, this.state.payload?.currency);
    }

    get filteredCatalog() {
        if (!this.state.payload) return [];
        const sorted = sortCatalog(this.state.payload.catalog);
        const q = (this.state.ui.search || "").trim();
        if (!q) return sorted;
        return sorted.filter((c) =>
            (c.name || "").toLowerCase().includes(q)
            || (c.sku || "").toLowerCase().includes(q)
            || (c.family || "").toLowerCase().includes(q)
        );
    }

    get selectedCabinet() {
        if (!this.state.payload || this.state.ui.selected_id === null) {
            return null;
        }
        return this.state.payload.catalog.find(
            (c) => c.id === this.state.ui.selected_id
        ) || null;
    }
}

// ---------------------------------------------------------------------
// Bootstrap — register KitchenPlanner as a public component.
//
// 2026-07-03 — replaces the manual `whenReady(mountKitchenPlanner)` +
// `mountComponent(KitchenPlanner, root, …)` boot (added 2026-07-01).
// `/kitchen-planner` is an anonymous PUBLIC page, so — exactly like the
// portal OrderBuilder — mountComponent's implicit `startServices()` ran
// a second service suite against the already-started public root and
// threw `DuplicatedKeyError: NotificationContainer`. Mounting through
// the `public_components` registry reuses the public-root env (services
// + template registry shared, no second startServices), so it is safe
// today (KitchenPlanner uses inline xml`` + no services) AND for the
// Phase-3 3D children that will need services. See
// [[odoo19_mountcomponent_startservices_trap]].
//
// views/kitchen_planner_template.xml renders
// <owl-component name="southbrook_estimating_website.KitchenPlanner"/>
// inside the .o_kp_owl_mount wrapper. KitchenPlanner takes no props
// (static props = {}); it fetches partner + catalog via
// /southbrook/api/kitchen-planner/state, so no props attribute needed.
// ---------------------------------------------------------------------

registry
    .category("public_components")
    .add("southbrook_estimating_website.KitchenPlanner", KitchenPlanner);
