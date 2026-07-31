# SPDX-License-Identifier: LGPL-3.0-only
# 2026-07-01 E2E audit — baseline test suite. Before this session the
# addon shipped ZERO tests (0/0/0). This is the first regression pin.
from . import test_save_design_acl

# 2026-07-02 Track B end-to-end regression insurance for the fixes
# landed across 5.6.3 → 5.6.7 (mrp.bom.note removal, autoseed order,
# Manufacture route defense-in-depth, savepoint action_confirm, plus
# the 5.6.7 UX pack: portal-partner autofill, walk-in singleton,
# active field, unlink guard).
from . import test_track_b_end_to_end

# 2026-07-06 — M1 (design totals ignore order-added cabinet lines) and
# M2 (reconcile cron can create a duplicate room) regression pins.
from . import test_m1_m2_reconcile_fixes

# 2026-07-12 — auto-arrange lifecycle: idempotence, no canonical loss, no
# derived accumulation, atomic rollback, mirror-reflects-visible.
from . import test_auto_arrange_service

# 2026-07-12 — COORDINATE_CONTRACT.md enforceability (living ledger).
from . import test_coordinate_contract

# PR2 (2026-07-12) — `wall` becomes part of the canonical persistence
# path: save_design write + validation, load_design_lines read emission.
from . import test_wall_persistence

# 2026-07-12 — Save -> Load is identity: save_design(items) ->
# load_design_lines() -> save_design(loaded items) must reproduce the
# exact same canonical model. Permanent regression pin for the
# controller-layer persistence round-trip.
from . import test_save_load_identity

# PR3.0 (2026-07-12) — y/z field-semantics migration
# (migrations/19.0.5.18.0/post-migrate.py): design-line + sale-order-
# line-mirror transform + idempotency.
from . import test_yz_semantics_migration

# PR4 (2026-07-12) — backend save_design routes non-back-wall
# configurator lines through the shared engine-placement helper
# (southbrook.kitchen.design._place_lines_on_wall); back-wall lines are
# left exactly as the client sent them.
from . import test_wall_placement_engine

# Task B3 (Materials geometry-writeback plan, Increment B) — thread
# per-instance cabinet dims (drag-resize/filler overrides) from
# design.line through action_create_quotation onto sale.order.line, plus
# the narrow sale.order.line -> mrp.bom.line mapping seam.
from . import test_geo_b3_line_dims

# v19.0.5.20.0 — L-shaped-kitchen multi-defect blocker regression pins:
# C1 (zone compute never fired — truthy default shadowed the backfill
# guard), C3 (existing corner cells not reserved via wall_start_offsets),
# C4 (engine's centre-convention poses persisted without converting to
# the anchor convention), C8 (corner lines omitted `zone`).
from . import test_zone_corner_l_shape_fixes

# M4 (2026-07-27) — collision-matrix regression suite: makes
# docs/research/corner-engine/06-collision-matrix.md's 23-pair JSON
# block visible in CI (4 pairs geometrically exercised, 19 skipped-but-
# visible pending appliance/drawer/handle/mechanism modeling), plus the
# concurrently-landed validator checks MOTION_ENVELOPE_COLLISION and
# CORNER_FILLER_MISSING.
from . import test_collision_matrix

# Kitchen Templates T1 (2026-07-27) — southbrook.kitchen.template +
# .line model regression pins: slot zone default, is_appliance_slot,
# layout_shape lexicon reuse from southbrook.room, unique code.
from . import test_kitchen_template_model
from . import test_template_instantiate
from . import test_template_picker

# Kitchen Templates T4 (2026-07-27) — the shipped starter catalog:
# every active template instantiates fully resolved at defaults,
# galley per-wall fit math, inactive engine-blocked placeholders.
from . import test_template_catalog

# Kitchen Templates T5 (2026-07-27) — server-side manipulation API:
# flip/rotate transform canonical (wall, run_seq) only, poses re-derived
# by auto-arrange; swap/width/move are canonical-only and savepoint-atomic.
from . import test_layout_manipulation

# Kitchen Templates T4a (2026-07-27) — SB-CORNER data repair (variant
# width 33->36, RH twin) + the L-10X8 flagship: engine-derived corner,
# hand-aware variant pick, flip swaps LH->RH.
from . import test_corner_repair

# Corner remediation T5 (2026-07-30) — ONE corner price-resolution path:
# the shared `_sb_resolve_corner_variant` / `_sb_variant_price` helper
# (V3-products.md claim 3).
from . import test_corner_price_resolution

# Kitchen Templates T6 (2026-07-27) — total_cabinets excludes fillers;
# filler_count carries the strips; price semantics unchanged.
from . import test_totals_filler_exclusion

# Kitchen Templates T7 (2026-07-27) — the /rearrange model seam: room
# resize re-derives through the engine; impossible shrinks are
# detectable (raise OR archive) and revertible.
from . import test_rearrange_route

# U-shape template (2026-07-27) — U-10X8X10 two-corner flagship:
# end-aware fit claims, all four corners derive (LH/RH base + 2 wall),
# flip symmetry, u_shape compat preset resolves.
from . import test_u_shape_template

