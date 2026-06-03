# Southbrook Estimating v19CR — Punchlist

> Open questions and blockers, dated and contextual.
> Per `CLAUDE.md` §7.4: open questions go here, never silently into commit messages.

---

## 2026-05-29 · Phase-1 prerequisites (blocking scaffold)

The brief's §1 lists 8 canonical input artifacts. As of workspace bootstrap, only
1 of 8 is local. Scaffold is gated until the remaining 7 land in `docs/` per
the §1 stop-and-ask rule.

| # | Artifact | Status | Source decision |
|---|---|---|---|
| 1 | OCA configurator suite (4 modules @ 19.0.1.0.0) | ✅ in `addons/` | Copied from `~/Downloads/Official/V19C/product-configurator/` |
| 2 | `SAMI_Southbrook_Odoo19_Build_Spec.md` | ✅ in `docs/` (2026-05-29) | Read end-to-end; 11 sections; §0 locked-decisions table + §4 7-routine register + §7 Phase plan internalised |
| 3 | `PRODBOARD_MANIFEST.md` | ✅ in `docs/` (2026-05-29) | Read end-to-end; §4.3 / §5.2 / §6 / §8 / §11 / §15 internalised |
| 4 | `Southbrook_Excel_to_Odoo_Mapping.md` | ✅ in `docs/` (2026-05-29) | Read end-to-end; §3.1–3.5 + §6 smoke test internalised |
| 5 | `Southbrook_Consolidated_Dataset.xlsx` | ✅ in `docs/` (2026-05-29 ILLUSTRATIVE mode); 10 tabs, 117 formulas | Skim done — Price Master + Channel Economics + Pricing Evolution structure confirmed, matches drafts. Minor: value_mm canonical values are 228/304/685/609 vs my draft's 229/305/686/610 (off-by-1 rounding); dataset wins on promote |
| 6 | `Southbrook_ImageFloor_Case_Study.md` | ✅ in `docs/` (2026-05-29) | Read end-to-end; 4 dealer archetypes drive NF6-NF9 Order Builder requirements (Pattern A duplicate-as-draft, B keyboard-first defaults, C width-first toggle, D zones-already-Q21) |
| 7 | `southbrook_book_templates.pdf` (Signature Series) | ⏳ John supplying original | Visual target for the customer one-pager |
| 8 | `Southbrook_Cabinetry_Dealer_Kitchen_Program.xls` | ⏳ John supplying original | Truth source for pricing rules not in #4 |
| † | Amended CLAUDE.md v1.1 (Q1/Q2/Q5/Q7/Q8/Q21 corrections folded) | ✅ at root (2026-05-29); v1.0 archived to `docs/archive/CLAUDE_v1.0_2026-05-28.md` | Read end-to-end; Q1/Q5/Q7/Q8/Q21 corrections confirmed in §3 / §6 / §8 / §9 / §10 |
| ‡ | `southbrook_internal_order_builder.html` mock | ✅ in `docs/` (2026-05-29) | Authoritative visual reference for `views/order_builder_views.xml`; 27 KB self-contained HTML, opens in any browser |
| § | `SIGNATURE_SERIES_TOKENS.md` | ⏳ John authoring alongside #7 | Design tokens extracted from Signature Series spec book |

**Scaffold gate** (§10 step 5): unblock only after #2 (SAMI Build Spec) + #5 + amended CLAUDE.md all in `docs/` (or at root for the amended brief).

## 2026-05-29 · CLAUDE.md contamination guard

A prior session loaded `/Users/naadmin/Downloads/CLAUDE.md` (the AlfaCore one)
as authoritative context. That CLAUDE.md belongs to a different project (Italian-
platform vehicle service, 14 `alfa_*` modules) and must not contaminate this
build.

**Mitigation in place:** the only `CLAUDE.md` in this project root is the
southbrook_estimating brief at `~/southbrook-v19cr/CLAUDE.md`. Working
exclusively from this root keeps Claude Code's directory-walking discovery
contained.

**Outstanding:** confirm no parent-directory `CLAUDE.md` exists at `~/CLAUDE.md`
or `~/Downloads/CLAUDE.md` reaches into this tree. If so, John will move/rename
or use `--no-project-context`.

---

## 2026-05-29 · Locked decisions (21 questions acked by John)

Every decision below was raised as an open question after the §10 step 2/3
reads, then locked by John's authoritative answer. Listed in the same Q-numbering
so future-session traceability is one-to-one. Cite the Q-number in commit
messages where the decision is exercised.

### Phase-1 (manifest + attributes + rules) blockers — all resolved

**Q1 · Pricelist count = 6.** Six `product.pricelist` records: one Retail base
+ five channel pricelists (Dealer / Tradesperson / KD / Big-box / Refacing)
inheriting from Retail. Brief §6 opening to be amended from "five" to "six"
in the next CLAUDE.md regeneration.

**Q2 · Canonical 11-attribute list (Mapping §3.3, not Brief §3).** Lock these
keys for `data/attributes.xml`. Brief manifest comment to drop `collection`
and `interior`.

| Seq | Attribute key | Values |
|---|---|---|
| 1 | `family` | wall, base, drawer, sink, tall, corner, vanity, worktop, accessory |
| 2 | `width` | series-gated snap-grid (per family) |
| 3 | `series` | contractor, contemporary, elegance, signature |
| 4 | `box_material` | white_melamine, maple (Rule 2 applies) |
| 5 | `door_style` | thermofoil_slab, five_piece_woodgrain, custom (Rule 1 applies) |
| 6 | `finish` | colour palette, scoped by series |
| 7 | `hinge_side` | left, right, n_a (hidden when family has no hinged doors) |
| 8 | `finished_sides` | none, left, right, both (drives edge-banding BoM) |
| 9 | `gables` | standard, finished, decorative |
| 10 | `handle` | from handle catalog |
| 11 | `accessories` | multi-select: soft_close, drawer_organisers, pull_outs, … |

**Q3 · Custom register grows to 7 routines.** Add a 7th entry to Mapping §5's
custom code surface: `models/product_attribute_value.py` carrying a new
`lead_time_extra` field on `product.template.attribute.value`, plus a
roll-up call in the BoM that sums extras and sets `produce_delay` accordingly.
Pattern mirrors Odoo's native `price_extra`.

**Q4 · Dimensions: dual storage, imperial display, mm canonical.** Two fields
on `product.attribute.value` for any dimensional attribute:
- `value_inches` — display label ("21″", "24″"); used by Order Builder + workbook + dealers
- `value_mm` — canonical numeric; used by Phase-3 BufferGeometry math
**Do not compute one from the other.** Spec wins over literal conversion
(21″ may be 533 mm by Southbrook spec, not 533.4 mm by 21 × 25.4).

**Q5 · Contractor naming disambiguation.**

| Surface | Technical key | UI label |
|---|---|---|
| `product.attribute.value` (series) | `contractor` | "Contractor Series" |
| `res.partner.channel` selection | `tradesperson` | "Contractor Pricing" |
| `product.pricelist` xml id | `southbrook.pricelist.tradesperson` | "Contractor (Tiered)" |

`grep tradesperson` hits the channel only; `grep contractor` hits the series
only. UI strings preserve workbook vocabulary so Peter Tuschak and dealers
see no rename.

### Phase-1.5 (post-#2-read) decisions — locked ahead

**Q6 · Dynamic variant creation, mandated.** All attributes set
`create_variant = 'dynamic'`. Demo seed instantiates variants by placing a
demo sale order; variants materialise as a side-effect. Phase-1 smoke test
creates exactly 9 `product.product` records (no more).

**Q7 · Smoke-test partner: `Demo Tradesperson (Tier 3)`** with
`channel=tradesperson` + `tradesperson_tier=3` → auto-resolves to
`southbrook.pricelist.tradesperson` with −35% applied. Keep a separate
`Richwood Renovations` partner (`channel=dealer`) for a parallel dealer-pricelist
test. Mapping §6 step 1 to be amended in the next regeneration.

**Q8 · 12 templates locked, exact xml_ids:**
`southbrook.wall_1dr`, `southbrook.wall_2dr`, `southbrook.base_1dr`,
`southbrook.base_2dr`, `southbrook.drawer_bank`, `southbrook.sink_base`,
`southbrook.tall_pantry`, `southbrook.tall_oven`, `southbrook.corner`,
`southbrook.vanity`, `southbrook.accessory`, `southbrook.worktop`.
The brief's "end_panel / filler" split collapses into a single
`southbrook.accessory` template with an `accessory_type` sub-attribute
(values: `end_panel`, `filler`, `cornice`, `pelmet`, `plinth`).

### Phase-2 / Phase-3 decisions — locked ahead

**Q9 · Sky/Walnut/Linen palette** — defer hex values. Phase-1 SCSS uses
variable names (`$sky`, `$walnut`, `$linen`, `$carbon`) with TBD; tokens
resolved when `SIGNATURE_SERIES_TOKENS.md` lands alongside #7.

**Q10 · Internal Order Builder mock** to be regenerated in the next batch
with #2/#5/#6. Until it lands, sketch view XML from Brief §2.2 prose
(multi-zone grid, inline config drawer, BoM preview tab, stage pipeline,
channel-pricelist header strip). Do not block on it.

**Q11 · Signature Series tokens markdown** to be authored by John alongside #7.
QWeb authoring stays mechanical, not interpretive.

