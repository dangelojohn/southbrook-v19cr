# -*- coding: utf-8 -*-
"""Backfill project.task.partner_id from the originating sale.order.

Kitchen Jobs board remediation (BlindSpot review of action-1298, contract
§2). ``_kitchen_task_vals`` in sale_order.py now sets partner_id when it
creates the spine, but every task created before that fix landed can carry
partner_id=False even though its x_southbrook_sale_order_id plainly has a
customer — the Kitchen Jobs card's L1 line would render "no customer in
data" for a job the CRM has never lost track of.

``_backfill_task_partner_id`` is exposed as a standalone function (not
inlined in ``migrate``) specifically so tests can call it directly and
twice, without going through the ``if not version`` early-return guard
``migrate`` uses for the at-install-with-no-version case. It is naturally
idempotent: the WHERE clause is ``partner_id IS NULL``, so once a task is
backfilled it never matches again.
"""

import logging

_logger = logging.getLogger(__name__)


def _backfill_task_partner_id(env):
    """Set partner_id on any project.task whose SO has one and whose own
    partner_id is unset. Returns the number of tasks touched."""
    Task = env["project.task"]
    if "x_southbrook_sale_order_id" not in Task._fields:
        # Fresh CE/CI DB without southbrook_project's custom field —
        # nothing to backfill, and searching on a nonexistent field would
        # raise at registry build rather than silently no-op.
        return 0

    orphans = Task.search([
        ("partner_id", "=", False),
        ("x_southbrook_sale_order_id", "!=", False),
        ("x_southbrook_sale_order_id.partner_id", "!=", False),
    ])
    count = 0
    for task in orphans:
        task.partner_id = task.x_southbrook_sale_order_id.partner_id.id
        count += 1

    _logger.info(
        "Kitchen Jobs board (19.0.5.0.0): backfilled partner_id on %s "
        "task(s) from their originating sale.order", count)
    return count


def migrate(cr, version):
    if not version:
        return

    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    _backfill_task_partner_id(env)
