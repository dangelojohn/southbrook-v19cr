# Project SIGHTLINE

## UX/UI Enhancement Specification — Southbrook CMMS & 3PL Screen Family

### Odoo 17/18 · Build-ready spec for Claude Code

**Target instance:** `southbrookcabinetry.space` **Author:** Claude (Cowork) — derived from live DB introspection + screen walkthrough, 2026-08-13 **Consumer:** Claude Code (implementation agent) **Scope decisions locked with stakeholder:** Mixed/phased ambition (native first, custom OWL second) · Role-aware for both floor technicians and plant/ops managers · Deliverable is a build spec, not a mockup

---

## 0. How to use this document

Every enhancement carries a stable ID (`SL-Fxx`, `SL-xx`, `SL-Xxx`) and a **name**. Names are intentional — they are the vocabulary for the whole program. Use the ID in branch names, commit messages, and view `xml_id`s so the spec and the codebase stay welded together.

```text
feat(SL-01): Reliability Ledger — regroup MTBF list, availability meter widget
xml_id: southbrook_cmms_ux.view_mtbf_list_reliability_ledger   # SL-01
```


Each item is written as: **Name** → *Problem* → *Change* → *Implementation* → *Done when*.

Nothing in Phase 0 or Phase 1 requires a custom view type. Phase 2 is where the OWL work lives.

---

## 1. Ground truth — what actually exists today

This was read directly from the target database, not assumed. Trust it.

### 1.1 The screen family

Menu root: **`Inventory ▸ CMMS & 3PL`** (`ir.ui.menu` id **930**)

| Menu ID | Screen | Action | Model | Current `view_mode` |
| :---- | :---- | :---- | :---- | :---- |
| 931 | MTBF/MTTR Reports | `act_window,1401` | `southbrook.cmms.mtbf_mttr_report` | `list,form,graph` |
| 932 | Breakdown Alerts | `act_window,1402` | `southbrook.cmms.breakdown_alert` | `list,kanban,form` |
| 933 | Service Contracts | `act_window,1403` | `southbrook.cmms.service_contract` | `list,form,calendar` |
| 934 | Landed Cost Templates | `act_window,1404` | `southbrook.cmms.landed_cost_template` | `list,form` |
| 935 | Snapshot | `act_window,1405` | `southbrook.cmms.mi_tiles` | `list,form` |

Sibling screen, same family, different app (**`Manufacturing ▸ Integrations ▸ 3PL ASNs`**, menu 918):

| 918 | 3PL ASNs | `act_window,1370` | `southbrook.integrations.asn_3pl` | `list,form` |

### 1.2 Field inventory (mail-mixin fields stripped)

**`southbrook.cmms.mtbf_mttr_report`** — inherits `mail.thread` `name` (char) · `equipment_id` (m2o `maintenance.equipment`) · `as_of_date` (date) · `period_days` (int) · `breakdown_count` (int) · `mtbf_hours` (float) · `mttr_hours` (float) · `availability_pct` (float) · `total_downtime_hours` (float) · `total_runtime_hours` (float)

**`southbrook.cmms.breakdown_alert`** — inherits `mail.thread` `name` · `equipment_id` · `workcenter_id` (m2o `mrp.workcenter`) · `severity` (`low|medium|high|critical`) · `state` (`open|dispatched|fixed`) · `reported_at` (datetime) · `reported_by` (m2o `res.users`) · `description` (text) · `maintenance_request_id` (m2o `maintenance.request`) · `affected_production_ids` (m2m `mrp.production`) Form header already exposes: `action_dispatch`, `action_block_affected_mos`, statusbar.

**`southbrook.cmms.service_contract`** `name` · `vendor_id` · `date_start` · `date_end` · `days_to_expiry` (int) · `annual_value` (monetary) · `currency_id` · `equipment_ids` (m2m `maintenance.equipment`) · `expiry_alert_at` (`30|60|90|180`) · `state` (`active|expiring_soon|expired|…`)

**`southbrook.cmms.landed_cost_template`** `name` · `freight_pct` · `duty_pct` · `customs_pct` · `handling_pct` · `default_for_vendor_ids` (m2m `res.partner`)

**`southbrook.cmms.mi_tiles`** (action title: *CMMS / WMS Snapshot*) `label` · `as_of` (datetime) · `currency_id` · `open_breakdowns_count` · `service_contracts_expiring_60d_count` · `oversize_pickings_pending_count` · `mtbf_avg_30d` · `mttr_avg_30d` · `landed_cost_value_30d` (monetary)

**`southbrook.integrations.asn_3pl`** `name` · `picking_id` (m2o `stock.picking`) · `carrier_id` (m2o `delivery.carrier`) · `tracking_number` · `state` (`draft|sent|acked|shipped|delivered`) · `oversize_load_flag` (bool) · `permit_jurisdictions` (char) · `payload_json` (text)

### 1.3 Adjacent fields you are allowed to pull from

`maintenance.equipment` is unusually rich here and is currently **almost entirely unused by the CMMS screens**. Available:

`expected_mtbf` · `mtbf` · `mttr` · `estimated_next_failure` · `latest_failure_date` · `maintenance_open_count` · `maintenance_count` · `workcenter_id` · `category_id` · `location_id` · `technician_user_id` · `maintenance_team_id` · `warranty_date` · `serial_no` · `model` · `cost` · `effective_date` · `scrap_date` · `color` · `equipment_properties` · `southbrook_condition` · `southbrook_condition_last_updated` · `southbrook_condition_note` · `southbrook_condition_updated_by` · `southbrook_impacted_production_count` · `southbrook_impacted_production_ids` · `southbrook_tool_asset_count` · `southbrook_tool_asset_ids`

> **This is the single biggest missed opportunity in the current build.** There is already a computed `expected_mtbf` vs actual `mtbf`, an `estimated_next_failure`, and a bespoke `southbrook_condition` rating — and not one of the five CMMS screens surfaces any of them. Half of Phase 1 is simply *connecting what already exists*.

### 1.4 Defects and gaps found during the walkthrough

| # | Finding | Severity | Evidence |
| :---- | :---- | :---- | :---- |
| D1 | **Zero search views.** Not one of the six models has an `ir.ui.view` of type `search`. Users cannot filter, group, or save a favourite anywhere in this family. | **Critical** | View-type audit returned only `form/list/kanban/graph/calendar` across all 5 CMMS models |
| D2 | **Duplicated sequence prefix.** References read `MTBF/2026-08-12/MTBF/0241` — the `MTBF/` token appears twice. | High | Every row on action-1401 |
| D3 | **3PL ASNs menu is visible but inaccessible.** Opening action-1370 as *Administrator* throws `Access Error` requiring `Southbrook Integrations / Manager` or `/ User`. The menu has no `groups=` guard, so it advertises a dead end. | High | Screenshot of action-1370 |
| D4 | **Snapshot KPI model rendered as a list.** `mi_tiles` is a single-row KPI aggregate displayed as a 6-column table with truncated headers (`Open Br…`, `Contract…`, `Oversize…`). This is the worst possible presentation of a dashboard model. | High | action-1405 |
| D5 | **Default boilerplate empty states.** Every action carries `help = "<p class='o_view_nocontent_smiling_face'>Create new document</p>"`. Six screens, six identical meaningless placeholders. | Medium | `ir.actions.act_window.help` on 1401–1405, 1370 |
| D6 | **No pivot view anywhere.** Six analytical models, zero pivots. | Medium | `view_mode` audit |
| D7 | **No activity view** despite `mail.thread` on the alert and report models. | Medium | `view_mode` audit |
| D8 | **Column truncation on default widths.** `Period (…`, `Breakdo…`, `Availability (…` all clipped at standard viewport. | Medium | action-1401 |
| D9 | **Signal buried in noise.** 241 MTBF rows, one per equipment per day. Four of five machines sit at exactly `100.00 / 2,160.00 / 0` forever; the one machine that matters (Biesse Rover K FT / CNC-001, 47–64 % availability) is one row in five. The list is 80 % padding. | **Critical** | action-1401, 241 records |
| D10 | **Menu label ≠ action title.** Menu says `Snapshot`; breadcrumb says `CMMS / WMS Snapshot`. | Low | action-1405 |
| D11 | **Monetary field showing `$ 0.00` on a live contract.** Service Contract "Smoke Cont…" is `Expiring Soon`, 36 days out, `annual_value = 0`. Either a data gap or a required-field gap — the UI gives no hint which. | Medium | action-1403 |
| D12 | **No mobile/touch consideration.** Standard Odoo list density; a technician wearing gloves cannot reliably hit a row or the Dispatch button. | High | All screens |
| D13 | **Severity/state encoded by colour alone** in list decorations — fails WCAG 1.4.1 (use of colour). | Medium | Breakdown Alerts list arch |
| D14 | **Unbounded growth, no default date filter.** 5 snapshot rows/day ≈ 1,825/year on `mtbf_mttr_report`, with no domain on the action and no index guidance. | High | Record count trajectory |

