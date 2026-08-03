# SPDX-License-Identifier: LGPL-3.0-only
from . import test_res_partner_channel
from . import test_customer_resolver
from . import test_room_unplaced_cabinets
from . import test_customer_classification
from . import test_attribute_seed
from . import test_configuration_sets_seed
from . import test_pricelist_resolution
from . import test_config_rule_domains
from . import test_analytics_capture
from . import test_bom_math
from . import test_sale_order_versioning
from . import test_sale_order_line_zone
from . import test_res_users_prefs
from . import test_order_builder_views
from . import test_qweb_reports
from . import test_demo_data
from . import test_phase1_smoke
from . import test_catalog_metadata_seed
from . import test_audit_phase2
from . import test_a1_prodboard_taxonomy
from . import test_a4_image_uuid
from . import test_a5_template_code
from . import test_prodboard_asset_importer
from . import test_template_archetype_mapping
from . import test_t2_door_area
from . import test_sales_journal_hook
from . import test_variant_sku_cost
from . import test_3d_payload_phase2
from . import test_southbrook_room
from . import test_room_wall_assignment
from . import test_room_wall_conflict_count
# 2026-07-01 E2E audit follow-up.
from . import test_rule_enforcement
from . import test_pricelist_math
from . import test_price_extra_flow
# QA bugs 2026-07-04 (configurator-from-order dead-end, Box Material
# No records, blank inline 3D preview).
from . import test_configurator_from_order
from . import test_kitchen_3d_payload_order
# QA follow-up 2026-07-05 (backend Confirm had no hard-validation guard).
from . import test_action_confirm_hard_validation
# P0 multi-wall layout (2026-07-11) — pure engine + golden back-wall parity.
from . import test_layout_engine
# Phase-1 hardening — topology-agnostic invariants + capacity enforcement.
from . import test_layout_invariants
# Task A1 — Materials geometry-writeback.
from . import test_geometry_writeback
# Repair Wave 2, Upgrade 2 — live shorthand SKUs added to _SKU_DEFAULTS.
from . import test_sku_defaults_wave2
# M1 rules-as-data corner cabinetry layer (09-rule-engine-spec.md §6-7).
from . import test_placement_rule
# Task 4 (corner-remediation-2026-07-30) — corner catalog truth: real
# SKUs for SB-CORNER-BLIND/DIAG/WALL-CORNER, SB-CORNER renamed, the
# SB-CORNER-LSUSAN duplicate archived.
from . import test_corner_family_repair
# Task 23 (corner-remediation-2026-07-30, SBK-037) — catalogue hygiene:
# unique, non-empty default_code on cabinet-family variants.
from . import test_cabinet_variant_ref_repair
# Deploy-gate hotfix (2026-07-31) — catalog-family duplicate template
# archive sweep (repair_corner_sku's SB-WALL-CORNER seed variant-first
# fix + archive_catalog_family_duplicate_templates).
from . import test_catalog_family_duplicate_archive
# Prod-audit follow-up (2026-07-31) — archive_zero_variant_duplicate_
# templates: the template-first, code-list-agnostic sweep that catches
# zero-variant catalog-family duplicates archive_catalog_family_
# duplicate_templates structurally cannot see (SB-BASE-1DR/-2DR,
# SB-WALL-1DR, SB-WALL-CORNER debris ids 1094/1095/1097/1098).
from . import test_zero_variant_duplicate_archive
# Task 4b (corner-remediation-2026-07-30 follow-up) — deploy-blocking
# gap: migrations/19.0.9.2.0/post-migrate.py re-runs the full corner/
# cabinet-variant repair chain since prod is already at 19.0.9.1.0.
from . import test_corner_repair_chain_migration
# Task 4 (2026-07-31, height-drift) — base corner height was 876.0mm
# (34.488" -> persisted 34.49") while every other base cabinet read
# 34.50"; 876.3 = 25.4 * 34.5 is inch-exact.
from . import test_corner_base_height_precision
# Phase 1 sales-rep UX fix (2026-07-31) — Task 1: hard-validation
# issues surfaced pre-confirm (audit: docs sales-ops retest).
from . import test_p1_preconfirm_validation
# Phase 1 sales-rep UX fix (2026-07-31) — Task 2: blocked-option
# reason surfaced to the rep, CLAUDE.md section 5.
from . import test_p1_blocked_option_reason
from . import test_sales_tax_ratchet
from . import test_attribute_rule_scoping
# SBK-049 (2026-08-03) — authored 2026-06-10 and never imported here, so its
# 10 tests had NEVER been collected by any selector. Found while tracing why
# the module-wide test tag under-collected; this is the second time an
# unimported test file has hidden in this package (see test_filler_remainder_
# gate.py, fixed 2026-07-30).
from . import test_sale_order_line_bom_rollup
from . import test_all_tests_are_imported
