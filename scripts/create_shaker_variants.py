# SPDX-License-Identifier: LGPL-3.0-only
"""Southbrook Shaker variant backfill — idempotent (defect H3).

An E2E evaluation surfaced H3: "the catalog cannot build a non-white
kitchen." Investigation showed the catalog is ALREADY scaffolded and
priced for a Shaker door style (a real +$25.00 price_extra sits on the
"Door Style" product.template.attribute.value of every cabinet template
that offers it) — what's missing is that these templates are OCA
config_ok=True (dynamic attributes), so Odoo does NOT auto-materialise
product.product variants for every combination. Until a variant exists
that actually carries the Shaker PTAV, no sale/quote line can ever
select "Shaker" — the option is priced and visible in the configurator
but unbuildable. This script creates exactly the missing variant, once,
per template.

PRECEDENT, and why it is NOT copied verbatim (read-only inspection of
prod, 2026-08-03 — full trail in
/private/tmp/.../scratchpad/v_shaker_variants.md):

Templates 39 (Base 2-Door), 214 (Base Cabinet - 3-Drawer Stack) and 215
(Base Cabinet - 4-Drawer Pot) already carry Shaker variants, proving the
mechanism (the standard Odoo m2m `product.product
.product_template_attribute_value_ids`, backed by the
`product_variant_combination` table, with `combination_indices` as
Odoo's own stored-compute) works. But their SHAPE is inconsistent and
unsafe to clone wholesale: template 39's two Shaker variants are
partial stubs carrying only 2-3 attribute values each (e.g. "White
Melamine + Shaker", default_code "SB-BASE-2DR-WHITEMELAMINE-1148") with
NO Width value at all, while template 39 also has ~9 OTHER active
variants carrying a full ~15-attribute spec. The estimating/configurator
resolves a cabinet by placed width (`placed_width_in`); a Width-less
variant falls back to a template default and would just add another
ambiguous same-width candidate rather than making a Shaker kitchen
genuinely orderable. Template 214/215's single Shaker variant, by
contrast, IS a full-attribute combination.

APPROACH — "clone the best variant, swap one attribute": for each
active, non-corner template that offers a Shaker PTAV with zero active
Shaker variants:
  1. Pick a DONOR: the template's active variant with the most PTAVs on
     its combination (ties broken by lowest id) — i.e. the most fully
     specified, most "real" existing cabinet for that template.
  2. If the donor has no Width attribute value (attribute name "Width",
     resolved by query — id is NOT hardcoded, since local ids drift
     from prod), the clone would be unresolvable by the width-based
     placement engine and would only add noise. Skip and log
     SKIP-NO-WIDTH-DONOR.
  3. Build the new combination = donor's PTAVs, with whatever PTAV the
     donor has on the *same attribute* as the Shaker PTAV (i.e. its
     "Door Style" value, again resolved by attribute id off the Shaker
     PTAV itself, not by name literal) replaced by the Shaker PTAV. If
     the donor has no value at all on that attribute, the Shaker PTAV
     is simply ADDED, keeping every other donor attribute untouched.
     This is the same idiom already used elsewhere in this codebase for
     customer-driven variant creation — see
     `southbrook_estimating_website/controllers/main.py` (~line 1390):
     `new_combination = (current_ptav - old_ptav_for_attr) | ptav_for_target`.
  4. Price is never hand-set: it comes entirely from the new
     combination's own PTAV price_extras via Odoo's `lst_price`
     compute. The delta vs. the donor is logged for comparison — it is
     NOT always +$25: e.g. on prod, template 38's donor already carries
     a pricier door style (Reeded, +$75), so swapping in Shaker
     (+$25) is a $50 *decrease* relative to that donor. This script
     never writes a price field directly and never assumes the delta.
  5. default_code follows the donor's own SKU with a "-SHAKER" suffix;
     if that string is already taken by ANY product (checked with
     active_test=False — archived rows squat on SKUs in this DB), the
     new record's own id is appended once it exists, mirroring the
     "-1148"/"-1149" disambiguation already observed on prod.

HARD EXCLUSIONS (non-negotiable):
  - ANY corner template. Detected by "corner" (case-insensitive) in the
    template name, the template's own default_code, OR any existing
    variant's default_code (belt-and-braces; on prod all three signals
    agree for every corner template found: 44, 223, 224, 236 all have
    "Corner" in the name AND an SB-CORNER*/SB-WALL-CORNER* SKU family).
    The corner-cabinet subsystem is currently failing 11 tests in
    another engineer's hands (see corner_sku_repair.py) — writing new
    variants into it here would be reckless. Logged as SKIP-CORNER.
  - Templates that already have >= 1 ACTIVE variant carrying the Shaker
    PTAV (idempotency). Logged as SKIP-HAS-SHAKER.
  - Inactive templates (excluded at the query level; also re-checked
    defensively per row).
  - A Shaker PTAV with no usable price: if `price_extra <= 0` (unset,
    zero, or negative), this script does NOT invent a number. Logged as
    SKIP-NO-PRICE.
  - A template with zero active variants at all (nothing to clone from,
    and inventing a from-scratch full combination would be exactly the
    kind of guess this script must not make). Logged as SKIP-NO-DONOR.
  - A donor with no Width value (see step 2 above). Logged as
    SKIP-NO-WIDTH-DONOR.
No price is ever fabricated: the amount charged is always exactly the
existing PTAVs' price_extra, read fresh off prod/local data, never a
literal number in this file.

Run via `odoo shell`, piped in (same idiom as
scripts/backfill_product_skus.py):

    docker exec -i <container> sh -lc \\
        'PYTHONPATH=/srv/shared odoo shell -c /etc/odoo/odoo.conf \\
         --addons-path=/mnt/extra-addons,/mnt/pg-addons \\
         --db_host=db --db_user=odoo --db_password="$PGPASSWORD" \\
         -d southbrook --http-port=8997 2>/dev/null' \\
        < scripts/create_shaker_variants.py

Mode — SKJ_APPLY env var (same contract as backfill_product_skus.py,
NOT a hardcoded flag, so the same file can be piped in dry-run and then
apply mode without editing it):

    SKJ_APPLY unset or != "1"  -> DRY RUN (default). Every write below
        (variant creation / reactivation) is executed for real against
        the ORM in this transaction — so unique-combination
        constraints, computed price fields, etc. are exercised exactly
        as they would be for real — then the WHOLE transaction is
        rolled back at the very end via env.cr.rollback(). Nothing
        persists.
    SKJ_APPLY=1                -> APPLY. Same code path, same writes,
        committed at the end via env.cr.commit().

Every log line is prefixed VERB = "DID" (apply) or "WOULD" (dry run) so
a diff of the two runs' output is meaningful. Idempotent throughout: a
second run after SKJ_APPLY=1 should log nothing but SKIP-HAS-SHAKER /
SKIP-CORNER / SKIP-NO-PRICE / SKIP-NO-DONOR / SKIP-NO-WIDTH-DONOR lines
and a final recount of 0 (net of any SKIP-NO-WIDTH-DONOR / SKIP-NO-DONOR
templates, which by definition can never be fixed by this script and
are called out separately in the summary).

This script does NOT touch corner templates, does NOT run against
production directly (a separate controller applies it there), and does
NOT invent attribute combinations or prices beyond what's already in
the data.
"""
import os
import re

