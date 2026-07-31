# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook UI Foundation",
    "summary": "Canonical --sb-* design tokens (status colours, spacing, "
               "type, radius, elevation) shared across every Southbrook "
               "addon — dependency-light so any addon can adopt it.",
    "description": """
Southbrook UI Foundation
=========================

Phase 0 of the Southbrook design-system consolidation (see
scratchpad/retest/ux-designsystem.md for the audit this addon responds
to). Ships ONE SCSS token layer:

    static/src/scss/_sb_tokens.scss

Contents:

* A six-state status scale — ok / warn / late / blocked / info /
  neutral — each with a foreground, a background tint, and a border
  token (``--sb-status-<state>-fg/-bg/-border``), computed to clear
  WCAG AA (4.5:1 text, 3:1 non-text) in BOTH light and
  ``[data-theme="dark"]`` themes. This is the headline deliverable:
  today the same semantic ("ok", "danger", "late"...) resolves to 11+
  different greens / 8+ different reds across the codebase, and even
  the one "canonical" token file (southbrook_estimating's
  _southbrook_design_tokens.scss) disagrees with itself between its
  §1 legacy generation and its §6 "2026-06-26 Trade Portal" generation.
* A spacing scale, ``--sb-space-0`` … ``--sb-space-8``, 4px-based.
* A type scale (display / heading / body / label / caption / mono)
  with size + weight + line-height per step.
* Radius (``--sb-corner-*``) and elevation (``--sb-elevation-*``)
  scales, deliberately namespaced away from the legacy
  ``--sb-radius*`` / ``--sb-shadow*`` names already declared by
  southbrook_estimating's token file, so the two files never fight
  over the same custom property when both happen to load in the same
  bundle (several existing addons transitively depend on
  southbrook_estimating already).
* Back-compat aliases: ``--sb-ok``, ``--sb-success``, ``--sb-alert``,
  ``--sb-danger``, ``--sb-warn`` are redeclared here pointing at the
  new ``--sb-status-*`` tokens, so existing SCSS that references the
  legacy names keeps resolving to a real, contrast-checked colour.

This addon has NO models, NO security, NO data — assets only — and
depends on nothing but ``web``, by design: shop-floor and catalog
addons must be able to pull in the shared tokens without also pulling
in the estimating/quoting engine. See the addon README for the
one-line adoption recipe and the "no addon may hardcode a status
colour" rule.

Nothing in ``southbrook_estimating`` or any other addon's SCSS/JS/
Python is modified by this addon. It is purely additive.
""",
    "version": "19.0.1.0.0",
    "category": "Technical",
    "license": "LGPL-3",
    "author": "Southbrook Kitchens",
    "depends": [
        "web",
    ],
    "data": [],
    "assets": {
        "web.assets_backend": [
            "southbrook_ui/static/src/scss/_sb_tokens.scss",
        ],
        "web.assets_frontend": [
            "southbrook_ui/static/src/scss/_sb_tokens.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
