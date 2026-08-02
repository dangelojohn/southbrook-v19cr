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

# Fix (design-naming, 2026-07-31) — save_design's resize-path
# regeneration of a stale auto-generated name (prod fact: design 103
# named "Walk-in - 12x30 - 2026-07-30" with ACTUAL dims 54x54), plus
# the one-time backfill migration
# (migrations/19.0.5.35.1/post-migrate.py) for rows that went stale
# before that write-path fix existed. Covers: auto-named resize
# regenerates, manually-renamed design is never clobbered (including
# a simultaneous rename+resize), first-save behaviour unchanged, and
# the migration's selective/idempotent backfill.
from . import test_design_naming

# Remainder-occupancy fix (2026-07-31) — `remainder_in` (models/
# kitchen_design.py's `_compute_totals`) is now derived from REAL
# per-wall cabinet occupancy once a design has a placed base-elevation
# run (verified live: production design 107, an L-shape whose back-wall
# run leaves a real 6" gap that a room-wide modulo hid entirely because
# 120 is evenly divisible by the 24" module), with a corner-bridging fix
# for multi-corner designs (production design 108, a U-shape, where both
# corners carry a single presentational `wall` tag but physically span
# two walls' runs) and an n==0 guard added to the legacy (no-placed-
# lines) modulo fallback so it matches controllers/main.py's /layout
# route (production design 104: an undersized room previously reported
# its own full width back as "leftover"). Independent of, and does not
# alter, test_filler_remainder_gate.py's controller-side /layout gate.
from . import test_remainder_occupancy

# Copy review (2026-07-31) — `_check_production_ready` message rewrite
# and the `kitchen.design.template.picker.fit_summary` happy-path fix:
# no leaked internal vocabulary (raw enum values, field names, key=value
# coordinate dumps) in any generated issue message, MOTION_ENVELOPE_
# COLLISION prefers the placement rule's human name over its taxonomy
# slug, UNRESOLVED_SLOT names the wall, CABINET_COLLISION shows the
# Zone field's own label, and fit_summary's happy path renders nothing.
from . import test_validator_copy

# 2026-07-31 — `_resolve_slot()`'s last-resort fallback rung (cabinet_type
# + template nominal width) took `.product_variant_id` — Odoo's arbitrary
# "first" variant of the matched template — blind to that variant's own
# width. V5-partc.md live evidence: production designs 107/108 both
# persisted a 24in cabinet for a 30in-nominal slot (SB-BASE-2DR's 24in
# variant). Fix scores every variant of the (unchanged) chosen template
# by its real placed width (`placed_width_in`, the SBK-024 helper) and
# picks the closest/exact match; pin precedence is untouched.
from . import test_slot_variant_width
# Task 26, corner-remediation (2026-07-30) — action_create_quotation's
# mirror loop now stamps sb_layout_key/sb_layout_origin on each created
# SO line and writes sale_order_line_id back onto the design line, so
# the reconcile cron's own idempotency search matches instead of
# create()ing a duplicate set of SO lines on its next tick. Proven
# against a real U-shape design pre-fix (14 -> 28 SO lines after one
# reconcile tick); this file reproduces the shape synthetically.
from . import test_quote_reconcile_dedup

# Task 29, corner-remediation (2026-07-30) — quote-lifecycle probe: five
# post-remediation surfaces (double-quote idempotency, post-quote edit +
# reconcile cron, customer-switch repricing, spec-sheet report render,
# quote-then-archive interplay) exercised against a live 4-corner +
# $0-appliance draft SO. Tests-and-evidence only — findings documented
# inline, no production files touched by this task.
from . import test_quote_lifecycle_probe

# Corner-orientation fix (2026-07-31) — regression suite for the
# 19.0.5.36.1 repair migration (back-left/front-left/front-right corner
# lines persisted under the old verified-wrong presentation).
from . import test_corner_orientation_repair_migration

# Task 36 (SBK-039) — dimensioned Plan PDF: render + zero-side-effects
# tests for the new report action (reports/kitchen_plan_report.xml) +
# its SVG generator (models/kitchen_plan_report.py).
from . import test_plan_report

# Task 15, corner-remediation (2026-07-30, SBK-027) — wall-run / base-run
# coordination: `_check_production_ready` gains WALL_RUN_SHORTER_THAN_BASE
# (warning — a wall's WALL-cabinet coverage doesn't reach as far as its
# BASE-cabinet coverage; downgraded from the audit's literal BLOCK by
# orchestrator ruling, shipped templates exhibit the shape deliberately)
# and WALL_BASE_JOINT_MISALIGNED (warning — the runs match in length but
# their interior seams don't line up by more than 0.5"), both excluding
# corner cells and appliance gaps from the comparison per the brief.
# Generation (part b) SHIPPED: _generate_standard_layout targets the base
# run's length and fills non-divisible tails with a wall-side filler.
from . import test_wall_base_run_coordination

