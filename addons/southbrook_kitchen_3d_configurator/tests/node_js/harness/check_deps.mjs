/**
 * `npm test`'s `pretest` hook (see package.json) — npm runs any
 * `pretest` script automatically before `test`, so this fires on every
 * plain `npm test` with no extra step for a developer to remember.
 *
 * This exists because it already bit once: `node_modules` here had
 * never been installed, and nothing in the repo surfaced that fact —
 * `npm test` just ran straight into 38 failures with a missing-`jsdom`
 * error buried in the middle of a 505-test run, with no pointer back to
 * the fix. `package.json` + `package-lock.json` both existed the whole
 * time, so `npm ci` was always the correct (and only) fix — it just
 * wasn't wired into anything. This is that wiring: fail loud, first,
 * with the exact command, instead of failing deep and unexplained.
 *
 * Deliberately a RESOLVE check (`import.meta.resolve`), not an actual
 * `import()`: `@odoo/owl`'s ESM build reads DOM globals (`Element`,
 * `Node`, ...) at module-EVALUATION time (see harness/setup_dom.mjs's
 * own doc comment), so actually importing it here — before any test
 * has run setupDom() — throws "Element is not defined" even when the
 * package is installed and perfectly fine. `import.meta.resolve()`
 * only answers "does this specifier resolve to a file on disk", with
 * no evaluation, so it can't be confused by that.
 */
const REQUIRED = ["jsdom", "@odoo/owl"];

const missing = [];
for (const name of REQUIRED) {
    try {
        import.meta.resolve(name);
    } catch {
        missing.push(name);
    }
}

if (missing.length) {
    console.error(
        `\nMissing pinned dev ${missing.length === 1 ? "dependency" : "dependencies"}: ${missing.join(", ")}.\n` +
        "package-lock.json exists for exactly this reason — from tests/node_js/, run:\n\n" +
        "    npm ci\n\n" +
        "(see README.md's \"Run it\" section), then re-run npm test.\n",
    );
    process.exit(1);
}