---

## 2. Design principles for this program

1. **The exception is the interface.** A CMMS screen that shows 200 healthy rows to surface 40 unhealthy ones has failed. Default every view to the thing that is wrong.
2. **Two front doors, one dataset.** A technician and a plant manager should never share a default view. Role-aware actions, shared models.
3. **Colour is a reinforcement, never the message.** Every severity/state/health signal gets an icon or a label alongside its colour.
4. **Every number is a door.** No KPI tile, aggregate, or badge is inert — clicking it opens the filtered records behind it.
5. **Native until native breaks.** Do not write an OWL component for something a `widget="progressbar"` or a pivot can do. Phase 2 exists because Phase 1 genuinely runs out of road, not before.
6. **Upgrade-safe.** New module, no core patches, no `ir.ui.view` surgery on Odoo standard views. Everything is additive and removable.

### 2.1 Role model

| Role | Group | Default landing | Optimised for |
| :---- | :---- | :---- | :---- |
| **Floor Technician** | `southbrook_cmms_ux.group_cmms_tech` | *Tech Mode* (SL-X7) | Touch, glare, gloves, one task at a time |
| **Maintenance Lead** | `southbrook_cmms_ux.group_cmms_lead` | Triage Board (SL-07) | Queue management, dispatch, SLA |
| **Plant / Ops Manager** | `southbrook_cmms_ux.group_cmms_manager` | Command Deck (SL-19 / SL-X6) | Trend, cost, contract, exception |
| **Logistics / 3PL** | `southbrook_integrations.group_*` (existing) | Load Board (SL-X11) | Shipment state, oversize, permits |

---

# PHASE 0 — FOUNDATION

*Native only. No new view types. Ship this first; everything else depends on it.*

---

### SL-F01 · **Ground Truth** — search views for all six models

*Fixes D1*

**Problem.** No filters, no group-bys, no saved favourites, anywhere in the family. Every user is doing manual visual scanning of an unsorted table.

**Change.** A full `search` view per model with named filters, group-by dimensions, and searchable fields.

**Implementation.**

```xml
<record id="view_mtbf_search" model="ir.ui.view">   <!-- SL-F01 -->
  <field name="name">southbrook.cmms.mtbf_mttr_report.search</field>
  <field name="model">southbrook.cmms.mtbf_mttr_report</field>
  <field name="arch" type="xml">
    <search string="Reliability">
      <field name="equipment_id"/>
      <field name="name" string="Reference"/>
      <field name="as_of_date"/>
      <filter name="f_today"   string="Today"       domain="[('as_of_date','=', context_today().strftime('%Y-%m-%d'))]"/>
      <filter name="f_7d"      string="Last 7 Days" domain="[('as_of_date','&gt;=', (context_today() - relativedelta(days=7)).strftime('%Y-%m-%d'))]"/>
      <filter name="f_30d"     string="Last 30 Days" domain="[('as_of_date','&gt;=', (context_today() - relativedelta(days=30)).strftime('%Y-%m-%d'))]"/>
      <separator/>
      <filter name="f_degraded" string="⚠ Degraded (&lt; 95%)"  domain="[('availability_pct','&lt;',95)]"/>
      <filter name="f_critical" string="🛑 Critical (&lt; 80%)"  domain="[('availability_pct','&lt;',80)]"/>
      <filter name="f_hadbreak" string="Had Breakdowns"        domain="[('breakdown_count','&gt;',0)]"/>
      <filter name="f_below_expected" string="Below Expected MTBF"
              domain="[('mtbf_hours','&lt;','equipment_id.expected_mtbf')]"/>  <!-- see note -->
      <separator/>
      <group expand="1" string="Group By">
        <filter name="g_equipment" string="Equipment"  context="{'group_by':'equipment_id'}"/>
        <filter name="g_category"  string="Category"   context="{'group_by':'equipment_id.category_id'}"/>
        <filter name="g_workcenter" string="Work Center" context="{'group_by':'equipment_id.workcenter_id'}"/>
        <filter name="g_week"      string="Week"       context="{'group_by':'as_of_date:week'}"/>
        <filter name="g_month"     string="Month"      context="{'group_by':'as_of_date:month'}"/>
      </group>
    </search>
  </field>
</record>
```


> **Note on `f_below_expected`:** cross-field comparison is not expressible in a domain. Add a stored computed boolean `is_below_expected_mtbf` on the report model (`mtbf_hours < equipment_id.expected_mtbf`, `store=True`, depends on both) and filter on that. Same pattern applies wherever a filter needs a comparison rather than a constant.

Required search views, minimum filter set per model:

| Model | Must-have filters | Must-have group-bys |
| :---- | :---- | :---- |
| `mtbf_mttr_report` | Today · 7d · 30d · Degraded · Critical · Had Breakdowns · Below Expected MTBF | Equipment · Category · Work Center · Week · Month |
| `breakdown_alert` | Open · Unassigned · Critical+High · Reported Today · Ageing > 4h · Ageing > 24h · Blocking Production · My Reports | Severity · State · Equipment · Work Center · Reported By · Day |
| `service_contract` | Active · Expiring ≤30/60/90 · Expired · No Value Set · Covers Critical Equipment | State · Vendor · Expiry Month · Alert Threshold |
| `landed_cost_template` | Has Vendor Defaults · Unassigned · Total > 20 % | Vendor |
| `mi_tiles` | Latest · Last 7 Snapshots · Last 30 | Day · Week |
| `asn_3pl` | Draft · In Transit · Delivered · **Oversize** · Awaiting Ack > 24h · Permit Required | State · Carrier · Oversize · Jurisdiction |

**Done when.** Every screen in the family opens with a working filter dropdown, a Group By section, and at least one default filter applied via the action's `context`.

---

### SL-F02 · **Nameplate** — reference sequence and label hygiene

*Fixes D2, D10*

**Problem.** `MTBF/2026-08-12/MTBF/0241`. The `MTBF/` prefix is applied twice — almost certainly an `ir.sequence` with `prefix = 'MTBF/%(range_year)s-%(range_month)s-%(range_day)s/MTBF/'` or a Python-side `f"MTBF/{date}/"` concatenated onto a sequence that already carries the prefix. Menu label `Snapshot` disagrees with action title `CMMS / WMS Snapshot`.

