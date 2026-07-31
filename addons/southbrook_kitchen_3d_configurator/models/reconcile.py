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
                 "created_lines": 0, "divergence": 0, "errors": 0}
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
                    "layout_shape":          "straight",
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
            room.sudo().write({
                "ceiling_height_mm":     int(round((design.room_height_in or 0) * IN_TO_MM)),
                "sb_soffit_height_mm":   int(round((design.soffit_height_in or 0) * IN_TO_MM)),
                "sb_wall_cab_top_alignment": design.wall_cab_top_alignment or "fixed_gap",
                "sb_filler_strategy":    design.filler_strategy or "split",
                "sb_design_state":       design.state,
                "x_kitchen_image":       design.x_kitchen_image or False,
            })

        # 3) Ensure one synthetic wall spanning the design's room_width_in.
        #    Sprint 2 introduces multi-wall room shapes (L/U/G/galley);
        #    for Sprint 1 the design's flat room maps to a single wall.
        Wall = self.env["southbrook.room.wall"]
        wall = room.wall_ids[:1]
        wall_length_mm = int(round((design.room_width_in or 0) * IN_TO_MM))
        if not wall:
            wall = Wall.sudo().create({
                "room_id":            room.id,
                "name":               "Wall A",
                "length_mm":          wall_length_mm,
                "wall_order":         10,
                "has_base_cabinets":  True,
                "has_upper_cabinets": True,
                "has_tall_cabinets":  True,
            })
        elif wall.length_mm != wall_length_mm:
            wall.sudo().length_mm = wall_length_mm

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
            # draft orders only.
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
                "wall_id":               wall.id,
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
                sol = Line.sudo().create(line_vals)
                dline.sudo().sale_order_line_id = sol.id
                stats["created_lines"] += 1
            else:
                # T5 review fix — an ALREADY-mirrored corner line on a
                # non-draft order: capture the price BEFORE the write so
                # it can be re-pinned after (see the long comment above
                # `draft_order` — omitting "price_unit" from write_vals
                # is NOT sufficient on its own).
                pin_price_unit = (dline.cabinet_type == "corner"
                                   and not draft_order)
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
            stale.unlink()

        # 6) Divergence sensor — log-only, no auto-heal in Sprint 1.
        so_total = sum(l.price_subtotal or 0 for l in design.sale_order_id.order_line)
        if abs(so_total - (design.estimated_price or 0)) > 0.5:
            _logger.warning(
                "[sb.rec_d] price divergence on design id=%s: design=%.2f, SO=%.2f",
                design.id, design.estimated_price or 0, so_total,
            )
            stats["divergence"] += 1

        stats["mirrored"] += 1
