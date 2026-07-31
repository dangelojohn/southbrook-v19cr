# SPDX-License-Identifier: LGPL-3.0-only
"""
NF2 override stub for the OCA product_configurator
validate_configuration mechanism (per Build Spec section 9.1).

Upstream status (OCA v19.0.1.0.0):
  - `product.config.session.validate_configuration()` returns a dict
    `{"value": True}` on success or `{"value": False, "reason": str}`
    on rule-blocked. Brief section 2.2 ("rule reason visible to sales rep")
    works with this dict-return today.
  - There is a `# TODO: Raise ConfigurationError with reason` marker at
    `product_configurator/models/product_config.py:1500`. If OCA upstream
    converts the dict-return to a raise-with-reason pattern, this override
    is the swap point.

Current behaviour: this override is a NO-OP — it does not change the
dict-return contract. Its purpose is to RESERVE the override site so
that swapping from dict-return to raise is a single-file change in
southbrook_estimating, NOT a per-call-site fixup across the codebase.

When OCA upstream switches:
  1. Remove the super() call below.
  2. Replace with a wrapper that catches ConfigurationError, builds the
     same {"value": False, "reason": str(e)} dict, and returns it.
  3. Or invert: convert the dict-return into a raise for consumers that
     prefer exception flow.

3D viewport payload (Track 1 — Charter amendment 1):
  Method `get_3d_payload()` reads the session's value_ids, extracts
  W/H/D + family + door_count + finished_sides, calls Phase-1 routine
  #1 (mrp.bom._compute_panel_dimensions) for the cut list, and converts
  the cut list into a per-panel 3D layout for the OWL Three.js viewport.
  Same named constants drive cut list AND geometry — no fork.
"""
from odoo import api, models


class ProductConfigLine(models.Model):
    """Reserved hook for the NF2 override.

    Currently no-op. See module docstring for the swap rationale.
    """
    _inherit = "product.config.line"

    # No methods overridden today. The class declaration itself is what
    # gives southbrook_estimating priority in the inheritance chain when
    # the swap is needed.


