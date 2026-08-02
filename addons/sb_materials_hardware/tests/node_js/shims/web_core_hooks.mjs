/**
 * Boundary shim for "@web/core/utils/hooks". Mirrors the real
 * `useService(name)`'s actual mechanism (web/static/src/core/utils/
 * hooks.js): read `useComponent().env.services[name]`. Using the REAL
 * `useComponent` from owl keeps this faithful rather than reinventing
 * hook plumbing; only the service *lookup source* (env.services, set up
 * per-test by harness/mount_catalog.mjs) is a boundary the harness
 * controls.
 */
import { useComponent } from "@odoo/owl";

export function useService(name) {
    const component = useComponent();
    const services = (component.env && component.env.services) || {};
    if (!(name in services)) {
        throw new Error(
            `[hooks shim] service "${name}" not provided in env.services — ` +
            `pass it via mountCatalog({ services: { ${name}: ... } }).`
        );
    }
    return services[name];
}
