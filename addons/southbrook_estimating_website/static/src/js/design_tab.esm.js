/** @odoo-module **/
/*
 * SPDX-License-Identifier: LGPL-3.0-only
 *
 * 2026-07-03 T1/T2 — Order Builder "3D Design" tab.
 * 2026-07-06 — parity pass against the standalone Kitchen 3D
 * Configurator's "Kitchen Designs" screen (E2E comparison on order
 * #1381). Adds: numbered camera-view shortcuts (1-6 + R) with the
 * SAME key mapping as kitchen_configurator.js's _onKeyDown, an
 * on-canvas discoverable hint bar, editable room Width/Depth/Height,
 * a portal-safe cabinet inventory panel with type filter + search +
 * drag-to-add, a read-only "selected cabinet" detail panel (SKU /
 * price / material, × to deselect), and a live cost/quantity summary.
 * "Save as my default" is intentionally NOT ported — that's a rep
 * convenience for reusing room dims across many quotes; a portal
 * customer has exactly one order and no "my defaults" concept.
 *
 * 2026-07-06 (follow-up) — touch support for the inventory drag-to-add
 * (native HTML5 DnD events never fire on iOS/Android touch), an
 * empty-state hint pointing first-time users at the inventory panel,
 * and a toast stack (reusing the exact `.o_owl_toast*` markup/SCSS
 * portal_root.scss already defines for OrderBuilder) for add/error
 * confirmation. Cabinet select/move/room-resize gestures inside
 * KitchenCanvas itself are still mouse-only — that's a shared-component
 * gap (kitchen_canvas.esm.js's own pointer pipeline), out of scope here.
 *
 * 2026-07-06 (enrichment pass) — remaining kitchen_configurator.js
 * surface ported to the portal-appropriate subset: zoom +/- (keys and
 * buttons, kitchen_canvas.esm.js's existing api.zoomBy), ←/→ arrow-key
 * cabinet cycling (SAME selectable-order algorithm as the backend's
 * _cycleSelection — base cabinets first, then wall, each left-to-right,
 * wraps), a Width input + product-swap select + Remove (×) on the
 * detail panel (new /design-3d/swap + /design-3d/remove routes, and
 * /design-3d/move now also accepts width_in), a BOM-readiness warning
 * (text only — the backend's "Open BoM →" deep-link targets a
 * backend-only view, not ported), a channel/pricing badge (hidden for
 * the default retail case, matching the backend's own suppression
 * rule), and a dimension ruler overlay along the room width. Still NOT
 * ported: "Save as my default" (no portal equivalent — one order, no
 * "my defaults" concept) and the customer picker (the order's partner
 * is fixed; the backend hides this for portal users too).
 *
 * A portal parent that drives the standalone Configurator's KitchenCanvas
 * OWL engine (reused verbatim from southbrook_kitchen_3d_configurator's
 * canvas/* modules, now also loaded into web.assets_frontend) inside the
 * Order Builder portal SPA.
 *
 * Data model: the order's southbrook.kitchen.design (get-or-created +
 * seeded from the order's SB-SKU lines by the portal route
 * /southbrook/api/order/<id>/design-3d). The editor reads and writes its
 * OWN design, so it is self-coherent (unlike the zone-tiled read-only
 * KitchenViewport Preview). Drag-moves persist to
 * kitchen.design.line.x_position_in via .../design-3d/move — in-bounds
 * (never touches sale.order/MO; positions ride the existing design→order
 * reconcile cron). Room-dimension edits persist via .../design-3d/room;
 * inventory adds persist via .../design-3d/add — same cron bridge, same
 * ownership + sudo() pattern, no new write path onto sale.order/MO.
 *
 * We deliberately do NOT reuse the backend parent kitchen_configurator.js
 * (it uses useService("action"), whose service is absent from
 * web.assets_frontend). This component is that parent's portal-safe twin.
 */
import {
    Component, onMounted, onWillUnmount, onWillUpdateProps, useRef, useState, xml,
} from "@odoo/owl";
import { KitchenCanvas } from "@southbrook_kitchen_3d_configurator/js/canvas/kitchen_canvas.esm";
// Touch editing v1 (2026-08-02 spec) — the shared control-pad component
// + its pure family/visibility, wall-cycle/elevation, and touch-
// capability helpers. See touch_pad.esm.js's own header for the full
// contract shared with the backend (kitchen_configurator.js); this
// file owns the portal-specific gating (phone dock vs tablet floating,
// and which mutations this addon's own /design-3d/* routes can
// actually persist — see _flipSelectedHand/_showTouchPad's comments
// for the two capability gaps the portal has that the backend doesn't).
import {
    TouchPad, getPadAffordances, elevationViewForWall,
    NUDGE_STEP_IN, isTouchCapableAtBoot, touchPadModeForViewport,
} from "@southbrook_kitchen_3d_configurator/js/canvas/touch_pad.esm";

// Plain JSON-RPC fetch — no @web service dependency, matching the portal
// context pattern used by kitchen_viewport.esm.js / portal_boot.esm.js.
async function rpcCall(url, params = {}) {
    const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: params,
            id: Math.floor(Math.random() * 1e9),
        }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const j = await res.json();
    if (j.error) {
        throw new Error(j.error.data?.message || j.error.message || "RPC error");
    }
    return j.result;
}

// Same key→view mapping as kitchen_configurator.js's _onKeyDown, so a
// rep who already knows the backend shortcuts feels no discontinuity
// switching to the portal tab.
const VIEW_KEY_MAP = { "1": "iso", "2": "top", "3": "front",
                        "4": "left", "5": "right", "6": "persp" };
const VIEW_BUTTONS = [
    { key: "iso",   num: "1", icon: "⬡", label: "Isometric" },
    { key: "top",   num: "2", icon: "▦", label: "Top / Plan" },
    { key: "front", num: "3", icon: "▮", label: "Front" },
    { key: "left",  num: "4", icon: "◧", label: "Left side" },
    { key: "right", num: "5", icon: "◨", label: "Right side" },
    { key: "persp", num: "6", icon: "◉", label: "Perspective / orbit" },
];
const CATEGORY_LABELS = { all: "All", base: "Base", wall: "Wall",
                           tall: "Tall", other: "Other" };
function categoryOf(cabinetType) {
    if (cabinetType === "base" || cabinetType === "wall" || cabinetType === "tall") {
        return cabinetType;
    }
    return "other";
}
const MATERIAL_LABELS = {
    white_melamine: "White Melamine", grey_melamine: "Grey Melamine",
    maple_veneer: "Maple Veneer", oak_veneer: "Oak Veneer",
    painted_mdf: "Painted MDF", thermoplastic: "Thermoplastic",
};
// Same fixed tick set as kitchen_configurator.js's o_sbk_ruler.
const RULER_MARKS = [0, 24, 48, 72, 96, 120, 144];

