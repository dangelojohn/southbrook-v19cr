from . import res_groups
from . import product_template, kitchen_design
# Kitchen Templates T1 — imports kitchen_design (ZONE_SELECTION,
# _ZONE_FROM_CABINET_TYPE), so must load after it.
from . import kitchen_template
# Task 36 (SBK-039) — Plan PDF floor-plan SVG generator; _inherit-only,
# no new fields, so load order relative to kitchen_design is not load-
# bearing, but keeping it here (right after the model it extends) is
# consistent with the rest of this file.
from . import kitchen_plan_report
# Recommendation D · Sprint 1 unification bridge
from . import sale_order_line, southbrook_room, reconcile
# Recommendation D · Sprint 2c · sale.order "Open in 3D"
from . import sale_order
# 2026-07-02 · reverse of the ordered→SO transition: when the SO is
# cancelled, revert its kitchen.design back to state='configured' and
# clear sale_order_id so it becomes editable + re-quotable.
from . import sale_order_cancel_sync
