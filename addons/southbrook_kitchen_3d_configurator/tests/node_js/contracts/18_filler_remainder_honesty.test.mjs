/**
 * Task 10 (corner-remediation, SBK-023) — honest remainder_in in
 * `_recomputeLayoutFromItems()`.
 *
 * V5-partc §SBK-023 finding #7 (CONFIRMED, "understated" direction):
 * the client used to redefine `remainder_in` as the sum of CURRENTLY
 * SURVIVING filler items' widths. Deleting one filler line (e.g. one
 * half of a "split" pair) doesn't repack the survivors to close the
 * gap — the room's real physical gap is unchanged — but the old
 * formula reported only whatever filler width happened to still be
 * present. Fixed: remainder_in is now the geometric identity (room
 * run length minus occupied cabinet-run length), matching
 * controllers/main.py's `rw - n*module_w` for the same run, and
 * INCLUDING a same-wall corner cabinet's reserved footprint in
 * "occupied" (a corner cell reserves real run length too — see
 * kitchen_design.py's wall_start_offsets folding).
 *
 * Review-fix amendments (same task, post-review):
 *   Finding 1 (Important) — an orphan filler (no real cabinet at all —
 *     either a pre-fix persisted "$70, zero cabinets" record, or every
 *     real cabinet deleted live) used to still count toward
 *     filler_price / the "+$X filler" UI row even though its lone
 *     pre-selection guard was already fixed. Both are now gated on the
 *     same `hasCabinets` check.
 *   Finding 2 (Minor) — a corner cabinet's back-wall occupancy now
 *     reads its ROTATION-correct extent (width_in for rot 0/180,
 *     depth_in for rot 90/270), matching kitchen_design.py's
 *     wall_start_offsets ext_x/ext_z split, instead of always width_in.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

function mountHydrated(room, lines) {
    return mountConfigurator({
        actionParams: { design_id: 950 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room, design_name: "T10 remainder honesty test", lines,
            }),
        },
    });
}

test("deleting one filler of a split pair does not shrink the true remainder (SBK-023 understated-direction fix)", async () => {
    // rw=30, module_w=24 -> n=1, remainder=6, split into two 3in fillers.
    const h = await mountHydrated(
        { width_in: 30, depth_in: 24, height_in: 96 },
        [
            {
                layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                width_in: 24, height_in: 34.5, depth_in: 24,
                x_position_in: 3, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 500,
            },
            {
                layout_key: "filler-L", cabinet_type: "filler", product_name: "Filler L",
                width_in: 3, height_in: 34.5, depth_in: 0.75,
                x_position_in: 0, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 35,
            },
            {
                layout_key: "filler-R", cabinet_type: "filler", product_name: "Filler R",
                width_in: 3, height_in: 34.5, depth_in: 0.75,
                x_position_in: 27, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 35,
            },
        ],
    );

    assert.equal(h.component.state.summary.remainder_in, 6,
        "fresh hydrate: remainder matches the run-length identity (30 - 1*24)");

    const rightFiller = h.component.state.items.find(it => it.layout_key === "filler-R");
    h.component.state.selected = rightFiller;
    h.component._removeSelectedCabinet();
    clearTimeout(h.component._autoSaveTimer);

    assert.equal(h.component.state.summary.remainder_in, 6,
        "deleting one filler leaves the true gap unchanged — must NOT drop to 3");
    assert.equal(h.component.state.summary.filler_price, 35,
        "filler_price still reflects only the surviving filler (unaffected by this fix)");
});

test("a same-wall corner cabinet's footprint counts as occupied run length, not remaining gap", async () => {
    // room 72in wide; base(24) + corner(36) on the back wall consume 60in
    // of run length -> honest remainder is 12, not 72 (corner ignored)
    // and not 0 (corner miscounted as a gap-filler).
    const h = await mountHydrated(
        { width_in: 72, depth_in: 96, height_in: 96 },
        [
            {
                layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                width_in: 24, height_in: 34.5, depth_in: 24,
                x_position_in: 0, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 500,
            },
            {
                layout_key: "corner-1", cabinet_type: "corner", product_name: "Corner unit",
                width_in: 36, height_in: 34.5, depth_in: 24,
                x_position_in: 24, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 900,
            },
        ],
    );

    assert.equal(h.component.state.summary.remainder_in, 12,
        "occupied = base(24) + corner(36) = 60; remainder = 72 - 60 = 12");
});

test("a corner cabinet on a DIFFERENT wall does not affect the back-wall remainder", async () => {
    // remainder_in has only ever described the back-wall run (room.width_in);
    // a left-wall corner must not bleed into that scalar.
    const h = await mountHydrated(
        { width_in: 48, depth_in: 96, height_in: 96 },
        [
            {
                layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                width_in: 24, height_in: 34.5, depth_in: 24,
                x_position_in: 0, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 500,
            },
            {
                layout_key: "corner-left", cabinet_type: "corner", product_name: "Corner unit",
                width_in: 36, height_in: 34.5, depth_in: 24,
                x_position_in: 0, y_position_in: 0, z_position_in: 0,
                wall: "left", price: 900,
            },
        ],
    );

    assert.equal(h.component.state.summary.remainder_in, 24,
        "only the back-wall base(24) counts as occupied; remainder = 48 - 24 = 24");
});

test("reopening a legacy zero-cabinet design (lone filler, no real cabinets) pre-selects nothing and reports no remainder", async () => {
    // The confirmed "$70 persisted, zero cabinets" SBK-023 finding: a
    // design saved before this fix could have a filler line with no
    // base/wall/corner cabinet at all. Reopening it must not treat the
    // filler as the selected "cabinet", and must not report an honest
    // "remainder" for a run that was never real to begin with.
    const h = await mountHydrated(
        { width_in: 96, depth_in: 96, height_in: 96 },
        [
            {
                layout_key: "filler-orphan", cabinet_type: "filler", product_name: "Filler",
                width_in: 12, height_in: 34.5, depth_in: 0.75,
                x_position_in: 0, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 140,
            },
        ],
    );

    assert.equal(h.component.state.selected, null,
        "a lone persisted filler must never be pre-selected as the edited cabinet");
    assert.equal(h.component.state.summary.remainder_in, 0,
        "no cabinets -> no honest remainder to report either");
    // Review fix (Finding 1) — the orphan filler's $140 must not leak
    // into filler_price / the "+$X filler" UI row either. An inert
    // filler with no cabinet run to belong to is not a real $ line.
    assert.equal(h.component.state.summary.filler_price, 0,
        "an orphan filler on a 0-cabinet design must not count toward filler_price");
});

test("deleting the last real cabinet leaves an orphan filler inert (filler_price drops to 0, not the filler's own $)", async () => {
    // Same failure shape as the legacy-reopen case above, but reached by
    // live user action: delete every real cabinet one at a time, leaving
    // a filler behind. _recomputeLayoutFromItems must treat it as inert
    // the moment the last real cabinet is gone, not just on hydrate.
    const h = await mountHydrated(
        { width_in: 30, depth_in: 24, height_in: 96 },
        [
            {
                layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                width_in: 24, height_in: 34.5, depth_in: 24,
                x_position_in: 3, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 500,
            },
            {
                layout_key: "filler-L", cabinet_type: "filler", product_name: "Filler L",
                width_in: 6, height_in: 34.5, depth_in: 0.75,
                x_position_in: 0, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 70,
            },
        ],
    );
    assert.equal(h.component.state.summary.filler_price, 70,
        "sanity check: filler_price counts normally while a real cabinet exists");

    const base = h.component.state.items.find(it => it.layout_key === "base-1");
    h.component.state.selected = base;
    h.component._removeSelectedCabinet();
    clearTimeout(h.component._autoSaveTimer);

    const s = h.component.state.summary;
    assert.equal(s.base_count, 0);
    assert.equal(s.filler_price, 0,
        "the surviving filler is orphaned (no cabinet run) — must go inert, not keep billing $70");
    assert.equal(s.remainder_in, 0);
});

test("a corner cabinet rotated 90/270 counts its DEPTH (not width) toward back-wall occupancy", async () => {
    // Mirrors kitchen_design.py's wall_start_offsets reservation math
    // (M2, read-only reference): rot 0/180 -> width_in spans the
    // back/front (X) axis; rot 90/270 -> the corner has pivoted, so
    // depth_in spans that axis instead. room=60in; base(24) + a
    // 90deg-rotated corner whose width_in=36 but depth_in=24 must
    // occupy 24 (its rotated back-wall extent), not 36 -> remainder
    // = 60 - 24 - 24 = 12, not 0.
    const h = await mountHydrated(
        { width_in: 60, depth_in: 96, height_in: 96 },
        [
            {
                layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                width_in: 24, height_in: 34.5, depth_in: 24,
                x_position_in: 0, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 500,
            },
            {
                layout_key: "corner-rot90", cabinet_type: "corner", product_name: "Corner unit",
                width_in: 36, height_in: 34.5, depth_in: 24, rotation_deg: 90,
                x_position_in: 24, y_position_in: 0, z_position_in: 0,
                wall: "back", price: 900,
            },
        ],
    );

    assert.equal(h.component.state.summary.remainder_in, 12,
        "rotated corner's back-wall extent is depth_in(24), not width_in(36): "
        + "60 - base(24) - corner_depth(24) = 12");
});
