# SPDX-License-Identifier: LGPL-3.0-only
"""Southbrook door-style variant backfill — idempotent (defect H3, generalized).

HISTORY: this file started as a Shaker-only backfill (H3: "the catalog
cannot build a non-white kitchen"). An E2E evaluation surfaced that the
catalog was already scaffolded and priced for a Shaker door style (a
real +$25.00 price_extra sits on the "Door Style"
product.template.attribute.value of every cabinet template that offers
it) — what was missing is that these templates are OCA config_ok=True
(dynamic attributes), so Odoo does NOT auto-materialise
product.product variants for every combination. Until a variant exists
that actually carries a given door-style PTAV, no sale/quote line can
ever select that style — it is priced and visible in the configurator
but unbuildable. The Shaker-only version of this script proved the
mechanism and created 25 Shaker variants on prod successfully. THIS
version generalizes that proven mechanism from "just Shaker" to EVERY
door style the catalog actually offers — Five-Piece Woodgrain, Custom
(Signature), Raised Panel, Beadboard, Mullion Glass, V-Groove, Reeded,
and any future style — without hardcoding the style list: the set of
styles to process is read fresh, every run, from whatever values exist
under the "Door Style" product.attribute (itself resolved by name, not
id, since local ids drift hard from prod — see rehearsal notes in
/private/tmp/.../scratchpad/v_shaker_variants.md and
/private/tmp/.../scratchpad/v2_all_door_styles.md).

Never invents a combination: a (template, style) pair is only ever
processed here because an active product.template.attribute.value
already exists for it. The PTAV existing IS the business declaring
that combination valid — that is the ONLY authority used to decide
what gets created. If a style has no PTAV on a template, that template
is simply never visited for that style; nothing is guessed.

APPROACH — "clone the best donor, swap one attribute" (unchanged from
the Shaker-only version, now run once per style instead of once total):
for each active, non-corner template that offers a given door-style
PTAV with zero active variants carrying it:
  1. Pick a DONOR: the template's active variant with the most PTAVs on
     its combination (ties broken by lowest id) — i.e. the most fully
     specified, most "real" existing cabinet for that template.
  2. If the donor has no Width attribute value (attribute name "Width",
     resolved by query — id is NOT hardcoded), the clone would be
     unresolvable by the width-based placement engine and would only
     add noise. Skip and log SKIP-NO-WIDTH-DONOR.
  3. Build the new combination = donor's PTAVs, with whatever PTAV the
     donor has on the *same attribute* as the target style (i.e. its
     current "Door Style" value, resolved by attribute id off the
     target PTAV itself, never a literal) replaced by the target PTAV.
     If the donor has no value at all on that attribute, the target
     PTAV is simply ADDED, keeping every other donor attribute
     untouched. Same idiom already used elsewhere in this codebase for
     customer-driven variant creation — see
     `southbrook_estimating_website/controllers/main.py` (~line 1390):
     `new_combination = (current_ptav - old_ptav_for_attr) | ptav_for_target`.
  4. Price is never hand-set: it comes entirely from the new
     combination's own PTAV price_extras via Odoo's `lst_price`
     compute. The delta vs. the donor is logged for comparison and is
     NOT assumed uniform across styles or templates — it is whatever
     the two PTAVs' price_extra values actually produce (e.g. swapping
     a donor that already carries a pricier style, such as Reeded
     +$75, for a cheaper one is a real *decrease*). This script never
     writes a price field directly.
  5. default_code follows the donor's own SKU with a "-<STYLE SLUG>"
     suffix (see `style_slug()` below); if that string is already taken
     by ANY product (checked with active_test=False — archived rows
     squat on SKUs in this DB), the new record's own id is appended once
     it exists, mirroring the "-1148"/"-1149" disambiguation observed on
     prod for the original Shaker precedent.

WHICH STYLES GET PROCESSED — data-driven, not hardcoded:
  Every active value under the "Door Style" product.attribute (resolved
  by name, see `find_door_style_attribute()`), in ascending attribute-
  value id order (deterministic, reproducible runs; requirement: log per
  style, then per template within that style) — EXCEPT:

  - BASE_STYLE_NAMES ("Thermofoil Slab — White" as observed on prod
    2026-08-03): this is the donor's own default style and was already
    100% covered (every active, non-corner template offering it already
    carries an active variant for it) at the time this generalization
    was written. It is excluded from iteration ENTIRELY — not merely
    left to fall out via the idempotency check — because it is the
    donor default; touching it here is out of scope by design, not an
    oversight. This is a deliberate, documented, name-based exception
    (the same kind of business-concept name-matching already used for
    corner detection below), not a numeric id, and not a guess: if this
    style ever needs backfilling in the future, remove its name from
    BASE_STYLE_NAMES.

  Everything else under "Door Style" is processed, whatever it is,
  whenever it exists — no per-style Python branch decides whether a
  style is "in scope"; the query does.

HUMAN REVIEW FLAG — "Custom (Signature)": as observed on prod
2026-08-03, this style value reads like a specify-at-order-time option
(a custom-quote door, not a stock catalog choice) rather than a normal
stock door style. It is still processed exactly like every other style
— the PTAV existing is the business declaring the combination valid,
and this script does not second-guess that — but every variant created
or reactivated under this style is logged with a distinct
HUMAN-REVIEW-REQUIRED marker (see HUMAN_REVIEW_STYLE_NAMES) and called
out again in the final summary, so a human confirms it before treating
it as a normal, orderable stock SKU.

ASSERT-LOUD SAFETY CHECK: if a chosen donor is ever found to ALREADY
carry the exact target-style PTAV, that is a structural impossibility
under this script's own logic — the donor is one of the template's
active variants, so it would already have been counted in
`existing_active` above and the whole template/style pair would have
been SKIP-HAS-STYLE'd before a donor was even picked. If it ever
happens anyway (a bug, or a change in the surrounding data model this
script wasn't written against), it is logged LOUDLY
(ASSERT-VIOLATION...) and that row is refused rather than silently
creating a duplicate/no-op variant.

HARD EXCLUSIONS (non-negotiable, all style-agnostic):
  - ANY corner template. Detected by "corner" (case-insensitive) in the
    template name, the template's own default_code, OR any existing
    variant's default_code (belt-and-braces; on prod all three signals
    agree for every corner template found: 44, 223, 224, 236 all have
    "Corner" in the name AND an SB-CORNER*/SB-WALL-CORNER* SKU family).
    The corner-cabinet subsystem is currently failing 11 tests in
    another engineer's hands (see corner_sku_repair.py) — writing new
    variants into it here would be reckless. Logged as SKIP-CORNER.
  - Templates that already have >= 1 ACTIVE variant carrying the target
    style's PTAV (idempotency). Logged as SKIP-HAS-STYLE.
  - Inactive templates (excluded at the query level; also re-checked
    defensively per row).
  - A style PTAV with no usable price: if `price_extra <= 0` (unset,
    zero, or negative), this script does NOT invent a number. Logged as
    SKIP-NO-PRICE.
  - A template with zero active variants at all (nothing to clone from,
    and inventing a from-scratch full combination would be exactly the
    kind of guess this script must not make). Logged as SKIP-NO-DONOR.
  - A donor with no Width value (see step 2 above). Logged as
    SKIP-NO-WIDTH-DONOR.
  - "Thermofoil Slab — White" (BASE_STYLE_NAMES) — not iterated at all,
    see above.
No price is ever fabricated: the amount charged is always exactly the
existing PTAVs' price_extra, read fresh off prod/local data, never a
literal number in this file.

Run via `odoo shell`, piped in (same idiom as
scripts/backfill_product_skus.py and the original Shaker-only version):

    docker exec -i <container> sh -lc \\
        'PYTHONPATH=/srv/shared odoo shell -c /etc/odoo/odoo.conf \\
         --addons-path=/mnt/extra-addons,/mnt/pg-addons \\
         --db_host=db --db_user=odoo --db_password="$PGPASSWORD" \\
         -d southbrook --http-port=8997 2>/dev/null' \\
        < scripts/create_shaker_variants.py

Mode — SKJ_APPLY env var (same contract as backfill_product_skus.py and
the original Shaker-only version, NOT a hardcoded flag, so the same
file can be piped in dry-run and then apply mode without editing it):

    SKJ_APPLY unset or != "1"  -> DRY RUN (default). Every write below
        (variant creation / reactivation) is executed for real against
        the ORM in this transaction — so unique-combination
        constraints, computed price fields, etc. are exercised exactly
        as they would be for real — then the WHOLE transaction is
        rolled back at the very end via env.cr.rollback(). Nothing
        persists. This is safe to run read-only against prod for
        reporting purposes (no SKJ_APPLY=1) — it never commits.
    SKJ_APPLY=1                -> APPLY. Same code path, same writes,
        committed at the end via env.cr.commit(). The controller, not
        this script's author, decides when this runs against prod.

Every log line is prefixed VERB = "DID" (apply) or "WOULD" (dry run) so
a diff of the two runs' output is meaningful. Idempotent throughout: a
second run after SKJ_APPLY=1 should log nothing but SKIP-HAS-STYLE /
SKIP-CORNER / SKIP-NO-PRICE / SKIP-NO-DONOR / SKIP-NO-WIDTH-DONOR lines
and a final recount of 0 (net of any SKIP-NO-WIDTH-DONOR / SKIP-NO-DONOR
/ SKIP-NO-PRICE pairs, which by definition can never be fixed by this
script and are called out separately in the summary).

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

# Styles excluded from iteration entirely (see docstring "WHICH STYLES
# GET PROCESSED"). Name-based, not id-based — a deliberate, documented
# business exception, same idiom as the corner-name detection below.
BASE_STYLE_NAMES = {"Thermofoil Slab — White"}

# Styles that still get processed (a PTAV existing is always sufficient
# authority to create the variant) but whose created/reactivated rows
# are flagged for a human to confirm before being trusted as ordinary
# stock catalog SKUs. See docstring "HUMAN REVIEW FLAG".
HUMAN_REVIEW_STYLE_NAMES = {"Custom (Signature)"}

# Cosmetic SKU-suffix overrides for style names whose generic slug would
# be needlessly long. Never used for anything but the default_code
# suffix — has zero bearing on which (template, style) pairs are
# selected or validated. See style_slug().
STYLE_SLUG_OVERRIDES = {
    "Five-Piece Woodgrain": "FIVEPIECE",
    "Custom (Signature)": "CUSTOMSIG",
}

counters = {}
style_counters = {}


def bump(key, n=1):
    counters[key] = counters.get(key, 0) + n


def sbump(style_name, key, n=1):
    style_counters.setdefault(style_name, {})
    style_counters[style_name][key] = style_counters[style_name].get(key, 0) + n
    bump(key, n)


def log(msg):
    print(msg)


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


hr("SOUTHBROOK DOOR STYLE VARIANT BACKFILL — mode=%s (SKJ_APPLY=%r)"
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
    """Generic fallback SKU root when a donor has no default_code at
    all — unrelated to style_slug(), kept from the original Shaker-only
    version.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name or "").strip("-").upper()
    slug = re.sub(r"-+", "-", slug)
    return "SB-%s" % slug if slug else "SB-VARIANT"


