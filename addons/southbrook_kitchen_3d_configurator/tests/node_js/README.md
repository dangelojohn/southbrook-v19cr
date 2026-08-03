# PR3a — `southbrook_kitchen_3d_configurator` regression suite (Node, not Hoot)

## Run it

```sh
cd addons/southbrook_kitchen_3d_configurator/tests/node_js
npm ci
npm test
```

Prereqs: Node >= 20.6 (uses the stable `node:module` `register()` hook and
the built-in `node:test` runner; developed/CI'd against Node 22). No
browser, no Chromium, no Docker, no Odoo/Postgres needed — this suite is
fully independent of the `docker compose` stack `make test` brings up.
`npm ci` installs two pinned dev dependencies (`@odoo/owl@2.8.2` — pinned
to match the vendored `web/static/lib/owl/owl.js` version in the local
`sami-odoo` image — and `jsdom`) from `package-lock.json`.

If you skip `npm ci` (or `node_modules` is stale/absent for any other
reason), `npm test`'s `pretest` script (`harness/check_deps.mjs`) catches
it up front and fails with the exact command to run, instead of the
suite running partway in and failing 38 tests deep on a missing `jsdom`
with no pointer back to the fix — which is exactly what used to happen
here before this check existed.

Wired into CI: `.forgejo/workflows/tests.yml`, job
`kitchen-3d-configurator-node-tests` (runs on every push/PR to `main` and
`feature/**`/`fix/**`/`chore/**`, in a plain `node:22-slim` container,
independent of the `southbrook-tests` Odoo-stack job).

## Flake audit (2026-08-03) — the suite is now deterministic, with one known exception

The suite used to be flaky: three back-to-back runs against identical,
pristine code produced 1, 3, and 1 failures. A 30-run baseline (full
`npm test`, this box) nailed down exactly which:

| test | file:line | seen failing |
|---|---|---|
| `URL-restore fallback is model-gated: ...` | `contracts/02_persistence_reload.test.mjs:109` | 30/30 (deterministic) |
| `active-wall indicator reflects state.activeWall after a pick` | `contracts/04_active_wall_indicator.test.mjs:28` | 1/30 |
| `_toggleControlsPanel flips ...` / `_toggleInventoryPanel flips ...` | `contracts/31_panel_collapse_responsive.test.mjs:100,119` | 2/30 |
| `a sticky (blocking/...) toast gets role="alert"` / `the refuse-by-default over-length-add toast ...` | `contracts/32_toast_and_panel_a11y.test.mjs:51,73` | 4/30 |
| `detail drawer: 'Availability' reads 'Made to order' ...` | `contracts/23_made_to_order_availability.test.mjs:106` | 2/30 |
| `toolbar: undo/redo buttons enable/disable correctly ...` | `contracts/33_undo_redo.test.mjs:537` | not caught in the 30-run baseline itself, but confirmed same root cause (same tick()-then-assert shape); previously reported to fail once under full-suite load |

Root cause for every intermittent one: `harness/tick.mjs`'s old
`tick()` was a flat `setTimeout(resolve, 10)` racing Owl's own
multi-hop, batched render-then-DOM-patch cycle (see `tick.mjs`'s doc
comment for the exact mechanism). `node --test` forks one OS process
per test file — confirmed live via `ps` during a run: 7 concurrent
`node` workers on this 8-core box, each pegged near 65-70% CPU — and
under that genuine cross-process CPU contention, Owl's render
occasionally didn't finish patching the DOM within the fixed 10ms
budget, so the assertion read stale, pre-patch DOM. Fixed by replacing
the fixed wait with a double-`requestAnimationFrame` wait, which is an
ordering guarantee (works no matter how many internal microtask hops
Owl's batching needs, and no matter how long the OS suspends the
process for) rather than a timing guess. Post-fix: 23 consecutive full
`npm test` runs, same single deterministic failure every time, no
others — see `tick.mjs` for the full account, including a first attempt
at this fix (manually counting microtask hops) that made things worse
(20/505 deterministic failures) before the double-rAF fix landed.

**The one still-failing test is a confirmed PRODUCT bug, not a test
bug, and is NOT fixed here** (out of scope: production code,
`static/src/js/kitchen_configurator.js`). The bug is in the URL-restore
fallback in `setup()` (`kitchen_configurator.js` lines ~350-364, the
"FIX 6" comment block):
its `restoredActionFrame` lookup matches ANY actionStack frame whose
`action` equals the configurator's own tag, without checking that the
stack is actually the model-less numeric-`action-<id>` URL shape the
surrounding comment says justifies skipping the model check. Since the
ordinary two-frame tag-string shape (used for a "bare" reload of ANY
record — a room, a sale order, or a real design) ends in that exact
same `{action: ownTag, active_id: N}` shape, "FIX 6" ends up re-adopting
an `active_id` the FIRST branch (`restoredDesignFrame`, correctly
model-gated) had already and deliberately rejected — e.g. a bare
room-reload's room id gets silently treated as a design id. Proposed
fix: require that no frame in the stack carries a `model` key at all
before "FIX 6" runs (that absence is the actual structural signature of
the numeric-`action-<id>` shape it was written for):

```diff
-        if (restoredDesignId === null && Array.isArray(restoredStack)) {
+        if (restoredDesignId === null && Array.isArray(restoredStack)
+            && !restoredStack.some((s) => s && "model" in s)) {
             const ownTag = (this.props.action && this.props.action.tag)
```

No existing test covers the genuine numeric-`action-<id>` 3-frame shape
`[{action:1460}, {action:1460, resId:6}, {active_id:6, action:tag}]`
this fallback was actually written for — worth adding once the fix
lands, as a regression guard that the model-key guard above doesn't
also break the case it's meant to keep working.

## Why Node, not Hoot

Odoo 19's own JS unit-test framework (Hoot, `web/static/lib/hoot/`) drives
a real headless-Chrome process (`odoo/tests/common.py::ChromeBrowser`,
via the `websocket-client` CDP bridge). This environment has **no
headless-Chromium binary**: checked `which chromium chromium-browser
google-chrome` (empty) and `apt-cache policy chromium` inside the
`sami-odoo` container — on this image's Ubuntu 24.04 base, `chromium` is
a **snap-only transitional package** (`apt-get install -s chromium` ->
`E: Unable to locate package chromium` after `apt-get update`; no
`snapd` present). `odoo.tools.which.find_in_path('google-chrome')`
raises `FileNotFoundError`. Hoot literally cannot execute here — no
container in the local stack ships a usable browser, and installing one
isn't possible via the OS package manager in this environment.

**The fallback that *is* runnable and verifiable here:** this suite
mounts the REAL, unmodified `SouthbrookKitchenConfigurator` OWL
component (`static/src/js/kitchen_configurator.js`) under jsdom, using
the real `@odoo/owl` npm package (pinned to 2.8.2, matching the vendored
build) — proven end-to-end with a spike before any test was written
(mount a trivial component, read back rendered DOM text). Odoo's own
`@web/core/*` modules aren't real npm packages, so
`harness/loader.mjs` (a `node:module` `register()` resolve hook) maps
those bare specifiers to small boundary shims in `shims/` — playing the
same role Odoo's in-browser Hoot module loader
(`web/static/tests/_framework/hoot_module_loader.js`) plays for bare
Odoo specifiers, just for Node instead of the browser. Every
`"@southbrook_kitchen_3d_configurator/..."` specifier resolves to the
REAL file on disk, **except** `.../canvas/kitchen_canvas.esm.js`, which
resolves to `harness/kitchen_canvas_mock.mjs` — the one substitution
point the PR3a brief calls for ("mock only the canvas's THREE/scene/RPC
boundaries, not the logic under test"). Contracts #3 and #6 (renderer
coordinate math + the golden snapshot) import the REAL
`kitchen_canvas.esm.js` directly by relative path instead, bypassing
that redirect, and call its real `_placeCabinetGroup` /
`_sbSceneSnapshot` prototype methods against the REAL vendored r160
Three.js (`southbrook_estimating/static/lib/three/three.min.js`,
loaded via `require()` — it's a UMD/CJS-compatible build). No WebGL
context is needed anywhere: `THREE.Group`/`Mesh`/`Scene`/matrixWorld
decomposition is pure CPU matrix math.

This is a genuine alternative *runner*, not Hoot-format tests that
happen not to run: there is no `@odoo/hoot` import anywhere in this
directory, and every test uses `node:test` + `node:assert/strict`.
Because of that, the manifest was **not** given a `web.assets_unit_tests`
bundle entry — there is nothing Hoot-discoverable to bundle, and adding
one would ship unverifiable dead weight.

## Layout

```
tests/node_js/
  harness/
    loader.mjs               — the node:module resolve hook (the aliasing layer)
    register.mjs              — `node --import`-able bootstrap that registers the loader
    setup_dom.mjs              — jsdom bootstrap + attaches the real vendored THREE to window.THREE
    mount_configurator.mjs     — the ONE canonical mount helper for the real parent component
    kitchen_canvas_mock.mjs    — the <KitchenCanvas> mock (captures every prop, incl. callbacks)
    tick.mjs                   — await-a-render-frame helper (owl batches onto rAF)
  shims/
    web_core_registry.mjs      — registry.category(...).add/get — real actionRegistry lookup
    web_core_hooks.mjs         — useService(name) -> useComponent().env.services[name]
    web_core_rpc.mjs           — the RPC boundary: per-route handlers + a call log
    web_core_user.mjs          — the `user` singleton (isInternalUser)
  contracts/
    01_wall_selection.test.mjs         — PR1: _onWallSelect, KitchenCanvas prop wiring, _wallLabel
    02_persistence_reload.test.mjs     — PR2/2.5/2.5a: hydrate-from-design, fail-loud, param intake, actionStack fallback
    03_renderer_coords.test.mjs        — PR3.0/3.1: _placeCabinetGroup, wall-cabinet __localFrame complement
    04_active_wall_indicator.test.mjs  — PR1: the rendered "Active Wall: X" DOM text
    05_callback_contract.test.mjs      — every KitchenCanvas -> parent callback prop
    06_golden_design83.test.mjs        — PR3.1 golden byte-identity snapshot (see below)
```

## The golden snapshot (contract #6) is a real build gate, not a manual artifact

`06_golden_design83.test.mjs` rebuilds Design 83's 8 cabinets through the
real `buildBaseCabinet`/`buildWallCabinet` builders and the real
`_placeCabinetGroup`, snapshots them exactly like
`KitchenCanvas._sbSceneSnapshot()` does, and asserts the result
`deepEqual`s `tests/golden/design83.snapshot.json`'s `rows` (the
byte-identity baseline captured live in Chrome at PR3.1 deploy time,
hash `3591767287`, mesh_count 48). A mismatch fails the test with a
non-zero exit code — verified by deliberately perturbing one coordinate
in the golden file, confirming the suite goes red (`deepStrictEqual`
failure, `EXIT_CODE=1`), then reverting (`git diff` on
`tests/golden/` comes back empty).

**Honesty note on the 8 reconstructed items:** `design_id=83` does not
exist in any local/clone database reachable from this environment
(checked the local `southbrook` dev DB — 0 rows), and this suite must
never touch the QNAP production DB. The 8 items in the test file were
not read from a database; they were algebraically recovered from the
golden `rows` themselves by solving each builder's own position
formulas backwards until every one of the 48 rows matched uniquely (see
the in-file comment for the worked derivation). The regression-guard
value is unaffected either way: if the real placement/builder code ever
changes this output, the test fails.

The stored `hash` (3591767287) was produced by an ad-hoc snippet run
live in a browser console at capture time — its algorithm isn't
recorded in the shipped codebase, and common 32-bit string hashes
(Java `String.hashCode`, FNV-1a, djb2, sdbm) over the rows don't
reproduce it, so this suite gates on `rows` deep-equality directly
(strictly stronger than reproducing one 32-bit integer) rather than
re-deriving that number.