// ----------------------------------------------------------------------
// friendlyDesignError — 2026-08-02 UX Phase 2 vocab gate.
//
// Every `/design-3d/*` route in this addon's controllers/main.py returns
// a thin `{"error": "<code>"}` shape (not_found, forbidden, no_design,
// no_layout_key, no_matching_line, rate_limited, not_a_cabinet,
// type_mismatch, ROOM_TOO_SMALL — grep controllers/main.py for the
// authoritative list), and an uncaught RPC exception surfaces as a raw
// `Error` object. Both used to reach the DOM verbatim via
// `this.state.error = resp.error` / `e?.message || String(e)`
// (ux-customer.md audit §5/§7 #4 cites this pattern, with NO_CABINETS /
// ROOM_OUT_OF_BOUNDS / invalid_wall as the flavor of thing that can leak
// through a passthrough like this one). Every `state.error =` assignment
// in this file now routes through this map instead. Known codes get a
// plain-language "what happened + what to do" message; anything unknown
// (a real bug, a future code, a raw JS exception string) gets the same
// generic, non-alarming fallback rather than a stack trace or a bare
// server token.
// ----------------------------------------------------------------------
const DESIGN_ERROR_COPY = {
    not_found:        "This order couldn't be found. Go back to My Orders and try again.",
    forbidden:        "You don't have access to make that change.",
    no_design:        "Your kitchen design hasn't finished loading. Refresh the page and try again.",
    no_layout_key:    "That cabinet isn't in your design anymore. Refresh the page to see the latest layout.",
    no_matching_line: "That cabinet isn't in your design anymore. Refresh the page to see the latest layout.",
    rate_limited:     "You're making changes faster than we can save them. Wait a moment and try again.",
    not_a_cabinet:    "That item isn't available to add. Pick a cabinet from the list on the right.",
    type_mismatch:    "That swap isn't available for this cabinet. Pick a cabinet of the same type.",
    // Defensive — not currently reachable from this tab's own routes, but
    // the shared layout engine's own vocabulary (see kitchen_3d_configurator),
    // kept here in case a future wiring change forwards it unmapped.
    NO_CABINETS:       "Add a cabinet from the panel on the right to get started.",
    ROOM_OUT_OF_BOUNDS: "Your room size doesn't fit the cabinets already placed. Try a larger room or remove a cabinet.",
    invalid_wall:       "Pick a wall to place this cabinet on.",
};
const DESIGN_ERROR_FALLBACK =
    "Something didn't load — try again; your design is saved.";

function friendlyDesignError(raw) {
    if (!raw) return DESIGN_ERROR_FALLBACK;
    // ROOM_TOO_SMALL is handled by its own caller with the real
    // dimensions already baked into the sentence (see _onAutoArrange) —
    // if it ever reaches here unbuilt, still don't show the bare code.
    if (raw === "ROOM_TOO_SMALL") {
        return "Not enough wall space for these cabinets. Remove a cabinet or enlarge the room.";
    }
    return DESIGN_ERROR_COPY[raw] || DESIGN_ERROR_FALLBACK;
}

