# SPDX-License-Identifier: LGPL-3.0-only
"""
sale.order extension — channel-to-pricelist resolution dispatcher.

This file IS custom routine #3 per Build Spec section 4. Adding any
business logic beyond _resolve_channel_pricelist requires PUNCHLIST
justification.

The dispatcher reads partner.channel (and partner.tradesperson_tier
when applicable) and returns the matching pricelist record. Called
from a default-getter on pricelist_id so the user can still manually
override the resolved pricelist post-creation.

NF5 behaviour: when channel=tradesperson and tier is null, returns
the base pricelist_tradesperson (cost+5% floor, no tier discount).
A soft warning is logged (not raised) so order creation isn't
blocked but the operations team is alerted.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # 2026-07-01 E2E audit follow-up — BoM Preview HTML summary
    # (docs/E2E_AUDIT_SOUTHBROOK_ESTIMATING_2026-07-01.md §6.1 #1).
    # A single Html field beats a second `order_line` embed on the
    # form: two embeds of the same one2many collide in Odoo's Form
    # helper (`field_info` map is per-field, not per-view-key) and
    # break Form-driven tests. Rendering per-line summaries into an
    # HTML blob keeps the primary Order Lines tab clean.
    sb_bom_preview_html = fields.Html(
        string="BoM Preview",
        compute="_compute_sb_bom_preview_html",
        sanitize=False,  # we generate every tag; no user input
        readonly=True,
        store=False,
    )

    @api.depends("order_line", "order_line.product_id", "order_line.sb_bom_id",
                 "order_line.sb_panel_count", "order_line.sb_door_count",
                 "order_line.product_uom_qty")
    def _compute_sb_bom_preview_html(self):
        for order in self:
            if not order.order_line:
                order.sb_bom_preview_html = (
                    "<div class='text-muted'>"
                    "No configured lines yet — add a product to see its "
                    "BoM preview here."
                    "</div>"
                )
                continue
            rows = []
            for line in order.order_line:
                bom_name = (
                    line.sb_bom_id.display_name if line.sb_bom_id
                    else "<em class='text-muted'>no BoM defined</em>"
                )
                product_name = (
                    line.product_id.display_name if line.product_id
                    else "(unassigned)"
                )
                rows.append(
                    "<tr>"
                    "<td>%(seq)d</td>"
                    "<td>%(product)s</td>"
                    "<td>%(bom)s</td>"
                    "<td class='text-end'>%(qty).2f</td>"
                    "<td class='text-end'>%(panels)d</td>"
                    "<td class='text-end'>%(doors)d</td>"
                    "</tr>" % dict(
                        seq=line.sequence or 0,
                        product=product_name,
                        bom=bom_name,
                        qty=line.product_uom_qty or 0.0,
                        panels=line.sb_panel_count or 0,
                        doors=line.sb_door_count or 0,
                    )
                )
            order.sb_bom_preview_html = (
                "<table class='table table-sm o_list_view'>"
                "<thead><tr>"
                "<th>#</th>"
                "<th>Product</th>"
                "<th>Manufacturing BoM</th>"
                "<th class='text-end'>Qty</th>"
                "<th class='text-end'>Panels</th>"
                "<th class='text-end'>Doors</th>"
                "</tr></thead>"
                "<tbody>%s</tbody>"
                "</table>" % "".join(rows)
            )

    # G14 + G17 (customer-flow JTBD gap 2026-06-01) — customer-visible
    # progress timeline. Stamped by the portal 'Request a Price' action
    # so the StagePipeline can show 'Submitted on <date>' instead of
    # an undated chip. Phase 3 polish adds southbrook_in_production_date
    # populated by the MO-creation hook.
    southbrook_submitted_date = fields.Datetime(
        string="Submitted for Pricing",
        readonly=True,
        copy=False,
        tracking=True,
        help=(
            "Set automatically the first time the customer (or sales "
            "rep on the customer's behalf) submits a draft order for "
            "pricing review via the portal 'Request a Price' action."
        ),
    )

    # Channel → pricelist xml_id mapping (Q1). Centralised alongside the
    # _TRADESPERSON_TIER_PRICELISTS table below for symmetry. Tradesperson
    # is the one channel that needs a sub-dispatcher (NF5 tier resolution);
    # the others are flat direct lookups.
    _CHANNEL_PRICELISTS = {
        "retail":   "southbrook_estimating.pricelist_retail",
        "dealer":   "southbrook_estimating.pricelist_dealer",
        "kd":       "southbrook_estimating.pricelist_kd",
        "bigbox":   "southbrook_estimating.pricelist_bigbox",
        "refacing": "southbrook_estimating.pricelist_refacing",
    }
    _FALLBACK_PRICELIST_XML_ID = "southbrook_estimating.pricelist_retail"

    @api.model
    def _resolve_channel_pricelist(self, partner):
        """Return the product.pricelist matching the partner's channel.

        Custom routine #3 per Build Spec section 4. Dispatch-only — no
        business logic beyond mapping channel -> pricelist xml_id.

        Args:
            partner: a res.partner record.

        Returns:
            A product.pricelist record. Falls back to retail when no
            partner is supplied or the channel is unrecognised.
        """
        if not partner:
            return self.env.ref(self._FALLBACK_PRICELIST_XML_ID)

        channel = partner.channel or "retail"

        if channel == "tradesperson":
            return self._resolve_tradesperson_pricelist(partner)

        return self.env.ref(
            self._CHANNEL_PRICELISTS.get(channel, self._FALLBACK_PRICELIST_XML_ID)
        )

    # Tier → pricelist xml_id mapping (NF5). Adding a tier 4 means one
    # row here, one row in res.partner.tradesperson_tier selection, one
    # new pricelist record. Centralising the table makes the contract obvious.
    _TRADESPERSON_TIER_PRICELISTS = {
        "1": "southbrook_estimating.pricelist_tradesperson_tier_1",
        "2": "southbrook_estimating.pricelist_tradesperson_tier_2",
        "3": "southbrook_estimating.pricelist_tradesperson_tier_3",
    }

    def _resolve_tradesperson_pricelist(self, partner):
        """Pick the right tradesperson tier sub-pricelist (NF5).

        Returns the tier-specific pricelist when the partner has a
        tradesperson_tier set; falls back to the base (cost+5% floor,
        no tier discount) with a soft warning otherwise.
        """
        tier_xml_id = self._TRADESPERSON_TIER_PRICELISTS.get(
            partner.tradesperson_tier
        )
        if tier_xml_id:
            return self.env.ref(tier_xml_id)

        _logger.warning(
            "southbrook: partner %s has channel=tradesperson but no "
            "tradesperson_tier set; falling back to base pricelist "
            "(cost+5%% floor, no tier discount).",
            partner.display_name,
        )
        return self.env.ref("southbrook_estimating.pricelist_tradesperson")

    # Default-getter — on new sale.order, auto-resolve from partner
    # without preventing the user from overriding pricelist_id manually.
    @api.onchange("partner_id")
    def _onchange_partner_id_southbrook_pricelist(self):
        for order in self:
            if not order.partner_id:
                continue
            resolved = order._resolve_channel_pricelist(order.partner_id)
            if resolved:
                order.pricelist_id = resolved.id

    # ------------------------------------------------------------------
    # C1 fix (2026-07-06): the "Configure Product" header button.
    #
    # The stock OCA flow (product_configurator_sale/models/sale.py) opens
    # the product.configurator wizard on a VIRTUAL (unsaved) record. On that
    # record `product_tmpl_id` is a delegated (_inherits product.config.
    # session) field, which the Odoo 19 web client renders disabled/greyed —
    # the user can't pick a template and Next raises the create() guard. The
    # earlier (2026-07-04) attempt only flipped allow_preset_selection and
    # only asserted on the returned action dict — it never rendered the view,
    # so the greyed picker survived QA. Confirmed on the dev stack: the
    # picker is visible & editable & non-empty at the ORM level, so the fault
    # is purely the client rendering of the delegated field on a virtual
    # record. And a template-less wizard can't be pre-created (config.session
    # product_tmpl_id is NOT NULL), so the virtual record is unavoidable via
    # that path.
    #
    # So we bypass the configurator's own picker: open a small transient
    # (southbrook.config.template.picker) whose product_tmpl_id is a PLAIN
    # Many2one (no delegation → nothing to grey). On confirm it hands off to
    # product.template.create_config_wizard(...) — the proven path used by
    # every other entry point, which persists a real wizard with the template
    # pre-set, so the greyed-virtual-record problem cannot recur. The
    # create() guard in models/product_configurator.py remains as a backstop.
    def action_config_start(self):
        self.ensure_one()
        return self.env["southbrook.config.template.picker"].action_open_for_order(
            self.id
        )

    # ------------------------------------------------------------------
    # QA follow-up (2026-07-05): hard config-rule validation, shared
    # between the portal preflight check (southbrook_estimating_website
    # controllers/main.py, _southbrook_collect_validation) and
    # action_confirm() below. Before this, the hard-severity checks
    # (Rule 1 series/door, Rule 2 Maple/Contractor) only lived in the
    # portal controller and only gated the customer-facing "Send to
    # Manufacturing" button client-side — a sales rep confirming the
    # SAME order from the ordinary backend Sales Order form had no
    # equivalent guard and could push an invalid combination straight
    # into a real Manufacturing Order. This is the single source of
    # truth both surfaces now call; the portal controller keeps its own
    # per-line loop only for the soft/info severities (width/door-count
    # suggestions, Maple lead-time note), which never blocked anything
    # and don't need a shared home.
    def southbrook_hard_validation_issues(self):
        """Return a list of {code, line_id, message} dicts — hard-
        severity config rule violations that must block confirmation.
        Empty list means the order is clean. Mirrors CLAUDE.md §5 rules
        1 and 2 (declarative data enforces selection; this is a defence-
        in-depth check against combinations that predate a rule, or
        were entered before the rule existed / via direct ORM writes)."""
        self.ensure_one()
        issues = []
        for line in self.order_line:
            if not line.product_id:
                continue
            # IMPORTANT (2026-07-05 code-review fix): scan ONLY the
            # variant's real product.template.attribute.value records —
            # the authoritative configuration — never line.name. line.name
            # is user-editable free text; a rep typing "Contractor: rush
            # job" into the description of a legitimate woodgrain-door line
            # must NOT hard-block Confirm. The portal preflight collector
            # keeps its own name-based SOFT checks (advisory, non-blocking)
            # where a false positive is harmless; a blocking guard cannot
            # afford them.
            attr_tokens = []
            for ptav in line.product_id.product_template_attribute_value_ids:
                attr_tokens.append((ptav.name or "").lower())

            def _has_token(*tokens):
                return any(
                    t in v for v in attr_tokens for t in tokens)

            if _has_token("contractor") and _has_token(
                    "five-piece", "5-piece", "woodgrain"):
                issues.append({
                    "code": "series_door_incompatible",
                    "line_id": line.id,
                    "message": (
                        f"{line.name}: Contractor series only allows "
                        "the white thermofoil slab door."
                    ),
                })
            if _has_token("elegance") and _has_token("slab", "thermofoil"):
                issues.append({
                    "code": "series_door_incompatible",
                    "line_id": line.id,
                    "message": (
                        f"{line.name}: Elegance series uses five-piece "
                        "woodgrain doors only."
                    ),
                })
            if _has_token("maple") and _has_token("contractor"):
                issues.append({
                    "code": "box_series_incompatible",
                    "line_id": line.id,
                    "message": (
                        f"{line.name}: Maple carcass not available on "
                        "Contractor series."
                    ),
                })
        return issues

    # ------------------------------------------------------------------
    # Analytics capture (NF1 — Build Spec section 8 "AI data spine")
    # ------------------------------------------------------------------
    # Fire the southbrook.order.analytics.capture() hook at confirm-time.
    # Idempotent; safe to re-confirm. NF1 carve-out: this is data capture,
    # not business logic — does not bump the 7-routine custom register.
    def action_confirm(self):
        for order in self:
            issues = order.southbrook_hard_validation_issues()
            if issues:
                raise UserError(_(
                    "This order has configuration rule violations and "
                    "cannot be confirmed:\n\n%s"
                ) % "\n".join("- %s" % i["message"] for i in issues))
        result = super().action_confirm()
        Analytics = self.env["southbrook.order.analytics"]
        for order in self:
            Analytics.capture(order)
        return result

    # ------------------------------------------------------------------
    # NF6 — Image Floor iterative-design pattern (Case Study section 3.A)
    # ------------------------------------------------------------------
    # parent_order_id + version + action_duplicate_as_draft give reps
    # the "Duplicate as Draft" affordance that Image Floor's 3-visit flow
    # needs: same kitchen revised 3 times, each saved as a new draft with
    # the prior version linked. Free side-effect: full revision history
    # walkable via parent_order_id chain.
    #
    # Schema only. The view button + action wiring lands in
    # views/sale_order_views.xml.
    parent_order_id = fields.Many2one(
        "sale.order",
        string="Parent Order (Duplicated From)",
        ondelete="set null",
        copy=False,
        help=(
            "When this order was created via 'Duplicate as Draft', this "
            "Many2one points at the prior version. NF6 — Image Floor "
            "iterative-design pattern."
        ),
    )
    version = fields.Integer(
        string="Version",
        default=1,
        copy=False,
        help=(
            "Auto-incremented by action_duplicate_as_draft. The Image "
            "Floor flow typically reaches v3 before final confirmation."
        ),
    )

    # ------------------------------------------------------------------
    # Room-First UX (Phase 1.3) — O2m to southbrook.room + smart button.
    # ------------------------------------------------------------------
    # copy=False per NF6 — Duplicate-as-Draft (v1 → v2) typically wants
    # a fresh room measurement, not a carbon-copy. If a future workflow
    # needs the room cloned across versions, flip this and add an
    # explicit override on action_duplicate_as_draft.
    room_ids = fields.One2many(
        "southbrook.room", "order_id", string="Rooms", copy=False)
    room_count = fields.Integer(
        compute="_compute_room_count", store=False)

    @api.depends("room_ids")
    def _compute_room_count(self):
        for rec in self:
            rec.room_count = len(rec.room_ids)

    def action_open_southbrook_room(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "southbrook_estimating.action_southbrook_room")
        if len(self.room_ids) == 1:
            action.update({
                "views": [(False, "form")],
                "res_id": self.room_ids.id,
            })
        else:
            action["domain"] = [("order_id", "=", self.id)]
        return action

    # ------------------------------------------------------------------
    # 3D kitchen-run viewport — Track 1 commits 6 + 7 (2026-05-30).
    #
    # Returns a multi-cabinet 3D payload spanning every order_line whose
    # product matches one of the 12 Q8-locked SB cabinet SKUs.
    #
    # Track 1 commit 7 layout — zone-aware X positioning:
    #
    #   Real kitchen elevation looks like:
    #
    #       [W1][W2][W3]                    ← wall cabs at Y=1400mm
    #       [B1][B2][B3] [T1]               ← base + tall at Y=0mm
    #                          [WT1]        ← worktop at Y=762mm
    #                                       ← island at Z=-2500mm
    #
    #   Each zone gets its OWN cumulative X cursor so wall cabinets
    #   share the X range with the base cabinets they sit above
    #   (visually aligned in elevation), while tall extends the ground
    #   cursor (continues the run to the right), and island/other use
    #   distinct Z offsets so they don't collide visually with the
    #   main run.
    #
    # The same _SKU_DEFAULTS / _cut_list_to_3d_payload pipeline that
    # drives the single-cabinet wizard viewport also drives this one,
    # so a change to the named geometric constants (BOX_TH, BACK_TH,
    # DOOR_TH, TOEKICK_H) propagates to BOTH views — no fork.
    # ------------------------------------------------------------------
    # Zone → (cursor_name, y_floor_mm, z_offset_mm). Zones sharing a
    # cursor_name accumulate along the same X axis.
    _ZONE_LAYOUT = {
        # GROUND cursor: base + tall + accessory continue the floor run
        # left to right. Wall cabinets get their own cursor so they
        # share the X range with their base partners, not extending
        # past them.
        "base_run":  ("ground", 0,    0),
        "wall":      ("wall",   1400, 0),       # wall mount height
        "tall":      ("ground", 0,    0),       # extends ground run
        "island":    ("island", 0,    -2500),   # separate Z plane
        "accessory": ("ground", 0,    0),
        "other":     ("other",  0,    -3000),   # tucked away
    }
    # Worktop family override — countertop sits at Y=762mm regardless
    # of zone. T1C7 dispatches by FAMILY for worktops; the zone field
    # is still respected for everything else.
    _WORKTOP_Y_FLOOR = 762
    _WORKTOP_CURSOR = "ground"

    # Legacy alias for T1C6 callers (just zone → y_floor). Kept for
    # backwards-compat with any test that grew up against commit 6.
    _ZONE_FLOOR_Y = {z: l[1] for z, l in _ZONE_LAYOUT.items()}

    def get_kitchen_3d_payload(self):
        """Multi-cabinet 3D payload for the OWL kitchen viewport.

        Loop over self.order_line in display order. For each line whose
        product matches an SB-* SKU, compute its single-cabinet panels at
        the origin (via the existing config_session pipeline), then
        translate every panel by (x_offset, y_floor, 0):
          • x_offset accumulates as cabinets are placed — cumulative
            X grows by the cabinet's width per placement.
          • y_floor comes from _ZONE_FLOOR_Y[line.zone].

        Concatenates all per-cabinet panels into one list with prefixed
        names so the OWL component can address each line's panels
        independently in future highlight-on-hover work.

        Returns the same payload shape as
        product.config.session.get_3d_payload — the OWL component
        consumes it without per-payload code paths.
        """
        self.ensure_one()
        Session = self.env["product.config.session"]
        Bom = self.env["mrp.bom"]
        sku_defaults = Session._SKU_DEFAULTS

        # Per-zone X cursors — T1C7. Each cursor accumulates the
        # cabinet widths placed under it. Zones in _ZONE_LAYOUT
        # share a cursor name when they belong on the same horizontal
        # axis (e.g. base_run, tall, accessory all advance "ground").
        cursors = {"ground": 0, "wall": 0, "island": 0, "other": 0}
        max_h = 0             # tallest cabinet's top, for camera framing
        max_d = 0             # deepest cabinet, for camera framing
        max_z_back = 0        # most negative Z reached (for camera + bounds)
        all_panels = []
        # T1C8 — per-line index for hover tooltip in the OWL component.
        # The frontend reads this map by line.id (extracted from panel
        # name prefix "L{id}_") to display "Line N · Family · SKU"
        # in the toolbar while the mouse is over a cabinet.
        lines_index = {}
        sequence = 0

        for line in self.order_line:
            tmpl = line.product_id.product_tmpl_id if line.product_id else None
            # Task 23 (SBK-037) review finding 1, item 1 — same fallback
            # product_config_line.py's _extract_cabinet_inputs() uses:
            # tmpl.default_code reads blank for a genuinely multi-variant
            # template (Odoo only mirrors it for a single variant), which
            # is exactly right post-repair for a split SB-BASE-2DR/
            # SB-WALL-2DR-class template — but a naive read here fell
            # straight through to the generic-box fallback below instead
            # of resolving the real SKU row. Session._resolve_template_sku
            # accepts an explicit tmpl override precisely for callers like
            # this one that don't have a product.config.session instance.
            sku = Session._resolve_template_sku(tmpl) if tmpl else None
            row = sku_defaults.get(sku) if sku else None
            if not row:
                # QA Bug 3 fix (2026-07-04): used to `continue` here,
                # silently dropping any line whose product_tmpl_id.
                # default_code isn't one of the 12 hardcoded Q8 SKUs —
                # e.g. every cabinet placed via the SEPARATE full-screen
                # "Open in 3D" configurator (southbrook_kitchen_3d_
                # configurator), whose catalog is flagged
                # southbrook_is_cabinet=True and uses its own SKU codes
                # (B24, DB24, SB30, ...) with no overlap with this
                # table. An order made entirely of THOSE lines had every
                # single one skipped, leaving all_panels empty and the
                # embedded preview rendering a blank floor/lighting-only
                # scene — even though every line was fully configured
                # and priced correctly everywhere else in Odoo. This is
                # documented as a known limitation in
                # southbrook_elearning_internal's own training slides.
                #
                # Fall back to a generic box instead of skipping,
                # mirroring the SAME "hard defaults when no SKU match"
                # pattern _extract_cabinet_inputs() (product_config_line
                # .py) already uses for the single-cabinet wizard
                # preview — so an unrecognized line still renders SOME
                # representative cabinet rather than vanishing.
                #
                # Only do this for lines that plausibly ARE cabinets —
                # never for section/note lines, and never for an
                # ordinary non-cabinet product (delivery fee, install
                # labour, etc.), which should stay invisible in a 3D
                # scene. southbrook_is_cabinet is defined by the
                # (optionally installed, not a hard dependency)
                # southbrook_kitchen_3d_configurator addon — getattr
                # keeps this addon installable standalone.
                if line.display_type:
                    continue  # section/note lines never render as a box
                is_cabinet_like = bool(tmpl) and bool(
                    getattr(tmpl, "southbrook_is_cabinet", False))
                if not is_cabinet_like:
                    continue  # not a recognized SB SKU, not flagged as a cabinet either
                row = ("base", 1, 0, 609, 762, 609)

            fam, doors, drawers, w, h, d = row

            # T1C7 dispatch: worktops sit on the counter (Y=762) and
            # share the ground X cursor regardless of zone. Everything
            # else dispatches by zone via _ZONE_LAYOUT.
            if fam == "worktop":
                cursor_name = self._WORKTOP_CURSOR
                y_floor = self._WORKTOP_Y_FLOOR
                z_offset = 0
            else:
                zone = line.zone or "base_run"
                cursor_name, y_floor, z_offset = self._ZONE_LAYOUT.get(
                    zone, ("ground", 0, 0),
                )

            cab_inputs = {
                "width_mm":   w,
                "height_mm":  h,
                "depth_mm":   d,
                "family":     fam,
                "door_count": doors,
                "drawer_count": drawers,
                "finished_sides": "none",
            }
            cut = Bom._compute_panel_dimensions(
                width_mm=w, height_mm=h, depth_mm=d,
                family=fam, door_count=doors, drawer_count=drawers,
                finished_sides="none",
            )
            single = Session._cut_list_to_3d_payload(cab_inputs, cut)

            x_offset = cursors[cursor_name] + w / 2
            line_tag = f"L{line.id}_"
            for panel in single["panels"]:
                p = dict(panel)
                p["pos"] = {
                    "x": panel["pos"]["x"] + x_offset,
                    "y": panel["pos"]["y"] + y_floor,
                    "z": panel["pos"]["z"] + z_offset,
                }
                p["name"] = line_tag + panel["name"]
                all_panels.append(p)

            cursors[cursor_name] += w
            cabinet_top = (h if fam != "worktop" else 25) + y_floor
            max_h = max(max_h, cabinet_top)
            max_d = max(max_d, d)
            # Z bounds — most-negative z (back of farthest cabinet) used
            # for camera framing + scene bounds.
            cabinet_back_z = z_offset - d
            if cabinet_back_z < max_z_back:
                max_z_back = cabinet_back_z

            # T1C8 — line index entry.
            sequence += 1
            lines_index[str(line.id)] = {
                "id": line.id,
                "sequence": sequence,
                "sku": sku or "",
                "family": fam,
                "zone": line.zone or "base_run",
                "width_mm": w,
                "height_mm": h,
                "depth_mm": d,
                "product_name": (
                    tmpl.display_name if tmpl else ""
                ),
            }

        # Widest cursor determines the camera framing width.
        widest = max(cursors.values())
        cumulative_x = widest

        # Frame the camera around the full kitchen run.
        if cumulative_x == 0:
            # No SB cabinets on this order — return an empty scene with
            # a sane default camera so the viewport still renders the
            # floor and shadow without errors.
            return {
                "panels": [],
                "metadata": {
                    "line_count": 0,
                    "kitchen_width_mm": 0,
                    "kitchen_height_mm": 0,
                    "kitchen_depth_mm": 0,
                },
                "camera": {
                    "target":   [0, 400, 0],
                    "position": [2500, 1800, 3500],
                },
                "bounds": {"min": [-500, 0, -500], "max": [500, 800, 0]},
            }

        kitchen_w = cumulative_x
        # Z extent — from the farthest-back cabinet (negative Z) to
        # the front of the deepest cabinet at the main row (~0).
        scene_z_back = max_z_back - max_d
        scene_z_front = 0
        scene_z_centre = (scene_z_back + scene_z_front) / 2

        cam_target = [kitchen_w / 2, max_h / 2, scene_z_centre]
        # Camera distance scales with kitchen footprint so wide runs
        # still frame cleanly without manual zoom.
        cam_dist = max(kitchen_w, max_h, abs(scene_z_back)) * 1.8
        cam_position = [
            kitchen_w / 2,
            max_h * 1.4,
            scene_z_front + cam_dist,
        ]

        return {
            "panels": all_panels,
            "metadata": {
                # Same fallback as the main render loop above (Task 23
                # review finding 1, item 1) — a raw default_code read
                # here would under-count post-repair multi-variant
                # templates even though the loop above correctly
                # renders their panels.
                "line_count": len([
                    l for l in self.order_line
                    if l.product_id
                    and Session._resolve_template_sku(
                        l.product_id.product_tmpl_id)
                    in sku_defaults
                ]),
                "kitchen_width_mm":  kitchen_w,
                "kitchen_height_mm": max_h,
                "kitchen_depth_mm":  max_d,
                "cursors": dict(cursors),  # diagnostic: per-zone X totals
                "lines": lines_index,      # T1C8 — per-line lookup for hover
            },
            "camera": {"target": cam_target, "position": cam_position},
            "bounds": {
                "min": [0, 0, scene_z_back],
                "max": [kitchen_w, max_h, scene_z_front],
            },
        }

    # ------------------------------------------------------------------
    # Phase 3 Sprint C3 — version-history chain walker.
    # ------------------------------------------------------------------
    def _southbrook_history_chain(self, max_depth=20):
        """Walk parent_order_id backwards and return the ancestry.

        Returns a list of dicts (newest first) covering EVERY order in
        the chain — including self. Caller renders it as a timeline.

        max_depth guards against pathological cycles; 20 is enough for
        any plausible iterative-design workflow (Image Floor's typical
        v3 is the high-water mark in observed practice).
        """
        self.ensure_one()
        chain = []
        seen = set()
        cur = self
        while cur and cur.id not in seen and len(chain) < max_depth:
            seen.add(cur.id)
            chain.append({
                "id": cur.id,
                "name": cur.name,
                "version": int(cur.version or 1),
                "state": cur.state,
                "amount_total": float(cur.amount_total or 0.0),
                "date_order": (
                    cur.date_order.isoformat() if cur.date_order else None
                ),
                "is_current": cur.id == self.id,
            })
            cur = cur.parent_order_id
        return chain

    def action_duplicate_as_draft(self):
        """Create a new draft sale.order copied from this one (NF6).

        Copies all order lines (preserving product.config.session refs
        where applicable), links parent_order_id, increments version,
        stays in draft state ('draft' / 'sent'). Safe to chain — v3
        duplicates v2 which duplicates v1.

        Returns the action descriptor that opens the new order's form.
        """
        self.ensure_one()
        new_order = self.copy({
            "parent_order_id": self.id,
            "version": self.version + 1,
            "state": "draft",
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": new_order.id,
            "view_mode": "form",
            "target": "current",
            "name": f"{new_order.name} (v{new_order.version})",
        }
