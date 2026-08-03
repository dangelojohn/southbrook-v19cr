import logging
import math
import re

from odoo import http
from odoo.http import request

# PR2 — the pure layout engine's WALLS tuple is the single source of
# truth for valid wall identifiers server-side too. Imported the same
# way models/kitchen_design.py:11 does.
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine
# C2 — same exception the model's action_auto_arrange raises when the
# corner-resolution engine can't physically fit the cabinets. Imported
# the same way kitchen_layout_engine itself is above (models/
# kitchen_design.py never needs this one directly — it lets the
# savepoint rollback speak for itself — but the controller surfaces a
# human-readable warning to the client instead of a bare 500).
from odoo.addons.southbrook_estimating.models.kitchen_layout_engine import (
    LayoutCapacityExceeded,
)
# Task 9b (review round 2) — the debris/sentinel threshold constant now
# has exactly ONE definition, on the model (kitchen_design.py), shared
# by this guard, the migration, and all three RESET-TO-CANONICAL
# reactivation sites (the other two live on the model itself as
# SouthbrookKitchenDesignLine._is_debris_signature; this file's own
# elif-branch reset — save_design's down-shift path — calls that same
# method directly on the line recordset, so only the raw threshold
# VALUE needs importing here for the write-path guard's own
# raw-JSON-value check below, which runs before any line record exists).
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    DEBRIS_THRESHOLD_IN as _DEBRIS_THRESHOLD_IN,
)
# Task 11 (corner-remediation 2026-07-30, SBK-024) — the ONE helper that
# resolves a product.product variant's actual placed width (its own Width
# PTAV when present, else the template's shared southbrook_width_in).
# Imported the same way DEBRIS_THRESHOLD_IN is above — single definition
# on the model, shared here.
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    placed_width_in,
)
# Task 12 (corner-remediation 2026-07-30, SBK-021/022) — the ONE
# definition of the sane room-dimension range (see that module's own
# comment for the full rationale). save_design's pre-check below
# duplicates these bounds into a readable {error, error_code} response
# so an out-of-range room is rejected BEFORE any create/write, never
# relying on the model's own @api.constrains raising an unhandled
# ValidationError.
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    ROOM_WIDTH_MIN_IN, ROOM_WIDTH_MAX_IN,
    ROOM_DEPTH_MIN_IN, ROOM_DEPTH_MAX_IN,
    ROOM_HEIGHT_MIN_IN, ROOM_HEIGHT_MAX_IN,
)
# Task 14 review, finding 2 (SBK-026 fix-round) — the ONE clamp for
# counter_thickness_in, shared by /layout and save_design below (see
# that module's own comment for why this is a CLAMP, not a
# ROOM_*_MIN/MAX_IN-style reject: no resource-consumption blast radius,
# just a geometry input with a sane physical range).
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    clamp_counter_thickness_in,
)
# Touch editing v1 (2026-08-02 spec §4 item 3 / addendum item 3) — the
# ONE validation rule for the optional touch_stats health-counter dict
# save_design accepts, shared with a unit test that pins the clamp/drop
# behaviour directly against the model layer (same convention as
# clamp_counter_thickness_in above).
from odoo.addons.southbrook_kitchen_3d_configurator.models.kitchen_design import (
    normalize_touch_stats,
)

_logger = logging.getLogger(__name__)

# Hard bounds for a posted cabinet width, in inches. Deliberately far wider
# than the UI's own 6-48" width control: these are not a UX constraint, they
# are the sanity floor/ceiling on a value that DIVIDES INTO THE PRICE (see
# the C3 pro-rata block in save_design). Their only job is to stop an
# unbounded payload from driving a line's price to ~$0 or negative.
_SBK_MIN_WIDTH_IN = 3.0
_SBK_MAX_WIDTH_IN = 96.0

# Task 9b (corner-remediation, 2026-07-30) — server-side write-path guard
# against the client's 1e6 sort-key sentinel (kitchen_configurator.js
# `_addCabinetFromProduct`'s `sortX` fallback) leaking into a save_design
# payload when packRow is skipped in multiWall mode (Task 9 report,
# "producer" section). Threshold value imported from kitchen_design.py's
# `DEBRIS_THRESHOLD_IN` (review round 2 — single source of truth; see
# that constant's docstring for the full list of shared call sites).


# Task 12 (corner-remediation 2026-07-30, SBK-021/022) — hard cap on how
# many lines a single /layout call will generate. Live evidence:
# room_width_in=99999 with a 24in module -> n≈4166 base+wall PAIRS
# (8,332 total lines) built in one unbounded Python loop, returned in
# one JSON-RPC payload, then persisted verbatim by save_design (which
# had no length/count guard of its own — see save_design's own
# ROOM_OUT_OF_BOUNDS check, which closes the room-dims half of this;
# this constant closes the "even a merely-large-but-in-range room
# shouldn't flood the client with thousands of items" half).
# Reserves 2 slots for the largest possible filler-strategy output
# ("split" emits 2 pieces) so the true post-filler item count never
# exceeds _MAX_AUTOFILL_LINES — hence filler creation is skipped
# entirely once capped (see `layout()` below): a filler bridging a
# multi-thousand-inch leftover run would just be the same "phantom
# runaway cost" bug in a different shape.
_MAX_AUTOFILL_LINES = 120
_MAX_AUTOFILL_PAIRS = (_MAX_AUTOFILL_LINES - 2) // 2


def _is_sentinel_position(value):
    """True if `value` (an incoming x/z position, still raw JSON) is at
    or beyond the debris-sentinel threshold in either direction. Any
    non-numeric input is NOT a sentinel here — it fails elsewhere
    (Line.create's own field validation) and is not this guard's job."""
    try:
        return abs(float(value)) >= _DEBRIS_THRESHOLD_IN
    except (TypeError, ValueError):
        return False


# P0 fix (silent-drop truthful-response, 2026-08-02) — prod evidence:
# design 160 held 26 configurator lines, six of them at
# x_position_in=1,000,000 (the sentinel _is_sentinel_position catches),
# priced $0, excluded from total_cabinets/estimated_price — invisible
# debris. The write-path guard above already stops a *sentinel* from
# ever landing as an active/priced line, but a client bug can also send
# a position that is merely wildly wrong (not 1,000,000, just nowhere
# near the room) — nothing before this fix caught that shape at all,
# and save_design's response never told the caller anything was wrong
# either way (see the two hardcoded `"unplaced_count": 0` literals in
# `layout()` above, which is a *different*, always-server-computed
# endpoint where 0 happens to be true — this fix is about save_design,
# which accepts untrusted client positions).
#
# Scoped to wall == "back" items ONLY. PR4 (see save_design's own
# comment further down, at the `wall_lines` loop) already establishes
# that a non-back-wall item's incoming x/z is a PROVISIONAL client
# guess — `_addCabinetFromProduct` only computes a correct position for
# the back wall; every other wall's line is deliberately overwritten
# by `design._place_lines_on_wall`'s real engine pose later in this
# same request. Bounds-checking that provisional guess here would
# reject legitimate multi-wall saves before the engine ever gets a
# chance to fix them (see test_sentinel_write_guard.py's wall="left"
# x=999 fixtures in a 96in-deep room — those must keep saving).
#
# Deliberately NOT persisted via the sentinel guard's archived +
# is_unresolved convention: that convention's "survives every
# RESET-TO-CANONICAL revival site untouched" guarantee comes from
# SouthbrookKitchenDesignLine._is_debris_signature(), which keys
# specifically off `_DEBRIS_THRESHOLD_IN` (9999in) on x or z — an
# out-of-bounds-but-under-threshold position would NOT match that
# signature, so the very next auto-arrange/down-shift reset would
# silently REACTIVATE it with a freshly computed pose, reintroducing
# the exact "never really placed, cheerfully reported as placed" bug
# this fix exists to close. Refused outright instead (no line
# created/updated for it at all) and reported in the `dropped` list
# built below — this file may not alter the model, so inventing a
# second on-the-wire "unplaced" signature here is not an option.
_ROOM_BOUNDS_MARGIN_IN = 24.0  # generous slack for corner/panel overhang geometry


def _out_of_room_bounds_reason(x_val, z_val, wall, room_w, room_d):
    """Short human reason string if a BACK-WALL item's x/z falls
    outside [-_ROOM_BOUNDS_MARGIN_IN, room_dim + _ROOM_BOUNDS_MARGIN_IN]
    on either axis, else None. Never called for a value already caught
    by `_is_sentinel_position` (that check takes priority — see the
    call site) and never applied to a non-back-wall item (see the
    module comment above for why)."""
    if (wall or "back") != "back":
        return None
    try:
        xf, zf = float(x_val or 0), float(z_val or 0)
    except (TypeError, ValueError):
        return None
    m = _ROOM_BOUNDS_MARGIN_IN
    if xf < -m or xf > (room_w or 0) + m:
        return 'x position %.1f" is outside the %g"-wide room' % (xf, room_w or 0)
    if zf < -m or zf > (room_d or 0) + m:
        return 'z position %.1f" is outside the %g"-deep room' % (zf, room_d or 0)
    return None


# Fix (design-naming, prod fact 2026-07-31) — a kitchen design's `name`
# is a plain Char, auto-set ONCE at first save by
# SouthbrookKitchenConfiguratorController._auto_name (`if not
# design_id` in save_design below). Every later write — including a
# plain room resize — just re-persists whatever `name` the caller
# sent, which for a never-renamed design is the client's cached copy
# of the now-stale auto name. Verified live: design 103 was named
# "Walk-in - 12x30 - 2026-07-30" while its ACTUAL room dims were
# 54x54 — 4 of the last 11 designs (98/100/101/103) showed the same
# drift.
#
# AUTO_NAME_FMT / auto_name_str() / AUTO_NAME_RE / is_auto_generated_
# name() below are the ONE definition of what an auto name looks like
# and how it's built. `_auto_name()` (the controller method) and the
# resize-regeneration check in save_design both call these; so does
# the one-time backfill migration
# (migrations/19.0.5.35.1/post-migrate.py, imported directly from this
# module). There is no second, hand-copied pattern anywhere to drift
# out of sync.
AUTO_NAME_FMT = "%s - %dx%d - %s"

# One (name, regex-fragment) pair per %s/%d placeholder in
# AUTO_NAME_FMT, IN ORDER: partner display name, room width, room
# depth, ISO date.
_AUTO_NAME_PLACEHOLDERS = (
    ("partner", r".+"),
    ("width",   r"\d+"),
    ("depth",   r"\d+"),
    ("date",    r"\d{4}-\d{2}-\d{2}"),
)


def _regex_from_printf_fmt(fmt, named_placeholders):
    """Turn a `%s`/`%d`-only printf-style format string into a fully
    anchored regex with one NAMED capture group per placeholder, in
    order. Every literal character in `fmt` (the ` - ` separators, the
    `x` between width/depth) is pulled straight out of `fmt` itself
    and regex-escaped — so if AUTO_NAME_FMT's literal text ever
    changes, this regex changes with it automatically. There is
    nothing here to hand-maintain in step with the generator.
    """
    pieces, last, i = [], 0, 0
    for m in re.finditer(r"%[sd]", fmt):
        pieces.append(re.escape(fmt[last:m.start()]))
        name, pattern = named_placeholders[i]
        pieces.append("(?P<%s>%s)" % (name, pattern))
        i += 1
        last = m.end()
    pieces.append(re.escape(fmt[last:]))
    return "^" + "".join(pieces) + "$"


AUTO_NAME_RE = re.compile(
    _regex_from_printf_fmt(AUTO_NAME_FMT, _AUTO_NAME_PLACEHOLDERS))


def auto_name_str(partner_name, room_w, room_d, date_str):
    """Build the exact string `_auto_name()` persists at first save:
    "<partner-or-Walk-in> - <width>x<depth> - <YYYY-MM-DD>". Pulled
    into a free function so both the controller method AND the
    backfill migration format a design's auto name through the
    IDENTICAL code path — never two independently-typed copies of the
    same '%s - %dx%d - %s' string.
    """
    return AUTO_NAME_FMT % (
        partner_name or "Walk-in", int(room_w or 0), int(room_d or 0),
        date_str,
    )


def is_auto_generated_name(name):
    """True when `name` still has the exact shape `_auto_name()` /
    `auto_name_str()` produce — i.e. nothing has hand-edited it since
    it was set (or last regenerated). A name that fails this check —
    including any manual rename, even one that happens to reuse the
    ` - ` separators in some other way — is NEVER touched by the
    resize-regeneration logic in save_design or by the backfill
    migration. Once a design's name fails this check, it permanently
    opts out of auto-regeneration from that point on.
    """
    return bool(name) and AUTO_NAME_RE.match(name) is not None