export class KitchenDesignTab extends Component {
    static components = { KitchenCanvas, TouchPad };
    static template = xml`
        <div class="o_owl_design3d">
            <div class="o_owl_design3d_toolbar">
                <span class="o_owl_design3d_title">3D Design</span>
                <span class="o_owl_design3d_status"
                      t-if="state.loading">Loading…</span>
                <span class="o_owl_design3d_status text-danger"
                      t-elif="state.error" t-esc="state.error"/>
                <span class="o_owl_design3d_meta" t-else="">
                    <t t-esc="state.items.length"/> cabinets
                    <t t-if="state.saving"> · saving…</t>
                    <span t-if="state.activeWall" class="o_owl_design3d_activewall">
                        · Active Wall: <t t-esc="_wallLabel(state.activeWall)"/>
                    </span>
                </span>
                <!-- Suppressed for the default retail/no-channel case,
                     matching kitchen_configurator.js's own topbar rule
                     ("so it doesn't clutter the topbar"). -->
                <span t-if="state.channel &amp;&amp; state.channel.channel !== 'retail'"
                      class="o_owl_design3d_channel_badge"
                      t-att-title="'Pricelist: ' + (state.channel.pricelist_name || '')">
                    <t t-esc="state.channel.channel_label"/>
                </span>
                <div class="o_owl_design3d_room_dims">
                    <label class="o_owl_design3d_dim">
                        <span>W</span>
                        <input type="number" min="72" max="600" step="6"
                               t-att-value="state.room.width_in"
                               t-on-change="(ev) => this._changeRoom('width_in', ev.target.value)"/>
                    </label>
                    <label class="o_owl_design3d_dim">
                        <span>D</span>
                        <input type="number" min="72" max="600" step="6"
                               t-att-value="state.room.depth_in"
                               t-on-change="(ev) => this._changeRoom('depth_in', ev.target.value)"/>
                    </label>
                    <label class="o_owl_design3d_dim">
                        <span>H</span>
                        <input type="number" min="84" max="144" step="6"
                               t-att-value="state.room.height_in"
                               t-on-change="(ev) => this._changeRoom('height_in', ev.target.value)"/>
                    </label>
                </div>
                <div class="o_owl_design3d_wallpick" role="group" aria-label="Active wall">
                    <span class="o_owl_design3d_wallpick_lbl">Wall</span>
                    <button t-foreach="['back', 'left', 'right', 'front']" t-as="w" t-key="w"
                            type="button"
                            class="btn btn-sm o_owl_design3d_wallbtn"
                            t-att-class="state.activeWall === w ? 'btn-primary' : 'btn-outline-secondary'"
                            t-att-title="'Place the next cabinets on the ' + _wallLabel(w) + ' wall'"
                            t-on-click="() => this._pickWall(w)"
                            t-esc="_wallLabel(w)"/>
                </div>
                <button type="button"
                        class="btn btn-sm btn-primary o_owl_design3d_autoarrange"
                        t-att-disabled="state.loading || state.saving || !state.items.length"
                        t-on-click="() => this._onAutoArrange()"
                        title="Wrap cabinets across walls into an L/U layout, replacing the corner with a corner cabinet">
                    Auto-arrange L/U
                </button>
            </div>
            <div class="o_owl_design3d_body">
                <div class="o_owl_design3d_stage_col">
                    <div class="o_owl_design3d_stage" t-ref="stage"
                         t-att-class="'o_owl_design3d_stage' + (state.dragHover ? ' is-drag-hover' : '') + (state.cornerFlash ? ' o_owl_design3d_corner_flash' : '')"
                         t-on-dragover="_onCanvasDragOver"
                         t-on-dragleave="_onCanvasDragLeave"
                         t-on-drop="_onCanvasDrop">
                        <KitchenCanvas items="state.items"
                                       room="state.room"
                                       view="state.view"
                                       selected="state.selected"
                                       draggedProduct="state.draggedProduct"
                                       dragHover="state.dragHover"
                                       onSelectItem.bind="_onSelectItem"
                                       onMoveItem.bind="_onMoveItem"
                                       onResizeRoom.bind="_onResizeRoom"
                                       onResizeRoomDepth.bind="_onResizeRoomDepth"
                                       onViewChange.bind="_onViewChange"
                                       onWallSelect.bind="_onWallSelect"
                                       activeWall="state.activeWall"
                                       touchEnabled="_showTouchPad()"
                                       onGestureRollback="() => this._recordTouchEvent('rollback')"
                                       onGestureAbandon="() => this._recordTouchEvent('abandon')"
                                       onReady.bind="_onCanvasReady"/>
                        <!-- Touch editing v1 (2026-08-02 spec §1 decision
                             4) — "Portal 3D tab: full touch on phones +
                             tablets." Bottom-dock on phones, floating on
                             tablets (touchPadMode tracks viewport width
                             live — see the resize listener in setup()).
                             Gated on BOTH the server kill-switch and a
                             runtime touch-capability probe. -->
                        <TouchPad t-if="_showTouchPad()"
                                  mode="state.touchPadMode"
                                  selection="state.selected"
                                  currentView="state.view"
                                  capabilities="_touchPadCapabilities()"
                                  onNudge="(dir) => this._nudgeSelected(dir)"
                                  onRotate="(deg) => this._rotateSelected(deg)"
                                  onFlipHand="() => this._flipSelectedHand()"
                                  onCameraToElevation="() => this._cameraToSelectedElevation()"
                                  onRemove="() => this._removeSelectedCabinet()"
                                  onUndo="() => this._undo()"/>
                        <div class="o_owl_design3d_viewbar" role="toolbar" aria-label="Camera view">
                            <button t-foreach="viewButtons" t-as="vb" t-key="vb.key"
                                    type="button"
                                    t-att-class="'o_owl_design3d_viewbtn' + (state.view === vb.key ? ' is-active' : '')"
                                    t-att-aria-pressed="state.view === vb.key"
                                    t-att-title="vb.label + ' (' + vb.num + ')'"
                                    t-on-click="() => this._setView(vb.key)">
                                <span class="o_owl_design3d_viewicon" t-esc="vb.icon"/>
                                <span class="o_owl_design3d_viewkey" t-esc="vb.num"/>
                            </button>
                            <div class="o_owl_design3d_viewsep"/>
                            <button type="button" class="o_owl_design3d_viewbtn"
                                    title="Reset to Isometric (R)"
                                    t-on-click="() => this._setView('iso')">
                                <span class="o_owl_design3d_viewicon">⌂</span>
                                <span class="o_owl_design3d_viewkey">R</span>
                            </button>
                            <div class="o_owl_design3d_viewsep"/>
                            <button type="button" class="o_owl_design3d_viewbtn"
                                    title="Zoom in (+)"
                                    t-on-click="() => this._zoomBy(0.9)">
                                <span class="o_owl_design3d_viewicon">+</span>
                            </button>
                            <button type="button" class="o_owl_design3d_viewbtn"
                                    title="Zoom out (-)"
                                    t-on-click="() => this._zoomBy(1.1)">
                                <span class="o_owl_design3d_viewicon">−</span>
                            </button>
                        </div>
                        <!-- Dimension ruler along the room width — visual
                             parity with kitchen_configurator.js's
                             o_sbk_ruler; ticks that exceed the current
                             room width simply don't render. -->
                        <div class="o_owl_design3d_ruler">
                            <t t-foreach="rulerMarks" t-as="mark" t-key="mark">
                                <span t-if="mark &lt;= state.room.width_in" class="o_owl_design3d_mark">
                                    <t t-esc="mark"/> in
                                </span>
                            </t>
                        </div>
                        <div class="o_owl_design3d_hint">
                            <t t-if="!state.loading &amp;&amp; !state.items.length">no cabinets yet — drag one in from the panel on the right to get started</t>
                            <t t-elif="state.view === 'iso' || state.view === 'top'">click a wall to select it · drag cabinet to move · drag ● to resize room · ←/→ cycle · 1-6 views</t>
                            <t t-elif="state.view === 'persp'">drag cabinet to move · drag empty space to orbit · scroll to zoom · ←/→ cycle · R reset</t>
                            <t t-else="">scroll to zoom · click select · ←/→ cycle · switch to Iso/Top/Persp to move cabinets</t>
                        </div>
                    </div>
                    <div class="o_owl_design3d_summary">
                        <span class="o_owl_design3d_summary_item">
                            <strong t-esc="summary.baseCount"/> base
                        </span>
                        <span class="o_owl_design3d_summary_item">
                            <strong t-esc="summary.wallCount"/> wall
                        </span>
                        <span t-if="summary.fillerCost" class="o_owl_design3d_summary_item">
                            + <strong t-esc="_money(summary.fillerCost)"/> filler
                        </span>
                        <span class="o_owl_design3d_summary_item o_owl_design3d_summary_total">
                            Total <strong t-esc="_money(summary.total)"/>
                        </span>
                    </div>
                </div>
                <aside class="o_owl_design3d_inventory">
                    <div class="o_owl_design3d_inv_head">
                        <h4>Cabinet Inventory</h4>
                        <span class="o_owl_design3d_inv_count"
                              t-esc="filteredProducts.length + ' / ' + state.products.length"/>
                    </div>
                    <label class="o_owl_design3d_inv_cat">
                        <span>Type</span>
                        <select t-on-change="(ev) => state.inventoryCategory = ev.target.value">
                            <option t-foreach="Object.keys(categoryLabels)" t-as="cat" t-key="cat"
                                    t-att-value="cat"
                                    t-att-selected="state.inventoryCategory === cat ? 'selected' : ''"
                                    t-esc="categoryLabels[cat]"/>
                        </select>
                    </label>
                    <div class="o_owl_design3d_inv_search">
                        <!-- 2026-08-02 UX Phase 2 vocab gate — SKU stays
                             in the search hint for reps (they search by
                             it); a customer doesn't know what a SKU is
                             (ux-customer.md audit §4/§7 #4/#6). The
                             search itself still matches against sku for
                             everyone (see filteredProducts) — only the
                             displayed hint text changes. -->
                        <input type="search"
                               t-att-placeholder="props.mode === 'customer' ? 'Search name, type…' : 'Search name, SKU, type…'"
                               t-att-value="state.inventorySearch"
                               t-on-input="(ev) => state.inventorySearch = ev.target.value"/>
                        <button t-if="state.inventorySearch" type="button"
                                aria-label="Clear search"
                                t-on-click="() => state.inventorySearch = ''">×</button>
                    </div>
                    <div t-if="filteredProducts.length" class="o_owl_design3d_inv_draghint">
                        ↔ drag a product into the scene to add it
                    </div>
                    <div class="o_owl_design3d_prod_list">
                        <button t-foreach="filteredProducts" t-as="product" t-key="product.product_id"
                                type="button"
                                draggable="true"
                                t-att-class="'o_owl_design3d_prod_row' + (state.touchDragProduct &amp;&amp; state.touchDragProduct.product_id === product.product_id ? ' is-touch-dragging' : '')"
                                t-on-dragstart="(ev) => this._onProductDragStart(ev, product)"
                                t-on-dragend="_onProductDragEnd"
                                t-on-touchstart="(ev) => this._onProductTouchStart(ev, product)"
                                t-att-title="'Drag onto scene to add ' + product.name">
                            <span class="o_owl_design3d_prod_name" t-esc="product.name"/>
                            <span class="o_owl_design3d_prod_meta"
                                  t-esc="(props.mode === 'customer' ? '' : product.sku + ' · ') + product.width_in + '″'"/>
                            <span class="o_owl_design3d_prod_price" t-esc="_money(product.price)"/>
                        </button>
                        <div t-if="!filteredProducts.length &amp;&amp; state.products.length"
                             class="o_owl_design3d_inv_empty">
                            No products match "<t t-esc="state.inventorySearch"/>".
                        </div>
                    </div>
                    <div t-if="state.selected" class="o_owl_design3d_detail">
                        <div class="o_owl_design3d_detail_head">
                            <h4>Selected Cabinet</h4>
                            <div class="o_owl_design3d_detail_actions">
                                <button type="button" class="o_owl_design3d_detail_remove"
                                        aria-label="Remove this cabinet" title="Remove cabinet"
                                        t-on-click="_removeSelectedCabinet">🗑</button>
                                <button type="button" aria-label="Deselect" title="Deselect"
                                        t-on-click="_deselect">×</button>
                            </div>
                        </div>
                        <p class="o_owl_design3d_detail_name" t-esc="state.selected.product_name"/>
                        <!-- 2026-08-02 UX Phase 2 vocab gate — "No BOM
                             defined... Bill of Materials" is ops/
                             manufacturing information a homeowner has no
                             use for and no way to act on; it stayed
                             unconditional before (ux-customer.md audit
                             §4/§7 #3/#4). Rep mode is unchanged. -->
                        <div t-if="state.selected.bom_available === false &amp;&amp; props.mode !== 'customer'"
                             class="o_owl_design3d_bom_warn">
                            ⚠ No BOM defined — this item can't be manufactured until
                            a Bill of Materials exists for it.
                        </div>
                        <div class="o_owl_design3d_edit_grid">
                            <label class="o_owl_design3d_edit_field">
                                <span>Product</span>
                                <select t-on-change="(ev) => this._swapSelectedProduct(ev.target.value)">
                                    <option t-foreach="sameTypeProducts" t-as="p" t-key="p.product_id"
                                            t-att-value="p.product_id"
                                            t-att-selected="p.product_id === state.selected.product_id ? 'selected' : ''"
                                            t-esc="p.name + ' — ' + p.width_in + ' in'"/>
                                </select>
                            </label>
                            <label class="o_owl_design3d_edit_field">
                                <span>Width (in)</span>
                                <input type="number" min="6" max="48" step="3"
                                       t-att-value="state.selected.width_in"
                                       t-on-change="(ev) => this._updateSelectedWidth(ev.target.value)"/>
                            </label>
                        </div>
                        <dl class="o_owl_design3d_detail_spec">
                            <!-- 2026-08-02 UX Phase 2 vocab gate — SKU
                                 stays for reps; hidden for customers
                                 (same mode gate as the rest of this
                                 tree). -->
                            <t t-if="props.mode !== 'customer'">
                                <dt>SKU</dt><dd t-esc="state.selected.sku || '—'"/>
                            </t>
                            <dt>Height</dt><dd t-esc="state.selected.height_in + ' in'"/>
                            <dt>Depth</dt><dd t-esc="state.selected.depth_in + ' in'"/>
                            <dt>Material</dt><dd t-esc="_materialLabel(state.selected.material)"/>
                            <dt>Price</dt><dd t-esc="_money(state.selected.price)"/>
                        </dl>
                    </div>
                </aside>
            </div>
            <!-- Reuses the exact toast markup/classes portal_root.scss
                 already styles for OrderBuilder's own toast stack —
                 no new CSS needed. -->
            <div class="o_owl_toast_stack" role="status" aria-live="polite"
                 t-if="state.toasts.length">
                <div t-foreach="state.toasts" t-as="toast" t-key="toast.id"
                     t-attf-class="o_owl_toast o_owl_toast_{{ toast.kind }}">
                    <span class="o_owl_toast_text" t-esc="toast.text"/>
                    <button type="button" class="o_owl_toast_close"
                            aria-label="Dismiss"
                            t-on-click="() => this._dismissToast(toast.id)">×</button>
                </div>
            </div>
        </div>
    `;
    static props = {
        orderId: { type: String, optional: true },
        // Bumped by OrderBuilder after autosaves; a change re-fetches so the
        // design reseeds if the order's cabinet set changed (T1: reload).
        payloadVersion: { type: Number, optional: true },
        // 2026-08-02 — UX Phase 2 vocab gate (BOM warning + SKU
        // visibility). Forwarded from OrderBuilder, same flag
        // FooterActions/OrderTitlebar already branch on.
        mode: { type: String, optional: true },
    };