**Change.**

- Reference format → `MTBF/2026-08-12/0241`. Migration script to rewrite existing 241 records so history stays searchable.
- Align menu label and action name across the family. Proposed final naming:

| Menu (short, scannable) | Action title (explicit, breadcrumb-friendly) |
| :---- | :---- |
| Reliability | Equipment Reliability — MTBF/MTTR |
| Breakdowns | Breakdown Alerts |
| Contracts | Equipment Service Contracts |
| Landed Costs | Landed Cost Templates |
| Command Deck | CMMS / WMS Command Deck |
| 3PL Shipments | 3PL Advance Shipping Notices |

**Done when.** No reference contains a repeated token; breadcrumb text matches the menu item the user clicked.

---

### SL-F03 · **Doorman** — menu visibility follows access rights

*Fixes D3*

**Problem.** The `3PL ASNs` menu renders for users who cannot open it, producing a hard `Access Error` modal. This is the single most trust-destroying moment in the family.

**Change.**

1. Add `groups="southbrook_integrations.group_asn_user,southbrook_integrations.group_asn_manager"` to menu 918 so it disappears for users without rights.
2. Grant `base.group_system` (Administrator) implied membership in `group_asn_user` — an admin who cannot open a screen in their own database is a configuration bug, not a security posture.
3. Sweep all six menus and add matching `groups=` guards.
4. Audit `ir.model.access` + `ir.rule` for all six models; anything with a menu must have a matching read ACL for every group that can see the menu.

**Done when.** No user in the system can click a CMMS/3PL menu item and receive an access error. Verify by impersonating one user per group.

---

### SL-F04 · **Signal Palette** — the CMMS design token layer

*Foundation for everything visual*

**Problem.** Every screen invents its own colour meaning. `decoration-danger` currently means "high severity" on alerts and nothing at all elsewhere; the MTBF list colours whole rows red when a machine has a single breakdown, which is louder than the actual availability number deserves.

**Change.** One SCSS token file defining semantic health bands, reused by every list decoration, kanban card, badge, and Phase 2 component.

```scss
// static/src/scss/cmms_tokens.scss  — SL-F04
:root {

  /* Availability / health bands — the spine of the whole family */
  --sb-health-good:      #1a7f5a;   /* ≥ 98 %  */
  --sb-health-watch:     #b8860b;   /* 95–98 % */
  --sb-health-degraded:  #c2571a;   /* 80–95 % */
  --sb-health-critical:  #b02a2a;   /* < 80 %  */
  --sb-health-unknown:   #6b7280;

  /* Severity — deliberately distinct from health so they never read as the same axis */
  --sb-sev-low:      #4b7bec;
  --sb-sev-medium:   #b8860b;
  --sb-sev-high:     #c2571a;
  --sb-sev-critical: #8b1a1a;

  /* Lifecycle states */
  --sb-state-open:       #b02a2a;
  --sb-state-dispatched: #b8860b;
  --sb-state-fixed:      #1a7f5a;

  /* Surfaces */
  --sb-tile-bg:     #ffffff;
  --sb-tile-border: #e5e7eb;
  --sb-tile-radius: 12px;

  /* Touch targets — Tech Mode */
  --sb-touch-min:   48px;
  --sb-touch-gap:   12px;
}
@media (prefers-color-scheme: dark) { /* dark-mode overrides — required, Odoo 17+ ships dark mode */ }
```


Plus a small component set: `.sb-chip`, `.sb-chip--sev-critical`, `.sb-meter`, `.sb-meter__fill`, `.sb-tile`, `.sb-delta--up/--down`.

**Rule:** every colour token pairs with an icon token. `--sb-sev-critical` always renders with `fa-exclamation-triangle`. Colour never carries meaning alone (fixes D13).

**Done when.** No hardcoded hex exists in any CMMS view or component; contrast ratio ≥ 4.5:1 verified in both light and dark mode.

---

### SL-F05 · **Column Discipline** — list ergonomics pass

*Fixes D8*

**Change**, applied to all six lists:

- `optional="hide"` on secondary columns (`total_runtime_hours`, `total_downtime_hours`, `period_days`, `payload_json`) so users opt in via the column picker.
- Explicit `width` on short columns; `nolabel`/short `string` on long headers (`Period (days)` → `Period`, `Availability (%)` → `Avail.`).
- `sum=` aggregates on every numeric column so grouped views produce real totals.
- `widget="progressbar"` on `availability_pct`; `widget="float_time"` on `mtbf_hours` / `mttr_hours` / downtime — these are hours, and `2,160.00` is far less legible than `2160:00` or `90d 0h`.
- `decoration-*` bound to the SL-F04 bands, not to ad-hoc conditions.
- `default_order` set on every model (`as_of_date desc, availability_pct asc` for reliability; `severity desc, reported_at asc` for alerts).

**Done when.** No header is clipped at 1280 px; every numeric column has a sensible aggregate.

---

### SL-F06 · **Density Switch** — comfortable / compact / tech

*Precursor to D12 fix*

**Change.** A three-position density control in the CMMS control panel, persisted per user via `res.users` setting.

- **Comfortable** — Odoo default.
- **Compact** — reduced row height, for managers scanning 200 rows.
- **Tech** — 48 px minimum row height, 18 px base font, oversized checkboxes and buttons, high-contrast borders. Automatically forced when viewport < 768 px or when the user is in `group_cmms_tech`.

**Implementation.** A body-level class (`o_sb_density--tech`) toggled by a small OWL control-panel button; all sizing in SL-F04 tokens so the switch is a single variable swap.

**Done when.** A user on a 10" shop-floor tablet can hit every interactive control without zooming.

---

### SL-F07 · **Empty States That Teach**

*Fixes D5*

**Change.** Replace all six `Create new document` placeholders with purposeful `nocontent` helpers that explain what the screen is for, what generates the records, and what to do next.

```xml
<field name="help" type="html">
  <p class="o_view_nocontent_smiling_face">No reliability data for this period</p>
  <p>MTBF/MTTR snapshots are generated nightly for every piece of equipment with a
     work center assignment. If this is empty, either the scheduled action hasn't
     run yet or your filter is too narrow.</p>
  <p><a href="#" data-oe-action="clear_filters">Clear filters</a> ·
     <a href="#" data-oe-action="run_snapshot">Run snapshot now</a></p>
</field>
```


Distinguish three empty states per screen, because they mean completely different things:

1. **Never had data** — onboarding copy.
2. **Filtered to nothing** — offer a one-click filter reset.
3. **Genuinely all-clear** — celebrate it. "No open breakdowns. All 5 assets above 95 % availability." is a *result*, not an absence.

**Done when.** No screen in the family shows generic boilerplate.

---

### SL-F08 · **Load Bearing** — performance and growth guards

*Fixes D14*

**Change.**

- `index=True` on `mtbf_mttr_report.as_of_date`, `equipment_id`; `breakdown_alert.state`, `severity`, `reported_at`; `service_contract.date_end`, `state`; `asn_3pl.state`, `picking_id`.
- Default action `context` applies `search_default_f_30d` on the reliability screen so the list never opens unbounded.
- All KPI computations use `read_group`, never Python loops over `search()`.
- Add a retention policy: roll `mtbf_mttr_report` rows older than 13 months into monthly aggregates (`ir.cron`), keeping the daily grain hot and the archive small.

**Done when.** Reliability list first paint < 800 ms with 10,000 seeded records.

---

# PHASE 1 — SCREEN UPGRADES

*Native Odoo view types only. Big visible payoff, low risk, upgrade-safe.*

---

## 1A · Screen: **Equipment Reliability — MTBF/MTTR** (action 1401)

