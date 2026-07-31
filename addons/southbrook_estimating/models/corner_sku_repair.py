# SPDX-License-Identifier: LGPL-3.0-only
"""T4a (kitchen templates) — SB-CORNER catalog data repair.

Ground truth (live prod, 2026-07-27 — .superpowers/sdd/t4a-groundtruth.md):
the Q8 corner template is a 36"x36"x34.5" HIGHLINE corner base
(archetype CC-CHL), but its single live variant carries Width='33 in'
(the outlier — 3 of 4 data points say 36) and only an LH hand, which
blocked every L/U kitchen template from shipping. The Width and Hinge
Side attribute LINES already allow '36 in' and 'RH (Right Hand)' — this
is variant-level data repair, not schema work.

Called from the 19.0.9.1.0 post-migration AND from tests (which first
recreate the known-bad prod state on a bare DB). Idempotent: every step
is guarded to the known-bad state, so a later hand edit is never
clobbered and re-running is a no-op.
"""
import logging

_logger = logging.getLogger(__name__)


def repair_corner_sku(env):
    """Repair SB-CORNER variants. Returns a summary dict.

    1. Any variant whose Width PTAV is '33 in' is relinked to '36 in'.
    2. LH variants with no default_code get 'SB-CORNER' backfilled.
    3. An RH twin (default_code 'SB-CORNER-R') is created when an LH
       variant exists and no RH variant does — enabling right-facing
       L/U layouts (the corner engine picks the variant by the node's
       handedness).
    4. The WALL-layer corner SKU (SB-WALL-CORNER) is seeded if absent —
       the arrange engine substitutes it for junction uppers, and with
       no product it silently ARCHIVES those uppers and inserts nothing
       (catalog blocker #1: "no wall-layer equivalent"). Name/price are
       John's (2026-07-27): "Wall Corner 24" @ $345.
    """
    summary = {"width_fixed": 0, "codes_set": 0, "rh_created": False,
               "wall_corner_created": False}
    Template = env["product.template"]
    if not Template.search(
            [("default_code", "=", "SB-WALL-CORNER")], limit=1):
        vals = {
            "name": "Wall Corner 24",
            "default_code": "SB-WALL-CORNER",
            "type": "consu",
            "list_price": 345.0,
        }
        if "southbrook_cabinet_type" in Template._fields:
            # Classification fields belong to the 3D configurator addon
            # (installed on prod; guarded for estimating-only DBs).
            vals.update({
                "southbrook_cabinet_type": "corner",
                "southbrook_width_in": 24.0,
                "southbrook_height_in": 30.0,
                "southbrook_depth_in": 12.0,
            })
        Template.create(vals)
        summary["wall_corner_created"] = True
    tmpl = env.ref("southbrook_estimating.corner", raise_if_not_found=False)
    if not tmpl:
        return summary
    PTAV = env["product.template.attribute.value"]

    def _ptav(attr_name, prefix):
        return PTAV.search([
            ("product_tmpl_id", "=", tmpl.id),
            ("attribute_id.name", "=", attr_name),
            ("name", "=like", prefix + "%"),
        ], limit=1)

    p33 = _ptav("Width", "33")
    p36 = _ptav("Width", "36")
    lh = _ptav("Hinge Side", "LH")
    rh = _ptav("Hinge Side", "RH")

    if p33 and p36:
        bad = tmpl.product_variant_ids.filtered(
            lambda v: p33 in v.product_template_attribute_value_ids)
        for variant in bad:
            variant.write({"product_template_attribute_value_ids": [
                (3, p33.id), (4, p36.id)]})
            summary["width_fixed"] += 1

    if lh and rh:
        variants = tmpl.product_variant_ids
        lh_variants = variants.filtered(
            lambda v: lh in v.product_template_attribute_value_ids)
        rh_variants = variants.filtered(
            lambda v: rh in v.product_template_attribute_value_ids)
        for variant in lh_variants.filtered(lambda v: not v.default_code):
            variant.default_code = "SB-CORNER"
            summary["codes_set"] += 1
        if lh_variants and not rh_variants:
            combo = (lh_variants[0].product_template_attribute_value_ids
                     - lh) | rh
            env["product.product"].create({
                "product_tmpl_id": tmpl.id,
                "product_template_attribute_value_ids": [(6, 0, combo.ids)],
                "default_code": "SB-CORNER-R",
            })
            summary["rh_created"] = True

    if any(v for v in summary.values()):
        _logger.info("[T4a corner repair] %s", summary)

    # T4 (corner catalog truth, .superpowers/sdd/corner-remediation-
    # 2026-07-30/task-4-brief.md) rides the SAME entry point as the T4a
    # repair above (this function is the one thing both the
    # 19.0.9.1.0 post-migrate script and every test import) rather than
    # adding a second call site anywhere. Its own summary is logged
    # independently and NOT merged into `summary` above: callers that
    # assert the exact shape of `summary` (e.g.
    # southbrook_kitchen_3d_configurator/tests/test_corner_repair.py::
    # test_repair_is_idempotent) must keep passing unchanged.
    repair_corner_family_skus(env)
    return summary


