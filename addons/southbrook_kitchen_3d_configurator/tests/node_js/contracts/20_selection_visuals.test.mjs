/**
 * Task 22 (SBK-036 · corner-remediation-2026-07-30) — selection visual
 * feedback contract.
 *
 * Audit finding this fixes: clicking a cabinet only highlighted the
 * inventory row — the 3D view never changed (every cabinet stayed the
 * same grey box). Root cause: PR3.1's group-transform refactor
 * (`_placeCabinetGroup`) changed `KitchenCanvas.T.cabObjs` to hold
 * THREE.Group WRAPPERS instead of cab-flagged meshes directly, but the
 * pre-Task-22 `highlightSelected` (selection.esm.js) still checked
 * `m.userData?.cab` against each `cabObjs` ENTRY — always false against
 * a Group (only its CHILDREN carry `userData.cab`), so the function
 * silently did nothing from that refactor onward.
 *
 * This exercises the real, unmodified `_placeCabinetGroup` (same
 * production path base/wall/corner cabinets are actually built
 * through) against the real vendored r160 Three.js — same "no WebGL
 * needed, pure CPU matrix/material math" architecture as
 * 03_renderer_coords / 10_scene_selection_identity / 17_corner_identity
 * — then drives the real `applySelectionVisuals` / `clearSelectionVisuals`
 * from selection.esm.js against the groups it produced.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "../harness/setup_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ADDON_ROOT = path.resolve(HERE, "..", "..", "..");
const CANVAS_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "kitchen_canvas.esm.js");
const BASE_CABINET_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "base_cabinet.esm.js");
const CORNER_CABINET_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "corner_cabinet.esm.js");
const SELECTION_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "selection.esm.js");
const CONSTANTS_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "constants.esm.js");

async function loadReal() {
    await setupDom();
    const { KitchenCanvas } = await import(CANVAS_SRC);
    const { buildBaseCabinet } = await import(BASE_CABINET_SRC);
    const { buildCornerCabinet, cornerBlindNearX0 } = await import(CORNER_CABINET_SRC);
    const { applySelectionVisuals, clearSelectionVisuals, createSelectionState } = await import(SELECTION_SRC);
    const { P } = await import(CONSTANTS_SRC);
    const THREE = global.window.THREE;
    return {
        KitchenCanvas, buildBaseCabinet, buildCornerCabinet, cornerBlindNearX0,
        applySelectionVisuals, clearSelectionVisuals, createSelectionState, P, THREE,
    };
}

function fakeCanvas(THREE, createSelectionState) {
    const scene = new THREE.Scene();
    return { T: { THREE, scene, cabObjs: [], clickable: [], selectionVisual: createSelectionState() } };
}

// Every cab-flagged mesh in a group (mirrors selection.esm.js's own
// internal traversal, kept independent here so the test isn't just
// re-asserting the implementation against itself).
function cabMeshesOf(group) {
    const out = [];
    group.traverse((obj) => { if (obj.userData && obj.userData.cab) out.push(obj); });
    return out;
}

test("applySelectionVisuals tints the selected cabinet's meshes and leaves the other cabinet untouched", async () => {
    const { KitchenCanvas, buildBaseCabinet, applySelectionVisuals, createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item1 = {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    const item2 = {
        layout_key: "back-2", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 24, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item1, buildBaseCabinet);
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item2, buildBaseCabinet);

    const mesh1 = cabMeshesOf(fake.T.cabObjs[0])[0];
    const mesh2 = cabMeshesOf(fake.T.cabObjs[1])[0];
    const original1 = mesh1.material;
    const original2 = mesh2.material;

    applySelectionVisuals(fake.T, item1, P);

    assert.notEqual(mesh1.material, original1,
        "the selected cabinet's material must be swapped for a tinted clone");
    assert.equal(mesh2.material, original2,
        "an UNSELECTED cabinet's material must never be touched");
    assert.notEqual(original1.dispose, undefined, "sanity: original material is a real THREE material");
    assert.notEqual(mesh1.material.color.getHex(), original1.color.getHex(),
        "the tinted clone's color must differ from the untouched original");
});

test("clearSelectionVisuals restores the EXACT original material object (reference identity), not a fresh copy", async () => {
    const { KitchenCanvas, buildBaseCabinet, applySelectionVisuals, clearSelectionVisuals,
            createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item = {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    const mesh = cabMeshesOf(fake.T.cabObjs[0])[0];
    const original = mesh.material;

    applySelectionVisuals(fake.T, item, P);
    assert.notEqual(mesh.material, original, "selection must have swapped the material");

    clearSelectionVisuals(fake.T.selectionVisual);
    assert.equal(mesh.material, original,
        "deselect must restore the EXACT SAME material object, not an equal-looking clone");
});

test("selection composes with — and visibly wins over — a corner cabinet's teal tint, and restores it exactly on deselect", async () => {
    const { KitchenCanvas, buildCornerCabinet, cornerBlindNearX0,
            applySelectionVisuals, clearSelectionVisuals, createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item = {
        layout_key: "corner-1", cabinet_type: "corner", wall: "left",
        default_code: "SB-CORNER-R", // diagonal clipped-corner style
        width_in: 36, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
        pinned: false,
    };
    const room = { width_in: 120, depth_in: 96 };
    KitchenCanvas.prototype._placeCabinetGroup.call(
        fake, item, buildCornerCabinet, { __blindNearX0: cornerBlindNearX0(item, room) });

    const mesh = cabMeshesOf(fake.T.cabObjs[0])[0];
    const original = mesh.material;
    const tealMixedHex = original.color.getHex(); // baseline WITH the corner tint already baked in

    applySelectionVisuals(fake.T, item, P);
    assert.notEqual(mesh.material, original, "corner cabinet material must also be swapped on select");
    assert.notEqual(mesh.material.color.getHex(), tealMixedHex,
        "selection tint must visibly change the color, not leave the teal-only mix untouched");

    clearSelectionVisuals(fake.T.selectionVisual);
    assert.equal(mesh.material, original,
        "deselect must restore the exact original (teal-tinted) material object");
    assert.equal(mesh.material.color.getHex(), tealMixedHex,
        "the restored material's color must be exactly the pre-selection teal-mixed value");
});

test("Task 22 review fix: EVERY cab-flagged mesh of a clipped-corner group (body + door face + handle) tints together, not just the body", async () => {
    const { KitchenCanvas, buildCornerCabinet, cornerBlindNearX0,
            applySelectionVisuals, clearSelectionVisuals, createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item = {
        layout_key: "corner-1", cabinet_type: "corner", wall: "left",
        default_code: "SB-CORNER-R", // diagonal clipped-corner style (buildClippedCorner)
        width_in: 36, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
        pinned: false,
    };
    const room = { width_in: 120, depth_in: 96 };
    KitchenCanvas.prototype._placeCabinetGroup.call(
        fake, item, buildCornerCabinet, { __blindNearX0: cornerBlindNearX0(item, room) });

    const group = fake.T.cabObjs[0];
    const cabMeshes = cabMeshesOf(group);
    // body + the diagonal door face + its handle — the prominent
    // "lazy susan" surface this cabinet style exists for. Before the
    // review fix, only `body` carried ud:{cab:true}; the door face
    // (faceStripGeometry) and handle did not, so the most visible
    // surface silently stayed untinted/unoutlined on selection.
    assert.equal(cabMeshes.length, 3,
        "expected exactly 3 cab-flagged meshes: body, door face, handle");
    const originals = cabMeshes.map(m => m.material);

    applySelectionVisuals(fake.T, item, P);
    cabMeshes.forEach((m, i) => {
        assert.notEqual(m.material, originals[i],
            `cab-flagged mesh #${i} (of ${cabMeshes.length}) must be tinted on select`);
    });

    // Toe-kick + countertop are deliberately NOT cab-flagged (matches
    // every other builder in this file — they're structural/material
    // surfaces, not door/carcass identity) — confirm they stay
    // completely untouched, not swept up by some broader traversal.
    const nonCabMeshes = [];
    group.traverse((obj) => {
        if (obj.isMesh && !(obj.userData && obj.userData.cab)) nonCabMeshes.push(obj);
    });
    assert.ok(nonCabMeshes.length >= 2, "expect at least toe-kick + countertop as non-cab meshes");
    const nonCabOriginals = nonCabMeshes.map(m => m.material);
    nonCabMeshes.forEach((m, i) => {
        assert.equal(m.material, nonCabOriginals[i],
            "a non-cab-flagged mesh's material must never be touched by selection");
    });

    clearSelectionVisuals(fake.T.selectionVisual);
    cabMeshes.forEach((m, i) => {
        assert.equal(m.material, originals[i],
            `cab-flagged mesh #${i} must restore its exact original material on deselect`);
    });
});

test("F8 identity rule still holds for a SELECTED item: userData.item stays the real state item through select + deselect", async () => {
    const { KitchenCanvas, buildBaseCabinet, applySelectionVisuals, clearSelectionVisuals,
            createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item = {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 30, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    const group = fake.T.cabObjs[0];
    const mesh = cabMeshesOf(group)[0];

    assert.equal(mesh.userData.item, item, "precondition: F8 identity holds before any selection");

    applySelectionVisuals(fake.T, item, P);
    assert.equal(mesh.userData.item, item,
        "selection must never rebind userData.item — only .material may be swapped");
    assert.equal(group.userData.item, item);

    // Mutation proof, exactly like 10_scene_selection_identity.test.mjs:
    // a drag/rotate write via userData.item must still land on the real
    // state item while the cabinet is selected.
    mesh.userData.item.x_position_in = 999;
    assert.equal(item.x_position_in, 999);

    clearSelectionVisuals(fake.T.selectionVisual);
    assert.equal(mesh.userData.item, item,
        "deselect must never rebind userData.item either");
});

test("applySelectionVisuals(null) clears any active selection and adds nothing new", async () => {
    const { KitchenCanvas, buildBaseCabinet, applySelectionVisuals,
            createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item = {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    const mesh = cabMeshesOf(fake.T.cabObjs[0])[0];
    const original = mesh.material;

    applySelectionVisuals(fake.T, item, P);
    assert.notEqual(mesh.material, original);

    applySelectionVisuals(fake.T, null, P);
    assert.equal(mesh.material, original, "deselecting (null item) must restore the original material");
    assert.equal(fake.T.selectionVisual.outlines.length, 0);
    assert.equal(fake.T.selectionVisual.bboxHelper, null);
});

test("re-selecting a different cabinet clears the previous one's visuals first (no leaked tint)", async () => {
    const { KitchenCanvas, buildBaseCabinet, applySelectionVisuals,
            createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item1 = {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    const item2 = {
        layout_key: "back-2", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 24, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item1, buildBaseCabinet);
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item2, buildBaseCabinet);
    const mesh1 = cabMeshesOf(fake.T.cabObjs[0])[0];
    const mesh2 = cabMeshesOf(fake.T.cabObjs[1])[0];
    const original1 = mesh1.material;
    const original2 = mesh2.material;

    applySelectionVisuals(fake.T, item1, P);
    assert.notEqual(mesh1.material, original1);

    applySelectionVisuals(fake.T, item2, P);
    assert.equal(mesh1.material, original1, "switching selection must restore the previous mesh's material");
    assert.notEqual(mesh2.material, original2, "the newly-selected mesh must now be tinted");
    // Exactly one bounding-box helper should ever be live in the scene.
    const boxHelpers = fake.T.scene.children.filter(c => c.type === "Box3Helper" || c.isBox3Helper);
    assert.equal(boxHelpers.length, 1, "only one selection bbox helper may be in the scene at a time");
});

test("a bounding-box helper is added to the scene for the selected cabinet and removed on deselect", async () => {
    const { KitchenCanvas, buildBaseCabinet, applySelectionVisuals, clearSelectionVisuals,
            createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item = {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);

    applySelectionVisuals(fake.T, item, P);
    assert.ok(fake.T.selectionVisual.bboxHelper, "a Box3Helper must be tracked once something is selected");
    assert.ok(fake.T.scene.children.includes(fake.T.selectionVisual.bboxHelper),
        "the bbox helper must actually be attached to the scene, not just tracked");

    clearSelectionVisuals(fake.T.selectionVisual);
    assert.equal(fake.T.selectionVisual.bboxHelper, null);
});

// Review fix (Finding 3) — onWillUnmount previously never cleared the
// selection tint/outline/bbox before destroyScene, so unmounting while
// a cabinet was selected leaked the tint clone's material, every
// outline LineSegments' geometry+material, and the Box3Helper's
// geometry+material (destroyScene only disposes the renderer/orbit,
// never traverses the scene graph). Fixed by extracting a
// `_teardownScene(mount)` method that onWillUnmount now calls, mirroring
// the same `clearSelectionVisuals` call `_buildScene` already made.
//
// This environment has no WebGL (THREE.WebGLRenderer's constructor
// throws without a real GL context — see scene_init.esm.js), so a
// literal owl.mount()+unmount() of KitchenCanvas isn't possible here;
// every other real-KitchenCanvas test in this suite works around that
// by calling a prototype method directly against a fake `this`
// (`_placeCabinetGroup`, `_sbSceneSnapshot`) instead of going through
// the full component lifecycle — this test does the same against the
// newly-extracted `_teardownScene`, the exact function onWillUnmount
// now calls.
test("Task 22 review fix: unmounting while a cabinet is selected clears the tint/outline/bbox instead of leaking them", async () => {
    const { KitchenCanvas, buildBaseCabinet, applySelectionVisuals,
            createSelectionState, P, THREE } = await loadReal();
    const fake = fakeCanvas(THREE, createSelectionState);

    const item = {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
    };
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    const mesh = cabMeshesOf(fake.T.cabObjs[0])[0];
    const original = mesh.material;

    applySelectionVisuals(fake.T, item, P);
    assert.notEqual(mesh.material, original, "precondition: the cabinet is selected (tinted) going into unmount");
    assert.ok(fake.T.selectionVisual.outlines.length > 0, "precondition: at least one outline is live");
    assert.ok(fake.T.selectionVisual.bboxHelper, "precondition: the bbox helper is live");

    // Exactly what onWillUnmount calls — no renderer/orbit on this fake
    // T, which destroyScene already guards against (`if (T.orbit)` /
    // `if (T.renderer)`), so this exercises clearSelectionVisuals
    // end-to-end without needing a real WebGL renderer to tear down.
    KitchenCanvas.prototype._teardownScene.call(fake, null);

    assert.equal(mesh.material, original,
        "unmount must restore the tinted mesh's original material, not leave the clone attached");
    assert.equal(fake.T.selectionVisual.tinted.size, 0, "no tinted-mesh entries may survive unmount");
    assert.equal(fake.T.selectionVisual.outlines.length, 0, "no outline LineSegments may survive unmount");
    assert.equal(fake.T.selectionVisual.bboxHelper, null, "the bbox helper must be cleared on unmount");
    assert.ok(!fake.T.scene.children.includes(fake.T.selectionVisual.bboxHelper) &&
        fake.T.scene.children.every(c => c.type !== "Box3Helper"),
        "no Box3Helper may remain attached to the scene after unmount");
});
