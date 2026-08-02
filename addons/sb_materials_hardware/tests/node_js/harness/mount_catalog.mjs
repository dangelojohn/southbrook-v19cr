/**
 * The ONE canonical mount helper for MaterialsHardwareCatalog
 * (catalog.js), used by every contract test in this suite.
 *
 * Boots jsdom, loads the REAL unmodified component (registered into the
 * shimmed actions registry exactly as `registry.category("actions")
 * .add(...)` does at the bottom of catalog.js) together with the REAL
 * catalog.xml template source (passed as owl's `templates` config, the
 * same mechanism Odoo's own asset bundle uses to resolve
 * `static template = "sb_materials_hardware.Catalog"` by name), wires
 * the orm boundary to a per-test stub, and mounts it under owl's real
 * `mount()`, awaiting the full onWillStart lifecycle (the first load())
 * before returning — exactly like a real client action boot.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "./setup_dom.mjs";
import { registry } from "../shims/web_core_registry.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CATALOG_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "catalog", "catalog.js",
);
const CATALOG_XML = path.resolve(
    HERE, "..", "..", "..", "static", "src", "catalog", "catalog.xml",
);

let catalogModuleLoaded = false;

/**
 * @param {object} opts
 * @param {(model:string, method:string, args:any[], kwargs:object) => any} [opts.ormCall]
 *   Stub for `orm.call(...)` — every RPC the component makes (get_catalog,
 *   get_detail) goes through this one function.
 * @param {object} [opts.services] — extra/overriding env.services.
 */
export async function mountCatalog({ ormCall = () => ({ ok: true }), services = {} } = {}) {
    const dom = await setupDom();

    const owl = await import("@odoo/owl");

    if (!catalogModuleLoaded) {
        await import(CATALOG_SRC);
        catalogModuleLoaded = true;
    }
    const Comp = registry.category("actions").get("sb_materials_hardware.catalog");
    const templates = fs.readFileSync(CATALOG_XML, "utf8");

    const ormCalls = [];
    const defaultServices = {
        orm: {
            call(model, method, args, kwargs) {
                ormCalls.push({ model, method, args, kwargs });
                return Promise.resolve(ormCall(model, method, args, kwargs));
            },
        },
    };
    const env = { services: { ...defaultServices, ...services } };

    const root = dom.window.document.getElementById("root");
    const component = await owl.mount(Comp, root, { props: {}, env, templates });

    return {
        dom, root, component, owl,
        ormCalls,
        destroy: () => {
            const app = component && component.__owl__ && component.__owl__.app;
            if (app) app.destroy();
        },
    };
}
