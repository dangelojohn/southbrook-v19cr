/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — mesh factory.
 *
 * Rec D · Sprint 2d · Step 5 · closure-body extraction.
 *
 * Extracted from the pre-2d `mk = (geo, color, pos, rotE, opts) => …`
 * closure inside kitchen_configurator.js's `_buildScene`
 * (was line 1321-1335). The extracted function takes THREE + scene
 * explicitly so it can be shared across builders in the future
 * <KitchenCanvas>. Zero behaviour change; every mesh option flag
 * (rough / metal / cs / rs / ud) matches the pre-2d default.
 */

/**
 * Create + add a PBR mesh to the given Three.js scene.
 *
 * @param {typeof THREE} THREE — the vendored Three.js module
 * @param {THREE.Scene} scene — target scene (mesh is added to it)
 * @param {THREE.BufferGeometry} geo — mesh geometry
 * @param {number} color — hex color (e.g. 0xC9C4BC)
 * @param {[number, number, number]} pos — world position
 * @param {[number, number, number] | null} rotE — Euler rotation
 *   (x, y, z in radians) or null for identity
 * @param {object} opts — flags:
 *   - rough (number, default 0.7): MeshStandardMaterial roughness
 *   - metal (number, default 0.0): MeshStandardMaterial metalness
 *   - cs    (bool,   default false): castShadow
 *   - rs    (bool,   default false): receiveShadow
 *   - ud    (object, default undefined): userData attached to the mesh
 *   - side  ("double", default undefined -> THREE's own default
 *            FrontSide): render both faces. Task 8 (corner-remediation)
 *            — the hand-rolled clipped-corner prism geometry
 *            (corner_cabinet.esm.js) can't be visually winding-checked
 *            in this environment (no WebGL/browser here), so its
 *            meshes opt into DoubleSide as a deliberate hedge against a
 *            wrong-winding face silently disappearing. Every pre-
 *            existing call site omits this key, so this branch is
 *            strictly additive — zero behaviour change for them.
 * @returns {THREE.Mesh}
 */
export function makeMesh(THREE, scene, geo, color, pos, rotE, opts = {}) {
    const matOpts = {
        color,
        roughness: opts.rough != null ? opts.rough : 0.7,
        metalness: opts.metal != null ? opts.metal : 0.0,
    };
    if (opts.side === "double") matOpts.side = THREE.DoubleSide;
    const mat = new THREE.MeshStandardMaterial(matOpts);
    const m = new THREE.Mesh(geo, mat);
    m.position.set(...pos);
    if (rotE) { m.rotation.x = rotE[0]; m.rotation.y = rotE[1]; m.rotation.z = rotE[2]; }
    if (opts.cs) m.castShadow    = true;
    if (opts.rs) m.receiveShadow = true;
    if (opts.ud) m.userData      = opts.ud;
    scene.add(m);
    return m;
}
