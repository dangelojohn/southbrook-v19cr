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
});
