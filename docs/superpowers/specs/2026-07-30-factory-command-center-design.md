# Factory Command Center — design

**Date:** 2026-07-30
**Supersedes:** the Central Command dashboard at `/odoo/action-1512`
**Module:** `southbrook_command_center` (19.0.2.0.0 → 19.0.3.0.0)
**Status:** approved for planning

---

## 0. Summary

Rename the Central Command dashboard (`ir.actions.client` 1512, tag
`southbrook_command_center.central_command`) to **Factory Command Center** and
rebuild it as four zones driven by a lens system: a pinned context bar of
click-to-filter truth chips, a ranked Do Next list that replaces the issue
firehose, a lens rail carrying workload and resource depth, and a detail drawer
in the right column.

Two findings during design moved work earlier in the plan. Both are data-integrity
defects rather than presentation defects, and both are instances of the same
error in different directions: **absent data being given a value.**

---

## 1. Findings that shape the design

### 1.1 The 20,668-issue firehose is a keying bug

Measured on production 2026-07-30:

| Measure | Value |
|---|---|
| Open `southbrook.command.exception` rows | 38,910 |
| — of which `cutlist_divergence` | 20,673 |
| — of which `mi_blocker` | 18,240 |
| Distinct `source_res_id` | 38,910 (every row unique) |
| Distinct manufacturing orders behind them | **9** |
| Live `southbrook.mi.check` rows | **17** (ids 60367–60383) |

`southbrook.command.exception` dedupes on
`unique(source_model, source_res_id, exception_type)` where `source_res_id` is the
`southbrook.mi.check` **row id**. The `Southbrook MI: sweep recompute` cron
(15-minute interval) deletes and recreates those rows, so ids churn — max id 60383
against 17 live rows. Every sweep mints fresh ids, the `create` hook in
`models/mi_check.py` fires, the uniqueness constraint never matches, and a new
exception row is written.

The arithmetic confirms it exactly: 2,280 duplicates per MO × 8 MOs = 18,240, and
2,280 ≈ 24 days × 96 sweeps/day since first materialization on 2026-07-05. Growth
is ~2,600 rows/day and unbounded.

Clustering alone would be cosmetics over a leak. The key must change.

### 1.2 The health score scores absent data in both directions

Live `factory_health_score()` returns **55/100, band red**:

| Component | Reads | Points | Reality |
|---|---|---|---|
| OEE (7d avg) | 0% | 0 of 30 | No OEE snapshots exist — absent data scored **zero** |
| On-time delivery (30d) | 100% | 25 of 25 | **0 outgoing pickings have ever completed** — absent data scored **perfect** |
| First-pass yield (7d) | 100% | 20 of 20 | 0 closed MOs — same default shape |
| Open MI blockers | 9 | 0 of 15 | Real |
| Work centers overloaded | 0/14 | 10 of 10 | Real |

`southbrook_exec_dashboard/models/exec_snapshot.py:197` reads `otd = 100.0`
before the `if pickings_30d:` guard, so an empty result set yields a perfect
score. Verified: zero `state=done` outgoing `stock.picking` rows exist in the
database at all, and zero `state=done` manufacturing orders.

The scored-as-zero direction is visible and deflates. The scored-as-perfect
direction is invisible and inflates — 45 of the 55 points currently displayed
rest on metrics with no observations behind them.

Honestly recomputed: **10 of 25 available points, 75 points marked "no data."**

### 1.3 The two screens disagree because one measures nothing

| Metric | Command Center | Process Explorer |
|---|---|---|
| On-time delivery | 100% | no data (0 closed MOs to measure) |
| Work centers | 14 (all active) | 8 (only those carrying open work) |
| Late orders | not surfaced | **10 of 13** |

The dashboard reports 100% on-time delivery while Process Explorer shows ten of
thirteen orders past deadline.

### 1.4 Lens data availability