class SouthbrookKitchenConfiguratorController(http.Controller):

    # ── Products catalogue ───────────────────────────────────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/products",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def products(self, partner_id=False):
        """Return all cabinet products available to the configurator.

        D3 — when partner_id is supplied, prices reflect the partner's
        channel pricelist (Dealer/Tradesperson/etc.). Also returns the
        resolved channel metadata so the UI can show a "Channel:
        Dealer -50%" badge alongside the catalog. Without partner_id
        falls back to product.lst_price (retail).
        """
        # v19 ORM rejects dotted M2O field references in order= clauses
        # ("Invalid field property '<name>' on <model>.<m2o>") even
        # though it accepts them in domains. Sort by sortable column-
        # local fields here; the JS layer groups by cabinet_type on
        # display.
        products = request.env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
            ("sale_ok", "=", True),
        ], order="default_code, name")
        partner = self._browse_partner(partner_id)
        pricelist = self._resolve_pricelist(partner)
        return {
            "channel": self._channel_meta(partner, pricelist),
            "products": [self._product_payload(p, pricelist, partner) for p in products],
            # Touch editing v1 (2026-08-02 spec §4 item 2) — this is the
            # backend configurator's one unconditional boot RPC
            # (kitchen_configurator.js's onWillStart always calls
            # _loadProducts(), new-design or existing-design alike,
            # unlike /layout and /load_design_lines which are
            # branch-specific), so it's the equivalent of qr_scan.py's
            # `whoami` for this surface: a single always-fetched call
            # the per-surface kill switch can ride on.
            "touch_enabled": request.env["southbrook.kitchen.design"]
                ._sb_touch_enabled("backend"),
        }

    # ── Layout calculation ───────────────────────────────────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/layout",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    # E2E round 3 — these kwarg defaults were the last surviving source of the
    # 12in x 24in room: a one-foot-wide, two-foot-deep space in which not one
    # 24in cabinet fits. The client always sends explicit values, so this is a
    # defensive default rather than a live path — but "defensive" that hands
    # back an unusable room is not defensive. `/user_defaults` was corrected to
    # 144/120/96 last round; these now agree with it, so every entry point
    # falls back to the same sane room instead of two different wrong answers.
    def layout(self, room_width_in=144, room_depth_in=120, room_height_in=96,
               partner_id=False, filler_strategy="split",
               wall_cab_top_alignment="fixed_gap", soffit_height_in=0.0,
               counter_thickness_in=1.5):
        """
        Compute the cabinet fill for the given room dimensions.

        Returns a dict with:
          room        — echoed dimensions
          items       — list of cabinet placement records
          summary     — counts, totals, remainder
          channel     — pricelist metadata (D3)
          error       — non-empty string if no products configured

        D3 — partner_id (optional) drives channel-pricelist resolution
        so per-item prices and the summary total reflect Dealer /
        Tradesperson / KD / etc. discounts. Falls back to retail when
        no partner is supplied.
        """
        env = request.env
        base = self._first_product(env, "base")
        wall = self._first_product(env, "wall")

        partner = self._browse_partner(partner_id)
        pricelist = self._resolve_pricelist(partner)

        if not base or not wall:
            missing = []
            if not base: missing.append("Base Cabinet")
            if not wall: missing.append("Wall Cabinet")
            return {
                "error": (
                    "No %s product configured. "
                    "Tag a saleable product with 'Southbrook Cabinet' + "
                    "the matching cabinet type to populate the catalog."
                ) % " / ".join(missing),
                # D10 — structured error code + CTA so the UI can render
                # an onboarding nudge with a real button instead of a
                # bare error string.
                "error_code": "NO_CABINETS",
                "error_cta":  {
                    "label": "Open Cabinet Products",
                    "action": {
                        "type":      "ir.actions.act_window",
                        "name":      "Southbrook Cabinet Products",
                        "res_model": "product.template",
                        "view_mode": "list,form",
                        "views":     [(False, "list"), (False, "form")],
                        "domain":    [("southbrook_is_cabinet", "=", True)],
                        "target":    "current",
                        "context":   {
                            "default_southbrook_is_cabinet": True,
                            "default_sale_ok":               True,
                        },
                    },
                },
                "items": [],
                # Task 7 (SBK-033) — same shape as the success branch's
                # summary below (and southbrook.kitchen.design.
                # _get_counter_summary()): every bucket present, even
                # though nothing was generated. Prevents a NO_CABINETS
                # boot from handing the client a partial summary object
                # (missing corner_count etc.) that later renders as
                # blank instead of "0".
                "summary": {"base_count": 0, "wall_count": 0,
                             "tall_count": 0, "corner_count": 0,
                             "filler_count": 0, "panel_count": 0,
                             "appliance_count": 0, "unplaced_count": 0,
                             "total": 0, "price": 0.0,
                             "cabinet_price": 0.0, "filler_price": 0.0,
                             "remainder_in": 0.0},
                "room": {"width_in": float(room_width_in),
                         "depth_in": float(room_depth_in),
                         "height_in": float(room_height_in)},
                "channel":  self._channel_meta(partner, pricelist),
                "warnings": [],
            }

        # Task 11 (SBK-024) — the resolved base variant's own Width.
        module_w = placed_width_in(base) or 24.0
        rw = float(room_width_in)
        n_uncapped = max(0, int(math.floor(rw / module_w)))
        # Task 12 (corner-remediation 2026-07-30, SBK-021/022) — hard cap
        # so a huge (or merely large) room can never flood this response
        # with thousands of lines (see _MAX_AUTOFILL_PAIRS's own comment
        # for the live-evidence numbers). Deliberately NOT silent: `capped`
        # drives an explicit warning appended below, and filler creation
        # is skipped entirely when capped (a filler bridging a multi-
        # thousand-inch leftover run would just reinvent the same
        # phantom-cost bug in a different shape).
        capped = n_uncapped > _MAX_AUTOFILL_PAIRS
        n = min(n_uncapped, _MAX_AUTOFILL_PAIRS)
        # Task 10 (SBK-023) — remainder only has meaning in the context of
        # a real cabinet run; an undersized/empty room (n==0, e.g. the
        # client's own 12in minimum room width vs. a 24in module) has no
        # run to complete, so it must not synthesize one. Pre-fix this
        # computed `remainder = rw` unconditionally for n==0, and the
        # filler-creation branch below then priced + placed that as a
        # real (phantom) filler line on a room with ZERO real cabinets —
        # the confirmed "[FP3] +$140 filler on an empty 12in room" /
        # "$70 persisted, zero cabinets" findings (V5-partc §SBK-023).
        # For n >= 1 this is UNCHANGED: `rw - n*module_w` is the honest
        # geometric gap and stays authoritative for every filler_strategy
        # (including "scribe" — remainder_in there is by design the true
        # physical gap the installer scribes on site, decoupled from
        # filler $, same pattern as "Est. excludes filler by design").
        # When capped, this is still the honest geometric gap (now a
        # large one, by construction) — just never turned into a filler.
        remainder = (rw - n * module_w) if n > 0 else 0.0

        # Cabinet dimensions for placement
        base_h = base.product_tmpl_id.southbrook_height_in or 34.5
        wall_h = wall.product_tmpl_id.southbrook_height_in or 30.0
        # SBK-026 — was a bare local constant; now the caller-supplied
        # design-level setting (kitchen.design.counter_thickness_in),
        # same formula southbrook.kitchen.design._wall_cab_top_geometry
        # uses for the persisted arrange path, so a new-design preview
        # here and a later regenerate never disagree.
        # Task 14 review, finding 2 — clamped (was `float(x or 1.5)`,
        # which let ANY negative value through unchanged since only
        # 0/None/False are falsy in Python — a negative ctr_t would
        # have SUBTRACTED from the industry-minimum floor instead of
        # padding it).
        ctr_t  = clamp_counter_thickness_in(counter_thickness_in)  # countertop thickness (in)
        gap    = 18.0   # clearance between counter surface and wall cab bottom
        min_z  = base_h + ctr_t + gap   # bottom of wall cabinet (industry minimum)

        # D8 — Wall-cabinet Z computed from chosen alignment mode.
        # Never drops the bottom-of-cab below the industry minimum
        # (18" above counter); the alignment mode only raises it.
        alignment = (wall_cab_top_alignment or "fixed_gap").lower()
        soffit_h  = float(soffit_height_in or 0)
        if alignment == "to_ceiling":
            wall_z = max(min_z, float(room_height_in) - wall_h)
        elif alignment == "to_soffit" and soffit_h > 0:
            wall_z = max(min_z, soffit_h - wall_h)
        else:
            alignment = "fixed_gap"
            wall_z = min_z

        # D8 — Validation: wall cab top must not exceed the effective
        # ceiling (soffit-bottom when 'to_soffit', otherwise ceiling).
        effective_ceiling = (soffit_h
            if (alignment == "to_soffit" and soffit_h > 0)
            else float(room_height_in))
        wall_top = wall_z + wall_h
        warnings = []
        if wall_top > effective_ceiling + 0.01:
            warnings.append({
                "code":     "WALL_CAB_EXCEEDS_CEILING",
                "severity": "blocking",
                "message":  (
                    "Wall cabinet top reaches %.1f\" but %s sits at %.1f\". "
                    "Either lower the wall cabinets, drop the alignment "
                    "mode, or raise the %s."
                ) % (
                    wall_top,
                    "soffit" if alignment == "to_soffit" else "ceiling",
                    effective_ceiling,
                    "soffit" if alignment == "to_soffit" else "ceiling",
                ),
            })
        if capped:
            # Task 12 (corner-remediation 2026-07-30, SBK-021/022) —
            # explicit, non-silent notice that the auto-fill stopped
            # short of covering the whole room. `severity: "warning"`
            # (non-blocking — the truncated fill is still usable) mirrors
            # the `o_sbk_warning_warning` CSS class kitchen_configurator.js
            # already renders for this shape (state.warnings, D8).
            warnings.append({
                "code":     "ROOM_TOO_LARGE_FOR_AUTOFILL",
                "severity": "warning",
                "message":  (
                    "Room too large to auto-fill: only the first %d "
                    "cabinet(s) (%.0f in of %.0f in total width) were "
                    "generated. Add cabinets manually beyond this point, "
                    "or resize the room in stages."
                ) % (n, n * module_w, rw),
            })

        items = []
        for i in range(n):
            x = i * module_w
            # PR3.0 — y/z field-semantics migration: y_position_in is the
            # canonical mount-height/elevation field, z_position_in is the
            # depth axis per COORDINATE_CONTRACT.md. Base cabinets are
            # floor-flush (y=0, z=0, unchanged). Wall cabinets carry their
            # D8-computed mount height (`wall_z`) in y now, not z; z stays
            # 0 (flush to the back wall) — this was the "z-as-height"
            # convention flagged as the riskiest finding in the PR3
            # coordinate-contract matrix.
            items.append(self._layout_item(base, i, x, 0.0,    0.0, pricelist, partner))
            items.append(self._layout_item(wall, i, x, wall_z, 0.0, pricelist, partner))

        # D7 — Smart filler placement. Honors the chosen filler_strategy:
        #  split: half-width filler at each end (default; pushes bases by half)
        #  left:  single filler at x=0 (push all bases right by remainder)
        #  right: single filler at x = n * module_w (right end)
        #  scribe: no filler (carpenter scribes end cabinet on site)
        filler_strategy = (filler_strategy or "split").lower()
        if filler_strategy not in ("split", "left", "right", "scribe"):
            filler_strategy = "split"
        # Task 12 — `not capped`: once the auto-fill has stopped short of
        # the room's true width (see ROOM_TOO_LARGE_FOR_AUTOFILL above),
        # `remainder` is the (large, honest) leftover run beyond the cap —
        # NOT a small end-of-run gap a filler panel is meant to bridge.
        # Synthesizing one here would just be the same phantom/runaway-
        # cost bug (V5-partc §SBK-023) in a new shape.
        if remainder >= 1.0 and filler_strategy != "scribe" and not capped:
            if filler_strategy == "split":
                half = remainder / 2.0
                # Shift bases + walls right by `half` so the left filler
                # sits at x=0 cleanly.
                for it in items:
                    if it["cabinet_type"] in ("base", "wall"):
                        it["x_position_in"] = it["x_position_in"] + half
                lf = self._make_filler(env, half, 0.0,
                                        "filler-L", pricelist, partner)
                rf = self._make_filler(env, half, n * module_w + half,
                                        "filler-R", pricelist, partner)
                if lf: items.append(lf)
                if rf: items.append(rf)
            elif filler_strategy == "left":
                for it in items:
                    if it["cabinet_type"] in ("base", "wall"):
                        it["x_position_in"] = it["x_position_in"] + remainder
                lf = self._make_filler(env, remainder, 0.0,
                                        "filler-L", pricelist, partner)
                if lf: items.append(lf)
            else:   # right
                rf = self._make_filler(env, remainder, n * module_w,
                                        "filler-R", pricelist, partner)
                if rf: items.append(rf)

        cabinet_price = sum(it["price"] for it in items
                             if it["cabinet_type"] not in ("filler",))
        filler_price  = sum(it["price"] for it in items
                             if it["cabinet_type"] == "filler")
        # Task 7 (SBK-033) — this generator only ever produces base/wall/
        # filler items (no corner/tall/panel/appliance slot exists in the
        # straight-fill algorithm above), but the summary shape must still
        # match southbrook.kitchen.design._get_counter_summary() exactly
        # so the client's sidebar/header can render from ONE consistent
        # object regardless of which endpoint populated it. `total` is
        # every bucket summed (INCLUDING fillers), unlike the legacy
        # per-endpoint `n * 2` this replaces (base+wall only).
        filler_count = sum(1 for it in items if it["cabinet_type"] == "filler")

        return {
            "room": {
                "width_in":  rw,
                "depth_in":  float(room_depth_in),
                "height_in": float(room_height_in),
            },
            "items": items,
            "summary": {
                "base_count":      n,
                "wall_count":      n,
                "tall_count":      0,
                "corner_count":    0,
                "filler_count":    filler_count,
                "panel_count":     0,
                "appliance_count": 0,
                "unplaced_count":  0,
                "total":           n + n + filler_count,
                # Backward compat: 'price' kept as cabinet total. The
                # UI can now show filler as a separate breakdown row.
                "price":           cabinet_price,
                "cabinet_price":   cabinet_price,
                "filler_price":    filler_price,
                "remainder_in":    round(remainder, 2),
                "filler_strategy": filler_strategy,
                "snap_hint":       self._snap_hint(rw, module_w),
            },
            "channel":  self._channel_meta(partner, pricelist),
            "warnings": warnings,
            "error":    "",
        }

    # ─── D7 — filler helpers ────────────────────────────────────────────────────
    def _make_filler(self, env, width_in, x, key, pricelist, partner):
        """Build a single filler item at position x, width prorated."""
        filler = self._first_product(env, "filler")
        if not filler:
            return None
        fp = self._product_payload(filler, pricelist, partner)
        # Prorate price by actual filler width vs the catalog standard
        # width. Avoids over-charging for a 0.5" filler at the price of
        # a 6" panel.
        std_w = float(fp.get("width_in") or width_in or 1.0)
        if std_w <= 0:
            std_w = 1.0
        prorated = (fp.get("price") or 0.0) * (max(width_in, 0.5) / std_w)
        fp.update({
            "layout_key":    key,
            "x_position_in": x,
            "y_position_in": 0.0,
            "z_position_in": 0.0,
            "width_in":      width_in,
            "cabinet_type":  "filler",
            "price":         round(prorated, 2),
        })
        return fp

    def _snap_hint(self, rw, module_w):
        """When rw is 1-6 inches away from a module-clean total, suggest
        the nearest clean width so the rep can avoid filler altogether."""
        if not module_w or module_w <= 0:
            return None
        # Floor count
        n_floor = int(rw // module_w)
        clean_lo = n_floor * module_w
        clean_hi = (n_floor + 1) * module_w
        gap_lo = rw - clean_lo
        gap_hi = clean_hi - rw
        if gap_lo == 0:
            return None
        # Suggest hi (stretch) if rw is close enough to hi; else lo (shrink)
        if 0 < gap_hi <= 6.0:
            return {"target_width": clean_hi,
                    "delta":         round(gap_hi, 2),
                    "direction":     "expand"}
        if 0 < gap_lo <= 6.0:
            return {"target_width": clean_lo,
                    "delta":         round(-gap_lo, 2),
                    "direction":     "shrink"}
        return None

    # ── Save design ──────────────────────────────────────────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/save",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def save_design(self, name, room, items, partner_id=False, design_id=False,
                     filler_strategy=None, wall_cab_top_alignment=None,
                     soffit_height_in=None, counter_thickness_in=None,
                     touch_stats=None, allow_relayout=False):
        """
        Persist (or update) a KitchenDesign and its layout lines.

        Pass design_id to overwrite an existing record.

        D4 — auto-names new designs (no design_id, no real name) using
        the customer + room dims + date so the design tree view doesn't
        fill with "New Kitchen Design" collisions.

        Touch editing v1 (2026-08-02 spec §4 item 3 / addendum item 3)
        — `touch_stats` is an OPTIONAL dict riding this existing
        round-trip: `{gesture_rollbacks: int, gesture_abandons: int}`.
        Validated via normalize_touch_stats (ints, capped, unknown keys
        dropped) and logged one line per save when present — no new
        model/table; the log IS the v1 telemetry per the addendum's
        "minimal signal, not a telemetry build-out" verdict. Omitted or
        malformed input is ignored silently: the response shape below
        is unchanged either way, so existing clients that never send
        this key see a byte-identical contract (pinned by
        test_touch_health_counters.py).

        SBK-026 — filler_strategy / wall_cab_top_alignment /
        soffit_height_in / counter_thickness_in are the Room panel's
        settings the OWL client mutates live (kitchen_configurator.js
        state.fillerStrategy etc.) but, before this fix, never sent
        here — so they were never persisted onto the design record,
        meaning the NEXT auto-arrange/resize (which reads these fields
        off the record, not off any client session) silently reverted
        to the model defaults. `None` (the default) means "field not
        sent" — leaves the persisted value untouched, so a caller that
        only cares about room/items (e.g. an older client build, or a
        test) never clobbers these.

        P0 fix (drag-revert / undo-corruption, 2026-08-02) —
        `allow_relayout` is an OPTIONAL, opt-IN kwarg (default False)
        that lets a caller explicitly ask this save to run the corner-
        resolution auto-arrange (see the `pinned`-gating comment further
        down, at the `if len(walls_used) >= 2:` block) even though the
        design already has a manually-positioned line. Omitted, it
        changes nothing for any existing caller: a design with no
        pinned line still auto-arranges exactly as before.
        """
        # PR2 — validate every incoming item's `wall` BEFORE any
        # create/write happens, so a bad payload never partially
        # writes (validate-all-items-first). `wall` is a layout-domain
        # input the pure kitchen_layout_engine consumes (P0.2,
        # 2026-07-11) — an unrecognised value here would otherwise
        # silently fall back to the model field's own "back" default,
        # masking a client bug. Same error-dict shape as the /layout
        # endpoint's NO_CABINETS response.
        for item in items or []:
            wall = item.get("wall") or "back"
            if wall not in kitchen_layout_engine.WALLS:
                _logger.warning(
                    "save_design: rejected payload — invalid wall=%r "
                    "(item layout_key=%s); must be one of %s. No "
                    "design/lines were created or modified.",
                    wall, item.get("layout_key"), kitchen_layout_engine.WALLS,
                )
                return {
                    "error": "invalid_wall",
                    "detail": (
                        "wall=%r is not a recognised room wall (must "
                        "be one of %s)."
                    ) % (wall, ", ".join(kitchen_layout_engine.WALLS)),
                }

        Design = request.env["southbrook.kitchen.design"]
        room_w = room.get("width_in",  12)
        room_d = room.get("depth_in",  24)
        room_h = room.get("height_in", 96)
        # Task 12 (corner-remediation 2026-07-30, SBK-021/022) — reject an
        # out-of-range room BEFORE any create/write, with a readable
        # {error, error_code} response (same convention as invalid_wall/
        # NO_CABINETS/ROOM_TOO_SMALL above and below). Live evidence:
        # room_width_in=99999 -> 8,332 lines / ~$1.77M persisted, because
        # nothing on this path checked a sane upper bound. This early
        # return happens before Design.create/write, so a rejected save
        # persists NOTHING — not even a design record with the bad dims.
        # The model's own @api.constrains (kitchen_design.py
        # _check_room_bounds) is the belt-and-suspenders backstop for any
        # OTHER write path (e.g. /rearrange); this check exists so THIS
        # path gives a friendly message instead of a raw ValidationError.
        try:
            room_w_f, room_d_f, room_h_f = float(room_w), float(room_d), float(room_h)
        except (TypeError, ValueError):
            return {
                "error": "Room width/depth/height must be numbers.",
                "error_code": "ROOM_OUT_OF_BOUNDS",
            }
        # Fix-round (coordinator review, corner-remediation 2026-07-30) —
        # GRANDFATHER an unchanged legacy out-of-bounds dimension on an
        # EXISTING design, instead of rejecting it forever. Without this,
        # _saveDesign/_autoSave (kitchen_configurator.js) resend the
        # client's ENTIRE state.room on every save — so reopening a
        # legacy design whose room was persisted before this bounds
        # check existed (e.g. the 99999in live-evidence room) made EVERY
        # future save of that design fail, including a plain cabinet
        # edit that never touches the room at all, because the unchanged
        # legacy value is still out of range. Only a genuine ATTEMPT to
        # move a dimension to a (different) out-of-range value is
        # rejected; a resend of the EXACT value already on file for that
        # dimension is let through. Best-effort: any failure resolving
        # the existing record (not found, no read access, no design_id
        # at all — i.e. a brand-new design) simply means no
        # grandfathering applies, and the normal strict check runs.
        _existing_room = None
        if design_id:
            try:
                _existing = Design.browse(int(design_id or 0))
                if _existing.exists():
                    _existing_room = {
                        "width_in":  _existing.room_width_in,
                        "depth_in":  _existing.room_depth_in,
                        "height_in": _existing.room_height_in,
                        # Fix (design-naming) — the CURRENTLY persisted
                        # name, read alongside the dims above, purely
                        # so save_design's resize-regeneration check
                        # further below can tell "still the auto name"
                        # from "a human already renamed this" without
                        # a second browse.
                        "name":      _existing.name,
                    }
            except Exception:
                _existing_room = None

        def _grandfathered(value, key):
            if _existing_room is None:
                return False
            persisted = _existing_room.get(key)
            return persisted is not None and abs(value - persisted) < 1e-6

        _room_problems = []
        if not (ROOM_WIDTH_MIN_IN <= room_w_f <= ROOM_WIDTH_MAX_IN) \
                and not _grandfathered(room_w_f, "width_in"):
            _room_problems.append(
                'width %g" (must be %g"-%g")'
                % (room_w_f, ROOM_WIDTH_MIN_IN, ROOM_WIDTH_MAX_IN))
        if not (ROOM_DEPTH_MIN_IN <= room_d_f <= ROOM_DEPTH_MAX_IN) \
                and not _grandfathered(room_d_f, "depth_in"):
            _room_problems.append(
                'depth %g" (must be %g"-%g")'
                % (room_d_f, ROOM_DEPTH_MIN_IN, ROOM_DEPTH_MAX_IN))
        if not (ROOM_HEIGHT_MIN_IN <= room_h_f <= ROOM_HEIGHT_MAX_IN) \
                and not _grandfathered(room_h_f, "height_in"):
            _room_problems.append(
                'height %g" (must be %g"-%g")'
                % (room_h_f, ROOM_HEIGHT_MIN_IN, ROOM_HEIGHT_MAX_IN))
        if _room_problems:
            _logger.warning(
                "save_design: rejected payload — room dims out of bounds "
                "(%s). No design/lines were created or modified.",
                "; ".join(_room_problems))
            return {
                "error": "Room size is out of range: %s." % "; ".join(_room_problems),
                "error_code": "ROOM_OUT_OF_BOUNDS",
            }
        room_w, room_d, room_h = room_w_f, room_d_f, room_h_f
        # Resolve the partner through the ACL-checked helper (empty recordset
        # if the caller may not read it) and persist THAT validated id — never
        # the raw client partner_id, which a user could point at any partner to
        # drive channel-pricelist resolution off someone else's record.
        partner = self._browse_partner(partner_id)
        if not design_id and (not name or name.strip() in (
            "", "Kitchen Design", "New Kitchen Design", "Untitled Kitchen",
        )):
            name = self._auto_name(partner, room_w, room_d)
        vals = {
            "name":           name or "Kitchen Design",
            "partner_id":     partner.id if partner else False,
            # Task 21 (corner-remediation 2026-07-30, SBK-035) — state is
            # deliberately NOT set here. This dict feeds BOTH
            # Design.create() (new design) and design.write() (existing
            # design) BEFORE the item-processing loop further down even
            # runs, so forcing "configured" here — the old behaviour —
            # could persist state='configured' with zero cabinet lines
            # (empty save, or a payload where every product_id fails the
            # catalog ACL gate below). See the end of this method, which
            # sets state="configured" only once the line set for this
            # save is actually known, gated on cabinet_line_ids being
            # non-empty — same convention kitchen_design.py's own
            # state="configured" writers already use. The model's
            # `_check_configured_state_has_lines` constraint is the
            # backstop that would otherwise reject this dict outright.
        }
        if design_id and _existing_room is not None:
            # Fix-round — only include a room-dimension key when it is
            # an ACTUAL change from what is already persisted. This is
            # what makes the grandfathering above safe end-to-end: the
            # pre-check just let an UNCHANGED legacy out-of-bounds value
            # through, but design.write() below would otherwise still
            # hand that same value to the model's own @api.constrains
            # (kitchen_design.py _check_room_bounds), which fires
            # whenever a listed field is present in the vals dict
            # REGARDLESS of whether the value actually differs from
            # what is already stored — silently undoing the
            # grandfathering and raising anyway. Skipping unchanged
            # dims here closes that gap. A genuinely NEW value (already
            # bounds-checked above) is always included as normal.
            if abs(room_w - _existing_room["width_in"]) >= 1e-6:
                vals["room_width_in"] = room_w
            if abs(room_d - _existing_room["depth_in"]) >= 1e-6:
                vals["room_depth_in"] = room_d
            if abs(room_h - _existing_room["height_in"]) >= 1e-6:
                vals["room_height_in"] = room_h

            # Fix (design-naming) — regenerate the auto name when a
            # resize actually lands in `vals` above, so the design
            # tree/landing page never again shows a name that
            # contradicts the record's real dimensions (prod fact:
            # design 103 — see AUTO_NAME_FMT's docstring above).
            #
            # Regenerate ONLY when ALL of:
            #   (a) a room dimension is ACTUALLY changing this save
            #       ("room_width_in"/"room_depth_in" just landed in
            #       `vals` above — height alone never renames, the
            #       auto name doesn't encode height);
            #   (b) the caller's incoming name is IDENTICAL to what is
            #       currently persisted (vals["name"] ==
            #       _existing_room["name"]) — i.e. THIS request did
            #       not also try to change the name field. If it did,
            #       that is a deliberate, simultaneous human edit and
            #       must win outright even though a resize is also
            #       happening in the same call;
            #   (c) that (identical) name still matches the exact
            #       shape is_auto_generated_name() defines. Once a
            #       design's name fails (c) — i.e. it was ever
            #       hand-renamed — it permanently opts out of
            #       regeneration, even across future resizes.
            if ("room_width_in" in vals or "room_depth_in" in vals) \
                    and vals["name"] == _existing_room.get("name") \
                    and is_auto_generated_name(_existing_room.get("name")):
                vals["name"] = self._auto_name(partner, room_w, room_d)
        else:
            vals["room_width_in"]  = room_w
            vals["room_depth_in"]  = room_d
            vals["room_height_in"] = room_h

        # SBK-026 — round-trip the Room panel's Wall Cabinet Top
        # settings. Same "None = not sent, leave alone" contract as the
        # grandfathered room dims above: only write a key the caller
        # actually supplied, so a partial/older payload never resets an
        # already-configured design back to defaults. Validated the
        # same way /layout's own params are (unknown selection ->
        # fixed_gap; non-finite/negative numbers dropped) so a bad
        # client payload degrades safely instead of raising.
        if filler_strategy is not None:
            fs = str(filler_strategy or "split").lower()
            vals["filler_strategy"] = (
                fs if fs in ("split", "left", "right", "scribe") else "split")
        if wall_cab_top_alignment is not None:
            wcta = str(wall_cab_top_alignment or "fixed_gap").lower()
            vals["wall_cab_top_alignment"] = (
                wcta if wcta in ("fixed_gap", "to_ceiling", "to_soffit")
                else "fixed_gap")
        if soffit_height_in is not None:
            try:
                vals["soffit_height_in"] = max(0.0, float(soffit_height_in))
            except (TypeError, ValueError):
                pass
        if counter_thickness_in is not None:
            # Task 14 review, finding 2 — clamped into
            # [COUNTER_THICKNESS_MIN_IN, COUNTER_THICKNESS_MAX_IN]
            # (was `max(0.0, float(...))`, which let e.g. 999 through
            # untouched — no upper bound at all). Non-numeric input
            # clamps to the 1.5in default via the shared helper rather
            # than raising, matching this block's own "degrade safely"
            # convention for the other three fields above.
            vals["counter_thickness_in"] = clamp_counter_thickness_in(
                counter_thickness_in)

        # D9 — Non-destructive save. Update existing lines by
        # layout_key, create only new ones, unlink only those that
        # disappeared from the incoming set. Manual lines (added in
        # the backend form, origin='manual') are NEVER touched.
        Line = request.env["southbrook.kitchen.design.line"]
        if design_id:
            # IDOR guard mirroring save_position/delete_line: verify existence
            # + write access, degrade gracefully (no AccessError 500 oracle).
            design = Design.browse(int(design_id or 0))
            if not design.exists():
                return {"ok": False, "reason": "not_found"}
            try:
                design.check_access("write")
            except Exception:
                return {"ok": False, "reason": "forbidden"}
            design.write(vals)
            configurator_lines = design.cabinet_line_ids.filtered(
                lambda l: l.origin == "configurator"
            )
            existing_by_key = {
                l.layout_key: l for l in configurator_lines if l.layout_key
            }
        else:
            design = Design.create(vals)
            existing_by_key = {}

        # Panel-placement rule (Southbrook domain, 2026-07-01) — enforced
        # server-side so a client that skips the JS validator still can't
        # persist an end-cap panel pinned to a room wall. Rule:
        #   • cabinet_type == "filler" — auto-placed between cabinets by
        #     /layout; may contact a wall as a consequence of bridging the
        #     gap. No validation needed.
        #   • cabinet_type == "panel"  — decorative end cap; MUST attach to
        #     the left or right side face of a base or wall cabinet in the
        #     same design. Position must equal `host.x_position_in - width`
        #     (left cap) OR `host.x_position_in + host.width` (right cap).
        # Warnings are logged AND returned in the response so the client
        # can surface a banner. Persistence is not blocked (a rep may need
        # to save mid-configuration); the visual + JS-side error is the
        # primary UX signal.
        self._validate_end_cap_panel_placement(items)

        # 2026-07-01 E2E audit — enforce the /products catalog contract
        # on the save_design payload. The client posts product_ids
        # sourced from the /products response (main.py:31-34 filters on
        # southbrook_is_cabinet=True AND sale_ok=True), but the old
        # loop just browsed whatever id the client shipped: an authed
        # user could persist configurator lines pointing at archived,
        # non-cabinet, or ACL-restricted products (implicit info-
        # disclosure once /load_design_lines mirrors them back). Use
        # search() so ir.rule + domain filter apply in one shot —
        # rejected ids are logged + skipped per-line, the rest of the
        # save proceeds. This mirrors the ACL discipline already used
        # by save_position / delete_line / load_design_lines
        # (check_access at :420 / :459 / :492).
        # Server-side pricing: resolve the channel pricelist ONCE; each line is
        # priced from it below — the client's item["price"] is NEVER trusted
        # (HIGH-1 money vector: a tampered client could otherwise persist an
        # arbitrary price that flows verbatim into the quotation via
        # action_create_quotation, whose explicit price_unit is honoured over
        # the pricelist per its own kitchen_design.py comment).
        pricelist = self._resolve_pricelist(partner)
        Product = request.env["product.product"]
        # Batch the ACL-respecting catalog lookup into ONE search (was a
        # search() per item — 40-60 queries on a large save). Preserves the
        # /products contract (southbrook_is_cabinet=True AND sale_ok=True
        # AND readable by the caller) PLUS the narrow ctype='appliance'
        # admission below (Task 16 / SBK-028) — see that block's comment.
        wanted_ids = []
        for item in items:
            try:
                wanted_ids.append(int(item["product_id"]))
            except (KeyError, TypeError, ValueError):
                pass
        products_by_id = {}
        if wanted_ids:
            # Task 16 (SBK-028) — the is_cabinet gate above never admits
            # the SBK-APPL-* appliance-space stand-ins (southbrook_
            # is_cabinet=False BY DESIGN — they're excluded from the drag
            # catalog on purpose; see data/template_support_products.xml).
            # That meant a design instantiated from a template (which DOES
            # create real appliance lines — kitchen_template.py
            # action_instantiate) silently lost its appliance line on the
            # NEXT save from this 3D configurator page: the client re-
            # posts every line it currently has loaded, this search
            # rejected the appliance product_id, the line never made it
            # into incoming_keys below, and the removed-keys cleanup then
            # unlinked it as "stale" (prod-verified: design 102's RANGE
            # slot). Admit ctype='appliance' as a second, narrow OR-branch
            # — nothing else about the is_cabinet gate widens. The HIGH-1
            # money vector stays closed: price_unit is hard-pinned to 0.0
            # for every appliance line below, never routed through
            # _channel_price (a bigbox/fixed pricelist rule could
            # otherwise price a $0 stand-in at $98/SKU).
            for _p in Product.search([
                ("id", "in", wanted_ids),
                ("sale_ok", "=", True),
                "|",
                    ("product_tmpl_id.southbrook_is_cabinet", "=", True),
                    ("product_tmpl_id.southbrook_cabinet_type", "=", "appliance"),
            ]):
                products_by_id[_p.id] = _p
        incoming_keys = set()
        # PR4 — track every line this save actually created/wrote (by
        # layout_key) so the non-back-wall engine-placement pass below
        # can find them without a second search.
        saved_lines_by_key = {}
        # Task 9b — layout_keys this save wrote as sentinel/debris lines
        # (see _is_sentinel_position below). Excluded from the wall-
        # placement pass further down so the engine never computes a
        # real pose for a line we've just archived out of the design.
        sentinel_keys = set()
        # P0 fix (silent-drop truthful-response, 2026-08-02) — every
        # item THIS save did not end up as a real, active, positioned
        # line: sentinel/debris (persisted archived, see sentinel_keys
        # above) AND out-of-room-bounds (refused outright, never
        # persisted at all — see _out_of_room_bounds_reason's module
        # comment for why those two get different treatment). Surfaced
        # in the response below as top-level `unplaced_count`/`dropped`
        # so a client comparing sent-vs-returned counts can catch the
        # discrepancy instead of a bare "Design saved" — the response
        # can no longer claim zero drops while quietly failing to place
        # some of what it was handed.
        dropped_items = []
        for seq, item in enumerate(items, start=1):
            try:
                pid = int(item["product_id"])
            except (KeyError, TypeError, ValueError):
                _logger.warning(
                    "save_design: item[%d] missing/invalid product_id (%r); "
                    "skipping",
                    seq, item.get("product_id"),
                )
                dropped_items.append({
                    "layout_key": item.get("layout_key") or "auto-%d" % seq,
                    "sku":        item.get("sku") or "",
                    "reason":     "missing or invalid product_id",
                })
                continue
            product = products_by_id.get(pid)
            if not product:
                # Either the id doesn't exist, the caller can't read
                # it, the template isn't tagged as a Southbrook cabinet,
                # or it's not sale_ok. Reject in one branch — the
                # client never gets to distinguish "which of those"
                # (avoids existence-oracle leak; matches the
                # AccessError convention set by southbrook_estimating_
                # website/controllers/room_api.py:_get_room_scoped).
                _logger.warning(
                    "save_design: rejected product_id=%d on design=%s "
                    "(fails /products catalog contract: must be sale_ok="
                    "True AND readable by user AND either "
                    "southbrook_is_cabinet=True OR "
                    "southbrook_cabinet_type='appliance')",
                    pid, design.id,
                )
                dropped_items.append({
                    "layout_key": item.get("layout_key") or "auto-%d" % seq,
                    "sku":        item.get("sku") or "",
                    "reason":     "product not available in the cabinet catalog",
                })
                continue
            tmpl    = product.product_tmpl_id
            layout_key = item.get("layout_key") or "auto-%d" % seq
            incoming_keys.add(layout_key)
            is_appliance = tmpl.southbrook_cabinet_type == "appliance"
            # Task 9b — server-side sentinel guard (Task 9 report,
            # "producer" section). The client's 1e6 sort-key sentinel
            # (kitchen_configurator.js `_addCabinetFromProduct`'s
            # `sortX` fallback) is meant to be transient, overwritten by
            # a real packed position before save — but `packRow` is
            # skipped whenever `multiWall` is true, so a newly-added
            # back-wall item can reach this endpoint still carrying it.
            # This save path previously wrote x/z_position_in verbatim,
            # letting the sentinel land in the DB as an active, priced,
            # "real" line (prod-verified: design 99). Any incoming
            # position at/beyond the debris threshold — in either axis,
            # regardless of sign — is never persisted as active,
            # positioned-for-billing, or priced: it's written archived +
            # is_unresolved so it never counts toward totals/price
            # (cabinet_line_ids excludes inactive lines by default) and
            # never silently vanishes (still a real row, still logged,
            # still visible to an admin who unarchives it to inspect).
            is_sentinel = (_is_sentinel_position(item.get("x_position_in", 0))
                           or _is_sentinel_position(item.get("z_position_in", 0)))
            if is_sentinel:
                sentinel_keys.add(layout_key)
                _logger.warning(
                    "save_design: sentinel/out-of-bounds position on "
                    "design=%s product=%s (%s, layout_key=%s) — "
                    "x=%r z=%r (threshold=%s). Persisting archived + "
                    "is_unresolved + unpriced instead of an active line.",
                    design.id, product.id, product.display_name,
                    layout_key, item.get("x_position_in"),
                    item.get("z_position_in"), _DEBRIS_THRESHOLD_IN,
                )
                dropped_items.append({
                    "layout_key": layout_key,
                    "sku":        product.default_code or "",
                    "reason":     (
                        "position sentinel/debris (>= %g in on x or z) — "
                        "not a real placement; saved archived, excluded "
                        "from totals and price" % _DEBRIS_THRESHOLD_IN
                    ),
                })
            # NOTE — an out-of-room-bounds refusal was tried here and REMOVED.
            # It rejected any back-wall item outside [-24in, room_dim+24in],
            # which is defensible in isolation but contradicts a contract this
            # codebase has already settled: test_sentinel_write_guard's
            # `test_exact_threshold_boundary` pins x=9999 as debris and
            # x=9998.99 as a LEGITIMATE line that must persist active at full
            # price. Refusing it broke that test. _DEBRIS_THRESHOLD_IN is this
            # endpoint's single agreed line between "a real placement" and "a
            # sentinel", and a bulk save is the wrong place for a tighter one:
            # refusing here DISCARDS data the client believed it saved, which
            # is the very failure this round exists to fix.
            # `_out_of_room_bounds_reason` is retained and still used by
            # save_position, where the contract is different in kind — a drag
            # is one interactive gesture and refusing it gives the user
            # immediate, visible feedback at the moment they let go, rather
            # than silently dropping one row out of a batch of twenty.
            # Task 11 (SBK-024) — client normally echoes back the
            # server-computed width_in (from /products or /layout,
            # both now placed_width_in-sourced below); the variant's
            # own Width is the honest fallback when it's omitted.
            _width_in = item.get("width_in", placed_width_in(product) or 24.0)
            # SERVER-SIDE WIDTH BOUND — money-critical, added with the C3
            # pro-rata pricing below. `width_in` arrives from an untrusted
            # payload and now DIVIDES into the price: an unbounded value
            # makes the price arbitrarily manipulable (a posted 0.001"
            # prices a cabinet at ~$0; a negative width prices it
            # NEGATIVE, which would flow straight into the quotation as a
            # credit). The UI's own width control offers 6-48", so this
            # clamp is far wider than any legitimate edit and exists only
            # to make the ratio's denominator sane. Clamping (rather than
            # raising) keeps a batch save of twenty lines from failing
            # wholesale on one bad row — the same posture the surrounding
            # code already takes — and geometry and price stay consistent
            # because BOTH read the clamped value from here on.
            try:
                _width_in = float(_width_in or 0.0)
            except (TypeError, ValueError):
                _width_in = placed_width_in(product) or 24.0
            if not (_SBK_MIN_WIDTH_IN <= _width_in <= _SBK_MAX_WIDTH_IN):
                _logger.warning(
                    "save_design: width_in %.4f out of bounds [%.1f, %.1f] "
                    "for product %s — clamped (price is pro-rated from this "
                    "value, so it must never be unbounded).",
                    _width_in, _SBK_MIN_WIDTH_IN, _SBK_MAX_WIDTH_IN,
                    product.display_name)
                _width_in = min(max(_width_in, _SBK_MIN_WIDTH_IN),
                                _SBK_MAX_WIDTH_IN)
            # C3 (money-integrity fix, 2026-08-03) — a custom width dialed
            # into the 3D configurator's width-edit control used to change
            # the cabinet's geometry (width_in) with NO effect at all on
            # price_unit: this loop's price came straight from
            # `_channel_price(product, pricelist, partner)`, a pure
            # function of product x pricelist x partner that has never
            # taken a width argument. A rep could resize any cabinet to
            # any width 6-48in and the persisted price stayed pinned to
            # whatever the ORIGINAL (nominal-width) product resolved to.
            #
            # `is_custom_width` (kitchen_design.py's design-line field) is
            # the honest, explicit acknowledgement that gates a re-price:
            # only when the client says "yes, this width is deliberate"
            # do we pro-rate; an un-acknowledged mismatch is instead left
            # exactly as before (still visibly wrong) and BLOCKED at
            # production-validation time by the new CUSTOM_WIDTH_UNCONFIRMED
            # check (_check_production_ready) — never silently re-priced
            # behind the rep's back.
            #
            # The pro-rata mechanism itself mirrors the ONE other prorate
            # already in this file — `_make_filler` above prices a filler
            # by (filler width / catalog standard width). Same idea here:
            # (dialed-in width / the product's own nominal placed_width_in).
            # This is the smallest honest mechanism available — the
            # codebase has no per-series custom-width surcharge concept
            # to reuse (grepped: no hits) — and it is IMMEDIATELY visible
            # to the rep, since the line's price genuinely moves with the
            # width instead of silently staying flat.
            # SERVER-AUTHORITATIVE, deliberately NOT the client's claim.
            # Trusting `item["is_custom_width"]` would let any caller post
            # the flag at a nominal width and pro-rate the price to
            # whatever they liked — a price-manipulation vector from an
            # untrusted payload. The width delta is the only honest
            # source: if the dialed width differs from the product's own
            # nominal width, the line IS custom, whatever the client says.
            # This also means the money bug is closed unconditionally —
            # the price follows the width every time, including on
            # drag-to-resize (a first-class feature here), instead of
            # waiting for anyone to tick an acknowledgement.
            _nominal_for_flag = (0.0 if (is_appliance or is_sentinel)
                                 else (placed_width_in(product) or 0.0))
            _is_custom_width = (
                _nominal_for_flag > 0
                and abs(float(_width_in or 0.0) - _nominal_for_flag) > 0.01
            )
            _base_price = (0.0 if (is_appliance or is_sentinel) else
                            self._channel_price(product, pricelist, partner))
            if _is_custom_width:
                _nominal_w = placed_width_in(product) or 0.0
                if _nominal_w > 0:
                    _price_unit = round(
                        _base_price * (float(_width_in or 0.0) / _nominal_w), 2)
                else:
                    # No meaningful nominal width to prorate against
                    # (e.g. a product with no Width attribute at all) —
                    # fall back to the plain channel price rather than
                    # divide by zero or invent a ratio from nothing.
                    _price_unit = _base_price
            else:
                _price_unit = _base_price

            line_vals = {
                "sequence":       seq * 10,
                "product_id":     product.id,
                "quantity":       1,
                # Task 16 (SBK-028) — appliance placeholders are ALWAYS
                # free: never route through _channel_price. Task 9b adds
                # the sentinel gate alongside it: neither an appliance
                # stand-in nor a debris line is ever priced from the
                # resolved channel pricelist.
                "price_unit":     _price_unit,
                "active":         not is_sentinel,
                "is_unresolved":  is_sentinel,
                "cabinet_type":   tmpl.southbrook_cabinet_type,
                # Whitelist, not passthrough — an unrecognised selection
                # value would raise ValueError deep in Line.create and
                # crash the WHOLE batch save (matches the defensive
                # posture the "wall" field gets above); a value outside
                # the model's Selection just degrades to unset.
                "appliance_type": (item.get("appliance_type")
                                    if item.get("appliance_type") in (
                                        "range", "fridge", "dishwasher",
                                        "hood", "other")
                                    else False),
                "width_in":       _width_in,
                "height_in":      item.get("height_in", tmpl.southbrook_height_in or 34.5),
                "depth_in":       item.get("depth_in",  tmpl.southbrook_depth_in or 24.0),
                # C3 — persisted verbatim from the client, same trust
                # level as `pinned` just below: it never grants access to
                # anything, it only gates whether THIS line's own price
                # is pro-rated (above) and whether production validation
                # blocks it (CUSTOM_WIDTH_UNCONFIRMED) — a false claim of
                # "yes I confirmed this" just means an honestly-computed
                # pro-rated price ships instead of a block, never a price
                # the rep didn't ask for and never an access-control
                # decision.
                # The SERVER-DERIVED value (computed above from the measured
                # width delta), never the client's claim. Persisting
                # `item["is_custom_width"]` here would have left the stored
                # flag lying even though the price was correct: a caller
                # could post the flag at a nominal width and the LINE would
                # read "custom width" forever after — misreporting a
                # standard cabinet to anyone who trusts the field.
                # One derivation, one source of truth, price and flag agree.
                "is_custom_width": _is_custom_width,
                "x_position_in":  item.get("x_position_in", 0),
                "y_position_in":  item.get("y_position_in", 0),
                "z_position_in":  item.get("z_position_in", 0),
                # v19.0.4.20.0 — Smart-pinning (Option C): persist per-line
                # manual-move + rotation so a reload restores the user's
                # placement instead of the /layout auto-generated one.
                "pinned":         bool(item.get("pinned")),
                "rotation_deg":   float(item.get("rotation_deg") or 0.0),
                "layout_key":     layout_key,
                "origin":         "configurator",
                # PR2 — persist the layout-domain wall assignment.
                # Already validated against kitchen_layout_engine.WALLS
                # above; re-derive the same default here for items that
                # omitted the key.
                "wall":           item.get("wall") or "back",
            }
            if layout_key in existing_by_key:
                line = existing_by_key[layout_key]
                line.write(line_vals)
            else:
                line = Line.create({"design_id": design.id, **line_vals})
            saved_lines_by_key[layout_key] = line

        # Unlink configurator-origin lines that the user removed in the
        # 3D pane. Manual-origin lines are excluded from existing_by_key
        # so they survive every save.
        removed_keys = set(existing_by_key) - incoming_keys
        if removed_keys:
            stale = Line.browse([])
            for k in removed_keys:
                stale |= existing_by_key[k]
            stale.unlink()

        # PR4 (2026-07-12) — a cabinet added/moved onto a non-back wall
        # (state.activeWall in the client) was, until now, persisted at
        # the raw CLIENT-computed x/y/z/rotation — `_addCabinetFromProduct`
        # only ever computes that correctly for the BACK wall (see
        # docs/2026-07-12-renderer-contract.md). Route every non-back-wall
        # configurator line through the SAME engine-delegation path the
        # website's 3D tab already uses (`_place_lines_on_wall`, shared on
        # the southbrook.kitchen.design model), overwriting the client's
        # guess with the pure kitchen_layout_engine's pose. Back-wall
        # lines are LEFT ALONE — the client's own back-wall packing is
        # correct today and must not change (backward compat).
        # Task 9b — a sentinel line is archived + unpriced on purpose;
        # never route it through the engine-placement pass, which would
        # otherwise compute and write it a real pose (undoing the guard
        # above for its x/y/z, even though `active`/`is_unresolved`/
        # `price_unit` would still hold). Its debris position is left
        # exactly as the client sent it, same as the migration leaves
        # existing debris rows untouched apart from `active`.
        wall_lines = Line.browse([])
        for key, line in saved_lines_by_key.items():
            if key in sentinel_keys:
                continue
            if (line.wall or "back") != "back":
                wall_lines |= line
        placed = []
        if wall_lines:
            poses = design._place_lines_on_wall(wall_lines)
            for line in wall_lines:
                pose = poses.get(line.id)
                if not pose:
                    continue
                placed.append({
                    "layout_key":     line.layout_key,
                    "x_position_in":  pose["x_position_in"],
                    "y_position_in":  pose["y_position_in"],
                    "z_position_in":  pose["z_position_in"],
                    "rotation_deg":   pose["rotation_deg"],
                })

        # C2 — corner resolution. The website portal's add route
        # (southbrook_estimating_website/controllers/main.py ~2894-2905)
        # already calls design.action_auto_arrange(sync=False) the moment
        # a design has cabinets on 2+ walls, so an inside corner gets a
        # real SB-CORNER / SB-WALL-CORNER cabinet instead of two runs
        # silently interpenetrating. This page had no equivalent hook —
        # mirror that pattern here, once per save, after the PR4
        # non-back-wall placement pass above has settled every line's
        # wall assignment.
        relaid = False
        layout_warning = None
        _arrangeable_lines = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
            and l.layout_role != "derived"
            and l.cabinet_type not in ("filler", "panel"))
        walls_used = {(dl.wall or "back") for dl in _arrangeable_lines}
        # P0 fix (drag-revert / undo-corruption, 2026-08-02) — this used
        # to be an unconditional `design.action_auto_arrange(sync=False)`
        # the moment walls_used >= 2, on EVERY save of a multi-wall
        # design. action_auto_arrange is a RESET-TO-CANONICAL re-derive
        # (see its own docstring/comments, models/kitchen_design.py):
        # it deletes every derived artifact, restores every superseded
        # canonical line, then repositions ALL surviving canonical lines
        # from a fresh run of kitchen_layout_engine — it does not read
        # `pinned` at all, so it is emphatically NOT idempotent against a
        # design a human has manually rearranged. Two verified,
        # user-visible bugs came out of that:
        #   (a) drag-then-revert — the client debounces a full
        #       save_design 3s after any change. A rep drags a cabinet;
        #       `save_position` persists the new pose (and flips
        #       `pinned` True) immediately on pointer-up; three seconds
        #       later the OWN autosave fires, lands here with
        #       walls_used >= 2, and action_auto_arrange silently
        #       recomputes that same cabinet's pose from scratch,
        #       stomping the drag the user just watched happen. The rep
        #       sees their move "take" for three seconds and then
        #       unexplainably snap back.
        #   (b) undo-destroys-itself — an undo (client-side, restores a
        #       prior item snapshot into state.items) calls this same
        #       save_design to persist the restored snapshot. If the
        #       restored design still spans 2+ walls, THIS save's own
        #       trailing autosave re-triggers auto-arrange, which
        #       re-derives poses/cabinet substitutions from the
        #       CURRENT (just-restored) canonical set — not from
        #       whatever the user actually undid to — so the
        #       just-restored state is immediately overwritten again,
        #       including cabinet_type/price on any line a corner
        #       absorbed. The undo "worked" for one render frame and
        #       then quietly un-did itself.
        # Fix: only auto-arrange when nothing in this design has been
        # manually positioned — `pinned` (the Smart-pinning field,
        # v19.0.4.20.0) is that existing signal; it is flipped True by
        # `save_position` the moment a rep drags/rotates a cabinet, and
        # persisted on every line this endpoint writes (see `line_vals`
        # above). No line pinned -> this is a freshly-generated/
        # untouched design -> auto-arrange still runs on every save,
        # unchanged from before (a template instantiation or a
        # cabinet-add flow still gets its corner resolved live). Any
        # line pinned -> a human has taken over placement -> skip the
        # re-derive so this save never overwrites what they just did.
        # `allow_relayout` (new, default False, see docstring) is the
        # explicit-opt-in escape hatch for a future caller that KNOWS it
        # wants a forced re-arrange (e.g. a "Reset Layout" action) even
        # over pinned lines — no such caller exists yet, so in practice
        # this only ever widens the gate, never narrows it.
        _has_pinned = any(dl.pinned for dl in _arrangeable_lines)
        # The `if len(walls_used) >= 2:` test itself is UNCHANGED from
        # before this fix — it still gates the whole if/elif pair below
        # (walls_used>=2 -> try to resolve corners; fewer than 2 walls
        # -> the elif's stale-derived-line cleanup). Only the decision
        # of whether to actually CALL action_auto_arrange is nested
        # inside it now, so a pinned multi-wall design that skips the
        # call still lands in this branch (not the elif) and leaves any
        # existing derived corner lines exactly as they are — the elif's
        # own premise ("fewer than 2 walls now in use") does not apply
        # to it.
        if len(walls_used) >= 2:
            if allow_relayout or not _has_pinned:
                try:
                    design.action_auto_arrange(sync=False)
                    relaid = True
                except LayoutCapacityExceeded as e:
                    # action_auto_arrange runs inside its own
                    # cr.savepoint() — this exception means it already
                    # rolled itself back, so the design as saved above
                    # is untouched/still persisted. relaid stays False:
                    # nothing changed for the client to pull, but we
                    # still owe the rep a plain-English reason the
                    # corner didn't resolve.
                    outside = getattr(e, "outside", []) or []
                    if outside:
                        worst_overflow_in = max(
                            (o.get("overflow_mm") or 0) for o in outside
                        ) / 25.4
                        layout_warning = (
                            "Your cabinet run is too long for the wall: "
                            "%d cabinet(s) don't fit (worst overflows "
                            "~%.1f in). Remove a cabinet or enlarge the "
                            "room."
                        ) % (len(outside), round(worst_overflow_in, 1))
                    else:
                        layout_warning = (
                            "Your cabinet run is too long for the wall. "
                            "Remove a cabinet or enlarge the room."
                        )
            # else: at least one line is pinned and the caller did not
            # opt in via allow_relayout — leave this design exactly as
            # the create/write loop above just persisted it. `relaid`
            # stays False (truthful: nothing was relaid) and
            # `layout_warning` stays None (this isn't a capacity
            # failure, it's a deliberate no-op).
        elif design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
        ):
            # Fewer than 2 walls now in use but stale derived corner lines
            # remain (e.g. the rep deleted cabinets back down to a single
            # wall since the last auto-arrange) — clean up so the scene
            # doesn't keep showing a corner cabinet with no corner. Mirrors
            # action_auto_arrange's own reset block (kitchen_design.py
            # ~461-477): restore archived canonical lines, then unlink the
            # derived ones.
            #
            # Task 9b (review round 2) — THIRD reactivation site with the
            # same class of bug the first two had: a two-save sequence
            # (save #1 multi-wall, creates derived corners; save #2
            # single-wall, containing/after a guarded sentinel line) hits
            # this elif branch and would blanket-reactivate the sentinel
            # right along with the legitimately-superseded canonicals.
            # Excluded via the same DEBRIS-SIGNATURE predicate the two
            # kitchen_design.py reset sites use (archived + is_unresolved
            # + at/beyond DEBRIS_THRESHOLD_IN on x or z —
            # SouthbrookKitchenDesignLine._is_debris_signature) — NOT a
            # bare `not l.is_unresolved`, which would also strand
            # legitimate template GHOST placeholder lines (also
            # is_unresolved=True, but ordinary active cabinets that
            # participate in arrange and must remain revivable).
            ctx = design.with_context(active_test=False)
            ctx.cabinet_line_ids.filtered(
                lambda l: not l.active and not l._is_debris_signature()
            ).write({"active": True})
            design.invalidate_recordset(["cabinet_line_ids"])
            ctx.cabinet_line_ids.filtered(
                lambda l: l.layout_role == "derived").unlink()
            relaid = True

        if relaid:
            design.invalidate_recordset()

        # Task 21 (corner-remediation 2026-07-30, SBK-035) — flip to
        # 'configured' only now that the line set this save actually
        # produced is known (creates/updates/stale-unlinks above, plus
        # any corner-engine relay just above), gated on the design
        # actually having at least one active cabinet line. Mirrors
        # kitchen_design.py's own convention (_apply_kitchen_template's
        # "if seeded: self.state = 'configured'"). Left unspecified (NOT
        # reverted to draft) when the save ends up with zero lines, so an
        # existing quoted/ordered design's state is never silently
        # downgraded by this endpoint — same posture it already takes
        # toward quoted/ordered designs elsewhere in this method. The
        # model's own _check_configured_state_has_lines constraint is
        # the backstop that rejects any OTHER attempt to force
        # state='configured' onto a lineless design.
        if design.cabinet_line_ids:
            design.state = "configured"

        # Touch editing v1 (2026-08-02 spec §4 item 3) — only on the
        # success path (every early-return above this point rejected
        # the save outright and never reaches here — nothing to log for
        # a save that didn't happen). `normalize_touch_stats` returns {}
        # for absent/malformed input, which this treats as "don't log"
        # rather than logging a hollow line every save.
        _touch_stats = normalize_touch_stats(touch_stats)
        if _touch_stats:
            _logger.info(
                "[touch] save_design design=%s gesture_rollbacks=%s "
                "gesture_abandons=%s",
                design.id,
                _touch_stats.get("gesture_rollbacks", 0),
                _touch_stats.get("gesture_abandons", 0),
            )

        resp = {
            "id":     design.id,
            "name":   design.display_name,
            # PR4 — engine-updated poses for the wall lines this save
            # moved, so the client can apply them onto state.items and
            # re-render immediately without a reload.
            "placed": placed,
            # C2 — when the corner engine (or the stale-derived-line
            # cleanup) changed the line set/geometry, `lines` carries the
            # authoritative post-save state (same shape as
            # load_design_lines) so the client can replace state.items
            # wholesale instead of trusting its own pre-save guess.
            "relaid":         relaid,
            "lines":          self._serialize_design_lines(design) if relaid else None,
            "layout_warning": layout_warning,
            # Task 7 (SBK-033) — every save refreshes state.summary from
            # the record just written, so counts/price can never drift
            # from the DB the way the audited "four disagreeing totals"
            # finding describes. Sent unconditionally (not gated on
            # `relaid`) — server-side pricing (_channel_price) or the
            # sentinel guard above can change price_unit/active even on
            # an ordinary single-wall save that never touches the corner
            # engine. `summary.total`/`summary.unplaced_count` are both
            # sourced from design.summary_total/design.unplaced_count —
            # computed from `cabinet_line_ids`, which Odoo's default
            # active_test already excludes every archived sentinel/
            # debris line from, and which the out-of-bounds items
            # rejected below never even joined — so `summary.total`
            # always equals the count of lines genuinely, actively
            # placed by this (and every prior) save, never inflated by
            # what was refused.
            "summary": design._get_counter_summary(),
        }
        # P0 fix (silent-drop truthful-response, 2026-08-02) — real
        # unplaced_count/dropped, added ONLY when this save actually
        # refused or archived something. Two reasons for "only when
        # non-empty" rather than unconditionally:
        #   1. Truthful either way — an empty `dropped` list and an
        #      absent one both mean "nothing was dropped"; the client
        #      only needs to check truthiness/length, never a specific
        #      shape for the all-placed case.
        #   2. This file may not touch any other file (this task's
        #      file-ownership contract), and
        #      test_touch_health_counters.py's
        #      test_absent_touch_stats_is_byte_compatible pins the EXACT
        #      response key set for an all-in-bounds save
        #      (`{"id", "name", "placed", "relaid", "lines",
        #      "layout_warning", "summary"}`) — unconditionally adding
        #      two more keys to every response, including that one,
        #      would break a contract test this fix has no way to
        #      update in step.
        # `unplaced_count` here is THIS save's own refused/archived
        # item count — deliberately NOT the same number as
        # summary["unplaced_count"] (design.unplaced_count, the model's
        # stored field): that field only ever counts ACTIVE
        # is_unresolved GHOST placeholder lines (template-instantiation
        # slots), because archived debris/sentinel rows are excluded
        # from cabinet_line_ids by the same active_test default the
        # summary comment above relies on — see
        # _get_counter_summary()'s own docstring. Without this
        # top-level pair the client had no truthful per-save signal at
        # all: summary.unplaced_count silently reads 0 even on a save
        # that just archived/refused N items — the exact "Design saved"
        # false-positive this fix exists to close.
        if dropped_items:
            resp["unplaced_count"] = len(dropped_items)
            resp["dropped"] = dropped_items
        return resp

    # ── v19.0.4.20.0 · Smart-pinning RPC surface ─────────────────────────────────
    # Three lightweight routes so the client can persist a single
    # cabinet's placement (or unlink it) without triggering the full
    # save_design write-cycle. Called on pointer-up after a drag, on
    # R-key rotation, and on Delete/Backspace.
    #
    #   /load_design_lines  → returns the design's configurator-origin
    #                         lines as {layout_key: item-dict} so the
    #                         client can rehydrate a saved manual
    #                         placement instead of throwing it away.
    #   /save_position      → writes x_position_in / y_position_in /
    #                         z_position_in / rotation_deg / pinned on
    #                         a single line, matched by layout_key.
    #   /delete_line        → unlinks a single configurator-origin
    #                         line, matched by layout_key.
    #
    # None of these touch manual-origin lines. save_position + delete_line
    # silently no-op when the target design/line isn't found or the
    # calling user lacks write access — surfacing an error would hijack
    # the drag UX.
    @http.route(
        "/southbrook_kitchen/configurator/load_design_lines",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def load_design_lines(self, design_id):
        # Task 20 (SBK-034) — deep-link source-of-truth. A design id
        # that doesn't resolve now comes back with an explicit
        # `error`/`error_code` (NOT_FOUND / ACCESS_DENIED — same code
        # strings /rearrange already uses below) instead of the old
        # bare `{"lines": []}`. The client used to read an empty
        # `lines` list as "a real, freshly-created design with nothing
        # saved yet" and silently ran the starter-layout generator over
        # it — indistinguishable from "this id doesn't exist / isn't
        # yours", which is exactly the audited "reload silently resets
        # to a fresh design, abandoning the saved one" bug (SBK-034).
        # A design that genuinely EXISTS and is READABLE, even with
        # zero configurator-origin lines (e.g. a template preset saved
        # with generate_layout=False), is UNCHANGED below — that empty-
        # but-real case still returns the bare success shape; only the
        # two short-circuits above it are new.
        design = request.env["southbrook.kitchen.design"].browse(int(design_id or 0))
        if not design.exists():
            return {
                "lines": [],
                "error": "This design no longer exists. It may have "
                         "been deleted.",
                "error_code": "NOT_FOUND",
            }
        try:
            design.check_access("read")
        except Exception:
            return {
                "lines": [],
                "error": "You don't have access to this design.",
                "error_code": "ACCESS_DENIED",
            }
        out = self._serialize_design_lines(design)
        # PR2.5a (Gap 1) — additive: a direct-URL reload of the
        # configurator restores the client action's `design_id` (via the
        # actionStack fallback in kitchen_configurator.js) but NOT the
        # room dims / design name that normally arrive via
        # action_open_configurator's `params` (models/kitchen_design.py
        # action_open_configurator). Echo them here, read-side only, so
        # _hydrateFromDesign() can backfill state.room / state.designName
        # when a URL restore skipped params entirely. The no-design /
        # no-access branches above now short-circuit with an
        # error/error_code (Task 20, SBK-034) before reaching here —
        # this key is only ever added when a design was actually found
        # and readable.
        return {
            "lines": out,
            "room": {
                "width_in":  design.room_width_in,
                "depth_in":  design.room_depth_in,
                "height_in": design.room_height_in,
            },
            "design_name": design.display_name,
            # Task 7 (SBK-033) — hydrate state.summary with the
            # authoritative server object immediately, rather than
            # leaving it at the boot placeholder until the client's own
            # first local recompute runs.
            "summary": design._get_counter_summary(),
        }

    # ─── Task 19 (corner-remediation 2026-07-30, SBK-032 + C9 UX) ───────────────
    # Read-only structured-issue feed for the 3D configurator's
    # persistent status chip. Same NOT_FOUND/ACCESS_DENIED shape as
    # /load_design_lines just above, and the SAME normalized issue
    # structure (`_normalize_production_issues`) the record form's own
    # `action_validate_production` button renders from, so the chip and
    # the form button can never disagree about what's wrong with a
    # design. No writes — a validation check is read-only by
    # construction (`_check_production_ready` never mutates the design).
    @http.route(
        "/southbrook_kitchen/configurator/validate",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def validate_production(self, design_id):
        design = request.env["southbrook.kitchen.design"].browse(
            int(design_id or 0))
        if not design.exists():
            return {
                "issues": [],
                "error": "This design no longer exists. It may have "
                         "been deleted.",
                "error_code": "NOT_FOUND",
            }
        try:
            design.check_access("read")
        except Exception:
            return {
                "issues": [],
                "error": "You don't have access to this design.",
                "error_code": "ACCESS_DENIED",
            }
        issues = design._normalize_production_issues(
            design._check_production_ready())
        # Resolve each issue's line_ids to the client's own per-cabinet
        # identity (layout_key) so a click can select+reveal the
        # offending cabinet the SAME way Task 22's canvas selection
        # already works (state.selected + the canvas reveal) — never a
        # second, id-keyed selection path. Lines with no layout_key
        # (e.g. a manual/backend-origin line the 3D canvas never
        # rendered) are simply omitted from `layout_keys`, not errored.
        wanted_ids = {lid for issue in issues for lid in issue["line_ids"]}
        key_by_id = {
            line.id: line.layout_key
            for line in design.cabinet_line_ids
            if line.id in wanted_ids and line.layout_key
        }
        for issue in issues:
            issue["layout_keys"] = [
                key_by_id[lid] for lid in issue["line_ids"]
                if lid in key_by_id
            ]
        blocking = sum(1 for i in issues if i["severity"] == "blocking")
        warning = sum(1 for i in issues if i["severity"] == "warning")
        return {
            "issues": issues,
            "counts": {"blocking": blocking, "warning": warning},
            "ready": blocking == 0,
        }

    # ─── Task 20 (SBK-034) — landing-state recent designs ───────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/recent_designs",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def recent_designs(self, limit=8):
        """The current user's most-recently-touched designs, for the
        configurator's landing screen (opened with no design id, e.g.
        the bare menu entry) so it can offer "Continue <design>"
        instead of the old silent-auto-generate-a-fresh-design
        behaviour. Deliberately minimal — id + a few display fields,
        no line data (the landing screen never touches
        cabinet_line_ids). A plain non-sudo search: ir.rule already
        does the right per-role scoping everywhere else in this
        controller (security/kitchen_design_rules.xml — designer sees
        own via create_uid, readonly/manager see all); no special-
        casing needed here either.
        """
        try:
            n = max(1, min(int(limit or 8), 25))
        except (TypeError, ValueError):
            n = 8
        designs = request.env["southbrook.kitchen.design"].search(
            [], order="write_date desc", limit=n,
        )
        return [{
            "id":            d.id,
            "name":          d.display_name,
            "partner_name":  d.partner_id.name or "",
            "room_width_in":  d.room_width_in,
            "room_depth_in":  d.room_depth_in,
            "room_height_in": d.room_height_in,
            "write_date":    d.write_date.isoformat() if d.write_date else "",
        } for d in designs]

    # T7 (kitchen templates) — engine-routed room resize. The naive
    # /layout generator above is a single-wall fill: calling it on a
    # SAVED design's room resize regenerates (and on save, replaces)
    # the canonical multi-wall layout. This route is the honest path:
    # write the dims, re-derive every pose through action_auto_arrange
    # (corner engine included), and hand back the same line emission
    # load_design_lines uses. A resize the cabinets can't fit REVERTS
    # the dims and reports ROOM_TOO_SMALL — the room is never left in a
    # non-fitting state.
    @http.route(
        "/southbrook_kitchen/configurator/rearrange",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def rearrange(self, design_id, room=None):
        design = request.env["southbrook.kitchen.design"].browse(
            int(design_id or 0)).exists()
        if not design:
            return {"error": "Design not found", "error_code": "NOT_FOUND"}
        try:
            design.check_access("write")
        except Exception:
            return {"error": "You don't have write access to this design.",
                    "error_code": "ACCESS_DENIED"}
        prev_room = {
            "width_in": design.room_width_in,
            "depth_in": design.room_depth_in,
            "height_in": design.room_height_in,
        }
        pre_active_ids = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "canonical").ids
        if room:
            design.write({
                "room_width_in": float(
                    room.get("width_in") or prev_room["width_in"]),
                "room_depth_in": float(
                    room.get("depth_in") or prev_room["depth_in"]),
                "room_height_in": float(
                    room.get("height_in") or prev_room["height_in"]),
            })
        try:
            design.action_auto_arrange(sync=False)
            # T5 lesson — the engine "fits" impossible runs by ARCHIVING
            # cabinets, it does not raise. pre_active_ids only holds
            # lines that were ACTIVE going in (already-substituted
            # corner leads are not in it), so any of them inactive now
            # means this resize FORCED a drop: refuse it.
            dropped = request.env["southbrook.kitchen.design.line"] \
                .with_context(active_test=False) \
                .browse(pre_active_ids).filtered(lambda l: not l.active)
            if room and dropped:
                raise LayoutCapacityExceeded(
                    0, 0, detail="%d cabinet(s) would be dropped" %
                    len(dropped))
        except LayoutCapacityExceeded as e:
            # arrange rolled ITSELF back (its own savepoint) — the dims
            # write above is ours to revert (never persist a resize the
            # cabinets don't fit). Then re-arrange at the ORIGINAL dims
            # (they fit before, so this is a restore, best-effort).
            design.write({
                "room_width_in": prev_room["width_in"],
                "room_depth_in": prev_room["depth_in"],
                "room_height_in": prev_room["height_in"],
            })
            try:
                design.action_auto_arrange(sync=False)
            except LayoutCapacityExceeded:
                pass
            return {"error": "That room size doesn't fit the current "
                             "cabinets (%s). The room was not changed — "
                             "remove a cabinet first." % e,
                    "error_code": "ROOM_TOO_SMALL",
                    "room": prev_room}
        return {
            "lines": self._serialize_design_lines(design),
            "room": {
                "width_in": design.room_width_in,
                "depth_in": design.room_depth_in,
                "height_in": design.room_height_in,
            },
            # Task 7 (SBK-033) — auto-arrange is exactly where corner/
            # tall/appliance counts CHANGE (derived corners inserted,
            # absorbed canonicals archived), so the client needs a fresh
            # authoritative summary here more than almost anywhere else.
            "summary": design._get_counter_summary(),
        }

    @http.route(
        "/southbrook_kitchen/configurator/save_position",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def save_position(self, design_id, layout_key, x_position_in=None,
                      y_position_in=None, z_position_in=None,
                      rotation_deg=None, pinned=None):
        design = request.env["southbrook.kitchen.design"].browse(int(design_id or 0))
        if not design.exists() or not layout_key:
            return {"ok": False, "reason": "not_found"}
        try:
            design.check_access("write")
        except Exception:
            return {"ok": False, "reason": "forbidden"}
        line = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
                      and l.layout_key == layout_key
        )[:1]
        if not line:
            return {"ok": False, "reason": "no_matching_line"}
        vals = {}
        if x_position_in is not None:
            vals["x_position_in"] = float(x_position_in)
        if y_position_in is not None:
            vals["y_position_in"] = float(y_position_in)
        if z_position_in is not None:
            vals["z_position_in"] = float(z_position_in)
        if rotation_deg is not None:
            vals["rotation_deg"] = float(rotation_deg) % 360.0
        if pinned is not None:
            vals["pinned"] = bool(pinned)

        # P0 fix (silent-drop truthful-response, 2026-08-02) — the SAME
        # sentinel/out-of-bounds guard save_design's item-write loop
        # applies (see _is_sentinel_position / _out_of_room_bounds_reason
        # above), mirrored here because THIS route is a SEPARATE write
        # path onto x_position_in/z_position_in that never goes through
        # that loop — it writes directly on pointer-up after a drag /
        # R-key rotation. Prod fact: migrations/19.0.5.44.0 swept 12
        # ACTIVE (never archived) sentinel design lines, explicitly
        # attributed to "at least one other write path [that] still lets
        # a sentinel position reach the DB verbatim" despite Task 9b's
        # save_design guard already being live (that migration's own
        # docstring). This is the only other place in this controller
        # that persists x/z_position_in, so it is that remaining
        # producer. Evaluated against the position this write would
        # RESULT in (an omitted axis keeps its current, already-
        # validated value) — not just the incoming kwargs — so a caller
        # that only sends rotation_deg can never accidentally trip this
        # on a line whose stored position was always fine.
        final_x = vals.get("x_position_in", line.x_position_in)
        final_z = vals.get("z_position_in", line.z_position_in)
        is_sentinel = (_is_sentinel_position(final_x)
                       or _is_sentinel_position(final_z))
        if is_sentinel:
            # Same archived + is_unresolved + unpriced shape save_design
            # writes for a sentinel — required for
            # SouthbrookKitchenDesignLine._is_debris_signature() to
            # recognise this row (it keys off active=False AND
            # is_unresolved AND the position, exactly the shape below),
            # so it survives every RESET-TO-CANONICAL revival site
            # untouched, same as one written via save_design.
            vals["active"]        = False
            vals["is_unresolved"] = True
            vals["price_unit"]    = 0.0
            _logger.warning(
                "save_position: sentinel/out-of-bounds position on "
                "design=%s line=%s (layout_key=%s) — x=%r z=%r "
                "(threshold=%s). Persisting archived + is_unresolved + "
                "unpriced instead of an active line.",
                design.id, line.id, layout_key, final_x, final_z,
                _DEBRIS_THRESHOLD_IN,
            )
            if vals:
                line.write(vals)
            return {
                "ok":       True,
                "id":       line.id,
                "unplaced": True,
                "reason": (
                    "position sentinel/debris (>= %g in on x or z) — not "
                    "a real placement; saved archived, excluded from "
                    "totals and price" % _DEBRIS_THRESHOLD_IN
                ),
            }
        oob_reason = _out_of_room_bounds_reason(
            final_x, final_z, line.wall, design.room_width_in,
            design.room_depth_in,
        )
        if oob_reason:
            _logger.warning(
                "save_position: rejected out-of-room-bounds position on "
                "design=%s line=%s (layout_key=%s) — %s. Position not "
                "applied; any other fields in this same call (rotation/"
                "pinned) still are.",
                design.id, line.id, layout_key, oob_reason,
            )
            vals.pop("x_position_in", None)
            vals.pop("z_position_in", None)
            if vals:
                line.write(vals)
            return {
                "ok":     False,
                "id":     line.id,
                "reason": "out_of_room_bounds",
                "detail": oob_reason,
            }
        if vals:
            line.write(vals)
        return {"ok": True, "id": line.id}

    @http.route(
        "/southbrook_kitchen/configurator/delete_line",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def delete_line(self, design_id, layout_key):
        design = request.env["southbrook.kitchen.design"].browse(int(design_id or 0))
        if not design.exists() or not layout_key:
            return {"ok": False, "reason": "not_found"}
        try:
            design.check_access("write")
        except Exception:
            return {"ok": False, "reason": "forbidden"}
        line = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
                      and l.layout_key == layout_key
        )
        n = len(line)
        line.unlink()
        return {"ok": True, "removed": n}

    # ── Helpers ──────────────────────────────────────────────────────────────────
    def _serialize_design_lines(self, design):
        """Return this design's configurator-origin lines as the same list
        of item dicts load_design_lines has always returned.

        Factored out (C2) so save_design can hand the client fresh server
        geometry after a corner-resolution pass without duplicating the
        field list — keep this the single source of truth for the
        line → item-dict shape. Behaviour must stay byte-identical to the
        pre-factor inline loop in load_design_lines.

        Touch editing v1 (2026-08-02 spec §5) — the field-list body that
        used to live here moved onto the model as
        southbrook.kitchen.design._serialize_configurator_lines(), so
        SouthbrookKitchenDesignLine.action_move (a model method, called
        directly via /web/dataset/call_kw — it never passes through this
        controller) can return relaid lines in this exact shape without
        a second network hop. This method is now a thin delegate kept
        for every existing controller call site (load_design_lines,
        rearrange, save_design's corner-relay) — output is unchanged,
        pinned by test_save_load_identity.py / test_wall_persistence.py
        and friends, which never needed to change for this refactor.
        """
        return design._serialize_configurator_lines()

    def _validate_end_cap_panel_placement(self, items, tol=0.5):
        """Log a warning for any `cabinet_type == "panel"` item whose
        x_position_in does not correspond to a host cabinet's exposed
        side face.

        A valid end-cap position matches EITHER:
            host.x_position_in - panel.width_in   (left cap)
            host.x_position_in + host.width_in    (right cap)
        where host is any item with cabinet_type in ("base", "wall").

        This is warn-only (not blocking) because a sales rep may need to
        save mid-configuration. The client-side validator + the visual
        3D render are the primary UX signals. See kitchen_configurator.js
        _validateEndCapPlacement for the client twin.

        Filler panels ("filler") are auto-placed between cabinets by the
        /layout endpoint (see D7 filler strategies) and may contact a
        wall as a consequence of bridging the last gap; they do NOT
        require this validation.
        """
        panels = [it for it in (items or [])
                  if it.get("cabinet_type") == "panel"]
        if not panels:
            return
        hosts = [it for it in items
                 if it.get("cabinet_type") in ("base", "wall")]
        valid_x_positions = []
        for h in hosts:
            hx = float(h.get("x_position_in", 0) or 0)
            hw = float(h.get("width_in", 0) or 0)
            valid_x_positions.append(hx + hw)   # right side of host
            for p in panels:
                pw = float(p.get("width_in", 0) or 0)
                valid_x_positions.append(hx - pw)   # left side of host
        for p in panels:
            px = float(p.get("x_position_in", 0) or 0)
            if not any(abs(px - v) <= tol for v in valid_x_positions):
                _logger.warning(
                    "End cap panel product_id=%s layout_key=%s at "
                    "x_position_in=%s is not adjacent to any cabinet "
                    "side face. Panels must never attach to room walls; "
                    "they attach only to the left or right side of a "
                    "base or wall cabinet.",
                    p.get("product_id"), p.get("layout_key"), px,
                )

    def _first_product(self, env, cabinet_type):
        return env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet",    "=", True),
            ("product_tmpl_id.southbrook_cabinet_type",  "=", cabinet_type),
            ("sale_ok", "=", True),
        ], limit=1)

    def _layout_item(self, product, index, x, y, z, pricelist=False, partner=False):
        payload = self._product_payload(product, pricelist, partner)
        payload.update({
            "layout_key":    "%s-%d" % (payload["cabinet_type"], index + 1),
            "x_position_in": x,
            "y_position_in": y,
            "z_position_in": z,
        })
        return payload

    @staticmethod
    def _door_style_label(product):
        """The TRUE per-variant Door Style value, or None.

        X2 fix (2026-08-03) — the payload's legacy `door_style` field
        (`tmpl.southbrook_door_style`) is template-level and stale: it
        reads 'shaker' for 116 of 117 saleable cabinet variants
        regardless of each variant's actual attribute selection, so it
        cannot be used to tell same-template variants apart. The real
        value lives on the variant's own
        `product_template_attribute_value_ids`, matched by attribute
        NAME (case-insensitive) rather than attribute id, since the
        `product.attribute` id for "Door Style" is not stable across
        databases. Returns None (never a guessed/default style) when
        the variant carries no such attribute line at all — e.g. older,
        pre-sweep templates with no Door Style attribute.

        `ptav.name` on `product.template.attribute.value` is
        "AttrName: ValueName" (e.g. "Door Style: Shaker"), not the bare
        value — same gotcha already documented and worked around in
        `southbrook_kitchen_mrp/models/mrp_production.py`
        (`_sbk_attribute_value`) and `mi_engine.py`. Read the bare value
        off `product_attribute_value_id.name` instead, same fallback
        order as those call sites.
        """
        ptav = product.product_template_attribute_value_ids.filtered(
            lambda p: (p.attribute_id.name or "").strip().lower() == "door style"
        )[:1]
        if not ptav:
            return None
        return ptav.product_attribute_value_id.name or ptav.name or None

    def _product_payload(self, product, pricelist=False, partner=False):
        tmpl = product.product_tmpl_id
        price = self._channel_price(product, pricelist, partner)
        payload = {
            "product_id":    product.id,
            "template_id":   tmpl.id,
            "name":          product.display_name,
            "sku":           product.default_code or "",
            "cabinet_type":  tmpl.southbrook_cabinet_type,
            # Debt fix (2026-08-02) — explicit catalog-icon/3D-shape
            # override (product.template.southbrook_thumb_family). Read
            # by cabinet_thumb.esm.js's thumbFamily(), which honours it
            # outright over cabinet_type/SKU/name inference; False/unset
            # here (the default) leaves that inference completely
            # unchanged. This is the ONE catalog payload every consumer
            # of thumbFamily()/cabinetThumbSVG() in the client (catalog
            # list, catalog preview, /layout-generated items via the
            # `sku` fallback) is built from.
            "thumb_family":  tmpl.southbrook_thumb_family or False,
            # Task 11 (SBK-024) — this is THE payload feeding both
            # /products and /layout (which the client echoes back
            # verbatim into save_design's item.width_in), so it's the
            # primary fix site: the variant's own Width, not the
            # template's shared nominal default.
            "width_in":      placed_width_in(product) or 24.0,
            "height_in":     tmpl.southbrook_height_in or 34.5,
            "depth_in":      tmpl.southbrook_depth_in  or 24.0,
            "material":      tmpl.southbrook_material   or "white_melamine",
            "door_style":    tmpl.southbrook_door_style or "shaker",
            # X2 fix (2026-08-03) — additive, per-variant, real Door
            # Style attribute value (see _door_style_label). Distinct
            # from the stale `door_style` key above: this is None when
            # unknown, never a guessed default, and is what the catalog
            # list/preview badge is built from. Do NOT collapse the two
            # keys — `door_style` above is left untouched in case any
            # other consumer already depends on its (wrong) behaviour.
            "door_style_label": self._door_style_label(product),
            "price":         price,
            "list_price":    product.lst_price,
            "available_qty": product.qty_available,
            "bom_available": tmpl.southbrook_bom_available,
            "image_url":     "/web/image/product.product/%d/image_128" % product.id,
            "asset_url":     tmpl.southbrook_3d_asset_url or "",
        }
        # D17 — Tier-B additive: enrich with archetype taxonomy when
        # the southbrook_estimating archetype map is set. x_prodboard_
        # archetype_id is the canonical M2O to southbrook.cabinet.
        # archetype (223 records: body_class / collection / code from
        # the Prodboard manifest clone). Falls back silently when the
        # template isn't mapped to an archetype.
        # 2026-07-01 audit cleanup — southbrook_estimating became a
        # hard dep on 2026-06-28 (__manifest__.py:22-26), so
        # x_prodboard_archetype_id is guaranteed present at runtime.
        # The hasattr guard was defensive from before the manifest
        # hardened; dead code now.
        archetype = tmpl.x_prodboard_archetype_id or None
        if archetype:
            payload["archetype_code"]       = getattr(archetype, "code", "") or ""
            payload["archetype_body_class"] = getattr(archetype, "body_class", "") or ""
            payload["archetype_collection"] = getattr(archetype, "collection", "") or ""
            # Prefer the archetype-supplied canonical image when set —
            # vendor renders are richer than auto-generated thumbnails.
            canon_img = getattr(archetype, "canonical_image_url", "") or ""
            if canon_img:
                payload["archetype_image_url"] = canon_img
        return payload

    # ─── D3 — channel pricelist resolution ──────────────────────────────────────
    # Centralise partner/pricelist plumbing so /products + /layout
    # share the same resolver and the UI gets a single channel-meta
    # block to render the topbar badge.

    def _browse_partner(self, partner_id):
        """Resolve partner_id to a browsable res.partner while enforcing
        the caller's ACL + record rules. Empty recordset means 'no
        partner, use retail' — never raises so /products keeps
        rendering.

        v19.0.4.22.0 audit P1#5: pre-4.22 this method .sudo()'d the
        browse, letting any authenticated user enumerate every
        partner in the DB via /products or /layout and leak their
        .channel / pricing tier through _channel_meta +
        _resolve_pricelist. The check_access("read") gate degrades
        strangers to retail silently rather than raising (an error
        would leak existence and would break inventory rendering
        for logged-in customers whose portal_user has no partner
        access at all).
        """
        if not partner_id:
            return request.env["res.partner"]
        try:
            pid = int(partner_id)
        except (TypeError, ValueError):
            return request.env["res.partner"]
        if pid <= 0:
            return request.env["res.partner"]
        partner = request.env["res.partner"].browse(pid).exists()
        if not partner:
            return request.env["res.partner"]
        try:
            partner.check_access("read")
        except Exception:
            # Silent degrade — never raise AccessError; would leak
            # existence and would break configurator rendering.
            return request.env["res.partner"]
        return partner

    def _resolve_pricelist(self, partner):
        # Reuses the canonical southbrook_estimating dispatcher so the
        # configurator's live preview matches the price the Order
        # Builder + spec sheet PDF will charge. Falls back to retail
        # when no partner.
        # 2026-07-01 audit cleanup — southbrook_estimating is a hard
        # dep since 2026-06-28, so _resolve_channel_pricelist is
        # guaranteed present. Try/except kept as it protects against
        # partner-side data problems (missing channel, invalid tier).
        SaleOrder = request.env["sale.order"]
        try:
            return SaleOrder._resolve_channel_pricelist(partner)
        except Exception:
            pass
        # Fallback path: partner's property pricelist, else env default.
        if partner:
            pl = partner.property_product_pricelist
            if pl:
                return pl
        return request.env["product.pricelist"].search([], limit=1)

    def _channel_price(self, product, pricelist, partner):
        if not pricelist:
            return product.lst_price
        try:
            # v19 unified API: pricelist._get_product_price(product, qty, partner)
            return pricelist._get_product_price(product, 1.0, partner or False)
        except Exception:
            return product.lst_price

    def _channel_meta(self, partner, pricelist):
        # Compact dict the UI renders into a topbar badge.
        channel = (partner and partner.channel) or "retail"
        # Best-effort tier suffix for tradesperson.
        suffix = ""
        if channel == "tradesperson" and partner:
            tier = getattr(partner, "tradesperson_tier", False)
            if tier:
                suffix = " T%s" % tier
        return {
            "partner_id":     (partner and partner.id) or False,
            "partner_name":   (partner and partner.display_name) or "",
            "channel":        channel,
            "channel_label":  self._CHANNEL_LABELS.get(channel, channel.title()) + suffix,
            "pricelist_id":   (pricelist and pricelist.id) or False,
            "pricelist_name": (pricelist and pricelist.display_name) or "",
            "currency_id":    (pricelist and pricelist.currency_id.id) or False,
            "currency_symbol": (pricelist and pricelist.currency_id.symbol) or "$",
        }

    _CHANNEL_LABELS = {
        "retail":       "Retail",
        "dealer":       "Dealer -50%",
        "tradesperson": "Contractor",
        "kd":           "KD",
        "bigbox":       "Big-Box",
        "refacing":     "Refacing",
    }

    # ─── D4 — auto-name + per-user sticky room defaults ─────────────────────────
    @http.route(
        "/southbrook_kitchen/configurator/user_defaults",
        type="jsonrpc", auth="user", methods=["POST"],
    )
    def user_defaults(self, save=False, width=None, depth=None, height=None):
        """Read or write the current user's preferred room dimensions.

        Stored as ir.config_parameter keyed by uid so each rep gets
        their own sticky defaults; no res.users schema bump required.

        P0 fix (2026-08-02) — the FALLBACKS below (used only when this
        user has never saved a preference) used to be 12in width x 24in
        depth. `_loadUserDefaults()` calls this endpoint unconditionally
        from "+ New Design", so every brand-new user — before they ever
        touch the Room panel — got seeded into a 12x24 room, which is
        smaller than a single 24in-deep base cabinet's footprint. Live
        evidence: real design records named "Walk-in - 12x120" and
        "Walk-in - 12x42" with 0 cabinets — the rep opened a new design,
        the 12x24 fallback room silently made every cabinet they tried
        to add un-placeable, and they gave up before ever resizing the
        Room panel. New fallback (144in x 120in x 96in, i.e. a
        12ft x 10ft room, 8ft ceiling) is merely a saner FIRST GUESS —
        it changes nothing for any user who has ever saved a preference
        (the `if save:` block above already persisted their real value,
        and `_read` returns THAT before ever reaching this default).
        """
        ICP = request.env["ir.config_parameter"].sudo()
        uid = request.env.uid
        keys = {
            "w": "southbrook_kitchen.default_room_width_in.%d"  % uid,
            "d": "southbrook_kitchen.default_room_depth_in.%d"  % uid,
            "h": "southbrook_kitchen.default_room_height_in.%d" % uid,
        }
        if save:
            if width  is not None: ICP.set_param(keys["w"], str(width))
            if depth  is not None: ICP.set_param(keys["d"], str(depth))
            if height is not None: ICP.set_param(keys["h"], str(height))

        def _read(k, default):
            try:
                return float(ICP.get_param(keys[k], default))
            except Exception:
                return default
        return {
            "width":  _read("w", 144),
            "depth":  _read("d", 120),
            "height": _read("h", 96),
        }

    def _auto_name(self, partner, room_w, room_d):
        """First-save (and resize-regeneration) name generator. Output
        format/behaviour is UNCHANGED from before this fix — this just
        delegates the actual string-building to auto_name_str() (the
        module-level, migration-shared single source of truth) instead
        of formatting the literal inline.
        """
        from odoo import fields as _fields
        today = _fields.Date.context_today(request.env.user).strftime("%Y-%m-%d")
        pname = (partner and partner.display_name) or "Walk-in"
        return auto_name_str(pname, room_w, room_d, today)
