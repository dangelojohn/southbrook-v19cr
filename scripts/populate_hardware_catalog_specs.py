# SPDX-License-Identifier: LGPL-3.0-only
"""Hardware Catalog (action 1571) — idempotent spec-field population.

WHY: sb_materials_hardware.action_materials_hardware_catalog is a READ-ONLY
faceted catalog. It renders 62 facet declarations + 32 columns over the 28
products classified into southbrook.tool.category. On prod (2026-08-12) the
audit reported 17/62 facets populated and 45 EMPTY -- so nearly every spec
row in the catalog rendered as a dash. Nothing was wrong with the catalog;
the taxonomy simply shipped ahead of the data ("declarations are data").
This script keys in the descriptive/spec layer for all 28 products.

Run via `odoo shell` (env is bound at global scope by the shell -- no
odoo.cli imports here). On prod, offsite:

    ssh qnap-tunnel 'D=/share/CACHEDEV3_DATA/.qpkg/container-station/bin/system-docker
      $D exec -i southbrook-odoo sh -lc "SKJ_APPLY=1 odoo shell \\
        -c /etc/odoo/odoo.conf -d southbrook --no-http \\
        --max-cron-threads=0 --log-level=warn 2>/dev/null"' \\
      < scripts/populate_hardware_catalog_specs.py

Mode -- SKJ_APPLY env var, same convention as
scripts/complete_consumables_data.py so one file serves both passes:

    unset / != "1"  -> DRY RUN. Every write really executes against the ORM
        in this transaction (so @api.constrains fire exactly as they would
        for real), then the WHOLE transaction is rolled back at the end.
    SKJ_APPLY=1     -> APPLY. Same code path, committed at the end.

Log lines are prefixed DID (apply) / WOULD (dry run) so diffing the two
runs' output is meaningful.

IDEMPOTENT: every write is guarded by a falsy-check. An already-set value is
never overwritten -- re-running after an apply logs only SKIP lines. Note
x_southbrook_reorder_multiple ships default=1.0, so it is already truthy on
all 28 and is deliberately left alone rather than "corrected".

=====================================================================
DELIBERATELY OUT OF SCOPE -- three fields this script will NOT write
=====================================================================
1. The four tri-state safety fields (x_southbrook_hazardous / _flammable /
   _requires_ventilation / _msds_required). product_template.py's own
   _onchange_tool_category states the rule outright: "a falsy category flag
   must NEVER auto-set 'no' here ... a real 'no' has to come from someone
   actually assessing the product." 24 of the 28 sit at not_assessed. A
   blanket programmatic 'no' would also silently satisfy the
   _check_safety_assessed_before_release gate. It buys ZERO facet coverage
   either way: the only safety facets declared belong to the Adhesives and
   Finishing categories, and all 7 of those are already populated by the
   four chemical products whose verdicts a human (John) entered on
   2026-08-02. So there is no completeness argument for touching them.

2. x_southbrook_preferred_vendor_id. Only two suppliers exist in this DB
   (Marathon Hardware, Upper Canada Forest Products). Naming either as the
   preferred vendor for a carbide router bit asserts a supply relationship
   that does not exist -- that is not "generic data", it is a false fact
   about a third party, and it drives purchasing. This is the ONLY reason
   8 facets (one per category) remain unpopulated after this script; they
   close in one pass the moment real vendors are assigned.

3. x_southbrook_tool_lifecycle_state. Operational state, not spec data. It
   backs no facet. Guessing "available" for a tool that is actually broken
   or out for sharpening is a claim with real consequences on the floor.

x_southbrook_vendor_sku IS written, but deliberately as "TBD-<default_code>"
-- unmistakably a placeholder. A realistic-looking invented part number is
the one value here somebody could actually order against.

x_southbrook_safety_notes IS written (pass 3). It is free-text operator
guidance -- "sharp carbide edges, isolate the spindle before a tool change"
-- NOT a hazard verdict, and it is not what the release gate reads. Where an
item has no human safety verdict yet the note says so in words rather than
implying one, and for the four chemicals it points at the supplier SDS
instead of restating a classification.

PROVENANCE: every value below is tagged.
    "NAME"    -- read off the product's own name/code. 1/2" Compression Bit
                 IS 12.7 mm; Panel Saw Blade 305mm IS 305 mm; 120 Grit IS
                 P120. These are facts about the record, not guesses.
    "TYPICAL" -- industry-standard value for that class of item, correct to
                 cabinet-shop practice but NOT read from a vendor sheet.
                 Verify against the real spec sheet before anyone machines
                 or purchases to these numbers.
Counts of each are printed at the end.
"""
import os

APPLY = os.environ.get("SKJ_APPLY") == "1"
VERB = "DID" if APPLY else "WOULD"

Product = env["product.template"].sudo()
Provider = env["tools.catalog.provider"].sudo()

UOM_UNITS = 1  # uom.uom "Units" -- verified id on this DB

