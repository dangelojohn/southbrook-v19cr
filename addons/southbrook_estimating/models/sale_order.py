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
import html
import logging

from markupsafe import Markup

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

    # ------------------------------------------------------------------
    # H-6 / H-9 (2026-08-05) — the BoM Preview must report the REAL BOM.
    #
    # This used to render sale.order.line.sb_panel_count / sb_door_count
    # — a SYNTHETIC geometry formula (southbrook_dims.panel_cut_list,
    # sale_order_line.py's _compute_sb_panel_rollup) that never reads the
    # resolved mrp.bom at all. Every line showed "8 Panels / 1 Door",
    # including an appliance-space void whose real BOM has zero lines.
    #
    # sb_panel_count/sb_door_count FIELDS are left completely untouched
    # (still computed exactly as before) — grep confirms other real
    # consumers read them directly and must keep working:
    #   - southbrook_estimating/reports/door_order.xml (shop-facing Door
    #     Order report; per-series door-count totals)
    #   - southbrook_estimating/models/southbrook_order_analytics.py
    #     (order-level panel_count/door_count rollup capture)
    #   - southbrook_room_capture (QR scan part/staff lookups + its
    #     qr_scan.xml display)
    #   - southbrook_estimating_website/controllers/main.py +
    #     portal_boot.esm.js (customer portal per-line display) and its
    #     own test (test_bom_payload_per_line.py)
    # Only THIS preview (the Order Builder "BoM Preview" tab) changes
    # what it renders.
    # ------------------------------------------------------------------

    # Cabinet-type flags meaning "this line is not manufactured" — an
    # appliance-space stand-in, a filler, or a decorative panel. The
    # field lives on southbrook_kitchen_3d_configurator (optional dep,
    # same guarded-getattr pattern get_kitchen_3d_payload already uses
    # for southbrook_is_cabinet below) — southbrook_estimating must stay
    # installable without that module.
    _SB_NOT_MANUFACTURED_CABINET_TYPES = {"appliance", "filler", "panel"}

    # Known auto-generated BoM code prefixes (southbrook_kitchen_mrp's
    # mrp_bom_catalog.py generator) — a shop-facing preview must never
    # surface these verbatim ("CatalogAutoBOM-Variant-SB-BASE-2DR..."
    # reads as internal plumbing, not a part number).
    _SB_BOM_AUTOGEN_CODE_PREFIXES = ("CatalogAutoBOM-", "KitchenAutoSeed-")

    def _sb_bom_shop_label(self, bom):
        """Shop-facing label for a resolved mrp.bom.

        Core mrp's own display_name mixes the raw `code` field into the
        name (which is how "CatalogAutoBOM-Variant-SB-BASE-2DR..." ends
        up on screen) — format our own label instead: the manufactured
        product's name, with the BoM's own code appended in parentheses
        ONLY when that code doesn't look like one of this codebase's
        known auto-generated stub prefixes.
        """
        if not bom:
            return ""
        name = (
            bom.product_id.display_name if bom.product_id
            else (bom.product_tmpl_id.display_name
                  if bom.product_tmpl_id else "")
        ) or "Unnamed BoM"
        code = (bom.code or "").strip()
        if code and not code.startswith(self._SB_BOM_AUTOGEN_CODE_PREFIXES):
            return "%s (%s)" % (name, code)
        return name

    @api.depends(
        "order_line", "order_line.product_id", "order_line.sb_bom_id",
        "order_line.sb_bom_id.bom_line_ids", "order_line.product_uom_qty",
    )
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
            for idx, line in enumerate(order.order_line, start=1):
                product_name = (
                    line.product_id.display_name if line.product_id
                    else "(unassigned)"
                )
                tmpl = (
                    line.product_id.product_tmpl_id
                    if line.product_id else False
                )
                not_manufactured = bool(tmpl) and (
                    getattr(tmpl, "southbrook_cabinet_type", False)
                    in self._SB_NOT_MANUFACTURED_CABINET_TYPES
                )
                bom = line.sb_bom_id
                if not_manufactured:
                    # H-6/H-9: an appliance-space / filler / decorative-
                    # panel line is CORRECTLY not manufactured — zero
                    # components is the honest answer, not a data gap.
                    # Render it as its own thing, never as a cutlist row.
                    rows.append(
                        "<tr class='text-muted'>"
                        "<td>%(idx)d</td>"
                        "<td>%(product)s</td>"
                        "<td class='text-end'>%(qty).2f</td>"
                        "<td colspan='2'>"
                        "<em>Not manufactured</em> — appliance space / "
                        "filler / decorative panel"
                        "</td>"
                        "</tr>" % dict(
                            idx=idx,
                            product=html.escape(product_name),
                            qty=line.product_uom_qty or 0.0,
                        )
                    )
                    continue
                bom_lines = bom.bom_line_ids if bom else bom
                if not bom or not bom_lines:
                    # Empty/missing BoM is a real data gap — align with
                    # the tab's existing footnote language, never a
                    # fabricated count.
                    bom_cell = "<em class='text-muted'>no BoM defined yet</em>"
                    components_cell = "&mdash;"
                else:
                    n = len(bom_lines)
                    bom_cell = html.escape(order._sb_bom_shop_label(bom))
                    components_cell = "%d component%s" % (
                        n, "" if n == 1 else "s")
                rows.append(
                    "<tr>"
                    "<td>%(idx)d</td>"
                    "<td>%(product)s</td>"
                    "<td class='text-end'>%(qty).2f</td>"
                    "<td>%(bom)s</td>"
                    "<td class='text-end'>%(components)s</td>"
                    "</tr>" % dict(
                        idx=idx,
                        product=html.escape(product_name),
                        qty=line.product_uom_qty or 0.0,
                        bom=bom_cell,
                        components=components_cell,
                    )
                )
            order.sb_bom_preview_html = (
                "<table class='table table-sm o_list_view'>"
                "<thead><tr>"
                "<th>#</th>"
                "<th>Product</th>"
                "<th class='text-end'>Qty</th>"
                "<th>Manufacturing BoM</th>"
                "<th class='text-end'>Components</th>"
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
    # Task 31 (corner remediation 2026-07-30) — write-path repricing fix.
    #
    # DEFECT (Task 29 probe): stock sale.order._compute_pricelist_id is a
    # real @api.depends('partner_id', 'company_id') compute — it fires on
    # ANY write to partner_id, not just Form/onchange dispatch. It sets
    # pricelist_id = partner.property_product_pricelist, which is empty
    # for any Southbrook channel partner (they're priced via the channel
    # resolver's pricelist records, not the stock per-partner pricelist
    # field). _onchange_partner_id_southbrook_pricelist above is declared
    # @api.onchange ONLY, so it never runs on a plain ORM
    # `order.write({"partner_id": ...})` — any backend script, server
    # action, or RPC call that isn't the web client's onchange dispatch.
    # Net effect: pricelist_id silently BLANKS on any non-UI partner
    # switch, breaking the locked "switch customer on the header -> whole
    # order re-prices" contract (CLAUDE.md section 2.2 / section 9).
    #
    # Fix: override the compute itself so channel resolution is
    # authoritative on the WRITE path too, not just onchange. Run stock's
    # compute first (super()) so behaviour for a partner with NO channel
    # is untouched (still partner.property_product_pricelist, still
    # blanks to False when that's empty — exactly stock semantics). Then,
    # only for a partner that DOES carry a Southbrook channel, overwrite
    # with the channel-resolved pricelist — _resolve_channel_pricelist
    # always returns a record (falls back to retail), so this branch
    # never blanks pricelist_id when a resolvable pricelist exists. No
    # re-declared @api.depends needed: Odoo's field.get_depends() unions
    # dependencies from every MRO-resolved implementation of the same
    # compute method name (odoo/orm/fields.py resolve_mro +
    # get_depends), so stock's ('partner_id', 'company_id') already
    # applies to this override — but it's declared explicitly below to
    # keep the trigger set self-documenting, matching this file's other
    # computes.
    #
    # This does not touch line price_unit logic (design-time snapshot
    # architecture, T5-adjudicated) or the order.state != 'draft' guard
    # stock's own compute already applies — both preserved unchanged.
    #
    # Review fix-round finding (IMPORTANT): website_sale ALSO overrides
    # this exact compute (GeoIP pricelist resolution for website_id-
    # bearing carts — website_sale/models/sale_order.py). res.partner.
    # channel defaults to "retail" for EVERY partner (see res_partner.py),
    # so without a gate, every storefront checkout address write (which
    # writes partner_id on the cart order) would have Southbrook's
    # channel resolution silently discard website_sale's GeoIP pricelist
    # selection — a surface the old @api.onchange-only resolver never
    # reached (onchange never fires on a website controller's ORM write
    # either). Q1's "channel pricelists auto-apply" architecture was
    # written for BACKEND/Order-Builder/kitchen-quote orders, which never
    # carry a website_id; storefront carts are website_sale's domain and
    # must keep ITS pricing resolution untouched. Conservative fix
    # (matches pre-fix reality for storefront carts exactly): skip the
    # channel override entirely when order.website_id is set. super()
    # above still runs unconditionally first, so website_sale's own
    # override — wherever it sits in the MRO super() chain relative to
    # this one — still gets to compute (and this method never clobbers)
    # whatever pricelist_id it produces for those orders.
    #
    # getattr(..., False), not order.website_id directly: website_id is
    # a website_sale field, and southbrook_estimating must stay
    # installable standalone for the backend-only sales-rep persona
    # (CLAUDE.md section 2 -- "it can run without the website addon").
    # Same guarded-optional-dependency pattern this file already uses
    # for southbrook_is_cabinet above (get_kitchen_3d_payload).
    @api.depends("partner_id", "company_id")
    def _compute_pricelist_id(self):
        super()._compute_pricelist_id()
        for order in self:
            if order.state != "draft":
                continue
            if getattr(order, "website_id", False):
                continue
            partner = order.partner_id
            if not partner or not partner.channel:
                continue
            resolved = order._resolve_channel_pricelist(partner)
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
            # SBK-043 (2026-08-03) — these checks used to flatten EVERY
            # attribute into one bag of lowercase strings and ask "does
            # any of them contain 'maple'". That is how the shipped
            # L-Shape and U-Shape templates became unsellable.
            #
            # SB-WALL-CORNER carries Box Material = "White Melamine",
            # which is perfectly legal on the Contractor series. What it
            # also carries is Wood Species = "Maple (Hard)" — a door and
            # trim veneer descriptor added on 2026-06-09, unrelated to
            # the carcass. The unscoped scan could not tell the two
            # apart, so every corner-wall cabinet tripped the box rule,
            # and both shaped templates instantiated to an order that
            # could never be confirmed. Roughly 35 live designs.
            #
            # The rules in CLAUDE.md section 5 are each about ONE named
            # attribute, so the check now reads that attribute by name.
            # Exact names, not substrings: this catalog has four "Door *"
            # attributes (Count, Edge Profile, Overlay, Style) and only
            # Door Style is the door rule's subject.
            #
            # Also note `ptav.name` can render as "Attribute: Value"
            # rather than the bare value, which is a second way a
            # substring scan reaches the wrong conclusion. Read the
            # value off product_attribute_value_id instead.
            attr_index = {}
            for ptav in line.product_id.product_template_attribute_value_ids:
                attr_name = (ptav.attribute_id.name or "").strip().lower()
                value_name = (
                    ptav.product_attribute_value_id.name or "").strip().lower()
                if attr_name and value_name:
                    attr_index.setdefault(attr_name, set()).add(value_name)

            def _attr_has(attribute, *tokens):
                """True when the named attribute's value matches a token."""
                values = attr_index.get(attribute, ())
                return any(t in v for v in values for t in tokens)

            if _attr_has("series", "contractor") and _attr_has(
                    "door style", "five-piece", "5-piece", "woodgrain"):
                issues.append({
                    "code": "series_door_incompatible",
                    "line_id": line.id,
                    "message": (
                        f"{line.name}: Contractor series only allows "
                        "the white thermofoil slab door."
                    ),
                })
            if _attr_has("series", "elegance") and _attr_has(
                    "door style", "slab", "thermofoil"):
                issues.append({
                    "code": "series_door_incompatible",
                    "line_id": line.id,
                    "message": (
                        f"{line.name}: Elegance series uses five-piece "
                        "woodgrain doors only."
                    ),
                })
            # The carcass rule is about Box Material only. Wood Species
            # is a veneer descriptor and must never reach this check.
            if _attr_has("box material", "maple") and _attr_has(
                    "series", "contractor"):
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
    # Phase 1 sales-rep UX fix (2026-07-31) — surface hard-validation
    # issues WHILE the order is being built, not only as a blocking
    # dialog at Confirm. Persona: a rep on the phone with the customer
    # builds a whole multi-line quote and only learns a rule was
    # violated when they click Confirm.
    #
    # This is a thin, non-stored read-through onto
    # southbrook_hard_validation_issues() above — the SAME method
    # action_confirm() calls — so there is exactly one place the four
    # hard rules are checked. No refactor of that method was needed:
    # it only ever reads line.product_id.product_template_attribute_
    # value_ids (no writes, no side effects), so it is already safe to
    # call on an in-progress/unsaved draft order mid-onchange, not just
    # a persisted one. The Confirm-time gate in action_confirm() below
    # is untouched — same method, same codes, same severity.
    # ------------------------------------------------------------------
    sb_hard_validation_issue_count = fields.Integer(
        string="Configuration Rule Violations",
        compute="_compute_sb_hard_validation_issues",
        store=False,
        help=(
            "How many configuration rule violations this order has "
            "right now, as configured. Zero means every line is "
            "buildable. This is the same check Confirm runs — "
            "surfaced earlier so you catch it while building, not "
            "after."
        ),
    )
    sb_hard_validation_issues_html = fields.Html(
        string="Configuration Issues",
        compute="_compute_sb_hard_validation_issues",
        sanitize=False,  # every tag is ours; line text is HTML-escaped below
        store=False,
        readonly=True,
    )

    # SBK-046 (2026-08-03) — the backend "ILLUSTRATIVE SEED" banner was
    # gated on `context.get('seed_mode_canonical')`, a context key that
    # nothing in the codebase has ever set. Its own comment promised it
    # "hides automatically when mode flips to canonical"; in fact it
    # could never hide, so the banner — internal doc references and all
    # — has been permanently pinned to the top of every sale order.
    #
    # Read the actual flag the printed report already reads.
    sb_seed_mode_illustrative = fields.Boolean(
        string="Illustrative Seed Data",
        compute="_compute_sb_seed_mode_illustrative",
        store=False,
        readonly=True,
        help="True while ir.config_parameter southbrook.seed_mode is "
             "'illustrative'. Drives the demo-data banner.",
    )

    def _compute_sb_seed_mode_illustrative(self):
        mode = self.env["ir.config_parameter"].sudo().get_param(
            "southbrook.seed_mode", default="illustrative")
        for order in self:
            order.sb_seed_mode_illustrative = (mode == "illustrative")

    @api.depends(
        "order_line.product_id",
        "order_line.product_id.product_template_attribute_value_ids",
    )
    def _compute_sb_hard_validation_issues(self):
        for order in self:
            issues = order.southbrook_hard_validation_issues()
            order.sb_hard_validation_issue_count = len(issues)
            if not issues:
                order.sb_hard_validation_issues_html = False
                continue
            # issue["message"] is built from line.name, which is
            # user-editable free text — escape before embedding in an
            # unsanitized Html field.
            rows = "".join(
                "<li>%s</li>" % html.escape(issue["message"])
                for issue in issues
            )
            order.sb_hard_validation_issues_html = Markup(
                "<div class='alert alert-danger mb-0' role='alert'>"
                "<strong>Fix these before you confirm this order:</strong>"
                "<ul class='mb-0'>%s</ul>"
                "</div>" % rows
            )

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

    # ------------------------------------------------------------------
    # C-2 / H-10 (2026-08-05) — wall-aware placement.
    #
    # get_kitchen_3d_payload used to place EVERY cabinet from the flat
    # _ZONE_LAYOUT X-cursor above and NEVER read order_line.wall_id
    # (grep confirmed zero references) — so a genuine 3-wall U-shape
    # room collapsed onto two straight runs sharing one X axis. The
    # wall data has been present and correct on every line since the
    # 2026-08-03 reconcile fix (wall_id -> southbrook.room.wall, with
    # name / wall_order / length_mm).
    #
    # Wall roles — back / left / right / front — are derived per-room
    # by _classify_room_walls: a wall literally named "Back Wall" /
    # "Left Wall" / etc. wins by name; every other wall (the actual
    # common case — southbrook_estimating_website's room_setup_wizard
    # and the room_templates.xml seeds only ever generate "Wall A" /
    # "Wall B" / "Wall C") fills the remaining roles in wall_order
    # sequence: back, then left, then right, then front. A room's 5th+
    # wall gets no role.
    #
    # Placement per role is a genuine 90/180-degree rotation about the
    # vertical (Y) axis — realized WITHOUT a mesh-level rotation field.
    # At cardinal angles, rotating an axis-aligned box is exactly
    # equivalent to (a) permuting which local axis feeds which world
    # axis and (b) swapping width<->depth in the panel's own dims (and,
    # for the rare cylinder hardware panel, swapping its "axis" name);
    # see _wall_panel_transform. cabinet_viewport.esm.js DOES read a
    # per-panel `rot` field (used today only for the cylinder handle
    # axis defaults), but composing that with the axis-default rotation
    # already applied to bar-pull cylinders would need quaternion
    # composition the client doesn't do — so this fix deliberately
    # reorients via geometry placement (position + dims permutation)
    # rather than emitting `rot`. That is a genuine physical
    # reorientation (doors/handles end up facing INTO the room on every
    # wall, not just repositioned while still facing the old way) — not
    # a fallback — achieved with zero client-side changes.
    #
    # Lines with no wall_id — or a wall_id whose role can't be resolved
    # (a room's 5th+ wall, or a wall with no room) — fall back to the
    # ORIGINAL _ZONE_LAYOUT cursor scheme byte-for-byte: older orders
    # with no Room-First wall data must not regress. Worktops are
    # DELIBERATELY excluded from wall-based placement even when
    # wall_id happens to be set on a worktop line — they are countertop
    # slabs that already share the ground cursor with the base run they
    # sit on (a true per-wall mitred worktop run is a separate,
    # materially bigger geometry problem this task does not cover).
    # ------------------------------------------------------------------
    _WALL_ROLE_TOKENS = {
        "back": ("back",),
        "front": ("front",),
        "left": ("left",),
        "right": ("right",),
    }
    _WALL_ROLE_SEQUENCE = ("back", "left", "right", "front")

    def _classify_room_walls(self, room):
        """Assign each of `room`'s walls a role: back / left / right /
        front. Name substrings win first; every remaining wall fills
        the remaining roles in wall_order sequence. Returns
        {wall_id: role}; a wall with no assignable role (a room's 5th+
        wall) is simply absent — callers fall back to zone placement.
        """
        ordered = room.wall_ids.sorted(key=lambda w: (w.wall_order, w.id))
        roles = {}
        used = set()
        unmatched = []
        for w in ordered:
            nm = (w.name or "").lower()
            role = next(
                (r for r, tokens in self._WALL_ROLE_TOKENS.items()
                 if r not in used and any(t in nm for t in tokens)),
                None,
            )
            if role:
                roles[w.id] = role
                used.add(role)
            else:
                unmatched.append(w)
        remaining = iter(
            [r for r in self._WALL_ROLE_SEQUENCE if r not in used])
        for w in unmatched:
            role = next(remaining, None)
            if role:
                roles[w.id] = role
            # else: no role left (5th+ wall in this room) — line stays
            # unclassified, caller falls back to zone placement.
        return roles

    @api.model
    def _room_wall_extents(self, room, roles):
        """room_width (back/front wall length_mm) + room_depth (left/
        right wall length_mm), derived from the classified walls. 0
        when the relevant wall is absent — a degenerate but non-
        crashing input to the Right/Front wall anchor calc, only
        reachable for a room missing a Back or a Left/Right wall."""
        by_role = {}
        for w in room.wall_ids:
            role = roles.get(w.id)
            if role and role not in by_role:
                by_role[role] = w
        width_wall = by_role.get("back") or by_role.get("front")
        depth_wall = by_role.get("left") or by_role.get("right")
        return {
            "width": float(width_wall.length_mm or 0) if width_wall else 0.0,
            "depth": float(depth_wall.length_mm or 0) if depth_wall else 0.0,
        }

    @api.model
    def _wall_panel_transform(self, role, room_width, room_depth,
                               w_cab, d_cab, cursor):
        """Return (transform_fn, dims_swapped, axis_map) for one
        cabinet placed by wall `role`.

        `cursor` is the running along-wall offset (mm) for THIS wall
        BEFORE this cabinet is placed — the caller advances it by
        w_cab afterwards, mirroring the pre-existing zone-cursor
        accumulation.

        transform_fn(local_x, local_y, local_z) -> (world_x, world_y,
        world_z). local_y always passes through UNCHANGED — elevation
        is applied by the caller via y_floor, exactly like the
        pre-existing zone path, so "wall" zone cabinets keep their
        elevated Y regardless of which wall placed them. Local frame:
        front face at local z ~= 0, back at local z = -d_cab, width
        centered on local x in [-w_cab/2, w_cab/2] — the same cabinet-
        local convention product_config_line.py's
        _cut_list_to_3d_payload already builds.
        """
        if role == "back":
            return (
                lambda x, y, z: (x + cursor + w_cab / 2, y, z),
                False, {},
            )
        if role == "front":
            # Opposite the back wall: back-of-cabinet touches the front
            # wall (Z = room_depth); door faces back toward Z = 0.
            anchor_z = room_depth - d_cab
            return (
                lambda x, y, z: (-x + cursor + w_cab / 2, y, -z + anchor_z),
                False, {},
            )
        if role == "left":
            # 90 degrees: local width axis (x) -> world Z (along the
            # wall); local depth axis (z) -> world X (away from the
            # wall, at X=0, into the room).
            return (
                lambda x, y, z: (z + d_cab, y, -x + cursor + w_cab / 2),
                True, {"x": "z", "z": "x"},
            )
        if role == "right":
            # Mirror of "left": back-of-cabinet touches X = room_width.
            anchor_x = room_width - d_cab
            return (
                lambda x, y, z: (-z + anchor_x, y, x + cursor + w_cab / 2),
                True, {"x": "z", "z": "x"},
            )
        return None, False, {}

    # ------------------------------------------------------------------
    # H-10 companion — Finish-driven door material.
    #
    # cab_inputs used to never pass "finish", so product_config_line.py's
    # _cut_list_to_3d_payload always fell through to the generic brown
    # "door" material — even for a White Melamine kitchen. Extract the
    # variant's real Finish (fallback: Box Material) attribute value and
    # normalize it the SAME way product_config_line.py's own
    # _extract_cabinet_inputs() does for the single-cabinet wizard
    # preview, so both viewports render the same door color for the
    # same variant. ptav.name renders as "Attribute: Value" — read the
    # bare value off product_attribute_value_id, not ptav.name itself.
    # ------------------------------------------------------------------
    def _sb_finish_slug(self, variant):
        """Best-effort Finish/Box Material slug for the door_<slug>
        material lookup (door_white/_maple_stain/_cherry_stain/
        _walnut_stain — the exact _KNOWN_FINISHES set product_config_
        line.py's _cut_list_to_3d_payload gates on). Empty string when
        neither attribute is set or neither value maps to a known
        slug — callers fall back to the generic "door" material, same
        as today."""
        if not variant:
            return ""
        finish_val = ""
        box_material_val = ""
        for ptav in variant.product_template_attribute_value_ids:
            attr_name = (ptav.attribute_id.name or "").strip().lower()
            value_name = (
                ptav.product_attribute_value_id.name or "").strip().lower()
            if not value_name:
                continue
            if attr_name == "finish" and not finish_val:
                finish_val = value_name
            elif attr_name == "box material" and not box_material_val:
                box_material_val = value_name
        for name in (finish_val, box_material_val):
            if not name:
                continue
            if "white" in name:
                return "white"
            if "maple" in name:
                return "maple_stain"
            if "cherry" in name:
                return "cherry_stain"
            if "walnut" in name:
                return "walnut_stain"
        return ""

    def get_kitchen_3d_payload(self):
        """Multi-cabinet 3D payload for the OWL kitchen viewport.

        Loop over self.order_line in display order. For each line whose
        product matches an SB-* SKU, compute its single-cabinet panels at
        the origin (via the existing config_session pipeline), then place
        every panel in world space:
          • a line WITH a resolvable wall_id places along that wall's own
            axis, right-angled per its room role (back/left/right/front —
            see _wall_panel_transform);
          • a line with no wall_id, or an unclassifiable one, falls back
            to the original per-zone X cursor (x_offset accumulates as
            cabinets are placed; y_floor comes from _ZONE_FLOOR_Y[line.zone]).

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
        # C-2/H-10 — per-wall along-wall cursors, keyed by wall.id.
        wall_cursors = {}
        # Per-room wall-role / extent caches — computed once per room,
        # not per line.
        wall_role_cache = {}
        room_extent_cache = {}
        max_h = 0             # tallest cabinet's top, for camera framing
        # Genuine world-space X/Z bounding box across every placed
        # cabinet (replaces the old single-run "widest cursor" +
        # doubled-depth approximation, which only ever framed a flat
        # X-axis run and silently cropped a second/third wall out of
        # camera range).
        gmin_x = gmax_x = gmin_z = gmax_z = None
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
            # share the ground X cursor regardless of zone — NEVER
            # wall-based (see method docstring / class comment above).
            # Everything else: wall_id wins when it resolves to a role;
            # otherwise dispatch by zone via _ZONE_LAYOUT, exactly as
            # before.
            zone = line.zone or "base_run"
            wall_role = None
            wall = False if fam == "worktop" else line.wall_id
            if wall and wall.room_id:
                room = wall.room_id
                if room.id not in wall_role_cache:
                    wall_role_cache[room.id] = self._classify_room_walls(room)
                roles = wall_role_cache[room.id]
                wall_role = roles.get(wall.id)

            if fam == "worktop":
                cursor_name = self._WORKTOP_CURSOR
                y_floor = self._WORKTOP_Y_FLOOR
                x_offset = cursors[cursor_name] + w / 2
                fn = lambda x, y, z, _xo=x_offset: (x + _xo, y, z)  # noqa: E731
                dims_swapped = False
                axis_map = {}
                advance = ("zone", cursor_name)
            elif wall_role:
                if room.id not in room_extent_cache:
                    room_extent_cache[room.id] = self._room_wall_extents(
                        room, roles)
                extents = room_extent_cache[room.id]
                wall_cursor = wall_cursors.get(wall.id, 0.0)
                y_floor = self._ZONE_LAYOUT.get(
                    zone, ("ground", 0, 0))[1]
                fn, dims_swapped, axis_map = self._wall_panel_transform(
                    wall_role, extents["width"], extents["depth"],
                    w, d, wall_cursor,
                )
                advance = ("wall", wall.id)
            else:
                cursor_name, y_floor, z_offset = self._ZONE_LAYOUT.get(
                    zone, ("ground", 0, 0),
                )
                x_offset = cursors[cursor_name] + w / 2
                fn = lambda x, y, z, _xo=x_offset, _zo=z_offset: (  # noqa: E731
                    x + _xo, y, z + _zo)
                dims_swapped = False
                axis_map = {}
                advance = ("zone", cursor_name)

            cab_inputs = {
                "width_mm":   w,
                "height_mm":  h,
                "depth_mm":   d,
                "family":     fam,
                "door_count": doors,
                "drawer_count": drawers,
                "finished_sides": "none",
                # H-10 companion — real Finish/Box Material pick, so the
                # door renders the actual configured color instead of
                # always falling back to the generic brown material.
                "finish": self._sb_finish_slug(line.product_id),
            }
            cut = Bom._compute_panel_dimensions(
                width_mm=w, height_mm=h, depth_mm=d,
                family=fam, door_count=doors, drawer_count=drawers,
                finished_sides="none",
            )
            single = Session._cut_list_to_3d_payload(cab_inputs, cut)

            line_tag = f"L{line.id}_"
            for panel in single["panels"]:
                p = dict(panel)
                wx, wy, wz = fn(
                    panel["pos"]["x"], panel["pos"]["y"], panel["pos"]["z"])
                p["pos"] = {"x": wx, "y": wy + y_floor, "z": wz}
                if dims_swapped:
                    pd = panel["dims"]
                    p["dims"] = {
                        "width": pd["depth"],
                        "height": pd["height"],
                        "depth": pd["width"],
                    }
                if axis_map and panel.get("shape") == "cylinder":
                    p["axis"] = axis_map.get(
                        panel.get("axis", "y"), panel.get("axis"))
                p["name"] = line_tag + panel["name"]
                all_panels.append(p)

            if advance[0] == "zone":
                cursors[advance[1]] += w
            else:
                wall_cursors[advance[1]] = wall_cursors.get(advance[1], 0.0) + w

            cabinet_top = (h if fam != "worktop" else 25) + y_floor
            max_h = max(max_h, cabinet_top)

            # World-space X/Z footprint of THIS cabinet — evaluated at
            # its own local corners through the same fn used for its
            # panels, so back/front/left/right/zone placements all fold
            # into one genuine bounding box (fn is always an axis-
            # aligned permutation/reflection at these cardinal angles,
            # so evaluating just the 4 (x,z) corner combinations is
            # exact, not an approximation).
            corner_xs, corner_zs = [], []
            for lx in (-w / 2, w / 2):
                for lz in (-d, 0):
                    cx, _cy, cz = fn(lx, 0, lz)
                    corner_xs.append(cx)
                    corner_zs.append(cz)
            cab_min_x, cab_max_x = min(corner_xs), max(corner_xs)
            cab_min_z, cab_max_z = min(corner_zs), max(corner_zs)
            if gmin_x is None:
                gmin_x, gmax_x, gmin_z, gmax_z = (
                    cab_min_x, cab_max_x, cab_min_z, cab_max_z)
            else:
                gmin_x = min(gmin_x, cab_min_x)
                gmax_x = max(gmax_x, cab_max_x)
                gmin_z = min(gmin_z, cab_min_z)
                gmax_z = max(gmax_z, cab_max_z)

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

        # C-2/H-10 — genuine world-space bounding box (gmin_x is None
        # iff no cabinet was ever placed — equivalent to the old
        # "widest cursor == 0" empty check).
        if gmin_x is None:
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

        kitchen_w = gmax_x - gmin_x
        kitchen_d = gmax_z - gmin_z

        cam_target = [(gmin_x + gmax_x) / 2, max_h / 2, (gmin_z + gmax_z) / 2]
        # Camera distance scales with kitchen footprint so wide/multi-
        # wall runs still frame cleanly without manual zoom.
        cam_dist = max(kitchen_w, max_h, kitchen_d, 1) * 1.8
        cam_position = [
            (gmin_x + gmax_x) / 2,
            max_h * 1.4,
            gmax_z + cam_dist,
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
                "kitchen_depth_mm":  kitchen_d,
                "cursors": dict(cursors),  # diagnostic: per-zone X totals
                # C-2/H-10 — diagnostic: per-wall along-wall totals.
                "wall_cursors": {
                    str(k): v for k, v in wall_cursors.items()},
                "lines": lines_index,      # T1C8 — per-line lookup for hover
            },
            "camera": {"target": cam_target, "position": cam_position},
            "bounds": {
                "min": [gmin_x, 0, gmin_z],
                "max": [gmax_x, max_h, gmax_z],
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
