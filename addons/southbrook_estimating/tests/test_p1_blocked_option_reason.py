# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 1 sales-rep UX fix (2026-07-31), Task 2 — CLAUDE.md section 5:
a blocked configurator option must explain WHY to the sales rep, and
never to the customer.

`ProductConfigurator._sb_blocked_option_help()` (models/
product_configurator.py) reuses `product.config.session.
values_available()` — the exact rule-engine call the OCA wizard's own
`read()` uses to build a dynamic attribute field's domain — so the
reason can never diverge from what the picklist is actually hiding.
The reason text itself is the triggering `product.config.domain.name`,
an already-authored string from data/config_rules.xml; nothing here
re-implements Rule 2 (box material -> series) as a Python `if` branch.

Scenario used throughout: base_1dr with Series=Contractor picked.
Rule 2 (data/config_rules.xml `domain_series_is_maple_eligible`) makes
Maple unavailable under Contractor — a live example of the exact
"Maple box not available on Contractor series" case CLAUDE.md section
5 names.
"""
from odoo.tests.common import tagged

from .common import SouthbrookTestCase


@tagged("post_install", "-at_install", "southbrook", "rule_enforcement")
class TestBlockedOptionReason(SouthbrookTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmpl = cls.env.ref("southbrook_estimating.base_1dr")
        cls.series_contractor = cls.env.ref(
            "southbrook_estimating.value_series_contractor")
        cls.box_line = cls.env.ref(
            "southbrook_estimating.attr_line_base_1dr_box_material")
        cls.width_line = cls.env.ref(
            "southbrook_estimating.attr_line_base_1dr_width")

    def _wizard_with_series_contractor(self):
        """A product.configurator wizard for base_1dr with Series
        pinned to Contractor on whichever session create_get_session()
        actually bound it to — write through wiz.config_session_id
        rather than assuming which session object create() picked, so
        this cannot drift from the real object the help-method reads.
        """
        wiz = self.env["product.configurator"].create({
            "product_tmpl_id": self.tmpl.id,
        })
        wiz.config_session_id.write({
            "value_ids": [(4, self.series_contractor.id)],
        })
        return wiz

    # ------------------------------------------------------------------
    # Core logic — direct call, mirrors how add_dynamic_fields calls it.
    # ------------------------------------------------------------------
    def test_01_maple_blocked_under_contractor_names_the_reason(self):
        wiz = self._wizard_with_series_contractor()
        help_text = self.env["product.configurator"]._sb_blocked_option_help(
            wiz, self.box_line)
        self.assertTrue(
            help_text,
            "Maple must be reported as blocked once Series=Contractor "
            "is picked (Rule 2)",
        )
        self.assertIn("Maple", help_text)
        # The reason must come from the domain's own authored name —
        # not a fabricated/hardcoded string.
        domain = self.env.ref(
            "southbrook_estimating.domain_series_is_maple_eligible")
        self.assertIn(domain.name, help_text)
        # White Melamine remains selectable under Contractor — must
        # NOT be reported as blocked.
        self.assertNotIn("White Melamine", help_text)

    def test_02_series_that_allows_both_box_materials_reports_nothing(self):
        """Contemporary is eligible for BOTH White Melamine and Maple
        under Rule 2 (data/config_rules.xml grants both via
        domain_series_is_white_melamine_eligible /
        domain_series_is_maple_eligible) — nothing should be reported
        as blocked, matching TestRule2BoxMaterialSeries.
        test_04_contemporary_offers_both_box_materials in
        test_rule_enforcement.py."""
        series_contemporary = self.env.ref(
            "southbrook_estimating.value_series_contemporary")
        wiz = self.env["product.configurator"].create({
            "product_tmpl_id": self.tmpl.id,
        })
        wiz.config_session_id.write({
            "value_ids": [(4, series_contemporary.id)],
        })
        help_text = self.env["product.configurator"]._sb_blocked_option_help(
            wiz, self.box_line)
        self.assertEqual(
            help_text, "",
            "Contemporary allows both box materials — nothing should "
            "be reported as blocked",
        )

    def test_03_unrestricted_attribute_never_reports_a_reason(self):
        """Width carries no config.line restriction on base_1dr at
        all — must always return "" regardless of what's picked."""
        wiz = self._wizard_with_series_contractor()
        help_text = self.env["product.configurator"]._sb_blocked_option_help(
            wiz, self.width_line)
        self.assertEqual(help_text, "")

    # ------------------------------------------------------------------
    # Wiring — the reason must actually reach the rendered wizard arch
    # as the field's `help=` (tooltip), not just exist as dead code.
    # ------------------------------------------------------------------
    def test_04_reason_reaches_rendered_arch_as_field_help(self):
        wiz = self._wizard_with_series_contractor()
        box_attr = self.env.ref("southbrook_estimating.attr_box_material")
        field_name = f"__attribute_{box_attr.id}"

        res = wiz.with_context(wizard_id=wiz.id).get_view(view_type="form")
        arch = res["arch"]
        arch_text = arch.decode() if isinstance(arch, bytes) else arch

        self.assertIn(
            field_name, arch_text,
            "dynamic Box Material field missing from the rendered arch",
        )
        self.assertIn(
            "isn't offered here", arch_text,
            "blocked-option help text did not reach the rendered arch",
        )
        self.assertIn("Maple", arch_text)

    # ------------------------------------------------------------------
    # Rep-only, structurally — the customer path can never reach this.
    # ------------------------------------------------------------------
    def test_05_estimating_addon_has_no_customer_facing_dependency(self):
        """Pins the structural claim in models/product_configurator.py's
        module docstring: southbrook_estimating's product.configurator
        wizard views are reachable ONLY via a backend act_window
        because this addon does not depend on
        website_product_configurator (the addon that would give
        product.configurator a public website route). If a future
        change adds that dependency, this test forces a conscious
        re-review of the rep-only claim rather than a silent regression.
        """
        module = self.env["ir.module.module"].search(
            [("name", "=", "southbrook_estimating")], limit=1)
        self.assertTrue(module, "southbrook_estimating module record not found")
        dep_names = module.dependencies_id.mapped("name")
        self.assertNotIn(
            "website_product_configurator", dep_names,
            "southbrook_estimating must not depend on "
            "website_product_configurator — doing so would give "
            "product.configurator (and this file's rep-only help text) "
            "a public website route",
        )
