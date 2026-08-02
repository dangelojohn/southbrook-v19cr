# SPDX-License-Identifier: LGPL-3.0-only
"""
Portal routes for the Southbrook Estimating Order Builder.

Track 2 commit 1 (2026-05-30) — Phase 2 charter amendment 1:
this is the SCAFFOLD route. Renders a portal page with the chrome,
breadcrumbs, sidebar, and an empty `<div id="order_builder_root">`
placeholder where commit 2 will mount the OWL `<OrderBuilder/>`
component tree.

The controller intentionally leaves the OWL bundle off the page in
this commit so we can verify the portal frame, auth, and routing
work cleanly before adding the JavaScript layer. Commit 2 adds the
OWL bundle to the manifest's assets section and switches this
template to inherit the bundle.

Route shape: `/my/southbrook/order-builder/<int:order_id>`
  Matches Odoo portal convention (`/my/...`) so portal-side menu
  hooks + breadcrumbs work without bespoke wiring.

Auth model: portal user; `partner_id.parent_id` chain identifies
the dealer. The controller verifies the order belongs to either
the logged-in partner OR the partner's parent (dealer org).
"""
import json
import logging
import math
import time

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.portal.controllers.portal import CustomerPortal
# P0.2 (2026-07-11) — the single pure layout engine (no ORM), source of
# truth for cabinet placement. See southbrook_estimating design doc.
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine

_logger = logging.getLogger(__name__)


def _snap_quarter_in(value):
    """Snap a raw inch value to the nearest 1/4in (cabinet-industry-
    standard display granularity).

    SBK-025 (V5-partc.md) — the same `round(inches * 4) / 4` convention
    already used by every other mm<->inch display boundary in this
    codebase: `kitchen_viewport.esm.js:_formatMm`,
    `cabinet_viewport.esm.js:_formatMm`, and the inline
    `round((width_mm / 25.4) * 4) / 4` a few hundred lines below in this
    same file. Applying it here, at the mm-table -> design-line write
    boundary in `_southbrook_seed_design_lines`, means the persisted
    `width_in`/`height_in`/`depth_in`/`x_position_in`/`y_position_in`/
    `z_position_in` values are already clean (e.g. 24.00, not
    23.9763779...) before they ever reach the raw client echo at
    `kitchen_configurator.js:2604` — no second rounding convention, and
    no client-side change needed for values seeded through this path.
    """
    return round(value * 4) / 4.0


# ======================================================================
# G5 + G4 + G8 (customer-flow JTBD gap analysis 2026-06-01) — capture
# the visitor's project name at signup so we can label their first
# auto-created sale.order without forcing a second prompt.
#
# Pre-fix: signup asked only Name / Email / Password, the post-signup
# redirect went straight to /my, the user landed at a generic portal
# dashboard with a CTA card (G10) and no project label.
#
# Mechanism:
#   1. views/auth_template.xml adds a 'Project Name' input to
#      auth_signup.signup right after the Name field (G5).
#   2. SouthbrookAuthSignup below stashes that value into
#      request.session at the same point the stock signup controller
#      validates the form (so it survives the auth flip + redirect).
#   3. SouthbrookOrderBuilder.southbrook_order_builder_new pops the
#      session value and writes it to sale.order.client_order_ref —
#      Odoo's existing 'Customer Reference' field, perfect for free-
#      text project labels like 'Smith Kitchen Renovation'.
# ======================================================================
class _SouthbrookOrderAccessMixin:
    """Shared helper for per-order auth checks used by both the
    customer-facing planner routes and the portal Order Builder.

    Kept as a plain mixin (NOT a Controller subclass) so it can be
    inherited without polluting Odoo's controller-route registry.
    Owns exactly one method — `_southbrook_resolve_order` — so any
    sibling controller can look up + auth-check a sale.order without
    duplicating the partner-chain rules.

    Regression 2026-07-01: `SouthbrookRoomApi(SouthbrookKitchenPlanner)`
    was silently inheriting nothing useful because the resolver used
    to live on `SouthbrookOrderBuilderPortal` (a sibling, not an
    ancestor). Every /southbrook/api/order/<id>/... call blew up
    with AttributeError. The mixin unifies the source of truth.
    """

    def _southbrook_resolve_order(self, order_id):
        """Look up the sale.order and check the user has access.

        Access rule:

          • Internal users (admin, sales reps, anyone whose
            res.users.share is False) see every order — they're
            staff with full backend access anyway, the portal page
            is just an alternate presentation.

          • Portal users (res.users.share=True — customers and
            dealers logged in via the public portal): the logged-in
            partner must equal order.partner_id, OR order.partner_id
            .parent_id must equal the logged-in partner (dealer
            views customer order), OR the logged-in partner's
            parent_id must equal order.partner_id (parent partner
            views child's order).

          • Anything else → AccessError, controller redirects to /my
            (or JSON endpoints return {"error": "forbidden"}).

        Missing IDs collapse to AccessError too — distinguishing
        "not found" from "forbidden" is an existence-oracle leak: an
        authenticated portal user could probe order_ids and infer
        which ones belong to other partners. Matches the convention
        f44eff9 established for `_southbrook_resolve_line` and the
        room_api scope guards at room_api.py:_get_room_scoped.
        Callers (main.py:1097, 1238, 1377, 1588, 1693, 1863, 2166,
        2223, 2250, 2339) still have `except MissingError` branches;
        those are now dead code but harmless — delete-in-a-follow-up.
        """
        order = request.env["sale.order"].sudo().browse(order_id).exists()
        user = request.env.user
        if order:
            if not user.share:
                return order
            my_partner = user.partner_id
            order_partner = order.partner_id
            if my_partner == order_partner:
                return order
            if order_partner.parent_id and order_partner.parent_id == my_partner:
                return order
            if my_partner.parent_id and my_partner.parent_id == order_partner:
                return order
        raise AccessError("This order is not accessible to your account.")


class SouthbrookAuthSignup(AuthSignupHome):

    _SESSION_KEY = "southbrook_project_name"

    def get_auth_signup_qcontext(self):
        # Echo project_name back into the qcontext so the form re-
        # populates after a validation error (password mismatch etc.).
        # Otherwise the stock controller drops every unknown POST key.
        qcontext = super().get_auth_signup_qcontext()
        if "project_name" in request.params:
            qcontext["project_name"] = request.params["project_name"]
        return qcontext

    def _prepare_signup_values(self, qcontext):
        # Stash the project name in the session before delegating to
        # the stock validator. Stash BEFORE the super call so even if
        # password validation fails (UserError) we don't drop a name
        # the user already typed — the next POST will overwrite it.
        # The stash happens on every signup-form POST attempt; the
        # /my/southbrook/order-builder/new route pops it on first read
        # so a stale value cannot leak into a future user's session.
        project_name = (qcontext.get("project_name") or "").strip()
        if project_name:
            request.session[self._SESSION_KEY] = project_name[:128]
        return super()._prepare_signup_values(qcontext)