| Source | Rows | Consequence |
|---|---|---|
| `mrp.production` / `mrp.workorder` / `mrp.workcenter` | 13 / 99 / 14 | Orders, Stations, Schedule, Andon, Decide are live |
| `southbrook.tool.asset` (has `lifecycle_state`) | 30 | Assets lifecycle + swap scheduling live |
| `southbrook.tool.usage` | 0 | Wear / remaining life have **no source**; no wear field exists on any model |
| `hr.employee` / `hr.employee.skill` / `hr.attendance` | 2 / 0 / 0 | People lens has nothing to compute from |

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Fix the dedupe key to stable business identity (MO + check name + category), not the churning `mi.check` id | Collapses 38,910 rows to ~17; stops unbounded growth |
| D2 | Archive the historical duplicates (`active=False`), not delete — every MI-sourced row beyond the one surviving row per live condition | Reversible; stays auditable. The count is measured and logged at migration time, not hard-coded: it was ~38,890 on 2026-07-30 and grows ~2,600/day until D1 lands |
| D3 | Every lens ships, including those without data, in an honest-empty state naming its unpopulated sources | A hidden gap is untracked; a named gap is a work item. Same contract both screens already hold |
| D4 | Assets ships partial: lifecycle + swap scheduling live, wear/remaining-life rows marked "no data" | Half the lens has a real source today |
| D5 | Command Center consumes `mrp.process.explorer.get_data()` at runtime, soft-detected, no manifest dependency | Agreement becomes structural rather than tested; no cross-repo dependency edge while `mrp_process_explorer` has no permanent home |
| D6 | Capture project (attendance, skills, tool-usage logging) is a separate follow-on; lens contracts are written now to accept it | Its blocker is floor process, not code |
| D7 | Chips carry explicit units | `orders` counts MOs; `running`/`ready`/`blocked` count work orders. Unlabelled adjacency implies a false total |
| D8 | Do Next renormalises over available components when a term has no data | Prevents "no cost loaded" ranking identically to "worthless" |

---

## 3. Architecture

### 3.1 Zones

```
┌───────────────────────────────────────────────────────────────────┬─────────────┐
│ ZONE 1 · CONTEXT BAR (pinned)                                     │             │
│ scope ▾   live ● 14s   provenance: 13 MOs · native mrp.production │  ZONE 4     │
│ [orders 13][running 1][ready 36][blocked 57][late 10][at-risk 0]  │  DETAIL     │
│ [material-short 1][capacity-over 0]        ← filter every lens    │  DRAWER     │
├───────────────────────────────────────────────────────────────────┤             │
│ ZONE 2 · DO NEXT — max 5, ranked                                  │  (right     │
│ 1 ▸ WH/MO/00024 · load cutlist · CNC-1 · 40m · unblocks 6 · Fabio │   column)   │
│     14d late · $12,400              [ Start ]  [ ↗ ]              │             │
├───────────────────────────────────────────────────────────────────┤             │
│ ZONE 3 · LENSES                                                   │             │
│ Orders │ Stations │ People │ Materials │ Assets │ Schedule │ ...  │             │
│ ┌───────────────────────────────────────────────────────────────┐ │             │
│ │ (active lens body — dense, numerically aligned)               │ │             │
└───────────────────────────────────────────────────────────────────┴─────────────┘
```

### 3.2 Data flow

One payload per refresh. `/command_center/bootstrap` calls
`mrp.process.explorer.get_data()` through a soft-detect helper, merges Command
Center's own exception/cluster/health payload, and returns a single object. The
chips, every lens, the drawer and Do Next all read that one object. A lens cannot
disagree with the bar because it has no independent query to disagree from.

When `mrp.process.explorer` is absent from the registry the helper degrades to
Command Center's own queries and the provenance line says so — the screen never
silently reports numbers from a different source than it claims.

### 3.3 Component boundaries

| Unit | Purpose | Depends on |
|---|---|---|
| `explorer_bridge.py` | Soft-detect + call `get_data()`, normalise, degrade | nothing in-module |
| `truth_chips.py` | Derive the 8 chip counts from the payload | bridge |
| `do_next.py` | Blended urgency ranking, renormalising over available terms | bridge |
| `issue_clusters.py` | Group open exceptions, diagnose root cause, re-score severity | exception model |
| `health.py` (rewrite of `command_center.py`) | Score out of *available* points, per-component data state + trend | exception model |
| `factory_command_center.esm.js` | Zone shell, lens rail, shared filter state | — |
| `lens_*.js` | One file per lens, each rendering from the shared payload | shell |
| `detail_drawer.js` | `{model, res_id, title, facts[], actions[]}` | shell |

