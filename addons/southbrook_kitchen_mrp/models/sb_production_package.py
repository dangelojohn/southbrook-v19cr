# SPDX-License-Identifier: LGPL-3.0-only
"""sb.production.package — bundles a cutlist + a hardware package + state
machine for the shop-floor handoff. The orchestrator the rest of the
platform calls when an MO needs its complete manufacturing recipe."""
import logging
from typing import Optional

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# /srv/shared on PYTHONPATH — same canonical formula source the bridge
# uses for /validate and the G1 test asserts byte-for-byte parity against.
from southbrook_dims import panel_cut_list

_logger = logging.getLogger(__name__)


PRODUCTION_PACKAGE_STATES = [
    ("draft", "Draft"),
    ("ready", "Ready for Shop Floor"),
    ("released", "Released to Production"),
    ("done", "Done"),
]

# P2 — Brand-aware Drawer Slide bindings. Same table the configurator UX
# carries in its drawer_slide_p2 module; duplicated here to avoid a
# manifest dep on configurator_ux just to read 5 rows of constants.
# Each row: (configurator_value_name, x_marathon_sku, is_soft_close).
_SLIDE_VALUE_TO_SKU = [
    ("King Slide K2832 21\" Soft-Close",  "KS-K2832-21",   True),
    ("King Slide 3032 18\" Ball-Bearing", "KS-3032-18",    False),
    ("Blum MOVENTO 450",                  "BLM-MOV-450",   True),
    ("Hettich Actro 5D 500",              "HET-ACTRO-500", True),
    ("Salice Progressa+ (PR-602728)",     "PR-602728",     False),
]


