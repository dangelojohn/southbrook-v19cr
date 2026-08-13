# SIGHTLINE — ground-truth check against the live database

**Date:** 2026-08-13 · **Instance:** `southbrook` on `southbrook-odoo` (QNAP 192.168.68.108)
**Method:** direct psql against the production database. Every number below is a query
result, not an estimate. Queries kept in `scratchpad/q.sql`, `q2.sql`.

Read alongside `docs/SOUTHBROOK_CMMS_3PL_UXUI_SPEC.md` (commit `34653e4a`).

---

## 1. Appendix B — both open questions are now closed

| Question | Answer | Evidence |
|---|---|---|
| Enterprise or Community? | **Community** | `web_cohort`, `web_gantt`, `web_map`, `web_enterprise` are not installed and not present in `ir_module_module`. `web_studio` is `uninstallable`. |
| `vendor_id` or `partner_id`? | **`vendor_id`** (m2o → `res.partner`) | `ir_model_fields` on `southbrook.cmms.service_contract` |

**Consequence.** SL-05 (Failure Cohort) and SL-12 (Coverage Timeline / gantt) are dead as
written, and SL-11's `map` view goes with them. Their inline fallbacks become the plan:
the pivot + bucket field for SL-05, and SL-X9 Coverage Heat Grid promoted out of Phase 2
for SL-12. W4 changes shape.

**Also:** the version banner says Odoo 17/18. Production runs **19.0-20260504**.

**One field missing from §1.2's inventory:** `service_contract.state_override`
(selection) is not listed in the spec's ground truth. It matters — SL-13 groups by
`state`, and a manual override changes what that grouping means.

---

## 2. The finding that outranks everything else: there is almost no data

| Model | Rows |
|---|---|
| `southbrook.cmms.breakdown_alert` | **1** — dispatched, high, 2026-06-26, author `__system__` |
| `southbrook.cmms.service_contract` | **1** — the $0 one (D11) |
| `southbrook.cmms.landed_cost_template` | **0** |
| `southbrook.integrations.asn_3pl` | **1** |
| `maintenance.request` | **1** — corrective; **zero preventive, ever** |
| `maintenance.equipment` | 5 (all have a work centre) |
| `southbrook.cmms.mtbf_mttr_report` | 241 — of which **194 (80.5%)** are flat at 100% / 0 breakdowns |
| `southbrook.cmms.mi_tiles` | **1** — and by design it can never be more (see §2.5) |

Snapshot range 2026-06-26 → 2026-08-12: 48 days × 5 machines. The cron writes five rows
a day whether or not anything happened.

**The 47-58% availability on CNC-001 — the number the entire spec is oriented around —
derives from that one system-generated breakdown.** There is no second event to compare
it to.

### Two fields the spec leans on are empty on every machine

| Field | Populated |
|---|---|
| `expected_mtbf` | **0 / 5** |
| `warranty_date` | **0 / 5** |

The spec calls `expected_mtbf` "the single biggest missed opportunity… half of Phase 1 is
simply connecting what already exists." The field exists; **the value does not.** Connecting
it surfaces zeros in SL-01's *vs Expected* column, SL-02's "48% of target" card line,
SL-F01's *Below Expected MTBF* filter, and SL-X4's entire Failure Runway.

`warranty_date` being NULL is worse than empty — it is silently falsy. SL-15's Uncovered
Equipment Radar filters `warranty_date < today`, which **excludes NULLs**, so the radar
returns an empty list on all five machines. The plate ships, looks fine, finds nothing.

---

## 2.5. What the code actually does — including two corrections to §2

Added after reading `addons/southbrook_cmms_wms/`. Two claims above were inferred from
row counts and are wrong; both are corrected here rather than quietly edited out.

### CORRECTION 1 — there is no `mi_tiles` cron, and history is not a cron fix

I wrote that the single `mi_tiles` row "confirms the cron overwrites." There is no
`mi_tiles` cron. The module defines two crons only (daily MTBF/MTTR sweep, service
contract expiry sweep). `mi_tiles` is **one seeded record** (`data/mi_tiles.xml`,
`noupdate="1"`) whose six tile fields are **`store=False` computes** evaluated on read.

