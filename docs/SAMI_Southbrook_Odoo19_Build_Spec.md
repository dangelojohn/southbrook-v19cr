# SAMI / Southbrook — Odoo 19 Community Build Spec

**Working specification for Claude Code.** Demand-to-delivery: customer configures a cabinet on a one-page Product Configurator order form; the demand flows through Sales → Procurement → parametric BoM → Manufacturing → Quality → Logistics → Invoicing, with a captured data spine for a later AI layer.

This document is the **locked architecture** for the build. Read it top to bottom before generating code. Where it says **VERIFY**, confirm against the live environment or repo before relying on it — Odoo 19 is recent and some OCA ports lag.

---

## 0 · Locked decisions

| Dimension | Decision | Source |
|---|---|---|
| Platform | Odoo **19.0 Community**, OCA modules only (no Enterprise, no proprietary apps) | John, prior session |
| Configurator base | **OCA `product_configurator` v19** (the four ported modules in `addons/`) — do **not** depend on the native v19 configurator | Migration package delivered, Q18 verified clean |
| Configured-product model | **Hybrid**: finite choices = dynamically-created variants; W×H×D = parametric numeric inputs driving a formula BoM | Q6 locked |
| Variant creation mode | **Dynamic** (`create_variant = 'dynamic'`) — variants materialise only when actually ordered | Q6 locked |
| Cutlist boundary | Panel **math inside** Odoo (deterministic, feeds BoM/labels/price/cost); **nesting/optimization handed to Accucutt**, nest result stored back | John, prior session |
| MVP scope | **Full chain** demand → MO → ship → invoice. Thread **one channel end-to-end first** (the smoke-test tradesperson tier 3), then fan out | John, prior session |
| Channels | **6 pricelists**: Retail base + 5 channels (Dealer 50% off retail, Tradesperson tiered 25/30/35%, Central Kitchens KD ~46%, Big-box fixed $65/$98, Canadian Tire refacing per-SF) | Q1 locked |
| Dimension storage | **Dual storage**: `value_inches` (display) + `value_mm` (canonical) on every dimensional `product.attribute.value`. No conversion math — both fields explicitly set | Q4 locked |
| Naming disambiguation | Series uses `contractor`; partner channel uses `tradesperson` — grep-safe in code, UI label preserves workbook vocabulary | Q5 locked |
| AI in v1 | **AI-ready, not AI-yet** — capture the data spine via analytic tags + structured fields; defer models (forecast / quote analytics / lead-time predictor) to Phase 2+ | John, prior session |

### Community-edition consequences (do not assume Enterprise modules exist)

The build operates entirely within Community Edition. Several Enterprise modules John might assume exist do **not**:

| Enterprise module | Status in Community | Replacement |
|---|---|---|
| Quality | Absent | **OCA `quality_control`** family |
| PLM | Absent in CE | **Lightweight, ECO-governed** — `southbrook_plm` addon (routine #8, LGPL-3, RC 2026-05-31). Engineering Change Orders with stage pipeline + approval gating; ECO-applied template-BoM versioning + parametric cut-spec activation. NOT a CAD/PDM vault. |
| Shop Floor / MES tablet | Absent | Work-order execution on standard MO views |
| Studio | Absent | All view extensions via standard XML inheritance |
| Documents | Absent | Standard `ir.attachment` + portal `/my/attachments/*` |

`mrp`, `sale_management`, `stock`, `purchase`, `account`, `website_sale`, `crm` are all present in Community and used as-is.

---

## 1 · Environment & stack

### 1.1 Server

- Odoo **19.0** Community Edition
- Python **3.12+** (**VERIFY** against the 19.0 release notes for the actual minimum)
- PostgreSQL **16** (15 acceptable; 14 untested with v19)
- Linux server (Ubuntu 24.04 LTS or Debian 12 recommended)
- `wkhtmltopdf` 0.12.6 (for QWeb PDF reports)

### 1.2 Addons path

```
~/southbrook-v19cr/
└── addons/
    ├── product_configurator/                 (OCA, 19.0.1.0.0 — DO NOT EDIT)
    ├── product_configurator_mrp/             (OCA, 19.0.1.0.0 — DO NOT EDIT)
    ├── product_configurator_sale/            (OCA, 19.0.1.0.0 — DO NOT EDIT)
    ├── website_product_configurator/         (OCA, 19.0.1.0.0 — DO NOT EDIT)
    ├── quality_control/                      (OCA, 19.0.x — Phase 4)
    ├── southbrook_estimating/                (NEW — Claude Code builds)
    └── southbrook_estimating_website/        (NEW — Claude Code builds)
```

### 1.3 Python deps

The two Southbrook addons add no new Python dependencies beyond what OCA configurator already requires. Phase 4's Accucutt bridge adds `requests` (already in Odoo's base) — nothing else.

