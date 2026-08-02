/**
 * Process-wide bootstrap: registers harness/loader.mjs as a Node module
 * customization hook (node:module `register()`, stable API) so every
 * bare Odoo-style specifier ("@odoo/owl", "@web/core/...") resolves for
 * the whole test run, including inside catalog.js's own import
 * statements. Load via `node --import ./harness/register.mjs --test ...`.
 * Mirrors southbrook_kitchen_3d_configurator/tests/node_js/harness/register.mjs.
 */
import { register } from "node:module";

register("./loader.mjs", import.meta.url);
