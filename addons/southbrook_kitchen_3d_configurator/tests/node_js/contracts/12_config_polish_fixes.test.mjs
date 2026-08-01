/**
 * F12 / F13 / F14 contracts — three related "the control/UI lies about
 * what it did" defects fixed together in the same pass as F7/F8.
 *
 * F12 — Wall cab top alignment (D8) and filler-strategy controls are
 * dead once a design is hydrated from a saved record
 * (`_refreshLayout()` early-returns to a pure local recompute, never
 * regenerating), but before this fix they still queued the debounced
 * auto-save and lit the "Auto-saved" pill — the control lied about
 * having applied the edit. Fixed by: (a) the handlers early-return
 * before any state write / auto-save once `_hydratedFromDesign`, and
 * (b) the controls render `disabled` in the template.
 *
 * F13 — `_recomputeLayoutFromItems` used to spread the PREVIOUS
 * `state.summary` forward and only overwrite base_count/wall_count/
 * total/price; every other key (cabinet_price, filler_price,
 * remainder_in, snap_hint) survived stale across an item edit that made
 * them wrong (e.g. deleting the last filler still showed its price).
 * Fixed by recomputing every derivable key fresh from state.items each
 * call and dropping (never spreading forward) whatever isn't
 * recomputable client-side (snap_hint).
 *   Task 10 (SBK-023) amendment — F13 made remainder_in FRESH but not
 *   HONEST: it defined remainder_in as the sum of currently-surviving
 *   filler items' widths, which understated the true gap the moment a
 *   filler line was deleted without repacking the survivors. Task 10
 *   redefines remainder_in as the geometric identity (run length minus
 *   occupied cabinet-run length), independent of which/how-many filler
 *   items happen to still exist. See the second F13 test below.
 *

 * F14 — clicking an inventory/catalog row used to write the raw catalog
 * product straight into `state.selected` (no layout_key), so the
 * "Selected Cabinet" edit drawer's actions (remove/width/swap) would
 * silently no-op via `findIndex(...) === -1`. Fixed by splitting catalog
 * BROWSING (`state.catalogPreview`, written by `_previewCatalogProduct`)
 * from layout SELECTION (`state.selected`, written by `_selectCabinet` /
 * the canvas callbacks) and hard-guarding the drawer's action methods.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

function fieldControl(root, labelText) {
    const labels = [...root.querySelectorAll("label.o_sbk_field")];
    const label = labels.find((l) => l.textContent.includes(labelText));
    return label ? label.querySelector("select, input") : null;
}

const PRODUCT = {
    product_id: 42, name: "Base 24", cabinet_type: "base", sku: "B24",
    width_in: 24, height_in: 34.5, depth_in: 24, price: 500,
    available_qty: 10, material: "white_melamine",
};

// ─── F12 ─────────────────────────────────────────────────────────────────────

async function mountHydratedDesign() {
    return mountConfigurator({
        actionParams: { design_id: 900 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "F12 test design",
                lines: [{
                    layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                    width_in: 24, height_in: 34.5, depth_in: 24,
                    x_position_in: 0, y_position_in: 0, z_position_in: 0,
                    rotation_deg: 0, wall: "back", price: 500,
                }],
            }),
        },
    });
}

test("F12: a hydrated design marks state.hydratedFromDesign true", async () => {
    const h = await mountHydratedDesign();
    assert.equal(h.component.state.hydratedFromDesign, true);
    assert.equal(h.component._hydratedFromDesign, true);
});





// ─── F13 ─────────────────────────────────────────────────────────────────────

async function mountFreshWithFiller() {
    return mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/layout": () => ({
                items: [
                    {
                        layout_key: "base-1", cabinet_type: "base", name: "Base A",
                        product_name: "Base A", price: 500, width_in: 24,
                        x_position_in: 0, y_position_in: 0, z_position_in: 0,
                    },
                    {
                        layout_key: "filler-1", cabinet_type: "filler", name: "Filler",
                        product_name: "Filler", price: 40, width_in: 3,
                        x_position_in: 24, y_position_in: 0, z_position_in: 0,
                    },
                ],
                summary: {
                    base_count: 1, wall_count: 0, total: 1, price: 500,
                    cabinet_price: 500, filler_price: 40, remainder_in: 3,
                    filler_strategy: "split",
                    snap_hint: { target_width: 96, direction: "expand" },
                },
            }),
        },
    });
}

test("F13: fresh /layout summary carries filler_price/remainder_in/snap_hint as the server sent them", async () => {
    const h = await mountFreshWithFiller();
    assert.equal(h.component.state.summary.filler_price, 40);
    assert.equal(h.component.state.summary.remainder_in, 3);
    assert.ok(h.component.state.summary.snap_hint);
});

test("F13: removing the filler drops stale filler_price/snap_hint instead of carrying them forward", async () => {
    const h = await mountFreshWithFiller();
    const filler = h.component.state.items.find((it) => it.cabinet_type === "filler");
    assert.ok(filler, "fixture filler item present before removal");

    h.component.state.selected = filler;
    h.component._removeSelectedCabinet();
    clearTimeout(h.component._autoSaveTimer);   // no designId yet -> harmless debounced auto-save; avoid a stray late RPC

    const s = h.component.state.summary;
    assert.equal(s.filler_price, 0,
        "filler_price must be recomputed from current items (0 fillers left), not spread forward stale");
    // Task 10 (SBK-023) — this used to assert remainder_in === 0 here,
    // matching the OLD (buggy, "understated") definition of remainder_in
    // as "sum of currently surviving filler widths". That was the
    // confirmed SBK-023 finding #7 understated-direction bug: deleting
    // one filler line doesn't shrink the room's real physical gap, so
    // reporting 0 lied. remainder_in is now the honest geometric
    // identity (room width 96 minus the 24in occupied by the surviving
    // base cabinet = 72), unchanged by a filler item's presence/absence.
    assert.equal(s.remainder_in, 72,
        "remainder_in is the geometric gap (room width - occupied cabinet "
        + "run), not a sum of surviving filler widths — deleting the "
        + "filler must not shrink the true remaining gap");
    assert.equal(s.cabinet_price, 500, "cabinet_price still reflects the remaining base cabinet");
    assert.equal(s.price, 500);
    assert.ok(!("snap_hint" in s),
        "snap_hint is not recomputable client-side and must be dropped, not spread forward stale");
});

// ─── F14 ─────────────────────────────────────────────────────────────────────

async function freshConfiguratorWithProduct() {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products: [PRODUCT],
            }),
        },
    });
    h.component.state.products = [PRODUCT];
    return h;
}

test("F14: _previewCatalogProduct writes catalogPreview, never state.selected", async () => {
    const h = await freshConfiguratorWithProduct();
    assert.equal(h.component.state.selected, null);

    h.component._previewCatalogProduct(PRODUCT);

    // Compared by product_id, not object identity: OWL's useState() wraps
    // assigned objects in a reactive Proxy, so a value read back off
    // `state.x` is never `===` its raw pre-assignment source object even
    // though it's the same logical data (see the reference-equal
    // identity checks in 10_scene_selection_identity.test.mjs, which
    // deliberately bypass useState/OWL state entirely for that reason).
    assert.equal(h.component.state.catalogPreview.product_id, PRODUCT.product_id);
    assert.equal(h.component.state.selected, null,
        "a catalog row preview must never populate state.selected (the edit-drawer binding)");
});

test("F14: the edit drawer never renders for a catalog-preview-only product", async () => {
    const h = await freshConfiguratorWithProduct();
    h.component._previewCatalogProduct(PRODUCT);
    await tick();

    const drawer = h.root.querySelector(".o_sbk_detail");
    assert.equal(drawer, null, "no edit drawer should render without a real layout_key selection");
});

test("F14: drawer actions are guarded no-ops if state.selected is ever a layout_key-less object", async () => {
    const h = await freshConfiguratorWithProduct();
    // Defense-in-depth scenario: simulate the old bug directly (a raw
    // catalog product landing in state.selected) and confirm every
    // action method is now a hard no-op instead of a silent findIndex(-1).
    h.component.state.items = [{
        layout_key: "base-1", cabinet_type: "base", product_name: "Base A",
        width_in: 24, height_in: 34.5, depth_in: 24, price: 500,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
    }];
    h.component.state.selected = PRODUCT;   // no layout_key
    const itemsBefore = h.component.state.items.length;

    h.component._removeSelectedCabinet();
    assert.equal(h.component.state.items.length, itemsBefore, "_removeSelectedCabinet must no-op");

    h.component._updateSelectedWidth("30");
    assert.equal(h.component.state.items[0].width_in, 24, "_updateSelectedWidth must no-op");

    h.component._swapSelectedProduct(String(PRODUCT.product_id));
    assert.equal(h.component.state.items[0].product_name, "Base A", "_swapSelectedProduct must no-op");
});

test("F14: selecting a real layout item clears any stale catalogPreview", async () => {
    const h = await freshConfiguratorWithProduct();
    h.component._previewCatalogProduct(PRODUCT);
    assert.ok(h.component.state.catalogPreview);

    const item = {
        layout_key: "base-1", cabinet_type: "base", product_name: "Base A",
        width_in: 24, height_in: 34.5, depth_in: 24, price: 500,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
    };
    h.component._selectCabinet(item);

    // Compared by layout_key, not object identity — see the identical
    // note in the _previewCatalogProduct test above.
    assert.equal(h.component.state.selected.layout_key, item.layout_key);
    assert.equal(h.component.state.catalogPreview, null,
        "a real selection supersedes any inventory-row preview highlight");
});
