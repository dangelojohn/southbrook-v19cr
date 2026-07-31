/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — selection visual feedback.
 *
 * Task 22 (SBK-036 · corner-remediation-2026-07-30).
 *
 * Rewrite of the pre-Task-22 `highlightSelected` (Sprint 2d Step 9).
 * That function assumed `cabObjs` held cab-flagged MESHES directly
 * (`m.userData?.cab`, `m.material.color.setHex(...)`) and repainted
 * the mesh's own material in place. PR3.1's group-transform refactor
 * (`_placeCabinetGroup`, kitchen_canvas.esm.js) changed `cabObjs` to
 * hold THREE.Group WRAPPERS instead — the cab-flagged meshes are now
 * CHILDREN of those groups. `group.userData.cab` is never set (only
 * `group.userData.item` is), so the old function's every-entry check
 * was silently always-false: it stopped doing anything the moment
 * that refactor landed. This is the audit finding — "clicking a
 * cabinet highlights only the inventory row — no visible change in
 * the 3D view (every cabinet the same grey box)".
 *
 * This rewrite traverses into each group's children to find the real
 * cab meshes, and never mutates a mesh's ORIGINAL material in place —
 * materials may become shared/cached in a future mesh_factory revision
 * (see its docstring), so painting a material object that might be
 * referenced by more than one mesh would leak the highlight onto
 * every mesh sharing it. Every visual here is either a CLONE swapped
 * onto `mesh.material` (restored to the exact same original object on
 * deselect/clear — reference identity, not a fresh copy) or a pure
 * additive overlay (edge outline, bounding box) that never touches
 * the mesh's own material/geometry at all.
 *
 * Three signals compose to make the selected cabinet unmistakable in
 * every view, including the flat orthographic front/left/right views
 * where a color change alone reads poorly face-on:
 *   1. Tint    — each cab mesh's material is swapped for a clone
 *      blended toward palette.sel. Blends from whatever color the
 *      mesh already has (e.g. a corner cabinet's teal CORNER_TINT
 *      mix — corner_cabinet.esm.js), so selection COMPOSES with, and
 *      visibly wins over, the corner accent rather than replacing it.
 *   2. Outline — a THREE.LineSegments edge overlay (EdgesGeometry)
 *      per cab mesh, exactly matching its local transform. Reads
 *      crisply even face-on in ortho views, unlike a scaled
 *      "inflated hull" outline trick (which would look asymmetric
 *      once the group's own local mesh offsets are taken into
 *      account).
 *   3. Bounding box — one THREE.Box3Helper around the whole selected
 *      group's world AABB. The one signal that doesn't depend on
 *      which way the cabinet's own faces point — always a clean box,
 *      in every view.
 *
 * All three restore to nothing on deselect / before the next
 * selection: materials restored (by reference), helpers disposed and
 * removed from the scene.
 */

// Task 22 review fix (SBK-036, 2026-07-30) — mixHex used to be
// duplicated here and in corner_cabinet.esm.js; now a shared helper.
import { mixHex } from "@southbrook_kitchen_3d_configurator/js/canvas/color_utils.esm";

const OUTLINE_LINE_OPACITY = 0.9;
const TINT_AMOUNT = 0.55;

/**
 * Every cab-flagged mesh belonging to `item` across all groups in
 * `cabObjs`. Matches by layout_key (stable across rebuilds), not
 * object identity — group children are rebuilt fresh on every
 * `_buildScene`, so a mesh reference kept from a previous selection
 * cycle would be stale.
 *
 * @param {THREE.Group[]} cabObjs
 * @param {{ layout_key?: string } | null} item
 * @returns {THREE.Mesh[]}
 */
function cabMeshesForItem(cabObjs, item) {
    if (!item) return [];
    const out = [];
    for (const grp of cabObjs || []) {
        if (!grp || typeof grp.traverse !== "function") continue;
        grp.traverse((obj) => {
            if (obj.userData && obj.userData.cab &&
                obj.userData.item && obj.userData.item.layout_key === item.layout_key) {
                out.push(obj);
            }
        });
    }
    return out;
}

/**
 * Fresh, empty selection-visual tracking state. One instance lives on
 * `KitchenCanvas`'s `this.T` for the component's lifetime (survives
 * `_buildScene` rebuilds — {@link clearSelectionVisuals} is called
 * before every rebuild disposes the old scene graph, so it never
 * holds a dangling reference into a torn-down group across rebuilds).
 *
 * @returns {{ tinted: Map<THREE.Mesh, THREE.Material>,
 *             outlines: THREE.LineSegments[],
 *             bboxHelper: THREE.Box3Helper|null,
 *             selectedKey: string|null }}
 */
