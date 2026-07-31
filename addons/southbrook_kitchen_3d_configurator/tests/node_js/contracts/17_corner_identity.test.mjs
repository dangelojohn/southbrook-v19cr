/**
 * Task 8 code-review fix (corner-remediation, 2026-07-30) — pins the
 * identity-break finding against regressing.
 *
 * The corner-cabinet dispatch in kitchen_canvas.esm.js's `_buildScene`
 * originally computed a per-item "which end is nearest the room
 * corner" hint by calling a helper that returned a NEW shallow-copied
 * item (`{...item, __blindNearX0}`) and passed THAT COPY as the first
 * argument to `_placeCabinetGroup`. Since `_placeCabinetGroup` binds
 * its first argument as `mesh.userData.item` / `grp.userData.item`
 * (the canonical interaction identity every drag/pin/select handler
 * reads), every corner cabinet's clickable mesh ended up pointing at a
 * disconnected clone instead of the real `state.items` entry — the
 * exact F8 bug class `10_scene_selection_identity.test.mjs` already
 * pins for base/wall cabinets, reintroduced for corners.
 *
 * The fix: the geometry hint is now delivered via a THIRD, optional
 * `extra` argument to `_placeCabinetGroup`, merged only into the
 * LOCAL-FRAME COPY the builder itself sees — `it` (the first argument,
 * and the thing bound as userData.item) is always the real,
 * unmodified state item. This test exercises the real, unmodified
 * `_placeCabinetGroup` and `buildCornerCabinet` against the real
 * vendored r160 Three.js — same architecture as
 * `03_renderer_coords.test.mjs` / `10_scene_selection_identity.test.mjs`
 * (the closest existing models): no WebGL context needed, pure CPU
 * matrix math, `.call(fake, ...)` against a fake `this.T`.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "../harness/setup_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ADDON_ROOT = path.resolve(HERE, "..", "..", "..");
const CANVAS_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "kitchen_canvas.esm.js");
const CORNER_CABINET_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "corner_cabinet.esm.js");

async function loadReal() {
    await setupDom();
    const { KitchenCanvas } = await import(CANVAS_SRC);
    const { buildCornerCabinet, cornerBlindNearX0 } = await import(CORNER_CABINET_SRC);
    const THREE = global.window.THREE;
    return { KitchenCanvas, buildCornerCabinet, cornerBlindNearX0, THREE };
}

function fakeCanvas(THREE) {
    const scene = new THREE.Scene();
    return { T: { THREE, scene, cabObjs: [], clickable: [] } };
}

test("corner: _placeCabinetGroup binds the REAL state item as userData.item, not a hint-copy", async () => {
    const { KitchenCanvas, buildCornerCabinet, cornerBlindNearX0, THREE } = await loadReal();
    const fake = fakeCanvas(THREE);

    // Simulates a real state.items entry for a blind-corner design line
    // (the style whose massing actually consults the geometry hint).
    const cornerItem = {
        layout_key: "corner-1", cabinet_type: "corner", wall: "left",
        default_code: "SB-CORNER-BLIND",
        width_in: 45, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 90,
        pinned: false,
    };
    const room = { width_in: 120, depth_in: 96 };

    // Exactly the call kitchen_canvas.esm.js's _buildScene now makes.
    const hint = cornerBlindNearX0(cornerItem, room);
    KitchenCanvas.prototype._placeCabinetGroup.call(
        fake, cornerItem, buildCornerCabinet, { __blindNearX0: hint },
    );

    assert.equal(fake.T.clickable.length, 1);
    const body = fake.T.clickable[0];

    assert.equal(body.userData.item, cornerItem,
        "clickable mesh userData.item must be === the real state item, not a hint-carrying copy");
    assert.equal(fake.T.cabObjs[0].userData.item, cornerItem,
        "the enclosing group must also carry the real item's identity");

    // The real item must never be mutated by the hint computation or
    // by _placeCabinetGroup — __blindNearX0 only ever exists on the
    // internal local-frame copy the builder saw, never on state.items.
    assert.equal(cornerItem.__blindNearX0, undefined,
        "the real state item must never gain a __blindNearX0 field");
    assert.equal(cornerItem.x_position_in, 0,
        "the real item's own fields must be completely untouched");

    // Mutation proof, mirroring 10_scene_selection_identity.test.mjs:
    // a drag/rotate write via userData.item must land on the object
    // callers (state.items) actually hold a reference to.
    body.userData.item.x_position_in = 42;
    assert.equal(cornerItem.x_position_in, 42,
        "a drag-move mutation via userData.item must land on the real state item");
});

test("corner: two corner cabinets placed together each keep their OWN distinct real-item identity", async () => {
    const { KitchenCanvas, buildCornerCabinet, cornerBlindNearX0, THREE } = await loadReal();
    const fake = fakeCanvas(THREE);
    const room = { width_in: 120, depth_in: 96 };

    const cornerA = {
        layout_key: "corner-a", cabinet_type: "corner", wall: "left",
        default_code: "SB-CORNER-BLIND",
        width_in: 45, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 90,
    };
    const cornerB = {
        layout_key: "corner-b", cabinet_type: "corner", wall: "right",
        default_code: "SB-CORNER-BLIND",
        width_in: 45, height_in: 34.5, depth_in: 24,
        x_position_in: 120, y_position_in: 0, z_position_in: 0, rotation_deg: 270,
    };

    for (const it of [cornerA, cornerB]) {
        KitchenCanvas.prototype._placeCabinetGroup.call(
            fake, it, buildCornerCabinet, { __blindNearX0: cornerBlindNearX0(it, room) },
        );
    }

    assert.equal(fake.T.clickable.length, 2);
    const [bodyA, bodyB] = fake.T.clickable;
    assert.equal(bodyA.userData.item, cornerA);
    assert.equal(bodyB.userData.item, cornerB);
    assert.notEqual(bodyA.userData.item, bodyB.userData.item);

    bodyA.userData.item.x_position_in = 111;
    assert.equal(cornerA.x_position_in, 111);
    assert.equal(cornerB.x_position_in, 120, "cornerB must be untouched by a mutation on cornerA");
});

test("cornerBlindNearX0 returns a bare boolean/undefined, never an item copy", async () => {
    const { cornerBlindNearX0 } = await loadReal();
    const item = {
        width_in: 45, x_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    const result = cornerBlindNearX0(item, { width_in: 120, depth_in: 96 });
    assert.equal(typeof result, "boolean",
        "must return a bare boolean so the caller never mistakes it for a substitute item");
    // No room dims -> can't resolve -> undefined, not the item itself
    // and not a copy of it (the pre-fix version returned `item`).
    const unresolved = cornerBlindNearX0(item, null);
    assert.equal(unresolved, undefined);
});
