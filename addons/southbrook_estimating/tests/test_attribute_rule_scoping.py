# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-043 — compatibility rules must read the attribute they are about.

`sale.order.southbrook_hard_validation_issues()` blocks Confirm on two
of the four business rules in CLAUDE.md section 5. Until 19.0.9.3.0 it
implemented them by flattening every attribute of a variant into one bag
of lowercase strings and asking whether any of them contained "maple",
"contractor", "slab" and so on.

That is how the shipped L-Shape and U-Shape templates became unsellable.
`SB-WALL-CORNER` carries:

    Series        = Contractor Series
    Box Material  = White Melamine      <- legal on Contractor
    Wood Species  = Maple (Hard)        <- a veneer descriptor

The carcass rule ("Maple box only on Contemporary and Elegance") saw the
word "maple" anywhere on the variant and fired, so every corner wall
cabinet blocked its order. Roughly 35 live designs, and both shaped
templates unconditionally, because the corner SKU is a frozen
combination rather than something chosen at instantiation.

Two things make the old approach fail, and these tests pin both:
  1. attributes were not scoped — Wood Species answered a Box Material
     question;
  2. `ptav.name` can render as "Attribute: Value" rather than the bare
     value, so a substring scan can match on the attribute's own name.

The rules themselves are unchanged. Nothing here decides what IS
compatible — that is business data, and it lives in CLAUDE.md section 5.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_estimating", "sb_hard_validation")
class TestAttributeRuleScoping(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Attribute = cls.env["product.attribute"]
        cls.Value = cls.env["product.attribute.value"]
        cls.Template = cls.env["product.template"]
        cls.partner = cls.env["res.partner"].create({"name": "SBK043 Customer"})

        def attr(name, values):
            record = cls.Attribute.create({
                "name": name, "create_variant": "always"})
            return record, {
                v: cls.Value.create({"name": v, "attribute_id": record.id})
                for v in values
            }

        cls.a_series, cls.v_series = attr(
            "Series", ["Contractor Series", "Elegance Series"])
        cls.a_box, cls.v_box = attr(
            "Box Material", ["White Melamine", "Maple (Hard)"])
        cls.a_species, cls.v_species = attr(
            "Wood Species", ["Maple (Hard)", "Oak"])
        cls.a_door, cls.v_door = attr(
            "Door Style", ["Thermofoil Slab", "Five-Piece Woodgrain"])
        # A decoy: this catalog has four attributes whose name starts
        # with "Door", and only Door Style is the door rule's subject.
        cls.a_overlay, cls.v_overlay = attr(
            "Door Overlay", ["Full Overlay", "Woodgrain Edge"])

    def _variant(self, name, chosen):
        """chosen: [(attribute, value)] -> the single resulting variant."""
        tmpl = self.Template.create({
            "name": name,
            "type": "consu",
            "sale_ok": True,
            "list_price": 425.0,
            "attribute_line_ids": [
                (0, 0, {"attribute_id": a.id, "value_ids": [(6, 0, [v.id])]})
                for a, v in chosen
            ],
        })
        return tmpl.product_variant_ids[:1]

    def _issues_for(self, variant):
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": variant.id,
                "product_uom_qty": 1,
            })],
        })
        return order.southbrook_hard_validation_issues()

    # ── the regression that made the templates unsellable ─────────────
    def test_01_wood_species_maple_does_not_trip_the_carcass_rule(self):
        """SB-WALL-CORNER, reproduced: Contractor + White Melamine box +
        Maple wood species. Legal, and it must confirm."""
        variant = self._variant("SBK043 Corner Wall", [
            (self.a_series, self.v_series["Contractor Series"]),
            (self.a_box, self.v_box["White Melamine"]),
            (self.a_species, self.v_species["Maple (Hard)"]),
        ])
        codes = [i["code"] for i in self._issues_for(variant)]
        self.assertNotIn(
            "box_series_incompatible", codes,
            "Wood Species is a veneer descriptor and must never answer a "
            "Box Material question — this is the defect that made both the "
            "L-Shape and U-Shape templates impossible to sell")
        self.assertFalse(codes, f"expected a clean order, got {codes}")

    def test_02_a_real_maple_carcass_on_contractor_is_still_blocked(self):
        """The rule must keep working. Fixing a false positive by
        deleting the check would be worse than the bug."""
        variant = self._variant("SBK043 Maple Box Contractor", [
            (self.a_series, self.v_series["Contractor Series"]),
            (self.a_box, self.v_box["Maple (Hard)"]),
        ])
        codes = [i["code"] for i in self._issues_for(variant)]
        self.assertIn("box_series_incompatible", codes)

    # ── the door rule, and its decoy attribute ────────────────────────
    def test_03_contractor_with_a_woodgrain_door_is_blocked(self):
        variant = self._variant("SBK043 Contractor Woodgrain", [
            (self.a_series, self.v_series["Contractor Series"]),
            (self.a_door, self.v_door["Five-Piece Woodgrain"]),
        ])
        codes = [i["code"] for i in self._issues_for(variant)]
        self.assertIn("series_door_incompatible", codes)

    def test_04_contractor_with_its_own_slab_door_is_clean(self):
        variant = self._variant("SBK043 Contractor Slab", [
            (self.a_series, self.v_series["Contractor Series"]),
            (self.a_door, self.v_door["Thermofoil Slab"]),
            (self.a_box, self.v_box["White Melamine"]),
        ])
        self.assertFalse([i["code"] for i in self._issues_for(variant)])

    def test_05_a_woodgrain_edge_on_a_different_door_attribute_is_ignored(self):
        """Door Overlay = "Woodgrain Edge" is not a door style. Only
        Door Style is the door rule's subject."""
        variant = self._variant("SBK043 Contractor Woodgrain Edge", [
            (self.a_series, self.v_series["Contractor Series"]),
            (self.a_door, self.v_door["Thermofoil Slab"]),
            (self.a_overlay, self.v_overlay["Woodgrain Edge"]),
        ])
        codes = [i["code"] for i in self._issues_for(variant)]
        self.assertNotIn(
            "series_door_incompatible", codes,
            "this catalog has four Door* attributes; matching the "
            "attribute name by substring reintroduces the same class of "
            "false positive in a new place")

    def test_06_elegance_with_a_slab_door_is_blocked(self):
        variant = self._variant("SBK043 Elegance Slab", [
            (self.a_series, self.v_series["Elegance Series"]),
            (self.a_door, self.v_door["Thermofoil Slab"]),
        ])
        codes = [i["code"] for i in self._issues_for(variant)]
        self.assertIn("series_door_incompatible", codes)

    def test_07_a_variant_with_no_relevant_attributes_is_clean(self):
        variant = self._variant("SBK043 Plain", [
            (self.a_species, self.v_species["Oak"]),
        ])
        self.assertFalse([i["code"] for i in self._issues_for(variant)])
