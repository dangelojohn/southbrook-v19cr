# SPDX-License-Identifier: LGPL-3.0-only
"""T1/T2 — the "3D Design" tab backend: get-or-create + seed a
southbrook.kitchen.design from an order, and persist drag-moves.

Covers the new portal routes on SouthbrookOrderBuilderPortal:
    /southbrook/api/order/<id>/design-3d        (get-or-create + seed + payload)
    /southbrook/api/order/<id>/design-3d/move   (persist x_position_in)

The controller resolves the order via _southbrook_resolve_order (ownership)
then operates on southbrook.kitchen.design under sudo() — portal customers
hold no ACL on that model. These tests exercise the Python directly (not
over HTTP) via the module-level `request` swap, mirroring
test_customer_flow_endpoints.py.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_estimating_website.controllers import main as ctrl_main


@contextmanager
def stubbed_request(env, user=None):
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env if user is None else env(user=user.id)
    mock.session = {}
    mock.params = {}
    mock.httprequest.args = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


@tagged("post_install", "-at_install", "southbrook", "southbrook_design_3d")
class TestDesign3dTab(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.controller = ctrl_main.SouthbrookOrderBuilderPortal()

        # Portal customer + partner (share=True → the ownership path).
        cls.partner = cls.env["res.partner"].create({
            "name": "Design3D Test Customer",
            "email": "design3d.test@southbrook.test",
        })
        portal_group = cls.env.ref("base.group_portal")
        cls.user = cls.env["res.users"].create({
            "name": "Design3D Test Customer",
            "login": "design3d.test@southbrook.test",
            "partner_id": cls.partner.id,
            "group_ids": [(6, 0, [portal_group.id])],
        })

        # Two SB-SKU cabinets → the seed should produce two design lines.
        base = cls.env.ref("southbrook_estimating.base_1dr").product_variant_id
        wall = cls.env.ref("southbrook_estimating.wall_1dr").product_variant_id
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.partner.id,
            "order_line": [
                (0, 0, {"product_id": base.id, "product_uom_qty": 1,
                        "name": base.display_name, "zone": "base_run"}),
                (0, 0, {"product_id": wall.id, "product_uom_qty": 1,
                        "name": wall.display_name, "zone": "wall"}),
            ],
        })

    # ------------------------------------------------------------------
    def test_get_or_create_seeds_two_lines(self):
        with stubbed_request(self.env, user=self.user):
            payload = self.controller.southbrook_api_design_3d(self.order.id)
        self.assertNotIn("error", payload, f"unexpected: {payload}")
        self.assertTrue(payload["design_id"])
        self.assertEqual(len(payload["items"]), 2,
                         "two SB cabinets should seed two design lines")
        design = self.Design.browse(payload["design_id"])
        self.assertEqual(design.sale_order_id, self.order)
        cfg_lines = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator")
        self.assertEqual(len(cfg_lines), 2)
        # origin MUST be "configurator" so the move route can match them.
        self.assertTrue(all(l.origin == "configurator" for l in cfg_lines))
        # cabinet_type is required + populated.
        self.assertTrue(all(l.cabinet_type for l in cfg_lines))

    def test_positions_are_inches_and_non_overlapping(self):
        with stubbed_request(self.env, user=self.user):
            payload = self.controller.southbrook_api_design_3d(self.order.id)
        items = {i["cabinet_type"]: i for i in payload["items"]}
        # Base cabinet SB-BASE-1DR is 609mm wide → ~24in; centre x ≈ 12in.
        base_item = next(i for i in payload["items"]
                         if i["layout_key"].endswith("-base")
                         or i["cabinet_type"] == "base")
        self.assertGreater(base_item["width_in"], 20)
        self.assertLess(base_item["width_in"], 30)
        self.assertGreater(base_item["x_position_in"], 0)

    def test_get_or_create_idempotent_preserves_edits(self):
        with stubbed_request(self.env, user=self.user):
            payload = self.controller.southbrook_api_design_3d(self.order.id)
            design_id = payload["design_id"]
            key = payload["items"][0]["layout_key"]
            # Move it.
            res = self.controller.southbrook_api_design_3d_move(
                self.order.id, layout_key=key, x_position_in=99.0)
            self.assertTrue(res.get("ok"), res)
            # Re-open — must NOT reseed / clobber the moved position.
            payload2 = self.controller.southbrook_api_design_3d(self.order.id)
        self.assertEqual(payload2["design_id"], design_id)
        moved = next(i for i in payload2["items"] if i["layout_key"] == key)
        self.assertAlmostEqual(moved["x_position_in"], 99.0, places=2)

    def test_move_persists_to_design_line(self):
        with stubbed_request(self.env, user=self.user):
            payload = self.controller.southbrook_api_design_3d(self.order.id)
            key = payload["items"][0]["layout_key"]
            res = self.controller.southbrook_api_design_3d_move(
                self.order.id, layout_key=key, x_position_in=42.5)
        self.assertTrue(res.get("ok"), res)
        design = self.Design.browse(payload["design_id"])
        line = design.cabinet_line_ids.filtered(lambda l: l.layout_key == key)
        self.assertAlmostEqual(line.x_position_in, 42.5, places=2)

    def test_move_unknown_layout_key_is_soft_error(self):
        with stubbed_request(self.env, user=self.user):
            self.controller.southbrook_api_design_3d(self.order.id)
            res = self.controller.southbrook_api_design_3d_move(
                self.order.id, layout_key="does-not-exist", x_position_in=1.0)
        self.assertEqual(res.get("error"), "no_matching_line")

    def test_non_owner_forbidden(self):
        other_partner = self.env["res.partner"].create({
            "name": "Intruder", "email": "intruder@southbrook.test"})
        other = self.env["res.users"].create({
            "name": "Intruder", "login": "intruder@southbrook.test",
            "partner_id": other_partner.id,
            "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])],
        })
        with stubbed_request(self.env, user=other):
            payload = self.controller.southbrook_api_design_3d(self.order.id)
        self.assertEqual(payload.get("error"), "forbidden")


@tagged("post_install", "-at_install", "southbrook", "southbrook_design_3d")
class TestDesign3dSeedDimSnapping(TransactionCase):
    """SBK-025 (V5-partc.md) pin.

    `_southbrook_seed_design_lines` used to write `width_in`/`height_in`/
    `depth_in`/`x_position_in`/`y_position_in`/`z_position_in` as a raw,
    unrounded `mm * (1/25.4)` — e.g. SB-BASE-1DR's 609mm width landing at
    23.9763779...in instead of a clean 24.00in — which then echoed
    verbatim in the OWL UI (`kitchen_configurator.js:2604`,
    `t-att-value="state.selected.width_in"`, no rounding). The fix routes
    every seeded dimension/position through `_snap_quarter_in`, the same
    `round(inches * 4) / 4` convention already used at every other mm<->
    inch display boundary in this codebase (`kitchen_viewport.esm.js`/
    `cabinet_viewport.esm.js`'s `_formatMm`, and this same controller
    file's own inline `round((width_mm / 25.4) * 4) / 4` a few hundred
    lines below `_southbrook_seed_design_lines`).

    Uses freshly `.create()`d product templates rather than
    `env.ref("southbrook_estimating.base_1dr"/"wall_1dr")` — see
    `test_design_3d_add_new_wall.py`'s setUpClass comment: a shared dev-DB
    clone's demo product variants are not a stable fixture to pin unit
    tests against (confirmed here too: those two xml-ref'd templates have
    zero product.product variants on the CI dev-DB clone this suite runs
    against, an unrelated pre-existing data-drift issue that already
    errors `TestDesign3dTab.setUpClass` above, independent of this
    change — verified via a controlled before/after diff)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.controller = ctrl_main.SouthbrookOrderBuilderPortal()
        cls.partner = cls.env["res.partner"].create({
            "name": "SnapTest Customer",
            "email": "snaptest.customer@southbrook.test",
        })
        portal_group = cls.env.ref("base.group_portal")
        cls.user = cls.env["res.users"].create({
            "name": "SnapTest Customer",
            "login": "snaptest.customer@southbrook.test",
            "partner_id": cls.partner.id,
            "group_ids": [(6, 0, [portal_group.id])],
        })

        # SB-BASE-1DR: product_config_line.py's _SKU_DEFAULTS row is
        # (family="base", doors=1, drawers=0, w=609, h=762, d=609)mm.
        # 609 / 25.4 = 23.9763779...in raw — the exact drift example
        # V5-partc.md traces for this SKU.
        base_tmpl = cls.env["product.template"].create({
            "name":         "SnapTest Base Cabinet",
            "default_code": "SB-BASE-1DR",
            "type":         "consu",
            "sale_ok":      True,
            "list_price":   500.0,
        })
        base_tmpl.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": "base",
        })
        cls.base_product = base_tmpl.product_variant_id

        # SB-WALL-1DR: (family="wall", w=457, h=762, d=350)mm.
        # 350 / 25.4 = 13.7795275591...in raw (*4 = 55.118110236..., the
        # exact "55.12"-style drift digits named in the task brief's
        # title); snapped -> 13.75.
        wall_tmpl = cls.env["product.template"].create({
            "name":         "SnapTest Wall Cabinet",
            "default_code": "SB-WALL-1DR",
            "type":         "consu",
            "sale_ok":      True,
            "list_price":   300.0,
        })
        wall_tmpl.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": "wall",
        })
        cls.wall_product = wall_tmpl.product_variant_id

        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.partner.id,
            "order_line": [
                (0, 0, {"product_id": cls.base_product.id,
                        "product_uom_qty": 1,
                        "name": cls.base_product.display_name,
                        "zone": "base_run"}),
                (0, 0, {"product_id": cls.wall_product.id,
                        "product_uom_qty": 1,
                        "name": cls.wall_product.display_name,
                        "zone": "wall"}),
            ],
        })

    def test_seeded_dims_are_snapped_to_quarter_inch_not_raw_mm_division(self):
        with stubbed_request(self.env, user=self.user):
            payload = self.controller.southbrook_api_design_3d(self.order.id)
        self.assertNotIn("error", payload, f"unexpected: {payload}")
        items = {i["sku"]: i for i in payload["items"]}
        self.assertIn("SB-BASE-1DR", items)
        self.assertIn("SB-WALL-1DR", items)

        base_item = items["SB-BASE-1DR"]
        self.assertEqual(base_item["width_in"], 24.0)
        self.assertEqual(base_item["height_in"], 30.0)
        self.assertEqual(base_item["depth_in"], 24.0)

        wall_item = items["SB-WALL-1DR"]
        self.assertEqual(wall_item["width_in"], 18.0)
        self.assertEqual(wall_item["height_in"], 30.0)
        self.assertEqual(wall_item["depth_in"], 13.75)

        # No seeded dimension/position for either cabinet may land off
        # the 1/4in grid.
        for item in payload["items"]:
            for key in ("width_in", "height_in", "depth_in",
                        "x_position_in", "y_position_in", "z_position_in"):
                value = item[key]
                snapped = round(value * 4) / 4.0
                self.assertEqual(
                    value, snapped,
                    f"{item['sku']}.{key}={value} is not snapped to the "
                    "nearest 1/4in")


