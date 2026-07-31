/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator
 * OWL 2 component with real Three.js isometric scene.
 *
 * Architecture:
 *  - OWL manages reactive state (room dims, products, selection, saving)
 *  - Three.js renders the 3D scene inside a <div t-ref="canvas3d">
 *  - JSON-RPC controller provides live Odoo product data
 *  - Scene rebuilds whenever room dimensions or product list change
 */

import { Component, onMounted, onWillStart, onWillUnmount, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
// Task #48 — portal-user detection for the customer picker + reassurance
// pill. v19 exposes `isInternalUser` on the `@web/core/user` singleton
// (mapped from res.users.share server-side; internal users have
// share=False → isInternalUser=true). No `share` prop directly on the
// client side; do NOT read `user.share` — that's the server-side flag.
import { user } from "@web/core/user";

// Rec D · Sprint 2d Step 24e — dead 3D imports excised.
// 24c moved the visible surface into <KitchenCanvas>; the parent's
// inline scene was already self-disabled (canvas3dRef.el === null).
// Survivors from the pre-24e canvas import block:
//   packRow (state math — drives _recomputeLayoutFromItems)
//   KitchenCanvas (the child component)
import { packRow } from "@southbrook_kitchen_3d_configurator/js/canvas/pack_row.esm";
import { KitchenCanvas } from "@southbrook_kitchen_3d_configurator/js/canvas/kitchen_canvas.esm";
// PR2 — WALLS is the single source of truth for wall identifiers; see
// constants.esm.js. Used below to default a new cabinet's persisted
// `wall` to the active wall selection.
import { WALLS } from "@southbrook_kitchen_3d_configurator/js/canvas/constants.esm";

const actionRegistry = registry.category("actions");

// ─── OWL Component ────────────────────────────────────────────────────────────
class SouthbrookKitchenConfigurator extends Component {
    setup() {
        this.notification = useService("notification");
        this.action       = useService("action");

        // PR2.5 — Odoo 19 client-action components receive `props.action`
        // (the full ir.actions.client record); `_getActionInfo` in the
        // core action service (web/static/src/webclient/actions/
        // action_service.js, `_executeClientAction`) builds component
        // props as `{...clientAction.extractProps?.(action), action,
        // actionId}` — it does NOT spread `action.params` onto the flat
        // props. This component registers with `actionRegistry.add(...)`
        // and defines no `extractProps`, so every `this.props.design_id`
        // (etc.) read below was always `undefined`. That's the root
        // cause of the "reopens as a blank 12in room" defect: room
        // dims/design_id/partner_id/filler_strategy/etc. never arrived.
        // Read from `props.action.params` first; keep the flat
        // `this.props` as a fallback so a caller that ever passes props
        // directly (tests, a future doAction shape) still works.
        const params = (this.props.action && this.props.action.params) || this.props || {};
        this._actionParams = params;

        // PR2.5a (Gap 1) — a direct-URL reload (browser refresh /
        // bookmark) restores the client action WITHOUT `params.design_id`:
        // Odoo 19's router rebuilds the action purely from the URL path,
        // never replaying the `params` dict action_open_configurator()
        // originally sent. Verified against the vendored source inside
        // the local `sami-odoo` container:
        //   - web/static/src/core/browser/router.js `urlToState`
        //     (~lines 186-232) parses a path segment like
        //     `/odoo/southbrook.kitchen.design/81/
        //     southbrook_kitchen_configurator` into
        //     actionStack = [{model:"southbrook.kitchen.design", resId:81},
        //                    {action:"southbrook_kitchen_configurator",
        //                     active_id:81}]
        //     and merges the LAST entry's keys onto the top-level state
        //     (so state.active_id = 81, state.action = the tag).
        //   - web/static/src/webclient/actions/action_service.js
        //     `_getActionParams` (~lines 505-536), reached ONLY from
        //     `loadState()` (i.e. only on a URL-driven load, never on a
        //     live doAction() button dispatch), builds
        //     `actionRequest = { context, params: state, tag, type:
        //     "ir.actions.client" }` for a registered client-action tag —
        //     so `action.params.actionStack` (and `.active_id`) exist
        //     ONLY when Odoo reconstructed the action from the URL.
        // We recover the design id from the actionStack frame that
        // precedes our own action, but ONLY when that frame's `model` is
        // genuinely "southbrook.kitchen.design" — this is what tells a
        // reload of "an opened design's configurator" (safe: active_id
        // really is the design) apart from a reload of one of the
        // "bare" entry points with no design_id at all (a room with no
        // bridged design — southbrook_room.py action_open_kitchen_3d —
        // or an SO with neither a design nor a room —
        // sale_order.py action_open_in_kitchen_3d — whose preceding
        // record is a southbrook.room / sale.order, not a design;
        // treating THAT active_id as a design id would silently hydrate
        // an unrelated record). Live button-click opens never populate
        // `params.actionStack` at all (see above), so this fallback can
        // only ever fire on a URL restore — it never runs on a normal
        // open, even the "bare" ones.
        const restoredStack = params.actionStack;
        const restoredDesignFrame = Array.isArray(restoredStack)
            ? restoredStack.find(
                  // router.js `urlToState` can also produce resId:"new"
                  // (an unsaved record's form) — explicitly require a
                  // real numeric id here, never treat "new" as a
                  // design id.
                  (s) => s && s.model === "southbrook.kitchen.design"
                      && typeof s.resId === "number"
              )
            : null;
        const restoredDesignId = restoredDesignFrame ? restoredDesignFrame.resId : null;
        // Room dims / design name are never part of `params` on a URL
        // restore (only the id survives) — _hydrateFromDesign() backfills
        // them additively from the /load_design_lines response (server
        // extended in PR2.5a). Track whether `params` explicitly supplied
        // them so that path knows it's safe to overwrite state.room /
        // state.designName only when params did NOT — params, when
        // present, keep precedence.
        this._hasExplicitRoomParams = !!(
            params.room_width_in || params.room_depth_in || params.room_height_in
        );
        this._hasExplicitDesignName = !!params.design_name;

        // ── Reactive state ────────────────────────────────────────────────────
        this.state = useState({
            loading:  true,
            saving:   false,
            error:     "",
            errorCode: "",
            errorCta:  null,
            room: {
                width_in:  params.room_width_in  || 96,
                depth_in:  params.room_depth_in  || 96,
                height_in: params.room_height_in || 96,
            },
            products:  [],
            items:     [],
            selected:  null,
            // F14 — catalog BROWSING is not layout SELECTION. Clicking an
            // inventory row previews/highlights that catalog product; it
            // must never write `selected` (a raw catalog product has no
            // layout_key, so the "Selected Cabinet" edit drawer's actions
            // — remove/width/swap — would silently no-op via findIndex
            // -1). Only `_previewCatalogProduct` writes this field.
            catalogPreview: null,
            // PR1 — which room wall is the active target. Owned here as
            // pure UI state only; the shared <KitchenCanvas> does all the
            // wall hover/click/highlight/raycast and reports the pick via
            // onWallSelect. Nothing downstream reads this yet, so
            // placement / save / rendering are intentionally unaffected
            // (that arrives in PR2/PR3/PR4).
            activeWall: null,
            summary:   { base_count: 0, wall_count: 0, total: 0, price: 0, remainder_in: 0 },
            // PR2.5a (Gap 1) — falls back to the URL-restore-only
            // actionStack signal (see setup() comment above) when
            // params.design_id is absent (a direct-URL reload).
            designId:  params.design_id || restoredDesignId || null,
            designName: params.design_name || "",
            // D1 — multi-view camera system. 'iso' default; switchable
            // among iso/top/front/left/right/persp. Hotkeys 1-6 + R reset.
            view:      "iso",
            // D3 — partner-driven channel pricelist. partnerId is set
            // from props (when opened from a design with a customer)
            // or stays null (configurator opened standalone). channel
            // meta is populated by the first /products + /layout RPC
            // response and drives the topbar badge.
            partnerId: params.partner_id || null,
            channel:   {
                partner_name:    "",
                channel:         "retail",
                channel_label:   "Retail",
                pricelist_name:  "",
                currency_symbol: "$",
            },
            // D5 — debounced auto-save state. autoSaving renders the
            // tiny "Saving..." pill; lastAutoSaveAt feeds the "Saved
            // <Xs> ago" hint.
            autoSaving:      false,
            lastAutoSaveAt:  0,
            // D7 — filler placement strategy. Drives /layout on each
            // refresh; default matches the controller default ("split"
            // — half-width fillers at both ends, most common spec).
            fillerStrategy:  params.filler_strategy || "split",
            // D8 — wall-cab Z alignment + soffit height. Defaults match
            // the model defaults so an unsaved design still computes
            // sensible wall-cab positions.
            wallCabTopAlignment: params.wall_cab_top_alignment || "fixed_gap",
            soffitHeightIn:      params.soffit_height_in       || 84.0,
            // F12 — reactive mirror of `this._hydratedFromDesign` (a
            // plain, non-reactive flag `_refreshLayout()` already uses
            // internally to stop regenerating a reopened design's
            // layout). This copy exists purely so the template can
            // disable the filler-strategy / wall-cab-top / soffit
            // controls once hydrated — those controls are dead post-
            // hydration (see `_refreshLayout`'s guard) but, before this
            // fix, still fired the debounced auto-save and lit the
            // "Auto-saved" pill, lying about having applied the edit.
            hydratedFromDesign: false,
            warnings:            [],
            // D13 — searchable inventory + drag-and-drop add.
            inventorySearch: "",
            // Category filter pill selection. Values: "all" |
            // "base" | "wall" | "tall" | "panels". Tab click toggles
            // this; _filteredProducts() applies it BEFORE the search
            // filter so users can narrow to "wall" then type a size.
            inventoryCategory: "all",
            dragHover:       false,
            // D16 — Track which product is being dragged from the
            // inventory so the canvas can highlight the matching
            // drop-lane. HTML5 dragover events don't expose
            // dataTransfer payloads (only types) for security, so we
            // stash the product on dragstart / clear on dragend|drop.
            draggedProduct:  null,
            // Task #48 — Top-bar customer picker.
            //   partnerName mirrors state.partnerId for display; kept
            //     alongside the Number id so the topbar can render the
            //     selected customer's label without a second RPC.
            //   portalUser is a memoized `!user.isInternalUser` — set
            //     once in setup() so it survives OWL reactive reads.
            //   picker* control the dropdown lifecycle (open, loading,
            //     result list). Results fetched lazily on focus so the
            //     initial paint isn't gated by the RPC.
            partnerName:       params.partner_name || "",
            portalUser:        false,
            pickerOpen:        false,
            pickerLoading:     false,
            pickerResults:     [],
        });
        // Auto-save debounce timer (not reactive; managed imperatively).
        this._autoSaveTimer = null;

        // Task #48 — Portal-user detection. v19's `@web/core/user`
        // singleton exposes `isInternalUser` (server-side maps from
        // res.users.share — internal users have share=False → this is
        // true). A missing/null value is treated as internal so an
        // office rep with a stale bundle still sees the picker.
        try {
            this.state.portalUser = user && user.isInternalUser === false;
        } catch (_) {
            this.state.portalUser = false;
        }

        // Rec D · Sprint 2d Step 24c/24e — child <KitchenCanvas> owns
        // the visible 3D scene and every scene-side handler (mouse,
        // wheel, resize observer). Parent keeps only an imperative
        // handle so window-scoped shortcuts (view keys, zoom keys,
        // Cmd/Ctrl+S) and the HTML5 drop handler can delegate on
        // demand. Populated by _onCanvasReady once the child's
        // onMounted finishes initScene + buildScene.
        this._canvasApi = null;

        // Only _onKeyDown remains pre-bound — it attaches to `window`
        // so we need a stable reference for add/removeEventListener.
        // The five mouse/wheel binds excised at 24e moved into
        // <KitchenCanvas> at 24b/24c.
        this._onKeyDown = this._onKeyDown.bind(this);

        // ── Lifecycle ─────────────────────────────────────────────────────────
        onWillStart(async () => {
            // 24e — loadThreeJS moved into <KitchenCanvas>.onMounted;
            // parent no longer touches THREE. Product + defaults still
            // load in parallel so first paint isn't gated.
            await Promise.all([
                this._loadProducts(),
                this._loadUserDefaults(),
            ]);
            // PR2.5 — a design_id means there's a canonical saved layout
            // to reopen; hydrate from it instead of running the /layout
            // generator (which would silently replace the saved
            // cabinets with a freshly computed fill — the destructive-
            // reopen bug). No design_id (brand-new/standalone session)
            // keeps the original generate-on-open behavior.
            if (this.state.designId) {
                await this._hydrateFromDesign();
            } else {
                await this._refreshLayout();
            }
            this.state.loading = false;
        });

        // T7 — auto-save first-action gate. Programmatic refreshes
        // (hydration, template instantiation echo, prop-driven
        // recomputes) must NEVER auto-save over a saved design before
        // the rep has actually touched anything: save_design is a
        // full-replace writer. Every genuine user mutation calls
        // _markUserAction() to open the gate. (Undo/redo deferred —
        // investigation E-f: the gate is the safe half.)
        this._userActed = false;

        onMounted(() => {
            // 24e — scene bring-up is <KitchenCanvas>'s job. Parent
            // only wires window-scoped keyboard shortcuts here.
            window.addEventListener("keydown", this._onKeyDown);
            // T7 — read-only automation/diagnostics hook. Deep-copied
            // snapshot with a FIXED key contract; never a mutation
            // surface.
            window.__sbk = Object.assign(window.__sbk || {}, {
                getLayout: () => (this.state.items || []).map(it => ({
                    id:            it.id || null,
                    sku:           it.default_code || null,
                    wall:          it.wall || "back",
                    x_position_in: it.x_position_in || 0,
                    width_in:      it.width_in || 0,
                    cabinet_type:  it.cabinet_type || "base",
                    is_filler:     it.cabinet_type === "filler",
                })),
            });
        });

        onWillUnmount(() => {
            // 24e — no scene to destroy at parent scope. Clear the
            // pending auto-save (so we don't fire after unmount) and
            // detach the window listener; the child's onWillUnmount
            // handles scene disposal itself.
            if (this._autoSaveTimer) {
                clearTimeout(this._autoSaveTimer);
                this._autoSaveTimer = null;
            }
            window.removeEventListener("keydown", this._onKeyDown);
            if (window.__sbk) {
                delete window.__sbk.getLayout;   // T7 — hook dies with us
            }
        });
    }

    // T7 — flips the auto-save gate open. Called by every GENUINE user
    // mutation entry point (add/drop, drag end, remove, width/swap,
    // room change, wall select) and nowhere else.
    _markUserAction() {
        this._userActed = true;
    }

    // ─── Odoo data ──────────────────────────────────────────────────────────────
    // ─── D4 — sticky per-user room defaults ─────────────────────────────────────
    // Reads the user's saved defaults. If the configurator was opened
    // without a design_id or pre-set room dims, prefill the room
    // inputs so the rep doesn't retype 192/144/96 on every new design.
    async _loadUserDefaults() {
        try {
            const d = await rpc("/southbrook_kitchen/configurator/user_defaults", {
                save: false,
            });
            this._userDefaults = d;
            const params = this._actionParams || {};
            const hasExplicitProps = !!(params.room_width_in
                                    || params.room_depth_in
                                    || params.room_height_in
                                    || params.design_id);
            if (!hasExplicitProps && d) {
                if (d.width  > 0) this.state.room.width_in  = d.width;
                if (d.depth  > 0) this.state.room.depth_in  = d.depth;
                if (d.height > 0) this.state.room.height_in = d.height;
            }
        } catch (_) {
            this._userDefaults = null;
        }
    }

    async _saveAsMyDefault() {
        try {
            await rpc("/southbrook_kitchen/configurator/user_defaults", {
                save:   true,
                width:  this.state.room.width_in,
                depth:  this.state.room.depth_in,
                height: this.state.room.height_in,
            });
            this.notification.add("Saved as your default room size.", { type: "success" });
        } catch (e) {
            this.notification.add("Couldn't save default: " + (e.message || e), { type: "danger" });
        }
    }

    async _loadProducts() {
        try {
            const resp = await rpc("/southbrook_kitchen/configurator/products", {
                partner_id: this.state.partnerId || false,
            });
            // D3 — response is now {channel, products}; handle the
            // legacy bare-array shape too for forward-compat.
            if (Array.isArray(resp)) {
                this.state.products = resp;
            } else {
                this.state.products = resp.products || [];
                if (resp.channel) this.state.channel = resp.channel;
            }
        } catch (_) {
            this.state.products = [];
        }
    }

    // ─── Task #48 — Top-bar customer picker ─────────────────────────────────────
    // Agent A owns the model-side helpers (`_get_recent_partners_for_picker`
    // and `_get_or_create_walkin_partner`). Both are called via call_kw so
    // ACLs + rules resolve exactly as they would from any other client.
    //
    // Never opens for portal users — Agent A's default_get already
    // auto-fills partner_id from user.partner_id, and we render a
    // reassurance pill ("Designing as {name}") in the same slot.
    async _openCustomerPicker() {
        if (this.state.portalUser) return;
        this.state.pickerOpen = true;
        if (this.state.pickerResults && this.state.pickerResults.length) {
            // Cached from a prior focus — refetch only on _refreshPicker.
            return;
        }
        await this._refreshPicker();
    }

    async _refreshPicker() {
        if (this.state.portalUser) return;
        this.state.pickerLoading = true;
        try {
            const results = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design",
                method: "get_recent_partners_for_picker",
                args:   [10],
                kwargs: {},
            });
            this.state.pickerResults = Array.isArray(results) ? results : [];
        } catch (e) {
            // Non-fatal — the walk-in option below still works.
            console.warn("[SouthbrookKitchenConfigurator] picker fetch failed:", e);
            this.state.pickerResults = [];
        } finally {
            this.state.pickerLoading = false;
        }
    }

    // Blur-driven close needs a small delay so a click on a dropdown
    // option gets its mousedown->click cycle before we tear down the
    // list. 150ms is enough for even a slow tap on tablet.
    _closeCustomerPicker() {
        setTimeout(() => { this.state.pickerOpen = false; }, 150);
    }

    // Input handler mirrors the typed text into state.partnerName so
    // the field is controlled + rerenders on paste. Selecting an
    // option overwrites partnerName with the canonical record label.
    _onCustomerTyped(ev) {
        this.state.partnerName = ev.target.value || "";
    }

    async _setCustomer(partner) {
        if (!partner || !partner.id) return;
        this.state.partnerId    = partner.id;
        this.state.partnerName  = partner.name || "";
        this.state.pickerOpen   = false;
        // Refetching /products picks up the new channel pricelist and
        // repaints the inventory prices; /layout follows so the summary
        // and any per-item price fields also update.
        await this._loadProducts();
        await this._refreshLayout();
        // Persist server-side so the design carries the new partner
        // into the next session even if the browser refreshes before
        // the debounced auto-save fires.
        this._queueAutoSave();
    }

    async _useWalkInCustomer() {
        this.state.pickerLoading = true;
        try {
            const partnerId = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design",
                method: "get_or_create_walkin_partner",
                args:   [],
                kwargs: {},
            });
            const pid = parseInt(partnerId, 10);
            if (Number.isFinite(pid) && pid > 0) {
                await this._setCustomer({ id: pid, name: "Walk-in Customer" });
            }
        } catch (e) {
            this.notification.add(
                "Couldn't set walk-in customer: " + (e.message || e),
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.pickerLoading = false;
        }
    }

    // Task #48 — Count summary. Filler is NEVER counted since fillers
    // are billing-invisible (no line on the SO, no cabinet on the
    // shop floor) — matching the existing convention in the price
    // summary (`if it.cabinet_type !== "filler"`).
    _nonFillerCount() {
        return (this.state.items || []).filter(
            it => it.cabinet_type !== "filler"
        ).length;
    }

    // PR2.5 — Reopen an existing design from its canonical saved lines
    // instead of running the /layout generator. /layout always computes
    // a *fresh* cabinet fill for the current room width; calling it on
    // open would immediately discard whatever was saved (design 81:
    // 84×24 room / 3 base cabinets → rendered as an empty 12in default
    // room), and because save_design is a full-replace writer, the very
    // next auto-save would persist that regenerated/empty state over
    // the original — data loss. /load_design_lines is the read-side
    // counterpart PR2 already wired to emit `wall`; this is its first
    // caller from the boot path.
    //
    // Sets `this._hydratedFromDesign = true` on success so
    // `_refreshLayout()` (called by every later room/filler/alignment
    // edit) knows to stop regenerating for the rest of this session —
    // see the guard at the top of `_refreshLayout`.
    // C2 — the single source of truth for turning a server line-dict
    // (load_design_lines / save_design's post-relaid `lines`) into a
    // state.items entry. `name` mirrors `product_name` — every other
    // item-producing path (/layout, _addCabinetFromProduct) sets `name`
    // and a few UI spots (the detail-panel header, "Moved X to Y‴"
    // toasts) read `state.selected.name` directly rather than falling
    // back to `product_name`. Shared by _hydrateFromDesign and
    // _applyRelaidResult so both land items in the exact same shape.
    // Task 8 (corner-remediation) — the 3D renderer's corner-cabinet
    // builder (canvas/corner_cabinet.esm.js) picks its style (blind /
    // pie-cut / diagonal) off each design line's product default_code
    // prefix. Server line-dicts from /load_design_lines and /rearrange
    // (controllers/main.py _serialize_design_lines) now send
    // default_code directly (code-review fix, 2026-07-30) — read that
    // first. The state.products catalog lookup is kept only as a
    // fallback for shapes that predate/bypass that field (e.g. a raw
    // line dict missing it for some other reason); it is NOT relied on
    // as the primary path any more, because /products gates on
    // southbrook_is_cabinet=True and a derived corner-node line can
    // legitimately reference a product outside that panel-catalog
    // filter (e.g. SB-WALL-CORNER/SB-CORNER-BLIND before/without a
    // catalog-visibility flag) — a catalog-only lookup would silently
    // never resolve those. Non-destructive either way: an explicit
    // default_code already on the line always wins; falls back to null
    // (renderer degrades to the plain box) when neither source has it.
    _lineToItem(line) {
        let defaultCode = line.default_code || null;
        if (!defaultCode) {
            const product = (this.state.products || [])
                .find(p => p.product_id === line.product_id);
            defaultCode = (product && product.sku) || null;
        }
        return { ...line, name: line.product_name, default_code: defaultCode };
    }

    async _hydrateFromDesign() {
        try {
            const resp = await rpc(
                "/southbrook_kitchen/configurator/load_design_lines",
                { design_id: this.state.designId },
            );
            // PR2.5a (Gap 1) — backfill room dims / design name from the
            // server's additive echo (controllers/main.py
            // load_design_lines) when `params` didn't already supply
            // them explicitly. A live button-click open always does —
            // action_open_configurator forwards the same design's
            // room_width_in/depth_in/height_in + design_name — so this
            // is a no-op there; only a direct-URL reload skips `params`
            // entirely, and that's the case this backfills. Params, when
            // present, keep precedence (see the `_hasExplicit*` flags
            // set in setup()) since they're always sourced from the same
            // design row anyway — no real conflict is possible.
            if (resp && resp.room && !this._hasExplicitRoomParams) {
                this.state.room = {
                    width_in:  resp.room.width_in  || this.state.room.width_in,
                    depth_in:  resp.room.depth_in  || this.state.room.depth_in,
                    height_in: resp.room.height_in || this.state.room.height_in,
                };
            }
            if (resp && resp.design_name && !this._hasExplicitDesignName) {
                this.state.designName = resp.design_name;
            }
            const lines = (resp && resp.lines) || [];
            if (!lines.length) {
                // Design record exists but has no saved configurator-
                // origin lines yet (e.g. freshly created from a template
                // preset with generate_layout=False, or from the "open
                // configurator" action on a design nobody has ever
                // saved cabinets into). Nothing to protect — fall back
                // to the generator exactly like the no-design_id path
                // so the rep still sees a starter fill, and leave
                // _hydratedFromDesign unset so normal generate-on-edit
                // behavior continues until the first real save. Safe:
                // the next save is a full-replace of nothing.
                await this._refreshLayout();
                return;
            }
            // `name` mirrors `product_name` — every other item-producing
            // path (/layout, _addCabinetFromProduct) sets `name` and a
            // few UI spots (the detail-panel header, "Moved X to Y‴"
            // toasts) read `state.selected.name` directly rather than
            // falling back to `product_name`. /load_design_lines only
            // emits `product_name`; alias it here so a hydrated
            // session's selected-cabinet panel isn't blank.
            this.state.items = lines.map(line => this._lineToItem(line));
            // Task 10 (SBK-023) — a design saved before this fix could
            // have ended up with a lone filler line and ZERO real
            // cabinets (the confirmed "$70 persisted, zero cabinets"
            // finding). Reopening such a design must not pre-select
            // that phantom filler as if it were a real cabinet to edit;
            // leave nothing selected instead. Any design with at least
            // one real cabinet keeps the original items[0] selection
            // unchanged.
            const hasRealCabinet = this.state.items.some(
                it => it.cabinet_type !== "filler");
            if (this.state.items.length && hasRealCabinet) {
                this.state.selected = this.state.items[0];
            }
            this._recomputeLayoutFromItems();
            this._hydratedFromDesign = true;
            // F12 — reactive mirror; see the state field's doc comment.
            this.state.hydratedFromDesign = true;
        } catch (e) {
            // PR2.5a (Gap 2) — fail loud, don't fall back to the
            // generator. A TRANSIENT load_design_lines failure used to
            // silently fall through to `_refreshLayout()` (the
            // generator) while `designId` stayed set — the next
            // debounced auto-save would then full-replace the saved
            // design with a freshly generated (and completely
            // different) fill: the exact overwrite class PR2.5 closed.
            // Instead: surface the existing error-banner mechanism
            // (`state.error` / `state.errorCode`, rendered at
            // `o_sbk_error` in the template — see the
            // `t-elif="state.error"` branch) and hard-gate every write
            // path (`_queueAutoSave` + `_saveDesign`) behind
            // `_hydrationFailed` until the rep reloads the page.
            // `_hydrationFailed` is set ONLY here, in the
            // designId-was-set + load_design_lines-threw path, so a
            // normal session (no designId, or a successful hydrate, or
            // the empty-lines branch above) can never trip it.
            console.warn(
                "[SouthbrookKitchenConfigurator] load_design_lines failed; "
                + "refusing to fall back to the generator (would "
                + "silently overwrite the saved design on the next "
                + "auto-save):", e
            );
            this._hydrationFailed = true;
            this.state.error = "Couldn't load the saved design — reload "
                + "the page. Saving is disabled to protect your design.";
            this.state.errorCode = "HYDRATION_FAILED";
            this.state.errorCta = null;
        }
    }

    async _refreshLayout(opts = {}) {
        // PR2.5 — once hydrated from a saved design's canonical lines,
        // /layout must never run again for the life of this session:
        // it's a pure generator that reflows a brand-new cabinet fill
        // for the current room width with no knowledge of what was
        // loaded, and would silently replace the saved layout (losing
        // pinned positions + layout_keys — the exact defect this PR
        // fixes, since the next auto-save would then persist the
        // regenerated state over the original design). Room / filler /
        // alignment / soffit edits made after reopening still update
        // their piece of `state` and re-run the local packer
        // (`_recomputeLayoutFromItems`) so the summary/price reflect
        // the edit — they just don't trigger a server-side regenerate.
        //
        // Documented trade-off (accepted, out of scope for this P1 data
        // -integrity fix): the server's filler/remainder-width recompute
        // and channel-pricelist repricing for the /layout-only fields
        // don't refresh after hydration. Re-running the generator
        // post-hydration is a placement/packing concern, explicitly out
        // of scope per the PR2.5 brief ("no placement/packing changes").
        // 2026-07-27 (user decision) — `opts.force` pierces the guard for
        // the ROOM-RESIZE family only (width/depth pullers, the numeric
        // room fields, the ± stretch buttons): resizing the room on a
        // reopened saved design now REGENERATES the layout, replacing the
        // saved arrangement (and the ensuing auto-save persists that).
        // The guard still protects everything else — most importantly the
        // boot/hydration path the original PR2.5 data-loss bug came from.
        // After a successful forced regen the design is generator-owned
        // again, so the hydration flag flips off below (re-enabling the
        // filler/alignment controls consistently).
        if (this._hydratedFromDesign && !opts.force) {
            this._recomputeLayoutFromItems();
            return;
        }
        try {
            const result = await rpc("/southbrook_kitchen/configurator/layout", {
                room_width_in:           this.state.room.width_in,
                room_depth_in:           this.state.room.depth_in,
                room_height_in:          this.state.room.height_in,
                partner_id:              this.state.partnerId || false,
                filler_strategy:         this.state.fillerStrategy || "split",
                wall_cab_top_alignment:  this.state.wallCabTopAlignment || "fixed_gap",
                soffit_height_in:        this.state.soffitHeightIn || 84.0,
            });
            this.state.error      = result.error || "";
            this.state.errorCode  = result.error_code || "";
            this.state.errorCta   = result.error_cta || null;
            this.state.items      = result.items  || [];
            this.state.summary    = result.summary || this.state.summary;
            this.state.warnings   = result.warnings || [];
            if (result.channel) this.state.channel = result.channel;
            if (!this.state.selected && this.state.items.length) {
                this.state.selected = this.state.items[0];
            }
            // Forced regen on a hydrated design succeeded: the layout is
            // now generator-owned — drop hydration semantics so later
            // edits (incl. the F12-disabled controls) behave like a
            // normal fresh session.
            if (opts.force && this._hydratedFromDesign) {
                this._hydratedFromDesign = false;
                this.state.hydratedFromDesign = false;
            }
            // Rebuild 3D after data refresh
            } catch (e) {
            this.state.error = "Layout error: " + (e.message || e);
        }
    }

    // ─── Templates (T3): top-frame entry to the four-dropdown picker ────────────
    async _openTemplatePicker() {
        // v19 doAction REQUIRES explicit views (memory: doaction_views_required)
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Start from Template",
            res_model: "kitchen.design.template.picker",
            views: [[false, "form"]],
            target: "new",
        });
    }

    // ─── Room controls ──────────────────────────────────────────────────────────
    // T7 — a SAVED (hydrated) design's room resize routes through the
    // engine (`/rearrange` → action_auto_arrange, corners included),
    // never the naive single-wall /layout generator, which would
    // regenerate — and on the next save REPLACE — the canonical
    // multi-wall layout. Fresh unsaved sessions keep /layout unchanged.
    async _rearrangeOnServer() {
        try {
            const res = await rpc(
                "/southbrook_kitchen/configurator/rearrange",
                { design_id: this.state.designId, room: this.state.room },
            );
            if (res && res.error_code === "ROOM_TOO_SMALL") {
                if (res.room) {
                    this.state.room = { ...this.state.room, ...res.room };
                }
                this.notification.add(res.error, { type: "warning" });
                return false;
            }
            if (res && res.error) {
                this.notification.add(res.error, { type: "danger" });
                return false;
            }
            const lines = (res && res.lines) || [];
            const selKey = this.state.selected
                && this.state.selected.layout_key;
            this.state.items = lines.map(line => this._lineToItem(line));
            this.state.selected = selKey
                ? (this.state.items.find(i => i.layout_key === selKey)
                   || null)
                : null;
            if (res && res.room) {
                this.state.room = { ...this.state.room, ...res.room };
            }
            this._recomputeLayoutFromItems();
            return true;
        } catch (e) {
            console.warn(
                "[SouthbrookKitchenConfigurator] rearrange failed:", e);
            this.notification.add(
                "Room resize failed — nothing was changed.",
                { type: "danger" });
            return false;
        }
    }

    _usesEngineResize() {
        return !!(this.state.designId && this._hydratedFromDesign);
    }

    async _changeRoom(field, raw) {
        const v = parseFloat(raw);
        if (!Number.isFinite(v)) return;
        this._markUserAction();
        const min = { width_in: 12, depth_in: 12, height_in: 84 };
        this.state.room[field] = Math.max(min[field] || 12, v);
        if (this._usesEngineResize()) {
            await this._rearrangeOnServer();   // persists server-side; no auto-save needed
            return;
        }
        await this._refreshLayout({ force: true });   // room resize regenerates, saved designs included
        this._queueAutoSave();    // D5
    }

    async _stretchWidth(delta) {
        this._markUserAction();
        this.state.room.width_in = Math.max(12, this.state.room.width_in + delta);
        if (this._usesEngineResize()) {
            await this._rearrangeOnServer();
            return;
        }
        await this._refreshLayout({ force: true });   // room resize regenerates, saved designs included
        this._queueAutoSave();    // D5
    }

    // D7 — Change the filler placement strategy + re-emit the layout.
    //
    // F12 fix (2026-07-27) — once hydrated from a saved design,
    // `_refreshLayout()` below is a guaranteed no-op for this control
    // (see its own early-return comment): it never regenerates the
    // filler placement. Before this fix the handler still mutated
    // `state.fillerStrategy` and queued an auto-save regardless — the
    // control looked live (the select's value changed, the "Auto-saved"
    // pill lit up) while doing nothing to the actual layout. Early-return
    // BEFORE any state write or auto-save so a no-op edit never claims to
    // have saved. The <select> is also disabled in the template for the
    // same reason (belt + suspenders: a stale bundle or a direct call
    // still can't lie).
    async _changeFillerStrategy(value) {
        if (this._hydratedFromDesign) return;
        const allowed = ["split", "left", "right", "scribe"];
        const v = (value || "").toLowerCase();
        if (!allowed.includes(v)) return;
        this._markUserAction();   // T7 gate
        this.state.fillerStrategy = v;
        await this._refreshLayout();
        this._queueAutoSave();
    }

    // D8 — Wall-cab top alignment selector. Re-runs layout so the
    // wall cabinets snap to the chosen mode immediately.
    //
    // F12 fix (2026-07-27) — same rationale as _changeFillerStrategy
    // above: dead post-hydration, so guarded the same way.
    async _changeWallAlignment(value) {
        if (this._hydratedFromDesign) return;
        const allowed = ["fixed_gap", "to_ceiling", "to_soffit"];
        const v = (value || "").toLowerCase();
        if (!allowed.includes(v)) return;
        this._markUserAction();   // T7 gate
        this.state.wallCabTopAlignment = v;
        await this._refreshLayout();
        this._queueAutoSave();
    }

    // F12 fix (2026-07-27) — soffit height only ever feeds the
    // to_soffit wall-cab-top alignment mode, which _refreshLayout()
    // stops regenerating post-hydration exactly like the two controls
    // above; guarded the same way for the same reason.
    async _changeSoffit(rawValue) {
        if (this._hydratedFromDesign) return;
        const v = parseFloat(rawValue);
        if (!Number.isFinite(v) || v < 36 || v > 144) return;
        this._markUserAction();   // T7 gate
        this.state.soffitHeightIn = v;
        // Only re-emit if the alignment actually consults soffit.
        if (this.state.wallCabTopAlignment === "to_soffit") {
            await this._refreshLayout();
        }
        this._queueAutoSave();
    }

    // D7 — Apply the server's snap hint: round room width to the
    // nearest module-clean value to eliminate the filler.
    async _applySnapHint() {
        const hint = this.state.summary && this.state.summary.snap_hint;
        if (!hint || !hint.target_width) return;
        this._markUserAction();   // T7 gate
        this.state.room.width_in = hint.target_width;
        await this._refreshLayout();
        this._queueAutoSave();
    }

    _selectCabinet(item) {
        // 24e — parent no longer owns cabObjs. <KitchenCanvas>
        // observes state.selected via onWillUpdateProps and calls
        // highlightSelected against its own scene.
        this.state.selected = item;
        // F14 — a real layout-cabinet selection supersedes any inventory
        // row catalog-preview highlight.
        this.state.catalogPreview = null;
    }

    // F14 — inventory row click is catalog BROWSING, not layout
    // SELECTION. Previously the row's click handler called
    // `_selectCabinet(product)` directly with the raw catalog product
    // (no layout_key) — `state.selected` became a dict the "Selected
    // Cabinet" edit drawer rendered as if it were a placed item, but
    // every drawer action (`_removeSelectedCabinet` /
    // `_updateSelectedWidth` / `_swapSelectedProduct`) resolves via
    // `items.findIndex(it => it.layout_key === sel.layout_key)`, which
    // is always -1 for a catalog product — a silent no-op the rep had
    // no way to know failed. `catalogPreview` is a separate,
    // drawer-inert field used only to highlight the row; it never opens
    // the drawer or feeds an edit action (see the template's row
    // is-selected class and the `t-if` gate on the drawer itself).
    _previewCatalogProduct(product) {
        this.state.catalogPreview = product;
    }

    // ─── D13 — Searchable inventory + drag-and-drop add ─────────────────────────
    // Category tabs first, then case-insensitive text search across
    // name / SKU / cabinet type / material / door-style. Both empty →
    // full catalog. INVENTORY_CATEGORY_MAP maps each tab key to the
    // cabinet_type values it should include; keeping the map next to
    // the filter keeps the tab UI and the data contract in one place.
    // F1 fix (2026-07-27) — "tall" used to also swallow "corner", so a
    // corner unit had no way to surface under its own filter — it only
    // ever showed up buried inside the Tall tab (mislabeled, uncounted
    // as its own category). Corner now gets its own key/tab.
    _inventoryCategoryTypes(key) {
        return ({
            all:      null,   // null = passthrough (no cabinet_type filter)
            base:     ["base"],
            wall:     ["wall"],
            tall:     ["tall"],
            corner:   ["corner"],
            panels:   ["filler", "panel"],
        })[key] || null;
    }

    _productsByCategory(key) {
        const types = this._inventoryCategoryTypes(key);
        const list = this.state.products || [];
        if (!types) return list;
        return list.filter(p => types.includes(p.cabinet_type));
    }

    _filteredProducts() {
        const q = (this.state.inventorySearch || "").toLowerCase().trim();
        const list = this._productsByCategory(this.state.inventoryCategory || "all");
        if (!q) return list;
        return list.filter(p =>
            (p.name && p.name.toLowerCase().includes(q)) ||
            (p.sku && p.sku.toLowerCase().includes(q)) ||
            (p.cabinet_type && p.cabinet_type.toLowerCase().includes(q)) ||
            (p.material && p.material.toLowerCase().includes(q)) ||
            (p.door_style && p.door_style.toLowerCase().includes(q))
        );
    }

    // Tab count badges — only compute against the raw catalog so a
    // typed search doesn't shrink the visible tab counts.
    _categoryCount(key) {
        return this._productsByCategory(key).length;
    }

    _setInventoryCategory(key) {
        this.state.inventoryCategory = key;
    }

    _onSearchInput(ev) {
        this.state.inventorySearch = ev.target.value || "";
    }

    _clearSearch() {
        this.state.inventorySearch = "";
    }

    // Drag-from-inventory: stash the product_id in dataTransfer so the
    // canvas drop handler can resolve it. text/plain mirror for older
    // browsers + Safari quirks. D16 — also stash the full product on
    // component state so the lane-highlight code knows which type is
    // mid-drag (dataTransfer.getData isn't readable during dragover).
    _onProductDragStart(ev, product) {
        if (!ev.dataTransfer) return;
        ev.dataTransfer.effectAllowed = "copy";
        ev.dataTransfer.setData(
            "application/x-sbk-product",
            JSON.stringify({ product_id: product.product_id })
        );
        ev.dataTransfer.setData("text/plain", String(product.product_id));
        this.state.draggedProduct = product;
    }

    // D16 — Clear the dragged-product state when the gesture ends
    // (drop succeeded OR user released outside any drop target).
    _onProductDragEnd(_ev) {
        this.state.draggedProduct = null;
        this.state.dragHover      = false;
        this._canvasApi?.clearDragGhost?.();   // T7 ghost
    }

    // Allow drop on the canvas wrapper. preventDefault is mandatory or
    // browsers default to "no-drop".
    _onCanvasDragOver(ev) {
        if (!ev.dataTransfer) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "copy";
        if (!this.state.dragHover) {
            this.state.dragHover = true;
        }
        // T7 — ghost preview: translucent stand-in at the hovered
        // wall's run end. Visual affordance only — the drop still goes
        // through the existing server-side placement.
        this._canvasApi?.updateDragGhost?.(ev);
    }

    _onCanvasDragLeave(ev) {
        // Only clear when the cursor truly leaves the canvas wrapper
        // (children fire dragleave when crossing internal elements).
        if (ev.currentTarget && ev.currentTarget.contains(ev.relatedTarget)) return;
        this.state.dragHover = false;
        this._canvasApi?.clearDragGhost?.();   // T7 ghost
    }

    _onCanvasDrop(ev) {
        ev.preventDefault();
        this.state.dragHover     = false;
        this.state.draggedProduct = null;
        this._canvasApi?.clearDragGhost?.();   // T7 ghost
        let pid = null;
        try {
            const raw = ev.dataTransfer.getData("application/x-sbk-product");
            if (raw) pid = JSON.parse(raw).product_id;
            else     pid = parseInt(ev.dataTransfer.getData("text/plain"), 10);
        } catch (_) {
            return;
        }
        if (!Number.isFinite(pid) || pid <= 0) return;
        const product = (this.state.products || []).find(p => p.product_id === pid);
        if (!product) return;
        // D14 — raycast the drop point to the floor plane so the new
        // cabinet lands where the user dropped it (not always at the
        // end of the run). Falls back to end-of-run if the ray misses
        // the floor (e.g. dropping in empty sky area of perspective view).
        const dropX = this._computeDropX(ev);
        // PR4b — which wall did the pointer drop onto? Determined by the
        // canvas raycast (userData.wall on the room-wall meshes), NOT by
        // any client-side math here. null when the drop missed every wall
        // (open floor / sky) — then _addCabinetFromProduct falls back to
        // the previously-picked activeWall, exactly as before PR4b.
        const dropWall = this._computeDropWall(ev);
        this._addCabinetFromProduct(product, dropX, dropWall);
    }

    // D14 — Cast a ray from the active camera through the drop point
    // and intersect the floor plane (Y=0) in world space, then convert
    // back to inches and snap to the 6" grid. Returns null when the
    // ray doesn't hit the floor (e.g. the cursor was over the sky in
    // a perspective view tilted upward).
    // D14 — floor-plane raycast for HTML5 drops onto the canvas.
    // 24e — delegates to <KitchenCanvas>'s imperative API which owns
    // the camera + raycaster. Returns null when the ray misses the
    // floor OR the api isn't attached yet (mount race).
    _computeDropX(ev) {
        return this._canvasApi?.computeDropX(ev) ?? null;
    }

    // PR4b — delegates to <KitchenCanvas>'s imperative API (which owns
    // the camera + raycaster + tagged wall meshes). Returns the wall the
    // HTML5 drop landed on, or null when the ray misses (or the api
    // isn't attached yet). Sibling to _computeDropX; adds no wall math.
    _computeDropWall(ev) {
        return this._canvasApi?.computeDropWall(ev) ?? null;
    }

    // Generic add — used by drop AND by a future "click to add" button.
    // D14 — `targetX` (inches) sets the new item's sort-key so it
    // slots in at the dropped position. _recomputeLayoutFromItems
    // then sorts-by-x and re-packs contiguously: visual semantic is
    // "where in the run order does this go". `null` = append at end.
    _addCabinetFromProduct(product, targetX = null, wallOverride = null) {
        this._markUserAction();   // T7 gate
        const type = product.cabinet_type || "base";
        // PR3.0 — y/z field-semantics migration: y_position_in is the
        // canonical mount-height/elevation field, z_position_in is the
        // depth axis per COORDINATE_CONTRACT.md. Default mount height:
        // walls track the configured wall Y; everything else sits on
        // the floor (y=0). z stays 0 for every new item (flush to the
        // back wall) — this was the "z-as-height" convention flagged as
        // the riskiest finding in the PR3 coordinate-contract matrix.
        let mountY = 0;
        if (type === "wall") {
            // Use the most recent y_position_in we saw from the server
            // so the wall cab tracks D8's alignment mode without
            // re-running /layout.
            const walls = (this.state.items || []).filter(it => it.cabinet_type === "wall");
            mountY = (walls.length && walls[0].y_position_in) || (34.5 + 1.5 + 18.0);
        }
        // Unique layout_key (timestamp + random tail = collision-free).
        const layoutKey = `${type}-add-${Date.now()}-${Math.floor((performance.now() % 1) * 10000)}`;
        // D14 — when a targetX is supplied, use it as the cascade
        // sort-key so the new cabinet lands at that ordered position.
        // No-target case retains the 1e6 sentinel so the cabinet
        // appends at the end of its group.
        let sortX = (typeof targetX === "number" && !Number.isNaN(targetX))
                    ? targetX : 1e6;

        // Panel-placement rule (2026-07-01): end cap decorative panels
        // (cabinet_type === "panel") must snap to the exposed side face
        // of an existing cabinet, NEVER land at the wall edge that the
        // tails-loop sentinel would place them at. Default: right side
        // of the rightmost base cabinet (fallback to wall cabinet, then
        // 0 if no cabinets yet).
        if (type === "panel"
                && (typeof targetX !== "number" || Number.isNaN(targetX))) {
            const hosts = (this.state.items || []).filter(
                it => it.cabinet_type === "base"
                   || it.cabinet_type === "wall");
            if (hosts.length) {
                const sorted = hosts.slice().sort(
                    (a, b) => (a.x_position_in || 0) - (b.x_position_in || 0));
                const last = sorted[sorted.length - 1];
                sortX = (last.x_position_in || 0) + (last.width_in || 0);
            } else {
                sortX = 0;   // no host cabinet yet — start at left origin
            }
            // Base end caps sit on the floor; wall end caps track wall
            // mount height (y), not depth (z).
        }
        const newItem = {
            ...product,
            layout_key:    layoutKey,
            x_position_in: sortX,
            y_position_in: mountY,
            z_position_in: 0,
            width_in:      product.width_in || 24,
            height_in:     product.height_in || (type === "wall" ? 30 : 34.5),
            depth_in:      product.depth_in  || (type === "wall" ? 12 : 24),
            // The wall this cabinet belongs to. Persisted (save_design /
            // load_design_lines) and — since PR4 — routed through the
            // pure kitchen_layout_engine server-side (_place_lines_on_wall)
            // so a non-back wall gets its canonical engine pose applied
            // back onto state.items immediately (_applyServerPlacements).
            // Precedence (F7 fix, 2026-07-27 — was PR4b's wallOverride ||
            // activeWall || BACK): a DELIBERATELY selected Active Wall
            // now always wins over a drop-raycast override; the ray only
            // decides the wall when nothing is actively selected. PR4b's
            // original ordering let an accidental ray (see room_shell.esm
            // .js's low kick-strip walls for right/front — F7) silently
            // beat an explicit click-to-select, overriding a wall the rep
            // deliberately picked. The drop path (_onCanvasDrop) and the
            // select-then-add path still converge on the SAME persisted
            // record whenever they agree on a wall — locked by
            // tests/node_js/contracts/09_drag_to_wall_convergence.test.mjs.
            wall:          this.state.activeWall || wallOverride || WALLS.BACK,
        };
        this.state.items = [...(this.state.items || []), newItem];
        this.state.selected = newItem;
        this._recomputeLayoutFromItems();
        // T7 — NO silent room growth (the spec's "54->60->84" defect):
        // an overflowing run gets an honest warning, never a mutated
        // room. The server-side validators flag the overflow too.
        const required = this._currentRunWidthIn();
        if (required > this.state.room.width_in) {
            this.notification.add(
                `Run needs ${Math.ceil(required)}″ — room is ` +
                `${this.state.room.width_in}″. Remove a cabinet or ` +
                `widen the room.`,
                { type: "warning" }
            );
        }
        // D14 — Drop UX feedback: tell the rep WHERE it landed when
        // they used drop-positioning (vs the bare "Added X" toast).
        //
        // F6 fix (2026-07-27, partial — honest-toast only): the position
        // claim below is only ever true for the BACK wall — _computeDropX
        // is an X-only floor raycast, and a non-back-wall cabinet's
        // along-wall position is decided entirely server-side by the
        // pure kitchen_layout_engine's run-order placement
        // (_place_lines_on_wall), which hasn't run yet at add-time. Before
        // this fix, dropping onto e.g. the left wall still toasted
        // "Added ... at 0 in" — a claim about an X coordinate that has
        // nothing to do with where the cabinet actually lands (the
        // engine places it along Z, by run order). Deferred (out of
        // scope here): true positional side-wall drops — the engine
        // only supports append-by-run-order today, so a non-back wall
        // always gets a wall-only toast, never a position.
        if (newItem.wall !== WALLS.BACK) {
            this.notification.add(
                `Added ${product.name} to ${this._wallLabel(newItem.wall)} wall`,
                { type: "success" }
            );
        } else if (typeof targetX === "number" && !Number.isNaN(targetX)) {
            const finalX = newItem.x_position_in;
            this.notification.add(
                `Added ${product.name} at ${finalX}″`,
                { type: "success" }
            );
        } else {
            this.notification.add(`Added ${product.name}`, { type: "success" });
        }
        // C2 — a cabinet landing on a non-back wall (or added to a
        // design that already spans 2+ walls) may complete an inside
        // corner. save_design only runs the corner-resolution engine
        // (design.action_auto_arrange) when 2+ walls are in play, so an
        // extra immediate save here when that's not yet the case is
        // harmless — but waiting out the full 3s debounce for the case
        // where it DOES apply is exactly the "corner never appears"
        // symptom this fixes. Skips the debounce entirely (an immediate
        // save already persists everything the debounce would have).
        // Guarded on designId the same way every other side-channel
        // write in this file is (_savePinnedPosition, _removeSelectedCabinet)
        // — nothing server-side to save into yet for a brand-new,
        // never-saved design.
        const designAlreadyMultiWall = (this.state.items || []).some(
            it => ((it.wall || "back") !== "back")
                || it.cabinet_type === "corner"
        );
        if (this.state.designId
                && (newItem.wall !== WALLS.BACK || designAlreadyMultiWall)) {
            this._saveDesign();
        } else {
            this._queueAutoSave();
        }
    }

    // Total inches consumed by the longest cabinet row (max of base
    // run / wall run / extras). Used to auto-extend room width on add.
    //
    // C5 — only BACK-wall items may drive this. room.width_in is the
    // back wall's length; a run added on the left/right/front wall has
    // its own axis (kitchen_layout_engine places it along Z, not the
    // back wall's X — see the PR4 comment on packRow below) and must
    // never inflate the back wall's room width. Before this fix, a
    // left-wall run's cabinet widths were summed in with the back
    // wall's and grew room.width_in to fit (measured: 120in -> 240in
    // for a single added left-wall run) — the C5 defect.
    _currentRunWidthIn() {
        const items = (this.state.items || []).filter(
            it => (it.wall || "back") === "back"
        );
        const by = {};
        for (const it of items) {
            const t = it.cabinet_type || "other";
            by[t] = (by[t] || 0) + (it.width_in || 0);
        }
        // Bases + extras pack on the floor row; walls pack on the upper row.
        const floorRow = (by.base || 0) + (by.filler || 0) + (by.tall || 0)
                       + (by.panel || 0) + (by.corner || 0) + (by.other || 0);
        const wallRow  = (by.wall || 0);
        return Math.max(floorRow, wallRow);
    }

    // ─── D2 — Inline cabinet edit drawer ────────────────────────────────────────
    // Filter the loaded product catalog by cabinet_type so the swap
    // dropdown only shows compatible substitutes (a wall cab can't
    // replace a base cab — different mount Z, different depth).
    _sameTypeProducts(cabinetType) {
        return (this.state.products || []).filter(
            p => p.cabinet_type === cabinetType
        );
    }

    // Width edit: validate, mutate item, cascade x positions, rebuild.
    _updateSelectedWidth(rawValue) {
        const v = parseFloat(rawValue);
        if (!Number.isFinite(v) || v < 6 || v > 48) return;
        const sel = this.state.selected;
        // F14 — defense in depth: the edit drawer must only ever bind to
        // a placed layout item (one with a layout_key), never a raw
        // catalog product. `_previewCatalogProduct` no longer writes
        // `state.selected` at all, so this should be unreachable in
        // practice — kept as a hard guard against any future caller
        // that writes `state.selected` without a layout_key.
        if (!sel || !sel.layout_key) return;
        const idx = this.state.items.findIndex(it => it.layout_key === sel.layout_key);
        if (idx < 0) return;
        this._markUserAction();   // T7 gate
        this.state.items[idx].width_in = v;
        sel.width_in = v;
        this._recomputeLayoutFromItems();
        this._queueAutoSave();    // D5
    }

    // Product swap: replace cabinet at the same position with the
    // new product's payload, keeping position + layout_key + dimensions.
    _swapSelectedProduct(rawProductId) {
        const pid = parseInt(rawProductId, 10);
        if (!Number.isFinite(pid)) return;
        const product = (this.state.products || []).find(p => p.product_id === pid);
        if (!product) return;
        const sel = this.state.selected;
        // F14 — see the identical guard comment in _updateSelectedWidth.
        if (!sel || !sel.layout_key) return;
        const idx = this.state.items.findIndex(it => it.layout_key === sel.layout_key);
        if (idx < 0) return;
        this._markUserAction();   // T7 gate
        const oldItem = this.state.items[idx];
        // Preserve position + layout_key + current width (user may have
        // dialed in a custom width; swapping product shouldn't reset it).
        const merged = {
            ...product,
            layout_key:    oldItem.layout_key,
            x_position_in: oldItem.x_position_in,
            y_position_in: oldItem.y_position_in,
            z_position_in: oldItem.z_position_in,
            width_in:      oldItem.width_in,
        };
        this.state.items[idx] = merged;
        this.state.selected = merged;
        this._recomputeLayoutFromItems();
        this._queueAutoSave();    // D5
    }

    // Remove the selected cabinet entirely.
    _removeSelectedCabinet() {
        this._markUserAction();   // T7 gate
        const sel = this.state.selected;
        // F14 — see the identical guard comment in _updateSelectedWidth.
        if (!sel || !sel.layout_key) return;
        const idx = this.state.items.findIndex(it => it.layout_key === sel.layout_key);
        if (idx < 0) return;
        this.state.items.splice(idx, 1);
        // Pick a neighbour as the new selection (or null if empty).
        this.state.selected = this.state.items[idx] || this.state.items[idx - 1] || null;
        this._recomputeLayoutFromItems();
        // v19.0.4.24.0 P0#1 Stage 1 — sync delete server-side so
        // pinned-line rows don't resurrect on next load. Fire-and-
        // forget; the debounced /save is the fallback. Skipped when
        // designId isn't set yet (nothing server-side to delete).
        if (this.state.designId && sel.layout_key) {
            rpc("/southbrook_kitchen/configurator/delete_line", {
                design_id:  this.state.designId,
                layout_key: sel.layout_key,
            }).catch(() => { /* non-fatal — auto-save reconciles */ });
        }
        this._queueAutoSave();    // D5
    }

    // Cascade x positions inside each cabinet group.
    //  - Bases pack left-to-right contiguously on the floor row.
    //  - Walls track their own left-to-right order on the upper row.
    //  - Tall / corner / filler / panel pack at the right end of the
    //    base run (so a drag-dropped tall cab lands after the bases).
    //  - Summary price excludes fillers per the existing convention.
    _recomputeLayoutFromItems() {
        // Panel-placement rule (Southbrook domain, 2026-07-01):
        //   Panels come in TWO shapes, both of which must NEVER attach to a
        //   room wall.
        //   1. Filler ("filler")  — plain spacers, sit between cabinets, may
        //      touch a wall only as a consequence of filling the gap between
        //      the last cabinet and the wall. Server /layout drives their X.
        //   2. End cap ("panel")  — decorative panels that finish the exposed
        //      left/right SIDE FACE of a base or wall cabinet. Their X is
        //      relative to the cabinet they cap and MUST NOT be overwritten
        //      by the tails-loop that packs tall/corner items after the base
        //      run (packing there placed them against the room wall — the
        //      visual bug this fix corrects).
        const items = this.state.items || [];
        const sortX = (a, b) => (a.x_position_in || 0) - (b.x_position_in || 0);

        const bases   = items.filter(it => it.cabinet_type === "base").sort(sortX);
        const walls   = items.filter(it => it.cabinet_type === "wall").sort(sortX);
        // Only tall + corner items pack after the base run; filler + panel
        // carry their own X and must not be repositioned here.
        const tailItems = items.filter(it =>
            ["tall", "corner"].includes(it.cabinet_type)
        ).sort(sortX);

        // v19.0.4.24.0 P0#1 Stage 1 — Pin-aware row packer. Pinned
        // items keep their stored x_position_in; unpinned items fit
        // into the free gaps between pinned ones (left-to-right),
        // and any overflow extends past the rightmost pinned. When
        // no item in a row is pinned, behaviour is identical to the
        // pre-4.23 cascade (pack from x=0), so existing designs
        // render exactly as before until the user drags a cabinet.
        // Rec D · Sprint 2d step 6 — the packRow closure moved to
        // canvas/pack_row.esm.js as a pure exported function; the
        // three call sites keep the same signature so scene-diff
        // rebuild timings are unchanged.
        //
        // PR4 (2026-07-12) — packRow is a legacy SINGLE-AXIS (X)
        // packer; it is only valid for the back wall, where "along
        // the run" is X. A non-back-wall cabinet's pose is decided
        // server-side by the pure kitchen_layout_engine (its along-
        // wall axis is Z for left/right — see
        // docs/2026-07-12-pr3-coordinate-contract-matrix.md §4) and
        // returned via save_design's `placed` list (applied in
        // _saveDesign below), or hydrated verbatim from a saved
        // design. The client must never repack that geometry back
        // onto the X axis (Renderer Contract: the client does not
        // decide wall-item geometry) — so only the back-wall subset
        // of each bucket is ever handed to packRow. Counts/price
        // above still see every item regardless of wall.
        const isBackWall = (it) => (it.wall || "back") === "back";
        // C2/C5 — server-authoritative geometry mode. Once a design
        // spans 2+ walls (or already carries a corner cabinet), the
        // whole layout's geometry belongs to the server-side
        // kitchen_layout_engine (via design.action_auto_arrange /
        // _place_lines_on_wall) — it reserves the 36" corner cell and
        // re-flows both runs. Repacking locally from x=0 here would
        // shove the back-wall cabinets straight into that reserved
        // corner cell, undoing what the server just computed. A
        // single-(back)-wall design has no server involvement in its
        // geometry, so it keeps the legacy local packing untouched.
        const multiWall = (this.state.items || []).some(
            it => ((it.wall || "back") !== "back")
                || it.cabinet_type === "corner"
        );
        if (!multiWall) {
            packRow(bases.filter(isBackWall));
            packRow(walls.filter(isBackWall));
            packRow(tailItems.filter(isBackWall));
        }
        // NOTE: filler items get X from server /layout; end-cap panels get X
        //       relative to their host cabinet (set by _addCabinetFromProduct
        //       or downstream). Both are left untouched here on purpose.

        let price = 0;
        let fillerPrice = 0;
        for (const it of items) {
            if (it.cabinet_type === "filler") {
                fillerPrice += (it.price || 0);
            } else {
                price += (it.price || 0);
            }
        }
        // F13 fix (2026-07-27) — every key here is recomputed FRESH from
        // the CURRENT state.items on every call; nothing is spread
        // forward from the previous state.summary. The old code did
        // `{ ...(this.state.summary || {}), base_count, wall_count,
        // total, price }` — every other key (cabinet_price, filler_price,
        // remainder_in, filler_strategy, snap_hint) survived untouched
        // from whichever /layout response (or even older local
        // recompute) happened to leave it there. Deleting a filler,
        // swapping a product, or resizing a cabinet left the sidebar
        // showing a stale filler price/width and a stale remainder — the
        // F13 defect.
        //   - cabinet_price mirrors `price` (same definition server-side:
        //     controllers/main.py sums non-filler item prices for both).
        //   - filler_price is recomputable locally as the sum of current
        //     filler items' prices (unchanged by Task 10).
        //   - filler_strategy isn't read from state.summary anywhere in
        //     the template (state.fillerStrategy is used directly) and
        //     snap_hint is NOT recomputable client-side (it needs the
        //     base product's module width, southbrook_width_in, which
        //     state.items doesn't carry) — both are intentionally
        //     DROPPED rather than spread forward stale. A fresh /layout
        //     round-trip (a room-dimension edit on a not-yet-hydrated
        //     session) repopulates snap_hint when it's next valid.
        //
        // Task 10 (SBK-023) — remainder_in used to be redefined here as
        // `fillerWidthIn`, the sum of CURRENTLY SURVIVING filler items'
        // widths. That understated the true gap the moment a user
        // deleted one filler line (e.g. one half of a "split" pair) —
        // deleting a filler doesn't repack the survivors to close the
        // gap (see the "left untouched here on purpose" note above), so
        // the room's real physical gap is unchanged, but the old formula
        // reported only half of it. remainder_in must instead be the
        // HONEST geometric identity — run length minus occupied length —
        // matching controllers/main.py's `rw - n*module_w` for the same
        // run. Occupied length is base-cabinet footprint PLUS any corner
        // cell's footprint on that same wall: a corner cabinet reserves
        // real run length too (kitchen_design.py folds its footprint
        // into wall_start_offsets server-side), so it belongs in
        // "occupied", not in the leftover gap. Scoped to the back wall —
        // the only run this scalar has ever described (its origin is the
        // single-wall /layout controller, and packRow above is likewise
        // back-wall-only); a left/right wall's run length is room.depth_in,
        // a dimension this one field was never meant to carry. No
        // cabinets at all (n==0, mirroring the server's own n>0 gate) ->
        // no honest "remainder" to report either, same as a fresh empty
        // room never gets a phantom filler.
        const hasCabinets = items.some(it => it.cabinet_type !== "filler");
        const backRunOccupiedIn = items
            .filter(it => (it.wall || "back") === "back"
                && (it.cabinet_type === "base" || it.cabinet_type === "corner"))
            .reduce((sum, it) => sum + (it.width_in || 0), 0);
        const roomWidthIn = (this.state.room && this.state.room.width_in) || 0;
        const remainderIn = hasCabinets
            ? Math.max(0, roomWidthIn - backRunOccupiedIn)
            : 0;

        this.state.summary = {
            base_count:    bases.length,
            wall_count:    walls.length,
            total:         bases.length + walls.length,
            price:         price,
            cabinet_price: price,
            filler_price:  fillerPrice,
            remainder_in:  remainderIn,
        };

        // Runtime validation — surface a warning if any end-cap panel would
        // end up flush against or beyond a room wall. End caps must attach
        // ONLY to the left/right side face of a base or wall cabinet; a
        // panel whose X pushes it past the room boundary means the anchor
        // logic upstream lost track of its host cabinet.
        this._validateEndCapPlacement(
            items.filter(it => it.cabinet_type === "panel"),
        );
        // 24c — force a new array reference so <KitchenCanvas>'s
        // onWillUpdateProps rebuild path fires. packRow mutates
        // entries in place, which OWL cannot observe as a prop change.
        this.state.items = this.state.items.slice();
    }

    _validateEndCapPlacement(endCapItems) {
        const rw = (this.state.room && this.state.room.width_in) || 0;
        const warnings = [];
        for (const item of endCapItems) {
            const x = item.x_position_in || 0;
            const w = item.width_in || 0;
            const rightEdge = x + w;
            if (x < 0 || (rw && rightEdge > rw + 0.01)) {
                console.warn(
                    "[SBK] End cap panel " +
                    (item.product_name || item.layout_key || item.id) +
                    " is positioned at x=" + x + "\" (width " + w + "\"), " +
                    "which places it against or beyond the room wall. " +
                    "End cap panels must attach ONLY to the left or right " +
                    "side face of a base or wall cabinet — never to a room wall."
                );
                warnings.push({
                    code:     "PANEL_WALL_ATTACH",
                    severity: "error",
                    message:  "End cap panel \"" +
                              (item.product_name || item.layout_key || "") +
                              "\" cannot be placed against a wall. Attach " +
                              "it to the side of a cabinet instead.",
                });
            }
        }
        if (warnings.length) {
            const existing = (this.state.warnings || []).filter(
                w => w.code !== "PANEL_WALL_ATTACH");
            this.state.warnings = existing.concat(warnings);
        } else if (this.state.warnings) {
            // Clear stale PANEL_WALL_ATTACH warnings once fixed.
            this.state.warnings = this.state.warnings.filter(
                w => w.code !== "PANEL_WALL_ATTACH");
        }
    }

    // ─── Camera view (state-only wrapper) ────────────────────────────────────
    // Rec D · Sprint 2d Step 24e — view-swap orchestration moved to
    // <KitchenCanvas>. Parent writes state.view so the child re-runs
    // its own _setView via onWillUpdateProps AND fires the imperative
    // api.setView(key) so a "reset" click at the current view still
    // re-lerps the camera (preserving the pre-24e Reset button UX).
    // The child's _setView is idempotent under _currentView === key,
    // so the two paths never race.
    _setView(key) {
        this.state.view = key;
        if (this._canvasApi) this._canvasApi.setView(key);
    }

    _onKeyDown(e) {
        // Ignore when typing in form inputs.
        const tag = (e.target && e.target.tagName) || "";
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
            e.target?.isContentEditable) return;
        const k = e.key;
        const ctrl = e.metaKey || e.ctrlKey;

        // D6 — Cmd/Ctrl+S manual save (debounced auto-save also covers
        // this, but power users expect the muscle-memory shortcut).
        if (ctrl && (k === "s" || k === "S")) {
            e.preventDefault();
            this._saveDesign();
            return;
        }

        const map = { "1": "iso", "2": "top", "3": "front",
                       "4": "left", "5": "right", "6": "persp" };
        if (!ctrl && map[k]) { e.preventDefault(); this._setView(map[k]); return; }
        if (!ctrl && (k === "r" || k === "R")) { e.preventDefault(); this._setView("iso"); return; }
        if (!ctrl && (k === "+" || k === "=")) {
            e.preventDefault();
            // 24e — vsScale + frustum are child-owned now.
            this._canvasApi?.zoomBy(0.9);
            return;
        }
        if (!ctrl && (k === "-" || k === "_")) {
            e.preventDefault();
            this._canvasApi?.zoomBy(1.1);
            return;
        }

        // D6 — Arrow keys cycle selection through cabinets in x-position
        // order (bases first, then walls). Skips fillers since they're
        // not user-editable.
        if (!ctrl && (k === "ArrowLeft" || k === "ArrowRight")) {
            e.preventDefault();
            this._cycleSelection(k === "ArrowRight" ? 1 : -1);
            return;
        }

        // v19.0.4.25.0 P0#1 Stage 3 — Q rotates selected cabinet 90°
        // counter-clockwise, E rotates 90° clockwise. Both flip
        // pinned=true so the pack cascade leaves the cabinet in place.
        // Y-axis only (kitchen cabinets don't tumble). Persistence
        // happens via _savePinnedPosition. Visual rotation of the
        // mesh is deferred; the rotation_deg is stored + toasted so
        // reps can reason about door direction even before Stage 3.5
        // ships the visual layer.
        //
        // Q/E chosen (not R) to avoid clobbering the existing R
        // "reset view to iso" muscle memory.
        if (!ctrl && (k === "q" || k === "Q" || k === "e" || k === "E")) {
            e.preventDefault();
            const delta = (k === "e" || k === "E") ? 90 : -90;
            this._rotateSelected(delta);
            return;
        }

        // D6 — Esc clears the cabinet selection.
        if (k === "Escape") {
            if (this.state.selected) {
                // 24e — child observes state.selected and clears its
                // own highlight via onWillUpdateProps.
                this.state.selected = null;
                e.preventDefault();
            }
            return;
        }

        // Delete / Backspace — remove the selected item (any type: base,
        // wall, tall, corner, filler, panel) from the layout AND the
        // quote. _removeSelectedCabinet already splices the item out of
        // state.items and calls _queueAutoSave which persists the design
        // to the server (drives southbrook.kitchen.design.line records —
        // the quote source). Standard 3D-editor UX (Blender / SketchUp /
        // Fusion keybinding).
        //
        // Guarded against firing when the user is typing in a text
        // input, textarea, or contentEditable region so we don't hijack
        // Backspace inside inline edits (width entry, notes, room name,
        // inventory search).
        if (k === "Delete" || k === "Backspace") {
            const t = e.target;
            const inEditable = t && (
                t.tagName === "INPUT"
                || t.tagName === "TEXTAREA"
                || t.tagName === "SELECT"
                || t.isContentEditable
            );
            if (inEditable) return;
            // F14 — same guard as the drawer actions: Delete/Backspace
            // only ever removes a real placed layout item.
            if (!this.state.selected || !this.state.selected.layout_key) return;
            const doomed = this.state.selected;
            const doomedName = doomed.product_name || doomed.name
                || doomed.layout_key || "item";
            const doomedType = doomed.cabinet_type || "item";
            e.preventDefault();
            this._removeSelectedCabinet();
            if (this.notification && this.notification.add) {
                this.notification.add(
                    "Removed " + doomedName + " (" + doomedType +
                    ") from the layout and quote.",
                    { type: "info" }
                );
            }
            return;
        }
    }

    // D6 — cycle selection through selectable items (bases then walls,
    // each sorted left-to-right). +1 = next, -1 = previous; wraps.
    _cycleSelection(dir) {
        const items = this.state.items || [];
        const selectable = items
            .filter(it => it.cabinet_type === "base" || it.cabinet_type === "wall")
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
                it => it.layout_key === this.state.selected.layout_key
            );
        }
        idx = (idx + dir + selectable.length) % selectable.length;
        if (idx < 0) idx += selectable.length;
        this._selectCabinet(selectable[idx]);
    }

    // v19.0.4.25.0 P0#1 Stage 3 — Rotate the selected cabinet by
    // deltaDeg (±90) and persist. Sets item.rotation_deg (mod 360),
    // flips pinned=true so the pack cascade leaves the cabinet
    // where the user put it, and fires _savePinnedPosition to sync
    // the new rotation to the DB. Fillers + panels don't rotate
    // (their orientation is host-relative).
    //
    // Visual rotation of the mesh is DEFERRED to a follow-up patch —
    // rotation_deg is stored and displayed via toast so the sales
    // rep can reason about door direction, but the 3D scene keeps
    // meshes at their un-rotated pose. Wrapping cabinet meshes in
    // THREE.Groups is what broke the WebClient at 4.20.0; that
    // refactor is staged separately (see audit §P0#1 recovery plan).
    _rotateSelected(deltaDeg) {
        const item = this.state.selected;
        if (!item) return;
        if (item.cabinet_type === "filler" || item.cabinet_type === "panel") {
            return;
        }
        const cur = ((item.rotation_deg || 0) + deltaDeg) % 360;
        item.rotation_deg = (cur + 360) % 360;
        item.pinned = true;
        this._savePinnedPosition(item);
        if (this.notification && this.notification.add) {
            this.notification.add(
                `Rotated ${item.product_name || item.name || "cabinet"} to ${item.rotation_deg}°`,
                { type: "success" }
            );
        }
        this._queueAutoSave();
    }

    // ─── Save ─────────────────────────────────────────────────────────────────────

    // PR4.1 (2026-07-12) — shared by both save paths (_saveDesign and
    // _autoSave). Applies the server's `placed` array (engine-computed
    // poses for non-back-wall cabinets, from
    // southbrook.kitchen.design._place_lines_on_wall) onto the matching
    // state.items by layout_key, then forces a new items array
    // reference via _recomputeLayoutFromItems() so <KitchenCanvas>'s
    // prop-change detection re-renders. This ONLY copies fields the
    // server already computed; per the Renderer Contract, the client
    // never derives a wall-item coordinate itself. Originally inlined
    // in _saveDesign only (PR4) — a drag-add goes through the debounced
    // _queueAutoSave() -> _autoSave() path instead, which never applied
    // `placed`, so a cabinet dropped on a non-back wall stayed drawn on
    // the back wall until reload. Extracted here so both save paths
    // share one implementation and can't drift again (PR4.1).
    _applyServerPlacements(placed) {
        if (!Array.isArray(placed) || !placed.length) return;
        const placedByKey = new Map(placed.map(p => [p.layout_key, p]));
        for (const it of this.state.items) {
            const p = placedByKey.get(it.layout_key);
            if (!p) continue;
            it.x_position_in = p.x_position_in;
            it.y_position_in = p.y_position_in;
            it.z_position_in = p.z_position_in;
            it.rotation_deg  = p.rotation_deg;
        }
        this._recomputeLayoutFromItems();
    }

    // C2 — shared by both save paths (_saveDesign and _autoSave), same
    // reasoning as _applyServerPlacements above (PR4.1): a corner-
    // resolution pass can be triggered by either an explicit Save or a
    // debounced auto-save (e.g. dragging a cabinet onto a second wall),
    // so both must react identically or the two paths drift again.
    //
    // When save_design's corner-resolution pass (controllers/main.py
    // C2) ran — because the design now spans 2+ walls, or because it
    // cleaned up stale derived corner lines — `result.lines` carries
    // the authoritative post-save state (same shape as
    // load_design_lines). The server, not the client's pre-save guess,
    // now owns this design's geometry, so state.items is REPLACED
    // wholesale rather than patched. `_recomputeLayoutFromItems()`
    // recomputes counts/price/validation but — per its own multiWall
    // guard — will not repack: repacking from x=0 would shove the
    // back-wall cabinets straight into the corner cell the server just
    // reserved.
    //
    // `result.layout_warning` surfaces a plain-English reason the
    // corner engine couldn't resolve (the run doesn't physically fit
    // the wall) — the design itself is still saved, since
    // action_auto_arrange is savepoint-atomic and only rolls itself
    // back on failure.
    _applyRelaidResult(result) {
        if (result && result.relaid && result.lines) {
            const selectedKey = this.state.selected
                && this.state.selected.layout_key;
            const items = result.lines.map(line => this._lineToItem(line));
            this.state.items = items;
            const stillSelected = selectedKey
                && items.some(it => it.layout_key === selectedKey);
            if (!stillSelected) this.state.selected = null;
            this._recomputeLayoutFromItems();
        }
        if (result && result.layout_warning) {
            this.notification.add(result.layout_warning,
                { type: "warning", sticky: true });
        }
    }

    async _saveDesign() {
        // PR2.5a (Gap 2) — same rationale as the _queueAutoSave() guard:
        // a failed hydration means state doesn't reflect the saved
        // design, so a manual save (button or Cmd/Ctrl+S) must also be
        // refused rather than overwriting it.
        if (this._hydrationFailed) {
            this.notification.add(
                "Saving is disabled — the saved design failed to load. "
                + "Reload the page to try again.",
                { type: "danger", sticky: true }
            );
            return;
        }
        this.state.saving = true;
        try {
            const result = await rpc("/southbrook_kitchen/configurator/save", {
                name:       this.state.designName ||
                            `Kitchen ${this.state.room.width_in}"`,
                room:       this.state.room,
                items:      this.state.items,
                design_id:  this.state.designId || false,
                partner_id: this.state.partnerId || false,
            });
            this.state.designId       = result.id;
            this.state.designName     = result.name;
            this.state.lastAutoSaveAt = Date.now();
            // PR4 (2026-07-12) — the server may have re-placed any
            // non-back-wall cabinets via the pure kitchen_layout_engine
            // (southbrook.kitchen.design._place_lines_on_wall),
            // overwriting the client's back-wall-only x/y/z/rotation
            // guess with the engine's real wall pose. Apply those
            // poses onto the matching state.items so the cabinet
            // visibly jumps onto its wall immediately — no reload
            // needed. See _applyServerPlacements() for the shared
            // logic (also used by _autoSave() — PR4.1).
            this._applyServerPlacements(result.placed);
            // C2 — apply any corner-resolution relay + surface a
            // layout_warning, if the server sent either.
            this._applyRelaidResult(result);
            // Task #48 — quiet, non-sticky toast on the happy path.
            // Previous format ("Saved: ${name}") leaked implementation
            // detail into the message; the rep already sees the design
            // title in the header pill.
            this.notification.add("Design saved", { type: "success", sticky: false });
        } catch (e) {
            // Task #48 — actionable error when the server rejects for
            // missing partner. Server-side validation raises with a
            // message that includes "partner" or "customer"; sniff for
            // that so the CTA is only added on the exact case.
            const msg = e && (e.message || String(e)) || "Save failed";
            const missingPartner = !this.state.partnerId
                && !this.state.portalUser
                && /partner|customer/i.test(msg);
            if (missingPartner) {
                this.notification.add(
                    "Assign a customer via the picker (top-right) — " + msg,
                    { type: "warning", sticky: true }
                );
            } else {
                this.notification.add(msg, { type: "danger", sticky: true });
            }
        } finally {
            this.state.saving = false;
        }
    }

    // Task #48 — Create & Confirm. Runs the server-side action that
    // materializes a sale.order from the current design + partner,
    // toasts success with the SO name, and dispatches the returned
    // action so the rep lands on the SO form. Uses call_kw so ACLs
    // resolve the same way any backend button would.
    async _createAndConfirm() {
        // Ensure the design is persisted before we ask the server to
        // materialize a quotation from it; save-in-flight is fine —
        // _saveDesign is idempotent under `state.designId`.
        if (!this.state.designId) {
            await this._saveDesign();
        }
        if (!this.state.designId) {
            // Save failed — _saveDesign already surfaced the reason.
            return;
        }
        this.state.saving = true;
        try {
            const action = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design",
                method: "action_create_quotation",
                args:   [[this.state.designId]],
                kwargs: {},
            });
            // Server may return either an ir.actions.act_window dict
            // (open the SO form) or a bare {order_name, order_id} payload
            // for reps who just want the confirmation toast. Handle both.
            let orderName = "";
            let actionToDispatch = null;
            if (action && typeof action === "object") {
                orderName = action.order_name
                    || (action.context && action.context.default_name)
                    || "";
                if (action.type && action.type.startsWith("ir.actions.")) {
                    actionToDispatch = action;
                }
            }
            this.notification.add(
                orderName
                    ? `Quotation created (${orderName}) — check the Sale Order form`
                    : "Quotation created — check the Sale Order form",
                { type: "success", sticky: false }
            );
            if (actionToDispatch) {
                return this.action.doAction(actionToDispatch);
            }
        } catch (e) {
            const msg = e && (e.message || String(e)) || "Quotation failed";
            const missingPartner = !this.state.partnerId
                && !this.state.portalUser
                && /partner|customer/i.test(msg);
            if (missingPartner) {
                this.notification.add(
                    "Assign a customer via the picker (top-right) — " + msg,
                    { type: "warning", sticky: true }
                );
            } else {
                this.notification.add(msg, { type: "danger", sticky: true });
            }
        } finally {
            this.state.saving = false;
        }
    }

    // v19.0.4.24.0 P0#1 Stage 1 — Persist a single pinned drop
    // position. Fire-and-forget — silent on failure so the debounced
    // /save reconciles the whole design later. Skipped when designId
    // isn't set yet (unsaved design, nothing server-side to update).
    //
    // IMPORTANT: this must NEVER be awaited by onWillStart or any
    // lifecycle hook. It's a side-channel write; any lifecycle
    // dependency will silently hang the WebClient mount if the RPC
    // hits a routing miss (see post-mortem of 4.20.0 in
    // docs/southbrook_kitchen_audit_2026-07-01.md §P0#1).
    async _savePinnedPosition(item) {
        if (!this.state.designId || !item || !item.layout_key) return;
        try {
            await rpc("/southbrook_kitchen/configurator/save_position", {
                design_id:      this.state.designId,
                layout_key:     item.layout_key,
                x_position_in:  item.x_position_in || 0,
                y_position_in:  item.y_position_in || 0,
                z_position_in:  item.z_position_in || 0,
                rotation_deg:   item.rotation_deg  || 0,
                pinned:         true,
            });
        } catch (_) {
            // Non-fatal — pin stays on the client until the next
            // full /save.
        }
    }

    // ─── D5 — Debounced auto-save ───────────────────────────────────────────────
    // Called from every mutation path (room change, drag-resize end,
    // cabinet edit drawer ops). 3s after the last edit, fires a silent
    // save against /save. Skipped when state.items is empty (nothing
    // to persist) or a manual save is in flight.
    _queueAutoSave() {
        // PR2.5a (Gap 2) — a failed hydration must never auto-save: the
        // in-memory state is whatever was left over (possibly the
        // generator's fill from a prior edit path), and save_design is
        // a full-replace writer. See the catch branch in
        // _hydrateFromDesign() for the full rationale.
        if (this._hydrationFailed) return;
        // T7 — first-action gate: until the rep genuinely touches the
        // design (_markUserAction), programmatic refresh paths must not
        // schedule a full-replace save over a saved/template design.
        if (!this._userActed) return;
        if (this._autoSaveTimer) {
            clearTimeout(this._autoSaveTimer);
            this._autoSaveTimer = null;
        }
        if (!this.state.items || !this.state.items.length) return;
        this._autoSaveTimer = setTimeout(() => {
            this._autoSaveTimer = null;
            this._autoSave();
        }, 3000);
    }

    async _autoSave() {
        if (this.state.saving) return;       // manual save in flight - skip
        if (this.state.autoSaving) return;   // another auto-save in flight
        this.state.autoSaving = true;
        try {
            const result = await rpc("/southbrook_kitchen/configurator/save", {
                name:       this.state.designName || "Untitled Kitchen",
                room:       this.state.room,
                items:      this.state.items,
                design_id:  this.state.designId || false,
                partner_id: this.state.partnerId || false,
            });
            this.state.designId       = result.id;
            this.state.designName     = result.name;
            this.state.lastAutoSaveAt = Date.now();
            // PR4.1 (2026-07-12) — see _applyServerPlacements() doc
            // comment. This is the fix: a drag-add on a non-back wall
            // routes through _queueAutoSave() -> here, not
            // _saveDesign(), and previously never got the engine's
            // wall pose applied client-side.
            this._applyServerPlacements(result.placed);
            // C2 — same as _saveDesign; see _applyRelaidResult doc
            // comment. A drag onto a second wall routes through
            // _queueAutoSave() -> here, so this path must react to a
            // corner-resolution relay too.
            this._applyRelaidResult(result);
        } catch (e) {
            // Silent on auto-save: manual save will surface errors. Log
            // for diagnostics only.
            console.warn("[SouthbrookKitchenConfigurator] auto-save failed:", e);
        } finally {
            this.state.autoSaving = false;
        }
    }

    async _openDesigns() {
        return this.action.doAction(
            "southbrook_kitchen_3d_configurator.action_sbk_kitchen_designs"
        );
    }

    // D10 — Run the controller-supplied CTA action (typically opens
    // the Cabinet Products list filtered by southbrook_is_cabinet).
    async _runErrorCta() {
        const cta = this.state.errorCta;
        if (!cta || !cta.action) return;
        try {
            return this.action.doAction(cta.action);
        } catch (e) {
            this.notification.add(
                "Couldn't open the cabinet catalog: " + (e.message || e),
                { type: "danger" }
            );
        }
    }

    // D10 — Open the BOM list for the currently-selected product so
    // the rep can add a BoM and unblock manufacturing in one click.
    async _openSelectedProductBom() {
        const sel = this.state.selected;
        if (!sel || !sel.template_id) return;
        return this.action.doAction({
            type:      "ir.actions.act_window",
            name:      "Bill of Materials",
            res_model: "mrp.bom",
            view_mode: "list,form",
            views:     [[false, "list"], [false, "form"]],
            domain:    [["product_tmpl_id", "=", sel.template_id]],
            context:   {
                default_product_tmpl_id: sel.template_id,
                default_type:            "normal",
            },
            target:    "current",
        });
    }

    // ─── Formatting helpers ───────────────────────────────────────────────────────
    _money(v) {
        const sym = (this.state.channel && this.state.channel.currency_symbol) || "$";
        return `${sym}${Number(v || 0).toFixed(2)}`;
    }

    _materialLabel(k) {
        return ({
            white_melamine: "White Melamine",
            grey_melamine:  "Grey Melamine",
            maple_veneer:   "Maple Veneer",
            oak_veneer:     "Oak Veneer",
            painted_mdf:    "Painted MDF",
            thermoplastic:  "Thermoplastic",
        })[k] || k;
    }

    // PR1 — the shared <KitchenCanvas> owns the wall hover/click/highlight
    // + raycasting; it reports the picked wall through the onWallSelect
    // prop, which routes here. The configurator only records it as UI
    // state — no placement / save / render path reads state.activeWall
    // yet (PR2/PR3/PR4), so straight-kitchen behaviour is unchanged.
    _onWallSelect(wall) {
        this._markUserAction();   // T7 gate
        this.state.activeWall = wall;
    }

    // Display label for the active-wall indicator; falsy (no wall picked
    // yet) renders as "None". Mirrors _materialLabel's map-with-fallback
    // shape above.
    _wallLabel(wall) {
        return { back: "Back", left: "Left", right: "Right", front: "Front" }[wall] || "None";
    }
}

