/**
 * Minimal jsdom bootstrap for mounting the REAL OWL MaterialsHardwareCatalog
 * headlessly in Node (no browser/Chromium available in this environment —
 * same rationale as southbrook_kitchen_3d_configurator/tests/node_js).
 *
 * Must run (and be awaited) BEFORE any dynamic `import("@odoo/owl")` or
 * import of source files that transitively import it — Owl's ESM build
 * reads `Element`/`Node`/etc. at module-evaluation time, not lazily.
 */
import { JSDOM } from "jsdom";

export async function setupDom() {
    const dom = new JSDOM(
        "<!doctype html><html><body><div id='root'></div></body></html>",
        { url: "http://localhost/odoo/action-sb_materials_hardware.catalog" },
    );
    dom.window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
    dom.window.cancelAnimationFrame = (id) => clearTimeout(id);

    global.window = dom.window;
    global.document = dom.window.document;
    for (const key of Object.getOwnPropertyNames(dom.window)) {
        if (key in global) continue;
        try { global[key] = dom.window[key]; } catch { /* getter-only, skip */ }
    }
    global.requestAnimationFrame = dom.window.requestAnimationFrame;
    global.cancelAnimationFrame = dom.window.cancelAnimationFrame;

    return dom;
}
