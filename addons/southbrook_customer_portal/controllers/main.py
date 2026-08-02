# SPDX-License-Identifier: LGPL-3.0-only
"""Customer-facing /my/kitchen-projects portal."""
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


def _project_state_label(project):
    """Human label for sb.kitchen.project.state — never interpolate the
    raw Selection key (e.g. "awaiting_customer") into customer-facing
    text (UX Phase 2 vocab gate, ux-customer.md audit §4: a hand-written
    UserError string bypassed the Selection field's own labels, which
    the QWeb `t-field="project.state"` widget renders correctly
    elsewhere on this same page). Falls back to the raw value only if
    the field's selection list is ever a callable rather than a static
    list — defensive, not expected to trigger."""
    try:
        selection = dict(project._fields["state"].selection)
    except (TypeError, ValueError):
        return project.state
    return selection.get(project.state, project.state)


class KitchenPortal(http.Controller):

    # ------------------------------------------------------------------
    # Fabio Ask
    # ------------------------------------------------------------------
    @http.route(
        ["/my/fabio"], type="http", auth="user",
        website=True, methods=["GET"],
    )
    def fabio_ask(self, question_id=None, **kw):
        partner = request.env.user.partner_id
        questions = self._fabio_questions_for_partner(partner)
        selected_question = request.env["southbrook.hermes.question"].sudo()
        if question_id:
            try:
                selected_id = int(question_id)
            except (TypeError, ValueError):
                selected_id = 0
            selected_question = questions.filtered(lambda q: q.id == selected_id)[:1]
        return request.render(
            "southbrook_customer_portal.portal_fabio_ask",
            {
                "projects": self._kitchen_projects_for_partner(partner),
                "questions": questions[:10],
                "selected_question": selected_question,
                "page_name": "fabio",
            },
        )

    @http.route(
        ["/my/fabio/ask"], type="http", auth="user",
        website=True, methods=["POST"], csrf=True,
    )
    def fabio_ask_submit(self, **post):
        question_text = (post.get("question") or "").strip()
        if not question_text:
            raise UserError(_("Type a question for Fabio."))
        partner = request.env.user.partner_id
        project = self._optional_project_for_user(post.get("project_id"))
        question = request.env["southbrook.hermes.question"].ask_customer(
            partner,
            question_text,
            project=project,
            user=request.env.user,
        )
        return request.redirect(f"/my/fabio?question_id={question.id}")

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------
    @http.route(
        ["/my/kitchen-projects"], type="http", auth="user",
        website=True, methods=["GET"],
    )
    def kitchen_projects_list(self, **kw):
        partner = request.env.user.partner_id
        projects = self._kitchen_projects_for_partner(partner)
        return request.render(
            "southbrook_customer_portal.portal_kitchen_projects_list",
            {"projects": projects, "page_name": "kitchen_projects"},
        )

    # ------------------------------------------------------------------
    # Detail (concept review)
    # ------------------------------------------------------------------
    @http.route(
        ["/my/kitchen-project/<int:project_id>"], type="http", auth="user",
        website=True, methods=["GET"],
    )
    def kitchen_project_detail(self, project_id, **kw):
        project = self._fetch_project_for_user(project_id)
        return request.render(
            "southbrook_customer_portal.portal_kitchen_project_detail",
            {
                "project": project,
                "options": project.design_option_ids,
                "page_name": "kitchen_project",
            },
        )

    # ------------------------------------------------------------------
    # Select option (POST)
    # ------------------------------------------------------------------
    @http.route(
        ["/my/kitchen-project/<int:project_id>/select/<int:option_id>"],
        type="http", auth="user", website=True, methods=["POST"], csrf=True,
    )
    def kitchen_project_select_option(self, project_id, option_id, **kw):
        project = self._fetch_project_for_user(project_id)
        # Server-side state gate (the template only HIDES the button — a customer
        # legitimately holds a CSRF token from their own pages and can POST
        # directly). Without this, after the project is approved/in production a
        # customer could re-point selected_design_option_id to a different
        # concept than the one approved, desyncing the quote/MO/spec-sheet from
        # the portal with no re-approval or audit. Mirror the approve gate.
        if project.state not in ("designing", "awaiting_customer"):
            # 2026-08-02 UX Phase 2 vocab gate — this used to interpolate
            # the raw Selection VALUE (e.g. "awaiting_customer") into the
            # customer-facing message; a double-submit or back-navigation
            # could show literally "Project state is awaiting_customer..."
            # (ux-customer.md audit §4). _project_state_label() reads the
            # same field's own human labels — the template's
            # `t-field="project.state"` already renders those correctly,
            # this hand-written string just wasn't using them.
            raise UserError(_(
                "This project is already %s, so design options can no "
                "longer be changed. Contact your Southbrook representative "
                "if you need something adjusted."
            ) % _project_state_label(project))
        option = project.design_option_ids.filtered(lambda o: o.id == option_id)
        if not option:
            raise MissingError(_("Design option does not belong to this project."))
        option.sudo().write({"is_selected": True})
        return request.redirect(f"/my/kitchen-project/{project_id}")

    # ------------------------------------------------------------------
    # Approve (POST)
    # ------------------------------------------------------------------
    @http.route(
        ["/my/kitchen-project/<int:project_id>/approve"],
        type="http", auth="user", website=True, methods=["POST"], csrf=True,
    )
    def kitchen_project_approve(self, project_id, **kw):
        project = self._fetch_project_for_user(project_id)
        if not project.selected_design_option_id:
            raise UserError(_(
                "Pick a design option first, then approve."
            ))
        if project.state != "awaiting_customer":
            # 2026-08-02 UX Phase 2 vocab gate — see the matching fix in
            # kitchen_project_select_option above; same raw-enum leak,
            # same fix.
            raise UserError(_(
                "This project is already %s, so it can't be approved "
                "again. Refresh the page to see its current status."
            ) % _project_state_label(project))
        # Create an approval record + advance state.
        request.env["sb.kitchen.approval"].sudo().create({
            "project_id": project.id,
            "approval_type": "design",
            "approver_id": request.env.user.id,
            "approver_type": "customer",
            "state": "approved",
            # was `http.fields.Datetime.now()` — odoo.http has no `fields`
            # attribute, so the guard always evaluated to None and the decision
            # timestamp was never recorded.
            "date_decided": fields.Datetime.now(),
        })
        project.sudo().action_customer_approves()
        return request.redirect(f"/my/kitchen-project/{project_id}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _fetch_project_for_user(self, project_id):
        """Return the project IFF the current portal user owns it.
        Raises MissingError on either no-such-project or wrong-customer
        (we deliberately give the same response to avoid leaking
        existence of other customers' projects)."""
        Project = request.env["sb.kitchen.project"].sudo()
        project = Project.browse(project_id).exists()
        if not project:
            raise MissingError(_("Project not found."))
        partner = request.env.user.partner_id
        if project.partner_id != partner:
            _logger.warning(
                "Portal ACL: user %s (partner %s) attempted to access "
                "project %s owned by partner %s — denied.",
                request.env.user.id, partner.id, project.id,
                project.partner_id.id,
            )
            raise MissingError(_("Project not found."))
        return project

    def _optional_project_for_user(self, project_id):
        if not project_id:
            return request.env["sb.kitchen.project"].sudo()
        try:
            project_id = int(project_id)
        except (TypeError, ValueError) as exc:
            raise MissingError(_("Project not found.")) from exc
        return self._fetch_project_for_user(project_id)

    def _kitchen_projects_for_partner(self, partner):
        return request.env["sb.kitchen.project"].sudo().search(
            [("partner_id", "=", partner.id)],
            order="date_created desc, id desc",
        )

    def _fabio_questions_for_partner(self, partner):
        return request.env["southbrook.hermes.question"].sudo().search([
            ("scope", "=", "customer"),
            ("partner_id", "=", partner.id),
        ], order="create_date desc, id desc")
