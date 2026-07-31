# SPDX-License-Identifier: LGPL-3.0-only
"""Common-category kitchen cabinet catalog expansion.

Builds ~35 industry-standard kitchen cabinet product templates with full
OCA product_configurator wiring (attribute_lines + value subsets + the
right southbrook_category / icon for the configurator UX badge).

Categories covered:
  - Base cabinets: 1-door, 2-door, 3/4-drawer, sink, cooktop, dishwasher
    panel, microwave drawer, wine rack
  - Corner cabinets: lazy-susan, blind, diagonal
  - Pull-out base: trash, spice, tray divider, mixer lift
  - Wall cabinets: 1-door, 2-door, glass-door, open-shelf, microwave,
    range hood, refrigerator bridge, corner wall
  - Tall cabinets: pantry, pull-out pantry, oven tower, broom/utility,
    refrigerator enclosure
  - Vanity cabinets: 1-door, 2-door, drawer bank
  - Accessories: filler strips, end panels, crown molding, light rail,
    toe kick

This expands the locked-12 template set (CLAUDE.md Q8) to ~47 templates
so the configurator UX can demo against a realistic catalog. The Q8
twelve remain untouched. Each new template carries config_ok=True and
is wired to the same attribute pool as the Q8 base/wall/tall set,
using width subsets appropriate to the cabinet's size range.

Pricing is anchor-only (representative list_price per cabinet at its
middle width). The tactical price_extra deltas in tactical_price_seed.py
still drive LIVE recalc; this file just sets the base.
"""
from odoo import api, models


# =====================================================================
# Width subsets — used to filter which Width values each template offers.
# =====================================================================

# Narrow base/wall — for single-door cabinets and small pulls.
_W_NARROW = ["9 in", "12 in", "15 in", "18 in", "21 in"]
# Wide base/wall — for double-door, sink, drawer banks at upper end.
_W_WIDE = ["24 in", "27 in", "30 in", "33 in", "36 in"]
# Full base/wall range — for accessories and shelves that span the whole catalog.
_W_FULL = ["9 in", "12 in", "15 in", "18 in", "21 in",
           "24 in", "27 in", "30 in", "33 in", "36 in"]
# Drawer bank — 12-30 the typical drawer-stack range.
_W_DRAWER = ["12 in", "15 in", "18 in", "21 in", "24 in", "27 in", "30 in"]
# Spice / tray — sub-narrow.
_W_TINY = ["9 in", "12 in"]
# Sink / cooktop — only wide enough.
_W_SINK = ["30 in", "33 in", "36 in"]
# Corner / wide-corner — large.
_W_CORNER = ["33 in", "36 in"]
# T4 (corner-remediation-2026-07-30, code review finding 1b): SB-CORNER-
# BLIND is a real 45in-wide blind-corner base — southbrook_estimating's
# corner_sku_repair.repair_corner_family_skus() adds "45 in" to the
# shared Width vocabulary and relinks the BLIND variant onto it. Without
# its own width subset here, the reconcile loop below (write-only-if-
# changed, ~line 396) would see this line's actual value_ids (33/36/45)
# differ from the plain _W_CORNER list (33/36) on the very next upgrade
# that touches this module and strip 45in straight back out — silently
# reverting that repair every time (southbrook_configurator_ux depends
# on southbrook_estimating, so it re-upgrades, and this data reloads,
# on every -u that includes the latter). DIAG/LSUSAN stay on the plain
# _W_CORNER list — their Width vocabulary is genuinely just 33/36.
_W_CORNER_BLIND = ["33 in", "36 in", "45 in"]
# Vanity — bath sizes (use same width values).
_W_VANITY_SMALL = ["12 in", "15 in", "18 in", "21 in", "24 in"]
_W_VANITY_LARGE = ["24 in", "27 in", "30 in", "33 in", "36 in"]

