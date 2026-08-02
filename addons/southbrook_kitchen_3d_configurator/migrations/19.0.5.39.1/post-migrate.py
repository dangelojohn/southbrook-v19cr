# SPDX-License-Identifier: LGPL-3.0-only
"""Debt fix (2026-08-02) — thumbFamily explicit footprint fields, backfill.

`product.template.southbrook_thumb_family` (new field, this version) is the
explicit override `static/src/js/canvas/cabinet_thumb.esm.js`'s `thumbFamily()`
has always wanted in place of its SKU/name keyword inference (that module's
own doc comment used to call it "the honest fix ... pending"). The field wins
outright when set; left blank, `thumbFamily()` behaves exactly as it always
has.

This migration is the one-time backfill: for every `southbrook_is_cabinet`
template, compute what `thumbFamily()` would infer TODAY (from
`southbrook_cabinet_type` + `default_code` + `name`) and write that value
into the new field, so the catalog reads as explicit from day one instead of
silently depending on the client-side inference for every existing row.

`_infer_thumb_family` below is a literal Python port of `thumbFamily()`'s
JS logic as of this version — same branch order, same `.includes(...)`
substring semantics (not `startswith`), same fallback-of-a-fallback shape.
It intentionally reproduces today's behaviour byte-for-byte, INCLUDING any
of its known rough edges (e.g. a bare "SB-CORNER" code with no BLIND/DIAG/
LSUSAN/WALL-CORNER keyword and no matching name keyword resolves to
"cornerL", the same generic bucket the JS fallback would have shown for
that row before this migration ever ran) — this backfill's job is to make
the CURRENT observed icon explicit, not to silently correct it. See
`corner_cabinet.esm.js`'s `cornerStyleFromCode` doc comment for the
documented, deliberate gap between that bucket and the mesh's own "diag"
style for the same SKU — a separate, out-of-scope reconciliation.

Idempotent and non-destructive: only templates whose
`southbrook_thumb_family` is currently unset (False) are touched; a value a
human already set (whether it agrees with the inference or not) is NEVER
overwritten. A second run finds nothing left to fill and changes nothing.

Fires on the 19.0.5.39.1 upgrade (bumped from 19.0.5.39.0, the version this
field was authored against).
"""
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_TWO_DOOR_RE = re.compile(r"2[- ]?DOOR|DOUBLE")

# Keep in lockstep with cabinet_thumb.esm.js's FAMILY_LABEL keys — the
# fixed 11-value vocabulary both the field's Selection and thumbFamily()
# share.
_VALID_FAMILIES = frozenset((
    "base1", "base2", "drawer", "sink", "vanity", "wall", "tall", "panel",
    "cornerL", "cornerBlind", "cornerDiag",
))


def _infer_thumb_family(cabinet_type, default_code, name):
    """Literal port of cabinet_thumb.esm.js's thumbFamily() FALLBACK path
    (the part below its explicit-field precedence check — there is no
    field to be explicit about yet when this function is computing what
    to backfill INTO that field)."""
    ctype = (cabinet_type or "base").lower()
    code = (default_code or "").upper()
    name_lower = (name or "").lower()

    if ctype == "corner":
        if "BLIND" in code:
            return "cornerBlind"
        if "DIAG" in code or "LSUSAN" in code:
            return "cornerDiag"
        if "WALL-CORNER" in code:
            return "cornerL"
        if "blind" in name_lower:
            return "cornerBlind"
        if "diagonal" in name_lower:
            return "cornerDiag"
        return "cornerL"

    if ctype == "wall":
        return "wall"
    if ctype == "tall":
        return "tall"
    if ctype in ("panel", "filler"):
        return "panel"

    hay = code + " " + (name or "").upper()
    if "SINK" in hay:
        return "sink"
    if "VANITY" in hay:
        return "vanity"
    if "DRAWER" in hay:
        return "drawer"
    if _TWO_DOOR_RE.search(hay):
        return "base2"
    return "base1"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "product.template" not in env:
        _logger.warning(
            "19.0.5.39.1 post-migrate: product.template model not found — "
            "skipping southbrook_thumb_family backfill.")
        return
    if "southbrook_thumb_family" not in env["product.template"]._fields:
        _logger.warning(
            "19.0.5.39.1 post-migrate: product.template has no "
            "southbrook_thumb_family field yet — skipping backfill "
            "(schema not loaded before this migration ran?).")
        return
    _backfill_thumb_family(env)


def _backfill_thumb_family(env):
    Template = env["product.template"]
    # active_test=False — an archived cabinet template still benefits from
    # an explicit, human-inspectable icon family; matches this addon's own
    # convention (e.g. the 19.0.5.35.2 stale-auto-name migration).
    templates = Template.with_context(active_test=False).search([
        ("southbrook_is_cabinet", "=", True),
        ("southbrook_thumb_family", "=", False),
    ])

    filled = []
    skipped_invalid = []
    for tmpl in templates:
        family = _infer_thumb_family(
            tmpl.southbrook_cabinet_type, tmpl.default_code, tmpl.name)
        if family not in _VALID_FAMILIES:
            # Defensive only — _infer_thumb_family's own branches only
            # ever return a _VALID_FAMILIES member, so this should be
            # unreachable; never write a value the Selection field would
            # reject and crash the whole backfill over one bad row.
            skipped_invalid.append((tmpl.id, family))
            _logger.warning(
                "19.0.5.39.1 post-migrate: computed family %r for "
                "template %s (%s) is not a recognised "
                "southbrook_thumb_family value — leaving it unset.",
                family, tmpl.id, tmpl.default_code)
            continue
        tmpl.write({"southbrook_thumb_family": family})
        filled.append((tmpl.id, tmpl.default_code or tmpl.name, family))

    if not filled and not skipped_invalid:
        _logger.info(
            "19.0.5.39.1 post-migrate: no southbrook_is_cabinet template "
            "with an unset southbrook_thumb_family found — nothing to "
            "backfill (0 rows).")
        return

    _logger.info(
        "19.0.5.39.1 post-migrate: backfilled southbrook_thumb_family on "
        "%d cabinet template(s) (%d skipped as unrecognised): %s",
        len(filled), len(skipped_invalid),
        ", ".join("%s(%s)->%s" % (tid, code, fam) for tid, code, fam in filled),
    )
