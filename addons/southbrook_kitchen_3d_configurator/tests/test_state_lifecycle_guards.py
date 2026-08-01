# SPDX-License-Identifier: LGPL-3.0-only
"""Task 21, corner-remediation (2026-07-30, SBK-035, REDUCED SCOPE) —
naming + lifecycle guards. The naming half (auto-name regeneration on
resize + migration backfill) shipped separately in a concurrent
workstream (see controllers/main.py's auto_name_str/
is_auto_generated_name and test_design_naming.py) and is untouched
here. Record deletion/pruning stays the owner's call — out of scope.

This file locks the three remaining ACs:

  1. STATE GUARD — a design must never persist in state='configured'
     with zero ACTIVE cabinet lines
     (`SouthbrookKitchenDesign._check_configured_state_has_lines`,
     models/kitchen_design.py).

     Live gap (pre-fix, reproduced by `test_reproduce_*` below against
     git history / the pre-fix diff): controllers/main.py's save_design
     endpoint forced `"state": "configured"` into the FIRST vals dict
     handed to Design.create()/design.write() — BEFORE the item-
     processing loop later in that same function ever runs — so an
     empty save (items=[]) or an all-rejected payload could reach
     state='configured' having created not one single line. Fixed by
     (a) the model constraint, which is the real backstop, and (b)
     moving save_design's own state="configured" write to the END of
     the method, gated on `design.cabinet_line_ids` being non-empty —
     the same convention kitchen_design.py's own state="configured"
     writers already use (`_apply_kitchen_template`'s "if seeded:
     self.state = 'configured'").

     Known, accepted residual gap (documented, not covered by a test
     here — out of this reduced scope): `@api.constrains("state")`
     only re-fires when a write's vals dict contains the literal key
     "state". A write that only removes/archives an already-configured
     design's LAST line (never re-touching `state`) is not caught by
     this guard. Closing that would need a guard on the line model's
     own write/unlink path, which edges into "record pruning" — the
     brief marks that the owner's decision.

  2. ARCHIVE ACTION — the existing `active` field + `action_archive_
     bulk` (already shipped, task #49) is now ALSO bound to the design
     FORM's Actions (⚙) menu, not just the list's bulk-select menu
     (views/kitchen_design_views.xml's `action_archive_designs_server`,
     binding_view_types widened from "list" to "list,form"). No new
     model method — reuses the existing one, per the brief's "ensure
     it's exposed properly ... rather than duplicating."

  3. "EMPTY DESIGNS" FILTER — a search filter (`filter_empty_designs`,
     views/kitchen_design_views.xml) with domain
     `[('cabinet_line_ids', 'not any', [('active', '=', True)])]`.
     Deliberately NOT the naive `[('cabinet_line_ids', '=', False)]`
     shape: Odoo's relational condition_to_sql forces
     `active_test=False` when resolving that specific "is-empty" leaf,
     so it would call a design whose lines are all ARCHIVED
     "non-empty" — wrong against this filter's literal "zero ACTIVE
     lines" contract. `'not any' + [('active', '=', True)]` means
     exactly what it says. The pre-existing "Archived" filter
     (`filter_archived`, active=False on the design itself) is a
     different axis entirely and already shipped (task #49) — just
     confirmed present here, not re-added.
"""
import ast
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)


@contextmanager
def _stubbed_request(env):
    """Same stub-the-controller-global pattern as test_save_load_identity.py
    / test_wall_cab_top_controller.py — exercises save_design as a plain
    Python call against a real env, no HTTP layer needed."""
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
        "southbrook_kitchen_3d_configurator", "corner_remediation",
        "task21_state_lifecycle")
