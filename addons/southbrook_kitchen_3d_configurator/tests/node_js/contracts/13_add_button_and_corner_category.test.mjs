/**
 * F15 / F1 contracts — two small, template-only inventory-panel fixes,
 * verified end-to-end through the REAL rendered OWL template (only
 * <KitchenCanvas> is mocked, same as every other component-level
 * contract in this suite).
 *
 * F15 — adding a cabinet was drag-and-drop only
 * (`_addCabinetFromProduct` even documented a "future click to add
 * button" that was never built) — an accessibility + testability gap.
 * Each catalog row now renders a real, keyboard-reachable "+ Add"
 * <button> that calls `_addCabinetFromProduct(product, null, null)`
 * directly.
 *   Panel P2 (catalog redesign) amendment — F15 originally built Add
 *   as a SIBLING of a literal row <button> ("buttons cannot legally
 *   nest"). P2's row now also hosts an L/R handing toggle for merged
 *   pair rows, so the row itself became a role="button" + tabindex="0"
 *   container (kitchen_configurator.js's _onRowKeydown gives it the
 *   same Enter/Space activation a native <button> gets for free) that
 *   Add and the toggle nest INSIDE, each with a stopped click so
 *   activating them never also fires the row's own preview click. The
 *   structural "not nested" assertion below was re-targeted to match;
 *   the behavioural guarantee it protected (Add is independently
 *   operable and never also triggers row preview) is unchanged and
 *   still covered by the second test below.
 *
 * F1 — `_inventoryCategoryTypes` used to map `tall -> ["tall",
 * "corner"]`, so a corner unit had no way to surface under its own
 * filter; it only ever showed up (mislabeled, uncounted as its own
 * category) buried inside the Tall tab. Corner now gets its own
 * key/tab.
 *   Panel P2 amendment — `_inventoryCategoryTypes` / the "Filter by
 *   type" <select> it drove are both gone; category membership now
 *   comes straight from groupCatalog()'s own fixed category order
 *   (catalog_list.esm.js), rendered as always-visible chips. The third
 *   test's DOM query was re-targeted from `<select><option>` to the
 *   chip buttons; `_categoryCount("tall"/"corner")` / `_setInventory
 *   Category` / `_filteredProducts` keep their exact old signatures
 *   and behaviour, so those assertions are unchanged.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const BASE_PRODUCT = {
    product_id: 42, name: "Base 24", cabinet_type: "base", sku: "B24",
    width_in: 24, height_in: 34.5, depth_in: 24, price: 500,
    available_qty: 10, material: "white_melamine",
};
const CORNER_PRODUCT = {
    product_id: 43, name: "Corner Base 36", cabinet_type: "corner", sku: "CB36",
    width_in: 36, height_in: 34.5, depth_in: 24, price: 650,
    available_qty: 4, material: "white_melamine",
};

async function mountWithProducts(products) {
    return mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products,
            }),
        },
    });
}

test("F15/P2: row exposes button semantics (role+tabindex) and + Add is a real, independently keyboard-reachable <button>", async () => {
    const h = await mountWithProducts([BASE_PRODUCT]);
    await tick();

    const row = h.root.querySelector(".o_sbk_product_row");
    assert.ok(row, "product row rendered");
    // Panel P2 (catalog redesign) — see the file header amendment: the
    // row can no longer be a literal <button> (it now nests the "+ Add"
    // button, and for pair rows the L/R toggle too), so it's a
    // role="button" + tabindex="0" container instead, with its own
    // keydown handler standing in for the native Enter/Space activation
    // a real <button> gets for free.
    assert.equal(row.getAttribute("role"), "button",
        "row exposes button semantics via role for assistive tech");
    assert.equal(row.tabIndex, 0, "row stays natively keyboard-reachable (Tab order)");

    const addBtn = h.root.querySelector(".o_sbk_prod_add_btn");
    assert.ok(addBtn, "+ Add button rendered");
    assert.equal(addBtn.tagName, "BUTTON", "+ Add must be a real <button> element (keyboard-reachable)");
    assert.notEqual(addBtn, row, "+ Add is a distinct control from the row");
});

test("F15: clicking + Add calls _addCabinetFromProduct(product, null, null) and does not also fire the row preview", async () => {
    const h = await mountWithProducts([BASE_PRODUCT]);
    await tick();

    assert.equal(h.component.state.items.length, 0);
    assert.equal(h.component.state.catalogPreview, null);

    const addBtn = h.root.querySelector(".o_sbk_prod_add_btn");
    addBtn.dispatchEvent(new h.dom.window.Event("click", { bubbles: true }));
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.items.length, 1, "+ Add must append a cabinet to the layout");
    assert.equal(h.component.state.items[0].product_id, BASE_PRODUCT.product_id);
    assert.equal(h.component.state.catalogPreview, null,
        "+ Add is a distinct action from row-click preview and must not set catalogPreview either");
});

test("F1: a corner product gets its own 'Corner' filter tab (not folded into Tall)", async () => {
    const h = await mountWithProducts([BASE_PRODUCT, CORNER_PRODUCT]);
    await tick();

    assert.equal(h.component._categoryCount("tall"), 0, "corner must not be counted under Tall");
    assert.equal(h.component._categoryCount("corner"), 1, "corner has its own count");

    // Panel P2 (catalog redesign) — the "Filter by type" <select> is
    // gone; category membership is now always-visible chips rendered
    // straight off groupCatalog()'s own categories (catalog_list.esm.js),
    // clicked via _setInventoryCategory exactly like the old <option>s
    // were selected via that same method.
    const chipLabels = [...h.root.querySelectorAll(".o_sbk_inv_chiprow .o_sbk_chip")]
        .map((btn) => btn.textContent.trim());
    assert.ok(chipLabels.some((label) => label.startsWith("Corner Cabinets")),
        "a Corner Cabinets chip renders in the category filter row");

    h.component._setInventoryCategory("corner");
    await tick();
    const filtered = h.component._filteredProducts();
    assert.equal(filtered.length, 1);
    assert.equal(filtered[0].product_id, CORNER_PRODUCT.product_id);
});

test("F1: filtering by 'tall' no longer includes corner products (non-regression check on the old folded mapping)", async () => {
    const h = await mountWithProducts([BASE_PRODUCT, CORNER_PRODUCT]);
    h.component._setInventoryCategory("tall");
    await tick();
    const filtered = h.component._filteredProducts();
    assert.equal(filtered.length, 0, "no tall products in this fixture, and corner must not leak in");
});
