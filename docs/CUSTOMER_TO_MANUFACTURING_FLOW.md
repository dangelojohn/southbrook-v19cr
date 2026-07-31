# Customer-to-Manufacturing Flow — Architecture

**Status:** draft for review · 2026-06-01
**Author:** Claude Code (architecture pass)
**Scope:** the end-to-end product flow from customer click to shipped
cabinet, including the PLM governance layer that wraps it.

## 0 · Why this doc exists

After today's PLM RC + the gate-walk demos + the partial Phase 2
revert, the direction needs to be locked before more code lands. The
goal is a **smooth, professional, easy-to-use, sophisticated**
workflow — front of house as easy as Prodboard, back of house as
trustworthy as enterprise PLM, with bridges between so a sales-floor
issue can surface as an engineering change order without
re-keying anything.

This doc is the **architecture lock**. Once approved it gates the next
several commits.

---

## 1 · The four personas

| Persona | Surface | What they see | What they don't see |
|---|---|---|---|
| **Customer** | `/kitchen-planner` (public, portal-auth) | three-pane SPA: tool rail · catalog · 3D viewport. Pick cabinets, drag onto a kitchen run, see live price. Click "Request a Price." | nothing manufacturing, nothing pricing-mechanic, nothing internal |
| **Dealer / Sales Rep** | `/my/southbrook/order-builder/<id>` | Track 2 portal SPA: header + tabs + zone grid + ConfigDrawer + footer actions. Sees the customer's exact configuration + can edit. Approves / sends to production. | PLM workflow, ECO chatter, cut-spec internals |
| **Engineering** | `/odoo` backend — Manufacturing > Engineering Change Orders | the PLM Kanban: ECOs across stages. Raise, approve, apply. BoM + cut spec governance. | customer-facing pricing UX, dealer Order Builder unless they switch role |
| **Manufacturing** | `/odoo` backend — Manufacturing > Manufacturing Orders | standard Odoo MO views + Southbrook's Shop Copy + Door Order reports. Pull panel cut list from the active cut spec (via the seam). | customer planner, dealer Order Builder |

The **same Odoo records** carry every persona's view. There is
**one configurator** (the OCA `product_configurator` suite + Southbrook
extensions), **one BoM model** (Odoo's `mrp.bom` + Southbrook's
versioning), **one cut spec** (`southbrook.cut.spec` + the seam). What
differs is the chrome around them and the entry points each persona
uses.

---

## 2 · The end-to-end data spine

Every step below creates structured Odoo records. Nothing in
spreadsheets. Nothing requires re-keying.

```
[Customer SPA]                                                      [Engineering]
     │                                                                    │
     │  drag cabinets, attribute picks                                    │
     ▼                                                                    │
product.config.session (draft)                                            │
     │                                                                    │
     │  commit (= "Request a Price")                                      │
     ▼                                                                    │
product.product variant materialised + sale.order (state=draft)           │
     │                                                                    │
     │  visible in /my for customer + /my/southbrook/order-builder        │
     │  for dealer                                                        │
     ▼                                                                    │
[Dealer Order Builder]                                                    │
     │                                                                    │
     │  qty edits, attribute swaps, line additions                        │
     ▼                                                                    │
sale.order (state=sent or sale)                                           │
     │                                                                    │
     │  action_confirm                                                    │
     ▼                                                                    │
mrp.production + mrp.bom (one per cabinet line, parametric)               │
     │                                                                    │
     │  ─────────────────────  the seam fires here  ────────────────────▶ │  southbrook.cut.spec
     │                                                                    │  (active version
     │                                                                    │   → cut math)
     ▼                                                                    │
[Manufacturing]                                                           │
     │                                                                    │
     │  panel cut list, door order, hardware pull                         │
     ▼                                                                    │
stock.picking → ship                                                      │
     │                                                                    │
     ▼                                                                    │
account.move (invoice)                                                    │
                                                                          │
                                                                          │
       ┌──────────────────────────────────────────────────────────────────┘
       │
       ▼
[PLM — sideways governance]
southbrook.eco (target_kind: bom / cut_spec / rule / document)
       │
       │  apply_handler runs
       ▼
mutates: mrp.bom version / southbrook.cut.spec active / git audit / etc.
```

The **PLM layer is sideways**, not in the customer→manufacturing flow.
ECOs govern the *rules of the road* (cut specs, BoM templates,
construction rules) that the in-line flow follows. The bridges (§3.3)
let a sales-floor issue surface as an ECO without leaving the order.

