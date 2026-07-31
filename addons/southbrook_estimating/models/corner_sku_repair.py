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
import re

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
    Product = env["product.product"]
    # Deploy-gate hotfix (2026-07-31, same SBK-037-class bug as Task 23's
    # _resolve_template_sku) — repair_corner_family_skus() below gives
    # SB-WALL-CORNER a materialized RH twin, at which point its
    # TEMPLATE-level default_code correctly blanks (Odoo only mirrors a
    # single variant onto the template field). This function gets
    # called TWICE in the same upgrade run (19.0.9.1.0's migration, then
    # 19.0.9.2.0's, each with its own fresh env) — the FIRST call still
    # sees a non-blank template-level code (twin not created yet) and
    # correctly finds it; the SECOND call, re-entering AFTER the first
    # call's twin-creation has been flushed to DB, saw nothing via a
    # template-only, active-only search and minted a SECOND, single-
    # variant, attribute-less "SB-WALL-CORNER" template every such pass
    # — the deploy-gate-caught duplicate. Resolve via the still-unique
    # VARIANT default_code first (T4a's own LH variant always carries
    # it, blanking never touches product.product), falling back to the
    # template-level field only when no variant resolves at all (a
    # genuinely bare DB).
    wall_corner_variant = Product.search(
        [("default_code", "=", "SB-WALL-CORNER")], limit=1)
    wall_corner_tmpl = (
        wall_corner_variant.product_tmpl_id if wall_corner_variant else
        Template.search([("default_code", "=", "SB-WALL-CORNER")], limit=1))
    if not wall_corner_tmpl:
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

    # Deploy-gate hotfix (2026-07-31) — cleanup pass for any catalog-
    # family duplicate TEMPLATES a prior run of this same repair chain
    # (or catalog_expansion.build_catalog()) already minted before the
    # variant-first fixes above/in catalog_expansion.py landed. Runs
    # AFTER repair_corner_family_skus so a real template's RH twin
    # already exists to identify it by, and BEFORE the generic dedup
    # below (whose corner-family exclusion means it would otherwise
    # never notice or fix this class of duplicate at all).
    archive_catalog_family_duplicate_templates(env)

    # Task 23 (SBK-037, .superpowers/sdd/corner-remediation-2026-07-30/
    # task-23-brief.md) — same pattern again: its own independent
    # summary, called last so it sees the codes T4/T4a just settled
    # (SB-CORNER-BLIND/-DIAG/-WALL-CORNER's LH/RH twins, etc.) rather
    # than racing them.
    repair_cabinet_variant_refs(env)
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

# Task 23 (SBK-037) code-review finding 2 — the corner family's OWN
# default_code convention ("SB-CORNER" / "SB-CORNER-R" for the LH/RH
# hand pair, same for -BLIND/-DIAG/-WALL-CORNER) is owned entirely by
# T4a's repair_corner_sku() and T4's repair_corner_family_skus() above
# — repair_cabinet_variant_refs() below must never re-derive or
# rewrite it with its own generic Width-suffix convention. Today the
# corner repairs always run FIRST (repair_corner_sku calls them, then
# repair_cabinet_variant_refs, in that order) and leave every corner
# variant uniquely coded, so the generic dedup naturally never finds a
# duplicate/empty corner ref to touch — but that safety is incidental
# to call ORDER, not guaranteed by anything structural. This explicit
# exclusion set makes it structural: repair_cabinet_variant_refs()
# skips any row whose OWN code (bare or the corner family's own "-R"
# hand-twin suffix) or whose TEMPLATE's raw default_code column is one
# of these, full stop, regardless of call order or of whether a future
# bug ever leaves a corner variant duplicated or empty.
_CORNER_FAMILY_BASE_CODES = frozenset(
    {"SB-CORNER"} | {spec[0] for spec in _CORNER_FAMILY_SPECS})


def _is_corner_family_code(code):
    """True for a bare corner-family code (e.g. "SB-CORNER",
    "SB-WALL-CORNER") or its "-R" hand-twin (e.g. "SB-CORNER-R")."""
    if not code:
        return False
    if code in _CORNER_FAMILY_BASE_CODES:
        return True
    return code.endswith("-R") and code[:-2] in _CORNER_FAMILY_BASE_CODES


