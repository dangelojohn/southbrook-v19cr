/**
 * sb_materials_hardware — Node test-harness module loader.
 *
 * Node `module.register()` resolve hook that maps bare Odoo module
 * specifiers ("@odoo/owl", "@web/core/...") to real files on disk (the
 * actual shipped catalog.js/catalog.xml, unmodified) or to small
 * boundary shims, so the REAL component can be imported and executed
 * headlessly under Node + jsdom. Mirrors
 * southbrook_kitchen_3d_configurator/tests/node_js/harness/loader.mjs.
 */
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NODE_JS_ROOT = path.resolve(HERE, "..");   // tests/node_js

const SHIMS = path.join(NODE_JS_ROOT, "shims");
const OWL_ESM = path.join(
    NODE_JS_ROOT, "node_modules", "@odoo", "owl", "dist", "owl.es.js",
);

const EXACT = {
    "@odoo/owl": OWL_ESM,
    "@web/core/registry": path.join(SHIMS, "web_core_registry.mjs"),
    "@web/core/utils/hooks": path.join(SHIMS, "web_core_hooks.mjs"),
    "@web/core/browser/browser": path.join(SHIMS, "web_core_browser.mjs"),
};

export async function resolve(specifier, context, nextResolve) {
    if (Object.prototype.hasOwnProperty.call(EXACT, specifier)) {
        return {
            url: pathToFileURL(EXACT[specifier]).href,
            format: "module",
            shortCircuit: true,
        };
    }
    return nextResolve(specifier, context);
}
