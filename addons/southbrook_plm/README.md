# Southbrook PLM

A thin, configurator-fit **Product Lifecycle Management** layer for the
Southbrook Estimating build on **Odoo 19.0 Community Edition**.

> Design rationale and the full options analysis live in
> [`docs/superpowers/specs/2026-05-31-southbrook-plm-gap-design.md`](../../docs/superpowers/specs/2026-05-31-southbrook-plm-gap-design.md)
> (the Gap-Fit study). This README is the operator's quick reference.

## Why this exists (not a packaged app)

| Option | Verdict |
|--------|---------|
| Odoo native `mrp_plm` | ❌ Enterprise-only — cannot install on CE |
| OmniaSolutions OdooPLM | ❌ free + v19, but a CAD/PDM suite that fights `product_configurator_mrp` |
| Just Try `plm_product_bom` | ❌ right shape, but no v19 build and OPL-1 proprietary |
| CLuedoo `fal_mrp_plm_documents` | ❌ requires Enterprise |
| **This module** | ✅ synthesises only the needed capabilities, LGPL-3, no CAD/PDM weight |

Southbrook's BoMs are **generated dynamically** from configurator sessions, so a
CAD/engineering-BoM PLM is the wrong shape. This module governs only what humans
actually edit.

## What it manages

- **Engineering Change Orders** (`southbrook.eco`) with a user-configurable
  Kanban pipeline (`southbrook.eco.stage`), categories (`southbrook.eco.type`),
  per-stage approval gating, and a full chatter audit trail.
- **Template-BoM versioning** — applying a `bom`-kind ECO copies the target
  `mrp.bom` to a new `southbrook_version` and archives the prior one.
- **The parametric cut spec** (`southbrook.cut.spec`) — the NF14 geometric
  constants, promoted out of code into a versioned, ECO-governed record.
- **Engineering documents** — attached to the ECO and carried on its trail.
- **Construction-rule changes** — kept in `config_rules.xml` + git; the ECO
  records the `git_ref` (commit SHA / PR).

Out of scope by design: CAD/PDM vault, SolidWorks/Inventor bridges, 3D viewers,
part-number generation, routing versioning, per-variant runtime-BoM versioning,
and commercial/pricing change control.

## The cut-spec seam

`southbrook_estimating/models/mrp_bom.py` reads its NF14 constants through a
seam method, `_get_cut_constants()`, which standalone returns the code defaults
(behaviour unchanged). This module **overrides** that seam to return the
**active** `southbrook.cut.spec`. When no spec is active, it falls back to the
code defaults — so installing this module never changes cut output until a spec
is activated. The seeded `NF14 Baseline` spec mirrors the code defaults exactly,
so day-one output is byte-identical.

To change cabinet geometry without a deploy:
1. Create a draft `southbrook.cut.spec` with the new values.
2. Raise a *Cut-Geometry Revision* ECO referencing it.
3. Approve → Apply. The spec activates, the prior one is superseded, and all
   cabinet cut math picks up the new numbers immediately.

## Dependencies

`mrp`, `southbrook_estimating` (which brings the OCA `product_configurator`
suite). No Enterprise modules. License **LGPL-3**.

## Security

- **PLM User** — raise/edit ECOs.
- **PLM Approver** — approve, apply, reject; manage stages, types, cut specs.

## Status

**Release Candidate** (2026-05-31). Phase-4 deliverable; engine + integration
surfaces complete:

- Model + workflow (ECO + stage + type + cut spec + `mrp.bom` extension)
- Cut-spec seam override on `mrp.bom._get_cut_constants()` (panel math
  reflects the active spec; falls back to estimating code defaults when
  no spec is active)
- BoM versioning on apply (copy + archive + `southbrook_version` bump)
- Approval gating (per-stage `approval_required` + `group_southbrook_plm_approver`)
- Smart-button surface on `mrp.bom` form (ECO count → filtered list)
- Symmetric smart-button surface on `southbrook.cut.spec` form
- Demo seed — 4 example ECOs (one per `target_kind`) + 1 candidate cut spec
- 16 test methods, 18-test suite green on Odoo 19 CE live install

Pending (Phase-3 reservation): per-variant runtime-BoM versioning when the
configurator-session-driven BoM flow lands. Today's BoM versioning targets
template BoMs only.

This is custom routine **#8** in the SAMI register; see
`PUNCHLIST.md` for the boundary-rule justification and the reopening of the
SAMI §0 "PLM Absent" decision.