Each lens is independently readable and independently testable, and the current
489-line monolithic client action does not grow into a 2,000-line one.

---

## 4. Zone 1 — context bar

Pinned. Carries scope selector, live/sync freshness, data-provenance line, and
eight chips.

| Chip | Source in payload | Unit | Live |
|---|---|---|---|
| orders | `workload.orders` | orders | 13 |
| running | `workload.progress` | steps | 1 |
| ready | `workload.ready` | steps | 36 |
| blocked | `workload.blocked` | steps | 57 |
| late | `workload.late` | orders | 10 |
| at-risk | `workload.at_risk` | orders | 0 |
| material-short | rows with `mat_short` | orders | 1 |
| capacity-over | work centers whose `queued_days` exceeds `command_center.chips.capacity_over_days` (default 1.0) | stations | 0 |

Chips are multi-select. Selection lives in one shared filter state that every
lens subscribes to, so a chip filters all lenses at once.

---

## 5. Zone 2 — Do Next

Maximum five items. Each carries next action, resource, duration, downstream
unblock count, owner, and impact in both days late and dollars, with a one-click
start and an open-record arrow.

```
urgency = w_late × lateness + w_value × value + w_unblock × downstream
```

Defaults `w_late=0.5`, `w_value=0.2`, `w_unblock=0.3`, all in
`ir.config_parameter` under `command_center.do_next.*`, matching the module's
existing convention that every threshold is configurable.

- `lateness` — days past deadline, or projected slack for not-yet-late orders
- `value` — order value normalised across the current scope
- `downstream` — count of operations the item unblocks, from the `waiting_on`
  dependency edges Process Explorer already computes

**Missing-term rule (D8):** a term with no data is excluded and the score
renormalises over the remaining weights; the card names the excluded term. A term
is never silently zero.

Process Explorer's ordinal ranking (late › at-risk › blocked › material-short ›
deadline) is the tie-break, so the two screens cannot disagree on position 1.

---

## 6. Zone 3 — lenses

| Lens | State | Contents |
|---|---|---|
| **Orders** | live | Per-order state, ops done/total, blocked count, deadline, days late, projected finish, value, dependency chain; expandable to operations |
| **Stations** | live | Per-work-center state, queue depth split open/ready/blocked, load in hours and days, capacity vs demand, utilisation, bottleneck flag, next operation with takt, changeover time, alternate center that could absorb overflow. All 14 work centers shown; the 6 with no open work labelled idle, not omitted |
| **People** | honest-empty | Names `hr.attendance` (0 rows) and `hr.employee.skill` (0 rows) as unpopulated. Contract defined for operators on shift vs required by skill, per-station coverage, overtime risk |
| **Materials** | live | Shortages by component with the orders each blocks, on-hand, incoming, PO ETA. The Accuride 3832 slide shortage is a first-class row |
| **Assets** | partial | Tooling and maintenance: lifecycle state and swap scheduling live (SB-PSB-004 lands here); wear and remaining life marked "no data" |
| **Schedule** | live | Projected makespan (386 min live), critical path, load heatmap by day, what-if simulation with its advisory disclaimer intact |
| **Andon** | live | Station card wall for the floor, unchanged |
| **Decide** | live | Sortable triage table, unchanged |
| **Issues** | live | Replaces Exceptions; clustered per §8 |

---

## 7. Zone 4 — detail drawer

Opens in the right column on row click from any lens. One component, one
contract: `{model, res_id, title, facts[], actions[]}`. Reads from the shared
payload rather than issuing its own queries, so it cannot disagree with the row
that opened it. Escape closes; the open record survives a lens switch.

---

## 8. Issue clustering

After D1 the open population collapses to one row per live condition — on
2026-07-30 data that is 17 MI checks plus 3 breakdown/NCR rows, so roughly 20
rows rather than 38,910. Clustering remains
the right structure — it is what keeps Issues readable as the count grows
honestly — but the cluster card is the primary object and individual instances
sit behind a show link.

