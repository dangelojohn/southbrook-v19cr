# SPDX-License-Identifier: LGPL-3.0-only
"""Extends SouthbrookOrderBuilderPortal with the production-request
action branch.

2026-06-03 — production-approval gate. The dealer-mode FooterActions
"Request Production" button fires /southbrook/api/order/<id>/action with
action_code='request_production' (the legacy 'send_to_production' code is
accepted as an alias so older cached bundles keep working). We delegate
to sale.order.action_request_production(), which submits the confirmed
order for approval — it does NOT create MOs.

MO creation now happens only when an approver runs
action_approve_production() in the backend (the single-chokepoint gate),
so production can never start from the portal without sign-off.
"""
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from odoo.addons.southbrook_estimating_website.controllers.main import (
    SouthbrookOrderBuilderPortal,
)

# action_codes that mean "submit this order for production approval".
_REQUEST_PRODUCTION_CODES = ("request_production", "send_to_production")


class SouthbrookOrderBuilderPortalMRP(SouthbrookOrderBuilderPortal):

    def southbrook_api_order_action(self, order_id, action_code=None, **kw):
        if action_code not in _REQUEST_PRODUCTION_CODES:
            return super().southbrook_api_order_action(
                order_id, action_code=action_code, **kw,
            )

        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}

        # sudo() bypasses the portal user's lack of sale.order write
        # access; with_user keeps env.user as the real dealer, so the
        # request is attributed to them (production_requested_by) rather
        # than to OdooBot.
        try:
            order.with_user(request.env.user).sudo().action_request_production()
        except UserError as e:
            return {"error": "wrong_state", "message": str(e)}

        return {
            "ok": True,
            "requested": True,
            "approval_state": order.production_approval_state,
            "message": (
                "Submitted for production approval. You'll be notified "
                "once it's reviewed."
            ),
        }
