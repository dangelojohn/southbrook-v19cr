# SPDX-License-Identifier: LGPL-3.0-only
"""Pre-migrate 19.0.2.0.0 — safety three-state remediation, step 1 of 2.

product_template.py converts x_southbrook_hazardous / _flammable /
_requires_ventilation / _msds_required from fields.Boolean to
fields.Selection(yes/no/not_assessed). Odoo's schema auto-migration
does not convert an existing boolean column in place to the varchar
column a Selection field needs — left alone, the ORM would either
error during the registry upgrade or (worse) silently strand the old
boolean data under a column type nothing can read as the new
selection's text values.

This script runs BEFORE the registry reloads product_template.py's
field declarations, so it renames each raw boolean column to
``<field>_bool_legacy`` while it is still safe to touch it directly
with SQL. With the plain field name free, the ORM upgrade that follows
creates a fresh varchar column for the Selection with no conflict.
post-migrate.py (same version dir) then reads the legacy boolean data
back out of the renamed columns, writes the correct three-state value
into the new selection columns, and drops the ``*_bool_legacy``
columns for good.

Guarded so it is safe to re-run: a column is only renamed if it (a)
currently exists under the plain field name and (b) is boolean-typed.
A fresh install (ORM creates the Selection column directly, no boolean
predecessor ever existed) or a prior partial run (already renamed) are
both no-ops here.
"""
import logging

_logger = logging.getLogger(__name__)

# The four safety fields this remediation converts. Order matches the
# form view's Safety group / product_template.py's field declarations.
SAFETY_FIELDS = (
    "x_southbrook_hazardous",
    "x_southbrook_flammable",
    "x_southbrook_requires_ventilation",
    "x_southbrook_msds_required",
)

TABLE = "product_template"


def _rename_boolean_column_to_legacy(cr, field_name):
    """Rename ``product_template.<field_name>`` to
    ``<field_name>_bool_legacy`` iff it currently exists and is a
    boolean column. Returns True iff a rename actually happened —
    tests call this directly (see tests/test_safety_three_state.py)."""
    cr.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (TABLE, field_name),
    )
    row = cr.fetchone()
    if not row:
        # No column under the plain name at all — fresh install with
        # no boolean predecessor, or a prior run already renamed it
        # away. Nothing to do.
        _logger.info(
            "safety-three-state pre-migrate: %s has no existing "
            "column — nothing to rename (fresh install or already "
            "migrated).", field_name)
        return False
    if row[0] != "boolean":
        # Already varchar (Selection) — a prior run already migrated
        # it, or the ORM upgrade already ran ahead of this script for
        # some reason. Do not touch it.
        _logger.info(
            "safety-three-state pre-migrate: %s is already %s, not "
            "boolean — skipping rename.", field_name, row[0])
        return False

    legacy_name = f"{field_name}_bool_legacy"
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (TABLE, legacy_name),
    )
    if cr.fetchone():
        # A legacy column from a partial prior run is already sitting
        # there — do not clobber it by renaming over it a second time.
        _logger.warning(
            "safety-three-state pre-migrate: %s already exists, "
            "leaving %s as-is (possible partial prior run — check "
            "manually before re-running).", legacy_name, field_name)
        return False

    cr.execute(
        f'ALTER TABLE "{TABLE}" RENAME COLUMN "{field_name}" '
        f'TO "{legacy_name}"'
    )
    _logger.info(
        "safety-three-state pre-migrate: renamed %s.%s to %s.",
        TABLE, field_name, legacy_name)
    return True


def migrate(cr, version):
    if not version:
        return
    for field_name in SAFETY_FIELDS:
        _rename_boolean_column_to_legacy(cr, field_name)
