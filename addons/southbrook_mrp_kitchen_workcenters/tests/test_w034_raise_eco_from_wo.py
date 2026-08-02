# SPDX-License-Identifier: LGPL-3.0-only
"""W034 (R4.W5, 2026-06-27) — Report Engineering Issue from a WO.

JTBD: "When I find a problem at the CNC that needs an engineering
change, I want to photo + describe it from the WO form and have a
draft ECO auto-created with the WO context attached."

Coverage:
  * Button on the WO form opens the wizard pre-bound to the WO.
  * Submitting creates a southbrook.eco in a non-applied stage (draft).
  * The wizard NEVER auto-approves: ECO state remains 'open'.
  * The WO chatter receives a back-link note.
  * The ECO description embeds the WO/MO context block.
  * `mi_check_id` linkage is reflected in the ECO description.
  * (2026-08-02 blindspot fix) Submit returns a `display_notification`
    toast naming the ECO instead of an `act_window` onto it, and the
    whole flow works cleanly when driven by a REAL mrp.group_mrp_user
    session — one with zero access rows on southbrook.eco — so the
    AccessError-on-redirect regression can't creep back in unnoticed
    (TransactionCase's default superuser context would mask it).
"""
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_kitchen", "w034")
class TestW034RaiseEcoFromWo(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wizard = cls.env["southbrook.wo.raise.eco.wizard"]
        cls.Eco = cls.env["southbrook.eco"]
        cls.Workorder = cls.env["mrp.workorder"]
        cls.Check = cls.env["southbrook.mi.check"]

    def _make_wo(self):
        wc = self.env["mrp.workcenter"].search([], limit=1)
        if not wc:
            wc = self.env["mrp.workcenter"].create({
                "name": "W034 WC", "code": "W034WC",
            })
        product = self.env["product.product"].search([
            ("type", "=", "consu")], limit=1)
        if not product:
            product = self.env["product.product"].create({
                "name": "W034 product", "type": "consu",
            })
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": product.product_tmpl_id.id,
            "product_qty": 1.0,
        })
        production = self.env["mrp.production"].create({
            "product_id": product.id,
            "product_qty": 1.0,
            "bom_id": bom.id,
        })
        wo = self.Workorder.create({
            "name": "W034 WO",
            "production_id": production.id,
            "workcenter_id": wc.id,
            "product_uom_id": product.uom_id.id,
        })
        return wo

    def _eco_by_title(self, title):
        """Look up the ECO action_submit created. The wizard's return
        value no longer carries res_id (see test_60 below for why), so
        tests locate the record via the title they submitted instead."""
        return self.Eco.search([("title", "=", title)], order="id desc", limit=1)

    # ------------------------------------------------------------------
    def test_10_button_returns_wizard_action(self):
        wo = self._make_wo()
        action = wo.action_sbk_raise_eco_wizard()
        self.assertEqual(action["res_model"], "southbrook.wo.raise.eco.wizard")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_workorder_id"], wo.id)

    def test_20_submit_creates_draft_eco(self):
        wo = self._make_wo()
        wiz = self.Wizard.with_context(
            default_workorder_id=wo.id,
            active_model="mrp.workorder",
            active_id=wo.id,
        ).create({
            "title": "CNC drill hole 4mm off",
            "description": "Hole pattern is shifted left.",
            "severity": "high",
        })
        result = wiz.action_submit()
        # Submit returns a display_notification toast, not an act_window
        # onto the ECO (the operator generally can't read southbrook.eco —
        # see test_60). Locate the created record by title instead.
        self.assertEqual(result["type"], "ir.actions.client")
        self.assertEqual(result["tag"], "display_notification")
        eco = self._eco_by_title("CNC drill hole 4mm off")
        self.assertTrue(eco.exists(), "ECO must exist after submit.")
        # Always draft / open. Spec: never auto-approved.
        self.assertEqual(
            eco.state, "open",
            "Wizard must NEVER auto-approve; ECO must be in 'open' state.")
        self.assertFalse(
            eco.stage_id.is_applied_stage,
            "ECO must not land in an applied stage.")
        # Priority maps from severity=high -> '2' (Urgent).
        self.assertEqual(eco.priority, "2")
        # Description embeds the WO context block.
        self.assertIn(wo.display_name or str(wo.id), eco.description or "")
        self.assertIn("W034", eco.description or "")

    def test_30_default_target_kind_is_document(self):
        """W034 always pins ECO type to 'document' so apply is record-
        only. Engineering re-types to bom/cut_spec only after triage."""
        wo = self._make_wo()
        wiz = self.Wizard.with_context(
            default_workorder_id=wo.id,
        ).create({
            "title": "test default type",
            "description": "x",
            "severity": "low",
        })
        wiz.action_submit()
        eco = self._eco_by_title("test default type")
        self.assertEqual(
            eco.target_kind, "document",
            "W034 must default to a record-only ECO type so apply "
            "doesn't accidentally mutate a BoM or cut-spec.")

    def test_40_wo_chatter_gets_back_link(self):
        wo = self._make_wo()
        existing_msg_count = len(wo.message_ids)
        wiz = self.Wizard.with_context(
            default_workorder_id=wo.id,
        ).create({
            "title": "y",
            "description": "z",
            "severity": "medium",
        })
        wiz.action_submit()
        wo.invalidate_recordset()
        new_msgs = wo.message_ids
        self.assertGreater(
            len(new_msgs), existing_msg_count,
            "WO chatter must receive a back-link note pointing at the "
            "new ECO after submit.")
        body = " ".join(new_msgs.mapped("body") or [])
        self.assertIn("ECO", body)

    def test_50_mi_check_linkage_in_description(self):
        wo = self._make_wo()
        check = self.Check.create({
            "name": "W034 origin defect",
            "message": "origin",
            "severity": "warning",
            "category": "production",
            "production_id": wo.production_id.id,
            "x_sbk_workorder_id": wo.id,
            "x_sbk_inspector_id": self.env.user.id,
        })
        wiz = self.Wizard.with_context(
            default_workorder_id=wo.id,
        ).create({
            "title": "linked to MI check",
            "description": "linked",
            "severity": "medium",
            "mi_check_id": check.id,
        })
        wiz.action_submit()
        eco = self._eco_by_title("linked to MI check")
        self.assertIn(
            check.display_name, eco.description or "",
            "The originating MI check display name should be embedded "
            "in the ECO description.")

    def test_60_submit_as_real_mrp_user_no_access_error(self):
        """The 2026-08-02 blindspot bug: the wizard is usable by plain
        mrp.group_mrp_user (shop floor), creates the ECO with sudo(),
        but used to hand back an act_window onto that ECO's form. A
        Manufacturing user has NO access rows on southbrook.eco (only
        southbrook_plm.group_southbrook_plm_user / _approver do), so
        the redirect raised AccessError immediately after a successful
        save. TransactionCase runs as superuser by default, which is
        exactly why the earlier tests in this file never caught it —
        this test drives the wizard as a REAL non-superuser mrp user
        via with_user() so the regression can't hide again."""
        wo = self._make_wo()
        mrp_group = self.env.ref("mrp.group_mrp_user")
        operator = self.env["res.users"].create({
            "name": "W034 Shop Floor Operator",
            "login": "w034_operator@test",
            "email": "w034_operator@test",
            "group_ids": [(4, mrp_group.id)],
        })

        # Sanity check on the premise: this operator has no read access
        # on southbrook.eco at all. If this assertion ever fails (e.g.
        # someone widens southbrook.eco access to mrp users), the rest
        # of this test still must pass, but the redirect-avoidance
        # design would no longer be strictly necessary.
        with self.assertRaises(AccessError):
            self.Eco.with_user(operator).search([], limit=1)

        wiz = self.Wizard.with_user(operator).with_context(
            default_workorder_id=wo.id,
            active_model="mrp.workorder",
            active_id=wo.id,
        ).create({
            "title": "W034 real-user submit",
            "description": "Operator-filed issue, non-sudo session.",
            "severity": "high",
        })

        # This must NOT raise AccessError — that's the whole bug.
        result = wiz.action_submit()

        self.assertEqual(
            result.get("type"), "ir.actions.client",
            "Submit must return a client action, never an act_window "
            "onto a model the operator can't read.")
        self.assertEqual(result.get("tag"), "display_notification")
        params = result.get("params", {})
        self.assertEqual(params.get("type"), "success")
        self.assertFalse(params.get("sticky"))
        self.assertIn("W034 real-user submit", params.get("message", ""))

        # The ECO really was created (via the sudo'd create inside the
        # wizard), even though the operator can't read it back directly.
        eco = self._eco_by_title("W034 real-user submit")
        self.assertTrue(
            eco.exists(),
            "ECO must exist after a real-operator submit, even though "
            "that operator has no direct read access to the model.")

        # The operator CAN read the traceable back-link on the WO they
        # already have access to.
        wo_as_operator = wo.with_user(operator)
        wo_as_operator.invalidate_recordset()
        body = " ".join(wo_as_operator.message_ids.mapped("body") or [])
        self.assertIn("Engineering change reported", body)
        self.assertIn(eco.name, body)