# Task 1, corner-remediation (2026-07-30) — validator repair: check #3
# CABINET_COLLISION rewrite (wall/zone-grouped, real footprints), new
# OUT_OF_ROOM_BOUNDS and CORNER_HAND_MISMATCH checks.
from . import test_validator_repair

# Task 9, corner-remediation (2026-07-30) — debris repair migration
# (migrations/19.0.5.34.1/post-migrate.py): archives (active=False)
# southbrook.kitchen.design.line rows parked at the client's sentinel
# position (design 99 prod fact: x_position_in=1,000,000).
from . import test_debris_repair_migration

# Task 2, corner-remediation (2026-07-30) — BOM-gate coherence on cold
# validate: a missing BOM the quotation flow's autoseed would resolve
# reports as BOM_WILL_AUTOSEED (warning), not MISSING_BOM (blocking); a
# product genuinely outside the auto-seed logic (sibling-variant BOM
# shadowing the template fast path) still blocks.
from . import test_bom_gate_coherence

# Task 10, corner-remediation (2026-07-30) — /layout filler-remainder
# honesty gate: no phantom filler/remainder on a 0-cabinet (undersized)
# room, honest geometric remainder once populated, all four filler
# strategies place correctly. Shipped alongside Task 10's fix but never
# wired into this package's discovery until Task 9b (this file was
# never imported, so Odoo's test loader never picked it up — flagged in
# task-9-report.md's Concerns §3).
from . import test_filler_remainder_gate

# Task 9b, corner-remediation (2026-07-30) — save_design write-path
# sentinel guard: an incoming line at/beyond the debris threshold
# (abs(x)>=9999 or abs(z)>=9999) is persisted archived + is_unresolved +
# unpriced instead of an active/positioned/priced line, normal lines in
# the same batch are unaffected, and design totals exclude the sentinel
# line. Closes the write-path gap task-9-report.md's "producer" section
# identified (controllers/main.py's "back-wall lines are LEFT ALONE"
# write previously persisted x/z verbatim).
from . import test_sentinel_write_guard

# Task 6, corner-remediation (2026-07-30) — hand-aware wall corners
# (the shared _sb_resolve_corner_variant resolver generalizes to the
# wall layer once an RH twin exists) + action_swap_product's catalog
# gate (southbrook_is_cabinet=True), corner-only/footprint-fit
# restriction, and hand-contradiction guard (shared with the
# validator's CORNER_HAND_MISMATCH check via _sb_corner_expected_hand).
from . import test_corner_hand_swap

# Task 25, corner-remediation (2026-07-30) — the invariant fixture suite:
# ONE entrypoint asserting geometry/corner/reconciliation/catalogue
# invariants for EVERY active shipped kitchen template.
from . import test_invariant_matrix

# Task 11, corner-remediation (2026-07-30, SBK-024) — placed_width_in()
# unit + placement-call-site integration tests, decoupled from catalog
# data drift via a synthetic two-width cabinet family.
from . import test_placed_width_in

# Task 7, corner-remediation (2026-07-30, SBK-033 expanded scope) — ONE
# server-computed counter summary: tall_count/corner_count/panel_count/
# appliance_count/unplaced_count/summary_total, the sum-of-parts ==
# active-line-count identity for every shipped template, and the
# is_unresolved diagnostic-overlay contract (counted once under its own
# bucket, never a double-counted eighth bucket).
from . import test_counter_summary

# Task 12, corner-remediation (2026-07-30, SBK-021/022) — room-dimension
# bounds as an @api.constrains (model), out-of-range rejection in
# save_design (readable {error, error_code} response, nothing
# persisted), and /layout's 120-line hard autofill cap with a surfaced,
# non-silent warning.
from . import test_room_bounds_constraint
from . import test_room_bounds_save_and_autofill_cap

# Task 14, corner-remediation (2026-07-30, SBK-026) — the Wall Cabinet
# Top selector (fixed_gap/to_ceiling/to_soffit) now feeds the
# persisted arrange path (action_auto_arrange / _place_lines_on_wall)
# via a per-design zone_layout override, instead of the hardcoded
# 1400mm/55.12in sale.order._ZONE_LAYOUT["wall"] constant. Covers the
# three selector states end-to-end, the to_soffit gate, the new
# counter_thickness_in setting, the no-position-migration guarantee,
# and that run-axis (X) placement is unaffected.
from . import test_wall_cab_top_selector
from . import test_wall_cab_top_controller

# Task 26, corner-remediation (2026-07-30) — action_create_quotation's
# mirror loop now stamps sb_layout_key/sb_layout_origin on each created
# SO line and writes sale_order_line_id back onto the design line, so
# the reconcile cron's own idempotency search matches instead of
# create()ing a duplicate set of SO lines on its next tick. Proven
# against a real U-shape design pre-fix (14 -> 28 SO lines after one
# reconcile tick); this file reproduces the shape synthetically.
from . import test_quote_reconcile_dedup
