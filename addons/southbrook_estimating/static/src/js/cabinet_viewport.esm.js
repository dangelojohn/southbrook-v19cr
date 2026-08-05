/** @odoo-module **/
/*
 * SPDX-License-Identifier: LGPL-3.0-only
 *
 * Cabinet Viewport — OWL view widget that renders a parametric cabinet
 * in 3D for the OCA product_configurator wizard. Track 1 of Phase 2
 * charter amendment 1 (2026-05-30).
 *
 * Mount point: inherited product.configurator wizard form view
 *   (see views/product_configurator_3d_view.xml).
 *
 * RPC: product.config.session.get_3d_payload() — returns a per-panel
 * 3D layout derived from Phase-1 routine #1 (_compute_panel_dimensions).
 *
 * Three.js: required as window.THREE plus window.THREE.OrbitControls.
 * If absent, the component shows a friendly "not loaded" placeholder
 * pointing to addons/southbrook_estimating/static/lib/three/README.md
 * for vendoring instructions.
 */
import {
    Component,
    onMounted,
    onWillUnmount,
    onWillUpdateProps,
    useRef,
    useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class CabinetViewport extends Component {
    static template = "southbrook_estimating.CabinetViewport";
    static props = {
        record: { type: Object, optional: true },
        readonly: { type: Boolean, optional: true },
        name: { type: String, optional: true },
        "*": true,
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.orm = useService("orm");
        // T1C9 — action dispatcher for click-to-edit. doAction(...)
        // takes the action dict returned by sale.order.line.action_reconfigure
        // and routes it through Odoo's web client (typically opens a
        // modal wizard).
        this.action = useService("action");
        this.state = useState({
            mode: "solid",
            loading: false,
            error: null,
            threeLoaded: !!window.THREE,
            renderedAt: null,
            // T1C8 — per-line hover state (kitchen view only).
            hoveredLineId: null,
            hoveredLineInfo: null,
            // Phase 3 (2026-06-25) — live configured price summary +
            // dim summary chip + template SKU prefix chip.
            priceLabel: null,
            priceTooltip: null,
            dimsLabel: null,
            templateCode: null,
        });

        // Three.js scene handles — populated in _initThreeScene().
        this._THREE = null;
        this._renderer = null;
        this._scene = null;
        this._camera = null;
        this._controls = null;
        this._cabinetGroup = null;
        this._materials = {};
        this._frameId = null;
        this._resizeObserver = null;
        // T1C5: dimension-line overlay (auto-dimensioning per
        // PRODBOARD_MANIFEST §3 — derived programmatically from the
        // cabinet bounding box, snapped to the run).
        this._dimensionGroup = null;
        this._dimensionMaterial = null;
        this._spriteResources = [];

        // T1C8 — hover state for per-line highlighting.
        // _linesIndex caches payload.metadata.lines keyed by string
        // line id (matching the L{id}_ panel name prefix).
        this._linesIndex = {};
        this._hoveredLineId = null;
        this._raycaster = null;
        this._mouse = null;
        // T1C9 — click discrimination. OrbitControls owns mousedown +
        // drag; a click is "mousedown then mouseup within MAX_CLICK_DELTA
        // pixels with no significant movement between". This guards
        // against accidental clicks during orbit drags.
        this._mouseDownAt = null;
        this._MAX_CLICK_DELTA_PX = 5;

        // Reactivity state — used by onWillUpdateProps + debounced refresh.
        // Track 1 commit 2 reactivity contract:
        //   • _lastSessionId tracks which session we last fetched for.
        //   • _lastValueIdsKey is a stable join of the current value_ids;
        //     a change means the user picked / unpicked an attribute and
        //     we must re-fetch the payload.
        //   • _refreshTimer debounces rapid picks (the user can step
        //     through several attributes in <200ms; each onchange would
        //     otherwise fire an RPC).
        this._lastSessionId = null;
        this._lastValueIdsKey = null;
        this._refreshTimer = null;
        this._refreshDebounceMs = 150;

        onMounted(() => this._initThreeScene());
        onWillUpdateProps((nextProps) => {
            // The OCA wizard updates the record on every attribute pick:
            // the onchange handler writes the new value_ids onto the
            // session and back onto the wizard. fieldDependencies (below
            // at registry.add) ensures this widget receives those props.
            this._reactToRecord(nextProps.record);
        });
        onWillUnmount(() => this._dispose());
    }

    // ------------------------------------------------------------------
    // Init / dispose
    // ------------------------------------------------------------------

    async _initThreeScene() {
        const THREE = window.THREE;
        if (!THREE) {
            this.state.threeLoaded = false;
            return;
        }
        this._THREE = THREE;
        const canvas = this.canvasRef.el;
        if (!canvas) return;

        // Renderer — ACES Filmic per PRODBOARD_MANIFEST §10.
        this._renderer = new THREE.WebGLRenderer({
            canvas,
            antialias: true,
            alpha: false,
            // 2026-06-25 — preserveDrawingBuffer keeps the rendered
            // framebuffer addressable for onDownloadSnapshot's
            // canvas.toDataURL() call. Slight perf hit on continuous
            // re-rendering (the GPU can't ditch the buffer between
            // composites), negligible for a single-cabinet scene.
            preserveDrawingBuffer: true,
        });
        if (THREE.SRGBColorSpace) {
            this._renderer.outputColorSpace = THREE.SRGBColorSpace;
        }
        this._renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this._renderer.toneMappingExposure = 1.0;
        this._renderer.setPixelRatio(window.devicePixelRatio || 1);
        // T1C4: shadow grounding — PCFSoftShadowMap blurs shadow edges
        // for a less harsh, more product-shot look. Cost on a 5k-tri
        // cabinet scene with one shadow-casting light is negligible
        // even on integrated GPUs.
        this._renderer.shadowMap.enabled = true;
        this._renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this._fitRendererToCanvas();

        // Scene — paper-warm background to match the Southbrook palette.
        this._scene = new THREE.Scene();
        this._scene.background = new THREE.Color(0xfbf7ef);

        // Camera — sane defaults; payload.camera overrides on first build.
        this._camera = new THREE.PerspectiveCamera(35, 1, 10, 10000);
        this._camera.position.set(1200, 900, 1500);

        // Lights — 3 directional + 1 hemi. 2026-06-24 retry of Phase 1:
        // first pass added a PMREM env map and broke the viewport in a
        // way I couldn't diagnose from CLI (canvas blank, root cause
        // unknown without browser console). This version is DEFENSIVE:
        // lighting changes are zero-risk and shipped unconditionally;
        // the PMREM env map below is wrapped in try/catch so a failure
        // there logs a warning instead of taking down the canvas.
        // Net effect: worst case is "lighting improved, no env map";
        // best case is the full Phase 1 win.
        //
        // HemisphereLight intensity dropped slightly to leave headroom
        // for the (best-case) env map. Front-fill addresses the
        // "front face crushed to black" complaint either way.
        const hemi = new THREE.HemisphereLight(0xffffff, 0xd8cfbf, 0.4);
        this._scene.add(hemi);
        const dirA = new THREE.DirectionalLight(0xffffff, 0.9);
        dirA.position.set(800, 1200, 600);
        // T1C4: dirA is the shadow-casting "key" light. Shadow camera
        // bounds sized to cover any of the 12 Q8-locked templates
        // (max footprint: corner 900mm × 900mm, max height: tall
        // pantry 2100mm). Generous frustum = no shadow clipping
        // for any cabinet; mapSize 2048² = soft edges without
        // pixelation under default camera zoom.
        dirA.castShadow = true;
        dirA.shadow.mapSize.set(2048, 2048);
        dirA.shadow.camera.near = 100;
        dirA.shadow.camera.far = 6000;
        dirA.shadow.camera.left = -2500;
        dirA.shadow.camera.right = 2500;
        dirA.shadow.camera.top = 3000;
        dirA.shadow.camera.bottom = -500;
        // bias mitigates "shadow acne" — without it the cabinet's
        // own panels self-shadow with stripey artifacts.
        dirA.shadow.bias = -0.0005;
        this._scene.add(dirA);
        const dirB = new THREE.DirectionalLight(0xffffff, 0.4);
        dirB.position.set(-500, 500, 800);
        this._scene.add(dirB);
        // Front-fill — directly in front of the cabinet (camera-side),
        // slightly above. dirA (upper-right key) only grazes the front
        // face; this light hits it head-on. No shadows so we keep
        // dirA's shadow as the only grounding shadow source.
        const dirFront = new THREE.DirectionalLight(0xffffff, 0.5);
        dirFront.position.set(0, 800, 2000);
        this._scene.add(dirFront);

        // Phase 1 best-case: PMREM-baked env map for PBR reflections.
        // Wrapped in try/catch — if any step fails (older bundled
        // Three.js without PMREM, sigma-arg mismatch, GPU env quirk),
        // we log + continue. The lighting above stands on its own; the
        // env map is a bonus when it works.
        try {
            const pmrem = new THREE.PMREMGenerator(this._renderer);
            const envScene = new THREE.Scene();
            const envColors = {
                ceiling: 0xfff5e6,   // warm white — sun bounce
                walls:   0xe8e2d5,   // neutral warm — wall bounce
                floor:   0x6b5a48,   // medium walnut — floor bounce
            };
            const envPlane = (color) => new THREE.Mesh(
                new THREE.PlaneGeometry(2, 2),
                new THREE.MeshBasicMaterial({
                    color, side: THREE.DoubleSide,
                }),
            );
            const ceil = envPlane(envColors.ceiling);
            ceil.position.y = 1; ceil.rotation.x = Math.PI / 2;
            envScene.add(ceil);
            const envFloor = envPlane(envColors.floor);
            envFloor.position.y = -1; envFloor.rotation.x = -Math.PI / 2;
            envScene.add(envFloor);
            for (const [x, ry] of [[-1, Math.PI / 2], [1, -Math.PI / 2]]) {
                const w = envPlane(envColors.walls);
                w.position.x = x; w.rotation.y = ry;
                envScene.add(w);
            }
            for (const [z, ry] of [[-1, 0], [1, Math.PI]]) {
                const w = envPlane(envColors.walls);
                w.position.z = z; w.rotation.y = ry;
                envScene.add(w);
            }
            const rt = pmrem.fromScene(envScene, 0.04);
            this._envTexture = rt.texture;
            this._scene.environment = this._envTexture;
            pmrem.dispose();
        } catch (e) {
            // Non-fatal — viewport still renders with the lighting rig
            // above, just without the env-map specular reflections.
            // Surface so DevTools shows it but don't block.
            // eslint-disable-next-line no-console
            console.warn(
                "[CabinetViewport] PMREM env map setup failed; rendering "
                + "with lighting-only. Error:", e,
            );
            this._envTexture = null;
        }

        // T1C4: floor plane — receives the cabinet's shadow.
        // 20m × 20m so OrbitControls panning never reveals an edge;
        // colour matches the body background of the OWL portal
        // mockup (#e8dfd2) so the viewport visually nests into the
        // surrounding chrome. Roughness near 1 = matte = no specular
        // hot-spot from the directional light reflecting off the floor.
        const floorGeom = new THREE.PlaneGeometry(20000, 20000);
        const floorMat = new THREE.MeshStandardMaterial({
            color: 0xe8dfd2,
            roughness: 0.95,
            metalness: 0.0,
        });
        const floor = new THREE.Mesh(floorGeom, floorMat);
        floor.rotation.x = -Math.PI / 2;
        floor.position.y = 0;
        floor.receiveShadow = true;
        this._scene.add(floor);
        // Stash for disposal at unmount.
        this._floor = floor;
        this._floorMat = floorMat;
        this._floorGeom = floorGeom;

        // OrbitControls — drag-rotate, scroll-zoom.
        if (THREE.OrbitControls) {
            this._controls = new THREE.OrbitControls(this._camera, canvas);
            this._controls.enableDamping = true;
            this._controls.dampingFactor = 0.1;
            this._controls.target.set(0, 400, 0);
        }

        // Cabinet group — child of scene; cleared + repopulated per payload.
        this._cabinetGroup = new THREE.Group();
        this._scene.add(this._cabinetGroup);

        // T1C5: dimension-line group — populated by _buildDimensionLines
        // on every payload. Visibility tracks state.mode (only shown in
        // blueline mode). The group is OUTSIDE _cabinetGroup so it never
        // gets the wireframe-material swap during toggle.
        this._dimensionGroup = new THREE.Group();
        this._dimensionGroup.visible = this.state.mode === "blueline";
        this._scene.add(this._dimensionGroup);

        // Material registry — referenced by panel.material name.
        // Track 1 commit 3 added `toekick` + `worktop` for the family-
        // dispatched payloads (drawer_bank, base/sink/tall/vanity,
        // worktop slabs).
        this._materials = {
            carcass: new THREE.MeshStandardMaterial({
                color: 0xc89e85, roughness: 0.85, metalness: 0.0,
            }),
            // Generic "door" = walnut default (back-compat with every
            // panel emitted before the Phase 2 Round 4 finish split).
            door: new THREE.MeshStandardMaterial({
                color: 0x6b3f2a, roughness: 0.7, metalness: 0.05,
            }),
            // Phase 2 Round 4 (2026-06-24) — Finish-driven door
            // materials. Color + roughness tuned per finish family.
            // Painted (white) reads matte-cool; stained woods read
            // satin-warm with grain-tight roughness.
            door_white: new THREE.MeshStandardMaterial({
                color: 0xfafafa, roughness: 0.50, metalness: 0.02,
            }),
            door_maple_stain: new THREE.MeshStandardMaterial({
                color: 0xd9bb86, roughness: 0.60, metalness: 0.05,
            }),
            door_cherry_stain: new THREE.MeshStandardMaterial({
                color: 0x8b4a2f, roughness: 0.55, metalness: 0.05,
            }),
            door_walnut_stain: new THREE.MeshStandardMaterial({
                color: 0x4d2f1f, roughness: 0.65, metalness: 0.05,
            }),
            back: new THREE.MeshStandardMaterial({
                color: 0xa68872, roughness: 0.9, metalness: 0.0,
            }),
            shelf: new THREE.MeshStandardMaterial({
                color: 0xc89e85, roughness: 0.85, metalness: 0.0,
            }),
            // Toe-kick — typically painted matte black or recessed in
            // shadow. We use near-black with high roughness to read
            // visually as "the dark gap below the cabinet".
            toekick: new THREE.MeshStandardMaterial({
                color: 0x2a2520, roughness: 0.95, metalness: 0.0,
            }),
            // Worktop — light grey quartz/Caesarstone stand-in. When
            // the canonical worktop catalog lands (Caesarstone vs
            // butcher-block vs marble), this becomes attribute-driven.
            worktop: new THREE.MeshStandardMaterial({
                color: 0xb5b0a8, roughness: 0.4, metalness: 0.05,
            }),
            // Phase 2 Round 2.5 (2026-06-24) — Pull Finish-driven
            // hardware materials. 8 known finishes pre-registered
            // with color + roughness + metalness tuned per family.
            // "hardware" (the generic) stays as brushed-nickel default
            // so panels emitted without a finish pick (or with an
            // unknown finish) still render correctly.
            hardware: new THREE.MeshStandardMaterial({
                color: 0xa6a8ad, roughness: 0.35, metalness: 0.85,
            }),
            hardware_polished_nickel: new THREE.MeshStandardMaterial({
                color: 0xc8cad0, roughness: 0.15, metalness: 0.95,
            }),
            hardware_brushed_nickel: new THREE.MeshStandardMaterial({
                color: 0xa6a8ad, roughness: 0.40, metalness: 0.85,
            }),
            hardware_matte_black: new THREE.MeshStandardMaterial({
                color: 0x1a1a1a, roughness: 0.60, metalness: 0.20,
            }),
            hardware_antique_bronze: new THREE.MeshStandardMaterial({
                color: 0x6e4a2c, roughness: 0.50, metalness: 0.70,
            }),
            hardware_brushed_brass: new THREE.MeshStandardMaterial({
                color: 0xc9a64a, roughness: 0.35, metalness: 0.90,
            }),
            hardware_polished_chrome: new THREE.MeshStandardMaterial({
                color: 0xd8dade, roughness: 0.05, metalness: 1.00,
            }),
            hardware_oil_rubbed_bronze: new THREE.MeshStandardMaterial({
                color: 0x3a2618, roughness: 0.55, metalness: 0.40,
            }),
            hardware_champagne_bronze: new THREE.MeshStandardMaterial({
                color: 0xb09575, roughness: 0.40, metalness: 0.75,
            }),
            blueline: new THREE.MeshBasicMaterial({
                color: 0x2b4f6b, wireframe: true,
            }),
            // T1C8 — highlight overlay. Hovered cabinet's panels swap
            // to this material. Emissive sky tint over a warm carcass
            // base reads as "this cabinet is selected" without losing
            // the silhouette of the underlying meshes.
            highlight: new THREE.MeshStandardMaterial({
                color: 0xc89e85,
                emissive: 0x2b4f6b,
                emissiveIntensity: 0.4,
                roughness: 0.5,
                metalness: 0.1,
            }),
        };

        // T1C8 — raycaster for picking the hovered cabinet.
        this._raycaster = new THREE.Raycaster();
        this._mouse = new THREE.Vector2();

        // Bind canvas hover listeners. mouseleave clears the hover
        // so we never get a "stuck highlighted" cabinet when the
        // pointer exits the canvas.
        canvas.addEventListener("mousemove", (e) => this._onCanvasMouseMove(e));
        canvas.addEventListener("mouseleave", () => this._setHoveredLine(null));
        // T1C9 — click-to-edit. mousedown stores the position; mouseup
        // checks whether the cursor moved more than _MAX_CLICK_DELTA_PX.
        // If not, it's a click (not a drag) → raycast + open reconfigure.
        canvas.addEventListener("mousedown", (e) => this._onCanvasMouseDown(e));
        canvas.addEventListener("mouseup", (e) => this._onCanvasMouseUp(e));

        // Resize hook.
        if (typeof ResizeObserver === "function") {
            this._resizeObserver = new ResizeObserver(() => this._fitRendererToCanvas());
            this._resizeObserver.observe(canvas);
        }

        // Initial fetch + animate. Also prime the reactivity baseline
        // so the first onWillUpdateProps sees a populated _lastValueIdsKey
        // and only fires when the user actually picks something.
        // T1C6: dispatch by record model so both wizard + sale.order
        // mount points work from the same code path.
        const initialDispatch = this._rpcDispatch();
        this._lastSessionId = initialDispatch.recordId;
        this._lastValueIdsKey = this._recordKey(this.props.record);
        await this._fetchAndRebuild(initialDispatch.recordId);
        this._animate();
    }

    _animate() {
        if (!this._renderer) return;
        this._frameId = requestAnimationFrame(() => this._animate());
        if (this._controls) this._controls.update();
        this._renderer.render(this._scene, this._camera);
    }

    _fitRendererToCanvas() {
        const canvas = this.canvasRef.el;
        if (!canvas || !this._renderer) return;
        const w = canvas.clientWidth || 400;
        const h = canvas.clientHeight || 400;
        this._renderer.setSize(w, h, false);
        if (this._camera) {
            this._camera.aspect = w / h;
            this._camera.updateProjectionMatrix();
        }
    }

    _dispose() {
        if (this._frameId) cancelAnimationFrame(this._frameId);
        if (this._refreshTimer) clearTimeout(this._refreshTimer);
        if (this._resizeObserver) this._resizeObserver.disconnect();
        if (this._cabinetGroup) {
            this._cabinetGroup.traverse((obj) => {
                if (obj.geometry) obj.geometry.dispose();
            });
        }
        if (this._materials) {
            for (const m of Object.values(this._materials)) m.dispose?.();
        }
        // T1C4: floor + floor material need their own dispose; they're
        // not in this._materials (kept separate so blueline-toggle
        // material swap doesn't touch the floor).
        if (this._floorGeom) this._floorGeom.dispose();
        if (this._floorMat) this._floorMat.dispose();
        // 2026-06-24 Phase 1 retry: env map cleanup. Null-guard
        // because the PMREM block is try/catch'd — _envTexture stays
        // null on failure.
        if (this._envTexture) this._envTexture.dispose();
        // T1C5: dimension overlay — clears all line geometries, sprite
        // textures, sprite materials, and the shared line material.
        this._clearDimensionGroup();
        if (this._renderer) this._renderer.dispose();
    }

    // ------------------------------------------------------------------
    // Record helpers
    // ------------------------------------------------------------------

    _sessionIdFromRecord(record) {
        if (!record || !record.data) return null;
        const m2o = record.data.config_session_id;
        if (!m2o) return null;
        if (Array.isArray(m2o)) return m2o[0] || null;
        return m2o.id || m2o.resId || null;
    }

    /**
     * T1C6 — RPC dispatch by record model.
     *
     * The same component now renders TWO surfaces:
     *   • product.configurator → single-cabinet wizard preview
     *   • sale.order           → multi-cabinet kitchen-run preview
     *
     * Returning the right (model, method, recordId) tuple per record
     * keeps both surfaces sharing one Three.js scene + lighting +
     * shadow + dimension-overlay code path. The payload shape is
     * identical for both methods — just panel counts differ.
     */
    _rpcDispatch() {
        const rec = this.props.record;
        const model = rec?.resModel;
        if (model === "sale.order") {
            return {
                rpcModel: "sale.order",
                rpcMethod: "get_kitchen_3d_payload",
                recordId: rec?.resId || null,
            };
        }
        // Default — OCA product.configurator wizard.
        return {
            rpcModel: "product.config.session",
            rpcMethod: "get_3d_payload",
            recordId: this._sessionIdFromRecord(rec),
        };
    }

    /**
     * Produce a stable string key from record.data.value_ids that
     * changes iff the set of selected attribute values changes.
     *
     * Odoo 19 exposes x2many record data through several shapes
     * depending on the source view; we try the documented APIs in
     * order and fall back to empty-string when nothing matches
     * (which makes the early "no values yet" state stable).
     */
    _valueIdsKey(record) {
        if (!record || !record.data) return "";
        const field = record.data.value_ids;
        if (!field) return "";
        const ids =
            field.currentIds ||
            field.resIds ||
            field.ids ||
            (Array.isArray(field) ? field : null);
        if (!Array.isArray(ids)) return "";
        return ids.slice().sort((a, b) => a - b).join(",");
    }

    /**
     * T1C6 — analogous key for the sale.order kitchen-run view.
     * Tracks the set of order_line ids; a change means a line was
     * added/removed or its product changed.
     */
    _orderLinesKey(record) {
        if (!record || !record.data) return "";
        const field = record.data.order_line;
        if (!field) return "";
        const ids =
            field.currentIds ||
            field.resIds ||
            field.ids ||
            (Array.isArray(field) ? field : null);
        if (!Array.isArray(ids)) return "";
        return ids.slice().sort((a, b) => a - b).join(",");
    }

    /**
     * T1C6 — dispatch the "reactivity key" by record model so the
     * change detector tracks the right field set per surface.
     */
    _recordKey(record) {
        if (!record) return "";
        if (record.resModel === "sale.order") return this._orderLinesKey(record);
        return this._valueIdsKey(record);
    }

    /**
     * Decide whether the new record state warrants a re-fetch, and
     * schedule a debounced refresh if so. Called from onWillUpdateProps
     * on every record diff.
     *
     * T1C6: now dispatches by model. For product.configurator the
     * record id source is config_session_id; for sale.order it's
     * the record's own resId.
     */
    _reactToRecord(record) {
        const dispatch = this._rpcDispatchForRecord(record);
        const rid = dispatch.recordId;
        if (!rid) return;
        const key = this._recordKey(record);
        const ridChanged = rid !== this._lastSessionId;
        const keyChanged = key !== this._lastValueIdsKey;
        if (!ridChanged && !keyChanged) return;
        this._lastSessionId = rid;
        this._lastValueIdsKey = key;
        this._scheduleRefresh(rid);
    }

    /**
     * Same as _rpcDispatch() but parametrised by a record argument so
     * onWillUpdateProps can dispatch on the INCOMING record (not the
     * stale this.props.record).
     */
    _rpcDispatchForRecord(record) {
        const model = record?.resModel;
        if (model === "sale.order") {
            return {
                rpcModel: "sale.order",
                rpcMethod: "get_kitchen_3d_payload",
                recordId: record?.resId || null,
            };
        }
        return {
            rpcModel: "product.config.session",
            rpcMethod: "get_3d_payload",
            recordId: this._sessionIdFromRecord(record),
        };
    }

    /**
     * Debounce wrapper around _fetchAndRebuild. The user can step
     * through several attribute picks in <200ms; without this we'd
     * fire an RPC per pick.
     */
    _scheduleRefresh(sessionId) {
        if (this._refreshTimer) {
            clearTimeout(this._refreshTimer);
        }
        this._refreshTimer = setTimeout(() => {
            this._refreshTimer = null;
            this._fetchAndRebuild(sessionId);
        }, this._refreshDebounceMs);
    }

    // ------------------------------------------------------------------
    // Fetch + rebuild
    // ------------------------------------------------------------------

    async _fetchAndRebuild(recordId) {
        if (!recordId) {
            this._buildScene(null);
            return;
        }
        this._lastSessionId = recordId;
        this.state.loading = true;
        this.state.error = null;
        try {
            const dispatch = this._rpcDispatch();
            // Re-derive recordId in case props changed under us.
            const effectiveId = dispatch.recordId || recordId;
            const payload = await this.orm.call(
                dispatch.rpcModel,
                dispatch.rpcMethod,
                [[effectiveId]],
            );
            this._buildScene(payload);
            this.state.renderedAt = new Date().toLocaleTimeString();
        } catch (e) {
            this.state.error =
                e?.data?.message || e?.message || String(e);
        }
        this.state.loading = false;
    }

    _buildScene(payload) {
        const THREE = this._THREE;
        if (!THREE || !this._cabinetGroup) return;

        // Dispose existing geometries.
        while (this._cabinetGroup.children.length) {
            const child = this._cabinetGroup.children[0];
            this._cabinetGroup.remove(child);
            if (child.geometry) child.geometry.dispose();
        }

        if (!payload || !Array.isArray(payload.panels)) return;

        // T1C8 — refresh per-line index from payload metadata.
        // The kitchen-view payload includes a `lines` map keyed by
        // line id (string); the wizard payload omits it (no hover
        // tooltip needed when there's only one cabinet).
        this._linesIndex = (payload.metadata && payload.metadata.lines) || {};
        this._hoveredLineId = null;
        this.state.hoveredLineId = null;
        this.state.hoveredLineInfo = null;

        // Phase 3 (2026-06-25) — live price + dimension summary in the
        // toolbar. get_3d_payload embeds price + breakdown + currency;
        // format once here so OWL can just t-esc state.priceLabel.
        const meta = payload.metadata || {};
        const fmtPrice = (n) => {
            const symbol = meta.currency_symbol || "$";
            const before = meta.currency_position !== "after";
            const formatted = n.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            });
            return before ? `${symbol}${formatted}` : `${formatted} ${symbol}`;
        };
        if (typeof meta.price === "number" && !Number.isNaN(meta.price)) {
            this.state.priceLabel = fmtPrice(meta.price);
            // Tooltip: only show breakdown when there's a non-zero extra
            // (otherwise the tooltip would say "Base $545 = $545" which
            // adds nothing). Pricelist name appended if present —
            // critical UX disclosure for the dealer flow.
            const extras = meta.extras_sum;
            let tooltip;
            if (typeof extras === "number" && extras > 0
                && typeof meta.list_price === "number") {
                tooltip =
                    `Base ${fmtPrice(meta.list_price)} `
                    + `+ Options ${fmtPrice(extras)} `
                    + `= ${fmtPrice(meta.price)}`;
            } else {
                tooltip = "Configured price";
            }
            if (meta.pricelist_name) {
                tooltip += `\n(${meta.pricelist_name})`;
            }
            this.state.priceTooltip = tooltip;
        } else {
            this.state.priceLabel = null;
            this.state.priceTooltip = null;
        }
        // Dimension chip: W × H × D mm. These are integer mm, no need
        // for locale formatting. Skip if any axis is missing/zero
        // (worktop short-circuit, accessory).
        if (meta.width_mm > 0 && meta.height_mm > 0 && meta.depth_mm > 0) {
            this.state.dimsLabel =
                `${Math.round(meta.width_mm)} × `
                + `${Math.round(meta.height_mm)} × `
                + `${Math.round(meta.depth_mm)} mm`;
        } else {
            this.state.dimsLabel = null;
        }
        // Template SKU chip: shown only when the template carries a
        // default_code (every Q8 template does, every accessory variant
        // does as of A5).
        this.state.templateCode = meta.template_code || null;

        // Build each panel.
        for (const p of payload.panels) {
            const d = p.dims;
            if (!d || d.width <= 0 || d.height <= 0 || d.depth <= 0) continue;
            // 2026-06-24 Phase 2 Round 2 — shape dispatcher.
            // Defaults to box (back-compat with every panel emitted by
            // server-side code before today). Cylinder + sphere are
            // wrapped in try/catch + fall through to BoxGeometry so a
            // malformed shape hint never blanks the canvas.
            let geom;
            const shape = p.shape || "box";
            try {
                if (shape === "cylinder") {
                    // axis: "y" (default — vertical cylinder, length=height)
                    //       "x" (horizontal X — bar pull on a drawer)
                    //       "z" (horizontal Z — rare)
                    const axis = p.axis || "y";
                    const segments = 18;
                    let len, rad;
                    if (axis === "x") {
                        len = d.width;
                        rad = Math.min(d.height, d.depth) / 2;
                    } else if (axis === "z") {
                        len = d.depth;
                        rad = Math.min(d.width, d.height) / 2;
                    } else {
                        len = d.height;
                        rad = Math.min(d.width, d.depth) / 2;
                    }
                    geom = new THREE.CylinderGeometry(rad, rad, len, segments);
                    // CylinderGeometry's default axis is Y. Rotate to
                    // align with the requested axis at mesh-build time
                    // (below, via mesh.rotation).
                } else if (shape === "sphere") {
                    const radius = Math.min(d.width, d.height, d.depth) / 2;
                    geom = new THREE.SphereGeometry(radius, 24, 16);
                } else {
                    geom = new THREE.BoxGeometry(d.width, d.height, d.depth);
                }
            } catch (e) {
                // Defensive: malformed shape hint or vendored Three.js
                // missing the geometry class → fall back to box and
                // log once. Cabinet renders something, the user does
                // not see a blank canvas.
                // eslint-disable-next-line no-console
                console.warn(
                    "[CabinetViewport] geometry build failed for "
                    + "shape '" + shape + "'; falling back to box. Error:", e,
                );
                geom = new THREE.BoxGeometry(d.width, d.height, d.depth);
            }
            const matName =
                this.state.mode === "blueline" ? "blueline" : (p.material || "carcass");
            // Material lookup with family fallback:
            //   hardware_<finish>  → hardware → carcass
            //   door_<finish>      → door     → carcass
            let material = this._materials[matName];
            if (!material && matName.startsWith("hardware_")) {
                material = this._materials.hardware;
            }
            if (!material && matName.startsWith("door_")) {
                material = this._materials.door;
            }
            if (!material) material = this._materials.carcass;
            const mesh = new THREE.Mesh(geom, material);
            mesh.position.set(p.pos.x, p.pos.y, p.pos.z);
            if (p.rot) {
                mesh.rotation.set(p.rot.x || 0, p.rot.y || 0, p.rot.z || 0);
            } else if (shape === "cylinder" && p.axis === "x") {
                // Default cylinder axis is Y; rotate 90° around Z to lay
                // it along X. (No explicit rot needed for axis=y.)
                mesh.rotation.set(0, 0, Math.PI / 2);
            } else if (shape === "cylinder" && p.axis === "z") {
                // Rotate 90° around X to lay it along Z.
                mesh.rotation.set(Math.PI / 2, 0, 0);
            }
            // T1C4: every panel casts AND receives shadows. Cast =
            // floor shadow grounding. Receive = inter-panel shadows
            // (the door darkens the back panel through the cabinet
            // mouth; sides darken the shelves on the unlit side).
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            // T1C8 — extract line id from panel name prefix L{id}_.
            // Wizard payload panel names lack the prefix; the regex
            // simply doesn't match and lineId stays null (no hover).
            const m = (p.name || "").match(/^L(\d+)_/);
            if (m) {
                mesh.userData.lineId = m[1];
            }
            this._cabinetGroup.add(mesh);
        }

        // T1C5: rebuild dimension overlay for the new bounds.
        this._buildDimensionLines(payload);

        // Camera framing.
        if (payload.camera) {
            this._camera.position.set(...payload.camera.position);
            const tgt = payload.camera.target;
            if (this._controls) {
                this._controls.target.set(...tgt);
                this._controls.update();
            } else {
                this._camera.lookAt(new THREE.Vector3(...tgt));
            }
            // 2026-06-24 Phase 3 polish — stash the payload's framing
            // so onResetCamera can snap back. Stored as plain arrays
            // so a later config change overwrites cleanly.
            this._defaultCameraPosition = [...payload.camera.position];
            this._defaultCameraTarget = [...tgt];
        }
        // 2026-06-25 — stash payload bounds for preset camera angles.
        if (payload.bounds) {
            this._payloadBounds = {
                min: [...payload.bounds.min],
                max: [...payload.bounds.max],
            };
        }
    }

    // ------------------------------------------------------------------
    // Toolbar actions
    // ------------------------------------------------------------------

    onToggleMode() {
        this.state.mode = this.state.mode === "solid" ? "blueline" : "solid";
        if (!this._cabinetGroup) return;
        const blueline = this._materials.blueline;
        this._cabinetGroup.traverse((obj) => {
            if (!obj.isMesh) return;
            if (this.state.mode === "blueline") {
                obj._solidMaterial = obj.material;
                obj.material = blueline;
            } else if (obj._solidMaterial) {
                obj.material = obj._solidMaterial;
            }
        });
        // T1C5: show dimension lines only in blueline mode. The group
        // is rebuilt by _buildDimensionLines on every payload, so the
        // toggle is just a visibility flip — no rebuild needed.
        if (this._dimensionGroup) {
            this._dimensionGroup.visible = this.state.mode === "blueline";
        }
    }

    // 2026-06-24 Phase 3 polish — Reset Camera toolbar action.
    // Snaps back to the framing the last payload requested (the 3/4
    // view set in get_3d_payload). No-op until first payload lands;
    // OrbitControls drag positions reset cleanly.
    onResetCamera() {
        if (!this._defaultCameraPosition || !this._camera) return;
        this._camera.position.set(...this._defaultCameraPosition);
        if (this._controls && this._defaultCameraTarget) {
            this._controls.target.set(...this._defaultCameraTarget);
            this._controls.update();
        }
    }

    // 2026-06-25 — Preset camera angles derived from payload bounds.
    // Center of the cabinet on the floor footprint; distance scaled to
    // the cabinet's largest dimension so different families frame
    // consistently (a wall cabinet doesn't sit lost in a tall-cabinet
    // shot).
    _presetCamera(view) {
        if (!this._camera || !this._payloadBounds) return;
        const min = this._payloadBounds.min;
        const max = this._payloadBounds.max;
        const w = Math.max(1, max[0] - min[0]);
        const h = Math.max(1, max[1] - min[1]);
        const d = Math.max(1, max[2] - min[2]);
        // Target = center of the bounding box.
        const cx = (min[0] + max[0]) / 2;
        const cy = (min[1] + max[1]) / 2;
        const cz = (min[2] + max[2]) / 2;
        // Distance heuristic: ~2.5x the largest in-frame dim.
        const span = Math.max(w, h, d);
        let pos;
        if (view === "front") {
            pos = [cx, cy, cz + span * 2.5];
        } else if (view === "top") {
            // 2026-08-05 fix: [cx, cy + span*2.5, cz + 0.01] put the
            // camera 0.01mm off the up-axis from its target — the
            // degenerate OrbitControls/lookAt case where the view
            // direction is (numerically) parallel to camera.up. The
            // cross product OrbitControls/lookAt use to derive the
            // camera's "right" vector collapses toward zero length in
            // that case, corrupting the basis; every frame after
            // clicking Top rendered nothing until an unrelated full
            // refresh happened to reset the camera. A real oblique
            // z-offset (15% of the framing span) keeps the view
            // direction well clear of camera.up while still reading as
            // "top-down" — the same fix as nudging camera.up, but
            // without touching shared camera state other presets rely
            // on.
            pos = [cx, cy + span * 2.5, cz + span * 0.15];
        } else {
            // "three_quarter" — match the get_3d_payload default vibe.
            pos = [cx + w * 1.4, cy + h * 0.6, cz + d * 1.8];
        }
        this._camera.position.set(...pos);
        if (this._controls) {
            this._controls.target.set(cx, cy, cz);
            this._controls.update();
        } else {
            this._camera.lookAt(new THREE.Vector3(cx, cy, cz));
        }
    }

    onPresetFront() { this._presetCamera("front"); }
    onPresetThreeQuarter() { this._presetCamera("three_quarter"); }
    onPresetTop() { this._presetCamera("top"); }

    // 2026-06-25 — Snapshot. Renders one fresh frame then exports
    // the canvas as a downloadable PNG. Sales reps can grab a hero
    // shot of the current spec without taking an OS screenshot.
    // File name embeds the cabinet's dim summary if available (the
    // toolbar formats it; we reuse the same value).
    onDownloadSnapshot() {
        const canvas = this.canvasRef.el;
        if (!canvas || !this._renderer || !this._scene || !this._camera) return;
        try {
            // Force a fresh render — the animation loop may have left
            // a stale framebuffer if the page tab was throttled.
            this._renderer.render(this._scene, this._camera);
            const data = canvas.toDataURL("image/png");
            const dimsTag = (this.state.dimsLabel || "cabinet")
                .replace(/[^\w×x]/g, "_");
            const a = document.createElement("a");
            a.href = data;
            a.download = `southbrook_${dimsTag}.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (e) {
            // eslint-disable-next-line no-console
            console.warn(
                "[CabinetViewport] snapshot failed; canvas may be tainted "
                + "(cross-origin texture?) or the renderer is mid-mount.",
                e,
            );
        }
    }

    async onRefresh() {
        const dispatch = this._rpcDispatch();
        if (dispatch.recordId) await this._fetchAndRebuild(dispatch.recordId);
    }

    // ------------------------------------------------------------------
    // T1C8 — Per-line highlight on hover
    //
    // The kitchen viewport prefixes each panel name with `L{id}_` (set
    // in get_kitchen_3d_payload). On mousemove we raycast the canvas,
    // extract the line id from the topmost intersected mesh, and swap
    // every mesh sharing that line id to the highlight material.
    //
    // Toolbar shows the hovered line's sequence + family + SKU read
    // from payload.metadata.lines (built backend-side per line).
    // ------------------------------------------------------------------

    _onCanvasMouseMove(event) {
        const canvas = this.canvasRef.el;
        if (!canvas || !this._raycaster || !this._cabinetGroup || !this._camera) {
            return;
        }
        const rect = canvas.getBoundingClientRect();
        this._mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        this._mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        this._raycaster.setFromCamera(this._mouse, this._camera);
        const intersects = this._raycaster.intersectObjects(
            this._cabinetGroup.children, false,
        );

        if (intersects.length > 0) {
            const mesh = intersects[0].object;
            const lineId = mesh.userData?.lineId || null;
            this._setHoveredLine(lineId);
            canvas.style.cursor = lineId ? "pointer" : "default";
        } else {
            this._setHoveredLine(null);
            canvas.style.cursor = "default";
        }
    }

    // ------------------------------------------------------------------
    // T1C9 — Click-to-edit
    //
    // mousedown / mouseup pair distinguish a click from a drag (which
    // OrbitControls consumes for orbit/pan). When a clean click lands
    // on a cabinet, the OWL component calls
    // sale.order.line.action_reconfigure to launch the wizard for that
    // line's product.
    // ------------------------------------------------------------------

    _onCanvasMouseDown(event) {
        this._mouseDownAt = { x: event.clientX, y: event.clientY };
    }

    _onCanvasMouseUp(event) {
        const down = this._mouseDownAt;
        this._mouseDownAt = null;
        if (!down) return;
        const dx = event.clientX - down.x;
        const dy = event.clientY - down.y;
        if (Math.hypot(dx, dy) > this._MAX_CLICK_DELTA_PX) return;
        // Clean click — raycast the same canvas-relative position.
        this._handleCanvasClick(event);
    }

    async _handleCanvasClick(event) {
        const canvas = this.canvasRef.el;
        if (!canvas || !this._raycaster || !this._cabinetGroup || !this._camera) {
            return;
        }
        const rect = canvas.getBoundingClientRect();
        this._mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        this._mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        this._raycaster.setFromCamera(this._mouse, this._camera);
        const hits = this._raycaster.intersectObjects(
            this._cabinetGroup.children, false,
        );
        if (!hits.length) return;
        const lineId = hits[0].object.userData?.lineId;
        if (!lineId) return;

        await this._launchReconfigureForLine(lineId);
    }

    async _launchReconfigureForLine(lineId) {
        try {
            const action = await this.orm.call(
                "sale.order.line",
                "action_reconfigure",
                [[parseInt(lineId, 10)]],
            );
            if (!action || !action.type) return;

            // T1C10 — onClose hook. When the wizard modal closes
            // (Save, Cancel, X), refetch the kitchen payload so the
            // viewport reflects whatever the rep just changed. Also
            // reload the form's record so the sibling widgets
            // (order_line list, total, BoM preview tab) see the
            // updated product / variant / pricing.
            //
            // onClose fires on EVERY close path (Save AND Cancel).
            // The wasted refetch on Cancel is cheap and avoids the
            // race where a Save dialog isn't truly "committed" until
            // the modal teardown completes.
            const refreshAfter = async () => {
                try {
                    // 1) Form record reload — picks up new product +
                    //    config_session_id on the modified line. Lets
                    //    other widgets see the change without a manual
                    //    refresh.
                    if (this.props.record?.load) {
                        await this.props.record.load();
                    }
                    // 2) Kitchen payload refetch — new geometry +
                    //    line metadata. Hover tooltip will reflect
                    //    the new family / SKU / dims immediately.
                    const dispatch = this._rpcDispatch();
                    if (dispatch.recordId) {
                        await this._fetchAndRebuild(dispatch.recordId);
                    }
                } catch (e) {
                    this.state.error =
                        e?.data?.message || e?.message || String(e);
                }
            };
            this.action.doAction(action, { onClose: refreshAfter });
        } catch (e) {
            this.state.error = e?.data?.message || e?.message || String(e);
        }
    }

    _setHoveredLine(lineId) {
        if (lineId === this._hoveredLineId) return;

        // Restore the previously-hovered cabinet's original materials.
        if (this._hoveredLineId !== null && this._cabinetGroup) {
            this._cabinetGroup.traverse((obj) => {
                if (obj.isMesh
                    && obj.userData?.lineId === this._hoveredLineId
                    && obj.userData?._origMaterial) {
                    obj.material = obj.userData._origMaterial;
                    obj.userData._origMaterial = null;
                }
            });
        }

        this._hoveredLineId = lineId;
        this.state.hoveredLineId = lineId;

        // Apply highlight + populate the toolbar tooltip text.
        if (lineId !== null && this._cabinetGroup && this._materials.highlight) {
            const hl = this._materials.highlight;
            this._cabinetGroup.traverse((obj) => {
                if (obj.isMesh && obj.userData?.lineId === lineId) {
                    if (!obj.userData._origMaterial) {
                        obj.userData._origMaterial = obj.material;
                    }
                    obj.material = hl;
                }
            });
            const info = this._linesIndex[lineId];
            if (info) {
                const dims = `${Math.round(info.width_mm)}×${Math.round(info.height_mm)}×${Math.round(info.depth_mm)} mm`;
                this.state.hoveredLineInfo =
                    `#${info.sequence} · ${info.family} · ${info.sku || ""} · ${dims}`;
            } else {
                this.state.hoveredLineInfo = `Line ${lineId}`;
            }
        } else {
            this.state.hoveredLineInfo = null;
        }
    }

    // ------------------------------------------------------------------
    // T1C5 — Auto-dimension lines for blueline mode
    //
    // Per PRODBOARD_MANIFEST §3, the blueline overlay derives its
    // dimension lines PROGRAMMATICALLY from the cabinet's bounding box.
    // No hand-placed measurements. When the user picks a wider cabinet,
    // the dimension lines stretch + the label updates automatically.
    //
    // Phase 1 ships W / H / D outside dimensions only. Phase 3 polish
    // adds per-shelf-interval, per-door, per-toekick, and the snap-to-
    // run multi-cabinet horizontal/vertical dim chains.
    // ------------------------------------------------------------------

    _buildDimensionLines(payload) {
        const THREE = this._THREE;
        const group = this._dimensionGroup;
        if (!THREE || !group) return;

        // Clear previous dim geometry + sprites.
        this._clearDimensionGroup();

        if (!payload || !payload.bounds) return;

        const b = payload.bounds;
        const W = b.max[0] - b.min[0];
        const H = b.max[1] - b.min[1];
        const D = b.max[2] - b.min[2];

        const lineMat = new THREE.LineBasicMaterial({
            color: 0x2b4f6b,           // --sky from the palette
            transparent: true,
            opacity: 0.85,
        });
        this._dimensionMaterial = lineMat;

        const TICK = 25;               // tick-mark half-length, mm
        const OFFSET_W = 130;          // dim line offset from cabinet face, mm
        const OFFSET_H = 200;
        const OFFSET_D = 200;
        const LABEL_GAP = 90;          // label distance from dim line

        // ---- WIDTH — across X, in front of the cabinet (above floor),
        //      so it doesn't fight the shadow on the ground plane.
        const wYbar = -OFFSET_W;       // below the floor by OFFSET_W
        const wZbar = b.max[2] + 60;   // just in front of door plane
        const xL = b.min[0];
        const xR = b.max[0];
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(xL, wYbar, wZbar),
            new THREE.Vector3(xR, wYbar, wZbar));
        // Extension lines from cabinet corner to dim line.
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(xL, 0, b.max[2]),
            new THREE.Vector3(xL, wYbar - 15, wZbar));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(xR, 0, b.max[2]),
            new THREE.Vector3(xR, wYbar - 15, wZbar));
        // Tick marks at endpoints.
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(xL, wYbar - TICK, wZbar),
            new THREE.Vector3(xL, wYbar + TICK, wZbar));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(xR, wYbar - TICK, wZbar),
            new THREE.Vector3(xR, wYbar + TICK, wZbar));
        this._addLabelSprite(group,
            this._formatMm(W), 0, wYbar - LABEL_GAP, wZbar);

        // ---- HEIGHT — vertical, on the right side of the cabinet.
        const hX = b.max[0] + OFFSET_H;
        const hZmid = (b.min[2] + b.max[2]) / 2;
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(hX, 0, hZmid),
            new THREE.Vector3(hX, H, hZmid));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(b.max[0], 0, hZmid),
            new THREE.Vector3(hX + 15, 0, hZmid));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(b.max[0], H, hZmid),
            new THREE.Vector3(hX + 15, H, hZmid));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(hX - TICK, 0, hZmid),
            new THREE.Vector3(hX + TICK, 0, hZmid));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(hX - TICK, H, hZmid),
            new THREE.Vector3(hX + TICK, H, hZmid));
        this._addLabelSprite(group,
            this._formatMm(H), hX + LABEL_GAP, H / 2, hZmid);

        // ---- DEPTH — along Z, on the floor to the left of the cabinet.
        const dX = b.min[0] - OFFSET_D;
        const dY = 5;                  // just above floor so it isn't hidden
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(dX, dY, b.min[2]),
            new THREE.Vector3(dX, dY, b.max[2]));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(b.min[0], dY, b.min[2]),
            new THREE.Vector3(dX + 15, dY, b.min[2]));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(b.min[0], dY, b.max[2]),
            new THREE.Vector3(dX + 15, dY, b.max[2]));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(dX, dY, b.min[2] - TICK),
            new THREE.Vector3(dX, dY, b.min[2] + TICK));
        this._addLineSegment(group, lineMat,
            new THREE.Vector3(dX, dY, b.max[2] - TICK),
            new THREE.Vector3(dX, dY, b.max[2] + TICK));
        this._addLabelSprite(group,
            this._formatMm(D), dX - LABEL_GAP, dY, (b.min[2] + b.max[2]) / 2);
    }

    _addLineSegment(group, mat, a, b) {
        const THREE = this._THREE;
        const geom = new THREE.BufferGeometry().setFromPoints([a, b]);
        const line = new THREE.Line(geom, mat);
        // Dimension lines should never cast/receive shadows.
        line.castShadow = false;
        line.receiveShadow = false;
        // Always draw on top so labels aren't occluded by the cabinet.
        line.renderOrder = 999;
        group.add(line);
        return line;
    }

    _addLabelSprite(group, text, x, y, z) {
        const THREE = this._THREE;
        const dpr = Math.max(1, window.devicePixelRatio || 1);

        const W_PX = 320;
        const H_PX = 110;
        const canvas = document.createElement("canvas");
        canvas.width = W_PX * dpr;
        canvas.height = H_PX * dpr;
        const ctx = canvas.getContext("2d");
        ctx.scale(dpr, dpr);

        // Background pill — translucent paper so the label reads on
        // both the warm floor AND the cool cabinet behind it.
        ctx.font =
            "600 28px 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace";
        const padX = 16;
        const pillH = 56;
        const tw = ctx.measureText(text).width;
        const pillW = tw + padX * 2;
        const px = (W_PX - pillW) / 2;
        const py = (H_PX - pillH) / 2;
        ctx.fillStyle = "rgba(251, 247, 239, 0.92)";       // --paper
        const r = 6;
        ctx.beginPath();
        ctx.moveTo(px + r, py);
        ctx.arcTo(px + pillW, py, px + pillW, py + pillH, r);
        ctx.arcTo(px + pillW, py + pillH, px, py + pillH, r);
        ctx.arcTo(px, py + pillH, px, py, r);
        ctx.arcTo(px, py, px + pillW, py, r);
        ctx.closePath();
        ctx.fill();

        // Pill border — thin sky line.
        ctx.strokeStyle = "rgba(43, 79, 107, 0.4)";
        ctx.lineWidth = 1;
        ctx.stroke();

        // Text — sky on paper.
        ctx.fillStyle = "#2b4f6b";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(text, W_PX / 2, H_PX / 2);

        const texture = new THREE.CanvasTexture(canvas);
        if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
        const material = new THREE.SpriteMaterial({
            map: texture,
            transparent: true,
            depthTest: false,
        });
        const sprite = new THREE.Sprite(material);
        sprite.position.set(x, y, z);
        // Scale in world mm. Aspect-ratio matches the canvas
        // (320:110 → 2.91:1). Adjust 280 to taste — bigger = more
        // readable, smaller = less obtrusive.
        sprite.scale.set(280, 280 * (H_PX / W_PX), 1);
        sprite.renderOrder = 1000;
        group.add(sprite);

        this._spriteResources.push({ texture, material });
    }

    _clearDimensionGroup() {
        const group = this._dimensionGroup;
        if (!group) return;
        while (group.children.length) {
            const child = group.children[0];
            group.remove(child);
            if (child.geometry) child.geometry.dispose();
        }
        for (const r of this._spriteResources) {
            if (r.texture) r.texture.dispose();
            if (r.material) r.material.dispose();
        }
        this._spriteResources = [];
        if (this._dimensionMaterial) {
            this._dimensionMaterial.dispose();
            this._dimensionMaterial = null;
        }
    }

    _formatMm(mm) {
        const inches = mm / 25.4;
        // Round to nearest 0.25" — matches cabinet-industry granularity.
        const inchesRounded = Math.round(inches * 4) / 4;
        const inchesDisplay =
            Math.abs(inchesRounded) > 0.001
                ? `${inchesRounded.toFixed(2).replace(/\.?0+$/, "")}"`
                : "";
        return `${Math.round(mm)} mm${inchesDisplay ? ` · ${inchesDisplay}` : ""}`;
    }
}

registry.category("view_widgets").add("cabinet_viewport", {
    component: CabinetViewport,
    // fieldDependencies: list every record field the widget reads. Odoo's
    // form view uses this list to decide WHEN to re-render the widget.
    // Without value_ids here, the widget would miss attribute picks
    // (the m2m field updates on the record but the widget's prop diff
    //  never includes it). Track 1 commit 2 makes the widget reactive.
    fieldDependencies: [
        { name: "config_session_id", type: "many2one" },
        {
            name: "value_ids",
            type: "many2many",
            relation: "product.attribute.value",
        },
    ],
});

// T1C6 — second registration for the sale.order kitchen-run view.
// Same component class; different field deps (order_line one2many
// instead of value_ids many2many). The component dispatches by
// record.resModel internally so all the rendering / shadow /
// dimension code is shared.
registry.category("view_widgets").add("kitchen_viewport", {
    component: CabinetViewport,
    fieldDependencies: [
        {
            name: "order_line",
            type: "one2many",
            relation: "sale.order.line",
        },
    ],
});