def style_slug(style_name):
    """Short, uppercase, SKU-safe slug for a door-style attribute value
    name, e.g. 'Five-Piece Woodgrain' -> 'FIVEPIECE', 'Shaker' ->
    'SHAKER', 'V-Groove' -> 'VGROOVE'.

    Purely a cosmetic SKU-suffix concern — this has zero bearing on
    which (template, style) pairs get selected or validated; that
    authority is always the PTAV itself, resolved by query, never a
    name. Most style names collapse correctly under the generic rule
    (strip everything but letters/digits, uppercase); two of the
    observed names are long enough in full that a shorter suffix reads
    better as a SKU tail (mirroring the brevity of the original
    "-SHAKER" precedent), so those two are special-cased in
    STYLE_SLUG_OVERRIDES. Any future door style not in that table still
    gets a safe, if longer, slug from the generic rule — never silently
    dropped or invented.
    """
    if style_name in STYLE_SLUG_OVERRIDES:
        return STYLE_SLUG_OVERRIDES[style_name]
    slug = re.sub(r"[^A-Za-z0-9]+", "", style_name or "").upper()
    return slug or "STYLE"


def find_door_style_attribute():
    """Resolve the 'Door Style' product.attribute by name — never a
    hardcoded id, since local ids drift hard from prod (confirmed
    during rehearsal: local template ids do not match prod's). Every
    style processed by this script hangs off whatever attribute this
    resolves to, fresh every run.
    """
    attrs = Attribute.search([("name", "=", "Door Style")])
    return attrs[:1] if attrs else Attribute.browse()