class TestStateGuardConfiguredRequiresLines(TransactionCase):
    """Model-level half: `_check_configured_state_has_lines`."""

    def _design_vals(self, **overrides):
        vals = {
            "name": "T21 state guard",
            "room_width_in": 96.0,
            "room_depth_in": 96.0,
            "room_height_in": 96.0,
        }
        vals.update(overrides)
        return vals

    def _add_line(self, design):
        base = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_b24"
        ).product_variant_id
        return self.env["southbrook.kitchen.design.line"].create({
            "design_id":   design.id,
            "product_id":  base.id,
            "quantity":    1,
            "price_unit":  base.lst_price,
            "cabinet_type": "base",
            "width_in":    24.0,
            "height_in":   34.5,
            "depth_in":    24.0,
            "origin":      "configurator",
        })

    # ── create() ────────────────────────────────────────────────────────
    def test_create_configured_with_zero_lines_is_blocked(self):
        """The direct reproduction: a lineless design created straight
        into state='configured' via the ORM must be rejected."""
        with self.assertRaises(ValidationError):
            self.env["southbrook.kitchen.design"].create(
                self._design_vals(state="configured"))

    def test_create_configured_with_lines_via_o2m_command_is_allowed(self):
        base = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_b24"
        ).product_variant_id
        design = self.env["southbrook.kitchen.design"].create(
            self._design_vals(state="configured", cabinet_line_ids=[(0, 0, {
                "product_id":  base.id,
                "quantity":    1,
                "price_unit":  base.lst_price,
                "cabinet_type": "base",
                "width_in":    24.0,
                "height_in":   34.5,
                "depth_in":    24.0,
                "origin":      "configurator",
            })]))
        self.assertEqual(design.state, "configured")
        self.assertEqual(len(design.cabinet_line_ids), 1)

    # ── write() — the reproduce-first case ─────────────────────────────
    def test_write_configured_with_zero_lines_is_blocked(self):
        """Reproduce-first: before this task's fix, this exact write —
        an ordinary draft design, zero cabinet lines, flipped straight
        to 'configured' — succeeded silently (this is precisely what
        controllers/main.py's save_design used to do on an empty save).
        It must now raise."""
        design = self.env["southbrook.kitchen.design"].create(
            self._design_vals())
        self.assertEqual(design.state, "draft")
        self.assertFalse(design.cabinet_line_ids)
        with self.assertRaises(ValidationError):
            design.write({"state": "configured"})

    def test_write_configured_with_lines_is_allowed(self):
        design = self.env["southbrook.kitchen.design"].create(
            self._design_vals())
        self._add_line(design)
        design.write({"state": "configured"})
        self.assertEqual(design.state, "configured")

    def test_write_of_unrelated_field_while_draft_is_unaffected(self):
        """Sanity mirror of test_room_bounds_constraint.py's own pattern
        — the constraint must not fire on every write, only when
        'state' itself is part of the vals dict."""
        design = self.env["southbrook.kitchen.design"].create(
            self._design_vals())
        design.write({"name": "T21 renamed"})
        self.assertEqual(design.name, "T21 renamed")

    def test_guard_scope_is_narrow_to_configured_only(self):
        """The guard checks `state == 'configured'` literally — it does
        not attempt to police every OTHER state's own line-count
        requirements (e.g. 'quoted'/'ordered' always arrive FROM
        'configured' with lines already present via this codebase's own
        write paths, per the brief's reduced scope). Pin that a bare
        jump straight to 'quoted' with zero lines is NOT rejected by
        THIS constraint (no other guard exists to catch it either —
        that combination is simply outside what SBK-035's reduced scope
        asked for)."""
        design = self.env["southbrook.kitchen.design"].create(
            self._design_vals())
        design.write({"state": "quoted"})
        self.assertEqual(design.state, "quoted")


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation",
        "task21_state_lifecycle")