> This screen has 241 rows of which roughly 48 carry information. Everything below is in service of inverting that ratio.

### SL-01 · **Reliability Ledger** — the rebuilt list

*Fixes D9, D8*

**Change.** The list stops being a log and becomes a health register.

- **Default grouping: `equipment_id`**, collapsed. Five groups instead of 241 rows. Each group header shows the aggregate that matters (avg availability, total breakdowns, total downtime).
- `availability_pct` renders as `widget="progressbar"` with SL-F04 band colouring — the eye finds the 47 % bar instantly.
- New **Trend** column: a text-mode delta (`▲ 2.2` / `▼ 4.1` / `–`) comparing this snapshot to the prior one for the same equipment. Requires a stored computed `availability_delta`.
- New **vs Expected** column: `mtbf_hours / equipment_id.expected_mtbf` as a percentage, with the SL-F04 bands. This connects the two existing fields nobody is currently reading.
- Row decorations bound to *availability bands*, not breakdown count. A machine with one breakdown but 99 % availability should not be red.
- `optional="hide"` on `period_days`, `total_runtime_hours`, `total_downtime_hours`.
- `float_time` widget on all hour fields.
- Icon prefix on the equipment cell reflecting `southbrook_condition`.

```xml
<list string="Reliability" default_order="as_of_date desc, availability_pct asc"
      decoration-danger="availability_pct &lt; 80"
      decoration-warning="availability_pct &gt;= 80 and availability_pct &lt; 95"
      decoration-muted="availability_pct &gt;= 98">
  <field name="equipment_id" widget="sb_equipment_chip"/>   <!-- Phase 2 widget; plain m2o until then -->
  <field name="as_of_date"/>
  <field name="availability_pct" widget="progressbar" string="Avail." sum="Avg"/>
  <field name="availability_delta" string="Trend" widget="sb_delta" optional="show"/>
  <field name="mtbf_hours"  widget="float_time" string="MTBF" sum="Total"/>
  <field name="mtbf_vs_expected_pct" string="vs Expected" widget="progressbar" optional="show"/>
  <field name="mttr_hours"  widget="float_time" string="MTTR" sum="Total"/>
  <field name="breakdown_count" string="Failures" sum="Total"/>
  <field name="total_downtime_hours" widget="float_time" optional="hide" sum="Total"/>
  <field name="total_runtime_hours"  widget="float_time" optional="hide" sum="Total"/>
  <field name="period_days" optional="hide"/>
</list>
```


**Done when.** Opening the screen surfaces CNC-001 as the problem asset without the user filtering, sorting, or scrolling.

---

### SL-02 · **Fleet Health Board** — new kanban

*New view type for this model*

**Change.** One card per **equipment** (not per snapshot) showing its current reliability posture. Backed by a `read_group` on latest `as_of_date`.

Card anatomy:

```text
┌──────────────────────────────────────┐
│ ● Biesse Rover K FT      CNC-001     │  ← condition dot + name + serial
│ ████████░░░░░░░░░░░░  47.8 %         │  ← availability meter, banded
│ MTBF 1032 h  ▼        MTTR 1128 h ▲  │  ← paired metrics with trend arrows
│ Expected 2160 h · 48 % of target     │  ← the connection to expected_mtbf
│ ⚠ 1 open breakdown · 3 MOs blocked   │  ← live cross-model badges
│ Next failure est. Aug 19             │  ← estimated_next_failure
│ [ Log Breakdown ]  [ Open History ]  │  ← two actions, thumb-sized
└──────────────────────────────────────┘
```


Kanban `default_group_by="equipment_id.category_id"`, `records_draggable="false"`. Cards inherit SL-F04 tokens; card left border is a 4 px health band.

**Done when.** A manager can assess the entire fleet in one screen without opening a record.

---

### SL-03 · **Reliability Pivot**

*Fixes D6*

**Change.** Add `pivot` to `view_mode`.

- Rows: `equipment_id` → `as_of_date:month`
- Cols: none by default
- Measures: `availability_pct` (avg), `mtbf_hours` (avg), `mttr_hours` (avg), `breakdown_count` (sum), `total_downtime_hours` (sum)
- `<pivot sample="1" display_quantity="1">` with `<field name="availability_pct" type="measure" widget="percentage"/>`

**Done when.** Month-over-month fleet reliability is one click from the list.

---

### SL-04 · **Trend Wall** — upgraded graph

**Change.** The existing graph view is unconfigured. Make it a multi-series line chart: X = `as_of_date:day`, series = `equipment_id`, measure = `availability_pct` (avg), with a secondary saved config for MTBF. Add `stacked="0"`, `<field name="as_of_date" interval="day" type="row"/>`. Set `graph` as the **default view for `group_cmms_manager`** via a separate role-scoped action.

---

### SL-05 · **Failure Cohort**

**Change.** Cohort view (`web_cohort`, Enterprise) on `breakdown_alert`: `date_start = reported_at`, `date_stop = maintenance_request_id.close_date`, interval month, measure count. Answers "of the machines that failed in March, how many failed again within 30/60/90 days" — the classic repeat-failure question a CMMS should answer and this one currently cannot.

*If the instance is Community:* substitute a pivot with a stored `days_since_previous_failure` bucket field.

---

### SL-06 · **Equipment Reliability Card** — form redesign

**Change.** The form stops being a read-only field dump and becomes an asset briefing.

- **Stat button row** (`oe_button_box`): Breakdowns (90d) · Open Work Orders · Impacted MOs · Active Contracts · Downtime Cost. Each drills through to a filtered list (Principle 4).
- **Header ribbon**: availability band as a full-width coloured strip with the number in 32 px type. This is the one thing the reader came for.
- **Left group**: equipment identity pulled from `maintenance.equipment` (`model`, `serial_no`, `location_id`, `workcenter_id`, `category_id`, `technician_user_id`).
- **Right group**: the period metrics, with `expected_mtbf` shown *adjacent to* `mtbf_hours` so the comparison is unavoidable.
- **Notebook tabs**: `Failure History` (related breakdown alerts) · `Coverage` (related service contracts + `warranty_date`) · `Production Impact` (`southbrook_impacted_production_ids`) · `Notes`.
- **Chatter stays** — repurpose it as *Reliability Annotations*. A snapshot record with a chatter is unusual, but it means a lead can annotate "MTBF dip caused by tooling change, not machine fault" against a specific data point. Rename the chatter section accordingly and add a `Log annotation` quick button.

---

## 1B · Screen: **Breakdown Alerts** (action 1402)

### SL-07 · **Triage Board** — kanban rework

**Change.** Default view becomes kanban for `group_cmms_lead`. `default_group_by="state"` with columns Open → Dispatched → Fixed, drag-to-progress enabled (dragging to Dispatched calls `action_dispatch`).

Card anatomy:

- Left border 4 px in severity colour + severity icon + label text (never colour alone).
- **Elapsed timer** — "Open 3h 12m", recomputed client-side, turning amber past 4 h and red past 24 h. This is the single most useful thing you can put on a breakdown card and it does not exist today.
- Equipment name + work center.
- `affected_production_ids` count as a red badge — "3 MOs blocked".
- Reporter avatar.
- Footer: `[Dispatch]` primary button, thumb-sized.

Add `<progressbar field="severity" colors='{"critical":"danger","high":"warning","medium":"info","low":"muted"}'/>` to the kanban column headers so each state column shows its severity mix at a glance.

### SL-08 · **Escalation Ladder** — SLA ageing

**Change.** New stored computed `age_hours` and `sla_band` (`fresh|ageing|breached`) on the alert model, driven by severity (critical: 1 h, high: 4 h, medium: 24 h, low: 72 h). Surface as:

