# SPDX-License-Identifier: LGPL-3.0-only
"""SAMI / Southbrook Cabinetry — canonical 7-panel formulas (single source).

Peter Tuschak signed off these formulas on 2026-06-09 (G2 closed). Treat them
as final. Geometric conventions: NF14 (frameless euro construction, metric mm).

This module is intentionally Odoo-free so it can be imported by:
  - addons/southbrook_estimating/models/mrp_bom.py
        (Odoo BoM math — existing implementation at _compute_panel_dimensions;
        eventual convergence is enforced by Module 2's G1 assertion suite)
  - services/freecad_bridge/scripts/render_cabinet.py
        (FreeCAD render dimensions)
  - addons/southbrook_estimating_website/static/src/js/parametric_carcass.esm.js
        (Three.js — uses the .js sibling in this directory)
  - addons/southbrook_freecad_bridge/tests/test_bom_contents.py
        (Module-2 G1 gate — asserts create_get_bom output matches the formulas here)

Convention: a panel cut tuple is (length_mm, width_mm, thickness_mm).

Optional ``constants`` override (PLM cut-spec wiring, 2026-08):
    Every formula function below and ``panel_cut_list()`` accept an optional
    ``constants: Optional[Dict[str, float]] = None`` keyword. When omitted
    (the default, and the ONLY mode ``services/freecad_bridge`` and the
    standalone bridge ever use), every function reads the module-level
    constants exactly as before — output is byte-identical to pre-override
    behaviour. When a caller passes a dict, each of the 8 keys (``box_th``,
    ``back_th``, ``rabbet``, ``door_th``, ``door_reveal``, ``shelf_tol``,
    ``shelf_vent_gap``, ``toekick_h``) is read from the dict, falling back
    to the module constant for any key the dict omits — so a partial
    override dict is safe and never crashes.

    This exists so ``addons/southbrook_kitchen_mrp`` can inject the dict
    produced by an ECO-approved ``southbrook.cut.spec`` record (via
    ``southbrook.cut.spec.constants_dict()``) into the SAME formulas the
    FreeCAD bridge renders from, instead of maintaining a second copy of
    the math. This module itself stays Odoo-free and dependency-free: it
    never imports Odoo and never reads a database — the caller resolves
    the spec and hands over a plain dict.
"""
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Geometric constants — signed off 2026-06-09 (Peter Tuschak, G2 closed).
# ---------------------------------------------------------------------------
BOX_TH: float = 15.875        # 5/8" melamine — carcass material thickness
BACK_TH: float = 6.35         # 1/4" hardboard — back panel
RABBET: float = 6.35          # rabbet depth for back-panel capture
DOOR_TH: float = 18.0         # 3/4" slab/5-piece — door or drawer front
DOOR_REVEAL: float = 3.0      # uniform gap on all four door edges
SHELF_TOL: float = 1.5        # hand-placement clearance, subtracted from inside_width
SHELF_VENT_GAP: float = 12.7  # 1/2" ventilation gap subtracted from depth at the back
TOEKICK_H: float = 101.6      # 4" — toe-kick height (integrated into sides, see toe_kick())

# Parity mirror of southbrook_estimating.mrp_bom.TOEKICK_FAMILIES; the G1
# gate (test_bom_contents.test_constants_parity) enforces the two sets are
# identical.
#
# `drawer` is deliberately ABSENT. It was present until 52339aa
# (2026-06-30, "TOEKICK 'drawer' removal … per Q8 spec"), which removed it
# from the estimating side on purpose. A later round-2 branch re-added it
# here claiming it aligned with estimating — it did not, and re-adding it
# both broke the parity gate and silently changed drawer-bank cut lists
# (toe_kick() below emits a panel for every family in this set). Do not
# re-add without a Q8 spec change on BOTH sides.
TOEKICK_FAMILIES = frozenset({"base", "sink", "tall", "vanity"})

PanelCut = Tuple[float, float, float]

# The 8 keys a `constants` override dict may carry, and the module constant
# each falls back to when the key is absent (or `constants` is None). Names
# match southbrook.cut.spec.constants_dict()'s output 1:1 by construction —
# see the module docstring above.
_CONSTANT_DEFAULTS: Dict[str, float] = {
    "box_th": BOX_TH,
    "back_th": BACK_TH,
    "rabbet": RABBET,
    "door_th": DOOR_TH,
    "door_reveal": DOOR_REVEAL,
    "shelf_tol": SHELF_TOL,
    "shelf_vent_gap": SHELF_VENT_GAP,
    "toekick_h": TOEKICK_H,
}