def find_style_ptavs(style_value):
    """All active product.template.attribute.value rows for a given
    door-style product.attribute.value, on active templates, with the
    PTAV itself active (i.e. currently offered, not archived off the
    attribute line). Generalizes find_shaker_ptavs() from the original
    Shaker-only version — same query shape, parameterized by style.
    """
    return TemplateAttributeValue.search([
        ("product_attribute_value_id", "=", style_value.id),
        ("ptav_active", "=", True),
        ("product_tmpl_id.active", "=", True),
    ])


WIDTH_ATTRS = Attribute.search([("name", "=", "Width")])


def has_width(ptavs):
    if not WIDTH_ATTRS:
        return False
    return bool(ptavs.filtered(lambda v: v.attribute_id.id in WIDTH_ATTRS.ids))


def zero_gap_noncorner_count(style_values):
    """Recount: across the given style values, how many DISTINCT
    (template, style) pairs are active, non-corner templates offering
    that style (active PTAV) with ZERO active variant carrying it.
    Generalizes zero_shaker_noncorner_count() from the original
    Shaker-only version across every style passed in.

    Dedupes at the (template, style) level rather than the raw PTAV-row
    level: if a template ever has more than one active PTAV row for the
    same style value (not observed for Door Style in practice, but not
    assumed impossible either), the pair only counts as a gap if NONE
    of those rows has an active variant.

    Returns (total, {style_name: count}).
    """
    total = 0
    per_style = {}
    for style_value in style_values:
        gap_tmpl_ids = set()
        covered_tmpl_ids = set()
        for ptav in find_style_ptavs(style_value):
            tmpl = ptav.product_tmpl_id
            if is_corner(tmpl):
                continue
            has_variant = Product.search_count([
                ("product_tmpl_id", "=", tmpl.id),
                ("product_template_attribute_value_ids", "in", [ptav.id]),
                ("active", "=", True),
            ])
            if has_variant:
                covered_tmpl_ids.add(tmpl.id)
            else:
                gap_tmpl_ids.add(tmpl.id)
        n = len(gap_tmpl_ids - covered_tmpl_ids)
        per_style[style_value.name] = n
        total += n
    return total, per_style


