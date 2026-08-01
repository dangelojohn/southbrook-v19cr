# SPDX-License-Identifier: LGPL-3.0-only
"""Task 19 (corner-remediation 2026-07-30, SBK-032 + C9 UX) — validation
surfaced in the configurator.

`_check_production_ready` (kitchen_design.py) already returns a raw
issue list (code/severity/message, with " (line #NN)" refs baked into
`message` since Task 1) — that method is untouched here (every existing
test exercising it directly keeps seeing the exact same shape; see
test_validator_copy.py / test_validator_repair.py / test_wall_base_run_
coordination.py etc.). This suite covers the two NEW things Task 19
adds on top of it:

  1. `_normalize_production_issues` (model method) — turns the raw list
     into {code, severity, message, line_ids}, deduped by
     (code, line_ids). `action_validate_production` (the record-form
     button) now renders from this too, so its behaviour is pinned
     here as well.
  2. `/southbrook_kitchen/configurator/validate` (controller route) —
     the read-only JSON endpoint the 3D configurator's status chip
     polls: same NOT_FOUND/ACCESS_DENIED shape as the sibling
     /load_design_lines endpoint (test_wall_persistence.py's own
     pattern, reused here), plus each issue's `line_ids` resolved to
     the client's own per-cabinet identity (`layout_keys`) and a
     {blocking, warning} tally.

Design: exercise the controller method directly with a stubbed
`request`, the SAME pattern as test_save_design_acl.py /
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
        "southbrook_kitchen_3d_configurator", "validation_endpoint")
class TestValidationEndpoint(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["product.product"]
        cls.base_prod = P.create(
            {"name": "T19 Base", "default_code": "ZZ-T19-BASE",
             "list_price": 200.0})
        cls.wall_prod = P.create(
            {"name": "T19 Wall", "default_code": "ZZ-T19-WALL",
             "list_price": 150.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "T19 Validation Endpoint Customer"})

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _design(self, with_partner=True, room_width_in=96.0):
        return self.env["southbrook.kitchen.design"].create({
            "name": "T19 design",
            "partner_id": self.partner.id if with_partner else False,
            "room_width_in": room_width_in, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })

    def _line(self, design, product, cabinet_type, width_in, x_position_in,
              layout_key, wall="back", height_in=34.5, depth_in=24.0):
        Line = self.env["southbrook.kitchen.design.line"]
        return Line.create({
            "design_id": design.id, "product_id": product.id,
            "quantity": 1, "price_unit": product.lst_price,
            "cabinet_type": cabinet_type, "wall": wall,
            "width_in": width_in, "height_in": height_in,
            "depth_in": depth_in, "x_position_in": x_position_in,
            "z_position_in": 0.0, "rotation_deg": 0,
            "run_seq": 0, "origin": "configurator",
            "layout_key": layout_key,
        })

    # ── _normalize_production_issues ────────────────────────────────────

    def test_normalize_adds_line_ids_parsed_from_the_message(self):
        """The raw `_check_production_ready` message already embeds
        " (line #NN)" (Task 1); normalization must parse that back out
        into a real int list without touching the message text."""
        design = self._design(with_partner=False)
        line = self._line(design, self.base_prod, "base", 36.0, 0.0, "t19-a")
        issues = design._normalize_production_issues(
            design._check_production_ready())
        missing_customer = next(
            i for i in issues if i["code"] == "MISSING_CUSTOMER")
        self.assertEqual(missing_customer["line_ids"], [])
        # UNRESOLVED_SLOT isn't produced here (line has a real product),
        # but any issue naming `line` must carry its real id.
        for issue in issues:
            if "(line #%d)" % line.id in issue["message"]:
                self.assertIn(line.id, issue["line_ids"])

    def test_normalize_dedupes_identical_code_and_line_ids(self):
        """AC: no duplicate messages. Two raw issues with the SAME code
        and the SAME (possibly empty) line_ids collapse to one."""
        design = self._design(with_partner=False)
        raw = [
            {"code": "MISSING_CUSTOMER", "severity": "blocking",
             "message": "This design needs a customer before quoting."},
            {"code": "MISSING_CUSTOMER", "severity": "blocking",
             "message": "This design needs a customer before quoting."},
        ]
        out = design._normalize_production_issues(raw)
        self.assertEqual(len(out), 1)

    def test_normalize_keeps_same_code_on_different_lines_distinct(self):
        """The SAME code naming DIFFERENT lines is two real, distinct
        issues — must never collapse into one (that would silently
        hide a genuine second problem)."""
        design = self._design(with_partner=False)
        raw = [
            {"code": "NON_STANDARD_WIDTH", "severity": "warning",
             "message": "A is 37\" wide. (line #1)"},
            {"code": "NON_STANDARD_WIDTH", "severity": "warning",
             "message": "B is 41\" wide. (line #2)"},
        ]
        out = design._normalize_production_issues(raw)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["line_ids"], [1])
        self.assertEqual(out[1]["line_ids"], [2])

    def test_normalize_multi_line_issue_collects_every_id(self):
        design = self._design(with_partner=False)
        raw = [{"code": "CABINET_COLLISION", "severity": "blocking",
                "message": "A and B overlap. (line #5) (line #9)"}]
        out = design._normalize_production_issues(raw)
        self.assertEqual(out[0]["line_ids"], [5, 9])

    def test_normalize_passes_message_and_severity_through_unchanged(self):
        design = self._design(with_partner=False)
        raw = [{"code": "EMPTY_DESIGN", "severity": "blocking",
                "message": "Design has no cabinets. Add at least one "
                           "before quoting."}]
        out = design._normalize_production_issues(raw)
        self.assertEqual(out[0]["message"], raw[0]["message"])
        self.assertEqual(out[0]["severity"], "blocking")
        self.assertEqual(out[0]["code"], "EMPTY_DESIGN")

    def test_normalize_empty_issue_list_returns_empty_list(self):
        design = self._design()
        self.assertEqual(design._normalize_production_issues([]), [])

    # ── action_validate_production renders from the SAME structure ─────

    def test_action_validate_production_uses_normalized_issues(self):
        """AC: 'record form and configurator render the same data'.
        Pin that the form button's toast is built from
        `_normalize_production_issues`, not a second derivation, by
        checking the issue count in the title matches the deduped
        (not raw) count."""
        design = self._design(with_partner=False)
        raw = design._check_production_ready()
        normalized = design._normalize_production_issues(raw)
        result = design.action_validate_production()
        self.assertIn(
            "%d issue(s)" % len(normalized),
            result["params"]["title"],
        )

    def test_action_validate_production_ready_when_no_issues(self):
        design = self._design()   # has a partner
        self._line(design, self.base_prod, "base", 24.0, 0.0, "t19-ready")
        # A BOM so MISSING_BOM/BOM_WILL_AUTOSEED can't fire — this
        # fixture is deliberately clean so the success-toast shape is
        # pinned deterministically.
        self.env["mrp.bom"].create({
            "product_tmpl_id": self.base_prod.product_tmpl_id.id,
            "product_qty": 1.0, "type": "normal",
        })
        self.assertEqual(design._check_production_ready(), [],
                          "sanity: fixture must be genuinely clean")
        result = design.action_validate_production()
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn("No blocking issues", result["params"]["message"])

    # ── /validate endpoint: structure ───────────────────────────────────

    def test_validate_endpoint_structure(self):
        design = self._design(with_partner=False)
        line = self._line(design, self.wall_prod, "wall", 37.0, 0.0, "t19-w1")
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=design.id)
        self.assertIn("issues", result)
        self.assertIn("counts", result)
        self.assertIn("ready", result)
        self.assertNotIn("error", result)
        self.assertNotIn("error_code", result)
        self.assertIn("blocking", result["counts"])
        self.assertIn("warning", result["counts"])
        self.assertEqual(
            result["counts"]["blocking"],
            len([i for i in result["issues"] if i["severity"] == "blocking"]))
        self.assertEqual(
            result["counts"]["warning"],
            len([i for i in result["issues"] if i["severity"] == "warning"]))
        self.assertEqual(result["ready"], result["counts"]["blocking"] == 0)
        # MISSING_CUSTOMER (no partner) must be present and blocking.
        codes = {i["code"]: i for i in result["issues"]}
        self.assertIn("MISSING_CUSTOMER", codes)
        self.assertEqual(codes["MISSING_CUSTOMER"]["severity"], "blocking")
        # The non-standard-width warning names `line` and must resolve
        # its layout_key for the client to select+reveal it.
        self.assertIn("NON_STANDARD_WIDTH", codes)
        self.assertIn(line.id, codes["NON_STANDARD_WIDTH"]["line_ids"])
        self.assertIn("t19-w1", codes["NON_STANDARD_WIDTH"]["layout_keys"])

    def test_validate_endpoint_matches_record_form_button(self):
        """AC: 'record form and configurator render the same data' —
        the endpoint's `issues` (code/severity/message/line_ids) must
        be byte-identical to `_normalize_production_issues`' own
        output, modulo the endpoint's additive `layout_keys` key."""
        design = self._design(with_partner=False)
        self._line(design, self.base_prod, "base", 36.0, 0.0, "t19-b1")
        expected = design._normalize_production_issues(
            design._check_production_ready())
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=design.id)
        got = [
            {"code": i["code"], "severity": i["severity"],
             "message": i["message"], "line_ids": i["line_ids"]}
            for i in result["issues"]
        ]
        self.assertEqual(got, expected)

    def test_validate_endpoint_dedupes_no_duplicate_messages(self):
        """AC: 'no duplicate messages' — every (code, message) pair in
        the endpoint's response is unique."""
        design = self._design(with_partner=False)
        self._line(design, self.base_prod, "base", 24.0, 0.0, "t19-d1")
        self._line(design, self.wall_prod, "wall", 24.0, 0.0, "t19-d2")
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=design.id)
        seen = set()
        for issue in result["issues"]:
            key = (issue["code"], issue["message"])
            self.assertNotIn(key, seen, "duplicate issue: %r" % (key,))
            seen.add(key)

    def test_validate_endpoint_no_raw_coordinate_or_sentinel_in_copy(self):
        """AC: 'no raw coordinate/sentinel in user copy'. Task 15
        already made `_check_production_ready`'s messages copy-clean
        (test_validator_copy.py owns that regression pin in depth);
        this just confirms normalization/the endpoint never reintroduce
        anything the brief calls out, since /validate is the actual
        surface the chip renders from."""
        design = self._design(with_partner=False)
        self._line(design, self.base_prod, "base", 200.0, 0.0, "t19-huge")
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=design.id)
        forbidden = ("run_seq", "layout_role", "template_slot_code",
                     "base_run", "layout_key", "rot=", "x=", "z=")
        for issue in result["issues"]:
            for token in forbidden:
                self.assertNotIn(
                    token, issue["message"],
                    "issue %s leaked internal token %r: %s"
                    % (issue["code"], token, issue["message"]))

    def test_validate_endpoint_issue_with_no_layout_key_line_omits_it(self):
        """A design-level issue with no line at all (MISSING_CUSTOMER)
        carries empty line_ids/layout_keys, never a KeyError. Needs at
        least one cabinet line — an EMPTY design short-circuits
        `_check_production_ready` at check #1 before MISSING_CUSTOMER
        (check #2) ever runs."""
        design = self._design(with_partner=False)
        self._line(design, self.base_prod, "base", 24.0, 0.0, "t19-mc")
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=design.id)
        missing_customer = next(
            i for i in result["issues"] if i["code"] == "MISSING_CUSTOMER")
        self.assertEqual(missing_customer["line_ids"], [])
        self.assertEqual(missing_customer["layout_keys"], [])

    def test_validate_endpoint_ready_true_and_empty_issues_when_clean(self):
        design = self._design()   # has a partner
        line = self._line(design, self.base_prod, "base", 24.0, 0.0, "t19-clean")
        # Seed a BOM so MISSING_BOM/BOM_WILL_AUTOSEED can't fire and
        # muddy the "clean" assertion.
        self.env["mrp.bom"].create({
            "product_tmpl_id": self.base_prod.product_tmpl_id.id,
            "product_qty": 1.0, "type": "normal",
        })
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=design.id)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["counts"], {"blocking": 0, "warning": 0})
        self.assertTrue(result["ready"])
        self.assertTrue(line)   # line created; keeps the design non-empty

    # ── /validate endpoint: auth (NOT_FOUND / ACCESS_DENIED) ────────────

    def test_validate_endpoint_not_found(self):
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=999999999)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["error_code"], "NOT_FOUND")
        self.assertIn("error", result)
        self.assertNotIn("counts", result)
        self.assertNotIn("ready", result)

    def test_validate_endpoint_access_denied(self):
        group_user = self.env.ref("base.group_user")
        outsider = self.env["res.users"].create({
            "name":  "Validation-endpoint outsider",
            "login": "kitchen3d.validate.outsider@southbrook.test",
            # Odoo 19 renamed groups_id -> group_ids. Internal-user
            # group only — none of this module's three kitchen groups,
            # so ir.model.access.csv denies read outright, same
            # fixture shape as test_wall_persistence.py's own
            # ACCESS_DENIED regression pin.
            "group_ids": [(6, 0, [group_user.id])],
        })
        design = self._design()
        controller = self._controller()
        with stubbed_request(self.env, user=outsider):
            result = controller.validate_production(design_id=design.id)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["error_code"], "ACCESS_DENIED")
        self.assertIn("error", result)

    def test_validate_endpoint_found_and_readable_has_no_error_code(self):
        design = self._design()
        controller = self._controller()
        with stubbed_request(self.env):
            result = controller.validate_production(design_id=design.id)
        self.assertNotIn("error_code", result)
        self.assertNotIn("error", result)

    # ── /validate endpoint: no side effects (read-only) ─────────────────

    def test_validate_endpoint_never_writes_to_the_design_or_its_lines(self):
        """A validation check must be read-only by construction —
        `_check_production_ready` never mutates `self`, and the
        endpoint only reads off `design`/`design.cabinet_line_ids`."""
        design = self._design(with_partner=False)
        line = self._line(design, self.base_prod, "base", 24.0, 0.0, "t19-ro")
        before_design = design.read(
            ["write_date", "partner_id", "room_width_in"])[0]
        before_line = line.read(
            ["write_date", "x_position_in", "width_in", "layout_key"])[0]
        line_count_before = self.env["southbrook.kitchen.design.line"].search_count(
            [("design_id", "=", design.id)])
        controller = self._controller()
        with stubbed_request(self.env):
            controller.validate_production(design_id=design.id)
            # Call it twice — a side-effecting endpoint would show drift
            # (e.g. an autoseeded BOM, a re-sequenced line) on the
            # second pass; a pure read never does.
            controller.validate_production(design_id=design.id)
        after_design = design.read(
            ["write_date", "partner_id", "room_width_in"])[0]
        after_line = line.read(
            ["write_date", "x_position_in", "width_in", "layout_key"])[0]
        line_count_after = self.env["southbrook.kitchen.design.line"].search_count(
            [("design_id", "=", design.id)])
        self.assertEqual(before_design, after_design)
        self.assertEqual(before_line, after_line)
        self.assertEqual(line_count_before, line_count_after)

    def test_validate_endpoint_never_creates_a_bom(self):
        """Check #4's own comment (kitchen_design.py) is explicit that
        a missing-BOM WARNING is deliberately never autoseeded by the
        validator itself (only the quote flow does that, as a
        documented side effect of quoting) — pin that /validate
        genuinely never does either."""
        design = self._design()
        self._line(design, self.base_prod, "base", 24.0, 0.0, "t19-nobom")
        boms_before = self.env["mrp.bom"].search_count(
            [("product_tmpl_id", "=", self.base_prod.product_tmpl_id.id)])
        controller = self._controller()
        with stubbed_request(self.env):
            controller.validate_production(design_id=design.id)
        boms_after = self.env["mrp.bom"].search_count(
            [("product_tmpl_id", "=", self.base_prod.product_tmpl_id.id)])
        self.assertEqual(boms_before, boms_after)
