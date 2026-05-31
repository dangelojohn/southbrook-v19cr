# Southbrook PLM — Gap-Fit Analysis & Module Scoping Spec

> **Type:** GAP-FIT analysis (decision artifact) that doubles as the scoping
> spec for a future `southbrook_plm` addon.
> **Project:** Southbrook Estimating v19CR — Odoo 19.0 Community Edition.
> **Date:** 2026-05-31.
> **Status:** Draft for John's review.
> **Reopens:** `docs/SAMI_Southbrook_Odoo19_Build_Spec.md` §0 — the locked
> "PLM | Absent" decision (see §1 and §9 below).

---

## 1 · Executive summary & the reopened decision

The SAMI build spec §0 deliberately marked **PLM as "Absent"** for v1, with the
substitution *"engineering changes captured in `mrp.bom` versioning + git history
of the addon."* That was the correct call for Phase 1. This document reopens it
for **Phase 4**, on the premise that Southbrook now wants a structured,
auditable, non-developer-operable change-control surface for its cabinet
engineering.

**Recommendation (full rationale in §6–§7):** **Build a thin, purpose-fit
`southbrook_plm` addon (option a)** rather than adopt a packaged PLM suite.
Adopt a **hybrid architecture**: an Odoo-Enterprise-PLM-style ECO workflow
governing the database-resident objects (canonical template BoMs + engineering
documents), the NF14 cut constants **promoted into an ECO-governed
`southbrook.cut.spec` record**, and the declarative construction rules left in
code with the ECO **referencing the git SHA**. This synthesises the best of every
surveyed offering while inheriting none of their CAD/PDM weight or their
Enterprise/proprietary licensing.

This becomes **custom routine #8** in the SAMI §4 register and therefore requires
the §4 boundary-rule PUNCHLIST justification (seeded in §9).

---

## 2 · Southbrook's actual PLM need

The decisive architectural fact: **Southbrook does not author static engineering
BoMs.** A cabinet's BoM is generated dynamically by `product_configurator_mrp`
from a configurator session, with panel geometry computed at runtime by
`southbrook_estimating/models/mrp_bom.py::_compute_panel_dimensions(...)` against
the NF14 constants. There are no CAD part files, no part-number vault, no
engineer-drawn assemblies. Any PLM solution that assumes those is a conceptual
mismatch (see §6 option b).

### In scope (the objects humans deliberately edit)

| # | Object | Where it lives today | System-of-record today |
|---|--------|----------------------|------------------------|
| 1 | The 12 canonical cabinet **template BoMs** | `data/product_templates.xml` → DB `mrp.bom` | git + DB |
| 2 | The **NF14 parametric cut constants** (`BOX_TH`, `BACK_TH`, `RABBET`, `DOOR_TH`, reveal…) | `models/mrp_bom.py` module constants | git only |
| 3 | The **4 declarative construction rules** + 65 `product.config.line` records | `data/config_rules.xml` | git only |
| 6 | **Engineering documents** — vendor cut sheets, hardware spec PDFs, shop drawings | not yet systematised | ad hoc |

### Explicitly out of scope (with reason)

- **#4 Attribute values / pricing / lead-time / the 6 pricelists** — this is
  *commercial* change control, not engineering PLM. Different approvers,
  different cadence. Keep it out of the ECO workflow.
- **#5 Per-variant runtime BoMs** — what `product_configurator_mrp` emits per
  confirmed order. Odoo's standard MO already snapshots the consumed BoM; a PLM
  versioning layer over auto-generated per-variant BoMs would be noise. The
  `southbrook.order.analytics` model already captures the BoM-rollup counts per
  order for the AI spine.

---

## 3 · The offerings surveyed

| Offering | Vendor | Edition | v19 status | License | Cost | One-line |
|----------|--------|---------|-----------|---------|------|----------|
| **Native PLM** (`mrp_plm`) | Odoo SA | **Enterprise only** | n/a on CE | Enterprise | subscription | ECO types/stages, BoM version control, document attach |
| **OdooPLM suite** (~30 modules: `plm_engineering`, `plm_compare_bom`, `plm_web_3d_support`, CAD bridges…) | OmniaSolutions | Community | ✅ 19.0 | Open-source (LGPL-3 per listings — *verify per module*) | Free | CAD/PDM: read BoM from SolidWorks/Inventor, part revisions, document vault |
| **Product Lifecycle Management For Community** (`plm_product_bom`) | Just Try | Community | ❌ 17.0/18.0 only | OPL-1 (proprietary) | $9.69 | Lightweight Enterprise-style ECO: types, stages, approvals, BoM-version compare |
| **MRP PLM Documents** (`fal_mrp_plm_documents`) | CLuedoo | **Enterprise** (depends on Enterprise PLM + Documents) | ✅ 19.0 | OPL-1 (proprietary) | $149.94 | BoM-document sync into the Documents app |

