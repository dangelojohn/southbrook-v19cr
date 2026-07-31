# SPDX-License-Identifier: LGPL-3.0-only
"""Prebuilt sample kitchen templates (Kitchen Templates T1).

southbrook.kitchen.template + southbrook.kitchen.template.line: the data
model behind "start from a prebuilt sample kitchen" instead of an empty
room. Templates carry SLOTS, not SKU pins — a resolver (T2) maps
slot -> product at instantiation time, then hands off to the existing
auto-arrange engine for all geometry. See:
docs/superpowers/specs/2026-07-27-kitchen-templates-design.md

No geometry fields live here — `wall` + `run_seq` is the whole spatial
contract; the kitchen_layout_engine (via action_auto_arrange) owns poses.
"""
from odoo import api, fields, models

# Reuse — never fork — the room lexicon and the design-line zone lexicon.
from odoo.addons.southbrook_estimating.models.southbrook_room import _LAYOUT_SHAPES
from .kitchen_design import ZONE_SELECTION, _ZONE_FROM_CABINET_TYPE


class SouthbrookKitchenTemplate(models.Model):
    _name = "southbrook.kitchen.template"
    _description = "Prebuilt Sample Kitchen Template"
    _order = "sequence, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True,
                        help="Stable short code (e.g. L-10X8). Referenced by the "
                             "picker's legacy-preset compat map.")
    description = fields.Text()
    layout_shape = fields.Selection(selection=_LAYOUT_SHAPES, required=True,
                                     default="straight")
    default_room_width_in = fields.Float(required=True, default=120.0, digits=(6, 2))
    default_room_depth_in = fields.Float(required=True, default=96.0, digits=(6, 2))
    default_room_height_in = fields.Float(required=True, default=96.0, digits=(6, 2))
    default_module_width_in = fields.Float(required=True, default=24.0, digits=(6, 2))
    min_cabinet_count = fields.Integer(default=1)
    max_cabinet_count = fields.Integer(
        default=0, help="0 = engine-bounded only (n_max).")
    default_filler_strategy = fields.Selection(
        [("split", "Split (both ends)"), ("right", "Right side"),
         ("left", "Left side"), ("scribe", "Scribe (no filler)")],
        default="split", required=True)
    default_wall_cab_top_alignment = fields.Selection(
        [("fixed_gap", "Fixed 18\" gap above counter"),
         ("to_ceiling", "Up to ceiling"), ("to_soffit", "Up to soffit")],
        default="fixed_gap", required=True)
    # NOTE: plain Binary, NOT fields.Image — PIL (Image's backing codec)
    # rejects SVG, and the generated top-view preview (T3) is an SVG.
    # A manual override upload therefore also has to accept SVG, so the
    # field must stay Binary for both the generated and override paths.
    thumbnail = fields.Binary(
        attachment=True,
        help="Optional manual override; the generated top-view SVG (T3) "
             "is the default preview.")
    thumbnail_filename = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    notes = fields.Text()
    line_ids = fields.One2many("southbrook.kitchen.template.line",
                                "template_id", string="Slots", copy=True)

    _code_uniq = models.Constraint(
        "unique (code)",
        "Template code must be unique.",
    )