APPLY = os.environ.get("SKJ_APPLY") == "1"
VERB = "DID" if APPLY else "WOULD"

Template = env["product.template"].sudo()
Product = env["product.product"].sudo()
AttributeValue = env["product.attribute.value"].sudo()
Attribute = env["product.attribute"].sudo()
TemplateAttributeValue = env["product.template.attribute.value"].sudo()

counters = {}


def bump(key, n=1):
    counters[key] = counters.get(key, 0) + n


def log(msg):
    print(msg)


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


hr("SOUTHBROOK SHAKER VARIANT BACKFILL — mode=%s (SKJ_APPLY=%r)"
   % ("APPLY" if APPLY else "DRY-RUN", os.environ.get("SKJ_APPLY")))

CORNER_RE = re.compile(r"corner", re.IGNORECASE)


def is_corner(tmpl):
    """True if this template is (or otherwise smells like) a corner
    cabinet. Name is the primary signal; the template's own
    default_code and any existing variant's default_code are checked
    too, belt-and-braces, since on prod all corner templates carry an
    SB-CORNER*/SB-WALL-CORNER* SKU family in addition to the name.
    """
    if CORNER_RE.search(tmpl.name or ""):
        return True
    if tmpl.default_code and CORNER_RE.search(tmpl.default_code):
        return True
    variants = Product.with_context(active_test=False).search(
        [("product_tmpl_id", "=", tmpl.id)])
    for code in variants.mapped("default_code"):
        if code and CORNER_RE.search(code):
            return True
    return False


def describe_combination(ptavs):
    """Human-readable 'Attribute=Value' list, for logging/report use."""
    parts = ["%s=%s" % (v.attribute_id.name, v.product_attribute_value_id.name)
              for v in ptavs.sorted(key=lambda r: r.id)]
    return "; ".join(parts)


def combination_key(ptavs):
    """Mirror Odoo's own product.product.combination_indices compute:
    sorted, comma-joined ids. Used to look for an exact archived
    duplicate before inserting a fresh row.
    """
    return ",".join(str(i) for i in sorted(ptavs.ids))


