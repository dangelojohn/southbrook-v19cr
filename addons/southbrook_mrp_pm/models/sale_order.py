# SPDX-License-Identifier: LGPL-3.0-only
"""sale.order extension — Send-to-Production action.

M3 + M7 of the Manufacturing PM JTBD gap analysis (2026-06-01):

  Pre-fix: the customer-facing OrderBuilder shows a 'Send to
  Production' button in dealer mode (FooterActions component),
  but the underlying action_code='send_to_production' is not yet
  wired. Clicking it returns 'unknown_action' from the dispatcher.

  Wire shape (this commit):
    sale.order.action_send_to_production() — for each confirmed line
      with a resolvable BoM, creates one mrp.production carrying the
      M6 routing, confirms it, and returns the MO ids. Returns a
      list of MO records for the controller to translate into a
      JSON-RPC payload.

  Send-to-Production is idempotent on the sale.order — if MOs
  already exist with origin=order.name, the call is a no-op that
  returns the existing MO ids.

  Out of scope for this commit:
    - The other 11 SB-* SKUs need parallel BoM + routing seeds
      (their lines will skip MO creation until that lands).
    - Scheduling logic: today date_start=now(), date_deadline=
      now()+SUM(routing minutes). Realistic capacity-aware
      scheduling lives in M18.
    - ECO → in-flight MO notification (M20).
"""
from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # ------------------------------------------------------------------
    # Production-approval gate (folded into mrp_pm 2026-06-03).
    #
    # There is no custom lifecycle stage field on sale.order — the
    # native `state` (draft/sent/sale/done/cancel) IS the order
    # lifecycle, and "In Production" is represented implicitly by the
    # existence of mrp.production records (origin == order.name) created
    # by action_send_to_production(). The approval gate therefore sits
    # BETWEEN native 'sale' (Confirmed) and the MO-creation call: a
    # confirmed order must be approved before any MO is created.
    #
    # The single chokepoint is action_send_to_production() itself, which
    # both the dealer portal button (via the order_builder controller)
    # and the backend form button route through. Guarding it there means
    # production cannot start by any path without approval.
    # ------------------------------------------------------------------
    _PRODUCTION_APPROVAL_STATES = [
        ("none", "Not Submitted"),
        ("pending", "Pending Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    production_approval_state = fields.Selection(
        selection=_PRODUCTION_APPROVAL_STATES,
        string="Production Approval",
        default="none",
        copy=False,
        tracking=True,
        help="Approval gate that must reach 'Approved' before any "
             "manufacturing order can be created for this sale order.",
    )
    production_requested_by = fields.Many2one(
        "res.users",
        string="Production Requested By",
        copy=False,
        readonly=True,
    )
    production_requested_date = fields.Datetime(
        string="Production Requested On",
        copy=False,
        readonly=True,
    )
    production_approved_by = fields.Many2one(
        "res.users",
        string="Production Approved/Rejected By",
        copy=False,
        readonly=True,
    )
    production_approved_date = fields.Datetime(
        string="Production Decision On",
        copy=False,
        readonly=True,
    )
    production_reject_reason = fields.Text(
        string="Rejection Reason",
        copy=False,
    )

    def action_send_to_production(self):
        """Create one mrp.production per order line that has a
        resolvable BoM. Idempotent on the sale.order: existing
        MOs with origin matching this order's name are returned
        unchanged.

        Returns:
            recordset of mrp.production created OR already-existing
            for this order.

        Raises:
            UserError if the order is not in 'sale' state, or if the
            production-approval gate has not reached 'approved'.
        """
        self.ensure_one()
        if self.state != "sale":
            raise UserError(_(
                "Order %(name)s must be confirmed (sale state) before "
                "sending to production. Current state: %(state)s."
            ) % {"name": self.name, "state": self.state})

        # Approval gate (2026-06-03). MO creation is only reachable once
        # an approver has signed off. This guards BOTH callers — the
        # backend form button and the dealer portal "Send to Production"
        # button (order_builder controller) — so production cannot start
        # without approval by any route. action_approve_production() sets
        # the state to 'approved' immediately before calling this method,
        # so the approve path passes the gate.
        if self.production_approval_state != "approved":
            raise UserError(_(
                "Order %(name)s cannot be sent to production until it is "
                "approved. Current approval state: %(state)s. Use "
                "'Request Production' and have an approver sign off first."
            ) % {
                "name": self.name,
                "state": dict(self._PRODUCTION_APPROVAL_STATES).get(
                    self.production_approval_state,
                    self.production_approval_state,
                ),
            })

        MO = self.env["mrp.production"].sudo()
        Bom = self.env["mrp.bom"].sudo()

        # Idempotency: short-circuit if MOs already exist for this SO.
        existing = MO.search([("origin", "=", self.name)])
        if existing:
            return existing

        created = MO.browse()
        for line in self.order_line:
            if not line.product_id:
                continue
            # Resolve the BoM applicable to this variant. _bom_find
            # signature varies across Odoo versions; cover both.
            bom = self._resolve_bom_for_line(Bom, line)
            if not bom:
                continue

            # Schedule: start now, finish at now + total routing time.
            total_minutes = sum(
                w.time_cycle_manual for w in bom.operation_ids
            )
            start = fields.Datetime.now()
            deadline = start + timedelta(minutes=total_minutes)

            # Odoo 19 renamed sale.order.line.product_uom → product_uom_id.
            line_uom = (
                line.product_uom_id
                if hasattr(line, "product_uom_id")
                else line.product_uom
            )
            mo = MO.create({
                "product_id": line.product_id.id,
                "product_uom_id": line_uom.id,
                "product_qty": line.product_uom_qty,
                "bom_id": bom.id,
                "origin": self.name,
                "date_start": start,
                "date_deadline": deadline,
            })
            # Confirm the MO so work orders materialise from the
            # routing. The standard flow is create → confirm →
            # plan → start. Confirm gives Floor Manager visibility
            # in the kanban board; planning + start happens on the
            # floor.
            mo.action_confirm()
            created |= mo

        return created

    @staticmethod
    def _resolve_bom_for_line(Bom, line):
        """Find the lowest-sequence normal BoM for the line's variant
        or template. Falls back to a plain search if _bom_find is
        unavailable / signature mismatched."""
        # Modern Odoo (16+) — _bom_find takes a recordset and returns
        # a dict keyed by product.
        if hasattr(Bom, "_bom_find"):
            try:
                result = Bom._bom_find(
                    products=line.product_id,
                    company_id=line.company_id.id,
                    bom_type="normal",
                )
                if isinstance(result, dict):
                    bom = result.get(line.product_id)
                    if bom:
                        return bom
            except TypeError:
                # Older signature — fall through to search.
                pass

        # Fallback search: lowest-sequence normal BoM matching the
        # variant or the variant's template.
        return Bom.search(
            [
                "|",
                ("product_id", "=", line.product_id.id),
                "&",
                ("product_id", "=", False),
                (
                    "product_tmpl_id",
                    "=",
                    line.product_id.product_tmpl_id.id,
                ),
                ("type", "=", "normal"),
            ],
            order="sequence, id",
            limit=1,
        )

    # ------------------------------------------------------------------
    # Production-approval workflow (2026-06-03).
    #
    # request → (approver) approve | reject. Approve is the ONLY path
    # that reaches MO creation: it flips the gate to 'approved' and then
    # calls action_send_to_production(). Notifications follow the house
    # pattern — message_post chatter + a mail.activity To-Do for the
    # approver group — NOT mail.template (the codebase ships zero).
    # ------------------------------------------------------------------
    _APPROVER_GROUP = "southbrook_mrp_pm.group_southbrook_production_approver"

    def _assert_production_approver(self):
        """Defense-in-depth: the view hides Approve/Reject behind the
        approver group, but the methods guard too in case they're
        reached by RPC / server action."""
        if not self.env.user.has_group(self._APPROVER_GROUP):
            raise UserError(_(
                "Only a Production Approver can approve or reject a "
                "production request."
            ))

    def action_request_production(self):
        """Submit a confirmed order for production approval. Notifies
        the approver group via a To-Do activity + chatter."""
        self.ensure_one()
        if self.state != "sale":
            raise UserError(_(
                "Order %(name)s must be confirmed before requesting "
                "production. Current state: %(state)s."
            ) % {"name": self.name, "state": self.state})
        if self.production_approval_state not in ("none", "rejected"):
            raise UserError(_(
                "Production for %(name)s has already been submitted "
                "(state: %(state)s)."
            ) % {"name": self.name, "state": self.production_approval_state})

        # sudo() the write: production_requested_by + production_approved_by
        # are readonly Many2one→res.users fields. A salesman (who
        # legitimately requests production for their own order) needs to
        # write here but doesn't necessarily have direct write ACL on
        # readonly fields, and the Many2one target res.users check
        # triggers an AccessError on lookups of foreign user records.
        # The gate's permission boundary is action-level (see
        # _assert_production_approver and the state == 'sale' guard
        # above) — sudo is the right escape hatch for the field write.
        self.sudo().write({
            "production_approval_state": "pending",
            "production_requested_by": self.env.user.id,
            "production_requested_date": fields.Datetime.now(),
            "production_reject_reason": False,
            # Clear any prior decision so a rejected → re-requested order
            # doesn't carry the stale rejecter / decision timestamp into
            # the new pending cycle.
            "production_approved_by": False,
            "production_approved_date": False,
        })

        # Schedule a To-Do activity for every approver so the request
        # surfaces in their Activities inbox. sudo because the
        # salesman doesn't have read access to other users' records.
        # v19: res.groups.users → res.groups.user_ids (explicit users).
        approvers = self.env.ref(self._APPROVER_GROUP, raise_if_not_found=False)
        if approvers:
            # sudo() the scheduling: creating mail.activity records for
            # OTHER users (the approvers) needs elevated access. The
            # salesman owns this sale.order legitimately but doesn't
            # have ACL to write to the approver's activities directly.
            order_su = self.sudo()
            requester_name = self.env.user.name
            for user in approvers.sudo().user_ids:
                order_su.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=user.id,
                    summary=_("Production approval requested"),
                    note=_(
                        "%(user)s requested production for order "
                        "%(name)s. Review and approve or reject."
                    ) % {"user": requester_name, "name": self.name},
                )
        self.sudo().message_post(
            body=_("Production requested by %(user)s — awaiting approval.")
            % {"user": self.env.user.name},
            subject=_("Production approval requested — %s") % self.name,
        )
        # Customer-facing email (hybrid notification — chatter/activity
        # above is the internal hand-off; this is the outbound mail).
        # Queued via the standard outgoing-mail layer (force_send=False)
        # so the transaction doesn't block on SMTP.
        template = self.env.ref(
            "southbrook_mrp_pm.mail_template_production_submitted",
            raise_if_not_found=False,
        )
        if template:
            template.sudo().send_mail(self.id, force_send=False)
        return True

    def action_approve_production(self):
        """Approver sign-off. Flips the gate to 'approved' and creates
        the manufacturing orders via action_send_to_production()."""
        self.ensure_one()
        self._assert_production_approver()
        if self.production_approval_state != "pending":
            raise UserError(_(
                "Order %(name)s is not pending approval (state: "
                "%(state)s)."
            ) % {"name": self.name, "state": self.production_approval_state})

        # See note on action_request_production: sudo() the readonly-
        # field write. The permission check above (_assert_production_
        # approver) is the boundary; the field write is mechanical.
        self.sudo().write({
            "production_approval_state": "approved",
            "production_approved_by": self.env.user.id,
            "production_approved_date": fields.Datetime.now(),
        })
        # Close the outstanding approval To-Do activities.
        self.activity_feedback(["mail.mail_activity_data_todo"])

        # Now actually create the MOs. The gate above is satisfied, so
        # the guard in action_send_to_production() passes.
        mos = self.action_send_to_production()

        self.sudo().message_post(
            body=_(
                "Production approved by %(user)s — %(count)s "
                "manufacturing order(s) created."
            ) % {"user": self.env.user.name, "count": len(mos)},
            subject=_("Production approved — %s") % self.name,
            partner_ids=self.production_requested_by.partner_id.ids,
        )
        # Customer-facing approval email (hybrid notification).
        template = self.env.ref(
            "southbrook_mrp_pm.mail_template_production_approved",
            raise_if_not_found=False,
        )
        if template:
            template.sudo().send_mail(self.id, force_send=False)

        if not mos:
            return True
        # Surface the created MOs to the approver.
        return {
            "type": "ir.actions.act_window",
            "name": _("Manufacturing Orders"),
            "res_model": "mrp.production",
            "domain": [("id", "in", mos.ids)],
            "view_mode": "list,form",
        }

    def action_reject_production(self):
        """Approver rejection. Returns the order to a re-submittable
        state; no MOs are created."""
        self.ensure_one()
        self._assert_production_approver()
        if self.production_approval_state != "pending":
            raise UserError(_(
                "Order %(name)s is not pending approval (state: "
                "%(state)s)."
            ) % {"name": self.name, "state": self.production_approval_state})

        # sudo() the readonly write — same pattern as request/approve.
        self.sudo().write({
            "production_approval_state": "rejected",
            "production_approved_by": self.env.user.id,
            "production_approved_date": fields.Datetime.now(),
        })
        self.activity_feedback(["mail.mail_activity_data_todo"])

        reason = self.production_reject_reason or _("(no reason given)")
        self.sudo().message_post(
            body=_("Production rejected by %(user)s. Reason: %(reason)s")
            % {"user": self.env.user.name, "reason": reason},
            subject=_("Production rejected — %s") % self.name,
            partner_ids=self.production_requested_by.partner_id.ids,
        )
        return True
