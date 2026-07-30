# `wc_type` is empty on 13 of 14 work centres, and that costs money

## What this module fixed

`mrp_product_costing` marks **Work Center Type** required in the work-centre form
(`views/mrp_workcenter_views.xml:17`, view `mrp_workcenter_view_mpc`). Nothing populates
it. Every work-centre form was therefore unsaveable, and because Odoo's web client saves
the record before running a header button, **Open Tablet Queue** failed too — with a
generic "Missing required fields" toast naming no field. The shop floor could not reach
its queue.

This module drops that one attribute. The Southbrook button and action were correct all
along and are unchanged.

## What this module did NOT fix, deliberately

`wc_type` has exactly one consumer in the entire codebase —
`mrp_product_costing/models/mrp_workorder.py:61`:

```python
if record.workcenter_id.wc_type == "H":
    variable_account_id = ...labour_cost_account_id
else:
    variable_account_id = ...machine_run_cost_account_id
```

It picks which GL account absorbs a work order's direct cost. With 13 of 14 work centres
NULL, that `else` fires on nearly every work order, so **labour is posting to the Machine
Run account across almost the whole shop**. Production today:

```
wc_type NULL : 13 work centres
wc_type 'H'  :  1
```

This is a live financial-accuracy defect. It is not in the Kitchen Ops audit — it was
found while tracing why the form would not save — and it is **not** fixed here, because
deciding which stations are Man and which are Machine is a finance decision, not an
engineering one. Making that call in code, quietly, would put a guess into the general
ledger.

## The backfill, for sign-off

`wc_type` is a two-value flag: `H` = Man, `M` = Machine. Proposed mapping from the
populated `x_sbk_station_type` taxonomy. **The last two need a human answer.**

| `x_sbk_station_type` | proposed `wc_type` | reasoning |
|---|---|---|
| engineering | H — Man | design review, no machine run time |
| assembly | H — Man | carcass and door assembly is manual |
| hardware | H — Man | hardware fitting is manual |
| quality | H — Man | inspection is manual |
| packing | H — Man | packing and dispatch is manual |
| cutting | M — Machine | panel saw, machine-time dominant |
| cnc | M — Machine | CNC router/borer, machine-time dominant |
| edge_banding | M — Machine | edge bander, machine-time dominant |
| **sanding** | **needs a decision** | hand-sanding is labour; a wide-belt sander is machine |
| **finishing** | **needs a decision** | booth and oven utility cost argues Machine; operator-driven spraying argues Man |

`drilling`, `countertop`, `subcontract` and `other` exist in the selection but no seeded
work centre uses them, so they need no mapping today.

Once the two open rows are answered, the backfill is a one-off data migration. It is not
written yet, on purpose — a mapping this consequential should be approved before it exists
as code, not after.
