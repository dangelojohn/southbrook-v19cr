# SPDX-License-Identifier: LGPL-3.0-only
"""Post-migrate 19.0.2.0.0 — safety three-state remediation, step 2 of 2.

Two independent, standalone-testable jobs, both idempotent:

1. ``_backfill_safety_three_state`` — reads the ``*_bool_legacy``
   columns pre-migrate.py (same version dir) renamed the original
   boolean columns to, writes the equivalent three-state value into
   the new Selection columns the ORM upgrade just created
   (True -> 'yes', False/NULL -> 'not_assessed'), then drops the
   legacy columns. Prod reality per the recon: 0 of 263 products have
   any of the four safety flags set True, so every row lands on
   'not_assessed' — this migration does not silently invent an
   assessment nobody performed, it just gives the existing "nobody
   looked" state an honest name instead of leaving it as an
   indistinguishable False.

2. ``_classify_chemical_products`` — the recon separately found four
   real chemical products shipped with x_southbrook_tool_category_id
   = NULL (ids 198/199/201/202 in prod, all $1.00 placeholder
   products: "PUR Glue", "EVA Glue Cartridge", "Cabinet Paint White",
   "Spray Booth Filter"). Uncategorized chemical products are exactly
   what falls through both this migration's own release-gate
   constraint (product_template.py
   ``_check_safety_assessed_before_release`` only fires for products
   INSIDE the cat_adhesives/cat_finishing subtree — no category means
   no gate) and the sb_materials_hardware catalog's facets (which key
   off the same tool category). This function gives each of the four
   its correct category so the rest of the remediation actually
   applies to them. Classification mapping chosen (read every
   relevant seed node's exact name before picking, per contract —
   never approximate a leaf):

   - "PUR Glue" -> cat_glue_pur ("PUR Glue", tool_categories_seed.xml
     ~line 529). Exact family-leaf match; NOT cat_glue_pur_hot ("PUR
     Hot Melt Glue") — a cold-applied PUR glue is not a hot-melt
     product, so that leaf would be a wrong-family approximation.

   - "EVA Glue Cartridge" -> cat_adhesives (the ROOT, not a leaf). The
     only EVA leaf seeded is cat_glue_eva, whose actual seeded *name*
     is "EVA Hot Melt Glue Pellets" — pellet form factor. A cartridge
     is a different physical form (bulk/stick cartridge, not loose
     pellets) and classifying it onto the pellet-specific leaf would
     be exactly the kind of leaf-approximation the contract forbids.
     Falling back to the cat_adhesives root is honest about what is
     actually known ("it's an adhesive") without asserting a form
     factor the product name does not support.

   - "Cabinet Paint White" -> cat_paint_lacquer ("Lacquer / Paint /
     Primer", tool_categories_seed.xml ~line 704). Exact match — paint
     is literally in both the product name and the category name, and
     this is the only paint-family leaf under cat_finishing.

   - "Spray Booth Filter" -> cat_paint_booth_filter ("Booth Filters",
     tool_categories_seed.xml ~line 721, under cat_finishing). The
     contract's own fallback instruction is "cat_finishing root UNLESS
     a booth/filter leaf exists in the seed tree" — one does exist
     (cat_paint_booth_filter), so the leaf is used, not the root.

Split into standalone functions (not inlined in ``migrate``) so tests
can import this module directly via importlib (Odoo migration scripts
aren't a regular importable package — see tests/test_safety_three_state.py)
and call each function twice to prove idempotency, following the
southbrook_premium_orchestration 19.0.5.0.0/post-migrate.py precedent.
"""
import logging

_logger = logging.getLogger(__name__)

SAFETY_FIELDS = (
    "x_southbrook_hazardous",
    "x_southbrook_flammable",
    "x_southbrook_requires_ventilation",
    "x_southbrook_msds_required",
)

TABLE = "product_template"

# Exact product name -> target southbrook.tool.category xml_id. See the
# module docstring above for the reasoning behind each mapping.
CHEMICAL_PRODUCT_CLASSIFICATION = {
    "PUR Glue": "southbrook_mrp_kitchen_tools.cat_glue_pur",
    "EVA Glue Cartridge": "southbrook_mrp_kitchen_tools.cat_adhesives",
    "Cabinet Paint White": "southbrook_mrp_kitchen_tools.cat_paint_lacquer",
    "Spray Booth Filter": "southbrook_mrp_kitchen_tools.cat_paint_booth_filter",
}


def _backfill_safety_three_state(env):
    """Move each safety field's data out of its ``*_bool_legacy``
    column (pre-migrate.py) into the new Selection column, then drop
    the legacy column. Returns
    ``{field_name: {"yes": n, "not_assessed": n}}`` for fields
    actually processed, or ``{field_name: {"skipped": True}}`` for
    fields whose legacy column no longer exists (already processed by
    a prior run, or a fresh install that never had a boolean column —
    both cases are idempotent no-ops that leave the current Selection
    value untouched rather than stomping it back to a default)."""
    cr = env.cr
    counts = {}
    for field_name in SAFETY_FIELDS:
        legacy_name = f"{field_name}_bool_legacy"
        cr.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            (TABLE, legacy_name),
        )
        if not cr.fetchone():
            counts[field_name] = {"skipped": True}
            continue

        cr.execute(
            f'UPDATE "{TABLE}" SET "{field_name}" = \'yes\' '
            f'WHERE "{legacy_name}" IS TRUE'
        )
        yes_count = cr.rowcount
        cr.execute(
            f'UPDATE "{TABLE}" SET "{field_name}" = \'not_assessed\' '
            f'WHERE "{legacy_name}" IS NOT TRUE'
        )
        not_assessed_count = cr.rowcount
        cr.execute(f'ALTER TABLE "{TABLE}" DROP COLUMN "{legacy_name}"')

        counts[field_name] = {
            "yes": yes_count, "not_assessed": not_assessed_count,
        }
        _logger.info(
            "safety-three-state post-migrate: %s -> yes=%s "
            "not_assessed=%s (legacy column dropped).",
            field_name, yes_count, not_assessed_count)

    return counts


def _classify_chemical_products(env):
    """Set x_southbrook_tool_category_id on the four known
    uncategorized chemical products, matching by EXACT product name
    AND x_southbrook_tool_category_id IS NULL — idempotent (a second
    run finds nothing left to match) and non-destructive (never
    overwrites a category an owner has since set by hand, even a
    'wrong' one; this migration classifies gaps, it does not correct
    human decisions). Returns {name: xml_id} for products actually
    touched this run."""
    Product = env["product.template"]
    touched = {}
    for name, xml_id in CHEMICAL_PRODUCT_CLASSIFICATION.items():
        category = env.ref(xml_id, raise_if_not_found=False)
        if not category:
            _logger.warning(
                "safety-three-state post-migrate: category %s not "
                "found — skipping classification of %r.", xml_id, name)
            continue
        products = Product.search([
            ("name", "=", name),
            ("x_southbrook_tool_category_id", "=", False),
        ])
        if not products:
            continue
        products.write({"x_southbrook_tool_category_id": category.id})
        touched[name] = xml_id
        _logger.info(
            "safety-three-state post-migrate: classified %s "
            "product(s) named %r -> %s.", len(products), name, xml_id)
    return touched


def migrate(cr, version):
    if not version:
        return

    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    _backfill_safety_three_state(env)
    _classify_chemical_products(env)