    setup() {
        this.state = useState({
            loading: true,
            error: null,
            saving: false,
            items: [],
            // 2026-07-06 — matches the portal's own new-design server
            // default (_PORTAL_NEW_DESIGN_ROOM in controllers/main.py)
            // so even the brief pre-_load() loading frame agrees with
            // what a first-ever design actually resolves to.
            room: { width_in: 96, depth_in: 72, height_in: 96 },
            activeWall: null,
            selected: null,
            designId: null,
            // 2026-07-06 — default camera preset is #6 (persp — the
            // two-wall corner/isometric room view), not #1 (iso).
            // Camera view is never persisted server-side (there's no
            // saved-view field on southbrook.kitchen.design), so this
            // is simply which preset every tab load starts on.
            view: "persp",
            products: [],
            inventoryCategory: "all",
            inventorySearch: "",
            draggedProduct: null,
            dragHover: false,
            // touchDragProduct mirrors draggedProduct but only for the
            // touch path — kept separate so a stray touchend after a
            // completed mouse-DnD sequence can never clear the wrong one.
            touchDragProduct: null,
            toasts: [],
            channel: null,
            // 2026-07-12 (Task 4) — briefly true right after a corner
            // cabinet is auto-inserted (res.relaid); toggles the
            // o_owl_design3d_corner_flash CSS class on the stage
            // wrapper for a quick fade-out/in cue. Skipped entirely
            // under prefers-reduced-motion (toast still fires).
            cornerFlash: false,
            // Touch editing v1 — server kill-switch (T2's wiring, this
            // task's one-line addition to _southbrook_design_payload)
            // and pad anatomy (phone bottom-dock vs tablet floating,
            // spec §1 decision 4). touchPadMode starts "floating" and
            // is corrected synchronously in onMounted below, before
            // first paint reads it, then kept live by the resize
            // listener — the portal has no CSS-only width gate like the
            // backend's 767px one, so this can't be a boot-only probe.
            touchEnabledServer: false,
            touchPadMode: "floating",
        });
        this.viewButtons = VIEW_BUTTONS;
        this.categoryLabels = CATEGORY_LABELS;
        this.rulerMarks = RULER_MARKS;
        this._canvasApi = null;
        this._lastPayloadVersion = this.props.payloadVersion || 0;
        this._toastSeq = 0;
        this._toastTimers = new Set();      // audit A16 — cancel on unmount
        this._cornerFlashTimer = null;      // Task 4 — cancel on unmount
        this._cornerFlashRaf = null;        // M2 — cancel on unmount
        this._roomSaveSeq = 0;              // audit A6 — last-write-wins guard
        this._itemsMutSeq = 0;              // audit A7 — reload/add race guard
        this._lastAdd = null;               // audit A9 — hybrid double-add guard
        this._stageRef = useRef("stage");
        this._onKeyDown = this._onKeyDown.bind(this);
        this._onTouchMove = this._onTouchMove.bind(this);
        this._onTouchEnd = this._onTouchEnd.bind(this);
        // Touch editing v1 — boot-time device-capability probe (same
        // "AtBoot" pattern kitchen_configurator.js's own
        // isNarrowViewportAtBoot uses); pad mode below IS reactive to
        // resize (the portal, unlike the backend, has no width floor
        // below which it simply doesn't render at all).
        this._touchCapable = isTouchCapableAtBoot(
            typeof window !== "undefined" ? window : null,
            typeof navigator !== "undefined" ? navigator : null,
        );
        this._onTouchPadResize = () => {
            this.state.touchPadMode = touchPadModeForViewport(window);
        };
        // Touch editing v1 — single-level "restore last committed
        // state" undo fallback (spec build brief's explicit sanctioned
        // fallback for a surface with no undo stack — grepped this
        // file for "undo"/"Undo"/"UNDO" and confirmed none exists).
        // Scoped to nudge/rotate only; see _flipSelectedHand's own
        // comment for why remove/swap aren't covered by this minimal
        // path. Set by _snapshotForUndo just before each covered
        // mutation, consumed (and cleared) by _undo().
        this._lastPortalSnapshot = null;

        onMounted(() => {
            this._load();
            this._loadCatalog();
            window.addEventListener("keydown", this._onKeyDown);
            this._onTouchPadResize();
            window.addEventListener("resize", this._onTouchPadResize);
        });
        // Audit A8 — honor the payloadVersion contract: OrderBuilder bumps
        // it after autosaves; a change while this tab stays mounted must
        // re-fetch so the design reseeds from the updated order lines.
        // (Currently the tab is destroyed/remounted on every tab switch,
        // which masked this, but the prop's contract shouldn't depend on
        // that architecture holding.)
        onWillUpdateProps((next) => {
            const v = next.payloadVersion || 0;
            if (v !== this._lastPayloadVersion) {
                this._lastPayloadVersion = v;
                this._load();
            }
        });
        onWillUnmount(() => {
            window.removeEventListener("keydown", this._onKeyDown);
            window.removeEventListener("resize", this._onTouchPadResize);
            this._detachTouchListeners();
            for (const t of this._toastTimers) clearTimeout(t);
            this._toastTimers.clear();
            if (this._cornerFlashTimer) clearTimeout(this._cornerFlashTimer);
            // M2 — a rAF scheduled by _triggerCornerFlash() right before
            // unmount must not fire after teardown (it would mutate
            // state.cornerFlash / schedule a timer on a torn-down component).
            if (this._cornerFlashRaf !== null) {
                cancelAnimationFrame(this._cornerFlashRaf);
                this._cornerFlashRaf = null;
            }
        });
    }