class TestSaveDesignStateGuardControllerFix(TransactionCase):
    """Controller-level half: save_design no longer forces
    state='configured' before it knows whether the save produced any
    lines."""

    def setUp(self):
        super().setUp()
        self.Design = self.env["southbrook.kitchen.design"]
        self.partner = self.env["res.partner"].create({
            "name": "T21 save-design guard customer",
            "email": "t21.state.guard@southbrook.test",
        })
        # Dedicated fixture product (NOT the product_tmpl_b24 demo
        # xmlid): this test drives save_design's real /products catalog
        # ACL gate (must be sale_ok=True AND southbrook_is_cabinet=True
        # AND readable), and demo data is `noupdate="1"` — a shared CI
        # database can carry an already-drifted copy of that record
        # (verified live: southbrook_is_cabinet=False on the persisted
        # ci_t21 fixture DB). A freshly created template guarantees the
        # flags this test actually needs, same pattern
        # test_save_load_identity.py already uses for the same reason.
        tmpl = self.env["product.template"].create({
            "name": "T21 Save-Design Guard Base Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 180.0,
        })
        tmpl.southbrook_is_cabinet = True
        tmpl.southbrook_cabinet_type = "base"
        self.base = tmpl.product_variant_id
        assert self.base, "expected auto-created variant"

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _room(self):
        return {"width_in": 96, "depth_in": 96, "height_in": 96}

    def test_reproduce_then_block_empty_save_stays_draft(self):
        """Reproduce-first at the controller boundary: before this
        task's fix, save_design ALWAYS forced state='configured' into
        the initial create/write vals — an items=[] save on a brand
        new design would have persisted state='configured' with ZERO
        cabinet lines (the exact bug the model constraint now rejects
        outright). After the fix, an empty save simply leaves a new
        design in its model default state ('draft') instead of racing
        into a state the model itself would refuse."""
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T21 empty save",
                room=self._room(),
                items=[],
                partner_id=self.partner.id,
                design_id=False,
            )
        self.assertNotIn("error", result, f"unexpected error: {result}")
        design = self.Design.browse(result["id"])
        self.assertTrue(design.exists())
        self.assertFalse(design.cabinet_line_ids)
        self.assertEqual(
            design.state, "draft",
            "an empty save_design (items=[]) on a new design must not "
            "force state='configured' with zero lines",
        )

    def test_existing_draft_design_empty_save_stays_draft(self):
        """Same contract on the write() branch (design_id supplied) —
        an existing draft design saved with an empty item set must not
        be force-flipped to 'configured'."""
        design = self.Design.create({
            "name": "T21 existing draft",
            "partner_id": self.partner.id,
            "room_width_in": 96.0, "room_depth_in": 96.0,
            "room_height_in": 96.0,
        })
        self.assertEqual(design.state, "draft")
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name=design.name,
                room=self._room(),
                items=[],
                partner_id=self.partner.id,
                design_id=design.id,
            )
        self.assertNotIn("error", result, f"unexpected error: {result}")
        design.invalidate_recordset()
        self.assertFalse(design.cabinet_line_ids)
        self.assertEqual(design.state, "draft")

    def test_save_design_with_real_items_still_reaches_configured(self):
        """Non-regression: the common, legitimate path (save a design
        that actually has cabinets) must still end in state='configured'
        — unchanged behaviour, just now sequenced AFTER the lines
        exist instead of racing ahead of them."""
        controller = self._controller()
        items = [{
            "product_id":    self.base.id,
            "layout_key":    "t21-base-1",
            "wall":          "back",
            "x_position_in": 0,
            "y_position_in": 0,
            "z_position_in": 0,
            "width_in":      24.0,
            "height_in":     34.5,
            "depth_in":      24.0,
            "price":         self.base.lst_price,
        }]
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T21 real save",
                room=self._room(),
                items=items,
                partner_id=self.partner.id,
                design_id=False,
            )
        self.assertNotIn("error", result, f"unexpected error: {result}")
        design = self.Design.browse(result["id"])
        self.assertEqual(len(design.cabinet_line_ids), 1)
        self.assertEqual(design.state, "configured")


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation",
        "task21_state_lifecycle")
