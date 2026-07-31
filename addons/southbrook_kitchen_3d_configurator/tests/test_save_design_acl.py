# SPDX-License-Identifier: LGPL-3.0-only
"""Regression tests for the 2026-07-01 E2E audit fix on
`controllers/main.py:save_design` — the /save endpoint now enforces
the /products catalog contract on incoming product_ids (must be
`southbrook_is_cabinet=True AND sale_ok=True AND readable by user`).

Before the fix, an authed user could persist a design line pointing at
any product they could name — archived, non-cabinet, or hidden by
ir.rule — because the loop just called
    Product.browse(int(item["product_id"]))
with no ACL / domain filter. Downstream /load_design_lines mirrored the
line back to the client, giving an implicit read-through the catalog
contract was designed to prevent.

Design: exercise the controller method directly with a stubbed
`request`, same pattern as the sibling audits'
southbrook_estimating_website `stubbed_request` (2026-07-01).
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)


@contextmanager
def stubbed_request(env, user=None):
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env if user is None else env(user=user.id)
    mock.session = {}
    mock.params = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "save_design_acl")
class TestSaveDesignAcl(TransactionCase):
    """Regression pin — /save must enforce the /products catalog
    contract on every incoming product_id."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.partner = cls.env["res.partner"].create({
            "name": "Kitchen 3D Test Customer",
            "email": "kitchen3d.test@southbrook.test",
        })
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Save-design ACL test",
            "partner_id": cls.partner.id,
            "room_width_in": 120,
            "room_depth_in": 96,
            "room_height_in": 96,
        })

        # A CATALOG-CONFORMING cabinet — must survive the save.
        cls.good_tmpl = cls.env["product.template"].create({
            "name": "Good Cabinet (in catalog)",
            "type": "consu",
            "sale_ok": True,
            "list_price": 500.0,
        })
        cls.good_tmpl.southbrook_is_cabinet = True
        cls.good_tmpl.southbrook_cabinet_type = "base"
        cls.good_variant = cls.good_tmpl.product_variant_id
        assert cls.good_variant, "expected auto-created variant"

        # A NON-cabinet product — same shape but southbrook_is_cabinet
        # left False. Must be rejected by the /save contract.
        cls.non_cabinet_tmpl = cls.env["product.template"].create({
            "name": "Not-a-cabinet product",
            "type": "consu",
            "sale_ok": True,
            "list_price": 20.0,
        })
        cls.non_cabinet_variant = cls.non_cabinet_tmpl.product_variant_id

        # A NON-SALEABLE cabinet — southbrook_is_cabinet=True but
        # sale_ok=False. Also rejected.
        cls.archived_tmpl = cls.env["product.template"].create({
            "name": "Archived cabinet",
            "type": "consu",
            "sale_ok": False,
            "list_price": 500.0,
        })
        cls.archived_tmpl.southbrook_is_cabinet = True
        cls.archived_tmpl.southbrook_cabinet_type = "base"
        cls.archived_variant = cls.archived_tmpl.product_variant_id

        # Task 16 (SBK-028) — an appliance-space stand-in, shaped exactly
        # like the shipped SBK-APPL-* seed data: southbrook_is_cabinet
        # stays False (never in the drag catalog) but
        # southbrook_cabinet_type='appliance'. list_price is deliberately
        # NONZERO here — the whole point of the test below is that this
        # never reaches the persisted line regardless of what the catalog
        # or a channel pricelist would otherwise charge for it.
        cls.appliance_tmpl = cls.env["product.template"].create({
            "name": "Appliance Space — Test Range",
            "type": "consu",
            "sale_ok": True,
            "list_price": 999.0,
        })
        cls.appliance_tmpl.southbrook_cabinet_type = "appliance"
        cls.appliance_variant = cls.appliance_tmpl.product_variant_id
        assert cls.appliance_variant

        # Same shape, but sale_ok=False — ctype='appliance' must NOT
        # bypass the sale_ok leg of the contract.
        cls.appliance_unsaleable_tmpl = cls.env["product.template"].create({
            "name": "Appliance Space — Unsaleable",
            "type": "consu",
            "sale_ok": False,
            "list_price": 0.0,
        })
        cls.appliance_unsaleable_tmpl.southbrook_cabinet_type = "appliance"
        cls.appliance_unsaleable_variant = (
            cls.appliance_unsaleable_tmpl.product_variant_id)

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _save(self, product_ids):
        """Fire /save with the given product_ids and return the count
        of persisted lines on the design after."""
        items = [
            {
                "product_id": pid,
                "layout_key": "test-%d" % i,
                "x_position_in": i * 30,
            }
            for i, pid in enumerate(product_ids)
        ]
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=items,
                partner_id=self.partner.id,
                design_id=self.design.id,
            )
        # Line count AFTER the save.
        lines = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        return result, lines

    def test_01_good_cabinet_is_persisted(self):
        """Baseline — a catalog-conforming cabinet passes through."""
        _, lines = self._save([self.good_variant.id])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].product_id, self.good_variant)

    def test_02_non_cabinet_product_is_rejected(self):
        """Rule 1 of the catalog contract: southbrook_is_cabinet=True."""
        _, lines = self._save([self.non_cabinet_variant.id])
        self.assertEqual(
            len(lines), 0,
            "save_design must NOT persist a product whose template "
            "lacks southbrook_is_cabinet=True",
        )

    def test_03_archived_cabinet_is_rejected(self):
        """Rule 2 of the catalog contract: sale_ok=True."""
        _, lines = self._save([self.archived_variant.id])
        self.assertEqual(
            len(lines), 0,
            "save_design must NOT persist a product with sale_ok=False",
        )

    def test_04_mixed_payload_keeps_good_drops_bad(self):
        """Per-line reject: a mixed payload persists the good rows and
        drops the bad ones, rather than 500'ing the whole save."""
        _, lines = self._save([
            self.good_variant.id,
            self.non_cabinet_variant.id,
            self.archived_variant.id,
        ])
        self.assertEqual(
            len(lines), 1,
            "mixed payload should persist exactly the 1 catalog-"
            "conforming cabinet and skip the other 2",
        )
        self.assertEqual(lines[0].product_id, self.good_variant)

    def test_05_nonexistent_product_id_is_dropped_silently(self):
        """Client-supplied garbage id: skip the line, don't blow up."""
        # Pick an id we know doesn't exist by adding a big offset to
        # the good variant's id — Odoo's search returns empty, the
        # controller logs + continues.
        garbage_id = self.good_variant.id + 10_000_000
        _, lines = self._save([garbage_id])
        self.assertEqual(len(lines), 0)

    def test_07_client_price_is_ignored_server_reprices(self):
        """HIGH-1 money-vector regression: the client-supplied item['price']
        must NOT be trusted. save_design reprices every line server-side from
        the resolved channel pricelist, so a tampered price cannot flow into
        the design line (and thence verbatim into the quotation)."""
        controller = self._controller()
        with stubbed_request(self.env):
            controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=[{
                    "product_id": self.good_variant.id,
                    "layout_key": "cheap",
                    "price": 1.00,  # attacker-chosen bargain price
                }],
                partner_id=self.partner.id,
                design_id=self.design.id,
            )
        line = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
            ("layout_key", "=", "cheap"),
        ], limit=1)
        self.assertTrue(line)
        self.assertNotEqual(
            line.price_unit, 1.00,
            "client-supplied price must NOT be persisted onto the design line")
        self.assertEqual(
            line.price_unit, self.good_variant.lst_price,
            "line must be repriced server-side to the catalog/pricelist price")

    def test_06_missing_product_id_key_does_not_crash(self):
        """Malformed payload: a dict without product_id must be
        skipped rather than KeyError'd."""
        items = [
            # Well-formed item
            {"product_id": self.good_variant.id, "layout_key": "ok"},
            # Malformed — no product_id key
            {"layout_key": "broken"},
            # Malformed — non-int product_id
            {"product_id": "not-a-number", "layout_key": "also-broken"},
        ]
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=items,
                partner_id=self.partner.id,
                design_id=self.design.id,
            )
        lines = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].product_id, self.good_variant)

    # ── Task 16 (SBK-028) — the ctype='appliance' catalog widening ─────
    def test_08_appliance_product_admitted_at_zero_price(self):
        """The catalog contract's narrow ctype='appliance' OR-branch
        admits the product, but price_unit is HARD-PINNED to 0.0 — never
        routed through _channel_price — even though this product's own
        list_price is 999.0. This is the actual money-vector gate: the
        widening must never become a way to persist a non-zero-priced
        line for a product that was never meant to be sold on its own."""
        _, lines = self._save([self.appliance_variant.id])
        self.assertEqual(
            len(lines), 1,
            "an appliance-space stand-in must be admitted by the "
            "catalog contract now (Task 16)")
        self.assertEqual(lines[0].product_id, self.appliance_variant)
        self.assertEqual(lines[0].cabinet_type, "appliance")
        self.assertEqual(
            lines[0].price_unit, 0.0,
            "appliance lines must never be priceable above 0 through "
            "save_design, regardless of the product's own list_price")

    def test_09_appliance_still_requires_sale_ok(self):
        """ctype='appliance' is an OR-branch on TOP of sale_ok=True, not
        a bypass of it — an unsaleable appliance product must still be
        rejected."""
        _, lines = self._save([self.appliance_unsaleable_variant.id])
        self.assertEqual(
            len(lines), 0,
            "sale_ok=False must still reject an appliance-typed product")

    def test_10_appliance_line_survives_unchanged_resave(self):
        """Regression pin for the prod-verified bug (design 102: a
        U-shape design's RANGE slot vanished): before Task 16, an
        appliance line's product_id failed the catalog contract on
        EVERY save, so a second save_design call with the exact same
        payload the client just loaded (e.g. a template-instantiated
        design reopened in the 3D configurator and saved again with no
        changes) silently unlinked the appliance line via the stale-
        keys cleanup. Two successive identical saves must both keep it."""
        items = [
            {
                "product_id":    self.good_variant.id,
                "layout_key":    "base-1",
                "x_position_in": 0,
            },
            {
                "product_id":    self.appliance_variant.id,
                "layout_key":    "range-1",
                "x_position_in": 30,
                "appliance_type": "range",
            },
        ]
        controller = self._controller()
        with stubbed_request(self.env):
            controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=items, partner_id=self.partner.id,
                design_id=self.design.id,
            )
        lines_after_1 = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        self.assertEqual(len(lines_after_1), 2, "first save must persist both lines")
        appl_line = lines_after_1.filtered(
            lambda l: l.cabinet_type == "appliance")
        self.assertEqual(len(appl_line), 1)
        self.assertEqual(appl_line.appliance_type, "range")

        # Re-save the EXACT same payload — simulates "load, then save
        # with no edits". The appliance line must NOT be dropped.
        with stubbed_request(self.env):
            controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=items, partner_id=self.partner.id,
                design_id=self.design.id,
            )
        lines_after_2 = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        self.assertEqual(
            len(lines_after_2), 2,
            "an unchanged re-save must not silently drop the appliance line")
        self.assertEqual(
            len(lines_after_2.filtered(lambda l: l.cabinet_type == "appliance")),
            1, "appliance line must survive the second save")

    def test_11_appliance_type_round_trips_through_load_and_resave(self):
        """load_design_lines must echo appliance_type (it now persists it
        on save) so a load -> unmodified resave doesn't quietly blank
        the field back to False."""
        controller = self._controller()
        with stubbed_request(self.env):
            controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=[{
                    "product_id":     self.appliance_variant.id,
                    "layout_key":     "range-rt",
                    "x_position_in":  0,
                    "appliance_type": "range",
                }],
                partner_id=self.partner.id,
                design_id=self.design.id,
            )
        with stubbed_request(self.env):
            loaded = controller.load_design_lines(design_id=self.design.id)
        line_dict = next(
            it for it in loaded["lines"] if it["layout_key"] == "range-rt")
        self.assertEqual(line_dict["appliance_type"], "range")

        # Resave the exact dict the client just loaded, unmodified.
        with stubbed_request(self.env):
            controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=[line_dict],
                partner_id=self.partner.id,
                design_id=self.design.id,
            )
        line = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
            ("layout_key", "=", "range-rt"),
        ], limit=1)
        self.assertEqual(
            line.appliance_type, "range",
            "appliance_type must survive an unmodified load -> resave round trip")