class SbProductionPackage(models.Model):
    _name = "sb.production.package"
    _description = "Southbrook Production Package"
    _order = "id desc"

    name = fields.Char(required=True, default=lambda self: _("New"))
    mo_id = fields.Many2one(
        comodel_name="mrp.production",
        string="Manufacturing Order",
        ondelete="cascade",
        required=True,
        index=True,
    )
    state = fields.Selection(
        PRODUCTION_PACKAGE_STATES, default="draft", tracking=True, required=True,
    )
    cutlist_id = fields.Many2one(
        comodel_name="sb.cutlist", string="Cut List", ondelete="restrict",
    )
    hardware_package_id = fields.Many2one(
        comodel_name="sb.hardware.package",
        string="Hardware Package",
        ondelete="restrict",
    )
    has_pricing_pending = fields.Boolean(
        related="hardware_package_id.has_pricing_pending", store=True,
    )
    # P1 — back-reference to the sale.order.line that emitted this package.
    # Indexed because the audit P1 idempotency key is (sale_order_line_id).
    # Nullable: legacy packages created via generate_from_mo() before P1
    # carry no line reference and that's fine.
    sale_order_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Source Order Line",
        ondelete="set null",
        index=True,
        copy=False,
        help="Set when the package was auto-emitted from a confirmed "
             "sale.order.line by the Premium Orchestration auto_emit_cutlist "
             "flag (audit P1). Used as the idempotency key on re-confirm.",
    )

    @api.constrains("mo_id")
    def _check_unique_mo(self):
        """One production package per MO — enforced at Python level so
        the constraint survives the Odoo-19 _sql_constraints deprecation.
        """
        for record in self:
            if not record.mo_id:
                continue
            dup = self.search_count([
                ("mo_id", "=", record.mo_id.id),
                ("id", "!=", record.id),
            ])
            if dup:
                raise ValidationError(_(
                    "A production package already exists for this "
                    "manufacturing order. Use generate_from_mo() to "
                    "replace it instead of creating a duplicate."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sb.production.package"
                ) or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # ECO-governed cut-spec resolution (2026-08 wiring)
    # ------------------------------------------------------------------
    @api.model
    def _resolve_cut_spec(self, order_line=None, mo=None):
        """Resolve the ECO-governed southbrook.cut.spec that should drive
        this production package's geometry, or a falsy value when none
        applies (callers then fall back to shared.southbrook_dims' own
        module constants — today's exact behaviour).

        Resolution order (most authoritative first):
          1. ``order_line.southbrook_cut_spec_version_id`` — the as-sold
             pin snapshotted at sale.order.action_confirm (by
             southbrook_plm). This is the authoritative answer whenever
             it is set: manufacturing must cut to whatever spec was live
             when the customer's order was actually confirmed, never to
             whatever happens to be active today.
          2. ``order_line`` present but unpinned (a legacy line confirmed
             before the pinning feature shipped) — reconstruct what WAS
             active at confirm time via
             ``southbrook.cut.spec._get_spec_active_at()``, keyed on the
             order's ``date_order`` (falling back to the line's
             ``create_date`` if the order has none).
          3. No ``order_line``, but an ``mo`` that traces back to one via
             ``mo.sale_line_id`` — ONLY when that field actually exists
             on mrp.production in this database (it is contributed by
             the optional ``sale_mrp`` module, not a guaranteed field) —
             recurse into (1)/(2) against that line.
          4. Nothing resolvable — falsy.

        Guarded to a no-op when southbrook_plm isn't installed:
        southbrook_kitchen_mrp deliberately does NOT depend on
        southbrook_plm (they are sibling addons — see this addon's own
        __manifest__.py), so the southbrook.cut.spec model may simply
        not be registered in this database. ``self.env.get(...)``
        returns None for an unregistered model instead of raising
        KeyError, which is what lets this method be called
        unconditionally regardless of which addons happen to be
        installed.

        Returned recordsets are sudo()'d — cut specs are PLM-engineer
        data (read restricted to PLM User/Approver), and this method is
        reached from flows driven by all sorts of users (portal
        customers via the configurator, cron, sales reps), mirroring the
        same sudo rationale southbrook_plm's own _get_cut_constants
        override documents.
        """
        CutSpec = self.env.get("southbrook.cut.spec")
        if CutSpec is None:
            return False

        if order_line:
            pinned = getattr(order_line, "southbrook_cut_spec_version_id", False)
            if pinned:
                return pinned.sudo()
            order = order_line.order_id
            when = (order.date_order if order else False) or order_line.create_date
            return CutSpec.sudo()._get_spec_active_at(when)

        if mo and "sale_line_id" in mo._fields and mo.sale_line_id:
            return self._resolve_cut_spec(order_line=mo.sale_line_id)

        return False

    # ------------------------------------------------------------------
    # Orchestration: an MO -> a complete cutlist + hardware package
    # ------------------------------------------------------------------
    @api.model
    def generate_from_mo(
        self,
        mo,
        width_mm: float,
        height_mm: float,
        depth_mm: float,
        cabinet_family: str = "base",
        door_count: int = 1,
        drawer_count: int = 0,
        soft_close: bool = True,
        slide_sku: Optional[str] = None,
        cut_spec=None,
    ):
        """Build (or rebuild) the complete production package for an MO.

        Idempotent — calling twice replaces the prior cutlist + hardware
        package on the same MO rather than creating duplicates. The init
        doc Module 4 DoD ("an MO produces a complete cutlist + hardware
        package") is met by a single call to this method.

        Geometry comes from shared.southbrook_dims.panel_cut_list — same
        module the bridge and the G1 gate use, so cutlist geometry is
        guaranteed identical to rendered geometry.

        Hardware comes from southbrook.hardware.catalog.resolve — same
        path the bridge will use post-render.

        :param cut_spec: optional, explicit southbrook.cut.spec
            recordset. When omitted (None — the default, and every
            pre-existing call site), it is resolved from ``mo`` via
            ``_resolve_cut_spec(mo=mo)``. Pass an empty/falsy recordset
            explicitly to force module-default constants even if ``mo``
            could otherwise resolve one. When resolution (explicit or
            automatic) yields nothing, geometry is generated exactly as
            before this parameter existed.

        Returns the production-package record.
        """
        if not mo:
            raise UserError(_("generate_from_mo requires an mrp.production record."))

        if cut_spec is None:
            cut_spec = self._resolve_cut_spec(mo=mo)
        constants = cut_spec.constants_dict() if cut_spec else None

        # Idempotency — replace any existing package on this MO.
        # Deletion order matters: production package holds m2o references
        # to cutlist and hardware_package with ondelete='restrict', so we
        # delete the package FIRST, then the things it referenced.
        existing = self.search([("mo_id", "=", mo.id)])
        if existing:
            cutlists = existing.mapped("cutlist_id")
            hardware_packages = existing.mapped("hardware_package_id")
            existing.unlink()
            (cutlists.mapped("line_ids")).unlink()
            cutlists.unlink()
            (hardware_packages.mapped("line_ids")).unlink()
            hardware_packages.unlink()

        Cutlist = self.env["sb.cutlist"]
        HardwarePackage = self.env["sb.hardware.package"]
        Catalog = self.env["southbrook.hardware.catalog"]

        # 1. Geometry — call shared.southbrook_dims and emit cutlist lines.
        # `constants` (resolved above from cut_spec, or None) threads the
        # ECO-governed cut spec into the SAME formulas the FreeCAD bridge
        # renders from. None reproduces today's module-default output
        # exactly — see shared/southbrook_dims.py's module docstring.
        panel_dict = panel_cut_list(
            width_mm, height_mm, depth_mm,
            family=cabinet_family, door_count=door_count,
            constants=constants,
        )
        cutlist = Cutlist.create({"mo_id": mo.id})
        if cut_spec:
            # Traceability snapshot — see the field help on sb.cutlist
            # for why this is a plain id/name pair, not a Many2one.
            cutlist.write({
                "cut_spec_used_id": cut_spec.id,
                "cut_spec_used_name": cut_spec.display_name,
            })
        # R3 PR #31 — pass drawer_count so the generator emits a drawer_front
        # cutlist line for drawer banks (closes the 6-vs-7-row gap that
        # test_p1_auto_emit_cutlist + test_build_from_order_line surfaced).
        Cutlist.generate_lines_from_panel_dict(
            cutlist, panel_dict, drawer_count=drawer_count or 0,
            constants=constants,
        )

        # 2. Hardware — resolve picks and build the package. P2 threads
        # the configurator's chosen Drawer Slide SKU through so the BoM
        # binds to the real product (King Slide K2832 etc.) rather than
        # the legacy per_drawer default.
        shelf_count = int(panel_dict.get("shelf_count") or 0)
        picks = Catalog.resolve(
            cabinet_family=cabinet_family,
            door_count=door_count,
            drawer_count=drawer_count,
            shelf_count=shelf_count,
            soft_close=soft_close,
            slide_sku=slide_sku,
        )
        hardware_package = HardwarePackage.create({"mo_id": mo.id})
        HardwarePackage.generate_lines_from_resolution(hardware_package, picks)

        # 3. The wrap.
        package = self.create({
            "mo_id": mo.id,
            "cutlist_id": cutlist.id,
            "hardware_package_id": hardware_package.id,
            "state": "ready",
        })
        return package

    # ------------------------------------------------------------------
    # P1 — Configurator -> Cutlist + Production Package on order confirm
    # ------------------------------------------------------------------
    # The audit found 0 cutlists in the DB and 63/63 MOs tripping the MI
    # "Missing cutlist" blocker. The configurator already knows enough
    # (Width + Drawer Construction + Box Material + drawer count + slide)
    # to derive panel sizes deterministically — so we close the loop by
    # emitting one production package per kitchen-cabinet order line on
    # confirm. Premium Orchestration gates the call behind the flag
    # ``southbrook_premium_orchestration.auto_emit_cutlist`` (default off).

    # Heuristic defaults for a Southbrook base-cabinet carcass when the
    # configurator hasn't surfaced the dim. Sourced from the company's
    # standard frameless euro base (24" / 36" / 24" approx, in mm).
    _DEFAULT_HEIGHT_MM = 720.0
    _DEFAULT_DEPTH_MM = 580.0
    _DEFAULT_FAMILY = "base"

    @api.model
    def build_from_order_line(self, order_line, mo=None):
        """Emit a production package (cutlist + hardware) for a confirmed
        configured kitchen-cabinet line.

        Idempotent. Returns the package (existing or newly created), or an
        empty recordset when the line is not configurable / has no MO.

        :param order_line: sale.order.line
        :param mo:         optional pre-resolved mrp.production. When omitted
                           we look for an MO already linked to the line.
        """
        if not order_line:
            return self.browse()

        # Idempotency: a package already exists for this line.
        existing = self.search(
            [("sale_order_line_id", "=", order_line.id)], limit=1,
        )
        if existing:
            return existing

        if mo is None:
            mo = self._resolve_mo_for_order_line(order_line)
        if not mo:
            _logger.info(
                "P1 auto-emit: no MO yet for sale.order.line %s — skipping",
                order_line.id,
            )
            return self.browse()

        # Idempotency tier 2: an MO already carries a package (e.g. set up
        # manually). Adopt it by setting the back-reference and return.
        same_mo = self.search([("mo_id", "=", mo.id)], limit=1)
        if same_mo:
            if not same_mo.sale_order_line_id:
                same_mo.sale_order_line_id = order_line.id
            return same_mo

        dims = self._resolve_dims_from_order_line(order_line)
        # Resolve the cut spec from the order line itself (the as-sold
        # pin, or a reconstruction of what was active at confirm time —
        # see _resolve_cut_spec) rather than letting generate_from_mo
        # re-derive it from the MO. The line is the authoritative source
        # of truth for what the customer actually bought.
        cut_spec = self._resolve_cut_spec(order_line=order_line)
        package = self.generate_from_mo(
            mo,
            width_mm=dims["width_mm"],
            height_mm=dims["height_mm"],
            depth_mm=dims["depth_mm"],
            cabinet_family=dims["family"],
            door_count=dims["door_count"],
            drawer_count=dims["drawer_count"],
            soft_close=dims["soft_close"],
            slide_sku=dims.get("slide_sku"),
            cut_spec=cut_spec,
        )
        package.sale_order_line_id = order_line.id
        _logger.info(
            "P1 auto-emit: emitted production package %s (cutlist %d lines) "
            "for sale.order.line %s (MO %s)",
            package.name, package.cutlist_id.line_count, order_line.id, mo.name,
        )
        return package

    @api.model
    def _resolve_mo_for_order_line(self, order_line):
        """Find the mrp.production that materialised this configured line.

        Two probes: ``sale_line_id`` (the canonical Odoo link) and
        ``origin`` (covers MOs created manually that only carry the SO
        name). Returns the first hit or an empty recordset."""
        MO = self.env["mrp.production"]
        mo = MO.search([("sale_line_id", "=", order_line.id)], limit=1)
        if mo:
            return mo
        # Origin fallback: same heuristic the spine backlink uses.
        if order_line.order_id and order_line.order_id.name:
            mo = MO.search(
                [("origin", "=", order_line.order_id.name),
                 ("product_id", "=", order_line.product_id.id)],
                limit=1,
            )
            if mo:
                return mo
        return MO

    @api.model
    def _resolve_dims_from_order_line(self, order_line):
        """Pull cabinet dimensions + counts off the configured variant.

        We read ``product_template_attribute_value_ids`` on the variant —
        Odoo's first-class accessor for "what did the user pick" — and
        match attribute names against the canonical Southbrook attribute
        set. Unknown values fall back to the company-standard base.

        Returns a dict: width_mm, height_mm, depth_mm, family, door_count,
        drawer_count, soft_close.
        """
        product = order_line.product_id
        attr_map = self._southbrook_attr_map(product)

        width_mm = self._parse_dim_to_mm(attr_map.get("width")) or self._DEFAULT_HEIGHT_MM
        # Height is rarely a configurator attribute (cabinets share standard
        # 720mm carcass height); honour the rare case where it is exposed.
        height_mm = self._parse_dim_to_mm(attr_map.get("height")) or self._DEFAULT_HEIGHT_MM
        depth_mm = self._parse_dim_to_mm(attr_map.get("depth")) or self._DEFAULT_DEPTH_MM
        if width_mm == self._DEFAULT_HEIGHT_MM and "width" not in attr_map:
            # No Width attribute at all — fall back to a standard 600mm so
            # geometry isn't accidentally pegged to the cabinet height.
            width_mm = 600.0

        family = self._infer_family(attr_map, product) or self._DEFAULT_FAMILY
        drawer_count = self._infer_drawer_count(attr_map, product)
        door_count = 0 if drawer_count else self._infer_door_count(attr_map, width_mm)
        soft_close = self._infer_soft_close(attr_map)
        slide_sku, slide_is_soft_close = self._resolve_slide_sku_from_attr_map(attr_map)
        # P2 — when a brand-aware slide is picked, soft-close becomes a
        # *derivation* of the slide's is_soft_close rather than a separate
        # billable add-on. This keeps the resolver consistent with the
        # configurator's suppression of the legacy +$15 Accessories pick.
        if slide_sku:
            soft_close = bool(slide_is_soft_close)

        return {
            "width_mm": width_mm,
            "height_mm": height_mm,
            "depth_mm": depth_mm,
            "family": family,
            "door_count": door_count,
            "drawer_count": drawer_count,
            "soft_close": soft_close,
            "slide_sku": slide_sku,
        }

    # ------------------------------------------------------------------
    # BOM-generation task (2026-07-31) — bare-variant dims resolution.
    #
    # Same attribute-walking inference as _resolve_dims_from_order_line
    # above, generalized to accept a product.product DIRECTLY (no
    # sale.order.line required) so the catalog-wide BoM generator
    # (southbrook_kitchen_mrp.mrp_bom_catalog) can give a config_ok=True
    # cabinet's real, materialized variants their own correctly-
    # dimensioned mrp.bom instead of a template-level midpoint fallback.
    #
    # READ-ONLY on `product` — never writes to it. Wizard-configured
    # variants are user data; this only inspects the attribute picks
    # already on the record.
    # ------------------------------------------------------------------
    @api.model
    def _resolve_dims_from_variant(self, product, tmpl=None):
        """Pull cabinet dimensions + counts off a bare configured variant.

        :param product: product.product — the materialized variant.
        :param tmpl:    optional product.template (defaults to
                         ``product.product_tmpl_id``) — its
                         southbrook_width_in/_height_in/_depth_in and
                         southbrook_cabinet_type are the fallback for any
                         dimension/family the variant's own attribute
                         picks don't cover.

        Returns the same shape as _resolve_dims_from_order_line, plus
        ``accessory_type`` (lower-cased "Accessory Type" attribute pick,
        or None) for the accessory-family SKU resolution in
        mrp_bom_catalog.py.
        """
        tmpl = tmpl or product.product_tmpl_id
        attr_map = self._southbrook_attr_map(product)

        width_mm = self._parse_dim_to_mm(attr_map.get("width"))
        if not width_mm:
            width_mm = (tmpl.southbrook_width_in or 24.0) * 25.4
        height_mm = self._parse_dim_to_mm(attr_map.get("height"))
        if not height_mm:
            height_mm = (tmpl.southbrook_height_in or 34.5) * 25.4
        depth_mm = self._parse_dim_to_mm(attr_map.get("depth"))
        if not depth_mm:
            depth_mm = (tmpl.southbrook_depth_in or 24.0) * 25.4

        family = self._infer_family(attr_map, product)
        if not family:
            MrpBom = self.env["mrp.bom"]
            cabinet_type = tmpl.southbrook_cabinet_type or "base"
            family = MrpBom._SOUTHBROOK_FAMILY_MAP.get(cabinet_type, "base")

        drawer_count = self._infer_drawer_count(attr_map, product)
        if family == "accessory":
            door_count = 0
        else:
            door_count = 0 if drawer_count else self._infer_door_count(attr_map, width_mm)

        slide_sku, slide_is_soft_close = self._resolve_slide_sku_from_attr_map(attr_map)
        if slide_sku:
            soft_close = bool(slide_is_soft_close)
        else:
            # Rule 4 (family -> soft-close): bi-fold corner cabinets ship
            # without soft-close hinges by default; every other family
            # defaults to the explicit attribute pick (or True).
            soft_close = self._infer_soft_close(attr_map) if family != "corner" else False

        accessory_type = None
        if family == "accessory":
            accessory_type = (attr_map.get("accessory type") or "").strip().lower() or None

        return {
            "width_mm": width_mm,
            "height_mm": height_mm,
            "depth_mm": depth_mm,
            "family": family,
            "door_count": door_count,
            "drawer_count": drawer_count,
            "soft_close": soft_close,
            "slide_sku": slide_sku,
            "accessory_type": accessory_type,
        }

    def _southbrook_attr_map(self, product):
        """Build the {attribute_name_lower: value_name} map off a bare
        product.product's product_template_attribute_value_ids. Shared by
        both _resolve_dims_from_order_line and _resolve_dims_from_variant
        so the two never drift."""
        attr_map = {}
        picks = product.product_template_attribute_value_ids if product else False
        if picks:
            for ptav in picks:
                attr = ptav.attribute_id
                val = ptav.product_attribute_value_id
                if attr and val:
                    attr_map[(attr.name or "").strip().lower()] = (val.name or "").strip()
        return attr_map

    def _parse_dim_to_mm(self, raw):
        """Best-effort '24 in' / '600mm' / '600' -> millimetres float."""
        if not raw:
            return 0.0
        s = str(raw).strip().lower().replace(",", "")
        # Strip parenthetical, e.g. '24 in (Standard)'
        if "(" in s:
            s = s.split("(", 1)[0].strip()
        is_inches = ("in" in s) or ("\"" in s)
        s = s.replace("mm", "").replace("in", "").replace("\"", "").replace(" ", "")
        try:
            n = float(s)
        except (TypeError, ValueError):
            return 0.0
        return n * 25.4 if is_inches else n

    # BOM-generation task (2026-07-31) — needle order matters: "corner"
    # is checked before "wall" so e.g. "SB-WALL-CORNER" resolves to
    # family="corner" (its real southbrook_cabinet_type) rather than the
    # more-generic "wall" substring matching first. "drawer"/"corner"
    # were also flagged (bom-research.md §2.3) as missing from the
    # needle list entirely -- _infer_drawer_count already derives drawer
    # count independently, but a template/product name or a "Cabinet
    # Style" pick that says "Corner"/"Drawer" was previously silently
    # falling through to the base-cabinet default.
    _FAMILY_NEEDLES = ("corner", "drawer", "tall", "sink", "vanity", "wall", "base")

    def _infer_family(self, attr_map, product):
        """Heuristic family inference (base / wall / tall / sink / vanity /
        drawer / corner).

        :param product: the product.product being resolved -- the
            configured variant on a sale.order.line (legacy call site) or
            a bare catalog variant (catalog-wide BoM generator call site).
        """
        # NOTE (bom-research.md §2.3): the real Southbrook attribute is
        # named "Cabinet Style" (attr_family in southbrook_estimating/
        # data/attributes.xml), not "Family" -- checking attr_map["family"]
        # alone meant this branch never actually matched a real configured
        # pick and always fell through to product-name matching. Both
        # keys are checked now so genuine "Family" attributes (if any
        # database seeds one under that literal name) keep working too.
        fam = (attr_map.get("family") or attr_map.get("cabinet style") or "").lower()
        if fam:
            for needle in self._FAMILY_NEEDLES:
                if needle in fam:
                    return needle
        if product:
            name = (product.display_name or "").lower()
            for needle in self._FAMILY_NEEDLES:
                if needle in name:
                    return needle
        return None

    def _infer_drawer_count(self, attr_map, product):
        """Drawer count from Drawer Construction / explicit Drawer Count /
        product display name. 3-drawer base cabinet is the audit's sample."""
        explicit = attr_map.get("drawer count") or attr_map.get("drawers")
        if explicit:
            try:
                return int(str(explicit).split()[0])
            except (TypeError, ValueError):
                pass
        # The construction string typically encodes count ('3-Drawer Stack').
        construction = (attr_map.get("drawer construction") or "").lower()
        for n in range(9, 0, -1):
            if f"{n}-drawer" in construction or f"{n} drawer" in construction:
                return n
        # Fall back to the product display name.
        if product:
            name = (product.display_name or "").lower()
            for n in range(9, 0, -1):
                if f"{n}-drawer" in name or f"{n} drawer" in name:
                    return n
        return 0

    def _infer_door_count(self, attr_map, width_mm):
        """Width -> door count rule (Southbrook_Excel_to_Odoo_Mapping §3.4):
        9-21" => 1 door; 24-36" => 2 doors. Applied only when the line is
        a door cabinet (drawer_count = 0)."""
        explicit = attr_map.get("door count") or attr_map.get("doors")
        if explicit:
            try:
                return int(str(explicit).split()[0])
            except (TypeError, ValueError):
                pass
        # 9" = 228.6 mm, 21" = 533.4 mm, 24" = 609.6 mm, 36" = 914.4 mm.
        if 200.0 <= width_mm <= 540.0:
            return 1
        if 540.0 < width_mm <= 920.0:
            return 2
        return 1

    def _resolve_slide_sku_from_attr_map(self, attr_map):
        """P2 — Resolve the configured Drawer Slide pick to its SKU.

        Returns ``(sku, is_soft_close)``. ``(None, None)`` when no slide
        is configured (the legacy per_drawer default fires through).
        """
        picked = (attr_map.get("drawer slide") or "").strip()
        if not picked:
            return (None, None)
        for value_name, sku, is_sc in _SLIDE_VALUE_TO_SKU:
            if value_name == picked:
                return (sku, is_sc)
        return (None, None)

    def _infer_soft_close(self, attr_map):
        """Soft-close inference: explicit 'Soft-Close' add-on OR a brand-aware
        slide whose name signals soft-close. Default True for kitchen cabinets
        (matches the existing generate_from_mo() default and demo behaviour).
        """
        slide = (attr_map.get("drawer slide") or "").lower()
        if slide:
            return "soft" in slide or "k2832" in slide or "movento" in slide or "actro" in slide
        accessories = (attr_map.get("accessories") or "").lower()
        if "soft" in accessories:
            return True
        return True
