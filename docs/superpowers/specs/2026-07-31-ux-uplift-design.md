# UX/UI uplift — operators and manufacturers first, then outside customers

**Date:** 2026-07-31
**Status:** approved (phasing approved by John 2026-07-31)
**Method:** six parallel read-only audits (shop floor, production planning, sales
operator, support operators, customer-facing, design-system coherence), each
evidence-based against source with file:line citations.

---

## 1 · The finding

The problem across every persona is **not polish — it is incoherence**. Three
independent audits converged on the same shape:

| Layer | Evidence |
|---|---|
| Visual system | A canonical token file exists (`southbrook_estimating/static/src/scss/_southbrook_design_tokens.scss`, 643 lines, documented in `docs/CUSTOMER_TO_MANUFACTURING_FLOW.md` §5) but **only 2 of 24** SCSS-shipping addons load it. ~550 distinct colours across 17,565 lines (one new colour every ~32 lines). **11+ greens** for "ok", 8+ reds for "danger/late", 6+ ambers for "warn". No spacing scale exists at all. Buttons reimplemented in 15 addons, badges in 12, cards in 11. |
| Planner surfaces | **Four** competing "what should I work on" queues (Ready Queue, Ready to Release, Blocked, Today's Plan) whose own code comments admit each was built to resolve the previous one's confusion — none retired. **Three** unconnected factory-health scores. Three literal menu-sequence collisions. |
| Customer surfaces | **Two** parallel customer flows under `/my/` (Order Builder vs Kitchen Projects) with separate information architecture and branding. |

Fixing individual screens without fixing this adds a 25th dialect.

A second, structural finding: **the design system has already forked from its own
spec.** `CUSTOMER_TO_MANUFACTURING_FLOW.md` §5.1 documents `--sb-ok:#2f8a4f`;
the shipped file says `#4a6f3e`. Neither matches the eleven greens in the wild.
The smallest possible design system — one file, three tokens — drifted.

## 2 · Worst defect per persona

- **Shop floor operator.** The Floor Traveler — the physical paper carried to the
  machine — closes its scan loop in total silence: no confirmation, no error, no
  screen. A complete PIN modal with correct 56px touch targets exists but is in no
  asset bundle, so no scan is attributed to an operator. Only `southbrook_qr_kit`
  has genuine operator-touch UI; the modules named for the shop floor serve
  planners.
- **Production planner.** Asks "what is late, what is blocked, where is the
  bottleneck" and receives four disagreeing queues and three disagreeing health
  scores. The bottleneck panel is non-interactive text. A "Late" tile is
  permanently red by the SCSS's own admission.
- **Sales operator.** `southbrook_hard_validation_issues()` runs **only** inside
  `action_confirm()`, so a rep completes an entire multi-line quote before
  discovering a rule violation — and blocked options never explain themselves.
  Both contradict the project's own brief (CLAUDE.md §2.2 mandates an inline
  config drawer; §5 requires the rule reason visible to reps, hidden from
  customers).
- **Outside customer.** The homepage's only CTA ("Design Your Kitchen") lands on
  a page titled, breadcrumbed and headed **"Order Builder"** — the internal name
  for the sales tool — where a manufacturing warning ("No BOM defined — this item
  can't be manufactured") can be shown to a homeowner, and the submit button can
  render the raw Odoo state code as **"Submitted (sale)"**.

## 3 · Phasing

**Phase 0 — enforce the system that already exists.** Cheapest phase; every later
change either compounds or fights it. Deliberately *enforce*, not rebuild: the
audit's recommendation was to consolidate the existing token file rather than
introduce a third generation.

1. New dependency-light addon `southbrook_ui` (depends on `web` only, assets
   only, no models) owning the canonical layer, so shop-floor and catalog addons
   can adopt it without pulling in the estimating engine.
2. ONE status scale — `ok / warn / late / blocked / info / neutral`, each with
   fg/bg/border, light and dark. Every value WCAG-computed (4.5:1 text, 3:1
   graphical) with the ratio recorded beside it. Divergent status colour is
   safety-relevant: the same red must mean the same thing in every module.
3. The missing spacing and type scales.
4. Legacy names (`--sb-ok`, `--sb-success`, `--sb-alert`, `--sb-danger`,
   `--sb-warn`) aliased, never broken — migration, not a flag day.
5. The five addons consuming `var(--sb-*)` with no declared dependency
   (`southbrook_room_chat`, `southbrook_kitchen_3d_configurator`,
   `southbrook_room_capture`, `southbrook_mrp_pm`, `southbrook_configurator_ux`)
   get a real `depends`. They currently render correctly only by accident of
   Odoo merging all installed addons into one global bundle.
6. `scripts/lint-design-tokens.py` with a baseline, so the existing violations
   are recorded and no *new* one lands. A gate that fails on day one gets
   disabled; a gate that holds the line gets kept.
7. Persist the system as a `southbrook-design-system` skill — skills outlive the
   session and propagate to every subagent in a fan-out, which is the only way
   this stops being re-improvised.

**Phase 1 — operators and manufacturers.** Traveler scan confirmation; load the
dead PIN modal; touch targets. Collapse the four queues into one canonical
Today's Plan; make the bottleneck actionable; retire the permanently-red tile;
reconcile the three health scores. Inline per-line validation and visible block
reasons for reps (closes the two CLAUDE.md gaps).

**Phase 2 — outside customers.** A customer-mode vocabulary gate (no "Order
Builder", no BOM warnings, no SKUs, no raw state codes); the anonymous-vs-login
decision for the primary CTA; a real touch path for in-scene cabinet
manipulation or an honest mobile fallback; sanitise the client error paths
(`kitchen_canvas.js:223` interpolates a raw JS Error into `innerHTML`).

## 4 · Open decisions (owner's, not the implementer's)

- **Visual identity.** Derive from cabinetry's own material world (timber, linen,
  ink; the existing Sky/Walnut/Linen and taupe/gold generations) rather than a
  generic palette. Status colours may be more saturated than brand colours
  because they must read at a glance on a factory tablet.
- **Customer CTA behind a login wall** — commercial call, not a design one.
- **Public `user-manual.pdf`** (`auth="public"`, `sitemap=True`, so
  search-indexed) contains chapters written for reps and dealers and names the
  six-channel pricing structure. It publishes **no discount rates** — verified,
  the only `%` characters in the document are CSS. Audience mismatch, not a
  pricing leak; decide whether it stays public.

## 5 · Verification standard

Every phase ships behind the existing gate: full suite with a **same-HEAD
baseline diff** (never a raw failure count — the suite carries ~32 pre-existing
reds), plus visual confirmation in a real browser. Code-only audits cannot tell
you a board is unusable at 1055px, and two agent claims this session
("stale bundle", "pricing matrix leaked") were wrong on severity and had to be
corrected against source. Locate with agents; verify yourself.
