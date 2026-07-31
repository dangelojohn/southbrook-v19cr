# SPDX-License-Identifier: LGPL-3.0-only
"""Wizard polish: rename last-step Next button to Confirm.

End-to-end test 2026-06-22 (Bug #4): the OCA configurator wizard's
final step still showed "Next" — confusing because the click actually
materialised the variant and closed the wizard. Customers expected
"Confirm" or "Done" on the terminal step.

Fix: add a computed `is_last_step` to the wizard, then the view layer
swaps the Next button for a Confirm button when true. Both buttons
call the same `action_next_step` method — the OCA implementation
already routes to `action_config_done()` when no next step exists.

The compute is deliberately defensive: it returns False whenever the
wizard isn't on a real step yet (template-picker state, no session,
no template), so first-load never lights up the Confirm button. Any
unexpected error → False (Next stays visible) to avoid masking the
forward path on a broken state.

Phase 1 sales-rep UX fix (2026-07-31) — CLAUDE.md section 5 requires
that when a configurator option is unavailable, the sales rep sees WHY
(the customer never does). `add_dynamic_fields` below (already
southbrook's override seam onto the OCA wizard) hides a blocked value
by narrowing the field's domain (`__domain_<attr_id>`) — the rep just
sees the option vanish, with no reason. `_sb_blocked_option_help()`
closes that gap: for each dynamic attribute field it re-derives which
of the attribute's values are unavailable using
`config_session_id.values_available()` — the exact rule-engine call
the OCA wizard's own `read()` uses to build that field's domain
(product_configurator/wizard/product_configurator.py `read()`) — so
the reason can never diverge from what's actually hidden. The reason
text is the triggering `product.config.domain.name` — an
already-authored, human-readable string from data/config_rules.xml
(e.g. "Series is Contractor"); nothing here re-implements a rule as a
Python `if` branch. The result is set as the field's `help=`
attribute, rendered by the standard Odoo tooltip — no new widget.

Rep-only, structurally: this wizard (`product.configurator`) and its
views live entirely in southbrook_estimating, which does NOT depend on
website_product_configurator and has no website route of its own. The
customer-facing catalog (southbrook_configurator_ux) never opens this
model — it talks to product.config.session directly through its own
JS/controller. So this view is only reachable via a backend
act_window (the "Configure Product" header button / per-line
"Reconfigure" gear) — only to a logged-in internal user. The help text
added here cannot leak into a customer template.
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProductConfigurator(models.TransientModel):
    _inherit = "product.configurator"

    # ------------------------------------------------------------------
    # QA Bug 1 fix (2026-07-04): the base create() (product_configurator/
    # wizard/product_configurator.py) unconditionally does
    # `int(vals.get("product_tmpl_id"))` to seed/find a
    # product.config.session. If neither product_id nor product_tmpl_id
    # is in vals, that's `int(False) == 0`, and the resulting session
    # create() hits product.config.session.product_tmpl_id's NOT NULL
    # constraint — surfacing as a raw, uncaught ORM ValidationError to
    # the end user (see models/sale_order.py's action_config_start
    # override for the UX-level fix; this is the defensive backstop for
    # any OTHER path that still reaches create() without a template).
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("product_id") and not vals.get("product_tmpl_id"):
                raise UserError(self.env._(
                    "Please select a Configurable Template before "
                    "continuing."))
        return super().create(vals_list)

    is_last_step = fields.Boolean(
        compute="_compute_is_last_step",
        help="True when the wizard is on its terminal configuration "
             "step. Drives the Confirm-vs-Next button swap.",
    )

    @api.depends("state", "value_ids", "product_tmpl_id",
                 "config_session_id")
    def _compute_is_last_step(self):
        for wiz in self:
            wiz.is_last_step = False
            session = wiz.config_session_id
            tmpl = wiz.product_tmpl_id
            if not session or not tmpl or wiz.state == "select":
                continue
            try:
                adj = session.get_adjacent_steps(
                    value_ids=session.value_ids.ids,
                )
                # Empty dict = template has no config_step_lines
                # (one-page wizard) → first action_next_step click IS
                # the confirm. Otherwise next_step is the recordset
                # or None.
                if not adj:
                    wiz.is_last_step = True
                else:
                    wiz.is_last_step = not adj.get("next_step")
            except Exception:
                wiz.is_last_step = False

    # ------------------------------------------------------------------
    # Hide single-value attribute lines that have a backfilled default_val.
    #
    # Phase 3 follow-up (2026-06-24): the wizard renders one field per
    # attribute_line, including lines where the template carries exactly
    # one value (e.g. attr_family on every Q8 template carries one Family
    # value). The user has to click through these no-choice pickers.
    #
    # _backfill_single_value_attribute_defaults (see __init__.py) sets
    # default_val on those lines so OCA's session.create() auto-applies
    # the value to value_ids. This override post-processes the wizard
    # view arch and adds invisible="1" to the matching fields — BUT only
    # when default_val is set, so a line that never got backfilled stays
    # visible and the user is never stranded with no way to submit.
    # ------------------------------------------------------------------
    @api.model
    def add_dynamic_fields(self, res, dynamic_fields, wiz):
        mod_view = super().add_dynamic_fields(res, dynamic_fields, wiz)
        if not wiz.product_tmpl_id:
            return mod_view
        field_prefix = self._prefixes.get("field_prefix")
        AttrLine = self.env["product.template.attribute.line"].sudo()
        # Build attribute_id -> attr_line map once per render.
        line_by_attr = {
            line.attribute_id.id: line
            for line in wiz.product_tmpl_id.attribute_line_ids
        }
        for field in mod_view.xpath(f"//field[starts-with(@name, '{field_prefix}')]"):
            name = field.get("name") or ""
            try:
                attr_id = int(name[len(field_prefix):])
            except (ValueError, TypeError):
                continue
            line = line_by_attr.get(attr_id)
            if not line:
                continue
            if len(line.value_ids) == 1 and line.default_val:
                # Both safe-to-hide gates met.
                field.set("invisible", "1")
            # Phase 1 sales-rep UX fix (2026-07-31) — see module
            # docstring. Adds a rep-only tooltip naming any value
            # currently missing from this field's picklist and the
            # declarative rule that's blocking it.
            help_text = self._sb_blocked_option_help(wiz, line)
            if help_text:
                field.set("help", help_text)
        return mod_view

    def _sb_blocked_option_help(self, wiz, attr_line):
        """Rep-facing tooltip text for values currently blocked on
        `attr_line`, or "" when nothing is blocked.

        Reuses `config_session_id.values_available()` — the identical
        call the base OCA wizard's own `read()` makes to build this
        field's `__domain_<attr_id>` — so a value only ever shows up
        here when it is ALSO the value the picklist actually hides.
        The reason is the triggering `product.config.line.domain_id.
        name` (authored in data/config_rules.xml); if a value is
        blocked by more than one rule, or by a rule whose domain
        record was left unnamed, that is surfaced honestly rather than
        inventing wording (CLAUDE.md brief: no fabricated copy).
        """
        session = wiz.config_session_id
        all_values = attr_line.value_ids
        if not session or not all_values:
            return ""
        available_ids = set(session.values_available(
            check_val_ids=all_values.ids,
            product_template_attribute_line_id=attr_line.id,
        ))
        blocked = all_values.filtered(lambda v: v.id not in available_ids)
        if not blocked:
            return ""
        tmpl = wiz.product_tmpl_id
        notes = []
        for val in blocked:
            config_lines = tmpl.config_line_ids.filtered(
                lambda line, val=val, attr_line=attr_line:
                    line.attribute_line_id == attr_line
                    and val.id in line.value_ids.ids
            )
            reasons = [r for r in config_lines.mapped("domain_id.name") if r]
            reason = (
                "; ".join(sorted(set(reasons))) if reasons
                else "the rest of the current configuration"
            )
            notes.append(
                self.env._("%(value)s isn't offered here — it requires %(reason)s.")
                % {"value": val.name, "reason": reason}
            )
        return self.env._(
            "Rep note, not shown to the customer: %(notes)s"
        ) % {"notes": " ".join(notes)}
