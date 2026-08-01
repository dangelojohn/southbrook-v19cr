/**
 * Southbrook Kitchen 3D Configurator — PR3a Node test-harness module loader.
 *
 * NOT a Hoot substitute for the browser. This is a Node "module.register()"
 * resolve hook that plays the same role Odoo's in-browser Hoot module
 * loader (web/static/tests/_framework/hoot_module_loader.js) plays for
 * bare Odoo module specifiers ("@odoo/owl", "@web/core/...",
 * "@southbrook_kitchen_3d_configurator/...") — it maps them to real files
 * on disk (the actual shipped source, unmodified) or to small boundary
 * shims (@web/core/* framework services + the KitchenCanvas mock), so the
 * REAL component/business-logic files can be imported and executed
 * head­lessly under Node + jsdom. See tests/node_js/README-in-comments
 * at the top of run_all.mjs for the full runnability rationale (PR3a
 * gate finding: no headless-Chrome binary in this environment — Hoot
 * itself cannot execute here).
 */
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NODE_JS_ROOT = path.resolve(HERE, "..");           // tests/node_js
const ADDON_ROOT = path.resolve(NODE_JS_ROOT, "..", ""); // tests/
const STATIC_SRC = path.resolve(ADDON_ROOT, "..", "static", "src");

const SHIMS = path.join(NODE_JS_ROOT, "shims");
const OWL_ESM = path.join(
    NODE_JS_ROOT, "node_modules", "@odoo", "owl", "dist", "owl.es.js",
);

// Bare-specifier -> absolute file path, forced to ESM ("module") format.
// Odoo's own web-framework modules (@web/core/*) have no Node equivalent
// package, so every one the configurator actually touches gets a small,
// literal, boundary shim (see tests/node_js/shims/*). The addon's own
// "@southbrook_kitchen_3d_configurator/*" alias is resolved generically
// below, matching what web.assets_backend does for real at runtime.
const EXACT = {
    "@odoo/owl": OWL_ESM,
    "@web/core/registry": path.join(SHIMS, "web_core_registry.mjs"),
    "@web/core/utils/hooks": path.join(SHIMS, "web_core_hooks.mjs"),
    "@web/core/network/rpc": path.join(SHIMS, "web_core_rpc.mjs"),
    "@web/core/user": path.join(SHIMS, "web_core_user.mjs"),
    // Task 35 — confirm-on-regenerate. See shims/web_core_confirmation_
    // dialog.mjs's own header for why a placeholder class is enough.
    "@web/core/confirmation_dialog/confirmation_dialog":
        path.join(SHIMS, "web_core_confirmation_dialog.mjs"),
    // Contracts #1/#2/#4/#5 mount the REAL parent configurator but must
    // not drag in the real <KitchenCanvas> (Three.js/WebGL/DOM canvas
    // boundary) — that's the one substitution point the PR3a brief
    // explicitly calls for ("mock only the canvas's THREE/scene/RPC
    // boundaries, not the logic under test"). Contracts #3/#6 import the
    // REAL kitchen_canvas.esm.js directly by relative path (bypassing
    // this alias entirely) to exercise the real _placeCabinetGroup +
    // builders — see contracts/03_renderer_coords.test.mjs.
    "@southbrook_kitchen_3d_configurator/js/canvas/kitchen_canvas.esm":
        path.join(NODE_JS_ROOT, "harness", "kitchen_canvas_mock.mjs"),
};

const ALIAS_PREFIX = "@southbrook_kitchen_3d_configurator/";

export async function resolve(specifier, context, nextResolve) {
    if (Object.prototype.hasOwnProperty.call(EXACT, specifier)) {
        return {
            url: pathToFileURL(EXACT[specifier]).href,
            format: "module",
            shortCircuit: true,
        };
    }
    if (specifier.startsWith(ALIAS_PREFIX)) {
        const rel = specifier.slice(ALIAS_PREFIX.length); // e.g. "js/canvas/constants.esm"
        const abs = path.join(STATIC_SRC, rel) + ".js";
        return {
            url: pathToFileURL(abs).href,
            format: "module",
            shortCircuit: true,
        };
    }
    return nextResolve(specifier, context);
}