# product.category ids for the substrate families created 2026-08-02 under
# "Sheet Goods". Re-resolved by name below so an id drift cannot mis-link.
SUBSTRATE_NAMES = {
    "mfc": "Melamine-Faced Chipboard",
    "mdf": "MDF",
    "ply": "Plywood",
    "hardwood": "Solid Hardwood",
}
ALL_SUBSTRATES = ("mfc", "mdf", "ply", "hardwood")
PANEL_ONLY = ("mfc", "mdf")

# ═════════════════════════════════════════════════════════════════════
# Per-product spec table. Keyed by default_code (stable, human-checkable).
# NAME_GUARD is asserted as a case-insensitive substring of the live
# product name -- if a code has been reused for a different item this
# script SKIPS it loudly rather than writing specs onto the wrong record.
# Values are (value, provenance) pairs. "compat" is a list of
# SUBSTRATE_NAMES keys, written to x_southbrook_compatible_material_ids.
# ═════════════════════════════════════════════════════════════════════
SPECS = {
    # ---------------- Screws (facet category "Screws") ----------------
    "FAST-CONFIRMAT-50": {
        "NAME_GUARD": "confirmat screw",
        "x_southbrook_thread_type": ("confirmat", "NAME"),
        "x_southbrook_screw_size": ("7 mm", "NAME"),
        "x_southbrook_screw_length_mm": (50.0, "NAME"),
        "x_southbrook_head_type": ("flat", "TYPICAL"),
        "x_southbrook_drive_type": ("pozidriv", "TYPICAL"),
        "x_southbrook_material_grade": ("Case-hardened carbon steel", "TYPICAL"),
        "x_southbrook_coating": ("Zinc-plated", "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Labelled bin, dry store.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Pre-drill 7 mm body / 5 mm pilot for correct Confirmat seating.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (500.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (5000.0, "TYPICAL"),
        "compat": (PANEL_ONLY, "TYPICAL"),
    },
    "FAST-HANDLE-SCR": {
        "NAME_GUARD": "handle screw",
        "x_southbrook_thread_type": ("machine", "NAME"),
        "x_southbrook_screw_size": ("M4", "NAME"),
        "x_southbrook_screw_length_mm": (25.0, "TYPICAL"),
        "x_southbrook_head_type": ("truss", "TYPICAL"),
        "x_southbrook_drive_type": ("phillips", "TYPICAL"),
        "x_southbrook_material_grade": ("Zinc-plated steel", "TYPICAL"),
        "x_southbrook_coating": ("Zinc-plated", "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Labelled bin, dry store.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Length must not exceed door thickness plus handle boss.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (200.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (2000.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "FAST-SLIDE-SCR": {
        "NAME_GUARD": "drawer slide screw",
        "x_southbrook_thread_type": ("coarse", "TYPICAL"),
        "x_southbrook_screw_size": ("#8", "TYPICAL"),
        "x_southbrook_screw_length_mm": (16.0, "TYPICAL"),
        "x_southbrook_head_type": ("pan", "TYPICAL"),
        "x_southbrook_drive_type": ("phillips", "TYPICAL"),
        "x_southbrook_material_grade": ("Case-hardened carbon steel", "TYPICAL"),
        "x_southbrook_coating": ("Zinc-plated", "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Labelled bin, dry store.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Pan head seats flush in slide mounting slots -- do not "
            "substitute a countersunk head.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (500.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (5000.0, "TYPICAL"),
        "compat": (("mfc", "mdf", "ply"), "TYPICAL"),
    },
    "MRH-MTGSCR-25PK": {
        "NAME_GUARD": "eurocam screw",
        "x_southbrook_thread_type": ("euro", "NAME"),
        "x_southbrook_screw_size": ("6.3 mm", "TYPICAL"),
        "x_southbrook_screw_length_mm": (13.5, "TYPICAL"),
        "x_southbrook_head_type": ("euro", "TYPICAL"),
        "x_southbrook_drive_type": ("pozidriv", "TYPICAL"),
        "x_southbrook_material_grade": ("Case-hardened carbon steel", "TYPICAL"),
        "x_southbrook_coating": ("Nickel-plated", "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Keep packs sealed until issued.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Seats into a 5 mm system hole -- no pilot drill required.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (20.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (200.0, "TYPICAL"),
        "compat": (PANEL_ONLY, "TYPICAL"),
    },
    # ---------------- Fasteners (facet category "Fasteners") ----------
    "FAST-DOWEL-8MM": {
        "NAME_GUARD": "dowel",
        "x_southbrook_screw_size": ("8 mm", "NAME"),
        "x_southbrook_screw_length_mm": (30.0, "TYPICAL"),
        "x_southbrook_material_grade": ("Beech hardwood", "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Dry store, sealed -- moisture swell prevents seating.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Bore 8 mm x 16 mm deep per side; fluted body needs glue in "
            "the hole, not on the dowel.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (500.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (5000.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "FAST-CAM": {
        "NAME_GUARD": "cam lock",
        "x_southbrook_screw_size": ("15 mm", "TYPICAL"),
        "x_southbrook_screw_length_mm": (12.5, "TYPICAL"),
        "x_southbrook_material_grade": ("Zinc die-cast", "TYPICAL"),
        "x_southbrook_coating": ("Nickel-plated", "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Labelled bin, dry store.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "15 mm cam housing bore, 12.5 mm deep; arrow on cam face must "
            "point at the bolt before tightening.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (200.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (2000.0, "TYPICAL"),
        "compat": (PANEL_ONLY, "TYPICAL"),
    },
    "MRH-SHLFPIN-5MM": {
        "NAME_GUARD": "shelf pin",
        "x_southbrook_screw_size": ("5 mm", "NAME"),
        "x_southbrook_screw_length_mm": (16.0, "TYPICAL"),
        "x_southbrook_material_grade": ("Nickel-plated steel", "NAME"),
        "x_southbrook_coating": ("Nickel-plated", "NAME"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Keep bagged -- loose pins are a slip hazard.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Suits the standard 5 mm / 32 mm system hole line boring.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (10.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (100.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "MRH-CAMLOCK-KIT": {
        "NAME_GUARD": "cam-lock kit",
        "x_southbrook_screw_size": ("15 mm", "TYPICAL"),
        "x_southbrook_screw_length_mm": (34.0, "TYPICAL"),
        "x_southbrook_material_grade": (
            "Zinc die-cast cam with steel bolt", "TYPICAL"),
        "x_southbrook_coating": ("Nickel-plated", "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Keep kit components bagged together.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "RTA joint: 34 mm bolt into a 5 mm pilot, cam into a 15 mm "
            "bore. Do not overtighten -- the cam strips the panel.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (50.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (500.0, "TYPICAL"),
        "compat": (PANEL_ONLY, "TYPICAL"),
    },
    # ---------------- Saw Blades --------------------------------------
    "SBK-TOOL-PSB-305": {
        "NAME_GUARD": "panel saw blade",
        "x_southbrook_blade_diameter_mm": (305.0, "NAME"),
        "x_southbrook_bore_size_mm": (30.0, "TYPICAL"),
        "x_southbrook_tooth_count": (96, "TYPICAL"),
        "x_southbrook_kerf_width_mm": (3.2, "TYPICAL"),
        "x_southbrook_material_grade": (
            "Carbide-tipped (micrograin C4)", "TYPICAL"),
        "x_southbrook_coating": ("PTFE anti-friction", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (3000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (6000.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (15000.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (30, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Hang on blade rack with tooth guard fitted; keep dry.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Inspect tooth condition each shift. Do not exceed 6000 rpm. "
            "Resin build-up causes burning long before the teeth dull.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (1.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (4.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    # ---------------- CNC Router Bits ---------------------------------
    "CNC-COMP-HALF": {
        "NAME_GUARD": "compression bit",
        "x_southbrook_cutting_diameter_mm": (12.7, "NAME"),
        "x_southbrook_shank_diameter_mm": (12.7, "TYPICAL"),
        "x_southbrook_cutting_length_mm": (31.75, "TYPICAL"),
        "x_southbrook_overall_length_mm": (76.2, "TYPICAL"),
        "x_southbrook_material_grade": ("Solid carbide", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (12000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (18000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (3000.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (7000.0, "TYPICAL"),
        "x_southbrook_estimated_life_qty": (800.0, "TYPICAL"),
        "x_southbrook_estimated_life_unit": ("meters", "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (800.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (14, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store in bit sleeve, cutting edge protected; keep dry.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Up/down shear geometry gives clean edges both faces -- needs "
            "full-depth engagement in one pass to work.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (2.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (8.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "CNC-DOWN-3-8": {
        "NAME_GUARD": "downcut bit",
        "x_southbrook_cutting_diameter_mm": (9.53, "NAME"),
        "x_southbrook_shank_diameter_mm": (9.53, "TYPICAL"),
        "x_southbrook_cutting_length_mm": (25.4, "TYPICAL"),
        "x_southbrook_overall_length_mm": (63.5, "TYPICAL"),
        "x_southbrook_material_grade": ("Solid carbide", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (14000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (20000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (3000.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (6500.0, "TYPICAL"),
        "x_southbrook_estimated_life_qty": (700.0, "TYPICAL"),
        "x_southbrook_estimated_life_unit": ("meters", "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (700.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (14, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store in bit sleeve, cutting edge protected; keep dry.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Downcut holds the top face down -- excellent top edge, but it "
            "packs chips into the cut. Do not use for full-depth slotting.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (2.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (8.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "SBK-TOOL-CCB-6": {
        "NAME_GUARD": "compression router bit",
        "x_southbrook_cutting_diameter_mm": (6.0, "NAME"),
        "x_southbrook_shank_diameter_mm": (6.0, "TYPICAL"),
        "x_southbrook_cutting_length_mm": (22.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (60.0, "TYPICAL"),
        "x_southbrook_material_grade": ("Solid carbide", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (16000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (22000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (2500.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (6000.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (500.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (14, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store in bit sleeve, cutting edge protected; keep dry.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "6 mm shank -- check collet is a true 6 mm, not a 1/4 in.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (3.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (10.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "SBK-TOOL-CSB-8": {
        "NAME_GUARD": "straight router bit",
        "x_southbrook_cutting_diameter_mm": (8.0, "NAME"),
        "x_southbrook_shank_diameter_mm": (8.0, "TYPICAL"),
        "x_southbrook_cutting_length_mm": (25.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (60.0, "TYPICAL"),
        "x_southbrook_material_grade": ("Solid carbide", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (15000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (21000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (2500.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (6000.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (500.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (14, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store in bit sleeve, cutting edge protected; keep dry.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "General-purpose slotting and rebate bit; step depth of cut in "
            "hardwood.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (3.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (10.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "SBK-TOOL-CSP-6": {
        "NAME_GUARD": "spiral router bit",
        "x_southbrook_cutting_diameter_mm": (6.0, "NAME"),
        "x_southbrook_shank_diameter_mm": (6.0, "TYPICAL"),
        "x_southbrook_cutting_length_mm": (22.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (60.0, "TYPICAL"),
        "x_southbrook_material_grade": ("Solid carbide", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (16000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (22000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (2500.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (6000.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (500.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (14, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store in bit sleeve, cutting edge protected; keep dry.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Upcut spiral clears chips well but lifts the top face -- "
            "score or tape veneered panels.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (3.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (10.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "SBK-TOOL-CVG-90": {
        "NAME_GUARD": "v-groove",
        "x_southbrook_cutting_diameter_mm": (12.7, "TYPICAL"),
        "x_southbrook_shank_diameter_mm": (6.0, "TYPICAL"),
        "x_southbrook_cutting_length_mm": (10.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (50.0, "TYPICAL"),
        "x_southbrook_material_grade": ("Solid carbide", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (14000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (18000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (1500.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (4000.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (400.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (14, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store in bit sleeve, point protected; keep dry.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "90 degree included angle for V-groove folding. Step depth "
            "<= 4 mm per pass in MDF; the point burns if fed too slowly.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (2.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (6.0, "TYPICAL"),
        "compat": (PANEL_ONLY, "TYPICAL"),
    },
    # ---------------- Drill and Boring Bits ---------------------------
    "SBK-TOOL-CFM-M6": {
        "NAME_GUARD": "confirmat boring bit",
        "x_southbrook_cutting_diameter_mm": (5.0, "NAME"),
        "x_southbrook_shank_diameter_mm": (10.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (70.0, "TYPICAL"),
        "x_southbrook_material_grade": (
            "HSS body with brazed carbide tip", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (3000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (8000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (800.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (2500.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (12000.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (30, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Return to the boring-head index tray; keep the 10 mm shank "
            "clean.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Steps the 5 mm pilot for an M6 Confirmat. Check depth stop "
            "against panel thickness before the first hole.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (2.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (6.0, "TYPICAL"),
        "compat": (PANEL_ONLY, "TYPICAL"),
    },
    "SBK-TOOL-CFM-M8": {
        "NAME_GUARD": "confirmat boring bit",
        "x_southbrook_cutting_diameter_mm": (7.0, "NAME"),
        "x_southbrook_shank_diameter_mm": (10.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (70.0, "TYPICAL"),
        "x_southbrook_material_grade": (
            "HSS body with brazed carbide tip", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (3000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (8000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (800.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (2500.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (12000.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (30, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Return to the boring-head index tray; keep the 10 mm shank "
            "clean.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Steps the 7 mm pilot for an M8 Confirmat. Do not substitute "
            "the M6 bit -- the screw will strip.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (2.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (6.0, "TYPICAL"),
        "compat": (PANEL_ONLY, "TYPICAL"),
    },
    "SBK-TOOL-HCB-35": {
        "NAME_GUARD": "hinge cup boring bit",
        "x_southbrook_cutting_diameter_mm": (35.0, "NAME"),
        "x_southbrook_shank_diameter_mm": (10.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (57.0, "TYPICAL"),
        "x_southbrook_material_grade": (
            "Carbide-tipped with steel body", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (2500.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (5000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (500.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (1500.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (8000.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (30, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store tip-up in the boring tray; the spurs chip if dropped.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "35 mm cup at 12.5 mm depth is the standard Euro hinge "
            "setting. Breaks through a 16 mm door if the stop slips.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (2.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (5.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "SBK-TOOL-DRB-5": {
        "NAME_GUARD": "boring bit",
        "x_southbrook_cutting_diameter_mm": (5.0, "NAME"),
        "x_southbrook_shank_diameter_mm": (10.0, "TYPICAL"),
        "x_southbrook_overall_length_mm": (70.0, "TYPICAL"),
        "x_southbrook_material_grade": (
            "HSS body with brazed carbide tip", "TYPICAL"),
        "x_southbrook_coating": ("Uncoated", "TYPICAL"),
        "x_southbrook_rotation_speed_min": (3000.0, "TYPICAL"),
        "x_southbrook_rotation_speed_max": (8000.0, "TYPICAL"),
        "x_southbrook_feed_rate_min": (800.0, "TYPICAL"),
        "x_southbrook_feed_rate_max": (2500.0, "TYPICAL"),
        "x_southbrook_sharpening_interval_qty": (12000.0, "TYPICAL"),
        "x_southbrook_inspection_interval_days": (30, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Return to the boring-head index tray; keep the 10 mm shank "
            "clean.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "5 mm system hole for drawer runners and shelf pins on the "
            "32 mm line.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (4.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (12.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    # ---------------- Abrasives ---------------------------------------
    "ABR-120-GRIT": {
        "NAME_GUARD": "grit sandpaper",
        "x_southbrook_grit": ("P120", "NAME"),
        "x_southbrook_material_grade": ("Aluminium oxide", "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Dust mask + safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Flat and dry -- damp sheets shed grit.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Intermediate grit: after P80 levelling, before P180 finish "
            "sanding.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (50.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (500.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    "SBK-TOOL-SB-CAB": {
        "NAME_GUARD": "sanding belt",
        "x_southbrook_grit": ("P150", "TYPICAL"),
        "x_southbrook_material_grade": (
            "Aluminium oxide, cloth-backed", "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Dust mask + safety glasses + hearing protection", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Hang on a belt bar -- never fold; a creased belt tracks off "
            "and tears.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Check tracking and platen pressure at belt change; a glazed "
            "belt burns the panel rather than cutting.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (2.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (12.0, "TYPICAL"),
        "compat": (ALL_SUBSTRATES, "TYPICAL"),
    },
    # ---------------- Adhesives ---------------------------------------
    "ADH-EVA": {
        "NAME_GUARD": "eva",
        "x_southbrook_glue_type": ("eva", "NAME"),
        "x_southbrook_open_time_min": (0.5, "TYPICAL"),
        "x_southbrook_cure_time_min": (1.0, "TYPICAL"),
        "x_southbrook_shelf_life_days": (730, "TYPICAL"),
        "x_southbrook_expiry_required": (True, "TYPICAL"),
        "x_southbrook_storage_temperature_notes": (
            "Store 10-25 C, dry and sealed.", "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Heat-resistant gloves + safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Keep cartridges sealed and upright; contamination in the pot "
            "shows up as edge-line voids.", "TYPICAL"),
        "x_southbrook_disposal_notes": (
            "Cured EVA is inert -- general waste. Never pour molten "
            "adhesive to drain.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Short open time: panel must reach the pressure zone "
            "immediately after glue application.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (10.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (60.0, "TYPICAL"),
    },
    "ADH-PUR": {
        "NAME_GUARD": "pur",
        "x_southbrook_glue_type": ("pur", "NAME"),
        "x_southbrook_open_time_min": (1.0, "TYPICAL"),
        "x_southbrook_cure_time_min": (60.0, "TYPICAL"),
        "x_southbrook_shelf_life_days": (270, "TYPICAL"),
        "x_southbrook_expiry_required": (True, "TYPICAL"),
        "x_southbrook_storage_temperature_notes": (
            "Store 15-25 C, sealed -- moisture-cured, so an opened pack "
            "has a much shorter life.", "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Respirator + nitrile gloves + safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Sealed, dry, away from humidity. Purge the pot before "
            "shutdown or it sets solid.", "TYPICAL"),
        "x_southbrook_disposal_notes": (
            "Uncured PUR is hazardous waste -- licensed disposal only. "
            "Cured offcuts are inert.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Reaches handling strength in about an hour but keeps curing "
            "for 24 h -- do not machine the edge same-shift.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (4.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (24.0, "TYPICAL"),
    },
    # ---------------- Finishing ---------------------------------------
    "FIN-FILTER": {
        "NAME_GUARD": "filter",
        "x_southbrook_ppe_required": (
            "Dust mask + nitrile gloves", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store flat and dry, away from solvent vapour.", "TYPICAL"),
        "x_southbrook_disposal_notes": (
            "Overspray-laden filters follow local coatings-waste rules -- "
            "not general waste.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Change on differential pressure, not on a calendar. A loaded "
            "filter starves the booth and spoils the finish.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (20.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (120.0, "TYPICAL"),
        # No shelf life: a dry filter medium does not expire. Left empty
        # deliberately -- the Finishing shelf-life facet is carried by the
        # paint below.
    },
    "FIN-PAINT-WHITE": {
        "NAME_GUARD": "paint",
        "x_southbrook_shelf_life_days": (365, "TYPICAL"),
        "x_southbrook_expiry_required": (True, "TYPICAL"),
        "x_southbrook_storage_temperature_notes": (
            "Store 5-30 C. Do not freeze.", "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Respirator + nitrile gloves + safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Sealed, upright, in the flammables cabinet; keep away from "
            "ignition sources.", "TYPICAL"),
        "x_southbrook_disposal_notes": (
            "Coating waste and cleaning solvent -- licensed hazardous "
            "disposal only.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Stir, never shake. Strain into the pot; skinned material "
            "causes nibs that only show after cure.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (10.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (60.0, "TYPICAL"),
    },
    # ---------------- Items outside any facet category ----------------
    # These three back no facet declaration (their tool categories have
    # none), but they are rows in the same catalog and rendered dashes
    # just like the rest.
    "CNC-DRILL-BLOCK": {
        "NAME_GUARD": "drill block",
        "x_southbrook_material_grade": ("Anodised aluminium", "TYPICAL"),
        "x_southbrook_estimated_life_qty": (5000.0, "TYPICAL"),
        "x_southbrook_estimated_life_unit": ("cycles", "TYPICAL"),
        "x_southbrook_inspection_interval_days": (90, "TYPICAL"),
        "x_southbrook_calibration_interval_days": (180, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Store on the fixture shelf; protect the location pins.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Re-probe the fixture zero after any re-clamp -- a shifted "
            "block scraps a whole nest.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (1.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (2.0, "TYPICAL"),
    },
    "CNC-COLLET-ER32": {
        "NAME_GUARD": "collet",
        "x_southbrook_material_grade": ("Hardened spring steel", "TYPICAL"),
        "x_southbrook_estimated_life_qty": (2000.0, "TYPICAL"),
        "x_southbrook_estimated_life_unit": ("cycles", "TYPICAL"),
        "x_southbrook_inspection_interval_days": (30, "TYPICAL"),
        "x_southbrook_calibration_interval_days": (365, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": ("Safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Keep in the collet tray, taper clean and lightly oiled.",
            "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Clean taper and nut threads at every tool change. Replace the "
            "collet once runout exceeds 0.01 mm -- do not chase it with "
            "feed changes.", "TYPICAL"),
        "x_southbrook_min_stock_qty": (1.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (3.0, "TYPICAL"),
    },
    "SBK-TOOL-EBH-01": {
        "NAME_GUARD": "hot-melt head",
        "x_southbrook_material_grade": (
            "Machined steel with PTFE-lined glue pot", "TYPICAL"),
        "x_southbrook_inspection_interval_days": (30, "TYPICAL"),
        "x_southbrook_cleaning_required_after_use": (True, "TYPICAL"),
        "x_southbrook_ppe_required": (
            "Heat-resistant gloves + safety glasses", "TYPICAL"),
        "x_southbrook_storage_instructions": (
            "Keep the glue pot purged and capped when idle.", "TYPICAL"),
        "x_southbrook_notes_for_operator": (
            "Purge at shift end. Do not exceed the 200 C set point -- "
            "charred adhesive scores the edge and blocks the nozzle.",
            "TYPICAL"),
        "x_southbrook_min_stock_qty": (1.0, "TYPICAL"),
        "x_southbrook_max_stock_qty": (2.0, "TYPICAL"),
    },
}

# ═════════════════════════════════════════════════════════════════════
# Resolve substrate categories by NAME (id drift guard).
# ═════════════════════════════════════════════════════════════════════
Categ = env["product.category"].sudo()
substrate_ids = {}
for key, nm in SUBSTRATE_NAMES.items():
    rec = Categ.search([("name", "=", nm)], limit=1)
    if rec:
        substrate_ids[key] = rec.id
    else:
        print("WARN substrate category not found, compat links for it will "
              "be skipped: %s" % nm)

# ═════════════════════════════════════════════════════════════════════
# Pass 1 -- category-derived fields (mirrors _onchange_tool_category,
# which does NOT fire on an ORM write). Never sets a safety tri-state:
# the onchange only ever sets 'yes' from a category assertion, never
# 'no', and this reproduces exactly that.
# ═════════════════════════════════════════════════════════════════════
print("")
print("=== PASS 1: category-derived (family / directness / flags) ===")
p1 = 0
prods = Product.search([("x_southbrook_tool_category_id", "!=", False)],
                       order="id")
print("in scope: %d products" % len(prods))
for p in prods:
    cat = p.x_southbrook_tool_category_id
    vals = {}
    if not p.x_southbrook_is_tool:
        vals["x_southbrook_is_tool"] = True
    if not p.x_southbrook_tool_family and cat.tool_family:
        vals["x_southbrook_tool_family"] = cat.tool_family
    if not p.x_southbrook_directness:
        if cat.directness:
            vals["x_southbrook_directness"] = cat.directness
        elif cat.consumable:
            vals["x_southbrook_directness"] = "direct_consumable"
        elif cat.reusable:
            vals["x_southbrook_directness"] = "direct_production_tool"
    if cat.reusable and not p.x_southbrook_is_reusable_tool:
        vals["x_southbrook_is_reusable_tool"] = True
    if cat.consumable and not p.x_southbrook_is_consumable_tool:
        vals["x_southbrook_is_consumable_tool"] = True
    if cat.has_expiry_date and not p.x_southbrook_expiry_required:
        vals["x_southbrook_expiry_required"] = True
    if not p.x_southbrook_issue_uom_id:
        vals["x_southbrook_issue_uom_id"] = (
            cat.default_issue_uom_id.id or UOM_UNITS)
    if not p.x_southbrook_vendor_sku:
        # Deliberately synthetic -- see module docstring.
        vals["x_southbrook_vendor_sku"] = "TBD-%s" % (p.default_code or p.id)
    if vals:
        p.write(vals)
        p1 += len(vals)
        print("%s %-18s %s" % (VERB, p.default_code or p.id,
                               ", ".join(sorted(vals))))
    else:
        print("SKIP-ALREADY-SET   %s" % (p.default_code or p.id))
print("PASS1_FIELDS_WRITTEN=%d" % p1)

# ═════════════════════════════════════════════════════════════════════
# Pass 2 -- the per-product spec table.
# ═════════════════════════════════════════════════════════════════════
print("")
print("=== PASS 2: per-product spec fields ===")
written = 0
skipped = 0
prov_counts = {"NAME": 0, "TYPICAL": 0}
missing = []
guard_fails = []

for code, spec in SPECS.items():
    p = Product.search([("default_code", "=", code)], limit=1)
    if not p:
        missing.append(code)
        print("SKIP-NOT-FOUND     %s" % code)
        continue
    guard = spec["NAME_GUARD"].lower()
    live_name = (p.name or "").lower()
    if guard not in live_name:
        guard_fails.append("%s (expected %r in %r)" % (code, guard, p.name))
        print("SKIP-NAME-GUARD    %s -- %r not in %r" % (code, guard, p.name))
        continue
    if not p.x_southbrook_tool_category_id:
        guard_fails.append("%s (no tool category)" % code)
        print("SKIP-NO-TOOL-CAT   %s" % code)
        continue

    vals = {}
    for field, pair in spec.items():
        if field == "NAME_GUARD":
            continue
        value, prov = pair
        if field == "compat":
            if p.x_southbrook_compatible_material_ids:
                skipped += 1
                continue
            ids = [substrate_ids[k] for k in value if k in substrate_ids]
            if not ids:
                continue
            vals["x_southbrook_compatible_material_ids"] = [(6, 0, ids)]
            prov_counts[prov] += 1
            continue
        if p[field]:                      # never overwrite
            skipped += 1
            continue
        vals[field] = value
        prov_counts[prov] += 1

    if vals:
        p.write(vals)
        written += len(vals)
        print("%s %-18s %d fields: %s" % (
            VERB, code, len(vals), ", ".join(
                sorted(f.replace("x_southbrook_", "") for f in vals))))
    else:
        print("SKIP-ALREADY-SET   %s" % code)

print("")
print("PASS2_FIELDS_WRITTEN=%d" % written)
print("PASS2_FIELDS_SKIPPED_ALREADY_SET=%d" % skipped)
print("PROVENANCE_NAME_DERIVED=%d" % prov_counts["NAME"])
print("PROVENANCE_INDUSTRY_TYPICAL=%d" % prov_counts["TYPICAL"])
print("PRODUCTS_IN_SPEC_TABLE=%d" % len(SPECS))
if missing:
    print("MISSING_CODES=%s" % ",".join(missing))
if guard_fails:
    print("GUARD_FAILURES=%s" % " | ".join(guard_fails))

# ═════════════════════════════════════════════════════════════════════
# Pass 3 -- safety_notes (operator guidance, NOT a verdict) plus the two
# remaining classification booleans, derived from the tool category the
# same way pass 1 derives the others.
#
# NOTE ON WORDING: for the 24 products whose tri-state verdicts are still
# not_assessed, the note SAYS SO. That is the opposite of papering over
# the gap -- an operator reading the catalog now sees "classification not
# yet assessed" in words instead of an ambiguous dash.
# ═════════════════════════════════════════════════════════════════════
NOT_ASSESSED_TAIL = (" Formal hazard classification for this item is still "
                     "recorded as NOT ASSESSED -- treat the safety flags on "
                     "this record as unanswered, not as 'safe'.")

SAFETY_NOTES_BY_CODE = {
    "ADH-EVA": (
        "Hot-melt adhesive: contact burn risk at pot temperature. "
        "Heat-resistant gloves and eye protection when charging the pot. "
        "Consult the supplier SDS before handling -- the safety verdicts on "
        "this record were entered by a human on 2026-08-02."),
    "ADH-PUR": (
        "Moisture-cured polyurethane: isocyanate exposure risk while "
        "uncured. Respirator and extraction required per the verdicts on "
        "this record. Never purge to an open bench. Consult the supplier "
        "SDS before handling."),
    "FIN-FILTER": (
        "Overspray-loaded filter media is combustible -- bag and remove "
        "from the booth, keep clear of ignition sources, never reuse."),
    "FIN-PAINT-WHITE": (
        "Recorded conservatively as SOLVENT-BORNE. Ventilation, respirator "
        "and flammables storage apply per the verdicts on this record. If "
        "this product is in fact water-based, the hazardous and flammable "
        "flags should be revised. Consult the supplier SDS before use."),
    "ABR-120-GRIT": (
        "Wood dust is a respiratory sensitiser -- extraction on and dust "
        "mask worn. Do not use a torn sheet on a powered pad." +
        NOT_ASSESSED_TAIL),
    "SBK-TOOL-SB-CAB": (
        "Wood dust is a respiratory sensitiser -- extraction on and dust "
        "mask worn. Never fit a creased or torn belt: it tracks off and "
        "can break at speed." + NOT_ASSESSED_TAIL),
    "CNC-DRILL-BLOCK": (
        "Heavy fixture -- two-person lift onto the bed. Confirm clamping "
        "and re-probe zero before the first cycle." + NOT_ASSESSED_TAIL),
    "CNC-COLLET-ER32": (
        "Isolate the spindle before any collet change. A worn or dirty "
        "collet can release the tool at speed -- replace, do not persist "
        "with it." + NOT_ASSESSED_TAIL),
    "SBK-TOOL-EBH-01": (
        "Hot surfaces and molten adhesive: heat-resistant gloves and eye "
        "protection. Allow the head to cool before servicing." +
        NOT_ASSESSED_TAIL),
}

CUTTING_NOTE = (
    "Sharp carbide edges -- handle by the shank or body, gloves for tool "
    "changes. Isolate the spindle before changing. Never exceed the maximum "
    "rated speed recorded on this product." + NOT_ASSESSED_TAIL)
FASTENER_NOTE = (
    "Eye protection when driving. Dropped fasteners are a slip hazard -- "
    "sweep the bench and floor after assembly." + NOT_ASSESSED_TAIL)

SAFETY_NOTES_BY_FAMILY = {
    "saw_blade": CUTTING_NOTE,
    "cnc_router_bit": CUTTING_NOTE,
    "drill_bit": CUTTING_NOTE,
    "boring_bit": CUTTING_NOTE,
    "screw": FASTENER_NOTE,
    "fastener": FASTENER_NOTE,
    "jig_fixture": (
        "Confirm the fixture is clamped and located before cycling the "
        "machine." + NOT_ASSESSED_TAIL),
    "spare_part": (
        "Isolate the machine before fitting. Confirm the part is rated for "
        "the spindle or assembly it goes into." + NOT_ASSESSED_TAIL),
}

print("")
print("=== PASS 3: safety_notes + remaining classification flags ===")
p3 = 0
for p in Product.search([("x_southbrook_tool_category_id", "!=", False)],
                        order="id"):
    cat = p.x_southbrook_tool_category_id
    vals = {}
    if not p.x_southbrook_safety_notes:
        note = SAFETY_NOTES_BY_CODE.get(p.default_code)
        if not note:
            note = SAFETY_NOTES_BY_FAMILY.get(
                p.x_southbrook_tool_family or cat.tool_family or "")
        if note:
            vals["x_southbrook_safety_notes"] = note
    directness = p.x_southbrook_directness or cat.directness or ""
    family = p.x_southbrook_tool_family or cat.tool_family or ""
    if directness in ("indirect_tool", "indirect_consumable") \
            and not p.x_southbrook_is_indirect_tool:
        vals["x_southbrook_is_indirect_tool"] = True
    if (family == "maintenance_supply" or directness == "maintenance_supply") \
            and not p.x_southbrook_is_maintenance_supply:
        vals["x_southbrook_is_maintenance_supply"] = True
    if vals:
        p.write(vals)
        p3 += len(vals)
        print("%s %-18s %s" % (VERB, p.default_code or p.id,
                               ", ".join(sorted(
                                   f.replace("x_southbrook_", "")
                                   for f in vals))))
    else:
        print("SKIP-ALREADY-SET   %s" % (p.default_code or p.id))
print("PASS3_FIELDS_WRITTEN=%d" % p3)

# ═════════════════════════════════════════════════════════════════════
# Verify -- re-audit facet coverage in the SAME transaction.
# ═════════════════════════════════════════════════════════════════════
print("")
print("=== FACET COVERAGE AFTER ===")
rows = Provider.audit_facet_coverage()
unpop = sorted(r["key"] for r in rows if not r["populated"])
print("TOTAL_FACETS=%d" % len(rows))
print("POPULATED=%d" % (len(rows) - len(unpop)))
print("UNPOPULATED=%d" % len(unpop))
print("---UNPOPULATED-BEGIN---")
for k in unpop:
    print(k)
print("---UNPOPULATED-END---")

# Safety fields must be untouched -- prove it.
SAFETY = ["x_southbrook_hazardous", "x_southbrook_flammable",
          "x_southbrook_requires_ventilation", "x_southbrook_msds_required"]
na = Product.search_count([
    ("x_southbrook_tool_category_id", "!=", False),
    ("x_southbrook_hazardous", "=", "not_assessed")])
print("STILL_NOT_ASSESSED_HAZARDOUS=%d  (expected 24 -- unchanged)" % na)
lc = Product.search_count([
    ("x_southbrook_tool_category_id", "!=", False),
    ("x_southbrook_tool_lifecycle_state", "!=", False)])
print("LIFECYCLE_STATE_SET=%d  (expected 0 -- untouched)" % lc)
vend = Product.search_count([
    ("x_southbrook_tool_category_id", "!=", False),
    ("x_southbrook_preferred_vendor_id", "!=", False)])
print("PREFERRED_VENDOR_SET=%d  (expected 0 -- out of scope)" % vend)

if APPLY:
    env.cr.commit()
    print("")
    print("COMMITTED")
else:
    env.cr.rollback()
    print("")
    print("ROLLED_BACK (dry run -- set SKJ_APPLY=1 to persist)")
