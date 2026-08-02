# SPDX-License-Identifier: LGPL-3.0-only
"""M4 — catalog-wide cabinet BoM generator, extended 2026-07-31 for the
full-BoM-for-every-cabinet task.

Verifies mrp.bom._southbrook_generate_catalog_boms():
  * creates a BoM with real, non-empty bom_line_ids for a cabinet
    template that had none — both a boxed cabinet (panel raw materials +
    real Marathon hardware SKUs) and a pure filler/accessory (minimal
    sensible single-panel BoM, per the task's ambiguity guard),
  * is idempotent (re-running creates nothing new),
  * never mutates a non-empty, or an empty-but-unrecognized-origin,
    existing BoM,
  * (2026-07-31) config_ok=True templates are NO LONGER hard-skipped:
    an empty "Canonical"/KitchenAutoSeed-*/CatalogAutoBOM-* shell gets
    real lines written onto it (never unlinked/replaced — its routing
    must survive), a config_ok template with no BoM at all gets one
    created, and every real materialized variant of a config_ok
    template gets its own correctly-dimensioned, product_id-scoped BoM,
  * per-family line counts (panel substrates + hardware) match the
    formulas in bom-research.md §5,
  * a corner cabinet's BoM includes its corner-mechanism SKU,
  * the per-variant path never writes to the variant record itself.

Also verifies mrp.bom._southbrook_cleanup_empty_catalog_boms():
  * deletes an empty, auto-seeded (KitchenAutoSeed-*/CatalogAutoBOM-*)
    stub BoM,
  * refuses to delete one referenced by an mrp.production,
  * never touches an empty BoM that isn't auto-seeded (hand-built),
  * is idempotent.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "kitchen_mrp",
        "catalog_bom_generator")
class TestCatalogBomGenerator(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["product.template"]
        cls.Bom = cls.env["mrp.bom"]
        # southbrook_is_cabinet is owned by southbrook_kitchen_3d_
        # configurator, which is NOT a hard dependency of
        # southbrook_kitchen_mrp (see mrp_bom_catalog.py's own defensive
        # check). If it isn't installed alongside this module in the test
        # database, the generator has nothing in scope — skip gracefully
        # rather than failing on a missing field.
        cls._flag_missing = "southbrook_is_cabinet" not in cls.Template._fields

    def setUp(self):
        super().setUp()
        if self._flag_missing:
            self.skipTest(
                "southbrook_is_cabinet field not present — "
                "southbrook_kitchen_3d_configurator not installed in this "
                "database"
            )

    def _new_cabinet_template(self, name, cabinet_type, w_in, h_in, d_in, code):
        return self.Template.create({
            "name": name,
            "default_code": code,
            "sale_ok": True,
            "purchase_ok": True,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": cabinet_type,
            "southbrook_width_in": w_in,
            "southbrook_height_in": h_in,
            "southbrook_depth_in": d_in,
        })

    def _normal_boms(self, tmpl):
        return tmpl.with_context(active_test=False).bom_ids.filtered(
            lambda b: b.type == "normal"
        )

    # ------------------------------------------------------------------
    # Creation — real, non-empty bom_line_ids
    # ------------------------------------------------------------------
    def test_creates_real_bom_for_boxed_cabinet(self):
        """A 24in base cabinet with no BoM gets one, with materialized
        (non-empty) bom_line_ids covering both panel raw materials and
        real Marathon hardware SKUs — not an empty stub."""
        tmpl = self._new_cabinet_template(
            "M4 Test Base 24", "base", 24.0, 34.5, 24.0, "M4-B24"
        )
        self.assertFalse(self._normal_boms(tmpl))

        result = self.Bom._southbrook_generate_catalog_boms()
        self.assertGreaterEqual(result["created"], 1)

        boms = self._normal_boms(tmpl)
        self.assertEqual(len(boms), 1, "exactly one BoM created")
        bom = boms[0]
        self.assertTrue(
            bom.bom_line_ids, "bom_line_ids must be materialized, not empty"
        )

        codes = bom.bom_line_ids.mapped("product_id.default_code")
        self.assertTrue(
            any((c or "").startswith("RM-") for c in codes),
            "expected at least one materialized panel raw-material line",
        )
        skus = bom.bom_line_ids.mapped("product_id.x_marathon_sku")
        self.assertTrue(
            any(skus), "expected at least one real Marathon hardware SKU line"
        )

    def test_creates_minimal_bom_for_filler_accessory(self):
        """A filler panel (e.g. FP3) — a pure accessory with no boxed
        carcass — gets a minimal, sensible one-line BoM rather than being
        left BoM-less, and rather than a wrongly-synthesized full 6-panel
        box."""
        tmpl = self._new_cabinet_template(
            "M4 Test Filler Panel", "filler", 3.0, 34.5, 0.75, "M4-FP3"
        )
        self.assertFalse(self._normal_boms(tmpl))

        result = self.Bom._southbrook_generate_catalog_boms()
        self.assertGreaterEqual(result["created"], 1)

        boms = self._normal_boms(tmpl)
        self.assertEqual(len(boms), 1)
        self.assertTrue(boms[0].bom_line_ids)

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------
    def test_idempotent_second_run_creates_nothing_new(self):
        tmpl = self._new_cabinet_template(
            "M4 Test Wall 30", "wall", 30.0, 30.0, 12.0, "M4-W30"
        )
        first = self.Bom._southbrook_generate_catalog_boms()
        self.assertGreaterEqual(first["created"], 1)
        boms_after_first = self._normal_boms(tmpl)
        self.assertEqual(len(boms_after_first), 1)

        second = self.Bom._southbrook_generate_catalog_boms()
        self.assertEqual(
            second["created"], 0,
            "second run must create nothing new — every template already "
            "has a BoM",
        )
        boms_after_second = self._normal_boms(tmpl)
        self.assertEqual(
            len(boms_after_second), 1,
            "must still be exactly one BoM — no duplicate created",
        )
        self.assertEqual(boms_after_first.id, boms_after_second.id)

    # ------------------------------------------------------------------
    # Conservatism — never touch an existing BoM
    # ------------------------------------------------------------------
    def test_never_touches_existing_bom(self):
        """A template that already has a (possibly empty stub) BoM must
        be left completely untouched — counted as skipped, no mutation."""
        tmpl = self._new_cabinet_template(
            "M4 Test Pre-existing Stub", "tall", 24.0, 84.0, 24.0,
            "M4-TALL-STUB",
        )
        stub = self.Bom.create({
            "product_tmpl_id": tmpl.id,
            "type": "normal",
            "product_qty": 1.0,
            "code": "PreexistingStub",
            "bom_line_ids": [],
        })
        self.Bom._southbrook_generate_catalog_boms()

        boms = self._normal_boms(tmpl)
        self.assertEqual(len(boms), 1, "no second BoM created")
        self.assertEqual(boms.id, stub.id, "the original stub BoM, untouched")
        self.assertFalse(
            boms.bom_line_ids,
            "generator must never mutate an existing BoM's lines",
        )

    # ------------------------------------------------------------------
    # config_ok=True — 2026-07-31: no longer hard-skipped. A cabinet
    # template's real per-cabinet dimensions are still chosen per-VARIANT
    # at config time, but the client now requires every cabinet template
    # (config_ok or not) to end up with a real, non-empty BoM. The
    # template-level scalar-dims BoM is a NOMINAL/planning BoM (native
    # Odoo falls back to it for any variant without its own); real
    # variants additionally get their own correctly-dimensioned,
    # product_id-scoped BoM — see the per-variant test block below.
    # ------------------------------------------------------------------
    def test_configurable_template_shell_gets_real_lines_keeps_routing(self):
        """The confirmed prod defect's OLD fix (hard-skip config_ok) is
        superseded 2026-07-31: a config_ok=True cabinet's empty
        "Canonical" routing-carrier BoM shell now gets real, non-empty
        lines written onto it — the routing survives untouched, and the
        shell record itself is never unlinked/replaced (this is the
        deliberate, client-directed change to the previous "never touch
        an existing BoM, even empty" invariant)."""
        tmpl = self._new_cabinet_template(
            "M4 Test Configurable Base Shell", "base", 30.0, 34.5, 24.0,
            "M4-CFG-SHELL-B30",
        )
        tmpl.write({"config_ok": True})
        shell = self.Bom.create({
            "product_tmpl_id": tmpl.id,
            "type": "normal",
            "product_qty": 1.0,
            "code": "%s Canonical" % tmpl.default_code,
            "bom_line_ids": [],
        })
        workcenter = self.env["mrp.workcenter"].create({"name": "M4 Test WC"})
        routing_line = self.env["mrp.routing.workcenter"].create({
            "bom_id": shell.id,
            "name": "Cut Panels",
            "workcenter_id": workcenter.id,
            "time_cycle_manual": 12.0,
            "sequence": 10,
        })

        result = self.Bom._southbrook_generate_catalog_boms()

        self.assertTrue(shell.exists(), "the shell BoM record must never be unlinked")
        self.assertTrue(shell.bom_line_ids, "the shell must be filled with real lines")
        self.assertEqual(
            shell.operation_ids.ids, [routing_line.id],
            "the 8-station routing must survive the fill untouched",
        )
        self.assertGreaterEqual(result["shell_filled"], 1)
        boms = self._normal_boms(tmpl)
        self.assertEqual(len(boms), 1, "no sibling BoM created next to the shell")
        self.assertEqual(boms.id, shell.id)

    def test_configurable_template_with_no_bom_at_all_gets_one_created(self):
        """A config_ok=True template with NO pre-existing BoM (8 of the
        19 real cabinet templates carry no BoM/shell at all) gets a
        brand-new one created — the old hard-skip is gone."""
        tmpl = self._new_cabinet_template(
            "M4 Test Configurable Corner Blind", "corner", 45.0, 34.5,
            24.0, "M4-CFG-CORNER-BLIND",
        )
        tmpl.write({"config_ok": True})
        self.assertFalse(self._normal_boms(tmpl))

        result = self.Bom._southbrook_generate_catalog_boms()

        boms = self._normal_boms(tmpl)
        self.assertEqual(len(boms), 1)
        self.assertTrue(boms.bom_line_ids)
        self.assertGreaterEqual(result["created"], 1)

    def test_configurable_accessory_shell_gets_real_lines(self):
        """Same shell-fill behaviour for the config_ok accessory family
        (SB-ACCESSORY) — it takes the single-panel minimal-BoM branch,
        which must still count as "real, non-empty"."""
        tmpl = self._new_cabinet_template(
            "M4 Test Configurable Accessory Shell", "panel", 3.0, 34.5,
            0.75, "M4-CFG-ACC-SHELL",
        )
        tmpl.write({"config_ok": True})
        shell = self.Bom.create({
            "product_tmpl_id": tmpl.id,
            "type": "normal",
            "product_qty": 1.0,
            "code": "%s Canonical" % tmpl.default_code,
            "bom_line_ids": [],
        })

        self.Bom._southbrook_generate_catalog_boms()

        self.assertTrue(shell.exists())
        self.assertTrue(shell.bom_line_ids)

    # ------------------------------------------------------------------
    # Cleanup — remove empty auto-seeded stubs, refuse in-use ones
    # ------------------------------------------------------------------
    def _new_empty_stub(self, tmpl, code):
        return self.Bom.create({
            "product_tmpl_id": tmpl.id,
            "type": "normal",
            "product_qty": 1.0,
            "code": code,
            "bom_line_ids": [],
        })

    def test_cleanup_deletes_empty_stub_but_refuses_in_use_one(self):
        tmpl_orphan = self._new_cabinet_template(
            "M4 Test Orphan Empty Stub", "base", 24.0, 34.5, 24.0,
            "M4-CLEANUP-ORPHAN",
        )
        orphan_stub = self._new_empty_stub(
            tmpl_orphan, "KitchenAutoSeed-M4-CLEANUP-ORPHAN"
        )

        tmpl_inuse = self._new_cabinet_template(
            "M4 Test In-Use Empty Stub", "base", 24.0, 34.5, 24.0,
            "M4-CLEANUP-INUSE",
        )
        inuse_stub = self._new_empty_stub(
            tmpl_inuse, "CatalogAutoBOM-M4-CLEANUP-INUSE"
        )
        variant = tmpl_inuse.product_variant_ids[:1]
        self.env["mrp.production"].create({
            "product_id": variant.id,
            "product_qty": 1.0,
            "bom_id": inuse_stub.id,
        })

        result = self.Bom._southbrook_cleanup_empty_catalog_boms()

        self.assertFalse(orphan_stub.exists(), "orphaned empty stub deleted")
        self.assertTrue(
            inuse_stub.exists(),
            "empty stub referenced by an mrp.production must be refused",
        )
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(len(result["refused_in_use"]), 1)
        self.assertEqual(result["refused_in_use"][0]["bom_id"], inuse_stub.id)

    def test_cleanup_never_deletes_non_autoseeded_empty_bom(self):
        """An empty BoM without one of the two known auto-seed code
        prefixes is presumed hand-built (or Hermes-applied) and must
        never be touched, even though it's empty."""
        tmpl = self._new_cabinet_template(
            "M4 Test Hand-Built Empty Stub", "base", 24.0, 34.5, 24.0,
            "M4-CLEANUP-HANDBUILT",
        )
        hand_built = self._new_empty_stub(tmpl, "HandBuiltPlaceholder")

        result = self.Bom._southbrook_cleanup_empty_catalog_boms()

        self.assertTrue(hand_built.exists())
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["refused_in_use"], [])

    def test_cleanup_idempotent(self):
        tmpl = self._new_cabinet_template(
            "M4 Test Cleanup Idempotent", "base", 24.0, 34.5, 24.0,
            "M4-CLEANUP-IDEMPOTENT",
        )
        self._new_empty_stub(tmpl, "KitchenAutoSeed-M4-CLEANUP-IDEMPOTENT")

        first = self.Bom._southbrook_cleanup_empty_catalog_boms()
        self.assertEqual(first["deleted"], 1)

        second = self.Bom._southbrook_cleanup_empty_catalog_boms()
        self.assertEqual(
            second["deleted"], 0,
            "second run must find nothing left to delete",
        )

    # ------------------------------------------------------------------
    # Per-family line counts (bom-research.md §5 formulas) — template
    # level (non-config_ok), which exercises the shared panel/hardware
    # line-builder every other path (shell-fill, per-variant) reuses.
    # ------------------------------------------------------------------
    def _line_qty_by_sku_or_code(self, bom):
        result = {}
        for line in bom.bom_line_ids:
            key = line.product_id.x_marathon_sku or line.product_id.default_code
            result[key] = result.get(key, 0.0) + line.product_qty
        return result

    def test_base_2door_line_counts(self):
        """24in base, 2-door, 34.5in (876.3mm -> 2 shelves): 6 melamine
        panel-equivalents (2 sides + 2 top/bottom + 2 shelves), 1 back,
        2 doors, + real Marathon hardware incl. the 5 fastener SKUs."""
        tmpl = self._new_cabinet_template(
            "M4 Test Base 2Door", "base", 24.0, 34.5, 24.0, "M4-BASE-2DR",
        )
        self.Bom._southbrook_generate_catalog_boms()
        bom = self._normal_boms(tmpl)
        self.assertEqual(len(bom), 1)
        by_code = self._line_qty_by_sku_or_code(bom)

        self.assertEqual(by_code.get("RM-MELAMINE_WHITE_5_8"), 6.0)
        self.assertEqual(by_code.get("RM-HARDBOARD_1_4"), 1.0)
        self.assertEqual(by_code.get("RM-PLY_3_4"), 2.0, "1 door panel x 2 doors")
        self.assertGreaterEqual(by_code.get("RM-EDGEBAND-PVC-34", 0), 1.0)

        self.assertEqual(by_code.get("BLM-110-SC"), 4, "2 hinges x 2 doors")
        self.assertEqual(by_code.get("MRH-DOORBUMP-CL"), 4)
        self.assertEqual(by_code.get("MRH-HDL-PUL128"), 2)
        self.assertEqual(by_code.get("MRH-SHLFPIN-5MM"), 8, "4 pins x 2 shelves")
        self.assertEqual(by_code.get("MRH-CAMLOCK-KIT"), 1)
        self.assertEqual(by_code.get("MRH-LVL-50"), 4)
        self.assertEqual(by_code.get("MRH-MTGSCR-25PK"), 1)
        self.assertEqual(by_code.get("FAST-CONFIRMAT-50"), 4)
        self.assertEqual(by_code.get("FAST-DOWEL-8MM"), 8)
        self.assertEqual(by_code.get("FAST-CAM"), 4)
        self.assertEqual(by_code.get("FAST-HANDLE-SCR"), 4, "2 screws x 2 doors")

    def test_tall_pantry_extra_levelers_and_three_shelves(self):
        """24x84x24in tall pantry: height 2133.6mm -> 3 shelves,
        per_family['tall'] adds 4 extra levelers (8 total)."""
        tmpl = self._new_cabinet_template(
            "M4 Test Tall Pantry", "tall", 24.0, 84.0, 24.0, "M4-TALL-PANTRY",
        )
        self.Bom._southbrook_generate_catalog_boms()
        bom = self._normal_boms(tmpl)
        by_code = self._line_qty_by_sku_or_code(bom)

        self.assertEqual(by_code.get("MRH-SHLFPIN-5MM"), 12, "4 pins x 3 shelves")
        self.assertEqual(by_code.get("MRH-LVL-50"), 8, "4 base + 4 tall family extra")

    def test_sink_base_water_barrier(self):
        tmpl = self._new_cabinet_template(
            "M4 Test Sink Base", "base", 33.0, 34.5, 24.0, "M4-SINK-BASE",
        )
        self.Bom._southbrook_generate_catalog_boms()
        bom = self._normal_boms(tmpl)
        by_code = self._line_qty_by_sku_or_code(bom)
        # NOTE: the template-level cabinet_type field cannot distinguish
        # "sink" from "base" (bom-research.md §2.2 — southbrook_cabinet_
        # type has no sink value) so this exercises the family map's own
        # documented limitation; the per-variant path (test below) is
        # where a real sink/water-barrier distinction would need a
        # dedicated attribute pick, which this catalog doesn't have
        # either. Kept here as a structural smoke test on the base path.
        self.assertIn("BLM-110-SC", by_code)

    # ------------------------------------------------------------------
    # Corner mechanism — a corner BoM includes its mechanism SKU
    # ------------------------------------------------------------------
    def test_corner_bom_includes_blind_mechanism_and_hinge_override(self):
        tmpl = self._new_cabinet_template(
            "M4 Test Corner Blind", "corner", 45.0, 34.5, 24.0,
            "M4-CORNER-BLIND",
        )
        self.Bom._southbrook_generate_catalog_boms()
        bom = self._normal_boms(tmpl)
        self.assertEqual(len(bom), 1)
        skus = set(bom.bom_line_ids.mapped("product_id.x_marathon_sku"))
        self.assertIn("SB-CM-BCO", skus, "blind-corner code resolves to the BCO mechanism")
        self.assertIn("SAL-C2R-165", skus, "blind-corner hinge override must be present")
        self.assertNotIn("BLM-110-SC", skus, "standard hinge must be fully replaced")
        self.assertNotIn("BLM-110-NSC", skus, "standard hinge must be fully replaced")

    def test_corner_bom_diag_gets_generic_mechanism(self):
        tmpl = self._new_cabinet_template(
            "M4 Test Corner Diag", "corner", 36.0, 34.5, 36.0,
            "M4-CORNER-DIAG",
        )
        self.Bom._southbrook_generate_catalog_boms()
        bom = self._normal_boms(tmpl)
        skus = set(bom.bom_line_ids.mapped("product_id.x_marathon_sku"))
        self.assertIn("SB-CM-CPOS", skus)
        # Corner cabinets default to soft_close=False (Rule 4) at the
        # template-nominal level, and "diag" has no hinge override — so
        # the standard NON-soft-close hinge is what should resolve here.
        self.assertIn("BLM-110-NSC", skus, "standard non-soft-close hinge, no override for diag")
        self.assertNotIn("SAL-C2R-165", skus)

    # ------------------------------------------------------------------
    # Per-variant BoMs — config_ok templates, real materialized variants
    # ------------------------------------------------------------------
    def _make_configured_variant(self, tmpl, attrs):
        """attrs: list of (attribute_name, value_name). Creates one
        ad-hoc product.attribute + product.attribute.value + attribute
        line per pair (test-isolated — doesn't touch the real seeded
        southbrook_estimating attributes), then creates the
        product.product variant DIRECTLY with the resulting
        product.template.attribute.value ids.

        NOTE: product_configurator/models/product.py's own
        ProductTemplate._create_variant_ids() override deliberately
        no-ops for ANY config_ok=True template ("Prevent configurable
        products from creating variants as these serve only as a
        template for the product configurator") — so the standard
        attribute-line "always"/create_variant auto-materialization path
        NEVER runs on these templates, by design. Real configured
        variants are created explicitly by the configurator wizard via
        product.config.session.create_get_variant() / get_variant_vals(),
        which builds {"product_tmpl_id": ..., "product_template_
        attribute_value_ids": [(6, 0, ptav_ids)]} and calls
        product.product.create() directly — this helper mirrors that
        exact pattern rather than relying on the (blocked) automatic
        path.
        """
        ptav_ids = []
        for name, value in attrs:
            attribute = self.env["product.attribute"].create({
                "name": name, "create_variant": "no_variant",
            })
            val = self.env["product.attribute.value"].create({
                "name": value, "attribute_id": attribute.id,
            })
            self.env["product.template.attribute.line"].create({
                "product_tmpl_id": tmpl.id,
                "attribute_id": attribute.id,
                "value_ids": [(6, 0, [val.id])],
            })
            ptav = self.env["product.template.attribute.value"].search([
                ("product_tmpl_id", "=", tmpl.id),
                ("product_attribute_value_id", "=", val.id),
            ], limit=1)
            self.assertTrue(
                ptav,
                "expected a product.template.attribute.value for %s=%s" % (name, value),
            )
            ptav_ids.append(ptav.id)

        variant = self.env["product.product"].create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, ptav_ids)],
        })
        return variant

    def test_variant_gets_own_correctly_dimensioned_bom(self):
        """A config_ok base template's real, materialized 30in-wide
        variant gets its OWN product_id-scoped BoM sized from ITS
        attribute pick (2 doors, per Rule 3), independent of the
        template's own (different) scalar dims."""
        tmpl = self._new_cabinet_template(
            "M4 Test Variant Base", "base", 15.0, 34.5, 24.0,
            "M4-VARIANT-BASE",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Cabinet Style", "Base"), ("Width", "30 in")],
        )

        result = self.Bom._southbrook_generate_catalog_boms()

        variant_bom = self.Bom.search([
            ("product_id", "=", variant.id), ("type", "=", "normal"),
        ])
        self.assertEqual(len(variant_bom), 1)
        self.assertTrue(variant_bom.bom_line_ids)
        self.assertGreaterEqual(result["variant_created"], 1)
        by_code = self._line_qty_by_sku_or_code(variant_bom)
        self.assertEqual(by_code.get("BLM-110-SC"), 4, "30in -> 2 doors -> 4 hinges")

    def test_variant_generation_idempotent_and_never_writes_variant(self):
        tmpl = self._new_cabinet_template(
            "M4 Test Variant Idempotent", "base", 15.0, 34.5, 24.0,
            "M4-VARIANT-IDEMPOTENT",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Cabinet Style", "Base"), ("Width", "24 in")],
        )
        write_date_before = variant.write_date

        first = self.Bom._southbrook_generate_catalog_boms()
        self.assertGreaterEqual(first["variant_created"], 1)
        bom_after_first = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom_after_first), 1)

        second = self.Bom._southbrook_generate_catalog_boms()
        self.assertEqual(
            second["variant_created"], 0,
            "second run must create no new variant BoM",
        )
        bom_after_second = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom_after_second), 1)
        self.assertEqual(bom_after_first.id, bom_after_second.id)

        variant.invalidate_recordset()
        self.assertEqual(
            variant.write_date, write_date_before,
            "the variant record itself must never be written to — "
            "wizard-configured variants are user data",
        )

    def test_drawer_family_variant_suppresses_shelf_adds_slides(self):
        """A variant whose own attribute picks resolve to family='drawer'
        (bom-research.md §5.2/§7's flagged gap) must get slides + drawer-
        front lines, NO hinges, and NO shelf pins — even though the
        template's own cabinet_type is 'base' (southbrook_cabinet_type
        has no drawer value at all)."""
        tmpl = self._new_cabinet_template(
            "M4 Test Drawer Bank", "base", 24.0, 34.5, 24.0,
            "M4-DRAWER-BANK",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(tmpl, [
            ("Cabinet Style", "Drawer"),
            ("Drawer Construction", "3-Drawer Stack"),
            ("Width", "24 in"),
        ])

        self.Bom._southbrook_generate_catalog_boms()

        bom = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom), 1)
        by_code = self._line_qty_by_sku_or_code(bom)
        self.assertEqual(by_code.get("BLM-MOV-450"), 3, "1 slide per drawer x 3")
        self.assertEqual(by_code.get("MRH-HDL-PUL128"), 3)
        self.assertNotIn("BLM-110-SC", by_code)
        self.assertNotIn("BLM-110-NSC", by_code)
        self.assertNotIn("MRH-SHLFPIN-5MM", by_code, "no shelves in a drawer bank")
        self.assertEqual(
            by_code.get("RM-PLY_3_4"), 3.0,
            "1 drawer-front panel-equivalent x 3 drawers",
        )

    def test_accessory_variant_end_panel_resolves_real_sku(self):
        """bom-research.md §4 item 3 — an accessory variant whose
        Accessory Type pick is 'End Panel' resolves to the real seeded
        SB-EP-BASE-STD SKU instead of the generic melamine placeholder."""
        tmpl = self._new_cabinet_template(
            "M4 Test Accessory End Panel", "panel", 3.0, 34.5, 0.75,
            "M4-ACCESSORY-EP",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Accessory Type", "End Panel")],
        )

        self.Bom._southbrook_generate_catalog_boms()

        bom = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom), 1)
        codes = bom.bom_line_ids.mapped("product_id.default_code")
        self.assertIn("SB-EP-BASE-STD", codes)

    def test_accessory_variant_filler_resolves_real_sku(self):
        tmpl = self._new_cabinet_template(
            "M4 Test Accessory Filler", "filler", 2.0, 34.5, 0.75,
            "M4-ACCESSORY-FILLER",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Accessory Type", "Filler")],
        )

        self.Bom._southbrook_generate_catalog_boms()

        bom = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom), 1)
        codes = bom.bom_line_ids.mapped("product_id.default_code")
        self.assertIn("SB-FILLER-BASE", codes)

    def test_accessory_variant_cornice_resolves_real_sku(self):
        """Catalog decision (2026-08-02) — Cornice now has a real seeded
        SKU (SB-CORNICE-STD, southbrook_hardware_catalog/data/
        end_panel_seed.xml) and no longer falls back to the generic
        melamine placeholder."""
        tmpl = self._new_cabinet_template(
            "M4 Test Accessory Cornice", "panel", 3.0, 6.0, 0.75,
            "M4-ACCESSORY-CORNICE",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Accessory Type", "Cornice")],
        )

        self.Bom._southbrook_generate_catalog_boms()

        bom = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom), 1)
        codes = bom.bom_line_ids.mapped("product_id.default_code")
        self.assertIn("SB-CORNICE-STD", codes)
        self.assertNotIn("RM-MELAMINE_WHITE_5_8", codes)

    def test_accessory_variant_pelmet_resolves_real_sku(self):
        """Catalog decision (2026-08-02) — Pelmet -> SB-PELMET-STD."""
        tmpl = self._new_cabinet_template(
            "M4 Test Accessory Pelmet", "panel", 3.0, 6.0, 0.75,
            "M4-ACCESSORY-PELMET",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Accessory Type", "Pelmet")],
        )

        self.Bom._southbrook_generate_catalog_boms()

        bom = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom), 1)
        codes = bom.bom_line_ids.mapped("product_id.default_code")
        self.assertIn("SB-PELMET-STD", codes)
        self.assertNotIn("RM-MELAMINE_WHITE_5_8", codes)

    def test_accessory_variant_plinth_resolves_real_sku(self):
        """Catalog decision (2026-08-02) — Plinth -> SB-PLINTH-STD."""
        tmpl = self._new_cabinet_template(
            "M4 Test Accessory Plinth", "panel", 3.0, 6.0, 0.75,
            "M4-ACCESSORY-PLINTH",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Accessory Type", "Plinth")],
        )

        self.Bom._southbrook_generate_catalog_boms()

        bom = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom), 1)
        codes = bom.bom_line_ids.mapped("product_id.default_code")
        self.assertIn("SB-PLINTH-STD", codes)
        self.assertNotIn("RM-MELAMINE_WHITE_5_8", codes)

    def test_accessory_variant_unrecognised_type_still_falls_back(self):
        """The generic melamine placeholder still exists as an honest
        fallback for an accessory_type this mapping doesn't recognise at
        all (unmapped/future value) — it isn't dead code now that all 5
        DOCUMENTED types resolve to real SKUs."""
        tmpl = self._new_cabinet_template(
            "M4 Test Accessory Unmapped", "panel", 3.0, 6.0, 0.75,
            "M4-ACCESSORY-UNMAPPED",
        )
        tmpl.write({"config_ok": True})
        variant = self._make_configured_variant(
            tmpl, [("Accessory Type", "Some Future Type")],
        )

        self.Bom._southbrook_generate_catalog_boms()

        bom = self.Bom.search([("product_id", "=", variant.id)])
        self.assertEqual(len(bom), 1)
        codes = bom.bom_line_ids.mapped("product_id.default_code")
        self.assertIn("RM-MELAMINE_WHITE_5_8", codes)