class SouthbrookKitchenTemplateLine(models.Model):
    _name = "southbrook.kitchen.template.line"
    _description = "Kitchen Template Slot"
    _order = "wall, run_seq, sequence, id"

    template_id = fields.Many2one("southbrook.kitchen.template", required=True,
                                   ondelete="cascade", index=True)
    slot_code = fields.Char(required=True,
                             help="Stable per-template slot id (SINK, RANGE, B1…).")
    cabinet_type = fields.Selection(
        [("base", "Base Cabinet"), ("wall", "Wall Cabinet"),
         ("tall", "Tall Cabinet"), ("corner", "Corner Preference"),
         ("appliance", "Appliance Space")],
        required=True, default="base",
        help="Corner slots carry a PRODUCT PREFERENCE only — the corner "
             "engine (M2 rules-as-data) owns all corner geometry. Fillers "
             "are never templated (M3 derives them).")
    zone = fields.Selection(selection=ZONE_SELECTION, compute="_compute_zone",
                             store=True, readonly=False, precompute=True)
    wall = fields.Selection(
        [("back", "Back"), ("left", "Left"),
         ("right", "Right"), ("front", "Front")],
        required=True, default="back")
    run_seq = fields.Integer(default=0)
    sequence = fields.Integer(default=10)
    nominal_width_in = fields.Float(
        default=0.0, digits=(6, 2),
        help="0 = this slot takes the parametric module width.")
    archetype_id = fields.Many2one("southbrook.cabinet.archetype",
                                    string="Archetype (preferred)")
    product_id = fields.Many2one("product.product", string="Pinned Product",
                                  help="Optional hard pin for fixed quotable BOMs; "
                                       "wins over the archetype resolver.")
    is_appliance_slot = fields.Boolean(compute="_compute_is_appliance", store=True)
    appliance_type = fields.Selection(
        [("range", "Range"), ("fridge", "Fridge"),
         ("dishwasher", "Dishwasher"), ("hood", "Hood"), ("other", "Other")])
    repeat_ok = fields.Boolean(
        default=False,
        help="When the requested cabinet count exceeds this template's module "
             "slots, slots flagged repeat_ok are cloned (in run order) to fill.")
    priority = fields.Integer(
        default=10,
        help="Drop order when the room/count shrinks — HIGHER drops first.")
    notes = fields.Char()

    @api.depends("cabinet_type")
    def _compute_zone(self):
        for line in self:
            if not line.zone:
                line.zone = ("accessory" if line.cabinet_type == "appliance"
                             else _ZONE_FROM_CABINET_TYPE.get(line.cabinet_type,
                                                               "other"))

    @api.depends("cabinet_type")
    def _compute_is_appliance(self):
        for line in self:
            line.is_appliance_slot = line.cabinet_type == "appliance"


# ── Task 2: resolver + parametric fit + instantiation ─────────────────────
# Appended below both model classes so the file reads models-first. The
# heavy lifting (poses, corners, fillers) belongs to action_auto_arrange /
# kitchen_layout_engine — nothing here computes geometry.

from odoo.exceptions import UserError  # noqa: E402
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine  # noqa: E402

_APPLIANCE_PRODUCT_XMLID = {
    "range": "southbrook_kitchen_3d_configurator.product_tmpl_appl_range",
    "fridge": "southbrook_kitchen_3d_configurator.product_tmpl_appl_fridge",
    "dishwasher": "southbrook_kitchen_3d_configurator.product_tmpl_appl_dishwasher",
    "hood": "southbrook_kitchen_3d_configurator.product_tmpl_appl_hood",
}


