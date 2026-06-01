# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook PLM",
    "summary": "Lightweight, configurator-fit Product Lifecycle Management for "
               "Southbrook Cabinetry — ECO workflow over template BoMs, "
               "ECO-governed parametric cut spec, and engineering documents.",
    "description": """
Southbrook PLM
==============

A thin, purpose-built PLM layer for the Southbrook Estimating build on Odoo
19.0 Community Edition. It exists because:

* Odoo's native PLM (``mrp_plm``) is Enterprise-only and cannot install on CE.
* The mature free CE option (OmniaSolutions OdooPLM) is a CAD/PDM suite whose
  engineering-BoM model fights ``product_configurator_mrp`` and carries a large
  CAD surface Southbrook does not use.
* The well-fit lightweight ECO option (Just Try ``plm_product_bom``) has no v19
  build and is OPL-1 proprietary.

So this module synthesises only the capabilities Southbrook actually needs,
drawn from the Gap-Fit analysis at
``docs/superpowers/specs/2026-05-31-southbrook-plm-gap-design.md``:

* **ECO workflow** — ``southbrook.eco`` with user-configurable
  ``southbrook.eco.stage`` Kanban pipeline, ``southbrook.eco.type`` categories,
  per-stage approval gating, and full ``mail.thread`` audit trail.
* **Template-BoM version control** — on Apply, an ECO copies the target
  ``mrp.bom`` to a new ``southbrook_version`` and archives the prior one.
* **ECO-governed parametric cut spec** — the NF14 cut constants are promoted
  out of code into a versioned ``southbrook.cut.spec`` record. The
  ``southbrook_estimating`` panel-cut math reads the active spec through the
  ``_get_cut_constants()`` seam, so a shop lead can revise reveals / panel
  thicknesses through an approved ECO with no code deploy.
* **Engineering documents** — vendor cut sheets, hardware spec PDFs and shop
  drawings attach to the ECO (``ir.attachment``) and ride its approval trail.
* **Code-resident change references** — construction-rule changes stay in
  ``config_rules.xml`` + git; the ECO carries a ``git_ref`` field (SHA / PR) so
  the audit trail still captures them.

Deliberately OUT of scope: CAD/PDM file vault, SolidWorks/Inventor bridges, 3D
viewers, part-number generation, routing/operation versioning, per-variant
runtime-BoM versioning, and commercial/pricing change control.

This is custom routine #8 in the SAMI register; see PUNCHLIST for the
boundary-rule justification.
""",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "website": "https://southbrookcabinetry.space",
    "category": "Manufacturing/Product Lifecycle Management",
    "depends": [
        "mrp",
        "southbrook_estimating",
    ],
    "data": [
        "security/southbrook_plm_security.xml",
        "security/ir.model.access.csv",
        "data/eco_stage_data.xml",
        "data/eco_type_data.xml",
        "data/cut_spec_data.xml",
        "data/ir_sequence_data.xml",
        "views/southbrook_cut_spec_views.xml",
        "views/southbrook_eco_type_views.xml",
        "views/southbrook_eco_views.xml",
        "views/mrp_bom_views.xml",
        # Step 4 — sale.order.line → ECO bridge button on the order
        # builder's order_line list.
        "views/sale_order_views.xml",
        "views/southbrook_plm_menus.xml",
    ],
    "demo": [
        "demo/southbrook_plm_demo.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