- A list column with a coloured pill.
- A search filter set (SL-F01).
- An `ir.cron` that escalates breached alerts by creating an `mail.activity` on the maintenance team lead.

### SL-09 · **One-Tap Dispatch** — form for the floor

**Change.** The form currently has the right buttons in the wrong ergonomics.

- Header buttons become full-width, 56 px tall stacked blocks in Tech density.
- `action_dispatch` gets a confirmation sheet showing *what will happen* (creates maintenance request, notifies team X, assigns technician Y) rather than firing silently.
- `action_block_affected_mos` is destructive and currently unguarded — add `confirm="This will block N manufacturing orders. Continue?"` with N interpolated.
- Add a **Report Breakdown** quick-create flow: equipment (barcode/QR scannable), severity (four big buttons, not a dropdown), description (voice-to-text friendly textarea), photo attachment. Three taps from cold start to logged alert.

### SL-10 · **Blast Radius Panel**

**Change.** `affected_production_ids` is currently an m2m tag soup. Render it as an embedded list inside a form notebook tab showing MO reference, product, quantity, date planned, and — the point — **estimated value at risk**. Add a computed `production_value_at_risk` monetary summing the affected MOs. A breakdown stops being an inconvenience and becomes a dollar figure.

### SL-11 · **Alert Calendar & Map**

**Change.** Add `calendar` (`date_start = reported_at`, colour by `severity`) and `activity` view (fixes D7) to `view_mode`. If `maintenance.equipment.location_id` resolves to a physical location with coordinates, add the `map` view for multi-site operations.

---

## 1C · Screen: **Equipment Service Contracts** (action 1403)

### SL-12 · **Coverage Timeline** — Gantt

**Change.** Add `gantt` view (Enterprise): `date_start = date_start`, `date_stop = date_end`, `default_group_by = vendor_id`, colour by `state`. Contract coverage is inherently an interval-on-a-timeline problem and a list is the wrong shape for it. Today's calendar view shows a contract as a series of unhelpful day-cells.

*Community fallback:* the Phase 2 **Coverage Heat Grid** (SL-X9).

### SL-13 · **Expiry Runway** — list rework

**Change.**

- `days_to_expiry` as a **reversed progressbar** against `expiry_alert_at` — the bar drains as expiry approaches.
- Decorations from the alert threshold, not a hardcoded number: a contract with a 180-day alert should turn amber at 180, not at 30.
- Group by `state` by default, `expired` group collapsed.
- `annual_value` with `sum="Total Annual Spend"` — the manager wants the portfolio number.
- Equipment count column (`equipment_ids` count) + computed `cost_per_equipment`.

### SL-14 · **Coverage Sheet** — form rework

**Change.**

- Stat buttons: Equipment Covered · Breakdowns Under This Contract (90d) · Value per Asset · Days Remaining.
- **Renewal action** in the header: `action_renew` clones the contract forward by its term with dates shifted, in draft.
- **Zero-value guard** (fixes D11): if `annual_value == 0` on a non-draft contract, show an inline warning banner — "No annual value recorded. Portfolio spend reporting will under-count this contract." — with a jump-to-field link. Do not make it a blocking constraint; make it visible.
- Equipment list rendered with coverage dates and each asset's current availability, so you can see whether you are paying for coverage on your worst machine.

### SL-15 · **Uncovered Equipment Radar**

*A genuinely new capability, still native*

**Change.** A new action + list on `maintenance.equipment` with domain `equipment not in any active service_contract.equipment_ids AND warranty_date < today`. Columns: equipment, category, criticality proxy (`availability_pct` 90 d avg), `cost`, `latest_failure_date`, `maintenance_open_count`.

**Why.** The contract screen currently answers "what am I paying for". It cannot answer "what am I exposed on" — which is the question that costs money. Ordered by lowest availability, this is a procurement to-do list.

---

## 1D · Screen: **Landed Cost Templates** (action 1404)

### SL-16 · **Rate Card** — list rework

**Change.** Add a computed `total_pct` (`freight + duty + customs + handling`) as the primary column with a stacked-segment bar showing the four components in distinct hues. Four separate percentage columns are four numbers the user has to add in their head; one bar with four segments is the same data, instantly.

### SL-17 · **Cost Stack Simulator** — form

**Change.** Add a non-stored `sample_base_amount` (default 10,000) and computed `sample_freight/duty/customs/handling/total`. Editing the base recomputes live. Render as a small waterfall table inside the form:

```text
Base value                        10,000.00
  + Freight        8.0 %             800.00
  + Duty           4.5 %             450.00
  + Customs        1.2 %             120.00
  + Handling       2.0 %             200.00
  ──────────────────────────────────────────
  Landed cost     15.7 %          11,570.00
```


A template that is currently four abstract percentages becomes a number the buyer can reason about.

### SL-18 · **Vendor Mapping Matrix**

**Change.**

- `default_for_vendor_ids` with `widget="many2many_tags"` and vendor colour.
- **Conflict detection**: a constraint + inline warning when a vendor is the default for more than one template — currently silently possible and silently wrong.
- A "Vendors with no template" smart link on the list view, mirroring SL-15's exposure logic.

---

## 1E · Screen: **CMMS / WMS Command Deck** (action 1405)

### SL-19 · **Command Deck** — kill the list, build the dashboard

*Fixes D4. Native implementation; SL-X6 upgrades it in Phase 2.*

**Change.** Phase 1 native version: replace the list default with a **kanban view rendered as KPI tiles** (a standard Odoo idiom — `<kanban class="o_sb_deck">` with card templates styled as tiles). Six tiles, each clickable, each drilling through to the underlying filtered records:

| Tile | Field | Drills to |
| :---- | :---- | :---- |
| **Open Breakdowns** | `open_breakdowns_count` | Breakdown Alerts filtered `state = open` |
| **Contracts Expiring (60d)** | `service_contracts_expiring_60d_count` | Service Contracts filtered `Expiring ≤60` |
| **Oversize Pickings Pending** | `oversize_pickings_pending_count` | 3PL ASNs filtered `oversize_load_flag = True` |
| **Fleet MTBF (30d avg)** | `mtbf_avg_30d` | Reliability pivot |
| **Fleet MTTR (30d avg)** | `mttr_avg_30d` | Reliability pivot |
| **Landed Cost Value (30d)** | `landed_cost_value_30d` | Landed cost analysis |

Each tile: large value, small label, **delta vs previous snapshot** (`▲ 3 since yesterday`), and a health colour from SL-F04. `as_of` rendered once as "Last updated 2 hours ago" — freshness is a first-class piece of information on a dashboard and is currently a raw datetime in a table cell.

**Done when.** Nobody ever sees `mi_tiles` as a table again.

### SL-20 · **Snapshot History**

**Change.** `mi_tiles` currently holds one record. Make the nightly cron append rather than overwrite, then:

- Add `graph` and `pivot` to `view_mode` for KPI trending.
- Each Command Deck tile gains a **sparkline** of its last 30 snapshots (native: a small inline SVG in the kanban template; no OWL needed).
- Retention: keep daily for 90 days, weekly thereafter.

**Why this matters.** A KPI without a trend is a number without a verdict. `open_breakdowns_count = 3` is meaningless; `3, down from 11 last week` is a management decision.

---

## 1F · Screen: **3PL Advance Shipping Notices** (action 1370)

### SL-21 · **Shipment Pipeline** — kanban