# ---------------------------------------------------------------------
# T4 — corner catalog truth: promote SB-CORNER-BLIND / SB-CORNER-DIAG /
# SB-WALL-CORNER to real, panel-visible, hand-aware SKUs; rename the
# SB-CORNER template to reflect what it actually is (the operative
# lazy-susan carcass); archive the SB-CORNER-LSUSAN duplicate.
#
# Ground truth (verified against a live-catalog DB clone, 2026-07-30):
# all three templates already carry Width and Hinge Side attribute
# LINES (config_ok=True, create_variant='dynamic' on both — the Q6
# hybrid model), so — same as T4a — this is variant/template DATA
# repair, not schema work. SB-CORNER-BLIND / SB-CORNER-DIAG carry ZERO
# materialized product.product variants at all (the "config-enabled
# products can report zero variants" trap: dynamic create_variant
# means nothing is auto-generated). SB-WALL-CORNER already has exactly
# one product.product bearing its default_code, but with an EMPTY
# attribute-value combination (no Width/Hinge Side linked yet) — that
# placeholder is reused/backfilled in place rather than duplicated.
#
# "45 in" does not exist anywhere in the shared Width attribute's value
# vocabulary (checked: 9/12/15/18/21/24/27/30/33/36 only) even though
# SB-CORNER-BLIND is a real 45"-wide blind-corner base — the vocabulary
# gap is filled here (new `product.attribute.value`, linked onto just
# this one template's Width line) rather than skipped, since "45 in" is
# a real, correct width for this real SKU, not a substitution.
# ---------------------------------------------------------------------

# (default_code, dim flags, stale Width prefix, target Width prefix,
#  new Width attribute-value name to create if the target isn't found
#  in the shared vocabulary yet — None when the target already exists).
_CORNER_FAMILY_SPECS = [
    ("SB-CORNER-BLIND",
     {"southbrook_cabinet_type": "corner", "southbrook_is_cabinet": True,
      "southbrook_width_in": 45.0, "southbrook_height_in": 34.5,
      "southbrook_depth_in": 24.0},
     "33", "45", "45 in"),
    ("SB-CORNER-DIAG",
     {"southbrook_cabinet_type": "corner", "southbrook_is_cabinet": True,
      "southbrook_width_in": 36.0, "southbrook_height_in": 34.5,
      "southbrook_depth_in": 36.0},
     "33", "36", None),
    # Height 34.5 -> 30 (base height on what is physically a wall
    # cabinet) and cabinet_type -> 'corner' (not 'wall') are both
    # deliberate per the brief: the Corner panel filter is
    # southbrook_cabinet_type='corner' AND southbrook_is_cabinet=True,
    # and SB-WALL-CORNER is one of the four SKUs that filter must
    # match. "33 in" is not a Width option on this template at all
    # (its vocabulary is 24/27 in, not 33/36 in) — the stale-width
    # relink below is a genuine no-op here by design, kept only so a
    # future catalog state that DOES carry a stray 33in link still
    # self-heals.
    ("SB-WALL-CORNER",
     {"southbrook_cabinet_type": "corner", "southbrook_is_cabinet": True,
      "southbrook_width_in": 24.0, "southbrook_height_in": 30.0,
      "southbrook_depth_in": 24.0},
     "33", "24", None),
]


