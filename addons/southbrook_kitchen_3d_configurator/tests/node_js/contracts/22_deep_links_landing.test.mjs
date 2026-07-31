/**
 * Task 20 (SBK-034 · corner-remediation-2026-07-30) — deep links + no
 * silent new design.
 *
 * Audit finding this fixes: reloading a design URL rendered blank then
 * silently reset to a fresh empty 12×24×96 design, abandoning the
 * saved one; entering from the menu always started a new design;
 * there was no recent-designs entry point. Root cause (client side):
 * `_hydrateFromDesign` read `load_design_lines`'s `lines: []]` — the
 * SAME bare shape for "this design doesn't exist / isn't yours" and
 * "this design exists but has nothing saved yet" — and silently ran
 * the starter-layout generator for both. `onWillStart` also ran that
 * same generator unconditionally whenever no design id was present,
 * with no user-facing choice.
 *
 * The fix (kitchen_configurator.js + controllers/main.py):
 *   - `state.screen` ('landing' | 'workspace' | 'error') decides the
 *     top-level template branch BEFORE any product/layout fetch and
 *     BEFORE <KitchenCanvas> (the 3D scene) ever mounts.
 *   - load_design_lines now returns an explicit `error_code`
 *     (NOT_FOUND / ACCESS_DENIED) instead of a bare `{"lines": []}`
 *     for a missing/unreadable design; `_hydrateFromDesign` routes
 *     that (and any thrown RPC) through `_failHydration`, which sets
 *     `state.screen = 'error'` — never falls through to the workspace
 *     with an empty/default room.
 *   - No design id -> `state.screen = 'landing'`; nothing is
 *     generated or created until the rep explicitly calls
 *     `_startNewDesign()` ("New Design") or `_continueDesign(id)`
 *     ("Continue <design>", fed by the new /recent_designs endpoint).
 *
 * This file mounts with `autoAdvancePastLanding: false` throughout —
 * every other contract file in this suite relies on the harness's
 * DEFAULT (true) to skip straight past the landing screen; this is
 * the one file that deliberately observes it.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const LINES = [
    { id: 601, layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
      width_in: 24, height_in: 34.5, depth_in: 24,
      x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0 },
];

// ─── Boot with a valid id ───────────────────────────────────────────────────

test("boot with a valid design id loads that design straight into the workspace — no /layout, no create call", async () => {
    let hydrateCalls = 0;
    let saveCalls = 0;
    const h = await mountConfigurator({
        actionParams: { design_id: 601 },
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": (params) => {
                hydrateCalls++;
                assert.equal(params.design_id, 601);
                return { room: { width_in: 96, depth_in: 96, height_in: 96 },
                          design_name: "Design 601", lines: LINES };
            },
            "/southbrook_kitchen/configurator/save": () => {
                saveCalls++;
                return { id: 601, name: "Design 601", lines: [], placed: [] };
            },
        },
    });
    assert.equal(hydrateCalls, 1, "the id resolves via load_design_lines exactly once");
    assert.equal(h.component.state.screen, "workspace");
    assert.equal(h.component.state.loading, false);
    assert.equal(h.component.state.items.length, 1);
    assert.equal(h.component._hydratedFromDesign, true);
    assert.equal(
        h.rpcCalls("/southbrook_kitchen/configurator/layout").length, 0,
        "a resolved id must never run the fresh-layout generator");
    assert.equal(saveCalls, 0, "loading an existing design must never create/write a record");
    h.destroy();
});

test("boot with a valid id never mounts <KitchenCanvas> before the record resolves (no blank/empty first paint)", async () => {
    // owl.mount() only resolves once onWillStart (which awaits
    // _hydrateFromDesign, which awaits this RPC — deliberately
    // delayed a tick to prove the ordering, not just assert its
    // eventual result) has fully settled, and a component's very
    // FIRST render (unlike later state-triggered re-renders) is part
    // of that same awaited mount — so <KitchenCanvas> cannot possibly
    // render with a pre-hydration empty scene: it either doesn't
    // exist yet (screen==='loading'/'landing') or exists already
    // carrying the real hydrated items.
    let hydrateResolved = false;
    const h = await mountConfigurator({
        actionParams: { design_id: 602 },
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": async (params) => {
                assert.equal(params.design_id, 602);
                await new Promise((r) => setTimeout(r, 5));
                hydrateResolved = true;
                return { lines: LINES };
            },
        },
    });
    assert.equal(hydrateResolved, true, "mountConfigurator() only returned after the delayed hydrate actually resolved");
    assert.equal(h.component.state.screen, "workspace");
    assert.equal(h.canvasMountCount(), 1, "<KitchenCanvas> mounts exactly once, after the record resolved");
    assert.equal(h.canvasProps().items.length, 1, "the scene's first mount already carries the real hydrated items, never an empty placeholder");
    h.destroy();
});

// ─── Boot with a bad id ─────────────────────────────────────────────────────

test("boot with a NOT_FOUND design id shows the error screen — no create, no silent fresh-design fallback", async () => {
    let layoutCalls = 0;
    let saveCalls = 0;
    const h = await mountConfigurator({
        actionParams: { design_id: 999999 },
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                lines: [], error: "This design no longer exists.", error_code: "NOT_FOUND",
            }),
            "/southbrook_kitchen/configurator/layout": () => { layoutCalls++; return { items: [], summary: {} }; },
            "/southbrook_kitchen/configurator/save": () => { saveCalls++; return { id: 1, name: "x" }; },
        },
    });
    assert.equal(h.component.state.screen, "error");
    assert.equal(h.component.state.errorCode, "NOT_FOUND");
    assert.match(h.component.state.error, /no longer exists/);
    assert.equal(h.component._hydrationFailed, true);
    assert.equal(layoutCalls, 0, "a bad id must never fall back to the fresh-layout generator");
    assert.equal(h.component.state.items.length, 0, "no phantom items appear behind the error screen");

    // Belt + suspenders: the write paths this task's brief calls out
    // ("no create on bare load") must both refuse while the error
    // screen is up, exactly like the pre-existing transient-RPC-
    // failure path already did.
    await h.component._saveDesign();
    h.component._queueAutoSave();
    assert.equal(saveCalls, 0, "no /save RPC while the design id never resolved");
    h.destroy();
});

test("boot with an ACCESS_DENIED design id shows the error screen with the access-denied message", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 42 },
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                lines: [], error: "You don't have access to this design.", error_code: "ACCESS_DENIED",
            }),
        },
    });
    assert.equal(h.component.state.screen, "error");
    assert.equal(h.component.state.errorCode, "ACCESS_DENIED");
    assert.match(h.component.state.error, /don't have access/);
    h.destroy();
});

test("the error screen's 'Back to designs' action returns to the landing screen and clears the bad id", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 999999 },
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                lines: [], error: "gone", error_code: "NOT_FOUND",
            }),
            "/southbrook_kitchen/configurator/recent_designs": () => ([]),
        },
    });
    assert.equal(h.component.state.screen, "error");
    h.component._backToLanding();
    await tick();
    assert.equal(h.component.state.screen, "landing");
    assert.equal(h.component.state.designId, null);
    assert.equal(h.component.state.errorCode, "");
    h.destroy();
});

// ─── Boot with no id ────────────────────────────────────────────────────────

test("boot with no design id shows the landing screen — no /layout, no create, no <KitchenCanvas>", async () => {
    let layoutCalls = 0;
    const h = await mountConfigurator({
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/layout": () => { layoutCalls++; return { items: [], summary: {} }; },
            "/southbrook_kitchen/configurator/recent_designs": () => ([
                { id: 7, name: "Kitchen A", partner_name: "Acme", room_width_in: 120, room_depth_in: 96 },
            ]),
        },
    });
    assert.equal(h.component.state.screen, "landing");
    assert.equal(h.component.state.loading, false, "the landing screen itself is never gated behind the loading spinner");
    assert.equal(layoutCalls, 0, "no id must never auto-generate a layout on bare boot");
    assert.equal(h.canvasMountCount(), 0, "the 3D scene never mounts on the landing screen");
    await tick();
    assert.equal(h.component.state.recentDesigns.length, 1);
    assert.equal(h.component.state.recentDesigns[0].name, "Kitchen A");
    h.destroy();
});

test("landing screen's 'New Design' (_startNewDesign) is the only thing that generates a layout — and still creates no record", async () => {
    let layoutCalls = 0;
    let saveCalls = 0;
    const h = await mountConfigurator({
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/layout": () => {
                layoutCalls++;
                return { items: [{ layout_key: "new1", cabinet_type: "base", name: "Base 24" }], summary: {} };
            },
            "/southbrook_kitchen/configurator/save": () => { saveCalls++; return { id: 1, name: "x" }; },
        },
    });
    assert.equal(h.component.state.screen, "landing");
    await h.component._startNewDesign();
    await tick();
    assert.equal(h.component.state.screen, "workspace");
    assert.equal(layoutCalls, 1, "New Design runs the generator exactly once, on explicit click");
    assert.equal(h.component.state.items.length, 1);
    assert.equal(saveCalls, 0, "generating a starter layout must never itself create/write a design record");
    assert.equal(h.canvasMountCount(), 1, "<KitchenCanvas> mounts once New Design has actually produced a layout");
    h.destroy();
});

test("landing screen's 'Continue' (_continueDesign) hydrates the chosen design in place", async () => {
    let hydrateCalls = 0;
    const h = await mountConfigurator({
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/recent_designs": () => ([
                { id: 88, name: "Kitchen B", partner_name: "", room_width_in: 96, room_depth_in: 96 },
            ]),
            "/southbrook_kitchen/configurator/load_design_lines": (params) => {
                hydrateCalls++;
                assert.equal(params.design_id, 88);
                return { lines: LINES, design_name: "Kitchen B" };
            },
        },
    });
    await tick();
    assert.equal(h.component.state.recentDesigns[0].id, 88);
    await h.component._continueDesign(88);
    await tick();
    assert.equal(hydrateCalls, 1);
    assert.equal(h.component.state.screen, "workspace");
    assert.equal(h.component.state.designId, 88);
    assert.equal(h.component.state.items.length, 1);
    h.destroy();
});

test("bouncing landing -> Continue design A -> back to landing -> Continue design B never leaks design A's selection/items into B", async () => {
    // The landing screen lets a rep switch designs without a full
    // component remount (unlike the pre-existing "Kitchen Designs"
    // list doAction, which always destroys + remounts). Without an
    // explicit reset, a stale `state.selected` from design A would
    // survive into design B's session — _hydrateFromDesign only
    // backfills `state.selected` when it's currently falsy — silently
    // pointing the edit drawer at an object that isn't even in B's
    // items array.
    const linesA = [{ id: 701, layout_key: "a1", cabinet_type: "base",
        product_name: "Design A Base", width_in: 24, height_in: 34.5,
        depth_in: 24, x_position_in: 0, y_position_in: 0, z_position_in: 0 }];
    const linesB = [{ id: 702, layout_key: "b1", cabinet_type: "wall",
        product_name: "Design B Wall", width_in: 30, height_in: 30,
        depth_in: 12, x_position_in: 0, y_position_in: 54, z_position_in: 0 }];
    const h = await mountConfigurator({
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": (params) => {
                if (params.design_id === 701) return { lines: linesA, design_name: "Design A" };
                if (params.design_id === 702) return { lines: linesB, design_name: "Design B" };
                throw new Error("unexpected design_id " + params.design_id);
            },
        },
    });
    await h.component._continueDesign(701);
    await tick();
    assert.equal(h.component.state.designId, 701);
    assert.equal(h.component.state.selected.layout_key, "a1");
    assert.equal(h.component.state.designName, "Design A");

    h.component._backToLanding();
    await tick();
    assert.equal(h.component.state.screen, "landing");

    await h.component._continueDesign(702);
    await tick();
    assert.equal(h.component.state.designId, 702);
    assert.equal(h.component.state.items.length, 1);
    assert.equal(h.component.state.items[0].layout_key, "b1",
        "design B's items must not carry design A's line");
    assert.equal(h.component.state.selected.layout_key, "b1",
        "the selected cabinet must be design B's, never a leftover from A");
    assert.equal(h.component.state.designName, "Design B");
    h.destroy();
});

test("landing screen renders in the real DOM with a New Design button and no workspace/canvas markup", async () => {
    const h = await mountConfigurator({ autoAdvancePastLanding: false });
    await tick();
    assert.ok(h.root.querySelector(".o_sbk_landing"), "landing container is in the DOM");
    assert.ok(!h.root.querySelector(".o_sbk_workspace"), "workspace must not be in the DOM alongside landing");
    assert.ok(!h.root.querySelector(".o_sbk_canvas_mock"), "the (mocked) 3D canvas must not be in the DOM on landing");
    const newBtn = [...h.root.querySelectorAll("button")].find(
        (b) => b.textContent.includes("New Design"));
    assert.ok(newBtn, "a New Design button is present");
    h.destroy();
});

// ─── Harness teardown ───────────────────────────────────────────────────────

test("harness destroy() teardown runs onWillUnmount for a landing-screen mount (no leaked timers)", async () => {
    const h = await mountConfigurator({ autoAdvancePastLanding: false });
    assert.equal(h.component.state.screen, "landing");
    // Must not throw, and must be safe to call exactly once.
    h.destroy();
});

test("harness destroy() teardown runs onWillUnmount for a hydrated-workspace mount", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 603 },
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({ lines: LINES }),
        },
    });
    assert.equal(h.component.state.screen, "workspace");
    h.destroy();
});

test("harness destroy() teardown runs onWillUnmount for an error-screen mount", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 999999 },
        autoAdvancePastLanding: false,
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                lines: [], error: "gone", error_code: "NOT_FOUND",
            }),
        },
    });
    assert.equal(h.component.state.screen, "error");
    h.destroy();
});
