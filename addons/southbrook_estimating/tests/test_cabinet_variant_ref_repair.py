# SPDX-License-Identifier: LGPL-3.0-only
"""Task 23 (SBK-037) — catalogue hygiene: unique, non-empty
product.product.default_code on cabinet-family variants.

See models/corner_sku_repair.py::repair_cabinet_variant_refs for the
ground-truth citation (two SB-BASE-2DR-coded variants on the same
multi-variant cabinet template, and a Drawer Bank width variant with
no code at all — both live-verified on a catalogue clone,
2026-07-30) and .superpowers/sdd/corner-remediation-2026-07-30/
task-23-brief.md for the requirements this covers.

A bare test DB doesn't naturally reproduce either bad state (config_ok
suppresses variant auto-creation — the same trap test_corner_repair.
py's ensure_repaired_corner() and test_corner_family_repair.py work
around), so every test here constructs the known-bad state directly:
two product.product rows on one cabinet template sharing a
default_code (distinguished only by Width), or a cabinet variant with
an empty default_code alongside a resolvable base code.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models import corner_sku_repair
from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    repair_cabinet_variant_refs,
)


@tagged("post_install", "-at_install", "southbrook", "southbrook_estimating",
        "catalog_hygiene")
class TestCabinetVariantRefRepair(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["product.template"]
        cls.Product = cls.env["product.product"]
        cls.PTAV = cls.env["product.template.attribute.value"]
        cls.has_cabinet_fields = (
            "southbrook_is_cabinet" in cls.Template._fields)
        cls.width_attr = cls.env["product.attribute"].search(
            [("name", "=", "Width")], limit=1)
        if not cls.width_attr:
            cls.width_attr = cls.env["product.attribute"].create(
                {"name": "Width"})

    def _width_value(self, label):
        val = self.env["product.attribute.value"].search([
            ("attribute_id", "=", self.width_attr.id),
            ("name", "=", label),
        ], limit=1)
        if not val:
            val = self.env["product.attribute.value"].create({
                "attribute_id": self.width_attr.id, "name": label,
            })
        return val

    def _cabinet_template(self, name, widths):
        """A fresh, cabinet-flagged (when installed), config_ok
        template with a Width attribute line carrying `widths` (e.g.
        ["24 in", "30 in"]) and matching PTAVs. No default_code is set
        at creation — callers control that explicitly (directly via
        SQL where the test needs to pin the exact raw-column state).
        Returns (template, {label: ptav})."""
        vals = {"name": name, "config_ok": True, "type": "consu"}
        if self.has_cabinet_fields:
            vals["southbrook_is_cabinet"] = True
            vals["southbrook_cabinet_type"] = "base"
        tmpl = self.Template.create(vals)
        values = self.env["product.attribute.value"].browse()
        for w in widths:
            values |= self._width_value(w)
        self.env["product.template.attribute.line"].create({
            "product_tmpl_id": tmpl.id,
            "attribute_id": self.width_attr.id,
            "value_ids": [(6, 0, values.ids)],
        })
        # A fresh template with zero attribute lines at creation gets
        # one auto-created "ghost" variant (no attribute combination,
        # no default_code) before the Width line above is added —
        # an Odoo implementation detail, not something this fixture
        # intends to test. Drop it so the template carries exactly the
        # variants each test creates explicitly below (a stray extra
        # empty-code variant would otherwise contaminate the
        # empty/duplicate counts and the sibling-lookup fallback).
        ghost = tmpl.product_variant_ids.filtered(
            lambda v: not v.product_template_attribute_value_ids)
        if ghost:
            ghost.unlink()
        ptavs = self.PTAV.search([
            ("product_tmpl_id", "=", tmpl.id),
            ("attribute_id", "=", self.width_attr.id),
        ])
        return tmpl, {p.name: p for p in ptavs}

    # ── duplicate default_code on cabinet variants ──────────────────

    def test_duplicate_code_split_deterministically_by_width(self):
        """AC: no duplicate variant refs among cabinets. Two variants
        sharing one code get split by their own Width, never randomly."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl, ptav = self._cabinet_template("T23 Base 2-Door", ["24 in", "30 in"])
        v24 = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["24 in"].id])],
            "default_code": "T23-BASE-2DR",
        })
        v30 = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["30 in"].id])],
            "default_code": "T23-BASE-2DR",
        })

        summary = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(summary["duplicates_split"], 2)

        v24.invalidate_recordset(["default_code"])
        v30.invalidate_recordset(["default_code"])
        self.assertEqual(v24.default_code, "T23-BASE-2DR-24")
        self.assertEqual(v30.default_code, "T23-BASE-2DR-30")
        self.assertNotEqual(
            v24.default_code, v30.default_code,
            "the two former duplicates must now carry distinct codes")

    def test_duplicate_repair_is_idempotent(self):
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl, ptav = self._cabinet_template("T23 Wall 2-Door", ["24 in", "30 in"])
        for label in ("24 in", "30 in"):
            self.Product.create({
                "product_tmpl_id": tmpl.id,
                "product_template_attribute_value_ids": [(6, 0, [ptav[label].id])],
                "default_code": "T23-WALL-2DR",
            })

        first = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(first["duplicates_split"], 2)

        # Only the cabinet-repair counters are asserted to zero here —
        # the noncabinet_*_reported counters are a "report every run"
        # signal by design (report-only never fixes anything, so an
        # unrelated pre-existing non-cabinet issue elsewhere in the DB
        # legitimately re-reports on every call); they are NOT part of
        # this repair's own idempotency contract.
        second = repair_cabinet_variant_refs(self.env)
        self.assertEqual(second["duplicates_split"], 0)
        self.assertEqual(second["empty_filled"], 0)
        self.assertEqual(second["skipped_unresolvable"], 0)

    def test_unique_code_disambiguates_on_collision(self):
        """Two variants that happen to share BOTH the duplicate code
        AND the same distinguishing attribute value (so the natural
        suffix would collide too) must still end up with distinct
        codes — the variant-id disambiguator kicks in rather than
        silently overwriting. Odoo itself forbids two variants on one
        template from ever sharing an identical attribute-value
        combination ("Configurable Products cannot have duplicates"),
        so this scenario can't be constructed through real variant
        creation — _unique_code is exercised directly as the pure,
        deterministic function it is instead."""
        self.assertEqual(
            corner_sku_repair._unique_code("BASE-24", {"OTHER-CODE"}, 99),
            "BASE-24",
            "no collision -> candidate is used as-is")
        self.assertEqual(
            corner_sku_repair._unique_code("BASE-24", {"BASE-24"}, 99),
            "BASE-24-99",
            "collision -> the (deterministic, never random) "
            "disambiguator id is appended")
        # Same inputs, called again: identical output (no randomness,
        # no hidden state).
        self.assertEqual(
            corner_sku_repair._unique_code("BASE-24", {"BASE-24"}, 99),
            "BASE-24-99")

    # ── empty default_code on cabinet variants ───────────────────────

    def test_empty_default_code_filled_from_template_base_code(self):
        """AC: no duplicate/empty variant refs among cabinets. Base
        code recovered from the (raw, pre-recompute-drift) template
        column when it's present."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl, ptav = self._cabinet_template("T23 Drawer Bank", ["12 in", "18 in"])
        # Pin the raw column directly — deterministic control over
        # exactly the scenario _cabinet_template_base_code reads,
        # independent of the ORM's own recompute timing for this
        # related field (see corner_sku_repair.py's own trap docstring
        # on _resolve_tmpl / _cabinet_template_base_code).
        self.env.cr.execute(
            "UPDATE product_template SET default_code = %s WHERE id = %s",
            ("T23-DRAWER", tmpl.id))
        empty = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["18 in"].id])],
        })
        self.assertFalse(empty.default_code)

        summary = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(summary["empty_filled"], 1)

        empty.invalidate_recordset(["default_code"])
        self.assertEqual(empty.default_code, "T23-DRAWER-18")

    def test_empty_default_code_filled_from_sibling_when_template_code_blank(self):
        """Fallback path: when the template-level column is also
        blank (the natural state once a template has always had more
        than one variant), a coded sibling variant supplies the base."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl, ptav = self._cabinet_template("T23 Sink Base", ["24 in", "30 in"])
        coded = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["24 in"].id])],
            "default_code": "T23-SINK",
        })
        # Force-flush the coded variant's write, then null the
        # template-level mirror explicitly: the moment a 2nd variant
        # exists it would recompute to blank anyway (product.template.
        # default_code only mirrors a SINGLE variant), so this makes
        # that natural end-state deterministic for the test rather
        # than depending on ORM flush timing.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE product_template SET default_code = NULL WHERE id = %s",
            (tmpl.id,))
        empty = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["30 in"].id])],
        })
        self.assertFalse(empty.default_code)

        summary = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(summary["empty_filled"], 1)

        empty.invalidate_recordset(["default_code"])
        self.assertEqual(empty.default_code, "T23-SINK-30")
        self.assertNotEqual(empty.default_code, coded.default_code)

    def test_empty_repair_is_idempotent(self):
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl, ptav = self._cabinet_template("T23 Tall Pantry", ["24 in"])
        self.env.cr.execute(
            "UPDATE product_template SET default_code = %s WHERE id = %s",
            ("T23-TALL", tmpl.id))
        empty = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["24 in"].id])],
        })

        first = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(first["empty_filled"], 1)
        empty.invalidate_recordset(["default_code"])
        self.assertTrue(empty.default_code)

        # See test_duplicate_repair_is_idempotent for why only the
        # cabinet-repair counters (not noncabinet_*_reported) are
        # asserted to zero here.
        second = repair_cabinet_variant_refs(self.env)
        self.assertEqual(second["duplicates_split"], 0)
        self.assertEqual(second["empty_filled"], 0)
        self.assertEqual(second["skipped_unresolvable"], 0)

    def test_empty_code_with_no_resolvable_base_is_skipped_not_guessed(self):
        """No template-level code and no coded sibling: the repair
        must not invent a base out of thin air — it skips and reports,
        leaving the record for a human."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl, ptav = self._cabinet_template("T23 No Base Code", ["21 in"])
        self.env.cr.execute(
            "UPDATE product_template SET default_code = NULL WHERE id = %s",
            (tmpl.id,))
        empty = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["21 in"].id])],
        })

        summary = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(summary["skipped_unresolvable"], 1)
        empty.invalidate_recordset(["default_code"])
        self.assertFalse(
            empty.default_code,
            "an unresolvable empty ref must be left empty, not guessed")

    # ── template-level convention ─────────────────────────────────

    def test_repair_never_leaves_a_template_level_default_code(self):
        """AC: multi-variant cabinet templates deliberately carry no
        template-level default_code (locked convention) — the repair
        writes only product.product rows, never product.template."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        tmpl, ptav = self._cabinet_template("T23 Base 1-Door probe", ["9 in", "12 in"])
        for label in ("9 in", "12 in"):
            self.Product.create({
                "product_tmpl_id": tmpl.id,
                "product_template_attribute_value_ids": [(6, 0, [ptav[label].id])],
                "default_code": "T23-BASE-1DR-PROBE",
            })

        repair_cabinet_variant_refs(self.env)
        tmpl.invalidate_recordset(["default_code"])
        self.assertFalse(
            tmpl.default_code,
            "a multi-variant cabinet template must never carry a "
            "template-level default_code after this repair")

    # ── corner-family exclusion (code review finding 2) ──────────────

    def test_is_corner_family_code_recognizes_bare_and_hand_twin(self):
        self.assertTrue(corner_sku_repair._is_corner_family_code("SB-CORNER"))
        self.assertTrue(corner_sku_repair._is_corner_family_code("SB-CORNER-R"))
        self.assertTrue(
            corner_sku_repair._is_corner_family_code("SB-WALL-CORNER-R"))
        self.assertTrue(
            corner_sku_repair._is_corner_family_code("SB-CORNER-BLIND"))
        self.assertFalse(
            corner_sku_repair._is_corner_family_code("SB-BASE-2DR"))
        self.assertFalse(
            corner_sku_repair._is_corner_family_code("SB-BASE-2DR-R"),
            "the bare '-R' suffix convention is corner-family-specific, "
            "not a generic pattern to match against any code")
        self.assertFalse(corner_sku_repair._is_corner_family_code(""))
        self.assertFalse(corner_sku_repair._is_corner_family_code(None))

    def test_corner_family_duplicate_code_survives_repair_untouched(self):
        """Code review finding 2 — the corner family's own code
        convention is owned exclusively by T4/T4a's repair (called
        earlier in repair_corner_sku, ahead of this generic one). Even
        a synthetic, artificially-forced duplicate on a corner-family
        code must never be rewritten by the generic dedup, regardless
        of call order."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        corner_code = sorted(corner_sku_repair._CORNER_FAMILY_BASE_CODES)[0]
        tmpl, ptav = self._cabinet_template(
            "T23 Corner-Family Dup Probe", ["24 in", "30 in"])
        v1 = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["24 in"].id])],
            "default_code": corner_code,
        })
        v2 = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["30 in"].id])],
            "default_code": corner_code,
        })

        repair_cabinet_variant_refs(self.env)

        v1.invalidate_recordset(["default_code"])
        v2.invalidate_recordset(["default_code"])
        self.assertEqual(
            v1.default_code, corner_code,
            "a corner-family code must never be rewritten by the "
            "generic dedup, even when duplicated")
        self.assertEqual(v2.default_code, corner_code)

    def test_corner_family_empty_code_survives_repair_unfilled(self):
        """Same exclusion for the empty-ref case: a variant on a
        corner-family-coded template must be left for T4/T4a to
        resolve, never filled by the generic empty-ref repair."""
        if not self.has_cabinet_fields:
            self.skipTest("southbrook_kitchen_3d_configurator not installed.")
        corner_code = sorted(corner_sku_repair._CORNER_FAMILY_BASE_CODES)[1]
        tmpl, ptav = self._cabinet_template(
            "T23 Corner-Family Empty Probe", ["24 in"])
        self.env.cr.execute(
            "UPDATE product_template SET default_code = %s WHERE id = %s",
            (corner_code, tmpl.id))
        empty = self.Product.create({
            "product_tmpl_id": tmpl.id,
            "product_template_attribute_value_ids": [(6, 0, [ptav["24 in"].id])],
        })
        self.assertFalse(empty.default_code)

        repair_cabinet_variant_refs(self.env)

        empty.invalidate_recordset(["default_code"])
        self.assertFalse(
            empty.default_code,
            "a variant on a corner-family-coded template must be "
            "left empty by the generic repair — owned by T4/T4a")

    # ── non-cabinet products: report-only ────────────────────────────

    def test_noncabinet_duplicate_is_report_only_never_written(self):
        """AC: report-only (log, no write) for non-cabinet products."""
        tmpl1 = self.Template.create({"name": "T23 Non-cabinet widget", "type": "consu"})
        tmpl2 = self.Template.create({"name": "T23 Non-cabinet widget 2", "type": "consu"})
        if self.has_cabinet_fields:
            (tmpl1 | tmpl2).write({"southbrook_is_cabinet": False})
        v1 = self.Product.create({"product_tmpl_id": tmpl1.id, "default_code": "T23-HW-DUP"})
        v2 = self.Product.create({"product_tmpl_id": tmpl2.id, "default_code": "T23-HW-DUP"})

        summary = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(summary["noncabinet_duplicate_groups_reported"], 1)

        v1.invalidate_recordset(["default_code"])
        v2.invalidate_recordset(["default_code"])
        self.assertEqual(v1.default_code, "T23-HW-DUP")
        self.assertEqual(
            v2.default_code, "T23-HW-DUP",
            "non-cabinet duplicates must be left exactly as found — "
            "report-only, never written")

    def test_noncabinet_empty_code_is_report_only_never_written(self):
        tmpl = self.Template.create({"name": "T23 Non-cabinet widget 3", "type": "consu"})
        if self.has_cabinet_fields:
            tmpl.southbrook_is_cabinet = False
        v = self.Product.create({"product_tmpl_id": tmpl.id})
        self.assertFalse(v.default_code)

        summary = repair_cabinet_variant_refs(self.env)
        self.assertGreaterEqual(summary["noncabinet_empty_reported"], 1)

        v.invalidate_recordset(["default_code"])
        self.assertFalse(
            v.default_code,
            "non-cabinet empty refs must be left exactly as found — "
            "report-only, never written")

    def test_noncabinet_report_only_is_idempotent_in_reporting(self):
        """Report-only counters are stable across repeated calls (the
        repair never 'fixes its way out of' reporting a non-cabinet
        issue, since it never touches it)."""
        tmpl1 = self.Template.create({"name": "T23 Non-cabinet widget 4", "type": "consu"})
        tmpl2 = self.Template.create({"name": "T23 Non-cabinet widget 5", "type": "consu"})
        if self.has_cabinet_fields:
            (tmpl1 | tmpl2).write({"southbrook_is_cabinet": False})
        self.Product.create({"product_tmpl_id": tmpl1.id, "default_code": "T23-HW-DUP2"})
        self.Product.create({"product_tmpl_id": tmpl2.id, "default_code": "T23-HW-DUP2"})

        first = repair_cabinet_variant_refs(self.env)
        second = repair_cabinet_variant_refs(self.env)
        self.assertEqual(
            first["noncabinet_duplicate_groups_reported"],
            second["noncabinet_duplicate_groups_reported"],
            "report-only counters must be stable across repeated runs "
            "since nothing is ever written for non-cabinets")
