/**
 * Task 23 (SBK-037) — catalogue hygiene: the inventory panel used to
 * render `product.available_qty` as a literal on-hand count, coloured
 * red ("low") whenever it was 0 or under 50 — which was nearly always,
 * since Southbrook cabinets are built to order and never held as
 * stock (the /southbrook_kitchen/configurator/products route already
 * filters to southbrook_is_cabinet=True products only, so every row
 * in this panel is a cabinet). That's a false stock-shortage signal,
 * not real inventory data.
 *
 * Both places the old signal rendered are exercised here:
 *   1. the catalog row's Qty column (now "Availability" / "Made to
 *      order"), and
 *   2. the selected-cabinet detail drawer's "Available ... pcs" row
 *      (now "Availability" / "Made to order"), reached via the F15
 *      "+ Add" button (see contracts/13_add_button_and_corner_
 *      category.test.mjs for the button contract itself).
 *
 * available_qty: 0 is used deliberately in both fixtures — the exact
 * value that used to render in warning red — to prove no numeric
 * stock count and no ok/low colouring survive anywhere in this panel.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const ZERO_QTY_PRODUCT = {
    product_id: 91, name: "Base 24", cabinet_type: "base", sku: "SB-BASE-1DR-24",
    width_in: 24, height_in: 34.5, depth_in: 24, price: 500,
    available_qty: 0, material: "white_melamine",
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

test("inventory header no longer reads 'Qty' — reads 'Availability'", async () => {
    const h = await mountWithProducts([ZERO_QTY_PRODUCT]);
    await tick();

    const headerSpans = [...h.root.querySelectorAll(".o_sbk_inv_header span")]
        .map((s) => s.textContent.trim());
    assert.ok(!headerSpans.includes("Qty"), "the old 'Qty' header label must be gone");
    assert.ok(headerSpans.includes("Availability"), "header now reads 'Availability'");
});

test("catalog row: a zero-available cabinet shows 'Made to order', not a red '0'", async () => {
    const h = await mountWithProducts([ZERO_QTY_PRODUCT]);
    await tick();

    const row = h.root.querySelector(".o_sbk_product_row");
    assert.ok(row, "product row rendered");

    const qtyCell = row.querySelector(".o_sbk_prod_qty");
    assert.ok(qtyCell, "the qty-column element still renders (same column slot)");
    assert.equal(qtyCell.textContent.trim(), "Made to order",
        "no numeric quantity (e.g. '0') may render in this column");
    assert.ok(qtyCell.classList.contains("o_sbk_prod_madetoorder"),
        "carries the new neutral made-to-order class");
    assert.equal(qtyCell.classList.contains("ok"), false,
        "must never carry the old stock-level 'ok' class");
    assert.equal(qtyCell.classList.contains("low"), false,
        "must never carry the old stock-level 'low' (warning-red) class");
});

test("detail drawer: 'Availability' reads 'Made to order' for the selected cabinet, no '0 pcs'", async () => {
    const h = await mountWithProducts([ZERO_QTY_PRODUCT]);
    await tick();

    const addBtn = h.root.querySelector(".o_sbk_prod_add_btn");
    assert.ok(addBtn, "+ Add button rendered (see contracts/13_add_button...)");
    addBtn.dispatchEvent(new h.dom.window.Event("click", { bubbles: true }));
    await tick();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.selected && h.component.state.selected.layout_key
        ? h.component.state.selected.available_qty : undefined, 0,
        "sanity: the placed item really does carry available_qty === 0");

    const dts = [...h.root.querySelectorAll(".o_sbk_spec_grid dt")]
        .map((dt) => dt.textContent.trim());
    assert.ok(!dts.includes("Available"), "the old 'Available' label must be gone");
    const availIdx = dts.indexOf("Availability");
    assert.notEqual(availIdx, -1, "detail drawer now has an 'Availability' row");

    const dds = h.root.querySelectorAll(".o_sbk_spec_grid dd");
    const dd = dds[availIdx];
    assert.equal(dd.textContent.trim(), "Made to order",
        "no '0 pcs' / numeric stock count may render here");
    assert.equal(dd.classList.contains("ok"), false, "must not carry the old 'ok' class");
    assert.equal(dd.classList.contains("low"), false,
        "must not carry the old 'low' (warning-red) class");
});