# Attribute presence flags — controls which attribute_lines get attached.
# Every doored cabinet gets the full door bundle; drawer banks skip Door
# Style / Hinge / Door Count and add specific drawer attributes (none yet
# in the seed, so we just leave them off).
# --- Audit v1 (2026-06-09) additions ---
# Six attributes apply to every functional cabinet (base / wall / drawer /
# corner / tall / vanity). They land at the end of each tuple so the
# existing customer-facing UI order (family → width → series → finish →
# hardware) is preserved.
_AUDIT_UNIVERSAL = (
    "Frame Style", "Door Overlay", "Wood Species",
    "Pull Finish", "Door Edge Profile", "Lighting",
)

# Two attributes apply only to cabinets that have drawers or interior
# storage compartments — base, drawer-bank, corner, tall, vanity.
_AUDIT_DRAWER_INTERIOR = (
    "Drawer Construction", "Interior Storage",
)

# Two attributes apply only to wall cabinets (glass-door styling + crown
# molding option at the cabinet level).
_AUDIT_WALL_ONLY = (
    "Glass Insert", "Crown Molding",
)

# Crown molding alone applies to tall units too (they touch the ceiling).
_AUDIT_TALL_EXTRA = (
    "Crown Molding",
)

_ATTRS_DOORED = (
    "Family", "Width", "Series", "Box Material", "Door Style",
    "Finish", "Hinge Side", "Finished Sides", "Gables", "Handle",
    "Accessories", "Door Count",
) + _AUDIT_UNIVERSAL + _AUDIT_DRAWER_INTERIOR
_ATTRS_DOORED_WALL = _ATTRS_DOORED + _AUDIT_WALL_ONLY

_ATTRS_DOUBLEDOOR = (
    "Family", "Width", "Series", "Box Material", "Door Style",
    "Finish", "Finished Sides", "Gables", "Handle", "Accessories",
    "Door Count",
) + _AUDIT_UNIVERSAL + _AUDIT_DRAWER_INTERIOR
_ATTRS_DOUBLEDOOR_WALL = _ATTRS_DOUBLEDOOR + _AUDIT_WALL_ONLY

_ATTRS_DRAWER_BANK = (
    "Family", "Width", "Series", "Box Material", "Door Style",
    "Finish", "Finished Sides", "Gables", "Handle", "Accessories",
) + _AUDIT_UNIVERSAL + _AUDIT_DRAWER_INTERIOR

_ATTRS_OPEN_SHELF = (
    "Family", "Width", "Series", "Box Material", "Finish",
    "Finished Sides", "Gables",
) + _AUDIT_UNIVERSAL

_ATTRS_ACCESSORY = (
    "Family", "Series", "Box Material", "Finish",
)

_ATTRS_CORNER = (
    "Family", "Width", "Series", "Box Material", "Door Style",
    "Finish", "Hinge Side", "Gables", "Handle", "Accessories",
) + _AUDIT_UNIVERSAL + _AUDIT_DRAWER_INTERIOR

_ATTRS_TALL = (
    "Family", "Width", "Series", "Box Material", "Door Style",
    "Finish", "Hinge Side", "Finished Sides", "Gables", "Handle",
    "Accessories",
) + _AUDIT_UNIVERSAL + _AUDIT_DRAWER_INTERIOR + _AUDIT_TALL_EXTRA

# Phase 2L (audit 2026-06-09) — wizard step membership map. Each
# step bucket lists the attribute names it owns. Used in the reconcile
# loop to create/update product.config.step.line records on every
# expanded cabinet so the configurator wizard renders the same four
# tabs as the 10 Q8-locked cabinets defined statically in
# southbrook_estimating/data/config_steps.xml.
_AUDIT_STEPS = (
    ("step_construction", 10,
     ("Family", "Width", "Series", "Box Material", "Frame Style",
      "Door Count", "Family Subtype", "Accessory Type")),
    ("step_door_finish", 20,
     ("Door Style", "Door Overlay", "Wood Species", "Finish",
      "Door Edge Profile", "Glass Insert", "Crown Molding")),
    ("step_hardware", 30,
     ("Hinge Side", "Handle", "Pull Finish", "Finished Sides", "Gables")),
    ("step_interior", 40,
     ("Drawer Construction", "Interior Storage", "Lighting", "Accessories")),
)

