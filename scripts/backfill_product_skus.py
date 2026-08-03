# SPDX-License-Identifier: LGPL-3.0-only
"""Southbrook SKU backfill — idempotent product default_code assignment (P3).

Assigns default_code (SKU) to 11 sellable products that lack one. Blocks
production-readiness validator checks for any design using a product with
no SKU (cabinet/consumable line only).

Run via `odoo shell`, piped in (env is bound at global scope by the shell —
no odoo.cli imports here):

    docker exec -i <container> sh -lc \\
        'PYTHONPATH=/srv/shared odoo shell -c /etc/odoo/odoo.conf \\
         --addons-path=/mnt/extra-addons,/mnt/pg-addons \\
         --db_host=db --db_user=odoo --db_password="$PGPASSWORD" \\
         -d southbrook --http-port=8997 2>/dev/null' \\
        < scripts/backfill_product_skus.py

(Never inline the DB password here — this file is committed. Pass it via the
environment, or omit the flag entirely on stacks that read it from the
container env, which the prod container does.)

Mode — SKJ_APPLY env var (NOT a hardcoded flag, so the same file can be
piped in dry-run and then apply mode without editing it):

    SKJ_APPLY unset or != "1"  -> DRY RUN (default). Every write below is
        executed for real against the ORM in this transaction (so
        constraints/onchange-adjacent behaviour is exercised exactly as it
        would be for real), then the WHOLE transaction is rolled back at
        the very end via env.cr.rollback(). Nothing persists.
    SKJ_APPLY=1                -> APPLY. Same code path, same writes,
        committed at the end via env.cr.commit().

Every log line is prefixed VERB = "DID" (apply) or "WOULD" (dry run) so a
diff of the two runs' output is meaningful. Idempotent throughout: every
write is preceded by a skip-if-already-set check (never overwrites a
non-null default_code); re-running this script after SKJ_APPLY=1 should
log nothing but SKIP-ALREADY-SET / SKIP-DRIFT / SKIP-NOT-ROOT lines and
zero net changes.

Uniqueness guard with guarded SKU release: if a proposed SKU is held by
another product, checks if the holder is archived + unreferenced
(zero sale_order_line via variants). If yes, safely clears the holder's SKU
(ID drift cleanup) and proceeds. If no, logs CONFLICT and skips. This
avoids silent overwrites while tidying archived duplicates.

Service products (type=service) are intentionally skipped — the
production-readiness validator only blocks designs using products in
cabinet/consumable categories.

House SKU convention (reverse-engineered from ~50 existing SKUs):
  - Cabinets (Cabinets (Finished) category): SB-<TYPE>[-<DESCRIPTOR>]
    Examples: SB-WALL-2DR, SB-BASE-1DR, SB-SINK-BASE
  - Fasteners/Hardware: FAST-<TYPE>[-<SPEC>]
    Examples: FAST-CONFIRMAT-50, FAST-DOWEL-8MM
  - Adhesives: ADH-<TYPE>
    Examples: ADH-EVA, ADH-PUR
  - Abrasives: ABR-<TYPE>[-<SPEC>]
    Examples: ABR-120-GRIT
  - Finishing: FIN-<TYPE>[-<DESCRIPTOR>]
    Examples: FIN-FILTER, FIN-PAINT-WHITE
  - CNC Tools: CNC-<TYPE>[-<SPEC>]
    Examples: CNC-COMP-HALF, CNC-COLLET-ER32

Mapping (11 products):
  1  → SKIP (service type, not covered by validator)
  36 → SB-WALL-1DR (cabinet)
  198 → ADH-EVA (adhesive)
  199 → ADH-PUR (adhesive)
  200 → ABR-120-GRIT (abrasive)
  201 → FIN-FILTER (finishing)
  202 → FIN-PAINT-WHITE (finishing)
  203 → CNC-COMP-HALF (CNC tools)
  204 → CNC-DOWN-3-8 (CNC tools)
  205 → CNC-DRILL-BLOCK (CNC tools)
  206 → CNC-COLLET-ER32 (CNC tools)
"""
import os

APPLY = os.environ.get("SKJ_APPLY") == "1"
VERB = "DID" if APPLY else "WOULD"

Product = env["product.template"].sudo()

counters = {}


def bump(key, n=1):
    counters[key] = counters.get(key, 0) + n


def log(msg):
    print(msg)


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


hr("SOUTHBROOK SKU BACKFILL — mode=%s (SKJ_APPLY=%r)"
   % ("APPLY" if APPLY else "DRY-RUN", os.environ.get("SKJ_APPLY")))

# Mapping: (product_id, expected_name, proposed_sku, reason)
SKU_MAPPING = [
    (1, "Event Registration",
     None,
     "service type, not covered by cabinet/consumable validator"),
    (36, "Wall 1-Door",
     "SB-WALL-1DR",
     "cabinet, follows pattern SB-WALL-2DR (id=37)"),
    (198, "EVA Glue Cartridge",
     "ADH-EVA",
     "adhesive, follows convention ADH-<type>"),
    (199, "PUR Glue",
     "ADH-PUR",
     "adhesive, follows convention ADH-<type>"),
    (200, "120 Grit Sandpaper",
     "ABR-120-GRIT",
     "abrasive, follows convention ABR-<type>"),
    (201, "Spray Booth Filter",
     "FIN-FILTER",
     "finishing, follows convention FIN-<type>"),
    (202, "Cabinet Paint White",
     "FIN-PAINT-WHITE",
     "finishing, follows convention FIN-<type>-<descriptor>"),
    (203, "1/2\" Compression Bit",
     "CNC-COMP-HALF",
     "CNC tool, follows convention CNC-<type>-<spec>"),
    (204, "3/8\" Downcut Bit",
     "CNC-DOWN-3-8",
     "CNC tool, follows convention CNC-<type>-<spec>"),
    (205, "CNC Drill Block Tool",
     "CNC-DRILL-BLOCK",
     "CNC tool, follows convention CNC-<type>"),
    (206, "ER32 Collet Set",
     "CNC-COLLET-ER32",
     "CNC tool, follows convention CNC-<type>-<spec>"),
]

