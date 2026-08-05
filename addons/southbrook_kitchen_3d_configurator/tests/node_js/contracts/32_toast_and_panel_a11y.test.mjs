/**
 * Task 34 (SBK-020, corner-remediation-2026-07-30) — canvas a11y
 * (scoped).
 *
 * Covers, in order:
 *   1. Toasts — the container stays role="status"/aria-live="polite"
 *      (unchanged); a STICKY toast (by construction, one that demands
 *      a decision before it goes away — a refusal + "Add anyway", a
 *      save failure that disables further saves) additionally gets
 *      role="alert" on the individual toast so assistive tech
 *      announces it immediately. A non-sticky toast carries no role
 *      attribute at all (the container's aria-live already covers it;
 *      double-announcing would be noise).
 *   2. Canvas naming — <KitchenCanvas>'s accessible name identifies
 *      the OPEN DESIGN (falls back to a generic label pre-hydration),
 *      and the ←/→-cycling + other shortcuts moved out of that name
 *      into a separate aria-describedby hint (WAI-ARIA: name = "what
 *      is this", description = "how do I use it").
 *   3. Opened-panel focus management — the "Selected Cabinet" drawer
 *      and the catalog preview block both move focus in when a real
 *      user gesture opens them, restore focus to the opener when they
 *      close (Escape or an explicit close control), and do NOT steal
 *      focus for the two non-gesture paths that also write
 *      `state.selected`/`state.catalogPreview`: Task 22's ←/→ cabinet
 *      cycling (which is designed to keep focus on the canvas) and
 *      auto-select-on-load (hydration / a fresh /layout generate).
 *   4. The customer-picker dropdown closes on Escape without moving
 *      focus away from the input that's already its own opener.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "../harness/setup_dom.mjs";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CANVAS_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "js", "canvas", "kitchen_canvas.esm.js",
);

function findButtonByText(root, text) {
    return [...root.querySelectorAll("button")].find(
        (b) => b.textContent.trim() === text);
}

// ─── 1. Toast role="alert" for sticky (blocking) toasts only ──────────────

test("a sticky (blocking/decision-required) toast gets role=\"alert\"; a non-sticky toast carries no role attribute", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    c._toast("Design saved", { type: "success" });
    c._toast("Saving is disabled — reload to try again.", { type: "danger", sticky: true });
    await tick();

    const toastEls = [...h.root.querySelectorAll(".o_sbk_toast")];
    const transient = toastEls.find((el) => el.textContent.includes("Design saved"));
    const blocking = toastEls.find((el) => el.textContent.includes("Saving is disabled"));
    assert.ok(transient, "non-sticky toast rendered");
    assert.ok(blocking, "sticky toast rendered");
    assert.equal(transient.getAttribute("role"), null,
        "non-sticky toast must not duplicate role=\"alert\" — the container's aria-live already announces it");
    assert.equal(blocking.getAttribute("role"), "alert");

    const stack = h.root.querySelector(".o_sbk_toast_stack");
    assert.equal(stack.getAttribute("role"), "status");
    assert.equal(stack.getAttribute("aria-live"), "polite");
    h.destroy();
});

test("the refuse-by-default over-length-add toast (a real sticky decision prompt) renders role=\"alert\"", async () => {
    const B24 = {
        product_id: 24, name: "B24 Base Cabinet", cabinet_type: "base", sku: "B24",
        width_in: 24, height_in: 34.5, depth_in: 24, price: 180,
        available_qty: 10, material: "white_melamine",
    };
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({ channel: null, products: [B24] }),
        },
    });
    const c = h.component;
    for (let i = 0; i < 5; i++) {   // 5th overflows a 96in room
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    await tick();
    const refusalEl = [...h.root.querySelectorAll(".o_sbk_toast")]
        .find((el) => /won't fit/i.test(el.textContent));
    assert.ok(refusalEl, "refusal toast rendered");
    assert.equal(refusalEl.getAttribute("role"), "alert");
    h.destroy();
});

// ─── 2. Canvas naming + description (real kitchen_canvas.esm.js) ──────────

async function loadRealCanvas() {
    await setupDom();
    const mod = await import(CANVAS_SRC);
    return mod.KitchenCanvas;
}

test("KitchenCanvas._ariaLabel names the canvas from the open design when one is set", async () => {
    const KitchenCanvas = await loadRealCanvas();
    assert.equal(
        KitchenCanvas.prototype._ariaLabel.call({ props: { designName: "Richwood Kitchen" } }),
        "3D kitchen scene — Richwood Kitchen");
});

test("KitchenCanvas._ariaLabel falls back to a generic, still non-empty name pre-hydration/landing", async () => {
    const KitchenCanvas = await loadRealCanvas();
    assert.equal(KitchenCanvas.prototype._ariaLabel.call({ props: { designName: "" } }), "3D kitchen scene");
    assert.equal(KitchenCanvas.prototype._ariaLabel.call({ props: {} }), "3D kitchen scene");
    // Whitespace-only is treated the same as empty (trimmed).
    assert.equal(KitchenCanvas.prototype._ariaLabel.call({ props: { designName: "   " } }), "3D kitchen scene");
});

test("template: the canvas keeps tabindex, is named via _ariaLabel(), and points aria-describedby at a keyboard-shortcut hint that still covers ←/→ cycling", () => {
    const src = readFileSync(CANVAS_SRC, "utf8");
    const openAt = src.indexOf('t-ref="kitchenCanvas"');
    assert.notEqual(openAt, -1, "sanity: the canvas mount div must exist");
    const divStart = src.lastIndexOf("<div", openAt);
    const divEnd = src.indexOf("</div>", openAt) + "</div>".length;
    const el = src.slice(divStart, divEnd);
    assert.match(el, /tabindex="0"/, "canvas must stay a focusable surface (Task 22)");
    assert.match(el, /t-att-aria-label="_ariaLabel\(\)"/, "accessible name must be computed, not a static shortcut-prose string");
    assert.match(el, /aria-describedby="sbkCanvasHint"/);
    assert.match(el, /id="sbkCanvasHint"/, "the hint node must live INSIDE the canvas element it describes");
    assert.match(el, /class="visually-hidden"/, "reuse the platform Bootstrap 5 utility — no bespoke sr-only CSS needed");
    assert.match(el, /left and right arrow keys[\s\S]*cycle/i, "the ←/→ cycling shortcut (Task 22) must still be documented somewhere for assistive tech");
});

// ─── 3. Opened-panel focus management ──────────────────────────────────────

const SELECTABLE_ITEM = {
    id: 1, name: "Base 24", layout_key: "k1", cabinet_type: "base",
    width_in: 24, height_in: 34.5, depth_in: 24, price: 180, wall: "back", x_position_in: 0,
};

test("a real canvas click (onSelectItem) moves focus into the opened detail drawer, and Escape closes it and restores focus to the opener", async () => {
    const h = await mountConfigurator({});
    await tick();
    const openerBtn = findButtonByText(h.root, "Kitchen Designs");
    assert.ok(openerBtn, "sanity: the topbar's Kitchen Designs button exists");
    openerBtn.focus();
    assert.equal(h.dom.window.document.activeElement, openerBtn);

    h.canvasProps().onSelectItem(SELECTABLE_ITEM);
    await tick();
    const panel = h.root.querySelector(".o_sbk_detail");
    assert.ok(panel, "the drawer renders once a real layout item (with layout_key) is selected");
    assert.equal(panel.getAttribute("tabindex"), "-1");
    assert.equal(h.dom.window.document.activeElement, panel,
        "focus must move into the opened drawer (WAI-ARIA APG: opening a panel moves focus in)");

    const ev = new h.dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    panel.dispatchEvent(ev);
    await tick();
    assert.equal(h.component.state.selected, null);
    assert.equal(h.dom.window.document.activeElement, openerBtn,
        "Escape must close the drawer AND return focus to whatever opened it");
    h.destroy();
});

test("clicking the drawer's own Remove control (a close path other than Escape) also restores focus to the opener, once no neighbour cabinet remains", async () => {
    const h = await mountConfigurator({});
    await tick();
    h.component.state.items = [{ ...SELECTABLE_ITEM }];
    const openerBtn = findButtonByText(h.root, "Kitchen Designs");
    openerBtn.focus();

    h.canvasProps().onSelectItem(h.component.state.items[0]);
    await tick();
    assert.equal(h.dom.window.document.activeElement, h.root.querySelector(".o_sbk_detail"));

    // SBK-058 — removal moved off the header "✕" (which now closes the
    // panel) onto an explicit labelled button in the card body.
    const removeBtn = h.root.querySelector(".o_sbk_detail_remove_btn");
    assert.ok(removeBtn);
    removeBtn.click();
    await tick();
    clearTimeout(h.component._autoSaveTimer);   // debounced by _queueAutoSave; not this test's concern
    assert.equal(h.component.state.selected, null, "no neighbour left -> drawer fully closes");
    assert.equal(h.dom.window.document.activeElement, openerBtn);
    h.destroy();
});

test("Task 22's ←/→ cabinet cycling never steals focus into the detail drawer, even on the very first (nothing-previously-selected) keypress", async () => {
    const h = await mountConfigurator({});
    await tick();
    h.component.state.items = [
        { ...SELECTABLE_ITEM, layout_key: "k1", x_position_in: 0 },
        { ...SELECTABLE_ITEM, layout_key: "k2", x_position_in: 24 },
    ];
    h.component.state.selected = null;
    await tick();

    const openerBtn = findButtonByText(h.root, "Kitchen Designs");
    openerBtn.focus();
    h.component._cycleSelection(1);
    await tick();

    assert.ok(h.component.state.selected, "a cabinet IS now selected by the cycle");
    assert.equal(h.dom.window.document.activeElement, openerBtn,
        "keyboard cycling (Task 22) must keep focus on whatever had it — the canvas flow, never the drawer");
    h.destroy();
});

test("auto-select-on-hydration (no user gesture aimed at the drawer) never steals focus either", async () => {
    const LINES = [{
        id: 601, layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    }];
    const h = await mountConfigurator({
        actionParams: { design_id: 601 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "Design 601", lines: LINES,
            }),
        },
    });
    await tick();
    assert.ok(h.component.state.selected, "hydration auto-selects the first real cabinet (pre-existing behavior)");
    assert.notEqual(h.dom.window.document.activeElement, h.root.querySelector(".o_sbk_detail"),
        "but must never yank focus into the drawer on a silent, non-gesture boot selection");
    h.destroy();
});

test("previewing a catalog product (a row click) moves focus into the preview panel, and its × close button returns focus to the row", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null,
                products: [{
                    product_id: 5, name: "Base 24", cabinet_type: "base", sku: "B24",
                    width_in: 24, height_in: 34.5, depth_in: 24, price: 180, material: "white_melamine",
                }],
            }),
        },
    });
    await tick();
    const row = h.root.querySelector(".o_sbk_product_row");
    assert.ok(row, "sanity: an inventory row is rendered");
    row.focus();
    row.click();
    await tick();

    const preview = h.root.querySelector(".o_sbk_catalog_preview");
    assert.ok(preview, "preview panel renders on row click");
    assert.equal(preview.getAttribute("tabindex"), "-1");
    assert.equal(h.dom.window.document.activeElement, preview);

    const closeBtn = preview.querySelector(".o_sbk_catalog_preview_dismiss");
    assert.ok(closeBtn);
    closeBtn.click();
    await tick();
    assert.equal(h.component.state.catalogPreview, null);
    assert.equal(h.dom.window.document.activeElement, row, "focus returns to the row that opened the preview");
    h.destroy();
});

test("Escape closes the catalog preview before it touches the detail-drawer selection (last opened, first closed)", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null,
                products: [{
                    product_id: 5, name: "Base 24", cabinet_type: "base", sku: "B24",
                    width_in: 24, height_in: 34.5, depth_in: 24, price: 180, material: "white_melamine",
                }],
            }),
        },
    });
    await tick();
    h.component.state.items = [{ ...SELECTABLE_ITEM }];
    h.canvasProps().onSelectItem(h.component.state.items[0]);
    const row = h.root.querySelector(".o_sbk_product_row");
    row.click();   // preview, does NOT touch state.selected (F14)
    await tick();
    assert.ok(h.component.state.selected, "the drawer selection survives a preview click");
    assert.ok(h.component.state.catalogPreview, "the preview is also open");

    h.component._onKeyDown({ key: "Escape", target: { tagName: "DIV" }, preventDefault() {} });
    await tick();
    assert.equal(h.component.state.catalogPreview, null, "Escape closes the preview first");
    assert.ok(h.component.state.selected, "...and leaves the drawer selection untouched on this first Escape");

    h.component._onKeyDown({ key: "Escape", target: { tagName: "DIV" }, preventDefault() {} });
    await tick();
    assert.equal(h.component.state.selected, null, "a second Escape then closes the drawer");
    h.destroy();
});

// ─── 4. Customer-picker Escape ──────────────────────────────────────────────

test("_onCustomerInputKeydown: Escape closes the picker dropdown without touching anything else", async () => {
    const h = await mountConfigurator({});
    const c = h.component;

    c.state.pickerOpen = true;
    let pd = false, sp = false;
    c._onCustomerInputKeydown({ key: "Escape", preventDefault: () => { pd = true; }, stopPropagation: () => { sp = true; } });
    assert.equal(c.state.pickerOpen, false);
    assert.equal(pd, true);
    assert.equal(sp, true);
    h.destroy();
});

test("_onCustomerInputKeydown: a non-Escape key is a no-op", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    c.state.pickerOpen = true;
    c._onCustomerInputKeydown({ key: "a", preventDefault() { throw new Error("must not fire"); }, stopPropagation() {} });
    assert.equal(c.state.pickerOpen, true);
    h.destroy();
});

test("_onCustomerInputKeydown: Escape when the picker is already closed is a harmless no-op (never calls preventDefault)", async () => {
    const h = await mountConfigurator({});
    const c = h.component;
    c.state.pickerOpen = false;
    let called = false;
    c._onCustomerInputKeydown({ key: "Escape", preventDefault: () => { called = true; }, stopPropagation() {} });
    assert.equal(called, false);
    h.destroy();
});

test("SBK-058: the panel-header close control deselects and must NOT delete the cabinet", async () => {
    // Regression guard. That "✕" used to call _removeSelectedCabinet — a
    // control shaped, placed and read as "close this panel" silently
    // destroyed a cabinet on an auto-saving document. Its only warning was
    // an aria-label.
    const h = await mountConfigurator({});
    await tick();
    h.component.state.items = [{ ...SELECTABLE_ITEM }];

    h.canvasProps().onSelectItem(h.component.state.items[0]);
    await tick();

    const closeBtn = h.root.querySelector(".o_sbk_detail_close");
    assert.ok(closeBtn, "the header must carry a close control");
    assert.equal(
        closeBtn.getAttribute("aria-label"), "Close this panel",
        "the header control must describe itself as closing, not removing");

    closeBtn.click();
    await tick();

    assert.equal(h.component.state.selected, null, "closing deselects");
    assert.equal(
        h.component.state.items.length, 1,
        "closing the panel must leave the cabinet in the layout");
    h.destroy();
});