### 1.4 Frontend

- **Backend (Order Builder):** standard Odoo OWL framework, no new JS deps
- **Frontend planner (Phase 3):** Three.js (latest stable), KTX2Loader + Basis Universal transcoder, MeshBVH. All loaded via Odoo's asset bundle system from `addons/southbrook_estimating_website/static/lib/`. Pin exact versions in the manifest's `assets` block — no CDN fetches at runtime.

---

## 2 · Architecture overview

### 2.1 The data spine

Every interaction with the configurator creates structured Odoo records. Nothing lives in spreadsheets. Nothing requires re-keying.

```
customer / sales rep
       │
       ▼
product.config.session  ◄────────  configurator UI
       │                            (customer one-pager OR sales-rep Order Builder)
       ▼
sale.order + sale.order.line
       │
       ▼
mrp.production + mrp.bom (parametric, generated per line)
       │
       ▼
stock.picking (label generation per cabinet/component)
       │
       ▼
purchase.order (door supplier, with auto-aggregation)
       │
       ▼
account.move (invoice on completion)
```

Every record above carries **analytic tags** at creation: `channel`, `series`, `tradesperson_tier`, `dealer_id` where applicable. This is the data spine — captured from day one, queried by the future AI layer.

### 2.2 The hybrid configurator model — the architectural keystone

This is **the** decision that makes the whole build possible. Pure variants explode; pure parametric loses Odoo's pricing/inventory affordances. The hybrid:

**Finite cosmetic/material choices → dynamic variants.**
Door style, finish/colour, hinge side, hardware brand — these are real `product.attribute` records with `create_variant = 'dynamic'`. A SKU materialises only when actually ordered. No upfront SKU explosion.

**Continuous dimensions (W × H × D) → parametric numeric inputs.**
Width, height, depth are **never** stored as attribute custom values (Odoo's attribute custom values are free-text, won't drive formulas reliably). They are dedicated numeric fields on `product.config.session.value` (or a sibling extension) that feed the parametric BoM rollup in `models/mrp_bom.py::_compute_panel_dimensions`.

**Result:** one `product.template` per cabinet family (12 templates total, locked in Q8); a configured kitchen with 9 cabinets materialises exactly 9 `product.product` records, with their BoMs derived from 9 sets of (W, H, D) + attribute values.

### 2.3 Two personas, one engine

The customer one-pager and the sales-rep Order Builder share **the same** `product.config.session` records, **the same** `product.template` + attribute model, and **the same** rules. The customer flow is a constrained, themed `website_product_configurator` route; the sales-rep flow is the full backend form.

There must not be two configurators. There is one configurator with two surfaces.

### 2.4 The Prodboard-class layer (Phase 3 only)

`southbrook_estimating_website` adds a Three.js scene that renders each cabinet as **procedural BufferGeometry** from `(value_mm_width, value_mm_height, value_mm_depth, attribute_values...)`. The scene's solid↔blueline toggle and automatic dimensioning are projections of the same parametric object that drives the BoM rollup.

See `PRODBOARD_MANIFEST.md` §3, §5, §9, §11 for the reference details. The scene mimics Prodboard's visual architecture but **does not** copy assets, URLs, or any code from Prodboard.