---

## 3 · The bridges

### 3.1 Customer → Dealer

| Today | After this work |
|---|---|
| Customer doesn't exist as a persona yet (Phase 2 §8 not built) | Customer completes the SPA → `sale.order` is created in draft on their portal, dealer is notified via partner-link rule (customer.parent_id = dealer.partner_id) |

**Mechanism:** the customer one-page SPA commits the configurator
session, materialises variants, creates a draft `sale.order` with the
dealer as the partner's parent. Webhook/email goes to the dealer's
assigned salesperson. Customer sees a confirmation + spec-sheet PDF.

**Implementation surface:** `southbrook_estimating_website` (Phase 2
commit 6+ per `CLAUDE.md` §8.2).

### 3.2 Dealer → Manufacturing

| Today | After this work |
|---|---|
| FooterActions has Confirm Order which calls `action_confirm` — that already triggers `mrp.production` creation via Odoo standard | Explicit "Send to Production" button at the footer, distinct from "Confirm Order" — clarifies the irreversible step. Confirm Order = lock the deal; Send to Production = release to shop floor. |

**Mechanism:** split the Confirm Order action in two. Confirm sets
`state=sale` but defers MO creation. Send to Production explicitly fires
`action_confirm` and surfaces a confirmation modal showing exactly
which cut spec version + BoM versions are being committed.

**Implementation surface:** `southbrook_estimating_website/static/src/js/portal_boot.esm.js`
FooterActions component + a small backend action on `sale.order`.

### 3.3 Sale.order.line → ECO (the feedback loop)

| Today | After this work |
|---|---|
| No bridge. If a dealer realises a cabinet has a construction problem mid-order, they have to context-switch to the PLM backend, raise an ECO manually, paste the cabinet details, and bind the right BoM by hand. | Smart button on `sale.order.line`: **"Raise an ECO about this cabinet."** Pre-fills `target_kind` (auto-detects from the line's product family), `bom_id` (from the line's variant), description (auto-includes order ref + line ref + spec text). Approver sees the ECO in their Kanban with full context. |

**Mechanism:** new field `southbrook_eco_count` (computed) +
`action_raise_eco` button method on `sale.order.line`. The action
opens a draft ECO form pre-filled from the line's context.

**Implementation surface:** `southbrook_plm/models/sale_order_line.py`
(new file, ~40 lines) + smart button in `views/sale_order_line_views.xml`.

---

## 4 · Variant-BoM versioning lock

**The problem.** Today's `southbrook.cut.spec` seam means
`mrp.bom._get_cut_constants()` always returns the currently-active
spec. If an ECO activates a new cut spec mid-order, every
`mrp.production` in flight starts cutting panels with the new
reveal/thickness — including ones whose `sale.order` quoted the old
spec. The customer was promised cabinets cut to spec v3, the shop
floor cuts them to spec v4.

**The fix.** When `sale.order.action_confirm` fires, each
`sale.order.line` captures **two snapshots** at commit time:

| Field | Captures |
|---|---|
| `southbrook_cut_spec_version_id` (many2one) | the cut spec record that was active when this line was confirmed |
| `southbrook_bom_version` (integer) | the `mrp.bom.southbrook_version` of the BoM that materialised this line |

The MO and downstream cut list **read those snapshots, not the
current state**. A later ECO that activates a new cut spec affects
**only orders confirmed after the apply** — never in-flight ones.

**Status of in-flight orders** is then deterministic:

- *Quoted but not confirmed* → re-quotes off the current active spec
  on next view (customer/dealer always see the latest)
- *Confirmed* → frozen against the snapshot; manufactures off the
  spec that was active at confirm time

**Implementation surface:** `southbrook_estimating/models/sale_order.py`
(extend `action_confirm` to write snapshots) +
`southbrook_estimating/models/sale_order_line.py` (the snapshot fields) +
`southbrook_estimating/models/mrp_bom.py` (seam reads the line's
snapshot if available, falls back to active spec for non-southbrook
BoMs).

**Tests:** ~4 methods. (1) confirm captures snapshot. (2) ECO apply
after confirm doesn't change MO. (3) ECO apply before confirm uses
new spec. (4) re-confirming a duplicated order picks up the new spec.

This is the **structural keystone**. Without it, every other elevation
is built on sand — a single ECO mid-day breaks every in-flight quote.

---

## 5 · Visual design register

Three surfaces (customer SPA, dealer Order Builder, PLM
forms/kanban) need to share one design language so the product feels
coherent.

