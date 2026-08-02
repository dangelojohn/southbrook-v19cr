# SPDX-License-Identifier: LGPL-3.0-only
"""E2E P0/P1 remediation (2026-08-02) — three configurator payload gaps
the client's E2E pass surfaced, all traced to server-side serialization
(not product/BoM resolution, which was already correct):

  Fix A (P0) — action_open_configurator's params carried partner_id but
      never partner_name, so the client's `partnerName: params.
      partner_name || ""` seed left the customer chip an empty grey pill
      for every design that HAS a customer. Also echoed onto
      _get_counter_summary() (the design-level dict load_design_lines/
      rearrange already both source their "summary" key from) so a
      direct-URL reload — which skips action_open_configurator's params
      entirely — still rehydrates the name.

  Fix B (P1) — _serialize_configurator_lines() (the single source of
      truth for load_design_lines/rearrange/save's line shape) never
      forwarded sku/material/bom_available, so the configurator's line
      list showed "—" for all three on every loaded line even though
      the underlying products resolve to real, priced catalog SKUs.

  Fix C (P1) — _compute_position_label() always read x_position_in.
      Per kitchen_layout_engine._WALL_SPEC's axis table, back/front runs
      advance along world X but left/right runs advance along world Z —
      on left/right walls x_position_in is the CROSS (wall-anchor)
      offset, constant for the whole run, not the along-wall coordinate.
      Two cabinets on different walls could therefore render the
      identical "Base @ X=..." label.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "e2e_p0")
class TestConfiguratorPayload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.Template = cls.env["product.template"]
        cls.Partner = cls.env["res.partner"]

        cls.partner = cls.Partner.create({
            "name": "E2E P0 Test Customer",
            "email": "e2e.p0@southbrook.test",
        })

        # A product WITH a material set and a real mrp.bom — the
        # "everything resolves" case.
        cls.tmpl_with_bom = cls.Template.create({
            "name":       "E2E P0 Base w/ BoM",
            "type":       "consu",
            "sale_ok":    True,
            "list_price": 300.0,
            "default_code": "E2E-P0-BASE-BOM",
        })
        cls.tmpl_with_bom.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": "base",
            "southbrook_material":     "maple_veneer",
        })
        cls.product_with_bom = cls.tmpl_with_bom.product_variant_id
        cls.env["mrp.bom"].create({
            "product_tmpl_id": cls.tmpl_with_bom.id,
            "product_qty": 1.0, "type": "normal",
        })

        # A product with NO bom and material left at whatever the field
        # default resolves to cleared — the "nothing extra to report"
        # case, to prove bom_available/material degrade rather than
        # crash or lie.
        cls.tmpl_no_bom = cls.Template.create({
            "name":       "E2E P0 Base w/o BoM",
            "type":       "consu",
            "sale_ok":    True,
            "list_price": 250.0,
            "default_code": "E2E-P0-BASE-NOBOM",
        })
        cls.tmpl_no_bom.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": "base",
            "southbrook_material":     False,
        })
        cls.product_no_bom = cls.tmpl_no_bom.product_variant_id

    def _make_design(self, partner_id=None):
        return self.Design.create({
            "name":           "E2E P0 Test Design",
            "partner_id":     partner_id,
            "room_width_in":  144,
            "room_depth_in":  120,
            "room_height_in": 96,
        })

    # ── Fix A — partner_name ─────────────────────────────────────────────
    def test_action_open_configurator_forwards_partner_name(self):
        """A design WITH a customer must carry both partner_id and
        partner_name in the client-action params — partner_id alone is
        exactly the P0 bug (empty grey chip)."""
        design = self._make_design(partner_id=self.partner.id)
        action = design.action_open_configurator()
        params = action["params"]
        self.assertEqual(params["partner_id"], self.partner.id)
        self.assertIn("partner_name", params)
        self.assertEqual(params["partner_name"], self.partner.name)
        self.assertIsInstance(params["partner_name"], str)

    def test_action_open_configurator_partner_name_blank_safe(self):
        """A design with NO customer must not raise and must come back
        with partner_name == "" (never False/None) — the client does
        `params.partner_name || ""`, but the contract itself promises
        a blank string, not a falsy sentinel that happens to also work."""
        design = self._make_design(partner_id=None)
        self.assertFalse(design.partner_id)
        action = design.action_open_configurator()
        params = action["params"]
        self.assertFalse(params["partner_id"])
        self.assertEqual(params["partner_name"], "")

    def test_counter_summary_echoes_partner_name_for_reload_rehydration(self):
        """load_design_lines/rearrange both source their "summary" key
        from _get_counter_summary() — a direct-URL reload skips
        action_open_configurator's params entirely, so the name has to
        be reachable from here too, or a reload still shows the blank
        chip."""
        design = self._make_design(partner_id=self.partner.id)
        summary = design._get_counter_summary()
        self.assertEqual(summary["partner_name"], self.partner.name)

        design_no_partner = self._make_design(partner_id=None)
        summary_np = design_no_partner._get_counter_summary()
        self.assertEqual(summary_np["partner_name"], "")

    # ── Fix B — sku / material / bom_available ──────────────────────────
    def _add_line(self, design, product, wall="back", x=0, z=0):
        return self.Line.create({
            "design_id":      design.id,
            "product_id":     product.id,
            "quantity":       1,
            "price_unit":     product.list_price,
            "cabinet_type":   "base",
            "origin":         "configurator",
            "width_in":       24.0,
            "height_in":      34.5,
            "depth_in":       24.0,
            "x_position_in":  x,
            "y_position_in":  0,
            "z_position_in":  z,
            "wall":           wall,
        })

    def test_serialized_lines_carry_sku_material_bom_available(self):
        design = self._make_design(partner_id=self.partner.id)
        line_bom = self._add_line(design, self.product_with_bom)
        line_no_bom = self._add_line(design, self.product_no_bom)

        out = design._serialize_configurator_lines()
        by_id = {row["product_id"]: row for row in out}
        self.assertEqual(len(out), 2)

        row_bom = by_id[line_bom.product_id.id]
        self.assertEqual(row_bom["sku"], "E2E-P0-BASE-BOM")
        self.assertEqual(row_bom["material"], "maple_veneer")
        self.assertIs(row_bom["bom_available"], True)

        row_no_bom = by_id[line_no_bom.product_id.id]
        self.assertEqual(row_no_bom["sku"], "E2E-P0-BASE-NOBOM")
        self.assertEqual(row_no_bom["material"], "",
                          "no southbrook_material set -> contract blank "
                          "string, not False/None")
        self.assertIs(row_no_bom["bom_available"], False)

        # Contract types: str/str/bool on every row, not just the happy path.
        for row in out:
            self.assertIsInstance(row["sku"], str)
            self.assertIsInstance(row["material"], str)
            self.assertIsInstance(row["bom_available"], bool)

    # ── Fix C — wall-aware position label ────────────────────────────────
    def test_position_label_differs_across_walls_at_same_x(self):
        """Pre-fix, both of these rendered the identical 'Base @ X=24"'
        label: a back-wall cabinet's x_position_in IS its along-wall
        coordinate, but a left-wall cabinet's x_position_in is the
        constant cross/anchor offset — its along-wall coordinate lives
        in z_position_in instead."""
        design = self._make_design(partner_id=self.partner.id)
        back_line = self._add_line(
            design, self.product_with_bom, wall="back", x=24, z=0)
        left_line = self._add_line(
            design, self.product_no_bom, wall="left", x=24, z=60)

        self.assertNotEqual(back_line.position_label,
                             left_line.position_label)
        self.assertIn("back wall", back_line.position_label)
        self.assertIn("left wall", left_line.position_label)
        # back wall's along-axis is x -> its own x_position_in (24).
        self.assertIn("24", back_line.position_label)
        # left wall's along-axis is z -> its own z_position_in (60), NOT
        # the shared/coincidental x_position_in=24 cross offset.
        self.assertIn("60", left_line.position_label)
        self.assertNotIn("24", left_line.position_label)

    def test_position_label_right_and_front_wall_use_correct_axis(self):
        """Same axis check for the other two walls, so all four are
        pinned, not just back/left."""
        design = self._make_design(partner_id=self.partner.id)
        front_line = self._add_line(
            design, self.product_with_bom, wall="front", x=36, z=0)
        right_line = self._add_line(
            design, self.product_no_bom, wall="right", x=0, z=48)

        self.assertIn("front wall", front_line.position_label)
        self.assertIn("36", front_line.position_label)

        self.assertIn("right wall", right_line.position_label)
        self.assertIn("48", right_line.position_label)
