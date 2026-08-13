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
| `southbrook.cmms.mi_tiles` | **1** — confirms the cron overwrites |

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

- **W4 depends on a data-model change flagged only in Appendix B.** `mi_tiles` holds one
  row because the cron overwrites. Every delta, sparkline, and trend in SL-19, SL-20,
  SL-X6 and SL-X14 needs history that does not exist. Fixing the cron starts the clock —
  a 30-day sparkline is empty for 30 days after the fix, not on the day it ships.
- **W2's payoff is gated on a data fill nobody owns.** Populating `expected_mtbf` and
  `warranty_date` on five machines is perhaps an hour of work and it unblocks four plates.
  It is not in the spec.

### Suggested W0, ahead of everything

Small, and it is what makes W1–W5 worth looking at:

1. Populate `expected_mtbf` and `warranty_date` on all 5 machines (~1 hour).
2. Add the failure-cause taxonomy as a model + required field on breakdown alerts.
3. Change the `mi_tiles` cron to append — starts KPI history accumulating now, so it has
   30 days by the time W4 lands.
4. Stop the reliability cron writing unchanged snapshots, or make the model a view over
   breakdowns. This is the *cause* of D9; SL-01's regrouping treats the symptom.
5. Pull SL-X7 (Tech Mode) and SL-X8 (Asset Passport) forward. Nothing else generates data.

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
| "PM compliance is the headline CMMS metric" | Domain judgement, not measured. Medium. |
