# Southbrook UI Foundation (`southbrook_ui`)

## What this is

A single Odoo 19 CE addon that ships ONE SCSS file:

```
static/src/scss/_sb_tokens.scss
```

It defines CSS custom properties (`--sb-*`) on `:root`, with a
`[data-theme="dark"]` (and OS `prefers-color-scheme: dark`) rebind:

- **A six-state status scale** — `ok`, `warn`, `late`, `blocked`,
  `info`, `neutral` — each with a foreground, a background tint, and a
  border token (`--sb-status-<state>-fg` / `-bg` / `-border`), computed
  to WCAG AA (4.5:1 text, 3:1 non-text) in both themes.
- **A spacing scale**, `--sb-space-0` … `--sb-space-8`.
- **A type scale** (`display` / `heading` / `body` / `label` /
  `caption` / `mono`) with size, weight, line-height, and family.
- **A radius scale** (`--sb-corner-*`) and **elevation scale**
  (`--sb-elevation-*`).
- **Back-compat aliases** for the five most-referenced legacy names —
  `--sb-ok`, `--sb-success`, `--sb-alert`, `--sb-danger`, `--sb-warn`
  — so existing SCSS that already writes `var(--sb-ok)` keeps
  resolving to a real, contrast-checked colour.

No models, no security, no data records — assets only.

## Why it exists

`scratchpad/retest/ux-designsystem.md` (the Phase 0 audit) found that
Southbrook's ~550 hardcoded colour literals, 63 font-size values and
32 spacing values are not the result of rejecting a design system —
a real token file already exists
(`southbrook_estimating/static/src/scss/_southbrook_design_tokens.scss`)
and is even fairly well thought through. The problem is that it is
only loaded by 2 of 24 SCSS-shipping addons, five more addons
reference `var(--sb-*, fallback)` with no declared dependency at all
(working today only because Odoo happens to merge every installed
addon into one global bundle), and the file itself contains two
unreconciled generations that disagree on what "ok" and "danger" mean.

The sharpest, safety-relevant version of this problem: a factory-floor
scheduler or kiosk reading "red" in one module and "red" in another
module may not mean the same thing, because there is no single,
declared definition of what "red" (blocked/late/danger) *is*.

`southbrook_ui` is Phase 0 of fixing that: one canonical,
contrast-checked, dependency-light token layer that any addon —
including shop-floor and catalog addons that must NOT pull in the
estimating/quoting engine — can adopt with a single line.

## How another addon adopts it

Add one line to `depends` in `__manifest__.py`:

```python
"depends": [
    "web",
    "southbrook_ui",
],
```

That's it — no asset entry needed in the consuming addon's own
manifest. `southbrook_ui` already contributes `_sb_tokens.scss` to
both `web.assets_backend` and `web.assets_frontend`, so the tokens are
available to backend views, kanban/list widgets, portal pages, and
public website pages alike, the moment the dependency is declared.

Then use the tokens directly:

```scss
.o_my_widget--late {
  color: var(--sb-status-late-fg);
  background: var(--sb-status-late-bg);
  border: 1px solid var(--sb-status-late-border);
  padding: var(--sb-space-2) var(--sb-space-3);
  border-radius: var(--sb-corner-sm);
}
```

## The rule

**No addon may hardcode a status colour.** "Status colour" means any
hex/rgb literal used to communicate ok/success, warn, late/overdue,
blocked/critical/error, informational, or neutral/unassessed meaning
— in a badge, pill, kanban card accent, dashboard tile, or similar.
If a new state is needed that doesn't map to one of the six semantics
above, raise it as a token-file change here, not as a new one-off
hex value in the consuming addon. The same discipline should extend
over time to spacing (`--sb-space-*`) and type (`--sb-type-*`) as
addons are touched, but the status-colour rule is the hard one: it is
the one place a disagreement is not just visually inconsistent but
factually misleading (two modules disagreeing about what "red" means
on a shop floor is a defect, not a style nit).

## What this addon deliberately does NOT do (yet)

- It does not edit `southbrook_estimating`'s existing token file. Both
  files can currently end up in the same asset bundle for addons that
  depend on both; see the "load order" note at the top of
  `_sb_tokens.scss` for what that means in practice today.
- It does not delete or migrate the `$sb-cfg-*`, `$sbk-*`, `--skj-*`,
  or `--mc-*` shadow namespaces the audit found in
  `southbrook_configurator_ux`, `southbrook_kitchen_3d_configurator`,
  `southbrook_premium_orchestration`, and `sb_materials_hardware`.
  Those addons' own `.scss`/`.js`/`.py` files were out of scope for
  this Phase-0 pass (a concurrent agent was editing SCSS/models in
  `southbrook_kitchen_3d_configurator` at the time this addon was
  built). Migrating them onto `southbrook_ui` is Phase 1+ work.
- It does not formalize the Button/Badge/Card/Table/Modal/Toast/
  EmptyState component vocabulary the audit recommends (§5 of the
  audit). This addon is tokens only, by design — components are a
  separate, larger piece of work that should build on a settled token
  layer, not ship alongside it.

See `scratchpad/retest/ux-designsystem.md` for the full audit and
`scratchpad/retest/p0-tokens.md` for the Phase-0 build note.
