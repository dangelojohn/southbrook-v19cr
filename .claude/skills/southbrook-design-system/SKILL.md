---
name: southbrook-design-system
description: Use when writing or changing ANY UI in the Southbrook stack — SCSS, OWL components, QWeb views, dashboards, status colours, user-facing copy, or Odoo field labels/help text. Carries the canonical token layer, the status-colour contract, and the copy standard.
---

# Southbrook design system

The stack had ~550 distinct colours across 17,565 lines of SCSS — one new
colour every ~32 lines — with **11+ greens for "ok"**, 8+ reds for
"danger/late", and 6+ ambers for "warn". A canonical token file existed and
only 2 of 24 SCSS-shipping addons loaded it. Phase 0 (2026-07-31) fixed the
cause. This skill exists so it does not regress.

## The rule

**Never hardcode a status colour.** Use the tokens from `southbrook_ui`.

```scss
color:        var(--sb-status-late-fg);
background:   var(--sb-status-late-bg);
border-color: var(--sb-status-late-border);
```

Six semantics, each with `-fg` / `-bg` / `-border`, light and dark:

| Token | Means |
|---|---|
| `--sb-status-ok-*` | success, complete, on-track |
| `--sb-status-warn-*` | needs attention soon, not yet overdue |
| `--sb-status-late-*` | overdue — escalated past warn, not a hard stop |
| `--sb-status-blocked-*` | hard stop, critical, rule violation |
| `--sb-status-info-*` | neutral informational |
| `--sb-status-neutral-*` | no status / unassessed |

`late` and `blocked` are deliberately distinct. On a factory floor "running
late" and "cannot proceed" demand different responses, and the codebase used
to render both as an arbitrary red.

Also available: `--sb-space-0..8` (0/4/8/12/16/24/32/48/64px), the type scale
(display/heading/body/label/caption/mono), `--sb-corner-*`, `--sb-elevation-*`.

Legacy `--sb-ok`, `--sb-success`, `--sb-alert`, `--sb-danger`, `--sb-warn`
still resolve — they alias the canonical tokens. Don't add new uses.

## Adopting the tokens in an addon

Add `"southbrook_ui"` to `depends` in `__manifest__.py`. That is the whole
step. It depends on `web` only, ships no models, and is safe for shop-floor
and catalog addons.

Do **not** rely on tokens resolving without declaring the dependency. Five
addons previously did, and worked only because Odoo merges every installed
addon into one global bundle — an accident of install topology, not a
contract.

## Enforcement

```bash
python3 scripts/lint-design-tokens.py        # fails on NEW violations only
python3 scripts/lint-design-tokens.py --all  # includes the 90 baselined
```

The baseline (`scripts/.design-tokens-baseline.txt`) grandfathers existing
debt so the gate stays enabled. Ratchet it down when you clean a file; never
add to it to silence a new violation.

## Contrast is non-negotiable

Text ≥ 4.5:1, graphical/border ≥ 3:1, in **both** themes. Compute it — do not
eyeball it:

```
linearize: c/12.92 if c <= 0.04045 else ((c+0.055)/1.055)**2.4
L = 0.2126R + 0.7152G + 0.0722B
ratio = (Lhi + 0.05) / (Llo + 0.05)
```

Two shipped legacy values had already failed AA before anyone measured.

## Copy is design material, not decoration

Odoo `help=` text, field labels, validator messages and toasts **are** the UI.
A 2026-07-31 review found 12 HIGH defects in this stack's copy alone.

- Write from the user's side of the screen. Name things by what the person
  controls, never by how the system is built. No `run_seq`, `layout_role`,
  `template_slot_code`, raw enum values (`base_run`), model names, or
  `key=value` dumps of a placement dict.
- Errors say what went wrong **and** what to do next. Never apologise, never
  vague.
- **Verify any control you name.** A message told users to "re-run
  auto-arrange"; no such control exists — the button reads "Generate Layout".
  Grep the view XML and `static/src/` for the real label before referencing it.
- One vocabulary. "Wall" meant both a room boundary and a wall cabinet,
  sometimes in one sentence. A room boundary is "the back/left/right wall";
  a unit is a "wall cabinet".
- Watch fallback precedence: `code or name` never falls through when `code` is
  always set, so the internal slug wins permanently. This shipped twice.
- Don't let a field restate the field next to it.

## Test copy by intent, not prose

Assert the issue **code**, severity, and `[BLOCK]`/`[WARN]` prefix. Exact
sentence assertions break on every rewrite and train people to avoid improving
copy. Useful assertion: no user-facing message contains `run_seq`,
`layout_role`, `template_slot_code`, `base_run`, `rot=`, `x=`, `z=`.

## Persona constraints

- **Shop floor** — gloved hands, glare, wall tablets. ≥44px touch targets
  (56px is the established size in `operator_pin_modal.scss`). Never
  hover-only. Every scan needs visible confirmation; a silent success is a
  failure.
- **Planner** — must reach a decision, not just a number. Exceptions outrank
  normals visually; every problem links to the record that fixes it.
- **Sales rep** — a blocked option must explain *why* (CLAUDE.md §5); reasons
  are visible to reps and hidden from customers.
- **Customer** — no internal vocabulary, ever. No "Order Builder", no BOM
  warnings, no SKU codes, no raw Odoo state codes (`Submitted (sale)`), no
  raw JS errors.

## Cabinet iconography — one source (owner decision, 2026-07-31)

`southbrook_kitchen_3d_configurator/static/src/js/canvas/cabinet_thumb.esm.js`
is THE cabinet icon language: axonometric SVG, neutral carcass
(#f2f4f7/#dadee4/#b4bbc4/#c8ced6, stroke #7d858e), doors/drawer fronts in
walnut #C9762B, blind-corner return leg darkened #9ea6b0. Wherever a surface
refers to a cabinet — lists, counters, previews, swap pickers — reuse
`cabinetThumbSVG(product, sizePx)` (synthetic `{cabinet_type, width_in}`
literals are fine for family-level icons). Never hand-draw a second variant:
one source means the icon can never disagree with what gets placed. Family
inference lives in ONE seam (`thumbFamily`) pending explicit footprint fields
on the product record.

## Before you claim a UI change works

Odoo caches asset bundles. Verify the shipped bytes, not the source:

```bash
grep -c 'yourSymbol' /var/lib/odoo/filestore/<db>/<xx>/<hash>
```

Code-only review misses rendered reality — a screenshot is worth 1000 tokens.

Full audit evidence and phasing:
`docs/superpowers/specs/2026-07-31-ux-uplift-design.md`.
