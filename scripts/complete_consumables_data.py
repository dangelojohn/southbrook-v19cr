# SPDX-License-Identifier: LGPL-3.0-only
"""Southbrook tools/consumables — idempotent data-completion script (B1).

Closes the biggest remaining data-quality gap in southbrook_mrp_kitchen_tools:
251 products with no x_southbrook_tool_category_id, no plain product.category
beyond the generic "Goods" catch-all, and three chemical products (EVA glue,
PUR glue, cabinet paint) with policy fields (glue type / issue UoM / coverage
rate / substrate compatibility) never keyed in.

Run via `odoo shell`, piped in (env is bound at global scope by the shell —
no odoo.cli imports here):

    docker exec -i kjb-data sh -lc \\
        'PYTHONPATH=/srv/shared odoo shell -c /etc/odoo/odoo.conf \\
         --addons-path=/mnt/extra-addons,/mnt/pg-addons \\
         --db_host=db --db_user=odoo --db_password="$PGPASSWORD" \\
         -d southbrook --http-port=8997 2>/dev/null' \\
        < scripts/complete_consumables_data.py

Mode — SKJ_APPLY env var (NOT a hardcoded flag, so the same file can be
piped in dry-run and then apply mode without editing it):

    SKJ_APPLY unset or != "1"  -> DRY RUN (default). Every write below is
        executed for real against the ORM in this transaction (so
        constraints/onchange-adjacent behaviour is exercised exactly as it
        would be for real), then the WHOLE transaction is rolled back at
        the very end via env.cr.rollback(). Nothing persists.
    SKJ_APPLY=1                -> APPLY. Same code path, same writes,
        committed at the end via env.cr.commit().

Every log line is prefixed VERB = "DID" (apply) or "WOULD" (dry run) so a
diff of the two runs' output is meaningful. Idempotent throughout: every
write is preceded by a skip-if-already-set check (never overwrites a
non-null / already-True value); re-running this script after SKJ_APPLY=1
should log nothing but SKIP-ALREADY-SET / SKIP-NOT-ROOT / SKIP-ALREADY-
LINKED lines and zero net changes.

Never touches: prices (list_price / standard_price), or the four tri-state
safety fields (x_southbrook_hazardous / x_southbrook_flammable /
x_southbrook_requires_ventilation / x_southbrook_msds_required) — those are
human-gated, John's verdicts pending, explicitly out of scope for this wave.

Sources (read-only research produced by a prior recon pass; referenced here
only for traceability, not shipped/read at runtime):
  - scratchpad/r1_classification_map.csv   251-row product classification
                                            (13 EXACT/STRONG assignable rows
                                            + 238 EXCLUDE rows w/ cluster
                                            reasons)
  - scratchpad/r1_products_raw.txt         251 products' current plain
                                            product.category state
  - scratchpad/r1_taxonomy.txt             southbrook.tool.category tree +
                                            existing plain product.category
                                            list (10 records; no "All" root
                                            exists in this DB — "Goods" is
                                            the de-facto top-level catch-all)
  - scratchpad/r2_coverage_rates.md        EVA/PUR/paint coverage-rate seeds
  - scratchpad/r3_substrate_matrix.md      adhesive/finish x substrate
                                            compatibility matrix (YES /
                                            CONDITIONAL / NO)
  - addons/southbrook_mrp_kitchen_tools/models/product_template.py
                                            exact field defs / Selections
                                            this script writes into
"""
import os

APPLY = os.environ.get("SKJ_APPLY") == "1"
VERB = "DID" if APPLY else "WOULD"

Product = env["product.template"].sudo()
Category = env["product.category"].sudo()

counters = {}


def bump(key, n=1):
    counters[key] = counters.get(key, 0) + n


def log(msg):
    print(msg)


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


hr("SOUTHBROOK CONSUMABLES DATA-COMPLETION — mode=%s (SKJ_APPLY=%r)"
   % ("APPLY" if APPLY else "DRY-RUN", os.environ.get("SKJ_APPLY")))

# ═════════════════════════════════════════════════════════════════════════
# SECTION 1 — TOOL CLASSIFICATION
# The 13 EXACT/STRONG product_id -> southbrook.tool.category xml_id
# assignments from r1_classification_map.csv, hardcoded verbatim.
# Tuple = (product_id, expected_name, tool_category_xml_id, tier, reason)
# ═════════════════════════════════════════════════════════════════════════
TOOL_CLASSIFICATION = [
    (193, "Confirmat Screw 7x50",
     "southbrook_mrp_kitchen_tools.cat_screw_confirmat", "EXACT",
     "name matches taxonomy leaf 'Confirmat Screws' exactly"),
    (194, "8mm Wood Dowel",
     "southbrook_mrp_kitchen_tools.cat_dowels", "EXACT",
     "name matches taxonomy leaf 'Dowels' exactly"),
    (195, "Minifix Cam Lock",
     "southbrook_mrp_kitchen_tools.cat_cam_locks", "EXACT",
     "'Minifix' is a cam-lock brand; matches 'Cam Locks' leaf"),
    (196, "Handle Screw M4",
     "southbrook_mrp_kitchen_tools.cat_screw_handle", "EXACT",
     "name matches taxonomy leaf 'Handle Screws' exactly"),
    (197, "Drawer Slide Screw",
     "southbrook_mrp_kitchen_tools.cat_screw_drawer", "EXACT",
     "name matches taxonomy leaf 'Drawer Slide Screws' exactly"),
    (200, "120 Grit Sandpaper",
     "southbrook_mrp_kitchen_tools.cat_abr_sheet", "STRONG",
     "generic abrasive grit paper; family clear (Abrasives) but "
     "sheet/disc/belt form factor not stated in name"),
    (203, '1/2" Compression Bit',
     "southbrook_mrp_kitchen_tools.cat_cnc_compression", "EXACT",
     "name matches taxonomy leaf 'Compression Bits' exactly"),
    (204, '3/8" Downcut Bit',
     "southbrook_mrp_kitchen_tools.cat_cnc_downcut", "EXACT",
     "name matches taxonomy leaf 'Downcut Bits' exactly"),
    (205, "CNC Drill Block Tool",
     "southbrook_mrp_kitchen_tools.cat_jig_cnc", "STRONG",
     "'block tool' for CNC drilling reads as a fixture/jig, not a router "
     "bit; no exact leaf named 'drill block'"),
    (206, "ER32 Collet Set",
     "southbrook_mrp_kitchen_tools.cat_maint_cnc_collet", "EXACT",
     "name matches taxonomy leaf 'Tool Holders / Collets' exactly"),
    (304, "Shelf Pin 5 mm Steel (Bag of 100)",
     "southbrook_mrp_kitchen_tools.cat_shelf_pins", "EXACT",
     "name matches taxonomy leaf 'Shelf Pins' exactly"),
    (307, "Eurocam Screw 25-Pack (cabinet mounting)",
     "southbrook_mrp_kitchen_tools.cat_screw_euro", "STRONG",
     "'Eurocam Screw' implies Euro-style screw family for cabinet "
     "mounting; not a verbatim leaf label match"),
    (309, "Cam-Lock Kit (RTA Joinery)",
     "southbrook_mrp_kitchen_tools.cat_cam_locks", "STRONG",
     "cam-lock family match (RTA joinery); 'Kit' bundling makes exact "
     "single-item fit uncertain"),
]