# Validate mapping integrity
assert len(SKU_MAPPING) == 11, (
    "expected 11 product mappings, got %d" % len(SKU_MAPPING))

hr("SECTION 1 — SKU assignment (11 products)")

for pid, expected_name, proposed_sku, reason in SKU_MAPPING:
    product = Product.browse(pid)

    # Skip services entirely
    if proposed_sku is None:
        log("SECTION1 SKIP-SERVICE id=%s|%s| %s"
            % (pid, expected_name, reason))
        bump("s1_skip_service")
        continue

    # Check product exists
    if not product.exists():
        log("SECTION1 SKIP-MISSING id=%s expected_name=%r "
            "(product no longer exists)" % (pid, expected_name))
        bump("s1_skip_missing")
        continue

    # Name drift guard: ensure product name still matches expectation
    if product.name != expected_name:
        log("SECTION1 SKIP-DRIFT id=%s expected_name=%r actual_name=%r "
            "(id drift guard tripped — not writing)"
            % (pid, expected_name, product.name))
        bump("s1_skip_drift")
        continue

    # Idempotent check: skip if already set
    if product.default_code:
        log("SECTION1 SKIP-ALREADY-SET id=%s name=%r sku=%r "
            "(idempotent no-op)"
            % (pid, product.name, product.default_code))
        bump("s1_skip_already_set")
        continue

    # Uniqueness guard with guarded release: check if proposed SKU is already taken
    # by another product. If the conflict holder is archived + unreferenced, we can
    # safely clear its SKU (ID drift cleanup). This preserves active products while
    # tidying up archived duplicates — no data loss, just clarity for pickers.
    # active_test=False is load-bearing, not defensive: Odoo's search()
    # hides archived records, and the SKU squatters in this database are
    # PRECISELY the archived duplicates (e.g. the archived "Wall Cabinet ·
    # Single Door" holding SB-WALL-1DR while the live "Wall 1-Door" has
    # none). Without this the conflict branch below — and the guarded
    # release it exists for — could never fire at all, and the SKU would
    # simply be assigned twice, leaving two products sharing one
    # reference. Seeing archived holders is the whole point.
    conflict = Product.with_context(active_test=False).search(
        [("default_code", "=", proposed_sku)], limit=1)
    if conflict:
        can_release = False
        release_reason = ""

        # Check 1: is the holder inactive (archived)?
        if not conflict.active:
            # Check 2: does the holder have any sale_order_line references via variants?
            SaleOrderLine = env["sale.order.line"].sudo()
            variant_ids = conflict.product_variant_ids.ids
            line_count = SaleOrderLine.search_count(
                [("product_id", "in", variant_ids)])
            if line_count == 0:
                # Check 3: holder is a different product (not the target itself)?
                if conflict.id != pid:
                    can_release = True
                    release_reason = "archived, 0 sale_order_line refs"

        if can_release:
            # Safe to release: the SKU holder is archived + unreferenced.
            # Clear its default_code to tidy up ID drift (no silent overwrite,
            # just cleanup of dead product records).
            conflict.write({"default_code": False})
            log("SECTION1 %s SKU-RELEASE id=%s|%s| released %r "
                "(%s — safe cleanup)"
                % (VERB, conflict.id, conflict.display_name,
                   proposed_sku, release_reason))
            bump("s1_sku_release")
            # Fall through to assign to the target product.
        else:
            # Cannot release: holder is active, has order references, or is the
            # target itself. Log reason and skip.
            # Report the ACTUAL reason the release was refused. (An earlier
            # revision printed "?" unconditionally here — this branch is only
            # reached when can_release is False, so the old ternary could
            # never report anything else.)
            if conflict.id == pid:
                refusal = "holder IS the target product"
            elif conflict.active:
                refusal = "holder is ACTIVE"
            else:
                refusal = "holder is archived but still referenced by %d order line(s)" % (
                    env["sale.order.line"].sudo().search_count(
                        [("product_id", "in", conflict.product_variant_ids.ids)]))
            log("SECTION1 CONFLICT id=%s|%s| proposed_sku=%r already held "
                "by id=%s|%s| — cannot release (%s), skipping"
                % (pid, product.name, proposed_sku,
                   conflict.id, conflict.display_name, refusal))
            bump("s1_conflict")
            continue

    # Assign SKU
    product.write({"default_code": proposed_sku})
    log("SECTION1 %s id=%s|%s| default_code -> %r [%s]"
        % (VERB, pid, product.name, proposed_sku, reason))
    bump("s1_set")

hr("SECTION 2 — summary and recount")

# Recount: how many sellable products still lack a SKU after backfill?
remaining_no_sku = Product.search([
    ("sale_ok", "=", True),
    ("default_code", "in", [False, ""])
])
remaining_count = len(remaining_no_sku)

log("\nAssignment summary:")
for key, count in sorted(counters.items()):
    log("  %s: %d" % (key, count))

log("\nRecount of remaining sellable products without SKU: %d"
    % remaining_count)

if APPLY:
    env.cr.commit()
    log("\nCOMMITTED to database.")
else:
    env.cr.rollback()
    log("\nROLLBACK (dry-run mode) — no changes persisted.")