    // ─── Toast helpers (same shape as portal_boot.esm.js's _pushToast /
    // _dismissToast — reuses portal_root.scss's .o_owl_toast* styling,
    // no new CSS needed) ──────────────────────────────────────────────
    _pushToast(text, kind = "success", ttl = 3000) {
        this._toastSeq += 1;
        const id = this._toastSeq;
        this.state.toasts.push({ id, text, kind });
        const timer = setTimeout(() => {
            this._toastTimers.delete(timer);
            const idx = this.state.toasts.findIndex((t) => t.id === id);
            if (idx >= 0) this.state.toasts.splice(idx, 1);
        }, ttl);
        this._toastTimers.add(timer);
    }

    _dismissToast(id) {
        const idx = this.state.toasts.findIndex((t) => t.id === id);
        if (idx >= 0) this.state.toasts.splice(idx, 1);
    }

    // Task 4 (2026-07-12) — brief opacity dip/restore on the 3D stage
    // wrapper when a corner cabinet was just auto-inserted (res.relaid).
    // The KitchenCanvas is an imperative THREE.js scene with no per-mesh
    // DOM, so the "fade" is a CSS transition on the stage's own wrapper
    // element rather than on the new cabinet itself. Respects
    // prefers-reduced-motion — skips the animation but the caller still
    // shows the toast regardless.
    _triggerCornerFlash() {
        if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            return;
        }
        if (this._cornerFlashTimer) {
            clearTimeout(this._cornerFlashTimer);
            this._cornerFlashTimer = null;
        }
        this.state.cornerFlash = false;
        // Restart the CSS transition even if it's already mid-flash.
        this._cornerFlashRaf = requestAnimationFrame(() => {
            this._cornerFlashRaf = null;
            this.state.cornerFlash = true;
            this._cornerFlashTimer = setTimeout(() => {
                this._cornerFlashTimer = null;
                this.state.cornerFlash = false;
            }, 350);
        });
    }

    get summary() {
        let baseCount = 0, wallCount = 0, fillerCost = 0, total = 0;
        for (const item of this.state.items) {
            const price = Number(item.price || 0) * Number(item.quantity || 1);
            total += price;
            if (item.cabinet_type === "base") baseCount += 1;
            else if (item.cabinet_type === "wall") wallCount += 1;
            else if (item.cabinet_type === "filler") fillerCost += price;
        }
        return { baseCount, wallCount, fillerCost, total };
    }

    get filteredProducts() {
        const cat = this.state.inventoryCategory;
        const q = (this.state.inventorySearch || "").trim().toLowerCase();
        return (this.state.products || []).filter((p) => {
            if (cat !== "all" && categoryOf(p.cabinet_type) !== cat) return false;
            if (!q) return true;
            return (p.name || "").toLowerCase().includes(q)
                || (p.sku || "").toLowerCase().includes(q)
                || (p.cabinet_type || "").toLowerCase().includes(q)
                || (p.material || "").toLowerCase().includes(q);
        });
    }

    get sameTypeProducts() {
        if (!this.state.selected) return [];
        const type = this.state.selected.cabinet_type;
        return (this.state.products || []).filter((p) => p.cabinet_type === type);
    }

    _money(v) {
        return `$${Number(v || 0).toFixed(2)}`;
    }

    _materialLabel(k) {
        return MATERIAL_LABELS[k] || k || "—";
    }

    // P1 — "Auto-arrange L/U" button. Runs the server-side pure layout
    // engine: wraps cabinets across walls and substitutes a real corner
    // cabinet at each inside corner (destructive — may remove/replace
    // cabinets), then re-loads from the server as the source of truth.
    async _onAutoArrange() {
        if (!this.props.orderId) return;
        const ok = window.confirm(
            "Auto-arrange will wrap your cabinets into an L/U layout and " +
            "replace the corner cabinets with a dedicated corner unit. " +
            "This may change your cabinet list. Continue?");
        if (!ok) return;
        this.state.saving = true;
        this.state.error = null;
        try {
            const resp = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/auto-arrange`,
                {},
            );
            if (resp && resp.error) {
                if (resp.error === "ROOM_TOO_SMALL") {
                    const needIn = Math.round((resp.requested_mm || 0) / 25.4);
                    const haveIn = Math.round((resp.capacity_mm || 0) / 25.4);
                    this.state.error =
                        `Not enough wall space for these cabinets (need ~${needIn}″, ` +
                        `walls hold ~${haveIn}″). Remove cabinets or enlarge the room.`;
                } else {
                    this.state.error = friendlyDesignError(resp.error);
                }
                return;
            }
        } catch (e) {
            this.state.error = friendlyDesignError(e?.message);
            return;
        } finally {
            this.state.saving = false;
        }
        await this._load();
    }

    async _load() {
        if (!this.props.orderId) {
            this.state.loading = false;
            this.state.error = "No order selected.";
            return;
        }
        this.state.loading = true;
        this.state.error = null;
        // Audit A7 — snapshot the mutation counter: if an add commits
        // while this fetch is in flight, this response was read before
        // that add and would silently drop it from local state; re-fetch
        // instead of applying the stale snapshot.
        const mutAtStart = this._itemsMutSeq;
        try {
            const payload = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d`,
                {},
            );
            if (payload && payload.error) {
                this.state.error = friendlyDesignError(payload.error);
                return;
            }
            if (mutAtStart !== this._itemsMutSeq) {
                return this._load();
            }
            this.state.designId = payload.design_id;
            this.state.room = payload.room || this.state.room;
            this.state.items = payload.items || [];
            this.state.channel = payload.channel || null;
            // Touch editing v1 — server kill-switch (T2's contract).
            this.state.touchEnabledServer = !!payload.touch_enabled;
        } catch (e) {
            this.state.error = friendlyDesignError(e?.message);
        } finally {
            this.state.loading = false;
        }
    }

    async _loadCatalog() {
        if (!this.props.orderId) return;
        try {
            const payload = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/catalog`,
                {},
            );
            if (payload && !payload.error) {
                this.state.products = payload.products || [];
            }
        } catch (e) {
            // Non-fatal: the canvas itself still works without the
            // inventory panel populated; just leave it empty.
        }
    }

    // ─── Camera view ────────────────────────────────────────────────────
    _onCanvasReady(api) {
        this._canvasApi = api;
    }

    _setView(key) {
        this.state.view = key;
        if (this._canvasApi) this._canvasApi.setView(key);
    }

    _onViewChange(key) {
        this.state.view = key;
    }

    // Interactive walls — the canvas reports which wall was clicked; it
    // becomes the active wall (where the next cabinets will be added).
    _onWallSelect(wall) {
        this.state.activeWall = wall;
    }

    // Toolbar wall-picker — the guaranteed way to select any wall, including
    // right + front (the open cutaway side, awkward to click in 3D). Clicking
    // the already-active wall clears the selection (back to no active wall).
    _pickWall(wall) {
        this.state.activeWall = this.state.activeWall === wall ? null : wall;
    }

    _wallLabel(wall) {
        return ({ back: "Back", left: "Left", right: "Right",
                  front: "Front" })[wall] || wall;
    }

    _onKeyDown(e) {
        const tag = (e.target && e.target.tagName) || "";
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
            e.target?.isContentEditable) return;
        const k = e.key;
        if (e.metaKey || e.ctrlKey) return;
        if (VIEW_KEY_MAP[k]) { e.preventDefault(); this._setView(VIEW_KEY_MAP[k]); return; }
        if (k === "r" || k === "R") { e.preventDefault(); this._setView("iso"); return; }
        if (k === "+" || k === "=") { e.preventDefault(); this._zoomBy(0.9); return; }
        if (k === "-" || k === "_") { e.preventDefault(); this._zoomBy(1.1); return; }
        if (k === "ArrowLeft" || k === "ArrowRight") {
            e.preventDefault();
            this._cycleSelection(k === "ArrowRight" ? 1 : -1);
        }
    }

    _zoomBy(factor) {
        this._canvasApi?.zoomBy(factor);
    }

    // Same selectable-order algorithm as kitchen_configurator.js's
    // _cycleSelection: base cabinets first, then wall, each sorted
    // left-to-right by x_position_in; fillers/panels aren't selectable
    // this way (nothing to edit on them). Wraps in both directions.
    _cycleSelection(dir) {
        const selectable = (this.state.items || [])
            .filter((it) => it.cabinet_type === "base" || it.cabinet_type === "wall")
            .sort((a, b) => {
                if (a.cabinet_type !== b.cabinet_type) {
                    return a.cabinet_type === "base" ? -1 : 1;
                }
                return (a.x_position_in || 0) - (b.x_position_in || 0);
            });
        if (!selectable.length) return;
        let idx = -1;
        if (this.state.selected) {
            idx = selectable.findIndex(
                (it) => it.layout_key === this.state.selected.layout_key,
            );
        }
        idx = ((idx + dir) % selectable.length + selectable.length) % selectable.length;
        this.state.selected = selectable[idx];
    }

    // ─── Room dimensions ────────────────────────────────────────────────
    async _changeRoom(field, rawValue) {
        const val = parseFloat(rawValue);
        if (!Number.isFinite(val)) return;
        this.state.room = { ...this.state.room, [field]: val };
        await this._persistRoom();
    }

    // KitchenCanvas → drag-handle room-width resize. inFlight=true fires
    // continuously during the drag (local-only, no persistence); the
    // final inFlight=false call is the commit.
    async _onResizeRoom(newWidthIn, inFlight) {
        this.state.room = { ...this.state.room, width_in: newWidthIn };
        if (inFlight) return;
        await this._persistRoom();
    }

    // KitchenCanvas → depth-handle room-depth resize. Mirrors
    // _onResizeRoom: inFlight=true streams during the drag (local
    // only), the final inFlight=false call commits via _persistRoom
    // (which already sends depth_in to the /design-3d/room route).
    async _onResizeRoomDepth(newDepthIn, inFlight) {
        if (typeof window !== "undefined" && window.__SBK_DEPTH_DEBUG__) {
            // eslint-disable-next-line no-console
            console.debug("[sbk-depth] portal _onResizeRoomDepth", { newDepthIn, inFlight });
        }
        this.state.room = { ...this.state.room, depth_in: newDepthIn };
        if (inFlight) return;
        await this._persistRoom();
    }

    async _persistRoom() {
        if (!this.props.orderId) return;
        // Audit A6 — last-write-wins: overlapping saves (resize commit
        // then a quick field edit) can resolve out of order; only the
        // newest request may apply its echoed room to state.
        this._roomSaveSeq += 1;
        const seq = this._roomSaveSeq;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/room`,
                {
                    width_in: this.state.room.width_in,
                    depth_in: this.state.room.depth_in,
                    height_in: this.state.room.height_in,
                },
            );
            if (seq !== this._roomSaveSeq) return;
            if (res && res.room) {
                this.state.room = res.room;
            } else if (res && res.error) {
                this.state.error = friendlyDesignError(res.error);
            }
        } catch (e) {
            if (seq === this._roomSaveSeq) {
                this.state.error = friendlyDesignError(e?.message);
            }
        } finally {
            if (seq === this._roomSaveSeq) {
                this.state.saving = false;
            }
        }
    }

    // KitchenCanvas → cabinet clicked. Highlight (T1). A parent-level
    // Order-Lines bridge is a later tier.
    _onSelectItem(item) {
        this.state.selected = item || null;
    }

    _deselect() {
        this.state.selected = null;
    }

    async _removeSelectedCabinet() {
        const sel = this.state.selected;
        if (!sel || !sel.layout_key || !this.props.orderId) return;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/remove`,
                { layout_key: sel.layout_key },
            );
            if (res && res.error) {
                const msg = friendlyDesignError(res.error);
                this.state.error = msg;
                this._pushToast(`Couldn't remove ${sel.product_name}: ${msg}`, "error");
                return;
            }
            this._itemsMutSeq += 1;   // audit A7 pattern — mark the mutation
            this.state.items = this.state.items.filter(
                (it) => it.layout_key !== sel.layout_key,
            );
            this.state.selected = null;
            this._pushToast(`Removed ${sel.product_name}`, "success");
        } catch (e) {
            const msg = friendlyDesignError(e?.message);
            this.state.error = msg;
            this._pushToast(`Couldn't remove ${sel.product_name}: ${msg}`, "error");
        } finally {
            this.state.saving = false;
        }
    }

    async _updateSelectedWidth(rawValue) {
        const sel = this.state.selected;
        if (!sel || !sel.layout_key || !this.props.orderId) return;
        const v = parseFloat(rawValue);
        if (!Number.isFinite(v) || v < 6 || v > 48) return;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/move`,
                { layout_key: sel.layout_key, width_in: v },
            );
            if (res && res.error) {
                this.state.error = friendlyDesignError(res.error);
                return;
            }
            const width_in = (res && res.width_in) ?? v;
            this.state.items = this.state.items.map((it) =>
                it.layout_key === sel.layout_key ? { ...it, width_in } : it,
            );
            this.state.selected = { ...sel, width_in };
        } catch (e) {
            this.state.error = friendlyDesignError(e?.message);
        } finally {
            this.state.saving = false;
        }
    }

    async _swapSelectedProduct(rawProductId) {
        const sel = this.state.selected;
        if (!sel || !sel.layout_key || !this.props.orderId) return;
        const pid = parseInt(rawProductId, 10);
        if (!Number.isFinite(pid)) return;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/swap`,
                { layout_key: sel.layout_key, product_id: pid },
            );
            if (res && res.error) {
                const msg = friendlyDesignError(res.error);
                this.state.error = msg;
                this._pushToast(`Couldn't swap product: ${msg}`, "error");
                return;
            }
            if (res && res.item) {
                this.state.items = this.state.items.map((it) =>
                    it.layout_key === sel.layout_key ? res.item : it,
                );
                this.state.selected = res.item;
                this._pushToast(`Swapped to ${res.item.product_name}`, "success");
            }
        } catch (e) {
            const msg = friendlyDesignError(e?.message);
            this.state.error = msg;
            this._pushToast(`Couldn't swap product: ${msg}`, "error");
        } finally {
            this.state.saving = false;
        }
    }

    // ─── Touch editing v1 — control pad ─────────────────────────────────
    // Phone = bottom-dock, tablet = floating (spec §1 decision 4); the
    // backend has a hard 767px CSS gate that keeps its own pad always
    // "floating", but the portal must support phones fully, so mode is
    // tracked live off resize (see the constructor's _onTouchPadResize).
    _showTouchPad() {
        return this.state.touchEnabledServer && this._touchCapable;
    }

    // Seam fix (2026-08-02, coordinator) — the canvas now emits
    // onGestureRollback/onGestureAbandon (the health-counter producers
    // that were missing end-to-end). The portal counts them locally; the
    // counters ride the next save the same way the backend's do. If this
    // surface's save path never grows touch_stats plumbing, the counts
    // are simply dropped at save time — count anyway, never throw.
    _recordTouchEvent(kind) {
        this._touchStats = this._touchStats || {
            gesture_rollbacks: 0, gesture_abandons: 0,
        };
        if (kind === "rollback") this._touchStats.gesture_rollbacks += 1;
        if (kind === "abandon") this._touchStats.gesture_abandons += 1;
    }

    // KNOWN GAPS vs. the backend pad (both flagged for the coordinator's
    // reconciliation pass — see this task's build note):
    //   - sendToWallSupported: false — this addon's /design-3d/* routes
    //     have NO wall-reassignment endpoint at all (only x/y/z/rotation/
    //     width via /move); the backend's send-to-wall calls the
    //     Odoo-ORM-only action_move via /web/dataset/call_kw, which
    //     portal customers have no ACL path to (every other portal
    //     mutation route exists specifically BECAUSE that ORM path isn't
    //     available to them). getPadAffordances still marks wallRun
    //     items sendToWall-eligible; this capability flag is what
    //     actually hides the button on this surface.
    //   - canUndo reflects the single-snapshot fallback (see
    //     _snapshotForUndo/_undo below), not a real stack.
    _touchPadCapabilities() {
        return {
            canUndo:             !!this._lastPortalSnapshot,
            canSendToWall:       false,
            sendToWallSupported: false,
            cameraReady:         !!this._canvasApi,
        };
    }

    // Nudge left/right — same per-wall axis convention as the backend's
    // _nudgeSelected (z_position_in on left/right walls, x_position_in
    // on back/front), persisted via the existing /design-3d/move route
    // (accepts x/y/z_position_in already, for drag-move + the Width
    // input's sibling fields).
    async _nudgeSelected(dir) {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).nudge) return;
        if (!item.layout_key || !this.props.orderId) return;
        this._snapshotForUndo(item);
        const alongZ = item.wall === "left" || item.wall === "right";
        const axisField = alongZ ? "z_position_in" : "x_position_in";
        const roomBoundIn = alongZ ? this.state.room.depth_in : this.state.room.width_in;
        const maxPos = Math.max(0, roomBoundIn - (item.width_in || 0));
        const next = Math.min(Math.max((item[axisField] || 0) + dir * NUDGE_STEP_IN, 0), maxPos);
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/move`,
                { layout_key: item.layout_key, [axisField]: next },
            );
            if (res && res.error) {
                this.state.error = friendlyDesignError(res.error);
                return;
            }
            this.state.items = this.state.items.map((it) =>
                it.layout_key === item.layout_key ? { ...it, [axisField]: next } : it,
            );
            this.state.selected = { ...item, [axisField]: next };
        } catch (e) {
            this.state.error = friendlyDesignError(e?.message);
        } finally {
            this.state.saving = false;
        }
    }

    // Rotate 45° (island/freestanding only) — the /design-3d/move route
    // already accepts rotation_deg (mod-360'd server-side); no route
    // change needed. Never wired to a keyboard shortcut here — rotation
    // was deliberately not ported to the portal's keydown handler before
    // touch v1 (see this file's own 2026-07-06 header note on what was/
    // wasn't ported), so the pad is the FIRST rotation affordance on
    // this surface, not a touch mirror of an existing one.
    async _rotateSelected(deltaDeg) {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).rotate) return;
        if (!item.layout_key || !this.props.orderId) return;
        this._snapshotForUndo(item);
        const next = (((item.rotation_deg || 0) + deltaDeg) % 360 + 360) % 360;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/move`,
                { layout_key: item.layout_key, rotation_deg: next },
            );
            if (res && res.error) {
                this.state.error = friendlyDesignError(res.error);
                return;
            }
            this.state.items = this.state.items.map((it) =>
                it.layout_key === item.layout_key ? { ...it, rotation_deg: next } : it,
            );
            this.state.selected = { ...item, rotation_deg: next };
            this._pushToast(`Rotated ${item.product_name} to ${next}°`, "success");
        } catch (e) {
            this.state.error = friendlyDesignError(e?.message);
        } finally {
            this.state.saving = false;
        }
    }

    // Camera-to-elevation — purely client-side, no persistence. Prefers
    // T4's purpose-built `api.cameraToElevation(wall)` (see build-t4-
    // canvas.md), falling back to `_setView`/`elevationViewForWall` —
    // same reconciliation as the backend's own version. KNOWN GAP:
    // `item.wall` is always undefined on the portal — this addon's own
    // `_southbrook_design_payload()` line-dict never serializes `wall`
    // (unlike the backend's `_serialize_configurator_lines()`, which
    // does) — so `elevationViewForWall(undefined)` always falls back to
    // "front", regardless of the cabinet's real wall. Flagged for the
    // coordinator; fixing it is a one-line serializer addition outside
    // this task's authorized one-line scope (T2's touch_enabled line).
    _cameraToSelectedElevation() {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).cameraToElevation) return;
        const wall = item.wall || "back";
        if (this._canvasApi && typeof this._canvasApi.cameraToElevation === "function") {
            this._canvasApi.cameraToElevation(wall);
            this.state.view = elevationViewForWall(wall);
            return;
        }
        this._setView(elevationViewForWall(wall));
    }

    // Flip-hand (corners only) — reuses the EXISTING _swapSelectedProduct
    // verbatim, resolving the mirrored-hand twin from the catalog by the
    // same "{base, base + '-R'}" family convention the backend's
    // _sb_resolve_corner_variant documents server-side.
    //
    // GAP flagged for the coordinator's reconciliation pass: unlike the
    // backend's action_swap_product (which enforces corner-only /
    // footprint-fit / hand-match governance server-side), this addon's
    // own /design-3d/swap route (controllers/main.py
    // southbrook_api_design_3d_swap) is a PLAIN product_id write with
    // only a same-cabinet_type check — no hand-awareness, no refusal.
    // Safe in practice here because siblingCode is always drawn from the
    // SAME template family (a genuine LH/RH pair), but there is no
    // server-side safety net if the catalog data is ever wrong, unlike
    // the backend path.
    async _flipSelectedHand() {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).flipHand) return;
        const code = item.sku || item.default_code || "";
        if (!code) {
            this._pushToast(
                "This corner cabinet has no catalog code — can't find its mirrored-hand twin.",
                "error",
            );
            return;
        }
        const base = code.endsWith("-R") ? code.slice(0, -2) : code;
        const siblingCode = code === base ? base + "-R" : base;
        const sibling = (this.state.products || []).find(
            (p) => p.cabinet_type === "corner" && (p.sku || "") === siblingCode,
        );
        if (!sibling) {
            this._pushToast(
                "No mirrored-hand twin found in the catalog for this corner.",
                "error",
            );
            return;
        }
        await this._swapSelectedProduct(sibling.product_id);
    }

    // ─── Undo fallback (spec build brief: "if none exists client-side,
    // wire undo=restore-last-committed-state ... and SAY so in the
    // note") — this file has no undo/redo stack (grepped, confirmed).
    // Single-level, scoped ONLY to the pad's own nudge/rotate (both
    // plain field writes on an EXISTING line, trivially reversible
    // through the SAME /move route). Remove and flip-hand are NOT
    // covered: a removed line can't be re-added with its original
    // identity through /design-3d/add (new layout_key), and re-swapping
    // would just re-run the same ungoverned swap route again for no
    // real safety gain — both are acceptable exclusions for this
    // minimal fallback, not oversights.
    _snapshotForUndo(item) {
        this._lastPortalSnapshot = {
            layout_key:     item.layout_key,
            x_position_in:  item.x_position_in,
            y_position_in:  item.y_position_in,
            z_position_in:  item.z_position_in,
            rotation_deg:   item.rotation_deg,
        };
    }

    async _undo() {
        const snap = this._lastPortalSnapshot;
        if (!snap || !this.props.orderId) return;
        this._lastPortalSnapshot = null;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/move`,
                {
                    layout_key:     snap.layout_key,
                    x_position_in:  snap.x_position_in,
                    y_position_in:  snap.y_position_in,
                    z_position_in:  snap.z_position_in,
                    rotation_deg:   snap.rotation_deg,
                },
            );
            if (res && res.error) {
                this.state.error = friendlyDesignError(res.error);
                return;
            }
            this.state.items = this.state.items.map((it) =>
                it.layout_key === snap.layout_key ? { ...it, ...snap } : it,
            );
            if (this.state.selected && this.state.selected.layout_key === snap.layout_key) {
                this.state.selected = { ...this.state.selected, ...snap };
            }
            this._pushToast("Undid last change", "success");
        } catch (e) {
            this.state.error = friendlyDesignError(e?.message);
        } finally {
            this.state.saving = false;
        }
    }

    // KitchenCanvas → cabinet dragged (X-axis). Persist to the design line
    // via the portal move route (T2). Optimistic: KitchenCanvas already
    // moved the mesh; we only persist. On failure, reload to resync.
    async _onMoveItem(payload) {
        const item = payload && payload.item;
        if (!item || !item.layout_key || !this.props.orderId) return;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/move`,
                {
                    layout_key: item.layout_key,
                    x_position_in: payload.x_position_in,
                },
            );
            if (res && res.error) {
                this.state.error = friendlyDesignError(res.error);
                await this._load();   // resync on server rejection
            }
        } catch (e) {
            this.state.error = friendlyDesignError(e?.message);
            await this._load();
        } finally {
            this.state.saving = false;
        }
    }

    // ─── Inventory drag-and-drop-add ────────────────────────────────────
    _onProductDragStart(ev, product) {
        if (ev.dataTransfer) {
            ev.dataTransfer.effectAllowed = "copy";
            ev.dataTransfer.setData(
                "application/x-sbk-product",
                JSON.stringify({ product_id: product.product_id }),
            );
            ev.dataTransfer.setData("text/plain", String(product.product_id));
        }
        this.state.draggedProduct = product;
    }

    _onProductDragEnd() {
        this.state.draggedProduct = null;
        this.state.dragHover = false;
    }

    _onCanvasDragOver(ev) {
        if (!ev.dataTransfer) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "copy";
        if (!this.state.dragHover) this.state.dragHover = true;
    }

    _onCanvasDragLeave(ev) {
        if (ev.currentTarget && ev.currentTarget.contains(ev.relatedTarget)) return;
        this.state.dragHover = false;
    }

    async _onCanvasDrop(ev) {
        ev.preventDefault();
        this.state.dragHover = false;
        const product = this.state.draggedProduct;
        this.state.draggedProduct = null;
        if (!product) return;
        await this._addProductAt(product, ev);
    }

    // ─── Touch fallback for the inventory drag-to-add ──────────────────
    // iOS/Android never fire the HTML5 dragstart/dragover/drop events
    // _onProductDragStart/_onCanvasDrop rely on for mouse/trackpad, so
    // touch needs its own path to the SAME _addProductAt endpoint.
    _onProductTouchStart(ev, product) {
        // Audit A15 — a second finger while a touch-drag is already in
        // progress would silently re-target the drag; first touch wins.
        if (this.state.touchDragProduct) return;
        this.state.touchDragProduct = product;
        this.state.draggedProduct = product;   // reuse dragHover highlight
        document.addEventListener("touchmove", this._onTouchMove, { passive: false });
        document.addEventListener("touchend", this._onTouchEnd);
        document.addEventListener("touchcancel", this._onTouchEnd);
    }

    _stageContainsPoint(clientX, clientY) {
        const el = this._stageRef.el;
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        return clientX >= rect.left && clientX <= rect.right &&
               clientY >= rect.top && clientY <= rect.bottom;
    }

    _onTouchMove(ev) {
        if (!this.state.touchDragProduct) return;
        const touch = ev.touches[0];
        if (!touch) return;
        // Only take over scrolling once we know this touch is actually
        // dragging a product over the stage — otherwise a tap-and-scroll
        // on the inventory list itself would get stuck.
        const over = this._stageContainsPoint(touch.clientX, touch.clientY);
        if (over) ev.preventDefault();
        this.state.dragHover = over;
    }

    async _onTouchEnd(ev) {
        this._detachTouchListeners();
        const product = this.state.touchDragProduct;
        this.state.touchDragProduct = null;
        this.state.draggedProduct = null;
        this.state.dragHover = false;
        if (!product) return;
        const touch = ev.changedTouches && ev.changedTouches[0];
        if (!touch || !this._stageContainsPoint(touch.clientX, touch.clientY)) return;
        await this._addProductAt(product, { clientX: touch.clientX, clientY: touch.clientY });
    }

    _detachTouchListeners() {
        document.removeEventListener("touchmove", this._onTouchMove);
        document.removeEventListener("touchend", this._onTouchEnd);
        document.removeEventListener("touchcancel", this._onTouchEnd);
    }

    // Shared by the mouse-DnD drop handler and the touch fallback —
    // evLike only needs clientX/clientY (all computeDropX/ndcFromEvent
    // actually read), so a plain {clientX, clientY} object from a touch
    // point works exactly like a real DragEvent here.
    async _addProductAt(product, evLike) {
        if (!this.props.orderId) return;
        // Audit A9 — on hybrid touch+mouse hardware (Surface-class, some
        // Android WebViews) one physical gesture can fire BOTH the HTML5
        // drop path and the touch path; suppress the echo.
        const now = performance.now();
        if (this._lastAdd &&
            this._lastAdd.pid === product.product_id &&
            now - this._lastAdd.t < 700) {
            return;
        }
        this._lastAdd = { pid: product.product_id, t: now };
        const dropX = this._canvasApi?.computeDropX(evLike) ?? null;
        this.state.saving = true;
        try {
            const res = await rpcCall(
                `/southbrook/api/order/${encodeURIComponent(this.props.orderId)}/design-3d/add`,
                {
                    product_id: product.product_id,
                    x_position_in: dropX,
                    // Phase 3 — if a wall is selected, the cabinet lands on it
                    // (the server places + orients it via the layout engine).
                    wall: this.state.activeWall || null,
                },
            );
            if (res && res.error) {
                const msg = friendlyDesignError(res.error);
                this.state.error = msg;
                this._pushToast(`Couldn't add ${product.name}: ${msg}`, "error");
            } else if (res && res.relaid && res.payload) {
                // Continuous corner resolution re-laid the whole scene.
                // Task 4 — toast + brief stage fade are additive; neither
                // blocks or delays the scene swap below.
                this._pushToast("Corner cabinet inserted automatically.", "success");
                this._triggerCornerFlash();
                this._itemsMutSeq += 1;
                this.state.items = res.payload.items || [];
                this.state.room = res.payload.room || this.state.room;
            } else if (res && res.item) {
                // Audit A7 — mark the mutation so an in-flight _load()
                // snapshot from before this add re-fetches instead of
                // clobbering it out of local state.
                this._itemsMutSeq += 1;
                this.state.items = [...this.state.items, res.item];
                this.state.selected = res.item;
                // Audit A1/A5 — the server now clamps/append-computes
                // x_position_in, so `required` is always sane; when the
                // room must grow to fit, PERSIST the new width (it was
                // local-only before, so any reload re-clipped the
                // cabinet that triggered the expansion).
                const required = res.item.x_position_in + res.item.width_in;
                if (Number.isFinite(required) &&
                    required > this.state.room.width_in) {
                    this.state.room = {
                        ...this.state.room,
                        width_in: Math.ceil(required / 6) * 6,
                    };
                    await this._persistRoom();
                }
                this._pushToast(`Added ${res.item.product_name}`, "success");
            }
        } catch (e) {
            const msg = friendlyDesignError(e?.message);
            this.state.error = msg;
            this._pushToast(`Couldn't add ${product.name}: ${msg}`, "error");
        } finally {
            this.state.saving = false;
        }
    }
}
