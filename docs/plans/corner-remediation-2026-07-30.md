# Corner Cabinet Remediation — 2026-07-30

Source: John's directive to execute the corner-cabinet audit remediation on the live
kitchen configurator (southbrookcabinetry.space). The audit's tickets were verified
against code + prod DB first; several were refuted (notably "SBK-C1 pre-increment
cursor" — the geometry is the documented anchor-pose convention and is correct).
This plan implements only the CONFIRMED defects. Verification reports live in
`.superpowers/sdd/corner-verify/V1-geometry.md … V5-partc.md` — task briefs cite them;
implementers should read the named report sections, not the raw audit.

Prod facts (2026-07-30, live DB): estimating 19.0.9.1.0 / kitchen_3d 19.0.5.34.0 /
website 19.0.31.14.0; origin/main 478a708 == prod (md5-verified on key files).
Corner catalog: tmpl 44 "Corner Cabinet" (SB-CORNER LH var 206 $810 / SB-CORNER-R RH
var 1411 $810, is_cab=True ctype=corner, stub BOM 125 with 0 lines, 3 design lines +
5 SO lines in use); tmpl 222 SB-CORNER-LSUSAN / 223 SB-CORNER-BLIND / 224
SB-CORNER-DIAG / 236 SB-WALL-CORNER all is_cab=False, ctype=False, dims 24×34.5×24,
bom_count=0, variants carry stale "Width: 33 in"; 222/223/224 have ZERO design/SO
lines (safe to reshape), 236 has 2 design lines (design 102), 0 SO lines. All four
templates already carry a Hinge Side attribute (LH/RH values exist). Placement rules
seeded: susan→tmpl 44 (seq10), blind→223 (seq20), wall pie-cut→236 (seq10), 3
appliance clearance rules. Design 99 has 5 ACTIVE debris lines at x=1,000,000.

## Global Constraints

- The anchor-pose coordinate convention is LAW (COORDINATE_CONTRACT.md; engine
  `anchor_pose_mm`/`footprint_from_anchor_mm`). No task may change persisted
  geometry semantics or the engine's placement math.
- All catalog/data changes ship as idempotent code (seeder / corner_sku_repair /
  migration) — never ad-hoc DB writes. Safe to run twice; skip-if-missing; NEVER
  substitute a different product family for a missing SKU.
- Do NOT bump any `__manifest__.py` version — the controller does one bump per
  module at the end (asset cache-busting rides on it).
- Odoo 19 CE traps apply: no `_sql_constraints` (use model `_constraints` API…
  actually `models.Constraint`), `group_ids` not `groups_id`, stored computes that
  feed NOT NULL columns need `precompute=True`, `ir.cron` schema changes, no
  `rpc`/`user` legacy JS services. When in doubt read neighbouring code.
- Tests: pure-engine tests run with plain `python3`. Odoo tests run in a throwaway
  container on YOUR OWN fresh CI DB (never the shared `southbrook` dev DB — a
  concurrent session may be using it):
  `docker run --rm --network southbrook-v19cr_default -v /Users/naadmin/sb_corner/addons:/mnt/extra-addons -v /Users/naadmin/sb_corner/shared:/srv/shared -e PYTHONPATH=/srv/shared sami-odoo:dev bash -c "PGPASSWORD=change-me-strong-password psql -h db -U odoo -d postgres -c 'DROP DATABASE IF EXISTS <CIDB>' ; PGPASSWORD=change-me-strong-password createdb -h db -U odoo <CIDB> && odoo --db_host=db --db_user=odoo --db_password=change-me-strong-password -d <CIDB> -i southbrook_estimating,southbrook_kitchen_3d_configurator --test-enable --test-tags=<TAGS> --stop-after-init --http-port=<PORT> --max-cron-threads=0 --log-level=warn"`
  Pick a unique `<CIDB>` (e.g. ci_corner_t1) and a unique `<PORT>` (8981-8996).
  Exit code is MEANINGLESS — grep the log for `odoo.tests.result:` and for
  `Failed to load registry` / `CRITICAL` / `ERROR`. Diff failures against
  `.superpowers/sdd/corner-verify/baseline-tests.md` (pre-existing reds are listed
  there; do not chase them, do not add new ones).
- Commit style: `fix(kitchen-3d): <what and why in one line>` — match repo history.
  If `git commit` hits a transient `index.lock` (parallel lanes share the worktree),
  wait 2-5 s and retry.
- Do not modify prod. Do not touch designs 98-102 except via the shipped migration.
- Never edit files under `.superpowers/` other than your own report file.

## Task 1: Validator repair — collision, bounds, hand, message hygiene

Files: `addons/southbrook_kitchen_3d_configurator/models/kitchen_design.py`
(`_check_production_ready`), `addons/southbrook_kitchen_3d_configurator/tests/`.
Required reading: V2-validator.md (all), V1-geometry.md (convention + check #3 repro).

1. Rewrite check #3 `CABINET_COLLISION` (kitchen_design.py:1035-1058): group by
   (wall, zone) and compare real anchor footprints via the engine's
   `footprint_from_anchor_mm` (same approach the fixed check #7 uses), with a
   tolerance of 2 mm. Kill the "same x_position_in" heuristic. Left/right-wall
   pairs must stop false-positiving; genuine same-wall same-zone overlaps must
   still block (pin both in tests, using record-102-shaped data from
   V1-geometry.md).
2. Wire an out-of-bounds check: every ACTIVE line's footprint must lie within the
   room (engine `obb_within_room` / `within_room` — currently never called from the
   validator, V2 §4b). BLOCK with the line named. Must catch a line parked at
   x=1,000,000 (real prod debris, design 99).
3. Hand/junction check: for derived corner lines, derive the expected Hinge Side
   the same way the corner writer does (kitchen_design.py:639 region) and BLOCK on
   mismatch (V2 §4d: the silent `handed[:1] or variant` fallback has no backstop).
4. Message hygiene: every issue message gains a ` (line #<id>)` suffix for each
   involved line; corner-clearance messages (checks 12/13) make the CORNER the
   grammatical subject and name the intruding neighbour second (V2 §3).
5. Fix the tautology test `test_u_shape_template.py:69` — asserts
   `i.get("blocking")` but the real key is `severity` (V2 §1). Then add the pin:
   a freshly instantiated + arranged U-shape and L-shape template produces NO
   collision/bounds/hand blocks (customer + BOM issues are allowed at this task).
6. IMPORTANT scope note: checks 12/13 (motion envelope / corner filler) currently
   fire "[B24] sits inside the 19.7in clearance…" on the engine's OWN template
   output (live-reproduced on design 102). Diagnose why (envelope anchored into the
   adjoining run's legitimately-reserved cells vs a real engine reservation gap) and
   fix the SIDE THAT IS WRONG so the engine's own output validates clean; document
   the diagnosis in your report. If you conclude the ENGINE under-reserves (i.e.
   the clearance violation is physically real), STOP and report BLOCKED with the
   worked geometry — do not silently weaken a safety check.

AC: fresh L/U template designs validate with zero geometry/hand blocks; artificial
true overlap, out-of-room line, and wrong-hand corner each produce exactly one
targeted BLOCK naming the line; full kitchen_3d + estimating suites show no new
failures vs baseline.

## Task 2: BOM-gate coherence on cold validate

Files: same module as Task 1 (serialize after it).
Required reading: V2-validator.md §5.

The quotation path auto-seeds missing BOMs, but `action_validate_production` calls
the BOM check cold, so SB-WALL-CORNER (bom_count=0) hard-blocks every U design that
the quote flow would have handled (live-reproduced, design 102). Make the cold
validate use the same auto-seed awareness: a missing BOM that the quotation flow
would auto-seed reports as `[WARN] BOM will be auto-created at quotation` instead of
`[BLOCK]`; a product the seeding logic cannot handle stays `[BLOCK]`. Do not seed
BOMs as a side effect of validating. Test both branches.

AC: design 102-shaped data validates without a BOM block; a product genuinely
outside the auto-seed logic still blocks; no new suite failures.

## Task 3: Inch-exact placement-rule payloads

Files: `addons/southbrook_estimating/models/placement_rule.py` (+ its seed/upgrade
machinery), affected pure-engine test pins, `addons/southbrook_estimating/tests/`.
Required reading: V1-geometry.md (drift math), prod facts header above.

The seeded corner-rule payloads use round-metric mm for inch-native leg dimensions:
914.0 (→ should be 914.4 = 36"), 610.0 (→ 609.6 = 24"), 686.2 (→ 685.8 = 27"),
1372.0 (→ 1371.6 = 54"). 1143.0 (45") and 762.0 (30") are already exact. This is
the sole source of the 35.98/24.02 drift on every corner line AND a real 0.04"
cell overlap (wall corner leg 24.016" vs W24 run ending on the 24.0 grid,
live-reproduced on design 102: W2B ends 96.02, right pie-cut cell starts 95.98).
APPLIANCE clearance payloads (500/300/450/610/914 mm on rules with
corner_type_id=False) are metric spec values — leave them untouched; note the 610/914
CLEARANCE values on appliance rules are intentional metric and NOT the same thing as
the corner LEG values being fixed.

Ship via the seeder's payload-upgrade path (the `_SEED_PAYLOADS_LEGACY` exact-match
mechanism that already upgrades factory-state payloads in place and skips
hand-edited ones). Update any test pins that encode the old numbers. The engine
itself must not change.

AC: after upgrade, a regenerated U-shape's corner lines read exactly 36.00 / 24.00
width and land on the inch grid; no cross-zone 0.04" overlap; seeder idempotent
(second run is a no-op); hand-edited payload simulation is NOT overwritten; no new
suite failures.

## Task 4: Corner catalog truth — promote real SKUs, retire the orphan

Files: `addons/southbrook_estimating/models/corner_sku_repair.py` (+ the catalog
data files it works with), `addons/southbrook_estimating/tests/`.
Required reading: V3-products.md (claims 1/2/4), prod facts header above.

Extend the existing idempotent repair machinery (same style as the shipped
SB-CORNER 33→36 + RH-twin repair) to make the corner catalog truthful:

1. Tmpl SB-CORNER-BLIND: `southbrook_cabinet_type='corner'`,
   `southbrook_is_cabinet=True`, dims W45 × H34.5 × D24; variant Width attr
   33→45; materialize the RH (Hinge Side) twin variant.
2. Tmpl SB-CORNER-DIAG: same flags, dims 36 × 34.5 × 36; Width 33→36; RH twin.
3. Tmpl SB-WALL-CORNER: same flags, dims W24 × H30 × D24 (height is currently
   34.5 — base height on a wall cabinet); Width 33→24 if stale (verify — prod
   shows 'Width: 24 in' on the variant but the template attr line has 2 width
   values); RH twin.
4. Tmpl 44 (SB-CORNER family): rename "Corner Cabinet" → "Corner Base ·
   Lazy-Susan 36" (it IS the operative lazy-susan carcass — rule 1's
   corner_type_id is diagonal-corner-lazy-susan; keep default_codes unchanged;
   it has live SO lines so name-only).
5. Tmpl SB-CORNER-LSUSAN (222): archive (`active=False`) — it duplicates 44,
   has zero design/SO lines, and two active "lazy susan" rows would double-list
   in the panel. Log what was done.
6. Optional new seed rule for the diagonal corner ONLY if the existing seeder
   pattern makes it trivial; otherwise leave auto-pick as susan/blind and note
   that diagonal is a manual swap choice (Task 6 makes that possible).

Everything by default_code lookup, skip-if-missing, resolve-then-verify, no
substitution. All idempotent.

AC: after repair, panel "Corner" filter domain (`southbrook_cabinet_type='corner'`,
`southbrook_is_cabinet=True`) matches blind + diag + wall-corner + tmpl 44; each
has an RH variant; 222 archived; running the repair twice changes nothing; no new
suite failures.

## Task 5: One corner price-resolution path

Files: `addons/southbrook_kitchen_3d_configurator/models/kitchen_design.py`,
`kitchen_template.py`, the reconcile write site (V3 cites reconcile.py:233), tests.
Serialize after Task 2 (same file as Task 1/2).
Required reading: V3-products.md claim 3.

All corner price writes use `variant.lst_price or tmpl.list_price`, but the PATHS
resolve DIFFERENT variants: auto-arrange resolves by exact default_code
(kitchen_design.py:619-621 → attribute-laden variant, $810), while
`kitchen_template._resolve_slot`'s fallback rung (kitchen_template.py:181-189)
resolves template+width and takes `product_variant_id` (first variant, different
price_extra → $625 live). Extract ONE helper: resolve-corner-variant(junction/hand,
rule/sku) → product.product, used by auto-arrange, the template fallback,
`action_swap_product`, and the reconcile freeze; price = that variant's
`lst_price`. Log when the fallback rung fires.

AC: regenerating the same template twice yields byte-identical price_unit on every
line; the same junction priced via template path and via auto-arrange path is
identical; a unit test pins corner pricing; no new suite failures.

## Task 6: Hand-aware wall corners + swap gate

Files: same module as Task 5 (serialize after it), tests.
Required reading: V3-products.md claims 2/4, V2 §4d.

1. The corner writer's handed selection currently only materializes for the base
   SB-CORNER family; wall corners always get the LH variant and rely on rotation.
   With Task 4's RH twin available, select the RH SB-WALL-CORNER variant at right
   junctions using the same handed logic as the base path (keep the safe fallback,
   but now Task 1's validator backstops it).
2. `action_swap_product` (kitchen_design.py:2551-2563) takes a plain browse() with
   NO domain gate — while every panel endpoint gates on
   `southbrook_is_cabinet=True` (flagged "HIGH-1 money vector",
   controllers/main.py:420-471). Add the same gate to swap. For derived corner
   lines: only allow swapping to `southbrook_cabinet_type='corner'` products
   whose footprint fits the junction cell; re-derive hand on swap (or refuse with
   a clear message when the requested variant's hand contradicts the junction).

AC: right junctions produce RH wall corners; swap to a non-catalog product id is
refused; swapping a corner to blind/diag works and keeps geometry + correct hand;
hand-contradicting swap refused with actionable message; no new suite failures.

## Task 7: Corner counter everywhere

Files: `addons/southbrook_kitchen_3d_configurator/static/src/**/kitchen_configurator.js`
(count buckets at ~:1301-1302, 1396-1404), `models/kitchen_design.py`
(`_compute_totals` :392-400), backend form Summary group in
`views/kitchen_design_views.xml`. Serialize after Task 6 (same py file).
Required reading: V4-counters-templates.md claim 1.

Corner lines are 56% of a U-design's value and appear in NO user-facing count.
Add `corner_count` (server field mirroring base_count/wall_count) + a "Corner"
sidebar entry + backend form line. Do NOT change `total_cabinets` semantics
(its filler-exclusion is a deliberate shipped decision). Sidebar buckets must
satisfy base + wall + corner + fillers(+any other bucket) == header total ==
active line count; assert that in a test.

AC: U-shape design shows base 4 / wall 5 / corner 4 (102-shaped data) and the
identity above holds; no new suite failures.

## Task 8: Render corners as corners

Files: `addons/southbrook_kitchen_3d_configurator/static/src/**` renderer
(builders around `kitchen_canvas.esm.js::_placeCabinetGroup` — read
COORDINATE_CONTRACT.md first).
Required reading: V1-geometry.md §renderer; the builder code itself.

Corner lines currently render as plain rectangular boxes on their square cell —
visually indistinguishable from a misplaced slab (this is John's literal
complaint). Give `cabinet_type=='corner'` lines corner massing, WITHOUT touching
placement/anchor math (same THREE.Group transform; geometry built inside the
local frame, exact cell footprint preserved):
- susan/diag: clipped-corner prism — square cell with the inner-facing corner cut
  by a diagonal front face (classic diagonal-corner plan shape), door face on the
  diagonal.
- blind (rule host_leg): L-massing — full cell along the host leg, blind panel
  visible, door face on the open leg.
- wall corners: same treatment at wall-cabinet depth/height.
- distinct subtle material tint for corner units in 3D and top/plan view.
Choose corner style from the line's product default_code prefix (SB-CORNER-BLIND →
blind; SB-WALL-CORNER → pie-cut; SB-CORNER/others → susan/diag). Degrade safely to
the current box for unknown codes. Respect the OWL/asset-bundle traps (no new
templates without owl attr conventions; bundle must build).

AC: in the 3D scene and plan view a corner unit is visually distinguishable from a
rectangular cabinet at a glance; footprint edges still align exactly with
neighbouring runs; node/JS contract tests still pass; no console errors on load.

## Task 9: Debris repair migration + arrange invariant

Files: `addons/southbrook_kitchen_3d_configurator/migrations/` (+ a small model
guard), tests.
Required reading: prod facts header (design 99 debris).

Design 99 carries 5 ACTIVE lines at x=1,000,000 (an "unplaced" sentinel that
leaked as active). Ship a migration that archives (`active=False`) any design line
with `abs(x_position_in) >= 9999` or `abs(z_position_in) >= 9999`, logging design
ids + counts. Add a write-side guard: any code path that parks a line at the
sentinel must not leave it active (find the path that produced it — likely the
arrange/misfit flow — and close it; if the producer cannot be identified
statically, the migration + Task 1's bounds validator are the accepted net,
document that).

AC: migration archives exactly the debris class (in-bounds lines untouched);
idempotent; a regression test covers the guard or the report documents why the
producer is unreachable; no new suite failures.

## Task 10: Filler engine — no phantom on empty rooms, honest remainder

Files: `addons/southbrook_kitchen_3d_configurator/controllers/main.py` (filler /
remainder computation at :126-220), related client filler-panel state in
`static/src/**/kitchen_configurator.js`, tests.
Required reading: V5-partc.md §SBK-023 (it names which sub-claims were refuted —
do not "fix" the refuted ones: proration is correct; Est.-excludes-filler is by
design).

Root cause (V5): `remainder = rw - n*module_w` is computed unconditionally even
when `n=0` (empty/undersized room), which yields the phantom "[FP3] +$140 filler
on an empty 12-inch room" and a persisted $70 filler on a zero-cabinet design.
Fix: gate filler computation/creation/selection on `n > 0`; an empty design has
no filler line, no filler price, no pre-selected filler in the client. Also make
the remainder honest in the two directions V5 documents (scribe overstates;
post-filler-delete recompute understates) — remainder must equal run length minus
occupied length (corner cells included) for the active runs.

AC: 0-cabinet design shows $0.00 and no filler anywhere (client + persisted);
remainder test pins run-length identity; the four filler strategies place
correctly on a real room (existing behavior guarded); no new suite failures.

## Task 11: Variant width resolution on placement

Files: the ~6 placement call sites reading template `southbrook_width_in`
(V5 lists them; `product_template.py:32` is the field), wiring in the
variant-Width extraction that already exists in
`product_config_line.py::_extract_cabinet_inputs`; tests.
Required reading: V5-partc.md §SBK-024.

Placement ignores the variant's Width attribute — a "Width: 18 in" variant
places at the template's nominal width. Centralize: one helper
`placed_width_in(product)` = variant Width attribute if present, else template
`southbrook_width_in` (log when the fallback fires), used by all placement call
sites. Do NOT change corner-cell sizing (rule payloads own corner footprints).

AC: an 18-in variant of a multi-width template places at 18; a catalog-walk
regression test asserts placed width == attribute width for every variant that
has a Width attribute; fallback logged once per product; no new suite failures.

## Task 12: Room bounds, line cap, honest field errors

Files: `addons/southbrook_kitchen_3d_configurator/models/kitchen_design.py`
(constraints), `controllers/main.py` (save_design validation), client
`static/src/**/kitchen_configurator.js` (`_stretchWidth`, dimension fields).
Required reading: V5-partc.md §SBK-021/022.

- Server-side clamps: room width/depth 12-480 in, height 84-144 in — enforced in
  save_design (readable JSON error) AND as a model constraint (Odoo 19: use the
  current constraint API, not `_sql_constraints`).
- Cap engine-generated lines per design at 120 with an explicit
  `layout_warning` ("room too large to auto-fill") — no silent truncation.
- Fix `_stretchWidth`: the ±24 in buttons must target the ACTIVE wall's axis
  (back/front → width, left/right → depth); no wall selected → width (current
  behavior).
- Client: out-of-range dimension input shows inline validation text next to the
  field instead of silently no-oping.
- Do NOT add confirm dialogs or an undo stack (explicitly parked — John's
  regenerate-on-resize decision stands).

AC: width=99999 rejected server-side with a readable message and nothing
persisted; cap enforced with the warning surfaced to the client; ±24 affects the
active wall's axis; invalid field shows text; no new suite failures.

## Task 13: Display-unit snap (kill the 35.98/55.12 drift echoes)

Files: `addons/southbrook_estimating_website/controllers/main.py:2456-2461`
(unrounded `w * (1/25.4)`), the raw echo at
`kitchen_configurator.js:2604`; tests.
Required reading: V5-partc.md §SBK-025.

Only these two spots bypass the existing drift-safe display helpers (others
already snap to 1/4 in). Route them through the same snap. Storage stays as-is
(design lines are canonically float-inches; `southbrook.room.wall` integer-mm is
intentional). Note Task 3 removes the largest drift source at the data level;
this task cleans the display boundary.

AC: a 36-in cabinet reads 36.00 everywhere in the UI; wall-cabinet mount height
displays 55.0 not 55.12 (or the design's true inch value post-Task-3); no new
suite failures.