**Hard eliminations up front:**

- **Native `mrp_plm`** — cannot install on Community. This is *why* §0 marked PLM
  Absent. Out.
- **`fal_mrp_plm_documents`** — requires Enterprise (`depends` on the Enterprise
  PLM + Documents apps). Out.

That leaves two real Community candidates: **OmniaSolutions OdooPLM** (free,
v19, available) and **Just Try `plm_product_bom`** (right shape, but no v19 build
and proprietary). Both are weighed in §6.

---

## 4 · The capability taxonomy

The union of everything the surveyed offerings provide, grouped by capability
family. The GAP-FIT matrix in §5 scores each row against Southbrook's need.

- **(i) ECO workflow** — ECO records; ECO *types* (categorise a change); ECO
  *stages* (Kanban progress); approval gates (mandatory/optional approvers per
  stage); change-tracking/audit trail.
- **(ii) BoM version control** — new versioned BoM on change; archive previous
  version; visual **compare/diff** of two BoMs (added/removed/qty-changed lines);
  "produce only latest version" guard.
- **(iii) Document management** — attach documents to a BoM/product/ECO;
  versioned document vault; sync to a Documents workspace.
- **(iv) Engineering parameters** — versioned, named numeric/engineering
  parameters attached to a product/BoM (OmniaSolutions' parameter model;
  conceptually maps to Southbrook's NF14 cut constants).
- **(v) CAD / PDM** — file vault; SolidWorks/Inventor/FreeCAD bridges; read BoM
  from CAD graphic hierarchy; 3D/2D viewer; auto part-number generation.
- **(vi) Routing / operation versioning** — ECO over MRP routings/operations.

---

## 5 · The GAP-FIT matrix (heart of the document)

Need = Required / Nice / Irrelevant for Southbrook. Best source = the offering
that does it best. Verdict = how well any *installable-on-CE-v19* path covers it.

| # | Capability | Need | Best source offering | Verdict (CE/v19 reality) |
|---|------------|------|----------------------|--------------------------|
| i.1 | ECO records + types | **Required** | Native PLM / Just Try | **Gap** — native is Enterprise; Just Try has no v19. Must build or wait. |
| i.2 | ECO stages (Kanban) | **Required** | Native PLM / Just Try | **Gap** — same. |
| i.3 | Approval gates per stage | **Required** | Native PLM / Just Try | **Gap** — same. |
| i.4 | Change audit trail | **Required** | All (via `mail.thread`) | **Fit** — standard Odoo chatter; trivial to wire. |
| ii.1 | Versioned BoM on change | **Required** | Native PLM / OmniaSolutions / Just Try | **Partial** — OmniaSolutions does it but in a CAD/engineering-BoM frame that fights the configurator. Cleaner to build for the 12 template BoMs. |
| ii.2 | BoM compare/diff | Nice | OmniaSolutions (`plm_compare_bom`) / Just Try | **Partial** — OmniaSolutions' `plm_compare_bom` is free + v19 and *potentially reusable standalone*; evaluate as a leaf dep (§6b). |
| ii.3 | "Produce only latest" guard | Irrelevant | OmniaSolutions | n/a — Southbrook BoMs are per-variant generated; no stale-version production risk. |
| iii.1 | Attach docs to BoM/product/ECO | **Required** | All | **Fit** — `ir.attachment` (already the SAMI §0 Documents substitute). No new dependency. |
| iii.2 | Versioned document vault | Nice | Native PLM / CLuedoo | **Gap** — Enterprise-only sources; approximate with attachment + ECO link + chatter. |
| iv.1 | Versioned engineering parameters (≈ NF14 cut constants) | **Required** | OmniaSolutions | **Gap** — OmniaSolutions' parameter model is entangled with its CAD frame; cleaner to build a focused `southbrook.cut.spec` (§7). |
| v.* | CAD / PDM (file vault, SolidWorks bridge, 3D viewer, part numbers) | **Irrelevant** | OmniaSolutions | **Anti-fit** — Southbrook has no CAD files; this is dead weight and a BoM-ownership conflict (§6b). |
| vi.1 | Routing/operation versioning | Irrelevant | Native PLM / OmniaSolutions | n/a — Southbrook has no engineered routings in scope. |

**Reading of the matrix:** Southbrook needs the **lightweight ECO core (i.*),
attachment-based documents (iii.1), versioned template BoMs (ii.1), and versioned
cut parameters (iv.1)** — and explicitly does **not** want CAD/PDM (v.*) or
routing versioning (vi.1). The capabilities it needs are exactly the ones the
*Enterprise/Just-Try lightweight* offerings provide and that are **not
installable** on CE/v19 today, while the one big *installable* offering
(OmniaSolutions) is concentrated in the capabilities Southbrook does **not** want.
This inversion is the core finding.

---

## 6 · The three options, costed

### (c) Decision matrix — neutral side-by-side

| Criterion | Native `mrp_plm` | OmniaSolutions OdooPLM | Just Try `plm_product_bom` | Build `southbrook_plm` |
|-----------|------------------|------------------------|----------------------------|------------------------|
| Installable on CE v19 | ❌ Enterprise | ✅ | ❌ no v19 | ✅ |
| License fits LGPL-3 / OCA-clean discipline | ❌ | ✅ (open-source) | ❌ OPL-1 proprietary | ✅ LGPL-3 |
| Conceptual fit to configurator BoMs | n/a | ❌ CAD/PDM mismatch | ✅ lightweight ECO | ✅ purpose-built |
| Covers needed caps (i, iii.1, ii.1, iv.1) | ✅ | ⚠ partial + dead weight | ✅ (minus iv.1) | ✅ |
| Avoids unwanted caps (v, vi) | n/a | ❌ brings them | ✅ | ✅ |
| Self-patchable / no external release cadence | ❌ | ⚠ (open but large) | ❌ (closed source) | ✅ |
| Effort to adopt | n/a | medium glue + ongoing | low (if it had v19) | medium build |

### (b) Adopt + extend — OmniaSolutions OdooPLM as base

The only mature, free, v19-ready Community PLM. Adoption would mean installing the
core OdooPLM modules and writing glue to point its engineering-BoM/parameter model
at Southbrook's templates.

**Why it scores poorly:**

1. **BoM-ownership conflict.** OdooPLM assumes engineer-authored "engineering"
   BoMs sourced from CAD; `product_configurator_mrp` already *owns* BoM
   generation. Two systems contending for the BoM is an integration liability.
2. **CAD/PDM dead weight (capability family v).** SolidWorks/Inventor bridges,
   3D vault, part-number generation — none applicable to a procedural-geometry
   cabinet shop. Installed surface area you must understand and maintain for zero
   benefit.
3. **Parameter model entanglement.** Its closest fit (iv.1, engineering
   parameters) is coupled to the CAD frame, not cleanly extractable for the NF14
   cut constants.

**Salvage value:** `plm_compare_bom` *may* be reusable **standalone** as a
BoM-diff utility (capability ii.2, "Nice"). The build option (a) should evaluate
it as an optional leaf dependency before reimplementing diff — but must verify it
installs without dragging the engineering-BoM core, and that its license is
compatible. If it pulls the suite, reimplement diff instead.

**Verdict:** Not recommended as the base. Cherry-pick `plm_compare_bom` only if
it is genuinely standalone.

### (a) Build `southbrook_plm` — the synthesised module (recommended)

A thin LGPL-3 addon that implements only the needed capabilities, drawn from the
matrix, in the hybrid architecture. Scope sketch in §7. This is the synthesis the
brief asked for: it **combines** Enterprise-PLM's ECO+document model, the Just
Try / native lightweight stage→approval workflow, and OmniaSolutions' engineering-
parameter-versioning concept (narrowed to the cut spec) — while discarding
CAD/PDM, routing versioning, and all Enterprise/proprietary dependencies.

**Cost:** medium build, but bounded — the needed surface is small (the Just Try
analogue is ~648 LOC), it stays self-patchable, and it carries no external
release-cadence or licensing risk.

---

## 7 · Recommended `southbrook_plm` scope sketch

**License:** LGPL-3 (matches `southbrook_estimating`). **Phase:** 4. **Depends:**
`mrp`, `southbrook_estimating`. **No** dependency on any Enterprise module or CAD
bridge.

### Architecture: hybrid (Fork A dominant + #2 promoted)

| In-scope object | Governance mechanism |
|-----------------|----------------------|
| #1 template BoMs | ECO workflow over the DB `mrp.bom` records (Fork A) |
| #6 documents | `ir.attachment` linked to the ECO + product/BoM, with chatter (Fork A) |
| #2 NF14 cut constants | **Promoted** to a DB `southbrook.cut.spec` record, ECO-governed (Fork B, surgical) |
| #3 construction rules | Stay in `config_rules.xml` + git; ECO carries a **git-SHA reference field** (Fork A) |

### Models

- `southbrook.eco` — the change order. Fields: name, `eco_type_id`, `stage_id`,
  `state`, target reference (BoM / cut.spec / "rule change"), `git_ref` (SHA/PR
  for code-resident #3 changes), approver(s), `mail.thread` audit trail.
- `southbrook.eco.type` — categorises a change (e.g. *Cut-geometry revision*,
  *Template BoM change*, *Construction-rule change*, *Document update*).
- `southbrook.eco.stage` — Kanban stages (e.g. Draft → Under Review → Approved →
  Applied), with per-stage approval flags.
- `southbrook.cut.spec` — versioned record holding the NF14 constants
  (`box_th`, `back_th`, `rabbet`, `door_th`, reveal, …). One record is `active`;
  superseded versions retained. Carries the NF14 provenance note.

### Touch-points in the existing codebase

- `models/mrp_bom.py::_compute_panel_dimensions` — change from reading
  module-level constants to reading the **active `southbrook.cut.spec`**. Keep the
  module constants as the seed/default for the first `cut.spec` record so behaviour
  is identical on day one. This is the only behavioural edit to existing code.
- ECO "Apply" action — for #1, writes the new BoM version + archives the prior;
  for #2, activates the new `cut.spec`; for #3, records the merged git SHA (the
  actual code change still lands via the normal git/PR flow).

### Out of scope (explicit)

- No CAD bridge, no PDM file vault, no 3D viewer, no part-number generation
  (capability family v).
- No routing/operation versioning (vi).
- No per-variant runtime-BoM versioning (#5) — Odoo MO snapshot already covers it.
- No commercial/pricing change control (#4) — separate concern.

### Security

- `group_southbrook_plm_user` (raise/edit ECOs) and
  `group_southbrook_plm_approver` (advance past approval stages). Approver maps to
  the existing Sales Manager / estimator-lead persona.

---

## 8 · How this "combines the offerings"

| Borrowed from | What we take | What we drop |
|---------------|--------------|--------------|
| Odoo Enterprise `mrp_plm` | ECO-on-BoM model, document-attach, version control concept | Enterprise dependency |
| Just Try `plm_product_bom` | Lightweight ECO type→stage→approval + BoM-version compare shape | OPL-1 license, no-v19 blocker |
| OmniaSolutions OdooPLM | Engineering-parameter-versioning concept (→ `cut.spec`); *maybe* `plm_compare_bom` as a leaf util | CAD/PDM suite, engineering-BoM ownership |
| Project's own architecture | git-as-system-of-record for code-resident rules (#3) | — |

---

## 9 · Open questions / punchlist seeds

1. **Reopen SAMI §0 "PLM Absent."** This GAP recommends superseding that
   decision for Phase 4. Needs John's explicit sign-off before any build.
2. **Routine-#8 boundary exception.** SAMI §4 caps custom code at 7 routines;
   `southbrook_plm` is net-new custom logic. Per the §4 boundary rule this needs a
   PUNCHLIST justification (this document is the basis for it).
3. **`plm_compare_bom` standalone check.** Verify OmniaSolutions'
   `plm_compare_bom` installs without the engineering-BoM core and that its license
   is LGPL-3-compatible, before deciding build-vs-reuse for capability ii.2.
4. **`cut.spec` promotion blast radius.** Confirm no other code reads the
   `mrp_bom.py` module constants directly (grep) before promoting them, so the
   `cut.spec` becomes the single source.
5. **Just Try v19 watch (optional).** If `plm_product_bom` ships a v19 build before
   Phase 4, re-evaluate — but the OPL-1 license and the missing `cut.spec`
   capability still favour the build.
6. **Host note.** Request referenced `Southbrookkitchens.local`; the canonical
   deployment is `southbrookcabinetry.space`. Confirm whether `.local` is a
   separate on-prem/dev instance this PLM module must also target.

---

## 10 · Recommendation restated

Build a thin LGPL-3 **`southbrook_plm`** addon in Phase 4, using the hybrid
architecture (ECO over template BoMs + documents; NF14 constants promoted to an
ECO-governed `cut.spec`; construction rules left in git with ECO SHA references).
Do **not** adopt OmniaSolutions OdooPLM as a base (CAD/PDM mismatch); cherry-pick
its `plm_compare_bom` only if it proves standalone. Native `mrp_plm` and
`fal_mrp_plm_documents` are eliminated by the Community-edition constraint. Gate
the build on John reopening SAMI §0 and accepting the routine-#8 exception.