**Change.** Add `kanban` to `view_mode`, `default_group_by="state"` (Draft → Sent → Acknowledged → Shipped → Delivered). Card shows carrier, tracking number (click-to-copy), destination from `picking_id`, an **OVERSIZE** badge when `oversize_load_flag`, and permit jurisdiction chips. Column-header progressbars split by oversize vs standard.

### SL-22 · **Permit Corridor**

**Change.** `permit_jurisdictions` is a bare char field holding what is semantically a list. Parse and render as chips; add a `many2many` to a new `southbrook.permit.jurisdiction` model so jurisdictions become filterable and reportable. Surface a warning banner on oversize loads with no jurisdictions recorded — that is a truck that will be stopped.

### SL-23 · **Payload Inspector**

**Change.** `payload_json` in a raw `<field>` textarea is unreadable and un-diffable. Replace with a JSON viewer: pretty-printed, syntax-coloured, collapsible nodes, copy button, and a validity indicator. Phase 1 can ship a read-only pretty-print via a computed HTML field; Phase 2 makes it a proper OWL widget with collapse and diff-vs-last-send.

### SL-24 · **ASN Timeline**

**Change.** A horizontal state timeline in the form header (Draft → Sent → Acked → Shipped → Delivered) with the timestamp under each completed step and elapsed time between steps. Add `date_sent`, `date_acked`, `date_shipped`, `date_delivered` datetime fields — currently the model records *which* state but not *when it got there*, which makes carrier SLA measurement impossible.

---

# PHASE 2 — BEYOND STANDARD ODOO

*Custom OWL components and view types. This is where the family stops looking like Odoo.*

Each item states why native could not do it — build nothing here that Phase 1 already solved.

---

### SL-X1 · **Plant Floor Live Map**

> *Native cannot: render a spatial floorplan with live per-asset state.*

A custom OWL **view type** (`js_class="sb_floorplan"`, registered in `view_registry`) rendering an SVG shop-floor plan of the Southbrook facility. Each machine is a node positioned by new `floor_x` / `floor_y` fields on `maintenance.equipment`.

- Node fill = availability band (SL-F04). Node ring = `southbrook_condition`.
- **Pulse animation** on any asset with an open breakdown; pulse rate scales with severity.
- Work centers drawn as translucent zones; material flow arrows between them.
- Hover → tooltip card (MTBF, MTTR, next failure estimate, open WOs).
- Click → the SL-06 Equipment Reliability Card in a side panel, floorplan stays visible.
- **Time scrubber** along the bottom: drag back through the last 90 days and watch the floor change colour. Failure clustering by zone becomes visible in a way no table will ever show.
- Live via `bus.bus` — a breakdown logged on a tablet lights up the manager's wall display within a second.

Editor mode: managers drag nodes to set `floor_x`/`floor_y`; no XML editing to lay out the plant.

**Effort:** L. **Payoff:** This is the screen that goes on the wall-mounted display in the plant office.

---

### SL-X2 · **Reliability Bathtub Curve**

> *Native cannot: plot failure rate against asset age with regime shading.*

An OWL chart component plotting failure rate (failures per 1,000 running hours) against asset age (derived from `effective_date`), with the three classic regimes shaded: infant mortality, useful life, wear-out. Overlay each asset as a dot positioned by its own age and failure rate.

**Why it matters.** It tells you *why* a machine is failing. A new machine failing often is a commissioning/training problem. An old machine failing often is a replacement decision. Those are different budgets and different conversations, and no standard Odoo view distinguishes them.

---

### SL-X3 · **Downtime Pareto & Waterfall**

> *Native pivot can rank; it cannot draw the 80 % cumulative line that makes a Pareto a Pareto.*

Combined bar + cumulative-line chart of `total_downtime_hours` by equipment (and, once failure-cause coding is added, by cause). The 80 % threshold line renders as a hard rule; everything left of it is highlighted as "the vital few". Clicking a bar drills to the contributing snapshots.

Add a companion **waterfall** showing how total plant downtime decomposes: planned maintenance → unplanned breakdown → changeover → material starvation.

---

### SL-X4 · **Failure Runway**

> *Native cannot: render a per-asset predictive risk band on a timeline.*

A horizontal strip per asset showing the projected window to next failure, computed from `expected_mtbf`, actual `mtbf`, and `latest_failure_date`, with `estimated_next_failure` marked. The band widens with uncertainty (few data points = wide band). Overlaid: scheduled preventive maintenance dates and service contract coverage end.

**The insight it delivers:** an asset whose predicted failure window opens *after* its contract expires is a specific, actionable, and currently invisible risk. Highlight those in red and the screen has paid for itself.

*Model note:* implement the projection as a stored computed field with a documented formula (simple: `latest_failure_date + mtbf_hours`; better: exponential-weighted MTBF over the last N snapshots). Do not present a statistical claim the data cannot support — label the band "estimate" and show the sample size.

---

### SL-X5 · **Blast Radius Graph**

> *Native cannot: render a multi-hop dependency graph.*

A force-directed graph (d3 or cytoscape, bundled — no CDN) launched from a breakdown alert:

```text
Equipment ─→ Work Center ─→ Manufacturing Orders ─→ Sales Orders ─→ Customers
```


Node size = value at risk. Edge thickness = quantity. Nodes turn red as the downstream date slips past commitment. A one-line summary sits above it: *"CNC-001 down 3h — 3 MOs blocked, $47,200 at risk, 2 customer deliveries slip past promise date."*

**Why it matters.** It converts a maintenance event into a commercial event. This is the artifact a maintenance lead forwards to the plant manager to get overtime approved.

---

### SL-X6 · **Command Deck (OWL)**

> *Upgrades SL-19 past what a kanban template can carry.*

A full OWL client action replacing the Phase 1 tile-kanban:

- Responsive tile grid, each tile with live sparkline, delta, and a threshold ring.
- **Drill-in without leaving** — clicking a tile expands it in place into a chart + record list panel.
- Auto-refresh via `bus.bus`, with a visible "live" indicator and last-updated stamp.
- **Role-aware layout**: managers see cost and trend tiles first; leads see queue and SLA tiles first. Same component, different tile manifest per group.
- Configurable — users pick and reorder their own tiles, persisted per user.
- A **wallboard mode** (`?wallboard=1`): no chrome, huge type, 30-second rotation between panels, designed for an unattended shop-floor screen.

---

### SL-X7 · **Tech Mode**

> *Native cannot: deliver a glove-operable, single-task, offline-tolerant floor interface.* *Fixes D12 completely.*

A dedicated full-screen OWL client action, the default landing for `group_cmms_tech`:

- **One question per screen.** No breadcrumb, no side menu, no notebook tabs.
- Home = today's assigned work as large cards: open breakdowns, due preventive maintenance, ordered by SLA urgency.
- **48 px minimum touch targets**, 18 px base type, high-contrast palette tuned for shop lighting and safety glasses.
- **Scan to act** — camera QR/barcode scan on equipment resolves straight to that asset's actions.
- **Voice-to-text** on every description field (Web Speech API); a technician in gloves does not type.
- **Photo capture** attached directly to the alert.
- **Offline tolerance** — service worker caches the assigned work list and queues actions taken while the connection drops, syncing on reconnect with a visible pending-sync indicator. Shop floors have dead spots; a CMMS that fails in a dead spot does not get used.
- Big **Log Breakdown** button, permanently docked. Three taps: scan → severity → submit.

**Effort:** L. **Payoff:** Highest of anything in this document. Data quality in a CMMS is entirely determined by whether the person holding the wrench will use it.

---

### SL-X8 · **Asset Passport**

> *Native cannot: give a physical machine a scannable digital identity page.*

Generate a QR code per `maintenance.equipment` (printable label sheet via a QWeb report). Scanning opens a public-ish, auth-gated mobile page:

