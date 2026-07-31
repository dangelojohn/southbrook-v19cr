# SPDX-License-Identifier: LGPL-3.0-only
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestDetail(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Provider = self.env["materials.catalog.provider"]
        self.cat = self.env.ref("southbrook_mrp_kitchen_tools.cat_adhesives")
        # Reuse seeded col_adh_open instead of creating a duplicate
        # (category, field_name) pair which would violate the uniqueness constraint.
        self.env["materials.catalog.column"].search([
            ("category_id", "=", self.cat.id),
            ("field_name", "=", "x_southbrook_open_time_min")
        ]) or self.env["materials.catalog.column"].create({
            "name": "Open time (min)", "category_id": self.cat.id,
            "field_name": "x_southbrook_open_time_min", "align": "right",
        })
        self.vendor = self.env["res.partner"].create({
            "name": "TEST Adhesive Supply Co",
        })
        self.uom = self.env.ref("uom.product_uom_unit")
        self.tmpl = self.env["product.template"].create({
            "name": "TEST PVA Type II",
            "x_southbrook_tool_category_id": self.cat.id,
            "x_southbrook_open_time_min": 8.0,
            "x_southbrook_hazardous": "yes",
            "x_southbrook_min_stock_qty": 4.0,
            "x_southbrook_preferred_vendor_id": self.vendor.id,
            "x_southbrook_issue_uom_id": self.uom.id,
        })
        self.product = self.tmpl.product_variant_ids[0]

    def test_detail_has_title_and_specs(self):
        detail = self.Provider.get_detail(self.product.id)
        self.assertTrue(detail["ok"])
        self.assertIn("TEST PVA Type II", detail["title"])
        labels = {s["label"]: s["value"] for s in detail["specs"]}
        self.assertEqual(labels["Open time (min)"], 8.0)

    def test_hazard_badge_present(self):
        """x_southbrook_hazardous is "yes" on this fixture: it renders as
        the existing danger-style badge, {label, kind}.
        """
        detail = self.Provider.get_detail(self.product.id)
        by_label = {b["label"]: b for b in detail["badges"]}
        self.assertEqual(by_label["Hazardous"]["kind"], "danger")

    def test_safety_badges_never_omitted(self):
        """The four safety fields are tri-state (see
        southbrook_mrp_kitchen_tools's safety-flag conversion) and must
        NEVER be silently omitted from the badges list — omitting "not
        assessed" is exactly the "absence renders as safe" failure this
        wave exists to close. Only x_southbrook_hazardous is explicitly
        "yes" on this fixture; the other three safety fields were never
        touched and default to "not_assessed", but they must still show up
        as their own distinct "unset" badge, not silently disappear the
        way the old omit-if-falsy behaviour would have dropped them.
        Expiry tracked (x_southbrook_expiry_required) is NOT one of the
        four safety fields and keeps the original omit-if-falsy badge
        behaviour — it must stay absent here.
        """
        detail = self.Provider.get_detail(self.product.id)
        by_label = {b["label"]: b for b in detail["badges"]}
        self.assertEqual(by_label["Hazardous"]["kind"], "danger")
        self.assertEqual(by_label["Flammable: not assessed"]["kind"], "unset")
        self.assertEqual(
            by_label["MSDS required: not assessed"]["kind"], "unset")
        self.assertEqual(
            by_label["Ventilation required: not assessed"]["kind"], "unset")
        self.assertNotIn("Expiry tracked", by_label)

    def test_safety_badge_no_gets_its_own_ok_badge(self):
        """A verified "no" is real information, not nothing — it gets its
        own distinctly labelled "ok"-kind badge, never the same label as a
        genuine "yes" and never folded into silence the way a plain
        Boolean False would have been omitted.
        """
        tmpl = self.env["product.template"].create({
            "name": "TEST Verified Non-hazardous Adhesive",
            "x_southbrook_tool_category_id": self.cat.id,
            "x_southbrook_hazardous": "no",
        })
        detail = self.Provider.get_detail(tmpl.product_variant_ids[0].id)
        by_label = {b["label"]: b for b in detail["badges"]}
        self.assertEqual(by_label["Non-hazardous"]["kind"], "ok")
        self.assertNotIn("Hazardous", by_label)
        self.assertNotIn("Hazardous: not assessed", by_label)

    def test_engineering_rail_includes_min_stock(self):
        detail = self.Provider.get_detail(self.product.id)
        labels = {e["label"]: e["value"] for e in detail["engineering"]}
        self.assertEqual(labels["Min stock"], 4.0)

    def test_unknown_product_degrades(self):
        detail = self.Provider.get_detail(-1)
        self.assertFalse(detail["ok"])

    def test_product_with_no_tool_category_is_out_of_scope(self):
        """get_detail is a TOOLS-catalog endpoint. A product with no
        x_southbrook_tool_category_id at all must degrade rather than serve
        its (unrelated) vendor/stock/lifecycle engineering rail — the spec
        grid was already correctly empty for such a product (no category ->
        no declared columns), but ENGINEERING_FIELDS was being read
        unconditionally, so any product id on the instance, tool or not,
        returned a fully populated engineering rail.
        """
        uncategorized = self.env["product.template"].create({
            "name": "TEST No-category widget",
        })
        detail = self.Provider.get_detail(
            uncategorized.product_variant_ids[0].id)
        self.assertFalse(detail["ok"])
        self.assertFalse(detail["engineering"])
        self.assertFalse(detail["specs"])

    def test_archived_product_degrades(self):
        """browse()/exists() both ignore the `active` flag, so an archived
        product still satisfies `exists()` even though get_catalog's own
        search()-based listing would never surface it again. A stale link
        to a discontinued item must not keep working forever.
        """
        self.product.write({"active": False})
        detail = self.Provider.get_detail(self.product.id)
        self.assertFalse(detail["ok"])

    def test_null_vs_zero_and_unset_char_via_get_detail(self):
        """Same three absence states as test_rows's
        test_null_vs_zero_and_unset_char_via_get_catalog, proven through
        get_detail() instead — the row and the detail panel must never
        disagree about NULL vs. a genuine stored 0 (they share the same
        normalization path).
        """
        zero_tmpl = self.env["product.template"].create({
            "name": "TEST Zero open-time adhesive",
            "x_southbrook_tool_category_id": self.cat.id,
            "x_southbrook_open_time_min": 0.0,
        })
        unset_tmpl = self.env["product.template"].create({
            "name": "TEST Unset open-time adhesive",
            "x_southbrook_tool_category_id": self.cat.id,
        })
        zero_detail = self.Provider.get_detail(
            zero_tmpl.product_variant_ids[0].id)
        unset_detail = self.Provider.get_detail(
            unset_tmpl.product_variant_ids[0].id)
        zero_specs = {s["label"]: s["value"] for s in zero_detail["specs"]}
        unset_specs = {s["label"]: s["value"] for s in unset_detail["specs"]}
        self.assertEqual(zero_specs["Open time (min)"], 0.0)
        self.assertIsNone(unset_specs["Open time (min)"])

    def test_engineering_relation_fields_show_display_name(self):
        """A many2one engineering field (vendor, issue UoM) must render its
        display name string in the payload — never a raw id, and never the
        (id, display_name) tuple Odoo's read() returns internally.
        """
        detail = self.Provider.get_detail(self.product.id)
        labels = {e["label"]: e["value"] for e in detail["engineering"]}
        self.assertEqual(labels["Preferred vendor"], self.vendor.display_name)
        self.assertEqual(labels["Issue UoM"], self.uom.display_name)
        self.assertNotIsInstance(labels["Preferred vendor"], tuple)
        self.assertNotIsInstance(labels["Preferred vendor"], int)