# Task 21, corner-remediation (2026-07-30, SBK-035, reduced scope) —
# naming half shipped separately (see test_design_naming.py); this file
# covers the remainder: state guard (a design must not reach
# state='configured' with zero active cabinet lines, model constraint
# + the save_design controller fix it required), the archive action
# now form-bound (reuses the existing action_archive_bulk), and the
# "Empty designs" search filter (+ confirming "Archived" already ships).
from . import test_state_lifecycle_guards

# Task 19, corner-remediation (2026-07-30, SBK-032 + C9 UX) — validation
# surfaced in the configurator: `_normalize_production_issues` (structured
# issue list — code/severity/message/line_ids, deduped by code+line_ids)
# and the new read-only `/southbrook_kitchen/configurator/validate` JSON
# endpoint the persistent status chip polls (NOT_FOUND/ACCESS_DENIED
# shape, layout_key resolution for click-to-select, no side effects).
from . import test_validation_endpoint

# Catalog decision (2026-08-02) — SINK slot pinning to SB30:
# `_pin_sink_slots_to_sb30` seeder pins every unpinned SINK slot in the
# shipped T4 catalog to SB30's own variant, never overwrites a hand pin,
# and the resolver honours the pin (never falls through to a same-width
# sibling template like SB-BASE-2DR).
from . import test_sink_slot_pin

# Debt fix (2026-08-02) — thumbFamily explicit footprint fields:
# product.template.southbrook_thumb_family backfill migration
# (migrations/19.0.5.39.1/post-migrate.py). Covers known-SKU correctness
# for all 11 families, never overwriting a human-set value, idempotency,
# non-cabinet templates untouched, and the falsy-version no-op guard.
from . import test_thumb_family_backfill_migration

# Touch editing v1, Wave-1 server build (2026-08-02 spec §4/§5) — three
# building blocks, three files:
#   test_touch_kill_switch     — per-surface ir.config_parameter kill
#                                 switch (TOUCH_ENABLED_PARAM_BACKEND/
#                                 _PORTAL), default-on, independent per
#                                 surface, threaded into /products
#                                 (the backend's one unconditional boot
#                                 RPC).
#   test_touch_health_counters — the OPTIONAL touch_stats health-signal
#                                 dict save_design now accepts: ints
#                                 clamped, unknown/malformed input
#                                 ignored silently, absence is
#                                 byte-compatible with every existing
#                                 caller.
#   test_send_to_wall          — action_move(wall, run_seq)'s touch-v1
#                                 hardening: wall validated against the
#                                 shared kitchen_layout_engine.WALLS
#                                 constant, relaid lines returned in
#                                 the response (no client follow-up
#                                 round trip needed), and another
#                                 line's `pinned` flag survives the
#                                 rearrange action_move triggers.
from . import test_touch_kill_switch
from . import test_touch_health_counters
from . import test_send_to_wall

# E2E P0/P1 remediation (2026-08-02) — partner_name on open-configurator
# params + reload rehydration, sku/material/bom_available on serialized
# lines, and the wall-aware position label (see the module docstring).
from . import test_configurator_payload

# E2E P0 remediation (2026-08-02) — save_design no longer silently
# re-runs auto-arrange over a manually-positioned (pinned) multi-wall
# design (drag-then-autosave revert / undo-destroys-itself, both fixed
# by gating action_auto_arrange on `pinned` + the new opt-in
# `allow_relayout` kwarg), and /user_defaults' no-preference fallback
# is now a 144x120x96in room instead of 12x24in (live evidence: 0-
# cabinet "Walk-in - 12x120"/"Walk-in - 12x42" designs).
from . import test_save_preserves_positions

# P0 E2E remediation (2026-08-02) — "Auto-arrange" on a MANUAL
# (multi-wall) design now actually resolves a same-run footprint
# overlap (auto_assign=False only preserves WALL choice — the packer's
# cumulative-width cursor makes same-run overlap mathematically
# impossible to survive a call regardless of auto_assign — see
# action_auto_arrange's docstring) and returns an honest report
# (moved/overlaps_before/overlaps_after/summary) instead of silent
# success, so a caller can tell "resolved" from "already arranged"
# from "didn't fully resolve it".
from . import test_manual_arrange_resolves

# FINDING 13 E2E P0 remediation (2026-08-02, "Wizard problems") —
# southbrook.kitchen.template.parametric_fit/action_instantiate gain an
# opt-in allow_clamp kwarg (default False = every existing caller's
# hard-fail contract is untouched) that turns an over-capacity cabinet
# count into an honest reduction (fit["clamped"]/["requested_count"],
# named in the Task 16 instantiation chatter) instead of a silent one,
# plus leftover_run_in (reusing the round-robin allocator's own
# `remaining` arithmetic) surfaces wall run a fit never claimed. Also
# pins that a literal cabinet_count=0 is never conflated with "not
# given" (this file's own `is not None` contract, distinct from the
# picker wizard's `cabinet_count or None` call sites in
# models/kitchen_design.py, out of this task's scope).
from . import test_wizard_coherence