- Identity: photo, model, serial, install date, location.
- Health: current availability meter, MTBF vs expected, condition rating and who set it.
- Coverage: warranty status, active service contract, vendor contact with tap-to-call.
- History: last 5 breakdowns, last 5 maintenance requests.
- Documents: manuals, schematics, safety sheets (attachments).
- **One tap: Report a Breakdown** — pre-filled with this equipment.

**Why it matters.** It removes the "which machine is this in the system?" step, which is where most floor-level CMMS data entry dies.

---

### SL-X9 · **Coverage Heat Grid**

> *Native cannot: render an equipment × time coverage matrix. Also the Community fallback for SL-12.*

Rows = equipment, columns = months (rolling 24). Cell colour: green = under active contract, blue = under warranty, amber = expiring within alert window, red = **uncovered**, grey = out of service. Uncovered cells on high-value or low-availability assets get a hatch pattern and float to the top of the sort.

Hovering a cell shows the contract and its annual value. Clicking a red cell opens a "create contract" flow pre-filled with that equipment.

**Why it matters.** Contract gaps are invisible in a list and glaring in a grid.

---

### SL-X10 · **Landed Cost Waterfall Simulator**

> *Upgrades SL-17 from a static table to an interactive model.*

Slider-driven interactive waterfall. Drag freight from 8 % to 11 % and watch the landed cost bar and the final number move in real time. Compare two templates side by side. Feed in an actual PO value from a picker rather than a hypothetical base. Export the comparison as a PDF for a vendor negotiation.

---

### SL-X11 · **3PL Load Board**

> *Native cannot: render a carrier × time swimlane with oversize/permit lanes.*

A timeline/swimlane board of ASNs: one lane per carrier, plus a dedicated **Oversize** lane at the top. Blocks positioned by ship date, length by transit estimate, colour by state. Permit-required loads carry jurisdiction chips and a permit-status traffic light. Drag a block to reschedule; the underlying `stock.picking` date follows.

Above it, a small **capacity strip** showing loads per day against a configurable dock capacity — the classic 3PL question "can we physically ship all of this on Thursday" answered visually.

---

### SL-X12 · **Reliability Ticker**

> *Native cannot: put persistent live status in the systray.*

A systray widget, visible on every screen in Odoo for CMMS groups: `⚙ 2 open · worst 47.8 %` — click for a popover listing open breakdowns and the worst-performing assets, each a link. Turns red and pulses on a new critical alert. `bus.bus`-driven.

**Why it matters.** A breakdown that nobody notices for two hours is a two-hour breakdown regardless of how good the alerts screen is.

---

### SL-X13 · **Downtime Cost Lens**

> *Native cannot: apply a global user-set economic parameter across every view.*

A single configurable `downtime_cost_per_hour` per work center (or per equipment). Once set, every downtime figure in the family gains a monetary companion: the reliability list shows "$14,400" next to "180 h", the Command Deck adds a "Downtime Cost (30d)" tile, the Blast Radius Graph sizes nodes by real dollars.

A control-panel toggle switches the entire family between **Hours** and **Dollars** views. Same data, two audiences: the maintenance lead thinks in hours, the plant manager thinks in dollars, and neither should have to do the conversion.

---

### SL-X14 · **Anomaly Spotlight**

> *Native cannot: proactively surface statistical outliers.*

A background job scanning each nightly snapshot for statistically notable movement — availability dropping more than 2σ from the asset's 30-day mean, MTTR doubling, a first-ever failure on a previously perfect asset, a breakdown pattern clustering by shift or day-of-week.

Findings render as a **dismissible banner strip** at the top of the Command Deck and the Reliability list:

> *"CNC-001 availability fell 12.2 pts over 6 days — steepest decline in 90 days. 3 of 4 failures occurred on second shift."*

Each finding links to the evidence and can be dismissed, snoozed, or converted into a maintenance request. Keep the language descriptive, not diagnostic — the system reports a pattern, the human decides what it means.

---

### SL-X15 · **Shift Handover Digest**

> *Native cannot: compose a cross-model narrative artifact.*

An auto-generated end-of-shift page and PDF: what broke, what got fixed, what is still open and who owns it, which assets degraded, which MOs are blocked, what the next shift must pick up first. One tap to send to the incoming lead.

**Why it matters.** Handover is where maintenance context is lost, every single day. This is a low-glamour, high-value artifact that no standard CMMS view produces.

---

# 3 · Cross-cutting requirements

### 3.1 Accessibility (non-negotiable)

- Colour never sole carrier of meaning — every band, severity, and state pairs with icon + text (SL-F04).
- Contrast ≥ 4.5:1 for text, ≥ 3:1 for graphical objects, verified in light **and** dark mode.
- All custom OWL components keyboard-navigable with visible focus rings; the floorplan and graph views need arrow-key node traversal and a **tabular alternative view** for screen readers.
- `aria-live="polite"` on the Ticker and any auto-refreshing tile.
- Respect `prefers-reduced-motion` — kill the breakdown pulse animation when set.

### 3.2 Keyboard

- `g` `r` → Reliability, `g` `b` → Breakdowns, `g` `d` → Command Deck, `g` `c` → Contracts.
- `n` → new breakdown alert from anywhere in the family.
- `/` → focus search.
- `?` → shortcut overlay.

### 3.3 Mobile / responsive

- Every Phase 1 list gets a mobile card fallback below 768 px (Odoo does some of this; verify each).
- Tech Mode (SL-X7) is the mobile experience for technicians — do not try to make the desktop list work on a phone.
- Test at 360 px (phone), 768 px (tablet portrait), 1024 px (tablet landscape).

### 3.4 Notifications

- Critical breakdown → immediate push to the maintenance team channel.
- SLA breach → escalation activity (SL-08).
- Contract crossing its alert threshold → activity on the contract owner.
- ASN unacknowledged > 24 h → activity on logistics.
- All routed through `mail.activity` / `mail.thread` so they land in Odoo's existing inbox rather than a bespoke system.

### 3.5 What NOT to do

- Do not patch Odoo core `maintenance` or `stock` views. Extend via inheritance in the new module only.
- Do not load charting libraries from a CDN — bundle them.
- Do not use `localStorage` for anything the server should own; user preferences go on `res.users`.
- Do not add a field to a model where a computed related field on an existing model will do.
- Do not build SL-X1 or SL-X7 before Phase 0 ships. The foundation carries them.

---

# 4 · Module structure for implementation

