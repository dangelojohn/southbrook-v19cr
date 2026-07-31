# SPDX-License-Identifier: LGPL-3.0-only
"""PR2 — the `wall` field becomes part of the canonical persistence path
of the backend 3D configurator's controller layer.

Covers:
  * save_design persists an incoming item's `wall` onto the design line.
  * a missing `wall` key defaults to "back" (matches the model field's
    own default + the pre-PR2 single-run-along-the-back-wall behaviour).
  * an invalid `wall` value is rejected BEFORE any create/write happens
    (validate-all-items-first — a bad payload never partially writes).
  * load_design_lines emits `wall` in its per-line dict.
  * a NULL wall column (pre-PR2 rows, no migration) reads back as
    "back" for backward compatibility.

Design: exercise the controller methods directly with a stubbed
`request`, same pattern as the sibling `test_save_design_acl.py` /
`southbrook_estimating_website`'s `test_design_3d_add_new_wall.py`.
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
        "southbrook_kitchen_3d_configurator", "wall_persistence")
class TestWallPersistence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.partner = cls.env["res.partner"].create({
            "name": "Kitchen 3D Wall Test Customer",
            "email": "kitchen3d.wall@southbrook.test",
        })
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Wall-persistence test",
            "partner_id": cls.partner.id,
            "room_width_in": 120,
            "room_depth_in": 96,
            "room_height_in": 96,
        })

        # Catalog-conforming cabinet — same recipe as test_save_design_acl.py.
        cls.tmpl = cls.env["product.template"].create({
            "name": "Wall Test Cabinet",
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

    def _save(self, items, design_id=None):
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.save_design(
                name=self.design.name,
                room={"width_in": 120, "depth_in": 96, "height_in": 96},
                items=items,
                partner_id=self.partner.id,
                design_id=self.design.id if design_id is None else design_id,
            )
        lines = self.env["southbrook.kitchen.design.line"].search([
            ("design_id", "=", self.design.id),
            ("origin", "=", "configurator"),
        ])
        return result, lines

    # ------------------------------------------------------------------
    def test_save_persists_wall(self):
        """An item carrying wall='left' must land on the design line."""
        _, lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "wall-test-1",
            "x_position_in": 0,
            "wall":          "left",
        }])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].wall, "left")

    def test_save_missing_wall_defaults_back(self):
        """No `wall` key in the payload -> defaults to 'back'."""
        _, lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "wall-test-2",
            "x_position_in": 0,
        }])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].wall, "back")

    def test_save_invalid_wall_rejected(self):
        """An unrecognised wall value must be rejected with an error AND
        must not partially write — no design lines created."""
        result, lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "wall-test-3",
            "x_position_in": 0,
            "wall":          "banana",
        }])
        self.assertIn("error", result, f"expected an error response: {result}")
        self.assertEqual(
            len(lines), 0,
            "an invalid wall value must not create/write ANY line from "
            "the payload (validate-all-items-first)",
        )

    def test_save_invalid_wall_rejects_whole_batch(self):
        """A payload mixing a valid item with an invalid one must reject
        the WHOLE batch — no partial write, even for the valid item."""
        result, lines = self._save([
            {
                "product_id":    self.product.id,
                "layout_key":    "wall-test-4a",
                "x_position_in": 0,
                "wall":          "right",
            },
            {
                "product_id":    self.product.id,
                "layout_key":    "wall-test-4b",
                "x_position_in": 30,
                "wall":          "banana",
            },
        ])
        self.assertIn("error", result, f"expected an error response: {result}")
        self.assertEqual(
            len(lines), 0,
            "one bad item in the batch must block the entire save, "
            "including the otherwise-valid item",
        )

    def test_load_design_lines_emits_wall(self):
        """load_design_lines must include 'wall' in each line's dict."""
        _, lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "wall-test-5",
            "x_position_in": 0,
            "wall":          "right",
        }])
        self.assertEqual(len(lines), 1)
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.load_design_lines(design_id=self.design.id)
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["lines"][0]["wall"], "right")

    def test_load_design_lines_emits_room_and_design_name(self):
        """PR2.5a (Gap 1) — load_design_lines must additively echo the
        design's room dims + display name alongside `lines`, so the
        client can backfill state on a direct-URL reload (where
        action.params never carries room_width_in/depth_in/height_in or
        design_name — only the 3D configurator's boot params do, and a
        URL restore skips params entirely)."""
        _, lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "wall-test-room-echo",
            "x_position_in": 0,
            "wall":          "back",
        }])
        self.assertEqual(len(lines), 1)
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.load_design_lines(design_id=self.design.id)
        self.assertIn("room", result)
        self.assertEqual(result["room"]["width_in"], self.design.room_width_in)
        self.assertEqual(result["room"]["depth_in"], self.design.room_depth_in)
        self.assertEqual(result["room"]["height_in"], self.design.room_height_in)
        self.assertIn("design_name", result)
        self.assertEqual(result["design_name"], self.design.display_name)
        # `lines` + `wall` still present alongside the new keys.
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["lines"][0]["wall"], "back")

    def test_load_design_lines_not_found_reports_explicit_error_code(self):
        """Task 20 (SBK-034) — a missing design id must come back with
        an explicit NOT_FOUND error_code, not the old bare
        `{"lines": []}` the client used to (mis)read as "a real design
        with nothing saved yet" and silently regenerate over — the
        audited "reload silently resets to a fresh design" bug."""
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.load_design_lines(design_id=999999999)
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["error_code"], "NOT_FOUND")
        self.assertIn("error", result)
        self.assertNotIn("room", result)
        self.assertNotIn("design_name", result)

    def test_load_design_lines_no_access_reports_explicit_error_code(self):
        """Task 20 (SBK-034) — a design the user genuinely cannot read
        (ir.rule/ACL) must come back ACCESS_DENIED, distinct from
        NOT_FOUND, and never the bare success shape."""
        group_user = self.env.ref("base.group_user")
        outsider = self.env["res.users"].create({
            "name":  "Wall-persistence outsider",
            "login": "kitchen3d.outsider@southbrook.test",
            # Odoo 19 renamed groups_id -> group_ids. Internal-user
            # group only — deliberately none of this module's three
            # kitchen groups, so ir.model.access.csv denies read
            # outright (security/ir.model.access.csv only grants
            # southbrook.kitchen.design read to group_kitchen_readonly
            # /designer/manager).
            "group_ids": [(6, 0, [group_user.id])],
        })
        controller = self._controller()
        with stubbed_request(self.env, user=outsider):
            result = controller.load_design_lines(design_id=self.design.id)
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["error_code"], "ACCESS_DENIED")
        self.assertIn("error", result)

    def test_load_design_lines_found_and_readable_has_no_error_code(self):
        """The success path (Task 20) carries no error/error_code key at
        all — only the two short-circuits above ever set one."""
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.load_design_lines(design_id=self.design.id)
        self.assertNotIn("error_code", result)
        self.assertNotIn("error", result)

    def test_null_wall_reads_as_back(self):
        """Pre-PR2 rows with a NULL wall column (no migration) must read
        back as 'back' for backward compatibility."""
        _, lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "wall-test-6",
            "x_position_in": 0,
            "wall":          "left",
        }])
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.env.cr.execute(
            "UPDATE southbrook_kitchen_design_line SET wall = NULL "
            "WHERE id = %s",
            (line.id,),
        )
        line.invalidate_recordset(["wall"])
        self.assertFalse(line.wall)

        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.load_design_lines(design_id=self.design.id)
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["lines"][0]["wall"], "back")

    # ------------------------------------------------------------------
    # PR3.0 — y/z field-semantics migration. A line carrying a nonzero
    # mount-height-shaped value in y_position_in (canonical, post-PR3.0
    # shape: y nonzero, z flush at 0) must persist and reload byte-
    # identical, same as any other placement field — save/load never
    # interprets these values. (This class's fixture product is
    # cabinet_type='base'; the field round-trip asserted here is
    # type-agnostic, matching TestCoordinateContractCompliance's
    # writer-level assertions for the real wall-type case.)
    def test_save_persists_canonical_wall_mount_height_in_y(self):
        _, lines = self._save([{
            "product_id":    self.product.id,
            "layout_key":    "wall-test-y-canonical",
            "x_position_in": 0,
            "y_position_in": 66,
            "z_position_in": 0,
            "wall":          "back",
        }])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].y_position_in, 66)
        self.assertEqual(lines[0].z_position_in, 0)

        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.load_design_lines(design_id=self.design.id)
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["lines"][0]["y_position_in"], 66)
        self.assertEqual(result["lines"][0]["z_position_in"], 0)

    # ------------------------------------------------------------------
    # Task 20 (SBK-034) — /recent_designs, the landing screen's
    # "Continue" list. A plain non-sudo search — ir.rule already scopes
    # it per role exactly like every other read in this controller.
    def test_recent_designs_includes_this_class_design(self):
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.recent_designs()
        self.assertIn(self.design.id, [d["id"] for d in result])
        row = next(d for d in result if d["id"] == self.design.id)
        self.assertEqual(row["name"], self.design.display_name)
        self.assertEqual(row["partner_name"], self.partner.name)
        self.assertEqual(row["room_width_in"], self.design.room_width_in)
        self.assertEqual(row["room_depth_in"], self.design.room_depth_in)
        self.assertIn("write_date", row)

    def test_recent_designs_orders_most_recently_written_first(self):
        newer = self.env["southbrook.kitchen.design"].create({
            "name": "Wall-persistence test (newer)",
            "partner_id": self.partner.id,
            "room_width_in": 96, "room_depth_in": 96, "room_height_in": 96,
        })
        # Force a distinct, strictly-later write_date than any fixture
        # created earlier in the class (write_date has second precision;
        # a bare create() in the same transaction can tie).
        self.env.cr.execute(
            "UPDATE southbrook_kitchen_design SET write_date = "
            "write_date + interval '1 hour' WHERE id = %s", (newer.id,))
        newer.invalidate_recordset(["write_date"])
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.recent_designs()
        ids = [d["id"] for d in result]
        self.assertIn(newer.id, ids)
        self.assertEqual(ids[0], newer.id,
                          "the most recently written design must sort first")

    def test_recent_designs_limit_is_clamped(self):
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.recent_designs(limit=999)
        self.assertLessEqual(len(result), 25, "limit must be clamped to a sane ceiling")

    def test_recent_designs_designer_tier_sees_all_via_implied_readonly(self):
        """security/groups.xml implies group_kitchen_designer ->
        group_kitchen_readonly, and rule_kitchen_design_readonly_all's
        domain is unconditionally `(1,'=',1)` — so a designer (like
        every tier in this module's three-tier taxonomy) ALSO carries
        the "sees every design" read rule; `rule_kitchen_design_
        designer_own`'s `create_uid = user.id` domain only narrows
        WRITE/CREATE/UNLINK for that tier, never READ (ir.rule ORs
        rules across the groups a user belongs to, per operation).
        recent_designs() is read-only, so a designer sees every
        existing design, including ones they didn't create — this
        locks in that real (if easy to mis-assume) behavior rather
        than a stricter own-only scoping this module doesn't actually
        implement for reads."""
        designer_group = self.env.ref(
            "southbrook_kitchen_3d_configurator.group_kitchen_designer")
        designer = self.env["res.users"].create({
            "name":  "Wall-persistence designer-only user",
            "login": "kitchen3d.designeronly@southbrook.test",
            "group_ids": [(6, 0, [designer_group.id])],
        })
        own_design = self.env["southbrook.kitchen.design"].with_user(
            designer).create({
                "name": "Designer-owned design",
                "room_width_in": 96, "room_depth_in": 96, "room_height_in": 96,
            })
        controller = self._controller()
        with stubbed_request(self.env, user=designer):
            result = controller.recent_designs(limit=25)
        ids = [d["id"] for d in result]
        self.assertIn(own_design.id, ids)
        self.assertIn(
            self.design.id, ids,
            "a designer-tier user's implied readonly group grants read "
            "on every design, not just their own",
        )