door_style_attr = find_door_style_attribute()
if not door_style_attr:
    log("FATAL: no product.attribute named 'Door Style' found — nothing "
        "to do this run.")
    all_style_values = AttributeValue.browse()
else:
    all_style_values = AttributeValue.search(
        [("attribute_id", "=", door_style_attr.id)]
    ).sorted(key=lambda v: v.id)

processed_style_values = all_style_values.filtered(
    lambda v: v.name not in BASE_STYLE_NAMES)
skipped_base_style_values = all_style_values - processed_style_values

if skipped_base_style_values:
    hr("BASE STYLE(S) EXCLUDED ENTIRELY (donor default, already fully covered)")
    for v in skipped_base_style_values:
        log("  attribute_value id=%s name=%r — not iterated at all this "
            "run (see BASE_STYLE_NAMES / docstring)" % (v.id, v.name))

hr("SECTION 0 — baseline")
baseline_total, baseline_per_style = zero_gap_noncorner_count(processed_style_values)
log("Baseline across %d processed style(s): %d active, non-corner "
    "(template, style) pair(s) offer a style with zero active variant."
    % (len(processed_style_values), baseline_total))
for name, n in baseline_per_style.items():
    log("  %-24s %d" % (name, n))
bump("baseline_zero_gap_total", baseline_total)

hr("SECTION 1 — door style variant backfill (clone donor, swap door style)")

