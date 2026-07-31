{
    "name": "Southbrook — Work Center Type Compatibility",
    "version": "19.0.1.1.0",
    "category": "Manufacturing",
    "summary": "Stop mrp_product_costing's Work Center Type blocking every work-centre "
               "save and every header-button action.",
    "description": """
One attribute, because one attribute is the whole defect
========================================================

`mrp_product_costing` inserts `wc_type` into the work-centre form as
`required="True"` (its `views/mrp_workcenter_views.xml`, view
`mrp.workcenter.form.costing`). Nothing populates it: production has it NULL on 13 of 14
work centres. So every work-centre form is unsaveable, and because Odoo's web client
saves the record before executing a header button, "Open Tablet Queue" fails too — with a
generic "Missing required fields" toast that names no field. The shop floor cannot reach
its queue, and the cause is one attribute in a module that lives outside this repository.

WHY THIS IS A SEPARATE MODULE. `mrp_product_costing` is not tracked in southbrook-v19cr —
it sits in ~/Downloads and is installed on production. Editing it there would put the fix
somewhere git does not watch and a reinstall would silently revert it. Folding the
override into `southbrook_mrp_kitchen_workcenters` is worse: that module does not depend
on `mrp_product_costing`, so an xpath onto `wc_type` would fail to install anywhere the
costing module is absent. A bridge with an explicit dependency installs exactly where it
is relevant and nowhere else.

WHY NOT JUST BACKFILL `wc_type`. Backfilling patches the 14 rows that exist and leaves
the trap armed: the next work centre created without it reproduces the identical failure.
The constraint is also arbitrary relative to its purpose — `wc_type` is a two-value
Man/Machine flag whose only consumer in the entire codebase is one branch in
`mrp_product_costing/models/mrp_workorder.py` choosing between the labour and machine-run
GL accounts. Blocking all CRUD on a form for a field that narrow, on a system already
running with 13 of 14 violating it, is not a constraint anyone is relying on.

SEPARATELY, AND NOT FIXED HERE: because `wc_type` is NULL on 13 of 14 work centres, that
GL branch falls to its `else` on nearly every work order, posting labour cost to the
MACHINE RUN account. That is a live financial-accuracy defect, it is not in the Kitchen
Ops audit, and it is not fixed here — choosing which stations are Man and which are
Machine is a finance decision, not a code one. See README_WC_TYPE_BACKFILL.md.
    """,
    "author": "Southbrook",
    "license": "LGPL-3",
    "depends": ["mrp_product_costing", "mrp"],
    "data": ["views/mrp_workcenter_views.xml"],
    "installable": True,
    "auto_install": True,
}