export function createSelectionState() {
    return { tinted: new Map(), outlines: [], bboxHelper: null, selectedKey: null };
}

/**
 * Clear every visual applied by a previous {@link applySelectionVisuals}
 * call: restore each tinted mesh's ORIGINAL material object (the exact
 * reference, not a fresh clone of it — callers/tests can assert
 * `mesh.material === originalMaterial` afterwards), dispose the tint
 * clones + outline geometries/materials, and remove/dispose the
 * bounding-box helper.
 *
 * Idempotent — safe to call repeatedly / with an already-empty state.
 *
 * @param {ReturnType<typeof createSelectionState>} state
 */
export function clearSelectionVisuals(state) {
    if (!state) return;
    for (const [mesh, originalMaterial] of state.tinted) {
        if (mesh.material && mesh.material !== originalMaterial &&
            typeof mesh.material.dispose === "function") {
            mesh.material.dispose();
        }
        mesh.material = originalMaterial;
    }
    state.tinted.clear();

    for (const outline of state.outlines) {
        if (outline.parent) outline.parent.remove(outline);
        if (outline.geometry) outline.geometry.dispose();
        if (outline.material) outline.material.dispose();
    }
    state.outlines.length = 0;

    if (state.bboxHelper) {
        if (state.bboxHelper.parent) state.bboxHelper.parent.remove(state.bboxHelper);
        if (state.bboxHelper.geometry) state.bboxHelper.geometry.dispose();
        if (state.bboxHelper.material) state.bboxHelper.material.dispose();
        state.bboxHelper = null;
    }
    state.selectedKey = null;
}

/**
 * Apply (or move) the selection visuals to `item`. Always clears
 * whatever was applied before first — cheap (bounded by one cabinet's
 * own mesh count) and keeps this idempotent under rapid re-selection
 * or a null (deselect) `item`.
 *
 * @param {object} T — KitchenCanvas's `this.T`: needs `.THREE`,
 *   `.scene`, `.cabObjs`, `.selectionVisual`
 *   (a {@link createSelectionState} instance).
 * @param {{ layout_key?: string } | null} item — selected item or null
 * @param {{ sel: number }} palette — hex colors (reads `.sel`)
 */
export function applySelectionVisuals(T, item, palette) {
    const state = T.selectionVisual;
    clearSelectionVisuals(state);
    if (!item || !T.THREE || !T.scene) return;

    const THREE = T.THREE;
    const meshes = cabMeshesForItem(T.cabObjs, item);
    if (!meshes.length) return;

    for (const mesh of meshes) {
        // ── 1. Tint — clone-on-select, never mutate the original. ──
        const original = mesh.material;
        state.tinted.set(mesh, original);
        const clone = original.clone();
        if (clone.color) {
            clone.color.setHex(mixHex(original.color.getHex(), palette.sel, TINT_AMOUNT));
        }
        if (clone.emissive) {
            clone.emissive.setHex(palette.sel);
            clone.emissiveIntensity = 0.25;
        }
        mesh.material = clone;

        // ── 2. Outline — edge overlay, exact transform match, purely
        // additive (never touches mesh.geometry/material). ──
        if (mesh.geometry) {
            const edges = new THREE.EdgesGeometry(mesh.geometry, 25);
            const line = new THREE.LineSegments(
                edges,
                new THREE.LineBasicMaterial({
                    color: palette.sel,
                    transparent: true,
                    opacity: OUTLINE_LINE_OPACITY,
                    depthTest: false,
                }),
            );
            line.renderOrder = 999;
            line.position.copy(mesh.position);
            line.rotation.copy(mesh.rotation);
            line.scale.copy(mesh.scale);
            (mesh.parent || T.scene).add(line);
            state.outlines.push(line);
        }
    }

    // ── 3. Bounding box — whole-group world AABB, view-independent. ──
    const group = (T.cabObjs || []).find(g => g && g.userData && g.userData.item &&
        g.userData.item.layout_key === item.layout_key);
    if (group) {
        const box = new THREE.Box3().setFromObject(group);
        if (!box.isEmpty()) {
            const helper = new THREE.Box3Helper(box, palette.sel);
            helper.material.depthTest = false;
            helper.renderOrder = 998;
            T.scene.add(helper);
            state.bboxHelper = helper;
        }
    }

    state.selectedKey = item.layout_key || null;
}
