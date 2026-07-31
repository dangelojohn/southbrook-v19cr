/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — reusable 3D canvas component.
 *
 * Rec D · Sprint 2d · Step 24c · visible-surface swap.
 *
 * The parent's <div t-ref="canvas3d"/> is gone. This component's own
 * <div t-ref="kitchenCanvas"> is now the ONLY visible 3D surface. It
 * owns:
 *   - the single WebGL context (parent's _initScene is a no-op stub)
 *   - the pointer pipeline (mousedown/move/up/leave/over on its own div)
 *   - the wheel-zoom on its own div
 *   - the camera controller (setView / animateCamera / applyOrthoFrustum)
 *   - the drop-lane visualisation
 *   - the selection highlight (post-hit + on-selected-prop-change)
 *
 * State-mutating side effects flow to the parent via 4 typed callbacks:
 *   props.onSelectItem(item)                    — cabinet click
 *   props.onMoveItem({item, x_position_in,
 *                     pinnable})                — move-drag commit
 *   props.onResizeRoom(newWidthIn, inFlight)    — room-width drag
 *   props.onResizeRoomDepth(newDepthIn, inFlight) — room-depth drag
 *   props.onViewChange(view)                    — post-setView ack
 *
 * Parent-scoped keyboard + HTML5 drop handlers reach into this
 * component via `props.onReady(api)`, which yields:
 *   api.setView(key)         — imperative view change
 *   api.zoomBy(k)            — imperative ortho zoom
 *   api.computeDropX(ev)     — floor-plane raycast for drop targets
 */

import {
    Component, onMounted, onWillUnmount, onWillUpdateProps, useRef, xml,
} from "@odoo/owl";

import { IN, P, FULL_HEIGHT_WALLS } from "@southbrook_kitchen_3d_configurator/js/canvas/constants.esm";
import { loadThreeJS }       from "@southbrook_kitchen_3d_configurator/js/canvas/three_loader.esm";
import { initScene }         from "@southbrook_kitchen_3d_configurator/js/canvas/scene_init.esm";
import { destroyScene }      from "@southbrook_kitchen_3d_configurator/js/canvas/scene_dispose.esm";
import { installPbrEnvMap }  from "@southbrook_kitchen_3d_configurator/js/canvas/pbr_env_map.esm";
import { makeMesh }          from "@southbrook_kitchen_3d_configurator/js/canvas/mesh_factory.esm";
import { buildRoomShell }    from "@southbrook_kitchen_3d_configurator/js/canvas/room_shell.esm";
import { buildDragHandle, buildDepthHandle } from "@southbrook_kitchen_3d_configurator/js/canvas/drag_handle.esm";
import { buildDropLanes }    from "@southbrook_kitchen_3d_configurator/js/canvas/drop_lanes.esm";
import { buildBaseCabinet }  from "@southbrook_kitchen_3d_configurator/js/canvas/base_cabinet.esm";
import { buildWallCabinet }  from "@southbrook_kitchen_3d_configurator/js/canvas/wall_cabinet.esm";
import {
    buildOtherCabinet, buildFillerPanel, buildEndCapPanel,
} from "@southbrook_kitchen_3d_configurator/js/canvas/other_cabinets.esm";
import {
    buildCornerCabinet, cornerBlindNearX0,
} from "@southbrook_kitchen_3d_configurator/js/canvas/corner_cabinet.esm";
import { ndcFromEvent }      from "@southbrook_kitchen_3d_configurator/js/canvas/pointer_helpers.esm";
import { computeViewSpecs }  from "@southbrook_kitchen_3d_configurator/js/canvas/view_specs.esm";
import { computeOrthoFrustum } from "@southbrook_kitchen_3d_configurator/js/canvas/ortho_frustum.esm";
import {
    applySelectionVisuals, clearSelectionVisuals, createSelectionState,
} from "@southbrook_kitchen_3d_configurator/js/canvas/selection.esm";
import { computeDropXIn }    from "@southbrook_kitchen_3d_configurator/js/canvas/drop_raycaster.esm";
import { easeInOutCubic }    from "@southbrook_kitchen_3d_configurator/js/canvas/easing.esm";
import {
    setView as setViewShared,
    animateCamera as animateCameraShared,
    onWheel as onWheelShared,
} from "@southbrook_kitchen_3d_configurator/js/canvas/camera_controller.esm";
import {
    isHandleActiveView, resolveRoomWidthFromDrag, resolveRoomDepthFromDrag,
    isCabinetDragCommitted, isPinnable, cursorForPointerState,
} from "@southbrook_kitchen_3d_configurator/js/canvas/pointer_pipeline.esm";

export class KitchenCanvas extends Component {
    // Task 22 (SBK-036) — tabindex makes the canvas a focusable surface
    // (keyboard shortcuts already work regardless, via the parent's
    // window-scoped keydown listener; this only adds the DOM focus
    // state a :focus outline can key off — see the SCSS focus-ring
    // rule for .o_sbk_canvas3d). aria-label gives it an accessible
    // name for screen readers tabbing through the page.
    static template = xml`
        <div t-ref="kitchenCanvas" class="o_sbk_canvas3d" tabindex="0"
             aria-label="3D kitchen scene. Click a cabinet to select it, arrow keys cycle selection, 1-6 switch camera views."/>
    `;
    static props = {
        items:          { type: Array, optional: true },
        room:           { type: Object, optional: true },
        view:           { type: String, optional: true },
        selected:       { type: [Object, { value: null }], optional: true },
        draggedProduct: { type: [Object, { value: null }], optional: true },
        dragHover:      { type: Boolean, optional: true },
        onSelectItem:   { type: Function, optional: true },
        onMoveItem:     { type: Function, optional: true },
        onResizeRoom:   { type: Function, optional: true },
        onResizeRoomDepth: { type: Function, optional: true },
        onViewChange:   { type: Function, optional: true },
        onReady:        { type: Function, optional: true },
        // Phase 1 interactive walls — the parent's active wall + the
        // click-to-select callback.
        onWallSelect:   { type: Function, optional: true },
        activeWall:     { type: [String, { value: null }], optional: true },
    };
    static defaultProps = {
        items: [], room: {}, view: "iso",
        selected: null, draggedProduct: null, dragHover: false,
        activeWall: null,
    };

    setup() {
        this.canvasRef = useRef("kitchenCanvas");
        this._currentView    = this.props.view || "iso";
        this._lastResizeWidth = null;
        this._lastResizeDepth = null;
        this._resizeObserver  = null;
        this.T = {
            THREE: null, scene: null, renderer: null,
            cameras: { ortho: null, persp: null },
            activeCamera: null, currentTarget: null,
            viewSpecs: null, vsScale: 1,
            orbit: null, raycaster: null,
            roomObjs: [], cabObjs: [], clickable: [],
            wallMeshes: {}, hoverWall: null,
            handleMesh: null, arrowMeshes: [],
            depthHandleMesh: null, depthArrowMeshes: [],
            laneMeshes: null,
            dragging: false, dragX0: 0, dragW0: 0,
            draggingDepth: false, dragY0: 0, dragD0: 0,
            animId: null, animatingCamera: false,
            movingItem: null, moveStartClient: { x: 0, y: 0 },
            moveLastX: null, moveCommitted: false,
            // Task 22 (SBK-036) — persistent across `_buildScene`
            // rebuilds; cleared (never recreated) at the top of every
            // rebuild so it never holds a reference into a torn-down
            // group. See selection.esm.js's docstring.
            selectionVisual: createSelectionState(),
        };
        this._onMouseDown = this._onMouseDown.bind(this);
        this._onMouseMove = this._onMouseMove.bind(this);
        this._onMouseUp   = this._onMouseUp.bind(this);
        this._onMouseOver = this._onMouseOver.bind(this);
        this._onWheel     = this._onWheel.bind(this);

        onMounted(async () => {
            // TRIPWIRE:kitchen_canvas_mount_24c
            const mount = this.canvasRef.el;
            if (!mount) return;
            const THREE = await loadThreeJS();
            this.T.THREE = THREE;
            this.T.raycaster = new THREE.Raycaster();
            Object.assign(this.T, initScene(THREE, mount, P));
            installPbrEnvMap(THREE, this.T.scene, this.T.renderer);

            const tick = () => {
                this.T.animId = requestAnimationFrame(tick);
                if (this.T.orbit && this.T.orbit.enabled) this.T.orbit.update();
                this.T.renderer.render(this.T.scene, this.T.activeCamera);
            };
            tick();

            this._resizeObserver = new ResizeObserver(() => {
                const nw = mount.clientWidth, nh = mount.clientHeight;
                if (!nw || !nh) return;
                this.T.cameras.persp.aspect = nw / nh;
                this.T.cameras.persp.updateProjectionMatrix();
                this.T.renderer.setSize(nw, nh);
                this._applyOrthoFrustum();
            });
            this._resizeObserver.observe(mount);

            mount.addEventListener("mousedown",  this._onMouseDown, { capture: true });
            mount.addEventListener("mousemove",  this._onMouseMove);
            mount.addEventListener("mouseup",    this._onMouseUp);
            mount.addEventListener("mouseleave", this._onMouseUp);
            mount.addEventListener("mousemove",  this._onMouseOver);
            mount.addEventListener("wheel",      this._onWheel, { passive: false });

            this._buildScene(this.props);

            // Golden-scene harness — expose a serialisable snapshot of every
            // cabinet leaf-mesh world transform, for visual-regression
            // baselines (straight vs L). Inspection-only; no rendering effect.
            // Capture in the browser console via:
            //   document.querySelector('.o_sbk_canvas3d').__sbSceneSnapshot()
            mount.__sbSceneSnapshot = () => this._sbSceneSnapshot();

            if (this.props.onReady) {
                this.props.onReady({
                    setView: (key) => this._setView(key),
                    zoomBy:  (k)   => this._zoomBy(k),
                    computeDropX: (ev) => this._computeDropX(ev),
                    // PR4b — sibling to computeDropX: which wall the HTML5
                    // drop landed on (null when the ray misses every wall).
                    computeDropWall: (ev) => this._computeDropWall(ev),
                    // T7 — drag ghost preview (visual affordance only;
                    // placement math stays server-side).
                    updateDragGhost: (ev) => this._updateDragGhost(ev),
                    clearDragGhost:  ()   => this._clearDragGhost(),
                });
            }
        });

        onWillUpdateProps((next) => {
            if (!this.T.scene) return;
            // In-flight drag: child is source of truth; ignore
            // prop echos from parent to avoid a re-render loop.
            if (this.T.dragging || this.T.draggingDepth || this.T.movingItem) return;

            const roomChanged = next.room !== this.props.room ||
                (next.room && this.props.room && (
                    next.room.width_in  !== this.props.room.width_in  ||
                    next.room.depth_in  !== this.props.room.depth_in  ||
                    next.room.height_in !== this.props.room.height_in));
            const itemsChanged  = next.items !== this.props.items ||
                (next.items && this.props.items &&
                 next.items.length !== this.props.items.length);
            const selChanged    = next.selected !== this.props.selected;
            const dragUiChanged = next.dragHover !== this.props.dragHover ||
                                  next.draggedProduct !== this.props.draggedProduct;
            const viewChanged   = next.view !== this._currentView;

            if (roomChanged || itemsChanged) {
                this._buildScene(next);
            } else if (selChanged) {
                applySelectionVisuals(this.T, next.selected, P);
            }
            // Active-wall change: just repaint the wall glow (no rebuild).
            if (!(roomChanged || itemsChanged)
                && next.activeWall !== this.props.activeWall) {
                this._applyWallHighlight(next.activeWall);
            }
            if (dragUiChanged) this._updateLaneVisibility(next);
            if (viewChanged) {
                this._currentView = next.view;
                this._setView(next.view, /*instant=*/false, /*skipCallback=*/true);
            }
        });

        onWillUnmount(() => {
            if (this.T.animId) cancelAnimationFrame(this.T.animId);
            if (this._resizeObserver) this._resizeObserver.disconnect();
            const mount = this.canvasRef.el;
            if (mount) {
                mount.removeEventListener("mousedown",  this._onMouseDown, { capture: true });
                mount.removeEventListener("mousemove",  this._onMouseMove);
                mount.removeEventListener("mouseup",    this._onMouseUp);
                mount.removeEventListener("mouseleave", this._onMouseUp);
                mount.removeEventListener("mousemove",  this._onMouseOver);
                mount.removeEventListener("wheel",      this._onWheel);
            }
            this._teardownScene(mount);
        });
    }

    // Task 22 review fix (SBK-036, 2026-07-30) — extracted from
    // onWillUnmount's body so it's independently callable, same as
    // `_placeCabinetGroup`/`_sbSceneSnapshot` above (this environment
    // has no WebGL, so a full owl.mount()+unmount() of this component
    // isn't possible in the node test harness — THREE.WebGLRenderer's
    // constructor throws without a real GL context; see scene_init.esm.js.
    // Tests instead call this method directly against a fake `this`,
    // exactly like every other `KitchenCanvas.prototype.*.call(fake, ...)`
    // test in this suite).
    //
    // Unmounting while a cabinet was selected used to leak the tint
    // clone's material, every outline LineSegments' geometry+material,
    // and the Box3Helper's geometry+material: `destroyScene` only
    // disposes the renderer/orbit, never traverses/disposes the scene
    // graph (a pre-existing characteristic of roomObjs/cabObjs too —
    // out of this task's scope to fix generally), so nothing else
    // would have caught these Task-22-introduced objects either.
    // `clearSelectionVisuals` mirrors the same call at the top of every
    // `_buildScene` rebuild.
    _teardownScene(mount) {
        clearSelectionVisuals(this.T.selectionVisual);
        destroyScene(this.T, mount);
    }

    _buildScene(props) {
        this._clearDragGhost();   // T7 — never survive a rebuild
        // Task 22 (SBK-036) — clear the selection tint/outline/bbox
        // BEFORE the old cabObjs are disposed below: this restores
        // every tinted mesh's material to its original reference and
        // disposes our own clones/overlays deterministically, instead
        // of leaving the original (untinted) material objects to leak
        // as orphaned references once the old groups are torn down.
        clearSelectionVisuals(this.T.selectionVisual);
        const { THREE, scene } = this.T;
        if (!scene) return;
        const room = props.room || {};
        const rw = (room.width_in  || 12) * IN;
        const rd = (room.depth_in  || 24) * IN;
        const rh = (room.height_in || 96) * IN;

        const dispose = (n) => {
            if (!n) return;
            if (n.geometry) n.geometry.dispose();
            if (n.material) {
                if (Array.isArray(n.material)) n.material.forEach(x => x.dispose());
                else n.material.dispose();
            }
        };
        [...this.T.roomObjs, ...this.T.cabObjs].forEach(m => {
            if (m && typeof m.traverse === "function") m.traverse(dispose);
            else dispose(m);
            scene.remove(m);
        });
        if (this.T.handleMesh) { scene.remove(this.T.handleMesh); dispose(this.T.handleMesh); }
        this.T.arrowMeshes.forEach(m => { scene.remove(m); dispose(m); });
        if (this.T.depthHandleMesh) { scene.remove(this.T.depthHandleMesh); dispose(this.T.depthHandleMesh); }
        this.T.depthArrowMeshes.forEach(m => { scene.remove(m); dispose(m); });
        if (this.T.laneMeshes) {
            for (const k of ["base", "wall", "tall"]) {
                const m = this.T.laneMeshes[k];
                if (!m) continue;
                scene.remove(m); dispose(m);
            }
            this.T.laneMeshes = null;
        }
        this.T.roomObjs = []; this.T.cabObjs = []; this.T.clickable = [];
        this.T.wallMeshes = {};
        this.T.handleMesh = null; this.T.arrowMeshes = [];
        this.T.depthHandleMesh = null; this.T.depthArrowMeshes = [];

        const mk = (geo, color, pos, rotE, opts) =>
            makeMesh(THREE, scene, geo, color, pos, rotE, opts);

        const { objects: shellObjects, walls } =
            buildRoomShell(THREE, scene, mk, P, rw, rh, rd);
        this.T.roomObjs.push(...shellObjects);
        this.T.wallMeshes = walls || {};

        const items = props.items || [];
        const knownTypes = new Set(["base", "wall", "filler", "panel", "corner"]);
        // PR3.1 — every cabinet type routes through the single central
        // group-transform path (_placeCabinetGroup); the old per-type
        // rotation-gated dual dispatch (legacy absolute path vs. group
        // path) is gone. See _placeCabinetGroup's docstring for why this
        // is byte-identical for rotation_deg=0 (back-wall) cabinets.
        items.filter(it => it.cabinet_type === "base").forEach(it =>
            this._placeCabinetGroup(it, buildBaseCabinet));
        items.filter(it => it.cabinet_type === "wall").forEach(it =>
            this._placeCabinetGroup(it, buildWallCabinet));
        // Task 8 (corner-remediation) — corner lines get real corner
        // massing (diagonal/pie-cut/blind) instead of falling into the
        // generic buildOtherCabinet box below. cornerBlindNearX0 is a
        // pure geometry helper (position + room dims only, no placement
        // change) that tells the blind-corner style which end of the
        // cell sits nearest an actual room corner; every other style
        // ignores it. Passed via `extra` (code-review fix, 2026-07-30)
        // — NOT by substituting a copy for `it` — so the real state
        // item, not a clone, is what _placeCabinetGroup binds as
        // userData.item (drag/pin/select identity; see its docstring).
        items.filter(it => it.cabinet_type === "corner").forEach(it =>
            this._placeCabinetGroup(it, buildCornerCabinet,
                { __blindNearX0: cornerBlindNearX0(it, room) }));
        items.filter(it => !knownTypes.has(it.cabinet_type)).forEach(it =>
            this._placeCabinetGroup(it, buildOtherCabinet));
        items.filter(it => it.cabinet_type === "filler").forEach(it =>
            this._placeCabinetGroup(it, buildFillerPanel));
        items.filter(it => it.cabinet_type === "panel").forEach(it =>
            this._placeCabinetGroup(it, buildEndCapPanel));

        const { handleMesh, arrowMeshes } =
            buildDragHandle(THREE, scene, P, rw, rd);
        this.T.handleMesh = handleMesh;
        this.T.arrowMeshes.push(...arrowMeshes);
        const { depthHandleMesh, depthArrowMeshes } =
            buildDepthHandle(THREE, scene, P, rw, rd);
        this.T.depthHandleMesh = depthHandleMesh;
        this.T.depthArrowMeshes.push(...depthArrowMeshes);
        this.T.laneMeshes = buildDropLanes(THREE, scene, rw, rh);
        this._updateLaneVisibility(props);

        this._recomputeViews(rw, rh, rd);
        this._setView(props.view || this._currentView, /*instant=*/true, /*skipCallback=*/true);
        if (props.selected) applySelectionVisuals(this.T, props.selected, P);
        this._applyWallHighlight(props.activeWall);
    }

    // Phase 1 — paint each room wall by its interaction state: the active
    // (selected) wall gets a strong blue glow, the hovered wall a soft one,
    // everything else no glow. Purely material emissive; re-applied after
    // every rebuild and on hover/select changes.
    _applyWallHighlight(active) {
        if (active === undefined) active = this.props.activeWall;
        const hover  = this.T.hoverWall;
        for (const [name, mesh] of Object.entries(this.T.wallMeshes)) {
            if (!mesh || !mesh.material) continue;
            let hex = 0x000000, intensity = 0.0;
            if (name === active) { hex = 0x1866D4; intensity = 0.45; }
            else if (name === hover) { hex = 0x1866D4; intensity = 0.16; }
            mesh.material.emissive.setHex(hex);
            mesh.material.emissiveIntensity = intensity;
        }
    }

    // ── T7 — drag ghost preview ─────────────────────────────────────
    // ONE translucent box at the hovered wall's run end while a catalog
    // product is dragged over the canvas. Extends the wall hover soft-
    // glow with a concrete "it would land about here" affordance. NO
    // placement math is duplicated: the box parks at the end of the
    // hovered wall's current run (max along + width); the real pose
    // still comes from the server-side engine on drop.
    _updateDragGhost(ev) {
        const THREE = this.T.THREE;
        const prod = this.props.draggedProduct;
        if (!THREE || !this.T.scene || !this.T.activeCamera || !prod) {
            this._clearDragGhost();
            return;
        }
        this.T.raycaster.setFromCamera(
            this._ndcFromEvent(ev), this.T.activeCamera);
        const wall = this._raycastWall(this.T.raycaster);
        if (!wall) {
            this._clearDragGhost();
            return;
        }
        if (wall !== this.T.hoverWall) {
            this.T.hoverWall = wall;
            this._applyWallHighlight();
        }
        const room = this.props.room || {};
        const roomW = room.width_in || 12, roomD = room.depth_in || 24;
        const isUpper = prod.cabinet_type === "wall";
        const w = prod.width_in  || 24;
        const h = prod.height_in || (isUpper ? 30 : 34.5);
        const d = prod.depth_in  || (isUpper ? 12 : 24);
        const alongOf = (it) => (wall === "back" || wall === "front")
            ? (it.x_position_in || 0) : (it.z_position_in || 0);
        const runEnd = (this.props.items || [])
            .filter(it => (it.wall || "back") === wall
                && (it.cabinet_type === "wall") === isUpper)
            .reduce((m, it) => Math.max(m, alongOf(it) + (it.width_in || 0)), 0);
        let y = 0;
        if (isUpper) {
            const anyUpper = (this.props.items || []).find(
                it => it.cabinet_type === "wall");
            y = (anyUpper && anyUpper.y_position_in) || 54;
        }
        // Centre-of-box position per wall (axis-aligned; side walls
        // swap width/depth so the box hugs the wall).
        let cx, cz, gw = w, gd = d;
        if (wall === "back")       { cx = runEnd + w / 2; cz = d / 2; }
        else if (wall === "front") { cx = runEnd + w / 2; cz = roomD - d / 2; }
        else if (wall === "left")  { gw = d; gd = w; cx = d / 2; cz = runEnd + w / 2; }
        else                       { gw = d; gd = w; cx = roomW - d / 2; cz = runEnd + w / 2; }
        if (!this.T.ghostMesh) {
            const geo = new THREE.BoxGeometry(1, 1, 1);
            const mat = new THREE.MeshStandardMaterial({
                color: 0x1866D4, transparent: true, opacity: 0.35,
                depthWrite: false,
            });
            this.T.ghostMesh = new THREE.Mesh(geo, mat);
            this.T.ghostMesh.renderOrder = 999;
            this.T.scene.add(this.T.ghostMesh);
        }
        this.T.ghostMesh.scale.set(gw * IN, h * IN, gd * IN);
        this.T.ghostMesh.position.set(
            cx * IN, (y + h / 2) * IN, cz * IN);
        this.T.ghostMesh.visible = true;
    }

    _clearDragGhost() {
        const g = this.T.ghostMesh;
        if (!g) return;
        this.T.scene && this.T.scene.remove(g);
        if (g.geometry) g.geometry.dispose();
        if (g.material) g.material.dispose();
        this.T.ghostMesh = null;
    }

    _updateLaneVisibility(props) {
        const lanes = this.T.laneMeshes;
        if (!lanes) return;
        const show = !!(props.dragHover && props.draggedProduct);
        const type = (props.draggedProduct && props.draggedProduct.cabinet_type) || "";
        const baseHi = (type === "base" || type === "filler");
        const wallHi = (type === "wall");
        const tallHi = (type === "tall" || type === "corner" || type === "panel");
        lanes.base.visible = show;
        lanes.wall.visible = show;
        lanes.tall.visible = show;
        if (show) {
            lanes.base.material.opacity = baseHi ? 0.42 : 0.14;
            lanes.wall.material.opacity = wallHi ? 0.42 : 0.14;
            lanes.tall.material.opacity = tallHi ? 0.42 : 0.14;
        }
    }

    _recomputeViews(rw, rh, rd) {
        this.T.viewSpecs = computeViewSpecs(this.T.THREE, rw, rh, rd);
    }

    _applyOrthoFrustum() {
        const t = this.T;
        const cam = t.cameras?.ortho;
        if (!cam || !t.renderer) return;
        const spec = t.viewSpecs?.[this._currentView];
        const f = computeOrthoFrustum(
            spec && spec.vs, t.vsScale,
            t.renderer.domElement.width,
            t.renderer.domElement.height,
        );
        cam.left = f.left; cam.right = f.right;
        cam.top  = f.top;  cam.bottom = f.bottom;
        cam.near = f.near; cam.far    = f.far;
        cam.updateProjectionMatrix();
    }

    _setView(key, instant = false, skipCallback = false) {
        if (!this.T.viewSpecs || !this.T.viewSpecs[key]) return;
        this._currentView = key;
        const ok = setViewShared(this.T, key, {
            instant,
            applyOrthoFrustum: () => this._applyOrthoFrustum(),
            animateCamera: (p, tgt, up, ms) => this._animateCamera(p, tgt, up, ms),
        });
        if (ok && !skipCallback && this.props.onViewChange) {
            this.props.onViewChange(key);
        }
    }

    _animateCamera(toPos, toTarget, toUp, ms = 400) {
        animateCameraShared(
            this.T, toPos, toTarget, toUp, ms,
            easeInOutCubic, requestAnimationFrame,
        );
    }

    _onWheel(e) { onWheelShared(this.T, e, () => this._applyOrthoFrustum()); }

    _zoomBy(k) {
        this.T.vsScale = Math.max(0.3, Math.min(3.0, (this.T.vsScale || 1) * k));
        this._applyOrthoFrustum();
    }

    _ndcFromEvent(ev) { return ndcFromEvent(ev, this.canvasRef.el); }

    _computeDropX(ev) {
        return computeDropXIn(
            this.T.THREE, this.T.activeCamera, this.T.raycaster,
            this._ndcFromEvent(ev),
            (this.props.room && this.props.room.width_in) || 0,
        );
    }

    // PR4b — cast the HTML5 drop point against the tagged room-wall
    // meshes and return which wall (back/left/right/front) the pointer
    // dropped onto. This adds NO placement math: it only names the
    // wall. Returns null when the ray misses every eligible wall mesh
    // (e.g. a drop over open floor) so the caller can fall back to
    // activeWall.
    //
    // F7 fix (2026-07-27) — this used to call the shared `_raycastWall`
    // (all four wall meshes, including the low 12" kick-strips
    // room_shell.esm.js renders for right/front). A drop near the room
    // boundary could land on an invisible kick-strip and silently
    // resolve to that wall — overriding the user's actual Active Wall
    // pick even though they never intended to target right/front.
    // Drop-inference is now restricted to `_raycastDropWall`, which only
    // considers FULL_HEIGHT_WALLS (constants.esm.js). Click-to-select
    // (`_onMouseDown`/`_onMouseOver`, still via `_raycastWall`) is
    // unaffected — kick-strip walls stay explicitly clickable.
    _computeDropWall(ev) {
        const { activeCamera, raycaster } = this.T;
        if (!activeCamera || !raycaster) return null;
        raycaster.setFromCamera(this._ndcFromEvent(ev), activeCamera);
        return this._raycastDropWall(raycaster);
    }

    // F7 fix (2026-07-27) — drop-inference-only wall raycast: filters
    // `wallMeshes` down to the full-height walls (back/left today) before
    // testing intersection, so a stray ray landing on a low kick-strip
    // (right/front) never gets inferred as a deliberate drop-onto-wall.
    // Sibling to `_raycastWall` (used by click/hover, all four walls).
    _raycastDropWall(raycaster) {
        const meshes = Object.entries(this.T.wallMeshes)
            .filter(([name, mesh]) => mesh && FULL_HEIGHT_WALLS.includes(name))
            .map(([, mesh]) => mesh);
        if (!meshes.length) return null;
        const hit = raycaster.intersectObjects(meshes, false);
        return hit.length ? (hit[0].object.userData && hit[0].object.userData.wall) || null : null;
    }

    // Golden-scene harness. Returns a sorted, order-independent list of every
    // cabinet leaf-mesh world transform [x, y, z, rotY°] (mm/deg, rounded).
    // Traversing to leaf meshes keeps the snapshot stable across the coming
    // THREE.Group refactor: a straight kitchen must produce an IDENTICAL
    // snapshot before and after each builder migration (compatibility gate).
    _sbSceneSnapshot() {
        const THREE = this.T.THREE, scene = this.T.scene;
        if (!THREE || !scene) return [];
        scene.updateMatrixWorld(true);
        const out = [];
        const p = new THREE.Vector3(), q = new THREE.Quaternion(),
              s = new THREE.Vector3(), e = new THREE.Euler();
        const collect = (obj) => {
            if (!obj || !obj.isMesh) return;
            obj.matrixWorld.decompose(p, q, s);
            e.setFromQuaternion(q, "YXZ");
            out.push([
                +p.x.toFixed(3), +p.y.toFixed(3), +p.z.toFixed(3),
                +((e.y * 180) / Math.PI).toFixed(1),
            ]);
        };
        for (const obj of this.T.cabObjs) {
            if (obj && obj.traverse) obj.traverse(collect);
            else collect(obj);
        }
        out.sort();
        return out;
    }

    // PR3.1 — the single placement path for every cabinet: a THREE.Group
    // carrying its world transform (x/y/z_position_in + rotation_deg per
    // COORDINATE_CONTRACT). The builder builds in a LOCAL frame
    // (x_position_in forced to 0; each builder that reads y_position_in
    // also zeroes it locally via item.__localFrame — see base/wall/other
    // builders); the group applies the world position + Y-rotation.
    // Teardown already handles groups (traverse); raycasting still hits
    // the real body mesh (its matrixWorld includes the group transform).
    //
    // Byte-identical for rotation_deg=0 (back-wall) cabinets by
    // construction: PR3.0 guarantees z_position_in=0 for every back-wall
    // cabinet_type, and y_position_in is 0 for floor-standing types
    // (base/tall/corner/panel/filler — every writer emits y=0,z=0 for
    // these) or the real D8 mount height for wall cabinets (every writer
    // emits a nonzero y for wall cabinets; wall_cabinet.esm.js's wbY
    // fallback additionally reproduces the pre-D8 WBY default locally
    // when y_position_in is ever falsy, so the group's grp.position.y=0
    // in that case doesn't drop the cabinet to the floor). With those
    // offsets zero (or reproduced locally), grp.position + local mesh
    // coordinates sum to exactly the old legacy absolute-path world
    // coordinates.
    // `extra` (optional) — additional read-only geometry hints merged
    // ONLY into the local-frame copy the builder sees (e.g. corner_
    // cabinet.esm.js's __blindNearX0), never onto `it` itself and never
    // onto the object bound as userData.item below. Task 8 code-review
    // fix (2026-07-30): an earlier version of the corner dispatch
    // computed such a hint by passing a NEW shallow-copied item as `it`
    // — that copy, not the real state.items entry, is what got bound as
    // userData.item, breaking drag/pin/select for every corner cabinet
    // (the exact F8 bug class described below, reintroduced by that
    // call site). `extra` fixes this at the root: `it` passed to this
    // method is ALWAYS the real state item, unconditionally.
    _placeCabinetGroup(it, builder, extra) {
        const THREE = this.T.THREE, scene = this.T.scene;
        const grp = new THREE.Group();
        const mkG = (geo, color, pos, rotE, opts) =>
            makeMesh(THREE, grp, geo, color, pos, rotE, opts);
        const res = builder(THREE, mkG, P,
            { ...it, ...(extra || null), x_position_in: 0, __localFrame: true });
        // F8 fix (2026-07-27) — the builder above ran against `{ ...it,
        // x_position_in: 0, __localFrame: true }`, a SHALLOW COPY used
        // only so the builder's local-frame geometry math (walls/panels
        // reading __localFrame to zero their own Y/X contribution — see
        // wall_cabinet.esm.js / other_cabinets.esm.js) works regardless
        // of the group's world transform below. Every builder attaches
        // that copy as `mesh.userData.item` (base/wall/other_cabinets'
        // `ud: { cab, cabType, item }`), so without this rebind
        // `mesh.userData.item !== it` — the real state.items entry.
        // Consequence (verified live): `_onMouseDown`'s drag-move and
        // Q/E rotate mutate the clone's x_position_in/rotation_deg
        // (never seen again — it's discarded on the next rebuild) while
        // `_savePinnedPosition`/`onMoveItem` still persist the clone's
        // values to the DB by layout_key — a silent no-op in the
        // client's own state that only surfaces as a divergence on
        // reload. The clone's x_position_in is also permanently forced
        // to 0, so `state.selected` (populated straight from
        // `h.userData.item` in `_onMouseDown`) read x=0 for whatever was
        // selected.
        //
        // Fix: after the builder returns, walk every mesh it attached
        // (the group + every child, since `mkG` above adds children
        // directly onto `grp`) and rebind any `userData.item` that
        // still points at the local-frame copy back onto the REAL item
        // `it`. The copy is discarded here; nothing downstream ever
        // sees it again. This does not touch the builder's geometry math
        // (which already ran) — only the ATTACHED INTERACTION IDENTITY.
        grp.traverse((obj) => {
            if (obj.userData && obj.userData.item) {
                obj.userData.item = it;
            }
        });
        grp.userData.item = it;
        grp.position.set(
            (it.x_position_in || 0) * IN,
            (it.y_position_in || 0) * IN,
            (it.z_position_in || 0) * IN,
        );
        grp.rotation.y = ((it.rotation_deg || 0) * Math.PI) / 180;
        scene.add(grp);
        this.T.cabObjs.push(grp);
        if (res && res.clickable) this.T.clickable.push(...res.clickable);
    }

    _onMouseDown(e) {
        // Task 22 (SBK-036) — canvas focus ring. Several branches below
        // call e.preventDefault() (handle-drag, cabinet-select), which
        // in some browsers suppresses the native "click focuses the
        // element" default action — so focus is claimed explicitly
        // here, unconditionally, rather than relying on that default.
        const mount = this.canvasRef.el;
        if (mount && typeof mount.focus === "function") {
            mount.focus({ preventScroll: true });
        }
        const { activeCamera, raycaster, handleMesh, arrowMeshes,
                depthHandleMesh, depthArrowMeshes, clickable } = this.T;
        if (!activeCamera || !raycaster) return;
        raycaster.setFromCamera(this._ndcFromEvent(e), activeCamera);

        // A handle's grab target is the whole visible cluster — the ●
        // sphere AND its flanking arrow cones. Sphere-only hit-testing
        // made the arrows dead: the width arrows happened to overlap
        // their sphere in screen space (so pulling them "worked"), but
        // the depth arrows don't, so pulling them hit nothing.
        const hitsWidth = handleMesh &&
            raycaster.intersectObjects([handleMesh, ...arrowMeshes], false).length;
        const hitsDepth = depthHandleMesh &&
            raycaster.intersectObjects([depthHandleMesh, ...depthArrowMeshes], false).length;

        const handleActive = isHandleActiveView(this._currentView);
        // TEMP DIAG (2026-07-11): opt-in, off by default. In the browser
        // console run `window.__SBK_DEPTH_DEBUG__ = true` then click the
        // depth pin to see why the raycast is/ isn't hitting. Zero effect
        // for normal users. Remove once the depth handle is confirmed.
        if (typeof window !== "undefined" && window.__SBK_DEPTH_DEBUG__) {
            // eslint-disable-next-line no-console
            console.debug("[sbk-depth] mousedown", {
                view: this._currentView, handleActive,
                hasWidthMesh: !!handleMesh, hasDepthMesh: !!depthHandleMesh,
                widthHit: hitsWidth || 0, depthHit: hitsDepth || 0,
                client: [e.clientX, e.clientY],
            });
        }
        if (handleActive && hitsWidth) {
            this.T.dragging = true;
            this.T.dragX0   = e.clientX;
            this.T.dragW0   = (this.props.room && this.props.room.width_in) || 0;
            this._lastResizeWidth = null;
            e.preventDefault();
            return;
        }
        if (handleActive && hitsDepth) {
            this.T.draggingDepth = true;
            this.T.dragY0   = e.clientY;
            this.T.dragD0   = (this.props.room && this.props.room.depth_in) || 0;
            this._lastResizeDepth = null;
            e.preventDefault();
            return;
        }

        const hits = raycaster.intersectObjects(clickable, false);
        if (hits.length) {
            const h = hits[0].object;
            if (h.userData?.item) {
                if (this.props.onSelectItem) this.props.onSelectItem(h.userData.item);
                if (h.userData.item.cabinet_type !== "filler") {
                    this.T.movingItem      = h.userData.item;
                    this.T.moveStartClient = { x: e.clientX, y: e.clientY };
                    this.T.moveLastX       = h.userData.item.x_position_in;
                    this.T.moveCommitted   = false;
                    e.preventDefault();
                    e.stopPropagation();
                }
            }
        }

        // Wall pick — lowest priority, only when no handle or cabinet was hit.
        // Clicking a room wall makes it the active wall (Phase 1).
        if (!hits.length) {
            const wall = this._raycastWall(raycaster);
            if (wall && wall !== this.props.activeWall && this.props.onWallSelect) {
                this.props.onWallSelect(wall);
            }
        }
    }

    _raycastWall(raycaster) {
        const meshes = Object.values(this.T.wallMeshes).filter(Boolean);
        if (!meshes.length) return null;
        const hit = raycaster.intersectObjects(meshes, false);
        return hit.length ? (hit[0].object.userData && hit[0].object.userData.wall) || null : null;
    }

    _onMouseMove(e) {
        if (this.T.dragging) {
            const nw = resolveRoomWidthFromDrag(e.clientX, this.T.dragX0, this.T.dragW0);
            if (nw !== this._lastResizeWidth) {
                this._lastResizeWidth = nw;
                if (this.props.onResizeRoom) this.props.onResizeRoom(nw, /*inFlight=*/true);
            }
            return;
        }

        if (this.T.draggingDepth) {
            const nd = resolveRoomDepthFromDrag(e.clientY, this.T.dragY0, this.T.dragD0);
            if (typeof window !== "undefined" && window.__SBK_DEPTH_DEBUG__) {
                // eslint-disable-next-line no-console
                console.debug("[sbk-depth] move", { clientY: e.clientY, dragY0: this.T.dragY0, dragD0: this.T.dragD0, nd });
            }
            if (nd !== this._lastResizeDepth) {
                this._lastResizeDepth = nd;
                if (this.props.onResizeRoomDepth) this.props.onResizeRoomDepth(nd, /*inFlight=*/true);
            }
            return;
        }

        if (this.T.movingItem) {
            if (!this.T.moveCommitted &&
                !isCabinetDragCommitted(this.T.moveStartClient, e)) return;
            if (!this.T.moveCommitted) {
                this.T.moveCommitted = true;
                const cvs = this.T.renderer && this.T.renderer.domElement;
                if (cvs) cvs.style.cursor = "move";
            }
            const targetX = this._computeDropX(e);
            if (targetX == null) return;
            if (this.T.movingItem.x_position_in === targetX) return;
            this.T.movingItem.x_position_in = targetX;
            this.T.moveLastX = targetX;
            this._buildScene(this.props);
        }
    }

    _onMouseUp() {
        if (this.T.dragging) {
            this.T.dragging = false;
            const finalNw = this._lastResizeWidth ??
                ((this.props.room && this.props.room.width_in) || 0);
            this._lastResizeWidth = null;
            if (this.props.onResizeRoom) this.props.onResizeRoom(finalNw, /*inFlight=*/false);
        }
        if (this.T.draggingDepth) {
            this.T.draggingDepth = false;
            const finalNd = this._lastResizeDepth ??
                ((this.props.room && this.props.room.depth_in) || 0);
            this._lastResizeDepth = null;
            if (typeof window !== "undefined" && window.__SBK_DEPTH_DEBUG__) {
                // eslint-disable-next-line no-console
                console.debug("[sbk-depth] up commit", { finalNd });
            }
            if (this.props.onResizeRoomDepth) this.props.onResizeRoomDepth(finalNd, /*inFlight=*/false);
        }
        if (this.T.movingItem) {
            const item = this.T.movingItem;
            const wasCommitted = this.T.moveCommitted;
            this.T.movingItem    = null;
            this.T.moveCommitted = false;
            const cvs = this.T.renderer && this.T.renderer.domElement;
            if (cvs) cvs.style.cursor = "";
            if (wasCommitted && this.props.onMoveItem) {
                this.props.onMoveItem({
                    item,
                    x_position_in: item.x_position_in,
                    pinnable:      isPinnable(item.cabinet_type),
                });
            }
        }
    }

    _onMouseOver(e) {
        const mount = this.canvasRef.el;
        if (!mount || !this.T.activeCamera || !this.T.raycaster) return;
        if (this.T.movingItem && this.T.moveCommitted) return;
        this.T.raycaster.setFromCamera(this._ndcFromEvent(e), this.T.activeCamera);
        const handleActive = isHandleActiveView(this._currentView);
        const onHandle = handleActive && this.T.handleMesh &&
            this.T.raycaster.intersectObjects([this.T.handleMesh, ...this.T.arrowMeshes], false).length > 0;
        const onDepthHandle = !onHandle && handleActive && this.T.depthHandleMesh &&
            this.T.raycaster.intersectObjects([this.T.depthHandleMesh, ...this.T.depthArrowMeshes], false).length > 0;
        const onCabinet = !onHandle && !onDepthHandle &&
            this.T.raycaster.intersectObjects(this.T.clickable, false).length > 0;

        // Wall hover — soft-glow the wall under the cursor (only when not over
        // a handle or cabinet). Repaint only when the hovered wall changes.
        const wall = (!onHandle && !onDepthHandle && !onCabinet)
            ? this._raycastWall(this.T.raycaster) : null;
        if (wall !== this.T.hoverWall) {
            this.T.hoverWall = wall;
            this._applyWallHighlight();
        }

        const cvs = this.T.renderer?.domElement;
        if (cvs) {
            const cur = cursorForPointerState({
                dragging: this.T.dragging, draggingDepth: this.T.draggingDepth,
                onHandle, onDepthHandle, onCabinet,
            });
            // A hoverable wall gets the pointer cursor when nothing else claims it.
            cvs.style.cursor = (cur === "default" && wall) ? "pointer" : cur;
        }
    }
}