def pick_donor(tmpl):
    """The template's active variant with the most PTAVs on its
    combination (most fully specified => most 'real' existing
    cabinet), ties broken by lowest id. None if the template has no
    active variants at all.
    """
    variants = Product.search([("product_tmpl_id", "=", tmpl.id),
                                ("active", "=", True)])
    if not variants:
        return None
    ranked = sorted(
        variants,
        key=lambda v: (-len(v.product_template_attribute_value_ids), v.id),
    )
    return ranked[0]


def slug_from_name(name):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name or "").strip("-").upper()
    slug = re.sub(r"-+", "-", slug)
    return "SB-%s" % slug if slug else "SB-VARIANT"


def find_shaker_ptavs():
    """All active product.template.attribute.value rows for the
    'Shaker' product.attribute.value, on active templates, with the
    PTAV itself active (i.e. currently offered, not archived off the
    attribute line).
    """
    shaker_values = AttributeValue.search([("name", "=", "Shaker")])
    if not shaker_values:
        return TemplateAttributeValue.browse()
    return TemplateAttributeValue.search([
        ("product_attribute_value_id", "in", shaker_values.ids),
        ("ptav_active", "=", True),
        ("product_tmpl_id.active", "=", True),
    ])


WIDTH_ATTRS = Attribute.search([("name", "=", "Width")])


def has_width(ptavs):
    if not WIDTH_ATTRS:
        return False
    return bool(ptavs.filtered(lambda v: v.attribute_id.id in WIDTH_ATTRS.ids))


def zero_shaker_noncorner_count():
    """Recount: how many active, non-corner templates offer Shaker
    (active PTAV) but carry ZERO active variant with that PTAV. This
    is the H3 defect count. Note this recount does NOT distinguish
    templates this script structurally cannot fix (SKIP-NO-DONOR /
    SKIP-NO-WIDTH-DONOR) — those are called out separately in the
    summary since they will still show up here after a real apply.
    """
    n = 0
    for ptav in find_shaker_ptavs():
        tmpl = ptav.product_tmpl_id
        if is_corner(tmpl):
            continue
        has_variant = Product.search_count([
            ("product_tmpl_id", "=", tmpl.id),
            ("product_template_attribute_value_ids", "in", [ptav.id]),
            ("active", "=", True),
        ])
        if not has_variant:
            n += 1
    return n


hr("SECTION 0 — baseline")
baseline = zero_shaker_noncorner_count()
log("Baseline: %d active, non-corner template(s) offer Shaker with "
    "zero active Shaker variant." % baseline)
bump("baseline_zero_shaker", baseline)

hr("SECTION 1 — Shaker variant backfill (clone donor, swap door style)")

ptavs = find_shaker_ptavs().sorted(key=lambda r: r.product_tmpl_id.id)
seen_tmpl_ids = set()