---

## 3 · The module structure (refresher — full version in `CLAUDE.md` §3)

```
southbrook_estimating/                ← engine + sales-rep backend
├── models/                           (~7 custom routines per Q3, no more)
├── views/                            (Order Builder backend form)
├── data/
│   ├── attributes.xml                (11 attributes per Q2)
│   ├── attribute_values.xml          (seeded from #5 Consolidated Dataset)
│   ├── config_rules.xml              (the 4 declarative rules from Mapping §3.4)
│   ├── pricelists.xml                (6 pricelists per Q1)
│   └── product_templates.xml         (12 templates per Q8)
├── reports/                          (Shop Copy, Door Order, Signature Spec Sheet)
└── demo/                             (Demo Tradesperson + Richwood + 5–10 case-study orders)

southbrook_estimating_website/        ← Three.js customer planner (Phase 3)
├── controllers/
├── views/                            (the /kitchen-planner three-pane page)
├── static/src/                       (Three.js, parametric_carcass.esm.js, dimensioning, catalog tiles)
└── data/                             (public menu)
```

---

## 4 · The custom code register — 8 routines

This is the **complete** list of genuinely custom code in the Southbrook build. Everything else is data, configuration, or OCA module extension via `_inherit`. Adding a ninth routine requires a fresh `PUNCHLIST.md` justification.

| # | Routine | Owns | Why it's custom |
|---|---|---|---|
| 1 | `southbrook_estimating/models/mrp_bom.py::_compute_panel_dimensions` | Parametric panel cut sizes from W×H×D | Can't be a simple variant attribute |
| 2 | `southbrook_estimating/models/product_pricelist.py::_compute_refacing_price` | Refacing channel 35% margin-target | Live-cost-targeting can't be declarative |
| 3 | `southbrook_estimating/models/sale_order.py::_resolve_channel_pricelist` | Auto-assigns pricelist from `res.partner.channel` | Dispatch logic, ~10 lines |
| 4 | `southbrook_estimating/models/product_attribute_value.py::lead_time_extra` + rollup | Maple-box +2-week lead-time bump (Q3) | Mirror of `price_extra` for lead time, summed into `produce_delay` |
| 5 | `southbrook_estimating_website/static/src/js/parametric_carcass.esm.js` | Three.js procedural BufferGeometry | The whole point of the planner layer (Phase 3) |
| 6 | `southbrook_estimating/reports/shop_copy.xml`, `reports/door_order.xml`, `reports/signature_spec_sheet.xml` | Southbrook-format QWeb reports | Layout templates, not logic |
| 7 | `southbrook_estimating/controllers/accucutt_bridge.py` | Accucutt nest hand-off (Phase 4) | External-system integration |
| 8 | `southbrook_plm/` (whole addon) | ECO workflow + template-BoM versioning + parametric cut-spec governance | Re-opens §0 "PLM Absent" — Odoo CE has no native PLM, and the mature free third-party option (OmniaSolutions OdooPLM) is a CAD/PDM suite that fights `product_configurator_mrp`. Boundary justification + Gap-Fit study in `docs/superpowers/specs/2026-05-31-southbrook-plm-gap-design.md`. RC 2026-05-31. |

**Boundary rule:** if a feature would add a 9th file/addon to this list, stop and raise the question in PUNCHLIST. The pattern almost always reveals that the feature belongs in `data/*.xml` (rule), in an OCA module extension via `_inherit` (behavior), or as a QWeb report (presentation). Routine #8's justification — re-opening a §0 locked decision — is the canonical exception shape; future 9-or-more reopenings must clear the same bar.

---

## 5 · The 4 declarative rules

(Detail in `Southbrook_Excel_to_Odoo_Mapping.md` §3.4 — this is a refresher index.)