**Cluster key:** `exception_type + inferred root cause + impact class`.

**Card:** title, instance count, affected orders, age and first-seen, diagnosed
root cause, one bulk action (e.g. "link cutlists for 12 orders").

**Severity re-scoring.** Today 100% of `cutlist_divergence` is `critical`, so
severity carries no information. Re-score from blocker severity × orders affected
× lateness of those orders, banded to produce a real distribution.

**Configuration-defect rule.** When one class exceeds a configurable share of
open volume (default 60%), the screen labels it a configuration defect with its
own remediation rather than rendering N emergencies.

---

## 9. Health score

- Never score absent data. A component with no observations is excluded from the
  denominator and marked **"no data"** — neither zero nor perfect.
- Display as *score out of available points*, with coverage stated: today,
  **10 of 25 available (75 points no data)**.
- Per-component trend sparkline.
- Every component clicks through to the records that produced it.

---

## 10. Phase 0 — quick wins, before any rebuild

Ordered by risk retired per hour.

1. **Fix the dedupe key** (D1) — stable business identity
2. **Archive the 38,893 duplicates** (D2) — migration script, counts verified
   before and after
3. **Exclude no-data components from the health denominator** — with "no data"
   chips, sparklines, click-through
4. **Reconcile OTD and work-center count** against the Process Explorer payload
5. Grouping on by default
6. Bulk actions on the issue list
7. Raw `southbrook.mi.check` strings behind a "technical detail" disclosure
8. Smoke-test recommendation out of the production feed
9. Right column put to use

Items 1–2 touch production data; the rest are display-layer.

---

## 11. Visual grammar

Process Explorer's grammar adopted wholesale, consuming its tokens rather than
re-deriving copies: flat cards, small colour-coded chips, thin left-border
severity bars, dense numeric alignment, hatched inline explanation banners, one
accent per state (orange ready, blue running, red blocked). The `--mpx-fs-*`
scale with its 12px floor and the WCAG ≥4.5:1 chip palette carry across.

All SCSS scoped under the client-action root — never bare `html, body`, which
leaks into every backend screen.

`ux_static_check.mjs --strict` runs against the new screen as a regression gate.

---

## 12. Testing

| Layer | Test |
|---|---|
| Keying | Two sweeps over the same MI condition produce **one** exception row |
| Migration | Archive count equals measured duplicate count; live rows survive; reversible |
| Reconciliation | Chip values equal `get_data()` values for all 8 chips |
| Degradation | With `mrp.process.explorer` absent, the screen renders and the provenance line declares the fallback |
| Health | A component with no observations is excluded from the denominator, and reported as neither 0 nor 100 |
| Do Next | A missing term renormalises rather than scoring zero; ordering agrees with Process Explorer at position 1 |
| Clustering | 20k synthetic instances collapse to the expected cluster count |
| Static | `ux_static_check.mjs --strict` passes |

---

## 13. Deferred

- **Recommendations panel content** — rotating blades, `lifecycle_state=available`.
  Treated as page data; not executed, and not to be executed without separate
  instruction.
- **Capture project** — attendance, skills-per-station, tool-usage logging. Its
  blocker is floor process (operators clocking in, usage logged at the station),
  not code. Lens contracts are written to accept it without rebuild.
- **`mrp_process_explorer` repo home** — commits are local-only with no settled
  remote. A live risk independent of this work.
- **Shared provider model** (option 2 of the reconciliation approaches) — the
  right end state once the repo question is settled; D5 buys the guarantee now
  without the cross-repo dependency.

---

## 14. Deploy notes

Carried from the Process Explorer remediation, which shares this stack:

- `flock` is **not installed** on the QNAP host — run the cold `-u` directly via
  `system-docker exec southbrook-odoo odoo -c /etc/odoo/odoo.conf -d southbrook
  -u southbrook_command_center --stop-after-init --no-http`, and always verify
  `latest_version` bumped before declaring done.
- JS/SCSS changes require a container restart, not SIGHUP, plus purging
  `%assets_backend%` / `%web.assets_web%`.
- `southbrook-odoo` has no host-published port — probe login with
  `exec curl localhost:8069` inside the container.