### 5.1 Tokens (single source of truth)

A shared SCSS partial — `addons/southbrook_estimating/static/src/scss/_southbrook_design_tokens.scss` —
exports the Signature Series register as CSS custom properties:

```scss
:root {
  // Palette (Sky / Walnut / Linen / Ink — from the prior intranet)
  --sb-sky:    #5a83a8;  --sb-sky-l:   #d8e3ec;
  --sb-walnut: #4a3c2e;  --sb-walnut-l:#6b5b48;
  --sb-linen:  #f5efe6;  --sb-paper:   #f5f4ee;
  --sb-ink:    #1a1815;  --sb-rule:    #d8d2c4;
  // Semantic — SUPERSEDED 2026-07-31. These three values never matched
  // what shipped, and the doc drifted from its own implementation before
  // anyone noticed. The canonical source is now
  // addons/southbrook_ui/static/src/scss/_sb_tokens.scss, which defines
  // six semantics (ok / warn / late / blocked / info / neutral), each with
  // -fg/-bg/-border in both light and dark, every pair WCAG-verified.
  // --sb-ok / --sb-warn / --sb-alert still resolve as aliases; do not add
  // new uses, and do not copy hex values out of this block.
  //   was: --sb-ok: #2f8a4f;  --sb-warn: #c8881e;  --sb-alert: #b54b3b;
  //   use: var(--sb-status-ok-fg), var(--sb-status-warn-fg),
  //        var(--sb-status-blocked-fg)   ("late" is now distinct from
  //        "blocked" — on a factory floor those demand different actions)
  --sb-hl:     #f3d98c;  // selection amber
  // Typography
  --sb-font-display: 'Roboto Flex', 'Inter', system-ui, sans-serif;
  --sb-font-sans:    'Roboto Flex', system-ui, sans-serif;
  --sb-font-mono:    'JetBrains Mono', ui-monospace, monospace;
  // Layout grid (per PRODBOARD_MANIFEST §8.1 + Signature Series book)
  --sb-rail-w:    58px;
  --sb-catalog-w: 394px;
  --sb-tile-w:    296px;
  --sb-tile-h:    94px;
  --sb-tile-thumb:80px;
}
```

Every Southbrook addon imports this partial before its own SCSS. The
backend PLM kanban, the portal Order Builder root, and the customer
SPA all read the same values.

### 5.2 Component vocabulary

A small shared component palette — defined once, used everywhere:

| Class | Purpose | Used by |
|---|---|---|
| `.sb-btn-primary` | walnut bg, linen text, uppercase tracked | Send to Production, Apply ECO, Request a Price |
| `.sb-btn-secondary` | walnut border, walnut text on hover invert | Cancel, Back, Reject |
| `.sb-card` | linen bg, walnut rule, 4px radius | every form container |
| `.sb-pill` | mono 11px, uppercase, coloured border | state badges, channel tags |
| `.sb-rule-stack` | 1px sb-rule horizontal dividers | sections within a card |

These land in `_southbrook_design_tokens.scss` alongside the variables.
Same partial is imported by `portal_root.scss`, `planner.scss` (future
customer SPA), and a new `plm_register.scss` for the PLM addon.

### 5.3 Typography axes

Roboto Flex's `font-variation-settings` are used consistently:

- Headings: `"opsz" 36, "wght" 500` (display, restrained weight)
- Body: `"opsz" 14, "wght" 400`
- Mono labels: JetBrains Mono 11px, `letter-spacing: 0.04em`, uppercase
- Price values: JetBrains Mono 18px, walnut

Same axis values across all surfaces. No surface inventing its own
heading scale.

---

## 6 · The build sequence

| # | Step | Lines (est) | Hours (est) | Dependency |
|---|---|---|---|---|
| 1 | This design doc | 350 | 0.25 | — |
| 2 | Shared design tokens partial + import threading | 100 | 0.5 | 1 |
| 3 | Variant-BoM versioning lock (§4) | 200 | 3 | 2 |
| 4 | Sale.order.line → ECO bridge (§3.3) | 150 | 2 | 2, 3 |
| 5 | Send to Production CTA + confirmation modal (§3.2) | 100 | 1.5 | 3 |
| 6 | PLM addon adopts shared tokens (visual refresh of Kanban + form) | 80 | 1 | 2 |
| 7 | Customer SPA restart (Phase 2 §8 commits 1-5) | 1500 | 12 | 2, 3 |
| 8 | Three.js procedural carcass (Phase 3) | 3000+ | 40+ | 7 |