# Subset overrides — when a cabinet exposes only a specific values from a
# multi-value attribute. Keyed by (sku_default_code, attribute_name) →
# list of value names. Anything not listed gets the full attribute value
# set (filtered by width subset for Width).
_VALUE_SUBSETS = {
    # Single-door cabinets only have Door Count = 1.
    ("SB-BASE-1DR", "Door Count"): ["1"],
    ("SB-WALL-1DR", "Door Count"): ["1"],
    ("SB-VAN-1DR", "Door Count"): ["1"],
    # Double-door: Door Count = 2.
    ("SB-BASE-2DR", "Door Count"): ["2"],
    ("SB-WALL-2DR", "Door Count"): ["2"],
    ("SB-VAN-2DR", "Door Count"): ["2"],
    # Glass door — only premium series + door style.
    ("SB-WALL-GLASS", "Series"): ["Elegance", "Signature"],
    ("SB-WALL-GLASS", "Door Style"): ["Custom (Signature)"],
    # Accessories — use the Accessory family.
    ("SB-ACC-FILLER", "Family"): ["Accessory"],
    ("SB-ACC-ENDPANEL-B", "Family"): ["Accessory"],
    ("SB-ACC-ENDPANEL-T", "Family"): ["Accessory"],
    ("SB-ACC-CROWN", "Family"): ["Accessory"],
    ("SB-ACC-LIGHTRAIL", "Family"): ["Accessory"],
    ("SB-ACC-TOEKICK", "Family"): ["Accessory"],
}


# =====================================================================
# Catalog table — the heart of this module.
#   (default_code, name, family_value_name, category_badge, icon_key,
#    list_price, attribute_keys_tuple, width_subset)
# =====================================================================