hr("SECTION 1 — tool classification (13 EXACT/STRONG rows)")
for pid, expected_name, xml_id, tier, reason in TOOL_CLASSIFICATION:
    product = Product.browse(pid)
    if not product.exists():
        log("SECTION1 SKIP-MISSING id=%s expected_name=%r "
            "(product no longer exists)" % (pid, expected_name))
        bump("s1_skip_missing")
        continue
    if product.name != expected_name:
        log("SECTION1 SKIP-DRIFT id=%s expected_name=%r actual_name=%r "
            "(id drift guard tripped — not writing)"
            % (pid, expected_name, product.name))
        bump("s1_skip_drift")
        continue
    if product.x_southbrook_tool_category_id:
        log("SECTION1 SKIP-ALREADY-SET id=%s name=%r tool_category=%r "
            "(idempotent no-op)"
            % (pid, product.name,
               product.x_southbrook_tool_category_id.display_name))
        bump("s1_skip_already_set")
        continue
    tool_cat = env.ref(xml_id, raise_if_not_found=False)
    if not tool_cat:
        log("SECTION1 GAP id=%s name=%r xml_id=%s "
            "(tool category record not found in this DB)"
            % (pid, product.name, xml_id))
        bump("s1_gap_missing_category")
        continue
    product.write({"x_southbrook_tool_category_id": tool_cat.id})
    log("SECTION1 %s id=%s|%s|-> %s [%s: %s]"
        % (VERB, pid, product.name, xml_id, tier, reason))
    bump("s1_set")

# ═════════════════════════════════════════════════════════════════════════
# SECTION 2 — PLAIN CATEGORIES for excluded clusters
# Derived from r1_classification_map.csv's 238 EXCLUDE rows' cluster
# reasons. Tuple per id = (expected_name, target_plain_category_or_None,
# cluster_reason_verbatim). target=None means no fitting bucket among the
# 7 allowed categories -> logged as GAP, never force-fit.
#
# Cluster -> category mapping used to build this table (22 distinct
# EXCLUDE reason strings in the CSV, collapsed to 7 buckets):
#   kitchen appliance placeholder SKU                    -> Appliances
#   template/placeholder slot ("Appliance Space - *")     -> Appliances
#     (name-content override; "Unresolved Template Slot"
#      stays None/GAP — too generic to bucket)
#   finished cabinet good / component                    -> Cabinets (Finished)
#   test finished-cabinet SKU (is_bom_parent)             -> Cabinets (Finished)
#   cabinet trim/architectural component (finished good)  -> Trim & Architectural
#   cabinet hardware (hinge / pull-knob / drawer slide /
#     leveler-foot / bumper / magnetic-push-catch /
#     hinge accessory / interior storage / interior
#     drawer system) incl. the one hinge test SKU         -> Hardware
#   kitchen corner-storage hardware mechanism             -> Hardware
#   cabinet lighting / handleless-profile hardware        -> Lighting
#   raw sheet-good/substrate material                     -> Sheet Goods
#   service or expense line item                          -> Services
#     (all 9 already sit in a meaningful existing category
#      -- Expenses/Services/Food/Deliveries -- so none of
#      these actually move; see root-bucket guard below)
#   construction/moisture-barrier material (id 311)        -> None/GAP
#     (no fitting bucket among the 7 allowed categories)
# ═════════════════════════════════════════════════════════════════════════
PLAIN_CATEGORY_ALLOWED = [
    "Appliances", "Cabinets (Finished)", "Trim & Architectural",
    "Hardware", "Lighting", "Sheet Goods", "Services",
]