// ─── OWL Template ─────────────────────────────────────────────────────────────
SouthbrookKitchenConfigurator.template = xml`
<div class="o_sbk_root">

  <!-- Top bar -->
  <header class="o_sbk_topbar">
    <div class="o_sbk_brand_block">
      <div class="o_sbk_logo">
        <div class="o_sbk_logo_inner"/>
      </div>
      <div>
        <div class="o_sbk_brand_label">SOUTHBROOK KITCHEN</div>
        <h1 class="o_sbk_title">3D Room Configurator</h1>
      </div>
    </div>
    <!-- Room dimensions moved into the topbar (2026-06-30) so the
         left aside is dedicated to setup/preferences, not spatial
         inputs. margin-right:auto (in SCSS) keeps this group flush
         against the brand block; actions stay right-aligned. -->
    <div class="o_sbk_room_dims">
      <label class="o_sbk_room_dim">
        <span class="o_sbk_room_dim_label">Width</span>
        <div class="o_sbk_room_dim_input_row">
          <input type="number" min="12" step="6"
                 t-att-value="state.room.width_in"
                 t-on-change="(ev) => this._changeRoom('width_in', ev.target.value)"/>
          <span class="o_sbk_room_dim_ft"><t t-esc="(state.room.width_in / 12).toFixed(1)"/>′</span>
        </div>
      </label>
      <label class="o_sbk_room_dim">
        <span class="o_sbk_room_dim_label">Depth</span>
        <input type="number" min="12" step="6"
               t-att-value="state.room.depth_in"
               t-on-change="(ev) => this._changeRoom('depth_in', ev.target.value)"/>
      </label>
      <label class="o_sbk_room_dim">
        <span class="o_sbk_room_dim_label">Height</span>
        <input type="number" min="84" step="6"
               t-att-value="state.room.height_in"
               t-on-change="(ev) => this._changeRoom('height_in', ev.target.value)"/>
      </label>
      <!-- Templates T3 (user directive): the template entry lives in the
           TOP FRAME beside the room dimensions — dims first, then shape
           + cabinet selections (the four-dropdown picker dialog). -->
      <button class="o_sbk_btn o_sbk_btn_template" type="button"
              title="Start from a prebuilt sample kitchen"
              t-on-click="_openTemplatePicker">Start from Template</button>
    </div>
    <!-- Task #48 — Customer picker (top-bar).
         Hidden entirely for portal users — Agent A's default_get
         autofills partner_id from user.partner_id so the customer
         doesn't need to pick themselves. In that case we show a
         subtle reassurance pill instead. -->
    <div t-if="!state.portalUser" class="o_sbk_customer_picker">
      <input type="text" class="o_sbk_customer_input"
             placeholder="Customer…"
             autocomplete="off"
             role="combobox"
             aria-haspopup="listbox"
             t-att-aria-expanded="state.pickerOpen ? 'true' : 'false'"
             t-att-value="state.partnerName"
             t-on-focus="_openCustomerPicker"
             t-on-blur="_closeCustomerPicker"
             t-on-input="_onCustomerTyped"
             aria-label="Assign customer to this design"/>
      <div t-if="state.pickerOpen" class="o_sbk_customer_dropdown" role="listbox">
        <div t-if="state.pickerLoading" class="o_sbk_customer_hint">Loading recent customers…</div>
        <div t-if="!state.pickerLoading &amp;&amp; !state.pickerResults.length"
             class="o_sbk_customer_hint">No recent customers</div>
        <button t-foreach="state.pickerResults" t-as="picked" t-key="picked.id"
                class="o_sbk_customer_option"
                type="button"
                role="option"
                t-att-aria-selected="state.partnerId === picked.id ? 'true' : 'false'"
                t-on-mousedown="() => this._setCustomer(picked)">
          <span class="o_sbk_customer_name" t-esc="picked.name"/>
          <span t-if="picked.channel" class="o_sbk_customer_channel"
                t-att-class="'o_sbk_customer_channel o_sbk_ch_' + picked.channel"
                t-esc="picked.channel"/>
        </button>
        <div class="o_sbk_customer_sep"/>
        <button class="o_sbk_customer_walkin"
                type="button"
                t-on-mousedown="_useWalkInCustomer">
          + Walk-in Customer
        </button>
      </div>
    </div>
    <!-- Portal user reassurance pill. Only rendered when we actually
         know the partner's name (partnerName may be blank if the
         design opened without props). Silent otherwise. -->
    <div t-elif="state.partnerName" class="o_sbk_portal_indicator"
         t-att-title="'You are designing as ' + state.partnerName">
      <span class="o_sbk_portal_prefix">Designing as</span>
      <strong class="o_sbk_portal_name" t-esc="state.partnerName"/>
    </div>
    <!-- D3 — Channel pricing badge. Shows the partner-resolved
         pricelist so every price the user sees in the configurator
         is the one the quote will use. Suppressed for the default
         retail/no-partner case so it doesn't clutter the topbar. -->
    <div t-if="state.channel.channel !== 'retail'" class="o_sbk_channel_badge"
         t-att-title="'Pricelist: ' + state.channel.pricelist_name + (state.channel.partner_name ? ' (' + state.channel.partner_name + ')' : '')">
      <span class="o_sbk_channel_dot"
            t-att-class="'o_sbk_channel_dot o_sbk_ch_' + state.channel.channel"/>
      <span class="o_sbk_channel_label" t-esc="state.channel.channel_label"/>
    </div>
    <div class="o_sbk_actions">
      <button class="o_sbk_btn o_sbk_btn_ghost"
              t-on-click="() => this._stretchWidth(-24)"
              t-att-disabled="state.room.width_in &lt;= 12">
        − 24 in
      </button>
      <button class="o_sbk_btn o_sbk_btn_ghost"
              t-on-click="() => this._stretchWidth(24)">
        + 24 in
      </button>
      <button class="o_sbk_btn o_sbk_btn_outline"
              t-on-click="_openDesigns">
        Kitchen Designs
      </button>
      <!-- D5 — Background auto-save status pill. Quiet by default;
           pops "Saving..." during a debounced save and a green
           checkmark for ~3s after success. -->
      <span t-if="state.autoSaving" class="o_sbk_autosave">Saving…</span>
      <span t-elif="state.lastAutoSaveAt &gt; 0" class="o_sbk_autosave is-ok">✓ Auto-saved</span>
      <button class="o_sbk_btn o_sbk_btn_primary"
              t-att-disabled="state.saving || (!state.items.length &amp;&amp; !state.portalUser)"
              t-on-click="_saveDesign">
        <t t-if="state.saving">Saving…</t>
        <t t-elif="state.portalUser">Save Draft</t>
        <t t-else="">Save Design</t>
      </button>
      <!-- Task #48 — Create &amp; Confirm. Only surfaces for internal
           users (portal customers don't create quotes directly; they
           submit the design and the assigned rep confirms). Disabled
           until the design has at least one cabinet, or a save is
           already in flight. -->
      <button t-if="!state.portalUser"
              class="o_sbk_btn o_sbk_btn_confirm"
              t-att-disabled="state.saving || !state.items.length"
              t-on-click="_createAndConfirm"
              title="Save the design and create a draft Sale Order for this customer">
        <t t-if="state.saving">Working…</t>
        <t t-else="">Create &amp; Confirm</t>
      </button>
    </div>
  </header>

  <!-- Loading splash -->
  <div t-if="state.loading" class="o_sbk_loading">
    <div class="o_sbk_spinner"/>
    <span>Loading cabinet inventory…</span>
  </div>

  <!-- Main workspace -->
  <div t-else="" class="o_sbk_workspace">

    <!-- Left controls -->
    <aside class="o_sbk_controls">
      <h3>Room</h3>

      <!-- Width/Depth/Height moved to the topbar (2026-06-30). -->

      <!-- D4 — Save current room dims as this user's default. -->
      <button class="o_sbk_save_default_link"
              t-on-click="_saveAsMyDefault"
              title="Use these room dimensions as the prefill for every new design you open">
        Save as my default
      </button>

      <!-- D7 — Filler placement strategy.
           F12 fix (2026-07-27) — disabled once hydrated from a saved
           design: _refreshLayout() never regenerates post-hydration, so
           this control is dead then (see _changeFillerStrategy's guard);
           disabling it here means it no longer LOOKS live when it isn't. -->
      <label class="o_sbk_field"
             t-att-title="state.hydratedFromDesign ? 'Reopen-safe mode — layout regeneration is disabled for a reopened design' : 'Filler placement strategy'">
        <span>Filler strategy</span>
        <select class="o_sbk_edit_select"
                t-att-value="state.fillerStrategy"
                t-att-disabled="state.hydratedFromDesign"
                t-on-change="(ev) => this._changeFillerStrategy(ev.target.value)">
          <option value="split"  t-att-selected="state.fillerStrategy === 'split'  ? 'selected' : ''">Split — both ends</option>
          <option value="right"  t-att-selected="state.fillerStrategy === 'right'  ? 'selected' : ''">Right end only</option>
          <option value="left"   t-att-selected="state.fillerStrategy === 'left'   ? 'selected' : ''">Left end only</option>
          <option value="scribe" t-att-selected="state.fillerStrategy === 'scribe' ? 'selected' : ''">Scribe — no filler</option>
        </select>
      </label>

      <!-- D8 — Wall cabinet top alignment + soffit height.
           F12 fix (2026-07-27) — same disabled-once-hydrated treatment
           as filler strategy above; both selects and the soffit input
           are dead post-hydration. -->
      <label class="o_sbk_field"
             t-att-title="state.hydratedFromDesign ? 'Reopen-safe mode — layout regeneration is disabled for a reopened design' : 'Wall cabinet top alignment'">
        <span>Wall cab top</span>
        <select class="o_sbk_edit_select"
                t-att-value="state.wallCabTopAlignment"
                t-att-disabled="state.hydratedFromDesign"
                t-on-change="(ev) => this._changeWallAlignment(ev.target.value)">
          <option value="fixed_gap"  t-att-selected="state.wallCabTopAlignment === 'fixed_gap'  ? 'selected' : ''">18″ gap above counter</option>
          <option value="to_ceiling" t-att-selected="state.wallCabTopAlignment === 'to_ceiling' ? 'selected' : ''">Up to ceiling</option>
          <option value="to_soffit"  t-att-selected="state.wallCabTopAlignment === 'to_soffit'  ? 'selected' : ''">Up to soffit</option>
        </select>
      </label>
      <label t-if="state.wallCabTopAlignment === 'to_soffit'" class="o_sbk_field"
             t-att-title="state.hydratedFromDesign ? 'Reopen-safe mode — layout regeneration is disabled for a reopened design' : 'Soffit height'">
        <span>Soffit height (in)</span>
        <input type="number" min="36" max="144" step="1"
               class="o_sbk_edit_input"
               t-att-value="state.soffitHeightIn"
               t-att-disabled="state.hydratedFromDesign"
               t-on-change="(ev) => this._changeSoffit(ev.target.value)"/>
      </label>

      <!-- Summary metrics -->
      <div class="o_sbk_divider"/>

      <div class="o_sbk_metric">
        <strong><t t-esc="state.summary.base_count"/></strong>
        base cabinets
      </div>
      <div class="o_sbk_metric">
        <strong><t t-esc="state.summary.wall_count"/></strong>
        wall cabinets
      </div>
      <div class="o_sbk_metric o_sbk_metric_price">
        <strong><t t-esc="_money(state.summary.price)"/></strong>
        cabinets
      </div>

      <!-- D7 — Filler price as a separate breakdown row when present -->
      <div t-if="state.summary.filler_price &gt; 0" class="o_sbk_metric o_sbk_metric_filler">
        <strong>+ <t t-esc="_money(state.summary.filler_price)"/></strong>
        filler
      </div>

      <div t-if="state.summary.remainder_in > 0" class="o_sbk_remainder">
        <span class="o_sbk_remainder_icon">▤</span>
        <t t-esc="state.summary.remainder_in.toFixed(1)"/> in filler
        <span class="o_sbk_remainder_strategy"> (<t t-esc="state.fillerStrategy"/>)</span>
      </div>

      <!-- D7 — Snap-to-clean-width CTA when room is a few inches off
           a module-clean total. One click eliminates the filler. -->
      <div t-if="state.summary.snap_hint" class="o_sbk_snap_hint">
        <button class="o_sbk_snap_btn"
                t-on-click="_applySnapHint"
                t-att-title="'Round room to ' + state.summary.snap_hint.target_width + &quot; in to eliminate filler&quot;">
          <t t-if="state.summary.snap_hint.direction === 'expand'">
            ↗ Stretch to <t t-esc="state.summary.snap_hint.target_width"/>″ (no filler)
          </t>
          <t t-else="">
            ↙ Shrink to <t t-esc="state.summary.snap_hint.target_width"/>″ (no filler)
          </t>
        </button>
      </div>

      <!-- D10 — Structured onboarding nudge when no cabinet products
           are configured (NO_CABINETS). Falls back to the bare error
           string for any other error. -->
      <div t-if="state.error &amp;&amp; state.errorCode === 'NO_CABINETS'" class="o_sbk_onboard">
        <strong class="o_sbk_onboard_title">Catalog is empty</strong>
        <p class="o_sbk_onboard_body" t-esc="state.error"/>
        <button t-if="state.errorCta" class="o_sbk_onboard_cta" t-on-click="_runErrorCta">
          <t t-esc="state.errorCta.label"/> →
        </button>
      </div>
      <div t-elif="state.error" class="o_sbk_error" t-esc="state.error"/>

      <!-- D8 (and forward-compat for D12) — layout validation warnings -->
      <div t-if="state.warnings &amp;&amp; state.warnings.length" class="o_sbk_warnings">
        <div t-foreach="state.warnings" t-as="w" t-key="w.code"
             t-att-class="'o_sbk_warning o_sbk_warning_' + (w.severity || 'info')">
          <strong class="o_sbk_warning_icon">⚠</strong>
          <span class="o_sbk_warning_text" t-esc="w.message"/>
        </div>
      </div>
    </aside>

    <!-- 3D Scene panel -->
    <!-- D13 — Wrapper carries the drag-and-drop handlers; the canvas3d
         div stays clean for Three.js. dragHover toggles a brand-blue
         outline so the rep gets immediate "drop here" feedback. -->
    <main t-att-class="'o_sbk_scene_panel' + (state.dragHover ? ' is-drag-hover' : '')"
          t-on-dragover="_onCanvasDragOver"
          t-on-dragleave="_onCanvasDragLeave"
          t-on-drop="_onCanvasDrop">
      <!-- Rec D · Sprint 2d Step 24c — KitchenCanvas is now the sole
           visible 3D surface. Parent's <div t-ref="canvas3d"/> is
           removed. Callback props route child events back into parent
           OWL state; onReady exposes an imperative API for the
           parent's window-scoped keyboard + HTML5 drop handlers. -->
      <KitchenCanvas items="state.items" room="state.room"
                     view="state.view" selected="state.selected"
                     draggedProduct="state.draggedProduct"
                     dragHover="state.dragHover"
                     activeWall="state.activeWall"
                     onWallSelect="(w) => this._onWallSelect(w)"
                     onSelectItem="(item) => this._onCanvasSelect(item)"
                     onMoveItem="(p) => this._onCanvasMove(p)"
                     onResizeRoom="(nw, f) => this._onCanvasResize(nw, f)"
                     onResizeRoomDepth="(nd, f) => this._onCanvasResizeDepth(nd, f)"
                     onViewChange="(k) => this._onCanvasViewChange(k)"
                     onReady="(api) => this._onCanvasReady(api)"/>

      <!-- Task #48 — Persistent count + room + estimate strip.
           Real-time sanity check for the customer / rep. Filler is
           excluded from the cabinet count to match the pricing rule
           (fillers don't ship on the SO, don't get built). Room
           feet come straight from state.room; estimated total is the
           already-computed state.summary.price so we don't overlap
           model-side pricing work Agent A hasn't scoped. -->
      <div class="o_sbk_count_strip" role="status" aria-live="polite">
        <span class="o_sbk_count_item">
          <strong t-esc="_nonFillerCount()"/>
          <span class="o_sbk_count_label"> cabinets</span>
        </span>
        <span class="o_sbk_count_sep">•</span>
        <span class="o_sbk_count_item">
          <t t-esc="(state.room.width_in / 12).toFixed(1)"/>′ W
          <span class="o_sbk_count_dim_x">×</span>
          <t t-esc="(state.room.depth_in / 12).toFixed(1)"/>′ D
        </span>
        <!-- PR1 — active-wall indicator. The room itself is the selector
             (hover glow + click-to-select are handled inside
             <KitchenCanvas>); this mirrors the picked wall as text so the
             active target is obvious even away from the 3D outline. Reads
             "None" until a wall is clicked. -->
        <span class="o_sbk_count_sep">•</span>
        <span class="o_sbk_count_item" role="status" aria-live="polite">
          <span class="o_sbk_count_label">Active Wall: </span>
          <strong t-esc="_wallLabel(state.activeWall)"/>
        </span>
        <t t-if="state.summary.price &gt; 0">
          <span class="o_sbk_count_sep">•</span>
          <span class="o_sbk_count_item o_sbk_count_price">
            <span class="o_sbk_count_label">Est. </span>
            <strong t-esc="_money(state.summary.price)"/>
          </span>
        </t>
      </div>

      <!-- Dimension ruler overlay -->
      <div class="o_sbk_ruler">
        <t t-foreach="[0, 24, 48, 72, 96, 120, 144]" t-as="mark" t-key="mark">
          <span t-if="mark &lt;= state.room.width_in" class="o_sbk_mark">
            <t t-esc="mark"/> in
          </span>
        </t>
      </div>

      <!-- View label -->
      <!-- D1 — View switcher toolbar. 6 cameras + reset + zoom. -->
      <div class="o_sbk_viewbar" role="toolbar" aria-label="Camera view">
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (state.view === 'iso' ? ' is-active' : '')"
                t-att-aria-pressed="state.view === 'iso'"
                aria-label="Isometric view (1)"
                title="Isometric (1)"
                t-on-click="() => this._setView('iso')">
          <span class="o_sbk_viewicon">⬡</span><span class="o_sbk_viewkey">1</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (state.view === 'top' ? ' is-active' : '')"
                t-att-aria-pressed="state.view === 'top'"
                aria-label="Top / Plan view (2)"
                title="Top / Plan (2)"
                t-on-click="() => this._setView('top')">
          <span class="o_sbk_viewicon">▦</span><span class="o_sbk_viewkey">2</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (state.view === 'front' ? ' is-active' : '')"
                t-att-aria-pressed="state.view === 'front'"
                aria-label="Front view (3)"
                title="Front (3)"
                t-on-click="() => this._setView('front')">
          <span class="o_sbk_viewicon">▮</span><span class="o_sbk_viewkey">3</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (state.view === 'left' ? ' is-active' : '')"
                t-att-aria-pressed="state.view === 'left'"
                aria-label="Left view (4)"
                title="Left side (4)"
                t-on-click="() => this._setView('left')">
          <span class="o_sbk_viewicon">◧</span><span class="o_sbk_viewkey">4</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (state.view === 'right' ? ' is-active' : '')"
                t-att-aria-pressed="state.view === 'right'"
                aria-label="Right view (5)"
                title="Right side (5)"
                t-on-click="() => this._setView('right')">
          <span class="o_sbk_viewicon">◨</span><span class="o_sbk_viewkey">5</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (state.view === 'persp' ? ' is-active' : '')"
                t-att-aria-pressed="state.view === 'persp'"
                aria-label="Perspective / free orbit (6)"
                title="Perspective / orbit (6)"
                t-on-click="() => this._setView('persp')">
          <span class="o_sbk_viewicon">◉</span><span class="o_sbk_viewkey">6</span>
        </button>
        <div class="o_sbk_viewsep"/>
        <button class="o_sbk_viewbtn"
                aria-label="Reset view (R)"
                title="Reset to Isometric (R)"
                t-on-click="() => this._setView('iso')">
          <span class="o_sbk_viewicon">⌂</span><span class="o_sbk_viewkey">R</span>
        </button>
      </div>
      <div class="o_sbk_draghint">
        <t t-if="state.view === 'iso' || state.view === 'top'">Drag cabinet to move • Drag ● to resize room • Click select • ←/→ cycle • 1-6 views • ⌘S save</t>
        <t t-elif="state.view === 'persp'">Drag cabinet to move • Drag empty space to orbit • Wheel zoom • ←/→ cycle • R reset</t>
        <t t-else="">+/- zoom • Click select • ←/→ cycle • Switch to Iso/Top/Persp to move cabinets</t>
      </div>
    </main>

    <!-- Right inventory + detail panel -->
    <aside class="o_sbk_inventory">

      <div class="o_sbk_inv_head">
        <h3>Cabinet Inventory</h3>
        <span class="o_sbk_inv_count">
          <t t-esc="_filteredProducts().length"/> / <t t-esc="state.products.length"/>
        </span>
      </div>

      <!-- Category filter dropdown. Options with zero matching
           products auto-hide so the menu stays tight as SKUs grow
           into new categories (Appliances etc. will surface here
           automatically when their data lands, no template edit
           needed). Count next to each label so users see at a
           glance how many items are in each group. -->
      <label class="o_sbk_inv_cat_field">
        <span class="o_sbk_inv_cat_label">Filter by type</span>
        <select class="o_sbk_inv_cat_select"
                t-att-value="state.inventoryCategory"
                t-on-change="(ev) => this._setInventoryCategory(ev.target.value)"
                aria-label="Filter cabinet inventory by product category">
          <option value="all" t-att-selected="state.inventoryCategory === 'all' ? 'selected' : ''">
            All (<t t-esc="_categoryCount('all')"/>)
          </option>
          <option t-if="_categoryCount('base') &gt; 0" value="base"
                  t-att-selected="state.inventoryCategory === 'base' ? 'selected' : ''">
            Base (<t t-esc="_categoryCount('base')"/>)
          </option>
          <option t-if="_categoryCount('wall') &gt; 0" value="wall"
                  t-att-selected="state.inventoryCategory === 'wall' ? 'selected' : ''">
            Wall (<t t-esc="_categoryCount('wall')"/>)
          </option>
          <option t-if="_categoryCount('tall') &gt; 0" value="tall"
                  t-att-selected="state.inventoryCategory === 'tall' ? 'selected' : ''">
            Tall (<t t-esc="_categoryCount('tall')"/>)
          </option>
          <!-- F1 fix (2026-07-27) — corner units used to be folded into
               the Tall filter (_inventoryCategoryTypes mapped
               tall -> ["tall", "corner"]), hiding them under the wrong
               label with no way to isolate them. Corner now gets its
               own tab. -->
          <option t-if="_categoryCount('corner') &gt; 0" value="corner"
                  t-att-selected="state.inventoryCategory === 'corner' ? 'selected' : ''">
            Corner (<t t-esc="_categoryCount('corner')"/>)
          </option>
          <option t-if="_categoryCount('panels') &gt; 0" value="panels"
                  t-att-selected="state.inventoryCategory === 'panels' ? 'selected' : ''">
            Panels (<t t-esc="_categoryCount('panels')"/>)
          </option>
        </select>
      </label>

      <!-- D13 — Searchable inventory. Filters by name, SKU, cabinet
           type, material, door style. Empty = full catalog. -->
      <div class="o_sbk_inv_search">
        <input type="search" class="o_sbk_inv_search_input"
               placeholder="Search by name, SKU, type, material…"
               t-att-value="state.inventorySearch"
               t-on-input="_onSearchInput"
               aria-label="Search cabinet inventory"/>
        <button t-if="state.inventorySearch" class="o_sbk_inv_search_clear"
                t-on-click="_clearSearch" aria-label="Clear search">×</button>
      </div>

      <!-- D13 — Drag hint pill. Only shown when search has results so it
           doesn't clutter the empty state. -->
      <div t-if="_filteredProducts().length" class="o_sbk_inv_draghint">
        ↔ Drag any product into the scene to add it
      </div>

      <!-- Column header -->
      <div class="o_sbk_inv_header">
        <span>Product</span><span>SKU</span><span>Width</span><span>Qty</span><span>Price</span>
      </div>

      <!-- Product rows.
           F14 fix (2026-07-27) — row click now PREVIEWS/highlights the
           catalog product (_previewCatalogProduct, writes
           state.catalogPreview) instead of writing state.selected — a
           raw catalog product has no layout_key, so the edit drawer's
           actions used to silently no-op against it. is-selected lights
           up for EITHER a matching real selection or a matching preview.
           F15 fix (2026-07-27) — a keyboard-reachable "+ Add" button
           (drag-and-drop was previously the only way to add a cabinet).
           It's a SIBLING of the row button, not nested inside it
           (buttons cannot legally nest) — the shared t-foreach lives on
           the wrapping <t>, which OWL renders as two flat siblings per
           iteration, no extra wrapper element needed. Appends to the
           active wall per F7's fixed precedence (state.activeWall wins
           over any stale drop-wall override). -->
      <div class="o_sbk_product_list">
        <t t-foreach="_filteredProducts()" t-as="product" t-key="product.product_id">
        <button
            t-att-class="'o_sbk_product_row o_sbk_prod_draggable' + ((state.selected &amp;&amp; state.selected.product_id === product.product_id) || (state.catalogPreview &amp;&amp; state.catalogPreview.product_id === product.product_id) ? ' is-selected' : '')"
            draggable="true"
            t-on-dragstart="(ev) => this._onProductDragStart(ev, product)"
            t-on-dragend="(ev) => this._onProductDragEnd(ev)"
            t-on-click="() => this._previewCatalogProduct(product)"
            t-att-title="'Click to preview · drag onto the scene, or use + Add, to place ' + product.name">
          <div class="o_sbk_prod_thumb">
            <img t-att-src="product.image_url" alt="" loading="lazy"/>
          </div>
          <div class="o_sbk_prod_info">
            <strong t-esc="product.name"/>
            <!-- Typo-spec item 7 — this caption now ellipsizes on overflow
                 (scss); the title= gives the full type/material on hover
                 so the row-level title above only has to cover the name. -->
            <small t-att-title="product.cabinet_type + ' | ' + product.material"><t t-esc="product.cabinet_type"/> | <t t-esc="product.material"/></small>
            <!-- D17 — Archetype taxonomy badge when the template is
                 mapped to a Southbrook cabinet archetype. -->
            <span t-if="product.archetype_code" class="o_sbk_arch_badge"
                  t-att-title="'Archetype: ' + product.archetype_code + (product.archetype_collection ? ' (' + product.archetype_collection + ')' : '')">
              <t t-esc="product.archetype_body_class || product.archetype_code"/>
            </span>
          </div>
          <span class="o_sbk_prod_sku"  t-esc="product.sku"/>
          <span class="o_sbk_prod_dim"  t-esc="product.width_in + '&quot;'"/>
          <span t-att-class="'o_sbk_prod_qty' + (product.available_qty > 50 ? ' ok' : ' low')"
                t-esc="product.available_qty"/>
          <span class="o_sbk_prod_price" t-esc="_money(product.price)"/>
        </button>
        <button type="button" class="o_sbk_prod_add_btn"
                draggable="false"
                t-on-click="() => this._addCabinetFromProduct(product, null, null)"
                t-att-aria-label="'Add ' + product.name + ' to the layout'"
                title="Add to layout">+ Add</button>
        </t>
        <!-- D13 — Empty-state when search yields zero matches. -->
        <div t-if="!_filteredProducts().length &amp;&amp; state.products.length"
             class="o_sbk_inv_empty">
          No products match "<t t-esc="state.inventorySearch"/>".
          <button class="o_sbk_inv_empty_clear" t-on-click="_clearSearch">Clear search</button>
        </div>
      </div>

      <!-- D2 — Selected cabinet inline-edit drawer.
           Width / product swap / remove are now first-class edits;
           the scene rebuilds locally on every change. Save Design
           still persists the result; D5 will add debounced auto-save.
           F14 fix (2026-07-27) — the drawer must only ever bind to a
           real placed layout item (one with a layout_key), never a
           catalog-preview product; the t-if gate now says so directly
           rather than relying solely on the action methods' guards. -->
      <div t-if="state.selected &amp;&amp; state.selected.layout_key" class="o_sbk_detail">
        <div class="o_sbk_detail_head">
          <h4>Selected Cabinet</h4>
          <button class="o_sbk_detail_remove"
                  aria-label="Remove this cabinet from the layout"
                  title="Remove cabinet"
                  t-on-click="_removeSelectedCabinet">✕</button>
        </div>
        <div class="o_sbk_detail_card">
          <div class="o_sbk_detail_img">
            <img t-if="state.selected.image_url" t-att-src="state.selected.image_url" alt=""/>
            <div t-else="" class="o_sbk_detail_img_placeholder">🗄</div>
          </div>
          <div class="o_sbk_detail_info">
            <p class="o_sbk_detail_name"><t t-esc="state.selected.name"/></p>
            <p class="o_sbk_detail_type"><t t-esc="state.selected.cabinet_type"/> cabinet</p>
          </div>
        </div>

        <!-- D10 — BOM warning banner: red bar with one-click jump to
             the BoM form pre-filtered to this product. Without a BoM
             the MO can't be generated even though the quote will save. -->
        <div t-if="state.selected.bom_available === false" class="o_sbk_bom_warn">
          <strong>⚠ No BOM defined</strong>
          <span> — this product can't be manufactured until a Bill of Materials exists.</span>
          <button class="o_sbk_bom_warn_btn" t-on-click="_openSelectedProductBom">Open BoM →</button>
        </div>

        <!-- Inline edit controls -->
        <div class="o_sbk_edit_grid">
          <label class="o_sbk_edit_field">
            <span>Product</span>
            <select class="o_sbk_edit_select"
                    t-att-value="state.selected.product_id"
                    t-on-change="(ev) => this._swapSelectedProduct(ev.target.value)">
              <option t-foreach="_sameTypeProducts(state.selected.cabinet_type)"
                      t-as="p" t-key="p.product_id"
                      t-att-value="p.product_id"
                      t-att-selected="p.product_id === state.selected.product_id ? 'selected' : ''"
                      t-esc="p.name + ' — ' + p.width_in + ' in'"/>
            </select>
          </label>
          <label class="o_sbk_edit_field">
            <span>Width (in)</span>
            <input type="number" min="6" max="48" step="3" class="o_sbk_edit_input"
                   t-att-value="state.selected.width_in"
                   t-on-change="(ev) => this._updateSelectedWidth(ev.target.value)"/>
          </label>
        </div>

        <dl class="o_sbk_spec_grid">
          <dt>SKU</dt>      <dd><t t-esc="state.selected.sku || '—'"/></dd>
          <dt>Height</dt>   <dd><t t-esc="state.selected.height_in"/> in</dd>
          <dt>Depth</dt>    <dd><t t-esc="state.selected.depth_in"/> in</dd>
          <dt>Material</dt> <dd><t t-esc="_materialLabel(state.selected.material)"/></dd>
          <!-- D17 — Archetype taxonomy rows (only when mapped). -->
          <t t-if="state.selected.archetype_code">
            <dt>Archetype</dt>
            <dd>
              <strong t-esc="state.selected.archetype_code"/>
              <span t-if="state.selected.archetype_body_class"> · <t t-esc="state.selected.archetype_body_class"/></span>
            </dd>
            <t t-if="state.selected.archetype_collection">
              <dt>Collection</dt>
              <dd t-esc="state.selected.archetype_collection"/>
            </t>
          </t>
          <dt>Available</dt><dd t-att-class="state.selected.available_qty > 0 ? 'ok' : 'low'">
            <t t-esc="state.selected.available_qty"/> pcs
          </dd>
          <dt>BoM</dt>      <dd><t t-if="state.selected.bom_available">✓ Available</t><t t-else="">—</t></dd>
          <dt>Price</dt>    <dd class="price"><t t-esc="_money(state.selected.price)"/></dd>
        </dl>
      </div>

    </aside>
  </div>
</div>`;

