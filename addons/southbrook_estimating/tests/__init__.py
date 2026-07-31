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
