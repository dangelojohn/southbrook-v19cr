/**
 * Reproduces the production crash reported on southbrookcabinetry.space
 * 2026-08-02: "OwlError: Invalid loop expression: 'undefined' is not
 * iterable" inside MaterialsHardwareCatalog.template, at the
 * `state.detail.specs` / `.engineering` / `.badges` t-foreach loops
 * (catalog.xml lines 136/147/156).
 *
 * Root cause: `_build_payload` (models/tools_catalog_provider.py) always
 * returns a `"detail": {}` placeholder key — real detail data comes
 * exclusively from the separate get_detail RPC (selectRow()). load()'s
 * `Object.assign(this.state, ..., payload, ...)` merged that placeholder
 * straight into state.detail. Whenever a reload's still-selected row
 * survives the new result set (the `stillPresent` fast path — "keep the
 * panel showing"), load() returns immediately without ever repairing
 * state.detail, permanently leaving it `{}` — an object with no specs/
 * engineering/badges keys, which the template cannot iterate.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountCatalog } from "../harness/mount_catalog.mjs";

const COLUMNS = [
    { key: "name", label: "Description", align: "left", sortable: true },
    { key: "default_code", label: "Reference", align: "left", sortable: true },
];

const ROW = { product_id: 1, name: "Widget", default_code: "SBK-W-1" };

function catalogPayload() {
    return {
        ok: true, reason: null, scope: "tools",
        categories: [], facets: [], columns: COLUMNS,
        rows: [ROW],
        // The real, unmodified provider contract: `detail` is always a
        // placeholder here, never the real detail shape. See
        // models/catalog_provider.py / tools_catalog_provider.py
        // `_build_payload`.
        detail: {},
        total: 1, provenance: "test provenance",
    };
}

function detailPayload() {
    return {
        ok: true, reason: null, title: "Widget", subtitle: "Screws",
        specs: [{ label: "Grit", value: "120" }],
        engineering: [{ label: "Preferred vendor", value: "Acme" }],
        badges: [{ label: "Hazardous", kind: "danger" }],
    };
}

test("state.detail keeps a renderable shape across a reload that keeps the same row selected", async () => {
    const h = await mountCatalog({
        ormCall: (model, method) => {
            assert.equal(model, "materials.catalog.provider");
            if (method === "get_catalog") return catalogPayload();
            if (method === "get_detail") return detailPayload();
            throw new Error(`unexpected RPC ${method}`);
        },
    });

    // Initial mount: onWillStart's load() already awaited selectRow(),
    // so state.detail is the real get_detail payload.
    assert.deepEqual(h.component.state.selectedProductId, 1);
    assert.ok(Array.isArray(h.component.state.detail.specs));
    assert.equal(h.component.state.detail.specs.length, 1);

    // A second load() — e.g. a facet toggle or search keystroke — whose
    // result set still contains the currently-selected row. This is the
    // `stillPresent` fast path in load(): it returns immediately, on the
    // documented assumption that state.detail is left untouched ("the
    // panel stays put"). Before the fix, the preceding Object.assign had
    // already clobbered state.detail to the payload's `{}` placeholder.
    await h.component.load();

    assert.equal(h.component.state.selectedProductId, 1);
    assert.ok(
        Array.isArray(h.component.state.detail.specs),
        `state.detail.specs must stay an array — got ${JSON.stringify(h.component.state.detail)}. ` +
        `This is exactly what catalog.xml's t-foreach="state.detail.specs" needs; anything else ` +
        `reproduces the production OwlError "Invalid loop expression: 'undefined' is not iterable".`,
    );
    assert.ok(Array.isArray(h.component.state.detail.engineering));
    assert.ok(Array.isArray(h.component.state.detail.badges));
    // And it should still be showing the SAME detail that was already
    // loaded, not a blanked-out panel — the whole point of the
    // stillPresent fast path.
    assert.equal(h.component.state.detail.title, "Widget");

    h.destroy();
});
