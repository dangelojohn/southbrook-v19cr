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