class TestArchiveAction(TransactionCase):
    """Archive action: reuse of the existing action_archive_bulk model
    method (task #49), now also form-bound."""

    def test_action_archive_bulk_works_on_a_single_record(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T21 archive me", "room_width_in": 96.0,
        })
        self.assertTrue(design.active)
        design.action_archive_bulk()
        self.assertFalse(design.active)

    def test_archive_server_action_bound_to_list_and_form(self):
        action = self.env.ref(
            "southbrook_kitchen_3d_configurator.action_archive_designs_server"
        )
        bindings = {v.strip() for v in (action.binding_view_types or "").split(",")}
        self.assertIn(
            "form", bindings,
            "Task 21 — the archive action must be bound to the design "
            "FORM's Actions menu, not just the list's bulk-select menu",
        )
        self.assertIn("list", bindings, "must not have dropped the "
                       "pre-existing list-view bulk-archive binding")
        self.assertEqual(action.code.strip(), "records.action_archive_bulk()",
                          "must reuse the existing model method, not "
                          "duplicate its logic")


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation",
        "task21_state_lifecycle")
class TestEmptyDesignsFilter(TransactionCase):
    """'Empty designs' + pre-existing 'Archived' search filters."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base = cls.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_b24"
        ).product_variant_id
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.Line = cls.env["southbrook.kitchen.design.line"]

    def _search_arch(self):
        view = self.env.ref(
            "southbrook_kitchen_3d_configurator.view_sbk_kitchen_design_search"
        )
        return view.arch_db

    def _filter_domain(self, filter_name):
        root = ET.fromstring(self._search_arch())
        node = root.find(f".//filter[@name='{filter_name}']")
        self.assertIsNotNone(
            node, f"expected a <filter name='{filter_name}'> in the "
            "design search view")
        return ast.literal_eval(node.get("domain"))

    def test_empty_designs_filter_present_in_arch(self):
        domain = self._filter_domain("filter_empty_designs")
        self.assertEqual(
            domain,
            [("cabinet_line_ids", "not any", [("active", "=", True)])],
        )

    def test_archived_filter_already_present(self):
        # Pre-existing (task #49) — confirmed still present, not
        # duplicated by this task.
        domain = self._filter_domain("filter_archived")
        self.assertEqual(domain, [("active", "=", False)])

    def test_filter_domain_matches_zero_active_lines_designs(self):
        """Functional proof, not just XML presence: run the ACTUAL
        shipped domain against three designs — no lines at all, one
        active line, and lines that are all archived — and confirm
        exactly the first and third land in the result."""
        design_never_had_lines = self.Design.create({
            "name": "T21 filter — never had lines", "room_width_in": 96.0})
        design_with_active_line = self.Design.create({
            "name": "T21 filter — has an active line", "room_width_in": 96.0})
        self.Line.create({
            "design_id": design_with_active_line.id,
            "product_id": self.base.id, "quantity": 1,
            "price_unit": self.base.lst_price, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
        })
        design_all_lines_archived = self.Design.create({
            "name": "T21 filter — all lines archived", "room_width_in": 96.0})
        stale_line = self.Line.create({
            "design_id": design_all_lines_archived.id,
            "product_id": self.base.id, "quantity": 1,
            "price_unit": self.base.lst_price, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
        })
        stale_line.active = False

        domain = self._filter_domain("filter_empty_designs")
        candidate_ids = [
            design_never_had_lines.id, design_with_active_line.id,
            design_all_lines_archived.id,
        ]
        matched = self.Design.search(
            [("id", "in", candidate_ids)] + domain)
        self.assertEqual(
            set(matched.ids),
            {design_never_had_lines.id, design_all_lines_archived.id},
            "'Empty designs' must match a design with no lines at all "
            "AND one whose only line is archived, but not one with a "
            "genuinely active line",
        )