| Rule | One-liner | Mechanism |
|---|---|---|
| 1 | Series → door style | `product.config.line` domain on `door_style` keyed by `series` |
| 2 | Box material → series (maple gated) | Inverse domain on `box_material` keyed by `series` |
| 3 | Width → door count | `product.config.line` formula: `door_count = 1 if value_inches <= 21 else 2` |
| 4 | Family → soft-close (corner bifold exception) | Domain hiding `soft_close` when `family = corner_bifold` |

All four are encoded in `data/config_rules.xml` as XML records. **Zero Python `if` branches** duplicating these rules. The grep test in `CLAUDE.md` §9 acceptance criteria enforces this.

---

## 6 · The 6 pricelists

(Detail in `Southbrook_Excel_to_Odoo_Mapping.md` §3.2 — this is the architecture summary.)

```xml
<!-- Pseudocode for data/pricelists.xml seed -->

<record id="pricelist_retail" model="product.pricelist">
    <field name="name">Retail (List Price)</field>
    <!-- Base pricelist — others inherit from this -->
</record>

<record id="pricelist_dealer" model="product.pricelist">
    <field name="name">Dealer (−50% off retail)</field>
    <!-- Single global rule: price = retail × 0.50 -->
</record>

<record id="pricelist_tradesperson" model="product.pricelist">
    <field name="name">Contractor (Tiered)</field>
    <!-- Stack: cost × 1.05, then partner.tradesperson_tier multiplier -->
    <!-- tier 1 = 0.75 ; tier 2 = 0.70 ; tier 3 = 0.65 -->
</record>

<record id="pricelist_kd" model="product.pricelist">
    <field name="name">Central KD (Knock-Down Components)</field>
    <!-- ≈46% of retail, component-level pricing (no assembly labor) -->
</record>

<record id="pricelist_bigbox" model="product.pricelist">
    <field name="name">Big-Box Wholesale</field>
    <!-- Fixed $65 cost / $98 retail per SKU — flat override -->
</record>

<record id="pricelist_refacing" model="product.pricelist">
    <field name="name">Refacing (CTHS)</field>
    <!-- Per-SF door pricing, computed to hit 35% target margin off live cost -->
    <!-- Custom routine #2: _compute_refacing_price -->
</record>
```