def repair_corner_family_skus(env):
    """Promote SB-CORNER-BLIND / SB-CORNER-DIAG / SB-WALL-CORNER to real
    SKUs, rename SB-CORNER, archive SB-CORNER-LSUSAN. Returns a summary
    dict. Every step is by default_code lookup, skip-if-missing,
    resolve-then-verify — never substitutes a different product family,
    and never overwrites a value that doesn't match the known-bad state
    it's guarded against, so re-running is always a no-op.
    """
    summary = {
        "flags_set": 0, "width_values_created": 0, "width_fixed": 0,
        "codes_set": 0, "lh_created": 0, "lh_combo_backfilled": 0,
        "rh_created": 0, "renamed": False, "lsusan_archived": False,
    }
    Template = env["product.template"]
    Product = env["product.product"]
    AttrValue = env["product.attribute.value"]
    PTAV = env["product.template.attribute.value"]

    def _ptav(tmpl, attr_name, prefix):
        return PTAV.search([
            ("product_tmpl_id", "=", tmpl.id),
            ("attribute_id.name", "=", attr_name),
            ("name", "=like", prefix + "%"),
        ], limit=1)

    def _resolve_tmpl(default_code):
        # Odoo trap (already documented in kitchen_design.py's own
        # auto-arrange corner insertion, T4a comment): product.template
        # .default_code is a STORED COMPUTE that only mirrors the
        # single variant's code when the template has EXACTLY ONE
        # variant (odoo/addons/product/models/product_template.py::
        # _compute_template_field_from_variant_field) — it reads back
        # False once a second (e.g. the RH twin) is created. A
        # template-level search is therefore only safe BEFORE this
        # repair materializes the twin; every later run must resolve
        # via the still-unique VARIANT default_code first.
        variant = Product.search([("default_code", "=", default_code)],
                                  limit=1)
        if variant:
            return variant.product_tmpl_id
        return Template.search([("default_code", "=", default_code)],
                                limit=1)

    has_cabinet_fields = "southbrook_cabinet_type" in Template._fields
    # Odoo module-load-order trap (verified 2026-07-30 against a live
    # clone, load-order log confirmed): southbrook_kitchen_3d_
    # configurator — which OWNS southbrook_cabinet_type/is_cabinet/
    # width_in/height_in/depth_in via `_inherit = "product.template"`
    # — loads strictly AFTER southbrook_estimating in the module graph
    # (independent modules, alphabetical-ish tiebreak; neither depends
    # on the other, so this ordering is deterministic, not incidental).
    # This repair is invoked from southbrook_estimating's OWN 19.0.9.1.0
    # migration, which therefore runs BEFORE kitchen_3d_configurator's
    # Python has registered those fields — `has_cabinet_fields` is
    # False at that exact moment even though the fields (and their
    # already-created DB columns, from kitchen_3d_configurator's own
    # prior install) genuinely exist. Neither `post_init_hook` (fires
    # on -i only, never -u — confirmed against this Odoo version's
    # odoo/modules/loading.py) nor a later migration/data-function
    # (same early graph position) can close this gap from inside
    # southbrook_estimating without inverting the module dependency
    # (out of scope). Falling back to a raw, column-existence-checked,
    # value-guarded UPDATE — instead of the ORM write — lets the SAME
    # repair land the classification flags in the SAME migration pass
    # regardless of which side of this ordering it happens to run on;
    # the ORM path above (when available, e.g. every test bootstrap,
    # which runs after the full registry loads) stays the primary path.
    cabinet_columns = set()
    if not has_cabinet_fields:
        env.cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'product_template'
            AND column_name IN ('southbrook_cabinet_type',
                'southbrook_is_cabinet', 'southbrook_width_in',
                'southbrook_height_in', 'southbrook_depth_in')
        """)
        cabinet_columns = {row[0] for row in env.cr.fetchall()}

    def _write_cabinet_flags(tmpl, flags):
        if has_cabinet_fields:
            diff = {k: v for k, v in flags.items() if tmpl[k] != v}
            if diff:
                tmpl.write(diff)
                return True
            return False
        if not cabinet_columns.issuperset(flags.keys()):
            return False
        cols = list(flags.keys())
        set_clause = ", ".join("%s = %%s" % c for c in cols)
        guard_clause = " OR ".join(
            "%s IS DISTINCT FROM %%s" % c for c in cols)
        params = [flags[c] for c in cols]
        env.cr.execute(
            "UPDATE product_template SET %s WHERE id = %%s AND (%s)"
            % (set_clause, guard_clause),
            params + [tmpl.id] + params,
        )
        # No env-level cache invalidation: these fields aren't part of
        # the model's _fields in THIS registry generation (that's the
        # whole reason for this fallback), so nothing in this process
        # has — or could have — a cached value for them yet.
        return bool(env.cr.rowcount)

    for default_code, flags, stale_prefix, target_prefix, new_value_name \
            in _CORNER_FAMILY_SPECS:
        tmpl = _resolve_tmpl(default_code)
        if not tmpl:
            continue

        if _write_cabinet_flags(tmpl, flags):
            summary["flags_set"] += 1

        width_line = tmpl.attribute_line_ids.filtered(
            lambda l: l.attribute_id.name == "Width")[:1]
        if new_value_name and width_line:
            attr = width_line.attribute_id
            pav = AttrValue.search([
                ("attribute_id", "=", attr.id),
                ("name", "=", new_value_name),
            ], limit=1)
            if not pav:
                pav = AttrValue.create({
                    "attribute_id": attr.id, "name": new_value_name,
                })
                summary["width_values_created"] += 1
            if pav.id not in width_line.value_ids.ids:
                width_line.write({"value_ids": [(4, pav.id)]})

        target_ptav = _ptav(tmpl, "Width", target_prefix)
        stale_ptav = _ptav(tmpl, "Width", stale_prefix)
        lh = _ptav(tmpl, "Hinge Side", "LH")
        rh = _ptav(tmpl, "Hinge Side", "RH")

        if stale_ptav and target_ptav:
            bad = tmpl.product_variant_ids.filtered(
                lambda v: stale_ptav in v.product_template_attribute_value_ids)
            for variant in bad:
                variant.write({"product_template_attribute_value_ids": [
                    (3, stale_ptav.id), (4, target_ptav.id)]})
                summary["width_fixed"] += 1

        if not (lh and rh and target_ptav):
            continue

        variants = tmpl.product_variant_ids
        lh_variants = variants.filtered(
            lambda v: lh in v.product_template_attribute_value_ids)
        rh_variants = variants.filtered(
            lambda v: rh in v.product_template_attribute_value_ids)

        for variant in lh_variants.filtered(lambda v: not v.default_code):
            variant.default_code = default_code
            summary["codes_set"] += 1

        if not lh_variants and not rh_variants:
            combo = target_ptav | lh
            existing = Product.search([
                ("product_tmpl_id", "=", tmpl.id),
                ("default_code", "=", default_code),
            ], limit=1)
            if existing and not existing.product_template_attribute_value_ids:
                # The "zero variants" trap's other face: a variant
                # already carries this default_code (e.g. a prior
                # bare-vals repair) but has no attribute combination at
                # all yet. Backfill it in place instead of creating a
                # duplicate default_code.
                existing.write({"product_template_attribute_value_ids":
                                 [(6, 0, combo.ids)]})
                lh_variants = existing
                summary["lh_combo_backfilled"] += 1
            elif not existing:
                lh_variants = Product.create({
                    "product_tmpl_id": tmpl.id,
                    "product_template_attribute_value_ids":
                        [(6, 0, combo.ids)],
                    "default_code": default_code,
                })
                summary["lh_created"] += 1
            else:
                # A variant with this default_code exists AND already
                # carries a (different, non-empty) combination — never
                # substitute; leave it for a human to reconcile.
                _logger.warning(
                    "[T4 corner family repair] %s: existing variant "
                    "id=%s has this default_code but an unrecognised "
                    "attribute combination — skipped, not overwritten.",
                    default_code, existing.id)

        if lh_variants and not rh_variants:
            combo = (lh_variants[0].product_template_attribute_value_ids
                     - lh) | rh
            Product.create({
                "product_tmpl_id": tmpl.id,
                "product_template_attribute_value_ids": [(6, 0, combo.ids)],
                "default_code": default_code + "-R",
            })
            summary["rh_created"] += 1

    # Item 4 — SB-CORNER IS the operative diagonal-lazy-susan carcass
    # (southbrook.placement.rule's diagonal-corner-lazy-susan rule
    # already points its product_default_code at it) but is still
    # named "Corner Cabinet". default_code is untouched (it has live
    # sale.order.line references) — name only.
    corner_tmpl = _resolve_tmpl("SB-CORNER")
    if corner_tmpl and corner_tmpl.name == "Corner Cabinet":
        corner_tmpl.name = "Corner Base · Lazy-Susan 36"
        summary["renamed"] = True

    # Item 5 — SB-CORNER-LSUSAN duplicates SB-CORNER (same real-world
    # cabinet, now named accordingly by the rename above) and has zero
    # design/SO lines, so archiving is a pure data-quality fix: two
    # active "lazy susan" rows would double-list in the drag panel.
    # Archive EVERY active record with this default_code, not just one:
    # southbrook_configurator_ux's catalog_expansion.build_catalog()
    # (a sibling module, out of this file's scope) re-seeds its own
    # "does this SKU already exist" check with a plain (active-only)
    # search, so it can silently mint a fresh active duplicate on the
    # very next module load after this archives the original — this
    # loop self-heals that on every subsequent call instead of leaving
    # a second active row behind.
    lsusan_active = Template.search(
        [("default_code", "=", "SB-CORNER-LSUSAN")])
    for dup in lsusan_active:
        _logger.info(
            "[T4 corner family repair] archiving SB-CORNER-LSUSAN "
            "(tmpl id=%s) — duplicate of SB-CORNER, zero design/SO "
            "lines referencing it.", dup.id)
        dup.active = False
        summary["lsusan_archived"] = True

    if any(summary.values()):
        _logger.info("[T4 corner family repair] %s", summary)
    return summary
