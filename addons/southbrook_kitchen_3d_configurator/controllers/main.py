import logging
import math

from odoo import http
from odoo.http import request

# PR2 — the pure layout engine's WALLS tuple is the single source of
# truth for valid wall identifiers server-side too. Imported the same
# way models/kitchen_design.py:11 does.
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine
# C2 — same exception the model's action_auto_arrange raises when the
# corner-resolution engine can't physically fit the cabinets. Imported
# the same way kitchen_layout_engine itself is above (models/
# kitchen_design.py never needs this one directly — it lets the
# savepoint rollback speak for itself — but the controller surfaces a
# human-readable warning to the client instead of a bare 500).
from odoo.addons.southbrook_estimating.models.kitchen_layout_engine import (
    LayoutCapacityExceeded,
)
# Task 9b (review round 2) — the debris/sentinel threshold constant now
# has exactly ONE definition, on the model (kitchen_design.py), shared
# by this guard, the migration, and all three RESET-TO-CANONICAL
# reactivation sites (the other two live on the model itself as
# SouthbrookKitchenDesignLine._is_debris_signature; this file's own
# elif-branch reset — save_design's down-shift path — calls that same
# method directly on the line recordset, so only the raw threshold
# VALUE needs importing here for the write-path guard's own
# raw-JSON-value check below, which runs before any line record exists).
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    DEBRIS_THRESHOLD_IN as _DEBRIS_THRESHOLD_IN,
)

_logger = logging.getLogger(__name__)

# Task 9b (corner-remediation, 2026-07-30) — server-side write-path guard
# against the client's 1e6 sort-key sentinel (kitchen_configurator.js
# `_addCabinetFromProduct`'s `sortX` fallback) leaking into a save_design
# payload when packRow is skipped in multiWall mode (Task 9 report,
# "producer" section). Threshold value imported from kitchen_design.py's
# `DEBRIS_THRESHOLD_IN` (review round 2 — single source of truth; see
# that constant's docstring for the full list of shared call sites).


def _is_sentinel_position(value):
    """True if `value` (an incoming x/z position, still raw JSON) is at
    or beyond the debris-sentinel threshold in either direction. Any
    non-numeric input is NOT a sentinel here — it fails elsewhere
    (Line.create's own field validation) and is not this guard's job."""
    try:
        return abs(float(value)) >= _DEBRIS_THRESHOLD_IN
    except (TypeError, ValueError):
        return False