for ptav in ptavs:
    tmpl = ptav.product_tmpl_id

    if tmpl.id in seen_tmpl_ids:
        log("SECTION1 SKIP-DUPLICATE-PTAV id=%s|%s| template already "
            "processed via an earlier Shaker PTAV row — this one "
            "(ptav id=%s) ignored" % (tmpl.id, tmpl.name, ptav.id))
        bump("s1_skip_duplicate_ptav")
        continue
    seen_tmpl_ids.add(tmpl.id)

    if not tmpl.active:
        log("SECTION1 SKIP-INACTIVE-TEMPLATE id=%s|%s|" % (tmpl.id, tmpl.name))
        bump("s1_skip_inactive")
        continue

    if is_corner(tmpl):
        log("SECTION1 SKIP-CORNER id=%s|%s| (corner-cabinet subsystem is "
            "off-limits — 11 failing tests elsewhere)" % (tmpl.id, tmpl.name))
        bump("s1_skip_corner")
        continue

    existing_active = Product.search([
        ("product_tmpl_id", "=", tmpl.id),
        ("product_template_attribute_value_ids", "in", [ptav.id]),
        ("active", "=", True),
    ])
    if existing_active:
        log("SECTION1 SKIP-HAS-SHAKER id=%s|%s| already has %d active "
            "Shaker variant(s): %s"
            % (tmpl.id, tmpl.name, len(existing_active),
               existing_active.mapped("default_code")))
        bump("s1_skip_has_shaker")
        continue

    price_extra = ptav.price_extra
    if not price_extra or price_extra <= 0:
        log("SECTION1 SKIP-NO-PRICE id=%s|%s| Shaker PTAV id=%s has no "
            "usable price_extra (%r) — refusing to guess"
            % (tmpl.id, tmpl.name, ptav.id, price_extra))
        bump("s1_skip_no_price")
        continue

    donor = pick_donor(tmpl)
    if donor is None:
        log("SECTION1 SKIP-NO-DONOR id=%s|%s| has zero active variants — "
            "nothing to clone, refusing to invent a from-scratch "
            "combination" % (tmpl.id, tmpl.name))
        bump("s1_skip_no_donor")
        continue

    donor_ptavs = donor.product_template_attribute_value_ids
    if not has_width(donor_ptavs):
        log("SECTION1 SKIP-NO-WIDTH-DONOR id=%s|%s| best donor is "
            "variant_id=%s|%s| (%d attrs) but it carries no Width value — "
            "a clone would be unresolvable by the width-based placement "
            "engine" % (tmpl.id, tmpl.name, donor.id, donor.default_code,
                        len(donor_ptavs)))
        bump("s1_skip_no_width_donor")
        continue

    old_door_style_ptav = donor_ptavs.filtered(
        lambda v: v.attribute_id.id == ptav.attribute_id.id)
    new_combination = (donor_ptavs - old_door_style_ptav) | ptav
    swap_desc = ("%s -> Shaker" % old_door_style_ptav.product_attribute_value_id.name
                 if old_door_style_ptav else "(added) -> Shaker")

    combo_key = combination_key(new_combination)
    reactivate_candidate = Product.with_context(active_test=False).search([
        ("product_tmpl_id", "=", tmpl.id),
        ("combination_indices", "=", combo_key),
    ], limit=1)

    candidate_code = ("%s-SHAKER" % donor.default_code if donor.default_code
                       else "%s-SHAKER" % slug_from_name(tmpl.name))

    try:
        with env.cr.savepoint():
            if reactivate_candidate:
                reactivate_candidate.write({"active": True})
                variant = reactivate_candidate
                action = "REACTIVATE"
                disambiguated = False
            else:
                collision = Product.with_context(active_test=False).search_count(
                    [("default_code", "=", candidate_code)])
                if collision:
                    # Create first (no code yet), then disambiguate with the
                    # new record's own id — mirrors the "-1148"/"-1149"
                    # precedent, which suffixes the actual created id, not a
                    # guess made before the row exists.
                    variant = Product.create({
                        "product_tmpl_id": tmpl.id,
                        "product_template_attribute_value_ids":
                            [(6, 0, new_combination.ids)],
                    })
                    final_code = "%s-%s" % (candidate_code, variant.id)
                    variant.write({"default_code": final_code})
                    disambiguated = True
                else:
                    variant = Product.create({
                        "product_tmpl_id": tmpl.id,
                        "default_code": candidate_code,
                        "product_template_attribute_value_ids":
                            [(6, 0, new_combination.ids)],
                    })
                    disambiguated = False
                action = "CREATE"
    except Exception as exc:  # noqa: BLE001 - isolate one bad row, keep going
        log("SECTION1 ERROR id=%s|%s| Shaker PTAV id=%s: %r"
            % (tmpl.id, tmpl.name, ptav.id, exc))
        bump("s1_error")
        continue

    donor_price = donor.lst_price
    new_price = variant.lst_price
    delta = new_price - donor_price

    log("SECTION1 %s %s id=%s|%s| donor=variant_id=%s|%s|(%d attrs, "
        "price=%.2f) door_style_swap=[%s] -> variant_id=%s default_code=%r"
        "%s price=%.2f (delta vs donor %+.2f)"
        % (VERB, action, tmpl.id, tmpl.name, donor.id, donor.default_code,
           len(donor_ptavs), donor_price, swap_desc, variant.id,
           variant.default_code,
           " [disambiguated]" if disambiguated else "",
           new_price, delta))
    bump("s1_%s" % action.lower())

hr("SECTION 2 — summary and recount")

log("\nRun summary:")
for key, count in sorted(counters.items()):
    log("  %s: %d" % (key, count))

recount = zero_shaker_noncorner_count()
log("\nRecount of active, non-corner templates offering Shaker with "
    "zero active Shaker variants: %d" % recount)
unfixable = counters.get("s1_skip_no_donor", 0) + counters.get("s1_skip_no_width_donor", 0)
if unfixable:
    log("Of which %d template(s) are structurally unfixable by this script "
        "(SKIP-NO-DONOR + SKIP-NO-WIDTH-DONOR) — expected to remain in the "
        "recount even after a clean apply." % unfixable)
if APPLY and recount != unfixable:
    log("WARNING: recount does not equal the known-unfixable count after "
        "an APPLY run — investigate the ERROR/SKIP lines above.")

if APPLY:
    env.cr.commit()
    log("\nCOMMITTED to database.")
else:
    env.cr.rollback()
    log("\nROLLBACK (dry-run mode) — no changes persisted.")