def _resolve_constants(constants: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Merge an optional override dict onto the module-level defaults.

    ``constants=None`` (the default) returns the module constants
    unchanged — this is what keeps every call site byte-identical unless
    it explicitly opts in. A partial dict is safe: any of the 8 keys it
    omits falls back to the module constant for that key rather than
    raising.
    """
    if not constants:
        return dict(_CONSTANT_DEFAULTS)
    return {key: constants.get(key, default)
            for key, default in _CONSTANT_DEFAULTS.items()}


# ---------------------------------------------------------------------------
# 7 canonical panel formulas
# ---------------------------------------------------------------------------
def side(
    height_mm: float,
    depth_mm: float,
    constants: Optional[Dict[str, float]] = None,
) -> PanelCut:
    """Side panel (L or R) — full-height frameless side.

    The toe-kick (for families that have one) is integrated as a routed notch
    in the front-bottom corner of this panel — there is NO separate toe-kick
    cut. See toe_kick() for the metadata descriptor.
    """
    c = _resolve_constants(constants)
    return (height_mm, depth_mm, c["box_th"])


def top(
    width_mm: float,
    depth_mm: float,
    constants: Optional[Dict[str, float]] = None,
) -> PanelCut:
    """Top panel — captures between the sides (frameless euro)."""
    c = _resolve_constants(constants)
    inside_width = width_mm - 2 * c["box_th"]
    return (inside_width, depth_mm, c["box_th"])


def bottom(
    width_mm: float,
    depth_mm: float,
    constants: Optional[Dict[str, float]] = None,
) -> PanelCut:
    """Bottom panel — captures between the sides (frameless euro)."""
    c = _resolve_constants(constants)
    inside_width = width_mm - 2 * c["box_th"]
    return (inside_width, depth_mm, c["box_th"])


def back(
    width_mm: float,
    height_mm: float,
    constants: Optional[Dict[str, float]] = None,
) -> PanelCut:
    """Back panel — captures into rabbet on sides/top/bottom."""
    c = _resolve_constants(constants)
    inside_width = width_mm - 2 * c["box_th"]
    length = inside_width + 2 * c["rabbet"]
    width = (height_mm - 2 * c["box_th"]) + 2 * c["rabbet"]
    return (length, width, c["back_th"])


def adjustable_shelf(
    width_mm: float,
    depth_mm: float,
    constants: Optional[Dict[str, float]] = None,
) -> PanelCut:
    """One adjustable shelf — quantity comes from shelf_count(height)."""
    c = _resolve_constants(constants)
    inside_width = width_mm - 2 * c["box_th"]
    length = inside_width - c["shelf_tol"]
    width = depth_mm - c["back_th"] - c["rabbet"] - c["shelf_vent_gap"]
    return (length, width, c["box_th"])


def shelf_count(height_mm: float) -> int:
    """Shelf quantity from cabinet height — NF14 heuristic.

    No geometric constant feeds this heuristic (pure height thresholds),
    so there is nothing for a `constants` override to change here.
    """
    if height_mm <= 600:
        return 1
    if height_mm <= 900:
        return 2
    return 3


def door(
    width_mm: float,
    height_mm: float,
    door_count: int,
    constants: Optional[Dict[str, float]] = None,
) -> Optional[PanelCut]:
    """Door (or drawer-front) panel sized from door_count and uniform reveal.

    Returns None if door_count is 0 (e.g. open shelving).
    """
    c = _resolve_constants(constants)
    door_reveal = c["door_reveal"]
    door_th = c["door_th"]
    if door_count == 1:
        return (height_mm - 2 * door_reveal, width_mm - 2 * door_reveal, door_th)
    if door_count == 2:
        return (height_mm - 2 * door_reveal, (width_mm - 3 * door_reveal) / 2, door_th)
    return None


def toe_kick(
    family: str,
    width_mm: float,
    constants: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Toe-kick descriptor.

    Phase 1/2 construction: the toe-kick is INTEGRATED into the side panels
    via a notch routed into the front-bottom corner. There is NO separate
    toe-kick panel cut. This function returns metadata describing the
    integration (height + applicability), NOT a panel cut tuple, so callers
    do not mistakenly emit a separate piece on the cut list.

    Returns None for families without a toe-kick (e.g. wall).
    """
    if family not in TOEKICK_FAMILIES:
        return None
    c = _resolve_constants(constants)
    return {
        "integrated_into_sides": True,
        "height_mm": c["toekick_h"],
        "thickness_mm": c["box_th"],
    }


# ---------------------------------------------------------------------------
# Convenience: full panel cut list for a parametric carcass
# ---------------------------------------------------------------------------
def panel_cut_list(
    width_mm: float,
    height_mm: float,
    depth_mm: float,
    family: str = "base",
    door_count: int = 1,
    constants: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Complete 7-panel descriptor for a parametric carcass.

    Mirrors southbrook_estimating/models/mrp_bom.py::_compute_panel_dimensions
    so the BoM-contents assertion suite (Module 2 / G1) can compare the two
    for byte-identical parity. Any drift between the two is a manufacturing-
    safety regression by construction.

    ``constants`` (optional): see the module docstring's "Optional
    constants override" section. Omitted/None reproduces today's exact
    output (module-level defaults); the standalone FreeCAD bridge never
    passes it. When provided, it is threaded into every per-panel formula
    below so the whole cut list is computed from the same override.
    """
    shelves = shelf_count(height_mm)
    return {
        "side_L": side(height_mm, depth_mm, constants=constants),
        "side_R": side(height_mm, depth_mm, constants=constants),
        "top": top(width_mm, depth_mm, constants=constants),
        "bottom": bottom(width_mm, depth_mm, constants=constants),
        "back": back(width_mm, height_mm, constants=constants),
        "adjustable_shelf": (
            adjustable_shelf(width_mm, depth_mm, constants=constants)
            if shelves > 0 else None
        ),
        "shelf_count": shelves,
        "door": door(width_mm, height_mm, door_count, constants=constants),
        "door_count": door_count,
        "toe_kick": toe_kick(family, width_mm, constants=constants),
    }