Channel resolution on `sale.order` (custom routine #3): `_resolve_channel_pricelist` reads `partner.channel` and assigns the matching pricelist at order creation, unless explicitly overridden.

---

## 7 · Phased delivery plan

### Phase 1 · Engine & sales-rep Order Builder (weeks 1–3)

**Deliverables:**
- Both addons scaffolded with manifests, security, menus
- 11 attributes (Q2), all values seeded from #5 Consolidated Dataset (with the maple `lead_time_extra` and `price_extra`)
- 4 config rules from Mapping §3.4
- 6 pricelists with channel resolution
- 12 cabinet templates (Q8 xml_ids) with their BoM skeletons
- Backend Order Builder form: multi-zone grid (Q21 — 6-value selection + `zone_label`), inline config drawer, BoM preview tab, validation tab, stage pipeline
- Demo data: Demo Tradesperson (Tier 3) partner + Richwood partner + the 9-line smoke-test order
- Custom routines 1, 2, 3, 4, 6 in place (routine 5 = Phase 3; routine 7 = Phase 4)

**Phase 1 gate:** John can build the 9-line smoke-test order at Demo Tradesperson (−35%) pricing, see the BoM preview with the maple `+10%` price and `+2 weeks` lead time correctly applied, hit Confirm, watch the MO appear in Manufacturing. Switching the partner to Walk-in Retail re-prices all 9 lines instantly without re-configuration.

### Phase 2 · Customer one-page configurator, 2D first (weeks 4–5)

**Deliverables:**
- The `/kitchen-planner` route (in `southbrook_estimating_website`) — three-pane layout per Prodboard manifest §8.1 (58 + 394 + flex grid)
- Catalog tiles 296×94 with 80×80 Tier-3 SVG thumbnails (per manifest §11 — every cabinet code has a hand-authored SVG)
- Attribute selection panel with live pricing
- Signature Series spec-sheet PDF report (custom routine #6) when "Request a Price" is clicked
- Portal "My Estimates" page (`/my/estimates`)
- Mobile breakpoint: 2D card-stack flow reusing Tier-3 SVGs (Q17)
- Sky/Walnut/Linen palette applied (Q9 — hex values resolved from `SIGNATURE_SERIES_TOKENS.md` when that lands alongside #7)

**Phase 2 gate:** John completes a kitchen end-to-end as a portal user on desktop and tablet, gets the Signature-styled PDF, and sees the draft `sale.order` reach the assigned salesperson with the correct retail pricing applied (since no `partner.channel` is set for a fresh portal user).

### Phase 3 · 3D parametric carcass layer (weeks 6–9)

**Deliverables:**
- Three.js scene mounted in `/kitchen-planner` viewport (custom routine #5)
- Procedural `BufferGeometry` from `(value_mm_*, attribute_values...)`
- The top 5 element types from manifest §5.2 (hinge_block, drawer, delimiter, L_profile, stub_block) implemented in `parametric_carcass.esm.js`. Remaining 13 element types stub-rendered as boxes initially, fleshed out as orders demand
- ACES Filmic tone mapping, sRGB output, 6 lights (1 hemi + 1 directional + 4 point), KTX2/Basis textures, MeshBVH picking
- Automatic dimensioning (solid ↔ blueline toggle)
- Tier-2 runtime-baked thumbnails replacing Tier-3 SVGs progressively (manifest §11 cascade)
- Collision detection between adjacent cabinets in a run (the negative-space differentiator vs Prodboard)
- Browser support: Chrome/Edge/Safari/Firefox latest 2 majors (Q16)

**Phase 3 gate:** The customer flow visually matches the Prodboard reference artifacts on desktop browsers in the target floor. Mobile stays 2D. Picking a cabinet in the viewport opens the right-side inspector. Multi-select with shift-click works. Keyboard dimension entry works.

### Phase 4 · MRP polish & cut-list bridge (weeks 10–12)

**Deliverables:**
- BoM rollup completeness — every configurator selection produces correct panel/door/hardware lines for all 12 templates
- `controllers/accucutt_bridge.py` (custom routine #7): JSON export of panels → Accucutt; JSON ingest of nest result → stored on the MO
- OCA `quality_control` integrated with two control points: incoming material inspection + post-edge-band visual QC
- Shop Copy + Door Order QWeb reports finalized in production format (matching Southbrook's existing visual register)
- Replay of 5–10 real case-study orders from #6 ImageFloor Case Study through the full chain (sale → MO → picking → invoice). Screenshot each stage for the SAMI investment-committee deck

**Phase 4 gate:** The whole demand-to-delivery chain runs against real historical data, producing artifacts indistinguishable from what Southbrook used to do by spreadsheet — except generated, not typed.

### Beyond Phase 4

The AI layer (forecast / nest analytics / quote win-rate / lead-time predictor) is a separate brief. The data spine (§2.1) is captured from Phase 1, so retrofit cost is zero.

---

## 8 · The AI data spine — captured now, modeled later

Phase 1 mandates these analytic tags at every `sale.order` and `mrp.production` confirm-time. They cost nothing to capture, would cost months to backfill later.

| Tag | Source | Used by (later) |
|---|---|---|
| `channel` | `partner.channel` | Channel-mix forecasting, margin analytics by channel |
| `series` | `sale.order.line.product_id.series` (rolled up) | Demand mix by series, quote win-rate by series |
| `tradesperson_tier` | `partner.tradesperson_tier` (when applicable) | Tier conversion analysis |
| `dealer_id` | `partner.id` (when channel = dealer) | Dealer scorecard, dealer LTV |
| `quoted_at` | `sale.order.create_date` | Quote-to-order latency, seasonality |
| `confirmed_at` | `sale.order.date_order` (when confirmed) | Order rate computation |
| `production_start_at`, `production_end_at` | `mrp.production` lifecycle | Actual lead-time vs predicted (the lead-time predictor's training target) |
| `cabinet_count`, `panel_count`, `door_count` | BoM rollup | Resource demand forecasting |
| `nest_yield_pct` | Phase 4 Accucutt result | Sheet-yield optimization training |

These tags go on a `southbrook.order.analytics` companion record per `sale.order` (one-to-one), not on the order itself — keeps the analytic schema independently versionable from the sale order.

---

## 9 · Risks & dependencies

### 9.1 OCA module health

The four ported modules are at 19.0.1.0.0 and were verified clean of blockers (Q18 — 50 markers grepped, all categorized as upstream tech debt, none gating Southbrook scaffold). One marker worth tracking: `product_configurator/models/product_config.py:1500` — `# TODO: Raise ConfigurationError with reason`. Brief §2.2 wants disabled options in sales-rep mode to surface their rule reason. Plan: override the raise site in `southbrook_estimating/models/product_config_line.py` (override path, not in-place patch, per CLAUDE.md §7.7). PR upstream after Phase 1 stable.

### 9.2 The Three.js layer is real engineering, not glue

Phase 3 is genuinely net-new work — `parametric_carcass.esm.js` is custom routine #5 and the single biggest line item in the build. Budget accordingly. Mitigation: ship Phase 2 (2D-only customer flow) first so there is a functional customer one-pager months before Phase 3 lands.

### 9.3 #8 workbook fidelity for seed data

`#5 Consolidated Dataset` is derived from `#8 Southbrook_Cabinetry_Dealer_Kitchen_Program.xls` (18 MB legacy). When #5 is generated from the original workbook the numbers are canonical; when generated illustratively (placeholder mode) the demo data is **not** production-ready and must be re-seeded from the real workbook before any client demo.

### 9.4 The Accucutt envelope is unconfirmed

Phase 4 working assumption documented in Q19. Confirm shape with Accucutt before Phase 4 commits. Mitigation: the nest export is custom routine #7, isolated to a controller — no other module depends on its precise shape, so re-shaping post-Phase-3 has small blast radius.

### 9.5 Signature Series visual fidelity gates Phase 2

Phase 2's spec-sheet PDF must match the Signature Series spec book (#7) closely enough that dealers accept it as a replacement. Token extraction into `SIGNATURE_SERIES_TOKENS.md` happens when #7 lands; until then Phase 2 uses placeholder typography and Phase 1 is unaffected.

### 9.6 The 19.0 release is recent

Some OCA dependencies (e.g. `quality_control` for Phase 4) may not have 19.0 ports yet. **VERIFY** before Phase 4 starts. Mitigation: Phases 1–3 do not require any OCA module beyond the four already ported.

---

## 10 · Cross-references

| Question | Read |
|---|---|
| What's the brief? | `CLAUDE.md` |
| What's the configurator UX target? | `CLAUDE.md` §2 + `PRODBOARD_MANIFEST.md` §3, §8 |
| What's the data model? | `Southbrook_Excel_to_Odoo_Mapping.md` §3 + `PRODBOARD_MANIFEST.md` §4 |
| What are the business rules? | `Southbrook_Excel_to_Odoo_Mapping.md` §3.4 (the four declarative rules) |
| What's the recipe grammar / element vocabulary? | `PRODBOARD_MANIFEST.md` §5 |
| What numbers seed the system? | `Southbrook_Consolidated_Dataset.xlsx` (when generated from #8) |
| What do dealers actually look like operationally? | `Southbrook_ImageFloor_Case_Study.md` |
| What's the visual target for the spec sheet? | `southbrook_book_templates.pdf` + the to-be-authored `SIGNATURE_SERIES_TOKENS.md` |
| What's the layout target for the Order Builder backend? | The (to-be-regenerated) `southbrook_internal_order_builder.html` mock |
| What are the open questions and locked decisions? | `PUNCHLIST.md` § 2026-05-29 |

---

**End of build spec.**