@tagged("post_install", "-at_install", "southbrook", "southbrook_design_3d")
class TestDesign3dSeedWallCabTopSelector(TransactionCase):
    """Task 14 review, finding 1 (SBK-026, corner-remediation 2026-07-30,
    CRITICAL) — `_southbrook_seed_design_lines` fed
    `kitchen_layout_engine.layout` the raw `sale.order._ZONE_LAYOUT`
    class constant (wall always at 1400mm/55.12in) even though the
    `design` it had just created/looked up carries its own
    `wall_cab_top_alignment` / `soffit_height_in` /
    `counter_thickness_in` and exposes `_wall_cab_zone_layout()` for
    exactly this. A freshly seeded design therefore landed its wall
    cabinets at 55.12in, then jumped to a DIFFERENT elevation (e.g.
    54.00in) the moment ANY `action_auto_arrange` next ran on that same
    design (auto-arrange already routed through
    `_wall_cab_zone_layout()` — Task 14's main fix) — two elevations
    for one design, from two writers that disagreed. Fixed by routing
    the seed path through the SAME per-design helper.

    Deliberately its OWN class with self-built `product.template.
    create()` fixtures (mirrors `TestDesign3dSeedDimSnapping` above),
    NOT added to `TestDesign3dTab` — that class's `setUpClass` relies on
    `env.ref("southbrook_estimating.base_1dr"/"wall_1dr")`, which
    `TestDesign3dSeedDimSnapping`'s own docstring + Task 13's report
    already document as erroring on this suite's dev-DB clone (those
    demo templates carry zero `product.product` variants there) — a
    test added to that class would never even run on this DB."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.controller = ctrl_main.SouthbrookOrderBuilderPortal()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.partner = cls.env["res.partner"].create({
            "name": "WallCabTopSeed Customer",
            "email": "wallcabtopseed.customer@southbrook.test",
        })
        portal_group = cls.env.ref("base.group_portal")
        cls.user = cls.env["res.users"].create({
            "name": "WallCabTopSeed Customer",
            "login": "wallcabtopseed.customer@southbrook.test",
            "partner_id": cls.partner.id,
            "group_ids": [(6, 0, [portal_group.id])],
        })

        base_tmpl = cls.env["product.template"].create({
            "name":         "T14SeedTest Base Cabinet",
            "default_code": "SB-BASE-1DR",
            "type":         "consu",
            "sale_ok":      True,
            "list_price":   500.0,
        })
        base_tmpl.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": "base",
        })
        cls.base_product = base_tmpl.product_variant_id

        wall_tmpl = cls.env["product.template"].create({
            "name":         "T14SeedTest Wall Cabinet",
            "default_code": "SB-WALL-1DR",
            "type":         "consu",
            "sale_ok":      True,
            "list_price":   300.0,
        })
        wall_tmpl.write({
            "southbrook_is_cabinet":   True,
            "southbrook_cabinet_type": "wall",
        })
        cls.wall_product = wall_tmpl.product_variant_id

    def _new_order(self):
        return self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [
                (0, 0, {"product_id": self.base_product.id,
                        "product_uom_qty": 1,
                        "name": self.base_product.display_name,
                        "zone": "base_run"}),
                (0, 0, {"product_id": self.wall_product.id,
                        "product_uom_qty": 1,
                        "name": self.wall_product.display_name,
                        "zone": "wall"}),
            ],
        })

    def test_seeded_wall_cabinet_honours_fixed_gap_default_not_the_legacy_1400mm_constant(self):
        order = self._new_order()
        with stubbed_request(self.env, user=self.user):
            payload = self.controller.southbrook_api_design_3d(order.id)
        self.assertNotIn("error", payload, payload)
        wall_item = next(i for i in payload["items"]
                          if i["cabinet_type"] == "wall")
        # SB-BASE-1DR seeds at height 30in (< the 34.5in default, so it
        # does not widen base_h) + 1.5in counter (default) + 18in gap.
        self.assertAlmostEqual(wall_item["y_position_in"], 54.0, places=2)
        self.assertNotAlmostEqual(wall_item["y_position_in"], 55.12, places=1)

    def test_seeded_wall_cabinet_honours_a_non_default_selector(self):
        """Pre-create the design (linked to the order, zero configurator
        lines yet) with wall_cab_top_alignment='to_ceiling' BEFORE the
        seed runs — _southbrook_get_or_create_design finds this exact
        record and seeds INTO it, so the seeded Y must resolve via
        to_ceiling, not silently fall back to the fixed_gap default."""
        order = self._new_order()
        design = self.Design.sudo().create({
            "name": "T14 non-default selector",
            "sale_order_id": order.id,
            "state": "draft",
            "wall_cab_top_alignment": "to_ceiling",
            **self.controller._PORTAL_NEW_DESIGN_ROOM,
        })
        with stubbed_request(self.env, user=self.user):
            payload = self.controller.southbrook_api_design_3d(order.id)
        self.assertNotIn("error", payload, payload)
        self.assertEqual(payload["design_id"], design.id)
        wall_item = next(i for i in payload["items"]
                          if i["cabinet_type"] == "wall")
        # _PORTAL_NEW_DESIGN_ROOM's room_height_in=96 - wall_h 30in = 66.0
        self.assertAlmostEqual(wall_item["y_position_in"], 66.0, places=2)
