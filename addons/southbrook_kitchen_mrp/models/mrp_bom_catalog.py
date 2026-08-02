# SPDX-License-Identifier: LGPL-3.0-only
"""Catalog-wide cabinet BoM generator — M4 gap-fix, extended for the
"full BoM for every cabinet" task (2026-07-31).

Root cause originally fixed here (see PUNCHLIST / M4 ticket): only two
narrow, opt-in paths ever created an mrp.bom for a cabinet template —

  1. southbrook_kitchen_3d_configurator.kitchen_design._ensure_kitchen_bom,
     which only runs on the 3D-configurator "Create Quotation" action and
     seeds a STUB bom with EMPTY bom_line_ids (see that method's own
     docstring: "bom_line_ids stays empty; the shop team fills it in
     manually or a follow-up commit wires it up." — this IS that follow-up
     commit, for the catalog as a whole rather than one design at a time).
  2. The Hermes wizard, on explicit human Apply.

Fillers/accessories and any cabinet never pushed through the 3D
configurator's quote flow got neither, so the Order Builder's BoM Preview
warns and order confirmation spawns a placeholder MO with no cutlist.

This module provides ``mrp.bom._southbrook_generate_catalog_boms()`` — an
@api.model, catalog-wide, idempotent generator that MATERIALIZES real
bom_line_ids (never an empty stub) from:

  * The panel geometry ``mrp.bom._compute_panel_dimensions`` already
    computes (southbrook_estimating, ~mrp_bom.py) — the G1-gated single
    source of truth. NEVER re-derived here.
  * The real Marathon hardware SKUs resolved via
    ``southbrook.hardware.catalog.resolve()`` (southbrook_hardware_catalog,
    "Module 3") — the exact same resolution path
    sb.production.package.generate_from_mo() and the FreeCAD bridge
    already use to append hardware lines post-render.

ARCHITECTURE CHANGE (2026-07-31) — config_ok is no longer a hard skip
----------------------------------------------------------------------
Every prior version of this generator hard-skipped every
``config_ok=True`` template (``skipped_configurable``) on the reasoning
that its template-level ``southbrook_width_in``/``_height_in``/
``_depth_in`` scalars are a midpoint fallback, not a real customer's
chosen dimension — see the (now superseded) "PROD DEFECT" section below,
kept for history.

Client instruction (2026-07-31): every ``southbrook_is_cabinet`` template
must end up with a real, non-empty BoM — including the 19 ``config_ok=
True`` templates, 11 of which already carry an EMPTY, hand-seeded
"Canonical" ``mrp.bom`` shell (``southbrook_mrp_pm/data/routing_*.xml``)
that OWNS an 8-station routing (Cut Panels -> ... -> Pack & Label). That
routing is load-bearing infrastructure for the Manufacturing-PM Floor
Manager flow and must never be orphaned by unlinking/replacing its BoM.

The generator therefore now does TWO things for every cabinet template,
config_ok or not:

  1. Template-level "nominal" BoM (``product_id`` blank) — built from the
     template's own scalar dims, exactly as the non-configurable path
     always has. The "does a BoM already exist" guard is now "does a
     NON-EMPTY normal BoM exist" (skip) vs. "is there an EMPTY normal BoM
     whose code marks it as one of the three known auto-managed shell
     origins" (``"<code> Canonical"`` / ``"CatalogAutoBOM-*"`` /
     ``"KitchenAutoSeed-*"``) — write real lines ONTO that exact record
     rather than creating a sibling. An empty BoM with any OTHER code is
     presumed hand-built/unknown-origin and is still never touched (see
     ``_southbrook_is_recognized_shell_code``). This is a deliberate,
     client-directed change to the previously-documented "never touch an
     existing BoM, even empty" invariant — flagged here explicitly per
     the task's own instruction to call it out.
  2. Per-VARIANT BoM (``product_id`` set, native Odoo variant scoping) —
     ONLY for ``config_ok=True`` templates with real, materialized
     ``product.product`` variants. Dimensions/family/door/drawer counts
     are inferred from the variant's own configured attribute picks via
     ``sb.production.package._resolve_dims_from_variant`` (the same
     attribute-walking pattern ``_resolve_dims_from_order_line`` already
     uses for the quote-time cutlist path, generalized to work off a bare
     product.product). This gives a real customer's actual configured
     cabinet a correctly-dimensioned BoM for costing/planning BEFORE an
     order exists, while the template-level nominal BoM above remains as
     Odoo's native fallback for any variant that doesn't have its own.
     NEVER writes to the variant record itself — wizard-configured
     variants are user data.

KNOWN AMBIGUITY — panel/carcass raw material mapping (see
_southbrook_get_or_create_raw_material's docstring): this codebase has NO
real purchasable sheet-stock product data anywhere for panel substrates
(melamine/hardboard/plywood). Hardware lines bind to real, already-seeded
Marathon SKUs (unambiguous). Panel lines bind to a clearly-labeled
"Raw Material: ..." placeholder product, get-or-created once per
substrate, counted 1-unit-per-panel (not per real sheet/area). Edge
banding follows the same placeholder-product pattern (see
``_southbrook_get_or_create_edge_banding_tape``) — no cost is set on
either kind of placeholder; costing that catalog is the client's
decision, not guessed here. See bom-research.md §2.4/§6 for the fuller
"install sb_material_core/sb_material_mrp for real, costed materials"
alternative, deliberately left as a separate decision for the client
rather than a cross-module change bundled into this task.

PROD DEFECT (confirmed 2026-07-06, historical) — empty catalog BoMs on
config_ok templates
--------------------------------------------------------------------
The 12 catalog templates that are ``config_ok=True`` (SB-BASE-1DR,
SB-BASE-2DR, SB-WALL-1DR, SB-WALL-2DR, SB-DRAWER, SB-SINK-BASE,
SB-TALL-OVEN, SB-TALL-PANTRY, SB-CORNER, SB-VANITY, SB-ACCESSORY, and
``worktop``) ended up with a template-level ``mrp.bom`` carrying ZERO
``bom_line_ids`` on prod, because ``kitchen_design._ensure_kitchen_bom``
got there first with its known-empty ``KitchenAutoSeed-*`` stub and this
generator's old "skip if a normal BoM already exists" guard treated that
stub as "already handled." The 2026-07-31 architecture change above
supersedes the old fix (hard-skip config_ok) with the client-directed
fix (fill the shell in place, plus per-variant BoMs).
"""
import logging
import math