_CATALOG = [
    # ----- BASE cabinets -----
    ("SB-BASE-1DR",  "Base Cabinet · Single Door",  "Base",  "Base",  "base1",  295.00, _ATTRS_DOORED,      _W_NARROW),
    ("SB-BASE-2DR",  "Base Cabinet · Double Door",  "Base",  "Base",  "base2",  395.00, _ATTRS_DOUBLEDOOR,  _W_WIDE),
    ("SB-BASE-3DRW", "Base Cabinet · 3-Drawer Stack","Drawer Bank","Drawer","drawer", 475.00, _ATTRS_DRAWER_BANK, _W_DRAWER),
    ("SB-BASE-4DRW", "Base Cabinet · 4-Drawer Pot", "Drawer Bank","Drawer","drawer", 545.00, _ATTRS_DRAWER_BANK, ["18 in", "24 in", "30 in"]),
    ("SB-BASE-SINK", "Sink Base · Single Bowl",     "Sink",  "Base",  "sink",   385.00, _ATTRS_DOUBLEDOOR,  _W_SINK),
    ("SB-BASE-SINK-DBL", "Sink Base · Double Bowl", "Sink",  "Base",  "sink",   445.00, _ATTRS_DOUBLEDOOR,  ["33 in", "36 in"]),
    ("SB-BASE-COOKTOP","Cooktop Base",              "Base",  "Base",  "base2",  365.00, _ATTRS_DOUBLEDOOR,  _W_SINK),
    ("SB-BASE-DISHWASH","Dishwasher End Panel Base","Accessory","Base","base1",185.00, _ATTRS_ACCESSORY,   None),
    ("SB-BASE-MICRO","Microwave Drawer Base",       "Base",  "Base",  "base2",  595.00, _ATTRS_DOUBLEDOOR,  ["30 in"]),
    ("SB-BASE-WINE", "Wine Rack Base",              "Base",  "Base",  "base1",  295.00, _ATTRS_OPEN_SHELF,  ["12 in", "18 in"]),

    # ----- CORNER cabinets -----
    ("SB-CORNER-LSUSAN","Corner Base · Lazy-Susan", "Corner","Base",  "corner", 625.00, _ATTRS_CORNER,      _W_CORNER),
    ("SB-CORNER-BLIND","Corner Base · Blind",       "Corner","Base",  "corner", 545.00, _ATTRS_CORNER,      _W_CORNER_BLIND),
    ("SB-CORNER-DIAG","Corner Base · Diagonal",     "Corner","Base",  "corner", 685.00, _ATTRS_CORNER,      _W_CORNER),

    # ----- PULL-OUT base cabinets -----
    ("SB-BASE-PO-TRASH","Pull-Out Trash Base",      "Base",  "Base",  "base1",  325.00, _ATTRS_DOORED,      ["15 in", "18 in"]),
    ("SB-BASE-PO-SPICE","Pull-Out Spice Base",      "Base",  "Base",  "base1",  185.00, _ATTRS_DOORED,      _W_TINY),
    ("SB-BASE-PO-TRAY","Tray Divider Base",         "Base",  "Base",  "base1",  225.00, _ATTRS_DOORED,      _W_TINY),
    ("SB-BASE-PO-MIXER","Mixer Lift Base",          "Base",  "Base",  "base1",  475.00, _ATTRS_DOORED,      ["15 in", "18 in"]),

    # ----- WALL cabinets -----
    # NF (audit v1): wall entries use _ATTRS_*_WALL so Glass Insert +
    # Crown Molding land on wall cabinets only. SB-WALL-OPEN stays on
    # OPEN_SHELF (no glass/crown on open shelving).
    ("SB-WALL-1DR",  "Wall Cabinet · Single Door",  "Wall",  "Wall",  "wall1",  245.00, _ATTRS_DOORED_WALL,     _W_NARROW),
    ("SB-WALL-2DR",  "Wall Cabinet · Double Door",  "Wall",  "Wall",  "wall2",  325.00, _ATTRS_DOUBLEDOOR_WALL, _W_WIDE),
    ("SB-WALL-GLASS","Wall Cabinet · Glass Door",   "Wall",  "Wall",  "wall2",  445.00, _ATTRS_DOUBLEDOOR_WALL, ["18 in", "24 in", "30 in", "36 in"]),
    ("SB-WALL-OPEN", "Wall Cabinet · Open Shelf",   "Wall",  "Wall",  "wall1",  185.00, _ATTRS_OPEN_SHELF,      _W_FULL),
    ("SB-WALL-MICRO","Wall Microwave Cabinet",      "Wall",  "Wall",  "wall2",  395.00, _ATTRS_DOUBLEDOOR_WALL, ["24 in", "30 in"]),
    ("SB-WALL-RANGEH","Range Hood Wall Cabinet",    "Wall",  "Wall",  "wall2",  365.00, _ATTRS_DOUBLEDOOR_WALL, ["30 in", "36 in"]),
    ("SB-WALL-FRIDGE","Wall Refrigerator Bridge",   "Wall",  "Wall",  "wall2",  285.00, _ATTRS_DOUBLEDOOR_WALL, _W_SINK),
    ("SB-WALL-CORNER","Corner Wall Cabinet",        "Corner","Wall",  "wall1",  295.00, _ATTRS_DOORED_WALL,     ["24 in", "27 in"]),

    # ----- TALL cabinets -----
    ("SB-TALL-PANTRY","Tall Pantry",                "Tall",  "Tall",  "pantry", 895.00, _ATTRS_TALL,        ["18 in", "24 in"]),
    ("SB-TALL-PANTRY-PO","Pull-Out Pantry",         "Tall",  "Tall",  "pantry", 1245.00, _ATTRS_TALL,       ["12 in", "15 in", "18 in"]),
    ("SB-TALL-OVEN", "Oven Tower",                  "Tall",  "Tall",  "oven",   1095.00, _ATTRS_TALL,       ["30 in", "33 in"]),
    ("SB-TALL-BROOM","Tall Broom / Utility",        "Tall",  "Tall",  "pantry", 695.00, _ATTRS_TALL,        ["18 in", "24 in"]),
    ("SB-TALL-FRIDGE","Tall Refrigerator Enclosure","Tall",  "Tall",  "oven",   985.00, _ATTRS_TALL,        ["33 in", "36 in"]),

    # ----- VANITY cabinets -----
    ("SB-VAN-1DR",   "Vanity · Single Door",        "Vanity","Vanity","vanity", 345.00, _ATTRS_DOORED,      _W_VANITY_SMALL),
    ("SB-VAN-2DR",   "Vanity · Double Door",        "Vanity","Vanity","vanity", 475.00, _ATTRS_DOUBLEDOOR,  _W_VANITY_LARGE),
    ("SB-VAN-DRW",   "Vanity · Drawer Bank",        "Vanity","Vanity","vanity", 525.00, _ATTRS_DRAWER_BANK, ["24 in", "30 in"]),

    # ----- ACCESSORIES -----
    ("SB-ACC-FILLER","Filler Strip · 3in",          "Accessory","Extras","extra", 28.00, _ATTRS_ACCESSORY,  None),
    ("SB-ACC-FILLER-6","Filler Strip · 6in",        "Accessory","Extras","extra", 38.00, _ATTRS_ACCESSORY,  None),
    ("SB-ACC-ENDPANEL-B","End Panel · Base 24x34",  "Accessory","Extras","extra", 85.00, _ATTRS_ACCESSORY,  None),
    ("SB-ACC-ENDPANEL-T","End Panel · Tall 24x84",  "Accessory","Extras","extra", 145.00, _ATTRS_ACCESSORY, None),
    ("SB-ACC-CROWN", "Crown Molding · 96in",        "Accessory","Extras","extra", 85.00, _ATTRS_ACCESSORY,  None),
    ("SB-ACC-LIGHTRAIL","Light Rail · 96in",        "Accessory","Extras","extra", 65.00, _ATTRS_ACCESSORY,  None),
    ("SB-ACC-TOEKICK","Toe Kick · 96in",            "Accessory","Extras","extra", 45.00, _ATTRS_ACCESSORY,  None),
]