That is worse than an overwriting cron, not better: **no KPI value is ever persisted, so
no history can exist at all.** SL-20 is therefore not "change the cron to append" — it is
(a) make the six fields stored, (b) write a cron that creates one row per day, (c) keep
the live-compute path for the current row. Every delta, sparkline and trend in SL-19,
SL-20, SL-X6 and SL-X14 sits on top of that change.

### CORRECTION 2 — the reliability cron already upserts

I recommended "stop the reliability cron writing unchanged snapshots." It does not write
duplicates — `_cron_generate_daily_reports` already upserts one row per
(equipment, day), with a comment recording that an earlier version's unbounded growth
was fixed. The 241 rows are 48 days × 5 machines, one per machine per day **by design**.
So D9's padding is inherent to a daily-snapshot model, not a cron bug. The choice is to
keep the design and default the view to the exception (which is what SL-01 does), or to
stop writing a row when nothing changed. The first is cheaper and is already specified.

### NEW — the reliability numbers do not come from breakdown alerts

`_compute_metrics` counts **`maintenance.request`** records where
`maintenance_type = 'corrective'`. It never reads `southbrook.cmms.breakdown_alert`.
The bridge is `action_dispatch()`, which creates the request — so **an alert that is
logged and closed without anyone pressing Dispatch never reaches MTBF, MTTR or
availability.**

This explains the dataset exactly: 1 maintenance request → four machines with zero
corrective requests → `breakdown_count = 0`, `mtbf = runtime / max(1, 0) = 2160 h`,
availability 100%. The famous "100.00 / 2,160.00 / 0" rows are not stale data. They are
the formula's output for a machine with no requests, and they will stay that way however
many breakdowns the floor logs, unless Dispatch is pressed every time.

### NEW — MTTR cannot resolve anything shorter than a day

Downtime is `(close_date - request_date).days * 24.0`. Both are **Date** fields in v19
(the code says so). Consequences:

- **A repair opened and closed on the same day contributes zero downtime**, so it scores
  100% availability and MTTR 0 — a fast repair is indistinguishable from no failure.
- **MTTR can only ever be a multiple of 24.** CNC-001's MTTR of ~1128 h is one request
  left open ~47 days, not a real repair duration.

The spec builds SL-08's SLA bands at **1 h / 4 h / 24 h**, SL-07's "Open 3h 12m" elapsed
timer, and MTTR trend columns on top of this. The alert model already has a
`reported_at` **Datetime**; there is no corresponding `fixed_at`. Sub-day MTTR needs its
own timestamps on the alert, not the maintenance request's dates — otherwise the SLA
layer is measuring a metric that has no sub-day resolution.

**This outranks everything in §3.** A UX program can survive thin data; it cannot survive
a metric that is structurally unable to express the thing the screens promise to show.

---

## 3. What the spec does not cover at all

Ranked by how much each one blocks the rest.

1. **No failure-cause taxonomy.** Confirmed by query: the only cause-adjacent field
   anywhere in `southbrook.cmms.*` or `maintenance.*` is `category_id` — equipment
   category, not failure cause. SL-X3 mentions coding causes in a parenthetical
   ("once failure-cause coding is added"). That parenthetical is the most consequential
   sentence in the document. Without it a CMMS cannot answer *why* machines fail —
   Pareto by cause, the bathtub regimes, and anomaly clustering are all unbuildable.
   This belongs in Phase 0 as a model, not in Phase 2 as an aside.

2. **No preventive-maintenance loop.** Zero preventive requests exist. The spec is
   entirely reactive — breakdown → alert → dispatch → fixed. **PM compliance
   (scheduled vs completed on time) is the headline metric of every real CMMS and it
   appears nowhere:** not a plate, not a Command Deck tile, not an acceptance criterion.
   MTBF tells you the machine broke. PM compliance tells you whether you did the work
   that would have prevented it.

3. **No spare-parts / MRO link.** MTTR is mostly determined by whether the part is on
   the shelf. Nothing connects a breakdown to parts consumed, stock on hand for critical
   spares, or lead time. SL-15 does this exposure analysis for *contracts*; the same
   logic applied to *parts* — single point of failure, no spare, long lead time — is
   absent, and it is the version that stops a line.

