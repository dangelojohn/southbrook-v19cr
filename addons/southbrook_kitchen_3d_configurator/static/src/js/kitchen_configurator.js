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

import {
    Component, markup, onMounted, onPatched, onWillStart, onWillUnmount,
    useRef, useState, xml,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
// Task 35 — confirm-on-regenerate. House rule: NEVER window.confirm
// (browser modals wedge automation) — the Odoo Dialog service's own
// ConfirmationDialog is the sanctioned pattern (see e.g.
// web/static/src/views/kanban/kanban_renderer.js's archiveRecord for
// the house usage this mirrors: useService("dialog") +
// dialog.add(ConfirmationDialog, {...})).
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
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
// SBK-030 — local toast discipline. See toast_store.esm.js's own
// header for why this replaces `useService("notification")` for every
// message this component produces.
import { ToastStore } from "@southbrook_kitchen_3d_configurator/js/toast/toast_store.esm";
// Task 35 — undo/redo stack (minimal default scope) + the pure
// confirm-on-regenerate heuristic. See undo_store.esm.js's own header
// for the full contract; this file owns every "when do we snapshot"
// decision, the store only owns the stack mechanics.
import {
    UndoStore, shouldConfirmArrange,
} from "@southbrook_kitchen_3d_configurator/js/undo/undo_store.esm";
// Task 19 (corner-remediation 2026-07-30, SBK-032 + C9 UX) — production-
// readiness status chip pure reducer (chip-state derivation, dedupe,
// severity grouping). Same ownership split as the two imports above:
// this file decides WHEN to fetch /validate and WHAT a click does; the
// module only shapes the data.
import {
    dedupeIssues, groupBySeverity, deriveChipState,
} from "@southbrook_kitchen_3d_configurator/js/validation/validation_store.esm";
// Panel P2 (catalog redesign) — the inventory panel's pure data layer
// (name parsing, L/R handing, width recovery, search haystack, and the
// grouped/L-R-merged/sorted catalog shape) and its thumbnail renderer.
// Both are owned/tested independently of this file — see each module's
// own header for the contract. Neither is ever re-implemented here.
import {
    parseProductName, liftWidth, groupCatalog, searchText,
} from "@southbrook_kitchen_3d_configurator/js/canvas/catalog_list.esm";
import { cabinetThumbSVG } from "@southbrook_kitchen_3d_configurator/js/canvas/cabinet_thumb.esm";
// Touch editing v1 (2026-08-02 spec) — the shared control-pad component
// + its pure family/visibility, wall-cycle/elevation, and touch-stats
// helpers. See touch_pad.esm.js's own header for the full contract;
// this file owns WHEN to mount it, WHAT gates it, and WHICH parent
// method each callback prop drives.
import {
    TouchPad, getPadAffordances, nextWall, elevationViewForWall,
    NUDGE_STEP_IN, isTouchCapableAtBoot, makeEmptyTouchStats,
    accumulateTouchStats, hasTouchStats,
} from "@southbrook_kitchen_3d_configurator/js/canvas/touch_pad.esm";

const actionRegistry = registry.category("actions");

// Fix 10 (E2E P1) — the Placement panel's fine nudge step. Deliberately
// NOT the touch pad's NUDGE_STEP_IN (6in, imported above) — that pad is
// tuned for fast coarse repositioning by touch; this panel exists
// specifically so a rep who hits an overlap blocker can dial in the
// small correction (an eighth of an inch) that actually resolves it.
const PLACEMENT_NUDGE_STEP_IN = 0.125;

// ─── Task 7 (SBK-033) — the ONE cabinet-count partition ─────────────────────
// V4-counters-templates.md claim 1: four different places in this codebase
// each computed their OWN count of "how many cabinets" from state.items —
// the sidebar's base/wall-only tally, the header's non-filler tally, the
// server's base_count/wall_count/total_cabinets fields — and none of them
// agreed (corner/tall/panel/appliance lines were invisible to every one of
// them). This function is now the SINGLE definition every client-side
// surface uses, mirroring southbrook.kitchen.design._get_counter_summary()
// (models/kitchen_design.py) field-for-field so the two can never drift:
// every item has exactly one of the seven cabinet_type buckets below, so
// their sum always equals items.length (excluding nothing — fillers count
// too, unlike the legacy total_cabinets server field, which stays as-is).
// `unplaced_count` is a DIAGNOSTIC OVERLAY (an is_unresolved GHOST item is
// already counted once under its own cabinet_type bucket, same as the
// server), not an eighth additive bucket.
const CABINET_TYPE_COUNT_KEY = {
    base:      "base_count",
    wall:      "wall_count",
    tall:      "tall_count",
    corner:    "corner_count",
    filler:    "filler_count",
    panel:     "panel_count",
    appliance: "appliance_count",
};

export function countCabinetBuckets(items) {
    const counts = {
        base_count: 0, wall_count: 0, tall_count: 0, corner_count: 0,
        filler_count: 0, panel_count: 0, appliance_count: 0,
        unplaced_count: 0,
    };
    for (const it of (items || [])) {
        const key = CABINET_TYPE_COUNT_KEY[it && it.cabinet_type];
        if (key) counts[key] += 1;
        if (it && it.is_unresolved) counts.unplaced_count += 1;
    }
    counts.total = counts.base_count + counts.wall_count + counts.tall_count
        + counts.corner_count + counts.filler_count + counts.panel_count
        + counts.appliance_count;
    return counts;
}

// Task 34 (SBK-019) — pure, DOM-agnostic detector for the "side panels
// collapsed by default under 1024px" boot decision (see setup()'s own
// comment on `narrowAtBoot`). Exported — not just inlined — so it's
// independently unit-testable without a jsdom mount, same rationale as
// `countCabinetBuckets` above. Accepts an explicit `win` argument
// (rather than reading the global `window`) so a test can pass a bare
// `{ matchMedia }` fake; guards both a missing `matchMedia` (the jsdom
// test harness has no polyfill — confirmed, no `window.matchMedia` at
// all) and a thrown call (some embedders/older browsers).
export function isNarrowViewportAtBoot(win) {
    try {
        return !!(win && typeof win.matchMedia === "function"
            && win.matchMedia("(max-width: 1023px)").matches);
    } catch (_) {
        return false;
    }
}

// ─── P2 icons-everywhere — synthetic counter-strip family icons ────────────
// Design-system rule (southbrook-design-system skill, "Cabinet
// iconography — one source"): cabinetThumbSVG() is the ONLY cabinet icon
// language in this stack. The sidebar counter strip refers to cabinets by
// family (base/wall/corner/tall/panel) exactly like the inventory rows
// below it do, so it reuses that same renderer at metric-row scale
// instead of a hand-drawn second icon set. Synthetic, minimal-literal
// products (`{cabinet_type, width_in}`) are explicitly sanctioned by that
// skill for family-level icons — these never claim to represent one
// specific SKU. Built ONCE here at module load (pure function of a fixed
// literal, no product/user data involved) and reused across every
// render via `_metricIcon()` below — never rebuilt per paint.
// `appliance` is deliberately absent: cabinet_thumb.esm.js's
// `thumbFamily()` table has no appliance family, and faking one would
// show an icon that lies about what actually gets placed.
const METRIC_ICON_SVG = {
    base:   markup(cabinetThumbSVG({ cabinet_type: "base", width_in: 24, name: "", sku: "" }, 20)),
    wall:   markup(cabinetThumbSVG({ cabinet_type: "wall", width_in: 24, name: "", sku: "" }, 20)),
    corner: markup(cabinetThumbSVG({ cabinet_type: "corner", width_in: 24, name: "", sku: "" }, 20)),
    tall:   markup(cabinetThumbSVG({ cabinet_type: "tall", width_in: 24, name: "", sku: "" }, 20)),
    panel:  markup(cabinetThumbSVG({ cabinet_type: "panel", width_in: 24, name: "", sku: "" }, 20)),
};

// ─── OWL Component ────────────────────────────────────────────────────────────
class SouthbrookKitchenConfigurator extends Component {
    setup() {
        this.action = useService("action");
        // Task 35 — confirm-on-regenerate dialog service. See the
        // import comment above for the house pattern this mirrors.
        this.dialogService = useService("dialog");
        // SBK-030 — local toast store (reducer) + its reactive render
        // mirror. `_toast()` is the single funnel every notification-
        // worthy event in this file goes through now; nothing in this
        // component calls `useService("notification")` anymore — see
        // the import comment above and toast_store.esm.js's header.
        this._toastStore = new ToastStore();
        // Task 35 — undo/redo stack (minimal default scope). Pure
        // reducer, same pattern as `_toastStore` above; see
        // undo_store.esm.js's own header for the full contract.
        // `state.undoCount`/`state.redoCount` (below, in the useState
        // block) are the reactive mirror the template's two toolbar
        // buttons key their `disabled` state off — the store itself is
        // NOT reactive (deep snapshot arrays proxied through OWL's
        // reactivity would be pure overhead; nothing renders the
        // snapshots themselves, only the counts).
        this._undoStore = new UndoStore();
        // Task 35 — confirm-on-regenerate heuristic input (see
        // shouldConfirmArrange, undo_store.esm.js). true = "unknown,
        // assume dirty" — the safe default on boot and on every design
        // switch (_resetForNewSession resets this too); flips true on
        // every manual placement op (add/remove/move/width-resize/
        // swap) and false again after a successful explicit
        // auto-arrange (_confirmedAutoArrange). Plain instance field,
        // not reactive state — nothing renders it directly.
        this._placementsDirty = true;

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
        let restoredDesignId = restoredDesignFrame ? restoredDesignFrame.resId : null;

        // FIX 6 (P0, E2E) — the `/odoo/<model>/<resid>/<tag>` shape handled
        // above is NOT the only URL a refresh/deep-link can restore. This
        // client action is ALSO reachable by its own persisted
        // ir.actions.client record (views/kitchen_configurator_views.xml
        // `action_sbk_kitchen_configurator`, wired to the "3D Configurator"
        // menu item) — a doAction/URL for that goes through router.js
        // `urlToState`'s numeric-`action-<id>` branch instead, e.g.
        // `/odoo/action-1460/6/southbrook_kitchen_configurator`. Traced
        // against the vendored router.js in the local Odoo 19 checkout:
        // that path segment loop pushes THREE frames —
        //   [{action:1460}, {action:1460, resId:6},
        //    {active_id:6, action:"southbrook_kitchen_configurator"}]
        // — and, unlike the model-based shape above, NONE of them ever
        // carry a `model` key (an `action-<id>` segment has no model of
        // its own), so `restoredDesignFrame` above can never match this
        // shape at all — the id was silently dropped and the user landed
        // on the picker instead of their design.
        // Recover it here from the trailing frame instead: `active_id`
        // (falling back to `resId`, the same key `urlToState` would use
        // one frame earlier if `active_id` were ever absent) on the frame
        // whose own `action` equals THIS client action's tag — i.e. we
        // only trust a frame that is unambiguously our own action's
        // resolved id, not some unrelated frame that happens to be
        // sitting on the stack. This is safe from the same "bare open"
        // confusion the model-based check above guards against: the
        // "bare" entry points (southbrook_room.py action_open_kitchen_3d /
        // sale_order.py action_open_in_kitchen_3d) return plain dict
        // actions with no persisted `id`, so per action_service.js
        // `makeState` they only ever serialize to the tag-string URL
        // shape (handled above, correctly filtered by `model`) — they can
        // never produce this numeric `action-<id>` shape, so a resId/
        // active_id found here cannot be a room/SO id in disguise.
        // Defensive throughout: `actionStack` on this path may be
        // absent, short, or missing keys entirely — never throw.
        if (restoredDesignId === null && Array.isArray(restoredStack)) {
            const ownTag = (this.props.action && this.props.action.tag)
                || "southbrook_kitchen_configurator";
            const restoredActionFrame = restoredStack.find(
                (s) => s && s.action === ownTag
            );
            if (restoredActionFrame) {
                const candidate = typeof restoredActionFrame.active_id === "number"
                    ? restoredActionFrame.active_id
                    : restoredActionFrame.resId;
                if (typeof candidate === "number") {
                    restoredDesignId = candidate;
                }
            }
        }
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

        // Task 20 (SBK-034) — the id (or its absence) resolved above is
        // the single source of truth for what the FIRST render shows.
        // Computed once, here, so both `designId` and `screen` below
        // agree with each other from the very first paint — no window
        // where the component has decided "there's an id" but is still
        // rendering the landing screen, or vice versa.
        const initialDesignId = params.design_id || restoredDesignId || null;

        // Task 34 (SBK-019) — sidebar collapse default. Decided ONCE
        // here, at boot, from the viewport width — deliberately NOT a
        // live-updating media-query watcher. A few sections up in this
        // file, the 2026-07-25 overflow fix exists specifically
        // because a continuously-enforced width rule on this same
        // workspace grid used to fire off a false-narrow signal from
        // the HOST backend window (DevTools split-screen), silently
        // wrecking the layout — see ".o_sbk_workspace" in the SCSS for
        // the full incident writeup. A one-shot default avoids
        // refighting that: from here on the rep has an explicit
        // toggle (see `_toggleControlsPanel`/`_toggleInventoryPanel`
        // below) that works regardless of how the host window is
        // sized. See `isNarrowViewportAtBoot` (pure, exported below)
        // for the guarded matchMedia check itself.
        const narrowAtBoot = isNarrowViewportAtBoot(
            typeof window !== "undefined" ? window : null);

        // ── Reactive state ────────────────────────────────────────────────────
        this.state = useState({
            loading:  true,
            // Task 20 (SBK-034) — 'landing' (no id: recent designs +
            // "New design", nothing generated/created yet), 'workspace'
            // (an id was supplied — resolve it; see onWillStart/
            // _hydrateFromDesign for the loading -> workspace-or-error
            // transition), or 'error' (the id didn't resolve — set only
            // by _failHydration, never by initial state). The template's
            // top-level branch reads this FIRST, before `loading` — the
            // landing screen has its own (non-blocking) fetch and must
            // never fall through to the loading spinner OR the 3D scene.
            screen: initialDesignId ? "workspace" : "landing",
            saving:   false,
            error:     "",
            errorCode: "",
            errorCta:  null,
            room: {
                width_in:  params.room_width_in  || 96,
                depth_in:  params.room_depth_in  || 96,
                height_in: params.room_height_in || 96,
            },
            // Task 12 (corner-remediation 2026-07-30, SBK-021/022) — one
            // inline validation message per room-dimension field, keyed
            // the same as `room` above. Empty string means "no error".
            // Set by `_changeRoom` when an out-of-range value is clamped;
            // cleared on the next in-range edit. Rendered next to the
            // offending field (o_sbk_field_error) — NOT a toast, per the
            // brief ("toast store ONLY for transient notices").
            roomDimErrors: { width_in: "", depth_in: "", height_in: "" },
            products:  [],
            items:     [],
            selected:  null,
            // Task 12 — same pattern as roomDimErrors above, for the
            // "Selected Cabinet" drawer's Width field. V5-partc SBK-021/
            // 022 claim 6: `_updateSelectedWidth` used to bare-early-
            // return on an out-of-range value with zero feedback.
            selectedWidthError: "",
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
            // Task 7 (SBK-033) — full bucket shape from boot, matching
            // southbrook.kitchen.design._get_counter_summary() 1:1 (see
            // countCabinetBuckets below). Every surface (header strip,
            // sidebar metrics, and — server-side — the backend Summary
            // group) renders from THIS one object; no surface computes
            // its own independent partition of state.items.
            summary:   {
                base_count: 0, wall_count: 0, tall_count: 0,
                corner_count: 0, filler_count: 0, panel_count: 0,
                appliance_count: 0, unplaced_count: 0,
                total: 0, price: 0, remainder_in: 0,
            },
            // PR2.5a (Gap 1) — falls back to the URL-restore-only
            // actionStack signal (see setup() comment above) when
            // params.design_id is absent (a direct-URL reload).
            designId:  initialDesignId,
            // Task 20 (SBK-034) — landing-screen recent-designs list.
            // Populated by _loadRecentDesigns(), fired from onWillStart
            // ONLY on the no-id boot path; never touched otherwise.
            recentDesigns:        [],
            recentDesignsLoading: false,
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
            // SBK-026 — 0 is the deliberate "unset" sentinel gating
            // 'to_soffit' (see kitchen_design.py's
            // _wall_cab_top_geometry). Previously defaulted to 84.0,
            // which meant 'to_soffit' silently worked even when nobody
            // had ever set a soffit height; falling back to 0 here
            // (same as a missing/blank param) keeps that gate honest.
            soffitHeightIn:      params.soffit_height_in || 0.0,
            // SBK-026 — counter thickness feeding the fixed-gap formula
            // (base carcass + counter thickness + 18" gap).
            counterThicknessIn:  params.counter_thickness_in || 1.5,
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
            // Panel P2 (catalog redesign) — category CHIP selection
            // (replaces the old <select>). "all" or one of the keys
            // groupCatalog() (catalog_list.esm.js) returns — never a
            // hardcoded set here, so a new cabinet_type surfaces its
            // own chip automatically. Chip click toggles this;
            // _filteredProducts() applies it BEFORE the search filter,
            // same "narrow then type" order the old tabs used.
            inventoryCategory: "all",
            // Panel P2 — width CHIP selection, mutually exclusive with
            // an implicit "all" (no dedicated All button in this row;
            // clicking the active chip again clears back to "all").
            // Values come from groupCatalog().widths, never hardcoded.
            inventoryWidth: "all",
            // Panel P2 — per-pair L/R handing toggle. Keyed by
            // "<left product_id>:<right product_id>" (stable across
            // re-renders, unlike the row object groupCatalog() rebuilds
            // every call) -> "L" | "R". Missing key = default "L" (see
            // _pairHanding). Browsing-only UI state, same family as
            // catalogPreview below — never written to a placed item.
            pairHanding: {},
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
            //     selected customer's label without a second RPC. Once
            //     a customer IS assigned, this is the ONLY thing the
            //     chip ever shows — never a typed-but-unselected value
            //     (see customerQuery below).
            //   portalUser is a memoized `!user.isInternalUser` — set
            //     once in setup() so it survives OWL reactive reads.
            //   picker* control the search dropdown's lifecycle (open,
            //     loading, result list, keyboard-highlighted row).
            partnerName:       params.partner_name || "",
            portalUser:        false,
            pickerOpen:        false,
            pickerLoading:     false,
            pickerResults:     [],
            // P3 — real res.partner autocomplete (retires the free-text
            // input pre-filled with the "Walk-in Customer" magic
            // string). customerQuery is the rep's IN-PROGRESS typed
            // text — deliberately a SEPARATE field from partnerName, so
            // a search that's abandoned (blur/Escape/close without
            // picking a result) can never leak into the assigned-
            // customer label; see _closeCustomerPicker/_setCustomer.
            // Nothing ever persists customerQuery itself — only a
            // clicked/Enter-selected result (or Walk-in / quick-create)
            // ever writes partnerId/partnerName.
            customerQuery:         "",
            pickerActiveIndex:     -1,
            // null = not checked yet (hidden), true/false = the ORM's
            // own answer for "can this user create a res.partner" —
            // gates the "Create '<query>'" quick-create row. Checked
            // lazily, once, the first time the picker actually opens
            // (_openCustomerPicker) — never on mount, so a test/session
            // that never touches the picker never fires this RPC.
            customerCreateAllowed: null,
            // SBK-030 — reactive render mirror of this._toastStore
            // (getView()'s shape). Kept inside the same `state` root as
            // everything else so the template's existing `state.foo`
            // convention holds; `_syncToastView()` is the only writer.
            toastView: { visible: [], overflowCount: 0 },
            // Task 34 (SBK-019) — independent collapse state for the
            // left "Room" controls aside and the right "Cabinet
            // Inventory" aside. See `narrowAtBoot` above for the
            // default and `_toggleControlsPanel`/
            // `_toggleInventoryPanel` for the explicit toggle every
            // width can always reach regardless of this default.
            controlsCollapsed:  narrowAtBoot,
            inventoryCollapsed: narrowAtBoot,
            // Task 35 — reactive mirror of this._undoStore's counts;
            // the toolbar's two history buttons key `disabled` off
            // these (0 = disabled). `_syncHistoryCounts()` is the only
            // writer.
            undoCount: 0,
            redoCount: 0,
            // Task 19 (corner-remediation 2026-07-30, SBK-032 + C9 UX) —
            // persistent production-readiness status chip. `counts` is
            // the server's OWN {blocking, warning} tally
            // (controllers/main.py /validate) — the chip's label/tone
            // (`_validationChip()`) derives from THIS, never from a
            // client-side recount of `issues`, so the two can never
            // disagree. `issues` already carries the server's dedupe +
            // this client's defensive second pass (dedupeIssues) and
            // each entry's `layout_keys` for click-to-select. Empty/
            // `ready: true` until the first successful fetch — a brand
            // new, never-saved session has nothing to validate yet
            // (the chip itself is hidden pre-save; see the template's
            // `t-if="state.designId"` gate).
            validation: {
                loading:   false,
                issues:    [],
                counts:    { blocking: 0, warning: 0 },
                ready:     true,
                panelOpen: false,
            },
            // Touch editing v1 — server kill-switch (spec §4 item 2),
            // read from /configurator/products' `touch_enabled` key
            // (T2's wiring — see _loadProducts). Defaults false so the
            // pad never flashes on before the boot RPC resolves.
            touchEnabledServer: false,
        });
        // Auto-save debounce timer (not reactive; managed imperatively).
        this._autoSaveTimer = null;
        // P3 — customer-search debounce timer (~250ms, see
        // _onCustomerTyped) + a monotonic request counter so a slow
        // early response can never clobber a faster later one
        // (_searchCustomers checks its own captured id against this
        // before writing state.pickerResults).
        this._customerSearchTimer = null;
        this._customerSearchSeq = 0;
        // Fix-round (coordinator review, corner-remediation 2026-07-30) —
        // dedup flag for _autoSave's structured-rejection toast (see
        // that method's own comment): true once an ongoing auto-save
        // failure has already been surfaced, reset to false the moment
        // any save (auto or manual) succeeds again.
        this._autoSaveErrorShown = false;
        // Task 38 (client hardening) — a follow-up auto-save requested
        // while one is already in flight (see _autoSave()'s own comment)
        // instead of being silently dropped.
        this._autoSavePending = false;
        // Task 38 (client hardening) — monotonic request id for
        // _refreshValidation()'s five fire-and-forget call sites
        // (hydrate/save/autosave/arrange/walk-in fix). Under real
        // latency those RPCs can resolve out of order; this guards
        // against a stale response overwriting a fresher one.
        this._validationReqSeq = 0;

        // Touch editing v1 — boot-time device-capability probe (same
        // "AtBoot" pattern as isNarrowViewportAtBoot above: evaluated
        // once, not reactively, since pointer capability doesn't
        // change mid-session on this surface). Plain instance field —
        // nothing renders it directly, only the derived
        // `_showTouchPad()` getter below does.
        this._touchCapable = isTouchCapableAtBoot(
            typeof window !== "undefined" ? window : null,
            typeof navigator !== "undefined" ? navigator : null,
        );
        // Touch editing v1 (spec §4 item 3) — accumulated gesture
        // health counters, flushed onto the next successful auto-save
        // (see _autoSave). NOT YET incremented anywhere: kitchen_canvas
        // .esm.js (sibling-owned; out of this task's edit scope) has no
        // gesture-rollback/abandon callback prop yet. `_recordTouchEvent`
        // below is the ready-to-call receiving end for whoever wires
        // that in — see this task's build note for the exact contract.
        this._touchStats = makeEmptyTouchStats();

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

        // Task 34 (SBK-020) — focus management for the two opened-
        // panel/drawer surfaces this component owns: the "Selected
        // Cabinet" edit drawer (.o_sbk_detail, gated on
        // `state.selected.layout_key`) and the catalog preview block
        // (.o_sbk_catalog_preview, gated on `state.catalogPreview`).
        // `useRef` targets are read inside the `onPatched` hook below
        // (once the DOM has the newly-opened panel), never read
        // synchronously here — the panel doesn't exist in the DOM
        // until OWL renders the state change that opens it.
        this.detailRef         = useRef("sbkDetailPanel");
        this.catalogPreviewRef = useRef("sbkCatalogPreview");
        // Whether each panel was open as of the LAST patch — compared
        // against the current render in `onPatched` to detect an
        // open/close TRANSITION (never re-steal focus on every update
        // to an already-open panel, e.g. re-selecting a different
        // already-open cabinet).
        this._panelWasOpen = { detail: false, catalogPreview: false };
        // The element that had focus right before each panel opened —
        // captured in `onPatched` at the moment of the open
        // transition (before this component ever calls `.focus()`),
        // restored when the panel closes. `document.contains(...)`
        // guards a since-removed opener (e.g. the "+ Add" button of a
        // row that scrolled out of a since-re-filtered list).
        this._panelOpener = { detail: null, catalogPreview: null };
        // Task 22's ←/→ cycling (`_cycleSelection`) and the
        // hydration/refresh auto-select-first-item paths
        // (`_hydrateFromDesign`, `_refreshLayout`) both write
        // `state.selected` WITHOUT a user gesture aimed at the
        // drawer — cycling is explicitly designed to keep keyboard
        // focus on the canvas (Task 22), and auto-select-on-load must
        // never yank focus into a drawer the rep never asked to open.
        // Both call sites set this true immediately before that
        // write; `onPatched` consumes (and always resets) it, so it
        // only ever suppresses the ONE patch it was set for.
        this._suppressDetailFocus = false;

        // Only _onKeyDown remains pre-bound — it attaches to `window`
        // so we need a stable reference for add/removeEventListener.
        // The five mouse/wheel binds excised at 24e moved into
        // <KitchenCanvas> at 24b/24c.
        this._onKeyDown = this._onKeyDown.bind(this);

        // ── Lifecycle ─────────────────────────────────────────────────────────
        onWillStart(async () => {
            // Task 20 (SBK-034) — the id in the boot params is the
            // source of truth, decided BEFORE any product/layout fetch
            // and BEFORE the 3D scene ever mounts (the template's
            // 'workspace' branch — the only one that renders
            // <KitchenCanvas> — is gated on state.screen, and this is
            // the only place besides _startNewDesign/_continueDesign/
            // _failHydration that writes it):
            //   - no id -> a minimal landing state (recent designs +
            //     "New design"). Nothing is generated, nothing is
            //     created, until the rep explicitly picks one of those
            //     two actions (_startNewDesign / _continueDesign) —
            //     this is what closes the audited "entering from the
            //     menu always silently starts a new design" defect.
            //     _loadRecentDesigns() is intentionally NOT awaited —
            //     it owns its own loading flag so the landing screen's
            //     first paint isn't gated on it either.
            //   - an id -> resolve it via _hydrateFromDesign(); success
            //     enters the workspace hydrated from the saved design,
            //     failure (missing/unreadable) is routed to state.screen
            //     = 'error' by _failHydration — NEVER a silently
            //     generated fresh design (the audited "reload abandons
            //     the saved design" defect). Product/defaults load in
            //     parallel with the hydrate attempt so first paint isn't
            //     gated on them once the record resolves.
            if (!this.state.designId) {
                this.state.loading = false;
                this._loadRecentDesigns();
                return;
            }
            await Promise.all([
                this._loadProducts(),
                this._loadUserDefaults(),
            ]);
            await this._hydrateFromDesign();
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
            // SBK-030 — auto-dismiss clock for non-sticky toasts. A
            // 1s tick is coarse enough to be free and fine-grained
            // enough that an ~8s auto-dismiss never visibly lags.
            // `.unref()` (Node-only; real browsers return a plain
            // number with no such method, hence the guard) keeps this
            // from holding a headless/test process open forever if a
            // caller never triggers onWillUnmount — it has no effect
            // on the interval actually firing while the page is alive.
            this._toastTimer = setInterval(() => this._tickToasts(), 1000);
            if (typeof this._toastTimer.unref === "function") {
                this._toastTimer.unref();
            }
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

        // Task 34 (SBK-020) — panel/drawer focus management. Runs after
        // EVERY re-render (cheap — a handful of boolean reads/compares
        // unless an open/close transition actually happened), looking
        // for the specific open<->close edges of the detail drawer and
        // the catalog preview block. See the field comments on
        // `detailRef`/`_panelWasOpen`/`_panelOpener`/
        // `_suppressDetailFocus` above for the full contract.
        onPatched(() => {
            this._syncPanelFocus(
                "detail",
                !!(this.state.selected && this.state.selected.layout_key),
                this.detailRef,
                this._suppressDetailFocus,
            );
            this._suppressDetailFocus = false;
            this._syncPanelFocus(
                "catalogPreview",
                !!this.state.catalogPreview,
                this.catalogPreviewRef,
                false,
            );
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
            // P3 — same rationale for the customer-search debounce.
            if (this._customerSearchTimer) {
                clearTimeout(this._customerSearchTimer);
                this._customerSearchTimer = null;
            }
            // SBK-030 — stop the toast auto-dismiss clock.
            if (this._toastTimer) {
                clearInterval(this._toastTimer);
                this._toastTimer = null;
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

    // ─── Task 35 — undo/redo stack (minimal default scope) ─────────────────────
    // Snapshot = the EXACT shape _autoSave() already sends for `room`
    // + `items` (brief: "reuse the EXACT state serialization the
    // autosave path already sends ... do not invent a second
    // serializer") — NOT the full autosave payload (name/design_id/
    // partner_id/filler_strategy/etc are session/global settings, not
    // per-cabinet design-mutating ops, and are out of this task's
    // scope). Every `state.items` entry is a flat record of primitives
    // (see _lineToItem / _addCabinetFromProduct — no nested
    // objects/arrays), and several call sites mutate an item IN PLACE
    // rather than replacing it in the array (e.g.
    // _updateSelectedWidth's `this.state.items[idx].width_in = v`), so
    // each item needs its OWN shallow copy here — copying just the
    // array would leave the snapshot pointing at the same mutable
    // objects the live state keeps editing.
    _snapshotState() {
        return {
            room: { ...this.state.room },
            items: (this.state.items || []).map(it => ({ ...it })),
        };
    }

    _pushUndoSnapshot() {
        this._undoStore.pushSnapshot(this._snapshotState());
        this._syncHistoryCounts();
    }

    _syncHistoryCounts() {
        this.state.undoCount = this._undoStore.undoCount;
        this.state.redoCount = this._undoStore.redoCount;
    }

    // Shared by _undo/_redo — replaces state.room/state.items wholesale
    // with a stored snapshot and re-derives everything downstream of
    // them exactly like every other full-replace path in this file
    // (_rearrangeOnServer, _hydrateFromDesign) does.
    _applyHistorySnapshot(snapshot) {
        this.state.room  = { ...snapshot.room };
        this.state.items = snapshot.items.map(it => ({ ...it }));
        // Best-effort selection preservation: keep the same cabinet
        // selected across the undo/redo if it still exists in the
        // restored snapshot, otherwise clear rather than point at a
        // stale/foreign object.
        const selKey = this.state.selected && this.state.selected.layout_key;
        this.state.selected = selKey
            ? (this.state.items.find(i => i.layout_key === selKey) || null)
            : null;
        this.state.selectedWidthError = "";
        this._recomputeLayoutFromItems();
    }

    // Ctrl/Cmd+Z and the toolbar's undo button. No-op (not even a
    // toast — an empty stack is communicated by the button's own
    // disabled state / nothing happening) when there's nothing to undo.
    _undo() {
        if (!this._undoStore.canUndo) return;
        this._markUserAction();
        const current = this._snapshotState();
        const snapshot = this._undoStore.undo(current);
        if (!snapshot) return;
        this._applyHistorySnapshot(snapshot);
        this._syncHistoryCounts();
        // A restored-to state is, by definition, a placement change
        // relative to whatever the design last had auto-arranged —
        // the next explicit Auto-arrange should confirm again.
        this._placementsDirty = true;
        // Brief: "Undo restores client state AND triggers the normal
        // autosave so the server converges" — the NORMAL (debounced)
        // path, not the synchronous flush-then-save variant
        // _swapCornerProductOnServer uses for its own different race
        // (see that method's comment) — no such race exists here,
        // this is a plain full-replace like any other edit.
        this._queueAutoSave();
    }

    // Ctrl/Cmd+Shift+Z, Ctrl/Cmd+Y, and the toolbar's redo button.
    _redo() {
        if (!this._undoStore.canRedo) return;
        this._markUserAction();
        const current = this._snapshotState();
        const snapshot = this._undoStore.redo(current);
        if (!snapshot) return;
        this._applyHistorySnapshot(snapshot);
        this._syncHistoryCounts();
        this._placementsDirty = true;
        this._queueAutoSave();
    }

    // Task 35 — explicit "Auto-arrange" toolbar command. Re-invokes
    // the SAME engine call a room resize already triggers implicitly
    // on a hydrated design (_rearrangeOnServer -> /rearrange ->
    // action_auto_arrange — no new server route) as its own
    // deliberate, on-demand action, gated by a confirmation dialog
    // (Odoo's ConfirmationDialog service — never window.confirm, see
    // the import comment) whenever shouldConfirmArrange() says the
    // design has manual placement changes since the last arrange that
    // this would discard. Either way — confirmed or skipped because
    // nothing's at risk — an undo snapshot is pushed BEFORE arranging
    // (brief: "arranging pushes an undo snapshot first, so even a
    // confirmed arrange is undoable").
    //
    // Scoping note (see task-35-report.md "Concerns" for the full
    // writeup): this button is the ONLY auto-arrange call site gated
    // behind the confirm dialog. The pre-existing IMPLICIT triggers
    // (_changeRoom, the canvas width/depth drag
    // handles) are left unguarded on purpose — those are routine,
    // continuous room-dimension edits where re-deriving the layout
    // through the engine is already the established, expected
    // behavior (T7/PR4b), and popping a confirmation dialog on every
    // drag-resize tick would make ordinary resizing unusable. "Auto-
    // arrange" in the brief's sense — the thing worth a confirm gate —
    // is this discrete, deliberate regenerate command (its dialog
    // literally offers "Arrange"/"Cancel", not "keep resizing").
    async _confirmedAutoArrange() {
        if (!this.state.designId || !this.state.items
                || !this.state.items.length) return;
        const doArrange = () => {
            this._pushUndoSnapshot();
            this._markUserAction();
            this._rearrangeOnServer().then((ok) => {
                if (ok) this._placementsDirty = false;
            });
        };
        if (!shouldConfirmArrange(this.state.items.length, this._placementsDirty)) {
            doArrange();
            return;
        }
        this.dialogService.add(ConfirmationDialog, {
            title: "Auto-arrange this design?",
            body: "This re-arranges every cabinet using the layout "
                + "engine. Manual positions or edits made since the "
                + "last arrange may be discarded.",
            confirmLabel: "Arrange",
            cancelLabel: "Cancel",
            confirm: () => doArrange(),
            cancel: () => {},
        });
    }

    // ─── Task 34 (SBK-020) — panel/drawer focus management ─────────────────────
    // Called from `onPatched` (see setup()) once per named panel, per
    // render. Detects the open<->close TRANSITION only — an
    // already-open panel receiving a content update (e.g. a different
    // cabinet becomes selected while the drawer is already showing)
    // does nothing here, matching the WAI-ARIA APG expectation that
    // opening a panel moves focus in ONCE, not on every internal
    // update.
    //   - opening (was closed, now open, not suppressed): remember
    //     whatever currently has focus as this panel's "opener", then
    //     move focus onto the panel container itself (it carries
    //     tabindex="-1" in the template — focusable programmatically,
    //     never part of the normal Tab order).
    //   - closing (was open, now closed): return focus to the
    //     captured opener, if it's still attached to the document
    //     (a design switch or list re-filter can remove it — silently
    //     doing nothing is the safe fallback, never throwing).
    _syncPanelFocus(key, isOpen, ref, suppress) {
        const wasOpen = this._panelWasOpen[key];
        if (isOpen && !wasOpen) {
            if (!suppress) {
                this._panelOpener[key] =
                    (typeof document !== "undefined" && document.activeElement) || null;
                if (ref.el && typeof ref.el.focus === "function") {
                    ref.el.focus({ preventScroll: true });
                }
            } else {
                // Suppressed open (keyboard cycling / auto-select on
                // load) — no opener to restore to later either.
                this._panelOpener[key] = null;
            }
        } else if (!isOpen && wasOpen) {
            const opener = this._panelOpener[key];
            this._panelOpener[key] = null;
            if (opener && typeof opener.focus === "function"
                && typeof document !== "undefined" && document.contains(opener)) {
                opener.focus({ preventScroll: true });
            }
        }
        this._panelWasOpen[key] = isOpen;
    }

    // ─── SBK-030 — local toast discipline ───────────────────────────────────────
    // The single funnel every message in this file uses. Mirrors the
    // shape `useService("notification").add(message, opts)` used to
    // take (`type`, `sticky`) so most call sites were a rename, not a
    // rewrite. `opts.dedupeKey` and `opts.actions` are new — see
    // toast_store.esm.js's header for the full contract.
    _toast(message, opts = {}) {
        this._toastStore.add(message, opts);
        this._syncToastView();
    }

    // Pushes the store's current view (cap+rollup already applied) into
    // reactive state so the template re-renders. The ONLY writer of
    // state.toastView.
    _syncToastView() {
        const { visible, overflowCount } = this._toastStore.getView();
        this.state.toastView.visible = visible;
        this.state.toastView.overflowCount = overflowCount;
    }

    _dismissToast(id) {
        this._toastStore.dismiss(id);
        this._syncToastView();
    }

    _dismissAllToasts() {
        this._toastStore.dismissAll();
        this._syncToastView();
    }

    // Runs on the ~1s interval armed in onMounted; a no-op render-wise
    // unless something actually expired.
    _tickToasts() {
        const removed = this._toastStore.tick(Date.now());
        if (removed.length) this._syncToastView();
    }

    // A toast action button (e.g. "Add anyway") always dismisses its
    // own toast on click unless the action opts out — the decision it
    // represents has been made, so the prompt has nothing left to say.
    _runToastAction(toast, action) {
        if (typeof action.onClick === "function") action.onClick();
        if (action.dismissOnClick !== false) this._dismissToast(toast.id);
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
                // Touch editing v1 — server kill-switch (T2's wiring;
                // see this route's own comment). `resp.touch_enabled`
                // is only absent on the legacy bare-array shape above,
                // which this `else` branch never takes.
                this.state.touchEnabledServer = !!resp.touch_enabled;
            }
        } catch (_) {
            this.state.products = [];
        }
    }

    // ─── Task #48 / P3 — Top-bar customer picker ─────────────────────────────────
    // P3 (2026-08-02, client-approved ticket) — replaced the free-text
    // input (pre-filled with the "Walk-in Customer" magic string,
    // advertising role="combobox" with no real listbox wiring) with a
    // real res.partner search combobox. `get_or_create_walkin_partner`
    // (model-side, Agent A) still backs the Walk-in button unchanged;
    // `get_recent_partners_for_picker` stays available server-side
    // (still covered by its own Python test) but is no longer called
    // from here — the "recent customers on focus" behavior is
    // superseded by real search-as-you-type.
    //
    // Never opens for portal users — Agent A's default_get already
    // auto-fills partner_id from user.partner_id, and we render a
    // reassurance pill ("Designing as {name}") in the same slot.
    async _openCustomerPicker() {
        if (this.state.portalUser) return;
        this.state.pickerOpen = true;
        // Lazy, once-per-session permission check (never on mount) —
        // gates the "Create '<query>'" quick-create row. Fire-and-
        // forget; _loadCustomerCreatePermission owns its own try/catch
        // so a denied/failed check never surfaces as an error, it just
        // keeps the quick-create row hidden.
        if (this.state.customerCreateAllowed === null) {
            this._loadCustomerCreatePermission();
        }
    }

    // @api.model check_access_rights('create', raise_exception=False) —
    // the same ORM gate the framework's own Many2one quick-create uses.
    // res.partner is a core model reachable by any authenticated
    // internal user's default ACLs; this only hides the row for a
    // locked-down group that's explicitly had create revoked.
    async _loadCustomerCreatePermission() {
        try {
            const allowed = await rpc("/web/dataset/call_kw", {
                model:  "res.partner",
                method: "check_access_rights",
                args:   ["create", false],
                kwargs: {},
            });
            this.state.customerCreateAllowed = !!allowed;
        } catch (_) {
            this.state.customerCreateAllowed = false;
        }
    }

    // Blur-driven close needs a small delay so a click on a dropdown
    // option gets its mousedown->click cycle before we tear down the
    // list. 150ms is enough for even a slow tap on tablet.
    _closeCustomerPicker() {
        setTimeout(() => { this.state.pickerOpen = false; }, 150);
    }

    // Debounced (~250ms) search-as-you-type. `state.customerQuery` is
    // the controlled input's OWN value — kept separate from
    // `state.partnerName` (the assigned customer's real label) so an
    // abandoned search never leaks into the assigned display and never
    // gets persisted (only `_setCustomer`/`_useWalkInCustomer`/
    // `_createCustomerFromQuery` ever write partnerId/partnerName).
    _onCustomerTyped(ev) {
        const q = ev.target.value || "";
        this.state.customerQuery = q;
        this.state.pickerActiveIndex = -1;
        if (this._customerSearchTimer) {
            clearTimeout(this._customerSearchTimer);
            this._customerSearchTimer = null;
        }
        if (!q.trim()) {
            // Nothing typed — clear results immediately, no RPC needed.
            this.state.pickerResults = [];
            this.state.pickerLoading = false;
            return;
        }
        this.state.pickerLoading = true;
        this._customerSearchTimer = setTimeout(() => {
            this._customerSearchTimer = null;
            this._searchCustomers(q);
        }, 250);
    }

    // web_search_read against res.partner's name/email/phone/ref — the
    // exact fields the brief calls out (name_search's own rec_names_
    // search on this model covers name/email/ref/vat but NOT phone, so
    // a hand-built domain is the only way to cover all four in one
    // round trip). Limit 8; secondary line is city, falling back to
    // email, matching the brief's "secondary line city-or-email".
    // `requestId` guards against a slow early response clobbering a
    // faster later one if the rep keeps typing.
    async _searchCustomers(query) {
        const q = (query || "").trim();
        if (!q) {
            this.state.pickerResults = [];
            this.state.pickerLoading = false;
            return;
        }
        const requestId = ++this._customerSearchSeq;
        try {
            const domain = ["|", "|", "|",
                ["name", "ilike", q], ["email", "ilike", q],
                ["phone", "ilike", q], ["ref", "ilike", q]];
            const resp = await rpc("/web/dataset/call_kw", {
                model:  "res.partner",
                method: "web_search_read",
                args:   [domain, { name: {}, email: {}, phone: {}, city: {} }],
                kwargs: { limit: 8 },
            });
            if (requestId !== this._customerSearchSeq) return;   // superseded
            const records = (resp && resp.records) || [];
            this.state.pickerResults = records.map((r) => ({
                id:        r.id,
                name:      r.name || "",
                secondary: r.city || r.email || "",
            }));
        } catch (e) {
            if (requestId !== this._customerSearchSeq) return;
            console.warn("[SouthbrookKitchenConfigurator] customer search failed:", e);
            this.state.pickerResults = [];
        } finally {
            if (requestId === this._customerSearchSeq) this.state.pickerLoading = false;
        }
    }

    // Full combobox a11y keyboard contract (WAI-ARIA APG): Up/Down move
    // the highlighted row, Enter picks it, Escape closes without
    // touching the query or any assignment. Task 34 (SBK-020) note
    // (unchanged from the prior implementation) — the global window
    // keydown handler (`_onKeyDown`) bails out immediately for any
    // INPUT target, so Esc-to-close needs its own listener scoped to
    // this field; focus never leaves the input, so there's nothing to
    // restore.
    _onCustomerInputKeydown(ev) {
        if (!this.state.pickerOpen) {
            // Arrow keys open the dropdown even before the first
            // keystroke lands a search (mirrors native <select>/
            // combobox expectations); every other key here is a no-op
            // when already closed, INCLUDING Escape — no dropdown, no
            // preventDefault, nothing to swallow.
            if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                ev.preventDefault();
                this._openCustomerPicker();
            }
            return;
        }
        const n = (this.state.pickerResults || []).length;
        if (ev.key === "ArrowDown") {
            ev.preventDefault();
            if (n) this.state.pickerActiveIndex = (this.state.pickerActiveIndex + 1) % n;
        } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            if (n) this.state.pickerActiveIndex =
                (this.state.pickerActiveIndex - 1 + n) % n;
        } else if (ev.key === "Enter") {
            const picked = this.state.pickerResults[this.state.pickerActiveIndex];
            if (picked) {
                ev.preventDefault();
                this._setCustomer(picked);
            }
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            this.state.pickerOpen = false;
        }
    }

    // The active option's element id, for aria-activedescendant — undefined
    // (never an empty string) so the attribute is fully ABSENT when
    // nothing is keyboard-highlighted, same convention as every other
    // undefined-means-omit t-att in this template.
    _customerActiveOptionId() {
        const idx = this.state.pickerActiveIndex;
        const list = this.state.pickerResults || [];
        return (idx >= 0 && list[idx]) ? ("sbkCustomerOpt" + list[idx].id) : undefined;
    }

    // Hides "Create '<query>'" once the typed text already exactly
    // matches a real search result — no point offering to create a
    // near-duplicate the rep almost certainly means to just click.
    _customerExactMatch() {
        const q = (this.state.customerQuery || "").trim().toLowerCase();
        if (!q) return false;
        return (this.state.pickerResults || []).some(
            (r) => (r.name || "").trim().toLowerCase() === q);
    }

    // Resets every piece of SEARCH-only state (never partnerId/
    // partnerName — those are the caller's job) — shared by
    // _setCustomer and _unassignCustomer so the dropdown always comes
    // back empty/closed rather than showing a stale query or result set.
    _resetCustomerSearch() {
        this.state.pickerOpen        = false;
        this.state.customerQuery     = "";
        this.state.pickerResults     = [];
        this.state.pickerActiveIndex = -1;
        if (this._customerSearchTimer) {
            clearTimeout(this._customerSearchTimer);
            this._customerSearchTimer = null;
        }
    }

    async _setCustomer(partner) {
        if (!partner || !partner.id) return;
        this.state.partnerId    = partner.id;
        this.state.partnerName  = partner.name || "";
        this._resetCustomerSearch();
        // Refetching /products picks up the new channel pricelist and
        // repaints the inventory prices; /layout follows so the summary
        // and any per-item price fields also update.
        await this._loadProducts();
        await this._refreshLayout();
        // Task 38 (client hardening) — _queueAutoSave() no-ops until
        // _userActed is true (the T7 gate). On a reopened, not-yet-
        // edited design, assigning a customer (walk-in or from the
        // picker — both routes land here, the guard above already
        // guarantees a real partner is being set) was silently
        // swallowed by that gate: the partner never got persisted and
        // the validation panel kept showing MISSING_CUSTOMER until some
        // later, unrelated edit opened the gate. Assigning a customer
        // IS a deliberate user action — open the gate before queueing.
        this._markUserAction();
        // Persist server-side so the design carries the new partner
        // into the next session even if the browser refreshes before
        // the debounced auto-save fires.
        this._queueAutoSave();
    }

    // The chip's (x) — unassigns back to the empty/search state. Never
    // deletes the res.partner record itself, only this design's link
    // to it.
    async _unassignCustomer() {
        this.state.partnerId   = null;
        this.state.partnerName = "";
        this._resetCustomerSearch();
        await this._loadProducts();
        await this._refreshLayout();
        this._queueAutoSave();
    }

    // The chip's name — opens the assigned contact's form via the
    // action service, as a dialog so the rep's in-progress design state
    // (unsaved room/cabinet edits) is never disturbed by a full
    // navigation away from the configurator.
    _openCustomerForm() {
        if (!this.state.partnerId) return;
        this.action.doAction({
            type:      "ir.actions.act_window",
            res_model: "res.partner",
            res_id:    this.state.partnerId,
            views:     [[false, "form"]],
            target:    "new",
        });
    }

    // Quick-create — gated on state.customerCreateAllowed (see
    // _loadCustomerCreatePermission); the template hides the row
    // entirely on denial, this is belt-and-suspenders against a stale
    // render/direct call.
    async _createCustomerFromQuery() {
        const name = (this.state.customerQuery || "").trim();
        if (!name || !this.state.customerCreateAllowed) return;
        this.state.pickerLoading = true;
        try {
            const id = await rpc("/web/dataset/call_kw", {
                model:  "res.partner",
                method: "create",
                args:   [{ name }],
                kwargs: {},
            });
            const pid = parseInt(id, 10);
            if (Number.isFinite(pid) && pid > 0) {
                await this._setCustomer({ id: pid, name });
            }
        } catch (e) {
            this._toast(
                "Couldn't create customer: " + (e.message || e),
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.pickerLoading = false;
        }
    }

    async _useWalkInCustomer() {
        this.state.pickerLoading = true;
        try {
            // SBK-031 — get_or_create_walkin_partner now returns
            // {id, name} (was a bare id): the resolved partner's REAL
            // name (server-side it may resolve to the legacy "Walk-in
            // Customer" record on an older DB, or the canonical
            // "Walk-in Retail" one) is what the chip shows. P3 retires
            // the old hardcoded "Walk-in Customer" label this call site
            // used to pass regardless of which record actually got
            // resolved — that mismatch WAS the "magic-string prefill"
            // bug. `|| "Walk-in Customer"` below is only a defensive
            // fallback for a still-bare-id response shape; it's never
            // what production returns.
            const resp = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design",
                method: "get_or_create_walkin_partner",
                args:   [],
                kwargs: {},
            });
            const pid = parseInt(resp && resp.id !== undefined ? resp.id : resp, 10);
            if (Number.isFinite(pid) && pid > 0) {
                const name = (resp && typeof resp === "object" && resp.name)
                    || "Walk-in Customer";
                await this._setCustomer({ id: pid, name });
            }
        } catch (e) {
            this._toast(
                "Couldn't set walk-in customer: " + (e.message || e),
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.pickerLoading = false;
        }
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
        return {
            ...line,
            name: line.product_name,
            default_code: defaultCode,
            // E2E P1 fix — the detail-panel SKU/Material/BoM dt/dd
            // (~L5338-5358) read these three keys straight off
            // state.selected. /layout-generated items (_product_payload)
            // already carry them; a HYDRATED line (this method's only
            // caller) previously left them undefined whenever the
            // server line dict didn't include them, rendering "—" even
            // when the underlying product resolves to a real SKU.
            // Explicit default here (never relying on the bare `...line`
            // spread above) so every item this method ever returns has
            // all three keys, whatever the server payload does or
            // doesn't send.
            sku:           line.sku || "",
            material:      line.material || "",
            bom_available: !!line.bom_available,
        };
    }

    // Task 20 (SBK-034) — the single place every "the id in the URL/
    // params doesn't resolve to a design this session can use" path
    // funnels through, whether that's a thrown RPC (transient failure,
    // see the catch block below — `code` defaults to the string this
    // path has always used, "HYDRATION_FAILED", so existing behavior/
    // tests are unchanged) or an explicit NOT_FOUND/ACCESS_DENIED
    // response from load_design_lines (new). Routes state.screen to
    // 'error' — a dedicated blocking screen, never the workspace/scene
    // — and hard-gates every write path via `_hydrationFailed` exactly
    // like the pre-existing RPC-failure path already did.
    _failHydration(message, code = "HYDRATION_FAILED") {
        this._hydrationFailed = true;
        this.state.error = message;
        this.state.errorCode = code;
        this.state.errorCta = null;
        this.state.screen = "error";
    }

    async _hydrateFromDesign() {
        try {
            const resp = await rpc(
                "/southbrook_kitchen/configurator/load_design_lines",
                { design_id: this.state.designId },
            );
            // Task 20 (SBK-034) — an explicit error_code means the id
            // does not resolve to a design this user can read right
            // now: NOT_FOUND (deleted / never existed) or
            // ACCESS_DENIED (ir.rule/ACL says no — controllers/main.py
            // load_design_lines). Must be checked BEFORE the "no
            // lines" branch below: both shapes carry a bare `lines: []`
            // array, and without this check "doesn't exist" and
            // "exists but is empty" are indistinguishable — reading
            // the former as the latter is exactly the silent-fresh-
            // design bug this task closes (the generator would run
            // over an id that was never a real, readable design).
            if (resp && resp.error_code === "NOT_FOUND") {
                this._failHydration(
                    resp.error || "This design no longer exists.",
                    "NOT_FOUND");
                return;
            }
            if (resp && resp.error_code === "ACCESS_DENIED") {
                this._failHydration(
                    resp.error || "You don't have access to this design.",
                    "ACCESS_DENIED");
                return;
            }
            this.state.screen = "workspace";
            // Task 19 — the design resolved (readable, whether or not
            // it carries any lines yet — an empty design still has its
            // own EMPTY_DESIGN issue to surface). Refresh the status
            // chip now rather than waiting for the rep's first edit;
            // fire-and-forget, same as `_loadRecentDesigns()` above.
            this._refreshValidation();
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
            // E2E P0 fix — same additive-backfill shape as room/
            // design_name just above: a live button-click open always
            // carries `partner_name` via action params (setup(), ~L471);
            // a direct-URL reload loses every param except the design
            // id, so `state.partnerName` is still "" here in that case.
            // Backfill from the server's own echo so the topbar customer
            // chip (guarded on state.partnerId, template ~L4409) never
            // renders a populated pill with a blank name. Never clobber
            // a value params already supplied (`!this.state.partnerName`
            // mirrors the `_hasExplicit*` guards' precedence rule — this
            // function runs once, before any user customer-picker
            // action, so an already-truthy partnerName here can only
            // have come from params).
            if (resp && resp.partner_name && !this.state.partnerName) {
                this.state.partnerName = resp.partner_name;
            }
            // Task 7 (SBK-033) — seed state.summary from the server's
            // authoritative _get_counter_summary() before the local
            // recompute below runs. Belt-and-suspenders: _recomputeLayout
            // FromItems() (called a few lines down once state.items is
            // set) immediately recomputes the same shape from the same
            // data and supersedes this, but a hydrate that short-circuits
            // before reaching that call (see the `!lines.length` branch
            // just below) still ends up with real numbers instead of the
            // boot placeholder's all-zero shape.
            if (resp && resp.summary) {
                this.state.summary = resp.summary;
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
                // Task 34 (SBK-020) — this is an auto-select on load,
                // not a user gesture aimed at the drawer; don't yank
                // focus into it on boot (see _syncPanelFocus).
                this._suppressDetailFocus = true;
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
            this._failHydration(
                "Couldn't load the saved design — reload the page. "
                + "Saving is disabled to protect your design.");
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
                // SBK-026 — 0 is "unset"; must not `|| 84.0` this back up
                // (see state-init comment above for why).
                soffit_height_in:        this.state.soffitHeightIn || 0.0,
                counter_thickness_in:    this.state.counterThicknessIn || 1.5,
            });
            this.state.error      = result.error || "";
            this.state.errorCode  = result.error_code || "";
            this.state.errorCta   = result.error_cta || null;
            this.state.items      = result.items  || [];
            this.state.summary    = result.summary || this.state.summary;
            this.state.warnings   = result.warnings || [];
            if (result.channel) this.state.channel = result.channel;
            if (!this.state.selected && this.state.items.length) {
                // Task 34 (SBK-020) — same auto-select-on-generate
                // reasoning as the hydration path above.
                this._suppressDetailFocus = true;
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

    // ─── Task 20 (SBK-034) — landing-state entry points ─────────────────────────
    // Fetches the current user's recent designs for the landing screen's
    // "Continue" list. Fired once from onWillStart on the no-id boot
    // path only (never re-fired automatically); _backToLanding() below
    // re-fires it when the rep bounces back from an error screen. Owns
    // its own loading flag so the landing screen's first paint is never
    // gated on this RPC.
    async _loadRecentDesigns() {
        this.state.recentDesignsLoading = true;
        try {
            const resp = await rpc(
                "/southbrook_kitchen/configurator/recent_designs", {});
            this.state.recentDesigns = Array.isArray(resp) ? resp : [];
        } catch (e) {
            // Non-fatal — the "New Design" button still works with an
            // empty/failed recent-designs list.
            console.warn(
                "[SouthbrookKitchenConfigurator] recent_designs fetch failed:", e);
            this.state.recentDesigns = [];
        } finally {
            this.state.recentDesignsLoading = false;
        }
    }

    // Shared by _startNewDesign/_continueDesign — the landing screen
    // lets a rep bounce between designs (or bounce to New) WITHOUT a
    // full component remount, a scenario that essentially didn't exist
    // before this task (previously the only way to switch designs was
    // the "Kitchen Designs" list's doAction, which always destroys and
    // remounts the whole component, wiping state for free). Without
    // this, a stale `state.selected`/`state.items` entry from whatever
    // was on screen before landing could survive into the new session:
    // _hydrateFromDesign/_refreshLayout only backfill `state.selected`
    // when it's currently falsy (`if (!this.state.selected && ...)`),
    // so a leftover selection from a PRIOR design would silently stick
    // around pointing at an object that isn't in the new items array —
    // the edit drawer's `findIndex(it => it.layout_key === sel.
    // layout_key)` guard (the same F14 defect class this codebase has
    // already fixed elsewhere) would then make every drawer action a
    // silent no-op against the new design. Also clears any debounced
    // autosave timer left armed by the design being left behind — same
    // cleanup onWillUnmount already does for a real unmount.
    _resetForNewSession() {
        if (this._autoSaveTimer) {
            clearTimeout(this._autoSaveTimer);
            this._autoSaveTimer = null;
        }
        this.state.items = [];
        this.state.selected = null;
        this.state.catalogPreview = null;
        this.state.activeWall = null;
        this.state.warnings = [];
        this.state.designName = "";
        this.state.hydratedFromDesign = false;
        this._hydratedFromDesign = false;
        this._hydrationFailed = false;
        this._userActed = false;
        // Task 34 (SBK-020) — a session switch invalidates any opener
        // captured for the OLD session's panels; the elements it
        // pointed at belong to a design that's about to disappear.
        // Discarding them here means the close-transition
        // `_syncPanelFocus` runs on the state clears above (drawer was
        // open -> now null) resolves to a harmless no-op instead of
        // trying to refocus something from a design that's gone.
        this._panelOpener.detail = null;
        this._panelOpener.catalogPreview = null;
        // Task 35 — brief: "stack cleared on design switch/reload".
        // Both _startNewDesign and _continueDesign route through here.
        // A page reload doesn't need this — it constructs a brand new
        // component instance, so setup()'s own fresh `new UndoStore()`
        // / `_placementsDirty = true` already start clean.
        this._undoStore.clear();
        this._placementsDirty = true;
        this.state.undoCount = 0;
        this.state.redoCount = 0;
    }

    // The landing screen's "New Design" button. This — a genuine,
    // explicit user click — is the ONLY place a brand-new session's
    // product catalog + starter layout are ever fetched/generated on
    // the no-id path; onWillStart itself never calls this. Nothing here
    // creates a database record either — /layout (via _refreshLayout)
    // is a pure in-memory generator, exactly as it always was; the
    // first real write only happens on an explicit Save (see
    // _saveDesign/_autoSave), unchanged by this task.
    async _startNewDesign() {
        this._resetForNewSession();
        this.state.screen = "workspace";
        this.state.loading = true;
        this.state.error = "";
        this.state.errorCode = "";
        await Promise.all([
            this._loadProducts(),
            this._loadUserDefaults(),
        ]);
        await this._refreshLayout();
        this.state.loading = false;
    }

    // The landing screen's "Continue <design>" action — reopens one of
    // the rep's recent designs in place, through the exact same
    // _hydrateFromDesign() path a deep link takes (including its
    // NOT_FOUND/ACCESS_DENIED handling, in case the list is stale by
    // the time it's clicked).
    async _continueDesign(designId) {
        if (!designId) return;
        this._resetForNewSession();
        this.state.designId = designId;
        this.state.screen = "workspace";
        this.state.loading = true;
        this.state.error = "";
        this.state.errorCode = "";
        await Promise.all([
            this._loadProducts(),
            this._loadUserDefaults(),
        ]);
        await this._hydrateFromDesign();
        this.state.loading = false;
    }

    // Bounces the error screen (bad/unreadable design id) back to the
    // landing list. Clears the bad id so it can never leak into a
    // later save, and refreshes the recent-designs list in case the
    // stale entry that led here needs to disappear from it too.
    _backToLanding() {
        this.state.screen = "landing";
        this.state.designId = null;
        this.state.error = "";
        this.state.errorCode = "";
        this._hydrationFailed = false;
        this._loadRecentDesigns();
    }

    // ─── Task 34 (SBK-019) — collapsible side panels ───────────────────────────
    // Independent toggles for the left "Room" controls aside and the
    // right "Cabinet Inventory" aside — see `narrowAtBoot` in setup()
    // for the default and the SCSS (.o_sbk_workspace.is-*-collapsed)
    // for how each state maps to the grid layout. Both stay reachable
    // from the topbar regardless of viewport width or which panel is
    // currently hidden.
    _toggleControlsPanel() {
        this.state.controlsCollapsed = !this.state.controlsCollapsed;
    }

    _toggleInventoryPanel() {
        this.state.inventoryCollapsed = !this.state.inventoryCollapsed;
    }

    // ─── Templates (T3): top-frame entry to the four-dropdown picker ────────────
    async _openTemplatePicker() {
        // v19 doAction REQUIRES explicit views (memory: doaction_views_required)
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Start Custom",
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
                this._toast(res.error, { type: "warning" });
                return false;
            }
            if (res && res.error) {
                this._toast(res.error, { type: "danger" });
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
            // Task 19 — brief: status chip "refreshed after save/
            // arrange". Arranging can add/drop corner-derived lines and
            // change wall-run coordination, so the readiness picture
            // may have genuinely changed. Fire-and-forget.
            this._refreshValidation();
            return true;
        } catch (e) {
            console.warn(
                "[SouthbrookKitchenConfigurator] rearrange failed:", e);
            this._toast(
                "Room resize failed — nothing was changed.",
                { type: "danger" });
            return false;
        }
    }

    _usesEngineResize() {
        return !!(this.state.designId && this._hydratedFromDesign);
    }

    // Task 12 (corner-remediation 2026-07-30, SBK-021/022) — mirrors the
    // server's ROOM_WIDTH_MIN_IN/MAX_IN etc. (models/kitchen_design.py,
    // controllers/main.py save_design). One lookup table for every
    // client-side room-dimension clamp. Not the model constraint itself (that's server-side and
    // authoritative) — this only prevents an obviously-bad value from
    // ever leaving the browser, and gives the honest inline message
    // V5-partc's claim 6 found missing entirely.
    static ROOM_DIM_BOUNDS = {
        width_in:  { min: 12, max: 480 },
        depth_in:  { min: 12, max: 480 },
        height_in: { min: 84, max: 144 },
    };

    async _changeRoom(field, raw) {
        const v = parseFloat(raw);
        if (!Number.isFinite(v)) return;
        this._markUserAction();
        const bounds = SouthbrookKitchenConfigurator.ROOM_DIM_BOUNDS[field]
            || { min: 12, max: 480 };
        // Out-of-range input is CLAMPED (never silently dropped — the
        // pre-fix behavior floored at a minimum but had NO ceiling at
        // all) AND flagged with an inline message next to the field
        // (state.roomDimErrors, rendered by the template's
        // o_sbk_field_error span) instead of just silently correcting
        // the number with no feedback.
        this.state.roomDimErrors[field] = (v < bounds.min || v > bounds.max)
            ? `Must be ${bounds.min}"–${bounds.max}".`
            : "";
        this.state.room[field] = Math.min(bounds.max, Math.max(bounds.min, v));
        if (this._usesEngineResize()) {
            await this._rearrangeOnServer();   // persists server-side; no auto-save needed
            return;
        }
        await this._refreshLayout({ force: true });   // room resize regenerates, saved designs included
        this._queueAutoSave();    // D5
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
        // observes state.selected via onWillUpdateProps and applies
        // the selection visuals (tint + outline + bbox — Task 22)
        // against its own scene.
        this.state.selected = item;
        // F14 — a real layout-cabinet selection supersedes any inventory
        // row catalog-preview highlight.
        this.state.catalogPreview = null;
        // Task 12 — a stale width-validation message from a PREVIOUS
        // selection must not bleed onto a newly-selected cabinet's Width
        // field.
        this.state.selectedWidthError = "";
    }

    // ─── Task 19 (corner-remediation 2026-07-30, SBK-032 + C9 UX) ──────────────
    // Persistent production-readiness status chip. `/validate` is only
    // meaningful once the design is actually persisted (`_check_
    // production_ready` runs against the DB record); a brand-new,
    // never-saved session has nothing to fetch, and the topbar chip
    // itself is hidden until `state.designId` exists (see the
    // template). Fire-and-forget from every call site below (hydrate/
    // save/auto-save/arrange) — same pattern as `_loadRecentDesigns()`
    // in onWillStart: the chip updating a beat after the action that
    // triggered it is fine, gating anything on it is not.
    async _refreshValidation() {
        if (!this.state.designId) return;
        this.state.validation.loading = true;
        // Task 38 (client hardening) — this method is fired-and-forgotten
        // from five call sites (hydrate/save/autosave/arrange/walk-in
        // fix); under real-world latency two of those RPCs can resolve
        // in a different order than they were issued. A monotonic
        // request id lets a response detect it's been superseded by a
        // newer request BEFORE it overwrites state.validation with
        // stale data.
        const seq = ++this._validationReqSeq;
        try {
            const resp = await rpc("/southbrook_kitchen/configurator/validate", {
                design_id: this.state.designId,
            });
            if (seq !== this._validationReqSeq) {
                // A newer _refreshValidation() call has since been
                // issued (and may already have landed) — this response
                // is stale, discard it rather than clobber whatever the
                // newer request wrote (or is about to write).
                return;
            }
            if (!resp || resp.error_code) {
                // A design that stopped existing/being readable mid-
                // session (e.g. deleted from another tab) — the
                // hydrate/save paths already own surfacing THAT failure
                // mode; the chip just quietly stops updating rather
                // than opening a second error channel.
                return;
            }
            this.state.validation.issues = dedupeIssues(resp.issues || []);
            this.state.validation.counts = resp.counts
                || { blocking: 0, warning: 0 };
            this.state.validation.ready = !!resp.ready;
        } catch (e) {
            console.warn(
                "[SouthbrookKitchenConfigurator] validate failed:", e);
        } finally {
            // Fix round 1 (review) — this used to clear `loading`
            // unconditionally. With 3+ overlapping calls, a very-stale
            // request (seq 1 of 3) could settle AFTER a newer one (seq
            // 2) already applied its data but BEFORE the newest (seq 3)
            // has come back — clearing `loading` here would flip the
            // indicator off while seq 3 is still genuinely in flight.
            // Only the request that currently matches the live counter
            // (i.e. no newer request has been issued since) may own
            // this write; every other settlement — earlier or later in
            // wall-clock time — leaves it alone.
            if (seq === this._validationReqSeq) {
                this.state.validation.loading = false;
            }
        }
    }

    // Pure chip-state derivation, delegated straight to the reducer
    // (brief: "chip state derivation (pure function)") — this wrapper
    // only supplies the live counts from state.
    _validationChip() {
        return deriveChipState(this.state.validation.counts);
    }

    // Panel content, grouped by severity (brief: "panel grouped by
    // severity") — blocking first.
    _groupedValidationIssues() {
        return groupBySeverity(this.state.validation.issues);
    }

    _toggleValidationPanel() {
        this.state.validation.panelOpen = !this.state.validation.panelOpen;
    }

    // Click-to-select (brief AC: "click selects the offending cabinet").
    // Reuses the EXACT selection mechanism Task 22 already built
    // (state.selected + <KitchenCanvas>'s own reveal of it) via
    // `_selectCabinet` — no second selection path. An issue whose
    // line(s) aren't in `state.items` (deleted since the check ran, or
    // a manual/backend-origin line the 3D canvas never rendered) is a
    // silent no-op: the template only renders this as a clickable
    // button when `layout_keys.length`, so reaching here with nothing
    // matching is the stale-data edge case, not the common path.
    _selectValidationIssue(issue) {
        const keys = (issue && issue.layout_keys) || [];
        if (!keys.length) return;
        const item = (this.state.items || []).find(
            (it) => keys.includes(it.layout_key));
        if (!item) return;
        this._selectCabinet(item);
        this.state.validation.panelOpen = false;
    }

    // Review fix (fix round 1, Minor) — the panel's one wired fix
    // action. Calls the EXACT existing `_useWalkInCustomer()` flow
    // (Task 18) unchanged — never a second implementation — but,
    // unlike the topbar customer-picker's own walk-in button (which
    // only needs to close ITS OWN dropdown), this call site also needs
    // to close the validation panel and re-fetch /validate once the
    // assignment lands, or the panel keeps showing the now-stale
    // MISSING_CUSTOMER row until the next save/arrange happens to
    // refresh it. `_useWalkInCustomer` itself never throws (it catches
    // and toasts internally) and never rejects, so awaiting it here is
    // always safe — no separate try/catch needed.
    async _fixMissingCustomerFromPanel() {
        await this._useWalkInCustomer();
        this.state.validation.panelOpen = false;
        await this._refreshValidation();
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

    // P2 icons-everywhere — explicit dismiss for the catalog preview/
    // detail block's × control. Same field a real selection already
    // clears (F14, _selectCabinet) — this is the user-driven equivalent
    // for when no selection follows the preview.
    _dismissCatalogPreview() {
        this.state.catalogPreview = null;
    }

    // The previewed product's OWN icon (not a synthetic family icon
    // like _metricIcon above) — cabinetThumbSVG is a pure function of
    // the product's own fields, so this can't be memoized at module
    // load the way the counter-strip icons are; it's cheap enough to
    // rebuild per render, same as the inventory row thumbs.
    _catalogPreviewMarkup() {
        return markup(cabinetThumbSVG(this.state.catalogPreview, 88));
    }

    // Panel P2 stripped the parenthetical attribute boilerplate off
    // every list row's title (parseProductName) to keep the list tight;
    // this detail block is where that stripped detail belongs — shown
    // on demand for the one product being looked at, not repeated on
    // every one of the ~48 rows.
    _catalogPreviewParsed() {
        return parseProductName(this.state.catalogPreview && this.state.catalogPreview.name);
    }

    _catalogPreviewWidthText() {
        const p = this.state.catalogPreview;
        if (!p) return "";
        const w = liftWidth(p, this._catalogPreviewParsed().attrs);
        return w > 0 ? this._fmtWidthIn(w) : "width n/a";
    }

    // ─── D13 / Panel P2 — Searchable, grouped inventory + drag-and-drop add ─────
    // Panel P2 (catalog redesign) replaced the old flat, per-product,
    // <select>-filtered list with groupCatalog()'s grouped/L-R-merged/
    // sorted shape (catalog_list.esm.js). The old INVENTORY_CATEGORY_MAP
    // tab logic (a hand-maintained cabinet_type -> tab-key map living
    // only in this file) is gone — chips now render straight off
    // groupCatalog()'s own `categories` array, so the chip set and the
    // grouped list's own section headers can never disagree (one
    // source of truth instead of two parallel filter systems).
    //
    // _catalogGroups() is the single call site for groupCatalog() on
    // the FULL raw catalog; chip metadata (both rows) is read from it
    // so counts stay stable regardless of the currently active filter
    // — same "a typed search must not shrink the tab counts" rule the
    // pre-P2 select used, now extended to the width row too.
    _catalogGroups() {
        return groupCatalog(this.state.products || []);
    }

    _categoryChips() {
        return this._catalogGroups().categories;
    }

    _widthChips() {
        return this._catalogGroups().widths;
    }

    _categoryCount(key) {
        if (key === "all") return this._catalogGroups().total;
        const cat = this._catalogGroups().categories.find((c) => c.key === key);
        return cat ? cat.count : 0;
    }

    _setInventoryCategory(key) {
        this.state.inventoryCategory = key;
    }

    // Width row is mutually-exclusive-single-select with an IMPLICIT
    // "all" — there's no dedicated All chip taking up row space;
    // clicking the already-active chip clears the filter back to "all".
    _setInventoryWidth(w) {
        this.state.inventoryWidth = (this.state.inventoryWidth === w) ? "all" : w;
    }

    // product_id -> category key, derived from groupCatalog()'s OWN
    // bucketing (never re-implemented here) so the category chip
    // filter can never disagree with the grouped list's own headers.
    _categoryMembership() {
        const map = new Map();
        for (const cat of this._catalogGroups().categories) {
            for (const row of cat.rows) {
                if (row.kind === "pair") {
                    if (row.left)  map.set(row.left.product_id, cat.key);
                    if (row.right) map.set(row.right.product_id, cat.key);
                } else if (row.product) {
                    map.set(row.product.product_id, cat.key);
                }
            }
        }
        return map;
    }

    // The single filter funnel: search (via searchText()/
    // parseProductName() so the now-hidden attribute boilerplate still
    // matches) + category chip + width chip, all applied to the raw
    // product list. Feeds groupCatalog() (see _groupedInventory) for
    // the actual rendered, grouped/L-R-merged list.
    _filteredProducts() {
        const q = (this.state.inventorySearch || "").toLowerCase().trim();
        const catKey = this.state.inventoryCategory || "all";
        const widthKey = this.state.inventoryWidth || "all";
        const list = this.state.products || [];
        const catMap = catKey === "all" ? null : this._categoryMembership();

        return list.filter((p) => {
            if (catMap && catMap.get(p.product_id) !== catKey) return false;
            const parsed = parseProductName(p && p.name);
            if (widthKey !== "all" && liftWidth(p, parsed.attrs) !== widthKey) return false;
            if (q && !searchText(p, parsed).toLowerCase().includes(q)) return false;
            return true;
        });
    }

    // P3 removals (2026-08-02) — gates the relocated "Showing X of Y"
    // line (see the template, under the chip rows): true whenever a
    // search term or either chip filter is actively narrowing the
    // catalog, so the line stays silent for the (common) unfiltered
    // "everything is showing" case.
    _isInventoryFiltered() {
        return !!(
            (this.state.inventorySearch || "").trim()
            || (this.state.inventoryCategory && this.state.inventoryCategory !== "all")
            || (this.state.inventoryWidth && this.state.inventoryWidth !== "all")
        );
    }

    // Grouped/L-R-merged/sorted view of the currently-filtered catalog
    // — what the template actually renders (sticky category headers +
    // rows). Re-grouping the already-filtered list (rather than
    // filtering groupCatalog()'s output) means selecting a category
    // chip naturally collapses this to that one category/header.
    _groupedInventory() {
        return groupCatalog(this._filteredProducts());
    }

    // ─── Panel P2 — L/R handing toggle + row-product resolution ─────────────────
    // Keyed by product_id pair (stable across re-renders — groupCatalog()
    // rebuilds a fresh row object every call, so the row itself can't be
    // used as a key). Default "L" per the mockup; browsing-only state,
    // same family as catalogPreview (never written to a placed item).
    _pairKey(row) {
        return `${row.left.product_id}:${row.right.product_id}`;
    }

    _pairHanding(row) {
        return this.state.pairHanding[this._pairKey(row)] || "L";
    }

    _setPairHanding(row, side) {
        this.state.pairHanding[this._pairKey(row)] = side;
    }

    // Which real product a row currently resolves to — the toggled
    // side for a pair row, the sole product for a single row. Preview,
    // drag, is-selected highlight, and "+ Add" all resolve through
    // this so toggling L/R changes what every one of those acts on.
    _resolveRowProduct(row) {
        return row.kind === "pair"
            ? (this._pairHanding(row) === "R" ? row.right : row.left)
            : row.product;
    }

    // D17 — archetype taxonomy is no longer a visible row badge (the
    // list is tighter now); folded into the row's title tooltip instead
    // so the data keeps flowing to anyone who hovers, same info as
    // before, less visual weight.
    _rowTitle(resolved) {
        const base = `Click to preview · drag onto the scene, or use + Add, to place ${resolved.name}`;
        if (!resolved.archetype_code) return base;
        const collection = resolved.archetype_collection
            ? ` (${resolved.archetype_collection})` : "";
        return `${base} · Archetype: ${resolved.archetype_code}${collection}`;
    }

    // The SVG is generated entirely from numeric product fields
    // (cabinet_type / width_in / height_in / depth_in — see
    // cabinet_thumb.esm.js's own contract) with no user-supplied text
    // ever interpolated into it, so trusting it as markup() here is
    // safe — t-esc would just show the raw "<svg>...</svg>" text.
    _rowThumbMarkup(resolved) {
        return markup(cabinetThumbSVG(resolved, 46));
    }

    // P2 icons-everywhere — returns the pre-built (module-load-time)
    // markup for a counter-strip family icon; see METRIC_ICON_SVG above.
    // `key` is one of the CABINET_TYPE_COUNT_KEY buckets this template
    // renders a tile for (base/wall/corner/tall/panel only).
    _metricIcon(key) {
        return METRIC_ICON_SVG[key];
    }

    _fmtWidthIn(w) {
        return `${w}″`;
    }

    _onRowKeydown(ev, resolved) {
        // WAI-ARIA APG role="button" pattern — Enter/Space activate,
        // Space must also prevent the page from scrolling.
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
            ev.preventDefault();
            this._previewCatalogProduct(resolved);
        }
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

    // Generic add — used by drop AND by the "+ Add" button.
    // D14 — `targetX` (inches) sets the new item's sort-key so it
    // slots in at the dropped position. _recomputeLayoutFromItems
    // then sorts-by-x and re-packs contiguously: visual semantic is
    // "where in the run order does this go". `null` = append at end.
    //
    // SBK-030 — `opts.force` (default false) is the "Add anyway" exit
    // from the over-length refusal gate below. It is never set by a
    // human click directly; it's the onClick a refused toast's "Add
    // anyway" action re-invokes this same method with.
    _addCabinetFromProduct(product, targetX = null, wallOverride = null, opts = {}) {
        this._markUserAction();   // T7 gate
        const force = !!opts.force;
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
            //
            // SBK-030 review fix (Finding 1) — `opts.wall`, when set,
            // PINS the wall instead of re-deriving it from live state.
            // Only the "Add anyway" retry (below) ever sets it, to the
            // exact wall the refusal evaluated. Without this, a rep who
            // refuses on the back wall, clicks a different Active Wall
            // (the toast is sticky — plenty of time to), then clicks
            // "Add anyway" on the stale toast would silently reroute
            // the add to the NEW active wall — dodging the very
            // condition the refusal was measuring and mislabeling the
            // resulting "Added anyway" confirmation.
            wall: opts.wall || this.state.activeWall || wallOverride || WALLS.BACK,
        };

        // SBK-030 — refuse-by-default over-length add. T7 already
        // forbade silently GROWING the room to fit; the audit finding
        // was one step worse — an add that clearly doesn't fit was
        // still allowed, backed only by a warning toast that (with the
        // old unbounded notification stack) got buried under identical
        // repeats within a few more adds. Now: compute what the run
        // WOULD be with this candidate BEFORE it's ever pushed into
        // state.items, and refuse the mutation entirely unless the rep
        // explicitly opts in via the toast's "Add anyway" action
        // (which re-calls this method with opts.force = true).
        // Scoped to the BACK wall only — _currentRunWidthIn (C5) only
        // ever measures the back run; a side-wall add can't trip it.
        const required = this._currentRunWidthIn(newItem);
        const overflowsRoom = newItem.wall === WALLS.BACK
            && required > this.state.room.width_in;
        if (overflowsRoom && !force) {
            this._toast(
                `${product.name} won't fit — run needs ` +
                `${Math.ceil(required)}″, room is ` +
                `${this.state.room.width_in}″. Remove a cabinet, widen ` +
                `the room, or add it anyway.`,
                {
                    type: "warning",
                    sticky: true,
                    actions: [{
                        label: "Add anyway",
                        // Finding 1 — pin the EVALUATED wall (newItem.wall,
                        // captured now, not re-read live at click time).
                        onClick: () => this._addCabinetFromProduct(
                            product, targetX, wallOverride,
                            { force: true, wall: newItem.wall }),
                    }],
                }
            );
            return;   // refused — nothing added, nothing saved
        }

        // Task 35 — pre-op snapshot; see _snapshotState's doc comment.
        // Pushed only once we KNOW the add will proceed (past the
        // refusal-gate return above), never for a refused add.
        this._pushUndoSnapshot();
        this._placementsDirty = true;
        this.state.items = [...(this.state.items || []), newItem];
        this.state.selected = newItem;
        this._recomputeLayoutFromItems();

        // SBK-030 review fix (Finding 2) — pre-fix, the old post-add check
        // ran unconditionally on EVERY add (back wall or not) and warned,
        // non-blocking, whenever the back run already exceeded the room —
        // even for a side/front-wall add that had no effect on that sum
        // (e.g. a design hydrated already over capacity, or a width edit
        // via _updateSelectedWidth that doesn't itself gate). The refuse-
        // gate above fully owns the BACK-wall case now (refuse, or the
        // force "Added anyway" toast right below), but a non-back add
        // can't reach that gate at all and had lost the informational
        // signal entirely. Restored here, scoped to exactly the case the
        // gate doesn't cover: non-back adds, informational only, never
        // refusing (this add always succeeds regardless).
        if (newItem.wall !== WALLS.BACK) {
            const existingBackRun = this._currentRunWidthIn();
            if (existingBackRun > this.state.room.width_in) {
                this._toast(
                    `Note: the back-wall run still needs ` +
                    `${Math.ceil(existingBackRun)}″ in a ` +
                    `${this.state.room.width_in}″ room.`,
                    { type: "warning" }
                );
            }
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
        //
        // SBK-030 — the force=true path (explicit "Add anyway" opt-in)
        // gets its own honest confirmation instead of the generic
        // "Added X" — the rep just overrode a refusal and should see
        // the overflow acknowledged, not silently dropped.
        if (overflowsRoom && force) {
            this._toast(
                `Added ${product.name} anyway — run needs ` +
                `${Math.ceil(required)}″, room is ` +
                `${this.state.room.width_in}″.`,
                { type: "warning" }
            );
        } else if (newItem.wall !== WALLS.BACK) {
            this._toast(
                `Added ${product.name} to ${this._wallLabel(newItem.wall)} wall`,
                { type: "success" }
            );
        } else if (typeof targetX === "number" && !Number.isNaN(targetX)) {
            const finalX = newItem.x_position_in;
            this._toast(
                `Added ${product.name} at ${finalX}″`,
                { type: "success" }
            );
        } else {
            this._toast(`Added ${product.name}`, { type: "success" });
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
    //
    // SBK-030 — takes an optional `candidate` item so the over-length
    // gate in _addCabinetFromProduct can ask "what WOULD the run be if
    // this were added" BEFORE mutating state.items, instead of the old
    // push-then-measure-then-warn order. `_currentRunWidthIn()` (no
    // arg) reduces to exactly the prior behavior.
    _currentRunWidthIn(candidate = null) {
        const items = (this.state.items || []).filter(
            it => (it.wall || "back") === "back"
        );
        if (candidate && (candidate.wall || "back") === "back") {
            items.push(candidate);
        }
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
        if (!Number.isFinite(v) || v < 6 || v > 48) {
            // Task 12 (corner-remediation 2026-07-30, SBK-021/022 claim
            // 6) — this used to be a bare early-return: no state
            // mutation, no notification, nothing. The rep saw the field
            // silently ignore/revert their input with zero explanation.
            // An inline message next to the field (state.selectedWidth
            // Error / o_sbk_field_error in the template) fixes that —
            // NOT a toast: per the brief, the toast store is for
            // TRANSIENT notices, and this is a persistent field-level
            // validation state that should stay visible until corrected.
            this.state.selectedWidthError = "Must be 6–48 in.";
            return;
        }
        this.state.selectedWidthError = "";
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
        this._pushUndoSnapshot();   // Task 35 — pre-op, before the in-place mutation below
        this._placementsDirty = true;
        this.state.items[idx].width_in = v;
        sel.width_in = v;
        this._recomputeLayoutFromItems();
        this._queueAutoSave();    // D5
    }

    // Product swap: replace cabinet at the same position with the
    // new product's payload, keeping position + layout_key + dimensions.
    //
    // Task 27 (corner-remediation 2026-07-30, opportunity-trace.md §1)
    // — a DERIVED CORNER line used to go through nothing but the
    // client-only merge below + save_design's plain line.write(),
    // which only re-validates the catalog contract (sale_ok/
    // is_cabinet) — NONE of action_swap_product's Task-6 corner
    // governance (corner-only restriction, junction footprint-fit,
    // hand re-derive/refusal — models/kitchen_design.py
    // action_swap_product / _swap_derived_corner). A rep could swap a
    // junction cell for an oversized or wrong-hand corner SKU with
    // zero pushback. Corner lines now route through that server
    // method via call_kw before touching state; every other
    // cabinet_type (and any corner line the server doesn't know about
    // yet) is UNCHANGED — still the pure client merge + debounced
    // /save.
    // `selectEl` (the real <select> DOM node, passed from the template's
    // `ev.target`) is OPTIONAL — every non-DOM caller (tests, any future
    // programmatic swap) can omit it — but is required to fix review
    // Finding 2: see the corner refusal paths in
    // _swapCornerProductOnServer for why it's needed.
    async _swapSelectedProduct(rawProductId, selectEl) {
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

        // Corner governance only makes sense once the design (and
        // therefore this line) is actually persisted server-side —
        // this.state.designId unset means an in-session design that
        // has never been saved, so there is no server-side line row
        // for action_swap_product to act on yet. That case (and any
        // resolve-miss below, e.g. a save still in flight) falls back
        // to the same client-only merge every other cabinet_type
        // always uses.
        if (oldItem.cabinet_type === "corner" && this.state.designId) {
            return this._swapCornerProductOnServer(idx, oldItem, product, selectEl);
        }

        // Preserve position + layout_key + current width (user may have
        // dialed in a custom width; swapping product shouldn't reset it).
        this._applyClientSwap(idx, oldItem, product);
    }

    // Shared by the legacy (non-corner, or not-yet-persisted-corner)
    // swap path — pure client-side state merge, unchanged from the
    // pre-Task-27 behaviour of _swapSelectedProduct itself.
    _applyClientSwap(idx, oldItem, product) {
        const merged = {
            ...product,
            layout_key:    oldItem.layout_key,
            x_position_in: oldItem.x_position_in,
            y_position_in: oldItem.y_position_in,
            z_position_in: oldItem.z_position_in,
            width_in:      oldItem.width_in,
        };
        this._pushUndoSnapshot();   // Task 35 — pre-op, before the replace below
        this._placementsDirty = true;
        this.state.items[idx] = merged;
        this.state.selected = merged;
        this._recomputeLayoutFromItems();
        this._queueAutoSave();    // D5
    }

    // Task 27 — corner-line swap, routed through the server's
    // action_swap_product (models/kitchen_design.py) instead of the
    // client-only merge above: that method enforces the corner-only
    // restriction, the junction footprint-fit check, and the hand
    // re-derive/refusal from the T6 remediation, none of which
    // save_design's catalog gate ever did.
    //
    // Two-hop plain ORM call via /web/dataset/call_kw — the same
    // generic-ORM pattern _searchCustomers / _useWalkInCustomer /
    // _createAndConfirm already use in this file (see those for the
    // established RPC + toast-on-refusal conventions) — because
    // state.items never carries the design LINE's own DB id: every
    // server line-dict this file consumes (controllers/main.py
    // _serialize_design_lines) keys "id"/"product_id" off the
    // PRODUCT, not the line (Task 8's corner-style dispatch relies on
    // that same product id being there). search_read resolves
    // layout_key -> the real line id first (every Southbrook Kitchen
    // group has read access to this model —
    // security/ir.model.access.csv), then action_swap_product is
    // called on that id.
    async _swapCornerProductOnServer(idx, oldItem, product, selectEl) {
        // Review Finding 3 — a pending debounced autosave armed by an
        // earlier, UNRELATED edit (e.g. a width tweak on a different
        // cabinet) can otherwise fire mid-swap: it posts the CURRENT
        // (pre-swap) state.items to /save, whose keyed line.write()
        // would silently revert the server's just-applied swap the
        // instant that timer lands. Flush it FIRST — cancel the timer
        // and run the save now, synchronously ahead of the swap RPCs —
        // so no stale write can ever land after the swap starts.
        // Nothing pending is lost; it's just sent now instead of in
        // ~3s, same payload either way.
        if (this._autoSaveTimer) {
            clearTimeout(this._autoSaveTimer);
            this._autoSaveTimer = null;
            await this._autoSave();
        }

        let rows;
        try {
            rows = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design.line",
                method: "search_read",
                args: [
                    [["design_id", "=", this.state.designId],
                     ["layout_key", "=", oldItem.layout_key]],
                    ["id"],
                ],
                kwargs: { limit: 1 },
            });
        } catch (e) {
            // Review Finding 1 — a THROWN exception here (network
            // blip, timeout, 500, ACL error) is NOT the same thing as
            // "this line legitimately has no server row yet". The
            // bare catch this used to be treated BOTH cases
            // identically and fell back to the ungoverned client-only
            // merge — a transient failure would silently reopen the
            // exact corner-governance gap this task closes. Fail
            // CLOSED instead, exactly like the action_swap_product hop
            // below: toast the failure, abort, leave state untouched.
            const msg = e && (e.message || String(e))
                || "Couldn't verify this cabinet with the server — swap refused.";
            this._toast(msg, { type: "danger", sticky: true });
            // Review Finding 2 — see the identical revert below for
            // the full rationale; this fail-closed path leaves
            // state.selected unchanged too, so the same DOM correction
            // applies here.
            if (selectEl) selectEl.value = String(oldItem.product_id);
            return;
        }
        const lineId = rows && rows[0] && rows[0].id;
        if (!lineId) {
            // A clean, successful lookup that found NO row — this is
            // the genuine "not persisted yet" case (e.g. a save is
            // still in-flight when the rep swaps again): no
            // server-side line exists to govern the swap, so fall back
            // to the same client-only merge every unsaved line uses.
            this._applyClientSwap(idx, oldItem, product);
            return;
        }
        try {
            await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design.line",
                method: "action_swap_product",
                args:   [[lineId], product.product_id],
                kwargs: {},
            });
        } catch (e) {
            // action_swap_product raises UserError with an actionable
            // refusal message (wrong hand / oversized-for-the-cell /
            // not a corner product) — surface it exactly via the toast
            // store, same convention as every other server refusal in
            // this file (_saveDesign/_createAndConfirm). Nothing above
            // this point mutated state.items, so a refused swap leaves
            // state completely unchanged.
            const msg = e && (e.message || String(e)) || "Swap refused.";
            this._toast(msg, { type: "danger", sticky: true });
            // Review Finding 2 — the native <select> already flipped
            // to the refused option visually (a browser applies a
            // <select> value change before firing 'change', ahead of
            // this handler ever running); state.selected.product_id
            // stays exactly as it was, so OWL's vdom diff sees nothing
            // to patch on that attribute and leaves the wrong option
            // showing selected. Force the control back to the truth
            // explicitly — least-invasive fix that doesn't require a
            // render-nonce/key bump anywhere else in the template.
            if (selectEl) selectEl.value = String(oldItem.product_id);
            return;
        }
        // Success — the server is authoritative for this line's
        // product/price/dimensions now (_swap_derived_corner can
        // change any of width/height/depth/price_unit; the rarer
        // canonical-corner branch can re-run the full auto-arrange).
        // Re-pull just this line via the existing read-side route
        // (/load_design_lines) and patch it in place — NOT a wholesale
        // state.items replacement like _applyRelaidResult uses for a
        // full /save response: that would also discard any other
        // in-session cabinet the rep added but hasn't saved yet, which
        // this single-line RPC never touched.
        await this._reloadOneLineAfterCornerSwap(oldItem.layout_key);
        this._toast("Cabinet swapped", { type: "success", sticky: false });
    }

    async _reloadOneLineAfterCornerSwap(layoutKey) {
        try {
            const resp = await rpc(
                "/southbrook_kitchen/configurator/load_design_lines",
                { design_id: this.state.designId },
            );
            const lines = (resp && resp.lines) || [];
            const fresh = lines.find(l => l.layout_key === layoutKey);
            if (!fresh) return;
            const merged = this._lineToItem(fresh);
            const freshIdx = this.state.items.findIndex(
                it => it.layout_key === layoutKey);
            if (freshIdx < 0) return;
            // Task 35 — pre-op snapshot for the server-governed corner
            // swap. Only reached on a genuine success (the RPC already
            // succeeded in the caller; a refusal/network failure never
            // gets here), so no snapshot is wasted on a no-op refusal.
            this._pushUndoSnapshot();
            this._placementsDirty = true;
            this.state.items[freshIdx] = merged;
            if (this.state.selected
                    && this.state.selected.layout_key === layoutKey) {
                this.state.selected = merged;
            }
            this._recomputeLayoutFromItems();
        } catch (e) {
            // The swap already succeeded server-side; a refresh
            // failure just leaves the client's view stale until the
            // next save/reload — non-fatal, matches _searchCustomers'
            // "non-fatal" catch convention elsewhere in this file.
            console.warn(
                "[SouthbrookKitchenConfigurator] post-swap line refresh "
                + "failed:", e);
        }
    }

    // Remove the selected cabinet entirely.
    _removeSelectedCabinet() {
        this._markUserAction();   // T7 gate
        const sel = this.state.selected;
        // F14 — see the identical guard comment in _updateSelectedWidth.
        if (!sel || !sel.layout_key) return;
        const idx = this.state.items.findIndex(it => it.layout_key === sel.layout_key);
        if (idx < 0) return;
        this._pushUndoSnapshot();   // Task 35 — pre-op, before the splice below
        this._placementsDirty = true;
        this.state.items.splice(idx, 1);
        // Pick a neighbour as the new selection (or null if empty).
        this.state.selected = this.state.items[idx] || this.state.items[idx - 1] || null;
        // Task 12 — same reasoning as _selectCabinet: don't carry a
        // stale width error onto whichever cabinet becomes selected next.
        this.state.selectedWidthError = "";
        // Item 18 (E2E fix) — F14 parity with _selectCabinet: a real
        // selection change (including "selection cleared/moved by a
        // delete") must also drop any stale catalog-preview highlight
        // (_selectCabinet already does this on every ordinary click; the
        // delete path never did) so a preview row from a DIFFERENT
        // product can never visually linger where the removed cabinet's
        // panel/highlight used to be.
        this.state.catalogPreview = null;
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
        // Item 18 (E2E fix) — refresh the production-readiness badge
        // NOW instead of waiting on the 3s auto-save debounce + that
        // save's own round trip (the only place this used to happen —
        // _autoSave()'s success path). Same fire-and-forget call every
        // other mutation in this Placement block already uses
        // (_setSelectedAlongWallOffset/_nudgeSelectedFine above);
        // _refreshValidation() itself no-ops safely without a
        // state.designId (see its own guard).
        this._refreshValidation();
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

        // Task 10 (SBK-023) — a design can end up with a lone filler line
        // and ZERO real cabinets: either a pre-fix persisted "$70 filler,
        // zero cabinets" record reopened here, or a user manually deleting
        // every real cabinet and leaving an orphan filler behind. Either
        // way there is no cabinet run for that filler to belong to, so it
        // is treated as INERT below — no filler $ counted, no honest
        // remainder reported (see remainderIn further down) — mirroring
        // the parallel "don't pre-select it either" guard in
        // _hydrateFromDesign above. Computed up front so both the price
        // loop and remainderIn share one definition.
        const hasCabinets = items.some(it => it.cabinet_type !== "filler");

        let price = 0;
        let fillerPrice = 0;
        for (const it of items) {
            if (it.cabinet_type === "filler") {
                if (hasCabinets) fillerPrice += (it.price || 0);
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
        //     filler items' prices, EXCEPT when there are no real cabinets
        //     at all — see the `hasCabinets` gate above and the Task 10
        //     note below (an orphan filler with no cabinet run is inert,
        //     not a real $ line).
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
        // room never gets a phantom filler. Reuses the `hasCabinets` gate
        // computed above the price loop.
        //
        // A corner cabinet's extent ALONG the back wall isn't always its
        // width_in — kitchen_design.py's wall_start_offsets reservation
        // (read-only reference, not duplicated logic) uses width_in for
        // rot 0/180 but depth_in for rot 90/270 (the corner pivots 90°,
        // swapping which dimension spans the back/front axis vs. the
        // left/right axis). Mirror that read here so a rotated corner
        // doesn't under/over-count back-wall occupancy.
        const backWallExtentIn = (it) => {
            if (it.cabinet_type !== "corner") return (it.width_in || 0);
            const rot = Math.round(it.rotation_deg || 0) % 360;
            return (rot === 0 || rot === 180)
                ? (it.width_in || 0)
                : (it.depth_in || 0);
        };
        const backRunOccupiedIn = items
            .filter(it => (it.wall || "back") === "back"
                && (it.cabinet_type === "base" || it.cabinet_type === "corner"))
            .reduce((sum, it) => sum + backWallExtentIn(it), 0);
        const roomWidthIn = (this.state.room && this.state.room.width_in) || 0;
        const remainderIn = hasCabinets
            ? Math.max(0, roomWidthIn - backRunOccupiedIn)
            : 0;

        // Task 7 (SBK-033) — counts come from the ONE shared partition
        // (countCabinetBuckets, module-level above), not a bespoke
        // bases.length/walls.length pair. That old pair is exactly the
        // V4-counters-templates.md claim 1 bug: corner/tall/panel/
        // appliance items were silently invisible to `total`.
        this.state.summary = {
            ...countCabinetBuckets(items),
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

    // Item 15 (E2E fix) — the view-switcher toolbar's ONE-and-only
    // source of truth for "which view button is active" (state.view —
    // written here in _setView and, redundantly-but-harmlessly, by
    // _onCanvasViewChange's ack and _cameraToSelectedElevation).
    // Previously each of the 6 buttons repeated its own inline
    // `state.view === '<key>'` ternary — functionally equivalent, but
    // impossible to eyeball for a copy/paste mismatch across 6 near-
    // identical lines. Centralising it here also keeps the template
    // binding a simple no-arg-shape call (this repo's OWL-expression-
    // linter convention — see _selectedCanSendToWall/_selectedCanNudge
    // Placement above for the same pattern) rather than 6 separate
    // inline comparisons.
    _isActiveView(key) {
        return this.state.view === key;
    }

    // Task 22 (SBK-036) — front/left/right are flat orthographic
    // elevations: cabinet move-drag isn't usable there (the width/depth
    // resize handles are also gated off it — pointer_pipeline.esm.js's
    // isHandleActiveView). Before this task the ONLY signal was a line
    // of body text in the bottom-right hint pill (easy to miss/never
    // read); this getter backs an actual visual marker (badge + scene-
    // panel border tint — see the template + SCSS) so the read-only
    // state is unmistakable, not just documented.
    _isReadOnlyView() {
        return this.state.view === "front" || this.state.view === "left"
            || this.state.view === "right";
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

        // Task 35 — Ctrl/Cmd+Z undo; Ctrl/Cmd+Shift+Z OR Ctrl/Cmd+Y
        // redo (both bindings, per the brief — Windows/Linux muscle
        // memory differs between the two). Extends this SAME
        // window-level listener rather than adding a second one (T34
        // precedent — see this method's own single-listener setup in
        // setup()).
        if (ctrl && (k === "z" || k === "Z")) {
            e.preventDefault();
            if (e.shiftKey) this._redo(); else this._undo();
            return;
        }
        if (ctrl && (k === "y" || k === "Y")) {
            e.preventDefault();
            this._redo();
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

        // D6 / Task 34 (SBK-020) — Esc closes whichever "opened panel"
        // is on top first: the catalog preview block (opened most
        // recently in normal use — browsing a row after already
        // having a cabinet selected) takes priority over the detail
        // drawer, so a single Esc always closes ONE thing at a time
        // rather than both at once. `_syncPanelFocus` (onPatched)
        // handles returning focus to whatever opened the panel that
        // closes; this handler only ever touches the state field.
        if (k === "Escape") {
            if (this.state.catalogPreview) {
                this._dismissCatalogPreview();
                e.preventDefault();
                return;
            }
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
            this._toast(
                "Removed " + doomedName + " (" + doomedType +
                ") from the layout and quote.",
                { type: "info" }
            );
            return;
        }
    }

    // D6 / Task 22 (SBK-036) — cycle selection through every placed,
    // independently-selectable cabinet. +1 = next, -1 = previous; wraps.
    //
    // Audit finding: the pre-Task-22 version only included
    // cabinet_type "base"/"wall" — tall/corner/appliance cabinets
    // (real, clickable, independently selectable placed units; see
    // the "Selected Cabinet" drawer, which is generic across every
    // cabinet_type) were silently unreachable via the keyboard, which
    // read as "advances unreliably and repeats items" once a design
    // had any of those types mixed in with base/wall runs — the loop
    // only ever cycled through the base/wall subset, so it looked
    // short/repetitive relative to what's actually on screen. Filler
    // panels stay excluded — they are server-derived, not
    // independently clickable in the 3D view at all (not `cab`-
    // flagged, never pushed to `clickable`; see
    // other_cabinets.esm.js's buildFillerPanel), so cycling to one
    // would select something nothing else in the UI treats as
    // selectable.
    //
    // Order is fully deterministic — wall (canonical WALLS order), then
    // POSITION along that wall's run axis, then layout_key as a final
    // tiebreak — so two items that legitimately share a wall and
    // position (or are missing one) still sort the same way every
    // time, regardless of the incoming items array's own order (which
    // can shuffle across a reload/re-fetch). This also fixes an
    // off-by-one on the very first ArrowLeft press with nothing
    // selected: it used to land on the second-to-last item instead of
    // the last one (idx defaulted to -1 for both directions, so
    // -1 + -1 wrapped one short of the end).
    //
    // Within-wall key is POSITION (x_position_in for back/front walls,
    // z_position_in for left/right — same per-wall axis convention
    // `_updateDragGhost`'s `alongOf` uses above), not `run_seq`.
    // Review fix (Task 22, 2026-07-30): an earlier version of this
    // sorted on `run_seq`, but BOTH corner-insertion paths in the
    // engine give EVERY corner line `run_seq = -1` unconditionally
    // (kitchen_layout_engine.py's corner-node builder hardcodes it;
    // kitchen_design.py's standalone/derived-line path defaults to the
    // same -1 via `node.get("run_seq", -1)`, which just reads back the
    // engine's own hardcoded value in the normal case — universal, not
    // the narrow "front-right standalone corner" case that call site's
    // own comment describes). `a.run_seq ?? 0` never normalized -1, so
    // every corner sorted before every real run_seq on its wall, and
    // two corners sharing a wall always tied and fell through to the
    // alphabetical layout_key tiebreak instead of their actual
    // position. Position is populated and geometrically meaningful for
    // every cabinet_type, including corners, with no sentinel to
    // normalize.
    _cycleSelection(dir) {
        const items = this.state.items || [];
        const wallRank = { [WALLS.BACK]: 0, [WALLS.LEFT]: 1, [WALLS.RIGHT]: 2, [WALLS.FRONT]: 3 };
        const alongRunIn = (it) => {
            const wall = it.wall || WALLS.BACK;
            return (wall === WALLS.LEFT || wall === WALLS.RIGHT)
                ? (it.z_position_in || 0)
                : (it.x_position_in || 0);
        };
        const selectable = items
            .filter(it => it.cabinet_type !== "filler")
            .slice()
            .sort((a, b) => {
                const wa = wallRank[a.wall] ?? 99;
                const wb = wallRank[b.wall] ?? 99;
                if (wa !== wb) return wa - wb;
                const pa = alongRunIn(a), pb = alongRunIn(b);
                if (pa !== pb) return pa - pb;
                const ka = a.layout_key || "";
                const kb = b.layout_key || "";
                return ka < kb ? -1 : ka > kb ? 1 : 0;
            });
        if (!selectable.length) return;
        let idx = selectable.findIndex(
            it => this.state.selected && it.layout_key === this.state.selected.layout_key
        );
        // Nothing currently selected (or the selection isn't in this
        // list — e.g. it was just deleted): treat "next" as starting
        // one before the first item (lands on index 0) and "previous"
        // as starting one after the last item (lands on the last
        // index), so the very first keypress in either direction
        // lands somewhere sensible rather than skipping an item.
        if (idx === -1) idx = dir > 0 ? -1 : 0;
        idx = ((idx + dir) % selectable.length + selectable.length) % selectable.length;
        // Task 34 (SBK-020) — ←/→ cycling is Task 22's canvas-focused
        // keyboard flow; it must keep focus on the canvas, never yank
        // it into the detail drawer on every keypress.
        this._suppressDetailFocus = true;
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
    // Touch editing v1 (2026-08-02 spec §5 "riding along") — Q/E used to
    // rotate ANY non-filler/panel cabinet ungoverned. The spec's scoped-
    // rotation decision (§1 decision 3) restricts free rotation to
    // freestanding/island items; wall-run cabinets (and corners, which
    // get their own flip-hand affordance instead) must refuse with an
    // actionable toast rather than silently writing a rotation_deg the
    // 3D scene never rendered anyway. Shares the exact same family
    // classification the touch pad's own rotate button visibility uses
    // (getPadAffordances) — the keyboard and the pad can never disagree
    // about which cabinets are rotatable.
    _rotateSelected(deltaDeg) {
        const item = this.state.selected;
        if (!item) return;
        if (item.cabinet_type === "filler" || item.cabinet_type === "panel") {
            return;
        }
        if (!getPadAffordances(item).rotate) {
            this._toast(
                "Wall cabinets follow their wall — use Send to wall or "
                + "the corner's flip arrow",
                { type: "info" }
            );
            return;
        }
        // Task 35 review fast-follow (Important 1) — this is a design-
        // mutating op (item.rotation_deg, in place) exactly like
        // _updateSelectedWidth, and was missing undo coverage entirely:
        // a rep who rotated a cabinet and hit Ctrl+Z would silently
        // undo an unrelated EARLIER op instead. Pushed before the
        // in-place mutation below, same pattern as every other op.
        // One snapshot per Q/E keypress (not coalesced across repeated
        // taps) — simple and correct; the 25-entry cap already absorbs
        // a burst of quick rotations the same way it absorbs a burst
        // of any other op.
        this._pushUndoSnapshot();
        this._placementsDirty = true;
        const cur = ((item.rotation_deg || 0) + deltaDeg) % 360;
        item.rotation_deg = (cur + 360) % 360;
        item.pinned = true;
        this._savePinnedPosition(item);
        this._toast(
            `Rotated ${item.product_name || item.name || "cabinet"} to ${item.rotation_deg}°`,
            { type: "success" }
        );
        this._queueAutoSave();
    }

    // ─── Touch editing v1 — control pad ─────────────────────────────────────────
    // Backend is tablet-only (the 767px CSS gate in kitchen_configurator
    // .scss means anything below that width never reaches this
    // component at all), so the pad is always "floating" mode here —
    // dock mode exists only for the portal's phone case
    // (design_tab.esm.js).
    _showTouchPad() {
        return this.state.touchEnabledServer && this._touchCapable;
    }

    _touchPadCapabilities() {
        return {
            canUndo:              this.state.undoCount > 0,
            canSendToWall:        !!this.state.designId,
            sendToWallSupported:  true,
            cameraReady:          !!this._canvasApi,
        };
    }

    // Health counters (spec §4 item 3) — receiving end only; see the
    // instance-field comment in setup() and this task's build note for
    // the still-missing canvas-side call site.
    _recordTouchEvent(kind) {
        this._touchStats = accumulateTouchStats(this._touchStats, kind);
    }

    // Fix 10 (E2E P1) — the along-wall axis a cabinet slides on, keyed
    // off which wall it's currently placed on. Factored out of
    // `_nudgeSelected` (below) unchanged (z_position_in on the left/
    // right walls, x_position_in on back/front — matches
    // `_cycleSelection`'s `alongRunIn` convention) so the new Placement
    // panel's numeric offset field/fine-nudge buttons and the pre-
    // existing touch-pad nudge can never disagree about which axis a
    // given wall uses.
    _alongWallAxisField(wall) {
        return (wall === WALLS.LEFT || wall === WALLS.RIGHT)
            ? "z_position_in" : "x_position_in";
    }

    // Nudge left/right — drives the SAME position-commit pipeline the
    // mouse drag path uses (_savePinnedPosition + _recomputeLayoutFromItems
    // + _queueAutoSave; see _onCanvasMove), just with a fixed-step delta
    // instead of a raycasted pointer position. Per-wall axis convention
    // matches _cycleSelection's `alongRunIn` (z_position_in on the
    // left/right walls, x_position_in on back/front) and is clamped to
    // the room bounds minus the cabinet's own width so a nudge can never
    // push a cabinet off the visible run.
    // `stepIn` defaults to the touch pad's 6" step (NUDGE_STEP_IN,
    // unchanged call site at the bottom of this file) — Fix 10's
    // Placement panel passes PLACEMENT_NUDGE_STEP_IN (1/8") instead for
    // the fine-grained overlap-fixing nudge buttons, reusing this exact
    // commit pipeline rather than a second copy of it.
    _nudgeSelected(dir, stepIn = NUDGE_STEP_IN) {
        const item = this.state.selected;
        if (!item) return;
        if (!getPadAffordances(item).nudge) return;
        this._markUserAction();
        this._pushUndoSnapshot();
        this._placementsDirty = true;
        const axisField = this._alongWallAxisField(item.wall || WALLS.BACK);
        const alongZ = axisField === "z_position_in";
        const roomBoundIn = alongZ
            ? this.state.room.depth_in : this.state.room.width_in;
        const maxPos = Math.max(0, roomBoundIn - (item.width_in || 0));
        const next = (item[axisField] || 0) + dir * stepIn;
        item[axisField] = Math.min(Math.max(next, 0), maxPos);
        item.pinned = true;
        this._savePinnedPosition(item);
        this._recomputeLayoutFromItems();
        this._toast(
            `Nudged ${item.product_name || item.name || "cabinet"} to `
            + `${Math.round(item[axisField])}″`,
            { type: "success" }
        );
        this._queueAutoSave();
    }

    // Camera-to-elevation — wall-run cabinets' rotation substitute (spec
    // §1 decision 3). Purely client-side, no persistence. Reconciled
    // against T4's canvas-integration note (build-t4-canvas.md):
    // <KitchenCanvas> exposes a purpose-built `api.cameraToElevation
    // (wall)` via onReady (lerps the camera, "front" wall falls back to
    // "persp" internally — no dedicated elevation for that low kick-
    // strip wall) — preferred when available. Falls back to the pre-
    // existing `_setView`/`elevationViewForWall` path (used by the
    // view-switcher toolbar already) so this never regresses if
    // `_canvasApi` isn't ready yet for any reason.
    _cameraToSelectedElevation() {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).cameraToElevation) return;
        const wall = item.wall || WALLS.BACK;
        if (this._canvasApi && typeof this._canvasApi.cameraToElevation === "function") {
            this._canvasApi.cameraToElevation(wall);
            // Keep the pad's aria-pressed (isElevationActive) accurate
            // regardless of whether T4's imperative method itself fires
            // onViewChange internally — redundant-but-harmless if it
            // already does.
            this.state.view = elevationViewForWall(wall);
            return;
        }
        this._setView(elevationViewForWall(wall));
    }

    // Flip-hand (corners only) — reuses the EXISTING hand-aware swap
    // action verbatim (_swapSelectedProduct -> _swapCornerProductOnServer
    // -> action_swap_product's corner-hand governance), never a new
    // server call. Resolves the mirrored-hand twin from the catalog by
    // the same "{base, base + '-R'}" family convention
    // _sb_resolve_corner_variant documents server-side (Task 6). Items
    // loaded from a saved design key their code as `default_code`;
    // items freshly placed from the catalog key it as `sku`
    // (_product_payload) — read both defensively.
    async _flipSelectedHand() {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).flipHand) return;
        const code = item.sku || item.default_code || "";
        if (!code) {
            this._toast(
                "This corner cabinet has no catalog code — can't "
                + "resolve its mirrored-hand twin.",
                { type: "warning", sticky: true }
            );
            return;
        }
        const base = code.endsWith("-R") ? code.slice(0, -2) : code;
        const siblingCode = code === base ? base + "-R" : base;
        const sibling = (this.state.products || []).find(
            p => p.cabinet_type === "corner" && (p.sku || "") === siblingCode
        );
        if (!sibling) {
            this._toast(
                "No mirrored-hand twin found in the catalog for this corner.",
                { type: "warning", sticky: true }
            );
            return;
        }
        await this._swapSelectedProduct(sibling.product_id);
    }

    // Send-to-wall (spec §5) — the touch pad's "cycle icon" affordance.
    // Calls the T2-verified `action_move(wall, run_seq)` via the SAME
    // generic /web/dataset/call_kw plumbing _swapCornerProductOnServer
    // already uses for action_swap_product (search_read the line id by
    // design_id + layout_key, then call the action on it) — no new
    // controller route. `run_seq` is computed from the CLIENT's own
    // state.items (the client never receives the server's true run_seq;
    // see _serialize_configurator_lines) as "how many cabinets are
    // already on the target wall" — appends after them in the common
    // case; the server is authoritative for feasibility regardless
    // (action_move's _write_then_rearrange rolls back and raises on a
    // layout that doesn't fit, exactly like every other governed write
    // on this line model).
    //
    // Fix 10 (E2E P1) — split into this target-accepting body and the
    // thin cycle wrapper below it, so the Placement panel's explicit
    // wall selector (rep picks "Left" deliberately) and the touch pad's
    // blind "cycle to the next wall" tap drive the exact same
    // action_move RPC and can never diverge in behaviour.
    async _sendSelectedToWallTarget(targetWall) {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).sendToWall) return;
        if (!targetWall || targetWall === (item.wall || WALLS.BACK)) return;
        if (!this.state.designId) {
            this._toast(
                "Save the design first, then use Send to wall.",
                { type: "warning", sticky: true }
            );
            return;
        }
        // Fix 10 — this is an explicit user action (Placement panel's
        // wall selector, same class as the touch pad's own tap); open
        // the T7 gate so a debounced autosave from an EARLIER edit this
        // session is never silently skipped just because THIS action
        // itself persists immediately via action_move below.
        this._markUserAction();
        // Finding-3 pattern (see _swapCornerProductOnServer) — flush any
        // pending debounced autosave FIRST so a stale write can never
        // land after the move and silently revert it.
        if (this._autoSaveTimer) {
            clearTimeout(this._autoSaveTimer);
            this._autoSaveTimer = null;
            await this._autoSave();
        }
        let rows;
        try {
            rows = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design.line",
                method: "search_read",
                args: [
                    [["design_id", "=", this.state.designId],
                     ["layout_key", "=", item.layout_key]],
                    ["id"],
                ],
                kwargs: { limit: 1 },
            });
        } catch (e) {
            const msg = e && (e.message || String(e))
                || "Couldn't verify this cabinet with the server — move refused.";
            this._toast(msg, { type: "danger", sticky: true });
            return;
        }
        const lineId = rows && rows[0] && rows[0].id;
        if (!lineId) {
            this._toast(
                "Save the design first, then use Send to wall.",
                { type: "warning", sticky: true }
            );
            return;
        }
        const runSeq = (this.state.items || []).filter(
            it => it.wall === targetWall && it.layout_key !== item.layout_key
        ).length;
        let result;
        try {
            result = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design.line",
                method: "action_move",
                args:   [[lineId], targetWall, runSeq],
                kwargs: {},
            });
        } catch (e) {
            // action_move raises UserError (savepoint-rolled-back) when
            // the move doesn't fit — the "refused: doesn't fit" path
            // T2's note calls for, distinct from success below.
            const msg = e && (e.message || String(e)) || "That move doesn't fit — refused.";
            this._toast(msg, { type: "danger", sticky: true });
            return;
        }
        // action_move's raw call_kw response has no `relaid` key (that
        // flag is a controller-only concept — see save_design's
        // response shape in controllers/main.py); synthesize it so this
        // can flow through the SAME _applyRelaidResult every other
        // relayout response uses, rather than hand-rolling a second
        // lines-application path.
        this._applyRelaidResult({ ...result, relaid: true });
        this._toast(
            `Sent ${item.product_name || item.name || "cabinet"} to the `
            + `${this._wallLabel(targetWall)} wall`,
            { type: "success" }
        );
        this._refreshValidation();
    }

    // Touch pad's "cycle icon" — unchanged entry point/behaviour, now a
    // thin wrapper over `_sendSelectedToWallTarget` above (Fix 10).
    async _sendSelectedToWall() {
        const item = this.state.selected;
        if (!item) return;
        return this._sendSelectedToWallTarget(nextWall(item.wall || WALLS.BACK));
    }

    // ─── Fix 10 (E2E P1) — Placement panel ──────────────────────────────────────
    // The Selected Cabinet panel used to expose only Product + Width — a
    // rep who hit an overlap blocker had no field to type the number
    // that would fix it. These three methods back that panel's new
    // Placement block (template further down): a wall picker wired to
    // the EXISTING `_sendSelectedToWallTarget` (action_move) above, and
    // a along-wall offset field + 1/8" nudge buttons wired to the
    // EXISTING `_savePinnedPosition` (save_position route) via
    // `_nudgeSelected` — no new server route for either.

    // Template-bound booleans — simple, no-arg calls (no chained
    // `getPadAffordances(state.selected).sendToWall`-style expression
    // in the t-if binding itself) so the Placement panel matches this
    // template's existing "simple names only" convention (see
    // _sameTypeProducts/_materialLabel's single-arg calls above). Both
    // delegate to the SAME getPadAffordances() classification the
    // touch pad and the keyboard nudge/rotate guards already use, so
    // this panel can never offer a control a cabinet's family doesn't
    // actually support (e.g. no wall-picker for a corner, no offset
    // field for a filler/panel).
    _selectedCanSendToWall() {
        return !!(this.state.selected
            && getPadAffordances(this.state.selected).sendToWall);
    }

    _selectedCanNudgePlacement() {
        return !!(this.state.selected
            && getPadAffordances(this.state.selected).nudge);
    }

    // Template-bound getter — the numeric offset field's current value.
    // Simple/no-arg by design (OWL-expression-linter friendly): all the
    // branching lives here in JS, never in the template binding itself.
    // Defensive like every other selector in this file even though the
    // template only calls this while state.selected is truthy.
    _selectedAlongWallOffset() {
        const item = this.state.selected;
        if (!item) return 0;
        const axisField = this._alongWallAxisField(item.wall || WALLS.BACK);
        const raw = item[axisField] || 0;
        // Round for display only — the stored value keeps full
        // precision; this just keeps the field from showing long float
        // noise (e.g. 24.000000000000004) after repeated 1/8" nudges.
        return Math.round(raw * 1000) / 1000;
    }

    // Wall selector <select> change handler — moves the selected
    // cabinet to the chosen wall via the SAME action_move RPC the touch
    // pad's cycle button uses (`_sendSelectedToWallTarget` above).
    _setSelectedWall(wallValue) {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).sendToWall) return;
        if (!wallValue || wallValue === (item.wall || WALLS.BACK)) return;
        this._sendSelectedToWallTarget(wallValue);
    }

    // Numeric "position along wall" field's commit handler (on change/
    // blur, matching `_updateSelectedWidth`'s pattern right below this
    // block in the template). Explicit user action — `_markUserAction()`
    // first (T7 gate) so a debounced `_queueAutoSave()` this triggers
    // is never silently dropped — then the SAME commit pipeline
    // `_nudgeSelected`/the mouse-drag path already use: clamp to
    // [0, roomBound - width], persist via `_savePinnedPosition`
    // (save_position route), recompute, and refresh the validation chip
    // so a rep resolving an overlap blocker sees it clear immediately.
    _setSelectedAlongWallOffset(rawValue) {
        const item = this.state.selected;
        if (!item) return;
        if (!getPadAffordances(item).nudge) return;
        const parsed = parseFloat(rawValue);
        if (!Number.isFinite(parsed)) return;
        this._markUserAction();
        this._pushUndoSnapshot();
        this._placementsDirty = true;
        const axisField = this._alongWallAxisField(item.wall || WALLS.BACK);
        const alongZ = axisField === "z_position_in";
        const roomBoundIn = alongZ
            ? this.state.room.depth_in : this.state.room.width_in;
        const maxPos = Math.max(0, roomBoundIn - (item.width_in || 0));
        item[axisField] = Math.min(Math.max(parsed, 0), maxPos);
        item.pinned = true;
        this._savePinnedPosition(item);
        this._recomputeLayoutFromItems();
        this._refreshValidation();
        this._queueAutoSave();
    }

    // Fine nudge buttons (1/8") — thin wrapper over the existing
    // `_nudgeSelected` commit pipeline (now step-parameterized, see its
    // own comment above) plus an immediate validation refresh, since
    // this panel's whole purpose is letting a rep see an overlap
    // blocker clear right away rather than waiting on the next
    // hydrate/save/arrange refresh cycle. Guarded identically to
    // `_nudgeSelected` itself so `_refreshValidation()` only fires
    // after a move that actually happened.
    _nudgeSelectedFine(dir) {
        const item = this.state.selected;
        if (!item || !getPadAffordances(item).nudge) return;
        this._nudgeSelected(dir, PLACEMENT_NUDGE_STEP_IN);
        this._refreshValidation();
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
        // Task 7 (SBK-033) — the server's counts/price are authoritative:
        // merge them over whatever the client's local recompute produced
        // (either just above, or from a prior edit) so a save can never
        // leave state.summary out of sync with the row that was actually
        // written (server-side channel pricing, the sentinel guard, or
        // appliance price-pinning can all change price_unit/active
        // without the client knowing). Local-only breakdown keys not on
        // the server object (remainder_in, snap_hint) survive via the
        // spread-merge.
        if (result && result.summary) {
            this.state.summary = { ...this.state.summary, ...result.summary };
        }
        if (result && result.layout_warning) {
            this._toast(result.layout_warning,
                { type: "warning", sticky: true });
        }
    }

    // Fix-round (coordinator review, corner-remediation 2026-07-30) —
    // /save (save_design) can reject with a normal (non-thrown) JSON
    // body instead of an RPC exception, in THREE different pre-existing
    // shapes: {error, detail} (invalid_wall), {error, error_code} (e.g.
    // Task 12's ROOM_OUT_OF_BOUNDS), or {ok:false, reason} (not_found/
    // forbidden on a stale/foreign design_id). Before this fix, NEITHER
    // _saveDesign nor _autoSave inspected the response body at all —
    // unlike every other RPC site in this file (_hydrateFromDesign's
    // NOT_FOUND/ACCESS_DENIED check, _refreshLayout's error/error_code
    // mirror, /rearrange's error handling in _rearrangeOnServer) — so a
    // structured rejection was silently treated as success:
    // _saveDesign showed a FALSE "Design saved" toast, and _autoSave's
    // catch block never even ran (the RPC itself succeeded; only the
    // JSON body says "no"). ONE place turns any of the three shapes
    // into a human-readable message so both callers react identically.
    // Returns null when `result` looks like a normal success payload.
    _saveErrorMessage(result) {
        if (!result) return null;
        if (result.error) return result.detail || result.error;
        if (result.ok === false) {
            if (result.reason === "not_found") {
                return "This design no longer exists. It may have been deleted.";
            }
            if (result.reason === "forbidden") {
                return "You don't have write access to this design.";
            }
            return "Save failed.";
        }
        return null;
    }

    async _saveDesign() {
        // PR2.5a (Gap 2) — same rationale as the _queueAutoSave() guard:
        // a failed hydration means state doesn't reflect the saved
        // design, so a manual save (button or Cmd/Ctrl+S) must also be
        // refused rather than overwriting it.
        if (this._hydrationFailed) {
            this._toast(
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
                // SBK-026 — round-trip the Wall Cabinet Top selector +
                // its inputs onto the design record so the NEXT
                // Auto-arrange/resize (which reads these fields off the
                // record, not off this client session) honours the same
                // choice — previously never persisted at all.
                // P3 removals (2026-08-02) — counter_thickness_in is
                // deliberately NOT round-tripped here anymore: the Room
                // panel's editing surface for it is gone (bebb5b72), so
                // state.counterThicknessIn never changes after boot —
                // sending it back on every save was a guaranteed no-op
                // write. The model field (kitchen_design.py's
                // counter_thickness_in, default 1.5) keeps whatever
                // value it already has; save_design's controller-side
                // `if counter_thickness_in is not None` guard leaves it
                // untouched when the key is absent. It still feeds the
                // SBK-026 math via `_refreshLayout()`'s /layout call
                // below (unchanged) and the server's own
                // _wall_cab_top_geometry — only this redundant write-
                // back is gone.
                filler_strategy:         this.state.fillerStrategy || "split",
                wall_cab_top_alignment:  this.state.wallCabTopAlignment || "fixed_gap",
                soffit_height_in:        this.state.soffitHeightIn || 0.0,
            });
            // Fix-round — a structured rejection (see _saveErrorMessage)
            // must never fall through to the success path below.
            const _saveErr = this._saveErrorMessage(result);
            if (_saveErr) {
                this._toast(_saveErr, { type: "danger", sticky: true });
                return;
            }
            // A save that actually succeeded resolves any earlier
            // auto-save failure condition too (see _autoSave's dedup).
            this._autoSaveErrorShown = false;
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
            // Task 19 — brief: status chip "refreshed after save/
            // arrange". Fire-and-forget, same as every other call site.
            this._refreshValidation();
            // Task #48 — quiet, non-sticky toast on the happy path.
            // Previous format ("Saved: ${name}") leaked implementation
            // detail into the message; the rep already sees the design
            // title in the header pill.
            this._toast("Design saved", { type: "success", sticky: false });
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
                this._toast(
                    "Assign a customer via the picker (top-right) — " + msg,
                    { type: "warning", sticky: true }
                );
            } else {
                this._toast(msg, { type: "danger", sticky: true });
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
        // Item 19 (E2E fix) — surface a KNOWN blocking issue instead of
        // firing the quotation RPC blindly. state.validation is the
        // SAME {counts, issues} the persistent chip/panel (Task 19)
        // already polls via /validate — reused here, no extra fetch.
        // This can go stale between an edit and the next debounced
        // /validate refresh (same staleness window the chip itself
        // already accepts — see that Task's own doc comment), so it's
        // a UX shortcut, not the enforcement point: action_create_
        // quotation still runs its own server-side checks regardless.
        if (this.state.validation.counts.blocking > 0) {
            this.state.validation.panelOpen = true;
            const n = this.state.validation.counts.blocking;
            this._toast(
                `Resolve ${n} blocking issue${n === 1 ? "" : "s"} first — `
                + "see Production check.",
                { type: "warning", sticky: true }
            );
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
            this._toast(
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
                this._toast(
                    "Assign a customer via the picker (top-right) — " + msg,
                    { type: "warning", sticky: true }
                );
            } else {
                this._toast(msg, { type: "danger", sticky: true });
            }
        } finally {
            this.state.saving = false;
        }
    }

    // ─── Item 19 (E2E fix) — form-parity toolbar actions ────────────────────────
    // "Plan PDF, Validate, Duplicate as New Version, and Create Quotation
    // exist only on the Odoo form, not in the configurator toolbar" — a
    // rep had to leave the 3D tool to finish a quote. Create Quotation
    // was already covered by _createAndConfirm above (same server
    // method, action_create_quotation, just under the "Create &
    // Confirm" label) — now also guarded against known blocking issues
    // (see the guard just added above). The three below wire the
    // REMAINING form-only actions to their EXACT existing object-button
    // methods (views/kitchen_design_views.xml's header) via the same
    // call_kw + this.action.doAction pattern _createAndConfirm already
    // established — no new routes. All three require an already-saved
    // design (each server method is `self.ensure_one()` against a real
    // record) and are gated on state.loading like every other topbar
    // mutation this fix round added (see the room-dim inputs' own P0
    // comment above).

    // action_report_kitchen_plan (reports/kitchen_plan_report.xml) —
    // the SAME ir.actions.report the form's "Plan PDF" header button
    // triggers (type="action" against %(action_report_kitchen_plan)d).
    // additionalContext.active_ids is the standard Odoo web-client
    // mechanism for telling a report action which record(s) to render
    // (action_service.js's _preprocessAction merges it into the loaded
    // action's own context; the report engine reads active_ids from
    // there) — no bespoke report route.
    async _printPlanPdf() {
        if (!this.state.designId || this.state.loading) return;
        return this.action.doAction(
            "southbrook_kitchen_3d_configurator.action_report_kitchen_plan",
            {
                additionalContext: {
                    active_id:  this.state.designId,
                    active_ids: [this.state.designId],
                },
            }
        );
    }

    // action_validate_production (models/kitchen_design.py) — the SAME
    // object-button method the form's "Validate" header button calls.
    // Returns a display_notification client-action dict (sticky if any
    // blocking issue), dispatched via the existing this.action.doAction
    // helper. Distinct from the persistent validation chip/panel above
    // (Task 19's own /validate polling, already in the toolbar) — this
    // is the one-shot form-parity action the E2E report specifically
    // asked for.
    async _runValidateAction() {
        if (!this.state.designId || this.state.loading) return;
        try {
            const result = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design",
                method: "action_validate_production",
                args:   [[this.state.designId]],
                kwargs: {},
            });
            if (result && result.type) {
                return this.action.doAction(result);
            }
        } catch (e) {
            this._toast(
                "Validate failed: " + (e && (e.message || String(e)) || "unknown error"),
                { type: "danger", sticky: true }
            );
        }
    }

    // action_duplicate_and_open (models/kitchen_design.py) — the SAME
    // method the form's "Duplicate as New Version" header button calls.
    // It copies the design server-side and returns action_open_
    // configurator()'s client-action dict — tag "southbrook_kitchen_
    // configurator", THIS component's own registry tag (see the bottom
    // of this file). Dispatching it through this.action.doAction (the
    // same helper _openDesigns/_openTemplatePicker/_createAndConfirm
    // already use) remounts a fresh instance of this component on the
    // new copy, exactly like navigating here from the Kitchen Designs
    // list does — see _resetForNewSession's own doc comment on that
    // "destroys and recreates" behaviour.
    async _duplicateAsNewVersion() {
        if (!this.state.designId || this.state.loading || this.state.saving) return;
        this.state.saving = true;
        try {
            const result = await rpc("/web/dataset/call_kw", {
                model:  "southbrook.kitchen.design",
                method: "action_duplicate_and_open",
                args:   [[this.state.designId]],
                kwargs: {},
            });
            if (result && result.type && result.type.startsWith("ir.actions.")) {
                return this.action.doAction(result);
            }
        } catch (e) {
            this._toast(
                "Duplicate failed: " + (e && (e.message || String(e)) || "unknown error"),
                { type: "danger", sticky: true }
            );
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
        // P0 (E2E fix) — the 5-10s boot/continue/new-design window
        // (state.loading true) must never schedule a persist.
        // _startNewDesign() leaves state.designId null for that entire
        // window; if a mutating control slips an edit through mid-load
        // (e.g. a room-dim keystroke racing the input-disable added for
        // this same fix, or any other programmatic call), the old
        // behaviour would still debounce into a save_design POST with
        // design_id:false — the server mints a brand-new, unasked-for
        // record ("Walk-in - WxD - <date>"). state.loading IS this
        // codebase's only "a load is in flight" signal (set true for
        // the whole onWillStart/_startNewDesign/_continueDesign async
        // window, including the null-designId case), so this one check
        // covers both. _autoSave() re-checks the same flag immediately
        // before its RPC as a second gate against the debounce timer
        // outliving a slow load.
        if (this.state.loading) return;
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
        // P0 (E2E fix) — see _queueAutoSave()'s matching guard just
        // above for the full rationale. Re-checked here (not just at
        // schedule time) because the debounce timer's 3s can easily
        // outlive the boot load: a design's first record must never get
        // silently minted mid-load with design_id:false.
        if (this.state.loading) return;
        if (this.state.saving) return;       // manual save in flight - skip
        if (this.state.autoSaving) {
            // Task 38 (client hardening) — a save requested while one is
            // already in flight used to be silently dropped here. The
            // in-flight request already captured a snapshot of
            // state.items/room at ITS start, so a mutation that landed
            // after that snapshot (and fired this call) would never get
            // persisted until some LATER, unrelated edit happened to
            // re-queue one. Latest-wins, matching _queueAutoSave's own
            // debounce semantics: mark one follow-up pending (no queue
            // depth beyond that) and let the in-flight save's own
            // `finally` below run it once, after this one settles.
            this._autoSavePending = true;
            return;
        }
        this.state.autoSaving = true;
        // Touch editing v1 (spec §4 item 3) — attach the accumulated
        // gesture health counters to THIS autosave round-trip only
        // (per the build brief's explicit scope: "attach touch_stats to
        // the existing autosave payload", not every save path).
        // Captured before the request so a successful send can net out
        // exactly what was sent, never a stats event that arrived
        // during the await (see the success-path comment below).
        const _sentTouchStats = this._touchStats;
        try {
            const result = await rpc("/southbrook_kitchen/configurator/save", {
                name:       this.state.designName || "Untitled Kitchen",
                room:       this.state.room,
                items:      this.state.items,
                design_id:  this.state.designId || false,
                partner_id: this.state.partnerId || false,
                // SBK-026 — same round-trip as the manual _saveDesign
                // path above; auto-save must not silently drop the
                // Wall Cabinet Top selection on its next debounce.
                // P3 removals (2026-08-02) — counter_thickness_in
                // dropped from this payload too; see _saveDesign's
                // comment for why (dead editing surface, guaranteed
                // no-op write, model field keeps its existing value).
                filler_strategy:         this.state.fillerStrategy || "split",
                wall_cab_top_alignment:  this.state.wallCabTopAlignment || "fixed_gap",
                soffit_height_in:        this.state.soffitHeightIn || 0.0,
                ...(hasTouchStats(_sentTouchStats)
                    ? { touch_stats: _sentTouchStats } : {}),
            });
            // Fix-round (coordinator review) — a structured rejection
            // (see _saveErrorMessage) used to fall straight through to
            // the success-path writes below with ZERO signal to the
            // rep: the comment on this catch block said "manual save
            // will surface errors", but a design that saves fine except
            // for its room dims (e.g. a reopened legacy design whose
            // room was persisted before the ROOM_OUT_OF_BOUNDS check
            // existed) would have EVERY auto-save AND every manual save
            // rejected the same way — there was no working save path
            // left to "surface" anything. Auto-save failures are
            // deduped (shown once per ongoing failure, not every 3s
            // debounce) so a persistent condition doesn't spam toasts;
            // the flag resets the moment ANY save (auto or manual)
            // succeeds again, so a later NEW failure still surfaces.
            const _autoErr = this._saveErrorMessage(result);
            if (_autoErr) {
                if (!this._autoSaveErrorShown) {
                    this._autoSaveErrorShown = true;
                    this._toast(
                        "Auto-save failed: " + _autoErr
                        + " Your latest change may not be saved.",
                        { type: "danger", sticky: true }
                    );
                }
                console.warn(
                    "[SouthbrookKitchenConfigurator] auto-save rejected:",
                    _autoErr);
                return;
            }
            this._autoSaveErrorShown  = false;
            this.state.designId       = result.id;
            this.state.designName     = result.name;
            this.state.lastAutoSaveAt = Date.now();
            // Touch editing v1 — net out exactly the counters this
            // request sent (never a flat reset-to-zero): a gesture
            // event that accumulated DURING the await must survive for
            // the next autosave, not be silently dropped.
            if (hasTouchStats(_sentTouchStats)) {
                this._touchStats = {
                    gesture_rollbacks: Math.max(0,
                        this._touchStats.gesture_rollbacks - _sentTouchStats.gesture_rollbacks),
                    gesture_abandons: Math.max(0,
                        this._touchStats.gesture_abandons - _sentTouchStats.gesture_abandons),
                };
            }
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
            // Task 19 — brief: status chip "refreshed after save/
            // arrange" — auto-save is a save. Fire-and-forget.
            this._refreshValidation();
        } catch (e) {
            // Silent on auto-save: manual save will surface errors. Log
            // for diagnostics only.
            console.warn("[SouthbrookKitchenConfigurator] auto-save failed:", e);
        } finally {
            this.state.autoSaving = false;
            // Task 38 (client hardening) — run the ONE follow-up save a
            // mid-flight request marked pending above, now that this
            // save has fully settled (success, rejection, or throw —
            // any of those leaves state.items possibly newer than what
            // just got persisted). Fire-and-forget, same as every other
            // auto-save call site.
            if (this._autoSavePending) {
                this._autoSavePending = false;
                this._autoSave();
            }
        }
    }

    // Item 25 (E2E fix) — this used to doAction() with no options, which
    // (target defaulting to "current" for an act_window) replaced the
    // running configurator's own controller outright — no breadcrumb
    // survives that swap (this action is normally reached straight from
    // a menu click, so there's nothing to "back" into either), so a rep
    // who wasn't done with the current design had no way back to it
    // short of re-finding it in the list. `newWindow: true` is the
    // action service's own existing option for exactly this ("open in
    // a new context" — see action_service.js's `_openActionInNewWindow`)
    // — the current tab's configurator instance (and state.designId)
    // is left completely untouched; Kitchen Designs opens in a new tab
    // alongside it. No new mechanism invented.
    async _openDesigns() {
        return this.action.doAction(
            "southbrook_kitchen_3d_configurator.action_sbk_kitchen_designs",
            { newWindow: true }
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
            this._toast(
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

    // Item 16 (E2E fix) — "Active Wall" used to read state.activeWall
    // ONLY, which is real, load-bearing state (F7 precedence just above
    // in _addCabinetFromProduct: it decides which wall a NEW cabinet
    // drops onto) — but its ONLY setter is a direct click on a bare
    // wall SURFACE in the 3D scene (_onWallSelect, "PR1" comment
    // above), an easy-to-miss gesture most reps never discover. The
    // indicator read "None" for an entire session as a result —
    // "permanent, unexplained, unsettable" per the E2E report.
    //
    // Decision: keep state.activeWall exactly as-is (removing it would
    // regress the F7 add-flow precedence it drives) but make the
    // DISPLAYED value fall back to the SELECTED cabinet's own wall —
    // now that the Placement block's wall picker (_setSelectedWall)
    // makes a placed cabinet's wall a first-class, visible/settable
    // thing — whenever no wall has been explicitly clicked yet. A
    // deliberate wall click still wins once made (same F7 ordering).
    // This is the far more common case in a real session (a rep has
    // almost always selected SOME cabinet), so the row is only ever
    // "None" for a genuinely empty, nothing-selected canvas — where
    // "None" is honest, not confusing.
    _displayActiveWall() {
        return this.state.activeWall
            || (this.state.selected && this.state.selected.wall)
            || null;
    }
}

// ─── OWL Template ─────────────────────────────────────────────────────────────
SouthbrookKitchenConfigurator.template = xml`
<div class="o_sbk_root">

  <!-- Task 34 (SBK-019) — below-768px mobile gate. Always in the DOM
       (regardless of state.screen — landing/loading/error/workspace
       all get gated the same way) but display:none above 767px; the
       SCSS also hides every OTHER direct child of .o_sbk_root at that
       width, so this is the only thing that renders on a phone —
       including the topbar's own brand mark, hence the small eyebrow
       repeating it here so this screen isn't unbranded.
       Prodboard itself gates the full 3D planner to tablet/desktop —
       this mirrors that boundary rather than attempting a phone
       layout (brief: SBK-019). Copy matches the app's own terse,
       action-first voice elsewhere (e.g. the read-only view banner:
       "switch to Iso, Top or Perspective to move cabinets") rather
       than a generic "please" apology. -->
  <div class="o_sbk_mobile_gate">
    <div class="o_sbk_mobile_gate_brand">SOUTHBROOK KITCHEN</div>
    <strong class="o_sbk_mobile_gate_title">Best on tablet or desktop</strong>
    <p class="o_sbk_mobile_gate_body">
      This 3D kitchen configurator needs a wider screen. Switch to a
      tablet, laptop, or desktop, or rotate your device to landscape.
    </p>
  </div>

  <!-- Task 20 (SBK-034) — minimal landing state. Shown whenever the
       configurator was opened with no design id (the bare menu entry,
       or a direct "new" URL) — instead of the old behavior of
       silently generating (and, on first edit, persisting) a fresh
       design. Deliberately bare: nothing else in state (products,
       room, channel, ...) is fetched yet at this point, so none of
       the workspace's topbar controls would mean anything here. Ends
       the moment the rep picks Continue (_continueDesign) or
       New Design (_startNewDesign) — see those methods and the
       onWillStart comment above for the full boot-decision contract. -->
  <t t-if="state.screen === 'landing'">
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
    </header>
    <div class="o_sbk_landing">
      <div class="o_sbk_landing_card">
        <h2 class="o_sbk_landing_heading">Open a kitchen design</h2>
        <p class="o_sbk_landing_sub">Continue a recent design, or start a new one.</p>

        <div t-if="state.recentDesignsLoading" class="o_sbk_landing_loading">
          <div class="o_sbk_spinner"/>
          <span>Loading recent designs…</span>
        </div>
        <div t-elif="!state.recentDesigns.length" class="o_sbk_landing_empty">
          No recent designs yet.
        </div>
        <ul t-else="" class="o_sbk_landing_list">
          <li t-foreach="state.recentDesigns" t-as="d" t-key="d.id">
            <button type="button" class="o_sbk_landing_item"
                    t-on-click="() => this._continueDesign(d.id)">
              <span class="o_sbk_landing_item_name" t-esc="d.name"/>
              <span class="o_sbk_landing_item_meta">
                <t t-if="d.partner_name" t-esc="d.partner_name"/>
                <t t-if="d.partner_name"> · </t>
                <t t-esc="d.room_width_in"/>″ × <t t-esc="d.room_depth_in"/>″
              </span>
            </button>
          </li>
        </ul>

        <button type="button" class="o_sbk_btn o_sbk_btn_primary o_sbk_landing_new"
                t-on-click="_startNewDesign">
          + New Design
        </button>
      </div>
    </div>
  </t>
  <t t-else="">

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
      <!-- Item 14 (E2E fix) — "(in)" on every label makes the unit
           explicit (there was no indication anywhere that these are
           inches, not cm/ft); Depth and Height now get the SAME
           o_sbk_room_dim_input_row + o_sbk_room_dim_ft feet-helper
           markup Width already had (both classes are generic — see
           the SCSS, neither is Width-specific — so this is a template-
           only mirror, no new styling needed). -->
      <label class="o_sbk_room_dim">
        <span class="o_sbk_room_dim_label">Width (in)</span>
        <div class="o_sbk_room_dim_input_row">
          <!-- P0 (E2E fix) — disabled while state.loading: these were
               live during the 5-10s boot/hydrate window, sitting
               outside the o_sbk_loading gate below. A keystroke here
               mid-load both got silently clobbered by the load's own
               room-dim writeback and (via _changeRoom -> _queueAutoSave)
               could mint an unasked-for design record — see the
               _queueAutoSave() guard for the write-side half of this
               fix. Disabled (dimmed, native cursor) rather than hidden
               so the topbar layout never jumps. -->
          <input type="number" min="12" max="480" step="6"
                 t-att-value="state.room.width_in"
                 t-att-disabled="state.loading"
                 t-on-change="(ev) => this._changeRoom('width_in', ev.target.value)"/>
          <span class="o_sbk_room_dim_ft"><t t-esc="(state.room.width_in / 12).toFixed(1)"/>′</span>
        </div>
        <!-- Task 12 (corner-remediation, SBK-021/022) — inline, next to
             the offending field (never a toast — see _changeRoom). -->
        <span t-if="state.roomDimErrors.width_in" class="o_sbk_field_error"
              t-esc="state.roomDimErrors.width_in"/>
      </label>
      <label class="o_sbk_room_dim">
        <span class="o_sbk_room_dim_label">Depth (in)</span>
        <div class="o_sbk_room_dim_input_row">
          <input type="number" min="12" max="480" step="6"
                 t-att-value="state.room.depth_in"
                 t-att-disabled="state.loading"
                 t-on-change="(ev) => this._changeRoom('depth_in', ev.target.value)"/>
          <span class="o_sbk_room_dim_ft"><t t-esc="(state.room.depth_in / 12).toFixed(1)"/>′</span>
        </div>
        <span t-if="state.roomDimErrors.depth_in" class="o_sbk_field_error"
              t-esc="state.roomDimErrors.depth_in"/>
      </label>
      <label class="o_sbk_room_dim">
        <span class="o_sbk_room_dim_label">Height (in)</span>
        <div class="o_sbk_room_dim_input_row">
          <input type="number" min="84" max="144" step="6"
                 t-att-value="state.room.height_in"
                 t-att-disabled="state.loading"
                 t-on-change="(ev) => this._changeRoom('height_in', ev.target.value)"/>
          <span class="o_sbk_room_dim_ft"><t t-esc="(state.room.height_in / 12).toFixed(1)"/>′</span>
        </div>
        <span t-if="state.roomDimErrors.height_in" class="o_sbk_field_error"
              t-esc="state.roomDimErrors.height_in"/>
      </label>
      <!-- Templates T3 (user directive): the template entry lives in the
           TOP FRAME beside the room dimensions — dims first, then shape
           + cabinet selections (the four-dropdown picker dialog).
           P0 (E2E fix) — label renamed "Start Custom" -> "New Kitchen":
           this is the single most important control on a bare/new
           session and the old label didn't say so (it opens a dialog
           titled "Start from a Sample Kitchen" — that dialog's own XML
           title is unchanged here, still owned by another file). Also
           disabled while state.loading, same rationale as the room-dim
           inputs above. -->
      <button class="o_sbk_btn o_sbk_btn_template" type="button"
              title="Start from a prebuilt sample kitchen"
              t-att-disabled="state.loading"
              t-on-click="_openTemplatePicker">New Kitchen</button>
    </div>
    <!-- Task #48 / P3 — Customer picker (top-bar).
         Hidden entirely for portal users — Agent A's default_get
         autofills partner_id from user.partner_id so the customer
         doesn't need to pick themselves. In that case we show a
         subtle reassurance pill instead (t-elif below).
         P3 (2026-08-02) — two mutually-exclusive states once a
         partner assignment is possible:
           - assigned (state.partnerId): a CHIP — the customer's real
             name (never a hardcoded label — see _useWalkInCustomer),
             opens the contact form on click, (x) unassigns.
           - unassigned: a search combobox (name/email/phone/ref,
             ~250ms debounce, limit 8) plus the "+ Walk-in Customer"
             quick action and, when the typed text has no exact match
             and the rep can create contacts, "Create '<text>'". -->
    <div t-if="!state.portalUser" class="o_sbk_customer_picker">
      <div t-if="state.partnerId" class="o_sbk_customer_chip">
        <button type="button" class="o_sbk_customer_chip_name"
                t-on-click="_openCustomerForm"
                t-att-title="'Open contact: ' + state.partnerName">
          <t t-esc="state.partnerName"/>
        </button>
        <button type="button" class="o_sbk_customer_chip_remove"
                t-on-click="_unassignCustomer"
                aria-label="Unassign customer">×</button>
      </div>
      <t t-else="">
        <!-- P0 (E2E fix) — disabled while state.loading, same class of
             mutating topbar control as the room-dim inputs above
             (picking a customer here routes through _setCustomer ->
             _markUserAction -> _queueAutoSave, which is now also
             hard-gated on state.loading, but disabling the control
             itself avoids the dead interaction entirely). -->
        <input type="text" class="o_sbk_customer_input"
               placeholder="No customer assigned — search contacts…"
               autocomplete="off"
               role="combobox"
               aria-autocomplete="list"
               aria-haspopup="listbox"
               aria-controls="sbkCustomerListbox"
               aria-label="Search customers"
               t-att-aria-expanded="state.pickerOpen ? 'true' : 'false'"
               t-att-aria-activedescendant="_customerActiveOptionId()"
               t-att-value="state.customerQuery"
               t-att-disabled="state.loading"
               t-on-focus="_openCustomerPicker"
               t-on-blur="_closeCustomerPicker"
               t-on-input="_onCustomerTyped"
               t-on-keydown="_onCustomerInputKeydown"/>
        <div t-if="state.pickerOpen" id="sbkCustomerListbox"
             class="o_sbk_customer_dropdown" role="listbox"
             aria-label="Customer search results">
          <div t-if="state.pickerLoading" class="o_sbk_customer_hint">Searching…</div>
          <div t-if="!state.pickerLoading &amp;&amp; !state.pickerResults.length &amp;&amp; state.customerQuery"
               class="o_sbk_customer_hint">No contacts match "<t t-esc="state.customerQuery"/>"</div>
          <div t-if="!state.pickerLoading &amp;&amp; !state.pickerResults.length &amp;&amp; !state.customerQuery"
               class="o_sbk_customer_hint">Type to search contacts</div>
          <button t-foreach="state.pickerResults" t-as="picked" t-key="picked.id"
                  t-attf-id="sbkCustomerOpt{{picked.id}}"
                  t-att-class="'o_sbk_customer_option' + (_customerActiveOptionId() === ('sbkCustomerOpt' + picked.id) ? ' is-active' : '')"
                  type="button"
                  role="option"
                  t-att-aria-selected="_customerActiveOptionId() === ('sbkCustomerOpt' + picked.id) ? 'true' : 'false'"
                  t-on-mousedown="() => this._setCustomer(picked)">
            <span class="o_sbk_customer_name" t-esc="picked.name"/>
            <span t-if="picked.secondary" class="o_sbk_customer_secondary" t-esc="picked.secondary"/>
          </button>
          <button t-if="state.customerQuery &amp;&amp; state.customerCreateAllowed &amp;&amp; !_customerExactMatch()"
                  class="o_sbk_customer_create"
                  type="button"
                  t-on-mousedown="_createCustomerFromQuery">
            Create "<t t-esc="state.customerQuery"/>"
          </button>
          <div class="o_sbk_customer_sep"/>
          <button class="o_sbk_customer_walkin"
                  type="button"
                  t-on-mousedown="_useWalkInCustomer">
            + Walk-in Customer
          </button>
        </div>
      </t>
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
      <!-- Task 34 (SBK-019) — collapsible-panel toggles. Always
           reachable here in the topbar regardless of which panel (or
           both) is currently collapsed, and regardless of viewport
           width — see narrowAtBoot (kitchen_configurator.js setup())
           for the collapsed-by-default-under-1024px rule these
           override. aria-pressed reflects the PANEL's open state (a
           pressed toggle = "the thing it controls is showing"), not
           the collapsed flag directly.
           Icon choice: U+25E9/U+25EA (diagonal-split squares), NOT the
           horizontal-split U+25E7/U+25E8 the view-switcher toolbar
           below already uses for its Left/Right camera buttons — reusing
           those here would collide two unrelated meanings onto the
           same glyphs in the same page (design-review catch, Task 34). -->
      <div class="o_sbk_panel_toggles" role="group" aria-label="Toggle side panels">
        <button type="button" class="o_sbk_btn o_sbk_btn_ghost o_sbk_panel_toggle"
                t-att-aria-pressed="state.controlsCollapsed ? 'false' : 'true'"
                t-att-aria-label="state.controlsCollapsed ? 'Show room panel' : 'Hide room panel'"
                t-att-title="state.controlsCollapsed ? 'Show room panel' : 'Hide room panel'"
                t-on-click="_toggleControlsPanel">
          <span aria-hidden="true">&#x25E9;</span>
        </button>
        <button type="button" class="o_sbk_btn o_sbk_btn_ghost o_sbk_panel_toggle"
                t-att-aria-pressed="state.inventoryCollapsed ? 'false' : 'true'"
                t-att-aria-label="state.inventoryCollapsed ? 'Show cabinet inventory panel' : 'Hide cabinet inventory panel'"
                t-att-title="state.inventoryCollapsed ? 'Show cabinet inventory panel' : 'Hide cabinet inventory panel'"
                t-on-click="_toggleInventoryPanel">
          <span aria-hidden="true">&#x25EA;</span>
        </button>
      </div>
      <!-- Task 35 — undo/redo (minimal default scope). Icon-only,
           same aria-label/title/disabled pattern as the panel toggles
           above (Task 34 precedent). Icon glyphs U+21B6/U+21B7 (curved
           undo/redo arrows) — distinct from every other glyph already
           in this toolbar (view-switcher U+25E7/U+25E8, panel-toggle
           U+25E9/U+25EA just above). Keyboard: Ctrl/Cmd+Z undo,
           Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redo (_onKeyDown). -->
      <div class="o_sbk_history_controls" role="group" aria-label="Undo and redo">
        <button type="button" class="o_sbk_btn o_sbk_btn_ghost o_sbk_history_btn"
                t-att-disabled="!state.undoCount"
                aria-label="Undo last change"
                title="Undo (Ctrl+Z)"
                t-on-click="_undo">
          <span aria-hidden="true">&#x21B6;</span>
        </button>
        <button type="button" class="o_sbk_btn o_sbk_btn_ghost o_sbk_history_btn"
                t-att-disabled="!state.redoCount"
                aria-label="Redo last undone change"
                title="Redo (Ctrl+Shift+Z)"
                t-on-click="_redo">
          <span aria-hidden="true">&#x21B7;</span>
        </button>
      </div>
      <!-- Task 35 — explicit Auto-arrange command (minimal default
           scope). See _confirmedAutoArrange's own comment for the
           confirm-gate + undo-snapshot ordering and the scoping note
           on why this is the ONLY auto-arrange call site that
           confirms. Disabled with nothing to arrange (no saved design
           yet, or an empty layout). -->
      <button type="button" class="o_sbk_btn o_sbk_btn_ghost"
              t-att-disabled="!state.designId || !state.items.length"
              title="Re-arrange every cabinet with the layout engine"
              t-on-click="_confirmedAutoArrange">
        Auto-arrange
      </button>
      <button class="o_sbk_btn o_sbk_btn_outline"
              t-on-click="_openDesigns">
        Kitchen Designs
      </button>
      <!-- Item 19 (E2E fix) — form-parity actions (Plan PDF / Validate /
           Duplicate as New Version); see _printPlanPdf/_runValidateAction/
           _duplicateAsNewVersion's own doc comments for exactly which
           existing server method each wires to. All three need an
           already-saved design (server-side ensure_one()) and are
           gated on state.loading like the rest of this fix round. -->
      <button type="button" class="o_sbk_btn o_sbk_btn_outline"
              t-att-disabled="!state.designId || state.loading"
              title="Print the dimensioned Plan PDF for this design"
              t-on-click="_printPlanPdf">
        Plan PDF
      </button>
      <button type="button" class="o_sbk_btn o_sbk_btn_outline"
              t-att-disabled="!state.designId || state.loading"
              title="Run the production-readiness checks (collision, BOM, width, soffit)"
              t-on-click="_runValidateAction">
        Validate
      </button>
      <button type="button" class="o_sbk_btn o_sbk_btn_outline"
              t-att-disabled="!state.designId || state.loading"
              title="Copy this design as a new draft version and open it"
              t-on-click="_duplicateAsNewVersion">
        Duplicate as New Version
      </button>
      <!-- Task 19 (corner-remediation 2026-07-30, SBK-032 + C9 UX) —
           persistent production-readiness status chip. Same toolbar-
           button lineage as the panel toggles (Task 34) and undo/redo
           (Task 35) above: an aria-expanded toggle button opening a
           dropdown panel, the same button-triggered-popover pattern
           the customer picker (Task #48) already established. Hidden
           entirely until the design is actually persisted
           (state.designId) — nothing to validate before the first
           save; _refreshValidation() itself no-ops for the same
           reason (see that method's own guard).
           AC: "a blocking issue is visible without leaving the
           configurator" — the chip's tone/label reflect the latest
           save/auto-save/arrange without navigating off this screen. -->
      <div t-if="state.designId" class="o_sbk_validation">
        <button type="button"
                t-att-class="'o_sbk_btn o_sbk_validation_chip o_sbk_vc_' + _validationChip().tone"
                aria-haspopup="true"
                t-att-aria-expanded="state.validation.panelOpen ? 'true' : 'false'"
                t-att-aria-label="'Production check: ' + _validationChip().label + (state.validation.loading ? ' (checking)' : '')"
                t-att-title="'Production check: ' + _validationChip().label"
                t-on-click="_toggleValidationPanel">
          <span class="o_sbk_validation_dot" aria-hidden="true"/>
          <span t-esc="_validationChip().label"/>
        </button>
        <div t-if="state.validation.panelOpen" class="o_sbk_validation_panel"
             role="dialog" aria-label="Production check">
          <div class="o_sbk_validation_panel_head">
            <span class="o_sbk_validation_panel_title">Production check</span>
            <button type="button" class="o_sbk_validation_close"
                    aria-label="Close production check panel"
                    t-on-click="_toggleValidationPanel">×</button>
          </div>
          <div t-if="!state.validation.issues.length" class="o_sbk_customer_hint">
            No blocking issues. Ready to quote.
          </div>
          <t t-else="">
            <!-- Panel grouped by severity, blocking first (brief). -->
            <div t-if="_groupedValidationIssues().blocking.length" class="o_sbk_validation_group">
              <div class="o_sbk_validation_group_title">Blocking</div>
              <ul class="o_sbk_validation_list">
                <li t-foreach="_groupedValidationIssues().blocking" t-as="issue"
                    t-key="issue.code + ':' + (issue.line_ids || []).join(',')"
                    class="o_sbk_validation_issue">
                  <!-- Click selects+reveals the offending cabinet (AC) —
                       reuses _selectCabinet/state.selected (Task 22),
                       never a second selection path. Only rendered as a
                       button when the issue actually names a cabinet
                       still on the canvas; otherwise plain text. -->
                  <button t-if="(issue.layout_keys || []).length" type="button"
                          class="o_sbk_validation_issue_btn"
                          t-on-click="() => this._selectValidationIssue(issue)">
                    <t t-esc="issue.message"/>
                  </button>
                  <span t-else="" class="o_sbk_validation_issue_text" t-esc="issue.message"/>
                  <!-- The one wired fix action (Task 18's existing
                       walk-in flow); every other conceivable fix stays
                       unimplemented per the brief ("others...inert").
                       _fixMissingCustomerFromPanel wraps _useWalkInCustomer
                       with a panel-close + /validate re-fetch so the chip/
                       panel reflect the fix immediately (fix round 1,
                       Minor) — the topbar picker's own walk-in button
                       still calls _useWalkInCustomer directly, unchanged. -->
                  <button t-if="issue.code === 'MISSING_CUSTOMER'" type="button"
                          class="o_sbk_btn o_sbk_btn_ghost o_sbk_validation_fix"
                          t-on-click="_fixMissingCustomerFromPanel">
                    Assign walk-in customer
                  </button>
                </li>
              </ul>
            </div>
            <div t-if="_groupedValidationIssues().warning.length" class="o_sbk_validation_group">
              <div class="o_sbk_validation_group_title">Warnings</div>
              <ul class="o_sbk_validation_list">
                <li t-foreach="_groupedValidationIssues().warning" t-as="issue"
                    t-key="issue.code + ':' + (issue.line_ids || []).join(',')"
                    class="o_sbk_validation_issue">
                  <button t-if="(issue.layout_keys || []).length" type="button"
                          class="o_sbk_validation_issue_btn"
                          t-on-click="() => this._selectValidationIssue(issue)">
                    <t t-esc="issue.message"/>
                  </button>
                  <span t-else="" class="o_sbk_validation_issue_text" t-esc="issue.message"/>
                </li>
              </ul>
            </div>
          </t>
        </div>
      </div>
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
      <!-- Task #48 — Create &amp; Confirm (item 19's "Create Quotation" —
           same action_create_quotation server method the form's header
           buttons call). Only surfaces for internal users (portal
           customers don't create quotes directly; they submit the
           design and the assigned rep confirms). Disabled until the
           design has at least one cabinet, a save is already in
           flight, or (P0 E2E fix) a boot/load is in flight. Firing
           against a KNOWN blocking issue is refused inside
           _createAndConfirm itself (see that guard) rather than here,
           so the click still surfaces a toast pointing at Production
           check instead of just staying inert. -->
      <button t-if="!state.portalUser"
              class="o_sbk_btn o_sbk_btn_confirm"
              t-att-disabled="state.saving || state.loading || !state.items.length"
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

  <!-- Task 20 (SBK-034) — explicit blocking error screen: the design
       id from the URL/params (or a "Continue" click) didn't resolve —
       missing or unreadable (_failHydration, from either an explicit
       NOT_FOUND/ACCESS_DENIED response or a thrown RPC). Never falls
       through to the workspace/scene below with an empty/default room
       — that's exactly the silent-fresh-design bug this task closes. -->
  <div t-elif="state.screen === 'error'" class="o_sbk_fatal_error">
    <strong class="o_sbk_fatal_error_title">Couldn't open this design</strong>
    <p class="o_sbk_fatal_error_body" t-esc="state.error"/>
    <button type="button" class="o_sbk_btn o_sbk_btn_outline"
            t-on-click="_backToLanding">
      ← Back to designs
    </button>
  </div>

  <!-- Main workspace -->
  <!-- Task 34 (SBK-019) — is-controls-collapsed / is-inventory-
       collapsed drive the responsive grid-template-columns overrides
       in the SCSS (placed after the pinned desktop default there —
       see that file's own comment for why the pin exists). -->
  <div t-else=""
       t-att-class="'o_sbk_workspace'
                    + (state.controlsCollapsed ? ' is-controls-collapsed' : '')
                    + (state.inventoryCollapsed ? ' is-inventory-collapsed' : '')">

    <!-- Left controls -->
    <aside class="o_sbk_controls">
      <h3>Summary</h3>

      <!-- Room-settings edit fields removed 2026-08-01 (John): filler
           strategy, wall-cab top alignment, counter thickness, soffit
           height, save-as-default, and the bottom-of-cabinet helper
           line. The STATE behind them (state.fillerStrategy,
           state.wallCabTopAlignment, state.counterThicknessIn,
           state.soffitHeightIn) stays — it seeds from user defaults,
           hydrates from saved designs, and still flows through the
           layout-regeneration and autosave payloads; only the sidebar
           editing surface is gone. The /user_defaults server route is
           deliberately left in place. -->

      <!-- Summary metrics — Task 7 (SBK-033): base/wall/corner are
           always-visible (the V4-counters-templates.md AC: a U-shape
           design must show base/wall/corner counts side by side, not
           corner value silently folded into the $ figure only); tall/
           appliance/panel/unresolved are conditional so the common
           design (none of those) doesn't clutter. All eight numbers —
           base_count, wall_count, tall_count, corner_count,
           filler_count, panel_count, appliance_count, unplaced_count —
           come from state.summary, the SAME object the header total
           above renders from (countCabinetBuckets / server
           _get_counter_summary()) — no separate tally lives here. -->
      <div class="o_sbk_divider"/>

      <div class="o_sbk_metric">
        <strong><t t-esc="state.summary.base_count"/></strong>
        <span class="o_sbk_metric_label">
          <span class="o_sbk_metric_icon" aria-hidden="true"><t t-out="_metricIcon('base')"/></span>
          base cabinets
        </span>
      </div>
      <div class="o_sbk_metric">
        <strong><t t-esc="state.summary.wall_count"/></strong>
        <span class="o_sbk_metric_label">
          <span class="o_sbk_metric_icon" aria-hidden="true"><t t-out="_metricIcon('wall')"/></span>
          wall cabinets
        </span>
      </div>
      <div class="o_sbk_metric">
        <strong><t t-esc="state.summary.corner_count"/></strong>
        <span class="o_sbk_metric_label">
          <span class="o_sbk_metric_icon" aria-hidden="true"><t t-out="_metricIcon('corner')"/></span>
          corner units
        </span>
      </div>
      <div t-if="state.summary.tall_count" class="o_sbk_metric">
        <strong><t t-esc="state.summary.tall_count"/></strong>
        <span class="o_sbk_metric_label">
          <span class="o_sbk_metric_icon" aria-hidden="true"><t t-out="_metricIcon('tall')"/></span>
          tall cabinets
        </span>
      </div>
      <!-- Appliance row deliberately gets no family icon — cabinet_
           thumb.esm.js's thumbFamily() has no "appliance" bucket, and a
           faked one would show an icon that lies about what's placed. -->
      <div t-if="state.summary.appliance_count" class="o_sbk_metric">
        <strong><t t-esc="state.summary.appliance_count"/></strong>
        appliance spaces
      </div>
      <div t-if="state.summary.panel_count" class="o_sbk_metric">
        <strong><t t-esc="state.summary.panel_count"/></strong>
        <span class="o_sbk_metric_label">
          <span class="o_sbk_metric_icon" aria-hidden="true"><t t-out="_metricIcon('panel')"/></span>
          end panels
        </span>
      </div>
      <div t-if="state.summary.unplaced_count" class="o_sbk_metric o_sbk_metric_unplaced">
        <strong><t t-esc="state.summary.unplaced_count"/></strong>
        unresolved
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
    <main t-att-class="'o_sbk_scene_panel'
                       + (state.dragHover ? ' is-drag-hover' : '')
                       + (_isReadOnlyView() ? ' is-readonly-view' : '')"
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
                     designName="state.designName"
                     touchEnabled="_showTouchPad()"
                     onGestureRollback="() => this._recordTouchEvent('rollback')"
                     onGestureAbandon="() => this._recordTouchEvent('abandon')"
                     onWallSelect="(w) => this._onWallSelect(w)"
                     onSelectItem="(item) => this._onCanvasSelect(item)"
                     onMoveItemStart="(item) => this._onCanvasMoveStart(item)"
                     onMoveItem="(p) => this._onCanvasMove(p)"
                     onResizeRoom="(nw, f) => this._onCanvasResize(nw, f)"
                     onResizeRoomDepth="(nd, f) => this._onCanvasResizeDepth(nd, f)"
                     onViewChange="(k) => this._onCanvasViewChange(k)"
                     onReady="(api) => this._onCanvasReady(api)"/>

      <!-- Touch editing v1 (2026-08-02 spec) — tablet-only here (the
           767px CSS gate above means this component never mounts below
           that width), so always "floating" mode; the phone bottom-dock
           mode is the portal's (design_tab.esm.js). Gated on BOTH the
           server kill-switch and a runtime touch-capability probe —
           _showTouchPad() checks both. -->
      <TouchPad t-if="_showTouchPad()"
                mode="'floating'"
                selection="state.selected"
                currentView="state.view"
                capabilities="_touchPadCapabilities()"
                onNudge="(dir) => this._nudgeSelected(dir)"
                onRotate="(deg) => this._rotateSelected(deg)"
                onFlipHand="() => this._flipSelectedHand()"
                onCameraToElevation="() => this._cameraToSelectedElevation()"
                onSendToWall="() => this._sendSelectedToWall()"
                onRemove="() => this._removeSelectedCabinet()"
                onUndo="() => this._undo()"/>

      <!-- Task 22 (SBK-036) — read-only view marker. front/left/right
           are flat elevations with no move-drag/resize; body text
           alone (the o_sbk_draghint pill) wasn't visible enough per
           audit. role="status" so a screen reader announces the mode
           change same as the count strip below. -->
      <div t-if="_isReadOnlyView()" class="o_sbk_readonly_badge" role="status">
        <span class="o_sbk_readonly_icon">&#x1F512;</span> Read-only view — switch to Iso, Top or Perspective to move cabinets
      </div>

      <!-- Task #48 — Persistent count + room + estimate strip.
           Real-time sanity check for the customer / rep. Room feet come
           straight from state.room; estimated total is the already-
           computed state.summary.price so we don't overlap model-side
           pricing work Agent A hasn't scoped.
           Task 7 (SBK-033) — this used to be _nonFillerCount(), a THIRD
           independent tally alongside the sidebar's base/wall-only pair
           and the server's own fields — none of the three agreed once a
           design had a corner cabinet (V4-counters-templates.md claim 1:
           "header 9, sidebar 4+2, record 13, summary 10+2" for one
           design). Now reads state.summary.total — the SAME object the
           sidebar metrics below render from — so header and sidebar can
           never disagree again; identity: base+wall+tall+corner+filler+
           panel+appliance == total == this number == active line count. -->
      <div class="o_sbk_count_strip" role="status" aria-live="polite">
        <span class="o_sbk_count_item">
          <strong t-esc="state.summary.total"/>
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
             active target is obvious even away from the 3D outline.
             Item 16 (E2E fix) — falls back to the SELECTED cabinet's
             wall (_displayActiveWall) rather than reading state.
             activeWall alone, so this only shows "None" when nothing
             has been wall-clicked AND nothing is selected — see
             _displayActiveWall's own comment for the full rationale. -->
        <span class="o_sbk_count_sep">•</span>
        <span class="o_sbk_count_item" role="status" aria-live="polite">
          <span class="o_sbk_count_label">Active Wall: </span>
          <strong t-esc="_wallLabel(_displayActiveWall())"/>
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
      <!-- Item 15 (E2E fix) — every button's highlight + aria-pressed
           reads _isActiveView('<key>'), the single method wrapping
           state.view (see that getter's own comment on _setView above)
           instead of 6 separately-typed inline ternaries. -->
      <div class="o_sbk_viewbar" role="toolbar" aria-label="Camera view">
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (_isActiveView('iso') ? ' is-active' : '')"
                t-att-aria-pressed="_isActiveView('iso')"
                aria-label="Isometric view (1)"
                title="Isometric (1)"
                t-on-click="() => this._setView('iso')">
          <span class="o_sbk_viewicon">⬡</span><span class="o_sbk_viewkey">1</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (_isActiveView('top') ? ' is-active' : '')"
                t-att-aria-pressed="_isActiveView('top')"
                aria-label="Top / Plan view (2)"
                title="Top / Plan (2)"
                t-on-click="() => this._setView('top')">
          <span class="o_sbk_viewicon">▦</span><span class="o_sbk_viewkey">2</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (_isActiveView('front') ? ' is-active' : '')"
                t-att-aria-pressed="_isActiveView('front')"
                aria-label="Front view (3)"
                title="Front (3)"
                t-on-click="() => this._setView('front')">
          <span class="o_sbk_viewicon">▮</span><span class="o_sbk_viewkey">3</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (_isActiveView('left') ? ' is-active' : '')"
                t-att-aria-pressed="_isActiveView('left')"
                aria-label="Left view (4)"
                title="Left side (4)"
                t-on-click="() => this._setView('left')">
          <span class="o_sbk_viewicon">◧</span><span class="o_sbk_viewkey">4</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (_isActiveView('right') ? ' is-active' : '')"
                t-att-aria-pressed="_isActiveView('right')"
                aria-label="Right view (5)"
                title="Right side (5)"
                t-on-click="() => this._setView('right')">
          <span class="o_sbk_viewicon">◨</span><span class="o_sbk_viewkey">5</span>
        </button>
        <button class="o_sbk_viewbtn" t-att-class="'o_sbk_viewbtn' + (_isActiveView('persp') ? ' is-active' : '')"
                t-att-aria-pressed="_isActiveView('persp')"
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

      <!-- SBK-030 — local toast stack. Anchored bottom-left INSIDE the
           scene panel (this <main>, position:relative) — structurally
           incapable of covering the header (o_sbk_topbar, a sibling
           above this element) or either sidebar (o_sbk_controls /
           o_sbk_inventory, both siblings beside it). Caps at 3 visible
           (state.toastView.visible, already capped by
           ToastStore.getView()) with an "N more" rollup; every close
           target is a real ≥32×32 <button>; "Dismiss all" clears the
           whole stack including the rolled-up overflow in one click. -->
      <!-- Task 34 (SBK-020) — container stays role="status"/aria-live
           "polite" for the common (non-blocking) case; a STICKY toast
           is, by construction, one that demands a decision before it
           goes away (a refusal + "Add anyway", a save failure that
           disables further saves — see toast_store.esm.js's header
           and every sticky:true call site in this file) — those
           get role="alert" on the individual toast so assistive tech
           announces them immediately/assertively rather than waiting
           for the next polite-queue opening. undefined (not a falsy
           string) so the attribute is fully ABSENT, not role="", on a
           non-sticky toast. -->
      <div t-if="state.toastView.visible.length" class="o_sbk_toast_stack"
           role="status" aria-live="polite">
        <div t-foreach="state.toastView.visible" t-as="toast" t-key="toast.id"
             t-att-class="'o_sbk_toast o_sbk_toast_' + toast.type"
             t-att-role="toast.sticky ? 'alert' : undefined">
          <div class="o_sbk_toast_body">
            <span class="o_sbk_toast_message">
              <t t-esc="toast.message"/>
              <span t-if="toast.count &gt; 1" class="o_sbk_toast_count">
                × <t t-esc="toast.count"/>
              </span>
            </span>
            <div t-if="toast.actions" class="o_sbk_toast_actions">
              <button t-foreach="toast.actions" t-as="toastAction" t-key="toastAction.label"
                      type="button" class="o_sbk_toast_action"
                      t-on-click="() => this._runToastAction(toast, toastAction)">
                <t t-esc="toastAction.label"/>
              </button>
            </div>
          </div>
          <button type="button" class="o_sbk_toast_close"
                  t-att-aria-label="'Dismiss: ' + toast.message"
                  title="Dismiss"
                  t-on-click="() => this._dismissToast(toast.id)">×</button>
        </div>
        <div class="o_sbk_toast_footer">
          <span t-if="state.toastView.overflowCount &gt; 0" class="o_sbk_toast_rollup">
            +<t t-esc="state.toastView.overflowCount"/> more
          </span>
          <button type="button" class="o_sbk_toast_dismiss_all"
                  t-on-click="_dismissAllToasts">Dismiss all</button>
        </div>
      </div>
    </main>

    <!-- Right inventory + detail panel.
         P3 removals (2026-08-02) — the o_sbk_inv_head block (h3
         "Cabinet Inventory" + the N/M count span) and the made-to-order
         note are gone. The panel lost its only heading, so the aside
         itself now carries aria-label="Cabinet inventory" for
         assistive tech. The filtered-vs-total readout isn't deleted,
         just relocated — see o_sbk_inv_filtered_count below the chip
         rows, silent unless a search/filter is actually narrowing the
         list. The search input moves up to be this panel's FIRST
         element (fixes the top spacing a removed heading left behind). -->
    <aside class="o_sbk_inventory" aria-label="Cabinet inventory">

      <!-- D13 — Searchable inventory. Matches via searchText()
           (catalog_list.esm.js) against parsed title + attrs + sku +
           material — attrs covers the attribute boilerplate (handing,
           finish, etc.) that parseProductName() strips off the row's
           now-plain title, so a search for e.g. "Right Hand" or
           "maple" still finds the right row even though that text no
           longer renders anywhere in the row. Empty = full catalog. -->
      <div class="o_sbk_inv_search">
        <input type="search" class="o_sbk_inv_search_input"
               placeholder="Search by name, SKU, type, material…"
               t-att-value="state.inventorySearch"
               t-on-input="_onSearchInput"
               aria-label="Search cabinet inventory"/>
        <button t-if="state.inventorySearch" class="o_sbk_inv_search_clear"
                t-on-click="_clearSearch" aria-label="Clear search">×</button>
      </div>

      <!-- P2 icons-everywhere — catalog preview/detail. Row clicks only
           PREVIEW (F14 — _previewCatalogProduct never touches
           state.selected), and Panel P2 stripped the parenthetical
           attributes off every row's title to keep the list tight
           (parseProductName, catalog_list.esm.js) — this is where that
           stripped detail belongs: on demand, for the one product being
           looked at, not repeated on all ~48 rows. Fixed icon size and
           a couple of short text lines keep this compact by design so
           it never pushes the grouped list below the fold. -->
      <!-- Task 34 (SBK-020) — t-ref/tabindex="-1"/role/aria-label make
           this an opened-panel focus target for _syncPanelFocus
           (kitchen_configurator.js setup()/onPatched): programmatically
           focusable (never in the normal Tab order — tabindex="-1"),
           named so a screen reader announces what focus just landed
           in. Escape (global keydown) and the × button below both
           close it; either way _syncPanelFocus returns focus to
           whichever row/opener triggered the preview. -->
      <div t-if="state.catalogPreview" class="o_sbk_catalog_preview"
           t-ref="sbkCatalogPreview" tabindex="-1"
           role="region" aria-label="Product preview">
        <button type="button" class="o_sbk_catalog_preview_dismiss"
                t-on-click="_dismissCatalogPreview"
                aria-label="Close product preview">×</button>
        <div class="o_sbk_catalog_preview_thumb">
          <t t-out="_catalogPreviewMarkup()"/>
        </div>
        <div class="o_sbk_catalog_preview_info">
          <strong t-esc="_catalogPreviewParsed().title"/>
          <span t-if="_catalogPreviewParsed().attrs" class="o_sbk_catalog_preview_attrs"
                t-esc="_catalogPreviewParsed().attrs"/>
          <span class="o_sbk_catalog_preview_meta">
            <t t-esc="_catalogPreviewWidthText()"/> · <t t-esc="_money(state.catalogPreview.price)"/>
          </span>
        </div>
      </div>

      <!-- Panel P2 — category chips replace the old "Filter by type"
           <select> (and the F1 fix's per-option corner/tall split,
           which now lives permanently in groupCatalog()'s fixed
           category order — see catalog_list.esm.js). Always visible,
           never a hidden dropdown; counts read straight off
           groupCatalog() so they can never disagree with the grouped
           list's own section headers below. -->
      <div class="o_sbk_inv_chiprow" role="group" aria-label="Filter by cabinet type">
        <button type="button"
                t-att-class="'o_sbk_chip' + (state.inventoryCategory === 'all' ? ' is-active' : '')"
                t-att-aria-pressed="state.inventoryCategory === 'all'"
                t-on-click="() => this._setInventoryCategory('all')">
          All <span class="o_sbk_chip_count" t-esc="_categoryCount('all')"/>
        </button>
        <button t-foreach="_categoryChips()" t-as="cat" t-key="cat.key" type="button"
                t-att-class="'o_sbk_chip' + (state.inventoryCategory === cat.key ? ' is-active' : '')"
                t-att-aria-pressed="state.inventoryCategory === cat.key"
                t-on-click="() => this._setInventoryCategory(cat.key)">
          <t t-esc="cat.label"/> <span class="o_sbk_chip_count" t-esc="cat.count"/>
        </button>
      </div>

      <!-- Panel P2 — width chips, derived from groupCatalog().widths
           (never hardcoded), so the row grows/shrinks automatically as
           the real catalog's width range changes. Multi-exclusive
           single-select with an IMPLICIT "all": no dedicated All chip
           here (unlike the category row above) — clicking the already-
           active width chip clears it back to "all" (_setInventoryWidth). -->
      <div t-if="_widthChips().length" class="o_sbk_inv_chiprow o_sbk_inv_chiprow_width"
           role="group" aria-label="Filter by width">
        <span class="o_sbk_inv_chiprow_label">Width</span>
        <button t-foreach="_widthChips()" t-as="w" t-key="w" type="button"
                t-att-class="'o_sbk_chip o_sbk_chip_width' + (state.inventoryWidth === w ? ' is-active' : '')"
                t-att-aria-pressed="state.inventoryWidth === w"
                t-on-click="() => this._setInventoryWidth(w)"
                t-esc="_fmtWidthIn(w)"/>
      </div>

      <!-- P3 removals (2026-08-02) — relocated from the removed
           o_sbk_inv_head count span. Silent unless a search/filter is
           actually narrowing the list (_isInventoryFiltered) — the
           unfiltered "48 of 48" case said nothing useful, so it says
           nothing at all. -->
      <div t-if="_isInventoryFiltered()" class="o_sbk_inv_filtered_count">
        Showing <t t-esc="_filteredProducts().length"/> of <t t-esc="state.products.length"/>
      </div>

      <!-- D13 — Drag hint pill. Only shown when search has results so it
           doesn't clutter the empty state. -->
      <div t-if="_filteredProducts().length" class="o_sbk_inv_draghint">
        ↔ Drag any product into the scene to add it
      </div>

      <!-- Panel P2 — grouped, sticky-header list. groupCatalog()
           (catalog_list.esm.js) buckets the already-filtered products
           by category in its fixed order, L/R-merges handed pairs, and
           sorts each group by width.
           F14 fix (2026-07-27), preserved — row click PREVIEWS/
           highlights the catalog product (_previewCatalogProduct,
           writes state.catalogPreview) instead of writing
           state.selected — a raw catalog product has no layout_key, so
           the edit drawer's actions would silently no-op against it.
           is-selected lights up for EITHER a matching real selection or
           a matching preview, resolved against the row's CURRENT L/R
           toggle side (_resolveRowProduct) for pair rows.
           F15 fix (2026-07-27), preserved but restructured — "+ Add" is
           a real, keyboard-reachable <button>. It (and, for pair rows,
           the L/R toggle) can no longer be a SIBLING of a literal row
           <button> the way F15 originally built it: this row now needs
           to host that toggle AND Add as independently-clickable
           controls, and a <button> can never legally contain another
           <button> (nested buttons get silently mangled by the HTML
           parser). The row is instead a role="button" + tabindex="0"
           container (_onRowKeydown gives it the same Enter/Space
           activation a native <button> has for free — WAI-ARIA APG
           button pattern) with real nested <button>s for the toggle
           and Add; both stop click propagation so activating them never
           also fires the row's own preview click. Appends to the
           active wall per F7's fixed precedence (state.activeWall wins
           over any stale drop-wall override). -->
      <div class="o_sbk_product_list">
        <t t-foreach="_groupedInventory().categories" t-as="cat" t-key="cat.key">
          <div class="o_sbk_cat_header"
               t-esc="cat.label.toUpperCase() + ' (' + cat.count + ')'"/>
          <t t-foreach="cat.rows" t-as="row"
             t-key="row.kind === 'pair' ? (row.left.product_id + '-' + row.right.product_id) : row.product.product_id">
            <t t-set="resolved" t-value="_resolveRowProduct(row)"/>
            <div
                t-att-class="'o_sbk_product_row o_sbk_prod_draggable' + ((state.selected &amp;&amp; state.selected.product_id === resolved.product_id) || (state.catalogPreview &amp;&amp; state.catalogPreview.product_id === resolved.product_id) ? ' is-selected' : '')"
                role="button"
                tabindex="0"
                draggable="true"
                t-on-dragstart="(ev) => this._onProductDragStart(ev, resolved)"
                t-on-dragend="(ev) => this._onProductDragEnd(ev)"
                t-on-click="() => this._previewCatalogProduct(resolved)"
                t-on-keydown="(ev) => this._onRowKeydown(ev, resolved)"
                t-att-title="_rowTitle(resolved)">
              <div class="o_sbk_prod_thumb">
                <t t-out="_rowThumbMarkup(resolved)"/>
              </div>
              <div class="o_sbk_prod_info">
                <div class="o_sbk_prod_title_line">
                  <strong t-esc="row.title"/>
                  <span t-if="row.kind === 'pair'" class="o_sbk_handing_toggle"
                        role="group" aria-label="Handing">
                    <button type="button"
                            t-att-class="'o_sbk_handing_btn' + (_pairHanding(row) === 'L' ? ' is-active' : '')"
                            t-att-aria-pressed="_pairHanding(row) === 'L'"
                            t-on-click.stop="() => this._setPairHanding(row, 'L')">L</button>
                    <button type="button"
                            t-att-class="'o_sbk_handing_btn' + (_pairHanding(row) === 'R' ? ' is-active' : '')"
                            t-att-aria-pressed="_pairHanding(row) === 'R'"
                            t-on-click.stop="() => this._setPairHanding(row, 'R')">R</button>
                  </span>
                </div>
                <div class="o_sbk_prod_meta_line">
                  <span t-if="row.dataFlags.includes('missing_width')"
                        class="o_sbk_data_flag" title="No width on file for this product">no width</span>
                  <span t-else="" class="o_sbk_prod_dim" t-esc="_fmtWidthIn(row.width_in)"/>
                  <span class="o_sbk_prod_price" t-esc="_money(row.price)"/>
                  <span t-if="row.dataFlags.includes('missing_sku')"
                        class="o_sbk_data_flag" title="No SKU on file for this product">no SKU</span>
                  <span t-else="" class="o_sbk_prod_sku" t-esc="row.sku"/>
                </div>
              </div>
              <button type="button" class="o_sbk_prod_add_btn"
                      draggable="false"
                      t-on-click.stop="() => this._addCabinetFromProduct(resolved, null, null)"
                      t-att-aria-label="'Add ' + resolved.name + ' to the layout'"
                      title="Add to layout">+ Add</button>
            </div>
          </t>
        </t>
        <!-- D13 — Empty-state when search/filters yield zero matches. -->
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
      <!-- Task 34 (SBK-020) — same opened-panel focus-target contract
           as .o_sbk_catalog_preview above: t-ref/tabindex="-1" so
           _syncPanelFocus can move focus in when the drawer opens
           (a canvas click, an inventory "+ Add", or a fresh add) and
           Escape/Remove/deselect all route back through the SAME
           close-transition, which returns focus to whatever opened
           it. aria-labelledby names the panel from its own visible
           heading instead of duplicating that text in a fresh
           aria-label. -->
      <div t-if="state.selected &amp;&amp; state.selected.layout_key" class="o_sbk_detail"
           t-ref="sbkDetailPanel" tabindex="-1"
           role="region" aria-labelledby="sbkDetailHeading">
        <div class="o_sbk_detail_head">
          <h4 id="sbkDetailHeading">Selected Cabinet</h4>
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
                    t-on-change="(ev) => this._swapSelectedProduct(ev.target.value, ev.target)">
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
            <!-- Task 12 (corner-remediation, SBK-021/022 claim 6) —
                 inline, next to the field; see _updateSelectedWidth. -->
            <span t-if="state.selectedWidthError" class="o_sbk_field_error"
                  t-esc="state.selectedWidthError"/>
          </label>
        </div>

        <!-- Fix 10 (E2E P1) — Placement block. Before this, the panel
             had no field to type the number that fixes an overlap
             blocker. Wall picker reuses the SAME action_move RPC the
             touch pad's send-to-wall cycle button already calls
             (_sendSelectedToWallTarget, via _setSelectedWall); the
             offset field + 1/8" nudge buttons reuse the SAME
             save_position route the drag/keyboard-nudge path already
             calls (_savePinnedPosition, via _setSelectedAlongWallOffset
             / _nudgeSelectedFine) — no new server route either way.
             Reuses the EXISTING o_sbk_edit_grid/o_sbk_edit_field/
             o_sbk_edit_select/o_sbk_edit_input classes above (this is
             a JS-file-only fix — no new SCSS); o_sbk_nudge_btn/
             o_sbk_placement_offset are unstyled hooks for a future
             styling pass and render with plain browser button chrome
             until then. Per-affordance visibility uses the two simple
             no-arg booleans below (_selectedCanSendToWall/
             _selectedCanNudgePlacement) rather than a chained
             getPadAffordances(...).x expression in the binding itself,
             matching this template's existing single-call convention
             (_sameTypeProducts/_materialLabel above) — so e.g. a
             corner cabinet never shows a wall picker it can't use.
             "Nothing selected" is already covered by this whole
             panel's own outer t-if; state.loading disables each
             control individually, the same P0 (E2E fix) convention
             every other mutating control in this template follows. -->
        <div class="o_sbk_edit_grid">
          <label t-if="_selectedCanSendToWall()" class="o_sbk_edit_field">
            <span>Wall</span>
            <select class="o_sbk_edit_select"
                    t-att-value="state.selected.wall"
                    t-att-disabled="state.loading"
                    t-on-change="(ev) => this._setSelectedWall(ev.target.value)">
              <option value="back">Back</option>
              <option value="left">Left</option>
              <option value="front">Front</option>
              <option value="right">Right</option>
            </select>
          </label>
          <label t-if="_selectedCanNudgePlacement()" class="o_sbk_edit_field">
            <span>Position along wall (in)</span>
            <div class="o_sbk_placement_offset">
              <button type="button" class="o_sbk_nudge_btn"
                      aria-label="Nudge 1/8 inch back"
                      t-att-disabled="state.loading"
                      t-on-click="() => this._nudgeSelectedFine(-1)">−</button>
              <input type="number" step="0.125" min="0" class="o_sbk_edit_input"
                     t-att-value="_selectedAlongWallOffset()"
                     t-att-disabled="state.loading"
                     t-on-change="(ev) => this._setSelectedAlongWallOffset(ev.target.value)"/>
              <button type="button" class="o_sbk_nudge_btn"
                      aria-label="Nudge 1/8 inch forward"
                      t-att-disabled="state.loading"
                      t-on-click="() => this._nudgeSelectedFine(1)">+</button>
            </div>
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
          <!-- Task 23 (SBK-037) — same "Made to order" swap as the
               inventory list column above; every placeable/placed item
               here is a southbrook_is_cabinet=True product. -->
          <dt>Availability</dt><dd class="o_sbk_madetoorder">Made to order</dd>
          <dt>BoM</dt>      <dd><t t-if="state.selected.bom_available">✓ Available</t><t t-else="">—</t></dd>
          <dt>Price</dt>    <dd class="price"><t t-esc="_money(state.selected.price)"/></dd>
        </dl>
      </div>

    </aside>
  </div>
  </t>
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
SouthbrookKitchenConfigurator.components = { KitchenCanvas, TouchPad };

// Rec D · Sprint 2d Step 24c bridge — child-to-parent callbacks.
// Injects fire-and-forget state mutations into the parent lifecycle
// so the child stays pure viewport (no RPCs, no notification writes).
SouthbrookKitchenConfigurator.prototype._onCanvasReady = function (api) {
    this._canvasApi = api;
};
SouthbrookKitchenConfigurator.prototype._onCanvasSelect = function (item) {
    this.state.selected = item;
};
// Task 35 — fires exactly once per drag gesture, BEFORE <KitchenCanvas>
// applies its first in-place x_position_in mutation to `item` (a live
// reference into state.items) — see kitchen_canvas.esm.js's own
// comment on `onMoveItemStart` for why this has to be a separate
// callback rather than derived from onMoveItem's (post-drag) payload.
// state.items is still fully pristine at this instant, so a plain
// snapshot of the CURRENT state is the correct pre-op capture — no
// special-casing of the moved item's old position is needed.
SouthbrookKitchenConfigurator.prototype._onCanvasMoveStart = function (_item) {
    this._pushUndoSnapshot();
};
SouthbrookKitchenConfigurator.prototype._onCanvasMove = function ({ item, x_position_in, pinnable }) {
    this._markUserAction();   // T7 gate
    this._placementsDirty = true;   // Task 35
    if (pinnable) {
        item.pinned = true;
        this._savePinnedPosition(item);
    }
    this._recomputeLayoutFromItems();
    this._toast(
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
