# SPDX-License-Identifier: LGPL-3.0-only
"""Recommendation D — Sprint 1 reconciliation cron.

One-way mirror: `southbrook.kitchen.design(.line)` (source of truth in
Sprint 1) → `southbrook.room` + `southbrook.room.wall` +
`sale.order.line` (Rec D authoritative graph from Sprint 2 onward).

Runs every 5 minutes. Only touches designs whose write_date is newer
than the last successful run (stored in ir.config_parameter). Logs
divergence between design.estimated_price and the summed
sale.order line totals but does NOT auto-heal — flagged for manual
review during Sprint 1 to build confidence before Sprint 2 flips the
direction.

Sprint 2 will add the reverse cron; Sprint 3 will delete this cron
after retiring the design model.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# 25.4 mm per inch — canonical conversion used both here and in
# southbrook_estimating's room_api.py for cross-controller parity.
IN_TO_MM = 25.4

# Config-param key tracking the last successful cron run so re-runs
# skip untouched designs. Never manually reset — use TaskUpdate on
# the cron record itself to force a full re-scan.
_LAST_RUN_KEY = "southbrook.rec_d.design_reconcile.last_run"

# ── Wall identity, shape derivation (SBK-041, 2026-08-03) ──────────
#
# Until now this cron created exactly ONE synthetic wall per room
# ("Wall A", spanning room_width_in) and hardcoded layout_shape to
# "straight" at room-creation time, never revisiting it. The Sprint-1
# comment below step 3 said as much: multi-wall shapes were deferred to
# a Sprint 2 that reached the reverse cron but never reached this.
#
# The cost was not cosmetic. Two separate defects in the 2026-08-03 E2E
# review are the same line of code:
#   * every U/L design was stored as layout_shape="straight",
#     wall_count=1 — 7 of the 8 bridged designs contradicted their own
#     geometry, so quote, BOM and plan all described a straight kitchen;
#   * every sale.order.line was pinned to that one wall, so wall
#     information never survived the trip to the quote.
#
# wall_order doubles as the wall's stable identity, so re-running this
# is idempotent and an existing "Wall A" (always order 10) is adopted as
# the back wall rather than duplicated.
_WALL_META = {
    #  key   → (display name,  wall_order, which room dimension spans it)
    "back":  ("Back Wall",  10, "width"),
    "right": ("Right Wall", 20, "depth"),
    "front": ("Front Wall", 30, "width"),
    "left":  ("Left Wall",  40, "depth"),
}

# Two runs facing each other across the room is a galley; two meeting at
# a corner is an L. This is standard cabinetry vocabulary, not a rule
# invented here — see _LAYOUT_SHAPES in southbrook_estimating.
_OPPOSITE_WALL_PAIRS = ({"back", "front"}, {"left", "right"})

# Shapes this cron is competent to derive from a wall set. Anything else
# a human chose on the room form (island, peninsula, custom) expresses
# something a set of walls cannot, so we never overwrite it — see
# _sb_shape_write_vals.
_DERIVABLE_SHAPES = {"straight", "l_shape", "u_shape", "galley", "g_shape"}


def _sb_walls_used_by(design):
    """The distinct walls this design's configurator lines actually occupy.

    Returns a set of wall keys. An empty design (or one whose lines all
    predate the `wall` field) reconciles as a single back wall, which is
    the historical behaviour and the honest reading of "no information".
    """
    used = {
        line.wall
        for line in design.cabinet_line_ids
        if line.origin == "configurator" and line.wall in _WALL_META
    }
    return used or {"back"}


def _sb_derive_layout_shape(wall_keys):
    """Map a set of occupied walls onto a layout shape.

    Deliberately conservative: it reports what the geometry supports and
    nothing more. Four walls is reported as a G-shape rather than
    guessing at an island, because a wall set cannot tell the two apart.
    """
    walls = set(wall_keys)
    count = len(walls)
    if count <= 1:
        return "straight"
    if count == 2:
        return "galley" if walls in _OPPOSITE_WALL_PAIRS else "l_shape"
    if count == 3:
        return "u_shape"
    return "g_shape"


def _sb_shape_write_vals(room, derived_shape):
    """{'layout_shape': ...} when it is ours to correct, else {}.

    Never overwrites a shape a human picked that this cron could not
    have produced (island / peninsula / custom). Those carry intent the
    wall set does not encode, and silently flattening them to "l_shape"
    would be exactly the kind of unrequested edit this product is trying
    to stop making.
    """
    current = room.layout_shape
    if current and current not in _DERIVABLE_SHAPES:
        return {}
    if current == derived_shape:
        return {}
    return {"layout_shape": derived_shape}


class SouthbrookDesignReconcile(models.AbstractModel):
    """Rec D Sprint 1 · one-way mirror design → room + SO line graph.

    Abstract because there's no persistent state — the cron just
    iterates recent designs and writes into the target graph.
    """

    _name = "southbrook.design.reconcile"
    _description = "Rec D Sprint 1 · Design → Room reconciliation"

    # ── Public cron entrypoint ─────────────────────────────────────
    @api.model
    def cron_reconcile_designs(self, batch_limit=200):
        """Runs every 5 min via ir.cron. Reads modified designs, mirrors
        them into rooms + sale.order.line. Never destroys authoring
        data on the design side; logs divergence for manual review.
        """
        params = self.env["ir.config_parameter"].sudo()
        last_run_iso = params.get_param(_LAST_RUN_KEY) or "1970-01-01 00:00:00"

        Design = self.env["southbrook.kitchen.design"]
        domain = [("write_date", ">", last_run_iso)]
        designs = Design.search(domain, order="write_date", limit=batch_limit)

        # Stamp the new watermark BEFORE writing so a partial run doesn't
        # re-process the same batch. Any failures inside the loop are
        # logged individually; the cron self-heals on the next tick.
        if designs:
            new_watermark = max(d.write_date for d in designs)
            params.set_param(_LAST_RUN_KEY, str(new_watermark))

        stats = {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                 "created_walls": 0, "created_lines": 0, "divergence": 0,
                 "errors": 0}
        for design in designs:
            # Per-design SAVEPOINT so a failure on one design doesn't
            # poison the whole batch (Postgres aborts the outer txn on
            # any error — savepoints let us rollback to a clean state
            # and continue with the next design).
            try:
                with self.env.cr.savepoint():
                    self._reconcile_one(design, stats)
            except Exception as e:                      # noqa: BLE001
                _logger.warning(
                    "[sb.rec_d] reconcile failed for design id=%s (%s): %s",
                    design.id, design.name, e,
                    exc_info=True,
                )
                stats["errors"] += 1

        if designs:
            _logger.info(
                "[sb.rec_d] reconciled batch of %d designs: %s",
                len(designs), stats,
            )
        return stats

    # ── Per-design reconciliation ──────────────────────────────────
    def _reconcile_one(self, design, stats):
        # 1) Ensure the design has a sale.order shell. Copies the
        #    channel-priced structure from action_create_quotation
        #    for a design that hasn't been quoted yet.
        SaleOrder = self.env["sale.order"]
        if not design.sale_order_id:
            partner = design.partner_id
            # sale.order.partner_id has a NOT NULL DB constraint. All
            # 18 existing designs at the time of Sprint-1 rollout are
            # partner-less scratchpads (per the 2026-07-01 audit data-
            # hygiene finding). Skip them until a customer is
            # assigned — the next cron tick after that write picks
            # them up automatically.
            if not partner:
                _logger.info(
                    "[sb.rec_d] skipping design id=%s — no partner "
                    "assigned yet (will reconcile once customer is "
                    "set)", design.id,
                )
                stats["skipped_no_partner"] = stats.get("skipped_no_partner", 0) + 1
                return
            vals = {
                "partner_id":  partner.id,
                "origin":      design.name,
                "note":        design.notes or "",
                "state":       "draft",
            }
            # 2026-07-01 audit cleanup — southbrook_estimating is a
            # hard dep, so _resolve_channel_pricelist is guaranteed
            # to be defined at reconcile-time.
            pricelist = SaleOrder._resolve_channel_pricelist(partner)
            if pricelist:
                vals["pricelist_id"] = pricelist.id
            order = SaleOrder.sudo().create(vals)
            design.sudo().sale_order_id = order.id
            stats["created_orders"] += 1

        # 2) Ensure the sale.order has one southbrook.room. Room dims
        #    come from the design; the reverse-cron in Sprint 2 will
        #    flip the direction so room edits propagate back to the
        #    design during the deprecation window.
        Room = self.env["southbrook.room"]
        room = design.room_id
        # SBK-041 — derive once; used by the room create/refresh below and
        # by the wall maintenance in step 3.
        walls_used = _sb_walls_used_by(design)
        derived_shape = _sb_derive_layout_shape(walls_used)
        if not room:
            # M2 fix (2026-07-06) — root cause: this used to create a room
            # gated only on `design.room_id`, never checking whether the
            # design's own sale_order_id already has a room. A stray/
            # abandoned design (default room_width_in=12 -> ~12in room)
            # that later gains a sale_order_id matching an order that
            # already has a real, wizard-built room ("Main Kitchen") would
            # get a brand-new duplicate placeholder room instead of being
            # linked to the one that's already there. Idempotency guard:
            # reuse the order's existing room if one exists; only create
            # when the order genuinely has none. Deliberately does NOT
            # refresh the reused room's scalars here (unlike the "already
            # bridged" branch below) — this room may belong to a design
            # other than this one, and blindly overwriting its geometry
            # with a stray design's defaults would trade one data-
            # integrity bug for another.
            existing_room = design.sale_order_id.room_ids[:1]
            if existing_room:
                room = existing_room
                design.sudo().room_id = room.id
                _logger.info(
                    "[sb.rec_d] design id=%s reusing existing room id=%s "
                    "on order id=%s instead of creating a duplicate",
                    design.id, room.id, design.sale_order_id.id,
                )
            else:
                room = Room.sudo().create({
                    "order_id":              design.sale_order_id.id,
                    "name":                  design.name or "Kitchen",
                    "room_type":             "kitchen",
                    # SBK-041 — was hardcoded "straight" and never
                    # revisited. Derived from the walls the design's own
                    # lines occupy.
                    "layout_shape":          derived_shape,
                    "ceiling_height_mm":     int(round((design.room_height_in or 0) * IN_TO_MM)),
                    "unit_preference":       "imperial",
                    "sb_soffit_height_mm":   int(round((design.soffit_height_in or 0) * IN_TO_MM)),
                    "sb_wall_cab_top_alignment": design.wall_cab_top_alignment or "fixed_gap",
                    "sb_filler_strategy":    design.filler_strategy or "split",
                    "sb_design_state":       design.state,
                    "x_kitchen_image":       design.x_kitchen_image or False,
                })
                design.sudo().room_id = room.id
                stats["created_rooms"] += 1
        else:
            # Refresh scalars — a designer editing the design after
            # a prior reconciliation must see updated room dims.
            room_vals = {
                "ceiling_height_mm":     int(round((design.room_height_in or 0) * IN_TO_MM)),
                "sb_soffit_height_mm":   int(round((design.soffit_height_in or 0) * IN_TO_MM)),
                "sb_wall_cab_top_alignment": design.wall_cab_top_alignment or "fixed_gap",
                "sb_filler_strategy":    design.filler_strategy or "split",
                "sb_design_state":       design.state,
                "x_kitchen_image":       design.x_kitchen_image or False,
            }
            # SBK-041 — the refresh branch previously left layout_shape
            # alone, so a design that GREW into an L or a U after its
            # first reconciliation stayed "straight" forever.
            room_vals.update(_sb_shape_write_vals(room, derived_shape))
            room.sudo().write(room_vals)

        # 3) Ensure one real wall per wall the design's lines occupy.
        #    SBK-041 (2026-08-03) — this used to maintain a single
        #    synthetic "Wall A" spanning room_width_in, with a comment
        #    deferring multi-wall shapes to Sprint 2. Sprint 2 shipped the
        #    reverse cron but never this, so every L and U kitchen in the
        #    system was mirrored as a one-wall straight run.
        #
        #    Identity is wall_order, not name, so this is idempotent and
        #    the historical "Wall A" (always order 10) is ADOPTED as the
        #    back wall rather than duplicated — which keeps every existing
        #    sale.order.line.wall_id pointing at a live record.
        #
        #    Walls that fall out of use are deliberately NOT deleted: they
        #    may still carry cabinet lines or constraints from an earlier
        #    revision, and destroying authoring data is exactly what this
        #    cron's docstring promises never to happen. They are left in
        #    place with their length refreshed.
        Wall = self.env["southbrook.room.wall"]
        width_mm = int(round((design.room_width_in or 0) * IN_TO_MM))
        depth_mm = int(round((design.room_depth_in or 0) * IN_TO_MM))
        span_mm = {"width": width_mm, "depth": depth_mm}

        by_order = {w.wall_order: w for w in room.wall_ids}
        walls_by_key = {}
        for key in sorted(walls_used, key=lambda k: _WALL_META[k][1]):
            name, order, span = _WALL_META[key]
            length_mm = span_mm[span]
            existing = by_order.get(order)
            if existing:
                wall_vals = {}
                if existing.length_mm != length_mm:
                    wall_vals["length_mm"] = length_mm
                # Rename the legacy synthetic wall to what it actually is.
                if existing.name != name and existing.name in ("Wall A", "Wall", False):
                    wall_vals["name"] = name
                if wall_vals:
                    existing.sudo().write(wall_vals)
                walls_by_key[key] = existing
            else:
                walls_by_key[key] = Wall.sudo().create({
                    "room_id":            room.id,
                    "name":               name,
                    "length_mm":          length_mm,
                    "wall_order":         order,
                    "has_base_cabinets":  True,
                    "has_upper_cabinets": True,
                    "has_tall_cabinets":  True,
                })
                stats["created_walls"] = stats.get("created_walls", 0) + 1

        # Every downstream write needs a wall even for a line whose own
        # `wall` value is missing or unrecognised; the back wall is the
        # design model's own default, so it is the honest fallback.
        fallback_wall = (
            walls_by_key.get("back")
            or walls_by_key[sorted(walls_by_key, key=lambda k: _WALL_META[k][1])[0]]
        )

        # 4) Mirror each configurator-origin design.line into a SO line.
        Line = self.env["sale.order.line"]
        for dline in design.cabinet_line_ids:
            if dline.origin != "configurator":
                # Manual-origin lines are user-authored on the design's
                # backend form and reconciled Sprint 2. Skip in Sprint 1.
                continue
            sol = dline.sale_order_line_id
            # FIX-A (repair wave 1, finding #1/#4) — `_sb_dims_mm()`
            # returns {} (all-or-nothing) when the design line's dims
            # are partial/cleared. Unpacking that {} via **kwargs used
            # to OMIT the three sb_line_*_mm keys entirely, so a later
            # sol.write(line_vals) — which only touches keys present in
            # the dict — left stale non-zero overrides from a prior
            # complete-dims state on the SO line. Material weight calc
            # then read those stale mm values as a live override.
            # Always include all three keys (defaulting to 0) so both
            # the create AND update paths below reset the override to
            # 0 — "no override" — the moment the design line loses a
            # complete dims set. Honesty contract: 0 here means
            # "nothing to report", never a fabricated measurement.
            dims = dline._sb_dims_mm()
            # T5 (corner remediation 2026-07-30, V3-products.md claim 3) —
            # for corner lines specifically, recompute price_unit from the
            # CURRENT variant via the single shared formula every corner
            # write site now uses, rather than trusting whatever got
            # frozen into the design line (possibly by a stale pre-fix
            # formula, or before an upstream catalog edit changed the
            # resolved variant's price_extra). Cheap and idempotent:
            # corner lines are DERIVED artifacts regenerated on every
            # auto-arrange run anyway, and this heals any pre-existing
            # corner line on the next reconcile tick without touching the
            # design's own price_unit (a Sprint-1 one-way mirror never
            # writes back to the design side). Non-corner lines keep the
            # design line's authored price_unit verbatim, unchanged.
            #
            # T5 review fix (CRITICAL) — this recompute must NEVER touch a
            # line on a quote the customer has already seen. This method
            # runs both from the 5-min cron (cron_reconcile_designs, any
            # design whose write_date advanced) and synchronously from
            # action_auto_arrange(sync=True) whenever a design tied to an
            # already-confirmed sale order is re-arranged — a later
            # price_extra edit anywhere in the catalog must not silently
            # rewrite that order's corner line. Gate the recompute to
            # draft orders only. (This recompute-from-variant is still
            # corner-specific — Task 33 generalizes the WRITE-side
            # freeze below to every cabinet_type, not this recompute.)
            #
            # "Coherent unit" is the PER-LINE MIRROR WRITE of price_unit
            # on an ALREADY-mirrored corner line, not just the recompute
            # call above — and it has to be enforced with an explicit
            # re-pin AFTER `sol.write(...)` below, not just by omitting
            # "price_unit" from that write's vals dict: sale.order.line.
            # price_unit is itself `compute='_compute_price_unit',
            # store=True, readonly=False` in stock Odoo — writing ANY of
            # its OTHER dependencies (product_id, product_uom_qty, ...,
            # all of which line_vals legitimately still needs to carry
            # for geometry/product sync) re-triggers that compute and
            # silently overwrites price_unit from the product's
            # pricelist price regardless of what key set the write() call
            # used. Explicitly re-pinning price_unit back to its pre-
            # write value after `sol.write()` is the only way to make
            # "don't touch this line's price" actually hold.
            #
            # Pre-existing, OUT OF SCOPE for this guard (noted for the
            # next reviewer): this mirror — corner recompute or not —
            # writes the variant's retail lst_price straight onto the SO
            # line, bypassing the order's own pricelist_id entirely. That
            # channel-pricing gap predates T5 and is not fixed here.
            draft_order = design.sale_order_id.state == "draft"
            price_unit = dline.price_unit
            if dline.cabinet_type == "corner" and dline.product_id and draft_order:
                price_unit = self.env[
                    "southbrook.kitchen.design.line"
                ].sudo()._sb_variant_price(
                    dline.product_id, dline.product_id.product_tmpl_id)
            line_vals = {
                "order_id":              design.sale_order_id.id,
                "product_id":            dline.product_id.id,
                "product_uom_qty":       dline.quantity,
                "price_unit":            price_unit,
                # SBK-041 — was `wall.id`, the single synthetic wall, so
                # every line on every quote claimed the same wall no
                # matter where its cabinet actually stood. Now the line
                # points at the wall it is really on.
                "wall_id":               walls_by_key.get(dline.wall, fallback_wall).id,
                "position_from_left_mm": int(round((dline.x_position_in or 0) * IN_TO_MM)),
                "sb_layout_x_mm":        (dline.x_position_in or 0) * IN_TO_MM,
                "sb_layout_y_mm":        (dline.y_position_in or 0) * IN_TO_MM,
                "sb_layout_z_mm":        (dline.z_position_in or 0) * IN_TO_MM,
                "sb_layout_rotation_deg": dline.rotation_deg or 0,
                "sb_layout_pinned":      bool(dline.pinned),
                "sb_layout_key":         dline.layout_key or f"design-{design.id}-line-{dline.id}",
                "sb_layout_origin":      "configurator",
                "zone":                  dline.zone or False,
                "sb_line_width_mm":      dims.get("sb_line_width_mm", 0),
                "sb_line_height_mm":     dims.get("sb_line_height_mm", 0),
                "sb_line_depth_mm":      dims.get("sb_line_depth_mm", 0),
            }
            if not sol:
                # A design line regenerated with the same layout_key (e.g. a
                # DERIVED corner cabinet rebuilt on every auto-arrange run)
                # already has a mirrored SO line — reuse it so the mirror is
                # idempotent and never duplicates corner lines.
                sol = Line.sudo().search([
                    ("order_id",         "=", design.sale_order_id.id),
                    ("sb_layout_origin", "=", "configurator"),
                    ("sb_layout_key",    "=", line_vals["sb_layout_key"]),
                ], limit=1)
            if not sol:
                # Task 33b (2026-07-31, Task 33 follow-up) — mirrors the
                # draft_order guard immediately above/below: a design line
                # with no mirrored SO line counterpart (e.g. a cabinet
                # added to a design tied to an already-confirmed order)
                # must never land a brand-new PRICED line on a CONFIRMED
                # order. Skip the create, count it, log once, and move on
                # to the next design line — a human reconciles this
                # deliberately. dline.sale_order_line_id is deliberately
                # left unset (not pointed at anything) so the next cron
                # tick re-evaluates it rather than this branch silently
                # pretending a mirror happened.
                if not draft_order:
                    _logger.info(
                        "[sb.rec_d] Task 33b — skipped creating a new SO "
                        "line on confirmed order id=%s for design id=%s "
                        "(design line id=%s, layout_key=%r) — a human "
                        "should reconcile this deliberately",
                        design.sale_order_id.id, design.id, dline.id,
                        line_vals["sb_layout_key"],
                    )
                    stats["skipped_create_confirmed"] = (
                        stats.get("skipped_create_confirmed", 0) + 1)
                    continue
                sol = Line.sudo().create(line_vals)
                dline.sudo().sale_order_line_id = sol.id
                stats["created_lines"] += 1
            else:
                # Task 33 (2026-07-31, T5 follow-up) — the freeze below
                # used to be cabinet_type == "corner"-scoped, but the
                # write() re-trigger hazard it guards against is generic:
                # EVERY cabinet_type goes through this same
                # sol.sudo().write(line_vals) call, and line_vals always
                # carries "price_unit" (see `price_unit` above — only
                # corner+draft lines get a recomputed value; every other
                # line carries dline.price_unit verbatim, but it's still
                # IN the vals dict). sale.order.line.price_unit is
                # `compute='_compute_price_unit', store=True,
                # readonly=False` in stock Odoo — writing ANY of its
                # OTHER dependencies (product_id, product_uom_qty, ...,
                # all of which line_vals legitimately still carries for
                # geometry/product sync) re-triggers that compute and
                # silently overwrites price_unit from the product's
                # pricelist price regardless of which keys the write()
                # call used or what value line_vals put there — corner
                # or not. An ALREADY-mirrored line (any cabinet_type) on
                # a non-draft order must capture the price BEFORE the
                # write so it can be re-pinned after (omitting
                # "price_unit" from the vals dict is NOT sufficient on
                # its own — see above).
                pin_price_unit = not draft_order
                frozen_price_unit = sol.price_unit if pin_price_unit else None
                sol.sudo().write(line_vals)
                if pin_price_unit:
                    sol.sudo().price_unit = frozen_price_unit
                dline.sudo().sale_order_line_id = sol.id

        # 5) Unlink SO lines whose design.line disappeared. Only touches
        #    configurator-origin SO lines to protect manually-added ones.
        existing_dline_keys = {dl.layout_key or f"design-{design.id}-line-{dl.id}"
                                for dl in design.cabinet_line_ids
                                if dl.origin == "configurator"}
        stale = Line.sudo().search([
            ("order_id",         "=", design.sale_order_id.id),
            ("sb_layout_origin", "=", "configurator"),
            ("sb_layout_key",    "not in", list(existing_dline_keys) or [False]),
        ])
        if stale:
            # Task 33b (2026-07-31, Task 33 follow-up) — mirrors the
            # per-line draft_order price-pin guard above: a mirrored SO
            # line whose design-side layout_key disappeared (design line
            # edited/retired/removed) must never be unlinked from a
            # CONFIRMED order — that's deleting a real line from an order
            # the customer has already seen. Evaluated once for the whole
            # stale batch (rather than per-line, like the price pin) since
            # "stale" is already computed against the design's WHOLE
            # order, and every stale line here shares the same order_id
            # and therefore the same order state.
            order_is_draft = design.sale_order_id.state == "draft"
            if order_is_draft:
                stale.unlink()
            else:
                _logger.info(
                    "[sb.rec_d] Task 33b — skipped unlinking %d stale "
                    "mirrored line(s) on confirmed order id=%s for design "
                    "id=%s — a human should reconcile this deliberately",
                    len(stale), design.sale_order_id.id, design.id,
                )
                stats["skipped_unlink_confirmed"] = (
                    stats.get("skipped_unlink_confirmed", 0) + len(stale))

        # 6) Divergence sensor — log-only, no auto-heal in Sprint 1.
        so_total = sum(l.price_subtotal or 0 for l in design.sale_order_id.order_line)
        if abs(so_total - (design.estimated_price or 0)) > 0.5:
            _logger.warning(
                "[sb.rec_d] price divergence on design id=%s: design=%.2f, SO=%.2f",
                design.id, design.estimated_price or 0, so_total,
            )
            stats["divergence"] += 1

        stats["mirrored"] += 1
