# SPDX-License-Identifier: LGPL-3.0-only
"""product.template extension — Southbrook tool / consumable fields.

Naming convention follows the build brief: every field prefixed
``x_southbrook_*``. Matches the existing ``x_hardware_*`` / ``x_marathon_*``
prefix in southbrook_hardware_catalog.

The fields are organised in six policy groups so the form view in
``views/product_template_views.xml`` can hide irrelevant tabs per
tool_family:

  1. Classification        — is_tool, tool_category_id, tool_family, directness
  2. Lifecycle             — reusable / consumable / lifecycle_state +
                             default work centers / required-for / compatible
  3. Cutting geometry      — cutting_diameter_mm, shank_diameter_mm,
                             tooth_count, blade_diameter_mm, kerf, bore_size,
                             rotation_speed, feed_rate, material_grade, coating, grit
  4. Fastener geometry     — screw_size, screw_length_mm, head_type,
                             drive_type, thread_type
  5. Chemical / consumable — glue_type, open_time, cure_time, shelf_life_days,
                             expiry_required, hazardous, msds_required
  6. Replenishment & life  — preferred_vendor_id, vendor_sku, min/max_stock,
                             reorder_multiple, issue_uom_id, estimated_life,
                             sharpening_interval, calibration_interval,
                             inspection_interval, cleaning_required_after_use,
                             notes_for_operator / storage / safety

All policy flag fields default to ``False`` so products outside the
tool category surface stay invisible — the form view's
``invisible="not x_southbrook_is_tool"`` keeps the tab hidden until the
admin opts a product in.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .tool_category import TOOL_FAMILY_SELECTION, DIRECTNESS_SELECTION


# Estimated-life units the shop floor measures by.
ESTIMATED_LIFE_UNIT_SELECTION = [
    ("cuts", "Cuts"),
    ("sheets", "Sheets"),
    ("meters", "Linear meters"),
    ("holes", "Holes"),
    ("minutes", "Cutting minutes"),
    ("hours", "Cutting hours"),
    ("cycles", "Cycles"),
    ("uses", "Uses"),
    ("days", "Days"),
]

# Tool lifecycle — also reused on southbrook.tool.asset in commit 2.
TOOL_LIFECYCLE_SELECTION = [
    ("new", "New / Unused"),
    ("available", "Available"),
    ("in_use", "In Use"),
    ("checked_out", "Checked Out"),
    ("needs_cleaning", "Needs Cleaning"),
    ("needs_sharpening", "Needs Sharpening"),
    ("needs_calibration", "Needs Calibration"),
    ("under_maintenance", "Under Maintenance"),
    ("broken", "Broken"),
    ("retired", "Retired"),
    ("scrapped", "Scrapped"),
]

# Fastener head / drive types — surfaced on the form as Selections so
# planners pick from a controlled vocabulary rather than free text.
HEAD_TYPE_SELECTION = [
    ("hex", "Hex"),
    ("pan", "Pan"),
    ("round", "Round"),
    ("flat", "Flat / Countersunk"),
    ("button", "Button"),
    ("socket", "Socket"),
    ("truss", "Truss"),
    ("oval", "Oval"),
    ("wafer", "Wafer"),
    ("euro", "Euro"),
    ("other", "Other"),
]
DRIVE_TYPE_SELECTION = [
    ("phillips", "Phillips"),
    ("ph2", "PH2"),
    ("slotted", "Slotted"),
    ("hex", "Hex / Allen"),
    ("torx", "Torx"),
    ("robertson", "Robertson / Square"),
    ("pozidriv", "Pozidriv"),
    ("pozi", "Pozi"),
    ("other", "Other"),
]
THREAD_TYPE_SELECTION = [
    ("coarse", "Coarse"),
    ("fine", "Fine"),
    ("self_tapping", "Self-tapping"),
    ("particleboard", "Particleboard"),
    ("confirmat", "Confirmat"),
    ("euro", "Euro"),
    ("machine", "Machine"),
    ("other", "Other"),
]

# Three-state safety assessment (safety three-state remediation,
# 19.0.2.0.0). A plain Boolean cannot distinguish "assessed and
# confirmed" from "nobody has ever looked at this product" — both
# collapse to False and render identically: an unchecked box on the
# form, a missing badge/chip in the catalog, a search filter that
# silently excludes it. That is exactly backwards for safety data.
# Governing rule (BlindSpot review): "Absence of data never renders as
# good." 'not_assessed' is therefore the explicit default — never
# False, never blank — so every renderer (form, search filter, the
# release-readiness gate below, sb_materials_hardware's catalog badges
# and facets) has a real value to draw a distinct, honest state from.
SAFETY_ASSESSMENT_SELECTION = [
    ("yes", "Yes"),
    ("no", "No"),
    ("not_assessed", "Not assessed"),
]

# Coverage-rate unit of measure. Deliberately unseeded (no default) —
# the rate itself is vendor spec-sheet data nobody has entered yet;
# forcing a UoM choice without a rate to go with it would just be a
# different flavour of the same "looks complete, isn't" problem this
# wave exists to fix.
COVERAGE_UOM_SELECTION = [
    ("g_per_lm", "g / linear m"),
    ("ml_per_m2", "ml / m²"),
    ("g_per_m2", "g / m²"),
]

# Adhesive / glue families
GLUE_TYPE_SELECTION = [
    ("pva", "PVA Wood Glue"),
    ("pur", "PUR (Polyurethane)"),
    ("eva", "EVA Hot-Melt"),
    ("contact", "Contact Cement"),
    ("epoxy", "Epoxy"),
    ("silicone", "Silicone"),
    ("ca", "Cyanoacrylate (CA)"),
    ("hide", "Hide Glue"),
    ("laminate", "Laminate Adhesive"),
    ("construction", "Construction Adhesive"),
    ("spray", "Spray Adhesive"),
    ("other", "Other"),
]


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # ═════════════════════════════════════════════════════════════════
    # 1. Classification
    # ═════════════════════════════════════════════════════════════════
    x_southbrook_is_tool = fields.Boolean(
        string="Is Southbrook Tool / Consumable",
        index=True,
        help="Marks this product as a tool, consumable, maintenance "
             "supply, or PPE managed by the Southbrook shop-floor tool "
             "control system.",
    )
    x_southbrook_is_consumable_tool = fields.Boolean(
        string="Is Consumable Tool",
        help="Issued and consumed (depleted) against work orders.",
    )
    x_southbrook_is_reusable_tool = fields.Boolean(
        string="Is Reusable Tool",
        help="Reusable asset — checked in/out of the tool crib.",
    )
    x_southbrook_is_indirect_tool = fields.Boolean(
        string="Is Indirect Tool / Supply",
        help="Shared shop overhead — not issued to a specific work order.",
    )
    x_southbrook_is_maintenance_supply = fields.Boolean(
        string="Is Maintenance Supply",
        help="Grease / oil / filters / belts / spare parts.",
    )
    x_southbrook_tool_category_id = fields.Many2one(
        "southbrook.tool.category",
        string="Tool Category",
        index=True,
        ondelete="restrict",
    )
    x_southbrook_tool_family = fields.Selection(
        TOOL_FAMILY_SELECTION,
        string="Tool Family",
        index=True,
    )
    x_southbrook_directness = fields.Selection(
        DIRECTNESS_SELECTION,
        string="Directness",
        index=True,
    )

    # ═════════════════════════════════════════════════════════════════
    # 2. Lifecycle / linkage
    # ═════════════════════════════════════════════════════════════════
    x_southbrook_tool_lifecycle_state = fields.Selection(
        TOOL_LIFECYCLE_SELECTION,
        string="Lifecycle State (catalog default)",
        help="Default lifecycle state stamped on new tool-asset records "
             "created from this product. Per-asset state on "
             "southbrook.tool.asset overrides this at runtime.",
    )
    x_southbrook_default_workcenter_ids = fields.Many2many(
        "mrp.workcenter",
        "southbrook_tool_product_default_wc_rel",
        "product_tmpl_id", "workcenter_id",
        string="Default Work Centers",
    )
    x_southbrook_required_for_workcenter_ids = fields.Many2many(
        "mrp.workcenter",
        "southbrook_tool_product_required_wc_rel",
        "product_tmpl_id", "workcenter_id",
        string="Required For Work Centers",
        help="Work centers that cannot run without this tool / "
             "consumable. Commit 4's readiness check blocks work "
             "orders here if the item is unavailable.",
    )
    x_southbrook_compatible_material_ids = fields.Many2many(
        "product.category",
        "southbrook_tool_product_compat_mat_rel",
        "product_tmpl_id", "category_id",
        string="Compatible Material Categories",
        help="Material product categories this tool is suited for "
             "(e.g. MDF, plywood, melamine). Used to surface a warning "
             "when a wrong-tool combination is selected.",
    )
    x_southbrook_compatible_finish_ids = fields.Many2many(
        "product.category",
        "southbrook_tool_product_compat_fin_rel",
        "product_tmpl_id", "category_id",
        string="Compatible Finishes",
    )
    x_southbrook_compatible_machine_ids = fields.Many2many(
        "maintenance.equipment",
        "southbrook_tool_product_compat_eq_rel",
        "product_tmpl_id", "equipment_id",
        string="Compatible Machines",
    )

    # ═════════════════════════════════════════════════════════════════
    # 3. Cutting / blade / bit geometry
    # ═════════════════════════════════════════════════════════════════
    x_southbrook_cutting_diameter_mm = fields.Float(string="Cutting Ø (mm)")
    x_southbrook_shank_diameter_mm = fields.Float(string="Shank Ø (mm)")
    x_southbrook_cutting_length_mm = fields.Float(string="Cutting Length (mm)")
    x_southbrook_overall_length_mm = fields.Float(string="Overall Length (mm)")
    x_southbrook_tooth_count = fields.Integer(string="Tooth Count")
    x_southbrook_blade_diameter_mm = fields.Float(string="Blade Ø (mm)")
    x_southbrook_kerf_width_mm = fields.Float(string="Kerf Width (mm)")
    x_southbrook_bore_size_mm = fields.Float(string="Bore Size (mm)")
    x_southbrook_rotation_speed_min = fields.Float(string="Min RPM")
    x_southbrook_rotation_speed_max = fields.Float(string="Max RPM")
    x_southbrook_feed_rate_min = fields.Float(string="Min Feed Rate (mm/min)")
    x_southbrook_feed_rate_max = fields.Float(string="Max Feed Rate (mm/min)")
    x_southbrook_material_grade = fields.Char(string="Material Grade")
    x_southbrook_coating = fields.Char(string="Coating")
    x_southbrook_grit = fields.Char(string="Grit")

    # ═════════════════════════════════════════════════════════════════
    # 4. Fastener / screw geometry
    # ═════════════════════════════════════════════════════════════════
    x_southbrook_screw_size = fields.Char(
        string="Screw Size", help="Diameter — e.g. 3.5mm, #8, M4.",
    )
    x_southbrook_screw_length_mm = fields.Float(string="Screw Length (mm)")
    x_southbrook_head_type = fields.Selection(
        HEAD_TYPE_SELECTION, string="Head Type",
    )
    x_southbrook_drive_type = fields.Selection(
        DRIVE_TYPE_SELECTION, string="Drive Type",
    )
    x_southbrook_thread_type = fields.Selection(
        THREAD_TYPE_SELECTION, string="Thread Type",
    )

    # ═════════════════════════════════════════════════════════════════
    # 5. Chemical / glue / consumable
    # ═════════════════════════════════════════════════════════════════
    x_southbrook_glue_type = fields.Selection(
        GLUE_TYPE_SELECTION, string="Glue / Adhesive Type",
    )
    x_southbrook_open_time_min = fields.Float(
        string="Open Time (min)",
        help="Working time after application, before the bond sets.",
    )
    x_southbrook_cure_time_min = fields.Float(
        string="Cure Time (min)",
        help="Full cure time before the assembly can be handled.",
    )
    x_southbrook_shelf_life_days = fields.Integer(string="Shelf Life (days)")
    x_southbrook_expiry_required = fields.Boolean(
        string="Expiry Required",
        help="Readiness check blocks the work order if no in-date lot is "
             "available.",
    )
    x_southbrook_hazardous = fields.Selection(
        SAFETY_ASSESSMENT_SELECTION,
        string="Hazardous",
        default="not_assessed",
        tracking=True,
        help="Is this product classified as a hazardous material? "
             "'Not assessed' is the honest default until someone "
             "actually checks — it is not the same thing as 'No', and "
             "must never render or filter as if it were.",
    )
    x_southbrook_msds_required = fields.Selection(
        SAFETY_ASSESSMENT_SELECTION,
        string="MSDS Required",
        default="not_assessed",
        tracking=True,
        help="Does this product require a Material Safety Data Sheet "
             "on file? 'Not assessed' means nobody has checked — "
             "distinct from a confirmed 'No'.",
    )

    # Additional safety flags (commit 4 surfaces in operator panel)
    x_southbrook_flammable = fields.Selection(
        SAFETY_ASSESSMENT_SELECTION,
        string="Flammable",
        default="not_assessed",
        tracking=True,
        help="Is this product flammable? 'Not assessed' means nobody "
             "has checked — distinct from a confirmed 'No'.",
    )
    x_southbrook_requires_ventilation = fields.Selection(
        SAFETY_ASSESSMENT_SELECTION,
        string="Requires Ventilation",
        default="not_assessed",
        tracking=True,
        help="Does using this product require active ventilation? "
             "'Not assessed' means nobody has checked — distinct from "
             "a confirmed 'No'.",
    )
    x_southbrook_ppe_required = fields.Char(
        string="PPE Required",
        help="Short label, e.g. 'Respirator + nitrile gloves'.",
    )
    x_southbrook_storage_temperature_notes = fields.Char(
        string="Storage Temperature Notes",
    )
    x_southbrook_disposal_notes = fields.Char(string="Disposal Notes")

    # ═════════════════════════════════════════════════════════════════
    # 6. Replenishment + life tracking
    # ═════════════════════════════════════════════════════════════════
    x_southbrook_preferred_vendor_id = fields.Many2one(
        "res.partner", string="Preferred Vendor",
        domain="[('supplier_rank', '>', 0)]",
    )
    x_southbrook_vendor_sku = fields.Char(string="Vendor SKU")
    x_southbrook_min_stock_qty = fields.Float(string="Min Stock Qty")
    x_southbrook_max_stock_qty = fields.Float(string="Max Stock Qty")
    x_southbrook_reorder_multiple = fields.Float(
        string="Reorder Multiple", default=1.0,
    )
    x_southbrook_issue_uom_id = fields.Many2one(
        "uom.uom", string="Issue UoM",
    )
    x_southbrook_coverage_rate = fields.Float(
        string="Coverage Rate",
        help="Consumption per unit of application — e.g. grams per "
             "linear metre of edge adhesive, millilitres per square "
             "metre of finish. 0.0 means NOT ENTERED, not 'zero "
             "consumption': this is vendor spec-sheet data nobody has "
             "keyed in yet. Renderers must say 'no coverage rate in "
             "data' for 0.0 — never '0 g/lm' — the numeric-falsy trap "
             "this field is named to avoid (same failure mode as the "
             "boolean safety flags above).",
    )
    x_southbrook_coverage_uom = fields.Selection(
        COVERAGE_UOM_SELECTION,
        string="Coverage Rate UoM",
        help="Unit the coverage rate above is expressed in. Optional "
             "and unseeded on purpose — deferred to whichever owner "
             "actually has the vendor spec sheet in hand.",
    )
    x_southbrook_estimated_life_qty = fields.Float(string="Estimated Life Qty")
    x_southbrook_estimated_life_unit = fields.Selection(
        ESTIMATED_LIFE_UNIT_SELECTION,
        string="Estimated Life Unit",
    )
    x_southbrook_sharpening_interval_qty = fields.Float(
        string="Sharpening Interval (qty)",
        help="Number of cuts / sheets / hours after which a sharpening "
             "request is automatically created.",
    )
    x_southbrook_calibration_interval_days = fields.Integer(
        string="Calibration Interval (days)",
    )
    x_southbrook_inspection_interval_days = fields.Integer(
        string="Inspection Interval (days)",
    )
    x_southbrook_cleaning_required_after_use = fields.Boolean(
        string="Cleaning Required After Use",
    )

    # ═════════════════════════════════════════════════════════════════
    # Operator-facing notes
    # ═════════════════════════════════════════════════════════════════
    x_southbrook_notes_for_operator = fields.Text(
        string="Notes for Operator",
    )
    x_southbrook_storage_instructions = fields.Text(
        string="Storage Instructions",
    )
    x_southbrook_safety_notes = fields.Text(string="Safety Notes")

    # ──────────────────────────────────────────────────────────────────
    # Onchange — picking a category seeds the policy defaults
    # ──────────────────────────────────────────────────────────────────
    @api.onchange("x_southbrook_tool_category_id")
    def _onchange_tool_category(self):
        for rec in self:
            cat = rec.x_southbrook_tool_category_id
            if not cat:
                continue
            # Flip the master "is tool" toggle so the form's tab unhides.
            if not rec.x_southbrook_is_tool:
                rec.x_southbrook_is_tool = True
            # Inherit family + directness when blank.
            if not rec.x_southbrook_tool_family and cat.tool_family:
                rec.x_southbrook_tool_family = cat.tool_family
            if not rec.x_southbrook_directness:
                if cat.directness:
                    rec.x_southbrook_directness = cat.directness
                elif cat.consumable:
                    rec.x_southbrook_directness = "direct_consumable"
                elif cat.reusable:
                    rec.x_southbrook_directness = "direct_production_tool"
            # Inherit reusable / consumable flags.
            rec.x_southbrook_is_reusable_tool = cat.reusable
            rec.x_southbrook_is_consumable_tool = cat.consumable
            # Inherit safety + expiry flags. The category's hazardous /
            # msds_required flags are policy *assertions* ("this whole
            # category is hazardous") — they can justify setting 'yes',
            # but their absence proves nothing about this one product,
            # so a falsy category flag must NEVER auto-set 'no' here.
            # Leaving the field at its 'not_assessed' default in that
            # case is the honest behaviour; a real 'no' has to come
            # from someone actually assessing the product.
            rec.x_southbrook_expiry_required = cat.has_expiry_date
            if cat.hazardous:
                rec.x_southbrook_hazardous = "yes"
            if cat.msds_required:
                rec.x_southbrook_msds_required = "yes"
            # Replenishment defaults.
            if cat.default_uom_id and not rec.uom_id:
                rec.uom_id = cat.default_uom_id
            if cat.default_issue_uom_id and not rec.x_southbrook_issue_uom_id:
                rec.x_southbrook_issue_uom_id = cat.default_issue_uom_id
            if cat.default_reorder_min_qty and not rec.x_southbrook_min_stock_qty:
                rec.x_southbrook_min_stock_qty = cat.default_reorder_min_qty
            if cat.default_reorder_max_qty and not rec.x_southbrook_max_stock_qty:
                rec.x_southbrook_max_stock_qty = cat.default_reorder_max_qty
            # Linkage defaults.
            if cat.default_workcenter_ids and not rec.x_southbrook_default_workcenter_ids:
                rec.x_southbrook_default_workcenter_ids = [(6, 0, cat.default_workcenter_ids.ids)]

    # ──────────────────────────────────────────────────────────────────
    # Constraint — block release of unassessed adhesive / finishing
    # chemicals (safety three-state remediation, 19.0.2.0.0)
    # ──────────────────────────────────────────────────────────────────
    # "Released / in-service" gate value(s): {"available", "new"}.
    # x_southbrook_tool_lifecycle_state shares its 11-value selection
    # (TOOL_LIFECYCLE_SELECTION, above) with southbrook.tool.asset
    # .lifecycle_state — and tool_asset.py's own
    # _compute_is_available already treats exactly
    # ready_states = {"available", "new"} as "cleared for use"; that is
    # the one existing precedent in this module for what "released"
    # means, so the gate starts from it rather than inventing a new
    # notion of "released" — and then also covers "in_use" and
    # "checked_out": a Selection write can jump straight to any value,
    # so gating only the earliest ready states would leave a one-write
    # bypass ("set it to In Use directly") around the exact stop this
    # constraint exists to be. Out-of-service states (needs_*, under_
    # maintenance, broken, retired, scrapped) stay ungated on purpose —
    # parking an unassessed chemical as retired/broken is legitimate
    # and blocking it would train users to ignore the error.
    _RELEASED_LIFECYCLE_STATES = frozenset(
        {"new", "available", "in_use", "checked_out"})

    # The four fields this constraint watches, paired with the label
    # used in the error message. Order matches the form view's Safety
    # group.
    _SAFETY_ASSESSMENT_FIELDS = (
        ("x_southbrook_hazardous", "Hazardous"),
        ("x_southbrook_flammable", "Flammable"),
        ("x_southbrook_requires_ventilation", "Requires Ventilation"),
        ("x_southbrook_msds_required", "MSDS Required"),
    )

    @api.constrains(
        "x_southbrook_tool_lifecycle_state",
        "x_southbrook_tool_category_id",
        "x_southbrook_hazardous",
        "x_southbrook_flammable",
        "x_southbrook_requires_ventilation",
        "x_southbrook_msds_required",
    )
    def _check_safety_assessed_before_release(self):
        """A product cannot be marked released/in-service
        (lifecycle state 'available' or 'new' — see
        ``_RELEASED_LIFECYCLE_STATES`` above) while any of the four
        safety fields is still 'not_assessed', if its tool category
        sits inside the cat_adhesives or cat_finishing subtree — the
        two category roots the recon confirmed actually carry
        meaningful chemical-safety data (glues, sealants, paints,
        thinners, degreasers). Cutting tooling, fasteners, screws,
        etc. are never gated by this constraint even if released: an
        unassessed MSDS flag on a saw blade is not a live safety
        question, so blocking it would just train users to ignore the
        error.

        Governing rule: "Absence of data never renders as good." This
        is the one place in the module that turns that rule into an
        actual hard stop rather than just a rendering convention.
        """
        Category = self.env["southbrook.tool.category"]
        adhesives_root = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_adhesives",
            raise_if_not_found=False)
        finishing_root = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_finishing",
            raise_if_not_found=False)
        gated_roots = (adhesives_root or Category.browse()) | (
            finishing_root or Category.browse())
        if not gated_roots:
            # Seed data not loaded (e.g. a stripped-down test DB with
            # this module's models but not its data/ fixtures) —
            # nothing to gate against. Fail open rather than raise on
            # a missing xml_id that has nothing to do with the record
            # actually being saved.
            return

        state_labels = dict(TOOL_LIFECYCLE_SELECTION)

        for rec in self:
            if (rec.x_southbrook_tool_lifecycle_state
                    not in self._RELEASED_LIFECYCLE_STATES):
                continue
            cat = rec.x_southbrook_tool_category_id
            if not cat:
                continue
            # child_of semantics via parent_path (southbrook.tool.category
            # is _parent_store=True): a category's own parent_path always
            # starts with each of its ancestors' parent_path, including
            # itself — so this also matches products classified directly
            # on cat_adhesives / cat_finishing, not just their children.
            in_gated_subtree = any(
                cat.parent_path and root.parent_path
                and cat.parent_path.startswith(root.parent_path)
                for root in gated_roots
            )
            if not in_gated_subtree:
                continue

            unassessed = [
                label for field_name, label in self._SAFETY_ASSESSMENT_FIELDS
                if rec[field_name] == "not_assessed"
            ]
            if unassessed:
                raise ValidationError(_(
                    "%(product)s cannot be released (lifecycle state "
                    "\"%(state)s\") with unassessed safety data: "
                    "%(fields)s. Set each one to Yes or No before "
                    "releasing an adhesive or finishing product — "
                    "absence of data never renders as good."
                ) % {
                    "product": rec.display_name,
                    "state": state_labels.get(
                        rec.x_southbrook_tool_lifecycle_state,
                        rec.x_southbrook_tool_lifecycle_state),
                    "fields": ", ".join(unassessed),
                })
