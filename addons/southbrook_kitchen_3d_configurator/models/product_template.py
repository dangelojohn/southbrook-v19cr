from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # ── Discovery flag ──────────────────────────────────────────────────────────
    southbrook_is_cabinet = fields.Boolean(
        string="Southbrook Cabinet",
        help="Marks this product as part of the Southbrook cabinet catalogue. "
             "Only marked products appear in the 3D Configurator.",
    )

    # ── Classification ──────────────────────────────────────────────────────────
    southbrook_cabinet_type = fields.Selection(
        selection=[
            ("base",   "Base Cabinet"),
            ("wall",   "Wall Cabinet"),
            ("tall",   "Tall Cabinet"),
            ("filler", "Filler Panel"),
            ("panel",  "Decorative Panel"),
            ("corner", "Corner Unit"),
            # Task 2 (kitchen templates): appliance-space stand-in products
            # (SBK-APPL-*). southbrook_is_cabinet stays False on them, so
            # they never appear in the drag catalog.
            ("appliance", "Appliance Space"),
        ],
        string="Cabinet Type",
    )

    # ── Icon / 3D shape override ────────────────────────────────────────────────
    # Debt fix (2026-08-02) — `cabinet_thumb.esm.js`'s `thumbFamily()` has always
    # inferred which of the 11 catalog-icon shapes a product renders as from
    # `southbrook_cabinet_type` plus SKU/name keyword matching (documented in
    # that module as "the ONE place cabinet family is inferred ... pending
    # explicit footprint fields on the product record"). This field is that
    # explicit field: when set, it wins outright over the inference in
    # `thumbFamily()`, so a rep can correct a wrong guess without renaming or
    # re-coding the product. Left blank (the default), behaviour is completely
    # unchanged — the SKU/name inference is still what decides the icon.
    # Values match `thumbFamily()`'s fixed 11-value vocabulary exactly (see
    # that module's `FAMILY_LABEL` table) so the field can never encode a
    # shape the icon renderer doesn't know how to draw.
    southbrook_thumb_family = fields.Selection(
        selection=[
            ("base1",       "Base Cabinet"),
            ("base2",       "2-Door Base Cabinet"),
            ("drawer",      "Drawer Base Cabinet"),
            ("sink",        "Sink Base Cabinet"),
            ("vanity",      "Vanity Base Cabinet"),
            ("wall",        "Wall Cabinet"),
            ("tall",        "Tall Cabinet"),
            ("panel",       "Panel Cabinet"),
            ("cornerL",     "Corner Base Cabinet"),
            ("cornerBlind", "Blind Corner Base Cabinet"),
            ("cornerDiag",  "Diagonal Corner Base Cabinet"),
        ],
        string="Icon / 3D Shape",
        help="Which cabinet shape this product's catalog icon and 3D "
             "preview use. Leave blank to let Southbrook work it out from "
             "the product code and name — set this only to correct a "
             "wrong guess, so the icon and the 3D preview always agree.",
    )

    # ── Physical dimensions ─────────────────────────────────────────────────────
    southbrook_width_in  = fields.Float(string="Width (in)",  default=24.0, digits=(6, 2))
    southbrook_height_in = fields.Float(string="Height (in)", default=34.5, digits=(6, 2))
    southbrook_depth_in  = fields.Float(string="Depth (in)",  default=24.0, digits=(6, 2))

    # ── Finish / material ───────────────────────────────────────────────────────
    southbrook_material = fields.Selection(
        selection=[
            ("white_melamine",  "White Melamine"),
            ("grey_melamine",   "Grey Melamine"),
            ("maple_veneer",    "Maple Veneer"),
            ("oak_veneer",      "Oak Veneer"),
            ("painted_mdf",     "Painted MDF"),
            ("thermoplastic",   "Thermoplastic"),
        ],
        string="Material / Finish",
        default="white_melamine",
    )
    southbrook_door_style = fields.Selection(
        selection=[
            ("slab",   "Slab"),
            ("shaker", "Shaker"),
            ("raised", "Raised Panel"),
        ],
        string="Door Style",
        default="shaker",
    )

    # ── Future 3D asset reference ───────────────────────────────────────────────
    southbrook_3d_asset_url = fields.Char(
        string="3D Asset URL (GLB/GLTF)",
        help="Optional link to a GLB or GLTF file for future photorealistic "
             "rendering. When present the configurator will substitute the box "
             "geometry with this model.",
    )

    # ── Computed helpers ────────────────────────────────────────────────────────
    southbrook_bom_available = fields.Boolean(
        string="BoM Available",
        compute="_compute_southbrook_bom_available",
        store=False,
    )

    @api.depends("bom_ids")
    def _compute_southbrook_bom_available(self):
        for tmpl in self:
            tmpl.southbrook_bom_available = bool(tmpl.bom_ids)