for style_value in processed_style_values:
    style_name = style_value.name
    slug = style_slug(style_name)
    is_human_review_style = style_name in HUMAN_REVIEW_STYLE_NAMES

    hr("STYLE %r (attribute_value id=%s, sku slug=-%s)"
       % (style_name, style_value.id, slug))

    if is_human_review_style:
        log("!!! HUMAN REVIEW FLAG !!! %r reads like a specify-at-order-time "
            "option rather than a stock catalog variant. Still processed — "
            "the PTAV existing is the business declaring the combination "
            "valid, and that is the only authority used here — but every "
            "row created or reactivated under this style is logged below "
            "with a distinct HUMAN-REVIEW-REQUIRED marker and must be "
            "confirmed by a human before being trusted as a real stock SKU."
           % style_name)

    ptavs = find_style_ptavs(style_value).sorted(key=lambda r: r.product_tmpl_id.id)
    seen_tmpl_ids = set()

    for ptav in ptavs:
        tmpl = ptav.product_tmpl_id

        if tmpl.id in seen_tmpl_ids:
            log("SECTION1 SKIP-DUPLICATE-PTAV id=%s|%s| template already "
                "processed via an earlier %s PTAV row — this one (ptav "
                "id=%s) ignored" % (tmpl.id, tmpl.name, style_name, ptav.id))
            sbump(style_name, "s1_skip_duplicate_ptav")
            continue
        seen_tmpl_ids.add(tmpl.id)

        if not tmpl.active:
            log("SECTION1 SKIP-INACTIVE-TEMPLATE id=%s|%s|" % (tmpl.id, tmpl.name))
            sbump(style_name, "s1_skip_inactive")
            continue

        if is_corner(tmpl):
            log("SECTION1 SKIP-CORNER id=%s|%s| (corner-cabinet subsystem is "
                "off-limits — 11 failing tests elsewhere)" % (tmpl.id, tmpl.name))
            sbump(style_name, "s1_skip_corner")
            continue

        existing_active = Product.search([
            ("product_tmpl_id", "=", tmpl.id),
            ("product_template_attribute_value_ids", "in", [ptav.id]),
            ("active", "=", True),
        ])
        if existing_active:
            log("SECTION1 SKIP-HAS-STYLE id=%s|%s| already has %d active "
                "%s variant(s): %s"
                % (tmpl.id, tmpl.name, len(existing_active), style_name,
                   existing_active.mapped("default_code")))
            sbump(style_name, "s1_skip_has_style")
            continue

        price_extra = ptav.price_extra
        if not price_extra or price_extra <= 0:
            log("SECTION1 SKIP-NO-PRICE id=%s|%s| %s PTAV id=%s has no "
                "usable price_extra (%r) — refusing to guess"
                % (tmpl.id, tmpl.name, style_name, ptav.id, price_extra))
            sbump(style_name, "s1_skip_no_price")
            continue

        donor = pick_donor(tmpl)
        if donor is None:
            log("SECTION1 SKIP-NO-DONOR id=%s|%s| has zero active variants — "
                "nothing to clone, refusing to invent a from-scratch "
                "combination" % (tmpl.id, tmpl.name))
            sbump(style_name, "s1_skip_no_donor")
            continue

        donor_ptavs = donor.product_template_attribute_value_ids
        if not has_width(donor_ptavs):
            log("SECTION1 SKIP-NO-WIDTH-DONOR id=%s|%s| best donor is "
                "variant_id=%s|%s| (%d attrs) but it carries no Width value — "
                "a clone would be unresolvable by the width-based placement "
                "engine" % (tmpl.id, tmpl.name, donor.id, donor.default_code,
                            len(donor_ptavs)))
            sbump(style_name, "s1_skip_no_width_donor")
            continue

        old_style_ptav = donor_ptavs.filtered(
            lambda v: v.attribute_id.id == ptav.attribute_id.id)

        if old_style_ptav and old_style_ptav.id == ptav.id:
            # Structurally impossible: if the donor already carried this
            # exact PTAV it would be in `existing_active` above and this
            # (template, style) pair would already have been
            # SKIP-HAS-STYLE'd before a donor was ever picked. Loud,
            # because a silent `continue` here would hide a real bug
            # rather than surface it.
            log("!!!!!! ASSERT-VIOLATION id=%s|%s| donor variant_id=%s|%s| "
                "ALREADY carries the target %s PTAV id=%s — the idempotency "
                "check should have caught this before a donor was picked. "
                "Refusing to create a duplicate/no-op variant; investigate "
                "immediately. !!!!!!"
                % (tmpl.id, tmpl.name, donor.id, donor.default_code,
                   style_name, ptav.id))
            sbump(style_name, "s1_assert_violation_donor_already_has_target")
            continue

        new_combination = (donor_ptavs - old_style_ptav) | ptav
        swap_desc = (
            "%s -> %s" % (old_style_ptav.product_attribute_value_id.name, style_name)
            if old_style_ptav else "(added) -> %s" % style_name)

        combo_key = combination_key(new_combination)
        reactivate_candidate = Product.with_context(active_test=False).search([
            ("product_tmpl_id", "=", tmpl.id),
            ("combination_indices", "=", combo_key),
        ], limit=1)

        candidate_code = ("%s-%s" % (donor.default_code, slug) if donor.default_code
                           else "%s-%s" % (slug_from_name(tmpl.name), slug))

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
                        # Create first (no code yet), then disambiguate with
                        # the new record's own id — mirrors the
                        # "-1148"/"-1149" precedent, which suffixes the
                        # actual created id, not a guess made before the row
                        # exists.
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
            log("SECTION1 ERROR id=%s|%s| %s PTAV id=%s: %r"
                % (tmpl.id, tmpl.name, style_name, ptav.id, exc))
            sbump(style_name, "s1_error")
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
        sbump(style_name, "s1_%s" % action.lower())

        if is_human_review_style:
            log("HUMAN-REVIEW-REQUIRED style=%r template_id=%s|%s| "
                "variant_id=%s default_code=%r — confirm this is meant to be "
                "a real, orderable stock catalog SKU before relying on it "
                "downstream."
                % (style_name, tmpl.id, tmpl.name, variant.id,
                   variant.default_code))
            sbump(style_name, "human_review_flagged")

    log("\n-- %s summary --" % style_name)
    for key, count in sorted(style_counters.get(style_name, {}).items()):
        log("  %s: %d" % (key, count))

