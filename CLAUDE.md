# Claude Code Prompt — Southbrook Kitchen MRP V19CR  
## Estimating Application: a Prodboard‑class one‑page configurator on Odoo 19.0 CE

> **Operating brief for Claude Code.** Read this fully before editing anything.
> This prompt is **the brief**, not the implementation. Treat the *Project* materials
> as canonical, treat the prior packages as the starting point, and only generate
> code under `addons/southbrook_estimating/` (and a small companion website addon).
>
> **Amendment history.**
> - **v1.0 (2026-05-28)** — initial draft.
> - **v1.1 (2026-05-29)** — Q1/Q2/Q3/Q5/Q7/Q8/Q21 corrections folded in after
>   Claude Code's 21-question gate. The 21 locked decisions are catalogued in
>   `PUNCHLIST.md` § "2026-05-29 · Locked decisions" — that is the canonical
>   source for any decision detail; this brief is the operating frame.

---

## 0 · Mission in one paragraph

Co‑develop, with the human (John), the customer‑and‑sales‑rep‑facing **Estimating
Application** for Southbrook Kitchens on Odoo **19.0 Community Edition**, built on
top of the already‑migrated OCA `product_configurator` v19 suite. The estimating
UX must close the gap to **Prodboard** (the BetterKitchens‑style WebGL kitchen
planner) — single page, 3D‑first interaction, parametric carcasses snapped to a
metric grid, live pricing, automatic dimensioning, and a clean handoff into the
Southbrook MRP/BOM pipeline. Customers use it to design and request a price;
sales reps use the same engine in an internal "Order Builder" mode with channel
pricelists, multi‑zone orders, validation rules and BOM preview. Everything the
configurator captures becomes structured Odoo data — no spreadsheets, no
re‑keying — and feeds the manufacturing chain already scoped in the SAMI build.

---

## 1 · What already exists in this Project (the inputs you must use)

Before writing any code, read these in this order. They are the canonical
reference set; do **not** re‑derive what they already settle.

| # | Artifact | What it gives you | Treat as |
|---|---|---|---|
| 1 | `sami-product-configurator-v19.zip` | The four OCA modules ported from 18.0/17.0 to 19.0 CE: `product_configurator`, `product_configurator_mrp`, `product_configurator_sale`, `website_product_configurator`, with `CLAUDE.md`, `BUILD_RUNBOOK.md`, `RECIPE.md`, `PUNCHLIST.md` | **Foundation** — this is what `southbrook_estimating` depends on. Finish any open punch‑list items first. |
| 2 | `docs/SAMI_Southbrook_Odoo19_Build_Spec.md` | The locked architecture: v19 CE, OCA‑only, hybrid variant + parametric model (dynamic variant creation per Q6), cutlist math in Odoo / nesting to Accucutt, 6 pricelists per Q1, AI‑ready not AI‑yet | **Architecture** — do not deviate without raising it to John. |
| 3 | `PRODBOARD_MANIFEST.md` | Forensic reconnaissance of Prodboard (BetterKitchens deployment): 401 modules across 5 catalogs, 132 recipes, 18 element types, 40 door families, dimension‑envelope schema, classification system, recipe grammar, layout tokens, lighting setup | **Data‑model & UX blueprint** — mirror its structure where possible; the negative space (no MRP, no BOM, no quote document, no payment) is exactly what Southbrook fills. |
| 4 | `Southbrook_Excel_to_Odoo_Mapping.md` | Module‑by‑module mapping from the original Excel order workbook into the Odoo 19 CE build, including the rule set (series→door, box→series, width→door‑count, family→soft‑close) | **Business rules** — these are the configurator restriction rules. Encode them declaratively in `product.config.line` rules, not Python. |
| 5 | `Southbrook_Consolidated_Dataset.xlsx` | Price master, dealer orders, quote log, cabinet line items, KD component pricing, channel economics across 10 tabs | **Seed data** — derive demo `product.template` + pricelist fixtures from this, not from generic Odoo demos. |
| 6 | `Southbrook_ImageFloor_Case_Study.md` | Multi‑dealer operating‑model + pricing case study (Image Floor, Amazing Window, Pro Finish, Richwood) | **Persona reference** — drives the sales‑rep Order Builder ("Richwood −35%", "Image Floor dealer") flows. |
| 7 | `southbrook_book_templates.pdf` (Signature Series) | The visual target — the company's own spec‑book is what the customer‑facing one‑pager must read like | **Visual target** for the customer mode. |
| 8 | `Southbrook_Cabinetry_Dealer_Kitchen_Program.xls` | The 18 MB legacy workbook authored by Peter Tuschak — the business logic in spreadsheet form | **Truth source** for any pricing/rule question not covered in #4. |