class ProductConfigSession(models.Model):
    """Reserved hook for the NF2 validate_configuration override.

    Currently passes through to upstream verbatim. Locks in the override
    site so future swap is a one-file change.
    """
    _inherit = "product.config.session"

    def validate_configuration(
        self, product_tmpl_id=None, value_ids=None, custom_vals=None, final=True
    ):
        """Pass-through. See module docstring."""
        return super().validate_configuration(
            product_tmpl_id=product_tmpl_id,
            value_ids=value_ids,
            custom_vals=custom_vals,
            final=final,
        )

    # ------------------------------------------------------------------
    # 3D viewport payload — Track 1 of Phase 2 charter amendment 1.
    # ------------------------------------------------------------------
    def get_3d_payload(self):
        """Return JSON payload for the OWL cabinet viewport.

        Pipeline:
          1. Read session.value_ids; extract dimensions + family + door_count
             + finished_sides by walking attribute_id → southbrook xml_ids.
          2. Call mrp.bom._compute_panel_dimensions (Phase-1 routine #1) for
             the cut list.
          3. Translate the cut list into per-panel 3D placements.

        Coordinate system (right-handed, mm):
          X — horizontal (left ↔ right; +X is right)
          Y — vertical   (floor ↔ ceiling; +Y is up)
          Z — depth      (back ↔ front; +Z is forward, toward viewer)
        Cabinet origin sits at the centre of the floor footprint, with
        the back of the cabinet on -Z.

        Returns a dict the OWL component consumes verbatim:
          {
            "panels": [{name, dims:{width,height,depth}, pos:{x,y,z}, material?}, ...],
            "metadata": {family, door_count, width_mm, height_mm, depth_mm},
            "camera":   {target:[x,y,z], position:[x,y,z]},
            "bounds":   {min:[...], max:[...]},
          }
        """
        self.ensure_one()
        cab = self._extract_cabinet_inputs()
        cut = self.env["mrp.bom"]._compute_panel_dimensions(
            width_mm=cab["width_mm"],
            height_mm=cab["height_mm"],
            depth_mm=cab["depth_mm"],
            family=cab["family"],
            door_count=cab["door_count"],
            drawer_count=cab["drawer_count"],
            finished_sides=cab["finished_sides"],
        )
        payload = self._cut_list_to_3d_payload(cab, cut)
        # Phase 3 (2026-06-25) — embed current configured price + currency
        # in the payload so the client can show a live read-out next to
        # the cabinet. Defensive: any computation error degrades to
        # null price (toolbar then hides the chip). Breakdown
        # (list_price + extras) goes through too so the client tooltip
        # can show the customer where the total comes from.
        try:
            total = self.get_cfg_price()
            list_price = float(self.product_tmpl_id.list_price or 0.0)
            extras_sum = max(0.0, float(total) - list_price)
            currency = (
                self.pricelist_id.currency_id
                or self.env.company.currency_id
            )
            payload.setdefault("metadata", {})
            payload["metadata"]["price"] = float(total)
            payload["metadata"]["list_price"] = list_price
            payload["metadata"]["extras_sum"] = extras_sum
            payload["metadata"]["currency_symbol"] = currency.symbol or "$"
            payload["metadata"]["currency_position"] = currency.position or "before"
            # 2026-06-25 — pricelist name in the metadata so the
            # client tooltip discloses WHICH price scheme this is.
            # Critical UX for the sales rep / dealer flow — Retail vs
            # Dealer −50% vs Contractor −35% is the single highest-
            # leverage info on the screen.
            payload["metadata"]["pricelist_name"] = (
                self.pricelist_id.display_name
                if self.pricelist_id else ""
            )
            payload["metadata"]["template_code"] = self._resolve_template_sku()
        except Exception:
            payload.setdefault("metadata", {})
            payload["metadata"]["price"] = None
        return payload

    # ---- helpers ----------------------------------------------------

    # ------------------------------------------------------------------
    # SKU → family/dim defaults table.
    #
    # T1C3 (caught at live RPC verification 2026-05-30): a freshly-created
    # session has NO value_ids — the OCA wizard creates the session FIRST
    # and only writes value_ids as the user picks attributes. That left
    # every template defaulting to family="base" in the viewport (since
    # _extract_cabinet_inputs only consulted value_ids).
    #
    # The 12 cabinet template SKUs are locked per Q8 — so a static lookup
    # table is the cheapest way to get correct first-render geometry the
    # moment the user opens the wizard. When a value pick lands, it
    # overrides whatever this table seeded.
    # ------------------------------------------------------------------
    _SKU_DEFAULTS = {
        # SKU prefix         family       door  drawer  W    H     D
        "SB-BASE-1DR":      ("base",      1,    0,      609, 762,  609),
        "SB-BASE-2DR":      ("base",      2,    0,      762, 762,  609),
        "SB-WALL-1DR":      ("wall",      1,    0,      457, 762,  350),
        "SB-WALL-2DR":      ("wall",      2,    0,      762, 762,  350),
        "SB-DRAWER":        ("drawer",    0,    3,      609, 762,  609),
        "SB-SINK-BASE":     ("sink",      2,    0,      762, 762,  609),
        "SB-TALL-PANTRY":   ("tall",      2,    0,      600, 2100, 609),
        "SB-TALL-OVEN":     ("tall",      1,    0,      762, 2100, 609),
        "SB-CORNER":        ("corner",    1,    0,      900, 762,  900),
        "SB-VANITY":        ("vanity",    2,    0,      762, 800,  533),
        "SB-ACCESSORY":     ("accessory", 0,    0,      600, 762,   18),
        "SB-WORKTOP":       ("worktop",   0,    0,     1200,  25,  600),
        # ------------------------------------------------------------
        # Repair Wave 2, Upgrade 2 — live shorthand `default_code`s found
        # on live BoM'd templates that don't match any row above, so
        # `_sb_backfill_geometry()` honestly left them at 0/0/0. Each
        # row below REUSES the exact H/D/family/door/drawer values of
        # its mapped existing row (never invented); only width is
        # overridden where the live product name states a specific
        # width, using this table's own 24"/30" convention (609/762,
        # same values as attr_width's value_mm for 24in/30in above —
        # see product_product.py's "24-36in (~609-914mm)" door-count
        # comment for the same 24in=609mm anchor).
        # ------------------------------------------------------------
        # B24 "B24 Base Cabinet" -> SB-BASE-2DR, width=24in.
        "B24":              ("base",      2,    0,      609, 762,  609),
        # DB24 "DB24 3-Drawer Base" -> SB-DRAWER, width=24in (already
        # SB-DRAWER's own width; kept explicit per the live mapping).
        "DB24":             ("drawer",    0,    3,      609, 762,  609),
        # SB-BASE-3DRW "Base Cabinet - 3-Drawer Stack" -> SB-DRAWER,
        # width left at SB-DRAWER's own table width (no width override
        # given for this code).
        "SB-BASE-3DRW":     ("drawer",    0,    3,      609, 762,  609),
        # SB30 "SB30 Sink Base 30in" -> SB-SINK-BASE, width=30in
        # (already SB-SINK-BASE's own width; kept explicit).
        "SB30":             ("sink",      2,    0,      762, 762,  609),
        # T24 "T24 Tall Pantry Cabinet" -> SB-TALL-PANTRY, width=24in.
        "T24":              ("tall",      2,    0,      609, 2100, 609),
        # W24 "W24 Wall Cabinet" -> SB-WALL-2DR, width=24in.
        "W24":              ("wall",      2,    0,      609, 762,  350),
        # W24-2 "W24-2 Wall Cabinet 2 Door" -> SB-WALL-2DR, width=24in.
        "W24-2":            ("wall",      2,    0,      609, 762,  350),
        # FP3 "FP3 Filler Panel 3in" -- DELIBERATELY EXCLUDED. A filler
        # panel has no carcass (no family/door/drawer/H/D to honestly
        # assign); per the honesty contract this code is left OUT of
        # the table so `_sb_backfill_geometry()` continues to leave it
        # at 0/0/0 rather than fabricate carcass dimensions for a part
        # that isn't a cabinet.
    }

    def _resolve_template_sku(self, tmpl=None):
        """Best-effort recovery of the SKU prefix `_SKU_DEFAULTS` is
        keyed on, for a (possibly multi-variant) cabinet template.

        Task 23 (SBK-037) review finding — `product.template.
        default_code` is a stored compute that only mirrors a SINGLE
        variant's code onto the template (product/models/
        product_template.py's `_compute_template_field_from_variant_
        field`; see `_SKU_DEFAULTS`' own docstring for why the
        pre-repair catalogue relied on this). Once the catalogue-
        hygiene repair (`corner_sku_repair.py::
        repair_cabinet_variant_refs`) splits a duplicate/empty variant
        ref into "<base>-<suffix>" codes, a genuinely multi-variant
        template's OWN `default_code` correctly reads blank — falling
        straight through to the hard defaults at that point silently
        mis-renders (wrong family/door-count/dims) for every
        multi-variant Q8 template split by that repair. This is the
        exact class of bug the SKU-defaults table exists to prevent.

        Single-variant behaviour is untouched: when `tmpl.default_code`
        is truthy it is returned as-is, byte-identical to the prior
        (pre-Task-23) behaviour. Only when it is falsy does this fall
        back to matching each active variant's own `default_code`
        against the KNOWN `_SKU_DEFAULTS` keys — exact match first
        (checked across every variant before any prefix match is
        tried, since a key can itself contain a "-", e.g. "W24-2" —
        prefix-matching that against the unrelated "W24" key first
        would misfire), then the repair's own "<key>-<suffix>" prefix
        convention, keys tried **longest first** (review finding, item
        2): "W24" is itself a dash-prefix of the distinct key "W24-2",
        so a hypothetical repaired variant coded e.g. "W24-2-30" must
        match the more specific "W24-2" before the shorter "W24" ever
        gets a chance, regardless of dict insertion order. `_SKU_
        DEFAULTS` is a small, closed, known table, so this reverse
        lookup — not a generic string-split heuristic — is what stays
        safe as the table grows.
        """
        tmpl = tmpl or self.product_tmpl_id
        if not tmpl:
            return ""
        if tmpl.default_code:
            return tmpl.default_code
        variant_codes = [
            c for c in tmpl.product_variant_ids.sorted("id").mapped("default_code")
            if c
        ]
        for variant_code in variant_codes:
            if variant_code in self._SKU_DEFAULTS:
                return variant_code
        longest_first_keys = sorted(
            self._SKU_DEFAULTS, key=len, reverse=True)
        for variant_code in variant_codes:
            for key in longest_first_keys:
                if variant_code.startswith(key + "-"):
                    return key
        return ""

    def _extract_cabinet_inputs(self):
        """Walk product_tmpl_id default_code + session.value_ids → cabinet inputs.

        Order of precedence (lowest first):
          1. Hard defaults (covers the rare "no template yet" edge).
          2. SKU lookup from product_tmpl_id.default_code — seeds the
             initial wizard render with the correct family + door/drawer
             count + plausible dimensions for the locked Q8 templates.
          3. Per-attribute picks on session.value_ids — these override
             whatever the SKU seeded as the user makes choices.

        Finding I-1 (materials geometry-writeback final review, 2026-07-24):
        this method ALWAYS returns non-zero width/height/depth (the hard
        defaults at step 1 guarantee that), so callers cannot distinguish
        "the viewport needs SOME geometry to render, even if guessed" (the
        3D-payload use case this method was originally built for) from "we
        genuinely resolved real geometry for this variant" (what A2's
        variant-materialisation write needs, per the honesty contract).

        The returned dict now carries an extra `"_geo_resolved"` bool
        signal (additive — every existing key + the return type are
        unchanged, so `get_3d_payload()` / `_cut_list_to_3d_payload()` and
        any other existing caller keep working with zero changes). It is
        True when EITHER:
          - the SKU lookup (step 2) hit a real `_SKU_DEFAULTS` row, OR
          - a real `attr_width` pick with a non-zero `value_mm` was found
            on `session.value_ids` (step 3).
        It is False when the dict is built ENTIRELY from the hard
        defaults at step 1 — i.e. an unknown/non-cabinet template with no
        width pick. Callers that need to render *something* (the 3D
        viewport) can ignore the flag and keep using the guessed hard
        defaults; callers that must never fabricate stored data (A2's
        `get_variant_vals` writeback) gate on it.
        """
        ref = self.env.ref
        resolved = False
        # 1. Hard defaults.
        out = {
            "width_mm": 609,
            "height_mm": 762,
            "depth_mm": 609,
            "family": "base",
            "door_count": 1,
            "drawer_count": 0,
            "finished_sides": "none",
            # Phase 2 (2026-06-24): door style + handle picks. Default
            # to "slab" + "none" so legacy cabinets without these
            # attributes render exactly as before (no regression).
            "door_style": "slab",
            "handle": "none",
            # Phase 2 Round 2.5 (2026-06-24): Pull Finish drives the
            # hardware material color on the client. Default falls back
            # to the generic "hardware" (brushed-nickel) material when
            # no pick is set.
            "pull_finish": "",
            # Phase 2 Round 3 (2026-06-24): crown molding profile.
            # "none" = no crown. Other values map to crown heights:
            # simple=38mm (1.5in cove), ogee=76mm (3in), stacked=114mm,
            # dental=76mm (3in dental). Emitted only on wall + tall.
            "crown_molding": "none",
            # Phase 2 Round 4 (2026-06-24): Finish drives the door
            # material color + roughness. Slug values: "white",
            # "maple_stain", "cherry_stain", "walnut_stain", "custom".
            # Empty string = no pick → falls back to the generic "door"
            # material (default walnut).
            "finish": "",
        }
        # 2. SKU lookup. _resolve_template_sku() falls back to a
        # variant-code reverse lookup when the template-level
        # default_code is blank (multi-variant template, post-Task-23
        # catalogue-hygiene repair) — see its docstring.
        sku = self._resolve_template_sku() if self.product_tmpl_id else ""
        sku_row = self._SKU_DEFAULTS.get(sku)
        if sku_row:
            fam, doors, drawers, w, h, d = sku_row
            out["family"] = fam
            out["door_count"] = doors
            out["drawer_count"] = drawers
            out["width_mm"] = w
            out["height_mm"] = h
            out["depth_mm"] = d
            resolved = True

        def attr_xml(name):
            return ref(f"southbrook_estimating.{name}", raise_if_not_found=False)

        attr_width = attr_xml("attr_width")
        attr_height = attr_xml("attr_height")
        attr_depth = attr_xml("attr_depth")
        attr_family = attr_xml("attr_family")
        attr_door_count = attr_xml("attr_door_count")
        attr_finished_sides = attr_xml("attr_finished_sides")
        attr_door_style = attr_xml("attr_door_style")
        attr_handle = attr_xml("attr_handle")
        attr_pull_finish = attr_xml("attr_pull_finish")
        attr_crown_molding = attr_xml("attr_crown_molding")
        attr_finish = attr_xml("attr_finish")

        for val in self.value_ids:
            attr = val.attribute_id
            if attr_width and attr == attr_width and val.value_mm:
                out["width_mm"] = val.value_mm
                # I-1: a real attr_width pick with a real value_mm is a
                # genuine geometry signal on its own, independent of
                # whether the SKU table also matched.
                resolved = True
            elif attr_height and attr == attr_height and val.value_mm:
                out["height_mm"] = val.value_mm
            elif attr_depth and attr == attr_depth and val.value_mm:
                out["depth_mm"] = val.value_mm
            elif attr_family and attr == attr_family:
                out["family"] = (val.name or "base").lower().split()[0]
            elif attr_door_count and attr == attr_door_count:
                # Value names look like "1 Door", "2 Doors", "1", "2".
                first_token = (val.name or "1").strip().split()[0]
                try:
                    out["door_count"] = int(first_token)
                except ValueError:
                    pass
            elif attr_finished_sides and attr == attr_finished_sides:
                out["finished_sides"] = (val.name or "none").lower()
            elif attr_door_style and attr == attr_door_style:
                # Door Style values today: "Thermofoil Slab — White",
                # "Five-Piece Woodgrain", "Custom (Signature)". Normalize
                # via substring match — Phase 2 geometry only branches
                # on slab vs five-piece; custom renders as slab pending
                # a canonical signature-door profile.
                name = (val.name or "").lower()
                if "five" in name or "shaker" in name:
                    out["door_style"] = "five_piece"
                elif "slab" in name or "thermofoil" in name:
                    out["door_style"] = "slab"
                else:
                    out["door_style"] = "slab"
            elif attr_handle and attr == attr_handle:
                # Handle values today: "Bar Pull", "Knob", "Cup Pull",
                # "Integrated", "None". Order matters in the substring
                # check (Cup before Bar because "Cup Pull" contains
                # "pull" but not "bar").
                name = (val.name or "").lower()
                if "cup" in name:
                    out["handle"] = "cup_pull"
                elif "bar" in name:
                    out["handle"] = "bar_pull"
                elif "knob" in name:
                    out["handle"] = "knob"
                elif "integrated" in name:
                    out["handle"] = "integrated"
                else:
                    out["handle"] = "none"
            elif attr_crown_molding and attr == attr_crown_molding:
                # Crown molding values: "None", "Simple Cove (1.5 in)",
                # "Ogee (3 in)", "Stacked Two-Tier (4.5 in)",
                # "Dental (Traditional)". Normalize to short keys.
                name = (val.name or "").lower()
                if "simple" in name or "cove" in name:
                    out["crown_molding"] = "simple"
                elif "ogee" in name:
                    out["crown_molding"] = "ogee"
                elif "stacked" in name:
                    out["crown_molding"] = "stacked"
                elif "dental" in name:
                    out["crown_molding"] = "dental"
                else:
                    out["crown_molding"] = "none"
            elif attr_finish and attr == attr_finish:
                # Finish values: "White", "Maple Stain", "Cherry Stain",
                # "Walnut Stain", "Custom". Normalize to slug.
                name = (val.name or "").lower()
                if "white" in name:
                    out["finish"] = "white"
                elif "maple" in name:
                    out["finish"] = "maple_stain"
                elif "cherry" in name:
                    out["finish"] = "cherry_stain"
                elif "walnut" in name:
                    out["finish"] = "walnut_stain"
                else:
                    out["finish"] = "custom"
            elif attr_pull_finish and attr == attr_pull_finish:
                # Phase 2 Round 2.5 — pull-finish key maps to the
                # client-registered material name. Slugify the value
                # name: "Polished Nickel" → "polished_nickel" → material
                # "hardware_polished_nickel". The 8 known finishes
                # (polished_nickel, brushed_nickel, matte_black,
                # antique_bronze, brushed_brass, polished_chrome,
                # oil_rubbed_bronze, champagne_bronze) are pre-
                # registered on the client; unknown keys fall back to
                # the generic "hardware" material.
                slug = (val.name or "").lower().strip()
                slug = (
                    slug.replace("—", " ")
                        .replace("–", " ")
                        .replace("-", " ")
                )
                slug = "_".join(slug.split())
                out["pull_finish"] = slug
        out["_geo_resolved"] = resolved
        return out

    @api.model
    def _cut_list_to_3d_payload(self, cab, cut):
        """Translate Phase-1 cut list into 3D panel placements.

        Geometric constants (BOX_TH, BACK_TH, RABBET, DOOR_TH, DOOR_REVEAL,
        TOEKICK_H, TOEKICK_FAMILIES) intentionally re-imported from mrp_bom
        module so the same named constants drive both cut list and 3D
        placement. If a future workbook update changes BOX_TH from 15.875mm
        to something else, both layers update without divergence.

        Track 1 commit 3 family dispatch:
          • worktop   — short-circuit slab (no carcass)
          • accessory — short-circuit end panel (no carcass)
          • drawer    — carcass + N drawer fronts instead of doors
          • toekick families (base/sink/tall/vanity) — carcass elevated
            by TOEKICK_H + toe-kick face panel at the floor
          • everything else — carcass + doors (Phase-1 behaviour)
        """
        from . import mrp_bom as _mb

        BOX_TH = _mb.BOX_TH
        BACK_TH = _mb.BACK_TH
        RABBET = _mb.RABBET
        DOOR_TH = _mb.DOOR_TH
        DOOR_REVEAL = _mb.DOOR_REVEAL
        TOEKICK_H = _mb.TOEKICK_H
        TOEKICK_FAMILIES = _mb.TOEKICK_FAMILIES

        W = cab["width_mm"]
        H = cab["height_mm"]
        D = cab["depth_mm"]
        door_count = cab["door_count"]
        family = cab["family"]

        # ------------------------------------------------------------------
        # Short-circuit families: no carcass, just a single slab.
        # ------------------------------------------------------------------
        if family == "worktop":
            return self._3d_payload_worktop(W, H, D)
        if family == "accessory":
            return self._3d_payload_accessory(W, H, D, BOX_TH)

        # ------------------------------------------------------------------
        # Carcass families (base / wall / sink / tall / vanity / drawer / corner).
        # Toe-kick families lift the carcass off the floor and add a recessed
        # face panel at the front-bottom. Wall + corner sit on the cabinet's
        # own bottom — no toe-kick.
        # ------------------------------------------------------------------
        has_toekick = family in TOEKICK_FAMILIES
        y0 = TOEKICK_H if has_toekick else 0   # carcass bottom y-offset
        inside_w = W - 2 * BOX_TH

        panels = []

        # ---- Sides: vertical, BOX_TH thick. For toekick families the
        #      side panels extend the full visible height (door + toekick);
        #      we approximate by keeping sides at H tall and lifting them.
        #
        # Phase 2 (2026-06-24) — Finished Sides material swap.
        # Default = carcass (matches interior plywood). When the
        # customer picks Left / Right / Both for the Finished Sides
        # attribute, the corresponding side panel(s) render with the
        # door material so the exposed face reads as matched cabinetry
        # instead of raw construction grade.
        finished_sides = (cab.get("finished_sides") or "none").lower()
        # Phase 2 Round 4 (2026-06-24): resolve door_mat from the
        # picked Finish slug. Used for finished sides, crown molding,
        # all door faces, drawer fronts, and Shaker frame rails —
        # every wood surface the customer sees. Unknown / empty
        # finish falls back to "door" (the generic walnut material).
        _KNOWN_FINISHES = {
            "white", "maple_stain", "cherry_stain", "walnut_stain",
        }
        finish_slug = (cab.get("finish") or "").lower()
        door_mat = (
            f"door_{finish_slug}"
            if finish_slug in _KNOWN_FINISHES
            else "door"
        )
        side_L_mat = door_mat if finished_sides in ("left", "both") else "carcass"
        side_R_mat = door_mat if finished_sides in ("right", "both") else "carcass"
        panels.append({
            "name": "side_L",
            "dims": {"width": BOX_TH, "height": H, "depth": D},
            "pos":  {"x": -(W - BOX_TH) / 2, "y": y0 + H / 2, "z": -D / 2},
            "material": side_L_mat,
        })
        panels.append({
            "name": "side_R",
            "dims": {"width": BOX_TH, "height": H, "depth": D},
            "pos":  {"x": (W - BOX_TH) / 2, "y": y0 + H / 2, "z": -D / 2},
            "material": side_R_mat,
        })

        # ---- Top + bottom: horizontal, captured between sides.
        panels.append({
            "name": "top",
            "dims": {"width": inside_w, "height": BOX_TH, "depth": D},
            "pos":  {"x": 0, "y": y0 + H - BOX_TH / 2, "z": -D / 2},
        })
        panels.append({
            "name": "bottom",
            "dims": {"width": inside_w, "height": BOX_TH, "depth": D},
            "pos":  {"x": 0, "y": y0 + BOX_TH / 2, "z": -D / 2},
        })

        # ---- Back panel: rabbet-captured.
        back_w = inside_w + 2 * RABBET
        back_h = (H - 2 * BOX_TH) + 2 * RABBET
        panels.append({
            "name": "back",
            "dims": {"width": back_w, "height": back_h, "depth": BACK_TH},
            "pos":  {"x": 0, "y": y0 + H / 2, "z": -D + BACK_TH / 2},
            "material": "back",
        })

        # ---- Shelves (1, 2, or 3) — evenly spaced inside the cavity.
        shelf = cut.get("shelf")
        shelf_count = cut.get("shelf_count", 0)
        if shelf is not None and shelf_count > 0:
            interior_h = H - 2 * BOX_TH
            spacing = interior_h / (shelf_count + 1)
            for i in range(shelf_count):
                shelf_y = y0 + BOX_TH + spacing * (i + 1)
                panels.append({
                    "name": f"shelf_{i + 1}",
                    "dims": {
                        "width": shelf[0],
                        "height": BOX_TH,
                        "depth": shelf[1],
                    },
                    "pos":  {"x": 0, "y": shelf_y, "z": -(D + BACK_TH) / 2 + 6},
                    "material": "shelf",
                })

        # ---- Toe-kick face panel: recessed ~30mm from the door plane,
        #      sits between the floor and the carcass bottom.
        if has_toekick:
            panels.append({
                "name": "toekick",
                "dims": {"width": inside_w, "height": TOEKICK_H, "depth": 18},
                "pos":  {"x": 0, "y": TOEKICK_H / 2, "z": DOOR_TH - 30},
                "material": "toekick",
            })
            self._emit_base_feet(panels, W, D, TOEKICK_H, BOX_TH)

        # ---- Door OR drawer-front stack, depending on family.
        # door_mat resolved above (alongside the finished-sides logic).
        door_style = cab.get("door_style", "slab")
        handle = cab.get("handle", "none")
        pull_finish = cab.get("pull_finish") or ""
        if family == "drawer":
            n_drawers = cab["drawer_count"] or door_count or 3
            self._emit_drawer_fronts(
                panels, W, H, y0, DOOR_TH, DOOR_REVEAL,
                drawer_count=n_drawers,
                door_style=door_style, handle=handle,
                pull_finish=pull_finish, door_mat=door_mat,
            )
            self._emit_drawer_rails(
                panels, W, H, D, y0, DOOR_TH, DOOR_REVEAL,
                BOX_TH, BACK_TH, drawer_count=n_drawers,
            )
        else:
            self._emit_doors(panels, W, H, y0, DOOR_TH, DOOR_REVEAL, door_count,
                             door_style=door_style, handle=handle,
                             pull_finish=pull_finish, door_mat=door_mat)

        # ---- Crown molding — wall + tall families only. Sits on top of
        #      the carcass, overhangs sides (10mm each) and front (20mm).
        #      Back stays flush against the wall. Material = door so the
        #      crown reads as matching the cabinet's visible finish.
        crown_molding = (cab.get("crown_molding") or "none").lower()
        if crown_molding != "none" and family in ("wall", "tall"):
            CROWN_HEIGHTS = {
                "simple": 38,    # 1.5 in cove
                "ogee": 76,      # 3 in
                "stacked": 114,  # 4.5 in two-tier
                "dental": 76,    # 3 in dental
            }
            crown_h = CROWN_HEIGHTS.get(crown_molding, 50)
            SIDE_OVERHANG = 10
            FRONT_OVERHANG = 20
            crown_w = W + 2 * SIDE_OVERHANG
            crown_d = D + FRONT_OVERHANG
            # Centered over the cabinet but shifted forward by half the
            # front overhang so the back stays flush at z = -D.
            crown_z = -D / 2 + FRONT_OVERHANG / 2
            crown_y = y0 + H + crown_h / 2
            panels.append({
                "name": "crown_molding",
                "dims": {"width": crown_w, "height": crown_h, "depth": crown_d},
                "pos": {"x": 0, "y": crown_y, "z": crown_z},
                "material": door_mat,
            })
            # Lift the camera framing so the crown is in frame.
            H = H + crown_h

        # ---- Camera framing — 3/4 view, slightly elevated; include the
        #      toe-kick in the framing height for base/tall/sink/vanity.
        total_h = H + y0
        cam_position = [W * 1.4, total_h * 1.25, D * 1.8]
        cam_target = [0, total_h / 2, -D / 2]

        return {
            "panels": panels,
            "metadata": {
                "family": cab["family"],
                "door_count": door_count,
                "width_mm": W,
                "height_mm": H,
                "depth_mm": D,
            },
            "camera": {"target": cam_target, "position": cam_position},
            "bounds": {
                "min": [-W / 2, 0, -D],
                "max": [W / 2, total_h, DOOR_TH + DOOR_REVEAL],
            },
        }

    # ------------------------------------------------------------------
    # _3d_payload helpers — family-specific geometry emitters (commit 3).
    # ------------------------------------------------------------------

    @api.model
    def _3d_payload_worktop(self, W, H, D):
        """Short-circuit payload for the worktop family: a single slab.

        Worktops are countertop slabs, not carcasses. Phase-1 simplified
        worktop_thickness to 25mm — when the canonical workbook lands and
        Caesarstone / quartz / butcher-block thicknesses diverge, this
        becomes attribute-driven.
        """
        worktop_th = 25
        return {
            "panels": [{
                "name": "worktop_slab",
                "dims": {"width": W, "height": worktop_th, "depth": D},
                "pos":  {"x": 0, "y": worktop_th / 2, "z": -D / 2},
                "material": "worktop",
            }],
            "metadata": {
                "family": "worktop",
                "door_count": 0,
                "width_mm": W,
                "height_mm": worktop_th,
                "depth_mm": D,
            },
            "camera": {
                "target":   [0, worktop_th / 2, -D / 2],
                "position": [W * 0.8, W * 0.5, D * 1.5],
            },
            "bounds": {
                "min": [-W / 2, 0, -D],
                "max": [W / 2, worktop_th, 0],
            },
        }

    @api.model
    def _3d_payload_accessory(self, W, H, D, BOX_TH):
        """Short-circuit payload for accessory family: a single flat panel.

        Accessory_type sub-attribute (end_panel / filler / cornice / pelmet
        / plinth, per Q8 spec) tells us which shape to emit. Phase 1 ships
        the end_panel variant only — the others land in Phase 3 polish.
        """
        return {
            "panels": [{
                "name": "end_panel",
                "dims": {"width": BOX_TH, "height": H, "depth": D},
                "pos":  {"x": 0, "y": H / 2, "z": -D / 2},
                "material": "carcass",
            }],
            "metadata": {
                "family": "accessory",
                "door_count": 0,
                "width_mm": W,
                "height_mm": H,
                "depth_mm": D,
            },
            "camera": {
                "target":   [0, H / 2, -D / 2],
                "position": [W * 2, H * 1.2, D * 1.8],
            },
            "bounds": {
                "min": [-BOX_TH / 2, 0, -D],
                "max": [BOX_TH / 2, H, 0],
            },
        }

    @api.model
    def _emit_doors(self, panels, W, H, y0, DOOR_TH, DOOR_REVEAL, door_count,
                    door_style="slab", handle="none", pull_finish="",
                    door_mat="door"):
        """Append the door panel(s) for a non-drawer carcass.

        Phase-1 NF14 conventions:
          • 1-door: door spans (W − 2*DOOR_REVEAL) × (H − 2*DOOR_REVEAL).
          • 2-door: each leaf spans ((W − 3*DOOR_REVEAL)/2) × (H − 2*DOOR_REVEAL),
            with a centre reveal of DOOR_REVEAL between them.

        Phase-2 (2026-06-24) — `door_style` + `handle` elaboration.
        Slab = current single-quad behaviour. Five-piece adds a Shaker
        frame extrusion (4 rails protruding ~6mm in front of the inset
        panel). Handle dispatch via _emit_handle (drawer=False).
        """
        if door_count == 1:
            self._emit_single_door_face(
                panels, x=0, y=y0 + H / 2,
                z=DOOR_TH / 2 + DOOR_REVEAL,
                w=W - 2 * DOOR_REVEAL, h=H - 2 * DOOR_REVEAL,
                DOOR_TH=DOOR_TH, door_style=door_style,
                name_prefix="door", door_mat=door_mat,
            )
            self._emit_handle(
                panels, x=0, y=y0 + H / 2,
                z=DOOR_TH + DOOR_REVEAL + 6,
                face_w=W - 2 * DOOR_REVEAL, face_h=H - 2 * DOOR_REVEAL,
                handle=handle, on_drawer=False, name_prefix="door_handle",
                pull_finish=pull_finish,
            )
        elif door_count == 2:
            half_w = (W - 3 * DOOR_REVEAL) / 2
            for idx, sign in enumerate((-1, 1), start=1):
                cx = sign * (half_w / 2 + DOOR_REVEAL / 2)
                self._emit_single_door_face(
                    panels, x=cx, y=y0 + H / 2,
                    z=DOOR_TH / 2 + DOOR_REVEAL,
                    w=half_w, h=H - 2 * DOOR_REVEAL,
                    DOOR_TH=DOOR_TH, door_style=door_style,
                    name_prefix=f"door_{idx}", door_mat=door_mat,
                )
                self._emit_handle(
                    panels, x=cx, y=y0 + H / 2,
                    z=DOOR_TH + DOOR_REVEAL + 6,
                    face_w=half_w, face_h=H - 2 * DOOR_REVEAL,
                    handle=handle, on_drawer=False,
                    name_prefix=f"door_{idx}_handle",
                    pull_finish=pull_finish,
                )

    @api.model
    def _emit_single_door_face(self, panels, x, y, z, w, h,
                               DOOR_TH, door_style, name_prefix,
                               door_mat="door"):
        """Emit one door's face geometry — slab or shaker frame.

        Slab: a single full-area panel (current behaviour).
        Five-piece (Shaker): 1 inset panel at door depth + 4 frame
        rails (top, bottom, left, right) protruding ~PROTRUSION in
        front of the panel, each rail FRAME wide. The center panel
        sits at the same z as the slab would; the 4 rails sit at
        z + PROTRUSION/2 so they read as Shaker frame from any
        camera angle.
        """
        if door_style != "five_piece":
            panels.append({
                "name": name_prefix,
                "dims": {"width": w, "height": h, "depth": DOOR_TH},
                "pos": {"x": x, "y": y, "z": z},
                "material": door_mat,
            })
            return
        FRAME = 60        # Rail width — typical Shaker stile/rail
        PROTRUSION = 4    # Rails protrude this far in front of panel
        if w <= 2 * FRAME or h <= 2 * FRAME:
            # Too small for a frame extrusion — fall back to slab so
            # geometry stays sane on small accessory doors.
            panels.append({
                "name": name_prefix,
                "dims": {"width": w, "height": h, "depth": DOOR_TH},
                "pos": {"x": x, "y": y, "z": z},
                "material": door_mat,
            })
            return
        # Inset center panel at base depth
        panels.append({
            "name": f"{name_prefix}_panel",
            "dims": {"width": w, "height": h, "depth": DOOR_TH},
            "pos": {"x": x, "y": y, "z": z},
            "material": door_mat,
        })
        rail_z = z + PROTRUSION / 2
        rail_depth = DOOR_TH + PROTRUSION
        # Top + bottom rails span full width
        panels.append({
            "name": f"{name_prefix}_rail_top",
            "dims": {"width": w, "height": FRAME, "depth": rail_depth},
            "pos": {"x": x, "y": y + h / 2 - FRAME / 2, "z": rail_z},
            "material": door_mat,
        })
        panels.append({
            "name": f"{name_prefix}_rail_bottom",
            "dims": {"width": w, "height": FRAME, "depth": rail_depth},
            "pos": {"x": x, "y": y - h / 2 + FRAME / 2, "z": rail_z},
            "material": door_mat,
        })
        # Left + right stiles span the inset (between rails)
        stile_h = h - 2 * FRAME
        panels.append({
            "name": f"{name_prefix}_stile_L",
            "dims": {"width": FRAME, "height": stile_h, "depth": rail_depth},
            "pos": {"x": x - w / 2 + FRAME / 2, "y": y, "z": rail_z},
            "material": door_mat,
        })
        panels.append({
            "name": f"{name_prefix}_stile_R",
            "dims": {"width": FRAME, "height": stile_h, "depth": rail_depth},
            "pos": {"x": x + w / 2 - FRAME / 2, "y": y, "z": rail_z},
            "material": door_mat,
        })

    @api.model
    def _emit_handle(self, panels, x, y, z, face_w, face_h,
                     handle, on_drawer, name_prefix, pull_finish=""):
        """Emit a handle mesh in front of a door or drawer face.

        Round 1 approximation: all handles are thin boxes (no cylinder
        or sphere geometry yet — keeps client-side dispatcher simple).
        Round 2 may swap to proper CylinderGeometry / SphereGeometry
        when the client gains a `shape` dispatcher.

        Positioning conventions:
          • Doors: handle centered horizontally on the door, ~80% up
            (closer to the top for vertical Bar Pulls; upper third
            for Knobs). Center-x doesn't depend on hinge side because
            the door is typically not so wide that this matters
            visually at the configurator scale.
          • Drawers: handle centered horizontally, near the top of the
            drawer front.
        """
        if handle in ("none", "integrated"):
            return
        # Phase 2 Round 2.5: resolve hardware material name from
        # the pull_finish slug. Known finishes get a dedicated
        # client material (color + roughness/metalness tuned per
        # finish family); unknown/empty fall back to the generic
        # brushed-nickel "hardware" material so legacy panels and
        # cabinets without a Pull Finish pick still render.
        _KNOWN_FINISHES = {
            "polished_nickel", "brushed_nickel", "matte_black",
            "antique_bronze", "brushed_brass", "polished_chrome",
            "oil_rubbed_bronze", "champagne_bronze",
        }
        hw_mat = (
            f"hardware_{pull_finish}"
            if pull_finish in _KNOWN_FINISHES
            else "hardware"
        )
        if handle == "bar_pull":
            # Phase 2 Round 2 (2026-06-24): emit as cylinder shape.
            # The client interprets `shape: cylinder` + `axis` to pick
            # length and radius from dims:
            #   axis="x" → length=width,  radius=min(height,depth)/2
            #   axis="y" → length=height, radius=min(width,depth)/2
            # 18mm diameter is a common cabinet bar-pull size.
            DIAM = 18
            STAND_OFF = 25  # how far the bar stands off the face
            if on_drawer:
                bar_len = min(face_w * 0.5, 200)
                if bar_len < 30:
                    return
                hy = y + face_h / 2 - face_h * 0.15
                panels.append({
                    "name": name_prefix,
                    "shape": "cylinder", "axis": "x",
                    "dims": {"width": bar_len, "height": DIAM, "depth": DIAM},
                    "pos": {"x": x, "y": hy, "z": z + STAND_OFF / 2},
                    "material": hw_mat,
                })
            else:
                bar_len = min(face_h * 0.35, 200)
                if bar_len < 30:
                    return
                hy = y + face_h * 0.15
                panels.append({
                    "name": name_prefix,
                    "shape": "cylinder", "axis": "y",
                    "dims": {"width": DIAM, "height": bar_len, "depth": DIAM},
                    "pos": {"x": x, "y": hy, "z": z + STAND_OFF / 2},
                    "material": hw_mat,
                })
            return
        if handle == "knob":
            # Phase 2 Round 2: emit as sphere shape.
            # Radius = min(dim)/2 = 16mm (32mm diameter, common
            # cabinet-knob size). Client uses SphereGeometry.
            DIAM = 32
            if on_drawer:
                hy = y + face_h / 2 - face_h * 0.18
            else:
                hy = y + face_h * 0.25
            panels.append({
                "name": name_prefix,
                "shape": "sphere",
                "dims": {"width": DIAM, "height": DIAM, "depth": DIAM},
                "pos": {"x": x, "y": hy, "z": z + DIAM / 4},
                "material": hw_mat,
            })
            return
        if handle == "cup_pull":
            # Drawer-only convention; on a door it falls back to knob-ish
            # geometry (no real-world doors take cup pulls, but render
            # something so the configurator doesn't silently swallow it).
            cup_w = min(face_w * 0.4, 120)
            cup_h = 24
            cup_d = 22
            if cup_w < 30:
                return
            if on_drawer:
                hy = y + face_h / 2 - face_h * 0.15
            else:
                hy = y + face_h * 0.25
            panels.append({
                "name": name_prefix,
                "dims": {"width": cup_w, "height": cup_h, "depth": cup_d},
                "pos": {"x": x, "y": hy, "z": z},
                "material": hw_mat,
            })

    @api.model
    def _emit_drawer_fronts(self, panels, W, H, y0, DOOR_TH, DOOR_REVEAL,
                            drawer_count, door_style="slab", handle="none",
                            pull_finish="", door_mat="door"):
        """Append `drawer_count` evenly-divided drawer fronts.

        Algorithm (Phase-1 simplification: all fronts the same height):
            total_face_h = H − 2*DOOR_REVEAL             (top + bottom reveals)
            front_h      = (total_face_h − (n−1)*DOOR_REVEAL) / n
            Front i (0-indexed from bottom) sits at:
              y_centre = y0 + DOOR_REVEAL + front_h/2 + i*(front_h + DOOR_REVEAL)

        Phase-2 (2026-06-24) — each drawer front gets door_style elaboration
        (Shaker frame on five-piece) and one handle mesh.

        Phase-3 polish: graduated front heights (deeper drawers at bottom
        per real cabinetry practice) — pulled from the BoM workbook when
        it lands.
        """
        n = max(1, int(drawer_count))
        face_w = W - 2 * DOOR_REVEAL
        front_h = (H - 2 * DOOR_REVEAL - (n - 1) * DOOR_REVEAL) / n
        for i in range(n):
            y_centre = y0 + DOOR_REVEAL + front_h / 2 + i * (front_h + DOOR_REVEAL)
            z_face = DOOR_TH / 2 + DOOR_REVEAL
            self._emit_single_door_face(
                panels, x=0, y=y_centre, z=z_face,
                w=face_w, h=front_h,
                DOOR_TH=DOOR_TH, door_style=door_style,
                name_prefix=f"drawer_front_{i + 1}", door_mat=door_mat,
            )
            self._emit_handle(
                panels, x=0, y=y_centre, z=DOOR_TH + DOOR_REVEAL + 6,
                face_w=face_w, face_h=front_h,
                handle=handle, on_drawer=True,
                name_prefix=f"drawer_front_{i + 1}_handle",
                pull_finish=pull_finish,
            )

    def _emit_base_feet(self, panels, W, D, TOEKICK_H, BOX_TH):
        """Append 4 adjustable plastic levelers under a toekick family.

        Industry-default base "feet": Ø40mm × 75mm black plastic
        levelers sitting on the floor behind the toekick face panel.
        Inset 50mm from each side and front/back so they're tucked
        out of the customer's eye line in the centred 3/4 view.

        Approximated as 40×75×40mm boxes (the viewport only renders
        BoxGeometry — square footprint reads identically at this
        scale). Material `toekick` (matte black) — no new viewport
        material needed.

        Geometry note: foot top sits at y=75 (its centre y=37.5);
        the carcass bottom sits at y=TOEKICK_H (default 90mm), so
        the levelers physically lift the bottom panel with a small
        gap for the foot mount plate — matches real construction.
        """
        FOOT_DIM = 40
        FOOT_H = 75
        INSET = 50
        cy = FOOT_H / 2
        x_inset = (W / 2) - BOX_TH - INSET
        # Behind the toekick (which sits ~30mm back from door plane at
        # z = -18/2 ≈ -9), feet inset 50mm further from front + 50mm
        # from back so they fall in the hidden volume under the box.
        z_front = -INSET
        z_back = -D + INSET
        positions = [
            ( x_inset, cy, z_front),
            (-x_inset, cy, z_front),
            ( x_inset, cy, z_back),
            (-x_inset, cy, z_back),
        ]
        for i, (x, y, z) in enumerate(positions):
            panels.append({
                "name": f"foot_{i + 1}",
                "dims": {"width": FOOT_DIM, "height": FOOT_H, "depth": FOOT_DIM},
                "pos":  {"x": x, "y": y, "z": z},
                "material": "toekick",
            })

    def _emit_drawer_rails(self, panels, W, H, D, y0, DOOR_TH, DOOR_REVEAL,
                           BOX_TH, BACK_TH, drawer_count):
        """Append left + right side-mount slides for every drawer.

        Side-mount ball-bearing slides (Accuride/Knape & Vogt style):
        12mm × 50mm cross-section, full drawer depth minus a 30mm
        back gap (the standard clearance for the rear bracket).
        Mounted just inside each side panel at the drawer's vertical
        centreline — visible through the drawer opening when the
        front is open, peeks out at the back edge in solid mode.

        Y-spacing mirrors _emit_drawer_fronts exactly so each rail
        aligns with its matching drawer face. Material `hardware`
        (gunmetal, low roughness, high metalness — added to the
        viewport material dict in the same commit).
        """
        n = max(1, int(drawer_count))
        front_h = (H - 2 * DOOR_REVEAL - (n - 1) * DOOR_REVEAL) / n
        RAIL_TH = 12
        RAIL_H = 50
        RAIL_GAP = 6           # clearance between rail and side panel
        REAR_CLEAR = 30        # rail stops 30mm short of back panel
        # Rail extends from the front opening back to the rear clearance.
        # Front edge of the drawer cavity ≈ z = 0 (carcass front face);
        # rear ≈ z = -D + BACK_TH + REAR_CLEAR. Mid-point + depth:
        rail_d = D - BACK_TH - REAR_CLEAR
        rail_z = -(rail_d / 2) - 0  # centred in the cavity
        # X: just inside the side panel — side panel inside face is
        # at x = ±((W/2) - BOX_TH); rail sits RAIL_GAP further in.
        x_in = (W / 2) - BOX_TH - RAIL_GAP - (RAIL_TH / 2)
        for i in range(n):
            y_centre = y0 + DOOR_REVEAL + front_h / 2 + i * (front_h + DOOR_REVEAL)
            for side, x in (("L", -x_in), ("R", x_in)):
                panels.append({
                    "name": f"drawer_rail_{i + 1}_{side}",
                    "dims": {"width": RAIL_TH, "height": RAIL_H, "depth": rail_d},
                    "pos":  {"x": x, "y": y_centre, "z": rail_z},
                    "material": "hardware",
                })