from odoo import api, models

_logger = logging.getLogger(__name__)


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    # ------------------------------------------------------------------
    # Cabinet-type -> family classification. Mirrors
    # southbrook_kitchen_3d_configurator.kitchen_design._ensure_kitchen_bom
    # 's own family_map exactly (Rule 3 / NF14), so both BoM-seeding paths
    # treat the same southbrook_cabinet_type identically.
    # ------------------------------------------------------------------
    _SOUTHBROOK_FAMILY_MAP = {
        "base": "base",
        "wall": "wall",
        "tall": "tall",
        "corner": "corner",
        "filler": "accessory",
        "panel": "accessory",
    }

    # Panel key -> substrate slug. Mirrors the INTENT of
    # southbrook_kitchen_mrp.sb_cutlist.DEFAULT_SUBSTRATE_BY_PANEL (sides/
    # top/bottom/shelf = melamine carcass stock, back = hardboard, door =
    # plywood), adapted to the key names mrp.bom._compute_panel_dimensions
    # actually returns ("shelf"/"door", not "adjustable_shelf" — that's a
    # SEPARATE geometry source, shared.southbrook_dims.panel_cut_list,
    # consumed by sb.cutlist/sb.production.package for the per-MO cutlist;
    # this generator only touches the template-level mrp.bom and does not
    # change that other, already-working path).
    _SOUTHBROOK_PANEL_SUBSTRATE = {
        "side_L": "melamine_white_5_8",
        "side_R": "melamine_white_5_8",
        "top": "melamine_white_5_8",
        "bottom": "melamine_white_5_8",
        "shelf": "melamine_white_5_8",
        "back": "hardboard_1_4",
        "door": "ply_3_4",
    }

    _SOUTHBROOK_SUBSTRATE_LABELS = {
        "melamine_white_5_8": "Raw Material: 5/8in White Melamine Panel Stock",
        "hardboard_1_4": "Raw Material: 1/4in Hardboard Panel Stock",
        "ply_3_4": "Raw Material: 3/4in Plywood Panel Stock",
    }

    # BOM-generation task (2026-07-31), extended by catalog decision
    # (2026-08-02) — accessory_type (End Panel / Filler / Cornice /
    # Pelmet / Plinth, southbrook_estimating/data/attributes.xml's
    # attr_accessory_type) -> a real, purchasable SKU from
    # southbrook_hardware_catalog/data/end_panel_seed.xml. All 5
    # accessory_type values now have a real seeded product (Cornice/
    # Pelmet/Plinth were the last 3 with no SKU anywhere in this catalog
    # — bom-research.md §4 item 3 — seeded 2026-08-02 as SB-CORNICE-STD/
    # SB-PELMET-STD/SB-PLINTH-STD). "Standard"/"Base" is the least-wrong
    # single default within each mapped type absent a dedicated
    # style/class attribute anywhere on the accessory template (see
    # _southbrook_resolve_accessory_product's docstring for the full
    # ambiguity note) — the generic melamine placeholder now only fires
    # for an accessory_type this dict doesn't recognise at all (e.g. a
    # future 6th value, or the type left blank).
    _SOUTHBROOK_ACCESSORY_SKU_BY_TYPE = {
        "end panel": "SB-EP-BASE-STD",
        "filler": "SB-FILLER-BASE",
        "cornice": "SB-CORNICE-STD",
        "pelmet": "SB-PELMET-STD",
        "plinth": "SB-PLINTH-STD",
    }

    @api.model
    def _southbrook_generate_catalog_boms(self):
        """Catalog-wide cabinet BoM generator (M4, extended 2026-07-31).

        Searches every ``product.template`` flagged
        ``southbrook_is_cabinet = True`` and ensures each ends up with a
        real, non-empty template-level ``mrp.bom`` — creating one,
        writing lines onto a recognized empty shell, or leaving an
        already-populated / unrecognized-origin BoM untouched. For
        ``config_ok=True`` templates, ALSO ensures each real, materialized
        ``product.product`` variant gets its own correctly-dimensioned,
        product_id-scoped BoM (see the module docstring's "ARCHITECTURE
        CHANGE" section).

        Idempotent — safe to re-run. Once a BoM (shell-filled, newly
        created, or per-variant) carries real lines, later runs treat it
        as already-handled and leave it alone (never recompute/clobber a
        BoM a human may since have hand-edited) — matching this
        generator's existing, long-standing idempotence contract.

        Returns a dict:
          ``created`` / ``shell_filled`` — new template-level BoMs
            created, and pre-existing EMPTY recognized shells that got
            real lines written onto them.
          ``skipped_existing_bom`` — a non-empty, or an empty-but-
            unrecognized-origin, template-level BoM was left untouched.
          ``skipped_configurable`` — retained for return-shape backward
            compatibility; always 0 now (nothing is hard-skipped for
            being config_ok anymore).
          ``skipped_empty`` — the build path genuinely produced zero
            materializable lines (belt-and-suspenders; should not happen
            for real catalog data).
          ``variant_created`` / ``variant_updated`` / ``variant_skipped``
            — per-variant BoM counters (config_ok templates only).
          ``skipped`` — sum of the three template-level skip counters.
          ``details`` — list of {template_id, template, bom_id,
            line_count} for every template-level BoM created or filled
            this run.
        """
        Template = self.env["product.template"].sudo()
        Bom = self.env["mrp.bom"].sudo()
        empty_result = {
            "created": 0,
            "shell_filled": 0,
            "skipped": 0,
            "skipped_existing_bom": 0,
            "skipped_configurable": 0,
            "skipped_empty": 0,
            "variant_created": 0,
            "variant_updated": 0,
            "variant_skipped": 0,
            "details": [],
        }
        if "southbrook_is_cabinet" not in Template._fields:
            # southbrook_kitchen_3d_configurator (owner of the flag) isn't
            # installed in this database. Nothing is in scope — this is
            # not an error, mirrors the getattr-defensive pattern already
            # used in southbrook_estimating.sale_order for the same field.
            _logger.info(
                "Southbrook catalog BoM generator: southbrook_is_cabinet "
                "field not present (southbrook_kitchen_3d_configurator not "
                "installed) — nothing to do."
            )
            return empty_result

        templates = Template.with_context(active_test=False).search(
            [("southbrook_is_cabinet", "=", True)]
        )

        created = 0
        shell_filled = 0
        skipped_existing_bom = 0
        skipped_empty = 0
        variant_created = 0
        variant_updated = 0
        variant_skipped = 0
        details = []

        for tmpl in templates:
            normal_boms = tmpl.with_context(active_test=False).bom_ids.filtered(
                lambda b: b.type == "normal" and not b.product_id
            )
            non_empty = normal_boms.filtered(lambda b: b.bom_line_ids)

            if non_empty:
                # Already has real lines (hand-built, Hermes-applied, a
                # prior run of this generator, or already filled) — never
                # touch it.
                skipped_existing_bom += 1
            else:
                reusable_shell = normal_boms.filtered(
                    lambda b: self._southbrook_is_recognized_shell_code(b.code)
                )
                hand_built_stub = normal_boms - reusable_shell
                if hand_built_stub:
                    # An empty BoM of unrecognized origin — presumed
                    # hand-built. Never mutate it, never create a sibling.
                    skipped_existing_bom += 1
                else:
                    bom_line_vals, _shelf_count = self._southbrook_build_cabinet_bom_lines(tmpl)
                    if not bom_line_vals:
                        _logger.warning(
                            "Southbrook catalog BoM generator: %s produced "
                            "zero sensible bom_line_ids — skipped rather "
                            "than creating/filling an empty BoM.",
                            tmpl.display_name,
                        )
                        skipped_empty += 1
                    elif reusable_shell:
                        shell = reusable_shell[0]
                        # (5, 0, 0) is a no-op here (the shell is empty by
                        # construction of this branch) but keeps the write
                        # correct/idempotent-shaped if this branch is ever
                        # reached a second time for the same record.
                        shell.write({"bom_line_ids": [(5, 0, 0)] + bom_line_vals})
                        shell_filled += 1
                        details.append({
                            "template_id": tmpl.id,
                            "template": tmpl.display_name,
                            "bom_id": shell.id,
                            "line_count": len(shell.bom_line_ids),
                        })
                    else:
                        default_code = tmpl.default_code or ("TMPL-%d" % tmpl.id)
                        bom = Bom.create({
                            "product_tmpl_id": tmpl.id,
                            "type": "normal",
                            "product_qty": 1.0,
                            "code": "CatalogAutoBOM-%s" % default_code,
                            "bom_line_ids": bom_line_vals,
                        })
                        created += 1
                        details.append({
                            "template_id": tmpl.id,
                            "template": tmpl.display_name,
                            "bom_id": bom.id,
                            "line_count": len(bom.bom_line_ids),
                        })

            # Per-variant BoMs — config_ok templates only. Never touches
            # the variant record itself (read-only attribute inspection).
            if getattr(tmpl, "config_ok", False):
                v_created, v_updated, v_skipped = self._southbrook_ensure_variant_boms(tmpl)
                variant_created += v_created
                variant_updated += v_updated
                variant_skipped += v_skipped

        skipped = skipped_existing_bom + skipped_empty
        _logger.info(
            "Southbrook catalog BoM generator: created=%s shell_filled=%s "
            "skipped_existing_bom=%s skipped_empty=%s variant_created=%s "
            "variant_updated=%s variant_skipped=%s",
            created, shell_filled, skipped_existing_bom, skipped_empty,
            variant_created, variant_updated, variant_skipped,
        )
        return {
            "created": created,
            "shell_filled": shell_filled,
            "skipped": skipped,
            "skipped_existing_bom": skipped_existing_bom,
            "skipped_configurable": 0,
            "skipped_empty": skipped_empty,
            "variant_created": variant_created,
            "variant_updated": variant_updated,
            "variant_skipped": variant_skipped,
            "details": details,
        }

    @api.model
    def _southbrook_is_recognized_shell_code(self, code):
        """True when ``code`` marks a BoM as one of the three known
        auto-managed shell/stub origins this generator is allowed to
        write real lines onto when empty:

          * ``"<SKU> Canonical"`` — the 12 hand-seeded routing-carrier
            BoMs from southbrook_mrp_pm/data/routing_*.xml. These OWN an
            8-station mrp.routing.workcenter sequence for the
            Manufacturing-PM Floor Manager flow and must never be
            unlinked/replaced — only filled in place.
          * ``"CatalogAutoBOM-*"`` — this generator's own prior stub
            (belt-and-suspenders; should be unreachable since this
            generator never creates an empty one, but a partially-applied
            manual edit could leave one behind).
          * ``"KitchenAutoSeed-*"`` — kitchen_design._ensure_kitchen_bom's
            quote-time stub (deliberately empty by that method's own
            design).

        Anything else (a blank/None code, or a hand-typed one like
        "PreexistingStub") is presumed hand-built/unknown-origin and is
        NEVER written onto, even if empty.
        """
        code = code or ""
        return (
            code.endswith(" Canonical")
            or code.startswith("CatalogAutoBOM-")
            or code.startswith("KitchenAutoSeed-")
        )

    @api.model
    def _southbrook_infer_corner_subtype(self, default_code):
        """Best-effort corner sub-type from a SKU's default_code, per
        bom-research.md §3.4/§5.8's mapping. Used to pick the right
        corner-mechanism SKU + hinge override via
        ``southbrook.hardware.catalog.resolve(corner_subtype=...)``.

        Returns "blind" | "wall" | "diag" | None (None = not a corner
        cabinet, or an unrecognized code — resolve() then falls back to
        the coarse per_family["corner"] default).
        """
        code = (default_code or "").upper()
        if "BLIND" in code:
            return "blind"
        if "WALL" in code and "CORNER" in code:
            return "wall"
        if "CORNER" in code:
            # SB-CORNER, SB-CORNER-DIAG, SB-CORNER-LSUSAN — the diagonal /
            # lazy-susan family is the default "plain corner" shape.
            return "diag"
        return None

    def _southbrook_build_cabinet_bom_lines(self, tmpl):
        """Build the template-level "nominal" bom_line_vals for a cabinet
        template, from its own scalar dims (southbrook_width_in/_height_in/
        _depth_in) and southbrook_cabinet_type. Used for every cabinet
        template regardless of config_ok — see the module docstring's
        "ARCHITECTURE CHANGE" section for why config_ok is no longer a
        reason to skip this.

        Returns (bom_line_vals, shelf_count).
        """
        w_in = tmpl.southbrook_width_in or 24.0
        h_in = tmpl.southbrook_height_in or 34.5
        d_in = tmpl.southbrook_depth_in or 24.0
        width_mm = w_in * 25.4
        height_mm = h_in * 25.4
        depth_mm = d_in * 25.4

        cabinet_type = tmpl.southbrook_cabinet_type or "base"
        family = self._SOUTHBROOK_FAMILY_MAP.get(cabinet_type, "base")

        # Rule 3 (Southbrook_Excel_to_Odoo_Mapping.md §3.4) — width -> door
        # count for boxed families; accessories carry no door.
        if family == "accessory":
            door_count = 0
        elif w_in >= 24.0:
            door_count = 2
        else:
            door_count = 1

        corner_subtype = None
        if family == "corner":
            corner_subtype = self._southbrook_infer_corner_subtype(tmpl.default_code)

        return self._southbrook_compute_bom_line_vals(
            family=family,
            width_mm=width_mm, height_mm=height_mm, depth_mm=depth_mm,
            door_count=door_count, drawer_count=0,
            corner_subtype=corner_subtype,
        )

    def _southbrook_build_variant_bom_lines(self, tmpl, variant):
        """Build bom_line_vals for ONE real, materialized product.product
        variant of a config_ok=True cabinet template, using its own
        configured attribute picks (via sb.production.package.
        _resolve_dims_from_variant) rather than the template's midpoint
        scalar. READ-ONLY on `variant` — never writes to it.

        Returns (bom_line_vals, shelf_count).
        """
        dims = self.env["sb.production.package"]._resolve_dims_from_variant(variant, tmpl)
        family = dims["family"]

        corner_subtype = None
        if family == "corner":
            code = variant.default_code or tmpl.default_code
            corner_subtype = self._southbrook_infer_corner_subtype(code)

        return self._southbrook_compute_bom_line_vals(
            family=family,
            width_mm=dims["width_mm"], height_mm=dims["height_mm"],
            depth_mm=dims["depth_mm"],
            door_count=dims["door_count"], drawer_count=dims["drawer_count"],
            corner_subtype=corner_subtype,
            accessory_type=dims.get("accessory_type"),
            soft_close=dims["soft_close"], slide_sku=dims.get("slide_sku"),
        )

    def _southbrook_compute_bom_line_vals(
        self, family, width_mm, height_mm, depth_mm, door_count,
        drawer_count=0, corner_subtype=None, accessory_type=None,
        soft_close=None, slide_sku=None,
    ):
        """The shared core: panel/carcass lines + hardware lines for one
        cabinet configuration. Used by BOTH the template-level nominal
        builder and the per-variant builder so the two never drift.

        Returns (bom_line_vals, shelf_count).
        """
        Bom = self.env["mrp.bom"].sudo()
        bom_line_vals = []
        shelf_count = 0

        if family == "accessory":
            # AMBIGUITY FLAG: a filler/decorative panel (southbrook_
            # cabinet_type "filler"/"panel") is a single flat board, not a
            # 6-panel box. Calling _compute_panel_dimensions here would
            # wrongly synthesize sides/top/bottom/back/shelf for a product
            # that is physically just one panel. Resolve to a real SKU
            # when accessory_type maps to one (End Panel / Filler); fall
            # back to the generic melamine placeholder otherwise (see
            # _southbrook_resolve_accessory_product).
            product = self._southbrook_resolve_accessory_product(accessory_type)
            bom_line_vals.append((0, 0, {
                "product_id": product.id,
                "product_qty": 1.0,
            }))
        else:
            # Drawer banks: _compute_panel_dimensions' "door" key is only
            # ever populated by door_count, and a drawer bank has none —
            # pass door_count=0 into the geometry call regardless of what
            # the caller passed, so no phantom full-height door panel is
            # ever synthesized for a drawer front.
            panel_door_count = 0 if family == "drawer" else door_count
            panel = Bom._compute_panel_dimensions(
                width_mm=width_mm,
                height_mm=height_mm,
                depth_mm=depth_mm,
                family=family,
                door_count=panel_door_count,
                drawer_count=drawer_count,
                finished_sides="none",
            )
            # Drawer banks carry no adjustable shelves (bom-research.md
            # §5.2's flagged gap: _compute_panel_dimensions' shelf logic is
            # height-only and doesn't know about family, so it still
            # returns a shelf tuple for a drawer bank -- suppressed here).
            shelf_count = 0 if family == "drawer" else int(panel.get("shelf_count") or 0)

            counts = {}
            for key, substrate in self._SOUTHBROOK_PANEL_SUBSTRATE.items():
                if family == "drawer" and key == "shelf":
                    continue
                value = panel.get(key)
                if value is None:
                    continue
                if key == "shelf":
                    qty = shelf_count
                elif key == "door":
                    qty = int(panel.get("door_count") or 0)
                else:
                    qty = 1
                if qty <= 0:
                    continue
                counts[substrate] = counts.get(substrate, 0) + qty

            # Drawer-front geometry (bom-research.md §5.2/§7) — ported
            # from sb_cutlist.py's R3 drawer-front block. mrp_bom.py's
            # _compute_panel_dimensions has no drawer-front equivalent (its
            # "door" key is None whenever door_count==0, which a drawer
            # bank always passes); the ACTUAL cut geometry
            # (face_height_mm = (H - (N+1)*3mm) / N per drawer) lives only
            # in sb.cutlist.generate_lines_from_panel_dict. For a
            # catalog-level BoM (materials count, not exact cut sizes) we
            # only need the LINE — one ply_3_4 substrate line per drawer
            # front, matching a door's finished-face substrate.
            if family == "drawer" and drawer_count > 0:
                counts["ply_3_4"] = counts.get("ply_3_4", 0) + drawer_count

            for substrate, qty in counts.items():
                product = self._southbrook_get_or_create_raw_material(substrate)
                bom_line_vals.append((0, 0, {
                    "product_id": product.id,
                    "product_qty": float(qty),
                }))

            # Edge banding (bom-research.md §4 item 1 — "100% absent as a
            # BoM/purchasable line"). Converts the Phase-1 scalar
            # edge_banding_length_mm into whole linear metres of banding
            # tape, rounded up (buying in whole metres, not fractional).
            edge_len_mm = panel.get("edge_banding_length_mm") or 0
            if edge_len_mm > 0:
                meters = max(1, math.ceil(edge_len_mm / 1000.0))
                eb_product = self._southbrook_get_or_create_edge_banding_tape()
                bom_line_vals.append((0, 0, {
                    "product_id": eb_product.id,
                    "product_qty": float(meters),
                }))

        # Hardware — real Marathon SKUs (+ the 5 southbrook_mrp_pm
        # fastener products, wired via default_code — see hardware_map.
        # json), the exact same resolution path Module 3/4 already use
        # post-render (sb.production.package.generate_from_mo). Rule 4
        # (bi-fold corner cabinets ship without soft-close) is the
        # default when the caller doesn't supply an explicit soft_close
        # (the per-variant path derives it from the configured slide;
        # the template-level nominal path has no such signal).
        if soft_close is None:
            soft_close = (family != "corner")

        Catalog = self.env["southbrook.hardware.catalog"]
        hardware_picks = Catalog.resolve(
            cabinet_family=family,
            door_count=door_count,
            drawer_count=drawer_count,
            shelf_count=shelf_count,
            soft_close=soft_close,
            slide_sku=slide_sku,
            corner_subtype=corner_subtype,
        )
        for product, qty in hardware_picks:
            if qty <= 0:
                continue
            bom_line_vals.append((0, 0, {
                "product_id": product.id,
                "product_qty": float(qty),
            }))

        return bom_line_vals, shelf_count

    def _southbrook_resolve_accessory_product(self, accessory_type):
        """Resolve an accessory_type ("End Panel" / "Filler" / "Cornice" /
        "Pelmet" / "Plinth") to a real, purchasable SKU — all 5 values
        now have one seeded (End Panel -> SB-EP-BASE-STD, Filler ->
        SB-FILLER-BASE, Cornice -> SB-CORNICE-STD, Pelmet ->
        SB-PELMET-STD, Plinth -> SB-PLINTH-STD — see end_panel_seed.xml)
        — falling back to the generic melamine placeholder only for an
        accessory_type this mapping doesn't recognise at all (unmapped/
        blank/future value).

        HONEST AMBIGUITY (bom-research.md §4 item 3): the real SKU
        catalog is keyed by style (Standard/Inline/T&G/Radius/Square) x
        class (Base/Tall/Wall) for End Panel, but no attribute anywhere
        on the accessory template/variant captures either axis for any
        of the 5 types — only the coarse accessory_type. "Base"+
        "Standard" (or the single representative SKU seeded for Cornice/
        Pelmet/Plinth, which carry no style/class axis at all) is used
        as the least-wrong single default (same "least-wrong" convention
        canonical_catalog_tag.xml's own comment already uses for tagging
        the accessory template cabinet_type="panel"), not a guess dressed
        as certainty.
        """
        sku = self._SOUTHBROOK_ACCESSORY_SKU_BY_TYPE.get((accessory_type or "").strip().lower())
        if sku:
            product = self.env["product.product"].sudo().search([
                "|", ("x_marathon_sku", "=", sku), ("default_code", "=", sku),
            ], limit=1)
            if product:
                return product
            _logger.warning(
                "Southbrook catalog BoM generator: accessory_type=%r maps "
                "to SKU %s but it isn't in the installed catalog — falling "
                "back to the generic melamine placeholder.",
                accessory_type, sku,
            )
        return self._southbrook_get_or_create_raw_material("melamine_white_5_8")

    @api.model
    def _southbrook_ensure_variant_boms(self, tmpl):
        """Ensure every real, materialized product.product variant of a
        config_ok=True cabinet template has its own product_id-scoped
        mrp.bom, correctly dimensioned from its own configured attribute
        picks. NEVER writes to the variant record itself.

        Idempotent per-variant, same shell/skip semantics as the
        template-level generator: a non-empty product_id-scoped BoM
        (ours or hand-built) is left alone; an empty one WE previously
        created (code prefix "CatalogAutoBOM-Variant-") gets its lines
        (re)written; anything else empty is presumed hand-built and left
        untouched.

        Returns (created, updated, skipped) counts.
        """
        Bom = self.env["mrp.bom"].sudo()
        created = updated = skipped = 0

        for variant in tmpl.product_variant_ids:
            if not variant.product_template_attribute_value_ids:
                # No configured attribute picks at all -- this variant IS
                # the bare template (every product.template always has at
                # least one product.product, even one with zero attribute
                # lines). It is indistinguishable from "no variant chosen
                # yet", so there is nothing per-variant to build: Odoo's
                # native BoM fallback already resolves such a variant to
                # the template-level nominal BoM above. Building a
                # separate, identical product_id-scoped BoM here would
                # only duplicate it.
                continue

            existing = Bom.with_context(active_test=False).search([
                ("product_id", "=", variant.id),
                ("type", "=", "normal"),
            ])
            non_empty = existing.filtered(lambda b: b.bom_line_ids)
            if non_empty:
                skipped += 1
                continue

            ours = existing.filtered(
                lambda b: (b.code or "").startswith("CatalogAutoBOM-Variant-")
            )
            hand_built = existing - ours
            if hand_built:
                skipped += 1
                continue

            bom_line_vals, _shelf_count = self._southbrook_build_variant_bom_lines(tmpl, variant)
            if not bom_line_vals:
                skipped += 1
                continue

            if ours:
                ours[0].write({"bom_line_ids": [(5, 0, 0)] + bom_line_vals})
                updated += 1
            else:
                code_base = variant.default_code or tmpl.default_code or ("TMPL-%d" % tmpl.id)
                Bom.create({
                    "product_tmpl_id": tmpl.id,
                    "product_id": variant.id,
                    "type": "normal",
                    "product_qty": 1.0,
                    "code": "CatalogAutoBOM-Variant-%s" % code_base,
                    "bom_line_ids": bom_line_vals,
                })
                created += 1

        return created, updated, skipped

    @api.model
    def _southbrook_cleanup_empty_catalog_boms(self):
        """One-time (repeatable) cleanup for the empty catalog BoMs.

        Prod carries 12 template-level ``mrp.bom`` records on
        ``config_ok=True`` cabinet templates with ZERO ``bom_line_ids`` —
        see the module docstring's "PROD DEFECT" section for the
        confirmed root cause (``kitchen_design._ensure_kitchen_bom``'s
        known-empty ``KitchenAutoSeed-*`` stub, created before this
        generator ever saw the template, which then treated the empty
        stub as "already handled").

        NOTE (2026-07-31): since ``_southbrook_generate_catalog_boms`` no
        longer hard-skips config_ok templates, re-running the generator
        now fills these in place instead of leaving them empty — this
        cleanup method is retained as a safety net for any
        ``KitchenAutoSeed-*``/``CatalogAutoBOM-*`` stub that is STILL
        empty for some other reason (e.g. the generator hasn't run yet on
        this database), not as the primary fix anymore.

        This method finds every EMPTY (``bom_line_ids`` falsy),
        normal-type ``mrp.bom`` on a ``southbrook_is_cabinet=True``
        template whose ``code`` marks it as machine-autoseeded by one of
        the two known auto-stub paths — ``CatalogAutoBOM-*`` or
        ``KitchenAutoSeed-*`` — and unlinks it, UNLESS it is referenced by
        any ``mrp.production`` (confirmed or not — an MO pointing at it
        means it is "in use", not orphaned, and must never be deleted
        out from under a production order).

        A hand-built or Hermes-applied BoM never carries either code
        prefix, so this can never delete real shop-authored data even if
        it happens to be empty. A "<SKU> Canonical" routing-carrier shell
        also never carries either prefix, so it is never a cleanup
        candidate — it should be FILLED (via the main generator), never
        deleted.

        Idempotent — a second run finds nothing left to delete.

        Returns ``{"deleted": int, "deleted_details": [...],
        "refused_in_use": [...]}``.
        """
        Bom = self.env["mrp.bom"].sudo()
        Production = self.env["mrp.production"].sudo()
        Template = self.env["product.template"].sudo()

        if "southbrook_is_cabinet" not in Template._fields:
            _logger.info(
                "Southbrook catalog BoM cleanup: southbrook_is_cabinet "
                "field not present — nothing to do."
            )
            return {"deleted": 0, "deleted_details": [], "refused_in_use": []}

        AUTOSEED_PREFIXES = ("CatalogAutoBOM-", "KitchenAutoSeed-")

        candidates = Bom.with_context(active_test=False).search([
            ("type", "=", "normal"),
            ("bom_line_ids", "=", False),
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
        ])
        candidates = candidates.filtered(
            lambda b: (b.code or "").startswith(AUTOSEED_PREFIXES)
        )

        deleted = 0
        deleted_details = []
        refused_in_use = []
        for bom in candidates:
            in_use_count = Production.search_count([("bom_id", "=", bom.id)])
            if in_use_count:
                refused_in_use.append({
                    "bom_id": bom.id,
                    "template_id": bom.product_tmpl_id.id,
                    "template": bom.product_tmpl_id.display_name,
                    "code": bom.code,
                    "production_count": in_use_count,
                })
                continue

            deleted_details.append({
                "bom_id": bom.id,
                "template_id": bom.product_tmpl_id.id,
                "template": bom.product_tmpl_id.display_name,
                "code": bom.code,
            })
            bom.unlink()
            deleted += 1

        _logger.info(
            "Southbrook catalog BoM cleanup: deleted=%s refused_in_use=%s",
            deleted, len(refused_in_use),
        )
        return {
            "deleted": deleted,
            "deleted_details": deleted_details,
            "refused_in_use": refused_in_use,
        }

    def _southbrook_get_or_create_raw_material(self, substrate):
        """Get-or-create a placeholder raw-material product for a panel
        substrate slug (melamine_white_5_8 / hardboard_1_4 / ply_3_4).

        KNOWN AMBIGUITY (flagged per task instructions, not guessed away):
        this codebase has NO real sheet-stock product data anywhere.
        ``southbrook.kitchen.material`` (southbrook_mrp_kitchen_workcenters)
        is shop-routing classification metadata, not an inventory/purchase
        record. The ``southbrook_kitchen_mrp.product_substrate_<slug>``
        xmlids that ``sb.cutlist._resolve_offcut_product`` defensively
        looks up (with ``raise_if_not_found=False``) don't exist either.

        Rather than invent a real purchasable SKU, supplier, or unit of
        measure that isn't in any of the source artifacts, this method
        creates ONE clearly-labeled "Raw Material: ..." consu product per
        substrate, get-or-created by a stable xmlid so re-runs and every
        template that needs the same substrate share one product instead
        of multiplying rows. Quantity on the BoM line is counted PER PANEL
        (1 unit = 1 panel-equivalent leaf), not per real sheet or square
        metre — the real sheet-yield/nesting accounting already exists
        downstream (sb.cutlist / to_nesting_envelope) and is untouched by
        this. Swap this method for a real purchasable SKU lookup when the
        raw-material catalog lands; the panel-count math above does not
        change.
        """
        xmlid = "raw_material_%s" % substrate
        product = self.env.ref(
            "southbrook_kitchen_mrp.%s" % xmlid, raise_if_not_found=False
        )
        if product:
            return product

        Product = self.env["product.product"].sudo()
        label = self._SOUTHBROOK_SUBSTRATE_LABELS.get(
            substrate, "Raw Material: %s" % substrate
        )
        product = Product.create({
            "name": label,
            "default_code": "RM-%s" % substrate.upper(),
            "type": "consu",
            "is_storable": True,
            "purchase_ok": True,
            "sale_ok": False,
        })
        self.env["ir.model.data"].create({
            "module": "southbrook_kitchen_mrp",
            "name": xmlid,
            "model": "product.product",
            "res_id": product.id,
            "noupdate": True,
        })
        return product

    # ------------------------------------------------------------------
    # Researched North American sheet-goods costs (client-authorized,
    # 2026-08-02 pricing pass). Applied by
    # ``_southbrook_apply_researched_sheet_goods_costs`` below. Full
    # citations live in scratchpad/retest/pricing-sheetgoods.md — read
    # that file in full before touching this table.
    #
    # SCOPE (deliberately not comprehensive — see the source doc's own
    # gaps section): "melamine 3/4in", "melamine 1/4in (backs)",
    # "maple edge tape", "baltic birch 5x5", and "thermofoil door" all
    # have researched figures too, but NONE of them has a real,
    # purchasable BOM-component product in this codebase to apply to —
    # this generator has no baltic-birch/drawer-box substrate, no
    # distinct maple-veneer-tape SKU (only the one generic PVC roll),
    # and no purchased-slab-door product at all (doors are cut from the
    # ply_3_4 panel-substrate placeholder, not bought finished). Those
    # figures are intentionally NOT applied to anything here rather than
    # invented onto the wrong product — see pricing-applied.md for the
    # explicit skip log.
    _RESEARCHED_SHEET_GOODS_COSTS = {
        "melamine_white_5_8": {
            "price": 58.00,
            "source": (
                "Mid-point of $44 (Chamberlain Timber Mart, live "
                "small-yard CAD price) to $73 (RONA Crystal White, "
                "box-store) CAD/sheet, 2026-08-02, confidence: medium. "
                "UNIT CAVEAT (pre-existing, not fixed by this seeder): "
                "this RM- placeholder's BOM qty is 1-unit-per-PANEL, "
                "not per real sheet (see _southbrook_get_or_create_"
                "raw_material's own docstring) — applying a per-sheet "
                "figure here overstates true per-panel cost until real "
                "area-based costing lands (sb_material_core / "
                "sb_material_mrp, currently uninstalled). Flagged, not "
                "silently corrected — that is a separate, larger "
                "architecture decision, not this seeder's job."
            ),
        },
        "ply_3_4": {
            "price": 103.99,
            "source": (
                "RONA - Maple Sanded Air-Dried Prefinished Plywood "
                "3/4in x 4x8, SKU 51206975, $103.99 CAD/sheet, "
                "2026-08-02, confidence: high (specific dated CAD "
                "quote). SKU MAPPING: this RM- placeholder is the "
                "generic '3/4in Plywood Panel Stock' bound to "
                "door-panel lines; no distinct maple-specific SKU "
                "exists in this catalog, so the maple-faced-ply figure "
                "is applied here as the best available match. Same "
                "per-panel-not-per-sheet caveat as melamine above "
                "applies."
            ),
        },
    }
    # Edge-banding tape: research gives a $/linear-ft mid-range rate;
    # this product's BOM qty is whole linear METRES (see
    # _southbrook_get_or_create_edge_banding_tape — qty =
    # ceil(edge_banding_length_mm / 1000)). UNIT CONVERSION: $0.12
    # CAD/ft (mid of the $0.10-0.15/ft working range — WWHardware /
    # Westlund non-glued white PVC .018in tape, various listings,
    # 2026-08-02, confidence: medium) x 3.28084 ft/m = $0.3937 CAD/m.
    _EDGEBAND_FT_TO_M_PRICE = round(0.12 * 3.28084, 4)

    @api.model
    def _southbrook_apply_researched_sheet_goods_costs(self):
        """Idempotent, fill-zeros-only seeder for the panel-substrate +
        edge-banding placeholder costs researched 2026-08-02
        (client-authorized — see scratchpad/retest/pricing-
        sheetgoods.md).

        Uses the SAME get-or-create helpers the catalog BoM generator
        itself uses (``_southbrook_get_or_create_raw_material`` /
        ``_southbrook_get_or_create_edge_banding_tape``) so this
        seeder works correctly whether or not the catalog generator has
        already run on a given database — it materializes the
        placeholder product if missing (idempotent by stable xmlid,
        exactly like the generator's own calls), then only ever fills
        its price if that price is currently zero.

        HARD RULE (do not relax): never overwrites a non-zero
        ``standard_price`` — see southbrook_hardware_catalog's sibling
        method for the full rationale (a concurrent Marathon
        warehouse/brand pricing pass always wins). Re-running this
        method after it has already applied a cost is a no-op.

        Company-currency note: figures are CAD — same "verify
        res.company.currency_id before trusting these numbers" caveat
        as the hardware/fastener sibling seeders; logged, non-blocking.

        Returns {"applied": [...], "skipped_nonzero": [...]} (default
        codes).
        """
        company_currency = self.env.company.currency_id.name
        if company_currency != "CAD":
            _logger.warning(
                "Researched sheet-goods cost seeder: company currency "
                "is %s, not CAD — the figures in _RESEARCHED_SHEET_"
                "GOODS_COSTS / _EDGEBAND_FT_TO_M_PRICE are "
                "CAD-denominated and are applied as-is to "
                "standard_price regardless. Verify res.company."
                "currency_id before trusting these numbers on this "
                "database (db=%s).",
                company_currency, self.env.cr.dbname,
            )

        applied, skipped_nonzero = [], []

        for substrate, info in self._RESEARCHED_SHEET_GOODS_COSTS.items():
            product = self._southbrook_get_or_create_raw_material(substrate)
            if product.standard_price:
                skipped_nonzero.append(product.default_code)
                continue
            product.standard_price = info["price"]
            applied.append(product.default_code)

        eb_product = self._southbrook_get_or_create_edge_banding_tape()
        if eb_product.standard_price:
            skipped_nonzero.append(eb_product.default_code)
        else:
            eb_product.standard_price = self._EDGEBAND_FT_TO_M_PRICE
            applied.append(eb_product.default_code)

        _logger.info(
            "Researched sheet-goods cost seeder: applied=%s "
            "skipped_nonzero=%s", applied, skipped_nonzero,
        )
        return {"applied": applied, "skipped_nonzero": skipped_nonzero}

    def _southbrook_get_or_create_edge_banding_tape(self):
        """Get-or-create a single placeholder edge-banding-tape product,
        same get-or-create-by-stable-xmlid pattern as
        ``_southbrook_get_or_create_raw_material`` (bom-research.md §4
        item 1 — "no PVC/ABS edge-banding tape product.product exists
        anywhere in this codebase"). Quantity on the BoM line is whole
        linear metres (see ``_southbrook_compute_bom_line_vals``'s edge-
        banding block) derived from the Phase-1 scalar
        ``edge_banding_length_mm`` — not a per-edge/per-substrate-color
        breakdown (that level of detail doesn't exist anywhere in the
        engine yet — flagged, not invented). No cost is set: costing this
        catalog is the client's decision, matching the raw-material
        placeholders' convention.
        """
        xmlid = "edge_banding_tape_pvc_34"
        product = self.env.ref(
            "southbrook_kitchen_mrp.%s" % xmlid, raise_if_not_found=False
        )
        if product:
            return product

        Product = self.env["product.product"].sudo()
        product = Product.create({
            "name": "Edge Banding Tape — PVC 3/4in (per linear metre)",
            "default_code": "RM-EDGEBAND-PVC-34",
            "type": "consu",
            "is_storable": True,
            "purchase_ok": True,
            "sale_ok": False,
        })
        self.env["ir.model.data"].create({
            "module": "southbrook_kitchen_mrp",
            "name": xmlid,
            "model": "product.product",
            "res_id": product.id,
            "noupdate": True,
        })
        return product