**Step 2 is the smallest investment with the highest leverage** —
without shared tokens, every downstream commit invents its own
palette/grid. Three commits in we'd have three slightly-different
visual registers to reconcile.

**Step 3 is the structural keystone** — see §4. Without it, every
elevation built later is unstable against PLM apply events.

**Steps 4-6 are the bridges** — they make PLM and sales/manufacturing
work as one product instead of two adjacent tools.

**Step 7 is the visible elevation** — when the customer SPA ships,
the whole product is suddenly Prodboard-class. But shipping it before
Steps 2-3 means shipping over sand.

**Step 8 is the moonshot** — full Three.js parametric carcass. Out of
scope for this design pass.

---

## 7 · Acceptance criteria

The elevation is "done" when:

1. **One-product feel.** A customer opening the SPA, a dealer opening
   the Order Builder, and an engineer opening PLM see the same
   palette, typography, and component vocabulary. Visual scan: each
   surface is recognisably the same product.
2. **Customer → confirmed order in < 3 minutes** on a single cabinet.
   No internal jargon, no Odoo-isms, no "configurator wizard."
3. **In-flight orders are stable against ECOs.** An ECO that activates
   a new cut spec at 14:00 does not change the panel cuts of an order
   confirmed at 11:00.
4. **ECO can be raised from any sale.order.line in one click**, with
   `bom_id` + `target_kind` + description pre-filled.
5. **PLM Kanban renders cleanly at 1280×800** with the same Sky/Walnut/
   Linen palette as the customer SPA.
6. **All commits ship green tests.** The 32-test PLM suite stays at
   100%. Estimating's test suite extended for the snapshot fields.

When (1)-(6) are all true, the brief's "smooth, professional, easy to
use, sophisticated" wording is met.

---

## 8 · Open questions for the user

These need confirmation before the spine commits land. They each
affect § 4 / § 3.3 design decisions.

1. **Q-A: ECO mid-flight semantics on duplicated orders.** When a
   dealer "Duplicate as Draft"s an order whose original was on cut
   spec v3, should the duplicate (a) keep v3 frozen, or (b) re-quote
   off the now-active v4? Draft assumption: **(b) re-quote off
   active** — duplicate is a fresh negotiation.
2. **Q-B: ECO bridge auto-detection of target_kind.** From a base
   cabinet line, the obvious target is the line's `mrp.bom`
   (target_kind=bom). But sometimes the issue is a construction rule
   (target_kind=rule) or a cut-spec value (target_kind=cut_spec). Should
   the smart button (a) auto-pick `bom`, (b) open a chooser, or (c)
   open a draft ECO with no target and let the engineer scope it?
   Draft assumption: **(a) auto-pick bom** with a hint in the description
   that the engineer can re-target.
3. **Q-C: Customer SPA auth model.** CLAUDE.md §2.1 says "behind light
   auth (Odoo portal user)." Should the SPA (a) require login before
   any picks, or (b) let visitors play with cabinets anonymously, with
   login required only at "Request a Price"? Draft assumption: **(b) anonymous
   play, login at commit** — Prodboard parity.
4. **Q-D: Visual consistency timing.** Should Step 6 (PLM visual
   refresh) ship before Step 7 (customer SPA), or be deferred? Draft
   assumption: **before** — by the time the SPA ships, every backend
   surface should already look Southbrook-branded.

If any draft assumption is wrong, raise it in your reply and I'll
revise this doc + the downstream build plan.

---

## 9 · What this doc does NOT cover

These are intentional omissions to keep the doc focused. Each may
need its own design pass when its phase arrives.

- The 3D parametric carcass implementation (Phase 3 / Three.js)
- The Accucutt nest envelope (Phase 4 routine #7)
- The AI data spine — analytics tags, forecast model, lead-time
  predictor (the brief's Phase 2+ explicit deferral)
- Mobile breakpoint behaviour beyond "2D card-stack fallback"
- Cross-currency support, taxes, shipping address logic
- The actual demo dataset cleanup work (PT-P1-01 through PT-P1-06 in
  PUNCHLIST.md — those continue in parallel)

---

## 10 · Sign-off

Once you've read this:

- **Approve as-is** → I start Step 2 (shared design tokens partial)
- **Approve with the open-questions answered** → I update the doc with
  your answers, then start Step 2
- **Major redirect needed** → tell me where, I revise the doc, we
  re-align before any code

I'll wait for one of those three before touching any model or SCSS.