# ======================================================================
# Phase 2 commit 1 (2026-05-31) — /kitchen-planner scaffold
#
# The CUSTOMER-facing one-page configurator per CLAUDE.md §2.1.
# Three-pane layout per PRODBOARD_MANIFEST §8.1:
#   left  58 px tool rail
#   centre 394 px catalog pane (296×94 tile grid)
#   right flex viewport (Phase 3 mounts Three.js parametric carcass here;
#         Phase 2 ships the 2D-isometric SVG fallback at Tier 3 per the
#         four-tier image cascade per CLAUDE.md §4.5)
#
# This commit scaffolds:
#   - The /kitchen-planner route (portal-authed, website=True so the
#     site frame, theme, and breadcrumbs apply).
#   - The mount-point template with `id="kitchen_planner_root"` (mirrors
#     T2C1's pattern for the Order Builder — commit 2 here would mount
#     <KitchenPlanner/> OWL component into it).
#   - The three-pane SCSS layout grid + Southbrook token import.
#
# This commit does NOT yet ship:
#   - The OWL component tree (Phase 2 commit 2+).
#   - Catalog tile renderer (Phase 2 commit 3+).
#   - The 2D-isometric SVG layer (Phase 2 commit 4+).
#   - Live attribute → price wiring (Phase 2 commit 5+).
#   - "Request a Price" → sale.order.draft + portal email (Phase 2
#     commit 6+).
#   - The Three.js procedural 3D layer (Phase 3).
#
# Auth model: same `auth="user"` as the Order Builder route. The
# customer logs in as an Odoo portal user (light auth — name + email).
# No SSO yet; Phase 4 polish.
# ======================================================================
class SouthbrookKitchenPlanner(_SouthbrookOrderAccessMixin, http.Controller):
    """Customer-facing /kitchen-planner one-page configurator route."""

    @http.route(
        ["/commercial", "/commercial/"],
        type="http",
        auth="public",
        website=True,
        sitemap=True,
    )
    def commercial_page(self, **kw):
        """Render the public commercial manufacturing page."""
        return request.render(
            "southbrook_estimating_website.commercial_page_template",
            {"page_name": "southbrook_commercial"},
        )

    @http.route(
        "/kitchen-planner",
        type="http",
        auth="user",
        website=True,
    )
    def kitchen_planner(self, **kw):
        """Render the customer kitchen planner.

        Phase 2 commit 1 surface: empty three-pane shell with the
        mount-point div in the viewport pane. Commit 2 mounts the OWL
        <KitchenPlanner/> component into that div via planner_boot.esm.js.
        """
        values = {
            "page_name": "southbrook_kitchen_planner",
            "owl_mount_id": "kitchen_planner_root",
            "user_partner": request.env.user.partner_id,
        }
        return request.render(
            "southbrook_estimating_website.kitchen_planner_template",
            values,
        )

    # ==================================================================
    # Phase 2 commit 2 — initial state JSON-RPC endpoint.
    #
    # The OWL <KitchenPlanner/> component calls this on mount to seed
    # its reactive store. Shape mirrors the planned state.session +
    # state.catalog objects the customer SPA needs:
    #
    #   { user: {partner_id, partner_name, channel}
    #     catalog: [{xml_id, sku, name, family, list_price, ...}, ...]
    #     currency: {symbol, position, decimal_places}
    #     session: null  # commit 3+ surfaces an active config session
    #   }
    #
    # Auth=user keeps the planner behind portal-light auth per
    # CLAUDE.md §2.1 ("behind light auth"). Public anonymous browsing
    # is a Phase-3-polish ask separate from this scope.
    # ==================================================================
    @http.route(
        "/southbrook/api/kitchen-planner/state",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def kitchen_planner_state(self, **kw):
        """Return the initial state payload the OWL planner mounts with.

        Phase 2 commit 2 surface: read-only — catalog + user. Subsequent
        commits add session create/update/commit endpoints.
        """
        user = request.env.user
        partner = user.partner_id

        # 2026-06-02 catalog-picker redesign — channel-aware preview
        # pricing.
        #
        # Display-only. The add-line endpoint and order-line pricing
        # are unchanged: when the user clicks Add the line is created
        # with the default product_uom_qty and its price flows through
        # Odoo's standard product_id_change → pricelist resolution
        # (which lands on the SAME pricelist as the preview because
        # the partner is the same). This block just shows a preview
        # of that figure on the catalog card.
        #
        # Resolution path:
        #   1. sale.order._resolve_channel_pricelist(partner) — custom
        #      routine #3, same dispatcher the order uses at confirm.
        #   2. Per-cabinet: pricelist._get_product_price(variant,
        #      qty=1.0, partner) on the lowest-id variant we already
        #      use for SKU resolution.
        #   3. Round to the website currency's decimal_places (same
        #      currency as list_price), so display matches what the
        #      order line will round to at confirmation time.
        #   4. Guarded — if the pricelist or the variant is missing,
        #      or the call raises, channel_price falls back to
        #      tmpl.list_price (i.e. retail). The catalog endpoint
        #      must never 500 over a price-preview failure.
        SaleOrder = request.env["sale.order"].sudo()
        channel_pricelist = SaleOrder._resolve_channel_pricelist(partner)
        channel_label = (
            channel_pricelist.name if channel_pricelist else ""
        )

        # Website currency — resolved here (above the loop) so the
        # per-cabinet channel_price can round to its decimal_places.
        # JSON-RPC routes don't get `request.website` injected (that
        # requires `website=True` on the route, which is only valid
        # for `type='http'`); use get_current_website() instead.
        Website = request.env["website"].sudo()
        website = (
            Website.get_current_website()
            if hasattr(Website, "get_current_website")
            else Website
        )
        currency = (
            (website and website.currency_id)
            or request.env.company.currency_id
        )

        # Catalog: the 12 Q8 cabinet templates resolved by stable
        # xml_id rather than default_code matching.
        #
        # Background: filtering by template.default_code LIKE 'SB-%'
        # silently drops templates whose default_code field has
        # blanked out — and that happens routinely for dynamic-
        # variant templates once they accumulate >1 variant (Odoo
        # populates template.default_code only when the template has
        # exactly one variant, otherwise it goes to NULL).
        #
        # Searching via product.product variants instead drops
        # templates that have ZERO variants yet (e.g. cabinets a
        # customer hasn't configured at all — the OCA dynamic-
        # variant model defers variant creation until configuration).
        #
        # xml_ids are stable across both edge cases: they're set at
        # install time, never blank, never depend on variant count.
        # The 12 cabinet xml_ids are locked per Q8 (see CLAUDE.md
        # §3 + data/product_templates.xml).
        Tmpl = request.env["product.template"].sudo()
        CABINET_XML_IDS = (
            "southbrook_estimating.wall_1dr",
            "southbrook_estimating.wall_2dr",
            "southbrook_estimating.base_1dr",
            "southbrook_estimating.base_2dr",
            "southbrook_estimating.drawer_bank",
            "southbrook_estimating.sink_base",
            "southbrook_estimating.tall_pantry",
            "southbrook_estimating.tall_oven",
            "southbrook_estimating.corner",
            "southbrook_estimating.vanity",
            "southbrook_estimating.accessory",
            "southbrook_estimating.worktop",
        )
        # ir.model.data is itself sudo'd here so portal users can
        # resolve the xml_ids without triggering product.template
        # access checks.
        ImD = request.env["ir.model.data"].sudo()
        catalog = []
        for xml_id in CABINET_XML_IDS:
            module, name = xml_id.split(".", 1)
            tmpl_id = ImD._xmlid_to_res_id(xml_id, raise_if_not_found=False)
            if not tmpl_id:
                continue
            tmpl = Tmpl.browse(tmpl_id)  # already sudo'd above
            if not tmpl.exists():
                continue
            # SKU + base-variant resolution. Walked in one pass so the
            # SKU-resolution and channel-price-resolution paths share
            # the same variant (the lowest-id product.product with a
            # default_code; otherwise the lowest-id variant overall).
            #
            # SKU priority:
            #   1. Lowest-id variant with non-empty default_code
            #      (the canonical product.product SKU)
            #   2. template.default_code if still set (single-variant
            #      case)
            #   3. Empty string — frontend falls back to displaying
            #      just the name
            sorted_variants = tmpl.product_variant_ids.sorted("id")
            base_variant = sorted_variants[:1]
            sku = ""
            for variant in sorted_variants:
                if variant.default_code:
                    sku = variant.default_code
                    base_variant = variant
                    break
            if not sku and tmpl.default_code:
                sku = tmpl.default_code

            # 2026-06-02 redesign: read display metadata off the
            # template's southbrook_* fields. Translatable fields
            # (southbrook_description) resolve in the request's
            # active language automatically — Tmpl was browsed with
            # the current env (request.env.user's lang), so accessing
            # tmpl.southbrook_description here returns the translated
            # value for that user. No explicit with_context(lang=...)
            # needed for the public-facing customer flow.

            # 2026-06-02 — channel-aware preview price (display-only;
            # add-line endpoint and order-line pricing are unchanged
            # and still flow through standard product_id_change /
            # pricelist resolution at order time).
            #
            # Compute via the canonical Odoo pricelist engine on the
            # base variant, then round to the website currency's
            # decimal_places so display matches what an order line
            # will round to at confirm. Guarded — any exception or
            # missing prereq falls back to tmpl.list_price (retail).
            channel_price = tmpl.list_price
            if channel_pricelist:
                # Cabinet templates use create_variant='dynamic' so
                # no product.product variants exist until a customer
                # configures one. The catalog endpoint can't force
                # variant creation just to preview a price, so we
                # fall back to pricing against the template itself —
                # which _get_product_price accepts per its docstring
                # ("product record (product.product/product.template)").
                priceable = base_variant or tmpl
                try:
                    # Odoo 19: _compute_price_rule signature changed
                    # to (products, quantity, *, currency, uom, date,
                    # compute_price, **kwargs) — currency is keyword-
                    # only after *. The legacy call _get_product_price(
                    # variant, 1.0, partner) was passing partner as a
                    # third positional, which raised TypeError that
                    # the broad except swallowed — every cabinet fell
                    # back to list_price and the Tradesperson Tier 3
                    # discount never reached the catalog.
                    # Partner context for partner-specific rules is
                    # carried via with_context(partner_id=...).
                    channel_price = channel_pricelist.with_context(
                        partner_id=partner.id if partner else None,
                    )._get_product_price(priceable, 1.0)
                except Exception:                       # noqa: BLE001
                    _logger.warning(
                        "kitchen_planner_state: pricelist %s could not "
                        "price cabinet %s (variant id=%s); falling back "
                        "to list_price for display.",
                        channel_pricelist.display_name,
                        sku or tmpl.name,
                        base_variant.id if base_variant else None,
                        exc_info=True,
                    )
            # Phase 4 Sprint 5 — multi-currency awareness.
            # Convert list_price + channel_price from the source
            # currency (template's currency_id, typically the company
            # currency) to the display currency (website.currency_id).
            # When the two match, _convert is a no-op so this is a
            # zero-cost change for single-currency installs.
            display_list = tmpl.list_price
            tmpl_currency = (
                tmpl.currency_id
                or request.env.company.currency_id
            )
            if currency and tmpl_currency and currency != tmpl_currency:
                try:
                    display_list = tmpl_currency._convert(
                        tmpl.list_price,
                        currency,
                        request.env.company,
                        fields.Date.today(),
                    )
                    if channel_price != tmpl.list_price:
                        # channel_price came from the pricelist engine
                        # which already prices in the pricelist's
                        # currency; convert IT only if the pricelist's
                        # currency differs from the display currency.
                        pl_currency = (
                            channel_pricelist and channel_pricelist.currency_id
                        )
                        if pl_currency and pl_currency != currency:
                            channel_price = pl_currency._convert(
                                channel_price,
                                currency,
                                request.env.company,
                                fields.Date.today(),
                            )
                except Exception:                       # noqa: BLE001
                    # Conversion fails on missing exchange rate; fall
                    # back to the raw source values so the card still
                    # renders something rather than 500'ing.
                    _logger.warning(
                        "kitchen_planner_state: currency conversion "
                        "%s -> %s failed for cabinet %s; using raw "
                        "source values.",
                        tmpl_currency.name, currency.name,
                        sku or tmpl.name,
                        exc_info=True,
                    )
            if currency:
                display_list = currency.round(display_list)
                channel_price = currency.round(channel_price)
            catalog.append({
                "id": tmpl.id,
                "sku": sku,
                "name": tmpl.name,
                "list_price": display_list,
                "channel_price": channel_price,
                "family": self._family_from_sku(sku),
                "category": (
                    tmpl.southbrook_category
                    or self._CATALOG_DEFAULTS["category"]
                ),
                "description": (
                    tmpl.southbrook_description
                    or self._CATALOG_DEFAULTS["description"]
                ),
                "dimensions": (
                    tmpl.southbrook_dimensions
                    or self._CATALOG_DEFAULTS["dimensions"]
                ),
                "icon": (
                    tmpl.southbrook_icon_key
                    or self._CATALOG_DEFAULTS["icon"]
                ),
            })

        # Partner channel resolution — informs the planner's
        # "your tier / dealer" badge in the viewport corner. Customer
        # users default to channel='retail' if their partner has no
        # channel set.
        channel = getattr(partner, "channel", None) or "retail"

        return {
            "ok": True,
            "user": {
                "partner_id": partner.id,
                "partner_name": partner.name,
                "channel": channel,
                # 2026-06-02 — pricelist display name (e.g. "Dealer
                # (-50%)", "Contractor Tier 3 (-35%)", "Retail (List
                # Price)") for the CatalogPicker channel badge.
                "channel_label": channel_label,
            },
            "catalog": catalog,
            "currency": {
                "symbol": currency.symbol or "$",
                "position": currency.position or "before",
                "decimal_places": currency.decimal_places or 2,
                "name": currency.name or "USD",
            },
            "session": None,
        }

    # Channel discount tables — same shape as SouthbrookOrderBuilderPortal's
    # version. Duplicated rather than shared via a mixin because the two
    # controllers ship in the same module + the dict is 6 entries; a
    # base mixin would obscure more than it would save. Phase-3 polish
    # consolidates if a third controller needs them.
    _CHANNEL_META = {
        "dealer":       {"label": "DEALER · -50%",            "discount_pct": 50, "css": "dealer"},
        "tradesperson": {"label": "CONTRACTOR · Tiered",      "discount_pct": 0,  "css": "tradesperson"},
        "kd":           {"label": "CENTRAL KD",               "discount_pct": 54, "css": "kd"},
        "bigbox":       {"label": "BIG-BOX WHOLESALE",        "discount_pct": 33, "css": "bigbox"},
        "refacing":     {"label": "REFACING · CTHS",          "discount_pct": 35, "css": "refacing"},
        "retail":       {"label": "RETAIL · list price",      "discount_pct": 0,  "css": "retail"},
    }
    _TRADESPERSON_TIER_DISCOUNT = {"1": 25, "2": 30, "3": 35}

    # Helper — derive family code from SKU. Mirrors the Q8 family
    # taxonomy without re-deriving the full _SKU_DEFAULTS table
    # (which lives on product.config.line in southbrook_estimating).
    _FAMILY_BY_PREFIX = {
        "SB-WALL":     "wall",
        "SB-BASE":     "base",
        "SB-DRAWER":   "drawer",
        "SB-SINK":     "sink",
        "SB-TALL":     "tall",
        "SB-CORNER":   "corner",
        "SB-VANITY":   "vanity",
        "SB-ACCESSORY": "accessory",
        "SB-WORKTOP":  "worktop",
    }

    def _family_from_sku(self, sku):
        for prefix, family in self._FAMILY_BY_PREFIX.items():
            if sku.startswith(prefix):
                return family
        return ""

    # 2026-06-02 catalog-picker redesign — display metadata fields.
    #
    # The four southbrook_* fields are now defined on product.template
    # (southbrook_category, southbrook_description, southbrook_dimensions,
    # southbrook_icon_key) and seeded by
    # southbrook_estimating/data/cabinet_catalog_metadata.xml.
    # kitchen_planner_state reads them off each template below and
    # emits them under unprefixed keys in the JSON payload (category /
    # description / dimensions / icon) so the OWL CatalogPicker
    # contract stays unchanged.
    #
    # Defaults applied at the controller layer for cabinets missing
    # metadata (e.g. third-party templates added to CABINET_XML_IDS
    # without a metadata seed row): category=Extras, icon=extra,
    # description/dimensions empty strings.
    _CATALOG_DEFAULTS = {
        "category": "Extras",
        "description": "",
        "dimensions": "",
        "icon": "extra",
    }

    # ==================================================================
    # Phase 2 commit 4 — session create + attribute discovery.
    #
    # User clicks a catalog tile → frontend posts to .../session/create
    # with the template id → backend creates a product.config.session
    # (the OCA configurator's per-user-config record) and returns:
    #
    #   { ok, session_id, template: {id, sku, name, family, list_price},
    #     attributes: [ {attribute_id, name, display_type,
    #                    values: [{value_id, name, price_extra,
    #                              html_color}, ...]}, ... ] }
    #
    # Attributes ARE the configurator decisions: series / box_material /
    # door_style / colour / hinge / etc. — the 11-attribute set per Q2.
    # Each value carries the OCA price_extra so P2C5 can sum them into
    # the live total.
    #
    # P2C4 does NOT yet:
    #   - Write a value selection to session.value_ids (P2C5)
    #   - Compute price from value_ids (P2C5)
    #   - Commit session → materialise variant → add to sale.order (P2C6)
    # ==================================================================
    @http.route(
        "/southbrook/api/kitchen-planner/session/create",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def kitchen_planner_session_create(self, template_id=None, **kw):
        if not template_id:
            return {"error": "missing_template_id"}
        template = request.env["product.template"].sudo().browse(
            int(template_id)).exists()
        if not template:
            return {"error": "template_not_found"}
        if not template.config_ok:
            return {"error": "not_configurable"}

        session = request.env["product.config.session"].sudo().create({
            "product_tmpl_id": template.id,
            "user_id": request.env.user.id,
        })

        return {
            "ok": True,
            "session_id": session.id,
            "template": {
                "id": template.id,
                "sku": template.default_code or "",
                "name": template.name,
                "family": self._family_from_sku(template.default_code or ""),
                "list_price": template.list_price,
            },
            "attributes": self._serialize_template_attributes(template),
        }

    def _serialize_template_attributes(self, template):
        """Walk template.attribute_line_ids → drawer-ready shape.

        Each attribute line carries:
          - the product.attribute (name, display_type)
          - the subset of product.attribute.value records exposed for
            this template (the line's value_ids)
          - per-value price_extra (default_extra_price on the value
            unless the line carries an override — OCA stores per-line
            overrides on product.template.attribute.value).
        """
        out = []
        for line in template.attribute_line_ids:
            attribute = line.attribute_id
            values = []
            for tav in line.product_template_value_ids:
                values.append({
                    "value_id": tav.product_attribute_value_id.id,
                    "name": tav.name,
                    "price_extra": tav.price_extra or 0.0,
                    "html_color": (
                        tav.product_attribute_value_id.html_color or ""
                    ),
                    "default_extra": (
                        tav.product_attribute_value_id.default_extra_price
                        or 0.0
                    ),
                })
            out.append({
                "attribute_id": attribute.id,
                "name": attribute.name,
                "display_type": attribute.display_type or "radio",
                "values": values,
            })
        return out

    @http.route(
        "/southbrook/api/kitchen-planner/session/<int:session_id>/cancel",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def kitchen_planner_session_cancel(self, session_id, **kw):
        """Tear down a session when the user abandons the drawer.

        OCA's product.config.session uses state='cancel' as the
        terminal-abandoned state. We mark + leave for the Phase-3
        cleanup job; the session row stays for analytics
        (configurations attempted but not completed).
        """
        session = request.env["product.config.session"].sudo().browse(
            session_id).exists()
        if not session:
            return {"error": "not_found"}
        if session.user_id != request.env.user:
            return {"error": "forbidden"}
        session.write({"state": "cancel"})
        return {"ok": True}

    # ==================================================================
    # Phase 2 commit 5 — set / add / remove a value + live pricing.
    #
    # Three actions (one endpoint) — driven by the OWL drawer:
    #
    #   action='set'    Default. For radio / select / color attributes.
    #                   Replaces any existing value for that attribute,
    #                   then adds the new one. Idempotent: clicking the
    #                   same selected chip "deselects" by removing
    #                   without re-adding.
    #
    #   action='add'    For multi attributes (e.g. Accessories). Adds
    #                   the value without touching other values on the
    #                   same attribute.
    #
    #   action='remove' For multi attributes. Removes the value.
    #
    # Response shape mirrors session_create but with:
    #
    #   selected_values: [value_id, ...] — current session.value_ids
    #   price:           OCA's session.price (the live total, base
    #                    list_price + sum of selected value
    #                    price_extras + rule-driven uplifts)
    #   channel_total:   price × (1 - discount_pct/100) — same
    #                    controller-side discount as Track 2 uses
    #
    # Rule-violation handling: any UserError raised by OCA's
    # _onchange or write hooks is caught and returned as
    # {ok: False, error: rule_blocked, message: <text>} so the
    # frontend can surface it in the drawer without losing state.
    # ==================================================================
    @http.route(
        "/southbrook/api/kitchen-planner/session/<int:session_id>/set-value",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def kitchen_planner_session_set_value(
        self, session_id, attribute_id=None, value_id=None,
        action="set", **kw,
    ):
        if not attribute_id or not value_id:
            return {"error": "missing_args"}

        session = request.env["product.config.session"].sudo().browse(
            session_id).exists()
        if not session:
            return {"error": "not_found"}
        if session.user_id != request.env.user:
            return {"error": "forbidden"}

        attribute_id = int(attribute_id)
        value_id = int(value_id)

        # OCA's session has a custom write path — direct
        # session.write({'value_ids': [...]}) does NOT persist due to
        # its _check_value_ids constraint AND the wizard-state coupling.
        # The documented OCA API is session.update_config(attr_val_dict)
        # which uses (6,0,[full_list]) replacement and handles
        # duplicate filtering internally.
        #
        # Build the NEW list of value_ids for this attribute based on
        # the action + current selection.
        same_attr_ids = set(
            session.value_ids.filtered(
                lambda v: v.attribute_id.id == attribute_id
            ).ids
        )

        if action == "remove":
            new_attr_vals = list(same_attr_ids - {value_id})
        elif action == "add":
            new_attr_vals = list(same_attr_ids | {value_id})
        elif action == "set":
            # Re-clicking the only selected value deselects.
            if same_attr_ids == {value_id}:
                new_attr_vals = []
            else:
                new_attr_vals = [value_id]
        else:
            return {"error": "unknown_action"}

        try:
            session.update_config({attribute_id: new_attr_vals})
        except Exception as exc:                    # noqa: BLE001
            # OCA's config-rule hooks raise UserError when a value
            # picks an excluded combo. Surface the message inline.
            return {
                "ok": False,
                "error": "rule_blocked",
                "message": getattr(exc, "args", [str(exc)])[0],
            }

        # Read-back: refresh session-level fields after the write.
        session = session.exists()
        price = getattr(session, "price", 0.0) or 0.0

        # Channel discount — same path Track 2 uses. Customer-mode
        # planner users default to channel='retail' (discount 0%).
        partner = request.env.user.partner_id
        channel = getattr(partner, "channel", None) or "retail"
        if channel == "tradesperson":
            tier = (getattr(partner, "tradesperson_tier", "1") or "1")
            discount = self._TRADESPERSON_TIER_DISCOUNT.get(tier, 0)
        else:
            discount = (self._CHANNEL_META.get(channel) or {}).get(
                "discount_pct", 0
            )
        channel_total = price * (1 - discount / 100.0)

        return {
            "ok": True,
            "session_id": session.id,
            "selected_values": session.value_ids.ids,
            "price": price,
            "channel_total": channel_total,
            "discount_pct": discount,
        }


class SouthbrookOrderBuilderPortal(_SouthbrookOrderAccessMixin, CustomerPortal):
    """Portal route hosting the OWL Order Builder."""

    # T2C1 NF: website=True is required after all. The portal layout
    # template (portal.frontend_layout, called via portal.portal_layout)
    # references the `website` variable in scope, so without
    # website=True the render aborts with KeyError: 'website'.
    # With website=True Odoo injects the current website record (id 1
    # "My Website" on the southbrook stack) into the template context.
    # ==================================================================
    # G9 (customer-flow JTBD gap analysis, 2026-06-01) — self-service
    # order creation.
    #
    # Pre-fix: portal customers had no way to start a new quote — the
    # Order Builder required an order_id in the URL, but only admins
    # could create that order in the backend. Dead-end Priority-1
    # blocker.
    #
    # This route creates a draft sale.order for the logged-in user
    # then redirects to the Order Builder with the new id, closing
    # the self-service loop. Phase-3 polish will add a project_name
    # prompt before creation; today we let Odoo auto-name (S0XXXX).
    # ==================================================================
    @http.route(
        "/my/southbrook/order-builder/new",
        type="http",
        auth="user",
        website=True,
    )
    def southbrook_order_builder_new(self, **kw):
        """Create a draft sale.order bound to the current user's partner,
        then redirect to the Order Builder for it.

        Sudo because portal users typically lack direct sale.order
        write rights; the partner_id binding makes the order accessible
        through the existing _southbrook_resolve_order auth check.

        G4 + G5 + G8 (2026-06-01): accept a project name from either
        a `?name=` query param OR a session key stashed by the signup
        controller, and apply it to client_order_ref so the customer's
        first quote is labelled the way they asked. The query-param
        path lets the homepage CTA optionally pre-label without a
        signup flow; the session path is the registration handoff.
        """
        partner = request.env.user.partner_id
        project_name = (
            (kw.get("name") or "").strip()
            or request.session.pop(
                SouthbrookAuthSignup._SESSION_KEY, ""
            ).strip()
        )
        vals = {"partner_id": partner.id}
        if project_name:
            vals["client_order_ref"] = project_name[:128]
        order = request.env["sale.order"].sudo().create(vals)
        return request.redirect(
            "/my/southbrook/order-builder/%s" % order.id
        )

    # ==================================================================
    # Customer identity capture (2026-07-06).
    #
    # The Order Builder is a staff/dealer tool — the logged-in user
    # (an employee, e.g. Administrator) builds an order ON BEHALF OF a
    # customer, so the order MUST be tied to a distinct, captured
    # customer contact, never the employee's own partner. This endpoint
    # takes the customer's details from the Order Builder's Customer
    # panel, find-or-creates the res.partner (via the shared
    # _southbrook_resolve_customer resolver), and re-points the order.
    # ==================================================================
    @http.route(
        "/my/southbrook/order-builder/<int:order_id>/set-customer",
        type="jsonrpc",
        auth="user",
        website=True,
    )
    def southbrook_set_order_customer(self, order_id, **kw):
        order = self._southbrook_resolve_order(order_id)  # access-checked
        # Security (2026-07-11 audit MEDIUM-1): this is a staff/dealer Order
        # Builder tool. It resolves-or-backfills a res.partner by email with
        # trusted=True (fills a matched partner's blank name/phone/street from
        # the payload). A portal customer (share=True) must not be able to use
        # it to tamper with another contact's PII — restrict to internal users.
        if request.env.user.share:
            return {"ok": False,
                    "error": "Only staff may set the order's customer."}
        name = (kw.get("name") or "").strip()
        email = (kw.get("email") or "").strip()
        if not name and not email:
            return {"ok": False,
                    "error": "Enter the customer's name or email."}
        vals = {
            k: (kw.get(k) or "").strip()
            for k in ("name", "email", "phone", "street", "street2",
                      "city", "zip", "function")
        }
        vals["is_company"] = bool(kw.get("is_company"))
        partner = request.env["res.partner"].sudo()._southbrook_resolve_customer(
            vals, trusted=True)
        order.sudo().write({"partner_id": partner.id})
        return {
            "ok": True,
            "partner_id": partner.id,
            "partner_name": partner.name,
            "partner_email": partner.email or "",
            "partner_phone": partner.phone or "",
        }

    @http.route(
        ["/my/southbrook/order-builder",
         "/my/southbrook/order-builder/<int:order_id>"],
        type="http",
        auth="user",
        website=True,
    )
    def southbrook_order_builder(self, order_id=None, **kw):
        """Render the Order Builder portal page.

        Without order_id: shows the dealer's list of open orders so
        they can pick one (Phase 2 commit 3 polish).

        With order_id: validates access (the partner OR their parent
        partner must own the order), then renders the OWL mount-point
        template with the order context.
        """
        order = None
        if order_id is not None:
            try:
                order = self._southbrook_resolve_order(order_id)
            except (AccessError, MissingError):
                return request.redirect("/my")

        values = self._prepare_southbrook_portal_values(order)
        return request.render(
            "southbrook_estimating_website.portal_order_builder",
            values,
        )

    # ------------------------------------------------------------------
    # Auth helpers — `_southbrook_resolve_order` lives on the shared
    # `_SouthbrookOrderAccessMixin` above so RoomApi + KitchenPlanner
    # can reuse it via MRO.
    # ------------------------------------------------------------------

    def _prepare_southbrook_portal_values(self, order):
        """Common template context (sidebar, breadcrumb, palette tokens)."""
        values = self._prepare_portal_layout_values()
        # G14 (2026-06-01) — auto-select customer mode for portal users
        # (res.users.share=True). Internal users still get dealer mode
        # by default so the dealer/sales-rep workflow is unchanged.
        # Override to customer via ?mode=customer on the URL.
        #
        # 2026-07-03 — this used to be resolved client-side in the OWL
        # bootstrap (portal_boot mountOrderBuilder). Now that OrderBuilder
        # mounts via the public_components registry, its props come from
        # the server-rendered `props` JSON attribute, so the mode is
        # resolved here. The rule mirrors the prior client logic exactly:
        # customer iff the user is a portal user OR ?mode=customer is
        # present (there was never a path that forced dealer via URL).
        user = request.env.user
        is_portal_user = bool(user.share)
        # Case-sensitive, untrimmed — EXACT parity with the prior client
        # check `URLSearchParams.get("mode") === "customer"`. (Deliberately
        # not .lower()/.strip(): a dealer must not be dropped into the
        # reduced customer surface by ?mode=Customer / trailing space.)
        url_mode = request.httprequest.args.get("mode") or ""
        order_mode = (
            "customer" if (is_portal_user or url_mode == "customer") else "dealer"
        )
        values.update({
            "page_name": "southbrook_order_builder",
            "order": order,
            "order_id": order.id if order else None,
            "order_name": order.name if order else "New Order",
            "user_partner": request.env.user.partner_id,
            # The mount <div> keeps this id + the .o_southbrook_owl_mount
            # class purely as the SCSS scope and the southbrook_hermes
            # chat-inject XPath anchor. (The old data-order-id/-name/-mode
            # attributes were dropped — their only reader was the deleted
            # mountOrderBuilder bootstrap; props now flow via owl_props_json.)
            "owl_mount_id": "order_builder_root",
            # Kept in the render context for any inheriting view that may
            # branch on mode; the OWL app itself receives it via owl_props_json.
            "order_mode": order_mode,
            # 2026-07-03 — props for the <owl-component> that mounts
            # OrderBuilder via the public_components registry. Read as
            # JSON by web's PublicComponentInteraction. orderId is a
            # STRING to match OrderBuilder.props (type: String) and the
            # prior dataset-derived value.
            "owl_props_json": json.dumps({
                "orderId": str(order.id) if order else "",
                "orderName": order.name if order else "New Order",
                "mode": order_mode,
            }),
        })
        return values

    # ==================================================================
    # T2C4 — JSON-RPC: /southbrook/api/order/<id>
    #
    # Returns the normalised order payload the OWL `<OrderBuilder/>`
    # store (commit 5) will consume. Shape mirrors the mockup's
    # state.order + state.lines + state.zones objects so the
    # client-side reducer barely needs to transform.
    # ==================================================================

    # Channel → human label + discount %. Mirrors the dispatcher in
    # southbrook_estimating.sale_order. Phase 3 polish reads these
    # from a database table so non-engineers can edit labels.
    _CHANNEL_META = {
        "dealer":       {"label": "DEALER · -50%",            "discount_pct": 50, "css": "dealer"},
        "tradesperson": {"label": "CONTRACTOR · Tiered",      "discount_pct": 0,  "css": "tradesperson"},
        "kd":           {"label": "CENTRAL KD",               "discount_pct": 54, "css": "kd"},
        "bigbox":       {"label": "BIG-BOX WHOLESALE",        "discount_pct": 33, "css": "bigbox"},
        "refacing":     {"label": "REFACING · CTHS",          "discount_pct": 35, "css": "refacing"},
        "retail":       {"label": "RETAIL · list price",      "discount_pct": 0,  "css": "retail"},
    }
    _TRADESPERSON_TIER_DISCOUNT = {"1": 25, "2": 30, "3": 35}

    # T2C10 — ConfigDrawer autosave endpoint.
    #
    # The OWL drawer fires this when the user edits Qty (the only
    # editable field in commit 10 — Phase 3 polish extends to
    # attribute pickers + custom-spec text). Returns {ok: true} on
    # success; the frontend then re-fetches /api/order/<id> to
    # refresh prices, line subtotals, zone subtotals, header totals.
    #
    # Auth: same partner-chain resolver as the read endpoint. Reuses
    # the order's resolver via line.order_id.
    @http.route(
        "/southbrook/api/line/<int:line_id>/update",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_line_update(self, line_id, qty=None, **kw):
        """Apply a partial edit to a sale.order.line.

        Phase 2 commit 10 surface: qty only. The frontend may send
        other keys (e.g. zone, spec text) — they're ignored without
        error so commit-11+ doesn't need a separate version field.
        """
        line = request.env["sale.order.line"].sudo().browse(line_id).exists()
        if not line:
            return {"error": "not_found"}
        try:
            self._southbrook_resolve_order(line.order_id.id)
        except (AccessError, MissingError):
            return {"error": "forbidden"}

        # Apply qty update if present.
        # 2026-06-27 — qty == 0 now deletes the line (was: error). This
        # mirrors the new explicit /delete endpoint so the OWL trash-icon
        # path and the qty-stepper-to-zero path both work without the
        # frontend needing to branch. Hard cap at 999 prevents an
        # accidental "1e9" paste from triggering an ORM amount overflow
        # (per the backend review's P3 hardening item).
        if qty is not None:
            try:
                qty_f = float(qty)
            except (TypeError, ValueError):
                return {"error": "invalid_qty"}
            if qty_f < 0 or qty_f > 999:
                return {"error": "invalid_qty"}
            if qty_f == 0:
                line.with_user(request.env.user).unlink()
                return {"ok": True, "line_id": line_id, "deleted": True}
            line.with_user(request.env.user).product_uom_qty = qty_f

        return {"ok": True, "line_id": line.id}

    # 2026-06-27 — explicit delete endpoint. Frontend can also reach
    # delete via /update with qty=0, but a dedicated route reads better
    # in logs + makes the trash-icon click handler unambiguous (no need
    # for the OWL code to know about the qty=0 convention).
    @http.route(
        "/southbrook/api/line/<int:line_id>/delete",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_line_delete(self, line_id, **kw):
        line = request.env["sale.order.line"].sudo().browse(line_id).exists()
        if not line:
            # Idempotent: already-gone is a success.
            return {"ok": True, "line_id": line_id, "deleted": True}
        try:
            self._southbrook_resolve_order(line.order_id.id)
        except (AccessError, MissingError):
            return {"error": "forbidden"}
        # Cannot delete from a confirmed/done order via portal.
        if line.order_id.state not in ("draft", "sent"):
            return {"error": "order_locked"}
        line.with_user(request.env.user).unlink()
        return {"ok": True, "line_id": line_id, "deleted": True}

    # G15 (customer-flow JTBD gap 2026-06-01) — line attribute picker
    # endpoints backing the inline drawer.
    #
    # Pre-fix: customers could add cabinets to their order (G11) but
    # had no way to configure them — door style, finish, width, etc.
    # all stayed locked to whatever default variant the add-line
    # endpoint materialised. The drawer placeholder said 'Phase 3
    # polish opens the full attribute picker'. This is that polish.
    #
    # Approach:
    #   1. /line/<id>/attributes  — read shape: returns the product
    #      template's attribute_line_ids with current selection per
    #      attribute (or null when the variant has no value for that
    #      attribute, which is the case for lines added via the
    #      default-variant fast path in /add-line).
    #   2. /line/<id>/set-attribute — write: takes one (attribute_id,
    #      value_id) at a time. Resolves the new variant via Odoo's
    #      _get_variant_for_combination (or creates one for dynamic-
    #      variant templates). Stamps line.product_id and trips the
    #      Odoo onchange that recomputes price_unit/name. Returns the
    #      refreshed line shape so the OWL store can drop the
    #      response straight into state without a second fetch.
    #
    # Auth: piggybacks on _southbrook_resolve_line which walks the
    # line → order.partner → user-partner chain (same gate as the
    # qty autosave at /api/line/<id>/update).
    def _southbrook_resolve_line(self, line_id):
        """Return the sale.order.line if the current user owns its
        order; raise AccessError otherwise. Mirrors the partner-chain
        check used by /api/order/<id>.

        2026-07-01 E2E website audit — this used to raise MissingError
        when the line didn't exist and AccessError when it did but
        wasn't owned. That distinction is an existence oracle: a probe
        of line IDs across orders can tell the difference between
        "doesn't exist" (`not_found`) and "belongs to someone else"
        (`forbidden`). Room API (`room_api.py:_get_room_scoped`) fixed
        this convention months earlier: return AccessError for both
        missing AND wrong-owner. Match that here.
        """
        line = (
            request.env["sale.order.line"]
            .sudo()
            .browse(line_id)
            .exists()
        )
        if not line:
            # Same as room_api's convention — collapse "missing" and
            # "not yours" into a single AccessError so callers cannot
            # distinguish the two from the outside.
            raise AccessError("line not accessible")
        # Walks back through the order to the existing resolver,
        # which is the canonical access check. Any MissingError from
        # the order resolver is likewise collapsed to AccessError so
        # a deleted order under a foreign partner doesn't leak either.
        try:
            self._southbrook_resolve_order(line.order_id.id)
        except MissingError:
            raise AccessError("line not accessible")
        return line

    @http.route(
        "/southbrook/api/line/<int:line_id>/attributes",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_line_attributes(self, line_id, **kw):
        try:
            line = self._southbrook_resolve_line(line_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}

        tmpl = line.product_id.product_tmpl_id
        PAV = request.env["product.attribute.value"].sudo()

        # Effective current values for the line — two sources.
        # Primary: variant attribute values (empty for default fast-path
        # variants created by /add-line).
        current_ptav = line.product_id.product_template_attribute_value_ids
        line_value_ids = set(current_ptav.product_attribute_value_id.ids)
        # Fallback: dimensional sb_*_mm fields on sale.order.line. The
        # default fast-path variant has no PTAVs, but sb_width_mm (etc.)
        # still carries the chosen dimension. Map mm → "<N> in" attribute
        # value name so the picker can pre-select even before any /set-
        # attribute call resolves a configured variant.
        def _value_for_mm(attr_xmlid, mm):
            if not mm:
                return None
            attr = request.env.ref(attr_xmlid, raise_if_not_found=False)
            if not attr:
                return None
            v = PAV.search(
                [
                    ("attribute_id", "=", attr.id),
                    ("name", "=", "%d in" % round(mm / 25.4)),
                ],
                limit=1,
            )
            return v.id if v else None

        fallback_width = _value_for_mm(
            "southbrook_estimating.attr_width", line.sb_width_mm,
        )
        if fallback_width:
            line_value_ids.add(fallback_width)
        # Future: extend with sb_height_mm / sb_depth_mm once attr_height
        # / attr_depth ship in the data. Same pattern.

        # Group line_value_ids by attribute_id for per-attribute lookup.
        line_values_by_attr = {}
        for v in PAV.browse(list(line_value_ids)):
            line_values_by_attr.setdefault(
                v.attribute_id.id, set(),
            ).add(v.id)

        # 2026-06-27 L2 — rule-block resolution per value.
        # Calls OCA's product.config.session.values_available so the
        # combobox can show ALL values with disabled-state for those
        # blocked by the current selection (e.g. Contractor series →
        # all door styles except thermofoil_slab_white). Frontend
        # renders disabled values at the bottom with a hover tooltip.
        Session = request.env["product.config.session"].sudo()
        current_pav_ids = list(line_value_ids)

        attributes = []
        for attr_line in tmpl.attribute_line_ids:
            # Hide attributes with a single option — nothing to pick.
            if len(attr_line.value_ids) < 2:
                continue
            attr_id = attr_line.attribute_id.id
            # Union: template-allowed values + the line's effective current
            # value. Without the union, a line whose stored value sits
            # outside the template's allowed set (e.g. SB-BASE-1DR at 24 in,
            # outside the 9-21 in band the business rule keeps for 1-doors)
            # would render with the current value missing, defaulting to
            # "— pick —". The union keeps whatever the line actually has
            # selectable so users can keep it or switch.
            extra_ids = (
                line_values_by_attr.get(attr_id, set())
                - set(attr_line.value_ids.ids)
            )
            all_values = attr_line.value_ids + PAV.browse(list(extra_ids))
            # Sort by sequence (Odoo standard) then id — keeps 24 in AFTER
            # 21 in in the Width picker, etc.
            all_values = all_values.sorted(
                key=lambda v: (v.sequence or 0, v.id),
            )

            # 2026-06-27 L2 — per-value allowed/blocked check.
            # values_available() is an @api.model on product.config.session
            # so we can call it without instantiating a real session. The
            # current PTAV combination (current_pav_ids) becomes
            # the "selected so far" context; for each candidate value we
            # ask "would this value be available given the current
            # selection?" If not, mark blocked and emit a short reason.
            try:
                allowed_ids = Session.values_available(
                    check_val_ids=all_values.ids,
                    value_ids=current_pav_ids,
                    custom_vals={},
                    product_tmpl_id=tmpl.id,
                    product_template_attribute_line_id=attr_line.id,
                )
                allowed_set = set(allowed_ids)
            except Exception:
                # Defensive: if OCA's machinery errors on a malformed
                # rule, treat all values as allowed rather than locking
                # the user out of the picker entirely. Frontend still
                # functions; only the rule-block badge goes missing.
                allowed_set = set(all_values.ids)

            attributes.append({
                "attribute_id": attr_id,
                "name": attr_line.attribute_id.name,
                "display_type": (
                    attr_line.attribute_id.display_type or "select"
                ),
                "values": [
                    {
                        "value_id": v.id,
                        "name": v.name,
                        "current": v.id in line_value_ids,
                        # 2026-06-27 L2 — allowed/blocked status from
                        # values_available. The combobox renders
                        # blocked options disabled, grouped at the
                        # bottom, with reason as the title tooltip.
                        "allowed": v.id in allowed_set,
                        "reason": (
                            "" if v.id in allowed_set
                            else "Blocked by current selection"
                        ),
                    }
                    for v in all_values
                ],
            })
        return {"ok": True, "attributes": attributes}

    @http.route(
        "/southbrook/api/line/<int:line_id>/set-attribute",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_line_set_attribute(
        self, line_id, attribute_id=None, value_id=None, **kw,
    ):
        try:
            line = self._southbrook_resolve_line(line_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}

        if not (attribute_id and value_id):
            return {"error": "missing_params"}
        if line.order_id.state not in ("draft", "sent"):
            return {"error": "order_locked", "state": line.order_id.state}

        Tmpl = request.env["product.template"].sudo()
        tmpl = line.product_id.product_tmpl_id

        # Find the template-attribute-value record corresponding to
        # (attribute_id, value_id). product_template_value_ids is the
        # 'PTAV' join row; we filter on the underlying value + the
        # attribute_id so the lookup is unambiguous for multi-attr lines.
        ptav_for_target = tmpl.attribute_line_ids.product_template_value_ids.filtered(
            lambda v: (
                v.product_attribute_value_id.id == int(value_id)
                and v.attribute_id.id == int(attribute_id)
            )
        )
        if not ptav_for_target:
            return {"error": "value_not_in_template"}

        # Build the new combination = current values minus the old
        # value-for-this-attribute, plus the new one. For lines added
        # via the default-fast-path variant, current_ptav is empty,
        # so the new combination is just the single new ptav.
        current_ptav = line.product_id.product_template_attribute_value_ids
        old_ptav_for_attr = current_ptav.filtered(
            lambda v: v.attribute_id.id == int(attribute_id)
        )
        new_combination = (current_ptav - old_ptav_for_attr) | ptav_for_target

        # Resolve the matching variant via Odoo's combination lookup —
        # if an exact variant exists, reuse it.
        variant = tmpl._get_variant_for_combination(new_combination)

        # Else create one directly. The OCA configurator's create_variant
        # mode for the 12 Q8 templates does NOT auto-materialise via
        # tmpl._create_product_variant (it expects a product.config.session
        # to gate creation). For the customer self-serve flow we bypass
        # that by writing the variant row ourselves with the explicit
        # PTAV ids — the same end state the OCA wizard would produce,
        # minus the session bookkeeping. Phase-2 polish: when the
        # configurator session pathway lands for the customer, switch
        # this to invoke the session's commit (so rule validation +
        # price_extras run through OCA's canonical path).
        if not variant:
            variant = (
                request.env["product.product"]
                .sudo()
                .create({
                    "product_tmpl_id": tmpl.id,
                    "product_template_attribute_value_ids": [
                        (6, 0, new_combination.ids)
                    ],
                })
            )

        if not variant:
            return {"error": "could_not_resolve_variant"}

        # Stamp the line and reset name/price from the new variant
        # (price_unit recomputes from list_price + ptav extras).
        line.sudo().write({
            "product_id": variant.id,
        })
        # 2026-07-01 audit cleanup — the previous hasattr guard for
        # `product_id_change` was v16-era; v19 removed the method
        # entirely (recomputes fire via @api.onchange on price_unit
        # / product_uom / name automatically when product_id is
        # written). Guard was always-False in v19 → dead code.

        return {"ok": True}

    # G11 + G12 + G13 (customer-flow JTBD gap 2026-06-01) — add-line
    # endpoint backing the customer-facing CatalogPicker.
    #
    # Pre-fix: portal customers could not add cabinets to their own
    # orders. The Order Builder's empty-state placeholder told them
    # to "Add lines via the backend Order Builder" — an admin-only
    # path that defeats the whole self-service flow.
    #
    # POST {order_id, product_tmpl_id} → {ok, line_id} OR
    #   {error: 'forbidden'|'not_found'|'no_variant'|...}.
    #
    # Variant resolution: most of the 12 Q8 templates have
    # create_variant='dynamic' (per Q6), so product_variant_ids is
    # empty until a configurator session materialises a variant.
    # For the customer entry flow we materialise a base variant on
    # demand — they pick a cabinet today, refine attributes (G15-G17)
    # later. The default variant carries the template's list_price;
    # the channel pricelist + attribute price_extras apply when the
    # user steps into the configurator.
    # 2026-06-27 — family → zone mapping for auto-zoning per BE
    # reviewer P1#6. Falls back to base_run when the family doesn't
    # match (the legacy default-everything-to-base_run behaviour, just
    # narrowed). Explicit `zone` arg from the frontend always wins
    # (the per-zone "+ Add to <zone>" path already knows the target).
    _FAMILY_TO_ZONE = {
        "base":      "base_run",
        "sink":      "base_run",
        "corner":    "base_run",
        "drawer":    "base_run",
        "wall":      "wall",
        "tall":      "tall",
        "pantry":    "tall",
        "oven":      "tall",
        "island":    "island",
        "vanity":    "base_run",
        "worktop":   "accessory",
        "accessory": "accessory",
    }

    def _southbrook_resolve_zone(self, family, explicit_zone=None):
        """Pick the zone for a newly-added line. Explicit > family map
        > base_run fallback."""
        if explicit_zone:
            valid = {"base_run", "wall", "tall", "island",
                     "accessory", "other"}
            if explicit_zone in valid:
                return explicit_zone
        fam = (family or "").lower()
        for prefix, zone in self._FAMILY_TO_ZONE.items():
            if fam.startswith(prefix):
                return zone
        return "base_run"

    @http.route(
        "/southbrook/api/order/<int:order_id>/add-line",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_add_line(
        self, order_id, product_tmpl_id=None, qty=None, zone=None, **kw,
    ):
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}

        if not product_tmpl_id:
            return {"error": "missing_product_tmpl_id"}

        # Catalog-picker redesign 2026-06-02: optional qty arg carrying
        # the quantity stepper value through to the order line. Default
        # 1 keeps the original behaviour for any caller still passing
        # only product_tmpl_id.
        try:
            qty_int = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            qty_int = 1
        if qty_int < 1:
            qty_int = 1

        Template = request.env["product.template"].sudo()
        tmpl = Template.browse(int(product_tmpl_id)).exists()
        if not tmpl:
            return {"error": "template_not_found"}

        # Materialise the base variant if the template has none yet.
        # product_variant_ids is empty for the dynamic-variant cabinets
        # until a configurator session produces one.
        variant = tmpl.product_variant_ids[:1]
        if not variant:
            variant = (
                request.env["product.product"]
                .sudo()
                .create({"product_tmpl_id": tmpl.id})
            )

        # Block adding lines on confirmed / cancelled orders — keeps
        # the customer from accidentally editing a quote that's
        # already in production.
        if order.state not in ("draft", "sent"):
            return {"error": "order_locked", "state": order.state}

        # UX bugfix 2026-06-23 (Option B): merge identical configurations.
        # If this order already has a line for the SAME variant
        # (same product_id ≡ same template + same attribute combination,
        # since Odoo encodes attribute choices into variant identity),
        # increment the existing line's qty instead of creating a
        # duplicate. Fixes the "I clicked Add twice and got two
        # identical lines totalling qty 4" UX bug.
        #
        # Default-variant adds (no attributes configured yet) all share
        # product_variant_ids[:1] for the template, so two clicks of
        # the same catalog card merge with each other. Once a user
        # configures attributes on a line (via /set-attribute → variant
        # swap), the line's variant becomes specific and won't merge
        # with the default-variant counterpart — preserving the user's
        # intent that "this configured line is distinct".
        existing = (
            request.env["sale.order.line"]
            .sudo()
            .search(
                [
                    ("order_id", "=", order.id),
                    ("product_id", "=", variant.id),
                ],
                limit=1,
            )
        )
        if existing:
            existing.product_uom_qty = existing.product_uom_qty + qty_int
            return {
                "ok": True,
                "line_id": existing.id,
                "merged": True,
                "qty": existing.product_uom_qty,
            }

        # 2026-06-27 — derive zone from the product's family (looked up
        # via SKU defaults). Frontend can pass an explicit `zone` arg
        # which wins. Without this, every newly-added line landed in
        # `base_run` regardless of family, forcing the dealer to
        # re-zone wall/tall/island cabinets one at a time.
        sku = tmpl.default_code or ""
        sku_row = request.env["product.config.session"]._SKU_DEFAULTS.get(sku)
        family = sku_row[0] if sku_row else ""
        resolved_zone = self._southbrook_resolve_zone(family, zone)

        line = (
            request.env["sale.order.line"]
            .sudo()
            .create({
                "order_id": order.id,
                "product_id": variant.id,
                "product_uom_qty": qty_int,
                "zone": resolved_zone,
            })
        )
        # v19 removed `sale.order.line.product_id_change` — the
        # equivalent recomputes fire from @api.onchange('product_id')
        # on the ORM write above. Nothing needed here.

        # 2026-06-27 — smart attribute defaults (FE reviewer P1#7).
        # Inherit the prior in-zone line's attribute choices (door
        # style, finish, species…) so a kitchen-wide build of base
        # cabinets in matching Shaker White Maple takes 1 click per
        # cabinet instead of 6. Skipped silently when:
        #   • no prior line in this zone, OR
        #   • prior line has no PTAVs (default-variant fast path), OR
        #   • the new template doesn't expose any of the prior's
        #     attributes (e.g. wall-cabinet template with no door
        #     style attribute when copying from a tall pantry).
        inherited_attr_count = self._southbrook_inherit_prior_line_attrs(
            order, line, tmpl, resolved_zone,
        )

        return {
            "ok": True,
            "line_id": line.id,
            "zone": resolved_zone,
            "inherited_attrs": inherited_attr_count,
        }

    def _southbrook_inherit_prior_line_attrs(
        self, order, new_line, new_tmpl, zone,
    ):
        """Apply the prior in-zone line's PTAVs to the new line.

        Returns the number of attributes inherited (0 when no prior
        line / no PTAVs / no overlapping attributes — caller logs the
        count back to the frontend so the OWL UI could surface 'inherited
        N picks from line K' if it wanted).
        """
        prior = (
            request.env["sale.order.line"]
            .sudo()
            .search(
                [
                    ("order_id", "=", order.id),
                    ("zone", "=", zone),
                    ("id", "!=", new_line.id),
                ],
                order="sequence desc, id desc",
                limit=1,
            )
        )
        if not prior or not prior.product_id:
            return 0
        prior_ptavs = (
            prior.product_id.product_template_attribute_value_ids
        )
        if not prior_ptavs:
            return 0
        # Filter to PTAVs whose attribute_line is defined on the new
        # template. Two different templates can share attributes (e.g.
        # door_style is on both base and wall cabinets); we only carry
        # over the overlap. For each inherited attribute, pick the PTAV
        # on the NEW template that has the same attribute_id + value_id.
        new_template_attr_lines = new_tmpl.attribute_line_ids
        inherited = request.env["product.template.attribute.value"].sudo()
        for prior_ptav in prior_ptavs:
            attr_id = prior_ptav.attribute_id.id
            value_id = prior_ptav.product_attribute_value_id.id
            target_line = new_template_attr_lines.filtered(
                lambda al: al.attribute_id.id == attr_id
            )
            if not target_line:
                continue
            target_ptav = target_line.product_template_value_ids.filtered(
                lambda v: v.product_attribute_value_id.id == value_id
            )
            if target_ptav:
                inherited |= target_ptav[:1]
        if not inherited:
            return 0
        # Resolve / create the variant for this combination — same
        # pattern as southbrook_api_line_set_attribute uses.
        variant = new_tmpl._get_variant_for_combination(inherited)
        if not variant:
            variant = (
                request.env["product.product"]
                .sudo()
                .create({
                    "product_tmpl_id": new_tmpl.id,
                    "product_template_attribute_value_ids": [
                        (6, 0, inherited.ids)
                    ],
                })
            )
        if not variant:
            return 0
        new_line.sudo().write({"product_id": variant.id})
        # v19 onchange fires automatically on the write above.
        return len(inherited)

    # 2026-06-27 — bulk-add endpoint (BE reviewer P2#8). Accepts
    # `items: [{product_tmpl_id, qty, zone?}]` and creates / merges
    # all lines in one round-trip + one ORM transaction. Reuses the
    # same dedupe-merge + zone-resolution logic as the single-add path.
    #
    # Use cases: "starter kit" pre-fill (5 typical wall cabinets +
    # 3 base sized to the room), spreadsheet paste, "duplicate as
    # draft" with overrides. Replaces N×/add-line calls.
    @http.route(
        "/southbrook/api/order/<int:order_id>/lines/bulk-add",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_bulk_add(self, order_id, items=None, **kw):
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}

        if order.state not in ("draft", "sent"):
            return {"error": "order_locked", "state": order.state}

        if not items or not isinstance(items, list):
            return {"error": "missing_items"}

        # Hard cap per call to keep the loop bounded and reduce blast
        # radius of a scripted abuse. 200 cabinets is well above any
        # legitimate kitchen.
        if len(items) > 200:
            return {"error": "too_many_items", "max": 200}

        Template = request.env["product.template"].sudo()
        Sol = request.env["sale.order.line"].sudo()
        Variant = request.env["product.product"].sudo()
        sku_defaults = request.env["product.config.session"]._SKU_DEFAULTS

        created_ids, merged_ids, skipped = [], [], []
        for idx, raw in enumerate(items):
            if not isinstance(raw, dict):
                skipped.append({"index": idx, "error": "bad_item"})
                continue
            tmpl_id = raw.get("product_tmpl_id")
            if not tmpl_id:
                skipped.append({"index": idx, "error": "missing_tmpl"})
                continue
            try:
                qty_int = max(1, int(raw.get("qty") or 1))
            except (TypeError, ValueError):
                qty_int = 1
            if qty_int > 999:
                qty_int = 999

            tmpl = Template.browse(int(tmpl_id)).exists()
            if not tmpl:
                skipped.append({"index": idx, "error": "tmpl_not_found"})
                continue

            variant = tmpl.product_variant_ids[:1]
            if not variant:
                variant = Variant.create({"product_tmpl_id": tmpl.id})

            # Dedupe-merge — same contract as single-add.
            existing = Sol.search(
                [
                    ("order_id", "=", order.id),
                    ("product_id", "=", variant.id),
                ],
                limit=1,
            )
            if existing:
                existing.product_uom_qty = existing.product_uom_qty + qty_int
                merged_ids.append(existing.id)
                continue

            sku = tmpl.default_code or ""
            sku_row = sku_defaults.get(sku)
            family = sku_row[0] if sku_row else ""
            resolved_zone = self._southbrook_resolve_zone(
                family, raw.get("zone"),
            )
            line = Sol.create({
                "order_id": order.id,
                "product_id": variant.id,
                "product_uom_qty": qty_int,
                "zone": resolved_zone,
            })
            # v19 onchange fires automatically on ORM write above.
            created_ids.append(line.id)

        return {
            "ok": True,
            "created": created_ids,
            "merged": merged_ids,
            "skipped": skipped,
            "total_created": len(created_ids),
            "total_merged": len(merged_ids),
        }

    # ──────────────────────────────────────────────────────────────────
    # 2026-06-27 — bulk-action endpoints (L1 from the deferred-L list).
    # Mirror the single-line endpoints but accept a `line_ids` list and
    # apply the change once per line in a single ORM transaction. The
    # frontend bulk-toolbar (checkbox column on OrderLine + sticky
    # toolbar) calls these so kitchen-wide edits ("change all 12 base
    # doors to Slab") are one click instead of 12.
    #
    # All three: per-line access check, draft/sent state gate, skip
    # malformed entries gracefully. Returns per-line outcome so the
    # frontend can show "10 updated, 2 skipped".
    # ──────────────────────────────────────────────────────────────────
    def _southbrook_resolve_bulk_lines(self, order_id, line_ids):
        """Resolve + gate a list of line ids against this order.

        Returns (order, lines) on success or ({error}, None) on failure
        so callers can early-return cleanly.
        """
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}, None
        except AccessError:
            return {"error": "forbidden"}, None
        if order.state not in ("draft", "sent"):
            return ({"error": "order_locked", "state": order.state}, None)
        if not isinstance(line_ids, list) or not line_ids:
            return {"error": "missing_line_ids"}, None
        # Hard cap. A bulk action is meant for kitchen-wide edits, not
        # a scripted run over thousands of rows.
        if len(line_ids) > 200:
            return {"error": "too_many_lines", "max": 200}, None
        Sol = request.env["sale.order.line"].sudo()
        try:
            ids_int = [int(i) for i in line_ids]
        except (TypeError, ValueError):
            return {"error": "bad_line_ids"}, None
        lines = Sol.search([
            ("id", "in", ids_int),
            ("order_id", "=", order.id),  # also gates: must be this order
        ])
        if not lines:
            return {"error": "no_lines_resolved"}, None
        return order, lines

    @http.route(
        "/southbrook/api/order/<int:order_id>/lines/bulk-delete",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_bulk_delete(
        self, order_id, line_ids=None, **kw,
    ):
        result = self._southbrook_resolve_bulk_lines(order_id, line_ids)
        if result[1] is None:
            return result[0]
        order, lines = result
        deleted_ids = lines.ids
        lines.with_user(request.env.user).unlink()
        return {
            "ok": True,
            "deleted": deleted_ids,
            "total_deleted": len(deleted_ids),
        }

    @http.route(
        "/southbrook/api/order/<int:order_id>/lines/bulk-move-zone",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_bulk_move_zone(
        self, order_id, line_ids=None, zone=None, **kw,
    ):
        valid_zones = {
            "base_run", "wall", "tall", "island", "accessory", "other",
        }
        if zone not in valid_zones:
            return {"error": "bad_zone", "valid": list(valid_zones)}
        result = self._southbrook_resolve_bulk_lines(order_id, line_ids)
        if result[1] is None:
            return result[0]
        order, lines = result
        lines.with_user(request.env.user).write({"zone": zone})
        return {
            "ok": True,
            "moved": lines.ids,
            "total_moved": len(lines),
            "zone": zone,
        }

    @http.route(
        "/southbrook/api/order/<int:order_id>/lines/bulk-set-attribute",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_bulk_set_attribute(
        self, order_id, line_ids=None, attribute_id=None,
        value_id=None, **kw,
    ):
        if not attribute_id or not value_id:
            return {"error": "missing_attribute_or_value"}
        result = self._southbrook_resolve_bulk_lines(order_id, line_ids)
        if result[1] is None:
            return result[0]
        order, lines = result

        try:
            attr_id_int = int(attribute_id)
            val_id_int = int(value_id)
        except (TypeError, ValueError):
            return {"error": "bad_ids"}

        updated, skipped = [], []
        Ptav = request.env["product.template.attribute.value"].sudo()
        Variant = request.env["product.product"].sudo()
        for line in lines:
            if not line.product_id:
                skipped.append({"line_id": line.id, "reason": "no_variant"})
                continue
            tmpl = line.product_id.product_tmpl_id
            if not tmpl:
                skipped.append({"line_id": line.id, "reason": "no_template"})
                continue
            # Find the PTAV for this template + this attribute_value pair.
            target_ptav = Ptav.search([
                ("product_tmpl_id", "=", tmpl.id),
                ("attribute_id", "=", attr_id_int),
                ("product_attribute_value_id", "=", val_id_int),
            ], limit=1)
            if not target_ptav:
                # This template doesn't expose this value — silent skip.
                # Common when bulk-editing a mixed-template selection.
                skipped.append({
                    "line_id": line.id,
                    "reason": "attribute_not_on_template",
                })
                continue
            # Drop any existing PTAV for the same attribute, keep all
            # others. Same combination math as the single-line
            # set-attribute endpoint above.
            current_ptav = line.product_id.product_template_attribute_value_ids
            same_attr = current_ptav.filtered(
                lambda v: v.attribute_id.id == attr_id_int
            )
            new_combination = (current_ptav - same_attr) | target_ptav
            variant = tmpl._get_variant_for_combination(new_combination)
            if not variant:
                variant = Variant.create({
                    "product_tmpl_id": tmpl.id,
                    "product_template_attribute_value_ids": [
                        (6, 0, new_combination.ids)
                    ],
                })
            if not variant:
                skipped.append({
                    "line_id": line.id,
                    "reason": "variant_resolve_failed",
                })
                continue
            line.sudo().write({"product_id": variant.id})
            # v19 onchange fires automatically on ORM write above.
            updated.append(line.id)

        return {
            "ok": True,
            "updated": updated,
            "skipped": skipped,
            "total_updated": len(updated),
            "total_skipped": len(skipped),
        }

    # T2C12 — FooterActions dispatcher.
    #
    # Single endpoint that the OWL FooterActions component calls with
    # action_code in {confirm, duplicate, print}. Returns {ok, ...}
    # or {error, ...}. The dispatch keeps the API surface small and
    # avoids one route per button.
    @http.route(
        "/southbrook/api/order/<int:order_id>/action",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_action(self, order_id, action_code=None, **kw):
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}

        order_su = order.with_user(request.env.user)

        if action_code == "confirm":
            if order.state not in ("draft", "sent"):
                return {
                    "error": "wrong_state",
                    "message": (
                        "Order must be in draft/sent state — current "
                        + str(order.state)
                    ),
                }
            order_su.action_confirm()
            return {"ok": True, "new_state": order.state}

        if action_code == "send_to_manufacturing":
            # Phase 4 Sprint 2 — explicit Send-to-Manufacturing
            # button. Distinct from action_confirm so dealers can
            # review the confirmed order before kicking off MOs.
            # State-guarded: only confirmed (sale) orders can fire.
            #
            # Security (2026-07-11 audit HIGH-1): releasing an order to the shop
            # floor is a STAFF action. _southbrook_resolve_order grants a portal
            # customer / dealer / parent partner (share=True) access to the
            # order for review — but they must NOT be able to create MOs under
            # sudo. Reject share callers, and honour the production-approval
            # gate when the southbrook_mrp_pm layer (which owns
            # production_approval_state) is installed — guard on field presence
            # since this Tier-3 module can't hard-depend on Tier-5 mrp_pm.
            if request.env.user.share:
                return {
                    "error": "forbidden",
                    "message": "Only staff may send an order to manufacturing.",
                }
            if ("production_approval_state" in order._fields
                    and order.production_approval_state != "approved"):
                return {
                    "error": "not_approved",
                    "message": (
                        "Order has not passed production approval (state: %s)."
                        % order.production_approval_state
                    ),
                }
            if order.state != "sale":
                return {
                    "error": "wrong_state",
                    "message": (
                        "Order must be confirmed (sale state) before "
                        "sending to manufacturing — current "
                        + str(order.state)
                    ),
                }
            mo_ids = []
            already_existed = []
            new_created = []
            for line in order.order_line:
                if not line.product_id:
                    continue
                # Standard Odoo sale-stock-mrp routing already creates
                # MOs at confirm-time when a product has a BoM. Find
                # existing MOs tied to this line; create one if absent
                # and the product has a BoM.
                existing = request.env["mrp.production"].sudo().search([
                    ("origin", "=", order.name),
                    ("product_id", "=", line.product_id.id),
                ], limit=1)
                if existing:
                    mo_ids.append(existing.id)
                    already_existed.append(existing.id)
                    continue
                bom = request.env["mrp.bom"].sudo()._bom_find(
                    products=line.product_id,
                )[line.product_id]
                if not bom:
                    # No BoM yet — skip silently. The dealer can wire
                    # one in the backend if they want this cabinet on
                    # the shop floor.
                    continue
                # Odoo 19 renamed sale.order.line.product_uom to
                # product_uom_id (matching mrp.production's field
                # name). Use it directly.
                mo = request.env["mrp.production"].sudo().create({
                    "product_id": line.product_id.id,
                    "product_qty": line.product_uom_qty or 1.0,
                    "product_uom_id": line.product_uom_id.id,
                    "bom_id": bom.id,
                    "origin": order.name,
                })
                mo_ids.append(mo.id)
                new_created.append(mo.id)
            try:
                order.sudo().message_post(
                    body=(
                        "<strong>Sent to manufacturing</strong>"
                        " — {} MOs ({} new, {} existing)."
                        .format(len(mo_ids), len(new_created),
                                len(already_existed))
                    ),
                    subject="Sent to manufacturing",
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            except Exception:  # noqa: BLE001
                pass
            return {
                "ok": True,
                "mo_count": len(mo_ids),
                "mo_ids": mo_ids,
                "new_count": len(new_created),
                "existing_count": len(already_existed),
            }

        if action_code == "request_price":
            # G14 + G16 + G17 (2026-06-01): real customer-side submit
            # path. Previously this just called action_confirm() —
            # which jumped the order straight to 'sale', skipping the
            # natural review-and-quote loop. Now it:
            #   1. Flips draft → sent (Submitted for Review). The
            #      sales-rep still has to action_confirm later, after
            #      reviewing the customer's spec + pricing.
            #   2. Stamps southbrook_submitted_date for the timeline.
            #   3. Sends the standard 'Sales: Send Quotation' mail
            #      template to the customer (auto-acknowledgement of
            #      receipt).
            #   4. Posts a chatter message to the order so the
            #      assigned salesperson (and anyone subscribed) sees
            #      the submission in their inbox.
            if order.state not in ("draft", "sent"):
                return {
                    "error": "wrong_state",
                    "message": "Order already past pricing review.",
                }
            # Idempotent: if the customer hits Request a Price twice,
            # don't move state backwards and don't overwrite the
            # original submitted_date.
            already_submitted = order.state == "sent"
            if not already_submitted:
                order_su.sudo().write({
                    "state": "sent",
                    "southbrook_submitted_date": fields.Datetime.now(),
                })

            # Mail: send the standard Odoo 'Sales: Send Quotation'
            # template to the customer. Wrap in a try so a missing
            # template (custom DB) doesn't fail the action.
            try:
                template = request.env.ref(
                    "sale.email_template_edi_sale", raise_if_not_found=False,
                )
                if template:
                    template.sudo().send_mail(
                        order.id,
                        force_send=False,  # queue mail for asynchronous send
                        email_layout_xmlid="mail.mail_notification_layout",
                    )
            except Exception:  # noqa: BLE001
                # Phase-3 polish: surface mail failures back to the
                # caller as a soft warning rather than swallowing.
                # For now we keep the action successful and log only.
                import logging
                logging.getLogger(__name__).warning(
                    "Mail send failed on submit for order %s", order.id,
                    exc_info=True,
                )

            # Chatter post — visible to the salesperson + all order
            # followers. Distinct from the customer acknowledgement
            # email above.
            try:
                order.sudo().message_post(
                    body=(
                        "<strong>Submitted for pricing review</strong>"
                        " by <em>{}</em> via the customer portal."
                        .format(request.env.user.name or "customer")
                    ),
                    subject="Quote submitted for review",
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            except Exception:  # noqa: BLE001
                pass  # never fail the action on chatter error

            return {
                "ok": True,
                "new_state": order.state,
                "submitted_for_pricing": True,
                "already_submitted": already_submitted,
            }

        if action_code == "duplicate":
            # action_duplicate_as_draft is the NF6 method on
            # southbrook_estimating.sale_order. Returns an
            # ir.actions.act_window dict pointing at the new draft.
            if not hasattr(order_su, "action_duplicate_as_draft"):
                return {"error": "feature_missing"}
            try:
                action = order_su.action_duplicate_as_draft()
            except Exception as exc:                            # noqa: BLE001
                return {"error": "dup_failed", "message": str(exc)}
            new_id = action.get("res_id") if isinstance(action, dict) else None
            if not new_id:
                return {"error": "dup_failed",
                        "message": "No new_id returned"}
            return {
                "ok": True,
                "new_order_id": new_id,
                "redirect_url": f"/my/southbrook/order-builder/{new_id}",
            }

        if action_code == "cancel":
            # Phase 3 Sprint C4 — customer cancels an unconfirmed order.
            # State-guarded: only draft/sent are cancelable from the
            # portal. Once the salesperson has confirmed, cancellation
            # has to go through the backend (rebates, MO cleanup, etc.).
            if order.state not in ("draft", "sent"):
                return {
                    "error": "wrong_state",
                    "message": (
                        "Cancellation from the portal is only allowed "
                        "before pricing review confirms the order — "
                        "current state: " + str(order.state)
                    ),
                }
            try:
                # action_cancel() is Odoo sale.order's built-in. It
                # cleans up any reserved inventory + invoice draft.
                # We sudo so portal users (who lack write on sale.order
                # directly) can fire it; the record-rule already
                # confirmed via _southbrook_resolve_order that THIS
                # order belongs to THIS user.
                order_su.sudo().action_cancel()
            except Exception as exc:  # noqa: BLE001
                return {
                    "error": "cancel_failed",
                    "message": str(exc),
                }
            try:
                order.sudo().message_post(
                    body=(
                        "<strong>Cancelled by customer</strong>"
                        " via the portal — <em>{}</em>."
                        .format(request.env.user.name or "customer")
                    ),
                    subject="Order cancelled by customer",
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True, "new_state": order.state}

        if action_code == "print":
            # Signature Spec Sheet QWeb PDF — the customer-print
            # report from southbrook_estimating Track 1. We hand the
            # URL back; the OWL component opens it in a new tab so
            # the SPA stays mounted.
            report_xml_id = (
                "southbrook_estimating.action_report_signature_spec_sheet"
            )
            return {
                "ok": True,
                "redirect_url": f"/report/pdf/{report_xml_id}/{order_id}",
            }

        if action_code == "print_door_order":
            # Phase 4 Sprint 7 — Door Order QWeb PDF surfacing.
            # Same pattern as print: return URL, OWL opens in new tab.
            # Dealer-facing report; the door supplier consumes the PDF
            # to fill the actual door order.
            return {
                "ok": True,
                "redirect_url": (
                    f"/report/pdf/"
                    f"southbrook_estimating.action_report_door_order/"
                    f"{order_id}"
                ),
            }

        if action_code == "print_shop_copy":
            # Phase 4 Sprint 7 — Shop Copy QWeb PDF.
            # Shop Copy binds to mrp.production. We find the
            # earliest MO tied to this order (by origin = order.name)
            # and print that one. Returns wrong_state if no MO
            # exists yet so the OWL component can surface a clear
            # message instead of a 404.
            mo = request.env["mrp.production"].sudo().search(
                [("origin", "=", order.name)], limit=1, order="id",
            )
            if not mo:
                return {
                    "error": "no_mo",
                    "message": (
                        "No manufacturing order exists for this sale "
                        "order yet. Use 'Send to Manufacturing' first."
                    ),
                }
            return {
                "ok": True,
                "redirect_url": (
                    f"/report/pdf/"
                    f"southbrook_estimating.action_report_shop_copy/"
                    f"{mo.id}"
                ),
            }

        return {"error": "unknown_action", "message": str(action_code)}

    # Phase 2.5 commit 1 — portal kitchen-3d payload.
    #
    # Mirrors the backend `sale.order.get_kitchen_3d_payload` (Track 1
    # T1C6) for portal-auth consumers. Returns the same shape so the
    # portal-side KitchenViewport can reuse Track 1's rendering logic
    # without payload translation.
    @http.route(
        "/southbrook/api/order/<int:order_id>/kitchen-3d",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_kitchen_3d(self, order_id, **kw):
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        # The method itself is on the southbrook_estimating sale.order
        # extension (Track 1 T1C6). Same env, same source of truth.
        return order.with_user(request.env.user).get_kitchen_3d_payload()

    # ------------------------------------------------------------------
    # 2026-07-03 T1 — "3D Design" tab (persisted drag editor).
    #
    # get-or-create the order's southbrook.kitchen.design and seed its
    # cabinet lines from the order's SB-SKU lines, then return the
    # KitchenCanvas payload (room + flat items).
    #
    # Ownership is enforced by _southbrook_resolve_order (the portal user
    # must own the order). Portal customers hold NO ACL on
    # southbrook.kitchen.design, so the design is read/written under
    # sudo() AFTER that ownership check — the standard portal pattern.
    # This route only ever touches southbrook.kitchen.design(.line); it
    # never writes sale.order / MO. Positions ride the pre-existing,
    # unmodified design→order reconcile cron.
    # ------------------------------------------------------------------
    _FAM_TO_CABTYPE = {
        "base": "base", "sink": "base", "drawer": "base", "vanity": "base",
        "wall": "wall", "tall": "tall", "corner": "corner",
        "accessory": "filler", "worktop": "panel",
    }

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_3d(self, order_id, **kw):
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        design = self._southbrook_get_or_create_design(order)
        return self._southbrook_design_payload(design)

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d/auto-arrange",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_auto_arrange(self, order_id, **kw):
        """P1 button endpoint — auto-distribute the design's cabinets across
        walls into an L/U layout and substitute a real corner cabinet at
        each inside corner. Operates ONLY on the layout domain (design
        lines); the reconciliation mirror then propagates the corner PRODUCT
        to the manufacturing sale.order.line so BOM/price/cutlist follow.
        Strict domain separation preserved."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        design = self._southbrook_get_or_create_design(order)
        # Thin controller: validate (above) → call the service → return JSON.
        # All geometry/substitution/manufacturing/persistence lives in the
        # design.action_auto_arrange() service, which is atomic on its own.
        try:
            arrange = design.sudo().action_auto_arrange()
        except kitchen_layout_engine.LayoutCapacityExceeded as ex:
            _logger.info("[auto-arrange] ROOM_TOO_SMALL order=%s cap=%.0f "
                         "req=%.0f", order.id, ex.capacity_mm, ex.requested_mm)
            return {"error": "ROOM_TOO_SMALL",
                    "capacity_mm": ex.capacity_mm,
                    "requested_mm": ex.requested_mm,
                    "detail": ex.detail}
        return {"ok": True, "arrange": arrange,
                "payload": self._southbrook_design_payload(design)}

    # 2026-07-06 — portal-scoped "new design" defaults. The shared
    # southbrook.kitchen.design model's own field defaults (12in W ×
    # 24in D — a placeholder, not a usable room) stay untouched so the
    # standalone Kitchen 3D Configurator app's own new-design flow
    # (which already has its own walk-in/explicit-customer defaults,
    # see kitchen_design.py) is unaffected. This portal route passes
    # its own sane starting room explicitly instead.
    _PORTAL_NEW_DESIGN_ROOM = {
        "room_width_in": 96.0, "room_depth_in": 72.0, "room_height_in": 96.0,
    }

    def _southbrook_get_or_create_design(self, order):
        """Find (or create + seed) the southbrook.kitchen.design linked to
        this order. Runs under sudo() because portal customers hold no ACL
        on the model; safe because the caller already verified the user
        owns `order`."""
        Design = request.env["southbrook.kitchen.design"].sudo()
        design = Design.search([("sale_order_id", "=", order.id)], limit=1)
        if not design:
            design = Design.create({
                "name": order.name or "Kitchen Design",
                "partner_id": order.partner_id.id if order.partner_id else False,
                "sale_order_id": order.id,
                "state": "draft",
                **self._PORTAL_NEW_DESIGN_ROOM,
            })
        # Seed only when no configurator-origin lines exist yet, so a
        # user's persisted drag edits are preserved on re-open.
        if not design.cabinet_line_ids.filtered(lambda l: l.origin == "configurator"):
            self._southbrook_seed_design_lines(order, design)
        return design

    def _southbrook_seed_design_lines(self, order, design):
        """Materialise design lines from the order's SB-SKU lines.

        Placement is delegated to the ONE pure layout engine
        (kitchen_layout_engine) — no inline cursor math here. Seeding puts
        every cabinet on the BACK wall in line order; the user re-assigns
        walls later. The golden back-wall parity test guarantees this
        produces the same coordinates as the historical cursor pack (and
        as the read-only Preview), so existing designs are unchanged. mm →
        inches, snapped to the nearest 1/4in via `_snap_quarter_in`
        (SBK-025) so the persisted Float-inch fields don't carry the
        609/25.4 = 23.9763779... style drift into the UI."""
        SaleOrder = request.env["sale.order"]
        Session = request.env["product.config.session"]
        Line = request.env["southbrook.kitchen.design.line"].sudo()
        sku_defaults = Session._SKU_DEFAULTS
        mm_to_in = 1.0 / 25.4

        # Pass 1 — build the engine's semantic cabinet list (skip logic
        # identical to the Preview's).
        cabs, metas = [], []
        seq = 0
        for oline in order.order_line:
            tmpl = oline.product_id.product_tmpl_id if oline.product_id else None
            sku = tmpl.default_code if tmpl else None
            row = sku_defaults.get(sku) if sku else None
            if not row:
                continue   # non-SB product → skip (matches the Preview)
            fam, _doors, _drawers, w, h, d = row
            seq += 1
            cabinet_type = (
                tmpl.southbrook_cabinet_type
                or self._FAM_TO_CABTYPE.get(fam, "base")
            )
            cabs.append({
                "id": oline.id,
                "width_mm": w, "height_mm": h, "depth_mm": d,
                "family": fam,
                "zone": oline.zone or "base_run",
                "wall": "back",
                "run_seq": seq,
            })
            metas.append((oline, tmpl, fam, w, h, d, seq, cabinet_type))

        room = {
            "width_mm":  (design.room_width_in or 0.0) * 25.4,
            "depth_mm":  (design.room_depth_in or 0.0) * 25.4,
            "height_mm": (design.room_height_in or 0.0) * 25.4,
        }
        placements = kitchen_layout_engine.layout(
            cabs, room,
            # SBK-026 (Task 14 review, finding 1) — was the raw
            # sale.order._ZONE_LAYOUT class constant, so a freshly
            # seeded design's wall cabinets landed at 1400mm/55.12in
            # regardless of `design`'s own wall_cab_top_alignment /
            # soffit_height_in / counter_thickness_in — then jumped to
            # a DIFFERENT elevation (e.g. 54.00in) the moment the
            # design's own action_auto_arrange next ran, since that
            # path already consults design._wall_cab_zone_layout().
            # `design` already exists at this point (created/looked up
            # by the caller just above) and carries those settings —
            # route through the SAME helper so seed and arrange can
            # never disagree. See kitchen_design.py's
            # _wall_cab_zone_layout docstring for what it does and does
            # NOT override.
            zone_layout=design._wall_cab_zone_layout(),
            worktop_cursor=SaleOrder._WORKTOP_CURSOR,
            worktop_y=SaleOrder._WORKTOP_Y_FLOOR,
        )

        # Pass 2 — persist a design line per cabinet from the engine's
        # derived placement.
        for (oline, tmpl, fam, w, h, d, cab_seq, cabinet_type), place in zip(
                metas, placements):
            Line.create({
                "design_id":     design.id,
                "sequence":      cab_seq * 10,
                "product_id":    oline.product_id.id,
                "quantity":      1,
                "price_unit":    oline.price_unit or (tmpl.list_price if tmpl else 0.0),
                "cabinet_type":  cabinet_type,
                "width_in":      _snap_quarter_in(w * mm_to_in),
                "height_in":     _snap_quarter_in(h * mm_to_in),
                "depth_in":      _snap_quarter_in(d * mm_to_in),
                "x_position_in": _snap_quarter_in(place["x"] * mm_to_in),
                "y_position_in": _snap_quarter_in(place["y"] * mm_to_in),
                "z_position_in": _snap_quarter_in(place["z"] * mm_to_in),
                "pinned":        False,
                "rotation_deg":  place["rotation_deg"],
                "wall":          "back",
                "run_seq":       cab_seq,
                "layout_key":    "L%d-%s" % (oline.id, fam),
                "origin":        "configurator",
            })

    # 2026-07-06 (enrichment pass) — same compact shape as the standalone
    # Kitchen 3D Configurator's _channel_meta, but the portal already
    # knows its own order's partner + pricelist directly — no partner_id
    # param / _resolve_pricelist lookup needed.
    _CHANNEL_LABELS = {
        "retail": "Retail", "dealer": "Dealer -50%",
        "tradesperson": "Contractor", "kd": "KD",
        "bigbox": "Big-Box", "refacing": "Refacing",
    }

    def _southbrook_design_channel(self, order):
        partner = order.partner_id
        pricelist = order.pricelist_id
        channel = (partner and getattr(partner, "channel", False)) or "retail"
        suffix = ""
        if channel == "tradesperson" and partner:
            tier = getattr(partner, "tradesperson_tier", False)
            if tier:
                suffix = " T%s" % tier
        return {
            "channel":        channel,
            "channel_label":  self._CHANNEL_LABELS.get(channel, channel.title()) + suffix,
            "pricelist_name": (pricelist and pricelist.display_name) or "",
        }

    def _southbrook_design_payload(self, design):
        """KitchenCanvas payload: room dims + one flat item per
        configurator-origin design line (mirrors the configurator's
        /load_design_lines shape)."""
        design = design.sudo()
        items = []
        for line in design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
        ):
            prod = line.product_id
            tmpl = prod.product_tmpl_id
            items.append({
                "id":            prod.id,
                "product_id":    prod.id,
                "product_name":  prod.display_name,
                "sku":           tmpl.default_code or "",
                "material":      tmpl.southbrook_material or "",
                # 2026-07-06 — BOM-readiness parity with the backend's
                # detail-drawer warning ("No BOM defined... can't be
                # manufactured until a Bill of Materials exists"). No
                # "Open BoM" deep-link ported — that view is backend-
                # only; portal shows the fact, not the internal action.
                "bom_available": bool(tmpl.southbrook_bom_available),
                "layout_key":    line.layout_key,
                "cabinet_type":  line.cabinet_type,
                "width_in":      line.width_in,
                "height_in":     line.height_in,
                "depth_in":      line.depth_in,
                "x_position_in": line.x_position_in,
                "y_position_in": line.y_position_in,
                "z_position_in": line.z_position_in,
                "rotation_deg":  line.rotation_deg,
                "pinned":        line.pinned,
                "price":         line.price_unit,
                "quantity":      line.quantity,
                # Touch v1 seam fix (2026-08-02) — the portal is the
                # PRIMARY phone surface, but its items carried no
                # wall/zone, degrading the pad's nudge, camera-to-
                # elevation, and family scoping there. Additive keys.
                "wall":          line.wall or "back",
                "zone":          line.zone or "base_run",
            })
        return {
            "design_id": design.id,
            "room": {
                "width_in":  design.room_width_in,
                "depth_in":  design.room_depth_in,
                "height_in": design.room_height_in,
            },
            "channel": self._southbrook_design_channel(design.sale_order_id),
            "items": items,
            # Touch editing v1 (2026-08-02 spec §4 item 2) — portal kill-
            # switch, per T2's handoff note (Wave 1): the one line the
            # backend controller couldn't add on this addon's behalf.
            "touch_enabled": design._sb_touch_enabled("portal"),
        }

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d/move",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_3d_move(self, order_id, layout_key,
                                      x_position_in=None, y_position_in=None,
                                      z_position_in=None, rotation_deg=None,
                                      width_in=None, **kw):
        """Persist a drag-move (or a width-edit — same "write one field
        on my own design line" shape, added 2026-07-06 for the detail
        panel's Width input) to the order's design line. Portal-safe:
        order ownership is enforced via _southbrook_resolve_order, then the
        write runs under sudo() (portal customers hold no ACL on
        southbrook.kitchen.design). Writes ONLY kitchen.design.line — the
        design→order position mirror is the existing reconcile cron's job."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        if not layout_key:
            return {"error": "no_layout_key"}
        Design = request.env["southbrook.kitchen.design"].sudo()
        design = Design.search([("sale_order_id", "=", order.id)], limit=1)
        if not design:
            return {"error": "no_design"}
        line = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator" and l.layout_key == layout_key
        )[:1]
        if not line:
            return {"error": "no_matching_line"}
        vals = {}
        if x_position_in is not None:
            vals["x_position_in"] = self._sb_design_coord(x_position_in)
        if y_position_in is not None:
            vals["y_position_in"] = self._sb_design_coord(y_position_in)
        if z_position_in is not None:
            vals["z_position_in"] = self._sb_design_coord(z_position_in)
        if rotation_deg is not None:
            vals["rotation_deg"] = self._sb_design_coord(rotation_deg) % 360.0
        if width_in is not None:
            # Same 6-48in envelope the backend's own Width input enforces.
            w = self._sb_design_coord(width_in)
            if w is not None:
                vals["width_in"] = max(6.0, min(48.0, w))
        vals = {k: v for k, v in vals.items() if v is not None}
        if vals:
            line.write(vals)
        return {"ok": True, "id": line.id, "width_in": line.width_in}

    # 2026-07-06 audit A4 — coordinate sanitizer shared by the design-3d
    # write routes. float("1e400") parses to inf (and "nan" to NaN)
    # WITHOUT raising, and Python's json.dumps then emits the
    # non-standard Infinity/NaN tokens, which break strict JSON.parse()
    # for every subsequent loader of the design payload (portal AND the
    # backend Kitchen 3D Configurator reading the same rows). Reject
    # non-finite, clamp to the physically meaningful envelope.
    _DESIGN_COORD_MAX = 2400.0   # 200 ft — far beyond any real kitchen

    def _sb_design_coord(self, raw):
        """Coerce a client-supplied coordinate to a finite, clamped
        float; None when unusable."""
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(val):
            return None
        return max(-self._DESIGN_COORD_MAX, min(self._DESIGN_COORD_MAX, val))

    # ------------------------------------------------------------------
    # 2026-07-06 — "3D Design" tab parity pass. Three additive routes,
    # all following the exact ownership + sudo() pattern already
    # established by southbrook_api_design_3d_move above: portal
    # customers hold no ACL on southbrook.kitchen.design(.line), so
    # every write happens under sudo() AFTER _southbrook_resolve_order
    # confirms the caller owns the order. None of these touch
    # sale.order / MO — the design→order reconcile cron remains the
    # only bridge, unchanged.
    # ------------------------------------------------------------------

    # Sane real-world clamps. The standalone Kitchen 3D Configurator's
    # own topbar inputs allow min="12" (inches) on width/depth, which
    # is a legacy artifact (nobody designs a 1ft-wide kitchen) — this
    # portal-facing route enforces the geometry southbrook.room.
    # validate_geometry already treats as a sane lower bound elsewhere
    # in this addon, rather than copying that oddity.
    _DESIGN_ROOM_MIN = {"width_in": 72.0, "depth_in": 72.0, "height_in": 84.0}
    _DESIGN_ROOM_MAX = {"width_in": 600.0, "depth_in": 600.0, "height_in": 144.0}

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d/room",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_3d_room(self, order_id, width_in=None,
                                       depth_in=None, height_in=None, **kw):
        """Persist room Width/Depth/Height edits from the portal's 3D
        Design tab. Clamped server-side regardless of what the client
        sent — the client applies the same clamps for immediate
        feedback, but the server is the source of truth."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        Design = request.env["southbrook.kitchen.design"].sudo()
        design = Design.search([("sale_order_id", "=", order.id)], limit=1)
        if not design:
            return {"error": "no_design"}
        vals = {}
        for field, raw in (("width_in", width_in), ("depth_in", depth_in),
                            ("height_in", height_in)):
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            lo, hi = self._DESIGN_ROOM_MIN[field], self._DESIGN_ROOM_MAX[field]
            vals["room_" + field] = max(lo, min(hi, val))
        if vals:
            design.write(vals)
        return {
            "ok": True,
            "room": {
                "width_in":  design.room_width_in,
                "depth_in":  design.room_depth_in,
                "height_in": design.room_height_in,
            },
        }

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d/catalog",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_3d_catalog(self, order_id, **kw):
        """Portal-safe cabinet catalog for the 3D Design tab's inventory
        panel — every southbrook_is_cabinet product, priced through the
        order's own pricelist (never raw list_price) so a dropped
        cabinet's price matches what the quote will actually charge.
        Read-only; no sale.order / design write."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        Template = request.env["product.template"].sudo()
        templates = Template.search([
            ("southbrook_is_cabinet", "=", True),
            ("sale_ok", "=", True),
        ], order="default_code, name")
        pricelist = order.pricelist_id
        partner_id = order.partner_id.id if order.partner_id else None
        products = []
        for tmpl in templates:
            variant = tmpl.product_variant_id
            # 2026-07-06 audit A13 — product_variant_id is only set when
            # the template resolves to exactly ONE live variant. Emitting
            # tmpl.id in its place would put a product.template id in a
            # field /design-3d/add browses as product.product (independent
            # id sequences → ID-space confusion). Multi-variant cabinet
            # templates (dynamic-variant configurator SKUs) are added via
            # the configurator flow, not this catalog — skip them.
            if not variant:
                continue
            price = tmpl.list_price
            if pricelist:
                try:
                    price = pricelist.with_context(
                        partner_id=partner_id,
                    )._get_product_price(variant, 1.0)
                except Exception:                       # noqa: BLE001
                    _logger.warning(
                        "design-3d/catalog: pricelist %s could not price "
                        "%s, falling back to list_price",
                        pricelist.id, tmpl.default_code, exc_info=True,
                    )
            products.append({
                "product_id":   variant.id,
                "sku":          tmpl.default_code or "",
                "name":         tmpl.display_name,
                "cabinet_type": tmpl.southbrook_cabinet_type or "base",
                "material":     tmpl.southbrook_material or "",
                "width_in":     tmpl.southbrook_width_in,
                "height_in":    tmpl.southbrook_height_in,
                "depth_in":     tmpl.southbrook_depth_in,
                "price":        price,
            })
        return {"products": products}

    # 2026-07-06 audit A10, generalized 2026-07-06 (enrichment pass) —
    # in-process sliding-window rate limiter shared by every route that
    # mutates the design's cabinet SET (add/remove/swap — not /move,
    # which fires on every drag-commit and doesn't create/destroy
    # records), mirroring the established pattern in
    # southbrook_room_capture/controllers/main.py (_RATE_BUCKETS /
    # _rate_limit_check; per-worker, keyed on user id — same documented
    # per-worker caveat). Generous default: a busy design session adds/
    # swaps/removes cabinets in bursts, but nothing legitimate reaches
    # hundreds of these per hour.
    _DESIGN_ADD_WINDOW_SEC = 3600
    _DESIGN_ADD_LIMIT_DEFAULT = 240
    _DESIGN_ADD_BUCKETS = {}   # uid -> (window_head_ts, count)

    def _sb_design_mutate_rate_ok(self):
        uid = request.env.user.id
        if not uid:
            return True
        try:
            limit = int(request.env["ir.config_parameter"].sudo().get_param(
                "southbrook_estimating_website.design_add_rate_limit",
                str(self._DESIGN_ADD_LIMIT_DEFAULT)))
        except Exception:                           # noqa: BLE001
            limit = self._DESIGN_ADD_LIMIT_DEFAULT
        limit = max(1, min(limit, 10_000))
        now = int(time.time())
        buckets = self._DESIGN_ADD_BUCKETS
        if len(buckets) >= 4096:
            # Bounded scratch data; crude trim is fine per-worker.
            buckets.clear()
        head, cnt = buckets.get(uid, (now, 0))
        if now - head > self._DESIGN_ADD_WINDOW_SEC:
            buckets[uid] = (now, 1)
            return True
        if cnt + 1 > limit:
            return False
        buckets[uid] = (head, cnt + 1)
        return True

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d/add",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_3d_add(self, order_id, product_id,
                                      x_position_in=None, wall=None, **kw):
        """Add a cabinet from the inventory panel into the scene. Only
        southbrook_is_cabinet products can be dropped — this is a plain
        catalog pick (a fixed, already-priced SKU), not the attribute-
        configurator combination flow, so the hard config-rule guard
        added to sale_order.action_confirm doesn't apply here; there is
        no free attribute combination to validate."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        if not self._sb_design_mutate_rate_ok():
            return {"error": "rate_limited"}
        # 2026-07-06 audit A3 — product_id arrives as raw JSON; guard the
        # int() coercion like every other client input in these routes.
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return {"error": "not_a_cabinet"}
        Product = request.env["product.product"].sudo()
        product = Product.browse(product_id).exists()
        # 2026-07-06 audit A12 — browse() bypasses active-record
        # filtering, so also require what the /catalog domain requires
        # (active + sale_ok); an archived/discontinued cabinet must not
        # be addable by remembered id. Same error token as the
        # nonexistent-id case — no new existence oracle.
        if (not product or not product.active or not product.sale_ok
                or not product.product_tmpl_id.southbrook_is_cabinet):
            return {"error": "not_a_cabinet"}
        Design = request.env["southbrook.kitchen.design"].sudo()
        design = Design.search([("sale_order_id", "=", order.id)], limit=1)
        if not design:
            return {"error": "no_design"}
        tmpl = product.product_tmpl_id
        pricelist = order.pricelist_id
        price = tmpl.list_price
        if pricelist:
            try:
                price = pricelist.with_context(
                    partner_id=order.partner_id.id if order.partner_id else None,
                )._get_product_price(product, 1.0)
            except Exception:                       # noqa: BLE001
                pass
        # 2026-07-06 audit A1/A4 — sanitize the drop position (finite +
        # clamped, shared _sb_design_coord). No/unusable position means
        # "append at the end of the current run", computed from the real
        # lines — NOT the backend configurator's 1e6 sort-sentinel: that
        # sentinel only works there because its _recomputeLayoutFromItems
        # re-packs positions afterwards, which this portal route never
        # does, so storing 1e6 raw would park the cabinet a million
        # inches away and balloon the client's auto-expanded room width.
        x_val = self._sb_design_coord(x_position_in)
        if x_val is None:
            run_lines = design.cabinet_line_ids.filtered(
                lambda l: l.origin == "configurator"
            )
            x_val = min(
                max(((l.x_position_in or 0.0) + (l.width_in or 0.0)
                     for l in run_lines), default=0.0),
                self._DESIGN_COORD_MAX,
            )
        cabinet_type = tmpl.southbrook_cabinet_type or "base"
        z_val = 0.0
        if cabinet_type == "wall":
            wall_lines = design.cabinet_line_ids.filtered(
                lambda l: l.origin == "configurator" and l.cabinet_type == "wall"
            )
            z_val = wall_lines[:1].z_position_in if wall_lines else 54.0
        layout_key = "%s-add-%d-%d" % (cabinet_type, product.id, design.id)
        # A prior drop of the same product could already have this
        # layout_key from an earlier request (double-submit); make the
        # key unique per call so re-adding the same SKU never collides.
        existing_keys = design.cabinet_line_ids.mapped("layout_key")
        suffix = 0
        base_key = layout_key
        while layout_key in existing_keys:
            suffix += 1
            layout_key = "%s-%d" % (base_key, suffix)
        # Phase 3 — if the user has a wall selected, the cabinet lands on it.
        # Assign the wall + the next run position; the engine then computes
        # the exact x/y/z + rotation (below).
        if wall not in ("back", "left", "right", "front"):
            wall = None
        line_wall = wall or "back"
        same_wall = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator"
            and (l.wall or "back") == line_wall)
        run_seq = max(same_wall.mapped("run_seq") or [-1]) + 1
        Line = request.env["southbrook.kitchen.design.line"].sudo()
        line = Line.create({
            "design_id":     design.id,
            "product_id":    product.id,
            "quantity":      1,
            "price_unit":    price,
            "cabinet_type":  cabinet_type,
            "wall":          line_wall,
            "run_seq":       run_seq,
            "width_in":      tmpl.southbrook_width_in or 24.0,
            "height_in":     tmpl.southbrook_height_in or (
                30.0 if cabinet_type == "wall" else 34.5),
            "depth_in":      tmpl.southbrook_depth_in or (
                12.0 if cabinet_type == "wall" else 24.0),
            "x_position_in": x_val,
            "y_position_in": 0.0,
            "z_position_in": z_val,
            "pinned":        False,
            "rotation_deg":  0.0,
            "layout_key":    layout_key,
            "origin":        "configurator",
        })
        # Phase 3 — when a wall is selected, place the new cabinet on it via
        # the pure engine (snaps to the next free position, auto-orients).
        # Only the NEW line is repositioned; existing cabinets (incl. D8
        # mount heights) are untouched.
        #
        # 2026-07-26 defect C9 fix — resolution now ALWAYS runs (no gate)
        # once the design's configurator cabinets span >=2 walls, INCLUDING
        # on the very first cabinet placed on a brand-new wall. The
        # 2026-07-12 "Task 3" gate (formerly `wall_had_cabinets`, captured
        # before this line existed) used to skip resolution for that first
        # cabinet, so the bare engine laid it at the start of the wall's
        # run — physically interpenetrating whatever the adjacent wall
        # already had in the shared corner cell, with no corner cabinet
        # substituted until a SECOND cabinet landed on the same wall.
        # That gate existed only to dodge a "vanishing cabinet" UX bug
        # (commit 5f15786): resolving AND still returning the plain `item`
        # shape meant the client rendered nothing, because resolution can
        # archive the very line the response claimed to describe. The
        # correct fix, applied here, is to always return the RE-LAID
        # payload whenever resolution runs — design_tab already replaces
        # the whole scene on `res.relaid` + `res.payload`, so the user
        # simply sees their cabinet become/join the corner arrangement;
        # nothing "vanishes".
        layout_warning = None
        if wall:
            self._sb_place_line_on_wall(design, line)
            # Continuous corners — if the new cabinet completes an inside
            # corner (cabinets now on two different walls), resolve it in the
            # design/visual layer immediately. sync=False keeps it light (no
            # per-drop manufacturing mirror — the 5-min cron / an explicit
            # auto-arrange catches BOM/price up). Return the full re-laid
            # payload so the scene reflects the corner + any re-flow.
            walls_used = {
                (dl.wall or "back")
                for dl in design.cabinet_line_ids.filtered(
                    lambda l: l.origin == "configurator"
                    and l.layout_role != "derived"
                    and l.cabinet_type not in ("filler", "panel"))
            }
            if len(walls_used) >= 2:
                try:
                    design.action_auto_arrange(sync=False)
                except kitchen_layout_engine.LayoutCapacityExceeded as ex:
                    # The new line already exists and is engine-placed on
                    # its wall (above) — resolution merely couldn't find a
                    # geometrically valid corner substitution for the
                    # design as a whole. Don't fail the add; fall through
                    # to the plain item response below with a warning the
                    # client can surface to the user.
                    outside = getattr(ex, "outside", []) or []
                    if outside:
                        overflow_in = max(
                            o.get("overflow_mm", 0.0) for o in outside
                        ) / 25.4
                        layout_warning = (
                            "Cabinet added, but %d cabinet(s) don't fit "
                            "the room and the layout could not be "
                            "auto-arranged (up to %.1f\" over)." % (
                                len(outside), overflow_in)
                        )
                    else:
                        layout_warning = (
                            "Cabinet added, but the layout could not be "
                            "auto-arranged — the design does not fit the "
                            "room."
                        )
                else:
                    return {"ok": True, "relaid": True,
                            "payload": self._southbrook_design_payload(design)}
        response = {"ok": True, "item": {
            "id":            product.id,
            "product_id":    product.id,
            "product_name":  product.display_name,
            "sku":           tmpl.default_code or "",
            "material":      tmpl.southbrook_material or "",
            "bom_available": bool(tmpl.southbrook_bom_available),
            "layout_key":    line.layout_key,
            "cabinet_type":  line.cabinet_type,
            "width_in":      line.width_in,
            "height_in":     line.height_in,
            "depth_in":      line.depth_in,
            "x_position_in": line.x_position_in,
            "y_position_in": line.y_position_in,
            "z_position_in": line.z_position_in,
            "rotation_deg":  line.rotation_deg,
            "pinned":        line.pinned,
            "price":         line.price_unit,
            "quantity":      line.quantity,
        }}
        if layout_warning:
            response["layout_warning"] = layout_warning
        return response

    def _sb_place_line_on_wall(self, design, new_line):
        """Compute the new cabinet's x/y/z + rotation on its assigned wall
        via the pure layout engine, then write ONLY that line. The engine
        lays out the whole wall run (so the new unit snaps to the next free
        position after the cabinets already there), but we persist just the
        new line — existing cabinets keep their positions and any D8 mount
        heights untouched.

        PR4 (2026-07-12) — this is now a thin call to the shared model
        method `southbrook.kitchen.design._place_lines_on_wall`
        (southbrook_kitchen_3d_configurator/models/kitchen_design.py),
        extracted so the backend 3D configurator's `save_design` can
        reach the SAME engine-delegation path instead of persisting
        client-computed (back-wall-only) geometry for wall cabinets.
        Behavior-preserving: identical engine call, identical single-line
        write — see docs/2026-07-12-add-cabinet-sequence.md."""
        design._place_lines_on_wall(new_line)

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d/remove",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_3d_remove(self, order_id, layout_key, **kw):
        """Remove a cabinet from the scene — the missing symmetry to
        /add. Portal-safe: same resolve-order-then-sudo pattern; deletes
        ONLY the southbrook.kitchen.design.line row, never sale.order/MO."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        if not self._sb_design_mutate_rate_ok():
            return {"error": "rate_limited"}
        if not layout_key:
            return {"error": "no_layout_key"}
        Design = request.env["southbrook.kitchen.design"].sudo()
        design = Design.search([("sale_order_id", "=", order.id)], limit=1)
        if not design:
            return {"error": "no_design"}
        line = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator" and l.layout_key == layout_key
        )[:1]
        if not line:
            return {"error": "no_matching_line"}
        line.unlink()
        return {"ok": True}

    @http.route(
        "/southbrook/api/order/<int:order_id>/design-3d/swap",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_design_3d_swap(self, order_id, layout_key, product_id, **kw):
        """Swap the product on an existing design line for a different
        SKU of the same cabinet_type — mirrors the backend detail
        drawer's product-swap select. Keeps position + custom width (a
        user-dialed-in width shouldn't reset just because the SKU
        changed); recomputes price + canonical height/depth from the
        new product, same pricelist-resolution path as /add."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        if not self._sb_design_mutate_rate_ok():
            return {"error": "rate_limited"}
        if not layout_key:
            return {"error": "no_layout_key"}
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return {"error": "not_a_cabinet"}
        Product = request.env["product.product"].sudo()
        product = Product.browse(product_id).exists()
        if (not product or not product.active or not product.sale_ok
                or not product.product_tmpl_id.southbrook_is_cabinet):
            return {"error": "not_a_cabinet"}
        Design = request.env["southbrook.kitchen.design"].sudo()
        design = Design.search([("sale_order_id", "=", order.id)], limit=1)
        if not design:
            return {"error": "no_design"}
        line = design.cabinet_line_ids.filtered(
            lambda l: l.origin == "configurator" and l.layout_key == layout_key
        )[:1]
        if not line:
            return {"error": "no_matching_line"}
        tmpl = product.product_tmpl_id
        # Same cabinet_type only — swapping a base for a wall cabinet
        # mid-run would silently break the run's Z-alignment logic.
        new_type = tmpl.southbrook_cabinet_type or "base"
        if new_type != line.cabinet_type:
            return {"error": "type_mismatch"}
        pricelist = order.pricelist_id
        price = tmpl.list_price
        if pricelist:
            try:
                price = pricelist.with_context(
                    partner_id=order.partner_id.id if order.partner_id else None,
                )._get_product_price(product, 1.0)
            except Exception:                       # noqa: BLE001
                pass
        line.write({
            "product_id":  product.id,
            "price_unit":  price,
            "height_in":   tmpl.southbrook_height_in or line.height_in,
            "depth_in":    tmpl.southbrook_depth_in or line.depth_in,
        })
        return {"ok": True, "item": {
            "id":            product.id,
            "product_id":    product.id,
            "product_name":  product.display_name,
            "sku":           tmpl.default_code or "",
            "material":      tmpl.southbrook_material or "",
            "bom_available": bool(tmpl.southbrook_bom_available),
            "layout_key":    line.layout_key,
            "cabinet_type":  line.cabinet_type,
            "width_in":      line.width_in,
            "height_in":     line.height_in,
            "depth_in":      line.depth_in,
            "x_position_in": line.x_position_in,
            "y_position_in": line.y_position_in,
            "z_position_in": line.z_position_in,
            "rotation_deg":  line.rotation_deg,
            "pinned":        line.pinned,
            "price":         line.price_unit,
            "quantity":      line.quantity,
        }}

    def _southbrook_order_signature(self, order):
        """Cheap server-side change-detection signature for the order
        payload. Includes everything the OWL store would notice as a
        change:

          • order.write_date + state (top-level shape)
          • partner.write_date (channel/tier-driven price recompute)
          • pricelist.write_date (rule edits)
          • line count + id-sum (add/delete bumps even if line write_date
            wouldn't move — e.g. a delete-then-readd of the same template)
          • per-line write_date (qty / attribute / spec edits)

        Returned as a "|"-joined string for human-readable log inspection.
        Stable hashing is left to the caller (the value is short enough
        to compare directly).
        """
        parts = [
            order.write_date.isoformat() if order.write_date else "",
            str(order.state or ""),
            str(len(order.order_line)),
            str(sum(line.id for line in order.order_line)),
        ]
        if order.partner_id and order.partner_id.write_date:
            parts.append(order.partner_id.write_date.isoformat())
        if order.pricelist_id and order.pricelist_id.write_date:
            parts.append(order.pricelist_id.write_date.isoformat())
        for line in order.order_line:
            if line.write_date:
                parts.append(line.write_date.isoformat())
        return "|".join(parts)

    @http.route(
        "/southbrook/api/order/<int:order_id>",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order(self, order_id, client_etag=None, **kw):
        """Return the order shape for the OWL store.

        2026-06-27 — accepts a `client_etag` from the OWL poll loop. When
        the server-computed signature matches, return `{unchanged: True,
        etag: <sig>}` in <5ms instead of rebuilding the ~80-SQL payload.
        Backstops the existing client-side payload_hash (which still
        catches false-positive ETag matches caused by clock skew or
        derived-field drift the signature can't see).
        """
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        sig = self._southbrook_order_signature(order)
        if client_etag and client_etag == sig:
            return {"unchanged": True, "etag": sig}
        payload = self._build_southbrook_order_payload(order)
        payload["etag"] = sig
        return payload

    # 2026-06-27 — preflight-confirm + MO preview.
    #
    # Pre-flight dry-run that returns the same data
    # action_send_to_production would create, WITHOUT actually creating
    # anything. The OWL confirm modal renders the preview so the user
    # never clicks Confirm and gets a wall-of-text UserError popup
    # from the gate (per the backend reviewer's P1 #7).
    @http.route(
        "/southbrook/api/order/<int:order_id>/preflight-confirm",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_preflight_confirm(self, order_id, **kw):
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}

        # MO preview — count lines with a resolvable BoM, grouped by
        # product family for the "8 base · 3 wall · 1 tall MOs" copy.
        mo_preview = []
        unapproved_count = 0
        Bom = request.env["mrp.bom"].sudo()
        resolver = getattr(order, "_resolve_bom_for_line", None)
        if resolver:
            family_counts = {}
            for line in order.order_line:
                if not line.product_id or line.display_type:
                    continue
                bom = order._resolve_bom_for_line(Bom, line)
                if not bom:
                    continue
                # Family lookup mirrors _build_southbrook_order_payload.
                tmpl = line.product_id.product_tmpl_id
                sku = tmpl.default_code if tmpl else ""
                sku_row = request.env[
                    "product.config.session"
                ]._SKU_DEFAULTS.get(sku)
                fam = (sku_row[0] if sku_row else "other").lower()
                fam_bucket = "other"
                for f in ("base", "wall", "tall", "island", "accessory"):
                    if fam.startswith(f):
                        fam_bucket = f
                        break
                family_counts[fam_bucket] = (
                    family_counts.get(fam_bucket, 0) + 1
                )
            mo_preview = [
                {"family": f, "count": family_counts[f]}
                for f in ("base", "wall", "tall", "island", "accessory", "other")
                if family_counts.get(f, 0) > 0
            ]
            unapproved_count = sum(family_counts.values())

        # Blockers — pre-confirm validation summary.
        blockers = []
        approval_state = getattr(order, "production_approval_state", None)
        if order.state == "sale" and approval_state in ("none", "rejected"):
            blockers.append({
                "code": "needs_production_approval",
                "message": (
                    "Production approval is required before Send to "
                    "Manufacturing."
                ),
            })
        if not order.order_line:
            blockers.append({
                "code": "empty_order",
                "message": "Add at least one cabinet to the order.",
            })
        # Surface any hard-severity validation issues from the existing
        # collector — those would also block confirmation downstream.
        for v in self._southbrook_collect_validation(order):
            if v.get("severity") == "hard":
                blockers.append({
                    "code": v.get("code", "validation"),
                    "message": v.get("message", ""),
                })

        return {
            "ok": True,
            "can_confirm": not blockers,
            "mo_preview": mo_preview,
            "mo_total": unapproved_count,
            "blockers": blockers,
            "approval_state": approval_state,
        }

    # 2026-06-27 — request-production-approval. Thin portal wrapper
    # around southbrook_mrp_pm's action_request_production so a dealer
    # can advance the order without bouncing into the backend form.
    @http.route(
        "/southbrook/api/order/<int:order_id>/request-production-approval",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_request_production_approval(
        self, order_id, **kw,
    ):
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        if not hasattr(order, "action_request_production"):
            return {"error": "not_supported"}
        try:
            order.with_user(request.env.user).action_request_production()
        except Exception as e:
            return {"error": "rejected", "message": str(e)[:300]}
        return {
            "ok": True,
            "approval_state": getattr(
                order, "production_approval_state", None,
            ),
        }

    def _southbrook_collect_validation(self, order):
        """Phase 3 Sprint B1 — produce ValidationStrip issue list.

        Each entry: {severity, code, message, line_id (or None)}.
          severity: 'hard'  → rule violated, line is invalid
                    'soft'  → suggestion / heuristic, not blocking
                    'info'  → FYI (maple +10%, etc.)

        Rules checked (CLAUDE.md §5 declarative set):
          1. Series → door style: Contractor only allows slab door,
             Elegance only allows five-piece woodgrain. Hard.
          2. Box material → series: Maple offered on Contemporary +
             Elegance only. Hard.
          3. Width → door count: 9-21" cabinets are 1-door; 24-36"
             are 2-door. Soft.
          4. Family → soft-close: bi-fold corner ships without
             soft-close. Soft (UI hides; this is just FYI).

        Plus Maple price-extra +10% / +2 week info entries per
        Sprint B2's lessons learned.

        2026-07-05 — the hard-severity checks (Rule 1, Rule 2) now live
        on sale.order.southbrook_hard_validation_issues() (southbrook_
        estimating), shared with action_confirm()'s own guard so the
        backend Confirm button enforces the same rules this portal
        preflight does, not just the client-side Send-to-Manufacturing
        button. This method still owns the soft/info severities below,
        since those never blocked anything and don't need a shared home.
        """
        issues = [
            dict(issue, severity="hard")
            for issue in order.southbrook_hard_validation_issues()
        ]
        for line in order.order_line:
            if not line.product_id:
                continue
            name = (line.name or "")
            name_lower = name.lower()

            # Variant attribute snapshot — preferred when present.
            attr_vals = {}
            for ptav in line.product_id.product_template_attribute_value_ids:
                attr = (ptav.attribute_id.name or "").lower()
                attr_vals.setdefault(attr, []).append(
                    (ptav.name or "").lower()
                )

            def _has_token(*tokens):
                return any(t in name_lower for t in tokens) or any(
                    any(t in v for v in vs)
                    for vs in attr_vals.values() for t in tokens
                )

            # Info — Maple lead-time + price impact (CLAUDE.md §5).
            if _has_token("maple"):
                issues.append({
                    "severity": "info",
                    "code": "maple_lead_extra",
                    "line_id": line.id,
                    "message": (
                        f"{line.name}: Maple carcass adds +10% "
                        "price and +2 weeks lead time."
                    ),
                })
            # Rule 3 — width → door-count sanity.
            import re as _re
            m = _re.search(r'(\d{1,2})\s*(?:"|″|in\b|in\.\b)',
                            name, _re.I)
            if m:
                w_in = int(m.group(1))
                door_m = _re.search(r'(\d)\s*[-– ]\s*door', name, _re.I)
                if door_m:
                    d = int(door_m.group(1))
                    if 9 <= w_in <= 21 and d != 1:
                        issues.append({
                            "severity": "soft",
                            "code": "narrow_should_be_1dr",
                            "line_id": line.id,
                            "message": (
                                f"{line.name}: cabinets {w_in}\" wide "
                                "should be 1-door — current spec uses "
                                f"{d}-door."
                            ),
                        })
                    elif 24 <= w_in <= 36 and d == 1:
                        issues.append({
                            "severity": "soft",
                            "code": "wide_should_be_2dr",
                            "line_id": line.id,
                            "message": (
                                f"{line.name}: cabinets {w_in}\" wide "
                                "are typically 2-door for door sag — "
                                "current spec uses 1-door."
                            ),
                        })
        return issues

    def _build_southbrook_order_payload(self, order):
        """Shape the order + lines + zones for client-side consumption.

        Stable contract (the OWL store keys against these names):

            {
              "order": {
                  id, name, state, version,
                  partner_id, partner_name, via,
                  channel, channel_label, channel_css,
                  tradesperson_tier,
                  pricelist_id, pricelist_name,
                  discount_pct,
                  retail_subtotal, channel_total, savings,
                  lead_time_days, line_count
              },
              "lines": [
                  {
                    id, sequence, product_id, product_name, product_sku,
                    family, zone, zone_label, qty,
                    price_unit, price_subtotal, retail_price, channel_price,
                    config_session_id, spec_summary, is_maple, rule_blocked
                  },
                  ...
              ],
              "zones": [
                  { code, label, line_count, subtotal, channel_subtotal },
                  ...
              ]
            }
        """
        partner = order.partner_id
        channel = (partner.channel or "retail") if hasattr(partner, "channel") else "retail"
        tier = (
            partner.tradesperson_tier
            if hasattr(partner, "tradesperson_tier") else None
        )
        # Resolve effective discount %.
        if channel == "tradesperson" and tier:
            discount_pct = self._TRADESPERSON_TIER_DISCOUNT.get(tier, 0)
            channel_label = f"CONTRACTOR · TIER {tier} · -{discount_pct}%"
        else:
            meta = self._CHANNEL_META.get(channel) or self._CHANNEL_META["retail"]
            discount_pct = meta["discount_pct"]
            channel_label = meta["label"]
        channel_css = (self._CHANNEL_META.get(channel) or {}).get("css", "retail")

        # Through-partner ("via Image Floor" reads when a dealer is the
        # customer's partner.parent_id).
        via = partner.parent_id.name if partner.parent_id else None

        retail_subtotal = sum(line.price_subtotal for line in order.order_line)
        channel_total = retail_subtotal * (1 - discount_pct / 100.0)
        savings = retail_subtotal - channel_total

        # 2026-06-27 — cost rollup for the channel-margin chip
        # (MFG reviewer P1#8). standard_price × qty per line; total
        # margin = channel_total - cost. Hidden when zero (e.g. empty
        # order, or templates without a cost — refacing SKUs).
        cost_subtotal = 0.0
        for line in order.order_line:
            if line.product_id:
                cost_subtotal += (
                    (line.product_id.standard_price or 0.0)
                    * (line.product_uom_qty or 0.0)
                )
        margin_total = channel_total - cost_subtotal
        # Guard against div-by-zero when channel_total is 0 (empty order
        # or all-zero pricing). Negative margin is real and shown red.
        margin_pct = (
            (margin_total / channel_total * 100.0)
            if channel_total > 0 else 0.0
        )

        # Per-line shape.
        lines = []
        zone_buckets = {}
        for idx, line in enumerate(order.order_line, start=1):
            tmpl = (
                line.product_id.product_tmpl_id
                if line.product_id and line.product_id.product_tmpl_id
                else None
            )
            sku = tmpl.default_code if tmpl else ""
            # Family lookup via the SKU defaults table on product.config.session
            # (already used by Track 1 — single source of truth).
            sku_row = request.env["product.config.session"]._SKU_DEFAULTS.get(sku)
            family = sku_row[0] if sku_row else ""

            # Spec summary — the line name carries the attribute mix for
            # dynamic-variant cabinets. Strip the product display prefix
            # when present so the spec text reads cleanly.
            spec_summary = (line.name or "").strip()
            if tmpl and tmpl.display_name and spec_summary.startswith(tmpl.display_name):
                spec_summary = spec_summary[len(tmpl.display_name):].lstrip(" /·-")

            is_maple = "Maple" in spec_summary

            line_retail = line.price_subtotal
            line_channel = line_retail * (1 - discount_pct / 100.0)

            # T2C9 — width fields for the line-row display. Width
            # comes from the SKU defaults table (commit 4 wired the
            # family lookup; same row carries the width). Phase 3
            # polish reads the configured width from the line's
            # variant attributes once dynamic-variant materialisation
            # lands; for now the SKU default is the natural fallback.
            width_mm = sku_row[3] if sku_row else 0
            # Round to 0.25" granularity (cabinet-industry standard).
            width_inches = (
                round((width_mm / 25.4) * 4) / 4 if width_mm else 0
            )

            # 2026-06-27 — ECO/PLM revision drift detection (MFG #1.5).
            # When a line was snapshotted at confirm against revision N
            # but the active revision has since moved to N+M, surface
            # the drift inline so the user knows the quoted spec is
            # stale relative to what manufacturing will build. Only
            # relevant for confirmed lines (snapshots fire at confirm).
            cut_spec_drift = False
            cut_spec_snap_name = ""
            cut_spec_current_name = ""
            bom_drift = False
            bom_snap_ver = 0
            bom_current_ver = 0
            snap_spec = getattr(line, "southbrook_cut_spec_version_id", False)
            if snap_spec:
                cut_spec_snap_name = snap_spec.display_name or ""
                active_spec = (
                    request.env["southbrook.cut.spec"]
                    .sudo()._get_active()
                    if "southbrook.cut.spec" in request.env else False
                )
                if active_spec:
                    cut_spec_current_name = active_spec.display_name or ""
                    cut_spec_drift = active_spec.id != snap_spec.id
            snap_bom_ver = getattr(line, "southbrook_bom_version", 0) or 0
            if snap_bom_ver and tmpl:
                BomLookup = request.env["mrp.bom"].sudo()
                current_bom = BomLookup.search(
                    [("product_tmpl_id", "=", tmpl.id), ("active", "=", True)],
                    limit=1,
                )
                if current_bom and getattr(
                    current_bom, "southbrook_version", 0,
                ):
                    bom_snap_ver = snap_bom_ver
                    bom_current_ver = current_bom.southbrook_version
                    bom_drift = bom_current_ver > snap_bom_ver

            line_payload = {
                "id": line.id,
                "sequence": idx,
                "product_id": line.product_id.id if line.product_id else None,
                "product_name": tmpl.display_name if tmpl else (line.name or ""),
                "product_sku": sku or "",
                "family": family,
                "zone": line.zone or "other",
                "zone_label": line.zone_label or "",
                "qty": line.product_uom_qty,
                "price_unit": line.price_unit,
                "price_subtotal": line.price_subtotal,
                "retail_price": line_retail,
                "channel_price": line_channel,
                "width_mm": width_mm,
                "width_inches": width_inches,
                # 2026-06-27 — ECO drift surfaces. Frontend gates the
                # orange chip on `revision_drift` being true.
                "revision_drift": cut_spec_drift or bom_drift,
                "cut_spec_drift": cut_spec_drift,
                "cut_spec_snap_name": cut_spec_snap_name,
                "cut_spec_current_name": cut_spec_current_name,
                "bom_drift": bom_drift,
                "bom_snap_ver": bom_snap_ver,
                "bom_current_ver": bom_current_ver,
                "config_session_id": (
                    line.config_session_id.id
                    if hasattr(line, "config_session_id")
                    and line.config_session_id
                    else None
                ),
                "spec_summary": spec_summary,
                "is_maple": is_maple,
                # Phase 3 polish wires the rule engine output per-line; for
                # now nothing is rule-blocked at payload time.
                "rule_blocked": False,
                # Phase 4 Sprint 3 — per-line BoM breakdown payload.
                # The fields are the B2 live-compute on sale.order.line
                # (sb_panel_count / sb_door_count / sb_width_mm) that
                # derive from variant attrs or parse from line.name.
                # The OWL BoMPreview "Per Line" section reads these to
                # surface panel/door counts at line resolution rather
                # than only at order total.
                "sb_panel_count": int(
                    getattr(line, "sb_panel_count", 0) or 0),
                "sb_door_count": int(
                    getattr(line, "sb_door_count", 0) or 0),
                "sb_width_mm": float(
                    getattr(line, "sb_width_mm", 0.0) or 0.0),
            }
            lines.append(line_payload)

            zb = zone_buckets.setdefault(
                line.zone or "other",
                {
                    "code": line.zone or "other",
                    "label": (
                        dict(line._fields["zone"].selection).get(
                            line.zone, line.zone or "Other",
                        )
                    ),
                    "line_count": 0,
                    "subtotal": 0.0,
                    "channel_subtotal": 0.0,
                },
            )
            zb["line_count"] += 1
            zb["subtotal"] += line_retail
            zb["channel_subtotal"] += line_channel

        # Stable zone ordering for the UI (per Q21 enumeration).
        zone_order = ["base_run", "wall", "tall", "island", "accessory", "other"]
        zones = [
            zone_buckets[z]
            for z in zone_order
            if z in zone_buckets
        ]

        # M19 (Manufacturing PM JTBD 2026-06-01) — lead-time rollup.
        # Base 14 days (2-week shop default for any in-flight order)
        # plus the MAX southbrook_lead_time_extra across order lines
        # (lines run in parallel, so total order time is governed by
        # the slowest cabinet — typically the Maple-box +14 day add).
        # Reading southbrook_lead_time_extra from the line's BoM if
        # one exists; orders with no BoM-resolved lines fall back to
        # the base 14.
        # 2026-06-27 — also stamp per-line lead_time_days so the OWL
        # OrderLine row can surface which cabinet is dragging the
        # schedule. Manufacturing JTBD: one glance to spot the maple
        # tall driving the whole order to 8wk.
        Bom = request.env["mrp.bom"].sudo()
        BASE_LEAD = 14
        lead_time_days = BASE_LEAD
        max_extra = 0
        for line in order.order_line:
            if not line.product_id:
                continue
            bom = Bom.search(
                [
                    "|",
                    ("product_id", "=", line.product_id.id),
                    "&",
                    ("product_id", "=", False),
                    (
                        "product_tmpl_id",
                        "=",
                        line.product_id.product_tmpl_id.id,
                    ),
                    ("type", "=", "normal"),
                ],
                order="sequence, id",
                limit=1,
            )
            extra = 0
            if bom and hasattr(bom, "southbrook_lead_time_extra"):
                extra = int(bom.southbrook_lead_time_extra or 0)
                max_extra = max(max_extra, extra)
            # Backfill the matching line_payload by line.id — payloads
            # are keyed by line id in `lines`.
            for lp in lines:
                if lp["id"] == line.id:
                    lp["lead_time_days"] = BASE_LEAD + extra
                    break
        # Lines that never matched (no BoM) fall back to the base.
        for lp in lines:
            lp.setdefault("lead_time_days", BASE_LEAD)
        lead_time_days += int(max_extra)

        # 2026-06-27 — cabinet-class summary for the HeaderStrip glance
        # check ("12 base · 8 wall · 3 tall"). Grouped by family (the
        # cabinet's intrinsic class) so the count is independent of
        # zone assignment. Order matches the standard kitchen layout
        # convention (base → wall → tall → island → accessory).
        family_order = ["base", "wall", "tall", "island", "accessory", "other"]
        family_label = {
            "base": "base",
            "wall": "wall",
            "tall": "tall",
            "island": "island",
            "accessory": "accessory",
            "other": "other",
        }
        family_count_map = {f: 0 for f in family_order}
        for lp in lines:
            fam = (lp.get("family") or "other").lower()
            # Normalise edge-case families ("base_2dr" → "base").
            fam_bucket = next(
                (f for f in family_order if fam.startswith(f)),
                "other",
            )
            family_count_map[fam_bucket] += int(lp.get("qty") or 1)
        family_counts = [
            {"code": f, "label": family_label[f], "count": family_count_map[f]}
            for f in family_order
            if family_count_map[f] > 0
        ]

        # T2C11 — BoM rollup across the order's SB cabinets. Computes
        # panel + hardware + edge-banding totals by calling Phase-1
        # routine #1 (mrp.bom._compute_panel_dimensions) per line and
        # summing. Same single-source-of-truth pattern as the 3D
        # viewport (Track 1) and the kitchen-run view (T1C6) so any
        # change to BoX_TH / DOOR_TH / etc. propagates everywhere.
        bom_rollup = {
            "cabinet_count": 0,
            "panels": {
                "side": 0,
                "top": 0,
                "bottom": 0,
                "back": 0,
                "shelf": 0,
                "door": 0,
                "drawer_front": 0,
            },
            "hardware": {
                "hinge_pair_count": 0,
                "handle_count": 0,
                "drawer_slide_pair_count": 0,
            },
            "edge_banding_mm": 0,
        }
        Bom = request.env["mrp.bom"]
        # T2C11 fix: walk THIS order's lines, not self.order_line. `self`
        # is the CustomerPortal controller, not the sale.order — the
        # original copy-paste from sale_order.get_kitchen_3d_payload
        # missed the rebind.
        for line in order.order_line:
            tmpl = (
                line.product_id.product_tmpl_id
                if line.product_id and line.product_id.product_tmpl_id
                else None
            )
            sku = tmpl.default_code if tmpl else None
            sku_row = (
                request.env["product.config.session"]._SKU_DEFAULTS.get(sku)
                if sku else None
            )
            if not sku_row:
                continue
            fam, doors, drawers, w, h, d = sku_row
            cut = Bom._compute_panel_dimensions(
                width_mm=w, height_mm=h, depth_mm=d,
                family=fam, door_count=doors, drawer_count=drawers,
                finished_sides="none",
            )
            qty = int(line.product_uom_qty or 0)
            if qty <= 0:
                continue

            bom_rollup["cabinet_count"] += qty
            # Each cabinet: 2 sides + 1 top + 1 bottom + 1 back.
            # Worktop / accessory short-circuit: only 1 panel, no carcass.
            if fam in ("worktop", "accessory"):
                bom_rollup["panels"]["side"] += 1 * qty
            else:
                bom_rollup["panels"]["side"] += 2 * qty
                bom_rollup["panels"]["top"] += 1 * qty
                bom_rollup["panels"]["bottom"] += 1 * qty
                bom_rollup["panels"]["back"] += 1 * qty
            bom_rollup["panels"]["shelf"] += cut.get("shelf_count", 0) * qty
            bom_rollup["panels"]["door"] += cut.get("door_count", 0) * qty
            bom_rollup["panels"]["drawer_front"] += cut.get("drawer_count", 0) * qty
            bom_rollup["hardware"]["hinge_pair_count"] += cut.get("hinge_pair_count", 0) * qty
            bom_rollup["hardware"]["handle_count"] += cut.get("handle_count", 0) * qty
            bom_rollup["hardware"]["drawer_slide_pair_count"] += cut.get("drawer_slide_pair_count", 0) * qty
            bom_rollup["edge_banding_mm"] += cut.get("edge_banding_length_mm", 0) * qty

        # Phase 3 Sprint B1 — rule engine output. Two layers:
        #   1. Per-line inspection of the CLAUDE.md §5 hard rules
        #      against parsed line.name + variant attrs. Covers
        #      demo-seed orders where no config_session exists.
        #   2. For lines WITH a config_session, walk the session's
        #      product.config.line records and surface any conflict
        #      the OCA validator already detected.
        validation = self._southbrook_collect_validation(order)

        # 2026-06-27 — gate IllustrativeBanner on the canonical config_param.
        # Default 'canonical' so an unseeded prod DB does NOT show "ILLUSTRATIVE
        # SEED · Demo numbers" to real customers. The seeded value remains
        # 'illustrative' so dev databases keep the warning.
        seed_mode = request.env["ir.config_parameter"].sudo().get_param(
            "southbrook.seed_mode", default="canonical",
        )

        return {
            "order": {
                "id":             order.id,
                "name":           order.name,
                "state":          order.state,
                "version":        getattr(order, "version", 1),
                "partner_id":     partner.id,
                "partner_name":   partner.name,
                # Customer identity (2026-07-06): surfaced so the Order
                # Builder can show WHO the order is for and prefill the
                # capture form. A partner that is just the logged-in
                # employee (internal user) is flagged so the UI can prompt
                # the rep to identify the real customer.
                "partner_email":  partner.email or "",
                "partner_phone":  partner.phone or "",
                "partner_is_self": bool(
                    partner.id == request.env.user.partner_id.id),
                "partner_is_internal": bool(
                    partner.user_ids and not all(u.share for u in partner.user_ids)),
                "via":            via,
                "channel":        channel,
                "channel_label":  channel_label,
                "channel_css":    channel_css,
                "tradesperson_tier": tier,
                "seed_mode":      seed_mode,
                "pricelist_id":   order.pricelist_id.id if order.pricelist_id else None,
                "pricelist_name": order.pricelist_id.name if order.pricelist_id else "",
                "discount_pct":   discount_pct,
                "retail_subtotal": retail_subtotal,
                "channel_total":  channel_total,
                "savings":        savings,
                # 2026-06-27 — channel margin surfaces (MFG #1.8).
                # cost_subtotal == sum(standard_price × qty); margin_pct
                # is (channel - cost)/channel × 100. Frontend renders a
                # margin chip red below 10%, amber 10-15%, green ≥ 15%.
                "cost_subtotal":  cost_subtotal,
                "margin_total":   margin_total,
                "margin_pct":     round(margin_pct, 1),
                "lead_time_days": lead_time_days,
                "line_count":     len(lines),
                # G14 + G17 (2026-06-01) — timeline timestamps for the
                # customer-visible StagePipeline. None when the order
                # hasn't reached that stage yet.
                "created_date": (
                    order.create_date.isoformat() if order.create_date else None
                ),
                "submitted_date": (
                    order.southbrook_submitted_date.isoformat()
                    if getattr(order, "southbrook_submitted_date", False)
                    else None
                ),
                "confirmed_date": (
                    order.date_order.isoformat()
                    if order.state in ("sale", "done") and order.date_order
                    else None
                ),
                # Phase 3 Sprint C3 — parent-order chain (NF6 history).
                # Newest first, each entry: {id, name, version, state,
                # amount_total, date_order, is_current}. Empty list
                # for v1 orders that were never duplicated.
                "history_chain": (
                    order._southbrook_history_chain()
                    if hasattr(order, "_southbrook_history_chain") else []
                ),
                # 2026-06-27 — server write_date for the "last saved
                # X min ago" stamp at order level (FE reviewer P3#13).
                # ISO string so the OWL formatter can produce a
                # localised relative-time label without parsing
                # ambiguity.
                "write_date": (
                    order.write_date.isoformat()
                    if order.write_date else None
                ),
                # 2026-06-27 — production-approval surfaces. Optional
                # (defaults to None) so the page renders cleanly even
                # when southbrook_mrp_pm isn't installed. The OWL
                # chip + Request-Approval button are gated on
                # production_approval_state being non-null.
                "production_approval_state": getattr(
                    order, "production_approval_state", None,
                ),
                "production_requested_by_name": (
                    getattr(order, "production_requested_by", False)
                    and order.production_requested_by.name
                ) or "",
                "production_approved_by_name": (
                    getattr(order, "production_approved_by", False)
                    and order.production_approved_by.name
                ) or "",
                "production_reject_reason": (
                    getattr(order, "production_reject_reason", "") or ""
                ),
            },
            "lines": lines,
            "zones": zones,
            "family_counts": family_counts,
            "bom_rollup": bom_rollup,
            "validation": validation,
        }
