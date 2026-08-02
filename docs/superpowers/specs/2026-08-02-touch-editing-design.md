# Touch editing for the 3D kitchen configurator — design specification

**Date:** 2026-08-02 · **Status:** approved decision-by-decision with John
(four multiple-choice rounds + two deep-review addenda); this document is the
consolidated spec for final sign-off.
**Method:** five research streams (13-app touch-UX pattern catalog; pointer/
raycast/gesture engineering recipes; a file:line constraints audit of our
input pipeline; a live teardown of Prodboard-as-shipped; mining of the repo's
PRODBOARD_MANIFEST) plus three follow-up deep reviews John directed
(manipulation visual language, wall-run rotation semantics, scope addendum).
Research artifacts: `~/Downloads/Southbrook Reseach/UX_Uplift_And_Retest_
2026-07-31/evidence/investigations/touch-*.md, prodboard-*.md,
manipulation-visual-spec.md, wallrun-rotation-review.md,
touch-scope-addendum.md`.

---

## 1 · The locked decisions

| # | Question | Decision |
|---|---|---|
| 1 | Interaction model | **Tap-select + on-screen control pad**, augmented by constrained long-press direct drag. Independently validated: a published IKEA Place case study proposes nudge controls as the fat-finger fix. |
| 2 | Lock model | **Soft lock** — the selection is the only manipulable object; every other cabinet dims ~30% but remains tappable for instant reselection. (Prodboard evidence killed the *hard* lock: neighbour-relative ops — swap places, fill gap — need reachable neighbours.) |
| 3 | Rotation | **Scoped affordances, honest glyphs.** Free rotation (45° snaps) on freestanding/island items only; corners get a flip-hand arrow driving the existing hand-aware swap; wall-run cabinets get **camera-to-elevation** (camera icon) and **send-to-wall** (cycle icon) instead — both S-effort, both riding shipped, tested paths. No affordance ever fakes being a rotation. |
| 4 | Scope | **Portal-first, tablets-everywhere.** Portal 3D tab: full touch on phones + tablets. Backend configurator: touch on tablets; the 767px gate stays (verified a product-parity decision, commit `5748ca79`, not a technical wall). Phone = bottom-docked pad in the thumb zone; tablet = floating pad near the selection. |

## 2 · Gesture grammar

- **Two fingers are always camera** (orbit/pinch) — never object. Satisfies
  WCAG 2.5.1 and the cross-app convention.
- **One finger from empty space** = orbit.
- **Tap on a cabinet** = select (soft lock engages).
- **One-finger drag on the *selected* cabinet** = constrained direct drag
  (selection-gated manipulation — the arbitration policy three streams
  independently converged on).
- **Long-press (500ms) on the selected cabinet** = pickup with ghost preview
  (the one "model A" technique adopted wholesale).
- Drag maths: raycast against a math `THREE.Plane`, convert to local space,
  project onto the run axis (1-DOF along-wall), snap in that 1D space.
  Optimistic transform during drag; commit-and-reconcile on release —
  matching the server-authoritative multi-wall relayout.
- The arbitration layer is a **pure `(state, event) → state` reducer** with
  hit/timer results injected as events — node-contract-tested, parameterized
  per surface (addendum item 5), registered at `pointerdown` **capture
  phase** to win against OrbitControls' native listener (constraints audit
  risk #1).
- Engineering constants: 8-10px slop; `touch-action: none` scoped to the
  canvas only; invisible enlarged proxy meshes for 44-48px-equivalent hit
  targets (Three.js raycasts `visible=false` meshes — free hit inflation);
  raycast/snap once per rAF tick.

## 3 · Visual language (measured, not styled)

Keep the shipped **blue `#1866D4`** — every Prodboard-style amber tested
fails the 3:1 graphical minimum against our warm palette (1.17–1.88:1);
blue clears 3.11–4.46:1 on every scene surface and is already the converged
accent across ~8 call sites. Nine states specified in
`manipulation-visual-spec.md`; headlines:

