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
 *   1. the catalog row's Qty column (originally swapped for a per-row
 *      "Availability" / "Made to order" cell here; Panel P2, the
 *      catalog redesign, later found that cell carried the IDENTICAL
 *      value on all ~48 rows — zero discriminating information — and
 *      removed the per-row cell entirely in favour of a single static
 *      note under the panel header, .o_sbk_inv_madetoorder_note; P3
 *      removals (2026-08-02) then cut that panel-level note too — see
 *      the re-targeted first test below, now asserting its absence),
 *      and
 *   2. the selected-cabinet detail drawer's "Available ... pcs" row
 *      (now "Availability" / "Made to order", UNCHANGED by Panel P2 or
 *      P3 — that drawer is a separate section neither pass touched),
 *      reached via the F15 "+ Add" button (see contracts/13_add_button_
 *      and_corner_category.test.mjs for the button contract itself).
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

test("Panel P2/P3: inventory panel carries no 'Qty' column and no made-to-order note", async () => {
    const h = await mountWithProducts([ZERO_QTY_PRODUCT]);
    await tick();

    // Panel P2 (catalog redesign) removed the old 5-column
    // .o_sbk_inv_header row entirely — the grouped/card list replaced
    // the flat table, so there's no longer a column-header element to
    // carry a "Qty"/"Availability" label at all. Assert its complete
    // absence rather than its text.
    assert.equal(h.root.querySelector(".o_sbk_inv_header"), null,
        "the old column-header row is gone");
    const bareQty = [...h.root.querySelectorAll(".o_sbk_inventory *")]
        .some((el) => el.children.length === 0 && el.textContent.trim() === "Qty");
    assert.equal(bareQty, false, "no element anywhere in the panel reads the bare old 'Qty' label");

    // P3 removals (2026-08-02, client-approved ticket) — the panel-
    // level made-to-order note is gone too now (every row carried the
    // identical value; Panel P2 already noted zero discriminating
    // information even at the single-note stage). Re-targeted from
    // asserting the note's presence to asserting its absence.
    assert.equal(h.root.querySelector(".o_sbk_inv_madetoorder_note"), null,
        "the panel-level made-to-order note no longer renders");
    const bareNote = [...h.root.querySelectorAll(".o_sbk_inventory *")]
        .some((el) => el.children.length === 0
            && el.textContent.trim() === "All Southbrook cabinets are made to order.");
    assert.equal(bareNote, false, "the made-to-order sentence renders nowhere in the panel");
});

test("Panel P2: catalog row carries no per-row availability cell — no numeric quantity, no red '0'", async () => {
    const h = await mountWithProducts([ZERO_QTY_PRODUCT]);
    await tick();

    const row = h.root.querySelector(".o_sbk_product_row");
    assert.ok(row, "product row rendered");

    // Panel P2 (catalog redesign) — the per-row "Made to order" cell
    // (.o_sbk_prod_qty / .o_sbk_prod_madetoorder) is gone entirely: all
    // ~48 rows carried the identical value, so it was zero
    // discriminating information (see the panel-level note assertion
    // above). Assert absence directly rather than the now-nonexistent
    // element's text.
    assert.equal(row.querySelector(".o_sbk_prod_qty"), null,
        "the old per-row qty-column element no longer renders");
    assert.equal(row.querySelector(".o_sbk_prod_madetoorder"), null,
        "the old per-row made-to-order class no longer renders");
    const bareZero = [...row.querySelectorAll("*")]
        .some((el) => el.children.length === 0 && el.textContent.trim() === "0");
    assert.equal(bareZero, false,
        "no leaf element renders a bare numeric '0' (the old warning-red quantity) anywhere on the row");
    assert.equal(row.querySelector(".ok"), null, "must never carry the old stock-level 'ok' class");
    assert.equal(row.querySelector(".low"), null,
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
