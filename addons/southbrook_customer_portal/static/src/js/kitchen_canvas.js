// SPDX-License-Identifier: LGPL-3.0-only
//
// KitchenCanvas — Module 8 Phase 2.
//
// Customer-facing Three.js scene that renders the Configuration Engine
// output (Module 7) live in the browser. Geometry comes from the
// browser port of shared/southbrook_dims (the same module Module 4's
// cutlist generator + the FreeCAD bridge render against — drift between
// rendered and manufactured geometry is impossible by construction).
//
// We deliberately do NOT serve STEP files to the customer (init-doc
// D-FC-06 — the customer preview shares only dimensional geometry, not
// the manufacturing CAD). The scene composes BoxGeometry per panel
// from the dim formulas; nothing is downloaded except a placement JSON.

// `three` is resolved by the importmap in
// addons/southbrook_customer_portal/views/kitchen_portal_templates.xml
// to /southbrook_customer_portal/static/lib/three/three.module.min.js.
// OrbitControls.js itself does `import { ... } from 'three'`, so the
// same importmap makes that work too.
import * as THREE from "three";
import { OrbitControls } from "../lib/three/OrbitControls.js";

import {
  BOX_TH, BACK_TH, DOOR_TH, side, top, bottom, back, door,
} from "./kitchen_dims.js";

// ---------------------------------------------------------------------------
// One carcass mesh per cabinet — Group containing the 5–7 panels.
// ---------------------------------------------------------------------------
function buildCarcass(cabinetSpec, materials) {
  const group = new THREE.Group();
  const W = cabinetSpec.width_mm;
  const H = cabinetSpec.height_mm;
  const D = cabinetSpec.depth_mm;
  const doorCount = cabinetSpec.door_count ?? 1;

  // Sides
  const sideDim = side(H, D);
  const sideGeom = new THREE.BoxGeometry(sideDim[2], sideDim[0], sideDim[1]);
  const sideL = new THREE.Mesh(sideGeom, materials.carcass);
  sideL.position.set(BOX_TH / 2, H / 2, D / 2);
  const sideR = new THREE.Mesh(sideGeom, materials.carcass);
  sideR.position.set(W - BOX_TH / 2, H / 2, D / 2);
  group.add(sideL, sideR);

  // Top and bottom (length × width × thickness — length runs along X)
  const topDim = top(W, D);
  const topGeom = new THREE.BoxGeometry(topDim[0], topDim[2], topDim[1]);
  const topMesh = new THREE.Mesh(topGeom, materials.carcass);
  topMesh.position.set(W / 2, H - BOX_TH / 2, D / 2);
  const bottomMesh = new THREE.Mesh(topGeom, materials.carcass);
  bottomMesh.position.set(W / 2, BOX_TH / 2, D / 2);
  group.add(topMesh, bottomMesh);

  // Back
  const backDim = back(W, H);
  const backGeom = new THREE.BoxGeometry(backDim[0], backDim[1], backDim[2]);
  const backMesh = new THREE.Mesh(backGeom, materials.back);
  backMesh.position.set(W / 2, H / 2, D - BACK_TH / 2);
  group.add(backMesh);

  // Doors (1 or 2)
  const doorDim = door(W, H, doorCount);
  if (doorDim) {
    const doorWidth = doorDim[1];
    const doorHeight = doorDim[0];
    const doorGeom = new THREE.BoxGeometry(doorWidth, doorHeight, DOOR_TH);
    if (doorCount === 1) {
      const d = new THREE.Mesh(doorGeom, materials.door);
      d.position.set(W / 2, H / 2, -DOOR_TH / 2);
      group.add(d);
    } else if (doorCount === 2) {
      const dl = new THREE.Mesh(doorGeom, materials.door);
      dl.position.set(W / 4 + 1.5, H / 2, -DOOR_TH / 2);
      const dr = new THREE.Mesh(doorGeom, materials.door);
      dr.position.set(3 * W / 4 - 1.5, H / 2, -DOOR_TH / 2);
      group.add(dl, dr);
    }
  }

  return group;
}

