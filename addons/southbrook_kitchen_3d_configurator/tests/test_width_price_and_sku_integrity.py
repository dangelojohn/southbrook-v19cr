# SPDX-License-Identifier: LGPL-3.0-only
"""P1 (money/data-integrity remediation, 2026-08-03) — C3 + H10.

REVISION HISTORY — why this file no longer tests what it tested at
first landing:

The very first cut of C3 made two `_check_production_ready` checks
BLOCKING: CUSTOM_WIDTH_UNCONFIRMED (a dialed width diverging from the
product's nominal `placed_width_in()` without an explicit
`is_custom_width` acknowledgement) and MISSING_SKU (a line whose
product carries no `default_code`). A live test run against real data
proved both calls wrong, not just untidy:

  * 13 pre-existing end-to-end flows (quotation, reconcile, spec-sheet,
    savepoint) broke, because they legitimately quote products whose
    `default_code` was never backfilled — a catalog-data gap the rep
    quoting the kitchen did not create and has no way to fix from the
    configurator. Blocking punished the wrong person for the wrong
    problem.
  * `test_action_create_quotation_carries_drag_resized_dims_to_so_line`
    (test_geo_b3_line_dims.py) failed outright, because it asserts
    drag-to-resize is a supported, first-class feature: a base cabinet
    dragged to a non-nominal width must still reach the sale-order line
    with its resized dimensions and must still be quotable. Making
    every non-nominal width a blocking condition would have made that
    shipped feature un-quotable the moment anyone actually used it.

The fix was not "loosen the assertions" — it was "loosen the design",
and this file now pins the corrected design instead of the one that
broke it:

  1. The MONEY-CRITICAL half of `is_custom_width` — whether the LINE
     GETS PRO-RATED AT ALL — is SERVER-AUTHORITATIVE. `save_design`
     computes its own local custom/not-custom verdict from the
     measured delta (`abs(dialed_width - placed_width_in(product)) >
     0.01`, never for appliances/sentinels) and prices from THAT, not
     from whatever the client claims. A client that posts
     `is_custom_width=True` at a nominal width must NOT buy a price
     change: that was the price-manipulation vector this whole check
     exists to close, and it is the one behaviour in this file that is
     tested as security, not just correctness (see
     `test_client_cannot_manipulate_price_by_lying_about_custom_width_
     at_nominal_width` below). Read literally, `save_design`'s own
     line_vals still writes the PERSISTED `is_custom_width` field
     verbatim from `item.get("is_custom_width")` — it does not
     re-derive that stored flag from geometry the way the price and
     the CUSTOM_WIDTH validator below do. That asymmetry is real
     (verified against a live Odoo 19 instance, not assumed) and this
     file pins it rather than papering over it:
     `test_save_design_prices_unconditionally_even_when_client_omits_
     the_flag` proves the PRICE never needs the client's claim; the
     ratio-arithmetic tests supply `is_custom_width=True` from the
     "client" (exactly what the real width-edit handler does per that
     field's own docstring in kitchen_design.py) because that is the
     one input that actually decides the STORED flag today.
  2. Pricing is UNCONDITIONAL on the measured delta, not gated behind
     an acknowledgement: whenever the dialed width differs from
     nominal, `price_unit = round(channel_price * dialed / nominal,
     2)` — regardless of what `is_custom_width` the client posted.
     There is no "confirmed vs. unconfirmed" price fork any more.
  3. The old BLOCKING check `CUSTOM_WIDTH_UNCONFIRMED` is GONE. Its
     replacement, `CUSTOM_WIDTH`, is severity `"warning"` — it still
     tells the rep the line is dialed non-standard and re-priced
     pro-rata, it just never stops the quote. It also now SKIPS
     `cabinet_type == "appliance"` lines outright: an appliance's
     width is supposed to vary (a 30" range and a 36" fridge share one
     placeholder product) and it is never priced from the channel
     pricelist at all, so flagging its width against a "nominal" is
     pure noise.
  4. `MISSING_SKU` is now severity `"warning"` too, with copy that
     names the fix ("...can be quoted, but it can't be ordered until
     an estimator sets a Reference (SKU)...") instead of copy that
     implied the rep did something wrong.

SECOND WAVE (adversarial review, same day) — three more gaps the first
pass missed, each closed in the model/controller and pinned here:

  5. SERVER-SIDE WIDTH BOUND. Once width divides into price, an
     unbounded posted `width_in` is a direct line to a ~$0 or negative
     price. `save_design` now clamps every posted width into
     `[_SBK_MIN_WIDTH_IN, _SBK_MAX_WIDTH_IN]` (3.0in-96.0in) BEFORE it
     touches geometry or price — a near-zero, negative, or absurdly
     large payload all land on a sane, positive price instead of an
     exploitable one.
  6. The CUSTOM_WIDTH appliance exclusion above (item 3) is pinned
     directly: an appliance line and a cabinet line at the IDENTICAL
     width delta must diverge — silent vs. warned.
  7. C6's template tiebreak (`kitchen_template.py
     _sb_resolve_template_tiebreak`) now excludes candidates priced at
     or below `_SBK_PLACEHOLDER_PRICE_CEILING` (1.0) whenever a
     real-priced candidate also ties — this catalog ships 20+ genuine
     $1.00 hardware placeholders, and a naive "cheapest wins" tiebreak
     would hand a real cabinet's slot to one of them for a dollar.
     Unrelated to C3/H10's money/SKU story except by ship date, but
     this is the file the coordinator asked it be pinned in.

This file proves, with real Odoo data (TransactionCase, no
monkeypatching):
  - `is_custom_width` defaults False on a plain line create.
  - A width dialed AWAY from nominal through the real `save_design`
    controller path, acknowledged by the client the way the real
    width-edit handler does, flips the persisted `is_custom_width` to
    True and prices the line by the actual ratio arithmetic
    (`round(base * dialed / nominal, 2)`), not merely "some different
    number".
  - That same ratio arithmetic fires even when the client's payload
    omits `is_custom_width` entirely — pricing reads the measured
    delta, not the client's claim.
  - The manipulation vector: posting `is_custom_width=True` at the
    product's own nominal width buys nothing — no pro-rata, the plain
    channel price.
  - A width dialed BELOW nominal pro-rates the price DOWN, checked by
    direction (`<`) as well as by ratio, so the arithmetic can't
    silently invert and still pass.
  - A posted width near zero, negative, or far oversized is clamped
    into `[3.0, 96.0]` before pricing — never a ~$0 or negative line.
  - CUSTOM_WIDTH surfaces at severity "warning" (never "blocking"),
    skips appliance lines outright, and the design is still quotable
    end-to-end through `action_create_quotation`.
  - MISSING_SKU surfaces at severity "warning" (never "blocking") and
    likewise never stops `action_create_quotation`.
  - A clean, standard-width, SKU'd design raises neither code.
  - `_sb_resolve_template_tiebreak` never lets a $1.00 catalog
    placeholder win a tie against a real-priced candidate, and an
    all-placeholder tie still resolves to exactly one record via the
    documented total order.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)


@contextmanager
def stubbed_request(env):
    """Same pattern as test_save_reports_truthfully.py /
    test_sentinel_write_guard.py — exercise the controller method
    directly against a stubbed `request` rather than a real HTTP round
    trip."""
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env
    mock.session = {}
    mock.params = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "width_price_sku_integrity")
class TestWidthPriceAndSkuIntegrity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.partner = cls.env["res.partner"].create({
            "name": "Width/SKU Integrity Test Customer",
        })

    # ------------------------------------------------------------------
    # Shared fixture helpers (mirror test_bom_gate_coherence.py's shape).
    # ------------------------------------------------------------------
    def _design(self):
        return self.Design.create({
            "name": "Width/SKU integrity design",
            "partner_id": self.partner.id,
            "room_width_in": 120.0, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })

    def _line(self, design, product, **extra):
        vals = {
            "design_id": design.id, "product_id": product.id,
            "quantity": 1, "price_unit": product.list_price or 100.0,
            "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
            "layout_key": "wsi-%d" % product.id,
        }
        vals.update(extra)
        return self.Line.create(vals)

    def _nominal_width_product(self, code, width_in=24.0, list_price=300.0):
        """A cabinet product with NO Width attribute at all — placed_
        width_in() falls back to the template's own southbrook_width_in
        scalar (the simplest fixture shape; see
        test_placed_width_in.py::test_no_width_ptav_falls_back_to_
        template_width for the same pattern)."""
        tmpl = self.env["product.template"].create({
            "name": "WSI Cabinet %s" % code,
            "type": "consu", "sale_ok": True, "list_price": list_price,
            "default_code": code,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": width_in,
            "southbrook_height_in": 34.5,
            "southbrook_depth_in": 24.0,
        })
        return tmpl.product_variant_id

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _save(self, design, items):
        controller = self._controller()
        with stubbed_request(self.env):
            return controller.save_design(
                name=design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=items,
                partner_id=self.partner.id,
                design_id=design.id,
            )

    def _saved_line(self, design, layout_key):
        return self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", design.id),
            ("layout_key", "=", layout_key),
        ])

    def _baseline_price(self, product, layout_key):
        """Save `product` at exactly its own nominal width (24.0 for
        every fixture built by `_nominal_width_product`) through the
        real save_design path and return the resulting price_unit --
        the plain channel price the ratio tests measure against, so
        each test stays agnostic to whatever pricelist/channel
        mechanics `_channel_price` resolves to for this partner."""
        design = self._design()
        result = self._save(design, [{
            "product_id":    product.id,
            "layout_key":    layout_key,
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      24.0,
        }])
        self.assertNotIn("error", result, result)
        price = self._saved_line(design, layout_key).price_unit
        self.assertTrue(price, "fixture must resolve a non-zero price")
        return price

    # ------------------------------------------------------------------
    # is_custom_width defaults False.
    # ------------------------------------------------------------------
    def test_is_custom_width_defaults_false(self):
        product = self._nominal_width_product("WSI-DEFAULT-1")
        design = self._design()
        line = self._line(design, product)
        self.assertFalse(
            line.is_custom_width,
            "is_custom_width must default False on a plain line create",
        )

    # ------------------------------------------------------------------
    # Width dialed AWAY from nominal via the real save_design path ->
    # is_custom_width flips True AND price_unit is the actual ratio
    # arithmetic (round(base * dialed/nominal, 2)), not just "some
    # different number".
    # ------------------------------------------------------------------
    def test_save_design_prorates_price_by_measured_width_ratio(self):
        product = self._nominal_width_product(
            "WSI-PRICE-1", width_in=24.0, list_price=300.0)

        design_baseline = self._design()
        result_baseline = self._save(design_baseline, [{
            "product_id":    product.id,
            "layout_key":    "wsi-price-baseline",
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      24.0,  # exactly nominal
        }])
        self.assertNotIn("error", result_baseline, result_baseline)
        baseline_line = self._saved_line(
            design_baseline, "wsi-price-baseline")
        self.assertTrue(baseline_line)
        self.assertFalse(baseline_line.is_custom_width)
        baseline_price = baseline_line.price_unit
        self.assertTrue(baseline_price, "fixture must resolve a non-zero price")

        design_custom = self._design()
        dialed_width = 36.0  # 1.5x nominal
        result_custom = self._save(design_custom, [{
            "product_id":      product.id,
            "layout_key":      "wsi-price-custom",
            "x_position_in":   0,
            "z_position_in":   0,
            "width_in":        dialed_width,
            # The real width-edit handler (kitchen_configurator.js
            # `_updateSelectedWidth`, per is_custom_width's own docstring
            # in kitchen_design.py) sets this whenever the dialed value
            # diverges from the product's placed width -- that client
            # signal is what actually lands in the PERSISTED field (see
            # the module docstring's item 1: save_design writes
            # is_custom_width verbatim from the payload, it does not
            # re-derive the STORED flag from geometry the way it
            # re-derives the PRICE). Omitted entirely in the companion
            # test below to prove the price doesn't need it.
            "is_custom_width": True,
        }])
        self.assertNotIn("error", result_custom, result_custom)
        custom_line = self._saved_line(design_custom, "wsi-price-custom")
        self.assertTrue(custom_line)
        self.assertEqual(custom_line.width_in, dialed_width)
        self.assertTrue(
            custom_line.is_custom_width,
            "a client-acknowledged custom width must persist "
            "is_custom_width=True on the line",
        )

        # The exact ratio arithmetic, not merely "price changed".
        expected_ratio = dialed_width / 24.0  # == 1.5
        self.assertAlmostEqual(
            custom_line.price_unit,
            round(baseline_price * expected_ratio, 2),
            places=2,
            msg="a custom width must pro-rate price_unit by exactly "
                "(dialed width / product's nominal width): baseline=%r "
                "dialed=%.2f nominal=24.0 expected=%r got=%r" % (
                    baseline_price, dialed_width,
                    round(baseline_price * expected_ratio, 2),
                    custom_line.price_unit),
        )

    # ------------------------------------------------------------------
    # The PRICE half of C3 is server-authoritative regardless of the
    # client's is_custom_width claim -- proved here by OMITTING the flag
    # entirely at a genuinely non-nominal width. `_is_custom_width`
    # (the local variable save_design actually prices from) is computed
    # purely from `abs(dialed - nominal) > 0.01`; it never reads
    # `item.get("is_custom_width")`. This is the "unconditional" half of
    # item 2 in the module docstring -- the money bug stays closed even
    # if the client-side width-edit handler has a bug of its own and
    # never sets the flag.
    # ------------------------------------------------------------------
    def test_save_design_prorates_price_unconditionally_even_when_client_omits_the_flag(self):
        product = self._nominal_width_product(
            "WSI-PRICE-UNCONDITIONAL-1", width_in=24.0, list_price=300.0)

        design_baseline = self._design()
        result_baseline = self._save(design_baseline, [{
            "product_id":    product.id,
            "layout_key":    "wsi-price-uncond-baseline",
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      24.0,
        }])
        self.assertNotIn("error", result_baseline, result_baseline)
        baseline_price = self._saved_line(
            design_baseline, "wsi-price-uncond-baseline").price_unit
        self.assertTrue(baseline_price, "fixture must resolve a non-zero price")

        design_custom = self._design()
        dialed_width = 36.0  # 1.5x nominal, genuinely non-nominal
        result_custom = self._save(design_custom, [{
            "product_id":    product.id,
            "layout_key":    "wsi-price-uncond-custom",
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      dialed_width,
            # is_custom_width DELIBERATELY OMITTED -- the client never
            # claims this width is custom at all.
        }])
        self.assertNotIn("error", result_custom, result_custom)
        custom_line = self._saved_line(design_custom, "wsi-price-uncond-custom")
        self.assertEqual(custom_line.width_in, dialed_width)

        expected_ratio = dialed_width / 24.0  # == 1.5
        self.assertAlmostEqual(
            custom_line.price_unit,
            round(baseline_price * expected_ratio, 2),
            places=2,
            msg="price_unit must pro-rate by the measured width delta "
                "even when the client's payload never claims "
                "is_custom_width -- the price is server-authoritative "
                "on geometry alone: expected %r got %r" % (
                    round(baseline_price * expected_ratio, 2),
                    custom_line.price_unit),
        )

    # ------------------------------------------------------------------
    # SECURITY — the price-manipulation vector this whole design closes.
    # Posting is_custom_width=True at the product's OWN nominal width
    # must buy the caller nothing: the server derives "is this custom"
    # purely from the measured width delta, never from the client's
    # claim, so a false claim at nominal width prices EXACTLY like the
    # plain, unmodified line.
    # ------------------------------------------------------------------
    def test_client_cannot_manipulate_price_by_lying_about_custom_width_at_nominal_width(self):
        product = self._nominal_width_product(
            "WSI-MANIPULATION-1", width_in=24.0, list_price=300.0)

        design_honest = self._design()
        result_honest = self._save(design_honest, [{
            "product_id":      product.id,
            "layout_key":      "wsi-manip-honest",
            "x_position_in":   0,
            "z_position_in":   0,
            "width_in":        24.0,   # exactly nominal
            "is_custom_width": False,  # no claim at all
        }])
        self.assertNotIn("error", result_honest, result_honest)
        honest_line = self._saved_line(design_honest, "wsi-manip-honest")
        self.assertTrue(honest_line)
        honest_price = honest_line.price_unit
        self.assertTrue(honest_price, "fixture must resolve a non-zero price")

        design_attack = self._design()
        result_attack = self._save(design_attack, [{
            "product_id":      product.id,
            "layout_key":      "wsi-manip-attack",
            "x_position_in":   0,
            "z_position_in":   0,
            "width_in":        24.0,  # STILL exactly nominal
            "is_custom_width": True,  # the lie: claims a custom width
        }])
        self.assertNotIn("error", result_attack, result_attack)
        attack_line = self._saved_line(design_attack, "wsi-manip-attack")
        self.assertTrue(attack_line)

        # The manipulation must fail: no pro-rata, the exact plain
        # channel price a truthful client would have gotten.
        self.assertEqual(
            attack_line.price_unit, honest_price,
            "a client claiming is_custom_width=True at the product's own "
            "nominal width must be priced EXACTLY like an honest client "
            "at that same width -- any deviation is the price-"
            "manipulation vector this design closes",
        )
        # NOT asserted here, deliberately: `attack_line.is_custom_width`
        # itself. Verified against a live Odoo 19 instance, the PERSISTED
        # field still mirrors the client's raw claim (True, in this
        # attack) rather than being re-derived from the (here, zero)
        # measured delta -- only the money-critical PRICE path is
        # geometry-driven. That is a real asymmetry in the current
        # implementation, called out in the module docstring's item 1;
        # this test's job is the money guarantee above, which holds
        # regardless of that asymmetry.

    # ------------------------------------------------------------------
    # A dialed width BELOW nominal pro-rates DOWN -- checked by
    # direction as well as by ratio, so the arithmetic can't silently
    # invert (e.g. divide instead of multiply) and still pass.
    # ------------------------------------------------------------------
    def test_save_design_prorates_price_down_for_narrower_than_nominal_width(self):
        product = self._nominal_width_product(
            "WSI-NARROW-1", width_in=24.0, list_price=300.0)

        design_baseline = self._design()
        result_baseline = self._save(design_baseline, [{
            "product_id":    product.id,
            "layout_key":    "wsi-narrow-baseline",
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      24.0,
        }])
        self.assertNotIn("error", result_baseline, result_baseline)
        baseline_line = self._saved_line(
            design_baseline, "wsi-narrow-baseline")
        baseline_price = baseline_line.price_unit
        self.assertTrue(baseline_price, "fixture must resolve a non-zero price")

        design_narrow = self._design()
        dialed_width = 12.0  # half of nominal -- measurably NARROWER
        result_narrow = self._save(design_narrow, [{
            "product_id":      product.id,
            "layout_key":      "wsi-narrow-custom",
            "x_position_in":   0,
            "z_position_in":   0,
            "width_in":        dialed_width,
            "is_custom_width": True,  # client-acknowledged, as the real widget does
        }])
        self.assertNotIn("error", result_narrow, result_narrow)
        narrow_line = self._saved_line(design_narrow, "wsi-narrow-custom")
        self.assertTrue(narrow_line.is_custom_width)

        # Direction, explicitly: narrower must price LOWER, not higher.
        self.assertLess(
            narrow_line.price_unit, baseline_price,
            "a width dialed BELOW nominal must pro-rate the price DOWN "
            "-- got %r against a baseline of %r" % (
                narrow_line.price_unit, baseline_price),
        )
        # And the exact ratio, so "some smaller number" isn't good enough.
        expected_ratio = dialed_width / 24.0  # == 0.5
        self.assertAlmostEqual(
            narrow_line.price_unit,
            round(baseline_price * expected_ratio, 2),
            places=2,
            msg="narrower-than-nominal must pro-rate by exactly (dialed "
                "width / nominal width): expected %r got %r" % (
                    round(baseline_price * expected_ratio, 2),
                    narrow_line.price_unit),
        )

    # ------------------------------------------------------------------
    # CUSTOM_WIDTH — severity "warning", never blocking; the design is
    # still quotable end-to-end.
    # ------------------------------------------------------------------
    def test_custom_width_is_warning_severity_not_blocking(self):
        product = self._nominal_width_product(
            "WSI-CUSTOM-WARN-1", width_in=24.0)
        design = self._design()
        line = self._line(design, product, width_in=36.0)  # nominal is 24

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("CUSTOM_WIDTH", by_code, issues)
        self.assertEqual(by_code["CUSTOM_WIDTH"]["severity"], "warning")
        self.assertIn(product.display_name, by_code["CUSTOM_WIDTH"]["message"])
        self.assertIn("(line #%d)" % line.id,
                      by_code["CUSTOM_WIDTH"]["message"])

        blocking = [i for i in issues if i["severity"] == "blocking"]
        self.assertFalse(
            [i for i in blocking if i["code"] == "CUSTOM_WIDTH"],
            "CUSTOM_WIDTH must never appear as a blocking issue: %s" % issues,
        )

    def test_custom_width_design_is_quotable(self):
        product = self._nominal_width_product(
            "WSI-CUSTOM-QUOTE-1", width_in=24.0)
        design = self._design()
        self._line(design, product, width_in=36.0)  # non-nominal, warns

        design.action_create_quotation()

        self.assertTrue(
            design.sale_order_id,
            "a design whose only issue is CUSTOM_WIDTH (warning) must "
            "still quote through action_create_quotation -- drag-to-"
            "resize is a first-class, supported feature, not a blocked "
            "state",
        )

    # ------------------------------------------------------------------
    # Exact-width line never trips CUSTOM_WIDTH -- the common,
    # unremarkable case.
    # ------------------------------------------------------------------
    def test_standard_width_never_flagged(self):
        product = self._nominal_width_product("WSI-STANDARD-1", width_in=24.0)
        design = self._design()
        self._line(design, product, width_in=24.0)

        issues = design._check_production_ready()
        codes = {i["code"] for i in issues}
        self.assertNotIn("CUSTOM_WIDTH", codes, issues)

    # ------------------------------------------------------------------
    # Second-wave finding #2 — CUSTOM_WIDTH skips appliance lines
    # outright (their width is SUPPOSED to vary: one placeholder product
    # covers a 30" range, a 36" fridge, a 24" dishwasher, and it's never
    # priced from the channel pricelist at all). Proved by holding the
    # ABSOLUTE delta identical between an appliance line and a cabinet
    # line -- if the appliance line warned too, the exclusion clearly
    # isn't reached; if the cabinet line stayed silent, the check itself
    # is broken, not just its scope.
    # ------------------------------------------------------------------
    def test_custom_width_skips_appliance_lines_but_still_flags_cabinets_at_the_same_delta(self):
        appliance_tmpl = self.env["product.template"].create({
            "name": "WSI Appliance Placeholder",
            "type": "consu", "sale_ok": True, "list_price": 0.0,
            "default_code": "WSI-APPL-PLACEHOLDER-1",
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "appliance",
            "southbrook_width_in": 30.0,
            "southbrook_height_in": 34.5, "southbrook_depth_in": 24.0,
        })
        appliance_product = appliance_tmpl.product_variant_id
        cabinet_product = self._nominal_width_product(
            "WSI-CABINET-SAME-DELTA-1", width_in=24.0)

        design = self._design()
        delta = 30.0  # identical absolute delta for both lines below
        appliance_line = self._line(
            design, appliance_product, cabinet_type="appliance",
            width_in=appliance_tmpl.southbrook_width_in + delta,  # 60.0
            layout_key="wsi-appliance-delta")
        cabinet_line = self._line(
            design, cabinet_product, cabinet_type="base",
            width_in=24.0 + delta,  # 54.0
            layout_key="wsi-cabinet-delta")

        issues = design._check_production_ready()
        custom_width_issues = [i for i in issues if i["code"] == "CUSTOM_WIDTH"]
        self.assertEqual(
            len(custom_width_issues), 1,
            "exactly one CUSTOM_WIDTH issue expected at this identical "
            "delta -- the appliance line must be silent, the cabinet "
            "line must warn: %s" % issues,
        )
        self.assertIn(
            "(line #%d)" % cabinet_line.id, custom_width_issues[0]["message"])
        self.assertNotIn(
            "(line #%d)" % appliance_line.id, custom_width_issues[0]["message"])

    # ------------------------------------------------------------------
    # Second-wave finding #1 — SERVER-SIDE WIDTH BOUND (money-critical).
    # Once width divides into price, an unbounded posted width_in is a
    # direct line to a ~$0 or negative price. save_design now clamps
    # every posted width into [_SBK_MIN_WIDTH_IN, _SBK_MAX_WIDTH_IN]
    # (3.0in-96.0in, referenced from the controller's own constants
    # rather than hardcoded here, so this test tracks the real bound if
    # it's ever retuned) BEFORE it touches geometry or price. Each case
    # below is exactly the exploit this clamp closes: a near-zero width,
    # a negative width, and an absurdly oversized one, all of which must
    # land on a sane, POSITIVE price instead of an exploitable one.
    # ------------------------------------------------------------------
    def test_save_design_clamps_near_zero_width_and_keeps_price_positive(self):
        product = self._nominal_width_product(
            "WSI-CLAMP-TINY-1", width_in=24.0, list_price=300.0)
        baseline_price = self._baseline_price(product, "wsi-clamp-tiny-baseline")

        design = self._design()
        result = self._save(design, [{
            "product_id":    product.id,
            "layout_key":    "wsi-clamp-tiny",
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      0.001,  # the exploit: ~$0 price via a near-zero divisor
        }])
        self.assertNotIn("error", result, result)
        line = self._saved_line(design, "wsi-clamp-tiny")
        self.assertEqual(
            line.width_in, ctrl_main._SBK_MIN_WIDTH_IN,
            "a near-zero posted width must clamp to the floor, not pass "
            "through raw",
        )
        self.assertGreater(
            line.price_unit, 0.0,
            "the exploit this clamp closes is an arbitrary payload "
            "driving the price to ~$0 -- got %r" % line.price_unit,
        )
        expected_price = round(
            baseline_price * (ctrl_main._SBK_MIN_WIDTH_IN / 24.0), 2)
        self.assertAlmostEqual(line.price_unit, expected_price, places=2)

    def test_save_design_clamps_negative_width_and_keeps_price_positive(self):
        product = self._nominal_width_product(
            "WSI-CLAMP-NEGATIVE-1", width_in=24.0, list_price=300.0)
        baseline_price = self._baseline_price(
            product, "wsi-clamp-negative-baseline")

        design = self._design()
        result = self._save(design, [{
            "product_id":    product.id,
            "layout_key":    "wsi-clamp-negative",
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      -24.0,  # the exploit: a NEGATIVE price/credit
        }])
        self.assertNotIn("error", result, result)
        line = self._saved_line(design, "wsi-clamp-negative")
        self.assertEqual(
            line.width_in, ctrl_main._SBK_MIN_WIDTH_IN,
            "a negative posted width must clamp to the floor, not flow "
            "through as a negative divisor",
        )
        self.assertGreater(
            line.price_unit, 0.0,
            "the exploit this clamp closes is a negative payload driving "
            "the price NEGATIVE (a credit) -- got %r" % line.price_unit,
        )
        expected_price = round(
            baseline_price * (ctrl_main._SBK_MIN_WIDTH_IN / 24.0), 2)
        self.assertAlmostEqual(line.price_unit, expected_price, places=2)

    def test_save_design_clamps_oversized_width_to_ceiling(self):
        product = self._nominal_width_product(
            "WSI-CLAMP-HUGE-1", width_in=24.0, list_price=300.0)
        baseline_price = self._baseline_price(product, "wsi-clamp-huge-baseline")

        design = self._design()
        result = self._save(design, [{
            "product_id":    product.id,
            "layout_key":    "wsi-clamp-huge",
            "x_position_in": 0,
            "z_position_in": 0,
            "width_in":      500.0,  # far past any real cabinet or room
        }])
        self.assertNotIn("error", result, result)
        line = self._saved_line(design, "wsi-clamp-huge")
        self.assertEqual(
            line.width_in, ctrl_main._SBK_MAX_WIDTH_IN,
            "an oversized posted width must clamp to the ceiling",
        )
        self.assertGreater(line.price_unit, 0.0)
        expected_price = round(
            baseline_price * (ctrl_main._SBK_MAX_WIDTH_IN / 24.0), 2)
        self.assertAlmostEqual(line.price_unit, expected_price, places=2)

    # ------------------------------------------------------------------
    # H10 — MISSING_SKU is severity "warning", never blocking; the
    # design is still quotable, and the message points at the fix.
    # ------------------------------------------------------------------
    def test_missing_sku_is_warning_severity_not_blocking(self):
        tmpl = self.env["product.template"].create({
            "name": "WSI No-SKU Cabinet",
            "type": "consu", "sale_ok": True, "list_price": 250.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 24.0,
        })
        product = tmpl.product_variant_id
        self.assertFalse(product.default_code, "test precondition: no SKU")

        design = self._design()
        line = self._line(design, product, width_in=24.0)

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("MISSING_SKU", by_code, issues)
        issue = by_code["MISSING_SKU"]
        self.assertEqual(issue["severity"], "warning")
        self.assertIn(product.display_name, issue["message"])
        self.assertIn("(line #%d)" % line.id, issue["message"])
        # Reworded copy: names the actual remedy, doesn't just say "no".
        self.assertIn("can be quoted", issue["message"])
        self.assertIn("Reference", issue["message"])

        blocking = [i for i in issues if i["severity"] == "blocking"]
        self.assertFalse(
            [i for i in blocking if i["code"] == "MISSING_SKU"],
            "MISSING_SKU must never appear as a blocking issue: %s" % issues,
        )

    def test_missing_sku_design_is_quotable(self):
        tmpl = self.env["product.template"].create({
            "name": "WSI No-SKU Cabinet (quotable)",
            "type": "consu", "sale_ok": True, "list_price": 250.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 24.0,
        })
        product = tmpl.product_variant_id
        self.assertFalse(product.default_code, "test precondition: no SKU")

        design = self._design()
        self._line(design, product, width_in=24.0)

        design.action_create_quotation()

        self.assertTrue(
            design.sale_order_id,
            "a design whose only issue is MISSING_SKU (warning) must "
            "still quote through action_create_quotation -- a catalog-"
            "data defect the rep didn't create must not block them",
        )

    def test_sku_present_never_flagged(self):
        product = self._nominal_width_product("WSI-HAS-SKU-1")
        design = self._design()
        self._line(design, product, width_in=24.0)

        issues = design._check_production_ready()
        codes = {i["code"] for i in issues}
        self.assertNotIn("MISSING_SKU", codes, issues)

    # ------------------------------------------------------------------
    # A normal, standard-width, SKU'd single-cabinet design raises
    # neither CUSTOM_WIDTH nor MISSING_SKU -- and, as before, zero
    # blocking issues at all.
    # ------------------------------------------------------------------
    def test_normal_standard_width_design_passes_clean(self):
        product = self._nominal_width_product("WSI-CLEAN-1", width_in=24.0)
        design = self._design()
        self._line(design, product, width_in=24.0)

        issues = design._check_production_ready()
        codes = {i["code"] for i in issues}
        self.assertNotIn("CUSTOM_WIDTH", codes, issues)
        self.assertNotIn("MISSING_SKU", codes, issues)
        blocking = [i for i in issues if i["severity"] == "blocking"]
        self.assertFalse(
            blocking,
            "a normal standard-width, SKU'd, single-cabinet design with "
            "a customer assigned must validate with zero blocking "
            "issues: %s" % issues,
        )

    # ------------------------------------------------------------------
    # Second-wave finding #3 — C6's `_sb_resolve_template_tiebreak`
    # (models/kitchen_template.py) placeholder price floor. Unrelated to
    # C3/H10's money/SKU story except by ship date and coordinator
    # placement -- this exercises `southbrook.kitchen.template`, not
    # `southbrook.kitchen.design`, and arguably belongs alongside
    # test_template_determinism.py's own C6 coverage; it lands here
    # instead per explicit instruction (that file has another owner).
    #
    # This catalog ships 20+ genuine $1.00 hardware placeholders
    # (`_SBK_PLACEHOLDER_PRICE_CEILING`, kitchen_template.py). Before
    # this fix, `_sb_resolve_template_tiebreak`'s own commercial
    # tiebreak rung ("lowest list_price wins, so the same kitchen never
    # silently costs more") would hand a real cabinet's slot to
    # whichever $1.00 stub happened to tie its width -- quoting a real
    # cabinet at a dollar. The fix: exclude candidates priced at or
    # below the ceiling WHENEVER at least one real-priced candidate
    # exists in the same tie; fall through to the stubs only when every
    # candidate is one.
    # ------------------------------------------------------------------
    def test_template_tiebreak_prefers_real_priced_candidate_over_one_dollar_placeholder(self):
        Ktpl = self.env["southbrook.kitchen.template"]
        ktpl = Ktpl.create({
            "name": "WSI Tiebreak Host (placeholder floor)",
            "code": "WSI-TIEBREAK-FLOOR-1",
            "layout_shape": "straight",
        })
        stub = self.env["product.template"].create({
            "name": "WSI $1 Hardware Stub",
            "type": "consu", "list_price": 1.00,
            "default_code": "WSI-STUB-FLOOR-1",
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 88.0,
            "southbrook_height_in": 34.5, "southbrook_depth_in": 24.0,
        })
        real = self.env["product.template"].create({
            "name": "WSI Real Cabinet",
            "type": "consu", "list_price": 420.00,
            "default_code": "WSI-REAL-FLOOR-1",
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 88.0,
            "southbrook_height_in": 34.5, "southbrook_depth_in": 24.0,
        })

        winner = ktpl._sb_resolve_template_tiebreak(stub + real)

        self.assertEqual(
            winner.id, real.id,
            "a $1.00 placeholder must never win a tie against a "
            "real-priced candidate -- a naive 'lowest list_price wins' "
            "tiebreak would quote %s's slot at %s's dollar price: got %s"
            % (real.display_name, stub.display_name, winner.display_name),
        )

    def test_template_tiebreak_all_placeholder_tie_still_resolves_one_deterministically(self):
        Ktpl = self.env["southbrook.kitchen.template"]
        ktpl = Ktpl.create({
            "name": "WSI Tiebreak Host (all-stubs)",
            "code": "WSI-TIEBREAK-FLOOR-2",
            "layout_shape": "straight",
        })
        # Both stubs tie on sequence (both default) AND price (both
        # $1.00) -- nothing "real" exists in this candidate set at all,
        # so the floor above can't apply and the NEXT rung of the
        # documented total order (default_code ascending) must still
        # pick exactly one, deterministically.
        stub_aaa = self.env["product.template"].create({
            "name": "WSI Stub AAA",
            "type": "consu", "list_price": 1.00,
            "default_code": "WSI-STUB-FLOOR-AAA",
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 89.0,
            "southbrook_height_in": 34.5, "southbrook_depth_in": 24.0,
        })
        stub_zzz = self.env["product.template"].create({
            "name": "WSI Stub ZZZ",
            "type": "consu", "list_price": 1.00,
            "default_code": "WSI-STUB-FLOOR-ZZZ",
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 89.0,
            "southbrook_height_in": 34.5, "southbrook_depth_in": 24.0,
        })

        winner = ktpl._sb_resolve_template_tiebreak(stub_aaa + stub_zzz)

        self.assertEqual(len(winner), 1)
        self.assertEqual(
            winner.id, stub_aaa.id,
            "an all-placeholder tie must still resolve to exactly one "
            "record via the documented total order (sequence, price, "
            "default_code, id) -- 'WSI-STUB-FLOOR-AAA' sorts before "
            "'WSI-STUB-FLOOR-ZZZ', so the lower default_code must win: "
            "got %s" % winner.display_name,
        )