class SouthbrookKitchenConfiguratorController(http.Controller):

    # ── Products catalogue ───────────────────────────────────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/products",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def products(self, partner_id=False):
        """Return all cabinet products available to the configurator.

        D3 — when partner_id is supplied, prices reflect the partner's
        channel pricelist (Dealer/Tradesperson/etc.). Also returns the
        resolved channel metadata so the UI can show a "Channel:
        Dealer -50%" badge alongside the catalog. Without partner_id
        falls back to product.lst_price (retail).
        """
        # v19 ORM rejects dotted M2O field references in order= clauses
        # ("Invalid field property '<name>' on <model>.<m2o>") even
        # though it accepts them in domains. Sort by sortable column-
        # local fields here; the JS layer groups by cabinet_type on
        # display.
        products = request.env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
            ("sale_ok", "=", True),
        ], order="default_code, name")
        partner = self._browse_partner(partner_id)
        pricelist = self._resolve_pricelist(partner)
        return {
            "channel": self._channel_meta(partner, pricelist),
            "products": [self._product_payload(p, pricelist, partner) for p in products],
        }

    # ── Layout calculation ───────────────────────────────────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/layout",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def layout(self, room_width_in=12, room_depth_in=24, room_height_in=96,
               partner_id=False, filler_strategy="split",
               wall_cab_top_alignment="fixed_gap", soffit_height_in=84.0):
        """
        Compute the cabinet fill for the given room dimensions.

        Returns a dict with:
          room        — echoed dimensions
          items       — list of cabinet placement records
          summary     — counts, totals, remainder
          channel     — pricelist metadata (D3)
          error       — non-empty string if no products configured

        D3 — partner_id (optional) drives channel-pricelist resolution
        so per-item prices and the summary total reflect Dealer /
        Tradesperson / KD / etc. discounts. Falls back to retail when
        no partner is supplied.
        """
        env = request.env
        base = self._first_product(env, "base")
        wall = self._first_product(env, "wall")

        partner = self._browse_partner(partner_id)
        pricelist = self._resolve_pricelist(partner)

        if not base or not wall:
            missing = []
            if not base: missing.append("Base Cabinet")
            if not wall: missing.append("Wall Cabinet")
            return {
                "error": (
                    "No %s product configured. "
                    "Tag a saleable product with 'Southbrook Cabinet' + "
                    "the matching cabinet type to populate the catalog."
                ) % " / ".join(missing),
                # D10 — structured error code + CTA so the UI can render
                # an onboarding nudge with a real button instead of a
                # bare error string.
                "error_code": "NO_CABINETS",
                "error_cta":  {
                    "label": "Open Cabinet Products",
                    "action": {
                        "type":      "ir.actions.act_window",
                        "name":      "Southbrook Cabinet Products",
                        "res_model": "product.template",
                        "view_mode": "list,form",
                        "views":     [(False, "list"), (False, "form")],
                        "domain":    [("southbrook_is_cabinet", "=", True)],
                        "target":    "current",
                        "context":   {
                            "default_southbrook_is_cabinet": True,
                            "default_sale_ok":               True,
                        },
                    },
                },
                "items": [],
                "summary": {"base_count": 0, "wall_count": 0, "total": 0,
                             "price": 0.0, "remainder_in": 0.0},
                "room": {"width_in": float(room_width_in),
                         "depth_in": float(room_depth_in),
                         "height_in": float(room_height_in)},
                "channel":  self._channel_meta(partner, pricelist),
                "warnings": [],
            }

        module_w = base.product_tmpl_id.southbrook_width_in or 24.0
        rw = float(room_width_in)
        n  = max(0, int(math.floor(rw / module_w)))
        # Task 10 (SBK-023) — remainder only has meaning in the context of
        # a real cabinet run; an undersized/empty room (n==0, e.g. the
        # client's own 12in minimum room width vs. a 24in module) has no
        # run to complete, so it must not synthesize one. Pre-fix this
        # computed `remainder = rw` unconditionally for n==0, and the
        # filler-creation branch below then priced + placed that as a
        # real (phantom) filler line on a room with ZERO real cabinets —
        # the confirmed "[FP3] +$140 filler on an empty 12in room" /
        # "$70 persisted, zero cabinets" findings (V5-partc §SBK-023).
        # For n >= 1 this is UNCHANGED: `rw - n*module_w` is the honest
        # geometric gap and stays authoritative for every filler_strategy
        # (including "scribe" — remainder_in there is by design the true
        # physical gap the installer scribes on site, decoupled from
        # filler $, same pattern as "Est. excludes filler by design").
        remainder = (rw - n * module_w) if n > 0 else 0.0

        # Cabinet dimensions for placement
        base_h = base.product_tmpl_id.southbrook_height_in or 34.5
        wall_h = wall.product_tmpl_id.southbrook_height_in or 30.0
        ctr_t  = 1.5    # countertop thickness (in)
        gap    = 18.0   # clearance between counter surface and wall cab bottom
        min_z  = base_h + ctr_t + gap   # bottom of wall cabinet (industry minimum)

        # D8 — Wall-cabinet Z computed from chosen alignment mode.
        # Never drops the bottom-of-cab below the industry minimum
        # (18" above counter); the alignment mode only raises it.
        alignment = (wall_cab_top_alignment or "fixed_gap").lower()
        soffit_h  = float(soffit_height_in or 0)
        if alignment == "to_ceiling":
            wall_z = max(min_z, float(room_height_in) - wall_h)
        elif alignment == "to_soffit" and soffit_h > 0:
            wall_z = max(min_z, soffit_h - wall_h)
        else:
            alignment = "fixed_gap"
            wall_z = min_z

        # D8 — Validation: wall cab top must not exceed the effective
        # ceiling (soffit-bottom when 'to_soffit', otherwise ceiling).
        effective_ceiling = (soffit_h
            if (alignment == "to_soffit" and soffit_h > 0)
            else float(room_height_in))
        wall_top = wall_z + wall_h
        warnings = []
        if wall_top > effective_ceiling + 0.01:
            warnings.append({
                "code":     "WALL_CAB_EXCEEDS_CEILING",
                "severity": "blocking",
                "message":  (
                    "Wall cabinet top reaches %.1f\" but %s sits at %.1f\". "
                    "Either lower the wall cabinets, drop the alignment "
                    "mode, or raise the %s."
                ) % (
                    wall_top,
                    "soffit" if alignment == "to_soffit" else "ceiling",
                    effective_ceiling,
                    "soffit" if alignment == "to_soffit" else "ceiling",
                ),
            })

        items = []
        for i in range(n):
            x = i * module_w
            # PR3.0 — y/z field-semantics migration: y_position_in is the
            # canonical mount-height/elevation field, z_position_in is the
            # depth axis per COORDINATE_CONTRACT.md. Base cabinets are
            # floor-flush (y=0, z=0, unchanged). Wall cabinets carry their
            # D8-computed mount height (`wall_z`) in y now, not z; z stays
            # 0 (flush to the back wall) — this was the "z-as-height"
            # convention flagged as the riskiest finding in the PR3
            # coordinate-contract matrix.
            items.append(self._layout_item(base, i, x, 0.0,    0.0, pricelist, partner))
            items.append(self._layout_item(wall, i, x, wall_z, 0.0, pricelist, partner))

        # D7 — Smart filler placement. Honors the chosen filler_strategy:
        #  split: half-width filler at each end (default; pushes bases by half)
        #  left:  single filler at x=0 (push all bases right by remainder)
        #  right: single filler at x = n * module_w (right end)
        #  scribe: no filler (carpenter scribes end cabinet on site)
        filler_strategy = (filler_strategy or "split").lower()
        if filler_strategy not in ("split", "left", "right", "scribe"):
            filler_strategy = "split"
        if remainder >= 1.0 and filler_strategy != "scribe":
            if filler_strategy == "split":
                half = remainder / 2.0
                # Shift bases + walls right by `half` so the left filler
                # sits at x=0 cleanly.
                for it in items:
                    if it["cabinet_type"] in ("base", "wall"):
                        it["x_position_in"] = it["x_position_in"] + half
                lf = self._make_filler(env, half, 0.0,
                                        "filler-L", pricelist, partner)
                rf = self._make_filler(env, half, n * module_w + half,
                                        "filler-R", pricelist, partner)
                if lf: items.append(lf)
                if rf: items.append(rf)
            elif filler_strategy == "left":
                for it in items:
                    if it["cabinet_type"] in ("base", "wall"):
                        it["x_position_in"] = it["x_position_in"] + remainder
                lf = self._make_filler(env, remainder, 0.0,
                                        "filler-L", pricelist, partner)
                if lf: items.append(lf)
            else:   # right
                rf = self._make_filler(env, remainder, n * module_w,
                                        "filler-R", pricelist, partner)
                if rf: items.append(rf)

        cabinet_price = sum(it["price"] for it in items
                             if it["cabinet_type"] not in ("filler",))
        filler_price  = sum(it["price"] for it in items
                             if it["cabinet_type"] == "filler")

        return {
            "room": {
                "width_in":  rw,
                "depth_in":  float(room_depth_in),
                "height_in": float(room_height_in),
            },
            "items": items,
            "summary": {
                "base_count":      n,
                "wall_count":      n,
                "total":           n * 2,
                # Backward compat: 'price' kept as cabinet total. The
                # UI can now show filler as a separate breakdown row.
                "price":           cabinet_price,
                "cabinet_price":   cabinet_price,
                "filler_price":    filler_price,
                "remainder_in":    round(remainder, 2),
                "filler_strategy": filler_strategy,
                "snap_hint":       self._snap_hint(rw, module_w),
            },
            "channel":  self._channel_meta(partner, pricelist),
            "warnings": warnings,
            "error":    "",
        }

    # ─── D7 — filler helpers ────────────────────────────────────────────────────
    def _make_filler(self, env, width_in, x, key, pricelist, partner):
        """Build a single filler item at position x, width prorated."""
        filler = self._first_product(env, "filler")
        if not filler:
            return None
        fp = self._product_payload(filler, pricelist, partner)
        # Prorate price by actual filler width vs the catalog standard
        # width. Avoids over-charging for a 0.5" filler at the price of
        # a 6" panel.
        std_w = float(fp.get("width_in") or width_in or 1.0)
        if std_w <= 0:
            std_w = 1.0
        prorated = (fp.get("price") or 0.0) * (max(width_in, 0.5) / std_w)
        fp.update({
            "layout_key":    key,
            "x_position_in": x,
            "y_position_in": 0.0,
            "z_position_in": 0.0,
            "width_in":      width_in,
            "cabinet_type":  "filler",
            "price":         round(prorated, 2),
        })
        return fp

    def _snap_hint(self, rw, module_w):
        """When rw is 1-6 inches away from a module-clean total, suggest
        the nearest clean width so the rep can avoid filler altogether."""
        if not module_w or module_w <= 0:
            return None
        # Floor count
        n_floor = int(rw // module_w)
        clean_lo = n_floor * module_w
        clean_hi = (n_floor + 1) * module_w
        gap_lo = rw - clean_lo
        gap_hi = clean_hi - rw
        if gap_lo == 0:
            return None
        # Suggest hi (stretch) if rw is close enough to hi; else lo (shrink)
        if 0 < gap_hi <= 6.0:
            return {"target_width": clean_hi,
                    "delta":         round(gap_hi, 2),
                    "direction":     "expand"}
        if 0 < gap_lo <= 6.0:
            return {"target_width": clean_lo,
                    "delta":         round(-gap_lo, 2),
                    "direction":     "shrink"}
        return None

    # ── Save design ──────────────────────────────────────────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/save",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def save_design(self, name, room, items, partner_id=False, design_id=False):
        """
        Persist (or update) a KitchenDesign and its layout lines.

        Pass design_id to overwrite an existing record.

        D4 — auto-names new designs (no design_id, no real name) using
        the customer + room dims + date so the design tree view doesn't
        fill with "New Kitchen Design" collisions.
        """
        # PR2 — validate every incoming item's `wall` BEFORE any
        # create/write happens, so a bad payload never partially
        # writes (validate-all-items-first). `wall` is a layout-domain
        # input the pure kitchen_layout_engine consumes (P0.2,
        # 2026-07-11) — an unrecognised value here would otherwise
        # silently fall back to the model field's own "back" default,
        # masking a client bug. Same error-dict shape as the /layout
        # endpoint's NO_CABINETS response.
        for item in items or []:
            wall = item.get("wall") or "back"
            if wall not in kitchen_layout_engine.WALLS:
                _logger.warning(
                    "save_design: rejected payload — invalid wall=%r "
                    "(item layout_key=%s); must be one of %s. No "
                    "design/lines were created or modified.",
                    wall, item.get("layout_key"), kitchen_layout_engine.WALLS,
                )
                return {
                    "error": "invalid_wall",
                    "detail": (
                        "wall=%r is not a recognised room wall (must "
                        "be one of %s)."
                    ) % (wall, ", ".join(kitchen_layout_engine.WALLS)),
                }

        Design = request.env["southbrook.kitchen.design"]
        room_w = room.get("width_in",  12)
        room_d = room.get("depth_in",  24)
        room_h = room.get("height_in", 96)
        # Resolve the partner through the ACL-checked helper (empty recordset
        # if the caller may not read it) and persist THAT validated id — never
        # the raw client partner_id, which a user could point at any partner to
        # drive channel-pricelist resolution off someone else's record.
        partner = self._browse_partner(partner_id)
        if not design_id and (not name or name.strip() in (
            "", "Kitchen Design", "New Kitchen Design", "Untitled Kitchen",
        )):
            name = self._auto_name(partner, room_w, room_d)
        vals = {
            "name":           name or "Kitchen Design",
            "partner_id":     partner.id if partner else False,
            "room_width_in":  room_w,
            "room_depth_in":  room_d,
            "room_height_in": room_h,
            "state":          "configured",
        }

        # D9 — Non-destructive save. Update existing lines by
        # layout_key, create only new ones, unlink only those that
        # disappeared from the incoming set. Manual lines (added in
        # the backend form, origin='manual') are NEVER touched.
        Line = request.env["southbrook.kitchen.design.line"]
        if design_id:
            # IDOR guard mirroring save_position/delete_line: verify existence
            # + write access, degrade gracefully (no AccessError 500 oracle).
            design = Design.browse(int(design_id or 0))
            if not design.exists():
                return {"ok": False, "reason": "not_found"}
            try:
                design.check_access("write")
            except Exception:
                return {"ok": False, "reason": "forbidden"}
            design.write(vals)
            configurator_lines = design.cabinet_line_ids.filtered(
                lambda l: l.origin == "configurator"
            )
            existing_by_key = {
                l.layout_key: l for l in configurator_lines if l.layout_key
            }
        else:
            design = Design.create(vals)
            existing_by_key = {}

        # Panel-placement rule (Southbrook domain, 2026-07-01) — enforced
        # server-side so a client that skips the JS validator still can't
        # persist an end-cap panel pinned to a room wall. Rule:
        #   • cabinet_type == "filler" — auto-placed between cabinets by
        #     /layout; may contact a wall as a consequence of bridging the
        #     gap. No validation needed.
        #   • cabinet_type == "panel"  — decorative end cap; MUST attach to
        #     the left or right side face of a base or wall cabinet in the
        #     same design. Position must equal `host.x_position_in - width`
        #     (left cap) OR `host.x_position_in + host.width` (right cap).
        # Warnings are logged AND returned in the response so the client
        # can surface a banner. Persistence is not blocked (a rep may need
        # to save mid-configuration); the visual + JS-side error is the
        # primary UX signal.
        self._validate_end_cap_panel_placement(items)

        # 2026-07-01 E2E audit — enforce the /products catalog contract
        # on the save_design payload. The client posts product_ids
        # sourced from the /products response (main.py:31-34 filters on
        # southbrook_is_cabinet=True AND sale_ok=True), but the old
        # loop just browsed whatever id the client shipped: an authed
        # user could persist configurator lines pointing at archived,
        # non-cabinet, or ACL-restricted products (implicit info-
        # disclosure once /load_design_lines mirrors them back). Use
        # search() so ir.rule + domain filter apply in one shot —
        # rejected ids are logged + skipped per-line, the rest of the
        # save proceeds. This mirrors the ACL discipline already used
        # by save_position / delete_line / load_design_lines
        # (check_access at :420 / :459 / :492).
        # Server-side pricing: resolve the channel pricelist ONCE; each line is
        # priced from it below — the client's item["price"] is NEVER trusted
        # (HIGH-1 money vector: a tampered client could otherwise persist an
        # arbitrary price that flows verbatim into the quotation via
        # action_create_quotation, whose explicit price_unit is honoured over
        # the pricelist per its own kitchen_design.py comment).
        pricelist = self._resolve_pricelist(partner)
        Product = request.env["product.product"]
        # Batch the ACL-respecting catalog lookup into ONE search (was a
        # search() per item — 40-60 queries on a large save). Preserves the
        # /products contract (southbrook_is_cabinet=True AND sale_ok=True
        # AND readable by the caller) PLUS the narrow ctype='appliance'
        # admission below (Task 16 / SBK-028) — see that block's comment.
        wanted_ids = []
        for item in items:
            try:
                wanted_ids.append(int(item["product_id"]))
            except (KeyError, TypeError, ValueError):
                pass
        products_by_id = {}
        if wanted_ids:
            # Task 16 (SBK-028) — the is_cabinet gate above never admits
            # the SBK-APPL-* appliance-space stand-ins (southbrook_
            # is_cabinet=False BY DESIGN — they're excluded from the drag
            # catalog on purpose; see data/template_support_products.xml).
            # That meant a design instantiated from a template (which DOES
            # create real appliance lines — kitchen_template.py
            # action_instantiate) silently lost its appliance line on the
            # NEXT save from this 3D configurator page: the client re-
            # posts every line it currently has loaded, this search
            # rejected the appliance product_id, the line never made it
            # into incoming_keys below, and the removed-keys cleanup then
            # unlinked it as "stale" (prod-verified: design 102's RANGE
            # slot). Admit ctype='appliance' as a second, narrow OR-branch
            # — nothing else about the is_cabinet gate widens. The HIGH-1
            # money vector stays closed: price_unit is hard-pinned to 0.0
            # for every appliance line below, never routed through
            # _channel_price (a bigbox/fixed pricelist rule could
            # otherwise price a $0 stand-in at $98/SKU).
            for _p in Product.search([
                ("id", "in", wanted_ids),
                ("sale_ok", "=", True),
                "|",
                    ("product_tmpl_id.southbrook_is_cabinet", "=", True),
                    ("product_tmpl_id.southbrook_cabinet_type", "=", "appliance"),
            ]):
                products_by_id[_p.id] = _p
        incoming_keys = set()
        # PR4 — track every line this save actually created/wrote (by
        # layout_key) so the non-back-wall engine-placement pass below
        # can find them without a second search.
        saved_lines_by_key = {}
        # Task 9b — layout_keys this save wrote as sentinel/debris lines
        # (see _is_sentinel_position below). Excluded from the wall-
        # placement pass further down so the engine never computes a
        # real pose for a line we've just archived out of the design.
        sentinel_keys = set()
        for seq, item in enumerate(items, start=1):
            try:
                pid = int(item["product_id"])
            except (KeyError, TypeError, ValueError):
                _logger.warning(
                    "save_design: item[%d] missing/invalid product_id (%r); "
                    "skipping",
                    seq, item.get("product_id"),
                )
                continue
            product = products_by_id.get(pid)
            if not product:
                # Either the id doesn't exist, the caller can't read
                # it, the template isn't tagged as a Southbrook cabinet,
                # or it's not sale_ok. Reject in one branch — the
                # client never gets to distinguish "which of those"
                # (avoids existence-oracle leak; matches the
                # AccessError convention set by southbrook_estimating_
                # website/controllers/room_api.py:_get_room_scoped).
                _logger.warning(
                    "save_design: rejected product_id=%d on design=%s "
                    "(fails /products catalog contract: must be sale_ok="
                    "True AND readable by user AND either "
                    "southbrook_is_cabinet=True OR "
                    "southbrook_cabinet_type='appliance')",
                    pid, design.id,
                )
                continue
            tmpl    = product.product_tmpl_id
            layout_key = item.get("layout_key") or "auto-%d" % seq
            incoming_keys.add(layout_key)
            is_appliance = tmpl.southbrook_cabinet_type == "appliance"
            # Task 9b — server-side sentinel guard (Task 9 report,
            # "producer" section). The client's 1e6 sort-key sentinel
            # (kitchen_configurator.js `_addCabinetFromProduct`'s
            # `sortX` fallback) is meant to be transient, overwritten by
            # a real packed position before save — but `packRow` is
            # skipped whenever `multiWall` is true, so a newly-added
            # back-wall item can reach this endpoint still carrying it.
            # This save path previously wrote x/z_position_in verbatim,
            # letting the sentinel land in the DB as an active, priced,
            # "real" line (prod-verified: design 99). Any incoming
            # position at/beyond the debris threshold — in either axis,
            # regardless of sign — is never persisted as active,
            # positioned-for-billing, or priced: it's written archived +
            # is_unresolved so it never counts toward totals/price
            # (cabinet_line_ids excludes inactive lines by default) and
            # never silently vanishes (still a real row, still logged,
            # still visible to an admin who unarchives it to inspect).
            is_sentinel = (_is_sentinel_position(item.get("x_position_in", 0))
                           or _is_sentinel_position(item.get("z_position_in", 0)))
            if is_sentinel:
                sentinel_keys.add(layout_key)
                _logger.warning(
                    "save_design: sentinel/out-of-bounds position on "
                    "design=%s product=%s (%s, layout_key=%s) — "
                    "x=%r z=%r (threshold=%s). Persisting archived + "
                    "is_unresolved + unpriced instead of an active line.",
                    design.id, product.id, product.display_name,
                    layout_key, item.get("x_position_in"),
                    item.get("z_position_in"), _DEBRIS_THRESHOLD_IN,
                )
            line_vals = {
                "sequence":       seq * 10,
                "product_id":     product.id,
                "quantity":       1,
                # Task 16 (SBK-028) — appliance placeholders are ALWAYS
                # free: never route through _channel_price. Task 9b adds
                # the sentinel gate alongside it: neither an appliance
                # stand-in nor a debris line is ever priced from the
                # resolved channel pricelist.
                "price_unit":     (0.0 if (is_appliance or is_sentinel) else
                                    self._channel_price(product, pricelist, partner)),
                "active":         not is_sentinel,
                "is_unresolved":  is_sentinel,
                "cabinet_type":   tmpl.southbrook_cabinet_type,
                # Whitelist, not passthrough — an unrecognised selection
                # value would raise ValueError deep in Line.create and
                # crash the WHOLE batch save (matches the defensive
                # posture the "wall" field gets above); a value outside
                # the model's Selection just degrades to unset.
                "appliance_type": (item.get("appliance_type")
                                    if item.get("appliance_type") in (
                                        "range", "fridge", "dishwasher",
                                        "hood", "other")
                                    else False),
                "width_in":       item.get("width_in",  tmpl.southbrook_width_in or 24.0),
                "height_in":      item.get("height_in", tmpl.southbrook_height_in or 34.5),
                "depth_in":       item.get("depth_in",  tmpl.southbrook_depth_in or 24.0),
                "x_position_in":  item.get("x_position_in", 0),
                "y_position_in":  item.get("y_position_in", 0),
                "z_position_in":  item.get("z_position_in", 0),
                # v19.0.4.20.0 — Smart-pinning (Option C): persist per-line
                # manual-move + rotation so a reload restores the user's
                # placement instead of the /layout auto-generated one.
                "pinned":         bool(item.get("pinned")),
                "rotation_deg":   float(item.get("rotation_deg") or 0.0),
                "layout_key":     layout_key,
                "origin":         "configurator",
                # PR2 — persist the layout-domain wall assignment.
                # Already validated against kitchen_layout_engine.WALLS
                # above; re-derive the same default here for items that
                # omitted the key.
                "wall":           item.get("wall") or "back",
            }
            if layout_key in existing_by_key:
                line = existing_by_key[layout_key]
                line.write(line_vals)
            else:
                line = Line.create({"design_id": design.id, **line_vals})
            saved_lines_by_key[layout_key] = line

        # Unlink configurator-origin lines that the user removed in the
        # 3D pane. Manual-origin lines are excluded from existing_by_key
        # so they survive every save.
        removed_keys = set(existing_by_key) - incoming_keys
        if removed_keys:
            stale = Line.browse([])
            for k in removed_keys:
                stale |= existing_by_key[k]
            stale.unlink()

        # PR4 (2026-07-12) — a cabinet added/moved onto a non-back wall
        # (state.activeWall in the client) was, until now, persisted at
        # the raw CLIENT-computed x/y/z/rotation — `_addCabinetFromProduct`
        # only ever computes that correctly for the BACK wall (see
        # docs/2026-07-12-renderer-contract.md). Route every non-back-wall
        # configurator line through the SAME engine-delegation path the
        # website's 3D tab already uses (`_place_lines_on_wall`, shared on
        # the southbrook.kitchen.design model), overwriting the client's
        # guess with the pure kitchen_layout_engine's pose. Back-wall
        # lines are LEFT ALONE — the client's own back-wall packing is
        # correct today and must not change (backward compat).
        # Task 9b — a sentinel line is archived + unpriced on purpose;
        # never route it through the engine-placement pass, which would
        # otherwise compute and write it a real pose (undoing the guard
        # above for its x/y/z, even though `active`/`is_unresolved`/
        # `price_unit` would still hold). Its debris position is left
        # exactly as the client sent it, same as the migration leaves
        # existing debris rows untouched apart from `active`.
        wall_lines = Line.browse([])
        for key, line in saved_lines_by_key.items():
            if key in sentinel_keys:
                continue
            if (line.wall or "back") != "back":
                wall_lines |= line
        placed = []
        if wall_lines:
            poses = design._place_lines_on_wall(wall_lines)
            for line in wall_lines:
                pose = poses.get(line.id)
                if not pose:
                    continue
                placed.append({
                    "layout_key":     line.layout_key,
                    "x_position_in":  pose["x_position_in"],
                    "y_position_in":  pose["y_position_in"],
                    "z_position_in":  pose["z_position_in"],
                    "rotation_deg":   pose["rotation_deg"],
                })

        # C2 — corner resolution. The website portal's add route
        # (southbrook_estimating_website/controllers/main.py ~2894-2905)
        # already calls design.action_auto_arrange(sync=False) the moment
        # a design has cabinets on 2+ walls, so an inside corner gets a
        # real SB-CORNER / SB-WALL-CORNER cabinet instead of two runs
        # silently interpenetrating. This page had no equivalent hook —
        # mirror that pattern here, once per save, after the PR4
        # non-back-wall placement pass above has settled every line's
        # wall assignment.
        relaid = False
        layout_warning = None
        walls_used = {
            (dl.wall or "back")
            for dl in design.cabinet_line_ids.filtered(
                lambda l: l.origin == "configurator"
                and l.layout_role != "derived"
                and l.cabinet_type not in ("filler", "panel"))
        }
        if len(walls_used) >= 2:
            try:
                design.action_auto_arrange(sync=False)
                relaid = True
            except LayoutCapacityExceeded as e:
                # action_auto_arrange runs inside its own cr.savepoint() —
                # this exception means it already rolled itself back, so
                # the design as saved above is untouched/still persisted.
                # relaid stays False: nothing changed for the client to
                # pull, but we still owe the rep a plain-English reason
                # the corner didn't resolve.
                outside = getattr(e, "outside", []) or []
                if outside:
                    worst_overflow_in = max(
                        (o.get("overflow_mm") or 0) for o in outside
                    ) / 25.4
                    layout_warning = (
                        "Your cabinet run is too long for the wall: %d "
                        "cabinet(s) don't fit (worst overflows ~%.1f in). "
                        "Remove a cabinet or enlarge the room."
                    ) % (len(outside), round(worst_overflow_in, 1))
                else:
                    layout_warning = (
                        "Your cabinet run is too long for the wall. "
                        "Remove a cabinet or enlarge the room."
                    )
        elif design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
        ):
            # Fewer than 2 walls now in use but stale derived corner lines
            # remain (e.g. the rep deleted cabinets back down to a single
            # wall since the last auto-arrange) — clean up so the scene
            # doesn't keep showing a corner cabinet with no corner. Mirrors
            # action_auto_arrange's own reset block (kitchen_design.py
            # ~461-477): restore archived canonical lines, then unlink the
            # derived ones.
            #
            # Task 9b (review round 2) — THIRD reactivation site with the
            # same class of bug the first two had: a two-save sequence
            # (save #1 multi-wall, creates derived corners; save #2
            # single-wall, containing/after a guarded sentinel line) hits
            # this elif branch and would blanket-reactivate the sentinel
            # right along with the legitimately-superseded canonicals.
            # Excluded via the same DEBRIS-SIGNATURE predicate the two
            # kitchen_design.py reset sites use (archived + is_unresolved
            # + at/beyond DEBRIS_THRESHOLD_IN on x or z —
            # SouthbrookKitchenDesignLine._is_debris_signature) — NOT a
            # bare `not l.is_unresolved`, which would also strand
            # legitimate template GHOST placeholder lines (also
            # is_unresolved=True, but ordinary active cabinets that
            # participate in arrange and must remain revivable).
            ctx = design.with_context(active_test=False)
            ctx.cabinet_line_ids.filtered(
                lambda l: not l.active and not l._is_debris_signature()
            ).write({"active": True})
            design.invalidate_recordset(["cabinet_line_ids"])
            ctx.cabinet_line_ids.filtered(
                lambda l: l.layout_role == "derived").unlink()
            relaid = True

        if relaid:
            design.invalidate_recordset()

        return {
            "id":     design.id,
            "name":   design.display_name,
            # PR4 — engine-updated poses for the wall lines this save
            # moved, so the client can apply them onto state.items and
            # re-render immediately without a reload.
            "placed": placed,
            # C2 — when the corner engine (or the stale-derived-line
            # cleanup) changed the line set/geometry, `lines` carries the
            # authoritative post-save state (same shape as
            # load_design_lines) so the client can replace state.items
            # wholesale instead of trusting its own pre-save guess.
            "relaid":         relaid,
            "lines":          self._serialize_design_lines(design) if relaid else None,
            "layout_warning": layout_warning,
        }

    # ── v19.0.4.20.0 · Smart-pinning RPC surface ─────────────────────────────────
    # Three lightweight routes so the client can persist a single
    # cabinet's placement (or unlink it) without triggering the full
    # save_design write-cycle. Called on pointer-up after a drag, on
    # R-key rotation, and on Delete/Backspace.
    #
    #   /load_design_lines  → returns the design's configurator-origin
    #                         lines as {layout_key: item-dict} so the
    #                         client can rehydrate a saved manual
    #                         placement instead of throwing it away.
    #   /save_position      → writes x_position_in / y_position_in /
    #                         z_position_in / rotation_deg / pinned on
    #                         a single line, matched by layout_key.
    #   /delete_line        → unlinks a single configurator-origin
    #                         line, matched by layout_key.
    #
    # None of these touch manual-origin lines. save_position + delete_line
    # silently no-op when the target design/line isn't found or the
    # calling user lacks write access — surfacing an error would hijack
    # the drag UX.
    @http.route(
        "/southbrook_kitchen/configurator/load_design_lines",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def load_design_lines(self, design_id):
        # Task 20 (SBK-034) — deep-link source-of-truth. A design id
        # that doesn't resolve now comes back with an explicit
        # `error`/`error_code` (NOT_FOUND / ACCESS_DENIED — same code
        # strings /rearrange already uses below) instead of the old
        # bare `{"lines": []}`. The client used to read an empty
        # `lines` list as "a real, freshly-created design with nothing
        # saved yet" and silently ran the starter-layout generator over
        # it — indistinguishable from "this id doesn't exist / isn't
        # yours", which is exactly the audited "reload silently resets
        # to a fresh design, abandoning the saved one" bug (SBK-034).
        # A design that genuinely EXISTS and is READABLE, even with
        # zero configurator-origin lines (e.g. a template preset saved
        # with generate_layout=False), is UNCHANGED below — that empty-
        # but-real case still returns the bare success shape; only the
        # two short-circuits above it are new.
        design = request.env["southbrook.kitchen.design"].browse(int(design_id or 0))
        if not design.exists():
            return {
                "lines": [],
                "error": "This design no longer exists. It may have "
                         "been deleted.",
                "error_code": "NOT_FOUND",
            }
        try:
            design.check_access("read")
        except Exception:
            return {
                "lines": [],
                "error": "You don't have access to this design.",
                "error_code": "ACCESS_DENIED",
            }
        out = self._serialize_design_lines(design)
        # PR2.5a (Gap 1) — additive: a direct-URL reload of the
        # configurator restores the client action's `design_id` (via the
        # actionStack fallback in kitchen_configurator.js) but NOT the
        # room dims / design name that normally arrive via
        # action_open_configurator's `params` (models/kitchen_design.py
        # action_open_configurator). Echo them here, read-side only, so
        # _hydrateFromDesign() can backfill state.room / state.designName
        # when a URL restore skipped params entirely. The no-design /
        # no-access branches above now short-circuit with an
        # error/error_code (Task 20, SBK-034) before reaching here —
        # this key is only ever added when a design was actually found
        # and readable.
        return {
            "lines": out,
            "room": {
                "width_in":  design.room_width_in,
                "depth_in":  design.room_depth_in,
                "height_in": design.room_height_in,
            },
            "design_name": design.display_name,
        }

    # ─── Task 20 (SBK-034) — landing-state recent designs ───────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/recent_designs",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def recent_designs(self, limit=8):
        """The current user's most-recently-touched designs, for the
        configurator's landing screen (opened with no design id, e.g.
        the bare menu entry) so it can offer "Continue <design>"
        instead of the old silent-auto-generate-a-fresh-design
        behaviour. Deliberately minimal — id + a few display fields,
        no line data (the landing screen never touches
        cabinet_line_ids). A plain non-sudo search: ir.rule already
        does the right per-role scoping everywhere else in this
        controller (security/kitchen_design_rules.xml — designer sees
        own via create_uid, readonly/manager see all); no special-
        casing needed here either.
        """
        try:
            n = max(1, min(int(limit or 8), 25))
        except (TypeError, ValueError):
            n = 8
        designs = request.env["southbrook.kitchen.design"].search(
            [], order="write_date desc", limit=n,
        )
        return [{
            "id":            d.id,
            "name":          d.display_name,
            "partner_name":  d.partner_id.name or "",
            "room_width_in":  d.room_width_in,
            "room_depth_in":  d.room_depth_in,
            "room_height_in": d.room_height_in,
            "write_date":    d.write_date.isoformat() if d.write_date else "",
        } for d in designs]

    # T7 (kitchen templates) — engine-routed room resize. The naive
    # /layout generator above is a single-wall fill: calling it on a
    # SAVED design's room resize regenerates (and on save, replaces)
    # the canonical multi-wall layout. This route is the honest path:
    # write the dims, re-derive every pose through action_auto_arrange
    # (corner engine included), and hand back the same line emission
    # load_design_lines uses. A resize the cabinets can't fit REVERTS
    # the dims and reports ROOM_TOO_SMALL — the room is never left in a
    # non-fitting state.
    @http.route(
        "/southbrook_kitchen/configurator/rearrange",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def rearrange(self, design_id, room=None):
        design = request.env["southbrook.kitchen.design"].browse(
            int(design_id or 0)).exists()
        if not design:
            return {"error": "Design not found", "error_code": "NOT_FOUND"}
        try:
            design.check_access("write")
        except Exception:
            return {"error": "You don't have write access to this design.",
                    "error_code": "ACCESS_DENIED"}
        prev_room = {
            "width_in": design.room_width_in,
            "depth_in": design.room_depth_in,
            "height_in": design.room_height_in,
        }
        pre_active_ids = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "canonical").ids
        if room:
            design.write({
                "room_width_in": float(
                    room.get("width_in") or prev_room["width_in"]),
                "room_depth_in": float(
                    room.get("depth_in") or prev_room["depth_in"]),
                "room_height_in": float(
                    room.get("height_in") or prev_room["height_in"]),
            })
        try:
            design.action_auto_arrange(sync=False)
            # T5 lesson — the engine "fits" impossible runs by ARCHIVING
            # cabinets, it does not raise. pre_active_ids only holds
            # lines that were ACTIVE going in (already-substituted
            # corner leads are not in it), so any of them inactive now
            # means this resize FORCED a drop: refuse it.
            dropped = request.env["southbrook.kitchen.design.line"] \
                .with_context(active_test=False) \
                .browse(pre_active_ids).filtered(lambda l: not l.active)
            if room and dropped:
                raise LayoutCapacityExceeded(
                    0, 0, detail="%d cabinet(s) would be dropped" %
                    len(dropped))
        except LayoutCapacityExceeded as e:
            # arrange rolled ITSELF back (its own savepoint) — the dims
            # write above is ours to revert (never persist a resize the
            # cabinets don't fit). Then re-arrange at the ORIGINAL dims
            # (they fit before, so this is a restore, best-effort).
            design.write({
                "room_width_in": prev_room["width_in"],
                "room_depth_in": prev_room["depth_in"],
                "room_height_in": prev_room["height_in"],
            })
            try:
                design.action_auto_arrange(sync=False)
            except LayoutCapacityExceeded:
                pass
            return {"error": "That room size doesn't fit the current "
                             "cabinets (%s). The room was not changed — "
                             "remove a cabinet first." % e,
                    "error_code": "ROOM_TOO_SMALL",
                    "room": prev_room}
        return {
            "lines": self._serialize_design_lines(design),
            "room": {
                "width_in": design.room_width_in,
                "depth_in": design.room_depth_in,
                "height_in": design.room_height_in,
            },
        }

    @http.route(
        "/southbrook_kitchen/configurator/save_position",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def save_position(self, design_id, layout_key, x_position_in=None,
                      y_position_in=None, z_position_in=None,
                      rotation_deg=None, pinned=None):
        design = request.env["southbrook.kitchen.design"].browse(int(design_id or 0))
        if not design.exists() or not layout_key:
            return {"ok": False, "reason": "not_found"}
        try:
            design.check_access("write")
        except Exception:
            return {"ok": False, "reason": "forbidden"}
        line = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
                      and l.layout_key == layout_key
        )[:1]
        if not line:
            return {"ok": False, "reason": "no_matching_line"}
        vals = {}
        if x_position_in is not None:
            vals["x_position_in"] = float(x_position_in)
        if y_position_in is not None:
            vals["y_position_in"] = float(y_position_in)
        if z_position_in is not None:
            vals["z_position_in"] = float(z_position_in)
        if rotation_deg is not None:
            vals["rotation_deg"] = float(rotation_deg) % 360.0
        if pinned is not None:
            vals["pinned"] = bool(pinned)
        if vals:
            line.write(vals)
        return {"ok": True, "id": line.id}

    @http.route(
        "/southbrook_kitchen/configurator/delete_line",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def delete_line(self, design_id, layout_key):
        design = request.env["southbrook.kitchen.design"].browse(int(design_id or 0))
        if not design.exists() or not layout_key:
            return {"ok": False, "reason": "not_found"}
        try:
            design.check_access("write")
        except Exception:
            return {"ok": False, "reason": "forbidden"}
        line = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
                      and l.layout_key == layout_key
        )
        n = len(line)
        line.unlink()
        return {"ok": True, "removed": n}

    # ── Helpers ──────────────────────────────────────────────────────────────────
    def _serialize_design_lines(self, design):
        """Return this design's configurator-origin lines as the same list
        of item dicts load_design_lines has always returned.

        Factored out (C2) so save_design can hand the client fresh server
        geometry after a corner-resolution pass without duplicating the
        field list — keep this the single source of truth for the
        line → item-dict shape. Behaviour must stay byte-identical to the
        pre-factor inline loop in load_design_lines.
        """
        lines = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
        )
        out = []
        for line in lines:
            product = line.product_id
            out.append({
                "id":             product.id,
                "product_id":     product.id,
                "product_name":   product.display_name,
                # Task 8 fix (corner-remediation code review) — the 3D
                # renderer's corner-cabinet builder
                # (canvas/corner_cabinet.esm.js) picks its style
                # (blind/pie-cut/diagonal) off this line's product
                # default_code prefix. Send it directly instead of
                # requiring the client to cross-reference the
                # /products catalog: derived corner-node lines
                # (SB-WALL-CORNER / SB-CORNER-BLIND / SB-CORNER-*) can
                # legitimately reference a product outside the
                # panel-catalog's southbrook_is_cabinet=True set (that
                # catalog gate is for droppable inventory, not for
                # every product a line can ever point at), so a
                # client-side catalog lookup can never be relied on to
                # resolve these. Same access boundary as
                # product_name/display_name above — no sudo.
                "default_code":   product.default_code or None,
                "layout_key":     line.layout_key,
                "cabinet_type":   line.cabinet_type,
                "width_in":       line.width_in,
                "height_in":      line.height_in,
                "depth_in":       line.depth_in,
                "x_position_in":  line.x_position_in,
                "y_position_in":  line.y_position_in,
                "z_position_in":  line.z_position_in,
                "rotation_deg":   line.rotation_deg,
                "pinned":         line.pinned,
                "price":          line.price_unit,
                "quantity":       line.quantity,
                # PR2 — read-side emission. NULL on pre-PR2 rows (no
                # migration) reads back as "back" for backward compat,
                # matching the model field's own default.
                "wall":           line.wall or "back",
                # Task 16 (SBK-028) — round-trip appliance_type so a
                # load -> unmodified resave doesn't blank it out (save_design
                # now writes this field for cabinet_type='appliance' lines).
                "appliance_type": line.appliance_type or False,
            })
        return out

    def _validate_end_cap_panel_placement(self, items, tol=0.5):
        """Log a warning for any `cabinet_type == "panel"` item whose
        x_position_in does not correspond to a host cabinet's exposed
        side face.

        A valid end-cap position matches EITHER:
            host.x_position_in - panel.width_in   (left cap)
            host.x_position_in + host.width_in    (right cap)
        where host is any item with cabinet_type in ("base", "wall").

        This is warn-only (not blocking) because a sales rep may need to
        save mid-configuration. The client-side validator + the visual
        3D render are the primary UX signals. See kitchen_configurator.js
        _validateEndCapPlacement for the client twin.

        Filler panels ("filler") are auto-placed between cabinets by the
        /layout endpoint (see D7 filler strategies) and may contact a
        wall as a consequence of bridging the last gap; they do NOT
        require this validation.
        """
        panels = [it for it in (items or [])
                  if it.get("cabinet_type") == "panel"]
        if not panels:
            return
        hosts = [it for it in items
                 if it.get("cabinet_type") in ("base", "wall")]
        valid_x_positions = []
        for h in hosts:
            hx = float(h.get("x_position_in", 0) or 0)
            hw = float(h.get("width_in", 0) or 0)
            valid_x_positions.append(hx + hw)   # right side of host
            for p in panels:
                pw = float(p.get("width_in", 0) or 0)
                valid_x_positions.append(hx - pw)   # left side of host
        for p in panels:
            px = float(p.get("x_position_in", 0) or 0)
            if not any(abs(px - v) <= tol for v in valid_x_positions):
                _logger.warning(
                    "End cap panel product_id=%s layout_key=%s at "
                    "x_position_in=%s is not adjacent to any cabinet "
                    "side face. Panels must never attach to room walls; "
                    "they attach only to the left or right side of a "
                    "base or wall cabinet.",
                    p.get("product_id"), p.get("layout_key"), px,
                )

    def _first_product(self, env, cabinet_type):
        return env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet",    "=", True),
            ("product_tmpl_id.southbrook_cabinet_type",  "=", cabinet_type),
            ("sale_ok", "=", True),
        ], limit=1)

    def _layout_item(self, product, index, x, y, z, pricelist=False, partner=False):
        payload = self._product_payload(product, pricelist, partner)
        payload.update({
            "layout_key":    "%s-%d" % (payload["cabinet_type"], index + 1),
            "x_position_in": x,
            "y_position_in": y,
            "z_position_in": z,
        })
        return payload

    def _product_payload(self, product, pricelist=False, partner=False):
        tmpl = product.product_tmpl_id
        price = self._channel_price(product, pricelist, partner)
        payload = {
            "product_id":    product.id,
            "template_id":   tmpl.id,
            "name":          product.display_name,
            "sku":           product.default_code or "",
            "cabinet_type":  tmpl.southbrook_cabinet_type,
            "width_in":      tmpl.southbrook_width_in  or 24.0,
            "height_in":     tmpl.southbrook_height_in or 34.5,
            "depth_in":      tmpl.southbrook_depth_in  or 24.0,
            "material":      tmpl.southbrook_material   or "white_melamine",
            "door_style":    tmpl.southbrook_door_style or "shaker",
            "price":         price,
            "list_price":    product.lst_price,
            "available_qty": product.qty_available,
            "bom_available": tmpl.southbrook_bom_available,
            "image_url":     "/web/image/product.product/%d/image_128" % product.id,
            "asset_url":     tmpl.southbrook_3d_asset_url or "",
        }
        # D17 — Tier-B additive: enrich with archetype taxonomy when
        # the southbrook_estimating archetype map is set. x_prodboard_
        # archetype_id is the canonical M2O to southbrook.cabinet.
        # archetype (223 records: body_class / collection / code from
        # the Prodboard manifest clone). Falls back silently when the
        # template isn't mapped to an archetype.
        # 2026-07-01 audit cleanup — southbrook_estimating became a
        # hard dep on 2026-06-28 (__manifest__.py:22-26), so
        # x_prodboard_archetype_id is guaranteed present at runtime.
        # The hasattr guard was defensive from before the manifest
        # hardened; dead code now.
        archetype = tmpl.x_prodboard_archetype_id or None
        if archetype:
            payload["archetype_code"]       = getattr(archetype, "code", "") or ""
            payload["archetype_body_class"] = getattr(archetype, "body_class", "") or ""
            payload["archetype_collection"] = getattr(archetype, "collection", "") or ""
            # Prefer the archetype-supplied canonical image when set —
            # vendor renders are richer than auto-generated thumbnails.
            canon_img = getattr(archetype, "canonical_image_url", "") or ""
            if canon_img:
                payload["archetype_image_url"] = canon_img
        return payload

    # ─── D3 — channel pricelist resolution ──────────────────────────────────────
    # Centralise partner/pricelist plumbing so /products + /layout
    # share the same resolver and the UI gets a single channel-meta
    # block to render the topbar badge.

    def _browse_partner(self, partner_id):
        """Resolve partner_id to a browsable res.partner while enforcing
        the caller's ACL + record rules. Empty recordset means 'no
        partner, use retail' — never raises so /products keeps
        rendering.

        v19.0.4.22.0 audit P1#5: pre-4.22 this method .sudo()'d the
        browse, letting any authenticated user enumerate every
        partner in the DB via /products or /layout and leak their
        .channel / pricing tier through _channel_meta +
        _resolve_pricelist. The check_access("read") gate degrades
        strangers to retail silently rather than raising (an error
        would leak existence and would break inventory rendering
        for logged-in customers whose portal_user has no partner
        access at all).
        """
        if not partner_id:
            return request.env["res.partner"]
        try:
            pid = int(partner_id)
        except (TypeError, ValueError):
            return request.env["res.partner"]
        if pid <= 0:
            return request.env["res.partner"]
        partner = request.env["res.partner"].browse(pid).exists()
        if not partner:
            return request.env["res.partner"]
        try:
            partner.check_access("read")
        except Exception:
            # Silent degrade — never raise AccessError; would leak
            # existence and would break configurator rendering.
            return request.env["res.partner"]
        return partner

    def _resolve_pricelist(self, partner):
        # Reuses the canonical southbrook_estimating dispatcher so the
        # configurator's live preview matches the price the Order
        # Builder + spec sheet PDF will charge. Falls back to retail
        # when no partner.
        # 2026-07-01 audit cleanup — southbrook_estimating is a hard
        # dep since 2026-06-28, so _resolve_channel_pricelist is
        # guaranteed present. Try/except kept as it protects against
        # partner-side data problems (missing channel, invalid tier).
        SaleOrder = request.env["sale.order"]
        try:
            return SaleOrder._resolve_channel_pricelist(partner)
        except Exception:
            pass
        # Fallback path: partner's property pricelist, else env default.
        if partner:
            pl = partner.property_product_pricelist
            if pl:
                return pl
        return request.env["product.pricelist"].search([], limit=1)

    def _channel_price(self, product, pricelist, partner):
        if not pricelist:
            return product.lst_price
        try:
            # v19 unified API: pricelist._get_product_price(product, qty, partner)
            return pricelist._get_product_price(product, 1.0, partner or False)
        except Exception:
            return product.lst_price

    def _channel_meta(self, partner, pricelist):
        # Compact dict the UI renders into a topbar badge.
        channel = (partner and partner.channel) or "retail"
        # Best-effort tier suffix for tradesperson.
        suffix = ""
        if channel == "tradesperson" and partner:
            tier = getattr(partner, "tradesperson_tier", False)
            if tier:
                suffix = " T%s" % tier
        return {
            "partner_id":     (partner and partner.id) or False,
            "partner_name":   (partner and partner.display_name) or "",
            "channel":        channel,
            "channel_label":  self._CHANNEL_LABELS.get(channel, channel.title()) + suffix,
            "pricelist_id":   (pricelist and pricelist.id) or False,
            "pricelist_name": (pricelist and pricelist.display_name) or "",
            "currency_id":    (pricelist and pricelist.currency_id.id) or False,
            "currency_symbol": (pricelist and pricelist.currency_id.symbol) or "$",
        }

    _CHANNEL_LABELS = {
        "retail":       "Retail",
        "dealer":       "Dealer -50%",
        "tradesperson": "Contractor",
        "kd":           "KD",
        "bigbox":       "Big-Box",
        "refacing":     "Refacing",
    }

    # ─── D4 — auto-name + per-user sticky room defaults ─────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/user_defaults",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def user_defaults(self, save=False, width=None, depth=None, height=None):
        """Read or write the current user's preferred room dimensions.

        Stored as ir.config_parameter keyed by uid so each rep gets
        their own sticky defaults; no res.users schema bump required.
        """
        ICP = request.env["ir.config_parameter"].sudo()
        uid = request.env.uid
        keys = {
            "w": "southbrook_kitchen.default_room_width_in.%d"  % uid,
            "d": "southbrook_kitchen.default_room_depth_in.%d"  % uid,
            "h": "southbrook_kitchen.default_room_height_in.%d" % uid,
        }
        if save:
            if width  is not None: ICP.set_param(keys["w"], str(width))
            if depth  is not None: ICP.set_param(keys["d"], str(depth))
            if height is not None: ICP.set_param(keys["h"], str(height))

        def _read(k, default):
            try:
                return float(ICP.get_param(keys[k], default))
            except Exception:
                return default
        return {
            "width":  _read("w", 12),
            "depth":  _read("d", 24),
            "height": _read("h", 96),
        }

    def _auto_name(self, partner, room_w, room_d):
        from odoo import fields as _fields
        today = _fields.Date.context_today(request.env.user).strftime("%Y-%m-%d")
        pname = (partner and partner.display_name) or "Walk-in"
        return "%s - %dx%d - %s" % (pname, int(room_w or 0), int(room_d or 0), today)