If any of these are not in the workspace at start, **stop and ask** rather than
guessing. Do not invent business rules.

### What you are **not** rebuilding
- The OCA module port itself (#1) — already done, just consume it.
- The channel pricelist matrix — already specified in #5; you instantiate it as
  Odoo data, you do not redesign it.
- The MRP/BOM bridge to Accucutt — that's the `product_configurator_mrp` layer
  plus the planned custom cut‑dimension module; estimating *feeds* it, doesn't
  duplicate it.

---

## 2 · The mandate, decomposed

Build a new Odoo addon — **`southbrook_estimating`** — that depends on the four
ported OCA modules and adds the Prodboard‑class estimating experience for two
user personas, sharing one engine and one data model.

### 2.1 Persona A — **Customer** (one‑page online configurator)
- Single URL, single SPA‑feeling page on `southbrookcabinetry.space`, behind
  light auth (Odoo portal user).
- Three‑pane layout mirroring the Prodboard manifest §8.1 grid:
  **left 58 px tool rail / centre 394 px catalog pane / right flex viewport**
  with the 3D scene (or 2D‑isometric fallback) front and centre.
- Catalog tiles 296×94 px with 80×80 module thumbnail, Roboto Flex font, the
  Sky/Walnut/Linen public palette from the prior intranet design language.
- Customer flow: choose **family → width → series → box → door → colour →
  hinge → finished sides → gables → accessories**. Every selection re‑prices
  live and re‑renders the cabinet in the centre pane.
- "Request a Price" CTA at the end — generates an Odoo `sale.order` in draft
  state on the customer's portal, posts a webhook/email to the assigned dealer
  or salesperson, and shows the customer a confirmation with the priced spec
  sheet (PDF, Signature‑Series styled). **No payment in v1.**
- Mobile breakpoint: graceful degradation to a 2D card‑stack flow; do **not**
  attempt the full 3D planner on mobile (Prodboard itself gates this to
  desktop and we follow the same boundary in v1).

### 2.2 Persona B — **Sales Rep / Dealer** (Order Builder, internal backend)
- Lives under **Sales → Order Builder** in the Odoo backend; reuses the same
  `product.config.session` records as the customer flow.
- Multi‑line, multi‑zone order grid (zones per Q21: BASE_RUN, WALL, TALL, ISLAND,
  ACCESSORY, OTHER — with a `zone_label` free-text field shown only when
  zone=OTHER, encoded as a selection field on `sale.order.line`, no separate
  ORM model) — see the (regenerating) `southbrook_internal_order_builder.html` mock for
  the layout target.
- Header carries the resolved **channel pricelist** (Dealer −50%, Tradesperson
  −25→−35% [workbook label: "Contractor"], KD ~46% of retail, Big‑box fixed,
  Refacing per‑SF) auto‑applied from `res.partner.channel`.
- Per‑line inline config drawer with all 11 attributes; rule‑blocked options
  visibly disabled with the reason ("Maple box not available on Contractor
  series").
- **BoM preview tab** showing the generated `mrp.bom` per line (driven by
  `product_configurator_mrp`) before confirmation.
- **Validation tab** with hard rules (blocking) and soft suggestions (e.g.
  "9‑21″ cabinets should be 1‑door — current spec uses 2‑door").
- Stage pipeline: **Draft → Estimating → Approval → Confirmed → In Production**
  (Approval gate enforced for orders above a configurable threshold).
- Switching customer on the header re‑prices the whole order (the prior mock
  demonstrated Richwood −35% $1,036.24 ↔ Retail $1,594.21 — this must keep
  working).

### 2.3 Shared engine
Both personas drive **the same** `product.config.session` records and the same
`product.template` + attribute model. The customer flow is just a constrained,
themed `website_product_configurator` route; the sales‑rep flow is the full
backend form. There must not be two configurators.

---

## 3 · Architecture & module structure

<!-- Doctor trim 2026-08-02: an 88-line directory tree stood here. It had
     drifted (the repo now holds ~59 addons, not 2) and `ls addons/` is the
     living answer. Restore from git history if ever wanted. -->
The four OCA `product_configurator*` modules plus the Southbrook addons live
under `addons/` — enumerate with `ls addons/`.

### Why two addons, not one
- `southbrook_estimating` is the data + backend + rules — it can run *without*
  the website addon for the sales‑rep persona. Useful for early integration
  testing and for any dealer terminal that's backend‑only.
- `southbrook_estimating_website` is the public Three.js layer. It depends on
  the engine but is independently deployable, has its own asset bundle, and
  doesn't bloat backend load.

### What you do **not** touch
- The four `product_configurator*` modules under `addons/` — leave them at
  19.0.1.0.0 as delivered. All extension goes through `_inherit` in
  `southbrook_estimating`. (If you find a real upstream bug, file it against
  the OCA fork; do not patch in place.)
- Any module not in this tree.

---

## 4 · The Prodboard‑mimicking layer (the hard part)

This is the differentiator and most of the actual work. The `PRODBOARD_MANIFEST.md`
is the spec; what follows is the implementation translation.

### 4.1 The parametric carcass — procedural, not pre‑baked
Each cabinet is a **parametric object** carrying `width × height × depth` plus
its construction parameters (series, door style, hinge side, finished sides,
gables, interior). The Three.js scene generates the mesh from these on the fly
using `BufferGeometry` — **not** pre‑baked GLBs. This is what makes the
solid↔blueline toggle and the live re‑price work; everything downstream
(rendered mesh, dimension lines, BoM, cut list, label, door order) is just a
**projection** of that one object.

Mirror the manifest's recipe grammar (§5):

```json
{
  "BHL1DR": {
    "filling": {
      "hinge_block":  {"position": "L"},
      "1.drawer":     {"height": 150},
      "2.drawer":     {"height": 150},
      "delimiter":    {"position": 300}
    }
  }
}
```

Implement the **18 element types** enumerated in manifest §5.2 — at minimum
`hinge_block`, `drawer`, `delimiter`, `L_profile`, `stub_block`, `oven`,
`open_block`, `integrated_microwave`, `tall_cargo`, `container`, `lift` — as
classes in `parametric_carcass.esm.js`, each emitting its own geometry and its
own contribution to the BoM rollup.

### 4.2 DimensionEnvelope per attribute
Width, height, depth on every cabinet template carry the envelope shape from
manifest §4.3:

```python
DimensionEnvelope = {
    "items":   [200, 300, 400, 500, 600, 700, 800, 900, 1000],   # mm
    "min":     200,
    "max":     1000,
    "default": 600,
}
```

`items` is the enumeration shown in the UI (the snap‑grid); `min/max` are the
slider bounds for free entry (advanced users + sales reps); `default` is the
initial value. Customer mode shows the snap list; sales‑rep mode reveals the
slider.

### 4.3 Automatic dimensioning (the blueline mode)
A toggle in the viewport switches between the photoreal solid render and a
dimensioned isometric blueline drawing — the manifest §3 architectural
hallmark. The dimension lines must be **derived programmatically** from each
cabinet's bounding box and snapped to the run; never manually placed.

### 4.4 The classification system (5 catalogs, 6 purposes)
Map manifest §6 onto Odoo's existing `product.attribute` system. The six
purpose codes (Functional, Visual, Mechanical, Material, Geometric, Pricing)
become **attribute groups** with `display_type` and ordering carrying the
purpose. Each cabinet has ~6 classification memberships, exactly as in
Prodboard.

### 4.5 Asset strategy — the four‑tier image cascade
From manifest §11 and §16: **never** embed `blobs.prodboard.com` URLs. Use:
1. **Tier 1** — vendor‑supplied cabinet renders when Southbrook provides them.
2. **Tier 2** — runtime‑baked thumbnails from the live Three.js scene per
   configurator state change.
3. **Tier 3** — hand‑authored SVG fallbacks for every cabinet code (Day‑1
   launch ships 100% Tier 3 coverage so the configurator never shows a broken
   tile).
4. **Tier 4** — neutral placeholder.

### 4.6 Lighting & materials
Three.js scene with **ACES Filmic tone mapping, sRGB output**, 6 lights (1
hemispheric + 1 directional + 4 point), KTX2/Basis Universal textures,
MeshBVH for picking, scene‑diff updater (do not re‑mount on prop change).

### 4.7 What you **deliberately do not copy from Prodboard**
The negative space in manifest §2 is Southbrook's whole moat:
- ✅ Real BoM preview, generated by `product_configurator_mrp`, not absent.
- ✅ Real Odoo quote PDF (Signature Series styled), not a webhook to nowhere.
- ✅ Real payment path (deferred to v2, but the data model accommodates it
  from day one — link `sale.order` to `account.payment` cleanly).
- ✅ Full MRP loop into manufacturing orders, work orders, and the Accucutt
  cut list hand‑off.
- ✅ Right‑side inspector panel with multi‑select (shift‑click) and keyboard
  dimension entry — none of which Prodboard has.
- ✅ Collision detection between adjacent cabinets in a run (Prodboard sets
  `ignoreIntersections:true` on many modules; we do not).

---

## 5 · Business rules (declarative, in `data/config_rules.xml`)

Encode these as `product.config.line` rules — **not** as Python overrides.
Source: `Southbrook_Excel_to_Odoo_Mapping.md` §3.4.

1. **Series → door style.** Contractor series only exposes white thermofoil
   slab doors; Elegance series only exposes five‑piece woodgrain doors.
2. **Box material → series.** Maple box is offered only on Contemporary and
   Elegance, and carries **+10% price** and **+2 weeks lead time** (as
   `price_extra` and an `mrp` lead‑time bump).
3. **Width → door count.** 9‑21″ cabinets are 1‑door; 24‑36″ cabinets are
   2‑door. Drives hinge quantity and door count in the BoM automatically.
4. **Family → soft‑close.** Bi‑fold corner cabinets ship without soft‑close
   hinges — option is hidden, not disabled with a reason.

Acceptance: an invalid combination (e.g. Contractor + five‑piece door) is
**unselectable** in both customer and sales‑rep UIs, with the rule reason
visible to the sales rep but not to the customer (customers just see the
option absent).

---

## 6 · Channel pricelist matrix (data, not logic)

Seed **six** `product.pricelist` records, auto‑assigned by `res.partner.channel` (Q1 locked):

| Channel key | Mechanic | UI label | Source |
|---|---|---|---|
| `retail` | List price (Signature Series book) — the base pricelist others inherit from | "Retail (List Price)" | Price Master tab of #5 |
| `dealer` | List × 0.50 | "Dealer (−50%)" | Signed Dealer Agreement (in #6) |
| `tradesperson` | Cost × 1.05 → tier discount 25% / 30% / 35% (Q5 — workbook calls this "Contractor"; key renamed for grep-safety) | "Contractor (Tiered)" | Pricing Evolution tab of #5 |
| `kd` | ≈46% of retail, component pricing only (no assembly) | "Central KD" | KD Component Pricing tab of #5 |
| `bigbox` | Fixed wholesale $65 cost / $98 retail per SKU | "Big-Box Wholesale" | Channel Economics tab of #5 |
| `refacing` | Per‑SF door pricing, ~35% target margin | "Refacing (CTHS)" | Channel Economics tab of #5 |

The refacing channel is the one place a true margin‑target rule is needed
(price set live to hit 35% off current cost). Implement as a small computed
field on `product.pricelist.item`; do **not** hand‑roll the others.

---

## 7 · Co‑development protocol with John

This is **co‑development**, not autonomous build. The contract:

1. **Always read the Project artifacts first** when starting a fresh session.
   Never re‑derive what's in `Southbrook_Excel_to_Odoo_Mapping.md` from
   first principles.
2. **Before any non‑trivial commit**, post a short summary message to John:
   what changed, what files, what tests pass. Wait for ack on architectural
   decisions; proceed without ack on mechanical changes (typos, manifest
   bumps, lint).
3. **Stage gates** — at the end of each phase below, John reviews on a live
   Odoo 19.0 CE instance (he will provide the URL). Do not start the next
   phase until the prior phase is signed off.
4. **Open questions go in `PUNCHLIST.md`** in the addon root with date and
   context, never silently into commit messages.
5. **Never invent business rules.** If a rule is not in the source artifacts,
   ask. Cabinets are not generic; a 9‑21″ "1‑door" rule is real.
6. **Never embed Prodboard asset URLs**, screenshots, or copyrighted artwork.
   Use the four‑tier image cascade.
7. **Keep the OCA modules clean.** All Southbrook logic in
   `southbrook_estimating*`; the four OCA addons stay shippable upstream.

---

## 8 · Phasing — COMPLETE

<!-- Doctor trim 2026-08-02: the dated week-by-week Phase 1-4 roadmap stood
     here; all four phases shipped long ago (git history is the record).
     The per-phase gate discipline lives on in §7. -->
All four build phases (engine/Order Builder, 2D customer flow, 3D parametric
layer, MRP + cut-list bridge) are shipped and live. The AI layer remains a
separate brief.

---

## 9 · Acceptance criteria (the bar)

Phase‑independent quality gates the whole module must clear:

- [ ] `pre-commit run -a` clean across both addons.
- [ ] `odoo-bin -i southbrook_estimating,southbrook_estimating_website -d <db>
      --stop-after-init` installs without errors on a fresh 19.0 CE database
      with the four OCA modules already present.
- [ ] `odoo-bin --test-enable -i southbrook_estimating` — unit tests cover
      every config rule, the channel pricelist resolution, and the BoM rollup
      for the four canonical cabinets (base 1‑door, base 2‑door, drawer bank,
      wall).
- [ ] All four configurator rules from §5 are **declarative**, not Python.
      Grep `southbrook_estimating/models/` — there must be zero `if series ==`
      branches enforcing what `product.config.line` already enforces.
- [ ] Switching a saved sale order's customer between **Demo Tradesperson Tier 3** (−35%)
      and a retail walk‑in re‑prices every line without re‑configuring it (this is
      the locked smoke test per Q7).
- [ ] Zero references to `blobs.prodboard.com` or any Prodboard URL in the
      shipped codebase. `grep -rn prodboard.com addons/southbrook_estimating*`
      returns nothing in tracked files.
- [ ] The customer flow is accessible at WCAG AA on the catalog and form
      controls; the 3D viewport is a progressive enhancement.
- [ ] README in each addon root explains the dependency on the four OCA
      modules and points to this prompt + the SAMI build spec as canonical
      design docs.

---

## 10 · Session zero — EXECUTED

<!-- Doctor trim 2026-08-02: the 7-step first-session bootstrap checklist
     stood here; it ran in May 2026. The canonical-artifact reading list in
     §1 still names every document it pointed to. -->
Build deliberately, ask early, and keep the receipts.

<!-- HISTORICAL: §11 was removed per the user's explicit revert. The wider
     platform-expansion canon now lives in:
       ~/Downloads/CLAUDE_CODE_PROJECT_INIT.md  (the init doc)
       ~/.claude/projects/-Users-naadmin/memory/sami_southbrook_full_platform_build.md
     This file stays scoped to the Estimating Application surface.
-->