- **Selected:** blue tint/outline as shipped + 0.25in lift (180ms) + a
  35%-opacity blue base pad anchoring the affordance buttons.
- **Dimmed others:** `mixHex(orig, sceneBg, 0.30)` — never desaturate-to-grey
  ("disabled-broken"); silhouettes intact; tappable.
- **Drag ghost:** shipped blue ghost (0.35) + faint `#7d858e` wireframe left
  at origin; rollback 200ms.
- **Collision:** NEW scene constant `#A6281E` (both `--sb-status-blocked-*`
  tokens fail contrast on the floor — measured); commit is server-gated,
  diverging deliberately from Prodboard's allow-and-flag.
- **Snap:** blue guide line + 220ms pulse; `navigator.vibrate(10)` as
  Android-only garnish — iOS has no JS haptics in 2026, so the visual pulse
  is the universal channel.
- **Precision readouts** (Prodboard's signature, adopted): DOM chips with
  distance-to-neighbour/wall, `--sb-status-info` text, separated by
  `--sb-elevation-2` shadow (token borders measured <2:1 there).
- Affordance buttons: ≥44px hit, blue-on-white chips, honest glyphs.

## 4 · Rollout controls (mined from the rejected options)

1. Ship **portal phase first**, backend-tablet second — staged inside A.
2. **Per-surface kill switch** via `ir.config_parameter`, modeled on the one
   existing client-consumed precedent (`qr_scan.py:906-925`
   `pin_modal_enabled`) — touch can be disabled without a deploy.
3. Minimal health signals riding **existing round-trips** (autosave payload
   gains gesture-rollback/abandonment counters) — no new telemetry stack.
4. Touch listeners register behind a runtime `(pointer: coarse)` /
   `maxTouchPoints` check (no dynamic-import precedent in this codebase).
5. One shared reducer contract suite, parameterized per surface, pinning the
   reducer/mutator boundary and the shared sizing standard; pad anatomy stays
   free per surface.
6. Roadmap note only: a phone-capable backend later is cheaper than it looks
   (reuses the portal gesture code verbatim) — actionable only if the
   SBK-019 gate is separately re-authorized.

## 5 · Riding along (fixes the design surfaced)

- **Gate Q/E rotation on wall-run cabinets** — today it writes `rotation_deg`
  ungated (`kitchen_configurator.js:3210-3215, 3381-3408`), invisible only
  because mesh rotation isn't rendered. Touch v1 gates it to the same scoped
  rule.
- **Z-axis drag branch for left/right walls** — the mouse drag path only
  writes `x_position_in`; the constrained-drag maths fixes this for mouse and
  touch alike.
- **Wire send-to-wall** — `action_move(wall, run_seq)`
  (`kitchen_design.py:4963`) is server-complete and tested with zero client
  wiring; the pad exposes it for the first time.
- Invariants preserved verbatim: F14 (preview ≠ selected), F7 (activeWall
  raycast), F8 (live `userData.item` rebind), autosave two-call contract,
  multi-wall packRow skip.

## 6 · Out of scope

Flip-hinge for non-corner cabinets (attribute exists; mirrored variants
mostly unmaterialized — needs a catalog audit first); door-open preview (no
animation machinery, blocked behind rendered rotation); any change to the
customer CTA/auth posture; lifting the backend 767px gate.

## 7 · Acceptance

- Reducer contract suite green for both surface parameterizations; all
  existing node contracts (349+) and Odoo baselines unchanged.
- On a real phone (portal): select, nudge, long-press drag with snap, rotate
  an island, flip a corner, camera-to-elevation, send-to-wall, undo — all
  without pinch/orbit ever moving a cabinet.
- On a tablet (backend): same grammar, floating pad.
- Kill switch verified: parameter off → mouse behaviour byte-identical to
  today.
- Bundle gate + served-symbol verification on deploy, per standing practice.