class SouthbrookKitchenTemplateResolve(models.Model):
    _inherit = "southbrook.kitchen.template"

    # -- resolver ------------------------------------------------------
    def _resolve_slot(self, slot, width_in):
        """Map a template slot to a concrete product.product.

        Precedence: hard pin > archetype link (+width match) >
        cabinet_type + exact width > EMPTY RECORDSET (= unresolved; the
        caller creates a visible placeholder — honesty contract: never a
        silent substitute, never a dropped slot). Appliance slots resolve
        to the SBK-APPL-* stand-in for their appliance_type.
        """
        self.ensure_one()
        Product = self.env["product.product"]
        if slot.cabinet_type == "appliance":
            xmlid = _APPLIANCE_PRODUCT_XMLID.get(slot.appliance_type or "")
            tmpl = xmlid and self.env.ref(xmlid, raise_if_not_found=False)
            return tmpl.product_variant_id if tmpl else Product
        if slot.product_id:
            return slot.product_id
        if slot.archetype_id:
            # Only templates that HAVE a variant qualify — the canonical Q8
            # catalog is OCA-configurable (config_ok) with ZERO variants
            # until a config session builds one; resolving to a variantless
            # template would silently yield an empty product (T4 finding).
            tmpls = self.env["product.template"].search([
                ("x_prodboard_archetype_id", "=", slot.archetype_id.id),
                ("southbrook_is_cabinet", "=", True),
            ]).filtered("product_variant_ids")
            if tmpls:
                # Prefer a width match when the archetype maps to several.
                exact = tmpls.filtered(
                    lambda t: abs((t.southbrook_width_in or 0.0) - width_in) < 0.51)
                return (exact[:1] or tmpls[:1]).product_variant_id
            return Product  # archetype known, no linked product -> unresolved
        # Last rung: cabinet_type + exact width among flagged cabinets
        # (again variant-bearing only — see the archetype rung's note).
        tmpls = self.env["product.template"].search([
            ("southbrook_is_cabinet", "=", True),
            ("southbrook_cabinet_type", "=", slot.cabinet_type),
        ]).filtered("product_variant_ids")
        exact = tmpls.filtered(
            lambda t: abs((t.southbrook_width_in or 0.0) - width_in) < 0.51)
        return exact[:1].product_variant_id if exact else Product

    # -- parametric fill math (spec §Parametric fill math) --------------
    def _slot_fixed_width(self, s, module_w, appliance_widths):
        """Width a non-repeat floor slot occupies in the run arithmetic."""
        if s.cabinet_type == "appliance":
            return float(appliance_widths.get(
                s.appliance_type or "", s.nominal_width_in or 0.0))
        return float(
            s.nominal_width_in
            or (s.product_id.product_tmpl_id.southbrook_width_in
                if s.product_id else 0.0)
            or module_w)

    def parametric_fit(self, cabinet_count=None, module_width_in=None,
                       appliance_widths=None):
        """Pure math: bound the count, size every slot, trim/grow modules.

        PER-WALL arithmetic (T4): each wall is an independent run — its
        length comes from the room dimension it spans (back/front = room
        width, left/right = room depth), its fixed slots consume it, and
        repeat slots may only grow within THEIR wall's leftover capacity.
        Galley/multi-wall templates are impossible to bound with a single
        summed run (two 96" walls are not one 192" wall).

        Returns {"ok", "message", "count", "n_max", "module_width_in",
        "slots": [(slot, width_in), ...]}. UI-grade bound only — the
        AUTHORITATIVE fit check stays LayoutCapacityExceeded at
        auto-arrange time (single source of truth for geometry).
        """
        self.ensure_one()
        module_w = float(module_width_in or self.default_module_width_in or 24.0)
        appliance_widths = appliance_widths or {}
        corner_in = kitchen_layout_engine._CORNER_FOOTPRINT_MM / 25.4
        room_w = float(self.default_room_width_in or 0.0)
        room_d = float(self.default_room_depth_in or 0.0)
        run_len = {"back": room_w, "front": room_w,
                   "left": room_d, "right": room_d}

        slots = self.line_ids.sorted(lambda s: (s.wall, s.run_seq, s.sequence))
        fixed = dict.fromkeys(run_len, 0.0)
        claimed = dict.fromkeys(run_len, 0.0)
        # Corner slots claim the engine's leg footprint on BOTH legs of
        # the junction: the slot's own wall plus every adjacent wall
        # that carries floor slots. The engine SUBSTITUTES the corner
        # for run-end cabinets that fit fully inside its cell, so
        # non-repeat floor slots at the junction's END of each leg are
        # ABSORBED into the claim (not double-counted as fixed width)
        # up to the corner size. END-AWARE (U-shape, 2 corners): each
        # junction sits at a specific end of each leg — back-left is
        # the back run's LEADING end, back-right its TRAILING end — so
        # absorption walks the leg from the claiming junction's end.
        _ADJACENT = {"back": ("left", "right"), "front": ("left", "right"),
                     "left": ("back", "front"), "right": ("back", "front")}
        # (wall, other_leg) -> which end of `wall` the junction sits at.
        _JUNCTION_END = {
            ("back", "left"): "lead",   ("back", "right"): "trail",
            ("front", "left"): "lead",  ("front", "right"): "trail",
            ("left", "back"): "lead",   ("left", "front"): "trail",
            ("right", "back"): "lead",  ("right", "front"): "trail",
        }
        floor_walls = set(slots.filtered(
            lambda s: s.cabinet_type not in ("wall", "corner")).mapped("wall"))
        claims = []   # (wall, end) — one entry per leg per corner slot
        for c in slots.filtered(lambda s: s.cabinet_type == "corner"):
            for other in [w for w in _ADJACENT[c.wall] if w in floor_walls]:
                claims.append((c.wall, _JUNCTION_END[(c.wall, other)]))
                claims.append((other, _JUNCTION_END[(other, c.wall)]))
        absorbed_ids = set()
        for w, end in claims:
            claimed[w] += corner_in
            leg = [s for s in slots
                   if s.wall == w and s.cabinet_type in ("base", "tall")
                   and not s.repeat_ok]
            if end == "trail":
                leg = list(reversed(leg))
            cum = 0.0
            for s in leg:
                if s.id in absorbed_ids:
                    continue
                width = self._slot_fixed_width(s, module_w, appliance_widths)
                if cum + width <= corner_in + 1e-6:
                    absorbed_ids.add(s.id)
                    cum += width
                else:
                    break
        for s in slots:
            if s.cabinet_type in ("wall", "corner"):
                continue  # upper tier / claimed above
            if not s.repeat_ok and s.id not in absorbed_ids:
                fixed[s.wall] += self._slot_fixed_width(
                    s, module_w, appliance_widths)
        for w, ln in run_len.items():
            if ln > 0 and fixed[w] + claimed[w] > ln + 1e-6:
                return {"ok": False, "count": 0, "n_max": 0,
                        "module_width_in": module_w, "slots": [],
                        "message": (
                            "%(tpl)s: the fixed cabinets/appliances on the "
                            "%(wall)s wall need %(need)g\" but that run is "
                            "only %(len)g\". Choose smaller appliance sizes "
                            "— the room is never grown automatically." % {
                                "tpl": self.name, "wall": w,
                                "need": fixed[w] + claimed[w], "len": ln})}
        capacity = {
            w: int(max(run_len[w] - claimed[w] - fixed[w], 0.0) // module_w)
            if module_w > 0 else 0
            for w in run_len}
        repeat_slots = slots.filtered(
            lambda s: s.repeat_ok and s.cabinet_type in ("base", "tall"))
        floor_slots = slots.filtered(
            lambda s: s.cabinet_type not in ("wall", "corner"))
        n_fixed_modules = len(floor_slots.filtered(
            lambda s: s.cabinet_type != "appliance")) - len(repeat_slots)
        # Growth happens only inside repeat slots, bounded per wall.
        n_grow = sum(capacity[w] for w in
                     set(repeat_slots.mapped("wall")) & set(capacity))
        count = cabinet_count if cabinet_count is not None else (
            n_fixed_modules + len(repeat_slots))
        min_count = max(self.min_cabinet_count or 1, n_fixed_modules)
        cap = n_fixed_modules + n_grow
        if count > cap:
            return {"ok": False, "count": count, "n_max": cap,
                    "module_width_in": module_w, "slots": [],
                    "message": (
                        "%(tpl)s cannot fit %(count)d cabinets of %(w)g\" in "
                        "this room (max %(cap)d with the chosen appliance "
                        "sizes). Reduce the count or module width — the room "
                        "is never grown automatically." % {
                            "tpl": self.name, "count": count, "w": module_w,
                            "cap": cap})}
        if count < min_count:
            return {"ok": False, "count": count, "n_max": cap,
                    "module_width_in": module_w, "slots": [],
                    "message": "%s needs at least %d cabinets." % (
                        self.name, min_count)}
        n_repeat_needed = count - n_fixed_modules
        if n_repeat_needed > 0 and not repeat_slots:
            return {"ok": False, "count": count, "n_max": cap,
                    "module_width_in": module_w, "slots": [],
                    "message": "%s has no repeatable slot to grow the run." %
                               self.name}
        # Round-robin the extra modules across repeat slots, never
        # exceeding any single wall's leftover capacity.
        alloc = dict.fromkeys(repeat_slots.ids, 0)
        remaining = dict(capacity)
        need = max(n_repeat_needed, 0)
        while need > 0:
            progressed = False
            for s in repeat_slots:
                if need <= 0:
                    break
                if remaining.get(s.wall, 0) > 0:
                    alloc[s.id] += 1
                    remaining[s.wall] -= 1
                    need -= 1
                    progressed = True
            if not progressed:  # count <= cap makes this unreachable; guard
                return {"ok": False, "count": count, "n_max": cap,
                        "module_width_in": module_w, "slots": [],
                        "message": "%s has no repeatable slot capacity left."
                                   % self.name}
        out = []
        for s in slots:
            if s.cabinet_type == "corner":
                continue  # engine inserts corners; slot is a preference only
            if s in repeat_slots:
                for _n in range(alloc.get(s.id, 0)):
                    out.append((s, s.nominal_width_in or module_w))
            else:
                out.append((s, self._slot_fixed_width(
                    s, module_w, appliance_widths)))
        return {"ok": True, "message": "", "count": count, "n_max": cap,
                "module_width_in": module_w, "slots": out}

    # -- instantiation ---------------------------------------------------
    def action_instantiate(self, partner_id=False, cabinet_count=None,
                           module_width_in=None, appliance_widths=None):
        """Create a draft design from this template and let the EXISTING
        auto-arrange engine derive every pose/corner/filler. Savepoint-
        atomic: a failed fit leaves nothing behind (no silent room growth).
        """
        self.ensure_one()
        fit = self.parametric_fit(cabinet_count, module_width_in,
                                  appliance_widths)
        if not fit["ok"]:
            raise UserError(fit["message"])
        Design = self.env["southbrook.kitchen.design"]
        Line = self.env["southbrook.kitchen.design.line"]
        unresolved_tmpl = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_unresolved_slot")
        with self.env.cr.savepoint():
            design = Design.create({
                "name": self.name,
                "partner_id": partner_id or False,
                "room_width_in": self.default_room_width_in,
                "room_depth_in": self.default_room_depth_in,
                "room_height_in": self.default_room_height_in,
                "filler_strategy": self.default_filler_strategy,
                "wall_cab_top_alignment": self.default_wall_cab_top_alignment,
            })
            for idx, (slot, width_in) in enumerate(fit["slots"]):
                product = self._resolve_slot(slot, width_in)
                is_unresolved = not product
                if is_unresolved:
                    product = unresolved_tmpl.product_variant_id
                tmpl = product.product_tmpl_id
                Line.create({
                    "design_id": design.id,
                    "sequence": (idx + 1) * 10,
                    "product_id": product.id,
                    "quantity": 1,
                    "price_unit": 0.0 if is_unresolved else (
                        product.lst_price or tmpl.list_price or 0.0),
                    "cabinet_type": slot.cabinet_type,
                    "zone": _ZONE_FROM_CABINET_TYPE.get(
                        slot.cabinet_type, "other"),
                    "width_in": width_in,
                    "height_in": tmpl.southbrook_height_in or 34.5,
                    "depth_in": tmpl.southbrook_depth_in or 24.0,
                    "wall": slot.wall,
                    "run_seq": slot.run_seq,
                    "origin": "configurator",
                    "layout_role": "canonical",
                    "layout_key": "tpl-%s-%s-%d" % (
                        self.code, slot.slot_code, idx),
                    "template_slot_code": slot.slot_code,
                    "appliance_type": slot.appliance_type or False,
                    "is_unresolved": is_unresolved,
                })
            corner_slots = self.line_ids.filtered(
                lambda s: s.cabinet_type == "corner")
            try:
                design.action_auto_arrange(sync=True)
            except kitchen_layout_engine.LayoutCapacityExceeded as e:
                raise UserError(
                    "This template does not fit a %g\" x %g\" room with the "
                    "chosen count/widths (%s). Reduce the cabinet count or "
                    "module width — the room is never grown automatically."
                    % (design.room_width_in, design.room_depth_in, e)) from e
            design.state = "configured"

            # Task 16 (SBK-028) — chatter honesty. `fit["slots"]` is the
            # PRE-arrange plan (every non-corner slot, one Line.create
            # each, above). action_auto_arrange can then SUPERSEDE
            # (archive, never delete) some of those canonical lines when
            # a real corner cabinet's footprint absorbs them — reporting
            # the plan count as if it were the final line count would be
            # dishonest whenever a corner substitution happened. Read the
            # ACTUAL post-arrange state instead of trusting the plan OR
            # action_auto_arrange's own returned counters: written/
            # absorbed/dropped and is_unresolved below are all computed
            # straight from cabinet_line_ids, so "written" always equals
            # len(design.cabinet_line_ids) by construction.
            requested = len(fit["slots"])
            written = design.cabinet_line_ids  # active only — what the client sees
            all_slot_lines = design.with_context(
                active_test=False).cabinet_line_ids.filtered("template_slot_code")
            absorbed = len(all_slot_lines.filtered(lambda l: not l.active))
            dropped = max(requested - len(all_slot_lines), 0)
            unresolved = len(written.filtered("is_unresolved"))
            design.message_post(body=(
                "Instantiated from template %s (%s): %d slot(s) requested, "
                "%d line(s) written, %d absorbed into corner geometry, "
                "%d dropped, %d unresolved%s." % (
                    self.name, self.code, requested, len(written), absorbed,
                    dropped, unresolved,
                    "; corner preference expressed by %d template slot(s) — "
                    "corner geometry is engine-derived" % len(corner_slots)
                    if corner_slots else "")))
        return design


class SouthbrookKitchenTemplateThumbnail(models.Model):
    _inherit = "southbrook.kitchen.template"

    def _generate_thumbnail_svg(self):
        """Server-generated top-view SVG (floor slots only) for the picker
        preview / shape dropdown. Pure string building from template data —
        no user input ever enters this markup (safe for sanitize=False)."""
        self.ensure_one()
        W = self.default_room_width_in or 120.0
        D = self.default_room_depth_in or 96.0
        s = 200.0 / max(W, D)
        out = ['<svg xmlns="http://www.w3.org/2000/svg" '
               'viewBox="0 0 %.0f %.0f">' % (W * s, D * s),
               '<rect width="%.0f" height="%.0f" fill="#F4EFE7" '
               'stroke="#5E5346"/>' % (W * s, D * s)]
        cursors = {"back": 0.0, "front": 0.0, "left": 0.0, "right": 0.0}
        dpx = 24.0 * s
        for slot in self.line_ids.sorted(lambda l: (l.wall, l.run_seq)):
            if slot.cabinet_type == "wall":
                continue
            w = (slot.nominal_width_in
                 or self.default_module_width_in or 24.0) * s
            c = cursors[slot.wall]
            cursors[slot.wall] = c + w
            if slot.wall == "back":
                x, y, rw, rh = c, 0.0, w, dpx
            elif slot.wall == "front":
                x, y, rw, rh = c, D * s - dpx, w, dpx
            elif slot.wall == "left":
                x, y, rw, rh = 0.0, c, dpx, w
            else:
                x, y, rw, rh = W * s - dpx, c, dpx, w
            fill = ("#C28840" if slot.cabinet_type == "appliance"
                    else "#5E8FBE")
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                       'fill="%s" opacity="0.85"/>' % (x, y, rw, rh, fill))
        out.append("</svg>")
        return "".join(out)