EXCLUDE_CLUSTER_TABLE = {
    1: ('Event Registration', 'Services', 'service or expense line item - not a tool or consumable'),
    2: ('Meals', 'Services', 'service or expense line item - not a tool or consumable'),
    3: ('Travel & Accommodation', 'Services', 'service or expense line item - not a tool or consumable'),
    4: ('Mileage', 'Services', 'service or expense line item - not a tool or consumable'),
    5: ('Gifts', 'Services', 'service or expense line item - not a tool or consumable'),
    6: ('Communication', 'Services', 'service or expense line item - not a tool or consumable'),
    7: ('Expenses', 'Services', 'service or expense line item - not a tool or consumable'),
    8: ('Tips', 'Services', 'service or expense line item - not a tool or consumable'),
    9: ('Standard delivery', 'Services', 'service or expense line item - not a tool or consumable'),
    36: ('Wall 1-Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    37: ('Wall 2-Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    38: ('Base 1-Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    39: ('Base 2-Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    40: ('Drawer Bank', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    41: ('Sink Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    42: ('Tall Pantry', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    43: ('Tall Oven Housing', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    44: ('Corner Base · Lazy-Susan 36', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    45: ('Vanity', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    46: ('Accessory', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    47: ('Worktop', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    214: ('Base Cabinet · 3-Drawer Stack', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    215: ('Base Cabinet · 4-Drawer Pot', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    216: ('Sink Base · Single Bowl', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    217: ('Sink Base · Double Bowl', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    218: ('Cooktop Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    219: ('Dishwasher End Panel Base', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    220: ('Microwave Drawer Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    221: ('Wine Rack Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    223: ('Corner Base · Blind', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    224: ('Corner Base · Diagonal', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    225: ('Pull-Out Trash Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    226: ('Pull-Out Spice Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    227: ('Tray Divider Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    228: ('Mixer Lift Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    231: ('Wall Cabinet · Glass Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    232: ('Wall Cabinet · Open Shelf', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    233: ('Wall Microwave Cabinet', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    234: ('Range Hood Wall Cabinet', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    235: ('Wall Refrigerator Bridge', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    236: ('Corner Wall Cabinet', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    238: ('Pull-Out Pantry', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    240: ('Tall Broom / Utility', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    241: ('Tall Refrigerator Enclosure', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    242: ('Vanity · Single Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    243: ('Vanity · Double Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    244: ('Vanity · Drawer Bank', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    245: ('Filler Strip · 3in', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    246: ('Filler Strip · 6in', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    247: ('End Panel · Base 24x34', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    248: ('End Panel · Tall 24x84', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    249: ('Crown Molding · 96in', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    250: ('Light Rail · 96in', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    251: ('Toe Kick · 96in', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    264: ('PG_TEST Soft Close Hinge', 'Hardware', 'cabinet hardware (hinge) test SKU - no hinge-hardware branch in tool taxonomy'),
    265: ('PG_TEST Base Cabinet 600', 'Cabinets (Finished)', 'test finished-cabinet SKU (is_bom_parent) - not a tool/consumable'),
    298: ('Blum CLIP top BLUMOTION 110° (Soft-Close, Full Overlay)', 'Hardware', 'cabinet hardware (hinge) - no matching branch in tool/consumable taxonomy'),
    299: ('Blum CLIP top 110° (Non-Soft-Close, Full Overlay)', 'Hardware', 'cabinet hardware (hinge) - no matching branch in tool/consumable taxonomy'),
    300: ('Salice 200 Series (Soft-Close, Full Overlay)', 'Hardware', 'cabinet hardware (hinge, Salice 200 Series) - no matching branch in tool/consumable taxonomy'),
    301: ('Blum MOVENTO 450 mm (Soft-Close, Heavy-Duty)', 'Hardware', 'cabinet hardware (drawer slide) - no matching branch in tool/consumable taxonomy'),
    302: ('Hettich Quadro V6 450 mm (Push-To-Open)', 'Hardware', 'cabinet hardware (drawer slide runner system) - no matching branch in tool/consumable taxonomy'),
    303: ('Accuride 3832 400 mm (Side-Mount, 75 lb)', 'Hardware', 'cabinet hardware (drawer slide runner system) - no matching branch in tool/consumable taxonomy'),
    305: ('Bar Pull 128 mm CC (Brushed Nickel)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    306: ('Cabinet Knob 30 mm (Brushed Nickel)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    308: ('Cabinet Leveler 50 mm (M6 thread)', 'Hardware', 'cabinet hardware (leveler/foot) - no matching taxonomy branch'),
    310: ('Door Bumper Dot — Clear PU (Bag of 100)', 'Hardware', 'cabinet hardware (bumper) - no matching taxonomy branch'),
    311: ('Sink-Base Water Barrier — 1 m roll', None, 'construction/moisture-barrier material - no matching taxonomy leaf'),
    372: ('DTC C80 110° (Soft-Close, Full Overlay)', 'Hardware', 'cabinet hardware (hinge) - no matching branch in tool/consumable taxonomy'),
    373: ('DTC C80 110° (Non-Soft-Close, Full Overlay)', 'Hardware', 'cabinet hardware (hinge) - no matching branch in tool/consumable taxonomy'),
    374: ('Salice C2R Series 165° Blind Corner (Soft-Close)', 'Hardware', 'cabinet hardware (hinge) - no matching branch in tool/consumable taxonomy'),
    375: ('Hettich Sensys 8645i 110° (Soft-Close, Full Overlay)', 'Hardware', 'cabinet hardware (hinge) - no matching branch in tool/consumable taxonomy'),
    376: ('King Slide 3032 18" (Ball-Bearing, 100 lb)', 'Hardware', 'cabinet hardware (drawer slide) - no matching branch in tool/consumable taxonomy'),
    377: ('King Slide K2832 21" (Soft-Close Undermount)', 'Hardware', 'cabinet hardware (drawer slide) - no matching branch in tool/consumable taxonomy'),
    378: ('Hettich Actro 5D 500 mm (Soft-Close Undermount)', 'Hardware', 'cabinet hardware (drawer slide) - no matching branch in tool/consumable taxonomy'),
    379: ('Bar Pull 96 mm CC (Brushed Nickel)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    380: ('Bar Pull 128 mm CC (Matte Black)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    381: ('Bar Pull 128 mm CC (Antique Bronze)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    382: ('Bar Pull 192 mm CC (Brushed Nickel)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    383: ('Cabinet Knob 30 mm (Matte Black)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    384: ('Appliance Pull 18" (Brushed Nickel)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    385: ('Blum CLIP Mounting Plate (Cross-Cam, Screw-On)', 'Hardware', 'cabinet hardware (hinge accessory) - no matching taxonomy branch'),
    386: ('Blum CLIP Cover Cap (Set of 2, Nickel)', 'Hardware', 'cabinet hardware (hinge accessory) - no matching taxonomy branch'),
    387: ('Mesa Contemporary Knob (9621)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    388: ('Volta Classic Knob (9530)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    389: ('Ponte Transitional Knob (9766)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    390: ('Copo Classic Knob (9531)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    391: ('Claremont Transitional Knob (9627)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    393: ('K Push Tech Push and Magnetic Catches', 'Hardware', 'cabinet hardware (magnetic/push catch) - no matching taxonomy branch'),
    394: ('L&S Moove 2, 3m, Black, WDC, 2700/4000K', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    395: ('M-Series Led Strip 8mm X 5m', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    396: ('L&S Triac Power Driver, 96W', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    397: ('L&S Mec Lite Modules', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    398: ('L&S FlexSpot Light, Sleek Black, EDC 3000K/4000K', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    399: ('L&S MEC Pro Aluminium Profiles', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    400: ('L&S Sensors for Wired C Module', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    401: ('L&S Mec Lite Remotes', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    402: ('L&S Sensors for Dipswitch Module', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    403: ('L&S Moove 1, 3m, Black, WDC, 2700/4000K', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    404: ('L&S Moove 3 3M. Black WDC 2700/4000K', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    405: ('Float Shelf System', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    406: ('Gola Profiles', 'Lighting', 'cabinet lighting / handleless-profile hardware component - out of tool taxonomy scope'),
    407: ('Grantchester Contemporary Pull (9668)', 'Hardware', 'cabinet hardware (pull/knob) - no matching taxonomy branch'),
    408: ('M-Series SpaceBin Waste System Hardware Kits', 'Hardware', 'cabinet interior storage hardware system - not a tool/consumable'),
    409: ('Salice Progressa Undermount Slide', 'Hardware', 'cabinet hardware (drawer slide) - no matching branch in tool/consumable taxonomy'),
    410: ('M-Series Max Bin Waste Storage System', 'Hardware', 'cabinet interior storage hardware system - not a tool/consumable'),
    411: ('DTC Legacy Endura Internal Drawer', 'Hardware', 'cabinet interior drawer hardware system - not a tool/consumable'),
    412: ('Salice Progressa+ Undermount Slide', 'Hardware', 'cabinet hardware (drawer slide) - no matching branch in tool/consumable taxonomy'),
    515: ('Melamine Particleboard 3/4" (White/White, 4\'×8\')', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    516: ('Prefinished Birch Plywood 1/2" (4\'×8\')', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    517: ('Birch Plywood 1/4" (4\'×8\')', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    518: ('PVC Edgebanding White 1mm × 22mm', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    519: ('Melamine Particleboard 3/4" (White/Maple, 4\'×8\')', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    721: ('End Panel · Base · Standard (720 × 600)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    722: ('End Panel · Base · Inline (720 × 600)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    723: ('End Panel · Base · Tongue & Groove (720 × 600)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    724: ('End Panel · Base · Radius (720 × 600, R60)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    725: ('End Panel · Base · Square (720 × 600)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    726: ('End Panel · Tall · Standard (1970 × 570)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    727: ('End Panel · Tall · Inline (1970 × 570)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    728: ('End Panel · Tall · Tongue & Groove (1970 × 570)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    729: ('End Panel · Tall · Radius (1970 × 570, R60)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    730: ('End Panel · Tall · Square (1970 × 570)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    731: ('End Panel · Wall · Standard (720 × 330)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    732: ('End Panel · Wall · Inline (720 × 330)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    733: ('End Panel · Wall · Tongue & Groove (720 × 330)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    734: ('End Panel · Wall · Radius (720 × 330, R60)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    735: ('End Panel · Wall · Square (720 × 330)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    736: ('Filler · Base (50 × 720 × 570)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    737: ('Filler · Tall (50 × 1970 × 570)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    738: ('Filler · Wall (50 × 720 × 330)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    739: ('Corner Blind Corner Optimiser (Generic)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    740: ('Planero Blind Corner Optimiser (Vauth-Sagel)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    741: ('Corner Pull-Out Shelving (Generic)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    742: ('LAVA Corner Pull-Out Shelving', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    743: ('Magic Corner (LeMans Swing-Out, Häfele/Hettich)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    744: ('Planero Swing-Out Corner (Vauth-Sagel)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    745: ('Planero Pull-Out Larder Unit · Small Lower Door (Vauth-Sagel)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    746: ('Planero Pull-Out Larder Unit · Small Top Door (Vauth-Sagel)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    747: ('Planero Swing-Out Larder Unit · Small Lower Door (Vauth-Sagel)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    748: ('Planero Swing-Out Larder Unit · Small Top Door (Vauth-Sagel)', 'Hardware', 'kitchen corner-storage hardware mechanism - not a tool/consumable'),
    834: ('B24 Base Cabinet', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    835: ('DB24 3-Drawer Base', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    836: ('SB30 Sink Base 30"', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    837: ('W24 Wall Cabinet', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    838: ('W24-2 Wall Cabinet 2 Door', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    839: ('T24 Tall Pantry Cabinet', 'Cabinets (Finished)', 'finished cabinet good / component - not a tool or consumable'),
    840: ('FP3 Filler Panel 3"', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    999: ('24" Range / Stove (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1000: ('30" Range / Stove (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1001: ('36" Range / Stove (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1002: ('48" Range / Stove (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1003: ('60" Range / Stove (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1004: ('24" Cooktop (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1005: ('30" Cooktop (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1006: ('36" Cooktop (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1007: ('42" Cooktop (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1008: ('48" Cooktop (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1009: ('24" Single Wall Oven (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1010: ('27" Single Wall Oven (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1011: ('30" Single Wall Oven (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1012: ('27" Double Wall Oven (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1013: ('30" Double Wall Oven (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1014: ('30" Over-the-Range Microwave (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1015: ('36" Over-the-Range Microwave (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1016: ('24" Drawer Microwave (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1017: ('30" Drawer Microwave (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1018: ('30" Built-In Full-Height Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1019: ('36" Built-In Full-Height Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1020: ('42" Built-In Full-Height Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1021: ('48" Built-In Full-Height Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1022: ('30" Counter-Depth Freestanding Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1023: ('33" Counter-Depth Freestanding Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1024: ('36" Counter-Depth Freestanding Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1025: ('24" Undercounter Fridge / Drawers (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1026: ('18" Column Freezer (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1027: ('24" Column Freezer (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1028: ('30" Column Freezer (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1029: ('36" Column Freezer (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1030: ('18" Built-In Dishwasher (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1031: ('24" Built-In Dishwasher (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1032: ('24" Drawer Dishwasher (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1033: ('24" Single-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1034: ('30" Single-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1035: ('33" Single-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1036: ('36" Single-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1037: ('30" Double-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1038: ('33" Double-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1039: ('36" Double-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1040: ('39" Double-Bowl Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1041: ('12" Bar / Prep Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1042: ('15" Bar / Prep Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1043: ('18" Bar / Prep Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1044: ('30" Farmhouse / Apron Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1045: ('33" Farmhouse / Apron Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1046: ('36" Farmhouse / Apron Sink (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1047: ('30" Wall-Mount Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1048: ('36" Wall-Mount Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1049: ('42" Wall-Mount Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1050: ('48" Wall-Mount Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1051: ('54" Wall-Mount Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1052: ('60" Wall-Mount Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1053: ('30" Undercabinet Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1054: ('36" Undercabinet Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1055: ('42" Undercabinet Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1056: ('48" Undercabinet Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1057: ('30" Island Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1058: ('36" Island Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1059: ('42" Island Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1060: ('48" Island Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1061: ('54" Island Range Hood (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1062: ('15" Undercounter Wine Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1063: ('18" Undercounter Wine Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1064: ('24" Undercounter Wine Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1065: ('30" Undercounter Wine Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1066: ('18" Column Wine Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1067: ('24" Column Wine Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1068: ('30" Column Wine Fridge (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1069: ('24" Beverage Center (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1070: ('24" Warming Drawer (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1071: ('27" Warming Drawer (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1072: ('30" Warming Drawer (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1073: ('24" Built-In Coffee Machine (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1074: ('15" Undercounter Ice Maker (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1075: ('24" Undercounter Ice Maker (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1076: ('12" Trash Compactor (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1077: ('15" Trash Compactor (Placeholder)', 'Appliances', 'kitchen appliance placeholder SKU - not a tool/consumable'),
    1080: ('Raw Material: 5/8in White Melamine Panel Stock', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    1081: ('Raw Material: 1/4in Hardboard Panel Stock', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    1082: ('Raw Material: 3/4in Plywood Panel Stock', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    1089: ('Appliance Space — Range', 'Appliances', 'template/placeholder slot, not a real product - not a tool/consumable'),
    1090: ('Appliance Space — Refrigerator', 'Appliances', 'template/placeholder slot, not a real product - not a tool/consumable'),
    1091: ('Appliance Space — Dishwasher', 'Appliances', 'template/placeholder slot, not a real product - not a tool/consumable'),
    1092: ('Appliance Space — Range Hood', 'Appliances', 'template/placeholder slot, not a real product - not a tool/consumable'),
    1093: ('Unresolved Template Slot', None, 'template/placeholder slot, not a real product - not a tool/consumable'),
    1099: ('Edge Banding Tape — PVC 3/4in (per linear metre)', 'Sheet Goods', 'raw sheet-good/substrate material - separate substrate taxonomy (product.category), out of southbrook_tool_category scope'),
    1100: ('Cornice · Wall Cap Molding (2438 × 100)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    1101: ('Pelmet · Under-Cabinet Light Rail (2438 × 75)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
    1102: ('Plinth · Toe-Kick Panel (2438 × 101.6)', 'Trim & Architectural', 'cabinet trim/architectural component (finished good) - not a tool/consumable'),
}

# Internal consistency guard: every non-None target in the table above must
# be one of the 7 allowed category names — catches an authoring typo before
# it silently creates an 8th category.
_used_targets = {t for (_, t, _) in EXCLUDE_CLUSTER_TABLE.values() if t}
_unexpected = _used_targets - set(PLAIN_CATEGORY_ALLOWED)
assert not _unexpected, (
    "EXCLUDE_CLUSTER_TABLE references category names outside the allowed "
    "7: %r" % _unexpected)
assert len(EXCLUDE_CLUSTER_TABLE) == 238, (
    "expected 238 EXCLUDE rows from r1_classification_map.csv, got %d"
    % len(EXCLUDE_CLUSTER_TABLE))

hr("SECTION 2 — plain categories for excluded clusters")

# Resolve the "All" root. This DB has no product.category named "All" as
# of the recon pass (r1_taxonomy.txt lists exactly 10 plain categories, all
# either top-level or one level under "Services" -- no "All" root exists).
# Try the standard Odoo xmlid first in case a later data load added it;
# fall back to a by-name search; if genuinely absent, new categories are
# created at top level (parent_id=False), which is "the top" in a DB that
# has no deeper root than that.
ALL_CATEGORY = env.ref("product.product_category_all", raise_if_not_found=False)
if not ALL_CATEGORY:
    ALL_CATEGORY = Category.search(
        [("name", "=", "All"), ("parent_id", "=", False)], limit=1)
if ALL_CATEGORY:
    log("SECTION2 INFO root 'All' category resolved: id=%s"
        % ALL_CATEGORY.id)
else:
    log("SECTION2 INFO no 'All' root category exists in this DB "
        "(confirmed absent in the r1_taxonomy.txt recon too) -- new plain "
        "categories will be created as top-level records (parent_id=False)")

# The generic "Goods" catch-all -- the de-facto root/no-parent bucket most
# of these 251 products actually sit in (there being no "All").
GOODS_CATEGORY = Category.search(
    [("name", "=", "Goods"), ("parent_id", "=", False)], limit=1)
if GOODS_CATEGORY:
    log("SECTION2 INFO 'Goods' catch-all category resolved: id=%s"
        % GOODS_CATEGORY.id)
else:
    log("SECTION2 INFO 'Goods' category not found -- only products with "
        "categ_id=False will be treated as eligible for reassignment")

_category_cache = {}


def get_or_create_category(name):
    """Idempotent get-or-create by exact name. Returns (record, created)."""
    if name in _category_cache:
        return _category_cache[name], False
    existing = Category.search([("name", "=", name)], limit=1)
    if existing:
        _category_cache[name] = existing
        return existing, False
    vals = {"name": name}
    if ALL_CATEGORY:
        vals["parent_id"] = ALL_CATEGORY.id
    new_cat = Category.create(vals)
    _category_cache[name] = new_cat
    return new_cat, True


def in_root_bucket(product):
    """True if `product` currently sits in the root/no-parent bucket --
    either no category at all, or the generic 'Goods' catch-all -- and is
    therefore eligible for reassignment. Any other category (Fasteners,
    Adhesives, Finishing, CNC Tools, Sheet Goods, Expenses, Services,
    Services/Events, Food, Deliveries) is treated as already-meaningful
    and is never touched.
    """
    categ = product.categ_id
    if not categ:
        return True, "no category assigned"
    if GOODS_CATEGORY and categ.id == GOODS_CATEGORY.id:
        return True, "generic 'Goods' catch-all"
    return False, "already has a meaningful category (%r)" % categ.display_name


for pid in sorted(EXCLUDE_CLUSTER_TABLE):
    expected_name, target_name, reason = EXCLUDE_CLUSTER_TABLE[pid]
    product = Product.browse(pid)
    if not product.exists():
        log("SECTION2 SKIP-MISSING id=%s expected_name=%r "
            "(product no longer exists)" % (pid, expected_name))
        bump("s2_skip_missing")
        continue
    if product.name != expected_name:
        log("SECTION2 SKIP-DRIFT id=%s expected_name=%r actual_name=%r "
            "(id drift guard tripped -- not writing)"
            % (pid, expected_name, product.name))
        bump("s2_skip_drift")
        continue
    if target_name is None:
        log("SECTION2 GAP id=%s|%s| no fitting plain category among the "
            "7 allowed buckets [cluster: %s] -- needs controller/John "
            "judgment" % (pid, product.name, reason))
        bump("s2_gap")
        continue
    eligible, why = in_root_bucket(product)
    if not eligible:
        log("SECTION2 SKIP-NOT-ROOT id=%s|%s| %s -- never moving a "
            "product out of a meaningful category [cluster target would "
            "have been: %s]" % (pid, product.name, why, target_name))
        bump("s2_skip_not_root")
        continue
    category, created = get_or_create_category(target_name)
    if created:
        parent_desc = (ALL_CATEGORY.display_name if ALL_CATEGORY
                        else "(none -- top-level, no All root in this DB)")
        log("SECTION2 %s CREATE-CATEGORY name=%r parent=%s"
            % (VERB, target_name, parent_desc))
        bump("s2_categories_created")
    old_name = product.categ_id.display_name if product.categ_id else "(none)"
    product.write({"categ_id": category.id})
    log("SECTION2 %s MOVE %s|%s|%s→%s   [cluster: %s]"
        % (VERB, pid, product.name, old_name, target_name, reason))
    bump("s2_moved")

# ═════════════════════════════════════════════════════════════════════════
# SECTION 3 — COVERAGE SEEDS (products 198, 199, 202)
# Rate + UoM read from scratchpad/r2_coverage_rates.md's machine-readable
# block:
#   EVA Glue Cartridge|8|g_per_lm|6-10 g/lm|<casadei url>
#   PUR Glue|4|g_per_lm|3-5 g/lm|<casadei url>
#   Cabinet Paint White|100|ml_per_m2|75-130 ml/m2 per coat|<paintdocs url>
# glue_type target keys ('eva' / 'pur') come from GLUE_TYPE_SELECTION in
# product_template.py -- confirmed present, but re-checked dynamically
# below via fields_get() rather than assumed, per instruction.
# ═════════════════════════════════════════════════════════════════════════
hr("SECTION 3 — coverage seeds (products 198 EVA / 199 PUR / 202 paint)")

# (product_id, expected_name, glue_type_key_or_None, coverage_rate,
#  coverage_uom_key, source_comment)
COVERAGE_SEEDS = [
    (198, "EVA Glue Cartridge", "eva", 8.0, "g_per_lm",
     "R2 source: Casadei Industria EVA/PUR edge-banding comparison table "
     "(https://www.casadei-industria.com/en/knowledge/eva-or-pur-glue-for-edge-banding-a-guide-to-choosing-right/) "
     "-- industry range 6-10 g/lm, seed=8 g/lm, estimation-grade +/-15-25%%"),
    (199, "PUR Glue", "pur", 4.0, "g_per_lm",
     "R2 source: same Casadei Industria comparison table -- industry "
     "range 3-5 g/lm, seed=4 g/lm, estimation-grade +/-15-25%%"),
    (202, "Cabinet Paint White", None, 100.0, "ml_per_m2",
     "R2 source: Sherwin-Williams Sher-Wood CC-F43 Catalyzed Lacquer PDS "
     "(https://www.paintdocs.com/docs/webPDF.jsp?SITEID=SWPCGWOOD&doctype=PDS&prodno=035777339725&lang=2), "
     "cross-checked against Target Coatings EM6000 wet-film-thickness note "
     "(https://www.targetcoatings.com/2022/02/02/wet-film-thicknes/) -- "
     "industry range 75-130 ml/m2 per coat (2-3 coats typical), "
     "seed=100 ml/m2 per coat, estimation-grade +/-15-25%%"),
]

_fg = Product.fields_get(["x_southbrook_glue_type", "x_southbrook_coverage_uom"])
_glue_keys = {k for k, _v in _fg["x_southbrook_glue_type"]["selection"]}
_uom_keys = {k for k, _v in _fg["x_southbrook_coverage_uom"]["selection"]}
log("SECTION3 INFO x_southbrook_glue_type selection keys: %s" % sorted(_glue_keys))
log("SECTION3 INFO x_southbrook_coverage_uom selection keys: %s" % sorted(_uom_keys))

UNITS_UOM = env.ref("uom.product_uom_unit", raise_if_not_found=False)
if not UNITS_UOM:
    UNITS_UOM = env["uom.uom"].sudo().search([("name", "=", "Units")], limit=1)
if UNITS_UOM:
    log("SECTION3 INFO 'Units' UoM resolved: id=%s" % UNITS_UOM.id)
else:
    log("SECTION3 GAP 'Units' UoM not found in this DB -- issue_uom_id "
        "will not be set on any of the three coverage-seed products")

for pid, expected_name, glue_key, rate, uom_key, source in COVERAGE_SEEDS:
    product = Product.browse(pid)
    if not product.exists():
        log("SECTION3 SKIP-MISSING id=%s expected_name=%r "
            "(product no longer exists)" % (pid, expected_name))
        bump("s3_skip_missing")
        continue
    if product.name != expected_name:
        log("SECTION3 SKIP-DRIFT id=%s expected_name=%r actual_name=%r "
            "(id drift guard tripped -- not writing)"
            % (pid, expected_name, product.name))
        bump("s3_skip_drift")
        continue

    # -- glue_type --
    if glue_key is None:
        pass  # not a glue product (paint) -- nothing to set here
    elif product.x_southbrook_glue_type:
        log("SECTION3 SKIP-ALREADY-SET id=%s|%s| glue_type already %r"
            % (pid, product.name, product.x_southbrook_glue_type))
        bump("s3_skip_glue_already_set")
    elif glue_key not in _glue_keys:
        log("SECTION3 GAP id=%s|%s| no fitting glue_type selection key "
            "%r on this DB's field definition" % (pid, product.name, glue_key))
        bump("s3_gap_glue_type")
    else:
        product.write({"x_southbrook_glue_type": glue_key})
        log("SECTION3 %s id=%s|%s| glue_type -> %r"
            % (VERB, pid, product.name, glue_key))
        bump("s3_set_glue_type")

    # -- issue UoM --
    if product.x_southbrook_issue_uom_id:
        log("SECTION3 SKIP-ALREADY-SET id=%s|%s| issue_uom_id already %r"
            % (pid, product.name,
               product.x_southbrook_issue_uom_id.display_name))
        bump("s3_skip_uom_already_set")
    elif not UNITS_UOM:
        log("SECTION3 GAP id=%s|%s| 'Units' UoM unavailable, cannot set "
            "issue_uom_id" % (pid, product.name))
        bump("s3_gap_issue_uom")
    else:
        product.write({"x_southbrook_issue_uom_id": UNITS_UOM.id})
        log("SECTION3 %s id=%s|%s| issue_uom_id -> %r"
            % (VERB, pid, product.name, UNITS_UOM.display_name))
        bump("s3_set_issue_uom")

    # -- coverage rate --
    # 0.0 means NOT ENTERED (see field help text) -- any nonzero value is
    # treated as already-entered data and never overwritten.
    if product.x_southbrook_coverage_rate:
        log("SECTION3 SKIP-ALREADY-SET id=%s|%s| coverage_rate already %s"
            % (pid, product.name, product.x_southbrook_coverage_rate))
        bump("s3_skip_rate_already_set")
    else:
        product.write({"x_southbrook_coverage_rate": rate})
        log("SECTION3 %s id=%s|%s| coverage_rate -> %s   SOURCE: %s"
            % (VERB, pid, product.name, rate, source))
        bump("s3_set_rate")

    # -- coverage UoM --
    if product.x_southbrook_coverage_uom:
        log("SECTION3 SKIP-ALREADY-SET id=%s|%s| coverage_uom already %r"
            % (pid, product.name, product.x_southbrook_coverage_uom))
        bump("s3_skip_uom2_already_set")
    elif uom_key not in _uom_keys:
        log("SECTION3 GAP id=%s|%s| no fitting coverage_uom selection key "
            "%r on this DB's field definition" % (pid, product.name, uom_key))
        bump("s3_gap_coverage_uom")
    else:
        product.write({"x_southbrook_coverage_uom": uom_key})
        log("SECTION3 %s id=%s|%s| coverage_uom -> %r"
            % (VERB, pid, product.name, uom_key))
        bump("s3_set_coverage_uom")

# Never touched in this section, by design: list_price, standard_price,
# x_southbrook_hazardous, x_southbrook_flammable,
# x_southbrook_requires_ventilation, x_southbrook_msds_required.

# ═════════════════════════════════════════════════════════════════════════
# SECTION 4 — SUBSTRATE COMPAT (x_southbrook_compatible_material_ids)
# r3_substrate_matrix.md's YES/CONDITIONAL pairs for EVA (198) / PUR (199)
# / white lacquer paint (202), mapped onto product.category records that
# EXIST after section 2. The taxonomy has exactly one plain category that
# represents any of these substrate families -- "Sheet Goods" (its actual
# SKUs: melamine particleboard = MFC, birch plywood = PLYWOOD_BIRCH, PVC
# edgebanding = ABS_PVC_EDGEBANDING). MDF_RAW, SOLID_HARDWOOD, HPL_LAMINATE
# and WOOD_VENEER have no corresponding category or product anywhere in
# this catalog -- per instruction, those are logged as GAP, never
# force-fit onto "Sheet Goods" and never used to create a new substrate
# category (section 2's 7-category list is the hard ceiling).
# ═════════════════════════════════════════════════════════════════════════
hr("SECTION 4 — substrate compatibility (EVA / PUR / paint)")

SUBSTRATE_TO_CATEGORY = {
    "MFC": "Sheet Goods",
    "PARTICLEBOARD_RAW": "Sheet Goods",
    "MDF_RAW": None,
    "PLYWOOD_BIRCH": "Sheet Goods",
    "SOLID_HARDWOOD": None,
    "HPL_LAMINATE": None,
    "WOOD_VENEER": None,
    "ABS_PVC_EDGEBANDING": "Sheet Goods",
}

CHEMICAL_PRODUCTS = {
    198: "EVA Glue Cartridge",
    199: "PUR Glue",
    202: "Cabinet Paint White",
}

# YES/CONDITIONAL pairs only (NO pairs excluded), per r3's machine-readable
# block, keyed by product id.
R3_YES_CONDITIONAL = {
    198: [  # EVA_HOTMELT
        ("MFC", "YES"), ("PARTICLEBOARD_RAW", "YES"), ("MDF_RAW", "YES"),
        ("PLYWOOD_BIRCH", "CONDITIONAL"), ("HPL_LAMINATE", "CONDITIONAL"),
        ("WOOD_VENEER", "YES"), ("ABS_PVC_EDGEBANDING", "YES"),
    ],
    199: [  # PUR_HOTMELT
        ("MFC", "YES"), ("PARTICLEBOARD_RAW", "YES"), ("MDF_RAW", "YES"),
        ("PLYWOOD_BIRCH", "YES"), ("SOLID_HARDWOOD", "CONDITIONAL"),
        ("HPL_LAMINATE", "YES"), ("WOOD_VENEER", "YES"),
        ("ABS_PVC_EDGEBANDING", "YES"),
    ],
    202: [  # WHITE_LACQUER_PAINT
        ("MFC", "CONDITIONAL"), ("PARTICLEBOARD_RAW", "CONDITIONAL"),
        ("MDF_RAW", "YES"), ("PLYWOOD_BIRCH", "YES"),
        ("SOLID_HARDWOOD", "YES"), ("WOOD_VENEER", "YES"),
        ("ABS_PVC_EDGEBANDING", "CONDITIONAL"),
    ],
}

for pid, pairs in R3_YES_CONDITIONAL.items():
    expected_name = CHEMICAL_PRODUCTS[pid]
    product = Product.browse(pid)
    if not product.exists():
        log("SECTION4 SKIP-MISSING id=%s expected_name=%r "
            "(product no longer exists)" % (pid, expected_name))
        bump("s4_skip_missing")
        continue
    if product.name != expected_name:
        log("SECTION4 SKIP-DRIFT id=%s expected_name=%r actual_name=%r "
            "(id drift guard tripped -- not writing)"
            % (pid, expected_name, product.name))
        bump("s4_skip_drift")
        continue

    existing_ids = set(product.x_southbrook_compatible_material_ids.ids)
    new_ids = set(existing_ids)
    to_log = []
    for substrate, verdict in pairs:
        cat_name = SUBSTRATE_TO_CATEGORY.get(substrate)
        if not cat_name:
            log("SECTION4 GAP id=%s|%s| substrate=%s verdict=%s -- no "
                "fitting existing/creatable plain category for this "
                "substrate; not created (section 2's 7-category ceiling)"
                % (pid, product.name, substrate, verdict))
            bump("s4_gap_no_category")
            continue
        cat = Category.search([("name", "=", cat_name)], limit=1)
        if not cat:
            log("SECTION4 GAP id=%s|%s| substrate=%s verdict=%s target=%r "
                "-- category not found (expected to exist after section 2)"
                % (pid, product.name, substrate, verdict, cat_name))
            bump("s4_gap_category_missing")
            continue
        if cat.id in existing_ids:
            log("SECTION4 SKIP-ALREADY-LINKED id=%s|%s| substrate=%s "
                "verdict=%s -> %r already in compatible_material_ids"
                % (pid, product.name, substrate, verdict, cat_name))
            bump("s4_skip_already_linked")
            continue
        new_ids.add(cat.id)
        to_log.append((cat_name, substrate, verdict))

    if to_log:
        product.write({
            "x_southbrook_compatible_material_ids": [(6, 0, sorted(new_ids))],
        })
        for cat_name, substrate, verdict in to_log:
            log("SECTION4 %s id=%s|%s| substrate=%s verdict=%s -> "
                "compatible_material += %r"
                % (VERB, pid, product.name, substrate, verdict, cat_name))
            bump("s4_added")
    else:
        log("SECTION4 NOOP id=%s|%s| no new compatible-material links to add"
            % (pid, product.name))
        bump("s4_noop")

# Never touched in this section: prices, safety fields, x_southbrook_
# compatible_finish_ids (out of scope -- a different m2m for finish
# categories, not substrate materials).

# ═════════════════════════════════════════════════════════════════════════
# SECTION 5 — SUMMARY
# ═════════════════════════════════════════════════════════════════════════
hr("SECTION 5 — summary")

log("Counts per section (mode=%s):" % ("APPLY" if APPLY else "DRY-RUN"))
for key in sorted(counters):
    log("  %-32s %d" % (key, counters[key]))

# Recomputed DQ numbers (post-write, still inside the same transaction --
# reflects what the DB would look like if this run were committed).
tool_categorized_count = Product.search_count(
    [("x_southbrook_tool_category_id", "!=", False)])

root_domain = [("categ_id", "=", False)]
if GOODS_CATEGORY:
    root_domain = ["|", ("categ_id", "=", False),
                    ("categ_id", "=", GOODS_CATEGORY.id)]
root_category_count = Product.search_count(root_domain)

total_active_products = Product.search_count([])

log("")
log("Recomputed DQ numbers (active products, %s):"
    % ("post-APPLY" if APPLY else "as-if-applied, pre-rollback"))
log("  total active products:                     %d" % total_active_products)
log("  products WITH x_southbrook_tool_category_id: %d" % tool_categorized_count)
log("  products still in root/no-category bucket:   %d" % root_category_count)

# ═════════════════════════════════════════════════════════════════════════
# Commit or rollback
# ═════════════════════════════════════════════════════════════════════════
hr("MODE: %s" % ("APPLY -- committing" if APPLY else "DRY-RUN -- rolling back"))
if APPLY:
    env.cr.commit()
    print("Committed.")
else:
    env.cr.rollback()
    print("Rolled back -- no changes persisted. "
          "Set SKJ_APPLY=1 and re-pipe this file to commit.")