hr("SECTION 2 — summary and recount")

log("\nPer-style breakdown (create / reactivate / skips):")
for style_value in processed_style_values:
    name = style_value.name
    sc = style_counters.get(name, {})
    log("  %-24s slug=-%-12s create=%-3d reactivate=%-3d skip_has_style=%-3d "
        "skip_no_price=%-3d skip_no_width_donor=%-3d skip_no_donor=%-3d "
        "skip_corner=%-3d skip_dup=%-3d error=%-3d assert_violation=%d"
        % (name, style_slug(name), sc.get("s1_create", 0),
           sc.get("s1_reactivate", 0), sc.get("s1_skip_has_style", 0),
           sc.get("s1_skip_no_price", 0), sc.get("s1_skip_no_width_donor", 0),
           sc.get("s1_skip_no_donor", 0), sc.get("s1_skip_corner", 0),
           sc.get("s1_skip_duplicate_ptav", 0), sc.get("s1_error", 0),
           sc.get("s1_assert_violation_donor_already_has_target", 0)))

grand_creates = sum(sc.get("s1_create", 0) for sc in style_counters.values())
grand_reactivates = sum(sc.get("s1_reactivate", 0) for sc in style_counters.values())
log("\nGRAND TOTAL across all processed styles: create=%d reactivate=%d "
    "(create+reactivate=%d)"
    % (grand_creates, grand_reactivates, grand_creates + grand_reactivates))

human_review_total = sum(
    sc.get("human_review_flagged", 0) for sc in style_counters.values())
if human_review_total:
    log("\nHUMAN REVIEW: %d variant(s) created/reactivated under %s — "
        "flagged above with HUMAN-REVIEW-REQUIRED markers; a human should "
        "confirm these belong in the stock catalog before relying on them."
        % (human_review_total, sorted(HUMAN_REVIEW_STYLE_NAMES)))

assert_violation_total = sum(
    sc.get("s1_assert_violation_donor_already_has_target", 0)
    for sc in style_counters.values())
if assert_violation_total:
    log("\n!!!! WARNING: %d structurally-impossible ASSERT-VIOLATION row(s) "
        "logged above — investigate before trusting any of this run's "
        "output. !!!!" % assert_violation_total)

log("\nRun summary (flat counters):")
for key, count in sorted(counters.items()):
    log("  %s: %d" % (key, count))

recount_total, recount_per_style = zero_gap_noncorner_count(processed_style_values)
log("\nRecount across %d processed style(s): %d active, non-corner "
    "(template, style) pair(s) still offer a style with zero active variant."
    % (len(processed_style_values), recount_total))
for name, n in recount_per_style.items():
    log("  %-24s %d" % (name, n))

unfixable = sum(
    style_counters.get(sv.name, {}).get("s1_skip_no_donor", 0)
    + style_counters.get(sv.name, {}).get("s1_skip_no_width_donor", 0)
    for sv in processed_style_values
)
if unfixable:
    log("Of which %d (template, style) pair(s) are structurally unfixable "
        "by this script (SKIP-NO-DONOR + SKIP-NO-WIDTH-DONOR) — expected "
        "to remain in the recount even after a clean apply." % unfixable)
if APPLY and recount_total != unfixable:
    log("WARNING: recount does not equal the known-unfixable count after "
        "an APPLY run — investigate the ERROR/SKIP lines above.")

if APPLY:
    env.cr.commit()
    log("\nCOMMITTED to database.")
else:
    env.cr.rollback()
    log("\nROLLBACK (dry-run mode) — no changes persisted.")
