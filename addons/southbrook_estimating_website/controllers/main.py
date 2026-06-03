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
import logging

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)


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
class SouthbrookKitchenPlanner(http.Controller):
    """Customer-facing /kitchen-planner one-page configurator route."""

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
        type="json",
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
            if channel_pricelist and base_variant:
                try:
                    channel_price = channel_pricelist._get_product_price(
                        base_variant, 1.0, partner,
                    )
                except Exception:                       # noqa: BLE001
                    _logger.warning(
                        "kitchen_planner_state: pricelist %s could not "
                        "price cabinet %s (variant id=%s); falling back "
                        "to list_price for display.",
                        channel_pricelist.display_name,
                        sku or tmpl.name,
                        base_variant.id,
                        exc_info=True,
                    )
            # Round to website currency precision (same path list_price
            # would be formatted through downstream).
            if currency:
                channel_price = currency.round(channel_price)
            catalog.append({
                "id": tmpl.id,
                "sku": sku,
                "name": tmpl.name,
                "list_price": tmpl.list_price,
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
        type="json",
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
        type="json",
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
        type="json",
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


class SouthbrookOrderBuilderPortal(CustomerPortal):
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
    # Auth helpers
    # ------------------------------------------------------------------

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

          • Anything else → AccessError, controller redirects to /my.
        """
        order = request.env["sale.order"].sudo().browse(order_id).exists()
        if not order:
            raise MissingError("Sale order not found.")

        user = request.env.user
        if not user.share:
            # Internal user — full access.
            return order

        my_partner = user.partner_id
        order_partner = order.partner_id
        if my_partner == order_partner:
            return order
        if order_partner.parent_id and order_partner.parent_id == my_partner:
            return order
        if my_partner.parent_id and my_partner.parent_id == order_partner:
            return order
        # Phase 3: dealer-portal page lists every order under the
        # dealer's whole partner tree; for commit 1 we keep access
        # tight to the same partner or first-level parent/child.
        raise AccessError("This order is not accessible to your account.")

    def _prepare_southbrook_portal_values(self, order):
        """Common template context (sidebar, breadcrumb, palette tokens)."""
        values = self._prepare_portal_layout_values()
        # G14 (2026-06-01) — auto-select customer mode for portal users
        # (res.users.share=True). Internal users still get dealer mode
        # by default so the dealer/sales-rep workflow is unchanged.
        # Override at any time via ?mode=customer or ?mode=dealer on
        # the URL (the OWL bootstrap reads URL param first).
        user = request.env.user
        is_portal_user = bool(user.share)
        order_mode = "customer" if is_portal_user else "dealer"
        values.update({
            "page_name": "southbrook_order_builder",
            "order": order,
            "order_id": order.id if order else None,
            "order_name": order.name if order else "New Order",
            "user_partner": request.env.user.partner_id,
            # Track 2 commits 2+ will add an "owl_mount_id" used by the
            # OWL bootstrap to find its mount point on the page.
            "owl_mount_id": "order_builder_root",
            # G14 — written to data-mode on the mount div; the OWL
            # bootstrap reads it and propagates as props.mode.
            "order_mode": order_mode,
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
        type="json",
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
        if qty is not None:
            try:
                qty_f = float(qty)
            except (TypeError, ValueError):
                return {"error": "invalid_qty"}
            if qty_f <= 0:
                return {"error": "invalid_qty"}
            line.with_user(request.env.user).product_uom_qty = qty_f

        return {"ok": True, "line_id": line.id}

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
        order; raise AccessError / MissingError otherwise. Mirrors
        the partner-chain check used by /api/order/<id>."""
        line = (
            request.env["sale.order.line"]
            .sudo()
            .browse(line_id)
            .exists()
        )
        if not line:
            raise MissingError("line not found")
        # Walks back through the order to the existing resolver,
        # which is the canonical access check.
        self._southbrook_resolve_order(line.order_id.id)
        return line

    @http.route(
        "/southbrook/api/line/<int:line_id>/attributes",
        type="json",
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
        # The variant's value set — empty for the default fast-path
        # variant created by /add-line. We use it to mark 'current'.
        current_ptav = line.product_id.product_template_attribute_value_ids
        current_value_ids = current_ptav.product_attribute_value_id.ids

        attributes = []
        for attr_line in tmpl.attribute_line_ids:
            # Hide attributes with a single option — nothing to pick.
            if len(attr_line.value_ids) < 2:
                continue
            attributes.append({
                "attribute_id": attr_line.attribute_id.id,
                "name": attr_line.attribute_id.name,
                "display_type": attr_line.attribute_id.display_type or "select",
                "values": [
                    {
                        "value_id": v.id,
                        "name": v.name,
                        "current": v.id in current_value_ids,
                    }
                    for v in attr_line.value_ids
                ],
            })
        return {"ok": True, "attributes": attributes}

    @http.route(
        "/southbrook/api/line/<int:line_id>/set-attribute",
        type="json",
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
        # Trigger the standard onchange so name/description rebuild.
        if hasattr(line, "product_id_change"):
            line.product_id_change()

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
    @http.route(
        "/southbrook/api/order/<int:order_id>/add-line",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order_add_line(
        self, order_id, product_tmpl_id=None, qty=None, **kw,
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

        line = (
            request.env["sale.order.line"]
            .sudo()
            .create({
                "order_id": order.id,
                "product_id": variant.id,
                "product_uom_qty": qty_int,
            })
        )
        # Trigger Odoo's onchange-equivalent so price_unit /
        # product_uom / name get populated from the variant.
        line.product_id_change() if hasattr(line, "product_id_change") else None

        return {"ok": True, "line_id": line.id}

    # T2C12 — FooterActions dispatcher.
    #
    # Single endpoint that the OWL FooterActions component calls with
    # action_code in {confirm, duplicate, print}. Returns {ok, ...}
    # or {error, ...}. The dispatch keeps the API surface small and
    # avoids one route per button.
    @http.route(
        "/southbrook/api/order/<int:order_id>/action",
        type="json",
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

        return {"error": "unknown_action", "message": str(action_code)}

    # Phase 2.5 commit 1 — portal kitchen-3d payload.
    #
    # Mirrors the backend `sale.order.get_kitchen_3d_payload` (Track 1
    # T1C6) for portal-auth consumers. Returns the same shape so the
    # portal-side KitchenViewport can reuse Track 1's rendering logic
    # without payload translation.
    @http.route(
        "/southbrook/api/order/<int:order_id>/kitchen-3d",
        type="json",
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

    @http.route(
        "/southbrook/api/order/<int:order_id>",
        type="json",
        auth="user",
        methods=["POST"],
    )
    def southbrook_api_order(self, order_id, **kw):
        """Return the order shape for the OWL store."""
        try:
            order = self._southbrook_resolve_order(order_id)
        except MissingError:
            return {"error": "not_found"}
        except AccessError:
            return {"error": "forbidden"}
        return self._build_southbrook_order_payload(order)

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
        Bom = request.env["mrp.bom"].sudo()
        lead_time_days = 14
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
            if bom and hasattr(bom, "southbrook_lead_time_extra"):
                max_extra = max(max_extra, bom.southbrook_lead_time_extra or 0)
        lead_time_days += int(max_extra)

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

        # T2C11 — validation issue list. Phase 1 ships an empty list;
        # Phase 3 polish wires the OCA rule engine output here (per-
        # line hard/soft issues). Keeping the key in place now so
        # the OWL ValidationStrip doesn't need to widen the contract.
        validation = []

        return {
            "order": {
                "id":             order.id,
                "name":           order.name,
                "state":          order.state,
                # production-approval gate (southbrook_mrp_pm). getattr
                # fallback: this serializer lives in the website addon,
                # which doesn't depend on mrp_pm, so the field is absent
                # when the gate module isn't installed.
                "production_approval_state": getattr(
                    order, "production_approval_state", "none"
                ),
                "version":        getattr(order, "version", 1),
                "partner_id":     partner.id,
                "partner_name":   partner.name,
                "via":            via,
                "channel":        channel,
                "channel_label":  channel_label,
                "channel_css":    channel_css,
                "tradesperson_tier": tier,
                "pricelist_id":   order.pricelist_id.id if order.pricelist_id else None,
                "pricelist_name": order.pricelist_id.name if order.pricelist_id else "",
                "discount_pct":   discount_pct,
                "retail_subtotal": retail_subtotal,
                "channel_total":  channel_total,
                "savings":        savings,
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
            },
            "lines": lines,
            "zones": zones,
            "bom_rollup": bom_rollup,
            "validation": validation,
        }
