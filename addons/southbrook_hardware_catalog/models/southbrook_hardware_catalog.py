# SPDX-License-Identifier: LGPL-3.0-only
"""Hardware-resolution service.

Given a configured carcass (family + door/drawer/shelf counts + soft-close
preference), returns the list of hardware SKUs the cabinet needs and the
quantity of each. The FreeCAD bridge calls this post-render to append
hardware lines to the BoM; the configurator can use it to preview the
pick list in the UX.

Loading strategy: the mapping rules live in ``data/hardware_map.json`` —
shipped inside the addon and read on first use. Keeping the rules as data
(per the init-doc anti-pattern list) means a brand swap or a new rule
doesn't require a code change.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from odoo import api, models

_logger = logging.getLogger(__name__)


def _load_hardware_map() -> dict:
    """Load and return the hardware_map.json shipped inside this addon."""
    path = Path(__file__).resolve().parent.parent / "data" / "hardware_map.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


class SouthbrookHardwareCatalog(models.AbstractModel):
    """Resolution service — exposed as an env.['southbrook.hardware.catalog']
    handle so callers do `env['southbrook.hardware.catalog'].resolve(...)`.
    """
    _name = "southbrook.hardware.catalog"
    _description = "Southbrook Hardware Resolution Service"

    @api.model
    def _hardware_map(self) -> dict:
        # Cached on the registry so we don't re-read the file every call.
        if not hasattr(self.pool, "_southbrook_hardware_map"):
            self.pool._southbrook_hardware_map = _load_hardware_map()
        return self.pool._southbrook_hardware_map

    # P2 — Known drawer-slide SKUs. The resolver swaps any of these in the
    # per_drawer aggregation when the caller passes an explicit slide_sku
    # (the configurator's "Drawer Slide" pick). All slides outside this
    # set continue to use the per_drawer default from the JSON map.
    _DRAWER_SLIDE_SKUS = frozenset({
        "BLM-MOV-450",     # Blum MOVENTO 450 mm (legacy default)
        "KS-K2832-21",     # King Slide 21" Soft-Close Undermount
        "KS-3032-18",      # King Slide 18" Ball-Bearing
        "HET-ACTRO-500",   # Hettich Actro 5D 500 mm
        "PR-602728",       # Salice Progressa+ Undermount
    })

    # BOM-generation task (2026-07-31) — the two default hinge SKUs that a
    # per_family_subtype_hinge entry (hardware_map.json) is allowed to
    # replace, mirroring the _DRAWER_SLIDE_SKUS swap pattern above. Any
    # hinge outside this set (e.g. an already-specialty SAL-* pick coming
    # from a different rule) is left alone.
    _HINGE_DEFAULT_SKUS = frozenset({"BLM-110-SC", "BLM-110-NSC"})

    @api.model
    def resolve(
        self,
        cabinet_family: str,
        door_count: int = 0,
        drawer_count: int = 0,
        shelf_count: int = 0,
        soft_close: bool = True,
        pull_finish: str = None,
        pull_size_mm: int = 128,
        handle_style: str = "pull",
        mount_appliance: bool = False,
        slide_sku: str = None,
        corner_subtype: str = None,
    ) -> List[Tuple]:
        """Return a list of (product.product, qty) tuples for the configured carcass.

        Mapping rules — see ``data/hardware_map.json``:

        * ``per_door`` × door_count for hinges + door bumpers + handle.
        * ``per_drawer`` × drawer_count for slides + drawer-front handles.
        * ``per_shelf`` × shelf_count for adjustable-shelf pins.
        * ``per_cabinet`` for cabinet-mounting screws, levelers,
          the cam-lock kit, and a flat fastener estimate (confirmat/
          dowel/cam — see ``_fastener_comment`` in the JSON map).

        ``soft_close=False`` swaps the hinge SKU to the non-soft-close
        variant. Bi-fold corner cabinets pass soft_close=False per the
        configurator's Rule 4 (family → soft-close).

        Audit 2026-06-10 — finish-aware handle selection:

        * ``pull_finish`` — snake-case key matching attr_pull_finish
          values (e.g. ``brushed_nickel``, ``matte_black``). When given,
          the resolver strips legacy default-pull SKUs (per
          ``pull_default_skus``) from the result and substitutes the
          finish-specific SKU from ``by_pull_finish[finish][size]``.
        * ``pull_size_mm`` — pull center-to-center in mm (96, 128, 192).
          Falls back to the finish's ``default`` SKU when unseeded.
        * ``handle_style`` — ``"pull"`` (default) or ``"knob"``. Knob
          swaps go through ``by_knob_finish`` and ``knob_default_skus``.
        * ``mount_appliance`` — when True, an appliance pull is added on
          top of the regular per-door handle (for tall_oven/fridge).

        BOM-generation task (2026-07-31) — corner-mechanism resolution:

        * ``corner_subtype`` — optional sub-classification within
          ``cabinet_family=="corner"`` (``"blind"`` | ``"diag"`` |
          ``"wall"``). When the ``"<cabinet_family>:<corner_subtype>"``
          key exists in ``hardware_map.json``'s ``per_family_subtype``,
          its dict REPLACES the coarser ``per_family[cabinet_family]``
          entry for this call (e.g. a blind corner gets the BCO
          mechanism only, not BCO+the generic corner default). A
          matching ``per_family_subtype_hinge`` entry also swaps the
          resolved ``BLM-110-SC``/``BLM-110-NSC`` hinge SKU for a
          specialty one (e.g. the Salice 165° blind-corner hinge).
          Omitted or unmatched subtypes fall back to the pre-existing
          ``per_family[cabinet_family]`` behaviour unchanged — fully
          backward compatible with every existing caller.

        Any rule whose SKU is missing from the installed seed is skipped
        with a logger warning — the resolution survives partial catalogs
        so the addon installs cleanly with the 20-row seed and grows
        seamlessly once the 179-row import lands.
        """
        m = self._hardware_map()
        Product = self.env["product.product"]

        # BOM-generation task (2026-07-31) — resolve the family+subtype
        # override key once. Only ever set when corner_subtype is given
        # AND hardware_map.json actually has a matching per_family_subtype
        # entry — otherwise every existing caller (which never passes
        # corner_subtype) is byte-for-byte unaffected.
        subtype_key = (
            f"{cabinet_family}:{corner_subtype}" if corner_subtype else None
        )
        hinge_override_sku = None
        if subtype_key:
            hinge_override_sku = (
                (m.get("per_family_subtype_hinge") or {}).get(subtype_key)
            )

        # Pick the per-door hinge depending on soft-close preference.
        per_door_section = "per_door_soft_close" if soft_close else "per_door"
        rules: List[Tuple[str, str, int]] = []  # (source, sku, qty)

        for sku, qty_per in (m.get(per_door_section) or {}).items():
            effective_sku = sku
            if hinge_override_sku and sku in self._HINGE_DEFAULT_SKUS:
                effective_sku = hinge_override_sku
            rules.append(("per_door", effective_sku, qty_per * door_count))
        # P2 — honour an explicit slide brand override. When the caller
        # passes slide_sku and the per_drawer rule entry is a slide
        # (member of _DRAWER_SLIDE_SKUS), swap to the requested SKU so
        # the resolved BoM binds to the actual product the customer
        # configured. Non-slide per_drawer entries (handles, etc.)
        # flow through unchanged.
        for sku, qty_per in (m.get("per_drawer") or {}).items():
            effective_sku = sku
            if slide_sku and sku in self._DRAWER_SLIDE_SKUS:
                effective_sku = slide_sku
            rules.append(("per_drawer", effective_sku, qty_per * drawer_count))
        for sku, qty_per in (m.get("per_shelf") or {}).items():
            rules.append(("per_shelf", sku, qty_per * shelf_count))
        for sku, qty in (m.get("per_cabinet") or {}).items():
            rules.append(("per_cabinet", sku, qty))

        # Family-specific overrides — e.g. tall pantry gets extra levelers.
        # A matching per_family_subtype entry REPLACES the coarser
        # per_family[cabinet_family] entirely (see resolve()'s docstring)
        # rather than adding to it — a blind corner should get exactly
        # the BCO mechanism, not BCO plus the generic corner default.
        if subtype_key is not None and subtype_key in (m.get("per_family_subtype") or {}):
            family_extra = (m.get("per_family_subtype") or {}).get(subtype_key) or {}
        else:
            family_extra = (m.get("per_family") or {}).get(cabinet_family) or {}
        for sku, qty in family_extra.items():
            rules.append((f"per_family[{cabinet_family}]", sku, qty))

        # Aggregate per-SKU before resolving — so per_cabinet + per_family
        # contributions to the same SKU (e.g. extra levelers on a tall
        # pantry) sum instead of producing duplicate BoM lines.
        totals: Dict[str, int] = {}
        for _source, sku, qty in rules:
            if qty <= 0:
                continue
            totals[sku] = totals.get(sku, 0) + qty

        # ---- Finish-aware handle override ----------------------------
        # Apply only when the caller passed pull_finish. Without it,
        # the legacy default (MRH-HDL-PUL128 brushed nickel) flows
        # through unchanged — preserves backward compatibility for
        # callers that haven't been updated for the audit's
        # attr_pull_finish surface yet.
        if pull_finish and handle_style == "pull":
            handle_qty = self._strip_default_handles(
                totals, m.get("pull_default_skus") or [])
            target_sku = self._lookup_pull_sku(
                m, pull_finish, pull_size_mm)
            if target_sku and handle_qty > 0:
                totals[target_sku] = totals.get(target_sku, 0) + handle_qty
        elif pull_finish and handle_style == "knob":
            handle_qty = self._strip_default_handles(
                totals, m.get("knob_default_skus") or [])
            target_sku = (m.get("by_knob_finish") or {}).get(pull_finish)
            if target_sku and handle_qty > 0:
                totals[target_sku] = totals.get(target_sku, 0) + handle_qty

        # ---- Appliance pull addition --------------------------------
        if mount_appliance and pull_finish:
            app_sku = (m.get("appliance_pulls") or {}).get(pull_finish)
            if app_sku:
                # One appliance pull per appliance-door front. door_count
                # on tall_oven / tall_fridge equals appliance-door count.
                totals[app_sku] = totals.get(app_sku, 0) + max(door_count, 1)

        # Batch the SKU→product lookup: one query for all SKUs instead of a
        # search() per SKU. resolve() is the per-carcass hot path (called by
        # BoM generation across whole orders), so the round-trip count matters.
        # setdefault keeps the first product per SKU — same as the prior
        # limit=1 (default order) semantics.
        #
        # BOM-generation task (2026-07-31) — also match on default_code, not
        # just x_marathon_sku. The 5 southbrook_mrp_pm fastener products
        # (FAST-CONFIRMAT-50 etc.) carry a default_code but no
        # x_marathon_sku — mirroring the Marathon-SKU field would have
        # required southbrook_mrp_pm to take a new hard dependency on this
        # module just to set one field. Every pre-existing seeded SKU still
        # carries x_marathon_sku == default_code, so this is additive, not
        # a behaviour change for any SKU already in the catalog.
        by_sku: Dict[str, Any] = {}
        if totals:
            skus = list(totals)
            for product in Product.search([
                    "|",
                    ("x_marathon_sku", "in", skus),
                    ("default_code", "in", skus),
            ]):
                key = product.x_marathon_sku or product.default_code
                by_sku.setdefault(key, product)

        result: List[Tuple] = []
        for sku, qty in totals.items():
            product = by_sku.get(sku)
            if not product:
                _logger.warning(
                    "hardware_map references SKU '%s' not present in the "
                    "catalog — skipped. Add the SKU to the seed or run "
                    "the Marathon-catalog import.",
                    sku,
                )
                continue
            result.append((product, qty))
        return result

    @staticmethod
    def _strip_default_handles(totals: Dict[str, int], default_skus: list) -> int:
        """Remove default-handle SKUs from totals; return total stripped qty
        so the caller can re-add the finish-specific SKU with the same
        quantity (preserves the per_door × door_count + per_drawer × drawer_count
        sum that flowed through the legacy aggregation)."""
        stripped = 0
        for sku in default_skus:
            if sku in totals:
                stripped += totals.pop(sku)
        return stripped

    @staticmethod
    def _lookup_pull_sku(
        m: dict, pull_finish: str, pull_size_mm: int,
    ) -> str:
        """Find the right pull SKU for (finish, size). Falls back to the
        finish's ``default`` if the specific size isn't mapped. Returns
        None if the finish itself is unmapped — the caller logs and the
        default would be unable to land, so a downstream warning surfaces
        the gap."""
        finish_map = (m.get("by_pull_finish") or {}).get(pull_finish)
        if not finish_map:
            return None
        size_key = f"{pull_size_mm}mm"
        return finish_map.get(size_key) or finish_map.get("default")

    # ------------------------------------------------------------------
    # Researched North American component costs (client-authorized,
    # 2026-08-02 pricing pass). Applied by
    # ``_southbrook_apply_researched_hardware_costs`` below. Full
    # citations live in scratchpad/retest/pricing-hardware.md (read that
    # file for the complete sourcing table) — each entry here is a
    # one-line summary of that document's provenance for the SKU, per
    # the task's "the code IS the provenance record" instruction.
    #
    # x_pricing_pending is DELIBERATELY left untouched by this seeder
    # (still True on every SKU below, before and after). See
    # tests/test_pending_pricing.py::test_every_seed_sku_is_pricing_
    # pending — that invariant is CORRECT to keep: every figure below is
    # a retail-derived or explicit low-confidence estimate (Richelieu,
    # the real Ontario trade source, gates its net pricing behind a
    # trade-account login per pricing-hardware.md's own caveat), not a
    # confirmed trade-account cost. "Pending" still accurately describes
    # these numbers even once applied.
    _RESEARCHED_HARDWARE_COSTS = {
        "BLM-110-SC": {
            "price": 9.00,
            "source": (
                "Rockler.com - Blum 71B3550 CLIP top BLUMOTION 110deg "
                "soft-close 20-pack $109.99 USD ($5.50/hinge) x 1.40 "
                "CAD/USD + Blum Warehouse UK 0mm mounting plate ~$1.30 "
                "CAD cross-ref, 2026-08-02, confidence: medium "
                "(retail-derived)."
            ),
        },
        "SAL-C2R-165": {
            "price": 5.25,
            "source": (
                "Westlund Distributing - Salice C2RFA99 165deg blind-"
                "corner hinge, $3.75 USD single-unit x 1.40, 2026-08-02, "
                "confidence: medium (retail-derived)."
            ),
        },
        "BLM-MOV-450": {
            "price": 43.75,
            "source": (
                "WWHardware.com - Blum 563H4570B TANDEM 18in full-"
                "extension slide PAIR, $31.25 USD/set x 1.40, "
                "2026-08-02, confidence: firm (retail quote). PRICED "
                "PER PAIR - hardware_map.json's per_drawer rule already "
                "carries qty=1/drawer meaning one pair; no further unit "
                "conversion needed on this SKU."
            ),
        },
        "MRH-CAMLOCK-KIT": {
            "price": 0.55,
            "source": (
                "Hartert-Russell-Home (Hafele Minifix-15 cam, $0.14 "
                "USD) + Hafele.com (Minifix C100 spreading bolt, ~$0.25 "
                "USD), summed x 1.40, 2026-08-02, confidence: low-medium "
                "(retail-derived, two micro-retailers stitched "
                "together). SKU MAPPING: pricing-hardware.md's own "
                "header names MRH-CAMLOCK-KIT (not FAST-CAM) as the "
                "repo SKU this cam+bolt figure targets."
            ),
        },
        "MRH-HDL-PUL128": {
            "price": 2.83,
            "source": (
                "Home Depot Canada - Richelieu Essentials 128mm bar "
                "pull 6-pack, $16.98 CAD / 6, 2026-08-02, confidence: "
                "medium (firm CAD price, quoted for the Brushed English "
                "Gold finish tier - this SKU's actual Brushed Nickel "
                "finish wasn't independently re-priced this pass)."
            ),
        },
        "MRH-MTGSCR-25PK": {
            # UNIT CONVERSION: research gives $/each; this SKU's UoM is
            # a 25-pack (see southbrook_hardware_seed.xml's "Eurocam
            # Screw 25-Pack"). $0.1257 CAD/ea x 25 = $3.1425/pack.
            "price": round(0.1257 * 25, 4),
            "source": (
                "Home Depot - Everbilt #8x1-1/4in wood screw, $8.98 "
                "USD/100 x 1.40 = $0.1257 CAD/ea, 2026-08-02, "
                "confidence: firm retail (USD converted). SKU MAPPING "
                "INFERENCE: the research found no confirmed repo-SKU "
                "match for a generic wood screw; MRH-MTGSCR-25PK "
                "('Eurocam Screw 25-Pack, cabinet mounting') is this "
                "catalog's closest generic cabinet-mounting screw - "
                "applied here as the best available match, not a "
                "research-confirmed exact SKU tie."
            ),
        },
        "SB-CM-BCO": {
            "price": 991.19,
            "source": (
                "Ferguson Home - Rev-A-Shelf 5PSP-18SC-CR 18in blind-"
                "corner pull-out (soft-close), $707.99 USD x 1.40, "
                "2026-08-02, confidence: medium (retail-derived, mid "
                "tier - the research's recommended default)."
            ),
        },
        "SB-CM-CPOS": {
            "price": 173.17,
            "source": (
                "Home Depot - Rev-A-Shelf 6472-32-11-52 32in white "
                "polymer 2-shelf kidney lazy-susan set, $123.69 USD x "
                "1.40, 2026-08-02, confidence: firm retail (USD "
                "converted). SKU MAPPING: verified against "
                "corner_solutions_seed.xml - this is the mechanism "
                "hardware_map.json's 'corner:diag' per_family_subtype "
                "rule resolves to for a diagonal lazy-susan cabinet "
                "(seeded product name says 'Pull-Out Shelving' rather "
                "than 'lazy susan' but occupies the same functional "
                "slot; SB-CM-LCPOS/SB-CM-PBCO/etc. are NOT wired to any "
                "resolve() rule and are out of scope here)."
            ),
        },
        "SB-CM-PBCO": {
            # UNRELIABLE tier per the task brief — set the conservative
            # LOW end of the sourced range, flagged for a supplier
            # quote (see note_append below, written to x_material).
            "price": 1120.00,
            "source": (
                "UNRELIABLE (Vauth-Sagel VS COR premium tier) - no firm "
                "current price found for a full VS COR Fold assembly; "
                "comparable VS COR Flex subassemblies run ~$400-500 USD "
                "each (not a full set) on CabinetParts.com, 2026-08-02, "
                "confidence: LOW. Conservative LOW end of the "
                "directional $1,120-1,680 CAD range applied per task "
                "instruction (set the LOW figure + flag for a supplier "
                "quote rather than guess the midpoint)."
            ),
            "note_append": "PRICING ESTIMATE — needs supplier quote (2026-08-02)",
        },
    }

    @api.model
    def _southbrook_apply_researched_hardware_costs(self):
        """Idempotent, fill-zeros-only seeder for the hinge / slide /
        cam-lock / handle / corner-mechanism costs researched 2026-08-02
        (client-authorized — see scratchpad/retest/pricing-hardware.md,
        read in full before touching ``_RESEARCHED_HARDWARE_COSTS``
        above).

        HARD RULE (do not relax): a product whose ``standard_price`` is
        already non-zero is SKIPPED, never overwritten. A concurrent
        session may be applying real Marathon warehouse/brand pricing
        to the same SKUs — that data always wins over this
        retail-derived research pass. Re-running this method after it
        has already applied a cost is a no-op for every SKU it touched
        the first time (idempotent): once ``standard_price`` is
        non-zero, this method's own prior write makes every later call
        treat that SKU as already-priced and skip it exactly like a
        concurrent session's write would.

        Company-currency note: the sourced figures are CAD. This method
        does NOT convert currency — it assumes ``standard_price`` is
        being set in the current company's own currency. Callers /
        reviewers must verify ``res.company.currency_id`` is actually
        CAD before trusting the numeric values on a given database; a
        mismatch is logged as a warning but does not block the write
        (this seeder never touches a production company).

        Returns a dict: {"applied": [...], "skipped_nonzero": [...],
        "skipped_no_product": [...]} — SKU lists, for logging and test
        assertions.
        """
        company_currency = self.env.company.currency_id.name
        if company_currency != "CAD":
            _logger.warning(
                "Researched hardware cost seeder: company currency is "
                "%s, not CAD — the figures in _RESEARCHED_HARDWARE_"
                "COSTS are CAD-denominated and are applied as-is to "
                "standard_price regardless. Verify res.company."
                "currency_id before trusting these numbers on this "
                "database (db=%s).",
                company_currency, self.env.cr.dbname,
            )

        Product = self.env["product.product"].sudo()
        applied, skipped_nonzero, skipped_no_product = [], [], []

        for sku, info in self._RESEARCHED_HARDWARE_COSTS.items():
            product = Product.search([
                "|", ("x_marathon_sku", "=", sku), ("default_code", "=", sku),
            ], limit=1)
            if not product:
                skipped_no_product.append(sku)
                _logger.warning(
                    "Researched hardware cost seeder: SKU '%s' not "
                    "found in the installed catalog — skipped.", sku,
                )
                continue
            if product.standard_price:
                skipped_nonzero.append(sku)
                continue

            product.standard_price = info["price"]
            note_append = info.get("note_append")
            if note_append and note_append not in (product.x_material or ""):
                product.x_material = (
                    f"{product.x_material} — {note_append}"
                    if product.x_material else note_append
                )
            applied.append(sku)

        _logger.info(
            "Researched hardware cost seeder: applied=%s "
            "skipped_nonzero=%s skipped_no_product=%s",
            applied, skipped_nonzero, skipped_no_product,
        )
        return {
            "applied": applied,
            "skipped_nonzero": skipped_nonzero,
            "skipped_no_product": skipped_no_product,
        }