SouthbrookKitchenConfigurator.props = {
    design_id:     { type: Number,  optional: true },
    design_name:   { type: String,  optional: true },
    room_width_in:  { type: Number, optional: true },
    room_depth_in:  { type: Number, optional: true },
    room_height_in: { type: Number, optional: true },
    // D3 — partner for channel pricelist resolution.
    partner_id:     { type: Number, optional: true },
    // Task #48 — partner display label. Not RPC-critical (server can
    // re-resolve from partner_id), just avoids a synchronous name_get
    // round-trip on component mount when a design carries the label.
    partner_name:   { type: String, optional: true },
    "*":            true,
};
SouthbrookKitchenConfigurator.defaultProps = {};
// Rec D · Sprint 2d Step 24c — register <KitchenCanvas> as a child.
SouthbrookKitchenConfigurator.components = { KitchenCanvas };

// Rec D · Sprint 2d Step 24c bridge — child-to-parent callbacks.
// Injects fire-and-forget state mutations into the parent lifecycle
// so the child stays pure viewport (no RPCs, no notification writes).
SouthbrookKitchenConfigurator.prototype._onCanvasReady = function (api) {
    this._canvasApi = api;
};
SouthbrookKitchenConfigurator.prototype._onCanvasSelect = function (item) {
    this.state.selected = item;
};
SouthbrookKitchenConfigurator.prototype._onCanvasMove = function ({ item, x_position_in, pinnable }) {
    this._markUserAction();   // T7 gate
    if (pinnable) {
        item.pinned = true;
        this._savePinnedPosition(item);
    }
    this._recomputeLayoutFromItems();
    this.notification.add(
        `Moved ${item.product_name || item.name || "cabinet"} to ${Math.round(x_position_in)}″`,
        { type: "success" }
    );
    this._queueAutoSave();
};
SouthbrookKitchenConfigurator.prototype._onCanvasResize = function (newWidthIn, inFlight) {
    this.state.room.width_in = newWidthIn;
    if (!inFlight) {
        this._markUserAction();   // T7 gate
        // T7 — saved designs re-derive through the engine, never the
        // single-wall /layout generator (see _rearrangeOnServer).
        if (this._usesEngineResize()) {
            this._rearrangeOnServer();
        } else {
            this._refreshLayout({ force: true }).then(() => this._queueAutoSave());
        }
    }
};
// Depth-axis counterpart of _onCanvasResize. The front-wall puller
// (isDepthHandle in canvas/drag_handle.esm.js) reports the dragged room
// depth through the child's onResizeRoomDepth callback (fired guarded at
// kitchen_canvas.esm.js — in-flight on move, committed on release). This
// mirrors the width path exactly: write state.room.depth_in live during
// the drag so the scene tracks the pointer, then on release run the
// layout regen + debounced auto-save. The /layout RPC payload already
// sends room_depth_in, so no controller change is required. Before this
// existed, the parent bound onResizeRoom but never onResizeRoomDepth and
// had no depth handler, so the child's guarded call was a silent no-op —
// the "left/front puller does nothing" defect.
SouthbrookKitchenConfigurator.prototype._onCanvasResizeDepth = function (newDepthIn, inFlight) {
    this.state.room.depth_in = newDepthIn;
    if (!inFlight) {
        this._markUserAction();   // T7 gate
        // T7 — same engine routing as the width puller above.
        if (this._usesEngineResize()) {
            this._rearrangeOnServer();
        } else {
            this._refreshLayout({ force: true }).then(() => this._queueAutoSave());
        }
    }
};
SouthbrookKitchenConfigurator.prototype._onCanvasViewChange = function (key) {
    if (this.state.view !== key) this.state.view = key;
};

actionRegistry.add("southbrook_kitchen_configurator", SouthbrookKitchenConfigurator);