// ---------------------------------------------------------------------------
// Scene
// ---------------------------------------------------------------------------
export function mountKitchenCanvas(container, placementData) {
  const width = container.clientWidth;
  const height = Math.max(400, Math.round(width * 0.55));

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf5f1e8);  // Linen

  const camera = new THREE.PerspectiveCamera(40, width / height, 100, 50000);
  camera.position.set(3500, 2500, 4500);
  camera.lookAt(0, 800, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);

  // Lighting
  scene.add(new THREE.HemisphereLight(0xffffff, 0xe0d5b0, 0.7));
  const sun = new THREE.DirectionalLight(0xfff4d6, 1.1);
  sun.position.set(4000, 6000, 3000);
  scene.add(sun);
  for (const offset of [[3000, 1000, 2000], [-3000, 1000, 2000],
                          [2500, 800, -3000], [-2500, 800, -3000]]) {
    const p = new THREE.PointLight(0xffeac2, 0.2, 8000);
    p.position.set(...offset);
    scene.add(p);
  }

  // Materials — Signature/Walnut palette
  const materials = {
    carcass: new THREE.MeshStandardMaterial({
      color: 0xfaf6ee, roughness: 0.75, metalness: 0.02,
    }),
    back: new THREE.MeshStandardMaterial({
      color: 0xc9b896, roughness: 0.9, metalness: 0.0,
    }),
    door: new THREE.MeshStandardMaterial({
      color: 0x6b4a2b, roughness: 0.55, metalness: 0.05,
    }),
    floor: new THREE.MeshStandardMaterial({
      color: 0xd6c8a8, roughness: 0.92, metalness: 0.0,
    }),
  };

  // Floor
  const floorGeo = new THREE.PlaneGeometry(20000, 20000);
  const floor = new THREE.Mesh(floorGeo, materials.floor);
  floor.rotation.x = -Math.PI / 2;
  scene.add(floor);

  // Cabinets + appliance slots from placement
  if (placementData && Array.isArray(placementData.runs)) {
    for (const run of placementData.runs) {
      const runGroup = new THREE.Group();
      let cursor = 0;
      for (const item of run.cabinets || []) {
        if (item.type === "filler") {
          cursor += item.width_mm || 0;
          continue;
        }
        const carcass = buildCarcass(item, materials);
        carcass.position.x = cursor;
        runGroup.add(carcass);
        cursor += item.width_mm || 0;
      }
      // Appliance placeholders (grey boxes)
      for (const slot of run.appliance_slots || []) {
        const w = slot.width_mm || 600;
        const h = (slot.kind === "fridge" || slot.kind === "oven_wall" ||
                    slot.kind === "tall") ? 1800 : 900;
        const d = 600;
        const g = new THREE.BoxGeometry(w, h, d);
        const m = new THREE.MeshStandardMaterial({
          color: 0x8a8a8a, roughness: 0.4, metalness: 0.6,
        });
        const mesh = new THREE.Mesh(g, m);
        mesh.position.set(slot.x_offset_mm + w / 2, h / 2, d / 2);
        runGroup.add(mesh);
      }
      runGroup.position.set(run.anchor_x_mm || 0, 0, run.anchor_y_mm || 0);
      scene.add(runGroup);
    }
  } else {
    // Placeholder: a single demo base cabinet so the empty state shows
    // something rather than a bare floor.
    const demoSpec = { width_mm: 600, height_mm: 720, depth_mm: 580,
                       door_count: 2 };
    scene.add(buildCarcass(demoSpec, materials));
  }

  // Orbit controls
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 800, 0);
  controls.minDistance = 1500;
  controls.maxDistance = 12000;
  controls.update();

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  // Resize handling
  function onResize() {
    const w = container.clientWidth;
    const h = Math.max(400, Math.round(w * 0.55));
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }
  window.addEventListener("resize", onResize);

  return { dispose() {
    window.removeEventListener("resize", onResize);
    renderer.dispose();
  }};
}

// Auto-mount: find every <div class="o_kitchen_canvas" data-placement="…">
// on page load and mount a canvas into it.
document.addEventListener("DOMContentLoaded", () => {
  for (const container of document.querySelectorAll(".o_kitchen_canvas")) {
    const raw = container.getAttribute("data-placement");
    let placement = null;
    if (raw) {
      try { placement = JSON.parse(raw); } catch (_) { /* empty */ }
    }
    try {
      mountKitchenCanvas(container, placement);
    } catch (err) {
      // 2026-08-02 UX Phase 2 vocab gate — this used to interpolate the
      // raw JS Error's string form (e.g. "TypeError: Cannot read
      // properties of undefined (reading 'x')") straight into innerHTML,
      // on the concept-approval page — the single highest-trust moment
      // in the flow, right before a customer commits to a design
      // (ux-customer.md audit §5/§7 #4). Log the real error for us;
      // show the customer plain language via textContent, never raw
      // error text or unescaped HTML.
      console.error("3D preview failed to load:", err);
      container.textContent = "";
      const notice = document.createElement("div");
      notice.className = "alert alert-warning";
      notice.textContent =
        "The 3D preview didn't load. Try refreshing the page — your design is saved.";
      container.appendChild(notice);
    }
  }
});