_CABINET_FLAG_COLUMNS = (
    "southbrook_cabinet_type", "southbrook_is_cabinet",
    "southbrook_width_in", "southbrook_height_in", "southbrook_depth_in",
)


def _cabinet_flag_columns(cr):
    """Which of the classification-flag columns actually exist on
    product_template in the DATABASE right now (independent of whether
    the ORM's Template._fields has registered them yet in THIS process
    — see repair_corner_family_skus's module-load-order comment).
    """
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'product_template'
        AND column_name IN %s
    """, (_CABINET_FLAG_COLUMNS,))
    return {row[0] for row in cr.fetchall()}


def _write_cabinet_flags_sql(env, tmpl, flags, columns=None):
    """Write classification flags via raw SQL, bypassing the ORM field
    registry entirely. This is the fallback repair_corner_family_skus
    reaches for when southbrook_kitchen_3d_configurator's fields aren't
    registered in Template._fields yet at call time (the module-load-
    order trap) — but it's also a standalone, independently callable/
    testable unit on its own: a test can invoke it directly (forcing
    this path even when the ORM path IS available, since a
    TransactionCase always has the full registry loaded) and compare
    its result to the ORM path (`tmpl.write(flags)`) for the same input.

    `columns` is the set of matching DB column names (see
    `_cabinet_flag_columns`); pass None to have this function look it
    up itself. Idempotent: only writes when at least one flag's current
    DB value differs from the target (`IS DISTINCT FROM`, which handles
    NULL correctly, unlike `!=`). Returns True iff a row was changed.
    """
    if columns is None:
        columns = _cabinet_flag_columns(env.cr)
    if not columns.issuperset(flags.keys()):
        return False
    cols = list(flags.keys())
    set_clause = ", ".join("%s = %%s" % c for c in cols)
    guard_clause = " OR ".join("%s IS DISTINCT FROM %%s" % c for c in cols)
    params = [flags[c] for c in cols]
    env.cr.execute(
        "UPDATE product_template SET %s WHERE id = %%s AND (%s)"
        % (set_clause, guard_clause),
        params + [tmpl.id] + params,
    )
    # No env-level cache invalidation here: in the real fallback
    # scenario, these fields aren't part of the model's _fields in that
    # registry generation (that's the whole reason for the fallback),
    # so nothing in that process has — or could have — a cached value
    # for them. A test that calls this directly and then reads the
    # field back via the ORM (where the field DOES exist) is
    # responsible for its own invalidate_recordset() first.
    return bool(env.cr.rowcount)


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
    #
    # The decision of WHICH path to use lives here (has_cabinet_fields);
    # the SQL path itself is the module-level _write_cabinet_flags_sql
    # below so a test can call it directly — forcing the fallback branch
    # even in a TransactionCase, where the ORM path is always the one
    # that would naturally fire (the full registry, including kitchen_
    # 3d_configurator's fields, is already loaded by the time any test
    # runs) — and compare its result to the ORM path for the same input.
    cabinet_columns = (None if has_cabinet_fields
                        else _cabinet_flag_columns(env.cr))

    def _write_cabinet_flags(tmpl, flags):
        if has_cabinet_fields:
            diff = {k: v for k, v in flags.items() if tmpl[k] != v}
            if diff:
                tmpl.write(diff)
                return True
            return False
        return _write_cabinet_flags_sql(env, tmpl, flags, cabinet_columns)

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


def archive_catalog_family_duplicate_templates(env):
    """Deploy-gate hotfix (2026-07-31) — archive catalog-family
    duplicate TEMPLATES minted by the exact SBK-037-class bug the T23
    generic-dedup exclusion above was written to sidestep for the
    corner family.

    Mechanism (confirmed against a real reproduction, gate DB tmpl
    13790): once a corner-family template (SB-CORNER, SB-CORNER-BLIND,
    SB-CORNER-DIAG, SB-WALL-CORNER) gets a materialized "-R" hand
    twin, its TEMPLATE-level default_code correctly blanks (Odoo only
    mirrors a single variant). A caller that re-enters this repair
    chain on a LATER pass with a fresh env — e.g. this module's own
    two stacked migrations (19.0.9.1.0 then 19.0.9.2.0) both calling
    repair_corner_sku in the SAME upgrade run — can then find nothing
    via an active-only, template-level search and mint a SECOND,
    single-variant, attribute-less TEMPLATE carrying the SAME bare
    corner-family code on its own lone variant. This is now fixed at
    the source (repair_corner_sku's own SB-WALL-CORNER seed above, and
    catalog_expansion.build_catalog()'s existence check), but this
    sweep cleans up any duplicate that already got minted before those
    fixes landed (the gate DB has at least one; prod could too, since
    the exact same two-migration shape exists there).

    Never touches repair_cabinet_variant_refs()'s generic dedup path:
    corner-family codes are excluded there by design (owned
    exclusively by this file's own repair, never the generic
    Width-suffix convention) — which is exactly why a DEDICATED sweep
    for this specific duplicate shape is needed here instead.

    For each corner-family bare code, if more than one ACTIVE variant
    anywhere carries it (a genuine cross-TEMPLATE duplicate default_
    code), the template that ALSO carries the materialized "<code>-R"
    hand twin is treated as the real one — T4/T4a always creates the
    twin on the real template, never an impostor. Any OTHER template
    holding that bare code is archived (never deleted, never
    resurrected), UNLESS it carries any real sale.order.line or
    southbrook.kitchen.design.line reference, in which case it is left
    alone and logged for human review — this bug should only ever
    produce a same-session, zero-reference impostor, but this never
    risks a false-positive archive of real business data. Idempotent:
    an already-archived impostor no longer counts as an active
    duplicate, so a second call finds nothing left to do.
    """
    summary = {"duplicate_templates_archived": 0,
               "skipped_has_references": 0}
    Product = env["product.product"]
    Template = env["product.template"]
    SaleOrderLine = env["sale.order.line"]

    for base_code in sorted(_CORNER_FAMILY_BASE_CODES):
        bare_variants = Product.search([("default_code", "=", base_code)])
        tmpl_ids = set(bare_variants.mapped("product_tmpl_id").ids)
        if len(tmpl_ids) <= 1:
            continue  # no cross-template duplicate for this code

        twin_code = base_code + "-R"
        real_tmpl_ids = set(
            Product.search([("default_code", "=", twin_code)])
            .mapped("product_tmpl_id").ids)
        # The real template is whichever candidate also owns the hand
        # twin; if none (or, pathologically, more than one) does, fall
        # back to the lowest template id -- oldest, first-created,
        # same "first wins, never touch it" discipline as the LSUSAN
        # sweep above.
        real_candidates = tmpl_ids & real_tmpl_ids
        real_id = min(real_candidates) if real_candidates else min(tmpl_ids)

        for dup_id in sorted(tmpl_ids - {real_id}):
            dup_tmpl = Template.browse(dup_id)
            dup_variant_ids = bare_variants.filtered(
                lambda v, _id=dup_id: v.product_tmpl_id.id == _id).ids
            has_references = bool(SaleOrderLine.search_count(
                [("product_id", "in", dup_variant_ids)]))
            if (not has_references
                    and "southbrook.kitchen.design.line"
                    in env.registry.models):
                has_references = bool(
                    env["southbrook.kitchen.design.line"].search_count(
                        [("product_id", "in", dup_variant_ids)]))
            if has_references:
                summary["skipped_has_references"] += 1
                _logger.warning(
                    "[SBK-037 hotfix] %s: candidate duplicate template "
                    "id=%s carries a real sale-order-line/design-line "
                    "reference -- skipped, NOT archived, needs human "
                    "review.", base_code, dup_id)
                continue
            _logger.info(
                "[SBK-037 hotfix] archiving catalog-family duplicate "
                "template id=%s (%r) for code %s -- real template is "
                "id=%s.", dup_id, dup_tmpl.name, base_code, real_id)
            dup_tmpl.active = False
            summary["duplicate_templates_archived"] += 1

    if any(summary.values()):
        _logger.info(
            "[SBK-037 hotfix] archive_catalog_family_duplicate_"
            "templates: %s", summary)
    return summary


# ---------------------------------------------------------------------
# Task 23 (SBK-037, .superpowers/sdd/corner-remediation-2026-07-30/
# task-23-brief.md) — catalogue hygiene: unique, non-empty
# product.product.default_code on cabinet-family VARIANTS.
#
# Live-verified (2026-07-30, ci_corner_full clone): a config_ok
# multi-variant cabinet template's template-level default_code (e.g.
# "SB-BASE-2DR") reads back onto MORE THAN ONE of its materialized
# variants -- a stale mirror from the moment the template briefly had
# exactly one variant (product.template._compute_default_code only
# blanks the template-level field on its NEXT recompute, not
# retroactively -- see product/models/product_template.py's
# _compute_template_field_from_variant_field), while a sibling variant
# of the SAME template ends up with no code at all. Both are catalogue-
# hygiene bugs: duplicate refs break barcode/PO/label lookups by code,
# and an empty ref means the SKU can't be looked up by code at all.
# The brief's own example, SB-BASE-1DR, is the same class of bug on a
# different template/DB snapshot -- this repair is written generically
# against the pattern (any cabinet-family duplicate/empty ref), not
# hard-coded to one default_code.
# ---------------------------------------------------------------------

def _code_slug(value):
    """Deterministic, code-safe suffix derived from an attribute
    value's display name -- NEVER random. A numeric-leading value
    (e.g. "24 in", "36 in" -- the Width vocabulary used throughout
    this file) collapses to just the leading number ("24", "36"); a
    non-numeric value (e.g. a Hinge Side or Family name) collapses to
    an upper-cased alnum token."""
    if not value:
        return ""
    value = value.strip()
    m = re.match(r"^(\d+(?:\.\d+)?)", value)
    if m:
        num = m.group(1)
        return num[:-2] if num.endswith(".0") else num
    return re.sub(r"[^A-Za-z0-9]+", "", value).upper() or ""


def _distinguishing_suffix(variant):
    """Pick the attribute value that should distinguish this variant's
    code from its siblings. Width preferred (it's what actually varies
    within every live duplicate group found on this catalogue); falls
    back to the variant's other attribute values (lowest attribute id
    first, for determinism), then -- for a genuinely bare variant with
    no attribute combination at all -- to the variant's own database
    id, which is still fully deterministic and guaranteed unique,
    never random."""
    ptavs = variant.product_template_attribute_value_ids
    width = ptavs.filtered(lambda p: p.attribute_id.name == "Width")
    if width:
        slug = _code_slug(width[0].name)
        if slug:
            return slug
    for ptav in ptavs.sorted(key=lambda p: p.attribute_id.id):
        slug = _code_slug(ptav.name)
        if slug:
            return slug
    return str(variant.id)


def _unique_code(candidate, taken, disambiguator):
    """`candidate` unless it collides with an already-known/assigned
    code, in which case the (still deterministic, never random)
    variant id is appended so this repair can never manufacture a NEW
    collision."""
    if candidate not in taken:
        return candidate
    return "%s-%s" % (candidate, disambiguator)


def _cabinet_template_base_code(cr, tmpl_id):
    """Best-effort recovery of the intended base internal-reference
    prefix for a cabinet template, read via raw SQL (not the ORM) so
    the stored compute's pre-drift value survives even though the ORM
    would recompute product.template.default_code to blank the moment
    product_variant_ids is re-evaluated with more than one active
    variant (the same trap documented on repair_corner_family_skus's
    _resolve_tmpl above)."""
    cr.execute(
        "SELECT default_code FROM product_template WHERE id = %s",
        (tmpl_id,))
    row = cr.fetchone()
    code = (row[0] or "").strip() if row else ""
    return code or None


def _cabinet_sibling_base_code(cr, tmpl_id, exclude_variant_ids=()):
    """Fallback base-code source when the template-level code is also
    empty (or stale-blank): the lowest-id OTHER active variant of the
    same template that already carries a code. `exclude_variant_ids`
    keeps this from picking a code THIS SAME repair pass just
    generated for a sibling (which would already carry a distinguishing
    suffix and produce a nonsense double-suffixed code on the next
    variant) -- callers pass the running set of just-written variant
    ids."""
    cr.execute(
        "SELECT id, default_code FROM product_product "
        "WHERE product_tmpl_id = %s AND active = true ORDER BY id",
        (tmpl_id,))
    for vid, code in cr.fetchall():
        if vid in exclude_variant_ids:
            continue
        code = (code or "").strip()
        if code:
            return code
    return None


def repair_cabinet_variant_refs(env):
    """Unique, non-empty default_code on every active cabinet-family
    (southbrook_is_cabinet=True) product.product variant. Returns a
    summary dict.

    Deterministic repair, never random: every rewritten variant gets a
    code of the form "<base>-<suffix>", where <base> is the shared
    duplicate code itself (for a duplicate group) or the best-effort
    recovered template base code (for an empty ref -- see
    _cabinet_template_base_code / _cabinet_sibling_base_code above),
    and <suffix> is derived from the variant's OWN distinguishing
    attribute value (see _distinguishing_suffix). Idempotent: once a
    variant's code is unique and non-empty it is no longer part of any
    duplicate group and no longer empty, so a second pass finds
    nothing left to do for it -- confirmed by
    test_cabinet_variant_ref_repair.py's idempotency test.

    Scope is deliberately the coarse southbrook_is_cabinet flag, not
    southbrook_cabinet_type -- a duplicate/empty ref is a hygiene bug
    on ANY cabinet regardless of type (base/wall/tall/corner/...).
    Non-cabinet products (hardware, accessories, KD components, ...)
    are REPORT-ONLY per the brief: logged and counted, never written --
    their SKU conventions aren't verified against this repair's
    assumptions, and the brief scopes the write to cabinets.

    Never writes product.template.default_code -- per the brief,
    multi-variant cabinet templates deliberately carry no
    template-level ref (a locked convention); only variant rows
    (product.product) are ever written here. When kitchen_3d_
    configurator isn't installed on this DB (southbrook_is_cabinet
    doesn't exist as a column at all), there is no cabinet/non-cabinet
    distinction to make, so every active product falls into the
    report-only bucket and nothing is written.
    """
    summary = {
        "duplicates_split": 0, "empty_filled": 0,
        "skipped_unresolvable": 0,
        "noncabinet_duplicate_groups_reported": 0,
        "noncabinet_empty_reported": 0,
    }
    cr = env.cr
    Product = env["product.product"]

    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'product_template'
        AND column_name = 'southbrook_is_cabinet'
    """)
    has_cabinet_flag = bool(cr.fetchone())

    is_cabinet_sql = "pt.southbrook_is_cabinet" if has_cabinet_flag else "false"
    cr.execute("""
        SELECT pp.id, pp.product_tmpl_id, pp.default_code, %s
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE pp.active = true AND pt.active = true
        ORDER BY pp.product_tmpl_id, pp.id
    """ % is_cabinet_sql)
    rows = cr.fetchall()

    cabinet_rows = [r for r in rows if r[3]]
    noncabinet_rows = [r for r in rows if not r[3]]

    # -- corner family exclusion (code review finding 2) -------------
    # The corner family's OWN code convention ("SB-CORNER"/"SB-CORNER-R"
    # etc.) is owned entirely by repair_corner_sku/repair_corner_family
    # _skus above -- never re-derived or rewritten here, regardless of
    # call order. A template counts as corner-family either by its own
    # raw default_code column (catches a template whose corner repair
    # hasn't materialized a coded variant yet at all) or by any of its
    # variants already carrying a corner-family code.
    corner_family_tmpl_ids = set()
    cr.execute(
        "SELECT id FROM product_template WHERE default_code = ANY(%s)",
        (list(_CORNER_FAMILY_BASE_CODES),))
    corner_family_tmpl_ids.update(row[0] for row in cr.fetchall())
    for _vid, tmpl_id, code, _is_cab in cabinet_rows:
        if _is_corner_family_code((code or "").strip()):
            corner_family_tmpl_ids.add(tmpl_id)
    if corner_family_tmpl_ids:
        excluded = [r for r in cabinet_rows if r[1] in corner_family_tmpl_ids]
        if excluded:
            _logger.info(
                "[SBK-037] excluded %d corner-family variant(s) across "
                "%d template(s) from the generic dedup/empty-ref repair "
                "-- owned exclusively by T4/T4a's corner-family repair.",
                len(excluded), len(corner_family_tmpl_ids))
        cabinet_rows = [
            r for r in cabinet_rows if r[1] not in corner_family_tmpl_ids]

    # -- report-only: non-cabinet products ---------------------------
    nc_by_code = {}
    nc_empty = 0
    for vid, _tmpl_id, code, _is_cab in noncabinet_rows:
        code = (code or "").strip()
        if not code:
            nc_empty += 1
            continue
        nc_by_code.setdefault(code, []).append(vid)
    nc_dupes = {c: ids for c, ids in nc_by_code.items() if len(ids) > 1}
    if nc_dupes:
        summary["noncabinet_duplicate_groups_reported"] = len(nc_dupes)
        _logger.info(
            "[SBK-037 report-only] %d non-cabinet default_code(s) "
            "shared by more than one active variant -- NOT repaired "
            "(cabinet-only scope): %s",
            len(nc_dupes), nc_dupes)
    if nc_empty:
        summary["noncabinet_empty_reported"] = nc_empty
        _logger.info(
            "[SBK-037 report-only] %d active non-cabinet variant(s) "
            "have no default_code -- NOT repaired (cabinet-only "
            "scope).", nc_empty)

    if not cabinet_rows:
        return summary

    # Every currently-known code, cabinet + non-cabinet: the collision
    # guard for every candidate this pass ever writes.
    taken = {(code or "").strip() for _vid, _t, code, _c in rows}
    taken.discard("")

    # -- duplicates among cabinet variants ----------------------------
    by_code = {}
    for vid, _tmpl_id, code, _is_cab in cabinet_rows:
        code = (code or "").strip()
        if code:
            by_code.setdefault(code, []).append(vid)
    dup_groups = {c: ids for c, ids in by_code.items() if len(ids) > 1}

    generated_this_run = set()

    for base_code, variant_ids in sorted(dup_groups.items()):
        for variant in Product.browse(sorted(variant_ids)):
            suffix = _distinguishing_suffix(variant)
            candidate = ("%s-%s" % (base_code, suffix)) if suffix else base_code
            candidate = _unique_code(candidate, taken, variant.id)
            if candidate == (variant.default_code or "").strip():
                continue
            old_code = variant.default_code
            variant.default_code = candidate
            taken.add(candidate)
            generated_this_run.add(variant.id)
            summary["duplicates_split"] += 1
            _logger.info(
                "[SBK-037 cabinet ref repair] variant id=%s (tmpl "
                "id=%s): duplicate default_code %r -> %r",
                variant.id, variant.product_tmpl_id.id, old_code, candidate)

    # -- empty default_code among cabinet variants --------------------
    empty_variant_ids = sorted(
        vid for vid, _t, code, _c in cabinet_rows
        if not (code or "").strip())

    for vid in empty_variant_ids:
        variant = Product.browse(vid)
        tmpl_id = variant.product_tmpl_id.id
        base = (_cabinet_template_base_code(cr, tmpl_id)
                or _cabinet_sibling_base_code(
                    cr, tmpl_id, generated_this_run | {vid}))
        if not base:
            summary["skipped_unresolvable"] += 1
            _logger.warning(
                "[SBK-037 cabinet ref repair] variant id=%s (tmpl "
                "id=%s) has no default_code and no resolvable base "
                "code (no template-level code, no coded sibling) -- "
                "skipped, left for a human to reconcile.",
                vid, tmpl_id)
            continue
        suffix = _distinguishing_suffix(variant)
        candidate = ("%s-%s" % (base, suffix)) if suffix else base
        candidate = _unique_code(candidate, taken, vid)
        variant.default_code = candidate
        taken.add(candidate)
        generated_this_run.add(vid)
        summary["empty_filled"] += 1
        _logger.info(
            "[SBK-037 cabinet ref repair] variant id=%s (tmpl id=%s): "
            "empty default_code -> %r (base %r)",
            vid, tmpl_id, candidate, base)

    if any(summary.values()):
        _logger.info("[SBK-037 cabinet variant ref repair] %s", summary)
    return summary