**Q12 · Hardware brand: `blum` default.** New attribute `hardware_brand`
with values `blum, hettich, grass, salice, generic`. Workbook (#8) confirms.

**Q13 · Hinge as separate attribute.** `hinge_type` selection
(`standard, soft_close, soft_close_premium`), independent of door family.
Rule 3's math stays pure: `door_count × 1 hinge_pair` regardless of hinge upgrade.

**Q14 · `__cat_kitchen-cabinets` (25 modules) — skip in Phase 1.**
Re-evaluate in Phase 3 when 3D catalog import is concrete. Manifest §13 Q7
flagged role as unclear.

**Q15 · Don't replicate Prodboard's `filter_linear`.** Rule 1 + standard
Odoo `product.config.line` domains are equivalent. If a Phase-3 option
case can't be expressed in the domain system, add a Punchlist entry then.

**Q16 · Browser support floor**: Chrome / Edge / Safari latest 2 majors,
plus Firefox latest 2 as courtesy. ~95% desktop coverage. WebGL2 + KTX2
universally supported on target floor; no polyfills.

**Q17 · Mobile 2D fallback** reuses the Tier-3 SVG cascade from
Manifest §11. No separate 2D-isometric track. Phase-3 desktop 3D layer
is additive; mobile fallback = Phase-2 customer flow on narrow viewport.

### Operational decisions

**Q18 · OCA tech-debt scan (executed 2026-05-29).** 50 markers found:
- `product_configurator`: 49 markers (8 wizard/, 8 product_attribute, 16 product_config, 17 tests/views)
- `product_configurator_mrp`: 1 marker (demo data note)
- `product_configurator_sale`: 0
- `website_product_configurator`: 0

**None are blockers.** Test markers are upstream test-quality debt;
model markers are upstream enhancement notes (OR operators between
`implied_ids`, sequence on `implied_ids`, circular-dependency guard).
The four declarative rules from Mapping §3.4 do not require OR composition,
do not require ordered evaluation, and have no cycles — verified manually.

**One marker worth tracking** for Brief §2.2 compliance:
- `product_configurator/models/product_config.py:1500` —
  `# TODO: Raise ConfigurationError with reason` — sales-rep mode must surface
  the rule reason on disabled options. Decision: when implementing the
  Order Builder, if this TODO is still open upstream, override
  `_check_value_attributes` (or wherever the ConfigurationError raises) in
  `southbrook_estimating/models/product_config_line.py` to attach the
  reason string. PR upstream after Phase 1 stable.

**Q19 · Accucutt envelope — defer to Phase 4.** Working assumption:
panels-out = list of `{sku, length_mm, width_mm, thickness_mm,
edge_band_sides, grain_direction, material, quantity}`; nest-in =
list of `{sheet_id, panels: [{panel_id, x, y, rotation}]}`. Confirm with
Accucutt before Phase-4 commits.

**Q20 · Co-development cadence = (b) per-phase ack.** Intra-phase commits
autonomous if they don't touch architecture. Written commit-log summary
at the end of each scaffold session. Raise to John when architecture is
touched mid-phase.

**Q21 · Zones = 6-value selection on `sale.order.line` + free-text
`zone_label`.** Values: `base_run`, `wall`, `tall`, `island`, `accessory`,
`other`. `zone_label` is visible only when `zone=other`. No separate ORM
model; view uses Odoo's standard `<group expand="1" string="Zone">` pattern.

---

## 2026-05-29 · Q18 follow-up — OCA line-1500 TODO is NOT a blocker

Reading the surrounding context (`addons/product_configurator/models/product_config.py:1490–1514`)
showed that the `validate_configuration` method **already returns a structured
`{reason: <text>}` dict on failure**. The TODO is about converting the
dict-return into a raised `ConfigurationError` — a style change, not a missing
capability. Brief §2.2 ("rule reason visible to sales rep") works fine with the
dict-return: the Order Builder reads `result['reason']` and surfaces it on the
disabled option's tooltip. **No `southbrook_estimating` override needed.** Q18
status updated from "track + override later" to "no action — direct dict consumption sufficient".

---

## 2026-05-29 · NEW AMBIGUITIES surfaced during OCA reconnaissance

These were discovered while drafting `docs/drafts/config_rules_DRAFT.xml`. Both
have a recommended resolution baked into the draft; both require John's ack
before promotion to `addons/`.

### Q22 · Rule 3 — `door_count` derivation: declarative or computed?

Mapping §3.4 Rule 3 says "Encoded as a `product.config.line` rule that sets
door_count = `1 if width <= 21 else 2`". But OCA's `product.config.line`
semantics are **restrict-values, not set-derived-value**. For Rule 3 to be a
config.line, `door_count` must be an attribute on the template — but it's NOT
in Q2's locked 11-attribute list.

Two viable resolutions:
- **(a) door_count as hidden 12th attribute** — `display_type='hidden'`,
  values `[1, 2]`. Rule 3 fires via width-band domain → config.line restricting
  door_count to the matching single value. Pattern-consistent with Rules 1, 2, 4.
- **(b) door_count as computed BoM field** — Rule 3 moves out of `config_rules.xml`
  and into `models/mrp_bom.py::_compute_panel_dimensions`. Enforced at BoM
  materialisation, not configurator-selection time.

**Draft assumes (a).** Recommended for declarative consistency, BoM-preview
visibility (Brief §2.2), and custom register staying at 7 routines. If John
acks (b), `attributes_DRAFT.xml` loses the `door_count` attribute block and
`config_rules_DRAFT.xml` loses its Rule-3 section.

### Q23 · Rule 4 — `corner_bifold` family encoding

Mapping §3.4 Rule 4 references "bi-fold corner cabinets" as a distinct family,
but Q2's locked family attribute values list only `corner` (no `corner_bifold`).

Two viable resolutions:
- **(a) Split `corner` into `corner_standard` + `corner_bifold`** — family attribute
  grows to 10 values. Rule 4 fires on `family ∈ {corner_bifold}`.
- **(b) Add `family_subtype` sub-attribute scoped to `family=corner`** — values
  `standard / bifold`. Rule 4 fires on `family_subtype ∈ {bifold}`. Family
  stays at 9 high-level values.

**Draft assumes (b).** Recommended because bi-fold is a structural sub-choice
within `corner`, not a parallel family; pattern extends to future cases
(drawer-bank pull-out vs deep-drawer); 9-value family list matches dealer
vocabulary. If John acks (a), `attributes_DRAFT.xml` drops the `family_subtype`
attribute block and adds `corner_bifold` to family values.

---

## 2026-05-29 · Drafts staged in `docs/drafts/` (gate-respecting prep)

Three artifacts pre-written, **not** in `addons/`. Promote to canonical
locations after artifacts #2/#5/amended-CLAUDE.md land + Q22/Q23 acked.

| Draft | Promotes to | Records / LoC | Notes |
|---|---|---|---|
| `RULE_ENCODING_NOTES.md` | Reference doc (stays in docs/) | 208 lines | OCA syntax reconnaissance; cite from commit messages |
| `attributes_DRAFT.xml` | `addons/southbrook_estimating/data/attributes.xml` | 14 attrs + 50 values (Q8 accessory_type added 2026-05-29) | Width values carry inline `value_mm` comments; promotion adds the dual-storage fields from `product_attribute_value_DRAFT.py` |
| `config_rules_DRAFT.xml` | `addons/southbrook_estimating/data/config_rules.xml` | 19 records (pattern shown) | Promotion expands to ~65 records covering all 10/12 templates; macro script recommended |
| `product_templates_DRAFT.xml` | `addons/southbrook_estimating/data/product_templates.xml` | 12 templates + ~28 attribute_lines (pattern shown for wall_1dr/wall_2dr/drawer_bank/sink_base/tall_pantry/tall_oven/corner/vanity/accessory/worktop) | Q8 xml_ids locked; promotion replicates the wall_1dr attribute_line pattern across the other 9 cabinet templates (10+ records each), then re-validates ref() targets |
| `pricelists_DRAFT.xml` | `addons/southbrook_estimating/data/pricelists.xml` | 9 pricelists + 7 items | 6 base + 3 tradesperson sub-tiers (1/2/3) per Q5/Q21 — see NF5 for the dispatch ambiguity |
| `product_attribute_value_DRAFT.py` | `addons/southbrook_estimating/models/product_attribute_value.py` | 56 LoC (routine #4) | Q3 lead_time_extra + Q4 value_inches/value_mm |
| `southbrook_order_analytics_DRAFT.py` | `addons/southbrook_estimating/models/southbrook_order_analytics.py` | 162 LoC | NF1 companion model + capture hook; ack needed on whether this counts as routine 8 |
| `PHASE_1_FIRST_5_COMMITS.md` | Posted as message to John at scaffold gate | n/a (now 6-commit plan) | Adjusts after #5 lands with real $-values |

All XML drafts parse cleanly under `xml.etree.ElementTree`. Both Python drafts
compile cleanly under `py_compile`. Promotion is mechanical: copy → renumber
external IDs from `southbrook.*` → `southbrook_estimating.*` → verify
cross-references resolve.

---

## 2026-05-29 · NF5 surfaced while drafting pricelists

### NF5 · Tradesperson channel — base pricelist reachable, or tier-required?

Build Spec §6 specifies:
- `pricelist_tradesperson`: cost × 1.05 floor (the base)
- 3 tier multipliers (×0.75 / ×0.70 / ×0.65 for tiers 1/2/3)

My pricelists draft seeds **4** records for the tradesperson channel:
the cost-floor base + 3 tier sub-pricelists inheriting from it. The
`_resolve_channel_pricelist` routine (commit 4) dispatches:

```python
if partner.channel == 'tradesperson':
    if partner.tradesperson_tier == '1':
        return pricelist_tradesperson_tier_1
    if partner.tradesperson_tier == '2':
        return pricelist_tradesperson_tier_2
    if partner.tradesperson_tier == '3':
        return pricelist_tradesperson_tier_3
    return pricelist_tradesperson  # ← untiered fallback
```

**Question:** is the untiered fallback ever a real case (some
tradesperson partners with `channel=tradesperson` + `tradesperson_tier=False`),
or is tier always required for the channel?

If always required: drop `pricelist_tradesperson` base from the seeded
pricelists; constraint `tradesperson_tier` to required when
`channel=='tradesperson'`. If sometimes untiered: keep the base as a
reachable record exposing the +5% floor only.

**Recommendation:** keep the base reachable for now (defaults are easier
to remove than to add). Add a `tradesperson_tier` SQL-constraint that
gracefully allows null but emits a soft warning at order creation if
unset. Flag in the next batch for ack.

---

## 2026-05-29 · Five explicit acks from John (gate-opening batch)

All five questions resolved by John in the gate-opening reply. Quoted verbatim
where useful; rationale captured for downstream auditability.

**Q22 → (a) hidden 12th attribute, locked.** *"product.config.line is
restrict-values-only, so door_count has to be an attribute for Rule 3's
mechanism wording to be honest."* Draft assumption holds.

**Q23 → (b) family_subtype sub-attribute, locked.** Q2's 9-value family lock
wins over Build Spec §5 row 4's `family = corner_bifold` shorthand (the
shorthand to be amended in CLAUDE.md v1.2). family_subtype only on corner
template; values `standard / bifold`.

**NF1 → analytics model does NOT bump the register.** Routine count stays
at 7. *"data-capture schema does not count against [the register]. If the
analytics model ever grows methods that compute anything beyond rollup-from-
existing-fields, that's the signal to revisit the boundary."* Carve-out
explicit; document in commit 6 PR description.

**NF2 → ship the override stub in commit 5.** Belt-and-suspenders. One stub
file, three lines of effective logic, changes behavior in one place if OCA
upstream switches the contract.

**NF5 → keep `pricelist_tradesperson` base reachable; default tier 3.**
- Base record stays reachable
- `tradesperson_tier` is nullable on `res.partner`
- `_resolve_channel_pricelist` returns base when tier is null
- Default `tradesperson_tier='3'` on new tradesperson partners (matches the
  workbook entry tier; confirmed by Pricing Evolution tab footer)
- Soft warning at order creation only if tier is explicitly unset

**NF3 deferred.** Stale `contractor` channel reference in CLAUDE.md §3 line
127 to be cleaned up in v1.2 patch alongside Q23 shorthand amendment. Non-
blocking — §6 channel table is canonical.

---

## 2026-05-29 · Findings from #6 ImageFloor Case Study

Four operating patterns (A-D) map to four Order Builder requirements with
schema implications for commits 8-9. All confirmed to be additive to the
existing 7-routine register (none add custom logic; all are field+view+default
plumbing).

### NF6 · Pattern A — Image Floor iterative-design schema

The Image Floor rep revises kitchens 3× before order. **Order Builder needs
"Duplicate as Draft"** action that copies an existing sale.order, links
parent_order_id, increments version.

**Schema impact (commit 8):**
- `sale.order.parent_order_id` Many2one self-reference (ondelete='set null')
- `sale.order.version` Integer (default=1, auto-increments on duplicate)
- Server action "Duplicate as Draft" in views/order_builder_views.xml
- Free side-effect: revision history queryable via parent_order_id chain

### NF7 · Pattern B — Amazing Window keyboard-first defaults

The Amazing Window rep needs a 30-minute close window. Defaults pre-filled
where possible: Contractor + thermofoil_slab + white_melamine is the 80%
case → make it the actual default.

**Schema impact (commit 8 + commit 5 attribute_value extension):**
- `res.users.southbrook_default_series` Selection field (per-user override)
- `product.template.default_get` override pre-populates the configured-product
  defaults for non-Signature templates

**Boundary check:** the default_get override is a one-line dispatcher reading
user preference. Not custom logic — config dispatch. Register stays at 7.

### NF8 · Pattern C — Pro Finish width-first entry mode

Width-first toggle: user enters width before family; system suggests the
matching template.

**Schema impact (commit 9):**
- `res.users.southbrook_width_first_mode` Boolean (per-user preference)
- View conditional in order_builder_views.xml: when True, width attribute
  surfaces above family on the inline drawer
- No schema change beyond the user-preference flag

### NF9 · Pattern D — Richwood multi-zone aware grid

Already locked by Q21 (zone selection on sale.order.line + 6 values + zone_label
free-text). Pattern D confirms the 5 named zones (BASE_RUN, WALL, TALL,
ISLAND, ACCESSORY) cover ~95% of Richwood orders; the 6th value (OTHER) +
zone_label covers the remainder. No new finding — Q21 stands.

### Demo seed composition (commit 11) — finalised from case study §5

`demo/southbrook_demo.xml` seeds:
- 4 dealer partners (Image Floor, Amazing Window, Pro Finish, Richwood) at `channel=dealer`
- 1 tradesperson partner (Demo Tradesperson) at `channel=tradesperson`, `tradesperson_tier=3` — the smoke-test target
- 6 open quotes spanning dealers and series (Quote Log seed from #5)
- 5 confirmed orders spanning all channels (Orders Summary seed from #5)
- Linked `crm.lead` records for the 6 open quotes

---

## 2026-05-29 · Phase 1 modeling — NF10, NF11, NF12 surfaced during commits 3-5

### NF10 · #5 Price Master "Maple box uplift" row has columns swapped

Discovered while seeding `data/attributes.xml` in commit 3. The dataset's
Price Master tab footer row reads:

```
Maple box uplift | +10% | N/A (white only) | +10% available | +10% available
                   Contractor   Contemporary    Elegance         Signature
```

Cross-checked against Mapping §3.4 Rule 2:
- Contractor: white only (no maple) → row should be "N/A (white only)" not "+10%"
- Contemporary: maple available → row should be "+10% available" not "N/A"
- Signature: maple **standard** → row should reflect "maple standard" not "+10% available"

Illustrative-mode artifact per Build Spec §9.3. **Does not block** because
the real per-series gating is enforced by Rule 2 in `config_rules.xml`
(commit 5/7), not by per-series `price_extra` on the maple attribute value.
`value_box_maple` in commit 3 has lead_time_extra=14 but no price_extra —
correct, because the +10% applies only when maple IS selected (Rule 2
ensures it can only be selected on Contemporary/Elegance), and the
+10% is applied as a single price_extra value, not as a per-series matrix.

Surface in next #5 regeneration (or in the canonical re-seed when #8 lands).

### NF11 · `lead_time_extra` placed on master (product.attribute.value)

Diverges from Q3 wording ("on product.template.attribute.value"). Rationale
in commit 3 message + `models/product_attribute_value.py` docstring. Phase-1
implication: simpler seeding; one master record sets the bump for all
templates.

**Phase-2/4 escape hatch (acked 2026-05-29):** if Phase 4's Accucutt
integration surfaces template-specific lead-time variation (e.g. a
maple sink_base finishing in +3 weeks not +2 because of the cutout work),
the fix path is:

1. Add a sibling `lead_time_extra` field on `product.template.attribute.value`
   (the variant) with a computed default that reads from the master
   `product.attribute.value.lead_time_extra` as fallback.
2. Update the `mrp.bom._compute_southbrook_lead_time_extra` rollup to
   prefer the variant value when set, falling back to master otherwise.
3. Migration: zero data movement — existing master records continue to
   work, variant records only need creating for the per-template overrides.

Total cost ~30 LoC. Trigger: any time a real per-template variation
surfaces in the field, in a customer support call, or in Phase-4
manufacturing data. Until then, master-only is the right call.

### NF12 · XML lint should be in pre-commit

Surfaced when commit 5's `config_rules.xml` shipped with a malformed
comment block (`--grep` is illegal inside an XML comment). Test suite
didn't catch it because the data file failed to parse at install-time,
not at test-time. Fixed in `fix:` follow-up commit.

**Action item:** when pre-commit is configured (likely commit 11 or start
of Phase 2), include `xmllint --noout` or `python -c "ET.parse(...)"` on
every `*.xml` in the data directories. Cost: zero. Benefit: catches every
class of XML malformation before commit, not at install.

**Resolution (commit 7):** `scripts/lint-xml.sh` shipped — walks
`addons/southbrook_estimating*` and parses every `*.xml` with
`xml.etree.ElementTree`. Manual invocation today (`./scripts/lint-xml.sh`
before XML-touching commits); wires into a real pre-commit framework
in Phase 2 without behaviour change.

### NF13 · Method-body truncation is a class of slip py_compile can't catch

Surfaced 2026-05-30 as a **false alarm** during the modeling-layer review:
review of pasted code suggested `_onchange_partner_id_southbrook_pricelist`
had lost its body between commits 4 and 6. Disk read + Python AST inspection
proved the method was intact. The actual cause was likely a paste-rendering
artifact (markdown collapsing contiguous indented lines under the for-loop).

**But the class of slip is real.** Python `compile()` accepts a method with
`for x in self:` + a single `if guard: continue` body as syntactically valid
even if the rest of the intended body got truncated. The method returns None
and has a valid empty body after the guard; no exception. `py_compile` is
satisfied, but the semantic intent is gone.

**Mitigation (process):** every new model method introduced in a commit
SHOULD have at least one direct unit test asserting it does something
**observable**, not just that it can be called. The test is the proof
of behaviour; the compile-check is only the proof of syntax. When commit
N adds method M2 to a class that already has M1, commit N's test plan
includes a behavioural regression assertion on M1.

**Mitigation (this build, commit 7):** added
`test_pricelist_resolution.test_10_onchange_partner_id_resolves_pricelist`
that exercises the onchange via the `Form` harness and asserts the
resulting `order.pricelist_id` matches the resolver output. Even though
the bug it tests for didn't exist, the test catches the class.

**Discipline-D (Claude-side):** when I report a bug in code I haven't run,
the report shape is "I see X in the paste, that looks wrong, can you
confirm on disk?" — not "this is a real bug, fix it." Disk wins over
rendered paste. Cost of asking before asserting: one round-trip. Cost
of asserting broken-when-fine: a wild-goose-chase commit + a tax on
trust. Applies going forward.

---

## 2026-05-30 · NF14 — Geometric conventions for `_compute_panel_dimensions`

Surfaced before writing routine #1 (commit 8). Mapping section 3.5 lists
the 11-component quantity table but the per-panel L×W×T formulas are NOT
in any canonical artifact. The numbers below are assumed from
cabinetmaking convention; canonical #8 workbook (Peter Tuschak's actual
panel-cutting formulas) is the truth source when it lands.

Following the same data-shape-vs-data-fidelity discipline as the
`southbrook.seed_mode='illustrative'` flag: assumptions are explicit so
the divergences become focused PRs when #8 is parseable.

### Construction style

**Frameless / euro-style** (ASSUMED — awaiting #8 confirmation). Top + bottom
panels capture **between** the L and R sides (cabinet width = sides_width × 2
+ inside_width). Sides run the full cabinet height. This matches
mid-market Canadian cabinetry convention and is what the Image Floor
case-study supports anecdotally, but the workbook is the canonical source.

Alternative (face-frame) would push top/bottom over the sides, change
inside_width calculation, and add face-frame components. If #8 specifies
face-frame, NF14 entry gets rewritten and routine #1 formulas swap.

### Material thicknesses (ASSUMED)

| Material | Thickness | Used by |
|---|---|---|
| Box (carcass) | 15.875 mm (5/8") | side, top, bottom, shelf panels |
| Back | 6.35 mm (1/4") | back panel |
| Door | 18.0 mm (3/4") | door, drawer-front cuts |

If #8 specifies different thicknesses (e.g. 18mm box per metric standard),
update the BOX_TH / BACK_TH / DOOR_TH constants in `models/mrp_bom.py`
and re-run tests. Tests use re-derivation pattern so they tolerate constant
changes without expected-value updates.

### Back panel mounting (ASSUMED)

Captured into a **6.35 mm rabbet groove** routed on the inside back edge
of side / top / bottom panels. Back panel sizes:
- `back_length_mm = (cabinet_width - 2*BOX_TH) + 2*RABBET`
- `back_height_mm = (cabinet_height - 2*BOX_TH) + 2*RABBET`

Where `RABBET = 6.35 mm`.

### Shelf

- **Quantity**: 1 if `cabinet_height_mm <= 600`, 2 if `<= 900`, else 3.
- **Tolerance**: 1.5 mm subtracted from inside_width for hand placement.
- **Ventilation gap**: 12.7 mm (1/2") subtracted from depth at the back.
- Material: same as box.

### Door reveal

**3 mm uniform gap** on all four edges (ASSUMED). Single-door:
`door_width = cabinet_width - 2*REVEAL`. Two-door:
`door_width = (cabinet_width - 3*REVEAL) / 2` (centre reveal between doors).

### Toe-kick

**101.6 mm (4")** integrated into the side panels (sides extend below the
bottom panel by toe-kick height; no separate component). Applies to:
**base, sink_base, tall, vanity** families. Wall and accessory families
have no toe-kick.

**Toe-kick interpretation (clarified at commit 9 review):** `height_mm`
is the **total cabinet height including any integrated toe-kick**. For a
nominal 30" base cabinet, `height_mm=762` already includes the 102mm
toe-kick section. Side panels are cut at the full height (e.g. 762mm),
which is 102mm of toe-kick + 660mm of cabinet body; the bottom panel is
positioned 102mm above the floor of the sides at assembly time.

The `TOEKICK_H` constant + `TOEKICK_FAMILIES` set are therefore
**informational only** at the panel-cut level — they're used by
assembly drawings and shop-floor work-orders downstream, not by
`_compute_panel_dimensions`. If #8 specifies toe-kick as additional to
nominal cabinet height, swap the formula to:

```python
side_L = (height_mm + (TOEKICK_H if family in TOEKICK_FAMILIES else 0),
          depth_mm, BOX_TH)
```

and update this NF14 entry.

### Edge banding (Phase-1 scalar; Phase-4 per-edge)

For Phase 1, `edge_banding_length_mm` is computed as a scalar perimeter
sum based on the `finished_sides` attribute. The precise per-edge
mapping (which edges of which panels get which banding colour) is
**deferred to Phase 4** when the Accucutt nest hand-off needs per-edge
specification. Phase 1 BoM emits banding as a single line item with the
total length.

### Hardware quantities (per Mapping section 3.5)

| Component | Quantity rule |
|---|---|
| Hinge pair | 1 per door |
| Handle | 1 per door (or per drawer front) |
| Drawer slide pair | 1 per drawer |

### Drawer bank Phase-1 simplification (ASSUMED)

Real-world drawer banks typically have a **fixed count** (3-drawer or
4-drawer per family), not a width-derived count. Phase 1 treats
drawer_bank's `door_count` as the drawer-front count using Rule 3
(width → 1 or 2). This is a Phase-1 simplification; the canonical #8
workbook will likely require splitting drawer_bank into `drawer_bank_3`
and `drawer_bank_4` template variants. Flagged for Phase 2.

### What's NOT in NF14 (will surface as NF15+ when needed)

- Hinge-cup drilling spec (depth, X/Y offsets) — Phase 2 hardware spec
- Shelf-pin hole spacing — Phase 2
- Drawer-box construction details (slides, dovetails, etc.) — Phase 2
- Face-frame conversion if #8 says we're not frameless — would invalidate the construction-style assumption above

When #8 lands and any of these assumptions is wrong, the fix path is
mechanical: update the named constant + formula in `_compute_panel_dimensions`,
update this NF14 entry with the canonical value, re-run tests
(re-derivation-style, so they tolerate constant changes).

---

## 2026-05-30 · NF15 — QWeb report styling pre-#7-spec-book

Surfaced at commit 10. The three QWeb reports (`signature_spec_sheet`,
`shop_copy`, `door_order`) need styling matching the Signature Series
visual register, but `SIGNATURE_SERIES_TOKENS.md` is gated on John's
local file drop of #7 (the Signature Series spec book PDF). Canonical
hex values, fonts, and typographic measure can't land yet.

**Mitigation (commit 10):** named CSS variables with TBD-marked
placeholder values in `reports/southbrook_report_styles.xml`. Same
discipline as NF14's named-constants pattern for the panel-math
formulas. When `SIGNATURE_SERIES_TOKENS.md` is authored, the swap is a
single-file variable update:

```
:root {
  --southbrook-walnut:       #5C4033;   /* TBD - update from #7 */
  --southbrook-sky:          #C8D9E0;   /* TBD - update from #7 */
  --southbrook-linen:        #F8F4EC;   /* TBD - update from #7 */
  --southbrook-display-font: "Cardo", "Times New Roman", serif;  /* TBD */
  --southbrook-body-font:    "Source Sans Pro", ... ;  /* TBD */
  /* ... */
}
```

The Sky / Walnut / Linen palette names are stable (locked at Q9). Only
the hex values + font stacks need confirmation. Until #7 lands, the
reports render with the TBD values (which were chosen as a reasonable
warm-cabinetry aesthetic but are explicitly not the canonical brand
colours).

**No PUNCHLIST blocker.** Phase-1 gate review can proceed with
TBD-styled reports — the data binding is correct, the templates render,
the discipline is honest about what's canonical and what's placeholder.

---

## 2026-05-30 · 2 drafts still in `docs/drafts/` (reference only)

After commit 10 promotions, the drafts directory holds only the two
reference docs:

- `RULE_ENCODING_NOTES.md` — OCA syntax reconnaissance (stays)
- `PHASE_1_FIRST_5_COMMITS.md` — early commit plan (now superseded by
  reality of 16 commits 0a → 11b, kept for forensic comparison of plan
  vs execution; see `docs/PHASE_1_RETROSPECTIVE.md` §3 for the analysis)

All promote-target drafts have promoted to `addons/southbrook_estimating/`
and been removed from `docs/drafts/`.

---

## 2026-05-30 · Phase 2+ deferred items inventory

Consolidated list of everything Phase 1 deferred, with reason and target
phase. Phase 2 planning starts from this inventory rather than
re-deriving from scratch.

| # | Item | Origin | Reason for deferral | Target phase |
|---|---|---|---|---|
| 1 | Drawer-bank split into `drawer_bank_3` / `drawer_bank_4` | NF14 drawer simplification | Real-world drawer banks have fixed 3/4 drawer count, not Rule-3 width-derived. Phase 1 reuses door_count via Rule 3 as a simplification | Phase 2 (template split + attribute_line refactor) |
| 2 | Per-template `lead_time_extra` variant field | NF11 escape hatch | Q3 wording said variant; Phase 1 used master for simplicity. Trigger: Phase 4 Accucutt surfaces template-specific lead-time variation (e.g. sink_base maple = +3wks not +2) | Phase 2/4 trigger-based |
| 3 | Edge banding per-edge mapping | NF14 + Mapping §3.5 | Phase 1 emits scalar perimeter sum; per-edge mapping needs Accucutt nest spec | Phase 4 (Accucutt routine #7) |
| 4 | Multi-order door batched-supplier aggregation | Build Spec §4 routine #6 + Mapping §3.7 | Phase 1 ships per-SO door order; original workbook batched across all open orders | Phase 2 (sibling report or wizard) |
| 5 | Per-panel cut diagrams in Shop Copy | Build Spec §4 routine #6 | Needs SVG tooling for the diagram emission | Phase 2 (SVG tooling) |
| 6 | Signature Series visual register (cover page, typography) | NF15 | Gated on #7 (Signature Series spec book PDF) for canonical tokens | Phase 2 (gated on #7) |
| 7 | `_compute_panel_dimensions` Phase-4 lead-time variation | NF11 + NF14 | If Accucutt surfaces template-specific lead-time | Trigger-based, Phase 4 |
| 8 | `parametric_carcass.esm.js` (routine #5) | Build Spec §4 | Three.js procedural BufferGeometry — the planner-layer custom routine | Phase 3 |
| 9 | `accucutt_bridge.py` (routine #7) | Build Spec §4 | External-system integration; nest envelope confirmation needed | Phase 4 |
| 10 | 25 `__cat_kitchen-cabinets` modules from Prodboard manifest | Manifest §13 Q7 (skipped in Phase 1) | Role of the 25 unclear; deferred per Q14 acked decision | Phase 3 re-evaluation when 3D catalog import is concrete |
| 11 | NF8 width-first toggle UI implementation | NF8 (schema shipped commit 9) | Case Study §3.C says "user preference flag + view conditional"; the conditional isn't wired yet | Phase 2 polish |
| 12 | Toe-kick "additional to height" interpretation | NF14 (commit 9 clarification) | Phase 1 uses "height includes toe-kick"; #8 may specify otherwise | Triggered by #8 |

### Companion: items NOT in this list

The following surfaces were considered for deferral but explicitly
stayed in Phase 1 scope and shipped complete:

- The four declarative configurator rules (Mapping §3.4 / commit 5+7) — all 65 records
- The 12 cabinet template `xml_id`s + their 132 attribute lines (commit 7)
- The 6 channel pricelists (commit 4) + 3 tradesperson sub-tiers
- The 11 (+3) configurator attributes (commit 3)
- Custom routines 1, 2, 3, 4, 6 (partial)
- Q21 zone visual grouping (commit 9.5 closed it)
- The smoke-test stub framework (commit 7) + all 10 step promotions (commit 11b)

---

## 2026-05-30 · Loaded but cold — Phase 1 surfaces awaiting Phase 2 activation

Surfaces with schema + code + tests but no demo data or UI exercise
at the gate review. Phase 2 picks them up rather than re-inventing them.
See `docs/PHASE_1_RETROSPECTIVE.md` §5 for the rationale and the
activation paths.

| Surface | What's loaded | Activation path |
|---|---|---|
| Refacing channel margin-target (routine #2) | `pricelist_refacing` + `_compute_refacing_price` + tests | Add a CTHS refacing demo partner + small refacing order |
| NF7 `res.users.southbrook_default_series` | Field + default `contractor` + tests | Wire configurator wizard `default_get` to read it |
| NF8 `res.users.southbrook_order_entry_mode` | Field + default `family_first` + tests | Wire inline-drawer xpath conditional |
| Big-box channel pricelist | `pricelist_bigbox` ($98 fixed) + tests | Add Home Depot / Lowe's demo partner |
| NF6 parent_order_id chain UI | Schema + action + button + test_09 placement assertion | Add chain-visualisation widget on form |
| KD channel pricelist | `pricelist_kd` (54% off) + tests | Add KD demo partner with component-only line shape |

---

## 2026-05-29 · 11 drafts staged total

Final count for tonight's preparation pass — within the §10 step 7 gate.

| Type | Files | Lines |
|---|---|---|
| Reference docs | 2 | 443 |
| XML drafts | 4 | 1,229 |
| Python drafts | 2 | 218 |
| **Total in `docs/drafts/`** | **8** | **~1,890** |

When the gate opens (#5 illustrative + Q22(a)/Q23(b) ack), commits 1-6
promote these drafts and ship. Commits 7-11 deliver the remaining Phase 1
surface (full templates, BoM math, Order Builder views, smoke test, demo).

---

## 2026-05-29 · Findings from #2 (SAMI Build Spec) + amended CLAUDE.md v1.1

Four findings surfaced cross-checking the new artifacts against the existing
locked decisions and drafts. **None are blockers**; all are pre-scaffold
adjustments folded into the next draft pass.

### NF1 · NEW MODEL — `southbrook.order.analytics` (Build Spec §8)

Build Spec §8 introduces a companion record per `sale.order` capturing 9 analytic
tags at confirm-time:

| Tag | Source |
|---|---|
| `channel`, `series`, `tradesperson_tier`, `dealer_id` | `res.partner` rollup |
| `quoted_at`, `confirmed_at`, `production_start_at`, `production_end_at` | sale.order + mrp.production lifecycle |
| `cabinet_count`, `panel_count`, `door_count` | BoM rollup |
| `nest_yield_pct` | Phase 4 Accucutt result |

§8 line 308 explicit: *"a `southbrook.order.analytics` companion record per
`sale.order` (one-to-one), not on the order itself — keeps the analytic schema
independently versionable from the sale order."*

**Implication for the custom-register boundary (Build Spec §4):**
- A thin model with fields + a confirm-time write hook = **data capture**, not
  business logic.
- The 7-routine register lists **logic** files (custom math, custom dispatch).
- My read: `models/southbrook_order_analytics.py` does not bump the register
  count, because it adds a record shape + a `_inherit` hook on
  `sale.order.action_confirm` that writes a row.
- **Requires John ack** before promotion. If the register grows to 8, document
  the carve-out here.

**Phase 1 impact:** add a 6th commit (or fold into commit 4) for the analytics
model + the confirm-time write hook. Tag values come from existing
`res.partner` + `sale.order` fields — no new business rule needed.

### NF2 · Conservative override of `product_config.py:1500` (Build Spec §9.1)

My Q18 follow-up concluded "no override needed" because the OCA
`validate_configuration` method already returns a structured `{reason: ...}`
dict. The Build Spec §9.1 takes a **more conservative position**:

> *"Plan: override the raise site in `southbrook_estimating/models/product_config_line.py`
> (override path, not in-place patch, per CLAUDE.md §7.7). PR upstream after
> Phase 1 stable."*

**Reconciled position:** ship the override stub in commit 5 even though the
dict-return is sufficient for Brief §2.2 compliance today. Stub returns the
dict-format unchanged but is positioned to swap to `raise ConfigurationError`
if/when OCA upstream changes the contract. Safety hatch, zero functional change
in Phase 1.

**Adjustment to `docs/drafts/PHASE_1_FIRST_5_COMMITS.md`:** commit 5 description
amends to mention the override stub.

### NF3 · CLAUDE.md §3 module-tree comment inconsistency

Line 127 of the amended CLAUDE.md still reads:

```
│   │   ├── res_partner.py (channel field: dealer/contractor/kd/bigbox/refacing/retail)
```

— but Q5 + §6 + §0 of Build Spec all rename `contractor` → `tradesperson`. The
file-tree comment is stale relative to the rest of the brief. **Not a blocker**
(the brief's §6 channel table is canonical) but worth folding into a future
CLAUDE.md v1.2 patch. Flagged here so the inconsistency doesn't propagate into
code via a copy-paste-from-brief moment.

### NF4 · Q22 + Q23 status reconciliation

The amended CLAUDE.md and Build Spec do not explicitly answer Q22 or Q23, but
their wording is **implicitly consistent** with my draft assumptions:

**Q22 (door_count derivation) — implicit (a).**
- Mapping §3.4 Rule 3: *"Encoded as a `product.config.line` rule that sets door_count..."*
- Build Spec §5 row 3: *"`product.config.line` formula: door_count = 1 if value_inches <= 21 else 2"*
- Both name `product.config.line` as the mechanism. That requires `door_count`
  to be an attribute (Q22(a) — hidden 12th attribute). Q22(b) — BoM-computed —
  is incompatible with both wordings.
- **Draft assumption holds.** Recommend John explicit-ack at next batch if
  desired; otherwise proceed with (a).

**Q23 (corner-bifold encoding) — wording leans (b), invariant requires (b).**
- Build Spec §5 row 4 shorthand reads `family = corner_bifold` — *literally*
  consistent with Q23(a) (split corner into two family values).
- BUT Q2's locked 11-attribute list says family has exactly 9 values:
  `wall / base / drawer / sink / tall / corner / vanity / worktop / accessory`.
  Splitting corner would break the Q2 invariant.
- Therefore Q23(a) requires re-opening Q2; Q23(b) — `family_subtype`
  sub-attribute — preserves Q2 and matches my draft.
- **Draft assumption holds.** Re-surface Q23 explicitly in next batch so the
  Build Spec §5 shorthand can be amended to `family = corner` + `family_subtype = bifold`.

---

## 2026-05-31 · OCA session.write filters value_ids silently (P2C5 partial)

While shipping Phase 2 commit 5 (value-pick + live pricing), the
`/southbrook/api/kitchen-planner/session/<id>/set-value` endpoint
worked for the FIRST attribute pick but subsequent picks were
silently dropped. Live-traced root cause:

OCA's `product.config.session.write()` override
(`product_configurator/models/product_config.py:845`) runs
`values_available()` after every write and SILENTLY filters
out values not in the "available" set:

```python
def write(self, vals):
    res = super().write(vals)
    if not self.product_tmpl_id:
        return res
    value_ids = self.value_ids.ids
    avail_val_ids = self.values_available(value_ids)
    if set(value_ids) - set(avail_val_ids):
        self.value_ids = [(6, 0, avail_val_ids)]   # ← silent drop
    ...
```

`values_available()` walks the template's `product.config.line`
(rule) records to determine which values are "currently
allowed" given the existing value_ids selection. For our Phase-1
demo data, the four rules from CLAUDE.md §5 (series-door,
box-series, width-doorcount, family-softclose) are seeded in
`data/config_rules.xml` but the cross-rule dependency math isn't
producing the expected "available" set for our use case:

**Live trace 2026-05-31:**
- Fresh session for Base 1-Door (template 38)
- Pick Series=Contemporary (val 404): persists, value_ids=[404] ✓
- Pick Box Material=Maple (val 408): silently dropped,
  value_ids stays [404] ✗

This is because the SPA flow lets users pick attributes in any
order, but OCA's wizard architecture assumes sequential step-by-
step picking. After picking Series, the "available" set is
recomputed from `product.config.line` rules — and Maple isn't
in the "available" set despite §5 Rule 2 saying it should be
allowed on Contemporary.

**Why doesn't Rule 2 make Maple available?**
Rule 2 is *restrictive* ("Maple is only allowed when Series ∈
{Contemporary, Elegance}") — it tells OCA which combinations to
BLOCK, not which to ENABLE. The `values_available()` logic looks
for values explicitly listed as available via `product.config.line`
records. If no `product.config.line` record says "Maple is
available when Series=Contemporary", OCA defaults to NOT
available.

**Three paths forward (P2C5 polish):**

1. **Add positive `product.config.line` records.** For each
   attribute that should be freely pickable (i.e. not subject to
   a §5 restrictive rule), add a wide-open
   `product.config.line` saying "this value is always available."
   The existing restrictive rules then constrain on top. ~15-20
   new records in `data/config_rules.xml`.

2. **Use a SPA-friendly session subclass that overrides the
   write filter.** Subclass `product.config.session` with a
   `write` method that skips `values_available()` filtering for
   API-driven (non-wizard) writes via a context flag like
   `skip_availability_check`. The UI then does its own
   client-side rule enforcement (or defers all rule enforcement
   to commit/Add-to-Kitchen). ~50 lines in
   `southbrook_estimating/models/product_config_session.py`.

3. **Pre-build the full attribute_line ↔ value matrix in the
   /session/create response.** Compute available values per
   attribute on the server, send the WHOLE availability matrix
   to the frontend, frontend disables blocked values client-side
   BEFORE the user clicks. Avoids the silent-drop entirely by
   never letting the user pick a value that would be dropped.
   ~80 lines + a Phase-3 polish concern (multi-pick edge cases).

**Recommendation:** Path 1 first (smallest data fix, high
leverage). If path 1 has limits with multi-value attributes, path
2 as the more architecturally clean option.

**Current P2C5 state (shipped 2026-05-31, commits f7aff92 +
aec4c27):**
- The set-value endpoint + drawer UI ship in their full P2C5 form
- First-pick values persist correctly
- Second+ picks silently drop in the demo configuration (no
  visible error; UI updates with returned `selected_values`
  which doesn't include the dropped pick)
- Rule-block alerts work (`{ok: False, error: rule_blocked}`)
  but only when OCA raises ValidationError — silent-drop doesn't
  raise, so this surface isn't hit

P2C6 onward can proceed with these limitations documented;
multi-attribute kitchen configurations will need at least
path 1 before Phase 2 ships externally.

---

## 2026-05-30 · Phase-1 demo-seed polish (surfaced during Track 2 gate prep)

While prepping order S00235 for the Track 2 gate review walk, four
demo-data gaps surfaced. None of them block Track 2 sign-off — Track 2 is a UI
gate, not a content gate — but they leave the walk showing zeros and empty
strings where real cabinet metadata should be. All four trace to the same root
cause: the demo `product.product` variants were created bare (RPC
`product.product.create`) rather than through the OCA configurator session
flow. The configurator session is what populates the `_SKU_DEFAULTS` table
(width/height/depth/spec/maple flag/etc.) on `product.config.session`, and
without a session there is no metadata to project into the order line.

The four items below are filed for John's amendment after the gate walk. Phase
order: they should land **before** Phase 1 closes (the SAMI smoke-test gate
needs the BoM rollup populated to verify the maple `+10%` price + `+2 weeks`
lead time flow per Q7). Reasonable target: 1 week of work to seed configurator
sessions for the 12 cabinet templates + assign pricelist to the Demo
Tradesperson Tier 3 partner.

### PT-P1-01 · Seed configurator sessions for the 12 cabinet templates — **RESOLVED at XML layer 2026-05-31; sessions (layer 3) deferred to Phase 3**

**Symptom (in gate walk):** D4 spec column empty, D6 drawer Spec field empty,
no MAPLE badge ever appears.

**Root-cause split (re-diagnosed 2026-05-31):** What looked like one ticket
turned out to be three independent layers stacked on each other:

1. **SKU lookup layer.** Controller reads `product_tmpl_id.default_code` and
   queries `_SKU_DEFAULTS` for family + W/H/D defaults. The 5 templates I
   touched during demo seed (ids 36/38/39/40/42) had their default_code
   contaminated by Odoo's single-variant code-sync (variant write → template
   code mirror). My `-DEMO` suffix on the variant default_code propagated up
   to the template, breaking `_SKU_DEFAULTS` lookups. **Fixed in DB
   2026-05-31** by resetting templates 36/38/39/40/42 + variants 58/59/60/61/62
   back to their clean Q8 SKUs (no -DEMO).

2. **Spec-text layer.** Controller derives `spec_summary` from
   `line.name`. Demo lines were created with the bare product
   display name (`[SB-BASE-1DR] Base 1-Door`), no spec. **Fixed in DB
   2026-05-31** by writing realistic spec strings to the 6 lines of S00235
   (Contemporary + Maple + width + soft-close, mixing in 2 Maple lines so
   the MAPLE badge fires).

3. **Configurator-session layer.** A real session attaches the variant's
   attribute values to `value_ids`, which is the canonical source for
   `mrp.bom._compute_panel_dimensions` (Track 1 routine #1) BoM math.
   **Not done.** Phase 1 polish — see "Remaining work" below.

**Layers 1 + 2 resolved PT-P1-02, PT-P1-04, and the MAPLE badge surface for
the gate walk** — all without configurator sessions. The decontaminated SKU
strings make `_SKU_DEFAULTS` fire, which produces family + W/H/D + door/drawer
counts; the BoM rollup walks those defaults and returns sensible per-line
panel/hardware counts. PT-P1-01's "session" work is now only required to
get fully cabinet-attribute-driven spec text + dynamic prices (which the
controller would compute from the session's value_ids instead of the
line.name string).

**Live verification 2026-05-31:** S00235 payload now returns:
- 6 lines with `family ∈ {base, drawer, wall, tall}`
- `width_mm` ∈ {457, 600, 609, 762}, `width_inches` ∈ {18, 23.5, 24, 30}
- `is_maple = True` on L89 + L92 (the two Contemporary Maple lines)
- `spec_summary` like "Base 1-Door · Contemporary · Maple · 24″ · Soft-close hinge L"
- BoM rollup: 8 cabinets, 70 panels, 8.91m edge banding, 10 hinge pairs,
  13 handles, 3 drawer slide pairs

**Demo XML reproducibility executed 2026-05-31 (commit 9245093):**

Three files changed + manifest updated:
- New `demo/southbrook_demo_variants.xml`: 12 `product.product` records, one
  per cabinet template per Q8. Each carries clean SB-* `default_code`,
  `list_price` matching the Signature Series retail anchors, and
  `standard_price` seeded for Phase 3 customer-mode Contractor pricelist
  chain. Bypasses the Q6 `create_variant='dynamic'` rule for demo data
  only — production stays dynamic.
- Updated `demo/southbrook_demo_orders.xml`: all 10 `product.product_product_4`
  refs (the NF22 blocker) replaced with `demo_variant_*` xml_ids. New
  records `demo_gate_walk_order` + `demo_gate_walk_line_1..6` reproduce
  S00235 exactly — Demo Tradesperson Tier 3, 6 lines, 3 zones, 2 Maple
  lines, explicit `pricelist_id=ref('pricelist_retail')` per corrected
  PT-P1-03. Stays in draft so F3 (Duplicate) and F4 (Confirm) gate-walk
  paths both exercise cleanly without mutating the canonical demo state.
- Updated `demo/southbrook_demo_partners.xml`: all 6 demo partners now
  carry `specific_property_product_pricelist=ref('pricelist_retail')`
  (Odoo-19 writable field name per PT-P1-03 gotcha).
- Updated `__manifest__.py` demo list to add variants + re-enable
  orders. NF22 status amended from blocker → RESOLVED with cross-ref
  to this ticket.

**Layer-3 (real configurator sessions) deferred to Phase 3:**

The static demo variants are sufficient for Phase 1/2 gate review and
the Phase 2 customer-mode preview surfaces. They fall short when:
- BoM rollup needs truly attribute-driven panel dimensions (today's
  `_SKU_DEFAULTS` fallbacks are 80% accurate; session-driven values
  would be 100%).
- A configurator-rule-violation test needs to verify that an invalid
  combination (Contractor + Maple per the box_material→series rule) is
  blocked at session-commit time. Today the demo doesn't exercise the
  rule engine — only the SKU + line.name surfaces.

Phase 3 follow-up: replace `demo/southbrook_demo_variants.xml` with a
Python helper (e.g., a `post_init_hook` or a dedicated `demo.py` model
extending the addon's data load) that:
1. Walks the OCA `product.config.session` flow per cabinet
2. Picks attribute values via `wizard.action_next_step()` matching the
   spec text in the demo order's `line.name`
3. Commits each session (`session.action_confirm()`) which materialises
   a `product.product` variant with `value_ids` populated
4. Binds the resulting variant to the corresponding `demo_gate_walk_line_*`
   record

That gives a faithful end-to-end demo trace and exercises every routine
the engine ships (rule resolution, attribute-line price uplift, BoM
rollup from actual cabinet dimensions). ~2-3 hours scoped against the
OCA session lifecycle once Phase 3 starts.

**Acceptance (this commit):** fresh `-i southbrook_estimating
--stop-after-init` install with the demo flag produces the gate-walk
order automatically with the canonical numbers — verifiable via
`/southbrook/api/order/<demo_gate_walk_order_id>` returning retail
$3,275 / channel $2,128.75 / 6 lines / 3 zones / MAPLE badge on
lines 1 + 4.

### PT-P1-02 · Populate BoM rollup — **RESOLVED 2026-05-31**

**Symptom (in gate walk):** E1 BoM Preview tab summary cells show
`Cabinets 0 / Total Panels 0 / Edge Banding 0.00 m`; both tables (panels +
hardware) render with all zeros.

**Root cause (corrected):** Not "missing configurator sessions" as
originally filed. The real cause was the contaminated template
default_codes (per PT-P1-01 layer 1) breaking `_SKU_DEFAULTS` lookups,
which cascaded into the BoM rollup loop seeing 0 cabinets, 0 panels,
0 hardware.

**Fix:** Resolved by PT-P1-01 layer 1 (template default_code
decontamination). No additional work needed.

**Acceptance (live-verified 2026-05-31):** S00235's BoM rollup returns:
```
cabinet_count: 8 (= sum(line.qty))
panels: side=16, top=8, bottom=8, back=8, shelf=17, door=10, drawer_front=3
hardware: hinge_pair=10, handle=13, drawer_slide_pair=3
edge_banding_mm: 8914 (= 8.91 m)
```
All non-zero. Cabinet count (8) reflects qty rollup; panel counts scale
with door/drawer count per Q24 width→door rule; hardware tracks doors +
drawers per cabinet.

**Remaining for fresh-install reproducibility:** Same as PT-P1-01 — the
demo XML needs the populated order so a fresh install with --with-demo
produces these numbers.

### PT-P1-03 · Assign pricelist to demo partners — **partially executed 2026-05-30**

**Symptom (in gate walk):** B4 customer cell pricelist badge renders empty
between the partner-name row and the channel badge row.
**Symptom (RPC):** `order.pricelist_id = null`, `pricelist_name = ""` despite
`channel = tradesperson` and the channel discount resolving correctly to 35%.
**Root cause:** `res.partner` id=19 (Demo Tradesperson Tier 3) was seeded
without a pricelist. The channel resolution path (`res.partner.channel` →
`discount_pct`) is independent of pricelist and works, but the HeaderStrip's
pricelist-badge cell renders empty.

**Odoo 19 field rename — important gotcha:** The writable field is
`specific_property_product_pricelist` (store=True, company_dependent=True);
`property_product_pricelist` is now a computed/read-only mirror. Writing
to the latter silently fails (returns True but doesn't persist). All XML
seed records + RPC writes need to use the `specific_` prefix in Odoo 19.

**Executed during gate walk (2026-05-30) and CORRECTED 2026-05-31:**

Initial pass (incorrect — produced double-discount):
- Admin → Retail (id=37)
- Demo Tradesperson Tier 3 → Contractor Tier 3 (-35%) (id=42)
- 4 dealers → Dealer (-50%) (id=38)
- Order S00235 pricelist_id → 42

**Why that was wrong:** the controller's
`_build_southbrook_order_payload` computes
`channel_total = sum(line.price_subtotal) × (1 - discount_pct/100)`,
which assumes `line.price_subtotal` is the RETAIL value (so the
channel discount is layered on top). If the order's pricelist is
the channel pricelist (e.g. Contractor Tier 3 with -35% rule),
then `line.price_subtotal` is already discounted and the controller
double-discounts: $320 → $208 (pricelist) → $135.20 (controller).
For Tier 3 against a $3,275 retail kitchen, this surfaced as
retail $2,128.75 / channel $1,383.69 instead of the canonical
$3,275 / $2,128.75 / $1,146.25.

Corrected assignment (all 6 partners → Retail pricelist id=37,
order S00235 pricelist_id → 37):
- The channel discount is controller-side, derived from
  `partner.channel` + `tradesperson_tier`. The order pricelist
  is invariant (Retail = list price = `product.template.list_price`).
- This matches CLAUDE.md §6 ARITHMETIC ("retail $X × 0.65 = channel
  $Y") even though §6's wording suggests partner→channel-pricelist
  mapping. The wording is aspirational for Phase 3; today's
  controller doesn't multiply by anything except the channel
  discount, so the pricelist must be Retail or the math
  double-counts.
- CLAUDE.md §6 needs to be amended in Phase 3 to clarify: the
  channel pricelists exist for Phase 3 UI ("show me what the
  customer would see") but are not used as the order pricelist
  in Phase 1/2.

**Remaining for full ticket closure:**
- Move these assignments into `addons/southbrook_estimating/demo/southbrook_demo.xml`
  so they re-apply on fresh installs (currently they're DB-only writes that
  would be lost on a module reinstall + DB rebuild).
- Use `specific_property_product_pricelist` as the field name in the XML.
- Verify the existing 6 pricelists per Q1 have the expected xml_ids
  (`southbrook_estimating.pricelist_retail`, `pricelist_dealer`,
  `pricelist_tradesperson_tier1/2/3`, `pricelist_kd`, `pricelist_bigbox`,
  `pricelist_refacing`) — note that 9 pricelists are seeded today, not 6;
  the 3 Contractor tiers split out from the general Contractor list.
- Decide whether to backfill all existing draft sale.orders' pricelist_id
  on demo data refresh, or leave that as user-action (re-resolve via
  `partner.action_open_sale_order` flow).

**Acceptance (live-verified 2026-05-30):** B4 cell shows
`Contractor Tier 3 (-35%)` badge; `/southbrook/api/order/235` returns
`pricelist_id=42, pricelist_name='Contractor Tier 3 (-35%)'`.

### PT-P1-04 · Width-attribute resolution — **RESOLVED 2026-05-31**

**Symptom (in gate walk):** D4 width column shows `0″` for every line; D6
drawer Width field reads `0`.

**Root cause (corrected):** Not "missing configurator sessions" as
originally filed. Same as PT-P1-02 — contaminated template default_codes
broke `_SKU_DEFAULTS` lookups, which had the W field for each template
SKU. Without the lookup hit, width_mm fell through to 0.

**Fix:** Resolved by PT-P1-01 layer 1 (template default_code
decontamination). The `_SKU_DEFAULTS` table provides per-SKU W/H/D
defaults that satisfy the gate walk; full configurator-session-driven
width per Q2 attribute is Phase-3 work (when individual cabinet variants
have their own custom width).

**Acceptance (live-verified 2026-05-31):** S00235 width values:
- L89 (base 1-door) → 609 mm / 24″
- L90 (base 2-door) → 762 mm / 30″
- L91 (drawer bank) → 609 mm / 24″
- L92 (wall 1-door) → 457 mm / 18″
- L93 (wall 1-door) → 457 mm / 18″
- L94 (tall pantry) → 600 mm / 23.5″ (per Q23 enum value)
**Acceptance:** S00235's lines all return `width_mm > 0` (typical demo
spread: 305, 457, 610, 762 mm matching Q23 enumeration); `width_inches`
shows the matching imperial values (12, 18, 24, 30) per Q4 dual-unit storage.

### Cross-cutting note: this is the Phase-1 §10 step 2 work

CLAUDE.md root §10 step 2 already commits to "seed the 12 cabinet templates
through the OCA configurator session flow … so the 9-line smoke-test order
against Demo Tradesperson Tier 3 returns the maple `+10%` price and `+2 weeks`
lead time correctly applied" — this is the Phase-1 gate, not a Phase-2 add.
PT-P1-01..04 are the concrete tickets for that step; closing all four
unblocks the Phase 1 sign-off gate.

**Suggested order of work:** PT-P1-03 first (smallest — 1 partner edit ×5);
PT-P1-01 second (the substantive change); PT-P1-02 and PT-P1-04 fall out
automatically once PT-P1-01 lands.

### PT-P1-05 · Decide policy on `/shop/<slug>` route

**Symptom (in gate walk):** Visiting
`/shop/sb-base-1dr-demo-base-1-door-38` 500s with
`Expected singleton: res.currency()` from the OCA template
`website_product_configurator.product_configurator`. The failing
expression: `website._get_and_cache_current_pricelist().currency_id`.

**Root cause:** The `/shop/` route is Odoo's built-in
`website_sale` product page, which the OCA `website_product_configurator`
module extends. Its template assumes `_get_and_cache_current_pricelist()`
returns a non-empty pricelist for every visitor. In the current site
config, anonymous/admin visitors who don't match any selectable pricelist
get an empty recordset back, then `.currency_id` walks empty → singleton
error.

**Why this isn't a Southbrook bug:** CLAUDE.md §2.1 + §2.2 specify the
customer-facing surface is `/kitchen-planner` (Phase 2 §8 work, not
yet built). The internal surface is `/my/southbrook/order-builder/<id>`
(Track 2, gated 2026-05-30). The `/shop/` route is a legacy
Odoo-built-in surface that came along for the ride from the
`website_sale` dependency — not a Southbrook-supported route.

**Three options:**

1. **Skip — disable `/shop/` navigation entirely.** Phase 2 §8 will
   replace it with `/kitchen-planner`. Until then, the route can
   remain accessible by URL but isn't linked from anywhere. Risk:
   anyone who pastes a /shop/ URL hits the 500. Mitigation: add a
   `website.menu` cleanup pass that removes the auto-generated
   "Shop" menu item from the website navigation. **Recommended.**

2. **Patch OCA upstream.** Add a defensive `t-if="currency_id"` on
   the `<span class="config_product_price"/>` template. CLAUDE.md §3
   says: leave OCA modules at 19.0.1.0.0 as delivered; file upstream
   if you find a real bug. This IS a real upstream bug (the template
   should handle the empty-pricelist case). File it at the OCA repo
   for `product_configurator/website_product_configurator/views/`.
   Risk: 2-4 weeks for upstream merge + release.

3. **Make a CAD pricelist `selectable=True` to force resolution.**
   `_get_and_cache_current_pricelist()` walks `website.pricelist_ids`
   filtered by `selectable=True` + country match. If at least one
   country-less selectable pricelist exists, anonymous visitors get
   it. Set `pricelist_retail` (id=37) selectable=True. Risk: changes
   the website_sale resolution semantics for anyone hitting /shop/
   to always show retail prices, which may not be what a logged-in
   tradesperson expects.

**Suggested decision:** option 1 + file an OCA issue per option 2
(defensive `t-if` in the template is correct upstream behaviour).
Document the deferred /shop/ disablement as Phase 3 navigation cleanup.

**File targets if option 1 is picked:**
- `addons/southbrook_estimating_website/data/menu.xml` — already
  scaffolded per CLAUDE.md §3 file tree; add a menu-cleanup record
  to deactivate `website_sale.menu_shop` for this website.

**Acceptance:** `/shop/` either 404s cleanly or redirects to a
Southbrook-branded "Browse our kitchens" landing page (Phase 2 §8).

### PT-P1-06 · Channel pricelists return $0 due to missing cost data — **path C selected 2026-05-31**

**Symptom (in gate walk):** Editing qty on any line of order 235
caused that line's `price_unit` to drop to $0, scrambling the
HeaderStrip totals.
**Symptom (RPC):** After `sale.order.line.write({product_uom_qty: 3})`,
the line's `price_unit` recomputed via the pricelist and returned $0.

**Root cause (re-diagnosed 2026-05-31):** The XML seed in
`data/pricelists.xml` is architecturally correct per CLAUDE.md §6,
NOT broken. The Contractor (Tiered) base pricelist (id=39, rule
id=30) uses `compute=formula, base=standard_price, markup=5` →
`cost × 1.05`. The three Tier pricelists (Tier 1/2/3, rules
id=31/32/33) inherit from it via `compute=formula, base=pricelist,
base_pl=Contractor (Tiered), price_discount=25/30/35` → effective
`(cost × 1.05) × 0.75/0.70/0.65`.

The chain works as designed. It returns $0 ONLY because the demo
variants (58-62) have `standard_price = $0` (I created them bare
via RPC during the gate-walk seed without cost data). Real cabinets
sourced from supplier catalogs would have standard_price set; the
chain would then produce sensible values.

**Architecture clash documented:**

| Surface | Math used |
|---|---|
| CLAUDE.md §6 (Contractor pricing) | `cost × 1.05 × (1 - tier_discount/100)` |
| Phase 2 controller + gate spec | `retail × (1 - tier_discount/100)` |

These are DIFFERENT pricing models. §6 protects against zero/low
cost items via the cost-floor. The controller assumes the order's
pricelist is Retail and layers the channel discount on top.

The corrected PT-P1-03 reconciles them by always pointing
`order.pricelist_id` at Retail — which is what the controller's
math requires. The Contractor pricelist chain is then never
queried in Phase 1/2.

**Path C selected (2026-05-31, user choice).** Both models coexist:

- Phase 1/2 always uses Retail pricelist on orders (per corrected
  PT-P1-03).
- The 9 channel pricelists remain seeded per §6 but are never
  queried by `order.pricelist_id`. They exist for:
  - Phase 3 customer-mode pricing surfaces (when those land)
  - Manual "what would the customer pay" sanity checks
  - Future Phase 4 supplier-cost integration

No code or XML change required for Track 2 sign-off. The seed XML
stays §6-aligned; the controller stays gate-spec-aligned; the
corrected PT-P1-03 prevents them from colliding.

**Live patch reverted 2026-05-31:** during diagnosis I patched rule
id=33 to `compute=percentage, percent_price=35, base=list_price` in
the DB. That made the Tier 3 pricelist return $208 (= $320 × 0.65)
for the demo variant — but broke the §6 architecture. Reverted via
RPC to match the XML shape (`compute=formula, base=pricelist,
base_pl=Contractor (Tiered), price_discount=35`). DB now matches
XML; both are §6-aligned. Order 235 still returns the canonical
$3,275 / $2,128.75 / $1,146.25 because it uses Retail pricelist.

**When this ticket actually needs work:**
- Phase 3 §8.1 customer-mode surfaces will start querying the
  channel pricelists for display. At that point cost data needs
  to exist or the pricelists need to be rewritten to use
  `list_price` as the base (drop the cost-floor architecture).
- Phase 4 supplier-cost integration: real `standard_price` values
  arrive via product cost field. The Contractor chain then
  returns sensible values automatically; no rule change needed.

**No file changes.** No work scheduled until Phase 3 or Phase 4
forces the choice.

**Cross-ref:** PT-P1-03 (corrected — partners always point at
Retail pricelist); CLAUDE.md §6 (architecture spec to amend in
Phase 3 if cost-floor architecture is dropped); Build Spec §6
pseudocode (matches §6).

---

## 2026-06-03 · Production-approval gate — BoM/routing data completeness (follow-up)

The production-approval gate (folded into `southbrook_mrp_pm`: the
`action_send_to_production` chokepoint + request/approve/reject workflow +
approver group + hybrid notifications) is complete and correct, but its
*output* is bounded by manufacturing data that is only partially seeded.

**Issue.** Only `SB-BASE-1DR` has a full BoM + 8-station routing today
(`data/routing_base_1dr.xml`). The other 11 Q8 cabinet SKUs are covered by
`data/routings_other_11.xml`, but `action_send_to_production` skips any order
line whose variant has no resolvable normal-type BoM. So an **approved**
multi-cabinet order currently generates fewer manufacturing/work orders than
the "10 WO" vision — silently, line by line.

**This is data, not code.** The gate behaves correctly; it simply has nothing
to explode for the unseeded SKUs. No change to the gate is warranted.

**When this needs work.** Before any demo / sign-off that promises
per-cabinet work-order generation across the full catalogue, seed BoM +
routing for the remaining 11 SKUs (mirror the `SB-BASE-1DR` skeleton;
per-cabinet cycle times differ). Until then the portal `no_bom_lines`-style
skip is expected behaviour, not a bug.

**Cross-ref.** `southbrook_mrp_pm/models/sale_order.py`
(`action_send_to_production`, `_resolve_bom_for_line`); manifest notes on
`routing_base_1dr.xml` / `routings_other_11.xml`; gate patch
`0001-feat-mrp-pm-production-approval-gate.patch`.
