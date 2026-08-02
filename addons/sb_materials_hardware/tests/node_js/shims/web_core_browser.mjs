/**
 * Boundary shim for "@web/core/browser/browser". catalog.js only uses
 * `browser.setTimeout`/`browser.clearTimeout` for the search debounce —
 * the real module is a thin wrapper over `window.setTimeout`/
 * `window.clearTimeout` for testability, which is exactly what this
 * reproduces against the jsdom globals setup_dom.mjs installs.
 */
export const browser = {
    setTimeout: (...args) => globalThis.setTimeout(...args),
    clearTimeout: (...args) => globalThis.clearTimeout(...args),
};