```text
southbrook_cmms_ux/
├── __manifest__.py                 # depends: maintenance, mrp, stock, delivery,
│                                   #          southbrook_cmms, southbrook_integrations
├── models/
│   ├── mtbf_mttr_report.py         # SL-01: availability_delta, mtbf_vs_expected_pct,
│   │                               #        is_below_expected_mtbf
│   ├── breakdown_alert.py          # SL-08: age_hours, sla_band; SL-10: production_value_at_risk
│   ├── service_contract.py         # SL-13/14: cost_per_equipment, action_renew
│   ├── landed_cost_template.py     # SL-16/17: total_pct, sample_* simulator fields
│   ├── mi_tiles.py                 # SL-20: history append + delta computes
│   ├── asn_3pl.py                  # SL-24: date_sent/acked/shipped/delivered
│   ├── maintenance_equipment.py    # SL-X1: floor_x, floor_y; SL-X13: downtime_cost_per_hour
│   ├── permit_jurisdiction.py      # SL-22: new model
│   └── res_users.py                # SL-F06 density pref, SL-X6 tile layout pref
├── views/
│   ├── cmms_menus.xml              # SL-F02 labels, SL-F03 groups=
│   ├── mtbf_views.xml              # SL-01..SL-06
│   ├── breakdown_views.xml         # SL-07..SL-11
│   ├── contract_views.xml          # SL-12..SL-15
│   ├── landed_cost_views.xml       # SL-16..SL-18
│   ├── command_deck_views.xml      # SL-19, SL-20
│   ├── asn_views.xml               # SL-21..SL-24
│   └── search_views.xml            # SL-F01 — all six
├── security/
│   ├── cmms_ux_groups.xml          # tech / lead / manager (§2.1)
│   └── ir.model.access.csv         # SL-F03 sweep
├── data/
│   ├── ir_cron.xml                 # SL-08 escalation, SL-F08 rollup, SL-X14 anomaly scan
│   └── nocontent_help.xml          # SL-F07
├── report/
│   ├── asset_passport_qr.xml       # SL-X8
│   └── shift_handover.xml          # SL-X15
└── static/src/
    ├── scss/
    │   ├── cmms_tokens.scss        # SL-F04
    │   └── density.scss            # SL-F06
    ├── components/
    │   ├── health_meter/           # availability meter
    │   ├── delta_badge/            # SL-01 trend arrows
    │   ├── sparkline/              # SL-20
    │   ├── json_inspector/         # SL-23
    │   ├── state_timeline/         # SL-24
    │   └── reliability_ticker/     # SL-X12 (systray)
    ├── views/
    │   ├── floorplan/              # SL-X1 custom view type
    │   ├── blast_radius/           # SL-X5
    │   ├── heat_grid/              # SL-X9
    │   └── load_board/             # SL-X11
    ├── client_actions/
    │   ├── command_deck/           # SL-X6
    │   └── tech_mode/              # SL-X7
    └── xml/                        # OWL templates
```


---

# 5 · Delivery sequence

| Wave | Contents | Rationale |
| :---- | :---- | :---- |
| **W1 — Foundation** | SL-F01 · SL-F02 · SL-F03 · SL-F04 · SL-F05 · SL-F07 · SL-F08 | Fixes the two critical defects (no search, buried signal) and the trust-breaking access error. Everything else assumes these. |
| **W2 — Reliability** | SL-01 · SL-02 · SL-03 · SL-04 · SL-06 | The screen with 241 rows and the least information density. Biggest visible improvement per hour spent. |
| **W3 — Response** | SL-07 · SL-08 · SL-09 · SL-10 · SL-11 · SL-F06 | Makes breakdowns actionable and starts the floor-ergonomics work. |
| **W4 — Command & Coverage** | SL-19 · SL-20 · SL-12 · SL-13 · SL-14 · SL-15 | Manager layer: dashboard, trends, contract exposure. |
| **W5 — Cost & Logistics** | SL-16 · SL-17 · SL-18 · SL-21 · SL-22 · SL-23 · SL-24 | Completes native coverage of the family. |
| **W6 — Floor** | SL-X7 · SL-X8 · SL-X12 | Highest-payoff custom work. Data quality lives here. |
| **W7 — Insight** | SL-X6 · SL-X1 · SL-X4 · SL-X3 · SL-X13 · SL-X14 | The screens that make the system worth looking at. |
| **W8 — Depth** | SL-X2 · SL-X5 · SL-X9 · SL-X10 · SL-X11 · SL-X15 | Specialist views, built once the base is proven. |

---

# 6 · Acceptance criteria

**Foundation**

- [ ] Every one of the six models has a working search view with ≥ 4 filters and ≥ 3 group-bys.
- [ ] No user in any group receives an access error from a visible CMMS/3PL menu item.
- [ ] No reference string contains a repeated token.
- [ ] No screen displays the default "Create new document" empty state.
- [ ] Zero hardcoded hex values in CMMS views or components.
- [ ] Reliability list first paint < 800 ms at 10,000 records.

**Screens**

- [ ] Opening Reliability surfaces the worst-performing asset without user input.
- [ ] Every KPI, badge, and aggregate in the family is clickable and drills to its records.
- [ ] Command Deck is never rendered as a table.
- [ ] Every numeric list column has an aggregate; every hour field uses a time widget.
- [ ] Breakdown cards show elapsed time since report.
- [ ] Contract screen can answer "what equipment is uncovered" in one click.

**Cross-cutting**

- [ ] Contrast ≥ 4.5:1 verified in light and dark mode across all custom components.
- [ ] No status conveyed by colour alone anywhere in the family.
- [ ] Every custom view has a keyboard path and a tabular screen-reader alternative.
- [ ] All interactive controls ≥ 48 px in Tech density.
- [ ] `prefers-reduced-motion` respected.

**Phase 2**

- [ ] Tech Mode completes "log a breakdown" in ≤ 3 taps from cold start.
- [ ] Tech Mode queues and syncs actions taken offline.
- [ ] Floorplan reflects a new breakdown within 2 s via bus.
- [ ] Every predictive figure is labelled as an estimate and shows its sample size.

---

## Appendix A — Enhancement index

**Foundation (8):** SL-F01 Ground Truth · SL-F02 Nameplate · SL-F03 Doorman · SL-F04 Signal Palette · SL-F05 Column Discipline · SL-F06 Density Switch · SL-F07 Empty States That Teach · SL-F08 Load Bearing

**Screen upgrades (24):** SL-01 Reliability Ledger · SL-02 Fleet Health Board · SL-03 Reliability Pivot · SL-04 Trend Wall · SL-05 Failure Cohort · SL-06 Equipment Reliability Card · SL-07 Triage Board · SL-08 Escalation Ladder · SL-09 One-Tap Dispatch · SL-10 Blast Radius Panel · SL-11 Alert Calendar & Map · SL-12 Coverage Timeline · SL-13 Expiry Runway · SL-14 Coverage Sheet · SL-15 Uncovered Equipment Radar · SL-16 Rate Card · SL-17 Cost Stack Simulator · SL-18 Vendor Mapping Matrix · SL-19 Command Deck · SL-20 Snapshot History · SL-21 Shipment Pipeline · SL-22 Permit Corridor · SL-23 Payload Inspector · SL-24 ASN Timeline

**Beyond standard Odoo (15):** SL-X1 Plant Floor Live Map · SL-X2 Reliability Bathtub Curve · SL-X3 Downtime Pareto & Waterfall · SL-X4 Failure Runway · SL-X5 Blast Radius Graph · SL-X6 Command Deck (OWL) · SL-X7 Tech Mode · SL-X8 Asset Passport · SL-X9 Coverage Heat Grid · SL-X10 Landed Cost Waterfall Simulator · SL-X11 3PL Load Board · SL-X12 Reliability Ticker · SL-X13 Downtime Cost Lens · SL-X14 Anomaly Spotlight · SL-X15 Shift Handover Digest

**Total: 47 named enhancements.**

## Appendix B — Verification notes

Everything in §1 was read from the live database via `call_kw` against `ir.ui.menu`, `ir.actions.act_window`, `ir.model.fields`, and `ir.ui.view`, and cross-checked against screenshots of actions 1401, 1402, 1403, 1405, and 1370. Two items to re-confirm before building:

1. **Enterprise vs Community.** SL-05 (cohort) and SL-12 (gantt) require Enterprise. Fallbacks are specified inline. Confirm the licence before scheduling W4.
2. **`service_contract.vendor_id`** — the field name was inferred from the list header "Vendor"; confirm whether it is `vendor_id` or `partner_id` before writing views.

One assumption worth flagging: the `mi_tiles` nightly job is currently assumed to overwrite a single record (the screen shows exactly one row). SL-20 depends on changing it to append. Verify the cron's behaviour before building the sparklines.