class CatalogExpansion(models.AbstractModel):
    _name = "southbrook.configurator_ux.catalog_expansion"
    _description = "Common-category kitchen cabinet catalog expansion"

    @api.model
    def build_catalog(self):
        Template = self.env["product.template"]
        Product = self.env["product.product"]
        Attr = self.env["product.attribute"]
        AttrVal = self.env["product.attribute.value"]
        AttrLine = self.env["product.template.attribute.line"]
        Category = self.env["product.category"]

        # Vocab caches.
        attrs_by_name = {a.name: a for a in Attr.search([])}
        vals_by_attr_name = {}
        for v in AttrVal.search([]):
            vals_by_attr_name.setdefault(v.attribute_id.id, {})[v.name] = v

        # Audit Phase 2E (2026-06-09) — attribute xml_id cache.
        # The Phase 2B gating rules in southbrook_estimating reference
        # attribute_lines via xml_id (e.g. attr_line_wall_1dr_frame_style).
        # When this routine creates attribute_lines, we need to register
        # matching xml_ids so the rules can bind. Cache:
        #   attr_id → "attr_<short_name>"  e.g. attr_frame_style → frame_style
        IMD = self.env["ir.model.data"]
        _attr_xmlid_by_id = {}
        for imd in IMD.search([
            ("model", "=", "product.attribute"),
            ("module", "=", "southbrook_estimating"),
            ("name", "=like", "attr_%"),
        ]):
            _attr_xmlid_by_id[imd.res_id] = imd.name.replace("attr_", "", 1)
        # template xml_id cache: sku (default_code) → xml_id
        # (e.g. "SB-WALL-1DR" → "wall_1dr").
        _tmpl_xmlid_by_sku = {}
        for imd in IMD.search([
            ("model", "=", "product.template"),
            ("module", "=", "southbrook_estimating"),
        ]):
            tmpl = Template.browse(imd.res_id)
            if tmpl.exists() and tmpl.default_code:
                _tmpl_xmlid_by_sku[tmpl.default_code] = imd.name
        goods = Category.search([("name", "=", "Goods")], limit=1) or \
                Category.search([], limit=1)
        uom_units = self.env["uom.uom"].search(
            [("name", "=", "Units"), ("active", "=", True)], limit=1)

        created, updated, skipped = 0, 0, 0
        details = []

        for (sku, name, family_val, category_badge, icon_key, price,
             attr_keys, width_subset) in _CATALOG:
            # Look up by VARIANT default_code first, then template
            # default_code, then name as a last fallback.
            # active_test=False throughout (T4 code review finding
            # 1a): a plain, active-only search can't see a template
            # another repair archived on purpose (e.g. SB-CORNER-LSUSAN,
            # retired as a duplicate by corner_sku_repair.
            # repair_corner_family_skus()) — without this, this loop
            # would conclude "doesn't exist yet" and mint a fresh
            # ACTIVE duplicate with a new id on every upgrade, silently
            # un-doing that archive. Since `vals` below never sets
            # "active", finding an archived record here just updates
            # its name/price/etc in place and leaves it archived — it
            # does not resurrect it.
            #
            # Deploy-gate hotfix (2026-07-31, SBK-037-class bug) —
            # variant-first is now the PRIMARY lookup, not a fallback:
            # once ANY cabinet template genuinely has more than one
            # variant (corner_sku_repair.py's RH-twin creation, or
            # repair_cabinet_variant_refs()'s own hygiene split),
            # product.template.default_code correctly blanks (Odoo
            # only mirrors a single variant onto the template field —
            # see product_config_line.py's _resolve_template_sku for
            # the identical fix in that consumer). A template-only
            # search then finds nothing and — because the old code was
            # `A or B or C`, which short-circuits the instant ANY
            # clause returns a non-empty (even WRONG) recordset — never
            # even reaches the name fallback if some OTHER record
            # happens to carry the code on its own variant/template.
            # Live-verified: this is exactly how the deploy gate's
            # duplicate SB-WALL-CORNER template got "adopted" and
            # renamed to the real one's name instead of the real
            # template ever being found. Resolving via the variant
            # first sidesteps the whole blanking trap for every
            # multi-variant template, not just the one that happened
            # to be duplicated.
            ArchiveAwareProduct = Product.with_context(active_test=False)
            ArchiveAwareTemplate = Template.with_context(active_test=False)
            existing_variant = ArchiveAwareProduct.search(
                [("default_code", "=", sku)], limit=1)
            existing = (
                existing_variant.product_tmpl_id if existing_variant else
                ArchiveAwareTemplate.search([("default_code", "=", sku)], limit=1)
                or ArchiveAwareTemplate.search([("name", "=", name)], limit=1)
            )
            # NB: default_code is set in TWO places (template-level AND
            # variant-level) because Odoo's product.template.default_code
            # is a related-store field reading from product.product. With
            # create_variant='dynamic' on these attributes, the variant
            # gets recreated whenever attribute_lines change — and the
            # newly-spawned variant loses any default_code we wrote at
            # template-creation time. So we write it once up-front (so
            # the search-by-default_code lookup keeps working on re-runs)
            # and then again AFTER attribute_lines are wired (so the
            # post-recompute variant carries it through to the public-
            # facing SKU display in the configurator response).
            vals = {
                "name": name,
                "default_code": sku,
                "type": "consu",
                "categ_id": goods.id,
                "uom_id": uom_units.id if uom_units else False,
                "list_price": price,
                "is_published": True,
                "config_ok": True,
                "southbrook_category": category_badge,
                "southbrook_icon_key": icon_key,
            }
            if existing:
                existing.write(vals)
                tmpl = existing
                updated += 1
                # Audit Phase 2H (2026-06-09) — DO NOT unlink existing
                # attribute_lines. The destructive rebuild was the root
                # cause of the gating-rule cascade-delete cycle (see
                # docs/configurator_audit_phase2d_patch.md "Phase 2F
                # binding-cycle finding"). Instead, reconcile by:
                #   1. For each desired (attribute_id, value_subset)
                #      tuple in attr_keys, update existing line OR create.
                #   2. After the loop, delete any line whose attribute_id
                #      is NOT in the desired attr_keys set (true obsolete).
                # This preserves attribute_line IDs across upgrades,
                # which preserves FK references from product.config.line
                # gating rules so they don't CASCADE-delete.
            else:
                tmpl = Template.create(vals)
                created += 1

            # Build the desired attribute_id set so we know what to
            # preserve and what to delete after the reconcile loop.
            desired_attr_ids = set()

            # Wire attribute_lines (Phase 2H: write OR create, not wipe).
            for attr_name in attr_keys:
                attr = attrs_by_name.get(attr_name)
                if not attr:
                    details.append(f"{sku}: attr '{attr_name}' missing")
                    continue
                attr_vals = vals_by_attr_name.get(attr.id, {})
                # Determine which values to attach.
                subset_key = (sku, attr_name)
                if subset_key in _VALUE_SUBSETS:
                    chosen_names = _VALUE_SUBSETS[subset_key]
                elif attr_name == "Width" and width_subset is not None:
                    chosen_names = width_subset
                elif attr_name == "Family":
                    chosen_names = [family_val]
                else:
                    chosen_names = list(attr_vals.keys())
                value_ids = [attr_vals[n].id for n in chosen_names
                             if n in attr_vals]
                if not value_ids:
                    details.append(f"{sku}: no values for {attr_name} "
                                   f"({chosen_names})")
                    continue
                desired_attr_ids.add(attr.id)
                # Phase 2H reconcile: find existing attribute_line for
                # this (template, attribute) pair. Write or create.
                existing_line = AttrLine.search([
                    ("product_tmpl_id", "=", tmpl.id),
                    ("attribute_id", "=", attr.id),
                ], limit=1)
                # Phase 2I (P1) — soft-close default ON for accessories
                # attribute_lines. Industry-standard since ~2015; mirror
                # the same flip applied to the 10 Q8-locked cabinets in
                # southbrook_estimating/data/product_templates.xml so the
                # 24 catalog-expanded cabinets are consistent.
                line_vals = {
                    "product_tmpl_id": tmpl.id,
                    "attribute_id": attr.id,
                    "value_ids": [(6, 0, value_ids)],
                }
                if attr_name == "Accessories":
                    soft_close = attr_vals.get("Soft-Close")
                    if soft_close and soft_close.id in value_ids:
                        line_vals["default_val"] = soft_close.id

                if existing_line:
                    # Only write if value_ids or default_val changed —
                    # avoid touching unchanged lines so dependent records
                    # don't see spurious updates.
                    current_ids = set(existing_line.value_ids.ids)
                    write_vals = {}
                    if current_ids != set(value_ids):
                        write_vals["value_ids"] = [(6, 0, value_ids)]
                    if ("default_val" in line_vals and
                            existing_line.default_val.id != line_vals["default_val"]):
                        write_vals["default_val"] = line_vals["default_val"]
                    if write_vals:
                        existing_line.write(write_vals)
                    line = existing_line
                else:
                    line = AttrLine.create(line_vals)
                # Audit Phase 2E (2026-06-09) — register an ir.model.data
                # xml_id for this attribute_line so the gating rules in
                # southbrook_estimating/data/config_rules.xml can resolve
                # their `attribute_line_id` refs. The xml_id pattern is
                # attr_line_<tmpl_xmlid>_<attr_short> and must match what
                # the rule XML expects.
                tmpl_xmlid = _tmpl_xmlid_by_sku.get(sku)
                attr_short = _attr_xmlid_by_id.get(attr.id)
                if tmpl_xmlid and attr_short:
                    xml_name = "attr_line_%s_%s" % (tmpl_xmlid, attr_short)
                    # search_count avoids duplicate-xml_id errors on idempotent reruns
                    if not IMD.search_count([
                        ("module", "=", "southbrook_estimating"),
                        ("name", "=", xml_name),
                    ]):
                        IMD.create({
                            "module": "southbrook_estimating",
                            "name": xml_name,
                            "model": "product.template.attribute.line",
                            "res_id": line.id,
                            "noupdate": False,
                        })

            # Audit Phase 2H (2026-06-09) — obsolete attribute_line
            # cleanup. After the reconcile loop above, delete any
            # attribute_line on this template whose attribute_id is NOT
            # in the desired attr_keys set. This is the legitimate
            # cleanup: an attribute was removed from the catalog spec
            # for this cabinet family, so its line is obsolete.
            #
            # Crucially, this does NOT touch lines that were already
            # there for current attributes — those got write()-updated
            # above. Preserving their row IDs is what stops the gating
            # rules from getting CASCADE-deleted.
            obsolete = tmpl.attribute_line_ids.filtered(
                lambda l: l.attribute_id.id not in desired_attr_ids
            )
            if obsolete:
                obsolete.unlink()

            # Phase 2L — reconcile product.config.step.line records so
            # the OCA wizard renders this cabinet's attribute_lines in
            # the same four-tab layout as the Q8 cabinets in
            # data/config_steps.xml. Non-destructive: write only when
            # the attribute_line set actually changes; unlink the step
            # binding only when no member attribute applies.
            StepLine = self.env["product.config.step.line"]
            attr_line_by_name = {
                ln.attribute_id.name: ln.id
                for ln in tmpl.attribute_line_ids
            }
            for step_xmlid, step_seq, member_attr_names in _AUDIT_STEPS:
                step = self.env.ref(
                    f"southbrook_estimating.{step_xmlid}",
                    raise_if_not_found=False,
                )
                if not step:
                    continue
                member_ids = [
                    attr_line_by_name[n] for n in member_attr_names
                    if n in attr_line_by_name
                ]
                existing_sl = StepLine.search([
                    ("product_tmpl_id", "=", tmpl.id),
                    ("config_step_id", "=", step.id),
                ], limit=1)
                if not member_ids:
                    if existing_sl:
                        existing_sl.unlink()
                    continue
                if existing_sl:
                    current = set(existing_sl.attribute_line_ids.ids)
                    if current != set(member_ids):
                        existing_sl.write({
                            "attribute_line_ids": [(6, 0, member_ids)],
                        })
                else:
                    StepLine.create({
                        "product_tmpl_id": tmpl.id,
                        "config_step_id": step.id,
                        "attribute_line_ids": [(6, 0, member_ids)],
                        "sequence": step_seq,
                    })

            # POST-attribute-line write of default_code. The variant
            # recompute triggered by attribute_line creation discarded
            # the default_code we set in vals; re-write it now so it
            # survives on the freshly-spawned variant(s). Touch every
            # variant directly since the template-level write doesn't
            # always cascade to all variants when there are multiple.
            tmpl.write({"default_code": sku})
            for variant in tmpl.product_variant_ids:
                if not variant.default_code:
                    variant.default_code = sku

        # After templates are created, the tactical price_extras seed
        # should be re-run so the new PTAVs get their deltas.
        self.env["southbrook.configurator_ux.tactical_seed"
                ].backfill_demo_price_extras()

        # Log result for visibility.
        self.env["ir.logging"].sudo().create({
            "name": "southbrook.configurator_ux.catalog_expansion",
            "type": "server",
            "level": "INFO",
            "dbname": self.env.cr.dbname,
            "message": (f"catalog build: {created} created, {updated} "
                        f"updated. Issues: {len(details)}: "
                        f"{'; '.join(details[:5])}"
                        + ("…" if len(details) > 5 else "")),
            "path": __file__,
            "func": "build_catalog",
            "line": "0",
        })
        return {"created": created, "updated": updated,
                "issues": len(details)}
