# SPDX-License-Identifier: LGPL-3.0-only
"""Supplier catalogue import with a mandatory dry run.

The flow is deliberately three-step — Fetch, Analyse, Commit — and Analyse
writes nothing outside this module's own tables. You always see the complete
plan, including every conflict, before a single product record changes.
"""

import base64
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import catalog_parser as parser

_logger = logging.getLogger(__name__)

ACTION_SELECTION = [
    ("create", "Create"),
    ("update", "Update existing"),
    ("skip", "Skip"),
    ("review", "Needs review"),
]


class SupplierCatalogImport(models.Model):
    _name = "supplier.catalog.import"
    _description = "Supplier Catalogue Import Run"
    _order = "create_date desc"

    name = fields.Char(required=True, default="New Import", copy=False)
    partner_id = fields.Many2one(
        "res.partner", "Supplier", required=True,
        domain=[("supplier_rank", ">", 0)],
        help="Vendor these prices belong to. Price breaks are written as "
             "product.supplierinfo rows against this partner.")
    source_url = fields.Char(
        "Feed URL",
        default="https://eurofitcanada.com/products.json?limit=250&page=%d",
        help="Shopify products.json endpoint. Must contain a %d placeholder "
             "for the page number.")
    payload = fields.Binary(
        "Uploaded Feed",
        help="Optional. A saved products.json (or a JSON list of pages). When "
             "set, this is parsed instead of fetching the URL — which is how "
             "the tests run and how you re-analyse a feed without hitting the "
             "supplier's site again.")
    payload_name = fields.Char("Uploaded Filename")
    state = fields.Selection(
        [("draft", "Draft"), ("fetched", "Fetched"),
         ("analysed", "Analysed (dry run)"), ("committed", "Committed")],
        default="draft", required=True, readonly=True)
    line_ids = fields.One2many(
        "supplier.catalog.import.line", "import_id", "Staged Items",
        readonly=True)
    log = fields.Text("Run Log", readonly=True)

    count_total = fields.Integer("Items", compute="_compute_counts")
    count_create = fields.Integer("To Create", compute="_compute_counts")
    count_update = fields.Integer("To Update", compute="_compute_counts")
    count_review = fields.Integer("Needs Review", compute="_compute_counts")
    count_kit = fields.Integer("Kit Formulas", compute="_compute_counts")
    count_tool_clash = fields.Integer("Tool Clashes", compute="_compute_counts")

    @api.depends("line_ids.action", "line_ids.is_kit_formula",
                 "line_ids.tool_clash")
    def _compute_counts(self):
        for run in self:
            lines = run.line_ids
            run.count_total = len(lines)
            run.count_create = len(lines.filtered(lambda l: l.action == "create"))
            run.count_update = len(lines.filtered(lambda l: l.action == "update"))
            run.count_review = len(lines.filtered(lambda l: l.action == "review"))
            run.count_kit = len(lines.filtered("is_kit_formula"))
            run.count_tool_clash = len(lines.filtered("tool_clash"))

    # ------------------------------------------------------------------
    # 1. Fetch
    # ------------------------------------------------------------------
    def _load_products(self):
        """Return the raw product dicts, from the upload or the URL."""
        self.ensure_one()
        if self.payload:
            raw = base64.b64decode(self.payload)
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                return data.get("products") or []
            if isinstance(data, list):
                # Either a list of pages, or already a list of products.
                out = []
                for entry in data:
                    if isinstance(entry, dict) and "products" in entry:
                        out += entry["products"]
                    elif isinstance(entry, dict):
                        out.append(entry)
                return out
            raise UserError(_("Uploaded feed is not JSON this importer understands."))
        return self._fetch_remote()

    def _fetch_remote(self):
        """Page through the supplier feed.

        Kept separate so tests never touch the network, and so a failure here
        is clearly a connectivity problem rather than a parsing one.
        """
        self.ensure_one()
        if not self.source_url or "%d" not in self.source_url:
            raise UserError(_(
                "The feed URL must contain a %%d placeholder for the page "
                "number, for example "
                "https://eurofitcanada.com/products.json?limit=250&page=%%d"))
        try:
            import requests
        except ImportError:  # pragma: no cover
            raise UserError(_("The 'requests' library is not available."))
        products, page = [], 1
        while page <= 50:  # hard stop; the observed feed is 3 pages
            resp = requests.get(
                self.source_url % page, timeout=60,
                headers={"User-Agent": "Southbrook-Odoo-CatalogImport/1.0"})
            if resp.status_code != 200:
                raise UserError(_(
                    "Supplier feed returned HTTP %(code)s on page %(page)s.",
                    code=resp.status_code, page=page))
            batch = (resp.json() or {}).get("products") or []
            if not batch:
                break
            products += batch
            page += 1
        return products

    def action_fetch(self):
        self.ensure_one()
        self.line_ids.unlink()
        products = self._load_products()
        if not products:
            raise UserError(_("The feed contained no products."))
        items = parser.parse_shopify_products(products)
        Line = self.env["supplier.catalog.import.line"]
        vals = []
        for item in items:
            vals.append({
                "import_id": self.id,
                "sku": item["sku"],
                "title": item["title"][:250],
                "brand": item["brand"],
                "product_type": item["product_type"],
                "category_branch": item["category_branch"],
                "category_leaf": item["category_leaf"],
                "description": item["description"],
                "attributes_json": json.dumps(item["attributes"]),
                "tiers_json": json.dumps(item["tiers"]),
                "weight_kg": (item["weight_g"] or 0) / 1000.0,
                "available": item["available"],
                "image_url": (item["image_urls"] or [None])[0],
                "image_count": len(item["image_urls"]),
                "is_kit_formula": item["is_kit_formula"],
                "kit_components": ", ".join(
                    "%s x%s" % (c, q) for c, q in item["kit_components"]),
                "handle": item["handle"],
            })
        Line.create(vals)
        self.write({
            "state": "fetched",
            "log": _("Fetched %(prod)s products from the feed, staged as "
                     "%(items)s sellable items.",
                     prod=len(products), items=len(vals)),
        })
        return True

    # ------------------------------------------------------------------
    # 2. Analyse — the dry run. Writes nothing outside this module.
    # ------------------------------------------------------------------
    def action_analyse(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Nothing staged. Fetch the feed first."))
        Product = self.env["product.product"]
        Template = self.env["product.template"]
        notes = []

        # Existing references, matched case-insensitively because supplier
        # feeds are inconsistent about case and a near-miss creates a
        # duplicate that is very hard to spot later.
        existing = {}
        for prod in Product.search([("default_code", "!=", False)]):
            existing.setdefault(prod.default_code.strip().lower(), prod)

        # Products already flagged as shop tools. Eurofit sells jigs that
        # Southbrook may already own and track as reusable assets; importing
        # those as sellable hardware creates a parallel record for the same
        # physical tool.
        tool_codes = set()
        if "x_southbrook_is_tool" in Template._fields:
            for tmpl in Template.search([("x_southbrook_is_tool", "=", True)]):
                for variant in tmpl.product_variant_ids:
                    if variant.default_code:
                        tool_codes.add(variant.default_code.strip().lower())

        for line in self.line_ids:
            key = (line.sku or "").strip().lower()
            reasons = []
            action = "create"

            if not line.sku:
                action = "review"
                reasons.append(
                    _("No SKU in the feed — cannot key a product on it."))
            elif line.is_kit_formula:
                action = "review"
                reasons.append(_(
                    "SKU is an assembly formula, not a part number. It "
                    "encodes a bill of materials and should seed an mrp.bom "
                    "rather than become a default_code."))
            elif key in existing:
                action = "update"
                line.matched_product_id = existing[key].id
                reasons.append(_("Matches existing product %s.",
                                 existing[key].display_name))

            if key and key in tool_codes:
                line.tool_clash = True
                action = "review"
                reasons.append(_(
                    "This SKU is already tracked as a Southbrook shop tool. "
                    "Route it to tool-asset provisioning instead of the "
                    "sellable hardware catalogue."))

            if line.category_leaf == parser.UNCLASSIFIED[1]:
                reasons.append(_(
                    "Supplier gave no product type — category needs a human."))
                if action == "create":
                    action = "review"

            if not line.tiers_json or line.tiers_json == "[]":
                reasons.append(_("No price found."))
                action = "review"

            line.action = action
            line.reason = " ".join(reasons) or _("Clean import.")

        cats = {(l.category_branch, l.category_leaf) for l in self.line_ids}
        notes.append(_("%s category leaves required.", len(cats)))
        notes.append(_("%s items need review before committing.",
                       self.count_review))
        self.write({"state": "analysed", "log": "\n".join(notes)})
        return True

    # ------------------------------------------------------------------
    # 3. Commit
    # ------------------------------------------------------------------
    def _category(self, branch, leaf):
        """Get or create Hardware / <branch> / <leaf>, cached per run."""
        Category = self.env["product.category"]
        root = Category.search([("name", "=", "Hardware"),
                                ("parent_id", "=", False)], limit=1)
        if not root:
            root = Category.create({"name": "Hardware"})
        mid = Category.search([("name", "=", branch),
                               ("parent_id", "=", root.id)], limit=1)
        if not mid:
            mid = Category.create({"name": branch, "parent_id": root.id})
        leaf_cat = Category.search([("name", "=", leaf),
                                    ("parent_id", "=", mid.id)], limit=1)
        if not leaf_cat:
            leaf_cat = Category.create({"name": leaf, "parent_id": mid.id})
        return leaf_cat

    def action_commit(self):
        self.ensure_one()
        if self.state != "analysed":
            raise UserError(_(
                "Run the dry run first. Commit is only available once you "
                "have seen the plan."))
        Template = self.env["product.template"]
        Supplierinfo = self.env["product.supplierinfo"]
        created = updated = 0

        for line in self.line_ids.filtered(lambda l: l.action in ("create", "update")):
            category = self._category(line.category_branch, line.category_leaf)
            # Created as a TEMPLATE, not a product.product: name, categ_id,
            # type, is_storable, weight and description_purchase are all
            # template-level fields (is_storable in particular does not exist
            # on product.product in v19 and raises).
            vals = {
                "name": line.title or line.sku,
                "default_code": line.sku,
                "categ_id": category.id,
                "type": "consu",
                "is_storable": True,
                "purchase_ok": True,
                "sale_ok": False,
                "description_purchase": line.description or False,
                "weight": line.weight_kg or 0.0,
            }
            if line.action == "update" and line.matched_product_id:
                product = line.matched_product_id
                tmpl = product.product_tmpl_id
                # Never overwrite a name or category a human has curated; a
                # supplier feed is not authoritative over Southbrook's own
                # naming. Only fill what is genuinely missing.
                safe = {}
                if not tmpl.weight and vals["weight"]:
                    safe["weight"] = vals["weight"]
                if not tmpl.description_purchase and line.description:
                    safe["description_purchase"] = line.description
                if safe:
                    tmpl.write(safe)
                updated += 1
            else:
                tmpl = Template.create(vals)
                product = tmpl.product_variant_id
                line.matched_product_id = product.id
                created += 1

            # Price breaks: one supplierinfo row per tier.
            tiers = json.loads(line.tiers_json or "[]")
            Supplierinfo.search([
                ("partner_id", "=", self.partner_id.id),
                ("product_id", "=", product.id),
            ]).unlink()
            for tier in tiers:
                Supplierinfo.create({
                    "partner_id": self.partner_id.id,
                    "product_id": product.id,
                    "product_tmpl_id": product.product_tmpl_id.id,
                    "min_qty": tier.get("min_qty") or 1,
                    "price": tier.get("price") or 0.0,
                    "product_code": line.sku or False,
                    "delay": 7,
                })

        self.write({
            "state": "committed",
            "log": (self.log or "") + "\n" + _(
                "Committed: %(c)s products created, %(u)s updated. "
                "%(r)s left for review and untouched.",
                c=created, u=updated, r=self.count_review),
        })
        return True

    def action_reset(self):
        """Back to draft so the feed can be re-analysed."""
        self.ensure_one()
        if self.state == "committed":
            raise UserError(_(
                "This run has already been committed. Create a new run "
                "rather than replaying a committed one."))
        self.write({"state": "draft", "log": False})
        self.line_ids.unlink()
        return True


class SupplierCatalogImportLine(models.Model):
    _name = "supplier.catalog.import.line"
    _description = "Staged Supplier Catalogue Item"
    _order = "action, category_branch, category_leaf, title"

    import_id = fields.Many2one(
        "supplier.catalog.import", required=True, ondelete="cascade",
        index=True)
    sku = fields.Char(index=True)
    title = fields.Char()
    brand = fields.Char()
    product_type = fields.Char("Supplier Type")
    category_branch = fields.Char()
    category_leaf = fields.Char()
    description = fields.Text()
    attributes_json = fields.Text("Attributes")
    tiers_json = fields.Text("Price Breaks")
    weight_kg = fields.Float(digits="Stock Weight")
    available = fields.Boolean()
    image_url = fields.Char()
    image_count = fields.Integer()
    handle = fields.Char()
    is_kit_formula = fields.Boolean(
        help="The SKU is an assembly expression, not a part number.")
    kit_components = fields.Char(
        help="Parts decoded from the assembly expression, for BoM seeding.")
    tool_clash = fields.Boolean(
        help="Already tracked as a Southbrook shop tool.")
    action = fields.Selection(ACTION_SELECTION, default="create", index=True)
    reason = fields.Char()
    matched_product_id = fields.Many2one("product.product", "Matched Product")