4. **No work-order capture.** `maintenance_request_id` is referenced but no plate covers
   the work itself: labour time, technician, parts, sign-off. Every MTTR figure in the
   program is therefore unverifiable — and with one maintenance request in the database,
   currently unverified.

5. **Nothing enforces the acceptance criteria.** All 21 checkboxes are manual ("verify by
   impersonating one user per group"). Demonstrated concretely: SL-F04 requires ≥4.5:1
   contrast and its own palette misses it (`--sb-health-watch` 3.2:1,
   `--sb-health-degraded` 4.4:1 on white). A written criterion with no check is a wish.

---

## 4. The sequencing problem

The wave plan builds screens first (W1–W5) and the floor tools sixth (W6: Tech Mode,
Asset Passport, Ticker). But the screens have nothing to display, and W6 is what
generates the data they would display.

The spec argues this against itself, in SL-X7: *"Data quality in a CMMS is entirely
determined by whether the person holding the wrench will use it."* Correct — and then
scheduled sixth.

Two dependencies the wave table does not show:

- **W4 depends on a data-model change flagged only in Appendix B** — and a larger one
  than Appendix B suggests (§2.5, Correction 1). `mi_tiles` never stores a value, so
  history requires stored fields plus a new cron, not a cron tweak. And the change only
  starts the clock: a 30-day sparkline is empty for 30 days *after* it ships.
- **W2's payoff is gated on a data fill nobody owns.** Populating `expected_mtbf` and
  `warranty_date` on five machines is perhaps an hour of work and it unblocks four plates.
  It is not in the spec.

### Suggested W0, ahead of everything

Small, and it is what makes W1–W5 worth looking at:

Revised after reading the code (§2.5). Ordered by what blocks the most.

1. **Give MTTR sub-day resolution.** Add `fixed_at` (Datetime) to `breakdown_alert`, set
   it on the transition to `fixed`, and compute downtime from `reported_at → fixed_at`
   rather than from the maintenance request's Date fields. Without this, SL-07's elapsed
   timer and SL-08's 1 h / 4 h / 24 h SLA bands measure a metric that cannot express
   hours. **Blocks W3.**
2. **Close the alert → request gap.** Either create the maintenance request on alert
   creation rather than on Dispatch, or read alerts directly in `_compute_metrics`.
   Today an alert that is never dispatched is invisible to every reliability number.
3. **Persist KPI history.** Make the six `mi_tiles` fields stored and add a daily cron
   that appends a row. Do it first, because everything trend-shaped in W4 needs 30 days
   of accumulation before it shows anything. **Blocks W4, with a 30-day lead time.**
4. **Failure-cause taxonomy** — a model plus a required m2o on the alert, seeded with an
   editable starter list. Everything analytical in Phase 2 is built on it.
5. **Populate `expected_mtbf` and `warranty_date`** on all 5 machines. Perhaps an hour,
   and it unblocks four plates. **Needs the real values — do not seed placeholders**,
   since every "vs Expected" figure in the program inherits them.
6. **Pull SL-X7 (Tech Mode) and SL-X8 (Asset Passport) forward.** Nothing else in the
   program generates data.

None of this contradicts the spec's design work, which is strong and stays as written.
It changes the order, and it adds the data layer the UX layer is standing on.

---

## Confidence

| Claim | Basis |
|---|---|
| All row counts, field populations, edition, `vendor_id` | Direct psql on production, 2026-08-13. High. |
| Odoo version 19.0-20260504 | `version_info` RPC + `odoo --version` in the container. High. |
| SL-15's NULL-warranty domain returns nothing | Reasoned from SQL/Odoo domain semantics — NULL fails `<`. Not executed against the built view (the view does not exist yet). Medium-high. |
| Contrast ratios | WCAG 2.1 formula computed, both themes. High. |
| `mi_tiles` never persists; no cron exists | Read from `models/mi_engine_ext.py` (`store=False`) and `data/`. High. Supersedes the "cron overwrites" claim in the first version of this file. |
| Reliability metrics read `maintenance.request`, not alerts | Read from `_compute_metrics` in `models/mtbf_mttr_report.py`. High. |
| MTTR resolution is one day | `(close_date - request_date).days * 24.0` on Date fields. High. Not yet demonstrated against a same-day request, because only one request exists. |
| "PM compliance is the headline CMMS metric" | Domain judgement, not measured. Medium. |
