# SPDX-License-Identifier: LGPL-3.0-only
"""southbrook.room.wall — one wall of a room with its own cabinet zones."""
from odoo import api, fields, models


class SouthbrookRoomWall(models.Model):
    _name = "southbrook.room.wall"
    _description = "Southbrook Room Wall"
    _order = "room_id, wall_order, id"
    _rec_name = "name"

    name = fields.Char(required=True, default="Wall A")
    room_id = fields.Many2one(
        "southbrook.room", required=True, ondelete="cascade", index=True)
    order_id = fields.Many2one(
        "sale.order", related="room_id.order_id", store=True, index=True)

    length_mm = fields.Integer(string="Length (mm)", default=0)
    wall_order = fields.Integer(default=10)

    has_upper_cabinets = fields.Boolean(default=True)
    has_base_cabinets = fields.Boolean(default=True)
    has_tall_cabinets = fields.Boolean(default=False)

    constraint_ids = fields.One2many(
        "southbrook.room.constraint", "wall_id", string="Constraints")

    # Constraint-geometry conflict sub-flags (2026-07-03 QA fix). Split
    # out of the single has_conflicts so the summary can tell the user
    # WHICH geometry problem a wall has, and so the wizard's inline
    # validator (client) and the /room save endpoints (server, source of
    # truth) share one definition of "out of bounds" / "overlap".
    #   - out_of_bounds: a constraint whose span (distance..distance+width)
    #     leaves the wall (< 0 or > length). BLOCKED at save time.
    #   - overlap: two constraints on the same wall whose spans intersect.
    #     WARNED only (a sink under a window is a real-world possibility),
    #     surfaced here so "No conflicts detected" stops lying.
    has_constraint_out_of_bounds = fields.Boolean(
        compute="_compute_conflicts", store=False)
    has_constraint_overlap = fields.Boolean(
        compute="_compute_conflicts", store=False)

    # ------------------------------------------------------------------
    # Room-First UX Phase 1.2 — reverse O2m + capacity / conflict computes.
    # ------------------------------------------------------------------
    cabinet_line_ids = fields.One2many(
        "sale.order.line", "wall_id", string="Cabinet Lines")
    used_mm = fields.Integer(
        compute="_compute_capacity", store=False, string="Used (mm)",
        help="Floor run consumed — base, tall and unzoned cabinets. Wall "
             "cabinets hang above these and are measured separately.")
    remaining_mm = fields.Integer(
        compute="_compute_capacity", store=False, string="Remaining (mm)")
    # SBK-054 — the wall tier is its own run; see _compute_capacity.
    used_wall_tier_mm = fields.Integer(
        compute="_compute_capacity", store=False, string="Wall Tier Used (mm)")
    remaining_wall_tier_mm = fields.Integer(
        compute="_compute_capacity", store=False,
        string="Wall Tier Remaining (mm)")
    has_capacity_conflict = fields.Boolean(
        compute="_compute_conflicts", store=False,
        string="Over Capacity",
        help="A run on this wall holds more cabinet than the wall is long.")
    has_conflicts = fields.Boolean(
        compute="_compute_conflicts", store=False)
    # 2026-07-03 QA follow-up: the wizard/Room Layout tab only ever showed
    # a boolean, so a wall with 3 stacked problems looked identical to one
    # with a single overrun. conflict_count tallies DISTINCT constraints
    # that trip ANY of the three conditions above (out-of-bounds, overlap,
    # cabinet collision) — a constraint hit by two reasons still counts
    # once. Always == 0 when has_conflicts is False and >= 1 when True;
    # see _compute_conflicts for the shared derivation.
    conflict_count = fields.Integer(
        compute="_compute_conflicts", store=False, string="Conflict Count")

    # sb_width_mm is itself a non-stored compute on sale.order.line that
    # also depends on `name` (line free-text). Capacity recomputes when
    # sb_width_mm's SOURCE fields move (product_id, PTAVs, qty) but NOT
    # on line.name edits alone — acceptable since names are only edited
    # interactively for demo-seed lines.
    # SBK-054 (2026-08-03) — a wall carries SEVERAL independent runs, not one.
    #
    # This used to iterate every line on the wall and add its width into a
    # single `used_mm`. Base cabinets sit on the floor; wall cabinets hang
    # above them. They occupy the same wall LENGTH simultaneously, not
    # consecutively — so summing them counts the same linear inches twice and
    # produces arithmetic that cannot be true of any real kitchen. Production
    # showed Back Wall 3048mm long with 4524mm "used" and -1476mm remaining,
    # and Right Wall 2438 / 3324 / -886.
    #
    # The fix needs no new data: `zone` already exists on sale.order.line and
    # is populated (base_run / wall / tall / island / accessory / other). The
    # compute simply never read it. Floor-standing zones consume the run;
    # the wall tier is measured separately; accessories and islands consume
    # no wall length at all.
    #
    # `used_mm` keeps its meaning as the FLOOR run, because that is what every
    # existing caller treats it as, and `remaining_mm` stays the floor
    # remainder. The wall tier gets its own pair rather than being folded in.
    _FLOOR_ZONES = ("base_run", "tall", "other", False)
    _WALL_ZONES = ("wall",)

    @api.depends("cabinet_line_ids.sb_width_mm", "cabinet_line_ids.product_uom_qty",
                 "cabinet_line_ids.zone", "length_mm")
    def _compute_capacity(self):
        for rec in self:
            floor_used = 0.0
            wall_used = 0.0
            for line in rec.cabinet_line_ids:
                qty = line.product_uom_qty or 0.0
                width = (line.sb_width_mm or 0.0) * qty
                zone = line.zone or False
                if zone in rec._WALL_ZONES:
                    wall_used += width
                elif zone in rec._FLOOR_ZONES:
                    floor_used += width
                # island / accessory consume no length on this wall.
            length = rec.length_mm or 0
            rec.used_mm = int(round(floor_used))
            rec.remaining_mm = length - rec.used_mm
            rec.used_wall_tier_mm = int(round(wall_used))
            rec.remaining_wall_tier_mm = length - rec.used_wall_tier_mm

    # O2m membership (`cabinet_line_ids`) implicitly invalidates when a
    # line's wall_id changes — listing it as a separate depends entry is
    # redundant. Payload fields below are what actually drive recompute.
    @api.depends(
        "cabinet_line_ids",
        "cabinet_line_ids.position_from_left_mm",
        "cabinet_line_ids.sb_width_mm",
        # SBK-054 — this method now reads remaining_mm / remaining_wall_tier_mm
        # for the over-capacity check, so it must recompute on everything
        # _compute_capacity does. Without these two, changing only a line's
        # zone or quantity would move the capacity and leave the conflict flag
        # stale — the same class of silence this fix exists to remove.
        "cabinet_line_ids.zone",
        "cabinet_line_ids.product_uom_qty",
        "length_mm",
        "constraint_ids.distance_from_left_mm",
        "constraint_ids.width_mm",
        "constraint_ids.constraint_type",
    )
    def _compute_conflicts(self):
        for rec in self:
            length = rec.length_mm or 0
            # ranges = "blocking" constraint spans (things a cabinet must
            # not collide with), paired with the source constraint record
            # so conflict_count can attribute a conflict back to a
            # specific constraint. power_outlet / structural_post are
            # point markers, not cabinet-blocking, so they're excluded
            # from the cabinet-collision AND the overlap checks below.
            ranges = []
            oob = False
            conflicting = set()  # ids of constraints in ANY conflict below
            for c in rec.constraint_ids:
                start = c.distance_from_left_mm or 0
                width = c.width_mm or 0
                # Out-of-bounds: the constraint leaves the wall. width==0
                # markers (a power point) only fail if placed off the end.
                if start < 0 or start + width > length:
                    oob = True
                    conflicting.add(c.id)
                if width and c.constraint_type not in ("power_outlet", "structural_post"):
                    ranges.append((c, start, start + width))
            # Constraint-vs-constraint overlap on the same wall.
            overlap = False
            for i in range(len(ranges)):
                ci, a0, a1 = ranges[i]
                for j in range(i + 1, len(ranges)):
                    cj, b0, b1 = ranges[j]
                    if a0 < b1 and a1 > b0:
                        overlap = True
                        conflicting.add(ci.id)
                        conflicting.add(cj.id)
            # Cabinet-vs-constraint collision (original behaviour).
            cab_conflict = False
            for line in rec.cabinet_line_ids:
                if line.position_from_left_mm is False or not line.sb_width_mm:
                    continue
                lo = line.position_from_left_mm or 0
                hi = lo + int(line.sb_width_mm)
                for ci, clo, chi in ranges:
                    if lo < chi and hi > clo:
                        cab_conflict = True
                        conflicting.add(ci.id)
            rec.has_constraint_out_of_bounds = oob
            rec.has_constraint_overlap = overlap
            # SBK-054 — a wall holding more cabinet than it is long is a
            # conflict, and until now nothing said so. Production had walls
            # reporting -1476mm and -886mm remaining with "Has Conflicts"
            # showing false, because this method only ever looked at
            # constraints (windows, doors, plumbing) and cabinet-vs-constraint
            # collisions. Capacity was computed and then never consulted.
            over_capacity = (
                rec.remaining_mm < 0 or rec.remaining_wall_tier_mm < 0)
            rec.has_capacity_conflict = over_capacity
            rec.has_conflicts = cab_conflict or oob or overlap or over_capacity
            # conflict_count attributes to specific CONSTRAINTS, so an
            # over-capacity run has no constraint to name; counted as one.
            rec.conflict_count = len(conflicting) + (1 if over_capacity else 0)
