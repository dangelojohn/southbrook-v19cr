/**
 * Task 34 (SBK-019, corner-remediation-2026-07-30) — responsive
 * (scoped): collapsible side panels.
 *
 * Scope per the task-34 brief: the configurator page chrome only, NOT
 * a site-wide overhaul. Usable at tablet width (>=768px) and narrow
 * desktop (>=1024px) via two independent panel-collapse toggles (the
 * left "Room" controls aside and the right "Cabinet Inventory" aside),
 * collapsed by default under 1024px, always reachable regardless of
 * viewport width via two topbar buttons.
 *
 * What this file covers (JS/state contract — layout math itself is
 * pure CSS and isn't executable in jsdom, which has no layout engine;
 * verified instead by the libsass CI compile + a manual checklist —
 * see task-34-report.md):
 *   1. `isNarrowViewportAtBoot` (pure, exported) — the guarded
 *      matchMedia check that decides the collapsed-by-default state,
 *      unit-tested directly against a fake `window`-like object (no
 *      jsdom mount needed for this part).
 *   2. The two panels default to EXPANDED in this harness (jsdom has
 *      no `matchMedia` polyfill — confirmed empirically), matching
 *      every pre-existing contract test's assumption that both side
 *      panels render open.
 *   3. `_toggleControlsPanel` / `_toggleInventoryPanel` flip their own
 *      state field independently and drive the `.o_sbk_workspace`
 *      modifier classes the SCSS keys its grid-template-columns
 *      overrides off (`is-controls-collapsed` / `is-inventory-
 *      collapsed`).
 *   4. The topbar renders exactly two icon-only toggle buttons, each
 *      with a real accessible name (aria-label) and an aria-pressed
 *      state that tracks the PANEL's open state — not the collapsed
 *      flag's raw boolean — and clicking one is a real, keyboard-
 *      reachable <button> action.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CONFIGURATOR_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "js", "kitchen_configurator.js",
);

// See 24_counter_summary.test.mjs's identical helper for why a
// throwaway mountConfigurator() call must happen BEFORE the first
// dynamic import() of kitchen_configurator.js in this process: Owl's
// ESM build reads DOM globals at module-evaluation time, and an
// independent setupDom()+import() done before any real owl.mount()
// binds the compiled template to a jsdom realm that never actually
// gets used for mounting.
async function loadIsNarrowViewportAtBoot() {
    await mountConfigurator({});
    const { isNarrowViewportAtBoot } = await import(CONFIGURATOR_SRC);
    return isNarrowViewportAtBoot;
}

// ─── Part 1 — isNarrowViewportAtBoot (pure, no DOM) ────────────────────────

test("isNarrowViewportAtBoot: true when matchMedia reports the narrow query matches", async () => {
    const isNarrowViewportAtBoot = await loadIsNarrowViewportAtBoot();
    const fakeWin = { matchMedia: (q) => ({ matches: q === "(max-width: 1023px)" }) };
    assert.equal(isNarrowViewportAtBoot(fakeWin), true);
});

test("isNarrowViewportAtBoot: false when matchMedia reports no match (a wide/desktop viewport)", async () => {
    const isNarrowViewportAtBoot = await loadIsNarrowViewportAtBoot();
    const fakeWin = { matchMedia: () => ({ matches: false }) };
    assert.equal(isNarrowViewportAtBoot(fakeWin), false);
});

test("isNarrowViewportAtBoot: false, never throws, when matchMedia is missing entirely (this test harness's real jsdom shape)", async () => {
    const isNarrowViewportAtBoot = await loadIsNarrowViewportAtBoot();
    assert.equal(isNarrowViewportAtBoot({}), false);
    assert.equal(isNarrowViewportAtBoot(null), false);
    assert.equal(isNarrowViewportAtBoot(undefined), false);
});

test("isNarrowViewportAtBoot: false, never throws, when matchMedia itself throws", async () => {
    const isNarrowViewportAtBoot = await loadIsNarrowViewportAtBoot();
    const fakeWin = { matchMedia: () => { throw new Error("boom"); } };
    assert.equal(isNarrowViewportAtBoot(fakeWin), false);
});

// ─── Part 2 — component defaults + toggle behavior ─────────────────────────

test("both side panels default to EXPANDED when window.matchMedia is unavailable (this harness's real boot condition)", async () => {
    const h = await mountConfigurator({});
    assert.equal(h.component.state.controlsCollapsed, false);
    assert.equal(h.component.state.inventoryCollapsed, false);
    const ws = h.root.querySelector(".o_sbk_workspace");
    assert.ok(ws, "workspace must be in the DOM");
    assert.ok(!ws.className.includes("is-controls-collapsed"));
    assert.ok(!ws.className.includes("is-inventory-collapsed"));
    h.destroy();
});

test("_toggleControlsPanel flips state.controlsCollapsed and the workspace modifier class, independently of the inventory panel", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    c._toggleControlsPanel();
    await tick();
    assert.equal(c.state.controlsCollapsed, true);
    assert.equal(c.state.inventoryCollapsed, false, "toggling controls must not touch inventory");
    let ws = h.root.querySelector(".o_sbk_workspace");
    assert.ok(ws.className.includes("is-controls-collapsed"));
    assert.ok(!ws.className.includes("is-inventory-collapsed"));

    c._toggleControlsPanel();
    await tick();
    assert.equal(c.state.controlsCollapsed, false);
    ws = h.root.querySelector(".o_sbk_workspace");
    assert.ok(!ws.className.includes("is-controls-collapsed"), "toggling again must restore it");
    h.destroy();
});

test("_toggleInventoryPanel flips state.inventoryCollapsed and the workspace modifier class, independently of the controls panel", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    c._toggleInventoryPanel();
    await tick();
    assert.equal(c.state.inventoryCollapsed, true);
    assert.equal(c.state.controlsCollapsed, false, "toggling inventory must not touch controls");
    const ws = h.root.querySelector(".o_sbk_workspace");
    assert.ok(ws.className.includes("is-inventory-collapsed"));
    assert.ok(!ws.className.includes("is-controls-collapsed"));
    h.destroy();
});

test("both panels can be collapsed at once — the workspace carries BOTH modifier classes", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    c._toggleControlsPanel();
    c._toggleInventoryPanel();
    await tick();
    const ws = h.root.querySelector(".o_sbk_workspace");
    assert.ok(ws.className.includes("is-controls-collapsed"));
    assert.ok(ws.className.includes("is-inventory-collapsed"));
    h.destroy();
});

test("the topbar renders exactly two icon-only panel toggles, each with an accessible name and aria-pressed tracking the panel's open state", async () => {
    const h = await mountConfigurator({});
    const toggles = [...h.root.querySelectorAll(".o_sbk_panel_toggle")];
    assert.equal(toggles.length, 2, "one toggle per side panel");
    for (const btn of toggles) {
        assert.equal(btn.tagName, "BUTTON", "must be a real, keyboard-reachable control");
        assert.equal(btn.getAttribute("type"), "button");
        assert.ok(btn.getAttribute("aria-label"), "icon-only control needs an accessible name (SBK-020)");
        assert.equal(btn.getAttribute("aria-pressed"), "true", "expanded by default -> pressed=true");
    }
    h.destroy();
});

test("clicking the controls-panel toggle button flips its own aria-pressed/aria-label and collapses the workspace column, leaving the inventory toggle untouched", async () => {
    const h = await mountConfigurator({});
    const [controlsToggle, inventoryToggle] = [...h.root.querySelectorAll(".o_sbk_panel_toggle")];
    controlsToggle.click();
    await tick();
    const refreshed = [...h.root.querySelectorAll(".o_sbk_panel_toggle")];
    assert.equal(refreshed[0].getAttribute("aria-pressed"), "false");
    assert.match(refreshed[0].getAttribute("aria-label"), /show/i);
    assert.equal(refreshed[1].getAttribute("aria-pressed"), "true", "inventory toggle unaffected");
    assert.equal(h.component.state.controlsCollapsed, true);
    assert.equal(h.component.state.inventoryCollapsed, false);
    h.destroy();
});

test("the left toggle controls the Room aside and the right toggle controls the Cabinet Inventory aside (button order matches the panel it names)", async () => {
    const h = await mountConfigurator({});
    const [controlsToggle, inventoryToggle] = [...h.root.querySelectorAll(".o_sbk_panel_toggle")];
    assert.match(controlsToggle.getAttribute("aria-label"), /room panel/i);
    assert.match(inventoryToggle.getAttribute("aria-label"), /cabinet inventory panel/i);
    h.destroy();
});
