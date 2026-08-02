from datetime import datetime, timedelta
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError
_logger = logging.getLogger(__name__)

KNOWN_RULE_KEYS = [
    ('width_to_door_count', 'Width → door count threshold'),
    ('depth_to_drawer_count', 'Depth → drawer count threshold'),
    ('door_reveal_offset', 'Door reveal offset'),
    ('box_thickness_override', 'Box panel thickness override'),
    ('shelf_count_override', 'Shelf count override'),
    ('hardware_substitution', 'Hardware substitution'),
    ('other', 'Other'),
]

REASON_CODES = [
    ('customer_request', 'Customer-requested change'),
    ('material_constraint', 'Material constraint / out-of-stock'),
    ('tooling_constraint', 'Tooling constraint / blade unavailable'),
    ('rework_preference', 'Floor rework — easier to build differently'),
    ('engineering_typo', 'Engineering data error caught on floor'),
    ('other', 'Other'),
]


class CutSpecOverride(models.Model):
    _name = 'southbrook.cut.spec.override'
    _description = 'Recorded deviation from default cut.spec / construction rule'
    _order = 'recorded_at desc'
    _inherit = ['mail.thread']

    workorder_id = fields.Many2one('mrp.workorder', required=True, ondelete='cascade', index=True, tracking=True)
    production_id = fields.Many2one(related='workorder_id.production_id', store=True, index=True)
    rule_key = fields.Selection(KNOWN_RULE_KEYS, required=True, index=True, tracking=True)
    rule_default_value = fields.Char(required=True, tracking=True)
    actual_applied_value = fields.Char(required=True, tracking=True)
    operator_id = fields.Many2one('res.users', default=lambda s: s.env.user.id, required=True, tracking=True)
    reason_code = fields.Selection(REASON_CODES, default='other', required=True, tracking=True)
    notes = fields.Text()
    recorded_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    eco_proposed_id = fields.Many2one('southbrook.eco', string='ECO proposed in response', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        rule_labels = dict(KNOWN_RULE_KEYS)
        for rec in records:
            try:
                wo = rec.workorder_id
                mo_name = wo.production_id.name if wo and wo.production_id else "?"
                rule_label = rule_labels.get(rec.rule_key, rec.rule_key)
                self.env["southbrook.ops.event"].emit(
                    "override_flagged",
                    f"Cut-spec override on {mo_name}: {rule_label} "
                    f"({rec.rule_default_value} → {rec.actual_applied_value})",
                    res_model="southbrook.cut.spec.override",
                    res_id=rec.id,
                    severity="warn",
                )
            except Exception:  # noqa: BLE001
                pass
        return records

    @api.model
    def action_open_cut_spec_override_for_wo(self):
        ctx = dict(self.env.context)
        wo_id = ctx.get('default_workorder_id') or ctx.get('active_id')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Record Cut-Spec Deviation',
            'res_model': 'southbrook.cut.spec.override',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_workorder_id': wo_id},
        }

    @api.model
    def _cron_propose_eco_for_high_frequency_overrides(self, window_days=90, threshold_pct=50.0):
        """If any rule_key has been overridden in >= threshold_pct of a notable population
        over the last `window_days`, and no open ECO already references it, draft one.
        Population is approximated as: total WOs touched that rule's potential domain.
        For simplicity, we use the count of overrides per rule_key and require at least 10
        overrides before raising an ECO (to avoid noisy proposals)."""
        Eco = self.env['southbrook.eco']
        EcoType = self.env['southbrook.eco.type']
        rule_change_type = EcoType.search(
            [('name', '=ilike', '%Construction-Rule Change%')], limit=1
        ) or EcoType.search([('name', '=ilike', '%rule%')], limit=1)
        if not rule_change_type:
            _logger.warning('No Construction-Rule Change ECO type found; skipping ECO proposal sweep.')
            return {'proposed': 0}
        cutoff = fields.Datetime.now() - timedelta(days=window_days)
        # Aggregate overrides by rule_key in the window
        rows = self.read_group(
            domain=[('recorded_at', '>=', cutoff)],
            fields=['rule_key', 'id:count'],
            groupby=['rule_key'],
            lazy=False,
        )
        proposed = 0
        for row in rows:
            rk = row.get('rule_key')
            cnt = row.get('id_count') or row.get('__count') or 0
            if cnt < 10:
                continue
            # Check for an existing open ECO referencing this rule_key
            existing = Eco.search([
                ('state', '=', 'open'),
                ('eco_type_id', '=', rule_change_type.id),
                ('description', 'ilike', rk),
            ], limit=1)
            if existing:
                continue
            sample_overrides = self.search([
                ('rule_key', '=', rk),
                ('recorded_at', '>=', cutoff),
            ], limit=5, order='recorded_at desc')
            description_html = (
                f'<p>Rule <code>{rk}</code> was overridden in <strong>{cnt}</strong> work orders '
                f'in the last {window_days} days. Recommended: review whether the current '
                f'default rule should be updated to match shop-floor practice.</p>'
                f'<p><strong>Sample overrides:</strong></p><ul>'
            )
            for s in sample_overrides:
                description_html += (
                    f'<li>{s.recorded_at}: default <em>{s.rule_default_value}</em> → '
                    f'applied <em>{s.actual_applied_value}</em> (reason: {dict(REASON_CODES).get(s.reason_code, s.reason_code)})</li>'
                )
            description_html += '</ul><p><em>Auto-proposed by southbrook_premium_orchestration.</em></p>'
            # title is required=True (NOT NULL) on southbrook.eco — without
            # it this create crashes and the auto-proposal never lands.
            eco = Eco.create({
                'title': f"Auto-proposed: review rule '{dict(KNOWN_RULE_KEYS).get(rk, rk)}' "
                         f"({cnt} overrides in {window_days}d)",
                'eco_type_id': rule_change_type.id,
                'description': description_html,
                'state': 'open',
            })
            sample_overrides.write({'eco_proposed_id': eco.id})
            proposed += 1
            try:
                rule_label = dict(KNOWN_RULE_KEYS).get(rk, rk)
                self.env["southbrook.ops.event"].emit(
                    "eco_proposed",
                    f"ECO auto-proposed for rule '{rule_label}' "
                    f"({cnt} overrides in {window_days}d)",
                    res_model="southbrook.eco",
                    res_id=eco.id,
                    severity="warn",
                )
            except Exception:  # noqa: BLE001
                pass
        return {'proposed': proposed}
