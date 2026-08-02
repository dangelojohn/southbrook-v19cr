# SPDX-License-Identifier: LGPL-3.0-only
"""Hardware-catalog fields on product.product — stored related view of
the product.template source of truth.

Why related? The Tier 1.2 audit refactor moves the source of truth to
product.template (variant-invariant fields live there). product.product
keeps the same field NAMES so all existing call-sites continue to read
the same way — the related/stored declaration makes Odoo route writes
to the template and propagate to every variant automatically.
"""
from odoo import fields, models


HARDWARE_CATEGORIES = [
    ("hinge", "Hinge"),
    ("slide", "Drawer Slide"),
    ("pin", "Shelf Pin"),
    ("screw", "Screw / Fastener"),
    ("handle", "Handle / Pull"),
    ("leveler", "Cabinet Leveler"),
    ("cam_lock", "Cam Lock / RTA"),
    ("bumper", "Bumper / Stop"),
    # A2 (2026-06-18) — End-panel + filler categories. End panels are
    # finished side panels exposed at run-ends; fillers are narrow
    # strips that close gaps against walls or non-aligned units.
    ("end_panel", "End Panel"),
    ("filler", "Filler"),
    # A3 (2026-06-18) — Corner mechanism category for specialty corner
    # fitments (LAVA / Planero / Magic Corner / BCO / CPOS).
    ("corner_mech", "Corner Mechanism"),
    # Catalog decision (2026-08-02) — Cornice/Pelmet/Plinth trim SKUs
    # (southbrook_estimating's attr_accessory_type carries 5 values;
    # End Panel/Filler already had real SKUs here, these 3 didn't — see
    # data/end_panel_seed.xml and mrp_bom_catalog.py's
    # _SOUTHBROOK_ACCESSORY_SKU_BY_TYPE). One category per type, same
    # granularity as end_panel/filler above rather than lumping all
    # three "trim" SKUs into one bucket.
    ("cornice", "Cornice"),
    ("pelmet", "Pelmet"),
    ("plinth", "Plinth"),
    ("other", "Other"),
]


class ProductProduct(models.Model):
    _inherit = "product.product"

    # Existing fields — kept as stored-related so writes to the variant
    # propagate to the template (and back to every variant). This
    # preserves the legacy XML seed format where records were declared
    # as product.product with these fields set directly.
    # No selection= here: Odoo derives it from the related template
    # field. Passing both raises "selection attribute will be ignored".
    x_hardware_category = fields.Selection(
        related="product_tmpl_id.x_hardware_category",
        store=True, readonly=False,
        string="Hardware Category",
    )
    x_hardware_brand_id = fields.Many2one(
        comodel_name="southbrook.hardware.brand",
        related="product_tmpl_id.x_hardware_brand_id",
        store=True, readonly=False,
        ondelete="restrict",
        string="Brand",
    )
    x_marathon_sku = fields.Char(
        related="product_tmpl_id.x_marathon_sku",
        store=True, readonly=False, index=True,
        string="Marathon SKU",
    )
    x_pricing_pending = fields.Boolean(
        related="product_tmpl_id.x_pricing_pending",
        store=True, readonly=False, index=True,
        string="Pricing Pending",
    )

    # Tier 1.1 spec fields — same related pattern.
    x_length_mm = fields.Float(
        related="product_tmpl_id.x_length_mm", store=True, readonly=False)
    x_width_mm = fields.Float(
        related="product_tmpl_id.x_width_mm", store=True, readonly=False)
    x_projection_mm = fields.Float(
        related="product_tmpl_id.x_projection_mm", store=True, readonly=False)
    x_diameter_mm = fields.Float(
        related="product_tmpl_id.x_diameter_mm", store=True, readonly=False)
    x_center_to_center_mm = fields.Float(
        related="product_tmpl_id.x_center_to_center_mm",
        store=True, readonly=False)
    x_material = fields.Char(
        related="product_tmpl_id.x_material", store=True, readonly=False)
    x_lead_time_days = fields.Integer(
        related="product_tmpl_id.x_lead_time_days",
        store=True, readonly=False)
    x_package_quantity = fields.Integer(
        related="product_tmpl_id.x_package_quantity",
        store=True, readonly=False)
    x_marathon_image_url = fields.Char(
        related="product_tmpl_id.x_marathon_image_url",
        store=True, readonly=False,
        string="Marathon Image URL",
    )
