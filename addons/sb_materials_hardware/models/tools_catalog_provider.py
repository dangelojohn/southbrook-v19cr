# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 1 provider: southbrook_mrp_kitchen_tools data.

This is the ONLY file that knows about x_southbrook_* fields or
southbrook.tool.category. Phase 2's hardware provider is a sibling file.
"""
import logging

from odoo import api, models
from odoo.fields import Domain
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

PROVENANCE = (
    "Read-only view of the tool and consumable master. Specs are optional — "
    "an absent value is shown as a dash, never as zero. Safety flags are "
    "three-state — \"not assessed\" is the default truth, and it is never "
    "rendered as safe."
)

GENERIC_COLUMNS = [
    {"key": "name", "label": "Description", "align": "left", "sortable": True},
    {"key": "default_code", "label": "Reference", "align": "left",
     "sortable": True},
]

# Numeric and boolean values are never absence — 0 and False are real data
# for these types and must survive verbatim. Everything else's falsy value
# ("never set") normalizes to None. Shared by _rows (table) and _build_detail
# (spec grid + engineering rail) so there is exactly one absence rule.
VALUE_IS_NEVER_ABSENT = {"float", "integer", "monetary", "boolean"}

# Of the types above, only these three are genuinely ambiguous at the ORM
# level: read() returns 0/0.0 for both "never entered" and "entered as
# zero" on a Float/Integer/Monetary column, because Odoo has no Python-side
# NULL for them. Boolean is deliberately excluded — a Boolean column reads
# back as an honest True/False either way, so False never needs the SQL
# null-mask to mean what it says.
NUMERIC_AMBIGUOUS_TYPES = {"float", "integer", "monetary"}


def _normalize_value(value, field, is_null=False):
    """Apply the catalog's absence convention to one raw field value.

    Shared by _rows() (table cells) and _detail_value() (spec grid +
    engineering rail) so there is exactly one place that decides both
    what "absent" means AND how a relational value is shaped — the row
    and the detail panel can never disagree about the same field.

    `value` may be a batched read()'s raw shape: a many2one comes back as
    Odoo's (id, display_name) tuple, which is unwrapped to the display
    name here, before the same field ever reaches its type-aware absence
    check below.

    `field` is the product.template field object (or None if the key isn't
    a real field on the model) — its `.type` decides whether a falsy raw
    value means "never set" (-> None) or is genuine data to keep as-is.

    `is_null` is the answer — determined at the SQL layer, see
    ToolsCatalogProvider._null_mask() — to the one question read() itself
    cannot answer: was this NUMERIC column actually NULL in Postgres, as
    opposed to holding a genuine stored 0? It is only consulted for the
    three ambiguous numeric types; for every other type read()'s own
    falsy-value check below is already authoritative.
    """
    if isinstance(value, tuple):
        value = value[1] if len(value) > 1 else (value[0] if value else False)
    ftype = field.type if field else None
    if ftype in NUMERIC_AMBIGUOUS_TYPES:
        return None if is_null else value
    if ftype in VALUE_IS_NEVER_ABSENT:
        return value
    return None if not value else value


BADGE_FIELDS = [
    ("x_southbrook_hazardous", "Hazardous"),
    ("x_southbrook_flammable", "Flammable"),
    ("x_southbrook_msds_required", "MSDS required"),
    ("x_southbrook_requires_ventilation", "Ventilation required"),
    ("x_southbrook_expiry_required", "Expiry tracked"),
]

# The four safety fields carry a tri-state Selection on product.template
# (southbrook_mrp_kitchen_tools: "yes" / "no" / "not_assessed", default
# "not_assessed" — see that module's product_template.py). Unlike every
# other BADGE_FIELDS entry, these must NEVER be silently omitted: omitting
# the badge for a not-yet-assessed product is exactly the "absence renders
# as safe" failure this wave exists to close. `label` is the existing
# danger-style label for "yes"; `negative_label` is a distinct, still-worth-
# stating label for a verified "no" — "Non-flammable" is information, not
# nothing, so it gets its own badge rather than being folded into silence.
# "not_assessed" (or a bare False/None, for a column not-yet-migrated to
# the selection shape — see _safety_badge) renders as "<label>: not
# assessed" in the unset style.
SAFETY_BADGE_FIELDS = [
    ("x_southbrook_hazardous", "Hazardous", "Non-hazardous"),
    ("x_southbrook_flammable", "Flammable", "Non-flammable"),
    ("x_southbrook_msds_required", "MSDS required", "No MSDS required"),
    ("x_southbrook_requires_ventilation", "Ventilation required",
     "No ventilation required"),
]
SAFETY_BADGE_NEGATIVE_LABELS = {
    field_name: negative_label
    for field_name, _label, negative_label in SAFETY_BADGE_FIELDS
}
SAFETY_BADGE_FIELD_NAMES = set(SAFETY_BADGE_NEGATIVE_LABELS)

ENGINEERING_FIELDS = [
    ("x_southbrook_preferred_vendor_id", "Preferred vendor"),
    ("x_southbrook_vendor_sku", "Vendor SKU"),
    ("x_southbrook_issue_uom_id", "Issue UoM"),
    ("x_southbrook_min_stock_qty", "Min stock"),
    ("x_southbrook_max_stock_qty", "Max stock"),
    ("x_southbrook_reorder_multiple", "Reorder multiple"),
    ("x_southbrook_estimated_life_qty", "Estimated life"),
    ("x_southbrook_estimated_life_unit", "Life unit"),
    ("x_southbrook_tool_lifecycle_state", "Lifecycle"),
]


class ToolsCatalogProvider(models.AbstractModel):
    _name = "tools.catalog.provider"
    _inherit = "materials.catalog.provider"
    _description = "Tools and consumables catalog provider"

    @api.model
    def _build_payload(self, scope, category_id, facets, search, offset, limit):
        category = self.env["southbrook.tool.category"].browse(
            category_id) if category_id else None
        if category is not None and not category.exists():
            # A bad category_id is a bad SELECTION, not a broken category
            # tree — the rail itself is still perfectly computable, so show
            # it rather than wiping the whole navigation panel over one
            # invalid id.
            return self._degrade("Unknown category: %s" % category_id, scope,
                                 categories=self._categories_best_effort())
        columns = self._columns(category)
        rows, total = self._rows(category, facets, search, offset, limit,
                                  columns)
        return {
            "ok": True,
            "reason": None,
            "scope": scope,
            "categories": self._categories(),
            "facets": self._facets(category),
            "columns": columns,
            "rows": rows,
            "detail": {},
            "total": total,
            "provenance": PROVENANCE,
        }

    @api.model
    def _categories_best_effort(self):
        """Best-effort category rail for a degraded payload.

        Overrides the base no-op: this provider DOES have a rail concept,
        so recompute it here. Called by get_catalog only after its
        savepoint has already rolled back whatever failed, so the cursor is
        healthy. If _categories() itself raises again, that means the rail
        computation is what's actually broken — degrade to an empty rail,
        same as the base default, rather than raising a second time out of
        an already-degraded response.
        """
        try:
            return self._categories()
        except Exception:
            _logger.exception("Category rail unavailable during degrade")
            return []

    @api.model
    def _categories(self):
        """Rail tree with descendant-inclusive product counts.

        Counts are computed with TWO efficient queries:
        - one _read_group over product.product grouped by product_tmpl_id
        - one batched read of templates to map category, joined in Python
        This counts variants (not templates) so the rail matches table rows,
        and stays O(1) queries instead of O(categories).
        """
        Category = self.env["southbrook.tool.category"]
        cats = Category.search([])
        if not cats:
            return []

        # Odoo 17+ grouping API: _read_group returns a list of tuples,
        # (group_value, *aggregates). The old public read_group is gone.
        # Count product.product (variants) grouped by template.
        variant_grouped = self.env["product.product"]._read_group(
            [("product_tmpl_id.x_southbrook_tool_category_id", "in", cats.ids)],
            groupby=["product_tmpl_id"],
            aggregates=["__count"],
        )
        # variant_grouped is list of (product.template, count) tuples.
        # Map each template to its variant count.
        tmpl_to_variant_count = {tmpl.id: count for tmpl, count in variant_grouped if tmpl}

        # Batch-read all templates once and map variant counts to categories.
        tmpl_ids = list(tmpl_to_variant_count.keys())
        templates = self.env["product.template"].browse(tmpl_ids) if tmpl_ids else []
        direct = {}
        for tmpl in templates:
            cat_id = tmpl.x_southbrook_tool_category_id.id if tmpl.x_southbrook_tool_category_id else None
            if cat_id:
                direct[cat_id] = direct.get(cat_id, 0) + tmpl_to_variant_count[tmpl.id]

        # Roll direct counts up through parent_path, so an ancestor reports
        # everything beneath it.
        rollup = dict.fromkeys(cats.ids, 0)
        for cat in cats:
            n = direct.get(cat.id, 0)
            if not n:
                continue
            for raw in (cat.parent_path or "").strip("/").split("/"):
                if raw and int(raw) in rollup:
                    rollup[int(raw)] += n

        child_ids = {c.parent_id.id for c in cats if c.parent_id}
        return [{
            "id": c.id,
            "name": c.name,
            "parent_id": c.parent_id.id or False,
            "count": rollup.get(c.id, 0),
            "has_children": c.id in child_ids,
        } for c in cats.sorted(lambda r: (r.complete_name or ""))]

    @api.model
    def _ancestor_ids(self, category):
        """Category's own id plus every ancestor id, nearest LAST.

        parent_path is '/1/7/33/' — root first, self last.
        """
        if not category:
            return []
        return [int(x) for x in (category.parent_path or "").strip("/").split("/") if x]

    @api.model
    def _resolve_declarations(self, model_name, category):
        """Ancestor-inclusive declarations for `category`, nearest wins.

        The module's central claim is that facets and columns are both
        declarations resolved by ONE consistent rule: gather every
        declaration on `category` and its ancestors, then for any
        field_name declared at more than one level, keep only the nearest
        one (the uniqueness constraint on materials.catalog.facet/column is
        scoped per category_id, so the same field_name can legitimately be
        declared at two levels — e.g. a broad ancestor default overridden
        by a more specific child label).

        `_columns` and `_facets` each shape their own output from the
        records this returns; `_facet_domain` also consults it to learn a
        facet's declared type. One shared resolver means the three call
        sites cannot silently diverge on which declaration wins.

        Returns an empty recordset of `model_name` when there is nothing to
        resolve (no category, or no ancestors, or nothing declared).
        """
        if not category:
            return self.env[model_name]
        ids = self._ancestor_ids(category)
        if not ids:
            return self.env[model_name]
        declared = self.env[model_name].search([("category_id", "in", ids)])
        if not declared:
            return declared
        # Nearest declaration wins: order by depth of its category in `ids`
        # (ancestors first, `category` itself last), then collapse into a
        # dict keyed on field_name — the LAST write for a given field_name
        # is therefore the nearest one. dict preserves the position of each
        # field_name's FIRST appearance, so output order still follows
        # ancestor-to-nearest for distinct fields.
        depth = {cat_id: i for i, cat_id in enumerate(ids)}
        by_field = {}
        for dec in declared.sorted(
                lambda d: (depth.get(d.category_id.id, -1), d.sequence, d.id)):
            by_field[dec.field_name] = dec
        return self.env[model_name].browse(
            [dec.id for dec in by_field.values()])

    @api.model
    def _columns(self, category):
        """Columns for the category, generics first, then declared ones.

        Declarations from the selected category and all its ancestors apply.
        When the same field_name is declared at two levels, the nearest
        declaration wins — a child's label overrides its ancestor's. The
        resolution itself lives in `_resolve_declarations`; this method only
        shapes the winning records into column dicts.
        """
        cols = list(GENERIC_COLUMNS)
        declared = self._resolve_declarations("materials.catalog.column", category)
        return cols + [{
            "key": dec.field_name,
            "label": dec.name,
            "align": dec.align,
            "sortable": dec.sortable,
        } for dec in declared]

    @api.model
    def _facets(self, category):
        """Facet chips for the category, declared via materials.catalog.facet.

        Declarations from the selected category and all its ancestors apply,
        resolved by the same nearest-wins rule `_columns` uses — see
        `_resolve_declarations`. The frontend keys chips on "key", so only
        the nearest declaration for a given field_name may reach the payload.

        Each facet's values are built with ONE grouped query (or one
        aggregate query for range), never a query per value.

        Counts are variant-scoped (product.product), to match the category
        rail and the table's `total` — EXCEPT range facets, which carry no
        per-value count at all, only a min/max. A numeric spec lives on
        product.template and is identical across every variant of that
        template, so a variant-scoped aggregate would report the same
        numbers as a template-scoped one; it is also not mechanically
        available in one query, because _read_group's aggregate spec
        (unlike its groupby spec) must name a field declared directly on
        the queried model — "product_tmpl_id.<field>" is rejected for
        aggregates even though the identical path is accepted for groupby.
        Range facets are left grouped against product.template deliberately;
        nothing here is silently inconsistent, there is simply no "count"
        for this shape to disagree about.
        """
        declared = self._resolve_declarations("materials.catalog.facet", category)
        if not declared:
            return []

        Tmpl = self.env["product.template"]
        Product = self.env["product.product"]
        tmpl_domain = [("x_southbrook_tool_category_id", "child_of", category.id)]
        variant_domain = [("product_tmpl_id.x_southbrook_tool_category_id",
                           "child_of", category.id)]
        out = []
        for dec in declared:
            field = Tmpl._fields.get(dec.field_name)
            if not field:
                continue
            entry = {"key": dec.field_name, "label": dec.name,
                     "type": dec.facet_type, "values": []}
            variant_field = "product_tmpl_id.%s" % dec.field_name
            if dec.facet_type == "range":
                # Aggregate in Postgres — a bare aggregate query (no GROUP
                # BY, since groupby=[]) always returns exactly one row, even
                # when zero records match. Confirmed against this build's
                # _read_group: NULL aggregates postprocess to False, which we
                # normalise to None — the "no data" sentinel must not be
                # confused with a legitimate all-zero dataset.
                #
                # No `!= False` leaf here: MIN/MAX skip NULL
                # natively in SQL, so the leaf was never needed for
                # correctness — and on a Float/Integer/Monetary field it
                # actively hurt: falsy_value=0 means the ORM rewrites
                # `!= False` into `NOT IN (0.0)`, which drops a row whose
                # value is a genuine 0 out of the aggregate too, quietly
                # raising the reported minimum.
                grouped = Tmpl._read_group(
                    tmpl_domain,
                    groupby=[],
                    aggregates=["%s:min" % dec.field_name,
                                "%s:max" % dec.field_name],
                )
                raw_min, raw_max = grouped[0] if grouped else (False, False)
                entry["min"] = raw_min if raw_min is not False else None
                entry["max"] = raw_max if raw_max is not False else None
            elif dec.facet_type == "flag":
                if field.type == "selection":
                    # The tri-state safety shape (and any other Selection
                    # field someone declares as a "flag" facet): one chip
                    # per declared selection value, not one chip for
                    # "true". A plain Boolean flag field falls through to
                    # the single-chip branch below unchanged — this branch
                    # only fires once the underlying column has actually
                    # been migrated to Selection (see coordination note on
                    # _selection_flag_values).
                    entry["values"] = self._selection_flag_values(
                        Product, variant_domain, variant_field, field)
                else:
                    entry["values"] = [{"value": True, "label": dec.name,
                                        "count": Product.search_count(
                                            variant_domain +
                                            [(variant_field, "=", True)])}]
            else:
                # Odoo 17+ API: list of (group_value, count) tuples.
                # Grouped over product.product via the product_tmpl_id.*
                # path — one query, variant-scoped counts.
                grouped = Product._read_group(
                    variant_domain + [(variant_field, "!=", False)],
                    groupby=[variant_field],
                    aggregates=["__count"],
                )
                raw = []
                for val, count in grouped:
                    if val is False or val is None:
                        continue
                    if hasattr(val, "id"):                 # m2o / m2m recordset
                        raw.append({"value": val.id, "label": val.display_name,
                                    "count": count})
                    else:
                        label = val
                        if field.type == "selection":
                            label = dict(
                                field._description_selection(self.env)
                            ).get(val, val)
                        raw.append({"value": val, "label": label,
                                    "count": count})
                # Relation-valued facets ("value" is a database id) must
                # sort alphabetically by label, never numerically — an id
                # is always numeric-parseable, so the numeric-aware ordering
                # would otherwise sort chips by arbitrary internal id order.
                entry["values"] = self._order_values(
                    raw, alpha=(dec.facet_type == "m2m"))
            out.append(entry)
        return out

    @api.model
    def _selection_flag_values(self, Product, variant_domain, variant_field,
                                field):
        """One chip per Selection value, counts always present — zeros
        included — for a facet declared `facet_type == "flag"` whose
        underlying field has been converted from Boolean to Selection.

        This is what makes a "flag" facet honest for the three-state safety
        fields: the old single-chip flag branch above only ever asked "how
        many are true", so "not assessed" (and "no") had no chip and no
        count at all — the exact "absence renders as good" failure this
        wave exists to close. Every value the field declares gets a chip
        here, in the field's own declared order (typically yes / no /
        not_assessed), each carrying its own count.

        `not_assessed`'s count treats a bare SQL NULL the same as the
        literal string "not_assessed": a Selection field's falsy value is
        already `False` (not a genuine third option), so a row that has
        never been migrated to the three-state shape, or was bulk-loaded
        without the field set, is NULL/empty in Postgres rather than
        holding the literal default — and it must still land in the
        "not assessed" bucket, not vanish from every chip's count.
        """
        values = []
        for value, label in field._description_selection(self.env):
            domain = variant_domain + [(variant_field, "=", value)]
            if value == "not_assessed":
                domain = variant_domain + [
                    "|", (variant_field, "=", "not_assessed"),
                    (variant_field, "=", False)]
            values.append({
                "value": value, "label": label,
                "count": Product.search_count(domain),
            })
        return values

    @api.model
    def _order_values(self, raw, alpha=False):
        """Order facet values for display.

        Numeric-aware by default: unparseable values keep their label and
        sort last (see _numeric_prefix). Pass alpha=True for relation-valued
        facets (m2m) — their "value" is a database id, which would otherwise
        parse as a number and sort by arbitrary internal id order instead of
        by label.
        """
        if alpha:
            return sorted(raw, key=lambda item: str(item["label"]))
        numbered, unnumbered = [], []
        for item in raw:
            n = self._numeric_prefix(item["value"])
            (numbered if n is not None else unnumbered).append((n, item))
        numbered.sort(key=lambda pair: pair[0])
        unnumbered.sort(key=lambda pair: str(pair[1]["label"]))
        return [item for _n, item in numbered] + [item for _n, item in unnumbered]

    @api.model
    def audit_facet_coverage(self):
        """Read-only data-quality audit: which declared facets have ANY
        data behind them, anywhere in this install.

        The taxonomy (southbrook.tool.category) and the facet declarations
        that ride on it are seeded ahead of real product data — that's the
        whole point of "declarations are data" (see the module README):
        adding a facet is a data change, not a code change, so it's cheap
        to declare one long before anyone has classified a product into it.
        Cheap declaration has a cost, though: nothing forces a declared
        facet to ever be backed by a real value, and the catalog UI itself
        stays silent about a facet that quietly renders zero chips forever.
        This method is the mechanism that makes that gap visible — see
        tests/test_facet_coverage.py for the ratchet that turns it into an
        actual, enforced check rather than a method nobody calls.

        Deliberately reuses `_facets(category)` — the exact function that
        builds the live payload — for every declaration's OWN category,
        rather than a second, parallel counting query. A facet's uniqueness
        constraint is scoped to (category_id, field_name), so a
        declaration is always the nearest-winning entry at its own
        category (nothing else can be declared at the identical pair) —
        `_facets(dec.category_id)` is therefore guaranteed to include an
        entry keyed on `dec.field_name`, short of the field having been
        removed from product.template entirely (the `matched is None`
        branch below covers that edge defensively; the mixin's own
        `_check_field_exists` constraint should already make it
        unreachable in practice).

        "populated" is deliberately generic, not safety-aware: a tri-state
        safety flag's `not_assessed` default is real, queryable data (a
        genuine SQL value on a genuine row) the moment ANY product exists
        in that category subtree, so such a facet reports populated as
        soon as data exists at all — whether or not anyone has actually
        assessed anything. That "has anyone looked yet" question is
        answered elsewhere (the badges in _build_detail, which never omit
        a not_assessed field); this audit only answers "does this
        DECLARATION have zero rows behind it anywhere", the same question
        for every facet type, safety or not.

        Returns a list of {key, category, field_name, populated,
        distinct_count} dicts, one per active facet declaration. `key` is
        "<category name>::<field_name>" — unique because the underlying DB
        constraint already guarantees that (category_id, field_name) pair
        is unique. Pure read: creates nothing, writes nothing.
        """
        out = []
        for dec in self.env["materials.catalog.facet"].search(
                [("active", "=", True)]):
            category = dec.category_id
            entries = self._facets(category)
            matched = next(
                (e for e in entries if e["key"] == dec.field_name), None)
            if matched is None:
                populated, distinct_count = False, 0
            elif matched["type"] == "range":
                populated = (matched.get("min") is not None
                             or matched.get("max") is not None)
                distinct_count = 2 if populated else 0
            else:
                values = matched.get("values") or []
                distinct_count = len(values)
                populated = any(v.get("count") for v in values)
            out.append({
                "key": "%s::%s" % (category.name, dec.field_name),
                "category": category.name,
                "field_name": dec.field_name,
                "populated": populated,
                "distinct_count": distinct_count,
            })
        return out

    @api.model
    def _facet_domain(self, facets, category=None):
        """Domain fragment for the selected facet values.

        Within one facet, selected values are OR'ed. Across facets, the
        fragments are AND'ed (Odoo domains are implicitly AND).

        Each facet's selection must be a `list` (a set of values to OR
        together) or a `dict` (a `{"min": ..., "max": ...}` range) — nothing
        else. A `str` is the classic trap: Python happily iterates it
        character-by-character, silently turning "cnc_router_bit" into an OR
        of 14 single-character comparisons that matches nothing, with no
        hint to the client that the shape was wrong. Any other non-list,
        non-dict value is equally malformed. Both are rejected here by
        raising — get_catalog's outer try/except turns that into an honest
        `{"ok": False, "reason": ...}` naming the offending key, rather than
        degrading silently to an empty-looking result.

        `category` (optional) is used only to look up each facet's DECLARED
        type via `_resolve_declarations`, to catch the inverse shape
        mismatch: a `list` submitted for a field declared `facet_type ==
        "range"`. That combination is rejected outright rather than
        guessing which list element is the min and which is the max —
        a `[lo, hi]` list is genuinely ambiguous (what does a single-element
        or three-element list mean?), whereas the `{"min":, "max":}` dict
        the range branch below already expects says exactly what it means.
        Callers that don't pass `category` (there is one: the domain-builder
        unit tests, which exercise `_facet_domain` directly without going
        through `get_catalog`) simply skip this check — the direct-call path
        has no category to resolve declarations against, and everything
        downstream still degrades safely if it disagrees with the facet's
        true type.
        """
        declared_types = {}
        if category:
            for dec in self._resolve_declarations(
                    "materials.catalog.facet", category):
                declared_types[dec.field_name] = dec.facet_type

        domain = []
        for field_name, selection in (facets or {}).items():
            if field_name not in self.env["product.template"]._fields:
                continue
            if not isinstance(selection, (list, dict)):
                raise ValueError(
                    "Malformed facet selection for %r: expected a list of "
                    "values or a {\"min\": ..., \"max\": ...} range dict, "
                    "got %s" % (field_name, type(selection).__name__))
            if isinstance(selection, dict):          # range
                low, high = selection.get("min"), selection.get("max")
                if low is None and high is None:
                    continue
                # On a nullable Float/Integer/Monetary column, Odoo's
                # domain-to-SQL layer ORs in "field IS NULL" for a `<=`/`>=`
                # leaf whenever the field's falsy value (0) itself satisfies
                # the comparison — e.g. `<= 50` matches unset rows too,
                # because 0 <= 50. A leaf whose bound doesn't reject that
                # (max-only, or a min <= 0) leaves the null-acceptance in
                # place for the whole AND'ed domain, so rows with NO value
                # recorded come back alongside genuine matches.
                null_would_match = ((low is None or low <= 0)
                                     and (high is None or high >= 0))
                if not null_would_match:
                    # A plain leaf already excludes NULL outright here (a
                    # positive ">=" fails a NULL column rather than OR-ing
                    # in IS NULL), so the simple leaves are already correct.
                    if low is not None:
                        domain.append((field_name, ">=", low))
                    if high is not None:
                        domain.append((field_name, "<=", high))
                    continue
                # Refuted compromise: the previous fix here used
                # `(field_name, "!=", False)` to force NULL exclusion, but
                # that compiles to `NOT IN (0.0)` (falsy_value=0 on these
                # field types), which also drops a row whose value really
                # is 0 — the same trap as the facet min/max aggregate in
                # _facets(). No plain domain leaf can express
                # "IS NOT NULL, but a genuine 0 still counts", because every
                # leaf gets rewritten by the same per-field falsy-value
                # machinery. Domain.custom(to_sql=...) bypasses that
                # rewriting entirely and emits the SQL directly — nested
                # under a relational "any" so it plugs into an ordinary
                # domain list without special-casing elsewhere (mirrors
                # Odoo core's own use of this pattern for a predicate plain
                # operators can't express: purchase.order._search_is_late,
                # addons/purchase/models/purchase_order.py:372-388).
                # `product_tmpl_id` is the many2one _rows already filters
                # product.product through (it prefixes every plain tuple
                # leaf with "product_tmpl_id.%s" for the same reason: the
                # spec field lives on product.template, not product.product,
                # and _rows's domain is built for the latter). A Domain
                # object is not a tuple, so it skips that prefixing and is
                # appended to _rows's domain as-is (see _rows) — it must
                # therefore name the relation itself. This only resolves
                # correctly against a model that has a product_tmpl_id
                # field (product.product); a direct product.template query
                # would need this same leaf unwrapped one level.
                def _to_sql(model, alias, query, fn=field_name, lo=low, hi=high):
                    sql_field = model._field_to_sql(alias, fn, query)
                    parts = [SQL("%s IS NOT NULL", sql_field)]
                    if lo is not None:
                        parts.append(SQL("%s >= %s", sql_field, lo))
                    if hi is not None:
                        parts.append(SQL("%s <= %s", sql_field, hi))
                    return SQL(" AND ").join(parts)
                domain.append(
                    Domain("product_tmpl_id", "any", Domain.custom(to_sql=_to_sql)))
                continue
            # selection is a list at this point. A list submitted against a
            # field DECLARED as a numeric range is the inverse malformed
            # shape (see docstring): reject it rather than guess at [lo, hi]
            # ordering. Fields with no known declaration (declared_types has
            # no entry — including every direct `_facet_domain` unit-test
            # call, which passes no `category`) fall through to the plain
            # enum/OR handling below unchanged.
            if declared_types.get(field_name) == "range":
                raise ValueError(
                    "Malformed facet selection for %r: this facet is a "
                    "numeric range and must be selected with a "
                    "{\"min\": ..., \"max\": ...} dict, not a list"
                    % field_name)
            values = [v for v in selection if v not in (None, "")]
            if not values:
                continue
            if len(values) == 1:
                domain.append((field_name, "=", values[0]))
            else:
                domain += ["|"] * (len(values) - 1)
                domain += [(field_name, "=", v) for v in values]
        return domain

    _LIKE_ESCAPE_TABLE = str.maketrans({
        "\\": "\\\\",
        "%": "\\%",
        "_": "\\_",
    })

    @api.model
    def _escape_like_term(self, term):
        """Escape a user search term for use inside an `ilike` domain leaf.

        Postgres's LIKE/ILIKE honours backslash as the escape character by
        default. `str.translate` rewrites each character once, in a single
        pass, so escaping the backslash itself first is safe — it cannot be
        re-escaped by the `%`/`_` substitutions that follow in the same
        table, because a translation table maps individual input characters
        to their replacement text independent of each other.
        """
        return term.translate(self._LIKE_ESCAPE_TABLE)

    @api.model
    def _synonym_category_ids(self, term):
        """Category ids a search term should also widen the row search to.

        Exact match, case-insensitive, on the trimmed term — "solvent" must
        not fuzzy-match "solvents" via ilike substring rules (that's a
        second, separately-seeded synonym record, not a wildcard). No
        `.sudo()`: this model's ACL mirrors materials.catalog.facet's
        (base.group_user read-only), so a caller who cannot see the
        category tree cannot see synonym mappings into it either — same
        boundary the rest of this provider already holds to.
        """
        needle = (term or "").strip()
        if not needle:
            return []
        synonyms = self.env["materials.catalog.search.synonym"].search(
            [("active", "=", True), ("term", "=ilike", needle)])
        return synonyms.category_ids.ids

    @api.model
    def _null_mask(self, tmpl_ids, keys):
        """Which (template id, key) pairs are SQL NULL, for numeric keys.

        read() cannot distinguish a NULL Float/Integer/Monetary column from
        a genuinely stored 0 — both come back as 0.0/0 (see
        NUMERIC_AMBIGUOUS_TYPES). Postgres CAN tell the difference, so this
        asks it directly, once per page (one query), never once per row.

        Returns {tmpl_id: {key: True-if-NULL}}. A key absent from the inner
        dict (or the whole tmpl_id absent) means "not null" — callers treat
        a missing entry as False, so this degrades safely to the old
        behaviour if ever called with nothing to check.

        `keys` is filtered down to fields that are real, stored, non-related,
        NUMERIC_AMBIGUOUS_TYPES columns on product.template before anything
        reaches SQL — every declared field name is validated against
        product.template._fields here, never trusted as-is, and the
        surviving names are still only ever used via SQL.identifier(), never
        interpolated into the query string. `tmpl_ids` are passed as a bound
        parameter (`= ANY(%s)`), never formatted into the SQL text.
        """
        if not tmpl_ids or not keys:
            return {}
        Tmpl = self.env["product.template"]
        safe_keys = [
            key for key in dict.fromkeys(keys)
            if (field := Tmpl._fields.get(key))
            and field.type in NUMERIC_AMBIGUOUS_TYPES
            and field.store
            and not field.related
        ]
        if not safe_keys:
            return {}
        null_columns = SQL(", ").join(
            SQL("%s IS NULL", SQL.identifier(key)) for key in safe_keys)
        query = SQL(
            "SELECT id, %s FROM product_template WHERE id = ANY(%s)",
            null_columns, list(tmpl_ids),
        )
        self.env.cr.execute(query)
        mask = {}
        for row in self.env.cr.fetchall():
            tmpl_id, flags = row[0], row[1:]
            mask[tmpl_id] = dict(zip(safe_keys, flags))
        return mask

    @api.model
    def _rows(self, category, facets, search, offset, limit, columns):
        """One row per variant.

        Spec fields live on product.template, so templates are read ONCE in
        a batch and joined in Python — never one read per row.

        Absence is type-aware, driven by the field's actual type
        (product.template._fields[key].type):
        - Char/Text/Selection/Html and relational (m2o/m2m/o2m) fields:
          Odoo's False/empty for "never set" is reported as None.
        - Boolean: False is a real value, not absence — kept as-is.
        - Date/Datetime: False means absent — reported as None.
        - Float/Integer/Monetary: kept as-is when genuinely set, reported
          as None when the underlying column is SQL NULL. read() itself
          returns 0/0.0 for both "never entered" and "entered as zero" on
          a numeric field — those two states are indistinguishable through
          the ORM alone — so one extra batched query (_null_mask) asks
          Postgres directly, where the distinction genuinely exists, and
          that answer is what actually decides None vs. 0 here.

        No category: an unscoped call must not fan out across every product
        on the instance. `_columns(None)` and `_facets(None)` already
        degrade safely to "nothing declared yet"; `_rows` mirrors that with
        an empty result rather than a scoped default — there is no
        principled "default" category to fall back to, and picking one
        would silently show a different, arbitrary subset instead of the
        rail's neutral "choose a category" state the frontend already
        renders when `categoryId` is null.
        """
        if not category:
            return [], 0
        Product = self.env["product.product"]
        Tmpl = self.env["product.template"]
        domain = [("product_tmpl_id.x_southbrook_tool_category_id",
                   "child_of", category.id)]
        for leaf in self._facet_domain(facets, category):
            domain.append(("product_tmpl_id.%s" % leaf[0], leaf[1], leaf[2])
                          if isinstance(leaf, tuple) else leaf)
        search = (search or "").strip()
        if search:
            # `ilike` reaches Postgres as a plain LIKE pattern: `%` and `_`
            # are wildcards there regardless of how the parameter got bound,
            # so a literal `_` in a part number (these are common — e.g.
            # "SBK-TOOL-C_B") would otherwise match any single character,
            # and a bare `%` or `_` search term would match every row. Escape
            # the escape character itself first, then the two wildcards, so
            # Postgres's own (default) backslash-escape convention makes the
            # match literal again.
            escaped = self._escape_like_term(search)
            name_leaf = ("name", "ilike", escaped)
            code_leaf = ("default_code", "ilike", escaped)
            # "Solvent" is deliberately not a taxonomy node (see the seed
            # tree's own README section on why) — it's the word a shop-floor
            # operator actually types. materials.catalog.search.synonym is
            # itself a declaration: a seeded row widens this one search into
            # an OR against the categories that already hold the answer,
            # rather than inventing a first-class category to name it. Only
            # the row search widens — the facet chips and the category rail
            # answer a different question ("what does THIS category
            # contain") and must not be perturbed by a synonym match.
            synonym_category_ids = self._synonym_category_ids(search)
            if synonym_category_ids:
                domain += ["|", name_leaf, "|", code_leaf,
                           ("product_tmpl_id.x_southbrook_tool_category_id",
                            "child_of", synonym_category_ids)]
            else:
                domain += ["|", name_leaf, code_leaf]

        total = Product.search_count(domain)
        products = Product.search(domain, offset=offset, limit=limit,
                                  order="default_code, name")
        if not products:
            return [], total

        spec_keys = [c["key"] for c in columns
                     if c["key"] not in ("name", "default_code")]
        tmpl_ids = products.mapped("product_tmpl_id").ids
        tmpl_data = {}
        if spec_keys:
            for rec in products.mapped("product_tmpl_id").read(spec_keys):
                tmpl_data[rec["id"]] = rec
        # One extra query for the whole page — never one per row — to learn
        # which numeric specs are SQL NULL vs. genuinely stored as 0.
        null_mask = self._null_mask(tmpl_ids, spec_keys)

        rows = []
        for product in products:
            tmpl_id = product.product_tmpl_id.id
            row = {
                "product_id": product.id,
                "name": product.name,
                "default_code": product.default_code or False,
            }
            specs = tmpl_data.get(tmpl_id, {})
            nulls = null_mask.get(tmpl_id, {})
            for key in spec_keys:
                row[key] = _normalize_value(
                    specs.get(key), Tmpl._fields.get(key),
                    is_null=nulls.get(key, False))
            rows.append(row)
        return rows, total

    @api.model
    def _build_detail(self, product_id):
        """Detail payload for one variant: spec grid, engineering rail,
        safety badges.

        The spec grid reuses the selected category's declared columns
        (same `_columns()` as the table), so the detail panel and the row
        it was opened from never disagree about which specs exist.

        Values share the absence rule with _rows() via _normalize_value —
        one read() batches both the spec keys and the engineering fields
        so a many2one comes back as Odoo's (id, display_name) tuple, from
        which we take the display name: the detail panel must never show
        a raw database id. They also share _rows()'s NULL-vs-zero mask
        (one extra query, same _null_mask helper) so a numeric spec can
        never render 0 here for a row that showed a dash, or vice versa.

        Access: get_catalog's scope check is incidental — it only happens
        because _build_payload always calls _categories(), which searches
        southbrook.tool.category (readable only by group_tool_operator and
        friends). _build_detail has no equivalent forced touch:
        _columns(category) short-circuits to the generic columns whenever
        `category` is falsy, so a product with NO
        x_southbrook_tool_category_id set never reaches that model at all,
        and a base.group_user-only account could otherwise call get_detail
        directly and read title/specs/badges/engineering with no gate.
        Enforce the same scope check unconditionally, before any data is
        assembled, rather than relying on it happening to fire.

        Scope: this is a TOOLS-catalog endpoint. A product with no tool
        category at all (its own `x_southbrook_tool_category_id` is unset)
        is out of scope even though it may still exist and be perfectly
        readable through other models — degrade rather than serve vendor,
        stock and lifecycle data for a product the tools catalog was never
        meant to describe. Without this check, ENGINEERING_FIELDS was read
        unconditionally regardless of category, so any product id in the
        database — not just tools — returned a fully populated engineering
        rail; only the (correctly empty) spec grid hinted anything was
        scoped at all.

        Archived: `browse()`/`exists()` both ignore the `active` flag, so
        an archived product still satisfies `exists()` even though
        get_catalog's own `search()`-based listing (which does respect
        `active`) would never surface it again. A stale link to a
        discontinued item must not keep working forever — treat "exists but
        archived" the same as "does not exist".
        """
        self.env["southbrook.tool.category"].browse().check_access("read")
        product = self.env["product.product"].browse(product_id)
        if not product.exists() or not product.active:
            return self._degrade_detail("Unknown product")
        tmpl = product.product_tmpl_id
        category = tmpl.x_southbrook_tool_category_id
        if not category:
            return self._degrade_detail(
                "Product is not in the tools catalog")
        columns = self._columns(category)

        spec_keys = [c["key"] for c in columns
                     if c["key"] not in ("name", "default_code")]
        engineering_keys = [f for f, _label in ENGINEERING_FIELDS
                            if f in tmpl._fields]
        read_keys = list(dict.fromkeys(
            k for k in spec_keys + engineering_keys if k in tmpl._fields))
        data = tmpl.read(read_keys)[0] if read_keys else {}
        # One extra query, same helper _rows() uses — one variant, so one
        # tmpl id, but the query shape (and cost) stays identical either way.
        nulls = self._null_mask([tmpl.id], read_keys).get(tmpl.id, {})

        specs = [{"label": col["label"],
                  "value": self._detail_value(tmpl, data, col["key"], nulls)}
                 for col in columns if col["key"] not in ("name", "default_code")]

        engineering = [{"label": label,
                        "value": self._detail_value(tmpl, data, field_name, nulls)}
                       for field_name, label in ENGINEERING_FIELDS
                       if field_name in tmpl._fields]

        badges = []
        for field_name, label in BADGE_FIELDS:
            if field_name not in tmpl._fields:
                continue
            field = tmpl._fields[field_name]
            if field_name in SAFETY_BADGE_FIELD_NAMES and field.type == "selection":
                badges.append(self._safety_badge(tmpl, field_name, label))
            elif tmpl[field_name]:
                # Non-safety badge fields (and a safety field not yet
                # migrated off Boolean — the defensive fallback for a -u
                # that hasn't converted both modules in one transaction)
                # keep the original omit-if-falsy behaviour.
                badges.append({"label": label, "kind": "danger"})

        return {
            "ok": True,
            "reason": None,
            "title": product.display_name,
            "subtitle": category.complete_name if category else "",
            "specs": specs,
            "engineering": engineering,
            "badges": badges,
        }

    @api.model
    def _safety_badge(self, tmpl, field_name, label):
        """One {label, kind} badge for a tri-state safety field.

        kind is server-driven — the client only maps kind to a CSS class,
        it never re-derives danger/ok/unset from the raw value. "yes" is
        the existing danger badge; "no" is a genuine, distinctly-labelled
        "ok" badge (a verified negative is information, not nothing);
        "not_assessed", *or a bare falsy value* (a column that hasn't
        actually been migrated to the selection shape yet — belt-and-
        braces, since BADGE_FIELDS already gates this branch on
        `field.type == "selection"`), renders "unset" and never omits.
        """
        value = tmpl[field_name]
        if value == "yes":
            return {"label": label, "kind": "danger"}
        if value == "no":
            return {"label": SAFETY_BADGE_NEGATIVE_LABELS[field_name],
                    "kind": "ok"}
        return {"label": "%s: not assessed" % label, "kind": "unset"}

    @api.model
    def _detail_value(self, tmpl, data, key, nulls=None):
        """One field's value, read()-shaped, normalized, relation-resolved.

        `data` came from a batched tmpl.read(); a many2one there is Odoo's
        (id, display_name) tuple. Tuple-unwrapping and the absence rule
        both live in _normalize_value — the same helper _rows() uses for
        its cells — so a relation column can never render
        differently in the table than in this detail panel.

        `nulls` is this template's slice of _null_mask() — {key: True} for
        every numeric column that is genuinely SQL NULL. Defaults to {} so
        an unfiltered caller (there is none left, but the default keeps this
        method safe to call standalone) falls back to "nothing known null".
        """
        field = tmpl._fields.get(key)
        raw = data.get(key) if field else False
        is_null = (nulls or {}).get(key, False)
        return _normalize_value(raw, field, is_null=is_null)
