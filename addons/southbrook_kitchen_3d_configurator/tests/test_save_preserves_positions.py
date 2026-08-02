# SPDX-License-Identifier: LGPL-3.0-only
"""P0 fix (drag-revert / undo-corruption, 2026-08-02) — `save_design`
must not silently re-run the corner-resolution auto-arrange over a
design a human has manually positioned.

Before this fix, `save_design` unconditionally called
`design.action_auto_arrange(sync=False)` the instant a design had
cabinets on 2+ walls (`walls_used >= 2`), on EVERY save — including the
client's 3s debounced autosave. `action_auto_arrange` is a
RESET-TO-CANONICAL re-derive (it repositions every surviving canonical
line from a fresh layout-engine run) and never consulted the `pinned`
field, so:

  (a) drag-then-revert — a rep drags a cabinet; `save_position`
      persists the new pose and flips `pinned` True; the trailing
      autosave then re-derives and silently reverts it.
  (b) undo-destroys-itself — an undo restores a correct snapshot, then
      its own trailing autosave re-triggers auto-arrange, which
      overwrites the just-restored state again (cabinet_type/price
      included, for any line a corner absorbs).

Fix: `save_design` now only calls `action_auto_arrange` when nothing in
the design's arrangeable lines is `pinned` (or the caller explicitly
opts in via the new `allow_relayout=True` kwarg). This file covers the
controller-level contract:

  1. A pinned line's position survives a multi-wall save unchanged.
  2. A multi-wall design with NO pinned lines still auto-arranges —
     no regression to the generated/template flow.

Plus Fix 2 (also P0, `/southbrook_kitchen/configurator/user_defaults`)
— a brand-new user (no stored ir.config_parameter) used to get a
12in x 24in room fallback, too small for a single 24in-deep cabinet
(live evidence: "Walk-in - 12x120" / "Walk-in - 12x42", 0 cabinets).
Fallback is now 144in x 120in x 96in; a stored preference still wins.

Design: exercise the controller methods directly with a stubbed
`request`, same pattern as the sibling test_wall_placement_engine.py /
test_wall_persistence.py.
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
        "southbrook_kitchen_3d_configurator", "save_preserves_positions")
class TestSaveDesignPinnedSkipsRelayout(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Save-Preserves-Positions Test Customer",
            "email": "kitchen3d.savepreserve@southbrook.test",
        })
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Save-preserves-positions test",
            "partner_id": cls.partner.id,
            "room_width_in": 120,
            "room_depth_in": 96,
            "room_height_in": 96,
        })
        # Same recipe as test_wall_placement_engine.py — no
        # southbrook_width_in override so the controller's 24.0in
        # default is predictable.
        cls.tmpl = cls.env["product.template"].create({
            "name": "Save-Preserves-Positions Test Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl.southbrook_is_cabinet = True
        cls.tmpl.southbrook_cabinet_type = "base"
        cls.product = cls.tmpl.product_variant_id
        assert cls.product, "expected auto-created variant"

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _save(self, items, **kw):
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=items,
                partner_id=self.partner.id,
                design_id=self.design.id,
                **kw,
            )
        lines = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        return result, lines

    # ------------------------------------------------------------------
    def test_pinned_line_position_unchanged_after_multiwall_save(self):
        """A back-wall PINNED line's pose must be exactly what was sent
        — untouched by auto-arrange — once a second cabinet on another
        wall pushes walls_used to 2.

        The pinned line is deliberately placed on the BACK wall (not a
        side wall) so this test isolates Fix 1 (the auto-arrange gate)
        from the separate, pre-existing PR4 `_place_lines_on_wall` pass
        (which always recomputes non-back-wall poses regardless of
        `pinned` — out of this fix's scope, see controllers/main.py's
        `wall_lines` block). Back-wall lines are written verbatim from
        the payload and are ONLY ever subsequently touched by
        action_auto_arrange's own reposition step — so an unchanged
        back-wall pose after this save is proof the arrange never ran.
        """
        pinned_x, pinned_y, pinned_z, pinned_rot = 55.5, 0.0, 13.25, 0.0
        result, lines = self._save([
            {
                "product_id":     self.product.id,
                "layout_key":     "pin-back-1",
                "wall":           "back",
                "x_position_in":  pinned_x,
                "y_position_in":  pinned_y,
                "z_position_in":  pinned_z,
                "rotation_deg":   pinned_rot,
                "pinned":         True,
            },
            {
                "product_id":     self.product.id,
                "layout_key":     "pin-left-1",
                "wall":           "left",
            },
        ])
        self.assertNotIn("error", result, result)
        self.assertEqual(len(lines), 2)

        pinned_line = lines.filtered(lambda l: l.layout_key == "pin-back-1")
        self.assertTrue(pinned_line)
        self.assertTrue(pinned_line.pinned)
        self.assertAlmostEqual(pinned_line.x_position_in, pinned_x, places=2)
        self.assertAlmostEqual(pinned_line.y_position_in, pinned_y, places=2)
        self.assertAlmostEqual(pinned_line.z_position_in, pinned_z, places=2)
        self.assertAlmostEqual(pinned_line.rotation_deg, pinned_rot, places=2)

        # The `relaid` flag must stay truthful: nothing was relaid.
        self.assertFalse(result.get("relaid"))
        self.assertIsNone(result.get("lines"))

        # No corner substitution happened — both canonical lines survive,
        # nothing archived, nothing derived.
        self.assertEqual(
            lines.mapped("layout_role"), ["canonical", "canonical"])
        self.assertTrue(all(lines.mapped("active")))

    def test_pinned_design_allow_relayout_opts_back_in(self):
        """`allow_relayout=True` is the explicit escape hatch: even with
        a pinned line present, the caller can still force the corner
        resolution pass to run."""
        result, lines = self._save([
            {
                "product_id":     self.product.id,
                "layout_key":     "pin-optin-back-1",
                "wall":           "back",
                "x_position_in":  10.0,
                "pinned":         True,
            },
            {
                "product_id":     self.product.id,
                "layout_key":     "pin-optin-left-1",
                "wall":           "left",
            },
        ], allow_relayout=True)
        self.assertNotIn("error", result, result)
        self.assertTrue(result.get("relaid"))

    def test_no_pinned_lines_multiwall_design_still_auto_arranges(self):
        """No regression to the generated/template flow: a multi-wall
        design with NO pinned lines still gets auto-arranged on save,
        exactly as before this fix (mirrors
        test_wall_placement_engine.py's own back+left corner-formation
        contract)."""
        result, lines = self._save([
            {
                "product_id":     self.product.id,
                "layout_key":     "nopin-back-1",
                "wall":           "back",
            },
            {
                "product_id":     self.product.id,
                "layout_key":     "nopin-left-1",
                "wall":           "left",
            },
        ])
        self.assertNotIn("error", result, result)
        self.assertTrue(result.get("relaid"))
        self.assertIsNotNone(result.get("lines"))


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "save_preserves_positions")
class TestUserDefaultsFallback(TransactionCase):
    """Fix 2 — `/southbrook_kitchen/configurator/user_defaults`'s
    no-stored-preference fallback: was 12in x 24in (too small for a
    24in-deep cabinet), now 144in x 120in x 96in. A stored preference
    must still win outright."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref("base.group_user")
        cls.fresh_user = cls.env["res.users"].create({
            "name":      "User-Defaults Fallback Test User",
            "login":     "kitchen3d.userdefaults@southbrook.test",
            # Odoo 19 renamed groups_id -> group_ids.
            "group_ids": [(6, 0, [group_user.id])],
        })

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def test_no_stored_preference_returns_new_fallbacks(self):
        controller = self._controller()
        with stubbed_request(self.env, user=self.fresh_user):
            result = controller.user_defaults()
        self.assertEqual(result["width"], 144.0)
        self.assertEqual(result["depth"], 120.0)
        self.assertEqual(result["height"], 96.0)

    def test_stored_preference_overrides_fallback(self):
        controller = self._controller()
        with stubbed_request(self.env, user=self.fresh_user):
            save_result = controller.user_defaults(
                save=True, width=200, depth=150, height=90)
            read_result = controller.user_defaults()
        # The write call also returns the freshly-read values.
        self.assertEqual(save_result["width"], 200.0)
        self.assertEqual(save_result["depth"], 150.0)
        self.assertEqual(save_result["height"], 90.0)
        # A second, read-only call sees the SAME stored values — not
        # the fallback.
        self.assertEqual(read_result["width"], 200.0)
        self.assertEqual(read_result["depth"], 150.0)
        self.assertEqual(read_result["height"], 90.0)

    def test_partial_stored_preference_falls_back_for_unset_dims(self):
        """Saving only `width` must not disturb depth/height — those
        still resolve through the (new) fallback for this user."""
        controller = self._controller()
        with stubbed_request(self.env, user=self.fresh_user):
            controller.user_defaults(save=True, width=200)
            result = controller.user_defaults()
        self.assertEqual(result["width"], 200.0)
        self.assertEqual(result["depth"], 120.0)
        self.assertEqual(result["height"], 96.0)
